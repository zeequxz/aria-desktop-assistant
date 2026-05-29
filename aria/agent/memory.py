"""
agent/memory.py - Persistent memory system for ARIA.

ARIA can store and retrieve facts about the user, their preferences,
work context, and anything worth remembering. Memory persists across sessions.

Stored as a simple JSON file in the user's AppData folder.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

MEMORY_FILE = Path(os.environ.get("APPDATA", Path.home())) / "ARIA" / "memory.json"
MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"facts": [], "preferences": {}, "context": {}}


def _save(data: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Public API ─────────────────────────────────────────────────────────────

def remember(key: str, value: str, category: str = "general") -> dict:
    """Store a fact or preference in memory."""
    data = _load()
    # Check if key already exists and update it
    for fact in data["facts"]:
        if fact["key"].lower() == key.lower():
            fact["value"] = value
            fact["updated"] = datetime.now().isoformat()
            fact["category"] = category
            _save(data)
            return {"success": True, "updated": True, "key": key, "value": value}
    # New fact
    data["facts"].append({
        "key": key,
        "value": value,
        "category": category,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
    })
    _save(data)
    return {"success": True, "stored": True, "key": key, "value": value}


def recall(key: str = None, category: str = None) -> dict:
    """Retrieve facts from memory. Omit key to list all facts."""
    data = _load()
    facts = data["facts"]
    if key:
        matches = [f for f in facts if key.lower() in f["key"].lower() or key.lower() in f["value"].lower()]
        return {"query": key, "results": matches, "count": len(matches)}
    if category:
        matches = [f for f in facts if f.get("category") == category]
        return {"category": category, "results": matches, "count": len(matches)}
    return {"all_facts": facts, "count": len(facts)}


def forget(key: str) -> dict:
    """Remove a fact from memory."""
    data = _load()
    before = len(data["facts"])
    data["facts"] = [f for f in data["facts"] if f["key"].lower() != key.lower()]
    _save(data)
    removed = before - len(data["facts"])
    return {"success": True, "removed": removed, "key": key}


def get_memory_summary() -> str:
    """Returns a compact string of all memories to inject into system prompts."""
    data = _load()
    if not data["facts"]:
        return ""
    lines = ["[ARIA Memory — facts about this user:]"]
    for fact in data["facts"][-50:]:  # Last 50 facts
        lines.append(f"  • {fact['key']}: {fact['value']}")
    return "\n".join(lines)


def clear_all_memory() -> dict:
    """Wipe all stored memory."""
    _save({"facts": [], "preferences": {}, "context": {}})
    return {"success": True, "cleared": True}


# ── Tool registry ──────────────────────────────────────────────────────────

MEMORY_TOOLS = {
    "remember": remember,
    "recall": recall,
    "forget": forget,
}

MEMORY_TOOL_SCHEMAS = [
    {
        "name": "remember",
        "description": "Store a fact about the user in long-term memory so you can recall it in future conversations. Use this for names, preferences, work context, recurring tasks, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short label for the fact (e.g. 'user name', 'preferred language', 'workplace')"},
                "value": {"type": "string", "description": "The value to remember"},
                "category": {
                    "type": "string",
                    "enum": ["general", "preference", "work", "personal", "task"],
                    "default": "general",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Retrieve facts from memory. Search by key, category, or get everything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Search for facts matching this keyword (optional)"},
                "category": {
                    "type": "string",
                    "enum": ["general", "preference", "work", "personal", "task"],
                    "description": "Filter by category (optional)",
                },
            },
        },
    },
    {
        "name": "forget",
        "description": "Remove a fact from memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key of the fact to remove"},
            },
            "required": ["key"],
        },
    },
]
