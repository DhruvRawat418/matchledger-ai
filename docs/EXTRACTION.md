# Document Extraction Agent

Owner: Abdullah · Code: `src/extraction/` · Tests: `tests/test_extraction.py`
Status: draft on `feature/document-extraction`, built against `src/contracts.py` v0.1.
No shared contract was changed.

## What it does

```python
from src.extraction import ExtractionRequest, extract_document

outcome = await extract_document(ExtractionRequest(
    case_id="case-001", revision=1, document_id="case-001-inv-5001",
    file_path="uploads/inv.pdf", declared_type="invoice",
    supplier_directory={"Acme Displays Inc.": "SUP-ACME"}), client)

outcome.document         # ExtractedDocument (shared contract) or None if extraction failed
outcome.issues           # what staff must look at (severity "review") or should know ("info")
outcome.needs_review     # True if any review issue or no document
outcome.referenced_po_number   # printed PO number, for intake to set purchase_order_id
```

```
PDF / PNG / JPEG ──► pages.py ──► page images (+ PDF text layer)
                                    │
                                    ▼
                        model (vision, JSON only)      ◄─ prompt.py (versioned)
                        returns ModelDocument: a transcription
                                    │  invalid JSON / extra fields → retry (max 3)
                                    ▼
                        extractor.py (plain Python)
                        • IDs from the request, line IDs 1..n
                        • numbers, units, currency normalized, never guessed
                        • every value checked against the quoted row
                        • quoted row checked against the page's text layer
                                    │
                                    ▼
                        ExtractedDocument + issues
```

## Division of labour: model vs. code

| The model does | Python does |
| --- | --- |
| Read the page layout and copy printed values | Assign `case_id`, `revision`, `document_id`, `line_id` |
| Quote the printed row as evidence | Turn `"$1,250.00"` into `"1250.00"`; reject negatives |
| Read the document type, number, supplier name, PO number | Map `ea`, `pcs` … to `each` (same list as Matthew's adapter, gap G5) |
| | Map the printed supplier name to `supplier_id` via a supplier list |
| | Check each value appears in the quoted row, and the row on the page |
| | Validate the result against `src/contracts.py` |

The model never produces workflow IDs, `purchase_order_id`, totals or decisions.
Its output schema (`ModelDocument`) forbids extra fields, so a reply that adds
something like `"approved": true` is rejected and retried.

## Rules that follow the team contract

- **Missing stays `null`.** A unit that isn't printed is `null` with a
  `unit_missing` issue, never `"each"`. The same holds for currency, prices and
  supplier IDs.
- **Receipts** never get a currency (no prices are printed on them).
- **Currency:** only `USD`; `$`, `US$` and `USD` all count as USD. Anything else
  is `null` plus `unsupported_currency`.
- **Document type:** the type chosen at upload wins. If the model reads it
  differently, a `document_type_conflict` issue is raised.

## Issues raised

| Code | Severity | Meaning |
| --- | --- | --- |
| `missing_value` | review (info for a receipt's supplier) | A required value isn't printed or legible |
| `unit_missing` | review | No unit printed; left `null` |
| `invalid_number` | review | Printed number is negative, a credit or unreadable |
| `value_not_in_evidence` | review | Extracted value isn't in the row the model quoted, suggesting a misread or made-up value |
| `evidence_not_found` | review | The quoted row isn't in the PDF's text on that page |
| `evidence_page_invalid` | review | Cites a page the document doesn't have |
| `evidence_unverified` | info | Scanned or image page; quotes can't be checked automatically |
| `supplier_unresolved` | review (info on receipts) | Supplier name isn't in the supplier list |
| `currency_unknown` / `unsupported_currency` | review | Currency must be confirmed before money checks |
| `document_type_conflict` | review | Upload type and model reading disagree |
| `instruction_like_text` | review | A line contains text that reads like an instruction; it is treated as data |
| `extraction_failed`, `unreadable_file`, `no_line_items`, `document_type_unknown`, `contract_validation_failed` | review | No document produced |

Error messages carry only the error type, never provider text, API keys or
document content.

## Model provider

`client.py` defines the `ModelClient` interface (`complete(system, user, pages,
json_schema) -> ModelReply`). The provider adapter is a single file still to be
added, once the team records its model choice. The API key is read from `.env`,
which Git ignores. `FakeModelClient` returns scripted replies so the tests run
offline.

Each outcome records `prompt_version`, `model_id`, `attempts`, `latency_ms` and
provider token `usage` for the evaluation.

## Evaluation

`src/extraction/scoring.py` scores a document against an answer key
(`documents[]` in Jijuan's ground-truth cases), field by field: header fields,
then line fields paired by `line_id`, plus whether the number of lines is right.
`accuracy()` combines the results per field and overall. Evidence wording isn't
scored.

## Open decisions for the team

1. **Unit not printed.** Some answer keys (case-001 receipts, case-002 invoice)
   expect `"each"` where no unit is printed. Extraction returns `null`, as the
   contract requires. Either the answer keys change, or the team agrees on a
   documented default.
2. **Supplier list.** `supplier_id` (for example `SUP-ACME`) isn't printed on
   documents, so intake needs to supply the name-to-ID list in the request.
3. **Extraction issues in the graph.** `ExtractionOutcome.issues` has no place in
   `WorkflowState` yet. Proposal: add them to `issues` with `owner: "extract"`.
4. **Size and model fields (gap G4).** Not added. Descriptions keep the size as
   printed. Add the fields only if the test cases need them.
5. **PDF rendering of the answer keys.** Assigned to Dhruv in
   `make_sample_cases.py`.
