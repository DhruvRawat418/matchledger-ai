"""Deterministic reconciliation rules (Member 3).

Pure functions only: the same documents always give the same report. This
module imports only the standard library and the shared schemas, never LLM,
extraction or frontend code. It flags problems for a human and never approves
a payment.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

from procurement_recon.schemas.documents import (
    DeliveryLine,
    DeliveryRecord,
    PricedLine,
    PurchaseOrder,
    SupplierInvoice,
)
from procurement_recon.schemas.results import (
    Discrepancy,
    DiscrepancyType,
    ReconciliationReport,
    ReconciliationStatus,
)

ZERO = Decimal("0")

Document = TypeVar("Document", PurchaseOrder, SupplierInvoice, DeliveryRecord)
Line = TypeVar("Line", PricedLine, DeliveryLine)


@dataclass(frozen=True)
class SkuMatch:
    """Lines of a document split by whether their SKU is on the purchase order."""

    matched: tuple
    unmatched: tuple


def latest_revisions(documents: Iterable[Document]) -> tuple[Document, ...]:
    """Keep the highest revision of each document ID, sorted by ID.

    An identical resubmission collapses into one, so it cannot be counted
    twice. Two different documents claiming the same ID and revision raise
    ValueError, because picking one silently could hide a real change.
    """
    versions: dict[tuple[str, int], Document] = {}
    for document in documents:
        key = (document.document_id, document.revision)
        if key in versions and versions[key] != document:
            raise ValueError(
                f"{document.document_id} revision {document.revision} "
                "was submitted twice with different contents"
            )
        versions[key] = document

    latest: dict[str, Document] = {}
    for document in versions.values():
        current = latest.get(document.document_id)
        if current is None or document.revision > current.revision:
            latest[document.document_id] = document
    return tuple(latest[document_id] for document_id in sorted(latest))


def match_exact_sku(
    purchase_order_lines: Sequence[PricedLine], lines: Sequence[Line]
) -> SkuMatch:
    """Split lines by exact SKU, with no trimming or case folding."""
    ordered_skus = {line.sku for line in purchase_order_lines}
    return SkuMatch(
        matched=tuple(line for line in lines if line.sku in ordered_skus),
        unmatched=tuple(line for line in lines if line.sku not in ordered_skus),
    )


def compare_quantities(
    sku: str,
    ordered: Decimal,
    received: Decimal,
    billed: Decimal,
    unit_price: Decimal,
) -> tuple[Discrepancy, ...]:
    """Three-way check of ordered, received and billed quantities for one SKU.

    Amounts use the purchase order price, so a separate price difference is
    not mixed into a quantity finding.
    """
    findings: list[Discrepancy] = []

    if received < ordered:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.MISSING_DELIVERY,
                sku=sku,
                expected=ordered,
                actual=received,
                message=f"{sku}: ordered {ordered}, received {received}; "
                f"{ordered - received} missing",
            )
        )
    elif received > ordered:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.OVER_DELIVERY,
                sku=sku,
                expected=ordered,
                actual=received,
                message=f"{sku}: ordered {ordered}, received {received}; "
                f"{received - ordered} extra",
            )
        )

    if billed > received:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.BILLED_EXCEEDS_RECEIVED,
                sku=sku,
                expected=received,
                actual=billed,
                amount=(billed - received) * unit_price,
                message=f"{sku}: billed {billed}, received {received}",
            )
        )
    if billed > ordered:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.BILLED_EXCEEDS_ORDERED,
                sku=sku,
                expected=ordered,
                actual=billed,
                amount=(billed - ordered) * unit_price,
                message=f"{sku}: billed {billed}, ordered {ordered}",
            )
        )

    payable = min(ordered, received)
    if billed < payable:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.UNDER_BILLED,
                sku=sku,
                expected=payable,
                actual=billed,
                amount=(payable - billed) * unit_price,
                message=f"{sku}: billed {billed}, but {payable} "
                "were ordered and received",
            )
        )

    return tuple(findings)


def compare_unit_price(
    sku: str,
    expected_price: Decimal,
    invoiced_price: Decimal,
    quantity: Decimal,
    document_id: str,
    tolerance: Decimal = ZERO,
) -> Discrepancy | None:
    """Flag an invoice price further from the PO price than the tolerance.

    The check runs in both directions, so an undercharge is flagged as well.
    The tolerance is an absolute amount per unit and is zero by default.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("tolerance must be a non-negative Decimal")

    difference = invoiced_price - expected_price
    if abs(difference) <= tolerance:
        return None
    return Discrepancy(
        type=DiscrepancyType.PRICE_MISMATCH,
        sku=sku,
        document_id=document_id,
        expected=expected_price,
        actual=invoiced_price,
        amount=difference * quantity,
        message=f"{sku}: {document_id} charges {invoiced_price} per unit, "
        f"purchase order says {expected_price}",
    )


