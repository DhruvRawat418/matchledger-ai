"""Version 0.1 team interface draft. No extraction, model calls or arithmetic here."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# JSON numbers are intentionally rejected: decimal strings preserve precision.
DecimalText = Annotated[str, StringConstraints(strict=True, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

class SourceEvidence(Contract):
    page: PositiveInt
    text: Identifier  # Visible source text, not a model explanation.

class LineItem(Contract):
    line_id: Identifier
    description: Identifier | None
    sku: Identifier | None = None
    quantity: DecimalText | None
    unit: Identifier | None
    unit_price: DecimalText | None
    evidence: list[SourceEvidence] = Field(min_length=1)

class ExtractedDocument(Contract):
    schema_version: Literal["0.1"] = "0.1"
    case_id: Identifier
    revision: PositiveInt  # Case input revision used for this output.
    document_id: Identifier
    document_type: Literal["purchase_order", "invoice", "receipt"]
    business_document_number: Identifier | None = None
    currency: Literal["USD"] | None
    line_items: list[LineItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_lines(self):
        ids = [line.line_id for line in self.line_items]
        if len(ids) != len(set(ids)):
            raise ValueError("line_id must be unique within a document")
        return self

class LineReference(Contract):
    document_id: Identifier
    line_id: Identifier

class MatchResult(Contract):
    case_id: Identifier
    revision: PositiveInt
    source: LineReference
    candidate_po_lines: list[LineReference]
    selected_po_line: LineReference | None
    decision: Literal["matched", "uncertain", "unmatched"]
    method: Literal["exact", "fuzzy", "llm", "human"]
    explanation: Identifier
    evidence: list[SourceEvidence] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_selection(self):
        if self.decision == "matched":
            if self.selected_po_line is None or self.selected_po_line not in self.candidate_po_lines:
                raise ValueError("A matched decision must select a supplied candidate")
        elif self.selected_po_line is not None:
            raise ValueError("Uncertain/unmatched decisions cannot select an accepted line")
        return self

class Discrepancy(Contract):
    code: Literal["quantity_mismatch", "price_mismatch", "missing_information", "unmatched_item", "unit_mismatch", "duplicate_document"]
    lines: list[LineReference] = Field(min_length=1)
    expected: DecimalText | None
    observed: DecimalText | None
    explanation: Identifier

class ReconciliationResult(Contract):
    case_id: Identifier
    revision: PositiveInt
    status: Literal["clean", "needs_review", "blocked"]
    discrepancies: list[Discrepancy]
    checked_lines: list[LineReference]

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "clean" and (self.discrepancies or not self.checked_lines):
            raise ValueError("Clean requires checked lines and no discrepancies")
        if self.status != "clean" and not self.discrepancies:
            raise ValueError("Non-clean results must explain the blocking/review issue")
        return self


def validate_handoff(documents: list[ExtractedDocument], matches: list[MatchResult]) -> None:
    """Reject stale/mixed cases and references to invented or non-PO lines.

    This validates interfaces only. It does not establish that a match is correct
    or all documents are complete. Those checks belong to the workers/reviewer.
    """
    if not documents:
        raise ValueError("At least one document is required")
    case_revision = {(d.case_id, d.revision) for d in documents}
    if len(case_revision) != 1:
        raise ValueError("Documents must share one case and revision")
    by_id = {d.document_id: d for d in documents}
    if len(by_id) != len(documents):
        raise ValueError("Duplicate document_id")
    valid = {(d.document_id, line.line_id) for d in documents for line in d.line_items}
    seen_sources = set()
    for m in matches:
        if (m.case_id, m.revision) not in case_revision:
            raise ValueError("Stale or cross-case match")
        source = (m.source.document_id, m.source.line_id)
        if source not in valid or by_id[m.source.document_id].document_type == "purchase_order":
            raise ValueError("Source must be an existing invoice or receipt line")
        if source in seen_sources:
            raise ValueError("Duplicate match decision for source line")
        seen_sources.add(source)
        for candidate in m.candidate_po_lines:
            if (candidate.document_id, candidate.line_id) not in valid or by_id[candidate.document_id].document_type != "purchase_order":
                raise ValueError("Candidate must be an existing purchase-order line")
