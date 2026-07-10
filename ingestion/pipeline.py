"""Idempotent ingestion pipeline (T-011).

Per company: upsert company -> fetch/upsert filings -> extract/insert facts ->
extract/upsert sections. Every write is ON CONFLICT over natural keys
(CONTRACTS.md §6), so re-running the same ingest adds zero rows. The adapter
never touches the database; persistence lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.base import DataSourceAdapter
from common.logging import get_logger
from common.models import Company, Filing

_UPSERT_COMPANY = text(
    """
    INSERT INTO companies (source, external_id, ticker, name, sector, meta)
    VALUES (:source, :external_id, :ticker, :name, :sector, CAST(:meta AS jsonb))
    ON CONFLICT (source, external_id) DO UPDATE
        SET ticker = EXCLUDED.ticker, name = EXCLUDED.name, sector = EXCLUDED.sector
    RETURNING id
    """
)

_UPSERT_FILING = text(
    """
    INSERT INTO filings (company_id, source_filing_id, form_type, period_end,
                         fiscal_year, fiscal_period, filed_at, correction_number,
                         source_url, meta)
    VALUES (:company_id, :source_filing_id, :form_type, :period_end,
            :fiscal_year, :fiscal_period, :filed_at, :correction_number,
            :source_url, CAST(:meta AS jsonb))
    ON CONFLICT (company_id, source_filing_id, correction_number) DO NOTHING
    """
)

_SELECT_FILING_IDS = text("SELECT source_filing_id, id FROM filings WHERE company_id = :company_id")

_INSERT_FACT = text(
    """
    INSERT INTO financial_facts (filing_id, company_id, metric, value, unit,
                                 period_start, period_end, fiscal_year,
                                 fiscal_period, standard, source_tag)
    VALUES (:filing_id, :company_id, :metric, :value, :unit,
            :period_start, :period_end, :fiscal_year,
            :fiscal_period, :standard, :source_tag)
    ON CONFLICT (filing_id, metric, period_end, fiscal_period, unit) DO NOTHING
    """
)

_UPSERT_SECTION = text(
    """
    INSERT INTO filing_sections (filing_id, section, title, text)
    VALUES (:filing_id, :section, :title, :text)
    ON CONFLICT (filing_id, section) DO UPDATE
        SET title = EXCLUDED.title, text = EXCLUDED.text
    """
)


@dataclass
class TickerReport:
    ticker: str
    filings: int = 0
    facts: int = 0
    sections: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def _upsert_company(session: AsyncSession, company: Company) -> int:
    import json

    company_id = (
        await session.execute(
            _UPSERT_COMPANY,
            {
                "source": company.source,
                "external_id": company.external_id,
                "ticker": company.ticker,
                "name": company.name,
                "sector": company.sector,
                "meta": json.dumps(company.meta),
            },
        )
    ).scalar_one()
    return int(company_id)


async def _upsert_filings(
    session: AsyncSession, company_id: int, filings: list[Filing]
) -> dict[str, int]:
    import json

    for filing in filings:
        await session.execute(
            _UPSERT_FILING,
            {
                "company_id": company_id,
                "source_filing_id": filing.source_filing_id,
                "form_type": filing.form_type,
                "period_end": filing.period_end,
                "fiscal_year": filing.fiscal_year,
                "fiscal_period": filing.fiscal_period,
                "filed_at": filing.filed_at,
                "correction_number": filing.correction_number,
                "source_url": filing.source_url,
                "meta": json.dumps(filing.meta),
            },
        )
    rows = await session.execute(_SELECT_FILING_IDS, {"company_id": company_id})
    return {source_filing_id: filing_id for source_filing_id, filing_id in rows}


async def ingest_company(
    adapter: DataSourceAdapter,
    session_factory: async_sessionmaker[AsyncSession],
    company: Company,
    *,
    years: int,
    skip_narrative: bool = False,
) -> TickerReport:
    """Ingest one company; raises adapter errors up (isolation is the caller's job)."""
    log = get_logger(node="ingestion")
    report = TickerReport(ticker=company.ticker or company.external_id)
    async with session_factory() as session:
        company_id = await _upsert_company(session, company)
        filings = await adapter.fetch_filings(company, years)
        filing_ids = await _upsert_filings(session, company_id, filings)
        report.filings = len(filings)
        log.info("filings_ingested", ticker=report.ticker, count=len(filings))

        facts = await adapter.extract_facts(company, filings)
        for fact in facts:
            filing_id = filing_ids.get(fact.source_filing_id)
            if filing_id is None:
                log.warning(
                    "fact_without_filing", ticker=report.ticker, accession=fact.source_filing_id
                )
                continue
            await session.execute(
                _INSERT_FACT,
                {
                    "filing_id": filing_id,
                    "company_id": company_id,
                    "metric": fact.metric,
                    "value": fact.value,
                    "unit": fact.unit,
                    "period_start": fact.period_start,
                    "period_end": fact.period_end,
                    "fiscal_year": fact.fiscal_year,
                    "fiscal_period": fact.fiscal_period,
                    "standard": fact.standard,
                    "source_tag": fact.source_tag,
                },
            )
        report.facts = len(facts)
        log.info("facts_ingested", ticker=report.ticker, count=len(facts))

        if not skip_narrative:
            for filing in filings:
                sections = await adapter.extract_sections(company, filing)
                for section in sections:
                    await session.execute(
                        _UPSERT_SECTION,
                        {
                            "filing_id": filing_ids[section.source_filing_id],
                            "section": section.section,
                            "title": section.title,
                            "text": section.text,
                        },
                    )
                report.sections += len(sections)
            log.info("sections_ingested", ticker=report.ticker, count=report.sections)
        await session.commit()
    return report
