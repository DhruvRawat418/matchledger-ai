"""Create synthetic source documents for case 102's resolution demo.

These are new demo evidence, never edits to the original source documents.
"""
import json
from pathlib import Path
from src.contracts import ExtractedDocument
from src.extraction.render import render_document

ROOT = Path(__file__).resolve().parents[1]


def prepare(out=None):
    out = out or ROOT/'demo/resolution_102'
    out.mkdir(parents=True, exist_ok=True)
    case = json.loads((ROOT/'eval/cases/case-102.json').read_text())
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
    for doc in (receipt, invoice):
        render_document(doc, 'PO-3002', 'classic', out/f'{doc.document_id}.pdf')
    for name, doc, action, replaces in [('add_receipt',receipt,'add',None),
                                       ('replace_invoice',invoice,'replace','case-102-inv-8201')]:
        request = {'documents':[{'action':action, 'document_id':doc.document_id,
            'document_type':doc.document_type,'file_path':f'{doc.document_id}.pdf','replaces':replaces}]}
        (out/f'{name}.json').write_text(json.dumps(request,indent=2)+'\n')
    return originals, receipt, invoice


if __name__ == '__main__':
    prepare()
    print('Prepared two synthetic PDFs and change requests in demo/resolution_102.')
