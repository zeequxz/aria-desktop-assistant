"""runtime/tools/shell_tools.py - Sandboxed shell + python execution tools.

Both default to "ask" — they run real commands. Execution is confined to the
project folder via the sandbox and hard-timed.
"""

from __future__ import annotations

from aria2.runtime.tools import sandbox
from aria2.runtime.tools.base import Tool


def make_shell_tools(base_dir: str, dry_run_sandbox=None) -> list[Tool]:
    # In dry-run mode commands are captured, not executed — we can't roll back
    # real side effects, so we record intent and surface it in the predicted diff.
    def run_shell(command: str, timeout: int = 60, background: bool = False) -> dict:
        if dry_run_sandbox is not None:
            return dry_run_sandbox.record_command(
                command + ("  (background)" if background else ""))
        if background:
            return sandbox.run_command_background(command, cwd=base_dir)
        return sandbox.run_command(command, cwd=base_dir, timeout=timeout)

    def run_python(code: str, timeout: int = 60) -> dict:
        if dry_run_sandbox is not None:
            return dry_run_sandbox.record_command(f"python -c <{len(code)} chars>")
        return sandbox.run_python(code, cwd=base_dir, timeout=timeout)

    return [
        Tool(
            "run_shell",
            "Run a shell command in the project folder (sandboxed, timed). "
            "Returns exit code, stdout, stderr. For a long-running process such "
            "as starting a dev/HTTP server, set background=true: it launches "
            "without blocking, returns a PID immediately, and keeps running so "
            "you can continue (e.g. then notify the user). Background processes "
            "are stopped when ARIA exits.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 60},
                    "background": {
                        "type": "boolean", "default": False,
                        "description": "Run detached without waiting — use for "
                                       "servers / long-running processes.",
                    },
                },
                "required": ["command"],
            },
            run_shell,
            default_policy="ask",
        ),
        Tool(
            "run_python",
            "Run a Python snippet in a subprocess in the project folder.",
            {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer", "default": 60},
                },
                "required": ["code"],
            },
            run_python,
            default_policy="ask",
        ),
    ]
