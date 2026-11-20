"""Explicit source verification for saved scans; never payment approval."""
from copy import deepcopy
from pydantic import Field
from src.contracts import Contract, Identifier, PositiveInt, ExtractedDocument, validate_handoff
from src.document_cases import _manifest
from src.case_service import now
from src.graph import Workers, build_graph, initial_state
from src.matching import make_match_worker
from src.reconciliation_adapter import reconcile_worker
from src.integrated_workers import deterministic_review


class ScanReview(Contract):
    expected_revision: PositiveInt
    staff: Identifier
    reason: Identifier
    confirmed: bool = Field(strict=True)
    source_hashes: dict[str, Identifier]
    documents: list[ExtractedDocument]


def review_packet(record):
    if record.get('kind') != 'document_case':
        raise ValueError('Use a saved document case')
    return {'expected_revision': record['state']['revision'], 'staff': '', 'reason': '',
            'confirmed': False,
            'source_hashes': {k:record['uploads'][k]['sha256'] for k in record['active_document_ids']},
            'documents': deepcopy(record['state']['documents'])}


async def verify_scans(store, case_id, payload):
    request = ScanReview.model_validate(payload)
    version, record = store.load(case_id)
    state = record['state']
    if record.get('kind') != 'document_case' or state['status'] != 'needs_correction':
        raise ValueError('Only paused document cases can be verified')
    if state['revision'] != request.expected_revision or not request.confirmed:
        raise ValueError('Confirm source verification for the current revision')
    _manifest(record)  # Verify archived bytes still match their fingerprints.
    if request.source_hashes != review_packet(record)['source_hashes']:
        raise ValueError('Source files changed; export a fresh packet')
    notices = state.get('extraction_notices', [])
    if not any(n['code'] == 'evidence_unverified' for n in notices):
        raise ValueError('No unverified scan evidence to resolve')
    original = {d['document_id']: d for d in state['documents']}
    if len(request.documents) != len(original) or set(original) != set(record['active_document_ids']):
        raise ValueError('All active documents must have extracted records')
    validate_handoff(request.documents, [])
    if {d.document_id for d in request.documents} != set(original):
        raise ValueError('Do not add or remove documents during source verification')
    docs = []
    for doc in request.documents:
        old = original[doc.document_id]
        for key in ('case_id', 'revision', 'document_type', 'purchase_order_id'):
            if getattr(doc, key) != old[key]:
                raise ValueError('Document identity and links cannot change during scan verification')
        if [x.line_id for x in doc.line_items] != [x['line_id'] for x in old['line_items']]:
            raise ValueError('Line structure changed; replace the source document instead')
        if not doc.business_document_number or (doc.document_type != 'receipt' and (not doc.supplier_id or not doc.currency)):
            raise ValueError('Verify document number, supplier and currency')
        if doc.supplier_id is not None and doc.supplier_id not in record['supplier_directory'].values():
            raise ValueError('Supplier must be in the case directory')
        for line in doc.line_items:
            if line.quantity is None or line.unit is None or (doc.document_type != 'receipt' and line.unit_price is None):
                raise ValueError('Missing quantity, unit or price requires source clarification')
        data = doc.model_dump()
        data['revision'] = state['revision'] + 1
        docs.append(data)
    # Confirmation resolves only the scan warning. Other blockers are not waived.
    remaining = [n for n in notices if n['code'] != 'evidence_unverified']
    record['history'].append({'state': deepcopy(state), 'active_document_ids': list(record['active_document_ids'])})
    record.setdefault('scan_reviews', []).append({**request.model_dump(), 'kind':'human_scan_verification',
        'recorded_at':now(), 'new_revision':state['revision'] + 1})
    fresh = initial_state(case_id, state['revision'] + 1)
    record['state'] = fresh
    record['updated_at'] = now()
    version = store.save(record, version)  # Invalidate old calculations before running.
    async def extract(_state):
        return {'documents': docs, 'issues': remaining}
    graph = build_graph(Workers(extract, make_match_worker(), reconcile_worker, deterministic_review))
    record['state'] = await graph.ainvoke(fresh)
    record['state']['mode'] = 'human_verified_extraction'
    record['state']['review_mode'] = 'deterministic_boundary_audit'
    record['updated_at'] = now()
    store.save(record, version)
    return record
