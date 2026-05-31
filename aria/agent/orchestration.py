"""
agent/orchestration.py - Multi-agent orchestration (advanced mode).

When "Advanced mode" is enabled, the active agent gets two extra tools so it can
act as a coordinator — like Hermes' task-delegation / OpenClaw's sub-agents:

  list_agents()                     -> the agents it can delegate to
  delegate_to_agent(agent, task)    -> run another agent on a sub-task, return
                                       its result so the coordinator can combine
                                       outputs into a larger build.

Each delegated run is a fresh, self-contained agent invocation (its own persona
+ full tool set) executed synchronously and returned as text. Recursion is
capped so a sub-agent can't spawn an unbounded tree of sub-agents.
"""

import threading

from config import settings as cfg

# Guard against runaway recursion: a delegated agent may delegate further, but
# only a couple of levels deep.
_MAX_DEPTH = 2
_depth = threading.local()


def _cur_depth() -> int:
    return getattr(_depth, "value", 0)


def list_agents() -> dict:
    """Return the agents available for delegation (name + description)."""
    agents = cfg.get("agents", [])
    return {
        "agents": [
            {"name": a.get("name", ""), "description": a.get("desc", "")}
            for a in agents
        ]
    }


def delegate_to_agent(agent: str, task: str) -> dict:
    """Run another agent on a focused sub-task and return its final answer.

    `agent` is the agent's name (case-insensitive); `task` is a self-contained
    instruction. Returns {"agent", "result"} or {"error"}.
    """
    if _cur_depth() >= _MAX_DEPTH:
        return {
            "error": "Delegation depth limit reached; do this step yourself "
            "instead of delegating further."
        }

    agents = cfg.get("agents", [])
    match = next(
        (a for a in agents if a.get("name", "").lower() == (agent or "").lower()), None
    )
    if not match:
        names = ", ".join(a.get("name", "") for a in agents)
        return {"error": f"Unknown agent '{agent}'. Available: {names}"}
    if not (task or "").strip():
        return {"error": "No task provided to delegate."}

    # Imported lazily to avoid a circular import (orchestrator imports the tool
    # registry which imports this module).
    from agent.orchestrator import run_agent_sync

    system = match.get("system", "You are a helpful assistant.")
    # Sub-agents may use the browser but not computer control, and inherit the
    # user's per-run model only via global settings (kept simple/safe).
    _depth.value = _cur_depth() + 1
    try:
        result = run_agent_sync(
            task,
            system_prompt=system,
            use_computer_tools=False,
            use_browser_tools=cfg.get("browser_enabled", True),
        )
    finally:
        _depth.value = _cur_depth() - 1

    return {"agent": match.get("name", agent), "result": result}


ORCHESTRATION_TOOLS = {
    "list_agents": list_agents,
    "delegate_to_agent": delegate_to_agent,
}

ORCHESTRATION_TOOL_SCHEMAS = [
    {
        "name": "list_agents",
        "description": "List the specialist agents you can delegate sub-tasks to "
        "(each has a name and description). Use this before delegating if unsure "
        "which agent fits.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "delegate_to_agent",
        "description": "Delegate a focused sub-task to another specialist agent and "
        "get its result back. Use this to orchestrate complex builds: break the job "
        "into parts, delegate each to the best-suited agent (e.g. Researcher to "
        "gather info, Writer to draft, Computer Use to run things), then combine "
        "their results yourself. The sub-agent has no memory of this conversation, "
        "so make each task self-contained.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the agent to delegate to (see list_agents).",
                },
                "task": {
                    "type": "string",
                    "description": "A complete, self-contained instruction for the sub-agent.",
                },
            },
            "required": ["agent", "task"],
        },
    },
]
