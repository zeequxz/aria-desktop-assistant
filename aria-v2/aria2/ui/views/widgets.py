"""ui/views/widgets.py - Small shared UI helpers."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from aria2.ui import theme


class _Tooltip:
    """A lightweight hover tooltip (delayed, frameless) for any widget."""

    def __init__(self, widget, text: str, delay: int = 450):
        self.widget, self.text, self.delay = widget, text, delay
        self._tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - 40
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{max(0, x)}+{y}")
        self._tip.attributes("-topmost", True)
        tk.Label(self._tip, text=self.text, bg=theme.SURFACE_2, fg=theme.TEXT,
                 font=(theme.FONT, theme.font_size() - 2), padx=8, pady=4,
                 bd=1, relief="solid", highlightthickness=0).pack()

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def update_text(self, text: str):
        """Update tooltip text without rebinding — use this instead of calling
        add_tooltip() again, which would stack bindings."""
        self.text = text

    def _hide(self, _=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


def add_tooltip(widget, text: str):
    """Attach a hover tooltip to a widget. Returns the tooltip controller."""
    return _Tooltip(widget, text)


def header(parent, title: str, subtitle: str = "") -> ctk.CTkFrame:
    bar = ctk.CTkFrame(parent, fg_color="transparent")
    bar.pack(fill="x", padx=24, pady=(20, 8))
    ctk.CTkLabel(bar, text=title, font=theme.f(7, "bold"), text_color=theme.TEXT).pack(
        anchor="w"
    )
    if subtitle:
        ctk.CTkLabel(
            bar, text=subtitle, font=theme.f(-1), text_color=theme.TEXT_DIM
        ).pack(anchor="w", pady=(2, 0))
    return bar


def card(parent) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        parent, fg_color=theme.SURFACE, corner_radius=theme.RADIUS,
        border_width=1, border_color=theme.BORDER,
    )


def primary_button(parent, text, command, tooltip: str | None = None, **kw):
    b = ctk.CTkButton(
        parent, text=text, command=command, fg_color=theme.accent(),
        hover_color=theme.accent(), corner_radius=8, font=theme.f(0, "bold"), **kw
    )
    if tooltip:
        add_tooltip(b, tooltip)
    return b


def ghost_button(parent, text, command, tooltip: str | None = None, **kw):
    kw.setdefault("fg_color", theme.SURFACE_2)
    kw.setdefault("hover_color", theme.HOVER)
    kw.setdefault("text_color", theme.TEXT)
    kw.setdefault("corner_radius", 8)
    b = ctk.CTkButton(parent, text=text, command=command, **kw)
    if tooltip:
        add_tooltip(b, tooltip)
    return b


def labeled_entry(parent, label: str, value: str = "", show: str | None = None):
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(frame, text=label, font=theme.f(-1), text_color=theme.TEXT_DIM).pack(
        anchor="w"
    )
    entry = ctk.CTkEntry(
        frame, fg_color=theme.SURFACE_2, border_color=theme.BORDER, show=show
    )
    entry.pack(fill="x", pady=(2, 0))
    if value:
        entry.insert(0, value)
    return frame, entry
