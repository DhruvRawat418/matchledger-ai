# Resolve case 102 using new source documents

The document workflow archives each uploaded file by SHA-256, saves state in
SQLite, and records explicit additions/replacements with staff, reason and time.
Old files and previous revision results remain available in the record. Every
revision reruns extraction and downstream checks. Review readiness never approves
payment. This is a local single-operator service; dashboard controls are pending.

## Run the live demo

Run from the repository root with `.venv` active and your configured `.env`.
All files here are synthetic. The commands call your configured extraction model
and incur API usage. Semantic matching is optional and uses the existing settings.

Start a fresh saved case (your earlier `outputs/uploads-102.json` stays unchanged):

```sh
python -m demo.document_cases start demo/upload_manifest_102.json --case-id resolution-102
```

Expected: revision 1, `needs_correction`, unsupported amount $85.00.
If that case ID already exists, use `show` or choose a different ID; never delete
its history to restart a demo.

After inspecting the additional-delivery fixture, record the added receipt:

```sh
python -m demo.document_cases change resolution-102 demo/resolution_102/add_receipt.json --revision 1 --staff "Dhruv" --reason "Synthetic demo: remaining 10 paper units received, documented in DN-8202" --confirm
```

Expected: revision 2, price discrepancy remains, unsupported amount $34.00.
The original receipt for 30 units remains active alongside the new receipt for 10.

After inspecting the corrected-invoice fixture, replace the invoice:

```sh
python -m demo.document_cases change resolution-102 demo/resolution_102/replace_invoice.json --revision 2 --staff "Dhruv" --reason "Synthetic demo: supplier issued corrected invoice at the agreed paper price of 5.10" --confirm
```

Expected: revision 3, `ready_for_human_review`; billed/supported $306.00 and
unsupported $0. The original invoice is retained but excluded from the active
calculation. Two prior states and two document-change events are saved.

```sh
python -m demo.document_cases show resolution-102
```

The service rejects stale revisions, unconfirmed changes, reused document IDs,
repeated file contents, wrong replacement types, and changes to stopped cases.
It verifies archived fingerprints before each run. Failure invalidates old results
and saves a blocked state with a sanitized error. Interrupted/blocked runs can use:

```sh
python -m demo.document_cases retry resolution-102 --revision 2
```

Use the actual current revision. A completed or correction-needed revision cannot
be retried with this command; source changes must create a new revision.

## Files and application integration

- `outputs/document_cases.sqlite`: saved records and history (ignored by Git).
- `outputs/document_archive/`: original bytes keyed by fingerprint (ignored).
- `src/document_cases.py`: reusable `start_documents`, `change_documents`,
  and `retry_documents` service; `CaseStore.load` supports the dashboard display.
- `demo/resolution_102/*.json`: upload changes only, not extracted values.
- `python -m demo.prepare_resolution`: regenerate the synthetic PDFs and requests.

This starts a separate saved case; importing earlier JSON run snapshots is not
implemented. File hashes detect identical bytes; semantically equivalent rescans
still rely on extracted receipt numbers and reconciliation screening. Automatic
replacement guesses are deliberately unsupported. Current scope permits a single
active invoice, one PO and up to three active receipts. PO replacement and general
retirement actions are not yet implemented.

## Validation

Automated lifecycle tests use fixture extraction with real matching/math, and cover
$85 -> $34 -> $0, immutable originals, persistence, stale/invalid requests, duplicate
uploads, tampered archives and failed-run retry. They do not establish live model
quality. The new live resolution sequence must still be run and inspected by the
team; the earlier live clean/discrepancy runs are separate evidence.
