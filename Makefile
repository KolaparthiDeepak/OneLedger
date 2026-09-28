# OneLedger developer commands. Local PostgreSQL runs from .data/pg (no Docker needed);
# `make compose-up` runs the same stack in Docker instead.
SHELL := /bin/bash
PGPORT ?= 54329
PGDATA := .data/pg
ENV := set -a; [ -f .env ] && source .env; set +a;

.PHONY: help setup db-start db-stop migrate owner token dev api worker web stop test test-api test-e2e lint typecheck build compose-up compose-down backup restore

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install dependencies, create .env with fresh secrets, init local DB, migrate
	uv sync
	cd apps/web && npm install --no-audit --no-fund
	uv run python scripts/setup_local.py
	$(MAKE) db-start migrate
	$(ENV) uv run oneledger-admin setup-db-role

db-start: ## Start the local PostgreSQL cluster in .data/pg
	@if [ ! -d $(PGDATA) ]; then initdb -D $(PGDATA) -U postgres --auth=trust -E UTF8 --locale=C >/dev/null; fi
	@pg_ctl -D $(PGDATA) status >/dev/null 2>&1 || LC_ALL=en_US.UTF-8 pg_ctl -D $(PGDATA) -o "-p $(PGPORT) -k /tmp -c listen_addresses=localhost" -l .data/pg.log start
	@psql -h localhost -p $(PGPORT) -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='oneledger'" | grep -q 1 || psql -h localhost -p $(PGPORT) -U postgres -qc "CREATE DATABASE oneledger"

db-stop: ## Stop the local PostgreSQL cluster
	-pg_ctl -D $(PGDATA) stop

migrate: ## Apply database migrations (uses MIGRATION_DATABASE_URL)
	$(ENV) uv run alembic upgrade head

owner: ## Create the owner account: make owner EMAIL=you@example.com NAME="Your name"
	$(ENV) uv run oneledger-admin create-owner --email "$(EMAIL)" --name "$(or $(NAME),Owner)"

token: ## Read-only MCP token: make token EMAIL=you@example.com [DETAIL=1]
	$(ENV) uv run oneledger-admin create-token --email "$(EMAIL)" --label "$(or $(LABEL),AI client)" $(if $(DETAIL),--detail,)

dev: db-start ## Run API (8000), worker and web (3000) together; Ctrl+C stops all
	@trap 'kill 0' EXIT; \
	$(ENV) uv run uvicorn oneledger_api.main:app --port 8000 --reload --reload-dir apps --reload-dir packages & \
	$(ENV) uv run oneledger-worker & \
	cd apps/web && $(ENV) ONELEDGER_API_INTERNAL_URL=$${API_BASE_URL:-http://localhost:8000} npm run dev & \
	wait

api: ## Run only the API
	$(ENV) uv run uvicorn oneledger_api.main:app --port 8000 --reload

worker: ## Run only the background worker
	$(ENV) uv run oneledger-worker

web: ## Run only the web app
	cd apps/web && $(ENV) npm run dev

stop: db-stop ## Stop local services started outside `make dev`
	-pkill -f "uvicorn oneledger_api.main:app"
	-pkill -f "oneledger-worker"

lint: ## Ruff (Python) + TypeScript
	uv run ruff check . && uv run ruff format --check .
	cd apps/web && npx tsc --noEmit

typecheck: ## mypy strict
	uv run mypy apps/api/src packages && uv run mypy apps/mcp/src

test: test-api ## Run backend tests (real PostgreSQL, database oneledger_test)

test-api:
	uv run pytest tests -q

test-e2e: ## Playwright against a throwaway oneledger_e2e database (API 8010, web 3010)
	./scripts/e2e.sh

build: ## Production build of the web app
	cd apps/web && npm run build

compose-up: ## Run everything in Docker (Postgres, migrations, API, worker, web)
	docker compose up --build -d

compose-down:
	docker compose down

backup: ## Encrypted-at-rest backup is your storage's job; this writes a custom-format dump to backups/
	./scripts/backup.sh

restore: ## Restore a dump into an ISOLATED database: make restore FILE=backups/x.dump TARGET=oneledger_restore
	./scripts/restore.sh "$(FILE)" "$(or $(TARGET),oneledger_restore)"
