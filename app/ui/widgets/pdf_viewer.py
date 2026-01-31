from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable, Optional

import fitz  # PyMuPDF

# Pillow est généralement disponible dans le bundle (sinon, remplacer par PhotoImage PNG)
from PIL import Image, ImageTk


class PDFViewer(ttk.Frame):
    """
    Viewer PDF simple basé sur un Canvas scrollable.
    - Scroll vertical + horizontal (important en zoom)
    - Affiche toutes les pages empilées verticalement
    - Fournit des callbacks d'interaction (clic/drag/release/context) en coordonnées PDF (points).
    """

    def __init__(self, parent, bg: str = "#111827"):
        super().__init__(parent)
        self._bg = bg

        # Canvas + scrollbars (grid pour placer la barre horizontale en bas)
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(outer, bg=self._bg, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.vbar = ttk.Scrollbar(outer, orient="vertical", command=self._on_yview)
        self.vbar.grid(row=0, column=1, sticky="ns")

        self.hbar = ttk.Scrollbar(outer, orient="horizontal", command=self._on_xview)
        self.hbar.grid(row=1, column=0, sticky="ew")

        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)

        # lazy re-render (pour open_pdf(..., lazy_render=True))
        self._lazy_rerender_enabled: bool = False
        self._lazy_rerender_after_id = None

        # état PDF
        self._doc: Optional[fitz.Document] = None
        self._pdf_path: Optional[Path] = None
        self._zoom: float = 1.0

        # images (références PhotoImage)
        self._img_refs: list[ImageTk.PhotoImage] = []
        # layout pages: list dict {page_index, x0, y0, w_px, h_px, w_pt, h_pt}
        self._layout: list[dict] = []

        # callbacks
        self._click_cb: Optional[Callable[[int, float, float], None]] = None
        self._drag_cb: Optional[Callable[[int, float, float], None]] = None
        self._release_cb: Optional[Callable[[int, float, float], None]] = None
        self._context_cb: Optional[Callable[[int, float, float], None]] = None

        # bindings (zoom + scroll)
        self._bind_scroll()

    # ---------------- Public API ----------------
    def clear(self) -> None:
        self.canvas.delete("all")
        self._img_refs.clear()
        self._layout.clear()
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
        self._doc = None
        self._pdf_path = None
        self._lazy_rerender_enabled = False
        self._lazy_rerender_after_id = None
        self.canvas.configure(scrollregion=(0, 0, 1, 1))

    def open_pdf(
        self,
        pdf_path: str | Path,
        preserve_view: bool = False,
        force_reload: bool = False,
        lazy_render: bool = False,
    ) -> None:
        """Ouvre un PDF.

        preserve_view=True conserve le zoom + la position de scroll actuels (utile après régénération du PDF).
        """
        prev_zoom = float(getattr(self, "_zoom", 1.0) or 1.0)
        try:
            prev_x = self.canvas.xview()
            prev_y = self.canvas.yview()
        except Exception:
            prev_x = (0.0, 1.0)
            prev_y = (0.0, 1.0)

        self._pdf_path = Path(pdf_path)
        self._lazy_rerender_enabled = bool(lazy_render)
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
        # Ouvre le PDF depuis des bytes pour éviter des soucis de verrouillage/caching (surtout en .exe Windows)
        try:
            _data = Path(self._pdf_path).read_bytes()
            self._doc = fitz.open(stream=_data, filetype='pdf')
        except Exception:
            self._doc = fitz.open(str(self._pdf_path))

        if preserve_view:
            prev_zoom = max(0.2, min(6.0, prev_zoom))
            self._zoom = prev_zoom
        else:
            self._zoom = 1.0

        # lazy_render=True : on garde le layout/canvas existant si compatible et on ne re-render
        # que les pages visibles (énorme gain de perf après régénération fréquente).
        self._lazy_rerender_enabled = bool(lazy_render)
        if lazy_render and self._layout and len(self._layout) == self._doc.page_count and preserve_view:
            self._render_visible_pages()
        else:
            self._render_all_pages(reset_view=(not preserve_view))

        if preserve_view:
            try:
                self.canvas.update_idletasks()
                self.canvas.xview_moveto(float(prev_x[0]))
                self.canvas.yview_moveto(float(prev_y[0]))
            except Exception:
                pass

        if lazy_render and preserve_view:
            # après restauration du scroll, re-render à nouveau les pages visibles pour être sûr
            # d'afficher la version à jour (sinon on peut rester sur les anciennes images)
            try:
                self._render_visible_pages()
            except Exception:
                pass

    def _schedule_visible_rerender(self, delay_ms: int = 80) -> None:
        if not self._lazy_rerender_enabled:
            return
        try:
            if self._lazy_rerender_after_id is not None:
                self.after_cancel(self._lazy_rerender_after_id)
        except Exception:
            pass
        try:
            self._lazy_rerender_after_id = self.after(delay_ms, self._render_visible_pages)
        except Exception:
            self._lazy_rerender_after_id = None

    def _on_yview(self, *args):
        """Proxy yview (scrollbar) pour pouvoir déclencher le lazy refresh."""
        try:
            self.canvas.yview(*args)
        finally:
            self._schedule_visible_rerender()

    def _on_xview(self, *args):
        """Proxy xview (scrollbar) pour pouvoir déclencher le lazy refresh."""
        try:
            self.canvas.xview(*args)
        finally:
            self._schedule_visible_rerender()

    def set_interaction_callbacks(
        self,
        click_cb=None,
        drag_cb=None,
        release_cb=None,
        context_cb=None,
    ) -> None:
        self._click_cb = click_cb
        self._drag_cb = drag_cb
        self._release_cb = release_cb
        self._context_cb = context_cb

        # on (re)bind proprement
        self.canvas.unbind("<Button-1>")
        self.canvas.unbind("<B1-Motion>")
        self.canvas.unbind("<ButtonRelease-1>")
        self.canvas.unbind("<Button-3>")
        self.canvas.unbind("<Button-2>")
        self.canvas.unbind("<Control-Button-1>")  # mac trackpad parfois

        if click_cb is not None:
            self.canvas.bind("<Button-1>", self._on_click)
            # mac: ctrl+clic => button-3; mais on laisse aussi click normal
            self.canvas.bind("<Control-Button-1>", self._on_context_menu)

        if drag_cb is not None:
            self.canvas.bind("<B1-Motion>", self._on_drag)

        if release_cb is not None:
            self.canvas.bind("<ButtonRelease-1>", self._on_release)

        if context_cb is not None:
            self.canvas.bind("<Button-3>", self._on_context_menu)
            # macOS: clic droit trackpad peut arriver en Button-2
            self.canvas.bind("<Button-2>", self._on_context_menu)

    # ---------------- Zoom API (public) ----------------
    def get_zoom(self) -> float:
        return float(self._zoom)

    def set_zoom(self, zoom: float, *, reset_view: bool = False) -> None:
        """Définit le zoom (1.0 = 100%)."""
        try:
            z = float(zoom)
        except Exception:
            return
        z = max(0.2, min(6.0, z))
        if abs(z - self._zoom) < 1e-6:
            return
        self._zoom = z
        if self._doc:
            self._render_all_pages(reset_view=reset_view)

    def zoom(self, value: float, *, reset_view: bool = False) -> None:
        """
        Zoom public.
        - Compat boutons: value=+1 => zoom avant, value=-1 => zoom arrière
        - Sinon: value>0 est interprété comme un facteur multiplicatif (ex: 1.1, 0.9)
        """
        if not self._doc:
            return
        try:
            v = float(value)
        except Exception:
            return
        if v == 1.0:
            self.zoom_in()
            return
        if v == -1.0:
            self.zoom_out()
            return
        if v <= 0:
            return
        self.set_zoom(self._zoom * v, reset_view=reset_view)

    def zoom_in(self) -> None:
        self.zoom(1.1, reset_view=False)

    def zoom_out(self) -> None:
        self.zoom(1 / 1.1, reset_view=False)

    def zoom_reset(self) -> None:
        self.set_zoom(1.0, reset_view=False)

    # alias rétro-compatibilité (si d'autres modules appellent zoom_to)
    def zoom_to(self, zoom: float) -> None:
        self.set_zoom(zoom, reset_view=False)

    # ---------------- Rendering ----------------
    def _render_all_pages(self, reset_view: bool = False) -> None:
        self.canvas.delete("all")
        self._img_refs.clear()
        self._layout.clear()

        if not self._doc:
            self.canvas.configure(scrollregion=(0, 0, 1, 1))
            return

        y = 0
        margin = 12
        max_w = 1

        # rendu de toutes les pages empilées
        for i in range(self._doc.page_count):
            page = self._doc.load_page(i)
            rect = page.rect  # points
            w_pt, h_pt = float(rect.width), float(rect.height)

            mat = fitz.Matrix(self._zoom, self._zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            # pix -> PIL -> ImageTk
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            tk_img = ImageTk.PhotoImage(img)

            x0 = 0
            y0 = y
            item_id = self.canvas.create_image(x0, y0, anchor="nw", image=tk_img, tags=(f"pdf_page_{i}",))
            self._img_refs.append(tk_img)

            self._layout.append({
                "page_index": i,
                "x0": x0,
                "y0": y0,
                "w_px": pix.width,
                "h_px": pix.height,
                "w_pt": w_pt,
                "h_pt": h_pt,
                "item_id": item_id,
            })

            y = y0 + pix.height + margin
            max_w = max(max_w, pix.width)

        # scrollregion doit inclure largeur + hauteur (sinon pas de déplacement horizontal)
        total_h = max(1, y)
        self.canvas.configure(scrollregion=(0, 0, max_w, total_h))

        # garder la position sauf demande explicite
        if reset_view:
            try:
                self.canvas.yview_moveto(0.0)
                self.canvas.xview_moveto(0.0)
            except Exception:
                pass

    def _visible_page_indices(self) -> list[int]:
        """Retourne les indices de pages qui intersectent la zone visible du canvas."""
        if not self._layout:
            return []
        try:
            h = int(self.canvas.winfo_height() or 0)
        except Exception:
            h = 0
        if h <= 0:
            return [0]
        try:
            top = float(self.canvas.canvasy(0))
            bot = float(self.canvas.canvasy(h))
        except Exception:
            top, bot = 0.0, 1e9

        out: list[int] = []
        for d in self._layout:
            try:
                y0 = float(d.get("y0", 0.0))
                hp = float(d.get("h_px", 0.0))
                i = int(d.get("page_index", 0))
            except Exception:
                continue
            if (y0 + hp) >= top - 2 and y0 <= bot + 2:
                out.append(i)
        # garde un nombre raisonnable (dans 99% des cas, 1-2 pages)
        if len(out) > 4:
            out = out[:4]
        return out

    def _render_visible_pages(self) -> None:
        """Re-render uniquement les pages visibles (lazy)."""
        if not self._doc or not self._layout:
            return
        for i in self._visible_page_indices():
            self._rerender_page(i)

    def _rerender_page(self, page_index: int) -> None:
        """Re-render d'une page sans recalculer tout le layout."""
        if not self._doc:
            return
        if page_index < 0 or page_index >= self._doc.page_count:
            return
        if not self._layout or page_index >= len(self._layout):
            return

        d = self._layout[page_index]
        item_id = d.get("item_id")
        if not item_id:
            return

        page = self._doc.load_page(page_index)
        mat = fitz.Matrix(self._zoom, self._zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        tk_img = ImageTk.PhotoImage(img)

        # maj image
        try:
            self.canvas.itemconfig(item_id, image=tk_img)
        except Exception:
            # fallback : recrée l'item
            try:
                x0 = float(d.get("x0", 0.0))
                y0 = float(d.get("y0", 0.0))
                self.canvas.delete(item_id)
                new_id = self.canvas.create_image(x0, y0, anchor="nw", image=tk_img, tags=(f"pdf_page_{page_index}",))
                d["item_id"] = new_id
            except Exception:
                pass

        # conserve la référence (sinon garbage collected)
        try:
            self._img_refs[page_index] = tk_img
        except Exception:
            # fallback : étend si nécessaire
            while len(self._img_refs) <= page_index:
                self._img_refs.append(tk_img)
            self._img_refs[page_index] = tk_img

    # ---------------- Zoom / Scroll ----------------
    def _bind_scroll(self) -> None:
        # MouseWheel (Windows/mac) + Button-4/5 (Linux)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel, add="+")
        self.canvas.bind("<Button-4>", self._on_linux_wheel_up, add="+")
        self.canvas.bind("<Button-5>", self._on_linux_wheel_down, add="+")
        self.canvas.bind("<Shift-Button-4>", self._on_linux_shift_wheel_up, add="+")
        self.canvas.bind("<Shift-Button-5>", self._on_linux_shift_wheel_down, add="+")

    def _on_mousewheel(self, e):
        # vertical scroll
        delta = e.delta
        if sys.platform == "darwin":
            # sur mac, delta est souvent petit -> multiplier un peu
            step = int(-1 * (delta))
        else:
            step = int(-1 * (delta / 120))
        self.canvas.yview_scroll(step, "units")
        self._schedule_visible_rerender()

    def _on_shift_mousewheel(self, e):
        # horizontal scroll (shift + wheel)
        delta = e.delta
        if sys.platform == "darwin":
            step = int(-1 * (delta))
        else:
            step = int(-1 * (delta / 120))
        self.canvas.xview_scroll(step, "units")
        self._schedule_visible_rerender()

    def _on_ctrl_mousewheel(self, e):
        # zoom (Ctrl + wheel)
        if not self._doc:
            return
        if e.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()
    def _on_linux_wheel_up(self, _e):
        self.canvas.yview_scroll(-3, "units")
        self._schedule_visible_rerender()

    def _on_linux_wheel_down(self, _e):
        self.canvas.yview_scroll(3, "units")
        self._schedule_visible_rerender()

    def _on_linux_shift_wheel_up(self, _e):
        self.canvas.xview_scroll(-3, "units")
        self._schedule_visible_rerender()

    def _on_linux_shift_wheel_down(self, _e):
        self.canvas.xview_scroll(3, "units")
        self._schedule_visible_rerender()

    # ---------------- Coordinate mapping + events ----------------
    def _event_to_canvas_xy(self, e) -> tuple[float, float]:
        # coordonnées dans le canvas (en tenant compte du scroll)
        cx = self.canvas.canvasx(e.x)
        cy = self.canvas.canvasy(e.y)
        return float(cx), float(cy)

    def _canvas_to_pdf(self, cx: float, cy: float) -> tuple[int, float, float]:
        """
        Retourne (page_index, x_pt, y_pt) à partir de coordonnées canvas (pixels).
        Si hors pages, on clamp au plus proche.
        """
        if not self._layout:
            return 0, 0.0, 0.0

        # trouver la page dont le y couvre cy
        page = self._layout[-1]
        for info in self._layout:
            y0 = info["y0"]
            y1 = y0 + info["h_px"]
            if y0 <= cy <= y1:
                page = info
                break
            if cy < y0:
                page = info
                break

        px_x = cx - page["x0"]
        px_y = cy - page["y0"]

        # clamp
        px_x = max(0.0, min(float(page["w_px"]), px_x))
        px_y = max(0.0, min(float(page["h_px"]), px_y))

        # pixels -> points : / zoom
        x_pt = px_x / self._zoom
        y_pt = px_y / self._zoom

        # clamp pts
        x_pt = max(0.0, min(float(page["w_pt"]), x_pt))
        y_pt = max(0.0, min(float(page["h_pt"]), y_pt))

        return int(page["page_index"]), float(x_pt), float(y_pt)

    def _on_click(self, e):
        if self._click_cb is None:
            return
        cx, cy = self._event_to_canvas_xy(e)
        p, x_pt, y_pt = self._canvas_to_pdf(cx, cy)
        self._click_cb(p, x_pt, y_pt)

    def _on_drag(self, e):
        if self._drag_cb is None:
            return
        cx, cy = self._event_to_canvas_xy(e)
        p, x_pt, y_pt = self._canvas_to_pdf(cx, cy)
        self._drag_cb(p, x_pt, y_pt)

    def _on_release(self, e):
        if self._release_cb is None:
            return
        cx, cy = self._event_to_canvas_xy(e)
        p, x_pt, y_pt = self._canvas_to_pdf(cx, cy)
        self._release_cb(p, x_pt, y_pt)

    def _on_context_menu(self, e):
        if self._context_cb is None:
            return
        cx, cy = self._event_to_canvas_xy(e)
        p, x_pt, y_pt = self._canvas_to_pdf(cx, cy)
        # Compat : certaines implémentations attendent aussi les coordonnées écran (x_root, y_root)
        try:
            self._context_cb(p, x_pt, y_pt, int(getattr(e, "x_root", 0)), int(getattr(e, "y_root", 0)))
        except TypeError:
            try:
                self._context_cb(p, x_pt, y_pt)
            except TypeError:
                # ultime fallback : passer l'événement brut
                try:
                    self._context_cb(e)
                except Exception:
                    pass
