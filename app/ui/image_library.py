from __future__ import annotations

"""Bibliothèque d'images PNG stockées dans le projet.

Objectifs :
- Permettre à l'utilisateur d'"uploader" des PNG.
- Copier les fichiers dans un dossier du projet pour conserver la portabilité.
- Exposer une API simple pour lister / ajouter / supprimer des images.
- Gérer des catégories (tampons, icônes, schémas, ...).

Stockage :
- ``project.settings['image_library']`` : liste d'entrées
- ``project.settings['image_categories']`` : liste des catégories connues

Format d'une entrée (dict) :
    {
      'id': str,
      'name': str,              # libellé utilisateur (par défaut: nom du fichier)
      'rel': str,               # chemin relatif au dossier du projet
      'category': str,          # catégorie (ex: "tampons SVT")
      'w_px': int, 'h_px': int  # dimensions (pour calculer un ratio lors du placement)
    }

Compat :
- si un projet contient une ancienne forme (string relpath, ou dict sans category/id),
  on normalise automatiquement.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
import zipfile
import unicodedata

from PIL import Image

from app.core.project import Project


IMAGES_DIR_REL = "assets/images"
DEFAULT_CATEGORY = "Général"


# ---------------------------------------------------------------------------
# Bibliothèque globale (persistante entre projets)
# ---------------------------------------------------------------------------


GLOBAL_LIB_APP_NAME = "Pdf_correction"
LEGACY_LIB_APP_NAME = "FredC"
GLOBAL_LIB_SUBDIR = "ImageLibrary"
GLOBAL_LIB_SETTINGS = "library.json"


def _user_data_dir_for(app_name: str) -> Path:
    """Retourne un dossier de données utilisateur (cross-platform) pour `app_name`."""
    try:
        if sys.platform.startswith("win"):
            base = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA")
            if not base:
                base = str(Path.home() / "AppData" / "Roaming")
            return (Path(base) / app_name).resolve()
        if sys.platform == "darwin":
            return (Path.home() / "Library" / "Application Support" / app_name).resolve()
        base = os.getenv("XDG_DATA_HOME")
        if base:
            return (Path(base) / app_name).resolve()
        return (Path.home() / ".local" / "share" / app_name).resolve()
    except Exception:
        return (Path.home() / f".{app_name.lower()}").resolve()


def _user_data_dir() -> Path:
    """Retourne un dossier de données utilisateur (cross-platform)."""
    return _user_data_dir_for(GLOBAL_LIB_APP_NAME)


def _legacy_user_data_dir() -> Path:
    """Ancien dossier de données utilisateur (migration depuis FredC)."""
    return _user_data_dir_for(LEGACY_LIB_APP_NAME)


def _maybe_migrate_global_library(new_root: Path) -> None:
    """Migration douce : copie l'ancienne bibliothèque (FredC) vers Pdf_correction si besoin."""
    try:
        legacy_root = (_legacy_user_data_dir() / GLOBAL_LIB_SUBDIR).resolve()
        if not (legacy_root / GLOBAL_LIB_SETTINGS).exists():
            return
        # Si le nouveau n'a pas encore de settings, on copie/merge.
        if (new_root / GLOBAL_LIB_SETTINGS).exists():
            return
        new_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy_root, new_root, dirs_exist_ok=True)
    except Exception:
        pass

class _GlobalImageProject:
    """Pseudo-Project minimal pour réutiliser les helpers de bibliothèque d'images.

    On stocke un JSON dédié (library.json) et les PNG dans assets/images.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.settings: Dict[str, Any] = {}

    @property
    def settings_file(self) -> Path:
        return (self.root_dir / GLOBAL_LIB_SETTINGS).resolve()

    def save(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "image_categories": self.settings.get("image_categories", [DEFAULT_CATEGORY]),
            "image_library": self.settings.get("image_library", []),
        }
        with self.settings_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


_GLOBAL_CACHE: Optional[_GlobalImageProject] = None


def get_global_library() -> _GlobalImageProject:
    """Charge (ou crée) la bibliothèque globale d'images."""
    global _GLOBAL_CACHE
    if _GLOBAL_CACHE is not None:
        return _GLOBAL_CACHE

    root = (_user_data_dir() / GLOBAL_LIB_SUBDIR).resolve()
    # Migration douce depuis l'ancien dossier (FredC -> Pdf_correction)
    _maybe_migrate_global_library(root)
    gl = _GlobalImageProject(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "assets" / "images").mkdir(parents=True, exist_ok=True)

    if gl.settings_file.exists():
        try:
            with gl.settings_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                gl.settings["image_categories"] = data.get("image_categories", [DEFAULT_CATEGORY])
                gl.settings["image_library"] = data.get("image_library", [])
        except Exception:
            gl.settings["image_categories"] = [DEFAULT_CATEGORY]
            gl.settings["image_library"] = []
    else:
        gl.settings["image_categories"] = [DEFAULT_CATEGORY]
        gl.settings["image_library"] = []
        try:
            gl.save()
        except Exception:
            pass

    # normalise
    _ensure_categories_list(gl)  # type: ignore[arg-type]
    _ensure_library_list(gl)     # type: ignore[arg-type]

    # Réparation best-effort des chemins d'images (compat anciens nommages
    # utilisant "-" au lieu de "_" pour le suffixe, ou chemins contenant des quotes).
    try:
        _repair_missing_image_files(gl)  # type: ignore[arg-type]
        gl.save()
    except Exception:
        pass
    _GLOBAL_CACHE = gl
    return gl


