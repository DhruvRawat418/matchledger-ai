"""Offline runner for the bundled synthetic PDFs.

Used only when no extraction provider is configured. Each bundled sample file
is recognised by its SHA-256 and paired with its record from eval/cases (the
same substitution tests/test_document_cases.py makes). Matching, reconciliation
and review are the real workers. Nothing is read from the PDF in this mode, so
it says nothing about extraction quality, and any other file is refused.
"""
import hashlib
import json
from pathlib import Path
from src.contracts import ExtractedDocument
from src.graph import Workers, build_graph, initial_state
from src.integrated_workers import deterministic_review
from src.matching import make_match_worker
from src.reconciliation_adapter import reconcile_worker

ROOT = Path(__file__).resolve().parents[2]
MODE = 'sample_records'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolution_102(case):
    """Records for demo/resolution_102, built the way demo/prepare_resolution.py
    builds them (tests/test_web_app.py checks the two stay equal)."""
    originals = [ExtractedDocument.model_validate(d) for d in case['documents']]
    receipt = next(d for d in originals if d.document_type == 'receipt').model_copy(deep=True)
    receipt.document_id = 'case-102-rcv-8202'
    receipt.business_document_number = 'DN-8202'
    receipt.line_items = [receipt.line_items[1]]
    receipt.line_items[0].quantity = '10'
    receipt.line_items[0].evidence[0].text = 'A4 copy paper 500sheets | 10 each | received'
    invoice = next(d for d in originals if d.document_type == 'invoice').model_copy(deep=True)
    invoice.document_id = 'case-102-inv-8201-corrected'
    invoice.business_document_number = 'INV-8201-CORRECTED'
    invoice.line_items[1].unit_price = '5.10'
    invoice.line_items[1].evidence[0].text = 'a4 copy-paper (500 Sheets) -NEW | 40 each | $5.10'
    return [receipt, invoice]


def known_records(root=ROOT):
    """sha256 of a bundled sample file -> its ExtractedDocument record."""
    known = {}
    for case_file in sorted((root / 'eval/cases').glob('case-*.json')):
        case = json.loads(case_file.read_text())
        for raw in case['documents']:
            pdf = root / 'eval/rendered' / case['case_id'] / f"{raw['document_id']}.pdf"
            if pdf.exists():
                known[_sha(pdf)] = ExtractedDocument.model_validate(raw)
        if case['case_id'] == 'case-102':
            for doc in _resolution_102(case):
                pdf = root / 'demo/resolution_102' / f'{doc.document_id}.pdf'
                if pdf.exists():
                    known[_sha(pdf)] = doc
    return known


def make_sample_runner(known=None):
    known = known_records() if known is None else known

    async def runner(manifest):
        uploads = manifest['documents']
        po_id = next((u['document_id'] for u in uploads if u['document_type'] == 'purchase_order'), None)
        documents = []
        for upload in uploads:
            record = known.get(_sha(upload['file_path']))
            if record is None or record.document_type != upload['document_type']:
                raise ValueError('Not a bundled sample file of the declared type')
            doc = record.model_dump()
            doc.update(case_id=manifest['case_id'], revision=manifest['revision'], document_id=upload['document_id'])
            if doc['document_type'] != 'purchase_order':
                doc['purchase_order_id'] = po_id
            documents.append(doc)

        async def extract(state):
            return documents
        graph = build_graph(Workers(extract, make_match_worker(), reconcile_worker, deterministic_review))
        state = await graph.ainvoke(initial_state(manifest['case_id'], manifest['revision']))
        return {'mode': MODE, 'semantic_matching': False, 'review_mode': 'deterministic_boundary_audit', **state}
    return runner
