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


def check_for_update(manifest_url: str | None = None) -> dict | None:
    """Return {version, url, notes} if a newer version is published, else None."""
    url = manifest_url or config.get("update_manifest_url", "")
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    remote = manifest.get("version", "")
    if remote and is_newer(remote):
        return {"version": remote, "url": manifest.get("url", ""),
                "notes": manifest.get("notes", ""), "current": __version__}
    return None


def download_update(asset_url: str) -> dict:
    """Download an update asset into the app's downloads folder. Returns the path
    (does not install — the user applies it)."""
    if not asset_url:
        return {"error": "no asset url"}
    dest_dir = config.app_dir() / "downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(urllib.parse.urlparse(asset_url).path).name
    try:
        urllib.request.urlretrieve(asset_url, dest)
        return {"ok": True, "path": str(dest)}
    except Exception as e:
        return {"error": str(e)}
