"""Regression tests for reconciliation outputs: quantities, amounts, calculations and the
expected/observed rule (proposed extension in docs/DATA_CONTRACTS.md, pending Dhruv + Maia).

All expected values are worked out by hand: PO for 20 monitors (MON-24) at 125.50 USD.
"""
import asyncio

import pytest
from pydantic import ValidationError

from demo.sample_workers import make_sample_workers
from procurement_recon.reconciliation.reconciliation_rules import reconcile as engine_reconcile
from procurement_recon.schemas.documents import DeliveryLine, DeliveryRecord, PricedLine, PurchaseOrder, SupplierInvoice
from procurement_recon.schemas.results import DiscrepancyType
from src.contracts import AmountTotals, Discrepancy, QuantityTotals, ReconciliationResult
from src.graph import build_graph, initial_state
from src.reconciliation_adapter import reconcile_worker

from decimal import Decimal


# ------------------------------------------------------------------ helpers

def line(line_id, qty, price):
    return {"line_id": line_id, "description": "24-inch monitor", "sku": "MON-24", "quantity": qty,
            "unit": "each", "unit_price": price, "evidence": [{"page": 1, "text": "MON-24"}]}


def doc(doc_id, kind, qty, price=None, number=None):
    priced = kind != "receipt"
    return {"case_id": "out-case", "revision": 1, "document_id": doc_id, "document_type": kind,
            "business_document_number": number, "supplier_id": "SUP-1" if priced else None,
            "purchase_order_id": None if kind == "purchase_order" else "po-1",
            "currency": "USD" if priced else None, "line_items": [line(f"{doc_id}-l1", qty, price)]}


def match(doc_id):
    target = {"document_id": "po-1", "line_id": "po-1-l1"}
    return {"case_id": "out-case", "revision": 1, "source": {"document_id": doc_id, "line_id": f"{doc_id}-l1"},
            "candidate_po_lines": [target], "selected_po_line": target, "decision": "matched", "method": "exact",
            "explanation": "SKU agrees", "evidence": [{"page": 1, "text": "MON-24"}]}


def run(invoiced, *receipts, invoice_price="125.50"):
    """receipts: (document_id, quantity[, receipt number]). Ordered is always 20 at 125.50."""
    receipts = [r if len(r) == 3 else (*r, None) for r in receipts]
    docs = [doc("po-1", "purchase_order", "20", "125.50"), doc("inv-1", "invoice", invoiced, invoice_price, "INV-1")]
    docs += [doc(d, "receipt", q, None, n) for d, q, n in receipts]
    state = {"case_id": "out-case", "revision": 1, "documents": docs,
             "matches": [match(s) for s in ["inv-1", *(r[0] for r in receipts)]]}
    return asyncio.run(reconcile_worker(state))


def finding(result, kind):
    return next(d for d in result["discrepancies"] if d["explanation"].startswith(f"[{kind}]"))


def kinds(result):
    return sorted(d["explanation"][1:].split("]")[0] for d in result["discrepancies"])


def totals(result):
    q = result["quantities"][0]
    return q["ordered"], q["received"], q["invoiced"]


def money(result):
    a = result["amounts"]
    return a["billed"], a["supported"], a["unsupported"], a["unbilled"]


# ------------------------------------------------------------------ clean case

def test_clean_case_keeps_quantities_amounts_and_calculation():
    result = run("20", ("rec-1", "20"))
    assert result["status"] == "clean"
    assert totals(result) == ("20", "20", "20")
    assert result["quantities"][0]["po_line"] == {"document_id": "po-1", "line_id": "po-1-l1"}
    assert result["quantities"][0]["calculation"] == "ordered = 20; received = 20 [rec-1] = 20; invoiced = 20 [inv-1] = 20"
    assert money(result) == ("2510.00", "2510.00", "0", "0")
    assert result["amounts"]["currency"] == "USD"


# ------------------------------------------------------------------ partial deliveries

def test_partial_delivery_billed_as_received():
    result = run("18", ("rec-1", "18"))
    assert result["status"] == "needs_review"
    assert kinds(result) == ["MISSING_DELIVERY"]
    shortfall = finding(result, "MISSING_DELIVERY")
    assert (shortfall["expected"], shortfall["observed"]) == ("20", "18")
    assert shortfall["calculation"] == "ordered 20 - received 18 = 2 short"
    assert totals(result) == ("20", "18", "18")
    assert money(result) == ("2259.00", "2259.00", "0", "0")


def test_multiple_deliveries_show_every_part_of_the_received_total():
    result = run("20", ("rec-1", "18", "DN-1"), ("rec-2", "2", "DN-2"))
    assert result["status"] == "clean"
    assert "received = 18 [rec-1] + 2 [rec-2] = 20" in result["quantities"][0]["calculation"]


