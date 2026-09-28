"""OpenAI-compatible adapter: request shape, tool-call round trip, errors, model listing (offline)."""

from __future__ import annotations

import json

import httpx
import pytest
from oneledger_api.services.ai_providers import (
    PROVIDERS,
    Msg,
    OpenAICompatAdapter,
    ProviderCallError,
    ToolCall,
    ToolSpec,
)

TOOL = ToolSpec("get_net_worth", "Net worth", {"type": "object", "properties": {}, "additionalProperties": False})


def _adapter(handler, provider="openrouter"):
    return OpenAICompatAdapter(
        PROVIDERS[provider],
        "sk-or-test-key",
        "anthropic/claude-opus-5",
        5.0,
        "http://localhost:3000",
        transport=httpx.MockTransport(handler),
    )


def test_tool_call_round_trip_and_headers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request, body))
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={
                    "model": "anthropic/claude-opus-5",
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "get_net_worth", "arguments": "{}"},
                                    }
                                ],
                            },
                        }
                    ],
                },
            )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Net worth is {{m1}}."}}]}
        )

    a = _adapter(handler)
    r1 = a.chat("sys", [Msg("user", "net worth?")], [TOOL], max_tokens=100)
    assert r1.stop == "tool" and r1.tool_calls == [ToolCall("c1", "get_net_worth", {})] and r1.input_tokens == 7
    r2 = a.chat(
        "sys",
        [
            Msg("user", "net worth?"),
            Msg("assistant", "", tool_calls=r1.tool_calls),
            Msg("tool", results=[{"id": "c1", "content": "{}"}]),
        ],
        [TOOL],
        max_tokens=100,
    )
    assert r2.stop == "end" and r2.text == "Net worth is {{m1}}."
    req, body = seen[0]
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-or-test-key" and req.headers["x-title"] == "OneLedger"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["function"]["name"] == "get_net_worth"
    second = seen[1][1]["messages"]
    assert second[2]["tool_calls"][0]["id"] == "c1" and second[3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "{}",
    }


@pytest.mark.parametrize(
    "status,kind", [(401, "auth"), (404, "not_found"), (429, "rate_limited"), (402, "credit"), (500, "http")]
)
def test_errors_are_mapped_without_leaking_bodies(status, kind):
    a = _adapter(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(ProviderCallError) as e:
        a.chat("s", [Msg("user", "x")], None, max_tokens=5)
    assert e.value.kind == kind


def test_openrouter_model_list_keeps_tool_capable_models():
    data = {
        "data": [
            {"id": "a/tools", "supported_parameters": ["tools"]},
            {"id": "b/no-tools", "supported_parameters": ["temperature"]},
            {"id": "c/unknown"},
        ]
    }
    assert _adapter(lambda r: httpx.Response(200, json=data)).list_models() == ["a/tools", "c/unknown"]


def test_no_redirect_following():
    a = _adapter(lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    with pytest.raises(ProviderCallError):
        a.chat("s", [Msg("user", "x")], None, max_tokens=5)
