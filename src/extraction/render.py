"""Render answer-key documents (Jijuan's ground-truth `documents[]`) as PDFs, so the
extractor can be measured against a known answer.

    python -m src.extraction.render --cases <answer-key folder> --out eval/rendered

Each product row is printed exactly as its answer-key `evidence.text`, on the page
the evidence names. That is what makes the answer key correct for the rendered
file: whatever a layout does around the rows, the row text the model must quote
is on the page, word for word. The PDF text layer is checked after rendering.

Two layouts, alternated by case so every document type appears in both:
  * classic: letterhead, ruled item table, Helvetica
  * compact: monospace, one-line header, numbered rows, no rules
Optional `--scan` also writes a PNG of each page, slightly rotated, blurred and
noisy, with no text layer: the "messy scan" condition from the proposal.

Rendering is deterministic (fixed dates, invariant PDFs), so two answer-key
documents with the same content (a duplicate upload) produce identical files.
"""
import argparse
import csv
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from src.contracts import ExtractedDocument
from src.extraction.normalize import comparable

# Printed supplier names for the synthetic supplier IDs. Evaluation passes this
# same mapping to the extractor as the supplier list (written to suppliers.json).
SUPPLIER_NAMES = {
    "SUP-ACME": "Acme Displays Inc.",
    "SUP-NORTHWIND": "Northwind Traders",
    "SUP-SYNTH": "Synthetic Office Supply Co.",
}
LAYOUTS = ("classic", "compact")
TITLES = {"purchase_order": "PURCHASE ORDER", "invoice": "INVOICE", "receipt": "DELIVERY NOTE"}
NUMBER_LABELS = {"purchase_order": "PO number", "invoice": "Invoice number", "receipt": "Delivery note"}
BASE_DATE = date(2026, 11, 2)


def supplier_name(supplier_id: str | None) -> str | None:
    if supplier_id is None:
        return None
    return SUPPLIER_NAMES.get(supplier_id, supplier_id.removeprefix("SUP-").replace("-", " ").title() + " Ltd.")


def _doc_date(doc: ExtractedDocument) -> str:
    # Seeded by printed content, not document_id, so a duplicate upload is the same file.
    offset = int(hashlib.sha256(_content_key(doc).encode()).hexdigest(), 16) % 10
    order = {"purchase_order": 0, "invoice": 10, "receipt": 12}[doc.document_type]
    return (BASE_DATE + timedelta(days=order + offset)).isoformat()


def _content_key(doc: ExtractedDocument) -> str:
    return json.dumps(doc.model_dump(exclude={"case_id", "document_id", "revision", "purchase_order_id"}),
                      sort_keys=True, default=str)


def _total(doc: ExtractedDocument) -> str | None:
    if doc.document_type == "receipt":
        return None
    try:
        total = sum(Decimal(line.quantity) * Decimal(line.unit_price) for line in doc.line_items)
    except TypeError:  # A missing quantity or price: print no total rather than a wrong one.
        return None
    return f"{total:,.2f}"


def _rows_by_page(doc: ExtractedDocument) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = {}
    for line in doc.line_items:
        for evidence in line.evidence:
            pages.setdefault(evidence.page, []).append(evidence.text)
    return pages


def render_document(doc: ExtractedDocument, po_number: str | None, layout: str, path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}")
    rows = _rows_by_page(doc)
    page_count = max(rows)
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setTitle(f"{TITLES[doc.document_type]} {doc.business_document_number or ''}".strip())
    width, height = letter
    meta = {
        "name": supplier_name(doc.supplier_id),
        "title": TITLES[doc.document_type],
        "number": doc.business_document_number,
        "date": _doc_date(doc),
        "po": po_number if doc.document_type != "purchase_order" else None,
        "currency": doc.currency if doc.document_type != "receipt" else None,
        "total": _total(doc),
    }
    for page in range(1, page_count + 1):
        draw = _classic if layout == "classic" else _compact
        draw(pdf, width, height, doc, meta, rows.get(page, []), page, page_count)
        pdf.showPage()
    pdf.save()


def _classic(pdf, width, height, doc, meta, rows, page, page_count):
    left, right = 54, width - 54
    y = height - 64
    if meta["name"]:
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(left, y, meta["name"])
    pdf.setFont("Helvetica", 9)
    pdf.drawString(left, y - 14, "1200 Commerce Way, Ithaca, NY 14850")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawRightString(right, y, meta["title"])
    y -= 46
    pdf.setFont("Helvetica", 10)
    details = [(NUMBER_LABELS[doc.document_type], meta["number"]), ("Date", meta["date"]),
               ("Purchase order", meta["po"]), ("Currency", meta["currency"])]
    for label, value in details:
        if value:
            pdf.drawString(right - 220, y, f"{label}:")
            pdf.drawRightString(right, y, value)
            y -= 14
    y -= 16
    pdf.setFont("Helvetica-Bold", 10)
    header = "Items received" if doc.document_type == "receipt" else "Item / description / quantity / price"
    pdf.drawString(left, y, header)
    y -= 6
    pdf.setLineWidth(1)
    pdf.line(left, y, right, y)
    pdf.setFont("Helvetica", 10)
    pdf.setLineWidth(0.3)
    for text in rows:
        y -= 18
        pdf.drawString(left + 4, y, text)
        pdf.line(left, y - 6, right, y - 6)
    if page == page_count and meta["total"]:
        y -= 30
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawRightString(right, y, f"Total: ${meta['total']}")
    if page == page_count and doc.document_type == "receipt":
        y -= 40
        pdf.setFont("Helvetica", 9)
        pdf.drawString(left, y, "Received by: ____________________     Signature: ____________________")
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, 36, f"Page {page} of {page_count}")


