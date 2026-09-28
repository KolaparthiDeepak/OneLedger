#!/usr/bin/env bash
# Restore a dump into an isolated database and verify it. Does not switch traffic.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?usage: restore.sh <dump> [target_db]}"; TARGET="${2:-oneledger_restore}"
set -a; source .env; set +a
BASE="${MIGRATION_DATABASE_URL/+psycopg/}"; BASE="${BASE%/*}"
shasum -a 256 -c "$FILE.sha256"
psql "$BASE/postgres" -qc "DROP DATABASE IF EXISTS $TARGET WITH (FORCE)" -c "CREATE DATABASE $TARGET"
psql "$BASE/$TARGET" -qc "CREATE EXTENSION IF NOT EXISTS pg_trgm"
pg_restore --no-owner --dbname="$BASE/$TARGET" "$FILE"
psql "$BASE/$TARGET" -qc "GRANT USAGE ON SCHEMA oneledger TO oneledger_app" \
  -c "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA oneledger TO oneledger_app"
echo "Schema version: $(psql "$BASE/$TARGET" -tAc 'SELECT version_num FROM oneledger.alembic_version')"
psql "$BASE/$TARGET" -c "SELECT (SELECT count(*) FROM oneledger.transactions) AS transactions,
  (SELECT count(*) FROM oneledger.accounts) AS accounts, (SELECT count(*) FROM oneledger.transaction_allocations) AS allocations"
echo "Restored into $TARGET. Verify reports against the source before switching DATABASE_URL."
