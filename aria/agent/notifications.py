"""
agent/notifications.py - Persistent notifications inbox.

Stores all ARIA events (task completions, watchdog alerts, heartbeat actions,
skill suggestions) in a JSON log so nothing is lost when the user is away.
Also provides a push() helper the rest of the app calls.

Each notification: {id, type, title, body, ts, read, source}
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
import os

_NOTIF_FILE = (
    Path(os.environ.get("APPDATA", Path.home())) / "ARIA" / "notifications.json"
)
_NOTIF_FILE.parent.mkdir(parents=True, exist_ok=True)
_MAX = 200  # keep the last N


def _load() -> list:
    if _NOTIF_FILE.exists():
        try:
            return json.loads(_NOTIF_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(items: list):
    _NOTIF_FILE.write_text(
        json.dumps(items[-_MAX:], indent=2, ensure_ascii=False), encoding="utf-8"
    )


def push(title: str, body: str, ntype: str = "info", source: str = "") -> dict:
    """Add a notification and return it."""
    notif = {
        "id": uuid.uuid4().hex[:12],
        "type": ntype,  # info | task | watchdog | heartbeat | skill
        "title": title,
        "body": body,
        "ts": datetime.now().isoformat(),
        "read": False,
        "source": source,
    }
    items = _load()
    items.append(notif)
    _save(items)
    return notif


def list_notifications(unread_only: bool = False, limit: int = 50) -> list:
    items = _load()
    if unread_only:
        items = [n for n in items if not n.get("read")]
    return items[-limit:]


def mark_read(notif_id: str = None):
    """Mark one (by id) or all as read."""
    items = _load()
    for n in items:
        if notif_id is None or n["id"] == notif_id:
            n["read"] = True
    _save(items)


def unread_count() -> int:
    return sum(1 for n in _load() if not n.get("read"))


def clear_all():
    _save([])
