"""Unit tests for the MOEX adapter (T-032): entities, year batches, market facts.

Hermetic: a fake client serves the recorded ISS fixtures
(``tests/fixtures/moex``, produced by ``scripts/record_moex_fixtures.py``)
and re-paginates history rows so the real cursor-following code runs.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from adapters.base import registered_sources
from adapters.moex.adapter import MoexAdapter
from adapters.moex.client import MoexClient
from common.errors import SourceUnavailableError, ToolError
from common.models import Company

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "moex"
PAGE_SIZE = 100


def _load(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _all_history_rows() -> tuple[list[str], list[list[Any]]]:
    columns: list[str] = []
    rows: list[list[Any]] = []
    for path in sorted(FIXTURES_DIR.glob("sber_history_p*.json")):
        block = _load(path.name)["history"]
        columns = columns or list(block["columns"])
        assert block["columns"] == columns
        rows.extend(block["data"])
    return columns, rows


class FakeMoexClient(MoexClient):
    """Serves recorded fixtures without network; history is re-paginated."""

    def __init__(self) -> None:  # noqa: D107 — intentionally no super().__init__
        self._board = "TQBR"

    async def get_board_securities(self) -> dict[str, Any]:
        return _load("tqbr_securities.json")

    async def get_history_page(
        self, secid: str, date_from: date, date_till: date, *, start: int = 0
    ) -> dict[str, Any]:
        assert secid == "SBER", "history fixtures cover SBER only"
        columns, rows = _all_history_rows()
        date_index = columns.index("TRADEDATE")
        in_window = [
            row
            for row in rows
            if date_from <= date.fromisoformat(str(row[date_index])) <= date_till
        ]
        return {
            "history": {"columns": columns, "data": in_window[start : start + PAGE_SIZE]},
            "history.cursor": {
                "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                "data": [[start, len(in_window), PAGE_SIZE]],
            },
        }


@pytest.fixture
def adapter() -> MoexAdapter:
    return MoexAdapter(client=FakeMoexClient())


def sber() -> Company:
    return Company(source="moex", external_id="SBER", name="Сбербанк России ПАО ао", ticker="SBER")


def test_moex_adapter_is_registered() -> None:
    assert "moex" in registered_sources()


async def test_list_entities_maps_board_row(adapter: MoexAdapter) -> None:
    companies = await adapter.list_entities(["sber", "NOPE"])
    assert len(companies) == 1
    company = companies[0]
    assert company.source == "moex"
    assert company.external_id == "SBER"
    assert company.ticker == "SBER"
    assert company.name == "Сбербанк России ПАО ао"
    assert company.meta["isin"] == "RU0009029540"
    assert company.meta["board"] == "TQBR"


async def test_list_entities_without_filter_returns_whole_board(adapter: MoexAdapter) -> None:
    companies = await adapter.list_entities()
    assert [c.ticker for c in companies] == ["GAZP", "LKOH", "SBER"]  # fixture watchlist


async def test_fetch_filings_builds_one_batch_per_year(adapter: MoexAdapter) -> None:
    filings = await adapter.fetch_filings(sber(), years=3)
    assert [f.source_filing_id for f in filings] == [
        "MOEX-EOD-SBER-2024",
        "MOEX-EOD-SBER-2025",
        "MOEX-EOD-SBER-2026",
    ]
    assert all(f.form_type == "MOEX-EOD" for f in filings)
    assert [f.fiscal_year for f in filings] == [2024, 2025, 2026]
    # Past years close on Dec 31; the current year closes on the last trading day.
    assert filings[0].period_end == date(2024, 12, 31)
    assert filings[-1].period_end == date(2026, 7, 10)  # fixture PREVDATE
    assert filings[-1].filed_at == datetime(2026, 7, 10, tzinfo=UTC)
    assert filings[-1].source_url is not None
    assert "iss.moex.com/iss/history" in filings[-1].source_url


async def test_extract_facts_daily_closes_and_market_cap(adapter: MoexAdapter) -> None:
    filings = await adapter.fetch_filings(sber(), years=1)
    facts = await adapter.extract_facts(sber(), filings)

    closes = [f for f in facts if f.metric == "close_price"]
    caps = [f for f in facts if f.metric == "market_cap"]
    assert len(closes) == 131  # trading days recorded in the fixture window
    assert len(caps) == 1

    first = min(closes, key=lambda f: f.period_end)
    assert first.period_end == date(2026, 1, 5)
    assert first.value == Decimal("298.78")
    assert first.unit == "RUB/share"
    assert first.standard == "MOEX"
    assert first.fiscal_period == "FY"  # documented temporary value (adapter docstring)
    assert first.source_tag == "CLOSE"

    cap = caps[0]
    assert cap.value == Decimal("6289357299800")
    assert cap.unit == "RUB"
    assert cap.period_end == date(2026, 7, 10)
    assert cap.source_tag == "ISSUECAPITALIZATION"

    known = {f.source_filing_id for f in filings}
    assert {fact.source_filing_id for fact in facts} <= known


async def test_extract_facts_deduplicates_by_trading_day(adapter: MoexAdapter) -> None:
    filings = await adapter.fetch_filings(sber(), years=3)
    facts = await adapter.extract_facts(sber(), filings)
    closes = [f for f in facts if f.metric == "close_price"]
    # Fixture rows all belong to 2026: earlier year windows must stay empty.
    assert len(closes) == 131
    assert len({f.period_end for f in closes}) == len(closes)
    assert {f.source_filing_id for f in closes} == {"MOEX-EOD-SBER-2026"}


async def test_extract_sections_is_empty_market_data_has_no_narrative(
    adapter: MoexAdapter,
) -> None:
    filings = await adapter.fetch_filings(sber(), years=1)
    assert await adapter.extract_sections(sber(), filings[0]) == []


async def test_poll_events_points_to_edisclosure_plan(adapter: MoexAdapter) -> None:
    with pytest.raises(ToolError, match="e-disclosure"):
        await adapter.poll_events([sber()], datetime(2026, 1, 1, tzinfo=UTC))


async def test_unknown_security_fails_loudly(adapter: MoexAdapter) -> None:
    ghost = Company(source="moex", external_id="NOPE", name="Ghost")
    with pytest.raises(ToolError, match="NOPE"):
        await adapter.fetch_filings(ghost, years=1)


async def test_iss_outage_surfaces_as_source_unavailable(tmp_path: Path) -> None:
    """Gate criterion: ISS down (emulated) -> SourceUnavailableError, not a crash."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = MoexClient(
        cache_dir=tmp_path,
        min_interval_s=0.0,
        retry_wait_base_s=0.01,
        transport=httpx.MockTransport(refuse),
    )
    async with client:
        adapter = MoexAdapter(client=client)
        with pytest.raises(SourceUnavailableError):
            await adapter.list_entities(["SBER"])
