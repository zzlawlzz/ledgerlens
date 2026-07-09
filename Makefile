# LedgerLens — task automation. TODO-stubs are implemented by their backlog tasks.
.PHONY: up down lint test ingest demo seed eval smoke

lint:  ## Static checks: format, lint, types
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy .

test:  ## Unit tests (no network, no docker)
	uv run pytest -m "not slow"

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
