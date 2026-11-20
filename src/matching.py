"""Matching agent: pairs invoice/receipt lines with purchase-order lines.

Owner: Jijuan (AI matching and evaluation).

Three tiers, matching the evaluation design (exact / cleaned direct / LLM):

  "exact"    the base case: raw string equality on SKU, else on description.
  "cleaned"  the "skilled data professional": both strings are cleaned with
             the frozen rules below, then compared directly, with a bounded
             similarity fallback for residual spacing/gluing differences.
             Reported as method "fuzzy" (the closest value contracts v0.1
             allows; adding "normalized" is an open contract question).
  "llm"      the AI contribution: an injected LLM judges the lines the
             deterministic tiers could not pair. Confident agreement becomes
             "matched", confident absence becomes "unmatched", anything else
             stays "uncertain" and visible for human review.

Contract compliance (src/contracts.py, DATA_CONTRACTS.md):
  * one MatchResult per invoice/receipt line; PO lines are never sources;
  * "matched" selects one supplied PO candidate; "uncertain"/"unmatched"
    select none; the LLM can only pick from supplied candidates, so it can
    never invent a workflow ID;
  * case_id/revision are taken from the documents, never generated;
  * evidence is the source line's own extracted evidence (visible text);
  * outputs are checked with validate_handoff before being returned.

Run tiers=("exact",) or ("exact", "cleaned") to reproduce the evaluation
baselines; the default runs all three. Without an LLM client the leftover
lines of a full run are flagged "uncertain" for human review - the agent
never fakes an AI answer (see ORCHESTRATION.md: no default fake AI).
"""
from __future__ import annotations

import math
import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from src.contracts import ExtractedDocument, LineItem, MatchResult, validate_handoff

Tier = str  # "exact" | "cleaned" | "llm"
DEFAULT_TIERS: tuple[Tier, ...] = ("exact", "cleaned", "llm")

# ------------------------------------------------------------ cleaning
# The frozen "data professional" rules agreed in the evaluation plan.
# Do not tune these after seeing test results.

_UNIT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d+)\s*(?:inches|inch|in\.|in\b|\")"), r"\1in"),
    (re.compile(r"(\d+)\s*(?:pounds|pound|lbs|lb)\b"), r"\1lb"),
)
# Standalone supplier suffix/code tokens (from "_SB", "-NEW", "(pack)", ...).
_SUFFIX_TOKENS = frozenset({"sb", "new", "pack", "promo", "bulk"})
# Short fixed abbreviation list ("qty", "pcs", "ea" and nothing clever).
_ABBREVIATIONS = {"qty": "quantity", "pcs": "pieces", "ea": "each"}


def clean(text: str) -> str:
    """Apply the frozen cleaning rules to one description."""
    t = text.lower().strip()
    t = re.sub(r"[_\-]+", " ", t)                 # underscores/hyphens -> spaces
    for pattern, replacement in _UNIT_RULES:      # 24 inch / 24in / 24" -> 24in
        t = pattern.sub(replacement, t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)            # drop punctuation
    tokens = [_ABBREVIATIONS.get(tok, tok) for tok in t.split()]
    tokens = [tok for tok in tokens if tok not in _SUFFIX_TOKENS]
    return " ".join(tokens)


