"""Unit tests for the eval harness aggregation/threshold logic (T-029).

No network/DB: these exercise the pure aggregation and gating functions that
decide the harness's exit code.
"""

from __future__ import annotations

import pytest

from eval.golden.schema import GoldenCase
from eval.run import CaseResult, CostTracker, _aggregate, _check_thresholds, _full_context

_THRESHOLDS = {
    "numeric_accuracy": 0.8,
    "citation_coverage": 1.0,
    "faithfulness": 0.7,
    "guardrail_block": 1.0,
    "nodata_honesty": 1.0,
    "avg_cost_usd_per_case": 0.25,
}


def _numeric_case(case_id: str) -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "id": case_id,
            "category": "numeric_sql",
            "question": "What was the revenue?",
            "expected": {"value": 1000.0, "tolerance_pct": 1.0},
            "tags": ["ci"],
        }
    )


def test_all_numeric_cases_passing_meets_threshold() -> None:
    results = [
        CaseResult(case=_numeric_case("a"), passed=True, scores={"numeric_accuracy": 1.0}),
        CaseResult(case=_numeric_case("b"), passed=True, scores={"numeric_accuracy": 1.0}),
    ]
    summary = _aggregate(results)
    assert summary["metrics"]["numeric_accuracy"] == 1.0
    assert _check_thresholds(summary, _THRESHOLDS) == []


def test_regression_injection_breaks_numeric_accuracy_and_exits_nonzero() -> None:
    """Simulates T-029's required harness test: corrupting one metric's source
    of truth flips its case to a failure, dropping numeric_accuracy below the
    threshold — the exit-code contract downstream in main()/run_eval()."""
    results = [
        CaseResult(case=_numeric_case("a"), passed=True, scores={"numeric_accuracy": 1.0}),
        CaseResult(case=_numeric_case("b"), passed=True, scores={"numeric_accuracy": 1.0}),
        CaseResult(case=_numeric_case("c"), passed=True, scores={"numeric_accuracy": 1.0}),
        CaseResult(case=_numeric_case("d"), passed=True, scores={"numeric_accuracy": 1.0}),
        # the "broken metrics dictionary" case: previously passing, now fails
        CaseResult(case=_numeric_case("broken"), passed=False, scores={"numeric_accuracy": 0.0}),
    ]
    summary = _aggregate(results)
    assert summary["metrics"]["numeric_accuracy"] == 0.8  # right at the edge...
    assert _check_thresholds(summary, _THRESHOLDS) == []

    # one more broken case tips it under the 0.8 gate.
    results.append(
        CaseResult(case=_numeric_case("broken2"), passed=False, scores={"numeric_accuracy": 0.0})
    )
    summary = _aggregate(results)
    assert summary["metrics"]["numeric_accuracy"] < 0.8
    violations = _check_thresholds(summary, _THRESHOLDS)
    assert any("numeric_accuracy" in v for v in violations)


def test_cost_cap_exceeded_violates_avg_cost_threshold() -> None:
    results = [
        CaseResult(case=_numeric_case("a"), passed=True, scores={}, cost_usd=0.9),
    ]
    summary = _aggregate(results)
    violations = _check_thresholds(summary, _THRESHOLDS)
    assert any("avg_cost_usd_per_case" in v for v in violations)


def test_cost_tracker_stops_after_cap() -> None:
    tracker = CostTracker(cap_usd=0.01)
    assert tracker.has_budget() is True
    tracker.spent_usd = 0.02
    assert tracker.has_budget() is False


@pytest.mark.asyncio
async def test_full_context_empty_citations_short_circuits() -> None:
    """No chunk_id anywhere -> no Qdrant round-trip, just an empty context."""
    assert await _full_context([]) == ""
    assert await _full_context([{"snippet": "x"}]) == ""


@pytest.mark.asyncio
async def test_full_context_falls_back_to_snippet_when_qdrant_unreachable() -> None:
    """T-029 bug found live: the UI citation snippet is only 300 chars — far
    short of what the worker's rag_search call actually saw — so faithfulness
    judged against it alone flags true claims as unsupported. _full_context
    fetches the full chunk text from Qdrant by chunk_id; if that's
    unreachable it must still degrade to the snippet rather than raise."""
    citations = [{"chunk_id": "does-not-exist-in-any-collection", "snippet": "fallback text"}]
    context = await _full_context(citations)
    assert context == "fallback text"
