from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import math
import sys
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


def default_marker_position_in_margin(margin_cm: float, y_pt: float):
    """Compat: centre de la marge gauche."""
    margin_pt = cm_to_pt(margin_cm)
    return (margin_pt / 2.0, y_pt)


def _resolve_color(color_any: Any, default_hex: str = "#111827") -> Tuple[float, float, float]:
    """
    color_any peut être :
    - un nom ('rouge', 'bleu', ...)
    - un hex '#RRGGBB'
    """
    if isinstance(color_any, str):
        s = color_any.strip().lower()
        if s in BASIC_COLORS:
            return _hex_to_rgb01(BASIC_COLORS[s])
        if s.startswith("#") and len(s) == 7:
            return _hex_to_rgb01(s)
    return _hex_to_rgb01(default_hex)

def _resolve_font_request(style: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """
    Retourne (fontname, fontfile).
    - fontfile peut être fourni via style['fontfile'] (chemin vers un .ttf/.otf fourni par l'utilisateur)
    - si la police demandée n'est pas garantie (ex: Comic Sans MS), fallback sur Helvetica (portable).
    """
    fontname = str(style.get("fontname") or "Helvetica").strip()
    fontfile = style.get("fontfile")
    fontfile = str(fontfile).strip() if fontfile else None

    if fontfile:
        p = Path(fontfile)
        if p.exists() and p.is_file():
            return (fontname or "Helvetica"), str(p)

        base = getattr(sys, "_MEIPASS", None)
        if base:
            p2 = Path(base) / fontfile
            if p2.exists() and p2.is_file():
                return (fontname or "Helvetica"), str(p2)

        fontfile = None

    low = (fontname or "").lower()
    if "comic" in low:
        fontname = "Helvetica"

    return (fontname or "Helvetica"), fontfile


def _norm_rect(rect: List[float]) -> fitz.Rect:
    if len(rect) != 4:
        return fitz.Rect(0, 0, 0, 0)
    x0, y0, x1, y1 = [float(v) for v in rect]
    r = fitz.Rect(x0, y0, x1, y1)
    r.normalize()
    return r


def _insert_text_safe(
    page: "fitz.Page",
    point: Tuple[float, float],
    text: str,
    fontsize: float,
    fontname: Optional[str],
    color: Tuple[float, float, float],
    overlay: bool = True,
    fontfile: Optional[str] = None,
) -> None:
    """Insère du texte sans casser l'export si la police n'est pas disponible."""
    kwargs = {"fontsize": fontsize, "color": color, "overlay": overlay}

    try:
        if fontfile:
            page.insert_text(point, text, fontfile=fontfile, fontname=(fontname or "Helvetica"), **kwargs)
        elif fontname:
            page.insert_text(point, text, fontname=fontname, **kwargs)
        else:
            page.insert_text(point, text, **kwargs)
        return
    except Exception:
        pass

    try:
        page.insert_text(point, text, **kwargs)
    except Exception:
        return

def apply_annotations(
    base_pdf: Path,
    out_pdf: Path,
    annotations: List[Dict[str, Any]],
) -> None:
    """
    Applique les annotations:
    - score_circle: pastille (rond) + libellé bleu
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
            kind = str(ann.get("kind") or "").strip()

            page_i = int(ann.get("page", 0))
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

                # Style du libellé (compat: si absent -> bleu normal)
                label_style = str(style.get("label_style") or "blue").strip().lower()
                label_bold = bool(style.get("label_bold", False))
                label_color_any = style.get("label_color")

                if label_style in ("red_bold", "rouge_gras", "rouge-gras", "red-bold"):
                    label_bold = True
                    label_color_any = label_color_any or BASIC_COLORS["rouge"]
                else:
                    label_color_any = label_color_any or LABEL_BLUE

                label_color = _resolve_color(label_color_any, default_hex=LABEL_BLUE)
                label_font = "Helvetica-Bold" if label_bold else "Helvetica"

                shape = page.new_shape()
                shape.draw_circle((x, y), radius)
                shape.finish(color=None, fill=fill, fill_opacity=1.0)
                shape.commit()

                if label_text:
                    # Texte à droite (bleu)
                    text_point = (x + label_dx, y + (label_size / 3.0))
                    _insert_text_safe(page, text_point, label_text, fontsize=label_size, fontname=label_font, color=label_color, overlay=True)
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

            # ---------------- Zone de texte ----------------
            if kind == "textbox":
                rect = ann.get("rect")
                if not isinstance(rect, list):
                    continue
                r = _norm_rect(rect)
                if r.is_empty or r.get_area() <= 1:
                    continue

                text = str(ann.get("text") or "").rstrip("\n")
                if not text:
                    continue

                payload = ann.get("payload") or {}
                is_final = isinstance(payload, dict) and payload.get("tag") == "final_note"

                fontsize = float(style.get("fontsize", 14.0))
                fontname, fontfile = _resolve_font_request(style)

                # Couleurs / cadre (optionnels)
                border_color_name = style.get("border_color")
                border_width = float(style.get("border_width_pt", 1.2))
                padding = float(style.get("padding_pt", 4.0))
                bold_total = bool(style.get("bold_total", False)) or is_final

                if is_final:
                    # Toujours rouge + encadré (portable en packaging : Helvetica / Helvetica-Bold sont intégrées)
                    color = _resolve_color("rouge", default_hex=BASIC_COLORS["rouge"])
                    border_color = _resolve_color("rouge", default_hex=BASIC_COLORS["rouge"])
                else:
                    color = _resolve_color(style.get("color"), default_hex=BASIC_COLORS["bleu"])
                    border_color = _resolve_color(border_color_name, default_hex=BASIC_COLORS["bleu"]) if border_color_name else None

                # Encadré (si demandé ou si note finale)
                if border_color is not None:
                    try:
                        page.draw_rect(r, color=border_color, width=border_width, overlay=True)
                    except Exception:
                        pass

                # Si on doit mettre une ligne en gras (ex: Total), on dessine ligne par ligne
                if bold_total:
                    x = float(r.x0 + padding)
                    y = float(r.y0 + padding + fontsize)  # baseline
                    line_h = float(fontsize * 1.25)

                    for line in text.splitlines():
                        if y > float(r.y1 - padding):
                            break

                        if not line.strip():
                            y += line_h
                            continue

                        use_font = "Helvetica-Bold" if line.strip().lower().startswith("total") else fontname
                        _insert_text_safe(page, (x, y), line, fontsize=fontsize, fontname=use_font, color=color, overlay=True, fontfile=fontfile)
                        y += line_h
                else:
                    # Pas de cadre, pas de gras sélectif => insert_textbox suffit
                    try:
                        page.insert_textbox(
                            r,
                            text,
                            fontsize=fontsize,
                            fontname=fontname,
                            fontfile=fontfile,
                            color=color,
                            overlay=True,
                        )
                    except Exception:
                        page.insert_textbox(
                            r,
                            text,
                            fontsize=fontsize,
                            color=color,
                            overlay=True,
                        )
# ---------------- Flèche ----------------
            if kind == "arrow":
                s = ann.get("start")
                e = ann.get("end")
                if not (isinstance(s, list) and isinstance(e, list) and len(s) == 2 and len(e) == 2):
                    continue
                try:
                    x0, y0 = float(s[0]), float(s[1])
                    x1, y1 = float(e[0]), float(e[1])
                except Exception:
                    continue

                color = _resolve_color(style.get("color"), default_hex=BASIC_COLORS["bleu"])
                width = float(style.get("width_pt", 2.0))

                page.draw_line((x0, y0), (x1, y1), color=color, width=width, overlay=True)

                # tête de flèche (2 traits)
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
