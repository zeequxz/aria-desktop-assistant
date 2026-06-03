"""ui/theme.py - Central design tokens for the desktop client.

One place for colours/spacing/fonts so views stay consistent and a future
theme switch is a single edit. Dark-first, with an accent pulled from config.
"""

from __future__ import annotations

from aria2.core import config

# ── Palette (dark, layered) ──────────────────────────────────────────────────
BG = "#0b0d12"          # window background (deepest)
SIDEBAR = "#0e1117"     # nav rail — distinct from content
SURFACE = "#14171f"     # panels / cards
SURFACE_2 = "#1b1f29"   # raised elements (inputs, list rows)
HOVER = "#222734"       # hover state
BORDER = "#262b36"
TEXT = "#eef1f6"
TEXT_DIM = "#9aa3b2"
TEXT_FAINT = "#5b6472"
USER_BUBBLE = "#243a63"      # accent-tinted
ASSISTANT_BUBBLE = "#161b24"
DANGER = "#ff6b6b"
SUCCESS = "#5dd6a0"
WARN = "#ffcf6c"


def accent() -> str:
    return config.get("accent", "#6c8fff")


def accent_soft() -> str:
    """A dim, accent-tinted fill for selected/active surfaces."""
    return "#1d2740"


def font_size() -> int:
    return config.get("font_size", 13)


FONT = "Segoe UI"
MONO = "Cascadia Code"


def f(size_delta: int = 0, weight: str = "normal") -> tuple:
    return (FONT, font_size() + size_delta, weight)


def mono(size_delta: int = 0) -> tuple:
    return (MONO, font_size() + size_delta)


RADIUS = 10
PAD = 12
