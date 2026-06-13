"""services/ambient_service.py - Learn workflows by watching, propose automations.

The local-presence moat: a cloud agent can't legally watch your machine, so it
can't learn what you actually do. This service (opt-in, off by default) observes
project folders, records observations, and mines *recurring patterns* into
automation **proposals** the user can accept with one click.

It is intentionally conservative and dependency-free:
  * a polling watcher snapshots file mtimes in each project folder,
  * recurring edit patterns (same files/extensions touched repeatedly around the
    same time of day) become a proposal,
  * accepting a proposal materialises a real trigger.

Everything stays local. The point is the *signal no competitor can collect*, not
a heavyweight rules engine — the mining can later be handed to the LLM itself.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from aria2.core import config, db
from aria2.core.events import bus
from aria2.core.ids import new_id, now_ms

_TEXT_EXTS = {".py", ".js", ".ts", ".md", ".txt", ".json", ".sql", ".yaml", ".yml",
              ".go", ".rs", ".java", ".css", ".html"}
_MINE_AFTER = 5          # this many matching observations triggers a proposal
_MINE_WINDOW_MS = 14 * 24 * 3600 * 1000


# ── Observation log ─────────────────────────────────────────────────────────────

def record(kind: str, signature: str, data: dict, project_id: str | None = None) -> None:
    db.insert("observations", {
        "id": new_id("obs"), "kind": kind, "project_id": project_id,
        "signature": signature, "data_json": json.dumps(data), "created_at": now_ms(),
    })


def recent_observations(limit: int = 100) -> list[dict]:
    rows = db.all("SELECT * FROM observations ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


# ── Proposals ───────────────────────────────────────────────────────────────────

def list_proposals(status: str = "pending") -> list[dict]:
    rows = db.all("SELECT * FROM proposals WHERE status=? ORDER BY confidence DESC, created_at DESC",
                  (status,))
    return [dict(r) for r in rows]


def _propose(kind: str, title: str, rationale: str, payload: dict, confidence: float) -> str:
    # De-dupe: don't re-propose the same title while one is still pending.
    dup = db.one("SELECT id FROM proposals WHERE title=? AND status='pending'", (title,))
    if dup:
        return dup["id"]
    pid = new_id("prop")
    db.insert("proposals", {
        "id": pid, "kind": kind, "title": title, "rationale": rationale,
        "payload_json": json.dumps(payload), "status": "pending",
        "confidence": confidence, "created_at": now_ms(),
    })
    bus.publish("proposal.created", {"proposal_id": pid, "title": title})
    return pid


def accept_proposal(proposal_id: str) -> dict:
    """Materialise a proposal. Automation proposals become real triggers."""
    p = db.one("SELECT * FROM proposals WHERE id=?", (proposal_id,))
    if not p:
        return {"error": "not found"}
    payload = json.loads(p["payload_json"] or "{}")
    result = {}
    if p["kind"] == "automation":
        from aria2.services import automation_service

        t = automation_service.create(
            payload.get("name", p["title"]), "schedule", payload.get("prompt", ""),
            project_id=payload.get("project_id", "general"),
            agent_id=payload.get("agent_id", "assistant"),
            config_obj=payload.get("config", {"interval": "daily", "at": "09:00"}),
            enabled=False,  # created disabled so the user reviews before it fires
        )
        result = {"trigger_id": t["id"]}
    elif p["kind"] == "agent":
        # Self-improvement: append learned guidance to the agent's system prompt.
        from aria2.services import self_improvement_service

        result = self_improvement_service.apply_agent_proposal(payload)
    db.update("proposals", proposal_id, {"status": "accepted"})
    return {"accepted": True, **result}


def dismiss_proposal(proposal_id: str) -> None:
    db.update("proposals", proposal_id, {"status": "dismissed"})


# ── Pattern miner ───────────────────────────────────────────────────────────────

def mine() -> int:
    """Scan recent file-change observations for recurring patterns and emit
    proposals. Returns the number of new proposals. Safe to call repeatedly."""
    cutoff = now_ms() - _MINE_WINDOW_MS
    rows = db.all(
        "SELECT * FROM observations WHERE kind='file_change' AND created_at > ?", (cutoff,)
    )
    if len(rows) < _MINE_AFTER:
        return 0

    # Signature = "<project>:<ext>" ; count repeats and the typical hour.
    sig_counts = Counter(r["signature"] for r in rows)
    hours: dict[str, list[int]] = {}
    project_of: dict[str, str] = {}
    for r in rows:
        hours.setdefault(r["signature"], []).append(
            datetime.fromtimestamp(r["created_at"] / 1000).hour
        )
        project_of[r["signature"]] = r["project_id"] or "general"

    made = 0
    for sig, count in sig_counts.items():
        if count < _MINE_AFTER:
            continue
        ext = sig.split(":")[-1]
        hrs = hours[sig]
        typical = Counter(hrs).most_common(1)[0][0]
        title = f"Automate a daily review of {ext} files"
        rationale = (f"You've edited {ext} files {count} times recently, often around "
                     f"{typical:02d}:00. ARIA can summarise what changed each day.")
        payload = {
            "name": f"Daily {ext} digest",
            "project_id": project_of[sig],
            "agent_id": "assistant",
            "prompt": f"Review the {ext} files changed in the project today and give me "
                      "a short digest of what changed and anything worth my attention.",
            "config": {"interval": "daily", "at": f"{(typical + 1) % 24:02d}:00"},
        }
        conf = min(0.9, 0.4 + count * 0.08)
        before = db.one("SELECT id FROM proposals WHERE title=? AND status='pending'", (title,))
        _propose("automation", title, rationale, payload, conf)
        if not before:
            made += 1
    return made


# ── Watcher thread ────────────────────────────────────────────────────────────

class AmbientWatcher:
    def __init__(self, poll_seconds: int = 20):
        self._running = False
        self._thread: threading.Thread | None = None
        self._poll = poll_seconds
        self._mtimes: dict[str, float] = {}
        self._first_pass = True

    def start(self):
        if self._running or not config.get("ambient_enabled", False):
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ambient")
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        ticks = 0
        while self._running:
            try:
                if config.get("ambient_enabled", False):
                    self._scan()
                    ticks += 1
                    if ticks % 5 == 0:  # mine periodically, not every poll
                        mine()
            except Exception as e:
                print(f"[Ambient] {e}")
            for _ in range(self._poll):
                if not self._running:
                    return
                time.sleep(1)

    def _scan(self):
        import os

        from aria2.core import fsutil
        from aria2.services import project_service

        seen: set[str] = set()
        for p in project_service.list_projects():
            folder = p.get("folder")
            if not folder or not Path(folder).exists():
                continue
            for dirpath, name in fsutil.walk_files(folder):
                ext = os.path.splitext(name)[1].lower()
                if ext not in _TEXT_EXTS:
                    continue
                key = os.path.join(dirpath, name)
                try:
                    mtime = os.stat(key).st_mtime
                except OSError:
                    continue
                seen.add(key)
                prev = self._mtimes.get(key)
                self._mtimes[key] = mtime
                # First pass just seeds mtimes (no false "changes").
                if not self._first_pass and prev is not None and mtime > prev:
                    record("file_change", f"{p['id']}:{ext}",
                           {"path": key, "name": name}, project_id=p["id"])
        # Bound the cache: drop files no longer present (deleted / project removed),
        # so _mtimes can't grow without limit. Skip if the scan saw nothing (a
        # transient empty pass shouldn't wipe the cache).
        if seen:
            self._mtimes = {k: v for k, v in self._mtimes.items() if k in seen}
        self._first_pass = False


watcher = AmbientWatcher()
