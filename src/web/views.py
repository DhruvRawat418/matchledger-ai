"""Turn a saved document-case record into what the page shows.

Display only: every number and sentence comes from the saved state. Nothing is
recalculated here, and a value the backend left empty stays empty (None).
"""
import re

STATUS_LABELS = {'ready_for_human_review': 'Checks passed', 'needs_correction': 'Needs attention',
                 'blocked': 'Blocked', 'running': 'Running', 'stopped': 'Stopped'}
TYPE_LABELS = {'purchase_order': 'Purchase order', 'invoice': 'Invoice', 'receipt': 'Receipt'}
FINDING_LABELS = {'PRICE_MISMATCH': 'Price mismatch', 'MISSING_DELIVERY': 'Missing delivery',
                  'BILLED_EXCEEDS_RECEIVED': 'Billed more than received',
                  'REPEATED_RECEIPT': 'Repeated receipt', 'POSSIBLE_REPEATED_RECEIPT': 'Possible repeated receipt',
                  'POSSIBLE_REPEATED_LINE': 'Possible repeated line', 'CONFLICTING_RECEIPTS': 'Conflicting receipts'}
ISSUE_LABELS = {'document_run_failed': 'The run failed before any result was saved.',
                'worker_failed_or_invalid_output': 'A step failed or returned output that did not pass validation.',
                'unresolved_match': 'At least one line could not be matched to a purchase-order line.',
                'duplicate_document': 'More than one invoice is active. Confirm which one applies.'}
_TAG = re.compile(r'^\[([A-Z_]+)\]\s*(.*?)(?:\s*\(lines:.*\))?$', re.S)


def _pretty(code):
    return code.replace('_', ' ').capitalize()


def _readable(text, numbers):
    """Show printed document numbers in place of internal upload IDs (longest ID first)."""
    for doc_id in sorted(numbers, key=len, reverse=True):
        text = text.replace(doc_id, numbers[doc_id])
    return text


def _finding(d, po_id, numbers):
    tag = _TAG.match(d['explanation'])
    kind, text = (tag.group(1), tag.group(2)) if tag else (None, d['explanation'])
    return {'code': d['code'], 'kind': kind, 'title': FINDING_LABELS.get(kind) or _pretty(d['code']),
            'text': _readable(text, numbers), 'calculation': d.get('calculation') and _readable(d['calculation'], numbers), 'expected': d['expected'], 'observed': d['observed'],
            'po_line_ids': [r['line_id'] for r in d['lines'] if r['document_id'] == po_id], 'lines': d['lines']}


def summarize_state(state):
    rec = state.get('reconciliation') or {}
    return {'revision': state['revision'], 'status': state['status'],
            'status_label': STATUS_LABELS.get(state['status'], state['status']),
            'amounts': rec.get('amounts'), 'finding_count': len(rec.get('discrepancies', [])),
            'finding_codes': sorted({d['code'] for d in rec.get('discrepancies', [])})}


def case_summary(record):
    state = record['state']
    docs = {d['document_id']: d for d in state.get('documents', [])}
    po = next((d for d in docs.values() if d['document_type'] == 'purchase_order'), None)
    return {'case_id': state['case_id'], **summarize_state(state), 'updated_at': record.get('updated_at'),
            'po_number': po and po.get('business_document_number'), 'mode': state.get('mode'),
            'previous_revisions': len(record.get('history', []))}


