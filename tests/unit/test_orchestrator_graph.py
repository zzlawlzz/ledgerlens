"""Unit tests for the Plan-and-Execute orchestrator (T-020) on fakes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from common.agents import WorkerResult, WorkerTask
from common.errors import ToolError
from orchestrator.graph import (
    Classification,
    ContradictionVerdict,
    Orchestrator,
    PlanDraft,
    PlanDraftStep,
    PlanStep,
    StepKeyValues,
    _find_contradiction,
    _merge_replan,
    _next_ready,
    _sanitize_plan,
)
from orchestrator.worker_client import WorkerClient

BUDGET = {
    "max_plan_steps": 4,
    "max_replans": 1,
    "worker_max_iterations": 3,
    "max_cost_usd_per_run": 0.50,
    "max_tokens_per_run": 100_000,
    "wall_clock_s_per_run": 300,
}


class FakeRouter:
    """Scripted router: classification, a queue of plans, canned synthesis."""

    def __init__(
        self,
        classification: Classification | None = None,
        plans: list[PlanDraft] | None = None,
    ) -> None:
        self.classification = classification or Classification(complexity="analytical")
        self.plans = list(plans or [])
        self.plan_calls = 0
        self.synth_calls = 0

    async def chat_structured(self, task_class: str, messages: Any, schema: Any) -> Any:
        if schema is Classification:
            return self.classification
        if schema is PlanDraft:
            self.plan_calls += 1
            return self.plans.pop(0)
        if schema is StepKeyValues:
            return StepKeyValues()
        if schema is ContradictionVerdict:
            return ContradictionVerdict(is_contradiction=True)
        raise AssertionError(f"unexpected schema {schema}")

    async def chat(self, task_class: str, messages: Any, **_: Any) -> Any:
        self.synth_calls += 1
        prompt = str(messages)
        partial = "PARTIAL" in prompt
        return SimpleNamespace(text=f"SYNTH(partial={partial})")


class FakeWorker(WorkerClient):
    """Returns scripted results keyed by goal substring; records calls."""

    def __init__(self, script: list[WorkerResult]) -> None:
        self.script = list(script)
        self.calls: list[WorkerTask] = []

    async def run(self, task: WorkerTask) -> WorkerResult:
        self.calls.append(task)
        result = self.script.pop(0)
        return result.model_copy(update={"task_id": task.task_id})


class LocalFakeWorker(FakeWorker):
    """A scripted worker that identifies as the in-process node (T-031)."""

    is_local = True


class UnreachableWorker(WorkerClient):
    """A remote worker that always fails transport — raises retryable ToolError."""

    is_local = False
    relays_trace = False

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task: WorkerTask) -> WorkerResult:
        self.calls += 1
        raise ToolError("A2A worker is unreachable: node down")


def _result(status: str, answer: str = "answer") -> WorkerResult:
    return WorkerResult(task_id="x", status=status, answer=answer)  # type: ignore[arg-type]


def _make(
    router: FakeRouter,
    worker: FakeWorker,
    budget: dict[str, Any] | None = None,
    usage_probe: Any = None,
    checkpointer: Any = None,
) -> Orchestrator:
    return Orchestrator(
        worker_clients={"worker-local": worker},
        worker_skills={"worker-local": ["financial_sql_analysis", "narrative_rag_analysis"]},
        router=router,  # type: ignore[arg-type]
        budget_profile=budget or dict(BUDGET),
        track_db_steps=False,
        usage_probe=usage_probe,
        checkpointer=checkpointer,
    )


def _plan(*goals: str) -> PlanDraft:
    return PlanDraft(
        steps=[PlanDraftStep(id=f"s{i + 1}", goal=goal) for i, goal in enumerate(goals)]
    )


@pytest.mark.asyncio
async def test_simple_question_is_single_step_without_planner() -> None:
    router = FakeRouter(classification=Classification(complexity="simple"))
    worker = FakeWorker([_result("succeeded", "Revenue was $391B")])
    orchestrator = _make(router, worker)
    state = await orchestrator.run(question="AAPL revenue 2024?", mode="us", run_id="r1")
    assert router.plan_calls == 0  # cost control: no planning LLM call
    assert len(worker.calls) == 1
    assert len(state["plan"]) == 1
    assert state["answer"].startswith("SYNTH")
    assert "not investment advice" in state["answer"]  # disclaimer appended (T-022)
    assert not state["partial"]


@pytest.mark.asyncio
async def test_empty_synthesis_yields_honest_fallback_not_blank() -> None:
    # A synthesis completion can rarely come back empty under model load. A blank
    # answer must never reach run_finished on a succeeded run: _finalize replaces
    # it with an honest "no result" admission + disclaimer. (Regression for the
    # no_data eval flake where an empty answer scored nodata_honesty=0.0 and
    # red-gated a clean full-eval — docs/release/v1.0-dod.md §3 addendum.)
    class EmptySynthRouter(FakeRouter):
        async def chat(self, task_class: str, messages: Any, **_: Any) -> Any:
            self.synth_calls += 1
            return SimpleNamespace(text="")  # model returned nothing

    router = EmptySynthRouter(classification=Classification(complexity="simple"))
    worker = FakeWorker([_result("succeeded", "no matching facts")])
    orchestrator = _make(router, worker)
    state = await orchestrator.run(
        question="What was Ford's net income in FY2024?", mode="us", run_id="re"
    )
    assert state["answer"].strip()  # never blank
    assert "could not find" in state["answer"].lower()  # honest gap admission
    assert "not investment advice" in state["answer"]  # disclaimer still present
    assert state["dialogue"][-1]["answer"] == state["answer"]  # emitted answer matches


@pytest.mark.asyncio
async def test_empty_synthesis_fallback_is_localized_for_russian() -> None:
    class EmptySynthRouter(FakeRouter):
        async def chat(self, task_class: str, messages: Any, **_: Any) -> Any:
            self.synth_calls += 1
            return SimpleNamespace(text="   ")  # whitespace-only counts as empty

    router = EmptySynthRouter(classification=Classification(complexity="simple"))
    worker = FakeWorker([_result("succeeded", "нет данных")])
    orchestrator = _make(router, worker)
    state = await orchestrator.run(
        question="Какая была чистая прибыль Ford за 2024 год?", mode="ru", run_id="rr"
    )
    assert "недостаточно информации" in state["answer"]  # RU fallback
    assert "инвестиционная рекомендация" in state["answer"]  # RU disclaimer


@pytest.mark.asyncio
async def test_no_data_triggers_exactly_one_replan_with_changed_plan() -> None:
    router = FakeRouter(
        plans=[
            _plan("fetch revenue from wrong table"),
            _plan("fetch revenue from latest_facts"),
        ]
    )
    worker = FakeWorker([_result("no_data", "NO_DATA: nothing"), _result("succeeded", "found")])
    orchestrator = _make(router, worker)
    state = await orchestrator.run(question="revenue?", mode="us", run_id="r2")
    assert router.plan_calls == 2  # initial plan + exactly one replan
    assert state["replans"] == 1
    goals = [call.goal for call in worker.calls]
    assert goals[0] != goals[1]  # the replanned goal actually changed
    assert not state["partial"]


@pytest.mark.asyncio
async def test_replans_exhausted_leads_to_partial_synthesis() -> None:
    router = FakeRouter(plans=[_plan("try one"), _plan("try two")])
    worker = FakeWorker([_result("no_data"), _result("no_data")])
    orchestrator = _make(router, worker)
    state = await orchestrator.run(question="q", mode="us", run_id="r3")
    assert state["replans"] == 1  # max_replans respected
    assert state["partial"] is True
    assert "partial=True" in state["answer"]


@pytest.mark.asyncio
async def test_cost_budget_exhaustion_synthesizes_partial_without_exceptions() -> None:
    router = FakeRouter(plans=[_plan("step one", "step two", "step three")])
    worker = FakeWorker([_result("succeeded", "one"), _result("succeeded", "two")])
    costs = iter([0.0, 0.2, 0.9, 0.9])  # third check exceeds the 0.5 cap

    async def probe(run_id: str) -> tuple[float, int]:
        return next(costs), 0

    orchestrator = _make(router, worker, usage_probe=probe)
    state = await orchestrator.run(question="q", mode="us", run_id="r4")
    assert len(worker.calls) == 2  # step three was never dispatched
    assert state["partial"] is True
    assert "cost" in state["partial_reason"]
    assert state["answer"].startswith("SYNTH")


@pytest.mark.asyncio
async def test_follow_up_in_same_session_sees_dialogue_context() -> None:
    router = FakeRouter(classification=Classification(complexity="simple"))
    worker = FakeWorker([_result("succeeded", "first"), _result("succeeded", "second")])
    orchestrator = _make(router, worker, checkpointer=InMemorySaver())
    first = await orchestrator.run(question="Q1?", mode="us", run_id="ra", session_id="sess-1")
    second = await orchestrator.run(
        question="Q2 follow-up?", mode="us", run_id="rb", session_id="sess-1"
    )
    assert len(first["dialogue"]) == 1
    assert len(second["dialogue"]) == 2  # checkpointer carried the first turn
    assert second["dialogue"][0]["question"] == "Q1?"


@pytest.mark.asyncio
async def test_advice_question_short_circuits_without_planning_or_workers() -> None:
    # An open-ended advice question must never reach the planner or a worker: it
    # has no analyzable target, so planning it burns budget (the bug the fix
    # addresses). It gets a deterministic non-advice reply and finishes cleanly.
    router = FakeRouter()  # any LLM call here would be a regression
    worker = FakeWorker([])  # must never be dispatched (would IndexError if it were)
    orchestrator = _make(router, worker)
    state = await orchestrator.run(
        question="Во что вложиться чтобы стать богатым?", mode="ru", run_id="radv"
    )
    assert router.plan_calls == 0 and router.synth_calls == 0  # no LLM spend
    assert worker.calls == []  # no worker dispatched
    assert state["plan"] == []  # never planned
    assert not state["partial"]  # honest, complete reply -> status succeeded
    assert "инвестиционных рекомендаций" in state["answer"]
    assert "инвестиционная рекомендация" in state["answer"]  # disclaimer (RU)


def test_sanitize_plan_fixes_ids_needs_and_caps_length() -> None:
    draft = PlanDraft(
        steps=[
            PlanDraftStep(id="a", goal="g1", needs=["ghost", "a"]),
            PlanDraftStep(id="a", goal="g2 duplicate id", needs=["a"]),
            PlanDraftStep(id="c", goal="g3", needs=["a"]),
            PlanDraftStep(id="d", goal="over the cap"),
        ]
    )
    steps = _sanitize_plan(draft, max_steps=3)
    assert [s.id for s in steps] == ["a", "s2", "c"]
    assert steps[0].needs == []  # ghost and self-reference dropped
    assert steps[1].needs == ["a"]


def test_merge_replan_keeps_done_steps_verbatim() -> None:
    done = [PlanStep(id="s1", goal="done goal", status="done", result_summary="ok")]
    drafted = [
        PlanStep(id="s1", goal="OVERWRITE ATTEMPT"),
        PlanStep(id="s2", goal="new step", needs=["s1"]),
    ]
    merged = _merge_replan(done, drafted)
    assert merged[0].goal == "done goal" and merged[0].status == "done"
    assert merged[1].id == "s2" and merged[1].status == "pending"


def test_next_ready_respects_needs() -> None:
    steps = [
        PlanStep(id="s1", goal="a", status="done"),
        PlanStep(id="s2", goal="b", needs=["s1", "s3"]),
        PlanStep(id="s3", goal="c"),
    ]
    ready = _next_ready(steps)
    assert ready is not None and ready.id == "s3"  # s2 waits for s3


@pytest.mark.asyncio
async def test_failover_to_local_when_remote_unreachable() -> None:
    router = FakeRouter(classification=Classification(complexity="simple"))
    remote = UnreachableWorker()
    local = LocalFakeWorker([_result("succeeded", "local answered")])
    orchestrator = Orchestrator(
        worker_clients={"remote": remote, "local": local},
        worker_skills={
            "remote": ["financial_sql_analysis"],
            "local": ["financial_sql_analysis"],
        },
        router=router,  # type: ignore[arg-type]
        budget_profile=dict(BUDGET),
        track_db_steps=False,
    )
    state = await orchestrator.run(question="q", mode="us", run_id="rf")
    assert remote.calls == 1  # remote is the primary and was tried first
    assert len(local.calls) == 1  # ToolError failed over to the local worker
    assert not state["partial"]  # failover is transparent — the run still succeeds
    assert state["answer"].startswith("SYNTH")


@pytest.mark.asyncio
async def test_all_workers_unreachable_fails_the_step_not_the_run() -> None:
    router = FakeRouter(classification=Classification(complexity="simple"), plans=[_plan("retry")])
    a, b = UnreachableWorker(), UnreachableWorker()
    orchestrator = Orchestrator(
        worker_clients={"a": a, "b": b},
        worker_skills={"a": ["financial_sql_analysis"], "b": ["financial_sql_analysis"]},
        router=router,  # type: ignore[arg-type]
        budget_profile=dict(BUDGET),
        track_db_steps=False,
    )
    state = await orchestrator.run(question="q", mode="us", run_id="ru")
    assert a.calls >= 1 and b.calls >= 1  # both candidates were attempted before giving up
    assert state["partial"] is True  # no reachable worker → partial synthesis, no crash


@pytest.mark.asyncio
async def test_round_robin_spreads_primary_across_workers() -> None:
    router = FakeRouter(classification=Classification(complexity="simple"))
    a = LocalFakeWorker([_result("succeeded", "a")] * 3)
    b = LocalFakeWorker([_result("succeeded", "b")] * 3)
    orchestrator = Orchestrator(
        worker_clients={"a": a, "b": b},
        worker_skills={"a": ["financial_sql_analysis"], "b": ["financial_sql_analysis"]},
        router=router,  # type: ignore[arg-type]
        budget_profile=dict(BUDGET),
        track_db_steps=False,
    )
    for i in range(3):
        await orchestrator.run(question=f"q{i}", mode="us", run_id=f"rr{i}")
    # counter 0→a (primary), 1→b, 2→a again: work is spread, not pinned to one node
    assert len(a.calls) == 2
    assert len(b.calls) == 1


def test_find_contradiction_over_two_percent() -> None:
    key_values = {
        "aapl_revenue_fy2024": {"value": 391.0, "step_id": "s2"},
        "__first__aapl_revenue_fy2024": {"value": 350.0, "step_id": "s1"},
    }
    found = _find_contradiction(key_values)
    assert found is not None and found[0] == "aapl_revenue_fy2024"
    close = {
        "k": {"value": 100.0, "step_id": "s2"},
        "__first__k": {"value": 101.0, "step_id": "s1"},
    }
    assert _find_contradiction(close) is None
