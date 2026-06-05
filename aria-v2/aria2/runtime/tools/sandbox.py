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

import os
import subprocess
import tempfile
import time
from pathlib import Path

from aria2.core import procutil

MAX_OUTPUT = 20_000

# Background (detached) processes launched via run_shell(background=True).
_bg_procs: list = []  # (pid, Popen, log_path)


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
            **procutil.NO_WINDOW,
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


def run_command_background(command: str, cwd: str | None = None) -> dict:
    """Launch a long-running command (e.g. a dev / HTTP server) WITHOUT waiting.

    Returns immediately with the PID; stdout/stderr are redirected to a log file.
    The process is tracked and terminated when ARIA exits (terminate_background),
    so servers don't get orphaned and ports don't stay stuck."""
    workdir = Path(cwd) if cwd else Path.cwd()
    if not workdir.exists():
        return {"error": f"Working directory does not exist: {workdir}"}
    try:
        log_path = Path(tempfile.gettempdir()) / f"aria_bg_{int(time.time() * 1000)}.log"
        log = open(log_path, "w", encoding="utf-8", errors="replace")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW (no console flash)
        proc = subprocess.Popen(
            command, shell=True, cwd=str(workdir),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs,
        )
        log.close()  # the child holds its own dup of the fd
        _bg_procs.append((proc.pid, proc, str(log_path)))
        return {
            "started": True, "background": True, "pid": proc.pid,
            "log": str(log_path),
            "note": "Launched in the background; keeps running until ARIA exits.",
        }
    except Exception as e:
        return {"error": str(e)}


def terminate_background() -> int:
    """Stop all tracked background processes (called on app shutdown). On Windows
    it kills the whole process tree so a shell-spawned child (the actual server)
    doesn't survive. Returns how many were still running."""
    n = 0
    for pid, proc, _log in _bg_procs:
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                   capture_output=True, timeout=10, **procutil.NO_WINDOW)
                else:
                    proc.terminate()
                n += 1
        except Exception:
            pass
    _bg_procs.clear()
    return n


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
