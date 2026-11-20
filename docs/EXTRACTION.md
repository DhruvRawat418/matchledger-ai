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
| `po_number_possible_misread` | review | (cross-check) A referenced PO number matches a PO only after look-alike characters are treated as equal; on both documents |
| `po_reference_not_found` | review | (cross-check) An invoice or receipt references a PO number that no purchase order in the case has |
| `supplier_unresolved` | review (info on receipts) | Supplier name isn't in the supplier list |
| `currency_unknown` / `unsupported_currency` | review | Currency must be confirmed before money checks |
| `document_type_conflict` | review | Upload type and model reading disagree |
| `instruction_like_text` | review | A line contains text that reads like an instruction; it is treated as data |
| `extraction_failed`, `unreadable_file`, `no_line_items`, `document_type_unknown`, `contract_validation_failed` | review | No document produced |

Error messages carry only the error type, never provider text, API keys or
document content.

## Model provider

`client.py` defines the `ModelClient` interface. `providers.py` has adapters for
**OpenAI, Anthropic and Google Gemini**. Each one sends the page images and the
prompt over HTTPS and asks for a JSON reply. No temperature setting is sent unless
`MATCHLEDGER_TEMPERATURE` is set, because some current models reject the parameter
(see the change log below). How much results vary between runs is measured with
`--repeat`.
You choose the provider in `.env`; copy `.env.example`, and remember Git ignores
`.env`:

```
MATCHLEDGER_PROVIDER=openai | anthropic | gemini
MATCHLEDGER_MODEL=<exact model identifier>
<PROVIDER>_API_KEY=...
```

API keys are only sent in request headers. An error reports the provider, the
HTTP status and the provider's short error type and message (cut to 200
characters, and dropped if it contains the key), never the raw response body and
never the key. That detail is carried into the outcome's `extraction_failed` issue. `FakeModelClient` returns scripted replies so the tests run offline.

## Evaluation and model selection

`python -m src.extraction.evaluate` runs extraction over answer-key cases and writes
three files to `outputs/extraction/<model>-<time>/`:

- `summary.json`: accuracy overall, by field, by document type and by layout;
  the share of valid JSON on the first try; failure and needs-review rates; issue
  counts; median and 95th-percentile latency; tokens; and estimated cost if
  prices are given.
- `results.jsonl`: every outcome and its score.
- `field_errors.csv`: every wrong field, for error analysis.

```
python -m src.extraction.evaluate --cases <answer-key folder> --files <rendered docs> \
    --suppliers suppliers.json --layouts layouts.csv --only case-001 case-002 \
    --price-in <USD per 1M input tokens> --price-out <USD per 1M output tokens>
```

Rendered documents go in `<files>/<case_id>/<document_id>.pdf`. The `--repeat N`
option runs every document N times to measure how much results vary. By default
the model must determine the document type itself; `--declare-type` passes the
true type instead.

**Prompt versions.** `prompt.py` keeps every version (`extract-v1`, `extract-v2`,
and so on). The default is the latest. Set `MATCHLEDGER_PROMPT` to run an older one
on the same documents for a controlled before/after. Output folders are named
`<model>-<prompt>-<time>`.

**Results so far (development set: 7 answer-key cases, 26 documents, claude-sonnet-5-5, 2026-11-16)**

| Run | Documents | Prompt | Overall | Supplier | Unit | Valid JSON 1st try | Median latency | Tokens in / out |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | original (supplier unlabelled on POs) | extract-v1 | 96.2% | 92.3% | 74.5% | 100% | 2.5 s | 109k / 7.3k |
| 2 | realistic buyer/supplier | extract-v1 | 96.7% | 100% | 74.5% | 100% | 2.6 s | 110k / 7.0k |
| 3 | realistic buyer/supplier | extract-v2 | 96.7% | 100% | 74.5% | 100% | 2.4 s | 114k / 6.6k |
| 4 | scans of run 3's documents (no text layer) | extract-v2 | 96.4% | 100% | 74.5% | 100% | 2.3 s | 78k / 6.7k |
| 5 | scans, with cross-document check | extract-v2 | 96.4% | 100% | 74.5% | 100% | 2.2 s | 78k / 6.1k |
| 6 | realistic PDFs, **claude-haiku-5-5** | extract-v2 | 96.7% | 100% | 74.5% | 100% | 2.5 s | 114k / 12.6k |
| 7 | scans, **claude-haiku-5-5**, cross-document check | extract-v2 | 96.7% | 100% | 74.5% | 100% | 2.6 s | 78k / 14.2k |

