"""Day-to-day insights on top of the ledger: calendar totals, upcoming bills, safe to spend, alerts,
cash-flow diagram and budget suggestions. All of them read the ledger; none create money movements.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from oneledger_db.models import (
    Account,
    AlertDismissal,
    Anomaly,
    Budget,
    CreditCard,
    CreditCardStatement,
    FinancialGoal,
    Import,
    Loan,
    RecurringTransaction,
    Transaction,
    TransactionAllocation,
)
from oneledger_domain import safe_spend as ss
from oneledger_domain.enums import (
    AccountStatus,
    AllocationEffect,
    ImportState,
    RecurrenceCadence,
    RecurrenceType,
)
from oneledger_domain.periods import DateRange, add_months, month_range
from oneledger_domain.reporting import AllocationFact, summarize
from oneledger_domain.text import plural
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .categories import load_context
from .reports import account_balances, active_txn_filter, category_breakdown, summary_report

STALE_BALANCE_DAYS = 7
_TYPE_LABEL = {
    "SALARY": "Salary",
    "RENT": "Rent",
    "EMI": "EMI",
    "SUBSCRIPTION": "Subscription",
    "INSURANCE": "Insurance",
    "SIP": "SIP",
    "TRANSFER": "Transfer",
    "BILL": "Bill",
    "OTHER": "Payment",
}
BILL_ALERT_DAYS = 3


# --- Calendar ------------------------------------------------------------------------------------


def daily_totals(
    db: Session, owner_id: uuid.UUID, rng: DateRange, currency: str, account_ids: list[uuid.UUID] | None = None
) -> list[dict[str, Any]]:
    """Money in and spent per day, with the same definitions as the monthly summary."""
    stmt = (
        select(Transaction.transaction_date, TransactionAllocation.effect, TransactionAllocation.amount, Transaction.id)
        .join(TransactionAllocation, TransactionAllocation.transaction_id == Transaction.id)
        .where(
            Transaction.owner_id == owner_id,
            active_txn_filter(),
            Transaction.currency == currency,
            Transaction.transaction_date >= rng.start,
            Transaction.transaction_date < rng.end_exclusive,
        )
    )
    if account_ids:
        stmt = stmt.where(Transaction.account_id.in_(account_ids))
    facts: dict[date, list[AllocationFact]] = defaultdict(list)
    txns: dict[date, set[uuid.UUID]] = defaultdict(set)
    for d, effect, amount, tid in db.execute(stmt):
        facts[d].append(AllocationFact(effect, amount))
        txns[d].add(tid)
    out = []
    for d in sorted(facts):
        s = summarize(facts[d])
        out.append(
            {
                "date": d,
                "money_in": str(s.income + s.unclassified_inflow),
                "spent": str(s.net_expenses + s.unclassified_outflow),
                "transaction_count": len(txns[d]),
            }
        )
    return out


# --- Upcoming bills ------------------------------------------------------------------------------


def _card_obligations(db: Session, owner_id: uuid.UUID, today: date, horizon: date) -> list[dict[str, Any]]:
    from .products import statement_status

    out = []
    cards = db.execute(
        select(CreditCard, Account)
        .join(Account, Account.id == CreditCard.account_id)
        .where(CreditCard.owner_id == owner_id, Account.deleted_at.is_(None), Account.status == AccountStatus.ACTIVE)
    ).all()
    for card, account in cards:
        stmt = db.scalars(
            select(CreditCardStatement)
            .where(CreditCardStatement.card_id == card.id)
            .order_by(CreditCardStatement.period_end.desc())
        ).first()
        if stmt is not None:
            st = statement_status(db, owner_id, card, stmt, today)
            left = Decimal(st["statement_balance"]) - Decimal(st["paid_since_statement"])
            if st["status"] != "PAID" and left > 0 and stmt.due_date <= horizon:
                out.append(
                    {
                        "kind": "card",
                        "id": str(card.id),
                        "label": f"{account.name} bill",
                        "date": stmt.due_date,
                        "amount": str(left),
                        "currency": account.currency,
                        "estimated": False,
                        "overdue": st["status"] in ("OVERDUE", "PARTIAL_OVERDUE"),
                        "href": "/cards",
                        "detail": "From your card statement"
                        + (f", minimum {st['minimum_due']}" if st["minimum_due"] else ""),
                    }
                )
                continue
        if card.statement_day and card.payment_due_days is not None:
            # No statement entered: the next bill is the current outstanding, due after the next cycle date.
            cycle = today.replace(day=min(card.statement_day, 28))
            due = cycle + timedelta(days=card.payment_due_days)
            if due < today:
                due = add_months(cycle, 1) + timedelta(days=card.payment_due_days)
            bal = account_balances(db, owner_id, today, [account])[0].balance
            if bal is not None and bal > 0 and due <= horizon:
                out.append(
                    {
                        "kind": "card",
                        "id": str(card.id),
                        "label": f"{account.name} bill",
                        "date": due,
                        "amount": str(bal),
                        "currency": account.currency,
                        "estimated": True,
                        "overdue": False,
                        "href": "/cards",
                        "detail": "Estimated from today's outstanding and your billing cycle",
                    }
                )
    return out


def _loan_obligations(db: Session, owner_id: uuid.UUID, today: date, horizon: date) -> list[dict[str, Any]]:
    from .products import _next_emi

    out = []
    for loan in db.scalars(select(Loan).where(Loan.owner_id == owner_id, Loan.deleted_at.is_(None))):
        due = _next_emi(loan, today - timedelta(days=1))
        if due <= horizon:
            out.append(
                {
                    "kind": "loan",
                    "id": str(loan.id),
                    "label": f"{loan.lender} {loan.loan_type} EMI".strip(),
                    "date": due,
                    "amount": str(loan.emi_amount),
                    "currency": loan.currency,
                    "estimated": False,
                    "overdue": False,
                    "href": f"/loans/{loan.id}",
                    "detail": "Scheduled EMI",
                }
            )
    return out


def upcoming_bills(db: Session, owner_id: uuid.UUID, today: date, days: int = 35) -> list[dict[str, Any]]:
    """Money that will leave in the next ``days``: recurring payments, card bills and loan EMIs.

    A card bill replaces the recurring "card bill" payment it would otherwise duplicate, and a loan's
    scheduled EMI replaces a detected recurring EMI of the same amount. A regular cash withdrawal is
    kind "cash" when you keep a cash wallet: the money moves into it, so it is not a bill.
    """
    from .cash import primary_cash_wallet

    horizon = today + timedelta(days=days)
    cat = load_context(db, owner_id)
    has_wallet = primary_cash_wallet(db, owner_id) is not None
    cards = _card_obligations(db, owner_id, today, horizon)
    loans = _loan_obligations(db, owner_id, today, horizon)
    loan_amounts = {Decimal(x["amount"]) for x in loans}
    items: list[dict[str, Any]] = [*cards, *loans]
    recs = db.execute(
        select(RecurringTransaction, Account.name)
        .join(Account, Account.id == RecurringTransaction.account_id)
        .where(
            RecurringTransaction.owner_id == owner_id,
            RecurringTransaction.state != "DISMISSED",
            RecurringTransaction.typical_amount < 0,
        )
    ).all()
    for r, account_name in recs:
        code = cat.categories_by_id[r.category_id].code if r.category_id in cat.categories_by_id else ""
        if code == "TRANSFERS_CARD_PAYMENT" and cards:
            continue
        if r.recurrence_type == RecurrenceType.EMI and -r.typical_amount in loan_amounts:
            continue
        due = ss.roll_forward(r.expected_next_date, r.cadence, today - timedelta(days=1))
        if due > horizon:
            continue
        if code in ("CASH", "TRANSFERS_CASH"):
            items.append(
                {
                    "kind": "cash" if has_wallet else "recurring",
                    "id": str(r.id),
                    "label": r.label,
                    "date": due,
                    "amount": str(-r.typical_amount),
                    "currency": r.currency,
                    "estimated": True,
                    "overdue": False,
                    "confirmed": r.state == "ACCEPTED",
                    "href": "/recurring",
                    "detail": f"Cash withdrawal from {account_name}"
                    + (", moves to your cash wallet" if has_wallet else ""),
                    "type": r.recurrence_type.value,
                }
            )
            continue
        items.append(
            {
                "kind": "recurring",
                "id": str(r.id),
                "label": r.label,
                "date": due,
                "amount": str(-r.typical_amount),
                "currency": r.currency,
                "estimated": r.amount_variable or r.state != "ACCEPTED",
                "overdue": False,
                "confirmed": r.state == "ACCEPTED",
                "href": "/recurring",
                "detail": f"{_TYPE_LABEL.get(r.recurrence_type.value, 'Payment')} from {account_name}"
                + ("" if r.state == "ACCEPTED" else ", detected (not confirmed)"),
                "type": r.recurrence_type.value,
            }
        )
    return sorted(items, key=lambda i: (i["date"], i["label"]))


# --- Safe to spend -------------------------------------------------------------------------------


def _next_income(db: Session, owner_id: uuid.UUID, today: date) -> tuple[date | None, str | None, str | None]:
    """The next regular income date and its label, or a note saying why none is counted on."""
    recs = db.scalars(
        select(RecurringTransaction).where(
            RecurringTransaction.owner_id == owner_id,
            RecurringTransaction.state != "DISMISSED",
            RecurringTransaction.typical_amount > 0,
            RecurringTransaction.recurrence_type != RecurrenceType.TRANSFER,
        )
    ).all()
    salary = [r for r in recs if r.recurrence_type == RecurrenceType.SALARY] or [
        r for r in recs if r.cadence == RecurrenceCadence.MONTHLY
    ]
    if not salary:
        return None, None, None
    expected = [(ss.next_income_date(r.expected_next_date, r.cadence, today), r) for r in salary]
    on_time = [(d, r) for d, r in expected if d is not None]
    if on_time:
        d, best = min(on_time, key=lambda x: x[0])
        return d, best.label, None
    late = min(salary, key=lambda r: r.expected_next_date)
    return (
        None,
        None,
        f"{late.label} was expected on {late.expected_next_date:%d %b %Y} and hasn't arrived, so this doesn't "
        f"count on it and looks {ss.DEFAULT_HORIZON_DAYS} days ahead.",
    )


def safe_to_spend(db: Session, owner_id: uuid.UUID, today: date, currency: str) -> dict[str, Any]:
    accounts = [
        a
        for a in db.scalars(
            select(Account).where(
                Account.owner_id == owner_id, Account.deleted_at.is_(None), Account.status == AccountStatus.ACTIVE
            )
        )
        if a.kind.is_liquid and a.currency == currency
    ]
    bals = account_balances(db, owner_id, today, accounts)
    known = [b for b in bals if b.balance is not None]
    if not known:
        return {
            "available": False,
            "reason": "no_balance",
            "message": "Add a balance to a bank or cash account to see what is safe to spend.",
        }
    notes: list[str] = []
    unknown = len(bals) - len(known)
    if unknown:
        notes.append(f"{plural(unknown, 'account')} without a known balance left out.")
    # Staleness is judged by the last *confirmed* balance (statement or one you entered): a manual
    # entry today does not make an account's statement data current.
    stale = [b for b in known if (today - (b.observed_as_of or today)).days > STALE_BALANCE_DAYS]
    if stale:
        oldest = min(b.observed_as_of or today for b in stale)
        names = ", ".join(sorted(b.account.name for b in stale)[:3])
        notes.append(
            f"Last confirmed balance for {names} is from {oldest:%d %b %Y}; "
            "anything spent since then that isn't in OneLedger yet is not included."
        )
    next_income, income_label, no_income_note = _next_income(db, owner_id, today)
    # Cash withdrawals into your own wallet stay within bank, cash and wallets, so they reserve nothing.
    bills = [b for b in upcoming_bills(db, owner_id, today, days=62) if b["kind"] != "cash"]
    unconfirmed = sum(1 for b in bills if b["kind"] == "recurring" and not b.get("confirmed"))
    if unconfirmed:
        notes.append(f"Includes {plural(unconfirmed, 'detected recurring payment')} you have not confirmed.")
    obligations = [
        ss.Obligation(b["label"], b["date"], Decimal(b["amount"]), b["kind"], b["id"])
        for b in bills
        if b["currency"] == currency
    ]
    liquid = sum((b.balance for b in known if b.balance is not None), Decimal(0))
    r = ss.compute(liquid, today, next_income, obligations, notes, no_income_note=no_income_note)
    return {
        "available": True,
        "currency": currency,
        "liquid_balance": str(r.liquid_balance),
        "until": r.until,
        "until_label": income_label,
        "days_left": r.days_left,
        "obligations": [
            {"label": o.label, "date": o.due_date, "amount": str(o.amount), "kind": o.kind} for o in r.obligations
        ],
        "obligations_total": str(r.obligations_total),
        "safe_total": str(r.safe_total),
        "per_day": str(r.per_day),
        "shortfall": r.shortfall,
        "assumptions": r.assumptions,
        "income_overdue": no_income_note is not None,
        "calculation_version": ss.SAFE_SPEND_VERSION,
    }


# --- Alerts --------------------------------------------------------------------------------------


def alerts(
    db: Session, owner_id: uuid.UUID, today: date, currency: str, *, safe: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Things that need attention now. Each has a stable key so a dismissal sticks."""
    from .planning import budgets_report

    found: list[dict[str, Any]] = []
    rep = budgets_report(db, owner_id, today.year, today.month)
    for b in rep["data"]["budgets"]:
        pct = Decimal(b["percent"])
        if b["over"]:
            found.append(
                {
                    "key": f"budget_over:{b['id']}:{today:%Y-%m}",
                    "severity": "high",
                    "title": f"Over budget: {b['name']}",
                    "detail": f"Spent {b['spent']} of {b['available']} this month.",
                    "href": "/budgets",
                }
            )
        elif pct >= 90:
            found.append(
                {
                    "key": f"budget_near:{b['id']}:{today:%Y-%m}",
                    "severity": "medium",
                    "title": f"Almost at budget: {b['name']}",
                    "detail": f"{b['percent']}% used with {plural(_days_left_in_month(today), 'day')} to go.",
                    "href": "/budgets",
                }
            )
    for bill in upcoming_bills(db, owner_id, today, days=BILL_ALERT_DAYS):
        if bill["kind"] == "cash" or (bill["kind"] == "recurring" and not bill.get("confirmed")):
            continue
        found.append(
            {
                "key": f"bill:{bill['kind']}:{bill['id']}:{bill['date']}",
                "severity": "high" if bill["overdue"] else "medium",
                "title": f"{'Overdue' if bill['overdue'] else 'Due soon'}: {bill['label']}",
                "detail": f"{bill['amount']} due {bill['date']:%d %b}.",
                "href": bill["href"],
            }
        )
    for a, t in db.execute(
        select(Anomaly, Transaction)
        .join(Transaction, Transaction.id == Anomaly.transaction_id)
        .where(
            Anomaly.owner_id == owner_id,
            Anomaly.dismissed_at.is_(None),
            active_txn_filter(),
            Transaction.transaction_date >= today - timedelta(days=30),
        )
        .order_by(Transaction.transaction_date.desc())
        .limit(10)
    ):
        found.append(
            {
                "key": f"unusual:{a.id}",
                "severity": "low",
                "title": f"Unusual: {t.merchant_name or t.raw_description[:60]}",
                "detail": a.reason,
                "href": f"/transactions?q={(t.merchant_name or '')[:40]}",
            }
        )
    for r in db.scalars(
        select(RecurringTransaction).where(
            RecurringTransaction.owner_id == owner_id,
            RecurringTransaction.state == "ACCEPTED",
            RecurringTransaction.expected_next_date < today - timedelta(days=5),
        )
    ):
        found.append(
            {
                "key": f"missed:{r.id}:{r.expected_next_date}",
                "severity": "medium",
                "title": f"Not seen yet: {r.label}",
                "detail": f"Expected around {r.expected_next_date:%d %b}. Import your latest statement, "
                "or check the payment went through.",
                "href": "/recurring",
            }
        )
    safe = safe if safe is not None else safe_to_spend(db, owner_id, today, currency)
    if safe.get("available") and safe["shortfall"]:
        found.append(
            {
                "key": f"shortfall:{safe['until']}",
                "severity": "high",
                "title": "Upcoming bills are more than your balance",
                "detail": f"{safe['obligations_total']} due before {safe['until']:%d %b}; "
                f"you have {safe['liquid_balance']}.",
                "href": "/",
            }
        )
    for acct_name, last in _stale_statements(db, owner_id, today):
        found.append(
            {
                "key": f"stale:{acct_name}:{last}",
                "severity": "low",
                "title": f"Import a newer statement for {acct_name}",
                "detail": f"The latest transactions are from {last:%d %b %Y}.",
                "href": "/imports",
            }
        )
    dismissed = set(db.scalars(select(AlertDismissal.alert_key).where(AlertDismissal.owner_id == owner_id)))
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted((f for f in found if f["key"] not in dismissed), key=lambda f: order[f["severity"]])


