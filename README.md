# MatchLedger AI

Procurement and invoice reconciliation assistant — team starter.

**Current branch: `Dhruv-orchestration`.** This is a draft contract foundation,
not the completed application. There is a LangGraph skeleton with synthetic workers, but no live agents,
dashboard or Docker container yet. No API key is needed for this example.

## Run locally (Python 3.11 or newer)

```bash
git clone --branch Dhruv-orchestration https://github.com/DhruvRawat418/matchledger-ai.git
cd matchledger-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m demo.contracts_example
python -m pytest -q
```

Windows activation: `.venv\Scripts\Activate.ps1` in PowerShell.

The demo prints a synthetic PO/invoice/receipt handoff and an expected result.
It validates data formats; it does not read documents or calculate discrepancies.
See [the contract guide](docs/DATA_CONTRACTS.md) before implementing your worker.

## Branch workflow

Create your own branch from this starter once it is available remotely:

```bash
git fetch origin
git switch -c YourName-component origin/Dhruv-orchestration
```

Keep work off `main` until the team finalizes the project. Coordinate contract
changes with every consuming component; don't silently rename shared fields.
Do not commit keys, real business documents or local environments.

## Orchestration demo

```bash
python -m demo.run_workflow --scenario clean
python -m demo.run_workflow --scenario short-delivery
```

See [the orchestration guide](docs/ORCHESTRATION.md) for routing, worker adapters
and current limitations. These commands run real LangGraph with predefined
extraction/matching/review responses with Matthew’s real reconciliation engine.
The engine calculates totals internally; the graph currently exposes discrepancies,
not those totals. No live AI is connected.

## Saved cases and correction demo

Follow [the saved-case guide](docs/SAVED_CASES.md) to create a case, reopen it,
record a confirmed receipt-quantity correction and rerun checks. SQLite saves
case revisions; this is not node-level LangGraph checkpointing.

See [Matthew integration notes](docs/MATTHEW_INTEGRATION.md) for provenance,
contract additions and exact-SKU limitations.
