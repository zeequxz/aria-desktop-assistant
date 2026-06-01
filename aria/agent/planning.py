"""
agent/planning.py - A visible plan / to-do list the agent maintains (advanced).

Gives the agent three tools to plan complex work and track progress:

  create_plan(steps)            -> start a checklist of steps
  update_plan(step, status)     -> mark a step todo / doing / done
  show_plan()                   -> render the current checklist

The plan is kept per-thread so concurrent agent runs don't collide, and every
tool returns a formatted checklist string — which the chat already shows as a
tool result, so progress appears live in the conversation.
"""

import threading

_state = threading.local()

_ICON = {"todo": "☐", "doing": "▶", "done": "✓"}


def _plan():
    if not hasattr(_state, "steps"):
        _state.steps = []
    return _state.steps


def _render() -> str:
    steps = _plan()
    if not steps:
        return "(no plan yet)"
    done = sum(1 for s in steps if s["status"] == "done")
    lines = [f"Plan ({done}/{len(steps)} done):"]
    for i, s in enumerate(steps, 1):
        lines.append(f"  {_ICON.get(s['status'], '☐')} {i}. {s['text']}")
    return "\n".join(lines)


def create_plan(steps: list) -> dict:
    """Replace the current plan with a fresh checklist of step descriptions."""
    if not isinstance(steps, list) or not steps:
        return {"error": "Provide a non-empty list of step descriptions."}
    _state.steps = [
        {"text": str(s).strip(), "status": "todo"} for s in steps if str(s).strip()
    ]
    return {"plan": _render()}


def update_plan(step: int, status: str) -> dict:
    """Set a step's status. `step` is the 1-based number; status is one of
    'todo', 'doing', 'done'."""
    steps = _plan()
    if status not in _ICON:
        return {"error": "status must be 'todo', 'doing', or 'done'."}
    try:
        idx = int(step) - 1
    except (TypeError, ValueError):
        return {"error": "step must be a step number."}
    if not (0 <= idx < len(steps)):
        return {"error": f"No step {step}; the plan has {len(steps)} steps."}
    steps[idx]["status"] = status
    return {"plan": _render()}


def show_plan() -> dict:
    """Return the current plan checklist."""
    return {"plan": _render()}


def clear_plan():
    """Reset the plan (called by the app at the start of a new turn)."""
    _state.steps = []


PLANNING_TOOLS = {
    "create_plan": create_plan,
    "update_plan": update_plan,
    "show_plan": show_plan,
}

PLANNING_TOOL_SCHEMAS = [
    {
        "name": "create_plan",
        "description": "Start a visible to-do checklist for a complex task. Pass "
        "the ordered list of steps you intend to take. Do this first for any "
        "multi-step job, then mark steps done as you complete them so the user "
        "can follow your progress.",
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered list of step descriptions.",
                }
            },
            "required": ["steps"],
        },
    },
    {
        "name": "update_plan",
        "description": "Update a plan step's status as you work: 'doing' when you "
        "start it, 'done' when finished. Keeps the user's progress view current.",
        "input_schema": {
            "type": "object",
            "properties": {
                "step": {
                    "type": "integer",
                    "description": "The 1-based step number to update.",
                },
                "status": {
                    "type": "string",
                    "enum": ["todo", "doing", "done"],
                    "description": "New status for the step.",
                },
            },
            "required": ["step", "status"],
        },
    },
    {
        "name": "show_plan",
        "description": "Show the current plan checklist with each step's status.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