def test_repeated_receipt_is_excluded_from_the_received_calculation():
    result = run("20", ("rec-1", "18", "DN-1"), ("rec-2", "2", "DN-2"), ("rec-1-again", "18", "DN-1"))
    assert totals(result) == ("20", "20", "20")
    assert "rec-1-again" not in result["quantities"][0]["calculation"]
    repeat = finding(result, "REPEATED_RECEIPT")
    assert repeat["calculation"] == "received totals include rec-1 and exclude rec-1-again"


# ------------------------------------------------------------------ overbilling

def test_billed_more_than_received():
    result = run("20", ("rec-1", "18"))
    over = finding(result, "BILLED_EXCEEDS_RECEIVED")
    assert (over["expected"], over["observed"]) == ("18", "20")
    assert over["calculation"] == "billed 20 - received 18 = 2 units x PO price 125.50 = 251.00"
    assert money(result) == ("2510.00", "2259.00", "251.00", "0")


def test_billed_more_than_ordered():
    result = run("25", ("rec-1", "20"))
    assert kinds(result) == ["BILLED_EXCEEDS_ORDERED", "BILLED_EXCEEDS_RECEIVED"]
    over = finding(result, "BILLED_EXCEEDS_ORDERED")
    assert (over["expected"], over["observed"]) == ("20", "25")
    assert over["calculation"] == "billed 25 - ordered 20 = 5 units x PO price 125.50 = 627.50"
    assert money(result) == ("3137.50", "2510.00", "627.50", "0")


def test_over_delivery():
    result = run("20", ("rec-1", "22"))
    over = finding(result, "OVER_DELIVERY")
    assert (over["expected"], over["observed"]) == ("20", "22")
    assert over["calculation"] == "received 22 - ordered 20 = 2 extra"


# ------------------------------------------------------------------ underbilling (no negative numbers)

def test_under_billing_is_reported_as_nonnegative_unbilled():
    result = run("18", ("rec-1", "20"))
    under = finding(result, "UNDER_BILLED")
    assert (under["expected"], under["observed"]) == ("20", "18")
    assert under["calculation"] == ("payable = lower of ordered 20 and received 20 = 20; "
                                    "payable 20 - billed 18 = 2 units x PO price 125.50 = 251.00")
    assert money(result) == ("2259.00", "2510.00", "0", "251.00")


def test_lower_invoice_price_is_reported_as_unbilled_not_negative():
    result = run("20", ("rec-1", "20"), invoice_price="120.00")
    price = finding(result, "PRICE_MISMATCH")
    assert (price["expected"], price["observed"]) == ("125.50", "120.00")
    assert price["calculation"] == "invoice price 120.00 - PO price 125.50 = -5.50 per unit x billed quantity 20 = -110.00"
    assert money(result) == ("2400.00", "2510.00", "0", "110.00")


def test_higher_invoice_price_is_unsupported():
    result = run("20", ("rec-1", "20"), invoice_price="130.00")
    assert money(result) == ("2600.00", "2510.00", "90.00", "0")


# ------------------------------------------------------------------ missing values stay missing

@pytest.mark.parametrize("change", ["receipt_quantity", "invoice_price"])
def test_missing_values_produce_no_totals_rather_than_zero(change):
    result = run("20", ("rec-1", None)) if change == "receipt_quantity" else run("20", ("rec-1", "20"), invoice_price=None)
    assert result["status"] == "blocked"
    assert result["quantities"] == []      # not calculated, not "0"
    assert result["amounts"] is None


# ------------------------------------------------------------------ expected / observed rule

# expected = value from the reference document; observed = value on the document being checked.
RULE = {
    DiscrepancyType.MISSING_DELIVERY: ("ordered", "received"),
    DiscrepancyType.OVER_DELIVERY: ("ordered", "received"),
    DiscrepancyType.BILLED_EXCEEDS_RECEIVED: ("received", "invoiced"),
    DiscrepancyType.BILLED_EXCEEDS_ORDERED: ("ordered", "invoiced"),
    DiscrepancyType.UNDER_BILLED: ("lower of ordered and received", "invoiced"),
    DiscrepancyType.PRICE_MISMATCH: ("PO unit price", "invoice unit price"),
    DiscrepancyType.UNMATCHED_INVOICE_LINE: (None, "invoiced"),
    DiscrepancyType.UNMATCHED_DELIVERY_LINE: (None, "received"),
    DiscrepancyType.SUPPLIER_MISMATCH: (None, None),
    DiscrepancyType.CURRENCY_MISMATCH: (None, None),
}


def test_every_engine_finding_type_has_a_documented_expected_observed_rule():
    assert set(RULE) == set(DiscrepancyType)
    guide = open("docs/DATA_CONTRACTS.md", encoding="utf-8").read()
    for kind in DiscrepancyType:
        assert kind.value in guide, f"{kind.value} missing from the expected/observed table"


