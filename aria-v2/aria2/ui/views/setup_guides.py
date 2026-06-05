"""ui/views/setup_guides.py - Step-by-step setup guides for external services.

Opened from the ℹ️ buttons beside the Telegram and Discord fields in Settings.
Each guide is a scrollable modal with numbered steps, copyable commands, and
clickable links. No internet connection required to view them.
"""

from __future__ import annotations

import webbrowser

import customtkinter as ctk

from aria2.ui import theme
from aria2.ui.views import widgets as w


# ── Base guide dialog ─────────────────────────────────────────────────────────

class _GuideDialog(ctk.CTkToplevel):
    def __init__(self, parent, title: str, icon: str, steps: list[dict],
                 links: list[tuple] | None = None):
        super().__init__(parent)
        self.title(title)
        self.geometry("580x640")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)
        self.grab_set()

        # Header
        head = ctk.CTkFrame(self, fg_color=theme.SURFACE_2, corner_radius=0)
        head.pack(fill="x")
        ctk.CTkLabel(head, text=f"{icon}  {title}",
                     font=theme.f(4, "bold"), text_color=theme.TEXT).pack(
            anchor="w", padx=20, pady=16)

        # Steps
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        for i, step in enumerate(steps, 1):
            self._step_card(scroll, i, step)

        # Links
        if links:
            link_row = ctk.CTkFrame(self, fg_color=theme.SURFACE_2, corner_radius=0)
            link_row.pack(fill="x")
            ctk.CTkLabel(link_row, text="Useful links:", font=theme.f(-1, "bold"),
                         text_color=theme.TEXT_DIM).pack(side="left", padx=12, pady=8)
            for label, url in links:
                ctk.CTkButton(link_row, text=label, width=160, height=28,
                              fg_color=theme.accent(), font=theme.f(-1),
                              command=lambda u=url: webbrowser.open(u)).pack(
                    side="left", padx=4, pady=8)

        w.ghost_button(self, "Close", self.destroy, width=100, height=34).pack(
            anchor="e", padx=16, pady=12)

    def _step_card(self, parent, num: int, step: dict):
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE,
                            corner_radius=10, border_width=1,
                            border_color=theme.BORDER)
        card.pack(fill="x", padx=12, pady=4)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))

        # Step number badge
        ctk.CTkLabel(top, text=str(num), width=28, height=28,
                     fg_color=theme.accent(), text_color="#fff",
                     font=theme.f(-1, "bold"), corner_radius=14).pack(side="left")
        ctk.CTkLabel(top, text=step["title"], font=theme.f(0, "bold"),
                     text_color=theme.TEXT, anchor="w").pack(side="left", padx=10)

        ctk.CTkLabel(card, text=step["body"], font=theme.f(-1),
                     text_color=theme.TEXT_DIM, wraplength=480,
                     justify="left", anchor="w").pack(anchor="w", padx=12, pady=(0, 4))

        if step.get("code"):
            code = ctk.CTkTextbox(card, height=30, fg_color=theme.SURFACE_2,
                                  font=(theme.MONO, theme.font_size() - 1),
                                  border_width=0, activate_scrollbars=False)
            code.pack(fill="x", padx=12, pady=(0, 8))
            code.insert("1.0", step["code"])
            code.configure(state="disabled")

        if step.get("note"):
            ctk.CTkLabel(card, text=f"💡 {step['note']}", font=theme.f(-2),
                         text_color=theme.WARN, wraplength=480, justify="left",
                         anchor="w").pack(anchor="w", padx=12, pady=(0, 8))


# ── Telegram guide ─────────────────────────────────────────────────────────────

