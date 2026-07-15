"""Plan-and-Execute orchestrator (T-020; ARCHITECTURE §3.1, CONTRACTS §10).

LangGraph StateGraph: classify -> (plan) -> execute -> assess -> {execute |
replan | synthesize} -> guardrail -> finalize. Simple factual questions take
the short path (one-step plan, no planning LLM call — cost control).

Honesty and control invariants:
- budget (steps / wall clock / cost / tokens, config/budgets.yaml) is checked
  before every worker dispatch; overrun is not an error — synthesis runs with
  ``partial=True`` and the answer says what is missing;
- a failed / no_data step triggers at most ``max_replans`` replans, then the
  run synthesizes partially;
- numeric contradiction (same key value diverging > 2% between steps) is a
  deterministic replan signal, confirmed by a cheap LLM assess call;
- the state is JSON-serializable: the Postgres checkpointer (thread_id =
  session_id) gives follow-up questions the dialogue context.
"""

from __future__ import annotations

import operator
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, cast

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from common.agents import DEFAULT_ALLOWED_TOOLS, TaskContext, WorkerBudget, WorkerResult, WorkerTask
from common.config import load_yaml_config
from common.db import get_session_factory
from common.errors import ToolError
from common.logging import get_logger
from common.tracing import TraceBus
from model_router.router import RouterClient
from orchestrator.persistence import finish_step, insert_step
from orchestrator.worker_client import WorkerClient

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

SKILL_TOOLS: dict[str, list[str]] = {
    "financial_sql_analysis": ["sql_query", "schema_introspect"],
    "narrative_rag_analysis": ["rag_search"],
    # T-033: EOD price context; sql_query rides along for company/date lookups.
    "price_history_analysis": ["price_enrich", "sql_query"],
}
Skill = Literal["financial_sql_analysis", "narrative_rag_analysis", "price_history_analysis"]

CONTRADICTION_TOLERANCE = 0.02
RESULT_SUMMARY_CHARS = 400
DIALOGUE_CONTEXT_TURNS = 2
MAX_GUARDRAIL_SPANS = 8
# Least-wrong recovery when synthesis returns an empty completion (see _finalize).
EMPTY_ANSWER_FALLBACK_EN = (
    "I could not find enough information in the available data to answer this question."
)
EMPTY_ANSWER_FALLBACK_RU = (
    "В доступных данных недостаточно информации, чтобы ответить на этот вопрос."
)


def _empty_answer_fallback(question: str) -> str:
    """Least-wrong recovery text when a synthesis completion comes back empty."""
    from orchestrator.guardrail import is_russian

    return EMPTY_ANSWER_FALLBACK_RU if is_russian(question) else EMPTY_ANSWER_FALLBACK_EN


def _load_prompt(name: str) -> str:
    text_ = (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    if text_.startswith("---"):
        rest = text_.partition("---")[2].partition("---")[2]
        return rest.strip()
    return text_.strip()


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    goal: str
    needs: list[str] = Field(default_factory=list)
    skill: Skill = "financial_sql_analysis"
    status: Literal["pending", "done", "failed", "no_data"] = "pending"
    result_summary: str = ""


class PlanDraftStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    goal: str
    needs: list[str] = Field(default_factory=list)
    skill: Skill = "financial_sql_analysis"


class PlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[PlanDraftStep]


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complexity: Literal["simple", "analytical"]
    skill: Skill = "financial_sql_analysis"


class KeyValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    unit: str = ""
    period: str = ""


class StepKeyValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, KeyValue] = Field(default_factory=dict)


class ContradictionVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_contradiction: bool


class OrchestratorState(TypedDict, total=False):
    question: str
    mode: str
    run_id: str
    dialogue: Annotated[list[dict[str, str]], operator.add]
    plan: list[dict[str, Any]]
    results: dict[str, dict[str, Any]]
    key_values: dict[str, dict[str, Any]]
    citations: list[dict[str, Any]]
    replans: int
    partial: bool
    partial_reason: str
    answer: str
    advice_refused: bool
    started_monotonic: float


def _plan_steps(state: OrchestratorState) -> list[PlanStep]:
    return [PlanStep.model_validate(step) for step in state.get("plan", [])]


def _dump_plan(steps: list[PlanStep]) -> list[dict[str, Any]]:
    return [step.model_dump() for step in steps]


