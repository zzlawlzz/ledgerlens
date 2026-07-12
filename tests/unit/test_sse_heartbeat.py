"""SSE keep-alive heartbeat for the streaming endpoints (T-036 §4).

Both SSE endpoints block on the run-event queue while a slow orchestrator step
runs. Without a keep-alive, an idle connection is dropped by proxy idle timeouts
(Cloudflare Tunnel ~100 s), killing the stream mid-analysis. These tests pin the
behaviour: idle → an SSE comment tick every ``SSE_HEARTBEAT_INTERVAL_S`` (≤ 15 s),
no queued event is lost across a timeout, and the headers disable buffering.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from common.tracing import TraceEvent, TraceEventName
from orchestrator import agui, api


def _ev(run_id: str, event: TraceEventName) -> TraceEvent:
    return TraceEvent(ts=1000.0, run_id=run_id, step_id=None, seq=1, event=event, payload={})


async def _one(aiter: AsyncIterator[object]) -> object:
    return await aiter.__anext__()


def test_heartbeat_constant_is_an_ignored_sse_comment() -> None:
    # SSE comment lines start with ':' and are ignored by every SSE client,
    # including @ag-ui/client — safe to interleave between real events.
    assert agui.SSE_HEARTBEAT.startswith(":")
    assert agui.SSE_HEARTBEAT.endswith("\n\n")


def test_heartbeat_interval_within_proxy_budget() -> None:
    assert 0 < api.SSE_HEARTBEAT_INTERVAL_S <= 15.0  # T-036 §4


def test_sse_headers_disable_buffering() -> None:
    headers = api._sse_headers("run-1")
    assert headers["X-Accel-Buffering"] == "no"
    assert headers["Cache-Control"] == "no-cache"
    assert headers["X-Run-Id"] == "run-1"


async def test_iter_with_heartbeat_ticks_none_when_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "SSE_HEARTBEAT_INTERVAL_S", 0.01)
    queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
    assert await _one(api._iter_with_heartbeat(queue)) is None


async def test_iter_with_heartbeat_passes_events_through() -> None:
    queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
    event = _ev("run-x", "run_started")
    queue.put_nowait(event)
    assert await _one(api._iter_with_heartbeat(queue)) is event


async def test_iter_with_heartbeat_does_not_drop_event_after_a_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # wait_for cancels the pending get on timeout; the event queued afterwards
    # must still be delivered on the next iteration (no lost events).
    monkeypatch.setattr(api, "SSE_HEARTBEAT_INTERVAL_S", 0.01)
    queue: asyncio.Queue[TraceEvent] = asyncio.Queue()
    it = api._iter_with_heartbeat(queue)
    assert await _one(it) is None  # idle tick
    event = _ev("run-x", "run_finished")
    queue.put_nowait(event)
    assert await _one(it) is event


async def test_drain_events_emits_keepalive_then_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "SSE_HEARTBEAT_INTERVAL_S", 0.01)
    queue, subscriber = api._subscribe_run_events("run-drain")
    gen = api._drain_events(queue, subscriber)
    first = await _one(gen)
    assert first == agui.SSE_HEARTBEAT
    queue.put_nowait(_ev("run-drain", "run_finished"))
    data = await _one(gen)
    assert isinstance(data, str) and data.startswith("data: ")
    with pytest.raises(StopAsyncIteration):  # terminal event closes the stream
        await _one(gen)


async def test_stream_agui_run_emits_heartbeat_for_none_tick() -> None:
    from ag_ui.core import RunAgentInput

    body = RunAgentInput(
        thread_id="t1",
        run_id="r1",
        state={},
        messages=[],
        tools=[],
        context=[],
        forwarded_props={},
    )

    async def _ticks() -> AsyncIterator[TraceEvent | None]:
        yield None  # orchestrator idle -> heartbeat, then stream ends

    chunks = [chunk async for chunk in agui.stream_agui_run(body, _ticks())]
    assert chunks == [agui.SSE_HEARTBEAT]
