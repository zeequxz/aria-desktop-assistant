"""ui/views/toast.py - Bottom-right toast notifications.

Usage:
    app.toast("Settings saved", kind="success")
    app.toast("Connection failed", kind="error", duration=5000)

Kinds: "info" | "success" | "warning" | "error"
"""

from __future__ import annotations

import customtkinter as ctk
from aria2.ui import theme

_COLOURS = {
    "success": (theme.SUCCESS,   "#0d2015"),
    "error":   (theme.DANGER,    "#200d0d"),
    "warning": (theme.WARN,      "#201a08"),
    "info":    (theme.accent(),  "#111624"),
}

_ICONS = {"success": "✓", "error": "✕", "warning": "⚠", "info": "✦"}


class _Toast(ctk.CTkToplevel):
    def __init__(self, parent, message: str, kind: str, duration: int,
                 y_offset: int, on_done):
        super().__init__(parent)
        self._on_done = on_done
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color="transparent")

        fg, bg = _COLOURS.get(kind, _COLOURS["info"])
        icon = _ICONS.get(kind, "✦")

        card = ctk.CTkFrame(self, fg_color=bg, corner_radius=10,
                            border_width=1, border_color=fg)
        card.pack(padx=0, pady=0)

        ctk.CTkLabel(card, text=icon, font=(theme.FONT, 14, "bold"),
                     text_color=fg).pack(side="left", padx=(12, 4), pady=10)
        ctk.CTkLabel(card, text=message, font=theme.f(0),
                     text_color=theme.TEXT, wraplength=280,
                     justify="left").pack(side="left", padx=(0, 12), pady=10)

        self._y_offset = y_offset
        self._placed = False
        self.update_idletasks()
        self._place(parent)
        self.after(duration, self._dismiss)

    def _place(self, parent):
        try:
            self.update_idletasks()
            pw = parent.winfo_width()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            ph = parent.winfo_height()
            tw = self.winfo_reqwidth()
            th = self.winfo_reqheight()
            x = px + pw - tw - 20
            y = py + ph - th - 20 - self._y_offset
            self.geometry(f"+{x}+{y}")
            self._placed = True
        except Exception:
            pass

    def _dismiss(self):
        try:
            self.destroy()
        except Exception:
            pass
        self._on_done()


class ToastManager:
    """Manages a stack of toasts for a root window."""

    def __init__(self, root):
        self._root = root
        self._active: list[_Toast] = []

    def show(self, message: str, kind: str = "info", duration: int = 3000):
        # Calculate vertical offset so toasts stack.
        offset = sum(t.winfo_reqheight() + 8 for t in self._active
                     if t.winfo_exists())
        t = _Toast(self._root, message, kind, duration, offset,
                   on_done=lambda: self._remove(t))
        self._active.append(t)

    def _remove(self, toast: _Toast):
        if toast in self._active:
            self._active.remove(toast)
