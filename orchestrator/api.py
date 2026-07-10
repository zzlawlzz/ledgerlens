"""Chat API v0 (T-014): question -> SSE stream of TraceEvents -> answer.

No Plan-and-Execute yet — a single-step "plan" delegates straight to the
ReAct worker through the WorkerClient interface (T-020/T-021 swap the
implementation without touching this API).

Try it:
    curl -N -X POST http://localhost:8000/api/chat \
         -H "Content-Type: application/json" \
         -d '{"question": "What was the revenue of AAPL in FY2024?"}'
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from common.config import get_settings, load_yaml_config
from common.db import get_session_factory
from common.logging import bind_run_context, configure_logging, get_logger, reset_run_context
from common.tracing import Subscriber, TraceBus, TraceEvent, make_log_subscriber
from orchestrator.graph import Orchestrator
from orchestrator.persistence import create_run, finalize_run, make_db_subscriber
from orchestrator.worker_client import LocalWorkerClient, WorkerClient

TRACE_BUS = TraceBus()
STREAM_QUEUE_MAX = 1000

_infrastructure_ready = False


def ensure_stream_infrastructure() -> None:
    """Attach log/DB subscribers once per process (idempotent; used by tests too)."""
    global _infrastructure_ready
    if _infrastructure_ready:
        return
    configure_logging("orchestrator")
    TRACE_BUS.subscribe(make_log_subscriber())
    TRACE_BUS.subscribe(make_db_subscriber())
    _infrastructure_ready = True


async def _warm_up_local_llm() -> None:
    """T-017: probe the local tier once at startup; slow answers get a warning.

    The router falls back to cloud by timeout anyway — this is an SLA signal
    for the operator, not a gate.
    """
    settings = get_settings()
    log = get_logger(node="orchestrator")
    if not settings.has_local_llm():
        log.info("local_llm_disabled", reason="no OLLAMA_BASE_URL/LOCAL_MODEL (no-local mode)")
        return
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings.local_model,
        api_key="ollama",  # type: ignore[arg-type]
        base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
        timeout=120,
        temperature=0.0,
    )
    started = time.perf_counter()
    try:
        await model.ainvoke([("user", "Reply with exactly: OK")])
    except Exception as exc:  # noqa: BLE001 — warm-up must never break startup
        log.warning("local_llm_unreachable", model=settings.local_model, error=str(exc)[:200])
        return
    elapsed_s = round(time.perf_counter() - started, 1)
    log.info("local_llm_warmup_done", model=settings.local_model, seconds=elapsed_s)
    if elapsed_s > 10:
        log.warning(
            "local_llm_slow", seconds=elapsed_s, sla_hint="short classify should take <=10s"
        )


_CHECKPOINTER: object | None = None


async def _init_checkpointer() -> None:
    """Postgres checkpointer for session follow-ups (T-020).

    Unavailability degrades gracefully: runs work, sessions just don't
    remember previous turns.
    """
    global _CHECKPOINTER
    from urllib.parse import quote_plus

    settings = get_settings()
    conn = (
        f"postgresql://{quote_plus(settings.postgres_user)}:"
        f"{quote_plus(settings.postgres_password)}@{settings.postgres_host}:"
        f"{settings.postgres_port}/{settings.postgres_db}"
    )
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool: AsyncConnectionPool = AsyncConnectionPool(
            conn,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open()
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()
        _CHECKPOINTER = saver
        get_logger(node="orchestrator").info("checkpointer_ready")
    except Exception as exc:  # noqa: BLE001 — checkpointer is optional
        get_logger(node="orchestrator").warning(
            "checkpointer_unavailable", error=str(exc)[:200], effect="no session memory"
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_stream_infrastructure()
    await _init_checkpointer()
    asyncio.create_task(_warm_up_local_llm())
    yield


app = FastAPI(title="LedgerLens API", lifespan=_lifespan)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str = "us"
    session_id: str | None = Field(default=None, max_length=128)


def _build_orchestrator(checkpointer: object | None) -> Orchestrator:
    """Worker registry from config/workers.yaml; url 'local' = in-process."""
    registry = load_yaml_config("workers")["workers"]
    clients: dict[str, WorkerClient] = {}
    skills: dict[str, list[str]] = {}
    for entry in registry:
        name = str(entry["name"])
        if str(entry["url"]) == "local":
            clients[name] = LocalWorkerClient(trace_bus=TRACE_BUS)
        else:  # pragma: no cover — A2A clients arrive in T-021
            continue
        skills[name] = [str(s) for s in entry.get("skills", [])]
    return Orchestrator(
        worker_clients=clients,
        worker_skills=skills,
        trace_bus=TRACE_BUS,
        checkpointer=checkpointer,
    )


_ORCHESTRATOR: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = _build_orchestrator(_CHECKPOINTER)
    return _ORCHESTRATOR


def _run_status(state: dict[str, object]) -> str:
    if not state.get("partial"):
        return "succeeded"
    reason = str(state.get("partial_reason", ""))
    budget_markers = ("wall clock", "cost $", "tokens ", "executed steps")
    if any(marker in reason for marker in budget_markers):
        return "budget_exceeded"
    return "succeeded"  # partial after failed steps is still an honest answer


async def _execute_run(run_id: uuid.UUID, request: ChatRequest) -> None:
    """The actual run: orchestrator graph + DB finalization. Independent of the
    SSE relay — a client disconnect never leaves the run in 'running' forever."""
    started = time.perf_counter()
    context_tokens = bind_run_context(run_id=str(run_id), node="orchestrator")
    final_state: dict[str, object] | None = None
    error: str | None = None
    try:
        await TRACE_BUS.publish("run_started", {"question": request.question, "mode": request.mode})
        final_state = dict(
            await get_orchestrator().run(
                question=request.question,
                mode=request.mode,
                run_id=str(run_id),
                session_id=request.session_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 — a run must always be finalized
        error = str(exc)
        get_logger(node="orchestrator").error("run_crashed", error=error)
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        status = _run_status(final_state) if final_state is not None else "failed"
        answer = str(final_state.get("answer", "")) if final_state is not None else None
        usage = {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}
        try:
            usage = await finalize_run(
                run_id, status=status, answer=answer, error=error, latency_ms=latency_ms
            )
        except Exception as exc:  # noqa: BLE001
            get_logger(node="orchestrator").error("finalize_failed", error=str(exc))
        if error is not None or final_state is None:
            await TRACE_BUS.publish("run_error", {"error": error or "no state"}, run_id=str(run_id))
        else:
            await TRACE_BUS.publish(
                "run_finished",
                {
                    "status": status,
                    "answer": answer,
                    "key_values": _public_key_values(final_state),
                    "citations": final_state.get("citations", []),
                    "partial": bool(final_state.get("partial")),
                    "usage": usage,
                    "latency_ms": latency_ms,
                },
                run_id=str(run_id),
            )
        reset_run_context(context_tokens)


def _public_key_values(state: dict[str, object]) -> dict[str, object]:
    key_values = state.get("key_values", {})
    if not isinstance(key_values, dict):
        return {}
    return {k: v for k, v in key_values.items() if not str(k).startswith("__first__")}


def _subscribe_run_events(run_id: str) -> tuple[asyncio.Queue[TraceEvent], Subscriber]:
    """Subscribe BEFORE the run starts so no early event is lost."""
    queue: asyncio.Queue[TraceEvent] = asyncio.Queue(maxsize=STREAM_QUEUE_MAX)

    async def _enqueue(event: TraceEvent) -> None:
        if event.run_id == run_id:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow client: drop intermediate events; terminal ones still fit

    TRACE_BUS.subscribe(_enqueue)
    return queue, _enqueue


async def _drain_events(
    queue: asyncio.Queue[TraceEvent], subscriber: Subscriber
) -> AsyncIterator[str]:
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False, default=str)}\n\n"
            if event.event in ("run_finished", "run_error"):
                return
    finally:
        TRACE_BUS.unsubscribe(subscriber)


@app.post("/api/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    ensure_stream_infrastructure()
    run_id = await create_run(request.question, request.mode)
    queue, subscriber = _subscribe_run_events(str(run_id))
    asyncio.create_task(_execute_run(run_id, request))
    return StreamingResponse(
        _drain_events(queue, subscriber),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Run-Id": str(run_id)},
    )


@app.get("/healthz")
async def healthz() -> JSONResponse:
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — health endpoint reports, never raises
        return JSONResponse({"status": "unhealthy", "db": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "mode": get_settings().app_mode})
