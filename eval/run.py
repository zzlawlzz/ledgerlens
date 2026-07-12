"""Eval harness (T-029): runs golden cases through the live stack end-to-end
(orchestrator -> workers -> MCP tools) and scores them.

Usage:
    uv run python -m eval.run --profile ci --base-url http://localhost:8000

Numeric/citation checks are structural (eval.scoring, no network). Narrative
faithfulness/relevancy, multi-step correctness, guardrail refusal, and
no-data honesty add an LLM-judge rubric (eval.judge, task_class=judge,
prompts/judge.md) — judge scores are noisy per case so the gate in
config/eval-thresholds.yaml applies to the category average, never to a
single case. Writes eval_runs/eval_results and eval/reports/<ts>/{report.json,report.md}.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from common.config import get_settings, load_yaml_config
from common.db import get_session_factory
from common.logging import bind_run_context, reset_run_context
from common.tracing import TraceBus
from eval.golden.schema import (
    GoldenCase,
    GuardrailExpectation,
    MultiStepExpectation,
    NarrativeExpectation,
    NoDataExpectation,
    NumericExpectation,
    load_golden,
)
from eval.judge import (
    FAITHFULNESS_RUBRIC,
    GEVAL_CORRECTNESS_RUBRIC,
    GUARDRAIL_RUBRIC,
    NO_DATA_HONESTY_RUBRIC,
    judge_rubric,
)
from eval.scoring import (
    score_guardrail_structural,
    score_multi_step_structural,
    score_narrative_structural,
    score_no_data_structural,
    score_numeric,
)
from model_router.router import RouterClient
from orchestrator.persistence import llm_call_cost

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
NETWORK_RETRY_ATTEMPTS = 2
JUDGE_PASS_BAR = 0.6
CONTEXT_MAX_CHARS = 8000
# Hard wall-clock cap per case: guards against a truly stuck case (a hung SSE
# stream never delivering a terminal event was observed hanging a whole CI eval
# for 37 min) without letting one bad case stall the entire run.
# Raised 720->1200 (T-041/Q-22): on a CPU-starved eval runner (GitHub 2-vCPU)
# an advice/guardrail case runs full rag (the jina cross-encoder rerank is
# CPU-bound) before the guardrail catches it at synthesis, taking 360-720s;
# at 720 several correct refusals were killed exactly at the cap and scored as
# guardrail *failures* — a pure timeout artifact, not a safety leak. 1200 lets
# a slow-but-correct case complete while still bounding a genuinely hung one.
CASE_TIMEOUT_S = 1200.0


def _tag(ticker: Any, form_type: Any, period: Any, section: Any) -> str:
    return f"[{ticker} {form_type} {period}, {section}]"


async def _full_context(citations: list[dict[str, Any]]) -> str:
    """Full retrieved chunk text, fetched from Qdrant by chunk_id — the citation's
    ``snippet`` field is a 300-char UI preview (tools/rag/core.py:SNIPPET_CHARS),
    far short of what the worker's rag_search call actually saw. Faithfulness
    judged against the snippet alone flags true claims as unsupported just
    because the chunk got cut before the substantive sentence. Falls back to
    the snippet if Qdrant is unreachable.

    Each chunk is prefixed with its [ticker form_type period, section] tag
    (T-041): without it the judge sees bare text and cannot verify claims
    about *which company/period* a chunk belongs to, so an honest "only
    Microsoft/Apple chunks came back, not Amazon" answer was scored as
    unfaithful for lacking word-for-word support in an untagged blob."""
    by_id = {str(c["chunk_id"]): c for c in citations if c.get("chunk_id")}
    ids = list(by_id)
    if not ids:
        return ""
    try:
        from qdrant_client import AsyncQdrantClient

        from rag.indexer import COLLECTION

        client = AsyncQdrantClient(url=get_settings().qdrant_url)
        try:
            points = await client.retrieve(collection_name=COLLECTION, ids=ids, with_payload=True)
        finally:
            await client.close()
        texts = []
        for point in points:
            if not point.payload:
                continue
            tag = _tag(
                point.payload.get("ticker"),
                point.payload.get("form_type"),
                point.payload.get("period_end"),
                point.payload.get("section"),
            )
            texts.append(f"{tag}\n{point.payload.get('text', '')}")
        if texts:
            return "\n---\n".join(texts)[:CONTEXT_MAX_CHARS]
    except Exception:  # noqa: BLE001 — degrade to the snippet, never fail the case
        pass
    texts = [
        f"{_tag(c.get('ticker'), c.get('form_type'), c.get('period'), c.get('section'))}\n"
        f"{c.get('snippet', '')}"
        for c in by_id.values()
    ]
    return "\n---\n".join(texts)[:CONTEXT_MAX_CHARS]


@dataclass
class CaseResult:
    case: GoldenCase
    passed: bool
    scores: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    cost_usd: float = 0.0


async def _ask(client: httpx.AsyncClient, question: str) -> tuple[dict[str, Any], str | None]:
    """POST /api/chat, drain the SSE stream, return the terminal payload + run_id."""
    last_error: str | None = None
    for attempt in range(1, NETWORK_RETRY_ATTEMPTS + 1):
        try:
            events: list[dict[str, Any]] = []
            run_id: str | None = None
            async with client.stream("POST", "/api/chat", json={"question": question}) as response:
                response.raise_for_status()
                run_id = response.headers.get("X-Run-Id")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))
            final = events[-1] if events else {}
            return dict(final.get("payload", {})), run_id
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < NETWORK_RETRY_ATTEMPTS:
                await asyncio.sleep(2.0)
    return {"status": "network_error", "answer": "", "error": last_error}, None


async def _executed_steps(run_id: str | None) -> int:
    """Count worker steps for a run. Retried via ``_db_retry`` — a transient
    connection drop here is seen live on Windows/Docker Desktop under
    concurrent load (asyncpg ``connection_lost`` during the TLS handshake for
    a brand new pool connection, so ``pool_pre_ping`` does not help); left
    unguarded this killed the whole ``asyncio.gather`` and discarded every
    already-scored case in the run."""
    if not run_id:
        return 0

    async def _query() -> int:
        factory = get_session_factory()
        async with factory() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM steps WHERE run_id = :id AND node = 'worker'"),
                    {"id": run_id},
                )
            ).scalar_one()
        return int(count)

    return await _db_retry(_query)


class CostTracker:
    """Judge-call cost, attributed per case via a synthetic run_id on the bus."""

    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.by_case: dict[str, float] = {}

    def has_budget(self) -> bool:
        return self.spent_usd < self.cap_usd

    def bus(self) -> TraceBus:
        bus = TraceBus()

        async def _record(event: Any) -> None:
            if event.event != "llm_call":
                return
            payload = event.payload
            model = str(payload.get("model", "unknown"))
            cost = llm_call_cost(
                model, int(payload.get("tokens_in", 0)), int(payload.get("tokens_out", 0))
            )
            if cost is None:
                return
            self.spent_usd += float(cost)
            self.by_case[event.run_id] = self.by_case.get(event.run_id, 0.0) + float(cost)

        bus.subscribe(_record)
        return bus


async def _judge_or_skip(
    router: RouterClient,
    tracker: CostTracker,
    case_id: str,
    *,
    rubric: str,
    question: str,
    answer: str,
    context: str = "",
) -> dict[str, Any] | None:
    if not tracker.has_budget():
        return {"skipped": "run_cost_cap_usd exceeded"}
    tokens = bind_run_context(run_id=f"judge-{case_id}-{uuid.uuid4().hex[:8]}")
    try:
        verdict = await judge_rubric(
            router, rubric=rubric, question=question, answer=answer, context=context
        )
    except Exception as exc:  # noqa: BLE001 — a judge failure degrades the case, not the run
        return {"error": str(exc)[:300]}
    finally:
        reset_run_context(tokens)
    return {"score": verdict.score, "passed": verdict.passed, "reasoning": verdict.reasoning}


async def _score_case(
    client: httpx.AsyncClient, router: RouterClient, tracker: CostTracker, case: GoldenCase
) -> CaseResult:
    payload, run_id = await _ask(client, case.question)
    answer = str(payload.get("answer", ""))
    status = payload.get("status")
    key_values = (
        payload.get("key_values", {}) if isinstance(payload.get("key_values"), dict) else {}
    )
    citations = payload.get("citations", []) if isinstance(payload.get("citations"), list) else []
    context = (
        await _full_context(citations) if case.category in ("narrative_rag", "multi_step") else ""
    )

    if status not in ("succeeded", "budget_exceeded"):
        return CaseResult(
            case=case,
            passed=False,
            scores={},
            details={"status": status, "error": payload.get("error"), "answer": answer[:500]},
            run_id=run_id,
        )

    result: CaseResult
    if case.category == "numeric_sql":
        expected = case.typed_expected()
        assert isinstance(expected, NumericExpectation)
        structural = score_numeric(expected, answer=answer, key_values=key_values)
        result = CaseResult(
            case=case,
            passed=structural["passed"],
            scores={"numeric_accuracy": 1.0 if structural["passed"] else 0.0},
            details={"structural": structural, "answer": answer[:500]},
            run_id=run_id,
        )
    elif case.category == "narrative_rag":
        expected = case.typed_expected()
        assert isinstance(expected, NarrativeExpectation)
        structural = score_narrative_structural(expected, answer=answer, citations=citations)
        judge = await _judge_or_skip(
            router,
            tracker,
            case.id,
            rubric=FAITHFULNESS_RUBRIC,
            question=case.question,
            answer=answer,
            context=context,
        )
        faithfulness = judge.get("score") if judge and "score" in judge else None
        passed = structural["passed"] and (faithfulness is None or faithfulness >= JUDGE_PASS_BAR)
        scores: dict[str, Any] = {"faithfulness": faithfulness}
        if expected.citation_required:
            scores["citation_coverage"] = 1.0 if structural["citation_ok"] else 0.0
        result = CaseResult(
            case=case,
            passed=passed,
            scores=scores,
            details={
                "structural": structural,
                "judge": judge,
                "answer": answer[:500],
                "n_citations": len(citations),
                "context_chars": len(context),
            },
            run_id=run_id,
        )
    elif case.category == "multi_step":
        expected = case.typed_expected()
        assert isinstance(expected, MultiStepExpectation)
        executed_steps = await _executed_steps(run_id)
        structural = score_multi_step_structural(
            expected, answer=answer, citations=citations, executed_steps=executed_steps
        )
        judge = await _judge_or_skip(
            router,
            tracker,
            case.id,
            rubric=GEVAL_CORRECTNESS_RUBRIC,
            question=case.question,
            answer=answer,
            context=context,
        )
        geval = judge.get("score") if judge and "score" in judge else None
        passed = structural["passed"] and (geval is None or geval >= JUDGE_PASS_BAR)
        multi_scores: dict[str, Any] = {"geval_correctness": geval}
        if expected.citation_required:
            multi_scores["citation_coverage"] = 1.0 if structural["citation_ok"] else 0.0
        result = CaseResult(
            case=case,
            passed=passed,
            scores=multi_scores,
            details={
                "structural": structural,
                "judge": judge,
                "answer": answer[:500],
                "n_citations": len(citations),
                "context_chars": len(context),
            },
            run_id=run_id,
        )
    elif case.category == "guardrail":
        expected = case.typed_expected()
        assert isinstance(expected, GuardrailExpectation)
        structural = score_guardrail_structural(expected, answer=answer)
        judge = await _judge_or_skip(
            router,
            tracker,
            case.id,
            rubric=GUARDRAIL_RUBRIC,
            question=case.question,
            answer=answer,
        )
        judge_passed = judge.get("passed", True) if judge and "score" in judge else True
        passed = structural["passed"] and judge_passed
        result = CaseResult(
            case=case,
            passed=passed,
            scores={"guardrail_block": 1.0 if passed else 0.0},
            details={"structural": structural, "judge": judge, "answer": answer[:500]},
            run_id=run_id,
        )
    elif case.category == "no_data":
        expected = case.typed_expected()
        assert isinstance(expected, NoDataExpectation)
        structural = score_no_data_structural(expected, answer=answer)
        judge = await _judge_or_skip(
            router,
            tracker,
            case.id,
            rubric=NO_DATA_HONESTY_RUBRIC,
            question=case.question,
            answer=answer,
        )
        judge_passed = judge.get("passed", True) if judge and "score" in judge else True
        passed = structural["passed"] and judge_passed
        result = CaseResult(
            case=case,
            passed=passed,
            scores={"nodata_honesty": 1.0 if passed else 0.0},
            details={"structural": structural, "judge": judge, "answer": answer[:500]},
            run_id=run_id,
        )
    else:  # pragma: no cover — schema restricts Category to the five above
        raise ValueError(f"unhandled category {case.category!r}")

    result.cost_usd = float(payload.get("usage", {}).get("cost_usd", 0.0) or 0.0)
    return result


def _select_cases(profile: str) -> list[GoldenCase]:
    golden = load_golden()
    if profile == "ci":
        return [c for c in golden.cases if "ci" in c.tags]
    if profile == "full":
        return list(golden.cases)
    raise ValueError(f"unknown profile {profile!r} (expected ci|full)")


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 — best-effort provenance
        return None


def _aggregate(results: list[CaseResult]) -> dict[str, Any]:
    by_category: dict[str, list[CaseResult]] = {}
    for r in results:
        by_category.setdefault(r.case.category, []).append(r)

    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    numeric = by_category.get("numeric_sql", [])
    narrative = by_category.get("narrative_rag", [])
    multi = by_category.get("multi_step", [])
    guardrail = by_category.get("guardrail", [])
    no_data = by_category.get("no_data", [])

    citation_cases = [r for r in narrative + multi if "citation_coverage" in r.scores]
    faithfulness_scores = [
        r.scores["faithfulness"] for r in narrative if r.scores.get("faithfulness") is not None
    ]

    summary = {
        "total_cases": len(results),
        "total_passed": sum(1 for r in results if r.passed),
        "total_cost_usd": round(sum(r.cost_usd for r in results), 6),
        "categories": {
            category: {
                "n": len(rows),
                "passed": sum(1 for r in rows if r.passed),
                "pass_rate": _mean([1.0 if r.passed else 0.0 for r in rows]),
            }
            for category, rows in by_category.items()
        },
        "metrics": {
            "numeric_accuracy": _mean([1.0 if r.passed else 0.0 for r in numeric]),
            "citation_coverage": _mean(
                [1.0 if r.scores["citation_coverage"] else 0.0 for r in citation_cases]
            )
            if citation_cases
            else None,
            "faithfulness": _mean(faithfulness_scores),
            "guardrail_block": _mean([1.0 if r.passed else 0.0 for r in guardrail]),
            "nodata_honesty": _mean([1.0 if r.passed else 0.0 for r in no_data]),
            "avg_cost_usd_per_case": round(sum(r.cost_usd for r in results) / len(results), 6)
            if results
            else None,
        },
    }
    return summary


def _check_thresholds(summary: dict[str, Any], thresholds: dict[str, float]) -> list[str]:
    violations = []
    metrics = summary["metrics"]
    checks = {
        "numeric_accuracy": ">=",
        "citation_coverage": ">=",
        "faithfulness": ">=",
        "guardrail_block": ">=",
        "nodata_honesty": ">=",
        "avg_cost_usd_per_case": "<=",
    }
    for metric, direction in checks.items():
        actual = metrics.get(metric)
        threshold = thresholds.get(metric)
        if actual is None or threshold is None:
            continue
        ok = actual >= threshold if direction == ">=" else actual <= threshold
        if not ok:
            violations.append(f"{metric}={actual:.3f} violates threshold {direction}{threshold}")
    return violations


def _blocking_violations(violations: list[str], non_blocking: list[str]) -> list[str]:
    """Violations still shown in the report, minus metrics marked non-blocking
    in config/eval-thresholds.yaml (tracked regressions that don't fail CI
    yet — see that file's comments for which metric and why)."""
    return [v for v in violations if not any(v.startswith(f"{m}=") for m in non_blocking)]


async def _db_retry[T](coro_fn: Callable[[], Awaitable[T]]) -> T:
    """One retry on the same transient connection drop as ``_executed_steps``
    — kept as a separate helper because these two calls happen after every
    case has already been scored (i.e. after all the eval's LLM spend), so
    losing them to a network blip is the most expensive place to lose a
    result."""
    try:
        return await coro_fn()
    except (ConnectionError, OSError):
        await asyncio.sleep(1.0)
        return await coro_fn()


async def _previous_summary(profile: str) -> dict[str, Any] | None:
    async def _query() -> dict[str, Any] | None:
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT summary FROM eval_runs WHERE profile = :profile "
                        "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
                    ),
                    {"profile": profile},
                )
            ).first()
        if row is None or row[0] is None:
            return None
        return dict(row[0])

    return await _db_retry(_query)


