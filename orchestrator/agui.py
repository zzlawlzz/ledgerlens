"""AG-UI endpoint (T-023; ARCHITECTURE §6, CONTRACTS §10).

``POST /agui`` takes the protocol's ``RunAgentInput`` (thread_id = the
checkpointer session, the last user message = the question) and streams
AG-UI events over SSE. The adapter translates internal TraceEvents strictly
by the CONTRACTS §10 table:

    run_started/finished/error -> RUN_STARTED / RUN_FINISHED / RUN_ERROR
    plan_created               -> STATE_SNAPSHOT {plan, steps}
    plan_updated, step_*       -> STATE_DELTA (JSON Patch RFC 6902)
    tool_call_started/finished -> TOOL_CALL_START + ARGS / TOOL_CALL_END
    agent_thought, guardrail,
    llm_call, budget           -> CUSTOM (named)
    final answer               -> TEXT_MESSAGE_START / CONTENT / END

The old ``/api/chat`` SSE stays as the curl-friendly debug endpoint.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui.core.events import (
    BaseEvent,
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from common.tracing import TraceEvent

ANSWER_CHUNK_CHARS = 160

# SSE comment line (starts with ':') — ignored by every SSE client, including
# @ag-ui/client, but keeps an otherwise-idle connection warm through proxy
# idle timeouts (Cloudflare Tunnel ~100 s). See T-036 §4.
SSE_HEARTBEAT = ": keep-alive\n\n"


class TraceToAgUiAdapter:
    """Stateful translator: one instance per run (tracks tool-call ids)."""

    def __init__(self, thread_id: str, run_id: str) -> None:
        self._thread_id = thread_id
        self._run_id = run_id
        self._open_tool_calls: dict[str, str] = {}  # tool name -> tool_call_id

    def translate(self, event: TraceEvent) -> list[BaseEvent]:
        payload = event.payload
        match event.event:
            case "run_started":
                return [RunStartedEvent(thread_id=self._thread_id, run_id=self._run_id)]
            case "plan_created":
                return [
                    StateSnapshotEvent(snapshot={"plan": payload.get("steps", []), "steps": {}})
                ]
            case "plan_updated":
                return [
                    StateDeltaEvent(
                        delta=[
                            {
                                "op": "replace",
                                "path": "/plan",
                                "value": payload.get("steps", []),
                            }
                        ]
                    )
                ]
            case "step_started" | "step_finished":
                step_id = str(payload.get("plan_step", payload.get("node", "step")))
                status = (
                    "running" if event.event == "step_started" else payload.get("status", "done")
                )
                value: dict[str, Any] = {"status": status}
                if payload.get("goal"):
                    value["goal"] = payload["goal"]
                if payload.get("worker_node"):
                    value["worker_node"] = payload["worker_node"]
                return [
                    StateDeltaEvent(
                        delta=[{"op": "add", "path": f"/steps/{step_id}", "value": value}]
                    )
                ]
            case "tool_call_started":
                import json

                tool = str(payload.get("tool", "tool"))
                # Prefer the worker's stable per-call id; fall back to a fresh id
                # keyed by tool name for legacy traces that don't carry one.
                call_id = str(payload.get("tool_call_id") or uuid.uuid4())
                self._open_tool_calls[tool] = call_id
                return [
                    ToolCallStartEvent(tool_call_id=call_id, tool_call_name=tool),
                    ToolCallArgsEvent(
                        tool_call_id=call_id,
                        delta=json.dumps(payload.get("arguments", {}), ensure_ascii=False),
                    ),
                ]
            case "tool_call_finished":
                tool = str(payload.get("tool", "tool"))
                explicit = payload.get("tool_call_id")
                if explicit is not None:
                    # Pair by the worker-provided id: correct even when the same
                    # tool ran more than once with overlapping lifetimes. Keying
                    # by name alone collided (the second start overwrote the
                    # first) and the orphaned finish emitted an unstarted END,
                    # which the AG-UI client rejects with "No active tool call
                    # found" and aborts the run.
                    call_id = str(explicit)
                    if self._open_tool_calls.get(tool) == call_id:
                        self._open_tool_calls.pop(tool, None)
                else:
                    popped = self._open_tool_calls.pop(tool, None)
                    if popped is None:
                        # No matching start on record: never emit an orphan END.
                        return []
                    call_id = popped
                return [
                    ToolCallEndEvent(tool_call_id=call_id),
                    # Status/preview for the UI: a failed call is highlighted
                    # so the self-correction chain reads clearly (T-025).
                    CustomEvent(
                        name="tool_result",
                        value={
                            "tool_call_id": call_id,
                            "status": payload.get("status", "ok"),
                            "preview": str(payload.get("preview", ""))[:400],
                        },
                    ),
                ]
            case "agent_thought":
                return [CustomEvent(name="thought", value={"text": payload.get("text", "")})]
            case "guardrail" | "llm_call" | "budget":
                return [CustomEvent(name=event.event, value=dict(payload))]
            case "run_finished":
                message_id = str(uuid.uuid4())
                answer = str(payload.get("answer", ""))
                content = [
                    TextMessageContentEvent(
                        message_id=message_id,
                        delta=answer[i : i + ANSWER_CHUNK_CHARS],
                    )
                    for i in range(0, len(answer), ANSWER_CHUNK_CHARS)
                ] or [TextMessageContentEvent(message_id=message_id, delta=" ")]
                return [
                    TextMessageStartEvent(message_id=message_id),
                    *content,
                    TextMessageEndEvent(message_id=message_id),
                    CustomEvent(
                        name="run_summary",
                        value={
                            "status": payload.get("status"),
                            "key_values": payload.get("key_values", {}),
                            "citations": payload.get("citations", []),
                            "partial": payload.get("partial", False),
                            "usage": payload.get("usage", {}),
                        },
                    ),
                    RunFinishedEvent(thread_id=self._thread_id, run_id=self._run_id),
                ]
            case "run_error":
                return [RunErrorEvent(message=str(payload.get("error", "unknown error")))]
            case _:
                return []


def extract_question(body: RunAgentInput) -> str:
    """The last user message is the question."""
    for message in reversed(body.messages or []):
        if getattr(message, "role", "") == "user" and getattr(message, "content", ""):
            return str(message.content)
    raise ValueError("RunAgentInput has no user message")


async def stream_agui_run(
    body: RunAgentInput,
    trace_queue: AsyncIterator[TraceEvent | None],
) -> AsyncIterator[str]:
    """TraceEvents -> encoded AG-UI SSE lines (terminates on run end).

    A ``None`` from ``trace_queue`` is a heartbeat tick (the orchestrator has
    been silent longer than the keep-alive interval): emit an SSE comment line
    so an idle stream survives proxy timeouts (T-036 §4 / Cloudflare Tunnel).
    """
    encoder = EventEncoder()
    adapter = TraceToAgUiAdapter(body.thread_id, body.run_id)
    async for trace_event in trace_queue:
        if trace_event is None:
            yield SSE_HEARTBEAT
            continue
        for agui_event in adapter.translate(trace_event):
            yield encoder.encode(agui_event)
        if trace_event.event in ("run_finished", "run_error"):
            return
