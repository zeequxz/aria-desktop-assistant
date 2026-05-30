"""
agent/messaging_tools.py - Tools that let the agent use messaging channels.

These are always registered. They reach the running MessagingService singleton,
so they no-op gracefully if messaging isn't set up.

Tools:
  send_telegram_message(text)        - push a message to your Telegram
  send_discord_message(text)         - post to your Discord channel (webhook)
  notify_user(text)                  - push to every configured channel
  ask_user(question, wait_minutes)   - ask via Telegram and wait for a reply
"""

from agent import messaging


def _svc():
    return messaging.SERVICE


def send_telegram_message(text: str) -> dict:
    svc = _svc()
    if not svc:
        return {"error": "Messaging service not running."}
    return {"sent": svc.send_telegram(text)}


def send_discord_message(text: str) -> dict:
    svc = _svc()
    if not svc:
        return {"error": "Messaging service not running."}
    return {"sent": svc.send_discord(text)}


def notify_user(text: str) -> dict:
    svc = _svc()
    if not svc:
        return {"error": "Messaging service not running."}
    return {"sent": svc.notify(text)}


def ask_user(question: str, wait_minutes: int = 10) -> dict:
    """Send a question to the user on Telegram and wait for their reply.
    Use this in long tasks when you need input or a decision."""
    svc = _svc()
    if not svc:
        return {"error": "Messaging service not running."}
    reply = svc.ask(question, timeout=int(wait_minutes) * 60)
    if reply is None:
        return {"answered": False, "reply": "", "note": "No reply within the time limit."}
    return {"answered": True, "reply": reply}


MESSAGING_TOOLS = {
    "send_telegram_message": send_telegram_message,
    "send_discord_message": send_discord_message,
    "notify_user": notify_user,
    "ask_user": ask_user,
}

MESSAGING_TOOL_SCHEMAS = [
    {
        "name": "send_telegram_message",
        "description": "Send a message to the user's Telegram. Use to deliver "
                       "results or updates when the user isn't at the desktop.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Message body."}},
            "required": ["text"],
        },
    },
    {
        "name": "send_discord_message",
        "description": "Post a message to the user's configured Discord channel "
                       "(via webhook). Good for news digests or status updates.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Message body."}},
            "required": ["text"],
        },
    },
    {
        "name": "notify_user",
        "description": "Push a short notification to every messaging channel the "
                       "user has configured (Telegram and/or Discord). Use when a "
                       "long task finishes.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "ask_user",
        "description": "Ask the user a question on Telegram and WAIT for their "
                       "reply. Use during a long task when you need a decision or "
                       "missing information. Returns the user's reply text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask."},
                "wait_minutes": {"type": "integer",
                                 "description": "How many minutes to wait for a reply (default 10)."},
            },
            "required": ["question"],
        },
    },
]
