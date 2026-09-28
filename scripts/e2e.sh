#!/usr/bin/env bash
# Full-stack browser tests against a throwaway database. Never touches the dev/prod database.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/e2e_reset.sh >/dev/null
set -a; source .env; set +a
mkdir -p .data/logs .data/screenshots
DATABASE_URL="${DATABASE_URL%/*}/oneledger_e2e" uv run uvicorn oneledger_api.main:app --port 8010 > .data/logs/api-e2e.log 2>&1 &
API=$!
DATABASE_URL="${DATABASE_URL%/*}/oneledger_e2e" uv run oneledger-worker > .data/logs/worker-e2e.log 2>&1 &
WORKER=$!
(cd apps/web && npm run build >/dev/null && ONELEDGER_API_INTERNAL_URL=http://localhost:8010 ALLOWED_ORIGINS=http://localhost:3010 npx next start -p 3010 > ../../.data/logs/web-e2e.log 2>&1) &
WEB=$!
trap 'kill $API $WORKER $WEB 2>/dev/null; pkill -f "next start -p 3010" 2>/dev/null || true' EXIT
for _ in $(seq 1 90); do curl -sf localhost:3010/login >/dev/null && break; sleep 2; done
cd apps/web && npx playwright test
