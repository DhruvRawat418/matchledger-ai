"""Saved live document runs, additions and explicit replacements."""
import argparse
import asyncio
import json
from pathlib import Path
from src.case_store import CaseStore
from src.document_cases import start_documents, change_documents, retry_documents
from demo.run_uploads import run_data
from src.scan_review import review_packet, verify_scans


def summary(record):
    state = record['state']
    return {**{k:state[k] for k in ('case_id','revision','status','issues','stages')},
            'active_document_ids':record['active_document_ids'],
            'previous_revisions':len(record['history']), 'document_events':record['document_events'],
            'amounts':(state.get('reconciliation') or {}).get('amounts')}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('outputs/document_cases.sqlite'))
    sub = parser.add_subparsers(dest='action',required=True)
    start = sub.add_parser('start'); start.add_argument('manifest',type=Path)
    start.add_argument('--case-id',required=True)
    change = sub.add_parser('change'); change.add_argument('case_id'); change.add_argument('request',type=Path)
    change.add_argument('--staff',required=True); change.add_argument('--reason',required=True)
    change.add_argument('--revision',required=True,type=int); change.add_argument('--confirm',action='store_true')
    show = sub.add_parser('show'); show.add_argument('case_id')
    retry = sub.add_parser('retry'); retry.add_argument('case_id'); retry.add_argument('--revision',type=int,required=True)
    export = sub.add_parser('export-scan-review'); export.add_argument('case_id'); export.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify-scans'); verify.add_argument('case_id'); verify.add_argument('packet', type=Path)
    args = parser.parse_args()
    store = CaseStore(args.db)
    archive = args.db.parent/'document_archive'
    if args.action == 'export-scan-review':
        _, record = store.load(args.case_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as handle:
            json.dump(review_packet(record), handle, indent=2)
        print(f'Review packet saved to {args.output}; compare every field with the archived source images.')
        return
    if args.action == 'verify-scans':
        record = await verify_scans(store, args.case_id, json.loads(args.packet.read_text()))
    elif args.action == 'start':
        data = json.loads(args.manifest.read_text()); data['case_id'] = args.case_id
        record = await start_documents(store,data,args.manifest.parent,archive,run_data)
    elif args.action == 'change':
        data = {'documents':json.loads(args.request.read_text())['documents'],
                'expected_revision':args.revision,'staff':args.staff,'reason':args.reason,'confirmed':args.confirm}
        record = await change_documents(store,args.case_id,data,args.request.parent,archive,run_data)
    elif args.action == 'retry':
        record = await retry_documents(store,args.case_id,args.revision,run_data)
    else:
        _, record = store.load(args.case_id)
    print(json.dumps(summary(record),indent=2))


if __name__ == '__main__':
    asyncio.run(main())