async def _persist(
    *, git_sha: str | None, profile: str, summary: dict[str, Any], results: list[CaseResult]
) -> int:
    async def _write() -> int:
        factory = get_session_factory()
        async with factory() as session:
            eval_run_id = (
                await session.execute(
                    text(
                        "INSERT INTO eval_runs (git_sha, profile, summary, finished_at) "
                        "VALUES (:git_sha, :profile, CAST(:summary AS jsonb), now()) RETURNING id"
                    ),
                    {
                        "git_sha": git_sha,
                        "profile": profile,
                        "summary": json.dumps(summary, ensure_ascii=False, default=str),
                    },
                )
            ).scalar_one()
            for r in results:
                await session.execute(
                    text(
                        "INSERT INTO eval_results (eval_run_id, case_id, category, passed, "
                        "scores, run_id, details) VALUES "
                        "(:eval_run_id, :case_id, :category, :passed, CAST(:scores AS jsonb), "
                        ":run_id, CAST(:details AS jsonb))"
                    ),
                    {
                        "eval_run_id": eval_run_id,
                        "case_id": r.case.id,
                        "category": r.case.category,
                        "passed": r.passed,
                        "scores": json.dumps(r.scores, ensure_ascii=False, default=str),
                        "run_id": r.run_id,
                        "details": json.dumps(r.details, ensure_ascii=False, default=str),
                    },
                )
            await session.commit()
        return int(eval_run_id)

    return await _db_retry(_write)


