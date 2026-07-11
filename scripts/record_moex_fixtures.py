"""Record shortened real MOEX ISS fixtures for tests (T-032).

Usage: ``uv run python scripts/record_moex_fixtures.py``

Run it from a node with clean access to iss.moex.com (docs/moex-ingest.md).
Fetches live ISS data through MoexClient and stores trimmed copies in
``tests/fixtures/moex/``:

- ``tqbr_securities.json`` — board snapshot (securities + marketdata +
  dataversion blocks) reduced to the RU watchlist tickers and the columns
  the adapter reads;
- ``sber_history_p*.json`` — daily history pages for SBER over a ~6 month
  window (>100 rows, so the ``history.cursor`` pagination is captured),
  reduced to the price/volume columns.

Re-run to refresh fixtures; commit the result so unit tests stay offline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from adapters.moex.client import MoexClient, parse_block

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "moex"
TICKERS = ("SBER", "GAZP", "LKOH")
HISTORY_SECID = "SBER"
HISTORY_FROM = date(2026, 1, 5)
HISTORY_TILL = date(2026, 7, 10)  # closed window: fixture stays reproducible

KEEP_COLUMNS = {
    "securities": (
        "SECID",
        "BOARDID",
        "SHORTNAME",
        "SECNAME",
        "SECTORID",
        "ISIN",
        "LISTLEVEL",
        "ISSUESIZE",
        "PREVDATE",
        "CURRENCYID",
        "SECTYPE",
    ),
    "marketdata": ("SECID", "BOARDID", "LAST", "ISSUECAPITALIZATION", "SYSTIME"),
    "history": (
        "BOARDID",
        "TRADEDATE",
        "SHORTNAME",
        "SECID",
        "LEGALCLOSEPRICE",
        "WAPRICE",
        "CLOSE",
        "VOLUME",
        "VALUE",
        "CURRENCYID",
    ),
}


def trim_block(
    payload: dict[str, Any],
    name: str,
    *,
    keep_columns: tuple[str, ...] | None = None,
    keep_secids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Reduce one ISS block to selected columns/rows, preserving its shape."""
    rows = parse_block(payload, name)
    if keep_secids is not None:
        rows = [row for row in rows if str(row.get("SECID", "")).upper() in keep_secids]
    all_columns = list(payload.get(name, {}).get("columns", []))
    columns = (
        [column for column in keep_columns if column in all_columns]
        if keep_columns is not None
        else all_columns
    )
    return {"columns": columns, "data": [[row.get(column) for column in columns] for row in rows]}


def shorten_board(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "securities": trim_block(
            payload, "securities", keep_columns=KEEP_COLUMNS["securities"], keep_secids=TICKERS
        ),
        "marketdata": trim_block(
            payload, "marketdata", keep_columns=KEEP_COLUMNS["marketdata"], keep_secids=TICKERS
        ),
        "dataversion": trim_block(payload, "dataversion"),
    }


def shorten_history_page(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "history": trim_block(payload, "history", keep_columns=KEEP_COLUMNS["history"]),
        "history.cursor": trim_block(payload, "history.cursor"),
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KiB)")


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with MoexClient() as client:
        board = shorten_board(await client.get_board_securities())
        _write(FIXTURES_DIR / "tqbr_securities.json", board)

        start = 0
        page_index = 0
        while True:
            payload = await client.get_history_page(
                HISTORY_SECID, HISTORY_FROM, HISTORY_TILL, start=start
            )
            page = shorten_history_page(payload)
            _write(FIXTURES_DIR / f"sber_history_p{page_index}.json", page)
            cursor = parse_block(payload, "history.cursor")
            rows = parse_block(payload, "history")
            if not cursor or not rows:
                break
            total = int(cursor[0].get("TOTAL") or 0)
            page_size = int(cursor[0].get("PAGESIZE") or len(rows))
            start = int(cursor[0].get("INDEX") or start) + page_size
            page_index += 1
            if start >= total:
                break


if __name__ == "__main__":
    asyncio.run(main())
