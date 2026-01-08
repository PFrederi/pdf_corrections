from __future__ import annotations

"""Insertion d'images dans un PDF via PyMuPDF.

Ce module isole la logique d'insertion d'images de `pdf_annotate.py`.
Il tente d'être compatible avec plusieurs versions de PyMuPDF.
"""

from pathlib import Path
from typing import Optional

import fitz


def insert_image(
    page: "fitz.Page",
    rect: "fitz.Rect",
    image_path: str | Path,
    *,
    keep_proportion: bool = True,
    overlay: bool = True,
    opacity: float | None = None,
) -> None:
    """Insère une image sur une page.

    - `rect` est en points PDF.
    - `opacity` n'est pas disponible sur toutes les versions : fallback silencieux.
    """
    p = Path(image_path).expanduser()
    if not p.exists() or not p.is_file():
        return

    r = fitz.Rect(rect)
    r.normalize()
    if r.is_empty or r.get_area() <= 1:
        return

    kwargs = {
        "filename": str(p),
        "overlay": bool(overlay),
        "keep_proportion": bool(keep_proportion),
    }

    # Selon la version de PyMuPDF, certains arguments peuvent ne pas exister.
    if opacity is not None:
        try:
            kwargs["opacity"] = float(opacity)
        except Exception:
            pass

    try:
        page.insert_image(r, **kwargs)
        return
    except TypeError:
        # fallback : retire opacity si non supporté
        kwargs.pop("opacity", None)
        try:
            page.insert_image(r, **kwargs)
            return
        except Exception:
            return
    except Exception:
        return
