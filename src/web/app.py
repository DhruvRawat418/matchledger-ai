"""Local single-operator web app over the saved document workflow.

    python -m uvicorn src.web.app:app --port 8000

It only wraps src/document_cases.py: uploads are archived by fingerprint, every
change creates a new revision, and every revision reruns all checks. A passing
result means "ready for a person to review", never approval to pay.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from src.case_store import CaseConflict, CaseStore
from src.document_cases import change_documents, retry_documents, start_documents
from src.extraction.providers import load_dotenv
from src.web import sample_records
from src.web.views import case_summary, case_view

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / 'static'
MAX_BYTES = 20 * 1024 * 1024
MEDIA = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg'}

SAMPLE_SETS = [
    {'id': 'clean', 'label': 'Clean case', 'note': 'PO-3001: order, invoice and delivery agree.',
     'files': {'purchase_order': 'eval/rendered/case-101/case-101-po-3001.pdf',
               'invoice': 'eval/rendered/case-101/case-101-inv-8101.pdf',
               'receipt': 'eval/rendered/case-101/case-101-rcv-8101.pdf'}},
    {'id': 'discrepancy', 'label': 'Price mismatch and missing delivery',
     'note': 'PO-3002: paper billed at 5.95 against 5.10, and 30 of 40 delivered.',
     'files': {'purchase_order': 'eval/rendered/case-102/case-102-po-3002.pdf',
               'invoice': 'eval/rendered/case-102/case-102-inv-8201.pdf',
               'receipt': 'eval/rendered/case-102/case-102-rcv-8201.pdf'},
     'follow_ups': {'add_receipt': 'demo/resolution_102/case-102-rcv-8202.pdf',
                    'replace_invoice': 'demo/resolution_102/case-102-inv-8201-corrected.pdf'}},
]
SAMPLE_PATHS = {Path(p).name: p for s in SAMPLE_SETS for p in [*s['files'].values(), *s.get('follow_ups', {}).values()]}


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:60]


def create_app(data_dir=None, mode=None, runner=None):
    """mode: 'live' (configured extraction model) or 'sample' (bundled files only).
    Default: live when MATCHLEDGER_PROVIDER is set, otherwise sample."""
    load_dotenv()
    data_dir = Path(data_dir or os.getenv('MATCHLEDGER_DATA_DIR') or ROOT / 'outputs')
    mode = mode or os.getenv('MATCHLEDGER_UI_MODE') or ('live' if os.getenv('MATCHLEDGER_PROVIDER') else 'sample')
    if mode not in {'live', 'sample'}:
        raise ValueError('MATCHLEDGER_UI_MODE must be live or sample')
    store = CaseStore(data_dir / 'document_cases.sqlite')
    archive = data_dir / 'document_archive'
    known = sample_records.known_records() if mode == 'sample' else {}
    if runner is None:
        if mode == 'live':
            from demo.run_uploads import run_data
            runner = run_data
        else:
            runner = sample_records.make_sample_runner(known)
    supplier_directory = json.loads((ROOT / 'demo/upload_manifest.json').read_text())['supplier_directory']
    lock = asyncio.Lock()  # one run at a time: the service is single-operator
    app = FastAPI(title='MatchLedger')

    async def stage(folder, upload, document_type):
        data = await upload.read(MAX_BYTES + 1)
        if not data:
            raise HTTPException(400, f'{upload.filename or "A file"} is empty.')
        if len(data) > MAX_BYTES:
            raise HTTPException(400, f'{upload.filename} is larger than 20 MB.')
        if mode == 'sample':
            record = known.get(hashlib.sha256(data).hexdigest())
            if record is None or record.document_type != document_type:
                raise HTTPException(400, f'{upload.filename} is not a bundled sample {document_type.replace("_", " ")}. '
                                    'Without an extraction model the app can only run the bundled sample files. '
                                    'Set MATCHLEDGER_PROVIDER in .env to read your own documents.')
        name = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(upload.filename or 'upload').name) or 'upload'
        target = folder / f'{len(list(folder.iterdir())):02d}' / name
        target.parent.mkdir()
        target.write_bytes(data)
        return str(target.relative_to(folder))

    def load(case_id):
        try:
            version, record = store.load(case_id)
        except KeyError:
            raise HTTPException(404, 'No saved case has that ID.') from None
        if record.get('kind') != 'document_case':
            raise HTTPException(404, 'That case was not created from uploaded documents.')
        return record

    async def guarded(call):
        try:
            async with lock:
                return case_view(await call())
        except (ValueError, CaseConflict) as exc:
            # Service messages are written for staff and contain no file contents.
            raise HTTPException(400, str(exc).splitlines()[0][:300]) from None

    @app.get('/api/config')
    def config():
        return {'mode': mode, 'model': os.getenv('MATCHLEDGER_MODEL') if mode == 'live' else None,
                'semantic_matching': bool(mode == 'live' and os.getenv('MATCHLEDGER_MATCH_MODEL')),
                'samples': [{'id': s['id'], 'label': s['label'], 'note': s['note'],
                             'files': {k: Path(v).name for k, v in s['files'].items()},
                             'follow_ups': {k: Path(v).name for k, v in s.get('follow_ups', {}).items()}}
                            for s in SAMPLE_SETS]}

    @app.get('/api/samples/{name}')
    def sample(name: str):
        if name not in SAMPLE_PATHS:
            raise HTTPException(404, 'Unknown sample file.')
        return FileResponse(ROOT / SAMPLE_PATHS[name], media_type='application/pdf', filename=name)

    @app.get('/api/cases')
    def cases():
        rows = []
        for case_id in store.list_ids():
            record = store.load(case_id)[1]
            if record.get('kind') == 'document_case':
                rows.append(case_summary(record))
        return sorted(rows, key=lambda r: r['updated_at'] or '', reverse=True)

    @app.get('/api/cases/{case_id}')
    def case(case_id: str):
        return case_view(load(case_id))

    @app.post('/api/cases')
    async def create(purchase_order: UploadFile = File(...), invoice: UploadFile = File(...),
                     receipts: list[UploadFile] = File(...), name: str = Form('')):
        if not 1 <= len(receipts) <= 3:
            raise HTTPException(400, 'Add one to three delivery receipts.')
        case_id = _slug(name) or 'case-' + datetime.now().strftime('%Y%m%d-%H%M%S')
        with tempfile.TemporaryDirectory(dir=_tmp(data_dir)) as tmp:
            folder = Path(tmp)
            documents = [{'document_id': f'{case_id}-po', 'document_type': 'purchase_order',
                          'file_path': await stage(folder, purchase_order, 'purchase_order')},
                         {'document_id': f'{case_id}-inv-1', 'document_type': 'invoice',
                          'file_path': await stage(folder, invoice, 'invoice')}]
            for index, receipt in enumerate(receipts, 1):
                documents.append({'document_id': f'{case_id}-rcv-{index}', 'document_type': 'receipt',
                                  'file_path': await stage(folder, receipt, 'receipt')})
            manifest = {'case_id': case_id, 'revision': 1, 'supplier_directory': supplier_directory, 'documents': documents}
            return await guarded(lambda: start_documents(store, manifest, folder, archive, runner))

    @app.post('/api/cases/{case_id}/documents')
    async def change(case_id: str, file: UploadFile = File(...), action: str = Form(...),
                     expected_revision: int = Form(...), staff: str = Form(...), reason: str = Form(...),
                     confirmed: bool = Form(False)):
        record = load(case_id)
        if action not in {'add_receipt', 'replace_invoice'}:
            raise HTTPException(400, 'Choose add receipt or replace invoice.')
        if not staff.strip() or not reason.strip():
            raise HTTPException(400, 'Enter your name and the reason for the change.')
        document_type = 'receipt' if action == 'add_receipt' else 'invoice'
        prefix = 'rcv' if document_type == 'receipt' else 'inv'
        count = sum(u['document_type'] == document_type for u in record['uploads'].values())
        change = {'action': 'add', 'document_id': f'{case_id}-{prefix}-{count + 1}', 'document_type': document_type}
        if action == 'replace_invoice':
            target = case_view(record)['actions']['active_invoice_id']
            if target is None:
                raise HTTPException(400, 'This case has no active invoice to replace.')
            change.update(action='replace', replaces=target)
        with tempfile.TemporaryDirectory(dir=_tmp(data_dir)) as tmp:
            folder = Path(tmp)
            change['file_path'] = await stage(folder, file, document_type)
            request = {'expected_revision': expected_revision, 'staff': staff, 'reason': reason,
                       'confirmed': confirmed, 'documents': [change]}
            return await guarded(lambda: change_documents(store, case_id, request, folder, archive, runner))

    @app.post('/api/cases/{case_id}/retry')
    async def retry(case_id: str, expected_revision: int = Form(...)):
        load(case_id)
        return await guarded(lambda: retry_documents(store, case_id, expected_revision, runner))

    @app.get('/api/cases/{case_id}/documents/{document_id}/file')
    def document_file(case_id: str, document_id: str):
        upload = load(case_id)['uploads'].get(document_id)
        if upload is None:
            raise HTTPException(404, 'No such document in this case.')
        path = Path(upload['file_path'])
        if not path.is_file() or path.parent.resolve() != archive.resolve():
            raise HTTPException(404, 'The archived file is missing.')
        return FileResponse(path, media_type=MEDIA.get(path.suffix, 'application/octet-stream'),
                            filename=upload['original_name'], content_disposition_type='inline')

    app.mount('/', StaticFiles(directory=STATIC, html=True), name='static')
    return app


def _tmp(data_dir):
    folder = Path(data_dir) / 'incoming'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def __getattr__(name):
    # `uvicorn src.web.app:app` builds the app on first use, so importing this
    # module (tests, tooling) never creates the outputs/ database as a side effect.
    if name == 'app':
        globals()['app'] = create_app()
        return globals()['app']
    raise AttributeError(name)
