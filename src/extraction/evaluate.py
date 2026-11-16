"""Run extraction over answer-key cases and report accuracy, reliability, latency and
token use. Used for model selection (development cases) and the final held-out run.

    python -m src.extraction.evaluate --cases eval/cases --files eval/files \
        --suppliers eval/suppliers.json --layouts eval/layouts.csv

Expected files: <files>/<case_id>/<document_id>.pdf (or .png/.jpg), one per answer-key
document. Provider and model come from .env (see .env.example), so comparing models
means running this once per model; each run writes to its own folder.
"""
import argparse
import asyncio
import csv
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from src.contracts import ExtractedDocument
from src.extraction.extractor import extract_document
from src.extraction.prompt import active_prompt
from src.extraction.schema import ExtractionRequest
from src.extraction.scoring import score_document

EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")


def find_file(files_dir: Path, case_id: str, document_id: str) -> Path | None:
    for ext in EXTENSIONS:
        path = files_dir / case_id / f"{document_id}{ext}"
        if path.exists():
            return path
    return None


def load_layouts(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["document_id"]: row["layout"] for row in csv.DictReader(handle)}


async def evaluate(client, cases: list[dict], files_dir: Path, suppliers: dict, layouts: dict,
                   declare_type: bool = False, repeat: int = 1) -> tuple[list[dict], int]:
    rows, missing = [], 0
    for case in cases:
        for raw in case["documents"]:
            expected = ExtractedDocument.model_validate(raw)
            path = find_file(files_dir, case["case_id"], expected.document_id)
            if path is None:
                missing += 1
                continue
            for run in range(1, repeat + 1):
                request = ExtractionRequest(
                    case_id=expected.case_id, revision=expected.revision, document_id=expected.document_id,
                    file_path=path, supplier_directory=suppliers,
                    declared_type=expected.document_type if declare_type else None)
                outcome = await extract_document(request, client)
                score = score_document(outcome.document, expected)
                rows.append({"case_id": case["case_id"], "document_id": expected.document_id, "run": run,
                             "document_type": expected.document_type,
                             "layout": layouts.get(expected.document_id, "unknown"),
                             "categories": case.get("categories", []),
                             "outcome": json.loads(outcome.model_dump_json()), "score": score})
    return rows, missing


def _ratio(correct: Counter, total: Counter) -> float | None:
    n = sum(total.values())
    return round(sum(correct.values()) / n, 4) if n else None


def summarize(rows: list[dict], missing: int, model_id: str, price_in: float | None,
              price_out: float | None) -> dict:
    correct, total = Counter(), Counter()
    groups = {"document_type": defaultdict(lambda: [Counter(), Counter()]),
              "layout": defaultdict(lambda: [Counter(), Counter()])}
    for row in rows:
        correct.update(row["score"]["correct"])
        total.update(row["score"]["total"])
        for key, bucket in groups.items():
            bucket[row[key]][0].update(row["score"]["correct"])
            bucket[row[key]][1].update(row["score"]["total"])
    outcomes = [row["outcome"] for row in rows]
    latencies = sorted(o["latency_ms"] for o in outcomes)
    tokens_in = sum(o["usage"].get("input_tokens", 0) for o in outcomes)
    tokens_out = sum(o["usage"].get("output_tokens", 0) for o in outcomes)
    issues = Counter(i["code"] for o in outcomes for i in o["issues"])
    n = len(outcomes)
    summary = {
        "model_id": model_id,
        "prompt_version": outcomes[0]["prompt_version"] if outcomes else active_prompt()[0],
        "documents_scored": n,
        "documents_missing_files": missing,
        "accuracy_overall": _ratio(correct, total),
        "accuracy_by_field": {f: round(correct[f] / total[f], 4) for f in sorted(total)},
        **{f"accuracy_by_{key}": {name: _ratio(c, t) for name, (c, t) in sorted(bucket.items())}
           for key, bucket in groups.items()},
        "valid_json_first_attempt_rate": round(sum(o["attempts"] == 1 and o["document"] is not None
                                                   for o in outcomes) / n, 4) if n else None,
        "extraction_failure_rate": round(sum(o["document"] is None for o in outcomes) / n, 4) if n else None,
        "needs_review_rate": round(sum(o["document"] is None or any(i["severity"] == "review" for i in o["issues"])
                                       for o in outcomes) / n, 4) if n else None,
        "issue_counts": dict(issues.most_common()),
        "latency_ms_median": round(statistics.median(latencies), 1) if latencies else None,
        "latency_ms_p95": latencies[max(0, round(0.95 * len(latencies)) - 1)] if latencies else None,
        "tokens_input": tokens_in, "tokens_output": tokens_out,
    }
    if price_in is not None and price_out is not None:
        summary["estimated_cost_usd"] = round(tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out, 4)
    return summary


def write_outputs(out_dir: Path, rows: list[dict], summary: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(out_dir / "results.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    with open(out_dir / "field_errors.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, ["case_id", "document_id", "run", "document_type", "layout",
                                         "line_id", "field", "expected", "got"])
        writer.writeheader()
        for row in rows:
            for error in row["score"]["errors"]:
                writer.writerow({"case_id": row["case_id"], "run": row["run"],
                                 "document_type": row["document_type"], "layout": row["layout"], **error})


def main(argv=None):
    from src.extraction.providers import client_from_env, load_dotenv

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, required=True, help="Folder of answer-key case-*.json files")
    parser.add_argument("--files", type=Path, required=True, help="Folder of rendered documents")
    parser.add_argument("--only", nargs="*", help="Case IDs to include (e.g. the development split)")
    parser.add_argument("--suppliers", type=Path, help="JSON: printed supplier name -> supplier_id")
    parser.add_argument("--layouts", type=Path, help="CSV with columns document_id,layout")
    parser.add_argument("--declare-type", action="store_true", help="Pass the true document type as if chosen at upload")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per document, to measure variability")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens, for a cost estimate")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens")
    parser.add_argument("--out", type=Path, help="Output folder (default outputs/extraction/<model>-<time>)")
    args = parser.parse_args(argv)

    load_dotenv()
    client = client_from_env()
    cases = [json.loads(p.read_text()) for p in sorted(args.cases.glob("case-*.json"))]
    if args.only:
        cases = [c for c in cases if c["case_id"] in set(args.only)]
    suppliers = json.loads(args.suppliers.read_text()) if args.suppliers else {}
    rows, missing = asyncio.run(evaluate(client, cases, args.files, suppliers, load_layouts(args.layouts),
                                         args.declare_type, args.repeat))
    summary = summarize(rows, missing, client.model_id, args.price_in, args.price_out)
    out = args.out or Path("outputs/extraction") / (f"{client.model_id.replace('/', '_')}-{summary['prompt_version']}"
                                                    f"-{time.strftime('%Y%m%d-%H%M%S')}")
    write_outputs(out, rows, summary)
    print(json.dumps(summary, indent=2))
    print(f"\nWrote {out}/summary.json, results.jsonl, field_errors.csv")


if __name__ == "__main__":
    main()
