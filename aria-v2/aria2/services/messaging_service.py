"""services/messaging_service.py - Telegram bridge with tiered access control.

Ports v1's Telegram capability onto the v2 engine. A long-poll bot receives
messages, checks the sender against an allowlist, runs the agent, and replies.
The key safety feature: each session runs at a configurable **access level** that
maps to per-run tool-policy overrides —

    full        → allow shell + file writes + full PC control (mouse/keyboard)
    restricted  → allow read/search/memory + file writes; DENY shell + PC control
    chat_only   → deny all tools; the agent can only converse

Because Telegram has no interactive approval dialog, "ask" tools resolve to deny
there; "full" explicitly opts in. Only allowlisted chat IDs can command the bot;
unknown senders get their ID echoed so the user can add it.
"""

from __future__ import annotations

import threading
import time

import requests

from aria2.core import config
from aria2.core.events import bus
from aria2.core.ids import new_id
from aria2.runtime.tools.computer_tools import COMPUTER_TOOL_NAMES

_SAFE = ["read_file", "list_dir", "search_knowledge", "recall", "remember"]
_WRITE = ["write_file"]
_SHELL = ["run_shell", "run_python"]


def access_overrides(level: str, require_confirmation: bool = True) -> dict:
    """Map an access level to per-tool allow/deny overrides for a run.

    At "full", shell + PC-control tools are set to "ask" (so the host approval
    dialog must confirm them) unless require_confirmation is False, in which case
    they auto-allow. "ask" with no host approver resolves to deny — fail-safe."""
    if level == "full":
        risky = "ask" if require_confirmation else "allow"
        return {**{t: "allow" for t in _SAFE + _WRITE},
                **{t: risky for t in _SHELL + COMPUTER_TOOL_NAMES}}
    if level == "restricted":
        return {**{t: "allow" for t in _SAFE + _WRITE},
                **{t: "deny" for t in _SHELL + COMPUTER_TOOL_NAMES}}
    # chat_only
    return {t: "deny" for t in _SAFE + _WRITE + _SHELL + COMPUTER_TOOL_NAMES}


def process_message(text: str, send=None, source: str = "telegram") -> dict:
    """Run the agent on an authorised inbound message under the configured access
    level, reply via `send`, and return {reply, run_id}. (No allowlist check —
    callers gate authorisation per channel.)"""
    s = config.load()
    from aria2.runtime.run_engine import RunEngine, RunRequest
    from aria2.services import agent_service, project_service

    level = s.get("messaging_access", "restricted")
    agent = agent_service.get(s.get("messaging_agent", "assistant")) \
        or agent_service.get("assistant")
    project = project_service.get(s.get("messaging_project", "general")) \
        or project_service.get("general")
    engine = RunEngine(s)
    req = RunRequest(
        agent=agent, project=project,
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        kind="trigger", run_id=new_id("run"),
        include_shell=True, include_computer=(level == "full"),
        policy_overrides=access_overrides(
            level, s.get("messaging_require_confirmation", True)),
        overrides=agent_service.overrides_for(agent),
    )
    result = engine.execute(req)
    reply = result.text or f"(no reply — {result.status})"
    if send:
        send(reply)
    bus.publish("messaging.handled", {"source": source, "level": level,
                                      "run_id": result.run_id})
    return {"blocked": False, "reply": reply, "run_id": result.run_id}


def handle_message(text: str, chat_id, send=None) -> dict:
    """Telegram entry point: allowlist-gate the sender, then process."""
    s = config.load()
    allow = [str(x) for x in s.get("telegram_allowlist", [])]
    if str(chat_id) not in allow:
        reply = (f"Not authorised. Your chat id is {chat_id} — add it to the "
                 "allowlist in ARIA → Settings → Messaging.")
        if send:
            send(reply)
        return {"blocked": True, "reply": reply}
    return process_message(text, send=send, source="telegram")


def notify(text: str, chat_id=None) -> dict:
    """Send an outbound Telegram message to a chat (or all allowlisted chats).
    Used by agents (via the notify_user tool), heartbeat, and automations."""
    s = config.load()
    if not s.get("telegram_bot_token"):
        return {"error": "no telegram token configured"}
    targets = [chat_id] if chat_id is not None else s.get("telegram_allowlist", [])
    sent = 0
    for cid in targets:
        try:
            bridge.send_message(cid, text)
            sent += 1
        except Exception:
            pass
    return {"sent": sent}


