"""services/automation_service.py - Triggers + the run scheduler.

Replaces v1's in-process `schedule` loop with a durable, persisted trigger model
that supports several kinds (schedule today; file/webhook are wired through the
same dispatch path). A trigger binds a project + agent + prompt; when it fires we
create a `task` run through the same RunEngine the GUI uses, with retries.

The Scheduler thread:
  * persists `next_run` so a missed schedule (app was closed) catches up,
  * checks due triggers every 30s,
  * fires them via the engine, recording the run + last_fired.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from aria2.core import config, db
from aria2.core.events import bus
from aria2.core.ids import new_id, now_ms
from aria2.runtime.run_engine import RunEngine, RunRequest
from aria2.services import agent_service, project_service


# ── CRUD ────────────────────────────────────────────────────────────────────

def list_triggers() -> list[dict]:
    return [dict(r) for r in db.all("SELECT * FROM triggers ORDER BY created_at DESC")]


def get(trigger_id: str) -> dict | None:
    r = db.one("SELECT * FROM triggers WHERE id = ?", (trigger_id,))
    return dict(r) if r else None


def create(name: str, kind: str, prompt: str, *, project_id: str = "general",
           agent_id: str = "assistant", config_obj: dict | None = None,
           enabled: bool = True, max_retries: int = 0) -> dict:
    tid = new_id("trg")
    cfg = config_obj or {}
    if kind == "webhook" and "token" not in cfg:
        cfg["token"] = new_id("whk")  # shared secret in the hook URL
    db.insert("triggers", {
        "id": tid, "name": name, "kind": kind, "config_json": json.dumps(cfg),
        "project_id": project_id, "agent_id": agent_id, "prompt": prompt,
        "enabled": 1 if enabled else 0, "max_retries": max_retries,
        "last_fired": None, "next_run": _compute_next_run(kind, cfg),
        "last_run_id": None, "created_at": now_ms(),
    })
    return get(tid)


def update(trigger_id: str, changes: dict) -> None:
    if "config_obj" in changes:
        changes["config_json"] = json.dumps(changes.pop("config_obj"))
    allowed = {k: v for k, v in changes.items() if k in {
        "name", "kind", "config_json", "project_id", "agent_id", "prompt",
        "enabled", "max_retries", "next_run",
    }}
    if "config_json" in allowed or "kind" in allowed:
        t = get(trigger_id)
        kind = allowed.get("kind", t["kind"])
        cfg = json.loads(allowed.get("config_json", t["config_json"]))
        allowed["next_run"] = _compute_next_run(kind, cfg)
    db.update("triggers", trigger_id, allowed)


def delete(trigger_id: str) -> None:
    db.delete("triggers", trigger_id)


# ── Firing ────────────────────────────────────────────────────────────────────

def fire(trigger_id: str, context: str = "") -> str:
    """Run a trigger now (also used by the scheduler/watcher/webhook). Returns
    the run_id. `context` (e.g. a webhook payload or changed-file list) is
    appended to the prompt so the agent can act on the triggering event."""
    t = get(trigger_id)
    if not t:
        raise ValueError("trigger not found")
    project = project_service.get(t["project_id"]) or project_service.get("general")
    agent = agent_service.get(t["agent_id"]) or agent_service.get("assistant")
    settings = config.load()
    engine = RunEngine(settings)
    run_id = new_id("run")
    prompt = t["prompt"]
    if context:
        prompt = f"{prompt}\n\n--- Triggering event ---\n{context}"
    req = RunRequest(
        agent=agent, project=project,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        kind="trigger", trigger_id=trigger_id, run_id=run_id,
        overrides=agent_service.overrides_for(agent),
        include_shell=True,
    )

    def _worker():
        attempts = (t["max_retries"] or 0) + 1
        for i in range(attempts):
            result = engine.execute(req)
            if result.status == "done":
                break
            req.run_id = new_id("run")  # fresh run per retry
            time.sleep(min(60, 2 ** i))
        cfg = json.loads(t["config_json"])
        next_run = _compute_next_run(t["kind"], cfg)
        patch = {"last_fired": now_ms(), "last_run_id": run_id, "next_run": next_run}
        # One-off calendar triggers disable themselves after firing.
        if t["kind"] == "schedule" and cfg.get("interval") == "once":
            patch["enabled"] = 0
            patch["next_run"] = None
        db.update("triggers", trigger_id, patch)
        bus.publish("trigger.fired", {"trigger_id": trigger_id, "run_id": run_id})

    threading.Thread(target=_worker, daemon=True, name=f"trigger-{trigger_id}").start()
    return run_id


# ── Calendar helpers ────────────────────────────────────────────────────────

def scheduled_in_month(year: int, month: int) -> dict[int, list[dict]]:
    """Map day-of-month → triggers whose next_run falls in that month."""
    out: dict[int, list[dict]] = {}
    for t in list_triggers():
        nr = t.get("next_run")
        if not nr:
            continue
        d = datetime.fromtimestamp(nr / 1000)
        if d.year == year and d.month == month:
            out.setdefault(d.day, []).append(t)
    return out


def schedule_once(name: str, prompt: str, date_str: str, at: str = "09:00", *,
                  project_id: str = "general", agent_id: str = "assistant") -> dict:
    """Create a one-off calendar trigger for a specific date+time."""
    return create(name, "schedule", prompt, project_id=project_id, agent_id=agent_id,
                  config_obj={"interval": "once", "date": date_str, "at": at})


# ── Webhook helpers ─────────────────────────────────────────────────────────

def webhook_url(trigger: dict) -> str:
    """The localhost URL that fires a webhook trigger."""
    cfg = json.loads(trigger.get("config_json") or "{}")
    port = config.get("webhook_port", 8765)
    return f"http://127.0.0.1:{port}/hook/{trigger['id']}?token={cfg.get('token','')}"


# ── File trigger helpers ────────────────────────────────────────────────────

def _file_signature(path: str) -> tuple[float, int]:
    """(latest mtime, file count) under a file/folder path — cheap change key."""
    p = Path(path)
    if not p.exists():
        return (0.0, 0)
    if p.is_file():
        return (p.stat().st_mtime, 1)
    latest, count = 0.0, 0
    for f in p.rglob("*"):
        if f.is_file() and ".git" not in f.parts:
            try:
                latest = max(latest, f.stat().st_mtime)
                count += 1
            except OSError:
                pass
    return (latest, count)


# ── Schedule maths ──────────────────────────────────────────────────────────

def _compute_next_run(kind: str, cfg: dict) -> int | None:
    """For schedule triggers, return the next fire time (epoch ms)."""
    if kind != "schedule":
        return None
    interval = cfg.get("interval", "daily")
    at = cfg.get("at", "09:00")
    try:
        hh, mm = (int(x) for x in at.split(":"))
    except Exception:
        hh, mm = 9, 0
    now = datetime.now()
    if interval == "once":
        # A one-off at a specific calendar date+time (calendar view). Past => None.
        date_str = cfg.get("date")
        if not date_str:
            return None
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hh, minute=mm)
        except Exception:
            return None
        return int(d.timestamp() * 1000) if d > now else None
    if interval == "hourly":
        nxt = (now + timedelta(hours=1)).replace(minute=mm, second=0, microsecond=0)
    elif interval == "daily":
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
    elif interval == "weekly":
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        while nxt <= now or nxt.weekday() != cfg.get("weekday", 0):
            nxt += timedelta(days=1)
    else:
        nxt = now + timedelta(days=1)
    return int(nxt.timestamp() * 1000)


# ── Scheduler thread ──────────────────────────────────────────────────────────

class Scheduler:
    """Drives schedule triggers (time) and file triggers (folder changes), and
    owns the webhook server's lifecycle. All three are just ways to call fire()."""

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._file_state: dict[str, tuple] = {}  # trigger_id -> last signature

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()
        if config.get("webhook_enabled", False):
            webhook_server.start()

    def stop(self):
        self._running = False
        webhook_server.stop()

    def _loop(self):
        while self._running:
            try:
                self._check_schedule()
                self._check_files()
            except Exception as e:  # never let the scheduler thread die
                print(f"[Scheduler] {e}")
            for _ in range(30):
                if not self._running:
                    return
                time.sleep(1)

    def _check_schedule(self):
        now = now_ms()
        due = db.all(
            "SELECT id FROM triggers WHERE enabled=1 AND kind='schedule' "
            "AND next_run IS NOT NULL AND next_run <= ?", (now,),
        )
        for r in due:
            fire(r["id"])

    def _check_files(self):
        rows = db.all("SELECT * FROM triggers WHERE enabled=1 AND kind='file'")
        for t in rows:
            cfg = json.loads(t["config_json"] or "{}")
            path = cfg.get("path", "")
            if not path:
                continue
            sig = _file_signature(path)
            prev = self._file_state.get(t["id"])
            self._file_state[t["id"]] = sig
            # Seed on first sight so we don't fire on startup; fire on real change.
            if prev is not None and sig != prev and sig[0] > 0:
                fire(t["id"], context=f"Detected a change under: {path}")


