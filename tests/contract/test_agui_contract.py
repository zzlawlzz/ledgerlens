"""AG-UI adapter contract tests (T-023): mapping, ordering, START/END pairing.

A recorded TraceEvent sequence (the shape a real multi-step run produces) is
translated and validated: every event encodes with the protocol encoder,
RUN_STARTED opens, RUN_FINISHED closes, tool calls and the text message are
properly paired, state events carry valid JSON Patch.
"""

from __future__ import annotations

import json
from typing import Any

from ag_ui.core.events import EventType
from ag_ui.encoder import EventEncoder

from common.tracing import TraceEvent, TraceEventName
from orchestrator.agui import TraceToAgUiAdapter

RUN = "11111111-1111-1111-1111-111111111111"


def _trace(seq: int, event: TraceEventName, payload: dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        ts=1000.0 + seq, run_id=RUN, step_id=None, seq=seq, event=event, payload=payload
    )


RECORDED_RUN = [
    _trace(1, "run_started", {"question": "compare", "mode": "us"}),
    _trace(2, "llm_call", {"task_class": "route", "provider": "ollama", "model": "m"}),
    _trace(
        3,
        "plan_created",
        {"steps": [{"id": "s1", "goal": "g1", "status": "pending"}]},
    ),
    _trace(4, "step_started", {"node": "worker", "goal": "g1", "plan_step": "s1"}),
    _trace(5, "agent_thought", {"text": "let me query"}),
    _trace(6, "tool_call_started", {"tool": "sql_query", "arguments": {"sql": "SELECT 1"}}),
    _trace(7, "tool_call_finished", {"tool": "sql_query", "status": "ok"}),
    _trace(
        8,
        "step_finished",
        {"status": "succeeded", "plan_step": "s1", "worker_node": "local"},
    ),
    _trace(
        9,
        "plan_updated",
        {"steps": [{"id": "s1", "goal": "g1", "status": "done"}], "reason": "r"},
    ),
    _trace(10, "guardrail", {"triggered": False, "action": "pass", "spans": []}),
    _trace(
        11,
        "run_finished",
        {
            "status": "succeeded",
            "answer": "The answer. " * 40,  # long enough to need several chunks
            "key_values": {"aapl_revenue": {"value": 1.0}},
            "citations": [{"ticker": "AAPL"}],
            "partial": False,
            "usage": {"cost_usd": 0.01},
        },
    ),
]


def _translate_all() -> list[Any]:
    adapter = TraceToAgUiAdapter(thread_id="t1", run_id="r1")
    events: list[Any] = []
    for trace_event in RECORDED_RUN:
        events.extend(adapter.translate(trace_event))
    return events


def test_every_event_encodes_with_protocol_encoder() -> None:
    encoder = EventEncoder()
    for event in _translate_all():
        encoded = encoder.encode(event)
        assert encoded.startswith("data: ")
        json.loads(encoded[len("data: ") :])  # valid JSON payload


def test_ordering_run_started_first_run_finished_last() -> None:
    events = _translate_all()
    assert events[0].type == EventType.RUN_STARTED
    assert events[-1].type == EventType.RUN_FINISHED
    assert sum(1 for e in events if e.type == EventType.RUN_STARTED) == 1
    assert sum(1 for e in events if e.type == EventType.RUN_FINISHED) == 1


def test_tool_call_start_end_pairing_with_shared_id() -> None:
    events = _translate_all()
    starts = [e for e in events if e.type == EventType.TOOL_CALL_START]
    args = [e for e in events if e.type == EventType.TOOL_CALL_ARGS]
    ends = [e for e in events if e.type == EventType.TOOL_CALL_END]
    assert len(starts) == len(ends) == len(args) == 1
    assert starts[0].tool_call_id == args[0].tool_call_id == ends[0].tool_call_id
    assert starts[0].tool_call_name == "sql_query"
    assert json.loads(args[0].delta) == {"sql": "SELECT 1"}


def test_text_message_pairing_and_full_answer_reassembly() -> None:
    events = _translate_all()
    start = next(e for e in events if e.type == EventType.TEXT_MESSAGE_START)
    contents = [e for e in events if e.type == EventType.TEXT_MESSAGE_CONTENT]
    end = next(e for e in events if e.type == EventType.TEXT_MESSAGE_END)
    assert len(contents) >= 2  # long answer is actually chunked
    assert all(c.message_id == start.message_id for c in contents)
    assert end.message_id == start.message_id
    assert "".join(c.delta for c in contents) == "The answer. " * 40
    # the message must be closed before RUN_FINISHED
    assert events.index(end) < events.index(events[-1])


def test_state_snapshot_and_json_patch_deltas() -> None:
    events = _translate_all()
    snapshot = next(e for e in events if e.type == EventType.STATE_SNAPSHOT)
    assert snapshot.snapshot["plan"][0]["id"] == "s1"
    deltas = [e for e in events if e.type == EventType.STATE_DELTA]
    assert len(deltas) == 3  # step_started, step_finished, plan_updated
    for delta in deltas:
        for op in delta.delta:  # RFC 6902 shape
            assert set(op) >= {"op", "path", "value"}
            assert op["op"] in ("add", "replace")
            assert op["path"].startswith("/")
    step_delta = deltas[1].delta[0]
    assert step_delta["path"] == "/steps/s1"
    assert step_delta["value"]["worker_node"] == "local"


def test_custom_events_for_thought_guardrail_llm_call() -> None:
    events = _translate_all()
    names = [e.name for e in events if e.type == EventType.CUSTOM]
    assert "thought" in names and "guardrail" in names and "llm_call" in names
    assert "run_summary" in names  # key_values/citations for the UI
