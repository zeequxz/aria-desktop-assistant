"""ui/views/automations_view.py - Triggers (scheduled tasks today)."""

from __future__ import annotations

import customtkinter as ctk

from aria2.services import (
    agent_service,
    ambient_service,
    automation_service,
    project_service,
)
from aria2.ui import theme
from aria2.ui.views import widgets as w

_INTERVALS = ["hourly", "daily", "weekly"]


class AutomationsView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        w.header(self, "Automations", "Triggers fire an agent on a schedule; ARIA also "
                                      "proposes automations from what it sees you do.")

        # Ambient proposals (learned from your activity).
        self.proposals = ctk.CTkFrame(self, fg_color="transparent")
        self.proposals.pack(fill="x", padx=24, pady=(0, 4))

        from aria2.ui.views.paned_view import make_paned
        left_pane, right_pane = make_paned(self, "sidebar_automations_width",
                                           default_w=380, min_w=240, max_w=600)
        list_card = w.card(left_pane)
        list_card.pack(fill="both", expand=True)
        ctk.CTkLabel(list_card, text="Triggers", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.list = ctk.CTkScrollableFrame(list_card, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=6, pady=6)

        self.form = w.card(right_pane)
        self.form.pack(fill="both", expand=True)
        self._build_form()

    def on_show(self):
        projects = project_service.list_projects()
        self._projects = {p["name"]: p["id"] for p in projects}
        agents = agent_service.list_agents()
        self._agents = {a["name"]: a["id"] for a in agents}
        self.project_menu.configure(values=list(self._projects))
        self.agent_menu.configure(values=list(self._agents))
        self._refresh()
        self._refresh_proposals()

    def _refresh_proposals(self):
        for c in self.proposals.winfo_children():
            c.destroy()
        props = ambient_service.list_proposals("pending")
        if not props:
            return
        ctk.CTkLabel(self.proposals, text="✨ Suggestions (learned automations & agent improvements)",
                     font=theme.f(-1, "bold"), text_color=theme.accent()).pack(anchor="w", pady=(4, 2))
        for p in props:
            card = ctk.CTkFrame(self.proposals, fg_color=theme.SURFACE_2, corner_radius=8)
            card.pack(fill="x", pady=3)
            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=10, pady=6)
            ctk.CTkLabel(left, text=f"{p['title']}  ·  {p['confidence']:.0%}", font=theme.f(-1, "bold"),
                         text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(left, text=p["rationale"], font=theme.f(-2), text_color=theme.TEXT_DIM,
                         wraplength=560, justify="left", anchor="w").pack(anchor="w")
            ctk.CTkButton(card, text="Accept", width=70, height=28, fg_color=theme.accent(),
                          command=lambda i=p["id"]: self._accept(i)).pack(side="right", padx=4, pady=6)
            ctk.CTkButton(card, text="Dismiss", width=70, height=28, fg_color="transparent",
                          hover_color=theme.BORDER, text_color=theme.TEXT_DIM,
                          command=lambda i=p["id"]: self._dismiss(i)).pack(side="right", pady=6)

    def _accept(self, pid):
        ambient_service.accept_proposal(pid)
        self._refresh_proposals()
        self._refresh()

    def _dismiss(self, pid):
        ambient_service.dismiss_proposal(pid)
        self._refresh_proposals()

    def _refresh(self):
        for c in self.list.winfo_children():
            c.destroy()
        triggers = automation_service.list_triggers()
        if not triggers:
            ctk.CTkLabel(self.list, text="No triggers yet.", font=theme.f(-1),
                         text_color=theme.TEXT_FAINT).pack(anchor="w", padx=8, pady=8)
        for t in triggers:
            row = ctk.CTkFrame(self.list, fg_color=theme.SURFACE_2, corner_radius=6)
            row.pack(fill="x", pady=3, padx=2)
            txt = f"{'🟢' if t['enabled'] else '⚪'}  {t['name']}  ·  {t['kind']}"
            ctk.CTkLabel(row, text=txt, font=theme.f(-1), text_color=theme.TEXT,
                         anchor="w").pack(side="left", padx=8, pady=6)
            ctk.CTkButton(row, text="Run", width=46, height=26, fg_color=theme.accent(),
                          command=lambda i=t["id"]: automation_service.fire(i)).pack(
                side="right", padx=4, pady=4)
            ctk.CTkButton(row, text="✕", width=28, height=26, fg_color="transparent",
                          hover_color=theme.BORDER, text_color=theme.TEXT_FAINT,
                          command=lambda i=t["id"]: self._delete(i)).pack(side="right")

    def _build_form(self):
        ctk.CTkLabel(self.form, text="New trigger", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=16, pady=(10, 4))
        self.name_f, self.name_e = w.labeled_entry(self.form, "Name")
        self.name_f.pack(fill="x", padx=16, pady=4)

        kind_row = ctk.CTkFrame(self.form, fg_color="transparent")
        kind_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(kind_row, text="When", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.kind = ctk.CTkOptionMenu(kind_row, values=["schedule", "file", "webhook"],
                                      width=120, command=lambda *_: self._on_kind(),
                                      fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.kind.pack(side="left", padx=8)

        # Schedule fields.
        self.sched_row = ctk.CTkFrame(self.form, fg_color="transparent")
        self.sched_row.pack(fill="x", padx=16, pady=4)
        self.interval = ctk.CTkOptionMenu(self.sched_row, values=_INTERVALS, width=110,
                                          fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.interval.pack(side="left")
        self.time_f, self.time_e = w.labeled_entry(self.sched_row, "At (HH:MM)", "09:00")
        self.time_f.pack(side="left", padx=8)

        # File field.
        self.file_row = ctk.CTkFrame(self.form, fg_color="transparent")
        self.path_f, self.path_e = w.labeled_entry(self.file_row, "Watch path (file or folder)")
        self.path_f.pack(side="left", fill="x", expand=True)
        w.ghost_button(self.file_row, "Browse", self._browse, width=80, height=30).pack(
            side="left", padx=(8, 0), pady=(18, 0))

        row2 = ctk.CTkFrame(self.form, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=4)
        self.project_menu = ctk.CTkOptionMenu(row2, values=["General"], width=120,
                                              fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.project_menu.pack(side="left")
        self.agent_menu = ctk.CTkOptionMenu(row2, values=["Assistant"], width=120,
                                            fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.agent_menu.pack(side="left", padx=8)

        ctk.CTkLabel(self.form, text="Prompt", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=16, pady=(8, 2))
        self.prompt = ctk.CTkTextbox(self.form, height=120, fg_color=theme.SURFACE_2,
                                     font=theme.f(0), wrap="word")
        self.prompt.pack(fill="x", padx=16)

        btns = ctk.CTkFrame(self.form, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=12)
        w.primary_button(btns, "Create", self._create, width=100).pack(side="left")
        self.status = ctk.CTkLabel(btns, text="", font=theme.f(-1), text_color=theme.SUCCESS)
        self.status.pack(side="left", padx=10)

        self.hook_info = ctk.CTkLabel(self.form, text="", font=theme.mono(-2),
                                      text_color=theme.TEXT_DIM, wraplength=520, justify="left")
        self.hook_info.pack(anchor="w", padx=16, pady=(0, 8))
        self._on_kind()

    def _on_kind(self):
        """Show only the fields relevant to the selected trigger kind."""
        kind = self.kind.get()
        self.sched_row.pack_forget()
        self.file_row.pack_forget()
        if kind == "schedule":
            self.sched_row.pack(fill="x", padx=16, pady=4, after=self.kind.master)
        elif kind == "file":
            self.file_row.pack(fill="x", padx=16, pady=4, after=self.kind.master)
        # webhook: no extra fields; URL is shown after creation.

    def _browse(self):
        from tkinter import filedialog
        path = filedialog.askdirectory()
        if path:
            self.path_e.delete(0, "end")
            self.path_e.insert(0, path)

    def _create(self):
        name = self.name_e.get().strip()
        prompt = self.prompt.get("1.0", "end").strip()
        kind = self.kind.get()
        if not name or not prompt:
            self.status.configure(text="Name + prompt required", text_color=theme.DANGER)
            return
        cfg: dict = {}
        if kind == "schedule":
            cfg = {"interval": self.interval.get(), "at": self.time_e.get().strip()}
        elif kind == "file":
            path = self.path_e.get().strip()
            if not path:
                self.status.configure(text="Watch path required", text_color=theme.DANGER)
                return
            cfg = {"path": path}
        t = automation_service.create(
            name, kind, prompt,
            project_id=self._projects.get(self.project_menu.get(), "general"),
            agent_id=self._agents.get(self.agent_menu.get(), "assistant"),
            config_obj=cfg,
        )
        self.status.configure(text="")
        self.app.toast("Trigger created", "success")
        if kind == "webhook":
            self.hook_info.configure(
                text=f"POST to:  {automation_service.webhook_url(t)}\n"
                     "(enable the webhook server in Settings → Engine & safety)")
        else:
            self.hook_info.configure(text="")
        self.name_e.delete(0, "end")
        self.prompt.delete("1.0", "end")
        self._refresh()

    def _delete(self, tid):
        automation_service.delete(tid)
        self._refresh()