def _days_left_in_month(today: date) -> int:
    return (add_months(today.replace(day=1), 1) - today).days


def _stale_statements(db: Session, owner_id: uuid.UUID, today: date) -> list[tuple[str, date]]:
    rows = db.execute(
        select(Account.name, func.max(Transaction.transaction_date))
        .join(Transaction, Transaction.account_id == Account.id)
        .join(Import, Import.id == Transaction.import_id)
        .where(
            Account.owner_id == owner_id,
            Account.status == AccountStatus.ACTIVE,
            Account.deleted_at.is_(None),
            Import.state == ImportState.COMPLETED,
            active_txn_filter(),
        )
        .group_by(Account.name)
    ).all()
    return [(name, last) for name, last in rows if last is not None and (today - last).days > 35]


def dismiss_alert(db: Session, owner_id: uuid.UUID, key: str) -> None:
    db.execute(
        insert(AlertDismissal)
        .values(id=uuid.uuid4(), owner_id=owner_id, alert_key=key[:200])
        .on_conflict_do_nothing(index_elements=["owner_id", "alert_key"])
    )
    if key.startswith("unusual:"):
        a = db.get(Anomaly, uuid.UUID(key.split(":", 1)[1]))
        if a is not None and a.owner_id == owner_id and a.dismissed_at is None:
            from datetime import UTC, datetime

            a.dismissed_at = datetime.now(UTC)


