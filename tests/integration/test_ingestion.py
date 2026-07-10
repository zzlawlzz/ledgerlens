"""Integration tests for the ingestion pipeline (T-011) — fake adapter + real DB."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from adapters.base import DataSourceAdapter
from common.db import get_session_factory
from common.errors import SourceUnavailableError
from common.models import Company, Event, Filing, FilingSection, FinancialFact
from ingestion.run import run_ingest

pytestmark = pytest.mark.slow


def _company(ticker: str) -> Company:
    return Company(
        source="test", external_id=f"T011-{ticker}", ticker=ticker, name=f"{ticker} Corp"
    )


class FakeIngestAdapter(DataSourceAdapter):
    source = "test"

    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()

    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
        return [_company(t) for t in (tickers or [])]

    async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
        if company.ticker in self.failing:
            raise SourceUnavailableError(f"{company.ticker}: source is down (simulated)")
        return [
            Filing(
                source_filing_id=f"{company.ticker}-acc-1",
                form_type="10-K",
                period_end=date(2024, 12, 31),
                fiscal_period="FY",
                filed_at=datetime(2025, 2, 1, tzinfo=UTC),
                meta={"primary_document": "doc.htm"},
            )
        ]

    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]:
        return [
            FinancialFact(
                source_filing_id=filings[0].source_filing_id,
                metric="revenue",
                value=Decimal("1000"),
                unit="USD",
                period_end=date(2024, 12, 31),
                fiscal_period="FY",
                standard="US-GAAP",
            )
        ]

    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
        return [
            FilingSection(
                source_filing_id=filing.source_filing_id,
                section="risk_factors",
                title="Item 1A. Risk Factors",
                text="Synthetic risk text " * 10,
            )
        ]

    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
        return []


async def _table_counts() -> tuple[int, int, int, int]:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM companies WHERE source='test'),"
                    " (SELECT count(*) FROM filings f JOIN companies c ON c.id=f.company_id"
                    "  WHERE c.source='test'),"
                    " (SELECT count(*) FROM financial_facts ff JOIN companies c"
                    "  ON c.id=ff.company_id WHERE c.source='test'),"
                    " (SELECT count(*) FROM filing_sections fs JOIN filings f"
                    "  ON f.id=fs.filing_id JOIN companies c ON c.id=f.company_id"
                    "  WHERE c.source='test')"
                )
            )
        ).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


async def _cleanup() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source='test'"))
        await session.commit()


async def test_ingest_fills_tables_and_rerun_is_noop() -> None:
    await _cleanup()
    adapter = FakeIngestAdapter()
    reports = await run_ingest(adapter, ["AAA", "BBB"], years=3)
    assert all(r.ok for r in reports)
    first_counts = await _table_counts()
    assert first_counts == (2, 2, 2, 2)

    reports_again = await run_ingest(adapter, ["AAA", "BBB"], years=3)
    assert all(r.ok for r in reports_again)
    assert await _table_counts() == first_counts  # idempotent re-run: zero new rows
    await _cleanup()


async def test_one_failing_ticker_does_not_stop_the_rest() -> None:
    await _cleanup()
    adapter = FakeIngestAdapter(failing={"BAD"})
    reports = await run_ingest(adapter, ["AAA", "BAD", "CCC"], years=3)
    by_ticker = {r.ticker: r for r in reports}
    assert by_ticker["AAA"].ok and by_ticker["CCC"].ok
    assert not by_ticker["BAD"].ok and "simulated" in str(by_ticker["BAD"].error)
    counts = await _table_counts()
    # The failed ticker's transaction rolled back entirely — no partial rows.
    assert counts[0] == 2
    assert counts[2] == 2  # facts only for the two healthy tickers
    await _cleanup()


async def test_unknown_ticker_is_reported_not_raised() -> None:
    class EmptyAdapter(FakeIngestAdapter):
        async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
            return []

    reports = await run_ingest(EmptyAdapter(), ["NOPE"], years=1)
    assert len(reports) == 1
    assert reports[0].error is not None and "unknown ticker" in reports[0].error


async def test_skip_narrative_flag() -> None:
    await _cleanup()
    reports = await run_ingest(FakeIngestAdapter(), ["AAA"], years=1, skip_narrative=True)
    assert reports[0].ok and reports[0].sections == 0
    assert (await _table_counts())[3] == 0
    await _cleanup()
