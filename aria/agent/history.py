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
    project_id: str = "general",
    filename: Optional[str] = None,
) -> str:
    """Save a conversation. Returns the filename. If `filename` is given, the
    existing chat is overwritten (so editing a loaded chat doesn't duplicate)."""
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

    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{agent_id}.json"
    path = HISTORY_DIR / filename

    data = {
        "title": title,
        "agent_id": agent_id,
        "project_id": project_id,
        "timestamp": datetime.now().isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _prune_old_files()
    return filename


def list_conversations(limit: int = 50, project_id: Optional[str] = None) -> list:
    """List recent conversations, newest first. If project_id is given, only
    chats in that project are returned (chats with no project_id count as
    'general')."""
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    results = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            pid = data.get("project_id", "general")
            if project_id is not None and pid != project_id:
                continue
            results.append({
                "filename": f.name,
                "title": data.get("title", f.stem),
                "agent_id": data.get("agent_id", "assistant"),
                "project_id": pid,
                "timestamp": data.get("timestamp", ""),
                "message_count": data.get("message_count", 0),
            })
        except Exception:
            continue
        if len(results) >= limit:
            break
    return results


def search_conversations(query: str, limit: int = 50,
                         project_id: Optional[str] = None) -> list:
    """Full-text search across saved conversations. Returns matches newest
    first; each result has the same keys as list_conversations() plus 'snippet'.
    Matches on the title and on message text (string or block content).
    If project_id is given, only chats in that project are searched."""
    q = (query or "").strip().lower()
    if not q:
        return list_conversations(limit, project_id=project_id)

    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    results = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception:
            continue

        pid = data.get("project_id", "general")
        if project_id is not None and pid != project_id:
            continue

        title = data.get("title", f.stem)
        snippet = ""
        hit = q in title.lower()

        for msg in data.get("messages", []):
            text = _message_text(msg.get("content", ""))
            idx = text.lower().find(q)
            if idx != -1:
                hit = True
                start = max(0, idx - 30)
                snippet = ("…" if start else "") + text[start:idx + 70].strip() + "…"
                break

        if hit:
            results.append({
                "filename": f.name,
                "title": title,
                "agent_id": data.get("agent_id", "assistant"),
                "project_id": pid,
                "timestamp": data.get("timestamp", ""),
                "message_count": data.get("message_count", 0),
                "snippet": snippet,
            })
        if len(results) >= limit:
            break
    return results


def _message_text(content) -> str:
    """Flatten a message's content (plain string or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", "") or "")
            else:
                parts.append(str(block))
        return " ".join(parts)
    return str(content)


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
