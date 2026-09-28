from __future__ import annotations

from typing import Any


def account(api, name: str, kind: str = "BANK_SAVINGS", number: str | None = None, **kw: Any) -> str:
    body = {"name": name, "kind": kind, **kw}
    if number:
        body["account_number"] = number
    return api.ok(api.post("/accounts", json=body), 201)["id"]


def csv_bytes(
    rows: list[str], header: str = "Date,Narration,Ref No,Withdrawal Amt,Deposit Amt,Closing Balance"
) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def upload(api, account_id: str, data: bytes, name: str = "statement.csv") -> dict:
    r = api.post("/imports", data={"account_id": account_id}, files={"file": (name, data, "text/csv")})
    return api.ok(r, 201)


MAPPING = {
    "date_column": "Date",
    "date_format": "DD/MM/YYYY",
    "description_columns": ["Narration"],
    "amount_mode": "split",
    "debit_column": "Withdrawal Amt",
    "credit_column": "Deposit Amt",
    "reference_column": "Ref No",
    "balance_column": "Closing Balance",
    "currency": "INR",
}


def import_csv(api, account_id: str, data: bytes, mapping: dict | None = None, name: str = "statement.csv") -> dict:
    imp = upload(api, account_id, data, name)
    if imp["state"] == "NEEDS_MAPPING" or mapping:
        imp = api.ok(api.put(f"/imports/{imp['id']}/mapping", json=mapping or MAPPING))
    assert imp["state"] == "PREVIEW_READY", imp
    done = api.ok(
        api.post(
            f"/imports/{imp['id']}/confirm", json={"preview_hash": imp["preview_hash"], "accept_rejections": True}
        ),
        202,
    )
    assert done["state"] == "COMPLETED", done
    return done


def summary(api, start: str, end: str) -> dict:
    return api.ok(api.get("/analytics/summary", params={"start_date": start, "end_date_exclusive": end}))["data"]


def txns(api, **params) -> list[dict]:
    return api.ok(api.get("/transactions", params={"limit": 200, **params}))["items"]
