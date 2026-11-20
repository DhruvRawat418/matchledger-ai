"""Application entry point; runtime model clients are explicit dependencies."""
from src.graph import Workers
from src.extraction_adapter import make_extraction_worker
from src.matching import make_match_worker
from src.reconciliation_adapter import reconcile_worker


async def deterministic_review(state):
    """Boundary audit, not an LLM reviewer and never authorization to pay."""
    result = state['reconciliation']
    findings = []
    if result['status'] == 'clean' and (not result.get('quantities') or result.get('amounts') is None):
        findings.append({'code': 'missing_calculation_evidence', 'owner': 'reconcile',
                         'explanation': 'A clean result requires saved quantity and amount evidence.'})
    return {'case_id': state['case_id'], 'revision': state['revision'], 'findings': findings}


def make_upload_workers(requests, extraction_client, matching_client=None, review=deterministic_review):
    return Workers(extract=make_extraction_worker(requests, extraction_client),
                   match=make_match_worker(matching_client), reconcile=reconcile_worker, review=review)
