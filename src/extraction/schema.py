"""Shapes used inside the extraction component.

`ModelDocument` is what the model is allowed to return: a transcription of what is
printed, never workflow IDs. Python turns it into the shared `ExtractedDocument`
contract (src/contracts.py) after normalization and evidence checks.
"""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import ExtractedDocument, Identifier, PositiveInt

DocumentType = Literal["purchase_order", "invoice", "receipt"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- model output

class ModelLine(_Strict):
    """One printed product line, values copied as printed (not normalized)."""
    description: str | None
    sku: str | None
    quantity: str | None
    unit: str | None
    unit_price: str | None
    evidence_page: PositiveInt
    evidence_text: Identifier  # The printed row, copied verbatim.


class ModelDocument(_Strict):
    document_type: DocumentType | None
    business_document_number: str | None
    supplier_name: str | None
    referenced_po_number: str | None  # PO number printed on an invoice/receipt.
    currency: str | None              # As printed: "USD", "US$", "$", or null.
    line_items: list[ModelLine]


# ---------------------------------------------------------------- request/outcome

class ExtractionRequest(_Strict):
    """Created by intake (Dhruv). The model never generates these IDs."""
    case_id: Identifier
    revision: PositiveInt
    document_id: Identifier
    file_path: Path
    # Document type chosen at upload, if known. The model's reading is checked against it.
    declared_type: DocumentType | None = None
    # Printed supplier name -> internal supplier ID. Unknown names stay unresolved.
    supplier_directory: dict[str, Identifier] = Field(default_factory=dict)


Severity = Literal["review", "info"]


class ExtractionIssue(_Strict):
    """Something staff should see. `review` issues must be resolved by a person."""
    code: Identifier
    severity: Severity
    line_id: str | None = None
    field: str | None = None
    message: Identifier


class ExtractionOutcome(_Strict):
    document: ExtractedDocument | None  # None when extraction failed.
    issues: list[ExtractionIssue]
    referenced_po_number: str | None = None  # For intake to resolve purchase_order_id.
    supplier_name: str | None = None
    prompt_version: Identifier
    model_id: Identifier
    attempts: int
    latency_ms: float
    usage: dict[str, int] = Field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return self.document is None or any(i.severity == "review" for i in self.issues)
