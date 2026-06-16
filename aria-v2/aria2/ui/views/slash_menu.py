"""ui/views/slash_menu.py - Inline "/" command autocomplete for the composer.

Claude-Code-style: type "/" and a floating list of slash commands appears above
the composer, filtering as you type. ↑/↓ to move, Tab/Enter to complete, Esc to
dismiss, or click a row. The catalog + filter are a pure function so they're
unit-testable without a GUI.
"""

from __future__ import annotations

import customtkinter as ctk

from aria2.ui import theme

# The slash commands offered in the composer. `insert` is what gets put in the
# box on completion (command + trailing space, ready for the argument).
SLASH_COMMANDS = [
    {"name": "/team", "args": "<goal>",
     "desc": "Plan a goal and run it across specialist agents"},
    {"name": "/loop", "args": "<interval> <prompt>",
     "desc": "Repeat a prompt on a schedule (e.g. /loop 1h …)"},
]


def filter_slash(text: str) -> list[dict]:
    """Commands matching the composer while a slash command is being typed — i.e.
    the first line starts with "/" and the command word isn't finished yet (no
    space). Returns [] otherwise (so the menu closes once you start the argument)."""
    line = (text or "").split("\n", 1)[0]
    if not line.startswith("/") or " " in line:
        return []
    q = line[1:].lower()
    return [c for c in SLASH_COMMANDS if c["name"][1:].lower().startswith(q)]


class SlashMenu(ctk.CTkFrame):
    """Floating suggestion list, placed above the composer when open."""

    def __init__(self, parent, on_pick):
        super().__init__(parent, fg_color=theme.SURFACE_2, corner_radius=8,
                         border_width=1, border_color=theme.BORDER)
        self._on_pick = on_pick
        self._matches: list[dict] = []
        self._sel = 0
        self._rows: list = []
        self.visible = False
        self._list = ctk.CTkFrame(self, fg_color="transparent")
        self._list.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(self, text="↑↓ navigate · Tab/Enter select · Esc dismiss",
                     font=theme.f(-2), text_color=theme.TEXT_FAINT).pack(
            anchor="w", padx=10, pady=(0, 4))

    # ── State ────────────────────────────────────────────────────────────────
    def update_for(self, text: str, anchor) -> None:
        matches = filter_slash(text)
        if not matches:
            self.hide()
            return
        self._matches = matches
        self._sel = min(self._sel, len(matches) - 1)
        self._render()
        # Float just above the composer (bottom-left of the menu at its top-left).
        self.place(in_=anchor, relx=0.0, rely=0.0, anchor="sw", y=-6)
        self.lift()
        self.visible = True

    def move(self, delta: int) -> bool:
        if not self.visible or not self._matches:
            return False
        self._sel = (self._sel + delta) % len(self._matches)
        self._render()
        return True

    def accept(self) -> bool:
        """Complete the highlighted command. Returns True if it consumed the key."""
        if not self.visible or not self._matches:
            return False
        self._pick(self._sel)
        return True

    def hide(self) -> None:
        if self.visible:
            self.place_forget()
            self.visible = False
        self._sel = 0

    # ── Rendering ────────────────────────────────────────────────────────────
    def _render(self) -> None:
        for r in self._rows:
            r.destroy()
        self._rows = []
        for i, c in enumerate(self._matches):
            row = ctk.CTkFrame(
                self._list, fg_color=theme.HOVER if i == self._sel else "transparent",
                corner_radius=6, height=30)
            row.pack(fill="x", pady=1)
            name = ctk.CTkLabel(row, text=c["name"], font=theme.f(-1, "bold"),
                                text_color=theme.TEXT)
            name.pack(side="left", padx=(8, 4))
            ctk.CTkLabel(row, text=c["args"], font=theme.f(-2),
                         text_color=theme.TEXT_FAINT).pack(side="left")
            ctk.CTkLabel(row, text=c["desc"], font=theme.f(-2),
                         text_color=theme.TEXT_DIM).pack(side="right", padx=10)
            for wdg in (row, name):
                wdg.bind("<Button-1>", lambda _e, idx=i: self._pick(idx))
            self._rows.append(row)

    def _pick(self, idx: int) -> None:
        cmd = self._matches[idx]
        self.hide()
        self._on_pick(cmd)
