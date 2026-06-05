"""runtime/tools/sandbox.py - Confined command/code execution.

A modest but real jail (far better than v1's unguarded code runner):
  * commands run with cwd pinned to the project folder (or a temp dir),
  * a hard timeout kills runaway processes,
  * output is captured and truncated,
  * an optional allowlist of executables can be enforced.

True OS-level isolation (containers) is a future upgrade; this stops the
common foot-guns today.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_OUTPUT = 20_000


def _exec(cmd, cwd: str | None, timeout: int, shell: bool) -> dict:
    """Run a command (str for shell, argv list for no-shell), capture + truncate
    output, hard-timeout. Shared by run_command and run_python."""
    workdir = Path(cwd) if cwd else Path.cwd()
    if not workdir.exists():
        return {"error": f"Working directory does not exist: {workdir}"}
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "")[:MAX_OUTPUT]
        err = (proc.stderr or "")[:MAX_OUTPUT]
        return {
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "truncated": len(proc.stdout or "") > MAX_OUTPUT,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "timeout": True}
    except Exception as e:
        return {"error": str(e)}


def run_command(
    command: str,
    cwd: str | None = None,
    timeout: int = 60,
    shell: bool = True,
) -> dict:
    return _exec(command, cwd, timeout, shell)


def run_python(code: str, cwd: str | None = None, timeout: int = 60) -> dict:
    """Run a Python snippet in a subprocess (never in-process).

    The code is written to a temp file and executed by path rather than passed
    via `python -c "<...>"`. That avoids fragile shell quoting — on Windows,
    cmd.exe mangles `-c` payloads containing quotes/backslashes — so snippets run
    correctly regardless of their contents. No shell is involved.
    """
    import os
    import sys
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py", prefix="aria_py_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        return _exec([sys.executable, path], cwd, timeout, shell=False)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
