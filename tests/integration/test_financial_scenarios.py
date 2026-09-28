"""Known-answer financial fixtures from the implementation plan (F-series)."""

from __future__ import annotations

from decimal import Decimal

from .helpers import MAPPING, account, csv_bytes, import_csv, summary, txns, upload

AUG = ("2026-08-01", "2026-09-01")


def test_f02_same_csv_twice_is_idempotent_even_with_new_filename(api):
    a = account(api, "HDFC", number="1111222233334444")
    data = csv_bytes(
        [
            "01/08/2026,SALARY ACME,SAL001,,100000.00,100000.00",
            "03/08/2026,UPI/SWIGGY/123456789012,123456789012,650.00,,99350.00",
        ]
    )
    first = import_csv(api, a, data)
    assert first["counts"]["inserted"] == 2
    second = import_csv(api, a, data, name="renamed.csv")
    assert second["is_replay"] is True
    assert second["counts"]["inserted"] == 0 and second["counts"]["linked"] == 2
    assert len(txns(api)) == 2
    s = summary(api, *AUG)
    assert Decimal(s["income"]) == Decimal("100000") and Decimal(s["net_expenses"]) == Decimal("650")


def test_f03_identical_genuine_purchases_are_both_kept(api):
    a = account(api, "HDFC")
    data = csv_bytes(
        [
            "05/08/2026,POS CAFE COFFEE DAY,,100.00,,900.00",
            "05/08/2026,POS CAFE COFFEE DAY,,100.00,,800.00",
        ]
    )
    import_csv(api, a, data)
    assert len(txns(api)) == 2
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("200")


def test_f03b_overlapping_statement_without_balance_evidence_goes_to_review(api):
    a = account(api, "HDFC")
    header = "Date,Narration,Ref No,Withdrawal Amt,Deposit Amt"
    import_csv(
        api,
        a,
        csv_bytes(["05/08/2026,POS SHOP,,100.00,"], header),
        mapping={k: v for k, v in MAPPING.items() if k != "balance_column"},
    )
    imp = upload(api, a, csv_bytes(["05/08/2026,POS SHOP,,100.00,", "06/08/2026,POS OTHER,,50.00,"], header), "b.csv")
    rows = api.ok(api.get(f"/imports/{imp['id']}/rows"))["items"]
    assert [r["status"] for r in rows] == ["POSSIBLE_DUPLICATE", "VALID"]


def test_f05_own_transfer_with_shared_reference_is_not_expense(api):
    hdfc = account(api, "HDFC", number="50100011112222")
    union = account(api, "Union Bank", number="33334444")
    import_csv(api, hdfc, csv_bytes(["10/08/2026,NEFT TO SELF UNION,UTR123456789,50000.00,,50000.00"]))
    import_csv(api, union, csv_bytes(["10/08/2026,NEFT FROM SELF HDFC,UTR123456789,,50000.00,50000.00"]))
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) == 0 and Decimal(s["income"]) == 0
    assert Decimal(s["internal_transfers_out"]) == Decimal("50000")
    assert all(t["is_transfer"] for t in txns(api))


def test_f05b_amount_and_date_only_is_a_suggestion_not_confirmation(api):
    hdfc = account(api, "HDFC")
    union = account(api, "Union Bank")
    import_csv(api, hdfc, csv_bytes(["10/08/2026,SOME PAYMENT,,50000.00,,1.00"]))
    import_csv(api, union, csv_bytes(["11/08/2026,SOME CREDIT,,,50000.00,1.00"]))
    assert not any(t["is_transfer"] for t in txns(api))
    review = api.ok(api.get("/review", params={"kind": "TRANSFER_SUGGESTION"}))
    assert len(review) == 1
    tid = review[0]["related"]["transfer_id"]
    api.ok(api.post(f"/transfers/{tid}/confirm"))
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) == 0 and Decimal(s["unclassified_outflow"]) == 0


def test_f06_refund_reduces_net_expense(api):
    a = account(api, "HDFC")
    import_csv(api, a, csv_bytes(["03/08/2026,AMAZON ORDER,,650.00,,1.00", "09/08/2026,AMAZON REFUND,,,200.00,1.00"]))
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) == Decimal("450")
    assert Decimal(s["income"]) == 0
    items = {t["description"]: t for t in txns(api)}
    rel = api.ok(
        api.post(
            "/transactions/link-refund",
            json={"original_id": items["AMAZON ORDER"]["id"], "related_id": items["AMAZON REFUND"]["id"]},
        )
    )
    assert rel["relation_id"]
    sep = summary(api, "2026-08-05", "2026-09-01")  # refund-only window
    assert Decimal(sep["net_expenses"]) == Decimal("-200")


