"""Translate validated graph contracts to Matthew Suruadji's original engine.

Phase 1 supports exact SKU matches only (after the normalization below); no fuzzy
or AI matching here. No model decisions or arithmetic here: the engine computes.
"""
from collections import defaultdict
from decimal import Decimal
from src.contracts import ExtractedDocument, MatchResult, validate_handoff, ReconciliationResult
from procurement_recon.schemas.documents import PurchaseOrder, SupplierInvoice, DeliveryRecord, PricedLine, DeliveryLine
from procurement_recon.reconciliation.reconciliation_rules import reconcile

# Explicit unit equivalences. Anything not listed must match exactly (ignoring
# letter case and a trailing period); there is no unit conversion.
UNIT_ALIASES = {'each': 'each', 'ea': 'each', 'pc': 'each', 'pcs': 'each', 'piece': 'each', 'pieces': 'each'}


def normalize_sku(sku):
    """Exact product code, ignoring letter case and surrounding spaces. Inner characters still count."""
    return None if sku is None else sku.strip().upper()


def normalize_unit(unit):
    if unit is None:
        return None
    text = unit.strip().casefold().removesuffix('.')
    return UNIT_ALIASES.get(text, text)


def _receipt_number(doc):
    number = doc.business_document_number
    return None if number is None else number.casefold()


def _receipt_content(doc):
    """What a receipt says was delivered, ignoring line IDs and number formatting."""
    return tuple(sorted((normalize_sku(line.sku) or '',
                         str(Decimal(line.quantity).normalize()) if line.quantity is not None else '',
                         normalize_unit(line.unit) or '') for line in doc.line_items))


def screen_receipts(receipts):
    """Decide which receipts count as separate deliveries before any quantity is added up.

    Returns (counted, repeats, conflict):
    - counted: receipts treated as distinct deliveries, in input order.
    - repeats: (repeat, original, verified) for receipts counted only once. `verified` is True when
      both carry the same receipt number and identical lines; False when the lines are identical but
      at least one receipt has no number, so we cannot prove they are the same delivery.
    - conflict: (first, second) when two receipts share a number but list different lines. We cannot
      tell whether one replaces the other, so the caller must block rather than guess.
    Receipts with different numbers, or with different lines, are separate deliveries.
    """
    counted, repeats = [], []
    for receipt in receipts:
        number, content = _receipt_number(receipt), _receipt_content(receipt)
        for kept in counted:
            if number is not None and number == _receipt_number(kept) and content != _receipt_content(kept):
                return counted, repeats, (kept, receipt)
        original = next((kept for kept in counted if content == _receipt_content(kept) and (
            number is None or _receipt_number(kept) is None or number == _receipt_number(kept))), None)
        if original is None:
            counted.append(receipt)
        else:
            repeats.append((receipt, original, number is not None and number == _receipt_number(original)))
    return counted, repeats, None


def _ref(doc, line):
    return {'document_id': doc.document_id, 'line_id': line.line_id}


def _where(doc, line=None):
    label = {'purchase_order': 'PO', 'invoice': 'invoice', 'receipt': 'receipt'}[doc.document_type]
    return f'{label} {doc.document_id}' + (f' line {line.line_id}' if line is not None else '')


def _issue(code, lines, explanation):
    return {'code': code, 'lines': lines, 'expected': None, 'observed': None, 'explanation': explanation}


