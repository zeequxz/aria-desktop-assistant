"""runtime/sandbox_overlay.py - Copy-on-write overlay for speculative dry runs.

The "show me what it would do" moat. In dry-run mode an agent's file writes are
redirected into a temporary overlay instead of the real project folder, reads
are served from the overlay first (so the agent sees its own pending changes),
and shell/code execution is *captured, not executed* (we can't roll back real
commands). Afterwards we compute a predicted diff; the user commits the overlay
to the real folder atomically, or discards it with zero trace.

This requires deterministic, contained file ops — which aria-v2 already has —
so it slots in cleanly. Cloud agents that act directly on real resources have no
overlay to roll back.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
import tempfile
from pathlib import Path

from aria2.core import procutil

MAX_READ = 100_000


class OverlaySandbox:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).resolve()
        self.overlay = Path(tempfile.mkdtemp(prefix="aria2_dryrun_"))
        self.written: set[str] = set()
        self.commands: list[str] = []

    # ── Containment ────────────────────────────────────────────────────────────

    def _rel_ok(self, rel: str) -> bool:
        try:
            (self.base / rel).resolve().relative_to(self.base)
            return True
        except ValueError:
            return False

    # ── File ops (used by sandbox-aware file tools) ─────────────────────────────

    def read(self, rel: str) -> dict:
        if not self._rel_ok(rel):
            return {"error": "Path escapes the project folder."}
        ov = self.overlay / rel
        real = self.base / rel
        target = ov if ov.exists() else real
        if not target.exists():
            return {"error": f"Not found: {rel}"}
        try:
            return {"path": rel, "content": target.read_text("utf-8", "replace")[:MAX_READ]}
        except Exception as e:
            return {"error": str(e)}

    def write(self, rel: str, content: str) -> dict:
        if not self._rel_ok(rel):
            return {"error": "Path escapes the project folder."}
        dst = self.overlay / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        self.written.add(rel)
        return {"path": rel, "bytes": len(content.encode("utf-8")), "dry_run": True}

    def list(self, rel: str = ".") -> dict:
        names = {}
        for root in (self.base / rel, self.overlay / rel):
            if root.exists() and root.is_dir():
                for child in root.iterdir():
                    names[child.name] = "dir" if child.is_dir() else "file"
        return {"path": rel, "entries": [{"name": n, "type": t}
                                         for n, t in sorted(names.items())][:500]}

    def record_command(self, command: str) -> dict:
        self.commands.append(command)
        return {"dry_run": True, "captured": True, "would_run": command,
                "note": "Command was NOT executed (dry run). Commit to run for real."}

    # ── Diff / commit / discard ──────────────────────────────────────────────────

    def diff(self) -> dict:
        files = []
        for rel in sorted(self.written):
            ov, real = self.overlay / rel, self.base / rel
            new_text = ov.read_text("utf-8", "replace") if ov.exists() else ""
            old_text = real.read_text("utf-8", "replace") if real.exists() else ""
            status = "modified" if real.exists() else "created"
            udiff = "\n".join(list(difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="",
            ))[:200])
            files.append({"path": rel, "status": status,
                          "old_bytes": len(old_text.encode()),
                          "new_bytes": len(new_text.encode()),
                          "diff": udiff[:4000]})
        return {"files": files, "commands": list(self.commands),
                "has_changes": bool(files or self.commands)}

    def is_git_repo(self) -> bool:
        return (self.base / ".git").exists()

    def commit(self, git_commit: bool = False, message: str | None = None) -> dict:
        applied = []
        for rel in sorted(self.written):
            src, dst = self.overlay / rel, self.base / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                applied.append(rel)
        git_result = None
        if git_commit and applied and self.is_git_repo():
            git_result = self._git_commit(applied, message)
        self.discard()
        return {"committed": applied, "commands_skipped": list(self.commands),
                "git": git_result}

    def _git_commit(self, files: list[str], message: str | None) -> dict:
        msg = message or f"aria: apply dry-run changes ({len(files)} file(s))"
        try:
            subprocess.run(["git", "add", *files], cwd=str(self.base),
                           capture_output=True, text=True, timeout=30, check=True,
                           **procutil.NO_WINDOW)
            r = subprocess.run(["git", "commit", "-m", msg], cwd=str(self.base),
                               capture_output=True, text=True, timeout=30,
                               **procutil.NO_WINDOW)
            if r.returncode != 0:
                return {"error": (r.stderr or r.stdout).strip()[:300]}
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                 cwd=str(self.base), capture_output=True, text=True,
                                 timeout=10, **procutil.NO_WINDOW).stdout.strip()
            return {"committed_sha": sha, "message": msg, "files": files}
        except Exception as e:
            return {"error": str(e)}

    def discard(self) -> dict:
        shutil.rmtree(self.overlay, ignore_errors=True)
        return {"discarded": True}
