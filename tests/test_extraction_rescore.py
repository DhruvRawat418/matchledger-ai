import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.extraction.rescore import rescore

ROOT = Path(__file__).resolve().parents[1]


def test_answer_key_changes_are_exactly_the_audited_units():
    folder = ROOT / 'eval/extraction_cases'
    audit = json.loads((folder / 'label_corrections.json').read_text())
    changes = {(c['document_id'], c['line_id']): c for c in audit['changes']}
    seen = set()
    for path in folder.glob('case-*.json'):
        original = json.loads((ROOT / 'eval/cases' / path.name).read_text())
        corrected = json.loads(path.read_text())
        expected = deepcopy(original['documents'])
        for doc in expected:
            for line in doc['line_items']:
                key = (doc['document_id'], line['line_id'])
                if key in changes:
                    assert line['unit'] == changes[key]['old']
                    line['unit'] = changes[key]['new']
                    seen.add(key)
        assert corrected['documents'] == expected
    assert seen == set(changes)
    assert len(seen) == 12


def test_rescore_preserves_replies_and_refuses_overwrite(tmp_path):
    case = json.loads((ROOT / 'eval/extraction_cases/case-101.json').read_text())
    doc = deepcopy(case['documents'][0])
    doc['line_items'][0]['sku'] = 'INCORRECT-SKU'
    outcome = {'document': doc, 'model_id': 'offline-test', 'prompt_version': 'extract-v3',
               'latency_ms': 10, 'usage': {'input_tokens': 100, 'output_tokens': 10},
               'attempts': 1, 'needs_review': False, 'issues': []}
    row = {'case_id': case['case_id'], 'document_id': doc['document_id'],
           'document_type': doc['document_type'], 'layout': 'test', 'run': 1,
           'outcome': outcome, 'score': {}}
    source = tmp_path / 'results.jsonl'
    source.write_text(json.dumps(row) + '\n')
    before = source.read_bytes()
    output = tmp_path / 'rescored'
    summary = rescore(source, ROOT / 'eval/extraction_cases', output)
    saved = json.loads((output / 'results.jsonl').read_text())
    assert saved['outcome'] == outcome
    assert summary['new_api_calls'] == 0
    assert summary['silent_error_documents'] == 1
    assert summary['accuracy_by_field']['sku'] < 1
    assert source.read_bytes() == before
    assert summary['documents_missing_files'] is None
    with pytest.raises(ValueError, match='fresh output'):
        rescore(source, ROOT / 'eval/extraction_cases', output)
