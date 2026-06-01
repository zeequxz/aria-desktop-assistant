"""
agent/heartbeat.py - Proactive autonomy (OpenClaw-style heartbeat).

Every INTERVAL minutes ARIA wakes, looks at its memory + pending tasks, and
decides whether to act. If it does something useful it pushes a notification.
No action is taken silently — computer-use is always disabled for heartbeat
runs; if the agent wants to do something risky it asks via the messaging
channel (Telegram).

Configuration keys:
  heartbeat_enabled  : bool  (default False)
  heartbeat_interval : int   minutes between checks (default 30)
  heartbeat_prompt   : str   override the default check-in prompt
"""

import time
import threading
from datetime import datetime

from config import settings as cfg

DEFAULT_INTERVAL = 30  # minutes
DEFAULT_PROMPT = (
    "You are ARIA running a proactive check-in. Review the following and decide "
    "if there is anything useful you should do right now:\n"
    "1. Pending scheduled tasks (check their last_run and if any are overdue)\n"
    "2. Watchdog alerts or open items in memory\n"
    "3. Anything the user asked you to follow up on\n\n"
    "If there IS something to do, do it and summarise what you did.\n"
    "If there is NOTHING to do, reply with exactly: NOTHING_TO_DO\n"
    "Keep it brief. No computer-use."
)


class HeartbeatService:
    def __init__(self):
        self._running = False
        self._thread = None

    def start(self):
        if not cfg.get("heartbeat_enabled", False):
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def restart(self):
        """Call after settings change."""
        self.stop()
        time.sleep(0.1)
        self.start()

    def _loop(self):
        # Initial delay: wait one full interval before first check so startup
        # isn't noisy.
        interval_s = cfg.get("heartbeat_interval", DEFAULT_INTERVAL) * 60
        for _ in range(max(interval_s, 60)):
            if not self._running:
                return
            time.sleep(1)

        while self._running:
            try:
                self._tick()
            except Exception:
                pass
            interval_s = cfg.get("heartbeat_interval", DEFAULT_INTERVAL) * 60
            for _ in range(max(interval_s, 60)):
                if not self._running:
                    return
                time.sleep(1)

    def _tick(self):
        if not cfg.get("heartbeat_enabled", False):
            return

        from agent.orchestrator import run_agent_sync
        from agent import notifications

        prompt = cfg.get("heartbeat_prompt", "") or DEFAULT_PROMPT
        agents = cfg.get("agents", [])
        system = agents[0]["system"] if agents else "You are a helpful assistant."

        result = run_agent_sync(
            prompt,
            system_prompt=system,
            use_computer_tools=False,
            use_browser_tools=True,
        )

        if not result or "NOTHING_TO_DO" in result.upper():
            return  # quiet check — no notification needed

        notifications.push(
            title=f"💓 Heartbeat — {datetime.now().strftime('%H:%M')}",
            body=result,
            ntype="heartbeat",
            source="heartbeat",
        )

        # Also notify on Telegram if messaging is configured.
        try:
            from agent.messaging import SERVICE as msg_svc

            if msg_svc:
                msg_svc.notify(f"💓 ARIA heartbeat:\n\n{result[:1000]}")
        except Exception:
            pass


SERVICE: HeartbeatService = None


def start_service():
    global SERVICE
    SERVICE = HeartbeatService()
    SERVICE.start()
    return SERVICE