# --- Cash-flow diagram ---------------------------------------------------------------------------


def cash_flow_sankey(db: Session, owner_id: uuid.UUID, tz: str, currency: str, rng: DateRange) -> dict[str, Any]:
    """Where the period's money came from and went: income sources -> money in -> spending and savings.

    Transfers between your accounts are left out; investments and loan principal are shown as their
    own destinations, and anything not categorised yet is shown as such.
    """
    summary = summary_report(db, owner_id, tz, currency, rng)["data"]
    income = category_breakdown(db, owner_id, tz, currency, rng, AllocationEffect.INCOME)["data"]["categories"]
    spend = category_breakdown(db, owner_id, tz, currency, rng, AllocationEffect.EXPENSE)["data"]["categories"]
    nodes: list[dict[str, str]] = []
    links: list[dict[str, Any]] = []

    def node(key: str, name: str, group: str) -> int:
        for i, n in enumerate(nodes):
            if n["key"] == key:
                return i
        nodes.append({"key": key, "name": name, "group": group})
        return len(nodes) - 1

    hub = node("hub", "Money in", "hub")
    total_in = Decimal(0)
    for c in income:
        amt = Decimal(c["amount"])
        if amt > 0:
            links.append({"source": node(f"in:{c['code']}", c["name"], "income"), "target": hub, "value": str(amt)})
            total_in += amt
    u_in = Decimal(summary["unclassified_inflow"])
    if u_in > 0:
        links.append({"source": node("in:uncat", "Not categorised (in)", "income"), "target": hub, "value": str(u_in)})
        total_in += u_in
    total_out = Decimal(0)
    for c in spend:
        amt = Decimal(c["amount"])
        if amt > 0:
            links.append({"source": hub, "target": node(f"out:{c['code']}", c["name"], "expense"), "value": str(amt)})
            total_out += amt
    extras = [
        ("out:uncat", "Not categorised (out)", Decimal(summary["unclassified_outflow"])),
        ("out:invest", "Investments", Decimal(summary["investment_contributions"])),
        ("out:principal", "Loan principal", Decimal(summary["loan_principal_paid"])),
    ]
    for key, name, amt in extras:
        if amt > 0:
            links.append(
                {
                    "source": hub,
                    "target": node(key, name, "saving" if key != "out:uncat" else "expense"),
                    "value": str(amt),
                }
            )
            total_out += amt
    if total_in > total_out:
        links.append(
            {
                "source": hub,
                "target": node("out:kept", "Left over", "saving"),
                "value": str(total_in - total_out),
            }
        )
    elif total_out > total_in:
        links.append(
            {
                "source": node("in:savings", "From your savings", "income"),
                "target": hub,
                "value": str(total_out - total_in),
            }
        )
    return {
        "nodes": nodes,
        "links": links,
        "currency": currency,
        "money_in": str(total_in),
        "money_out": str(total_out),
    }


