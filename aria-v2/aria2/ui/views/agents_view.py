"""ui/views/agents_view.py - Agent builder (create/edit/delete + scoping)."""

from __future__ import annotations

import customtkinter as ctk

from aria2.services import agent_service, routing_service
from aria2.ui import theme
from aria2.ui.views import widgets as w

_SCOPES = ["project", "user", "agent", "none"]
_PROVIDERS = ["(default)", "claude", "openai", "local", "grok", "gemini"]


class AgentsView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.selected: str | None = None
        w.header(self, "Agents", "Build specialist agents with scoped tools and memory.")

        from aria2.ui.views.paned_view import make_paned
        left, right = make_paned(self, "sidebar_agents_width",
                                 default_w=240, min_w=160, max_w=460)
        w.primary_button(left, "+  New agent", self._new, height=34).pack(
            fill="x", padx=10, pady=10)
        self.list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list.pack(fill="both", expand=True)

        self.form = w.card(right)
        self.form.pack(fill="both", expand=True)
        self._build_form()

    def on_show(self):
        self._refresh_list()
        agents = agent_service.list_agents()
        if agents and not self.selected:
            self._select(agents[0]["id"])

    def _refresh_list(self):
        for c in self.list.winfo_children():
            c.destroy()
        for a in agent_service.list_agents():
            active = a["id"] == self.selected
            ctk.CTkButton(
                self.list, text=f"{a['icon']}  {a['name']}", anchor="w", height=34,
                fg_color=theme.SURFACE_2 if active else "transparent",
                hover_color=theme.SURFACE_2, text_color=theme.TEXT if active else theme.TEXT_DIM,
                font=theme.f(-1), command=lambda i=a["id"]: self._select(i),
            ).pack(fill="x", padx=6, pady=1)

    def _build_form(self):
        pad = {"padx": 18, "pady": (6, 0)}
        self.name_f, self.name_e = w.labeled_entry(self.form, "Name")
        self.name_f.pack(fill="x", **pad)
        self.icon_f, self.icon_e = w.labeled_entry(self.form, "Icon (emoji)")
        self.icon_f.pack(fill="x", **pad)
        self.desc_f, self.desc_e = w.labeled_entry(self.form, "Description")
        self.desc_f.pack(fill="x", **pad)

        ctk.CTkLabel(self.form, text="System prompt", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=18, pady=(10, 2))
        self.system = ctk.CTkTextbox(self.form, height=170, fg_color=theme.SURFACE_2,
                                     font=theme.f(0), wrap="word")
        self.system.pack(fill="x", padx=18)

        row = ctk.CTkFrame(self.form, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=10)
        ctk.CTkLabel(row, text="Memory scope", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left")
        self.scope = ctk.CTkOptionMenu(row, values=_SCOPES, width=120,
                                       fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.scope.pack(side="left", padx=8)
        ctk.CTkLabel(row, text="Provider", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(side="left", padx=(16, 0))
        self.provider = ctk.CTkOptionMenu(row, values=_PROVIDERS, width=120,
                                          fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.provider.pack(side="left", padx=8)

        btns = ctk.CTkFrame(self.form, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=14)
        w.primary_button(btns, "Save", self._save, width=100).pack(side="left")
        w.ghost_button(btns, "Delete", self._delete, width=90).pack(side="left", padx=8)
        self.status = ctk.CTkLabel(btns, text="", font=theme.f(-1), text_color=theme.SUCCESS)
        self.status.pack(side="left", padx=8)

        ctk.CTkLabel(self.form, text="Learned performance (from delegated runs)",
                     font=theme.f(-1, "bold"), text_color=theme.accent()).pack(
            anchor="w", padx=18, pady=(6, 2))
        self.perf = ctk.CTkFrame(self.form, fg_color="transparent")
        self.perf.pack(fill="x", padx=18, pady=(0, 10))

    def _new(self):
        self.selected = None
        for e in (self.name_e, self.icon_e, self.desc_e):
            e.delete(0, "end")
        self.icon_e.insert(0, "✦")
        self.system.delete("1.0", "end")
        self.scope.set("project")
        self.provider.set("(default)")
        self.status.configure(text="New agent")
        for c in self.perf.winfo_children():
            c.destroy()

    def _select(self, agent_id: str):
        self.selected = agent_id
        a = agent_service.get(agent_id)
        if not a:
            return
        self.name_e.delete(0, "end"); self.name_e.insert(0, a["name"])
        self.icon_e.delete(0, "end"); self.icon_e.insert(0, a["icon"])
        self.desc_e.delete(0, "end"); self.desc_e.insert(0, a["description"])
        self.system.delete("1.0", "end"); self.system.insert("1.0", a["system_prompt"])
        self.scope.set(a["memory_scope"])
        self.provider.set(a["provider"] or "(default)")
        self.status.configure(text="builtin" if a["builtin"] else "")
        self._refresh_perf(agent_id)
        self._refresh_list()

    def _refresh_perf(self, agent_id: str):
        for c in self.perf.winfo_children():
            c.destroy()
        report = routing_service.agent_report(agent_id)
        if not report:
            ctk.CTkLabel(self.perf, text="No delegated runs yet — stats appear as this "
                                         "agent is used.", font=theme.f(-2),
                         text_color=theme.TEXT_FAINT, wraplength=420, justify="left").pack(
                anchor="w")
            return
        for r in report:
            line = (f"{r['task_type']:<10}  {r['success_rate']:.0%} success  ·  "
                    f"{r['runs']} runs  ·  ${r['avg_cost']:.3f}/run")
            ctk.CTkLabel(self.perf, text=line, font=theme.mono(-2),
                         text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w")

    def _save(self):
        provider = self.provider.get()
        data = {
            "name": self.name_e.get().strip() or "Agent",
            "icon": self.icon_e.get().strip() or "✦",
            "description": self.desc_e.get().strip(),
            "system_prompt": self.system.get("1.0", "end").strip(),
            "memory_scope": self.scope.get(),
            "provider": None if provider == "(default)" else provider,
        }
        if self.selected:
            agent_service.update(self.selected, data)
        else:
            created = agent_service.create(
                data["name"], data["system_prompt"], icon=data["icon"],
                description=data["description"], memory_scope=data["memory_scope"],
                provider=data["provider"],
            )
            self.selected = created["id"]
        self.status.configure(text="")
        self.app.toast("Agent saved", "success")
        self._refresh_list()

    def _delete(self):
        if not self.selected:
            return
        res = agent_service.delete(self.selected)
        if res.get("error"):
            self.status.configure(text=res["error"], text_color=theme.DANGER)
            return
        self.selected = None
        self._new()
        self._refresh_list()
