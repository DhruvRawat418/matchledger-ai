# Matching agent (Jijuan)

`src/matching.py` pairs every invoice/receipt line with a purchase-order line
and returns one `MatchResult` per line, per the shared contracts. It
implements the proposed worker interface from DATA_CONTRACTS:

```python
from src.matching import match_lines
results = await match_lines(documents)              # list[MatchResult]
```

## Three tiers = the three evaluation methods

| Evaluation method | `tiers=` | `MatchResult.method` |
| --- | --- | --- |
| A. Exact match (base case) | `("exact",)` | `exact` |
| B. Cleaned direct match (data professional) | `("exact", "cleaned")` | `fuzzy` |
| C. AI agent (our system) | default, with `llm=` client | `llm` (on top of A+B) |

- **exact** — raw string equality on SKU, else on description; must be unique.
- **cleaned** — both descriptions pass the frozen cleaning rules (lowercase;
  underscores/hyphens to spaces; drop punctuation and supplier suffix codes
  `_SB`/`-NEW`/`(pack)`; normalize units `24 inch`/`24"` → `24in`; short fixed
  abbreviation list). Cleaned equality matches; otherwise a bounded similarity
  score (token overlap + `difflib` ratio, threshold 0.8, unique best by a 0.15
  margin) catches residual spacing/gluing. String evidence only — it cannot
  and must not "know" that display = monitor. Reported as `fuzzy`, the closest
  value contracts v0.1 allows (adding `normalized` is an open question for Dhruv).
- **llm** — an injected client judges only the lines the deterministic tiers
  left over. It sees description, SKU and unit — **not prices or quantities**,
  so price/quantity discrepancies cannot bias product identity. Confidence ≥
  0.8 becomes `matched` (or `unmatched` for not-on-PO items like a shipping
  fee); anything less stays `uncertain` for human review.

## Contract compliance

- Sources are invoice/receipt lines only; candidates are the lines of the PO
  resolved by intake (`purchase_order_id`), or all POs if unresolved.
- `matched` always selects a supplied candidate. The LLM can only answer with
  a supplied candidate key; an invented reference is downgraded to
  `uncertain` ("do not let the model generate workflow IDs").
- `case_id`/`revision` come from the documents. Evidence is the source line's
  own extracted evidence.
- Output is checked with `validate_handoff` before being returned.
- No LLM client configured ⇒ semantic lines come back `uncertain`, never a
  fabricated answer (ORCHESTRATION: the graph never defaults to fake AI).
- A model suggestion is not human approval: `matched` by `llm` still flows to
  Matthew/Maia as a *proposed* link; per MATTHEW_INTEGRATION, the adapter
  consumes exact SKU links today, so LLM links wait on the agreed
  acceptance/mapping step.

## Wiring for Dhruv

```python
from src.matching import make_match_worker
from src.llm_matcher import AnthropicMatcher          # optional, needs anthropic + API key

workers = Workers(..., match=make_match_worker(llm=AnthropicMatcher()), ...)
```

`make_match_worker(...)` returns an async adapter that takes the case state
(reads `state["documents"]`, or a plain list) and returns `MatchResult`
dictionaries. Timeouts/bounded retries around the provider call are listed as
future work in ORCHESTRATION and belong at that integration layer.

`src/llm_matcher.py` holds the Anthropic backend (temperature 0, fixed system
prompt, strict JSON parsing). It is import-guarded: nothing else needs the
`anthropic` package or an API key, and all tests run without either.

## Tests

`tests/test_matching.py` (51 total with the ground-truth suite) scores the
agent against `eval/cases/`: exact matches only identical lines; cleaning
recovers case-102 and never picks a wrong line on the 24/27in lookalike;
synonyms without an LLM surface as `uncertain`; a scripted truthful LLM
resolves case-103 and the case-002 shipping fee (`unmatched`); invented IDs,
low confidence and API failures all degrade to `uncertain`.
