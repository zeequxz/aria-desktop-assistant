# ARIA — Personal AI Assistant

ARIA is a desktop AI assistant for Windows. It can chat, manage files,
browse the web, run scheduled tasks, control your computer, and remember
things about you — all from a clean, easy-to-use interface.

---

## Quick Start

### Step 1 — Install Python
1. Go to **https://python.org/downloads**
2. Click the big yellow "Download Python" button
3. Run the installer — ⚠️ **CHECK "Add Python to PATH"**

### Step 2 — Launch ARIA
Double-click **`run.bat`**

First launch installs everything automatically (~2 min). After that it starts instantly.

---

## Features

### 💬 Chat
- 5 built-in agents: Assistant, Writer, Organizer, Researcher, Computer Use
- Attach files (Word, Excel, CSV, text, code) for context
- Send a screenshot and ask about what's on screen
- Voice input (speak instead of type)
- Clipboard watcher — copy text anywhere and ARIA offers to help
- Conversation history saved automatically
- Keyboard shortcut: **Ctrl+Shift+Space** to open ARIA from anywhere

### ⚡ Tasks
- Create one-time or recurring tasks (hourly, daily, weekly, monthly)
- Tasks run in the background — ARIA notifies you when done
- Each task can use a different specialized agent

### 🧠 Memory
- ARIA remembers facts about you across sessions
- View, add, and delete memories from the Memory tab
- Memory is automatically injected into every conversation

### 🌐 Web & Browser
- Web search (DuckDuckGo — no API key needed)
- Full browser automation via Playwright
- Read articles, fill forms, extract data from websites

### 🖥 Computer Use
- Control mouse and keyboard
- Automate desktop applications
- Combine with browser for end-to-end workflows
- Emergency stop: move mouse to top-left corner

### 🔌 Plugins
- Drop a `.py` file in the `/plugins` folder
- ARIA auto-loads it as new tools on next start
- Example plugin included: reminders

### ⚙ System Tray
- ARIA lives in your taskbar tray when minimized
- Right-click for quick actions
- Desktop notifications for completed tasks

---

## Setting up API Keys

### Claude (Recommended)
1. Go to https://console.anthropic.com → API Keys → Create Key
2. ARIA Settings → paste under "Claude API key" → Save

### ChatGPT
1. Go to https://platform.openai.com/api-keys → Create key
2. ARIA Settings → paste under "OpenAI API key" → Save

### Local AI (Free, no internet)
1. Install Ollama: https://ollama.com
2. Run in terminal: `ollama pull llama3`
3. ARIA Settings → Provider: Local (Ollama)

---

## Building a standalone .exe

To distribute ARIA without requiring Python:

```
pip install pyinstaller
build_exe.bat
```

Output: `dist\ARIA\ARIA.exe` — share the entire `dist\ARIA\` folder.

---

## Adding Plugins

Create `plugins/my_tool.py`:

```python
def my_tool(input: str) -> dict:
    return {"result": f"Processed: {input}"}

TOOLS = {"my_tool": my_tool}

TOOL_SCHEMAS = [{
    "name": "my_tool",
    "description": "Does something useful.",
    "input_schema": {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    },
}]
```

Restart ARIA and the tool is available to all agents.

---

## Privacy

- API keys: stored in `C:\Users\You\AppData\Roaming\ARIA\settings.json`
- Memory: `C:\Users\You\AppData\Roaming\ARIA\memory.json`
- Chat history: `C:\Users\You\AppData\Roaming\ARIA\history\`
- Nothing is sent to any ARIA server. Everything goes directly to your chosen AI provider.
- Use Local (Ollama) for 100% offline, zero data leaving your machine.

---

## File Structure

```
aria/
├── main.py              ← GUI application
├── run.bat              ← Launch script
├── build_exe.bat        ← Build standalone .exe
├── aria.spec            ← PyInstaller config
├── requirements.txt
├── plugins/             ← Drop plugin .py files here
│   └── reminders.py     ← Example plugin
├── config/
│   └── settings.py      ← Settings manager
└── agent/
    ├── orchestrator.py  ← AI agent loop
    ├── file_tools.py    ← File system tools
    ├── computer_tools.py← Mouse/keyboard control
    ├── browser_tools.py ← Web browser (Playwright)
    ├── search_tools.py  ← Web search (DuckDuckGo)
    ├── memory.py        ← Persistent memory
    ├── history.py       ← Chat history
    ├── scheduler.py     ← Task scheduler
    ├── tray.py          ← System tray icon
    ├── voice.py         ← Voice input
    ├── clipboard_watcher.py
    ├── plugins.py       ← Plugin loader
    └── updater.py       ← Update checker
```
