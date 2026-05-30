"""
agent/messaging.py - Messaging channels for ARIA (Telegram in/out, Discord out).

Lets you talk to ARIA from Telegram (message it, it runs the agent and replies)
and lets ARIA push messages to you (Telegram and/or a Discord webhook) — for
scheduled digests, "task done" notifications, and asking a question mid-task.

Design:
* No new dependencies — Telegram Bot API and Discord webhooks are plain HTTPS,
  called via `requests` (already a dependency) with a urllib fallback.
* A single background thread long-polls Telegram getUpdates. Inbound messages
  from allow-listed chat IDs run the agent; unknown senders get a reply telling
  them their chat id (so you can add it to the allowlist).
* A module-level singleton (`SERVICE`) lets agent tools reach the running
  service to send/ask without circular imports.

SECURITY: inbound Telegram messages can run the full agent INCLUDING computer
use (per the app's configuration). Only allow-listed chat IDs are honoured, so
guard your bot token and keep the allowlist tight.
"""

import json
import time
import threading
import urllib.request
import urllib.parse
from typing import Callable, Optional

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False

from config import settings as cfg

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Set when the service starts, so agent tools can reach it.
SERVICE: "Optional[MessagingService]" = None


# ── Low-level HTTP helpers (requests if available, else urllib) ──────────────

def _post_json(url: str, payload: dict, timeout: int = 35) -> Optional[dict]:
    try:
        if _REQUESTS:
            r = requests.post(url, json=payload, timeout=timeout)
            return r.json() if r.content else {}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except Exception:
        return None


def _get_json(url: str, timeout: int = 35) -> Optional[dict]:
    try:
        if _REQUESTS:
            r = requests.get(url, timeout=timeout)
            return r.json() if r.content else {}
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


class MessagingService:
    """Owns the Telegram poll loop and outbound sends. `run_agent` is a callable
    (prompt:str) -> str that runs the agent synchronously and returns the reply
    text; it's injected so this module stays decoupled from the orchestrator."""

    def __init__(self, run_agent: Callable[[str], str], on_status: Callable[[str], None] = None):
        self.run_agent = run_agent
        self.on_status = on_status or (lambda s: None)
        self._running = False
        self._thread = None
        self._offset = 0
        # For ask(): the next inbound text from an allowed chat is delivered here.
        self._pending_reply = None
        self._reply_event = threading.Event()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        global SERVICE
        SERVICE = self
        if self._running:
            return
        self._running = True
        if cfg.get("messaging_enabled") and cfg.get("telegram_bot_token"):
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False

    def restart(self):
        """Apply settings changes (token/enabled) by restarting the poll loop."""
        self.stop()
        time.sleep(0.1)
        self._running = True
        if cfg.get("messaging_enabled") and cfg.get("telegram_bot_token"):
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    # ── Telegram inbound ─────────────────────────────────────────────────────

    def _poll_loop(self):
        token = cfg.get("telegram_bot_token", "")
        if not token:
            return
        self.on_status("Messaging: Telegram connected")
        while self._running:
            if not cfg.get("messaging_enabled"):
                break
            url = TELEGRAM_API.format(token=token, method="getUpdates")
            url += f"?timeout=30&offset={self._offset}"
            data = _get_json(url, timeout=40)
            if not data or not data.get("ok"):
                time.sleep(5)
                continue
            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                if text:
                    self._on_inbound(chat_id, text)

    def _on_inbound(self, chat_id: str, text: str):
        allow = [str(c) for c in cfg.get("telegram_allowlist", [])]
        if chat_id not in allow:
            # Tell the unknown sender their id so the owner can allowlist them.
            self.send_telegram(
                f"⛔ Not authorized. Your chat id is: {chat_id}\n"
                "Add it in ARIA → Settings → Messaging to enable.",
                chat_id=chat_id)
            return

        # If something is waiting on ask(), this message is the answer.
        if not self._reply_event.is_set() and self._pending_reply is None and \
                getattr(self, "_awaiting", False):
            self._pending_reply = text
            self._reply_event.set()
            return

        # Otherwise treat it as a new command: run the agent and reply.
        self.send_telegram("…thinking", chat_id=chat_id)
        try:
            reply = self.run_agent(text)
        except Exception as e:
            reply = f"Error: {e}"
        self.send_telegram(reply or "(no response)", chat_id=chat_id)

    # ── Outbound ─────────────────────────────────────────────────────────────

    def send_telegram(self, text: str, chat_id: str = None) -> bool:
        token = cfg.get("telegram_bot_token", "")
        if not token:
            return False
        targets = [chat_id] if chat_id else [str(c) for c in cfg.get("telegram_allowlist", [])]
        ok = False
        url = TELEGRAM_API.format(token=token, method="sendMessage")
        for tid in targets:
            if not tid:
                continue
            # Telegram caps messages at 4096 chars.
            res = _post_json(url, {"chat_id": tid, "text": text[:4000]})
            ok = ok or bool(res and res.get("ok"))
        return ok

    def _discord_webhooks(self) -> dict:
        """Map of channel-name -> webhook URL. Includes the legacy single
        webhook as a channel named 'default' for backward compatibility."""
        hooks = {}
        legacy = cfg.get("discord_webhook_url", "")
        if legacy:
            hooks["default"] = legacy
        for ch in cfg.get("discord_channels", []):
            name = (ch.get("name") or "").strip()
            url = (ch.get("url") or "").strip()
            if name and url:
                hooks[name] = url
        return hooks

    def discord_channel_names(self) -> list:
        return list(self._discord_webhooks().keys())

    def send_discord(self, text: str, channel: str = None) -> bool:
        """Post to a named Discord channel. If `channel` is None, post to every
        configured channel (or, if only the legacy one exists, just that)."""
        hooks = self._discord_webhooks()
        if not hooks:
            return False
        if channel:
            url = hooks.get(channel)
            if not url:
                return False
            targets = [url]
        else:
            targets = list(hooks.values())
        ok = False
        for url in targets:
            # Discord webhooks return 204 No Content on success (res == {} via
            # our helper); treat a non-None result as success.
            res = _post_json(url, {"content": text[:1900]}, timeout=20)
            ok = ok or (res is not None)
        return ok

    def notify(self, text: str) -> bool:
        """Push to every configured channel (Telegram allowlist + Discord)."""
        a = self.send_telegram(text)
        b = self.send_discord(text)
        return a or b

    def ask(self, question: str, timeout: int = 600) -> Optional[str]:
        """Send a question to Telegram and block until the next allow-listed
        reply (or timeout). Returns the reply text, or None on timeout. Used by
        long-running tasks that need input."""
        if not cfg.get("telegram_bot_token"):
            return None
        self._pending_reply = None
        self._reply_event.clear()
        self._awaiting = True
        try:
            if not self.send_telegram(f"❓ {question}\n\n(Reply here within "
                                      f"{timeout // 60} min.)"):
                return None
            got = self._reply_event.wait(timeout=timeout)
            return self._pending_reply if got else None
        finally:
            self._awaiting = False
            self._pending_reply = None
            self._reply_event.clear()


# ── Status helpers for the UI ────────────────────────────────────────────────

def telegram_get_me(token: str) -> Optional[dict]:
    """Validate a bot token; returns the bot info dict or None."""
    if not token:
        return None
    data = _get_json(TELEGRAM_API.format(token=token, method="getMe"), timeout=15)
    if data and data.get("ok"):
        return data.get("result")
    return None
