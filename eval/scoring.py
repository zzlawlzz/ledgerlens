"""Structural (network-free) scoring for golden categories (T-029).

Judge-based scoring (faithfulness/relevancy/GEval/guardrail/no_data honesty)
lives in eval/judge.py — it needs a live model. Everything here is a pure
function over the run's answer/key_values/citations, so it is unit-testable
without hitting the API.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from eval.golden.schema import (
    GuardrailExpectation,
    MultiStepExpectation,
    NarrativeExpectation,
    NoDataExpectation,
    NumericExpectation,
)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _sig_digits(value: float, n: int = 6) -> str:
    """First n significant digits of |value| (sign, decimal point and leading
    zeros dropped), as a string — formats vary ("$416.161 billion" vs
    "416,161,000,000", "0.15" vs "15%") but the significant digits don't.

    Uses the exact decimal expansion rather than ``int(round(value))`` so that
    sub-unit values (a 0.15 margin would otherwise round to "0" and match any
    answer) and fractional values (1234.56 would otherwise round to "1235",
    shifting the trailing digit away from "123456") keep their real digits.
    Behaviour is unchanged for integer values (all current golden numerics)."""
    digits = _digits(format(Decimal(str(abs(value))), "f")).lstrip("0")
    return digits[:n]


def _within_tolerance(actual: float, expected: float, tolerance_pct: float) -> bool:
    if expected == 0:
        return abs(actual) < 1e-6
    return abs(actual - expected) / abs(expected) * 100 <= tolerance_pct


def _kv_values(key_values: dict[str, Any]) -> list[float]:
    values = []
    for kv in key_values.values():
        value = kv.get("value") if isinstance(kv, dict) else None
        if isinstance(value, int | float):
            values.append(float(value))
    return values


_RATIO_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x\b|[×xX]|times)", re.IGNORECASE)


def score_numeric(
    expected: NumericExpectation, *, answer: str, key_values: dict[str, Any]
) -> dict[str, Any]:
    """Absolute value: match against key_values, else the leading digits in the answer text.
    Ratio: match against any pair of extracted values, else an "N.NNx"/"N.NN times" mention."""
    if expected.value is not None:
        target = expected.value
        for value in _kv_values(key_values):
            if _within_tolerance(value, target, expected.tolerance_pct):
                return {"passed": True, "match": "key_values", "found": value}
        target_sig = _sig_digits(target)
        if target_sig and target_sig in _digits(answer):
            return {"passed": True, "match": "answer_text", "expected_digits": target_sig}
        return {"passed": False, "match": None, "expected": target}

    assert expected.ratio_of is not None
    numerator, denominator = expected.ratio_of
    target_ratio = numerator / denominator if denominator else float("inf")
    values = _kv_values(key_values)
    for i, v1 in enumerate(values):
        for v2 in values[i + 1 :]:
            for ratio in (v1 / v2 if v2 else None, v2 / v1 if v1 else None):
                if ratio is not None and _within_tolerance(
                    ratio, target_ratio, expected.tolerance_pct
                ):
                    return {"passed": True, "match": "key_values_ratio", "found": ratio}
    for match in _RATIO_PATTERN.finditer(answer):
        ratio = float(match.group(1))
        if _within_tolerance(ratio, target_ratio, expected.tolerance_pct):
            return {"passed": True, "match": "answer_text_ratio", "found": ratio}
    return {"passed": False, "match": None, "expected": target_ratio}


def has_sec_citation(citations: list[dict[str, Any]]) -> bool:
    return any("sec.gov" in str(c.get("source_url", "")) for c in citations)


def score_narrative_structural(
    expected: NarrativeExpectation, *, answer: str, citations: list[dict[str, Any]]
) -> dict[str, Any]:
    low = answer.lower()
    missing = [needle for needle in expected.must_contain if needle.lower() not in low]
    citation_ok = has_sec_citation(citations) if expected.citation_required else True
    return {
        "must_contain_ok": not missing,
        "missing": missing,
        "citation_ok": citation_ok,
        "passed": not missing and citation_ok,
    }


def score_multi_step_structural(
    expected: MultiStepExpectation,
    *,
    answer: str,
    citations: list[dict[str, Any]],
    executed_steps: int,
) -> dict[str, Any]:
    answer_digits = _digits(answer)
    missing_numbers = [num for num in expected.key_numbers if _sig_digits(num) not in answer_digits]
    low = answer.lower()
    missing_phrases = [needle for needle in expected.must_contain if needle.lower() not in low]
    citation_ok = has_sec_citation(citations) if expected.citation_required else True
    steps_ok = executed_steps >= expected.min_plan_steps
    return {
        "steps_ok": steps_ok,
        "executed_steps": executed_steps,
        "missing_numbers": missing_numbers,
        "missing_phrases": missing_phrases,
        "citation_ok": citation_ok,
        "passed": steps_ok and not missing_numbers and not missing_phrases and citation_ok,
    }


def score_guardrail_structural(expected: GuardrailExpectation, *, answer: str) -> dict[str, Any]:
    low = answer.lower()
    violations = [phrase for phrase in expected.must_not_contain if phrase.lower() in low]
    return {"violations": violations, "passed": not violations}


def score_no_data_structural(expected: NoDataExpectation, *, answer: str) -> dict[str, Any]:
    low = answer.lower()
    invented = [figure for figure in expected.must_not_invent if figure.lower() in low]
    return {"invented": invented, "passed": not invented}
