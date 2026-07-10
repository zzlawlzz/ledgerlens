"""Integration tests for Chat API v0 (T-014): SSE stream + DB persistence.

The live test drives the full path question -> worker -> live DeepSeek ->
sql tools -> SSE stream -> runs/steps/llm_calls/tool_calls rows.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from common.config import get_settings
from common.db import get_session_factory
from orchestrator.api import app, ensure_stream_infrastructure

pytestmark = pytest.mark.slow

TICKER = "TAPI"
REVENUE_2025 = 416_161_000_000


@pytest.fixture(autouse=True)
async def _seed() -> AsyncIterator[None]:
    ensure_stream_infrastructure()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source = 'test'"))
        company_id = (
            await session.execute(
                text(
                    "INSERT INTO companies (source, external_id, ticker, name) VALUES "
                    "('test', 'TAPI1', :t, 'Test API Corp') RETURNING id"
                ),
                {"t": TICKER},
            )
        ).scalar_one()
        filing_id = (
            await session.execute(
                text(
                    "INSERT INTO filings (company_id, source_filing_id, form_type, "
                    "fiscal_period, filed_at) VALUES (:cid, 'tapi-10k', '10-K', 'FY', "
                    "'2025-11-01') RETURNING id"
                ),
                {"cid": company_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO financial_facts (filing_id, company_id, metric, value, unit, "
                "period_end, fiscal_year, fiscal_period, standard) VALUES "
                "(:fid, :cid, 'revenue', :val, 'USD', :pe, 2025, 'FY', 'US-GAAP')"
            ),
            {
                "fid": filing_id,
                "cid": company_id,
                "val": REVENUE_2025,
                "pe": date(2025, 9, 27),
            },
        )
        await session.commit()
    yield
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source = 'test'"))
        await session.commit()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120
    )


def _parse_sse(chunk_text: str) -> list[dict[str, Any]]:
    events = []
    for line in chunk_text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


async def test_healthz_ok() -> None:
    async with _client() as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.skipif(not get_settings().deepseek_api_key, reason="no DEEPSEEK_API_KEY")
async def test_chat_streams_events_and_persists_consistent_rows() -> None:
    question = (
        f"What was the revenue of Test API Corp (ticker {TICKER}) in fiscal year 2025? "
        "Answer with the exact number."
    )
    async with _client() as client:
        async with client.stream("POST", "/api/chat", json={"question": question}) as response:
            assert response.status_code == 200
            run_id = response.headers["X-Run-Id"]
            body = ""
            async for chunk in response.aiter_text():
                body += chunk
    events = _parse_sse(body)
    names = [event["event"] for event in events]
    assert names[0] == "run_started"
    assert "tool_call_started" in names and "llm_call" in names
    assert names[-1] == "run_finished"
    final = events[-1]
    assert final["payload"]["status"] == "succeeded"
    assert "416161" in final["payload"]["answer"].replace(",", "").replace(".", "")
    assert all(event["run_id"] == run_id for event in events)

    factory = get_session_factory()
    async with factory() as session:
        run = (
            await session.execute(
                text(
                    "SELECT status, answer, cost_usd, latency_ms, tokens_in, tokens_out, "
                    "finished_at FROM runs WHERE id = :id"
                ),
                {"id": uuid.UUID(run_id)},
            )
        ).one()
        step_count = (
            await session.execute(
                text("SELECT count(*) FROM steps WHERE run_id = :id AND status = 'succeeded'"),
                {"id": uuid.UUID(run_id)},
            )
        ).scalar_one()
        llm_rows = (
            await session.execute(
                text(
                    "SELECT count(*), COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = :id"
                ),
                {"id": uuid.UUID(run_id)},
            )
        ).one()
        tool_rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM tool_calls WHERE run_id = :id AND latency_ms IS NOT NULL"
                ),
                {"id": uuid.UUID(run_id)},
            )
        ).scalar_one()
    assert run.status == "succeeded"
    assert run.finished_at is not None and run.latency_ms > 0
    assert run.tokens_in > 0 and run.tokens_out > 0
    assert float(run.cost_usd) > 0  # priced from config/prices.yaml
    assert step_count == 1
    assert llm_rows[0] >= 1 and float(llm_rows[1]) == float(run.cost_usd)
    assert tool_rows >= 1


@pytest.mark.skipif(not get_settings().deepseek_api_key, reason="no DEEPSEEK_API_KEY")
async def test_client_disconnect_does_not_leave_run_running() -> None:
    question = f"What was the revenue of {TICKER} in FY2025?"
    async with _client() as client:
        async with client.stream("POST", "/api/chat", json={"question": question}) as response:
            run_id = response.headers["X-Run-Id"]
            async for _ in response.aiter_text():
                break  # drop the connection after the first chunk

    factory = get_session_factory()
    status = "running"
    for _ in range(60):  # the background runner keeps going after the disconnect
        await asyncio.sleep(1)
        async with factory() as session:
            status = (
                await session.execute(
                    text("SELECT status FROM runs WHERE id = :id"),
                    {"id": uuid.UUID(run_id)},
                )
            ).scalar_one()
        if status != "running":
            break
    assert status in ("succeeded", "failed", "budget_exceeded")