_TELEGRAM_STEPS = [
    {
        "title": "Open Telegram and find BotFather",
        "body": "Search for @BotFather in Telegram and open that chat. BotFather is "
                "Telegram's official bot for creating bots — it's verified with a blue "
                "checkmark.",
    },
    {
        "title": "Create a new bot",
        "body": "Send the command /newbot to BotFather. It will ask you for a name "
                "(display name) and then a username — the username must end in 'bot', "
                "e.g. MyARIAbot.",
        "code": "/newbot",
    },
    {
        "title": "Copy your bot token",
        "body": "BotFather will reply with a token like:\n"
                "  123456789:ABCdefGHIjklMNOpqrSTUvwxYZ\n"
                "Copy the entire token and paste it into the Bot token field in ARIA Settings.",
        "note": "Keep this token private — anyone with it can control your bot.",
    },
    {
        "title": "Start a chat with your bot",
        "body": "Search for your bot by its username in Telegram and press Start. "
                "This is required — Telegram only delivers messages to bots after "
                "the user initiates the conversation.",
    },
    {
        "title": "Find your chat ID",
        "body": "Send any message to your bot. The bot will reply telling you your "
                "chat ID (it won't know any commands yet, so ARIA shows your ID). "
                "Alternatively, open @userinfobot and it will tell you your user ID directly.",
        "note": "Your chat ID is a number like 123456789. Add it to the Allowed chat IDs "
                "field in ARIA Settings.",
    },
    {
        "title": "Enable the bridge and configure access",
        "body": "In ARIA Settings → Messaging:\n"
                "• Paste the bot token\n"
                "• Add your chat ID to the allowlist\n"
                "• Choose an access level (restricted is recommended for safety)\n"
                "• Tick 'Enable Telegram bridge' and Save",
        "note": "Access levels: chat_only = no tools; restricted = read + search + files; "
                "full = shell + PC control (requires host confirmation by default).",
    },
    {
        "title": "Test it",
        "body": "Send 'Hello' to your bot in Telegram. ARIA should reply within a few "
                "seconds. If it doesn't, check that the bot token and your chat ID are "
                "correct and that the bridge is enabled.",
    },
]

_TELEGRAM_LINKS = [
    ("Open BotFather", "https://t.me/BotFather"),
    ("Find your ID", "https://t.me/userinfobot"),
    ("Telegram Bot docs", "https://core.telegram.org/bots"),
]


def show_telegram_guide(parent):
    _GuideDialog(parent, "How to set up Telegram", "📱",
                 _TELEGRAM_STEPS, _TELEGRAM_LINKS)


# ── Discord guide ─────────────────────────────────────────────────────────────

_DISCORD_STEPS = [
    {
        "title": "Open your Discord server",
        "body": "You need to be an admin (or have Manage Webhooks permission) on the "
                "server where you want ARIA to post messages.",
    },
    {
        "title": "Go to Server Settings → Integrations",
        "body": "Right-click the server name → Server Settings → Integrations. "
                "Click Webhooks, then New Webhook.",
    },
    {
        "title": "Configure the webhook",
        "body": "Give it a name (e.g. 'ARIA') and choose the channel where you want "
                "messages to appear. Click Copy Webhook URL.",
        "note": "The webhook URL is a secret — anyone with it can post to your server.",
    },
    {
        "title": "Paste into ARIA Settings",
        "body": "In ARIA Settings → Messaging, paste the webhook URL into the "
                "'Discord default webhook URL' field and Save. ARIA will now be able "
                "to post messages to that channel.",
    },
    {
        "title": "Set up named channels (optional)",
        "body": "If you want ARIA to route messages to different channels by topic, "
                "create multiple webhooks (one per channel) and list them in the "
                "'Named Discord channels' box — one per line in the format:\n"
                "  name=webhook_url",
        "code": "alerts=https://discord.com/api/webhooks/123/abc\nreports=https://discord.com/api/webhooks/456/def",
        "note": "Agents can then call post_discord(message, channel='alerts') to target "
                "a specific channel.",
    },
    {
        "title": "Set up an inbound bot (optional)",
        "body": "If you also want to send commands to ARIA from Discord:\n"
                "1. Go to discord.com/developers → New Application → Bot → Add Bot\n"
                "2. Copy the token and paste it into 'Discord bot token'\n"
                "3. Enable 'Message Content Intent' under Privileged Gateway Intents\n"
                "4. Invite the bot to your server with the OAuth2 URL Generator\n"
                "5. Add your Discord user ID to the allowlist",
        "note": "Inbound requires discord.py: pip install discord.py",
    },
    {
        "title": "Test it",
        "body": "In the ARIA chat, use the notify_user or post_discord tool, or "
                "wait for a scheduled task to fire. The message should appear in "
                "your Discord channel within seconds.",
    },
]

_DISCORD_LINKS = [
    ("Discord developer portal", "https://discord.com/developers/applications"),
    ("Webhook docs", "https://discord.com/developers/docs/resources/webhook"),
    ("discord.py docs", "https://discordpy.readthedocs.io"),
]


def show_discord_guide(parent):
    _GuideDialog(parent, "How to set up Discord", "💬",
                 _DISCORD_STEPS, _DISCORD_LINKS)
