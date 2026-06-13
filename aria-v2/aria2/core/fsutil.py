"""core/fsutil.py - Shared filesystem helpers.

One source of truth for "which directories should we never walk". The ambient
watcher, knowledge ingestion, and file-change triggers all scan project folders;
each had its own copy of the ignore list (and each independently had the bug of
descending into node_modules before the list was added). Centralising it means a
new vendored dir is excluded everywhere at once.
"""

from __future__ import annotations

import os
from typing import Iterator

# Vendored / build / cache directories — large and not user-authored, so never
# worth walking for capture, ingestion, or change-detection.
IGNORE_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", "dist", "build", "target", "out",
    "bin", "obj", ".tox", ".mypy_cache", ".pytest_cache", ".gradle", ".next",
    ".cache",
})


def walk_files(root: str) -> Iterator[tuple[str, str]]:
    """Yield (dirpath, filename) for every file under `root`, pruning IGNORE_DIRS
    and hidden directories IN PLACE so heavy/vendored trees are never descended
    into (the whole point — don't pay to walk node_modules/.git)."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            yield dirpath, name