Perfect-reader ceiling on these answer keys: 96.7%. Every other field scored 100% in runs 2–3. Run 4's
only extra error is one PO number misread on a scan (`PO-3004` → `PQ-3004`); see the change log.
Run 5 repeated the misread, and the cross-document check flagged it: 0 silent-error documents,
needs-review rate 38% (19% without scans). Runs 1–5 use claude-sonnet-5-5.

**Model selection (proposal, with Jijuan).** Run the command once per candidate
model on the development cases only, then record the results here:

| Model identifier | Provider | Accuracy, PDFs / scans | Silent errors (scans) | Valid JSON 1st try | Median / p95 latency (PDFs) | Tokens per doc in / out (PDFs) | Chosen |
| --- | --- | --- | --- | --- | --- | --- | --- |
| claude-sonnet-5-5 | Anthropic | 96.7% / 96.4% (misread PO-3004 as PQ-3004 on both scan runs) | 1 without the cross-check, 0 with it | 100% | 2.4 s / 6.9 s | 4.4k / 0.25k | |
| claude-haiku-5-5 | Anthropic | 96.7% / 96.7% | 0 | 100% | 2.5 s / 3.6 s | 4.4k / 0.48k | **yes (proposed, 2026-11-17)** |

Both models reach the answer-key ceiling (96.7%) on the PDFs. Haiku also reached it on
the scans, where Sonnet repeated one PO-number misread. Haiku is the lowest-cost tier in
the Claude Console and had a lower worst-case latency, although it writes about twice as
many output tokens. **Proposal: claude-haiku-5-5**, keeping the cross-document check,
because any model can misread a scan. Caveats: this is one error on one field of 26
documents, so it shows the two are comparable, not that Haiku is better. The choice is
to be confirmed with Jijuan and checked once on the held-out set. Other providers
(OpenAI, Gemini) need a teammate's key and run with the same command.

The held-out cases stay unused until the prompt and model are frozen.

## Test documents (rendered answer keys)

`python -m src.extraction.render` turns Jijuan's answer keys (`documents[]` in each
case on the `test_cases` branch) into the files the extractor is tested on.

```
python -m src.extraction.render --cases eval/cases --out eval/rendered --scan
```

- **One file per answer-key document:** `eval/rendered/<case_id>/<document_id>.pdf`.
  The answer keys from 2026-11-11 (7 cases) give 26 documents, already committed.
- **Rows match the answer key:** each product row is printed exactly as its
  answer-key `evidence.text`, on the cited page, and every row is checked against
  the PDF text layer after rendering.
- **Two layouts**, alternated by case so every document type appears in both:
  `classic` (letterhead, ruled table) and `compact` (monospace, numbered rows).
  `--layout classic|compact` renders every case in one layout.
- **Scans:** `--scan` also writes degraded PNG scans (rotated, blurred, speckled,
  no text layer) to `eval/rendered-scan/` for the messy-scan condition.
- **Duplicates:** rendering is deterministic, so a duplicate upload in the answer
  keys (case-104 INV-9901 a/b, case-105 DN-7701 a/b) gives byte-identical files.
  That's what file-hash duplicate detection needs.
- **Supplier names:** `eval/rendered/suppliers.json` maps printed names to IDs
  (Acme Displays Inc. → SUP-ACME and so on). Pass it to `--suppliers`.
  `layouts.csv` goes to `--layouts`.

**Perfect-reader check (2026-11-16).** A fake model that copies exactly what is
printed was run through the extractor and scoring on all 26 files. Every field
scored 100% except `unit` (74%): 12 lines print no unit, while the answer keys
expect `"each"` (case-001 receipts, case-002 invoice and receipt). That gap
belongs to the answer keys, not the model, so open decision 1 below caps
reachable accuracy at 96.7% until it's settled. On this set the classic/compact
split (98.9% / 94.4%) only reflects which cases got which layout. To compare
layouts fairly, render every case in both (`--layout`) and evaluate each folder.

## Change log from real runs

