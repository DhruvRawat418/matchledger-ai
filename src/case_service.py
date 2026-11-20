"""Saved run/correction boundary reusable by the future dashboard.

This resumes at a case revision boundary, not a LangGraph node checkpoint.
Worker functions must be read-only/idempotent: interrupted runs may be repeated.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import Field
from src.contracts import Contract, Identifier, PositiveInt, DecimalText, ExtractedDocument
from src.graph import build_graph, initial_state

class ReceiptQuantityCorrection(Contract):
    expected_revision: PositiveInt
    document_id: Identifier
    line_id: Identifier
    old_value: DecimalText
    new_value: DecimalText
    staff: Identifier
    reason: Identifier
    source_confirmation: Identifier
    confirmed: bool = Field(strict=True)


def now():
    return datetime.now(timezone.utc).isoformat()


async def start_case(store, workers, case_id, metadata=None):
    record = {'state': initial_state(case_id), 'history': [], 'corrections': [],
              'metadata': metadata or {}, 'updated_at': now()}
    version = store.create(record)
    return await _execute(store, workers, version, record)


async def _execute(store, workers, version, record):
    state = record['state']
    if state['documents']:
        # Validated human corrections supersede re-extraction for this rerun.
        # The original extraction and all prior outputs remain in history.
        documents = deepcopy(state['documents'])
        async def saved_extraction(_state):
            return deepcopy(documents)
        workers = replace(workers, extract=saved_extraction)
    request = initial_state(state['case_id'], state['revision'])
    request['corrections'] = deepcopy(record['corrections'])
    result = await build_graph(workers).ainvoke(request)
    record['state'] = result
    record['updated_at'] = now()
    store.save(record, version)
    return record


async def resume_case(store, workers, case_id):
    version, record = store.load(case_id)
    if record.get('kind') == 'document_case':
        raise ValueError('Use retry_documents for a document case')
    if record['state']['status'] != 'running':
        raise ValueError('Only an interrupted running revision can be resumed')
    return await _execute(store, workers, version, record)


async def correct_receipt_quantity(store, workers, case_id, correction):
    correction = ReceiptQuantityCorrection.model_validate(correction)
    version, record = store.load(case_id)
    if record.get('kind') == 'document_case':
        raise ValueError('Use source-document changes for a document case')
    state = record['state']
    if state['status'] != 'needs_correction':
        raise ValueError('This case is not waiting for correction')
    if state['revision'] != correction.expected_revision:
        raise ValueError('Stale revision; reload the saved case')
    if not correction.confirmed:
        raise ValueError('Confirm the source document supports this correction')
    if Decimal(correction.old_value) == Decimal(correction.new_value):
        raise ValueError('Correction must change the quantity')
    documents = deepcopy(state['documents'])
    document = next((d for d in documents if d['document_id'] == correction.document_id), None)
    if document is None or document['document_type'] != 'receipt':
        raise ValueError('Select a receipt document from this case')
    line = next((l for l in document['line_items'] if l['line_id'] == correction.line_id), None)
    if line is None or line['quantity'] != correction.old_value:
        raise ValueError('Line or previous value does not match the saved case')
    previous_state = deepcopy(state)
    line['quantity'] = correction.new_value
    # Do not fabricate/overwrite the original model's source quotation.
    # Human source confirmation is stored separately in the audit event.
    next_revision = state['revision'] + 1
    for d in documents:
        d['revision'] = next_revision
        ExtractedDocument.model_validate(d)
    event = {**correction.model_dump(), 'recorded_at': now(), 'new_revision': next_revision,
             'field': 'quantity', 'kind': 'human_source_correction'}
    record['history'].append(previous_state)
    record['corrections'].append(event)
    record['state'] = initial_state(case_id, next_revision)
    record['state']['documents'] = documents
    record['updated_at'] = now()
    # Persist the correction and invalidation BEFORE calling any worker.
    version = store.save(record, version)
    return await _execute(store, workers, version, record)


class StopCaseRequest(Contract):
    expected_revision: PositiveInt
    staff: Identifier
    reason: Identifier
    confirmed: bool = Field(strict=True)


def stop_case(store, case_id, request):
    """Close an idle case without changing evidence or calling workers.

    The input revision stays unchanged because no source data changed.
    SQLite's record version still advances to reject stale concurrent writes.
    """
    request = StopCaseRequest.model_validate(request)
    version, record = store.load(case_id)
    state = record['state']
    if state['revision'] != request.expected_revision:
        raise ValueError('Stale revision; reload the saved case')
    if not request.confirmed:
        raise ValueError('Confirm that this case should be stopped')
    if state['status'] not in {'needs_correction', 'blocked', 'ready_for_human_review'}:
        raise ValueError('Only an idle, non-stopped case can be stopped')
    record['stop_event'] = {**request.model_dump(), 'kind': 'case_stopped',
                            'previous_status': state['status'], 'recorded_at': now()}
    state['status'] = 'stopped'
    record['updated_at'] = record['stop_event']['recorded_at']
    store.save(record, version)
    return record
