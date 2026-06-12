"""core/logs.py - Structured, rotating application logging.

ARIA2 ships as a windowed app (no console), so `print()` went nowhere — failures
were invisible. This installs a rotating file log under `app_dir()/logs/aria2.log`
(JSON lines, keyed where possible by run_id) and exposes:

    log = logs.get("telegram")
    log.warning(logs.j("poll_failed", error=str(e)))     # structured payload
    logs.tail(200)                                        # for the in-app viewer

`attach_bus()` journals run errors/status off the event bus so every failure is
recorded even when no `except` block logs it.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

from aria2.core import config

_READY = False


def log_path() -> Path:
    d = config.app_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "aria2.log"


def setup(level: int = logging.INFO) -> None:
    """Idempotent: install the rotating file handler on the 'aria2' logger."""
    global _READY
    if _READY:
        return
    root = logging.getLogger("aria2")
    root.setLevel(level)
    root.propagate = False
    try:
        h = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=4_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s  %(message)s"))
        root.addHandler(h)
    except Exception:  # logging must never break startup
        pass
    _READY = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"aria2.{name}")


def j(event: str, **fields) -> str:
    """Render a structured log payload as one JSON string."""
    try:
        return json.dumps({"event": event, **fields}, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"event": event})


def tail(n: int = 200) -> str:
    """Return the last n lines of the log (for the in-app Diagnostics viewer)."""
    try:
        lines = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(no log file yet)"


def attach_bus() -> None:
    """Journal run failures + status changes from the event bus."""
    from aria2.core.events import bus
    rl = get("run")
    bus.subscribe("run.error", lambda p: rl.warning(
        j("run_error", run_id=p.get("run_id"), error=p.get("error"))))
    bus.subscribe("trigger.fired", lambda p: rl.info(
        j("trigger_fired", trigger_id=p.get("trigger_id"), run_id=p.get("run_id"))))
