from __future__ import annotations

"""UI + logique de placement d'images (PNG) comme annotations.

Ce module est conçu pour garder `app_window.py` léger :
- gestion de la bibliothèque (import/suppression)
- widgets (combobox + boutons)
- création d'un dict d'annotation de type "image"
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.services.pdf_margin import cm_to_pt
from app.ui.image_library import (
    list_library,
    list_categories,
    add_category,
    add_images_to_library,
    remove_image_from_library,
    export_library_to_zip,
    import_library_from_zip,
    DEFAULT_CATEGORY,
)


class ImageStampTool:
    """Contrôleur du nouvel outil "Image (PNG)".

    Il s'attache à une AppWindow (passée en `app`) et reste robuste si `app.project` est None.
    """

    def __init__(self, app: Any):
        self.app = app

        # catégorie (filtre + catégorie d'import)
        self.category_var = tk.StringVar(value="Tous")

        # variables Tk
        self.image_choice_var = tk.StringVar(value="")
        self.width_cm_var = tk.DoubleVar(value=2.0)
        self.keep_ratio_var = tk.BooleanVar(value=True)

        # widgets (set later)
        self._combo: Optional[ttk.Combobox] = None
        self._cat_combo: Optional[ttk.Combobox] = None
        self._btn_new_cat: Optional[ttk.Button] = None
        self._btn_export: Optional[ttk.Button] = None
        self._btn_import: Optional[ttk.Button] = None
        self._btn_add: Optional[ttk.Button] = None
        self._btn_del: Optional[ttk.Button] = None
        self._width_spin: Optional[ttk.Spinbox] = None
        self._keep_chk: Optional[ttk.Checkbutton] = None
        self._hint_lbl: Optional[ttk.Label] = None

        # mapping label -> entry
        self._label_to_entry: Dict[str, Dict[str, Any]] = {}

    # ---------------- UI ----------------
    def build_ui(self, parent: ttk.Frame) -> None:
        row_cat = ttk.Frame(parent)
        row_cat.pack(fill="x", padx=10, pady=(0, 6))

        ttk.Label(row_cat, text="Catégorie :").pack(side="left")
        self._cat_combo = ttk.Combobox(row_cat, state="readonly", width=22, textvariable=self.category_var)
        self._cat_combo.pack(side="left", padx=(6, 8))
        try:
            self._cat_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_options())
        except Exception:
            pass

        self._btn_new_cat = ttk.Button(row_cat, text="Nouvelle…", command=self._on_new_category)
        self._btn_new_cat.pack(side="left")

        self._btn_export = ttk.Button(row_cat, text="Exporter…", command=self._on_export_library)
        self._btn_export.pack(side="left", padx=(10, 4))

        self._btn_import = ttk.Button(row_cat, text="Importer…", command=self._on_import_library)
        self._btn_import.pack(side="left")

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Label(row, text="Image :").pack(side="left")
        self._combo = ttk.Combobox(row, state="readonly", width=24, textvariable=self.image_choice_var)
        self._combo.pack(side="left", padx=(6, 8))

        self._btn_add = ttk.Button(row, text="Ajouter…", command=self._on_add_images)
        self._btn_add.pack(side="left", padx=(0, 6))

        self._btn_del = ttk.Button(row, text="Supprimer", command=self._on_remove_selected)
        self._btn_del.pack(side="left", padx=(0, 12))

        ttk.Label(row, text="Largeur (cm) :").pack(side="left")
        self._width_spin = ttk.Spinbox(row, from_=0.5, to=30.0, increment=0.5, width=6, textvariable=self.width_cm_var)
        self._width_spin.pack(side="left", padx=(6, 10))

        self._keep_chk = ttk.Checkbutton(row, text="Conserver le ratio", variable=self.keep_ratio_var)
        self._keep_chk.pack(side="left")

        self._hint_lbl = ttk.Label(parent, text="Astuce : cliquer-déplacer pour définir la taille.")
        self._hint_lbl.pack(anchor="w", padx=10, pady=(0, 8))

        self.refresh_options()
        self.set_enabled(False)

    def set_enabled(self, enabled: bool) -> None:
        if self._cat_combo is not None:
            try:
                self._cat_combo.configure(state="readonly" if enabled else "disabled")
            except Exception:
                pass
        if self._btn_new_cat is not None:
            try:
                self._btn_new_cat.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        if self._btn_export is not None:
            try:
                self._btn_export.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        if self._btn_import is not None:
            try:
                self._btn_import.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        state = "readonly" if enabled else "disabled"
        state_btn = "normal" if enabled else "disabled"

        if self._combo is not None:
            try:
                self._combo.configure(state=state)
            except Exception:
                pass
        if self._btn_add is not None:
            try:
                self._btn_add.configure(state=state_btn)
            except Exception:
                pass
        if self._btn_del is not None:
            try:
                self._btn_del.configure(state=state_btn)
            except Exception:
                pass
        if self._width_spin is not None:
            try:
                self._width_spin.configure(state=state_btn)
            except Exception:
                pass
        if self._keep_chk is not None:
            try:
                self._keep_chk.configure(state=state_btn)
            except Exception:
                pass
        if self._hint_lbl is not None:
            try:
                self._hint_lbl.configure(state=state_btn)
            except Exception:
                pass

    def refresh_options(self) -> None:
        """Recharge la liste depuis le projet (si présent) et met à jour la combobox."""
        self._label_to_entry.clear()

        prj = getattr(self.app, "project", None)
        items: List[Dict[str, Any]] = []
        cats: List[str] = []
        if prj is not None:
            try:
                items = list_library(prj)
            except Exception:
                items = []
            try:
                cats = list_categories(prj)
            except Exception:
                cats = []

        # ---- catégories ----
        cat_values = ["Tous"] + list(cats or [])
        cur_cat = (self.category_var.get() or "Tous").strip() or "Tous"
        if cur_cat not in cat_values:
            cur_cat = "Tous"
            try:
                self.category_var.set(cur_cat)
            except Exception:
                pass

        if self._cat_combo is not None:
            try:
                self._cat_combo.configure(values=cat_values)
            except Exception:
                pass

        # filtre
        selected_cat = cur_cat
        if selected_cat != "Tous":
            items = [it for it in items if str(it.get("category") or "") == selected_cat]

        labels: List[str] = []
        show_cat_prefix = (selected_cat == "Tous")
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or Path(str(it.get("rel") or "")).name or "image")
            if show_cat_prefix:
                cat = str(it.get("category") or DEFAULT_CATEGORY)
                name = f"[{cat}] {name}"
            # évite collisions : ajoute un suffixe court si nécessaire
            base = name
            lbl = base
            k = 2
            while lbl in self._label_to_entry:
                lbl = f"{base} ({k})"
                k += 1
            self._label_to_entry[lbl] = it
            labels.append(lbl)

        if self._combo is not None:
            try:
                self._combo.configure(values=labels)
            except Exception:
                pass

        # garde la sélection si possible
        cur = self.image_choice_var.get().strip()
        if cur and cur in self._label_to_entry:
            return
        if labels:
            try:
                self.image_choice_var.set(labels[0])
            except Exception:
                pass
        else:
            try:
                self.image_choice_var.set("")
            except Exception:
                pass

    def _on_new_category(self) -> None:
        prj = getattr(self.app, "project", None)
        if prj is None:
            messagebox.showwarning("Images", "Veuillez d'abord créer / ouvrir un projet.")
            return

        parent = getattr(self.app, "root", None)
        try:
            if parent is not None:
                parent.update_idletasks()
                parent.lift()
                parent.focus_force()
        except Exception:
            pass

        name = simpledialog.askstring(
            "Nouvelle catégorie",
            "Nom de la catégorie (ex: Tampons SVT, Icônes, Schémas correction) :",
            parent=parent if isinstance(parent, tk.Misc) else None,
        )
        if not name:
            return

        try:
            cc = add_category(prj, name)
            prj.save()
            self.category_var.set(cc)
        except Exception as e:
            messagebox.showerror("Images", f"Impossible d'ajouter la catégorie.\n\n{e}")
            return

        self.refresh_options()

    def _on_export_library(self) -> None:
        prj = getattr(self.app, "project", None)
        if prj is None:
            messagebox.showwarning("Images", "Veuillez d'abord créer / ouvrir un projet.")
            return

        parent = getattr(self.app, "root", None)
        try:
            if parent is not None:
                parent.update_idletasks()
                parent.lift()
                parent.focus_force()
        except Exception:
            pass

        selected_cat = (self.category_var.get() or "Tous").strip() or "Tous"
        initialfile = "image_library.zip" if selected_cat == "Tous" else f"images_{selected_cat}.zip"

        kwargs = {
            "title": "Exporter la bibliothèque d'images",
            "defaultextension": ".zip",
            "filetypes": [("Archive ZIP", "*.zip")],
            "initialfile": initialfile,
        }
        try:
            if getattr(prj, "root_dir", None):
                kwargs["initialdir"] = str(prj.root_dir)
            if isinstance(parent, tk.Misc):
                kwargs["parent"] = parent
        except Exception:
            pass

        dest = filedialog.asksaveasfilename(**kwargs)
        if not dest:
            return

        ok, msg = export_library_to_zip(prj, dest, category=selected_cat)
        if ok:
            messagebox.showinfo("Images", msg)
        else:
            messagebox.showwarning("Images", msg)

    def _on_import_library(self) -> None:
        prj = getattr(self.app, "project", None)
        if prj is None:
            messagebox.showwarning("Images", "Veuillez d'abord créer / ouvrir un projet.")
            return

        parent = getattr(self.app, "root", None)
        try:
            if parent is not None:
                parent.update_idletasks()
                parent.lift()
                parent.focus_force()
        except Exception:
            pass

        kwargs = {
            "title": "Importer une bibliothèque d'images",
            "filetypes": [("Archive ZIP", "*.zip")],
        }
        try:
            if getattr(prj, "root_dir", None):
                kwargs["initialdir"] = str(prj.root_dir)
            if isinstance(parent, tk.Misc):
                kwargs["parent"] = parent
        except Exception:
            pass

        src = filedialog.askopenfilename(**kwargs)
        if not src:
            return

        ok, msg = import_library_from_zip(prj, src, mode="merge", category_override=None)
        if not ok:
            messagebox.showwarning("Images", msg)
            return

        try:
            prj.save()
        except Exception:
            pass

        self.refresh_options()
        messagebox.showinfo("Images", msg)

    # ---------------- Library actions ----------------
    def _on_add_images(self) -> None:
        prj = getattr(self.app, "project", None)
        if prj is None:
            messagebox.showwarning("Images", "Veuillez d'abord créer / ouvrir un projet.")
            return

        # IMPORTANT (Windows/macOS) : sans parent, la fenêtre de sélection peut s'ouvrir
        # derrière l'application et donner l'impression que "rien ne se passe".
        parent = getattr(self.app, "root", None)
        try:
            if parent is not None:
                parent.update_idletasks()
                parent.lift()
                parent.focus_force()
        except Exception:
            pass

        kwargs = {
            "title": "Ajouter des images PNG",
            "filetypes": [("Images PNG", "*.png")],
        }
        try:
            # initialdir rend l'expérience plus fluide
            if getattr(prj, "root_dir", None):
                kwargs["initialdir"] = str(prj.root_dir)
            # parent évite l'ouverture "derrière"
            if isinstance(parent, tk.Misc):
                kwargs["parent"] = parent
        except Exception:
            pass

        try:
            paths = filedialog.askopenfilenames(**kwargs)
        except Exception as e:
            messagebox.showerror(
                "Images",
                "Impossible d'ouvrir la fenêtre de sélection des fichiers.\n\n" + str(e),
            )
            return
        if not paths:
            return

        # la catégorie sélectionnée sert de catégorie d'import.
        # Si l'utilisateur est sur "Tous", on met dans "Général".
        selected_cat = (self.category_var.get() or "Tous").strip() or "Tous"
        cat = DEFAULT_CATEGORY if selected_cat == "Tous" else selected_cat
        created = add_images_to_library(prj, list(paths), category=cat)
        if not created:
            messagebox.showwarning("Images", "Aucune image PNG valide n'a été ajoutée.")
            return

        try:
            prj.save()
        except Exception:
            pass

        self.refresh_options()

    def _on_remove_selected(self) -> None:
        prj = getattr(self.app, "project", None)
        if prj is None:
            return
        entry = self.get_selected_entry()
        if not entry:
            return
        ok, msg = remove_image_from_library(prj, str(entry.get("id") or ""))
        if not ok:
            messagebox.showwarning("Images", msg)
            return
        try:
            prj.save()
        except Exception:
            pass
        self.refresh_options()

    # ---------------- Annotation build ----------------
    def get_selected_entry(self) -> Optional[Dict[str, Any]]:
        lbl = self.image_choice_var.get().strip()
        return self._label_to_entry.get(lbl)

    def build_annotation(
        self,
        page_index: int,
        start_pt: Tuple[float, float],
        end_pt: Tuple[float, float],
        click_pt: Tuple[float, float],
    ) -> Optional[Dict[str, Any]]:
        """Construit une annotation 'image'.

        - Si l'utilisateur a cliqué-déplacé => rect = start/end.
        - Sinon => rect centré sur le clic avec une largeur par défaut (cm), et hauteur via ratio.
        """
        entry = self.get_selected_entry()
        if not entry:
            return None

        prj = getattr(self.app, "project", None)
        if prj is None:
            return None

        x0, y0 = float(start_pt[0]), float(start_pt[1])
        x1, y1 = float(end_pt[0]), float(end_pt[1])

        # taille minimale : sinon on considère un simple clic
        if abs(x1 - x0) < 6.0 or abs(y1 - y0) < 6.0:
            cx, cy = float(click_pt[0]), float(click_pt[1])
            try:
                w_pt = float(cm_to_pt(float(self.width_cm_var.get() or 2.0)))
            except Exception:
                w_pt = float(cm_to_pt(2.0))
            w_px = int(entry.get("w_px") or 0)
            h_px = int(entry.get("h_px") or 0)
            if self.keep_ratio_var.get() and w_px > 0 and h_px > 0:
                h_pt = w_pt * (float(h_px) / float(w_px))
            else:
                h_pt = w_pt

            x0 = cx - w_pt / 2.0
            y0 = cy - h_pt / 2.0
            x1 = cx + w_pt / 2.0
            y1 = cy + h_pt / 2.0

        # normalisation
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0

        rel = str(entry.get("rel") or "")
        if not rel:
            return None

        return {
            "id": str(uuid.uuid4()),
            "kind": "image",
            "page": int(page_index),
            "rect": [float(x0), float(y0), float(x1), float(y1)],
            "image_rel": rel,
            "style": {
                "keep_proportion": bool(self.keep_ratio_var.get()),
            },
            "payload": {
                "image_id": str(entry.get("id") or ""),
                "name": str(entry.get("name") or ""),
            },
        }
