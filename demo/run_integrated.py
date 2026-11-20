"""Offline integration smoke run: ideal extracted fixtures, real matching and math.

No OCR/model performance claim is made by this command.
"""
import argparse
import asyncio
import json
from eval.ground_truth import CASES_DIR, load_case, snapshot
from src.graph import Workers, build_graph, initial_state
from src.integrated_workers import deterministic_review
from src.matching import make_match_worker
from src.reconciliation_adapter import reconcile_worker


async def run(case_id, revision):
    case = load_case(CASES_DIR / f'{case_id}.json')
    documents = [d.model_dump() for d in snapshot(case, revision)]
    async def extract(state):
        return documents
    graph = build_graph(Workers(extract, make_match_worker(), reconcile_worker, deterministic_review))
    result = await graph.ainvoke(initial_state(case.case_id, revision))
    return {'mode': 'fixture_extraction_real_matching_and_reconciliation', **result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', default='case-101', choices=[p.stem for p in CASES_DIR.glob('case-*.json')])
    parser.add_argument('--revision', type=int, default=1)
    args = parser.parse_args()
    result = asyncio.run(run(args.case, args.revision))
    print(json.dumps({k: result[k] for k in ('mode', 'status', 'issues', 'stages', 'reconciliation')}, indent=2))