# --- Budget suggestions --------------------------------------------------------------------------


def budget_suggestions(db: Session, owner_id: uuid.UUID, tz: str, currency: str, today: date) -> list[dict[str, Any]]:
    """For each spending category without a budget: the average of the last three full months,
    rounded up to the next 500."""
    first = add_months(today.replace(day=1), -3)
    months = [month_range(add_months(first, i).year, add_months(first, i).month) for i in range(3)]
    budgeted = set(
        db.scalars(
            select(Budget.category_id).where(
                Budget.owner_id == owner_id, Budget.deleted_at.is_(None), Budget.category_id.is_not(None)
            )
        )
    )
    totals: dict[str, dict[str, Any]] = {}
    for m in months:
        for c in category_breakdown(db, owner_id, tz, currency, m)["data"]["categories"]:
            if not c["category_id"] or uuid.UUID(c["category_id"]) in budgeted:
                continue
            t = totals.setdefault(c["category_id"], {"name": c["name"], "code": c["code"], "sum": Decimal(0), "n": 0})
            t["sum"] += Decimal(c["amount"])
            t["n"] += 1
    out = []
    for cid, t in totals.items():
        avg = t["sum"] / 3
        if avg <= 0 or t["code"] in ("LOANS",):
            continue
        suggested = (avg / 500).to_integral_value(rounding=ROUND_CEILING) * 500
        out.append(
            {
                "category_id": cid,
                "name": t["name"],
                "average": str(avg.quantize(Decimal("0.01"))),
                "suggested": str(suggested),
                "months_with_spending": t["n"],
            }
        )
    return sorted(out, key=lambda s: -Decimal(s["average"]))


def goals_total_monthly(db: Session, owner_id: uuid.UUID, today: date) -> Decimal:
    from .planning import goal_progress

    total = Decimal(0)
    for g in db.scalars(
        select(FinancialGoal).where(FinancialGoal.owner_id == owner_id, FinancialGoal.deleted_at.is_(None))
    ):
        need = goal_progress(db, owner_id, g, today)["monthly_needed"]
        if need:
            total += Decimal(need)
    return total
