from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple
import math
import fitz

from app.services.pdf_margin import cm_to_pt


def _hex_to_rgb01(hex_color: str) -> Tuple[float, float, float]:
    s = (hex_color or "").strip().lstrip("#")
    if len(s) != 6:
        return (0.0, 0.0, 0.0)
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (r, g, b)


# Pastilles correction
RESULT_COLORS = {
    "good":   "#1F9D55",  # vert
    "partial":"#F59E0B",  # orange
    "bad":    "#EF4444",  # rouge
}
LABEL_BLUE = "#3B82F6"  # bleu (libellé des pastilles)

# Palette "classique" demandée
BASIC_COLORS = {
    "noir":   "#111827",
    "rouge":  "#EF4444",
    "bleu":   "#3B82F6",
    "vert":   "#22C55E",
    "violet": "#8B5CF6",
    "marron": "#8B5E3C",
}


def default_marker_position_in_margin(margin_cm: float, y_pt: float) -> Tuple[float, float]:
    """Compat: centre de la marge gauche."""
    margin_pt = cm_to_pt(margin_cm)
    return (margin_pt / 2.0, y_pt)


def _resolve_color(color_any: Any, default_hex: str = "#111827") -> Tuple[float, float, float]:
    """
    color_any peut être :
    - un nom ('rouge', 'bleu', ...)
    - un hex '#RRGGBB'
    - déjà un tuple (r,g,b) en 0..1
    """
    if isinstance(color_any, tuple) and len(color_any) == 3:
        try:
            return (float(color_any[0]), float(color_any[1]), float(color_any[2]))
        except Exception:
            return _hex_to_rgb01(default_hex)
    if isinstance(color_any, str):
        s = color_any.strip().lower()
        if s in BASIC_COLORS:
            return _hex_to_rgb01(BASIC_COLORS[s])
        if s.startswith("#") and len(s) == 7:
            return _hex_to_rgb01(s)
    return _hex_to_rgb01(default_hex)


def _norm_rect(rect: List[float]) -> fitz.Rect:
    if len(rect) != 4:
        return fitz.Rect(0, 0, 0, 0)
    x0, y0, x1, y1 = [float(v) for v in rect]
    r = fitz.Rect(x0, y0, x1, y1)
    r.normalize()
    return r




def _insert_text_bold(page: fitz.Page, pos: Tuple[float, float], text: str, fontsize: float,
                      color: Tuple[float, float, float], fontname: str = "helv") -> None:
    """
    Rendu "gras" compatible toutes versions : on dessine 2 fois le texte avec un léger décalage.
    Évite les soucis de polices (ex: helvB introuvable selon versions).
    """
    page.insert_text(pos, text, fontsize=fontsize, fontname=fontname, color=color, overlay=True)
    page.insert_text((pos[0] + 0.45, pos[1]), text, fontsize=fontsize, fontname=fontname, color=color, overlay=True)

