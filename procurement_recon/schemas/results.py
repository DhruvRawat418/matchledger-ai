"""Shared output schemas: what modules hand to each other after the documents.

There is deliberately no "approved" reconciliation status. Only a human can
make a decision, and that decision is recorded in ReviewDecision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class DiscrepancyType(str, Enum):
    MISSING_DELIVERY = "MISSING_DELIVERY"
    OVER_DELIVERY = "OVER_DELIVERY"
    BILLED_EXCEEDS_RECEIVED = "BILLED_EXCEEDS_RECEIVED"
    BILLED_EXCEEDS_ORDERED = "BILLED_EXCEEDS_ORDERED"
    UNDER_BILLED = "UNDER_BILLED"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    UNMATCHED_INVOICE_LINE = "UNMATCHED_INVOICE_LINE"
    UNMATCHED_DELIVERY_LINE = "UNMATCHED_DELIVERY_LINE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    SUPPLIER_MISMATCH = "SUPPLIER_MISMATCH"


class ReconciliationStatus(str, Enum):
    NO_DISCREPANCIES_PENDING_REVIEW = "NO_DISCREPANCIES_PENDING_REVIEW"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True)
class Discrepancy:
    """One finding. Amounts on separate discrepancies can overlap, so do not
    add them up; use the totals on ReconciliationReport instead."""

    type: DiscrepancyType
    message: str
    sku: str | None = None
    document_id: str | None = None
    expected: Decimal | None = None
    actual: Decimal | None = None
    amount: Decimal | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    """Totals are in the purchase order currency, and are None when an invoice
    uses another currency, because nothing is converted."""

    purchase_order_id: str
    purchase_order_revision: int
    status: ReconciliationStatus
    discrepancies: tuple[Discrepancy, ...]
    invoice_ids: tuple[str, ...]
    delivery_ids: tuple[str, ...]
    billed_total: Decimal | None
    supported_total: Decimal | None
    unsupported_amount: Decimal | None


@dataclass(frozen=True)
class ProductMatchSuggestion:
    """A suggested SKU link from the AI matcher (Member 4). It stays a
    suggestion until a human accepts it."""

    source_document_id: str
    source_sku: str
    suggested_po_sku: str
    confidence: float
    rationale: str


class ReviewOutcome(str, Enum):
    ACCEPTED_BY_HUMAN = "ACCEPTED_BY_HUMAN"
    REJECTED_BY_HUMAN = "REJECTED_BY_HUMAN"
    MORE_INFORMATION_NEEDED = "MORE_INFORMATION_NEEDED"


@dataclass(frozen=True)
class ReviewDecision:
    """A person's decision on a report. This records a review; it does not
    trigger or authorise any payment."""

    purchase_order_id: str
    reviewer: str
    outcome: ReviewOutcome
    notes: str = ""
