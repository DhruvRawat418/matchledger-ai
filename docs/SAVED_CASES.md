# Saved cases and human corrections

This local demo uses SQLite (`outputs/cases.sqlite`) and requires no API key.
Run commands from the repository root with `.venv` activated. Extraction,
matching and evidence review are synthetic. A small deterministic demo worker
checks the saved quantities; it is not Matthew's full reconciliation engine.

## 1. Create a short-delivery case

```bash
python -m demo.saved_cases start correction-001 --scenario short-delivery
```

Expected: revision 1, `needs_correction`, `quantity_mismatch`.
The same ID cannot be started again; use `show` or create a new ID.

## 2. Read it in a new terminal/process

```bash
python -m demo.saved_cases show correction-001
```

The saved status persists. Reading a case does not rerun workers.

## 3. Record a confirmed extraction correction

For this synthetic test ONLY, assume the source receipt says 20 and the extracted
18 was a mistake. If the actual receipt says 18, do not change it to pass checks.

```bash
python -m demo.saved_cases correct correction-001 \
  --revision 1 \
  --document receipt-001 \
  --line receipt-001-line-1 \
  --old 18 --new 20 \
  --staff "Dhruv" \
  --reason "Synthetic test: receipt quantity was extracted incorrectly" \
  --source-confirmation "Synthetic source receipt confirms 20 monitors received" \
  --confirm
```

Expected: revision 2, `ready_for_human_review`, no current issues. The history
records staff, reason, confirmation, old/new quantities and UTC timestamp.
Read the case again to verify persistence. Ready means automated checks finished,
not payment authorization or final human approval.

Only receipt quantity correction is supported in this first version. Missing
quantities, prices, semantic match decisions, actual additional deliveries and
replacement documents need separate actions; do not use this action for them.
The operator's confirmation is recorded, not independently verified against an
image. The original extracted evidence quotation remains intact; corrections
are supplied separately in graph state for reviewers to inspect.

## Recovery after an interrupted run

If a process stops while status is `running`, reopen the same database and run:

```bash
python -m demo.saved_cases resume correction-001
```

This reruns that revision from the beginning. It does not resume a LangGraph
node checkpoint. Corrected documents are reused and all downstream outputs are
recomputed. Workers must have no external side effects (no payments or messages).
An ordinary worker error saves `blocked`; recovery of blocked provider failures
is not implemented in this step. A reviewed/paused case cannot be resumed using
this command; a valid correction must go through `correct`.

## Dashboard integration later

`src/case_service.py` exposes `start_case`, `correct_receipt_quantity` and
`resume_case`; `CaseStore.load` reads state/history without model calls.
`correct_receipt_quantity` checks the expected revision, original value, nonempty
staff/reason/source confirmation and explicit confirmation. It saves the audit
event and new revision with empty results before running the graph.

SQLite stores the current state, prior revision snapshots and correction events
in one atomic update. Compare-and-swap saves reject a stale writer. This remains
a local single-operator demo, not an authenticated multi-user service. Do not run
two commands on the same case simultaneously. A future job lock is needed to
prevent duplicate worker calls during concurrent resume attempts.

Use `--db PATH` before the subcommand to select a different database. Use the
same path for subsequent commands. The default path is relative to the working
directory. Databases are ignored by Git; back them up separately if needed.
