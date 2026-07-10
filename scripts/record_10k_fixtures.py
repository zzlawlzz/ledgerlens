"""Record real 10-K HTML fixtures (gzipped) for section-extraction tests (T-010).

Usage: ``uv run python scripts/record_10k_fixtures.py``

Downloads the newest 10-K primary document for three issuers with different
EDGAR HTML generators and stores them gzip-compressed under
``tests/fixtures/edgar/10k/``. Re-run to refresh; commit the results so unit
tests stay offline.
"""

from __future__ import annotations

import asyncio
import gzip
from pathlib import Path

from adapters.edgar.client import EdgarClient

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "edgar" / "10k"
# Three different EDGAR HTML generators. JPM and XOM were rejected as the
# third issuer: their 10-Ks incorporate MD&A by reference (a ~300-char stub
# under Item 7 pointing to an exhibit/appendix).
COMPANIES = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810}


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with EdgarClient() as client:
        for ticker, cik in COMPANIES.items():
            submissions = await client.get_submissions(cik)
            recent = submissions["filings"]["recent"]
            rows = zip(
                recent["accessionNumber"],
                recent["form"],
                recent["primaryDocument"],
                strict=True,
            )
            accession, _, primary_doc = next(row for row in rows if row[1] == "10-K")
            html = await client.get_archive_document(cik, accession, primary_doc)
            path = FIXTURES_DIR / f"{ticker.lower()}_10k.htm.gz"
            path.write_bytes(gzip.compress(html, compresslevel=9))
            print(
                f"wrote {path} ({path.stat().st_size / 1024:.0f} KiB gz, "
                f"{len(html) / 1024:.0f} KiB raw, accession {accession})"
            )


if __name__ == "__main__":
    asyncio.run(main())
