"""GIR BO adapter stub — RSBU fundamentals for the RU mode (not implemented).

Plan (ARCHITECTURE.md §5.4; scope fixed by Q-02 — out of T-032):

- source: ``bo.nalog.gov.ru`` (state register of accounting statements,
  RSBU, single legal entity — not consolidated IFRS); statements since 2019;
- no clean public REST API — the implementation must tolerate partially or
  fully blocked disclosures (RF Government Decree No. 395/2022 lets
  companies restrict third-party access; sanctioned issuers may be removed
  under Decree No. 35/2020) and degrade with ``SourceUnavailableError``;
- ``list_entities`` keys off INN (``Company.external_id``);
- ``extract_facts`` maps RSBU line codes (``common.metrics`` ``rsbu_codes``,
  already reserved in the dictionary) to canonical metrics with
  ``standard='РСБУ'``;
- narrative stays with the e-disclosure adapter (§5.5).

The class is intentionally NOT registered in the adapter registry: it must
not be reachable via ``get_adapter`` until it works. Registration happens in
``adapters/__init__.py`` together with the implementation task.
"""

from __future__ import annotations

from datetime import datetime

from adapters.base import DataSourceAdapter
from common.models import Company, Event, Filing, FilingSection, FinancialFact

_PLAN = "GIR BO adapter is a planned extension (ARCHITECTURE.md §5.4); see module docstring"


class GirboAdapter(DataSourceAdapter):
    """Stub proving the extension point; every method raises NotImplementedError."""

    source = "girbo"

    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
        raise NotImplementedError(_PLAN)

    async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
        raise NotImplementedError(_PLAN)

    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]:
        raise NotImplementedError(_PLAN)

    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
        raise NotImplementedError(_PLAN)

    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
        raise NotImplementedError(_PLAN)
