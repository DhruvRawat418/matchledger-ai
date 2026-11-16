"""Cross-document checks. Regression for the first scan run (2026-11-16): the PO
number `PO-3004` was read as `PQ-3004` on a speckled scan and nothing flagged it."""
import asyncio
import json

from src.contracts import ExtractedDocument
from src.extraction import ExtractionOutcome, FakeModelClient
from src.extraction.crosscheck import check_case
from src.extraction.evaluate import evaluate, summarize


def outcome(doc_id, doc_type, number, reference=None):
    line = {"line_id": "1", "description": "Office chair", "sku": "CHR-STD", "quantity": "10", "unit": "each",
            "unit_price": None if doc_type == "receipt" else "45.00",
            "evidence": [{"page": 1, "text": "CHR-STD Office chair 10 each"}]}
    doc = ExtractedDocument.model_validate({
        "case_id": "case-104", "revision": 1, "document_id": doc_id, "document_type": doc_type,
        "business_document_number": number, "supplier_id": "SUP-SYNTH", "purchase_order_id": None,
        "currency": None if doc_type == "receipt" else "USD", "line_items": [line]})
    return ExtractionOutcome(document=doc, issues=[], referenced_po_number=reference, prompt_version="v",
                             model_id="m", attempts=1, latency_ms=1.0)


def test_lookalike_misread_is_flagged_on_both_documents_and_not_corrected():
    po = outcome("po", "purchase_order", "PQ-3004")          # what the model read on the scan
    inv = outcome("inv", "invoice", "INV-9901", reference="PO-3004")
    rcv = outcome("rcv", "receipt", "DN-9901", reference="PO-3004")
    issues = check_case([po, inv, rcv])
    assert set(issues) == {"po", "inv", "rcv"}
    assert all(i.code == "po_number_possible_misread" and i.severity == "review" for v in issues.values() for i in v)
    assert len(issues["po"]) == 1  # Two references to the same PO give one message.
    assert po.document.business_document_number == "PQ-3004"  # Nothing is corrected.


def test_exact_match_raises_nothing_and_formatting_is_ignored():
    po = outcome("po", "purchase_order", "PO-3004")
    assert check_case([po, outcome("inv", "invoice", "I-1", reference="po 3004")]) == {}


def test_different_number_is_not_found_rather_than_misread():
    issues = check_case([outcome("po", "purchase_order", "PO-3004"),
                         outcome("inv", "invoice", "I-1", reference="PO-3005")])
    assert [i.code for i in issues["inv"]] == ["po_reference_not_found"] and "po" not in issues


def test_silent_error_measure_counts_only_unflagged_mistakes(tmp_path):
    """End to end: the misread is wrong in the score, but no longer silent."""
    import pytest
    pytest.importorskip("reportlab")
    from src.extraction.render import render_cases, write_metadata

    def doc(doc_id, doc_type, number, po_id=None):
        return {"case_id": "case-x", "revision": 1, "document_id": doc_id, "document_type": doc_type,
                "business_document_number": number, "supplier_id": "SUP-SYNTH", "purchase_order_id": po_id,
                "currency": "USD", "line_items": [{"line_id": "1", "description": "Office chair", "sku": "CHR-STD",
                "quantity": "10", "unit": "each", "unit_price": "45.00",
                "evidence": [{"page": 1, "text": "CHR-STD | Office chair | 10 each | $45.00"}]}]}

    case = {"case_id": "case-x", "documents": [doc("case-x-po", "purchase_order", "PO-3004"),
                                               doc("case-x-inv", "invoice", "INV-1", "case-x-po")]}
    written = render_cases([case], tmp_path)
    write_metadata(tmp_path, written)

    def reply(number, reference):
        return json.dumps({"document_type": None, "business_document_number": number,
                           "supplier_name": "Synthetic Office Supply Co.", "referenced_po_number": reference,
                           "currency": "USD", "line_items": [{"description": "Office chair", "sku": "CHR-STD",
                           "quantity": "10", "unit": "each", "unit_price": "$45.00", "evidence_page": 1,
                           "evidence_text": "CHR-STD | Office chair | 10 each | $45.00"}]})

    suppliers = json.loads((tmp_path / "suppliers.json").read_text())
    with_reference = FakeModelClient([reply("PQ-3004", None), reply("INV-1", "PO-3004")])
    rows, _ = asyncio.run(evaluate(with_reference, [case], tmp_path, suppliers, {}, declare_type=True))
    summary = summarize(rows, 0, "m", None, None)
    assert summary["accuracy_by_field"]["business_document_number"] == 0.5
    assert summary["silent_error_documents"] == 0
    assert summary["issue_counts"]["po_number_possible_misread"] == 2

    po_only = {"case_id": "case-x", "documents": case["documents"][:1]}
    rows, _ = asyncio.run(evaluate(FakeModelClient([reply("PQ-3004", None)]), [po_only], tmp_path,
                                   suppliers, {}, declare_type=True))
    assert summarize(rows, 0, "m", None, None)["silent_error_documents"] == 1  # Nothing to compare against.
