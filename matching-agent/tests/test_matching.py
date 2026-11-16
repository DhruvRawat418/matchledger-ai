"""Matching-agent tests, scored against the ground-truth answer keys.

Conventions under test (CASE_NOTES.md):
  * exact tier matches only "identical" lines and never matches wrongly;
  * cleaned tier additionally recovers "formatting" lines;
  * no deterministic tier may ever select a wrong PO line (lookalike trap);
  * without an LLM client, a full run leaves semantic lines "uncertain"
    (visible for human review), never silently wrong;
  * with an LLM, confident answers become matched/unmatched and invented
    line references are downgraded to "uncertain".
"""
import asyncio

import pytest

from eval.ground_truth import CASES_DIR, load_case, snapshot
from src.contracts import validate_handoff
from src.matching import (LlmError, LlmJudgement, MatchingConfig, clean,
                          make_match_worker, match_lines)

ALL_CASES = sorted(CASES_DIR.glob("case-*.json"))


def run(case, revision=1, **kwargs):
    return asyncio.run(match_lines(snapshot(case, revision), **kwargs))


def answer_key(case, revision=1):
    truth = next(r for r in case.revisions if r.revision == revision)
    return {(m.source.document_id, m.source.line_id):
            (m.decision, (m.selected_po_line.document_id, m.selected_po_line.line_id)
             if m.selected_po_line else None)
            for m in truth.expected_matches}


def tag_map(case):
    return {(t.line.document_id, t.line.line_id): t.match_category
            for t in case.line_tags}


def no_wrong_matches(results, key):
    """A method may miss, but may never select a different line than the truth."""
    for r in results:
        if r.decision == "matched":
            src = (r.source.document_id, r.source.line_id)
            sel = (r.selected_po_line.document_id, r.selected_po_line.line_id)
            expected_decision, expected_sel = key[src]
            assert expected_decision == "matched" and sel == expected_sel, \
                f"wrong match for {src}: picked {sel}, truth {expected_sel}"


# ------------------------------------------------------------- cleaning

@pytest.mark.parametrize("raw,expected", [
    ("STRAWBERRY_BANANA_SB 2LB", "strawberry banana 2lb"),
    ("Strawberry Banana Smoothie Mix, 2 lb", "strawberry banana smoothie mix 2lb"),
    ("a4 copy-paper (500 Sheets) -NEW", "a4 copy paper 500 sheets"),
    ('24" Monitor', "24in monitor"),
    ("Display, 24 in", "display 24in"),
    ("24-inch LED screen", "24in led screen"),
])
def test_cleaning_rules(raw, expected):
    assert clean(raw) == expected


# ---------------------------------------------------- deterministic tiers

@pytest.mark.parametrize("path", ALL_CASES, ids=lambda p: p.stem)
def test_exact_tier_never_matches_wrongly(path):
    case = load_case(path)
    for truth in case.revisions:
        results = run(case, truth.revision, tiers=("exact",))
        no_wrong_matches(results, answer_key(case, truth.revision))


@pytest.mark.parametrize("path", ALL_CASES, ids=lambda p: p.stem)
def test_cleaned_tier_never_matches_wrongly(path):
    case = load_case(path)
    for truth in case.revisions:
        results = run(case, truth.revision, tiers=("exact", "cleaned"))
        no_wrong_matches(results, answer_key(case, truth.revision))


def test_exact_tier_matches_identical_lines_only():
    case101, case102 = load_case(CASES_DIR / "case-101.json"), load_case(CASES_DIR / "case-102.json")
    key = answer_key(case101)
    for r in run(case101, tiers=("exact",)):
        src = (r.source.document_id, r.source.line_id)
        assert r.decision == "matched" and r.method == "exact"
        sel = (r.selected_po_line.document_id, r.selected_po_line.line_id)
        assert sel == key[src][1]
    assert all(r.decision == "unmatched" for r in run(case102, tiers=("exact",))), \
        "the base case must fail on pure formatting noise"


def test_cleaned_tier_recovers_formatting_lines():
    case = load_case(CASES_DIR / "case-102.json")
    key = answer_key(case)
    for r in run(case, tiers=("exact", "cleaned")):
        src = (r.source.document_id, r.source.line_id)
        assert r.decision == "matched", f"{src} should be recovered by cleaning"
        sel = (r.selected_po_line.document_id, r.selected_po_line.line_id)
        assert sel == key[src][1]
        assert r.method == "fuzzy"  # contracts v0.1 name for the cleaned tier


