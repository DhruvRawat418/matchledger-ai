"""Task 3 regression tests: SKU and unit normalization, precise messages, repeated invoice lines.

Run through the real adapter + procurement_recon engine. PO: 20 x MON-24 at 125.50 each.
"""
import asyncio
from decimal import Decimal

import pytest

from procurement_recon.schemas.documents import DeliveryLine, DeliveryRecord, PricedLine, SupplierInvoice
from src.reconciliation_adapter import normalize_sku, normalize_unit, reconcile_worker

PO_LINE = ("MON-24", "20", "each", "125.50")


def doc(doc_id, kind, lines, number=None, **header):
    priced = kind != "receipt"
    base = {"case_id": "rel-case", "revision": 1, "document_id": doc_id, "document_type": kind,
            "business_document_number": number, "supplier_id": "SUP-1" if priced else None,
            "purchase_order_id": None if kind == "purchase_order" else "po-1",
            "currency": "USD" if priced else None,
            "line_items": [{"line_id": f"{doc_id}-l{i}", "description": "monitor", "sku": sku, "quantity": qty,
                            "unit": unit, "unit_price": price if priced else None,
                            "evidence": [{"page": 1, "text": "synthetic"}]}
                           for i, (sku, qty, unit, price) in enumerate(lines, start=1)]}
    return {**base, **header}


def match(source_doc, line_no=1, decision="matched", method="exact", po_line="po-1-l1"):
    target = {"document_id": "po-1", "line_id": po_line}
    return {"case_id": "rel-case", "revision": 1,
            "source": {"document_id": source_doc, "line_id": f"{source_doc}-l{line_no}"},
            "candidate_po_lines": [target], "selected_po_line": target if decision == "matched" else None,
            "decision": decision, "method": method, "explanation": "test", "evidence": [{"page": 1, "text": "x"}]}


def run(docs, matches=None):
    if matches is None:
        matches = [match(d["document_id"], i) for d in docs if d["document_type"] != "purchase_order"
                   for i in range(1, len(d["line_items"]) + 1)]
    return asyncio.run(reconcile_worker({"case_id": "rel-case", "revision": 1, "documents": docs, "matches": matches}))


def case(invoice_lines=None, receipt_lines=None, po_lines=None, extra=()):
    return [doc("po-1", "purchase_order", po_lines or [PO_LINE]),
            doc("inv-1", "invoice", invoice_lines or [("MON-24", "20", "each", "125.50")], number="INV-1"),
            doc("rec-1", "receipt", receipt_lines or [("MON-24", "20", "each", None)], number="DN-1"), *extra]


def codes(result):
    return [d["code"] for d in result["discrepancies"]]


def refs(discrepancy):
    return {(r["document_id"], r["line_id"]) for r in discrepancy["lines"]}


# ------------------------------------------------------------------ 1. SKU normalization

@pytest.mark.parametrize("sku", ["mon-24", "Mon-24", "MON-24"])
def test_sku_letter_case_does_not_cause_a_false_mismatch(sku):
    result = run(case(invoice_lines=[(sku, "20", "each", "125.50")], receipt_lines=[(sku.lower(), "20", "each", None)]))
    assert result["status"] == "clean"


def test_surrounding_spaces_are_ignored():
    assert normalize_sku("  mon-24 ") == "MON-24"


@pytest.mark.parametrize("sku", ["MON 24", "MON24", "MON-27"])
def test_other_sku_differences_still_block_no_fuzzy_matching(sku):
    result = run(case(invoice_lines=[(sku, "20", "each", "125.50")]))
    assert result["status"] == "blocked"
    issue = result["discrepancies"][0]
    assert issue["code"] == "unmatched_item"
    assert refs(issue) == {("inv-1", "inv-1-l1"), ("po-1", "po-1-l1")}
    assert "invoice inv-1 line inv-1-l1" in issue["explanation"] and sku in issue["explanation"]


def test_receipt_repeat_detected_across_sku_letter_case():
    docs = case(extra=[doc("rec-1-again", "receipt", [("mon-24", "20", "EA", None)], number="DN-1")])
    result = run(docs)
    assert [d["explanation"].split("]")[0] for d in result["discrepancies"]] == ["[REPEATED_RECEIPT"]
    assert result["quantities"][0]["received"] == "20"  # still counted once


