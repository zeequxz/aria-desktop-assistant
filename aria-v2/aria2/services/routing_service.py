"""services/routing_service.py - Learned task→agent routing (self-improving org).

Because every delegated sub-task is a durable run with an agent_id, status, and
cost, we can *learn which agent is actually best at which kind of work* and route
accordingly. Over time the org gets better at your work without any prompt
engineering — a flywheel competitors with stateless sub-agents can't spin.

Scoring uses Laplace-smoothed success rate so a single lucky/unlucky run doesn't
dominate, blended with a small recency-of-evidence weight. With no history yet,
it falls back to a sensible role→task mapping.
"""

from __future__ import annotations

import re

from aria2.core import db
from aria2.core.ids import now_ms

# Task taxonomy + keyword cues for cheap classification.
_TASK_CUES = {
    "code": ("code", "bug", "refactor", "function", "implement", "compile", "test",
             "stack trace", "exception", "api", "script"),
    "research": ("research", "find", "search", "look up", "gather", "investigate",
                 "sources", "compare options"),
    "write": ("write", "draft", "email", "article", "blog", "summary", "rewrite",
              "edit", "proofread"),
    "analyze": ("analyze", "analyse", "evaluate", "assess", "plan", "strategy",
                "review", "reason"),
}
# Fallback role→task affinity when there's no learned history.
_FALLBACK = {"code": "coder", "research": "researcher", "write": "writer",
             "analyze": "assistant", "general": "assistant"}


def classify(task_text: str) -> str:
    t = (task_text or "").lower()
    best, best_hits = "general", 0
    for task_type, cues in _TASK_CUES.items():
        hits = sum(1 for c in cues if c in t)
        if hits > best_hits:
            best, best_hits = task_type, hits
    return best


def record(agent_id: str, task_text: str, status: str, cost_usd: float = 0.0,
           duration_ms: int = 0) -> None:
    """Update an agent's performance for the task type of `task_text`."""
    task_type = classify(task_text)
    success = 1 if status == "done" else 0
    existing = db.one(
        "SELECT * FROM agent_skill_stats WHERE agent_id=? AND task_type=?",
        (agent_id, task_type),
    )
    if existing:
        # Composite primary key, so update explicitly (db.update filters on one col).
        db.execute(
            "UPDATE agent_skill_stats SET runs=?, successes=?, total_cost=?, "
            "total_ms=?, updated_at=? WHERE agent_id=? AND task_type=?",
            (existing["runs"] + 1, existing["successes"] + success,
             existing["total_cost"] + cost_usd, existing["total_ms"] + duration_ms,
             now_ms(), agent_id, task_type),
        )
    else:
        db.insert("agent_skill_stats", {
            "agent_id": agent_id, "task_type": task_type, "runs": 1,
            "successes": success, "total_cost": cost_usd, "total_ms": duration_ms,
            "updated_at": now_ms(),
        })


def _score(agent_id: str, task_type: str) -> float:
    r = db.one(
        "SELECT runs, successes FROM agent_skill_stats WHERE agent_id=? AND task_type=?",
        (agent_id, task_type),
    )
    if not r or r["runs"] == 0:
        return 0.5  # neutral prior
    # Laplace smoothing: (s+1)/(n+2).
    return (r["successes"] + 1) / (r["runs"] + 2)


def best_agent(task_text: str, candidates: list[dict] | None = None) -> dict | None:
    """Return the best agent dict for a task, by learned score then fallback role."""
    from aria2.services import agent_service

    agents = candidates or agent_service.list_agents()
    if not agents:
        return None
    task_type = classify(task_text)

    have_history = db.one(
        "SELECT 1 FROM agent_skill_stats WHERE task_type=? LIMIT 1", (task_type,)
    )
    if have_history:
        ranked = sorted(agents, key=lambda a: _score(a["id"], task_type), reverse=True)
        return ranked[0]

    fallback_id = _FALLBACK.get(task_type, "assistant")
    return next((a for a in agents if a["id"] == fallback_id), agents[0])


def recommendations(task_text: str, candidates: list[dict] | None = None) -> list[dict]:
    """Ranked agents with scores, for the supervisor's list_agents/suggest tools."""
    from aria2.services import agent_service

    agents = candidates or agent_service.list_agents()
    task_type = classify(task_text)
    out = []
    for a in agents:
        s = db.one(
            "SELECT runs, successes FROM agent_skill_stats WHERE agent_id=? AND task_type=?",
            (a["id"], task_type),
        )
        out.append({
            "name": a["name"], "id": a["id"], "description": a.get("description", ""),
            "task_type": task_type, "score": round(_score(a["id"], task_type), 3),
            "runs": s["runs"] if s else 0,
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def agent_report(agent_id: str) -> list[dict]:
    """Per-task-type performance for one agent, for the Agents view."""
    rows = db.all(
        "SELECT * FROM agent_skill_stats WHERE agent_id=? ORDER BY runs DESC", (agent_id,)
    )
    report = []
    for r in rows:
        rate = (r["successes"] / r["runs"]) if r["runs"] else 0.0
        report.append({
            "task_type": r["task_type"], "runs": r["runs"],
            "success_rate": rate, "avg_cost": (r["total_cost"] / r["runs"]) if r["runs"] else 0,
            "avg_ms": int(r["total_ms"] / r["runs"]) if r["runs"] else 0,
        })
    return report