def test_synonyms_fall_through_cleaned_tier():
    case = load_case(CASES_DIR / "case-103.json")
    tags = tag_map(case)
    results = run(case, tiers=("exact", "cleaned"))
    synonym_lines = [r for r in results
                     if tags[(r.source.document_id, r.source.line_id)] == "synonym"]
    assert synonym_lines and all(r.decision == "unmatched" for r in synonym_lines), \
        "string methods must not claim semantic matches"


# ----------------------------------------------------- full run, no LLM

@pytest.mark.parametrize("path", ALL_CASES, ids=lambda p: p.stem)
def test_full_run_without_llm_is_safe_and_reviewable(path):
    case = load_case(path)
    for truth in case.revisions:
        docs = snapshot(case, truth.revision)
        results = asyncio.run(match_lines(docs))
        key = answer_key(case, truth.revision)
        assert {(r.source.document_id, r.source.line_id) for r in results} == set(key), \
            "exactly one result per invoice/receipt line"
        no_wrong_matches(results, key)
        validate_handoff(docs, results)
        for r in results:
            if r.decision != "matched":
                assert r.decision in {"uncertain", "unmatched"}
                assert r.selected_po_line is None
        # Semantic lines must surface for review rather than disappear.
        tags = tag_map(case)
        for r in results:
            if tags[(r.source.document_id, r.source.line_id)] == "synonym":
                assert r.decision == "uncertain"


# ------------------------------------------------------------- LLM tier

class TruthfulLlm:
    """Test double that answers from the answer key with fixed confidence."""

    def __init__(self, key, confidence=0.95):
        self.key, self.confidence = key, confidence
        self.calls = 0

    async def judge(self, source, candidates):
        self.calls += 1
        doc_id, line_id = source.line_key.split("::")
        decision, selected = self.key[(doc_id, line_id)]
        target = None if selected is None else "::".join(selected)
        return LlmJudgement(selected_line_key=target, confidence=self.confidence,
                            reason="test double answer")


class InventingLlm:
    async def judge(self, source, candidates):
        return LlmJudgement("fake-po::99", 0.99, "hallucinated line")


class BrokenLlm:
    async def judge(self, source, candidates):
        raise LlmError("simulated API outage")


@pytest.mark.parametrize("name", ["case-103.json", "case-002.json"])
def test_confident_llm_resolves_semantics_and_not_on_po(name):
    case = load_case(CASES_DIR / name)
    key = answer_key(case)
    llm = TruthfulLlm(key)
    results = run(case, llm=llm)
    assert llm.calls > 0, "the LLM tier must be exercised"
    for r in results:
        decision, selected = key[(r.source.document_id, r.source.line_id)]
        assert r.decision == decision
        got = (None if r.selected_po_line is None
               else (r.selected_po_line.document_id, r.selected_po_line.line_id))
        assert got == selected


def test_low_confidence_llm_goes_to_review():
    case = load_case(CASES_DIR / "case-103.json")
    results = run(case, llm=TruthfulLlm(answer_key(case), confidence=0.5))
    assert any(r.decision == "uncertain" and r.method == "llm" for r in results)
    assert not any(r.decision == "matched" and r.method == "llm" for r in results)


def test_invented_line_ids_are_rejected():
    case = load_case(CASES_DIR / "case-103.json")
    tags = tag_map(case)
    for r in run(case, llm=InventingLlm()):
        if tags[(r.source.document_id, r.source.line_id)] == "synonym":
            assert r.decision == "uncertain" and r.selected_po_line is None


def test_llm_failure_degrades_to_uncertain():
    case = load_case(CASES_DIR / "case-001.json")
    results = run(case, llm=BrokenLlm())
    assert all(r.decision in {"matched", "uncertain"} for r in results)
    assert any("human review" in r.explanation for r in results)


# ------------------------------------------------------------- adapter

def test_graph_worker_round_trips_dicts():
    case = load_case(CASES_DIR / "case-101.json")
    docs = [d.model_dump(mode="json") for d in snapshot(case, 1)]
    worker = make_match_worker(tiers=("exact",))
    payload = asyncio.run(worker({"documents": docs}))
    assert isinstance(payload, list) and payload
    assert all(isinstance(item, dict) and item["decision"] == "matched"
               for item in payload)


def test_human_review_rate_is_measurable():
    case = load_case(CASES_DIR / "case-103.json")
    results = run(case)  # no LLM: semantic lines must be flagged
    rate = sum(r.decision == "uncertain" for r in results) / len(results)
    assert 0 < rate <= 1
