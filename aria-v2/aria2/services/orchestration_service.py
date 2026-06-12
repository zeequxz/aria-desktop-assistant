"""services/orchestration_service.py - Project Leader (multi-agent orchestration).

A "Project Leader" turns a goal into a plan, assigns each task to a specialist
agent, runs them as durable child runs (parent_run_id = leader run), passes each
step's output to its dependents, then merges everything into a final result.

This is a state machine, not a prompt: the plan + per-task status live in the
`tasks` table, and progress is emitted on the bus (and posted into the chat it
was started from).

Stages:
  1  plan → assign → run in dependency order → merge (durable, graceful failure).
  2  independent tasks run in parallel *waves*; code output gets an auto-review.
  3  REVISION LOOP — a reviewer returns APPROVE/REVISE and the coder re-runs to
     address feedback (bounded by `max_revisions`); DELIVERABLE CONTRACTS — the
     planner can require a task's output to contain keywords or be valid JSON, and
     a failing contract drives a revision (or fails the task honestly); a
     PLAN-APPROVAL checkpoint (`orchestration_plan_approval`) lets the leader pause
     after planning until the human runs `/team go` (or `/team cancel`).

Specialist roles map to the built-in agents; the learned router refines this over
time. Start with: planner/generalist→assistant, researcher→researcher,
coder/reviewer/tester→coder, writer→writer.
"""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from aria2.core import db, logs
from aria2.core.events import bus
from aria2.core.ids import new_id, now_ms

_ROLE_AGENT = {
    "researcher": "researcher", "coder": "coder", "reviewer": "coder",
    "tester": "coder", "writer": "writer", "planner": "assistant",
    "generalist": "assistant",
}

_TITLE_PREFIX = "Team: "

USAGE = (
    "🧭 Project Leader — plan + run a goal across specialist agents:\n"
    "  /team <goal>   — e.g. /team build a snake game in one HTML file and test it\n"
    "  /team go       — run a plan that is waiting for approval\n"
    "  /team cancel   — discard a plan that is waiting for approval\n"
    "Results post here; full runs + the task graph are in the Runs and Team tabs."
)


def role_to_agent(role: str) -> str:
    return _ROLE_AGENT.get((role or "generalist").lower().strip(), "assistant")


# ── Plan parsing + ordering (deterministic, unit-tested) ─────────────────────

def parse_plan(text: str) -> list[dict]:
    """Parse the Planner's JSON task array. Robust to ``` fences + surrounding
    prose. Returns [] if nothing usable parses."""
    raw = text or ""
    m = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    body = m.group(1) if m else raw
    a, b = body.find("["), body.rfind("]")
    if a == -1 or b == -1 or b <= a:
        return []
    try:
        arr = json.loads(body[a:b + 1])
    except Exception:
        return []
    out: list[dict] = []
    for i, t in enumerate(arr, 1):
        if not isinstance(t, dict):
            continue
        deps = t.get("depends_on") or t.get("deps") or []
        expects = t.get("expects") or t.get("must_contain") or []
        if isinstance(expects, str):
            expects = [expects]
        out.append({
            "ordinal": int(t.get("id", i) or i),
            "title": str(t.get("title") or t.get("task") or f"Task {i}")[:120],
            "description": str(t.get("description") or t.get("details")
                               or t.get("task") or t.get("title") or ""),
            "role": str(t.get("role") or t.get("agent") or "generalist"),
            "depends_on": [int(x) for x in deps if str(x).strip().lstrip("-").isdigit()],
            "contract": {
                "expects": [str(x) for x in expects if str(x).strip()][:8],
                "format": str(t.get("format") or "").lower().strip(),
            },
        })
    return out


def topo_order(tasks: list[dict]) -> list[dict]:
    """Tasks in dependency order (stable). Unknown deps are ignored; a cycle is
    broken by appending the remaining tasks in their given order."""
    known = {t["ordinal"] for t in tasks}
    done: set = set()
    order: list[dict] = []
    remaining = list(tasks)
    while remaining:
        progressed = False
        for t in list(remaining):
            deps = [d for d in t["depends_on"] if d in known]
            if all(d in done for d in deps):
                order.append(t)
                done.add(t["ordinal"])
                remaining.remove(t)
                progressed = True
        if not progressed:  # cycle / unresolved — take the rest as-is
            order.extend(remaining)
            break
    return order


