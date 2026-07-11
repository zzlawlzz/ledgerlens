"""Sanity checks for recorded MOEX ISS fixtures (T-032).

Fixtures are produced by ``scripts/record_moex_fixtures.py`` from live ISS
data and committed so unit tests never touch the network. The adapter tests
rely on them heavily; here we only pin their shape.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "moex"
WATCHLIST = {"SBER", "GAZP", "LKOH"}


def _load(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    columns = block["columns"]
    for row in block["data"]:
        assert len(row) == len(columns), "row width must match columns"
    return [dict(zip(columns, row, strict=True)) for row in block["data"]]


def test_board_fixture_covers_ru_watchlist() -> None:
    data = _load("tqbr_securities.json")
    securities = _rows(data["securities"])
    marketdata = _rows(data["marketdata"])
    assert {row["SECID"] for row in securities} == WATCHLIST
    assert {row["SECID"] for row in marketdata} == WATCHLIST
    for row in securities:
        assert row["PREVDATE"], f"{row['SECID']} has no last trading day"
        assert row["SECNAME"]
    for row in marketdata:
        assert row["ISSUECAPITALIZATION"] > 0, f"{row['SECID']} has no capitalization"


def test_history_fixture_pages_are_consistent() -> None:
    pages = sorted(FIXTURES_DIR.glob("sber_history_p*.json"))
    assert len(pages) >= 2, "need >1 page so cursor pagination is exercised"
    total_rows = 0
    declared_total = 0
    for path in pages:
        data = _load(path.name)
        rows = _rows(data["history"])
        total_rows += len(rows)
        cursor = _rows(data["history.cursor"])[0]
        declared_total = int(cursor["TOTAL"])
        for row in rows:
            assert row["SECID"] == "SBER"
            traded = date.fromisoformat(row["TRADEDATE"])
            assert date(2026, 1, 5) <= traded <= date(2026, 7, 10)
            assert any(row.get(field) is not None for field in ("CLOSE", "LEGALCLOSEPRICE"))
    assert total_rows == declared_total
