"""Tests XAIClient : happy path, retry/backoff, parsing, validation, logs."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scripts.xai_client import (
    ALLOWED_TOOL_PARAMS,
    ITEMS_SCHEMA,
    PRICE_INPUT_PER_M_TOKENS,
    PRICE_OUTPUT_PER_M_TOKENS,
    PRICE_TOOL_CALL,
    XAIAuthError,
    XAIClient,
    XAIInvalidResponse,
    XAIRateLimited,
    XAIRequestError,
    XAIResponse,
    XAIUnavailable,
    XAIUsage,
)

XAI_URL = "https://api.x.ai/v1/responses"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_payload(
    items: list[dict] | None = None,
    warnings: list[str] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    tool_calls: int = 2,
    model: str = "grok-4-1-fast-2026-04-15",
) -> dict[str, Any]:
    """Build a well-formed Responses API payload (primary path)."""
    inner = json.dumps({"items": items or [], "warnings": warnings or []})
    return {
        "id": "resp_xxx",
        "model": model,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": inner}],
            }
        ],
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": tool_calls,
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralise time.sleep dans le module xai_client pour ne pas ralentir les tests."""
    monkeypatch.setattr("scripts.xai_client.time.sleep", lambda s: None)


@pytest.fixture
def client() -> XAIClient:
    c = XAIClient(api_key="test-key", max_retries=2, timeout_s=5.0)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_call_returns_xai_response_with_parsed_output(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    items = [{"title": "x", "summary": "y", "canonical_url": "https://e.com"}]
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload(items=items))

    resp = client.call("sys", "user", tool="x_search", tool_params={})

    assert isinstance(resp, XAIResponse)
    assert resp.parsed_output == {"items": items, "warnings": []}
    assert resp.model == "grok-4-1-fast-2026-04-15"
    assert isinstance(resp.usage, XAIUsage)
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 50
    assert resp.usage.tool_calls == 2


