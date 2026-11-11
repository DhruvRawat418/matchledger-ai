import asyncio
from copy import deepcopy
from dataclasses import replace
import pytest
from src.case_store import CaseStore
from src.case_service import start_case, stop_case, correct_receipt_quantity, resume_case
from demo.sample_workers import make_sample_workers


def request(**changes):
    return {'expected_revision': 1, 'staff': 'Dhruv', 'reason': 'Only 18 arrived; cannot resolve this case',
            'confirmed': True, **changes}


def setup(tmp_path, scenario='short-delivery'):
    store = CaseStore(tmp_path / 'cases.sqlite')
    workers = make_sample_workers(scenario)
    asyncio.run(start_case(store, workers, 'stop-test'))
    return store, workers


def test_stop_preserves_evidence_and_survives_reopen(tmp_path):
    store, _ = setup(tmp_path)
    version, before = store.load('stop-test')
    result = stop_case(store, 'stop-test', request())
    assert result['state']['status'] == 'stopped'
    expected = deepcopy(before['state'])
    expected['status'] = 'stopped'
    assert result['state'] == expected
    assert result['history'] == before['history']
    assert result['corrections'] == before['corrections']
    assert result['stop_event']['previous_status'] == 'needs_correction'
    assert result['stop_event']['staff'] == 'Dhruv'
    assert result['stop_event']['recorded_at']
    assert CaseStore(store.path).load('stop-test') == (version + 1, result)


@pytest.mark.parametrize('changes', [{'expected_revision': 2}, {'confirmed': False},
                                    {'confirmed': 'yes'}, {'staff': ' '}, {'reason': ''}])
def test_invalid_stop_leaves_record_unchanged(tmp_path, changes):
    store, _ = setup(tmp_path)
    before = store.load('stop-test')
    with pytest.raises(ValueError):
        stop_case(store, 'stop-test', request(**changes))
    assert store.load('stop-test') == before


def test_stopped_case_cannot_be_stopped_corrected_or_resumed(tmp_path):
    store, workers = setup(tmp_path)
    stop_case(store, 'stop-test', request())
    before = store.load('stop-test')
    with pytest.raises(ValueError):
        stop_case(store, 'stop-test', request())
    with pytest.raises(ValueError):
        asyncio.run(resume_case(store, workers, 'stop-test'))
    with pytest.raises(ValueError):
        asyncio.run(correct_receipt_quantity(store, workers, 'stop-test', {
            'expected_revision': 1, 'document_id': 'receipt-001', 'line_id': 'receipt-001-line-1',
            'old_value': '18', 'new_value': '20', 'staff': 'Dhruv', 'reason': 'test',
            'source_confirmation': 'test', 'confirmed': True}))
    assert store.load('stop-test') == before


def test_running_case_cannot_be_stopped(tmp_path):
    store, _ = setup(tmp_path)
    version, record = store.load('stop-test')
    record['state']['status'] = 'running'
    store.save(record, version)
    before = store.load('stop-test')
    with pytest.raises(ValueError):
        stop_case(store, 'stop-test', request())
    assert store.load('stop-test') == before


def test_ready_case_can_be_closed_without_approval(tmp_path):
    store, _ = setup(tmp_path, 'clean')
    result = stop_case(store, 'stop-test', request(reason='Synthetic case withdrawn'))
    assert result['state']['status'] == 'stopped'
    assert result['stop_event']['previous_status'] == 'ready_for_human_review'


def test_blocked_case_can_be_closed(tmp_path):
    store = CaseStore(tmp_path / 'cases.sqlite')
    async def fail(state):
        raise RuntimeError('provider unavailable')
    workers = replace(make_sample_workers('clean'), review=fail)
    asyncio.run(start_case(store, workers, 'stop-test'))
    result = stop_case(store, 'stop-test', request())
    assert result['state']['status'] == 'stopped'
    assert result['state']['issues'][0]['owner'] == 'review'
    assert result['stop_event']['previous_status'] == 'blocked'