def waves(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks into dependency *levels*: each level's tasks have all their
    (known) deps satisfied by earlier levels, so a level can run in parallel."""
    known = {t["ordinal"] for t in tasks}
    done: set = set()
    remaining = list(tasks)
    out: list[list[dict]] = []
    while remaining:
        wave = [t for t in remaining
                if all(d in done for d in t["depends_on"] if d in known)]
        if not wave:  # cycle / unresolved — run the rest together
            wave = remaining[:]
        for t in wave:
            done.add(t["ordinal"])
            remaining.remove(t)
        out.append(wave)
    return out


# ── Deliverable contracts + review verdicts (deterministic, unit-tested) ─────

def validate_deliverable(output: str, contract: dict | None) -> tuple[bool, str]:
    """Check a task's output against its contract. Returns (ok, reason). Always
    requires non-empty output; optionally requires keywords / valid JSON."""
    out = output or ""
    if not out.strip():
        return False, "empty output"
    c = contract or {}
    expects = c.get("expects") or []
    missing = [k for k in expects if k and k.lower() not in out.lower()]
    if missing:
        return False, "missing required: " + ", ".join(missing)
    if (c.get("format") or "").lower() == "json":
        body = out
        m = re.search(r"```(?:json)?\s*(.+?)```", out, re.S)
        if m:
            body = m.group(1)
        try:
            json.loads(body.strip())
        except Exception:
            return False, "output is not valid JSON"
    return True, ""


def review_verdict(text: str) -> str:
    """Map a reviewer reply to 'approve' or 'revise' (verdict is the lead word)."""
    head = (text or "").strip()[:24].upper()
    return "revise" if "REVISE" in head else "approve"


# ── Leader lifecycle ─────────────────────────────────────────────────────────

def start(goal: str, project: dict, agent: dict | None = None,
          chat_id: str | None = None) -> str:
    """Kick off a Project Leader run for `goal`. Returns the leader run id;
    progress arrives on the bus (orchestration.*) and in the chat."""
    leader_run_id = new_id("run")
    db.insert("runs", {
        "id": leader_run_id, "kind": "leader", "status": "running",
        "agent_id": (agent or {}).get("id") or "assistant",
        "project_id": project["id"], "chat_id": chat_id, "parent_run_id": None,
        "trigger_id": None, "title": _TITLE_PREFIX + goal,
        "budget_usd": 0, "cost_usd": 0, "token_total": 0,
        "forked_from_run_id": None, "forked_from_step": None,
        "started_at": now_ms(),
    })
    threading.Thread(target=_orchestrate, name="leader", daemon=True,
                     args=(goal, project, agent, chat_id, leader_run_id)).start()
    return leader_run_id


def pending_for_chat(chat_id: str) -> dict | None:
    """The most recent leader run in `chat_id` that is waiting for approval."""
    if not chat_id:
        return None
    row = db.one(
        "SELECT * FROM runs WHERE chat_id=? AND kind='leader' "
        "AND status='awaiting_approval' ORDER BY started_at DESC LIMIT 1",
        (chat_id,))
    return dict(row) if row else None


def resume(chat_id: str) -> bool:
    """`/team go` — run the plan that is awaiting approval in this chat."""
    run = pending_for_chat(chat_id)
    if not run:
        return False
    from aria2.core import config
    from aria2.services import agent_service, project_service
    goal = (run.get("title") or "").removeprefix(_TITLE_PREFIX)
    project = project_service.get(run.get("project_id")) or project_service.get("general")
    agent = agent_service.get(run.get("agent_id") or "assistant")
    tasks = tasks_for(run["id"])
    db.update("runs", run["id"], {"status": "running"})
    threading.Thread(
        target=_run_phase, name="leader-run", daemon=True,
        args=(goal, project, agent, chat_id, run["id"], config.load(), tasks)).start()
    return True


def cancel(chat_id: str) -> bool:
    """`/team cancel` — discard a plan that is awaiting approval."""
    run = pending_for_chat(chat_id)
    if not run:
        return False
    db.update("runs", run["id"], {"status": "cancelled", "ended_at": now_ms()})
    _say(chat_id, run["id"], "⏹ Plan cancelled.")
    return True


def tasks_for(leader_run_id: str) -> list[dict]:
    rows = db.all("SELECT * FROM tasks WHERE leader_run_id=? ORDER BY ordinal",
                  (leader_run_id,))
    return [dict(r) for r in rows]


def _say(chat_id, leader_run_id, text: str):
    """Persist a leader message into its chat (if any) and publish it on the bus."""
    if chat_id:
        try:
            from aria2.services import chat_service
            chat_service._persist_message(
                chat_id, "assistant", [{"type": "text", "text": text}])
        except Exception:
            pass
    bus.publish("orchestration.chat",
                {"leader_run_id": leader_run_id, "chat_id": chat_id, "text": text})


def _orchestrate(goal, project, agent, chat_id, leader_run_id):
    """Thread target: plan, then either pause for approval or run immediately."""
    from aria2.core import config
    log = logs.get("orchestration")
    settings = config.load()
    try:
        _say(chat_id, leader_run_id, f"🧭 **Project Leader** — planning: {goal}")
        tasks = _plan_phase(goal, project, agent, chat_id, leader_run_id, settings)
        if bool(settings.get("orchestration_plan_approval", False)):
            db.update("runs", leader_run_id, {"status": "awaiting_approval"})
            _say(chat_id, leader_run_id,
                 "⏸ **Awaiting approval.** Reply `/team go` to run this plan, "
                 "or `/team cancel` to discard it.")
            return
        _run_phase(goal, project, agent, chat_id, leader_run_id, settings, tasks)
    except Exception as e:
        log.exception(logs.j("leader_failed", run_id=leader_run_id, error=str(e)))
        db.update("runs", leader_run_id, {"status": "failed", "error": str(e),
                                          "ended_at": now_ms()})
        _say(chat_id, leader_run_id, f"⚠ Orchestration failed: {e}")


def _plan_phase(goal, project, agent, chat_id, leader_run_id, settings) -> list[dict]:
    plan = _plan(goal, project, agent, settings)
    if not plan:  # planner failed → run the goal as a single task
        plan = [{"ordinal": 1, "title": goal[:120], "description": goal,
                 "role": "generalist", "depends_on": [], "contract": {}}]
    tasks = _persist_tasks(leader_run_id, plan)
    bus.publish("orchestration.plan", {"leader_run_id": leader_run_id,
                                       "tasks": [t["title"] for t in tasks]})
    _say(chat_id, leader_run_id, "📋 **Plan**\n" + "\n".join(
        f"  {t['ordinal']}. [{t['role']}] {t['title']}" for t in tasks))
    return tasks


def _run_phase(goal, project, agent, chat_id, leader_run_id, settings, tasks):
    """Execute the persisted plan: parallel waves → merge → honest final status."""
    log = logs.get("orchestration")
    try:
        auto_review = bool(settings.get("auto_review", True))
        max_par = max(1, int(settings.get("orchestration_max_parallel", 3) or 3))

        # Run level by level; independent tasks in a level run in parallel on a
        # DEDICATED pool (never the global RunExecutor — a leader waiting on its
        # tasks must not be able to starve top-level runs).
        outputs: dict[int, str] = {}
        failures = 0
        for wave in waves(tasks):
            snapshot = dict(outputs)  # read-only deps for this wave (no race)
            if len(wave) == 1:
                results = [_run_one(wave[0], snapshot, project, settings,
                                    leader_run_id, chat_id, auto_review)]
            else:
                _say(chat_id, leader_run_id,
                     f"⚡ Running {len(wave)} steps in parallel…")
                with ThreadPoolExecutor(max_workers=min(max_par, len(wave)),
                                        thread_name_prefix="specialist") as pool:
                    futs = [pool.submit(_run_one, t, snapshot, project, settings,
                                        leader_run_id, chat_id, auto_review)
                            for t in wave]
                    results = [f.result() for f in futs]
            for ordinal, out, ok in results:
                outputs[ordinal] = out
                if not ok:
                    failures += 1

        final = _merge(goal, tasks, outputs, settings, agent)
        status = "done" if failures == 0 else "failed"
        db.update("runs", leader_run_id, {"status": status, "ended_at": now_ms()})
        ok_n = len(tasks) - failures
        head = "✅ **Result**" if failures == 0 else f"⚠ **Result** ({failures} step(s) failed)"
        _say(chat_id, leader_run_id, f"{head}  ·  {ok_n}/{len(tasks)} steps ok\n{final}")
        bus.publish("orchestration.done", {"leader_run_id": leader_run_id,
                                           "chat_id": chat_id, "text": final,
                                           "failures": failures})
    except Exception as e:
        log.exception(logs.j("leader_run_failed", run_id=leader_run_id, error=str(e)))
        db.update("runs", leader_run_id, {"status": "failed", "error": str(e),
                                          "ended_at": now_ms()})
        _say(chat_id, leader_run_id, f"⚠ Orchestration failed: {e}")


def _persist_tasks(leader_run_id: str, plan: list[dict]) -> list[dict]:
    out = []
    for t in plan:
        tid = new_id("task")
        db.insert("tasks", {
            "id": tid, "leader_run_id": leader_run_id, "ordinal": t["ordinal"],
            "title": t["title"], "description": t["description"], "role": t["role"],
            "agent_id": role_to_agent(t["role"]),
            "depends_on": json.dumps(t["depends_on"]),
            "contract": json.dumps(t.get("contract") or {}),
            "status": "pending", "run_id": None, "output": None,
            "created_at": now_ms(), "updated_at": now_ms(),
        })
        out.append({**t, "id": tid})
    return out


def _run_one(task, prior_outputs, project, settings, leader_run_id, chat_id,
             auto_review):
    """Run one task: execute the specialist; for code, loop review→revise until
    the reviewer approves AND the deliverable contract passes (bounded by
    `max_revisions`); validate the contract; persist status. Returns
    (ordinal, output, ok)."""
    o = task["ordinal"]
    contract = task.get("contract")
    if isinstance(contract, str):  # rehydrated from the DB
        try:
            contract = json.loads(contract or "{}")
        except Exception:
            contract = {}
    max_rev = max(0, int(settings.get("max_revisions", 2) or 0))
    db.update("tasks", task["id"], {"status": "running", "updated_at": now_ms()})
    _say(chat_id, leader_run_id, f"⚙ Step {o} · [{task['role']}] {task['title']}…")

    out, ok = _run_task(task, prior_outputs, project, settings, leader_run_id)

    review_notes = ""
    if ok and auto_review and (task.get("role") or "").lower() == "coder":
        attempt = 0
        while True:
            verdict, review_notes = _review(task, out, project, settings)
            cv_ok, cv_reason = validate_deliverable(out, contract)
            if (verdict == "approve" and cv_ok) or attempt >= max_rev:
                break
            attempt += 1
            feedback = review_notes if verdict == "revise" else \
                f"Deliverable check failed: {cv_reason}"
            _say(chat_id, leader_run_id, f"↻ Revising step {o} (round {attempt})…")
            out2, ok2 = _run_task(task, prior_outputs, project, settings,
                                  leader_run_id, revision=feedback, prev=out)
            if ok2 and (out2 or "").strip():
                out = out2
        if review_notes:
            out = f"{out}\n\n— Reviewer —\n{review_notes}"

    cv_ok, cv_reason = validate_deliverable(out, contract)
    ok = ok and cv_ok
    db.update("tasks", task["id"], {"status": "done" if ok else "failed",
                                    "output": (out or "")[:4000],
                                    "updated_at": now_ms()})
    tail = "" if ok else f" — {cv_reason}"
    _say(chat_id, leader_run_id,
         f"{'✓' if ok else '✗'} Step {o} {'done' if ok else 'failed'}{tail}")
    return o, out, ok


def _review(task, output, project, settings) -> tuple[str, str]:
    """One-shot reviewer pass. Returns (verdict, notes) where verdict is
    'approve' | 'revise'."""
    from aria2.services import agent_service
    reviewer = agent_service.get("coder") or agent_service.get("assistant")
    sys = ("You are a senior reviewer. Review the work below for correctness, "
           "bugs, and security. START your reply with APPROVE (if it is solid) or "
           "REVISE (if it needs changes), then 1-3 sentences of specifics.")
    user = f"Task: {task['title']}\n\nProduced:\n{(output or '')[:3000]}"
    text = _oneshot(sys, user, settings, reviewer)
    return review_verdict(text), text


def _run_task(task, outputs, project, settings, leader_run_id,
              revision: str | None = None, prev: str | None = None) -> tuple[str, bool]:
    """Run one task as a specialist child run (one bounded transient retry).
    Dependency outputs are passed in as context so the DAG carries data between
    steps; on a revision pass the previous attempt + reviewer feedback are
    appended so the coder produces an improved version."""
    from aria2.runtime.run_engine import RunEngine, RunRequest
    from aria2.services import agent_service

    agent = agent_service.get(role_to_agent(task["role"])) or agent_service.get("assistant")
    deps = [d for d in task["depends_on"] if d in outputs]
    ctx = ""
    if deps:
        ctx = "Context from earlier steps:\n" + "\n\n".join(
            f"[Step {d}]\n{outputs[d][:1500]}" for d in deps) + "\n\n"
    prompt = f"{ctx}Task: {task['title']}\n{task['description']}"
    if revision:
        prompt += (f"\n\n--- Revision needed ---\nYour previous attempt:\n"
                   f"{(prev or '')[:1500]}\n\nFeedback to address:\n{revision}\n\n"
                   "Produce an improved version that resolves the feedback.")
    result = None
    for _attempt in range(2):
        rid = new_id("run")
        db.update("tasks", task["id"], {"run_id": rid})
        req = RunRequest(
            agent=agent, project=project,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            kind="delegated", parent_run_id=leader_run_id, run_id=rid,
            overrides=agent_service.overrides_for(agent), include_shell=True)
        result = RunEngine(settings).execute(req)
        if result.status == "done" and (result.text or "").strip():
            return result.text.strip(), True
    return ((result.text if result else "") or "(no output)"), False


def _oneshot(goal_system: str, user: str, settings: dict, agent) -> str:
    """A single tool-free model call (planner + review + merge)."""
    from aria2.models import registry
    from aria2.services import agent_service
    try:
        overrides = agent_service.overrides_for(agent) if agent else None
        provider, model = registry.for_settings(settings, overrides)
    except Exception:
        return ""
    buf = ""
    try:
        for ev in provider.stream(
                model=model, system=goal_system,
                messages=[{"role": "user", "content": [{"type": "text", "text": user}]}],
                tools=None, max_tokens=1400, cache=False):
            if ev.type == "text":
                buf += ev.text
            elif ev.type == "error":
                return ""
    except Exception:
        return ""
    return buf.strip()


def _plan(goal, project, agent, settings) -> list[dict]:
    sys = ('You are a project planner. Break the user\'s goal into a MINIMAL '
           'ordered list of concrete tasks (2-6). Output ONLY a JSON array; each '
           'item: {"id": int, "title": str, "description": str, "role": '
           '"researcher"|"coder"|"reviewer"|"tester"|"writer"|"generalist", '
           '"depends_on": [ids of earlier tasks], "expects": [optional keywords '
           'the deliverable MUST contain], "format": "json" (optional, if the '
           'deliverable must be valid JSON)}.')
    return parse_plan(_oneshot(sys, goal, settings, agent))


def _merge(goal, tasks, outputs, settings, agent) -> str:
    body = "\n\n".join(
        f"## {t['ordinal']}. {t['title']}\n{(outputs.get(t['ordinal']) or '(no output)')[:1500]}"
        for t in tasks)
    user = (f"Goal: {goal}\n\nCompleted task outputs:\n{body}\n\n"
            "Synthesize the final result for the user. Be concise and concrete.")
    return _oneshot("You synthesize multi-agent results into one clear answer.",
                    user, settings, agent) or "Completed."
