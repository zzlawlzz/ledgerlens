"""Live EDGAR ingestion (T-011 criterion): 3 tickers x 3 years + reference check.

Requires network access to sec.gov and a running postgres. The EDGAR disk
cache makes re-runs cheap. Reference values are the same manually verified
numbers as in T-009 unit tests.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from adapters.base import get_adapter
from common.db import get_session_factory
from ingestion.run import run_ingest

pytestmark = pytest.mark.slow

AAPL_FY_REVENUE = {
    date(2023, 9, 30): Decimal("383285000000"),
    date(2024, 9, 28): Decimal("391035000000"),
    date(2025, 9, 27): Decimal("416161000000"),
}


async def test_live_ingest_three_tickers_with_reference_values() -> None:
    adapter = get_adapter("edgar")
    reports = await run_ingest(adapter, ["AAPL", "MSFT", "NVDA"], years=3)
    by_ticker = {r.ticker: r for r in reports}
    assert all(r.ok for r in reports), [r.error for r in reports if not r.ok]
    for ticker in ("AAPL", "MSFT", "NVDA"):
        assert by_ticker[ticker].filings >= 8, f"{ticker}: too few filings"
        assert by_ticker[ticker].facts >= 50, f"{ticker}: too few facts"
        assert by_ticker[ticker].sections >= 2, f"{ticker}: too few sections"

    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT period_end, value FROM latest_facts "
                "WHERE ticker = 'AAPL' AND metric = 'revenue' AND fiscal_period = 'FY'"
            )
        )
        revenue = {period_end: Decimal(value) for period_end, value in rows}
    for period_end, expected in AAPL_FY_REVENUE.items():
        assert revenue.get(period_end) == expected, (
            f"AAPL FY revenue mismatch for {period_end}: "
            f"got {revenue.get(period_end)}, expected {expected}"
        )
