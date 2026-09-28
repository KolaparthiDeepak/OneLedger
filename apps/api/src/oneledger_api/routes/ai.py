"""AI settings (bring-your-own-key), assistant, classification suggestions, and the read-only tool endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from oneledger_shared.errors import ConflictError, ValidationFailed
from pydantic import BaseModel, Field

from ..deps import AdminAuth, ReadAuth, WriteAuth
from ..security.auth import SCOPE_READ_DETAIL
from ..services import ai
from ..services.ai_providers import available_providers, provider_info
from ..services.audit import audit
from ..services.finance_tools import TOOLS, ToolContext, run_tool

router = APIRouter(tags=["ai"])

SHARING_NOTICE = (
    "When the assistant is enabled, answering a question sends your question plus the minimum ledger results needed "
    "(grouped totals, category names, masked account names, and dates) to the configured AI provider. Transaction "
    "descriptions and merchant text are sent only if you also enable 'share descriptions'. Nothing is sent until you "
    "ask a question. OneLedger never lets the AI move money or change data."
)


def settings_out(a: ReadAuth | WriteAuth | AdminAuth) -> dict[str, Any]:
    s = ai.get_settings_row(a.ctx, a.db, a.owner_id)
    k = ai.key_status(a.ctx, a.db, a.owner_id, s.provider)
    providers = [
        {
            "key": p.key,
            "label": p.label,
            "needs_key": p.needs_key,
            "default_model": p.default_model,
            "key_hint": p.key_hint,
            "key_saved": ai.key_status(a.ctx, a.db, a.owner_id, p.key).source in ("owner", "server"),
        }
        for p in available_providers(a.ctx)
    ]
    return {
        "server_enabled": a.ctx.settings.ai_enabled,
        "provider": s.provider,
        "model": s.model,
        "providers": providers,
        "assistant_enabled": s.assistant_enabled,
        "classification_enabled": s.classification_enabled,
        "share_descriptions": s.share_descriptions,
        "auto_categorize": s.auto_categorize,
        "auto_categorize_ready": ai.auto_categorize_ready(a.ctx, a.db, a.owner_id),
        "auto_categorize_error": {"code": s.auto_error_code, "message": s.auto_error_message, "at": s.auto_error_at}
        if s.auto_error_code and s.auto_categorize
        else None,
        "opted_in_at": s.opted_in_at,
        "policy_version": s.policy_version,
        "key": {
            "configured": k.configured,
            "source": k.source,
            "masked_suffix": f"…{k.masked_suffix}" if k.masked_suffix else None,
        },
        "notice": SHARING_NOTICE,
        "version": s.version,
    }


@router.get("/ai/settings")
def get_settings(a: AdminAuth) -> dict[str, Any]:
    out = settings_out(a)
    a.commit()
    return out


class AiSettingsIn(BaseModel):
    version: int
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{1,119}$")
    assistant_enabled: bool | None = None
    classification_enabled: bool | None = None
    share_descriptions: bool | None = None
    auto_categorize: bool | None = None
    accept_notice: bool = False


@router.patch("/ai/settings")
def update_settings(body: AiSettingsIn, a: AdminAuth) -> dict[str, Any]:
    s = ai.get_settings_row(a.ctx, a.db, a.owner_id)
    if s.version != body.version:
        raise ConflictError("Settings changed since you loaded them.", code="VERSION_CONFLICT")
    enabling = body.assistant_enabled or body.classification_enabled
    if enabling and s.opted_in_at is None and not body.accept_notice:
        raise ValidationFailed("Read and accept the data-sharing notice to enable AI.", code="NOTICE_NOT_ACCEPTED")
    changed = []
    if body.provider is not None and body.provider != s.provider:
        info = provider_info(a.ctx, body.provider)
        if info is None:
            raise ValidationFailed("That AI provider is not available on this server.", code="AI_PROVIDER_UNSUPPORTED")
        s.provider = info.key
        s.model = body.model or info.default_model or ""
        changed += ["provider", "model"]
        body.model = None
    for f in ("model", "assistant_enabled", "classification_enabled", "share_descriptions", "auto_categorize"):
        v = getattr(body, f)
        if v is not None:
            setattr(s, f, v)
            changed.append(f)
    if enabling and body.accept_notice:
        s.opted_in_at, s.policy_version = datetime.now(UTC), ai.POLICY_VERSION
        changed.append("opted_in_at")
    if not s.assistant_enabled and not s.classification_enabled:
        s.opted_in_at = None
    s.version += 1
    audit(a.db, a.owner_id, a.actor, "ai.settings", "ai_settings", None, changed)
    a.commit()
    return settings_out(a)


class KeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=400)


@router.put("/ai/key")
def set_key(body: KeyIn, a: AdminAuth) -> dict[str, Any]:
    """Saves the key for the currently selected provider (encrypted; never returned)."""
    a.principal.require_strong()
    s = ai.get_settings_row(a.ctx, a.db, a.owner_id)
    ai.store_key(a.ctx, a.db, a.owner_id, s.provider, body.api_key)
    audit(a.db, a.owner_id, a.actor, "ai.key_set", "user_secret", None, ["ai_api_key"])
    a.commit()
    return settings_out(a)


@router.delete("/ai/key")
def remove_key(a: AdminAuth) -> dict[str, Any]:
    s = ai.get_settings_row(a.ctx, a.db, a.owner_id)
    ai.delete_key(a.db, a.owner_id, s.provider)
    audit(a.db, a.owner_id, a.actor, "ai.key_delete", "user_secret", None, ["ai_api_key"])
    a.commit()
    return settings_out(a)


@router.post("/ai/test")
def test_key(a: AdminAuth) -> dict[str, Any]:
    out = ai.test_connection(a.ctx, a.db, a.owner_id)
    a.commit()
    return out


@router.get("/ai/models")
def models(a: AdminAuth) -> dict[str, Any]:
    """The selected provider's live model list (tool-capable models only where the provider says so)."""
    return {"models": ai.list_models(a.ctx, a.db, a.owner_id)}


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


