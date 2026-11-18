"""Model provider adapters: OpenAI, Anthropic, Google Gemini.

Plain HTTPS calls (httpx) instead of three SDKs, so each adapter is easy to read and
the request sent to the provider is visible in one place. Choose with environment
variables (see .env.example):

    MATCHLEDGER_PROVIDER=openai | anthropic | gemini
    MATCHLEDGER_MODEL=<exact model identifier, recorded in every outcome>
    MATCHLEDGER_TEMPERATURE=0   (optional; omitted by default, see below)
    OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY

Temperature is not sent unless set: some current models reject the parameter
(first real run, 2026-11-16: every call failed with HTTP 400 "temperature is
deprecated for this model"). Run-to-run variation is measured with
`evaluate --repeat` instead of being assumed away.

Keys are read here and sent only in request headers. They never go into prompts,
logs, outcomes or exceptions.
"""
import base64
import os

import httpx

from src.extraction.client import ModelCallError, ModelReply
from src.extraction.pages import Page

TIMEOUT_SECONDS = 90.0
MAX_OUTPUT_TOKENS = 4096


def _b64(page: Page) -> str:
    return base64.b64encode(page.image).decode("ascii")


class _HttpClient:
    provider = "base"
    key_env = ""

    def __init__(self, model_id: str, api_key: str | None = None, temperature: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not model_id:
            raise ValueError("A model identifier is required")
        self.model_id = model_id
        self.temperature = temperature  # None: provider default (some models reject the parameter)
        self._api_key = api_key or os.environ.get(self.key_env)
        if not self._api_key:
            raise ValueError(f"Set {self.key_env} (in .env, never in code or Git)")
        self._transport = transport

    def __repr__(self):  # Never show the key.
        return f"{type(self).__name__}(model_id={self.model_id!r})"

    async def _post(self, url: str, headers: dict, body: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport) as http:
                response = await http.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ModelCallError(f"{self.provider} request failed ({type(exc).__name__})") from None
        if response.status_code != 200:
            detail = self._error_detail(response)
            raise ModelCallError(f"{self.provider} returned HTTP {response.status_code}{detail}",
                                 safe_detail=f"HTTP {response.status_code}{detail}")
        try:
            return response.json()
        except ValueError:
            raise ModelCallError(f"{self.provider} returned a non-JSON response") from None


    def _error_detail(self, response: httpx.Response) -> str:
        """The provider's short error type and message (e.g. 'invalid_request_error:
        `temperature` is deprecated'), which say what to fix. Only the structured
        error.message field is used, never the raw body; it is cut to 200 characters
        and dropped if it contains the key."""
        try:
            error = response.json().get("error")
        except (ValueError, AttributeError):
            return ""
        if not isinstance(error, dict):
            return ""
        kind = str(error.get("type") or error.get("status") or error.get("code") or "")[:60]
        message = str(error.get("message") or "")[:200]
        if self._api_key and self._api_key in message:
            message = ""
        text = ": ".join(part for part in (kind, message) if part)
        return f" ({text})" if text else ""


class OpenAIClient(_HttpClient):
    provider, key_env = "openai", "OPENAI_API_KEY"
    url = "https://api.openai.com/v1/chat/completions"

    async def complete(self, *, system, user, pages, json_schema) -> ModelReply:
        content = [{"type": "image_url", "image_url": {"url": f"data:{p.media_type};base64,{_b64(p)}"}}
                   for p in pages]
        body = {"model": self.model_id,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": [*content, {"type": "text", "text": user}]}],
                "response_format": {"type": "json_object"}}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        data = await self._post(self.url, {"Authorization": f"Bearer {self._api_key}"}, body)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ModelCallError("openai response had no message content") from None
        usage = data.get("usage") or {}
        return ModelReply(text, {"input_tokens": usage.get("prompt_tokens", 0),
                                 "output_tokens": usage.get("completion_tokens", 0)})


class AnthropicClient(_HttpClient):
    provider, key_env = "anthropic", "ANTHROPIC_API_KEY"
    url = "https://api.anthropic.com/v1/messages"

    async def complete(self, *, system, user, pages, json_schema) -> ModelReply:
        content = [{"type": "image", "source": {"type": "base64", "media_type": p.media_type, "data": _b64(p)}}
                   for p in pages]
        body = {"model": self.model_id, "max_tokens": MAX_OUTPUT_TOKENS, "system": system,
                "messages": [{"role": "user", "content": [*content, {"type": "text", "text": user}]}]}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        headers = {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"}
        data = await self._post(self.url, headers, body)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        if not text:
            raise ModelCallError("anthropic response had no text content")
        usage = data.get("usage") or {}
        return ModelReply(text, {"input_tokens": usage.get("input_tokens", 0),
                                 "output_tokens": usage.get("output_tokens", 0)})


class GeminiClient(_HttpClient):
    provider, key_env = "gemini", "GEMINI_API_KEY"
    base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    async def complete(self, *, system, user, pages, json_schema) -> ModelReply:
        parts = [{"inline_data": {"mime_type": p.media_type, "data": _b64(p)}} for p in pages]
        config = {"responseMimeType": "application/json", "maxOutputTokens": MAX_OUTPUT_TOKENS}
        if self.temperature is not None:
            config["temperature"] = self.temperature
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [*parts, {"text": user}]}],
                "generationConfig": config}
        url = f"{self.base_url}/{self.model_id}:generateContent"
        data = await self._post(url, {"x-goog-api-key": self._api_key}, body)
        try:
            text = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise ModelCallError("gemini response had no candidate text") from None
        usage = data.get("usageMetadata") or {}
        return ModelReply(text, {"input_tokens": usage.get("promptTokenCount", 0),
                                 "output_tokens": usage.get("candidatesTokenCount", 0)})


PROVIDERS = {"openai": OpenAIClient, "anthropic": AnthropicClient, "gemini": GeminiClient}


def client_from_env(**kwargs):
    provider = os.environ.get("MATCHLEDGER_PROVIDER", "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Set MATCHLEDGER_PROVIDER to one of: {', '.join(PROVIDERS)}")
    temperature = os.environ.get("MATCHLEDGER_TEMPERATURE", "").strip()
    if temperature and "temperature" not in kwargs:
        kwargs["temperature"] = float(temperature)
    return PROVIDERS[provider](os.environ.get("MATCHLEDGER_MODEL", "").strip(), **kwargs)


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing environment variables win."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return
    for raw in lines:
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
