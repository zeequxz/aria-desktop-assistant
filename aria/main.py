"""
main.py - ARIA Desktop AI Assistant
Full-featured GUI with system tray, history, memory panel,
clipboard watcher, voice input, health monitor, and plugin browser.
"""

import threading
import json
import uuid
import os
import sys
import time
import platform
import calendar as _calendar
from datetime import datetime, date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

try:
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
except ImportError:
    print("Install CustomTkinter: pip install customtkinter")
    sys.exit(1)

from config import settings as cfg
from agent.orchestrator import run_agent_in_thread, run_agent_sync
from agent.scheduler import TaskScheduler
from agent.messaging import MessagingService
from agent.tray import TrayManager, send_notification
from agent.clipboard_watcher import ClipboardWatcher
from agent.voice import VoiceRecorder
from agent import memory as mem
from agent import history as hist
from agent.plugins import get_plugin_info
from agent import updater

try:
    import psutil
    PSUTIL = True
except ImportError:
    PSUTIL = False

# ── Colours ────────────────────────────────────────────────────────────────
# Two palettes; the active one is chosen at startup from the "theme" setting.
# (Switching applies on restart, since widgets read these as module constants.)
_THEMES = {
    "dark": {
        "BG": "#0d0d14", "SURFACE": "#13131e", "SURF2": "#1a1a28", "SURF3": "#20202f",
        "BORDER": "#252535", "ACCENT": "#6c8fff", "SUCCESS": "#5dba7d",
        "WARNING": "#e8b84b", "DANGER": "#e05c5c", "TEXT": "#e4e4f0",
        "MUTED": "#6a6a85", "PURPLE": "#9b72ff",
    },
    "light": {
        "BG": "#f4f5fb", "SURFACE": "#ffffff", "SURF2": "#eef0f7", "SURF3": "#e3e6f0",
        "BORDER": "#d4d8e6", "ACCENT": "#4665e0", "SUCCESS": "#2f9e5e",
        "WARNING": "#b8860b", "DANGER": "#c84444", "TEXT": "#1a1c28",
        "MUTED": "#7a7f95", "PURPLE": "#7d4ee0",
    },
}

_active_theme = cfg.get("theme", "dark")
if _active_theme not in _THEMES:
    _active_theme = "dark"
ctk.set_appearance_mode("light" if _active_theme == "light" else "dark")
_P = _THEMES[_active_theme]

BG      = _P["BG"]
SURFACE = _P["SURFACE"]
SURF2   = _P["SURF2"]
SURF3   = _P["SURF3"]
BORDER  = _P["BORDER"]
ACCENT  = _P["ACCENT"]
SUCCESS = _P["SUCCESS"]
WARNING = _P["WARNING"]
DANGER  = _P["DANGER"]
TEXT    = _P["TEXT"]
MUTED   = _P["MUTED"]
PURPLE  = _P["PURPLE"]

AGENT_COLORS = {
    "assistant": "#6c8fff",
    "writer":    "#ff8c6c",
    "organizer": "#5dba7d",
    "researcher":"#e8b84b",
    "computer":  "#9b72ff",
}

# Palette offered when creating a custom agent.
AGENT_PALETTE = ["#6c8fff", "#ff8c6c", "#5dba7d", "#e8b84b", "#9b72ff",
                 "#e05c5c", "#4dc9c9", "#d06cff", "#ff6cae", "#7da0ff"]


def agent_color(agent):
    """Resolve an agent's accent colour: built-in map first, then the agent's
    own 'color' field, then the default accent."""
    if not agent:
        return ACCENT
    return AGENT_COLORS.get(agent.get("id"), agent.get("color", ACCENT))


def tint(hex_color, alpha):
    """Blend hex_color over the app background at the given alpha (0-255) and
    return a solid #RRGGBB. Tkinter has no alpha channel, so 8-digit #RRGGBBAA
    values are invalid; this fakes the same translucent look with a solid color.
    `alpha` may be an int (0-255) or a 2-char hex string like "33"."""
    if isinstance(alpha, str):
        alpha = int(alpha, 16)
    fg = hex_color.lstrip("#")
    bg = BG.lstrip("#")
    fr, fg_, fb = int(fg[0:2], 16), int(fg[2:4], 16), int(fg[4:6], 16)
    br, bg_, bb = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
    a = alpha / 255
    r = round(fr * a + br * (1 - a))
    g = round(fg_ * a + bg_ * (1 - a))
    b = round(fb * a + bb * (1 - a))
    return f"#{r:02x}{g:02x}{b:02x}"

F_BODY  = ("Segoe UI", 13)
F_SMALL = ("Segoe UI", 11)
F_BOLD  = ("Segoe UI Semibold", 13)
F_HEAD  = ("Segoe UI Semibold", 16)
F_TITLE = ("Segoe UI Bold", 22)
F_MONO  = ("Cascadia Code", 12)


def on_main(root, fn):
    try:
        root.after(0, fn)
    except Exception:
        pass


class Tooltip:
    """A small hover label that appears after a short delay over any widget.
    Usage: Tooltip(widget, "What this button does")."""

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        try:
            self._tip.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(self._tip, text=self.text, justify="left",
                 background="#20202f", foreground="#e4e4f0",
                 relief="solid", borderwidth=1,
                 font=("Segoe UI", 9), padx=8, pady=4).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


def task_occurs_on(task, d):
    """Return True if `task` is scheduled to run on date `d` (a datetime.date).
    Used by the calendar to mark days. Recurring tasks repeat by their rule;
    'once' tasks match only their exact run_date."""
    interval = task.get("interval", "none")
    if interval in ("hourly", "daily"):
        return True
    anchor = task.get("run_date")
    if interval == "once":
        return anchor == d.strftime("%Y-%m-%d")
    if not anchor:
        return False
    try:
        ad = datetime.strptime(anchor, "%Y-%m-%d").date()
    except Exception:
        return False
    if d < ad:
        return False  # don't show recurrences before the task was anchored
    if interval == "weekly":
        return ad.weekday() == d.weekday()
    if interval == "monthly":
        return ad.day == d.day
    return False


# ══════════════════════════════════════════════════════════════════════════════
# CHAT TAB
# ══════════════════════════════════════════════════════════════════════════════

