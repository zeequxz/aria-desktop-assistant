"""
agent/watchdog.py - File/folder/URL trigger-on-change service.

Each watch: {id, name, type, target, agent_id, prompt, enabled, last_seen}
  type   : "file" | "folder" | "url"
  target : path or URL
  prompt : instruction sent to the agent when a change is detected
           (can reference {change} for a brief description)

The WatchdogService runs a background thread that polls every POLL_INTERVAL
seconds. On a change it fires the agent via run_agent_sync and pushes the
result to the notifications inbox.
"""

import os
import json
import time
import uuid
import hashlib
import threading
from datetime import datetime
from pathlib import Path

try:
    import requests as _req

    _REQ = True
except ImportError:
    _REQ = False

from config import settings as cfg

POLL_INTERVAL = 60  # seconds between checks
_WATCHES_KEY = "watchdog_watches"


# ── Watch store ─────────────────────────────────────────────────────────────


def list_watches() -> list:
    return cfg.get(_WATCHES_KEY, [])


def add_watch(
    name: str, wtype: str, target: str, prompt: str, agent_id: str = "assistant"
) -> dict:
    if wtype not in ("file", "folder", "url"):
        return {"error": "type must be 'file', 'folder', or 'url'"}
    watch = {
        "id": f"w_{uuid.uuid4().hex[:8]}",
        "name": name,
        "type": wtype,
        "target": target,
        "agent_id": agent_id,
        "prompt": prompt,
        "enabled": True,
        "last_seen": None,
        "created": datetime.now().isoformat(),
    }
    watches = cfg.get(_WATCHES_KEY, [])
    watches.append(watch)
    cfg.set_key(_WATCHES_KEY, watches)
    return watch


def delete_watch(watch_id: str) -> bool:
    watches = [w for w in cfg.get(_WATCHES_KEY, []) if w.get("id") != watch_id]
    cfg.set_key(_WATCHES_KEY, watches)
    return True


# ── Fingerprint helpers ──────────────────────────────────────────────────────


def _fingerprint_file(path: str) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return ""
        stat = p.stat()
        return f"{stat.st_size}:{stat.st_mtime}"
    except Exception:
        return ""


def _fingerprint_folder(path: str) -> str:
    try:
        p = Path(path)
        if not p.is_dir():
            return ""
        entries = sorted(
            (str(x.relative_to(p)), x.stat().st_mtime)
            for x in p.rglob("*")
            if x.is_file()
        )
        return hashlib.md5(str(entries).encode()).hexdigest()
    except Exception:
        return ""


def _fingerprint_url(url: str) -> str:
    if not _REQ:
        return ""
    try:
        resp = _req.get(url, timeout=15, headers={"User-Agent": "ARIA-Watchdog"})
        return hashlib.md5(resp.content).hexdigest()
    except Exception:
        return ""


def _fingerprint(watch: dict) -> str:
    wtype = watch.get("type", "")
    target = watch.get("target", "")
    if wtype == "file":
        return _fingerprint_file(target)
    if wtype == "folder":
        return _fingerprint_folder(target)
    if wtype == "url":
        return _fingerprint_url(target)
    return ""


# ── Service ──────────────────────────────────────────────────────────────────


class WatchdogService:
    def __init__(self):
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            self._check_all()
            # sleep in short bursts so stop() is responsive
            for _ in range(POLL_INTERVAL):
                if not self._running:
                    break
                time.sleep(1)

    def _check_all(self):
        watches = [w for w in cfg.get(_WATCHES_KEY, []) if w.get("enabled")]
        if not watches:
            return
        for watch in watches:
            try:
                self._check_one(watch)
            except Exception:
                pass

    def _check_one(self, watch: dict):
        current = _fingerprint(watch)
        last = watch.get("last_seen")
        # Update last_seen on first check (don't fire on initial load).
        watches = cfg.get(_WATCHES_KEY, [])
        for w in watches:
            if w["id"] == watch["id"]:
                w["last_seen"] = current
        cfg.set_key(_WATCHES_KEY, watches)

        if last is None or last == "":
            return  # first check — just record the baseline
        if current == last or current == "":
            return  # no change or unreachable

        # Change detected — run the agent and push a notification.
        target = watch.get("target", "")
        wtype = watch.get("type", "file")
        change_desc = f"{wtype} '{target}' changed"

        # Expand all supported placeholders so the user's prompt can reference
        # the watched location without needing to hard-code the path.
        raw_prompt = watch.get("prompt", "Describe what changed.")
        prompt = (
            raw_prompt
            .replace("{change}", change_desc)
            .replace("{target}", target)
            .replace("{path}", target)
            .replace("{folder}", target)
            .replace("{url}", target)
        )

        # Always prepend a context block so the agent knows exactly what was
        # being watched and where, even if the prompt doesn't use placeholders.
        context_header = (
            f"[Watchdog alert]\n"
            f"Type: {wtype}\n"
            f"Location: {target}\n"
            f"Status: change detected\n\n"
        )
        full_prompt = context_header + prompt

        from agent.orchestrator import run_agent_sync
        from agent import notifications

        agents = cfg.get("agents", [])
        agent = next(
            (a for a in agents if a["id"] == watch.get("agent_id")),
            agents[0] if agents else None,
        )
        system = agent["system"] if agent else "You are a helpful assistant."

        result = run_agent_sync(
            full_prompt,
            system_prompt=system,
            use_computer_tools=False,
            use_browser_tools=True,
        )

        notifications.push(
            title=f"🔔 {watch['name']}",
            body=result or change_desc,
            ntype="watchdog",
            source=watch["id"],
        )


SERVICE: WatchdogService = None


def start_service():
    global SERVICE
    SERVICE = WatchdogService()
    SERVICE.start()
    return SERVICE
