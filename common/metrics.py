"""Canonical metric dictionary v1 (T-007; CONTRACTS.md §7, ADR-1).

Rules: extend by adding metrics, never rename existing canonical names
(golden dataset and SQL examples depend on them). ``gaap_tags`` is a priority
chain — extractors take the first tag with data. ``rsbu_codes`` are RSBU form
line codes, a reference for the future RU adapter.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MetricDef(BaseModel):
    model_config = ConfigDict(frozen=True)

    canonical: str
    description: str
    unit_hint: str  # 'currency' | 'currency/share' | 'shares'
    gaap_tags: tuple[str, ...]  # priority chain, us-gaap namespace unless prefixed
    rsbu_codes: tuple[str, ...]  # RSBU line codes (reference for RU adapter)


_METRIC_DEFS: tuple[MetricDef, ...] = (
    MetricDef(
        canonical="revenue",
        description="Total revenue",
        unit_hint="currency",
        gaap_tags=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
        rsbu_codes=("2110",),
    ),
    MetricDef(
        canonical="cost_of_revenue",
        description="Cost of revenue / cost of goods and services sold",
        unit_hint="currency",
        gaap_tags=("CostOfRevenue", "CostOfGoodsAndServicesSold"),
        rsbu_codes=("2120",),
    ),
    MetricDef(
        canonical="gross_profit",
        description="Gross profit",
        unit_hint="currency",
        gaap_tags=("GrossProfit",),
        rsbu_codes=("2100",),
    ),
    MetricDef(
        canonical="operating_income",
        description="Operating income (loss)",
        unit_hint="currency",
        gaap_tags=("OperatingIncomeLoss",),
        rsbu_codes=("2200",),
    ),
    MetricDef(
        canonical="net_income",
        description="Net income (loss)",
        unit_hint="currency",
        gaap_tags=("NetIncomeLoss",),
        rsbu_codes=("2400",),
    ),
    MetricDef(
        canonical="eps_basic",
        description="Earnings per share, basic",
        unit_hint="currency/share",
        gaap_tags=("EarningsPerShareBasic",),
        rsbu_codes=("2900",),
    ),
    MetricDef(
        canonical="eps_diluted",
        description="Earnings per share, diluted",
        unit_hint="currency/share",
        gaap_tags=("EarningsPerShareDiluted",),
        rsbu_codes=(),
    ),
    MetricDef(
        canonical="total_assets",
        description="Total assets",
        unit_hint="currency",
        gaap_tags=("Assets",),
        rsbu_codes=("1600",),
    ),
    MetricDef(
        canonical="total_liabilities",
        description="Total liabilities",
        unit_hint="currency",
        gaap_tags=("Liabilities",),
        rsbu_codes=("1400+1500",),  # sum of two RSBU lines
    ),
    MetricDef(
        canonical="equity",
        description="Stockholders' equity",
        unit_hint="currency",
        gaap_tags=(
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        rsbu_codes=("1300",),
    ),
    MetricDef(
        canonical="cash_and_equivalents",
        description="Cash and cash equivalents",
        unit_hint="currency",
        gaap_tags=("CashAndCashEquivalentsAtCarryingValue",),
        rsbu_codes=("1250",),
    ),
    MetricDef(
        canonical="long_term_debt",
        description="Long-term debt",
        unit_hint="currency",
        gaap_tags=("LongTermDebtNoncurrent", "LongTermDebt"),
        rsbu_codes=("1410",),
    ),
    MetricDef(
        canonical="operating_cash_flow",
        description="Net cash provided by (used in) operating activities",
        unit_hint="currency",
        gaap_tags=("NetCashProvidedByUsedInOperatingActivities",),
        rsbu_codes=("4100",),
    ),
    MetricDef(
        canonical="capex",
        description="Capital expenditures (purchases of PP&E)",
        unit_hint="currency",
        gaap_tags=("PaymentsToAcquirePropertyPlantAndEquipment",),
        rsbu_codes=("4221",),
    ),
    MetricDef(
        canonical="rnd_expense",
        description="Research and development expense",
        unit_hint="currency",
        gaap_tags=("ResearchAndDevelopmentExpense",),
        rsbu_codes=(),
    ),
    MetricDef(
        canonical="shares_outstanding",
        description="Common shares outstanding",
        unit_hint="shares",
        gaap_tags=(
            "dei:EntityCommonStockSharesOutstanding",
            "CommonStockSharesOutstanding",
        ),
        rsbu_codes=(),
    ),
)

METRICS: dict[str, MetricDef] = {metric.canonical: metric for metric in _METRIC_DEFS}


def metric_names() -> list[str]:
    return list(METRICS)