class ChatTab(ctk.CTkFrame):
    def __init__(self, master, root, on_notify, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.root = root
        self.on_notify = on_notify
        self.history_msgs = []
        self.active_agent = None
        self.orch = None
        self._streaming = False
        self._pending_file = None
        self._pending_screenshot = False
        self._voice = None
        # Projects organize chats (like Codex/Claude). Track which project is
        # active and which saved chat (if any) is currently open.
        self.active_project = cfg.get("active_project", "general")
        self.current_chat_file = None
        self._build()
        self._load_agents()

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Left sidebar: Projects + chats ─────────────────────────────────
        left = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, width=210)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(4, weight=1)

        # Project selector row
        proj_hdr = ctk.CTkFrame(left, fg_color="transparent")
        proj_hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(12, 2))
        proj_hdr.columnconfigure(0, weight=1)
        ctk.CTkLabel(proj_hdr, text="PROJECT", font=("Segoe UI Semibold", 10),
                     text_color=MUTED).grid(row=0, column=0, sticky="w")
        mgr_btn = ctk.CTkButton(proj_hdr, text="⚙", width=24, height=24, fg_color="transparent",
                                hover_color=SURF2, text_color=MUTED, font=F_SMALL,
                                command=self._manage_projects)
        mgr_btn.grid(row=0, column=1)
        Tooltip(mgr_btn, "Manage projects (add / rename / delete)")

        self.project_var = ctk.StringVar()
        self.project_menu = ctk.CTkOptionMenu(
            left, variable=self.project_var, font=F_BODY, height=34,
            fg_color=SURF2, button_color=SURF3, button_hover_color=BORDER,
            dropdown_fg_color=SURF2, command=self._on_project_change)
        self.project_menu.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        new_chat = ctk.CTkButton(left, text="＋  New chat", anchor="w", height=36,
                                 fg_color=tint(ACCENT, 0x22), hover_color=tint(ACCENT, 0x44),
                                 text_color=ACCENT, font=F_BOLD, corner_radius=8,
                                 command=self._new_chat)
        new_chat.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        Tooltip(new_chat, "Start a new chat in this project")

        self.history_search = ctk.StringVar()
        self.history_search.trace_add("write", lambda *a: self._refresh_history())
        ctk.CTkEntry(left, textvariable=self.history_search, placeholder_text="🔍 Search chats…",
                     height=30, font=F_SMALL).grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
        self.history_frame = ctk.CTkScrollableFrame(left, fg_color="transparent", label_text="")
        self.history_frame.grid(row=4, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self._refresh_projects()
        self._refresh_history()

        # ── Right: chat ────────────────────────────────────────────────────
        right = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        # Header bar
        hdr = ctk.CTkFrame(right, fg_color=SURF2, corner_radius=10, height=52)
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        hdr.grid_propagate(False)
        hdr.columnconfigure(1, weight=1)

        self.agent_icon_lbl = ctk.CTkLabel(hdr, text="✦", font=("Segoe UI", 22), text_color=ACCENT, width=40)
        self.agent_icon_lbl.grid(row=0, column=0, padx=(12, 0), pady=8)

        # Agent selector as a dropdown menu (cleaner than the old sidebar list).
        self.agent_var = ctk.StringVar(value="Select an agent")
        self.agent_menu = ctk.CTkOptionMenu(
            hdr, variable=self.agent_var, font=F_BOLD, height=34, width=180,
            fg_color=SURF2, button_color=SURF3, button_hover_color=BORDER,
            dropdown_fg_color=SURF2, command=self._on_agent_pick)
        self.agent_menu.grid(row=0, column=1, sticky="w", padx=8)
        Tooltip(self.agent_menu, "Choose which agent answers in this chat")
        self.agent_sub_lbl = ctk.CTkLabel(hdr, text="", font=F_SMALL, text_color=MUTED)
        self.agent_sub_lbl.grid(row=0, column=1, sticky="w", padx=(196, 0))

        hdr_btns = ctk.CTkFrame(hdr, fg_color="transparent")
        hdr_btns.grid(row=0, column=2, padx=10)
        for txt, cmd, tip in [
            ("📷", self._attach_screenshot, "Attach a screenshot to your next message"),
            ("📁", self._attach_file, "Attach a file (text, code, Word, Excel…)"),
            ("🎤", self._toggle_voice, "Voice input — speak instead of typing"),
            ("📝", self._open_prompt_library, "Prompt library — insert a saved prompt"),
            ("📋", self._copy_chat, "Copy this conversation to the clipboard"),
            ("⬇", self._export_chat, "Export this conversation to a file"),
            ("✎", self._edit_agents, "Manage agents (add / edit / delete)"),
        ]:
            b = ctk.CTkButton(hdr_btns, text=txt, width=34, height=30,
                              fg_color=SURF2, border_color=BORDER, border_width=1,
                              hover_color=SURF3, text_color=TEXT, font=F_SMALL,
                              command=cmd)
            b.pack(side="left", padx=2)
            Tooltip(b, tip)

        # Messages
        self.msg_box = ctk.CTkTextbox(right, font=F_BODY, wrap="word",
                                       fg_color=SURFACE, text_color=TEXT,
                                       border_width=0, state="disabled")
        self.msg_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)

        # Tool activity strip
        self.tool_bar = ctk.CTkFrame(right, fg_color=SURF2, corner_radius=8, height=26)
        self.tool_bar.grid(row=2, column=0, sticky="ew", padx=12)
        self.tool_bar.grid_remove()
        self.tool_lbl = ctk.CTkLabel(self.tool_bar, text="", font=F_SMALL, text_color=ACCENT)
        self.tool_lbl.pack(side="left", padx=10)

        # Voice indicator
        self.voice_bar = ctk.CTkFrame(right, fg_color=tint(DANGER, 0x33), corner_radius=8, height=26)
        self.voice_bar.grid(row=3, column=0, sticky="ew", padx=12)
        self.voice_bar.grid_remove()
        ctk.CTkLabel(self.voice_bar, text="🎤 Listening… speak now, then click 🎤 again to stop",
                     font=F_SMALL, text_color=DANGER).pack(side="left", padx=10)

        # Input
        inp_frame = ctk.CTkFrame(right, fg_color=SURF2, corner_radius=12)
        inp_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 12))
        inp_frame.columnconfigure(0, weight=1)
        self.input_box = ctk.CTkTextbox(inp_frame, height=76, font=F_BODY, wrap="word",
                                         fg_color="transparent", text_color=TEXT, border_width=0)
        self.input_box.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        self.input_box.bind("<Return>", self._on_enter)

        bot = ctk.CTkFrame(inp_frame, fg_color="transparent")
        bot.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 8))
        ctk.CTkLabel(bot, text="Enter to send · Shift+Enter for new line",
                     font=F_SMALL, text_color=MUTED).pack(side="left")
        self.regen_btn = ctk.CTkButton(bot, text="↻ Regenerate", width=110, height=28,
                                       fg_color=SURF3, hover_color=BORDER,
                                       text_color=MUTED, font=F_SMALL, command=self._regenerate)
        self.regen_btn.pack(side="left", padx=8)
        self.stop_btn = ctk.CTkButton(bot, text="⏹ Stop", width=80, height=28,
                                       fg_color=tint(DANGER, 0x33), hover_color=tint(DANGER, 0x55),
                                       text_color=DANGER, border_color=DANGER, border_width=1,
                                       font=F_SMALL, command=self._stop_agent)
        self.send_btn = ctk.CTkButton(bot, text="Send →", width=90, height=28,
                                       fg_color=ACCENT, hover_color="#8aa5ff",
                                       text_color="white", font=F_BOLD, command=self._send)
        self.send_btn.pack(side="right")

        # Clipboard offer banner (hidden by default)
        self.clip_bar = ctk.CTkFrame(right, fg_color=tint(PURPLE, 0x22), corner_radius=8, height=36)
        self.clip_bar.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 4))
        self.clip_bar.grid_remove()
        self.clip_lbl = ctk.CTkLabel(self.clip_bar, text="", font=F_SMALL, text_color=PURPLE)
        self.clip_lbl.pack(side="left", padx=10)
        ctk.CTkButton(self.clip_bar, text="Summarize", width=90, height=24,
                      fg_color=tint(PURPLE, 0x44), hover_color=tint(PURPLE, 0x66),
                      text_color=PURPLE, font=F_SMALL,
                      command=lambda: self._use_clipboard("Summarize this text")).pack(side="right", padx=4)
        ctk.CTkButton(self.clip_bar, text="Improve", width=80, height=24,
                      fg_color=SURF2, hover_color=SURF3,
                      text_color=MUTED, font=F_SMALL,
                      command=lambda: self._use_clipboard("Improve and rewrite this text")).pack(side="right", padx=2)
        ctk.CTkButton(self.clip_bar, text="✕", width=24, height=24,
                      fg_color="transparent", hover_color=SURF2,
                      text_color=MUTED, font=F_SMALL,
                      command=self.clip_bar.grid_remove).pack(side="right")
        self._clipboard_text = ""

    # ── Agent management ───────────────────────────────────────────────────

    def _load_agents(self):
        """Populate the header agent dropdown from settings."""
        agents = cfg.get("agents", [])
        self._agents_by_name = {a["name"]: a for a in agents}
        names = list(self._agents_by_name.keys()) or ["(no agents)"]
        self.agent_menu.configure(values=names)
        # Keep the current selection if it still exists, else pick the first.
        if self.active_agent and self.active_agent["name"] in self._agents_by_name:
            self._select_agent(self._agents_by_name[self.active_agent["name"]])
        elif agents:
            self._select_agent(agents[0])

    def _on_agent_pick(self, name):
        agent = self._agents_by_name.get(name)
        if agent:
            self._select_agent(agent)

    def _edit_agents(self):
        """Open the agent manager (add / edit / delete)."""
        AgentManagerDialog(self.root, on_changed=self.reload_agents)

    def _select_agent(self, agent):
        self.active_agent = agent
        color = agent_color(agent)
        self.agent_icon_lbl.configure(text=agent["icon"], text_color=color)
        self.agent_var.set(agent["name"])
        self.agent_sub_lbl.configure(text=agent.get("desc", ""))

    def reload_agents(self):
        self._load_agents()

    # ── Projects ─────────────────────────────────────────────────────────────

    def _refresh_projects(self):
        projects = cfg.get("projects", [{"id": "general", "name": "General"}])
        self._projects_by_name = {p["name"]: p for p in projects}
        names = list(self._projects_by_name.keys())
        self.project_menu.configure(values=names)
        active = next((p for p in projects if p["id"] == self.active_project), projects[0])
        self.active_project = active["id"]
        self.project_var.set(active["name"])

    def _on_project_change(self, name):
        proj = self._projects_by_name.get(name)
        if not proj:
            return
        self.active_project = proj["id"]
        cfg.set_key("active_project", self.active_project)
        # Switching project starts a fresh chat view scoped to it.
        self._new_chat(save_current=True)
        self._refresh_history()

    def _manage_projects(self):
        ProjectManagerDialog(self.root, on_changed=self._on_projects_changed)

    def _on_projects_changed(self):
        # The active project may have been deleted; fall back to general.
        projects = cfg.get("projects", [])
        if not any(p["id"] == self.active_project for p in projects):
            self.active_project = "general"
            cfg.set_key("active_project", "general")
        self._refresh_projects()
        self._refresh_history()

    # ── History (chats) ──────────────────────────────────────────────────────

    def _refresh_history(self):
        for w in self.history_frame.winfo_children():
            w.destroy()
        query = self.history_search.get().strip() if hasattr(self, "history_search") else ""
        if query:
            convos = hist.search_conversations(query, limit=40, project_id=self.active_project)
        else:
            convos = hist.list_conversations(limit=40, project_id=self.active_project)
        if not convos:
            msg = "No matches" if query else "No chats yet"
            ctk.CTkLabel(self.history_frame, text=msg, font=F_SMALL,
                         text_color=MUTED).pack(pady=8)
            return
        for c in convos:
            is_current = c["filename"] == self.current_chat_file
            frame = ctk.CTkFrame(self.history_frame,
                                 fg_color=SURF2 if is_current else "transparent",
                                 corner_radius=6)
            frame.pack(fill="x", pady=1)
            frame.columnconfigure(0, weight=1)
            open_btn = ctk.CTkButton(
                frame, text=c["title"][:24], anchor="w", height=32,
                fg_color="transparent", hover_color=SURF2,
                text_color=TEXT if is_current else MUTED, font=F_SMALL, corner_radius=6,
                command=lambda fn=c["filename"]: self._load_history(fn),
            )
            open_btn.grid(row=0, column=0, sticky="ew")
            Tooltip(open_btn, c["title"])
            ren = ctk.CTkButton(frame, text="✎", width=22, height=32, fg_color="transparent",
                                hover_color=SURF2, text_color=MUTED, font=F_SMALL,
                                command=lambda fn=c["filename"], t=c["title"]: self._rename_chat(fn, t))
            ren.grid(row=0, column=1)
            Tooltip(ren, "Rename this chat")
            dele = ctk.CTkButton(frame, text="✕", width=22, height=32, fg_color="transparent",
                                 hover_color=tint(DANGER, 0x33), text_color=MUTED, font=F_SMALL,
                                 command=lambda fn=c["filename"]: self._delete_chat(fn))
            dele.grid(row=0, column=2)
            Tooltip(dele, "Delete this chat")
            if c.get("snippet"):
                ctk.CTkLabel(frame, text=c["snippet"], font=("Segoe UI", 9),
                             text_color=MUTED, anchor="w", justify="left",
                             wraplength=170).grid(row=1, column=0, columnspan=3, sticky="w", padx=10)

    def _rename_chat(self, filename, current_title):
        dlg = ctk.CTkInputDialog(text="New chat name:", title="Rename chat")
        new = dlg.get_input()
        if new and new.strip():
            hist.rename_conversation(filename, new.strip())
            self._refresh_history()

    def _delete_chat(self, filename):
        if not messagebox.askyesno("Delete chat", "Delete this chat permanently?"):
            return
        hist.delete_conversation(filename)
        if filename == self.current_chat_file:
            self._new_chat(save_current=False)
        self._refresh_history()

    def _load_history(self, filename: str):
        # Save the chat we're leaving (if it has unsaved content) first.
        self._persist_current_chat()
        data = hist.load_conversation(filename)
        if not data:
            return
        self.history_msgs = data.get("messages", [])
        self.current_chat_file = filename
        agent_id = data.get("agent_id", "assistant")
        agents = cfg.get("agents", [])
        agent = next((a for a in agents if a["id"] == agent_id), agents[0] if agents else None)
        if agent:
            self._select_agent(agent)
        # Replay messages in UI
        self.msg_box.configure(state="normal")
        self.msg_box.delete("1.0", "end")
        self.msg_box.configure(state="disabled")
        for m in self.history_msgs:
            if m["role"] == "user":
                self._append_msg("user", m["content"] if isinstance(m["content"], str) else str(m["content"]))
            elif m["role"] == "assistant":
                self._append_msg("assistant", m["content"] if isinstance(m["content"], str) else str(m["content"]))
        self._refresh_history()

    def _persist_current_chat(self):
        """Save the open conversation back to its file (or a new one), tagged
        with the active project. Returns the filename, or None if empty."""
        if not self.history_msgs:
            return None
        fn = hist.save_conversation(
            self.history_msgs,
            self.active_agent["id"] if self.active_agent else "assistant",
            project_id=self.active_project,
            filename=self.current_chat_file,
        )
        self.current_chat_file = fn
        return fn

    def _new_chat(self, save_current=True):
        """Start a fresh chat in the current project, saving the open one."""
        if save_current:
            self._persist_current_chat()
        self.history_msgs = []
        self.current_chat_file = None
        self.msg_box.configure(state="normal")
        self.msg_box.delete("1.0", "end")
        self.msg_box.configure(state="disabled")
        self._refresh_history()

    def _save_and_clear(self):
        # Kept for the tray "new chat" action and the ↩ flow.
        self._new_chat(save_current=True)

    # ── Export ─────────────────────────────────────────────────────────────

    def _conversation_markdown(self):
        """Render the current conversation as Markdown text."""
        agent_name = (self.active_agent or {}).get("name", "ARIA")
        lines = [f"# ARIA conversation — {agent_name}",
                 f"_Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
        for msg in self.history_msgs:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            parts.append(f"_[tool: {block.get('name', '')}]_")
                    else:
                        parts.append(str(block))
                content = "\n".join(parts)
            who = "You" if role == "user" else agent_name
            lines.append(f"**{who}:**\n\n{content}\n")
        return "\n".join(lines)

    def _export_chat(self):
        """Save the current conversation to a Markdown file."""
        if not self.history_msgs:
            messagebox.showinfo("Nothing to export", "Start a conversation first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")],
            initialfile=f"aria-chat-{datetime.now().strftime('%Y%m%d-%H%M')}.md")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._conversation_markdown())
            self.on_notify("Exported", "Conversation saved.")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _copy_chat(self):
        """Copy the whole conversation to the clipboard as Markdown."""
        if not self.history_msgs:
            messagebox.showinfo("Nothing to copy", "Start a conversation first.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._conversation_markdown())
            self.on_notify("Copied", "Conversation copied to clipboard.")
        except Exception as e:
            messagebox.showerror("Copy failed", str(e))

    # ── Clipboard ──────────────────────────────────────────────────────────

    def on_clipboard(self, text: str):
        """Called by ClipboardWatcher when something meaningful is copied."""
        self._clipboard_text = text
        preview = text[:55] + "…" if len(text) > 55 else text
        on_main(self.root, lambda: self._show_clipboard_offer(preview))

    def _show_clipboard_offer(self, preview: str):
        self.clip_lbl.configure(text=f"📋 {preview}")
        self.clip_bar.grid()

    def _use_clipboard(self, action: str):
        if not self._clipboard_text:
            return
        self.clip_bar.grid_remove()
        self.input_box.delete("1.0", "end")
        self.input_box.insert("end", f"{action}:\n\n{self._clipboard_text}")
        self._send()

    # ── Voice ──────────────────────────────────────────────────────────────

    def _toggle_voice(self):
        if self._voice and self._voice.is_recording:
            self._voice.stop_recording()
            self.voice_bar.grid_remove()
            return
        available, mode = VoiceRecorder.is_available()
        if not available:
            messagebox.showinfo("Voice Input", "Install voice support:\npip install SpeechRecognition pyaudio\n\nOr for local/offline:\npip install faster-whisper sounddevice")
            return
        self._voice = VoiceRecorder(
            on_result=lambda t: on_main(self.root, lambda t=t: self._on_voice_result(t)),
            on_error=lambda e: on_main(self.root, lambda e=e: self._on_voice_error(e)),
        )
        self._voice.start_recording(mode)
        self.voice_bar.grid()

    def _on_voice_result(self, text: str):
        self.voice_bar.grid_remove()
        self.input_box.delete("1.0", "end")
        self.input_box.insert("end", text)
        self._send()

    def _on_voice_error(self, err: str):
        self.voice_bar.grid_remove()
        self._append_msg("error", f"Voice error: {err}")

    # ── Send & agent loop ──────────────────────────────────────────────────

    def _on_enter(self, event):
        if event.state & 1:
            return
        self._send()
        return "break"

    def _send(self):
        text = self.input_box.get("1.0", "end").strip()
        if not text or not self.active_agent:
            return

        # Slash commands are handled locally and never sent to the model.
        if text.startswith("/"):
            self.input_box.delete("1.0", "end")
            self._handle_slash(text)
            return

        self.input_box.delete("1.0", "end")

        content = text
        if self._pending_file:
            content = f"{text}\n\n[Attached file:]\n{self._pending_file}"
            self._pending_file = None

        self.history_msgs.append({"role": "user", "content": content})
        self._append_msg("user", text)
        self._run_agent()

    # ── Slash commands ───────────────────────────────────────────────────────

    SLASH_HELP = (
        "/new        Save & start a new chat\n"
        "/clear      Same as /new\n"
        "/export     Export conversation to a file\n"
        "/copy       Copy conversation to clipboard\n"
        "/regen      Regenerate the last response\n"
        "/agent NAME Switch to an agent by name\n"
        "/prompts    Open the prompt library\n"
        "/help       Show this list"
    )

    def _handle_slash(self, text):
        parts = text[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("new", "clear"):
            self._save_and_clear()
        elif cmd == "export":
            self._export_chat()
        elif cmd == "copy":
            self._copy_chat()
        elif cmd in ("regen", "regenerate"):
            self._regenerate()
        elif cmd in ("prompts", "prompt"):
            self._open_prompt_library()
        elif cmd == "agent":
            agents = cfg.get("agents", [])
            match = next((a for a in agents if a["name"].lower() == arg.lower()), None) if arg else None
            if match:
                self._select_agent(match)
                self._append_msg("tool", f"Switched to {match['name']}.")
            else:
                names = ", ".join(a["name"] for a in agents)
                self._append_msg("tool", f"Unknown agent. Available: {names}")
        elif cmd == "help":
            self._append_msg("tool", "Slash commands:\n" + self.SLASH_HELP)
        else:
            self._append_msg("tool", f"Unknown command '/{cmd}'. Type /help for a list.")

    # ── Prompt library ───────────────────────────────────────────────────────

    def _open_prompt_library(self):
        PromptLibraryDialog(self.root, on_pick=self._insert_prompt)

    def _insert_prompt(self, prompt_text):
        """Put a library prompt into the input box, ready to edit and send."""
        self.input_box.delete("1.0", "end")
        self.input_box.insert("end", prompt_text)
        self.input_box.focus_set()

    def _regenerate(self):
        """Re-run the agent on the last user turn, discarding the last reply.
        Useful when a response was cut off or you want a different answer."""
        if self._streaming or not self.active_agent:
            return
        # Drop a trailing assistant message so the last turn is the user's.
        if self.history_msgs and self.history_msgs[-1].get("role") == "assistant":
            self.history_msgs.pop()
        if not any(m.get("role") == "user" for m in self.history_msgs):
            messagebox.showinfo("Nothing to regenerate", "Send a message first.")
            return
        # Redraw the transcript without the removed reply.
        self.msg_box.configure(state="normal")
        self.msg_box.delete("1.0", "end")
        self.msg_box.configure(state="disabled")
        for m in self.history_msgs:
            txt = m["content"] if isinstance(m["content"], str) else str(m["content"])
            self._append_msg(m["role"], txt)
        self._run_agent()

    def _run_agent(self):
        """Start the agent on the current history. Shared by send/regenerate."""
        self._set_busy(True)
        agent = self.active_agent
        use_computer = agent["id"] == "computer" or cfg.get("computer_use_enabled", False)
        use_browser = cfg.get("browser_enabled", True)

        self.orch = run_agent_in_thread(
            messages=list(self.history_msgs),
            system_prompt=agent["system"],
            on_token=lambda t: on_main(self.root, lambda t=t: self._on_token(t)),
            on_tool_call=lambda n, i: on_main(self.root, lambda n=n, i=i: self._on_tool_call(n, i)),
            on_tool_result=lambda n, r: on_main(self.root, lambda n=n, r=r: self._on_tool_result(n, r)),
            on_done=lambda t: on_main(self.root, lambda t=t: self._on_done(t)),
            on_error=lambda e: on_main(self.root, lambda e=e: self._on_error(e)),
            use_computer_tools=use_computer,
            use_browser_tools=use_browser,
            include_screenshot=self._pending_screenshot,
        )
        self._pending_screenshot = False

    def _on_token(self, token):
        if not self._streaming:
            self._streaming = True
            agent_name = self.active_agent["name"] if self.active_agent else "ARIA"
            color = agent_color(self.active_agent)
            self.msg_box.configure(state="normal")
            self.msg_box.insert("end", f"\n\n{agent_name}\n", "ai_label")
            self.msg_box.tag_config("ai_label", foreground=color)
            self.msg_box.configure(state="disabled")
        self.msg_box.configure(state="normal")
        self.msg_box.insert("end", token)
        self.msg_box.configure(state="disabled")
        self.msg_box.see("end")

    def _on_tool_call(self, name, inp):
        friendly = name.replace("_", " ").title()
        self.tool_bar.grid()
        self.tool_lbl.configure(text=f"⚙  {friendly}…")

    def _on_tool_result(self, name, result):
        if cfg.get("show_agent_thinking", True):
            short = str(result)[:110] + ("…" if len(str(result)) > 110 else "")
            self._append_msg("tool", f"[{name.replace('_',' ')}] {short}")

    def _on_done(self, text):
        if self._streaming:
            self.history_msgs.append({"role": "assistant", "content": text})
            self._streaming = False
        self.tool_bar.grid_remove()
        self._set_busy(False)
        # Auto-save into the current chat file (tagged with the active project)
        # so the sidebar updates and reopening continues the same chat.
        if cfg.get("auto_save_chats", True) and len(self.history_msgs) >= 2:
            self._persist_current_chat()
            self._refresh_history()
        self.on_notify("ARIA", "Response ready")

    def _on_error(self, err):
        self._streaming = False
        self._append_msg("error", err)
        self.tool_bar.grid_remove()
        self._set_busy(False)

    def _set_busy(self, busy):
        if busy:
            self.send_btn.pack_forget()
            self.stop_btn.pack(side="right")
        else:
            self.stop_btn.pack_forget()
            self.send_btn.pack(side="right")
        # Can't regenerate while a response is streaming.
        self.regen_btn.configure(state="disabled" if busy else "normal")

    def _stop_agent(self):
        if self.orch:
            self.orch.stop()
        self._streaming = False
        self._set_busy(False)
        self.tool_bar.grid_remove()

    def _append_msg(self, role, text):
        if not isinstance(text, str):
            text = str(text)
        self.msg_box.configure(state="normal")
        if role == "user":
            self.msg_box.insert("end", "\n\nYou\n", "user_label")
            self.msg_box.insert("end", text + "\n")
            self.msg_box.tag_config("user_label", foreground=ACCENT)
        elif role == "assistant":
            agent_name = self.active_agent["name"] if self.active_agent else "ARIA"
            color = agent_color(self.active_agent)
            self.msg_box.insert("end", f"\n\n{agent_name}\n", "ai_lbl2")
            self.msg_box.insert("end", text + "\n")
            self.msg_box.tag_config("ai_lbl2", foreground=color)
        elif role == "tool":
            self.msg_box.insert("end", f"\n  ⚙ {text}\n", "tool_t")
            self.msg_box.tag_config("tool_t", foreground=MUTED)
        elif role == "error":
            self.msg_box.insert("end", f"\n  ⚠ {text}\n", "err_t")
            self.msg_box.tag_config("err_t", foreground=DANGER)
        self.msg_box.configure(state="disabled")
        self.msg_box.see("end")

    def _attach_file(self):
        path = filedialog.askopenfilename(
            title="Attach file",
            filetypes=[("Supported", "*.txt *.md *.py *.js *.json *.csv *.docx *.xlsx"), ("All", "*.*")]
        )
        if not path:
            return
        from agent.file_tools import read_file
        r = read_file(path)
        if "error" in r:
            messagebox.showerror("Error", r["error"])
            return
        self._pending_file = r.get("content", "")
        self._append_msg("tool", f"📎 Attached: {Path(path).name}")

    def _attach_screenshot(self):
        self._pending_screenshot = True
        self._append_msg("tool", "📷 Screenshot will be sent with your next message.")


# ══════════════════════════════════════════════════════════════════════════════
# TASKS TAB
# ══════════════════════════════════════════════════════════════════════════════

class TasksTab(ctk.CTkFrame):
    def __init__(self, master, root, scheduler, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.root = root
        self.scheduler = scheduler
        self._running = set()
        self._build()
        self._refresh()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, height=58)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.grid_propagate(False)
        ctk.CTkLabel(top, text="Tasks", font=F_TITLE, text_color=TEXT).pack(side="left", padx=18)
        ctk.CTkLabel(top, text="One-time and recurring automated jobs",
                     font=F_SMALL, text_color=MUTED).pack(side="left")
        ctk.CTkButton(top, text="+ New Task", width=110, height=34,
                      fg_color=ACCENT, hover_color="#8aa5ff", text_color="white",
                      font=F_BOLD, command=self._create).pack(side="right", padx=14)
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._refresh())
        ctk.CTkEntry(top, textvariable=self.search_var, placeholder_text="Search tasks…",
                     width=180, height=34, font=F_SMALL).pack(side="right", padx=(0, 8))

        split = ctk.CTkFrame(self, fg_color="transparent")
        split.grid(row=1, column=0, sticky="nsew")
        split.columnconfigure(0, weight=1)
        split.rowconfigure(0, weight=1)

        self.task_list = ctk.CTkScrollableFrame(split, fg_color=SURFACE, corner_radius=14)
        self.task_list.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # Result panel
        self.result_panel = ctk.CTkFrame(split, fg_color=SURFACE, corner_radius=14, width=340)
        self.result_panel.grid(row=0, column=1, sticky="nsew")
        self.result_panel.grid_propagate(False)
        self.result_panel.grid_remove()
        self._r_title = ctk.CTkLabel(self.result_panel, text="", font=F_HEAD, text_color=TEXT, wraplength=300)
        self._r_title.pack(anchor="w", padx=14, pady=(14, 2))
        self._r_ts = ctk.CTkLabel(self.result_panel, text="", font=F_SMALL, text_color=MUTED)
        self._r_ts.pack(anchor="w", padx=14)
        ctk.CTkFrame(self.result_panel, fg_color=BORDER, height=1).pack(fill="x", padx=14, pady=8)
        self._r_text = ctk.CTkTextbox(self.result_panel, fg_color="transparent", text_color=TEXT,
                                       font=F_BODY, border_width=0, wrap="word")
        self._r_text.pack(fill="both", expand=True, padx=10, pady=4)
        ctk.CTkButton(self.result_panel, text="Close", width=80, height=28,
                      fg_color=SURF2, hover_color=BORDER, text_color=MUTED,
                      font=F_SMALL, command=self.result_panel.grid_remove).pack(anchor="e", padx=14, pady=(0, 12))

    def _refresh(self):
        for w in self.task_list.winfo_children():
            w.destroy()
        tasks = cfg.get("tasks", [])
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        if query:
            tasks = [t for t in tasks
                     if query in t.get("name", "").lower()
                     or query in t.get("prompt", "").lower()]
        if not tasks:
            msg = "No tasks match your search." if query else "No tasks yet. Create one →"
            ctk.CTkLabel(self.task_list, text=msg,
                         font=F_BODY, text_color=MUTED).pack(pady=40)
            return
        for task in tasks:
            self._row(task)

    def _row(self, task):
        agents_list = cfg.get("agents", [])
        agent = next((a for a in agents_list if a["id"] == task.get("agent", "assistant")),
                     {"icon": "✦", "id": "assistant"})
        color = AGENT_COLORS.get(agent["id"], ACCENT)
        running = task["id"] in self._running

        row = ctk.CTkFrame(self.task_list, fg_color=SURF2, corner_radius=12)
        row.pack(fill="x", padx=8, pady=4)
        row.columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=agent["icon"], font=("Segoe UI", 22), text_color=color, width=44
                     ).grid(row=0, column=0, rowspan=2, padx=(12, 0), pady=10)

        name_f = ctk.CTkFrame(row, fg_color="transparent")
        name_f.grid(row=0, column=1, sticky="w", padx=10, pady=(10, 0))
        ctk.CTkLabel(name_f, text=task["name"], font=F_BOLD, text_color=TEXT).pack(side="left")
        interval = task.get("interval", "none")
        if interval != "none":
            ctk.CTkLabel(name_f, text=f"  ↻ {interval}", font=F_SMALL, text_color=WARNING).pack(side="left", padx=4)
        if running:
            ctk.CTkLabel(name_f, text=" ⚙ running", font=F_SMALL, text_color=ACCENT).pack(side="left")

        preview = task.get("prompt", "")[:85] + ("…" if len(task.get("prompt","")) > 85 else "")
        ctk.CTkLabel(row, text=preview, font=F_SMALL, text_color=MUTED, anchor="w"
                     ).grid(row=1, column=1, sticky="w", padx=10, pady=(0, 8))

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.grid(row=0, column=2, rowspan=2, padx=10, pady=8)

        if task.get("last_result"):
            ctk.CTkButton(btns, text="View", width=60, height=26,
                          fg_color=SURF2, border_color=BORDER, border_width=1,
                          hover_color=BORDER, text_color=MUTED, font=F_SMALL,
                          command=lambda t=task: self._view(t)).pack(pady=2)

        ctk.CTkButton(btns,
                      text="⏳" if running else "▶ Run",
                      width=78, height=26,
                      fg_color=tint(SUCCESS, 0x22), hover_color=tint(SUCCESS, 0x44),
                      text_color=SUCCESS, border_color=tint(SUCCESS, 0x88), border_width=1,
                      font=F_SMALL, state="disabled" if running else "normal",
                      command=lambda t=task: self._run(t)).pack(pady=2)

        ctk.CTkButton(btns, text="✕", width=30, height=26,
                      fg_color=tint(DANGER, 0x22), hover_color=tint(DANGER, 0x44),
                      text_color=DANGER, border_color=tint(DANGER, 0x88), border_width=1,
                      font=F_SMALL, command=lambda t=task: self._delete(t)).pack(pady=2)

    def _view(self, task):
        self.result_panel.grid()
        self._r_title.configure(text=task["name"])
        self._r_ts.configure(text=f"Last run: {task.get('last_run','Never')}")
        self._r_text.configure(state="normal")
        self._r_text.delete("1.0", "end")
        self._r_text.insert("end", task.get("last_result", "No result."))
        self._r_text.configure(state="disabled")

    def _run(self, task):
        self._running.add(task["id"])
        self._refresh()
        self.scheduler.run_task_now(task)

    def _delete(self, task):
        if not messagebox.askyesno("Delete", f"Delete '{task['name']}'?"):
            return
        tasks = [t for t in cfg.get("tasks", []) if t["id"] != task["id"]]
        cfg.set_key("tasks", tasks)
        self._refresh()

    def _create(self):
        TaskDialog(self.root, on_save=self._on_saved)

    def _on_saved(self, data):
        tasks = cfg.get("tasks", [])
        tasks.append(data)
        cfg.set_key("tasks", tasks)
        self.scheduler.reload()
        self._refresh()

    def on_task_done(self, task_id, name, result):
        self._running.discard(task_id)
        on_main(self.root, self._refresh)


