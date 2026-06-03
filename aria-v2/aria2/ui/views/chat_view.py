"""ui/views/chat_view.py - Multi-session chat with streaming, agents, forking.

Left: chat list for the active project. Right: message transcript + composer.
Streaming arrives over the event bus (run.token / run.tool / run.done), filtered
by the run_id returned from chat_service.send_async.
"""

from __future__ import annotations

import customtkinter as ctk

from aria2.core import config
import threading
from datetime import datetime

from aria2.services import (
    agent_service,
    chat_service,
    explore_service,
    project_service,
    tts_service,
)
from aria2.ui import theme
from aria2.ui.views import widgets as w
from aria2.ui.views.bubble import MessageBubble


class ChatView(ctk.CTkFrame):
    """A self-contained conversation surface (chat list + transcript + composer)
    scoped to a single project. Used standalone in the Chat tab (project
    'general') and embedded in the Projects tab (per selected project)."""

    def __init__(self, parent, app, project_id: str | None = None,
                 enable_drop: bool = True):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.project_id = project_id or "general"
        self._enable_drop = enable_drop
        self.chat_id: str | None = None
        self.active_run: str | None = None
        self._stream_bubble = None
        self._stream_text = ""
        self._unsubs = []

        self._build()
        self._subscribe()
        if self._enable_drop:
            self._enable_file_drop()  # optional OS drag-and-drop (windnd)

    def set_project(self, project_id: str):
        """Retarget this panel at another project (used by the Projects tab)."""
        self.project_id = project_id
        self.chat_id = None
        self.on_show()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        import tkinter as tk
        from aria2.core import config as _cfg
        _chat_w = max(180, min(480, int(_cfg.get("sidebar_chat_width", 256))))

        # tk.PanedWindow: native C-level smooth resize.
        paned = tk.PanedWindow(
            self, orient=tk.HORIZONTAL,
            sashwidth=5, sashrelief="flat",
            bg=theme.BORDER, bd=0, borderwidth=0,
            handlesize=0, sashpad=0,
        )
        paned.pack(fill="both", expand=True)
        self._paned_chat = paned

        left = ctk.CTkFrame(paned, width=_chat_w, fg_color=theme.SIDEBAR, corner_radius=0)
        left.pack_propagate(False)

        right_host = ctk.CTkFrame(paned, fg_color=theme.BG)
        paned.add(left,       minsize=180, width=_chat_w, stretch="never")
        paned.add(right_host, minsize=300,               stretch="always")

        paned.bind("<ButtonRelease-1>", lambda e: _cfg.set_key(
            "sidebar_chat_width",
            max(180, min(480, int(paned.sash_coord(0)[0])))))

        # Read-only project context (switch projects from the Projects tab).
        ctk.CTkLabel(left, text="PROJECT", font=theme.f(-3, "bold"),
                     text_color=theme.TEXT_FAINT).pack(anchor="w", padx=16, pady=(14, 0))
        self.project_label = ctk.CTkLabel(left, text="General", font=theme.f(1, "bold"),
                                          text_color=theme.TEXT, anchor="w")
        self.project_label.pack(fill="x", padx=16, pady=(0, 2))

        ctk.CTkFrame(left, height=1, fg_color=theme.BORDER).pack(
            fill="x", padx=12, pady=(10, 8))

        self._show_archived = False
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x", padx=12)
        ctk.CTkLabel(head, text="CHATS", font=theme.f(-3, "bold"),
                     text_color=theme.TEXT_FAINT).pack(side="left")
        w.ghost_button(head, "+ New", self._new_chat, width=60, height=26,
                       tooltip="Start a new chat in this project").pack(side="right")
        self.arch_btn = w.ghost_button(head, "Archive", self._toggle_archived_view,
                                       width=64, height=26, fg_color="transparent",
                                       tooltip="Show archived chats")
        self.arch_btn.pack(side="right", padx=4)

        self.search = ctk.CTkEntry(left, placeholder_text="Search chats…",
                                   fg_color=theme.SURFACE_2, border_width=0, height=30)
        self.search.pack(fill="x", padx=12, pady=(8, 6))
        self.search.bind("<KeyRelease>", lambda e: self._refresh_chat_list())

        self.chat_list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.chat_list.pack(fill="both", expand=True, padx=6)

        # Right: transcript + composer (inside the PanedWindow pane).
        right = ctk.CTkFrame(right_host, fg_color=theme.BG)
        right.pack(fill="both", expand=True)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(right, fg_color=theme.BG, height=48)
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        self.agent_menu = ctk.CTkOptionMenu(
            bar, values=["Assistant"], command=self._on_agent_change,
            fg_color=theme.SURFACE, button_color=theme.SURFACE_2,
            button_hover_color=theme.BORDER, font=theme.f(-1), width=160,
        )
        self.agent_menu.pack(side="left")
        w.ghost_button(bar, "⑂ Fork", self._fork, width=70, height=30,
                       tooltip="Branch this chat into a new conversation").pack(side="right")

        self.transcript = ctk.CTkScrollableFrame(right, fg_color=theme.BG)
        self.transcript.grid(row=1, column=0, sticky="nsew", padx=8)

        # Composer: a single thin rounded bar — icons + input + send all inline
        # on one row (Codex/Claude-Code style), so the field hugs the input.
        self._attachments: list[str] = []
        field = ctk.CTkFrame(right, fg_color=theme.SURFACE_2, corner_radius=14,
                             border_width=1, border_color=theme.BORDER)
        field.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 14))
        field.grid_columnconfigure(2, weight=1)  # the input column expands

        # Attachment chips span the top, collapsed when empty.
        self.attach_bar = ctk.CTkFrame(field, fg_color="transparent")
        self.attach_bar.grid(row=0, column=0, columnspan=6, sticky="ew", padx=8, pady=(6, 0))
        self.attach_bar.grid_remove()

        w.ghost_button(field, "📎", self._attach, width=32, height=32,
                       fg_color="transparent",
                       tooltip="Attach files (or paste an image)").grid(
            row=1, column=0, padx=(8, 0), pady=6)
        w.ghost_button(field, "🎤", self._voice_input, width=32, height=32,
                       fg_color="transparent", tooltip="Voice input").grid(
            row=1, column=1, pady=6)

        self.input = ctk.CTkTextbox(
            field, height=34, fg_color="transparent", font=theme.f(0),
            border_width=0, wrap="word", activate_scrollbars=False,
        )
        self.input.grid(row=1, column=2, sticky="ew", padx=8, pady=6)
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<KP_Enter>", self._on_return)   # numpad Enter
        self.input.bind("<Control-Return>", lambda e: self._send())
        self.input.bind("<Control-v>", self._on_paste, add="+")
        self.input.bind("<Control-V>", self._on_paste, add="+")
        self.input.bind("<KeyRelease>", self._autosize_input, add="+")
        self.input.bind("<KeyRelease>", self._save_draft, add="+")

        self.dry_chk = ctk.CTkCheckBox(field, text="Dry", font=theme.f(-2),
                                       width=20, checkbox_width=15, checkbox_height=15)
        self.dry_chk.grid(row=1, column=3, padx=4)
        w.add_tooltip(self.dry_chk, "Preview changes in a sandbox without applying them")
        w.ghost_button(field, "Explore", self._explore, width=64, height=30,
                       fg_color="transparent", tooltip="Run several strategies and compare"
                       ).grid(row=1, column=4, padx=2)
        self.send_btn = w.primary_button(field, "Send", self._send, width=72, height=32,
                                         tooltip="Send  ·  Enter  (Shift+Enter for newline)")
        self.send_btn.grid(row=1, column=5, padx=(2, 8), pady=6)

    # ── Composer ──────────────────────────────────────────────────────────────────

    def _save_draft(self, _event=None):
        if self.chat_id:
            from aria2.core import config as _cfg
            _cfg.set_key(f"draft_{self.project_id}", self.input.get("1.0", "end").strip())

    def _restore_draft(self):
        from aria2.core import config as _cfg
        draft = _cfg.get(f"draft_{self.project_id}", "")
        if draft:
            self.input.delete("1.0", "end")
            self.input.insert("1.0", draft)
            self._autosize_input()

    def _on_return(self, event):
        """Enter sends; Shift+Enter inserts a newline."""
        if event.state & 0x1:  # Shift held
            self.input.insert("insert", "\n")
            self._autosize_input()
            return "break"
        self._send()
        return "break"

    def _autosize_input(self, _event=None):
        """Grow from one line up to ~8, then scroll — the field tracks content."""
        try:
            lines = int(self.input.index("end-1c").split(".")[0])
        except Exception:
            lines = 1
        lines = max(1, min(8, lines))
        self.input.configure(height=22 * lines + 14)
        # Re-enable the scrollbar only once we hit the cap.
        try:
            self.input.configure(activate_scrollbars=lines >= 8)
        except Exception:
            pass

    # ── Attachments ──────────────────────────────────────────────────────────────

    def _attach(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames()
        if paths:
            self._attachments.extend(paths)
            self._render_attachments()

    def _on_paste(self, _event=None):
        """Ctrl+V: if the clipboard holds an image (or copied files), attach it;
        otherwise let the normal text paste proceed."""
        try:
            from PIL import ImageGrab
        except Exception:
            return None  # Pillow missing → default paste
        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            return None
        if data is None:
            return None
        # Windows: copied files come back as a list of paths.
        if isinstance(data, list):
            files = [f for f in data if isinstance(f, str)]
            if files:
                self._attachments.extend(files)
                self._render_attachments()
                return "break"
            return None
        # Otherwise it's a PIL image — save to a temp PNG and attach.
        try:
            import tempfile
            from pathlib import Path
            tmp = Path(tempfile.mkdtemp(prefix="aria2_paste_")) / "pasted.png"
            data.save(tmp, "PNG")
            self._attachments.append(str(tmp))
            self._render_attachments()
            return "break"
        except Exception:
            return None

    def _enable_file_drop(self):
        """Optional OS drag-and-drop of files onto the window (Windows, via
        windnd). No-op if the library isn't installed."""
        try:
            import windnd
        except Exception:
            return

        def _dropped(files):
            paths = [f.decode("utf-8", "replace") if isinstance(f, bytes) else f
                     for f in files]
            self.after(0, lambda: self._add_dropped(paths))

        try:
            windnd.hook_dropfiles(self.app, func=_dropped)
        except Exception:
            pass

    def _add_dropped(self, paths: list[str]):
        self._attachments.extend(paths)
        self.app.show("chat")
        self._render_attachments()

    def _render_attachments(self):
        for c in self.attach_bar.winfo_children():
            c.destroy()
        if not self._attachments:
            self.attach_bar.grid_remove()  # collapse the row when empty
            return
        self.attach_bar.grid()
        from pathlib import Path
        for i, p in enumerate(self._attachments):
            chip = ctk.CTkFrame(self.attach_bar, fg_color=theme.SURFACE, corner_radius=6)
            chip.pack(side="left", padx=(0, 6), pady=4)
            ctk.CTkLabel(chip, text=f"📎 {Path(p).name}", font=theme.f(-2),
                         text_color=theme.TEXT).pack(side="left", padx=(8, 2), pady=2)
            ctk.CTkButton(chip, text="✕", width=18, height=18, fg_color="transparent",
                          hover_color=theme.BORDER, text_color=theme.TEXT_FAINT,
                          font=theme.f(-2), command=lambda idx=i: self._remove_attachment(idx)
                          ).pack(side="left", padx=(0, 4))

    def _remove_attachment(self, idx: int):
        if 0 <= idx < len(self._attachments):
            self._attachments.pop(idx)
            self._render_attachments()

    # ── Data refresh ────────────────────────────────────────────────────────────

    def on_show(self):
        projects = project_service.list_projects(include_archived=True)
        active = next((p for p in projects if p["id"] == self.project_id), None)
        self.project_label.configure(text=active["name"] if active else "General")

        agents = agent_service.list_agents()
        self._agents = {a["name"]: a["id"] for a in agents}
        self.agent_menu.configure(values=list(self._agents))

        self._refresh_chat_list()
        if not self.chat_id:
            chats = chat_service.list_chats(self.project_id)
            if chats:
                self._open_chat(chats[0]["id"])
            else:
                self._new_chat()
        self._restore_draft()

    def _refresh_chat_list(self):
        for child in self.chat_list.winfo_children():
            child.destroy()
        query = self.search.get().strip() if hasattr(self, "search") else ""
        archived = getattr(self, "_show_archived", False)
        chats = chat_service.search_chats(self.project_id, query,
                                          include_archived=archived)
        if not chats:
            msg = ("No archived chats." if archived else
                   ("No matches." if query else "No chats yet."))
            ctk.CTkLabel(self.chat_list, text=msg, font=theme.f(-1),
                         text_color=theme.TEXT_FAINT).pack(anchor="w", padx=8, pady=8)
            return
        for c in chats:
            self._chat_row(c)

    def _chat_row(self, c: dict):
        active = c["id"] == self.chat_id
        row = ctk.CTkFrame(self.chat_list,
                           fg_color=theme.accent_soft() if active else "transparent",
                           corner_radius=6)
        row.pack(fill="x", pady=1)
        btn = ctk.CTkButton(
            row, text=("📌 " if c["pinned"] else "") + (c["title"] or "Untitled"),
            anchor="w", height=32, corner_radius=6, fg_color="transparent",
            hover_color=theme.HOVER, text_color=theme.TEXT if active else theme.TEXT_DIM,
            font=theme.f(-1), command=lambda cid=c["id"]: self._open_chat(cid),
        )
        btn.pack(side="left", fill="x", expand=True)
        menu_btn = ctk.CTkButton(
            row, text="⋯", width=24, height=28, fg_color="transparent",
            hover_color=theme.HOVER, text_color=theme.TEXT_FAINT, font=theme.f(0),
            command=lambda cc=c, b=None: self._chat_menu(cc))
        menu_btn.pack(side="right", padx=(0, 2))
        # Right-click anywhere on the row also opens the menu.
        for wdg in (row, btn):
            wdg.bind("<Button-3>", lambda e, cc=c: self._chat_menu(cc, e))

    def _chat_menu(self, c: dict, event=None):
        import tkinter as tk
        m = tk.Menu(self, tearoff=0, bg=theme.SURFACE_2, fg=theme.TEXT,
                    activebackground=theme.accent(), activeforeground="#ffffff",
                    bd=0)
        m.add_command(label="Rename", command=lambda: self._rename_chat(c))
        m.add_command(label="Unpin" if c["pinned"] else "Pin",
                      command=lambda: self._toggle_pin(c))
        m.add_command(label="Unarchive" if c.get("archived") else "Archive",
                      command=lambda: self._archive_chat(c))
        m.add_separator()
        m.add_command(label="Delete", command=lambda: self._delete_chat(c))
        try:
            if event is not None:
                m.tk_popup(event.x_root, event.y_root)
            else:
                x = self.winfo_pointerx()
                y = self.winfo_pointery()
                m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _toggle_pin(self, c: dict):
        chat_service.set_pinned(c["id"], not c["pinned"])
        self._refresh_chat_list()

    def _toggle_archived_view(self):
        self._show_archived = not self._show_archived
        self.arch_btn.configure(
            text="← Back" if self._show_archived else "Archive",
            fg_color=theme.accent_soft() if self._show_archived else "transparent")
        self._refresh_chat_list()

    def _archive_chat(self, c: dict):
        chat_service.archive_chat(c["id"], not c.get("archived"))
        if self.chat_id == c["id"] and not c.get("archived"):
            self.chat_id = None  # archived the open chat
        self._refresh_chat_list()
        if not self.chat_id and not self._show_archived:
            remaining = chat_service.list_chats(self.project_id)
            if remaining:
                self._open_chat(remaining[0]["id"])
            else:
                self._new_chat()

    def _rename_chat(self, c: dict):
        dlg = ctk.CTkInputDialog(text="New chat name:", title="Rename chat")
        name = dlg.get_input()
        if name and name.strip():
            chat_service.rename_chat(c["id"], name.strip())
            self._refresh_chat_list()

    def _delete_chat(self, c: dict):
        from tkinter import messagebox
        title = c.get("title") or "this chat"
        if not messagebox.askyesno(
                "Delete chat", f"Delete “{title}”?\nThis cannot be undone.",
                icon="warning", parent=self):
            return
        chat_service.delete_chat(c["id"])
        if self.chat_id == c["id"]:
            self.chat_id = None
        self._refresh_chat_list()
        if not self.chat_id:
            remaining = chat_service.list_chats(self.project_id)
            if remaining:
                self._open_chat(remaining[0]["id"])
            else:
                self._new_chat()

    # ── Actions ──────────────────────────────────────────────────────────────────

    def _on_agent_change(self, name: str):
        if self.chat_id:
            chat_service.set_agent(self.chat_id, self._agents[name])

    def _new_chat(self):
        agent_id = self._agents.get(self.agent_menu.get(), "assistant")
        chat = chat_service.create_chat(self.project_id, agent_id=agent_id)
        self._open_chat(chat["id"])

    def _open_chat(self, chat_id: str):
        self.chat_id = chat_id
        chat = chat_service.get_chat(chat_id)
        if chat and chat["agent_id"] in [v for v in self._agents.values()]:
            name = next((n for n, i in self._agents.items() if i == chat["agent_id"]), None)
            if name:
                self.agent_menu.set(name)
        self._refresh_chat_list()
        self._render_transcript()

    def _fork(self):
        if not self.chat_id:
            return
        new = chat_service.fork(self.chat_id)
        self._open_chat(new["id"])

    _PAGE = 50  # render only the most recent N messages when opening a chat

    def _render_transcript(self):
        for child in self.transcript.winfo_children():
            child.destroy()
        if not self.chat_id:
            return
        self._empty_shown = False
        msgs = chat_service.list_messages(self.chat_id, limit=self._PAGE)
        if not msgs:
            self._render_empty_state()
            self._empty_shown = True
            return
        if len(msgs) >= self._PAGE:
            ctk.CTkLabel(self.transcript, text="— showing recent messages —",
                         font=theme.f(-2), text_color=theme.TEXT_FAINT).pack(pady=6)
        for m in msgs:
            self._add_bubble(m["role"], _blocks_to_text(m["content"]),
                             ts=m.get("created_at"))

    def _render_empty_state(self):
        """Friendly first-run / empty-chat guidance, with onboarding when no
        provider credentials are configured yet."""
        wrap = ctk.CTkFrame(self.transcript, fg_color="transparent")
        wrap.pack(expand=True, pady=60)
        ctk.CTkLabel(wrap, text="✦", font=(theme.FONT, 40),
                     text_color=theme.accent()).pack()
        if not config.provider_configured():
            ctk.CTkLabel(wrap, text="Welcome to ARIA", font=theme.f(8, "bold"),
                         text_color=theme.TEXT).pack(pady=(8, 2))
            ctk.CTkLabel(wrap, text="Add an AI provider key to start chatting.",
                         font=theme.f(0), text_color=theme.TEXT_DIM).pack()
            w.primary_button(wrap, "Open Settings → Providers",
                             lambda: self.app.show("settings"), height=38).pack(pady=12)
        else:
            ctk.CTkLabel(wrap, text="Start the conversation",
                         font=theme.f(6, "bold"), text_color=theme.TEXT).pack(pady=(8, 2))
            ctk.CTkLabel(wrap, text="Ask anything, attach a project folder, or press "
                                    "Ctrl+K for commands.",
                         font=theme.f(0), text_color=theme.TEXT_DIM, wraplength=420,
                         justify="center").pack()

    def _add_bubble(self, role: str, text: str, ts: int | None = None):
        when = ""
        if ts:
            try:
                when = datetime.fromtimestamp(ts / 1000).strftime("%H:%M")
            except Exception:
                when = ""
        b = MessageBubble(self.transcript, role, when=when)
        b.pack(fill="x", padx=12, pady=4)
        if text:
            b.set_markdown(text)
        self.transcript._parent_canvas.yview_moveto(1.0)
        return b

    def _send(self):
        from pathlib import Path
        text = self.input.get("1.0", "end").strip()
        attachments = list(self._attachments)
        if (not text and not attachments) or not self.chat_id or self.active_run:
            return
        self.input.delete("1.0", "end")
        self._autosize_input()  # shrink back to one line
        from aria2.core import config as _cfg
        _cfg.set_key(f"draft_{self.project_id}", "")
        if getattr(self, "_empty_shown", False):  # clear the welcome card
            for child in self.transcript.winfo_children():
                child.destroy()
            self._empty_shown = False
        self._dry = bool(self.dry_chk.get())
        now_ms = __import__("time").time() * 1000
        shown = text
        if attachments:
            shown = (shown + "\n" if shown else "") + " ".join(
                f"📎 {Path(a).name}" for a in attachments)
        if self._dry:
            shown += "   · dry run"
        self._add_bubble("user", shown, ts=now_ms)
        self._stream_text = ""
        self._stream_bubble = self._add_bubble("assistant", "", ts=now_ms)
        self._stream_bubble.set_note("…")
        self.send_btn.configure(text="Stop", command=self._stop)
        self.active_run = chat_service.send_async(
            self.chat_id, text, dry_run=self._dry, attachments=attachments)
        self._attachments = []
        self._render_attachments()

    def _stop(self):
        if self.active_run:
            chat_service.cancel(self.active_run)

    # ── Streaming events ──────────────────────────────────────────────────────────

    def _subscribe(self):
        self._unsubs.append(self.app.on_event("run.token", self._on_token))
        self._unsubs.append(self.app.on_event("run.tool", self._on_tool))
        self._unsubs.append(self.app.on_event("run.done", self._on_done))
        self._unsubs.append(self.app.on_event("run.error", self._on_error))

    def _on_token(self, payload):
        if payload.get("run_id") != self.active_run or not self._stream_bubble:
            return
        delta = payload.get("text", "")
        if not self._stream_text:  # clear the "…" placeholder on first token
            self._stream_bubble.set_note("")
        self._stream_text += delta
        self._stream_bubble.append(delta)
        self.transcript._parent_canvas.yview_moveto(1.0)

    def _on_tool(self, payload):
        if payload.get("run_id") != self.active_run:
            return
        if payload.get("phase") == "call" and self._stream_bubble:
            self._stream_bubble.append(f"\n  ⚙ using {payload.get('name')}…\n")

    def _on_done(self, payload):
        if payload.get("run_id") != self.active_run:
            return
        run_id = self.active_run
        # Re-render the streamed text once with markdown formatting.
        if self._stream_bubble and self._stream_text:
            self._stream_bubble.set_markdown(self._stream_text)
        self._finish()
        # The user + assistant bubbles were already rendered live during the
        # turn — no need to destroy and rebuild the whole transcript (that was
        # O(n) widgets per turn and caused flicker). Just refresh the sidebar.
        self._refresh_chat_list()
        if getattr(self, "_dry", False):
            self._show_dry_run(run_id)
            self._dry = False
        else:
            tts_service.speak(self._stream_text)  # speak reply if TTS enabled

    def _voice_input(self):
        self.input.delete("1.0", "end")
        self.input.insert("1.0", "🎤 listening…")

        def worker():
            res = tts_service.listen()
            text = res.get("text", "")
            self.after(0, lambda: (self.input.delete("1.0", "end"),
                                   self.input.insert("1.0", text or f"[{res.get('error','no speech')}]")))

        threading.Thread(target=worker, daemon=True).start()

    def _show_dry_run(self, run_id: str):
        diff = chat_service.dry_run_diff(run_id)
        if not diff:
            return
        row = ctk.CTkFrame(self.transcript, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=4)
        panel = ctk.CTkFrame(row, fg_color=theme.SURFACE, corner_radius=theme.RADIUS,
                             border_width=1, border_color=theme.WARN)
        panel.pack(anchor="w", fill="x", padx=(0, 40))
        ctk.CTkLabel(panel, text="🔎 Predicted changes (dry run — nothing applied yet)",
                     font=theme.f(-1, "bold"), text_color=theme.WARN).pack(
            anchor="w", padx=12, pady=(8, 2))
        if not diff.get("has_changes"):
            ctk.CTkLabel(panel, text="No file changes or commands.", font=theme.f(-1),
                         text_color=theme.TEXT_DIM).pack(anchor="w", padx=12, pady=(0, 8))
            return
        for f in diff.get("files", []):
            ctk.CTkLabel(panel, text=f"  {f['status']}: {f['path']}  "
                                     f"({f['old_bytes']}→{f['new_bytes']} bytes)",
                         font=theme.mono(-2), text_color=theme.TEXT, anchor="w").pack(
                anchor="w", padx=12)
        for cmd in diff.get("commands", []):
            ctk.CTkLabel(panel, text=f"  would run: {cmd[:80]}", font=theme.mono(-2),
                         text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w", padx=12)
        btns = ctk.CTkFrame(panel, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=8)
        w.primary_button(btns, "Commit", lambda: self._commit_dry(run_id, panel, False),
                         width=90, height=30).pack(side="left")
        if chat_service.dry_run_is_git(run_id):
            w.ghost_button(btns, "Commit + git", lambda: self._commit_dry(run_id, panel, True),
                           width=110, height=30).pack(side="left", padx=8)
        w.ghost_button(btns, "Discard", lambda: self._discard_dry(run_id, panel),
                       width=90, height=30).pack(side="left", padx=8)

    def _commit_dry(self, run_id, panel, git_commit):
        res = chat_service.commit_dry_run(run_id, git_commit=git_commit)
        for x in panel.winfo_children():
            x.destroy()
        msg = f"✓ Committed {len(res.get('committed', []))} file(s)."
        git = res.get("git")
        if git and git.get("committed_sha"):
            msg += f"  git {git['committed_sha']}"
        elif git and git.get("error"):
            msg += f"  (git: {git['error'][:50]})"
        ctk.CTkLabel(panel, text=msg, font=theme.f(-1, "bold"),
                     text_color=theme.SUCCESS).pack(anchor="w", padx=12, pady=8)

    def _discard_dry(self, run_id, panel):
        chat_service.discard_dry_run(run_id)
        for x in panel.winfo_children():
            x.destroy()
        ctk.CTkLabel(panel, text="Discarded — nothing was changed.", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=12, pady=8)

    def _on_error(self, payload):
        if payload.get("run_id") != self.active_run:
            return
        if self._stream_bubble:
            self._stream_bubble.set_note(f"⚠ {payload.get('error', 'error')}")
        self._finish()

    def _finish(self):
        self.active_run = None
        self._stream_bubble = None
        self.send_btn.configure(text="Send", command=self._send)

    def _explore(self):
        base = self.input.get("1.0", "end").strip()
        _ExploreDialog(self, self.project_id, base)


class _ExploreDialog(ctk.CTkToplevel):
    """Run several strategies as parallel dry runs and commit the best one."""

    def __init__(self, parent, project_id: str, base_prompt: str):
        super().__init__(parent)
        self.project_id = project_id
        self.base_prompt = base_prompt
        self.run_ids: list[str] = []
        self.title("Counterfactual explorer")
        self.geometry("720x620")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)

        ctk.CTkLabel(self, text="Explore strategies in parallel (dry runs)",
                     font=theme.f(2, "bold"), text_color=theme.TEXT).pack(
            anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(self, text="One variant per line as  label :: instruction  "
                                "(or just an instruction). Each runs in its own sandbox; "
                                "commit the winner.", font=theme.f(-1),
                     text_color=theme.TEXT_DIM, wraplength=680, justify="left").pack(
            anchor="w", padx=18)
        self.variants = ctk.CTkTextbox(self, height=110, fg_color=theme.SURFACE_2,
                                       font=theme.f(0), wrap="word")
        self.variants.pack(fill="x", padx=18, pady=8)
        seed = base_prompt or "do the task"
        self.variants.insert("1.0", f"Cautious :: {seed} — minimal changes\n"
                                    f"Thorough :: {seed} — comprehensive\n")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18)
        w.primary_button(row, "Run variants", self._run, width=130).pack(side="left")
        self.status = ctk.CTkLabel(row, text="", font=theme.f(-1), text_color=theme.TEXT_DIM)
        self.status.pack(side="left", padx=10)

        self.results = ctk.CTkScrollableFrame(self, fg_color=theme.BG)
        self.results.pack(fill="both", expand=True, padx=14, pady=12)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _parse(self) -> list[dict]:
        out = []
        for line in self.variants.get("1.0", "end").splitlines():
            line = line.strip()
            if not line:
                continue
            if "::" in line:
                label, prompt = line.split("::", 1)
                out.append({"label": label.strip(), "prompt": prompt.strip()})
            else:
                out.append({"label": line[:24], "prompt": line})
        return out

    def _run(self):
        variants = self._parse()
        if not variants:
            self.status.configure(text="Add at least one variant", text_color=theme.DANGER)
            return
        self.status.configure(text=f"Running {len(variants)} variants…",
                              text_color=theme.TEXT_DIM)
        for c in self.results.winfo_children():
            c.destroy()

        def worker():
            res = explore_service.run_variants(self.project_id, self.base_prompt, variants)
            self.after(0, lambda: self._show(res))

        threading.Thread(target=worker, daemon=True).start()

    def _show(self, results: list[dict]):
        self.run_ids = [r["run_id"] for r in results]
        self.status.configure(text=f"{len(results)} variants — pick one to commit",
                              text_color=theme.SUCCESS)
        for r in results:
            diff = r.get("diff") or {}
            nfiles = len(diff.get("files", []))
            ncmd = len(diff.get("commands", []))
            card = ctk.CTkFrame(self.results, fg_color=theme.SURFACE, corner_radius=theme.RADIUS,
                                border_width=1, border_color=theme.BORDER)
            card.pack(fill="x", pady=4)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(head, text=f"{r['label']}  ·  {r['status']}  ·  "
                                    f"${r['cost_usd']:.4f}  ·  {nfiles} files, {ncmd} cmds",
                         font=theme.f(-1, "bold"), text_color=theme.accent(),
                         anchor="w").pack(side="left")
            ctk.CTkButton(head, text="Commit this", width=100, height=26,
                          fg_color=theme.accent(),
                          command=lambda rid=r["run_id"]: self._commit(rid)).pack(side="right")
            ctk.CTkLabel(card, text=(r.get("text") or "")[:300], font=theme.f(-1),
                         text_color=theme.TEXT, wraplength=640, justify="left",
                         anchor="w").pack(anchor="w", padx=10, pady=(2, 4))
            for f in diff.get("files", [])[:6]:
                ctk.CTkLabel(card, text=f"  {f['status']}: {f['path']}", font=theme.mono(-2),
                             text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w", padx=10)
            ctk.CTkLabel(card, text="", height=2).pack()

    def _commit(self, run_id: str):
        res = explore_service.commit_variant(run_id, self.run_ids)
        self.run_ids = []  # committed one, discarded the rest
        self.status.configure(
            text=f"Committed {len(res.get('committed', []))} file(s) from the chosen variant.",
            text_color=theme.SUCCESS)
        for c in self.results.winfo_children():
            c.destroy()

    def _close(self):
        if self.run_ids:
            explore_service.discard_all(self.run_ids)  # clean up overlays
        self.destroy()


def _blocks_to_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if not isinstance(b, dict):
            parts.append(str(b))
        elif b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "image":
            parts.append("🖼 (image attachment)")
        elif b.get("type") == "tool_use":
            parts.append(f"⚙ {b.get('name')}")
        elif b.get("type") == "tool_result":
            parts.append("↳ (tool result)")
    return "\n".join(p for p in parts if p)
