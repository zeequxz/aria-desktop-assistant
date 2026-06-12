"""services/orchestration_service.py - Project Leader (multi-agent orchestration).

A "Project Leader" turns a goal into a plan, assigns each task to a specialist
agent, runs them as durable child runs (parent_run_id = leader run), passes each
step's output to its dependents, then merges everything into a final result.

This is a state machine, not a prompt: the plan + per-task status live in the
`tasks` table, and progress is emitted on the bus (and posted into the chat it
was started from). Stage 1 runs tasks in dependency order, with one bounded retry
per task and graceful failure (a failed task is recorded, the run continues).

Specialist roles map to the built-in agents; the learned router refines this over
time. Start with: planner/generalist→assistant, researcher→researcher,
coder/reviewer/tester→coder, writer→writer.
"""

from __future__ import annotations

import json
import re
import threading

from aria2.core import db, logs
from aria2.core.events import bus
from aria2.core.ids import new_id, now_ms

_ROLE_AGENT = {
    "researcher": "researcher", "coder": "coder", "reviewer": "coder",
    "tester": "coder", "writer": "writer", "planner": "assistant",
    "generalist": "assistant",
}

USAGE = (
    "🧭 Project Leader — plan + run a goal across specialist agents:\n"
    "  /team <goal>   — e.g. /team build a snake game in one HTML file and test it\n"
    "Results (plan, each step, final summary) post here; full runs are in the Runs tab."
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
        out.append({
            "ordinal": int(t.get("id", i) or i),
            "title": str(t.get("title") or t.get("task") or f"Task {i}")[:120],
            "description": str(t.get("description") or t.get("details")
                               or t.get("task") or t.get("title") or ""),
            "role": str(t.get("role") or t.get("agent") or "generalist"),
            "depends_on": [int(x) for x in deps if str(x).strip().lstrip("-").isdigit()],
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
        "trigger_id": None, "title": f"Team: {goal[:60]}",
        "budget_usd": 0, "cost_usd": 0, "token_total": 0,
        "forked_from_run_id": None, "forked_from_step": None,
        "started_at": now_ms(),
    })
    threading.Thread(target=_execute, name="leader", daemon=True,
                     args=(goal, project, agent, chat_id, leader_run_id)).start()
    return leader_run_id


def tasks_for(leader_run_id: str) -> list[dict]:
    rows = db.all("SELECT * FROM tasks WHERE leader_run_id=? ORDER BY ordinal",
                  (leader_run_id,))
    return [dict(r) for r in rows]


def _execute(goal, project, agent, chat_id, leader_run_id):
    from aria2.core import config
    log = logs.get("orchestration")
    settings = config.load()

    def say(text: str):
        if chat_id:
            try:
                from aria2.services import chat_service
                chat_service._persist_message(
                    chat_id, "assistant", [{"type": "text", "text": text}])
            except Exception:
                pass
        bus.publish("orchestration.chat",
                    {"leader_run_id": leader_run_id, "chat_id": chat_id, "text": text})

    try:
        say(f"🧭 **Project Leader** — planning: {goal}")
        plan = _plan(goal, project, agent, settings)
        if not plan:  # planner failed → run the goal as a single task
            plan = [{"ordinal": 1, "title": goal[:120], "description": goal,
                     "role": "generalist", "depends_on": []}]
        tasks = _persist_tasks(leader_run_id, plan)
        bus.publish("orchestration.plan", {"leader_run_id": leader_run_id,
                                           "tasks": [t["title"] for t in tasks]})
        say("📋 **Plan**\n" + "\n".join(
            f"  {t['ordinal']}. [{t['role']}] {t['title']}" for t in tasks))

        outputs: dict[int, str] = {}
        for t in topo_order(tasks):
            db.update("tasks", t["id"], {"status": "running", "updated_at": now_ms()})
            say(f"⚙ Step {t['ordinal']} · [{t['role']}] {t['title']}…")
            out, ok = _run_task(t, outputs, project, settings, leader_run_id)
            outputs[t["ordinal"]] = out
            db.update("tasks", t["id"], {"status": "done" if ok else "failed",
                                         "output": (out or "")[:4000],
                                         "updated_at": now_ms()})
            say((f"✓ Step {t['ordinal']} done" if ok
                 else f"✗ Step {t['ordinal']} failed (continuing)"))

        final = _merge(goal, tasks, outputs, settings, agent)
        db.update("runs", leader_run_id, {"status": "done", "ended_at": now_ms()})
        say(f"✅ **Result**\n{final}")
        bus.publish("orchestration.done", {"leader_run_id": leader_run_id,
                                           "chat_id": chat_id, "text": final})
    except Exception as e:
        log.exception(logs.j("leader_failed", run_id=leader_run_id, error=str(e)))
        db.update("runs", leader_run_id, {"status": "failed", "error": str(e),
                                          "ended_at": now_ms()})
        say(f"⚠ Orchestration failed: {e}")


def _persist_tasks(leader_run_id: str, plan: list[dict]) -> list[dict]:
    out = []
    for t in plan:
        tid = new_id("task")
        db.insert("tasks", {
            "id": tid, "leader_run_id": leader_run_id, "ordinal": t["ordinal"],
            "title": t["title"], "description": t["description"], "role": t["role"],
            "agent_id": role_to_agent(t["role"]),
            "depends_on": json.dumps(t["depends_on"]),
            "status": "pending", "run_id": None, "output": None,
            "created_at": now_ms(), "updated_at": now_ms(),
        })
        out.append({**t, "id": tid})
    return out


def _run_task(task, outputs, project, settings, leader_run_id) -> tuple[str, bool]:
    """Run one task as a specialist child run (one bounded retry). Dependency
    outputs are passed in as context so the DAG carries data between steps."""
    from aria2.runtime.run_engine import RunEngine, RunRequest
    from aria2.services import agent_service

    agent = agent_service.get(role_to_agent(task["role"])) or agent_service.get("assistant")
    deps = [d for d in task["depends_on"] if d in outputs]
    ctx = ""
    if deps:
        ctx = "Context from earlier steps:\n" + "\n\n".join(
            f"[Step {d}]\n{outputs[d][:1500]}" for d in deps) + "\n\n"
    prompt = f"{ctx}Task: {task['title']}\n{task['description']}"
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
    """A single tool-free model call (planner + merge)."""
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
           '"depends_on": [ids of earlier tasks]}.')
    return parse_plan(_oneshot(sys, goal, settings, agent))


def _merge(goal, tasks, outputs, settings, agent) -> str:
    body = "\n\n".join(
        f"## {t['ordinal']}. {t['title']}\n{(outputs.get(t['ordinal']) or '(no output)')[:1500]}"
        for t in tasks)
    user = (f"Goal: {goal}\n\nCompleted task outputs:\n{body}\n\n"
            "Synthesize the final result for the user. Be concise and concrete.")
    return _oneshot("You synthesize multi-agent results into one clear answer.",
                    user, settings, agent) or "Completed."
