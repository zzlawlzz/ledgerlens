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

from ag_ui.core import RunAgentInput
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text

from common.config import get_settings, load_yaml_config
from common.db import get_session_factory
from common.logging import bind_run_context, configure_logging, get_logger, reset_run_context
from common.tracing import Subscriber, TraceBus, TraceEvent, make_log_subscriber
from orchestrator.agui import SSE_HEARTBEAT, extract_question, stream_agui_run
from orchestrator.demo_limits import DemoLimiter, DemoLimitError, build_demo_limiter
from orchestrator.graph import Orchestrator
from orchestrator.monitor_api import monitor_router
from orchestrator.persistence import (
    create_run,
    finalize_run,
    make_db_subscriber,
    record_demo_rejection,
)
from orchestrator.worker_client import (
    A2AWorkerClient,
    LocalWorkerClient,
    WorkerClient,
    assert_worker_url_secure,
)

TRACE_BUS = TraceBus()
STREAM_QUEUE_MAX = 1000
# Emit an SSE keep-alive if the orchestrator produces no event for this long, so
# a stream that is idle during a slow step survives proxy idle timeouts. Must
# stay ≤ 15 s (T-036 §4; Cloudflare Tunnel drops idle connections ~100 s).
SSE_HEARTBEAT_INTERVAL_S = 10.0

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


# Public demo (T-036 §3) is a hardened surface: the interactive API schema and
# Swagger/ReDoc explorers are debug affordances, so they are switched off when
# BUDGET_PROFILE=demo. Off the demo profile (dev) they stay on for convenience.
_DEMO = get_settings().budget_profile == "demo"
app = FastAPI(
    title="LedgerLens API",
    lifespan=_lifespan,
    docs_url=None if _DEMO else "/docs",
    redoc_url=None if _DEMO else "/redoc",
    openapi_url=None if _DEMO else "/openapi.json",
)
app.include_router(monitor_router)  # T-035 monitoring layer B (/api/monitor/*)


_DEMO_LIMITER: DemoLimiter | None = None


def get_demo_limiter() -> DemoLimiter:
    """Public-demo admission limiter (T-036 §1). No-op off the demo profile."""
    global _DEMO_LIMITER
    if _DEMO_LIMITER is None:
        _DEMO_LIMITER = build_demo_limiter(get_settings())
    return _DEMO_LIMITER


@app.exception_handler(DemoLimitError)
async def _demo_limit_handler(_: Request, exc: DemoLimitError) -> JSONResponse:
    """Render a rejected demo request as a polite, human-readable refusal and
    record it for the operations dashboard (T-036 §1). The counter is best
    effort: a bookkeeping failure must never change what the caller sees."""
    try:
        await record_demo_rejection(exc.kind, exc.status_code)
    except Exception as bookkeeping_error:  # pragma: no cover - defensive
        get_logger(node="orchestrator").warning(
            "demo_rejection_record_failed", kind=exc.kind, error=str(bookkeeping_error)
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _client_ip(request: Request) -> str:
    """Caller IP for the demo rate limit. This is a security control, so the
    address must not be attacker-controllable.

    Cloudflare sets ``CF-Connecting-IP`` to the true client address and overwrites
    any client-supplied value; the demo's only ingress is the outbound cloudflared
    tunnel, so this header is authoritative and cannot be spoofed. We deliberately
    do NOT trust ``X-Forwarded-For``: nginx in front does not rewrite it
    (no ``$proxy_add_x_forwarded_for``), so a client-supplied XFF passes through
    untouched — every position in it is caller-controlled. Trusting it would let a
    visitor mint a fresh rate-limit bucket per request by rotating the header and
    defeat the whole budget-exhaustion protection (T-036 §1). Off Cloudflare (dev),
    fall back to the direct TCP peer."""
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    return request.client.host if request.client else "unknown"


def _admit(question: str, request: Request) -> None:
    """Run all pre-run demo checks and take a concurrency slot, or raise
    DemoLimitError. The slot is released in ``_execute_run``'s finally block.

    Ordering matters: the concurrency slot is taken *before* the rate hit is
    recorded so a request bounced with "demo is busy" (429 concurrency) does not
    burn the caller's hourly quota — otherwise the recommended retry could lock a
    real user out for an hour without ever getting a single run. If the rate
    check then rejects, the just-taken slot is handed back (no leak)."""
    limiter = get_demo_limiter()
    limiter.check_question(question)
    limiter.check_daily_cost()
    limiter.acquire_slot()
    try:
        limiter.check_rate(_client_ip(request))
    except BaseException:
        limiter.release_slot()
        raise


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str = "us"
    session_id: str | None = Field(default=None, max_length=128)


def _build_orchestrator(checkpointer: object | None) -> Orchestrator:
    """Worker registry from config/workers.yaml; url 'local' = in-process,
    anything else is an A2A endpoint (T-021)."""
    registry = load_yaml_config("workers")["workers"]
    clients: dict[str, WorkerClient] = {}
    skills: dict[str, list[str]] = {}
    for entry in registry:
        name = str(entry["name"])
        url = str(entry["url"])
        assert_worker_url_secure(name, url)  # T-031: TLS/WG required off-localhost
        if url == "local":
            clients[name] = LocalWorkerClient(trace_bus=TRACE_BUS)
        else:
            clients[name] = A2AWorkerClient(url, get_settings().a2a_token)
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
        # Demo accounting: return the concurrency slot and charge the daily cap
        # (no-op off the demo profile). Runs after finalize so cost is known.
        limiter = get_demo_limiter()
        limiter.release_slot()
        limiter.record_cost(float(usage.get("cost_usd", 0.0) or 0.0))
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


def _sse_headers(run_id: object) -> dict[str, str]:
    """Response headers for an SSE stream.

    ``X-Accel-Buffering: no`` disables response buffering at the app boundary
    itself (honoured by nginx and other reverse proxies), so streaming works
    even without the location-level nginx tuning — portable proxy-independent
    buffering-off required by T-036 §4.
    """
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Run-Id": str(run_id),
    }