def _dialogue_context(state: OrchestratorState) -> str:
    turns = state.get("dialogue", [])[-DIALOGUE_CONTEXT_TURNS:]
    if not turns:
        return ""
    rendered = "\n".join(f"Q: {turn['question']}\nA: {turn['answer'][:500]}" for turn in turns)
    return f"Recent conversation (context for follow-ups):\n{rendered}\n\n"


class Orchestrator:
    """Compiled Plan-and-Execute graph bound to a worker registry and router."""

    def __init__(
        self,
        *,
        worker_clients: dict[str, WorkerClient],
        worker_skills: dict[str, list[str]],
        router: RouterClient | None = None,
        trace_bus: TraceBus | None = None,
        budget_profile: dict[str, Any] | None = None,
        checkpointer: Any | None = None,
        track_db_steps: bool = True,
        usage_probe: Callable[[str], Awaitable[tuple[float, int]]] | None = None,
    ) -> None:
        self._clients = worker_clients
        self._skills = worker_skills
        self._router = router or RouterClient(trace_bus=trace_bus)
        self._bus = trace_bus
        self._budget = budget_profile or _load_budget_profile()
        self._track_db_steps = track_db_steps
        self._usage_probe = usage_probe or (self._db_usage if track_db_steps else None)
        self._dispatch_counter = 0  # round-robins the primary worker per skill (T-031)
        self._log = get_logger(node="orchestrator")
        self._graph = self._build().compile(checkpointer=checkpointer)

    # -- graph wiring --------------------------------------------------------

    def _build(self) -> StateGraph[OrchestratorState, None, OrchestratorState, OrchestratorState]:
        graph = StateGraph(OrchestratorState)
        graph.add_node("classify", self._classify)
        graph.add_node("plan", self._plan)
        graph.add_node("execute", self._execute)
        graph.add_node("assess", self._assess)
        graph.add_node("replan", self._replan)
        graph.add_node("synthesize", self._synthesize)
        graph.add_node("guardrail", self._guardrail)
        graph.add_node("finalize", self._finalize)
        graph.set_entry_point("classify")
        graph.add_conditional_edges(
            "classify",
            self._route_after_classify,
            {"execute": "execute", "plan": "plan", "finalize": "finalize"},
        )
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", "assess")
        graph.add_conditional_edges(
            "assess",
            self._route_after_assess,
            {"execute": "execute", "replan": "replan", "synthesize": "synthesize"},
        )
        graph.add_edge("replan", "execute")
        graph.add_edge("synthesize", "guardrail")
        graph.add_edge("guardrail", "finalize")
        graph.add_edge("finalize", END)
        return graph

    async def run(
        self, *, question: str, mode: str, run_id: str, session_id: str | None = None
    ) -> OrchestratorState:
        config: Any = {
            "configurable": {"thread_id": session_id or run_id},
            "recursion_limit": 10 + 4 * int(self._budget["max_plan_steps"]),
        }
        initial: OrchestratorState = {
            "question": question,
            "mode": mode,
            "run_id": run_id,
            "started_monotonic": time.monotonic(),
        }
        result = await self._graph.ainvoke(cast(Any, initial), config)
        return cast(OrchestratorState, result)

    # -- nodes ---------------------------------------------------------------

    @staticmethod
    def _route_after_classify(state: OrchestratorState) -> str:
        """advice short-circuit -> finalize; simple -> execute; else -> plan."""
        if state.get("advice_refused"):
            return "finalize"
        return "execute" if state.get("plan") else "plan"

    async def _classify(self, state: OrchestratorState) -> OrchestratorState:
        """Route simple questions to a one-step plan; reset per-run channels.

        First, short-circuit an open-ended advice-seeking question (T-022): it
        has no analyzable target, so planning it wastes budget on a doomed
        research plan. Reply with the non-advice message and skip to finalize.
        """
        from orchestrator.guardrail import build_advice_question_refusal, is_advice_question

        fresh: OrchestratorState = {
            "plan": [],
            "results": {},
            "key_values": {},
            "citations": [],
            "replans": 0,
            "partial": False,
            "partial_reason": "",
            "answer": "",
        }
        if is_advice_question(state["question"]):
            self._log.info("advice_question_refused")
            fresh["answer"] = build_advice_question_refusal(state["question"])
            fresh["advice_refused"] = True
            await self._publish(
                "guardrail",
                {"triggered": True, "action": "refuse_advice_question", "spans": []},
            )
            return fresh
        try:
            verdict = await self._router.chat_structured(
                "route",
                [
                    ("system", _load_prompt("orchestrator_classify")),
                    ("user", _dialogue_context(state) + f"Question: {state['question']}"),
                ],
                Classification,
            )
        except Exception as exc:  # noqa: BLE001 — classification must not kill the run
            self._log.warning("classify_failed", error=str(exc)[:200])
            return fresh  # fall through to full planning
        if verdict.complexity == "simple":
            step = PlanStep(id="s1", goal=state["question"], skill=verdict.skill)
            fresh["plan"] = _dump_plan([step])
            await self._publish("plan_created", {"steps": fresh["plan"], "classified": "simple"})
        return fresh

    async def _plan(self, state: OrchestratorState) -> OrchestratorState:
        max_steps = int(self._budget["max_plan_steps"])
        draft = await self._router.chat_structured(
            "plan",
            [
                ("system", _load_prompt("orchestrator_plan")),
                (
                    "user",
                    _dialogue_context(state)
                    + f"Question: {state['question']}\nData mode: {state.get('mode', 'us')}\n"
                    f"Step limit: {max_steps}",
                ),
            ],
            PlanDraft,
        )
        steps = _sanitize_plan(draft, max_steps)
        await self._publish("plan_created", {"steps": _dump_plan(steps)})
        return {"plan": _dump_plan(steps)}

    async def _execute(self, state: OrchestratorState) -> OrchestratorState:
        steps = _plan_steps(state)
        step = _next_ready(steps)
        if step is None:
            return {}
        overrun = await self._budget_overrun(state)
        if overrun:
            self._log.info("budget_exceeded", reason=overrun)
            return {"partial": True, "partial_reason": overrun}

        results = dict(state.get("results", {}))
        step_db_id = (
            await insert_step(uuid.UUID(state["run_id"]), node="worker", goal=step.goal)
            if self._track_db_steps
            else uuid.uuid4()
        )
        await self._publish(
            "step_started", {"node": "worker", "goal": step.goal, "plan_step": step.id}
        )
        prior = [
            {"goal": s.goal, "summary": s.result_summary}
            for s in steps
            if s.id in step.needs and s.status == "done"
        ]
        task = WorkerTask(
            task_id=str(step_db_id),
            run_id=state["run_id"],
            goal=step.goal,
            context=TaskContext(prior_results=prior, mode=state.get("mode", "us")),
            allowed_tools=SKILL_TOOLS.get(step.skill, list(DEFAULT_ALLOWED_TOOLS)),
            budget=WorkerBudget(
                max_iterations=int(self._budget["worker_max_iterations"]),
                # Profile-driven step deadline (T-041/Q-22): CPU-bound narrative
                # rag needs headroom on a slow eval runner. Falls back to the
                # WorkerBudget default (90s) for budget dicts that omit the key.
                deadline_s=float(self._budget.get("worker_deadline_s", WorkerBudget().deadline_s)),
            ),
        )
        candidates = self._ordered_clients(step.skill)
        result: WorkerResult | None = None
        client: WorkerClient | None = None
        transport_errors: list[str] = []
        for cand_name, candidate in candidates:
            try:
                result = await candidate.run(task)
                client = candidate
                break
            except ToolError as exc:
                # Transport/unreachable (remote node down) — fail over to the
                # next candidate (local-preferred, T-031). A worker that ran
                # and merely failed the task returns a WorkerResult, not this.
                transport_errors.append(f"{cand_name}: {exc}")
                self._log.warning("worker_failover", worker=cand_name, error=str(exc)[:200])
                await self._publish(
                    "budget",
                    {
                        "degradation": "worker_unreachable",
                        "worker": cand_name,
                        "error": str(exc)[:200],
                    },
                )
                continue
            except Exception as exc:  # noqa: BLE001 — a real crash is a failed step, not failover
                self._log.error("worker_crashed", step=step.id, error=str(exc)[:300])
                step.status = "failed"
                step.result_summary = f"worker crashed: {exc}"[:RESULT_SUMMARY_CHARS]
                if self._track_db_steps:
                    await finish_step(step_db_id, status="failed", output={}, error=str(exc)[:500])
                await self._publish("step_finished", {"status": "failed", "plan_step": step.id})
                return {"plan": _dump_plan(steps), "results": results}

        if result is None or client is None:
            # Every candidate worker was unreachable.
            reason = "; ".join(transport_errors)[:RESULT_SUMMARY_CHARS]
            self._log.error("all_workers_unreachable", step=step.id, reason=reason)
            step.status = "failed"
            step.result_summary = f"no worker reachable: {reason}"[:RESULT_SUMMARY_CHARS]
            if self._track_db_steps:
                await finish_step(step_db_id, status="failed", output={}, error=reason)
            await self._publish("step_finished", {"status": "failed", "plan_step": step.id})
            return {"plan": _dump_plan(steps), "results": results}

        if not client.relays_trace:
            # Remote worker: its trace arrived with the result — replay it on
            # our bus so the stream and llm_calls/tool_calls (cost accounting
            # for the budget) stay complete.
            for raw in result.trace:
                try:
                    await self._publish(str(raw.get("event")), dict(raw.get("payload", {})))
                except Exception as exc:  # noqa: BLE001 — relay must not kill the step
                    self._log.warning("trace_relay_failed", error=str(exc)[:120])

        status_map: dict[str, Literal["pending", "done", "failed", "no_data"]] = {
            "succeeded": "done",
            "no_data": "no_data",
            "budget_exceeded": "done",  # partial worker answer is still usable
            "failed": "failed",
        }
        step.status = status_map[result.status]
        step.result_summary = result.answer[:RESULT_SUMMARY_CHARS]
        results[step.id] = result.model_dump()
        citations = list(state.get("citations", []))
        citations.extend(result.evidence.citations)
        key_values = dict(state.get("key_values", {}))
        if step.status == "done" and step.skill == "financial_sql_analysis":
            for key, kv in (await self._extract_key_values(step, result.answer)).items():
                previous = key_values.get(key)
                if (
                    previous is not None
                    and previous.get("step_id") != kv.get("step_id")
                    and f"__first__{key}" not in key_values
                ):
                    # Same quantity reported by another step: keep the first
                    # value in a shadow key so assess can compare the pair.
                    key_values[f"__first__{key}"] = previous
                key_values[key] = kv
        if self._track_db_steps:
            await finish_step(
                step_db_id,
                status=result.status,
                output={"answer": result.answer[:2000], "usage": result.usage.model_dump()},
                error=None,
            )
        await self._publish(
            "step_finished",
            {
                "status": result.status,
                "plan_step": step.id,
                "worker_node": result.node,  # WORKER_NODE_NAME from the worker (T-021)
                "answer_preview": result.answer[:200],
            },
        )
        return {
            "plan": _dump_plan(steps),
            "results": results,
            "citations": citations,
            "key_values": key_values,
        }

    async def _assess(self, state: OrchestratorState) -> OrchestratorState:
        """Deterministic checks; routing itself happens in _route_after_assess."""
        contradiction = _find_contradiction(state.get("key_values", {}))
        if contradiction and not state.get("partial"):
            confirmed = await self._confirm_contradiction(state, contradiction)
            if confirmed:
                return {
                    "partial_reason": (
                        f"contradiction on {contradiction[0]}: "
                        f"{contradiction[1]} vs {contradiction[2]}"
                    )
                }
        return {}

    def _route_after_assess(self, state: OrchestratorState) -> str:
        if state.get("partial"):
            return "synthesize"
        steps = _plan_steps(state)
        max_replans = int(self._budget["max_replans"])
        if state.get("partial_reason", "").startswith("contradiction") and (
            state.get("replans", 0) < max_replans
        ):
            return "replan"
        problems = [s for s in steps if s.status in ("failed", "no_data")]
        if problems and state.get("replans", 0) < max_replans:
            return "replan"
        if _next_ready(steps) is not None and not problems:
            return "execute"
        return "synthesize"

    async def _replan(self, state: OrchestratorState) -> OrchestratorState:
        steps = _plan_steps(state)
        max_steps = int(self._budget["max_plan_steps"])
        done = [s for s in steps if s.status == "done"]
        problems = [s for s in steps if s.status in ("failed", "no_data")]
        reason = state.get("partial_reason") or "; ".join(
            f"step {s.id} ({s.goal[:80]}) -> {s.status}: {s.result_summary[:120]}" for s in problems
        )
        draft = await self._router.chat_structured(
            "plan",
            [
                ("system", _load_prompt("orchestrator_plan")),
                (
                    "user",
                    f"Question: {state['question']}\n"
                    f"Current plan: {[s.model_dump() for s in steps]}\n"
                    f"Problem to work around: {reason}\n"
                    f"Step limit: {max_steps}. Completed steps stay as-is; return the FULL "
                    "updated plan (completed steps included, unchanged).",
                ),
            ],
            PlanDraft,
        )
        new_steps = _merge_replan(done, _sanitize_plan(draft, max_steps))
        await self._publish(
            "plan_updated", {"steps": _dump_plan(new_steps), "reason": reason[:300]}
        )
        return {
            "plan": _dump_plan(new_steps),
            "replans": state.get("replans", 0) + 1,
            "partial_reason": "",
        }

    def _results_digest(self, state: OrchestratorState, *, per_step_chars: int = 3000) -> str:
        steps = _plan_steps(state)
        return "\n\n".join(
            f"## Step {s.id} [{s.status}]: {s.goal}\n"
            + str(state.get("results", {}).get(s.id, {}).get("answer", s.result_summary))[
                :per_step_chars
            ]
            for s in steps
        )

    async def _synthesize(self, state: OrchestratorState) -> OrchestratorState:
        steps = _plan_steps(state)
        digest = self._results_digest(state)
        partial = bool(state.get("partial")) or any(
            s.status in ("failed", "no_data", "pending") for s in steps
        )
        partial_reason = state.get("partial_reason") or "some steps did not finish"
        partial_note = (
            f"\nIMPORTANT: the analysis is PARTIAL ({partial_reason}) "
            "— state explicitly what is missing."
            if partial
            else ""
        )
        response = await self._router.chat(
            "synthesize",
            [
                ("system", _load_prompt("orchestrator_synthesize")),
                (
                    "user",
                    f"Question: {state['question']}\n\nStep results:\n{digest}\n"
                    f"Known key values: {state.get('key_values', {})}{partial_note}",
                ),
            ],
        )
        # An empty (or whitespace-only) completion can rarely come back under
        # model load. Normalize it to an honest "no result" admission here, at
        # the source, so the guardrail appends the disclaimer as usual and no
        # blank/near-blank answer reaches run_finished (see also _finalize).
        answer = response.text.strip()
        if not answer:
            answer = _empty_answer_fallback(state["question"])
            self._log.warning("empty_synthesis_fallback", run_id=state.get("run_id"))
        return {"answer": answer, "partial": partial}

    async def _guardrail(self, state: OrchestratorState) -> OrchestratorState:
        """Non-advice guardrail (T-022; CONTRACTS §12): check -> one
        re-synthesis -> template refusal; disclaimer on every answer."""
        from orchestrator.guardrail import (
            build_refusal,
            check_advice,
            disclaimer_for,
            find_advice_spans,
        )

        question = state["question"]
        answer = state.get("answer", "")
        action = "pass"
        verdict = await check_advice(answer, self._router) if answer else None
        if verdict and verdict.advice:
            action = "resynthesize"
            self._log.warning("guardrail_triggered", spans=verdict.spans[:3])
            response = await self._router.chat(
                "synthesize",
                [
                    ("system", _load_prompt("orchestrator_synthesize")),
                    (
                        "user",
                        f"Question: {question}\n\nStep results:\n"
                        f"{self._results_digest(state, per_step_chars=1500)}\n"
                        "Your previous draft contained INVESTMENT ADVICE, which is "
                        f"forbidden. Offending fragments: {verdict.spans}. Rewrite the "
                        "answer with facts, calculations and comparisons only — no "
                        "recommendations to buy/sell/hold, no price targets, no "
                        "allocation suggestions.",
                    ),
                ],
            )
            # Like _synthesize, the re-synthesis completion can rarely come back
            # empty/whitespace-only under model load. Normalize it here so no
            # near-blank answer (just the appended disclaimer) reaches
            # run_finished — _finalize's strip() backstop cannot catch that case
            # once the disclaimer makes the string non-empty.
            answer = response.text.strip()
            if not answer:
                answer = _empty_answer_fallback(question)
                self._log.warning("empty_resynthesis_fallback", run_id=state.get("run_id"))
            # Second pass is regex-only: deterministic and free; a stubborn
            # draft gets the template refusal built from collected facts.
            if find_advice_spans(answer):
                action = "refuse"
                facts = "\n".join(
                    f"- {key}: {kv.get('value')} {kv.get('unit', '')} ({kv.get('period', '')})"
                    for key, kv in state.get("key_values", {}).items()
                    if not key.startswith("__first__")
                )
                answer = build_refusal(question, facts)
        answer = f"{answer}\n\n_{disclaimer_for(question)}_" if answer else answer
        await self._publish(
            "guardrail",
            {
                "triggered": bool(verdict and verdict.advice),
                "action": action,
                "spans": (verdict.spans if verdict else [])[:5],
            },
        )
        if self._track_db_steps:
            step_db_id = await insert_step(
                uuid.UUID(state["run_id"]), node="guardrail", goal="non-advice check"
            )
            await finish_step(
                step_db_id,
                status="succeeded",
                output={
                    "triggered": bool(verdict and verdict.advice),
                    "action": action,
                    "spans": (verdict.spans if verdict else [])[:MAX_GUARDRAIL_SPANS],
                },
                error=None,
            )
        return {"answer": answer}

    async def _finalize(self, state: OrchestratorState) -> OrchestratorState:
        answer = state.get("answer", "")
        if not answer.strip():
            # A blank answer on a terminal run is never correct: synthesis (or
            # the guardrail re-synthesis) can rarely return an empty completion
            # under model load, and an empty string then propagates to
            # run_finished as a silent non-answer. Least-wrong recovery is an
            # honest "no result" admission with the standard disclaimer — this
            # also keeps no_data-honesty scoring truthful (an empty answer would
            # otherwise score 0.0 and red an otherwise-clean eval run).
            from orchestrator.guardrail import disclaimer_for

            question = state["question"]
            answer = f"{_empty_answer_fallback(question)}\n\n_{disclaimer_for(question)}_"
            self._log.warning("empty_answer_fallback", run_id=state.get("run_id"))
        return {"answer": answer, "dialogue": [{"question": state["question"], "answer": answer}]}

    # -- helpers -------------------------------------------------------------

    def _ordered_clients(self, skill: str) -> list[tuple[str, WorkerClient]]:
        """Dispatch order for a skill (T-031, Q-20): round-robin the primary
        across workers that have the skill so multi-step questions spread over
        nodes, then a local-preferred failover tail so an unreachable remote
        never breaks a run.
        """
        matching = [
            (name, self._clients[name])
            for name, skills in self._skills.items()
            if skill in skills and name in self._clients
        ]
        if not matching:  # no skill match: any worker (single-node phase)
            matching = list(self._clients.items())
        if len(matching) <= 1:
            return matching
        rotation = self._dispatch_counter % len(matching)
        self._dispatch_counter += 1
        rotated = matching[rotation:] + matching[:rotation]
        primary, rest = rotated[:1], rotated[1:]
        rest.sort(key=lambda item: 0 if item[1].is_local else 1)  # prefer local for failover
        return primary + rest

    async def _extract_key_values(self, step: PlanStep, answer: str) -> dict[str, dict[str, Any]]:
        """Cheap structured extraction feeding the contradiction rule."""
        try:
            extracted = await self._router.chat_structured(
                "extract",
                [
                    (
                        "system",
                        "Extract every concrete financial figure from the analyst text as "
                        "key_values. Key format: <TICKER>_<metric>_<period> lowercased, e.g. "
                        "aapl_revenue_fy2024. Use the numeric value as stated (keep units in "
                        "the unit field). JSON only.",
                    ),
                    ("user", answer[:4000]),
                ],
                StepKeyValues,
            )
        except Exception as exc:  # noqa: BLE001 — extraction is best-effort
            self._log.warning("key_values_extract_failed", step=step.id, error=str(exc)[:200])
            return {}
        return {
            key: {**kv.model_dump(), "step_id": step.id} for key, kv in extracted.values.items()
        }

    async def _confirm_contradiction(
        self, state: OrchestratorState, found: tuple[str, float, float]
    ) -> bool:
        """LLM second signal on a deterministic contradiction hit."""
        key, a, b = found
        try:
            verdict = await self._router.chat_structured(
                "route",
                [
                    (
                        "system",
                        "Two analysis steps reported the same quantity with different values. "
                        "Decide if this is a real contradiction (same metric, same period, "
                        "incompatible values) or a false alarm (different scopes/units). "
                        "JSON only.",
                    ),
                    ("user", f"Key: {key}; values: {a} vs {b}. Question: {state['question']}"),
                ],
                ContradictionVerdict,
            )
        except Exception:  # noqa: BLE001 — on doubt, trust the deterministic signal
            return True
        return verdict.is_contradiction

    async def _db_usage(self, run_id: str) -> tuple[float, int]:
        """Run cost/tokens so far — llm_calls is the source of truth."""
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT COALESCE(SUM(cost_usd), 0), "
                        "COALESCE(SUM(tokens_in + tokens_out), 0) "
                        "FROM llm_calls WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
            ).one()
        return float(row[0]), int(row[1])

    async def _budget_overrun(self, state: OrchestratorState) -> str:
        elapsed = time.monotonic() - state.get("started_monotonic", time.monotonic())
        if elapsed > float(self._budget["wall_clock_s_per_run"]):
            return f"wall clock {int(elapsed)}s > {self._budget['wall_clock_s_per_run']}s"
        executed = len(state.get("results", {}))
        if executed >= int(self._budget["max_plan_steps"]):
            return f"executed steps {executed} >= max_plan_steps"
        if self._usage_probe is not None:
            cost, tokens = await self._usage_probe(state["run_id"])
            if cost > float(self._budget["max_cost_usd_per_run"]):
                return f"cost ${cost:.4f} > ${self._budget['max_cost_usd_per_run']}"
            if tokens > int(self._budget["max_tokens_per_run"]):
                return f"tokens {tokens} > {self._budget['max_tokens_per_run']}"
        return ""

    async def _publish(self, event: str, payload: dict[str, Any]) -> None:
        if self._bus is not None:
            await self._bus.publish(event, payload)  # type: ignore[arg-type]


