from __future__ import annotations

import json
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
import fitz  # PyMuPDF
import uuid
from datetime import datetime
import math
import copy
import sys

from app.ui.theme import apply_dark_theme, DARK_BG, DARK_BG_2
from app.core.project import Project
from app.services.pdf_margin import add_margins, add_left_margin
from app.services.pdf_lock import export_locked
from app.services.pdf_annotate import apply_annotations, RESULT_COLORS, BASIC_COLORS
from app.services.pdf_recap_to_csv_table_fixed2 import collect_results as recap_collect_results, write_csv as recap_write_csv
from app.ui.image_tool import ImageStampTool
from app.ui.widgets.scrollable_frame import VScrollableFrame

def _sanitize_tk_filetypes(filetypes):
    """Nettoie les filetypes pour éviter des crashs Tk sur macOS (NSOpenPanel/NSSavePanel).

    Problèmes rencontrés sur macOS:
    - les patterns catch-all du type "*.*" / "*" peuvent planter Tk
    - certains patterns sans wildcard (ex: "project.json") peuvent se traduire en extension invalide -> nil
    """
    if not filetypes:
        return None

    def _normalize_pattern(p):
        p = (p or "").strip()
        if not p:
            return None
        # Convertit un nom de fichier en pattern d'extension
        if "*" not in p and "?" not in p:
            suf = Path(p).suffix
            if suf:
                return f"*{suf}"
            return None
        return p

    clean = []
    seen = set()

    for item in filetypes:
        if not item or not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        label, patterns = item
        label = str(label).strip() if label else "Fichiers"

        raw = []
        if patterns is None:
            raw = []
        elif isinstance(patterns, str):
            raw = [p for p in patterns.replace(";", " ").split() if p.strip()]
        elif isinstance(patterns, (list, tuple)):
            for p in patterns:
                if isinstance(p, str):
                    raw.extend([x for x in p.replace(";", " ").split() if x.strip()])

        pats = []
        for p in raw:
            np = _normalize_pattern(p)
            if not np:
                continue
            # Sur macOS, évite les entrées catch-all qui peuvent faire planter Tk
            if sys.platform == "darwin" and np in ("*.*", "*"):
                continue
            pats.append(np)

        # dédoublonne en conservant l'ordre
        dedup = []
        for p in pats:
            if p not in seen:
                seen.add(p)
                dedup.append(p)

        if not dedup:
            continue
        clean.append((label, tuple(dedup) if len(dedup) > 1 else dedup[0]))

    return clean or None
from app.ui.widgets.pdf_viewer import PDFViewer
from app.ui.widgets.multiline_text_dialog import MultiLineTextDialog

from app.core.grading import (
    ensure_scheme_dict, scheme_from_dict, scheme_to_dict,
    regenerate_exercises, add_exercise, add_sublevel, add_subsublevel,
    delete_node, delete_exercise, set_label, set_rubric, find_node,
    leaf_nodes, points_for
)


APP_VERSION = "0.7.7"


class AppWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"Corrections PDF — Projets v{APP_VERSION}")
        self.root.geometry("1280x860")
        apply_dark_theme(self.root)

        self.project: Project | None = None
        self._doc_ids: list[str] = []

        # Déplacement pastille (Correction V0)
        self._drag_active: bool = False
        self._drag_target_idx: int | None = None

        # Style du libellé des pastilles (Correction V0) : "blue" (bleu) ou "red_bold" (rouge gras)
        self.c_label_style_var = tk.StringVar(value="blue")
        self.c_label_style_var.trace_add("write", lambda *_: self._on_pastille_label_style_changed())

        # Alignement dans la marge (Correction V0) : distance (cm) depuis le bord gauche
        # (utilisé quand "Aligner dans la marge" est coché)
        self.c_align_margin_cm_var = tk.StringVar(value="0.5")

        # Outils d'annotation classiques (Visualisation PDF)
        self.ann_tool_var = tk.StringVar(value="none")   # none | ink | textbox | arrow | image | manual_score
        self.ann_color_var = tk.StringVar(value="bleu")  # couleur trait / flèche
        self.ann_width_var = tk.IntVar(value=3)          # épaisseur trait / flèche

        self.text_color_var = tk.StringVar(value="bleu") # couleur police
        self.text_size_var = tk.IntVar(value=14)         # taille police
        self.text_value_var = tk.StringVar(value="")     # texte à placer (optionnel)

        # Saisie de points manuels (par exercice principal)
        self.manual_score_ex_var = tk.StringVar(value="")
        self.manual_score_pts_var = tk.StringVar(value="")


        # Points manuels (par exercice principal)
        # Outil: "Points Ex" (cercle rouge)
        self.manual_score_ex_var = tk.StringVar(value="")
        self.manual_score_pts_var = tk.StringVar(value="")



        # GuideCorrection (overlay de correction)
        # - un overlay est un set d'annotations (marques / images / texte / traits) enregistré et réutilisable
        self.gc_overlay_enabled_var = tk.BooleanVar(value=False)
        self.gc_overlay_select_var = tk.StringVar(value="")
        self.gc_overlay_new_name_var = tk.StringVar(value="")
        # Aperçu overlay : opacité simulée à 50% (utile pour distinguer overlay vs annotations réelles)
        self.gc_overlay_opacity50_var = tk.BooleanVar(value=True)
        self._viewer_base_pdf_abs: Path | None = None  # PDF actuellement affiché (sans overlay)

        # Quand l'overlay est activé/désactivé ou quand la sélection change, on rafraîchit la vue (si possible)
        try:
            self.gc_overlay_enabled_var.trace_add("write", lambda *_: self._on_guide_overlay_state_changed())
        except Exception:
            pass
        try:
            self.gc_overlay_select_var.trace_add("write", lambda *_: self._on_guide_overlay_state_changed())
        except Exception:
            pass

        try:
            self.gc_overlay_opacity50_var.trace_add("write", lambda *_: self._on_guide_overlay_state_changed())
        except Exception:
            pass


        # Sélection d'annotations (outil 'Sélection')
        self._selected_ann_ids: set[str] = set()
        self._sel_info_var = tk.StringVar(value="Sélection : 0")
        self.sel_mode_var = tk.BooleanVar(value=False)
        self.sel_mode_var.trace_add("write", lambda *_: self._on_sel_mode_changed())
        self._sync_tool_sel_guard = False  # évite les boucles tool<->sélection


        # Etat runtime (drag)
        self._draw_kind: str | None = None
        self._draw_page: int | None = None
        self._draw_points: list[tuple[float, float]] = []
        self._draw_start: tuple[float, float] | None = None
        self._draw_end: tuple[float, float] | None = None

        # Robustesse : empêche une régénération PDF (open_pdf) de tomber au milieu
        # d'une interaction souris (clic-glisser / relâchement). Sinon, on peut
        # perdre l'événement <ButtonRelease> et l'insertion suivante "ne fait rien".
        self._pdf_mouse_down: bool = False

        # Déplacement d'annotations (outil "Déplacer")
        self._move_active: bool = False
        self._move_ann_id: str | None = None
        self._move_anchor: tuple[float, float] | None = None
        self._move_snapshot: dict | None = None
        self._move_has_moved: bool = False

        # Régénération PDF (debounce) : évite de régénérer trop souvent et améliore la robustesse
        self._regen_after_id = None

        # Outil "Image (PNG)" : géré dans un module séparé pour ne pas alourdir app_window.py
        self.image_tool = ImageStampTool(self)
        # --- Barre haute ---
        top = ttk.Frame(root)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Nom du projet :").pack(side="left")
        self.project_name_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.project_name_var, width=40).pack(side="left", padx=8)

        ttk.Button(top, text="Nouveau projet…", command=self.new_project).pack(side="left", padx=4)
        ttk.Button(top, text="Ouvrir projet…", command=self.open_project).pack(side="left", padx=4)
        ttk.Button(top, text="Enregistrer", command=self.save_project).pack(side="left", padx=12)

        # --- Onglets principaux ---
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tab_project = ttk.Frame(self.nb)
        self.tab_view = ttk.Frame(self.nb)
        self.tab_export = ttk.Frame(self.nb)
        self.tab_grading = ttk.Frame(self.nb)
        self.tab_synth_note = ttk.Frame(self.nb)

        self.nb.add(self.tab_project, text="Import / Projet")
        self.nb.add(self.tab_view, text="Visualisation PDF")
        self.nb.add(self.tab_export, text="Export verrouillé")
        self.nb.add(self.tab_grading, text="Notation")
        self.nb.add(self.tab_synth_note, text="Synthese Note")

        self._build_tab_project()
        self._build_tab_view()
        self._build_tab_export()
        self._build_tab_grading()
        self._build_tab_synthese_note()

        self.nb.bind("<<NotebookTabChanged>>", self._update_click_mode)
        # Rafraîchit le mode de clic quand l'outil change
        self.ann_tool_var.trace_add("write", lambda *_: self._on_annot_tool_changed())
        self.ann_color_var.trace_add("write", lambda *_: self._update_click_mode())

        # Molette souris / trackpad : route le scroll vers la zone sous le curseur (PDF à droite ou panneau Correction V0 à gauche)
        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_global_mousewheel_linux, add="+")
        self.root.bind_all("<Button-5>", self._on_global_mousewheel_linux, add="+")
        # Raccourci ergonomique : masquer/afficher le panneau de gauche (Correction/Infos) pour agrandir la vue PDF
        self.root.bind("<F8>", lambda _e: self._toggle_view_left_pane(), add="+")

    # ---------------- UI : Import/Projet ----------------
    def _build_tab_project(self) -> None:
        frm = ttk.Frame(self.tab_project)
        frm.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(frm, text="Importer des copies (PDF) : avant l'import, vous pouvez ajouter une marge (0 / 2,5 / 5 cm) à gauche, à droite ou des deux côtés.").pack(anchor="w")
        ttk.Button(frm, text="Importer PDF(s)…", command=self.import_pdfs).pack(anchor="w", pady=(10, 10))

        ttk.Label(frm, text="Documents du projet :").pack(anchor="w", pady=(6, 4))
        list_wrap = ttk.Frame(frm)
        list_wrap.pack(fill="both", expand=False, pady=(0, 8))
        self.files_list = tk.Listbox(
            list_wrap, height=18, bg=DARK_BG_2, fg="white",
            highlightthickness=0, selectbackground="#2F81F7"
        )
        files_scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.files_list.yview)
        self.files_list.configure(yscrollcommand=files_scroll.set)
        self.files_list.pack(side="left", fill="both", expand=True)
        files_scroll.pack(side="right", fill="y")
        self.files_list.bind("<<ListboxSelect>>", self.on_select_file)

        ttk.Label(frm, text="Astuce : Visualisation PDF > Correction V0 → clique dans le PDF pour poser les pastilles.").pack(anchor="w", pady=(10, 0))

    # ---------------- UI : Visualisation + sous-onglets ----------------
    

    def _get_project_margins_lr(self) -> tuple[float, float]:
        """Retourne (marge_gauche_cm, marge_droite_cm) depuis les settings (compat inclus)."""
        if not self.project:
            return 5.0, 0.0
        s = self.project.settings or {}

        def _f(key: str, default: float = 0.0) -> float:
            try:
                return float(s.get(key, default) or 0.0)
            except Exception:
                return float(default)

        left = _f("margin_left_cm", _f("left_margin_cm", 5.0))
        right = _f("margin_right_cm", 0.0)

        if left < 0:
            left = 0.0
        if right < 0:
            right = 0.0
        return left, right

    def _get_import_margin_choice_default(self) -> tuple[float, str]:
        """Retourne (largeur_cm, position) pour le dialogue d'import (position: left/right/both)."""
        if not self.project:
            return 5.0, "left"
        s = self.project.settings or {}

        # Si on a déjà mémorisé le choix explicite
        w = s.get("import_margin_width_cm", None)
        pos = s.get("import_margin_position", None)
        if w is not None and pos:
            try:
                return float(w), str(pos)
            except Exception:
                pass

        left, right = self._get_project_margins_lr()
        if left <= 0.0001 and right <= 0.0001:
            return 0.0, "left"
        if left > 0.0 and right <= 0.0:
            return float(left), "left"
        if right > 0.0 and left <= 0.0:
            return float(right), "right"
        if abs(left - right) < 1e-6:
            return float(left), "both"
        return float(max(left, right)), "both"

    def _apply_import_margin_choice_to_settings(self, width_cm: float, position: str) -> tuple[float, float]:
        """Applique le choix (0/2.5/5 + position) au projet et renvoie (left_cm, right_cm)."""
        if not self.project:
            return 0.0, 0.0

        width = float(width_cm or 0.0)
        pos = (position or "left").strip().lower()

        if width <= 0.0:
            left = right = 0.0
            pos = "left"
        elif pos in ("left", "gauche", "l"):
            left, right = width, 0.0
            pos = "left"
        elif pos in ("right", "droite", "r"):
            left, right = 0.0, width
            pos = "right"
        else:
            left, right = width, width
            pos = "both"

        # Settings canoniques (nouveau)
        self.project.settings["margin_left_cm"] = float(left)
        self.project.settings["margin_right_cm"] = float(right)

        # Compat (ancien)
        self.project.settings["left_margin_cm"] = float(left)

        # Pour pré-remplir le dialogue la prochaine fois
        self.project.settings["import_margin_width_cm"] = float(width)
        self.project.settings["import_margin_position"] = pos

        return float(left), float(right)

    def _ask_import_margin_options(self, default_width_cm: float, default_position: str) -> tuple[float, str] | None:
        """Dialogue modal : retourne (largeur_cm, position) ou None si annulé."""
        top = tk.Toplevel(self.root)
        top.title("Options de marge (import PDF)")
        try:
            top.transient(self.root)
            top.grab_set()
            top.resizable(False, False)
        except Exception:
            pass

        frm = ttk.Frame(top, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Choisis la marge à ajouter aux pages importées :").pack(anchor="w")

        w_var = tk.StringVar(value=str(float(default_width_cm)))
        pos_var = tk.StringVar(value=str(default_position or "left"))

        lf_w = ttk.LabelFrame(frm, text="Largeur de marge")
        lf_w.pack(fill="x", pady=(8, 6))

        # Valeurs imposées par le besoin (0 / 2,5 / 5)
        for val, lab in (
            (0.0, "0 cm (pas de marge)"),
            (2.5, "2,5 cm"),
            (5.0, "5 cm"),
        ):
            ttk.Radiobutton(lf_w, text=lab, variable=w_var, value=str(val)).pack(anchor="w", padx=8, pady=2)

        lf_pos = ttk.LabelFrame(frm, text="Position")
        lf_pos.pack(fill="x", pady=(6, 6))

        rb_left = ttk.Radiobutton(lf_pos, text="Gauche", variable=pos_var, value="left")
        rb_right = ttk.Radiobutton(lf_pos, text="Droite", variable=pos_var, value="right")
        rb_both = ttk.Radiobutton(lf_pos, text="Les 2 côtés", variable=pos_var, value="both")

        for rb in (rb_left, rb_right, rb_both):
            rb.pack(anchor="w", padx=8, pady=2)

        def _sync_side_state(*_args) -> None:
            try:
                w = float(str(w_var.get()).replace(",", "."))
            except Exception:
                w = 5.0
            state = "disabled" if w <= 0.0 else "normal"
            try:
                for rb in (rb_left, rb_right, rb_both):
                    rb.configure(state=state)
            except Exception:
                pass

        w_var.trace_add("write", _sync_side_state)
        _sync_side_state()

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill="x", pady=(10, 0))

        result = {"ok": False}

        def _ok() -> None:
            result["ok"] = True
            top.destroy()

        def _cancel() -> None:
            top.destroy()

        ttk.Button(btn_row, text="Annuler", command=_cancel).pack(side="right")
        ttk.Button(btn_row, text="OK", command=_ok).pack(side="right", padx=(0, 6))

        top.bind("<Escape>", lambda _e: _cancel())
        top.bind("<Return>", lambda _e: _ok())

        # Centre la fenêtre
        try:
            top.update_idletasks()
            x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - (top.winfo_width() // 2)
            y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - (top.winfo_height() // 2)
            top.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self.root.wait_window(top)

        if not result["ok"]:
            return None

        try:
            width = float(str(w_var.get()).replace(",", "."))
        except Exception:
            width = 5.0
        pos = str(pos_var.get() or "left").strip().lower()
        if width <= 0.0:
            pos = "left"
        if pos not in ("left", "right", "both"):
            pos = "left"
        return float(width), pos











    def _build_tab_view(self) -> None:
        container = ttk.Frame(self.tab_view)
        container.pack(fill="both", expand=True)

        self.view_pane = ttk.Panedwindow(container, orient="horizontal")
        self.view_pane.pack(fill="both", expand=True)

        # Gauche : sous-onglets (Correction / Infos / GuideCorrection)
        self.view_left = ttk.Frame(self.view_pane, width=380)
        self.view_pane.add(self.view_left, weight=0)

        self.view_subtabs = ttk.Notebook(self.view_left)
        self.view_subtabs.pack(fill="both", expand=True, padx=(0, 8))
        self.view_subtabs.bind("<<NotebookTabChanged>>", self._update_click_mode)

        self.sub_correction = ttk.Frame(self.view_subtabs)
        self.sub_info = ttk.Frame(self.view_subtabs)
        self.sub_guidecorr = ttk.Frame(self.view_subtabs)
        self.view_subtabs.add(self.sub_correction, text="Correction V0")
        self.view_subtabs.add(self.sub_info, text="Infos")
        self.view_subtabs.add(self.sub_guidecorr, text="GuideCorrection")

        # Droite : PDF (+ barre d'outils)
        self.viewer_right = ttk.Frame(self.view_pane)
        self.view_pane.add(self.viewer_right, weight=1)

        self._build_pdf_toolbar(self.viewer_right)

        self.viewer = PDFViewer(self.viewer_right, bg=DARK_BG)
        self.viewer.pack(fill="both", expand=True)

        self._build_view_correction_panel()
        self._build_view_info_panel()
        self._build_view_guidecorr_panel()

        self._click_hint = ttk.Label(self.view_left, text="Mode clic : OFF")
        self._click_hint.pack(anchor="w", padx=10, pady=(6, 6))












    def _toggle_view_left_pane(self) -> None:
        """Masque/affiche le panneau de gauche (Correction/Infos) pour agrandir la vue PDF.

        Raccourci : F8
        """
        pane = getattr(self, "view_pane", None)
        left = getattr(self, "view_left", None)
        if pane is None or left is None:
            return

        hidden = bool(getattr(self, "_view_left_hidden", False))

        if not hidden:
            # Mémorise (si possible) la position du séparateur
            try:
                self._view_left_prev_sash = pane.sashpos(0)
            except Exception:
                self._view_left_prev_sash = None
            try:
                pane.forget(left)
            except Exception:
                return
            self._view_left_hidden = True
        else:
            # Réinsère à gauche
            try:
                pane.insert(0, left, weight=0)
            except Exception:
                try:
                    pane.add(left, weight=0)
                except Exception:
                    return
            # Restaure la position du séparateur si dispo
            try:
                pos = getattr(self, "_view_left_prev_sash", None)
                if pos is not None and hasattr(pane, "sashpos"):
                    pane.sashpos(0, pos)
            except Exception:
                pass
            self._view_left_hidden = False
    def _build_view_correction_panel(self) -> None:
        # Zone scrollable : indispensable quand la fenêtre est petite (sinon les boutons du bas disparaissent)
        outer = ttk.Frame(self.sub_correction)
        outer.pack(fill="both", expand=True)

        self._corr_scroll_canvas = tk.Canvas(outer, bg=DARK_BG, highlightthickness=0)
        v_scroll = ttk.Scrollbar(outer, orient="vertical", command=self._corr_scroll_canvas.yview)
        self._corr_scroll_canvas.configure(yscrollcommand=v_scroll.set)

        v_scroll.pack(side="right", fill="y")
        self._corr_scroll_canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(self._corr_scroll_canvas)
        inner_id = self._corr_scroll_canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_evt=None):
            try:
                self._corr_scroll_canvas.configure(scrollregion=self._corr_scroll_canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(evt):
            # garde la largeur du contenu alignée sur le canvas (sinon barre horizontale implicite)
            try:
                self._corr_scroll_canvas.itemconfigure(inner_id, width=evt.width)
            except Exception:
                pass

        inner.bind("<Configure>", _on_inner_configure)
        self._corr_scroll_canvas.bind("<Configure>", _on_canvas_configure)

        frm = ttk.Frame(inner)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(frm, text="V0 — Correction par barème").pack(anchor="w")
        ttk.Label(frm, text="Choisis un item + résultat, puis clique où tu veux sur le PDF.").pack(anchor="w", pady=(4, 6))
        ttk.Label(frm, text="(Pastille + libellé à droite)").pack(anchor="w", pady=(0, 10))

        # Résumé points (document courant)
        self.c_total_var = tk.StringVar(value="Total attribué : — / —")
        ttk.Label(frm, textvariable=self.c_total_var).pack(anchor="w", pady=(0, 8))

        self.c_move_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Mode déplacer une pastille (cliquer-glisser)", variable=self.c_move_var).pack(anchor="w", pady=(0, 4))

        if not hasattr(self, 'c_align_margin_var'):
            self.c_align_margin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Aligner dans la marge", variable=self.c_align_margin_var).pack(anchor="w", pady=(0, 4))

        # Distance d'alignement (cm) : position X verrouillée depuis le bord gauche
        if not hasattr(self, 'c_align_margin_cm_var'):
            self.c_align_margin_cm_var = tk.StringVar(value="0.5")
        margin_row = ttk.Frame(frm)
        margin_row.pack(anchor="w", pady=(0, 10))
        ttk.Label(margin_row, text="Distance marge (cm) :").pack(side="left")
        try:
            spin = ttk.Spinbox(margin_row, from_=0.0, to=10.0, increment=0.1, width=6, textvariable=self.c_align_margin_cm_var, command=self._apply_corr_margin_cm)
        except Exception:
            spin = tk.Spinbox(margin_row, from_=0.0, to=10.0, increment=0.1, width=6, textvariable=self.c_align_margin_cm_var, command=self._apply_corr_margin_cm)
        spin.pack(side="left", padx=(6, 0))
        try:
            spin.bind("<Return>", self._apply_corr_margin_cm)
            spin.bind("<FocusOut>", self._apply_corr_margin_cm)
        except Exception:
            pass
        ttk.Label(margin_row, text="ex: 0,5").pack(side="left", padx=(8, 0))

        # Affiche/masque la ligne guide d'alignement dans la marge
        try:
            self.c_align_margin_var.trace_add("write", lambda *_: self._update_margin_guide())
        except Exception:
            pass

        ttk.Label(frm, text="Style du libellé :").pack(anchor="w")
        style_row = ttk.Frame(frm)
        style_row.pack(anchor="w", pady=(2, 4))
        ttk.Radiobutton(style_row, text="Bleu", variable=self.c_label_style_var, value="blue").pack(side="left", padx=(0, 12))
        ttk.Radiobutton(style_row, text="Rouge gras", variable=self.c_label_style_var, value="red_bold").pack(side="left")

        ttk.Button(frm, text="Appliquer ce style aux pastilles existantes", command=self.c_apply_label_style_to_all).pack(anchor="w", pady=(0, 10))


        ttk.Label(frm, text="Item (feuille) :").pack(anchor="w")
        self.c_item_var = tk.StringVar(value="")
        self.c_item_combo = ttk.Combobox(frm, textvariable=self.c_item_var, width=38, state="readonly", values=[])
        self.c_item_combo.pack(anchor="w", pady=(2, 10))
        self.c_item_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_points_preview())

        ttk.Label(frm, text="Résultat :").pack(anchor="w")
        self.c_result_var = tk.StringVar(value="good")
        for key, label in [("good", "Bonne (vert)"), ("partial", "Partielle (orange)"), ("bad", "Mauvaise (rouge)")]:
            ttk.Radiobutton(frm, text=label, variable=self.c_result_var, value=key, command=self._update_points_preview).pack(anchor="w", pady=2)

        self.c_points_lbl = ttk.Label(frm, text="Points : —")
        self.c_points_lbl.pack(anchor="w", pady=(10, 10))

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(6, 8))

        ttk.Label(frm, text="Marques du document :").pack(anchor="w")
        self.c_marks = tk.Listbox(frm, height=12, bg=DARK_BG_2, fg="white", highlightthickness=0, selectbackground="#2F81F7")
        self.c_marks.pack(fill="x", pady=(4, 8))

        # Double-clic : édition confortable des points manuels (lignes MANUEL)
        try:
            self.c_marks.bind("<Double-Button-1>", self._on_marks_double_click)
        except Exception:
            pass

        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="Supprimer sélection", command=(lambda: self.c_delete_selected() if hasattr(self, "c_delete_selected") else self.ann_delete_selected())).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Supprimer dernière", command=self.c_delete_last).pack(side="left")

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(10, 8))
        ttk.Button(frm, text="Régénérer PDF corrigé", command=self.c_regenerate).pack(anchor="w")
        ttk.Button(frm, text="Afficher PDF corrigé", command=self.open_current_corrected).pack(anchor="w", pady=(6, 0))

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(10, 8))

        # Note finale (récapitulatif)
        ttk.Label(frm, text="Note finale (récapitulatif) :").pack(anchor="w")
        self.c_final_target_var = tk.StringVar(value="first")
        target_row = ttk.Frame(frm)
        target_row.pack(anchor="w", pady=(2, 6))
        ttk.Radiobutton(target_row, text="Page 1", variable=self.c_final_target_var, value="first").pack(side="left", padx=(0, 12))
        ttk.Radiobutton(target_row, text="Page courante (dernier clic)", variable=self.c_final_target_var, value="current").pack(side="left")

        ttk.Button(frm, text="Insérer la note finale", command=self.c_insert_final_note).pack(anchor="w")
        ttk.Button(frm, text="Supprimer la note finale", command=self.c_delete_final_note).pack(anchor="w", pady=(6, 0))

        self._refresh_correction_ui()



    # ---------------- Pastilles : style du libellé ----------------
    def _get_pastille_label_style(self) -> str:
        """Renvoie le style choisi pour le libellé des pastilles."""
        try:
            v = (self.c_label_style_var.get() or "").strip()
        except Exception:
            v = ""
        if v in ("blue", "red_bold"):
            return v
        if self.project:
            pv = str(self.project.settings.get("pastille_label_style", "blue") or "blue").strip()
            if pv in ("blue", "red_bold"):
                return pv
        return "blue"

    def _on_pastille_label_style_changed(self) -> None:
        """Persiste la préférence dans le projet (si ouvert)."""
        if not self.project:
            return
        style = self._get_pastille_label_style()
        self.project.settings["pastille_label_style"] = style
        try:
            self.project.save()
        except Exception:
            pass

    def c_apply_label_style_to_all(self) -> None:
        """Applique le style du libellé à toutes les pastilles du document courant."""
        if not self._require_doc():
            return
        assert self.project is not None

        style = self._get_pastille_label_style()
        anns = self._annotations_for_current_doc()
        changed = 0

        for a in anns:
            if not isinstance(a, dict) or a.get("kind") != "score_circle":
                continue
            st = a.get("style") or {}
            if not isinstance(st, dict):
                st = {}
            if st.get("label_style") != style:
                st["label_style"] = style
                a["style"] = st
                changed += 1

        if not changed:
            messagebox.showinfo("Correction", "Aucune pastille à mettre à jour.")
            return

        self.project.save()
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()
        self._refresh_correction_totals()

        messagebox.showinfo("Correction", f"{changed} pastille(s) mise(s) à jour.")

    def _build_view_info_panel(self) -> None:
        frm = ttk.Frame(self.sub_info)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(frm, text="Infos — Points attribués").pack(anchor="w")
        ttk.Label(frm, text="Détail (document courant) : attribués / max par exercice.").pack(anchor="w", pady=(4, 10))

        self.info_doc_var = tk.StringVar(value="Document : —")
        ttk.Label(frm, textvariable=self.info_doc_var).pack(anchor="w", pady=(0, 10))

        columns = ("attrib", "max")
        self.info_tree = ttk.Treeview(frm, columns=columns, show="tree headings", height=14)
        self.info_tree.heading("#0", text="Exercice")
        self.info_tree.heading("attrib", text="Attribués")
        self.info_tree.heading("max", text="Max")

        self.info_tree.column("#0", width=220, stretch=True)
        self.info_tree.column("attrib", width=110, stretch=False, anchor="center")
        self.info_tree.column("max", width=110, stretch=False, anchor="center")

        self.info_tree.pack(fill="both", expand=True)

        bottom = ttk.Frame(frm)
        bottom.pack(fill="x", pady=(10, 0))

        ttk.Label(bottom, text="Total général :").pack(side="left")
        self.info_total_var = tk.StringVar(value="— / —")
        ttk.Label(bottom, textvariable=self.info_total_var).pack(side="left", padx=(8, 0))

        self._refresh_info_panel()

    # ---------------- UI : Export ----------------
    def _build_tab_export(self) -> None:
        frm = ttk.Frame(self.tab_export)
        frm.pack(fill="x", padx=12, pady=12)

        ttk.Label(frm, text="Exportation du PDF affiché en version verrouillée (impression OK, modification interdite).").pack(anchor="w")
        ttk.Button(frm, text="Exporter le document affiché…", command=self.export_current_locked).pack(anchor="w", pady=10)

    # ---------------- UI : Notation ----------------
    def _build_tab_grading(self) -> None:
        frm = ttk.Frame(self.tab_grading)
        frm.pack(fill="both", expand=True, padx=12, pady=12)

        top = ttk.Frame(frm)
        top.pack(fill="x")

        ttk.Label(top, text="Nombre d'exercices :").pack(side="left")
        self.nb_ex_var = tk.IntVar(value=1)
        ttk.Entry(top, textvariable=self.nb_ex_var, width=6).pack(side="left", padx=8)

        ttk.Button(top, text="Réinitialiser", command=self.grading_generate).pack(side="left", padx=6)
        ttk.Button(top, text="Ajouter un exercice", command=self.grading_add_exercise).pack(side="left", padx=6)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)

        ttk.Button(top, text="Exporter barème…", command=self.grading_export_scheme).pack(side="left", padx=4)
        ttk.Button(top, text="Importer barème…", command=self.grading_import_scheme).pack(side="left", padx=4)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)
        ttk.Label(top, text="Total général :").pack(side="left")
        self.total_general_var = tk.StringVar(value="—")
        ttk.Label(top, textvariable=self.total_general_var).pack(side="left", padx=(6, 0))

        columns = ("label", "good", "partial", "bad", "total")
        self.gr_tree = ttk.Treeview(frm, columns=columns, show="tree headings", height=18)
        self.gr_tree.heading("#0", text="Code")
        self.gr_tree.heading("label", text="Libellé")
        self.gr_tree.heading("good", text="Bonne")
        self.gr_tree.heading("partial", text="Partielle")
        self.gr_tree.heading("bad", text="Mauvaise")
        self.gr_tree.heading("total", text="Total")

        self.gr_tree.column("#0", width=110, stretch=False)
        self.gr_tree.column("label", width=520, stretch=True)
        self.gr_tree.column("good", width=90, stretch=False, anchor="center")
        self.gr_tree.column("partial", width=90, stretch=False, anchor="center")
        self.gr_tree.column("bad", width=90, stretch=False, anchor="center")
        self.gr_tree.column("total", width=90, stretch=False, anchor="center")

        self.gr_tree.pack(fill="both", expand=True, pady=(12, 8))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(6, 0))

        ttk.Button(btns, text="Ajouter sous-niveau (n.X)", command=self.grading_add_sublevel).pack(side="left", padx=4)
        ttk.Button(btns, text="Ajouter sous-sousniveau (n.X.Y)", command=self.grading_add_subsublevel).pack(side="left", padx=4)
        ttk.Button(btns, text="Renommer", command=self.grading_rename).pack(side="left", padx=12)
        ttk.Button(btns, text="Éditer barème (feuille)", command=self.grading_edit_rubric).pack(side="left", padx=4)
        ttk.Button(btns, text="Supprimer niveau", command=self.grading_delete).pack(side="left", padx=12)
        ttk.Button(btns, text="Supprimer exercice", command=self.grading_delete_exercise).pack(side="left", padx=4)

        self.refresh_grading_tree()

    # ---------------- Click mode (Correction V0) ----------------

    def _build_tab_synthese_note(self) -> None:
        """Onglet: extrait le bloc RÉCAPITULATIF des PDF verrouillés et construit un tableau + CSV."""
        # Conteneur scrollable (pour voir les boutons en bas même sur petits écrans)
        outer = ttk.Frame(self.tab_synth_note)
        outer.pack(fill="both", expand=True)

        self.sn_canvas = tk.Canvas(outer, highlightthickness=0)
        self.sn_vsb = ttk.Scrollbar(outer, orient="vertical", command=self.sn_canvas.yview)
        self.sn_canvas.configure(yscrollcommand=self.sn_vsb.set)

        self.sn_vsb.pack(side="right", fill="y")
        self.sn_canvas.pack(side="left", fill="both", expand=True)

        frm = ttk.Frame(self.sn_canvas, padding=(12, 12))
        win_id = self.sn_canvas.create_window((0, 0), window=frm, anchor="nw")

        def _sn_on_frame_configure(_event=None):
            # Met à jour la zone scrollable
            self.sn_canvas.configure(scrollregion=self.sn_canvas.bbox("all"))

        def _sn_on_canvas_configure(event):
            # Garde la largeur du contenu alignée sur la largeur visible
            try:
                self.sn_canvas.itemconfigure(win_id, width=event.width)
            except Exception:
                pass

        frm.bind("<Configure>", _sn_on_frame_configure)
        self.sn_canvas.bind("<Configure>", _sn_on_canvas_configure)

        # Etat
        self.sn_pdf_paths: list[Path] = []
        self.sn_sel_info = tk.StringVar(value="Aucun fichier sélectionné.")
        default_out = str(Path.home() / "notes_recapitulatif.csv")
        try:
            if self.project is not None:
                default_out = str((self.project.root_dir / "exports" / "notes_recapitulatif.csv").resolve())
        except Exception:
            pass
        self.sn_out_var = tk.StringVar(value=default_out)

        # --- 1) Sélection ---
        sel_box = ttk.LabelFrame(frm, text="1) Sélection des PDF", padding=10)
        sel_box.pack(fill="x")

        btns = ttk.Frame(sel_box)
        btns.pack(fill="x")

        def _refresh_sel_label():
            if not self.sn_pdf_paths:
                self.sn_sel_info.set("Aucun fichier sélectionné.")
            else:
                self.sn_sel_info.set(f"{len(self.sn_pdf_paths)} PDF sélectionné(s).")

        def _refresh_listbox():
            lb.delete(0, "end")
            for p in self.sn_pdf_paths:
                lb.insert("end", str(p))
            _refresh_sel_label()

        def choose_folder():
            folder = filedialog.askdirectory(title="Choisir un dossier contenant des PDF")
            if not folder:
                return
            folder_path = Path(folder)
            self.sn_pdf_paths = sorted(folder_path.glob("*.pdf"))
            _refresh_listbox()

        def choose_files():
            files = filedialog.askopenfilenames(
                title="Choisir des fichiers PDF",
                filetypes=_sanitize_tk_filetypes([("PDF", "*.pdf")]),
            )
            if not files:
                return
            self.sn_pdf_paths = [Path(p) for p in files]
            _refresh_listbox()

        def remove_selected():
            sel = list(lb.curselection())
            if not sel:
                return
            keep = []
            for i, p in enumerate(self.sn_pdf_paths):
                if i not in sel:
                    keep.append(p)
            self.sn_pdf_paths = keep
            _refresh_listbox()

        def clear_list():
            self.sn_pdf_paths = []
            _refresh_listbox()

        ttk.Button(btns, text="Choisir un dossier…", command=choose_folder).pack(side="left")
        ttk.Button(btns, text="Choisir des PDF…", command=choose_files).pack(side="left", padx=8)
        ttk.Button(btns, text="Retirer la sélection", command=remove_selected).pack(side="left", padx=8)
        ttk.Button(btns, text="Vider", command=clear_list).pack(side="left")

        ttk.Label(sel_box, textvariable=self.sn_sel_info).pack(anchor="w", pady=(8, 0))

        # Listbox + scrollbar
        list_row = ttk.Frame(sel_box)
        list_row.pack(fill="both", expand=False, pady=(8, 0))
        lb = tk.Listbox(list_row, height=6, selectmode="extended")
        lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_row, orient="vertical", command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.configure(yscrollcommand=sb.set)

        # --- 2) Sortie CSV ---
        out_box = ttk.LabelFrame(frm, text="2) Fichier de sortie CSV", padding=10)
        out_box.pack(fill="x", pady=10)

        out_row = ttk.Frame(out_box)
        out_row.pack(fill="x")
        out_entry = ttk.Entry(out_row, textvariable=self.sn_out_var)
        out_entry.pack(side="left", fill="x", expand=True)

        def choose_output():
            p = filedialog.asksaveasfilename(
                title="Enregistrer le CSV",
                defaultextension=".csv",
                filetypes=_sanitize_tk_filetypes([("CSV", "*.csv")]),
                initialfile=Path(self.sn_out_var.get()).name if self.sn_out_var.get() else "notes_recapitulatif.csv",
            )
            if p:
                self.sn_out_var.set(p)

        def use_project_exports():
            if self.project is None:
                messagebox.showwarning("Projet", "Aucun projet ouvert.")
                return
            exports_dir = (self.project.root_dir / "exports").resolve()
            exports_dir.mkdir(parents=True, exist_ok=True)
            self.sn_out_var.set(str((exports_dir / "notes_recapitulatif.csv").resolve()))

        ttk.Button(out_row, text="Choisir…", command=choose_output).pack(side="left", padx=8)
        ttk.Button(out_row, text="Utiliser exports du projet", command=use_project_exports).pack(side="left")

        # --- 3) Actions ---
        act_box = ttk.LabelFrame(frm, text="3) Génération", padding=10)
        act_box.pack(fill="x")

        ttk.Button(act_box, text="Générer (CSV + tableau)", command=lambda: generate()).pack(anchor="w")

        # --- 4) Tableau (Treeview) ---
        table_box = ttk.LabelFrame(frm, text="Résultats", padding=10)
        table_box.pack(fill="both", expand=True, pady=(10, 0))

        table_container = ttk.Frame(table_box)
        table_container.pack(fill="both", expand=True)

        self.sn_tree = ttk.Treeview(table_container, columns=("NOM PRENOM", "Total"), show="headings")
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.sn_tree.yview)
        hsb = ttk.Scrollbar(table_container, orient="horizontal", command=self.sn_tree.xview)
        self.sn_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.sn_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_container.rowconfigure(0, weight=1)
        table_container.columnconfigure(0, weight=1)

        def _setup_columns(columns: list[str]):
            self.sn_tree["columns"] = columns
            for col in columns:
                self.sn_tree.heading(col, text=col)
                w = 260 if col == "NOM PRENOM" else 90
                self.sn_tree.column(col, width=w, minwidth=70, stretch=True, anchor="center")

        def _fill_rows(results, columns, baremes=None):
            for iid in self.sn_tree.get_children(""):
                self.sn_tree.delete(iid)

            # Ligne barème en premier (si fournie)
            if baremes:
                row_bm = {"NOM PRENOM": "BAREME"}
                for c in columns:
                    if c != "NOM PRENOM":
                        row_bm[c] = baremes.get(c, "")
                values = [row_bm.get(c, "") for c in columns]
                self.sn_tree.insert("", "end", values=values)

            for r in results:
                row = {"NOM PRENOM": r.name}
                row.update(r.scores)
                values = [row.get(c, "") for c in columns]
                self.sn_tree.insert("", "end", values=values)

        def copy_all():
            cols = list(self.sn_tree["columns"])
            lines = ["\t".join(cols)]
            for iid in self.sn_tree.get_children(""):
                vals = self.sn_tree.item(iid, "values")
                lines.append("\t".join(str(v) for v in vals))
            txt_clip = "\n".join(lines)
            self.root.clipboard_clear()
            self.root.clipboard_append(txt_clip)
            self.root.update()

        btn_row = ttk.Frame(table_box)
        btn_row.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="Copier le tableau (TSV)", command=copy_all).pack(side="left")
        ttk.Label(btn_row, text="(Coller directement dans Excel / Sheets)").pack(side="left", padx=10)

        def generate():
            if not self.sn_pdf_paths:
                messagebox.showwarning("Synthèse", "Veuillez sélectionner un dossier ou des PDF.")
                return
            out_path = Path(self.sn_out_var.get()).expanduser()
            try:
                baremes = None

                # Compat: ancienne version (2 retours) / nouvelle version (3 retours: +baremes)
                try:
                    results, columns, baremes = recap_collect_results(self.sn_pdf_paths)
                except ValueError:
                    results, columns = recap_collect_results(self.sn_pdf_paths)

                # Compat: write_csv(out, results, cols) ou write_csv(out, results, cols, baremes)
                try:
                    recap_write_csv(out_path, results, columns, baremes)
                except TypeError:
                    recap_write_csv(out_path, results, columns)

                _setup_columns(columns)
                _fill_rows(results, columns, baremes)
                messagebox.showinfo("Synthèse", f"CSV généré :\\n{out_path}")
            except Exception as e:
                messagebox.showerror("Erreur", str(e))

        # Init
        _refresh_listbox()
        _setup_columns(["NOM PRENOM", "Total"])

    # ---------------- UI : GuideCorrection (overlay) ----------------
    def _build_view_guidecorr_panel(self) -> None:
        """Panneau permettant de gérer des overlays de correction réutilisables.

        Un overlay est enregistré sous forme de JSON dans le dossier du projet,
        sous le nom: GuideCorrection_XXXXX.json (XXXXX fourni par l'utilisateur).

        L'overlay peut ensuite être :
        - prévisualisé (superposé) sur le PDF en cours de visualisation
        - appliqué (copié) dans les marques du document courant
        """
        frm = ttk.Frame(self.sub_guidecorr)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        ttk.Label(frm, text="GuideCorrection — Overlay de correction").pack(anchor="w")
        ttk.Label(frm, text="Enregistre et réutilise un calque d'annotations (marques, images, texte, traits)").pack(anchor="w", pady=(4, 10))

        # Sélection overlay
        sel_box = ttk.LabelFrame(frm, text="Overlay")
        sel_box.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(sel_box)
        row.pack(fill="x", padx=10, pady=(8, 6))
        ttk.Label(row, text="Choisir :").pack(side="left")

        self.gc_overlay_combo = ttk.Combobox(row, state="readonly", width=28, textvariable=self.gc_overlay_select_var, values=[])
        self.gc_overlay_combo.pack(side="left", padx=(8, 8))

        ttk.Button(row, text="Rafraîchir", command=self._refresh_guide_overlay_list).pack(side="left")

        row2 = ttk.Frame(sel_box)
        row2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Checkbutton(row2, text="Activer la superposition sur le PDF (prévisualisation)", variable=self.gc_overlay_enabled_var).pack(anchor="w")
        ttk.Checkbutton(row2, text="Aperçu : opacité 50%", variable=self.gc_overlay_opacity50_var).pack(anchor="w", pady=(2, 0))

        self.gc_overlay_info_var = tk.StringVar(value="—")
        ttk.Label(sel_box, textvariable=self.gc_overlay_info_var).pack(anchor="w", padx=10, pady=(0, 8))

        btn_row = ttk.Frame(sel_box)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btn_row, text="Appliquer au document (copier dans marques + régénérer)", command=self._apply_selected_overlay_to_current_doc).pack(side="left")
        ttk.Button(btn_row, text="Mettre à jour l'overlay (écraser)", command=self._update_selected_overlay_from_current_doc).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Supprimer overlay", command=self._delete_selected_overlay).pack(side="left", padx=(8, 0))

        # Création overlay
        create_box = ttk.LabelFrame(frm, text="Créer un nouvel overlay")
        create_box.pack(fill="x", pady=(0, 10))

        crow = ttk.Frame(create_box)
        crow.pack(fill="x", padx=10, pady=(8, 8))
        ttk.Label(crow, text="Nom (XXXXX) :").pack(side="left")
        ttk.Entry(crow, textvariable=self.gc_overlay_new_name_var, width=26).pack(side="left", padx=(8, 8))
        ttk.Button(crow, text="Enregistrer depuis ce document", command=self._save_overlay_from_current_doc).pack(side="left")

        ttk.Label(create_box, text="Astuce : place tes annotations sur un PDF 'modèle', puis enregistre l'overlay.").pack(anchor="w", padx=10, pady=(0, 10))

        # init
        self._refresh_guide_overlay_list()
        self._refresh_guide_overlay_info()


    # ---------------- GuideCorrection : stockage / chargement ----------------
    def _guide_overlay_dir(self) -> Path | None:
        if not self.project:
            return None
        return self.project.root_dir

    def _guide_overlay_file(self, name: str) -> Path | None:
        d = self._guide_overlay_dir()
        if not d:
            return None
        safe = self._slugify_overlay_name(name)
        return (d / f"GuideCorrection_{safe}.json").resolve()

    def _slugify_overlay_name(self, name: str) -> str:
        s = (name or '').strip()
        if not s:
            return ''
        out = []
        for ch in s:
            if ch.isalnum():
                out.append(ch)
            elif ch in (' ', '-', '_'):
                out.append('_')
        v = ''.join(out).strip('_')
        while '__' in v:
            v = v.replace('__', '_')
        return v or ''

    def _list_guide_overlays(self) -> list[str]:
        d = self._guide_overlay_dir()
        if not d or not d.exists():
            return []
        out = []
        for p in sorted(d.glob('GuideCorrection_*.json')):
            stem = p.stem
            # stem = GuideCorrection_XXXXX
            name = stem[len('GuideCorrection_'):] if stem.startswith('GuideCorrection_') else stem
            if name:
                out.append(name)
        return out

    def _refresh_guide_overlay_list(self) -> None:
        names = []
        try:
            names = self._list_guide_overlays()
        except Exception:
            names = []
        try:
            if hasattr(self, 'gc_overlay_combo'):
                self.gc_overlay_combo.configure(values=names)
        except Exception:
            pass

        # conserve la sélection si possible
        try:
            cur = (self.gc_overlay_select_var.get() or '').strip()
        except Exception:
            cur = ''
        if cur and cur not in names:
            try:
                self.gc_overlay_select_var.set('')
            except Exception:
                pass
        if not cur and names:
            # auto-sélection du premier overlay si aucun n'est choisi
            try:
                self.gc_overlay_select_var.set(names[0])
            except Exception:
                pass

        self._refresh_guide_overlay_info()

    def _refresh_guide_overlay_info(self) -> None:
        info = '—'
        try:
            name = (self.gc_overlay_select_var.get() or '').strip()
        except Exception:
            name = ''
        if self.project and name:
            try:
                anns = self._load_overlay_annotations(name)
                info = f"Annotations dans l'overlay : {len(anns)}"
            except Exception:
                info = 'Overlay invalide'
        else:
            if not self.project:
                info = 'Ouvre un projet pour gérer les overlays.'
            elif not name:
                info = 'Aucun overlay sélectionné.'
        try:
            self.gc_overlay_info_var.set(info)
        except Exception:
            pass

    def _load_overlay_annotations(self, name: str) -> list[dict]:
        f = self._guide_overlay_file(name)
        if not f or not f.exists():
            return []
        data = json.loads(f.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            anns = data.get('annotations')
        else:
            anns = None
        if not isinstance(anns, list):
            return []
        # on ne filtre pas par kind: on conserve tout
        out = []
        for a in anns:
            if isinstance(a, dict):
                out.append(a)
        return out

    # ---------------- GuideCorrection : actions UI ----------------
    def _save_overlay_from_current_doc(self) -> None:
        if not self._require_doc():
            return
        assert self.project is not None

        name = (self.gc_overlay_new_name_var.get() or '').strip()
        if not name:
            name = simpledialog.askstring("GuideCorrection", "Nom de l'overlay (XXXXX) :", parent=self.root)
            name = (name or '').strip()
            if not name:
                return
            try:
                self.gc_overlay_new_name_var.set(name)
            except Exception:
                pass

        safe = self._slugify_overlay_name(name)
        if not safe:
            messagebox.showwarning('GuideCorrection', 'Nom invalide. Utilise uniquement lettres/chiffres/espace/_/-')
            return

        f = self._guide_overlay_file(name)
        if not f:
            messagebox.showwarning('GuideCorrection', 'Projet introuvable.')
            return

        if f.exists():
            ok = messagebox.askyesno('GuideCorrection', f"L'overlay existe déjà : {f.name}\n\nÉcraser ?")
            if not ok:
                return

        # capture des annotations du document courant
        anns = self._annotations_for_current_doc()
        payload = {
            'name': safe,
            'created_at': datetime.now().isoformat(timespec='seconds'),
            'source_doc_id': str(self.project.current_doc_id or ''),
            'annotations': anns,
        }
        try:
            f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            messagebox.showerror('GuideCorrection', f"Impossible d'enregistrer l'overlay.\n\n{e}")
            return

        self._refresh_guide_overlay_list()
        try:
            self.gc_overlay_select_var.set(safe)
        except Exception:
            pass
        messagebox.showinfo('GuideCorrection', f"Overlay enregistré : {f.name}\n\nAnnotations: {len(anns)}")

    def _update_selected_overlay_from_current_doc(self) -> None:
        """Met à jour (écrase) l'overlay sélectionné avec les annotations du document courant."""
        if not self._require_doc():
            return
        assert self.project is not None

        name = (self.gc_overlay_select_var.get() or '').strip()
        if not name:
            messagebox.showinfo('GuideCorrection', 'Aucun overlay sélectionné.')
            return

        safe = self._slugify_overlay_name(name)
        if not safe:
            messagebox.showwarning('GuideCorrection', 'Nom d\'overlay invalide.')
            return

        f = self._guide_overlay_file(safe)
        if not f:
            messagebox.showwarning('GuideCorrection', 'Projet introuvable.')
            return

        if f.exists():
            ok = messagebox.askyesno('GuideCorrection', f"Mettre à jour (écraser) : {f.name} ?")
            if not ok:
                return
        else:
            ok = messagebox.askyesno('GuideCorrection', f"L'overlay n'existe pas encore : {f.name}\n\nLe créer ?")
            if not ok:
                return

        anns = self._annotations_for_current_doc()

        # conserve created_at si présent
        created_at = datetime.now().isoformat(timespec='seconds')
        try:
            if f.exists():
                prev = json.loads(f.read_text(encoding='utf-8'))
                if isinstance(prev, dict) and prev.get('created_at'):
                    created_at = str(prev.get('created_at'))
        except Exception:
            pass

        payload = {
            'name': safe,
            'created_at': created_at,
            'updated_at': datetime.now().isoformat(timespec='seconds'),
            'source_doc_id': str(self.project.current_doc_id or ''),
            'annotations': anns,
        }
        try:
            f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            messagebox.showerror('GuideCorrection', f"Impossible de mettre à jour l'overlay.\n\n{e}")
            return

        self._refresh_guide_overlay_list()
        try:
            self.gc_overlay_select_var.set(safe)
        except Exception:
            pass
        self._refresh_guide_overlay_info()
        messagebox.showinfo('GuideCorrection', f"Overlay mis à jour : {f.name}\n\nAnnotations: {len(anns)}")

    def _apply_selected_overlay_to_current_doc(self) -> None:
        if not self._require_doc():
            return
        assert self.project is not None

        name = (self.gc_overlay_select_var.get() or '').strip()
        if not name:
            messagebox.showinfo('GuideCorrection', 'Aucun overlay sélectionné.')
            return

        try:
            overlay_anns = self._load_overlay_annotations(name)
        except Exception as e:
            messagebox.showerror('GuideCorrection', f"Overlay invalide.\n\n{e}")
            return

        if not overlay_anns:
            messagebox.showinfo('GuideCorrection', 'Overlay vide (0 annotation).')
            return

        anns = self._annotations_for_current_doc()
        added = 0
        for a in overlay_anns:
            if not isinstance(a, dict):
                continue
            b = copy.deepcopy(a)
            # assure unicité des ids
            b['id'] = str(uuid.uuid4())
            anns.append(b)
            added += 1

        try:
            self.project.save()
        except Exception:
            pass

        # régénère pour voir le résultat
        try:
            self.c_regenerate()
        except Exception:
            pass

        try:
            self._refresh_marks_list()
        except Exception:
            pass
        try:
            self._refresh_info_panel()
        except Exception:
            pass

        messagebox.showinfo('GuideCorrection', f"Overlay appliqué au document.\nAnnotations ajoutées : {added}")

    def _delete_selected_overlay(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None

        name = (self.gc_overlay_select_var.get() or '').strip()
        if not name:
            messagebox.showinfo('GuideCorrection', 'Aucun overlay sélectionné.')
            return

        f = self._guide_overlay_file(name)
        if not f or not f.exists():
            messagebox.showwarning('GuideCorrection', 'Fichier overlay introuvable.')
            return

        ok = messagebox.askyesno('GuideCorrection', f"Supprimer définitivement : {f.name} ?")
        if not ok:
            return
        try:
            f.unlink()
        except Exception as e:
            messagebox.showerror('GuideCorrection', f"Impossible de supprimer.\n\n{e}")
            return

        try:
            self.gc_overlay_select_var.set('')
        except Exception:
            pass
        self._refresh_guide_overlay_list()

    # ---------------- GuideCorrection : preview (superposition) ----------------
    def _on_guide_overlay_state_changed(self) -> None:
        """Callback: changement activation / sélection overlay -> rafraîchit le PDF affiché."""
        # Persistance légère dans le projet
        if self.project:
            try:
                self.project.settings['guide_overlay_selected'] = str(self.gc_overlay_select_var.get() or '')
                self.project.settings['guide_overlay_enabled'] = bool(self.gc_overlay_enabled_var.get())
                self.project.settings['guide_overlay_opacity50'] = bool(self.gc_overlay_opacity50_var.get())
                self.project.save()
            except Exception:
                pass

        # met à jour l'info
        self._refresh_guide_overlay_info()

        # rafraîchit la vue si on a déjà ouvert un PDF
        base = getattr(self, '_viewer_base_pdf_abs', None)
        if base and isinstance(base, Path) and base.exists():
            try:
                self._open_pdf_with_optional_overlay(base, preserve_view=True, lazy_render=True)
            except Exception:
                # fallback
                try:
                    self.viewer.open_pdf(base, preserve_view=True, lazy_render=True)
                except Exception:
                    pass

    def _open_pdf_with_optional_overlay(self, pdf_abs: Path, *, preserve_view: bool = False, lazy_render: bool = False) -> None:
        """Ouvre un PDF dans le viewer, en appliquant l'overlay GuideCorrection si activé."""
        try:
            pdf_abs = Path(pdf_abs)
        except Exception:
            return
        self._viewer_base_pdf_abs = pdf_abs

        use_overlay = False
        try:
            use_overlay = bool(self.gc_overlay_enabled_var.get())
        except Exception:
            use_overlay = False

        name = ''
        try:
            name = (self.gc_overlay_select_var.get() or '').strip()
        except Exception:
            name = ''

        if not (use_overlay and self.project and name):
            self.viewer.open_pdf(pdf_abs, preserve_view=preserve_view, lazy_render=lazy_render)
            return

        # charge overlay
        try:
            overlay_anns = self._load_overlay_annotations(name)
        except Exception:
            overlay_anns = []

        if not overlay_anns:
            self.viewer.open_pdf(pdf_abs, preserve_view=preserve_view, lazy_render=lazy_render)
            return

        # génère un PDF temporaire dans work/ (écrasé)
        try:
            doc = self.project.get_current_doc()
            doc_id = (doc.id if doc else 'doc')
        except Exception:
            doc_id = 'doc'

        slug = self._slugify_overlay_name(name) or 'overlay'
        out_pdf = (self.project.work_dir / f"{doc_id}__GuideCorrection_{slug}.pdf").resolve()
        try:
            out_pdf.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            op_factor = 1.0
            try:
                if bool(self.gc_overlay_opacity50_var.get()):
                    op_factor = 0.5
            except Exception:
                op_factor = 1.0

            apply_annotations(pdf_abs, out_pdf, overlay_anns, project_root=self.project.root_dir, opacity_factor=op_factor)
            self.viewer.open_pdf(out_pdf, preserve_view=preserve_view, lazy_render=lazy_render)
        except Exception:
            # si l'overlay échoue, on n'empêche pas l'ouverture du PDF
            self.viewer.open_pdf(pdf_abs, preserve_view=preserve_view, lazy_render=lazy_render)



    def _update_click_mode(self, _evt=None) -> None:
        # IMPORTANT: ne pas baser la logique sur le texte des onglets (fragile si renommage).
        # On compare directement les ids Tk des widgets.
        main_sel = ""
        try:
            main_sel = str(self.nb.select())
        except Exception:
            main_sel = ""

        sub_sel = ""
        try:
            sub_sel = str(self.view_subtabs.select())
        except Exception:
            sub_sel = ""

        in_view_tab = (main_sel == str(self.tab_view))
        in_correction_subtab = (sub_sel == str(getattr(self, "sub_correction", "")))

        tool = self.ann_tool_var.get() if hasattr(self, "ann_tool_var") else "none"
        sel_on = False
        try:
            sel_on = bool(self.sel_mode_var.get())
        except Exception:
            sel_on = False

        # Robustesse: tant qu'on est dans l'onglet de visualisation, on laisse les callbacks actifs.
        # Le handler décidera ensuite quoi faire (pastilles / outil / sélection).
        enabled = bool(in_view_tab)

        self.viewer.set_interaction_callbacks(
            click_cb=self._on_pdf_click if enabled else None,
            drag_cb=self._on_pdf_drag if enabled else None,
            release_cb=self._on_pdf_release if enabled else None,
            context_cb=self._on_pdf_context_menu if enabled else None,
        )

        if hasattr(self, "_click_hint"):
            label = "OFF"
            if enabled:
                if in_correction_subtab:
                    label = "ON • pastilles"
                else:
                    sel = False
                    try:
                        sel = bool(self.sel_mode_var.get())
                    except Exception:
                        sel = False
                    tool_disp = self._tool_label_for_value(tool)
                    if tool != "none" and sel:
                        label = f"ON • outil: {tool_disp} + sélection"
                    elif tool != "none":
                        label = f"ON • outil: {tool_disp}"
                    elif sel:
                        label = "ON • sélection/déplacement"
                    else:
                        label = "ON"
            self._click_hint.configure(text=f"Mode clic : {label}")


        # Met à jour l'affichage de la ligne guide (si activée)
        try:
            self._update_margin_guide()
        except Exception:
            pass


    # ---------------- Outils PDF (annotation) ----------------
    def _build_pdf_toolbar(self, parent: ttk.Frame) -> None:
        """Barre d'outils plus ergonomique pour maximiser la zone de visualisation.

        Toujours visibles (comme demandé) :
        - Zoom (- / 100% / +)
        - Outil "Sélection"
        - Combobox des outils
        - Boutons "Supprimer sélection" et "Désélectionner"

        Les réglages détaillés (couleur/épaisseur/texte/images, etc.) sont déplacés
        dans un panneau "Options" repliable.
        """

        # --- Ligne 1 (compacte) : choix d'outil ---
        bar1 = ttk.Frame(parent)
        bar1.pack(fill="x", padx=10, pady=(10, 0))

        ttk.Label(bar1, text="Outils :").pack(side="left")

        # Mode sélection (permet de sélectionner + déplacer par glisser-déposer)
        self._sel_toggle = ttk.Checkbutton(
            bar1,
            text="Sélection",
            variable=self.sel_mode_var,
            command=self._on_sel_toggle,
        )
        self._sel_toggle.pack(side="left", padx=(8, 8))

        # Combobox d'outils (robuste en HiDPI)
        self._tool_map = [
            ("Aucun", "none"),
            ("Main levée", "ink"),
            ("Texte", "textbox"),
            ("Flèche", "arrow"),
            ("Image (PNG)", "image"),
            ("Points Ex", "manual_score"),
        ]
        self._tool_label_var = tk.StringVar(value="Aucun")
        self._tool_combo = ttk.Combobox(
            bar1,
            state="readonly",
            width=14,
            values=[t for t, _v in self._tool_map],
            textvariable=self._tool_label_var,
        )
        self._tool_combo.pack(side="left", padx=(0, 10))
        self._tool_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_tool_combo())
        self._sync_tool_combo_from_var()

        # Bouton Options (repliable)
        self._pdf_opts_open_var = tk.BooleanVar(value=False)
        self._btn_pdf_opts = ttk.Button(bar1, text="Options ▸", command=self._toggle_pdf_options)
        self._btn_pdf_opts.pack(side="left")

        # --- Ligne 2 (compacte) : sélection + zoom ---
        bar2 = ttk.Frame(parent)
        bar2.pack(fill="x", padx=10, pady=(6, 0))

        ttk.Label(bar2, textvariable=self._sel_info_var).pack(side="left", padx=(0, 8))
        self._btn_del_sel = ttk.Button(bar2, text="Supprimer sélection", command=self.ann_delete_selected)
        self._btn_del_sel.pack(side="left", padx=(0, 6))
        self._btn_clear_sel = ttk.Button(bar2, text="Désélectionner", command=self.ann_clear_selection)
        self._btn_clear_sel.pack(side="left")

        # Zoom (à droite)
        zoom_box = ttk.Frame(bar2)
        zoom_box.pack(side="right")
        ttk.Button(zoom_box, text="Zoom -", width=8, command=lambda: self._viewer_zoom(-1)).pack(side="left", padx=(0, 4))
        ttk.Button(zoom_box, text="100%", width=6, command=self._viewer_zoom_reset).pack(side="left", padx=(0, 4))
        ttk.Button(zoom_box, text="Zoom +", width=8, command=lambda: self._viewer_zoom(+1)).pack(side="left")

        # Séparateur fin (compact)
        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=(6, 4))

        # --- Panneau Options (repliable) ---
        # NOTE : on ne le pack PAS ici (caché par défaut)
        # Panneau scrollable pour ne pas écraser la zone PDF quand les options sont longues
        self._pdf_opts_container = VScrollableFrame(parent, height=200)
        self._pdf_opts_inner = self._pdf_opts_container.inner

        ttk.Label(self._pdf_opts_inner, text="Options de l'outil sélectionné :").pack(anchor="w", padx=10, pady=(0, 4))

        # 1) Options trait/flèche
        self._opts_ink_arrow = ttk.Frame(self._pdf_opts_inner)
        row_ia = ttk.Frame(self._opts_ink_arrow)
        row_ia.pack(fill="x", padx=10, pady=(0, 6))

        ttk.Label(row_ia, text="Couleur :").pack(side="left")
        self._ann_color_combo = ttk.Combobox(
            row_ia,
            width=10,
            state="readonly",
            values=["rouge", "bleu", "vert", "violet", "marron", "noir"],
            textvariable=self.ann_color_var,
        )
        self._ann_color_combo.pack(side="left", padx=(6, 12))

        ttk.Label(row_ia, text="Épaisseur :").pack(side="left")
        self._ann_width_spin = ttk.Spinbox(row_ia, from_=1, to=20, width=5, textvariable=self.ann_width_var)
        self._ann_width_spin.pack(side="left", padx=(6, 0))

        # 2) Options texte (multi-lignes)
        self._opts_text = ttk.Frame(self._pdf_opts_inner)

        ttk.Label(self._opts_text, text="Texte :").pack(anchor="w", padx=10)
        box_tx = ttk.Frame(self._opts_text)
        box_tx.pack(fill="x", padx=10, pady=(0, 6))

        self._ann_text_widget = tk.Text(box_tx, height=4, wrap="word")
        self._ann_text_widget.pack(side="left", fill="x", expand=True)
        sb_tx = ttk.Scrollbar(box_tx, orient="vertical", command=self._ann_text_widget.yview)
        sb_tx.pack(side="right", fill="y")
        self._ann_text_widget.configure(yscrollcommand=sb_tx.set)

        # on initialise depuis la var (compat)
        try:
            init_txt = self.text_value_var.get() or ""
            if init_txt:
                self._ann_text_widget.insert("1.0", init_txt)
        except Exception:
            pass

        row_tx2 = ttk.Frame(self._opts_text)
        row_tx2.pack(fill="x", padx=10, pady=(0, 6))

        ttk.Label(row_tx2, text="Couleur :").pack(side="left")
        self._text_color_combo = ttk.Combobox(
            row_tx2,
            width=10,
            state="readonly",
            values=["rouge", "bleu", "vert", "violet", "marron", "noir"],
            textvariable=self.text_color_var,
        )
        self._text_color_combo.pack(side="left", padx=(6, 8))

        ttk.Label(row_tx2, text="Taille :").pack(side="left")
        self._text_size_spin = ttk.Spinbox(row_tx2, from_=8, to=72, width=5, textvariable=self.text_size_var)
        self._text_size_spin.pack(side="left", padx=(6, 0))

        # 3) Options images (PNG)
        self._opts_image = ttk.Frame(self._pdf_opts_inner)
        try:
            self.image_tool.build_ui(self._opts_image)
        except Exception:
            pass

        # état initial
        self._update_annot_toolbar_state()

    def _toggle_pdf_options(self) -> None:
        """Affiche/masque le panneau Options (repliable) pour gagner de la place."""
        var = getattr(self, "_pdf_opts_open_var", None)
        if var is None:
            return
        try:
            visible = not bool(var.get())
            var.set(visible)
        except Exception:
            visible = True
        self._set_pdf_options_visible(visible)

    def _set_pdf_options_visible(self, visible: bool) -> None:
        cont = getattr(self, "_pdf_opts_container", None)
        if cont is None:
            return
        try:
            if visible:
                cont.pack(fill="x", padx=0, pady=(0, 4))
            else:
                cont.pack_forget()
        except Exception:
            pass

        btn = getattr(self, "_btn_pdf_opts", None)
        if btn is not None:
            try:
                btn.configure(text=("Options ▾" if visible else "Options ▸"))
            except Exception:
                pass

        # met à jour les frames visibles selon l'outil courant
        try:
            self._update_annot_toolbar_state()
        except Exception:
            pass

    def _show_pdf_tool_options(self, tool: str) -> None:
        """Affiche uniquement les options pertinentes pour l'outil courant."""
        var = getattr(self, "_pdf_opts_open_var", None)
        if var is None:
            return
        try:
            if not bool(var.get()):
                return
        except Exception:
            return

        frames = [
            getattr(self, "_opts_ink_arrow", None),
            getattr(self, "_opts_text", None),
            getattr(self, "_opts_image", None),
        ]
        for fr in frames:
            if fr is None:
                continue
            try:
                fr.pack_forget()
            except Exception:
                pass

        if tool in ("ink", "arrow"):
            fr = getattr(self, "_opts_ink_arrow", None)
            if fr is not None:
                fr.pack(fill="x", padx=0, pady=(0, 0))
        elif tool == "textbox":
            fr = getattr(self, "_opts_text", None)
            if fr is not None:
                fr.pack(fill="x", padx=0, pady=(0, 0))
        elif tool == "image":
            fr = getattr(self, "_opts_image", None)
            if fr is not None:
                fr.pack(fill="x", padx=0, pady=(0, 0))

        # Remet le scroll en haut (utile si l'utilisateur a scrollé dans les options)
        cont = getattr(self, "_pdf_opts_container", None)
        if cont is not None:
            try:
                cont.scroll_to_top()
            except Exception:
                pass
