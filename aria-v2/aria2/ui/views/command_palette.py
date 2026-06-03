"""ui/views/command_palette.py - Ctrl+K command palette (Linear/Notion-style).

A modal overlay to jump to any view or run a core action by typing. Keyboard:
type to filter, ↑/↓ to move, Enter to run, Esc to close. The filtering logic is a
pure function (`filter_commands`) so it's unit-testable without a GUI.
"""

from __future__ import annotations

import customtkinter as ctk

from aria2.ui import theme


def filter_commands(commands: list[dict], query: str, limit: int = 8) -> list[dict]:
    """Rank commands by a simple, predictable scheme: prefix > word-start >
    substring, across label+hint. Empty query returns the first `limit`."""
    q = (query or "").strip().lower()
    if not q:
        return commands[:limit]
    scored = []
    for c in commands:
        label = c.get("label", "").lower()
        hay = f"{label} {c.get('hint', '').lower()}"
        if q not in hay:
            continue
        if label.startswith(q):
            score = 0
        elif any(w.startswith(q) for w in label.split()):
            score = 1
        else:
            score = 2
        scored.append((score, label, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in scored[:limit]]


class CommandPalette(ctk.CTkToplevel):
    def __init__(self, parent, commands: list[dict]):
        super().__init__(parent)
        self._commands = commands
        self._results: list[dict] = []
        self._sel = 0

        self.overrideredirect(True)  # frameless overlay
        self.configure(fg_color=theme.SURFACE)
        self.attributes("-topmost", True)
        w, h = 560, 380
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw = parent.winfo_width() or 1000
        self.geometry(f"{w}x{h}+{px + (pw - w) // 2}+{py + 80}")

        outer = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=theme.RADIUS,
                             border_width=1, border_color=theme.accent())
        outer.pack(fill="both", expand=True)
        self.entry = ctk.CTkEntry(outer, placeholder_text="Type a command or view…",
                                  fg_color=theme.SURFACE_2, border_width=0,
                                  font=theme.f(2), height=44)
        self.entry.pack(fill="x", padx=10, pady=10)
        self.list = ctk.CTkScrollableFrame(outer, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        self.entry.bind("<KeyRelease>", self._on_key)
        self.bind("<Down>", lambda e: self._move(1))
        self.bind("<Up>", lambda e: self._move(-1))
        self.bind("<Return>", lambda e: self._run())
        self.bind("<Escape>", lambda e: self.destroy())
        self.entry.bind("<Down>", lambda e: self._move(1))
        self.entry.bind("<Up>", lambda e: self._move(-1))
        self.entry.bind("<Return>", lambda e: self._run())

        self.after(50, self._grab_focus)
        self._refresh()

    def _grab_focus(self):
        try:
            self.grab_set()
            self.entry.focus_force()
        except Exception:
            pass

    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        self._sel = 0
        self._refresh()

    def _refresh(self):
        self._results = filter_commands(self._commands, self.entry.get())
        for c in self.list.winfo_children():
            c.destroy()
        for i, cmd in enumerate(self._results):
            active = i == self._sel
            row = ctk.CTkFrame(self.list, fg_color=theme.SURFACE_2 if active else "transparent",
                               corner_radius=6)
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=cmd["label"], anchor="w", font=theme.f(0),
                         text_color=theme.TEXT if active else theme.TEXT_DIM).pack(
                side="left", padx=10, pady=6)
            if cmd.get("hint"):
                ctk.CTkLabel(row, text=cmd["hint"], anchor="e", font=theme.f(-2),
                             text_color=theme.TEXT_FAINT).pack(side="right", padx=10)

    def _move(self, delta: int):
        if not self._results:
            return
        self._sel = (self._sel + delta) % len(self._results)
        self._refresh()

    def _run(self):
        if not self._results:
            return
        cmd = self._results[self._sel]
        self.destroy()
        try:
            cmd["action"]()
        except Exception as e:
            print(f"[Palette] {e}")
