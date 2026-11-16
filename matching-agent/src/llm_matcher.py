"""Anthropic backend for the matching agent's LLM tier.

Separate from src/matching.py so the agent and its tests never require an API
key or network access. Any backend implementing SupportsLlmMatch works; this
is the production one.

Rules (DATA_CONTRACTS.md): provider concerns stay inside the worker, the model
is never allowed to invent workflow IDs (it may only pick from the supplied
candidate keys), and failures raise LlmError, which the agent maps to an
"uncertain" decision for human review instead of a fake answer.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from src.matching import LlmError, LlmJudgement, LlmLineView

SYSTEM_PROMPT = """You decide whether a line item from a supplier invoice or delivery record \
refers to the same real-world product as one of the listed purchase-order lines.

Rules:
- Different wording can mean the same product (a "24-inch LED screen" can be a "24in monitor").
- Size, model, grade or count differences mean DIFFERENT products ("24in" is not "27in").
- Judge product identity only. Do not reason about prices or billed quantities.
- If the item is not on the purchase order (e.g. a shipping fee), candidate is null.
- Confidence is your probability that your answer is correct, between 0 and 1.
- Answer with ONLY a JSON object: {"candidate": "<key from the list or null>", "confidence": 0.0, "reason": "<one short sentence>"}"""


def build_user_prompt(source: LlmLineView, candidates: Sequence[LlmLineView]) -> str:
    def show(v: LlmLineView) -> dict:
        return {"key": v.line_key, "description": v.description,
                "sku": v.sku, "unit": v.unit}

    return json.dumps({"line_to_match": show(source),
                       "purchase_order_candidates": [show(c) for c in candidates]},
                      indent=2)


def parse_judgement(text: str, allowed_keys: frozenset[str]) -> LlmJudgement:
    """Strictly parse the model's JSON. Unknown keys for 'candidate' are kept
    (the agent downgrades them to 'uncertain'), everything malformed raises."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LlmError("no JSON object in model output")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LlmError(f"invalid JSON: {exc}") from exc
    candidate = data.get("candidate")
    if candidate is not None and not isinstance(candidate, str):
        raise LlmError("candidate must be a string key or null")
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise LlmError("confidence missing or not a number") from exc
    confidence = min(1.0, max(0.0, confidence))
    reason = str(data.get("reason") or "").strip() or "no reason given"
    # Keys are validated again by the agent against the supplied candidates;
    # this early check just normalises obvious echoes like quoted keys.
    if candidate is not None and candidate not in allowed_keys:
        candidate = candidate.strip().strip('"')
    return LlmJudgement(selected_line_key=candidate, confidence=confidence, reason=reason)


class AnthropicMatcher:
    """SupportsLlmMatch backed by the Anthropic API. temperature=0, fixed
    prompt; set the model once for the whole evaluation and do not change it
    between runs that will be compared."""

    def __init__(self, model: str = "claude-sonnet-4-5", *, api_key: str | None = None,
                 max_tokens: int = 300):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LlmError("anthropic package is not installed "
                           "(pip install anthropic)") from exc
        self._client = anthropic.AsyncAnthropic(api_key=api_key) if api_key \
            else anthropic.AsyncAnthropic()
        self.model = model
        self.max_tokens = max_tokens

    async def judge(self, source: LlmLineView,
                    candidates: Sequence[LlmLineView]) -> LlmJudgement:
        allowed = frozenset(c.line_key for c in candidates)
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user",
                           "content": build_user_prompt(source, candidates)}],
            )
        except Exception as exc:  # transport/API errors -> uncertain upstream
            raise LlmError(f"Anthropic API call failed: {exc}") from exc
        text = "".join(block.text for block in response.content
                       if getattr(block, "type", "") == "text")
        return parse_judgement(text, allowed)
