from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# -------------------------
# Helpers
# -------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slugify(name: str) -> str:
    s = (name or "").strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "projet"


def _unique_dir(parent: Path, slug: str) -> Path:
    parent = Path(parent).expanduser().resolve()
    base = parent / slug
    if not base.exists():
        return base
    for i in range(2, 9999):
        cand = parent / f"{slug}-{i}"
        if not cand.exists():
            return cand
    # fallback
    return parent / f"{slug}-{uuid.uuid4().hex[:6]}"


def _unique_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    if not p.exists():
        return p
    stem = p.stem
    suf = p.suffix
    for i in range(2, 9999):
        cand = folder / f"{stem}_{i}{suf}"
        if not cand.exists():
            return cand
    return folder / f"{stem}_{uuid.uuid4().hex[:6]}{suf}"


# -------------------------
# Data models
# -------------------------

@dataclass
class Document:
    id: str
    original_name: str
    input_rel: str
    variants: Dict[str, str] = field(default_factory=dict)  # e.g. {"margin": "work/..pdf", "corrected": "work/..pdf"}
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "input_rel": self.input_rel,
            "variants": dict(self.variants or {}),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Document":
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            original_name=str(d.get("original_name") or "document.pdf"),
            input_rel=str(d.get("input_rel") or ""),
            variants=dict(d.get("variants") or {}),
            created_at=str(d.get("created_at") or _now()),
        )


@dataclass
class ExportRecord:
    id: str
    source_doc_id: str
    source_rel: str
    export_rel: str
    locked: bool = True
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_doc_id": self.source_doc_id,
            "source_rel": self.source_rel,
            "export_rel": self.export_rel,
            "locked": bool(self.locked),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExportRecord":
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            source_doc_id=str(d.get("source_doc_id") or ""),
            source_rel=str(d.get("source_rel") or ""),
            export_rel=str(d.get("export_rel") or ""),
            locked=bool(d.get("locked", True)),
            created_at=str(d.get("created_at") or _now()),
        )


