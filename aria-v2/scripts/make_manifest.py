"""scripts/make_manifest.py - Generate the auto-update manifest from a build.

Writes latest.json = {"version", "url", "notes"} that the in-app updater
(services/update_service) polls. Run by the release workflow after building +
zipping; can also be run locally.

    python scripts/make_manifest.py --version 2.0.0 \
        --url https://.../ARIA2-2.0.0.zip --notes "..." --out latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _version_from_package() -> str:
    # Allow running from the repo root or the aria-v2 dir.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        import aria2

        return aria2.__version__
    except Exception:
        return "0.0.0"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default="latest.json")
    a = ap.parse_args(argv)

    version = a.version or _version_from_package()
    manifest = {"version": version, "url": a.url, "notes": a.notes}
    Path(a.out).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
