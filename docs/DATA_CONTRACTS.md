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
