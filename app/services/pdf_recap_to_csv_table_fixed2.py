#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF Recap Extractor -> CSV

Extrait, pour une liste de PDF, les lignes du bloc "RÉCAPITULATIF" de la 1ère page :
- Ex 1, Ex 2, ...
- Total

Le champ "NOM PRENOM" est déduit du nom de fichier (suffixe _verrouille / __verrouille supprimé).

Dépendance : PyMuPDF
    pip install pymupdf

Usage CLI :
    python pdf_recap_to_csv_app.py --input "dossier_ou_pdf" --output "notes.csv"

Usage GUI :
    python pdf_recap_to_csv_app.py
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
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
    r"\bEx\s*([0-9]+)\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*\w+)?",
    re.IGNORECASE,
)
_RE_TOTAL = re.compile(
    r"\bTotal\s*[:\-]?\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*\w+)?",
    re.IGNORECASE,
)


def _strip_spaces(s: str) -> str:
    return str(s).replace(" ", "").strip()


def _note_keep_comma(s: str) -> str:
    """Format note: sortie CSV en décimal FR (virgule)."""
    v = _strip_spaces(s)
    # Si le PDF utilise un point, on convertit vers virgule.
    return v.replace(".", ",")


def _bareme_dot(s: str) -> str:
    """Format barème: décimal avec point (jamais virgule)."""
    v = _strip_spaces(s)
    return v.replace(",", ".")





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
<<<<<<< HEAD
    Extrait le texte du bloc "RÉCAPITULATIF".

    Priorité :
    1) si le marqueur invisible NOTE_FINALE_BOX est présent, on clippe autour (robuste même sans marge
       ou si le récap est en marge droite).
    2) sinon, on clippe autour du mot RÉCAPITULATIF (heuristique historique, plutôt en haut-gauche).
    3) fallback : tout le texte de la page.
    """

    page_rect = page.rect

    # 1) Marqueur invisible (fiable)
    try:
        hits = page.search_for("NOTE_FINALE_BOX")
    except Exception:
        hits = []

    if hits:
        r0 = hits[0]
        # Zone englobante : assez large vers la droite et vers le bas pour contenir le cadre
        left = max(0, float(r0.x0) - 10)
        top = max(0, float(r0.y0) - 30)
        right = min(float(page_rect.width), float(r0.x0) + float(page_rect.width) * 0.55)
        bottom = min(float(page_rect.height), float(r0.y0) + 380)
        clip = fitz.Rect(left, top, right, bottom)
        try:
            txt = page.get_text("text", clip=clip)
            if _RE_RECAP.search(txt):
                return txt
        except Exception:
            pass

    # 2) Recherche du mot "RÉCAPITULATIF" avec coordonnées (heuristique historique)
=======
    Essaie d'abord de "clipper" la zone de gauche autour du mot RÉCAPITULATIF,
    puis fallback sur tout le texte si on ne trouve pas.
    """
    # Recherche du mot "RÉCAPITULATIF" avec coordonnées
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09
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
<<<<<<< HEAD
            # On prend une zone large vers le bas, sur ~45% de la largeur de page.
=======
            # On prend une zone large vers le bas, sur ~40% de la largeur de page.
            page_rect = page.rect
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09
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

<<<<<<< HEAD
    # 3) Fallback : tout le texte de la page
=======
    # Fallback : tout le texte de la page
>>>>>>> 4201597f12f2466f99b49d2bcf026dd86c87bc09
    return page.get_text("text")


def parse_recap(text: str) -> tuple[Dict[str, str], Dict[str, str]]:
    """
    Parse les lignes du bloc RÉCAPITULATIF et retourne un dict : {"Ex1":"7/9", ..., "Total":"55.5/82"}
    """
    t = text.replace("\r", "\n")

    m = _RE_RECAP.search(t)
    if not m:
        return {}, {}

    # On prend les lignes après le mot 'RÉCAPITULATIF'
    sub = t[m.end():]
    lines = [ln.strip() for ln in sub.splitlines() if ln.strip()]

    out: Dict[str, str] = {}
    baremes: Dict[str, str] = {}

    # On limite volontairement : le cadre contient peu de lignes
    for ln in lines[:80]:
        exm = _RE_EX.search(ln)
        if exm:
            exn = int(exm.group(1))
            num = _note_keep_comma(exm.group(2))
            den = _bareme_dot(exm.group(3))
            out[f"Ex{exn}"] = num
            baremes[f"Ex{exn}"] = den
            continue

        tm = _RE_TOTAL.search(ln)
        if tm:
            num = _note_keep_comma(tm.group(1))
            den = _bareme_dot(tm.group(2))
            out["Total"] = num
            baremes["Total"] = den
            # le Total est la dernière info utile → on peut arrêter
            break

    return out, baremes


