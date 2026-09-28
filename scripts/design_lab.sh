#!/usr/bin/env bash
# Design lab: hot-reloading web (3010) + API (8010) on the synthetic e2e database, so UI work and
# screenshots never touch real data. Seed first with ./scripts/e2e.sh. Ctrl+C stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
mkdir -p .data/logs
DATABASE_URL="${DATABASE_URL%/*}/oneledger_e2e" uv run uvicorn oneledger_api.main:app --port 8010 --reload --reload-dir apps --reload-dir packages > .data/logs/api-lab.log 2>&1 &
API=$!
trap 'kill $API 2>/dev/null; pkill -f "next dev -p 3010" 2>/dev/null || true' EXIT
cd apps/web && NEXT_DIST_DIR=.next-lab ONELEDGER_API_INTERNAL_URL=http://localhost:8010 ALLOWED_ORIGINS=http://localhost:3010 npx next dev -p 3010
