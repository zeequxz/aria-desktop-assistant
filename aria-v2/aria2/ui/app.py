"""ui/app.py - Main window: sidebar navigation + swappable views.

Responsibilities unique to the app shell:
  * marshal event-bus callbacks (fired on worker threads) onto the Tk main
    thread via `after`, so views can subscribe safely (`self.app.on_event`),
  * host the tool-approval dialog and register it as the permission approver, so
    an "ask" tool blocks the worker thread until the user decides,
  * own the active project/agent selection shared across views.
"""

from __future__ import annotations

import threading

import customtkinter as ctk

from aria2.core import config
from aria2.core.events import bus
from aria2.runtime.tools import permissions
from aria2.services import (
    ambient_service,
    automation_service,
    connector_service,
    heartbeat_service,
    messaging_service,
    tray_service,
)
from aria2.ui import theme
from aria2.ui.views.agents_view import AgentsView
from aria2.ui.views.automations_view import AutomationsView
from aria2.ui.views.calendar_view import CalendarView
from aria2.ui.views.chat_view import ChatView
from aria2.ui.views.connectors_view import ConnectorsView
from aria2.ui.views.evals_view import EvalsView
from aria2.ui.views.knowledge_view import KnowledgeView
from aria2.ui.views.memory_view import MemoryView
from aria2.ui.views.projects_view import ProjectsView
from aria2.ui.views.runs_view import RunsView
from aria2.ui.views.settings_view import SettingsView

_NAV = [
    ("chat", "💬  Chat", ChatView),
    ("projects", "🗂  Projects", ProjectsView),
    ("agents", "🤖  Agents", AgentsView),
    ("memory", "🧠  Memory", MemoryView),
    ("knowledge", "📚  Knowledge", KnowledgeView),
    ("connectors", "🔌  Connectors", ConnectorsView),
    ("automations", "⏱  Automations", AutomationsView),
    ("calendar", "📅  Calendar", CalendarView),
    ("runs", "📊  Runs", RunsView),
    ("evals", "🧪  Evals", EvalsView),
    ("settings", "⚙  Settings", SettingsView),
]


class ARIAApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("ARIA v2 — AI Workstation")
        self.minsize(980, 640)
        self.configure(fg_color=theme.BG)
        self._restore_geometry()
        self._set_icon()
        self._geo_save_id = None
        self.bind("<Configure>", self._on_configure)

        self.active_project = config.get("active_project", "general")
        self._views: dict[str, ctk.CTkFrame] = {}
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._current = None
        from aria2.ui.views.toast import ToastManager
        self._toasts = ToastManager(self)

        import tkinter as tk
        _nav_w = max(140, min(400, int(config.get("sidebar_nav_width", 216))))
        # tk.PanedWindow gives native C-level smooth resize — no Python event
        # polling, no grid minsize tricks. This is the only correct approach.
        paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL,
            sashwidth=5, sashrelief="flat",
            bg=theme.BORDER, bd=0, borderwidth=0,
            handlesize=0, sashpad=0,
        )
        paned.grid(row=0, column=0, sticky="nsew")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._paned_nav = paned

        # Sidebar frame — pack_propagate(False) keeps its width fixed.
        self._sidebar_host = ctk.CTkFrame(paned, width=_nav_w,
                                          fg_color=theme.SIDEBAR, corner_radius=0)
        self._sidebar_host.pack_propagate(False)

        # Content frame (views are packed/gridded inside here).
        self._content = ctk.CTkFrame(paned, fg_color=theme.BG, corner_radius=0)

        paned.add(self._sidebar_host, minsize=140, width=_nav_w, stretch="never")
        paned.add(self._content,      minsize=400,               stretch="always")

        # Save sidebar width when the user releases the sash.
        paned.bind("<ButtonRelease-1>",
                   lambda e: self._save_nav_width())

        self._build_sidebar()

        self.bind_all("<Control-k>", lambda e: self._open_palette())
        self.bind_all("<Control-K>", lambda e: self._open_palette())
        self.bind_all("<F1>", lambda e: self.open_about())
        self._subscribe_toast()
        permissions.set_approver(self._approve_tool)
        automation_service.scheduler.start()
        ambient_service.watcher.start()
        messaging_service.bridge.start()
        messaging_service.discord_bridge.start()
        heartbeat_service.heartbeat.start()
        tray_service.tray.start(self)
        from aria2.services import ollama_model_manager as _omm
        _omm.model_manager.start()   # manages all local models (load/unload/keep-alive)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show("chat")
        if config.get("auto_check_updates", True):
            self._check_updates_async()
        self._prewarm_views()

    # ── Toast ─────────────────────────────────────────────────────────────────────

    def toast(self, message: str, kind: str = "info", duration: int = 3000):
        """Show a bottom-right auto-dismissing notification.
        kind: 'info' | 'success' | 'warning' | 'error'
        Can also be called from any thread via the event bus:
          bus.publish('toast', {'message': '...', 'kind': 'success'})
        """
        self.after(0, lambda: self._toasts.show(message, kind, duration))

    def _subscribe_toast(self):
        from aria2.core.events import bus
        bus.subscribe("toast", lambda p: self.toast(
            p.get("message", ""), p.get("kind", "info"),
            p.get("duration", 3000)))

    # ── Window geometry ───────────────────────────────────────────────────────────

    def _restore_geometry(self):
        s = config.load()
        w = max(980, int(s.get("window_width") or 1240))
        h = max(640, int(s.get("window_height") or 820))
        x = s.get("window_x")
        y = s.get("window_y")
        if x is not None and y is not None:
            # Safety: keep the window on-screen.
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = max(0, min(int(x), sw - 200))
            y = max(0, min(int(y), sh - 100))
            self.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.geometry(f"{w}x{h}")

    def _on_configure(self, _event=None):
        """Debounce window geometry saves — write at most once per 500 ms."""
        if self._geo_save_id:
            try:
                self.after_cancel(self._geo_save_id)
            except Exception:
                pass
        self._geo_save_id = self.after(500, self._save_geometry)

    def _save_geometry(self):
        self._geo_save_id = None
        try:
            self.update_idletasks()
            geo = self.wm_geometry()           # e.g. "1240x820+123+456"
            rest, x, y = geo.rsplit("+", 2) if "+" in geo else (geo, None, None)
            wh = rest.split("x")
            config.set_key("window_width",  int(wh[0]))
            config.set_key("window_height", int(wh[1]))
            if x is not None:
                config.set_key("window_x", int(x))
                config.set_key("window_y", int(y))
        except Exception:
            pass

    # ── Icon ─────────────────────────────────────────────────────────────────────

    def _set_icon(self):
        from pathlib import Path
        import sys
        # Resolve the .ico from the package (works both from source and frozen).
        candidates = [
            Path(__file__).resolve().parents[1] / "assets" / "aria2.ico",
        ]
        if getattr(sys, "_MEIPASS", None):
            candidates.insert(0,
                Path(sys._MEIPASS) / "aria2" / "assets" / "aria2.ico")
        for ico in candidates:
            if ico.exists():
                try:
                    self.iconbitmap(str(ico))
                except Exception:
                    pass
                break
        else:
            # Icon file missing — regenerate silently.
            try:
                from scripts.make_icon import make
                ico = make()
                self.iconbitmap(str(ico))
            except Exception:
                pass

    def _save_nav_width(self):
        try:
            w = int(self._paned_nav.sash_coord(0)[0])
            config.set_key("sidebar_nav_width", max(140, min(400, w)))
        except Exception:
            pass

    # ── Sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        # All sidebar content goes inside _sidebar_host (the PanedWindow pane).
        bar = self._sidebar_host

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.pack(anchor="w", fill="x", padx=20, pady=(22, 16))
        ctk.CTkLabel(
            brand, text="✦ ARIA", font=(theme.FONT, 22, "bold"),
            text_color=theme.accent()
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand, text="AI Workstation v2", font=theme.f(-2),
            text_color=theme.TEXT_FAINT
        ).pack(anchor="w", pady=(0, 0))

        for key, label, _ in _NAV:
            b = ctk.CTkButton(
                bar, text=label, anchor="w", height=38, corner_radius=9,
                fg_color="transparent", hover_color=theme.HOVER,
                text_color=theme.TEXT_DIM, font=theme.f(0),
                command=lambda k=key: self.show(k),
            )
            b.pack(fill="x", padx=10, pady=1)
            self._nav_buttons[key] = b

        ctk.CTkLabel(
            bar, text="⌘ / Ctrl + K  —  commands", font=theme.f(-2),
            text_color=theme.TEXT_FAINT, anchor="w",
        ).pack(side="bottom", fill="x", padx=20, pady=(0, 2))
        self._cost_label = ctk.CTkLabel(
            bar, text="", font=theme.f(-2), text_color=theme.TEXT_FAINT, anchor="w"
        )
        self._cost_label.pack(side="bottom", fill="x", padx=20, pady=(14, 0))

        # Update banner (hidden until an update is found).
        self._update_banner = ctk.CTkButton(
            bar, text="", height=34, corner_radius=8, fg_color=theme.accent(),
            font=theme.f(-2, "bold"), command=self._open_update,
        )
        self._update_info = None
        self._refresh_cost()

    def _refresh_cost(self):
        from aria2.services import run_service
        from aria2.core import config as _cfg
        try:
            t = run_service.totals()
            s = _cfg.load()
            dot = "🟢" if _cfg.provider_configured(s) else "🔴"
            provider = s.get("provider", "claude")
            self._cost_label.configure(
                text=f"{dot} {provider}  ·  {t['runs']} runs  ·  ${t['cost_usd']:.2f}")
        except Exception:
            pass
        self.after(8000, self._refresh_cost)

    # ── Command palette ──────────────────────────────────────────────────────────

    def build_commands(self) -> list[dict]:
        """The Ctrl+K command set: jump to any view + a few core actions."""
        cmds = [{"label": f"Go to {label.split('  ', 1)[-1]}", "hint": "view",
                 "action": (lambda k=key: self.show(k))} for key, label, _ in _NAV]
        cmds += [
            {"label": "New chat", "hint": "action", "action": self._cmd_new_chat},
            {"label": "New project", "hint": "action",
             "action": lambda: (self.show("projects"), self._views["projects"]._new())},
            {"label": "Run eval self-test", "hint": "action", "action": self._cmd_eval_selftest},
            {"label": "Check for updates", "hint": "action", "action": self._check_updates_async},
            {"label": "About ARIA", "hint": "F1", "action": self.open_about},
        ]
        return cmds

    def rebuild_views(self):
        """Destroy and clear all views so they are reconstructed with the
        current font size on next navigation. Used by the font-size slider."""
        current_key = None
        for key, view in self._views.items():
            if view is self._current:
                current_key = key
                break
        for v in list(self._views.values()):
            try:
                v.pack_forget()
                v.destroy()
            except Exception:
                pass
        self._views.clear()
        self._current = None
        if current_key:
            self.show(current_key)
        self._prewarm_views()

    def _prewarm_views(self):
        """Construct all views in a background thread so first-click is instant."""
        def _warm():
            for key, _, _ in _NAV:
                if key not in self._views:
                    self.after(0, lambda k=key: self._warm_one(k))
        threading.Thread(target=_warm, daemon=True, name="prewarm").start()

    def _warm_one(self, key: str):
        if key not in self._views:
            cls = next((c for k, _, c in _NAV if k == key), None)
            if cls:
                self._views[key] = cls(self._content, app=self)

    # ── About ─────────────────────────────────────────────────────────────────

    def open_about(self):
        import aria2
        from tkinter import messagebox
        messagebox.showinfo(
            "About ARIA v2",
            f"ARIA v2  ·  version {aria2.__version__}\n\n"
            "Local-first AI workstation.\n\n"
            "Providers: Claude · OpenAI · Grok · Gemini · Ollama · "
            "OpenAI-compatible (LM Studio/vLLM/OpenRouter…)\n"
            "Keys + tokens encrypted at rest (DPAPI)\n\n"
            "Press Ctrl+K for the command palette.\n"
            "Press ? in the Runs view to see all keyboard shortcuts.",
            parent=self)

    def _open_palette(self):
        from aria2.ui.views.command_palette import CommandPalette
        CommandPalette(self, self.build_commands())

    def _cmd_new_chat(self):
        self.show("chat")
        view = self._views.get("chat")
        if view is not None:
            view._new_chat()

    def _cmd_eval_selftest(self):
        self.show("evals")
        view = self._views.get("evals")
        if view is not None:
            view._self_test()

    # ── Updates ──────────────────────────────────────────────────────────────────

    def _check_updates_async(self):
        def worker():
            from aria2.services import update_service
            info = update_service.check_for_update()
            if info:
                self.after(0, lambda: self._show_update_banner(info))
        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def _show_update_banner(self, info: dict):
        self._update_info = info
        self._update_banner.configure(text=f"⬆ Update to v{info['version']}")
        self._update_banner.pack(side="bottom", fill="x", padx=10, pady=(0, 4))

    def _open_update(self):
        if not self._update_info:
            return
        self.show("settings")  # Settings → Updates has the version + install button
        sv = self._views.get("settings")
        if sv is not None and hasattr(sv, "_show_update"):
            import aria2
            # Pre-populate the panel with what the banner found, so the user can
            # hit "Update & restart" straight away (no second "Check now").
            try:
                sv._show_update({
                    "status": "update",
                    "current": self._update_info.get("current", aria2.__version__),
                    "version": self._update_info.get("version", ""),
                    "url": self._update_info.get("url", ""),
                    "notes": self._update_info.get("notes", ""),
                    "sha256": self._update_info.get("sha256", ""),
                })
            except Exception:
                pass

    # ── View switching ─────────────────────────────────────────────────────────

    def show(self, key: str):
        # If we're navigating *away* from Settings, give it a chance to warn.
        if key != "settings" and self._current is not None:
            sv = self._views.get("settings")
            if sv is not None and self._current is sv:
                if not sv.confirm_leave():
                    return  # user chose Cancel — stay on Settings
                # Force a full repaint after any dialog closed — prevents the
                # "black box" phantom that appears when a Windows messagebox
                # releases its grab and the CTk canvas hasn't repainted yet.
                try:
                    self.update_idletasks()
                    self.update()
                except Exception:
                    pass
        for k, b in self._nav_buttons.items():
            if k == key:
                b.configure(fg_color=theme.accent_soft(), text_color=theme.TEXT,
                            hover_color=theme.accent_soft(), font=theme.f(0, "bold"))
            else:
                b.configure(fg_color="transparent", text_color=theme.TEXT_DIM,
                            hover_color=theme.HOVER, font=theme.f(0))
        if key not in self._views:
            cls = next(c for k, _, c in _NAV if k == key)
            self._views[key] = cls(self._content, app=self)
        if self._current is not None:
            self._current.pack_forget()
        view = self._views[key]
        view.pack(fill="both", expand=True)
        self._current = view
        if hasattr(view, "on_show"):
            view.on_show()
        # Auto-focus the message input on any chat surface.
        if hasattr(view, "input"):
            view.after(60, view.input.focus_set)

    # ── Event bus → UI thread ───────────────────────────────────────────────────

    def on_event(self, topic: str, handler):
        """Subscribe to a bus topic; handler runs on the Tk main thread.

        Returns the unsubscribe function from the bus.
        """
        def _marshal(payload):
            try:
                self.after(0, lambda: handler(payload))
            except Exception:
                pass

        return bus.subscribe(topic, _marshal)

    # ── Tool approval (blocks the worker thread) ────────────────────────────────

    def _approve_tool(self, tool_name: str, tool_input: dict, prompt: str) -> bool:
        done = threading.Event()
        result = {"ok": False}

        def _ask():
            # If the window is minimized, bring it to the front and flash
            # so the user knows approval is waiting.
            try:
                state = self.wm_state()
                if state in ("iconic", "withdrawn"):
                    self.deiconify()
                    self.lift()
                    self.focus_force()
                    # Flash the taskbar button
                    try:
                        import ctypes
                        ctypes.windll.user32.FlashWindowEx  # check availability
                        FLASHW_ALL, FLASHW_TIMERNOFG = 3, 12
                        import ctypes.wintypes as wt
                        class FLASHWINFO(ctypes.Structure):
                            _fields_ = [("cbSize", wt.UINT), ("hwnd", wt.HANDLE),
                                        ("dwFlags", wt.DWORD), ("uCount", wt.UINT),
                                        ("dwTimeout", wt.DWORD)]
                        fw = FLASHWINFO(ctypes.sizeof(FLASHWINFO),
                                        self.winfo_id(), FLASHW_ALL | FLASHW_TIMERNOFG, 8, 0)
                        ctypes.windll.user32.FlashWindowEx(ctypes.byref(fw))
                    except Exception:
                        pass
            except Exception:
                pass
            dlg = ToolApprovalDialog(self, tool_name, tool_input)
            self.wait_window(dlg)
            result["ok"] = dlg.approved
            done.set()

        self.after(0, _ask)

        # If still unanswered after 2 minutes, send a Telegram nudge so the user
        # knows approval is waiting (e.g. they stepped away from the PC). This
        # must fire BEFORE the 5-minute auto-deny below, or it would be a no-op.
        def _nudge():
            if not done.is_set():
                try:
                    from aria2.services import messaging_service
                    messaging_service.notify(
                        f"⏳ ARIA is waiting for your approval to run: "
                        f"{tool_name}\nOpen the app to approve or deny.")
                except Exception:
                    pass
        import threading as _th
        nudge_t = _th.Timer(120, _nudge)
        nudge_t.daemon = True
        nudge_t.start()

        done.wait(timeout=300)  # auto-deny after 5 min of no response
        nudge_t.cancel()
        return result["ok"]

    def _on_close(self):
        # If the tray is active, closing the window hides it (ARIA keeps running
        # in the background handling Telegram / schedules / heartbeat).
        if tray_service.tray.active:
            self.withdraw()
            return
        self._real_quit()

    def _real_quit(self):
        automation_service.scheduler.stop()
        ambient_service.watcher.stop()
        messaging_service.bridge.stop()
        messaging_service.discord_bridge.stop()
        heartbeat_service.heartbeat.stop()
        tray_service.tray.stop()
        from aria2.services import ollama_model_manager as _omm
        _omm.model_manager.stop()
        connector_service.shutdown_all()
        try:  # stop any background servers started via run_shell(background=True)
            from aria2.runtime.tools import sandbox as _sandbox
            _sandbox.terminate_background()
        except Exception:
            pass
        self.destroy()


class ToolApprovalDialog(ctk.CTkToplevel):
    def __init__(self, parent, tool_name: str, tool_input: dict):
        super().__init__(parent)
        self.approved = False
        self.title("Approve tool")
        self.geometry("440x300")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(
            self, text=f"Agent wants to run:  {tool_name}",
            font=theme.f(1, "bold"), text_color=theme.TEXT,
        ).pack(anchor="w", padx=18, pady=(18, 6))

        box = ctk.CTkTextbox(self, height=150, fg_color=theme.SURFACE_2, font=theme.mono(-1))
        box.pack(fill="both", expand=True, padx=18, pady=6)
        import json
        box.insert("1.0", json.dumps(tool_input, indent=2)[:2000])
        box.configure(state="disabled")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=14)
        ctk.CTkButton(
            row, text="Deny", fg_color=theme.SURFACE_2, hover_color=theme.BORDER,
            command=self._deny,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            row, text="Allow", fg_color=theme.accent(), command=self._allow
        ).pack(side="right")

    def _allow(self):
        self.approved = True
        self.destroy()

    def _deny(self):
        self.approved = False
        self.destroy()
