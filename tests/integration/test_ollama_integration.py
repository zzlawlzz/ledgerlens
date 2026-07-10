"""Integration tests for local inference in the loop (T-017).

Two complementary tests: exactly one of them runs depending on whether an
Ollama server is reachable — local service of `route` calls when it is, the
clean fallback to cloud when it is not (the T-017 outage criterion).
"""

from __future__ import annotations

import httpx
import pytest

from common.config import get_settings
from common.logging import bind_run_context, reset_run_context
from common.tracing import TraceBus, TraceEvent
from model_router.router import RouterClient

pytestmark = pytest.mark.slow


def _ollama_reachable() -> bool:
    settings = get_settings()
    if not settings.has_local_llm():
        return False
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=3)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


async def _route_call() -> tuple[str, bool, list[TraceEvent]]:
    bus = TraceBus()
    events: list[TraceEvent] = []

    async def _collect(event: TraceEvent) -> None:
        events.append(event)

    bus.subscribe(_collect)
    router = RouterClient(trace_bus=bus)
    tokens = bind_run_context(run_id="r-ollama-test")
    try:
        response = await router.chat(
            "route", [("user", "Classify this question as 'simple' or 'complex': 2+2. One word.")]
        )
    finally:
        reset_run_context(tokens)
    return response.provider, response.fallback_used, events


@pytest.mark.skipif(not _ollama_reachable(), reason="ollama is not running")
async def test_route_calls_are_served_locally() -> None:
    provider, fallback_used, events = await _route_call()
    assert provider == "ollama"
    assert fallback_used is False
    llm_events = [e for e in events if e.event == "llm_call" and "error" not in e.payload]
    assert llm_events and llm_events[-1].payload["provider"] == "ollama"


@pytest.mark.skipif(_ollama_reachable(), reason="ollama IS running — outage test skipped")
@pytest.mark.skipif(not get_settings().deepseek_api_key, reason="no DEEPSEEK_API_KEY")
async def test_route_falls_back_to_cloud_when_local_is_down() -> None:
    provider, fallback_used, events = await _route_call()
    assert provider == "deepseek"
    assert fallback_used is True
    failed = [e for e in events if e.event == "llm_call" and "error" in e.payload]
    assert failed, "the failed local attempts must be traced"
