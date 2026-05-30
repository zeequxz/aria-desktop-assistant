"""
config/settings.py - Persistent settings and API key management
Stored in user's AppData folder so it survives app updates.
"""

import json
import os
from pathlib import Path


def get_config_dir() -> Path:
    """Returns platform-appropriate config directory."""
    if os.name == "nt":  # Windows
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    config_dir = base / "ARIA"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


CONFIG_FILE = get_config_dir() / "settings.json"

DEFAULTS = {
    "provider": "claude",
    "claude_model": "claude-opus-4-5",
    "openai_model": "gpt-4o",
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434",
    "claude_api_key": "",
    "openai_api_key": "",
    # "apikey" = use openai_api_key; "oauth" = use Sign in with ChatGPT (Codex).
    "openai_auth_mode": "apikey",
    # ── Messaging channels (Telegram in/out, Discord out) ───────────────────
    "messaging_enabled": False,
    "telegram_bot_token": "",
    # Chat IDs allowed to command the bot. Empty = nobody (the bot will reply
    # with each sender's id so you can add it here).
    "telegram_allowlist": [],
    "discord_webhook_url": "",
    # Named Discord channels (each is a webhook): [{"name": ..., "url": ...}].
    # Lets the agent post to different channels for different topics.
    "discord_channels": [],
    # When False, inbound messages can chat/search/browse but NOT control the
    # mouse/keyboard — a quick way to revoke remote PC control temporarily.
    "messaging_allow_computer_use": True,
    "computer_use_enabled": False,
    "screenshot_interval": 2,
    "theme": "dark",
    "font_size": 13,
    "workspace_folder": str(Path.home() / "Documents"),
    "auto_save_chats": True,
    "show_agent_thinking": True,
    "max_tokens": 4096,
    "auto_check_updates": True,
    "github_repo": "",
    # Projects group related chats (like Codex/Claude). Every chat belongs to
    # one. "general" always exists as the default.
    "projects": [{"id": "general", "name": "General"}],
    "active_project": "general",
    "prompt_library": [
        {
            "name": "Summarize",
            "text": "Summarize the following clearly and concisely:\n\n",
        },
        {
            "name": "Improve writing",
            "text": "Improve and rewrite the following text. Keep my tone:\n\n",
        },
        {
            "name": "Explain simply",
            "text": "Explain the following in simple terms:\n\n",
        },
        {
            "name": "Find bugs",
            "text": "Review this code and list any bugs or issues:\n\n",
        },
    ],
    "tasks": [],
    "agents": [
        {
            "id": "assistant",
            "name": "Assistant",
            "icon": "✦",
            "color": "#6c8fff",
            "system": "You are ARIA, a friendly and capable personal AI assistant. Help the user with any task clearly and efficiently. When working with files or the computer, always confirm before making changes.",
            "builtin": True,
        },
        {
            "id": "writer",
            "name": "Writer",
            "icon": "✍",
            "color": "#ff8c6c",
            "system": "You are an expert writer. Draft emails, reports, articles, and any written content. Always match the user's tone and context. Ask about audience and purpose when unsure.",
            "builtin": True,
        },
        {
            "id": "organizer",
            "name": "Organizer",
            "icon": "◫",
            "color": "#6cffb8",
            "system": "You are a file and task organizer. Help rename, sort, move, and manage files. Suggest folder structures. Always describe exactly what you plan to do before doing it.",
            "builtin": True,
        },
        {
            "id": "researcher",
            "name": "Researcher",
            "icon": "◈",
            "color": "#ffdd6c",
            "system": "You are a research specialist. Find, summarize, and synthesize information clearly. Present findings in an easy-to-read format with key takeaways highlighted.",
            "builtin": True,
        },
        {
            "id": "computer",
            "name": "Computer Use",
            "icon": "⌥",
            "color": "#d06cff",
            "system": "You are a computer automation agent. You can control the mouse, keyboard, and applications to help users complete tasks on their computer. Always explain each step before taking it. Confirm before any destructive actions.",
            "builtin": True,
        },
    ],
}


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # Merge with defaults so new keys always exist
            merged = {**DEFAULTS, **saved}
            return merged
        except Exception:
            pass
    return dict(DEFAULTS)


def save(settings: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Config] Failed to save settings: {e}")


def get(key: str, default=None):
    s = load()
    return s.get(key, default)


def set_key(key: str, value):
    s = load()
    s[key] = value
    save(s)
