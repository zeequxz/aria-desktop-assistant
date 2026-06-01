"""
agent/batch.py - Batch processing tool (advanced mode).

Lets the agent run the same task over a list of items and collect results,
like "translate each of these 20 sentences" or "process every file in this
folder". Each item is run via run_agent_sync so it gets full tool access.

Tools:
  batch_run(items, task_template, max_items)
    items         : list of strings (or a JSON array string)
    task_template : instruction with {item} where the current item goes
    max_items     : cap (default 20, hard cap 50) to prevent runaway loops
"""

import json

MAX_ITEMS_DEFAULT = 20
MAX_ITEMS_HARD = 50


def batch_run(items, task_template: str, max_items: int = MAX_ITEMS_DEFAULT) -> dict:
    """Run task_template for each item and return all results.

    `items` can be a Python list or a JSON array string.
    Use {item} in task_template as the placeholder for the current item.
    """
    if not task_template or "{item}" not in task_template:
        return {"error": "task_template must contain the placeholder {item}."}

    # Accept both a real list and a JSON-encoded list string.
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            # Treat it as a newline-separated list.
            items = [x.strip() for x in items.splitlines() if x.strip()]

    if not isinstance(items, list) or not items:
        return {"error": "items must be a non-empty list."}

    cap = min(int(max_items), MAX_ITEMS_HARD)
    if len(items) > cap:
        items = items[:cap]

    from agent.orchestrator import run_agent_sync

    results = []
    for i, item in enumerate(items, 1):
        prompt = task_template.replace("{item}", str(item))
        result = run_agent_sync(
            prompt,
            system_prompt="You complete a single focused task and return the result.",
            use_computer_tools=False,
            use_browser_tools=True,
        )
        results.append({"item": item, "result": result})

    return {
        "processed": len(results),
        "total_items": len(items),
        "results": results,
    }


BATCH_TOOLS = {"batch_run": batch_run}

BATCH_TOOL_SCHEMAS = [
    {
        "name": "batch_run",
        "description": "Run the same task over a list of items and collect all "
        "results. Use for bulk operations: translate a list of phrases, summarise "
        "multiple documents, process every file in a directory, etc. "
        "Put {item} in task_template where each item should go. "
        "Capped at 50 items per call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of items to process.",
                },
                "task_template": {
                    "type": "string",
                    "description": "Task instruction with {item} placeholder.",
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum items to process (default 20, max 50).",
                },
            },
            "required": ["items", "task_template"],
        },
    }
]
