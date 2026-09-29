"""Statement import pipeline.

UPLOADED -> NEEDS_MAPPING -> PREVIEW_READY -> CONFIRMED -> COMMITTING -> COMPLETED
(or FAILED / CANCELLED). Zero ledger writes happen before explicit confirmation of a preview
hash. Commit runs as a durable job in bounded, idempotent chunks into an *unpublished* batch;
reports ignore it until every accepted row has committed, then the batch is published at once.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    BalanceSnapshot,
    Import,
    ImportFile,
    ImportRow,
    Transaction,
    TransactionSource,
)
from oneledger_domain.dedup import Candidate, Decision, Existing, detect_duplicates, fingerprint
from oneledger_domain.enums import (
    AccountNature,
    BalanceKind,
    BalanceSource,
    ImportRowResolution,
    ImportRowStatus,
    ImportState,
    PaymentChannel,
    ReviewKind,
    TransactionSourceKind,
)
from oneledger_domain.text import plural
from oneledger_providers.imports import (
    ColumnMapping,
    FileFormat,
    RawTable,
    UnsupportedFile,
    detect_format,
    match_preset,
    normalize_table,
    parse_file,
    parser_version,
    preset_mapping,
    suggest_mapping,
)
from oneledger_shared.crypto import sha256_hex
from oneledger_shared.errors import ConflictError, NotFoundError, ValidationFailed
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..context import AppContext
from .audit import audit, bump_ledger_revision
from .categories import load_context
from .jobs import (
    FAILURE_HOOKS,
    ClaimedJob,
    PermanentJobError,
    continue_later,
    enqueue,
    finish,
    handler,
    lease_tx,
)
from .ledger import NewTransaction, create_transaction, get_account
from .review import open_review

IMPORT_COMMIT_JOB = "import.commit"
SAMPLE_ROWS = 25


def _safe_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "._- ()")[:120]
    return cleaned or "statement"


def get_import(db: Session, owner_id: uuid.UUID, import_id: uuid.UUID, *, lock: bool = False) -> Import:
    stmt = select(Import).where(Import.id == import_id, Import.owner_id == owner_id)
    if lock:
        stmt = stmt.with_for_update()
    imp = db.scalars(stmt).first()
    if imp is None:
        raise NotFoundError()
    return imp


def create_import(
    ctx: AppContext, db: Session, owner_id: uuid.UUID, account_id: uuid.UUID, filename: str, data: bytes, actor: str
) -> Import:
    account = get_account(db, owner_id, account_id)
    if len(data) > ctx.settings.max_upload_bytes:
        raise ValidationFailed(
            f"Files are limited to {ctx.settings.max_upload_bytes // (1024 * 1024)} MiB.", code="FILE_TOO_LARGE"
        )
    try:
        fmt = detect_format(data)
    except UnsupportedFile as exc:
        raise ValidationFailed(exc.message, code=exc.code) from exc
    digest = sha256_hex(data)
    pversion = parser_version(fmt)
    file_row = ImportFile(
        owner_id=owner_id,
        sha256=digest,
        size_bytes=len(data),
        detected_format=fmt.value,
        content_enc=ctx.box.encrypt(data, associated_data=digest.encode()),
        key_version=ctx.box.version,
        expires_at=datetime.now(UTC) + timedelta(days=ctx.settings.raw_retention_days),
    )
    db.add(file_row)
    db.flush()
    replay = db.scalars(
        select(Import)
        .where(
            Import.owner_id == owner_id,
            Import.account_id == account.id,
            Import.file_sha256 == digest,
            Import.parser_version == pversion,
            Import.state == ImportState.COMPLETED,
        )
        .order_by(Import.completed_at.desc())
    ).first()
    imp = Import(
        owner_id=owner_id,
        account_id=account.id,
        file_id=file_row.id,
        filename=_safe_filename(filename),
        file_format=fmt.value,
        file_sha256=digest,
        state=ImportState.UPLOADED,
        parser_version=pversion,
        replay_of_id=replay.id if replay else None,
    )
    db.add(imp)
    db.flush()
    try:
        table = parse_file(data, fmt)
    except UnsupportedFile as exc:
        imp.state = ImportState.FAILED
        imp.error_code, imp.error_stage, imp.error_message = exc.code, "PARSING", exc.message[:300]
        audit(db, owner_id, actor, "import.failed", "import", imp.id, ["state"])
        return imp
    if len(table.rows) > ctx.settings.max_import_rows:
        imp.state = ImportState.FAILED
        imp.error_code, imp.error_stage = "TOO_MANY_ROWS", "PARSING"
        imp.error_message = f"Imports are limited to {ctx.settings.max_import_rows} rows."
        return imp
    imp.headers = table.headers
    suggestion = suggest_mapping(table)
    if fmt == FileFormat.PDF:
        notes = dict(n.split("=", 1) for n in table.notes if "=" in n)
        formats = [f for f in notes.get("date_formats", "").split(",") if f]
        ambiguous = notes.get("date_ambiguous") == "True"
        suggestion.update(
            date_column="Date",
            description_columns=["Description"],
            amount_mode="split",
            debit_column="Debit",
            credit_column="Credit",
            balance_column="Balance",
            date_format_candidates=formats,
            date_format_ambiguous=ambiguous,
        )
        suggestion.pop("date_format", None)
        if formats and not ambiguous:
            suggestion["date_format"] = formats[0]
    suggestion["currency"] = account.currency
    preset = match_preset(table.headers) if fmt != FileFormat.PDF else None
    if preset is not None:
        suggestion = {
            **suggestion,
            **preset_mapping(preset, table.headers, account.currency),
            "preset": preset.key,
            "preset_label": preset.label,
            "date_format_ambiguous": False,
        }
    imp.suggested_mapping = suggestion
    db.bulk_insert_mappings(
        ImportRow,
        [
            {
                "id": uuid.uuid4(),
                "owner_id": owner_id,
                "import_id": imp.id,
                "row_index": i,
                "source_row_number": table.row_numbers[i],
                "raw": {h: row[j] for j, h in enumerate(table.headers) if row[j]},
                "currency": account.currency,
                "status": ImportRowStatus.VALID,
                "description": "",
            }
            for i, row in enumerate(table.rows)
        ],
    )
    imp.state = ImportState.NEEDS_MAPPING
    audit(db, owner_id, actor, "import.create", "import", imp.id, ["state"])
    # Reuse the last confirmed mapping for identical headers on this account (a saved template).
    saved = db.scalars(
        select(Import)
        .where(
            Import.owner_id == owner_id,
            Import.account_id == account.id,
            Import.state == ImportState.COMPLETED,
            Import.headers == table.headers,
            Import.id != imp.id,
        )
        .order_by(Import.completed_at.desc())
    ).first()
    candidate = saved.mapping if saved and saved.mapping else _complete_suggestion(suggestion)
    if candidate:
        try:
            apply_mapping(db, owner_id, imp, ColumnMapping.model_validate(candidate), actor, table=table)
        except (ValidationError, ValidationFailed):
            imp.state = ImportState.NEEDS_MAPPING
    return imp


def reparse(ctx: AppContext, db: Session, owner_id: uuid.UUID, imp: Import, actor: str) -> Import:
    """Read a stored upload again (e.g. after a parser update) as a new import."""
    if imp.state != ImportState.FAILED or imp.error_stage != "PARSING":
        raise ConflictError("Only files that could not be read can be tried again.", code="IMPORT_STATE_INVALID")
    f = db.get(ImportFile, imp.file_id)
    if f is None or f.content_enc is None:
        raise ConflictError("The uploaded file has expired; upload it again.", code="FILE_PURGED")
    data = ctx.box.decrypt(f.content_enc, associated_data=f.sha256.encode())
    imp.state = ImportState.CANCELLED
    return create_import(ctx, db, owner_id, imp.account_id, imp.filename, data, actor)


def _complete_suggestion(s: dict[str, Any]) -> dict[str, Any] | None:
    keys = {"date_column", "date_format", "description_columns"}
    if not keys <= s.keys() or s.get("date_format_ambiguous"):
        return None
    allowed = set(ColumnMapping.model_fields)
    return {k: v for k, v in s.items() if k in allowed}


def _table_from_rows(imp: Import, rows: list[ImportRow]) -> RawTable:
    return RawTable(
        headers=list(imp.headers),
        rows=[[r.raw.get(h, "") for h in imp.headers] for r in rows],
        row_numbers=[r.source_row_number for r in rows],
        header_row_number=0,
    )


def _mapping_hash(mapping: ColumnMapping) -> str:
    return sha256_hex(json.dumps(mapping.model_dump(mode="json"), sort_keys=True).encode())


def apply_mapping(
    db: Session, owner_id: uuid.UUID, imp: Import, mapping: ColumnMapping, actor: str, table: RawTable | None = None
) -> Import:
    if imp.state not in (ImportState.NEEDS_MAPPING, ImportState.PREVIEW_READY):
        raise ConflictError("This import can no longer be remapped.", code="IMPORT_STATE_INVALID")
    account = db.get(Account, imp.account_id)
    assert account is not None
    if mapping.currency != account.currency:
        raise ValidationFailed("Statement currency must match the account currency.", code="CURRENCY_MISMATCH")
    rows = list(db.scalars(select(ImportRow).where(ImportRow.import_id == imp.id).order_by(ImportRow.row_index)))
    table = table or _table_from_rows(imp, rows)
    try:
        normalized = normalize_table(table, mapping)
    except ValueError as exc:
        raise ValidationFailed(str(exc), code="MAPPING_INVALID") from exc
    imp.state = ImportState.VALIDATING
    imp.mapping = mapping.model_dump(mode="json")
    imp.mapping_hash = _mapping_hash(mapping)
    by_index = {r.row_index: r for r in rows}
    for n in normalized:
        r = by_index[n.row_index]
        r.transaction_date, r.value_date, r.amount = n.transaction_date, n.value_date, n.amount
        r.description, r.reference, r.balance_after = n.description, n.reference, n.balance_after
        r.source_drcr, r.channel, r.currency = n.source_drcr, n.channel.value, mapping.currency
        r.errors = n.errors[:10]
        r.skipped = n.skipped
        r.fingerprint = (
            fingerprint(imp.account_id, n.transaction_date, n.amount, mapping.currency, n.description, n.reference)
            if n.valid and n.transaction_date and n.amount is not None
            else None
        )
        r.status = ImportRowStatus.INVALID if (n.errors and not n.skipped) else ImportRowStatus.VALID
        r.duplicate_of_transaction_id = None
        r.dedup_reason = None
    _run_dedup(db, owner_id, imp, rows)
    for r in rows:
        r.resolution = (
            {
                ImportRowStatus.VALID: ImportRowResolution.IMPORT,
                ImportRowStatus.DUPLICATE: ImportRowResolution.LINK,
                ImportRowStatus.POSSIBLE_DUPLICATE: ImportRowResolution.IMPORT,
                ImportRowStatus.INVALID: ImportRowResolution.SKIP,
            }[r.status]
            if not r.skipped
            else ImportRowResolution.SKIP
        )
    _refresh_preview(imp, rows)
    imp.state = ImportState.PREVIEW_READY
    imp.error_code = imp.error_stage = imp.error_message = None
    audit(db, owner_id, actor, "import.map", "import", imp.id, ["mapping", "state"])
    return imp


def _existing_for(
    db: Session, owner_id: uuid.UUID, account_id: uuid.UUID, lo: date, hi: date, exclude_import: uuid.UUID
) -> list[Existing]:
    txns = db.scalars(
        select(Transaction).where(
            Transaction.owner_id == owner_id,
            Transaction.account_id == account_id,
            Transaction.transaction_date >= lo - timedelta(days=3),
            Transaction.transaction_date <= hi + timedelta(days=3),
            Transaction.deleted_at.is_(None),
            Transaction.merged_into_id.is_(None),
            (Transaction.import_id.is_(None)) | (Transaction.import_id != exclude_import),
            Transaction.is_published.is_(True),
        )
    ).all()
    return [
        Existing(t.id, t.transaction_date, t.amount, t.currency, t.raw_description, t.reference, t.balance_after)
        for t in txns
    ]


def _run_dedup(db: Session, owner_id: uuid.UUID, imp: Import, rows: list[ImportRow]) -> None:
    valid = [r for r in rows if r.status == ImportRowStatus.VALID and not r.skipped]
    if not valid:
        return
    # 1. Same validated file + same mapping on this account: idempotent link to the original rows.
    if imp.replay_of_id:
        original = db.scalars(select(Import).where(Import.id == imp.replay_of_id)).first()
        if original and original.mapping_hash == imp.mapping_hash:
            orig_rows = {
                r.row_index: r for r in db.scalars(select(ImportRow).where(ImportRow.import_id == original.id))
            }
            for r in valid:
                o = orig_rows.get(r.row_index)
                target = o.transaction_id or o.duplicate_of_transaction_id if o else None
                if target is not None:
                    r.status, r.duplicate_of_transaction_id, r.dedup_reason = (
                        ImportRowStatus.DUPLICATE,
                        target,
                        "file_replay",
                    )
            valid = [r for r in valid if r.status == ImportRowStatus.VALID]
            if not valid:
                return
    dates = [r.transaction_date for r in valid if r.transaction_date]
    existing = _existing_for(db, owner_id, imp.account_id, min(dates), max(dates), imp.id)
    candidates = [
        Candidate(r.row_index, r.transaction_date, r.amount, r.currency, r.description, r.reference, r.balance_after)
        for r in valid
        if r.transaction_date and r.amount is not None
    ]
    decisions = detect_duplicates(imp.account_id, candidates, existing)
    by_index = {r.row_index: r for r in valid}
    for key, m in decisions.items():
        r = by_index[key]
        if m.decision == Decision.DUPLICATE:
            r.status, r.duplicate_of_transaction_id, r.dedup_reason = (
                ImportRowStatus.DUPLICATE,
                m.transaction_id,
                m.reason,
            )
        elif m.decision == Decision.POSSIBLE_DUPLICATE:
            r.status = ImportRowStatus.POSSIBLE_DUPLICATE
            r.duplicate_of_transaction_id, r.dedup_reason = m.transaction_id, m.reason


def _preview_hash(imp: Import, rows: list[ImportRow]) -> str:
    h = hashlib.sha256()
    h.update(f"{imp.file_sha256}|{imp.mapping_hash}|{imp.parser_version}".encode())
    for r in rows:
        h.update(
            f"|{r.row_index}:{r.transaction_date}:{r.amount}:{r.description}:{r.reference}:{r.status.value}:"
            f"{r.resolution.value if r.resolution else ''}:{r.duplicate_of_transaction_id}".encode()
        )
    return h.hexdigest()


def _refresh_preview(imp: Import, rows: list[ImportRow]) -> None:
    counts: dict[str, Any] = {"total": len(rows), "skipped_summary_rows": sum(1 for r in rows if r.skipped)}
    for status in ImportRowStatus:
        counts[status.value.lower()] = sum(1 for r in rows if r.status == status and not r.skipped)
    to_import = [r for r in rows if r.resolution == ImportRowResolution.IMPORT and r.amount is not None]
    inflow = sum((r.amount for r in to_import if r.amount and r.amount > 0), Decimal(0))
    outflow = sum((-r.amount for r in to_import if r.amount and r.amount < 0), Decimal(0))
    counts["to_import"] = len(to_import)
    counts["to_link"] = sum(1 for r in rows if r.resolution == ImportRowResolution.LINK)
    counts["to_skip"] = sum(1 for r in rows if r.resolution == ImportRowResolution.SKIP and not r.skipped)
    counts["totals"] = {
        rows[0].currency if rows else "INR": {
            "inflow": str(inflow),
            "outflow": str(outflow),
            "net": str(inflow - outflow),
        }
    }
    dates = [r.transaction_date for r in rows if r.transaction_date and not r.skipped]
    imp.date_min, imp.date_max = (min(dates), max(dates)) if dates else (None, None)
    imp.counts = counts
    imp.preview_hash = _preview_hash(imp, rows)
    imp.preview_version += 1


def set_resolutions(
    db: Session, owner_id: uuid.UUID, imp: Import, resolutions: dict[int, ImportRowResolution], actor: str
) -> Import:
    if imp.state != ImportState.PREVIEW_READY:
        raise ConflictError("Resolutions can only change while the preview is open.", code="IMPORT_STATE_INVALID")
    rows = list(db.scalars(select(ImportRow).where(ImportRow.import_id == imp.id).order_by(ImportRow.row_index)))
    by_index = {r.row_index: r for r in rows}
    for idx, res in resolutions.items():
        r = by_index.get(idx)
        if r is None:
            raise NotFoundError(f"Row {idx} not found.")
        if r.status == ImportRowStatus.INVALID and res != ImportRowResolution.SKIP:
            raise ValidationFailed("Invalid rows can only be skipped; fix the mapping instead.", code="ROW_INVALID")
        if res == ImportRowResolution.LINK and r.duplicate_of_transaction_id is None:
            raise ValidationFailed("Only duplicate candidates can be linked.", code="ROW_NOT_DUPLICATE")
        r.resolution = res
    _refresh_preview(imp, rows)
    audit(db, owner_id, actor, "import.resolve_rows", "import", imp.id, ["resolutions"])
    return imp


def confirm(
    db: Session, owner_id: uuid.UUID, imp: Import, preview_hash: str, accept_rejections: bool, actor: str
) -> uuid.UUID:
    if imp.state != ImportState.PREVIEW_READY:
        raise ConflictError("This import is not awaiting confirmation.", code="IMPORT_STATE_INVALID")
    if preview_hash != imp.preview_hash:
        raise ConflictError("Regenerate the preview before confirming.", code="IMPORT_PREVIEW_STALE")
    invalid = int(imp.counts.get("invalid", 0))
    if invalid and not accept_rejections:
        raise ValidationFailed(
            f"{plural(invalid, 'row')} could not be read. Confirm that they should be skipped, or fix the mapping.",
            code="IMPORT_HAS_INVALID_ROWS",
            details={"invalid": invalid},
        )
    imp.state = ImportState.CONFIRMED
    imp.confirmed_at = datetime.now(UTC)
    job_id = enqueue(db, owner_id, IMPORT_COMMIT_JOB, {"import_id": str(imp.id)}, f"import:{imp.id}")
    imp.job_id = job_id
    audit(db, owner_id, actor, "import.confirm", "import", imp.id, ["state"])
    return job_id


def cancel(db: Session, owner_id: uuid.UUID, imp: Import, actor: str) -> Import:
    if imp.state in (ImportState.COMMITTING, ImportState.COMPLETED):
        raise ConflictError(
            "Committed imports cannot be cancelled; merge or correct transactions instead.", code="IMPORT_STATE_INVALID"
        )
    imp.state = ImportState.CANCELLED
    audit(db, owner_id, actor, "import.cancel", "import", imp.id, ["state"])
    return imp


def _decisions_changed(db: Session, owner_id: uuid.UUID, imp: Import, rows: list[ImportRow]) -> bool:
    """Recheck duplicates against the ledger as it is now (another import may have committed)."""
    snapshot = {r.row_index: (r.status, r.duplicate_of_transaction_id) for r in rows}
    for r in rows:
        if (
            r.status in (ImportRowStatus.DUPLICATE, ImportRowStatus.POSSIBLE_DUPLICATE)
            and r.dedup_reason != "file_replay"
        ):
            r.status, r.duplicate_of_transaction_id, r.dedup_reason = ImportRowStatus.VALID, None, None
    _run_dedup(db, owner_id, imp, rows)
    changed = any(snapshot[r.row_index] != (r.status, r.duplicate_of_transaction_id) for r in rows)
    if not changed:
        return False
    for r in rows:
        if snapshot[r.row_index][0] != r.status and r.status == ImportRowStatus.DUPLICATE:
            r.resolution = ImportRowResolution.LINK
    return True


@handler(IMPORT_COMMIT_JOB)
def commit_import_job(ctx: AppContext, job: ClaimedJob, deadline: float) -> None:
    batch = ctx.settings.job_batch_size
    with lease_tx(ctx, job) as (db, row):
        imp = get_import(db, job.owner_id, uuid.UUID(row.payload["import_id"]), lock=True)
        if imp.state == ImportState.COMPLETED:
            finish(row, {"import_id": str(imp.id)})
            return
        if imp.state not in (ImportState.CONFIRMED, ImportState.COMMITTING):
            raise PermanentJobError("Import is not confirmed.", code="IMPORT_STATE_INVALID")
        # Serialize final source matching per account across concurrent imports.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"import-account:{imp.account_id}"})
        rows = list(db.scalars(select(ImportRow).where(ImportRow.import_id == imp.id).order_by(ImportRow.row_index)))
        if imp.state == ImportState.CONFIRMED:
            if _decisions_changed(db, job.owner_id, imp, rows):
                _refresh_preview(imp, rows)
                imp.state = ImportState.PREVIEW_READY
                imp.error_code = "DECISIONS_CHANGED"
                imp.error_message = "The ledger changed since the preview. Review the updated duplicate decisions."
                finish(row, {"import_id": str(imp.id), "returned_to_review": True})
                return
            imp.state = ImportState.COMMITTING
        account = db.get(Account, imp.account_id)
        assert account is not None
        cat = load_context(db, job.owner_id)
        pending = [
            r
            for r in rows
            if not r.committed and r.resolution in (ImportRowResolution.IMPORT, ImportRowResolution.LINK)
        ]
        processed = 0
        for r in pending:
            if processed >= batch or time.monotonic() > deadline - 2:
                break
            if r.resolution == ImportRowResolution.LINK:
                target = r.duplicate_of_transaction_id
                db.add(
                    TransactionSource(
                        owner_id=job.owner_id,
                        transaction_id=target,
                        account_id=account.id,
                        provider_namespace="import",
                        import_row_id=r.id,
                    )
                )
                r.transaction_id = target
            else:
                assert r.amount is not None and r.transaction_date is not None  # guaranteed by VALID status
                txn = create_transaction(
                    db,
                    job.owner_id,
                    NewTransaction(
                        account=account,
                        amount=r.amount,
                        transaction_date=r.transaction_date,
                        description=r.description,
                        source=TransactionSourceKind.IMPORT,
                        value_date=r.value_date,
                        reference=r.reference,
                        balance_after=r.balance_after,
                        source_drcr=r.source_drcr,
                        channel=PaymentChannel(r.channel),
                        import_id=imp.id,
                        is_published=False,
                        provider_namespace="import",
                        import_row_id=r.id,
                    ),
                    cat,
                    actor="worker",
                    bump=False,
                )
                r.transaction_id = txn.id
            r.committed = True
            processed += 1
        remaining = len(pending) - processed
        if remaining > 0:
            continue_later(row, {"committed": sum(1 for r in rows if r.committed)})
            return
        _publish(ctx, db, job.owner_id, imp, rows, account)
        finish(row, {"import_id": str(imp.id), "inserted": imp.counts.get("inserted", 0)})


def _publish(
    ctx: AppContext, db: Session, owner_id: uuid.UUID, imp: Import, rows: list[ImportRow], account: Account
) -> None:
    from .analytics_jobs import schedule_refresh
    from .transfers import run_matching

    db.execute(
        update(Transaction)
        .where(Transaction.import_id == imp.id, Transaction.owner_id == owner_id)
        .values(is_published=True)
    )
    inserted = sum(1 for r in rows if r.committed and r.resolution == ImportRowResolution.IMPORT)
    linked = sum(1 for r in rows if r.committed and r.resolution == ImportRowResolution.LINK)
    for r in rows:
        if (
            r.committed
            and r.status == ImportRowStatus.POSSIBLE_DUPLICATE
            and r.resolution == ImportRowResolution.IMPORT
        ):
            open_review(
                db,
                owner_id,
                ReviewKind.POSSIBLE_DUPLICATE,
                f"dup:{r.transaction_id}",
                [t for t in (r.transaction_id, r.duplicate_of_transaction_id) if t is not None],
                summary="Possible duplicate: same account, date, amount and description as an existing "
                "transaction. Merge it, or keep both if both are genuine.",
                evidence={"reason": r.dedup_reason or ""},
            )
    # Statement closing balance becomes an observed balance snapshot for asset accounts.
    with_balance = [
        r
        for r in rows
        if r.balance_after is not None and r.transaction_date and not r.skipped and r.status != ImportRowStatus.INVALID
    ]
    if with_balance and account.kind.nature == AccountNature.ASSET:
        last = max(with_balance, key=lambda r: (r.transaction_date, r.row_index))
        db.add(
            BalanceSnapshot(
                owner_id=owner_id,
                account_id=account.id,
                amount=last.balance_after,
                currency=account.currency,
                balance_kind=BalanceKind.CURRENT,
                as_of=last.transaction_date,
                source=BalanceSource.IMPORT,
                is_observed=True,
                source_ref=f"import:{imp.id}",
            )
        )
    imp.counts = {
        **imp.counts,
        "inserted": inserted,
        "linked": linked,
        "rejected": sum(1 for r in rows if r.status == ImportRowStatus.INVALID),
        "review_required": sum(
            1
            for r in rows
            if r.status == ImportRowStatus.POSSIBLE_DUPLICATE and r.resolution == ImportRowResolution.IMPORT
        ),
    }
    imp.state = ImportState.COMPLETED
    imp.completed_at = datetime.now(UTC)
    imp.error_code = imp.error_message = None
    db.flush()
    bump_ledger_revision(db, owner_id)
    if imp.date_min and imp.date_max:
        cat = load_context(db, owner_id)
        stats = run_matching(
            db, owner_id, cat, start=imp.date_min, end_exclusive=imp.date_max + timedelta(days=1), actor="worker"
        )
        from .products import match_loan_payments

        loans = match_loan_payments(
            db, owner_id, cat, actor="worker", start=imp.date_min, end_exclusive=imp.date_max + timedelta(days=1)
        )
        from .cash import match_cash_withdrawals

        cash = match_cash_withdrawals(
            db, owner_id, cat, actor="worker", start=imp.date_min, end_exclusive=imp.date_max + timedelta(days=1)
        )
        imp.counts = {
            **imp.counts,
            "cash_withdrawals_linked": cash,
            "transfers_auto_confirmed": stats["auto_confirmed"],
            "transfers_suggested": stats["suggested"],
            "loan_payments_recorded": loans["recorded"],
            "loan_payments_suggested": loans["suggested"],
        }
    from .ai import schedule_auto_categorize

    schedule_auto_categorize(ctx, db, owner_id, f"import:{imp.id}")
    schedule_refresh(db, owner_id)
    audit(db, owner_id, "worker", "import.complete", "import", imp.id, ["state"])


def import_failed(db: Session, row: Any, code: str) -> None:
    imp = db.scalars(select(Import).where(Import.id == uuid.UUID(row.payload["import_id"]))).first()
    if imp is not None and imp.state != ImportState.COMPLETED:
        imp.state = ImportState.FAILED
        imp.error_code, imp.error_stage = code[:60], "COMMITTING"
        imp.error_message = "The import could not be committed. Nothing was published; retry or re-upload."


FAILURE_HOOKS[IMPORT_COMMIT_JOB] = import_failed


def retry(db: Session, owner_id: uuid.UUID, imp: Import, actor: str) -> uuid.UUID:
    if imp.state != ImportState.FAILED or imp.error_stage != "COMMITTING":
        raise ConflictError("Only failed commits can be retried.", code="IMPORT_STATE_INVALID")
    imp.state = ImportState.COMMITTING
    imp.error_code = imp.error_stage = imp.error_message = None
    job_id = enqueue(
        db, owner_id, IMPORT_COMMIT_JOB, {"import_id": str(imp.id)}, f"import:{imp.id}:retry:{imp.version}"
    )
    imp.version += 1
    imp.job_id = job_id
    audit(db, owner_id, actor, "import.retry", "import", imp.id, ["state"])
    return job_id


def _formula_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection in exported cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def error_report_csv(db: Session, imp: Import) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source_row", "status", "errors", "description"])
    for r in db.scalars(
        select(ImportRow)
        .where(
            ImportRow.import_id == imp.id,
            ImportRow.status.in_([ImportRowStatus.INVALID, ImportRowStatus.POSSIBLE_DUPLICATE]),
        )
        .order_by(ImportRow.row_index)
    ):
        w.writerow([r.source_row_number, r.status.value, ";".join(r.errors), _formula_safe(r.description or "")])
    return buf.getvalue()


def purge_expired_files(db: Session, owner_id: uuid.UUID) -> int:
    now = datetime.now(UTC)
    files = db.scalars(
        select(ImportFile).where(
            ImportFile.owner_id == owner_id, ImportFile.expires_at <= now, ImportFile.purged_at.is_(None)
        )
    ).all()
    for f in files:
        f.content_enc = None
        f.purged_at = now
    return len(files)


def row_count(db: Session, imp: Import) -> int:
    return int(db.scalar(select(func.count()).select_from(ImportRow).where(ImportRow.import_id == imp.id)) or 0)


def delete_preview(db: Session, owner_id: uuid.UUID, imp: Import) -> dict[str, Any]:
    """What deleting this import would remove (shown before the owner confirms)."""
    added = int(
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.owner_id == owner_id, Transaction.import_id == imp.id, Transaction.deleted_at.is_(None))
        )
        or 0
    )
    linked = int(
        db.scalar(
            select(func.count())
            .select_from(ImportRow)
            .where(
                ImportRow.import_id == imp.id,
                ImportRow.committed.is_(True),
                ImportRow.resolution == ImportRowResolution.LINK,
            )
        )
        or 0
    )
    return {"transactions_removed": added, "shared_transactions_kept": linked, "state": imp.state.value}


def delete_import(ctx: AppContext, db: Session, owner_id: uuid.UUID, imp: Import, actor: str) -> dict[str, Any]:
    """Remove an import and every transaction it added; erase the uploaded file.

    Transactions are soft-deleted (hidden from the ledger, reports and exports) with an audit record.
    Transactions that already existed and were only *linked* by this file are kept. Transfers,
    loan payments, review items and the statement balance that depended on the removed rows are
    undone so nothing dangling keeps suppressing or inventing money.
    """
    from oneledger_db.models import (
        Anomaly,
        InvestmentTransaction,
        LoanPayment,
        RecurringTransactionMember,
        ReviewItem,
        TransactionAllocation,
        TransactionRelation,
        Transfer,
        TransferLeg,
    )
    from oneledger_domain.enums import RelationKind, ReviewStatus, TransferStatus

    from .categories import load_context
    from .transfers import _restore_previous, run_matching

    if imp.state in (ImportState.CONFIRMED, ImportState.COMMITTING):
        raise ConflictError(
            "This import is still being saved. Try again when it has finished.", code="IMPORT_IN_PROGRESS"
        )
    if imp.state == ImportState.DELETED:
        return {"transactions_removed": 0}
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"import-account:{imp.account_id}"})
    now = datetime.now(UTC)
    txns = list(
        db.scalars(
            select(Transaction)
            .where(Transaction.owner_id == owner_id, Transaction.import_id == imp.id, Transaction.deleted_at.is_(None))
            .with_for_update()
        )
    )
    ids = [t.id for t in txns]
    derived_removed = 0
    if ids:
        cat = load_context(db, owner_id)
        # Undo merges where a removed transaction was the survivor: the merged duplicate comes back.
        for rel in db.scalars(
            select(TransactionRelation).where(
                TransactionRelation.kind == RelationKind.MERGE,
                TransactionRelation.undone_at.is_(None),
                TransactionRelation.to_transaction_id.in_(ids),
            )
        ):
            loser = db.get(Transaction, rel.from_transaction_id)
            if loser is not None:
                loser.merged_into_id = None
                loser.version += 1
            rel.undone_at = now
        # Loan payments made by removed debits: remove them and their derived loan-account movements.
        derived_ids: set[uuid.UUID] = set()
        for pay in db.scalars(
            select(LoanPayment).where(LoanPayment.transaction_id.in_(ids), LoanPayment.deleted_at.is_(None))
        ):
            pay.deleted_at = now
        # Transfers touching removed rows are voided; the surviving side is re-categorised.
        transfers = list(
            db.scalars(
                select(Transfer)
                .join(TransferLeg, TransferLeg.transfer_id == Transfer.id)
                .where(TransferLeg.transaction_id.in_(ids), Transfer.status != TransferStatus.REJECTED)
            ).unique()
        )
        for tr in transfers:
            legs = list(db.scalars(select(TransferLeg).where(TransferLeg.transfer_id == tr.id)))
            was_confirmed = tr.status == TransferStatus.CONFIRMED
            for leg in legs:
                leg.is_active = False
                other = db.get(Transaction, leg.transaction_id)
                if (
                    other is not None
                    and other.id not in ids
                    and other.source.value == "MANUAL_DERIVED"
                    and tr.reason in ("loan_payment", "cash_withdrawal", "shared_expense", "settlement")
                ):
                    derived_ids.add(other.id)
            tr.status = TransferStatus.REJECTED
            tr.decided_by = "system"
            tr.reason = "import_deleted"
            db.flush()
            if was_confirmed:
                _restore_previous(db, owner_id, tr, cat)
        for t in [*txns, *(db.get(Transaction, d) for d in derived_ids)]:
            if t is None:
                continue
            t.deleted_at = now
            t.version += 1
        derived_removed = len(derived_ids)
        all_ids = ids + list(derived_ids)
        db.execute(
            update(TransactionSource).where(TransactionSource.transaction_id.in_(all_ids)).values(is_active=False)
        )
        db.execute(
            update(InvestmentTransaction)
            .where(InvestmentTransaction.transaction_id.in_(all_ids))
            .values(transaction_id=None)
        )
        from sqlalchemy import delete as sa_delete

        db.execute(sa_delete(RecurringTransactionMember).where(RecurringTransactionMember.transaction_id.in_(all_ids)))
        db.execute(sa_delete(Anomaly).where(Anomaly.transaction_id.in_(all_ids)))
        db.execute(
            update(TransactionAllocation)
            .where(TransactionAllocation.related_transaction_id.in_(all_ids))
            .values(related_transaction_id=None)
        )
        for item in db.scalars(
            select(ReviewItem).where(
                ReviewItem.owner_id == owner_id,
                ReviewItem.status == ReviewStatus.OPEN,
                ReviewItem.transaction_ids.overlap(all_ids),
            )
        ):
            item.status, item.resolution, item.resolved_at = ReviewStatus.RESOLVED, "import_deleted", now
    # Shared transactions this file only linked to stay; the link to the deleted file is dropped.
    row_ids = list(db.scalars(select(ImportRow.id).where(ImportRow.import_id == imp.id)))
    if row_ids:
        db.execute(
            update(TransactionSource).where(TransactionSource.import_row_id.in_(row_ids)).values(is_active=False)
        )
    db.execute(
        update(BalanceSnapshot)
        .where(
            BalanceSnapshot.owner_id == owner_id,
            BalanceSnapshot.source_ref == f"import:{imp.id}",
            BalanceSnapshot.deleted_at.is_(None),
        )
        .values(deleted_at=now)
    )
    f = db.get(ImportFile, imp.file_id)
    if f is not None and f.content_enc is not None:
        f.content_enc, f.purged_at = None, now
    was_completed = imp.state == ImportState.COMPLETED
    imp.state = ImportState.DELETED
    imp.error_code = imp.error_stage = imp.error_message = None
    imp.version += 1
    db.flush()
    if ids:
        bump_ledger_revision(db, owner_id)
        if was_completed and imp.date_min and imp.date_max:
            # Surviving legs of voided transfers may now match something else.
            run_matching(
                db,
                owner_id,
                load_context(db, owner_id),
                start=imp.date_min,
                end_exclusive=imp.date_max + timedelta(days=1),
                actor="system",
            )
        from .analytics_jobs import schedule_refresh

        schedule_refresh(db, owner_id)
    audit(db, owner_id, actor, "import.delete", "import", imp.id, ["state", "transactions"])
    return {"transactions_removed": len(ids), "derived_removed": derived_removed}