class ProjectManagerDialog(ctk.CTkToplevel):
    """Add, rename, and delete projects. 'General' is protected."""

    def __init__(self, master, on_changed):
        super().__init__(master)
        self.on_changed = on_changed
        self.title("Manage Projects")
        self.geometry("420x460")
        self.configure(fg_color=SURFACE)
        self.grab_set()
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="📁 Projects", font=F_HEAD, text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text="Group related chats together.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 10))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        add = ctk.CTkFrame(self, fg_color=SURF2, corner_radius=10)
        add.pack(fill="x", padx=14, pady=(0, 14))
        add.columnconfigure(0, weight=1)
        self.new_name = ctk.CTkEntry(add, placeholder_text="New project name", height=34, font=F_SMALL)
        self.new_name.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(add, text="Add", width=70, height=34, fg_color=ACCENT,
                      hover_color="#8aa5ff", text_color="white", font=F_SMALL,
                      command=self._add).grid(row=0, column=1, padx=(0, 10))

        self._refresh()

    def _refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        for p in cfg.get("projects", []):
            row = ctk.CTkFrame(self.list_frame, fg_color=SURF2, corner_radius=8)
            row.pack(fill="x", pady=3)
            row.columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=p["name"], font=F_BODY, text_color=TEXT, anchor="w"
                         ).grid(row=0, column=0, sticky="ew", padx=10, pady=8)
            if p["id"] != "general":
                ctk.CTkButton(row, text="Rename", width=70, height=28, fg_color=SURF3,
                              hover_color=BORDER, text_color=TEXT, font=F_SMALL,
                              command=lambda pr=p: self._rename(pr)).grid(row=0, column=1, padx=2)
                ctk.CTkButton(row, text="✕", width=28, height=28, fg_color="transparent",
                              hover_color=tint(DANGER, 0x33), text_color=DANGER, font=F_SMALL,
                              command=lambda pr=p: self._delete(pr)).grid(row=0, column=2, padx=(2, 8))
            else:
                ctk.CTkLabel(row, text="default", font=F_SMALL, text_color=MUTED
                             ).grid(row=0, column=1, padx=(0, 12))

    def _add(self):
        name = self.new_name.get().strip()
        if not name:
            return
        projects = cfg.get("projects", [])
        projects.append({"id": f"proj_{uuid.uuid4().hex[:8]}", "name": name})
        cfg.set_key("projects", projects)
        self.new_name.delete(0, "end")
        self._refresh()
        self.on_changed()

    def _rename(self, proj):
        dlg = ctk.CTkInputDialog(text=f"Rename '{proj['name']}' to:", title="Rename project")
        new = dlg.get_input()
        if new and new.strip():
            projects = cfg.get("projects", [])
            for p in projects:
                if p["id"] == proj["id"]:
                    p["name"] = new.strip()
            cfg.set_key("projects", projects)
            self._refresh()
            self.on_changed()

    def _delete(self, proj):
        if not messagebox.askyesno(
                "Delete project",
                f"Delete '{proj['name']}'? Its chats stay saved but become "
                "ungrouped (moved to General)."):
            return
        projects = [p for p in cfg.get("projects", []) if p["id"] != proj["id"]]
        cfg.set_key("projects", projects)
        self._refresh()
        self.on_changed()


