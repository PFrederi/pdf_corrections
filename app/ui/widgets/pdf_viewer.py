from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable, Optional
import fitz
from PIL import Image, ImageTk

PageCallback = Callable[[int, float, float], None]  # (page_index, x_pt, y_pt)
ContextCallback = Callable[[int, float, float, int, int], None]  # (page_index, x_pt, y_pt, x_root, y_root)


class PDFViewer(ttk.Frame):
    def __init__(self, master, bg: str, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self._bg = bg

        self._doc: fitz.Document | None = None
        self._pdf_path: Path | None = None
        self._zoom: float = 1.2

        self._click_cb: Optional[PageCallback] = None
        self._drag_cb: Optional[PageCallback] = None
        self._release_cb: Optional[PageCallback] = None
        self._context_cb: Optional[ContextCallback] = None

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(8, 4))

        ttk.Label(bar, text="Zoom:").pack(side="left")
        self.zoom_var = tk.DoubleVar(value=self._zoom)

        ttk.Button(bar, text="−", command=self.zoom_out).pack(side="left", padx=(8, 2))
        ttk.Button(bar, text="+", command=self.zoom_in).pack(side="left", padx=2)

        self.zoom_scale = ttk.Scale(
            bar, from_=0.6, to=2.6, orient="horizontal",
            variable=self.zoom_var, command=self._on_zoom_scale
        )
        self.zoom_scale.pack(side="left", fill="x", expand=True, padx=8)

        self.info_lbl = ttk.Label(bar, text="")
        self.info_lbl.pack(side="right")

        self.canvas = tk.Canvas(self, bg=self._bg, highlightthickness=0)
        self.v_scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.v_scroll.set)

        self.v_scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # scroll (molette) : géré globalement par AppWindow pour éviter de voler la molette partout

        self._page_imgs: list[ImageTk.PhotoImage] = []
        self._page_labels: list[tk.Label] = []

    # ---- public API ----
    def set_click_callback(self, cb: Optional[PageCallback]) -> None:
        # Compat (ancienne API)
        self.set_interaction_callbacks(click_cb=cb, drag_cb=None, release_cb=None, context_cb=None)

    def set_interaction_callbacks(
        self,
        click_cb: Optional[PageCallback] = None,
        drag_cb: Optional[PageCallback] = None,
        release_cb: Optional[PageCallback] = None,
        context_cb: Optional[ContextCallback] = None,
    ) -> None:
        self._click_cb = click_cb
        self._drag_cb = drag_cb
        self._release_cb = release_cb
        self._context_cb = context_cb

        cursor = "crosshair" if (click_cb or drag_cb or release_cb or context_cb) else ""
        for lbl in self._page_labels:
            lbl.configure(cursor=cursor)

    def open_pdf(self, pdf_path: Path) -> None:
        self._pdf_path = pdf_path
        if self._doc:
            self._doc.close()
        self._doc = fitz.open(str(pdf_path))
        self.info_lbl.config(text=f"{self._doc.page_count} page(s)")
        self._render_all_pages()

    def clear(self) -> None:
        if self._doc:
            self._doc.close()
        self._doc = None
        self._pdf_path = None
        self.info_lbl.config(text="")
        self._clear_pages()

    def zoom_in(self) -> None:
        self._zoom = min(2.6, self._zoom + 0.1)
        self.zoom_var.set(self._zoom)
        self._render_all_pages()

    def zoom_out(self) -> None:
        self._zoom = max(0.6, self._zoom - 0.1)
        self.zoom_var.set(self._zoom)
        self._render_all_pages()

    def _on_zoom_scale(self, _val) -> None:
        self._zoom = float(self.zoom_var.get())
        self._render_all_pages()

    def _clear_pages(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._page_imgs.clear()
        self._page_labels.clear()
        self._update_scrollregion()

    def _render_all_pages(self) -> None:
        if not self._doc:
            return
        self._clear_pages()

        cursor = "crosshair" if (self._click_cb or self._drag_cb or self._release_cb or self._context_cb) else ""

        for i in range(self._doc.page_count):
            page = self._doc.load_page(i)
            mat = fitz.Matrix(self._zoom, self._zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            tk_img = ImageTk.PhotoImage(img)

            lbl = tk.Label(self.inner, image=tk_img, bg=self._bg, bd=0, highlightthickness=0)
            lbl.pack(pady=(6, 6), padx=10)

            lbl.configure(cursor=cursor)
            lbl.bind("<Button-1>", lambda e, idx=i: self._handle_click(idx, e))
            lbl.bind("<B1-Motion>", lambda e, idx=i: self._handle_drag(idx, e))
            lbl.bind("<ButtonRelease-1>", lambda e, idx=i: self._handle_release(idx, e))

            # clic-droit / menu contextuel
            lbl.bind("<Button-3>", lambda e, idx=i: self._handle_context(idx, e))        # Windows/Linux
            lbl.bind("<Button-2>", lambda e, idx=i: self._handle_context(idx, e))        # macOS (souvent)
            lbl.bind("<Control-Button-1>", lambda e, idx=i: self._handle_context(idx, e))# macOS (ctrl+clic)

            self._page_imgs.append(tk_img)
            self._page_labels.append(lbl)

        self.inner.update_idletasks()
        self._update_scrollregion()

    def _event_to_pt(self, event) -> tuple[float, float]:
        x_pt = float(event.x) / float(self._zoom)
        y_pt = float(event.y) / float(self._zoom)
        return x_pt, y_pt

    def _handle_click(self, page_index: int, event) -> None:
        if not self._click_cb:
            return
        x_pt, y_pt = self._event_to_pt(event)
        self._click_cb(page_index, x_pt, y_pt)

    def _handle_drag(self, page_index: int, event) -> None:
        if not self._drag_cb:
            return
        x_pt, y_pt = self._event_to_pt(event)
        self._drag_cb(page_index, x_pt, y_pt)

    def _handle_release(self, page_index: int, event) -> None:
        if not self._release_cb:
            return
        x_pt, y_pt = self._event_to_pt(event)
        self._release_cb(page_index, x_pt, y_pt)

    def _handle_context(self, page_index: int, event) -> None:
        if not self._context_cb:
            return
        x_pt, y_pt = self._event_to_pt(event)
        # x_root/y_root = coordonnées écran pour placer le menu
        self._context_cb(page_index, x_pt, y_pt, int(getattr(event, "x_root", 0)), int(getattr(event, "y_root", 0)))

    def _update_scrollregion(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_inner_configure(self, _event=None) -> None:
        self._update_scrollregion()

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.inner_id, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event) -> None:
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
