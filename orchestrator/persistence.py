"""Run persistence (T-014): runs/steps rows + TraceBus -> llm_calls/tool_calls.

The API creates a run row up front, a bus subscriber mirrors LLM and tool
events into their tables (LLM cost is priced from config/prices.yaml), and
``finalize_run`` closes the run whatever happened — including client
disconnects (the runner keeps working in the background).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from common.config import load_yaml_config
from common.db import get_session_factory
from common.logging import get_logger
from common.tracing import Subscriber, TraceEvent

_PRICES_CACHE: dict[str, dict[str, float]] | None = None


def _price_table() -> dict[str, dict[str, float]]:
    global _PRICES_CACHE
    if _PRICES_CACHE is None:
        raw = load_yaml_config("prices").get("models", {})
        _PRICES_CACHE = {
            model: {key: float(value) for key, value in spec.items()} for model, spec in raw.items()
        }
    return _PRICES_CACHE


def llm_call_cost(model: str, tokens_in: int, tokens_out: int) -> Decimal | None:
    """USD cost per CONTRACTS §11; None (with warning) for unknown models."""
    spec = _price_table().get(model)
    if spec is None:
        get_logger(node="persistence").warning("unknown_model_price", model=model)
        return None
    cost = tokens_in / 1e6 * spec["in_per_1m"] + tokens_out / 1e6 * spec["out_per_1m"]
    return Decimal(str(round(cost, 6)))


async def create_run(question: str, mode: str) -> uuid.UUID:
    """Insert the runs row; plan steps add their own rows via insert_step."""
    run_id = uuid.uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO runs (id, question, mode, status) "
                "VALUES (:id, :question, :mode, 'running')"
            ),
            {"id": run_id, "question": question, "mode": mode},
        )
        await session.commit()
    return run_id


async def record_demo_rejection(kind: str, status_code: int) -> None:
    """Persist one public-demo admission rejection for observability (T-036 §1).

    Stores only the limit ``kind`` and HTTP status — never the IP or question.
    Callers treat a failure here as non-fatal: the refusal response to the user
    matters more than its bookkeeping.
    """
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("INSERT INTO demo_rejections (kind, status_code) VALUES (:kind, :status_code)"),
            {"kind": kind, "status_code": status_code},
        )
        await session.commit()


async def insert_step(run_id: uuid.UUID, *, node: str, goal: str) -> uuid.UUID:
    """One executed plan step = one steps row (T-020)."""
    step_id = uuid.uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO steps (id, run_id, node, status, goal) "
                "VALUES (:id, :run_id, :node, 'running', :goal)"
            ),
            {"id": step_id, "run_id": run_id, "node": node, "goal": goal},
        )
        await session.commit()
    return step_id


async def finish_step(
    step_id: uuid.UUID,
    *,
    status: str,
    output: dict[str, Any],
    error: str | None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE steps SET status = :status, output = CAST(:output AS jsonb), "
                "error = :error, finished_at = now() WHERE id = :id"
            ),
            {
                "id": step_id,
                "status": status,
                "output": json.dumps(output, ensure_ascii=False, default=str),
                "error": error,
            },
        )
        await session.commit()


async def finalize_run(
    run_id: uuid.UUID,
    *,
    status: str,
    answer: str | None,
    error: str | None,
    latency_ms: int,
) -> dict[str, Any]:
    """Close the run; usage is aggregated from llm_calls (the source of truth).

    Any step still 'running' (crash mid-plan) is closed as failed so the run
    never leaves dangling rows.
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "UPDATE runs SET status = :status, answer = :answer, error = :error, "
                    "tokens_in = COALESCE((SELECT SUM(tokens_in) FROM llm_calls "
                    "                      WHERE run_id = :id), 0), "
                    "tokens_out = COALESCE((SELECT SUM(tokens_out) FROM llm_calls "
                    "                       WHERE run_id = :id), 0), "
                    "cost_usd = COALESCE((SELECT SUM(cost_usd) FROM llm_calls "
                    "                     WHERE run_id = :id), 0), "
                    "latency_ms = :latency_ms, finished_at = now() WHERE id = :id "
                    "RETURNING cost_usd, tokens_in, tokens_out"
                ),
                {
                    "id": run_id,
                    "status": status,
                    "answer": answer,
                    "error": error,
                    "latency_ms": latency_ms,
                },
            )
        ).one()
        await session.execute(
            text(
                "UPDATE steps SET status = 'failed', error = 'run finalized while running', "
                "finished_at = now() WHERE run_id = :id AND status = 'running'"
            ),
            {"id": run_id},
        )
        await session.commit()
    return {
        "cost_usd": float(row[0]),
        "tokens_in": int(row[1]),
        "tokens_out": int(row[2]),
    }


def make_db_subscriber() -> Subscriber:
    """TraceBus subscriber: llm_call / tool_call_finished -> their tables."""
    pending_tool_started: dict[tuple[str, str], float] = {}

    async def _persist(event: TraceEvent) -> None:
        if event.event == "tool_call_started":
            key = (event.run_id, str(event.payload.get("tool")))
            pending_tool_started[key] = event.ts
            return
        if event.event == "tool_call_finished":
            key = (event.run_id, str(event.payload.get("tool")))
            started_ts = pending_tool_started.pop(key, None)
            payload_latency = event.payload.get("latency_ms")
            if payload_latency is not None:
                latency_ms: int | None = int(payload_latency)
            else:
                latency_ms = int((event.ts - started_ts) * 1000) if started_ts else None
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO tool_calls (run_id, step_id, tool, arguments, "
                        "status, latency_ms, result_preview) VALUES "
                        "(:run_id, :step_id, :tool, CAST(:arguments AS jsonb), "
                        ":status, :latency_ms, :preview)"
                    ),
                    {
                        "run_id": uuid.UUID(event.run_id),
                        "step_id": _as_uuid(event.step_id),
                        "tool": event.payload.get("tool"),
                        "arguments": json.dumps(event.payload.get("arguments", {})),
                        "status": event.payload.get("status", "ok"),
                        "latency_ms": latency_ms,
                        "preview": str(event.payload.get("preview", ""))[:500],
                    },
                )
                await session.commit()
            return
        if event.event == "llm_call":
            payload: dict[str, Any] = event.payload
            tokens_in = int(payload.get("tokens_in", 0))
            tokens_out = int(payload.get("tokens_out", 0))
            model = str(payload.get("model", "unknown"))
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO llm_calls (run_id, step_id, task_class, provider, "
                        "model, tokens_in, tokens_out, cost_usd, latency_ms, "
                        "fallback_used, error) VALUES "
                        "(:run_id, :step_id, :task_class, :provider, :model, "
                        ":tokens_in, :tokens_out, :cost_usd, :latency_ms, "
                        ":fallback_used, :error)"
                    ),
                    {
                        "run_id": uuid.UUID(event.run_id),
                        "step_id": _as_uuid(event.step_id),
                        "task_class": payload.get("task_class", "reason"),
                        "provider": payload.get("provider", "unknown"),
                        "model": model,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "cost_usd": llm_call_cost(model, tokens_in, tokens_out),
                        "latency_ms": payload.get("latency_ms"),
                        "fallback_used": bool(payload.get("fallback_used", False)),
                        "error": payload.get("error"),
                    },
                )
                await session.commit()

    return _persist


def _as_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None
