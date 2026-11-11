import asyncio
from dataclasses import replace
import pytest
from langgraph.errors import NodeCancelledError
from src.case_store import CaseStore, CaseConflict
from src.case_service import start_case, correct_receipt_quantity, resume_case
from demo.sample_workers import make_sample_workers


def run(coro):
    return asyncio.run(coro)


def correction(**changes):
    return {'expected_revision': 1, 'document_id': 'receipt-001', 'line_id': 'receipt-001-line-1',
            'old_value': '18', 'new_value': '20', 'staff': 'Dhruv',
            'reason': 'Synthetic extraction mistake', 'source_confirmation': 'Synthetic source confirms 20 each',
            'confirmed': True, **changes}


def setup_case(tmp_path):
    store = CaseStore(tmp_path / 'cases.sqlite')
    workers = make_sample_workers('short-delivery')
    run(start_case(store, workers, 'saved-test'))
    return store, workers


def test_reopen_correct_and_reopen_again(tmp_path):
    store, workers = setup_case(tmp_path)
    reopened = CaseStore(store.path)
    assert reopened.load('saved-test')[1]['state']['status'] == 'needs_correction'
    result = run(correct_receipt_quantity(reopened, workers, 'saved-test', correction()))
    assert result['state']['status'] == 'ready_for_human_review'
    assert result['state']['revision'] == 2
    assert result['state']['reconciliation']['revision'] == 2
    assert result['state']['review']['revision'] == 2
    assert result['history'][0]['documents'][-1]['line_items'][0]['quantity'] == '18'
    assert result['state']['documents'][-1]['line_items'][0]['quantity'] == '20'
    assert result['corrections'][0]['staff'] == 'Dhruv'
    assert CaseStore(store.path).load('saved-test')[1] == result


def test_actual_shortfall_stays_unresolved(tmp_path):
    store, workers = setup_case(tmp_path)
    result = run(correct_receipt_quantity(store, workers, 'saved-test', correction(new_value='19')))
    assert result['state']['status'] == 'needs_correction'
    assert result['state']['reconciliation']['discrepancies'][0]['observed'] == '19'


@pytest.mark.parametrize('change', [
    {'confirmed': False}, {'expected_revision': 2}, {'old_value': '17'},
    {'new_value': '18.0'}, {'new_value': '-1'}, {'staff': ' '},
    {'reason': ''}, {'source_confirmation': ''}, {'document_id': 'po-001'}, {'line_id': 'fake'}])
def test_invalid_correction_does_not_modify_saved_case(tmp_path, change):
    store, workers = setup_case(tmp_path)
    before = store.load('saved-test')
    with pytest.raises(ValueError):
        run(correct_receipt_quantity(store, workers, 'saved-test', correction(**change)))
    assert store.load('saved-test') == before


def test_cannot_overwrite_existing_case(tmp_path):
    store, workers = setup_case(tmp_path)
    with pytest.raises(CaseConflict):
        run(start_case(store, workers, 'saved-test'))


def test_duplicate_correction_rejected(tmp_path):
    store, workers = setup_case(tmp_path)
    run(correct_receipt_quantity(store, workers, 'saved-test', correction()))
    before = store.load('saved-test')
    with pytest.raises(ValueError):
        run(correct_receipt_quantity(store, workers, 'saved-test', correction()))
    assert store.load('saved-test') == before


def test_worker_failure_keeps_correction_but_no_stale_approval(tmp_path):
    store, workers = setup_case(tmp_path)
    async def fail(state):
        raise RuntimeError('provider failed')
    result = run(correct_receipt_quantity(store, replace(workers, match=fail), 'saved-test', correction()))
    assert result['state']['status'] == 'blocked'
    assert result['state']['revision'] == 2
    assert result['state']['reconciliation'] is None
    assert result['state']['review'] is None
    assert len(result['corrections']) == 1


def test_interrupted_correction_can_resume_after_reopen(tmp_path):
    store, workers = setup_case(tmp_path)
    async def interrupt(state):
        asyncio.current_task().cancel()
        await asyncio.sleep(0)
    with pytest.raises((asyncio.CancelledError, NodeCancelledError)):
        run(correct_receipt_quantity(store, replace(workers, match=interrupt), 'saved-test', correction()))
    state = CaseStore(store.path).load('saved-test')[1]['state']
    assert state['revision'] == 2
    assert state['status'] == 'running'
    assert state['matches'] == [] and state['reconciliation'] is None and state['review'] is None
    result = run(resume_case(CaseStore(store.path), workers, 'saved-test'))
    assert result['state']['status'] == 'ready_for_human_review'
    assert len(result['corrections']) == 1


def test_stale_store_save_rejected(tmp_path):
    store, workers = setup_case(tmp_path)
    version, record = store.load('saved-test')
    store.save(record, version)
    with pytest.raises(CaseConflict):
        store.save(record, version)


def test_completed_case_cannot_be_resumed(tmp_path):
    store, workers = setup_case(tmp_path)
    with pytest.raises(ValueError):
        run(resume_case(store, workers, 'saved-test'))
