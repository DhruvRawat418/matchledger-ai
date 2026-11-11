# First LangGraph workflow

This is real LangGraph routing with explicitly synthetic workers. No model API
calls, OCR, matching algorithm, full reconciliation engine or report generation
has been implemented. The demo includes a limited deterministic quantity check.

## Run it

```bash
python -m pip install -r requirements-dev.txt
python -m demo.run_workflow --scenario clean
python -m demo.run_workflow --scenario short-delivery
python -m pytest -q
```

The clean fixture ends `ready_for_human_review`; the short-delivery fixture ends
`needs_correction` with a `quantity_mismatch` issue. Both visit:

```text
extract -> match -> reconcile -> review -> finish
```

Invalid output or worker exceptions end `blocked` at the responsible stage.
Uncertain/unmatched decisions end `needs_correction` before reconciliation.
An empty evidence-review response cannot clear reconciliation discrepancies.

## Where LangGraph is used

`src/graph.py` constructs a `StateGraph`, registers each worker as a node, adds
conditional edges, and compiles it. The demo calls `await graph.ainvoke(...)`.
VS Code is the editor; the installed LangGraph Python library runs the graph.
Studio is not required for this example.

Reference: https://reference.langchain.com/python/langgraph/graph/state/StateGraph

## Files to read in order

1. `src/state.py`: case state and the initial evidence-review response contract.
2. `demo/sample_workers.py`: temporary, predefined agent responses.
3. `src/graph.py`: boundary validation and routing, with no business arithmetic.
4. `demo/run_workflow.py`: invokes the graph and prints a short summary.

## Connecting real workers later

`build_graph(Workers(extract=..., match=..., reconcile=..., review=...))`
requires four asynchronous adapters. Each receives a copy of the current state
and returns a dict/list matching its contract:

| Adapter | Output |
| --- | --- |
| extract | list of ExtractedDocument dictionaries |
| match | list of MatchResult dictionaries, one per invoice/receipt line |
| reconcile | ReconciliationResult dictionary |
| review | ReviewResult dictionary (see src/state.py) |

These are graph adapters, not a demand that teammates change their internal
function signatures. Wrap their functions and pass only the fields they need.
The demo imports sample workers explicitly; the graph never defaults to fake AI.
IDs/revisions must come from the case, not a model. Unsupported/incomplete
business data remains the workers' responsibility; schema validity alone is not
proof of correct extraction, matching or reconciliation.

## Current limits and next work

- The graph itself is one-pass; `case_service` now saves case revisions in SQLite.
  See [saved cases](SAVED_CASES.md) for correction and interrupted-run recovery.
  No node-level checkpoint or real uploads yet.
- `needs_correction` ends a graph run. A recorded correction starts a new revision
  and reruns the graph; it is not a LangGraph interrupt.
- Review findings identify the responsible component but do not trigger automatic
  repair loops yet. The saved-case service supports confirmed receipt-quantity
  corrections; other human decisions are future work.
- Async provider adapters will need timeouts and bounded retries when integrated.
- Ready for human review is not approval and never authorizes payment.