class AgentManagerDialog(ctk.CTkToplevel):
    """List agents with edit/delete, and a button to create new ones."""

    def __init__(self, master, on_changed):
        super().__init__(master)
        self.on_changed = on_changed
        self.title("Manage Agents")
        self.geometry("440x500")
        self.configure(fg_color=SURFACE)
        self.grab_set()
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="🤖 Agents", font=F_HEAD, text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text="Each agent has its own system prompt and style.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 10))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        ctk.CTkButton(self, text="＋  New agent", height=38, fg_color=ACCENT,
                      hover_color="#8aa5ff", text_color="white", font=F_BOLD,
                      command=self._new).pack(fill="x", padx=14, pady=(0, 14))
        self._refresh()

    def _refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        for agent in cfg.get("agents", []):
            row = ctk.CTkFrame(self.list_frame, fg_color=SURF2, corner_radius=8)
            row.pack(fill="x", pady=3)
            row.columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text=agent.get("icon", "✦"), font=("Segoe UI", 18),
                         text_color=agent_color(agent), width=34
                         ).grid(row=0, column=0, padx=(8, 0), pady=8)
            ctk.CTkLabel(row, text=agent["name"], font=F_BOLD, text_color=TEXT, anchor="w"
                         ).grid(row=0, column=1, sticky="ew", padx=6)
            ctk.CTkButton(row, text="Edit", width=60, height=28, fg_color=SURF3,
                          hover_color=BORDER, text_color=TEXT, font=F_SMALL,
                          command=lambda a=agent: self._edit(a)).grid(row=0, column=2, padx=2)
            if not agent.get("builtin"):
                ctk.CTkButton(row, text="✕", width=28, height=28, fg_color="transparent",
                              hover_color=tint(DANGER, 0x33), text_color=DANGER, font=F_SMALL,
                              command=lambda a=agent: self._delete(a)).grid(row=0, column=3, padx=(2, 8))

    def _new(self):
        AgentDialog(self, on_save=self._save)

    def _edit(self, agent):
        AgentDialog(self, on_save=self._save, on_delete=self._delete_id, agent=agent)

    def _save(self, data):
        agents = cfg.get("agents", [])
        existing = next((a for a in agents if a["id"] == data["id"]), None)
        if existing:
            existing.update(data)
        else:
            agents.append(data)
        cfg.set_key("agents", agents)
        self._refresh()
        self.on_changed()

    def _delete(self, agent):
        if messagebox.askyesno("Delete agent", f"Delete '{agent['name']}'?"):
            self._delete_id(agent["id"])

    def _delete_id(self, agent_id):
        agents = [a for a in cfg.get("agents", []) if a["id"] != agent_id]
        cfg.set_key("agents", agents)
        self._refresh()
        self.on_changed()


