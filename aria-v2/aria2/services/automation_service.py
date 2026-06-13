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
import re
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
        final_run_id = req.run_id
        for i in range(attempts):
            final_run_id = req.run_id
            result = engine.execute(req)
            if result.status == "done":
                break
            if i < attempts - 1:
                req.run_id = new_id("run")  # fresh run per retry
                time.sleep(min(60, 2 ** i))
        # NB: next_run advancement / one-off disabling is owned by the scheduler's
        # claim step (see Scheduler._check_schedule), which advances next_run
        # *before* dispatch so a long run can't be re-fired on the next tick.
        # Here we only record the outcome (the final attempt's run id).
        db.update("triggers", trigger_id,
                  {"last_fired": now_ms(), "last_run_id": final_run_id})
        bus.publish("trigger.fired", {"trigger_id": trigger_id, "run_id": final_run_id})
        # Chat-bound loops post their result back into the originating chat.
        cfg = json.loads(t["config_json"] or "{}")
        if cfg.get("chat_id"):
            _post_loop_result(
                cfg["chat_id"],
                (result.text or "").strip() or f"(no reply — {result.status})")

    from aria2.runtime import executor
    executor.submit(_worker)
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


# ── Loop prompting ("/loop 10m <prompt>" in chat) ───────────────────────────

LOOP_USAGE = (
    "🔁 Loop prompting — run a prompt on a schedule and post results here:\n"
    "  /loop 10m <prompt>   — every 10 minutes (also h = hours, d = days)\n"
    "  /loop list           — show this chat's active loops\n"
    "  /loop stop           — stop this chat's loops\n"
    "Loops also appear in the Automations tab."
)

_LOOP_RE = re.compile(r"^(\d+)\s*([mhd])\s+(.+)$", re.IGNORECASE | re.DOTALL)


def parse_loop_command(text: str) -> dict:
    """Parse a '/loop …' chat command.

    Returns {"action": "create", "every_minutes": N, "prompt": str} or
    {"action": "stop"|"list"|"help"|"none"} (+ "error" for a bad interval)."""
    t = (text or "").strip()
    if not t.lower().startswith("/loop"):
        return {"action": "none"}
    rest = t[5:].strip()
    if not rest or rest.lower() in ("help", "?"):
        return {"action": "help"}
    if rest.lower() == "stop":
        return {"action": "stop"}
    if rest.lower() == "list":
        return {"action": "list"}
    m = _LOOP_RE.match(rest)
    if not m:
        return {"action": "help",
                "error": "Couldn't read that — use an interval like 10m, 2h or 1d."}
    n, unit, prompt = int(m.group(1)), m.group(2).lower(), m.group(3).strip()
    minutes = n * {"m": 1, "h": 60, "d": 1440}[unit]
    return {"action": "create", "every_minutes": max(1, minutes), "prompt": prompt}


def create_loop(prompt: str, every_minutes: int, *, project_id: str = "general",
                agent_id: str = "assistant", chat_id: str | None = None) -> dict:
    """Create a recurring loop trigger bound to a chat (results post back there)."""
    name = "Loop: " + (prompt[:40] + ("…" if len(prompt) > 40 else ""))
    return create(name, "schedule", prompt, project_id=project_id, agent_id=agent_id,
                  config_obj={"interval": "minutes",
                              "every": max(1, int(every_minutes)),
                              "chat_id": chat_id or "", "loop": True})


def loops_for_chat(chat_id: str) -> list[dict]:
    out = []
    for t in list_triggers():
        if t["kind"] != "schedule" or not t["enabled"]:
            continue
        cfg = json.loads(t["config_json"] or "{}")
        if cfg.get("loop") and cfg.get("chat_id") == chat_id:
            out.append(t)
    return out


def stop_loops(chat_id: str) -> int:
    """Disable all of a chat's loops. Returns how many were stopped."""
    loops = loops_for_chat(chat_id)
    for t in loops:
        db.update("triggers", t["id"], {"enabled": 0, "next_run": None})
    return len(loops)


def _post_loop_result(chat_id: str, text: str) -> None:
    """Post a loop run's reply into the chat it was created from, so results
    show up in the conversation — not only in the Runs tab."""
    try:
        from aria2.services import chat_service

        chat_service._persist_message(
            chat_id, "assistant", [{"type": "text", "text": f"🔁 {text}"}])
        bus.publish("loop.result", {"chat_id": chat_id, "text": text})
    except Exception:
        pass


