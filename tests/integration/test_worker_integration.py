"""Integration tests for the ReAct worker (T-013): real DB tools + live LLM.

- self-correction: scripted LLM, real sql tools against seeded data —
  bad SQL -> teaching error -> schema_introspect -> fixed SQL -> success;
- live: real DeepSeek call (requires DEEPSEEK_API_KEY in .env).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage

from common.agents import WorkerTask
from common.config import get_settings
from common.db import get_session_factory
from workers.react_worker import run_worker_task

pytestmark = pytest.mark.slow

TICKER = "TAPL"
FY_REVENUE = {2023: 383_285_000_000, 2024: 391_035_000_000, 2025: 416_161_000_000}


class ScriptedChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self


@pytest.fixture(autouse=True)
async def _seed_test_apple() -> AsyncIterator[None]:
    from sqlalchemy import text

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source = 'test'"))
        company_id = (
            await session.execute(
                text(
                    "INSERT INTO companies (source, external_id, ticker, name) VALUES "
                    "('test', 'TAPL1', :t, 'Test Apple Inc.') RETURNING id"
                ),
                {"t": TICKER},
            )
        ).scalar_one()
        filing_id = (
            await session.execute(
                text(
                    "INSERT INTO filings (company_id, source_filing_id, form_type, "
                    "fiscal_period, filed_at) VALUES (:cid, 'tapl-10k', '10-K', 'FY', "
                    "'2025-11-01') RETURNING id"
                ),
                {"cid": company_id},
            )
        ).scalar_one()
        for year, revenue in FY_REVENUE.items():
            await session.execute(
                text(
                    "INSERT INTO financial_facts (filing_id, company_id, metric, value, "
                    "unit, period_end, fiscal_year, fiscal_period, standard) VALUES "
                    "(:fid, :cid, 'revenue', :val, 'USD', :pe, :fy, 'FY', 'US-GAAP')"
                ),
                {
                    "fid": filing_id,
                    "cid": company_id,
                    "val": revenue,
                    "pe": date(year, 9, 27),
                    "fy": year,
                },
            )
        await session.commit()
    yield
    async with factory() as session:
        await session.execute(text("DELETE FROM companies WHERE source = 'test'"))
        await session.commit()


async def test_self_correction_bad_sql_then_introspect_then_fixed() -> None:
    """Worker-level self-correction chain (T-025 scenario 'a', deterministic)."""
    script: list[BaseMessage] = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "sql_query",
                    "args": {"sql": f"SELECT profit FROM latest_facts WHERE ticker='{TICKER}'"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[{"name": "schema_introspect", "args": {}, "id": "c2", "type": "tool_call"}],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "sql_query",
                    "args": {
                        "sql": (
                            "SELECT period_end, value FROM latest_facts WHERE "
                            f"ticker='{TICKER}' AND metric='revenue' AND fiscal_period='FY' "
                            "ORDER BY period_end DESC LIMIT 1"
                        )
                    },
                    "id": "c3",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content="Revenue in the latest fiscal year was 416161000000 USD.",
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        ),
    ]
    task = WorkerTask(task_id="s1", run_id="r-corr", goal="latest revenue of TAPL")
    result = await run_worker_task(task, model=ScriptedChatModel(responses=script))
    assert result.status == "succeeded"
    assert "416161000000" in result.answer

    # The trace must show the correction pattern: error -> introspect -> ok.
    finished = [e for e in result.trace if e["event"] == "tool_call_finished"]
    pattern = [(e["payload"]["tool"], e["payload"]["status"]) for e in finished]
    assert pattern == [
        ("sql_query", "error"),
        ("schema_introspect", "ok"),
        ("sql_query", "ok"),
    ]


@pytest.mark.skipif(not get_settings().deepseek_api_key, reason="DEEPSEEK_API_KEY not configured")
async def test_live_llm_answers_revenue_question_from_db() -> None:
    """T-013 acceptance: live LLM + real tools; the number must match the DB."""
    task = WorkerTask(
        task_id="s-live",
        run_id="r-live",
        goal=(
            "What was the revenue of Test Apple Inc. (ticker TAPL) in its most "
            "recent fiscal year? Answer with the exact number from the database."
        ),
    )
    result = await run_worker_task(task)
    assert result.status == "succeeded", result.answer
    digits_only = re.sub(r"\D", "", result.answer)
    assert "416161" in digits_only, f"expected DB number in answer: {result.answer!r}"
    sql_calls = [
        e
        for e in result.trace
        if e["event"] == "tool_call_started" and e["payload"]["tool"] == "sql_query"
    ]
    assert sql_calls, "trace must contain at least one sql_query call"
    assert result.usage.tokens_in > 0 and result.usage.tokens_out > 0
