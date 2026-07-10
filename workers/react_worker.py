"""ReAct worker agent (T-013): LangGraph loop over SQL tools.

Contract: ``WorkerTask`` in — ``WorkerResult`` out (CONTRACTS.md §10).
Budget: ``max_iterations`` maps to the graph recursion limit, ``deadline_s``
to an asyncio timeout; both overruns end as status ``budget_exceeded`` with
a partial answer, never as an exception. Every step publishes TraceEvents
(agent_thought / tool_call_* / llm_call) to the provided TraceBus.

LLM calls go through ``model_router.simple_client`` — a stub with the
router-shaped interface; the full tiered router replaces it in T-016.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from common.agents import WorkerEvidence, WorkerResult, WorkerTask, WorkerUsage
from common.logging import bind_run_context, get_logger, reset_run_context
from common.tracing import TraceBus, TraceEvent
from model_router.simple_client import simple_chat_model
from tools.sql.core import schema_introspect, sql_query

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "worker_react.md"
THOUGHT_PREVIEW_CHARS = 300
PREVIEW_CHARS = 500
NO_DATA_MARKER = "NO_DATA:"

ToolImpl = Callable[..., Awaitable[dict[str, Any]]]


def load_worker_prompt() -> str:
    """Prompt body without the version header (id/task_class/version)."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    if text.startswith("---"):
        rest = text.partition("---")[2]
        rest = rest.partition("---")[2]
        return rest.strip()
    return text.strip()


def _default_tool_impls() -> dict[str, ToolImpl]:
    async def _schema_introspect() -> dict[str, Any]:
        return await schema_introspect()

    return {"sql_query": sql_query, "schema_introspect": _schema_introspect}


def _build_tools(
    task: WorkerTask,
    impls: dict[str, ToolImpl],
    bus: TraceBus,
    usage: WorkerUsage,
    evidence: WorkerEvidence,
) -> list[StructuredTool]:
    tools: list[StructuredTool] = []

    async def _traced_call(tool_name: str, arguments: dict[str, Any]) -> str:
        usage.tool_calls += 1
        await bus.publish("tool_call_started", {"tool": tool_name, "arguments": arguments})
        result = await impls[tool_name](**arguments)
        is_error = isinstance(result, dict) and "error" in result
        payload = json.dumps(result, ensure_ascii=False, default=str)
        await bus.publish(
            "tool_call_finished",
            {
                "tool": tool_name,
                "status": "error" if is_error else "ok",
                "preview": payload[:PREVIEW_CHARS],
            },
        )
        if tool_name == "sql_query" and not is_error:
            evidence.facts.append(
                {"sql": arguments.get("sql", ""), "row_count": result.get("row_count")}
            )
        return payload

    if "sql_query" in task.allowed_tools and "sql_query" in impls:

        async def _sql_query(sql: str, row_limit: int = 50) -> str:
            """Run one read-only SQL SELECT against the financial database."""
            return await _traced_call("sql_query", {"sql": sql, "row_limit": row_limit})

        tools.append(
            StructuredTool.from_function(
                coroutine=_sql_query,
                name="sql_query",
                description=(
                    "Execute a read-only PostgreSQL SELECT. Main entry point: the "
                    "latest_facts view (ticker, metric, value, unit, period_end, "
                    "fiscal_period). On error, read `hint` and fix the query."
                ),
            )
        )

    if "schema_introspect" in task.allowed_tools and "schema_introspect" in impls:

        async def _schema() -> str:
            """Describe available tables, canonical metrics and example queries."""
            return await _traced_call("schema_introspect", {})

        tools.append(
            StructuredTool.from_function(
                coroutine=_schema,
                name="schema_introspect",
                description=(
                    "List tables/columns, the canonical metric dictionary and "
                    "example SQL queries. Call when unsure about the schema."
                ),
            )
        )
    return tools


def _render_task(task: WorkerTask) -> str:
    parts = [f"Sub-task: {task.goal}", f"Data mode: {task.context.mode}"]
    if task.context.constraints:
        parts.append("Constraints: " + "; ".join(task.context.constraints))
    if task.context.prior_results:
        parts.append(
            "Prior step results: "
            + json.dumps(task.context.prior_results, ensure_ascii=False, default=str)[:2000]
        )
    return "\n".join(parts)