def _sha_index(project_like: Any) -> Tuple[Dict[str, str], set[tuple[str, str, str]]]:
    """Indexe les fichiers présents en SHA256.

    Retourne:
      - sha_to_rel: sha256 -> rel
      - trio_set: (sha256, category, name)
    """
    sha_to_rel: Dict[str, str] = {}
    trio_set: set[tuple[str, str, str]] = set()
    lib = _ensure_library_list(project_like)  # type: ignore[arg-type]
    for it in lib:
        try:
            rel = str(it.get("rel") or "")
            if not rel:
                continue
            ap = resolve_image_abs(project_like, rel)  # type: ignore[arg-type]
            if not ap.exists() or not ap.is_file():
                continue
            sha = _sha256_file(ap)
            if sha:
                sha_to_rel.setdefault(sha, rel)
                cat = str(it.get("category") or DEFAULT_CATEGORY)
                name = str(it.get("name") or Path(rel).stem)
                trio_set.add((sha, cat, name))
        except Exception:
            continue
    return sha_to_rel, trio_set


def _trio_to_entry_map(project: Project) -> Dict[tuple[str, str, str], Dict[str, Any]]:
    """Retourne un mapping (sha, cat, name) -> entrée (dict) pour un projet."""
    out: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    lib = _ensure_library_list(project)
    for it in lib:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("rel") or "")
        if not rel:
            continue
        ap = resolve_image_abs(project, rel)
        if not ap.exists() or not ap.is_file():
            continue
        try:
            sha = _sha256_file(ap)
        except Exception:
            continue
        if not sha:
            continue
        cat = _normalize_category(str(it.get("category") or DEFAULT_CATEGORY))
        name = str(it.get("name") or ap.stem)
        out[(sha, cat, name)] = it
    return out


def sync_project_library_to_global(project: Project) -> None:
    """Fusionne la bibliothèque du projet dans la bibliothèque globale.

    - Copie les PNG manquants dans la bibliothèque globale (dédup SHA).
    - Ajoute les catégories manquantes.
    - Ajoute des entrées manquantes (sha, category, name).
    """
    gl = get_global_library()

    # categories
    for c in list_categories(project):
        add_category(gl, c)  # type: ignore[arg-type]

    g_sha_to_rel, g_trios = _sha_index(gl)
    dest_dir = ensure_images_dir(gl)  # type: ignore[arg-type]
    g_lib = _ensure_library_list(gl)  # type: ignore[arg-type]

    # parcourt les entrées du projet
    for it in list_library(project):
        if not isinstance(it, dict):
            continue
        rel = str(it.get("rel") or "")
        if not rel:
            continue
        src = resolve_image_abs(project, rel)
        if not src.exists() or not src.is_file():
            continue
        try:
            sha = _sha256_file(src)
        except Exception:
            sha = ""
        if not sha:
            continue

        cat = _normalize_category(str(it.get("category") or DEFAULT_CATEGORY))
        name = str(it.get("name") or src.stem)
        add_category(gl, cat)  # type: ignore[arg-type]

        # assure le fichier
        if sha in g_sha_to_rel:
            g_rel = g_sha_to_rel[sha]
        else:
            stem = src.stem.strip() or "image"
            safe_stem = "".join(ch for ch in stem if (ch.isalnum() or ch in ("-", "_", " "))).strip()
            safe_stem = safe_stem.replace(" ", "_") or "image"
            dest_name = f"{safe_stem}_{uuid.uuid4().hex[:6]}.png"
            dest = (dest_dir / dest_name).resolve()
            try:
                shutil.copy2(src, dest)
            except Exception:
                continue
            g_rel = Path(IMAGES_DIR_REL, dest_name).as_posix()
            g_sha_to_rel[sha] = g_rel

        trio = (sha, cat, name)
        if trio in g_trios:
            continue

        g_trios.add(trio)
        g_lib.append({
            "id": uuid.uuid4().hex,
            "name": name,
            "rel": g_rel,
            "category": cat,
            "w_px": int(it.get("w_px") or 0),
            "h_px": int(it.get("h_px") or 0),
        })

    gl.settings["image_library"] = g_lib
    _ensure_categories_list(gl)  # type: ignore[arg-type]
    _ensure_library_list(gl)     # type: ignore[arg-type]
    try:
        gl.save()
    except Exception:
        pass


