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
_WRITE = ["write_file", "edit_file"]
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


def process_message(text: str, send=None, source: str = "telegram",
                    external_id=None) -> dict:
    """Run the agent on an authorised inbound message under the configured access
    level, reply via `send`, and return {reply, run_id}. (No allowlist check —
    callers gate authorisation per channel.)

    When `external_id` is given, the conversation is backed by a durable chat
    (find-or-create), so prior turns give the run context and the thread is visible
    in the desktop Chat view. Without it, the message is handled statelessly."""
    s = config.load()
    from aria2.runtime.run_engine import RunEngine, RunRequest
    from aria2.services import agent_service, chat_service, project_service

    level = s.get("messaging_access", "restricted")
    agent = agent_service.get(s.get("messaging_agent", "assistant")) \
        or agent_service.get("assistant")
    project = project_service.get(s.get("messaging_project", "general")) \
        or project_service.get("general")

    # Back the conversation with a durable chat so follow-ups have context and the
    # thread shows up in the GUI; persist the user turn before running.
    chat_id = None
    if external_id is not None:
        chat_id = chat_service.get_or_create_external_chat(
            source, external_id, project["id"], agent["id"])
        chat_service._persist_message(chat_id, "user", [{"type": "text", "text": text}])
        messages = chat_service._history_for_engine(chat_id)
    else:
        messages = [{"role": "user", "content": [{"type": "text", "text": text}]}]

    # Per-messaging-source model override (separate from the global default).
    # "local" forces Ollama; "cloud" forces the configured cloud provider;
    # "default" inherits the global Settings provider.
    mp = s.get("messaging_provider", "default")
    model_overrides = agent_service.overrides_for(agent)
    fallback = False
    if mp == "local":
        model_overrides = {**model_overrides, "provider": "local"}
        # If the local model is down/unavailable, degrade gracefully to cloud so
        # the user still gets a reply (the run engine retries transparently).
        fallback = bool(s.get("messaging_fallback_to_cloud", True))
    elif mp == "cloud":
        # Remove any local override so the global cloud provider is used.
        model_overrides = {k: v for k, v in model_overrides.items()
                          if k != "provider"}

    engine = RunEngine(s)
    req = RunRequest(
        agent=agent, project=project,
        messages=messages,
        kind="trigger", chat_id=chat_id, run_id=new_id("run"),
        include_shell=True, include_computer=(level == "full"),
        policy_overrides=access_overrides(
            level, s.get("messaging_require_confirmation", True)),
        overrides=model_overrides,
        fallback_to_cloud=fallback,
    )
    result = engine.execute(req)
    reply = result.text or f"(no reply — {result.status})"
    # Persist the assistant reply to the durable chat (text only — never a bare
    # tool_use, which would dangle and break the next turn).
    if chat_id:
        visible = chat_service._visible_assistant_content(result.assistant_content)
        if visible:
            chat_service._persist_message(chat_id, "assistant", visible)
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
    return process_message(text, send=send, source="telegram", external_id=chat_id)


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

    @staticmethod
    def _split(text: str, limit: int = 4096) -> list[str]:
        """Split a reply into Telegram-sized chunks (hard cap 4096 chars),
        preferring line boundaries so long answers aren't silently truncated."""
        text = text or ""
        if len(text) <= limit:
            return [text] if text else []
        chunks: list[str] = []
        buf = ""
        for line in text.splitlines(keepends=True):
            while len(line) > limit:          # a single oversized line — hard-split
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.append(line[:limit])
                line = line[limit:]
            if len(buf) + len(line) > limit:
                chunks.append(buf)
                buf = line
            else:
                buf += line
        if buf:
            chunks.append(buf)
        return chunks

    def send_message(self, chat_id, text: str):
        for chunk in self._split(text, 4096):
            try:
                self._api("sendMessage", chat_id=chat_id, text=chunk)
            except Exception:
                pass

    def _dispatch(self, text, chat):
        """Acknowledge immediately and handle the message off the poll thread so
        a slow agent run (a cold local model can take 30 s+) never blocks polling
        for new messages."""
        self.send_message(chat, "…working on it")

        def _work():
            try:
                handle_message(text, chat,
                               send=lambda r, c=chat: self.send_message(c, r))
            except Exception as e:
                self.send_message(chat, f"⚠ Error handling your message: {e}")

        from aria2.runtime import executor
        executor.submit(_work)

    def _drain_backlog(self):
        """Skip messages queued while the bot was offline by confirming pending
        updates (advancing the offset) without dispatching them. Prevents a
        backlog from spawning a flood of stale agent runs on startup."""
        try:
            resp = self._api("getUpdates", offset=-1, timeout=0)
            results = resp.json().get("result", [])
            if results:
                self._offset = results[-1]["update_id"] + 1
        except Exception:
            pass

    def _loop(self):
        if config.get("telegram_drain_backlog", True):
            self._drain_backlog()
        while self._running:
            try:
                resp = self._api("getUpdates", offset=self._offset, timeout=50)
                if resp.status_code != 200:
                    time.sleep(5)            # 5xx/4xx from Telegram — back off
                    continue
                data = resp.json()
                if not data.get("ok", False):
                    # e.g. 409 Conflict (a second poller or a webhook is set) is
                    # returned immediately; without a backoff this becomes a tight
                    # loop hammering the API. Pause and retry.
                    time.sleep(5)
                    continue
                for upd in data.get("result", []):
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message") or {}
                    text = msg.get("text")
                    chat = (msg.get("chat") or {}).get("id")
                    if text and chat is not None:
                        self._dispatch(text, chat)
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
            # Fail closed: an empty allowlist means NOBODY is authorised, the same
            # as the Telegram bridge. (Previously an empty list let *anyone* on the
            # server command the bot — a privilege-escalation hole.)
            if str(message.author.id) not in allow:
                return
            import asyncio

            def _work():
                return process_message(message.content, source="discord",
                                       external_id=message.channel.id)

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
