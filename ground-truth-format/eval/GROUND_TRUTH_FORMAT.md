# Ground-truth case format — v0.1 draft

Answer keys for the synthetic test set. `eval/ground_truth.py` is
the executable source of truth; run `python -m eval.ground_truth` to validate
every case.

## Principle: reuse the contracts, add only what evaluation needs

A case file contains **the exact outputs each worker should produce**, written
with the shared contracts in `src/contracts.py`:

| Part of the case | Contract | Scores |
| --- | --- | --- |
| `documents[]` | `ExtractedDocument` | Abdullah's extraction (field accuracy) |
| `revisions[].expected_matches[]` | `MatchResult` | Matching: exact vs cleaned vs AI |
| `revisions[].expected_reconciliation` | `ReconciliationResult` | Matthew's checks; final-case correctness |

So all contract rules apply unchanged: `document_type` is `purchase_order` /
`invoice` / `receipt`; quantities and prices are decimal strings; unreadable
values are `null`; every line carries page + visible text evidence; line
references use document ID **and** line ID.

Evaluation-only additions:

- `categories` — case-level tags (for the manifest and per-category reporting).
- `line_tags` — the matching difficulty of every invoice/receipt line:
  `identical`, `formatting`, `synonym`, `lookalike`, `not_on_po`.
- `revisions` — the upload sequence. Each entry lists the documents current at
  that revision (`document_ids`) and the correct matches and reconciliation
  after that upload.

## Folder layout

```
eval/cases/
  manifest.csv                 case_id, categories, revisions, description
  case-001.json                answer key
  case-001/                    (to be added) rendered PDFs: po, invoice, receipts
```

## Rules for writing a case

1. Each document is stored once, with `revision` = the revision where it first
   appears. `snapshot(case, n)` re-stamps all current documents with revision
   `n`, which matches the contract ("revision is the case input revision").
2. A revised document is a **new** `document_id` (same `business_document_number`)
   and the old one is dropped from later `document_ids`.
3. Every invoice and receipt line must have exactly one expected `MatchResult`
   in every revision, and a `line_tag`.
4. Answer-key matches use `method: "human"`. When scoring, compare only
   `decision` and `selected_po_line`. `explanation` and `evidence` are for readers.
5. `candidate_po_lines` = all lines of the linked PO (`purchase_order_id`).
6. PO and invoice carry `supplier_id` and `currency: "USD"`. Receipts show no
   prices, so `currency` is `null` (never inferred).
7. A `clean` result must list every current line in `checked_lines`.

## Sample cases

| Case | Shows |
| --- | --- |
| case-001 | Demo scenario: synonym descriptions (no SKU on supplier docs), 18 + 2 partial delivery → `needs_review`, then `clean` |
| case-002 | Formatting noise, a 24in/27in look-alike, a price error, an unordered shipping fee (`unmatched`), then a corrected invoice → `clean` |

## Open questions for the team

- **`MatchResult.method`** allows `exact`, `fuzzy`, `llm`, `human`. Our cleaned
  direct match is a rule-based exact match after cleaning, not fuzzy matching.
  Proposal: add `"normalized"` (or agree to record it as `fuzzy`).
- **Receipt currency:** confirm `null` on receipts doesn't block Matthew's
  reconciliation, since monetary checks only use PO and invoice prices.
- **`unmatched_item` values:** in case-002 the unordered fee uses
  `expected: null, observed: "25.00"` (billed amount). We need to agree what
  `expected` / `observed` mean for each discrepancy code.
- **`quantity_mismatch`** covers both short delivery and over-billing. That's
  fine for scoring, but the explanation must say which one it is.
