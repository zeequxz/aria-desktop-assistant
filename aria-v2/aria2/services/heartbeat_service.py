"""services/heartbeat_service.py - Proactive periodic check-in (ported from v1).

On a timer, ARIA runs a heartbeat prompt with a chosen agent and routes the
result to you — over Telegram if the bridge is on, otherwise on the event bus
for the GUI. Lets the assistant act without being asked ("every 30 min, check my
inbox and ping me if anything's urgent"). Off by default.
"""

from __future__ import annotations

import threading
import time

from aria2.core import config
from aria2.core.events import bus
from aria2.core.ids import new_id

_DEFAULT_PROMPT = ("Do a brief proactive check-in: based on the project and my "
                   "memory, is there anything timely I should know or act on right "
                   "now? If nothing is important, reply exactly: NOTHING.")


def run_once(settings: dict | None = None) -> dict:
    """Run a single heartbeat now and deliver the result. Returns {text}."""
    from aria2.runtime.run_engine import RunEngine, RunRequest
    from aria2.services import agent_service, messaging_service, project_service

    s = settings or config.load()
    agent = agent_service.get(s.get("heartbeat_agent", "assistant")) \
        or agent_service.get("assistant")
    project = project_service.get(s.get("heartbeat_project", "general")) \
        or project_service.get("general")
    prompt = s.get("heartbeat_prompt") or _DEFAULT_PROMPT
    engine = RunEngine(s)
    req = RunRequest(
        agent=agent, project=project,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        kind="trigger", run_id=new_id("run"),
        overrides=agent_service.overrides_for(agent),
    )
    result = engine.execute(req)
    text = (result.text or "").strip()
    important = text and text.upper() != "NOTHING"
    if important:
        bus.publish("heartbeat", {"text": text, "run_id": result.run_id})
        if s.get("messaging_enabled") and s.get("telegram_bot_token"):
            messaging_service.notify(f"🫀 ARIA check-in:\n{text}")
    return {"text": text, "important": bool(important), "run_id": result.run_id}


class Heartbeat:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._running or not config.get("heartbeat_enabled", False):
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="heartbeat")
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        # Wait one interval before the first beat (don't fire on launch).
        while self._running:
            interval_min = max(1, int(config.get("heartbeat_interval", 30)))
            for _ in range(interval_min * 60):
                if not self._running:
                    return
                time.sleep(1)
            if not config.get("heartbeat_enabled", False):
                continue
            try:
                run_once()
            except Exception as e:
                print(f"[Heartbeat] {e}")


heartbeat = Heartbeat()
