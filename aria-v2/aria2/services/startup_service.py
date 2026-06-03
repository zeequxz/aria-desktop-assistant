"""services/startup_service.py - Windows auto-start on login.

Adds / removes a registry entry under
  HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run
so ARIA v2 launches automatically when the user logs in. Works with both the
packaged exe (dist\\ARIA2\\ARIA2.exe) and a source install (pythonw.exe -m aria2).
"""

from __future__ import annotations

import sys
from pathlib import Path

_KEY  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_NAME = "ARIA2"


def _command() -> str:
    """Return the command string to register as the startup entry."""
    if getattr(sys, "frozen", False):
        # Running from the PyInstaller bundle — use the exe directly.
        return f'"{sys.executable}"'
    # Running from source — use pythonw.exe (no console window).
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    cwd = Path(__file__).resolve().parents[2]  # the aria-v2 directory
    return f'"{pythonw}" -m aria2 --workdir "{cwd}"'


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0,
                            winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _NAME)
            return bool(val)
    except Exception:
        return False


def enable() -> dict:
    try:
        import winreg
        cmd = _command()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _NAME, 0, winreg.REG_SZ, cmd)
        return {"ok": True, "command": cmd}
    except ImportError:
        return {"error": "winreg not available (Windows only)"}
    except Exception as e:
        return {"error": str(e)}


def disable() -> dict:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _NAME)
        return {"ok": True}
    except FileNotFoundError:
        return {"ok": True}  # already absent
    except ImportError:
        return {"error": "winreg not available (Windows only)"}
    except Exception as e:
        return {"error": str(e)}


def set_enabled(enabled: bool) -> dict:
    return enable() if enabled else disable()
