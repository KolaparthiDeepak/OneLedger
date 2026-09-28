"""Administrative CLI: provision the runtime DB role and the owner. There is no public signup."""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid
from urllib.parse import unquote, urlparse

from oneledger_db.models import User
from oneledger_db.session import owner_session
from oneledger_shared.config import get_settings
from sqlalchemy import create_engine, select, text

from .context import AppContext, get_context
from .security.auth import hash_password


def setup_db_role() -> None:
    """Create/refresh the restricted runtime login role from DATABASE_URL (local/dev helper)."""
    s = get_settings()
    if s.migration_database_url is None:
        sys.exit("MIGRATION_DATABASE_URL is required")
    rt = urlparse(s.database_url.get_secret_value().replace("postgresql+psycopg", "postgresql"))
    user, password = rt.username or "", unquote(rt.password or "")
    if not user.isidentifier() or not password:
        sys.exit("DATABASE_URL must contain a simple runtime role name and a password")
    engine = create_engine(s.migration_database_url.get_secret_value())
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": user}).first()
        verb = "ALTER" if exists else "CREATE"
        pw = password.replace("'", "''")
        conn.execute(text(f"{verb} ROLE {user} LOGIN PASSWORD '{pw}' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB"))
        conn.execute(text(f"GRANT oneledger_app TO {user}"))
    print(f"Runtime role '{user}' is ready (member of oneledger_app, no BYPASSRLS).")


def create_owner(email: str, name: str, timezone: str, currency: str, password: str | None) -> None:
    ctx = get_context()
    if password is None:
        password = getpass.getpass("Owner password (min 12 chars): ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords do not match")
    from .services.users import email_taken, provision_user

    with owner_session(ctx.sessions, None) as db:
        if email_taken(db, email):
            sys.exit("An account with that email already exists")
        user = provision_user(ctx, db, email=email, name=name, password=password, timezone=timezone, currency=currency)
        owner_id = user.id
    print(f"Owner created: {email} ({owner_id}). Sign in to the web app with this email and password.")


def reset_password(email: str) -> None:
    ctx = get_context()
    password = getpass.getpass("New password (min 12 chars): ")
    with owner_session(ctx.sessions, None) as db:
        row = db.execute(text("SELECT id FROM oneledger.auth_user_by_email(:e)"), {"e": email.lower()}).first()
    if row is None:
        sys.exit("No such owner")
    with owner_session(ctx.sessions, row[0]) as db:
        user = db.scalars(select(User).where(User.id == row[0])).one()
        user.password_hash = hash_password(password)
        user.failed_logins, user.locked_until = 0, None
        db.execute(
            text("UPDATE oneledger.sessions SET revoked_at = now() WHERE owner_id = :o AND revoked_at IS NULL"),
            {"o": row[0]},
        )
    print("Password reset; all sessions revoked.")


def reset_mfa(email: str) -> None:
    """Recovery procedure (run by the operator with DB access); not reachable over HTTP."""
    ctx = get_context()
    with owner_session(ctx.sessions, None) as db:
        row = db.execute(text("SELECT id FROM oneledger.auth_user_by_email(:e)"), {"e": email.lower()}).first()
    if row is None:
        sys.exit("No such owner")
    with owner_session(ctx.sessions, row[0]) as db:
        user = db.scalars(select(User).where(User.id == row[0])).one()
        user.mfa_enabled, user.totp_secret_enc = False, None
        db.execute(
            text("UPDATE oneledger.sessions SET revoked_at = now() WHERE owner_id = :o AND revoked_at IS NULL"),
            {"o": row[0]},
        )
    print("MFA reset and all sessions revoked. Re-enrol immediately after signing in.")


# (table, id column, ciphertext column, associated-data expression)
_ENCRYPTED = [
    ("users", "id", "totp_secret_enc", "id::text"),
    ("user_secrets", "id", "ciphertext", "owner_id::text || ':' || kind"),
    ("import_files", "id", "content_enc", "sha256"),
]