def test_po_listing_a_product_twice_after_normalization_is_blocked_with_clear_message():
    result = run(case(po_lines=[PO_LINE, ("mon-24", "5", "each", "125.50")]))
    assert result["status"] == "blocked"
    issue = result["discrepancies"][0]
    assert issue["code"] == "unmatched_item"
    assert refs(issue) == {("po-1", "po-1-l1"), ("po-1", "po-1-l2")}
    assert "PO po-1 lists product MON-24 on lines po-1-l1, po-1-l2" in issue["explanation"]


# ------------------------------------------------------------------ 2. unit normalization

@pytest.mark.parametrize("invoice_unit,receipt_unit", [("EA", "pcs"), ("ea.", "Pieces"), ("PC", "each"), ("Each", "EA")])
def test_listed_unit_spellings_are_equivalent(invoice_unit, receipt_unit):
    result = run(case(invoice_lines=[("MON-24", "20", invoice_unit, "125.50")],
                      receipt_lines=[("MON-24", "20", receipt_unit, None)]))
    assert result["status"] == "clean"


def test_unit_letter_case_only_is_equivalent():
    result = run(case(po_lines=[("MON-24", "20", "box", "125.50")], invoice_lines=[("MON-24", "20", "BOX", "125.50")],
                      receipt_lines=[("MON-24", "20", "Box", None)]))
    assert result["status"] == "clean"


@pytest.mark.parametrize("unit", ["box", "dozen", "unit", "pair"])
def test_units_without_a_defined_rule_stay_different(unit):
    result = run(case(invoice_lines=[("MON-24", "20", unit, "125.50")]))
    assert result["status"] == "blocked"
    issue = result["discrepancies"][0]
    assert issue["code"] == "unit_mismatch"
    assert f'"{unit}"' in issue["explanation"] and '"each"' in issue["explanation"]
    assert refs(issue) == {("inv-1", "inv-1-l1"), ("po-1", "po-1-l1")}


def test_unit_mapping_is_small_and_explicit():
    assert {normalize_unit(u) for u in ["each", "EA", "ea.", "pc", "PCS", "piece", "Pieces"]} == {"each"}
    assert normalize_unit("unit") == "unit" and normalize_unit("box") == "box"


# ------------------------------------------------------------------ 3. precise messages

def test_missing_value_names_the_exact_line_and_field():
    result = run(case(receipt_lines=[("MON-24", None, "each", None)]))
    issue = result["discrepancies"][0]
    assert result["status"] == "blocked" and issue["code"] == "missing_information"
    assert refs(issue) == {("rec-1", "rec-1-l1")}
    assert issue["explanation"] == "receipt rec-1 line rec-1-l1: missing quantity (not treated as zero)."


def test_all_line_problems_are_reported_together():
    result = run(case(invoice_lines=[("MON-24", "20", None, None)], receipt_lines=[("MON-24", None, "each", None)]))
    assert [d["explanation"] for d in result["discrepancies"]] == [
        "invoice inv-1 line inv-1-l1: missing unit, unit price (not treated as zero).",
        "receipt rec-1 line rec-1-l1: missing quantity (not treated as zero)."]


@pytest.mark.parametrize("field,text", [("supplier_id", "invoice inv-1: supplier ID is missing."),
                                        ("currency", "invoice inv-1: currency is unknown; USD is required."),
                                        ("purchase_order_id", "invoice inv-1: linked to PO none, not to the active PO po-1.")])
def test_header_problems_name_the_document(field, text):
    docs = case()
    docs[1][field] = None
    issue = run(docs)["discrepancies"][0]
    assert issue["explanation"] == text
    assert refs(issue) == {("inv-1", "inv-1-l1")}


def test_uncertain_match_names_the_line_and_is_not_accepted():
    docs = case()
    result = run(docs, [match("inv-1", decision="uncertain", method="llm"), match("rec-1")])
    assert result["status"] == "blocked"
    assert result["discrepancies"][0]["explanation"] == (
        "invoice inv-1 line inv-1-l1: match is uncertain (llm); this adapter accepts accepted line matches only.")


