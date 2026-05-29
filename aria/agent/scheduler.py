"""
agent/scheduler.py - Background task scheduler.

Runs recurring tasks (daily, weekly, etc.) while the app is open.
Uses the schedule library for simple cron-like scheduling.
"""

import threading
import time
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False

from config import settings as cfg

TASK_LOG_DIR = Path.home() / "AppData" / "Roaming" / "ARIA" / "task_logs"
TASK_LOG_DIR.mkdir(parents=True, exist_ok=True)


class TaskScheduler:
    def __init__(self, on_task_start: Callable, on_task_done: Callable):
        self.on_task_start = on_task_start
        self.on_task_done = on_task_done
        self._running = False
        self._thread = None
        self._jobs = {}  # task_id -> schedule job

    def start(self):
        if not SCHEDULE_AVAILABLE:
            print("[Scheduler] 'schedule' not installed, recurring tasks disabled.")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._reload_tasks()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            schedule.run_pending()
            time.sleep(10)

    def _reload_tasks(self):
        """Load tasks from settings and schedule recurring ones."""
        if not SCHEDULE_AVAILABLE:
            return
        schedule.clear()
        self._jobs = {}
        tasks = cfg.get("tasks", [])
        for task in tasks:
            if task.get("interval", "none") != "none" and task.get("enabled", True):
                self._schedule_task(task)

    def _schedule_task(self, task: dict):
        if not SCHEDULE_AVAILABLE:
            return
        interval = task.get("interval", "none")
        task_id = task.get("id")

        def run_job():
            self._run_task(task)

        if interval == "hourly":
            job = schedule.every().hour.do(run_job)
        elif interval == "daily":
            run_at = task.get("run_at", "09:00")
            job = schedule.every().day.at(run_at).do(run_job)
        elif interval == "weekly":
            run_at = task.get("run_at", "09:00")
            weekday = self._weekday_of(task)
            day_job = getattr(schedule.every(), weekday) if weekday else schedule.every().week
            try:
                job = day_job.at(run_at).do(run_job)
            except Exception:
                job = schedule.every().week.do(run_job)
        elif interval == "monthly":
            # schedule doesn't support monthly natively; check the day-of-month
            # each day and only run when it matches the task's anchor date.
            run_at = task.get("run_at", "09:00")
            target_day = self._day_of_month(task)

            def monthly_job():
                if datetime.now().day == target_day:
                    run_job()

            job = schedule.every().day.at(run_at).do(monthly_job)
        elif interval == "once":
            # One-off task tied to a specific calendar date. Checked daily; runs
            # once on its date, then disables itself so it never repeats.
            run_at = task.get("run_at", "09:00")
            run_date = task.get("run_date")

            def once_job():
                if run_date and datetime.now().strftime("%Y-%m-%d") == run_date:
                    self._run_task(task)
                    self._disable_task(task_id)
                    return schedule.CancelJob

            job = schedule.every().day.at(run_at).do(once_job)
        else:
            return

        self._jobs[task_id] = job

    @staticmethod
    def _weekday_of(task: dict):
        """Return the lowercase weekday name (e.g. 'monday') of a weekly task's
        anchor date, or None if it has no anchor."""
        anchor = task.get("run_date")
        if not anchor:
            return None
        try:
            d = datetime.strptime(anchor, "%Y-%m-%d")
            return ["monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday"][d.weekday()]
        except Exception:
            return None

    @staticmethod
    def _day_of_month(task: dict):
        """Return the day-of-month (1-31) a monthly task should run on."""
        anchor = task.get("run_date")
        if anchor:
            try:
                return datetime.strptime(anchor, "%Y-%m-%d").day
            except Exception:
                pass
        return datetime.now().day

    def _disable_task(self, task_id: str):
        """Mark a task disabled in settings (used after a one-off task runs)."""
        tasks = cfg.get("tasks", [])
        for t in tasks:
            if t.get("id") == task_id:
                t["enabled"] = False
        cfg.set_key("tasks", tasks)

    def _run_task(self, task: dict):
        """Execute a task using the agent."""
        from agent.orchestrator import run_agent_in_thread
        from config import settings as cfg

        task_id = task.get("id", str(uuid.uuid4()))
        task_name = task.get("name", "Unnamed Task")
        self.on_task_start(task_id, task_name)

        agents = cfg.get("agents", [])
        agent_id = task.get("agent", "assistant")
        agent = next((a for a in agents if a["id"] == agent_id), agents[0] if agents else None)
        system = agent["system"] if agent else "You are a helpful assistant."

        results = []

        def on_token(t): results.append(t)
        def on_tool_call(name, inp): pass
        def on_tool_result(name, res): pass
        def on_done(text):
            full = "".join(results)
            self._save_task_result(task_id, task_name, full)
            # Update task last_run in settings
            tasks = cfg.get("tasks", [])
            for t in tasks:
                if t["id"] == task_id:
                    t["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    t["last_result"] = full[:500]
                    t["status"] = "done"
            cfg.set_key("tasks", tasks)
            self.on_task_done(task_id, task_name, full)

        def on_error(e):
            self._save_task_result(task_id, task_name, f"ERROR: {e}")
            self.on_task_done(task_id, task_name, f"Error: {e}")

        run_agent_in_thread(
            messages=[{"role": "user", "content": task["prompt"]}],
            system_prompt=system,
            on_token=on_token,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_done=on_done,
            on_error=on_error,
        )

    def run_task_now(self, task: dict):
        """Manually trigger a task immediately."""
        t = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        t.start()

    def _save_task_result(self, task_id: str, task_name: str, result: str):
        """Save task output to a log file."""
        log_file = TASK_LOG_DIR / f"{task_id}.json"
        history = []
        if log_file.exists():
            try:
                with open(log_file, "r") as f:
                    history = json.load(f)
            except Exception:
                pass
        history.append({
            "timestamp": datetime.now().isoformat(),
            "task_name": task_name,
            "result": result,
        })
        history = history[-50:]  # Keep last 50 runs
        with open(log_file, "w") as f:
            json.dump(history, f, indent=2)

    def get_task_history(self, task_id: str) -> list:
        log_file = TASK_LOG_DIR / f"{task_id}.json"
        if not log_file.exists():
            return []
        try:
            with open(log_file, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def reload(self):
        self._reload_tasks()
