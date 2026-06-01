"""
config/i18n.py - Minimal UI translation for ARIA.

Usage:
    from config.i18n import t
    label = t("Settings")          # -> "Inställningar" when ui_language == "sv"

How it works:
  * `t(text)` returns the Swedish translation of an English UI string when the
    UI language is Swedish and a translation exists; otherwise it returns the
    English text unchanged. Untranslated strings (and pure icons/symbols)
    degrade gracefully to the original instead of breaking.
  * The active language is read once at import (UI language applies on restart),
    matching how the theme works.

To translate more UI text, add "English": "Svenska" pairs to SV below. Call
sites are already wrapped in t(...).
"""

from config import settings as cfg

# English -> Swedish. Keys are the exact English UI strings.
SV = {
    # ── Navigation / window / status ─────────────────────────────────────
    "Chat": "Chatt",
    "Tasks": "Uppgifter",
    "Calendar": "Kalender",
    "Memory": "Minne",
    "Plugins": "Tillägg",
    "Settings": "Inställningar",
    "STATUS": "STATUS",
    "● Ready": "● Redo",
    "SYSTEM": "SYSTEM",
    "PROJECT": "PROJEKT",
    # ── Chat ─────────────────────────────────────────────────────────────
    "Select an agent": "Välj en agent",
    "🔍 Search chats…": "🔍 Sök chattar…",
    "Enter to send · Shift+Enter for new line": "Enter för att skicka · Shift+Enter för ny rad",
    "↻ Regenerate": "↻ Generera om",
    "⏹ Stop": "⏹ Stoppa",
    "Send →": "Skicka →",
    "Summarize": "Sammanfatta",
    "Improve": "Förbättra",
    "New chat name:": "Nytt chattnamn:",
    "Rename chat": "Byt namn på chatt",
    "Attach file": "Bifoga fil",
    # ── Tasks ────────────────────────────────────────────────────────────
    "One-time and recurring automated jobs": "Engångs- och återkommande automatiska jobb",
    "+ New Task": "+ Ny uppgift",
    "Search tasks…": "Sök uppgifter…",
    "Close": "Stäng",
    " ⚙ running": " ⚙ körs",
    "View": "Visa",
    "New Task": "Ny uppgift",
    "Edit Task": "Redigera uppgift",
    "Create Task": "Skapa uppgift",
    "Save Changes": "Spara ändringar",
    "Tasks can run on a schedule automatically.": "Uppgifter kan köras automatiskt enligt schema.",
    "Task name": "Uppgiftsnamn",
    "e.g. Morning briefing": "t.ex. Morgongenomgång",
    "What should ARIA do?": "Vad ska ARIA göra?",
    "Schedule": "Schema",
    "Agent": "Agent",
    "AI model": "AI-modell",
    "Date (YYYY-MM-DD)": "Datum (ÅÅÅÅ-MM-DD)",
    "Time (HH:MM)": "Tid (TT:MM)",
    "▶ Run now": "▶ Kör nu",
    # ── Projects dialog ──────────────────────────────────────────────────
    "📁 Projects": "📁 Projekt",
    "Group related chats together.": "Gruppera relaterade chattar.",
    "Group related chats, each with an optional working folder.": "Gruppera relaterade chattar, var och en med en valfri arbetsmapp.",
    "Folder": "Mapp",
    "No folder set": "Ingen mapp angiven",
    "No working folder": "Ingen arbetsmapp",
    "New project name": "Nytt projektnamn",
    "Add": "Lägg till",
    "Rename": "Byt namn",
    "default": "standard",
    "Rename project": "Byt namn på projekt",
    # ── Agents dialog ────────────────────────────────────────────────────
    "🤖 Agents": "🤖 Agenter",
    "Each agent has its own system prompt and style.": "Varje agent har sin egen systemprompt och stil.",
    "Edit": "Redigera",
    "Custom Agent": "Egen agent",
    "Give it a name and a system prompt that defines its behaviour.": "Ge den ett namn och en systemprompt som styr beteendet.",
    "Name": "Namn",
    "e.g. Email Drafter": "t.ex. E-postskrivare",
    "Short description (optional)": "Kort beskrivning (valfritt)",
    "Icon": "Ikon",
    "Colour": "Färg",
    "System prompt": "Systemprompt",
    "Save": "Spara",
    "Delete": "Ta bort",
    "Cancel": "Avbryt",
    # ── Prompt library ───────────────────────────────────────────────────
    "📝 Prompt Library": "📝 Promptbibliotek",
    "Click a prompt to drop it into the chat box.": "Klicka på en prompt för att lägga den i chattrutan.",
    "New prompt": "Ny prompt",
    "+ Add prompt": "+ Lägg till prompt",
    "No prompts yet. Add one below.": "Inga prompter än. Lägg till en nedan.",
    # ── Calendar ─────────────────────────────────────────────────────────
    "See recurring tasks and schedule by date": "Se återkommande uppgifter och schemalägg per datum",
    "Today": "Idag",
    "+ Schedule on this day": "+ Schemalägg denna dag",
    "No tasks scheduled.\nClick below to add one.": "Inga uppgifter schemalagda.\nKlicka nedan för att lägga till en.",
    # ── Memory ───────────────────────────────────────────────────────────
    "Facts ARIA remembers about you": "Fakta ARIA minns om dig",
    "+ Add fact": "+ Lägg till fakta",
    "Clear all": "Rensa allt",
    "No memories yet. ARIA will store facts as you chat.": "Inga minnen än. ARIA sparar fakta medan du chattar.",
    "Key (short label)": "Nyckel (kort etikett)",
    "Value": "Värde",
    # ── Plugins ──────────────────────────────────────────────────────────
    "Drop a .py file in the /plugins folder to add new tools. Restart ARIA to load.": "Lägg en .py-fil i /plugins-mappen för att lägga till verktyg. Starta om ARIA för att läsa in.",
    "📁 Open plugins folder": "📁 Öppna plugins-mappen",
    "No plugins found. Add a .py file to /plugins.": "Inga tillägg hittades. Lägg en .py-fil i /plugins.",
    # ── Settings ─────────────────────────────────────────────────────────
    "Configure AI providers, privacy, and behaviour.": "Konfigurera AI-leverantörer, integritet och beteende.",
    "AI Provider": "AI-leverantör",
    "API Keys": "API-nycklar",
    "🔒 Stored only on your computer in AppData. Never uploaded.": "🔒 Lagras endast på din dator i AppData. Laddas aldrig upp.",
    "Use ChatGPT sign-in instead of API key": "Använd ChatGPT-inloggning istället för API-nyckel",
    "Sign out": "Logga ut",
    "Sign in with ChatGPT": "Logga in med ChatGPT",
    "Browse": "Bläddra",
    "Workspace": "Arbetsyta",
    "Language": "Språk",
    "AI reply language": "AI-svarsspråk",
    "Interface language (applies after restart)": "Gränssnittsspråk (gäller efter omstart)",
    "Appearance": "Utseende",
    "Theme (applies after restart)": "Tema (gäller efter omstart)",
    "Voice (text-to-speech)": "Röst (text-till-tal)",
    "Voice": "Röst",
    "Speak replies aloud": "Läs upp svar högt",
    "Speech engine not found. Install with: pip install pyttsx3": "Talmotor hittades inte. Installera med: pip install pyttsx3",
    "🔊 Test voice": "🔊 Testa röst",
    "Speaking rate (words per minute)": "Talhastighet (ord per minut)",
    "💡 Save these steps as a reusable skill?": "💡 Spara dessa steg som en återanvändbar färdighet?",
    "Save skill": "Spara färdighet",
    "💡 Summarising into a skill…": "💡 Sammanfattar till en färdighet…",
    "💡 Save as skill": "💡 Spara som färdighet",
    "Review and save this reusable workflow.": "Granska och spara detta återanvändbara arbetsflöde.",
    "Description": "Beskrivning",
    "Skill prompt ({input} = your subject)": "Färdighetsprompt ({input} = ditt ämne)",
    "🧩 Skills": "🧩 Färdigheter",
    "Reusable workflows ARIA learned from past tasks.": "Återanvändbara arbetsflöden som ARIA lärt sig av tidigare uppgifter.",
    "No skills yet. Finish a multi-step task and click 'Save skill'.": "Inga färdigheter än. Slutför en flerstegsuppgift och klicka 'Spara färdighet'.",
    "Inbox": "Inkorg",
    "Watchdog": "Vaktpost",
    "Trigger agents when files, folders, or URLs change": "Trigga agenter när filer, mappar eller URL:er ändras",
    "+ New watch": "+ Ny bevakning",
    "No watches yet. Click '+ New watch' to monitor a file, folder, or URL.": "Inga bevakningar än. Klicka '+ Ny bevakning' för att övervaka en fil, mapp eller URL.",
    "Pause": "Pausa",
    "Resume": "Fortsätt",
    "New watch": "Ny bevakning",
    "Edit watch": "Redigera bevakning",
    "Monitor a file, folder, or URL and trigger an agent when it changes.": "Övervaka en fil, mapp eller URL och trigga en agent när den ändras.",
    "Type": "Typ",
    "Target (path or URL)": "Mål (sökväg eller URL)",
    "What should ARIA do when it changes?": "Vad ska ARIA göra när det ändras?",
    "Use {change} for a brief description of what changed.": "Använd {change} för en kort beskrivning av vad som ändrades.",
    "Create watch": "Skapa bevakning",
    "Save watch": "Spara bevakning",
    "No notifications yet.": "Inga aviseringar ännu.",
    "Mark all read": "Markera alla lästa",
    "💬 Chat is getting long — summarise earlier turns to free context?": "💬 Chatten blir lång — sammanfatta tidigare tur för att frigöra kontext?",
    "Summarise": "Sammanfatta",
    "💬 Summarising conversation…": "💬 Sammanfattar konversation…",
    "Heartbeat": "Heartbeat",
    "ARIA proactively checks in on a timer and acts on pending items.": "ARIA checkar in proaktivt på en timer och agerar på väntande objekt.",
    "Enable heartbeat": "Aktivera heartbeat",
    "Heartbeat interval (minutes)": "Heartbeat-intervall (minuter)",
    "Advanced": "Avancerat",
    "Multi-agent orchestration: the active agent can delegate sub-tasks to your other agents and combine their results to tackle complex builds (like Hermes / OpenClaw).": "Multi-agent-orkestrering: den aktiva agenten kan delegera deluppgifter till dina andra agenter och kombinera deras resultat för att lösa komplexa bygg (som Hermes / OpenClaw).",
    "Enable advanced mode (multi-agent orchestration)": "Aktivera avancerat läge (multi-agent-orkestrering)",
    "Behaviour": "Beteende",
    "Enable Computer Use (AI controls mouse & keyboard)": "Aktivera datorstyrning (AI styr mus och tangentbord)",
    "Enable web browser (AI can browse websites)": "Aktivera webbläsare (AI kan surfa på webben)",
    "Show tool activity in chat": "Visa verktygsaktivitet i chatten",
    "Auto-save conversations to history": "Spara konversationer automatiskt i historiken",
    "Watch clipboard and offer to process copied text": "Bevaka urklipp och erbjud att bearbeta kopierad text",
    "Minimize to system tray instead of closing": "Minimera till aktivitetsfältet istället för att stänga",
    "Updates": "Uppdateringar",
    "Check for updates on startup": "Sök efter uppdateringar vid start",
    "GitHub repo (owner/name)": "GitHub-repo (ägare/namn)",
    "Check now": "Sök nu",
    "Enable messaging channels": "Aktivera meddelandekanaler",
    "Test token": "Testa token",
    "Send test message": "Skicka testmeddelande",
    "Save Settings": "Spara inställningar",
    "●  You have unsaved changes": "●  Du har osparade ändringar",
    "Select workspace folder": "Välj arbetsmapp",
    "Update check unavailable.": "Uppdateringskontroll otillgänglig.",
    "Checking…": "Söker…",
    "Opening browser…": "Öppnar webbläsare…",
    "✓ Signed in": "✓ Inloggad",
    "Not signed in": "Inte inloggad",
    # ── Update dialog ────────────────────────────────────────────────────
    "🚀 Update available": "🚀 Uppdatering tillgänglig",
    "Release notes": "Versionsinformation",
    "Download & Install": "Ladda ner & installera",
    "Run the packaged app to auto-install. From source, git pull.": "Kör den paketerade appen för autoinstallation. Från källkod: git pull.",
    "Open release page": "Öppna utgåvesidan",
    "Later": "Senare",
    "Downloading…": "Laddar ner…",
    "Update ready. Restarting ARIA…": "Uppdatering klar. Startar om ARIA…",
    "Retry": "Försök igen",
}

# Resolve the active UI language once (applies on restart, like the theme).
_LANG = cfg.get("ui_language", "en")


def t(text: str) -> str:
    """Translate an English UI string to the active language, or return it
    unchanged if there's no translation / the language is English."""
    if _LANG == "sv":
        return SV.get(text, text)
    return text
