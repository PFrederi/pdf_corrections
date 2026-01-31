"""Small reusable scrollable frame (Canvas + vertical scrollbar).

This keeps large option panels usable without shrinking the main content area.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


class VScrollableFrame(ttk.Frame):
    """A ttk.Frame with an internal vertical scrollbar.

    Access the scrollable content area via the `.inner` attribute.
    """

    def __init__(self, parent, *, height: int = 190):
        super().__init__(parent)

        self._canvas = tk.Canvas(self, highlightthickness=0, height=height)
        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)

        self._vbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="x", expand=True)

        self.inner = ttk.Frame(self._canvas)
        self._win_id = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        # Mousewheel support (bind only while cursor is over the widget)
        self._canvas.bind("<Enter>", self._bind_mousewheel)
        self._canvas.bind("<Leave>", self._unbind_mousewheel)
        self.inner.bind("<Enter>", self._bind_mousewheel)
        self.inner.bind("<Leave>", self._unbind_mousewheel)

    def scroll_to_top(self) -> None:
        try:
            self._canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _on_inner_configure(self, _event=None) -> None:
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas_configure(self, event) -> None:
        # Keep inner frame width in sync with canvas width
        try:
            self._canvas.itemconfigure(self._win_id, width=event.width)
        except Exception:
            pass

    # --- mouse wheel handling ---
    def _bind_mousewheel(self, _event=None) -> None:
        try:
            # Windows/macOS
            self.bind_all("<MouseWheel>", self._on_mousewheel, add=True)
            # Linux
            self.bind_all("<Button-4>", self._on_mousewheel_linux, add=True)
            self.bind_all("<Button-5>", self._on_mousewheel_linux, add=True)
        except Exception:
            pass

    def _unbind_mousewheel(self, _event=None) -> None:
        try:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")
        except Exception:
            pass

    def _on_mousewheel(self, event) -> None:
        try:
            # On Windows delta is ±120 multiples; on macOS it's smaller.
            delta = int(event.delta)
            if delta == 0:
                return
            # Normalize
            step = -1 if delta > 0 else 1
            # macOS often needs bigger step
            if sys.platform == "darwin":
                step *= 2
            self._canvas.yview_scroll(step, "units")
        except Exception:
            pass

    def _on_mousewheel_linux(self, event) -> None:
        try:
            if event.num == 4:
                self._canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self._canvas.yview_scroll(1, "units")
        except Exception:
            pass