def extract_scores_from_pdf(pdf_path: Path) -> tuple[Dict[str, str], Dict[str, str]]:
    """Extrait Ex* + Total depuis la première page du PDF."""
    with fitz.open(pdf_path) as doc:
        if doc.page_count < 1:
            return {}, {}
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
    baremes: Dict[str, str]


def collect_results(pdf_paths: Sequence[Path]) -> Tuple[List[ExtractResult], List[str], Dict[str, str]]:
    """
    Retourne la liste des résultats + la liste des colonnes CSV (NOM PRENOM, Ex1..ExN, Total).
    """
    results: List[ExtractResult] = []
    ex_nums = set()
    agg_baremes: Dict[str, str] = {}

    for p in pdf_paths:
        scores, bms = extract_scores_from_pdf(p)
        for k in scores:
            if k.startswith("Ex"):
                try:
                    ex_nums.add(int(k[2:]))
                except Exception:
                    pass
        for k, v in (bms or {}).items():
            if v and (k not in agg_baremes or not agg_baremes.get(k)):
                agg_baremes[k] = v
        results.append(ExtractResult(name=name_from_filename(p), scores=scores, baremes=bms))

    max_ex = max(ex_nums) if ex_nums else 0
    columns = ["NOM PRENOM"] + [f"Ex{i}" for i in range(1, max_ex + 1)] + ["Total"]
    return results, columns, agg_baremes