@pytest.mark.parametrize("invoiced,received,price,kind,expected,observed", [
    ("20", "18", "125.50", "MISSING_DELIVERY", "20", "18"),           # ordered, received
    ("20", "22", "125.50", "OVER_DELIVERY", "20", "22"),              # ordered, received
    ("20", "18", "125.50", "BILLED_EXCEEDS_RECEIVED", "18", "20"),    # received, invoiced
    ("25", "20", "125.50", "BILLED_EXCEEDS_ORDERED", "20", "25"),     # ordered, invoiced
    ("18", "20", "125.50", "UNDER_BILLED", "20", "18"),               # payable, invoiced
    ("20", "20", "130.00", "PRICE_MISMATCH", "125.50", "130.00"),     # PO price, invoice price
])
def test_expected_is_reference_and_observed_is_checked_document(invoiced, received, price, kind, expected, observed):
    found = finding(run(invoiced, ("rec-1", received), invoice_price=price), kind)
    assert (found["expected"], found["observed"]) == (expected, observed)


def test_unmatched_lines_have_no_expected_value_and_observe_their_own_quantity():
    po = PurchaseOrder("PO-1", 1, "SUP-1", "USD", (PricedLine("MON-24", "monitor", Decimal("20"), Decimal("125.50")),))
    inv = SupplierInvoice("INV-1", 1, "PO-1", "SUP-1", "USD", (
        PricedLine("MON-24", "monitor", Decimal("20"), Decimal("125.50")),
        PricedLine("KB-1", "keyboard", Decimal("3"), Decimal("40.00"))))
    rec = DeliveryRecord("REC-1", 1, "PO-1", (DeliveryLine("MON-24", "monitor", Decimal("20")),
                                               DeliveryLine("MS-1", "mouse", Decimal("4"))))
    report = engine_reconcile(po, [inv], [rec])
    by_type = {d.type: d for d in report.discrepancies}
    assert (by_type[DiscrepancyType.UNMATCHED_INVOICE_LINE].expected, by_type[DiscrepancyType.UNMATCHED_INVOICE_LINE].actual) == (None, Decimal("3"))
    assert by_type[DiscrepancyType.UNMATCHED_INVOICE_LINE].calculation == "billed 3 x invoice price 40.00 = 120.00 for a SKU not on the purchase order"
    assert (by_type[DiscrepancyType.UNMATCHED_DELIVERY_LINE].expected, by_type[DiscrepancyType.UNMATCHED_DELIVERY_LINE].actual) == (None, Decimal("4"))


def test_every_arithmetic_finding_carries_a_calculation():
    for result in [run("20", ("rec-1", "18")), run("25", ("rec-1", "22")), run("18", ("rec-1", "20"), invoice_price="130.00")]:
        for d in result["discrepancies"]:
            assert d["calculation"], d["explanation"]


# ------------------------------------------------------------------ backward compatibility of the proposed fields

def test_results_without_new_fields_remain_valid():
    old = {"case_id": "c", "revision": 1, "status": "needs_review", "checked_lines": [],
           "discrepancies": [{"code": "quantity_mismatch", "lines": [{"document_id": "d", "line_id": "l"}],
                              "expected": "20", "observed": "18", "explanation": "old producer"}]}
    result = ReconciliationResult.model_validate(old)
    assert result.quantities == [] and result.amounts is None and result.discrepancies[0].calculation is None


@pytest.mark.parametrize("field,value", [("unsupported", "-251.00"), ("unbilled", "-1"), ("billed", 2510.0), ("supported", "1e3")])
def test_amounts_follow_the_existing_number_rules(field, value):
    payload = {"currency": "USD", "billed": "2510.00", "supported": "2259.00", "unsupported": "251.00", "unbilled": "0"}
    payload[field] = value
    with pytest.raises(ValidationError):
        AmountTotals.model_validate(payload)


def test_amounts_cannot_be_both_unsupported_and_unbilled():
    with pytest.raises(ValidationError):
        AmountTotals(currency="USD", billed="1", supported="1", unsupported="1", unbilled="1")


def test_each_po_line_listed_once_in_quantities():
    q = QuantityTotals(po_line={"document_id": "po", "line_id": "1"}, ordered="1", received="1", invoiced="1", calculation="x")
    with pytest.raises(ValidationError):
        ReconciliationResult(case_id="c", revision=1, status="clean", discrepancies=[],
                             checked_lines=[q.po_line], quantities=[q, q])


def test_discrepancy_calculation_is_optional():
    assert Discrepancy(code="unit_mismatch", lines=[{"document_id": "d", "line_id": "l"}],
                       expected=None, observed=None, explanation="x").calculation is None


def test_full_workflow_keeps_the_new_fields():
    state = asyncio.run(build_graph(make_sample_workers("short-delivery")).ainvoke(initial_state("output-case")))
    reconciliation = state["reconciliation"]
    assert reconciliation["quantities"][0]["received"] == "18"
    assert reconciliation["amounts"]["unsupported"] == "251.00"
    assert all(d["calculation"] for d in reconciliation["discrepancies"])
