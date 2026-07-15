"""Durable cache for web-searched information (T-043).

The web_search tool (tools/web_search) looks facts up on the open web when the
loaded corpus can't answer, tags each source by a domain-trust tier, and caches
the findings here so a repeat query is served from the DB instead of hitting the
network again — the same cache-first discipline as the ``prices`` table behind
price_enrich.

Kept deliberately SEPARATE from the audited SEC/MOEX schema (``financial_facts``
is anchored to ``filings``): web sources are not primary filings and must not
pollute the citation-grade financial data. ``trust`` records the source tier and
``retrieved_at`` drives freshness (TTL in config/web_search.yaml). Written by the
read-write ``app`` role (not the read-only agent SQL path).

Revision ID: 005
Revises: 004
"""

from __future__ import annotations

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE web_documents (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  query_norm TEXT NOT NULL,          -- normalized query (cache key)
  url TEXT NOT NULL,
  domain TEXT NOT NULL,
  title TEXT,
  snippet TEXT,
  trust TEXT NOT NULL,               -- high | medium | low (domain-trust tier)
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  meta JSONB NOT NULL DEFAULT '{}',
  UNIQUE (query_norm, url)
)
"""
    )
    # Cache read is "rows for this query fresher than the TTL", newest first.
    op.execute(
        "CREATE INDEX web_documents_query_fresh_idx "
        "ON web_documents (query_norm, retrieved_at DESC)"
    )
    # grafana_ro (created in 003) may read the cache for a demo dashboard.
    op.execute(
        """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    EXECUTE 'GRANT SELECT ON web_documents TO grafana_ro';
  END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS web_documents")
