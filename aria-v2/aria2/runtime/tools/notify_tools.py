"""runtime/tools/notify_tools.py - Let agents message the user (outbound Telegram).

Offered when the Telegram bridge is configured, so an agent — including a
scheduled task or the heartbeat — can proactively ping you on your phone.
"""

from __future__ import annotations

from aria2.runtime.tools.base import Tool


def make_notify_tools() -> list[Tool]:
    from aria2.services import messaging_service

    def notify_user(message: str) -> dict:
        return messaging_service.notify(message)

    return [
        Tool("notify_user",
             "Send a short message to the user via Telegram (their phone).",
             {"type": "object", "properties": {"message": {"type": "string"}},
              "required": ["message"]},
             notify_user, default_policy="allow"),
    ]


def make_discord_tools() -> list[Tool]:
    from aria2.services import messaging_service

    def post_discord(message: str, channel: str = None) -> dict:
        return messaging_service.post_discord(message, channel)

    names = [c.get("name", "") for c in messaging_service.discord_channels()]
    chan_hint = (f" Named channels: {', '.join(n for n in names if n)}."
                 if names else " Posts to the default webhook if no channel is given.")
    return [
        Tool("post_discord",
             "Post a message to a Discord channel webhook." + chan_hint,
             {"type": "object",
              "properties": {"message": {"type": "string"},
                             "channel": {"type": "string",
                                         "description": "Named channel (optional)."}},
              "required": ["message"]},
             post_discord, default_policy="allow"),
    ]
