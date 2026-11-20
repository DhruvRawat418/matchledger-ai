"""Connect uploaded documents to the workflow without losing extraction warnings."""
from src.contracts import Contract, ExtractedDocument, Identifier
from src.extraction.schema import ExtractionRequest
from src.extraction.extractor import extract_document
from src.extraction.crosscheck import check_case, _exact
from pydantic import Field
from typing import Literal


class ExtractionNotice(Contract):
    code: Identifier
    owner: Literal['extract'] = 'extract'
    document_id: Identifier
    severity: Literal['review', 'info']
    explanation: Identifier


class ExtractionBatch(Contract):
    documents: list[ExtractedDocument]
    issues: list[ExtractionNotice] = Field(default_factory=list)


def make_extraction_worker(requests: list[ExtractionRequest], client):
    """Requests bind files to a case/revision; stale requests fail before API calls."""
    async def worker(state):
        if not requests or any((r.case_id, r.revision) != (state['case_id'], state['revision']) for r in requests):
            raise ValueError('Upload requests must match the active case revision')
        if len({r.document_id for r in requests}) != len(requests):
            raise ValueError('Upload document IDs must be unique')
        outcomes = [await extract_document(r, client) for r in requests]
        additional = check_case(outcomes)
        pos = [o.document for o in outcomes if o.document and o.document.document_type == 'purchase_order']
        notices, docs = [], []
        for req, outcome in zip(requests, outcomes):
            for issue in [*outcome.issues, *additional.get(req.document_id, [])]:
                notices.append(ExtractionNotice(code=issue.code, document_id=req.document_id,
                    severity=issue.severity, explanation=issue.message))
            if outcome.document is None:
                continue
            doc = outcome.document.model_copy(deep=True)
            if doc.document_type != 'purchase_order':
                reference = outcome.referenced_po_number
                linked = [p for p in pos if reference and p.business_document_number
                          and _exact(reference) == _exact(p.business_document_number)]
                if len(linked) == 1:
                    doc.purchase_order_id = linked[0].document_id
                else:
                    notices.append(ExtractionNotice(code='po_link_unresolved', document_id=doc.document_id,
                        severity='review', explanation='Confirm the printed PO reference against the uploaded purchase order.'))
            docs.append(doc)
        return ExtractionBatch(documents=docs, issues=notices).model_dump()
    return worker
