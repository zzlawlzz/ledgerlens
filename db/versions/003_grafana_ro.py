"""Grafana read-only role grafana_ro (T-034; same pattern as app_ro in 001).

Grafana dashboards are exposed to anonymous viewers, and any Grafana user
with datasource access can POST arbitrary SQL through /api/ds/query — the
only real safety boundary is the database role. grafana_ro therefore gets
SELECT on the observability/eval tables the dashboards read and nothing
else: no domain tables, no write privileges.

Revision ID: 003
Revises: 002
"""

from __future__ import annotations

from alembic import op

from common.config import get_settings
from common.errors import ConfigError

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

# Exactly what observability/dashboards/*.json query (db/versions/002 schema).
_GRAFANA_TABLES = "runs, steps, llm_calls, tool_calls, eval_runs, eval_results"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    settings = get_settings()
    if not settings.grafana_ro_password:
        raise ConfigError("GRAFANA_RO_PASSWORD must be set to create the grafana_ro role (T-034)")
    password_literal = _quote_literal(settings.grafana_ro_password)
    op.execute(
        f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro LOGIN PASSWORD {password_literal};
  ELSE
    ALTER ROLE grafana_ro WITH LOGIN PASSWORD {password_literal};
  END IF;
END
$$;
"""
    )
    op.execute(f'GRANT CONNECT ON DATABASE "{settings.postgres_db}" TO grafana_ro')
    op.execute("GRANT USAGE ON SCHEMA public TO grafana_ro")
    op.execute(f"GRANT SELECT ON {_GRAFANA_TABLES} TO grafana_ro")


def downgrade() -> None:
    op.execute(
        """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    EXECUTE 'DROP OWNED BY grafana_ro';
    EXECUTE 'DROP ROLE grafana_ro';
  END IF;
END
$$;
"""
    )
