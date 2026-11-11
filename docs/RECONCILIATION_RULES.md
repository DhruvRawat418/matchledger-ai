# Reconciliation rules — v0.1 draft (Matthew)

The reconciliation worker takes the extracted documents and the line-matching
decisions for one case and answers a single business question:

> **Do the purchase order, the delivery records and the invoice agree well
> enough to pay — and if not, exactly what disagrees?**

It is fully deterministic: no AI, no guessing, and the same input always gives
the same result. All arithmetic uses exact decimals (no floating-point
rounding), so `0.1 + 0.2` received really equals `0.3` invoiced.

Code: `src/reconciliation.py`. Tests: `tests/test_reconciliation.py`.
The output uses the shared `ReconciliationResult` contract unchanged.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Ordered | Quantity on the purchase-order (PO) line. |
| Received | Total quantity across **all** receipt lines matched to that PO line. |
| Invoiced | Total quantity across **all** invoice lines matched to that PO line. |
| Source line | Any invoice or receipt line (something matched *to* a PO line). |
| Missing | A value extraction could not read (`null`). Never the same as zero. |

## The rules, in the order they run

**R1 — Valid handoff.** The documents and matches must belong to one case and
revision and refer only to real lines (the shared `validate_handoff` check).
If not, the worker refuses to run; this is an orchestration error, not a
business discrepancy.

**R2 — Duplicate uploads.** Two documents of the same type with the same
business number (e.g. two copies of invoice `INV-1001`) are compared line by
line, ignoring line IDs and harmless formatting (`"125.5"` = `"125.50"`,
`"EA"` = `"each"`).
- *Identical:* the first one supplied is kept; the copy is excluded from all
  totals so nothing is counted twice. A `duplicate_document` discrepancy
  records the exclusion for a human to confirm → **needs review**.
- *Different contents:* we cannot tell whether one replaces the other or they
  genuinely conflict. **Both** are excluded and the case is **blocked** until a
  person (or a future "replaces document X" field) settles which is current.
- Documents with no business number are never treated as duplicates: two
  receipts of 10 units on different days are legitimate.

**R3 — Every invoice and receipt line must be matched.** A source line with no
matching decision, or whose decision is `uncertain` or `unmatched`, is reported
as `unmatched_item` and excluded from the totals → **needs review**.

**R4 — Matched products must agree.** If a matched invoice/receipt line and its
PO line both carry a SKU and the SKUs differ (ignoring case, spaces and
hyphens), the match is rejected as `unmatched_item` and excluded from totals.
This catches a wrong model being matched by mistake.

**R5 — A case needs a PO and an invoice.** If either is absent the case is
**blocked** with `missing_information`. (A missing *receipt* is handled in R8:
it means nothing has been received yet.)

**R6 — Units must agree before any comparison.** Every source line's unit must
match the PO line's unit. Common spellings of a single item are treated as the
same: `each`, `ea`, `unit`, `units`, `pc`, `pcs`, `piece`, `pieces`.
- Different units (e.g. `box` vs `each`) → `unit_mismatch`, **blocked**.
- A missing unit → `missing_information`, **blocked**.

In both cases the quantity and price checks for that PO line are skipped:
comparing 1 box against 12 each would produce a wrong answer.

**R7 — Add up quantities.** For each PO line, add all matched receipt lines and
all matched invoice lines. This is what makes **partial deliveries** and
**multiple receipts** work (9 + 9 received covers an invoice for 18).
- If any contributing quantity is missing, that total is **unknown** (not
  zero), a `missing_information` discrepancy is raised (**blocked**), and any
  comparison that needs that total is skipped.
- If there are *no* receipt lines at all, received is **0**: no delivery has
  been recorded. That's a real zero, not a missing value.

**R8 — Compare quantities** (only when both numbers are known):

| Check | Problem when | Expected | Observed |
| --- | --- | --- | --- |
| Over-billing vs order | invoiced > ordered | ordered | invoiced |
| Over-delivery | received > ordered | ordered | received |
| Billing for goods not received | invoiced > received | invoiced | received |

Each is a `quantity_mismatch` → **needs review**. Being *under* is not a
problem: received < ordered is an open partial delivery, and invoiced <
received means the supplier hasn't billed everything yet. The trace records
the open quantity.

**R9 — Compare prices.** Each invoice line's unit price must exactly equal the
PO line's unit price (exact decimal comparison, no tolerance).
- Different → `price_mismatch` (expected = PO price, observed = invoice price)
  → **needs review**.
- Missing price on the PO or invoice, or unknown currency on either document →
  `missing_information` → **blocked**.
- Receipt prices are ignored; receipts normally have none.

**R10 — Final status.**
- **clean**: everything was checked and nothing was flagged.
- **blocked**: at least one check *could not be performed* (missing data,
  incompatible units, conflicting duplicate versions, no PO or no invoice).
  Someone has to supply information before a decision is possible.
- **needs review**: every check ran but found a problem (quantity, price,
  unmatched line, excluded duplicate). A person must decide.

Blocked takes priority over needs review.

## Calculation trace

Alongside the result, `reconcile_with_trace` returns a numbered list of steps.
Each step names the rule, the lines involved, the exact inputs (as decimal
text) and the outcome. Examples: "received total = 9 + 9 = 18", "invoiced 18 ≤
received 18: OK". Every discrepancy also appears in the trace. There are no
timestamps or random values, so running the same case twice produces an
identical trace. The trace is not yet part of the shared contract; see
[the change proposal](proposals/reconciliation-contract-changes.md).

## Worked example (the shared team fixture)

PO 20 monitors at $125.50 · invoice 20 at $125.50 · receipt 18.

1. Units: all `each` → OK.
2. Ordered 20, received 18, invoiced 20.
3. Invoiced 20 ≤ ordered 20 → OK. Received 18 ≤ ordered 20 → OK (2 still open).
4. Invoiced 20 > received 18 → **quantity_mismatch, expected 20, observed 18**.
5. Price 125.50 = 125.50 → OK.

Status: **needs review**. This matches the team's expected-result fixture.

## Not yet covered (waiting on team decisions)

- Replacement documents with an explicit "replaces X" link (today: conflicting
  versions block the case, see R2).
- Size/model conflicts beyond SKU disagreement, and a dedicated discrepancy code.
- Price tolerance (today: exact match required).
- The exact-SKU / fuzzy-text matching baseline (ownership to agree with Jijuan).
