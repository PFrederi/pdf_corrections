import tkinter as tk
from tkinter import ttk

DARK_BG = "#0B1E3A"      # bleu foncé
DARK_BG_2 = "#0E2A52"    # bleu foncé légèrement plus clair
FG = "#FFFFFF"           # blanc
ACCENT = "#2F81F7"       # bleu accent

def apply_dark_theme(root: tk.Tk) -> None:
    root.configure(bg=DARK_BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=DARK_BG, foreground=FG, fieldbackground=DARK_BG_2)
    style.configure("TFrame", background=DARK_BG)
    style.configure("TLabel", background=DARK_BG, foreground=FG)
    style.configure("TButton", background=DARK_BG_2, foreground=FG, padding=8)
    style.map("TButton",
              background=[("active", ACCENT), ("pressed", ACCENT)],
              foreground=[("active", FG), ("pressed", FG)])

    style.configure("TEntry", fieldbackground=DARK_BG_2, foreground=FG, insertcolor=FG)
    style.configure("TCombobox", fieldbackground=DARK_BG_2, foreground=FG)

    style.configure("TNotebook", background=DARK_BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=DARK_BG_2, foreground=FG, padding=(12, 8))
    style.map("TNotebook.Tab",
              background=[("selected", DARK_BG), ("active", ACCENT)],
              foreground=[("selected", FG), ("active", FG)])

    # Treeview (Notation) : fond bleu + texte blanc
    style.configure(
    "Treeview",
    background=DARK_BG_2,
    fieldbackground=DARK_BG_2,
    foreground=FG,
    rowheight=24,
    borderwidth=0
    )
    style.map(
    "Treeview",
    background=[("selected", ACCENT)],
    foreground=[("selected", FG)]
    )
    style.configure("Treeview.Heading", background=DARK_BG, foreground=FG, relief="flat")
    style.map("Treeview.Heading", background=[("active", DARK_BG)], foreground=[("active", FG)])



    style.configure("Vertical.TScrollbar", background=DARK_BG_2, troughcolor=DARK_BG)
