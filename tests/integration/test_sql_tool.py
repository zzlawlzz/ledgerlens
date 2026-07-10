"""Integration tests for sql_query / schema_introspect (T-012) — real DB."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text

import tools.sql.core as sql_core
from common.db import get_session_factory
from tools.sql.core import reset_introspect_cache, schema_introspect, sql_query

pytestmark = pytest.mark.slow

_SEED_ROWS = 5


@pytest.fixture(autouse=True)
async def _seed_data() -> AsyncIterator[None]:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source = 'test'"))
        company_id = (
            await session.execute(
                text(
                    "INSERT INTO companies (source, external_id, ticker, name) "
                    "VALUES ('test', 'T012', 'TSTQ', 'SQL Tool Co') RETURNING id"
                )
            )
        ).scalar_one()
        filing_id = (
            await session.execute(
                text(
                    "INSERT INTO filings (company_id, source_filing_id, form_type, "
                    " fiscal_period, filed_at) "
                    "VALUES (:cid, 't012-acc', '10-K', 'FY', '2025-02-01') RETURNING id"
                ),
                {"cid": company_id},
            )
        ).scalar_one()
        for year in range(2020, 2020 + _SEED_ROWS):
            await session.execute(
                text(
                    "INSERT INTO financial_facts (filing_id, company_id, metric, value, "
                    " unit, period_end, fiscal_period, standard) "
                    "VALUES (:fid, :cid, 'revenue', :val, 'USD', :pe, 'FY', 'US-GAAP')"
                ),
                {"fid": filing_id, "cid": company_id, "val": year * 1000, "pe": date(year, 12, 31)},
            )
        await session.execute(
            text(
                "INSERT INTO filing_sections (filing_id, section, title, text) "
                "VALUES (:fid, 'risk_factors', 'Item 1A', :txt)"
            ),
            {"fid": filing_id, "txt": "very long risk text " * 100},
        )
        await session.commit()
    yield
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source = 'test'"))
        await session.commit()


async def test_happy_path_select_from_latest_facts() -> None:
    result = await sql_query(
        "SELECT ticker, period_end, value FROM latest_facts "
        "WHERE ticker = 'TSTQ' AND metric = 'revenue' ORDER BY period_end"
    )
    assert "error" not in result
    assert result["columns"] == ["ticker", "period_end", "value"]
    assert result["row_count"] == _SEED_ROWS
    assert result["truncated"] is False
    assert result["rows"][0][0] == "TSTQ"


async def test_truncation_flag_and_row_limit() -> None:
    result = await sql_query("SELECT * FROM latest_facts WHERE ticker = 'TSTQ'", row_limit=3)
    assert result["row_count"] == 3
    assert result["truncated"] is True


async def test_long_cells_are_truncated() -> None:
    result = await sql_query(
        "SELECT text FROM filing_sections fs JOIN filings f ON f.id = fs.filing_id "
        "WHERE f.source_filing_id = 't012-acc'"
    )
    cell = result["rows"][0][0]
    assert len(cell) == sql_core.MAX_CELL_CHARS + 1
    assert cell.endswith("…")


async def test_unknown_metric_returns_canonical_list_hint() -> None:
    result = await sql_query(
        "SELECT value FROM latest_facts WHERE ticker = 'TSTQ' AND metric = 'profit'"
    )
    assert "error" not in result
    assert result["row_count"] == 0
    assert "hint" in result
    assert "revenue" in result["hint"] and "net_income" in result["hint"]


async def test_unknown_column_gets_did_you_mean_hint() -> None:
    result = await sql_query("SELECT revenu FROM latest_facts")
    assert "error" in result
    assert "did you mean" in result["hint"] or "metric" in result["hint"]
    assert "schema_introspect" in result["hint"]


async def test_writing_cte_is_stopped_by_role_barrier() -> None:
    """sqlglot sees a top-level SELECT, but app_ro has no INSERT rights."""
    result = await sql_query(
        "WITH x AS (INSERT INTO companies (source, external_id, name) "
        "VALUES ('test', 'sneaky', 'X') RETURNING id) SELECT * FROM x"
    )
    assert "error" in result
    assert "read-only" in result["hint"] or "permission" in result["error"].lower()


async def test_timeout_returns_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sql_core, "STATEMENT_TIMEOUT_MS", 300)
    result = await sql_query("SELECT pg_sleep(2)")
    assert "error" in result
    assert "timeout" in result["error"].lower() or "cancel" in result["error"].lower()
    assert "Simplify" in result["hint"] or "schema_introspect" in result["hint"]


async def test_schema_introspect_contract() -> None:
    reset_introspect_cache()
    schema = await schema_introspect()
    table_names = {table["name"] for table in schema["tables"]}
    assert "latest_facts" in table_names
    latest = next(t for t in schema["tables"] if t["name"] == "latest_facts")
    assert "PRIMARY" in latest["description"]
    assert latest["columns"], "latest_facts columns must be introspected"
    assert len(schema["metrics"]) == 16
    assert len(schema["examples"]) == 3
    # Cache: second call returns the same object without re-querying.
    assert await schema_introspect() is schema


async def test_example_queries_execute_without_errors() -> None:
    schema = await schema_introspect()
    for example in schema["examples"]:
        result = await sql_query(example)
        assert "error" not in result, f"example failed: {example}\n{result}"
