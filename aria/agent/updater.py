"""
agent/updater.py - Update checker and self-updater for ARIA.

How it works:
  * You (the developer) publish each new version as a GitHub Release, tagged
    with a semantic version like ``v1.2.3``, and attach the built app as a
    ``.zip`` asset (a zip of the PyInstaller ``dist/ARIA`` folder).
  * On startup (and on demand from Settings) the app asks the GitHub API for
    the latest release and compares its version to ``CURRENT_VERSION``.
  * If a newer version exists the user can download it and install it. Because
    Windows cannot overwrite a running ``.exe``, the installer writes a small
    helper batch script that waits for ARIA to exit, copies the new files over
    the install directory, and relaunches the app.

The repo to check is read from settings (key ``github_repo``) so it can be
changed without rebuilding; ``DEFAULT_REPO`` is the fallback.
"""

import os
import sys
import json
import tempfile
import zipfile
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional

from config import settings as cfg

# Single source of truth for the running version. Bump this for each release
# and tag the GitHub release to match (e.g. version "1.1.0" -> tag "v1.1.0").
CURRENT_VERSION = "1.0.5"

# Used only if the user hasn't set "github_repo" in settings.
DEFAULT_REPO = "zeequxz/aria-desktop-assistant"


# ── Version helpers ──────────────────────────────────────────────────────────


def get_current_version() -> str:
    return CURRENT_VERSION


def _repo() -> str:
    return cfg.get("github_repo", DEFAULT_REPO) or DEFAULT_REPO


def _parse(v: str):
    """Turn '1.2.3' into a comparable tuple (1, 2, 3). Non-numeric parts are
    ignored so tags like 'v1.2.3-beta' still compare sensibly."""
    nums = []
    for part in v.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            nums.append(int(digits))
    return tuple(nums)


def is_newer(candidate: str, current: str = CURRENT_VERSION) -> bool:
    """True if `candidate` is a strictly newer version than `current`."""
    try:
        return _parse(candidate) > _parse(current)
    except Exception:
        return False


def is_frozen() -> bool:
    """True when running as a PyInstaller-built executable (not from source)."""
    return getattr(sys, "frozen", False)