def sync_global_library_to_project(project: Project) -> None:
    """Fusionne la bibliothèque globale vers le projet (pour éviter de réimporter).

    - Copie les PNG manquants dans le projet (dédup SHA).
    - Ajoute catégories/entrées manquantes.
    """
    gl = get_global_library()

    # categories
    for c in list_categories(gl):  # type: ignore[arg-type]
        add_category(project, c)

    p_sha_to_rel, p_trios = _sha_index(project)
    trio_to_entry = _trio_to_entry_map(project)
    dest_dir = ensure_images_dir(project)
    p_lib = _ensure_library_list(project)

    for it in list_library(gl):  # type: ignore[arg-type]
        if not isinstance(it, dict):
            continue
        rel = str(it.get("rel") or "")
        if not rel:
            continue
        src = resolve_image_abs(gl, rel)  # type: ignore[arg-type]
        if not src.exists() or not src.is_file():
            continue
        try:
            sha = _sha256_file(src)
        except Exception:
            sha = ""
        if not sha:
            continue

        cat = _normalize_category(str(it.get("category") or DEFAULT_CATEGORY))
        name = str(it.get("name") or src.stem)
        add_category(project, cat)

        # assure le fichier
        if sha in p_sha_to_rel:
            p_rel = p_sha_to_rel[sha]
        else:
            stem = src.stem.strip() or "image"
            safe_stem = "".join(ch for ch in stem if (ch.isalnum() or ch in ("-", "_", " "))).strip()
            safe_stem = safe_stem.replace(" ", "_") or "image"
            dest_name = f"{safe_stem}_{uuid.uuid4().hex[:6]}.png"
            dest = (dest_dir / dest_name).resolve()
            try:
                shutil.copy2(src, dest)
            except Exception:
                continue
            p_rel = Path(IMAGES_DIR_REL, dest_name).as_posix()
            p_sha_to_rel[sha] = p_rel

        trio = (sha, cat, name)

        # Si une entrée existe déjà (même contenu + même catégorie + même nom),
        # on la complète (global_id) et on s'assure que le fichier est présent.
        existing_entry = trio_to_entry.get(trio)
        if isinstance(existing_entry, dict):
            try:
                if not str(existing_entry.get("global_id") or "").strip():
                    existing_entry["global_id"] = str(it.get("id") or "")
            except Exception:
                pass
            # Si le fichier n'existe pas encore dans le projet, on le copie.
            if sha not in p_sha_to_rel:
                stem = src.stem.strip() or "image"
                safe_stem = "".join(ch for ch in stem if (ch.isalnum() or ch in ("-", "_", " "))).strip()
                safe_stem = safe_stem.replace(" ", "_") or "image"
                dest_name = f"{safe_stem}_{uuid.uuid4().hex[:6]}.png"
                dest = (dest_dir / dest_name).resolve()
                try:
                    shutil.copy2(src, dest)
                    p_rel = Path(IMAGES_DIR_REL, dest_name).as_posix()
                    existing_entry["rel"] = p_rel
                    p_sha_to_rel[sha] = p_rel
                except Exception:
                    pass
            continue

        p_trios.add(trio)
        new_entry = {
            "id": uuid.uuid4().hex,
            "name": name,
            "rel": p_rel,
            "category": cat,
            "global_id": str(it.get("id") or ""),
            "w_px": int(it.get("w_px") or 0),
            "h_px": int(it.get("h_px") or 0),
        }
        p_lib.append(new_entry)
        trio_to_entry[trio] = new_entry

    project.settings["image_library"] = p_lib
    _ensure_categories_list(project)
    _ensure_library_list(project)
    try:
        project.save()
    except Exception:
        pass


