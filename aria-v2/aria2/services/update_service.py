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
import urllib.parse
import urllib.request
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
                "notes": manifest.get("notes", ""), "current": __version__}
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
                "url": manifest.get("url", ""), "notes": manifest.get("notes", "")}
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