def rotate_encryption_key() -> None:
    """Re-encrypt every stored ciphertext with the current key version.

    Procedure: set the new key as SECRET_ENCRYPTION_KEY with a higher SECRET_KEY_VERSION, keep
    the old one in PREVIOUS_SECRET_ENCRYPTION_KEYS ("1:oldkey"), run this, verify, then remove the
    old key. Runs as the migration role because it spans every owner.
    """
    ctx = get_context()
    s = get_settings()
    if s.migration_database_url is None:
        sys.exit("MIGRATION_DATABASE_URL is required")
    box = ctx.box
    engine = create_engine(s.migration_database_url.get_secret_value())
    total = 0
    with engine.begin() as conn:
        for table, pk, col, ad in _ENCRYPTED:
            rows = conn.execute(
                text(f"SELECT {pk}, {col}, {ad} FROM oneledger.{table} WHERE {col} IS NOT NULL")  # noqa: S608
            ).all()
            for rid, blob, aad in rows:
                if box.key_version_of(bytes(blob)) == box.version:
                    continue
                plain = box.decrypt(bytes(blob), associated_data=str(aad).encode())
                conn.execute(
                    text(f"UPDATE oneledger.{table} SET {col} = :b WHERE {pk} = :i"),  # noqa: S608
                    {"b": box.encrypt(plain, associated_data=str(aad).encode()), "i": rid},
                )
                total += 1
        conn.execute(text("UPDATE oneledger.user_secrets SET key_version = :v"), {"v": box.version})
        conn.execute(
            text("UPDATE oneledger.import_files SET key_version = :v WHERE content_enc IS NOT NULL"), {"v": box.version}
        )
    print(f"Re-encrypted {total} value(s) with key version {box.version}.")


def _owner_id_for(ctx: AppContext, email: str) -> uuid.UUID:
    with owner_session(ctx.sessions, None) as db:
        row = db.execute(text("SELECT id FROM oneledger.auth_user_by_email(:e)"), {"e": email.strip().lower()}).first()
    if row is None:
        sys.exit(f"No account with email {email}")
    return uuid.UUID(str(row[0]))


def create_token(email: str, label: str, detail: bool, days: int) -> None:
    """Issue a read-only API token for an AI client (MCP). Printed once; only its hash is stored."""
    from .security.auth import SCOPE_READ, SCOPE_READ_DETAIL, issue_api_token

    ctx = get_context()
    owner_id = _owner_id_for(ctx, email)
    scopes = [SCOPE_READ, SCOPE_READ_DETAIL] if detail else [SCOPE_READ]
    with owner_session(ctx.sessions, owner_id) as db:
        token, row = issue_api_token(ctx, db, owner_id, label, scopes, days)
        expires = row.expires_at
    print(f"Read-only token for {email} ({', '.join(scopes)}), expires {expires:%Y-%m-%d}. Copy it now:")
    print(token)


def revoke_token(email: str, prefix: str) -> None:
    from datetime import UTC, datetime

    from oneledger_db.models import ApiToken

    ctx = get_context()
    owner_id = _owner_id_for(ctx, email)
    with owner_session(ctx.sessions, owner_id) as db:
        rows = db.scalars(
            select(ApiToken).where(ApiToken.token_prefix.startswith(prefix), ApiToken.revoked_at.is_(None))
        ).all()
        for t in rows:
            t.revoked_at = datetime.now(UTC)
    print(f"Revoked {len(rows)} token(s).")


def main() -> None:
    p = argparse.ArgumentParser(prog="oneledger-admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup-db-role")
    c = sub.add_parser("create-owner")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Owner")
    c.add_argument("--timezone", default="Asia/Kolkata")
    c.add_argument("--currency", default="INR")
    c.add_argument("--password-stdin", action="store_true", help="read the password from stdin (automation)")
    r = sub.add_parser("reset-password")
    r.add_argument("--email", required=True)
    sub.add_parser("rotate-encryption-key")
    m = sub.add_parser("reset-mfa")
    m.add_argument("--email", required=True)
    t = sub.add_parser("create-token", help="read-only API token for an AI client (MCP)")
    t.add_argument("--email", required=True)
    t.add_argument("--label", default="AI client")
    t.add_argument("--detail", action="store_true", help="also allow raw transaction descriptions")
    t.add_argument("--days", type=int, default=90)
    rv = sub.add_parser("revoke-token")
    rv.add_argument("--email", required=True)
    rv.add_argument("--prefix", required=True, help="the first characters of the token, e.g. olt_ab12")
    args = p.parse_args()
    if args.cmd == "setup-db-role":
        setup_db_role()
    elif args.cmd == "create-owner":
        pw = sys.stdin.readline().rstrip("\n") if args.password_stdin else None
        create_owner(args.email, args.name, args.timezone, args.currency, pw)
    elif args.cmd == "reset-password":
        reset_password(args.email)
    elif args.cmd == "rotate-encryption-key":
        rotate_encryption_key()
    elif args.cmd == "reset-mfa":
        reset_mfa(args.email)
    elif args.cmd == "create-token":
        create_token(args.email, args.label, args.detail, args.days)
    elif args.cmd == "revoke-token":
        revoke_token(args.email, args.prefix)


if __name__ == "__main__":
    main()
