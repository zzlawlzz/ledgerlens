"""MOEX ISS data source adapter (T-032): RU-mode market data.

Scope fixed by the backlog (Q-02): MOEX ISS supplies market metrics only —
``close_price`` per trading day plus a current ``market_cap`` snapshot, both
with ``standard='MOEX'``. RSBU fundamentals and narrative arrive later via
the GIR BO / e-disclosure adapter stubs (ARCHITECTURE.md §5.4–5.5;
``adapters/girbo/adapter.py``, ``adapters/edisclosure/adapter.py``).

Filings are synthetic: MOEX has no filing concept, but ingestion links facts
to filings by natural key (CONTRACTS.md §6), so the adapter groups market
data into one ``MOEX-EOD`` batch per calendar year
(``source_filing_id = 'MOEX-EOD-<SECID>-<year>'``).

fiscal_period note (temporary solution, documented deviation): daily market
facts do not fit the ``'FY' | 'Q1'..'Q4'`` taxonomy of
``FinancialFact.fiscal_period`` (CONTRACTS.md §6). The adapter stores
``'FY'`` with ``period_end`` set to the exact trading day; fact uniqueness
holds because every trading day has its own ``period_end``.
TODO(T-032 follow-up): extend the fiscal_period taxonomy (e.g. ``'D'``) in a
dedicated schema task instead of widening it ad hoc here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from adapters.base import DataSourceAdapter, register_adapter
from adapters.moex.client import HISTORY_URL, MoexClient, parse_block
from common.errors import ToolError
from common.logging import get_logger
from common.models import Company, Event, Filing, FilingSection, FinancialFact

FORM_TYPE = "MOEX-EOD"  # synthetic "filing": one year of end-of-day market data
STANDARD = "MOEX"  # market data, neither US-GAAP nor RSBU
# Daily close candidates in priority order; illiquid days may lack CLOSE.
CLOSE_FIELDS = ("CLOSE", "LEGALCLOSEPRICE", "WAPRICE")


def _filing_id(secid: str, year: int) -> str:
    return f"{FORM_TYPE}-{secid}-{year}"


def _close_price_fact(filing: Filing, row: dict[str, Any]) -> FinancialFact | None:
    """Daily close as a fact; None when the row carries no usable price."""
    raw_date = row.get("TRADEDATE")
    if not raw_date:
        return None
    for source_field in CLOSE_FIELDS:
        value = row.get(source_field)
        if value is None:
            continue
        return FinancialFact(
            source_filing_id=filing.source_filing_id,
            metric="close_price",
            value=Decimal(str(value)),
            unit="RUB/share",
            period_end=date.fromisoformat(str(raw_date)),
            fiscal_period="FY",  # temporary: see module docstring (daily data)
            fiscal_year=filing.fiscal_year,
            standard=STANDARD,
            source_tag=source_field,
        )
    return None


@register_adapter
class MoexAdapter(DataSourceAdapter):
    source = "moex"

    def __init__(self, client: MoexClient | None = None) -> None:
        self._client = client or MoexClient()

    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
        """Securities listed on the board (TQBR): SECID, name, sector if any.

        ISS gives no industry sector for TQBR rows (``SECTORID`` is null for
        shares), so ``sector`` is usually None; it is mapped when present.
        """
        payload = await self._client.get_board_securities()
        by_secid = {
            str(row["SECID"]).upper(): row
            for row in parse_block(payload, "securities")
            if row.get("SECID")
        }
        wanted = [t.upper() for t in tickers] if tickers else sorted(by_secid)
        companies: list[Company] = []
        for secid in wanted:
            row = by_secid.get(secid)
            if row is None:
                get_logger(node="moex_adapter").warning("unknown_ticker", ticker=secid)
                continue
            sector = row.get("SECTORID")
            meta = {
                key: value
                for key, value in (
                    ("isin", row.get("ISIN")),
                    ("board", self._client.board),
                    ("list_level", row.get("LISTLEVEL")),
                    ("short_name", row.get("SHORTNAME")),
                )
                if value is not None
            }
            companies.append(
                Company(
                    source=self.source,
                    external_id=secid,
                    ticker=secid,
                    name=str(row.get("SECNAME") or row.get("SHORTNAME") or secid),
                    sector=str(sector) if sector else None,
                    meta=meta,
                )
            )
        return companies

    async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
        """Synthetic ``MOEX-EOD`` batches: one per calendar year of market data.

        The anchor is the security's last trading day (board ``PREVDATE``) —
        deterministic against recorded fixtures, no dependency on today().
        """
        secid = company.external_id.upper()
        anchor = await self._last_trading_day(secid)
        filings: list[Filing] = []
        for year in range(anchor.year - years + 1, anchor.year + 1):
            window_from = date(year, 1, 1)
            window_till = min(date(year, 12, 31), anchor)
            filings.append(
                Filing(
                    source_filing_id=_filing_id(secid, year),
                    form_type=FORM_TYPE,
                    period_end=window_till,
                    fiscal_year=year,
                    fiscal_period="FY",
                    filed_at=datetime.combine(window_till, time.min, tzinfo=UTC),
                    source_url=HISTORY_URL.format(board=self._client.board, secid=secid),
                    meta={
                        "secid": secid,
                        "window_from": window_from.isoformat(),
                        "window_till": window_till.isoformat(),
                    },
                )
            )
        return filings

    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]:
        """Daily ``close_price`` facts per batch window + one ``market_cap`` snapshot."""
        secid = company.external_id.upper()
        facts: list[FinancialFact] = []
        for filing in filings:
            if filing.form_type != FORM_TYPE:
                continue
            window_from = date.fromisoformat(str(filing.meta["window_from"]))
            window_till = date.fromisoformat(str(filing.meta["window_till"]))
            rows = await self._client.get_history(secid, window_from, window_till)
            for row in rows:
                fact = _close_price_fact(filing, row)
                if fact is not None:
                    facts.append(fact)
        cap_fact = await self._market_cap_fact(secid, filings)
        if cap_fact is not None:
            facts.append(cap_fact)
        get_logger(node="moex_adapter").info(
            "facts_extracted",
            ticker=company.ticker,
            facts_total=len(facts),
            has_market_cap=cap_fact is not None,
        )
        return facts

    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
        """MOEX ISS carries no narrative — always empty.

        RU narrative (issuer reports, risk sections) belongs to the
        e-disclosure adapter (ARCHITECTURE.md §5.5), stubbed in
        ``adapters/edisclosure/adapter.py``.
        """
        return []

    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
        raise ToolError(
            "MOEX ISS has no disclosure event feed; material facts arrive with the "
            "e-disclosure adapter (ARCHITECTURE.md §5.5) and monitoring layer B (T-035)",
            retryable=False,
        )

    # --- helpers --------------------------------------------------------------

    async def _last_trading_day(self, secid: str) -> date:
        """Last trading day of ``secid``: board ``PREVDATE``, else ``dataversion``.

        An unknown security raises ``ToolError`` — the fallback covers only
        listed rows whose ``PREVDATE`` happens to be null.
        """
        payload = await self._client.get_board_securities()
        row = next(
            (
                r
                for r in parse_block(payload, "securities")
                if str(r.get("SECID", "")).upper() == secid
            ),
            None,
        )
        if row is None:
            raise ToolError(
                f"security {secid!r} is not listed on board {self._client.board!r}",
                retryable=False,
            )
        if row.get("PREVDATE"):
            return date.fromisoformat(str(row["PREVDATE"]))
        for version in parse_block(payload, "dataversion"):
            if version.get("trade_date"):
                return date.fromisoformat(str(version["trade_date"]))
        raise ToolError(
            f"security {secid!r} has no trading dates on board {self._client.board!r}",
            retryable=False,
        )

    async def _market_cap_fact(self, secid: str, filings: list[Filing]) -> FinancialFact | None:
        """Current capitalization snapshot (``ISSUECAPITALIZATION``) if ISS has it.

        The snapshot is dated by the last trading day and attached to the
        year batch covering it; ISS exposes no historical capitalization on
        this endpoint, so exactly one fact per ingest run is produced.
        """
        payload = await self._client.get_board_securities()
        row = next(
            (
                r
                for r in parse_block(payload, "marketdata")
                if str(r.get("SECID", "")).upper() == secid
            ),
            None,
        )
        cap = row.get("ISSUECAPITALIZATION") if row else None
        if cap is None:
            return None
        cap_date = await self._last_trading_day(secid)
        filing = next(
            (f for f in filings if f.form_type == FORM_TYPE and f.fiscal_year == cap_date.year),
            None,
        )
        if filing is None:
            return None  # snapshot falls outside the requested year windows
        return FinancialFact(
            source_filing_id=filing.source_filing_id,
            metric="market_cap",
            value=Decimal(str(cap)),
            unit="RUB",
            period_end=cap_date,
            fiscal_period="FY",  # temporary: see module docstring
            fiscal_year=cap_date.year,
            standard=STANDARD,
            source_tag="ISSUECAPITALIZATION",
        )
