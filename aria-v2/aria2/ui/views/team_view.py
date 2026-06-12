"""ui/views/team_view.py - Project Leader: watch leader runs + their task graph.

Lists recent "/team" (Project Leader) runs; selecting one shows its task graph —
each task's specialist role, status, dependencies, and output preview. Updates
live as orchestrations progress.
"""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

from aria2.services import orchestration_service, run_service
from aria2.ui import theme
from aria2.ui.views import widgets as w

_STATUS = {
    "done": (theme.SUCCESS, "✓"), "running": (theme.WARN, "…"),
    "failed": (theme.DANGER, "✗"), "pending": (theme.TEXT_FAINT, "•"),
    "blocked": (theme.TEXT_FAINT, "⏸"),
    "awaiting_approval": (theme.WARN, "⏸"), "cancelled": (theme.TEXT_FAINT, "⊘"),
}


class TeamView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.selected: str | None = None
        w.header(self, "Team",
                 "Project Leader runs. Start one in any chat: /team <goal>")

        from aria2.ui.views.paned_view import make_paned
        left, right = make_paned(self, "sidebar_runs_width",
                                 default_w=360, min_w=220, max_w=560)
        lc = w.card(left)
        lc.pack(fill="both", expand=True)
        ctk.CTkLabel(lc, text="Leader runs", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.list = ctk.CTkScrollableFrame(lc, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=6, pady=6)

        rc = w.card(right)
        rc.pack(fill="both", expand=True)
        ctk.CTkLabel(rc, text="Task graph", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.detail = ctk.CTkScrollableFrame(rc, fg_color="transparent")
        self.detail.pack(fill="both", expand=True, padx=6, pady=6)

        # Live refresh while orchestrations run.
        self._unsubs = [
            self.app.on_event("orchestration.done", lambda p: self._on_event()),
            self.app.on_event("orchestration.plan", lambda p: self._on_event()),
        ]

    def destroy(self):
        for u in getattr(self, "_unsubs", []):
            try:
                u()
            except Exception:
                pass
        super().destroy()

    def on_show(self):
        self._refresh_list()
        if self.selected:
            self._show_tasks(self.selected)

    def _on_event(self):
        self._refresh_list()
        if self.selected:
            self._show_tasks(self.selected)

    def _refresh_list(self):
        for c in self.list.winfo_children():
            c.destroy()
        runs = run_service.list_runs(limit=60, kind="leader")
        if not runs:
            ctk.CTkLabel(
                self.list, text="No team runs yet.\nType  /team <goal>  in a chat.",
                font=theme.f(-1), text_color=theme.TEXT_DIM, justify="left").pack(
                anchor="w", padx=10, pady=20)
            return
        for r in runs:
            color, _ = _STATUS.get(r["status"], (theme.TEXT_DIM, "•"))
            when = datetime.fromtimestamp(r["started_at"] / 1000).strftime("%m-%d %H:%M")
            title = (r.get("title") or "Team run")[:34]
            row = ctk.CTkButton(
                self.list, text=f"{when}  {title}\n{r['status']}", anchor="w",
                height=44,
                fg_color=theme.SURFACE_2 if r["id"] == self.selected else "transparent",
                hover_color=theme.SURFACE_2, text_color=color, font=theme.f(-1),
                command=lambda i=r["id"]: self._select(i))
            row.pack(fill="x", padx=4, pady=1)

    def _select(self, leader_run_id: str):
        self.selected = leader_run_id
        self._refresh_list()
        self._show_tasks(leader_run_id)

    def _show_tasks(self, leader_run_id: str):
        for c in self.detail.winfo_children():
            c.destroy()
        tasks = orchestration_service.tasks_for(leader_run_id)
        if not tasks:
            ctk.CTkLabel(self.detail, text="Planning…", font=theme.f(-1),
                         text_color=theme.TEXT_DIM).pack(pady=20)
            return
        for t in tasks:
            color, icon = _STATUS.get(t["status"], (theme.TEXT_DIM, "•"))
            card = ctk.CTkFrame(self.detail, fg_color=theme.SURFACE_2,
                                corner_radius=10, border_width=1, border_color=theme.BORDER)
            card.pack(fill="x", padx=6, pady=4)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8, 2))
            deps = t.get("depends_on") or "[]"
            dep_s = f"  ← {deps}" if deps not in ("[]", "", None) else ""
            rev = t.get("revisions") or 0
            rev_s = f"  ↻{rev}" if rev else ""
            risk_s = "  ⚠" if (t.get("risk") or "") == "high" else ""
            ctk.CTkLabel(top, text=f"{icon}  {t['ordinal']}. {t['title']}{risk_s}",
                         font=theme.f(0, "bold"), text_color=color, anchor="w").pack(side="left")
            ctk.CTkLabel(top, text=f"[{t.get('role','')}]{rev_s}{dep_s}", font=theme.f(-2),
                         text_color=theme.TEXT_FAINT).pack(side="right")
            preview = (t.get("output") or "")[:280]
            if preview:
                ctk.CTkLabel(card, text=preview, font=theme.f(-2),
                             text_color=theme.TEXT_DIM, wraplength=520, justify="left",
                             anchor="w").pack(anchor="w", padx=12, pady=(0, 8))
