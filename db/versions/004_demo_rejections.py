"""Public-demo rejection events for observability (T-036 §1).

The demo admission limiter (``orchestrator.demo_limits``) rejects overlong
questions, rate-limited callers, concurrency overflow and daily-cost-cap hits.
Those refusals were in-process only and vanished; this table gives them a
durable, Grafana-visible home (one row per rejection, mirroring the
row-per-event shape of ``llm_calls`` / ``tool_calls``).

Privacy: we record only the *kind* of limit and the HTTP status — never the
caller's IP or the question text. grafana_ro gets SELECT so the operations
dashboard can chart rejections over time.

Revision ID: 004
Revises: 003
"""

from __future__ import annotations

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE demo_rejections (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  -- kind: question_too_long | rate_limited | concurrency_limit | daily_cost_cap
  kind TEXT NOT NULL,
  status_code INT NOT NULL,        -- 413 | 429 | 503
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
    )
    op.execute("CREATE INDEX demo_rejections_created_at_idx ON demo_rejections (created_at)")
    # grafana_ro exists by now (003 creates it, failing hard without a password),
    # so the operations dashboard can read the new table (same boundary as 003).
    op.execute(
        """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    EXECUTE 'GRANT SELECT ON demo_rejections TO grafana_ro';
  END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS demo_rejections")
