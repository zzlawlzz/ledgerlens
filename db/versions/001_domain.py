"""Domain schema v1 + app_ro role (T-005; DDL verbatim from CONTRACTS.md §6).

Revision ID: 001
Revises: -
"""

from __future__ import annotations

from alembic import op

from common.config import get_settings
from common.errors import ConfigError

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

# asyncpg cannot run multi-statement strings — each statement is executed separately.
_DOMAIN_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
CREATE TABLE companies (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source TEXT NOT NULL,                -- 'edgar'|'moex'|'girbo'|'edisclosure'
  external_id TEXT NOT NULL,           -- CIK | ИНН | тикер MOEX
  ticker TEXT, name TEXT NOT NULL, sector TEXT,
  meta JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, external_id)
)
""",
    """
CREATE TABLE filings (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  source_filing_id TEXT NOT NULL,      -- accession number (EDGAR) | id источника
  form_type TEXT NOT NULL,             -- '10-K'|'10-Q'|'8-K'|'БФО'|'сущ.факт'|...
  period_end DATE, fiscal_year INT, fiscal_period TEXT,   -- 'FY'|'Q1'..'Q4'
  filed_at TIMESTAMPTZ, correction_number INT NOT NULL DEFAULT 0,
  source_url TEXT, meta JSONB NOT NULL DEFAULT '{}',
  UNIQUE (company_id, source_filing_id, correction_number)
)
""",
    """
CREATE TABLE financial_facts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  filing_id BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  -- денормализация для SQL-агента; CASCADE обязателен (T-005, см. CONTRACTS §6)
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  metric TEXT NOT NULL,                -- каноническое имя (CONTRACTS §7)
  value NUMERIC(28,4) NOT NULL, unit TEXT NOT NULL,      -- 'USD'|'RUB'|'shares'|'USD/share'
  period_start DATE, period_end DATE NOT NULL,
  fiscal_year INT, fiscal_period TEXT NOT NULL,
  standard TEXT NOT NULL,              -- 'US-GAAP'|'РСБУ'
  source_tag TEXT,                     -- исходный XBRL-тег / код строки РСБУ
  UNIQUE (filing_id, metric, period_end, fiscal_period, unit)
)
""",
    "CREATE INDEX ix_facts_lookup ON financial_facts "
    "(company_id, metric, fiscal_period, period_end)",
    # Один и тот же факт легитимно повторяется в разных filings (рестейтменты).
    # Агент по умолчанию читает представление «последняя версия факта»:
    """
CREATE VIEW latest_facts AS
SELECT DISTINCT ON (ff.company_id, ff.metric, ff.period_end, ff.fiscal_period, ff.unit)
       ff.*, c.ticker, c.name AS company_name, f.form_type, f.filed_at, f.source_url
FROM financial_facts ff
JOIN companies c ON c.id = ff.company_id
JOIN filings f ON f.id = ff.filing_id
ORDER BY ff.company_id, ff.metric, ff.period_end, ff.fiscal_period, ff.unit,
         f.filed_at DESC, f.correction_number DESC
""",
    """
CREATE TABLE filing_sections (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  filing_id BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  section TEXT NOT NULL,               -- 'risk_factors'|'mdna'|'business'|RU-эквиваленты
  title TEXT, text TEXT NOT NULL,
  UNIQUE (filing_id, section)
)
""",
    """
CREATE TABLE section_chunks (          -- текст чанков + pgvector (для бенчмарка и связки)
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  section_id BIGINT NOT NULL REFERENCES filing_sections(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL, text TEXT NOT NULL,
  embedding vector(1024),              -- размерность = ADR-6; менять только миграцией
  UNIQUE (section_id, chunk_index)
)
""",
]

_DOMAIN_TABLES = "companies, filings, financial_facts, filing_sections, section_chunks"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    for statement in _DOMAIN_STATEMENTS:
        op.execute(statement)

    settings = get_settings()
    if not settings.postgres_ro_password:
        raise ConfigError(
            "POSTGRES_RO_PASSWORD must be set to create the app_ro role (CONTRACTS.md §6)"
        )
    password_literal = _quote_literal(settings.postgres_ro_password)
    op.execute(
        f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_ro') THEN
    CREATE ROLE app_ro LOGIN PASSWORD {password_literal};
  ELSE
    ALTER ROLE app_ro WITH LOGIN PASSWORD {password_literal};
  END IF;
END
$$;
"""
    )
    op.execute(f'GRANT CONNECT ON DATABASE "{settings.postgres_db}" TO app_ro')
    op.execute("GRANT USAGE ON SCHEMA public TO app_ro")
    op.execute(f"GRANT SELECT ON {_DOMAIN_TABLES}, latest_facts TO app_ro")


def downgrade() -> None:
    op.execute(
        """
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_ro') THEN
    EXECUTE 'DROP OWNED BY app_ro';
    EXECUTE 'DROP ROLE app_ro';
  END IF;
END
$$;
"""
    )
    op.execute("DROP VIEW IF EXISTS latest_facts")
    op.execute(f"DROP TABLE IF EXISTS {_DOMAIN_TABLES} CASCADE")
