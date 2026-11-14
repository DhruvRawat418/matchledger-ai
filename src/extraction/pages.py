"""Turn an uploaded file into page images (for the vision model) and, when the PDF
has one, its text layer (used only to verify the model's evidence quotes)."""
from dataclasses import dataclass
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
MAX_PAGES = 10  # Scope: small documents. Larger files go to staff.


class UnsupportedFile(ValueError):
    pass


@dataclass(frozen=True)
class Page:
    number: int              # 1-based
    image: bytes             # PNG or JPEG
    media_type: str          # "image/png" or "image/jpeg"
    text: str | None = None  # PDF text layer; None for scans and images


def load_pages(path: Path, dpi: int = 150) -> list[Page]:
    data = Path(path).read_bytes()
    if data.startswith(b"%PDF"):
        return _pdf_pages(data, dpi)
    if data.startswith(PNG_MAGIC):
        return [Page(1, data, "image/png")]
    if data.startswith(JPEG_MAGIC):
        return [Page(1, data, "image/jpeg")]
    # Decide by content, not by the file extension a user typed.
    raise UnsupportedFile("Only PDF, PNG and JPEG uploads are supported")


def _pdf_pages(data: bytes, dpi: int) -> list[Page]:
    import io

    import pypdf
    import pypdfium2

    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise UnsupportedFile("Encrypted PDFs are not supported")
    if len(reader.pages) > MAX_PAGES:
        raise UnsupportedFile(f"PDF has more than {MAX_PAGES} pages")
    pdf = pypdfium2.PdfDocument(data)
    pages = []
    for index, page in enumerate(pdf):
        bitmap = page.render(scale=dpi / 72).to_pil()
        buffer = io.BytesIO()
        bitmap.save(buffer, format="PNG")
        text = (reader.pages[index].extract_text() or "").strip() or None
        pages.append(Page(index + 1, buffer.getvalue(), "image/png", text))
    return pages
