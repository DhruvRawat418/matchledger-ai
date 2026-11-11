"""Unit tests for the reconciliation worker. Rule IDs refer to docs/RECONCILIATION_RULES.md."""
import pytest

from demo.contracts_example import sample_documents, sample_matches
from src.contracts import ExtractedDocument, MatchResult
from src.reconciliation import reconcile, reconcile_with_trace

CASE = "case-1"
PO = ("po-1", "po-1-l1")
_DEFAULT = object()


def line(line_id, qty, price=_DEFAULT, unit="each", sku="MON-24", desc="24-inch monitor"):
    return {"line_id": line_id, "description": desc, "sku": sku, "quantity": qty, "unit": unit,
            "unit_price": "125.50" if price is _DEFAULT else price,
            "evidence": [{"page": 1, "text": "synthetic"}]}


def doc(doc_id, kind, *lines, number=None, currency=_DEFAULT):
    if currency is _DEFAULT:
        currency = None if kind == "receipt" else "USD"
    return ExtractedDocument.model_validate({
        "case_id": CASE, "revision": 1, "document_id": doc_id, "document_type": kind,
        "business_document_number": number, "currency": currency, "line_items": list(lines)})


def po(*lines, doc_id="po-1"):
    return doc(doc_id, "purchase_order", *(lines or [line("po-1-l1", "20")]))


def inv(doc_id, qty, **kw):
    number = kw.pop("number", None)
    currency = kw.pop("currency", _DEFAULT)
    return doc(doc_id, "invoice", line(f"{doc_id}-l1", qty, **kw), number=number, currency=currency)


def rec(doc_id, qty, **kw):
    kw.setdefault("price", None)
    number = kw.pop("number", None)
    return doc(doc_id, "receipt", line(f"{doc_id}-l1", qty, **kw), number=number)


def match(source_doc, decision="matched", target=PO):
    target_ref = {"document_id": target[0], "line_id": target[1]}
    return MatchResult.model_validate({
        "case_id": CASE, "revision": 1,
        "source": {"document_id": source_doc, "line_id": f"{source_doc}-l1"},
        "candidate_po_lines": [target_ref],
        "selected_po_line": target_ref if decision == "matched" else None,
        "decision": decision, "method": "exact", "explanation": "test",
        "evidence": [{"page": 1, "text": "synthetic"}]})


def run(docs, sources=None, **decisions):
    """Reconcile, matching every invoice/receipt to PO line 1 unless told otherwise."""
    sources = sources if sources is not None else [d.document_id for d in docs if d.document_type != "purchase_order"]
    return reconcile(docs, [match(s, decisions.get(s, "matched")) for s in sources])


def codes(result):
    return [d.code for d in result.discrepancies]


# --------------------------------------------------------------- shared fixture & clean path

def test_shared_team_fixture_matches_expected_result():
    result = reconcile(sample_documents(), sample_matches())
    assert result.status == "needs_review"
    assert codes(result) == ["quantity_mismatch"]
    d = result.discrepancies[0]
    assert (d.expected, d.observed) == ("20", "18")  # same numbers as the team's expected-result fixture


def test_perfect_three_way_match_is_clean():
    result = run([po(), inv("inv-1", "20"), rec("rec-1", "20")])
    assert result.status == "clean"
    assert result.discrepancies == []
    assert {(r.document_id, r.line_id) for r in result.checked_lines} == {PO, ("inv-1", "inv-1-l1"), ("rec-1", "rec-1-l1")}


# --------------------------------------------------------------- partial deliveries / multiple receipts (R7)

def test_partial_delivery_over_two_receipts_billed_correctly_is_clean():
    report = reconcile_with_trace([po(), inv("inv-1", "18"), rec("rec-1", "9"), rec("rec-2", "9")],
                                  [match("inv-1"), match("rec-1"), match("rec-2")])
    assert report.result.status == "clean"
    outcomes = " | ".join(s.outcome for s in report.trace)
    assert "received total = 9 + 9 = 18" in outcomes
    assert "2 still open" in outcomes


def test_partial_invoicing_across_two_invoices_is_clean():
    assert run([po(), inv("inv-1", "10"), inv("inv-2", "10"), rec("rec-1", "20")]).status == "clean"


def test_decimal_arithmetic_is_exact():
    docs = [po(line("po-1-l1", "0.3")), inv("inv-1", "0.3"), rec("rec-1", "0.1"), rec("rec-2", "0.2")]
    assert run(docs).status == "clean"  # with floats 0.1 + 0.2 != 0.3


def test_open_po_line_with_no_activity_is_not_a_discrepancy():
    docs = [po(line("po-1-l1", "20"), line("po-1-l2", "5", sku="KB-1")), inv("inv-1", "20"), rec("rec-1", "20")]
    assert run(docs).status == "clean"


# --------------------------------------------------------------- quantity discrepancies (R8)

def test_invoice_exceeds_order_and_receipt():
    result = run([po(), inv("inv-1", "25"), rec("rec-1", "20")])
    assert result.status == "needs_review"
    pairs = {(d.expected, d.observed) for d in result.discrepancies}
    assert pairs == {("20", "25"), ("25", "20")}  # over-billed vs order, and vs receipt


def test_over_delivery_across_receipts():
    result = run([po(), inv("inv-1", "20"), rec("rec-1", "12"), rec("rec-2", "10")])
    assert codes(result) == ["quantity_mismatch"]
    assert (result.discrepancies[0].expected, result.discrepancies[0].observed) == ("20", "22")


def test_invoice_with_no_receipt_means_nothing_received():
    result = run([po(), inv("inv-1", "20")])
    assert codes(result) == ["quantity_mismatch"]
    assert (result.discrepancies[0].expected, result.discrepancies[0].observed) == ("20", "0")


