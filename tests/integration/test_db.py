"""Integration tests for T-005: schema, roles, natural-key idempotency.

Require a running postgres (``make db-up db-migrate``); marked ``slow`` —
excluded from the default unit run, executed in CI against the service container.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from common.db import get_ro_session_factory, get_session_factory

pytestmark = pytest.mark.slow

PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def _cleanup_test_company(external_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("DELETE FROM companies WHERE source = 'test' AND external_id = :eid"),
            {"eid": external_id},
        )
        await session.commit()


async def test_schema_has_all_tables_and_view() -> None:
    factory = get_session_factory()
    expected = {
        "companies",
        "filings",
        "financial_facts",
        "filing_sections",
        "section_chunks",
        "runs",
        "steps",
        "llm_calls",
        "tool_calls",
        "eval_runs",
        "eval_results",
        "monitored_events",
        "prices",
    }
    async with factory() as session:
        rows = await session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        )
        tables = {row[0] for row in rows}
        views = await session.execute(
            text("SELECT table_name FROM information_schema.views WHERE table_schema='public'")
        )
        view_names = {row[0] for row in views}
    assert expected <= tables, f"missing tables: {expected - tables}"
    assert "latest_facts" in view_names


async def test_app_ro_selects_latest_facts_but_cannot_write() -> None:
    ro_factory = get_ro_session_factory()
    async with ro_factory() as session:
        rows = await session.execute(text("SELECT * FROM latest_facts LIMIT 1"))
        rows.all()  # SELECT on the view works (empty result is fine)

    async with ro_factory() as session:
        with pytest.raises(DBAPIError, match="(?i)permission denied"):
            await session.execute(
                text(
                    "INSERT INTO financial_facts "
                    "(filing_id, company_id, metric, value, unit, period_end, "
                    " fiscal_period, standard) "
                    "VALUES (1, 1, 'revenue', 1, 'USD', '2024-01-01', 'FY', 'US-GAAP')"
                )
            )

    # app_ro must not see observability tables either (domain-only grant).
    async with ro_factory() as session:
        with pytest.raises(DBAPIError, match="(?i)permission denied"):
            await session.execute(text("SELECT * FROM runs LIMIT 1"))


async def test_natural_keys_conflict_and_on_conflict_is_idempotent() -> None:
    await _cleanup_test_company("T005")
    factory = get_session_factory()
    async with factory() as session:
        insert_company = text(
            "INSERT INTO companies (source, external_id, name) "
            "VALUES ('test', 'T005', 'T-005 Co') "
            "ON CONFLICT (source, external_id) DO NOTHING"
        )
        await session.execute(insert_company)
        await session.execute(insert_company)  # idempotent re-run
        company_id = (
            await session.execute(
                text("SELECT id FROM companies WHERE source='test' AND external_id='T005'")
            )
        ).scalar_one()

        insert_filing = text(
            "INSERT INTO filings (company_id, source_filing_id, form_type, fiscal_period) "
            "VALUES (:cid, 'acc-1', '10-K', 'FY') "
            "ON CONFLICT (company_id, source_filing_id, correction_number) DO NOTHING"
        )
        await session.execute(insert_filing, {"cid": company_id})
        await session.execute(insert_filing, {"cid": company_id})
        filing_id = (
            await session.execute(
                text("SELECT id FROM filings WHERE company_id=:cid AND source_filing_id='acc-1'"),
                {"cid": company_id},
            )
        ).scalar_one()

        insert_fact = text(
            "INSERT INTO financial_facts "
            "(filing_id, company_id, metric, value, unit, period_end, fiscal_period, standard) "
            "VALUES (:fid, :cid, 'revenue', 100, 'USD', '2024-09-30', 'FY', 'US-GAAP') "
            "ON CONFLICT (filing_id, metric, period_end, fiscal_period, unit) DO NOTHING"
        )
        await session.execute(insert_fact, {"fid": filing_id, "cid": company_id})
        await session.execute(insert_fact, {"fid": filing_id, "cid": company_id})
        await session.commit()

        counts = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM companies WHERE source='test' "
                    "        AND external_id='T005'), "
                    "       (SELECT count(*) FROM filings WHERE company_id=:cid), "
                    "       (SELECT count(*) FROM financial_facts WHERE company_id=:cid)"
                ),
                {"cid": company_id},
            )
        ).one()
    assert tuple(counts) == (1, 1, 1)

    # A plain duplicate insert (no ON CONFLICT) must hit the UNIQUE constraint.
    async with factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO companies (source, external_id, name) "
                    "VALUES ('test', 'T005', 'Duplicate')"
                )
            )
    await _cleanup_test_company("T005")


async def test_latest_facts_picks_latest_filing_version() -> None:
    """Restatement: same metric/period in two filings — the later filed_at wins."""
    await _cleanup_test_company("T005R")
    factory = get_session_factory()
    async with factory() as session:
        company_id = (
            await session.execute(
                text(
                    "INSERT INTO companies (source, external_id, name) "
                    "VALUES ('test', 'T005R', 'Restate Co') RETURNING id"
                )
            )
        ).scalar_one()
        old_filing = (
            await session.execute(
                text(
                    "INSERT INTO filings (company_id, source_filing_id, form_type, "
                    " fiscal_period, filed_at) "
                    "VALUES (:cid, 'acc-old', '10-K', 'FY', '2024-11-01') RETURNING id"
                ),
                {"cid": company_id},
            )
        ).scalar_one()
        new_filing = (
            await session.execute(
                text(
                    "INSERT INTO filings (company_id, source_filing_id, form_type, "
                    " fiscal_period, filed_at) "
                    "VALUES (:cid, 'acc-new', '10-K', 'FY', '2025-02-01') RETURNING id"
                ),
                {"cid": company_id},
            )
        ).scalar_one()
        for filing_id, value in ((old_filing, 100), (new_filing, 120)):
            await session.execute(
                text(
                    "INSERT INTO financial_facts (filing_id, company_id, metric, value, "
                    " unit, period_end, fiscal_period, standard) "
                    "VALUES (:fid, :cid, 'revenue', :val, 'USD', '2024-09-30', 'FY', 'US-GAAP')"
                ),
                {"fid": filing_id, "cid": company_id, "val": value},
            )
        await session.commit()

        latest_value = (
            await session.execute(
                text(
                    "SELECT value FROM latest_facts "
                    "WHERE company_id = :cid AND metric = 'revenue' AND period_end='2024-09-30'"
                ),
                {"cid": company_id},
            )
        ).scalar_one()
    assert float(latest_value) == 120.0
    await _cleanup_test_company("T005R")


def test_alembic_downgrade_and_upgrade_cycle() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
