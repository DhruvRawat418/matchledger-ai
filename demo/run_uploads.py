"""Run uploaded PDFs/images using live extraction and optional live semantic matching.

The manifest supplies file paths, IDs, document types and a supplier directory;
it must not contain API keys. Paths are relative to the manifest directory.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from src.extraction.providers import client_from_env, load_dotenv
from src.extraction.schema import ExtractionRequest
from src.graph import build_graph, initial_state
from src.integrated_workers import make_upload_workers
from src.llm_matcher import AnthropicMatcher


async def run(manifest):
    return await run_data(json.loads(manifest.read_text()), manifest.parent)


async def run_data(data, base_dir=Path(".")):
    load_dotenv()
    requests = [ExtractionRequest(case_id=data['case_id'], revision=data.get('revision', 1),
                document_id=d['document_id'], file_path=base_dir / d['file_path'],
                declared_type=d['document_type'], supplier_directory=data['supplier_directory'])
                for d in data['documents']]
    matcher = AnthropicMatcher(model=os.environ['MATCHLEDGER_MATCH_MODEL']) if os.getenv('MATCHLEDGER_MATCH_MODEL') else None
    graph = build_graph(make_upload_workers(requests, client_from_env(), matcher))
    state = await graph.ainvoke(initial_state(data['case_id'], data.get('revision', 1)))
    return {'mode': 'live_extraction', 'semantic_matching': matcher is not None,
            'review_mode': 'deterministic_boundary_audit', **state}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    state = asyncio.run(run(args.manifest))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(state, indent=2))
    print(json.dumps({k: state[k] for k in ('mode', 'review_mode', 'status', 'issues', 'stages')}, indent=2))


if __name__ == '__main__':
    main()
