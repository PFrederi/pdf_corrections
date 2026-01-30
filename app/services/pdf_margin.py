from __future__ import annotations
from pathlib import Path
import fitz  # PyMuPDF


def cm_to_pt(cm: float) -> float:
    return (cm / 2.54) * 72.0


def add_margins(
    input_pdf: Path,
    output_pdf: Path,
    left_cm: float = 0.0,
    right_cm: float = 0.0,
) -> None:
    """Ajoute une marge blanche à gauche et/ou à droite.

    - left_cm : marge ajoutée à gauche (en cm)
    - right_cm : marge ajoutée à droite (en cm)

    Remarque : si left_cm == right_cm == 0, le fichier est simplement recopié.
    """
    left_pt = max(0.0, cm_to_pt(float(left_cm or 0.0)))
    right_pt = max(0.0, cm_to_pt(float(right_cm or 0.0)))

    # Cas trivial : aucune marge
    if left_pt == 0.0 and right_pt == 0.0:
        # On sauvegarde une copie simple pour garder un flux uniforme
        src = fitz.open(str(input_pdf))
        try:
            output_pdf.parent.mkdir(parents=True, exist_ok=True)
            src.save(str(output_pdf))
        finally:
            src.close()
        return

    src = fitz.open(str(input_pdf))
    dst = fitz.open()

    try:
        for i in range(src.page_count):
            sp = src.load_page(i)
            rect = sp.rect
            new_w = rect.width + left_pt + right_pt
            new_h = rect.height

            dp = dst.new_page(width=new_w, height=new_h)

            target = fitz.Rect(left_pt, 0, left_pt + rect.width, rect.height)
            dp.show_pdf_page(target, src, i)

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        dst.save(str(output_pdf))
    finally:
        dst.close()
        src.close()


def add_left_margin(input_pdf: Path, output_pdf: Path, margin_cm: float = 5.0) -> None:
    """Compat : ancienne API (marge à gauche seulement)."""
    add_margins(input_pdf, output_pdf, left_cm=margin_cm, right_cm=0.0)
