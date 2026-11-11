"""Translate validated graph contracts to Matthew Suruadji's original engine.

Phase 1 supports exact SKU matches only. No model decisions or arithmetic here.
"""
from decimal import Decimal
from src.contracts import ExtractedDocument, MatchResult, validate_handoff, ReconciliationResult
from procurement_recon.schemas.documents import PurchaseOrder, SupplierInvoice, DeliveryRecord, PricedLine, DeliveryLine
from procurement_recon.reconciliation.reconciliation_rules import reconcile


def _receipt_number(doc):
    number = doc.business_document_number
    return None if number is None else number.casefold()


def _receipt_content(doc):
    """What a receipt says was delivered, ignoring line IDs and number formatting."""
    return tuple(sorted((line.sku or '', str(Decimal(line.quantity).normalize()) if line.quantity is not None else '',
                         line.unit or '') for line in doc.line_items))


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


async def reconcile_worker(state):
    docs = [ExtractedDocument.model_validate(d) for d in state['documents']]
    matches = [MatchResult.model_validate(m) for m in state['matches']]
    validate_handoff(docs, matches)
    if any((d.case_id, d.revision) != (state['case_id'], state['revision']) for d in docs):
        raise ValueError('Stale reconciliation input')
    refs = [{'document_id': d.document_id, 'line_id': l.line_id} for d in docs for l in d.line_items]
    by_ref = {(d.document_id, l.line_id): l for d in docs for l in d.line_items}

    def blocked(code, explanation):
        return ReconciliationResult.model_validate({
            'case_id': state['case_id'], 'revision': state['revision'], 'status': 'blocked',
            'checked_lines': [], 'discrepancies': [{'code': code, 'lines': refs,
                'expected': None, 'observed': None, 'explanation': explanation}]}).model_dump()

    orders = [d for d in docs if d.document_type == 'purchase_order']
    invoices = [d for d in docs if d.document_type == 'invoice']
    receipts = [d for d in docs if d.document_type == 'receipt']
    if len(orders) != 1 or len(invoices) != 1 or not 1 <= len(receipts) <= 3:
        return blocked('missing_information', 'Expected one PO, one invoice and one to three active receipts')
    po = orders[0]
    if any(d.currency != 'USD' or d.supplier_id is None for d in [po, *invoices]):
        return blocked('missing_information', 'PO and invoice require known USD currency and supplier IDs')
    if any(d.purchase_order_id != po.document_id for d in [*invoices, *receipts]):
        return blocked('missing_information', 'Every invoice/receipt must explicitly link to the active PO document ID')
    for d in docs:
        if len({l.sku for l in d.line_items}) != len(d.line_items):
            return blocked('duplicate_document', 'Repeated SKU within a document is unsupported by the engine; review the lines')
        if any(l.sku is None or l.quantity is None or l.unit is None or
               (d.document_type != 'receipt' and l.unit_price is None) for l in d.line_items):
            return blocked('missing_information', 'Missing SKU, quantity, unit or priced-document unit price')
    required = {(d.document_id, l.line_id) for d in [*invoices, *receipts] for l in d.line_items}
    if {(m.source.document_id, m.source.line_id) for m in matches} != required:
        return blocked('unmatched_item', 'Every source line needs a match decision')
    for m in matches:
        if m.decision != 'matched' or m.method != 'exact':
            return blocked('unmatched_item', 'This adapter currently accepts exact SKU matches only')
        source = by_ref[(m.source.document_id, m.source.line_id)]
        target = by_ref[(m.selected_po_line.document_id, m.selected_po_line.line_id)]
        if source.sku != target.sku:
            return blocked('unmatched_item', 'Selected PO SKU differs from source SKU')
        if source.unit != target.unit:
            return blocked('unit_mismatch', 'Units differ; no implicit unit conversion is allowed')

    # A repeated upload is not a new delivery. Screen receipts before the engine adds anything up.
    counted_receipts, repeats, conflict = screen_receipts(receipts)
    if conflict:
        first, second = conflict
        return blocked('duplicate_document', f'Receipts {first.document_id} and {second.document_id} share receipt '
                       f'number {first.business_document_number} but list different lines. Cannot tell whether one '
                       'replaces the other; neither is counted until a person confirms which is current.')
    screening = []
    for repeat, original, verified in repeats:
        reason = (f'same receipt number {repeat.business_document_number} and identical lines' if verified else
                  'identical lines and no receipt number to tell them apart; confirm if they are separate deliveries')
        screening.append({'code': 'duplicate_document',
            'lines': [ref for ref in refs if ref['document_id'] in {repeat.document_id, original.document_id}],
            'expected': None, 'observed': None,
            'explanation': f"[{'REPEATED_RECEIPT' if verified else 'POSSIBLE_REPEATED_RECEIPT'}] "
                           f'{repeat.document_id} repeats {original.document_id} ({reason}); counted once.'})

    def priced(d):
        return tuple(PricedLine(l.sku, l.description or '', Decimal(l.quantity), Decimal(l.unit_price)) for l in d.line_items)
    report = reconcile(
        PurchaseOrder(po.document_id, po.revision, po.supplier_id, po.currency, priced(po)),
        [SupplierInvoice(d.document_id, d.revision, d.purchase_order_id, d.supplier_id, d.currency, priced(d)) for d in invoices],
        [DeliveryRecord(d.document_id, d.revision, d.purchase_order_id,
            tuple(DeliveryLine(l.sku, l.description or '', Decimal(l.quantity)) for l in d.line_items)) for d in counted_receipts])
    mapped = screening
    for finding in report.discrepancies:
        code = {'PRICE_MISMATCH': 'price_mismatch', 'SUPPLIER_MISMATCH': 'supplier_mismatch',
                'UNMATCHED_INVOICE_LINE': 'unmatched_item', 'UNMATCHED_DELIVERY_LINE': 'unmatched_item',
                'CURRENCY_MISMATCH': 'missing_information'}.get(finding.type.value, 'quantity_mismatch')
        relevant = [ref for ref in refs if (finding.sku is None or by_ref[(ref['document_id'], ref['line_id'])].sku == finding.sku)
                    and (finding.document_id is None or ref['document_id'] in {finding.document_id, po.document_id})]
        mapped.append({'code': code, 'lines': relevant, 'expected': str(finding.expected) if finding.expected is not None else None,
            'observed': str(finding.actual) if finding.actual is not None else None,
            'explanation': f'[{finding.type.value}] {finding.message}'})
    return ReconciliationResult.model_validate({'case_id': state['case_id'], 'revision': state['revision'],
        'status': 'needs_review' if mapped else 'clean', 'checked_lines': refs, 'discrepancies': mapped}).model_dump()
