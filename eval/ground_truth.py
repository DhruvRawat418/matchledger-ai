"""Ground-truth (answer key) format for evaluation cases — v0.1 draft.

AI matching and evaluation. 

A ground-truth case wraps the team contracts in src/contracts.py; it does not
invent parallel shapes. Every document is a valid ExtractedDocument, every
expected match is a valid MatchResult, and every expected outcome is a valid
ReconciliationResult. This file adds only what evaluation needs on top:

* revisions  - the sequence of uploads (e.g. a second receipt, a corrected invoice)
               and the correct answer after each one;
* line_tags  - the matching difficulty of each invoice/receipt line, so accuracy
               can be reported per category (identical / formatting / synonym ...).

Run `python -m eval.ground_truth` to validate every case in eval/cases/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field

from src.contracts import (
    Contract,
    ExtractedDocument,
    Identifier,
    LineReference,
    MatchResult,
    PositiveInt,
    ReconciliationResult,
    validate_handoff,
)

CASES_DIR = Path(__file__).parent / "cases"

CaseCategory = Literal[
    "identical",         # descriptions/SKUs agree exactly
    "formatting",        # same product, differs only in case/punctuation/suffix/units
    "synonym",           # same product, different words ("display" vs "monitor")
    "lookalike",         # a similar-looking PO line exists that must NOT be chosen
    "not_on_po",         # billed/received item that was never ordered
    "partial_delivery",  # quantity arrives across several receipts
    "revised_document",  # a document is replaced by a corrected version
    "price_error",
    "quantity_error",
    "duplicate_document",  # human error: the same document uploaded twice
]

MatchCategory = Literal["identical", "formatting", "synonym", "lookalike", "not_on_po"]

# The dividing line between levels: if fixed cleaning rules make the two
# descriptions (nearly) identical strings, the line is "formatting" and the
# cleaned/fuzzy baseline should match it. If pairing them requires knowing
# that different words name the same product ("LED screen" = "display" =
# "monitor"), the line is "synonym" and only the LLM is expected to match it.
# Used by the scorer to check each method against its predicted behaviour.
EXPECTED_BY_CATEGORY: dict[str, frozenset[str]] = {
    "identical":  frozenset({"exact", "cleaned", "llm"}),
    "formatting": frozenset({"cleaned", "llm"}),
    "synonym":    frozenset({"llm"}),
    "lookalike":  frozenset({"llm"}),  # exact/cleaned usually fail or pick the wrong line
    "not_on_po":  frozenset(),         # every method must leave these unmatched
}


class LineTag(Contract):
    line: LineReference              # an invoice or receipt line
    match_category: MatchCategory


class RevisionTruth(Contract):
    revision: PositiveInt
    event: Identifier                # what changed, e.g. "second receipt uploaded"
    document_ids: list[Identifier] = Field(min_length=1)  # documents current at this revision
    expected_matches: list[MatchResult]
    expected_reconciliation: ReconciliationResult


class GroundTruthCase(Contract):
    ground_truth_version: Literal["0.1"] = "0.1"
    case_id: Identifier
    categories: list[CaseCategory] = Field(min_length=1)
    description: Identifier
    # Each document is stored once, stamped with the revision it first appears in.
    documents: list[ExtractedDocument] = Field(min_length=1)
    line_tags: list[LineTag]
    revisions: list[RevisionTruth] = Field(min_length=1)


# ---------------------------------------------------------------- helpers

def load_case(path: Path) -> GroundTruthCase:
    return GroundTruthCase.model_validate(json.loads(path.read_text()))


def snapshot(case: GroundTruthCase, revision: int) -> list[ExtractedDocument]:
    """The correct extraction output for every document current at `revision`.

    Per the contracts, `revision` is the case input revision, so a document
    uploaded in revision 1 is re-stamped with revision 2 when the case advances.
    """
    rev = _revision(case, revision)
    by_id = {d.document_id: d for d in case.documents}
    return [by_id[i].model_copy(update={"revision": revision}) for i in rev.document_ids]


def _revision(case: GroundTruthCase, revision: int) -> RevisionTruth:
    for r in case.revisions:
        if r.revision == revision:
            return r
    raise KeyError(f"{case.case_id}: no revision {revision}")


def _ref(r: LineReference) -> tuple[str, str]:
    return (r.document_id, r.line_id)


# ------------------------------------------------------------- validation

def check_case(case: GroundTruthCase) -> None:
    """Raise ValueError if the answer key is internally inconsistent.

    Schema validity comes from the contracts; these checks make sure the
    answer key is complete enough to score against.
    """
    cid = case.case_id
    docs = {d.document_id: d for d in case.documents}
    if len(docs) != len(case.documents):
        raise ValueError(f"{cid}: duplicate document_id")
    for d in case.documents:
        if d.case_id != cid:
            raise ValueError(f"{cid}: document {d.document_id} has case_id {d.case_id}")

    numbers = [r.revision for r in case.revisions]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"{cid}: revisions must be numbered 1, 2, 3 ... in order")

    first_seen: dict[str, int] = {}
    for r in case.revisions:
        for doc_id in r.document_ids:
            if doc_id not in docs:
                raise ValueError(f"{cid} rev {r.revision}: unknown document {doc_id}")
            first_seen.setdefault(doc_id, r.revision)
    for doc_id, d in docs.items():
        if doc_id not in first_seen:
            raise ValueError(f"{cid}: document {doc_id} is never used")
        if d.revision != first_seen[doc_id]:
            raise ValueError(f"{cid}: {doc_id} must carry revision {first_seen[doc_id]} (first appearance)")
        if d.document_type != "purchase_order" and d.purchase_order_id not in docs:
            raise ValueError(f"{cid}: {doc_id} must link to a PO document via purchase_order_id")

    all_lines = {(d.document_id, l.line_id) for d in case.documents for l in d.line_items}
    tagged = {_ref(t.line) for t in case.line_tags}
    for t in case.line_tags:
        if _ref(t.line) not in all_lines or docs[t.line.document_id].document_type == "purchase_order":
            raise ValueError(f"{cid}: line tag {_ref(t.line)} must point at an invoice/receipt line")

    for r in case.revisions:
        snap = snapshot(case, r.revision)
        where = f"{cid} rev {r.revision}"

        # Interface checks shared with the live pipeline.
        validate_handoff(snap, r.expected_matches)
        rec = r.expected_reconciliation
        if (rec.case_id, rec.revision) != (cid, r.revision):
            raise ValueError(f"{where}: reconciliation has wrong case/revision")

        lines = {(d.document_id, l.line_id) for d in snap for l in d.line_items}
        sources = {(d.document_id, l.line_id) for d in snap if d.document_type != "purchase_order"
                   for l in d.line_items}

        # Completeness: exactly one correct answer for every invoice/receipt line.
        answered = {_ref(m.source) for m in r.expected_matches}
        if answered != sources:
            raise ValueError(f"{where}: missing match answers for {sorted(sources - answered)}")
        if not sources <= tagged:
            raise ValueError(f"{where}: untagged lines {sorted(sources - tagged)}")

        for m in r.expected_matches:
            if m.method != "human":
                raise ValueError(f"{where}: answer-key matches must use method 'human'")
            src_doc = docs[m.source.document_id]
            if m.selected_po_line and m.selected_po_line.document_id != src_doc.purchase_order_id:
                raise ValueError(f"{where}: {_ref(m.source)} matched outside its linked PO")

        refs = [_ref(x) for x in rec.checked_lines]
        refs += [_ref(x) for disc in rec.discrepancies for x in disc.lines]
        stray = set(refs) - lines
        if stray:
            raise ValueError(f"{where}: reconciliation refers to lines not in this revision: {sorted(stray)}")
        if rec.status == "clean" and set(_ref(x) for x in rec.checked_lines) != lines:
            raise ValueError(f"{where}: a clean result must check every current line")


def main() -> int:
    paths = sorted(CASES_DIR.glob("case-*.json"))
    failed = 0
    for p in paths:
        try:
            check_case(load_case(p))
            print(f"OK    {p.name}")
        except Exception as exc:  # report every case, not just the first failure
            failed += 1
            print(f"FAIL  {p.name}: {exc}")
    print(f"{len(paths) - failed}/{len(paths)} cases valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
