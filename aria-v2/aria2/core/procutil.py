"""core/procutil.py - Stop subprocesses from flashing a console window.

ARIA2 ships as a *windowed* app (PyInstaller console=False), so it has no
console of its own. On Windows, any subprocess that launches a console program
(nvidia-smi, wmic, ollama, git, taskkill, ping, robocopy, …) briefly pops up a
black console window unless CREATE_NO_WINDOW is set. Splat NO_WINDOW into such
calls so they run invisibly:

    subprocess.run([...], **procutil.NO_WINDOW)

On non-Windows it's an empty dict (the flag is Windows-only).
"""

from __future__ import annotations

import os

CREATE_NO_WINDOW = 0x08000000

NO_WINDOW: dict = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
