"""Verify numeric golden expectations against the live database (T-028).

Catches corpus/golden drift. Convention: a ``numeric_sql`` case id encodes
its source as ``<ticker>_<metric>_fy<year>`` — this script recomputes the
value from ``latest_facts`` and compares within the case tolerance, so a
re-ingested corpus that diverges from the golden file fails loudly.

Usage: uv run python -m eval.verify_golden
Exit 1 when any expectation diverges beyond its tolerance.
"""

from __future__ import annotations

import asyncio
import re
import sys

from sqlalchemy import text

from common.db import get_session_factory
from eval.golden.schema import NumericExpectation, load_golden

_ID_PATTERN = re.compile(r"^(?P<ticker>[a-z]+)_(?P<metric>[a-z_]+?)_fy(?P<year>\d{4})")


async def _db_value(ticker: str, metric: str, fiscal_year: int) -> float | None:
    factory = get_session_factory()
    async with factory() as session:
        value = (
            await session.execute(
                text(
                    "SELECT value FROM latest_facts WHERE ticker = :t AND metric = :m "
                    "AND fiscal_year = :y AND fiscal_period = 'FY' "
                    "ORDER BY period_end DESC LIMIT 1"
                ),
                {"t": ticker.upper(), "m": metric, "y": fiscal_year},
            )
        ).scalar()
    return float(value) if value is not None else None


def _parse_id(case_id: str) -> tuple[str, str, int] | None:
    match = _ID_PATTERN.match(case_id)
    if not match:
        return None
    return match["ticker"], match["metric"], int(match["year"])


async def verify() -> int:
    golden = load_golden()
    problems: list[str] = []
    checked = 0
    for case in golden.cases:
        if case.category != "numeric_sql":
            continue
        expected = case.typed_expected()
        assert isinstance(expected, NumericExpectation)
        parsed = _parse_id(case.id)
        if parsed is None:
            problems.append(f"{case.id}: id does not follow <ticker>_<metric>_fy<year>")
            continue
        ticker, metric, year = parsed
        actual = await _db_value(ticker, metric, year)
        if actual is None:
            problems.append(f"{case.id}: no DB row for {ticker}/{metric}/FY{year}")
            continue
        if expected.value is not None:
            drift = abs(actual - expected.value) / max(abs(actual), 1e-9) * 100
            if drift > expected.tolerance_pct:
                problems.append(
                    f"{case.id}: golden={expected.value} db={actual} drift={drift:.2f}%"
                )
        checked += 1
    print(f"verified {checked} numeric cases against the database")
    if problems:
        print("GOLDEN DRIFT:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("golden numeric expectations match the corpus")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify()))
