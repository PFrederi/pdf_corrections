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
import shutil
import tempfile
import uuid
import zipfile

from PIL import Image

from app.core.project import Project


IMAGES_DIR_REL = "assets/images"
DEFAULT_CATEGORY = "Général"


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
    return (project.root_dir / s).resolve()


def add_images_to_library(
    project: Project,
    src_paths: List[str | Path],
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Copie des PNG dans le projet et ajoute des entrées dans la bibliothèque.

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

        # On se limite au PNG pour éviter les surprises en export PDF.
        if src.suffix.lower() != ".png":
            continue

        # Lecture dimensions (ratio)
        try:
            with Image.open(src) as im:
                w_px, h_px = im.size
        except Exception:
            w_px, h_px = 0, 0

        stem = src.stem.strip() or "image"
        safe_stem = "".join(ch for ch in stem if (ch.isalnum() or ch in ("-", "_", " "))).strip()
        safe_stem = safe_stem.replace(" ", "_") or "image"

        dest_name = f"{safe_stem}_{uuid.uuid4().hex[:6]}.png"
        dest = (dest_dir / dest_name).resolve()
        shutil.copy2(src, dest)

        entry = {
            "id": uuid.uuid4().hex,
            "name": stem,
            "rel": Path(IMAGES_DIR_REL, dest_name).as_posix(),
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
