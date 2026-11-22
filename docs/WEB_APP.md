# Web app: upload, run the checks, see the result

A local, single-operator page over the saved document workflow in
`src/document_cases.py`. It adds no rules of its own: uploads are archived by
fingerprint, every change creates a new revision, and every revision reruns
extraction, matching, reconciliation and review. "Checks passed" means ready
for a person to review, never approval to pay.

## Run

From the repository root, with `.venv` active:

```sh
python -m pip install -r requirements.txt
python -m uvicorn src.web.app:app --port 8000
```

Open http://127.0.0.1:8000.

## Two modes

| Mode | When | What reads the files |
|---|---|---|
| `live` | `MATCHLEDGER_PROVIDER` is set in `.env` | The configured model (`demo.run_uploads.run_data`). Costs API usage. |
| `sample` | No provider is set | Nothing. Each bundled sample PDF is recognised by its SHA-256 and paired with its record from `eval/cases`. |

Force one with `MATCHLEDGER_UI_MODE=live` or `sample`. In sample mode any file
that is not a bundled sample is refused before a case is created. Matching,
reconciliation and review are the real workers in both modes. Sample mode says
nothing about extraction quality.

## The demo

1. **New case**, then **Use these files** on "Price mismatch and missing
   delivery", then **Run checks**. Expected: revision 1, needs attention,
   $85.00 billed without support, three discrepancies.
2. **Add receipt**, **Use sample: case-102-rcv-8202.pdf**, enter a name and a
   reason, tick the confirmation. Expected: revision 2, $34.00, price mismatch
   only.
3. **Replace invoice**, **Use sample: case-102-inv-8201-corrected.pdf**.
   Expected: revision 3, checks passed, $306.00 billed and verified, $0.00
   without support. The original invoice stays listed as "Replaced".

"Clean case" (PO-3001) passes on revision 1.

## API

| Call | Does |
|---|---|
| `GET /api/config` | Mode, model name, sample sets |
| `GET /api/cases` | Saved document cases, latest first |
| `POST /api/cases` | Multipart `purchase_order`, `invoice`, `receipts` (1 to 3), optional `name`; runs revision 1 |
| `GET /api/cases/{id}` | The case as the page shows it |
| `POST /api/cases/{id}/documents` | Multipart `file`, `action` (`add_receipt` or `replace_invoice`), `expected_revision`, `staff`, `reason`, `confirmed`; runs a new revision |
| `POST /api/cases/{id}/retry` | Rerun a blocked or interrupted revision |
| `GET /api/cases/{id}/documents/{document_id}/file` | The archived upload |

Refusals from the service (stale revision, unconfirmed change, repeated file,
existing case name) come back as HTTP 400 with the service's message and leave
the saved case unchanged.

## Files

- `src/web/app.py`: routes. `create_app(data_dir, mode, runner)` for tests.
- `src/web/views.py`: saved record to page data. Display only, no arithmetic.
- `src/web/sample_records.py`: the sample-mode runner.
- `src/web/static/`: the page (plain HTML, CSS and JavaScript, no build step).
- `tests/test_web_app.py`: the flow above, refusals, failed-run retry.
- `src/case_store.py`: one added method, `CaseStore.list_ids()`.

Data lives in `outputs/` (ignored by Git), or `MATCHLEDGER_DATA_DIR`.

## Limits

- One run at a time; a live run holds the request open until it is saved.
- The page offers what the service supports: add a receipt (up to three
  active), replace the invoice, retry. No PO replacement, no retiring a
  document, no stop, no confirming an uncertain match.
- Upload document IDs are generated as `<case>-po`, `<case>-inv-N`,
  `<case>-rcv-N`. Results show the printed document numbers instead.
- Not verified here: a live extraction run through the page.
