"""ui/views/bubble.py - Chat message bubble with markdown, timestamp, and copy.

Renders a message in a read-only tk.Text so we can do tagged rich text (bold,
inline code, fenced code blocks, headings, bullets) and let users select/copy —
things a flat CTkLabel can't do. Auto-sizes to content (display lines). During
streaming we append raw deltas for speed, then re-render markdown once on finish.
"""

from __future__ import annotations

import re
import tkinter as tk

import customtkinter as ctk

from aria2.ui import theme

_BODY_WIDTH = 74  # characters; ~matches the old 720px wrap


def _render_markdown(t: tk.Text, md: str) -> None:
    lines = md.split("\n")
    i, in_code, buf = 0, False, []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if not in_code:
                in_code, buf = True, []
            else:
                t.insert("end", "\n".join(buf) + "\n", "codeblock")
                in_code = False
            i += 1
            continue
        if in_code:
            buf.append(line)
            i += 1
            continue
        if line.startswith("## "):
            t.insert("end", line[3:] + "\n", "h2")
        elif line.startswith("# "):
            t.insert("end", line[2:] + "\n", "h1")
        elif re.match(r"^\s*[-*]\s+", line):
            t.insert("end", "   •  ")
            _inline(t, re.sub(r"^\s*[-*]\s+", "", line))
            t.insert("end", "\n")
        else:
            _inline(t, line)
            t.insert("end", "\n")
        i += 1
    if in_code and buf:
        t.insert("end", "\n".join(buf) + "\n", "codeblock")


def _inline(t: tk.Text, s: str) -> None:
    pos = 0
    for m in re.finditer(r"\*\*(.+?)\*\*|`([^`]+)`", s):
        t.insert("end", s[pos:m.start()])
        if m.group(1) is not None:
            t.insert("end", m.group(1), "bold")
        else:
            t.insert("end", m.group(2), "code")
        pos = m.end()
    t.insert("end", s[pos:])


class MessageBubble(ctk.CTkFrame):
    def __init__(self, parent, role: str, when: str = ""):
        super().__init__(parent, fg_color="transparent")
        self.is_user = role == "user"
        self._raw = ""
        fill = theme.USER_BUBBLE if self.is_user else theme.ASSISTANT_BUBBLE
        self.bubble = ctk.CTkFrame(self, fg_color=fill, corner_radius=theme.RADIUS)
        self.bubble.pack(anchor="e" if self.is_user else "w",
                         padx=(90, 4) if self.is_user else (4, 90), fill="x")

        head = ctk.CTkFrame(self.bubble, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(head, text="You" if self.is_user else "ARIA",
                     font=theme.f(-2, "bold"),
                     text_color=theme.TEXT_DIM if self.is_user else theme.accent()
                     ).pack(side="left")
        if when:
            ctk.CTkLabel(head, text=when, font=theme.f(-2),
                         text_color=theme.TEXT_FAINT).pack(side="left", padx=8)
        self._copy_btn = ctk.CTkButton(head, text="Copy", width=46, height=20,
                                       fg_color="transparent", hover_color=theme.HOVER,
                                       text_color=theme.TEXT_FAINT, font=theme.f(-2),
                                       command=self.copy)
        self._copy_btn.pack(side="right")

        mono = theme.MONO
        size = theme.font_size()
        self.text = tk.Text(
            self.bubble, wrap="word", width=_BODY_WIDTH, height=1,
            bg=fill, fg=theme.TEXT, insertbackground=theme.TEXT,
            relief="flat", bd=0, highlightthickness=0, padx=12, pady=6,
            font=(theme.FONT, size), cursor="arrow", selectbackground=theme.accent(),
        )
        self.text.pack(fill="x", padx=2, pady=(0, 6))
        self.text.tag_configure("bold", font=(theme.FONT, size, "bold"))
        self.text.tag_configure("code", font=(mono, size - 1),
                                background=theme.SURFACE_2)
        self.text.tag_configure("codeblock", font=(mono, size - 1),
                                background="#0d1017", lmargin1=10, lmargin2=10,
                                spacing1=2, spacing3=2)
        self.text.tag_configure("h1", font=(theme.FONT, size + 4, "bold"),
                                spacing1=4, spacing3=2)
        self.text.tag_configure("h2", font=(theme.FONT, size + 2, "bold"),
                                spacing1=3, spacing3=2)
        self.text.configure(state="disabled")

    # ── Content ────────────────────────────────────────────────────────────────

    def append(self, delta: str) -> None:
        """Fast path for streaming: insert raw text without re-parsing."""
        self._raw += delta
        self.text.configure(state="normal")
        self.text.insert("end", delta)
        self.text.configure(state="disabled")
        self._autosize()

    def set_markdown(self, md: str) -> None:
        self._raw = md or ""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        _render_markdown(self.text, self._raw)
        self.text.configure(state="disabled")
        self._autosize()

    def set_note(self, text: str) -> None:
        """Replace body with a transient note (e.g. a tool-use indicator)."""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", text)
        self.text.configure(state="disabled")
        self._autosize()

    def _autosize(self) -> None:
        self.text.update_idletasks()
        try:
            n = self.text.count("1.0", "end-1c", "displaylines")[0]
        except Exception:
            n = self._raw.count("\n") + 1
        self.text.configure(height=max(1, n))

    def copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._raw)
            self._copy_btn.configure(text="Copied")
            self.after(1200, lambda: self._copy_btn.configure(text="Copy"))
        except Exception:
            pass
