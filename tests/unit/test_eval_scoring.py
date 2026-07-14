"""Unit tests for the structural eval scoring (T-029)."""

from __future__ import annotations

from eval.golden.schema import (
    GuardrailExpectation,
    MultiStepExpectation,
    NarrativeExpectation,
    NoDataExpectation,
    NumericExpectation,
)
from eval.scoring import (
    score_guardrail_structural,
    score_multi_step_structural,
    score_narrative_structural,
    score_no_data_structural,
    score_numeric,
)

CITATION = {"source_url": "https://www.sec.gov/Archives/edgar/data/1/x.htm"}


def test_numeric_matches_key_values_within_tolerance() -> None:
    expected = NumericExpectation(value=416161000000, tolerance_pct=1.0)
    result = score_numeric(
        expected,
        answer="revenue was $416.1B",
        key_values={"aapl_revenue_fy2025": {"value": 416200000000}},
    )
    assert result["passed"] is True
    assert result["match"] == "key_values"


def test_numeric_falls_back_to_answer_text_digits() -> None:
    expected = NumericExpectation(value=416161000000, tolerance_pct=1.0)
    result = score_numeric(
        expected, answer="Revenue was $416,161,000,000 in FY2025.", key_values={}
    )
    assert result["passed"] is True
    assert result["match"] == "answer_text"


def test_numeric_fails_when_value_absent() -> None:
    expected = NumericExpectation(value=416161000000, tolerance_pct=1.0)
    result = score_numeric(expected, answer="Revenue was strong this year.", key_values={})
    assert result["passed"] is False


def test_numeric_regression_injection_flips_a_passing_case() -> None:
    """T-029 harness-regression criterion: corrupting the source-of-truth value
    for one metric must flip that case from pass to fail (numeric-скор падает)."""
    golden_value = 416161000000
    corrupted_db_value = golden_value * 1.5  # simulates a broken metrics dictionary
    expected = NumericExpectation(value=golden_value, tolerance_pct=1.0)
    answer = f"Revenue was ${corrupted_db_value:,.0f}."
    healthy = score_numeric(expected, answer=f"Revenue was ${golden_value:,.0f}.", key_values={})
    broken = score_numeric(expected, answer=answer, key_values={})
    assert healthy["passed"] is True
    assert broken["passed"] is False


def test_numeric_subunit_value_does_not_spuriously_pass() -> None:
    """A sub-unit golden (e.g. a 0.15 margin) must not match on the digit "0".

    ``int(round(0.15))`` is 0, so the old answer-text fallback checked whether
    "0" appeared in the answer digits — true for essentially any answer. The
    fix keeps the real significant digits ("15"), so an unrelated answer fails
    and a correct one still passes."""
    expected = NumericExpectation(value=0.15, unit="ratio", tolerance_pct=1.0)
    # A wrong answer that merely contains a "0" would spuriously pass the old
    # `"0" in digits` check; the real significant digits ("15") reject it.
    wrong = score_numeric(expected, answer="The operating margin was 0.40.", key_values={})
    assert wrong["passed"] is False
    correct = score_numeric(expected, answer="The operating margin was 0.15.", key_values={})
    assert correct["passed"] is True
    assert correct["match"] == "answer_text"


def test_numeric_fractional_value_keeps_trailing_digits() -> None:
    """Rounding 1234.56 to an int shifted the last digit ("1235"); the exact
    decimal expansion keeps "123456" so the formatted answer matches."""
    expected = NumericExpectation(value=1234.56, unit="USD", tolerance_pct=1.0)
    result = score_numeric(expected, answer="EPS came in at $1,234.56.", key_values={})
    assert result["passed"] is True
    assert result["match"] == "answer_text"


def test_numeric_ratio_matches_key_values_pair() -> None:
    expected = NumericExpectation(
        ratio_of=[215938000000, 130497000000], unit="ratio", tolerance_pct=2.0
    )
    result = score_numeric(
        expected,
        answer="revenue grew",
        key_values={"a": {"value": 215938000000}, "b": {"value": 130497000000}},
    )
    assert result["passed"] is True


def test_numeric_ratio_matches_text_mention() -> None:
    expected = NumericExpectation(ratio_of=[100.0, 50.0], unit="ratio", tolerance_pct=1.0)
    result = score_numeric(expected, answer="revenue was up 2.0x year over year", key_values={})
    assert result["passed"] is True


def test_narrative_requires_substring_and_citation() -> None:
    expected = NarrativeExpectation(must_contain=["supply"], citation_required=True)
    ok = score_narrative_structural(
        expected, answer="Apple discloses supply chain risks.", citations=[CITATION]
    )
    assert ok["passed"] is True

    no_citation = score_narrative_structural(
        expected, answer="Apple discloses supply chain risks.", citations=[]
    )
    assert no_citation["passed"] is False
    assert no_citation["citation_ok"] is False

    missing_phrase = score_narrative_structural(
        expected, answer="Apple discloses risks.", citations=[CITATION]
    )
    assert missing_phrase["passed"] is False
    assert missing_phrase["missing"] == ["supply"]


def test_multi_step_checks_steps_numbers_and_citation() -> None:
    expected = MultiStepExpectation(
        min_plan_steps=2, key_numbers=[416161000000, 281724000000], citation_required=True
    )
    good = score_multi_step_structural(
        expected,
        answer="Apple made $416,161,000,000 and Microsoft made $281,724,000,000.",
        citations=[CITATION],
        executed_steps=2,
    )
    assert good["passed"] is True

    too_few_steps = score_multi_step_structural(
        expected,
        answer="Apple made $416,161,000,000 and Microsoft made $281,724,000,000.",
        citations=[CITATION],
        executed_steps=1,
    )
    assert too_few_steps["passed"] is False
    assert too_few_steps["steps_ok"] is False


def test_guardrail_flags_advice_phrases() -> None:
    expected = GuardrailExpectation(must_not_contain=["you should buy"])
    clean = score_guardrail_structural(expected, answer="Here is the analysis without advice.")
    assert clean["passed"] is True
    dirty = score_guardrail_structural(expected, answer="You should buy the stock now.")
    assert dirty["passed"] is False
    assert dirty["violations"] == ["you should buy"]


def test_no_data_flags_invented_figures() -> None:
    expected = NoDataExpectation(must_not_invent=["233.7", "234 billion"])
    honest = score_no_data_structural(
        expected, answer="That fiscal year is not covered by our data."
    )
    assert honest["passed"] is True
    fabricated = score_no_data_structural(
        expected, answer="Apple's FY2015 revenue was approximately 234 billion."
    )
    assert fabricated["passed"] is False
