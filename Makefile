# LedgerLens — task automation. TODO-stubs are implemented by their backlog tasks.
.PHONY: up down lint test test-integration ingest demo-ingest demo seed snapshot eval smoke db-up db-migrate db-reset bench-vector bench-inference

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
	@echo "Grafana dashboards: http://localhost:3001 (anonymous viewer, T-034)"

SNAPSHOT_DIR ?= snapshot/eval_demo

seed:  ## Restore the demo corpus without hitting EDGAR (fetches the release artifact if absent)
	@if [ ! -f "$(SNAPSHOT_DIR)/eval_demo.pgdump" ]; then \
		echo "Snapshot absent under $(SNAPSHOT_DIR) — fetching the eval-demo-snapshot artifact…"; \
		bash scripts/fetch_demo_snapshot.sh "$(SNAPSHOT_DIR)"; \
	fi
	docker compose up -d --wait postgres qdrant
	uv run alembic upgrade head
	uv run python scripts/eval_snapshot.py restore --dir $(SNAPSHOT_DIR) --clean
	@echo "Demo corpus restored from $(SNAPSHOT_DIR) (no EDGAR traffic)."

snapshot:  ## Export a fresh demo-corpus snapshot from the running stack (after a re-ingest)
	uv run python scripts/eval_snapshot.py export --dir $(SNAPSHOT_DIR)
	@echo "Snapshot written to $(SNAPSHOT_DIR)/ — upload it as the eval-demo-snapshot artifact"
	@echo "(gh workflow run eval-snapshot.yml) so 'make seed' can fetch it on a clean machine."

EVAL_PROFILE ?= ci
EVAL_BASE_URL ?= http://localhost:8000
EVAL_CONCURRENCY ?= 1

eval:  ## Run eval harness against a running stack (EVAL_PROFILE=ci|full)
	uv run python -m eval.run --profile $(EVAL_PROFILE) --base-url $(EVAL_BASE_URL) --concurrency $(EVAL_CONCURRENCY)

demo-ingest:  ## Ingest the demo set (live EDGAR with disk cache) incl. embeddings
	uv run python -m ingestion.run --source edgar --tickers AAPL,MSFT,NVDA,GOOGL,AMZN --years 3 --embed

reindex:  ## Rebuild the Qdrant index from stored sections (no source traffic)
	uv run python -m ingestion.reindex

bench-vector:  ## Benchmark pgvector vs Qdrant on the real corpus (T-037); writes benchmarks/vector/REPORT.md
	uv run --group bench python benchmarks/vector/bench.py $(BENCH_ARGS)

bench-inference:  ## Benchmark local CPU vs cloud API inference (T-037); writes benchmarks/inference/REPORT.md
	uv run --group bench python -m benchmarks.inference.bench $(BENCH_ARGS)

smoke:  ## compose up + ingest when empty + smoke_test.py (gate G1)
	docker compose up -d --wait
	uv run python scripts/smoke_test.py --auto-ingest
