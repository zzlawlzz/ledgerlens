"""Record shortened real EDGAR fixtures for tests (T-008/T-009).

Usage: ``uv run python scripts/record_edgar_fixtures.py``

Fetches live submissions + companyfacts for two companies from different
industries (different XBRL tag sets) through EdgarClient and stores trimmed
copies in ``tests/fixtures/edgar/``:

- submissions: only 10-K / 10-Q / 8-K rows of ``filings.recent`` (max 40);
- companyfacts: only tags from the canonical metric dictionary
  (common.metrics), only USD / USD-per-share / shares units, periods
  ending 2020-01-01 or later.

Re-run to refresh fixtures; commit the result so unit tests stay offline.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from adapters.edgar.client import EdgarClient
from common.metrics import METRICS

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "edgar"
COMPANIES = {"AAPL": 320193, "JPM": 19617}
KEEP_FORMS = ("10-K", "10-Q", "8-K")
KEEP_UNITS = ("USD", "USD/shares", "shares")
MIN_PERIOD_END = "2020-01-01"
MAX_RECENT_ROWS = 40
RECENT_COLUMNS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "form",
    "primaryDocument",
    "primaryDocDescription",
)


def _wanted_tags() -> dict[str, set[str]]:
    """Namespace -> tag names referenced by the canonical dictionary."""
    namespaces: dict[str, set[str]] = {"us-gaap": set(), "dei": set()}
    for metric in METRICS.values():
        for tag in metric.gaap_tags:
            if ":" in tag:
                namespace, name = tag.split(":", 1)
                namespaces.setdefault(namespace, set()).add(name)
            else:
                namespaces["us-gaap"].add(tag)
    return namespaces


def shorten_submissions(data: dict[str, Any]) -> dict[str, Any]:
    recent = data["filings"]["recent"]
    forms: list[str] = recent["form"]
    keep_indexes = [i for i, form in enumerate(forms) if form in KEEP_FORMS][:MAX_RECENT_ROWS]
    trimmed_recent = {
        column: [recent[column][i] for i in keep_indexes]
        for column in RECENT_COLUMNS
        if column in recent
    }
    return {
        "cik": data["cik"],
        "name": data.get("name"),
        "tickers": data.get("tickers", []),
        "sic": data.get("sic"),
        "sicDescription": data.get("sicDescription"),
        "filings": {"recent": trimmed_recent},
    }


def shorten_companyfacts(data: dict[str, Any]) -> dict[str, Any]:
    wanted = _wanted_tags()
    facts_out: dict[str, Any] = {}
    for namespace, tags in wanted.items():
        source_ns = data.get("facts", {}).get(namespace, {})
        ns_out: dict[str, Any] = {}
        for tag in sorted(tags):
            tag_data = source_ns.get(tag)
            if tag_data is None:
                continue
            units_out: dict[str, Any] = {}
            for unit, entries in tag_data.get("units", {}).items():
                if unit not in KEEP_UNITS:
                    continue
                kept = [e for e in entries if str(e.get("end", "")) >= MIN_PERIOD_END]
                if kept:
                    units_out[unit] = kept
            if units_out:
                ns_out[tag] = {"label": tag_data.get("label"), "units": units_out}
        if ns_out:
            facts_out[namespace] = ns_out
    return {"cik": data["cik"], "entityName": data.get("entityName"), "facts": facts_out}


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with EdgarClient() as client:
        for ticker, cik in COMPANIES.items():
            submissions = shorten_submissions(await client.get_submissions(cik))
            companyfacts = shorten_companyfacts(await client.get_companyfacts(cik))
            for suffix, payload in (
                ("submissions", submissions),
                ("companyfacts", companyfacts),
            ):
                path = FIXTURES_DIR / f"{ticker.lower()}_{suffix}.json"
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    asyncio.run(main())
