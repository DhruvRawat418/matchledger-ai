"""Cross-document checks within a case, after each document is extracted.

Found on the first scan run (2026-11-16): on a speckled scan the model read the
PO number `PO-3004` as `PQ-3004`. Nothing was flagged, because header fields have
no quoted row to check and scans have no text layer. The invoice and receipt for
that order print `PO-3004`, so the mismatch shows up by comparing documents.

Nothing is corrected. A reference that matches a PO only once look-alike
characters are treated as equal (O/Q/D/0, I/L/1, S/5, B/8, Z/2) becomes a review
issue on both documents; a reference that matches no PO at all becomes another.
"""
import re

from src.extraction.normalize import clean_text
from src.extraction.schema import ExtractionIssue, ExtractionOutcome

_LOOKALIKE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})


def _exact(number: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", number.upper())


def _lookalike(number: str) -> str:
    return _exact(number).translate(_LOOKALIKE)


def check_case(outcomes: list[ExtractionOutcome]) -> dict[str, list[ExtractionIssue]]:
    """Issues to add, keyed by document_id. Only extracted documents take part."""
    docs = [o for o in outcomes if o.document is not None]
    pos = {o.document.document_id: o.document.business_document_number for o in docs
           if o.document.document_type == "purchase_order" and o.document.business_document_number}
    if not pos:
        return {}
    added: dict[str, list[ExtractionIssue]] = {}

    def add(document_id, code, message):
        added.setdefault(document_id, []).append(
            ExtractionIssue(code=code, severity="review", field="business_document_number", message=message))

    for outcome in docs:
        reference = clean_text(outcome.referenced_po_number)
        if outcome.document.document_type == "purchase_order" or not reference:
            continue
        if any(_exact(reference) == _exact(number) for number in pos.values()):
            continue
        near = [(doc_id, number) for doc_id, number in pos.items() if _lookalike(reference) == _lookalike(number)]
        if near:
            for doc_id, number in near:
                message = (f"References PO {reference}, but the purchase order reads {number}; "
                           "one of them may be misread (look-alike characters). Confirm against the documents.")
                add(outcome.document.document_id, "po_number_possible_misread", message)
                add(doc_id, "po_number_possible_misread", message)
        else:
            add(outcome.document.document_id, "po_reference_not_found",
                f"References PO {reference}, which matches no purchase order in this case.")
    # One message per document is enough when several references point at the same PO.
    return {doc_id: list({i.message: i for i in issues}.values()) for doc_id, issues in added.items()}