class AgentDialog(ctk.CTkToplevel):
    """Create or edit a custom agent (name, icon, colour, system prompt)."""

    ICONS = ["✦", "✍", "◫", "◈", "⌥", "★", "❖", "✸", "♦", "▲", "●", "✿"]

    def __init__(self, master, on_save, on_delete=None, agent=None):
        super().__init__(master)
        self.on_save = on_save
        self.on_delete = on_delete
        self.agent = agent  # None when creating
        self.title("Edit Agent" if agent else "New Agent")
        self.geometry("520x560")
        self.resizable(False, False)
        self.configure(fg_color=SURFACE)
        self.grab_set()
        self._icon = (agent or {}).get("icon", self.ICONS[0])
        self._color = (agent or {}).get("color", AGENT_PALETTE[0])
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Custom Agent", font=F_HEAD, text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text="Give it a name and a system prompt that defines its behaviour.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 12))

        ctk.CTkLabel(self, text="Name", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        self.name_var = ctk.StringVar(value=(self.agent or {}).get("name", ""))
        ctk.CTkEntry(self, textvariable=self.name_var, height=36, font=F_BODY,
                     placeholder_text="e.g. Email Drafter").pack(fill="x", padx=20, pady=(4, 10))

        ctk.CTkLabel(self, text="Short description (optional)", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        self.desc_var = ctk.StringVar(value=(self.agent or {}).get("desc", ""))
        ctk.CTkEntry(self, textvariable=self.desc_var, height=36, font=F_BODY).pack(fill="x", padx=20, pady=(4, 10))

        # Icon picker
        ctk.CTkLabel(self, text="Icon", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        icon_row = ctk.CTkFrame(self, fg_color="transparent")
        icon_row.pack(fill="x", padx=18, pady=(4, 10))
        self._icon_btns = {}
        for ic in self.ICONS:
            b = ctk.CTkButton(icon_row, text=ic, width=34, height=34, font=F_HEAD,
                              fg_color=SURF3 if ic == self._icon else SURF2,
                              hover_color=SURF3, text_color=TEXT,
                              command=lambda i=ic: self._pick_icon(i))
            b.pack(side="left", padx=2)
            self._icon_btns[ic] = b

        # Colour picker
        ctk.CTkLabel(self, text="Colour", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        col_row = ctk.CTkFrame(self, fg_color="transparent")
        col_row.pack(fill="x", padx=18, pady=(4, 10))
        self._col_btns = {}
        for col in AGENT_PALETTE:
            b = ctk.CTkButton(col_row, text="✓" if col == self._color else "", width=30, height=30,
                              fg_color=col, hover_color=col, text_color="white",
                              command=lambda c=col: self._pick_color(c))
            b.pack(side="left", padx=2)
            self._col_btns[col] = b

        ctk.CTkLabel(self, text="System prompt", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        self.sys_box = ctk.CTkTextbox(self, height=110, font=F_BODY, fg_color=SURF2,
                                      text_color=TEXT, border_width=0)
        self.sys_box.pack(fill="x", padx=20, pady=(4, 12))
        self.sys_box.insert("end", (self.agent or {}).get("system", ""))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btns, text="Save", height=40, fg_color=ACCENT, hover_color="#8aa5ff",
                      text_color="white", font=F_BOLD, command=self._save
                      ).pack(side="left", expand=True, fill="x", padx=(0, 4))
        if self.agent and self.on_delete:
            ctk.CTkButton(btns, text="Delete", height=40, width=90,
                          fg_color=tint(DANGER, 0x22), hover_color=tint(DANGER, 0x44),
                          text_color=DANGER, font=F_BODY, command=self._delete
                          ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Cancel", height=40, width=80, fg_color=SURF2,
                      hover_color=BORDER, text_color=MUTED, font=F_BODY,
                      command=self.destroy).pack(side="left", padx=(4, 0))

    def _pick_icon(self, ic):
        self._icon = ic
        for k, b in self._icon_btns.items():
            b.configure(fg_color=SURF3 if k == ic else SURF2)

    def _pick_color(self, col):
        self._color = col
        for k, b in self._col_btns.items():
            b.configure(text="✓" if k == col else "")

    def _save(self):
        name = self.name_var.get().strip()
        system = self.sys_box.get("1.0", "end").strip()
        if not name or not system:
            messagebox.showwarning("Missing info", "Name and system prompt are required.")
            return
        data = {
            "id": self.agent["id"] if self.agent else f"custom_{uuid.uuid4().hex[:8]}",
            "name": name,
            "desc": self.desc_var.get().strip(),
            "icon": self._icon,
            "color": self._color,
            "system": system,
            "builtin": False,
        }
        self.on_save(data)
        self.destroy()

    def _delete(self):
        if messagebox.askyesno("Delete agent", f"Delete '{self.agent['name']}'?"):
            self.on_delete(self.agent["id"])
            self.destroy()


class PromptLibraryDialog(ctk.CTkToplevel):
    """Browse, use, add, and delete reusable prompts. Picking one inserts it
    into the chat input box."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self.title("Prompt Library")
        self.geometry("460x520")
        self.configure(fg_color=SURFACE)
        self.grab_set()
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="📝 Prompt Library", font=F_HEAD, text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text="Click a prompt to drop it into the chat box.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 10))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        add = ctk.CTkFrame(self, fg_color=SURF2, corner_radius=10)
        add.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(add, text="New prompt", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=10, pady=(8, 2))
        self.new_name = ctk.CTkEntry(add, placeholder_text="Name", height=32, font=F_SMALL)
        self.new_name.pack(fill="x", padx=10, pady=2)
        self.new_text = ctk.CTkTextbox(add, height=60, font=F_SMALL, fg_color=SURFACE,
                                       text_color=TEXT, border_width=0)
        self.new_text.pack(fill="x", padx=10, pady=2)
        ctk.CTkButton(add, text="+ Add prompt", height=32, fg_color=ACCENT,
                      hover_color="#8aa5ff", text_color="white", font=F_SMALL,
                      command=self._add).pack(fill="x", padx=10, pady=(2, 10))

        self._refresh()

    def _refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        prompts = cfg.get("prompt_library", [])
        if not prompts:
            ctk.CTkLabel(self.list_frame, text="No prompts yet. Add one below.",
                         font=F_BODY, text_color=MUTED).pack(pady=20)
            return
        for i, p in enumerate(prompts):
            row = ctk.CTkFrame(self.list_frame, fg_color=SURF2, corner_radius=10)
            row.pack(fill="x", pady=3)
            row.columnconfigure(0, weight=1)
            preview = p.get("text", "").strip().replace("\n", " ")
            preview = preview[:50] + ("…" if len(preview) > 50 else "")
            ctk.CTkButton(row, text=p.get("name", "Prompt"), anchor="w",
                          fg_color="transparent", hover_color=SURF3, text_color=TEXT,
                          font=F_BOLD, height=30, command=lambda t=p.get("text", ""): self._pick(t)
                          ).grid(row=0, column=0, sticky="ew", padx=(8, 0), pady=(6, 0))
            ctk.CTkButton(row, text="✕", width=28, height=28, fg_color="transparent",
                          hover_color=tint(DANGER, 0x33), text_color=DANGER, font=F_SMALL,
                          command=lambda idx=i: self._delete(idx)).grid(row=0, column=1, rowspan=2, padx=4)
            ctk.CTkLabel(row, text=preview, font=F_SMALL, text_color=MUTED, anchor="w"
                         ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

    def _pick(self, text):
        self.on_pick(text)
        self.destroy()

    def _add(self):
        name = self.new_name.get().strip()
        text = self.new_text.get("1.0", "end").strip()
        if not name or not text:
            messagebox.showwarning("Missing info", "Enter both a name and prompt text.")
            return
        prompts = cfg.get("prompt_library", [])
        prompts.append({"name": name, "text": text})
        cfg.set_key("prompt_library", prompts)
        self.new_name.delete(0, "end")
        self.new_text.delete("1.0", "end")
        self._refresh()

    def _delete(self, idx):
        prompts = cfg.get("prompt_library", [])
        if 0 <= idx < len(prompts):
            prompts.pop(idx)
            cfg.set_key("prompt_library", prompts)
            self._refresh()


class TaskDialog(ctk.CTkToplevel):
    def __init__(self, master, on_save, preset_date=None):
        super().__init__(master)
        self.on_save = on_save
        # When opened from the calendar, default to a one-off task on that date.
        self.preset_date = preset_date
        self.title("New Task")
        self.geometry("520x600")
        self.resizable(False, False)
        self.configure(fg_color=SURFACE)
        self.grab_set()
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="New Task", font=F_HEAD, text_color=TEXT).pack(anchor="w", padx=20, pady=(18, 4))
        ctk.CTkLabel(self, text="Tasks can run on a schedule automatically.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 14))

        ctk.CTkLabel(self, text="Task name", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        self.name_var = ctk.StringVar()
        ctk.CTkEntry(self, textvariable=self.name_var, placeholder_text="e.g. Morning briefing",
                     height=38, font=F_BODY).pack(fill="x", padx=20, pady=(4, 14))

        ctk.CTkLabel(self, text="What should ARIA do?", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        self.prompt_box = ctk.CTkTextbox(self, height=100, font=F_BODY, fg_color=SURF2,
                                          text_color=TEXT, border_width=0)
        self.prompt_box.pack(fill="x", padx=20, pady=(4, 14))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 12))
        row.columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(row, text="Schedule", font=F_BOLD, text_color=TEXT).grid(row=0, column=0, sticky="w")
        self.interval_var = ctk.StringVar(value="once" if self.preset_date else "none")
        ctk.CTkComboBox(row, variable=self.interval_var, height=36, font=F_BODY,
                        values=["none","once","hourly","daily","weekly","monthly"],
                        command=self._on_interval, dropdown_fg_color=SURF2
                        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkLabel(row, text="Agent", font=F_BOLD, text_color=TEXT).grid(row=0, column=1, sticky="w")
        agents = cfg.get("agents", [])
        names = [a["name"] for a in agents]
        self.agent_var = ctk.StringVar(value=names[0] if names else "Assistant")
        ctk.CTkComboBox(row, variable=self.agent_var, height=36, font=F_BODY,
                        values=names, dropdown_fg_color=SURF2).grid(row=1, column=1, sticky="ew")

        # Date + time row. Date anchors 'once' (exact day), 'weekly' (weekday)
        # and 'monthly' (day-of-month) tasks; time sets when they run.
        dt_row = ctk.CTkFrame(self, fg_color="transparent")
        dt_row.pack(fill="x", padx=20, pady=(0, 12))
        dt_row.columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(dt_row, text="Date (YYYY-MM-DD)", font=F_BOLD, text_color=TEXT).grid(row=0, column=0, sticky="w")
        default_date = self.preset_date or date.today().strftime("%Y-%m-%d")
        self.date_var = ctk.StringVar(value=default_date)
        self._date_entry = ctk.CTkEntry(dt_row, textvariable=self.date_var, height=36, font=F_BODY)
        self._date_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkLabel(dt_row, text="Time (HH:MM)", font=F_BOLD, text_color=TEXT).grid(row=0, column=1, sticky="w")
        self.time_var = ctk.StringVar(value="09:00")
        ctk.CTkEntry(dt_row, textvariable=self.time_var, height=36, font=F_BODY
                     ).grid(row=1, column=1, sticky="ew")

        self._hint = ctk.CTkLabel(self, text="", font=F_SMALL, text_color=MUTED, anchor="w", justify="left")
        self._hint.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkButton(self, text="Create Task", height=42, fg_color=ACCENT,
                      hover_color="#8aa5ff", text_color="white", font=F_BOLD,
                      command=self._save).pack(fill="x", padx=20, pady=(0, 6))
        ctk.CTkButton(self, text="Cancel", height=36, fg_color=SURF2,
                      hover_color=BORDER, text_color=MUTED, font=F_BODY,
                      command=self.destroy).pack(fill="x", padx=20, pady=(0, 16))

        self._on_interval(self.interval_var.get())

    def _on_interval(self, value):
        """Show which schedule was picked and enable the date field when used."""
        hints = {
            "none":    "Runs only when you click ▶ Run. No schedule.",
            "once":    "Runs once on the date and time below, then disables itself.",
            "hourly":  "Runs every hour while ARIA is open. Date/time ignored.",
            "daily":   "Runs every day at the time below.",
            "weekly":  "Runs weekly on the weekday of the date below, at that time.",
            "monthly": "Runs monthly on the day-of-month of the date below, at that time.",
        }
        self._hint.configure(text=hints.get(value, ""))
        needs_date = value in ("once", "weekly", "monthly")
        self._date_entry.configure(state="normal" if needs_date else "disabled")

    def _save(self):
        name = self.name_var.get().strip()
        prompt = self.prompt_box.get("1.0", "end").strip()
        if not name or not prompt:
            messagebox.showwarning("Missing info", "Please fill in both fields.")
            return
        interval = self.interval_var.get()
        run_date = self.date_var.get().strip()
        run_at = self.time_var.get().strip() or "09:00"

        if interval in ("once", "weekly", "monthly"):
            try:
                datetime.strptime(run_date, "%Y-%m-%d")
            except ValueError:
                messagebox.showwarning("Invalid date", "Please enter the date as YYYY-MM-DD.")
                return
        if interval in ("once", "daily", "weekly", "monthly"):
            try:
                datetime.strptime(run_at, "%H:%M")
            except ValueError:
                messagebox.showwarning("Invalid time", "Please enter the time as HH:MM (24-hour).")
                return

        agents = cfg.get("agents", [])
        agent = next((a for a in agents if a["name"] == self.agent_var.get()), agents[0] if agents else {"id": "assistant"})
        self.on_save({
            "id": str(uuid.uuid4()),
            "name": name, "prompt": prompt,
            "interval": interval,
            "run_date": run_date, "run_at": run_at,
            "agent": agent["id"], "enabled": True,
            "last_run": None, "last_result": None, "status": "idle",
        })
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# CALENDAR TAB
# ══════════════════════════════════════════════════════════════════════════════

class CalendarTab(ctk.CTkFrame):
    """Month grid showing which days have scheduled tasks. Click a day to see
    its tasks and add a new one anchored to that date."""

    WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def __init__(self, master, root, scheduler, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.root = root
        self.scheduler = scheduler
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._selected = today
        self._day_cells = {}  # date -> frame, for highlight refresh
        self._build()
        self.refresh()

    def _build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        # ── Header with month nav ───────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, height=58)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        top.grid_propagate(False)
        ctk.CTkLabel(top, text="Calendar", font=F_TITLE, text_color=TEXT).pack(side="left", padx=18)
        ctk.CTkLabel(top, text="See recurring tasks and schedule by date",
                     font=F_SMALL, text_color=MUTED).pack(side="left")

        nav = ctk.CTkFrame(top, fg_color="transparent")
        nav.pack(side="right", padx=14)
        ctk.CTkButton(nav, text="‹", width=34, height=32, fg_color=SURF2,
                      hover_color=BORDER, text_color=TEXT, font=F_HEAD,
                      command=lambda: self._shift_month(-1)).pack(side="left", padx=2)
        self.month_lbl = ctk.CTkLabel(nav, text="", font=F_BOLD, text_color=TEXT, width=150)
        self.month_lbl.pack(side="left", padx=6)
        ctk.CTkButton(nav, text="›", width=34, height=32, fg_color=SURF2,
                      hover_color=BORDER, text_color=TEXT, font=F_HEAD,
                      command=lambda: self._shift_month(1)).pack(side="left", padx=2)
        ctk.CTkButton(nav, text="Today", width=64, height=32,
                      fg_color=tint(ACCENT, 0x33), hover_color=tint(ACCENT, 0x55),
                      text_color=ACCENT, font=F_SMALL, command=self._go_today).pack(side="left", padx=(8, 0))

        # ── Month grid ──────────────────────────────────────────────────────
        self.grid_frame = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14)
        self.grid_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        for c in range(7):
            self.grid_frame.columnconfigure(c, weight=1, uniform="day")
        for r in range(1, 7):
            self.grid_frame.rowconfigure(r, weight=1, uniform="week")

        for c, wd in enumerate(self.WEEKDAYS):
            ctk.CTkLabel(self.grid_frame, text=wd, font=("Segoe UI Semibold", 11),
                         text_color=MUTED).grid(row=0, column=c, pady=(10, 4))

        # ── Side panel: tasks on the selected day ───────────────────────────
        self.side = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14)
        self.side.grid(row=1, column=1, sticky="nsew")
        self.side.columnconfigure(0, weight=1)
        self.side.rowconfigure(1, weight=1)

        self.side_title = ctk.CTkLabel(self.side, text="", font=F_HEAD, text_color=TEXT)
        self.side_title.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        self.side_list = ctk.CTkScrollableFrame(self.side, fg_color="transparent")
        self.side_list.grid(row=1, column=0, sticky="nsew", padx=8)

        self.add_btn = ctk.CTkButton(self.side, text="+ Schedule on this day", height=38,
                                     fg_color=ACCENT, hover_color="#8aa5ff", text_color="white",
                                     font=F_BOLD, command=self._add_on_selected)
        self.add_btn.grid(row=2, column=0, sticky="ew", padx=12, pady=12)

    # ── Month navigation ────────────────────────────────────────────────────

    def _shift_month(self, delta):
        m = self._month + delta
        y = self._year
        while m < 1:
            m += 12; y -= 1
        while m > 12:
            m -= 12; y += 1
        self._month, self._year = m, y
        self.refresh()

    def _go_today(self):
        today = date.today()
        self._year, self._month, self._selected = today.year, today.month, today
        self.refresh()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def refresh(self):
        """Redraw the month grid and the side panel from current settings."""
        self.month_lbl.configure(text=f"{_calendar.month_name[self._month]} {self._year}")

        # Clear previous day cells (keep weekday header row).
        for cell in self._day_cells.values():
            cell.destroy()
        self._day_cells = {}

        tasks = cfg.get("tasks", [])
        today = date.today()
        weeks = _calendar.Calendar(firstweekday=0).monthdatescalendar(self._year, self._month)
        for r, week in enumerate(weeks, start=1):
            for c, d in enumerate(week):
                in_month = (d.month == self._month)
                count = sum(1 for t in tasks if t.get("enabled", True) and task_occurs_on(t, d))
                self._day_cells[d] = self._make_cell(r, c, d, in_month, d == today, count)

        self._render_side()

    def _make_cell(self, r, c, d, in_month, is_today, count):
        selected = (d == self._selected)
        if selected:
            fg = tint(ACCENT, 0x44)
        elif is_today:
            fg = SURF3
        else:
            fg = SURF2 if in_month else SURFACE
        cell = ctk.CTkFrame(self.grid_frame, fg_color=fg, corner_radius=8,
                            border_width=1 if is_today else 0, border_color=ACCENT)
        cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)

        day_color = TEXT if in_month else MUTED
        ctk.CTkLabel(cell, text=str(d.day), font=F_SMALL,
                     text_color=ACCENT if selected else day_color).pack(anchor="nw", padx=6, pady=(4, 0))
        if count:
            ctk.CTkLabel(cell, text=f"● {count}", font=("Segoe UI", 10),
                         text_color=SUCCESS if not selected else "white").pack(anchor="w", padx=6)

        # Whole cell is clickable.
        for w in [cell] + list(cell.winfo_children()):
            w.bind("<Button-1>", lambda e, dd=d: self._select(dd))
        return cell

    def _select(self, d):
        self._selected = d
        # Jump to that month if the clicked day spilled over from another month.
        if d.month != self._month or d.year != self._year:
            self._month, self._year = d.month, d.year
        self.refresh()

    def _render_side(self):
        self.side_title.configure(text=self._selected.strftime("%A, %b %d"))
        for w in self.side_list.winfo_children():
            w.destroy()

        tasks = [t for t in cfg.get("tasks", [])
                 if t.get("enabled", True) and task_occurs_on(t, self._selected)]
        if not tasks:
            ctk.CTkLabel(self.side_list, text="No tasks scheduled.\nClick below to add one.",
                         font=F_BODY, text_color=MUTED, justify="left").pack(anchor="w", padx=10, pady=20)
            return

        for task in tasks:
            agent_id = task.get("agent", "assistant")
            color = AGENT_COLORS.get(agent_id, ACCENT)
            card = ctk.CTkFrame(self.side_list, fg_color=SURF2, corner_radius=10)
            card.pack(fill="x", padx=6, pady=4)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(head, text=task.get("name", "Task"), font=F_BOLD, text_color=TEXT).pack(side="left")
            interval = task.get("interval", "none")
            badge = "once" if interval == "once" else f"↻ {interval}"
            ctk.CTkLabel(head, text=badge, font=F_SMALL, text_color=color).pack(side="right")
            meta = task.get("run_at", "")
            ctk.CTkLabel(card, text=f"⏰ {meta}" if meta else "", font=F_SMALL,
                         text_color=MUTED).pack(anchor="w", padx=10)
            ctk.CTkButton(card, text="▶ Run now", height=26,
                          fg_color=tint(SUCCESS, 0x22), hover_color=tint(SUCCESS, 0x44),
                          text_color=SUCCESS, border_color=tint(SUCCESS, 0x88), border_width=1,
                          font=F_SMALL, command=lambda t=task: self.scheduler.run_task_now(t)
                          ).pack(anchor="e", padx=10, pady=(4, 10))

    def _add_on_selected(self):
        TaskDialog(self.root, on_save=self._on_saved,
                   preset_date=self._selected.strftime("%Y-%m-%d"))

    def _on_saved(self, data):
        tasks = cfg.get("tasks", [])
        tasks.append(data)
        cfg.set_key("tasks", tasks)
        self.scheduler.reload()
        self.refresh()


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY TAB
# ══════════════════════════════════════════════════════════════════════════════

class MemoryTab(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._build()
        self._refresh()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14, height=58)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.grid_propagate(False)
        ctk.CTkLabel(top, text="Memory", font=F_TITLE, text_color=TEXT).pack(side="left", padx=18)
        ctk.CTkLabel(top, text="Facts ARIA remembers about you",
                     font=F_SMALL, text_color=MUTED).pack(side="left")
        ctk.CTkButton(top, text="+ Add fact", width=100, height=34,
                      fg_color=ACCENT, hover_color="#8aa5ff", text_color="white",
                      font=F_BOLD, command=self._add_dialog).pack(side="right", padx=4)
        ctk.CTkButton(top, text="Clear all", width=90, height=34,
                      fg_color=tint(DANGER, 0x22), hover_color=tint(DANGER, 0x44),
                      text_color=DANGER, border_color=tint(DANGER, 0x88), border_width=1,
                      font=F_SMALL, command=self._clear_all).pack(side="right", padx=14)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=SURFACE, corner_radius=14)
        self.scroll.grid(row=1, column=0, sticky="nsew")

    def _refresh(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        data = mem.recall()
        facts = data.get("all_facts", [])
        if not facts:
            ctk.CTkLabel(self.scroll, text="No memories yet. ARIA will store facts as you chat.",
                         font=F_BODY, text_color=MUTED).pack(pady=40)
            return
        for fact in facts:
            row = ctk.CTkFrame(self.scroll, fg_color=SURF2, corner_radius=10)
            row.pack(fill="x", padx=8, pady=3)
            row.columnconfigure(1, weight=1)
            cat_color = {"preference": PURPLE, "work": ACCENT, "personal": WARNING, "task": SUCCESS}.get(fact.get("category","general"), MUTED)
            ctk.CTkLabel(row, text=f"●", font=F_BOLD, text_color=cat_color, width=20
                         ).grid(row=0, column=0, padx=(12, 0), pady=12)
            ctk.CTkLabel(row, text=fact["key"], font=F_BOLD, text_color=TEXT, anchor="w"
                         ).grid(row=0, column=1, sticky="w", padx=8, pady=(12, 0))
            ctk.CTkLabel(row, text=fact["value"], font=F_BODY, text_color=MUTED, anchor="w", wraplength=500
                         ).grid(row=1, column=1, sticky="w", padx=8, pady=(0, 10))
            ctk.CTkButton(row, text="✕", width=28, height=28,
                          fg_color="transparent", hover_color=tint(DANGER, 0x33),
                          text_color=MUTED, font=F_SMALL,
                          command=lambda k=fact["key"]: self._forget(k)
                          ).grid(row=0, column=2, padx=10, pady=10)

    def _forget(self, key):
        mem.forget(key)
        self._refresh()

    def _clear_all(self):
        if messagebox.askyesno("Clear memory", "Delete all memories? This cannot be undone."):
            mem.clear_all_memory()
            self._refresh()

    def _add_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Memory")
        dialog.geometry("400x280")
        dialog.configure(fg_color=SURFACE)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="Key (short label)", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20, pady=(18, 2))
        key_var = ctk.StringVar()
        ctk.CTkEntry(dialog, textvariable=key_var, height=36, font=F_BODY).pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(dialog, text="Value", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        val_var = ctk.StringVar()
        ctk.CTkEntry(dialog, textvariable=val_var, height=36, font=F_BODY).pack(fill="x", padx=20, pady=(0, 14))
        def save():
            if key_var.get().strip() and val_var.get().strip():
                mem.remember(key_var.get().strip(), val_var.get().strip())
                self._refresh()
                dialog.destroy()
        ctk.CTkButton(dialog, text="Save", height=40, fg_color=ACCENT,
                      hover_color="#8aa5ff", text_color="white", font=F_BOLD,
                      command=save).pack(fill="x", padx=20, pady=(0, 6))
        ctk.CTkButton(dialog, text="Cancel", height=36, fg_color=SURF2,
                      hover_color=BORDER, text_color=MUTED, font=F_BODY,
                      command=dialog.destroy).pack(fill="x", padx=20)


# ══════════════════════════════════════════════════════════════════════════════
# PLUGINS TAB
# ══════════════════════════════════════════════════════════════════════════════

class PluginsTab(ctk.CTkScrollableFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Plugins", font=F_TITLE, text_color=TEXT).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(self, text="Drop a .py file in the /plugins folder to add new tools. Restart ARIA to load.",
                     font=F_SMALL, text_color=MUTED, wraplength=600).pack(anchor="w", pady=(0, 16))

        plugins_dir = Path(__file__).parent / "plugins"
        ctk.CTkButton(self, text="📁 Open plugins folder", width=180, height=36,
                      fg_color=SURF2, border_color=BORDER, border_width=1,
                      hover_color=SURF3, text_color=TEXT, font=F_BODY,
                      command=lambda: os.startfile(str(plugins_dir)) if os.name == "nt" else None
                      ).pack(anchor="w", pady=(0, 20))

        plugins = get_plugin_info()
        if not plugins:
            ctk.CTkLabel(self, text="No plugins found. Add a .py file to /plugins.",
                         font=F_BODY, text_color=MUTED).pack(pady=20)
            return

        for p in plugins:
            card = ctk.CTkFrame(self, fg_color=SURF2, corner_radius=12)
            card.pack(fill="x", pady=6)
            status_color = SUCCESS if p["status"] == "loaded" else DANGER
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=14, pady=(12, 4))
            ctk.CTkLabel(hdr, text=p["file"], font=F_BOLD, text_color=TEXT).pack(side="left")
            ctk.CTkLabel(hdr, text=f"● {p['status']}", font=F_SMALL,
                         text_color=status_color).pack(side="right")
            ctk.CTkLabel(card, text=p["description"], font=F_BODY,
                         text_color=MUTED, anchor="w", wraplength=580).pack(anchor="w", padx=14)
            if p["tools"]:
                tools_text = "Tools: " + ", ".join(p["tools"])
                ctk.CTkLabel(card, text=tools_text, font=F_SMALL,
                             text_color=ACCENT, anchor="w").pack(anchor="w", padx=14, pady=(4, 12))


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM MONITOR WIDGET (sidebar)
# ══════════════════════════════════════════════════════════════════════════════

class SystemMonitor(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color=SURF2, corner_radius=10, **kw)
        self._build()
        self._update()

    def _build(self):
        ctk.CTkLabel(self, text="SYSTEM", font=("Segoe UI Semibold", 9),
                     text_color=MUTED).pack(anchor="w", padx=10, pady=(8, 2))
        self.cpu_lbl = ctk.CTkLabel(self, text="CPU  —", font=F_SMALL, text_color=MUTED)
        self.cpu_lbl.pack(anchor="w", padx=10)
        self.ram_lbl = ctk.CTkLabel(self, text="RAM  —", font=F_SMALL, text_color=MUTED)
        self.ram_lbl.pack(anchor="w", padx=10, pady=(0, 8))

    def _update(self):
        if PSUTIL:
            try:
                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                cpu_color = DANGER if cpu > 80 else WARNING if cpu > 50 else MUTED
                ram_color = DANGER if ram > 85 else WARNING if ram > 70 else MUTED
                self.cpu_lbl.configure(text=f"CPU  {cpu:.0f}%", text_color=cpu_color)
                self.ram_lbl.configure(text=f"RAM  {ram:.0f}%", text_color=ram_color)
            except Exception:
                pass
        try:
            self.after(5000, self._update)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS TAB
# ══════════════════════════════════════════════════════════════════════════════

class SettingsTab(ctk.CTkScrollableFrame):
    def __init__(self, master, on_saved, app=None, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.on_saved = on_saved
        self.app = app
        self._build()

    def _build(self):
        s = cfg.load()
        ctk.CTkLabel(self, text="Settings", font=F_TITLE, text_color=TEXT).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(self, text="Configure AI providers, privacy, and behaviour.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", pady=(0, 20))

        def section(title):
            ctk.CTkLabel(self, text=title, font=F_HEAD, text_color=TEXT).pack(anchor="w", pady=(14, 0))
            ctk.CTkFrame(self, fg_color=BORDER, height=1).pack(fill="x", pady=(4, 12))

        def lbl(text):
            ctk.CTkLabel(self, text=text, font=F_BOLD, text_color=MUTED).pack(anchor="w")

        # Provider
        section("AI Provider")
        self.provider_var = ctk.StringVar(value=s.get("provider", "claude"))
        prow = ctk.CTkFrame(self, fg_color="transparent")
        prow.pack(fill="x", pady=(4, 14))
        for val, lab in [("claude","Claude (Anthropic)"),("openai","ChatGPT (OpenAI)"),("local","Local (Ollama)")]:
            ctk.CTkRadioButton(prow, text=lab, variable=self.provider_var, value=val,
                               font=F_BODY, text_color=TEXT).pack(side="left", padx=14)

        lbl("Claude model")
        self.claude_model = ctk.StringVar(value=s.get("claude_model","claude-opus-4-5"))
        ctk.CTkComboBox(self, variable=self.claude_model, height=38, font=F_BODY,
                        values=["claude-opus-4-5","claude-sonnet-4-5","claude-haiku-4-5-20251001"],
                        dropdown_fg_color=SURF2).pack(fill="x", pady=(4,12))

        lbl("OpenAI model")
        self.openai_model = ctk.StringVar(value=s.get("openai_model","gpt-4o"))
        ctk.CTkComboBox(self, variable=self.openai_model, height=38, font=F_BODY,
                        values=["gpt-5.5","gpt-5.5-codex","gpt-4o","gpt-4-turbo","gpt-3.5-turbo"],
                        dropdown_fg_color=SURF2).pack(fill="x", pady=(4,12))
        ctk.CTkLabel(self, text="ChatGPT sign-in needs a gpt-5.x model "
                                "(legacy ones fall back to gpt-5.5).",
                     font=F_SMALL, text_color=MUTED, justify="left").pack(anchor="w", pady=(0, 8))

        lbl("Local model (Ollama)")
        self.local_model = ctk.StringVar(value=s.get("ollama_model","llama3"))
        ctk.CTkComboBox(self, variable=self.local_model, height=38, font=F_BODY,
                        values=["llama3","mistral","gemma","phi3","llama3:70b","qwen2.5-coder:32b"],
                        dropdown_fg_color=SURF2).pack(fill="x", pady=(4,12))

        lbl("Ollama URL")
        self.ollama_url = ctk.StringVar(value=s.get("ollama_url","http://localhost:11434"))
        ctk.CTkEntry(self, textvariable=self.ollama_url, height=38, font=F_BODY).pack(fill="x", pady=(4,12))

        # API Keys
        section("API Keys")
        ctk.CTkLabel(self, text="🔒 Stored only on your computer in AppData. Never uploaded.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", pady=(0, 8))

        lbl("Claude API key")
        self.claude_key = ctk.StringVar(value=s.get("claude_api_key",""))
        ctk.CTkEntry(self, textvariable=self.claude_key, show="•", height=38, font=F_BODY,
                     placeholder_text="sk-ant-...").pack(fill="x", pady=(4,12))

        lbl("OpenAI API key")
        self.openai_key = ctk.StringVar(value=s.get("openai_api_key",""))
        ctk.CTkEntry(self, textvariable=self.openai_key, show="•", height=38, font=F_BODY,
                     placeholder_text="sk-...").pack(fill="x", pady=(4,12))

        # "Sign in with ChatGPT" (Codex OAuth) as an alternative to the key.
        from agent import openai_oauth
        oauth_row = ctk.CTkFrame(self, fg_color=SURF2, corner_radius=10)
        oauth_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(oauth_row, text="Or use your ChatGPT subscription (Codex OAuth)",
                     font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=10, pady=(8, 0))
        ctk.CTkLabel(oauth_row,
                     text="Uses your ChatGPT Plus/Pro plan instead of a per-token API key.\n"
                          "Community flow (like OpenClaw); OpenAI could change it at any time.",
                     font=F_SMALL, text_color=MUTED, justify="left").pack(anchor="w", padx=10)
        self.openai_auth_mode = ctk.StringVar(value=s.get("openai_auth_mode", "apikey"))
        ctk.CTkCheckBox(oauth_row, text="Use ChatGPT sign-in instead of API key",
                        variable=self.openai_auth_mode, onvalue="oauth", offvalue="apikey",
                        font=F_SMALL, text_color=TEXT).pack(anchor="w", padx=10, pady=(6, 2))
        signin_row = ctk.CTkFrame(oauth_row, fg_color="transparent")
        signin_row.pack(fill="x", padx=10, pady=(2, 10))
        self.oauth_status = ctk.CTkLabel(
            signin_row,
            text=("✓ Signed in" if openai_oauth.is_signed_in() else "Not signed in"),
            font=F_SMALL, text_color=(SUCCESS if openai_oauth.is_signed_in() else MUTED))
        self.oauth_status.pack(side="left")
        ctk.CTkButton(signin_row, text="Sign out", width=80, height=30, fg_color=SURF3,
                      hover_color=BORDER, text_color=MUTED, font=F_SMALL,
                      command=self._oauth_signout).pack(side="right", padx=(6, 0))
        ctk.CTkButton(signin_row, text="Sign in with ChatGPT", width=170, height=30,
                      fg_color=ACCENT, hover_color="#8aa5ff", text_color="white",
                      font=F_SMALL, command=self._oauth_signin).pack(side="right")

        # Workspace
        section("Workspace")
        lbl("Default folder")
        ws_row = ctk.CTkFrame(self, fg_color="transparent")
        ws_row.pack(fill="x", pady=(4,12))
        ws_row.columnconfigure(0, weight=1)
        self.workspace = ctk.StringVar(value=s.get("workspace_folder", str(Path.home() / "Documents")))
        ctk.CTkEntry(ws_row, textvariable=self.workspace, height=38, font=F_BODY
                     ).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(ws_row, text="Browse", width=90, height=38,
                      fg_color=SURF2, hover_color=BORDER, text_color=TEXT, font=F_BODY,
                      command=self._browse).grid(row=0, column=1, padx=(8,0))

        # Behaviour
        section("Appearance")
        lbl("Theme (applies after restart)")
        self.theme_var = ctk.StringVar(value=s.get("theme", "dark"))
        ctk.CTkComboBox(self, variable=self.theme_var, height=38, font=F_BODY,
                        values=["dark", "light"], dropdown_fg_color=SURF2).pack(fill="x", pady=(4, 12))

        section("Behaviour")
        checks = [
            ("computer_use_enabled", "Enable Computer Use (AI controls mouse & keyboard)"),
            ("browser_enabled",       "Enable web browser (AI can browse websites)"),
            ("show_agent_thinking",   "Show tool activity in chat"),
            ("auto_save_chats",       "Auto-save conversations to history"),
            ("clipboard_watcher",     "Watch clipboard and offer to process copied text"),
            ("minimize_to_tray",      "Minimize to system tray instead of closing"),
        ]
        self._check_vars = {}
        for key, label in checks:
            var = ctk.BooleanVar(value=s.get(key, True))
            self._check_vars[key] = var
            ctk.CTkCheckBox(self, text=label, variable=var,
                            font=F_BODY, text_color=TEXT).pack(anchor="w", pady=3)

        lbl("Max response length (tokens)")
        self.max_tokens = ctk.StringVar(value=str(s.get("max_tokens", 4096)))
        ctk.CTkComboBox(self, variable=self.max_tokens, height=38, font=F_BODY,
                        values=["1024","2048","4096","8192"],
                        dropdown_fg_color=SURF2).pack(fill="x", pady=(4,20))

        # Updates
        section("Updates")
        self._check_vars["auto_check_updates"] = ctk.BooleanVar(value=s.get("auto_check_updates", True))
        ctk.CTkCheckBox(self, text="Check for updates on startup",
                        variable=self._check_vars["auto_check_updates"],
                        font=F_BODY, text_color=TEXT).pack(anchor="w", pady=3)

        lbl("GitHub repo (owner/name)")
        self.github_repo = ctk.StringVar(value=s.get("github_repo", ""))
        ctk.CTkEntry(self, textvariable=self.github_repo, height=38, font=F_BODY,
                     placeholder_text="yourusername/aria-desktop-assistant").pack(fill="x", pady=(4, 8))

        ver_row = ctk.CTkFrame(self, fg_color="transparent")
        ver_row.pack(fill="x", pady=(0, 20))
        ctk.CTkLabel(ver_row, text=f"Current version: v{updater.get_current_version()}",
                     font=F_SMALL, text_color=MUTED).pack(side="left")
        self.update_status = ctk.CTkLabel(ver_row, text="", font=F_SMALL, text_color=ACCENT)
        self.update_status.pack(side="left", padx=10)
        ctk.CTkButton(ver_row, text="Check now", width=120, height=32,
                      fg_color=SURF2, hover_color=BORDER, text_color=TEXT, font=F_SMALL,
                      command=self._check_updates).pack(side="right")

        ctk.CTkButton(self, text="Save Settings", height=44, fg_color=ACCENT,
                      hover_color="#8aa5ff", text_color="white", font=F_BOLD,
                      command=self._save).pack(fill="x", pady=(0,8))

    def _browse(self):
        folder = filedialog.askdirectory(title="Select workspace folder")
        if folder:
            self.workspace.set(folder)

    def _save(self):
        s = cfg.load()
        s["provider"] = self.provider_var.get()
        s["claude_model"] = self.claude_model.get()
        s["openai_model"] = self.openai_model.get()
        s["ollama_model"] = self.local_model.get()
        s["ollama_url"] = self.ollama_url.get()
        s["claude_api_key"] = self.claude_key.get()
        s["openai_api_key"] = self.openai_key.get()
        s["openai_auth_mode"] = self.openai_auth_mode.get()
        s["workspace_folder"] = self.workspace.get()
        s["max_tokens"] = int(self.max_tokens.get())
        s["github_repo"] = self.github_repo.get().strip()
        s["telegram_bot_token"] = self.telegram_token.get().strip()
        s["discord_webhook_url"] = self.discord_webhook.get().strip()
        # Parse the comma-separated allowlist into a clean list of id strings.
        s["telegram_allowlist"] = [c.strip() for c in self.telegram_allow.get().split(",") if c.strip()]
        theme_changed = s.get("theme") != self.theme_var.get()
        s["theme"] = self.theme_var.get()
        for key, var in self._check_vars.items():
            s[key] = var.get()
        cfg.save(s)
        self.on_saved()
        msg = "Settings saved!"
        if theme_changed:
            msg += "\n\nRestart ARIA to apply the new theme."
        messagebox.showinfo("Saved", msg)

    def _check_updates(self):
        """Manual update check. Saves the repo field first so the check uses
        whatever is currently typed in the box."""
        cfg.set_key("github_repo", self.github_repo.get().strip())
        if self.app and hasattr(self.app, "check_updates_interactive"):
            self.app.check_updates_interactive(
                status_cb=lambda text: self.update_status.configure(text=text))
        else:
            self.update_status.configure(text="Update check unavailable.")

    # ── Messaging tests ──────────────────────────────────────────────────────

    def _test_telegram(self):
        from agent import messaging
        token = self.telegram_token.get().strip()
        self.telegram_status.configure(text="Checking…", text_color=ACCENT)

        def work():
            info = messaging.telegram_get_me(token)
            if info:
                txt = f"✓ Connected as @{info.get('username', '?')}"
                col = SUCCESS
            else:
                txt = "✗ Invalid token"
                col = DANGER
            on_main(self, lambda: self.telegram_status.configure(text=txt, text_color=col))

        threading.Thread(target=work, daemon=True).start()

    def _test_discord(self):
        from agent import messaging
        url = self.discord_webhook.get().strip()
        if not url:
            messagebox.showinfo("Discord", "Enter a webhook URL first.")
            return

        def work():
            # Temporarily use this URL via a throwaway send.
            ok = messaging._post_json(url, {"content": "✅ ARIA test message"}, timeout=15) is not None
            on_main(self, lambda: messagebox.showinfo(
                "Discord", "Test message sent!" if ok else "Failed to send. Check the URL."))

        threading.Thread(target=work, daemon=True).start()

    # ── OpenAI ChatGPT sign-in (Codex OAuth) ─────────────────────────────────

    def _oauth_signin(self):
        from agent import openai_oauth
        self.oauth_status.configure(text="Opening browser…", text_color=ACCENT)

        def done(_tokens):
            on_main(self, lambda: (
                self.oauth_status.configure(text="✓ Signed in", text_color=SUCCESS),
                self.openai_auth_mode.set("oauth")))

        def fail(msg):
            on_main(self, lambda: self.oauth_status.configure(
                text=msg[:60], text_color=DANGER))

        openai_oauth.start_login(on_success=done, on_error=fail)

    def _oauth_signout(self):
        from agent import openai_oauth
        openai_oauth.clear_tokens()
        self.oauth_status.configure(text="Not signed in", text_color=MUTED)
        self.openai_auth_mode.set("apikey")


# ══════════════════════════════════════════════════════════════════════════════
# UPDATE DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class UpdateDialog(ctk.CTkToplevel):
    """Shown when a newer release is available. Lets the user download & install
    in place, open the release page in a browser, or dismiss."""

    def __init__(self, master, info, on_quit_for_update):
        super().__init__(master)
        self.info = info
        self.on_quit_for_update = on_quit_for_update
        self.title("Update available")
        self.geometry("480x470")
        self.resizable(False, False)
        self.configure(fg_color=SURFACE)
        self.grab_set()
        self._build()

    def _build(self):
        cur = updater.get_current_version()
        new = self.info.get("version", "?")
        ctk.CTkLabel(self, text="🚀 Update available", font=F_HEAD, text_color=TEXT
                     ).pack(anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(self, text=f"You have v{cur}.  Latest is v{new}.",
                     font=F_SMALL, text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 12))

        ctk.CTkLabel(self, text="Release notes", font=F_BOLD, text_color=TEXT).pack(anchor="w", padx=20)
        notes = ctk.CTkTextbox(self, height=180, font=F_SMALL, fg_color=SURF2,
                               text_color=TEXT, border_width=0, wrap="word")
        notes.pack(fill="both", expand=True, padx=20, pady=(4, 10))
        notes.insert("end", self.info.get("notes") or "No release notes provided.")
        notes.configure(state="disabled")

        self.status = ctk.CTkLabel(self, text="", font=F_SMALL, text_color=MUTED)
        self.status.pack(anchor="w", padx=20)
        self.progress = ctk.CTkProgressBar(self, height=8)
        self.progress.set(0)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(8, 16))

        self.install_btn = ctk.CTkButton(btns, text="Download & Install", height=40,
                                         fg_color=ACCENT, hover_color="#8aa5ff",
                                         text_color="white", font=F_BOLD, command=self._install)
        self.install_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        # Self-update only works in the packaged build; from source just link out.
        if not updater.is_frozen():
            self.install_btn.configure(state="disabled")
            self.status.configure(text="Run the packaged app to auto-install. From source, git pull.")

        ctk.CTkButton(btns, text="Open release page", height=40, fg_color=SURF2,
                      hover_color=BORDER, text_color=TEXT, font=F_BODY,
                      command=self._open_page).pack(side="left", expand=True, fill="x", padx=4)
        ctk.CTkButton(btns, text="Later", height=40, width=70, fg_color="transparent",
                      hover_color=SURF2, text_color=MUTED, font=F_BODY,
                      command=self.destroy).pack(side="left", padx=(4, 0))

    def _open_page(self):
        import webbrowser
        url = self.info.get("html_url")
        if url:
            webbrowser.open(url)

    def _install(self):
        self.install_btn.configure(state="disabled", text="Downloading…")
        self.progress.pack(fill="x", padx=20, pady=(0, 8))
        updater.download_and_apply(
            self.info,
            on_progress=lambda frac: on_main(self, lambda f=frac: self.progress.set(f)),
            on_ready=lambda: on_main(self, self._apply),
            on_error=lambda msg: on_main(self, lambda m=msg: self._fail(m)),
        )

    def _apply(self):
        self.status.configure(text="Update ready. Restarting ARIA…", text_color=SUCCESS)
        # Hand off to the helper script, which waits for this process to exit.
        self.after(800, self.on_quit_for_update)

    def _fail(self, msg):
        self.status.configure(text=msg, text_color=DANGER)
        self.install_btn.configure(state="normal", text="Retry")
        self.progress.pack_forget()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class ARIAApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ARIA — Personal AI Assistant")
        self.geometry("1180x740")
        self.minsize(860, 580)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._setup_services()
        self._build()
        self._setup_hotkey()
        self._maybe_check_updates()

    def _setup_services(self):
        # Scheduler
        self.scheduler = TaskScheduler(
            on_task_start=lambda tid, name: on_main(self, lambda: self._on_task_start(tid, name)),
            on_task_done=lambda tid, name, res: on_main(self, lambda: self._on_task_done(tid, name, res)),
        )
        self.scheduler.start()

        # System tray
        self.tray = TrayManager(
            on_show=lambda: on_main(self, self._show_window),
            on_quit=lambda: on_main(self, self.destroy),
            on_new_chat=lambda: on_main(self, self._new_chat),
        )
        self.tray.start()

        # Clipboard watcher
        self.clip_watcher = ClipboardWatcher(
            on_new_content=lambda text: self._on_clipboard(text),
        )
        if cfg.get("clipboard_watcher", True):
            self.clip_watcher.start()

        # Messaging channels (Telegram in/out, Discord out). Inbound Telegram
        # messages run the agent with the default assistant prompt and full
        # tools (per the user's chosen security level).
        self.messaging = MessagingService(
            run_agent=self._run_agent_for_messaging,
            on_status=lambda s: on_main(self, lambda: self.status_lbl.configure(text=f"● {s}")),
        )
        self.messaging.start()

    def _run_agent_for_messaging(self, prompt: str) -> str:
        """Run the agent for an inbound Telegram message. Uses the first agent's
        system prompt and allows all tools (computer use included)."""
        agents = cfg.get("agents", [])
        system = agents[0]["system"] if agents else "You are a helpful assistant."
        return run_agent_sync(prompt, system_prompt=system,
                              use_computer_tools=True, use_browser_tools=True)

    def _setup_hotkey(self):
        """Register global hotkey Ctrl+Shift+Space to show ARIA."""
        try:
            from pynput import keyboard as kb
            def on_activate():
                on_main(self, self._show_window)
            hotkey = kb.GlobalHotKeys({"<ctrl>+<shift>+<space>": on_activate})
            t = threading.Thread(target=hotkey.run, daemon=True)
            t.start()
        except Exception:
            pass

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ── Sidebar ────────────────────────────────────────────────────────
        sb = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, width=210)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.rowconfigure(10, weight=1)

        # Logo
        logo = ctk.CTkFrame(sb, fg_color="transparent")
        logo.pack(fill="x", padx=16, pady=(20, 24))
        ctk.CTkLabel(logo, text="ARIA", font=("Segoe UI Black", 26), text_color=TEXT).pack(side="left")
        ctk.CTkLabel(logo, text=".", font=("Segoe UI Black", 30), text_color=ACCENT).pack(side="left")

        # Nav
        self._active_tab = None
        self.nav_btns = {}
        nav_items = [
            ("chat",     "💬", "Chat"),
            ("tasks",    "⚡", "Tasks"),
            ("calendar", "📅", "Calendar"),
            ("memory",   "🧠", "Memory"),
            ("plugins",  "🔌", "Plugins"),
            ("settings", "⚙",  "Settings"),
        ]
        for tab_id, icon, label in nav_items:
            btn = ctk.CTkButton(
                sb, text=f"  {icon}  {label}", anchor="w", height=44,
                fg_color="transparent", hover_color=SURF2,
                text_color=MUTED, font=F_BODY, corner_radius=10,
                command=lambda t=tab_id: self._switch(t),
            )
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_btns[tab_id] = btn

        # Spacer
        ctk.CTkFrame(sb, fg_color="transparent").pack(fill="both", expand=True)

        # System monitor
        self.sysmon = SystemMonitor(sb)
        self.sysmon.pack(fill="x", padx=10, pady=4)

        # Status box
        stat = ctk.CTkFrame(sb, fg_color=SURF2, corner_radius=10)
        stat.pack(fill="x", padx=10, pady=(4, 14))
        ctk.CTkLabel(stat, text="STATUS", font=("Segoe UI Semibold", 9),
                     text_color=MUTED).pack(anchor="w", padx=12, pady=(8, 2))
        self.status_lbl = ctk.CTkLabel(stat, text="● Ready", font=F_SMALL, text_color=SUCCESS)
        self.status_lbl.pack(anchor="w", padx=12)
        self.provider_lbl = ctk.CTkLabel(stat, text=f"Provider: {cfg.get('provider','claude')}",
                                          font=F_SMALL, text_color=MUTED)
        self.provider_lbl.pack(anchor="w", padx=12, pady=(0, 8))

        # ── Content ────────────────────────────────────────────────────────
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        self.chat_tab    = ChatTab(self.content, self, on_notify=self._notify)
        self.tasks_tab   = TasksTab(self.content, self, self.scheduler)
        self.calendar_tab = CalendarTab(self.content, self, self.scheduler)
        self.memory_tab  = MemoryTab(self.content)
        self.plugins_tab = PluginsTab(self.content)
        self.settings_tab = SettingsTab(self.content, on_saved=self._on_settings_saved, app=self)

        self._tabs = {
            "chat": self.chat_tab,
            "tasks": self.tasks_tab,
            "calendar": self.calendar_tab,
            "memory": self.memory_tab,
            "plugins": self.plugins_tab,
            "settings": self.settings_tab,
        }
        self._switch("chat")

    # ── Navigation ─────────────────────────────────────────────────────────

    def _switch(self, tab_id):
        if self._active_tab == tab_id:
            return
        self._active_tab = tab_id
        for tid, btn in self.nav_btns.items():
            btn.configure(fg_color=SURF2 if tid == tab_id else "transparent",
                          text_color=TEXT if tid == tab_id else MUTED)
        for tid, tab in self._tabs.items():
            if tid == tab_id:
                tab.grid(row=0, column=0, sticky="nsew")
            else:
                tab.grid_remove()
        # Calendar reflects tasks created in other tabs, so refresh on entry.
        if tab_id == "calendar":
            self.calendar_tab.refresh()

    # ── Event handlers ─────────────────────────────────────────────────────

    def _on_task_start(self, task_id, name):
        self.status_lbl.configure(text=f"⚙  {name}…", text_color=ACCENT)
        self.tray.update_status(f"Running: {name}")

    def _on_task_done(self, task_id, name, result):
        self.status_lbl.configure(text="● Ready", text_color=SUCCESS)
        self.tray.update_status("Ready")
        self.tasks_tab.on_task_done(task_id, name, result)
        self._notify("Task complete", f"{name} finished.")

    def _notify(self, title: str, message: str):
        """Send a desktop notification (only if window is not focused)."""
        try:
            if not self.focus_displayof():
                send_notification(title, message, duration=4)
        except Exception:
            pass

    def _on_clipboard(self, text: str):
        if cfg.get("clipboard_watcher", True):
            self.chat_tab.on_clipboard(text)

    def _on_settings_saved(self):
        provider = cfg.get("provider", "claude")
        self.provider_lbl.configure(text=f"Provider: {provider}")
        self.chat_tab.reload_agents()
        # Update clipboard watcher state
        if cfg.get("clipboard_watcher", True):
            self.clip_watcher.set_enabled(True)
        else:
            self.clip_watcher.set_enabled(False)
        # Apply any messaging changes (token / enabled toggled in Settings).
        if hasattr(self, "messaging"):
            self.messaging.restart()

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _new_chat(self):
        self._show_window()
        self.chat_tab._save_and_clear()
        self._switch("chat")

    # ── Updates ──────────────────────────────────────────────────────────────

    def _maybe_check_updates(self):
        """Silently check for updates on startup if enabled. Only surfaces a
        dialog when a newer version is actually available."""
        if not cfg.get("auto_check_updates", True):
            return
        updater.check_for_updates(
            on_update_available=lambda info: on_main(self, lambda: self._show_update_dialog(info)),
        )

    def check_updates_interactive(self, status_cb=None):
        """Check on demand (from Settings). `status_cb(text)` reports progress."""
        if status_cb:
            status_cb("Checking…")

        def report(text):
            if status_cb:
                on_main(self, lambda: status_cb(text))

        updater.check_for_updates(
            on_update_available=lambda info: on_main(self, lambda: (
                report(f"Update available: v{info['version']}"),
                self._show_update_dialog(info))),
            on_up_to_date=lambda: report(
                f"You're up to date (v{updater.get_current_version()})."),
            on_error=lambda msg: report(msg),
        )

    def _show_update_dialog(self, info):
        UpdateDialog(self, info, on_quit_for_update=self.destroy)

    def _on_close(self):
        if cfg.get("minimize_to_tray", True):
            self.withdraw()
        else:
            self.destroy()


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ARIAApp()
    app.mainloop()
