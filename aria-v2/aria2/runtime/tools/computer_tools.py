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


_MAX_TYPE = 5000   # cap a single type_text so a runaway can't flood input
_MAX_SHOTS = 40    # keep only the most recent N screenshots (prevent disk bloat)


def _need():
    return {"error": "pyautogui not installed — run: pip install pyautogui"}


def _act(fn):
    """Run a pyautogui action, converting the user's fail-safe abort and any
    platform error into a clean tool result instead of a raised exception.

    The fail-safe (mouse slammed to a screen corner) is the human's emergency
    stop, so it gets a DISTINCT, explicit signal — otherwise it looked like a
    generic 'Tool failed' the model might simply retry, defeating the abort."""
    try:
        return fn()
    except pyautogui.FailSafeException:
        return {"error": "Aborted by the user's fail-safe (mouse moved to a screen "
                "corner). Stop the current action and ask the user how to proceed.",
                "aborted": True}
    except Exception as e:
        return {"error": f"Computer action failed: {e}"}


_MAX_EDGE = 1568  # downscale the model-facing image to Anthropic's recommended max


def _encode_image(img) -> dict:
    """Base64-encode a (downscaled) PNG of the screenshot for the model to see.
    The full-resolution image is saved to disk separately; this copy is shrunk to
    keep the request small and within the provider's recommended image size."""
    import base64
    import io
    vis = img
    longest = max(img.width, img.height)
    if longest > _MAX_EDGE:
        ratio = _MAX_EDGE / longest
        vis = img.resize((max(1, int(img.width * ratio)),
                          max(1, int(img.height * ratio))))
    buf = io.BytesIO()
    vis.save(buf, format="PNG")
    return {"media_type": "image/png",
            "data": base64.b64encode(buf.getvalue()).decode("ascii")}


def _prune_screenshots(folder: Path, keep: int = _MAX_SHOTS) -> None:
    """Delete all but the newest `keep` screenshots so captures don't grow without
    bound on disk."""
    try:
        shots = sorted(folder.glob("shot_*.png"), key=lambda p: p.stat().st_mtime)
        for old in shots[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass


def make_computer_tools() -> list[Tool]:
    def take_screenshot() -> dict:
        if not AVAILABLE:
            return _need()

        def _do():
            shots = config.app_dir() / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / f"shot_{new_id('s')}.png"
            img = pyautogui.screenshot()
            img.save(path)  # full-resolution copy on disk
            _prune_screenshots(shots)
            out = {"path": str(path), "width": img.width, "height": img.height,
                   "_image": _encode_image(img)}
            return out
        return _act(_do)

    def get_screen_size() -> dict:
        if not AVAILABLE:
            return _need()
        return _act(lambda: dict(zip(("width", "height"), pyautogui.size())))

    def mouse_move(x: int, y: int) -> dict:
        if not AVAILABLE:
            return _need()

        def _do():
            pyautogui.moveTo(int(x), int(y), duration=0.1)
            return {"moved_to": [int(x), int(y)]}
        return _act(_do)

    def mouse_click(x: int = None, y: int = None, button: str = "left",
                    clicks: int = 1) -> dict:
        if button not in ("left", "right", "middle"):
            return {"error": f"Invalid button '{button}' (use left, right, or middle)."}
        if not AVAILABLE:
            return _need()

        def _do():
            kw = {"button": button, "clicks": int(clicks)}
            if x is not None and y is not None:
                kw.update(x=int(x), y=int(y))
            pyautogui.click(**kw)
            return {"clicked": True, "button": button, "clicks": int(clicks)}
        return _act(_do)

    def type_text(text: str) -> dict:
        if len(text) > _MAX_TYPE:
            return {"error": f"text too long ({len(text)} chars > {_MAX_TYPE} limit); "
                    "split it into smaller chunks."}
        if not AVAILABLE:
            return _need()

        def _do():
            pyautogui.typewrite(text, interval=0.01)
            return {"typed": len(text)}
        return _act(_do)

    def press_key(key: str) -> dict:
        if not AVAILABLE:
            return _need()

        def _do():
            pyautogui.press(key)
            return {"pressed": key}
        return _act(_do)

    def hotkey(keys: list) -> dict:
        if not isinstance(keys, list) or not keys:
            return {"error": "keys must be a non-empty list, e.g. ['ctrl','c']."}
        if not AVAILABLE:
            return _need()

        def _do():
            pyautogui.hotkey(*[str(k) for k in keys])
            return {"hotkey": keys}
        return _act(_do)

    def scroll(amount: int) -> dict:
        if not AVAILABLE:
            return _need()

        def _do():
            pyautogui.scroll(int(amount))
            return {"scrolled": int(amount)}
        return _act(_do)

    obj = {"type": "object"}
    return [
        Tool("take_screenshot", "Capture the screen and return it as an image you "
             "can SEE (plus the saved file path and size). Use this to look at the "
             "screen before deciding where to click or type.", obj, take_screenshot,
             default_policy="ask"),
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