def _input_issues(docs, matches, po, invoices, receipts):
    """Blocking problems, in stages. Each issue names its exact document and line.

    A later stage only runs when the earlier ones pass, so it never reasons about unusable data.
    """
    by_ref = {(d.document_id, l.line_id): (d, l) for d in docs for l in d.line_items}

    # Stage 1: document headers.
    issues = []
    for d in [po, *invoices]:
        if d.currency != 'USD':
            issues.append(_issue('missing_information', [_ref(d, l) for l in d.line_items],
                                 f'{_where(d)}: currency is {d.currency or "unknown"}; USD is required.'))
        if d.supplier_id is None:
            issues.append(_issue('missing_information', [_ref(d, l) for l in d.line_items],
                                 f'{_where(d)}: supplier ID is missing.'))
    for d in [*invoices, *receipts]:
        if d.purchase_order_id != po.document_id:
            issues.append(_issue('missing_information', [_ref(d, l) for l in d.line_items],
                                 f'{_where(d)}: linked to PO {d.purchase_order_id or "none"}, '
                                 f'not to the active PO {po.document_id}.'))
    if issues:
        return issues

    # Stage 2: line fields, then products listed twice where that is ambiguous.
    for d in docs:
        for l in d.line_items:
            missing = [name for name, value in [('SKU', l.sku), ('quantity', l.quantity), ('unit', l.unit)] if value is None]
            if d.document_type != 'receipt' and l.unit_price is None:
                missing.append('unit price')
            if missing:
                issues.append(_issue('missing_information', [_ref(d, l)],
                                     f'{_where(d, l)}: missing {", ".join(missing)} (not treated as zero).'))
    if issues:
        return issues
    for d in [po, *receipts]:
        lines_by_sku = defaultdict(list)
        for l in d.line_items:
            lines_by_sku[normalize_sku(l.sku)].append(l)
        for sku, lines in lines_by_sku.items():
            if len(lines) > 1:
                ids = ', '.join(l.line_id for l in lines)
                why = ('invoice and receipt lines cannot be assigned to a single PO line' if d.document_type == 'purchase_order'
                       else 'repeated products on one receipt are not supported yet; combine or correct the lines')
                issues.append(_issue('unmatched_item', [_ref(d, l) for l in lines],
                                     f'{_where(d)} lists product {sku} on lines {ids}; {why}.'))
    if issues:
        return issues

    # Stage 3: every invoice/receipt line needs an accepted exact match to a PO line with the same product and unit.
    decided = {(m.source.document_id, m.source.line_id) for m in matches}
    for d in [*invoices, *receipts]:
        for l in d.line_items:
            if (d.document_id, l.line_id) not in decided:
                issues.append(_issue('unmatched_item', [_ref(d, l)], f'{_where(d, l)}: no match decision.'))
    for m in matches:
        src_doc, src = by_ref[(m.source.document_id, m.source.line_id)]
        if m.decision != 'matched' or m.method != 'exact':
            issues.append(_issue('unmatched_item', [_ref(src_doc, src)],
                                 f'{_where(src_doc, src)}: match is {m.decision} ({m.method}); this adapter accepts '
                                 'confirmed exact matches only.'))
            continue
        po_doc, target = by_ref[(m.selected_po_line.document_id, m.selected_po_line.line_id)]
        if normalize_sku(src.sku) != normalize_sku(target.sku):
            issues.append(_issue('unmatched_item', [_ref(src_doc, src), _ref(po_doc, target)],
                                 f'{_where(src_doc, src)} has SKU {src.sku}, but its selected {_where(po_doc, target)} '
                                 f'has SKU {target.sku}.'))
        elif normalize_unit(src.unit) != normalize_unit(target.unit):
            issues.append(_issue('unit_mismatch', [_ref(src_doc, src), _ref(po_doc, target)],
                                 f'{_where(src_doc, src)} is in "{src.unit}" but {_where(po_doc, target)} is in '
                                 f'"{target.unit}"; units are not converted.'))
    return issues


