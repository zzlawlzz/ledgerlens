"""TraceEvent bus — the internal event spine (CONTRACTS.md §10).

Every agent/tool/LLM step publishes a TraceEvent; subscribers fan events out
to logs (here), SSE and the runs/steps tables (T-014), and AG-UI (T-023).
Subscriber failures are logged and never break the publishing side.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from common.logging import current_context, get_logger

TraceEventName = Literal[
    "run_started",
    "plan_created",
    "plan_updated",
    "step_started",
    "agent_thought",
    "tool_call_started",
    "tool_call_finished",
    "llm_call",
    "step_finished",
    "guardrail",
    "answer_delta",
    "budget",
    "run_finished",
    "run_error",
]

_RUN_TERMINAL_EVENTS: tuple[TraceEventName, ...] = ("run_finished", "run_error")

Subscriber = Callable[["TraceEvent"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One event on the trace spine; ``seq`` is monotonic within a run."""

    ts: float
    run_id: str
    seq: int
    event: TraceEventName
    payload: dict[str, Any] = field(default_factory=dict)
    step_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "seq": self.seq,
            "event": self.event,
            "payload": self.payload,
        }


class TraceBus:
    """Async pub/sub with per-run sequence numbers.

    Events published sequentially (the normal case: a run's steps are ordered)
    are delivered to every subscriber in seq order. A subscriber exception is
    logged and does not affect other subscribers or the publisher.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._seq_by_run: dict[str, int] = {}
        self._seq_lock = asyncio.Lock()

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.remove(subscriber)

    async def publish(
        self,
        event: TraceEventName,
        payload: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> TraceEvent:
        """Publish an event; run_id/step_id default to the bound context."""
        context = current_context()
        effective_run_id = run_id or context.get("run_id")
        if not effective_run_id:
            raise ValueError(
                "TraceBus.publish requires run_id: pass it explicitly or bind it "
                "via common.logging.bind_run_context()"
            )
        effective_step_id = step_id or context.get("step_id")

        async with self._seq_lock:
            seq = self._seq_by_run.get(effective_run_id, 0) + 1
            self._seq_by_run[effective_run_id] = seq

        trace_event = TraceEvent(
            ts=time.time(),
            run_id=effective_run_id,
            step_id=effective_step_id,
            seq=seq,
            event=event,
            payload=payload or {},
        )
        for subscriber in list(self._subscribers):
            try:
                await subscriber(trace_event)
            except Exception:
                self._report_subscriber_failure(subscriber, trace_event)
        if event in _RUN_TERMINAL_EVENTS:
            self._seq_by_run.pop(effective_run_id, None)
        return trace_event

    @staticmethod
    def _report_subscriber_failure(subscriber: Subscriber, trace_event: TraceEvent) -> None:
        try:
            get_logger(node="trace_bus").exception(
                "trace_subscriber_failed",
                subscriber=getattr(subscriber, "__qualname__", repr(subscriber)),
                trace_event=trace_event.event,
                run_id=trace_event.run_id,
                seq=trace_event.seq,
            )
        except Exception:  # noqa: BLE001 - logging must never break event delivery
            pass


def make_log_subscriber() -> Subscriber:
    """Subscriber that mirrors every TraceEvent into structlog (payload masked)."""
    log = get_logger(node="trace")

    async def _log_event(event: TraceEvent) -> None:
        log.info(
            "trace_event",
            trace_event=event.event,
            run_id=event.run_id,
            step_id=event.step_id,
            seq=event.seq,
            payload=event.payload,
        )

    return _log_event