# ── Webhook server (localhost only) ─────────────────────────────────────────

class WebhookServer:
    """A tiny localhost HTTP listener. POST/GET /hook/<trigger_id>?token=...
    fires the matching enabled webhook trigger, passing the request body as
    context. Bound to 127.0.0.1 so it is never exposed off-machine."""

    def __init__(self):
        self._httpd = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._httpd is not None:
            return
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence default stderr logging
                pass

            def _fire(self):
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 2 or parts[0] != "hook":
                    self.send_response(404); self.end_headers(); return
                trigger_id = parts[1]
                token = parse_qs(parsed.query).get("token", [""])[0]
                t = get(trigger_id)
                if not t or t["kind"] != "webhook" or not t["enabled"]:
                    self.send_response(404); self.end_headers(); return
                cfg = json.loads(t["config_json"] or "{}")
                if cfg.get("token") and token != cfg["token"]:
                    self.send_response(403); self.end_headers(); return
                body = ""
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length:
                    body = self.rfile.read(length).decode("utf-8", "replace")[:8000]
                run_id = fire(trigger_id, context=f"Webhook payload:\n{body}" if body else "")
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"fired": True, "run_id": run_id}).encode())

            def do_POST(self):
                self._fire()

            def do_GET(self):
                self._fire()

        port = config.get("webhook_port", 8765)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True,
                                        name="webhook-server")
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None


webhook_server = WebhookServer()
scheduler = Scheduler()
