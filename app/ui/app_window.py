from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
import fitz  # PyMuPDF
import uuid
import math
import copy
import sys

from app.ui.theme import apply_dark_theme, DARK_BG, DARK_BG_2
from app.core.project import Project
from app.services.pdf_margin import add_left_margin
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

from app.core.grading import (
    ensure_scheme_dict, scheme_from_dict, scheme_to_dict,
    regenerate_exercises, add_exercise, add_sublevel, add_subsublevel,
    delete_node, delete_exercise, set_label, set_rubric, find_node,
    leaf_nodes, points_for
)


APP_VERSION = "0.6.5"


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

        # Outils d'annotation classiques (Visualisation PDF)
        self.ann_tool_var = tk.StringVar(value="none")   # none | ink | textbox | arrow | image
        self.ann_color_var = tk.StringVar(value="bleu")  # couleur trait / flèche
        self.ann_width_var = tk.IntVar(value=3)          # épaisseur trait / flèche

        self.text_color_var = tk.StringVar(value="bleu") # couleur police
        self.text_size_var = tk.IntVar(value=14)         # taille police
        self.text_value_var = tk.StringVar(value="")     # texte à placer (optionnel)


        # Sélection d'annotations (outil 'Sélection')
        self._selected_ann_ids: set[str] = set()
        self._sel_info_var = tk.StringVar(value="Sélection : 0")
        self.sel_mode_var = tk.BooleanVar(value=True)
        self.sel_mode_var.trace_add("write", lambda *_: self._update_click_mode())


        # Etat runtime (drag)
        self._draw_kind: str | None = None
        self._draw_page: int | None = None
        self._draw_points: list[tuple[float, float]] = []
        self._draw_start: tuple[float, float] | None = None
        self._draw_end: tuple[float, float] | None = None

        # Déplacement d'annotations (outil "Déplacer")
        self._move_active: bool = False
        self._move_ann_id: str | None = None
        self._move_anchor: tuple[float, float] | None = None
        self._move_snapshot: dict | None = None
        self._move_has_moved: bool = False

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
        self.ann_tool_var.trace_add("write", lambda *_: (self._sync_tool_combo_from_var(), self._update_annot_toolbar_state(), self._update_click_mode()))
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

        ttk.Label(frm, text="Importer des copies (PDF) : marge 5 cm à gauche appliquée automatiquement.").pack(anchor="w")
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
    def _build_tab_view(self) -> None:
        container = ttk.Frame(self.tab_view)
        container.pack(fill="both", expand=True)

        self.view_pane = ttk.Panedwindow(container, orient="horizontal")
        self.view_pane.pack(fill="both", expand=True)

        # Gauche : sous-onglets (Correction / Infos)
        self.view_left = ttk.Frame(self.view_pane, width=380)
        self.view_pane.add(self.view_left, weight=0)

        self.view_subtabs = ttk.Notebook(self.view_left)
        self.view_subtabs.pack(fill="both", expand=True, padx=(0, 8))
        self.view_subtabs.bind("<<NotebookTabChanged>>", self._update_click_mode)

        self.sub_correction = ttk.Frame(self.view_subtabs)
        self.sub_info = ttk.Frame(self.view_subtabs)
        self.view_subtabs.add(self.sub_correction, text="Correction V0")
        self.view_subtabs.add(self.sub_info, text="Infos")        # Droite : PDF (+ barre d'outils)
        self.viewer_right = ttk.Frame(self.view_pane)
        self.view_pane.add(self.viewer_right, weight=1)

        self._build_pdf_toolbar(self.viewer_right)

        self.viewer = PDFViewer(self.viewer_right, bg=DARK_BG)
        self.viewer.pack(fill="both", expand=True)

        self._build_view_correction_panel()
        self._build_view_info_panel()

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
        self.c_align_margin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Aligner dans la marge", variable=self.c_align_margin_var).pack(anchor="w", pady=(0, 10))
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

    def _update_click_mode(self, _evt=None) -> None:
        main = self.nb.tab(self.nb.select(), "text")
        sub = ""
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""

        tool = self.ann_tool_var.get() if hasattr(self, "ann_tool_var") else "none"
        sel_on = False
        try:
            sel_on = bool(self.sel_mode_var.get())
        except Exception:
            sel_on = False

        enabled = (main == "Visualisation PDF") and ((sub == "Correction V0") or (tool != "none") or sel_on)

        self.viewer.set_interaction_callbacks(
            click_cb=self._on_pdf_click if enabled else None,
            drag_cb=self._on_pdf_drag if enabled else None,
            release_cb=self._on_pdf_release if enabled else None,
            context_cb=self._on_pdf_context_menu if enabled else None,
        )

        if hasattr(self, "_click_hint"):
            label = "OFF"
            if enabled:
                if sub == "Correction V0":
                    label = "ON • pastilles"
                else:
                    sel = False
                    try:
                        sel = bool(self.sel_mode_var.get())
                    except Exception:
                        sel = False
                    if tool != "none" and sel:
                        label = f"ON • outil: {tool} + sélection"
                    elif tool != "none":
                        label = f"ON • outil: {tool}"
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

        # 2) Options texte
        self._opts_text = ttk.Frame(self._pdf_opts_inner)
        row_tx = ttk.Frame(self._opts_text)
        row_tx.pack(fill="x", padx=10, pady=(0, 6))

        ttk.Label(row_tx, text="Texte :").pack(side="left")
        self._ann_text_entry = ttk.Entry(row_tx, textvariable=self.text_value_var, width=24)
        self._ann_text_entry.pack(side="left", padx=(6, 12))

        ttk.Label(row_tx, text="Couleur :").pack(side="left")
        self._text_color_combo = ttk.Combobox(
            row_tx,
            width=10,
            state="readonly",
            values=["rouge", "bleu", "vert", "violet", "marron", "noir"],
            textvariable=self.text_color_var,
        )
        self._text_color_combo.pack(side="left", padx=(6, 8))

        ttk.Label(row_tx, text="Taille :").pack(side="left")
        self._text_size_spin = ttk.Spinbox(row_tx, from_=8, to=72, width=5, textvariable=self.text_size_var)
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
        self._last_interaction_page = int(page_index)
        # Priorité : si le mode sélection est activé et qu'on clique sur une annotation,
        # on sélectionne puis on prépare un déplacement (glisser-déposer).
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
                # clic dans le vide : on désélectionne, mais on laisse les outils d'annotation fonctionner
                if self._selected_ann_ids:
                    self._selected_ann_ids.clear()
                    self._update_selection_info()

        # 1) outils d'annotation ?
        tool = self.ann_tool_var.get()
        if tool != "none":
            if not self._require_doc():
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

            if kind == "score_circle":
                # Option "Aligner dans la marge" (Correction V0) : verrouille X à 0,5 cm du bord gauche
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
        tool = self.ann_tool_var.get()
        if tool != "none":
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

            if self._draw_kind in ("arrow", "textbox", "image"):
                self._draw_end = (float(x_pt), float(y_pt))
                return

            return

        # 3) pastilles: déplacement éventuel
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""
        if sub == "Correction V0":
            self._on_pdf_drag_for_correction(page_index, x_pt, y_pt)

    def _on_pdf_release(self, page_index: int, x_pt: float, y_pt: float) -> None:
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
                    self.c_regenerate()
                except Exception:
                    pass
            self._reset_draw_state()
            return

        tool = self.ann_tool_var.get()
        if tool != "none":
            if not self._require_doc():
                self._reset_draw_state()
                return

            if self._draw_page is None or int(page_index) != int(self._draw_page):
                self._reset_draw_state()
                return

            assert self.project is not None
            doc = self.project.get_current_doc()
            assert doc is not None

            anns = self._annotations_for_current_doc()

            if tool == "ink":
                if len(self._draw_points) >= 2:
                    ann = {
                        "id": str(uuid.uuid4()),
                        "kind": "ink",
                        "page": int(page_index),
                        "points": [[p[0], p[1]] for p in self._draw_points],
                        "style": {
                            "color": self._color_hex(self.ann_color_var.get(), "bleu"),
                            "width_pt": float(self.ann_width_var.get()),
                        },
                        "payload": {},
                    }
                    anns.append(ann)
                    self.project.save()
                    self.c_regenerate()
                self._reset_draw_state()
                return

            if tool == "arrow":
                s = self._draw_start
                e = self._draw_end or (float(x_pt), float(y_pt))
                if s and e:
                    ann = {
                        "id": str(uuid.uuid4()),
                        "kind": "arrow",
                        "page": int(page_index),
                        "start": [float(s[0]), float(s[1])],
                        "end": [float(e[0]), float(e[1])],
                        "style": {
                            "color": self._color_hex(self.ann_color_var.get(), "bleu"),
                            "width_pt": float(self.ann_width_var.get()),
                        },
                        "payload": {},
                    }
                    anns.append(ann)
                    self.project.save()
                    self.c_regenerate()
                self._reset_draw_state()
                return

            if tool == "image":
                s = self._draw_start
                e = self._draw_end or (float(x_pt), float(y_pt))
                if not s or not e:
                    self._reset_draw_state()
                    return

                # construit une annotation image via le module (gestion bibliothèque / ratio)
                try:
                    ann = self.image_tool.build_annotation(
                        int(page_index),
                        (float(s[0]), float(s[1])),
                        (float(e[0]), float(e[1])),
                        (float(x_pt), float(y_pt)),
                    )
                except Exception:
                    ann = None

                if not ann:
                    messagebox.showwarning("Image", "Aucune image sélectionnée (ou bibliothèque vide).")
                    self._reset_draw_state()
                    return

                anns.append(ann)
                self.project.save()
                self.c_regenerate()
                self._reset_draw_state()
                return

            if tool == "textbox":
                s = self._draw_start
                e = self._draw_end or (float(x_pt), float(y_pt))
                if not s or not e:
                    self._reset_draw_state()
                    return

                x0, y0 = s
                x1, y1 = e
                # rect normalisé + taille minimale
                if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
                    x1 = x0 + 220
                    y1 = y0 + 40
                rect = [float(x0), float(y0), float(x1), float(y1)]

                text = self.text_value_var.get().strip()
                if not text:
                    text = simpledialog.askstring("Texte", "Contenu de la zone de texte :", parent=self.root) or ""
                    text = text.strip()
                if not text:
                    self._reset_draw_state()
                    return

                ann = {
                    "id": str(uuid.uuid4()),
                    "kind": "textbox",
                    "page": int(page_index),
                    "rect": rect,
                    "text": text,
                    "style": {
                        "color": self._color_hex(self.text_color_var.get(), "bleu"),
                        "fontsize": float(self.text_size_var.get()),
                    },
                    "payload": {},
                }
                anns.append(ann)
                self.project.save()
                self.c_regenerate()
                self._reset_draw_state()
                return

            self._reset_draw_state()
            return

        # pastilles: fin déplacement
        try:
            sub = self.view_subtabs.tab(self.view_subtabs.select(), "text")
        except Exception:
            sub = ""
        if sub == "Correction V0":
            self._on_pdf_release_for_correction(page_index, x_pt, y_pt)

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
        left_margin_cm = float(self.project.settings.get("left_margin_cm", 5.0))
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
            out_work = self.project.unique_work_path(f"{doc.id}__marge_5cm.pdf")
            try:
                add_left_margin(src, out_work, margin_cm=left_margin_cm)
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
        self.viewer.open_pdf(view_abs)
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
        self.viewer.open_pdf(p)
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
        self.project.save()

        # recharge la bibliothèque d'images (outil Image)
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
        self.project.save()

        # recharge la bibliothèque d'images (outil Image)
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
        """Affiche/masque la ligne guide verticale à 0,5 cm (si 'Aligner dans la marge' est coché)."""
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

        # X en pixels : 0,5 cm depuis le bord gauche (points -> pixels via zoom)
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

        paths = filedialog.askopenfilenames(title="Sélectionner des PDF", filetypes=[("PDF", "*.pdf")])
        if not paths:
            return

        left_margin_cm = float(self.project.settings.get("left_margin_cm", 5.0))
        last_doc_id: str | None = None

        for p in paths:
            src = Path(p)
            try:
                doc = self.project.import_pdf_copy(src)
                last_doc_id = doc.id

                input_abs = self.project.rel_to_abs(doc.input_rel) if doc.input_rel else None
                if not input_abs or not input_abs.exists():
                    raise FileNotFoundError("Fichier input introuvable après copie.")

                out_work = self.project.unique_work_path(f"{doc.id}__marge_5cm.pdf")
                add_left_margin(input_abs, out_work, margin_cm=left_margin_cm)
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
        self.c_marks.delete(0, tk.END)
        if not self.project:
            return
        doc = self.project.get_current_doc()
        if not doc:
            return
        anns = self._annotations_for_current_doc()
        for a in anns:
            if a.get("kind") != "score_circle":
                continue
            code = a.get("exercise_code", "?")
            label = a.get("exercise_label") or ""
            res = a.get("result", "?")
            pts = float(a.get("points", 0.0))
            page = int(a.get("page", 0))
            if label:
                self.c_marks.insert(tk.END, f"p{page+1} • {code} • {label} • {res} • {pts:g}")
            else:
                self.c_marks.insert(tk.END, f"p{page+1} • {code} • {res} • {pts:g}")


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
        if not self.project:
            return 0.0
        doc = self.project.get_current_doc()
        if not doc:
            return 0.0
        ann = self.project.settings.get("annotations", {})
        anns = ann.get(doc.id, []) if isinstance(ann, dict) else []
        total = 0.0
        if isinstance(anns, list):
            for a in anns:
                if isinstance(a, dict) and a.get("kind") == "score_circle":
                    try:
                        total += float(a.get("points", 0.0))
                    except Exception:
                        pass
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

    def _corr_margin_x_pt(self) -> float:
        """X (en points PDF) pour aligner une pastille à 0,5 cm du bord gauche."""
        return float((0.5 / 2.54) * 72.0)


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
                self.viewer.open_pdf(out_pdf, preserve_view=True)
                try:
                    self._update_margin_guide()
                except Exception:
                    pass
            except TypeError:
                # Compat avec d'anciennes versions de PDFViewer.open_pdf(pdf_path)
                self.viewer.open_pdf(out_pdf)
                try:
                    self._update_margin_guide()
                except Exception:
                    pass
            except Exception:
                # dernier recours : retenter un peu plus tard (écriture fichier / cache OS)
                try:
                    self.root.after(80, lambda: self.viewer.open_pdf(out_pdf))
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
        if not self._require_doc():
            return
        sel = self.c_marks.curselection()
        if not sel:
            return
        idx = sel[0]
        anns = self._annotations_for_current_doc()
        score_idxs = [i for i, a in enumerate(anns) if a.get("kind") == "score_circle"]
        if idx < 0 or idx >= len(score_idxs):
            return
        del anns[score_idxs[idx]]
        assert self.project is not None
        self.project.save()
        self.c_regenerate()
        self._refresh_marks_list()
        self._refresh_files_list()
        self._refresh_info_panel()

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
                    if isinstance(payload, dict) and payload.get("tag") == "final_note":
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
            lines.append(f"Ex {ex_code} : {at:g}/{mx:g}")

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

        # Rectangle dans la marge gauche
        left_margin_cm = float(self.project.settings.get("left_margin_cm", 5.0))
        margin_pt = (left_margin_cm / 2.54) * 72.0

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
            # fallback raisonnable
            w, h = 595.0, 842.0  # A4 portrait approx.

        x0 = 10.0
        x1 = min(w - 10.0, max(120.0, margin_pt - 10.0))
        y0 = 20.0
        line_count = max(1, len(text.splitlines()))
        est_h = 13.0 * line_count + 30.0
        y1 = min(h - 20.0, y0 + max(140.0, est_h))

        rect = [float(x0), float(y0), float(x1), float(y1)]

        anns = self._annotations_for_current_doc()
        self._remove_final_note_annotations(anns)

        ann = {
            "id": str(uuid.uuid4()),
            "kind": "textbox",
            "page": int(page_index),
            "rect": rect,
            "text": text,
            "style": {"color": "rouge", "fontsize": 11.0, "fontname": "Helvetica", "border_color": "rouge", "border_width_pt": 1.3, "padding_pt": 5.0, "bold_total": True},
            "payload": {"tag": "final_note"},
        }
        anns.append(ann)
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

        attrib_total = sum(attrib_by_ex.values())

        def sort_key_ex(s: str):
            try:
                return int(s)
            except Exception:
                return 9999

        for ex_code in sorted(max_by_ex.keys(), key=sort_key_ex):
            attrib = attrib_by_ex.get(ex_code, 0.0)
            mx = max_by_ex.get(ex_code, 0.0)
            self.info_tree.insert("", "end", text=label_by_ex.get(ex_code, f"Exercice {ex_code}"),
                                  values=(f"{attrib:g}", f"{mx:g}"))

        self.info_total_var.set(f"{attrib_total:g} / {max_total:g}")

def run_app() -> None:
    root = tk.Tk()
    AppWindow(root)
    root.mainloop()