@router.post("/ai/ask")
def ask(body: AskIn, a: WriteAuth) -> dict[str, Any]:
    try:
        r = ai.ask(a.ctx, a.db, a.owner_id, body.question, body.history)
    finally:
        a.commit()  # persists the run record (and report queries used as evidence) even on failure
    return {
        "answer": r.answer,
        "validated": r.validated,
        "metrics": r.metrics,
        "evidence": r.evidence,
        "tools_used": r.tools_used,
        "notice": r.notice,
    }


@router.post("/ai/suggest-categories")
def suggest(a: WriteAuth) -> dict[str, Any]:
    try:
        items = ai.suggest_categories(a.ctx, a.db, a.owner_id)
    finally:
        a.commit()
    return {"suggestions": items, "note": "Suggestions are not applied until you accept them."}


@router.post("/ai/categorize-now", status_code=202)
def categorize_now(a: WriteAuth) -> dict[str, Any]:
    """Queue AI categorisation of everything still uncategorised (applies medium/high confidence only)."""
    ai.require_enabled(a.ctx, a.db, a.owner_id, purpose="classification")
    job_id = ai.schedule_auto_categorize(a.ctx, a.db, a.owner_id, f"manual:{datetime.now(UTC):%Y%m%dT%H%M%S}")
    if job_id is None:
        raise ValidationFailed(
            "Turn on automatic AI categorisation and save an API key first.", code="AI_AUTO_CATEGORIZE_OFF"
        )
    a.commit()
    return {"job_id": str(job_id)}


@router.get("/tools")
def list_tools(a: ReadAuth) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "properties": t.properties} for t in TOOLS.values()]


@router.post("/tools/{name}")
def call_tool(name: str, args: dict[str, Any], a: ReadAuth) -> dict[str, Any]:
    """Read-only tool endpoint used by the MCP server. Never mutates ledger data."""
    out = run_tool(ToolContext(a.db, a.owner_id, detail=SCOPE_READ_DETAIL in a.principal.scopes), name, args)
    a.commit()  # only report-query provenance rows are written
    return out