| Date | What the run showed | Change | Effect |
| --- | --- | --- | --- |
| 2026-11-16 | First real run (claude-sonnet-5-5, 26 rendered documents): every call failed, 0 tokens used, and the issue only said `ModelCallError`. A direct API call showed HTTP 400 `` `temperature` is deprecated for this model ``. | Stop sending temperature by default (optional `MATCHLEDGER_TEMPERATURE`). Copy the provider's error type and message into the issue. | Calls go through; future failures name their cause. |
| 2026-11-16 | Every PDF triggered `incorrect startxref pointer` warnings on Windows, because Git turned the PDFs' line endings into CRLF and shifted their internal offsets. | `.gitattributes` marks PDF and image files as binary. pypdf repair warnings are hidden. | Files arrive byte-identical on every OS, which duplicate detection by file hash depends on. |
| 2026-11-16 | First successful run, prompt `extract-v1` (claude-sonnet-5-5, 26 documents): **96.2%** field accuracy; 100% valid JSON on the first try; 0 failures; median 2.5 s per document; 109k input / 7.3k output tokens. Every field was 100% except `unit` (74.5%, the 12 unprinted units, which matches the perfect-reader ceiling) and `supplier_id` (92.3%): **2 of 7 purchase orders came back with no supplier** (case-001 classic, case-002 compact). On those test POs the supplier's name was the letterhead and nothing was labelled "Supplier", but a real PO's letterhead is the buyer. The model's reading was reasonable and the test documents were unrealistic; the 5 POs it "got right" only matched because of that unrealistic layout. | (1) Test documents: POs now carry the buyer's letterhead and a labelled `Supplier:`; invoices show `Bill to:` and delivery notes `Deliver to:` with the buyer, a realistic distractor. (2) Prompt `extract-v2` defines the supplier on each document type and forbids returning the buyer, bill-to or deliver-to company. v1 is still selectable (`MATCHLEDGER_PROMPT=extract-v1`), so both prompts can be compared on the same new documents. | Same model, new documents: `extract-v1` 96.7%, `extract-v2` **96.7%** (supplier 100% with both; 100% valid JSON; 0 failures; only the 12 `unit_missing` remain). This is the perfect-reader ceiling, so the remaining gap is the answer-key unit question, not the model. The realistic documents closed the supplier gap; v2 made no measurable difference on this set and is kept as an explicit rule. Caveat: 26 documents, 7 POs. |
| 2026-11-16 | Scan run (`eval/rendered-scan`: rotated, blurred, speckled PNGs, no text layer; extract-v2): **96.4%**, nearly the same as clean PDFs, with 31% fewer input tokens. One error: case-104's PO number `PO-3004` read as **`PQ-3004`**. **Nothing flagged it**: header fields have no quoted row to check, and scans have no text layer (`evidence_unverified` on all 26). | `crosscheck.py`: after a case is extracted, every PO number referenced on an invoice or delivery note is compared with the purchase orders. A match only after look-alike characters (O/Q/D/0, I/L/1, S/5, B/8, Z/2) are treated as equal raises `po_number_possible_misread` on both documents; no match at all raises `po_reference_not_found`. Nothing is corrected. The evaluation now reports **`silent_error_documents`**: documents with a wrong field and no review issue. | Confirmed on a re-run of the scan set (2026-11-17): the model misread the same PO number again, and it was now flagged (`po_number_possible_misread` on the PO and the 4 documents referencing it). **Silent errors: 0.** Trade-off: needs-review rate rose from 19% to 38% (10 of 26 documents). |

## Open decisions for the team

1. **Unit not printed.** Some answer keys (case-001 receipts, case-002 invoice
   and receipt; 12 lines) expect `"each"` where no unit is printed. Extraction returns `null`, as the
   contract requires. Either the answer keys change, or the team agrees on a
   documented default.
2. **Supplier list.** `supplier_id` (for example `SUP-ACME`) isn't printed on
   documents, so intake needs to supply the name-to-ID list in the request.
3. **Extraction issues in the graph.** `ExtractionOutcome.issues` has no place in
   `WorkflowState` yet. Proposal: add them to `issues` with `owner: "extract"`.
4. **Size and model fields (gap G4).** Not added. Descriptions keep the size as
   printed. Add the fields only if the test cases need them.
5. **PDF rendering of the answer keys.** Done (Abdullah, at Dhruv's request).
   See "Test documents" above.
