# LedgerLens — task automation. TODO-stubs are implemented by their backlog tasks.
.PHONY: up down lint test test-integration ingest demo-ingest demo seed eval smoke db-up db-migrate db-reset

lint:  ## Static checks: format, lint, types
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy .

test:  ## Unit tests (no network, no docker)
	uv run pytest -m "not slow"

test-integration:  ## Integration tests (require running postgres, see db-up)
	uv run pytest -m slow

db-up:  ## Start postgres and wait until healthy
	docker compose up -d --wait postgres

db-migrate:  ## Apply alembic migrations
	uv run alembic upgrade head

db-reset:  ## Destroy volumes and re-create the schema from scratch
	docker compose down -v
	$(MAKE) db-up db-migrate

up:  ## Start the full stack incl. local LLM (profile "local")
	docker compose --profile local up -d --wait

up-no-local:  ## Start without the local LLM (set LOCAL_MODEL= empty in .env)
	docker compose up -d --wait

down:  ## Stop the full stack
	docker compose --profile local down

TICKERS ?= AAPL,MSFT,NVDA
YEARS ?= 3

ingest:  ## Ingest source data (TICKERS=..., YEARS=...)
	uv run python -m ingestion.run --source edgar --tickers $(TICKERS) --years $(YEARS)

demo:  ## Full stack up + ingest-on-empty + smoke + UI URL (gate G2)
	docker compose --profile local up -d --wait
	uv run python scripts/smoke_test.py --auto-ingest
	@echo ""
	@echo "LedgerLens UI: http://localhost:3000"

seed:  ## Restore demo data snapshot without hitting EDGAR
	@echo "TODO(T-036): seed"

eval:  ## Run eval harness against a running stack
	@echo "TODO(T-029): eval harness"

demo-ingest:  ## Ingest the demo set (live EDGAR with disk cache) incl. embeddings
	uv run python -m ingestion.run --source edgar --tickers AAPL,MSFT,NVDA,GOOGL,AMZN --years 3 --embed

reindex:  ## Rebuild the Qdrant index from stored sections (no source traffic)
	uv run python -m ingestion.reindex

smoke:  ## compose up + ingest when empty + smoke_test.py (gate G1)
	docker compose up -d --wait
	uv run python scripts/smoke_test.py --auto-ingest
