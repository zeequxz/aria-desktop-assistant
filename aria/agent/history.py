"""
agent/history.py - Chat history persistence.

Saves each conversation to a JSON file in the ARIA history folder.
Users can browse and reopen previous chats.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

HISTORY_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ARIA" / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

MAX_HISTORY_FILES = 200


def save_conversation(
    messages: list,
    agent_id: str,
    title: Optional[str] = None,
) -> str:
    """Save a conversation. Returns the filename."""
    if not messages:
        return ""
    # Auto-generate title from first user message
    if not title:
        first_user = next(
            (m["content"] for m in messages if m["role"] == "user"), ""
        )
        if isinstance(first_user, str):
            title = first_user[:60] + ("…" if len(first_user) > 60 else "")
        else:
            title = "Conversation"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{agent_id}.json"
    path = HISTORY_DIR / filename

    data = {
        "title": title,
        "agent_id": agent_id,
        "timestamp": datetime.now().isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _prune_old_files()
    return filename


def list_conversations(limit: int = 50) -> list:
    """List recent conversations, newest first."""
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
    results = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            results.append({
                "filename": f.name,
                "title": data.get("title", f.stem),
                "agent_id": data.get("agent_id", "assistant"),
                "timestamp": data.get("timestamp", ""),
                "message_count": data.get("message_count", 0),
            })
        except Exception:
            continue
    return results


def load_conversation(filename: str) -> Optional[dict]:
    """Load a saved conversation by filename."""
    path = HISTORY_DIR / filename
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def delete_conversation(filename: str) -> bool:
    path = HISTORY_DIR / filename
    if path.exists():
        path.unlink()
        return True
    return False


def _prune_old_files():
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    for old in files[MAX_HISTORY_FILES:]:
        try:
            old.unlink()
        except Exception:
            pass
