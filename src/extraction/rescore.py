"""Rescore saved extraction replies against revised, source-checked answer keys.

No provider client is loaded and no model calls are made. Original results remain
unchanged. Use a fresh output directory to preserve the evaluation audit trail.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from src.contracts import ExtractedDocument
from src.extraction.scoring import score_document
from src.extraction.evaluate import summarize, write_outputs


def rescore(results, cases, output):
    if output.exists():
        raise ValueError('Use a fresh output directory; do not overwrite earlier measurements')
    expected = {}
    key_hashes = {}
    for path in sorted(cases.glob('case-*.json')):
        raw = path.read_bytes()
        case = json.loads(raw)
        key_hashes[path.name] = hashlib.sha256(raw).hexdigest()
        for doc in case['documents']:
            key = (case['case_id'],doc['document_id'])
            if key in expected:raise ValueError('Duplicate expected document')
            expected[key] = ExtractedDocument.model_validate(doc)
    rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    if not rows:raise ValueError('No saved extraction responses')
    models = {r['outcome']['model_id'] for r in rows}
    prompts = {r['outcome']['prompt_version'] for r in rows}
    if len(models) != 1 or len(prompts) != 1:raise ValueError('Do not pool different models or prompts')
    updated = deepcopy(rows)
    for row in updated:
        want = expected[(row['case_id'],row['document_id'])]
        raw = row['outcome']['document']
        prediction = ExtractedDocument.model_validate(raw) if raw is not None else None
        row['score'] = score_document(prediction,want)
    source_summary_path = results.parent / 'summary.json'
    source_summary = json.loads(source_summary_path.read_text()) if source_summary_path.exists() else {}
    summary = summarize(updated, source_summary.get('documents_missing_files'), next(iter(models)), None, None)
    summary.update(rescored=True,new_api_calls=0,source_results=str(results),
                   source_results_sha256=hashlib.sha256(results.read_bytes()).hexdigest(),
                   answer_key_sha256=key_hashes,
                   evaluation_note='Development-set rescore of saved replies; not a new or held-out run. Missing units remain review issues.')
    write_outputs(output,updated,summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    result = rescore(args.results,args.cases,args.out)
    print(json.dumps({k:result[k] for k in ('documents_scored','accuracy_overall','accuracy_by_field','silent_error_documents','needs_review_rate','new_api_calls')},indent=2))
