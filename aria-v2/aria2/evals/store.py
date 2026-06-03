"""evals/store.py - Persist eval reports and load history for charting.

Each suite run is written as a timestamped JSON under %APPDATA%/ARIA2/evals/.
load_history() returns a compact, time-ordered series so the Evals view can plot
pass-rate over time and spot regressions at a glance.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aria2.core import config


def _dir() -> Path:
    d = config.app_dir() / "evals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_report(summary: dict, suite: str = "all") -> Path:
    record = {**summary, "suite": suite, "timestamp": int(time.time())}
    path = _dir() / f"eval_{record['timestamp']}_{suite}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def load_history(limit: int = 100) -> list[dict]:
    points = []
    for f in sorted(_dir().glob("eval_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        points.append({
            "timestamp": d.get("timestamp", int(f.stat().st_mtime)),
            "suite": d.get("suite", "all"),
            "pass_rate": d.get("pass_rate", 0.0),
            "passed": d.get("passed", 0),
            "total": d.get("total", 0),
            "cost_usd": d.get("cost_usd", 0.0),
        })
    points.sort(key=lambda p: p["timestamp"])
    return points[-limit:]
