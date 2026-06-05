"""services/update_service.py - Lightweight auto-update channel.

Polls a configurable JSON manifest and compares its version to the running one.
The manifest is just:

    {"version": "2.1.0", "url": "https://…/ARIA2-2.1.0.zip", "notes": "…"}

We never silently replace a running executable (that's fragile and, unsigned,
unsafe). Instead we surface an update banner, can download the asset to a
downloads folder, and open it for the user to install — a safe, transparent
channel that a code-signing + delta step can later build on.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from aria2 import __version__
from aria2.core import config


def _parse(v: str) -> tuple:
    parts = []
    for chunk in str(v).strip().lstrip("v").split("."):
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(remote: str, local: str = __version__) -> bool:
    return _parse(remote) > _parse(local)


# Only ever fetch/download over http(s). The manifest is remote data, so its
# asset URL is attacker-controllable if the manifest endpoint is compromised;
# without this guard urllib would happily follow file://, ftp://, etc.
_ALLOWED_SCHEMES = ("http", "https")


def _scheme_ok(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme.lower() in _ALLOWED_SCHEMES


def _fetch_manifest(url: str) -> tuple[dict | None, str]:
    """Fetch + parse the update manifest. Returns (manifest, error); error is ""
    on success, otherwise a human message — so callers can tell a *failed check*
    apart from *no update available* (both used to collapse to None, which the UI
    then showed as a false 'Up to date')."""
    if not url:
        return None, "No update manifest URL configured."
    if not _scheme_ok(url):
        return None, "Manifest URL must be http(s)."
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"ARIA2/{__version__}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), ""
    except Exception as e:
        return None, f"Couldn't reach the update server ({e})."


def _manifest_url(override: str | None = None) -> str:
    """Resolve the manifest URL, falling back to the built-in default when the
    saved config has it blank — some upgraded installs persisted an empty value,
    which silently broke every update check (it looked like 'up to date')."""
    return (override or config.get("update_manifest_url")
            or config.DEFAULTS.get("update_manifest_url", ""))


def check_for_update(manifest_url: str | None = None) -> dict | None:
    """Return {version, url, notes, current} if a newer version is published, else
    None. NOTE: None means 'no update' OR 'check failed' — use check_status() when
    you need to tell those apart (e.g. in the Settings UI)."""
    url = _manifest_url(manifest_url)
    manifest, _err = _fetch_manifest(url)
    if not manifest:
        return None
    remote = manifest.get("version", "")
    if remote and is_newer(remote):
        return {"version": remote, "url": manifest.get("url", ""),
                "notes": manifest.get("notes", ""), "current": __version__,
                "sha256": manifest.get("sha256", "")}
    return None


def check_status(manifest_url: str | None = None) -> dict:
    """Rich update status for the UI. Always reports the running version and
    distinguishes the three outcomes.

    Returns {"status": "update"|"current"|"error", "current": <running version>,
    plus "version"/"url"/"notes" for an update, or "error" for a failed check}."""
    url = _manifest_url(manifest_url)
    manifest, err = _fetch_manifest(url)
    if not manifest:
        return {"status": "error", "current": __version__, "error": err}
    remote = manifest.get("version", "")
    if remote and is_newer(remote):
        return {"status": "update", "current": __version__, "version": remote,
                "url": manifest.get("url", ""), "notes": manifest.get("notes", ""),
                "sha256": manifest.get("sha256", "")}
    return {"status": "current", "current": __version__,
            "version": remote or __version__}


def download_update(asset_url: str) -> dict:
    """Download an update asset into the app's downloads folder. Returns the path
    (does not install — the user applies it)."""
    if not asset_url:
        return {"error": "no asset url"}
    if not _scheme_ok(asset_url):
        return {"error": f"refusing non-http(s) download URL: "
                         f"{urllib.parse.urlparse(asset_url).scheme or 'none'}"}
    dest_dir = config.app_dir() / "downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(urllib.parse.urlparse(asset_url).path).name
    try:
        urllib.request.urlretrieve(asset_url, dest)
        return {"ok": True, "path": str(dest)}
    except Exception as e:
        return {"error": str(e)}


# ── In-place self-update (packaged .exe only) ────────────────────────────────

def is_frozen() -> bool:
    """True when running as the packaged PyInstaller build (there's an .exe to
    replace in place); False when running from source."""
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path | None:
    """Folder to replace during an in-place update (where ARIA2.exe lives)."""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_and_install(asset_url: str, sha256: str | None = None, on_status=None) -> dict:
    """Download the update zip, verify its SHA-256 (if provided), stage it, and
    hand off to a detached helper that waits for ARIA to exit, backs up the
    current build, copies the new build over the install folder, relaunches, and
    rolls back if the new build fails to start. On {"ok": True, "relaunch": True}
    the CALLER must quit the app so the locked files can be replaced.

    Packaged build only — from source it returns an error (update with git)."""
    def status(msg: str) -> None:
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    if not asset_url or not _scheme_ok(asset_url):
        return {"error": "Invalid or non-http(s) download URL."}
    if not is_frozen():
        return {"error": "In-place update only works in the packaged app — "
                         "you're running from source. Update with git instead."}
    dest = install_root()
    if dest is None or not dest.exists():
        return {"error": "Could not locate the install folder to update."}
    try:
        work = Path(tempfile.mkdtemp(prefix="aria2_update_"))
        zip_path = work / "update.zip"
        status("Downloading update…")
        req = urllib.request.Request(
            asset_url, headers={"User-Agent": f"ARIA2/{__version__}"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(zip_path, "wb") as f:
            shutil.copyfileobj(resp, f)
        # Integrity check — refuse to install a corrupt / tampered download.
        if sha256:
            status("Verifying…")
            actual = _sha256_file(zip_path)
            if actual.lower() != sha256.strip().lower():
                return {"error": "Downloaded update failed its SHA-256 integrity "
                                 f"check (expected {sha256[:12]}…, got {actual[:12]}…). "
                                 "Update aborted; your install is untouched."}
        status("Extracting…")
        staging = work / "new"
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(staging)
        # Locate the folder containing ARIA2.exe (the zip may be flat or nested).
        src = staging
        if not (src / "ARIA2.exe").exists():
            found = next((p.parent for p in staging.rglob("ARIA2.exe")), None)
            if not found:
                return {"error": "Update package did not contain ARIA2.exe."}
            src = found
        status("Installing — ARIA will restart…")
        bat = _write_updater_bat(work, os.getpid(), src, dest, work / "backup")
        # Detached + own process group so it outlives this process and can
        # replace the (now-unlocked, after we exit) install files.
        subprocess.Popen(["cmd", "/c", str(bat)],
                         creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_GROUP
        return {"ok": True, "relaunch": True}
    except Exception as e:
        return {"error": str(e)}


def _write_updater_bat(work: Path, pid: int, src: Path, dest: Path, backup: Path) -> Path:
    """Write the helper batch script. Once ARIA (pid) exits it: backs up the
    current build, installs the new build over it, relaunches ARIA2.exe, and —
    if the new build isn't running a few seconds later — rolls back to the
    backup and relaunches that. Best-effort, but a bad update can't brick it."""
    exe = f"{dest}\\ARIA2.exe"
    bat = work / "aria2_update.bat"
    script = (
        "@echo off\r\n"
        "chcp 65001 >NUL\r\n"
        ":waitloop\r\n"
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >NUL\r\n"
        "  goto waitloop\r\n"
        ")\r\n"
        "ping -n 3 127.0.0.1 >NUL\r\n"
        ":: back up the current build before overwriting it\r\n"
        f'robocopy "{dest}" "{backup}" /E /R:1 /W:1 /NFL /NDL /NJH /NJS >NUL\r\n'
        ":: install the new build\r\n"
        f'robocopy "{src}" "{dest}" /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS >NUL\r\n'
        f'start "" "{exe}"\r\n'
        ":: give it time to come up, then roll back if it didn\'t\r\n"
        "ping -n 14 127.0.0.1 >NUL\r\n"
        'tasklist /FI "IMAGENAME eq ARIA2.exe" 2>NUL | find /I "ARIA2.exe" >NUL\r\n'
        "if errorlevel 1 (\r\n"
        f'  robocopy "{backup}" "{dest}" /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS >NUL\r\n'
        f'  start "" "{exe}"\r\n'
        ")\r\n"
        f'rmdir /S /Q "{backup}" >NUL 2>&1\r\n'
    )
    bat.write_text(script, encoding="utf-8")
    return bat