async def _iter_with_heartbeat(
    queue: asyncio.Queue[TraceEvent],
) -> AsyncIterator[TraceEvent | None]:
    """Yield queued trace events; yield ``None`` as a heartbeat tick when the
    orchestrator has been silent for ``SSE_HEARTBEAT_INTERVAL_S`` (T-036 §4).

    ``wait_for`` cancels the pending ``get`` cleanly on timeout, so no event is
    consumed or lost — the next iteration re-awaits the same queue.
    """
    while True:
        try:
            yield await asyncio.wait_for(queue.get(), SSE_HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            yield None


async def _drain_events(
    queue: asyncio.Queue[TraceEvent], subscriber: Subscriber
) -> AsyncIterator[str]:
    try:
        async for event in _iter_with_heartbeat(queue):
            if event is None:
                yield SSE_HEARTBEAT
                continue
            yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False, default=str)}\n\n"
            if event.event in ("run_finished", "run_error"):
                return
    finally:
        TRACE_BUS.unsubscribe(subscriber)


@app.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request) -> StreamingResponse:
    ensure_stream_infrastructure()
    _admit(request.question, http_request)  # T-036 §1: demo limits (no-op in dev)
    # The concurrency slot taken in _admit is owned by _execute_run's finally
    # block, so it is only safe to stop guarding it once that task is scheduled.
    # Cover the whole setup: any failure before create_task must release the slot,
    # otherwise the slot leaks permanently (wedging the demo at "busy" 429).
    try:
        run_id = await create_run(request.question, request.mode)
        queue, subscriber = _subscribe_run_events(str(run_id))
        asyncio.create_task(_execute_run(run_id, request))
    except BaseException:
        get_demo_limiter().release_slot()
        raise
    return StreamingResponse(
        _drain_events(queue, subscriber),
        media_type="text/event-stream",
        headers=_sse_headers(run_id),
    )


@app.post("/agui")
async def agui(body: RunAgentInput, http_request: Request) -> StreamingResponse:
    """AG-UI protocol endpoint (T-023): RunAgentInput -> AG-UI SSE stream.

    thread_id maps to the checkpointer session, so follow-up questions in the
    same thread keep their dialogue context.
    """
    ensure_stream_infrastructure()
    # Validate the whole request BEFORE taking a demo slot or creating a run.
    # AG-UI thread_id is an unbounded string, but session_id is capped at 128:
    # building ChatRequest here turns an overlong thread_id into a clean 422
    # instead of a ValidationError raised *after* a slot/run was allocated —
    # which would leak the concurrency slot and orphan the run in 'running'.
    try:
        question = extract_question(body)
        request = ChatRequest(question=question, session_id=body.thread_id)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _admit(question, http_request)  # T-036 §1: demo limits (no-op in dev)
    # Guard the whole setup: any failure before create_task must release the
    # slot owned thereafter by _execute_run's finally block (see /api/chat).
    try:
        run_id = await create_run(question, get_settings().app_mode)
        queue, subscriber = _subscribe_run_events(str(run_id))
        asyncio.create_task(_execute_run(run_id, request))
    except BaseException:
        get_demo_limiter().release_slot()
        raise

    async def _stream() -> AsyncIterator[str]:
        try:
            async for chunk in stream_agui_run(body, _iter_with_heartbeat(queue)):
                yield chunk
        finally:
            TRACE_BUS.unsubscribe(subscriber)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers=_sse_headers(run_id),
    )


@app.get("/api/examples")
async def examples() -> JSONResponse:
    """Example questions for the UI buttons (T-024), both locales; the RU
    mode serves its own set (T-032)."""
    app_config = load_yaml_config("app")
    mode = get_settings().app_mode
    key = "examples_ru" if mode == "ru" else "examples"
    # `demo` lets the UI show the public-demo banner (T-036 §5) only when this
    # instance runs under BUDGET_PROFILE=demo; off the demo profile it stays hidden.
    return JSONResponse({"examples": app_config.get(key, []), "mode": mode, "demo": _DEMO})


@app.get("/healthz")
async def healthz() -> JSONResponse:
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — health endpoint reports, never raises
        return JSONResponse({"status": "unhealthy", "db": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "mode": get_settings().app_mode})
