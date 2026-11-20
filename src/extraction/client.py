"""Provider boundary. The extractor only knows this interface, so the provider
(OpenAI, Anthropic, Gemini, ...) is a one-file adapter chosen by the team.

API keys belong to the adapter, read from the environment (.env, never Git), and
never appear in prompts, logs or outcomes."""
from dataclasses import dataclass, field
from typing import Protocol

from src.extraction.pages import Page


class ModelCallError(RuntimeError):
    """Provider/network failure. Messages must not include keys or document text.

    `safe_detail` is set only by the adapters in providers.py, from the provider's
    structured error fields, and is the only part copied into an outcome."""

    def __init__(self, message: str = "", safe_detail: str | None = None):
        super().__init__(message)
        self.safe_detail = safe_detail


@dataclass(frozen=True)
class ModelReply:
    text: str                                       # Raw model output (expected: JSON)
    usage: dict[str, int] = field(default_factory=dict)  # Provider-reported tokens, if any


class ModelClient(Protocol):
    model_id: str

    async def complete(self, *, system: str, user: str, pages: list[Page], json_schema: dict) -> ModelReply:
        ...


class FakeModelClient:
    """Scripted replies for tests and offline demos. Each item is reply text or an
    exception to raise. Records every call so tests can inspect what was sent."""

    def __init__(self, replies: list[str | Exception], model_id: str = "fake-model"):
        self.replies = list(replies)
        self.model_id = model_id
        self.calls: list[dict] = []

    async def complete(self, *, system, user, pages, json_schema):
        self.calls.append({"system": system, "user": user, "pages": len(pages)})
        if not self.replies:
            raise ModelCallError("No scripted reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ModelReply(reply, {"input_tokens": 0, "output_tokens": 0})
