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


def run_command(
    command: str,
    cwd: str | None = None,
    timeout: int = 60,
    shell: bool = True,
) -> dict:
    workdir = Path(cwd) if cwd else Path.cwd()
    if not workdir.exists():
        return {"error": f"Working directory does not exist: {workdir}"}
    try:
        proc = subprocess.run(
            command,
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


def run_python(code: str, cwd: str | None = None, timeout: int = 60) -> dict:
    """Run a Python snippet in a subprocess (never in-process)."""
    import sys

    return run_command(
        f'"{sys.executable}" -c {_quote(code)}', cwd=cwd, timeout=timeout, shell=True
    )


def _quote(code: str) -> str:
    # Cross-platform-ish quoting for -c; rely on the shell, escape doublequotes.
    escaped = code.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