# --------------------------------------------------------------- prices (R9)

def test_price_mismatch():
    result = run([po(), inv("inv-1", "20", price="130.00"), rec("rec-1", "20")])
    assert codes(result) == ["price_mismatch"]
    d = result.discrepancies[0]
    assert (d.expected, d.observed) == ("125.50", "130.00")
    assert result.status == "needs_review"


def test_trailing_zero_prices_are_equal():
    assert run([po(), inv("inv-1", "20", price="125.5"), rec("rec-1", "20")]).status == "clean"


# --------------------------------------------------------------- missing values are never zero (R7, R9)

def test_missing_invoice_quantity_blocks_and_is_not_zero():
    result = run([po(), inv("inv-1", None), rec("rec-1", "20")])
    assert result.status == "blocked"
    assert codes(result) == ["missing_information"]  # no "invoiced 0 < received 20" style conclusions


def test_missing_receipt_quantity_blocks_and_is_not_zero():
    result = run([po(), inv("inv-1", "20"), rec("rec-1", None)])
    assert result.status == "blocked"
    assert "quantity_mismatch" not in codes(result)  # would appear if missing were treated as 0 received


def test_missing_ordered_quantity_blocks():
    result = run([po(line("po-1-l1", None)), inv("inv-1", "20"), rec("rec-1", "20")])
    assert result.status == "blocked"
    assert codes(result) == ["missing_information"]


def test_missing_invoice_price_blocks():
    result = run([po(), inv("inv-1", "20", price=None), rec("rec-1", "20")])
    assert (result.status, codes(result)) == ("blocked", ["missing_information"])


def test_unknown_currency_blocks_price_check():
    result = run([po(), inv("inv-1", "20", currency=None), rec("rec-1", "20")])
    assert (result.status, codes(result)) == ("blocked", ["missing_information"])


# --------------------------------------------------------------- units (R6)

def test_unit_mismatch_blocks_and_skips_comparisons():
    result = run([po(), inv("inv-1", "2", unit="box", price="1506.00"), rec("rec-1", "20")])
    assert result.status == "blocked"
    assert codes(result) == ["unit_mismatch"]  # no misleading quantity or price mismatch


def test_unit_spelling_variants_are_equivalent():
    assert run([po(), inv("inv-1", "20", unit="EA"), rec("rec-1", "20", unit="pcs.")]).status == "clean"


def test_missing_unit_blocks():
    result = run([po(), inv("inv-1", "20", unit=None), rec("rec-1", "20")])
    assert (result.status, codes(result)) == ("blocked", ["missing_information"])


# --------------------------------------------------------------- duplicates (R2)

def test_identical_duplicate_invoice_is_counted_once():
    docs = [po(), inv("inv-1", "20", number="INV-1001"), inv("inv-1-copy", "20.0", number="inv-1001"), rec("rec-1", "20")]
    result = run(docs)
    assert codes(result) == ["duplicate_document"]  # no "invoiced 40" mismatch
    assert result.status == "needs_review"
    assert ("inv-1-copy", "inv-1-copy-l1") not in {(r.document_id, r.line_id) for r in result.checked_lines}


def test_conflicting_versions_of_same_invoice_block():
    docs = [po(), inv("inv-1", "20", number="INV-1001"), inv("inv-2", "18", number="INV-1001"), rec("rec-1", "20")]
    result = run(docs)
    assert result.status == "blocked"
    assert "duplicate_document" in codes(result)
    assert "quantity_mismatch" not in codes(result)  # neither version was counted


def test_identical_receipts_without_document_numbers_both_count():
    assert run([po(), inv("inv-1", "20"), rec("rec-1", "10"), rec("rec-2", "10")]).status == "clean"


# --------------------------------------------------------------- matching decisions (R3, R4, R5)

def test_uncertain_match_needs_review_and_is_not_counted():
    result = run([po(), inv("inv-1", "20"), rec("rec-1", "20"), rec("rec-2", "5")], **{"rec-2": "uncertain"})
    assert codes(result) == ["unmatched_item"]  # the uncertain 5 did not create an over-delivery
    assert result.status == "needs_review"


def test_line_with_no_match_decision_is_flagged():
    result = run([po(), inv("inv-1", "20"), rec("rec-1", "20")], sources=["inv-1"])
    assert "unmatched_item" in codes(result)


def test_sku_conflict_rejects_match():
    result = run([po(), inv("inv-1", "20"), rec("rec-1", "20", sku="MON-27")])
    assert "unmatched_item" in codes(result)
    assert result.status == "needs_review"


def test_sku_formatting_differences_are_not_conflicts():
    assert run([po(), inv("inv-1", "20", sku="mon 24"), rec("rec-1", "20")]).status == "clean"


def test_case_without_invoice_is_blocked():
    result = run([po(), rec("rec-1", "20")])
    assert result.status == "blocked"
    assert "missing_information" in codes(result)


# --------------------------------------------------------------- traces & interface (R1, R10)

def test_trace_is_reproducible_and_covers_every_discrepancy():
    docs = [po(), inv("inv-1", "25", price="130.00"), rec("rec-1", "18")]
    first = reconcile_with_trace(docs, [match("inv-1"), match("rec-1")])
    second = reconcile_with_trace(docs, [match("inv-1"), match("rec-1")])
    assert first.model_dump() == second.model_dump()
    flagged = [s for s in first.trace if s.outcome.startswith("FLAG")]
    assert len(flagged) == len(first.result.discrepancies)
    assert [s.step for s in first.trace] == list(range(1, len(first.trace) + 1))


def test_invalid_handoff_is_rejected():
    stale = match("inv-1").model_copy(update={"revision": 2})
    with pytest.raises(ValueError):
        reconcile([po(), inv("inv-1", "20")], [stale])
