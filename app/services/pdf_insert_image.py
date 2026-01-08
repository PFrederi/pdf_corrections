from __future__ import annotations

"""Service utilitaire : insérer une image dans un PDF.

Ce module était auparavant un stub incorrect. On le garde (nom stable) car il peut
être utile pour des scripts externes / tests.
"""

from pathlib import Path

import fitz


def insert_image_in_pdf(
    pdf_path: str | Path,
    image_path: str | Path,
    output_path: str | Path,
    *,
    page_number: int = 0,
    x: float = 100.0,
    y: float = 100.0,
    width: float | None = None,
    height: float | None = None,
    keep_proportion: bool = True,
    opacity: float | None = None,
) -> None:
    """Insère une image (idéalement PNG) dans un PDF.

    Les coordonnées / tailles sont exprimées en points PDF.
    """
    pdf_path = Path(pdf_path).expanduser().resolve()
    image_path = Path(image_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    if not image_path.exists():
        raise FileNotFoundError(str(image_path))

    doc = fitz.open(str(pdf_path))
    try:
        page_number = int(page_number)
        if page_number < 0 or page_number >= doc.page_count:
            raise ValueError("page_number hors limites")
        page = doc.load_page(page_number)

        # Taille par défaut : on lit les dimensions de l'image via fitz.Pixmap
        w_pt = float(width) if width is not None else None
        h_pt = float(height) if height is not None else None
        if w_pt is None or h_pt is None:
            try:
                pm = fitz.Pixmap(str(image_path))
                # pixmap en pixels -> approx en points (1px ~ 1pt) : OK pour un utilitaire
                if w_pt is None:
                    w_pt = float(pm.width)
                if h_pt is None:
                    h_pt = float(pm.height)
            except Exception:
                w_pt = w_pt or 200.0
                h_pt = h_pt or 200.0

        r = fitz.Rect(float(x), float(y), float(x) + float(w_pt), float(y) + float(h_pt))
        r.normalize()

        kwargs = {
            "filename": str(image_path),
            "overlay": True,
            "keep_proportion": bool(keep_proportion),
        }
        if opacity is not None:
            try:
                kwargs["opacity"] = float(opacity)
            except Exception:
                pass

        try:
            page.insert_image(r, **kwargs)
        except TypeError:
            kwargs.pop("opacity", None)
            page.insert_image(r, **kwargs)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        doc.save(str(output_path), garbage=4, deflate=True)
    finally:
        doc.close()
