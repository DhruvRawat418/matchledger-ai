# Matching Agent — Procurement & Invoice Reconciliation Assistant

**Owner:** Jijuan Xu (AI-assisted matching and evaluation)
**Status:** working first version; all tests pass without an API key
**Contracts:** compliant with `src/contracts.py` v0.1 (Dhruv's shared contracts)

The matching agent pairs every **invoice and delivery-record (receipt) line**
with the purchase-order line it refers to, even when suppliers describe the
same product differently ("24-inch LED screen" vs "Display, 24 in" vs
"24in Monitor"). It returns one `MatchResult` per line and flags anything it
cannot settle as `uncertain`, so a human reviewer always sees it. It never
invents an answer.

```
ExtractedDocument[]  ──►  exact ──► cleaned ──► LLM ──►  MatchResult[]
(from Abdullah)           tier      tier        tier     (to Matthew / Dhruv / Maia)
```

Each line stops at the first tier that can settle it. Later tiers only see
what earlier tiers could not pair.

---

## Quickstart

```bash
# from the repository root (needs pydantic; pytest for tests)
python -m pytest tests/test_matching.py -q     # no API key required

# optional, only for the real LLM tier:
pip install anthropic
export ANTHROPIC_API_KEY=...
```

```python
from src.matching import match_lines

results = await match_lines(documents)          # list[MatchResult]
```

With no LLM configured this is still safe: deterministic tiers run, and every
semantic case comes back `decision="uncertain"` for human review.

---

## The three tiers = the three evaluation methods

| Evaluation method | How to run | `MatchResult.method` |
| --- | --- | --- |
| **A. Exact match** — the base case ("a very basic system": does *not* recognize "24 inch monitor" as "24 Inch Monitor") | `match_lines(docs, tiers=("exact",))` | `exact` |
| **B. Cleaned direct match** — the "data professional" who cleans data by fixed rules | `match_lines(docs, tiers=("exact", "cleaned"))` | `fuzzy` |
| **C. AI agent** — our system | `match_lines(docs, llm=AnthropicMatcher())` | `llm` on top of A+B |

**Tier details**

- **exact** — raw, case-sensitive string equality on SKU, else on
  description; the hit must be unique.
- **cleaned** — both descriptions pass the frozen cleaning rules, then are
  compared directly. A bounded similarity fallback (token overlap +
  `difflib` ratio, threshold **0.8**, unique best by a **0.15** margin)
  catches residual spacing/gluing like "500 sheets" vs "500sheets". String
  evidence only — this tier cannot and must not know that *display* =
  *monitor*. For a strict cleaned-equality-only baseline, pass
  `config=MatchingConfig(match_threshold=1.01)` (disables the fallback).
- **llm** — an injected client judges only the leftovers. It is shown
  description, SKU and unit — **never prices or quantities**, so a price or
  quantity error (which reconciliation must surface) cannot bias product
  identity. Confidence ≥ **0.8** → `matched`, or `unmatched` for
  not-on-PO items (e.g. an unordered shipping fee). Anything less →
  `uncertain`.

**Frozen cleaning rules** (agreed in the evaluation plan — do not tune after
seeing results): lowercase; trim; underscores/hyphens/repeated spaces →
single space; remove punctuation and supplier suffix codes (`_SB`, `-NEW`,
`(pack)`); standardize units (`24 inch` / `24in` / `24"` → `24in`,
`2 lb` → `2lb`); fixed abbreviation list (`qty`, `pcs`, `ea`).

---

## Guarantees

1. **Contract-true.** Sources are invoice/receipt lines only; `matched`
   always selects a supplied PO candidate; `uncertain`/`unmatched` select
   none; `case_id`/`revision` come from the documents; evidence is the source
   line's own extracted evidence. Every batch passes `validate_handoff`
   before it is returned.
2. **No invented IDs.** The LLM may only answer with a supplied candidate
   key. A hallucinated reference is downgraded to `uncertain`.
3. **No fake AI.** No LLM client, an API failure, or low confidence all
   degrade to `uncertain` — visible for review, never silently wrong.
4. **A model suggestion is not human approval.** `matched` by `llm` is a
   proposal. Per `MATTHEW_INTEGRATION.md`, the reconciliation adapter
   currently consumes exact-SKU links only; LLM/fuzzy links wait on the
   agreed acceptance/mapping step.

---

## Wiring into the graph (Dhruv)

```python
from src.matching import make_match_worker
from src.llm_matcher import AnthropicMatcher   # optional

workers = Workers(..., match=make_match_worker(llm=AnthropicMatcher()), ...)
```

`make_match_worker(...)` returns an async adapter that accepts the case state
(reads `state["documents"]`; a plain document list also works) and returns
`MatchResult` dicts. Provider timeouts/bounded retries belong at this
integration layer (listed as future work in `ORCHESTRATION.md`).

## LLM backends

`src/llm_matcher.py` ships the Anthropic backend: temperature 0, fixed system
prompt, strict JSON parsing, import-guarded so nothing else needs the SDK.
Any backend implementing the two-method `SupportsLlmMatch` protocol
(`judge(source, candidates) → LlmJudgement`) plugs in — this is where
OpenAI/Gemini backends go for the multi-LLM comparison in the report.

## Evaluation

Ground-truth cases live in `eval/cases/` (see `eval/GROUND_TRUTH_FORMAT.md`
and `eval/cases/CASE_NOTES.md`). Behavior verified by
`tests/test_matching.py`:

| Case | Level | Verified behavior |
| --- | --- | --- |
| 101 | exact | A matches everything; any alert here is a false alarm |
| 102 | fuzzy | A fails entirely; B recovers all lines and both planted errors stay visible |
| 103 | llm | A and B leave synonyms unresolved (never wrong); truthful LLM resolves them; the 24/27in lookalike is never mismatched |
| 002 | mixed | Unordered shipping fee → `unmatched`, not forced onto a PO line |
| 104/105 | human error | Trivial matching, so duplicate-document handling is tested upstream of this agent |

Human-review rate (the AI-only metric) = share of results with
`decision == "uncertain"`.

## Files

| File | What it is |
| --- | --- |
| `src/matching.py` | The agent: cleaning rules, three tiers, graph adapter. No deps beyond the contracts |
| `src/llm_matcher.py` | Anthropic LLM backend (optional) |
| `tests/test_matching.py` | Behavior tests against the ground-truth cases |
| `docs/MATCHING.md` | Design notes and contract-compliance detail |

## Open questions / limitations

- `MatchResult.method` has no `normalized` value, so the cleaned tier reports
  as `fuzzy` — contract change pending with Dhruv.
- Only the Claude backend exists; ChatGPT/Gemini backends are a ~60-line
  pattern each against the same protocol.
- Candidates come from the intake-resolved `purchase_order_id` (all POs if
  unresolved); multi-PO disambiguation beyond that is out of scope for v1.
- Thresholds (0.8 / 0.15 margin / 0.8 LLM confidence) are fixed before
  evaluation; tune only on the small dev set, never on test cases.
