"""Unit tests for Member 3's rules, using synthetic documents only."""

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from procurement_recon.reconciliation import reconciliation_rules
from procurement_recon.reconciliation.reconciliation_rules import (
    compare_quantities,
    compare_unit_price,
    format_discrepancy_report,
    latest_revisions,
    match_exact_sku,
    reconcile,
)
from procurement_recon.schemas.documents import (
    DeliveryLine,
    DeliveryRecord,
    PricedLine,
    PurchaseOrder,
    SupplierInvoice,
)
from procurement_recon.schemas.results import DiscrepancyType as T
from procurement_recon.schemas.results import ReconciliationStatus

SKU = "MON-24"
PRICE = "150.00"


def order(quantity="20", price=PRICE):
    return PurchaseOrder(
        document_id="PO-1",
        revision=1,
        supplier_id="SUP-TEST",
        currency="USD",
        lines=(PricedLine(SKU, "24 inch monitor", Decimal(quantity), Decimal(price)),),
    )


def invoice(
    quantity="20",
    price=PRICE,
    document_id="INV-1",
    sku=SKU,
    currency="USD",
    supplier_id="SUP-TEST",
    purchase_order_id="PO-1",
):
    return SupplierInvoice(
        document_id=document_id,
        revision=1,
        purchase_order_id=purchase_order_id,
        supplier_id=supplier_id,
        currency=currency,
        lines=(PricedLine(sku, "Monitor", Decimal(quantity), Decimal(price)),),
    )


def delivery(quantity="20", document_id="DEL-1", revision=1, sku=SKU):
    return DeliveryRecord(
        document_id=document_id,
        revision=revision,
        purchase_order_id="PO-1",
        lines=(DeliveryLine(sku, "Monitor", Decimal(quantity)),),
    )


def types(report):
    return {item.type for item in report.discrepancies}


# Normal orders


def test_matching_documents_have_no_discrepancies():
    report = reconcile(order(), [invoice()], [delivery()])

    assert report.discrepancies == ()
    assert report.status is ReconciliationStatus.NO_DISCREPANCIES_PENDING_REVIEW
    assert report.billed_total == Decimal("3000.00")
    assert report.supported_total == Decimal("3000.00")
    assert report.unsupported_amount == Decimal("0.00")


def test_split_delivery_that_adds_up_is_clean():
    report = reconcile(
        order(),
        [invoice()],
        [delivery("12", "DEL-1"), delivery("8", "DEL-2")],
    )

    assert report.discrepancies == ()
    assert report.delivery_ids == ("DEL-1", "DEL-2")


def test_clean_result_still_waits_for_a_human():
    assert not [s for s in ReconciliationStatus if "APPROVED" in s.value]
    text = format_discrepancy_report(reconcile(order(), [invoice()], [delivery()]))
    assert "Human review is required" in text


# Missing deliveries


def test_two_missing_monitors_are_flagged():
    report = reconcile(order(), [invoice()], [delivery("18")])

    assert types(report) == {T.MISSING_DELIVERY, T.BILLED_EXCEEDS_RECEIVED}
    assert report.status is ReconciliationStatus.NEEDS_REVIEW
    assert report.unsupported_amount == Decimal("300.00")


def test_later_delivery_clears_the_shortage():
    report = reconcile(
        order(),
        [invoice()],
        [delivery("18", "DEL-1"), delivery("2", "DEL-2")],
    )

    assert report.discrepancies == ()
    assert report.unsupported_amount == Decimal("0.00")


def test_same_delivery_submitted_twice_is_counted_once():
    report = reconcile(
        order(),
        [invoice()],
        [delivery("18", "DEL-1"), delivery("2", "DEL-2"), delivery("2", "DEL-2")],
    )

    assert report.discrepancies == ()


def test_duplicate_delivery_cannot_hide_a_shortage():
    report = reconcile(
        order(),
        [invoice()],
        [delivery("10", "DEL-1"), delivery("10", "DEL-1")],
    )

    assert T.MISSING_DELIVERY in types(report)


def test_no_delivery_at_all():
    report = reconcile(order(), [invoice()], [])

    assert types(report) == {T.MISSING_DELIVERY, T.BILLED_EXCEEDS_RECEIVED}
    assert report.supported_total == Decimal("0.00")


def test_revised_delivery_replaces_the_original():
    report = reconcile(
        order(),
        [invoice()],
        [delivery("20", "DEL-1", revision=1), delivery("18", "DEL-1", revision=2)],
    )

    assert T.MISSING_DELIVERY in types(report)


# Price mismatches


def test_overcharge_is_flagged_with_its_amount():
    report = reconcile(order(), [invoice(price="155.00")], [delivery()])

    assert types(report) == {T.PRICE_MISMATCH}
    assert report.discrepancies[0].amount == Decimal("100.00")
    assert report.unsupported_amount == Decimal("100.00")


def test_undercharge_is_also_flagged():
    report = reconcile(order(), [invoice(price="149.99")], [delivery()])

    assert types(report) == {T.PRICE_MISMATCH}
    assert report.discrepancies[0].amount == Decimal("-0.20")


def test_price_within_tolerance_passes():
    report = reconcile(
        order(),
        [invoice(price="150.01")],
        [delivery()],
        price_tolerance=Decimal("0.01"),
    )

    assert report.discrepancies == ()


def test_price_just_over_tolerance_fails():
    finding = compare_unit_price(
        SKU, Decimal("150.00"), Decimal("150.02"), Decimal("1"), "INV-1", Decimal("0.01")
    )

    assert finding is not None


