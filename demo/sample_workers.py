"""Synthetic extraction/matching/review plus a demo-only quantity check."""
from decimal import Decimal
from demo.contracts_example import sample_documents, sample_matches
from src.graph import Workers


def make_sample_workers(scenario: str) -> Workers:
    if scenario not in {"clean", "short-delivery"}:
        raise ValueError("Unknown sample scenario")

    async def extract(state):
        docs = [d.model_dump() for d in sample_documents()]
        for doc in docs:
            doc.update(case_id=state["case_id"], revision=state["revision"])
        if scenario == "clean":
            docs[-1]["line_items"][0]["quantity"] = "20"
            docs[-1]["line_items"][0]["evidence"][0]["text"] = "MON-24 | 24-inch monitor | 20 each"
        return docs

    async def match(state):
        matches = [m.model_dump() for m in sample_matches()]
        for result in matches:
            result.update(case_id=state["case_id"], revision=state["revision"])
        return matches

    async def reconcile(state):
        refs = [m["source"] for m in state["matches"]]
        by_kind = {d['document_type']: d for d in state['documents']}
        if len(state['documents']) != 3 or any(len(d['line_items']) != 1 for d in state['documents']):
            raise ValueError('Demo checker supports only one line in each of three documents')
        po, invoice, receipt = [by_kind[k]['line_items'][0] for k in ('purchase_order', 'invoice', 'receipt')]
        if any(l['sku'] != 'MON-24' or l['unit'] != 'each' or l['quantity'] is None for l in (po, invoice, receipt)):
            raise ValueError('Unsupported demo item/unit or missing quantity')
        if po['unit_price'] != '125.50' or invoice['unit_price'] != '125.50':
            raise ValueError('Unsupported demo price')
        discrepancies = []
        for label, expected, observed in [('ordered versus invoiced', po['quantity'], invoice['quantity']),
                                           ('invoiced versus received', invoice['quantity'], receipt['quantity'])]:
            if Decimal(expected) != Decimal(observed):
                discrepancies.append({'code': 'quantity_mismatch', 'lines': refs,
                    'expected': expected, 'observed': observed,
                    'explanation': f'Demo quantity check: {label} differs.'})
        return {'case_id': state['case_id'], 'revision': state['revision'],
                'status': 'needs_review' if discrepancies else 'clean',
                'checked_lines': refs, 'discrepancies': discrepancies}

    async def review(state):
        return {"case_id": state["case_id"], "revision": state["revision"], "findings": []}

    return Workers(extract=extract, match=match, reconcile=reconcile, review=review)
