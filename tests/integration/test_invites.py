"""Invite links: a new person gets their own ledger, and links work exactly once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from oneledger_db.models import Invite
from oneledger_db.session import owner_session
from sqlalchemy import update

from ..conftest import Api
from .helpers import account, csv_bytes, import_csv, txns

PASSWORD = "friend-password-123"


def token_of(invite: dict) -> str:
    return invite["url"].rsplit("/invite/", 1)[1]


def accept(client, token: str, email: str = "friend@example.com", **kw):  # type: ignore[no-untyped-def]
    body = {"token": token, "email": email, "name": "Friend", "password": PASSWORD, **kw}
    return client.post("/api/v1/invites/accept", json=body)


def test_invite_creates_a_separate_ledger(api, client):
    a = account(api, "Owner HDFC")
    import_csv(api, a, csv_bytes(["01/08/2026,SALARY ACME,SAL001,,100000.00,100000.00"]))
    inv = api.ok(api.post("/invites", json={"note": "For Priya"}), 201)
    assert inv["status"] == "pending" and inv["url"].startswith("http")

    check = client.post("/api/v1/invites/check", json={"token": token_of(inv)})
    assert check.status_code == 200 and check.json()["invited_by"] == "Owner"

    r = accept(client, token_of(inv))
    assert r.status_code == 201, r.text
    friend = Api(client, r.json()["session_token"])
    assert friend.ok(friend.get("/me"))["email"] == "friend@example.com"

    # The friend starts empty and cannot see the owner's accounts or transactions, and vice versa.
    assert friend.ok(friend.get("/accounts")) == [] and txns(friend) == []
    assert friend.get(f"/accounts/{a}").status_code == 404
    assert any(c["code"] == "FOOD" for c in friend.ok(friend.get("/categories")))
    fa = account(friend, "Friend SBI")
    assert [x["id"] for x in api.ok(api.get("/accounts"))] == [a]
    assert fa != a

    listed = api.ok(api.get("/invites"))[0]
    assert listed["status"] == "used" and listed["used_by_email"] == "friend@example.com"
    assert friend.ok(friend.get("/invites")) == []  # invites are private to their sender


def test_invite_works_once_and_can_be_revoked(api, client):
    inv = api.ok(api.post("/invites", json={}), 201)
    assert accept(client, token_of(inv)).status_code == 201
    again = accept(client, token_of(inv), email="someone@example.com")
    assert again.status_code == 404 and again.json()["error"]["code"] == "INVITE_INVALID"

    other = api.ok(api.post("/invites", json={}), 201)
    assert api.ok(api.post(f"/invites/{other['id']}/revoke"))["status"] == "revoked"
    assert client.post("/api/v1/invites/check", json={"token": token_of(other)}).status_code == 404
    assert accept(client, token_of(other), email="late@example.com").status_code == 404


def test_expired_and_email_bound_invites(api, client, ctx):
    bound = api.ok(api.post("/invites", json={"email": "Asha@Example.com"}), 201)
    assert bound["email"] == "asha@example.com"
    assert client.post("/api/v1/invites/check", json={"token": token_of(bound)}).json()["email"] == "asha@example.com"
    wrong = accept(client, token_of(bound), email="someone@example.com")
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"
    assert accept(client, token_of(bound), email="asha@example.com").status_code == 201

    old = api.ok(api.post("/invites", json={}), 201)
    owner_id = api.ok(api.get("/me"))["id"]
    import uuid

    with owner_session(ctx.sessions, uuid.UUID(owner_id)) as db:
        db.execute(
            update(Invite).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)).where(Invite.id == old["id"])
        )
    assert accept(client, token_of(old), email="late@example.com").status_code == 404
    assert next(i for i in api.ok(api.get("/invites")) if i["id"] == old["id"])["status"] == "expired"


def test_cannot_invite_or_join_with_an_existing_email(api, client):
    r = api.post("/invites", json={"email": "owner@example.com"})
    assert r.status_code == 409
    inv = api.ok(api.post("/invites", json={}), 201)
    taken = accept(client, token_of(inv), email="owner@example.com")
    assert taken.status_code == 409
    # A failed attempt does not use up the invite.
    assert accept(client, token_of(inv)).status_code == 201


def test_short_password_rejected_and_invite_kept(api, client):
    inv = api.ok(api.post("/invites", json={}), 201)
    assert accept(client, token_of(inv), password="short").status_code == 422
    assert client.post("/api/v1/invites/check", json={"token": token_of(inv)}).status_code == 200


def test_cli_token_is_read_only(api, client, capsys):
    from oneledger_api.cli import create_token

    create_token("owner@example.com", "Claude Desktop", detail=False, days=7)
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith("olt_")
    reader = Api(client, token)
    assert reader.get("/accounts").status_code == 200
    assert reader.post("/accounts", json={"name": "x", "kind": "CASH"}).status_code == 403
