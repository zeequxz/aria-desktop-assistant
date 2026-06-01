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

_MEMORY_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ARIA" / "memory"
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# Legacy path (global memory) kept for migration.
MEMORY_FILE = _MEMORY_DIR.parent / "memory.json"


def _memory_file_for(project_id: Optional[str] = None) -> Path:
    """Return the memory file for `project_id`, falling back to global."""
    if project_id and project_id != "general":
        return _MEMORY_DIR / f"{project_id}.json"
    # global / general memory lives in the legacy location so old data is kept
    return MEMORY_FILE


def _active_project_id() -> str:
    """Read the active project from settings (lazy import to avoid cycles)."""
    try:
        from config import settings as cfg

        return cfg.get("active_project", "general") or "general"
    except Exception:
        return "general"


def _load(project_id: Optional[str] = None) -> dict:
    pid = project_id if project_id is not None else _active_project_id()
    path = _memory_file_for(pid)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"facts": [], "preferences": {}, "context": {}}


def _save(data: dict, project_id: Optional[str] = None):
    pid = project_id if project_id is not None else _active_project_id()
    path = _memory_file_for(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Public API ─────────────────────────────────────────────────────────────


def remember(key: str, value: str, category: str = "general") -> dict:
    """Store a fact or preference in memory (active project's namespace)."""
    data = _load()
    for fact in data["facts"]:
        if fact["key"].lower() == key.lower():
            fact["value"] = value
            fact["updated"] = datetime.now().isoformat()
            fact["category"] = category
            _save(data)
            return {"success": True, "updated": True, "key": key, "value": value}
    data["facts"].append(
        {
            "key": key,
            "value": value,
            "category": category,
            "created": datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
        }
    )
    _save(data)
    return {"success": True, "stored": True, "key": key, "value": value}


def recall(key: str = None, category: str = None) -> dict:
    """Retrieve facts from memory (active project's namespace)."""
    data = _load()
    facts = data["facts"]
    if key:
        matches = [
            f
            for f in facts
            if key.lower() in f["key"].lower() or key.lower() in f["value"].lower()
        ]
        return {"query": key, "results": matches, "count": len(matches)}
    if category:
        matches = [f for f in facts if f.get("category") == category]
        return {"category": category, "results": matches, "count": len(matches)}
    return {"all_facts": facts, "count": len(facts)}


def forget(key: str) -> dict:
    """Remove a fact from memory (active project's namespace)."""
    data = _load()
    before = len(data["facts"])
    data["facts"] = [f for f in data["facts"] if f["key"].lower() != key.lower()]
    _save(data)
    return {"success": True, "removed": before - len(data["facts"]), "key": key}


def get_memory_summary() -> str:
    """Compact string of active-project memories to inject into system prompts."""
    data = _load()
    if not data["facts"]:
        return ""
    pid = _active_project_id()
    label = f"project '{pid}'" if pid != "general" else "user"
    lines = [f"[ARIA Memory — facts about this {label}:]"]
    for fact in data["facts"][-50:]:
        lines.append(f"  • {fact['key']}: {fact['value']}")
    return "\n".join(lines)


def clear_all_memory(project_id: Optional[str] = None) -> dict:
    """Wipe all stored memory for the active (or given) project."""
    _save({"facts": [], "preferences": {}, "context": {}}, project_id)
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
                "key": {
                    "type": "string",
                    "description": "Short label for the fact (e.g. 'user name', 'preferred language', 'workplace')",
                },
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
                "key": {
                    "type": "string",
                    "description": "Search for facts matching this keyword (optional)",
                },
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