def install_dir() -> Path:
    """Directory the app runs from. When frozen this is the folder containing
    ARIA.exe; from source it's the project folder."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ── Checking ─────────────────────────────────────────────────────────────────


def check_for_updates(
    on_update_available: Callable[[dict], None],
    on_up_to_date: Optional[Callable[[], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
):
    """Check GitHub for a newer release in a background thread.

    On success with a newer version, calls ``on_update_available(info)`` where
    ``info`` has keys: version, notes, html_url, asset_url, asset_name.
    Otherwise calls ``on_up_to_date`` / ``on_error`` if provided.
    """

    def worker():
        api = f"https://api.github.com/repos/{_repo()}/releases/latest"
        try:
            req = urllib.request.Request(
                api,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "ARIA-Updater",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if on_error:
                if e.code == 404:
                    # /releases/latest 404s both when the repo is wrong AND when
                    # the repo simply has no published releases yet. Point at the
                    # more common cause.
                    on_error(
                        f"No published releases found for '{_repo()}'. "
                        "Publish a GitHub Release (or check the repo name in Settings)."
                    )
                elif e.code == 403:
                    on_error("GitHub rate limit hit. Try again in a few minutes.")
                else:
                    on_error(
                        f"GitHub returned HTTP {e.code}. Check the repo name in Settings."
                    )
            return
        except Exception as e:
            if on_error:
                on_error(f"Could not reach GitHub: {e}")
            return

        latest = (data.get("tag_name") or "").strip()
        if not latest:
            if on_error:
                on_error("No releases found for this repository yet.")
            return

        if not is_newer(latest):
            if on_up_to_date:
                on_up_to_date()
            return

        # Find a downloadable .zip asset.
        asset_url, asset_name = "", ""
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".zip"):
                asset_url = asset.get("browser_download_url", "")
                asset_name = name
                break

        on_update_available(
            {
                "version": latest.lstrip("vV"),
                "notes": data.get("body", "") or "",
                "html_url": data.get("html_url", ""),
                "asset_url": asset_url,
                "asset_name": asset_name,
            }
        )

    threading.Thread(target=worker, daemon=True).start()


# ── Downloading & applying ───────────────────────────────────────────────────


def download_and_apply(
    info: dict,
    on_progress: Optional[Callable[[float], None]] = None,
    on_ready: Optional[Callable[[], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
):
    """Download the release zip and stage a self-update in a background thread.

    When staging succeeds, ``on_ready()`` is called; the caller should then quit
    the app so the helper script can swap the files in and relaunch.
    """

    def worker():
        asset_url = info.get("asset_url")
        if not asset_url:
            if on_error:
                on_error(
                    "This release has no downloadable .zip asset. "
                    "Use 'Open release page' to update manually."
                )
            return
        if not is_frozen():
            if on_error:
                on_error(
                    "Self-update only works in the packaged app. "
                    "Running from source: pull the new code instead."
                )
            return

        try:
            tmp = Path(tempfile.mkdtemp(prefix="aria_update_"))
            zip_path = tmp / (info.get("asset_name") or "update.zip")

            # Download with coarse progress reporting.
            req = urllib.request.Request(
                asset_url, headers={"User-Agent": "ARIA-Updater"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                read = 0
                with open(zip_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        read += len(chunk)
                        if on_progress and total:
                            on_progress(read / total)

            # Extract and locate the folder that actually contains ARIA.exe.
            extract_dir = tmp / "extracted"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            new_root = _find_app_root(extract_dir)
            if new_root is None:
                if on_error:
                    on_error("Downloaded update did not contain ARIA.exe.")
                return

            _write_and_launch_helper(new_root, install_dir())
            if on_ready:
                on_ready()
        except Exception as e:
            if on_error:
                on_error(f"Update failed: {e}")

    threading.Thread(target=worker, daemon=True).start()


def _find_app_root(folder: Path) -> Optional[Path]:
    """Return the directory inside `folder` that contains ARIA.exe (the zip may
    wrap everything in a top-level subfolder)."""
    direct = folder / "ARIA.exe"
    if direct.exists():
        return folder
    for exe in folder.rglob("ARIA.exe"):
        return exe.parent
    return None


def _write_and_launch_helper(new_root: Path, target_dir: Path):
    """Write a batch script that waits for THIS ARIA process to exit, copies the
    new files over the install directory, relaunches the app, and logs progress.

    Reliability notes (these were real bugs in an earlier version):
      * Wait on our exact PID, not the image name — and force-kill if it lingers,
        otherwise robocopy can't overwrite the still-locked ARIA.exe/_internal.
      * Use `ping` to sleep, not `timeout`, which errors out when the helper has
        no console (the process is launched in its own window).
      * Write a log to %TEMP%\\aria_update_log.txt so failures are diagnosable.
    """
    helper = Path(tempfile.gettempdir()) / "aria_apply_update.bat"
    log = Path(tempfile.gettempdir()) / "aria_update_log.txt"
    exe_path = target_dir / "ARIA.exe"
    pid = os.getpid()
    script = f"""@echo off
setlocal enableextensions
set "LOG={log}"
echo [%date% %time%] ARIA update helper started > "%LOG%"
echo waiting for PID {pid} to exit >> "%LOG%"

rem Wait up to ~30s for the running ARIA (this PID) to close.
set /a n=0
:waitloop
tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL
if errorlevel 1 goto exited
set /a n+=1
if %n% geq 30 goto force
ping -n 2 127.0.0.1 >NUL
goto waitloop

:force
echo wait timed out; force-killing ARIA.exe >> "%LOG%"
taskkill /IM ARIA.exe /F >> "%LOG%" 2>&1
ping -n 3 127.0.0.1 >NUL

:exited
echo copying new files from "{new_root}" to "{target_dir}" >> "%LOG%"
robocopy "{new_root}" "{target_dir}" /E /IS /IT /R:5 /W:2 >> "%LOG%" 2>&1
echo robocopy exit code: %errorlevel% >> "%LOG%"

echo relaunching ARIA >> "%LOG%"
cd /d "{target_dir}"
start "" "{exe_path}"
echo done >> "%LOG%"
"""
    helper.write_text(script, encoding="utf-8")
    # Own console window so cmd built-ins behave and the user sees progress;
    # survives this process exiting.
    subprocess.Popen(
        ["cmd", "/c", str(helper)],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        close_fds=True,
    )
