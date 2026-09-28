"""AI provider adapters behind one small chat interface.

Two wire formats cover the supported providers:

* ``anthropic`` -- Anthropic Messages API through the official SDK.
* ``openai`` -- the OpenAI-compatible Chat Completions format, used by OpenRouter (default),
  OpenAI, Google Gemini, Groq, DeepSeek, Mistral, Together, local Ollama and a custom endpoint.

Endpoints are fixed here or set by the server operator (``AI_CUSTOM_BASE_URL``); they are never
taken from user input, so the settings page cannot make the server call arbitrary URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
import httpx

from ..context import AppContext

Wire = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    wire: Wire
    base_url: str | None
    default_model: str | None
    needs_key: bool = True
    key_hint: str = ""
    local_only: bool = False
    json_schema: bool = False  # supports response_format json_schema


PROVIDERS: dict[str, ProviderInfo] = {
    p.key: p
    for p in (
        ProviderInfo(
            "openrouter",
            "OpenRouter (any model, one key)",
            "openai",
            "https://openrouter.ai/api/v1",
            "anthropic/claude-opus-5",
            key_hint="sk-or-…",
            json_schema=True,
        ),
        ProviderInfo("anthropic", "Anthropic (Claude)", "anthropic", None, "claude-opus-5", key_hint="sk-ant-…"),
        ProviderInfo(
            "openai", "OpenAI", "openai", "https://api.openai.com/v1", None, key_hint="sk-…", json_schema=True
        ),
        ProviderInfo(
            "gemini",
            "Google Gemini",
            "openai",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            None,
            key_hint="AIza…",
        ),
        ProviderInfo("groq", "Groq", "openai", "https://api.groq.com/openai/v1", None, key_hint="gsk_…"),
        ProviderInfo("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", None),
        ProviderInfo("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", None),
        ProviderInfo("together", "Together AI", "openai", "https://api.together.xyz/v1", None),
        ProviderInfo(
            "ollama",
            "Ollama (on this computer)",
            "openai",
            "http://localhost:11434/v1",
            None,
            needs_key=False,
            local_only=True,
        ),
        ProviderInfo("custom", "Custom OpenAI-compatible endpoint (set by the server)", "openai", None, None),
    )
}


def provider_info(ctx: AppContext, key: str) -> ProviderInfo | None:
    p = PROVIDERS.get(key)
    if p is None:
        return None
    if p.key == "custom":
        base = ctx.settings.ai_custom_base_url.strip()
        if not base:
            return None
        return ProviderInfo("custom", p.label, "openai", base.rstrip("/"), None, needs_key=False)
    if p.local_only and ctx.settings.is_production_like:
        return None
    return p


def available_providers(ctx: AppContext) -> list[ProviderInfo]:
    return [p for k in PROVIDERS if (p := provider_info(ctx, k)) is not None]


# --- Normalized conversation ---------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    stop: Literal["end", "tool", "refusal", "length"]
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    raw_assistant: Any = None  # provider-native assistant content, replayed unchanged (Anthropic)


@dataclass
class Msg:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)  # {"id", "content", "is_error"}
    raw: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


class ProviderCallError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # auth | not_found | rate_limited | connection | http | bad_response
        self.message = message


class Adapter(Protocol):
    def chat(
        self,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec] | None,
        *,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult: ...

    def list_models(self) -> list[str]: ...


# --- Anthropic -----------------------------------------------------------------------------------

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")
_EFFORT_MODELS = ("claude-opus-5", "claude-fable-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7")


class AnthropicAdapter:
    def __init__(self, key: str, model: str, timeout: float):
        self.model = model
        self.client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=1)

    def _wrap(self, exc: Exception) -> ProviderCallError:
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return ProviderCallError("auth", "The provider rejected the API key.")
        if isinstance(exc, anthropic.NotFoundError):
            return ProviderCallError("not_found", "The model is not available for this key.")
        if isinstance(exc, anthropic.RateLimitError):
            return ProviderCallError("rate_limited", "The provider is rate-limiting requests.")
        if isinstance(exc, anthropic.APIConnectionError | anthropic.APITimeoutError):
            return ProviderCallError("connection", "Could not reach the provider.")
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code == 402 or "credit balance" in str(exc).lower():
                return ProviderCallError("credit", "The provider account has no credit left.")
            return ProviderCallError("http", f"The provider returned HTTP {exc.status_code}.")
        return ProviderCallError("http", "The provider request failed.")

    def chat(
        self,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec] | None,
        *,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                wire.append({"role": "assistant", "content": m.raw if m.raw is not None else m.content})
            else:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["id"],
                                "content": r["content"],
                                **({"is_error": True} if r.get("is_error") else {}),
                            }
                            for r in m.results
                        ],
                    }
                )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": wire,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        output_config: dict[str, Any] = {}
        if self.model.startswith(_EFFORT_MODELS):
            output_config["effort"] = "medium" if tools else "low"
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        if self.model.startswith(_FALLBACK_MODELS):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            resp = self.client.beta.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise self._wrap(exc) from exc
        calls = [
            ToolCall(b.id, b.name, dict(b.input) if isinstance(b.input, dict) else {})
            for b in resp.content
            if b.type == "tool_use"
        ]
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        stop: Literal["end", "tool", "refusal", "length"] = (
            "refusal"
            if resp.stop_reason == "refusal"
            else "tool"
            if calls
            else "length"
            if resp.stop_reason == "max_tokens"
            else "end"
        )
        return ChatResult(
            text, calls, stop, resp.usage.input_tokens, resp.usage.output_tokens, resp.model, raw_assistant=resp.content
        )

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list(limit=100)]
        except anthropic.APIError as exc:
            raise self._wrap(exc) from exc


# --- OpenAI-compatible ---------------------------------------------------------------------------


class OpenAICompatAdapter:
    def __init__(
        self,
        info: ProviderInfo,
        key: str | None,
        model: str,
        timeout: float,
        app_url: str,
        transport: httpx.BaseTransport | None = None,
    ):
        assert info.base_url
        self.info = info
        self.model = model
        headers = {"Content-Type": "application/json", "User-Agent": "OneLedger/0.1"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if info.key == "openrouter":
            headers["X-Title"] = "OneLedger"
            headers["HTTP-Referer"] = app_url
        self.http = httpx.Client(
            base_url=info.base_url, headers=headers, timeout=timeout, transport=transport, follow_redirects=False
        )

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = self.http.request(method, path, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderCallError("connection", "The provider timed out.") from exc
        except httpx.HTTPError as exc:
            raise ProviderCallError("connection", "Could not reach the provider.") from exc
        if r.status_code in (401, 403):
            raise ProviderCallError("auth", "The provider rejected the API key.")
        if r.status_code == 404:
            raise ProviderCallError("not_found", "The model or endpoint was not found for this provider.")
        if r.status_code == 429:
            raise ProviderCallError("rate_limited", "The provider is rate-limiting requests (or credit is exhausted).")
        if r.status_code >= 400:
            detail = ""
            try:
                err = r.json().get("error")
                detail = str(err.get("message") if isinstance(err, dict) else err or "")[:200]
            except ValueError:
                pass
            if r.status_code == 402:
                raise ProviderCallError("credit", "The provider account has no credit left.")
            raise ProviderCallError("http", f"The provider returned HTTP {r.status_code}. {detail}".strip())
        try:
            data = r.json()
        except ValueError as exc:
            raise ProviderCallError("bad_response", "The provider returned an unreadable response.") from exc
        if not isinstance(data, dict):
            raise ProviderCallError("bad_response", "The provider returned an unexpected response.")
        return data

    def chat(
        self,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec] | None,
        *,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "user":
                wire.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    entry["tool_calls"] = [
                        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                        for c in m.tool_calls
                    ]
                wire.append(entry)
            else:
                wire.extend({"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m.results)
        body: dict[str, Any] = {"model": self.model, "messages": wire, "max_tokens": max_tokens}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        if json_schema is not None and self.info.json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            }
        data = self._request("POST", "/chat/completions", body)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderCallError("bad_response", "The provider returned no answer.")
        msg = choices[0].get("message") or {}
        calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(
                    str(tc.get("id") or f"call_{i}"), str(fn.get("name") or ""), args if isinstance(args, dict) else {}
                )
            )
        finish = choices[0].get("finish_reason")
        text = msg.get("content") or ""
        if isinstance(text, list):  # some providers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        stop: Literal["end", "tool", "refusal", "length"] = (
            "tool"
            if calls
            else "refusal"
            if finish == "content_filter" or msg.get("refusal")
            else "length"
            if finish == "length"
            else "end"
        )
        usage = data.get("usage") or {}
        return ChatResult(
            str(text).strip(),
            calls,
            stop,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            data.get("model"),
        )

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        items = data.get("data") or []
        out = []
        for m in items:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            params = m.get("supported_parameters")
            if isinstance(params, list) and "tools" not in params:
                continue  # OpenRouter tells us which models can call tools; the assistant needs them
            out.append(str(m["id"]))
        return sorted(out)


def make_adapter(ctx: AppContext, info: ProviderInfo, key: str | None, model: str) -> Adapter:
    timeout = ctx.settings.ai_timeout_seconds
    if info.wire == "anthropic":
        if not key:
            raise ProviderCallError("auth", "An API key is required.")
        return AnthropicAdapter(key, model, timeout)
    return OpenAICompatAdapter(info, key, model, timeout, ctx.settings.app_base_url)
