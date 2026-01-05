from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter

try:
    # pypdf >= 3 provides this enum
    from pypdf.constants import UserAccessPermissions as UAP
except Exception:  # pragma: no cover
    UAP = None  # type: ignore


def export_locked(input_pdf: Path, output_pdf: Path, owner_password: str = "owner") -> None:
    """
    Exporte un PDF "verrouillé" :
    - Mot de passe propriétaire (owner) uniquement
    - Autorise l'impression
    - Interdit la modification / l'extraction (copie)
    - Autorise l'ajout de notes (annotations) via le flag "ADD_OR_MODIFY" quand disponible
    """
    reader = PdfReader(str(input_pdf))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    # Compat multi-versions pypdf :
    # - anciennes versions : encrypt(..., permissions={...})
    # - versions récentes (ex: 6.x) : encrypt(..., permissions_flag=<int/flag>)
    try:
        writer.encrypt(
            user_password="",
            owner_password=owner_password,
            permissions={
                "print": True,
                "modify": False,
                "copy": False,
                "annotate": True,
            },
        )
    except TypeError:
        # pypdf récent : permissions_flag
        permissions_flag = 0
        if UAP is not None:
            permissions_flag = int(UAP.PRINT) | int(UAP.PRINT_TO_REPRESENTATION) | int(UAP.ADD_OR_MODIFY)
            # IMPORTANT: pas de MODIFY, pas de EXTRACT, pas de EXTRACT_TEXT_AND_GRAPHICS
        writer.encrypt(
            user_password="",
            owner_password=owner_password,
            permissions_flag=permissions_flag,
        )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as f:
        writer.write(f)