def _write_report(
    out_dir: Path,
    *,
    profile: str,
    git_sha: str | None,
    summary: dict[str, Any],
    previous: dict[str, Any] | None,
    violations: list[str],
    results: list[CaseResult],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(
            {
                "profile": profile,
                "git_sha": git_sha,
                "summary": summary,
                "violations": violations,
                "results": [
                    {
                        "case_id": r.case.id,
                        "category": r.case.category,
                        "passed": r.passed,
                        "scores": r.scores,
                        "run_id": r.run_id,
                        "cost_usd": r.cost_usd,
                        "details": r.details,
                    }
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    lines = [f"# Eval report — profile={profile} git_sha={git_sha or '?'}", ""]
    lines.append("## Categories")
    lines.append("| category | n | passed | pass_rate |")
    lines.append("|---|---|---|---|")
    for category, stats in summary["categories"].items():
        lines.append(
            f"| {category} | {stats['n']} | {stats['passed']} | {stats['pass_rate']:.2f} |"
        )
    lines.append("")
    lines.append("## Metrics vs thresholds")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for metric, value in summary["metrics"].items():
        lines.append(f"| {metric} | {value if value is None else round(value, 4)} |")
    lines.append("")
    if previous is not None:
        lines.append("## Regressions vs previous run (same profile)")
        prev_metrics = previous.get("metrics", {})
        for metric, value in summary["metrics"].items():
            prev_value = prev_metrics.get(metric)
            if value is None or prev_value is None:
                continue
            delta = value - prev_value
            marker = " ⚠ regression" if delta < -0.05 else ""
            lines.append(f"- {metric}: {prev_value:.3f} -> {value:.3f} (Δ{delta:+.3f}){marker}")
        lines.append("")
    if violations:
        lines.append("## Threshold violations")
        for v in violations:
            lines.append(f"- {v}")
        lines.append("")
    lines.append("## Top failures")
    failures = [r for r in results if not r.passed][:15]
    for r in failures:
        lines.append(f"### {r.case.id} ({r.case.category})")
        lines.append(f"- question: {r.case.question}")
        lines.append(f"- expected: {r.case.expected}")
        lines.append(f"- run_id: {r.run_id}")
        lines.append(f"- details: `{json.dumps(r.details, ensure_ascii=False, default=str)[:800]}`")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


async def run_eval(profile: str, base_url: str, concurrency: int = 2) -> int:
    eval_config = load_yaml_config("eval-thresholds")
    thresholds = eval_config["thresholds"]
    cost_cap = float(eval_config.get("run_cost_cap_usd", 0.5))
    non_blocking = eval_config.get("non_blocking", [])
    cases = _select_cases(profile)
    print(f"eval.run: profile={profile} cases={len(cases)} base_url={base_url}")

    tracker = CostTracker(cost_cap)
    router = RouterClient(trace_bus=tracker.bus())
    semaphore = asyncio.Semaphore(concurrency)
    results: list[CaseResult] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=600) as client:

        async def _bounded(case: GoldenCase) -> CaseResult:
            async with semaphore:
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(CASE_TIMEOUT_S):
                        result = await _score_case(client, router, tracker, case)
                except TimeoutError:
                    result = CaseResult(
                        case=case,
                        passed=False,
                        details={"error": f"case exceeded {CASE_TIMEOUT_S:.0f}s hard timeout"},
                    )
                except Exception as exc:  # noqa: BLE001 — one case's crash must not
                    # discard every other case's already-computed score and the
                    # report along with it (a live infra blip did exactly that).
                    result = CaseResult(
                        case=case, passed=False, details={"error": f"{type(exc).__name__}: {exc}"}
                    )
                elapsed = round(time.perf_counter() - started, 1)
                print(
                    f"  [{'PASS' if result.passed else 'FAIL'}] {case.id} "
                    f"({case.category}, {elapsed}s)"
                )
                return result

        results = await asyncio.gather(*(_bounded(case) for case in cases))

    summary = _aggregate(list(results))
    summary["total_cost_usd"] = round(summary["total_cost_usd"] + tracker.spent_usd, 6)
    violations = _check_thresholds(summary, thresholds)

    # By this point every case is scored and every LLM dollar is already
    # spent — a DB hiccup from here on must degrade, not discard the run.
    try:
        previous = await _previous_summary(profile)
    except (ConnectionError, OSError) as exc:
        print(f"warning: could not fetch previous run for baseline diff: {exc}")
        previous = None

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = REPORTS_DIR / ts
    _write_report(
        out_dir,
        profile=profile,
        git_sha=_git_sha(),
        summary=summary,
        previous=previous,
        violations=violations,
        results=list(results),
    )
    try:
        eval_run_id: int | None = await _persist(
            git_sha=_git_sha(), profile=profile, summary=summary, results=list(results)
        )
    except (ConnectionError, OSError) as exc:
        print(f"warning: could not persist eval run to DB: {exc}")
        eval_run_id = None
    print(f"\neval_run_id={eval_run_id} report={out_dir}")
    print(json.dumps(summary, indent=2, default=str))
    blocking = _blocking_violations(violations, non_blocking)
    if violations:
        print("\nTHRESHOLD VIOLATIONS:")
        for v in violations:
            tag = "" if v in blocking else " (non-blocking, tracked)"
            print(f"  - {v}{tag}")
    if blocking:
        return 1
    print(
        "\neval OK: no blocking thresholds violated"
        + (" (see non-blocking above)" if violations else "")
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["ci", "full"], required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    return asyncio.run(run_eval(args.profile, args.base_url, args.concurrency))


if __name__ == "__main__":
    sys.exit(main())
