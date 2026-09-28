#!/usr/bin/env bash
# Logical backup of the OneLedger schema. Store the output on encrypted storage, separate from
# the SECRET_ENCRYPTION_KEY (keys are needed to read encrypted secrets/uploads after restore).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
URL="${MIGRATION_DATABASE_URL/+psycopg/}"
mkdir -p backups && chmod 700 backups
OUT="backups/oneledger-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --no-privileges --schema=oneledger "$URL" -f "$OUT"
shasum -a 256 "$OUT" > "$OUT.sha256"
chmod 600 "$OUT" "$OUT.sha256"
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1)) and checksum."
