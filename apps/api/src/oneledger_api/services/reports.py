"""Financial reports with provenance.

Every aggregate uses the reporting definitions in ``oneledger_domain.reporting`` over *active*
rows only (posted, published, not deleted/merged). Every response carries a provenance block
with an owner-scoped ``query_id`` that reproduces the evidence transaction list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    BalanceSnapshot,
    Investment,
    InvestmentValuation,
    ReportQuery,
    ReviewItem,
    Transaction,
    TransactionAllocation,
    TransactionCategory,
)
from oneledger_domain import networth as nw
from oneledger_domain.enums import (
    AccountKind,
    AccountNature,
    AccountStatus,
    AllocationEffect,
    BalanceKind,
    ReviewStatus,
    TransactionStatus,
)
from oneledger_domain.periods import DateRange, add_months, months_between
from oneledger_domain.reporting import CALCULATION_VERSION, AllocationFact, Summary, summarize
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from .audit import current_ledger_revision
from .review import open_counts


def active_txn_filter() -> ColumnElement[bool]:
    return and_(
        Transaction.status == TransactionStatus.POSTED,
        Transaction.deleted_at.is_(None),
        Transaction.merged_into_id.is_(None),
        Transaction.is_published.is_(True),
    )


@dataclass
class Provenance:
    query_id: uuid.UUID
    start_date: date | None
    end_date_exclusive: date | None
    timezone: str
    account_ids: list[uuid.UUID]
    transaction_count: int
    as_of: datetime
    calculation_version: str
    ledger_revision: int
    partial: bool
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": str(self.query_id),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date_exclusive": self.end_date_exclusive.isoformat() if self.end_date_exclusive else None,
            "timezone": self.timezone,
            "account_ids": [str(a) for a in self.account_ids],
            "transaction_count": self.transaction_count,
            "as_of": self.as_of.isoformat(),
            "calculation_version": self.calculation_version,
            "ledger_revision": self.ledger_revision,
            "partial": self.partial,
            "warnings": self.warnings,
            # Evidence links only make sense for period reports built from transactions.
            "evidence_url": f"/transactions?query_id={self.query_id}" if self.start_date else None,
        }


def _accounts(db: Session, owner_id: uuid.UUID, account_ids: list[uuid.UUID] | None) -> list[Account]:
    stmt = select(Account).where(Account.owner_id == owner_id, Account.deleted_at.is_(None))
    if account_ids:
        stmt = stmt.where(Account.id.in_(account_ids))
    return list(db.scalars(stmt.order_by(Account.name)))


def record_query(
    db: Session, owner_id: uuid.UUID, kind: str, params: dict[str, Any], calc: str
) -> tuple[uuid.UUID, int]:
    rev = current_ledger_revision(db, owner_id)
    q = ReportQuery(owner_id=owner_id, kind=kind, params=params, ledger_revision=rev, calculation_version=calc)
    db.add(q)
    db.flush()
    return q.id, rev


def load_query(db: Session, owner_id: uuid.UUID, query_id: uuid.UUID) -> ReportQuery | None:
    q = db.get(ReportQuery, query_id)
    return q if q is not None and q.owner_id == owner_id else None


def _coverage_warnings(
    db: Session, owner_id: uuid.UUID, rng: DateRange | None, accounts: list[Account], summary: Summary | None
) -> tuple[list[str], bool]:
    warnings: list[str] = []
    partial = False
    if summary is not None and summary.unclassified_count:
        warnings.append(f"{summary.unclassified_count} transaction(s) are unclassified; totals are provisional.")
        partial = True
    pending = (
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.owner_id == owner_id,
                Transaction.status == TransactionStatus.PENDING,
                Transaction.deleted_at.is_(None),
                *([Transaction.account_id.in_([a.id for a in accounts])] if accounts else []),
                *(
                    [Transaction.transaction_date >= rng.start, Transaction.transaction_date < rng.end_exclusive]
                    if rng
                    else []
                ),
            )
        )
        or 0
    )
    if pending:
        warnings.append(f"{pending} pending transaction(s) are excluded until posted.")
    reviews = open_counts(db, owner_id)
    if reviews:
        n = sum(reviews.values())
        warnings.append(f"{n} item(s) need review (duplicates/transfers) and may change these totals.")
        if reviews.get("TRANSFER_SUGGESTION") or reviews.get("POSSIBLE_DUPLICATE"):
            partial = True
    if rng is not None and accounts:
        latest = db.scalar(
            select(func.max(Transaction.transaction_date)).where(
                Transaction.owner_id == owner_id,
                Transaction.account_id.in_([a.id for a in accounts]),
                active_txn_filter(),
            )
        )
        if latest is None:
            warnings.append("No transactions exist for the selected accounts.")
            partial = True
        else:
            horizon = min(rng.end_exclusive - timedelta(days=1), datetime.now(UTC).date())
            if latest < horizon - timedelta(days=3):
                warnings.append(f"Latest transaction data is from {latest.isoformat()}; later activity may be missing.")
    return warnings, partial


def allocation_facts(
    db: Session, owner_id: uuid.UUID, rng: DateRange, account_ids: list[uuid.UUID] | None, currency: str | None = None
) -> tuple[list[AllocationFact], int]:
    stmt = (
        select(TransactionAllocation.effect, TransactionAllocation.amount, Transaction.id)
        .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.transaction_date >= rng.start,
            Transaction.transaction_date < rng.end_exclusive,
        )
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    if currency:
        stmt = stmt.where(Transaction.currency == currency)
    rows = db.execute(stmt).all()
    return [AllocationFact(e, a) for e, a, _ in rows], len({t for _, _, t in rows})


def summary_report(
    db: Session, owner_id: uuid.UUID, tz: str, currency: str, rng: DateRange, account_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    accounts = _accounts(db, owner_id, account_ids)
    facts, count = allocation_facts(db, owner_id, rng, [a.id for a in accounts] if account_ids else None, currency)
    s = summarize(facts)
    warnings, partial = _coverage_warnings(db, owner_id, rng, accounts, s)
    qid, rev = record_query(
        db,
        owner_id,
        "summary",
        {
            "start": rng.start.isoformat(),
            "end": rng.end_exclusive.isoformat(),
            "account_ids": [str(a) for a in account_ids or []],
            "currency": currency,
        },
        CALCULATION_VERSION,
    )
    prov = Provenance(
        qid,
        rng.start,
        rng.end_exclusive,
        tz,
        [a.id for a in accounts],
        count,
        datetime.now(UTC),
        CALCULATION_VERSION,
        rev,
        partial,
        warnings,
    )
    return {
        "data": {
            "currency": currency,
            "income": str(s.income),
            "net_expenses": str(s.net_expenses),
            "gross_expenses": str(s.gross_expenses),
            "refunds": str(s.refunds),
            "unclassified_outflow": str(s.unclassified_outflow),
            "unclassified_inflow": str(s.unclassified_inflow),
            "unclassified_count": s.unclassified_count,
            "investment_contributions": str(s.investment_contributions),
            "investment_withdrawals": str(s.investment_withdrawals),
            "loan_principal_paid": str(s.loan_principal_paid),
            "internal_transfers_out": str(s.transfers_out),
            "internal_transfers_in": str(s.transfers_in),
            "savings": str(s.savings),
            "savings_provisional": s.provisional,
        },
        "provenance": prov.as_dict(),
    }


def category_breakdown(
    db: Session,
    owner_id: uuid.UUID,
    tz: str,
    currency: str,
    rng: DateRange,
    effect: AllocationEffect = AllocationEffect.EXPENSE,
    account_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    stmt = (
        select(
            TransactionAllocation.category_id,
            func.sum(TransactionAllocation.amount),
            func.count(func.distinct(Transaction.id)),
        )
        .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.currency == currency,
            Transaction.transaction_date >= rng.start,
            Transaction.transaction_date < rng.end_exclusive,
            TransactionAllocation.effect == effect,
        )
        .group_by(TransactionAllocation.category_id)
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    rows = db.execute(stmt).all()
    cats = {c.id: c for c in db.scalars(select(TransactionCategory).where(TransactionCategory.owner_id == owner_id))}
    sign = Decimal(-1) if effect == AllocationEffect.EXPENSE else Decimal(1)

    def root(cid: uuid.UUID | None) -> TransactionCategory | None:
        cur = cats.get(cid) if cid else None
        while cur is not None and cur.parent_id is not None:
            cur = cats.get(cur.parent_id)
        return cur

    groups: dict[str, dict[str, Any]] = {}
    total = Decimal(0)
    txn_count = 0
    for cid, amount, n in rows:
        value = sign * Decimal(amount)
        total += value
        txn_count += int(n)
        r = root(cid)
        key = str(r.id) if r else "uncategorized"
        g = groups.setdefault(
            key,
            {
                "category_id": str(r.id) if r else None,
                "code": r.code if r else None,
                "name": r.name if r else "Uncategorized",
                "amount": Decimal(0),
                "count": 0,
                "subcategories": [],
            },
        )
        g["amount"] += value
        g["count"] += int(n)
        c = cats.get(cid) if cid else None
        if c is not None and c.parent_id is not None:
            g["subcategories"].append(
                {"category_id": str(c.id), "code": c.code, "name": c.name, "amount": str(value), "count": int(n)}
            )
    items = sorted(groups.values(), key=lambda g: -g["amount"])
    for g in items:
        g["amount"] = str(g["amount"])
        g["share"] = str((Decimal(g["amount"]) / total * 100).quantize(Decimal("0.1"))) if total else "0"
        g["subcategories"].sort(key=lambda s: -Decimal(s["amount"]))
    accounts = _accounts(db, owner_id, account_ids)
    warnings, partial = _coverage_warnings(db, owner_id, rng, accounts, None)
    qid, rev = record_query(
        db,
        owner_id,
        "category_breakdown",
        {
            "start": rng.start.isoformat(),
            "end": rng.end_exclusive.isoformat(),
            "effect": effect.value,
            "account_ids": [str(a) for a in account_ids or []],
            "currency": currency,
        },
        CALCULATION_VERSION,
    )
    return {
        "data": {"currency": currency, "effect": effect.value, "total": str(total), "categories": items},
        "provenance": Provenance(
            qid,
            rng.start,
            rng.end_exclusive,
            tz,
            [a.id for a in accounts],
            txn_count,
            datetime.now(UTC),
            CALCULATION_VERSION,
            rev,
            partial,
            warnings,
        ).as_dict(),
    }


def monthly_series(
    db: Session,
    owner_id: uuid.UUID,
    tz: str,
    currency: str,
    end_month_start: date,
    months: int,
    account_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    start = add_months(end_month_start, -(months - 1))
    rng = DateRange(start, add_months(end_month_start, 1))
    stmt = (
        select(
            func.date_trunc("month", Transaction.transaction_date).label("m"),
            TransactionAllocation.effect,
            func.sum(TransactionAllocation.amount).filter(TransactionAllocation.amount < 0),
            func.sum(TransactionAllocation.amount).filter(TransactionAllocation.amount > 0),
            func.count(func.distinct(Transaction.id)),
        )
        .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.currency == currency,
            Transaction.transaction_date >= rng.start,
            Transaction.transaction_date < rng.end_exclusive,
        )
        .group_by("m", TransactionAllocation.effect)
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    series: dict[str, dict[str, Decimal]] = {
        m.start.strftime("%Y-%m"): {
            "income": Decimal(0),
            "net_expenses": Decimal(0),
            "unclassified_outflow": Decimal(0),
            "unclassified_inflow": Decimal(0),
            "investments": Decimal(0),
        }
        for m in months_between(rng)
    }
    count = 0
    for m, effect, neg, pos, n in db.execute(stmt).all():
        key = m.strftime("%Y-%m")
        neg, pos = Decimal(neg or 0), Decimal(pos or 0)
        count += int(n)
        if effect == AllocationEffect.INCOME:
            series[key]["income"] += neg + pos
        elif effect == AllocationEffect.EXPENSE:
            series[key]["net_expenses"] += -(neg + pos)
        elif effect == AllocationEffect.UNCLASSIFIED:
            series[key]["unclassified_outflow"] += -neg
            series[key]["unclassified_inflow"] += pos
        elif effect == AllocationEffect.INVESTMENT:
            series[key]["investments"] += -(neg + pos)
    qid, rev = record_query(
        db,
        owner_id,
        "monthly_series",
        {
            "start": rng.start.isoformat(),
            "end": rng.end_exclusive.isoformat(),
            "account_ids": [str(a) for a in account_ids or []],
            "currency": currency,
        },
        CALCULATION_VERSION,
    )
    accounts = _accounts(db, owner_id, account_ids)
    warnings, partial = _coverage_warnings(db, owner_id, None, accounts, None)
    return {
        "data": {
            "currency": currency,
            "months": [
                {"month": k, **{kk: str(vv) for kk, vv in v.items()}, "savings": str(v["income"] - v["net_expenses"])}
                for k, v in series.items()
            ],
        },
        "provenance": Provenance(
            qid,
            rng.start,
            rng.end_exclusive,
            tz,
            [a.id for a in accounts],
            count,
            datetime.now(UTC),
            CALCULATION_VERSION,
            rev,
            partial,
            warnings,
        ).as_dict(),
    }


def compare_periods(
    db: Session, owner_id: uuid.UUID, tz: str, currency: str, current: DateRange, previous: DateRange
) -> dict[str, Any]:
    cur = category_breakdown(db, owner_id, tz, currency, current)
    prev = category_breakdown(db, owner_id, tz, currency, previous)
    cur_s = summary_report(db, owner_id, tz, currency, current)
    prev_s = summary_report(db, owner_id, tz, currency, previous)
    prev_by = {c["code"]: Decimal(c["amount"]) for c in prev["data"]["categories"]}
    rows = []
    for c in cur["data"]["categories"]:
        before = prev_by.pop(c["code"], Decimal(0))
        rows.append(
            {
                "code": c["code"],
                "name": c["name"],
                "current": c["amount"],
                "previous": str(before),
                "change": str(Decimal(c["amount"]) - before),
            }
        )
    for code, before in prev_by.items():
        name = next((p["name"] for p in prev["data"]["categories"] if p["code"] == code), code)
        rows.append({"code": code, "name": name, "current": "0", "previous": str(before), "change": str(-before)})
    rows.sort(key=lambda r: -abs(Decimal(r["change"])))
    return {
        "data": {
            "currency": currency,
            "current": {
                "start": current.start.isoformat(),
                "end_exclusive": current.end_exclusive.isoformat(),
                **{k: cur_s["data"][k] for k in ("income", "net_expenses", "unclassified_outflow")},
            },
            "previous": {
                "start": previous.start.isoformat(),
                "end_exclusive": previous.end_exclusive.isoformat(),
                **{k: prev_s["data"][k] for k in ("income", "net_expenses", "unclassified_outflow")},
            },
            "net_expense_change": str(Decimal(cur_s["data"]["net_expenses"]) - Decimal(prev_s["data"]["net_expenses"])),
            "categories": rows,
        },
        "provenance": cur_s["provenance"] | {"compared_query_id": prev_s["provenance"]["query_id"]},
    }


def cash_flow(
    db: Session, owner_id: uuid.UUID, tz: str, currency: str, rng: DateRange, account_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    accounts = [a for a in _accounts(db, owner_id, account_ids) if a.kind.is_liquid and a.currency == currency]
    ids = [a.id for a in accounts]
    rows = (
        db.execute(
            select(
                Transaction.account_id,
                TransactionAllocation.effect,
                func.sum(TransactionAllocation.amount).filter(TransactionAllocation.amount > 0),
                func.sum(TransactionAllocation.amount).filter(TransactionAllocation.amount < 0),
                func.count(func.distinct(Transaction.id)),
            )
            .join(Transaction, Transaction.id == TransactionAllocation.transaction_id)
            .where(
                Transaction.owner_id == owner_id,
                active_txn_filter(),
                Transaction.account_id.in_(ids),
                Transaction.transaction_date >= rng.start,
                Transaction.transaction_date < rng.end_exclusive,
            )
            .group_by(Transaction.account_id, TransactionAllocation.effect)
        ).all()
        if ids
        else []
    )
    per: dict[uuid.UUID, dict[str, Decimal]] = {
        a: {"inflow": Decimal(0), "outflow": Decimal(0), "internal_in": Decimal(0), "internal_out": Decimal(0)}
        for a in ids
    }
    count = 0
    for acct, effect, pos, neg, n in rows:
        pos, neg = Decimal(pos or 0), Decimal(neg or 0)
        count += int(n)
        per[acct]["inflow"] += pos
        per[acct]["outflow"] += -neg
        if effect in (AllocationEffect.TRANSFER, AllocationEffect.LOAN_PRINCIPAL):
            per[acct]["internal_in"] += pos
            per[acct]["internal_out"] += -neg
    gross_in = sum((v["inflow"] for v in per.values()), Decimal(0))
    gross_out = sum((v["outflow"] for v in per.values()), Decimal(0))
    int_in = sum((v["internal_in"] for v in per.values()), Decimal(0))
    int_out = sum((v["internal_out"] for v in per.values()), Decimal(0))
    names = {a.id: a.name for a in accounts}
    qid, rev = record_query(
        db,
        owner_id,
        "cash_flow",
        {
            "start": rng.start.isoformat(),
            "end": rng.end_exclusive.isoformat(),
            "account_ids": [str(a) for a in ids],
            "currency": currency,
        },
        CALCULATION_VERSION,
    )
    warnings, partial = _coverage_warnings(db, owner_id, rng, accounts, None)
    warnings.append("Transfers and card/loan payments are internal movements; they appear in gross flow only.")
    return {
        "data": {
            "currency": currency,
            "gross_inflow": str(gross_in),
            "gross_outflow": str(gross_out),
            "gross_net": str(gross_in - gross_out),
            "external_inflow": str(gross_in - int_in),
            "external_outflow": str(gross_out - int_out),
            "external_net": str((gross_in - int_in) - (gross_out - int_out)),
            "accounts": [
                {"account_id": str(a), "name": names[a], **{k: str(v) for k, v in vals.items()}}
                for a, vals in per.items()
            ],
        },
        "provenance": Provenance(
            qid,
            rng.start,
            rng.end_exclusive,
            tz,
            ids,
            count,
            datetime.now(UTC),
            CALCULATION_VERSION,
            rev,
            partial,
            warnings,
        ).as_dict(),
    }


# --- Balances ----------------------------------------------------------------------------------

STALE_DAYS = 35


@dataclass
class AccountBalance:
    account: Account
    balance: Decimal | None
    observed_as_of: date | None
    derived_through: date | None
    transactions_after_snapshot: int
    snapshot_id: uuid.UUID | None
    stale: bool


def account_balances(
    db: Session, owner_id: uuid.UUID, as_of: date, accounts: list[Account] | None = None
) -> list[AccountBalance]:
    """Latest snapshot at/before ``as_of`` plus posted movements after it (never inferred from nothing)."""
    accounts = accounts if accounts is not None else _accounts(db, owner_id, None)
    out: list[AccountBalance] = []
    for a in accounts:
        kinds = (
            [BalanceKind.CURRENT, BalanceKind.OPENING, BalanceKind.STATEMENT]
            if a.kind.nature.value == "LIABILITY"
            else [BalanceKind.CURRENT, BalanceKind.OPENING]
        )
        snap = db.scalars(
            select(BalanceSnapshot)
            .where(
                BalanceSnapshot.owner_id == owner_id,
                BalanceSnapshot.account_id == a.id,
                BalanceSnapshot.as_of <= as_of,
                BalanceSnapshot.deleted_at.is_(None),
                BalanceSnapshot.balance_kind.in_(kinds),
            )
            .order_by(BalanceSnapshot.as_of.desc(), BalanceSnapshot.created_at.desc())
        ).first()
        if snap is None:
            out.append(AccountBalance(a, None, None, None, 0, None, False))
            continue
        total, n, last = db.execute(
            select(
                func.coalesce(func.sum(Transaction.amount), 0), func.count(), func.max(Transaction.transaction_date)
            ).where(
                Transaction.owner_id == owner_id,
                Transaction.account_id == a.id,
                active_txn_filter(),
                Transaction.transaction_date > snap.as_of,
                Transaction.transaction_date <= as_of,
            )
        ).one()
        balance = nw.expected_closing(a.kind.nature, snap.amount, [Decimal(total)])
        through = max(snap.as_of, last) if last else snap.as_of
        out.append(
            AccountBalance(a, balance, snap.as_of, through, int(n), snap.id, (as_of - through).days > STALE_DAYS)
        )
    return out


def balances_report(
    db: Session, owner_id: uuid.UUID, tz: str, as_of: date, kinds: set[AccountKind] | None = None
) -> dict[str, Any]:
    accounts = [
        a for a in _accounts(db, owner_id, None) if a.status == AccountStatus.ACTIVE and (not kinds or a.kind in kinds)
    ]
    bals = account_balances(db, owner_id, as_of, accounts)
    totals: dict[str, dict[str, Decimal]] = {}
    unknown = stale = 0
    items = []
    for b in bals:
        items.append(
            {
                "account_id": str(b.account.id),
                "name": b.account.name,
                "kind": b.account.kind.value,
                "nature": b.account.kind.nature.value,
                "currency": b.account.currency,
                "masked_identifier": b.account.masked_identifier,
                "balance": str(b.balance) if b.balance is not None else None,
                "observed_as_of": b.observed_as_of.isoformat() if b.observed_as_of else None,
                "derived_through": b.derived_through.isoformat() if b.derived_through else None,
                "transactions_after_snapshot": b.transactions_after_snapshot,
                "stale": b.stale,
                "status": "unknown" if b.balance is None else ("stale" if b.stale else "ok"),
            }
        )
        if b.balance is None:
            unknown += 1
            continue
        stale += int(b.stale)
        t = totals.setdefault(b.account.currency, {"assets": Decimal(0), "liabilities": Decimal(0)})
        t["assets" if b.account.kind.nature.value == "ASSET" else "liabilities"] += b.balance
    warnings = []
    if unknown:
        warnings.append(
            f"{unknown} account(s) have no known balance; add an opening balance or import a statement "
            "with a balance column."
        )
    if stale:
        warnings.append(f"{stale} account balance(s) are older than {STALE_DAYS} days.")
    qid, rev = record_query(db, owner_id, "balances", {"as_of": as_of.isoformat()}, "balances-v1")
    return {
        "data": {
            "as_of": as_of.isoformat(),
            "accounts": items,
            "totals": {c: {k: str(v) for k, v in t.items()} for c, t in totals.items()},
            "unknown_count": unknown,
            "stale_count": stale,
        },
        "provenance": Provenance(
            qid,
            None,
            as_of + timedelta(days=1),
            tz,
            [a.id for a in accounts],
            0,
            datetime.now(UTC),
            "balances-v1",
            rev,
            bool(unknown),
            warnings,
        ).as_dict(),
    }


# --- Net worth ---------------------------------------------------------------------------------


def _investment_components(db: Session, owner_id: uuid.UUID, cutoff: date) -> tuple[list[nw.Component], set[uuid.UUID]]:
    comps: list[nw.Component] = []
    covered_accounts: set[uuid.UUID] = set()
    for inv in db.scalars(
        select(Investment).where(
            Investment.owner_id == owner_id, Investment.deleted_at.is_(None), Investment.include_in_net_worth.is_(True)
        )
    ):
        v = db.scalars(
            select(InvestmentValuation)
            .where(
                InvestmentValuation.investment_id == inv.id,
                InvestmentValuation.valuation_date <= cutoff,
                InvestmentValuation.deleted_at.is_(None),
            )
            .order_by(InvestmentValuation.valuation_date.desc(), InvestmentValuation.created_at.desc())
        ).first()
        obs = nw.Observation(v.total_value, v.valuation_date, f"valuation:{v.id}") if v else None
        comps.append(
            nw.Component(
                f"investment:{inv.id}", inv.name, AccountNature.ASSET, inv.currency, inv.instrument_type.value, obs
            )
        )
        if inv.account_id is not None:
            covered_accounts.add(inv.account_id)
    return comps, covered_accounts


def net_worth_at(db: Session, owner_id: uuid.UUID, cutoff: date) -> tuple[nw.NetWorth, list[nw.Component]]:
    inv_comps, covered = _investment_components(db, owner_id, cutoff)
    accounts = [
        a
        for a in _accounts(db, owner_id, None)
        if a.include_in_net_worth
        and a.status == AccountStatus.ACTIVE
        and (a.opening_date is None or a.opening_date <= cutoff)
    ]
    comps: list[nw.Component] = []
    for b in account_balances(db, owner_id, cutoff, accounts):
        a = b.account
        # One canonical valuation owner: holdings accounts valued through their investments are not double counted.
        if a.id in covered and a.kind in (AccountKind.INVESTMENT, AccountKind.FIXED_DEPOSIT):
            continue
        obs = (
            nw.Observation(b.balance, b.derived_through or cutoff, f"balance:{b.snapshot_id}")
            if b.balance is not None
            else None
        )
        comps.append(nw.Component(f"account:{a.id}", a.name, a.kind.nature, a.currency, a.kind.value, obs))
    comps += inv_comps
    return nw.compute_net_worth(comps, cutoff), comps


def net_worth_report(db: Session, owner_id: uuid.UUID, tz: str, today: date) -> dict[str, Any]:
    current, comps = net_worth_at(db, owner_id, today)
    changes = {}
    for label, days in (("30d", 30), ("90d", 90), ("1y", 365)):
        prev, _ = net_worth_at(db, owner_id, today - timedelta(days=days))
        ch = nw.compare(current, prev)
        changes[label] = {
            "available": ch.available,
            "reason": ch.reason,
            "delta": {k: str(v) for k, v in ch.delta.items()},
        }
    qid, rev = record_query(db, owner_id, "net_worth", {"as_of": today.isoformat()}, nw.CALCULATION_VERSION)
    labels = {c.key: c for c in comps}
    return {
        "data": {
            "as_of": today.isoformat(),
            "totals": {
                cur: {"assets": str(t.assets), "liabilities": str(t.liabilities), "net_worth": str(t.net_worth)}
                for cur, t in current.totals.items()
            },
            "components": [
                {
                    "key": c.key,
                    "label": c.label,
                    "nature": c.nature.value,
                    "kind": c.kind,
                    "currency": c.currency,
                    "value": str(c.observation.value) if c.observation else None,
                    "as_of": c.observation.as_of.isoformat() if c.observation else None,
                    "stale": c.key in current.stale,
                }
                for c in comps
            ],
            "missing": [labels[k].label for k in current.missing],
            "changes": changes,
        },
        "provenance": Provenance(
            qid,
            None,
            today + timedelta(days=1),
            tz,
            [],
            0,
            datetime.now(UTC),
            nw.CALCULATION_VERSION,
            rev,
            current.partial,
            current.warnings(),
        ).as_dict(),
    }


def open_review_count(db: Session, owner_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.owner_id == owner_id, ReviewItem.status == ReviewStatus.OPEN)
        )
        or 0
    )