def test_missing_match_decision_names_the_line():
    result = run(case(), [match("inv-1")])
    assert result["discrepancies"][0]["explanation"] == "receipt rec-1 line rec-1-l1: no match decision."


def test_engine_findings_list_only_the_lines_involved():
    result = run(case(receipt_lines=[("MON-24", "18", "each", None)]))
    shortfall = next(d for d in result["discrepancies"] if d["explanation"].startswith("[MISSING_DELIVERY]"))
    assert refs(shortfall) == {("po-1", "po-1-l1"), ("rec-1", "rec-1-l1")}  # the invoice is not involved
    assert shortfall["explanation"].endswith("(lines: po-1 line po-1-l1; rec-1 line rec-1-l1)")
    billed = next(d for d in result["discrepancies"] if d["explanation"].startswith("[BILLED_EXCEEDS_RECEIVED]"))
    assert refs(billed) == {("po-1", "po-1-l1"), ("inv-1", "inv-1-l1"), ("rec-1", "rec-1-l1")}


def test_excluded_repeat_receipt_is_not_listed_on_quantity_findings():
    docs = case(receipt_lines=[("MON-24", "18", "each", None)],
                extra=[doc("rec-1-again", "receipt", [("MON-24", "18", "each", None)], number="DN-1")])
    shortfall = next(d for d in run(docs)["discrepancies"] if d["explanation"].startswith("[MISSING_DELIVERY]"))
    assert ("rec-1-again", "rec-1-again-l1") not in refs(shortfall)


# ------------------------------------------------------------------ 4. same product on two invoice lines

def test_two_different_invoice_lines_for_one_product_are_added_up_not_called_duplicates():
    result = run(case(invoice_lines=[("MON-24", "12", "each", "125.50"), ("MON-24", "8", "each", "125.50")]))
    assert result["status"] == "clean"
    assert result["quantities"][0]["invoiced"] == "20"
    assert "invoiced = 12 [inv-1] + 8 [inv-1] = 20" in result["quantities"][0]["calculation"]


def test_identical_invoice_lines_are_counted_but_flagged_for_review():
    result = run(case(invoice_lines=[("MON-24", "10", "each", "125.50"), ("MON-24", "10", "EA", "125.50")]))
    assert result["status"] == "needs_review"
    assert len(result["discrepancies"]) == 1
    flag = result["discrepancies"][0]
    assert flag["explanation"].startswith("[POSSIBLE_REPEATED_LINE] invoice inv-1 lines inv-1-l1, inv-1-l2 are identical")
    assert "Repeated SKU within a document" not in flag["explanation"]
    assert result["quantities"][0]["invoiced"] == "20"


def test_price_mismatch_points_at_the_off_price_line_only():
    result = run(case(invoice_lines=[("MON-24", "10", "each", "125.50"), ("MON-24", "10", "each", "130.00")]))
    price = [d for d in result["discrepancies"] if d["code"] == "price_mismatch"]
    assert len(price) == 1
    assert refs(price[0]) == {("po-1", "po-1-l1"), ("inv-1", "inv-1-l2")}
    assert (price[0]["expected"], price[0]["observed"]) == ("125.50", "130.00")


def test_repeated_product_on_one_receipt_is_still_blocked_with_an_accurate_reason():
    result = run(case(receipt_lines=[("MON-24", "10", "each", None), ("MON-24", "10", "each", None)]))
    assert result["status"] == "blocked"
    issue = result["discrepancies"][0]
    assert issue["code"] == "unmatched_item"  # not "duplicate_document"
    assert "receipt rec-1 lists product MON-24 on lines rec-1-l1, rec-1-l2" in issue["explanation"]


def test_engine_accepts_repeated_sku_on_invoices_but_not_on_deliveries():
    line = PricedLine("MON-24", "monitor", Decimal("1"), Decimal("1"))
    SupplierInvoice("INV-1", 1, "PO-1", "SUP-1", "USD", (line, line))  # no error
    with pytest.raises(ValueError, match="more than once"):
        DeliveryRecord("DEL-1", 1, "PO-1", (DeliveryLine("MON-24", "m", Decimal("1")),) * 2)