def ensure_images_dir(project: Project) -> Path:
    p = (project.root_dir / IMAGES_DIR_REL).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _normalize_category(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return DEFAULT_CATEGORY
    # évite des catégories "vides" ou trop longues
    s = " ".join(s.split())
    return s[:48]


def _ensure_categories_list(project: Project) -> List[str]:
    cats = project.settings.setdefault("image_categories", [DEFAULT_CATEGORY])
    if not isinstance(cats, list):
        cats = [DEFAULT_CATEGORY]
        project.settings["image_categories"] = cats

    out: List[str] = []
    for c in cats:
        if isinstance(c, str) and c.strip():
            cc = _normalize_category(c)
            if cc not in out:
                out.append(cc)
    if DEFAULT_CATEGORY not in out:
        out.insert(0, DEFAULT_CATEGORY)
    project.settings["image_categories"] = out
    return out


def list_categories(project: Project) -> List[str]:
    return list(_ensure_categories_list(project))


def add_category(project: Project, name: str) -> str:
    cats = _ensure_categories_list(project)
    cc = _normalize_category(name)
    if cc not in cats:
        cats.append(cc)
        project.settings["image_categories"] = cats
    return cc


def _normalize_entry(project: Project, item: Any) -> Optional[Dict[str, Any]]:
    """Normalise une entrée (compat anciens formats)."""
    if isinstance(item, str):
        rel = item.strip().replace("\\", "/")
        if not rel:
            return None
        name = Path(rel).stem or "image"
        return {
            "id": uuid.uuid4().hex,
            "name": name,
            "rel": rel,
            "category": DEFAULT_CATEGORY,
            "w_px": 0,
            "h_px": 0,
        }

    if not isinstance(item, dict):
        return None

    rel = str(item.get("rel") or "").strip().replace("\\", "/")
    if not rel:
        return None

    entry = dict(item)
    if not str(entry.get("id") or "").strip():
        entry["id"] = uuid.uuid4().hex

    if not str(entry.get("name") or "").strip():
        entry["name"] = Path(rel).stem or "image"

    entry["rel"] = rel

    cat = _normalize_category(str(entry.get("category") or DEFAULT_CATEGORY))
    entry["category"] = cat

    try:
        entry["w_px"] = int(entry.get("w_px") or 0)
    except Exception:
        entry["w_px"] = 0
    try:
        entry["h_px"] = int(entry.get("h_px") or 0)
    except Exception:
        entry["h_px"] = 0

    # s'assure que la catégorie existe côté liste
    add_category(project, cat)

    return entry


def _ensure_library_list(project: Project) -> List[Dict[str, Any]]:
    lib = project.settings.setdefault("image_library", [])
    if not isinstance(lib, list):
        lib = []
        project.settings["image_library"] = lib

    _ensure_categories_list(project)

    out: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in lib:
        entry = _normalize_entry(project, item)
        if not entry:
            continue
        eid = str(entry.get("id") or "")
        if eid in seen_ids:
            # collision improbable, mais on évite les doublons
            entry["id"] = uuid.uuid4().hex
            eid = entry["id"]
        seen_ids.add(eid)
        out.append(entry)

    project.settings["image_library"] = out
    return out


def list_library(project: Project) -> List[Dict[str, Any]]:
    return list(_ensure_library_list(project))


def resolve_image_abs(project: Project, rel_or_path: str) -> Path:
    """Résout une image à partir d'un chemin relatif (préféré) ou d'un chemin absolu."""
    s = str(rel_or_path or "").strip()
    if not s:
        return Path("")
    p = Path(s)
    if p.is_absolute():
        return p

    base = (project.root_dir / s).resolve()
    if base.exists():
        return base

    # Fallback: de nombreuses bibliothèques stockent les PNG dans assets/images
    # mais certaines anciennes entrées ne contiennent que le nom de fichier.
    try:
        img_dir = (project.root_dir / IMAGES_DIR_REL).resolve()
        alt = (img_dir / p.name).resolve()
        if alt.exists() and alt.is_file():
            return alt
    except Exception:
        img_dir = None

    # Compat : certains anciens projets/bibliothèques ont pu utiliser un séparateur "-"
    # au lieu de "_" avant le suffixe aléatoire, ou contenir des quotes.
    # Autre cas rencontré : le suffixe aléatoire peut différer entre l'entrée et le
    # fichier réellement présent (ex: copie/merge/migration, renommage, etc.).
    # On tente alors une résolution "best-effort" :
    #   1) variantes simples (quotes, -/_)
    #   2) recherche par préfixe (base sans suffixe)
    #   3) recherche "fuzzy" (normalisation forte) dans le même dossier.
    try:
        rel = s.replace("\\", "/")
        rel_p = Path(rel)
        parent = rel_p.parent.as_posix() if str(rel_p.parent) not in (".", "") else ""
        name = rel_p.name

        # Nettoyage simple
        cleaned = name.replace("'", "").replace('"', "")

        candidates = []
        if cleaned and cleaned != name:
            candidates.append(cleaned)

        import re

        # Swap ciblé sur le dernier séparateur avant un suffixe hex de 6 chars
        m = re.match(r"^(.*?)([-_])([0-9a-fA-F]{6})(\.png)$", cleaned)
        if m:
            alt_sep = "_" if m.group(2) == "-" else "-"
            candidates.append(f"{m.group(1)}{alt_sep}{m.group(3)}{m.group(4)}")

        # Swap global (fallback)
        candidates.append(cleaned.replace("-", "_"))
        candidates.append(cleaned.replace("_", "-"))

        seen = set()
        for cand in candidates:
            cand = str(cand or "").strip()
            if not cand or cand in seen:
                continue
            seen.add(cand)
            if parent:
                alt = (project.root_dir / parent / cand).resolve()
            else:
                alt = (project.root_dir / cand).resolve()
            if alt.exists() and alt.is_file():
                return alt

        # Dernier recours : recherche par préfixe dans le même dossier.
        # Exemple : entrée => nom_image-ABC123.png mais fichier => nom_image_DEF456.png
        try:
            # parent vide => on préfère assets/images s'il existe
            if parent:
                parent_dir = (project.root_dir / parent).resolve()
            else:
                try:
                    parent_dir = (project.root_dir / IMAGES_DIR_REL).resolve()
                except Exception:
                    parent_dir = (project.root_dir / "").resolve()
            if parent_dir.exists() and parent_dir.is_dir():
                stem = Path(cleaned).stem
                # 1) Construction de préfixes candidates :
                #    - enlève un suffixe hex classique (6)
                #    - enlève un suffixe alphanum générique (4..16)
                #    - garde le stem brut
                bases: list[str] = []
                bases.append(stem)

                # split générique au dernier séparateur (utile si le suffixe n'est pas strictement hex)
                try:
                    for sep in ("_", "-"):
                        if sep in stem:
                            left, right = stem.rsplit(sep, 1)
                            if left and 4 <= len(right) <= 32 and right.isalnum():
                                bases.append(left)
                except Exception:
                    pass

                # suffixe hex classique (nos versions récentes)
                mm_hex = re.match(r"^(.*?)([-_])([0-9a-fA-F]{4,})$", stem)
                if mm_hex:
                    bases.append(mm_hex.group(1))

                # suffixe alphanum (anciennes versions / variantes)
                mm_alnum = re.match(r"^(.*?)([-_])([0-9A-Za-z]{4,16})$", stem)
                if mm_alnum:
                    bases.append(mm_alnum.group(1))

                def _fold(txt: str) -> str:
                    try:
                        return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")
                    except Exception:
                        return txt

                # collecte candidats .png dont le nom commence par un de ces préfixes
                cands: list[Path] = []
                seen_fp: set[str] = set()
                for bp in bases:
                    bp = (bp or "").strip()
                    if not bp:
                        continue
                    for bp2 in {bp, bp.replace(" ", "_"), bp.replace("_", "-"), bp.replace("-", "_"), _fold(bp)}:
                        bp2 = (bp2 or "").strip()
                        if not bp2:
                            continue
                        try:
                            for fp in parent_dir.glob(f"{bp2}*.png"):
                                try:
                                    if fp.exists() and fp.is_file():
                                        key = str(fp.resolve())
                                        if key not in seen_fp:
                                            seen_fp.add(key)
                                            cands.append(fp)
                                except Exception:
                                    continue
                        except Exception:
                            continue

                if len(cands) == 1:
                    return cands[0].resolve()

                if cands:
                    def _norm_key(txt: str) -> str:
                        t = (txt or "")
                        t = t.replace("'", "").replace('"', "")
                        t = unicodedata.normalize("NFKD", t)
                        t = re.sub(r"([-_])[0-9A-Za-z]{4,16}$", "", t)
                        t = re.sub(r"[^0-9A-Za-z]+", "", t)
                        return t.lower()

                    target_keys = {_norm_key(stem), _norm_key(_fold(stem))}

                    best_fp: Path | None = None
                    best_score = -1
                    for fp in cands:
                        try:
                            st = fp.stem
                            k1 = _norm_key(st)
                            k2 = _norm_key(_fold(st))
                            score = 0
                            if k1 in target_keys or k2 in target_keys:
                                score += 100
                            for tk in target_keys:
                                if tk and (k1.startswith(tk) or tk.startswith(k1)):
                                    score += 15
                                if tk and (k2.startswith(tk) or tk.startswith(k2)):
                                    score += 15
                            if re.match(r".*[-_][0-9a-fA-F]{6}$", st):
                                score += 5
                            try:
                                score += int(fp.stat().st_mtime) // 100000
                            except Exception:
                                pass
                        except Exception:
                            continue
                        if score > best_score:
                            best_score = score
                            best_fp = fp

                    if best_fp is not None and best_score >= 15:
                        return best_fp.resolve()
        except Exception:
            pass
    except Exception:
        pass

    return base




def resolve_entry_abs(project_like: object, entry: dict) -> Path:
    """Résout un fichier image à partir d'une entrée de bibliothèque.

    Objectif : être robuste même si entry['rel'] est cassé (suffixe différent, renommage, etc.).

    Stratégie :
      1) resolve_image_abs(rel)
      2) chercher par id (suffixe ou nom exact) dans assets/images
      3) chercher par nom logique (entry['name']) / basename
      4) si trouvé : met à jour entry['rel'] (relatif à root_dir)

    NOTE: cette fonction peut MUTER l'entrée (repair best-effort).
    """
    try:
        rel = str(entry.get('rel') or '').strip()
    except Exception:
        rel = ''

    # 1) rel direct
    if rel:
        p = resolve_image_abs(project_like, rel)  # type: ignore[arg-type]
        if p.exists() and p.is_file():
            return p

    # dossier images
    try:
        root = Path(getattr(project_like, 'root_dir', '.')).resolve()
        img_dir = (root / IMAGES_DIR_REL).resolve()
    except Exception:
        img_dir = None
        root = Path('.')

    # 2) recherche par id
    try:
        eid = str(entry.get('id') or '').strip()
    except Exception:
        eid = ''
    if img_dir and eid:
        cand = (img_dir / f"{eid}.png")
        if cand.exists() and cand.is_file():
            try:
                entry['rel'] = cand.resolve().relative_to(root).as_posix()
            except Exception:
                entry['rel'] = Path(IMAGES_DIR_REL, cand.name).as_posix()
            return cand.resolve()
        short = eid[:8]
        if short:
            try:
                for fp in img_dir.glob(f"*{short}*.png"):
                    if fp.exists() and fp.is_file():
                        try:
                            entry['rel'] = fp.resolve().relative_to(root).as_posix()
                        except Exception:
                            entry['rel'] = Path(IMAGES_DIR_REL, fp.name).as_posix()
                        return fp.resolve()
            except Exception:
                pass

    # 3) recherche par préfixe safe_stem (nom utilisateur)
    if img_dir:
        try:
            nm0 = str(entry.get('name') or '')
        except Exception:
            nm0 = ''
        if nm0:
            try:
                stem0 = Path(nm0).stem.strip() or nm0.strip()
            except Exception:
                stem0 = nm0.strip()
            safe = "".join(ch for ch in stem0 if (ch.isalnum() or ch in ('-', '_', ' '))).strip()
            safe = safe.replace(' ', '_') or ''
            if safe:
                for pat in (f"{safe}__*.png", f"{safe}_*.png", f"{safe}-*.png", f"{safe}*.png"):
                    try:
                        for fp in img_dir.glob(pat):
                            if fp.exists() and fp.is_file():
                                try:
                                    entry['rel'] = fp.resolve().relative_to(root).as_posix()
                                except Exception:
                                    entry['rel'] = Path(IMAGES_DIR_REL, fp.name).as_posix()
                                return fp.resolve()
                    except Exception:
                        continue

    # 4) best-effort par nom logique
    base = ''
    try:
        base = str(entry.get('name') or '')
    except Exception:
        base = ''
    if (not base) and rel:
        base = Path(rel).name

    ap2 = _best_effort_find_png_by_basename(project_like, base)
    if ap2 is not None and ap2.exists() and ap2.is_file():
        try:
            entry['rel'] = ap2.resolve().relative_to(root).as_posix()
        except Exception:
            entry['rel'] = Path(IMAGES_DIR_REL, ap2.name).as_posix()
        return ap2.resolve()

    if rel:
        return resolve_image_abs(project_like, rel)  # type: ignore[arg-type]
    return Path("")


def _best_effort_find_png_by_basename(project_like: object, base_name: str) -> "Path | None":
    """Recherche un PNG dans assets/images en se basant sur un nom (préfixe/nom logique).

    Sert à réparer les entrées dont 'rel' pointe vers un fichier renommé (suffixe aléatoire différent).
    """
    try:
        root = Path(getattr(project_like, 'root_dir', '.')).resolve()
        img_dir = (root / IMAGES_DIR_REL).resolve()
        if not img_dir.exists() or not img_dir.is_dir():
            return None
    except Exception:
        return None

    import re

    def _fold(s: str) -> str:
        try:
            import unicodedata as _ud
        except Exception:
            _ud = None
        try:
            if _ud is not None:
                return _ud.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
        except Exception:
            pass
        try:
            import unicodedata as _ud2
            return _ud2.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
        except Exception:
            return s

    def _norm_base(stem: str) -> str:
        s = (stem or '').strip().replace("'", '').replace('"', '')
        s = _fold(s)
        s = re.sub(r"([-_])[0-9A-Za-z]{4,32}$", "", s)
        s = re.sub(r"[^0-9A-Za-z]+", "", s)
        return s.lower()

    target = _norm_base(Path(base_name).stem)
    if not target:
        return None

    best = None
    best_score = -1

    for fp in img_dir.glob('*.png'):
        try:
            st = fp.stem
            k = _norm_base(st)
            if not k:
                continue
            score = 0
            if k == target:
                score += 100
            elif k.startswith(target) or target.startswith(k):
                score += 40
            elif target in k:
                score += 15
            if re.match(r".*[-_][0-9a-fA-F]{6}$", st):
                score += 2
            try:
                score += int(fp.stat().st_mtime) // 100000
            except Exception:
                pass
        except Exception:
            continue

        if score > best_score:
            best_score = score
            best = fp

    if best is not None and best_score >= 15:
        return best.resolve()
    return None
def _repair_missing_image_files(project_like: Any) -> None:
    """Répare (best-effort) les entrées de bibliothèque dont le fichier rel est cassé.

    On ne fait que :
      - tester resolve_image_abs() (qui inclut déjà un fallback -/_)
      - si le fallback trouve un fichier existant, on met à jour l'entrée['rel']
        pour qu'elle pointe vers le bon fichier dans assets/images.

    Cela évite les "image introuvable" dans la fenêtre Bibliothèque.
    """
    try:
        lib = _ensure_library_list(project_like)  # type: ignore[arg-type]
    except Exception:
        return

    changed = False
    for it in lib:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("rel") or "").strip().replace("\\", "/")
        if not rel:
            continue

        # Chemin direct
        direct = (Path(getattr(project_like, "root_dir", ".")) / rel).resolve()
        if direct.exists() and direct.is_file():
            continue

        # Résolution best-effort (inclut recherche par ID et nom logique)
        ap = resolve_entry_abs(project_like, it)
        if (not ap.exists()) or (not ap.is_file()):
            ap = resolve_image_abs(project_like, rel)  # type: ignore[arg-type]
        if (not ap.exists() or not ap.is_file()):
            # Fallback: parfois seul le début du nom est correct (suffixe différent)
            try:
                nm = str(it.get('name') or '')
            except Exception:
                nm = ''
            ap2 = _best_effort_find_png_by_basename(project_like, nm or Path(rel).name)
            if ap2 is not None and ap2.exists() and ap2.is_file():
                ap = ap2

        if ap.exists() and ap.is_file():
            # On normalise le rel vers le fichier réellement présent
            try:
                root = Path(getattr(project_like, "root_dir", ".")).resolve()
                new_rel = ap.resolve().relative_to(root).as_posix()
            except Exception:
                new_rel = Path(IMAGES_DIR_REL, ap.name).as_posix()

            try:
                if new_rel != rel:
                    it["rel"] = new_rel
                    changed = True
            except Exception:
                pass

    if changed:
        try:
            project_like.settings["image_library"] = lib
        except Exception:
            pass