def test_call_no_retry_on_success(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    client.call("sys", "user", tool="x_search")
    # If it retried, pytest-httpx would error on missing additional mocks.
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# _extract_output_text — multiple paths
# ---------------------------------------------------------------------------


def test_extract_output_text_primary_path(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    payload = _ok_payload(items=[{"a": 1}])
    httpx_mock.add_response(url=XAI_URL, json=payload)
    resp = client.call("s", "u", tool="x_search")
    assert resp.parsed_output["items"] == [{"a": 1}]


def test_extract_output_text_fallback_output_text_top_level(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    inner = json.dumps({"items": [{"k": 1}], "warnings": []})
    payload = {
        "model": "grok-4-1",
        "output_text": inner,
        "usage": {"input_tokens": 10, "output_tokens": 5, "tool_calls": 1},
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)
    resp = client.call("s", "u", tool="x_search")
    assert resp.parsed_output["items"] == [{"k": 1}]


def test_extract_output_text_fallback_chat_completions_style(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    inner = json.dumps({"items": [{"z": 9}], "warnings": []})
    payload = {
        "model": "grok-4-1",
        "choices": [{"message": {"content": inner}}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "tool_calls": 0},
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)
    resp = client.call("s", "u", tool="x_search")
    assert resp.parsed_output["items"] == [{"z": 9}]


# ---------------------------------------------------------------------------
# Usage / cost
# ---------------------------------------------------------------------------


def test_usage_reads_tool_calls_field_first(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    payload = _ok_payload(tool_calls=7)
    httpx_mock.add_response(url=XAI_URL, json=payload)
    resp = client.call("s", "u", tool="x_search")
    assert resp.usage.tool_calls == 7


def test_usage_falls_back_to_num_tool_calls(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    inner = json.dumps({"items": [], "warnings": []})
    payload = {
        "model": "grok",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": inner}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "num_tool_calls": 4},
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)
    resp = client.call("s", "u", tool="x_search")
    assert resp.usage.tool_calls == 4


def test_usage_falls_back_to_counting_tool_call_entries(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    """Counts all 4 recognized tool-call entry types (custom_tool_call is the
    actual type emitted by xAI per issue #15)."""
    inner = json.dumps({"items": [], "warnings": []})
    payload = {
        "model": "grok",
        "output": [
            {"type": "custom_tool_call", "name": "x_keyword_search"},
            {"type": "tool_call", "name": "x_search"},
            {"type": "tool_use", "name": "x_search"},
            {"type": "function_call", "name": "x_search"},
            {"type": "message", "content": [{"type": "output_text", "text": inner}]},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},  # no tool_calls field
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)
    resp = client.call("s", "u", tool="x_search")
    assert resp.usage.tool_calls == 4


def test_cost_calculation_matches_constants() -> None:
    usage = XAIUsage(input_tokens=1_000_000, output_tokens=2_000_000, tool_calls=10)
    expected = (
        1.0 * PRICE_INPUT_PER_M_TOKENS
        + 2.0 * PRICE_OUTPUT_PER_M_TOKENS
        + 10 * PRICE_TOOL_CALL
    )
    assert usage.cost_usd == pytest.approx(expected)


def test_cost_calculation_zero_for_zero_usage() -> None:
    assert XAIUsage(0, 0, 0).cost_usd == 0.0


# ---------------------------------------------------------------------------
# Retry / error matrix
# ---------------------------------------------------------------------------


def test_200_valid_json_no_retry(client: XAIClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    client.call("s", "u", tool="x_search")
    assert len(httpx_mock.get_requests()) == 1


def test_200_malformed_json_retries_then_succeeds(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    # First response: 200 with non-JSON text in output_text
    bad_payload = {
        "model": "grok",
        "output_text": "this is not json {{{",
        "usage": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
    }
    httpx_mock.add_response(url=XAI_URL, json=bad_payload)
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())

    resp = client.call("s", "u", tool="x_search")
    assert resp.parsed_output == {"items": [], "warnings": []}
    assert len(httpx_mock.get_requests()) == 2


def test_200_malformed_json_persists_raises_invalid_response(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    bad_payload = {
        "model": "grok",
        "output_text": "not json !!!",
        "usage": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
    }
    httpx_mock.add_response(url=XAI_URL, json=bad_payload)
    httpx_mock.add_response(url=XAI_URL, json=bad_payload)

    with pytest.raises(XAIInvalidResponse):
        client.call("s", "u", tool="x_search")
    assert len(httpx_mock.get_requests()) == 2


def test_200_valid_json_missing_items_key_raises_invalid(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    inner = json.dumps({"foo": "bar"})  # missing items key → fatal
    payload = {
        "model": "grok",
        "output_text": inner,
        "usage": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)
    httpx_mock.add_response(url=XAI_URL, json=payload)

    with pytest.raises(XAIInvalidResponse):
        client.call("s", "u", tool="x_search")


def test_200_bare_array_output_wraps_as_items(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    """Issue #15: xAI Responses API may return a bare JSON array instead of
    the documented {items, warnings} dict. Client must wrap it defensively."""
    items = [
        {
            "title": "Sample", "summary": "s", "canonical_url": "https://x.com/a",
            "source_type": "x_account", "source_handle": "@a",
            "published_at": "2026-04-19T03:00:00Z", "score": 0.9,
            "section_id": "ai-tech", "likes": 100, "reposts": 10,
        }
    ]
    inner = json.dumps(items)  # bare array, not {"items": ..., "warnings": ...}
    payload = {
        "model": "grok",
        "output_text": inner,
        "usage": {"input_tokens": 50, "output_tokens": 20, "tool_calls": 1},
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)

    resp = client.call("s", "u", tool="x_search")
    assert resp.parsed_output["items"] == items
    assert resp.parsed_output["warnings"] == []


def test_200_primitive_output_raises_invalid(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    """Output that is neither list nor dict (e.g., a bare string) is pathological."""
    inner = json.dumps("unexpected string output")
    payload = {
        "model": "grok",
        "output_text": inner,
        "usage": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
    }
    httpx_mock.add_response(url=XAI_URL, json=payload)
    httpx_mock.add_response(url=XAI_URL, json=payload)

    with pytest.raises(XAIInvalidResponse):
        client.call("s", "u", tool="x_search")


def test_429_then_success(client: XAIClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=XAI_URL, status_code=429, text="rate limit")
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    resp = client.call("s", "u", tool="x_search")
    assert resp.parsed_output["items"] == []
    assert len(httpx_mock.get_requests()) == 2


def test_429_then_429_raises_rate_limited(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=XAI_URL, status_code=429, text="rate limit")
    httpx_mock.add_response(url=XAI_URL, status_code=429, text="rate limit")
    with pytest.raises(XAIRateLimited):
        client.call("s", "u", tool="x_search")


def test_5xx_exhaust_retries_raises_unavailable(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    # max_retries=2 → 3 attempts total
    for _ in range(3):
        httpx_mock.add_response(url=XAI_URL, status_code=503, text="boom")
    with pytest.raises(XAIUnavailable):
        client.call("s", "u", tool="x_search")
    assert len(httpx_mock.get_requests()) == 3


def test_5xx_then_5xx_then_200_succeeds(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=XAI_URL, status_code=502)
    httpx_mock.add_response(url=XAI_URL, status_code=500)
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    resp = client.call("s", "u", tool="x_search")
    assert isinstance(resp, XAIResponse)
    assert len(httpx_mock.get_requests()) == 3


def test_timeout_then_success(client: XAIClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.TimeoutException("slow"), url=XAI_URL)
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    resp = client.call("s", "u", tool="x_search")
    assert isinstance(resp, XAIResponse)
    assert len(httpx_mock.get_requests()) == 2


def test_network_error_exhaust_retries_raises_unavailable(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    for _ in range(3):
        httpx_mock.add_exception(httpx.ConnectError("refused"), url=XAI_URL)
    with pytest.raises(XAIUnavailable):
        client.call("s", "u", tool="x_search")
    assert len(httpx_mock.get_requests()) == 3


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_no_retry(
    client: XAIClient, httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(url=XAI_URL, status_code=status, text="nope")
    with pytest.raises(XAIAuthError):
        client.call("s", "u", tool="x_search")
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("status", [400, 422])
def test_request_error_no_retry(
    client: XAIClient, httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(url=XAI_URL, status_code=status, text="bad request")
    with pytest.raises(XAIRequestError):
        client.call("s", "u", tool="x_search")
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------


def test_build_body_has_model_input_tools_and_strict_schema(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    client.call("SYSTEM", "USER", tool="x_search", tool_params={})

    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)

    assert body["model"] == client.model
    assert body["input"] == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "USER"},
    ]
    assert isinstance(body["tools"], list) and len(body["tools"]) == 1
    assert body["tools"][0]["type"] == "x_search"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "briefing_items"
    assert body["response_format"]["json_schema"]["schema"] == ITEMS_SCHEMA


def test_build_body_spreads_x_search_params(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    handles = ["karpathy", "AnthropicAI"]
    client.call(
        "s", "u",
        tool="x_search",
        tool_params={"allowed_x_handles": handles, "from_date": "2026-04-18", "to_date": "2026-04-19"},
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    tool = body["tools"][0]
    assert tool["type"] == "x_search"
    assert tool["allowed_x_handles"] == handles
    assert tool["from_date"] == "2026-04-18"
    assert tool["to_date"] == "2026-04-19"


def test_build_body_spreads_web_search_params(
    client: XAIClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=XAI_URL, json=_ok_payload())
    domains = ["lapresse.ca", "lesaffaires.com"]
    client.call(
        "s", "u",
        tool="web_search",
        tool_params={"allowed_domains": domains, "from_date": "2026-04-18", "to_date": "2026-04-19"},
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    tool = body["tools"][0]
    assert tool["type"] == "web_search"
    assert tool["allowed_domains"] == domains
    assert tool["from_date"] == "2026-04-18"


def test_tool_params_unknown_key_raises_request_error(client: XAIClient) -> None:
    with pytest.raises(XAIRequestError):
        client.call("s", "u", tool="x_search", tool_params={"foo": "bar"})


def test_tool_params_with_type_key_raises_request_error(client: XAIClient) -> None:
    with pytest.raises(XAIRequestError):
        client.call("s", "u", tool="x_search", tool_params={"type": "x_search"})


def test_allowed_tool_params_covers_both_tools() -> None:
    assert "x_search" in ALLOWED_TOOL_PARAMS
    assert "web_search" in ALLOWED_TOOL_PARAMS
    assert "allowed_x_handles" in ALLOWED_TOOL_PARAMS["x_search"]
    assert "allowed_domains" in ALLOWED_TOOL_PARAMS["web_search"]


# ---------------------------------------------------------------------------
# Schema (ITEMS_SCHEMA)
# ---------------------------------------------------------------------------


def test_items_schema_has_required_keys() -> None:
    assert ITEMS_SCHEMA["required"] == ["items", "warnings"]
    assert "items" in ITEMS_SCHEMA["properties"]
    assert "warnings" in ITEMS_SCHEMA["properties"]


def test_items_schema_item_required_keys_complete() -> None:
    item_schema = ITEMS_SCHEMA["properties"]["items"]["items"]
    required = set(item_schema["required"])
    expected = {
        "title", "summary", "canonical_url", "source_type",
        "source_handle", "published_at", "score", "section_id",
        "likes", "reposts",
    }
    assert required == expected


def test_items_schema_no_format_keys_anywhere() -> None:
    # `format: uri` and `format: date-time` would break strict json_schema
    # → must NOT appear in the schema.
    serialized = json.dumps(ITEMS_SCHEMA)
    assert '"format"' not in serialized
    assert "uri" not in serialized
    assert "date-time" not in serialized


def test_items_schema_additional_properties_false_at_item_level() -> None:
    assert ITEMS_SCHEMA["additionalProperties"] is False
    item_schema = ITEMS_SCHEMA["properties"]["items"]["items"]
    assert item_schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _parse_log_lines(captured_err: str) -> list[dict]:
    out = []
    for line in captured_err.strip().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def test_successful_call_emits_structured_log(
    client: XAIClient, httpx_mock: HTTPXMock, capsys
) -> None:
    httpx_mock.add_response(
        url=XAI_URL,
        json=_ok_payload(input_tokens=123, output_tokens=45, tool_calls=2),
    )
    client.call("s", "u", tool="x_search", prompt_label="my_label")

    captured = capsys.readouterr()
    logs = _parse_log_lines(captured.err)
    ok_logs = [r for r in logs if r.get("status") == "ok"]
    assert len(ok_logs) == 1
    rec = ok_logs[0]
    assert rec["event"] == "xai_call"
    assert rec["prompt"] == "my_label"
    assert rec["tool"] == "x_search"
    assert rec["tokens_in"] == 123
    assert rec["tokens_out"] == 45
    assert "cost_usd" in rec


def test_failed_call_log_status_matches_error_type(
    client: XAIClient, httpx_mock: HTTPXMock, capsys
) -> None:
    httpx_mock.add_response(url=XAI_URL, status_code=401, text="nope")
    with pytest.raises(XAIAuthError):
        client.call("s", "u", tool="x_search", prompt_label="auth_test")

    captured = capsys.readouterr()
    logs = _parse_log_lines(captured.err)
    auth_logs = [r for r in logs if r.get("status") == "auth_error"]
    assert len(auth_logs) == 1
    assert auth_logs[0]["http"] == 401


def test_failed_5xx_log_status_server_error(
    client: XAIClient, httpx_mock: HTTPXMock, capsys
) -> None:
    for _ in range(3):
        httpx_mock.add_response(url=XAI_URL, status_code=503)
    with pytest.raises(XAIUnavailable):
        client.call("s", "u", tool="x_search", prompt_label="srv")

    captured = capsys.readouterr()
    logs = _parse_log_lines(captured.err)
    srv_logs = [r for r in logs if r.get("status") == "server_error"]
    assert len(srv_logs) == 3