@dataclass
class Project:
    version: int = 2
    name: str = "Projet"
    root_dir: Path = field(default_factory=lambda: Path.cwd())
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    current_doc_id: str = ""
    current_variant: str = ""  # e.g. "corrected" or ""

    documents: List[Document] = field(default_factory=list)
    exports: List[ExportRecord] = field(default_factory=list)

    settings: Dict[str, Any] = field(default_factory=dict)

    # ---- folders ----
    @property
    def inputs_dir(self) -> Path:
        return (self.root_dir / "inputs").resolve()

    @property
    def work_dir(self) -> Path:
        return (self.root_dir / "work").resolve()

    @property
    def exports_dir(self) -> Path:
        return (self.root_dir / "exports").resolve()

    @property
    def project_file(self) -> Path:
        return (self.root_dir / "project.json").resolve()

    # ---- lifecycle ----
    def _ensure_defaults(self) -> None:
        self.settings.setdefault("left_margin_cm", 5.0)
        self.settings.setdefault("owner_password", "owner")
        self.settings.setdefault("annotations", {})
        # grading_scheme is handled in app_window via ensure_scheme_dict

    @classmethod
    def create(cls, parent_dir: Path, name: str) -> "Project":
        parent_dir = Path(parent_dir).expanduser().resolve()
        slug = _slugify(name)
        root = _unique_dir(parent_dir, slug)
        root.mkdir(parents=True, exist_ok=True)

        prj = cls(name=name or "Projet", root_dir=root)
        prj._ensure_defaults()

        prj.inputs_dir.mkdir(parents=True, exist_ok=True)
        prj.work_dir.mkdir(parents=True, exist_ok=True)
        prj.exports_dir.mkdir(parents=True, exist_ok=True)

        prj.save()
        return prj

    @classmethod
    def load(cls, path: Path) -> "Project":
        """
        Charge un projet v2 à partir de :
        - un dossier contenant project.json
        - un chemin direct vers project.json
        """
        path = Path(path).expanduser().resolve()
        if path.is_dir():
            path = path / "project.json"

        if not path.exists():
            raise FileNotFoundError("project.json introuvable.")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("Fichier projet invalide (JSON attendu).")

        # compat : si version absente, on suppose v2
        version = int(data.get("version", 2) or 2)
        if version != 2:
            raise ValueError("Projet invalide (version attendue = 2).")

        prj = cls.from_dict(data)
        prj.root_dir = path.parent.resolve()
        prj._ensure_defaults()

        # Autocorrection : si documents vides, on tente de reconstruire
        if not prj.documents:
            prj.rebuild_documents_from_inputs()
            prj.add_history("rebuild_documents_from_inputs_on_load")
            prj.save()

        # current_doc_id valide
        if prj.documents and (not prj.current_doc_id or all(d.id != prj.current_doc_id for d in prj.documents)):
            prj.current_doc_id = prj.documents[0].id

        return prj

    @classmethod
    def load_any(cls, path: Path) -> "Project":
        """
        Ouvre un projet à partir d'un chemin qui peut être :
        - le fichier project.json
        - un dossier de projet (contenant project.json)
        - un autre fichier .json (on cherchera project.json dans le même dossier)
        """
        path = Path(path).expanduser().resolve()

        if path.is_dir():
            candidate = path / "project.json"
            if candidate.exists():
                return cls.load(candidate)
            raise FileNotFoundError(f"Aucun project.json dans le dossier : {path}")

        if path.name.lower() == "project.json":
            return cls.load(path)

        if path.suffix.lower() == ".json":
            # 1) tentative directe
            try:
                return cls.load(path)
            except Exception:
                # 2) tentative dans le dossier parent
                candidate = path.parent / "project.json"
                if candidate.exists():
                    return cls.load(candidate)

        raise FileNotFoundError(f"Chemin de projet invalide : {path}")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Project":
        prj = cls(
            version=int(d.get("version", 2) or 2),
            name=str(d.get("name") or "Projet"),
            root_dir=Path(d.get("root_dir") or Path.cwd()),
            created_at=str(d.get("created_at") or _now()),
            updated_at=str(d.get("updated_at") or _now()),
            current_doc_id=str(d.get("current_doc_id") or ""),
            current_variant=str(d.get("current_variant") or ""),
            documents=[Document.from_dict(x) for x in (d.get("documents") or [])],
            exports=[ExportRecord.from_dict(x) for x in (d.get("exports") or [])],
            settings=dict(d.get("settings") or {}),
        )
        prj._ensure_defaults()
        return prj

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": int(self.version),
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_doc_id": self.current_doc_id,
            "current_variant": self.current_variant,
            "documents": [d.to_dict() for d in self.documents],
            "exports": [e.to_dict() for e in self.exports],
            "settings": self.settings,
        }

    def save(self) -> None:
        self._ensure_defaults()
        self.updated_at = _now()
        self.project_file.parent.mkdir(parents=True, exist_ok=True)
        with self.project_file.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    # ---- history ----
    def add_history(self, action: str, **kwargs: Any) -> None:
        hist = self.settings.setdefault("history", [])
        if not isinstance(hist, list):
            hist = []
            self.settings["history"] = hist
        hist.append({"t": _now(), "action": str(action), "meta": dict(kwargs or {})})

    # ---- path helpers ----
    def rel_to_abs(self, rel: str) -> Path:
        rel = str(rel).replace("\\", "/").lstrip("/")
        return (self.root_dir / rel).resolve()

    def abs_to_rel(self, p: Path) -> str:
        p = Path(p).resolve()
        try:
            rel = p.relative_to(self.root_dir.resolve())
        except Exception:
            # fallback: return name only
            rel = Path(p.name)
        return rel.as_posix()

    def unique_work_path(self, filename: str) -> Path:
        return _unique_path(self.work_dir, filename)

    def unique_export_path(self, filename: str) -> Path:
        return _unique_path(self.exports_dir, filename)

    # ---- docs ----
    def import_pdf_copy(self, src_pdf: Path) -> Document:
        src_pdf = Path(src_pdf).expanduser().resolve()
        if not src_pdf.exists():
            raise FileNotFoundError(str(src_pdf))

        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(self.inputs_dir, src_pdf.name)
        shutil.copy2(src_pdf, dest)

        doc = Document(
            id=uuid.uuid4().hex,
            original_name=src_pdf.name,
            input_rel=self.abs_to_rel(dest),
            variants={},
        )
        self.documents.append(doc)
        if not self.current_doc_id:
            self.current_doc_id = doc.id
        self.add_history("import_pdf_copy", file=src_pdf.name)
        return doc

    def rebuild_documents_from_inputs(self) -> bool:
        """Reconstruit la liste documents à partir des fichiers présents dans inputs/."""
        inp = self.inputs_dir
        if not inp.exists():
            return False

        pdfs = sorted([p for p in inp.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"])
        if not pdfs:
            return False

        existing_by_input = {d.input_rel: d for d in self.documents if d.input_rel}
        for p in pdfs:
            rel = self.abs_to_rel(p)
            if rel in existing_by_input:
                continue
            self.documents.append(Document(
                id=uuid.uuid4().hex,
                original_name=p.name,
                input_rel=rel,
                variants={},
            ))
        return True

    def get_doc(self, doc_id: str) -> Document:
        for d in self.documents:
            if d.id == doc_id:
                return d
        raise KeyError(f"Document introuvable: {doc_id}")

    def get_current_doc(self) -> Optional[Document]:
        if not self.current_doc_id:
            return None
        for d in self.documents:
            if d.id == self.current_doc_id:
                return d
        return None

    def set_variant(self, doc_id: str, variant: str, rel_path: str) -> None:
        doc = self.get_doc(doc_id)
        doc.variants[str(variant)] = str(rel_path)

    def get_best_view_abs(self, doc: Document) -> Optional[Path]:
        # Variant choisie
        if self.current_variant and self.current_variant in (doc.variants or {}):
            p = self.rel_to_abs(doc.variants[self.current_variant])
            if p.exists():
                return p
        # Prefer margin then input
        for key in ("corrected", "margin"):
            if key in (doc.variants or {}):
                p = self.rel_to_abs(doc.variants[key])
                if p.exists():
                    return p
        if doc.input_rel:
            p = self.rel_to_abs(doc.input_rel)
            if p.exists():
                return p
        return None

    # ---- exports ----
    def add_export(self, source_doc_id: str, source_rel: str, export_rel: str, locked: bool = True) -> ExportRecord:
        rec = ExportRecord(
            id=uuid.uuid4().hex,
            source_doc_id=source_doc_id,
            source_rel=source_rel,
            export_rel=export_rel,
            locked=locked,
        )
        self.exports.append(rec)
        self.add_history("add_export", source_doc_id=source_doc_id, export_rel=export_rel, locked=locked)
        return rec
