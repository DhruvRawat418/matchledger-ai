import asyncio
from copy import deepcopy
import pytest
from src.graph import initial_state
from demo.sample_workers import make_sample_workers
from src.reconciliation_adapter import reconcile_worker


def inputs():
    async def build():
        state = initial_state('adapter-test')
        workers = make_sample_workers('clean')
        state['documents'] = await workers.extract(state)
        state['matches'] = await workers.match(state)
        return state
    return asyncio.run(build())


def run(state):
    return asyncio.run(reconcile_worker(state))


def test_clean_real_engine():
    assert run(inputs())['status'] == 'clean'


def test_two_independent_quantity_findings():
    state = inputs()
    state['documents'][-1]['line_items'][0]['quantity'] = '18'
    result = run(state)
    assert result['status'] == 'needs_review'
    assert {d['explanation'].split(']')[0] for d in result['discrepancies']} == {'[MISSING_DELIVERY', '[BILLED_EXCEEDS_RECEIVED'}


def test_price_mismatch():
    state = inputs()
    state['documents'][1]['line_items'][0]['unit_price'] = '130.00'
    result = run(state)
    assert result['discrepancies'][0]['code'] == 'price_mismatch'
    assert result['discrepancies'][0]['observed'] == '130.00'


def test_supplier_mismatch():
    state = inputs()
    state['documents'][1]['supplier_id'] = 'DIFFERENT'
    assert run(state)['discrepancies'][0]['code'] == 'supplier_mismatch'


@pytest.mark.parametrize('field', ['supplier_id', 'currency', 'purchase_order_id'])
def test_missing_header_is_blocked_not_invented(field):
    state = inputs()
    state['documents'][1][field] = None
    assert run(state)['status'] == 'blocked'


def test_unit_mismatch_blocks():
    state = inputs()
    state['documents'][-1]['line_items'][0]['unit'] = 'box'
    assert run(state)['discrepancies'][0]['code'] == 'unit_mismatch'


def test_ai_match_does_not_silently_become_exact():
    state = inputs()
    state['matches'][0]['method'] = 'llm'
    before = deepcopy(state)
    assert run(state)['status'] == 'clean'
    assert state == before
    assert state['matches'][0]['method'] == 'llm'


def test_wrong_sku_link_blocked():
    state = inputs()
    state['documents'][1]['line_items'][0]['sku'] = 'MON-27'
    assert run(state)['status'] == 'blocked'


def test_multiple_receipts_sum_without_running_totals():
    state = inputs()
    receipt = state['documents'][-1]
    receipt['line_items'][0]['quantity'] = '18'
    second = deepcopy(receipt)
    second['document_id'] = 'receipt-002'
    second['line_items'][0].update(line_id='receipt-002-line-1', quantity='2')
    state['documents'].append(second)
    match = deepcopy(state['matches'][-1])
    match['source'] = {'document_id': 'receipt-002', 'line_id': 'receipt-002-line-1'}
    state['matches'].append(match)
    assert run(state)['status'] == 'clean'
    assert run(state)['status'] == 'clean'


def test_missing_quantity_stays_missing():
    state = inputs()
    state['documents'][-1]['line_items'][0]['quantity'] = None
    assert run(state)['status'] == 'blocked'
