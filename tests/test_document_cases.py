"""Persistence and revision tests use real matching/math, with fixture extraction."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from demo.prepare_resolution import prepare, ROOT
from src.case_store import CaseStore
from src.document_cases import start_documents, change_documents, retry_documents
from src.graph import Workers, build_graph, initial_state
from src.matching import make_match_worker
from src.reconciliation_adapter import reconcile_worker
from src.integrated_workers import deterministic_review


def setup(tmp_path):
    out = tmp_path/'fixtures'
    originals, receipt, invoice = prepare(out)
    known = {}
    for doc in [*originals,receipt,invoice]:
        path = ROOT/'eval/rendered/case-102'/f'{doc.document_id}.pdf' if doc in originals else out/f'{doc.document_id}.pdf'
        known[hashlib.sha256(path.read_bytes()).hexdigest()] = doc
    calls = []
    async def runner(manifest):
        calls.append(deepcopy(manifest))
        docs = []
        for u in manifest['documents']:
            d = known[hashlib.sha256(Path(u['file_path']).read_bytes()).hexdigest()].model_dump()
            d.update(case_id=manifest['case_id'], revision=manifest['revision'],document_id=u['document_id'])
            docs.append(d)
        async def extract(state):return docs
        return await build_graph(Workers(extract,make_match_worker(),reconcile_worker,deterministic_review)).ainvoke(initial_state(manifest['case_id'],manifest['revision']))
    manifest = json.loads((ROOT/'demo/upload_manifest_102.json').read_text())
    store = CaseStore(tmp_path/'cases.sqlite')
    archive = tmp_path/'archive'
    def request(name, revision):
        return {**json.loads((out/f'{name}.json').read_text()),'expected_revision':revision,
                'staff':'Test staff','reason':'Synthetic source update','confirmed':True}
    return store,archive,out,runner,calls,manifest,request


def test_resolution_keeps_originals_and_reruns_each_revision(tmp_path):
    store,archive,out,runner,calls,manifest,request = setup(tmp_path)
    async def check():
        r = await start_documents(store,manifest,ROOT/'demo',archive,runner)
        original = deepcopy(r['state'])
        assert r['state']['reconciliation']['amounts']['unsupported'] == '85.00'
        r = await change_documents(store,'uploads-102',request('add_receipt',1),out,archive,runner)
        assert r['state']['revision'] == 2
        assert r['state']['reconciliation']['amounts']['unsupported'] == '34.00'
        assert {d['code'] for d in r['state']['reconciliation']['discrepancies']} == {'price_mismatch'}
        r = await change_documents(store,'uploads-102',request('replace_invoice',2),out,archive,runner)
        assert r['state']['revision'] == 3
        assert r['state']['status'] == 'ready_for_human_review'
        assert r['state']['reconciliation']['amounts']['billed'] == '306.00'
        assert r['state']['reconciliation']['amounts']['unsupported'] == '0'
        assert len(r['uploads']) == 5 and len(r['active_document_ids']) == 4
        assert 'case-102-inv-8201' not in r['active_document_ids']
        assert r['history'][0]['state'] == original
        assert len(r['history']) == len(r['document_events']) == 2
        assert len(calls) == 3
        saved = CaseStore(store.path).load('uploads-102')[1]
        assert saved == r
        for u in saved['uploads'].values():
            assert hashlib.sha256(Path(u['file_path']).read_bytes()).hexdigest() == u['sha256']
    asyncio.run(check())


@pytest.mark.parametrize('fault',['stale','unconfirmed','wrong_target','same_id','wrong_type','duplicate','stopped'])
def test_invalid_change_does_not_modify_saved_case_or_call_model(tmp_path,fault):
    store,archive,out,runner,calls,manifest,request = setup(tmp_path)
    async def check():
        await start_documents(store,manifest,ROOT/'demo',archive,runner)
        req = request('replace_invoice',1)
        if fault == 'stale':req['expected_revision'] = 2
        if fault == 'unconfirmed':req['confirmed'] = False
        if fault == 'wrong_target':req['documents'][0]['replaces'] = 'nonexistent'
        if fault == 'same_id':req['documents'][0]['document_id'] = 'case-102-inv-8201'
        if fault == 'wrong_type':req['documents'][0]['document_type'] = 'receipt'
        if fault == 'duplicate':req['documents'][0]['file_path'] = str(ROOT/'eval/rendered/case-102/case-102-inv-8201.pdf')
        if fault == 'stopped':
            v,r = store.load('uploads-102');r['state']['status']='stopped';store.save(r,v)
        before = store.load('uploads-102')
        with pytest.raises(ValueError):
            await change_documents(store,'uploads-102',req,out,archive,runner)
        assert store.load('uploads-102') == before
        assert len(calls) == 1
    asyncio.run(check())


def test_archive_tampering_blocks_before_runner(tmp_path):
    store,archive,out,runner,calls,manifest,request = setup(tmp_path)
    async def check():
        r = await start_documents(store,manifest,ROOT/'demo',archive,runner)
        Path(next(iter(r['uploads'].values()))['file_path']).write_bytes(b'changed')
        r = await change_documents(store,'uploads-102',request('add_receipt',1),out,archive,runner)
        assert r['state']['status'] == 'blocked'
        assert r['state']['revision'] == 2
        assert r['state']['reconciliation'] is None
        assert len(calls) == 1
    asyncio.run(check())


def test_failed_revision_invalidates_old_results_and_retry_uses_new_sources(tmp_path):
    store,archive,out,runner,calls,manifest,request = setup(tmp_path)
    async def broken(data):raise RuntimeError('secret provider detail')
    async def check():
        await start_documents(store,manifest,ROOT/'demo',archive,runner)
        r = await change_documents(store,'uploads-102',request('add_receipt',1),out,archive,broken)
        assert r['state']['status'] == 'blocked' and r['state']['reconciliation'] is None
        assert 'secret provider detail' not in json.dumps(r)
        r = await retry_documents(store,'uploads-102',2,runner)
        assert r['state']['reconciliation']['amounts']['unsupported'] == '34.00'
        assert len(r['history']) == 1
    asyncio.run(check())


def test_existing_case_cannot_be_restarted(tmp_path):
    store,archive,out,runner,calls,manifest,request = setup(tmp_path)
    async def check():
        await start_documents(store,manifest,ROOT/'demo',archive,runner)
        with pytest.raises(ValueError):await start_documents(store,manifest,ROOT/'demo',archive,runner)
        assert len(calls) == 1
    asyncio.run(check())


def test_legacy_quantity_edit_cannot_bypass_archived_source_workflow(tmp_path):
    from src.case_service import correct_receipt_quantity
    store,archive,out,runner,calls,manifest,request = setup(tmp_path)
    async def check():
        await start_documents(store,manifest,ROOT/'demo',archive,runner)
        before = store.load('uploads-102')
        with pytest.raises(ValueError, match='source-document'):
            await correct_receipt_quantity(store,None,'uploads-102',{
                'expected_revision':1,'document_id':'case-102-rcv-8201','line_id':'2',
                'old_value':'30','new_value':'40','staff':'Test','reason':'Invalid flow',
                'source_confirmation':'Test','confirmed':True})
        assert store.load('uploads-102') == before
    asyncio.run(check())