# ---------------- PDF Viewer helpers ----------------
    def _viewer_zoom(self, step_or_factor: float) -> None:
        """Zoom du PDF (step +1/-1 ou facteur 1.1/0.9)."""
        v = getattr(self, "viewer", None)
        if v is None:
            return
        fn = getattr(v, "zoom", None)
        if callable(fn):
            try:
                fn(step_or_factor)
            except Exception:
                pass
        # Le zoom re-render le canvas => il faut redessiner la ligne guide
        try:
            self._update_margin_guide()
        except Exception:
            pass

    def _viewer_zoom_reset(self) -> None:
        v = getattr(self, "viewer", None)
        if v is None:
            return
        fn = getattr(v, "zoom_reset", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
        # Le zoom re-render le canvas => il faut redessiner la ligne guide
        try:
            self._update_margin_guide()
        except Exception:
            pass

    def _on_annot_tool_changed(self) -> None:
        """Rend l'outil d'annotation et le mode Sélection mutuellement exclusifs.

        Problème corrigé : si Sélection est activé, un clic sur une annotation existante
        sélectionne/déplace au lieu d'insérer (ce qui donne l'impression que l'insertion ne marche plus).
        """
        if getattr(self, '_sync_tool_sel_guard', False):
            return
        self._sync_tool_sel_guard = True
        try:
            tool = 'none'
            try:
                tool = self.ann_tool_var.get()
            except Exception:
                tool = 'none'

            # Si un outil est sélectionné, on désactive le mode Sélection
            if tool and tool != 'none':
                try:
                    if bool(self.sel_mode_var.get()):
                        self.sel_mode_var.set(False)
                except Exception:
                    pass

            self._sync_tool_combo_from_var()
            self._update_annot_toolbar_state()
            self._update_click_mode()
        finally:
            self._sync_tool_sel_guard = False

    def _on_sel_mode_changed(self) -> None:
        """Active Sélection => désactive l'outil actif (mutuellement exclusif)."""
        if getattr(self, '_sync_tool_sel_guard', False):
            return
        self._sync_tool_sel_guard = True
        try:
            sel = False
            try:
                sel = bool(self.sel_mode_var.get())
            except Exception:
                sel = False

            if sel:
                try:
                    if self.ann_tool_var.get() != 'none':
                        self.ann_tool_var.set('none')
                except Exception:
                    pass

            self._update_annot_toolbar_state()
            self._update_click_mode()
        finally:
            self._sync_tool_sel_guard = False

    def _sync_tool_combo_from_var(self) -> None:
        """Synchronise la combobox d'outils (UI) avec self.ann_tool_var (logique).

        Méthode robuste : peut être appelée avant la construction complète des widgets.
        """
        tool_var = getattr(self, "ann_tool_var", None)
        current = tool_var.get() if tool_var is not None else "none"

        tool_map = getattr(self, "_tool_map", None) or []
        label = None
        for lbl, v in tool_map:
            if v == current:
                label = lbl
                break

        if label is None:
            label = tool_map[0][0] if tool_map else "Aucun"

        if hasattr(self, "_tool_label_var"):
            try:
                self._tool_label_var.set(label)
            except Exception:
                pass
        elif hasattr(self, "_tool_combo"):
            try:
                self._tool_combo.set(label)
            except Exception:
                pass

    def _tool_label_for_value(self, tool_value: str) -> str:
        """Retourne le libellé UI d'un outil (valeur logique)."""
        try:
            tool_map = getattr(self, "_tool_map", None) or []
            for lbl, v in tool_map:
                if v == tool_value:
                    return str(lbl)
        except Exception:
            pass
        return str(tool_value or "")

    def _on_tool_combo(self) -> None:
        """Callback quand l'utilisateur change l'outil dans la combobox."""
        tool_map = getattr(self, "_tool_map", None) or []

        selected_label = ""
        if hasattr(self, "_tool_label_var"):
            try:
                selected_label = self._tool_label_var.get()
            except Exception:
                selected_label = ""

        selected_val = "none"
        for lbl, v in tool_map:
            if lbl == selected_label:
                selected_val = v
                break

        if hasattr(self, "ann_tool_var"):
            try:
                self.ann_tool_var.set(selected_val)
            except Exception:
                pass

        # Les trace_add déclenchent déjà les refresh, mais on sécurise :
        try:
            self._update_annot_toolbar_state()
        except Exception:
            pass
        try:
            self._update_click_mode()
        except Exception:
            pass

    def _on_sel_toggle(self) -> None:
        """Active/désactive le mode sélection (sélection + déplacement par glisser-déposer)."""
        enabled = False
        try:
            enabled = bool(self.sel_mode_var.get())
        except Exception:
            enabled = False

        if not enabled:
            # On coupe proprement : stop move + clear sélection
            try:
                self._reset_draw_state()
            except Exception:
                pass
            try:
                self._selected_ann_ids.clear()
            except Exception:
                pass
            try:
                self._update_selection_info()
            except Exception:
                pass

        # refresh callbacks + hint
        try:
            self._update_click_mode()
        except Exception:
            pass
        try:
            self._update_annot_toolbar_state()
        except Exception:
            pass

    def _update_annot_toolbar_state(self) -> None:
        """Active/désactive certains contrôles selon l'outil sélectionné.

        Robustesse :
        - Peut être appelé avant la création complète des widgets.
        - Ignore proprement les widgets absents.
        """
        tool_var = getattr(self, "ann_tool_var", None)
        tool = tool_var.get() if tool_var is not None else "none"

        def set_state(widget, enabled: bool):
            if widget is None:
                return
            try:
                widget.configure(state=("normal" if enabled else "disabled"))
            except Exception:
                pass

        ann_color = getattr(self, "_ann_color_combo", None)
        ann_width = getattr(self, "_ann_width_spin", None)
        ann_text = getattr(self, "_ann_text_entry", None)
        txt_color = getattr(self, "_text_color_combo", None)
        txt_size = getattr(self, "_text_size_spin", None)

        if tool in ("ink", "arrow"):
            set_state(ann_color, True)
            set_state(ann_width, True)
            set_state(ann_text, False)
            set_state(txt_color, False)
            set_state(txt_size, False)
            try:
                self.image_tool.set_enabled(False)
            except Exception:
                pass
        elif tool == "textbox":
            set_state(ann_color, False)
            set_state(ann_width, False)
            set_state(ann_text, True)
            set_state(txt_color, True)
            set_state(txt_size, True)
            try:
                self.image_tool.set_enabled(False)
            except Exception:
                pass
        elif tool == "image":
            # insertion d'image : on désactive les réglages d'encre/texte
            set_state(ann_color, False)
            set_state(ann_width, False)
            set_state(ann_text, False)
            set_state(txt_color, False)
            set_state(txt_size, False)
            try:
                self.image_tool.set_enabled(True)
            except Exception:
                pass
        else:
            set_state(ann_color, False)
            set_state(ann_width, False)
            set_state(ann_text, False)
            set_state(txt_color, False)
            set_state(txt_size, False)
            try:
                self.image_tool.set_enabled(False)
            except Exception:
                pass


        # Affiche uniquement les options utiles (si le panneau Options est ouvert)
        try:
            self._show_pdf_tool_options(tool)
        except Exception:
            pass

        has_sel = bool(getattr(self, "_selected_ann_ids", set()))
        set_state(getattr(self, "_btn_del_sel", None), has_sel)
        set_state(getattr(self, "_btn_clear_sel", None), has_sel)

    def _color_hex(self, name: str, default_name: str = "bleu") -> str:
        key = (name or "").strip().lower()
        if key in BASIC_COLORS:
            return BASIC_COLORS[key]
        return BASIC_COLORS.get(default_name, "#3B82F6")

    def _get_text_tool_content(self) -> str:
        """Retourne le contenu du champ texte (multi-lignes)."""
        w = getattr(self, "_ann_text_widget", None)
        if w is not None:
            try:
                return str(w.get("1.0", "end-1c"))
            except Exception:
                pass
        try:
            return str(self.text_value_var.get() or "")
        except Exception:
            return ""

    def _set_text_tool_content(self, text: str) -> None:
        """Met à jour le champ texte (multi-lignes) + la var compat."""
        try:
            self.text_value_var.set(text)
        except Exception:
            pass
        w = getattr(self, "_ann_text_widget", None)
        if w is not None:
            try:
                w.delete("1.0", "end")
                if text:
                    w.insert("1.0", text)
            except Exception:
                pass

    def _reset_draw_state(self) -> None:
        self._draw_kind = None
        self._draw_page = None
        self._draw_points = []
        self._draw_start = None
        self._draw_end = None

        self._move_active = False
        self._move_ann_id = None
        self._move_anchor = None
        self._move_snapshot = None
        self._move_has_moved = False

    # ---------------- Régénération (debounce) ----------------
    def _schedule_regenerate(self, delay_ms: int = 140) -> None:
        """Planifie une régénération du PDF corrigé en 'debounce'.

        Permet d'enchaîner plusieurs insertions (texte, flèches, images…) sans
        payer le coût d'une régénération complète à chaque clic. Améliore aussi
        la robustesse (moins de rechargements PDF au milieu des interactions).
        """
        try:
            if self._regen_after_id is not None:
                self.root.after_cancel(self._regen_after_id)
        except Exception:
            pass
        self._regen_after_id = None

        # Si pas de projet/doc actif, on ne fait rien.
        if not self.project or not self.project.get_current_doc():
            return

        def _run():
            self._regen_after_id = None
            try:
                # Si l'utilisateur est en train de cliquer / glisser dans le PDF,
                # on décale la régénération : sinon open_pdf(...) peut interrompre
                # l'interaction et faire "disparaître" les insertions suivantes.
                if bool(getattr(self, "_pdf_mouse_down", False)) or getattr(self, "_draw_kind", None):
                    self._schedule_regenerate(delay_ms=120)
                    return
                self.c_regenerate()
            except Exception:
                # c_regenerate affiche déjà des messagebox si besoin
                pass

        try:
            self._regen_after_id = self.root.after(max(20, int(delay_ms)), _run)
        except Exception:
            try:
                _run()
            except Exception:
                pass

    # ---------------- Sélection / suppression d'annotations ----------------
    def _update_selection_info(self) -> None:
        n = len(self._selected_ann_ids)
        self._sel_info_var.set(f"Sélection : {n}")
        self._update_annot_toolbar_state()

    def ann_clear_selection(self) -> None:
        self._selected_ann_ids.clear()
        self._update_selection_info()

    def ann_delete_selected(self) -> None:
        if not self._require_doc():
            return
        if not self._selected_ann_ids:
            return
        anns = self._annotations_for_current_doc()
        anns[:] = [a for a in anns if not (isinstance(a, dict) and str(a.get("id", "")) in self._selected_ann_ids)]

        assert self.project is not None
        self.project.save()

        self.ann_clear_selection()
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()
        self._refresh_correction_totals()

    def _select_annotation_at(self, page_index: int, x_pt: float, y_pt: float) -> None:
        if not self._require_doc():
            return

        anns = self._annotations_for_current_doc()
        best_id = None
        best_d = None

        for a in anns:
            d = self._hit_test_ann(a, page_index, x_pt, y_pt)
            if d is None:
                continue
            if best_d is None or d < best_d:
                best_d = d
                best_id = str(a.get("id", ""))

        if not best_id:
            if hasattr(self, "_click_hint"):
                self._click_hint.configure(text="Mode clic : ON • sélection : rien à proximité")
            return

        # Toggle : multi-sélection par clics successifs
        if best_id in self._selected_ann_ids:
            self._selected_ann_ids.remove(best_id)
        else:
            self._selected_ann_ids.add(best_id)

        self._update_selection_info()

        if hasattr(self, "_click_hint"):
            self._click_hint.configure(text=f"Mode clic : ON • sélection : {len(self._selected_ann_ids)}")

    def _find_nearest_annotation(self, page_index: int, x_pt: float, y_pt: float) -> dict | None:
        """Renvoie l'annotation la plus proche (tous types) sur la page, ou None."""
        if not self._require_doc():
            return None
        anns = self._annotations_for_current_doc()
        best = None
        best_d = None
        for a in anns:
            d = self._hit_test_ann(a, page_index, x_pt, y_pt)
            if d is None:
                continue
            if best_d is None or d < best_d:
                best_d = d
                best = a
        return best if isinstance(best, dict) else None

    def _hit_test_ann(self, ann: Any, page_index: int, x_pt: float, y_pt: float) -> float | None:
        """Distance (petit=proche) si le point touche l'annotation, sinon None."""
        if not isinstance(ann, dict):
            return None
        if int(ann.get("page", -1)) != int(page_index):
            return None

        kind = ann.get("kind")

        # Pastille
        if kind == "score_circle":
            try:
                ax = float(ann.get("x_pt", 0.0))
                ay = float(ann.get("y_pt", 0.0))
                r = float((ann.get("style") or {}).get("radius_pt", 9.0))
            except Exception:
                return None
            d = math.hypot(x_pt - ax, y_pt - ay)
            return d if d <= (r + 10.0) else None

        # Points manuels (même hit-test qu'une pastille)
        if kind == "manual_score":
            try:
                ax = float(ann.get("x_pt", 0.0))
                ay = float(ann.get("y_pt", 0.0))
                r = float((ann.get("style") or {}).get("radius_pt", 11.0))
            except Exception:
                return None
            d = math.hypot(x_pt - ax, y_pt - ay)
            return d if d <= (r + 10.0) else None

        # Zone de texte : rect
        if kind == "textbox":
            rect = ann.get("rect")
            if not (isinstance(rect, list) and len(rect) == 4):
                return None
            x0, y0, x1, y1 = [float(v) for v in rect]
            if x0 > x1:
                x0, x1 = x1, x0
            if y0 > y1:
                y0, y1 = y1, y0
            pad = 8.0
            if (x0 - pad) <= x_pt <= (x1 + pad) and (y0 - pad) <= y_pt <= (y1 + pad):
                return 0.0
            dx = 0.0 if x0 <= x_pt <= x1 else min(abs(x_pt - x0), abs(x_pt - x1))
            dy = 0.0 if y0 <= y_pt <= y1 else min(abs(y_pt - y0), abs(y_pt - y1))
            d = math.hypot(dx, dy)
            return d if d <= pad else None

        # Image : rect (même hit-test que textbox)
        if kind == "image":
            rect = ann.get("rect")
            if not (isinstance(rect, list) and len(rect) == 4):
                return None
            x0, y0, x1, y1 = [float(v) for v in rect]
            if x0 > x1:
                x0, x1 = x1, x0
            if y0 > y1:
                y0, y1 = y1, y0
            pad = 8.0
            if (x0 - pad) <= x_pt <= (x1 + pad) and (y0 - pad) <= y_pt <= (y1 + pad):
                return 0.0
            dx = 0.0 if x0 <= x_pt <= x1 else min(abs(x_pt - x0), abs(x_pt - x1))
            dy = 0.0 if y0 <= y_pt <= y1 else min(abs(y_pt - y0), abs(y_pt - y1))
            d = math.hypot(dx, dy)
            return d if d <= pad else None

        # Flèche : segment start-end
        if kind == "arrow":
            try:
                s = ann.get("start")
                e = ann.get("end")
                if not (isinstance(s, list) and isinstance(e, list) and len(s) == 2 and len(e) == 2):
                    return None
                ax, ay = float(s[0]), float(s[1])
                bx, by = float(e[0]), float(e[1])
                w = float((ann.get("style") or {}).get("width_pt", 3.0))
            except Exception:
                return None
            d = self._dist_point_segment(x_pt, y_pt, ax, ay, bx, by)
            thr = 10.0 + w * 1.5
            return d if d <= thr else None

        # Main levée : polyline
        if kind == "ink":
            pts = ann.get("points")
            if not (isinstance(pts, list) and len(pts) >= 2):
                return None
            try:
                w = float((ann.get("style") or {}).get("width_pt", 3.0))
            except Exception:
                w = 3.0
            thr = 10.0 + w * 1.5
            best = None
            for i in range(len(pts) - 1):
                try:
                    ax, ay = float(pts[i][0]), float(pts[i][1])
                    bx, by = float(pts[i + 1][0]), float(pts[i + 1][1])
                except Exception:
                    continue
                d = self._dist_point_segment(x_pt, y_pt, ax, ay, bx, by)
                if best is None or d < best:
                    best = d
            if best is None:
                return None
            return best if best <= thr else None

        return None

    @staticmethod
    def _dist_point_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        vx, vy = bx - ax, by - ay
        wx, wy = px - ax, py - ay
        vv = vx * vx + vy * vy
        if vv <= 1e-9:
            return math.hypot(px - ax, py - ay)
        t = (wx * vx + wy * vy) / vv
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        cx = ax + t * vx
        cy = ay + t * vy
        return math.hypot(px - cx, py - cy)


    def _on_pdf_click(self, page_index: int, x_pt: float, y_pt: float) -> None:
        # marque le début d'une interaction souris sur le canvas PDF (utilisé par le debounce regen)
        self._pdf_mouse_down = True
        self._last_interaction_page = int(page_index)
        # 1) Outils d'annotation (Texte / Flèche / Encre / Image)
        # Priorité volontaire : si un outil est actif, on insère même si "Sélection" est cochée.
        # Sinon, un clic près d'une annotation existante (ex: la zone de texte insérée juste avant)
        # déclenche un déplacement au lieu d'une nouvelle insertion, ce qui donne l'impression que
        # "ça ne marche plus".
        tool = self.ann_tool_var.get()
        if tool != "none":
            if not self._require_doc():
                return

            # Pour éviter toute confusion, on efface la sélection courante dès qu'on est en mode insertion.
            if self._selected_ann_ids:
                self._selected_ann_ids.clear()
                self._update_selection_info()

            if tool == "manual_score":
                # Ajout de points manuels (par exercice principal) : insertion immédiate au clic
                try:
                    self._add_manual_score_at(int(page_index), float(x_pt), float(y_pt))
                except Exception as e:
                    try:
                        messagebox.showerror("Points", str(e))
                    except Exception:
                        pass
                finally:
                    self._reset_draw_state()
                return

            self._draw_kind = tool
            self._draw_page = int(page_index)

            if tool == "ink":
                self._draw_points = [(float(x_pt), float(y_pt))]
                return

            if tool in ("arrow", "textbox", "image"):
                self._draw_start = (float(x_pt), float(y_pt))
                self._draw_end = (float(x_pt), float(y_pt))
                return

            return

        # 2) Mode sélection : si on clique sur une annotation, on la sélectionne et on prépare un déplacement.
        sel_on = False
        try:
            sel_on = bool(self.sel_mode_var.get())
        except Exception:
            sel_on = False

        if sel_on:
            if not self._require_doc():
                return
            ann = self._find_nearest_annotation(page_index, x_pt, y_pt)
            if ann:
                ann_id = str(ann.get("id", "")) if isinstance(ann, dict) else ""
                self._selected_ann_ids = {ann_id} if ann_id else set()
                self._update_selection_info()

                # Prépare déplacement
                self._draw_kind = "move"
                self._draw_page = int(page_index)
                self._move_active = True
                self._move_ann_id = ann_id if ann_id else None
                self._move_anchor = (float(x_pt), float(y_pt))
                self._move_snapshot = copy.deepcopy(ann) if isinstance(ann, dict) else None
                self._move_has_moved = False
                if hasattr(self, "_click_hint"):
                    self._click_hint.configure(text="Mode clic : ON • sélection/déplacement (glisse pour déplacer)")
                return
            else:
                # clic dans le vide : on désélectionne
                if self._selected_ann_ids:
                    self._selected_ann_ids.clear()
                    self._update_selection_info()

        # 2) sinon: pastilles (Correction V0 uniquement)
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""
        if sub == "Correction V0":
            self._on_pdf_click_for_correction(page_index, x_pt, y_pt)

    def _on_pdf_drag(self, page_index: int, x_pt: float, y_pt: float) -> None:
        # 1) déplacement d'une annotation sélectionnée (si mode sélection actif et clic sur ann)
        if self._draw_kind == "move":
            if self._draw_page is None or int(page_index) != int(self._draw_page):
                return
            if not (self._move_active and self._move_ann_id and self._move_anchor and self._move_snapshot):
                return

            dx = float(x_pt) - float(self._move_anchor[0])
            dy = float(y_pt) - float(self._move_anchor[1])
            if abs(dx) > 0.2 or abs(dy) > 0.2:
                self._move_has_moved = True

            anns = self._annotations_for_current_doc()
            target = None
            for a in anns:
                if isinstance(a, dict) and str(a.get("id", "")) == self._move_ann_id:
                    target = a
                    break
            if not target:
                return

            orig = self._move_snapshot
            kind = orig.get("kind") if isinstance(orig, dict) else None

            if kind in ("score_circle", "manual_score"):
                # Option "Aligner dans la marge" (Correction V0) : verrouille X à la distance choisie du bord gauche
                cx = float(orig.get("x_pt", 0.0)) + dx
                cy = float(orig.get("y_pt", 0.0)) + dy
                try:
                    sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
                except Exception:
                    sub = ""
                if sub == "Correction V0" and self._corr_align_margin_enabled():
                    target["x_pt"] = float(self._corr_margin_x_pt())
                else:
                    target["x_pt"] = cx
                target["y_pt"] = cy
                return

            if kind == "textbox":
                rect = orig.get("rect")
                if isinstance(rect, (list, tuple)) and len(rect) == 4:
                    target["rect"] = [
                        float(rect[0]) + dx,
                        float(rect[1]) + dy,
                        float(rect[2]) + dx,
                        float(rect[3]) + dy,
                    ]
                return

            if kind == "image":
                rect = orig.get("rect")
                if isinstance(rect, (list, tuple)) and len(rect) == 4:
                    target["rect"] = [
                        float(rect[0]) + dx,
                        float(rect[1]) + dy,
                        float(rect[2]) + dx,
                        float(rect[3]) + dy,
                    ]
                return

            if kind == "arrow":
                s = orig.get("start")
                e = orig.get("end")
                if isinstance(s, (list, tuple)) and len(s) == 2 and isinstance(e, (list, tuple)) and len(e) == 2:
                    target["start"] = [float(s[0]) + dx, float(s[1]) + dy]
                    target["end"] = [float(e[0]) + dx, float(e[1]) + dy]
                return

            if kind == "ink":
                pts = orig.get("points")
                if isinstance(pts, list) and pts:
                    new_pts = []
                    for p in pts:
                        if isinstance(p, (list, tuple)) and len(p) == 2:
                            new_pts.append([float(p[0]) + dx, float(p[1]) + dy])
                    if new_pts:
                        target["points"] = new_pts
                return

            return

        # 2) dessin (outil combo)
        # Robustesse : on se base sur l'état de dessin démarré au clic (self._draw_kind)
        # plutôt que sur la valeur courante de ann_tool_var (qui peut changer entre temps).
        if self._draw_kind in ("ink", "arrow", "textbox", "image"):
            if self._draw_page is None or int(page_index) != int(self._draw_page):
                return

            if self._draw_kind == "ink":
                if not self._draw_points:
                    self._draw_points = [(float(x_pt), float(y_pt))]
                    return
                lx, ly = self._draw_points[-1]
                if math.hypot(float(x_pt) - lx, float(y_pt) - ly) >= 1.2:
                    self._draw_points.append((float(x_pt), float(y_pt)))
                return

            # arrow/text/image : on met à jour l'endpoint au fil du drag
            self._draw_end = (float(x_pt), float(y_pt))
            return

        # 3) pastilles: déplacement éventuel
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""
        if sub == "Correction V0":
            self._on_pdf_drag_for_correction(page_index, x_pt, y_pt)

    def _on_pdf_release(self, page_index: int, x_pt: float, y_pt: float) -> None:
        # fin interaction souris : important pour ne pas régénérer au milieu d'un clic
        self._pdf_mouse_down = False
        # Fin d'un déplacement (mode sélection)
        if self._draw_kind == "move":
            moved = bool(getattr(self, "_move_has_moved", False))
            if moved and self._require_doc():
                # Snap X pour les pastilles (score_circle) si "Aligner dans la marge" est coché (Correction V0)
                try:
                    sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
                except Exception:
                    sub = ""
                if sub == "Correction V0" and self._corr_align_margin_enabled():
                    try:
                        anns = self._annotations_for_current_doc()
                        for a in anns:
                            if isinstance(a, dict) and str(a.get("id", "")) == str(self._move_ann_id) and a.get("kind") == "score_circle":
                                a["x_pt"] = float(self._corr_margin_x_pt())
                                break
                    except Exception:
                        pass

                try:
                    # persiste et régénère pour voir le résultat dans la vue PDF
                    self.project.save()
                except Exception:
                    pass
                try:
                    self._schedule_regenerate()
                except Exception:
                    pass
            self._reset_draw_state()
            return

        kind = self._draw_kind
        if kind in ("ink", "arrow", "textbox", "image"):
            # Insertion d'annotations (robuste)
            try:
                if not self._require_doc():
                    return
                if self._draw_page is None:
                    return

                start_page = int(self._draw_page)
                anns = self._annotations_for_current_doc()

                if kind == "ink":
                    if len(self._draw_points) >= 2:
                        ann = {
                            "id": str(uuid.uuid4()),
                            "kind": "ink",
                            "page": int(start_page),
                            "points": [[p[0], p[1]] for p in self._draw_points],
                            "style": {
                                "color": self._color_hex(self.ann_color_var.get(), "bleu"),
                                "width_pt": float(self.ann_width_var.get()),
                            },
                            "payload": {},
                        }
                        anns.append(ann)
                        assert self.project is not None
                        self.project.save()
                        self._schedule_regenerate()
                    return

                if kind == "arrow":
                    s = self._draw_start
                    e = self._draw_end or (float(x_pt), float(y_pt))
                    if s and e:
                        ann = {
                            "id": str(uuid.uuid4()),
                            "kind": "arrow",
                            "page": int(start_page),
                            "start": [float(s[0]), float(s[1])],
                            "end": [float(e[0]), float(e[1])],
                            "style": {
                                "color": self._color_hex(self.ann_color_var.get(), "bleu"),
                                "width_pt": float(self.ann_width_var.get()),
                            },
                            "payload": {},
                        }
                        anns.append(ann)
                        assert self.project is not None
                        self.project.save()
                        self._schedule_regenerate()
                    return

                if kind == "image":
                    s = self._draw_start
                    e = self._draw_end or (float(x_pt), float(y_pt))
                    if not s or not e:
                        return

                    # construit une annotation image via le module (gestion bibliothèque / ratio)
                    ann = None
                    try:
                        ann = self.image_tool.build_annotation(
                            int(start_page),
                            (float(s[0]), float(s[1])),
                            (float(e[0]), float(e[1])),
                            (float(x_pt), float(y_pt)),
                        )
                    except Exception:
                        ann = None

                    if not ann:
                        messagebox.showwarning("Image", "Aucune image sélectionnée (ou bibliothèque vide).")
                        return

                    anns.append(ann)
                    assert self.project is not None
                    self.project.save()
                    self._schedule_regenerate()
                    return

                if kind == "textbox":
                    s = self._draw_start
                    e = self._draw_end or (float(x_pt), float(y_pt))
                    if not s or not e:
                        return

                    x0, y0 = s
                    x1, y1 = e

                    # Multi-lignes: on lit depuis le champ (Options) ; si vide on ouvre un dialogue multi-lignes.
                    text_val = (self._get_text_tool_content() or "").rstrip()
                    if not text_val:
                        text_val = MultiLineTextDialog.ask(
                            self.root,
                            title="Texte",
                            prompt="Contenu de la zone de texte (multi-lignes) :",
                            initial="",
                        )
                        text_val = (text_val or "").rstrip()
                        if text_val:
                            # pratique: garde le texte dans les options pour les insertions suivantes
                            self._set_text_tool_content(text_val)

                    if not text_val:
                        return

                    # --- Rect robuste ---
                    # Si l'utilisateur ne dessine pas (clic simple), on calcule une hauteur suffisante
                    # pour afficher toutes les lignes. Sinon, on respecte le rectangle dessiné.
                    # IMPORTANT: beaucoup d'utilisateurs cliquent (sans drag) -> si la hauteur est fixe (40pt),
                    # seules 1 ligne (voire 2) apparaissent, donnant l'impression que "seule la première ligne" est insérée.
                    fontsize = float(self.text_size_var.get())
                    padding = 4.0
                    lines = text_val.splitlines() or [text_val]
                    n_lines = max(1, len(lines))
                    line_h = max(10.0, fontsize * 1.25)
                    min_h_for_text = padding * 2 + n_lines * line_h + 2

                    if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
                        # largeur par défaut + hauteur adaptée au nombre de lignes
                        x1 = x0 + 260
                        y1 = y0 + max(44.0, float(min_h_for_text))
                    else:
                        # rectangle dessiné : si trop petit, on l'agrandit légèrement en hauteur
                        if abs(y1 - y0) < min_h_for_text:
                            y1 = y0 + float(min_h_for_text)

                    rect = [float(x0), float(y0), float(x1), float(y1)]

                    ann = {
                        "id": str(uuid.uuid4()),
                        "kind": "textbox",
                        "page": int(start_page),
                        "rect": rect,
                        "text": text_val,
                        "style": {
                            "color": self._color_hex(self.text_color_var.get(), "bleu"),
                            "fontsize": fontsize,
                        },
                        "payload": {},
                    }
                    anns.append(ann)
                    assert self.project is not None
                    self.project.save()
                    self._schedule_regenerate()

                    # Feedback visuel (utile si l'utilisateur pense que "rien ne se passe")
                    try:
                        if hasattr(self, "_click_hint"):
                            self._click_hint.configure(text=f"Mode clic : ON • texte ajouté (p.{start_page+1})")
                    except Exception:
                        pass
                    return

                # outil inconnu
                return

            except Exception as e:
                # En version packagée, les exceptions Tk peuvent être silencieuses : on affiche un message clair.
                try:
                    messagebox.showerror("Annotation", f"Erreur insertion annotation ({kind}).\n\n{e}")
                except Exception:
                    pass
            finally:
                self._reset_draw_state()
            return

        # pastilles: fin déplacement
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""
        if sub == "Correction V0":
            self._on_pdf_release_for_correction(page_index, x_pt, y_pt)


    def _add_manual_score_at(self, page_index: int, x_pt: float, y_pt: float) -> None:
        """Ajoute (ou remplace) un marqueur de points manuels pour un exercice principal.

        Option A : l'utilisateur sélectionne l'outil "Points Ex" puis clique dans le PDF.
        Une fenêtre demande : exercice + points.

        Règle : si un point manuel existe déjà pour cet exercice, il est remplacé.
        """
        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        assert doc is not None

        scheme = self._scheme()
        if not getattr(scheme, 'exercises', None):
            messagebox.showwarning('Points manuels', "Aucun exercice défini dans l'onglet Notation.")
            return

        # Max points par exercice principal
        def total_good(node) -> float:
            if getattr(node, 'children', None):
                return float(sum(total_good(c) for c in node.children))
            try:
                lvl = int(node.level())
            except Exception:
                lvl = 0
            if lvl in (1, 2):
                rub = getattr(node, 'rubric', None)
                if rub is not None:
                    try:
                        return float(rub.good)
                    except Exception:
                        return 1.0
                return 1.0
            return 0.0

        ex_items = []  # (code, label, max)
        for ex in scheme.exercises:
            try:
                code = str(ex.code)
            except Exception:
                continue
            label = str(ex.label) if getattr(ex, 'label', None) else f"Exercice {code}"
            mx = float(total_good(ex))
            ex_items.append((code, label, mx))

        if not ex_items:
            messagebox.showwarning('Points manuels', "Aucun exercice défini dans l'onglet Notation.")
            return

        # Valeurs par défaut persistantes
        try:
            if not self.manual_score_ex_var.get().strip():
                self.manual_score_ex_var.set(ex_items[0][0])
        except Exception:
            pass

        # --- UI dialog ---
        top = tk.Toplevel(self.root)
        top.title('Points manuels (exercice)')
        top.transient(self.root)
        top.grab_set()

        frm = ttk.Frame(top, padding=12)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text='Attribuer des points manuellement (par exercice principal)').pack(anchor='w')
        ttk.Label(frm, text="Ces points remplacent le total des pastilles de l'exercice dans la note.").pack(anchor='w', pady=(2, 10))

        # Exercice
        row1 = ttk.Frame(frm)
        row1.pack(fill='x', pady=(0, 8))
        ttk.Label(row1, text='Exercice :').pack(side='left')

        disp = [f"Ex {c} — {lab} (max {mx:g})" for c, lab, mx in ex_items]
        code_by_disp = {d: c for d, (c, _, _) in zip(disp, ex_items)}
        mx_by_code = {c: mx for c, _, mx in ex_items}
        label_by_code = {c: lab for c, lab, _ in ex_items}

        # sélection affichée
        cur_code = (self.manual_score_ex_var.get() or '').strip()
        if cur_code not in mx_by_code:
            cur_code = ex_items[0][0]
        cur_disp = next((d for d in disp if code_by_disp.get(d) == cur_code), disp[0])

        disp_var = tk.StringVar(value=cur_disp)
        cb = ttk.Combobox(row1, state='readonly', values=disp, textvariable=disp_var, width=44)
        cb.pack(side='left', padx=(8, 0), fill='x', expand=True)

        # Points
        row2 = ttk.Frame(frm)
        row2.pack(fill='x', pady=(0, 8))
        ttk.Label(row2, text='Points :').pack(side='left')

        pts_var = tk.StringVar(value=(self.manual_score_pts_var.get() or ''))
        if not pts_var.get().strip():
            pts_var.set(f"{mx_by_code.get(cur_code, 0.0):g}")

        ent = ttk.Entry(row2, textvariable=pts_var, width=10)
        ent.pack(side='left', padx=(8, 0))

        max_lbl_var = tk.StringVar(value=f"/ {mx_by_code.get(cur_code, 0.0):g}")
        ttk.Label(row2, textvariable=max_lbl_var).pack(side='left', padx=6)

        def _on_ex_change(_evt=None):
            c = code_by_disp.get(disp_var.get(), ex_items[0][0])
            try:
                self.manual_score_ex_var.set(c)
            except Exception:
                pass
            max_lbl_var.set(f"/ {mx_by_code.get(c, 0.0):g}")

        cb.bind('<<ComboboxSelected>>', _on_ex_change)

        # Boutons
        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(10, 0))

        result = {'ok': False}

        def on_ok():
            result['ok'] = True
            try:
                top.destroy()
            except Exception:
                pass

        def on_cancel():
            result['ok'] = False
            try:
                top.destroy()
            except Exception:
                pass

        ttk.Button(btns, text='Annuler', command=on_cancel).pack(side='right')
        ttk.Button(btns, text='OK', command=on_ok).pack(side='right', padx=(0, 8))

        try:
            ent.focus_set()
            ent.selection_range(0, 'end')
        except Exception:
            pass

        self.root.wait_window(top)
        if not result.get('ok'):
            return

        # Lecture valeurs
        ex_code = code_by_disp.get(disp_var.get(), ex_items[0][0])
        ex_label = label_by_code.get(ex_code, f"Exercice {ex_code}")

        raw_pts = (pts_var.get() or '').strip().replace(',', '.')
        try:
            pts = float(raw_pts)
        except Exception:
            messagebox.showwarning('Points manuels', 'Valeur de points invalide.')
            return

        # mémorise pour la prochaine fois
        try:
            self.manual_score_ex_var.set(ex_code)
        except Exception:
            pass
        try:
            self.manual_score_pts_var.set(str(pts).replace('.', ','))
        except Exception:
            pass

        # Ajout / remplacement
        anns = self._annotations_for_current_doc()
        kept = []
        for a in anns:
            if isinstance(a, dict) and a.get('kind') == 'manual_score':
                c = str(a.get('exercise_code', '')).strip()
                c = c.split('.', 1)[0] if c else ''
                if c == ex_code:
                    continue
            kept.append(a)
        anns[:] = kept

        x_use = float(x_pt)
        try:
            # Si alignement marge actif (Correction V0), on verrouille X
            if self._corr_align_margin_enabled():
                x_use = float(self._corr_margin_x_pt())
        except Exception:
            pass

        ann = {
            'id': str(uuid.uuid4()),
            'kind': 'manual_score',
            'page': int(page_index),
            'x_pt': float(x_use),
            'y_pt': float(y_pt),
            'exercise_code': str(ex_code),
            'exercise_label': str(ex_label),
            'points': float(pts),
            'style': {
                'radius_pt': 11.0,
                'border': BASIC_COLORS.get('rouge', '#EF4444'),
                'fill': '#FFFFFF',
                'label_fontsize': 11.0,
                'text_color': BASIC_COLORS.get('rouge', '#EF4444'),
            },
            'payload': {'tag': 'manual_score'},
        }
        anns.append(ann)

        try:
            self.project.save()
        except Exception:
            pass

        try:
            self._schedule_regenerate()
        except Exception:
            pass

        # MAJ UI
        try:
            self._refresh_marks_list()
        except Exception:
            pass
        try:
            self._refresh_correction_totals()
        except Exception:
            pass
        try:
            self._refresh_info_panel()
        except Exception:
            pass

    # ---------------- Helpers ----------------

    # ---------------- Helpers ----------------
    def _require_project(self) -> bool:
        if not self.project:
            messagebox.showwarning("Projet", "Crée ou ouvre d’abord un projet.")
            return False
        return True

    def _require_doc(self) -> bool:
        if not self._require_project():
            return False
        assert self.project is not None
        if not self.project.get_current_doc():
            messagebox.showwarning("Document", "Sélectionne un document dans l'onglet Projet.")
            return False
        return True

    def _scheme(self):
        assert self.project is not None
        d = ensure_scheme_dict(self.project.settings.get("grading_scheme"))
        self.project.settings["grading_scheme"] = d
        return scheme_from_dict(d)

    def _save_scheme(self, scheme) -> None:
        assert self.project is not None
        self.project.settings["grading_scheme"] = scheme_to_dict(scheme)
        self.project.save()
        self.refresh_grading_tree()
        self._refresh_correction_ui()
        self._refresh_info_panel()

    def _annotations_for_current_doc(self) -> list[dict]:
        assert self.project is not None
        doc = self.project.get_current_doc()
        if not doc:
            return []
        ann = self.project.settings.setdefault("annotations", {})
        self.project.settings.setdefault("pastille_label_style", "blue")
        self.project.settings.setdefault("corr_margin_cm", 0.5)
        self.project.settings.setdefault('guide_overlay_selected', '')
        self.project.settings.setdefault('guide_overlay_enabled', False)
        self.project.settings.setdefault('guide_overlay_opacity50', True)
        self.project.settings.setdefault('guide_overlay_opacity50', True)
        try:
            self.project.settings["corr_margin_cm"] = float(self._corr_margin_cm())
        except Exception:
            pass
        if not isinstance(ann, dict):
            ann = {}
            self.project.settings["annotations"] = ann
        lst = ann.setdefault(doc.id, [])
        if not isinstance(lst, list):
            lst = []
            ann[doc.id] = lst
        return lst

    def _refresh_files_list(self) -> None:
        self.files_list.delete(0, tk.END)
        self._doc_ids.clear()
        if not self.project:
            return
        for i, doc in enumerate(self.project.documents, start=1):
            label = f"{i}. {doc.original_name}"
            if "margin" in doc.variants:
                label += "  [marge]"
            if "corrected" in doc.variants:
                label += "  [corrigé]"
            self.files_list.insert(tk.END, label)
            self._doc_ids.append(doc.id)

        if self.project.current_doc_id and self.project.current_doc_id in self._doc_ids:
            idx = self._doc_ids.index(self.project.current_doc_id)
            self.files_list.selection_clear(0, tk.END)
            self.files_list.selection_set(idx)
            self.files_list.activate(idx)

    def _ensure_project_margins(self) -> None:
        if not self.project:
            return

        left_cm, right_cm = self._get_project_margins_lr()

        def _fmt_cm(v: float) -> str:
            s = f"{float(v):g}"
            return s.replace(".", "p")

        for doc in self.project.documents:
            if "margin" in doc.variants:
                p = self.project.rel_to_abs(doc.variants["margin"])
                if p.exists():
                    continue

            if not doc.input_rel:
                continue
            src = self.project.rel_to_abs(doc.input_rel)
            if not src.exists():
                continue

            # Pas de marge : la variante 'margin' pointe sur l'input
            if left_cm <= 0.0 and right_cm <= 0.0:
                doc.variants["margin"] = doc.input_rel
                continue

            out_work = self.project.unique_work_path(
                f"{doc.id}__marge_L{_fmt_cm(left_cm)}_R{_fmt_cm(right_cm)}cm.pdf"
            )
            try:
                add_margins(src, out_work, left_cm=left_cm, right_cm=right_cm)
                doc.variants["margin"] = self.project.abs_to_rel(out_work)
            except Exception:
                pass

        self.project.save()


    def _open_doc_in_viewer(self, doc_id: str, prefer_corrected: bool = True) -> None:
        assert self.project is not None
        doc = self.project.get_doc(doc_id)

        view_abs = None
        if prefer_corrected and "corrected" in doc.variants:
            p = self.project.rel_to_abs(doc.variants["corrected"])
            if p.exists():
                view_abs = p
        if not view_abs:
            view_abs = self.project.get_best_view_abs(doc)

        if not view_abs or not view_abs.exists():
            messagebox.showwarning("Visualisation", "Aucun fichier disponible pour ce document.")
            return

        self.project.current_doc_id = doc_id
        self._open_pdf_with_optional_overlay(view_abs)
        try:
            self._update_margin_guide()
        except Exception:
            pass
        self.nb.select(self.tab_view)
        self.view_subtabs.select(self.sub_correction)
        self._update_click_mode()

        self._refresh_correction_ui()
        self._refresh_info_panel()

    def open_current_corrected(self) -> None:
        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        assert doc is not None
        if "corrected" not in doc.variants:
            messagebox.showinfo("Correction", "Pas encore de PDF corrigé.")
            return
        p = self.project.rel_to_abs(doc.variants["corrected"])
        if not p.exists():
            messagebox.showwarning("Correction", "Le PDF corrigé est introuvable.")
            return
        self._open_pdf_with_optional_overlay(p)
        try:
            self._update_margin_guide()
        except Exception:
            pass

    # ---------------- Projet ----------------
    def new_project(self) -> None:
        parent = filedialog.askdirectory(title="Choisir un dossier parent pour le projet")
        if not parent:
            return
        name = self.project_name_var.get().strip() or "Nouveau projet"
        try:
            self.project = Project.create(Path(parent), name=name)
        except Exception as e:
            messagebox.showerror("Projet", f"Impossible de créer le projet.\n\n{e}")
            return

        self.project.settings["grading_scheme"] = ensure_scheme_dict(self.project.settings.get("grading_scheme"))
        self.project.settings.setdefault("annotations", {})
        self.project.settings.setdefault("pastille_label_style", "blue")
        try:
            self.c_label_style_var.set(str(self.project.settings.get("pastille_label_style", "blue")))
        except Exception:
            pass
        self.project.settings.setdefault("corr_margin_cm", 0.5)
        self.project.settings.setdefault('guide_overlay_selected', '')
        self.project.settings.setdefault('guide_overlay_enabled', False)
        try:
            self.c_align_margin_cm_var.set(str(self.project.settings.get("corr_margin_cm", 0.5)))
        except Exception:
            pass
        self.project.save()

        # recharge la bibliothèque d'images (outil Image)

        # GuideCorrection overlays
        try:
            self._refresh_guide_overlay_list()
            self.gc_overlay_select_var.set(str(self.project.settings.get('guide_overlay_selected', '') or ''))
            self.gc_overlay_enabled_var.set(bool(self.project.settings.get('guide_overlay_enabled', False)))
            self.gc_overlay_opacity50_var.set(bool(self.project.settings.get('guide_overlay_opacity50', True)))
        except Exception:
            pass
        try:
            self.image_tool.refresh_options()
        except Exception:
            pass

        self.project_name_var.set(self.project.name)
        self._refresh_files_list()
        self.viewer.clear()
        self.refresh_grading_tree()
        self._refresh_correction_ui()
        self._refresh_info_panel()
        self._update_click_mode()
        messagebox.showinfo("Projet", f"Projet créé :\n{self.project.root_dir}")

    def open_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Ouvrir un projet (project.json)",
            filetypes=_sanitize_tk_filetypes([("Projet JSON", "project.json"), ("JSON", "*.json"), ("Tous fichiers", "*.*")])
        )
        if not path:
            return
        try:
            self.project = Project.load_any(Path(path))
        except Exception as e:
            messagebox.showerror("Projet", f"Impossible d'ouvrir le projet.\n\n{e}")
            return

        self.project.settings["grading_scheme"] = ensure_scheme_dict(self.project.settings.get("grading_scheme"))
        self.project.settings.setdefault("annotations", {})
        self.project.settings.setdefault("pastille_label_style", "blue")
        try:
            self.c_label_style_var.set(str(self.project.settings.get("pastille_label_style", "blue")))
        except Exception:
            pass
        self.project.settings.setdefault("corr_margin_cm", 0.5)
        self.project.settings.setdefault('guide_overlay_selected', '')
        self.project.settings.setdefault('guide_overlay_enabled', False)
        self.project.settings.setdefault('guide_overlay_opacity50', True)
        try:
            self.c_align_margin_cm_var.set(str(self.project.settings.get("corr_margin_cm", 0.5)))
        except Exception:
            pass
        self.project.save()

        # recharge la bibliothèque d'images (outil Image)

        # GuideCorrection overlays
        try:
            self._refresh_guide_overlay_list()
            self.gc_overlay_select_var.set(str(self.project.settings.get('guide_overlay_selected', '') or ''))
            self.gc_overlay_enabled_var.set(bool(self.project.settings.get('guide_overlay_enabled', False)))
            self.gc_overlay_opacity50_var.set(bool(self.project.settings.get('guide_overlay_opacity50', True)))
        except Exception:
            pass
        try:
            self.image_tool.refresh_options()
        except Exception:
            pass

        self._ensure_project_margins()

        self.project_name_var.set(self.project.name)
        self._refresh_files_list()
        self.refresh_grading_tree()
        self._refresh_correction_ui()
        self._refresh_info_panel()
        self._update_click_mode()

        doc = self.project.get_current_doc()
        if doc:
            self._open_doc_in_viewer(doc.id)

    def save_project(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        self.project.name = self.project_name_var.get().strip() or self.project.name
        self.project.settings["grading_scheme"] = ensure_scheme_dict(self.project.settings.get("grading_scheme"))
        self.project.settings.setdefault("annotations", {})
        self.project.settings.setdefault("pastille_label_style", "blue")
        # Distance d'alignement dans la marge (Correction V0)
        # GuideCorrection : persiste la sélection / activation
        try:
            self.project.settings['guide_overlay_selected'] = str(self.gc_overlay_select_var.get() or '')
            self.project.settings['guide_overlay_enabled'] = bool(self.gc_overlay_enabled_var.get())
            self.project.settings['guide_overlay_opacity50'] = bool(self.gc_overlay_opacity50_var.get())
        except Exception:
            pass

        self.project.settings.setdefault("corr_margin_cm", 0.5)
        self.project.settings.setdefault('guide_overlay_selected', '')
        self.project.settings.setdefault('guide_overlay_enabled', False)
        self.project.settings.setdefault('guide_overlay_opacity50', True)
        try:
            self.project.settings["corr_margin_cm"] = float(self._corr_margin_cm())
        except Exception:
            pass
        try:
            self.project.save()
        except Exception as e:
            messagebox.showerror("Projet", f"Erreur d'enregistrement.\n\n{e}")
            return
        messagebox.showinfo("Projet", f"Projet enregistré :\n{self.project.project_file}")

    # ---------------- Scrolling (molette) ----------------
    def _is_descendant(self, widget: tk.Widget, ancestor: tk.Widget) -> bool:
        """Retourne True si widget est (ou est enfant de) ancestor."""
        try:
            w = widget
            while True:
                if w == ancestor:
                    return True
                parent_name = w.winfo_parent()
                if not parent_name:
                    break
                w = w.nametowidget(parent_name)
        except Exception:
            pass
        return False

    # ---------------- Ligne guide : alignement marge (Correction V0) ----------------
    def _clear_margin_guide(self) -> None:
        """Supprime la ligne guide d'alignement (si présente)."""
        try:
            v = getattr(self, "viewer", None)
            if v is None:
                return
            c = getattr(v, "canvas", None)
            if c is None:
                return
            c.delete("margin_guide")
        except Exception:
            pass

    def _update_margin_guide(self) -> None:
        """Affiche/masque la ligne guide verticale à la distance choisie (si 'Aligner dans la marge' est coché)."""
        # Toujours commencer par nettoyer
        self._clear_margin_guide()

        # Conditions d'affichage : onglet Visualisation PDF + sous-onglet Correction V0 + option cochée
        try:
            main = self.nb.tab(self.nb.select(), "text")
        except Exception:
            main = ""
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""

        if main != "Visualisation PDF" or sub != "Correction V0":
            return

        try:
            if not bool(self.c_align_margin_var.get()):
                return
        except Exception:
            return

        v = getattr(self, "viewer", None)
        if v is None:
            return
        canvas = getattr(v, "canvas", None)
        if canvas is None:
            return

        layout = getattr(v, "_layout", None)
        if not layout:
            return

        try:
            zoom = float(v.get_zoom()) if hasattr(v, "get_zoom") else float(getattr(v, "_zoom", 1.0) or 1.0)
        except Exception:
            zoom = 1.0

        # X en pixels : distance choisie depuis le bord gauche (points -> pixels via zoom)
        try:
            x_px = float(self._corr_margin_x_pt()) * zoom
        except Exception:
            return

        for info in layout:
            try:
                y0 = float(info.get("y0", 0.0))
                y1 = y0 + float(info.get("h_px", 0.0))
            except Exception:
                continue
            try:
                canvas.create_line(
                    x_px, y0, x_px, y1,
                    fill="#2F81F7",
                    width=2,
                    dash=(6, 4),
                    tags=("margin_guide",),
                    state="disabled",
                )
            except Exception:
                pass



    def _on_global_mousewheel(self, event) -> str | None:
        """Route la molette vers la zone sous le curseur (panneau correction ou PDF)."""
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        if not w:
            return None

        # delta: >0 = wheel up => scroll up (négatif pour yview_scroll)
        step = -1 if getattr(event, "delta", 0) > 0 else 1
        # Sur certains Mac, delta est très petit : on garde un pas fixe.
        units = 2

        # Priorité: panneau Correction V0 (gauche)
        try:
            c = getattr(self, "_corr_scroll_canvas", None)
            if c is not None and self._is_descendant(w, c):
                c.yview_scroll(step * units, "units")
                return "break"
        except Exception:
            pass

        # PDF viewer (droite)
        try:
            vc = getattr(getattr(self, "viewer", None), "canvas", None)
            if vc is not None and self._is_descendant(w, vc):
                vc.yview_scroll(step * units, "units")
                return "break"
        except Exception:
            pass

        return None

    def _on_global_mousewheel_linux(self, event) -> str | None:
        """Linux: Button-4 / Button-5."""
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            w = None
        if not w:
            return None

        # Button-4 = up, Button-5 = down
        step = -1 if getattr(event, "num", 0) == 4 else 1
        units = 2

        try:
            c = getattr(self, "_corr_scroll_canvas", None)
            if c is not None and self._is_descendant(w, c):
                c.yview_scroll(step * units, "units")
                return "break"
        except Exception:
            pass

        try:
            vc = getattr(getattr(self, "viewer", None), "canvas", None)
            if vc is not None and self._is_descendant(w, vc):
                vc.yview_scroll(step * units, "units")
                return "break"
        except Exception:
            pass

        return None

    # ---------------- Import ----------------
    def import_pdfs(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None

        # Demande les options de marge avant l'import
        def_w, def_pos = self._get_import_margin_choice_default()
        choice = self._ask_import_margin_options(def_w, def_pos)
        if choice is None:
            return
        width_cm, position = choice
        left_cm, right_cm = self._apply_import_margin_choice_to_settings(width_cm, position)

        paths = filedialog.askopenfilenames(title="Sélectionner des PDF", filetypes=[("PDF", "*.pdf")])
        if not paths:
            return

        def _fmt_cm(v: float) -> str:
            s = f"{float(v):g}"
            return s.replace(".", "p")

        last_doc_id: str | None = None

        for p in paths:
            src = Path(p)
            try:
                doc = self.project.import_pdf_copy(src)
                last_doc_id = doc.id

                input_abs = self.project.rel_to_abs(doc.input_rel) if doc.input_rel else None
                if not input_abs or not input_abs.exists():
                    raise FileNotFoundError("Fichier input introuvable après copie.")

                # Pas de marge : la variante 'margin' pointe sur l'input
                if left_cm <= 0.0 and right_cm <= 0.0:
                    doc.variants["margin"] = doc.input_rel
                else:
                    out_work = self.project.unique_work_path(
                        f"{doc.id}__marge_L{_fmt_cm(left_cm)}_R{_fmt_cm(right_cm)}cm.pdf"
                    )
                    add_margins(input_abs, out_work, left_cm=left_cm, right_cm=right_cm)
                    doc.variants["margin"] = self.project.abs_to_rel(out_work)

            except Exception as e:
                messagebox.showerror("Import", f"Impossible de traiter : {src.name}\n\n{e}")

        self.project.save()
        self._refresh_files_list()
        self._refresh_correction_ui()
        self._refresh_info_panel()

        if last_doc_id:
            self._open_doc_in_viewer(last_doc_id)


    def on_select_file(self, _evt=None) -> None:
        if not self.project:
            return
        sel = self.files_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < 0 or idx >= len(self._doc_ids):
            return
        self._open_doc_in_viewer(self._doc_ids[idx])

    # ---------------- Export verrouillé ----------------
    def export_current_locked(self) -> None:
        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        assert doc is not None

        src_abs = None
        if "corrected" in doc.variants:
            p = self.project.rel_to_abs(doc.variants["corrected"])
            if p.exists():
                src_abs = p
        if not src_abs:
            src_abs = self.project.get_best_view_abs(doc)

        if not src_abs or not src_abs.exists():
            messagebox.showwarning("Export", "Aucun fichier source disponible.")
            return

        suggested = f"{Path(doc.original_name).stem}__verrouille.pdf"
        exports_dir = (self.project.root_dir / "exports").resolve()

        chosen = filedialog.asksaveasfilename(
            title="Exporter corrigé verrouillé",
            defaultextension=".pdf",
            initialdir=str(exports_dir),
            initialfile=suggested,
            filetypes=[("PDF", "*.pdf")]
        )
        if not chosen:
            return

        try:
            owner_pw = str(self.project.settings.get("owner_password", "owner"))
            export_locked(src_abs, Path(chosen), owner_password=owner_pw)
            messagebox.showinfo("Export", f"Export verrouillé créé :\n{chosen}")
        except Exception as e:
            messagebox.showerror("Export", f"Erreur export.\n\n{e}")

    # ---------------- Notation : affichage + calcul totaux ----------------
    def refresh_grading_tree(self):
        for iid in self.gr_tree.get_children(""):
            self.gr_tree.delete(iid)

        if not self.project:
            self.total_general_var.set("—")
            return

        scheme = self._scheme()

        def total_good(node) -> float:
            if node.children:
                return sum(total_good(c) for c in node.children)
            if node.level() in (1, 2):
                if node.rubric:
                    return float(node.rubric.good)
                return 1.0
            return 0.0

        total_general = sum(total_good(ex) for ex in scheme.exercises)
        self.total_general_var.set(f"{total_general:g}")

        def insert_node(parent_iid: str, node):
            total = total_good(node)

            is_leaf = (not node.children) and (node.level() in (1, 2))
            if is_leaf and node.rubric:
                good = f"{node.rubric.good:g}"
                partial = f"{node.rubric.partial:g}"
                bad = f"{node.rubric.bad:g}"
            else:
                good = partial = bad = ""

            total_s = f"{total:g}" if total > 0 else ""

            self.gr_tree.insert(
                parent_iid, "end",
                iid=node.code,
                text=node.code,
                values=(node.label, good, partial, bad, total_s)
            )
            for ch in node.children:
                insert_node(node.code, ch)

        for ex in scheme.exercises:
            insert_node("", ex)

        self._expand_all_tree("")
        self._refresh_info_panel()

    def _expand_all_tree(self, parent: str = "") -> None:
        for iid in self.gr_tree.get_children(parent):
            self.gr_tree.item(iid, open=True)
            self._expand_all_tree(iid)

    def _selected_code(self) -> str | None:
        sel = self.gr_tree.selection()
        return sel[0] if sel else None

    def grading_generate(self) -> None:
        if not self._require_project():
            return
        try:
            n = int(self.nb_ex_var.get())
        except Exception:
            messagebox.showwarning("Notation", "Nombre d'exercices invalide.")
            return
        scheme = self._scheme()
        regenerate_exercises(scheme, n)
        self._save_scheme(scheme)

    def grading_add_exercise(self) -> None:
        if not self._require_project():
            return
        scheme = self._scheme()
        new_code = add_exercise(scheme)
        self._save_scheme(scheme)
        self.gr_tree.selection_set(new_code)

    def grading_add_sublevel(self) -> None:
        if not self._require_project():
            return
        code = self._selected_code()
        if not code:
            messagebox.showwarning("Notation", "Sélectionne un exercice (niveau 0).")
            return
        scheme = self._scheme()
        try:
            new_code = add_sublevel(scheme, code)
        except Exception as e:
            messagebox.showwarning("Notation", str(e))
            return
        self._save_scheme(scheme)
        self.gr_tree.selection_set(new_code)

    def grading_add_subsublevel(self) -> None:
        if not self._require_project():
            return
        code = self._selected_code()
        if not code:
            messagebox.showwarning("Notation", "Sélectionne un sous-niveau (niveau 1).")
            return
        scheme = self._scheme()
        try:
            new_code = add_subsublevel(scheme, code)
        except Exception as e:
            messagebox.showwarning("Notation", str(e))
            return
        self._save_scheme(scheme)
        self.gr_tree.selection_set(new_code)

    def grading_delete(self) -> None:
        if not self._require_project():
            return
        code = self._selected_code()
        if not code:
            return
        scheme = self._scheme()
        delete_node(scheme, code)
        self._save_scheme(scheme)

    def grading_delete_exercise(self) -> None:
        if not self._require_project():
            return
        code = self._selected_code()
        if not code:
            return
        scheme = self._scheme()
        found = find_node(scheme, code)
        if not found:
            return
        node, _ = found
        if node.level() != 0:
            messagebox.showwarning("Notation", "Sélectionne un exercice (niveau 0) pour le supprimer.")
            return
        delete_exercise(scheme, code)
        self._save_scheme(scheme)

    def grading_rename(self) -> None:
        if not self._require_project():
            return
        code = self._selected_code()
        if not code:
            return
        scheme = self._scheme()
        found = find_node(scheme, code)
        if not found:
            return
        node, _ = found

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Renommer {code}")
        dlg.geometry("520x170")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="Libellé :").pack(anchor="w", padx=12, pady=(12, 4))
        var = tk.StringVar(value=node.label)
        ent = ttk.Entry(dlg, textvariable=var, width=70)
        ent.pack(fill="x", padx=12)
        ent.focus_set()

        def save():
            set_label(scheme, code, var.get())
            self._save_scheme(scheme)
            dlg.destroy()

        ttk.Button(dlg, text="Enregistrer", command=save).pack(pady=12)

    def grading_edit_rubric(self) -> None:
        if not self._require_project():
            return
        code = self._selected_code()
        if not code:
            return
        scheme = self._scheme()
        found = find_node(scheme, code)
        if not found:
            return
        node, _ = found

        if node.level() == 0:
            messagebox.showwarning("Notation", "Pas de barème au niveau exercice.")
            return
        if node.children:
            messagebox.showwarning("Notation", "Ce niveau a des sous-niveaux : pas de barème (somme des enfants).")
            return

        rub = node.rubric
        good0 = rub.good if rub else 1.0
        part0 = rub.partial if rub else 0.5
        bad0 = rub.bad if rub else 0.0

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Barèmes {code}")
        dlg.geometry("440x260")
        dlg.transient(self.root)
        dlg.grab_set()

        frm = ttk.Frame(dlg)
        frm.pack(fill="both", expand=True, padx=12, pady=12)

        good = tk.StringVar(value=str(good0))
        part = tk.StringVar(value=str(part0))
        bad = tk.StringVar(value=str(bad0))

        ttk.Label(frm, text="Bonne réponse :").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=good, width=12).grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(frm, text="Réponse partielle :").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=part, width=12).grid(row=1, column=1, sticky="w", pady=6)

        ttk.Label(frm, text="Mauvaise réponse :").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=bad, width=12).grid(row=2, column=1, sticky="w", pady=6)

        def save():
            try:
                g = float(good.get().replace(",", "."))
                p = float(part.get().replace(",", "."))
                b = float(bad.get().replace(",", "."))
            except Exception:
                messagebox.showwarning("Notation", "Valeurs invalides (nombres attendus).")
                return

            try:
                set_rubric(scheme, code, g, p, b)
            except Exception as e:
                messagebox.showwarning("Notation", str(e))
                return

            self._save_scheme(scheme)
            dlg.destroy()

        ttk.Button(frm, text="Enregistrer", command=save).grid(row=3, column=0, columnspan=2, pady=18)

    def grading_export_scheme(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        scheme = self._scheme()
        suggested = f"bareme_{(self.project.name or 'projet').strip().replace(' ', '_')}.json"
        out = filedialog.asksaveasfilename(
            title="Exporter le barème",
            defaultextension=".json",
            initialdir=str(self.project.root_dir),
            initialfile=suggested,
            filetypes=[("Barème JSON", "*.json"), ("JSON", "*.json")]
        )
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(scheme_to_dict(scheme), f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Barème", f"Barème exporté :\n{out}")
        except Exception as e:
            messagebox.showerror("Barème", f"Erreur export barème.\n\n{e}")

    def grading_import_scheme(self) -> None:
        if not self._require_project():
            return
        assert self.project is not None
        inp = filedialog.askopenfilename(
            title="Importer un barème (JSON)",
            filetypes=_sanitize_tk_filetypes([("Barème JSON", "*.json"), ("JSON", "*.json"), ("Tous fichiers", "*.*")])
        )
        if not inp:
            return
        try:
            with open(inp, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.project.settings["grading_scheme"] = ensure_scheme_dict(data)
            self.project.save()
            self.refresh_grading_tree()
            self._refresh_correction_ui()
            self._refresh_info_panel()
            messagebox.showinfo("Barème", "Barème importé et appliqué au projet.")
        except Exception as e:
            messagebox.showerror("Barème", f"Erreur import barème.\n\n{e}")

    # ---------------- Correction V0 ----------------
    def _refresh_correction_ui(self) -> None:
        if not hasattr(self, "c_item_combo"):
            return
        if not self.project:
            self.c_item_combo.configure(values=[])
            self.c_item_var.set("")
            self.c_marks.delete(0, tk.END)
            self.c_points_lbl.configure(text="Points : —")
            return

        scheme = self._scheme()
        leaves = leaf_nodes(scheme)
        values = [f"{n.code} — {n.label or n.code}" for n in leaves]
        self.c_item_combo.configure(values=values)
        if not self.c_item_var.get() and values:
            self.c_item_var.set(values[0])

        self._update_points_preview()
        self._refresh_marks_list()
        self._refresh_correction_totals()

    def _selected_leaf_code(self) -> str | None:
        v = self.c_item_var.get().strip()
        if not v:
            return None
        return v.split("—")[0].strip()

    def _selected_leaf_label(self) -> str:
        v = self.c_item_var.get().strip()
        if not v:
            return ""
        parts = v.split("—", 1)
        if len(parts) == 2:
            return parts[1].strip()
        return parts[0].strip()

    def _update_points_preview(self) -> None:
        if not self.project:
            self.c_points_lbl.configure(text="Points : —")
            return
        code = self._selected_leaf_code()
        if not code:
            self.c_points_lbl.configure(text="Points : —")
            return
        scheme = self._scheme()
        result = self.c_result_var.get()
        pts = points_for(scheme, code, result)
        self.c_points_lbl.configure(text=f"Points : {pts:g}")


    def _refresh_marks_list(self) -> None:
        """Rafraîchit la liste 'Marques du document' (Correction V0).

        On liste :
        - pastilles (score_circle)
        - points manuels par exercice (manual_score)

        Un mapping (index listbox -> index annotation) est conservé pour permettre
        la suppression depuis cette liste.
        """
        if not hasattr(self, 'c_marks'):
            return
        self.c_marks.delete(0, tk.END)
        self._marks_list_map = []

        if not self.project:
            return
        doc = self.project.get_current_doc()
        if not doc:
            return

        # Max par exercice principal (pour afficher /max sur les points manuels)
        max_by_ex: dict[str, float] = {}
        try:
            scheme = self._scheme()

            def total_good(node) -> float:
                if getattr(node, 'children', None):
                    return float(sum(total_good(c) for c in node.children))
                try:
                    lvl = int(node.level())
                except Exception:
                    lvl = 0
                if lvl in (1, 2):
                    rub = getattr(node, 'rubric', None)
                    if rub is not None:
                        try:
                            return float(rub.good)
                        except Exception:
                            return 1.0
                    return 1.0
                return 0.0

            for ex in getattr(scheme, 'exercises', []) or []:
                try:
                    ex_code = str(ex.code)
                except Exception:
                    continue
                max_by_ex[ex_code] = float(total_good(ex))
        except Exception:
            max_by_ex = {}

        anns = self._annotations_for_current_doc()
        for i, a in enumerate(anns):
            if not isinstance(a, dict):
                continue
            kind = a.get('kind')

            if kind == 'score_circle':
                code = a.get('exercise_code', '?')
                label = a.get('exercise_label') or ''
                res = a.get('result', '?')
                try:
                    pts = float(a.get('points', 0.0))
                except Exception:
                    pts = 0.0
                page = int(a.get('page', 0))
                if label:
                    self.c_marks.insert(tk.END, f"p{page+1} • {code} • {label} • {res} • {pts:g}")
                else:
                    self.c_marks.insert(tk.END, f"p{page+1} • {code} • {res} • {pts:g}")
                self._marks_list_map.append(i)

            elif kind == 'manual_score':
                code = str(a.get('exercise_code', '') or '').strip()
                if code:
                    code = code.split('.', 1)[0]
                label = a.get('exercise_label') or (f"Exercice {code}" if code else 'Exercice')
                try:
                    pts = float(a.get('points', 0.0))
                except Exception:
                    pts = 0.0
                page = int(a.get('page', 0))
                mx = float(max_by_ex.get(code, 0.0)) if code else 0.0
                if mx > 0:
                    self.c_marks.insert(tk.END, f"p{page+1} • Ex {code} • {label} • MANUEL • {pts:g}/{mx:g}")
                else:
                    self.c_marks.insert(tk.END, f"p{page+1} • Ex {code} • {label} • MANUEL • {pts:g}")
                self._marks_list_map.append(i)

    def _on_marks_double_click(self, event=None) -> None:
        """Double-clic dans la liste 'Marques du document'.

        Amélioration confort :
        - si la ligne correspond à un marqueur de points manuels (kind='manual_score'),
          on ré-ouvre la fenêtre d'édition pour modifier les points (et éventuellement l'exercice).
        """
        if not hasattr(self, 'c_marks'):
            return
        if not self._require_doc():
            return

        try:
            lb_index = None
            if event is not None:
                try:
                    lb_index = int(self.c_marks.nearest(event.y))
                except Exception:
                    lb_index = None
            if lb_index is None:
                sel = self.c_marks.curselection()
                if not sel:
                    return
                lb_index = int(sel[0])
        except Exception:
            return

        try:
            if lb_index < 0 or lb_index >= len(getattr(self, '_marks_list_map', [])):
                return
            ann_index = int(self._marks_list_map[lb_index])
        except Exception:
            return

        anns = self._annotations_for_current_doc()
        if ann_index < 0 or ann_index >= len(anns):
            try:
                self._refresh_marks_list()
            except Exception:
                pass
            return

        a = anns[ann_index]
        if not isinstance(a, dict) or a.get('kind') != 'manual_score':
            return

        self._edit_manual_score_at_index(ann_index)

    def _edit_manual_score_at_index(self, ann_index: int) -> None:
        """Édite un marqueur 'manual_score' existant (points manuels) via une fenêtre.

        Conserve la position (page/x/y). Assure l'unicité : un seul 'manual_score' par exercice principal.
        """
        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        if not doc:
            return

        anns = self._annotations_for_current_doc()
        if ann_index < 0 or ann_index >= len(anns):
            return

        cur = anns[ann_index]
        if not isinstance(cur, dict) or cur.get('kind') != 'manual_score':
            return

        scheme = self._scheme()
        if not getattr(scheme, 'exercises', None):
            messagebox.showwarning('Points manuels', "Aucun exercice défini dans l'onglet Notation.")
            return

        # Max points par exercice principal (même logique que _add_manual_score_at)
        def total_good(node) -> float:
            if getattr(node, 'children', None):
                return float(sum(total_good(c) for c in node.children))
            try:
                lvl = int(node.level())
            except Exception:
                lvl = 0
            if lvl in (1, 2):
                rub = getattr(node, 'rubric', None)
                if rub is not None:
                    try:
                        return float(rub.good)
                    except Exception:
                        return 1.0
                return 1.0
            return 0.0

        ex_items = []  # (code, label, max)
        for ex in scheme.exercises:
            try:
                code = str(ex.code)
            except Exception:
                continue
            label = str(ex.label) if getattr(ex, 'label', None) else f"Exercice {code}"
            mx = float(total_good(ex))
            ex_items.append((code, label, mx))

        if not ex_items:
            messagebox.showwarning('Points manuels', "Aucun exercice défini dans l'onglet Notation.")
            return

        # Valeurs courantes
        cur_code = str(cur.get('exercise_code', '') or '').strip()
        if cur_code:
            cur_code = cur_code.split('.', 1)[0]
        try:
            cur_pts = float(cur.get('points', 0.0))
        except Exception:
            cur_pts = 0.0

        # --- UI dialog ---
        top = tk.Toplevel(self.root)
        top.title('Modifier points manuels (exercice)')
        top.transient(self.root)
        top.grab_set()

        frm = ttk.Frame(top, padding=12)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text='Modifier des points manuels (par exercice principal)').pack(anchor='w')
        ttk.Label(frm, text="Ces points remplacent le total des pastilles de l'exercice dans la note.").pack(anchor='w', pady=(2, 10))

        # Exercice
        row1 = ttk.Frame(frm)
        row1.pack(fill='x', pady=(0, 8))
        ttk.Label(row1, text='Exercice :').pack(side='left')

        disp = [f"Ex {c} — {lab} (max {mx:g})" for c, lab, mx in ex_items]
        code_by_disp = {d: c for d, (c, _, _) in zip(disp, ex_items)}
        mx_by_code = {c: mx for c, _, mx in ex_items}
        label_by_code = {c: lab for c, lab, _ in ex_items}

        if cur_code not in mx_by_code:
            cur_code = ex_items[0][0]
        cur_disp = next((d for d in disp if code_by_disp.get(d) == cur_code), disp[0])

        disp_var = tk.StringVar(value=cur_disp)
        cb = ttk.Combobox(row1, state='readonly', values=disp, textvariable=disp_var, width=44)
        cb.pack(side='left', padx=(8, 0), fill='x', expand=True)

        # Points
        row2 = ttk.Frame(frm)
        row2.pack(fill='x', pady=(0, 8))
        ttk.Label(row2, text='Points :').pack(side='left')

        pts_var = tk.StringVar(value=str(cur_pts).replace('.', ','))
        ent = ttk.Entry(row2, textvariable=pts_var, width=10)
        ent.pack(side='left', padx=(8, 0))

        max_lbl_var = tk.StringVar(value=f"/ {mx_by_code.get(cur_code, 0.0):g}")
        ttk.Label(row2, textvariable=max_lbl_var).pack(side='left', padx=6)

        def _on_ex_change(_evt=None):
            c = code_by_disp.get(disp_var.get(), ex_items[0][0])
            try:
                self.manual_score_ex_var.set(c)
            except Exception:
                pass
            max_lbl_var.set(f"/ {mx_by_code.get(c, 0.0):g}")

        cb.bind('<<ComboboxSelected>>', _on_ex_change)

        # Boutons
        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=(10, 0))

        result = {'ok': False}

        def on_ok():
            result['ok'] = True
            try:
                top.destroy()
            except Exception:
                pass

        def on_cancel():
            result['ok'] = False
            try:
                top.destroy()
            except Exception:
                pass

        ttk.Button(btns, text='Annuler', command=on_cancel).pack(side='right')
        ttk.Button(btns, text='OK', command=on_ok).pack(side='right', padx=(0, 8))

        try:
            ent.focus_set()
            ent.selection_range(0, 'end')
        except Exception:
            pass

        self.root.wait_window(top)
        if not result.get('ok'):
            return

        # Lecture valeurs
        ex_code = code_by_disp.get(disp_var.get(), ex_items[0][0])
        ex_label = label_by_code.get(ex_code, f"Exercice {ex_code}")

        raw_pts = (pts_var.get() or '').strip().replace(',', '.')
        try:
            pts = float(raw_pts)
        except Exception:
            messagebox.showwarning('Points manuels', 'Valeur de points invalide.')
            return

        # mémorise pour la prochaine fois
        try:
            self.manual_score_ex_var.set(ex_code)
        except Exception:
            pass
        try:
            self.manual_score_pts_var.set(str(pts).replace('.', ','))
        except Exception:
            pass

        # Mise à jour du marqueur (position conservée)
        cur['exercise_code'] = str(ex_code)
        cur['exercise_label'] = str(ex_label)
        cur['points'] = float(pts)

        # Assure payload/tag
        pl = cur.get('payload')
        if not isinstance(pl, dict):
            pl = {}
        pl['tag'] = 'manual_score'
        cur['payload'] = pl

        # Unicité : supprime d'éventuels autres 'manual_score' du même exercice (hors celui-ci)
        new_anns = []
        for i, a in enumerate(anns):
            if i == ann_index:
                new_anns.append(cur)
                continue
            if isinstance(a, dict) and a.get('kind') == 'manual_score':
                c = str(a.get('exercise_code', '') or '').strip()
                c = c.split('.', 1)[0] if c else ''
                if c == ex_code:
                    continue
            new_anns.append(a)
        anns[:] = new_anns

        try:
            self.project.save()
        except Exception:
            pass

        try:
            self._schedule_regenerate()
        except Exception:
            pass

        # MAJ UI
        try:
            self._refresh_marks_list()
        except Exception:
            pass
        try:
            self._refresh_correction_totals()
        except Exception:
            pass
        try:
            self._refresh_info_panel()
        except Exception:
            pass



    def _scheme_max_total(self) -> float:
        if not self.project:
            return 0.0
        scheme = self._scheme()

        def total_good(node) -> float:
            if node.children:
                return sum(total_good(c) for c in node.children)
            if node.level() in (1, 2):
                if node.rubric:
                    return float(node.rubric.good)
                return 1.0
            return 0.0

        return float(sum(total_good(ex) for ex in scheme.exercises))


    def _doc_attrib_total(self) -> float:
        """Total des points attribués au document courant.

        Règle :
        - par défaut on additionne les pastilles (score_circle)
        - si un marqueur 'manual_score' existe pour un exercice principal, il **remplace**
          le total des pastilles de cet exercice dans la note.
        """
        if not self.project:
            return 0.0
        doc = self.project.get_current_doc()
        if not doc:
            return 0.0
        ann = self.project.settings.get("annotations", {})
        anns = ann.get(doc.id, []) if isinstance(ann, dict) else []
        if not isinstance(anns, list):
            return 0.0

        sum_by_ex: dict[str, float] = {}
        manual_by_ex: dict[str, float] = {}

        for a in anns:
            if not isinstance(a, dict):
                continue
            kind = a.get('kind')
            if kind == 'score_circle':
                code = str(a.get('exercise_code', '') or '').strip()
                if not code:
                    continue
                ex_code = code.split('.', 1)[0]
                try:
                    pts = float(a.get('points', 0.0))
                except Exception:
                    pts = 0.0
                sum_by_ex[ex_code] = sum_by_ex.get(ex_code, 0.0) + float(pts)
            elif kind == 'manual_score':
                code = str(a.get('exercise_code', '') or '').strip()
                if not code:
                    continue
                ex_code = code.split('.', 1)[0]
                try:
                    pts = float(a.get('points', 0.0))
                except Exception:
                    pts = 0.0
                manual_by_ex[ex_code] = float(pts)

        total = 0.0
        all_ex = set(sum_by_ex.keys()) | set(manual_by_ex.keys())
        for ex_code in all_ex:
            if ex_code in manual_by_ex:
                total += float(manual_by_ex.get(ex_code, 0.0))
            else:
                total += float(sum_by_ex.get(ex_code, 0.0))
        return float(total)

    def _refresh_correction_totals(self) -> None:
        if not hasattr(self, "c_total_var"):
            return
        if not self.project or not self.project.get_current_doc():
            self.c_total_var.set("Total attribué : — / —")
            return
        attrib = self._doc_attrib_total()
        mx = self._scheme_max_total()
        self.c_total_var.set(f"Total attribué : {attrib:g} / {mx:g}")

    def _corr_align_margin_enabled(self) -> bool:
        try:
            return bool(getattr(self, "c_align_margin_var", None).get())
        except Exception:
            return False

    def _corr_margin_cm(self) -> float:
        """Distance (en cm) depuis le bord gauche pour l'alignement dans la marge."""
        default = 0.5
        raw = None
        try:
            raw = getattr(self, "c_align_margin_cm_var", None).get()
        except Exception:
            raw = None
        if raw is None or str(raw).strip() == "":
            try:
                if self.project:
                    raw = self.project.settings.get("corr_margin_cm", default)
            except Exception:
                raw = default
        try:
            cm = float(str(raw).replace(",", "."))
        except Exception:
            cm = float(default)
        if cm < 0.0:
            cm = 0.0
        if cm > 10.0:
            cm = 10.0
        return float(cm)

    def _apply_corr_margin_cm(self, *_evt) -> None:
        """Normalise/enregistre la distance d'alignement et met à jour la ligne guide."""
        cm = self._corr_margin_cm()
        # Normalise l'affichage
        try:
            if hasattr(self, "c_align_margin_cm_var"):
                txt = f"{cm:.2f}".rstrip("0").rstrip(".")
                self.c_align_margin_cm_var.set(txt)
        except Exception:
            pass
        if self.project:
            try:
                self.project.settings["corr_margin_cm"] = cm
            except Exception:
                pass
        try:
            self._update_margin_guide()
        except Exception:
            pass

    def _corr_margin_x_pt(self) -> float:
        """X (en points PDF) pour aligner une pastille à la distance choisie dans la marge."""
        cm = self._corr_margin_cm()
        return float((cm / 2.54) * 72.0)


    def _find_nearest_marker(self, page_index: int, x_pt: float, y_pt: float, threshold_pt: float = 14.0):
        """
        Renvoie (idx, ann_dict) du marqueur le plus proche sur la page, ou (None, None).
        """
        if not self.project:
            return None, None
        anns = self._annotations_for_current_doc()
        best_idx = None
        best_ann = None
        best_d2 = None
        for i, a in enumerate(anns):
            if not isinstance(a, dict) or a.get("kind") != "score_circle":
                continue
            if int(a.get("page", -1)) != int(page_index):
                continue
            try:
                ax = float(a.get("x_pt", 0.0))
                ay = float(a.get("y_pt", 0.0))
            except Exception:
                continue
            dx = ax - x_pt
            dy = ay - y_pt
            d2 = dx*dx + dy*dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best_idx = i
                best_ann = a
        if best_idx is None or best_ann is None:
            return None, None
        if best_d2 is not None and best_d2 <= threshold_pt*threshold_pt:
            return best_idx, best_ann
        return None, None
    def _on_pdf_click_for_correction(self, page_index: int, x_pt: float, y_pt: float) -> None:
        # Mode déplacement
        if hasattr(self, "c_move_var") and self.c_move_var.get():
            if not self._require_doc():
                return
            idx, ann = self._find_nearest_marker(page_index, x_pt, y_pt)
            if idx is None or ann is None:
                if hasattr(self, "_click_hint"):
                    self._click_hint.configure(text="Mode clic : ON • (déplacer) aucune pastille à proximité")
                self._drag_active = False
                self._drag_target_idx = None
                return

            self._drag_active = True
            self._drag_target_idx = idx
            code = ann.get("exercise_code", "?")
            if hasattr(self, "_click_hint"):
                self._click_hint.configure(text=f"Mode clic : ON • déplacement {code}… (glisse puis relâche)")
            return

        # Ajout normal
        if hasattr(self, "_click_hint"):
            self._click_hint.configure(text=f"Mode clic : ON • clic p{page_index+1}")

        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        assert doc is not None

        code = self._selected_leaf_code()
        if not code:
            messagebox.showwarning("Correction", "Choisis un item (feuille) dans la liste.")
            return

        label = self._selected_leaf_label() or code
        scheme = self._scheme()
        result = self.c_result_var.get()
        pts = points_for(scheme, code, result)

        ann = {
            "id": str(uuid.uuid4()),
            "kind": "score_circle",
            "page": int(page_index),
            "x_pt": float(self._corr_margin_x_pt() if self._corr_align_margin_enabled() else x_pt),
            "y_pt": float(y_pt),
            "exercise_code": code,
            "exercise_label": label,
            "result": result,
            "points": float(pts),
            "style": {
                "radius_pt": 9.0,
                "fill": RESULT_COLORS.get(result, RESULT_COLORS["good"]),
                "label_fontsize": 11.0,
                "label_dx_pt": 15.0,
                "label_style": self._get_pastille_label_style(),
            },
            "payload": {}
        }

        anns = self._annotations_for_current_doc()
        anns.append(ann)
        self.project.save()

        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()
        self._refresh_correction_totals()

        if hasattr(self, "_click_hint"):
            self._click_hint.configure(text=f"Mode clic : ON • ajout {code} ({result})")
    def _on_pdf_drag_for_correction(self, page_index: int, x_pt: float, y_pt: float) -> None:
        if not (hasattr(self, "c_move_var") and self.c_move_var.get()):
            return
        if not self._drag_active or self._drag_target_idx is None:
            return
        if not self._require_doc():
            return

        anns = self._annotations_for_current_doc()
        if self._drag_target_idx < 0 or self._drag_target_idx >= len(anns):
            return
        ann = anns[self._drag_target_idx]
        if not isinstance(ann, dict) or ann.get("kind") != "score_circle":
            return
        if int(ann.get("page", -1)) != int(page_index):
            return

        x_use = self._corr_margin_x_pt() if self._corr_align_margin_enabled() else x_pt


        ann["x_pt"] = float(x_use)
        ann["y_pt"] = float(y_pt)
    def _on_pdf_release_for_correction(self, page_index: int, x_pt: float, y_pt: float) -> None:
        if not (hasattr(self, "c_move_var") and self.c_move_var.get()):
            return
        if not self._drag_active or self._drag_target_idx is None:
            return
        if not self._require_doc():
            return

        assert self.project is not None

        # Applique la position finale au relâchement (plus robuste que dépendre uniquement de <B1-Motion>)
        try:
            anns = self._annotations_for_current_doc()
            idx = int(self._drag_target_idx) if self._drag_target_idx is not None else None
            if idx is not None and 0 <= idx < len(anns):
                ann = anns[idx]
                if isinstance(ann, dict) and ann.get("kind") == "score_circle":
                    x_use = self._corr_margin_x_pt() if self._corr_align_margin_enabled() else x_pt
                    ann["page"] = int(page_index)
                    ann["x_pt"] = float(x_use)
                    ann["y_pt"] = float(y_pt)
        except Exception:
            pass

        self.project.save()

        # Re-génère pour appliquer le déplacement dans le PDF
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()
        self._refresh_correction_totals()

        self._drag_active = False
        self._drag_target_idx = None

        if hasattr(self, "_click_hint"):
            self._click_hint.configure(text="Mode clic : ON • déplacement terminé")


    def _add_score_circle_at(self, page_index: int, x_pt: float, y_pt: float, code: str, label: str, result: str) -> None:
        if not self._require_doc():
            return
        assert self.project is not None
        scheme = self._scheme()
        pts = points_for(scheme, code, result)

        ann = {
            "id": str(uuid.uuid4()),
            "kind": "score_circle",
            "page": int(page_index),
            "x_pt": float(self._corr_margin_x_pt() if self._corr_align_margin_enabled() else x_pt),
            "y_pt": float(y_pt),
            "exercise_code": code,
            "exercise_label": label or code,
            "result": result,
            "points": float(pts),
            "style": {
                "radius_pt": 9.0,
                "fill": RESULT_COLORS.get(result, RESULT_COLORS["good"]),
                "label_fontsize": 11.0,
                "label_dx_pt": 15.0,
                "label_style": self._get_pastille_label_style(),
            },
            "payload": {}
        }

        anns = self._annotations_for_current_doc()
        anns.append(ann)
        self.project.save()

        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()
        self._refresh_correction_totals()

    def _on_pdf_context_menu(self, page_index: int, x_pt: float, y_pt: float, x_root: int, y_root: int) -> None:
        """
        Clic-droit sur le PDF (dans Correction V0) :
        menu hiérarchique Exercice -> Sous-niveau -> (option sous-sous) -> Bonne/Partielle/Mauvaise.
        Place directement la pastille à la position du clic-droit.
        """
        if not self.project:
            return

        # En mode "déplacer une pastille", on ne déclenche pas le menu (sinon conflit)
        if hasattr(self, "c_move_var") and self.c_move_var.get():
            return

        scheme = self._scheme()

        menu = tk.Menu(self.root, tearoff=0)
        # tentative thème sombre (sur certains OS, le menu reste natif)
        try:
            menu.configure(bg=DARK_BG_2, fg="white", activebackground="#2F81F7", activeforeground="white")
        except Exception:
            pass

        if not scheme.exercises:
            menu.add_command(label="(Aucun barème défini)", state="disabled")
        else:
            def add_leaf(parent_menu: tk.Menu, code: str, label: str):
                leaf_menu = tk.Menu(parent_menu, tearoff=0)
                try:
                    leaf_menu.configure(bg=DARK_BG_2, fg="white", activebackground="#2F81F7", activeforeground="white")
                except Exception:
                    pass

                leaf_menu.add_command(
                    label="Bonne (vert)",
                    command=lambda c=code, l=label: self._add_score_circle_at(page_index, x_pt, y_pt, c, l, "good")
                )
                leaf_menu.add_command(
                    label="Partielle (orange)",
                    command=lambda c=code, l=label: self._add_score_circle_at(page_index, x_pt, y_pt, c, l, "partial")
                )
                leaf_menu.add_command(
                    label="Mauvaise (rouge)",
                    command=lambda c=code, l=label: self._add_score_circle_at(page_index, x_pt, y_pt, c, l, "bad")
                )
                parent_menu.add_cascade(label=f"{code} — {label}", menu=leaf_menu)

            for ex in scheme.exercises:
                ex_menu = tk.Menu(menu, tearoff=0)
                try:
                    ex_menu.configure(bg=DARK_BG_2, fg="white", activebackground="#2F81F7", activeforeground="white")
                except Exception:
                    pass

                # niveaux 1 : ex.children
                if not ex.children:
                    # ex lui-même peut être une feuille (rare) : gère au cas où
                    add_leaf(ex_menu, ex.code, ex.label)
                else:
                    for sub in ex.children:
                        if sub.children:
                            sub_menu = tk.Menu(ex_menu, tearoff=0)
                            try:
                                sub_menu.configure(bg=DARK_BG_2, fg="white", activebackground="#2F81F7", activeforeground="white")
                            except Exception:
                                pass
                            for sub2 in sub.children:
                                add_leaf(sub_menu, sub2.code, sub2.label)
                            ex_menu.add_cascade(label=f"{sub.code} — {sub.label}", menu=sub_menu)
                        else:
                            add_leaf(ex_menu, sub.code, sub.label)

                menu.add_cascade(label=f"{ex.code} — {ex.label}", menu=ex_menu)

        # Option pratique : si tu veux juste sélectionner dans le panneau sans poser
        menu.add_separator()
        menu.add_command(label="Fermer", command=menu.destroy)

        try:
            menu.tk_popup(x_root, y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass




        # ---------------- Launch ----------------


    def c_regenerate(self) -> None:
        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        assert doc is not None

        if "margin" not in doc.variants:
            self._ensure_project_margins()
        if "margin" not in doc.variants:
            messagebox.showwarning("Correction", "Impossible de trouver / créer la variante 'margin'.")
            return

        base_pdf = self.project.rel_to_abs(doc.variants["margin"])
        if not base_pdf.exists():
            messagebox.showwarning("Correction", "PDF marge introuvable.")
            return

        anns = self._annotations_for_current_doc()
        out_pdf = self.project.unique_work_path(f"{doc.id}__corrected.pdf")
        try:
            apply_annotations(base_pdf, out_pdf, anns, project_root=self.project.root_dir)
        except Exception as e:
            messagebox.showerror("Correction", f"Erreur génération corrigé.\n\n{e}")
            return

        doc.variants["corrected"] = self.project.abs_to_rel(out_pdf)
        self.project.current_variant = "corrected"
        self.project.save()

        # Rafraîchissement robuste (important en version packagée .exe : les exceptions Tk peuvent être silencieuses)
        def _open_corrected_after_regen():
            try:
                self._open_pdf_with_optional_overlay(out_pdf, preserve_view=True, lazy_render=True)
                try:
                    self._update_margin_guide()
                except Exception:
                    pass
                try:
                    self._update_click_mode()
                except Exception:
                    pass
            except TypeError:
                # Compat avec d'anciennes versions de PDFViewer.open_pdf(pdf_path)
                self._open_pdf_with_optional_overlay(out_pdf)
                try:
                    self._update_margin_guide()
                except Exception:
                    pass
                try:
                    self._update_click_mode()
                except Exception:
                    pass
            except Exception:
                # dernier recours : retenter un peu plus tard (écriture fichier / cache OS)
                try:
                    self.root.after(80, lambda: self._open_pdf_with_optional_overlay(out_pdf, preserve_view=True, lazy_render=True))
                except Exception:
                    pass
        # Laisse Tk finir le callback (menu/context) avant de re-render le canvas
        try:
            self.root.after_idle(_open_corrected_after_regen)
        except Exception:
            _open_corrected_after_regen()

    def c_delete_last(self) -> None:
        if not self._require_doc():
            return
        anns = self._annotations_for_current_doc()
        if not anns:
            return
        anns.pop()
        assert self.project is not None
        self.project.save()
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()

    def c_delete_selected(self) -> None:
        """Supprime la marque sélectionnée dans la liste Correction V0.

        Supporte :
        - pastilles (score_circle)
        - points manuels (manual_score)
        """
        if not self._require_doc():
            return
        sel = self.c_marks.curselection()
        if not sel:
            return
        idx = int(sel[0])

        anns = self._annotations_for_current_doc()

        # Mapping listbox -> index annotation (créé dans _refresh_marks_list)
        mapping = getattr(self, '_marks_list_map', None)
        ann_idx = None
        if isinstance(mapping, list) and 0 <= idx < len(mapping):
            try:
                ann_idx = int(mapping[idx])
            except Exception:
                ann_idx = None

        # Fallback (ancienne logique) : uniquement pastilles
        if ann_idx is None:
            score_idxs = [i for i, a in enumerate(anns) if isinstance(a, dict) and a.get('kind') == 'score_circle']
            if idx < 0 or idx >= len(score_idxs):
                return
            ann_idx = score_idxs[idx]

        if ann_idx is None or ann_idx < 0 or ann_idx >= len(anns):
            return

        try:
            del anns[ann_idx]
        except Exception:
            return

        assert self.project is not None
        self.project.save()
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()
        self._refresh_correction_totals()

    # ---------------- Infos : points attribués / max ----------------

    # ---------------- Note finale (récapitulatif) ----------------

    def _remove_final_note_annotations(self, anns: list[dict]) -> int:
        """Supprime les annotations de type 'note finale' (tag final_note). Retourne le nombre supprimé."""
        removed = 0
        kept: list[dict] = []
        for a in anns:
            try:
                if isinstance(a, dict) and a.get("kind") == "textbox":
                    payload = a.get("payload") or {}
                    if isinstance(payload, dict) and payload.get("tag") in ("final_note", "final_note_marker"):
                        removed += 1
                        continue
            except Exception:
                pass
            kept.append(a)
        anns[:] = kept
        return removed

    def _build_final_note_text(self) -> str:
        """Construit le texte du récapitulatif (points par exercice + total + note /20)."""
        if not self.project or not self.project.get_current_doc():
            return ""

        scheme = self._scheme()

        def total_good(node) -> float:
            if getattr(node, "children", None):
                return float(sum(total_good(c) for c in node.children))
            try:
                lvl = int(node.level())
            except Exception:
                lvl = 0
            if lvl in (1, 2):
                rub = getattr(node, "rubric", None)
                if rub is not None:
                    try:
                        return float(rub.good)
                    except Exception:
                        return 1.0
                return 1.0
            return 0.0

        max_by_ex: dict[str, float] = {}
        for ex in scheme.exercises:
            try:
                ex_code = str(ex.code)
            except Exception:
                continue
            max_by_ex[ex_code] = float(total_good(ex))

        # Attribué depuis les pastilles
        attrib_by_ex: dict[str, float] = {k: 0.0 for k in max_by_ex.keys()}
        anns = self._annotations_for_current_doc()
        for a in anns:
            if not isinstance(a, dict) or a.get("kind") != "score_circle":
                continue
            code = str(a.get("exercise_code", "")).strip()
            if not code:
                continue
            ex_code = code.split(".", 1)[0]
            try:
                pts = float(a.get("points", 0.0))
            except Exception:
                pts = 0.0
            attrib_by_ex[ex_code] = attrib_by_ex.get(ex_code, 0.0) + pts

        # Points manuels: remplace le total des pastilles pour l'exercice principal
        manual_set: set[str] = set()
        for a in anns:
            if not isinstance(a, dict) or a.get("kind") != "manual_score":
                continue
            code = str(a.get("exercise_code", "")).strip()
            if not code:
                continue
            ex_code = code.split(".", 1)[0]
            if ex_code not in attrib_by_ex:
                continue
            try:
                pts = float(a.get("points", 0.0))
            except Exception:
                pts = 0.0
            attrib_by_ex[ex_code] = float(pts)
            manual_set.add(ex_code)

        def sort_key_ex(s: str):
            try:
                return (0, int(s))
            except Exception:
                return (1, s)

        max_total = float(sum(max_by_ex.values()))
        attrib_total = float(sum(attrib_by_ex.values()))

        lines: list[str] = []
        lines.append("RÉCAPITULATIF")
        for ex_code in sorted(max_by_ex.keys(), key=sort_key_ex):
            mx = float(max_by_ex.get(ex_code, 0.0))
            at = float(attrib_by_ex.get(ex_code, 0.0))
            # format compact (préserve la largeur de la marge)
            prefix = f"Ex {ex_code}"
            if 'manual_set' in locals() and ex_code in manual_set:
                prefix = f"{prefix} (M)"
            lines.append(f"{prefix} : {at:g}/{mx:g}")

        lines.append("")
        lines.append(f"Total : {attrib_total:g}/{max_total:g}")

        if max_total > 0:
            note20 = round(20.0 * attrib_total / max_total, 2)
            # joli : 14.0 -> 14
            if abs(note20 - round(note20)) < 1e-9:
                note20_s = f"{int(round(note20))}"
            else:
                note20_s = f"{note20:g}"
            lines.append(f"Note : {note20_s}/20")

        return "\n".join(lines).strip()
    def c_insert_final_note(self) -> None:
        if not self._require_doc():
            return
        assert self.project is not None
        doc = self.project.get_current_doc()
        assert doc is not None

        # S'assure d'avoir la variante marge (base de la régénération)
        if "margin" not in doc.variants:
            self._ensure_project_margins()
        if "margin" not in doc.variants:
            messagebox.showwarning("Correction", "Impossible de trouver / créer la variante 'margin'.")
            return

        base_pdf = self.project.rel_to_abs(doc.variants["margin"])
        if not base_pdf.exists():
            messagebox.showwarning("Correction", "PDF marge introuvable.")
            return

        # Page cible
        target = "first"
        try:
            target = str(self.c_final_target_var.get() or "first")
        except Exception:
            target = "first"

        page_index = 0
        if target == "current":
            try:
                page_index = int(getattr(self, "_last_interaction_page", 0) or 0)
            except Exception:
                page_index = 0

        # Texte
        text = self._build_final_note_text()
        if not text:
            messagebox.showwarning("Correction", "Rien à insérer (projet/document non prêt).")
            return

        # Marges disponibles (cm)
        left_cm, right_cm = self._get_project_margins_lr()
        left_pt = (left_cm / 2.54) * 72.0
        right_pt = (right_cm / 2.54) * 72.0

        # Choix du placement :
        # - si une marge est suffisamment large, on place le cadre dedans
        # - sinon (marge 0 ou marge trop petite), on place en haut-gauche sur la page
        #   avec fond semi-transparent.
        MIN_MARGIN_PT = 110.0  # ~3,9 cm
        placement = "overlay_topleft"
        if left_pt >= MIN_MARGIN_PT:
            placement = "left_margin"
        elif right_pt >= MIN_MARGIN_PT:
            placement = "right_margin"

        # Dimensions page
        try:
            pdf = fitz.open(str(base_pdf))
            try:
                if page_index < 0 or page_index >= pdf.page_count:
                    page_index = 0
                page = pdf.load_page(page_index)
                w = float(page.rect.width)
                h = float(page.rect.height)
            finally:
                pdf.close()
        except Exception:
            w, h = 595.0, 842.0  # A4 portrait approx.

        pad = 10.0
        y0 = 20.0

        # Largeur du cadre (pts). En mode "overlay" (pas assez de marge),
        # on la réduit de 50% pour éviter un cadre trop large.
        overlay_w_normal = min(260.0, w * 0.45)
        overlay_w_normal = max(180.0, overlay_w_normal)
        overlay_w_overlay = overlay_w_normal * 0.5

        bg_color = None
        bg_opacity = None

        if placement == "left_margin":
            x0 = pad
            x1 = min(w - pad, max(x0 + 80.0, left_pt - pad))
        elif placement == "right_margin":
            x1 = w - pad
            x0 = max(pad, (w - right_pt) + pad)
        else:
            # Overlay en haut-gauche (coin haut-gauche)
            x0 = pad
            x1 = min(w - pad, x0 + overlay_w_overlay)
            bg_color = "#FFFFFF"
            bg_opacity = 0.5

        line_count = max(1, len(text.splitlines()))
        # En overlay (cadre plus étroit), on anticipe davantage de retours à la ligne
        # et on augmente la hauteur minimale.
        wrap_factor = 1.0
        min_h = 140.0
        if placement == "overlay_topleft":
            wrap_factor = 1.6
            min_h = 170.0

        est_h = 13.0 * line_count * wrap_factor + 30.0
        y1 = min(h - 20.0, y0 + max(min_h, est_h))

        rect = [float(x0), float(y0), float(x1), float(y1)]

        anns = self._annotations_for_current_doc()
        self._remove_final_note_annotations(anns)

        ann_style = {
            "color": "rouge",
            "fontsize": 11.0,
            "fontname": "Helvetica",
            "border_color": "rouge",
            "border_width_pt": 1.3,
            "padding_pt": 5.0,
            "bold_total": True,
        }
        if bg_color is not None:
            ann_style["bg_color"] = bg_color
        if bg_opacity is not None:
            ann_style["fill_opacity"] = float(bg_opacity)

        ann = {
            "id": str(uuid.uuid4()),
            "kind": "textbox",
            "page": int(page_index),
            "rect": rect,
            "text": text,
            "style": ann_style,
            "payload": {"tag": "final_note"},
        }
        anns.append(ann)

        # Marqueur invisible pour la Synthèse Note (recherche fiable par texte)
        mx0 = float(rect[0] + 2.0)
        my0 = float(rect[1] + 2.0)
        marker_rect = [mx0, my0, mx0 + 80.0, my0 + 10.0]
        marker_ann = {
            "id": str(uuid.uuid4()),
            "kind": "textbox",
            "page": int(page_index),
            "rect": marker_rect,
            "text": "NOTE_FINALE_BOX",
            "style": {"color": "#FFFFFF", "fontsize": 1.0, "fontname": "Helvetica", "padding_pt": 0.0},
            "payload": {"tag": "final_note_marker"},
        }
        anns.append(marker_ann)

        self.project.save()

        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_correction_totals()
        self._refresh_info_panel()


    def c_delete_final_note(self) -> None:
        if not self._require_doc():
            return
        anns = self._annotations_for_current_doc()
        removed = self._remove_final_note_annotations(anns)
        if not removed:
            messagebox.showinfo("Correction", "Aucune note finale à supprimer.")
            return
        assert self.project is not None
        self.project.save()
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_correction_totals()
        self._refresh_info_panel()

    def _refresh_info_panel(self) -> None:
        if not hasattr(self, "info_tree"):
            return

        for iid in self.info_tree.get_children(""):
            self.info_tree.delete(iid)

        if not self.project:
            self.info_doc_var.set("Document : —")
            self.info_total_var.set("— / —")
            return

        scheme = self._scheme()

        def total_good(node) -> float:
            if node.children:
                return sum(total_good(c) for c in node.children)
            if node.level() in (1, 2):
                if node.rubric:
                    return float(node.rubric.good)
                return 1.0
            return 0.0

        max_by_ex: dict[str, float] = {}
        label_by_ex: dict[str, str] = {}
        for ex in scheme.exercises:
            ex_code = ex.code
            max_by_ex[ex_code] = float(total_good(ex))
            label_by_ex[ex_code] = ex.label or f"Exercice {ex_code}"

        max_total = sum(max_by_ex.values())

        doc = self.project.get_current_doc()
        if not doc:
            self.info_doc_var.set("Document : — (aucun sélectionné)")
            for ex_code in sorted(max_by_ex.keys(), key=lambda s: int(s) if s.isdigit() else 9999):
                self.info_tree.insert("", "end", text=label_by_ex.get(ex_code, f"Exercice {ex_code}"),
                                      values=("", f"{max_by_ex[ex_code]:g}"))
            self.info_total_var.set(f"— / {max_total:g}")
            return

        self.info_doc_var.set(f"Document : {doc.original_name}")

        ann = self.project.settings.get("annotations", {})
        anns = ann.get(doc.id, []) if isinstance(ann, dict) else []

        attrib_by_ex: dict[str, float] = {k: 0.0 for k in max_by_ex.keys()}
        if isinstance(anns, list):
            for a in anns:
                if not isinstance(a, dict):
                    continue
                if a.get("kind") != "score_circle":
                    continue
                code = str(a.get("exercise_code", "")).strip()
                if not code:
                    continue
                ex_code = code.split(".", 1)[0]
                try:
                    pts = float(a.get("points", 0.0))
                except Exception:
                    pts = 0.0
                attrib_by_ex[ex_code] = attrib_by_ex.get(ex_code, 0.0) + pts

        # Points manuels: remplace le total des pastilles pour l'exercice principal
        manual_set: set[str] = set()
        if isinstance(anns, list):
            for a in anns:
                if not isinstance(a, dict):
                    continue
                if a.get("kind") != "manual_score":
                    continue
                code = str(a.get("exercise_code", "")).strip()
                if not code:
                    continue
                ex_code = code.split(".", 1)[0]
                if ex_code not in max_by_ex:
                    continue
                try:
                    pts = float(a.get("points", 0.0))
                except Exception:
                    pts = 0.0
                attrib_by_ex[ex_code] = float(pts)
                manual_set.add(ex_code)

        attrib_total = sum(attrib_by_ex.values())

        def sort_key_ex(s: str):
            try:
                return int(s)
            except Exception:
                return 9999

        for ex_code in sorted(max_by_ex.keys(), key=sort_key_ex):
            attrib = attrib_by_ex.get(ex_code, 0.0)
            mx = max_by_ex.get(ex_code, 0.0)
            base_label = label_by_ex.get(ex_code, f"Exercice {ex_code}")
            if 'manual_set' in locals() and ex_code in manual_set:
                base_label = f"{base_label} (manuel)"
            self.info_tree.insert("", "end", text=base_label,
                                  values=(f"{attrib:g}", f"{mx:g}"))

        self.info_total_var.set(f"{attrib_total:g} / {max_total:g}")

def run_app() -> None:
    root = tk.Tk()
    AppWindow(root)
    root.mainloop()
