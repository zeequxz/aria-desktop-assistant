"""
agent/computer_tools.py - Computer control tools (mouse, keyboard, screenshot).

These are used by the Computer Use agent to automate GUI tasks.
All actions are logged so the user can see what's happening.
"""

import base64
import io
import time
import subprocess
import os
from typing import Optional

# These imports are guarded so the app doesn't crash if not installed
try:
    import pyautogui
    pyautogui.FAILSAFE = True  # Move mouse to top-left corner to abort
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    from PIL import ImageGrab
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def _check_available() -> Optional[str]:
    if not PYAUTOGUI_AVAILABLE:
        return "pyautogui is not installed. Run: pip install pyautogui"
    return None


# ── Screenshot ─────────────────────────────────────────────────────────────

def take_screenshot(region: Optional[dict] = None) -> dict:
    """Take a screenshot and return it as base64. Used by Claude Computer Use."""
    try:
        if PIL_AVAILABLE:
            if region:
                img = ImageGrab.grab(bbox=(region["x"], region["y"],
                                          region["x"] + region["width"],
                                          region["y"] + region["height"]))
            else:
                img = ImageGrab.grab()
        elif PYAUTOGUI_AVAILABLE:
            img = pyautogui.screenshot()
        else:
            return {"error": "Neither Pillow nor pyautogui available for screenshots"}

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        return {
            "success": True,
            "image_base64": b64,
            "width": img.width,
            "height": img.height,
            "format": "PNG",
        }
    except Exception as e:
        return {"error": str(e)}


# ── Mouse ──────────────────────────────────────────────────────────────────

def mouse_move(x: int, y: int, duration: float = 0.3) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return {"success": True, "moved_to": {"x": x, "y": y}}
    except Exception as e:
        return {"error": str(e)}


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.click(x, y, button=button, clicks=clicks, interval=0.1)
        return {"success": True, "clicked": {"x": x, "y": y, "button": button, "clicks": clicks}}
    except Exception as e:
        return {"error": str(e)}


def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.drag(start_x, start_y, end_x - start_x, end_y - start_y,
                       duration=duration, button="left")
        return {"success": True, "dragged": {"from": {"x": start_x, "y": start_y}, "to": {"x": end_x, "y": end_y}}}
    except Exception as e:
        return {"error": str(e)}


def mouse_scroll(x: int, y: int, clicks: int = 3) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.scroll(clicks, x=x, y=y)
        return {"success": True, "scrolled": clicks}
    except Exception as e:
        return {"error": str(e)}


# ── Keyboard ───────────────────────────────────────────────────────────────

def keyboard_type(text: str, interval: float = 0.03) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.typewrite(text, interval=interval)
        return {"success": True, "typed": text[:50] + "..." if len(text) > 50 else text}
    except Exception as e:
        # Fallback for unicode
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            return {"success": True, "typed_via_clipboard": True}
        except Exception:
            return {"error": str(e)}


def keyboard_hotkey(*keys: str) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.hotkey(*keys)
        return {"success": True, "hotkey": "+".join(keys)}
    except Exception as e:
        return {"error": str(e)}


def keyboard_press(key: str) -> dict:
    err = _check_available()
    if err:
        return {"error": err}
    try:
        pyautogui.press(key)
        return {"success": True, "pressed": key}
    except Exception as e:
        return {"error": str(e)}


# ── App control ────────────────────────────────────────────────────────────

def launch_app(app_name: str) -> dict:
    """Launch an application by name on Windows."""
    try:
        if os.name == "nt":
            subprocess.Popen(["start", app_name], shell=True)
        else:
            subprocess.Popen([app_name])
        time.sleep(1)  # Give it a moment to open
        return {"success": True, "launched": app_name}
    except Exception as e:
        return {"error": str(e)}


def get_screen_size() -> dict:
    if PYAUTOGUI_AVAILABLE:
        w, h = pyautogui.size()
        return {"width": w, "height": h}
    return {"error": "pyautogui not available"}


def wait_seconds(seconds: float) -> dict:
    time.sleep(seconds)
    return {"success": True, "waited": seconds}


def clipboard_set(text: str) -> dict:
    try:
        import pyperclip
        pyperclip.copy(text)
        return {"success": True}
    except ImportError:
        return {"error": "pyperclip not installed"}


def clipboard_get() -> dict:
    try:
        import pyperclip
        return {"success": True, "content": pyperclip.paste()}
    except ImportError:
        return {"error": "pyperclip not installed"}


# ── Tool registry ──────────────────────────────────────────────────────────

COMPUTER_TOOLS = {
    "take_screenshot": take_screenshot,
    "mouse_move": mouse_move,
    "mouse_click": mouse_click,
    "mouse_drag": mouse_drag,
    "mouse_scroll": mouse_scroll,
    "keyboard_type": keyboard_type,
    "keyboard_hotkey": keyboard_hotkey,
    "keyboard_press": keyboard_press,
    "launch_app": launch_app,
    "get_screen_size": get_screen_size,
    "wait_seconds": wait_seconds,
    "clipboard_set": clipboard_set,
    "clipboard_get": clipboard_get,
}

COMPUTER_TOOL_SCHEMAS = [
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the current screen to see what's on it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "object",
                    "description": "Optional region to capture: {x, y, width, height}",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                }
            },
        },
    },
    {
        "name": "mouse_click",
        "description": "Click the mouse at screen coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "description": "Number of clicks (2 for double-click)", "default": 1},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "mouse_move",
        "description": "Move the mouse to screen coordinates without clicking.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "duration": {"type": "number", "description": "Seconds for the movement", "default": 0.3},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "mouse_scroll",
        "description": "Scroll the mouse wheel at a position.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "clicks": {"type": "integer", "description": "Positive = scroll up, negative = scroll down"},
            },
            "required": ["x", "y", "clicks"],
        },
    },
    {
        "name": "keyboard_type",
        "description": "Type text using the keyboard. Works like a human typing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "keyboard_hotkey",
        "description": "Press a keyboard shortcut like Ctrl+C, Alt+F4, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keys to press together, e.g. ['ctrl', 'c'] or ['alt', 'f4']",
                },
            },
            "required": ["keys"],
        },
    },
    {
        "name": "keyboard_press",
        "description": "Press a single key like Enter, Escape, Tab, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name: enter, escape, tab, space, up, down, left, right, delete, backspace, etc."},
            },
            "required": ["key"],
        },
    },
    {
        "name": "launch_app",
        "description": "Launch an application by name (e.g. 'notepad', 'calc', 'chrome', 'excel').",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Application name or executable"},
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "get_screen_size",
        "description": "Get the screen resolution to understand screen coordinates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "wait_seconds",
        "description": "Wait for a number of seconds (e.g. for an app to load).",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "How long to wait"},
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "clipboard_set",
        "description": "Copy text to the clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "clipboard_get",
        "description": "Get the current clipboard content.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
