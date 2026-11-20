# Shared contracts — v0.1 draft

A contract is the shape of data one component promises to return and another
expects to receive. `src/contracts.py` is the executable source of truth.
Pydantic rejects unknown fields and invalid shapes rather than silently losing data.

## Ownership and handoffs

| Producer | Output | Consumer |
| --- | --- | --- |
| Abdullah: extraction | `ExtractedDocument` | Jijuan: matching; Matthew: reconciliation |
| Jijuan: matching | `MatchResult` per invoice/receipt line | Matthew; Dhruv; Maia |
| Matthew: deterministic checks | `ReconciliationResult` | Dhruv: routing; Maia: display |

Dhruv coordinates changes. Evidence-review and human-action contracts will be
added with those components; the state machine and LangGraph graph are next.

## Rules we must all use

- `document_type`: `purchase_order`, `invoice`, or `receipt`.
- A line reference includes BOTH document ID and line ID.
- `revision` is the case input revision, not a document version. All current
  outputs must refer to the same case and revision.
- Quantities/prices are nonnegative decimal STRINGS. `"20"`, `"125.50"` are valid;
  numeric JSON values, scientific notation, commas and negative values are not.
  Returns/credits are outside this initial scope.
- Missing values remain `null`; never infer zero from an unreadable field.
- Source evidence carries a 1-based page and visible supporting text. An evidence
  list is not proof of correctness; extraction/review must verify it.
- Currency is `USD` or unknown (`null`). Unknown currency must be resolved before
  monetary checks. Missing prices on receipts are expected.
- `matched` selects one supplied PO candidate. `uncertain` and `unmatched` select
  none and must remain visible for review. A model suggestion is not human approval.
- `clean` requires checked lines and no discrepancies. Workers must additionally
  establish completeness, valid units/currency and correct amounts. Schema
  validation alone cannot certify a clean result.
- `validate_handoff` rejects stale cases, invented references and duplicate
  decisions. It does not perform semantic matching or arithmetic.

## Integration pattern

```python
from src.contracts import ExtractedDocument

document = ExtractedDocument.model_validate(worker_output)
payload_for_next_worker = document.model_dump(mode="json")
```

Proposed worker interfaces (agree before implementation):

```python
async def extract_document(request) -> ExtractedDocument: ...
async def match_lines(documents: list[ExtractedDocument]) -> list[MatchResult]: ...
def reconcile(documents: list[ExtractedDocument], matches: list[MatchResult]) -> ReconciliationResult: ...
```

The extraction request will be defined separately: Dhruv assigns case/document
IDs and the revision; the extractor receives a validated local upload reference.
Do not let the model generate workflow IDs. Keep provider/API concerns inside
workers and return validated data to the graph.

## Shared first example

`python -m demo.contracts_example` supplies 20 ordered / 20 invoiced / 18 received
monitors. All lines refer to `MON-24`. Expected outcome: `needs_review` because
the received quantity is short by two. The result is a labelled fixture, not a
calculation implementation. Everyone should use it to agree on their handoff.

## Next team check

1. Read and agree on the three contracts.
2. Propose changes together before renaming fields.
3. Implement workers using the shared fixture.
4. Dhruv connects placeholders in LangGraph, then replaces each with a worker.
5. Add human decisions, revision invalidation and review contracts together.

## Reconciliation integration additions

Extraction now supports nullable `supplier_id` and `purchase_order_id`. For actual
reconciliation, PO/invoice supplier IDs and USD currency are required. Invoice
and receipt `purchase_order_id` is an explicitly resolved internal PO document ID,
not a model-invented ID or unverified source business number. Intake/linking must
resolve this from source references. Null blocks computation; no fallback values.
`src/reconciliation_adapter.py` currently supports exact SKU matches only.

## PROPOSED: reconciliation output extension (pending Dhruv + Maia approval)

> **Status: proposal on `feature/reconciliation-worker`, not yet approved.**
> Needs review by Dhruv (contracts and graph) and Maia (display). Until it is
> approved, treat this section as a draft. Every rule above still applies.

