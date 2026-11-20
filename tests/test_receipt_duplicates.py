"""Regression tests for receipt double-counting (audit findings B1-B4) in the integrated engine.

A repeated upload is not a new delivery; genuine separate deliveries still add up. Cases are run
through the real adapter + procurement_recon engine, exactly as the LangGraph workflow calls them.
Expected answers are worked out by hand from ordered 20 monitors at 125.50.
"""
import asyncio

from src.reconciliation_adapter import reconcile_worker, screen_receipts
from src.contracts import ExtractedDocument


def line(line_id, qty, price="125.50"):
    return {"line_id": line_id, "description": "24-inch monitor", "sku": "MON-24", "quantity": qty,
            "unit": "each", "unit_price": price, "evidence": [{"page": 1, "text": "MON-24"}]}


def doc(doc_id, kind, qty, number=None):
    priced = kind != "receipt"
    return {"case_id": "dup-case", "revision": 1, "document_id": doc_id, "document_type": kind,
            "business_document_number": number, "supplier_id": "SUP-1" if priced else None,
            "purchase_order_id": None if kind == "purchase_order" else "po-1",
            "currency": "USD" if priced else None,
            "line_items": [line(f"{doc_id}-l1", qty, "125.50" if priced else None)]}


def match(doc_id):
    target = {"document_id": "po-1", "line_id": "po-1-l1"}
    return {"case_id": "dup-case", "revision": 1, "source": {"document_id": doc_id, "line_id": f"{doc_id}-l1"},
            "candidate_po_lines": [target], "selected_po_line": target, "decision": "matched",
            "method": "exact", "explanation": "SKU MON-24 agrees", "evidence": [{"page": 1, "text": "MON-24"}]}


def reconcile(invoiced, *receipts):
    """receipts: (document_id, quantity, receipt_number) tuples. Ordered quantity is always 20."""
    docs = [doc("po-1", "purchase_order", "20"), doc("inv-1", "invoice", invoiced, "INV-1")]
    docs += [doc(doc_id, "receipt", qty, number) for doc_id, qty, number in receipts]
    sources = ["inv-1", *(r[0] for r in receipts)]
    state = {"case_id": "dup-case", "revision": 1, "documents": docs, "matches": [match(s) for s in sources]}
    return asyncio.run(reconcile_worker(state))


def findings(result):
    """Engine finding types (e.g. MISSING_DELIVERY) or screening labels, from the explanation prefix."""
    return sorted(d["explanation"][1:].split("]")[0] for d in result["discrepancies"])


def quantity_pairs(result):
    return sorted((d["expected"], d["observed"]) for d in result["discrepancies"] if d["code"] == "quantity_mismatch")


# ------------------------------------------------- legitimate multiple deliveries are preserved

def test_18_then_2_separate_deliveries_total_20_and_clean():
    result = reconcile("20", ("rec-1", "18", "DN-1"), ("rec-2", "2", "DN-2"))
    assert result["status"] == "clean"


def test_18_then_2_without_receipt_numbers_still_counts_both():
    # Different contents, no evidence of a repeat: two deliveries (matches Dhruv's existing test).
    assert reconcile("20", ("rec-1", "18", None), ("rec-2", "2", None))["status"] == "clean"


def test_two_identical_deliveries_with_different_numbers_both_count():
    assert reconcile("20", ("rec-1", "10", "DN-1"), ("rec-2", "10", "DN-2"))["status"] == "clean"


# ------------------------------------------------- repeated uploads never raise the received total

def test_reuploading_the_18_receipt_keeps_total_at_20():
    result = reconcile("20", ("rec-1", "18", "DN-1"), ("rec-2", "2", "DN-2"), ("rec-1-again", "18", "DN-1"))
    assert findings(result) == ["REPEATED_RECEIPT"]  # no OVER_DELIVERY: received is still 18 + 2 = 20
    assert result["status"] == "needs_review"


