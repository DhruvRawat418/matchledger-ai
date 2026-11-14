"""Provider adapters and the evaluation runner, tested with a mocked network:
checks what each adapter sends, how it reads replies, and that keys never leak."""
import asyncio
import json

import httpx
import pytest

from src.extraction import FakeModelClient, ModelCallError, Page
from src.extraction.evaluate import evaluate, summarize, write_outputs
from src.extraction.providers import AnthropicClient, GeminiClient, OpenAIClient, client_from_env

PAGE = Page(1, b"\x89PNGfake", "image/png", "text")
KEY = "sk-test-SECRET"


def mock(reply_json, status=200, seen=None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=reply_json)
    return httpx.MockTransport(handler)


def call(client):
    return asyncio.run(client.complete(system="SYS", user="USER", pages=[PAGE], json_schema={}))


def test_openai_request_and_reply():
    seen = []
    client = OpenAIClient("model-x", api_key=KEY, transport=mock(
        {"choices": [{"message": {"content": "{\"a\":1}"}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        seen=seen))
    reply = call(client)
    body = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "model-x" and body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert (reply.text, reply.usage) == ("{\"a\":1}", {"input_tokens": 7, "output_tokens": 3})


def test_anthropic_request_and_reply():
    seen = []
    client = AnthropicClient("model-y", api_key=KEY, transport=mock(
        {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 5, "output_tokens": 2}}, seen=seen))
    reply = call(client)
    body = json.loads(seen[0].content)
    assert seen[0].headers["x-api-key"] == KEY and seen[0].headers["anthropic-version"]
    assert body["system"] == "SYS" and body["messages"][0]["content"][0]["type"] == "image"
    assert reply.usage == {"input_tokens": 5, "output_tokens": 2}


def test_gemini_request_and_reply():
    seen = []
    client = GeminiClient("model-z", api_key=KEY, transport=mock(
        {"candidates": [{"content": {"parts": [{"text": "{}"}]}}],
         "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 1}}, seen=seen))
    reply = call(client)
    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/models/model-z:generateContent")
    assert seen[0].headers["x-goog-api-key"] == KEY and KEY not in str(seen[0].url)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert reply.usage == {"input_tokens": 4, "output_tokens": 1}


@pytest.mark.parametrize("cls", [OpenAIClient, AnthropicClient, GeminiClient])
def test_http_error_is_model_call_error_without_body_or_key(cls):
    client = cls("m", api_key=KEY, transport=mock({"error": "echo of document MON-24 and " + KEY}, status=429))
    with pytest.raises(ModelCallError) as info:
        call(client)
    assert "429" in str(info.value) and KEY not in str(info.value) and "MON-24" not in str(info.value)
    assert KEY not in repr(client)


@pytest.mark.parametrize("cls", [OpenAIClient, AnthropicClient, GeminiClient])
def test_network_failure_is_model_call_error(cls):
    def boom(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(ModelCallError):
        call(cls("m", api_key=KEY, transport=httpx.MockTransport(boom)))


def test_empty_reply_is_model_call_error():
    with pytest.raises(ModelCallError):
        call(AnthropicClient("m", api_key=KEY, transport=mock({"content": []})))


def test_client_from_env(monkeypatch):
    monkeypatch.setenv("MATCHLEDGER_PROVIDER", "anthropic")
    monkeypatch.setenv("MATCHLEDGER_MODEL", "some-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    client = client_from_env()
    assert isinstance(client, AnthropicClient) and client.model_id == "some-model"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        client_from_env()
    monkeypatch.setenv("MATCHLEDGER_PROVIDER", "nope")
    with pytest.raises(ValueError):
        client_from_env()


# ------------------------------------------------------------------ evaluation runner

def test_evaluation_run_end_to_end(tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    case = {"case_id": "case-x", "categories": ["identical"], "documents": [{
        "case_id": "case-x", "revision": 1, "document_id": "case-x-po", "document_type": "purchase_order",
        "business_document_number": "PO-1", "supplier_id": "SUP-A", "purchase_order_id": None,
        "currency": "USD", "line_items": [{"line_id": "1", "description": "Lamp", "sku": "LMP", "quantity": "10",
                                           "unit": "each", "unit_price": "18.00",
                                           "evidence": [{"page": 1, "text": "LMP | Lamp | 10 each | $18.00"}]}]},
        {**{"case_id": "case-x", "revision": 1, "document_id": "case-x-missing", "document_type": "invoice",
            "business_document_number": "INV-1", "supplier_id": "SUP-A", "purchase_order_id": None,
            "currency": "USD"}, "line_items": [{"line_id": "1", "description": "Lamp", "sku": None,
                                                "quantity": "10", "unit": "each", "unit_price": "18.00",
                                                "evidence": [{"page": 1, "text": "Lamp 10"}]}]}]}
    (tmp_path / "case-x").mkdir()
    pdf = canvas.Canvas(str(tmp_path / "case-x" / "case-x-po.pdf"))
    pdf.drawString(50, 800, "A Supplier   PO-1   USD")
    pdf.drawString(50, 760, "LMP | Lamp | 10 each | $18.00")
    pdf.save()
    model_reply = json.dumps({"document_type": "purchase_order", "business_document_number": "PO-1",
                              "supplier_name": "A Supplier", "referenced_po_number": None, "currency": "USD",
                              "line_items": [{"description": "Lamp", "sku": "LMP", "quantity": "10", "unit": "each",
                                              "unit_price": "$18.00", "evidence_page": 1,
                                              "evidence_text": "LMP | Lamp | 10 each | $18.00"}]})
    client = FakeModelClient([model_reply, model_reply])
    rows, missing = asyncio.run(evaluate(client, [case], tmp_path, {"A Supplier": "SUP-A"},
                                         {"case-x-po": "layout-a"}, repeat=2))
    assert missing == 1 and len(rows) == 2
    summary = summarize(rows, missing, client.model_id, price_in=1.0, price_out=2.0)
    assert summary["accuracy_overall"] == 1.0
    assert summary["accuracy_by_layout"] == {"layout-a": 1.0}
    assert summary["accuracy_by_document_type"] == {"purchase_order": 1.0}
    assert summary["valid_json_first_attempt_rate"] == 1.0 and summary["extraction_failure_rate"] == 0.0
    write_outputs(tmp_path / "out", rows, summary)
    assert json.loads((tmp_path / "out" / "summary.json").read_text())["documents_scored"] == 2
    assert (tmp_path / "out" / "field_errors.csv").read_text().count("\n") == 1  # header only
