"""Run real LangGraph routing with fake workers; no API key required."""
import argparse
import asyncio
import json
from src.graph import build_graph, initial_state
from demo.sample_workers import make_sample_workers

async def run(scenario):
    graph = build_graph(make_sample_workers(scenario))
    state = await graph.ainvoke(initial_state(f"demo-{scenario}"))
    return {"mode": "synthetic_workers", "case_id": state["case_id"], "status": state["status"],
            "issues": state["issues"], "stages": state["stages"]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["clean", "short-delivery"], default="clean")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.scenario)), indent=2))