def similarity(a: str, b: str) -> float:
    """Similarity of two cleaned strings in [0, 1]. String evidence only -
    deliberately unable to see that 'display' and 'monitor' are synonyms."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    containment = len(ta & tb) / min(len(ta), len(tb))
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return max(containment, ratio)


# ------------------------------------------------------------ LLM port

@dataclass(frozen=True)
class LlmLineView:
    """What the LLM is shown about a line: identity text only. Quantities and
    prices are withheld so price/quantity discrepancies (which reconciliation
    must surface) cannot bias the product-identity judgement."""
    line_key: str          # "document_id::line_id"
    description: str | None
    sku: str | None
    unit: str | None


@dataclass(frozen=True)
class LlmJudgement:
    selected_line_key: str | None  # a candidate line_key, or None for "not on this PO"
    confidence: float              # 0..1
    reason: str


class SupportsLlmMatch(Protocol):
    """Any LLM backend (Anthropic, OpenAI, a test fake) the agent can use."""

    async def judge(self, source: LlmLineView,
                    candidates: Sequence[LlmLineView]) -> LlmJudgement: ...


class LlmError(RuntimeError):
    """Raised by LLM backends on transport/parse failure; mapped to 'uncertain'."""


@dataclass(frozen=True)
class MatchingConfig:
    match_threshold: float = 0.8   # cleaned-tier score needed to match
    margin: float = 0.15           # best must beat runner-up by this much
    llm_accept: float = 0.8        # LLM confidence needed to act on its answer
    suffix_tokens: frozenset[str] = field(default=_SUFFIX_TOKENS)


# ------------------------------------------------------------ the agent

def _key(document_id: str, line_id: str) -> str:
    return f"{document_id}::{line_id}"


def _ref(document_id: str, line_id: str) -> dict[str, str]:
    return {"document_id": document_id, "line_id": line_id}


@dataclass(frozen=True)
class _Candidate:
    document_id: str
    line: LineItem

    @property
    def key(self) -> str:
        return _key(self.document_id, self.line.line_id)


def _candidates_for(source_doc: ExtractedDocument,
                    po_docs: list[ExtractedDocument]) -> list[_Candidate]:
    """Lines of the PO linked by intake (purchase_order_id); if intake has not
    resolved the link, all PO documents in the case are candidates."""
    linked = [d for d in po_docs if d.document_id == source_doc.purchase_order_id]
    return [_Candidate(d.document_id, line)
            for d in (linked if source_doc.purchase_order_id is not None else po_docs) for line in d.line_items]


def _result(source_doc: ExtractedDocument, line: LineItem,
            candidates: list[_Candidate], selected: _Candidate | None,
            decision: str, method: str, explanation: str) -> MatchResult:
    return MatchResult.model_validate({
        "case_id": source_doc.case_id,
        "revision": source_doc.revision,
        "source": _ref(source_doc.document_id, line.line_id),
        "candidate_po_lines": [_ref(c.document_id, c.line.line_id) for c in candidates],
        "selected_po_line": (_ref(selected.document_id, selected.line.line_id)
                             if selected else None),
        "decision": decision,
        "method": method,
        "explanation": explanation,
        "evidence": [e.model_dump() for e in line.evidence],
    })


def identity_conflict(source: LineItem, target: LineItem) -> bool:
    """Conservative veto: explicit SKU or numeric identity differences require review.

    Missing attributes never prove equality. This is not a general product ontology.
    Quantity and price are deliberately excluded from identity decisions.
    """
    if source.sku and target.sku and source.sku.strip().upper() != target.sku.strip().upper():
        return True
    a = set(re.findall(r"\d+(?:\.\d+)?", source.description or ""))
    b = set(re.findall(r"\d+(?:\.\d+)?", target.description or ""))
    return bool(a and b and a != b)


def _exact_pick(line: LineItem, candidates: list[_Candidate]) -> tuple[_Candidate, str] | None:
    """Raw string equality on SKU, else on description; must be unique."""
    if line.sku:
        hits = [c for c in candidates if c.line.sku == line.sku]
        if len(hits) == 1:
            return hits[0], f"SKU {line.sku} is identical on the purchase order"
    if line.description:
        hits = [c for c in candidates
                if c.line.description and c.line.description.strip() == line.description.strip()]
        if len(hits) == 1:
            return hits[0], "Description is identical on the purchase order"
    return None


def _cleaned_pick(line: LineItem, candidates: list[_Candidate],
                  config: MatchingConfig) -> tuple[_Candidate, str] | None:
    """Cleaned equality first; then bounded similarity with a unique-best margin."""
    if not line.description:
        return None
    source = clean(line.description)
    if not source:
        return None
    scored: list[tuple[float, _Candidate]] = []
    equal = [c for c in candidates if c.line.description and clean(c.line.description) == source]
    if len(equal) > 1:
        return None
    for c in candidates:
        if not c.line.description:
            continue
        target = clean(c.line.description)
        if source == target:
            return c, "Descriptions are identical after the agreed cleaning rules"
        scored.append((similarity(source, target), c))
    scored.sort(key=lambda sc: sc[0], reverse=True)
    if not scored or scored[0][0] < config.match_threshold:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < config.margin:
        return None  # ambiguous between two PO lines - leave for LLM/human
    best_score, best = scored[0]
    return best, (f"Cleaned descriptions agree (similarity {best_score:.2f}) "
                  "and no other purchase-order line comes close")


async def _llm_decide(llm: SupportsLlmMatch, source_doc: ExtractedDocument,
                      line: LineItem, candidates: list[_Candidate],
                      config: MatchingConfig) -> MatchResult:
    views = [LlmLineView(c.key, c.line.description, c.line.sku, c.line.unit)
             for c in candidates]
    src_view = LlmLineView(_key(source_doc.document_id, line.line_id),
                           line.description, line.sku, line.unit)
    try:
        verdict = await llm.judge(src_view, views)
    except LlmError as exc:
        return _result(source_doc, line, candidates, None, "uncertain", "llm",
                       "LLM unavailable or returned an unusable answer; needs human review")
    if isinstance(verdict.confidence, bool) or not isinstance(verdict.confidence, (int, float)) or not math.isfinite(verdict.confidence) or not 0 <= verdict.confidence <= 1:
        return _result(source_doc, line, candidates, None, "uncertain", "llm",
                       "Invalid model confidence; needs human review")
    by_key = {c.key: c for c in candidates}
    if verdict.selected_line_key is not None and verdict.selected_line_key not in by_key:
        # The model referred to a line we never offered. Never trust invented IDs.
        return _result(source_doc, line, candidates, None, "uncertain", "llm",
                       "LLM referred to a line that was not a supplied candidate; needs human review")
    reason = (verdict.reason or "no reason given").strip() or "no reason given"
    if verdict.selected_line_key is None:
        if verdict.confidence >= config.llm_accept:
            return _result(source_doc, line, candidates, None, "unmatched", "llm",
                           f"LLM (confidence {verdict.confidence:.2f}): not on this purchase order - {reason}")
        return _result(source_doc, line, candidates, None, "uncertain", "llm",
                       f"LLM is unsure whether this is on the purchase order ({verdict.confidence:.2f}): {reason}")
    if verdict.confidence >= config.llm_accept:
        return _result(source_doc, line, candidates, by_key[verdict.selected_line_key],
                       "matched", "llm",
                       f"LLM (confidence {verdict.confidence:.2f}): {reason}")
    return _result(source_doc, line, candidates, None, "uncertain", "llm",
                   f"LLM suggests {verdict.selected_line_key} but confidence "
                   f"{verdict.confidence:.2f} is below {config.llm_accept:.2f}; needs human review")


async def match_lines(documents: Sequence[ExtractedDocument], *,
                      llm: SupportsLlmMatch | None = None,
                      tiers: Sequence[Tier] = DEFAULT_TIERS,
                      config: MatchingConfig | None = None) -> list[MatchResult]:
    """One MatchResult per invoice/receipt line, in document order.

    tiers selects the evaluation condition:
      ("exact",)                     method A - base case
      ("exact", "cleaned")           method B - cleaned direct match
      ("exact", "cleaned", "llm")    method C - the AI agent (default)

    In a deterministic-only run, leftover lines are "unmatched": that is the
    baseline's actual claim. In a full run, leftovers are "uncertain" so they
    reach human review (with no llm client supplied, every semantic case lands
    there - the agent never invents an AI answer).
    """
    config = config or MatchingConfig()
    unknown = set(tiers) - {"exact", "cleaned", "llm"}
    if unknown:
        raise ValueError(f"Unknown tiers: {sorted(unknown)}")
    po_docs = [d for d in documents if d.document_type == "purchase_order"]
    results: list[MatchResult] = []
    for source_doc in documents:
        if source_doc.document_type == "purchase_order":
            continue
        available = _candidates_for(source_doc, po_docs)
        for line in source_doc.line_items:
            candidates = [c for c in available if not identity_conflict(line, c.line)]
            picked: MatchResult | None = None
            if "exact" in tiers and (hit := _exact_pick(line, candidates)):
                picked = _result(source_doc, line, candidates, hit[0],
                                 "matched", "exact", hit[1])
            if picked is None and "cleaned" in tiers and \
                    (hit := _cleaned_pick(line, candidates, config)):
                picked = _result(source_doc, line, candidates, hit[0],
                                 "matched", "fuzzy", hit[1])
            if picked is None and "llm" in tiers and llm is not None and candidates:
                picked = await _llm_decide(llm, source_doc, line, candidates, config)
            if picked is None:
                deterministic_only = "llm" not in tiers
                last = "exact" if "cleaned" not in tiers else "fuzzy"
                if deterministic_only:
                    picked = _result(source_doc, line, candidates, None,
                                     "unmatched", last,
                                     "No purchase-order line with the same text under this method")
                else:
                    picked = _result(source_doc, line, candidates, None,
                                     "uncertain", last if llm is None else "llm",
                                     "Not resolvable by string rules; needs human review")
            results.append(picked)
    validate_handoff(list(documents), results)
    return results


def make_match_worker(llm: SupportsLlmMatch | None = None,
                      tiers: Sequence[Tier] = DEFAULT_TIERS,
                      config: MatchingConfig | None = None,
                      documents_key: str = "documents"):
    """Graph adapter for Dhruv (ORCHESTRATION.md): returns an async worker that
    accepts the case state (or a plain document list) and returns a list of
    MatchResult dictionaries. IDs and revisions come from the documents."""

    async def match_worker(state) -> list[dict]:
        raw = state[documents_key] if isinstance(state, Mapping) else state
        documents = [d if isinstance(d, ExtractedDocument)
                     else ExtractedDocument.model_validate(d) for d in raw]
        results = await match_lines(documents, llm=llm, tiers=tiers, config=config)
        return [r.model_dump(mode="json") for r in results]

    return match_worker
