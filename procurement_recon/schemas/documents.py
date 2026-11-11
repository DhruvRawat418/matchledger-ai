"""Shared input schemas: the three document types every module agrees on.

Extraction (Member 2) produces these. Reconciliation (Member 3) consumes them.
Money and quantities must be Decimal; floats are rejected on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite number, got {value}")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_unique_skus(document_id: str, lines: tuple) -> None:
    skus = [line.sku for line in lines]
    duplicates = sorted({sku for sku in skus if skus.count(sku) > 1})
    if duplicates:
        raise ValueError(f"{document_id} lists a SKU more than once: {duplicates}")


@dataclass(frozen=True)
class PricedLine:
    """A purchase order or invoice line."""

    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal

    def __post_init__(self) -> None:
        _require_text("sku", self.sku)
        _require_non_negative_decimal("quantity", self.quantity)
        _require_non_negative_decimal("unit_price", self.unit_price)


@dataclass(frozen=True)
class DeliveryLine:
    """A delivery record line. Deliveries record quantities, not prices."""

    sku: str
    description: str
    quantity: Decimal

    def __post_init__(self) -> None:
        _require_text("sku", self.sku)
        _require_non_negative_decimal("quantity", self.quantity)


@dataclass(frozen=True)
class PurchaseOrder:
    document_id: str
    revision: int
    supplier_id: str
    currency: str
    lines: tuple[PricedLine, ...]

    def __post_init__(self) -> None:
        _validate_header(self.document_id, self.revision)
        _require_text("supplier_id", self.supplier_id)
        _require_text("currency", self.currency)
        object.__setattr__(self, "lines", tuple(self.lines))
        _require_unique_skus(self.document_id, self.lines)


@dataclass(frozen=True)
class SupplierInvoice:
    document_id: str
    revision: int
    purchase_order_id: str
    supplier_id: str
    currency: str
    lines: tuple[PricedLine, ...]

    def __post_init__(self) -> None:
        _validate_header(self.document_id, self.revision)
        _require_text("purchase_order_id", self.purchase_order_id)
        _require_text("supplier_id", self.supplier_id)
        _require_text("currency", self.currency)
        object.__setattr__(self, "lines", tuple(self.lines))
        _require_unique_skus(self.document_id, self.lines)


@dataclass(frozen=True)
class DeliveryRecord:
    document_id: str
    revision: int
    purchase_order_id: str
    lines: tuple[DeliveryLine, ...]

    def __post_init__(self) -> None:
        _validate_header(self.document_id, self.revision)
        _require_text("purchase_order_id", self.purchase_order_id)
        object.__setattr__(self, "lines", tuple(self.lines))
        _require_unique_skus(self.document_id, self.lines)


def _validate_header(document_id: str, revision: int) -> None:
    _require_text("document_id", document_id)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError(f"revision must be a whole number from 1, got {revision!r}")
