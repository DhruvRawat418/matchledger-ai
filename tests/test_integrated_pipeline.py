"""Cross-component regressions. Model responses are scripted; no API calls."""
import asyncio
import json
from copy import deepcopy
import pytest
from demo.contracts_example import sample_documents
from eval.ground_truth import CASES_DIR, load_case, snapshot
from src.contracts import ExtractedDocument
from src.graph import Workers, build_graph, initial_state
from src.integrated_workers import deterministic_review, make_upload_workers
from src.matching import match_lines, make_match_worker
from src.llm_matcher import LlmError, parse_judgement
from src.reconciliation_adapter import reconcile_worker
from src.extraction import FakeModelClient, ExtractionRequest


def test_size_conflict_never_selects_wrong_po_even_same_sku():
    for sku in ('MON-27', 'MON-24', None):
        docs = sample_documents()
        docs[1].line_items[0].sku = sku
        docs[1].line_items[0].description = '27-inch monitor'
        matches = asyncio.run(match_lines(docs))
        assert matches[0].decision != 'matched'


def test_cleaned_equality_tie_is_not_resolved_by_input_order():
    docs = sample_documents()
    docs[1].line_items[0].sku = None
    docs[1].line_items[0].description = '24 INCH MONITOR'
    second = docs[0].line_items[0].model_copy(deep=True)
    second.line_id = 'second'
    second.sku = 'OTHER'
    second.description = '24 inch monitor'
    docs[0].line_items.append(second)
    for _ in range(2):
        assert asyncio.run(match_lines(docs))[0].decision != 'matched'
        docs[0].line_items.reverse()


@pytest.mark.parametrize('confidence', [100, -1, True, float('inf'), float('nan')])
def test_invalid_model_confidence_is_rejected(confidence):
    with pytest.raises(LlmError):
        parse_judgement(json.dumps({'candidate': 'po::1', 'confidence': confidence}), frozenset({'po::1'}))


def test_explicit_wrong_po_link_does_not_fall_back_to_other_pos():
    docs = sample_documents()
    docs[1].purchase_order_id = 'unknown-po'
    assert asyncio.run(match_lines(docs))[0].decision != 'matched'


@pytest.mark.parametrize('name', ['case-101', 'case-102', 'case-104', 'case-105'])
def test_ground_truth_documents_through_real_matcher_and_engine(name):
    case = load_case(CASES_DIR / f'{name}.json')
    async def check():
        for revision in case.revisions:
            docs = snapshot(case, revision.revision)
            raw = [d.model_dump() for d in docs]
            before = deepcopy(raw)
            async def extract(state):
                return raw
            graph = build_graph(Workers(extract, make_match_worker(), reconcile_worker, deterministic_review))
            out = await graph.ainvoke(initial_state(case.case_id, revision.revision))
            if name == 'case-104' and revision.revision in (2, 3):
                assert out['status'] == 'needs_correction'
                assert out['issues'][0]['code'] == 'duplicate_document'
                assert 'reconcile' not in out['stages']  # never sum conflicting invoices
            else:
                expected = revision.expected_reconciliation
                assert out['reconciliation']['status'] == expected.status
                assert {d['code'] for d in out['reconciliation']['discrepancies']} == {d.code for d in expected.discrepancies}
            assert raw == before
    asyncio.run(check())


def pdf_uploads(tmp_path, bad_quote=False):
    from reportlab.pdfgen.canvas import Canvas
    requests, replies = [], []
    for doc_id, kind, number in [('po', 'purchase_order', 'PO-1'), ('inv', 'invoice', 'INV-1'), ('rec', 'receipt', 'DN-1')]:
        text = 'MON-24 24-inch monitor 20 each' + (' 125.50' if kind != 'receipt' else '')
        path = tmp_path / f'{doc_id}.pdf'
        canvas = Canvas(str(path)); canvas.drawString(30, 750, text); canvas.save()
        requests.append(ExtractionRequest(case_id='pdf-case', revision=1, document_id=doc_id,
            file_path=path, declared_type=kind, supplier_directory={'Acme': 'SUP-ACME'}))
        replies.append(json.dumps({'document_type':kind, 'business_document_number':number,
            'supplier_name':'Acme', 'referenced_po_number':None if kind == 'purchase_order' else 'PO-1',
            'currency':None if kind == 'receipt' else 'USD',
            'line_items':[{'description':'24-inch monitor','sku':'MON-24','quantity':'20','unit':'each',
                'unit_price':None if kind == 'receipt' else '125.50','evidence_page':1,
                'evidence_text':text if not bad_quote or doc_id != 'inv' else 'fabricated quote'}]}))
    return requests, FakeModelClient(replies)


@pytest.mark.parametrize('bad_quote', [False, True])
def test_real_pdf_processing_preserves_extraction_review_gate(tmp_path, bad_quote):
    requests, client = pdf_uploads(tmp_path, bad_quote)
    graph = build_graph(make_upload_workers(requests, client))
    result = asyncio.run(graph.ainvoke(initial_state('pdf-case')))
    assert len(client.calls) == 3
    if bad_quote:
        assert result['status'] == 'needs_correction'
        assert result['stages'] == ['extract']
        assert any(i['code'] == 'evidence_not_found' for i in result['issues'])
    else:
        assert result['status'] == 'ready_for_human_review'
        assert result['documents'][1]['purchase_order_id'] == 'po'
        assert result['reconciliation']['amounts']['billed'] == '2510.00'


def test_po_line_ids_differing_only_in_case_do_not_merge_totals():
    docs = sample_documents()
    for doc in docs:
        line = doc.line_items[0]
        line.line_id = 'a'
        line.quantity = '20'
        second = line.model_copy(deep=True)
        second.line_id = 'A'
        second.sku = 'MON-27'
        second.description = '27-inch monitor'
        second.quantity = '3'
        doc.line_items.append(second)
    async def check():
        matches = await match_lines(docs)
        result = await reconcile_worker({'case_id': docs[0].case_id, 'revision': 1,
            'documents':[d.model_dump() for d in docs], 'matches':[m.model_dump() for m in matches]})
        assert result['status'] == 'clean'
        assert {q['po_line']['line_id']:q['received'] for q in result['quantities']} == {'a':'20', 'A':'3'}
    asyncio.run(check())
