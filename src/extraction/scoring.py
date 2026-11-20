"""Field-level accuracy of an extracted document against an answer key
(Jijuan's ground-truth `documents[]`, same contract). Lines are paired by line_id.
Evidence text is not scored: wording of a quote is not a field value."""
from collections import Counter

from src.contracts import ExtractedDocument

HEADER_FIELDS = ("document_type", "business_document_number", "supplier_id", "currency")
LINE_FIELDS = ("description", "sku", "quantity", "unit", "unit_price")


def score_document(predicted: ExtractedDocument | None, expected: ExtractedDocument) -> dict:
    """Returns {'correct': Counter(field -> n), 'total': Counter(field -> n), 'errors': [...]}.
    A failed extraction (None) counts every expected field as wrong."""
    correct, total, errors = Counter(), Counter(), []

    def compare(field, got, want, line_id=None):
        total[field] += 1
        if got == want:
            correct[field] += 1
        else:
            errors.append({"document_id": expected.document_id, "line_id": line_id,
                           "field": field, "expected": want, "got": got})

    for field in HEADER_FIELDS:
        compare(field, getattr(predicted, field) if predicted else None, getattr(expected, field))
    got_lines = {line.line_id: line for line in predicted.line_items} if predicted else {}
    for want in expected.line_items:
        got = got_lines.get(want.line_id)
        for field in LINE_FIELDS:
            compare(field, getattr(got, field) if got else None, getattr(want, field), want.line_id)
    extra = set(got_lines) - {line.line_id for line in expected.line_items}
    total["line_count"] += 1
    if not extra and predicted and len(predicted.line_items) == len(expected.line_items):
        correct["line_count"] += 1
    else:
        errors.append({"document_id": expected.document_id, "line_id": None, "field": "line_count",
                       "expected": len(expected.line_items),
                       "got": len(predicted.line_items) if predicted else 0})
    return {"correct": correct, "total": total, "errors": errors}


def accuracy(results: list[dict]) -> dict[str, float]:
    correct, total = Counter(), Counter()
    for r in results:
        correct.update(r["correct"])
        total.update(r["total"])
    by_field = {f: correct[f] / total[f] for f in total}
    by_field["overall"] = sum(correct.values()) / sum(total.values()) if total else 0.0
    return by_field
