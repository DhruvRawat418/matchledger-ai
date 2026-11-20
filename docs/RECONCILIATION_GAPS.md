# Reconciliation: requirements status and gaps

**This is a status note, not a contract.** The team's data standard is
[`docs/DATA_CONTRACTS.md`](DATA_CONTRACTS.md) together with `src/contracts.py`.
If this note disagrees with either of them, they win.

- Owner: Matthew (deterministic reconciliation)
- Last checked: 2026-11-11, branch `feature/reconciliation-worker`

## Engine in use

The LangGraph workflow reconciles through `src/reconciliation_adapter.py`,
which calls the `procurement_recon` engine. All work below targets that engine;
there is no other reconciliation engine in the repository.

## Responsibilities: where they stand

| # | Responsibility | Status |
|---|---|---|
| 1 | Exact SKU matching, normalized/fuzzy baseline | Exact match ignoring letter case and surrounding spaces. No fuzzy matching (gap G3) |
| 2–4 | Three-way check of quantities and prices, Decimal arithmetic | Done |
| 5 | Partial deliveries, multiple receipts | Done. A shortfall is reported as `needs_review` |
| 6 | Repeated uploads not double-counted | Done in the adapter ([RECEIPT_SCREENING.md](RECEIPT_SCREENING.md)). A full fix needs gap G2 |
| 7 | Replacements exclude superseded records | Gap G1. Conflicting versions block the case; nothing is guessed |
| 8 | Missing values never become zero | Done. Missing data blocks the case and no totals are produced |
| 9 | Size, model and unit conflicts | Units: small explicit equivalence list, otherwise blocked. SKU conflicts are blocked. Size and model: gap G4 |
| 10 | Structured findings, calculation traces | Implemented as a **proposed** extension (quantities, amounts, calculation), pending Dhruv and Maia |
| 11 | Tests and documented rules | `tests/test_receipt_duplicates.py`, `tests/test_reconciliation_output.py`; rules in DATA_CONTRACTS.md (proposed section) and RECEIPT_SCREENING.md |

**Required demonstration** (from the project proposal):

| Step | Status |
|---|---|
| PO 20, receipt 18, invoice 20 → shortfall of 2 | Passes |
| A new receipt of 2 → received total 20 | Passes |
| Re-uploading either receipt → total stays 20 | Passes |
| A replacement receipt excludes the superseded one | Needs G1 |

## Gaps that need someone else's decision

| Gap | What is missing | Decision owner | Smallest proposal |
|---|---|---|---|
| G1 | Explicit replacement link | Dhruv (revisions and intake) | An optional field on `ExtractedDocument` naming the document it replaces, or Dhruv's planned document-version handling |
| G2 | File fingerprint, so a re-upload can be told apart from a genuine identical delivery | Dhruv (ingestion) | An optional file hash recorded at upload |
| G3 | Who owns normalized/fuzzy SKU matching, and how non-exact matches reach the adapter | Jijuan, Dhruv, Matthew | Not yet proposed |
| G4 | Model and size attributes on line items | Abdullah, Dhruv | Optional fields on `LineItem`, only if the test cases need them |
| G5 | Standard unit spellings beyond the adapter's list | Abdullah | Normalize during extraction |
| G7 | A dedicated code for two identical lines on one invoice (today `duplicate_document` + `[POSSIBLE_REPEATED_LINE]`) | Dhruv, Maia | Add a `duplicate_line` discrepancy code |
| G8 | Repeated product on one receipt, or on the PO (blocked today) | Dhruv, Matthew | Only if the test cases need it; receipts need line-level repeat screening first |
| G6 | The reconciliation output extension | Dhruv, Maia | See the PROPOSED section of DATA_CONTRACTS.md |

## Matching rules in the adapter

- **SKU.** Compared exactly, ignoring letter case and surrounding spaces:
  `mon-24` = `MON-24`. Any other difference still blocks the case, so
  `MON 24` ≠ `MON-24`. There is no fuzzy matching.
- **Unit.** `each`, `ea`, `pc`, `pcs`, `piece` and `pieces` all mean one item,
  ignoring letter case and a trailing period. Any other unit must match apart
  from letter case. Units are never converted (for example, `box` ≠ `each`).
- **Same product on several invoice lines.** The quantities are added up, and
  each line's price is checked on its own. If the lines are completely
  identical, they are still all counted, but the case is flagged for review in
  case extraction repeated a line.
- **Messages.** Every blocking problem names its document and line. All the
  problems found at the same stage are reported together.

## Known limits today

- **Some replacements can still slip through.** A replacement receipt with no
  receipt number and different lines is counted as a second delivery. The
  over-delivery check catches it if the total exceeds the order; a replacement
  that lands exactly on the ordered quantity is not caught. Fixing this needs G1.
- **Two genuinely identical deliveries with no receipt numbers** are flagged for
  review instead of being counted automatically. Fixing this needs G2.
- **The workflow allows at most three receipts per case, including repeats.**