# -- pure helpers (unit-tested directly) --------------------------------------


def _load_budget_profile() -> dict[str, Any]:
    import os

    profiles = load_yaml_config("budgets")["profiles"]
    name = os.environ.get("BUDGET_PROFILE", "dev")
    if name not in profiles:
        raise KeyError(f"unknown BUDGET_PROFILE {name!r}; known: {sorted(profiles)}")
    return dict(profiles[name])


def _sanitize_plan(draft: PlanDraft, max_steps: int) -> list[PlanStep]:
    """Unique ids, known needs, capped length — a bad plan must not crash the run."""
    steps: list[PlanStep] = []
    seen: set[str] = set()
    for raw in draft.steps[:max_steps]:
        step_id = raw.id if raw.id and raw.id not in seen else f"s{len(steps) + 1}"
        seen.add(step_id)
        steps.append(PlanStep(id=step_id, goal=raw.goal, skill=raw.skill, needs=list(raw.needs)))
    known = {s.id for s in steps}
    for step in steps:
        step.needs = [n for n in step.needs if n in known and n != step.id]
    return steps


def _merge_replan(done: list[PlanStep], drafted: list[PlanStep]) -> list[PlanStep]:
    """Completed steps survive replanning verbatim; drafted ones fill the rest."""
    done_ids = {s.id for s in done}
    merged = list(done)
    for step in drafted:
        if step.id not in done_ids:
            step.status = "pending"
            merged.append(step)
    known = {s.id for s in merged}
    for step in merged:
        step.needs = [n for n in step.needs if n in known]
    return merged


def _next_ready(steps: list[PlanStep]) -> PlanStep | None:
    done = {s.id for s in steps if s.status == "done"}
    for step in steps:
        if step.status == "pending" and all(need in done for need in step.needs):
            return step
    return None


def _find_contradiction(
    key_values: dict[str, dict[str, Any]],
) -> tuple[str, float, float] | None:
    """Same key reported by two different steps with > 2% divergence.

    _execute preserves the first step's value under ``__first__<key>`` when a
    later step overwrites the key; this scans those pairs.
    """
    for key, kv in key_values.items():
        if key.startswith("__first__"):
            continue
        shadow = key_values.get(f"__first__{key}")
        if shadow and shadow.get("step_id") != kv.get("step_id"):
            a, b = float(shadow["value"]), float(kv["value"])
            denominator = max(abs(a), abs(b))
            if denominator and abs(a - b) / denominator > CONTRADICTION_TOLERANCE:
                return key, a, b
    return None
