"""ui/views/paned_view.py - Reusable horizontal PanedWindow helper.

Replaces the grid body-frame pattern (left.grid col=0, right.grid col=1) with
a native tk.PanedWindow so every two-panel view gets smooth, C-level resize.
Width is persisted per-view via a config key.

Usage:
    from aria2.ui.views.paned_view import make_paned
    left, right = make_paned(parent, "sidebar_agents_width",
                             default_w=240, min_w=160, max_w=480)
    # build content into `left` and `right` exactly as before
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from aria2.core import config
from aria2.ui import theme


def make_paned(
    parent,
    config_key: str,
    default_w: int = 240,
    min_w: int = 160,
    max_w: int = 480,
    left_kwargs: dict | None = None,
    right_kwargs: dict | None = None,
    padx: int = 24,
    pady: tuple = (8, 8),
) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """Create a horizontal PanedWindow inside `parent` and return (left, right).

    The PanedWindow fills `parent`.  The left pane uses `config_key` to persist
    its width; `min_w` / `max_w` clamp it.  On each sash release the width is
    saved automatically.  Both returned frames can be used as parents for widget
    construction exactly like the old grid-based left/right frames.
    """
    w = max(min_w, min(max_w, int(config.get(config_key, default_w))))

    paned = tk.PanedWindow(
        parent, orient=tk.HORIZONTAL,
        sashwidth=5, sashrelief="flat",
        bg=theme.BORDER, bd=0, borderwidth=0,
        handlesize=0, sashpad=0,
    )
    paned.pack(fill="both", expand=True, padx=padx, pady=pady)

    lkw = {"fg_color": theme.SURFACE, "corner_radius": theme.RADIUS}
    lkw.update(left_kwargs or {})
    left = ctk.CTkFrame(paned, width=w, **lkw)
    left.pack_propagate(False)

    rkw = {"fg_color": "transparent"}
    rkw.update(right_kwargs or {})
    right = ctk.CTkFrame(paned, **rkw)

    paned.add(left,  minsize=min_w, width=w, stretch="never")
    paned.add(right, minsize=300,            stretch="always")

    def _save(e=None):
        try:
            nw = int(paned.sash_coord(0)[0])
            config.set_key(config_key, max(min_w, min(max_w, nw)))
        except Exception:
            pass

    paned.bind("<ButtonRelease-1>", _save)
    return left, right
