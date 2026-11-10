import pytest
from pydantic import ValidationError
from src.contracts import ExtractedDocument, MatchResult, ReconciliationResult, validate_handoff
from demo.contracts_example import sample_documents, sample_matches


def test_valid_handoff_and_missing_receipt_price():
    docs = sample_documents()
    assert docs[-1].line_items[0].unit_price is None
    validate_handoff(docs, sample_matches())


@pytest.mark.parametrize('value', [125.50, '-1', 'NaN', '1e3', '1,000'])
def test_invalid_money(value):
    payload = sample_documents()[0].model_dump()
    payload['line_items'][0]['unit_price'] = value
    with pytest.raises(ValidationError):
        ExtractedDocument.model_validate(payload)


def test_typo_does_not_silently_disappear():
    payload = sample_documents()[0].model_dump()
    payload['line_items'][0]['qty'] = '20'
    with pytest.raises(ValidationError):
        ExtractedDocument.model_validate(payload)


def test_duplicate_line_ids():
    payload = sample_documents()[0].model_dump()
    payload['line_items'] *= 2
    with pytest.raises(ValidationError):
        ExtractedDocument.model_validate(payload)


def test_match_must_select_supplied_candidate():
    payload = sample_matches()[0].model_dump()
    payload['selected_po_line']['line_id'] = 'invented'
    with pytest.raises(ValidationError):
        MatchResult.model_validate(payload)


def test_uncertain_cannot_select_accepted_line():
    payload = sample_matches()[0].model_dump()
    payload['decision'] = 'uncertain'
    with pytest.raises(ValidationError):
        MatchResult.model_validate(payload)


@pytest.mark.parametrize('change', ['stale', 'cross_case', 'invented_candidate', 'duplicate_decision'])
def test_bad_handoffs(change):
    matches = sample_matches()
    if change == 'stale':
        matches[0].revision = 2
    elif change == 'cross_case':
        matches[0].case_id = 'another-case'
    elif change == 'invented_candidate':
        # Consistent within its message, but refers to a nonexistent document.
        matches[0].candidate_po_lines[0].document_id = 'fake-po'
        matches[0].selected_po_line.document_id = 'fake-po'
    else:
        matches.append(matches[0])
    with pytest.raises(ValueError):
        validate_handoff(sample_documents(), matches)


def test_cannot_claim_clean_without_any_checks():
    with pytest.raises(ValidationError):
        ReconciliationResult(case_id='sample-001', revision=1, status='clean', discrepancies=[], checked_lines=[])