# ── Webhook helpers ─────────────────────────────────────────────────────────

def webhook_url(trigger: dict) -> str:
    """The localhost URL that fires a webhook trigger."""
    cfg = json.loads(trigger.get("config_json") or "{}")
    port = config.get("webhook_port", 8765)
    return f"http://127.0.0.1:{port}/hook/{trigger['id']}?token={cfg.get('token','')}"


# ── File trigger helpers ────────────────────────────────────────────────────

# Vendored / build dirs to skip when signing a watched folder — descending into
# them would make every scheduler tick (~30s) walk node_modules etc.
_IGNORE_DIRS = {"node_modules", "__pycache__", "venv", "dist", "build", "target",
                "out", "bin", "obj", ".tox", ".mypy_cache", ".pytest_cache",
                ".gradle", ".next", ".cache"}


def _file_signature(path: str) -> tuple[float, int]:
    """(latest mtime, file count) under a file/folder path — cheap change key."""
    import os

    p = Path(path)
    if not p.exists():
        return (0.0, 0)
    if p.is_file():
        return (p.stat().st_mtime, 1)
    latest, count = 0.0, 0
    for dirpath, dirs, files in os.walk(path):
        # Prune ignored + hidden dirs in place so we never walk node_modules/.git.
        dirs[:] = [d for d in dirs
                   if d not in _IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            try:
                latest = max(latest, os.stat(os.path.join(dirpath, name)).st_mtime)
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
    if interval == "minutes":
        # Loop prompting: re-run every N minutes (scheduler ticks every 30 s, so
        # the practical minimum is 1 minute).
        every = max(1, int(cfg.get("every", 10) or 10))
        return int((now + timedelta(minutes=every)).timestamp() * 1000)
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
        self._last_maint = 0.0

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
        from aria2.core import logs
        log = logs.get("scheduler")
        while self._running:
            try:
                self._check_schedule()
                self._check_files()
                self._maybe_maintain(log)
            except Exception:  # never let the scheduler thread die
                log.exception(logs.j("scheduler_tick_failed"))
            for _ in range(30):
                if not self._running:
                    return
                time.sleep(1)

    def _maybe_maintain(self, log):
        """Periodic housekeeping (every ~6h): decay stale, unused memories so the
        store doesn't grow without bound."""
        now = time.time()
        if now - self._last_maint < 6 * 3600:
            return
        self._last_maint = now
        try:
            from aria2.services import memory_service
            n = memory_service.decay()
            if n:
                log.info(logs.j("memory_decay", removed=n))
            merged = memory_service.consolidate_all()
            if merged:
                log.info(logs.j("memory_consolidate", merged=merged))
        except Exception:
            log.exception(logs.j("memory_decay_failed"))

    def _check_schedule(self):
        now = now_ms()
        due = db.all(
            "SELECT * FROM triggers WHERE enabled=1 AND kind='schedule' "
            "AND next_run IS NOT NULL AND next_run <= ?", (now,),
        )
        for r in due:
            cfg = json.loads(r["config_json"] or "{}")
            # Claim the trigger BEFORE dispatching: advance next_run (or disable a
            # one-off) now. A run can take far longer than the 30s tick; without
            # this the trigger stays "due" and is re-fired every tick, spawning
            # duplicate concurrent runs of the same scheduled task.
            if cfg.get("interval") == "once":
                db.update("triggers", r["id"], {"enabled": 0, "next_run": None})
            else:
                db.update("triggers", r["id"],
                          {"next_run": _compute_next_run("schedule", cfg)})
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
                # Prefer the Authorization: Bearer header (keeps the secret out
                # of URLs/logs); fall back to ?token= for back-compat.
                token = parse_qs(parsed.query).get("token", [""])[0]
                auth = self.headers.get("Authorization", "")
                if auth[:7].lower() == "bearer ":
                    token = auth[7:].strip() or token
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
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as e:
            # Port already in use / not bindable — don't let this crash app start.
            print(f"[Webhook] could not bind 127.0.0.1:{port}: {e}")
            self._httpd = None
            return
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
