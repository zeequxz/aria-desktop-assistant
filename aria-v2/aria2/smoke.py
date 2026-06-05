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
    db._conn = None

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

    # Knowledge ingest + search
    knowledge_service.ingest_text(p["id"], "arch.md",
                                  "The run engine streams tokens over an event bus "
                                  "and persists runs and steps in SQLite.")
    ks = knowledge_service.search("how are runs stored", p["id"])
    check("knowledge search returns a hit", len(ks) > 0)

    # Chat persistence + fork
    chat = chat_service.create_chat(p["id"], agent_id=a["id"])
    chat_service._persist_message(chat["id"], "user", [{"type": "text", "text": "hello"}])
    chat_service._persist_message(chat["id"], "assistant", [{"type": "text", "text": "hi"}])
    forked = chat_service.fork(chat["id"])
    check("chat fork copies messages",
          len(chat_service.list_messages(forked["id"])) == 2)

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
        _PPu("C:/src/app"), _PPu("C:/dest/app"))
    _ubt = _ubat.read_text(encoding="utf-8")
    check("updater script waits for the pid, copies the build, and relaunches",
          "4242" in _ubt and "robocopy" in _ubt.lower()
          and "ARIA2.exe" in _ubt and "waitloop" in _ubt)

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

    # run_python executes via a temp file (no shell), so code containing quotes
    # and backslashes runs correctly — the old `python -c "<...>"` path mangled
    # these on Windows cmd.exe.
    from aria2.runtime.tools import sandbox as _sandbox
    _pyres = _sandbox.run_python('print("quotes \\" and back\\\\slash")')
    check("run_python runs snippets with quotes/backslashes correctly",
          _pyres.get("exit_code") == 0
          and 'quotes " and back\\slash' in _pyres.get("stdout", ""))

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

    from aria2.runtime.tools import permissions as _perm
    _perm.set_approver(None)  # headless: no approver
    allowed_deny, _ = _perm.check("run_shell", {}, {"run_shell": "deny"}, "ask")
    allowed_ask, _ = _perm.check("run_shell", {}, {}, "ask")
    check("permission gate enforces deny", allowed_deny is False)
    check("ask resolves to deny when no approver is registered", allowed_ask is False)

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


def _status_via_server(update_service, version: str):
    """Serve a manifest with the given version and return check_status() against it."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"version": version, "url": "http://x/app.zip",
                               "notes": "test"}).encode()
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
