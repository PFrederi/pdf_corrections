from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class MultiLineTextDialog:
    """Petit dialogue modal pour saisir du texte multi-lignes (Tkinter pur)."""

    @staticmethod
    def ask(parent, title: str = "Texte", prompt: str = "Saisis le texte :", initial: str = "") -> str | None:
        root = parent.winfo_toplevel() if parent is not None else None

        win = tk.Toplevel(root)
        win.title(title)
        win.transient(root)
        win.grab_set()

        # évite la fenêtre derrière (Windows/mac)
        try:
            win.lift()
            win.attributes("-topmost", True)
            win.after(60, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=prompt).pack(anchor="w")

        box = ttk.Frame(frm)
        box.pack(fill="both", expand=True, pady=(8, 10))

        txt = tk.Text(box, height=8, wrap="word")
        txt.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.configure(yscrollcommand=sb.set)

        if initial:
            try:
                txt.insert("1.0", initial)
            except Exception:
                pass

        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        result = {"value": None}

        def _ok():
            try:
                v = txt.get("1.0", "end-1c")
            except Exception:
                v = ""
            v = v.rstrip("\n")
            result["value"] = v
            try:
                win.destroy()
            except Exception:
                pass

        def _cancel():
            result["value"] = None
            try:
                win.destroy()
            except Exception:
                pass

        ttk.Button(btns, text="Annuler", command=_cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=_ok).pack(side="right", padx=(0, 8))

        win.bind("<Escape>", lambda _e: _cancel())
        win.bind("<Control-Return>", lambda _e: _ok())

        # focus
        try:
            txt.focus_set()
        except Exception:
            pass

        # centre grossièrement
        try:
            win.update_idletasks()
            w = win.winfo_width()
            h = win.winfo_height()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            x = int((sw - w) / 2)
            y = int((sh - h) / 3)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

        win.wait_window()
        return result["value"]
