"""ui/views/calendar_view.py - Month calendar for scheduling one-off tasks.

A familiar way to schedule work: browse months, see which days have scheduled
triggers, click a day to create a one-off task (date + time) or review what's
already queued. One-off triggers fire once then disable themselves.
"""

from __future__ import annotations

import calendar
from datetime import datetime

import customtkinter as ctk

from aria2.services import agent_service, automation_service, project_service
from aria2.ui import theme
from aria2.ui.views import widgets as w

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class CalendarView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color=theme.BG)
        self.app = app
        now = datetime.now()
        self.year, self.month = now.year, now.month
        w.header(self, "Calendar", "Schedule one-off tasks on a day; see what's queued.")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=24)
        w.ghost_button(bar, "‹", lambda: self._shift(-1), width=40).pack(side="left")
        self.title_lbl = ctk.CTkLabel(bar, text="", font=theme.f(2, "bold"),
                                      text_color=theme.TEXT, width=200)
        self.title_lbl.pack(side="left", padx=8)
        w.ghost_button(bar, "›", lambda: self._shift(1), width=40).pack(side="left")
        w.ghost_button(bar, "Today", self._today, width=70).pack(side="left", padx=8)

        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.pack(fill="both", expand=True, padx=24, pady=12)

    def on_show(self):
        self._render()

    def _shift(self, delta: int):
        m = self.month + delta
        self.year += (m - 1) // 12
        self.month = (m - 1) % 12 + 1
        self._render()

    def _today(self):
        now = datetime.now()
        self.year, self.month = now.year, now.month
        self._render()

    def _render(self):
        for c in self.grid_frame.winfo_children():
            c.destroy()
        self.title_lbl.configure(text=f"{calendar.month_name[self.month]} {self.year}")
        sched = automation_service.scheduled_in_month(self.year, self.month)
        today = datetime.now()

        for col in range(7):
            self.grid_frame.grid_columnconfigure(col, weight=1, uniform="day")
            ctk.CTkLabel(self.grid_frame, text=_WEEKDAYS[col], font=theme.f(-1, "bold"),
                         text_color=theme.TEXT_DIM).grid(row=0, column=col, pady=(0, 4))

        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self.year, self.month)
        for r, week in enumerate(weeks, start=1):
            self.grid_frame.grid_rowconfigure(r, weight=1)
            for col, day in enumerate(week):
                if day == 0:
                    continue
                triggers = sched.get(day, [])
                is_today = (day == today.day and self.month == today.month
                            and self.year == today.year)
                cell = ctk.CTkFrame(
                    self.grid_frame,
                    fg_color=theme.SURFACE_2 if triggers else theme.SURFACE,
                    corner_radius=8,
                    border_width=2 if is_today else 0, border_color=theme.accent())
                cell.grid(row=r, column=col, sticky="nsew", padx=3, pady=3)
                btn = ctk.CTkButton(
                    cell, text=str(day), anchor="nw", height=24, width=24,
                    fg_color="transparent", hover_color=theme.BORDER,
                    text_color=theme.accent() if is_today else theme.TEXT,
                    font=theme.f(0, "bold"), command=lambda d=day: self._open_day(d))
                btn.pack(anchor="nw", padx=4, pady=2)
                for t in triggers[:3]:
                    ctk.CTkLabel(cell, text=f"• {t['name'][:14]}", font=theme.f(-2),
                                 text_color=theme.TEXT_DIM, anchor="w").pack(
                        anchor="w", padx=6)

    def _open_day(self, day: int):
        date_str = f"{self.year:04d}-{self.month:02d}-{day:02d}"
        _DayDialog(self, date_str)


class _DayDialog(ctk.CTkToplevel):
    def __init__(self, parent, date_str: str):
        super().__init__(parent)
        self.parent = parent
        self.date_str = date_str
        self.title(f"Schedule — {date_str}")
        self.geometry("460x520")
        self.configure(fg_color=theme.SURFACE)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        ctk.CTkLabel(self, text=f"Tasks on {date_str}", font=theme.f(2, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=18, pady=(16, 4))
        self.existing = ctk.CTkScrollableFrame(self, fg_color=theme.SURFACE_2, height=120)
        self.existing.pack(fill="x", padx=18, pady=4)
        self._list_existing()

        ctk.CTkLabel(self, text="New one-off task", font=theme.f(0, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=18, pady=(12, 2))
        self.name_f, self.name_e = w.labeled_entry(self, "Name")
        self.name_f.pack(fill="x", padx=18, pady=4)
        self.time_f, self.time_e = w.labeled_entry(self, "Time (HH:MM)", "09:00")
        self.time_f.pack(fill="x", padx=18, pady=4)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        self._projects = {p["name"]: p["id"] for p in project_service.list_projects()}
        self._agents = {a["name"]: a["id"] for a in agent_service.list_agents()}
        self.project_menu = ctk.CTkOptionMenu(row, values=list(self._projects), width=130,
                                              fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.project_menu.pack(side="left")
        self.agent_menu = ctk.CTkOptionMenu(row, values=list(self._agents), width=130,
                                            fg_color=theme.SURFACE_2, button_color=theme.SURFACE_2)
        self.agent_menu.pack(side="left", padx=8)

        ctk.CTkLabel(self, text="Prompt", font=theme.f(-1),
                     text_color=theme.TEXT_DIM).pack(anchor="w", padx=18, pady=(8, 2))
        self.prompt = ctk.CTkTextbox(self, height=110, fg_color=theme.SURFACE_2,
                                     font=theme.f(0), wrap="word")
        self.prompt.pack(fill="x", padx=18)
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=12)
        w.primary_button(btns, "Schedule", self._schedule, width=120).pack(side="left")
        self.status = ctk.CTkLabel(btns, text="", font=theme.f(-1), text_color=theme.SUCCESS)
        self.status.pack(side="left", padx=10)

    def _list_existing(self):
        for c in self.existing.winfo_children():
            c.destroy()
        day = int(self.date_str.split("-")[2])
        y, m = int(self.date_str.split("-")[0]), int(self.date_str.split("-")[1])
        triggers = automation_service.scheduled_in_month(y, m).get(day, [])
        if not triggers:
            ctk.CTkLabel(self.existing, text="Nothing scheduled.", font=theme.f(-1),
                         text_color=theme.TEXT_FAINT).pack(anchor="w", padx=6, pady=6)
        for t in triggers:
            ctk.CTkLabel(self.existing, text=f"• {t['name']} ({t['kind']})",
                         font=theme.f(-1), text_color=theme.TEXT, anchor="w").pack(
                anchor="w", padx=6, pady=2)

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()
        self.after(10, self.destroy)

    def _schedule(self):
        name = self.name_e.get().strip()
        prompt = self.prompt.get("1.0", "end").strip()
        if not name or not prompt:
            self.status.configure(text="Name + prompt required", text_color=theme.DANGER)
            return
        automation_service.schedule_once(
            name, prompt, self.date_str, at=self.time_e.get().strip() or "09:00",
            project_id=self._projects.get(self.project_menu.get(), "general"),
            agent_id=self._agents.get(self.agent_menu.get(), "assistant"))
        self.status.configure(text="Scheduled ✓", text_color=theme.SUCCESS)
        self._list_existing()
        self.parent._render()
