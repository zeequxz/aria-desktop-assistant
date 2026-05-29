"""
Example ARIA plugin: Simple reminders stored locally.

Drop this file in the /plugins folder and ARIA can set and list reminders.
"""

import json
from datetime import datetime
from pathlib import Path
import os

REMINDERS_FILE = Path(os.environ.get("APPDATA", Path.home())) / "ARIA" / "reminders.json"
REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load():
    if REMINDERS_FILE.exists():
        with open(REMINDERS_FILE) as f:
            return json.load(f)
    return []


def _save(data):
    with open(REMINDERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_reminder(text: str, due: str = None) -> dict:
    """Add a reminder. due = date string like '2025-06-15' or '2025-06-15 14:00'"""
    reminders = _load()
    reminders.append({
        "id": len(reminders) + 1,
        "text": text,
        "due": due,
        "created": datetime.now().isoformat(),
        "done": False,
    })
    _save(reminders)
    return {"success": True, "reminder": text, "due": due}


def list_reminders(include_done: bool = False) -> dict:
    reminders = _load()
    if not include_done:
        reminders = [r for r in reminders if not r["done"]]
    return {"reminders": reminders, "count": len(reminders)}


def complete_reminder(reminder_id: int) -> dict:
    reminders = _load()
    for r in reminders:
        if r["id"] == reminder_id:
            r["done"] = True
            _save(reminders)
            return {"success": True, "completed": r["text"]}
    return {"error": f"Reminder {reminder_id} not found"}


TOOLS = {
    "add_reminder": add_reminder,
    "list_reminders": list_reminders,
    "complete_reminder": complete_reminder,
}

TOOL_SCHEMAS = [
    {
        "name": "add_reminder",
        "description": "Add a reminder for the user. Optionally set a due date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to remind the user about"},
                "due": {"type": "string", "description": "Due date/time like '2025-06-15' or '2025-06-15 14:00'"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Show the user's pending reminders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_done": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "complete_reminder",
        "description": "Mark a reminder as done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "integer"},
            },
            "required": ["reminder_id"],
        },
    },
]
