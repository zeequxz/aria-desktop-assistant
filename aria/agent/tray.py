"""
agent/tray.py - Windows system tray icon + notifications.

Runs ARIA in the system tray so it stays running when the window is minimized.
Uses pystray for the tray icon and win10toast (or fallback) for notifications.
"""

import threading
from typing import Callable, Optional

PYSTRAY_AVAILABLE = False
try:
    import pystray
    from PIL import Image, ImageDraw
    PYSTRAY_AVAILABLE = True
except ImportError:
    pass

TOAST_AVAILABLE = False
try:
    from win10toast import ToastNotifier
    _toaster = ToastNotifier()
    TOAST_AVAILABLE = True
except ImportError:
    pass


def _make_icon_image(size=64, color="#6c8fff"):
    """Generate a simple square icon for the tray."""
    try:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        draw.ellipse([4, 4, size - 4, size - 4], fill=(r, g, b, 255))
        # Letter A
        draw.text((size // 2 - 7, size // 2 - 10), "A", fill=(255, 255, 255, 255))
        return img
    except Exception:
        return None


class TrayManager:
    def __init__(
        self,
        on_show: Callable,
        on_quit: Callable,
        on_new_chat: Callable,
    ):
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_new_chat = on_new_chat
        self._icon = None
        self._thread = None

    def start(self):
        if not PYSTRAY_AVAILABLE:
            print("[Tray] pystray not available — tray icon disabled")
            return
        img = _make_icon_image()
        if img is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open ARIA", self._do_show, default=True),
            pystray.MenuItem("New Chat", self._do_new_chat),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._do_quit),
        )
        self._icon = pystray.Icon("ARIA", img, "ARIA — Personal AI Assistant", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def update_status(self, status: str):
        """Update the tray tooltip."""
        if self._icon:
            try:
                self._icon.title = f"ARIA — {status}"
            except Exception:
                pass

    def _do_show(self, icon, item):
        try:
            self.on_show()
        except Exception:
            pass

    def _do_new_chat(self, icon, item):
        try:
            self.on_new_chat()
        except Exception:
            pass

    def _do_quit(self, icon, item):
        self.stop()
        try:
            self.on_quit()
        except Exception:
            pass


def send_notification(title: str, message: str, duration: int = 5):
    """Send a Windows desktop notification."""
    if TOAST_AVAILABLE:
        try:
            threading.Thread(
                target=_toaster.show_toast,
                args=(title, message),
                kwargs={"duration": duration, "threaded": True},
                daemon=True,
            ).start()
            return
        except Exception:
            pass
    # Fallback: PowerShell toast (Windows 10+)
    try:
        import subprocess
        ps = f"""
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
        $text = $xml.GetElementsByTagName('text')
        $text[0].AppendChild($xml.CreateTextNode('{title}')) | Out-Null
        $text[1].AppendChild($xml.CreateTextNode('{message[:100]}')) | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ARIA').Show($toast)
        """
        subprocess.run(["powershell", "-Command", ps], capture_output=True, timeout=5)
    except Exception:
        print(f"[Notification] {title}: {message}")
