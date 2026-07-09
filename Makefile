# LedgerLens — task automation. TODO-stubs are implemented by their backlog tasks.
.PHONY: up down lint test test-integration ingest demo seed eval smoke db-up db-migrate db-reset

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

up:  ## Start the full stack
	@echo "TODO(T-015): docker compose up"

down:  ## Stop the full stack
	@echo "TODO(T-015): docker compose down"

ingest:  ## Ingest source data (TICKERS=..., YEARS=3)
	@echo "TODO(T-011): ingestion CLI"

demo:  ## up + seed on empty DB + open UI
	@echo "TODO(T-026): demo"

seed:  ## Restore demo data snapshot without hitting EDGAR
	@echo "TODO(T-036): seed"

eval:  ## Run eval harness against a running stack
	@echo "TODO(T-029): eval harness"

smoke:  ## compose up + ingest + smoke_test.py
	@echo "TODO(T-015): smoke test"
