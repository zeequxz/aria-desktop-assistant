"""core/events.py - Tiny synchronous in-process event bus.

Services publish facts ("run.step", "chat.message", "run.token"); the GUI and
automations subscribe. Keeping this in one place is what lets every surface
(GUI / CLI / triggers) observe the same engine without wiring callbacks by hand.

Handlers run on whatever thread publishes the event. GUI subscribers must
marshal back onto the UI thread themselves (the CTk app does this with `after`).
"""

from __future__ import annotations

import threading
import traceback
from collections import defaultdict
from typing import Callable

Handler = Callable[[dict], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to a topic. Returns an unsubscribe function.

        Topic matching supports a single trailing wildcard, e.g. "run.*".
        """
        with self._lock:
            self._subs[topic].append(handler)

        def _unsub() -> None:
            with self._lock:
                if handler in self._subs.get(topic, []):
                    self._subs[topic].remove(handler)

        return _unsub

    def publish(self, topic: str, payload: dict | None = None) -> None:
        payload = dict(payload or {})
        payload.setdefault("topic", topic)
        with self._lock:
            handlers: list[Handler] = []
            for pattern, hs in self._subs.items():
                if _matches(pattern, topic):
                    handlers.extend(hs)
        for h in handlers:
            try:
                h(payload)
            except Exception:  # a bad subscriber must never break the publisher
                traceback.print_exc()


def _matches(pattern: str, topic: str) -> bool:
    if pattern == topic or pattern == "*":
        return True
    if pattern.endswith(".*"):
        return topic.startswith(pattern[:-1])
    return False


# Process-wide bus. Simple and sufficient for a single-process desktop app.
bus = EventBus()
