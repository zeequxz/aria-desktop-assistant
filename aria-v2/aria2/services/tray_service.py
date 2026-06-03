"""services/tray_service.py - System tray icon + minimize-to-tray (ported from v1).

Uses pystray + Pillow when available; a no-op otherwise. The icon menu can show
the window or quit the app. The app routes window-close to hide-to-tray when the
tray is active, so closing the window keeps ARIA running in the background
(handling Telegram, schedules, heartbeat).
"""

from __future__ import annotations

import threading

try:
    import pystray
    from PIL import Image, ImageDraw

    AVAILABLE = True
except Exception:  # pragma: no cover
    AVAILABLE = False


class Tray:
    def __init__(self):
        self._icon = None
        self._app = None

    @property
    def active(self) -> bool:
        return self._icon is not None

    def _image(self):
        img = Image.new("RGB", (64, 64), "#0f1116")
        d = ImageDraw.Draw(img)
        d.ellipse((14, 14, 50, 50), fill="#6c8fff")
        return img

    def start(self, app):
        from aria2.core import config
        if not AVAILABLE or self._icon is not None or not config.get("tray_enabled", False):
            return
        self._app = app

        def _show(icon, item):
            app.after(0, self._restore)

        def _quit(icon, item):
            icon.stop()
            self._icon = None
            app.after(0, app._real_quit)

        menu = pystray.Menu(
            pystray.MenuItem("Open ARIA", _show, default=True),
            pystray.MenuItem("Quit", _quit),
        )
        self._icon = pystray.Icon("ARIA2", self._image(), "ARIA v2", menu)
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    def _restore(self):
        if self._app:
            self._app.deiconify()
            self._app.lift()

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None


tray = Tray()
