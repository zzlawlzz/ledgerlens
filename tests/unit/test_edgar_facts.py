"""Unit tests for XBRL fact extraction (T-009) on real recorded fixtures.

Reference values are cross-checked against public filings (AAPL FY2024
revenue $391.035B etc.). JPM deliberately exercises a different industry:
banks structurally lack 6 of the 16 canonical metrics (no cost_of_revenue /
gross_profit / operating_income / capex / R&D; long-term debt not reported
under our tags in recent 10-K) — the honest maximum is 10, documented here.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from adapters.edgar.facts import ExtractionStats, extract_financial_facts
from common.models import FinancialFact

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"

JPM_BANK_ABSENT_METRICS = {
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "long_term_debt",
    "capex",
    "rnd_expense",
}


def _extract(ticker: str) -> tuple[list[FinancialFact], ExtractionStats]:
    data = json.loads((FIXTURES_DIR / f"{ticker}_companyfacts.json").read_text(encoding="utf-8"))
    return extract_financial_facts(data)


def _fy_values(facts: list[FinancialFact], metric: str) -> dict[date, set[Decimal]]:
    values: dict[date, set[Decimal]] = {}
    for fact in facts:
        if fact.metric == metric and fact.fiscal_period == "FY":
            values.setdefault(fact.period_end, set()).add(fact.value)
    return values


def test_aapl_extracts_at_least_12_of_16_metrics() -> None:
    _, stats = _extract("aapl")
    assert len(stats.found) >= 12
    assert stats.missing == []  # Apple actually yields all 16


def test_aapl_reference_values_for_last_three_fy() -> None:
    facts, _ = _extract("aapl")
    revenue = _fy_values(facts, "revenue")
    assert revenue[date(2023, 9, 30)] == {Decimal("383285000000")}
    assert revenue[date(2024, 9, 28)] == {Decimal("391035000000")}
    assert revenue[date(2025, 9, 27)] == {Decimal("416161000000")}
    net_income = _fy_values(facts, "net_income")
    assert net_income[date(2024, 9, 28)] == {Decimal("93736000000")}


def test_aapl_unit_edge_cases_eps_and_shares() -> None:
    facts, _ = _extract("aapl")
    eps = [
        f
        for f in facts
        if f.metric == "eps_diluted"
        and f.fiscal_period == "FY"
        and f.period_end == date(2024, 9, 28)
    ]
    assert eps and all(f.unit == "USD/share" for f in eps)
    assert {f.value for f in eps} == {Decimal("6.08")}
    shares = [f for f in facts if f.metric == "shares_outstanding"]
    assert shares and all(f.unit == "shares" for f in shares)


def test_jpm_bank_extracts_structural_maximum() -> None:
    facts, stats = _extract("jpm")
    assert len(stats.found) >= 10
    assert set(stats.missing) <= JPM_BANK_ABSENT_METRICS
    revenue = _fy_values(facts, "revenue")
    assert revenue[date(2024, 12, 31)] == {Decimal("177556000000")}
    # Bank cash comes from the T-009 chain extension.
    assert stats.found["cash_and_equivalents"] == "CashAndDueFromBanks"


def test_jpm_real_restatement_keeps_both_versions() -> None:
    """FY2020 revenue was restated — both values must survive with own accessions."""
    facts, _ = _extract("jpm")
    fy2020 = [
        f
        for f in facts
        if f.metric == "revenue" and f.fiscal_period == "FY" and f.period_end == date(2020, 12, 31)
    ]
    values = {f.value for f in fy2020}
    accessions = {f.source_filing_id for f in fy2020}
    assert len(values) == 2, f"expected restated pair, got {values}"
    assert len(accessions) == len(fy2020)  # each version carries its own filing


def _synthetic_facts(entries_by_tag: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "facts": {
            "us-gaap": {tag: {"units": {"USD": entries}} for tag, entries in entries_by_tag.items()}
        }
    }


def _entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "start": "2023-10-01",
        "end": "2024-09-28",
        "val": 1000,
        "accn": "0000000000-24-000001",
        "fy": 2024,
        "fp": "FY",
        "form": "10-K",
    }
    base.update(overrides)
    return base


def test_missing_tag_does_not_break_other_metrics() -> None:
    data = _synthetic_facts({"NetIncomeLoss": [_entry(val=5)]})
    facts, stats = extract_financial_facts(data)
    assert stats.found == {"net_income": "NetIncomeLoss"}
    assert [f.value for f in facts] == [Decimal("5")]
    assert "revenue" in stats.missing


def test_quarterly_ytd_rows_are_excluded() -> None:
    data = _synthetic_facts(
        {
            "Revenues": [
                _entry(start="2024-04-01", end="2024-06-29", val=90, fp="Q2", form="10-Q"),
                # 6-month YTD row under the same fp must be dropped.
                _entry(start="2024-01-01", end="2024-06-29", val=180, fp="Q2", form="10-Q"),
            ]
        }
    )
    facts, _ = extract_financial_facts(data)
    assert [f.value for f in facts] == [Decimal("90")]


def test_annual_duration_window_rejects_short_years() -> None:
    data = _synthetic_facts(
        {"Revenues": [_entry(start="2024-06-01", end="2024-09-28", val=42)]}  # ~120 days
    )
    facts, _ = extract_financial_facts(data)
    assert facts == []


def test_period_end_validation_skips_and_warns() -> None:
    data = _synthetic_facts({"Revenues": [_entry(start="1998-10-01", end="1999-09-25", val=1)]})
    facts, stats = extract_financial_facts(data)
    assert facts == []
    assert any("outside sane range" in w for w in stats.warnings)


def test_negative_revenue_is_kept_with_warning() -> None:
    data = _synthetic_facts({"Revenues": [_entry(val=-7)]})
    facts, stats = extract_financial_facts(data)
    assert [f.value for f in facts] == [Decimal("-7")]
    assert any("suspicious" in w for w in stats.warnings)


def test_allowed_accessions_filter_counts_skips() -> None:
    data = _synthetic_facts(
        {
            "Revenues": [
                _entry(),
                _entry(accn="0000000000-24-000002", val=2000),
            ]
        }
    )
    facts, stats = extract_financial_facts(data, allowed_accessions={"0000000000-24-000001"})
    assert [f.source_filing_id for f in facts] == ["0000000000-24-000001"]
    assert stats.skipped_unknown_filing == 1


def test_first_tag_with_data_wins_over_later_tags() -> None:
    data = _synthetic_facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": [_entry(val=100)],
            "Revenues": [_entry(val=999)],
        }
    )
    facts, stats = extract_financial_facts(data)
    assert stats.found["revenue"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert [f.value for f in facts] == [Decimal("100")]