def test_f07_card_purchase_and_bill_payment_counted_once(api):
    bank = account(api, "HDFC", number="50100099990000")
    card = api.ok(
        api.post(
            "/cards",
            json={
                "name": "HDFC Regalia",
                "issuer": "HDFC",
                "card_number": "4111111111119876",
                "credit_limit": "200000.00",
                "outstanding": "0.00",
                "outstanding_as_of": "2026-07-31",
            },
        ),
        201,
    )
    card_mapping = {
        "date_column": "Date",
        "date_format": "DD/MM/YYYY",
        "description_columns": ["Details"],
        "amount_mode": "drcr_column",
        "amount_column": "Amount",
        "drcr_column": "Type",
        "currency": "INR",
    }
    import_csv(
        api,
        card["account_id"],
        csv_bytes(
            ["02/08/2026,FLIPKART,3000.00,Dr", "20/08/2026,PAYMENT RECEIVED THANK YOU,3000.00,Cr"],
            "Date,Details,Amount,Type",
        ),
        card_mapping,
    )
    import_csv(api, bank, csv_bytes(["20/08/2026,CC PAYMENT XXXX9876,,3000.00,,5000.00"]))
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) == Decimal("3000")
    cards = api.ok(api.get("/cards"))
    assert Decimal(cards[0]["outstanding"]) == 0


def test_f08_emi_split_principal_interest(api):
    bank = account(api, "HDFC", opening_date="2026-07-31", opening_balance="50000.00")
    loan = api.ok(
        api.post(
            "/loans",
            json={
                "lender": "SBI",
                "loan_type": "Home Loan",
                "original_principal": "1000000.00",
                "opening_outstanding": "800000.00",
                "opening_date": "2026-07-31",
                "start_date": "2020-01-05",
                "first_emi_date": "2020-02-05",
                "tenure_months": 240,
                "annual_rate_percent": "8.5",
                "emi_amount": "10000.00",
            },
        ),
        201,
    )
    import_csv(api, bank, csv_bytes(["05/08/2026,NACH SBI HOME LOAN EMI,,10000.00,,40000.00"]))
    emi = txns(api)[0]
    api.ok(
        api.post(
            f"/loans/{loan['id']}/payments",
            json={"transaction_id": emi["id"], "principal": "8000.00", "interest": "2000.00"},
        ),
        201,
    )
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) == Decimal("2000")
    assert Decimal(s["loan_principal_paid"]) == Decimal("8000")
    detail = api.ok(api.get(f"/loans/{loan['id']}"))
    assert Decimal(detail["outstanding_principal"]) == Decimal("792000")
    bal = {b["name"]: b for b in api.ok(api.get("/analytics/balances"))["data"]["accounts"]}
    assert Decimal(bal["HDFC"]["balance"]) == Decimal("40000")


def test_f09_cash_withdrawal_then_cash_purchase(api):
    bank = account(api, "HDFC")
    cash = account(api, "Wallet cash", kind="CASH", opening_date="2026-07-31", opening_balance="0.00")
    import_csv(api, bank, csv_bytes(["04/08/2026,ATM CASH WDL,,2000.00,,1.00"]))
    atm = txns(api)[0]
    c = api.ok(
        api.post(
            "/transactions",
            json={
                "account_id": cash,
                "amount": "2000.00",
                "transaction_date": "2026-08-04",
                "description": "Cash from ATM",
            },
        ),
        201,
    )
    api.ok(api.post(f"/transactions/{atm['id']}/mark-transfer", json={"counterpart_transaction_id": c["id"]}))
    api.ok(
        api.post(
            "/transactions",
            json={
                "account_id": cash,
                "amount": "-300.00",
                "transaction_date": "2026-08-06",
                "description": "Vegetables market",
            },
        ),
        201,
    )
    s = summary(api, *AUG)
    assert Decimal(s["net_expenses"]) + Decimal(s["unclassified_outflow"]) == Decimal("300")


