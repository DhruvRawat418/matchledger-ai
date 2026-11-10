"""Synthetic interface example; not an implemented AI workflow."""
import json
from src.contracts import ExtractedDocument, MatchResult, ReconciliationResult, validate_handoff


def sample_documents():
    docs = []
    for doc_id, kind, quantity in [("po-001", "purchase_order", "20"), ("invoice-001", "invoice", "20"), ("receipt-001", "receipt", "18")]:
        docs.append(ExtractedDocument.model_validate({
            "case_id": "sample-001", "revision": 1, "document_id": doc_id,
            "document_type": kind, "currency": None if kind == "receipt" else "USD",
            "line_items": [{"line_id": f"{doc_id}-line-1", "description": "24-inch monitor", "sku": "MON-24",
                            "quantity": quantity, "unit": "each", "unit_price": None if kind == "receipt" else "125.50",
                            "evidence": [{"page": 1, "text": f"MON-24 | 24-inch monitor | {quantity} each"}]}]}))
    return docs


def sample_matches():
    target = {"document_id": "po-001", "line_id": "po-001-line-1"}
    return [MatchResult.model_validate({"case_id": "sample-001", "revision": 1,
        "source": {"document_id": d, "line_id": f"{d}-line-1"},
        "candidate_po_lines": [target], "selected_po_line": target, "decision": "matched", "method": "exact",
        "explanation": "Synthetic example: SKU MON-24 agrees.", "evidence": [{"page": 1, "text": "MON-24"}]})
        for d in ("invoice-001", "receipt-001")]


def main():
    docs, matches = sample_documents(), sample_matches()
    validate_handoff(docs, matches)
    result = ReconciliationResult.model_validate({"case_id": "sample-001", "revision": 1,
        "status": "needs_review", "checked_lines": [m.source.model_dump() for m in matches],
        "discrepancies": [{"code": "quantity_mismatch", "lines": [m.source.model_dump() for m in matches],
                           "expected": "20", "observed": "18", "explanation": "Fixture: 20 invoiced, 18 received. Two units require review."}]})
    print("Synthetic contracts validated. No AI calls or reconciliation engine ran.")
    print(json.dumps({"documents": [d.model_dump() for d in docs], "matches": [m.model_dump() for m in matches],
                      "expected_result_fixture": result.model_dump()}, indent=2))

if __name__ == "__main__":
    main()
