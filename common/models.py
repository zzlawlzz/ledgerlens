"""Domain DTOs (T-007): pydantic models crossing the adapter/ingestion boundary.

Fields mirror the DDL in CONTRACTS.md §6 one-to-one, but carry natural keys
instead of database ids: a Filing is identified by ``source_filing_id``
(EDGAR accession number), facts/sections reference their filing the same way.
Adapters only return these DTOs — persistence is ingestion's job.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Company(_DomainModel):
    source: str  # 'edgar' | 'moex' | 'girbo' | 'edisclosure'
    external_id: str  # CIK (US) | INN / MOEX ticker (RU)
    name: str
    ticker: str | None = None
    sector: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Filing(_DomainModel):
    source_filing_id: str  # accession number (EDGAR) | source-specific id
    form_type: str  # '10-K' | '10-Q' | '8-K' | RU equivalents
    period_end: date | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None  # 'FY' | 'Q1'..'Q4'
    filed_at: datetime | None = None
    correction_number: int = 0
    source_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class FinancialFact(_DomainModel):
    source_filing_id: str  # links the fact to its Filing (natural key)
    metric: str  # canonical name from common.metrics
    value: Decimal
    unit: str  # 'USD' | 'RUB' | 'shares' | 'USD/share'
    period_end: date
    fiscal_period: str  # 'FY' | 'Q1'..'Q4'
    standard: str  # 'US-GAAP' | 'РСБУ'
    period_start: date | None = None
    fiscal_year: int | None = None
    source_tag: str | None = None  # actual XBRL tag / RSBU line code


class FilingSection(_DomainModel):
    source_filing_id: str
    section: str  # 'risk_factors' | 'mdna' | 'business' | RU equivalents
    text: str
    title: str | None = None


class Event(_DomainModel):
    """Disclosure event for the monitoring layer (8-K / существенный факт)."""

    source: str
    external_id: str  # unique within source — dedup key (monitored_events)
    event_type: str
    company_external_id: str | None = None
    occurred_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
