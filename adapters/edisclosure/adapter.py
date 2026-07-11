"""e-disclosure.ru adapter stub — RU narrative and events (not implemented).

Plan (ARCHITECTURE.md §5.5; scope fixed by Q-02 — out of T-032):

- source: Interfax corporate disclosure center (``e-disclosure.ru``) for
  public issuers: quarterly/annual issuer reports plus material facts
  (существенные факты) — the direct RU analogue of 8-K;
- ``extract_sections`` supplies RAG raw material: risk and results-review
  sections of issuer reports (RU counterpart of risk_factors / MD&A);
- ``poll_events`` feeds monitoring layer B with material facts (pairs with
  T-035, which wires the US 8-K flow);
- no official public API — scraping must be resilient and degrade with
  ``SourceUnavailableError``.

The class is intentionally NOT registered in the adapter registry: it must
not be reachable via ``get_adapter`` until it works. Registration happens in
``adapters/__init__.py`` together with the implementation task.
"""

from __future__ import annotations

from datetime import datetime

from adapters.base import DataSourceAdapter
from common.models import Company, Event, Filing, FilingSection, FinancialFact

_PLAN = "e-disclosure adapter is a planned extension (ARCHITECTURE.md §5.5); see module docstring"


class EdisclosureAdapter(DataSourceAdapter):
    """Stub proving the extension point; every method raises NotImplementedError."""

    source = "edisclosure"

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
