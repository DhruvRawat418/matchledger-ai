import asyncio
from dataclasses import replace
import pytest
from src.graph import build_graph, initial_state
from demo.sample_workers import make_sample_workers


def execute(workers, state=None):
    return asyncio.run(build_graph(workers).ainvoke(state or initial_state('test-case')))


def test_clean_needs_human_review_not_approval():
    state = execute(make_sample_workers('clean'))
    assert state['status'] == 'ready_for_human_review'
    assert state['issues'] == []
    assert state['stages'] == ['extract', 'match', 'reconcile', 'review', 'finish']


def test_empty_review_does_not_clear_quantity_issue():
    state = execute(make_sample_workers('short-delivery'))
    assert state['status'] == 'needs_correction'
    assert state['issues'] == [{'code': 'quantity_mismatch', 'owner': 'reconcile'}]


@pytest.mark.parametrize('stage', ['extract', 'match', 'reconcile', 'review'])
def test_failure_blocks_and_stops_downstream(stage):
    workers = make_sample_workers('clean')
    async def fail(state):
        raise RuntimeError('sensitive provider detail')
    result = execute(replace(workers, **{stage: fail}))
    assert result['status'] == 'blocked'
    assert result['stages'][-1] == stage
    assert 'sensitive' not in str(result)


def test_uncertain_match_stops_before_calculation():
    workers = make_sample_workers('clean')
    async def uncertain(state):
        results = await workers.match(state)
        results[0].update(decision='uncertain', selected_po_line=None)
        return results
    result = execute(replace(workers, match=uncertain))
    assert result['status'] == 'needs_correction'
    assert result['stages'] == ['extract', 'match']
    assert result['reconciliation'] is None


@pytest.mark.parametrize('stage', ['extract', 'match', 'reconcile', 'review'])
def test_stale_outputs_rejected(stage):
    workers = make_sample_workers('clean')
    async def stale(state):
        output = await getattr(workers, stage)(state)
        for item in output if isinstance(output, list) else [output]:
            item['revision'] = 99
        return output
    result = execute(replace(workers, **{stage: stale}))
    assert result['status'] == 'blocked'
    assert result['stages'][-1] == stage


def test_omitted_match_blocks():
    workers = make_sample_workers('clean')
    async def incomplete(state):
        return (await workers.match(state))[:1]
    assert execute(replace(workers, match=incomplete))['status'] == 'blocked'


def test_clean_reconciliation_must_cover_all_sources():
    workers = make_sample_workers('clean')
    async def incomplete(state):
        result = await workers.reconcile(state)
        result['checked_lines'] = result['checked_lines'][:1]
        return result
    assert execute(replace(workers, reconcile=incomplete))['status'] == 'blocked'


def test_reviewer_finding_routes_for_correction():
    workers = make_sample_workers('clean')
    async def finding(state):
        return {'case_id': state['case_id'], 'revision': state['revision'], 'findings': [
            {'code': 'unsupported_match', 'owner': 'match', 'explanation': 'Synthetic challenge finding'}]}
    result = execute(replace(workers, review=finding))
    assert result['status'] == 'needs_correction'
    assert result['issues'][0]['owner'] == 'match'


def test_worker_cannot_mutate_shared_input():
    workers = make_sample_workers('clean')
    async def mutating_review(state):
        result = await workers.review(state)
        state['documents'].clear()
        return result
    result = execute(replace(workers, review=mutating_review))
    assert len(result['documents']) == 3


def test_new_run_clears_injected_previous_outputs():
    state = initial_state('test-case')
    state.update(status='ready_for_human_review', issues=[{'code':'old'}], stages=['old'], review={'old':True})
    result = execute(make_sample_workers('short-delivery'), state)
    assert result['status'] == 'needs_correction'
    assert 'old' not in str(result)
