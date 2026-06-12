"""Entry point: `python -m aria2`.

Initialises the database (schema + seed) then launches the desktop client.
Run headless smoke checks instead with `python -m aria2 --smoke`.
"""

from __future__ import annotations

import sys

from aria2.core import db


def main() -> int:
    smoke = "--smoke" in sys.argv
    if not smoke:
        # File logging — the windowed app has no console, so this is the only
        # place failures are recorded. (Skipped for --smoke to keep it isolated.)
        from aria2.core import logs
        logs.setup()
        logs.attach_bus()
        logs.get("app").info(logs.j("startup"))
    db.init()
    # Ensure the app icon exists (generates if first run or missing).
    try:
        from pathlib import Path
        ico = Path(__file__).resolve().parent / "assets" / "aria2.ico"
        if not ico.exists():
            from scripts.make_icon import make
            make()
    except Exception:
        pass
    if smoke:
        from aria2.smoke import run_smoke

        return run_smoke()
    from aria2.ui.app import ARIAApp

    app = ARIAApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
