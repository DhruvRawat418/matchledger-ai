"""Persistent document revisions for a local, single-operator upload workflow.

Files are archived by content hash. Replacement retires an active upload, never
edits it. Every revision reruns extraction and all downstream checks.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Literal
from pydantic import Field
from src.contracts import Contract, Identifier, PositiveInt
from src.graph import initial_state
from src.case_service import now


class Upload(Contract):
    document_id: Identifier
    document_type: Literal['purchase_order', 'invoice', 'receipt']
    file_path: str = Field(min_length=1)


class UploadManifest(Contract):
    case_id: Identifier
    revision: PositiveInt = 1
    supplier_directory: dict[str, Identifier]
    documents: list[Upload] = Field(min_length=3)


class DocumentChange(Upload):
    action: Literal['add', 'replace']
    replaces: Identifier | None = None


class ChangeRequest(Contract):
    expected_revision: PositiveInt
    staff: Identifier
    reason: Identifier
    confirmed: bool = Field(strict=True)
    documents: list[DocumentChange] = Field(min_length=1)


def _scope(uploads):
    kinds = [d['document_type'] for d in uploads]
    if kinds.count('purchase_order') != 1 or kinds.count('invoice') != 1 or not 1 <= kinds.count('receipt') <= 3:
        raise ValueError('Use one active PO, one active invoice and one to three active receipts')


def _archive(upload, base, archive):
    source = (Path(base) / upload.file_path).resolve()
    data = source.read_bytes()
    if not data.startswith((b'%PDF', b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
        raise ValueError('Upload must be a PDF, PNG or JPEG')
    digest = hashlib.sha256(data).hexdigest()
    suffix = '.pdf' if data.startswith(b'%PDF') else '.png' if data.startswith(b'\x89PNG') else '.jpg'
    path = Path(archive).resolve() / (digest + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Archived file no longer matches its fingerprint')
    else:
        with path.open('xb') as stream:
            stream.write(data)
    return {'document_id':upload.document_id, 'document_type':upload.document_type,
            'file_path':str(path), 'sha256':digest, 'original_name':source.name}


def _active(record):
    return [record['uploads'][key] for key in record['active_document_ids']]


def _manifest(record):
    for upload in _active(record):
        if hashlib.sha256(Path(upload['file_path']).read_bytes()).hexdigest() != upload['sha256']:
            raise ValueError('Archived document fingerprint mismatch')
    return {'case_id':record['state']['case_id'], 'revision':record['state']['revision'],
            'supplier_directory':record['supplier_directory'],
            'documents':[{k:u[k] for k in ('document_id','document_type','file_path')} for u in _active(record)]}


async def _execute(store, record, version, runner):
    try:
        result = await runner(_manifest(record))
        if (result['case_id'], result['revision']) != (record['state']['case_id'],record['state']['revision']):
            raise ValueError('Runner returned a different case/revision')
        record['state'] = result
    except Exception as exc:
        # Persist a safe failure, not an old ready-for-review result or provider text.
        record['state']['status'] = 'blocked'
        record['state']['issues'] = [{'code':'document_run_failed','owner':'human','error_type':type(exc).__name__}]
    record['updated_at'] = now()
    store.save(record, version)
    return record


async def start_documents(store, manifest, base, archive, runner):
    manifest = UploadManifest.model_validate(manifest)
    if manifest.revision != 1:
        raise ValueError('A new saved case starts at revision 1')
    # Reject existing cases before file work or model calls.
    try:
        store.load(manifest.case_id)
    except KeyError:
        pass
    else:
        raise ValueError('Case already exists; use show, change or a new case ID')
    uploads = [_archive(u, base, archive) for u in manifest.documents]
    if len({u['document_id'] for u in uploads}) != len(uploads):
        raise ValueError('Document IDs must be unique')
    if len({u['sha256'] for u in uploads}) != len(uploads):
        raise ValueError('Repeated file upload; no document should be counted twice')
    _scope(uploads)
    record = {'kind':'document_case', 'state':initial_state(manifest.case_id),
              'uploads':{u['document_id']:u for u in uploads},
              'active_document_ids':[u['document_id'] for u in uploads],
              'supplier_directory':manifest.supplier_directory,
              'history':[], 'document_events':[], 'updated_at':now()}
    version = store.create(record)
    return await _execute(store, record, version, runner)


async def change_documents(store, case_id, request, base, archive, runner):
    request = ChangeRequest.model_validate(request)
    version, record = store.load(case_id)
    if record.get('kind') != 'document_case':
        raise ValueError('Use the document workflow for this operation')
    if record['state']['status'] not in {'needs_correction','blocked','ready_for_human_review'}:
        raise ValueError('Only idle, non-stopped cases can accept changed documents')
    if request.expected_revision != record['state']['revision']:
        raise ValueError('Stale revision; reload the saved case')
    if not request.confirmed:
        raise ValueError('Confirm that these source documents support the change')
    active = list(record['active_document_ids'])
    additions, retired = [], []
    seen_ids = set(record['uploads'])
    seen_hashes = {u['sha256'] for u in record['uploads'].values()}
    for change in request.documents:
        if change.document_id in seen_ids:
            raise ValueError('Use a new document ID; previous uploads are immutable')
        if change.action == 'replace':
            if change.replaces not in active:
                raise ValueError('Replacement target must be an active document')
            old = record['uploads'][change.replaces]
            if change.document_type != old['document_type'] or change.document_type == 'purchase_order':
                raise ValueError('Only an invoice or receipt can be replaced with the same document type')
            active.remove(change.replaces)
            retired.append(change.replaces)
        elif change.replaces is not None or change.document_type != 'receipt':
            raise ValueError('Add supports additional receipts only; invoices must replace an active invoice')
        upload = _archive(change, base, archive)
        if upload['sha256'] in seen_hashes:
            raise ValueError('Repeated file upload; use show to inspect the existing document')
        additions.append(upload)
        seen_ids.add(upload['document_id'])
        seen_hashes.add(upload['sha256'])
    active += [u['document_id'] for u in additions]
    combined = {**record['uploads'], **{u['document_id']:u for u in additions}}
    _scope([combined[key] for key in active])
    record['history'].append({'state':deepcopy(record['state']),
                              'active_document_ids':list(record['active_document_ids'])})
    revision = request.expected_revision + 1
    record['document_events'].append({**request.model_dump(), 'recorded_at':now(),
        'kind':'source_document_change', 'new_revision':revision, 'retired_document_ids':retired})
    record['uploads'] = combined
    record['active_document_ids'] = active
    record['state'] = initial_state(case_id, revision)
    record['updated_at'] = now()
    # Save invalidation before external model calls. An interrupted run can resume.
    version = store.save(record, version)
    return await _execute(store, record, version, runner)


async def retry_documents(store, case_id, expected_revision, runner):
    version, record = store.load(case_id)
    if record.get('kind') != 'document_case' or record['state']['revision'] != expected_revision:
        raise ValueError('Wrong workflow or stale revision')
    if record['state']['status'] not in {'running','blocked'}:
        raise ValueError('Only interrupted or blocked runs can be retried')
    record['state'] = initial_state(case_id, expected_revision)
    version = store.save(record, version)
    return await _execute(store, record, version, runner)
