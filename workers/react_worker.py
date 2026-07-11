"""ReAct worker agent (T-013): LangGraph loop over SQL tools.

Contract: ``WorkerTask`` in — ``WorkerResult`` out (CONTRACTS.md §10).
Budget: ``max_iterations`` maps to the graph recursion limit, ``deadline_s``
to an asyncio timeout; both overruns end as status ``budget_exceeded`` with
a partial answer, never as an exception. Every step publishes TraceEvents
(agent_thought / tool_call_* / llm_call) to the provided TraceBus.

LLM calls go through the Model Router (T-016): the default model is
``RouterChatModel('reason')`` — tiered routing with fallback; the router
publishes ``llm_call`` events itself.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from common.agents import WorkerEvidence, WorkerResult, WorkerTask, WorkerUsage
from common.config import load_yaml_config
from common.logging import bind_run_context, get_logger, reset_run_context
from common.tracing import TraceBus, TraceEvent
from model_router.router import RouterChatModel, RouterClient
from tools.sql.core import schema_introspect, sql_query

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "worker_react.md"
GROUND_CHECK_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "worker_ground_check.md"
)
THOUGHT_PREVIEW_CHARS = 300
PREVIEW_CHARS = 500
NO_DATA_MARKER = "NO_DATA:"
DEFAULT_RAG_TOP_K = 8  # mirrors tools.rag.core.DEFAULT_TOP_K (T-041)
GROUND_CHECK_CONTEXT_MAX_CHARS = 8000

ToolImpl = Callable[..., Awaitable[dict[str, Any]]]


def _prompt_body(path: Path) -> str:
    """Prompt body without the version header (id/task_class/version)."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        rest = text.partition("---")[2]
        rest = rest.partition("---")[2]
        return rest.strip()
    return text.strip()


def load_worker_prompt() -> str:
    return _prompt_body(PROMPT_PATH)


def load_ground_check_prompt() -> str:
    return _prompt_body(GROUND_CHECK_PROMPT_PATH)


_MCP_SERVER_TOOLS = {
    "sql": ("sql_query", "schema_introspect"),
    "rag": ("rag_search",),
    "enrich": ("price_enrich",),
}


def _tool_endpoints() -> dict[str, str]:
    """config/workers.yaml -> tool_endpoints; 'local' means lib mode (T-027)."""
    try:
        raw = load_yaml_config("workers").get("tool_endpoints", {})
    except Exception:  # noqa: BLE001 — config problems must not kill the worker
        return {}
    return {str(k): str(v) for k, v in raw.items()}


