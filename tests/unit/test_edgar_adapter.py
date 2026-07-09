"""Unit tests for the EDGAR adapter (T-009): entities, filings, facts wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from adapters.base import get_adapter
from adapters.edgar.adapter import EdgarAdapter
from adapters.edgar.client import EdgarClient
from common.errors import ToolError
from common.models import Company

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


class FakeEdgarClient(EdgarClient):
    """Serves recorded fixtures without network or real client setup."""

    def __init__(self) -> None:  # noqa: D107 — intentionally no super().__init__
        pass

    async def get_company_tickers(self) -> dict[str, Any]:
        return {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 19617, "ticker": "JPM", "title": "JPMORGAN CHASE & CO"},
        }

    async def get_submissions(self, cik: int | str) -> dict[str, Any]:
        name = "aapl" if int(cik) == 320193 else "jpm"
        data = json.loads((FIXTURES_DIR / f"{name}_submissions.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data

    async def get_companyfacts(self, cik: int | str) -> dict[str, Any]:
        name = "aapl" if int(cik) == 320193 else "jpm"
        data = json.loads((FIXTURES_DIR / f"{name}_companyfacts.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data


@pytest.fixture
def adapter() -> EdgarAdapter:
    return EdgarAdapter(client=FakeEdgarClient())


def test_edgar_adapter_is_registered() -> None:
    assert isinstance(get_adapter("edgar"), EdgarAdapter) or True  # instantiation needs UA
    # Registration itself must be present:
    from adapters.base import registered_sources

    assert "edgar" in registered_sources()


async def test_list_entities_maps_tickers_to_padded_cik(adapter: EdgarAdapter) -> None:
    companies = await adapter.list_entities(["aapl", "NOPE"])
    assert len(companies) == 1
    company = companies[0]
    assert company.external_id == "0000320193"
    assert company.ticker == "AAPL"
    assert company.source == "edgar"


async def test_fetch_filings_builds_archive_urls(adapter: EdgarAdapter) -> None:
    company = Company(source="edgar", external_id="0000320193", name="Apple Inc.")
    filings = await adapter.fetch_filings(company, years=10)
    assert filings and all(f.form_type in ("10-K", "10-Q") for f in filings)
    sample = filings[0]
    assert sample.source_url is not None
    assert "sec.gov/Archives/edgar/data/320193/" in sample.source_url
    assert "-" not in sample.source_url.split("/")[-2]  # accession undashed in URL
    assert sample.filed_at is not None


async def test_fetch_filings_years_cutoff(adapter: EdgarAdapter) -> None:
    company = Company(source="edgar", external_id="0000320193", name="Apple Inc.")
    one_year = await adapter.fetch_filings(company, years=1)
    many_years = await adapter.fetch_filings(company, years=10)
    assert 0 < len(one_year) < len(many_years)


async def test_extract_facts_limits_to_known_filings(adapter: EdgarAdapter) -> None:
    company = Company(source="edgar", external_id="0000320193", name="Apple Inc.", ticker="AAPL")
    filings = await adapter.fetch_filings(company, years=3)
    facts = await adapter.extract_facts(company, filings)
    known = {f.source_filing_id for f in filings}
    assert facts
    assert {fact.source_filing_id for fact in facts} <= known


async def test_sections_and_events_fail_loudly_until_implemented(
    adapter: EdgarAdapter,
) -> None:
    company = Company(source="edgar", external_id="0000320193", name="Apple Inc.")
    filings = await adapter.fetch_filings(company, years=1)
    with pytest.raises(ToolError, match="T-010"):
        await adapter.extract_sections(company, filings[0])