def _compact(pdf, width, height, doc, meta, rows, page, page_count):
    left = 48
    y = height - 56
    pdf.setFont("Courier-Bold", 12)
    pdf.drawString(left, y, f"{(meta['name'] or 'SUPPLIER').upper()} -- {meta['title']}")
    y -= 18
    pdf.setFont("Courier", 9)
    parts = [f"Ref {meta['number']}" if meta["number"] else None, f"Order {meta['po']}" if meta["po"] else None,
             meta["date"], meta["currency"]]
    pdf.drawString(left, y, " | ".join(p for p in parts if p))
    pdf.drawRightString(width - 48, y, f"p.{page}/{page_count}")
    y -= 30
    pdf.setFont("Courier", 10)
    for number, text in enumerate(rows, start=1):
        pdf.drawString(left, y, f"{number:02d}  {text}")
        y -= 15
    if page == page_count and meta["total"]:
        y -= 15
        pdf.setFont("Courier-Bold", 10)
        pdf.drawString(left, y, f"AMOUNT DUE {meta['currency'] or ''} {meta['total'].replace(',', '')}".replace("  ", " "))
    pdf.setFont("Courier", 8)
    pdf.drawString(left, 40, "Questions? accounts@example.com  |  Terms: net 30")


def scan_pages(pdf_path: Path, png_path: Path, seed: str) -> None:
    """Page 1 as a degraded image with no text layer (single-page documents only)."""
    import random

    import pypdfium2
    from PIL import ImageFilter

    rng = random.Random(seed)
    image = pypdfium2.PdfDocument(str(pdf_path))[0].render(scale=110 / 72).to_pil().convert("L")
    image = image.rotate(rng.uniform(-1.6, 1.6), expand=True, fillcolor=245).filter(ImageFilter.GaussianBlur(0.7))
    pixels = image.load()
    for _ in range(image.width * image.height // 60):  # Speckle noise.
        x, y = rng.randrange(image.width), rng.randrange(image.height)
        pixels[x, y] = rng.choice((90, 140, 200))
    image.save(png_path, format="PNG", optimize=True)


def render_cases(cases: list[dict], out: Path, layout: str | None = None, scan: bool = False) -> list[dict]:
    """Render every answer-key document. Returns one record per file written."""
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for index, case in enumerate(cases):
        docs = [ExtractedDocument.model_validate(raw) for raw in case["documents"]]
        numbers = {d.document_id: d.business_document_number for d in docs}
        case_layout = layout or LAYOUTS[index % len(LAYOUTS)]
        folder = out / case["case_id"]
        folder.mkdir(exist_ok=True)
        for doc in docs:
            path = folder / f"{doc.document_id}.pdf"
            render_document(doc, numbers.get(doc.purchase_order_id), case_layout, path)
            written.append({"case_id": doc.case_id, "document_id": doc.document_id,
                            "document_type": doc.document_type, "layout": case_layout, "file": path})
            if scan and max(_rows_by_page(doc)) == 1:
                scan_dir = out.parent / f"{out.name}-scan" / case["case_id"]
                scan_dir.mkdir(parents=True, exist_ok=True)
                scan_pages(path, scan_dir / f"{doc.document_id}.png", _content_key(doc))
    return written


def check_rendered(cases: list[dict], out: Path) -> list[str]:
    """Every evidence row must be in the PDF text layer of its page. Returns problems."""
    from src.extraction.pages import load_pages

    problems = []
    for case in cases:
        for raw in case["documents"]:
            doc = ExtractedDocument.model_validate(raw)
            pages = load_pages(out / case["case_id"] / f"{doc.document_id}.pdf")
            for line in doc.line_items:
                for evidence in line.evidence:
                    text = pages[evidence.page - 1].text if evidence.page <= len(pages) else None
                    if text is None or comparable(evidence.text) not in comparable(text):
                        problems.append(f"{doc.document_id} line {line.line_id}: evidence not on page {evidence.page}")
    return problems


def write_metadata(out: Path, written: list[dict]) -> None:
    with open(out / "layouts.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, ["document_id", "case_id", "document_type", "layout"])
        writer.writeheader()
        for record in written:
            writer.writerow({k: record[k] for k in ("document_id", "case_id", "document_type", "layout")})
    (out / "suppliers.json").write_text(json.dumps({v: k for k, v in SUPPLIER_NAMES.items()}, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, required=True, help="Folder of answer-key case-*.json files")
    parser.add_argument("--out", type=Path, default=Path("eval/rendered"))
    parser.add_argument("--layout", choices=LAYOUTS, help="Use one layout for every case (default: alternate)")
    parser.add_argument("--scan", action="store_true", help="Also write degraded PNG scans to <out>-scan/")
    args = parser.parse_args(argv)
    cases = [json.loads(p.read_text()) for p in sorted(args.cases.glob("case-*.json"))]
    written = render_cases(cases, args.out, args.layout, args.scan)
    write_metadata(args.out, written)
    problems = check_rendered(cases, args.out)
    for problem in problems:
        print("PROBLEM", problem)
    print(f"Rendered {len(written)} documents from {len(cases)} cases into {args.out}"
          f" ({'all ' + args.layout if args.layout else 'layouts alternate by case'}).")
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
