from app.services.pdf_insert_image import insert_image_in_pdf

insert_image_in_pdf(
    pdf_path: str,
    image_path: str,
    output_path: str,
    page_number: int = 0,
    x: float = 100,
    y: float = 100,
    width: float = None,
    height: float = None,
    opacity: float = 1.0,
)
