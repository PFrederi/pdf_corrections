from __future__ import annotations

"""Service d'extraction 'RÉCAPITULATIF' -> scores et export CSV.

Ce module reprend la logique du script 'pdf_recap_to_csv_app_table_fixed2.py'
et est utilisé par l'onglet *Synthese Note* de l'application.
"""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import fitz  # PyMuPDF
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "PyMuPDF (module 'fitz') est requis.\n"
        "Installez-le avec : pip install pymupdf\n\n"
        f"Détail erreur import : {e}"
    )

# ---------------------------
# Extraction / parsing
# ---------------------------

_RE_RECAP = re.compile(r"R[ÉE]CAPITULATIF", re.IGNORECASE)
_RE_EX = re.compile(
    r"\bEx\s*([0-9]+)\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?\s*/\s*[0-9]+(?:[.,][0-9]+)?)",
    re.IGNORECASE,
)
_RE_TOTAL = re.compile(
    r"\bTotal\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?\s*/\s*[0-9]+(?:[.,][0-9]+)?)",
    re.IGNORECASE,
)


def _normalize_score(s: str) -> str:
    # "17,5 / 25" -> "17.5/25"
    return s.replace(" ", "").replace(",", ".")


def _value_before_slash(s: str) -> str:
    """Retourne uniquement la valeur avant le '/' et supprime le '/' et tout ce qui suit."""
    if s is None:
        return ""
    norm = _normalize_score(str(s))
    before = norm.split("/", 1)[0]
    m = re.search(r"-?\d+(?:\.\d+)?", before)
    return m.group(0) if m else before.strip()


def name_from_filename(pdf_path: Path) -> str:
    """Retourne 'NOM PRENOM' depuis le nom de fichier."""
    base = pdf_path.stem

    # Supprime un suffixe ..._verrouille / ...__verrouille (cas observés)
    base = re.sub(r"__?verrouille$", "", base, flags=re.IGNORECASE).strip()
    base = re.sub(r"_verrouille$", "", base, flags=re.IGNORECASE).strip()

    # Nettoie les underscores résiduels
    base = base.replace("__", " ").replace("_", " ").strip()
    return base


def extract_recap_text(page: "fitz.Page") -> str:
    """
    Essaie d'abord de "clipper" la zone de gauche autour du mot RÉCAPITULATIF,
    puis fallback sur tout le texte si on ne trouve pas.
    """
    # Recherche du mot "RÉCAPITULATIF" avec coordonnées
    try:
        words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,word_no)
    except Exception:
        words = []

    recap_box = None
    for w in words:
        word = w[4]
        if _RE_RECAP.fullmatch(word) or _RE_RECAP.search(word):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            # Le cadre rouge est typiquement en haut à gauche.
            # On prend une zone large vers le bas, sur ~40% de la largeur de page.
            page_rect = page.rect
            left = 0
            top = max(0, y0 - 30)
            right = page_rect.width * 0.45
            bottom = min(page_rect.height, y0 + 320)
            recap_box = fitz.Rect(left, top, right, bottom)
            break

    if recap_box is not None:
        try:
            txt = page.get_text("text", clip=recap_box)
            if _RE_RECAP.search(txt):
                return txt
        except Exception:
            pass

    # Fallback : tout le texte de la page
    return page.get_text("text")


def parse_recap(text: str) -> Dict[str, str]:
    """
    Parse les lignes du bloc RÉCAPITULATIF et retourne un dict : {"Ex1":"7/9", ..., "Total":"55.5/82"}
    """
    t = text.replace("\r", "\n")

    m = _RE_RECAP.search(t)
    if not m:
        return {}

    # On prend les lignes après le mot 'RÉCAPITULATIF'
    sub = t[m.end():]
    lines = [ln.strip() for ln in sub.splitlines() if ln.strip()]

    out: Dict[str, str] = {}

    # On limite volontairement : le cadre contient peu de lignes
    for ln in lines[:80]:
        exm = _RE_EX.search(ln)
        if exm:
            exn = int(exm.group(1))
            out[f"Ex{exn}"] = _value_before_slash(exm.group(2))
            continue

        tm = _RE_TOTAL.search(ln)
        if tm:
            out["Total"] = _value_before_slash(tm.group(1))
            # le Total est la dernière info utile → on peut arrêter
            break

    return out


def extract_scores_from_pdf(pdf_path: Path) -> Dict[str, str]:
    """Extrait Ex* + Total depuis la première page du PDF."""
    with fitz.open(pdf_path) as doc:
        if doc.page_count < 1:
            return {}
        page = doc[0]
        txt = extract_recap_text(page)
        return parse_recap(txt)


# ---------------------------
# CSV builder
# ---------------------------

@dataclass
class ExtractResult:
    name: str
    scores: Dict[str, str]


def collect_results(pdf_paths: Sequence[Path]) -> Tuple[List[ExtractResult], List[str]]:
    """
    Retourne la liste des résultats + la liste des colonnes CSV (NOM PRENOM, Ex1..ExN, Total).
    """
    results: List[ExtractResult] = []
    ex_nums = set()

    for p in pdf_paths:
        scores = extract_scores_from_pdf(p)
        for k in scores:
            if k.startswith("Ex"):
                try:
                    ex_nums.add(int(k[2:]))
                except Exception:
                    pass
        results.append(ExtractResult(name=name_from_filename(p), scores=scores))

    max_ex = max(ex_nums) if ex_nums else 0
    columns = ["NOM PRENOM"] + [f"Ex{i}" for i in range(1, max_ex + 1)] + ["Total"]
    return results, columns


def write_csv(output_path: Path, results: List[ExtractResult], columns: List[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {"NOM PRENOM": r.name}
            row.update(r.scores)
            writer.writerow(row)


# ---------------------------
# GUI (Tkinter)
# ---------------------------

