"""Persist synthetic cases and exercise human quantity correction. No live AI."""
import argparse
import asyncio
import json
from src.case_store import CaseStore
from src.case_service import start_case, correct_receipt_quantity, resume_case, stop_case
from demo.sample_workers import make_sample_workers


def summary(record):
    state = record['state']
    return {'mode': record.get('metadata', {}).get('mode', 'synthetic_workers'), 'case_id': state['case_id'], 'revision': state['revision'],
            'status': state['status'], 'issues': state['issues'], 'stages': state['stages'],
            'correction_history': record['corrections'], 'previous_revisions': len(record['history']),
            'stop_event': record.get('stop_event')}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='outputs/cases.sqlite', help='Use the same path across commands')
    sub = parser.add_subparsers(dest='action', required=True)
    start = sub.add_parser('start')
    start.add_argument('case_id')
    start.add_argument('--scenario', choices=['clean', 'short-delivery'], default='short-delivery')
    for action in ['show', 'resume']:
        sub.add_parser(action).add_argument('case_id')
    correction = sub.add_parser('correct')
    correction.add_argument('case_id')
    correction.add_argument('--revision', type=int, required=True)
    correction.add_argument('--document', required=True)
    correction.add_argument('--line', required=True)
    correction.add_argument('--old', required=True)
    correction.add_argument('--new', required=True)
    correction.add_argument('--staff', required=True)
    correction.add_argument('--reason', required=True)
    correction.add_argument('--source-confirmation', required=True)
    correction.add_argument('--confirm', action='store_true')
    stop = sub.add_parser('stop')
    stop.add_argument('case_id')
    stop.add_argument('--revision', type=int, required=True)
    stop.add_argument('--staff', required=True)
    stop.add_argument('--reason', required=True)
    stop.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    store = CaseStore(args.db)
    try:
        if args.action == 'start':
            record = await start_case(store, make_sample_workers(args.scenario), args.case_id,
                                      {'scenario': args.scenario, 'mode': 'synthetic_inputs_real_reconciliation'})
        elif args.action == 'stop':
            record = stop_case(store, args.case_id, {
                'expected_revision': args.revision, 'staff': args.staff,
                'reason': args.reason, 'confirmed': args.confirm})
        else:
            _, record = store.load(args.case_id)
            if args.action != 'show':
                workers = make_sample_workers(record['metadata']['scenario'])
                if args.action == 'resume':
                    record = await resume_case(store, workers, args.case_id)
                else:
                    record = await correct_receipt_quantity(store, workers, args.case_id, {
                        'expected_revision': args.revision, 'document_id': args.document,
                        'line_id': args.line, 'old_value': args.old, 'new_value': args.new,
                        'staff': args.staff, 'reason': args.reason,
                        'source_confirmation': args.source_confirmation, 'confirmed': args.confirm})
        print(json.dumps(summary(record), indent=2))
    except (ValueError, KeyError) as exc:
        parser.exit(2, f'Cannot complete action: {exc}\n')

if __name__ == '__main__':
    asyncio.run(main())
