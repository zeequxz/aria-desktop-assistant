"""ui/views/evals_view.py - Run eval suites and chart pass-rate over time.

Pick a suite, run it (real provider) or run the keyless harness self-test, see a
per-case results table, and watch pass-rate trend across past runs on a small
canvas chart fed by evals/store. This turns "is the agent getting better or
worse?" into a glance.
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

from aria2.evals import store
from aria2.evals.cases import get_suite, suite_names
from aria2.evals.harness import run_suite, self_test
from aria2.ui import theme
from aria2.ui.views import widgets as w


class EvalsView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        self._running = False
        w.header(self, "Evals", "Score agents on golden tasks; track pass-rate over time.")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=24)
        ctk.CTkLabel(bar, text="Suite", font=theme.f(-1), text_color=theme.TEXT_DIM).pack(side="left")
        self.suite = ctk.CTkOptionMenu(bar, values=suite_names(), width=140,
                                       fg_color=theme.SURFACE, button_color=theme.SURFACE_2)
        self.suite.pack(side="left", padx=8)
        self.run_btn = w.primary_button(bar, "Run suite", self._run, width=110)
        self.run_btn.pack(side="left")
        w.ghost_button(bar, "Self-test (no key)", self._self_test, width=150).pack(side="left", padx=8)
        self.status = ctk.CTkLabel(bar, text="", font=theme.f(-1), text_color=theme.TEXT_DIM)
        self.status.pack(side="left", padx=10)

        from aria2.ui.views.paned_view import make_paned
        left_pane, right_pane = make_paned(self, "sidebar_evals_width",
                                           default_w=420, min_w=240, max_w=700,
                                           pady=(10, 10))
        res_card = w.card(left_pane)
        res_card.pack(fill="both", expand=True)
        ctk.CTkLabel(res_card, text="Results", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.results = ctk.CTkScrollableFrame(res_card, fg_color="transparent")
        self.results.pack(fill="both", expand=True, padx=6, pady=6)

        hist_card = w.card(right_pane)
        hist_card.pack(fill="both", expand=True)
        ctk.CTkLabel(hist_card, text="Pass-rate history", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=12, pady=8)
        self.canvas = tk.Canvas(hist_card, height=220, bg=theme.SURFACE,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.hist_label = ctk.CTkLabel(hist_card, text="", font=theme.f(-2),
                                       text_color=theme.TEXT_DIM)
        self.hist_label.pack(anchor="w", padx=12, pady=(0, 8))

    def on_show(self):
        self._draw_history()

    # ── Running ─────────────────────────────────────────────────────────────────

    def _run(self):
        if self._running:
            return
        suite = self.suite.get()
        cases = get_suite(suite)
        self._running = True
        self.run_btn.configure(state="disabled")
        self.status.configure(text=f"Running '{suite}' ({len(cases)} cases)…",
                              text_color=theme.TEXT_DIM)
        self._clear_results()

        def worker():
            try:
                summary = run_suite(cases)
                store.save_report(summary, suite)
                err = None
            except Exception as e:
                summary, err = None, str(e)
            self.after(0, lambda: self._done(summary, err))

        threading.Thread(target=worker, daemon=True).start()

    def _self_test(self):
        self.status.configure(text="Running harness self-test…", text_color=theme.TEXT_DIM)

        def worker():
            res = self_test()
            self.after(0, lambda: self.status.configure(
                text=f"Self-test: pass-case={res['pass_ok']} fail-case={res['fail_ok']}",
                text_color=theme.SUCCESS if (res["pass_ok"] and res["fail_ok"]) else theme.DANGER))

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, summary, err):
        self._running = False
        self.run_btn.configure(state="normal")
        if err:
            self.status.configure(text=f"✗ {err[:70]}", text_color=theme.DANGER)
            return
        self.status.configure(
            text=f"{summary['passed']}/{summary['total']} passed "
                 f"({summary['pass_rate']:.0%}) · ${summary['cost_usd']:.4f}",
            text_color=theme.SUCCESS if summary["passed"] == summary["total"] else theme.WARN)
        for r in summary["results"]:
            self._result_row(r)
        self._draw_history()

    def _clear_results(self):
        for c in self.results.winfo_children():
            c.destroy()

    def _result_row(self, r: dict):
        card = ctk.CTkFrame(self.results, fg_color=theme.SURFACE_2, corner_radius=6)
        card.pack(fill="x", pady=2, padx=2)
        color = theme.SUCCESS if r["passed"] else theme.DANGER
        ctk.CTkLabel(card, text=f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']}  ·  "
                                f"score {r['score']:.2f}  ·  {r['status']}  ·  "
                                f"${r['cost_usd']:.4f}",
                     font=theme.f(-1, "bold"), text_color=color, anchor="w").pack(
            anchor="w", padx=8, pady=(5, 0))
        for c in r["checks"]:
            if not c["passed"]:
                ctk.CTkLabel(card, text=f"   ✗ {c['check']}", font=theme.mono(-2),
                             text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w", padx=8)
        ctk.CTkLabel(card, text="", height=2).pack()

    # ── History chart ─────────────────────────────────────────────────────────────

    def _draw_history(self):
        self.canvas.delete("all")
        pts = [p for p in store.load_history() if p["suite"] in (self.suite.get(), "all")] \
            or store.load_history()
        self.canvas.update_idletasks()
        wpx = max(self.canvas.winfo_width(), 240)
        hpx = max(self.canvas.winfo_height(), 200)
        pad = 28
        # Axes.
        self.canvas.create_line(pad, hpx - pad, wpx - 8, hpx - pad, fill=theme.BORDER)
        self.canvas.create_line(pad, 8, pad, hpx - pad, fill=theme.BORDER)
        for frac, lbl in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%")):
            y = (hpx - pad) - frac * (hpx - pad - 8)
            self.canvas.create_text(pad - 4, y, text=lbl, anchor="e",
                                    fill=theme.TEXT_FAINT, font=(theme.FONT, 8))
        if not pts:
            self.canvas.create_text(wpx / 2, hpx / 2, text="No eval runs yet",
                                    fill=theme.TEXT_FAINT, font=(theme.FONT, 11))
            self.hist_label.configure(text="")
            return
        n = len(pts)
        x0, x1 = pad, wpx - 8
        span = (x1 - x0) / max(n - 1, 1)
        coords = []
        for i, p in enumerate(pts):
            x = x0 + i * span
            y = (hpx - pad) - p["pass_rate"] * (hpx - pad - 8)
            coords.append((x, y))
        if len(coords) > 1:
            self.canvas.create_line(*[c for xy in coords for c in xy],
                                    fill=theme.accent(), width=2, smooth=True)
        for (x, y), p in zip(coords, pts):
            col = theme.SUCCESS if p["pass_rate"] >= 0.999 else (
                theme.WARN if p["pass_rate"] >= 0.5 else theme.DANGER)
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")
        last = pts[-1]
        when = datetime.fromtimestamp(last["timestamp"]).strftime("%m-%d %H:%M")
        self.hist_label.configure(
            text=f"{n} runs · latest {last['pass_rate']:.0%} "
                 f"({last['passed']}/{last['total']}) on {when}")
