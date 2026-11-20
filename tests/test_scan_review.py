import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest
from src.case_store import CaseStore
from src.graph import initial_state
from src.scan_review import review_packet, verify_scans
import hashlib


def setup_case(tmp_path):
    case = json.loads((Path(__file__).resolve().parents[1]/'eval/cases/case-101.json').read_text())
    docs = case['documents']
    for doc in docs:
        doc['business_document_number'] = doc['business_document_number'] or doc['document_id']
        doc['supplier_id'] = docs[0]['supplier_id']
    state = initial_state(case['case_id'])
    state.update(documents=docs, status='needs_correction', stages=['extract'])
    notices = [{'code':'evidence_unverified', 'owner':'extract', 'document_id':d['document_id'],
                'severity':'review', 'explanation':'Verify scan'} for d in docs]
    state.update(issues=notices, extraction_notices=notices)
    uploads = {}
    for i,d in enumerate(docs):
        p = tmp_path/f'{i}.png';p.write_bytes(b'fake-source-'+str(i).encode())
        uploads[d['document_id']] = {'document_id':d['document_id'], 'document_type':d['document_type'],
            'file_path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    record = {'kind':'document_case','state':state,'uploads':uploads,'active_document_ids':list(uploads),
              'supplier_directory':{'Supplier':docs[0]['supplier_id']},'history':[]}
    store = CaseStore(tmp_path/'test.sqlite');store.create(record)
    packet = review_packet(record)
    packet.update(staff='Tester',reason='Compared every field against source images',confirmed=True)
    return store, record, packet


def test_scan_confirmation_reruns_and_preserves_original_price(tmp_path):
    store, record, packet = setup_case(tmp_path)
    original = deepcopy(record['state'])
    packet['documents'][0]['line_items'][0]['unit_price'] = '850'
    result = asyncio.run(verify_scans(store, record['state']['case_id'], packet))
    assert result['state']['revision'] == 2
    assert result['history'][0]['state'] == original
    assert result['state']['status'] != 'ready_for_human_review'
    assert 'reconcile' in result['state']['stages']
    assert result['scan_reviews'][0]['staff'] == 'Tester'
    assert store.load(record['state']['case_id'])[1] == result


def test_verified_clean_documents_proceed_without_model_calls(tmp_path):
    store, record, packet = setup_case(tmp_path)
    result = asyncio.run(verify_scans(store, record['state']['case_id'], packet))
    assert result['state']['status'] == 'ready_for_human_review'
    assert result['state']['mode'] == 'human_verified_extraction'


@pytest.mark.parametrize('change', ['stale','unconfirmed','hash','identity','missing'])
def test_invalid_confirmation_does_not_change_saved_case(tmp_path, change):
    store, record, packet = setup_case(tmp_path)
    if change == 'stale': packet['expected_revision'] = 2
    if change == 'unconfirmed': packet['confirmed'] = False
    if change == 'hash': packet['source_hashes'] = {}
    if change == 'identity': packet['documents'][0]['case_id'] = 'other'
    if change == 'missing': packet['documents'][0]['line_items'][0]['quantity'] = None
    with pytest.raises(ValueError):
        asyncio.run(verify_scans(store, record['state']['case_id'], packet))
    assert store.load(record['state']['case_id'])[1] == record


def test_other_warnings_cannot_be_waived(tmp_path):
    store, record, packet = setup_case(tmp_path)
    v, record = store.load(record['state']['case_id'])
    record['state']['extraction_notices'].append({'code':'po_link_unresolved','owner':'extract',
        'document_id':packet['documents'][1]['document_id'],'severity':'review','explanation':'Check PO'})
    store.save(record,v)
    result = asyncio.run(verify_scans(store, record['state']['case_id'], packet))
    assert result['state']['status'] == 'needs_correction'
    assert result['state']['stages'] == ['extract']