async def _publish_ai_message(
    message: AIMessage,
    bus: TraceBus,
    usage: WorkerUsage,
    *,
    provider: str,
    model_name: str,
) -> str:
    """Publish thought/llm_call events for one AI message; returns its text."""
    content = message.content if isinstance(message.content, str) else str(message.content)
    if content.strip():
        await bus.publish("agent_thought", {"text": content.strip()[:THOUGHT_PREVIEW_CHARS]})
    tokens_in = tokens_out = 0
    if message.usage_metadata:
        tokens_in = int(message.usage_metadata.get("input_tokens", 0))
        tokens_out = int(message.usage_metadata.get("output_tokens", 0))
        usage.tokens_in += tokens_in
        usage.tokens_out += tokens_out
    await bus.publish(
        "llm_call",
        {
            "task_class": "reason",
            "provider": provider,
            "model": model_name,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tool_calls": [call["name"] for call in message.tool_calls],
        },
    )
    return content


async def run_worker_task(
    task: WorkerTask,
    *,
    model: BaseChatModel | None = None,
    trace_bus: TraceBus | None = None,
    tool_impls: dict[str, ToolImpl] | None = None,
) -> WorkerResult:
    """Execute one sub-task in a ReAct loop; never raises on budget overruns."""
    bus = trace_bus or TraceBus()
    collected: list[TraceEvent] = []

    async def _collect(event: TraceEvent) -> None:
        if event.run_id == task.run_id:
            collected.append(event)

    bus.subscribe(_collect)
    context_tokens = bind_run_context(run_id=task.run_id, step_id=task.task_id, node="worker")
    usage = WorkerUsage()
    evidence = WorkerEvidence()
    status: str = "failed"
    answer = ""
    last_ai_text = ""
    log = get_logger(node="worker")
    try:
        if model is None:
            chat_model: BaseChatModel = simple_chat_model("reason")
            provider = "deepseek"
        else:
            chat_model = model
            provider = "injected"
        model_name = str(
            getattr(chat_model, "model_name", None)
            or getattr(chat_model, "model", None)
            or type(chat_model).__name__
        )
        impls = {**_default_tool_impls(), **(tool_impls or {})}
        tools = _build_tools(task, impls, bus, usage, evidence)
        agent = create_react_agent(chat_model, tools, prompt=load_worker_prompt())
        # We enforce max_iterations ourselves (langgraph's prebuilt agent ends
        # the stream politely instead of raising); the recursion limit below is
        # only a generous backstop.
        recursion_limit = 2 * task.budget.max_iterations + 8
        iterations = 0
        budget_hit = False
        pending_tool_calls = False
        try:
            async with asyncio.timeout(task.budget.deadline_s):
                async for update in agent.astream(
                    {"messages": [("user", _render_task(task))]},
                    config={"recursion_limit": recursion_limit},
                    stream_mode="updates",
                ):
                    for node_payload in update.values():
                        for message in (node_payload or {}).get("messages", []):
                            if isinstance(message, AIMessage):
                                iterations += 1
                                pending_tool_calls = bool(message.tool_calls)
                                text = await _publish_ai_message(
                                    message,
                                    bus,
                                    usage,
                                    provider=provider,
                                    model_name=model_name,
                                )
                                if text.strip():
                                    last_ai_text = text.strip()
                    if iterations >= task.budget.max_iterations and pending_tool_calls:
                        budget_hit = True
                        await bus.publish(
                            "budget",
                            {"reason": "max_iterations", "iterations": iterations},
                        )
                        break
            answer = last_ai_text
            if budget_hit or (not answer and pending_tool_calls):
                status = "budget_exceeded"
                answer = answer or "iteration limit reached before a final answer"
            elif answer.startswith(NO_DATA_MARKER):
                status = "no_data"
                answer = answer[len(NO_DATA_MARKER) :].strip()
            elif answer:
                status = "succeeded"
            else:
                status = "failed"
                answer = "the agent produced no final answer"
        except TimeoutError:
            status = "budget_exceeded"
            answer = last_ai_text or (
                f"deadline of {task.budget.deadline_s}s exceeded before an answer"
            )
        except GraphRecursionError:
            status = "budget_exceeded"
            answer = last_ai_text or "iteration limit reached before a final answer"
    except Exception as exc:  # noqa: BLE001 — worker must return a result, not crash
        log.error("worker_failed", task_id=task.task_id, error=str(exc))
        status = "failed"
        answer = f"worker error: {exc}"
    finally:
        bus.unsubscribe(_collect)
        reset_run_context(context_tokens)
    return WorkerResult(
        task_id=task.task_id,
        status=status,  # type: ignore[arg-type]
        answer=answer,
        evidence=evidence,
        trace=[event.as_dict() for event in collected],
        usage=usage,
    )
