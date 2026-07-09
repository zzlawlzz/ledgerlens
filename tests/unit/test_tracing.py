"""Unit tests for the TraceEvent bus (T-004, CONTRACTS.md §10)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import structlog

from common.logging import bind_run_context, configure_logging
from common.tracing import TraceBus, TraceEvent, make_log_subscriber


@pytest.fixture(autouse=True)
def _clean_contextvars() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _collector(sink: list[TraceEvent]):  # type: ignore[no-untyped-def]
    async def _collect(event: TraceEvent) -> None:
        sink.append(event)

    return _collect


async def test_all_subscribers_receive_events_in_seq_order() -> None:
    bus = TraceBus()
    got_a: list[TraceEvent] = []
    got_b: list[TraceEvent] = []
    bus.subscribe(_collector(got_a))
    bus.subscribe(_collector(got_b))

    await bus.publish("run_started", run_id="r1")
    await bus.publish("step_started", {"goal": "g"}, run_id="r1", step_id="s1")
    await bus.publish("run_finished", run_id="r1")

    assert [e.seq for e in got_a] == [1, 2, 3]
    assert [e.seq for e in got_b] == [1, 2, 3]
    assert [e.event for e in got_a] == ["run_started", "step_started", "run_finished"]
    assert got_a[1].step_id == "s1"


async def test_run_id_comes_from_contextvars() -> None:
    bus = TraceBus()
    bind_run_context(run_id="ctx-run", step_id="ctx-step")
    event = await bus.publish("agent_thought", {"text": "hmm"})
    assert event.run_id == "ctx-run"
    assert event.step_id == "ctx-step"
    assert event.seq == 1


async def test_publish_without_run_id_raises() -> None:
    bus = TraceBus()
    with pytest.raises(ValueError, match="run_id"):
        await bus.publish("run_started")


async def test_seq_is_per_run_and_resets_after_terminal_event() -> None:
    bus = TraceBus()
    assert (await bus.publish("run_started", run_id="r1")).seq == 1
    assert (await bus.publish("run_started", run_id="r2")).seq == 1
    assert (await bus.publish("answer_delta", run_id="r1")).seq == 2
    assert (await bus.publish("run_finished", run_id="r1")).seq == 3
    # new run with the same id starts a fresh sequence
    assert (await bus.publish("run_started", run_id="r1")).seq == 1
    # r2 was not finished — its counter is intact
    assert (await bus.publish("run_error", run_id="r2")).seq == 2


async def test_subscriber_failure_does_not_affect_others() -> None:
    bus = TraceBus()
    received: list[TraceEvent] = []

    async def broken(event: TraceEvent) -> None:
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(_collector(received))
    event = await bus.publish("budget", {"spent_usd": 0.1}, run_id="r1")
    assert received == [event]


async def test_unsubscribe_stops_delivery() -> None:
    bus = TraceBus()
    received: list[TraceEvent] = []
    collector = _collector(received)
    bus.subscribe(collector)
    await bus.publish("run_started", run_id="r1")
    bus.unsubscribe(collector)
    await bus.publish("run_finished", run_id="r1")
    assert [e.event for e in received] == ["run_started"]


async def test_log_subscriber_masks_payload_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("svc")
    bus = TraceBus()
    bus.subscribe(make_log_subscriber())
    await bus.publish("llm_call", {"provider": "deepseek", "api_key": "sk-zzz"}, run_id="r9")
    raw = capsys.readouterr().out
    assert "sk-zzz" not in raw
    data = json.loads(raw.strip().splitlines()[-1])
    assert data["trace_event"] == "llm_call"
    assert data["payload"]["api_key"] == "***"
    assert data["payload"]["provider"] == "deepseek"


def test_trace_event_as_dict_roundtrip() -> None:
    event = TraceEvent(
        ts=1.0, run_id="r", seq=1, event="run_started", payload={"q": "?"}, step_id=None
    )
    as_dict = event.as_dict()
    assert as_dict["event"] == "run_started"
    assert json.loads(json.dumps(as_dict)) == as_dict
