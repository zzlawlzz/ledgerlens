"""EDGAR data source adapter (T-009 facts, T-010 narrative sections).

Event polling arrives with monitoring layer B (T-035); until then the method
raises a non-retryable ToolError so callers fail loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from adapters.base import DataSourceAdapter, register_adapter
from adapters.edgar.client import EdgarClient
from adapters.edgar.facts import extract_financial_facts
from adapters.edgar.sections import extract_sections_from_html, section_title
from common.errors import ToolError
from common.logging import get_logger
from common.models import Company, Event, Filing, FilingSection, FinancialFact

FACT_FORMS = ("10-K", "10-Q")
ARCHIVES_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{filename}"


def _parse_filing_date(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


@register_adapter
class EdgarAdapter(DataSourceAdapter):
    source = "edgar"

    def __init__(
        self, client: EdgarClient | None = None, *, include_business_section: bool = False
    ) -> None:
        self._client = client or EdgarClient()
        # Optional Item 1 (Business) extraction — off by default (T-010 §6).
        self._include_business_section = include_business_section

    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
        data = await self._client.get_company_tickers()
        by_ticker = {
            str(row["ticker"]).upper(): row
            for row in data.values()
            if isinstance(row, dict) and "ticker" in row
        }
        wanted = [t.upper() for t in tickers] if tickers else sorted(by_ticker)
        companies: list[Company] = []
        for ticker in wanted:
            row = by_ticker.get(ticker)
            if row is None:
                get_logger(node="edgar_adapter").warning("unknown_ticker", ticker=ticker)
                continue
            companies.append(
                Company(
                    source=self.source,
                    external_id=f"{int(row['cik_str']):010d}",
                    ticker=str(row["ticker"]).upper(),
                    name=str(row["title"]),
                )
            )
        return companies

    async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
        cik = int(company.external_id)
        submissions = await self._client.get_submissions(cik)
        recent = submissions.get("filings", {}).get("recent", {})
        columns = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
        rows = list(zip(*(recent.get(column, []) for column in columns), strict=True))
        filings: list[Filing] = []
        newest_filed: datetime | None = None
        for accession, form, filing_date_raw, report_date_raw, primary_doc in rows:
            if form not in FACT_FORMS:
                continue
            filed_at = _parse_filing_date(str(filing_date_raw))
            if filed_at and (newest_filed is None or filed_at > newest_filed):
                newest_filed = filed_at
            source_url = ARCHIVES_DOC_URL.format(
                cik=cik, accession=str(accession).replace("-", ""), filename=primary_doc
            )
            report_date = str(report_date_raw) if report_date_raw else None
            filings.append(
                Filing(
                    source_filing_id=str(accession),
                    form_type=str(form),
                    period_end=report_date,  # type: ignore[arg-type]  # pydantic parses ISO date
                    filed_at=filed_at,
                    source_url=source_url,
                    meta={"primary_document": str(primary_doc)},
                )
            )
        if newest_filed is not None:
            # ``years`` back from the newest filing (+1 quarter of slack) —
            # deterministic against recorded fixtures, no dependency on today().
            cutoff = newest_filed - timedelta(days=int(365.25 * years) + 92)
            filings = [f for f in filings if f.filed_at is None or f.filed_at >= cutoff]
        return filings

    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]:
        companyfacts = await self._client.get_companyfacts(int(company.external_id))
        allowed = {filing.source_filing_id for filing in filings}
        facts, stats = extract_financial_facts(companyfacts, allowed_accessions=allowed)
        get_logger(node="edgar_adapter").info(
            "facts_extracted",
            ticker=company.ticker,
            facts_total=stats.facts_total,
            metrics_found=len(stats.found),
            metrics_missing=stats.missing,
            skipped_unknown_filing=stats.skipped_unknown_filing,
            warning_count=len(stats.warnings),
        )
        return facts

    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
        if filing.form_type != "10-K":
            return []  # narrative comes from annual reports only (v1)
        primary_document = filing.meta.get("primary_document")
        if not primary_document:
            raise ToolError(
                f"filing {filing.source_filing_id} has no primary_document in meta",
                retryable=False,
            )
        html = await self._client.get_archive_document(
            int(company.external_id), filing.source_filing_id, str(primary_document)
        )
        result = extract_sections_from_html(html, include_business=self._include_business_section)
        log = get_logger(node="edgar_adapter")
        for warning in result.warnings:
            log.warning(
                "section_extraction_warning",
                ticker=company.ticker,
                filing=filing.source_filing_id,
                detail=warning,
            )
        return [
            FilingSection(
                source_filing_id=filing.source_filing_id,
                section=name,
                title=section_title(name),
                text=text,
            )
            for name, text in result.sections.items()
        ]

    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
        raise ToolError(
            "EDGAR event polling is implemented with monitoring layer B (T-035)",
            retryable=False,
        )
