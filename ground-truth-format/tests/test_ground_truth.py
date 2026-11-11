import json

import pytest
from pydantic import ValidationError

from eval.ground_truth import CASES_DIR, GroundTruthCase, check_case, load_case, snapshot

CASE_FILES = sorted(CASES_DIR.glob("case-*.json"))


@pytest.mark.parametrize("path", CASE_FILES, ids=lambda p: p.name)
def test_every_case_is_valid(path):
    check_case(load_case(path))


def test_snapshot_restamps_revision():
    case = load_case(CASES_DIR / "case-001.json")
    docs = snapshot(case, 2)
    assert {d.revision for d in docs} == {2}
    assert len(docs) == 4


def _raw(name="case-001.json"):
    return json.loads((CASES_DIR / name).read_text())


def _broken(mutate, name="case-001.json"):
    raw = _raw(name)
    mutate(raw)
    case = GroundTruthCase.model_validate(raw)
    with pytest.raises(ValueError):
        check_case(case)


def test_missing_answer_for_a_line_is_rejected():
    _broken(lambda r: r["revisions"][0]["expected_matches"].pop())


def test_wrong_revision_stamp_is_rejected():
    def mutate(r):
        r["documents"][3]["revision"] = 1  # second receipt first appears in revision 2
    _broken(mutate)


def test_untagged_line_is_rejected():
    _broken(lambda r: r["line_tags"].pop())


def test_clean_result_must_cover_all_lines():
    _broken(lambda r: r["revisions"][1]["expected_reconciliation"]["checked_lines"].pop())


def test_match_outside_linked_po_is_rejected():
    def mutate(r):
        r["documents"][1]["purchase_order_id"] = "case-001-rcv-0001"
    _broken(mutate)


def test_contract_rules_still_apply():
    raw = _raw()
    raw["documents"][0]["line_items"][0]["quantity"] = 20  # numbers must be decimal strings
    with pytest.raises(ValidationError):
        GroundTruthCase.model_validate(raw)
