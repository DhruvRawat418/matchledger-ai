"""Document Extraction Agent (owner: Abdullah).

    outcome = await extract_document(request, client)

The model transcribes. Python then:
  * assigns line IDs and copies workflow IDs from the request (never from the model),
  * normalizes numbers, units and currency without guessing,
  * checks every value against the row the model quoted, and that quote against the
    PDF's text layer when there is one,
  * returns a contract-valid `ExtractedDocument` plus issues for staff.
"""
import re
import time

from pydantic import ValidationError

from src.contracts import ExtractedDocument
from src.extraction.client import ModelCallError, ModelClient
from src.extraction.normalize import (NotANumber, clean_text, comparable, currency_code, decimal_text,
                                      numbers_in, split_quantity_unit, unit_text)
from src.extraction.pages import Page, UnsupportedFile, load_pages
from src.extraction.prompt import PROMPT_VERSION, SYSTEM_PROMPT, output_schema, user_message
from src.extraction.schema import ExtractionIssue, ExtractionOutcome, ExtractionRequest, ModelDocument

MAX_ATTEMPTS = 3

# Text that reads like an instruction to the system rather than a product line.
# Flagging is a safeguard for staff, not the defense: the model's output schema has
# no field through which such text could change workflow decisions.
_INSTRUCTION_LIKE = re.compile(
    r"\b(ignore (all |the |any |previous |prior )*(instructions|rules|discrepanc\w*)"
    r"|approve (this |the )?(payment|invoice)|system prompt|api key|disregard)\b",
    re.IGNORECASE)


def _issue(code, message, severity="review", line_id=None, field=None):
    return ExtractionIssue(code=code, severity=severity, line_id=line_id, field=field, message=message)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


async def extract_document(request: ExtractionRequest, client: ModelClient,
                           pages: list[Page] | None = None,
                           max_attempts: int = MAX_ATTEMPTS) -> ExtractionOutcome:
    started = time.perf_counter()
    base = {"prompt_version": PROMPT_VERSION, "model_id": client.model_id}

    def finish(document, issues, attempts, usage, model_doc=None):
        return ExtractionOutcome(
            document=document, issues=issues, attempts=attempts, usage=usage,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            referenced_po_number=clean_text(model_doc.referenced_po_number) if model_doc else None,
            supplier_name=clean_text(model_doc.supplier_name) if model_doc else None, **base)

    if pages is None:
        try:
            pages = load_pages(request.file_path)
        except (UnsupportedFile, OSError) as exc:
            return finish(None, [_issue("unreadable_file", f"File could not be read ({type(exc).__name__})")], 0, {})

    usage: dict[str, int] = {}
    model_doc, last_error, attempts = None, None, 0
    for attempts in range(1, max_attempts + 1):
        try:
            reply = await client.complete(system=SYSTEM_PROMPT, user=user_message([p.text for p in pages]),
                                          pages=pages, json_schema=output_schema())
            for key, value in reply.usage.items():
                usage[key] = usage.get(key, 0) + value
            model_doc = ModelDocument.model_validate_json(_strip_fences(reply.text))
            break
        except (ModelCallError, ValidationError) as exc:
            # Never echo raw provider text or document content; only an adapter's
            # sanitized detail (HTTP status and provider error message).
            last_error = type(exc).__name__
            if getattr(exc, "safe_detail", None):
                last_error += f": {exc.safe_detail}"
    if model_doc is None:
        return finish(None, [_issue("extraction_failed",
                                    f"No usable model output after {attempts} attempt(s) ({last_error})")],
                      attempts, usage)

    document, issues = _build(request, model_doc, pages)
    return finish(document, issues, attempts, usage, model_doc)


