"""Liveness and readiness. Public output discloses no secrets or financial counts."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..deps import DB

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/ready")
def ready(db: DB) -> JSONResponse:
    try:
        version = db.execute(text("SELECT version_num FROM oneledger.alembic_version")).scalar()
        return JSONResponse({"status": "ready", "schema": version})
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
