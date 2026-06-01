"""
agent/code_runner.py - Run code / shell commands (advanced mode, confirmed).

Gives the agent two tools:

  run_python(code)    -> execute a Python snippet, return stdout/stderr
  run_shell(command)  -> execute a shell command, return stdout/stderr

SECURITY: this is arbitrary code execution. Every call is gated behind a
user confirmation: the app registers a confirm callback via set_confirmer();
the tool blocks until the user approves or denies in a dialog. If no confirmer
is registered (e.g. headless), execution is denied by default. Commands run
with a timeout and their output is truncated.
"""

import sys
import subprocess
import tempfile
from pathlib import Path

from config import settings as cfg

_TIMEOUT = 60
_MAX_OUTPUT = 8000

# The app sets this to a function (kind, content) -> bool that asks the user to
# approve running `content`. Left None in headless contexts (denies by default).
_confirmer = None


def set_confirmer(fn):
    """Register the UI confirmation callback. fn(kind, content) -> bool."""
    global _confirmer
    _confirmer = fn


def _approved(kind: str, content: str) -> bool:
    if _confirmer is None:
        return False
    try:
        return bool(_confirmer(kind, content))
    except Exception:
        return False


def _trim(text: str) -> str:
    if text and len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + "\n…(output truncated)"
    return text or ""


def _result(proc) -> dict:
    return {
        "exit_code": proc.returncode,
        "stdout": _trim(proc.stdout),
        "stderr": _trim(proc.stderr),
    }


def _workdir() -> str:
    return cfg.get("workspace_folder", str(Path.home()))


def run_python(code: str) -> dict:
    """Run a Python snippet in a subprocess and return its output."""
    if not (code or "").strip():
        return {"error": "No code provided."}
    if not _approved("Python", code):
        return {"error": "User declined to run this code."}
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aria_code_")) / "snippet.py"
        tmp.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=_workdir(),
        )
        return _result(proc)
    except subprocess.TimeoutExpired:
        return {"error": f"Code timed out after {_TIMEOUT}s."}
    except Exception as e:
        return {"error": f"Failed to run code: {e}"}


def run_shell(command: str) -> dict:
    """Run a shell command and return its output."""
    if not (command or "").strip():
        return {"error": "No command provided."}
    if not _approved("Shell", command):
        return {"error": "User declined to run this command."}
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=_workdir(),
        )
        return _result(proc)
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {_TIMEOUT}s."}
    except Exception as e:
        return {"error": f"Failed to run command: {e}"}


CODE_RUNNER_TOOLS = {
    "run_python": run_python,
    "run_shell": run_shell,
}

CODE_RUNNER_TOOL_SCHEMAS = [
    {
        "name": "run_python",
        "description": "Run a short Python script and get back its stdout, stderr "
        "and exit code. Use for calculations, data processing, or generating "
        "files. The user must approve each run. Runs in the workspace folder with "
        "a 60s timeout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."}
            },
            "required": ["code"],
        },
    },
    {
        "name": "run_shell",
        "description": "Run a shell/terminal command and get back its stdout, "
        "stderr and exit code. The user must approve each run. Runs in the "
        "workspace folder with a 60s timeout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run."}
            },
            "required": ["command"],
        },
    },
]
