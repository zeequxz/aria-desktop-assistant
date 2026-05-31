"""
config/i18n.py - Minimal UI translation for ARIA.

Usage:
    from config.i18n import t
    label = t("Settings")          # -> "Inställningar" when ui_language == "sv"

How it works:
  * `t(text)` returns the Swedish translation of an English UI string when the
    UI language is Swedish and a translation exists; otherwise it returns the
    English text unchanged. This means untranslated strings degrade gracefully
    to English instead of breaking.
  * The active language is read once at import (UI language applies on restart),
    matching how the theme works.

To translate more of the UI, add "English": "Svenska" pairs to SV below and wrap
the corresponding UI string in t(...).
"""

from config import settings as cfg

# English -> Swedish. Keys are the exact English UI strings.
SV = {
    # ── Navigation / window ──────────────────────────────────────────────
    "Chat": "Chatt",
    "Tasks": "Uppgifter",
    "Calendar": "Kalender",
    "Memory": "Minne",
    "Plugins": "Tillägg",
    "Settings": "Inställningar",
    "STATUS": "STATUS",
    "● Ready": "● Redo",
    # ── Common buttons / actions ─────────────────────────────────────────
    "Save": "Spara",
    "Cancel": "Avbryt",
    "Delete": "Ta bort",
    "Add": "Lägg till",
    "Close": "Stäng",
    "Save Settings": "Spara inställningar",
    "Save Changes": "Spara ändringar",
    "Send →": "Skicka →",
    "↻ Regenerate": "↻ Generera om",
    "⏹ Stop": "⏹ Stoppa",
    "＋  New chat": "＋  Ny chatt",
    "+ New Task": "+ Ny uppgift",
    "New Task": "Ny uppgift",
    "Edit Task": "Redigera uppgift",
    "Create Task": "Skapa uppgift",
    # ── Chat ─────────────────────────────────────────────────────────────
    "Select an agent": "Välj en agent",
    "Default (Settings)": "Standard (Inställningar)",
    "🔍 Search chats…": "🔍 Sök chattar…",
    "PROJECT": "PROJEKT",
    "No chats yet": "Inga chattar än",
    "No matches": "Inga träffar",
    # ── Settings section headers ─────────────────────────────────────────
    "AI Provider": "AI-leverantör",
    "API Keys": "API-nycklar",
    "Workspace": "Arbetsyta",
    "Language": "Språk",
    "Appearance": "Utseende",
    "Behaviour": "Beteende",
    "Updates": "Uppdateringar",
    "Voice (text-to-speech)": "Röst (text-till-tal)",
    "Messaging (Telegram / Discord)": "Meddelanden (Telegram / Discord)",
    "AI reply language": "AI-svarsspråk",
    "Speak replies aloud": "Läs upp svar högt",
    # ── Misc ─────────────────────────────────────────────────────────────
    "Task name": "Uppgiftsnamn",
    "What should ARIA do?": "Vad ska ARIA göra?",
    "Agent": "Agent",
    "AI model": "AI-modell",
    "Schedule": "Schema",
}

# Resolve the active UI language once (applies on restart, like the theme).
_LANG = cfg.get("ui_language", "en")


def t(text: str) -> str:
    """Translate an English UI string to the active language, or return it
    unchanged if there's no translation / the language is English."""
    if _LANG == "sv":
        return SV.get(text, text)
    return text