def reconcile(
    purchase_order: PurchaseOrder,
    invoices: Iterable[SupplierInvoice],
    deliveries: Iterable[DeliveryRecord],
    price_tolerance: Decimal = ZERO,
) -> ReconciliationReport:
    """Rebuild the full result for one purchase order from its documents.

    The caller passes every document it holds, and the result is worked out
    from scratch, so a later delivery or revision updates the outcome without
    any running total to get out of step. Pass the current purchase order
    revision. Documents for another purchase order raise ValueError.
    """
    current_invoices = latest_revisions(invoices)
    current_deliveries = latest_revisions(deliveries)
    _require_same_purchase_order(
        purchase_order, (*current_invoices, *current_deliveries)
    )

    discrepancies: list[Discrepancy] = []
    received: dict[str, Decimal] = {line.sku: ZERO for line in purchase_order.lines}
    billed: dict[str, Decimal] = {line.sku: ZERO for line in purchase_order.lines}
    unit_prices = {line.sku: line.unit_price for line in purchase_order.lines}

    for delivery in current_deliveries:
        match = match_exact_sku(purchase_order.lines, delivery.lines)
        for line in match.matched:
            received[line.sku] += line.quantity
        for line in match.unmatched:
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.UNMATCHED_DELIVERY_LINE,
                    sku=line.sku,
                    document_id=delivery.document_id,
                    actual=line.quantity,
                    message=f"{line.sku}: on {delivery.document_id} "
                    "but not on the purchase order",
                )
            )

    all_same_currency = True
    for invoice in current_invoices:
        same_currency = invoice.currency == purchase_order.currency
        all_same_currency = all_same_currency and same_currency
        discrepancies.extend(_check_invoice_header(purchase_order, invoice))

        match = match_exact_sku(purchase_order.lines, invoice.lines)
        for line in match.matched:
            billed[line.sku] += line.quantity
            if same_currency:
                finding = compare_unit_price(
                    line.sku,
                    unit_prices[line.sku],
                    line.unit_price,
                    line.quantity,
                    invoice.document_id,
                    price_tolerance,
                )
                if finding is not None:
                    discrepancies.append(finding)
        for line in match.unmatched:
            discrepancies.append(
                Discrepancy(
                    type=DiscrepancyType.UNMATCHED_INVOICE_LINE,
                    sku=line.sku,
                    document_id=invoice.document_id,
                    actual=line.quantity,
                    amount=line.quantity * line.unit_price,
                    message=f"{line.sku}: billed on {invoice.document_id} "
                    "but not on the purchase order",
                )
            )

    for line in purchase_order.lines:
        discrepancies.extend(
            compare_quantities(
                line.sku,
                line.quantity,
                received[line.sku],
                billed[line.sku],
                line.unit_price,
            )
        )

    billed_total = supported_total = unsupported_amount = None
    if all_same_currency:
        billed_total = sum(
            (
                line.quantity * line.unit_price
                for invoice in current_invoices
                for line in invoice.lines
            ),
            ZERO,
        )
        supported_total = sum(
            (
                min(line.quantity, received[line.sku]) * line.unit_price
                for line in purchase_order.lines
            ),
            ZERO,
        )
        unsupported_amount = billed_total - supported_total

    return ReconciliationReport(
        purchase_order_id=purchase_order.document_id,
        purchase_order_revision=purchase_order.revision,
        status=(
            ReconciliationStatus.NEEDS_REVIEW
            if discrepancies
            else ReconciliationStatus.NO_DISCREPANCIES_PENDING_REVIEW
        ),
        discrepancies=tuple(discrepancies),
        invoice_ids=tuple(invoice.document_id for invoice in current_invoices),
        delivery_ids=tuple(delivery.document_id for delivery in current_deliveries),
        billed_total=billed_total,
        supported_total=supported_total,
        unsupported_amount=unsupported_amount,
    )


def format_discrepancy_report(report: ReconciliationReport) -> str:
    """Render a report as plain text for a human reviewer."""
    lines = [
        f"Purchase order: {report.purchase_order_id} "
        f"(revision {report.purchase_order_revision})",
        f"Status: {report.status.value}",
        f"Invoices counted: {', '.join(report.invoice_ids) or 'none'}",
        f"Deliveries counted: {', '.join(report.delivery_ids) or 'none'}",
    ]
    if report.billed_total is None:
        lines.append("Totals: not calculated because currencies differ")
    else:
        lines.append(f"Billed total: {report.billed_total}")
        lines.append(f"Supported by order and delivery: {report.supported_total}")
        lines.append(f"Unsupported amount: {report.unsupported_amount}")

    if report.discrepancies:
        lines.append("Discrepancies:")
        lines.extend(
            f"- [{item.type.value}] {item.message}" for item in report.discrepancies
        )
    else:
        lines.append("Discrepancies: none found")
    lines.append("Human review is required. This report does not approve payment.")
    return "\n".join(lines)


def _require_same_purchase_order(
    purchase_order: PurchaseOrder,
    documents: Iterable[SupplierInvoice | DeliveryRecord],
) -> None:
    for document in documents:
        if document.purchase_order_id != purchase_order.document_id:
            raise ValueError(
                f"{document.document_id} belongs to {document.purchase_order_id}, "
                f"not {purchase_order.document_id}"
            )


def _check_invoice_header(
    purchase_order: PurchaseOrder, invoice: SupplierInvoice
) -> tuple[Discrepancy, ...]:
    findings: list[Discrepancy] = []
    if invoice.supplier_id != purchase_order.supplier_id:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.SUPPLIER_MISMATCH,
                document_id=invoice.document_id,
                message=f"{invoice.document_id} is from {invoice.supplier_id}, "
                f"purchase order is with {purchase_order.supplier_id}",
            )
        )
    if invoice.currency != purchase_order.currency:
        findings.append(
            Discrepancy(
                type=DiscrepancyType.CURRENCY_MISMATCH,
                document_id=invoice.document_id,
                message=f"{invoice.document_id} is in {invoice.currency}, "
                f"purchase order is in {purchase_order.currency}; "
                "prices were not compared",
            )
        )
    return tuple(findings)
