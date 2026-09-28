#!/usr/bin/env bash
# Recreate the throwaway e2e database with one owner. Never touches the dev/prod database.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
BASE=${MIGRATION_DATABASE_URL%/*}
psql "${BASE/+psycopg/}/postgres" -qc "DROP DATABASE IF EXISTS oneledger_e2e WITH (FORCE)" -c "CREATE DATABASE oneledger_e2e"
export MIGRATION_DATABASE_URL="$BASE/oneledger_e2e" DATABASE_URL="${DATABASE_URL%/*}/oneledger_e2e"
uv run alembic upgrade head >/dev/null 2>&1
echo "${E2E_PASSWORD:-e2e-owner-password-123}" | uv run oneledger-admin create-owner --email "${E2E_EMAIL:-e2e@oneledger.local}" --name Asha --password-stdin
