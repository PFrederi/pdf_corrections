from __future__ import annotations
from pathlib import Path
import fitz  # PyMuPDF

def cm_to_pt(cm: float) -> float:
    return (cm / 2.54) * 72.0

def add_left_margin(input_pdf: Path, output_pdf: Path, margin_cm: float = 5.0) -> None:
    margin_pt = cm_to_pt(margin_cm)

    src = fitz.open(str(input_pdf))
    dst = fitz.open()

    try:
        for i in range(src.page_count):
            sp = src.load_page(i)
            rect = sp.rect
            new_w = rect.width + margin_pt
            new_h = rect.height

            dp = dst.new_page(width=new_w, height=new_h)

            target = fitz.Rect(margin_pt, 0, margin_pt + rect.width, rect.height)
            dp.show_pdf_page(target, src, i)

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        dst.save(str(output_pdf))
    finally:
        dst.close()
        src.close()
