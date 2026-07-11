"""Golden case schema tests (T-028)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from eval.golden.schema import CASES_PATH, GoldenCase, GoldenSet, load_golden


def _case(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "aapl_revenue_fy2025",
        "category": "numeric_sql",
        "question": "What was Apple's revenue in fiscal year 2025?",
        "expected": {"value": 416161000000, "unit": "USD", "tolerance_pct": 1.0},
        "tags": ["ci"],
        "difficulty": "easy",
    }
    base.update(overrides)
    return base


def test_valid_numeric_case() -> None:
    from eval.golden.schema import NumericExpectation

    case = GoldenCase.model_validate(_case())
    expected = case.typed_expected()
    assert isinstance(expected, NumericExpectation)
    assert expected.value == 416161000000


def test_expected_must_match_category() -> None:
    with pytest.raises(ValidationError, match="value/ratio_of"):
        GoldenCase.model_validate(_case(expected={}))
    with pytest.raises(ValidationError):
        GoldenCase.model_validate(_case(category="narrative_rag", expected={"value": 1.0}))


def test_numeric_value_xor_ratio() -> None:
    GoldenCase.model_validate(_case(expected={"ratio_of": [100.0, 50.0], "tolerance_pct": 2.0}))
    with pytest.raises(ValidationError, match="value/ratio_of"):
        GoldenCase.model_validate(_case(expected={"value": 1.0, "ratio_of": [1.0, 2.0]}))


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        GoldenSet.model_validate({"corpus_snapshot": "test", "cases": [_case(), _case()]})


def test_shipped_cases_file_is_valid_and_balanced() -> None:
    if not CASES_PATH.is_file():
        pytest.skip("cases.yaml not written yet")
    golden = load_golden()
    assert len(golden.cases) >= 40
    by_category: dict[str, int] = {}
    for case in golden.cases:
        by_category[case.category] = by_category.get(case.category, 0) + 1
    assert by_category["numeric_sql"] >= 12
    assert by_category["narrative_rag"] >= 10
    assert by_category["multi_step"] >= 8
    assert by_category["guardrail"] >= 5
    assert by_category["no_data"] >= 5
    ci_cases = [case for case in golden.cases if "ci" in case.tags]
    assert len(ci_cases) == 10
    assert {case.category for case in ci_cases} == set(by_category)  # all categories in ci
