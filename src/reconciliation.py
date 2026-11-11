"""Deterministic three-way reconciliation worker.

Business rules are documented in docs/RECONCILIATION_RULES.md; rule IDs (R1..R10)
in this file refer to that document. No AI calls, no floats: every quantity and
price is compared as an exact Decimal, and missing values are never zero.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from src.contracts import (
    Discrepancy,
    ExtractedDocument,
    LineItem,
    LineReference,
    MatchResult,
    ReconciliationResult,
    validate_handoff,
)

# R6: spellings that all mean "one item". Anything else must match exactly
# (case- and whitespace-insensitive).
UNIT_ALIASES = {alias: "each" for alias in ("each", "ea", "unit", "units", "pc", "pcs", "piece", "pieces")}

SourceLine = tuple[ExtractedDocument, LineItem]


class TraceStep(BaseModel):
    """One reproducible step of the calculation. Local to this worker (not a shared contract)."""

    model_config = ConfigDict(extra="forbid")
    step: int
    rule: str
    lines: list[LineReference]
    inputs: dict[str, str | None]
    outcome: str


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: ReconciliationResult
    trace: list[TraceStep]


# --------------------------------------------------------------------------- helpers

def _ref(doc: ExtractedDocument, line: LineItem) -> LineReference:
    return LineReference(document_id=doc.document_id, line_id=line.line_id)


def _unique(lines: Iterable[LineReference]) -> list[LineReference]:
    seen, out = set(), []
    for ref in lines:
        key = (ref.document_id, ref.line_id)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def _dec(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _norm_text(text: str | None) -> str | None:
    return None if text is None else " ".join(text.casefold().split())


def _norm_sku(sku: str | None) -> str | None:
    return None if sku is None else "".join(ch for ch in sku.casefold() if ch.isalnum())


def _norm_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    text = " ".join(unit.casefold().split()).rstrip(".")
    return UNIT_ALIASES.get(text, text)


def _canonical_number(text: str | None) -> str:
    return "" if text is None else str(Decimal(text).normalize())


def _fingerprint(doc: ExtractedDocument) -> tuple:
    """Content of a document, ignoring line IDs and harmless formatting (R2)."""
    lines = sorted(
        (
            _norm_sku(line.sku) or "",
            _norm_text(line.description) or "",
            _canonical_number(line.quantity),
            _norm_unit(line.unit) or "",
            _canonical_number(line.unit_price),
        )
        for line in doc.line_items
    )
    return (doc.currency or "", tuple(lines))


def _all_refs(docs: Iterable[ExtractedDocument]) -> list[LineReference]:
    return [_ref(doc, line) for doc in docs for line in doc.line_items]


# --------------------------------------------------------------------------- engine

class _Run:
    def __init__(self) -> None:
        self.discrepancies: list[Discrepancy] = []
        self.blocking = False
        self.trace: list[TraceStep] = []

    def note(self, rule: str, lines: Iterable[LineReference], inputs: dict[str, str | None], outcome: str) -> None:
        self.trace.append(TraceStep(step=len(self.trace) + 1, rule=rule, lines=_unique(lines), inputs=inputs, outcome=outcome))

    def flag(self, rule: str, code: str, lines: Iterable[LineReference], explanation: str, *,
             blocking: bool, expected: Decimal | None = None, observed: Decimal | None = None) -> None:
        lines = _unique(lines)
        discrepancy = Discrepancy(code=code, lines=lines, expected=_fmt(expected), observed=_fmt(observed), explanation=explanation)
        self.discrepancies.append(discrepancy)
        self.blocking = self.blocking or blocking
        self.note(rule, lines, {"expected": discrepancy.expected, "observed": discrepancy.observed},
                  f"FLAG {code} ({'blocking' if blocking else 'review'}): {explanation}")


def _exclude_duplicates(run: _Run, documents: list[ExtractedDocument]) -> set[str]:
    """R2. Returns the IDs of documents excluded from all totals."""
    groups: dict[tuple[str, str], list[ExtractedDocument]] = defaultdict(list)
    for doc in documents:
        if doc.business_document_number is not None:
            groups[(doc.document_type, _norm_text(doc.business_document_number))].append(doc)

    excluded: set[str] = set()
    for (doc_type, number), group in groups.items():
        if len(group) < 2:
            continue
        ids = [d.document_id for d in group]
        fingerprints = {_fingerprint(d) for d in group}
        if len(fingerprints) == 1:
            kept, copies = group[0], group[1:]
            for copy in copies:
                excluded.add(copy.document_id)
                run.flag("R2", "duplicate_document", _all_refs([copy]),
                         f"{doc_type} {number} uploaded more than once with identical contents; "
                         f"{copy.document_id} excluded from totals, {kept.document_id} kept.",
                         blocking=False)
        else:
            excluded.update(ids)
            run.flag("R2", "duplicate_document", _all_refs(group),
                     f"{doc_type} {number} appears in {len(group)} documents ({', '.join(ids)}) with different "
                     "contents; cannot tell which version is current, so all are excluded until resolved.",
                     blocking=True)
    return excluded


def reconcile_with_trace(documents: list[ExtractedDocument], matches: list[MatchResult]) -> ReconciliationReport:
    # R1: interface check; raises ValueError for stale/invalid handoffs.
    validate_handoff(documents, matches)
    case_id, revision = documents[0].case_id, documents[0].revision
    run = _Run()
    run.note("R1", [], {"documents": str(len(documents)), "matches": str(len(matches))}, "Handoff valid.")

    excluded = _exclude_duplicates(run, documents)
    active = [d for d in documents if d.document_id not in excluded]
    po_docs = [d for d in active if d.document_type == "purchase_order"]
    source_docs = [d for d in active if d.document_type != "purchase_order"]
    po_lines: dict[tuple[str, str], SourceLine] = {
        (d.document_id, l.line_id): (d, l) for d in po_docs for l in d.line_items
    }

    # R5: a three-way match needs a PO and an invoice.
    if not po_docs:
        run.flag("R5", "missing_information", _all_refs(source_docs) or _all_refs(documents),
                 "No usable purchase order in this case; nothing to reconcile against.", blocking=True)
    if not any(d.document_type == "invoice" for d in active):
        run.flag("R5", "missing_information", _all_refs(po_docs) or _all_refs(documents),
                 "No usable invoice in this case; nothing to approve for payment.", blocking=True)

    # R3/R4: route each matched invoice/receipt line to its PO line.
    match_by_source = {(m.source.document_id, m.source.line_id): m for m in matches}
    invoiced_by_po: dict[tuple[str, str], list[SourceLine]] = defaultdict(list)
    received_by_po: dict[tuple[str, str], list[SourceLine]] = defaultdict(list)
    for doc in source_docs:
        for line in doc.line_items:
            src = _ref(doc, line)
            match = match_by_source.get((doc.document_id, line.line_id))
            if match is None:
                run.flag("R3", "unmatched_item", [src],
                         f"{doc.document_type} line {line.line_id} has no matching decision; excluded from totals.",
                         blocking=False)
                continue
            if match.decision != "matched":
                run.flag("R3", "unmatched_item", [src],
                         f"{doc.document_type} line {line.line_id} match is '{match.decision}'; excluded from totals "
                         "until a PO line is confirmed.", blocking=False)
                continue
            po_key = (match.selected_po_line.document_id, match.selected_po_line.line_id)
            if po_key not in po_lines:
                run.flag("R3", "unmatched_item", [src, match.selected_po_line],
                         f"{doc.document_type} line {line.line_id} is matched to a PO line from an excluded document.",
                         blocking=False)
                continue
            po_doc, po_line = po_lines[po_key]
            if line.sku and po_line.sku and _norm_sku(line.sku) != _norm_sku(po_line.sku):
                run.flag("R4", "unmatched_item", [src, _ref(po_doc, po_line)],
                         f"Matched {doc.document_type} line {line.line_id} has SKU {line.sku} but PO line "
                         f"{po_line.line_id} has SKU {po_line.sku}; match rejected and excluded from totals.",
                         blocking=False)
                continue
            target = invoiced_by_po if doc.document_type == "invoice" else received_by_po
            target[po_key].append((doc, line))

    for po_key, (po_doc, po_line) in po_lines.items():
        _check_po_line(run, po_doc, po_line, invoiced_by_po[po_key], received_by_po[po_key])

    if not run.discrepancies:
        status = "clean"
    elif run.blocking:
        status = "blocked"
    else:
        status = "needs_review"
    run.note("R10", [], {"discrepancies": str(len(run.discrepancies))}, f"Status: {status}.")

    result = ReconciliationResult(case_id=case_id, revision=revision, status=status,
                                  discrepancies=run.discrepancies, checked_lines=_all_refs(active))
    return ReconciliationReport(result=result, trace=run.trace)


def reconcile(documents: list[ExtractedDocument], matches: list[MatchResult]) -> ReconciliationResult:
    """Agreed team interface: returns only the shared ReconciliationResult."""
    return reconcile_with_trace(documents, matches).result


def _total(run: _Run, label: str, po_ref: LineReference, lines: list[SourceLine]) -> Decimal | None:
    """R7: sum quantities; unknown (None) if any contributing quantity is missing."""
    refs = [_ref(d, l) for d, l in lines]
    if not lines:
        run.note("R7", [po_ref], {label: "0"}, f"No {label} lines matched; {label} total is 0.")
        return Decimal(0)
    missing = [_ref(d, l) for d, l in lines if l.quantity is None]
    if missing:
        run.flag("R7", "missing_information", [po_ref, *missing],
                 f"{label.capitalize()} quantity is unreadable on {len(missing)} line(s); "
                 f"{label} total is unknown (not treated as zero).", blocking=True)
        return None
    parts = [l.quantity for _, l in lines]
    total = sum((Decimal(p) for p in parts), Decimal(0))
    run.note("R7", [po_ref, *refs], {f"{label}_parts": " + ".join(parts)},
             f"{label} total = {' + '.join(parts)} = {_fmt(total)}")
    return total


def _check_po_line(run: _Run, po_doc: ExtractedDocument, po_line: LineItem,
                   invoice_lines: list[SourceLine], receipt_lines: list[SourceLine]) -> None:
    po_ref = _ref(po_doc, po_line)
    contributors = invoice_lines + receipt_lines
    if not contributors:
        run.note("R7", [po_ref], {"ordered": po_line.quantity}, "Open PO line: nothing invoiced or received yet; no checks needed.")
        return

    # R6: units must agree before anything is compared.
    units_ok = True
    missing_unit = [_ref(d, l) for d, l in contributors if l.unit is None]
    if po_line.unit is None or missing_unit:
        units_ok = False
        run.flag("R6", "missing_information", [po_ref, *missing_unit] if missing_unit else [po_ref, *(_ref(d, l) for d, l in contributors)],
                 f"Unit of measure is missing for PO line {po_line.line_id} or its matched lines; "
                 "quantities and prices cannot be compared.", blocking=True)
    else:
        po_unit = _norm_unit(po_line.unit)
        for doc, line in contributors:
            if _norm_unit(line.unit) != po_unit:
                units_ok = False
                run.flag("R6", "unit_mismatch", [po_ref, _ref(doc, line)],
                         f"PO line {po_line.line_id} is in '{po_line.unit}' but {doc.document_type} line "
                         f"{line.line_id} is in '{line.unit}'; quantities and prices cannot be compared.",
                         blocking=True)
    if not units_ok:
        run.note("R6", [po_ref], {}, "Quantity and price checks skipped for this PO line: units not comparable.")
        return
    run.note("R6", [po_ref, *(_ref(d, l) for d, l in contributors)], {"unit": po_line.unit}, "Units agree.")

    # R7: totals.
    ordered = _dec(po_line.quantity)
    if ordered is None:
        run.flag("R7", "missing_information", [po_ref],
                 f"Ordered quantity is unreadable on PO line {po_line.line_id} (not treated as zero).", blocking=True)
    invoiced = _total(run, "invoiced", po_ref, invoice_lines)
    received = _total(run, "received", po_ref, receipt_lines)
    inv_refs = [_ref(d, l) for d, l in invoice_lines]
    rec_refs = [_ref(d, l) for d, l in receipt_lines]

    # R8: quantity comparisons, only when both sides are known.
    def compare(name, left_label, left, right_label, right, lines, explanation):
        if left is None or right is None:
            run.note("R8", lines, {left_label: _fmt(left), right_label: _fmt(right)}, f"{name}: skipped, a total is unknown.")
            return
        if right > left:
            run.flag("R8", "quantity_mismatch", lines, explanation.format(left=_fmt(left), right=_fmt(right),
                     diff=_fmt(right - left)), blocking=False, expected=left, observed=right)
        else:
            run.note("R8", lines, {left_label: _fmt(left), right_label: _fmt(right)},
                     f"{name}: {right_label} {_fmt(right)} <= {left_label} {_fmt(left)}: OK.")

    compare("Over-billing vs order", "ordered", ordered, "invoiced", invoiced, [po_ref, *inv_refs],
            "Invoiced {right} exceeds ordered {left} by {diff}.")
    compare("Over-delivery", "ordered", ordered, "received", received, [po_ref, *rec_refs],
            "Received {right} exceeds ordered {left} by {diff}.")
    # Billing for goods not received: expected = invoiced, observed = received (team fixture convention).
    if invoiced is None or received is None:
        run.note("R8", [po_ref, *inv_refs, *rec_refs], {"invoiced": _fmt(invoiced), "received": _fmt(received)},
                 "Billed vs received: skipped, a total is unknown.")
    elif invoiced > received:
        run.flag("R8", "quantity_mismatch", [po_ref, *inv_refs, *rec_refs],
                 f"Invoiced {_fmt(invoiced)} but only {_fmt(received)} received "
                 f"(ordered {_fmt(ordered) or 'unknown'}); {_fmt(invoiced - received)} unit(s) billed without receipt.",
                 blocking=False, expected=invoiced, observed=received)
    else:
        run.note("R8", [po_ref, *inv_refs, *rec_refs], {"invoiced": _fmt(invoiced), "received": _fmt(received)},
                 f"Billed vs received: invoiced {_fmt(invoiced)} <= received {_fmt(received)}: OK.")
    if ordered is not None and received is not None and received < ordered:
        run.note("R8", [po_ref], {"ordered": _fmt(ordered), "received": _fmt(received)},
                 f"Partial delivery: {_fmt(ordered - received)} still open on PO line {po_line.line_id}.")

    # R9: unit prices, invoice vs PO.
    for inv_doc, inv_line in invoice_lines:
        inv_ref = _ref(inv_doc, inv_line)
        if po_doc.currency is None or inv_doc.currency is None:
            run.flag("R9", "missing_information", [po_ref, inv_ref],
                     f"Currency unknown on {'PO' if po_doc.currency is None else 'invoice'}; price cannot be checked.",
                     blocking=True)
            continue
        po_price, inv_price = _dec(po_line.unit_price), _dec(inv_line.unit_price)
        if po_price is None or inv_price is None:
            run.flag("R9", "missing_information", [po_ref, inv_ref],
                     f"Unit price unreadable on {'PO' if po_price is None else 'invoice'} line; price cannot be checked "
                     "(not treated as zero).", blocking=True)
            continue
        if inv_price != po_price:
            run.flag("R9", "price_mismatch", [po_ref, inv_ref],
                     f"Invoice line {inv_line.line_id} unit price {inv_line.unit_price} differs from PO price "
                     f"{po_line.unit_price} by {_fmt(inv_price - po_price)} {po_doc.currency}.",
                     blocking=False, expected=po_price, observed=inv_price)
        else:
            run.note("R9", [po_ref, inv_ref], {"po_price": po_line.unit_price, "invoice_price": inv_line.unit_price},
                     "Unit price agrees.")
