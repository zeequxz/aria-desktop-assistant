"""ui/views/drag_handle.py - Thin draggable resize handle between two panels.

Place it between any two grid columns, bind a callback, and it tracks the drag
delta so the caller can resize its panels. Highlights on hover so users can
discover it. Works with CTk's grid layout.
"""

from __future__ import annotations

import customtkinter as ctk
from aria2.ui import theme


class DragHandle(ctk.CTkFrame):
    """A draggable resize handle. Place in a grid column between two panels.

    Fix 1 — mouse capture: grab_set() on press so motion events are received
    even when the cursor moves off the thin strip. grab_release() on mouse-up.
    Fix 2 — grid re-layout: callers must also call
      parent.grid_columnconfigure(col, minsize=new_w)
    to force the grid manager to apply the new column width.
    """

    NORMAL = theme.BORDER
    HOVER  = theme.accent()

    def __init__(self, parent, on_drag, cursor: str = "sb_h_double_arrow"):
        super().__init__(parent, width=6, cursor=cursor,
                         fg_color=self.NORMAL, corner_radius=0)
        self.grid_propagate(False)
        self._on_drag = on_drag
        self._dragging = False

        self.bind("<Enter>",           self._on_enter)
        self.bind("<Leave>",           self._on_leave)
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<B1-Motion>",       self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def _on_enter(self, _=None):
        self.configure(fg_color=self.HOVER)

    def _on_leave(self, _=None):
        if not self._dragging:
            self.configure(fg_color=self.NORMAL)

    def _press(self, event):
        self._last_x = event.x_root
        self._dragging = True
        self.configure(fg_color=self.HOVER)
        self.grab_set()          # capture ALL mouse events while dragging

    def _motion(self, event):
        if not self._dragging:
            return
        delta = event.x_root - self._last_x
        self._last_x = event.x_root
        if delta != 0:
            self._on_drag(delta)

    def _release(self, _event):
        self._dragging = False
        self.grab_release()      # return events to normal routing
        self.configure(fg_color=self.NORMAL)
