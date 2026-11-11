"""Synthetic extraction/matching/review with Matthew’s real reconciliation."""
from src.reconciliation_adapter import reconcile_worker
from demo.contracts_example import sample_documents, sample_matches
from src.graph import Workers


def make_sample_workers(scenario: str) -> Workers:
    if scenario not in {"clean", "short-delivery"}:
        raise ValueError("Unknown sample scenario")

    async def extract(state):
        docs = [d.model_dump() for d in sample_documents()]
        for doc in docs:
            doc.update(case_id=state["case_id"], revision=state["revision"])
        if scenario == "clean":
            docs[-1]["line_items"][0]["quantity"] = "20"
            docs[-1]["line_items"][0]["evidence"][0]["text"] = "MON-24 | 24-inch monitor | 20 each"
        return docs

    async def match(state):
        matches = [m.model_dump() for m in sample_matches()]
        for result in matches:
            result.update(case_id=state["case_id"], revision=state["revision"])
        return matches

    async def review(state):
        return {"case_id": state["case_id"], "revision": state["revision"], "findings": []}

    return Workers(extract=extract, match=match, reconcile=reconcile_worker, review=review)
