"""Sanity checks for recorded EDGAR fixtures (T-008).

Fixtures are produced by ``scripts/record_edgar_fixtures.py`` from live SEC
data and committed so unit tests never touch the network. T-009 relies on
them heavily; here we only pin their shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
TICKERS = ("aapl", "jpm")


def _load(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("ticker", TICKERS)
def test_submissions_fixture_shape(ticker: str) -> None:
    data = _load(f"{ticker}_submissions.json")
    recent = data["filings"]["recent"]
    assert recent["accessionNumber"], "no filings recorded"
    assert set(recent["form"]) <= {"10-K", "10-Q", "8-K"}
    lengths = {len(column) for column in recent.values()}
    assert len(lengths) == 1, "recent columns must be equally long"


@pytest.mark.parametrize("ticker", TICKERS)
def test_companyfacts_fixture_has_gaap_facts(ticker: str) -> None:
    data = _load(f"{ticker}_companyfacts.json")
    gaap = data["facts"]["us-gaap"]
    assert len(gaap) >= 8, f"too few tags recorded for {ticker}: {sorted(gaap)}"
    # Every kept entry is within the recording window and has a value.
    for tag_data in gaap.values():
        for entries in tag_data["units"].values():
            for entry in entries:
                assert entry["end"] >= "2020-01-01"
                assert "val" in entry
