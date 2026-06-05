"""services/ollama_warmup.py - Pre-warm Ollama so the first request is fast.

Ollama loads the model into memory on the first request, which takes 60–120 s
on most hardware. This service fires a minimal 1-token generation in a
background thread shortly after the app starts, so by the time the user sends
their first message the model is already resident in RAM/VRAM.

Keep-alive pinging: Ollama unloads the model after 5 minutes of idle. The
keepalive loop re-pings every 4 minutes to keep it loaded. When the app has
been idle for more than `IDLE_THRESHOLD_MIN` minutes (default 20) the loop
pauses and only resumes when a real request comes in.
"""

from __future__ import annotations

import threading
import time

import requests as _requests

from aria2.core import config
from aria2.core.events import bus

IDLE_THRESHOLD_MIN = 20   # stop pinging after this many idle minutes
PING_INTERVAL_S    = 240  # ping every 4 min (Ollama keep-alive = 5 min)


class OllamaWarmup:
    def __init__(self):
        self._running   = False
        self._thread: threading.Thread | None = None
        self._last_used = time.time()

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self):
        """Launch warmup + keep-alive in background. No-op if not Ollama."""
        s = config.load()
        if s.get("provider") != "local":
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ollama-warmup")
        self._thread.start()

    def stop(self):
        self._running = False

    def ping(self):
        """Call this when a chat message is sent so the idle counter resets."""
        self._last_used = time.time()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        # Short delay on startup so the UI is rendered first.
        for _ in range(30):
            if not self._running:
                return
            time.sleep(0.1)

        # Initial warmup — load the model.
        self._warmup_once()

        while self._running:
            idle_s = time.time() - self._last_used
            if idle_s < IDLE_THRESHOLD_MIN * 60:
                self._warmup_once()          # extend keep-alive
            # Sleep in 1-second ticks so we can stop quickly.
            for _ in range(PING_INTERVAL_S):
                if not self._running:
                    return
                time.sleep(1)

    def _warmup_once(self):
        s = config.load()
        url   = s.get("ollama_url", "http://localhost:11434").rstrip("/")
        model = s.get("ollama_model", "llama3")
        try:
            # Use Ollama's native /api/generate with keep_alive so the model
            # stays resident after the ping (not just during it).
            _requests.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": "10m"},
                timeout=180,
            )
            bus.publish("ollama.ready", {"model": model})
        except Exception:
            pass  # Ollama not running — silently ignore


warmup = OllamaWarmup()
