"""Web app tests: the HTTP layer over the saved document workflow.

Extraction is the bundled-sample substitution (as in test_document_cases.py);
matching, reconciliation and review are the real workers.
"""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from demo.prepare_resolution import prepare
from src.web import sample_records
from src.web.app import ROOT, create_app

C102 = 'eval/rendered/case-102/case-102-'
C101 = 'eval/rendered/case-101/case-101-'
RECEIPT_2 = 'demo/resolution_102/case-102-rcv-8202.pdf'
INVOICE_2 = 'demo/resolution_102/case-102-inv-8201-corrected.pdf'


def pdf(path):
    return (Path(path).name, (ROOT / path).read_bytes(), 'application/pdf')


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, mode='sample'))


def start(client, prefix, po, inv, rcv, name):
    return client.post('/api/cases', data={'name': name}, files=[
        ('purchase_order', pdf(prefix + po)), ('invoice', pdf(prefix + inv)), ('receipts', pdf(prefix + rcv))])


def change(client, case_id, action, path, revision, **overrides):
    data = {'action': action, 'expected_revision': revision, 'staff': 'Test staff',
            'reason': 'Synthetic source update', 'confirmed': 'true', **overrides}
    return client.post(f'/api/cases/{case_id}/documents', data=data, files={'file': pdf(path)})


def test_clean_case_passes_with_matched_items_and_totals(client):
    view = start(client, C101, 'po-3001.pdf', 'inv-8101.pdf', 'rcv-8101.pdf', 'Clean').json()
    assert view['status'] == 'ready_for_human_review' and view['findings'] == []
    assert view['amounts']['unsupported'] == '0' and view['amounts']['billed'] == view['amounts']['supported']
    assert view['items'] and all(i['checked'] and i['ordered'] == i['received'] == i['invoiced'] for i in view['items'])
    assert all({s['document_type'] for s in i['sources']} == {'invoice', 'receipt'} for i in view['items'])


def test_discrepancy_case_resolves_through_added_receipt_and_corrected_invoice(client):
    view = start(client, C102, 'po-3002.pdf', 'inv-8201.pdf', 'rcv-8201.pdf', 'Paper order').json()
    assert view['case_id'] == 'paper-order' and view['status'] == 'needs_correction'
    assert view['amounts']['unsupported'] == '85.00'
    assert [f['title'] for f in view['findings']] == ['Price mismatch', 'Missing delivery', 'Billed more than received']
    paper = next(i for i in view['items'] if i['sku'] == 'PPR-A4')
    assert (paper['ordered'], paper['received'], paper['invoiced']) == ('40', '30', '40')
    assert (paper['po_price'], paper['invoice_price']) == ('5.10', '5.95')
    assert 'paper-order' not in paper['calculation'] and 'DN-8201' in paper['calculation']

    view = change(client, 'paper-order', 'add_receipt', RECEIPT_2, 1).json()
    assert view['revision'] == 2 and view['amounts']['unsupported'] == '34.00'
    assert [f['title'] for f in view['findings']] == ['Price mismatch']

    view = change(client, 'paper-order', 'replace_invoice', INVOICE_2, 2).json()
    assert view['revision'] == 3 and view['status'] == 'ready_for_human_review'
    assert view['amounts'] == {'currency': 'USD', 'billed': '306.00', 'supported': '306.00', 'unsupported': '0', 'unbilled': '0'}
    assert view['findings'] == []
    assert [(r['revision'], r['amounts']['unsupported'], r['finding_count']) for r in view['revisions']] == [
        (1, '85.00', 3), (2, '34.00', 1), (3, '0', 0)]
    replaced = [d for d in view['documents'] if not d['active']]
    assert [d['document_number'] for d in replaced] == ['INV-8201']
    assert len(view['documents']) == 5 and view['revisions'][2]['event']['staff'] == 'Test staff'
    assert client.get('/api/cases/paper-order').json() == view
    assert [c['case_id'] for c in client.get('/api/cases').json()] == ['paper-order']
    original = client.get(f"/api/cases/paper-order/documents/{replaced[0]['document_id']}/file")
    assert original.status_code == 200 and original.content == (ROOT / (C102 + 'inv-8201.pdf')).read_bytes()


