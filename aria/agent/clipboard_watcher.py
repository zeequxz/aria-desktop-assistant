"""
agent/clipboard_watcher.py - Watches the clipboard for new content.

When the user copies text, ARIA can offer to summarize, translate,
rewrite, or do anything else with it. Runs as a background thread.
"""

import threading
import time
from typing import Callable, Optional

PYPERCLIP_AVAILABLE = False
try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    pass

MIN_LENGTH = 80          # Only trigger for meaningful text
CHECK_INTERVAL = 1.2     # Seconds between clipboard checks
COOLDOWN = 5.0           # Don't trigger again for 5 seconds after an action


class ClipboardWatcher:
    def __init__(self, on_new_content: Callable[[str], None]):
        """
        on_new_content: called with the new clipboard text when something meaningful is copied.
        """
        self.on_new_content = on_new_content
        self._running = False
        self._last_content = ""
        self._last_trigger_time = 0
        self._thread = None
        self._enabled = True

    def start(self):
        if not PYPERCLIP_AVAILABLE:
            return
        self._running = True
        try:
            self._last_content = pyperclip.paste()
        except Exception:
            self._last_content = ""
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def _watch(self):
        while self._running:
            time.sleep(CHECK_INTERVAL)
            if not self._enabled:
                continue
            try:
                current = pyperclip.paste()
            except Exception:
                continue

            if (
                current != self._last_content
                and len(current) >= MIN_LENGTH
                and current.strip()
                and not current.startswith("file://")   # ignore file paths
                and time.time() - self._last_trigger_time > COOLDOWN
            ):
                self._last_content = current
                self._last_trigger_time = time.time()
                try:
                    self.on_new_content(current)
                except Exception:
                    pass
            elif current != self._last_content:
                self._last_content = current

    @staticmethod
    def is_available() -> bool:
        return PYPERCLIP_AVAILABLE