def _build(request: ExtractionRequest, model: ModelDocument, pages: list[Page]):
    issues: list[ExtractionIssue] = []

    # Document type: the uploader's choice wins, but a disagreement is shown to staff.
    doc_type = request.declared_type or model.document_type
    if request.declared_type and model.document_type and model.document_type != request.declared_type:
        issues.append(_issue("document_type_conflict",
                             f"Uploaded as {request.declared_type} but reads as {model.document_type}"))
    if doc_type is None:
        return None, [*issues, _issue("document_type_unknown", "Document type could not be determined")]
    if not model.line_items:
        return None, [*issues, _issue("no_line_items", "No product lines were found")]

    page_text = {p.number: p.text for p in pages}
    unverified = 0
    lines = []
    for number, row in enumerate(model.line_items, start=1):
        line_id = str(number)
        evidence = clean_text(row.evidence_text)
        quoted = comparable(evidence)

        # --- values, normalized without guessing
        qty_raw, unit_raw = split_quantity_unit(row.quantity, row.unit)
        values = {}
        for field, raw in (("quantity", qty_raw), ("unit_price", row.unit_price)):
            try:
                values[field] = decimal_text(raw)
            except NotANumber:
                values[field] = None
                issues.append(_issue("invalid_number", f"Printed {field} is not a nonnegative number",
                                     line_id=line_id, field=field))
        quantity, price = values["quantity"], values["unit_price"]
        unit = unit_text(unit_raw)
        description, sku = clean_text(row.description), clean_text(row.sku)

        if quantity is None and not any(i.line_id == line_id and i.field == "quantity" for i in issues):
            issues.append(_issue("missing_value", "Quantity is not printed or not legible",
                                 line_id=line_id, field="quantity"))
        if unit is None:
            issues.append(_issue("unit_missing", "No unit printed; left empty rather than assumed",
                                 line_id=line_id, field="unit"))
        if price is None and doc_type != "receipt" and not any(
                i.line_id == line_id and i.field == "unit_price" for i in issues):
            issues.append(_issue("missing_value", "Unit price is not printed or not legible",
                                 line_id=line_id, field="unit_price"))
        if description is None and sku is None:
            issues.append(_issue("missing_value", "Line has neither a description nor an item code",
                                 line_id=line_id, field="description"))

        # --- every value must be visible in the quoted row
        quoted_numbers = numbers_in(evidence)
        for field, value in (("quantity", quantity), ("unit_price", price)):
            if value is not None and value not in quoted_numbers:
                issues.append(_issue("value_not_in_evidence", f"{field} does not appear in the quoted row",
                                     line_id=line_id, field=field))
        for field, value in (("sku", sku), ("description", description)):
            if value is not None and comparable(value) not in quoted:
                issues.append(_issue("value_not_in_evidence", f"{field} does not appear in the quoted row",
                                     line_id=line_id, field=field))

        # --- the quoted row must be on the cited page
        if row.evidence_page not in page_text:
            issues.append(_issue("evidence_page_invalid", f"Cites page {row.evidence_page}, "
                                 f"document has {len(pages)}", line_id=line_id, field="evidence"))
        elif page_text[row.evidence_page] is None:
            unverified += 1
        elif not _on_page(quoted, page_text[row.evidence_page]):
            issues.append(_issue("evidence_not_found", "Quoted row is not in the page's text",
                                 line_id=line_id, field="evidence"))

        if _INSTRUCTION_LIKE.search(evidence) or (description and _INSTRUCTION_LIKE.search(description)):
            issues.append(_issue("instruction_like_text",
                                 "Line contains text that reads like an instruction; treated as data only",
                                 line_id=line_id, field="description"))

        lines.append({"line_id": line_id, "description": description, "sku": sku, "quantity": quantity,
                      "unit": unit, "unit_price": price,
                      "evidence": [{"page": row.evidence_page, "text": evidence}]})

    if unverified:
        issues.append(_issue("evidence_unverified", f"{unverified} line(s) come from pages without a "
                             "text layer; quotes could not be checked against the page", severity="info"))

    # --- header fields
    number = clean_text(model.business_document_number)
    if number is None:
        issues.append(_issue("missing_value", "Document number not found; duplicate checks rely on it",
                             field="business_document_number"))
    supplier_id = None
    supplier_name = clean_text(model.supplier_name)
    needed = "review" if doc_type != "receipt" else "info"
    if supplier_name is None:
        issues.append(_issue("missing_value", "Supplier name not found", needed, field="supplier_id"))
    else:
        directory = {comparable(name): sid for name, sid in request.supplier_directory.items()}
        supplier_id = directory.get(comparable(supplier_name))
        if supplier_id is None:
            issues.append(_issue("supplier_unresolved", "Supplier name is not in the supplier list",
                                 needed, field="supplier_id"))

    currency = None
    if doc_type != "receipt":  # Receipts carry no prices; currency stays unknown, never inferred.
        currency = currency_code(model.currency)
        if currency is None:
            issues.append(_issue("currency_unknown" if model.currency is None else "unsupported_currency",
                                 "Currency must be confirmed as USD before monetary checks",
                                 field="currency"))

    try:
        document = ExtractedDocument(
            case_id=request.case_id, revision=request.revision, document_id=request.document_id,
            document_type=doc_type, business_document_number=number, supplier_id=supplier_id,
            purchase_order_id=None,  # Linked by intake from referenced_po_number, never by the model.
            currency=currency, line_items=lines)
    except ValidationError as exc:
        return None, [*issues, _issue("contract_validation_failed",
                                      f"Output did not meet the shared contract ({exc.error_count()} error(s))")]
    return document, issues


def _on_page(quoted: str, page: str) -> bool:
    """Exact quote, or every word of it, on the page. Table cells often come out of
    a PDF text layer in a different order, so the word check avoids false alarms."""
    page = comparable(page)
    return quoted in page or set(quoted.split()) <= set(page.split())
