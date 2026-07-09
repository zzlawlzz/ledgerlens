"""Observability, eval and monitoring tables (T-005; DDL verbatim CONTRACTS.md §6).

Revision ID: 002
Revises: 001
"""

from __future__ import annotations

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

# asyncpg cannot run multi-statement strings — each statement is executed separately.
_OBSERVABILITY_STATEMENTS = [
    """
CREATE TABLE runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'us',
  status TEXT NOT NULL,                -- 'running'|'succeeded'|'failed'|'budget_exceeded'
  plan JSONB, answer TEXT, error TEXT,
  tokens_in BIGINT NOT NULL DEFAULT 0, tokens_out BIGINT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0, latency_ms INT,
  client_meta JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
)
""",
    """
CREATE TABLE steps (
  id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  node TEXT NOT NULL,                  -- 'planner'|'worker'|'synthesizer'|'guardrail'
  worker_node TEXT,                    -- 'local'|'vps-1'
  status TEXT NOT NULL, goal TEXT, output JSONB, error TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
)
""",
    """
CREATE TABLE llm_calls (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID, step_id UUID, task_class TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL,
  tokens_in INT, tokens_out INT, cost_usd NUMERIC(12,6), latency_ms INT,
  fallback_used BOOLEAN NOT NULL DEFAULT false, error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""",
    """
CREATE TABLE tool_calls (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID, step_id UUID, tool TEXT NOT NULL, arguments JSONB,
  status TEXT NOT NULL, latency_ms INT, result_preview TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""",
    """
CREATE TABLE eval_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  git_sha TEXT, profile TEXT NOT NULL, summary JSONB,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
)
""",
    """
CREATE TABLE eval_results (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  eval_run_id BIGINT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  case_id TEXT NOT NULL, category TEXT NOT NULL, passed BOOLEAN,
  scores JSONB, run_id UUID, details JSONB
)
""",
    """
CREATE TABLE monitored_events (        -- слой B
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source TEXT NOT NULL, external_id TEXT NOT NULL,
  company_id BIGINT REFERENCES companies(id), event_type TEXT NOT NULL,
  occurred_at TIMESTAMPTZ, payload JSONB, summary TEXT, alerted_at TIMESTAMPTZ,
  UNIQUE (source, external_id)
)
""",
    """
CREATE TABLE prices (                  -- опционально (price_enrich)
  company_id BIGINT NOT NULL REFERENCES companies(id),
  trade_date DATE NOT NULL, close NUMERIC(18,6) NOT NULL,
  currency TEXT NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (company_id, trade_date, source)
)
""",
]


def upgrade() -> None:
    for statement in _OBSERVABILITY_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS prices, monitored_events, eval_results, eval_runs, "
        "tool_calls, llm_calls, steps, runs CASCADE"
    )
