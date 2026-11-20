"""Regression for the live DN-8202 row copied into its description."""
import asyncio
import json
import pytest
from src.extraction.normalize import receipt_description
from src.extraction import ExtractionRequest, FakeModelClient, Page, extract_document
from src.matching import match_lines
from eval.ground_truth import load_case, snapshot, CASES_DIR
from src.reconciliation_adapter import reconcile_worker


@pytest.mark.parametrize('product', ['A4 copy paper 500sheets','27-inch monitor','Model X10, pack of 24'])
def test_only_explicit_quantity_and_status_columns_are_removed(product):
    text = f'{product} | 10 each | received'
    assert receipt_description(text,text,'10','each') == product


@pytest.mark.parametrize('text,qty,unit', [
    ('A4 paper 500sheets 10 each received','10','each'),
    ('A4 paper | 12 each | received','10','each'),
    ('A4 paper | 10 boxes | received','10','each'),
    ('A4 paper | 10 each | returned','10','each'),
    ('A4 paper | 10 each | received',None,'each'),
    ('A4 paper | 10 each | received','10',None)])
def test_ambiguous_or_conflicting_columns_are_not_guessed(text,qty,unit):
    assert receipt_description(text,text,qty,unit) == text


def test_live_receipt_shape_reaches_math_without_weakening_identity_guard():
    text = 'A4 copy paper 500sheets | 10 each | received'
    reply = {'document_type':'receipt','business_document_number':'DN-8202',
             'supplier_name':'Synthetic Office Supply Co.','referenced_po_number':'PO-3002','currency':None,
             'line_items':[{'description':text,'sku':None,'quantity':'10','unit':'each',
                           'unit_price':None,'evidence_page':1,'evidence_text':text}]}
    async def check():
        outcome = await extract_document(ExtractionRequest(case_id='case-102',revision=2,
            document_id='case-102-rcv-8202',file_path='unused.pdf',declared_type='receipt',
            supplier_directory={'Synthetic Office Supply Co.':'SUP-SYNTH'}),
            FakeModelClient([json.dumps(reply)]),pages=[Page(1,b'fake','image/png',text)])
        assert not outcome.needs_review
        assert outcome.document.line_items[0].description == 'A4 copy paper 500sheets'
        assert outcome.document.line_items[0].evidence[0].text == text
        assert any(i.code == 'description_columns_separated' and i.severity == 'info' for i in outcome.issues)
        docs = snapshot(load_case(CASES_DIR/'case-102.json'),1)
        for d in docs:d.revision=2
        outcome.document.purchase_order_id = docs[0].document_id
        docs.append(outcome.document)
        matches = await match_lines(docs)
        assert all(m.decision == 'matched' for m in matches)
        result = await reconcile_worker({'case_id':'case-102','revision':2,
            'documents':[d.model_dump() for d in docs],'matches':[m.model_dump() for m in matches]})
        assert {d['code'] for d in result['discrepancies']} == {'price_mismatch'}
        assert result['amounts']['unsupported'] == '34.00'
        outcome.document.line_items[0].description = 'A4 copy paper 250sheets'
        assert (await match_lines(docs))[-1].decision != 'matched'
    asyncio.run(check())