async def _load_mcp_impls(endpoints: dict[str, str], run_id: str) -> dict[str, ToolImpl]:
    """MCP-backed tool impls (T-027); a dead server degrades to observation
    errors on its tools instead of crashing the worker."""
    import json as json_module

    log = get_logger(node="worker")
    impls: dict[str, ToolImpl] = {}
    for server_name, url in endpoints.items():
        if not url or url == "local":
            continue
        tool_names = _MCP_SERVER_TOOLS.get(server_name, ())
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            client = MultiServerMCPClient(
                {
                    server_name: {
                        "transport": "streamable_http",
                        "url": url,
                        "headers": {"X-Run-Id": run_id},
                    }
                }
            )
            tools = await client.get_tools()
        except Exception as exc:  # noqa: BLE001 — server down: observation errors
            log.warning("mcp_server_unavailable", server=server_name, error=str(exc)[:200])
            for name in tool_names:

                async def _down(_error: str = str(exc)[:200], **_: Any) -> dict[str, Any]:
                    return {
                        "error": f"tool service unavailable: {_error}",
                        "retryable": True,
                    }

                impls[name] = _down
            continue
        for tool in tools:

            async def _impl(_tool: Any = tool, **kwargs: Any) -> dict[str, Any]:
                raw = await _tool.ainvoke(kwargs)
                if isinstance(raw, dict):
                    return raw
                if isinstance(raw, list):  # adapters may return content blocks
                    raw = "".join(
                        str(part.get("text", ""))
                        for part in raw
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                try:
                    parsed = json_module.loads(raw)
                except (TypeError, ValueError):
                    return {"error": f"malformed MCP result: {str(raw)[:200]}"}
                return parsed if isinstance(parsed, dict) else {"result": parsed}

            impls[str(tool.name)] = _impl
        log.info("mcp_tools_loaded", server=server_name, tools=[t.name for t in tools])
    return impls


def _default_tool_impls() -> dict[str, ToolImpl]:
    async def _schema_introspect() -> dict[str, Any]:
        return await schema_introspect()

    async def _rag_search(
        query: str, top_k: int = DEFAULT_RAG_TOP_K, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        from tools.rag.core import rag_search

        return await rag_search(query, top_k, filters)

    async def _price_enrich(ticker: str, date_from: str, date_to: str) -> dict[str, Any]:
        from tools.enrich.core import price_enrich

        return await price_enrich(ticker, date_from, date_to)

    return {
        "sql_query": sql_query,
        "schema_introspect": _schema_introspect,
        "rag_search": _rag_search,
        "price_enrich": _price_enrich,
    }


def _build_tools(
    task: WorkerTask,
    impls: dict[str, ToolImpl],
    bus: TraceBus,
    usage: WorkerUsage,
    evidence: WorkerEvidence,
    raw_chunks: list[str],
) -> list[StructuredTool]:
    tools: list[StructuredTool] = []

    async def _traced_call(tool_name: str, arguments: dict[str, Any]) -> str:
        import time

        usage.tool_calls += 1
        await bus.publish("tool_call_started", {"tool": tool_name, "arguments": arguments})
        started = time.perf_counter()
        result = await impls[tool_name](**arguments)
        latency_ms = int((time.perf_counter() - started) * 1000)
        is_error = isinstance(result, dict) and "error" in result
        payload = json.dumps(result, ensure_ascii=False, default=str)
        await bus.publish(
            "tool_call_finished",
            {
                "tool": tool_name,
                "status": "error" if is_error else "ok",
                # Measured at the call site: survives trace relay over A2A,
                # includes the MCP transport (T-027).
                "latency_ms": latency_ms,
                "preview": payload[:PREVIEW_CHARS],
            },
        )
        if tool_name == "sql_query" and not is_error:
            evidence.facts.append(
                {"sql": arguments.get("sql", ""), "row_count": result.get("row_count")}
            )
        if tool_name == "price_enrich" and not is_error and isinstance(result, dict):
            evidence.facts.append(
                {
                    "ticker": arguments.get("ticker"),
                    "price_points": len(result.get("series", [])),
                    "source": result.get("source"),
                    "cached": result.get("cached"),
                }
            )
        if tool_name == "rag_search" and not is_error and isinstance(result, dict):
            for chunk in result.get("chunks", []):
                citation = chunk.get("citation")
                if citation:
                    evidence.citations.append(dict(citation))
                    tag = (
                        f"[{citation.get('ticker')} {citation.get('form_type')} "
                        f"{citation.get('period')}, {citation.get('section')}]"
                    )
                    raw_chunks.append(f"{tag}\n{chunk.get('text', '')}")
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

    if "rag_search" in task.allowed_tools and "rag_search" in impls:

        async def _rag(
            query: str, top_k: int = DEFAULT_RAG_TOP_K, filters: dict[str, Any] | None = None
        ) -> str:
            """Search narrative filing sections (risk factors, MD&A) with citations."""
            return await _traced_call(
                "rag_search", {"query": query, "top_k": top_k, "filters": filters or {}}
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=_rag,
                name="rag_search",
                description=(
                    "Hybrid search over narrative 10-K sections (risk_factors, mdna). "
                    "Returns text chunks with citations. Optional filters: tickers, "
                    "form_types, sections, period_from/period_to (ISO dates). "
                    "If no_results is true, honestly say the data is not loaded."
                ),
            )
        )

    if "price_enrich" in task.allowed_tools and "price_enrich" in impls:

        async def _price_enrich(ticker: str, date_from: str, date_to: str) -> str:
            """Fetch end-of-day close prices for one ticker (cache, then Stooq)."""
            return await _traced_call(
                "price_enrich",
                {"ticker": ticker, "date_from": date_from, "date_to": date_to},
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=_price_enrich,
                name="price_enrich",
                description=(
                    "End-of-day close prices for one ticker between two ISO dates "
                    "(YYYY-MM-DD). Returns {series: [{date, close, currency}], "
                    "source, cached}. Use only as context for price dynamics — "
                    "never for forecasts or buy/sell recommendations. On error, "
                    "continue the analysis without price data."
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


_CITATION_MARKER = re.compile(r"\[[A-Z]{1,6}\s+10-[KQ][^\]]{0,80}\]")
_GAP_PHRASES = re.compile(
    r"(?i)\b(no data|not (available|loaded|present|found|disclosed|in the)|"
    r"нет данных|не (загружен|найден|раскрыт))"
)
_STRUCTURAL_MAX_CHARS = 90


def _strip_ungrounded(answer: str) -> str:
    """Deterministic grounding guard (T-041): in a cited narrative answer,
    a factual line without a citation marker is dropped.

    The LLM trim pass (_ground_check) is probabilistic and demonstrably
    inconsistent (faithfulness 0.0 vs 0.69 between runs on the same cases);
    this pass is the structural backstop. Kept lines: cited ones, honest
    data-gap statements, blank lines, and short digit-free structural lines
    (headings, intros). Fail-open: an answer with no markers at all is
    returned untouched, and an over-aggressive cut falls back to the input.
    """
    if not _CITATION_MARKER.search(answer):
        return answer
    kept: list[str] = []
    for line in answer.splitlines():
        stripped = line.strip().lstrip("-*•").strip()
        if (
            not stripped
            or _CITATION_MARKER.search(line)
            or _GAP_PHRASES.search(stripped)
            or (not re.search(r"\d", stripped) and len(stripped) <= _STRUCTURAL_MAX_CHARS)
        ):
            kept.append(line)
    result = "\n".join(kept).strip()
    return result if result else answer


async def _ground_check(
    router: RouterClient, *, question: str, draft: str, raw_chunks: list[str]
) -> str:
    """Post-answer grounding rewrite (T-041): the ReAct loop's draft answer
    tends to pad narrative lists with facts the model "knows" but that were
    never actually retrieved, despite the prompt forbidding it — a second
    pass with the SAME raw chunks (not the truncated digest orchestrator
    synthesize sees) catches what the drafting pass missed. Degrades to the
    draft, unchanged, on any failure — never blocks the worker's answer."""
    context = "\n---\n".join(raw_chunks)[:GROUND_CHECK_CONTEXT_MAX_CHARS]
    try:
        response = await router.chat(
            "ground_check",
            [
                ("system", load_ground_check_prompt()),
                (
                    "user",
                    f"QUESTION:\n{question}\n\nRETRIEVED_CONTEXT:\n{context}\n\n"
                    f"DRAFT_ANSWER:\n{draft}",
                ),
            ],
        )
        text = response.text.strip()
        return text or draft
    except Exception:  # noqa: BLE001 — grounding is a quality pass, not a gate
        return draft


async def _publish_ai_message(message: AIMessage, bus: TraceBus, usage: WorkerUsage) -> str:
    """Publish the agent thought and accumulate usage; returns the message text.

    ``llm_call`` events are the router's responsibility (T-016) — the worker
    only mirrors the reasoning into the trace.
    """
    content = message.content if isinstance(message.content, str) else str(message.content)
    if content.strip():
        await bus.publish("agent_thought", {"text": content.strip()[:THOUGHT_PREVIEW_CHARS]})
    if message.usage_metadata:
        usage.tokens_in += int(message.usage_metadata.get("input_tokens", 0))
        usage.tokens_out += int(message.usage_metadata.get("output_tokens", 0))
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
        router_client: RouterClient | None = None
        chat_model: BaseChatModel
        if model is not None:
            chat_model = model
        else:
            router_client = RouterClient(trace_bus=bus)
            chat_model = RouterChatModel(task_class="reason", router=router_client)
        mcp_impls = (
            await _load_mcp_impls(_tool_endpoints(), task.run_id) if tool_impls is None else {}
        )
        impls = {**_default_tool_impls(), **mcp_impls, **(tool_impls or {})}
        raw_chunks: list[str] = []
        tools = _build_tools(task, impls, bus, usage, evidence, raw_chunks)
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
                                text = await _publish_ai_message(message, bus, usage)
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
            if status == "succeeded" and raw_chunks and router_client is not None:
                answer = await _ground_check(
                    router_client, question=task.goal, draft=answer, raw_chunks=raw_chunks
                )
                if not evidence.facts:
                    # Pure narrative answer: enforce grounding structurally on
                    # top of the LLM trim (T-041 candidate 2). SQL-derived
                    # answers are excluded — their figures carry no citation
                    # markers and must not be cut.
                    answer = _strip_ungrounded(answer)
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