def case_view(record):
    state = record['state']
    rec = state.get('reconciliation') or {}
    docs = {d['document_id']: d for d in state.get('documents', [])}
    po = next((d for d in docs.values() if d['document_type'] == 'purchase_order'), None)
    po_id = po['document_id'] if po else None
    numbers = {i: d['business_document_number'] for i, d in docs.items() if d.get('business_document_number')}
    findings = [_finding(d, po_id, numbers) for d in rec.get('discrepancies', [])]
    quantities = {q['po_line']['line_id']: q for q in rec.get('quantities', [])}

    def source(match):
        doc = docs[match['source']['document_id']]
        line = next(l for l in doc['line_items'] if l['line_id'] == match['source']['line_id'])
        return {'document_id': doc['document_id'], 'document_type': doc['document_type'],
                'document_number': doc.get('business_document_number'), 'line_id': line['line_id'],
                'description': line['description'], 'sku': line['sku'], 'quantity': line['quantity'],
                'unit': line['unit'], 'unit_price': line['unit_price'], 'decision': match['decision'],
                'method': match['method'], 'explanation': match['explanation'], 'evidence': line['evidence']}

    by_line, unmatched = {}, []
    for match in state.get('matches', []):
        if match['source']['document_id'] not in docs:
            continue
        item = source(match)
        if match['decision'] == 'matched':
            by_line.setdefault(match['selected_po_line']['line_id'], []).append(item)
        else:
            unmatched.append(item)

    items = []
    for line in (po['line_items'] if po else []):
        q = quantities.get(line['line_id'])
        sources = sorted(by_line.get(line['line_id'], []), key=lambda s: s['document_type'] != 'invoice')
        invoice_prices = [s['unit_price'] for s in sources if s['document_type'] == 'invoice']
        line_findings = [f for f in findings if line['line_id'] in f['po_line_ids']]
        items.append({'line_id': line['line_id'], 'sku': line['sku'], 'description': line['description'],
                      'unit': line['unit'], 'po_price': line['unit_price'],
                      'invoice_price': invoice_prices[0] if len(invoice_prices) == 1 else None,
                      'ordered': q['ordered'] if q else line['quantity'], 'received': q and q['received'],
                      'invoiced': q and q['invoiced'], 'calculation': q and _readable(q['calculation'], numbers),
                      'checked': q is not None, 'finding_titles': [f['title'] for f in line_findings],
                      'sources': sources, 'evidence': line['evidence']})

    uploads = record.get('uploads', {})
    active = record.get('active_document_ids', [])
    # A replaced document is absent from the current state; its last reading is in history.
    earlier = {d['document_id']: d for h in record.get('history', []) for d in h['state'].get('documents', [])}
    documents = []
    for doc_id, upload in uploads.items():
        extracted = docs.get(doc_id) or earlier.get(doc_id)
        documents.append({'document_id': doc_id, 'document_type': upload['document_type'],
                          'type_label': TYPE_LABELS[upload['document_type']], 'original_name': upload['original_name'],
                          'sha256': upload['sha256'][:12], 'active': doc_id in active,
                          'document_number': extracted and extracted.get('business_document_number'),
                          'line_count': len(extracted['line_items']) if extracted else None})
    documents.sort(key=lambda d: (['purchase_order', 'invoice', 'receipt'].index(d['document_type']), not d['active']))

    issues = []
    reconcile_codes = {d['code'] for d in rec.get('discrepancies', [])}
    for issue in state.get('issues', []):
        if issue.get('owner') == 'reconcile' and issue['code'] in reconcile_codes:
            continue  # already shown as a finding
        issues.append({'code': issue['code'], 'owner': issue.get('owner'), 'error_type': issue.get('error_type'),
                       'document_id': issue.get('document_id'),
                       'text': issue.get('explanation') or ISSUE_LABELS.get(issue['code']) or _pretty(issue['code'])})

    events = {e['new_revision']: e for e in record.get('document_events', [])}
    scan_events = {e['new_revision']: e for e in record.get('scan_reviews', [])}
    trail = [*(h['state'] for h in record.get('history', [])), state]
    revisions = []
    for old in trail:
        entry = summarize_state(old)
        event = events.get(old['revision'])
        if event:
            entry['event'] = {'staff': event['staff'], 'reason': event['reason'], 'recorded_at': event['recorded_at'],
                              'changes': [{'action': c['action'], 'document_type': c['document_type'],
                                           'type_label': TYPE_LABELS[c['document_type']], 'replaces': c.get('replaces'),
                                           'original_name': uploads.get(c['document_id'], {}).get('original_name')}
                                          for c in event['documents']]}
        if old['revision'] in scan_events:
            e = scan_events[old['revision']]
            entry['scan_review'] = {k:e[k] for k in ('staff','reason','recorded_at')}
        revisions.append(entry)

    kinds = [uploads[i]['document_type'] for i in active if i in uploads]
    idle = state['status'] in {'needs_correction', 'blocked', 'ready_for_human_review'}
    invoice_id = next((i for i in active if uploads.get(i, {}).get('document_type') == 'invoice'), None)
    return {**case_summary(record), 'stages': state.get('stages', []), 'issues': issues,
            'notices': [n for n in state.get('extraction_notices', []) if n.get('severity') == 'info'],
            'reconciliation_status': rec.get('status'), 'findings': findings, 'items': items,
            'unmatched': unmatched, 'documents': documents, 'revisions': revisions,
            'semantic_matching': state.get('semantic_matching'),
            'actions': {'verify_scans': state['status'] == 'needs_correction' and any(n.get('code') == 'evidence_unverified' for n in state.get('extraction_notices', [])),
                        'add_receipt': idle and kinds.count('receipt') < 3,
                        'replace_invoice': idle and invoice_id is not None, 'active_invoice_id': invoice_id,
                        'retry': state['status'] in {'running', 'blocked'} and not rec}}
