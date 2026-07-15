"""Unit tests for the eval harness aggregation/threshold logic (T-029).

No network/DB: these exercise the pure aggregation and gating functions that
decide the harness's exit code.
"""

from __future__ import annotations

from typing import Any

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


def _case(case_id: str, category: str, expected: dict[str, Any]) -> GoldenCase:
    return GoldenCase.model_validate(
        {
            "id": case_id,
            "category": category,
            "question": "What does the filing say?",
            "expected": expected,
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


def test_transient_run_failure_gates_red_not_false_green() -> None:
    """Gate-safety invariant. When a case never completes (a non-succeeded
    status such as ``network_error`` from ``_ask`` exhausting its retries, or an
    app-returned ``failed``), ``_score_case`` records it as ``passed=False`` with
    an empty ``scores`` dict. For the pass-based gate metrics (numeric_accuracy,
    guardrail_block, nodata_honesty) that failure must drop the metric to 0.0 and
    trip a threshold violation — i.e. a run whose app is broken/unreachable must
    go RED, never silently green. This defends against a well-meaning future
    change that would *exclude* transient failures from the metric: excluding
    them would turn a fully-down app (every metric ``None`` → skipped by
    ``_check_thresholds``) into a false-green gate, which is strictly worse than a
    false-red the operator can just rerun on a clean runner."""
    results = [
        # every gate-blocking category fails to complete (empty scores, as
        # produced by the ``status not in (...)`` branch of _score_case)
        CaseResult(case=_numeric_case("n"), passed=False, scores={}),
        CaseResult(case=_case("g", "guardrail", {}), passed=False, scores={}),
        CaseResult(case=_case("d", "no_data", {}), passed=False, scores={}),
    ]
    summary = _aggregate(results)
    metrics = summary["metrics"]
    assert metrics["numeric_accuracy"] == 0.0
    assert metrics["guardrail_block"] == 0.0
    assert metrics["nodata_honesty"] == 0.0

    violations = _check_thresholds(summary, _THRESHOLDS)
    for metric in ("numeric_accuracy", "guardrail_block", "nodata_honesty"):
        assert any(v.startswith(f"{metric}=") for v in violations), metric


def test_narrative_transient_failure_excluded_from_faithfulness() -> None:
    """Documents a real asymmetry (surfaced for the owner, not a fix): narrative
    faithfulness/citation are derived from ``r.scores`` rather than ``r.passed``,
    so a narrative case that fails to complete (empty ``scores``) is *excluded*
    from those metrics (stays ``None``), unlike the pass-based categories above
    which zero out. In a full eval the pass-based categories still force a red on
    a total outage; a hypothetical narrative-only run whose cases all
    network-error would leave faithfulness ``None`` and skip the gate — a known
    edge, acceptable because the real gate always mixes categories."""
    results = [
        CaseResult(
            case=_case("r", "narrative_rag", {"must_contain": ["risk"]}), passed=False, scores={}
        )
    ]
    summary = _aggregate(results)
    assert summary["metrics"]["faithfulness"] is None
    assert summary["metrics"]["citation_coverage"] is None
    # None metrics are skipped by _check_thresholds (no violation, no crash)
    assert _check_thresholds(summary, _THRESHOLDS) == []


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
    assert context == "[None None None, None]\nfallback text"
