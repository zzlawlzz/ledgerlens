"""Structured web-sourced facts, enriched into the agent's lookup path (T-045).

The web_documents cache (005) keys on the exact normalized query, so a repeat of
the SAME user question re-issues differently-phrased worker sub-queries and hits
the network again. This table stores the *facts* extracted from web results —
keyed by (entity, metric, period), not by query string — so the agent finds a
previously web-sourced value via its ordinary SQL path (sql_query reads it, like
latest_facts) and never re-searches.

Kept SEPARATE from the audited SEC schema for the same reason as web_documents:
``financial_facts`` is anchored to ``filings`` and citation-grade; web facts are
not primary filings. Each row carries the source url + domain-trust tier, so the
answer can cite them as ``[web: <domain>]`` and flag their confidence. Written by
the read-write ``app`` role; read by the read-only ``app_ro`` agent SQL path.

Revision ID: 006
Revises: 005
"""

from __future__ import annotations

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE web_facts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  entity TEXT NOT NULL,               -- as found, e.g. "AMD (Advanced Micro Devices)"
  entity_norm TEXT NOT NULL,          -- lowercased lookup key, e.g. "amd"
  metric TEXT NOT NULL,               -- canonical-ish metric, e.g. "revenue"
  period TEXT NOT NULL DEFAULT '',    -- e.g. "FY2025", "Q1 2024", '' if unspecified
  value NUMERIC(28,4),                -- numeric value in `unit` (as stated, may be scaled)
  unit TEXT NOT NULL DEFAULT '',      -- e.g. "billion USD"
  value_text TEXT,                    -- original phrasing, e.g. "$34.6 billion"
  source_url TEXT NOT NULL,
  domain TEXT NOT NULL,
  trust TEXT NOT NULL,                -- high | medium | low (domain-trust tier)
  query_norm TEXT,                    -- the search that produced it (provenance)
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  meta JSONB NOT NULL DEFAULT '{}',
  UNIQUE (entity_norm, metric, period)
)
"""
    )
    # Agent lookup is "facts for this entity + metric", freshest first.
    op.execute("CREATE INDEX web_facts_lookup_idx ON web_facts (entity_norm, metric)")
    # app_ro is the read-only role the agent's sql_query uses; grafana_ro (003)
    # may read it for a demo dashboard. Guarded so a role-less env still migrates.
    op.execute(
        """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_ro') THEN
    EXECUTE 'GRANT SELECT ON web_facts TO app_ro';
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    EXECUTE 'GRANT SELECT ON web_facts TO grafana_ro';
  END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS web_facts")
