# Integration branch — November 19, 2026

This branch combines Dhruv's orchestration, Matthew's reconciliation reliability,
Abdullah's extraction, Jijuan's matching, and the shared evaluation cases.
The branch merge retains the original commits. Main is not changed.

## Verify locally

Use Python 3.11 or newer, from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m demo.run_integrated --case case-101
python -m demo.run_integrated --case case-102
python -m demo.run_integrated --case case-104 --revision 2
python -m demo.run_integrated --case case-105 --revision 2
```

Windows activation: `.venv\Scripts\Activate.ps1`.

Expected: case 101 ready for human review; case 102 price/quantity corrections;
case 104 revision 2 pauses for duplicate-invoice review before calculation;
case 105 revision 2 flags a duplicate receipt without doubling the delivered quantity.
These commands use ideal extracted fixture records, real matching and real math.
They do not measure OCR or live AI quality. Case 103 requires semantic matching;
without an LLM client it should pause, never fabricate a match.

## Live document pipeline

1. Copy `.env.example` to `.env` and set `MATCHLEDGER_PROVIDER`,
   `MATCHLEDGER_MODEL`, and that provider's API key. Use a vision-capable model
   available on your account. Never commit `.env` or put keys in a manifest.
2. Optional semantic matching currently uses Anthropic. Set
   `MATCHLEDGER_MATCH_MODEL` and `ANTHROPIC_API_KEY` to enable it. Extraction can
   independently use OpenAI, Anthropic or Gemini. Without a matching model,
   unresolved semantic matches go to human review.
3. Run:

```sh
python -m demo.run_uploads demo/upload_manifest.json --output outputs/uploads-101.json
```

The example uses synthetic PDFs already in the repository. The manifest contains
only upload paths, IDs, declared document types and the supplier directory.
It does not contain answer-key line values or expected decisions. All line values
come from extraction. The provider is actually called, so this command incurs usage.
The output identifies the deterministic boundary audit explicitly; it is not an
LLM adversarial review or permission to pay.

For your own documents, copy the manifest and update its metadata. File paths
are relative to the manifest file. Use fresh case IDs and correct revision numbers.
The upload command writes a state snapshot. For saved SQLite cases and explicit
source replacement, use [the document-resolution commands](DOCUMENT_RESOLUTION.md);
GUI correction controls are still pending.

## Integration changes

- Matching and evaluation packages moved into shared `src/`, `eval/`, and `tests/`.
- Conflicting explicit SKUs/numeric product attributes veto a match. Equal cleaned
  candidates no longer pick whichever appears first. This conservative numeric
  guard is not a complete model/size ontology; missing attributes remain uncertain.
- Invalid confidence values are rejected, not clamped into high confidence.
- Reconciliation uses accepted PO-line identities on a private engine copy.
  Original source SKUs, descriptions, evidence and match methods remain intact.
  Non-exact matches no longer fail solely because their method is fuzzy/LLM/human.
  Missing quantity, unit or price still blocks; explicit identity/unit conflicts
  still block. No unit conversions or inferred prices were introduced.
- Extraction notices survive the handoff. Review-level warnings stop before
  matching/calculation. PO references must resolve uniquely; near-miss OCR
  references are not silently repaired.
- Multiple invoices pause before calculation. A user must identify the active
  version. No guessed replacement or double billing.

## Evidence and remaining work

The integration test suite includes actual PDF parsing/rendering with scripted
model replies, bad evidence rejection, wrong-size matching, ambiguity, invalid
confidence and cross-component fixture checks. Live model quality is not inferred
from these tests. The existing extraction measurements belong to their documented
model/prompt versions; no new live benchmark was run during this integration.

Still required before a final submission:

- Connect Maia's UI to the [saved document-resolution service](DOCUMENT_RESOLUTION.md).
  Receipt addition, explicit invoice/receipt replacement, file fingerprints and
  saved revision history are implemented. Live resolution verification and general
  retirement/PO replacement actions remain pending. Unidentified changed receipts
  must not be assumed to be independent deliveries.
- Resolve evaluation PDFs that omit units while answer keys expect `each`.
  Never fill absent units just to improve a score.
- Case 104 revisions 2/3 deliberately pause without totals. This differs from the
  current evaluation expectation of duplicate plus price findings. Agree and
  document the policy rather than claiming those full expected outputs pass.
- Live extraction plus semantic matching runs, measured baseline comparisons,
  review-agent design, dashboard, output report and Docker verification.

The reconciliation status/gap notes describe the original component. Where they
say exact matches only, this integration document describes the updated behavior.