async def reconcile_worker(state):
    docs = [ExtractedDocument.model_validate(d) for d in state['documents']]
    matches = [MatchResult.model_validate(m) for m in state['matches']]
    validate_handoff(docs, matches)
    if any((d.case_id, d.revision) != (state['case_id'], state['revision']) for d in docs):
        raise ValueError('Stale reconciliation input')
    refs = [_ref(d, l) for d in docs for l in d.line_items]

    def blocked(issues):
        return ReconciliationResult.model_validate({
            'case_id': state['case_id'], 'revision': state['revision'], 'status': 'blocked',
            'checked_lines': [], 'discrepancies': issues}).model_dump()

    orders = [d for d in docs if d.document_type == 'purchase_order']
    invoices = [d for d in docs if d.document_type == 'invoice']
    receipts = [d for d in docs if d.document_type == 'receipt']
    if len(orders) != 1 or len(invoices) != 1 or not 1 <= len(receipts) <= 3:
        return blocked([_issue('missing_information', refs,
            f'Expected one PO, one invoice and one to three active receipts; got {len(orders)} PO, '
            f'{len(invoices)} invoice and {len(receipts)} receipt document(s).')])
    po = orders[0]
    issues = _input_issues(docs, matches, po, invoices, receipts)
    if issues:
        return blocked(issues)

    # A repeated upload is not a new delivery. Screen receipts before the engine adds anything up.
    counted_receipts, repeats, conflict = screen_receipts(receipts)
    if conflict:
        first, second = conflict
        return blocked([_issue('duplicate_document', [_ref(d, l) for d in conflict for l in d.line_items],
            f'[CONFLICTING_RECEIPTS] Receipts {first.document_id} and {second.document_id} share receipt number '
            f'{first.business_document_number} but list different lines. Cannot tell whether one replaces the '
            'other; neither is counted until a person confirms which is current.')])
    review = []
    for repeat, original, verified in repeats:
        reason = (f'same receipt number {repeat.business_document_number} and identical lines' if verified else
                  'identical lines and no receipt number to tell them apart; confirm if they are separate deliveries')
        review.append({**_issue('duplicate_document', [_ref(d, l) for d in (original, repeat) for l in d.line_items],
            f"[{'REPEATED_RECEIPT' if verified else 'POSSIBLE_REPEATED_RECEIPT'}] "
            f'{repeat.document_id} repeats {original.document_id} ({reason}); counted once.'),
            'calculation': f'received totals include {original.document_id} and exclude {repeat.document_id}'})

    # Identical lines on one invoice: both are billed and counted, but could be an extraction repeat.
    for inv in invoices:
        groups = defaultdict(list)
        for l in inv.line_items:
            groups[(normalize_sku(l.sku), Decimal(l.quantity), normalize_unit(l.unit), Decimal(l.unit_price))].append(l)
        for lines in groups.values():
            if len(lines) > 1:
                ids = ', '.join(l.line_id for l in lines)
                review.append(_issue('duplicate_document', [_ref(inv, l) for l in lines],
                    f'[POSSIBLE_REPEATED_LINE] {_where(inv)} lines {ids} are identical ({normalize_sku(lines[0].sku)}, '
                    f'{lines[0].quantity} {lines[0].unit} at {lines[0].unit_price}); all are counted. Confirm they are '
                    'separate charges, not a repeated line.'))

    def priced(d):
        return tuple(PricedLine(normalize_sku(l.sku), l.description or '', Decimal(l.quantity), Decimal(l.unit_price))
                     for l in d.line_items)
    report = reconcile(
        PurchaseOrder(po.document_id, po.revision, po.supplier_id, po.currency, priced(po)),
        [SupplierInvoice(d.document_id, d.revision, d.purchase_order_id, d.supplier_id, d.currency, priced(d)) for d in invoices],
        [DeliveryRecord(d.document_id, d.revision, d.purchase_order_id,
            tuple(DeliveryLine(normalize_sku(l.sku), l.description or '', Decimal(l.quantity)) for l in d.line_items))
         for d in counted_receipts])

    mapped = review
    for finding in report.discrepancies:
        code = {'PRICE_MISMATCH': 'price_mismatch', 'SUPPLIER_MISMATCH': 'supplier_mismatch',
                'UNMATCHED_INVOICE_LINE': 'unmatched_item', 'UNMATCHED_DELIVERY_LINE': 'unmatched_item',
                'CURRENCY_MISMATCH': 'missing_information'}.get(finding.type.value, 'quantity_mismatch')
        lines = _finding_lines(finding, po, invoices, counted_receipts)
        where = '; '.join(f'{r["document_id"]} line {r["line_id"]}' for r in lines)
        mapped.append({'code': code, 'lines': lines, 'expected': _text(finding.expected),
            'observed': _text(finding.actual), 'explanation': f'[{finding.type.value}] {finding.message} (lines: {where})',
            'calculation': finding.calculation})

    # Totals come straight from the engine; the adapter only formats them (no arithmetic here).
    po_line_by_sku = {normalize_sku(l.sku): l.line_id for l in po.line_items}
    quantities = [{'po_line': {'document_id': po.document_id, 'line_id': po_line_by_sku[t.sku]},
                   'ordered': _text(t.ordered), 'received': _text(t.received), 'invoiced': _text(t.billed),
                   'calculation': t.calculation} for t in report.line_totals]
    amounts = None if report.billed_total is None else {'currency': po.currency,
        'billed': _text(report.billed_total), 'supported': _text(report.supported_total),
        'unsupported': _text(report.billed_above_supported), 'unbilled': _text(report.supported_not_billed)}
    return ReconciliationResult.model_validate({'case_id': state['case_id'], 'revision': state['revision'],
        'status': 'needs_review' if mapped else 'clean', 'checked_lines': refs, 'discrepancies': mapped,
        'quantities': quantities, 'amounts': amounts}).model_dump()


# Which documents each engine finding is about (besides the PO line for its SKU).
_FINDING_SCOPE = {
    'MISSING_DELIVERY': ('receipt',), 'OVER_DELIVERY': ('receipt',),
    'BILLED_EXCEEDS_RECEIVED': ('invoice', 'receipt'), 'UNDER_BILLED': ('invoice', 'receipt'),
    'BILLED_EXCEEDS_ORDERED': ('invoice',), 'PRICE_MISMATCH': ('invoice',),
    'UNMATCHED_INVOICE_LINE': ('invoice',), 'UNMATCHED_DELIVERY_LINE': ('receipt',),
}


def _finding_lines(finding, po, invoices, counted_receipts):
    """The exact lines an engine finding refers to. Receipts excluded as repeats are not included."""
    kind = finding.type.value
    if kind in ('SUPPLIER_MISMATCH', 'CURRENCY_MISMATCH'):
        doc = next(d for d in invoices if d.document_id == finding.document_id)
        return [_ref(d, l) for d in (po, doc) for l in d.line_items]
    scope = _FINDING_SCOPE.get(kind, ('invoice', 'receipt'))  # unknown future type: be inclusive, never crash
    candidates = [po] + (invoices if 'invoice' in scope else []) + (counted_receipts if 'receipt' in scope else [])
    lines = []
    for d in candidates:
        if finding.document_id is not None and d.document_id not in {finding.document_id, po.document_id}:
            continue
        for l in d.line_items:
            if normalize_sku(l.sku) != finding.sku:
                continue
            if kind == 'PRICE_MISMATCH' and d is not po and Decimal(l.unit_price) != finding.actual:
                continue  # only the invoice line(s) charging the flagged price
            lines.append(_ref(d, l))
    return lines


def _text(value):
    """Decimal -> plain decimal text (never scientific notation); None stays None."""
    return None if value is None else format(value, 'f')
