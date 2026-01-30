from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import math
import sys
import tempfile

from PIL import Image
import fitz

from app.services.pdf_margin import cm_to_pt
from app.services.pdf_images import insert_image as _insert_pdf_image


_BG_IMAGE_CACHE: dict[tuple[int, int, int, int], str] = {}


def _get_solid_rgba_png(rgb01: Tuple[float, float, float], opacity: float) -> str:
    """Crée (si besoin) un petit PNG RGBA plein, pour simuler un fond semi-transparent.

    Pourquoi ?
    - Certaines versions de PyMuPDF ignorent `fill_opacity` sur les shapes.
    - Un PNG avec canal alpha reste une solution très robuste.
    """
    r = int(max(0.0, min(1.0, float(rgb01[0]))) * 255)
    g = int(max(0.0, min(1.0, float(rgb01[1]))) * 255)
    b = int(max(0.0, min(1.0, float(rgb01[2]))) * 255)
    a = int(max(0.0, min(1.0, float(opacity))) * 255)

    key = (r, g, b, a)
    cached = _BG_IMAGE_CACHE.get(key)
    if cached and Path(cached).exists():
        return cached

    tmp = Path(tempfile.gettempdir())
    path = tmp / f"pdfcorr_bg_{r:02x}{g:02x}{b:02x}_{a:03d}.png"
    if not path.exists():
        try:
            img = Image.new("RGBA", (8, 8), (r, g, b, a))
            img.save(path, format="PNG")
        except Exception:
            # Fallback : crée une version opaque si jamais la création échoue
            img = Image.new("RGB", (8, 8), (r, g, b))
            img.save(path, format="PNG")

    _BG_IMAGE_CACHE[key] = str(path)
    return str(path)


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
    project_root: Optional[Path] = None,
) -> None:
    """
    Applique les annotations:
    - score_circle: pastille (rond) + libellé bleu
    - ink: trait main levée (polyline)
    - textbox: zone de texte (sans cadre / fond transparent)
    - arrow: flèche (ligne + tête)
    - image: insertion d'un PNG (rect)
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

            # ---------------- Image (PNG) ----------------
            if kind == "image":
                rect = ann.get("rect")
                if not (isinstance(rect, list) and len(rect) == 4):
                    continue
                r = _norm_rect(rect)
                if r.is_empty or r.get_area() <= 1:
                    continue

                style = ann.get("style") or {}
                keep_prop = bool(style.get("keep_proportion", True))
                opacity = style.get("opacity")

                # Résolution du chemin image : priorise image_rel (portabilité du projet)
                img_ref = str(ann.get("image_rel") or ann.get("image_path") or "").strip()
                if not img_ref:
                    continue

                img_path = Path(img_ref)
                if not img_path.is_absolute():
                    if project_root:
                        img_path = Path(project_root) / img_ref
                    else:
                        # fallback : relatif au PDF de base
                        img_path = base_pdf.parent / img_ref

                _insert_pdf_image(page, r, img_path, keep_proportion=keep_prop, overlay=True, opacity=opacity)
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

                # Fond (optionnel) : utile pour la note finale sans marge (overlay sur la copie)
                bg_any = style.get("bg_color")
                if bg_any is None:
                    bg_any = style.get("fill_color")
                if bg_any is None:
                    bg_any = style.get("background")

                bg_opacity = style.get("bg_opacity")
                if bg_opacity is None:
                    bg_opacity = style.get("fill_opacity")
                if bg_opacity is None:
                    bg_opacity = style.get("background_opacity")

                if bg_any is not None:
                    try:
                        fill = _resolve_color(bg_any, default_hex="#FFFFFF")
                        op = float(bg_opacity) if bg_opacity is not None else 1.0
                        # Fond semi-transparent : méthode la plus robuste = image RGBA
                        # (certaines versions de PyMuPDF ignorent `fill_opacity`).
                        if op < 0.999:
                            try:
                                img_path = _get_solid_rgba_png(fill, op)
                                _insert_pdf_image(page, r, img_path, keep_proportion=False, overlay=True, opacity=None)
                            except Exception:
                                # fallback : fond opaque si l'insertion image échoue
                                shape = page.new_shape()
                                shape.draw_rect(r)
                                shape.finish(color=None, fill=fill)
                                shape.commit()
                        else:
                            shape = page.new_shape()
                            shape.draw_rect(r)
                            shape.finish(color=None, fill=fill)
                            shape.commit()
                    except Exception:
                        pass

                # Encadré (si demandé ou si note finale)
                if border_color is not None:
                    try:
                        page.draw_rect(r, color=border_color, width=border_width, overlay=True)
                    except Exception:
                        pass

                # Robustesse multi-lignes : on dessine ligne par ligne.
                # Pourquoi ?
                # - certains environnements PyMuPDF/packaging peuvent mal gérer les sauts de ligne
                #   avec insert_textbox (symptôme : seule la 1ère ligne apparaît)
                # - cela donne un résultat prévisible et permet d'ajuster facilement la hauteur.
                x = float(r.x0 + padding)
                y = float(r.y0 + padding + fontsize)  # baseline
                line_h = float(fontsize * 1.25)
                max_w = float(r.width - 2 * padding)

                def _text_len(s: str) -> float:
                    try:
                        # PyMuPDF: mesure en points
                        return float(fitz.get_text_length(s, fontname=fontname, fontsize=fontsize))
                    except Exception:
                        # fallback heuristique
                        return float(len(s) * fontsize * 0.55)

                def _wrap_line(raw: str) -> list[str]:
                    raw = raw.rstrip("\n")
                    if not raw:
                        return [""]
                    # si déjà OK, pas de wrap
                    if _text_len(raw) <= max_w:
                        return [raw]
                    words = raw.split(" ")
                    out: list[str] = []
                    cur = ""
                    for w in words:
                        cand = (cur + " " + w).strip() if cur else w
                        if _text_len(cand) <= max_w or not cur:
                            cur = cand
                        else:
                            out.append(cur)
                            cur = w
                    if cur:
                        out.append(cur)
                    return out or [raw]

                # Si on doit mettre une ligne en gras (ex: Total), on ne wrap pas (garde le style simple)
                # et on applique la règle "Total".
                lines_src = text.splitlines() if text is not None else []
                for src_line in lines_src:
                    if y > float(r.y1 - padding):
                        break
                    if src_line == "":
                        y += line_h
                        continue

                    if bold_total:
                        use_font = "Helvetica-Bold" if src_line.strip().lower().startswith("total") else fontname
                        _insert_text_safe(page, (x, y), src_line, fontsize=fontsize, fontname=use_font, color=color, overlay=True, fontfile=fontfile)
                        y += line_h
                        continue

                    # mode normal : wrap doux par mots
                    for wrapped in _wrap_line(src_line):
                        if y > float(r.y1 - padding):
                            break
                        _insert_text_safe(page, (x, y), wrapped, fontsize=fontsize, fontname=fontname, color=color, overlay=True, fontfile=fontfile)
                        y += line_h
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
        # garbage=4 est très lent sur des régénérations fréquentes (placement d'annotations).
        # garbage=1 garde un PDF propre tout en restant beaucoup plus rapide.
        doc.save(str(out_pdf), garbage=1, deflate=True)
    finally:
        doc.close()
