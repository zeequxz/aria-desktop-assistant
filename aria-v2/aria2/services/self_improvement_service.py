"""services/self_improvement_service.py - Learn from failed runs.

The durable run substrate means a failure isn't just an error message — it's a
fully recorded run we can analyse. When a run fails (and the feature is enabled),
this service diffs it against the agent's past successes, hypothesises a concrete
fix to the agent's guidance, and files an **agent proposal** for the user to
review. Accepting it appends versioned guidance to the agent's system prompt.

This closes the flywheel: runs → routing learns *who*; this learns *how to get
better*. It uses the LLM when a provider is available, with a transparent
heuristic fallback so it still works offline.
"""

from __future__ import annotations

import json

from aria2.core import db
from aria2.core.ids import new_id, now_ms
from aria2.services import agent_service, run_service


def analyze_failure(run_id: str, settings: dict | None = None,
                    use_llm: bool | None = None) -> str | None:
    """Analyse a failed run and file an improvement proposal. Returns its id."""
    run = run_service.get_run(run_id)
    if not run or not run.get("agent_id"):
        return None
    agent = agent_service.get(run["agent_id"])
    if not agent:
        return None

    steps = run_service.steps(run_id)
    error = run.get("error") or _last_error(steps) or "unknown failure"
    failed_tools = sorted({s["tool_name"] for s in steps
                           if s["type"] == "tool" and _is_error(s.get("output"))
                           and s["tool_name"]})
    denied = any("not permitted" in json.dumps(s.get("output", "")) for s in steps
                 if s["type"] == "tool")

    settings = settings or {}
    if use_llm is None:
        use_llm = settings.get("self_improvement_enabled", False)

    guidance = None
    if use_llm:
        guidance = _llm_guidance(agent, error, steps, settings)
    if not guidance:
        guidance = _heuristic_guidance(error, failed_tools, denied)

    title = f"Improve “{agent['name']}”: avoid repeat of recent failure"
    rationale = (f"Run {run_id[:10]} failed: {error[:120]}. "
                 f"Proposed guidance to add to this agent so it handles this better.")
    payload = {"agent_id": agent["id"], "system_append": guidance}
    return _propose(title, rationale, payload, confidence=0.55)


# ── Guidance generation ─────────────────────────────────────────────────────

def _heuristic_guidance(error: str, failed_tools: list[str], denied: bool) -> str:
    e = error.lower()
    hints: list[str] = []
    if "max iteration" in e:
        hints.append("Break large tasks into smaller steps and delegate independent "
                     "sub-tasks in parallel instead of looping on one long task.")
    if "budget" in e:
        hints.append("Be economical: plan before acting and avoid redundant tool "
                     "calls so you stay within the run budget.")
    if denied:
        hints.append("Some tool calls were blocked by policy — prefer read-only "
                     "tools first and ask before destructive actions.")
    if failed_tools:
        hints.append(f"Validate inputs before calling tools ({', '.join(failed_tools)}); "
                     "confirm paths/arguments exist first.")
    if not hints:
        hints.append("Re-read the request and verify assumptions before acting; if "
                     "blocked, report clearly rather than retrying blindly.")
    return " ".join(hints)


def _llm_guidance(agent: dict, error: str, steps: list[dict], settings: dict) -> str | None:
    try:
        from aria2.models import registry as model_registry

        provider, model = model_registry.for_settings(settings)
        timeline = []
        for s in steps[-8:]:
            if s["type"] == "model":
                timeline.append(f"THOUGHT: {((s.get('output') or {}).get('text','') or '')[:200]}")
            elif s["type"] == "tool":
                timeline.append(f"TOOL {s['tool_name']} -> {json.dumps(s.get('output'))[:160]}")
        prompt = (
            f"An AI agent named '{agent['name']}' failed a task.\n"
            f"Error: {error}\nRecent steps:\n" + "\n".join(timeline) +
            "\n\nIn 1-3 sentences, write concrete guidance to ADD to this agent's "
            "system prompt so it avoids this failure next time. Output only the guidance."
        )
        buf = ""
        for ev in provider.stream(
            model=model, system="You improve AI agents. Be specific and concise.",
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            tools=None, max_tokens=300, cache=False,
        ):
            if ev.type == "text":
                buf += ev.text
            elif ev.type == "error":
                return None
        return buf.strip() or None
    except Exception:
        return None


# ── Proposal plumbing (shares the proposals table) ──────────────────────────

def _propose(title: str, rationale: str, payload: dict, confidence: float) -> str:
    dup = db.one("SELECT id FROM proposals WHERE title=? AND status='pending'", (title,))
    if dup:
        return dup["id"]
    pid = new_id("prop")
    db.insert("proposals", {
        "id": pid, "kind": "agent", "title": title, "rationale": rationale,
        "payload_json": json.dumps(payload), "status": "pending",
        "confidence": confidence, "created_at": now_ms(),
    })
    return pid


def apply_agent_proposal(payload: dict) -> dict:
    """Append learned guidance to an agent's system prompt (versioned)."""
    agent = agent_service.get(payload.get("agent_id", ""))
    if not agent:
        return {"error": "agent not found"}
    append = (payload.get("system_append") or "").strip()
    if not append:
        return {"error": "empty guidance"}
    marker = "\n\n[Learned guidance]\n"
    new_prompt = agent["system_prompt"].split(marker)[0] + marker + append
    # update() snapshots the prior prompt + bumps the version (rollback-able).
    agent_service.update(agent["id"], {"system_prompt": new_prompt},
                         note="self-improvement: learned guidance")
    return {"applied": True, "agent_id": agent["id"]}


def _last_error(steps: list[dict]) -> str | None:
    for s in reversed(steps):
        if _is_error(s.get("output")):
            out = s.get("output")
            return out.get("error") if isinstance(out, dict) else str(out)
    return None


def _is_error(output) -> bool:
    return isinstance(output, dict) and "error" in output
