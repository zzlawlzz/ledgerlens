"""Snapshot tests for the canonical metric dictionary (T-007, CONTRACTS.md §7)."""

from common.metrics import METRICS, metric_names

# Frozen snapshot: canonical names are a public contract (golden dataset, SQL
# examples, adapters depend on them). Extending is fine; renaming is not.
EXPECTED_CANONICAL_V1 = {
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_basic",
    "eps_diluted",
    "total_assets",
    "total_liabilities",
    "equity",
    "cash_and_equivalents",
    "long_term_debt",
    "operating_cash_flow",
    "capex",
    "rnd_expense",
    "shares_outstanding",
}


def test_dictionary_matches_v1_snapshot() -> None:
    assert set(METRICS) == EXPECTED_CANONICAL_V1
    assert len(METRICS) == 16


def test_every_metric_is_fully_described() -> None:
    for name, metric in METRICS.items():
        assert metric.canonical == name
        assert metric.description
        assert metric.unit_hint in ("currency", "currency/share", "shares")
        assert metric.gaap_tags, f"{name} has no GAAP tag chain"


def test_gaap_tag_chains_preserve_priority_order() -> None:
    assert METRICS["revenue"].gaap_tags == (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    )
    assert METRICS["long_term_debt"].gaap_tags[0] == "LongTermDebtNoncurrent"
    assert METRICS["shares_outstanding"].gaap_tags[0].startswith("dei:")


def test_metric_names_helper() -> None:
    assert set(metric_names()) == EXPECTED_CANONICAL_V1
