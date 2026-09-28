"""Every transaction can be traced to its statement row and to the reason for its category."""

from __future__ import annotations

from .helpers import account, csv_bytes, import_csv, txns


def test_detail_shows_statement_row_and_category_reason(api):
    a = account(api, "HDFC")
    imp = import_csv(api, a, csv_bytes(["05/08/2026,BESCOM ELECTRICITY BILL,BILL42,1500.00,,8500.00"]))
    t = txns(api)[0]
    d = api.ok(api.get(f"/transactions/{t['id']}"))
    [origin] = d["origin"]
    assert origin["import_id"] == imp["id"] and origin["filename"] == "statement.csv"
    header = ["Date", "Narration", "Ref No", "Withdrawal Amt", "Deposit Amt", "Closing Balance"]
    names = [c[0] for c in origin["columns"]]
    assert names == [h for h in header if h in names]  # the file's column order
    assert dict(origin["columns"])["Narration"] == "BESCOM ELECTRICITY BILL" and origin["balance_after"] == "8500.00"
    assert origin["row_number"] >= 2

    rule = api.ok(
        api.post(
            "/rules",
            json={
                "name": "Power bill",
                "conditions": [{"field": "description", "op": "contains", "value": "BESCOM"}],
                "category_id": next(
                    c["id"] for c in api.ok(api.get("/categories")) if c["code"] == "HOUSING_UTILITIES"
                ),
            },
        ),
        201,
    )
    api.ok(api.post(f"/rules/{rule['id']}/apply", json={"confirm": True}))
    alloc = api.ok(api.get(f"/transactions/{t['id']}"))["allocations"][0]
    assert alloc["classification_source"] == "RULE" and alloc["rule"] == "Power bill"


def test_origin_hidden_after_import_deleted(api):
    a = account(api, "HDFC")
    first = csv_bytes(["01/08/2026,POS SHOP,,100.00,,900.00"])
    import_csv(api, a, first)
    second = import_csv(api, a, first, name="again.csv")  # replay: links the same row
    t = txns(api)[0]
    assert len(api.ok(api.get(f"/transactions/{t['id']}"))["origin"]) == 2
    api.ok(api.post(f"/imports/{second['id']}/delete", json={"confirm": True}))
    assert [o["filename"] for o in api.ok(api.get(f"/transactions/{t['id']}"))["origin"]] == ["statement.csv"]


def test_tags_rename_and_delete(api):
    a = account(api, "HDFC")
    import_csv(api, a, csv_bytes(["01/08/2026,POS SHOP,,100.00,,900.00"]))
    t = txns(api)[0]
    tag = api.ok(api.post("/tags", json={"name": "Goa trip"}), 201)
    api.ok(api.patch(f"/transactions/{t['id']}", json={"version": t["version"], "tag_ids": [tag["id"]]}))
    renamed = api.ok(api.patch(f"/tags/{tag['id']}", json={"name": "Goa 2026"}))
    assert renamed["name"] == "Goa 2026"
    assert txns(api)[0]["tags"] == [{"id": tag["id"], "name": "Goa 2026"}]
    assert api.delete(f"/tags/{tag['id']}").status_code == 204
    assert txns(api)[0]["tags"] == [] and api.ok(api.get("/tags")) == []
    assert len(txns(api)) == 1  # the transaction itself is untouched


def test_bulk_classify_skips_transfers_and_splits(api):
    hdfc = account(api, "HDFC", number="50100011112222")
    union = account(api, "Union Bank", number="33334444")
    import_csv(
        api,
        hdfc,
        csv_bytes(
            [
                "02/08/2026,ZXQ ONE,,100.00,,900.00",
                "03/08/2026,ZXQ TWO,,50.00,,850.00",
                "10/08/2026,NEFT TO SELF UNION,UTR123456789,500.00,,350.00",
            ]
        ),
    )
    import_csv(api, union, csv_bytes(["10/08/2026,NEFT FROM SELF HDFC,UTR123456789,,500.00,500.00"]))
    food = next(c["id"] for c in api.ok(api.get("/categories")) if c["code"] == "FOOD")
    ids = [t["id"] for t in txns(api)]
    out = api.ok(api.post("/transactions/classify-bulk", json={"transaction_ids": ids, "category_id": food}))
    assert out == {"changed": 2, "skipped": 2}
    by_desc = {t["description"]: t for t in txns(api)}
    assert by_desc["ZXQ ONE"]["allocations"][0]["category"]["code"] == "FOOD"
    assert by_desc["ZXQ ONE"]["allocations"][0]["classification_source"] == "USER"
    assert by_desc["NEFT TO SELF UNION"]["is_transfer"] is True


def test_sign_out_other_devices_keeps_current(api):
    before = api.ok(api.get("/auth/sessions"))
    out = api.ok(api.post("/auth/sessions/revoke-others"))
    assert out["signed_out"] == len(before) - 1
    after = api.ok(api.get("/auth/sessions"))
    assert len(after) == 1 and after[0]["current"] is True
