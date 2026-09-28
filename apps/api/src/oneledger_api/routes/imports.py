"""Statement imports: upload, map, preview, resolve, confirm, status, cancel, retry, error report."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
from oneledger_db.models import Import, ImportRow, Job
from oneledger_domain.enums import ImportRowResolution, ImportRowStatus
from oneledger_providers.imports import ColumnMapping, detect_date_formats
from oneledger_shared.errors import ValidationFailed
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import ReadAuth, WriteAuth
from ..security.ratelimit import hit
from ..services import imports as svc
from ..services.jobs import run_until

router = APIRouter(tags=["imports"])


def import_out(imp: Import, job: Job | None = None) -> dict[str, Any]:
    return {
        "id": str(imp.id),
        "account_id": str(imp.account_id),
        "filename": imp.filename,
        "format": imp.file_format,
        "state": imp.state.value,
        "error": {"code": imp.error_code, "stage": imp.error_stage, "message": imp.error_message}
        if imp.error_code
        else None,
        "headers": imp.headers,
        "suggested_mapping": imp.suggested_mapping,
        "mapping": imp.mapping,
        "preview_hash": imp.preview_hash,
        "preview_version": imp.preview_version,
        "counts": imp.counts,
        "date_min": imp.date_min,
        "date_max": imp.date_max,
        "is_replay": imp.replay_of_id is not None,
        "created_at": imp.created_at,
        "confirmed_at": imp.confirmed_at,
        "completed_at": imp.completed_at,
        "job": {
            "id": str(job.id),
            "status": job.status.value,
            "attempts": job.attempts,
            "error_code": job.last_error_code,
        }
        if job
        else None,
    }


def _job(a: ReadAuth | WriteAuth, imp: Import) -> Job | None:
    return a.db.get(Job, imp.job_id) if imp.job_id else None


@router.get("/imports")
def list_imports(a: ReadAuth, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> list[dict[str, Any]]:
    rows = a.db.scalars(
        select(Import).where(Import.owner_id == a.owner_id).order_by(Import.created_at.desc()).limit(limit)
    )
    return [import_out(i) for i in rows]


@router.post("/imports", status_code=201)
async def upload(
    a: WriteAuth, account_id: Annotated[uuid.UUID, Form()], file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    if not hit(a.db, f"import:{a.owner_id}", limit=60, window_seconds=3600):
        from oneledger_shared.errors import RateLimited

        raise RateLimited("Too many uploads. Try again later.")
    limit = a.ctx.settings.max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed(f"Files are limited to {limit // (1024 * 1024)} MiB.", code="FILE_TOO_LARGE")
    imp = svc.create_import(a.ctx, a.db, a.owner_id, account_id, file.filename or "statement", data, a.actor)
    a.commit()
    return import_out(imp)


@router.get("/imports/{import_id}")
def get_import(import_id: uuid.UUID, a: ReadAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id)
    return import_out(imp, _job(a, imp))


@router.get("/imports/{import_id}/rows")
def rows(
    import_id: uuid.UUID,
    a: ReadAuth,
    status: ImportRowStatus | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id)
    stmt = select(ImportRow).where(ImportRow.import_id == imp.id)
    if status:
        stmt = stmt.where(ImportRow.status == status)
    items = a.db.scalars(stmt.order_by(ImportRow.row_index).offset(offset).limit(limit)).all()
    return {
        "items": [
            {
                "row_index": r.row_index,
                "source_row_number": r.source_row_number,
                "raw": r.raw,
                "transaction_date": r.transaction_date,
                "value_date": r.value_date,
                "amount": str(r.amount) if r.amount is not None else None,
                "currency": r.currency,
                "description": r.description,
                "reference": r.reference,
                "balance_after": str(r.balance_after) if r.balance_after is not None else None,
                "status": r.status.value,
                "skipped": r.skipped,
                "errors": r.errors,
                "dedup_reason": r.dedup_reason,
                "duplicate_of_transaction_id": str(r.duplicate_of_transaction_id)
                if r.duplicate_of_transaction_id
                else None,
                "resolution": r.resolution.value if r.resolution else None,
                "committed": r.committed,
            }
            for r in items
        ],
        "offset": offset,
        "limit": limit,
        "total": svc.row_count(a.db, imp),
    }


@router.post("/imports/{import_id}/date-formats")
def date_formats(import_id: uuid.UUID, column: Annotated[str, Query(max_length=200)], a: ReadAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id)
    if column not in imp.headers:
        raise ValidationFailed("Unknown column.", code="UNKNOWN_COLUMN")
    values = [
        r.raw.get(column, "") for r in a.db.scalars(select(ImportRow).where(ImportRow.import_id == imp.id).limit(500))
    ]
    formats, ambiguous = detect_date_formats(values)
    return {"formats": formats, "ambiguous": ambiguous}


@router.put("/imports/{import_id}/mapping")
def set_mapping(import_id: uuid.UUID, mapping: ColumnMapping, a: WriteAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    svc.apply_mapping(a.db, a.owner_id, imp, mapping, a.actor)
    a.commit()
    return import_out(imp)


class ResolutionsIn(BaseModel):
    resolutions: dict[int, ImportRowResolution] = Field(max_length=100_000)


@router.post("/imports/{import_id}/resolutions")
def resolutions(import_id: uuid.UUID, body: ResolutionsIn, a: WriteAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    svc.set_resolutions(a.db, a.owner_id, imp, body.resolutions, a.actor)
    a.commit()
    return import_out(imp)


class ConfirmIn(BaseModel):
    preview_hash: str = Field(min_length=64, max_length=64)
    accept_rejections: bool = False
    run_now: bool = Field(default=True, description="Process the commit job inline within a bounded time budget.")


@router.post("/imports/{import_id}/confirm", status_code=202)
def confirm(import_id: uuid.UUID, body: ConfirmIn, a: WriteAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    svc.confirm(a.db, a.owner_id, imp, body.preview_hash, body.accept_rejections, a.actor)
    a.commit()
    if body.run_now:
        # Durable job already queued; running a bounded slice here just reduces latency.
        run_until(a.ctx, min(a.ctx.settings.job_time_budget_seconds, 15))
    a.db.expire_all()
    imp = svc.get_import(a.db, a.owner_id, import_id)
    return import_out(imp, _job(a, imp))


@router.post("/imports/{import_id}/cancel")
def cancel(import_id: uuid.UUID, a: WriteAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    svc.cancel(a.db, a.owner_id, imp, a.actor)
    a.commit()
    return import_out(imp)


@router.post("/imports/{import_id}/reparse", status_code=201)
def reparse(import_id: uuid.UUID, a: WriteAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    new = svc.reparse(a.ctx, a.db, a.owner_id, imp, a.actor)
    a.commit()
    return import_out(new)


@router.get("/imports/{import_id}/delete-preview")
def delete_preview(import_id: uuid.UUID, a: ReadAuth) -> dict[str, Any]:
    return svc.delete_preview(a.db, a.owner_id, svc.get_import(a.db, a.owner_id, import_id))


class DeleteIn(BaseModel):
    confirm: bool = Field(description="Must be true: removes every transaction this import added.")


@router.post("/imports/{import_id}/delete")
def delete_import(import_id: uuid.UUID, body: DeleteIn, a: WriteAuth) -> dict[str, Any]:
    if not body.confirm:
        raise ValidationFailed("Confirm to delete this import and its transactions.", code="CONFIRMATION_REQUIRED")
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    result = svc.delete_import(a.ctx, a.db, a.owner_id, imp, a.actor)
    a.commit()
    return result


@router.post("/imports/{import_id}/retry", status_code=202)
def retry(import_id: uuid.UUID, a: WriteAuth) -> dict[str, Any]:
    imp = svc.get_import(a.db, a.owner_id, import_id, lock=True)
    svc.retry(a.db, a.owner_id, imp, a.actor)
    a.commit()
    return import_out(imp, _job(a, imp))


@router.get("/imports/{import_id}/errors.csv", response_class=PlainTextResponse)
def errors_csv(import_id: uuid.UUID, a: ReadAuth) -> PlainTextResponse:
    imp = svc.get_import(a.db, a.owner_id, import_id)
    return PlainTextResponse(
        svc.error_report_csv(a.db, imp),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="import-{imp.id}-errors.csv"'},
    )
