from copy import deepcopy
from fastapi.testclient import TestClient
from src.case_store import CaseStore
from src.web.app import create_app
from tests.test_web_app import C101, start
from src.web.sample_records import make_sample_runner


def test_web_scan_review_saves_history_and_rejects_stale_packet(tmp_path):
    original = make_sample_runner()
    async def paused(manifest):
        state = await original(manifest)
        notices = [{'code':'evidence_unverified','owner':'extract','document_id':d['document_id'],
                    'severity':'review','explanation':'Compare source image'} for d in state['documents']]
        state.update(status='needs_correction', issues=notices, extraction_notices=notices,
                     stages=['extract'], matches=[], reconciliation=None, review=None)
        return state
    client=TestClient(create_app(tmp_path,mode='sample',runner=paused))
    c=start(client,C101,'po-3001.pdf','inv-8101.pdf','rcv-8101.pdf','Scan web').json()
    assert c['actions']['verify_scans'] and c['amounts'] is None
    packet=client.get('/api/cases/scan-web/scan-review').json()
    before=deepcopy(c)
    packet.update(staff='Test staff', reason='Synthetic test source verification', confirmed=False)
    assert client.post('/api/cases/scan-web/scan-review',json=packet).status_code==400
    assert client.get('/api/cases/scan-web').json()==before
    packet['confirmed']=True
    result=client.post('/api/cases/scan-web/scan-review',json=packet)
    assert result.status_code==200,result.text
    c=result.json()
    assert c['revision']==2 and c['status']=='ready_for_human_review'
    assert not c['actions']['verify_scans']
    assert c['revisions'][-1]['scan_review']['staff']=='Test staff'
    assert c['amounts']['billed']=='512.50'
    assert client.post('/api/cases/scan-web/scan-review',json=packet).status_code==400
    assert client.get('/api/cases/scan-web').json()==c
    record=CaseStore(tmp_path/'document_cases.sqlite').load('scan-web')[1]
    assert len(record['scan_reviews'])==1


def test_browser_assets_include_scan_form(tmp_path):
    client=TestClient(create_app(tmp_path,mode='sample'))
    assert 'scan-review.js' in client.get('/').text
    assert client.get('/scan-review.js').status_code==200