@pytest.mark.parametrize("tolerance", [Decimal("-0.01"), Decimal("NaN"), 0.01])
def test_bad_tolerance_is_rejected(tolerance):
    with pytest.raises(ValueError):
        compare_unit_price(
            SKU, Decimal("1"), Decimal("1"), Decimal("1"), "INV-1", tolerance
        )


def test_shortage_and_overcharge_total_is_exact():
    report = reconcile(order(), [invoice(price="155.00")], [delivery("18")])

    assert report.billed_total == Decimal("3100.00")
    assert report.supported_total == Decimal("2700.00")
    assert report.unsupported_amount == Decimal("400.00")


def test_decimal_avoids_float_rounding():
    report = reconcile(
        order("3", "0.10"), [invoice("3", "0.10")], [delivery("3")]
    )

    assert report.billed_total == Decimal("0.30")


# Over-deliveries


def test_over_delivery_billed_as_ordered():
    report = reconcile(order(), [invoice()], [delivery("22")])

    assert types(report) == {T.OVER_DELIVERY}
    assert report.unsupported_amount == Decimal("0.00")


def test_over_delivery_billed_in_full_is_not_supported_by_the_order():
    report = reconcile(order(), [invoice("22")], [delivery("22")])

    assert types(report) == {T.OVER_DELIVERY, T.BILLED_EXCEEDS_ORDERED}
    assert report.supported_total == Decimal("3000.00")
    assert report.unsupported_amount == Decimal("300.00")


# Other quantity checks


def test_billed_less_than_ordered_and_received():
    findings = compare_quantities(
        SKU, Decimal("20"), Decimal("20"), Decimal("15"), Decimal("150.00")
    )

    assert [item.type for item in findings] == [T.UNDER_BILLED]
    assert findings[0].amount == Decimal("750.00")


def test_billed_above_both_ordered_and_received():
    findings = compare_quantities(
        SKU, Decimal("20"), Decimal("20"), Decimal("21"), Decimal("150.00")
    )

    assert {item.type for item in findings} == {
        T.BILLED_EXCEEDS_RECEIVED,
        T.BILLED_EXCEEDS_ORDERED,
    }


def test_two_invoices_are_added_together():
    report = reconcile(
        order(),
        [invoice("15", document_id="INV-1"), invoice("15", document_id="INV-2")],
        [delivery()],
    )

    assert T.BILLED_EXCEEDS_RECEIVED in types(report)


# Exact SKU matching


def test_exact_match_is_case_sensitive():
    lines = (DeliveryLine("mon-24", "Monitor", Decimal("1")),)

    match = match_exact_sku(order().lines, lines)

    assert match.matched == ()
    assert match.unmatched == lines


def test_unknown_invoice_sku_is_reported_not_guessed():
    report = reconcile(order(), [invoice(sku="MON-27")], [delivery()])

    assert types(report) == {T.UNMATCHED_INVOICE_LINE, T.UNDER_BILLED}
    assert report.unsupported_amount == Decimal("0.00")


def test_unknown_delivery_sku_does_not_count_as_received():
    report = reconcile(order(), [invoice()], [delivery(sku="MON-27")])

    assert {T.UNMATCHED_DELIVERY_LINE, T.MISSING_DELIVERY} <= types(report)


# Document checks


def test_other_currency_is_flagged_and_not_converted():
    report = reconcile(order(), [invoice(currency="EUR", price="1.00")], [delivery()])

    assert types(report) == {T.CURRENCY_MISMATCH}
    assert report.billed_total is None
    assert report.unsupported_amount is None
    assert "currencies differ" in format_discrepancy_report(report)


def test_other_supplier_is_flagged():
    report = reconcile(order(), [invoice(supplier_id="SUP-OTHER")], [delivery()])

    assert types(report) == {T.SUPPLIER_MISMATCH}


def test_document_for_another_order_is_refused():
    with pytest.raises(ValueError, match="belongs to PO-2"):
        reconcile(order(), [invoice(purchase_order_id="PO-2")], [delivery()])


@pytest.mark.parametrize(
    "documents",
    [
        [delivery("20"), delivery("19")],
        [delivery("20"), delivery("5", revision=2), delivery("19")],
    ],
)
def test_conflicting_copies_of_a_revision_are_refused(documents):
    with pytest.raises(ValueError, match="different contents"):
        latest_revisions(documents)


def test_input_order_does_not_change_the_report():
    deliveries = [
        delivery("10", "DEL-2"),
        delivery("5", "DEL-1", revision=2),
        delivery("9", "DEL-1", revision=1),
    ]

    first = reconcile(order(), [invoice()], deliveries)
    second = reconcile(order(), [invoice()], list(reversed(deliveries)))

    assert first == second


# Schema guards


def test_float_money_is_rejected():
    with pytest.raises(TypeError):
        PricedLine(SKU, "Monitor", Decimal("1"), 150.0)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_invalid_amounts_are_rejected(value):
    with pytest.raises(ValueError):
        PricedLine(SKU, "Monitor", value, Decimal("1"))


def test_repeated_sku_in_one_document_is_rejected():
    line = DeliveryLine(SKU, "Monitor", Decimal("1"))

    with pytest.raises(ValueError, match="more than once"):
        DeliveryRecord("DEL-1", 1, "PO-1", (line, line))


# Independence


def test_rules_only_import_stdlib_and_shared_schemas():
    tree = ast.parse(Path(reconciliation_rules.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)

    allowed = {"__future__", "collections.abc", "dataclasses", "decimal", "typing"}
    project = {name for name in imported if name not in allowed}
    assert all(name.startswith("procurement_recon.schemas") for name in project)
