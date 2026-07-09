"""Contract tests for domain DTOs (T-007): lossless JSON round-trip."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from common.models import Company, Event, Filing, FilingSection, FinancialFact

SAMPLES: list[BaseModel] = [
    Company(
        source="edgar",
        external_id="0000320193",
        name="Apple Inc.",
        ticker="AAPL",
        sector="Technology",
        meta={"cik": 320193},
    ),
    Filing(
        source_filing_id="0000320193-24-000123",
        form_type="10-K",
        period_end=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period="FY",
        filed_at=datetime(2024, 11, 1, 12, 30, tzinfo=UTC),
        correction_number=0,
        source_url="https://www.sec.gov/Archives/...",
    ),
    FinancialFact(
        source_filing_id="0000320193-24-000123",
        metric="revenue",
        value=Decimal("391035000000.0000"),
        unit="USD",
        period_start=date(2023, 10, 1),
        period_end=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period="FY",
        standard="US-GAAP",
        source_tag="RevenueFromContractWithCustomerExcludingAssessedTax",
    ),
    FilingSection(
        source_filing_id="0000320193-24-000123",
        section="risk_factors",
        title="Item 1A. Risk Factors",
        text="The Company's business ...",
    ),
    Event(
        source="edgar",
        external_id="0000320193-25-000042",
        event_type="8-K",
        company_external_id="0000320193",
        occurred_at=datetime(2025, 1, 15, 9, 0, tzinfo=UTC),
        payload={"items": ["2.02"]},
    ),
]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda m: type(m).__name__)
def test_json_round_trip_is_lossless(sample: BaseModel) -> None:
    restored = type(sample).model_validate_json(sample.model_dump_json())
    assert restored == sample


def test_decimal_precision_survives_round_trip() -> None:
    fact = FinancialFact(
        source_filing_id="acc",
        metric="eps_basic",
        value=Decimal("6.1275"),
        unit="USD/share",
        period_end=date(2024, 9, 28),
        fiscal_period="FY",
        standard="US-GAAP",
    )
    restored = FinancialFact.model_validate_json(fact.model_dump_json())
    assert restored.value == Decimal("6.1275")


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Company.model_validate({"source": "edgar", "external_id": "1", "name": "X", "db_id": 42})
