"""runtime/tools/computer_tools.py - Control the PC (mouse/keyboard/screen).

Ports v1's computer-use capability. Backed by pyautogui (optional dependency);
if it's missing the tools load but return a clear error rather than crashing a
run. All default to "ask" — they're high-risk — so they only run when a human
approves (GUI dialog) or a run explicitly allows them via policy_overrides
(e.g. a Telegram session set to "full access"). In "restricted" sessions these
tools aren't offered at all.
"""

from __future__ import annotations

from pathlib import Path

from aria2.core import config
from aria2.core.ids import new_id
from aria2.runtime.tools.base import Tool

try:
    import pyautogui

    pyautogui.FAILSAFE = True  # slam mouse to a corner to abort
    AVAILABLE = True
except Exception:  # pragma: no cover - missing display / lib
    AVAILABLE = False


def _need():
    return {"error": "pyautogui not installed — run: pip install pyautogui"}


def make_computer_tools() -> list[Tool]:
    def take_screenshot() -> dict:
        if not AVAILABLE:
            return _need()
        shots = config.app_dir() / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / f"shot_{new_id('s')}.png"
        img = pyautogui.screenshot()
        img.save(path)
        return {"path": str(path), "width": img.width, "height": img.height}

    def get_screen_size() -> dict:
        if not AVAILABLE:
            return _need()
        w, h = pyautogui.size()
        return {"width": w, "height": h}

    def mouse_move(x: int, y: int) -> dict:
        if not AVAILABLE:
            return _need()
        pyautogui.moveTo(int(x), int(y), duration=0.1)
        return {"moved_to": [int(x), int(y)]}

    def mouse_click(x: int = None, y: int = None, button: str = "left",
                    clicks: int = 1) -> dict:
        if not AVAILABLE:
            return _need()
        kw = {"button": button, "clicks": int(clicks)}
        if x is not None and y is not None:
            kw.update(x=int(x), y=int(y))
        pyautogui.click(**kw)
        return {"clicked": True, "button": button, "clicks": int(clicks)}

    def type_text(text: str) -> dict:
        if not AVAILABLE:
            return _need()
        pyautogui.typewrite(text, interval=0.01)
        return {"typed": len(text)}

    def press_key(key: str) -> dict:
        if not AVAILABLE:
            return _need()
        pyautogui.press(key)
        return {"pressed": key}

    def hotkey(keys: list) -> dict:
        if not AVAILABLE:
            return _need()
        pyautogui.hotkey(*[str(k) for k in keys])
        return {"hotkey": keys}

    def scroll(amount: int) -> dict:
        if not AVAILABLE:
            return _need()
        pyautogui.scroll(int(amount))
        return {"scrolled": int(amount)}

    obj = {"type": "object"}
    return [
        Tool("take_screenshot", "Capture the screen to a PNG file; returns its path "
             "and size.", obj, take_screenshot, default_policy="ask"),
        Tool("get_screen_size", "Get the screen resolution.", obj, get_screen_size,
             default_policy="allow"),
        Tool("mouse_move", "Move the mouse to absolute (x, y).",
             {"type": "object", "properties": {"x": {"type": "integer"},
              "y": {"type": "integer"}}, "required": ["x", "y"]},
             mouse_move, default_policy="ask"),
        Tool("mouse_click", "Click the mouse (optionally at x, y).",
             {"type": "object", "properties": {"x": {"type": "integer"},
              "y": {"type": "integer"}, "button": {"type": "string"},
              "clicks": {"type": "integer"}}}, mouse_click, default_policy="ask"),
        Tool("type_text", "Type text at the current focus.",
             {"type": "object", "properties": {"text": {"type": "string"}},
              "required": ["text"]}, type_text, default_policy="ask"),
        Tool("press_key", "Press a single key (e.g. 'enter', 'tab', 'esc').",
             {"type": "object", "properties": {"key": {"type": "string"}},
              "required": ["key"]}, press_key, default_policy="ask"),
        Tool("hotkey", "Press a key combination, e.g. ['ctrl','c'].",
             {"type": "object", "properties": {"keys": {"type": "array",
              "items": {"type": "string"}}}, "required": ["keys"]},
             hotkey, default_policy="ask"),
        Tool("scroll", "Scroll vertically by an amount (positive = up).",
             {"type": "object", "properties": {"amount": {"type": "integer"}},
              "required": ["amount"]}, scroll, default_policy="ask"),
    ]


# Tool names, for messaging access-level policy mapping.
COMPUTER_TOOL_NAMES = ["take_screenshot", "get_screen_size", "mouse_move",
                       "mouse_click", "type_text", "press_key", "hotkey", "scroll"]
