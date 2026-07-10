"""Ingestion CLI (T-011).

Usage:
    uv run python -m ingestion.run --source edgar --tickers AAPL,MSFT --years 3
    uv run python -m ingestion.run                     # defaults from config/app.yaml

Per-ticker isolation: one failing ticker is logged and reported, the rest
continue; exit code is 1 only when every ticker failed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from adapters.base import DataSourceAdapter, get_adapter
from common.config import load_yaml_config
from common.db import get_session_factory
from common.errors import LedgerLensError
from common.logging import configure_logging, get_logger
from ingestion.pipeline import TickerReport, ingest_company


def _default_tickers(app_config: dict[str, object]) -> list[str]:
    mode = str(app_config.get("mode", "us"))
    watchlist = app_config.get("watchlist", {})
    tickers = watchlist.get(mode, []) if isinstance(watchlist, dict) else []
    return [str(t) for t in tickers]


def format_report(reports: list[TickerReport]) -> str:
    header = f"{'ticker':<8} {'filings':>8} {'facts':>8} {'sections':>9} {'chunks':>7}  error"
    lines = [header, "-" * len(header)]
    for report in reports:
        lines.append(
            f"{report.ticker:<8} {report.filings:>8} {report.facts:>8} "
            f"{report.sections:>9} {report.chunks:>7}  {report.error or '-'}"
        )
    return "\n".join(lines)


async def run_ingest(
    adapter: DataSourceAdapter,
    tickers: list[str],
    *,
    years: int,
    skip_narrative: bool = False,
    embed: bool = False,
    write_pgvector: bool = False,
) -> list[TickerReport]:
    log = get_logger(node="ingestion")
    session_factory = get_session_factory()
    companies = {
        (company.ticker or company.external_id): company
        for company in await adapter.list_entities(tickers)
    }
    embedder = indexer = None
    if embed and not skip_narrative:
        from rag.embedding import ChunkEmbedder
        from rag.indexer import NarrativeIndexer

        embedder = ChunkEmbedder()
        indexer = NarrativeIndexer(embedder=embedder)
    reports: list[TickerReport] = []
    for ticker in tickers:
        company = companies.get(ticker.upper())
        if company is None:
            reports.append(TickerReport(ticker=ticker, error="unknown ticker for this source"))
            continue
        try:
            report = await ingest_company(
                adapter,
                session_factory,
                company,
                years=years,
                skip_narrative=skip_narrative,
            )
            if embedder is not None and indexer is not None:
                from sqlalchemy import text

                from ingestion.embed import embed_company_sections

                async with session_factory() as session:
                    company_id = (
                        await session.execute(
                            text("SELECT id FROM companies WHERE source = :s AND external_id = :e"),
                            {"s": company.source, "e": company.external_id},
                        )
                    ).scalar_one()
                embed_report = await embed_company_sections(
                    int(company_id),
                    session_factory,
                    embedder=embedder,
                    indexer=indexer,
                    write_pgvector=write_pgvector,
                )
                report.chunks = embed_report.chunks
            reports.append(report)
        except LedgerLensError as exc:
            log.error("ticker_failed", ticker=ticker, error=str(exc))
            reports.append(TickerReport(ticker=ticker, error=str(exc)))
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingestion.run")
    parser.add_argument("--source", default=None, help="data source (default: from app.yaml)")
    parser.add_argument("--tickers", default=None, help="comma-separated (default: watchlist)")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--skip-narrative", action="store_true")
    parser.add_argument("--embed", action="store_true", help="chunk+embed sections into Qdrant")
    parser.add_argument(
        "--pgvector", action="store_true", help="also store dense vectors in section_chunks"
    )
    args = parser.parse_args(argv)

    configure_logging("ingestion")
    app_config = load_yaml_config("app")
    adapter = get_adapter(args.source, app_config=app_config)
    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else _default_tickers(app_config)
    )
    if not tickers:
        print("no tickers: pass --tickers or fill watchlist in config/app.yaml")
        return 2

    reports = asyncio.run(
        run_ingest(
            adapter,
            tickers,
            years=args.years,
            skip_narrative=args.skip_narrative,
            embed=args.embed,
            write_pgvector=args.pgvector,
        )
    )
    print(format_report(reports))
    succeeded = [r for r in reports if r.ok]
    get_logger(node="ingestion").info(
        "ingest_finished",
        tickers_ok=len(succeeded),
        tickers_failed=len(reports) - len(succeeded),
        facts_total=sum(r.facts for r in reports),
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