**What changes in `src/contracts.py`:** three optional fields are added.
Nothing is renamed or removed. Each new field defaults to empty, so any
producer, test or stored case that leaves them out still validates. All
numbers follow the existing rules: nonnegative decimal strings, and `null`
means unknown, never zero.

| Field | Where | Meaning |
| --- | --- | --- |
| `quantities` | `ReconciliationResult`, list, default `[]` | One `QuantityTotals` per PO line: `po_line` (a line reference), `ordered`, `received`, `invoiced`, plus `calculation`, e.g. `received = 18 [receipt-1] + 2 [receipt-2] = 20`. Empty means the totals were not calculated (for example, a blocked case). |
| `amounts` | `ReconciliationResult`, default `null` | `AmountTotals` at PO prices: `currency`, `billed`, `supported`, `unsupported`, `unbilled`. `null` means not calculated. |
| `calculation` | `Discrepancy`, default `null` | Plain-text arithmetic behind the finding, e.g. `billed 20 - received 18 = 2 units x PO price 125.50 = 251.00`. Present on every finding that involves arithmetic. Negative numbers may appear inside this sentence as words; they are never sent as number fields. |

**Amounts, all nonnegative:**

- `billed` = total on the invoice.
- `supported` = PO-priced value of the goods that were both ordered **and** received.
- `unsupported` = how much the invoice bills **above** `supported`. It is `0`
  when the invoice bills at or below `supported`.
- `unbilled` = how much of `supported` the invoice does **not** bill. Causes
  include under-billing, a lower invoice price, or billing not yet complete.
  It is `0` otherwise.
- At most one of `unsupported` and `unbilled` is above zero.
- These are review figures. They are never payment instructions.

Worked example: PO 20 at 125.50, 18 received, 20 invoiced. Billed 2510.00,
supported 2259.00, unsupported 251.00, unbilled 0.

**Expected / observed rule.** This is a proposed wording of how the engine
already behaves:

> `expected` = the value from the **reference** document (the PO, or what was
> actually received). `observed` = the value on the **document being checked**.

| Engine finding (in the explanation prefix) | `code` | expected | observed |
| --- | --- | --- | --- |
| `MISSING_DELIVERY` | `quantity_mismatch` | ordered | received |
| `OVER_DELIVERY` | `quantity_mismatch` | ordered | received |
| `BILLED_EXCEEDS_RECEIVED` | `quantity_mismatch` | received | invoiced |
| `BILLED_EXCEEDS_ORDERED` | `quantity_mismatch` | ordered | invoiced |
| `UNDER_BILLED` | `quantity_mismatch` | lower of ordered and received | invoiced |
| `PRICE_MISMATCH` | `price_mismatch` | PO unit price | invoice unit price |
| `UNMATCHED_INVOICE_LINE` | `unmatched_item` | null | invoiced quantity |
| `UNMATCHED_DELIVERY_LINE` | `unmatched_item` | null | received quantity |
| `SUPPLIER_MISMATCH` | `supplier_mismatch` | null | null |
| `CURRENCY_MISMATCH` | `missing_information` | null | null |
| Adapter checks (blocked inputs, repeated receipts) | various | null | null |

For the shared first example (20 ordered, 18 received, 20 invoiced), this gives
two findings:

- `MISSING_DELIVERY` with expected 20 and observed 18.
- `BILLED_EXCEEDS_RECEIVED` with expected 18 and observed 20.

The fixture's numbers (20 → 18) match the first finding. Its explanation text,
"20 invoiced, 18 received", would read more accurately as "20 ordered, 18
received". That rewording is suggested only; the fixture has not been changed.

**Open points for Dhruv:**

1. Should the graph also check that each `quantities[].po_line` refers to an
   existing PO line, as it already does for `checked_lines`?
2. Should `quantities` be required whenever the status is not `blocked`?

Tests: `tests/test_reconciliation_output.py`.