def write_csv(output_path: Path, results: List[ExtractResult], columns: List[str], baremes: Dict[str, str] | None = None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        # Ligne barème (juste après les entêtes)
        bm = {"NOM PRENOM": "BAREME"}
        if baremes:
            for c in columns:
                if c != "NOM PRENOM":
                    bm[c] = baremes.get(c, "")
        writer.writerow(bm)

        for r in results:
            row = {"NOM PRENOM": r.name}
            row.update(r.scores)
            writer.writerow(row)


# ---------------------------
# GUI (Tkinter)
# ---------------------------

def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Extraction RÉCAPITULATIF (PDF -> CSV)")
    root.geometry("780x520")

    pdf_paths: List[Path] = []

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    # --- Selection zone ---
    sel_box = ttk.LabelFrame(frm, text="1) Sélection des PDF", padding=10)
    sel_box.pack(fill="x")

    sel_info = tk.StringVar(value="Aucun fichier sélectionné.")

    def refresh_sel_label():
        if not pdf_paths:
            sel_info.set("Aucun fichier sélectionné.")
        else:
            sel_info.set(f"{len(pdf_paths)} PDF sélectionné(s).")

    def choose_folder():
        nonlocal pdf_paths
        folder = filedialog.askdirectory(title="Choisir un dossier contenant des PDF")
        if not folder:
            return
        folder_path = Path(folder)
        pdf_paths = sorted(folder_path.glob("*.pdf"))
        refresh_sel_label()
        log(f"Dossier: {folder_path} -> {len(pdf_paths)} PDF")

    def choose_files():
        nonlocal pdf_paths
        files = filedialog.askopenfilenames(
            title="Choisir des fichiers PDF",
            filetypes=[("PDF", "*.pdf")],
        )
        if not files:
            return
        pdf_paths = [Path(p) for p in files]
        refresh_sel_label()
        log(f"Fichiers sélectionnés: {len(pdf_paths)} PDF")

    btns = ttk.Frame(sel_box)
    btns.pack(fill="x")
    ttk.Button(btns, text="Choisir un dossier…", command=choose_folder).pack(side="left")
    ttk.Button(btns, text="Choisir des PDF…", command=choose_files).pack(side="left", padx=8)
    ttk.Label(sel_box, textvariable=sel_info).pack(anchor="w", pady=(8, 0))

    # --- Output zone ---
    out_box = ttk.LabelFrame(frm, text="2) Fichier de sortie CSV", padding=10)
    out_box.pack(fill="x", pady=10)

    out_var = tk.StringVar(value=str(Path.home() / "notes_recapitulatif.csv"))

    out_row = ttk.Frame(out_box)
    out_row.pack(fill="x")
    out_entry = ttk.Entry(out_row, textvariable=out_var)
    out_entry.pack(side="left", fill="x", expand=True)

    def choose_output():
        p = filedialog.asksaveasfilename(
            title="Enregistrer le CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=Path(out_var.get()).name,
        )
        if p:
            out_var.set(p)

    ttk.Button(out_row, text="Choisir…", command=choose_output).pack(side="left", padx=8)

    # --- Actions ---
    act_box = ttk.LabelFrame(frm, text="3) Génération", padding=10)
    act_box.pack(fill="x")

    def generate():
        if not pdf_paths:
            messagebox.showwarning("Aucun PDF", "Veuillez sélectionner un dossier ou des PDF.")
            return
        out_path = Path(out_var.get())
        try:
            log("Extraction en cours…")
            results, columns, baremes = collect_results(pdf_paths)
            write_csv(out_path, results, columns, baremes)
            show_table(results, columns, baremes)
            log(f"OK ✅ CSV généré : {out_path}")
            messagebox.showinfo("Terminé", f"CSV généré :\n{out_path}")
        except Exception as e:
            log(f"ERREUR ❌ {e}")
            messagebox.showerror("Erreur", str(e))

    ttk.Button(act_box, text="Générer le CSV", command=generate).pack(anchor="w")

    # --- Log ---
    log_box = ttk.LabelFrame(frm, text="Journal", padding=10)
    log_box.pack(fill="both", expand=True, pady=10)

    txt = tk.Text(log_box, height=12, wrap="word")
    txt.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(log_box, orient="vertical", command=txt.yview)
    sb.pack(side="right", fill="y")
    txt.configure(yscrollcommand=sb.set)

    def log(msg: str):
        txt.insert("end", msg + "\n")
        txt.see("end")


    def show_table(results, columns, baremes=None):
        """Affiche les résultats dans une nouvelle fenêtre sous forme de tableau."""
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(root)
        win.title("Résultats — RÉCAPITULATIF")
        win.geometry("980x520")

        container = ttk.Frame(win, padding=10)
        container.pack(fill="both", expand=True)

        # Table
        tree = ttk.Treeview(container, columns=columns, show="headings")
        vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        # Headings + default widths
        for col in columns:
            tree.heading(col, text=col)
            w = 240 if col == "NOM PRENOM" else 90
            tree.column(col, width=w, minwidth=70, stretch=True, anchor="center")

        # Insert rows (barème en premier)
        if baremes:
            row_bm = {"NOM PRENOM": "BAREME"}
            for c in columns:
                if c != "NOM PRENOM":
                    row_bm[c] = baremes.get(c, "")
            values = [row_bm.get(c, "") for c in columns]
            tree.insert("", "end", values=values)

        for r in results:
            row = {"NOM PRENOM": r.name}
            row.update(r.scores)
            values = [row.get(c, "") for c in columns]
            tree.insert("", "end", values=values)

        # Copy all to clipboard (tab-separated)
        def copy_all():
            lines = ["\t".join(columns)]
            for iid in tree.get_children(""):
                vals = tree.item(iid, "values")
                lines.append("\t".join(str(v) for v in vals))
            txt_clip = "\n".join(lines)
            win.clipboard_clear()
            win.clipboard_append(txt_clip)
            win.update()

        btn_row = ttk.Frame(container)
        btn_row.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(btn_row, text="Copier le tableau (TSV)", command=copy_all).pack(side="left")
        ttk.Label(btn_row, text="(Coller directement dans Excel / Sheets)").pack(side="left", padx=10)


    refresh_sel_label()
    root.mainloop()


# ---------------------------
# CLI
# ---------------------------

def _gather_input_paths(inp: str) -> List[Path]:
    p = Path(inp)
    if p.is_dir():
        return sorted(p.glob("*.pdf"))
    if p.is_file() and p.suffix.lower() == ".pdf":
        return [p]
    # motif (wildcard)
    return [Path(x) for x in sorted(Path().glob(inp)) if x.lower().endswith(".pdf")]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extrait le bloc RÉCAPITULATIF de PDF et génère un CSV.")
    parser.add_argument("--input", "-i", help="Dossier, fichier PDF ou motif (ex: 'copies/*.pdf').")
    parser.add_argument("--output", "-o", help="Chemin du CSV de sortie (ex: notes.csv).")
    args = parser.parse_args(argv)

    if not args.input and not args.output:
        run_gui()
        return 0

    if not args.input or not args.output:
        parser.error("--input et --output sont requis en mode CLI.")

    pdfs = _gather_input_paths(args.input)
    if not pdfs:
        raise SystemExit("Aucun PDF trouvé dans l'entrée fournie.")

    results, columns, baremes = collect_results(pdfs)
    write_csv(Path(args.output), results, columns, baremes)
    print(f"OK: {args.output} ({len(pdfs)} fichiers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
