"""LangGraph orchestration only: workers are injected, never silently mocked."""
from copy import deepcopy
from dataclasses import dataclass
from typing import Awaitable, Callable
from langgraph.graph import END, START, StateGraph
from src.contracts import ExtractedDocument, MatchResult, ReconciliationResult, validate_handoff
from src.state import ReviewResult, WorkflowRequest, WorkflowState

Worker = Callable[[WorkflowState], Awaitable[object]]

@dataclass(frozen=True)
class Workers:
    extract: Worker
    match: Worker
    reconcile: Worker
    review: Worker


def initial_state(case_id: str, revision: int = 1) -> WorkflowState:
    request = WorkflowRequest(case_id=case_id, revision=revision)
    return {**request.model_dump(), "status": "running", "documents": [], "matches": [],
            "reconciliation": None, "review": None, "issues": [], "stages": [], "corrections": []}


def _same_revision(output, state):
    if (output.case_id, output.revision) != (state["case_id"], state["revision"]):
        raise ValueError("Worker output belongs to a different case or revision")


def build_graph(workers: Workers):
    """Compile a bounded, one-pass graph. Invoke asynchronously with fresh input.

    needs_correction ends this run; case_service persists and reruns revisions.
    Workers accept isolated state copies and return contract-shaped data.
    """
    graph = StateGraph(WorkflowState)

    def initialize(state):
        # Never let a caller inject old approvals/results into a new run.
        return {**initial_state(state["case_id"], state.get("revision", 1)),
                "corrections": deepcopy(state.get("corrections", []))}

    def node(stage):
        async def run(state):
            stages = [*state["stages"], stage]
            try:
                raw = await getattr(workers, stage)(deepcopy(state))
                if stage == "extract":
                    docs = [ExtractedDocument.model_validate(x) for x in raw]
                    validate_handoff(docs, [])
                    for doc in docs:
                        _same_revision(doc, state)
                    kinds = [d.document_type for d in docs]
                    if kinds.count("purchase_order") != 1 or kinds.count("invoice") != 1 or not 1 <= kinds.count("receipt") <= 3:
                        raise ValueError("Expected one PO, one invoice and one to three receipts")
                    update = {"documents": [d.model_dump() for d in docs]}
                elif stage == "match":
                    docs = [ExtractedDocument.model_validate(x) for x in state["documents"]]
                    matches = [MatchResult.model_validate(x) for x in raw]
                    validate_handoff(docs, matches)
                    expected = {(d.document_id, line.line_id) for d in docs if d.document_type != "purchase_order" for line in d.line_items}
                    actual = {(m.source.document_id, m.source.line_id) for m in matches}
                    if expected != actual:
                        raise ValueError("Every invoice/receipt line needs one explicit match decision")
                    update = {"matches": [m.model_dump() for m in matches]}
                    if any(m.decision != "matched" for m in matches):
                        update.update(status="needs_correction", issues=[{"code": "unresolved_match", "owner": "match"}])
                elif stage == "reconcile":
                    result = ReconciliationResult.model_validate(raw)
                    _same_revision(result, state)
                    valid = {(d["document_id"], line["line_id"]) for d in state["documents"] for line in d["line_items"]}
                    refs = [*result.checked_lines, *(ref for issue in result.discrepancies for ref in issue.lines)]
                    if any((r.document_id, r.line_id) not in valid for r in refs):
                        raise ValueError("Reconciliation references a nonexistent line")
                    required = {(m["source"]["document_id"], m["source"]["line_id"]) for m in state["matches"]}
                    checked = {(r.document_id, r.line_id) for r in result.checked_lines}
                    if result.status == "clean" and not required <= checked:
                        raise ValueError("Clean result must cover all matched source lines")
                    update = {"reconciliation": result.model_dump(), "issues": [
                        {"code": d.code, "owner": "reconcile"} for d in result.discrepancies]}
                    if result.status == "blocked":
                        update["status"] = "blocked"
                else:
                    result = ReviewResult.model_validate(raw)
                    _same_revision(result, state)
                    update = {"review": result.model_dump(), "issues": [*state["issues"],
                        *[{"code": f.code, "owner": f.owner} for f in result.findings]]}
                return {**update, "stages": stages}
            except Exception as exc:
                # Avoid leaking provider messages, secrets or document text to UI.
                return {"status": "blocked", "stages": stages, "issues": [*state["issues"],
                    {"code": "worker_failed_or_invalid_output", "owner": stage, "error_type": type(exc).__name__}]}
        return run

    def next_step(state):
        return "stop" if state["status"] != "running" else "continue"

    def finish(state):
        reconciliation = state["reconciliation"]
        status = "needs_correction" if state["issues"] or reconciliation["status"] != "clean" else "ready_for_human_review"
        return {"status": status, "stages": [*state["stages"], "finish"]}

    graph.add_node("initialize", initialize)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "extract")
    for stage, following in [("extract", "match"), ("match", "reconcile"), ("reconcile", "review"), ("review", "finish")]:
        graph.add_node(stage, node(stage))
        graph.add_conditional_edges(stage, next_step, {"stop": END, "continue": following})
    graph.add_node("finish", finish)
    graph.add_edge("finish", END)
    return graph.compile()
