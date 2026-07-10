"""Live integration tests for the Plan-and-Execute orchestrator (T-020).

Require: postgres+qdrant up, demo corpus ingested with --embed (T-018),
DEEPSEEK_API_KEY. The multi-step test exercises plan -> 2xSQL + 1xRAG ->
synthesize with citations; the follow-up test exercises the checkpointer.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from common.config import get_settings
from common.db import get_session_factory
from orchestrator import api as api_module
from orchestrator.api import app, ensure_stream_infrastructure

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not get_settings().deepseek_api_key, reason="no DEEPSEEK_API_KEY"),
]


async def _corpus_ready() -> bool:
    factory = get_session_factory()
    async with factory() as session:
        chunks = (await session.execute(text("SELECT COUNT(*) FROM section_chunks"))).scalar_one()
        facts = (
            await session.execute(
                text(
                    "SELECT COUNT(DISTINCT c.ticker) FROM financial_facts f "
                    "JOIN companies c ON c.id = f.company_id "
                    "WHERE c.ticker IN ('AAPL','MSFT')"
                )
            )
        ).scalar_one()
    return int(chunks) > 0 and int(facts) == 2


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=600
    )


async def _ask(question: str, session_id: str | None = None) -> list[dict[str, Any]]:
    ensure_stream_infrastructure()
    payload: dict[str, Any] = {"question": question}
    if session_id:
        payload["session_id"] = session_id
    events: list[dict[str, Any]] = []
    async with _client() as client:
        async with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200
            body = ""
            async for chunk in response.aiter_text():
                body += chunk
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


async def test_multistep_question_plans_executes_and_cites() -> None:
    if not await _corpus_ready():
        pytest.skip("demo corpus not ingested (run make demo-ingest)")
    events = await _ask(
        "Compare the revenue dynamics of Apple and Microsoft over the last 3 fiscal "
        "years, and name the main risks Apple discloses."
    )
    names = [event["event"] for event in events]
    assert "plan_created" in names
    plan_event = next(e for e in events if e["event"] == "plan_created")
    steps = plan_event["payload"]["steps"]
    assert len(steps) >= 3  # 2x SQL (one per company) + 1x RAG
    skills = {step["skill"] for step in steps}
    assert "financial_sql_analysis" in skills and "narrative_rag_analysis" in skills
    assert names.count("step_started") >= 3
    final = events[-1]
    assert final["event"] == "run_finished"
    assert final["payload"]["status"] in ("succeeded", "budget_exceeded")
    answer = final["payload"]["answer"]
    # Citation markers survive synthesis (exact formatting is model-dependent).
    assert "10-K" in answer and "AAPL" in answer.upper()
    assert final["payload"]["citations"], "citations must be collected in the state"
    assert any(c.get("section") == "risk_factors" for c in final["payload"]["citations"])


async def test_follow_up_question_uses_session_context() -> None:
    if not await _corpus_ready():
        pytest.skip("demo corpus not ingested (run make demo-ingest)")
    if api_module._CHECKPOINTER is None:  # noqa: SLF001 — lifespan doesn't run under ASGITransport
        await api_module._init_checkpointer()  # noqa: SLF001
        api_module._ORCHESTRATOR = None  # rebuild with the checkpointer
    if api_module._CHECKPOINTER is None:
        pytest.skip("postgres checkpointer unavailable")
    session_id = f"it-{uuid.uuid4()}"
    first = await _ask(
        "What was Apple's revenue in fiscal year 2024 and fiscal year 2025?",
        session_id=session_id,
    )
    assert first[-1]["payload"]["status"] == "succeeded"
    second = await _ask("And Microsoft's, for the same fiscal years?", session_id=session_id)
    assert second[-1]["payload"]["status"] == "succeeded"
    answer = second[-1]["payload"]["answer"].lower()
    assert "microsoft" in answer or "msft" in answer
