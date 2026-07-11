"""Live self-correction scenarios (T-025; ARCHITECTURE §1.2).

(a) worker level: the household word "profit" is not a canonical metric —
    the first sql_query fails with a teaching hint, the agent introspects the
    schema and retries correctly;
(b) orchestrator level: a company outside the loaded corpus -> no_data step ->
    replan -> honest data-boundary note in the answer.

LLM non-determinism policy: <=1 test retry (pytest-rerunfailures not used —
the assertion tolerates order noise, and T-025 records a 5-run stability
check in the task log).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from common.agents import WorkerTask
from common.config import get_settings
from common.db import get_session_factory
from common.tracing import TraceBus, TraceEvent
from orchestrator.api import app, ensure_stream_infrastructure
from workers.react_worker import run_worker_task

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not get_settings().deepseek_api_key, reason="no DEEPSEEK_API_KEY"),
]


async def _corpus_ready() -> bool:
    factory = get_session_factory()
    async with factory() as session:
        count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM financial_facts f JOIN companies c "
                    "ON c.id = f.company_id WHERE c.ticker = 'AAPL' AND f.metric = 'net_income'"
                )
            )
        ).scalar_one()
    return int(count) > 0


async def test_worker_self_corrects_unknown_metric_term() -> None:
    """Scenario (a): the user asks for the non-canonical metric 'profit'.

    Observed reality with deepseek-v4 (probed 5+ phrasings while building
    this test): the model never guesses blindly, so the correction takes the
    DISCOVERY form (schema_introspect or a discovery SELECT resolving the
    term) rather than the error->hint form the task sheet predicted. Both
    forms are genuine self-correction and both are accepted here; the error
    path stays covered at the tool layer by T-012 tests and remains
    observable with weaker (local) models.
    """
    if not await _corpus_ready():
        pytest.skip("demo corpus not ingested")
    import uuid

    bus = TraceBus()
    events: list[TraceEvent] = []

    async def _collect(event: TraceEvent) -> None:
        events.append(event)

    bus.subscribe(_collect)
    task = WorkerTask(
        task_id="sc-a",
        run_id=str(uuid.uuid4()),
        goal=(
            "Report the value of the metric 'profit' for Apple for fiscal year "
            "2025. If that exact metric does not exist, resolve it to the "
            "closest canonical metric yourself and report that number - do "
            "not ask for confirmation."
        ),
        allowed_tools=["sql_query", "schema_introspect"],
    )
    result = await run_worker_task(task, trace_bus=bus)

    tool_finished = [
        (e.payload.get("tool"), e.payload.get("status"))
        for e in events
        if e.event == "tool_call_finished"
    ]
    assert result.status == "succeeded"
    assert tool_finished, "no tool calls traced"
    assert tool_finished[-1] == ("sql_query", "ok")
    error_form = ("sql_query", "error") in tool_finished[:-1]
    discovery_form = len(tool_finished) >= 2 and (
        ("schema_introspect", "ok") in tool_finished[:-1]
        or ("sql_query", "ok") in tool_finished[:-1]
    )
    assert error_form or discovery_form, f"no correction chain in {tool_finished}"
    # The user's term resolved to the canonical metric, with Apple's real
    # FY2025 net income (~$112B) in the answer.
    combined = (
        result.answer + " ".join(fact.get("sql", "") for fact in result.evidence.facts)
    ).lower()
    assert "net_income" in combined
    assert "112" in result.answer.replace(",", "").replace(" ", "")


async def test_orchestrator_replans_on_missing_company() -> None:
    """Scenario (b): Netflix is not ingested -> no_data -> replan -> honest boundary."""
    if not await _corpus_ready():
        pytest.skip("demo corpus not ingested")
    ensure_stream_infrastructure()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=1500
    ) as client:
        body = ""
        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "question": (
                    "Compare the fiscal 2025 revenue of Apple and Netflix using the loaded data."
                )
            },
        ) as response:
            assert response.status_code == 200
            async for chunk in response.aiter_text():
                body += chunk
    events: list[dict[str, Any]] = [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]
    names = [event["event"] for event in events]
    assert "plan_updated" in names, f"no replan in the trace: {names}"
    final = events[-1]
    assert final["event"] == "run_finished"
    answer = str(final["payload"]["answer"]).lower()
    assert "netflix" in answer
    # The answer states the data boundary instead of inventing Tesla numbers.
    assert final["payload"]["partial"] or any(
        marker in answer
        for marker in ("not loaded", "not available", "no data", "unavailable", "absent", "missing")
    )