def discord_channels() -> list[dict]:
    return config.get("discord_channels", []) or []


def post_discord(message: str, channel: str | None = None) -> dict:
    """Post a message to a named Discord channel webhook, or the default webhook
    if no channel is given. Channels are configured in Settings → Messaging."""
    s = config.load()
    url = ""
    if channel:
        match = next((c for c in s.get("discord_channels", [])
                      if c.get("name", "").lower() == channel.lower()), None)
        if not match:
            names = ", ".join(c.get("name", "") for c in s.get("discord_channels", []))
            return {"error": f"Unknown Discord channel '{channel}'. Available: {names}"}
        url = match.get("url", "")
    else:
        url = s.get("discord_webhook_url", "")
    if not url:
        return {"error": "No Discord webhook configured."}
    try:
        r = requests.post(url, json={"content": message[:1900]}, timeout=20)
        return {"sent": True, "status": r.status_code}
    except Exception as e:
        return {"error": str(e)}


def discord_configured() -> bool:
    s = config.load()
    return bool(s.get("discord_webhook_url") or s.get("discord_channels"))


class TelegramBridge:
    """Long-poll Telegram bot. Runs while messaging_enabled + a token are set."""

    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._offset = 0

    def start(self):
        s = config.load()
        if self._running or not s.get("messaging_enabled") or not s.get("telegram_bot_token"):
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram")
        self._thread.start()

    def stop(self):
        self._running = False

    def _api(self, method: str, **params):
        token = config.get("telegram_bot_token", "")
        url = self.API.format(token=token, method=method)
        return requests.post(url, json=params, timeout=70)

    def send_message(self, chat_id, text: str):
        try:
            # Telegram caps messages at 4096 chars.
            self._api("sendMessage", chat_id=chat_id, text=text[:4000])
        except Exception:
            pass

    def _loop(self):
        while self._running:
            try:
                resp = self._api("getUpdates", offset=self._offset, timeout=50)
                data = resp.json()
                for upd in data.get("result", []):
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    text = msg.get("text")
                    chat = (msg.get("chat") or {}).get("id")
                    if text and chat is not None:
                        self.send_message(chat, "…working on it")
                        handle_message(text, chat, send=lambda r, c=chat: self.send_message(c, r))
            except requests.exceptions.RequestException:
                time.sleep(5)  # network blip — back off and retry
            except Exception as e:
                print(f"[Telegram] {e}")
                time.sleep(5)


bridge = TelegramBridge()


class DiscordBridge:
    """Inbound Discord gateway bot (via discord.py, optional). Listens for
    messages from allowlisted user IDs and runs them at the configured access
    level, replying in-channel. No-op if discord.py or a token is missing."""

    def __init__(self):
        self._client = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        s = config.load()
        if (self._running or not s.get("discord_inbound_enabled")
                or not s.get("discord_bot_token")):
            return
        try:
            import discord  # type: ignore
        except Exception:
            print("[Discord] discord.py not installed — inbound bridge disabled.")
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_message(message):  # noqa: ANN001
            if message.author == client.user:
                return
            allow = [str(x) for x in config.get("discord_allowlist", [])]
            if allow and str(message.author.id) not in allow:
                return
            import asyncio

            def _work():
                return process_message(message.content, source="discord")

            result = await asyncio.to_thread(_work)
            try:
                await message.channel.send(result.get("reply", "")[:1900])
            except Exception:
                pass

        def _run():
            import asyncio

            asyncio.set_event_loop(asyncio.new_event_loop())
            try:
                client.run(config.get("discord_bot_token", ""))
            except Exception as e:
                print(f"[Discord] {e}")

        self._running = True
        self._thread = threading.Thread(target=_run, daemon=True, name="discord")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._client is not None:
            try:
                import asyncio

                fut = asyncio.run_coroutine_threadsafe(
                    self._client.close(), self._client.loop)
                fut.result(timeout=5)
            except Exception:
                pass
            self._client = None


discord_bridge = DiscordBridge()
