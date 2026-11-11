"""Shared graph state; runtime validation happens at worker boundaries."""
from typing import Literal, TypedDict
from src.contracts import Contract, Identifier, PositiveInt

class WorkflowRequest(Contract):
    case_id: Identifier
    revision: PositiveInt = 1

class ReviewFinding(Contract):
    code: Identifier
    owner: Literal["extract", "match", "reconcile", "human"]
    explanation: Identifier

class ReviewResult(Contract):
    case_id: Identifier
    revision: PositiveInt
    findings: list[ReviewFinding]

class WorkflowState(TypedDict, total=False):
    case_id: str
    revision: int
    status: Literal["running", "ready_for_human_review", "needs_correction", "blocked"]
    documents: list[dict]
    matches: list[dict]
    reconciliation: dict | None
    review: dict | None
    issues: list[dict]
    stages: list[str]
    corrections: list[dict]
