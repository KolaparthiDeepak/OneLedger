"""Seed category tree from the product requirements."""

from __future__ import annotations

from dataclasses import dataclass

from oneledger_domain.enums import AllocationEffect as E


@dataclass(frozen=True, slots=True)
class CategorySeed:
    code: str
    name: str
    parent: str | None
    effect: E


_TREE: list[tuple[str, str, E, list[tuple[str, str]]]] = [
    (
        "INCOME",
        "Income",
        E.INCOME,
        [
            ("INCOME_SALARY", "Salary"),
            ("INCOME_FREELANCE", "Freelance"),
            ("INCOME_INTEREST", "Interest"),
            ("INCOME_REFUND", "Refund"),
            ("INCOME_DIVIDEND", "Dividends"),
            ("INCOME_OTHER", "Other Income"),
        ],
    ),
    (
        "FOOD",
        "Food",
        E.EXPENSE,
        [
            ("FOOD_RESTAURANTS", "Restaurants"),
            ("FOOD_GROCERIES", "Groceries"),
            ("FOOD_DELIVERY", "Food Delivery"),
        ],
    ),
    (
        "TRANSPORT",
        "Transport",
        E.EXPENSE,
        [
            ("TRANSPORT_FUEL", "Fuel"),
            ("TRANSPORT_CAB", "Cab"),
            ("TRANSPORT_METRO", "Metro"),
            ("TRANSPORT_BUS", "Bus"),
            ("TRANSPORT_FLIGHT", "Flight"),
            ("TRANSPORT_TRAIN", "Train"),
        ],
    ),
    (
        "HOUSING",
        "Housing",
        E.EXPENSE,
        [
            ("HOUSING_RENT", "Rent"),
            ("HOUSING_HOME_LOAN", "Home Loan"),
            ("HOUSING_MAINTENANCE", "Maintenance"),
            ("HOUSING_UTILITIES", "Utilities"),
        ],
    ),
    ("SHOPPING", "Shopping", E.EXPENSE, []),
    ("ENTERTAINMENT", "Entertainment", E.EXPENSE, []),
    ("HEALTHCARE", "Healthcare", E.EXPENSE, []),
    ("EDUCATION", "Education", E.EXPENSE, []),
    ("TRAVEL", "Travel", E.EXPENSE, []),
    ("INSURANCE", "Insurance", E.EXPENSE, []),
    ("INVESTMENTS", "Investments", E.INVESTMENT, []),
    ("LOANS", "Loans", E.EXPENSE, [("LOANS_INTEREST", "Loan Interest"), ("LOANS_PRINCIPAL", "Loan Principal")]),
    ("SUBSCRIPTIONS", "Subscriptions", E.EXPENSE, []),
    (
        "TRANSFERS",
        "Transfers",
        E.TRANSFER,
        [
            ("TRANSFERS_SELF", "Own Account Transfer"),
            ("TRANSFERS_CARD_PAYMENT", "Credit Card Payment"),
        ],
    ),
    ("CASH", "Cash", E.EXPENSE, []),
    ("TAXES", "Taxes", E.EXPENSE, []),
    ("FEES", "Fees", E.EXPENSE, []),
    ("OTHER", "Other", E.EXPENSE, []),
]

_OVERRIDES = {"LOANS_PRINCIPAL": E.LOAN_PRINCIPAL}


def seed_categories() -> list[CategorySeed]:
    out: list[CategorySeed] = []
    for code, name, effect, children in _TREE:
        out.append(CategorySeed(code, name, None, effect))
        for ccode, cname in children:
            out.append(CategorySeed(ccode, cname, code, _OVERRIDES.get(ccode, effect)))
    return out


SEED_BY_CODE = {c.code: c for c in seed_categories()}
