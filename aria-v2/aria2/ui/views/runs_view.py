"""ui/views/runs_view.py - Run inspector: timeline of steps, tools, tokens, cost."""

from __future__ import annotations

import json
from datetime import datetime

import customtkinter as ctk

from aria2.services import run_service
from aria2.ui import theme
from aria2.ui.views import widgets as w

_STATUS_COLOR = {
    "done": theme.SUCCESS, "running": theme.WARN, "failed": theme.DANGER,
    "cancelled": theme.TEXT_FAINT, "queued": theme.TEXT_DIM,
}


class RunsView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self.selected: str | None = None
        w.header(self, "Runs", "Every chat turn, task, and delegation is an inspectable run.")

        from aria2.ui.views.paned_view import make_paned
        left_pane, right_pane = make_paned(self, "sidebar_runs_width",
                                           default_w=380, min_w=220, max_w=600)
        list_card = w.card(left_pane)
        list_card.pack(fill="both", expand=True)
        ctk.CTkLabel(list_card, text="Recent runs", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.list = ctk.CTkScrollableFrame(list_card, fg_color="transparent")
        self.list.pack(fill="both", expand=True, padx=6, pady=6)

        insp = w.card(right_pane)
        insp.pack(fill="both", expand=True)
        ctk.CTkLabel(insp, text="Inspector", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.steps = ctk.CTkScrollableFrame(insp, fg_color="transparent")
        self.steps.pack(fill="both", expand=True, padx=6, pady=6)

    def on_show(self):
        self._refresh_list()

    def _refresh_list(self):
        for c in self.list.winfo_children():
            c.destroy()
        for r in run_service.list_runs(limit=80):
            color = _STATUS_COLOR.get(r["status"], theme.TEXT_DIM)
            when = datetime.fromtimestamp(r["started_at"] / 1000).strftime("%m-%d %H:%M")
            label = f"{r['kind']} · {r['status']}  ${r['cost_usd']:.3f}"
            row = ctk.CTkButton(
                self.list, text=f"{when}  {label}", anchor="w", height=32,
                fg_color=theme.SURFACE_2 if r["id"] == self.selected else "transparent",
                hover_color=theme.SURFACE_2, text_color=color, font=theme.f(-1),
                command=lambda i=r["id"]: self._select(i),
            )
            row.pack(fill="x", padx=4, pady=1)

    def _select(self, run_id: str):
        self.selected = run_id
        self._refresh_list()
        for c in self.steps.winfo_children():
            c.destroy()
        run = run_service.get_run(run_id)
        if not run:
            return
        meta = (f"{run['kind']} · {run['status']} · {run['token_total']:,} tok · "
                f"${run['cost_usd']:.4f}")
        ctk.CTkLabel(self.steps, text=meta, font=theme.f(-1, "bold"),
                     text_color=theme.accent(), anchor="w").pack(anchor="w", padx=8, pady=(2, 4))
        if run.get("forked_from_run_id"):
            fr = ctk.CTkFrame(self.steps, fg_color="transparent")
            fr.pack(fill="x", padx=8)
            ctk.CTkLabel(fr, text=f"⑂ forked from {run['forked_from_run_id'][:12]} "
                                  f"@ step {run.get('forked_from_step')}",
                         font=theme.f(-2), text_color=theme.TEXT_DIM).pack(side="left")
            w.ghost_button(fr, "Diff vs parent",
                           lambda: self._diff(run["forked_from_run_id"], run["id"]),
                           width=110, height=26).pack(side="right")
        if run["error"]:
            ctk.CTkLabel(self.steps, text=f"⚠ {run['error']}", font=theme.f(-1),
                         text_color=theme.DANGER, wraplength=480, justify="left").pack(
                anchor="w", padx=8)

        for s in run_service.steps(run_id):
            self._step_card(s, run_id)
        # Show delegated child runs, if any.
        for child in run_service.children(run_id):
            ctk.CTkButton(self.steps, text=f"↳ child run: {child['status']}",
                          anchor="w", fg_color="transparent", text_color=theme.TEXT_DIM,
                          font=theme.f(-1), command=lambda i=child["id"]: self._select(i)).pack(
                anchor="w", padx=8)

    def _step_card(self, s: dict, run_id: str):
        card = ctk.CTkFrame(self.steps, fg_color=theme.SURFACE_2, corner_radius=6)
        card.pack(fill="x", pady=2, padx=2)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x")
        if s["type"] == "model":
            title = f"🧠 model  ·  {s['token_in']}→{s['token_out']} tok"
            detail = (s.get("output") or {}).get("text", "")
            # Time-travel: fork a new run from the context this step saw.
            ctk.CTkButton(head, text="⑂ Fork here", width=90, height=24,
                          fg_color="transparent", hover_color=theme.BORDER,
                          text_color=theme.accent(), font=theme.f(-2),
                          command=lambda i=s["idx"]: self._fork(run_id, i)).pack(side="right", padx=4)
        elif s["type"] == "tool":
            title = f"⚙ {s['tool_name']}  ·  {s['duration_ms']}ms"
            detail = json.dumps(s.get("output"))[:300]
        else:
            title = s["type"]
            detail = json.dumps(s.get("output"))[:300]
        ctk.CTkLabel(head, text=title, font=theme.f(-2, "bold"), text_color=theme.TEXT,
                     anchor="w").pack(side="left", padx=8, pady=(5, 0))
        if detail:
            ctk.CTkLabel(card, text=detail[:300], font=theme.mono(-2), text_color=theme.TEXT_DIM,
                         wraplength=480, justify="left", anchor="w").pack(
                anchor="w", padx=8, pady=(0, 5))

    def _fork(self, run_id: str, step_idx: int):
        dlg = _ForkDialog(self, run_id, step_idx)
        self.wait_window(dlg)
        if dlg.new_run_id:
            self.after(400, lambda: (self.on_show(), self._select(dlg.new_run_id)))

    def _diff(self, run_a: str, run_b: str):
        for c in self.steps.winfo_children():
            c.destroy()
        ctk.CTkLabel(self.steps, text=f"Diff: {run_a[:10]} ↔ {run_b[:10]}",
                     font=theme.f(-1, "bold"), text_color=theme.accent()).pack(
            anchor="w", padx=8, pady=6)
        for d in run_service.diff_runs(run_a, run_b):
            color = theme.WARN if d["changed"] else theme.TEXT_FAINT
            card = ctk.CTkFrame(self.steps, fg_color=theme.SURFACE_2, corner_radius=6)
            card.pack(fill="x", pady=2, padx=2)
            ctk.CTkLabel(card, text=f"step {d['idx']}  {'≠' if d['changed'] else '='}",
                         font=theme.f(-2, "bold"), text_color=color, anchor="w").pack(
                anchor="w", padx=8, pady=(4, 0))
            ctk.CTkLabel(card, text=f"A: {d['a'][:120]}", font=theme.mono(-2),
                         text_color=theme.TEXT_DIM, wraplength=480, justify="left").pack(
                anchor="w", padx=8)
            ctk.CTkLabel(card, text=f"B: {d['b'][:120]}", font=theme.mono(-2),
                         text_color=theme.TEXT, wraplength=480, justify="left").pack(
                anchor="w", padx=8, pady=(0, 4))


class _ForkDialog(ctk.CTkToplevel):
    def __init__(self, parent, run_id, step_idx):
        super().__init__(parent)
        self.new_run_id = None
        self.title("Fork run from step")
        self.geometry("460x240")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)
        self.grab_set()
        ctk.CTkLabel(self, text=f"Fork from step {step_idx}",
                     font=theme.f(1, "bold"), text_color=theme.TEXT).pack(anchor="w", padx=16, pady=(16, 4))
        ctk.CTkLabel(self, text="Optionally rewrite the last user message to explore a "
                                "counterfactual (leave blank to replay as-is):",
                     font=theme.f(-1), text_color=theme.TEXT_DIM, wraplength=420,
                     justify="left").pack(anchor="w", padx=16)
        self.edit = ctk.CTkTextbox(self, height=90, fg_color=theme.SURFACE_2, font=theme.f(0))
        self.edit.pack(fill="x", padx=16, pady=8)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=8)
        w.primary_button(row, "Run fork", lambda: self._go(run_id, step_idx), width=110).pack(side="right")

    def _go(self, run_id, step_idx):
        edited = self.edit.get("1.0", "end").strip() or None
        try:
            self.new_run_id = run_service.fork_from_step(run_id, step_idx, edited_user_text=edited)
        except Exception as e:
            ctk.CTkLabel(self, text=str(e), text_color=theme.DANGER, font=theme.f(-1)).pack(padx=16)
            return
        self.destroy()