@pytest.mark.parametrize('overrides,path', [
    ({'expected_revision': 2}, RECEIPT_2),            # stale revision
    ({'confirmed': 'false'}, RECEIPT_2),              # not confirmed
    ({'staff': ' '}, RECEIPT_2),                      # no name
    ({'action': 'retire'}, RECEIPT_2),                # unknown action
    ({}, C102 + 'rcv-8201.pdf'),                      # same file again
    ({'action': 'replace_invoice'}, RECEIPT_2),       # a receipt offered as the invoice
])
def test_refused_change_leaves_the_saved_case_alone(client, overrides, path):
    before = start(client, C102, 'po-3002.pdf', 'inv-8201.pdf', 'rcv-8201.pdf', 'Paper order').json()
    response = change(client, 'paper-order', overrides.pop('action', 'add_receipt'), path, 1, **overrides)
    assert response.status_code == 400 and response.json()['detail']
    assert client.get('/api/cases/paper-order').json() == before


def test_sample_mode_refuses_other_files_before_saving_anything(client):
    response = client.post('/api/cases', data={'name': 'Mine'}, files=[
        ('purchase_order', ('mine.pdf', b'%PDF-1.4 not a sample', 'application/pdf')),
        ('invoice', pdf(C102 + 'inv-8201.pdf')), ('receipts', pdf(C102 + 'rcv-8201.pdf'))])
    assert response.status_code == 400 and 'MATCHLEDGER_PROVIDER' in response.json()['detail']
    assert client.get('/api/cases').json() == []
    assert client.get('/api/cases/mine').status_code == 404


def test_existing_case_name_is_refused(client):
    assert start(client, C101, 'po-3001.pdf', 'inv-8101.pdf', 'rcv-8101.pdf', 'Same').status_code == 200
    again = start(client, C102, 'po-3002.pdf', 'inv-8201.pdf', 'rcv-8201.pdf', 'Same')
    assert again.status_code == 400 and client.get('/api/cases/same').json()['po_number'] == 'PO-3001'


def test_failed_run_is_saved_blocked_without_provider_text_and_can_be_retried(tmp_path):
    real = sample_records.make_sample_runner()
    calls = []

    async def flaky(manifest):
        calls.append(manifest['revision'])
        if len(calls) == 1:
            raise RuntimeError('secret provider detail')
        return await real(manifest)
    client = TestClient(create_app(data_dir=tmp_path, mode='sample', runner=flaky))
    response = start(client, C101, 'po-3001.pdf', 'inv-8101.pdf', 'rcv-8101.pdf', 'Retry')
    view = response.json()
    assert view['status'] == 'blocked' and view['amounts'] is None and view['actions']['retry']
    assert 'secret provider detail' not in response.text
    view = client.post('/api/cases/retry/retry', data={'expected_revision': 1}).json()
    assert view['status'] == 'ready_for_human_review' and view['revision'] == 1


def test_sample_records_match_the_resolution_fixtures(tmp_path):
    """The offline records for demo/resolution_102 must equal what prepare_resolution builds."""
    _, receipt, invoice = prepare(tmp_path / 'fixtures')
    known = sample_records.known_records()
    for doc in (receipt, invoice):
        committed = ROOT / 'demo/resolution_102' / f'{doc.document_id}.pdf'
        assert known[sample_records._sha(committed)] == doc


def test_page_and_config_are_served(client):
    assert client.get('/').status_code == 200 and 'MatchLedger' in client.get('/').text
    config = client.get('/api/config').json()
    assert config['mode'] == 'sample' and [s['id'] for s in config['samples']] == ['clean', 'discrepancy']
    for sample in config['samples']:
        for name in [*sample['files'].values(), *sample['follow_ups'].values()]:
            assert client.get(f'/api/samples/{name}').content.startswith(b'%PDF')
    assert client.get('/api/samples/..%2F.env').status_code == 404
