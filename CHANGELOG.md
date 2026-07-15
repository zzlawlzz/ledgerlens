# Changelog

All notable changes to LedgerLens, organised by delivery **gate** (G1–G4) as
defined in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) §2. Task IDs (`T-0xx`)
reference [BACKLOG.md](BACKLOG.md).

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Dates are the gate completion dates; this project has not yet cut a tagged
release — v1.0 lands at gate **G4**.

## [Unreleased] — towards G4 (v1.0)

Polish & packaging (Phase 4). In progress.

### Added
- **Monitoring layer B** (T-035): source-agnostic event ingest/summarize
  endpoints (`/api/monitor/*`) with dedup, guardrailed summaries, daily budget
  and ≤1 concurrency; n8n workflow under an opt-in `monitoring` profile;
  Telegram alerting (delivery proven live via egress proxy).
- **Grafana observability** (T-034): Operations, Session drill-down and Quality
  dashboards over a read-only role.
- **Demo mode & public hardening** (T-036): admission limits
  (rate/size/concurrency/daily-cost) under `BUDGET_PROFILE=demo`; security pass
  (non-root, docs-off, nginx security headers, CORS deny-by-default);
  `make seed`/`make snapshot` to restore the demo corpus without EDGAR; public
  UI banner; live public demo at https://ledgerlens.space/app/ — a workstation
  backend exposed through a small VPS door (DNS-only + Let's Encrypt, no CDN
  proxy in the SSE path). See [migration notes](docs/migration-workstation.md).
- **Benchmarks** (T-037): inference CPU-vs-API harness with live DeepSeek numbers
  ([report](benchmarks/inference/REPORT.md)) and vector-store pgvector-vs-Qdrant
  benchmark ([report](benchmarks/vector/REPORT.md)).
- **Bilingual documentation** (T-038): EN [README.md](README.md) +
  RU [README.ru.md](README.ru.md); this changelog.
- **Presentation site** (T-039): bilingual (EN/RU) static site published at
  https://ledgerlens.space via GitHub Pages.

### In progress
- **T-037**: local CPU inference part (2–3 Ollama candidates) + ADR-3 close-out
  — pending the home EPYC node.
- **T-040** (gate G4): the v1.0 clean-machine release — a tagged release, GitHub
  Release notes and branch protection remain.

## [G3] — Depth markers (Phase 3)

Signals that this is not a pet project.

### Added
- **Eval-in-CI** (T-028/T-029/T-030): 41-case golden set, RAGAS/DeepEval +
  LLM-as-a-judge metrics, gated in GitHub Actions with thresholds in
  `config/eval-thresholds.yaml`; scores published to the Grafana Quality board.
- **A2A worker split** (T-023/T-031): the worker runs as a separate service over
  the A2A contract; the dispatcher supports multiple nodes with local-preferred
  failover. A live two-node deploy over an AmneziaWG mesh (home EPYC + FI VPS) —
  with a remote worker step, failover trace and perimeter security verified
  end-to-end — closes gate **G3**.
- **RAG groundedness** (T-041): synthesis is constrained to the retrieved
  context — a deterministic ungrounded-claim stripper plus a fix for a
  placeholder-leak in the ReAct worker. `faithfulness` is promoted to a blocking
  eval threshold (≥ 0.7), validated by two consecutive green full-eval runs on a
  self-hosted runner.
- **MCP tool servers** (T-027): `sql_query`/`rag_search`/`price_enrich` are
  full MCP servers; the worker connects as an MCP client (lib mode for tests).
- **RU data source** (T-032): MOEX ISS adapter behind the pluggable
  `DataSourceAdapter` interface (SBER/GAZP/LKOH), with the informational-use
  disclaimer surfaced in the UI.
- **Price enrichment** (T-033): end-of-day prices via Alpha Vantage (Q-19),
  cached, with relative-date handling.

## [G2] — MVP (Phase 2)

A working product worth showing.

### Added
- **Plan-and-Execute orchestrator** (LangGraph) over ReAct workers, delegating
  via the A2A interface.
- **RAG tool** `rag_search`: hybrid retrieval + reranking over narrative filing
  sections, returning citable source chunks (Qdrant primary vector store).
- **Model Router**: tiered inference — local CPU model for cheap steps, cloud
  API (DeepSeek) for heavy reasoning, provider-agnostic with fallback.
- **Web frontend** with a live AG-UI stream of agent steps.
- **Non-advice guardrail** on the final synthesized answer.
- **Self-correction**: at least one scenario where the agent catches an
  empty/contradictory result and re-plans the step
  ([demo/self_correction.md](demo/self_correction.md)).

## [G1] — Vertical slice (Phase 1)

Type a question → get a sensible answer over real EDGAR data. End-to-end.

### Added
- `DataSourceAdapter` interface + EDGAR implementation.
- Ingestion of structured facts + a few narrative sections into Postgres.
- One ReAct worker agent with the `sql_query` tool.
- Minimal DB schema (ARCHITECTURE §4).
- Bare chat endpoint: question → plan → SQL → answer, all in Docker Compose.
