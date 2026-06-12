"""services/run_service.py - Inspect, replay, fork, and diff runs.

Because every model step persisted the exact context it saw (messages_json),
a run is *reproducible*: you can fork a new run from any step — optionally
editing the last user turn — and you can diff two runs step-by-step. This is
the "time-travel" moat: agent work behaves like version control, which is a
ground-up rewrite for any streaming-session product to retrofit.
"""

from __future__ import annotations

import json
import threading

from aria2.core import db
from aria2.core.ids import new_id


def list_runs(limit: int = 100, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM runs"
    params: tuple = ()
    if kind:
        sql += " WHERE kind = ?"
        params = (kind,)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params = params + (limit,)
    return [dict(r) for r in db.all(sql, params)]


def get_run(run_id: str) -> dict | None:
    r = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
    return dict(r) if r else None


def steps(run_id: str) -> list[dict]:
    rows = db.all("SELECT * FROM run_steps WHERE run_id = ? ORDER BY idx", (run_id,))
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "idx": r["idx"], "type": r["type"],
            "tool_name": r["tool_name"],
            "input": json.loads(r["input_json"]) if r["input_json"] else None,
            "output": json.loads(r["output_json"]) if r["output_json"] else None,
            "token_in": r["token_in"], "token_out": r["token_out"],
            "duration_ms": r["duration_ms"], "created_at": r["created_at"],
        })
    return out


def totals() -> dict:
    r = db.one(
        "SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) cost, "
        "COALESCE(SUM(token_total),0) tok FROM runs"
    )
    return {"runs": r["n"], "cost_usd": r["cost"], "tokens": r["tok"]}


def children(run_id: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM runs WHERE parent_run_id = ? ORDER BY started_at", (run_id,)
    )]


# ── Replay / fork / diff ────────────────────────────────────────────────────────

def model_steps(run_id: str) -> list[dict]:
    """Model steps only — the points you can fork from."""
    return [s for s in steps(run_id) if s["type"] == "model"]


def snapshot_at_step(run_id: str, step_idx: int) -> dict | None:
    """Return {system, messages} the model saw at a given step idx."""
    r = db.one(
        "SELECT messages_json FROM run_steps WHERE run_id=? AND idx=?", (run_id, step_idx)
    )
    if not r or not r["messages_json"]:
        return None
    return json.loads(r["messages_json"])


def fork_from_step(run_id: str, step_idx: int, edited_user_text: str | None = None,
                   overrides: dict | None = None, on_complete=None) -> str:
    """Create and run a new run that resumes from the context at `step_idx`.

    If `edited_user_text` is given, the last user turn in the snapshot is
    replaced — letting you ask "what if I'd said this instead?" and compare.
    Returns the new run_id immediately; it executes on a background thread.
    """
    from aria2.core import config
    from aria2.runtime.run_engine import RunEngine, RunRequest
    from aria2.services import agent_service, project_service

    orig = get_run(run_id)
    snap = snapshot_at_step(run_id, step_idx)
    if not orig or not snap:
        raise ValueError("run/step snapshot not available")

    messages = [dict(m) for m in snap["messages"]]
    if edited_user_text is not None:
        for m in reversed(messages):
            if m.get("role") == "user":
                m["content"] = [{"type": "text", "text": edited_user_text}]
                break

    agent = agent_service.get(orig["agent_id"]) or agent_service.get("assistant")
    project = project_service.get(orig["project_id"]) or project_service.get("general")
    new_run_id = new_id("run")
    engine = RunEngine(config.load())
    req = RunRequest(
        agent=agent, project=project, messages=messages,
        kind="chat", chat_id=orig["chat_id"], run_id=new_run_id,
        overrides=overrides or {}, forked_from_run_id=run_id, forked_from_step=step_idx,
    )

    def _worker():
        result = engine.execute(req)
        if on_complete:
            on_complete(result)

    from aria2.runtime import executor
    executor.submit(_worker)
    return new_run_id


def diff_runs(run_a: str, run_b: str) -> list[dict]:
    """Step-by-step comparison of two runs (e.g. a run and its fork)."""
    a, b = steps(run_a), steps(run_b)
    out = []
    for i in range(max(len(a), len(b))):
        sa = a[i] if i < len(a) else None
        sb = b[i] if i < len(b) else None
        out.append({
            "idx": i,
            "a": _summarise_step(sa),
            "b": _summarise_step(sb),
            "changed": _summarise_step(sa) != _summarise_step(sb),
        })
    return out


def _summarise_step(s: dict | None) -> str:
    if not s:
        return ""
    if s["type"] == "model":
        return f"model: {((s.get('output') or {}).get('text','') or '')[:160]}"
    if s["type"] == "tool":
        return f"tool {s['tool_name']}({json.dumps(s.get('input'))[:80]})"
    return s["type"]
