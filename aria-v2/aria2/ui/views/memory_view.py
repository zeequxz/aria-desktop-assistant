"""ui/views/memory_view.py - Inspect, contest, and correct what ARIA believes.

Lists memories for a scope, shows each fact's confidence and review flag, and
exposes the provenance moat: select a fact to see the run that produced it and
the chain it was derived from, then pin / retract / supersede it. Retraction
flags everything derived from the fact for review.
"""

from __future__ import annotations

import customtkinter as ctk

from aria2.services import memory_service, project_service
from aria2.ui import theme
from aria2.ui.views import widgets as w


class MemoryView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.selected: str | None = None
        w.header(self, "Memory", "Everything ARIA believes — inspectable, correctable, retractable.")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=24)
        ctk.CTkLabel(bar, text="Scope", font=theme.f(-1), text_color=theme.TEXT_DIM).pack(side="left")
        self.scope_menu = ctk.CTkOptionMenu(
            bar, values=["user", "project"], width=120, command=lambda *_: self._refresh(),
            fg_color=theme.SURFACE, button_color=theme.SURFACE_2,
        )
        self.scope_menu.pack(side="left", padx=8)
        self.project_menu = ctk.CTkOptionMenu(
            bar, values=["General"], width=160, command=lambda *_: self._refresh(),
            fg_color=theme.SURFACE, button_color=theme.SURFACE_2,
        )
        self.project_menu.pack(side="left")
        # Manual cleanup of near-duplicate beliefs (consolidation also runs in the
        # 6-hourly maintenance pass; this lets the user trigger it on demand).
        w.ghost_button(bar, "🧹 Merge duplicates", self._consolidate,
                       width=150, height=28).pack(side="left", padx=12)
        # At-a-glance count of beliefs flagged for review (e.g. after a retraction).
        self.review_lbl = ctk.CTkLabel(bar, text="", font=theme.f(-2),
                                       text_color=theme.WARN)
        self.review_lbl.pack(side="right")

        from aria2.ui.views.paned_view import make_paned
        left_pane, right_pane = make_paned(self, "sidebar_memory_width",
                                           default_w=320, min_w=200, max_w=560,
                                           pady=(10, 10))
        list_card = w.card(left_pane)
        list_card.pack(fill="both", expand=True)
        ctk.CTkLabel(list_card, text="Facts", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.list = ctk.CTkScrollableFrame(list_card, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=6, pady=6)

        self.detail = w.card(right_pane)
        self.detail.pack(fill="both", expand=True)
        self._empty_detail()

    def on_show(self):
        projects = project_service.list_projects()
        self._projects = {p["name"]: p["id"] for p in projects}
        self.project_menu.configure(values=list(self._projects))
        active = next((n for n, i in self._projects.items() if i == self.app.active_project), None)
        if active:
            self.project_menu.set(active)
        self._refresh()

    def _scope(self) -> tuple[str, str]:
        scope = self.scope_menu.get()
        if scope == "user":
            return "user", ""
        return "project", self._projects.get(self.project_menu.get(), "general")

    def _refresh(self):
        for c in self.list.winfo_children():
            c.destroy()
        scope, scope_id = self._scope()
        mems = memory_service.list_memories(scope, scope_id)
        flagged = sum(1 for m in mems if m.get("needs_review"))
        self.review_lbl.configure(text=f"⚠ {flagged} need review" if flagged else "")
        if not mems:
            ctk.CTkLabel(self.list, text="No memories yet in this scope.",
                         font=theme.f(-1), text_color=theme.TEXT_FAINT).pack(
                anchor="w", padx=8, pady=8)
            return
        for m in mems:
            self._row(m)

    def _consolidate(self):
        scope, scope_id = self._scope()
        n = memory_service.consolidate(scope, scope_id)
        self.app.toast(
            f"Merged {n} duplicate{'' if n == 1 else 's'}" if n else "No duplicates found",
            "success" if n else "info")
        self.selected = None
        self._refresh()
        self._empty_detail()

    def _row(self, m: dict):
        active = m["id"] == self.selected
        frame = ctk.CTkFrame(self.list, fg_color=theme.SURFACE_2 if active else "transparent",
                             corner_radius=6)
        frame.pack(fill="x", pady=2, padx=2)
        flag = " ⚠ review" if m.get("needs_review") else ""
        pin = "📌 " if m.get("pinned") else ""
        conf = m.get("confidence") or 0.7
        txt = f"{pin}{m['text'][:70]}"
        btn = ctk.CTkButton(
            frame, text=txt, anchor="w", height=30, fg_color="transparent",
            hover_color=theme.SURFACE_2,
            text_color=theme.WARN if m.get("needs_review") else theme.TEXT,
            font=theme.f(-1), command=lambda i=m["id"]: self._select(i),
        )
        btn.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(frame, text=f"{conf:.0%}{flag}", font=theme.f(-2),
                     text_color=theme.TEXT_FAINT).pack(side="right", padx=8)

    def _empty_detail(self):
        for c in self.detail.winfo_children():
            c.destroy()
        ctk.CTkLabel(self.detail, text="Select a fact to see its provenance.",
                     font=theme.f(-1), text_color=theme.TEXT_FAINT).pack(padx=16, pady=16)

    def _select(self, mem_id: str):
        self.selected = mem_id
        self._refresh()
        for c in self.detail.winfo_children():
            c.destroy()
        m = memory_service.get(mem_id)
        if not m:
            return
        ctk.CTkLabel(self.detail, text="Fact", font=theme.f(-2, "bold"),
                     text_color=theme.accent()).pack(anchor="w", padx=14, pady=(12, 0))
        ctk.CTkLabel(self.detail, text=m["text"], font=theme.f(0), text_color=theme.TEXT,
                     wraplength=320, justify="left").pack(anchor="w", padx=14, pady=(0, 8))

        meta = (f"confidence {m['confidence']:.0%} · importance {m['importance']:.0%} · "
                f"{m['kind']}" + (" · ⚠ needs review" if m["needs_review"] else ""))
        ctk.CTkLabel(self.detail, text=meta, font=theme.f(-2), text_color=theme.TEXT_DIM).pack(
            anchor="w", padx=14)

        prov = memory_service.derivation(mem_id)
        ctk.CTkLabel(self.detail, text="Provenance", font=theme.f(-2, "bold"),
                     text_color=theme.accent()).pack(anchor="w", padx=14, pady=(12, 2))
        src = prov.get("source_run_id")
        ctk.CTkLabel(self.detail,
                     text=f"source run: {src[:14] if src else '— (entered directly)'}",
                     font=theme.f(-2), text_color=theme.TEXT_DIM).pack(anchor="w", padx=14)
        parents = prov.get("derived_from") or []
        if parents:
            ctk.CTkLabel(self.detail, text="derived from:", font=theme.f(-2),
                         text_color=theme.TEXT_DIM).pack(anchor="w", padx=14, pady=(4, 0))
            for p in parents:
                ctk.CTkLabel(self.detail, text=f"  • {p['text'][:60]}", font=theme.f(-2),
                             text_color=theme.TEXT_FAINT, wraplength=300, justify="left").pack(
                    anchor="w", padx=14)

        deps = memory_service.dependents(mem_id)
        if deps:
            ctk.CTkLabel(self.detail, text=f"{len(deps)} fact(s) depend on this",
                         font=theme.f(-2), text_color=theme.WARN).pack(anchor="w", padx=14, pady=(6, 0))

        btns = ctk.CTkFrame(self.detail, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=14)
        pin_label = "Unpin" if m["pinned"] else "Pin"
        w.ghost_button(btns, pin_label, lambda: self._pin(mem_id, not m["pinned"]),
                       width=70, height=30).pack(side="left")
        if m["needs_review"]:
            w.ghost_button(btns, "Clear flag", lambda: self._clear(mem_id),
                           width=90, height=30).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Retract", width=80, height=30, fg_color=theme.DANGER,
                      hover_color=theme.DANGER, command=lambda: self._retract(mem_id)).pack(
            side="right")

    def _pin(self, mem_id, pinned):
        memory_service.set_pinned(mem_id, pinned)
        self._select(mem_id)

    def _clear(self, mem_id):
        memory_service.clear_review(mem_id)
        self._select(mem_id)

    def _retract(self, mem_id):
        res = memory_service.retract(mem_id, reason="user retracted via Memory view")
        self.selected = None
        self._refresh()
        self._empty_detail()
        ctk.CTkLabel(self.detail,
                     text=f"Retracted. {res.get('flagged_for_review',0)} dependent fact(s) "
                          "flagged for review.",
                     font=theme.f(-1), text_color=theme.TEXT_DIM, wraplength=300,
                     justify="left").pack(padx=16, pady=16)