def test_f10_split_must_sum_exactly(api):
    a = account(api, "HDFC")
    t = api.ok(
        api.post(
            "/transactions",
            json={"account_id": a, "amount": "-100.01", "transaction_date": "2026-08-02", "description": "DMART"},
        ),
        201,
    )
    bad = api.post(
        f"/transactions/{t['id']}/split",
        json={
            "version": t["version"],
            "parts": [{"amount": "-50.00", "effect": "expense"}, {"amount": "-50.00", "effect": "expense"}],
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "SPLIT_SUM_MISMATCH"
    ok = api.ok(
        api.post(
            f"/transactions/{t['id']}/split",
            json={
                "version": t["version"],
                "parts": [{"amount": "-60.00", "effect": "expense"}, {"amount": "-40.01", "effect": "expense"}],
            },
        )
    )
    assert ok["is_split"] and sum(Decimal(x["amount"]) for x in ok["allocations"]) == Decimal("-100.01")


def test_float_money_rejected(api):
    a = account(api, "HDFC")
    r = api.post(
        "/transactions", json={"account_id": a, "amount": -12.5, "transaction_date": "2026-08-02", "description": "X"}
    )
    assert r.status_code == 422


def test_f17_net_worth_components(api):
    account(api, "Bank", opening_date="2026-08-01", opening_balance="100000.00")
    account(api, "Cash", kind="CASH", opening_date="2026-08-01", opening_balance="2000.00")
    account(api, "Card", kind="CREDIT_CARD", opening_date="2026-08-01", opening_balance="3000.00")
    account(api, "Loan", kind="LOAN", opening_date="2026-08-01", opening_balance="80000.00")
    fd_acct = account(api, "FD", kind="FIXED_DEPOSIT", opening_date="2026-08-01", opening_balance="50000.00")
    inv = api.ok(
        api.post("/investments", json={"name": "FD 1y", "instrument_type": "FIXED_DEPOSIT", "account_id": fd_acct}), 201
    )
    api.ok(
        api.post(
            f"/investments/{inv['id']}/valuations", json={"valuation_date": "2026-08-01", "total_value": "50000.00"}
        ),
        201,
    )
    nw = api.ok(api.get("/analytics/net-worth"))
    assert Decimal(nw["data"]["totals"]["INR"]["net_worth"]) == Decimal("69000")
    assert nw["provenance"]["partial"] is False


def test_f18_missing_balance_is_partial_not_zero(api):
    account(api, "Bank", opening_date="2026-08-01", opening_balance="1000.00")
    account(api, "Unknown bank")
    nw = api.ok(api.get("/analytics/net-worth"))
    assert nw["provenance"]["partial"] is True
    assert "Unknown bank" in nw["data"]["missing"]


def test_manual_lock_survives_rule_application(api):
    a = account(api, "HDFC")
    import_csv(api, a, csv_bytes(["02/08/2026,ZOMATO ORDER,,300.00,,1.00", "03/08/2026,ZOMATO ORDER 2,,200.00,,1.00"]))
    cats = {c["code"]: c["id"] for c in api.ok(api.get("/categories"))}
    items = txns(api)
    api.ok(api.post(f"/transactions/{items[0]['id']}/classify", json={"category_id": cats["ENTERTAINMENT"]}))
    rule = {
        "name": "Zomato → Restaurants",
        "conditions": [{"field": "description", "op": "contains", "value": "zomato"}],
        "category_id": cats["FOOD_RESTAURANTS"],
    }
    dry = api.ok(api.post("/rules/dry-run", json=rule))
    assert dry["matching_transactions"] == 2 and dry["locked_skipped"] == 1
    assert {t["allocations"][0]["category"]["code"] for t in txns(api)} == {
        "ENTERTAINMENT",
        "FOOD_DELIVERY",
    }  # no writes
    created = api.ok(api.post("/rules", json=rule), 201)
    api.ok(api.post(f"/rules/{created['id']}/apply", json={"confirm": True}))
    codes = sorted(t["allocations"][0]["category"]["code"] for t in txns(api))
    assert codes == ["ENTERTAINMENT", "FOOD_RESTAURANTS"]


def test_merge_and_undo(api):
    a = account(api, "HDFC")
    t1 = api.ok(
        api.post(
            "/transactions",
            json={"account_id": a, "amount": "-500.00", "transaction_date": "2026-08-02", "description": "Dinner"},
        ),
        201,
    )
    t2 = api.ok(
        api.post(
            "/transactions",
            json={"account_id": a, "amount": "-500.00", "transaction_date": "2026-08-02", "description": "Dinner dup"},
        ),
        201,
    )
    rel = api.ok(api.post("/transactions/merge", json={"survivor_id": t1["id"], "duplicate_id": t2["id"]}))
    assert len(txns(api)) == 1
    api.ok(api.post(f"/relations/{rel['relation_id']}/undo"))
    assert len(txns(api)) == 2


def test_evidence_query_reproduces_totals(api):
    a = account(api, "HDFC")
    import_csv(api, a, csv_bytes(["02/08/2026,SWIGGY,,300.00,,1.00", "15/09/2026,SWIGGY,,200.00,,1.00"]))
    s = api.ok(api.get("/analytics/summary", params={"start_date": AUG[0], "end_date_exclusive": AUG[1]}))
    evidence = api.ok(api.get("/transactions", params={"query_id": s["provenance"]["query_id"]}))
    assert len(evidence["items"]) == s["provenance"]["transaction_count"] == 1


def test_f07b_card_imported_after_bank_matches_existing_payments(api):
    bank = account(api, "HDFC", number="50100099990000")
    import_csv(api, bank, csv_bytes(["20/08/2026,CC PAYMENT XXXX9876,,3000.00,,5000.00"]))
    assert api.ok(api.get("/review", params={"kind": "UNMATCHED_TRANSFER"}))  # no card account yet
    card = api.ok(
        api.post(
            "/cards",
            json={
                "name": "Card",
                "issuer": "HDFC",
                "card_number": "4111111111119876",
                "outstanding": "0.00",
                "outstanding_as_of": "2026-07-31",
            },
        ),
        201,
    )
    card_mapping = {
        "date_column": "Date",
        "date_format": "DD/MM/YYYY",
        "description_columns": ["Details"],
        "amount_mode": "drcr_column",
        "amount_column": "Amount",
        "drcr_column": "Type",
        "currency": "INR",
    }
    done = import_csv(
        api,
        card["account_id"],
        csv_bytes(
            ["02/08/2026,FLIPKART,3000.00,Dr", "20/08/2026,PAYMENT RECEIVED THANK YOU,3000.00,Cr"],
            "Date,Details,Amount,Type",
        ),
        card_mapping,
    )
    assert done["counts"]["transfers_auto_confirmed"] == 1
    assert api.ok(api.get("/review", params={"kind": "UNMATCHED_TRANSFER"})) == []
    assert Decimal(summary(api, *AUG)["net_expenses"]) == Decimal("3000")


def test_hdfc_csv_imports_without_mapping_step(api):
    a = account(api, "HDFC")
    imp = upload(
        api,
        a,
        csv_bytes(
            [
                "01/08/26,SALARY ACME,SAL01,01/08/26,,100000.00,100000.00",
                "03/08/26,UPI-SWIGGY-612345678901,612345678901,03/08/26,650.00,,99350.00",
            ],
            "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance",
        ),
    )
    # Every date here is ambiguous (day <= 12) but the HDFC preset fixes DD/MM/YY, so no mapping step.
    assert imp["state"] == "PREVIEW_READY" and imp["suggested_mapping"]["preset"] == "hdfc-netbanking"


def test_unreadable_pdf_can_be_reparsed_after_parser_update(api):
    from tests.providers.test_parsers import MULTI, _pdf

    a = account(api, "Bank")
    failed = api.ok(
        api.post(
            "/imports", data={"account_id": a}, files={"file": ("s.pdf", _pdf(["no rows here"]), "application/pdf")}
        ),
        201,
    )
    assert failed["state"] == "FAILED"
    r = api.post(f"/imports/{failed['id']}/reparse")
    assert r.status_code == 201 and r.json()["state"] == "FAILED"  # still unreadable: honest failure again
    ok = api.ok(
        api.post("/imports", data={"account_id": a}, files={"file": ("m.pdf", _pdf(MULTI), "application/pdf")}), 201
    )
    assert ok["state"] == "PREVIEW_READY" and ok["counts"]["to_import"] == 2


def test_monthly_series_reports_uncategorised_both_ways(api):
    a = account(api, "HDFC")
    import_csv(
        api,
        a,
        csv_bytes(
            [
                "02/08/2026,SALARY ACME,SAL001,,100000.00,100000.00",
                "03/08/2026,ZXQ UNKNOWN PAYEE,,1234.50,,98765.50",
                "04/08/2026,ZXQ UNKNOWN SENDER,,,500.25,99265.75",
            ]
        ),
    )
    months = api.ok(api.get("/analytics/monthly", params={"months": 36}))["data"]["months"]
    aug = next(m for m in months if m["month"] == "2026-08")
    s = summary(api, *AUG)
    # The chart's month must add up to the same Money in / Spent as the summary.
    assert Decimal(aug["unclassified_inflow"]) == Decimal(s["unclassified_inflow"]) == Decimal("500.25")
    assert Decimal(aug["unclassified_outflow"]) == Decimal(s["unclassified_outflow"]) == Decimal("1234.50")
    assert Decimal(aug["income"]) == Decimal(s["income"])