def apply_annotations(
    base_pdf: Path,
    out_pdf: Path,
    annotations: List[Dict[str, Any]],
) -> None:
    """
    Applique les annotations:
    - score_circle: pastille (rond) + libellé bleu
    - score_table: tableau récapitulatif (texte)
    - ink: trait main levée (polyline)
    - textbox: zone de texte (sans cadre / fond transparent)
    - arrow: flèche (ligne + tête)
    """
    base_pdf = Path(base_pdf)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(base_pdf))
    try:
        for ann in annotations:
            if not isinstance(ann, dict):
                continue
            kind = ann.get("kind")
            try:
                page_i = int(ann.get("page", 0))
            except Exception:
                continue
            if page_i < 0 or page_i >= doc.page_count:
                continue
            page = doc.load_page(page_i)

            style = ann.get("style") or {}

            # ---------------- Pastille (Correction V0) ----------------
            if kind == "score_circle":
                x = float(ann.get("x_pt", 0))
                y = float(ann.get("y_pt", 0))

                radius = float(style.get("radius_pt", 9.0))
                result = str(ann.get("result", "good"))
                fill_hex = str(style.get("fill", RESULT_COLORS.get(result, RESULT_COLORS["good"])))
                label_text = str(ann.get("exercise_label") or ann.get("exercise_code") or "").strip()

                label_size = float(style.get("label_fontsize", 11.0))
                label_dx = float(style.get("label_dx_pt", radius + 6.0))

                fill = _resolve_color(fill_hex, default_hex=RESULT_COLORS["good"])
                blue = _resolve_color(LABEL_BLUE, default_hex=LABEL_BLUE)

                shape = page.new_shape()
                shape.draw_circle((x, y), radius)
                shape.finish(color=None, fill=fill, fill_opacity=1.0)
                shape.commit()

                if label_text:
                    text_point = (x + label_dx, y + (label_size / 3.0))
                    page.insert_text(
                        text_point,
                        label_text,
                        fontsize=label_size,
                        fontname="helv",
                        color=blue,
                        overlay=True,
                    )
                continue

            # ---------------- Tableau récapitulatif (note) ----------------
            if kind == "score_table":
                try:
                    x0 = float(ann.get("x_pt", 0.0))
                    y0 = float(ann.get("y_pt", 0.0))
                except Exception:
                    x0, y0 = 0.0, 0.0

                payload = ann.get("payload") or {}
                rows = payload.get("rows") or []
                try:
                    total_score = float(payload.get("total_score", 0.0))
                except Exception:
                    total_score = 0.0
                try:
                    total_max = float(payload.get("total_max", 0.0))
                except Exception:
                    total_max = 0.0

                show20 = bool(payload.get("show_on_20", False))
                score20 = payload.get("score_20", None)
                try:
                    score20f = float(score20) if score20 is not None else None
                except Exception:
                    score20f = None

                font_size = float(style.get("font_size_pt", 11.0))
                line_h = float(style.get("line_h_pt", font_size + 3.0))
                color = _resolve_color(style.get("color"), default_hex=BASIC_COLORS["rouge"])

                y = y0

                # Cadre
                rect_any = ann.get("rect")
                if isinstance(rect_any, list) and len(rect_any) == 4:
                    try:
                        rx0, ry0, rx1, ry1 = [float(v) for v in rect_any]
                        page.draw_rect(fitz.Rect(rx0, ry0, rx1, ry1), color=color, width=1.2, overlay=True)
                    except Exception:
                        pass

                _insert_text_bold(page, (x0, y + font_size), "Note Finale", fontsize=font_size + 1.0, color=color, fontname="helv")
                y += line_h * 1.4

                if isinstance(rows, list):
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        code = str(r.get("code", "")).strip()
                        if not code:
                            continue
                        try:
                            a = float(r.get("attrib", 0.0))
                        except Exception:
                            a = 0.0
                        try:
                            mx = float(r.get("max", 0.0))
                        except Exception:
                            mx = 0.0
                        code_disp = code
                        if not code_disp.lower().startswith("ex"):
                            code_disp = f"Ex{code_disp}"
                        txt = f"{code_disp} : {a:g} / {mx:g}"
                        page.insert_text(
                            (x0, y + font_size),
                            txt,
                            fontsize=font_size,
                            fontname="helv",
                            color=color,
                            overlay=True,
                        )
                        y += line_h

                total_txt = f"Total : {total_score:g} / {total_max:g}"
                if show20 and total_max > 0:
                    if score20f is None:
                        score20f = (total_score / total_max) * 20.0
                    total_txt += f"  ({score20f:.1f}/20)"

                _insert_text_bold(page, (x0, y + font_size), total_txt, fontsize=font_size, color=color, fontname="helv")
                continue

            # ---------------- Main levée ----------------
            if kind == "ink":
                pts = ann.get("points") or []
                if not isinstance(pts, list) or len(pts) < 2:
                    continue
                points = []
                for p in pts:
                    if isinstance(p, (list, tuple)) and len(p) == 2:
                        try:
                            points.append((float(p[0]), float(p[1])))
                        except Exception:
                            pass
                if len(points) < 2:
                    continue
                color = _resolve_color(style.get("color"), default_hex=BASIC_COLORS["bleu"])
                width = float(style.get("width_pt", 2.0))
                page.draw_polyline(points, color=color, width=width, overlay=True)
                continue

            # ---------------- Texte (sans cadre) ----------------
            if kind == "textbox":
                rect_any = ann.get("rect")
                if not (isinstance(rect_any, list) and len(rect_any) == 4):
                    continue
                r = _norm_rect(rect_any)
                text = str(ann.get("text", "") or "").strip()
                if not text:
                    continue
                color = _resolve_color(style.get("color"), default_hex=BASIC_COLORS["bleu"])
                font_size = float(style.get("font_size_pt", 14.0))
                page.insert_textbox(
                    r,
                    text,
                    fontsize=font_size,
                    fontname="helv",
                    color=color,
                    align=0,
                    overlay=True,
                )
                continue

            # ---------------- Flèche ----------------
            if kind == "arrow":
                s = ann.get("start")
                e = ann.get("end")
                if not (isinstance(s, list) and isinstance(e, list) and len(s) == 2 and len(e) == 2):
                    continue
                x0, y0 = float(s[0]), float(s[1])
                x1, y1 = float(e[0]), float(e[1])

                color = _resolve_color(style.get("color"), default_hex=BASIC_COLORS["bleu"])
                width = float(style.get("width_pt", 2.5))

                page.draw_line((x0, y0), (x1, y1), color=color, width=width, overlay=True)

                dx = x1 - x0
                dy = y1 - y0
                L = math.hypot(dx, dy)
                if L < 0.5:
                    continue
                ang = math.atan2(dy, dx)

                head_len = float(style.get("head_len_pt", max(10.0, width * 4.0)))
                head_ang = math.radians(float(style.get("head_angle_deg", 28.0)))

                hx1 = x1 - head_len * math.cos(ang - head_ang)
                hy1 = y1 - head_len * math.sin(ang - head_ang)
                hx2 = x1 - head_len * math.cos(ang + head_ang)
                hy2 = y1 - head_len * math.sin(ang + head_ang)

                page.draw_line((x1, y1), (hx1, hy1), color=color, width=width, overlay=True)
                page.draw_line((x1, y1), (hx2, hy2), color=color, width=width, overlay=True)
                continue

            # autres kind : ignorés

        if out_pdf.exists():
            out_pdf.unlink()
        doc.save(str(out_pdf), garbage=4, deflate=True)
    finally:
        doc.close()
