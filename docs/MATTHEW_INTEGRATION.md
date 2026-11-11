# Matthew's reconciliation integration

## Contribution provenance

Matthew Suruadji supplied `multiagent-procurement-reconciliation.zip`. His engine
and schemas are imported unchanged under `procurement_recon/`; his original
38 test cases are retained as `tests/test_matthew_rules.py`.
Dhruv's integration adds `src/reconciliation_adapter.py`, contract fields,
graph wiring and integration tests. ZIP delivery has no imported Git author
history; this note records source ownership without inventing commit authorship.
The ZIP's placeholder agents, alternative orchestrator, frontend, Git metadata,
CI configuration and generated files were not imported.

## What's real now

The clean/short-delivery demos and saved-case commands use the real engine.
Extraction, matching and evidence review remain synthetic. The mode label is
`synthetic_inputs_real_reconciliation`; this is not end-to-end live AI.

The adapter converts decimal strings to Decimal, validates required headers and
units, invokes Matthew's `reconcile`, and translates findings back to the shared
contract. It does not rewrite his calculations or substitute fake supplier IDs.
Internal engine totals are not yet exposed in our graph contract. The original
engine tests verify them; dashboard totals need a later agreed output contract.
Do not sum individual discrepancy amounts: findings may overlap.

## Current supported boundary

- One active PO, one active invoice, one to three active receipts.
- Explicit USD currency and supplier IDs on PO/invoice; a resolved PO document
  link on invoice/receipts. Missing fields block instead of becoming defaults.
- Exact SKU links and identical units only. LLM/fuzzy/human semantic links still
  require a designed acceptance/mapping step before this adapter can use them.
- Repeated SKUs within a single document are blocked by the engine's schema.
- Case revision is used for the converted active snapshot. Historical document
  version selection is not wired into the graph yet. Do not infer a new delivery
  from a changed file hash or ID alone. Cross-ID duplicate detection remains future work.
- Supplier mismatch, price mismatch and quantity discrepancies remain visible.

For 20 ordered / 18 received / 20 invoiced, both MISSING_DELIVERY and
BILLED_EXCEEDS_RECEIVED map to `quantity_mismatch`; detailed explanations retain
the original finding type. These are two checks, not two additional shortfalls.

## Run

```bash
python -m pytest -q
python -m demo.run_workflow --scenario clean
python -m demo.run_workflow --scenario short-delivery
python -m demo.saved_cases start matthew-correction-001
```

Follow `docs/SAVED_CASES.md` with that new ID to correct 18 to 20 after a confirmed
synthetic extraction mistake. Old stored cases without the new header fields
remain readable; reruns block until valid header data can be supplied. Do not
silently add invented supplier identifiers to old cases.