def test_reuploading_the_2_receipt_keeps_total_at_20():
    result = reconcile("20", ("rec-1", "18", "DN-1"), ("rec-2", "2", "DN-2"), ("rec-2-again", "2.0", "dn-2"))
    assert findings(result) == ["REPEATED_RECEIPT"]  # "2.0" = "2" and "dn-2" = "DN-2"


def test_b1_receipt_of_10_uploaded_twice_is_not_a_false_clean():
    # Before the fix: 10 + 10 = 20 received -> "clean" while only 10 were delivered.
    result = reconcile("20", ("rec-1", "10", None), ("rec-1-copy", "10", None))
    assert result["status"] == "needs_review"
    assert findings(result) == ["BILLED_EXCEEDS_RECEIVED", "MISSING_DELIVERY", "POSSIBLE_REPEATED_RECEIPT"]
    assert quantity_pairs(result) == [("10", "20"), ("20", "10")]  # counted once: received 10


def test_b2_repeated_18_receipt_reports_real_shortfall_not_over_delivery():
    # Before the fix: "received 36, 16 extra", hiding the real shortfall of 2.
    result = reconcile("20", ("rec-1", "18", "DN-1"), ("rec-1-copy", "18", "DN-1"))
    assert findings(result) == ["BILLED_EXCEEDS_RECEIVED", "MISSING_DELIVERY", "REPEATED_RECEIPT"]
    assert quantity_pairs(result) == [("18", "20"), ("20", "18")]


def test_b3_same_receipt_number_with_different_lines_is_blocked_not_summed():
    # Before the fix: 18 + 20 = 38. Possibly a correction, possibly a conflict; we do not guess.
    result = reconcile("20", ("rec-1", "18", "DN-1"), ("rec-1-v2", "20", "DN-1"))
    assert result["status"] == "blocked"
    assert [d["code"] for d in result["discrepancies"]] == ["duplicate_document"]
    assert "rec-1" in result["discrepancies"][0]["explanation"] and "rec-1-v2" in result["discrepancies"][0]["explanation"]


def test_b4_repeat_detected_across_different_document_ids_when_one_number_is_missing():
    # Engine revision collapsing never fires (IDs differ, revision is the case revision);
    # the adapter screen must catch it.
    result = reconcile("20", ("rec-1", "20", "DN-1"), ("upload-2", "20", None))
    assert findings(result) == ["POSSIBLE_REPEATED_RECEIPT"]
    assert result["status"] == "needs_review"


def test_three_receipts_one_repeat_counts_two_deliveries():
    result = reconcile("20", ("rec-1", "10", "DN-1"), ("rec-1-copy", "10", "DN-1"), ("rec-2", "10", "DN-2"))
    assert findings(result) == ["REPEATED_RECEIPT"]  # 10 + 10 = 20, no shortfall or over-delivery


def test_repeat_finding_points_at_both_receipts():
    result = reconcile("20", ("rec-1", "18", "DN-1"), ("rec-2", "2", "DN-2"), ("rec-1-again", "18", "DN-1"))
    repeat = next(d for d in result["discrepancies"] if d["code"] == "duplicate_document")
    assert {r["document_id"] for r in repeat["lines"]} == {"rec-1", "rec-1-again"}


# ------------------------------------------------- partial delivery policy

def test_partial_delivery_billed_as_received_needs_review_without_overbilling():
    result = reconcile("18", ("rec-1", "18", "DN-1"))
    assert result["status"] == "needs_review"
    assert findings(result) == ["MISSING_DELIVERY"]  # no BILLED_EXCEEDS_RECEIVED / ORDERED


def test_screen_is_order_independent_for_which_deliveries_count():
    docs = [ExtractedDocument.model_validate(doc(*args)) for args in
            [("rec-1", "receipt", "18", "DN-1"), ("rec-1-copy", "receipt", "18", "DN-1"), ("rec-2", "receipt", "2", "DN-2")]]
    totals = []
    for ordering in (docs, docs[::-1]):
        counted, _, conflict = screen_receipts(ordering)
        assert conflict is None
        totals.append(sum(int(d.line_items[0].quantity) for d in counted))
    assert totals == [20, 20]