def add_images_to_library(
    project: Project,
    src_paths: List[str | Path],
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Copie des PNG dans le projet et ajoute des entrées dans la bibliothèque.

    IMPORTANT : le nom de fichier final est dérivé de l'ID de l'entrée.
    Cela évite toute incohérence (suffixe différent) même si l'entrée est
    reconstruite / migrée.

    Args:
        category: catégorie à associer aux images ajoutées (si None -> "Général").

    Retourne la liste des entrées créées.
    """
    if not src_paths:
        return []

    dest_dir = ensure_images_dir(project)
    lib = _ensure_library_list(project)

    cat = add_category(project, _normalize_category(category or DEFAULT_CATEGORY))

    created: List[Dict[str, Any]] = []
    for sp in src_paths:
        src = Path(sp).expanduser().resolve()
        if not src.exists() or not src.is_file():
            continue

        if src.suffix.lower() != ".png":
            continue

        try:
            with Image.open(src) as im:
                w_px, h_px = im.size
        except Exception:
            w_px, h_px = 0, 0

        stem = src.stem.strip() or "image"
        safe_stem = "".join(ch for ch in stem if (ch.isalnum() or ch in ("-", "_", " "))).strip()
        safe_stem = safe_stem.replace(" ", "_") or "image"

        entry_id = uuid.uuid4().hex
        dest_name = f"{safe_stem}__{entry_id[:8]}.png"
        dest = (dest_dir / dest_name).resolve()

        try:
            shutil.copy2(src, dest)
        except Exception:
            continue

        entry = {
            "id": entry_id,
            "name": stem,
            "rel": Path(IMAGES_DIR_REL, dest.name).as_posix(),
            "category": cat,
            "w_px": int(w_px or 0),
            "h_px": int(h_px or 0),
        }
        lib.append(entry)
        created.append(entry)

    project.settings["image_library"] = lib
    return created


def _is_rel_used_in_annotations(project: Project, image_rel: str) -> bool:
    """Renvoie True si une image est utilisée par au moins une annotation."""
    ann = project.settings.get("annotations", {})
    if not isinstance(ann, dict):
        return False
    rel = str(image_rel or "").replace("\\", "/")
    for _docid, items in ann.items():
        if not isinstance(items, list):
            continue
        for a in items:
            if isinstance(a, dict) and a.get("kind") == "image":
                if str(a.get("image_rel") or "").replace("\\", "/") == rel:
                    return True
    return False


def _count_rel_in_library(project: Project, image_rel: str) -> int:
    """Compte combien d'entrées de bibliothèque pointent vers un même fichier rel."""
    lib = _ensure_library_list(project)
    rel = str(image_rel or "").replace("\\", "/")
    if not rel:
        return 0
    n = 0
    for it in lib:
        if isinstance(it, dict) and str(it.get("rel") or "").replace("\\", "/") == rel:
            n += 1
    return n


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def remove_image_from_library(project: Project, image_id: str) -> Tuple[bool, str]:
    """Supprime une entrée de bibliothèque.

    - Si l'image est utilisée par des annotations, on refuse (évite de casser l'export).
    - Sinon, on supprime l'entrée + le fichier (si présent).

    Retourne (ok, message).
    """
    lib = _ensure_library_list(project)
    iid = str(image_id or "")
    if not iid:
        return False, "Image invalide."

    idx = None
    entry = None
    for i, it in enumerate(lib):
        if isinstance(it, dict) and str(it.get("id", "")) == iid:
            idx = i
            entry = it
            break

    if idx is None or not isinstance(entry, dict):
        return False, "Image introuvable."

    rel = str(entry.get("rel") or "")
    if rel and _is_rel_used_in_annotations(project, rel):
        return False, "Impossible : cette image est utilisée par une ou plusieurs annotations."

    # retire l'entrée
    try:
        lib.pop(idx)
    except Exception:
        pass
    project.settings["image_library"] = lib

    # supprime le fichier (best-effort) uniquement si plus aucune autre entrée
    # ne pointe dessus (sinon on casserait d'autres catégories/entrées).
    try:
        if rel and _count_rel_in_library(project, rel) <= 0:
            p = resolve_image_abs(project, rel)
            if p.exists() and p.is_file():
                p.unlink()
    except Exception:
        pass

    return True, "Image supprimée."


# ---------------------------------------------------------------------------
# Export / Import de bibliothèque (ZIP)
# ---------------------------------------------------------------------------


EXPORT_MANIFEST_NAME = "image_library.json"


def export_library_to_zip(
    project: Project,
    dest_zip: str | Path,
    category: Optional[str] = None,
) -> Tuple[bool, str]:
    """Exporte les catégories + images associées dans un fichier ZIP.

    - Si category est None ou "Tous" => export complet
    - Sinon => n'exporte que cette catégorie
    """
    dest = Path(dest_zip).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    lib = _ensure_library_list(project)
    cats = _ensure_categories_list(project)

    cat = (category or "").strip()
    if not cat or cat.lower() == "tous":
        selected = lib
        export_cats = cats
    else:
        selected = [it for it in lib if str(it.get("category") or "") == cat]
        export_cats = [cat]

    if not selected:
        return False, "Aucune image à exporter pour cette catégorie."

    manifest = {
        "version": 1,
        "categories": export_cats,
        "images": [],
    }

    # On stocke les fichiers sous images/<filename>
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for it in selected:
            rel = str(it.get("rel") or "")
            if not rel:
                continue
            abs_path = resolve_image_abs(project, rel)
            if not abs_path.exists() or not abs_path.is_file():
                # on conserve l'entrée mais marque "missing"
                rec = {
                    "name": str(it.get("name") or ""),
                    "category": str(it.get("category") or DEFAULT_CATEGORY),
                    "rel": rel,
                    "filename": Path(rel).name,
                    "sha256": "",
                    "w_px": int(it.get("w_px") or 0),
                    "h_px": int(it.get("h_px") or 0),
                    "missing": True,
                }
                manifest["images"].append(rec)
                continue

            sha = ""
            try:
                sha = _sha256_file(abs_path)
            except Exception:
                sha = ""

            arcname = Path("images") / abs_path.name
            zf.write(abs_path, arcname.as_posix())

            rec = {
                "name": str(it.get("name") or abs_path.stem),
                "category": str(it.get("category") or DEFAULT_CATEGORY),
                "filename": abs_path.name,
                "sha256": sha,
                "w_px": int(it.get("w_px") or 0),
                "h_px": int(it.get("h_px") or 0),
            }
            manifest["images"].append(rec)

        zf.writestr(EXPORT_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    return True, "Bibliothèque exportée."


def import_library_from_zip(
    project: Project,
    src_zip: str | Path,
    mode: str = "merge",
    category_override: Optional[str] = None,
) -> Tuple[bool, str]:
    """Importe une bibliothèque d'images depuis un ZIP.

    Args:
        mode:
          - "merge" (défaut) : ajoute/merge categories + images
          - "replace" : remplace la bibliothèque/catégories par celles du ZIP (⚠ ne supprime pas les fichiers existants)
        category_override:
          - si fourni, force toutes les images importées dans cette catégorie.

    Notes:
        - Déduplication : si un fichier importé a le même sha256 qu'un fichier existant,
          on réutilise le même fichier (rel) mais on crée une nouvelle entrée de bibliothèque
          si la catégorie diffère.
    """
    src = Path(src_zip).expanduser().resolve()
    if not src.exists() or not src.is_file():
        return False, "Fichier ZIP introuvable."

    dest_dir = ensure_images_dir(project)

    with zipfile.ZipFile(src, "r") as zf:
        if EXPORT_MANIFEST_NAME not in zf.namelist():
            return False, f"ZIP invalide : {EXPORT_MANIFEST_NAME} manquant."
        try:
            manifest = json.loads(zf.read(EXPORT_MANIFEST_NAME).decode("utf-8"))
        except Exception:
            return False, "ZIP invalide : manifeste illisible."

        if not isinstance(manifest, dict) or not isinstance(manifest.get("images"), list):
            return False, "ZIP invalide : format du manifeste incorrect."

        # mode replace : on remplace settings, mais on ne supprime pas physiquement les fichiers
        if mode == "replace":
            project.settings["image_library"] = []
            project.settings["image_categories"] = [DEFAULT_CATEGORY]

        # catégories
        cats_in = manifest.get("categories")
        if isinstance(cats_in, list):
            for c in cats_in:
                if isinstance(c, str) and c.strip():
                    add_category(project, c)

        # index existant par sha256 et par rel
        existing = _ensure_library_list(project)
        sha_to_rel: Dict[str, str] = {}
        for it in existing:
            rel = str(it.get("rel") or "")
            if not rel:
                continue
            ap = resolve_image_abs(project, rel)
            if ap.exists() and ap.is_file():
                try:
                    sha = _sha256_file(ap)
                    if sha:
                        sha_to_rel.setdefault(sha, rel)
                except Exception:
                    pass

        created = 0
        skipped_missing = 0

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for rec in manifest.get("images", []):
                if not isinstance(rec, dict):
                    continue
                if rec.get("missing") is True:
                    skipped_missing += 1
                    continue

                filename = str(rec.get("filename") or "").strip()
                if not filename:
                    continue
                zpath = Path("images") / filename
                if zpath.as_posix() not in zf.namelist():
                    skipped_missing += 1
                    continue

                # catégorie finale
                cat = _normalize_category(category_override or str(rec.get("category") or DEFAULT_CATEGORY))
                add_category(project, cat)

                # extrait en tmp
                out = tmp / filename
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(zpath.as_posix(), "r") as src_f, out.open("wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)

                # calc sha
                sha = ""
                try:
                    sha = _sha256_file(out)
                except Exception:
                    sha = str(rec.get("sha256") or "")

                # si on a déjà ce contenu, on réutilise le même fichier
                if sha and sha in sha_to_rel:
                    rel = sha_to_rel[sha]
                else:
                    # copie vers projet avec nom unique
                    stem = Path(filename).stem
                    safe_stem = "".join(ch for ch in stem if (ch.isalnum() or ch in ("-", "_", " "))).strip()
                    safe_stem = safe_stem.replace(" ", "_") or "image"
                    dest_name = f"{safe_stem}_{uuid.uuid4().hex[:6]}.png"
                    dest_path = (dest_dir / dest_name).resolve()
                    shutil.copy2(out, dest_path)
                    rel = Path(IMAGES_DIR_REL, dest_name).as_posix()
                    if sha:
                        sha_to_rel[sha] = rel

                entry = {
                    "id": uuid.uuid4().hex,
                    "name": str(rec.get("name") or Path(filename).stem or "image"),
                    "rel": rel,
                    "category": cat,
                    "w_px": int(rec.get("w_px") or 0),
                    "h_px": int(rec.get("h_px") or 0),
                }
                existing.append(entry)
                created += 1

        project.settings["image_library"] = existing

    msg = f"Import terminé : {created} image(s) ajoutée(s)."
    if skipped_missing:
        msg += f" ({skipped_missing} image(s) manquante(s) ignorée(s))"
    return True, msg
