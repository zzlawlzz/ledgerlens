"""Integration tests for monitoring layer B (T-035) against the real DB.

Covers the acceptance criteria that need Postgres: dedup on re-tick, the full
summarize+alert flow (guardrail-passed summary carrying a source_url, alerted_at
bookkeeping, idempotent re-summarize), and the budget / no-text guards. The
router, 8-K text fetch and alert channel are faked so the test needs no LLM,
no EDGAR network and no Telegram token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text

from common.db import get_session_factory
from orchestrator.alerting import AlertResult
from orchestrator.guardrail import GuardVerdict, find_advice_spans
from orchestrator.monitoring import DailyBudget, IngestEvent, ingest_events, summarize_event

pytestmark = pytest.mark.slow

SOURCE = "testmon"
CIK = "9999001"
FILING_TEXT = (
    "Item 5.02 Departure of Directors; Appointment of Officers. On March 1, 2026, "
    "Testmon Corp appointed Jane Roe as Chief Financial Officer, effective immediately."
)


class _FakeRouter:
    """chat() returns a fixed factual summary; chat_structured() is the guard."""

    def __init__(self, summary: str) -> None:
        self._summary = summary

    async def chat(self, task_class: str, messages: Any) -> Any:
        assert task_class == "summarize_event"
        return type("R", (), {"text": self._summary})()

    async def chat_structured(self, task_class: str, messages: Any, schema: Any) -> Any:
        return GuardVerdict(advice=False)


async def _fake_alerter(_text: str) -> AlertResult:
    return AlertResult(delivered=False, channel="dry-run")


def _event(external_id: str, **payload: Any) -> IngestEvent:
    return IngestEvent(
        source=SOURCE,
        external_id=external_id,
        event_type="8-K",
        company_external_id=CIK,
        source_url=f"https://www.sec.gov/Archives/edgar/data/{CIK}/{external_id}.htm",
        payload=payload,
    )


@pytest.fixture(autouse=True)
async def _seed() -> AsyncIterator[None]:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("DELETE FROM monitored_events WHERE source = :s"), {"s": SOURCE})
        await session.execute(text("DELETE FROM companies WHERE source = :s"), {"s": SOURCE})
        await session.execute(
            text(
                "INSERT INTO companies (source, external_id, ticker, name) "
                "VALUES (:s, :cik, 'TMON', 'Testmon Corp')"
            ),
            {"s": SOURCE, "cik": CIK},
        )
        await session.commit()
    yield
    async with factory() as session:
        await session.execute(text("DELETE FROM monitored_events WHERE source = :s"), {"s": SOURCE})
        await session.execute(text("DELETE FROM companies WHERE source = :s"), {"s": SOURCE})
        await session.commit()


async def _alerted_at(external_id: str) -> Any:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT alerted_at FROM monitored_events WHERE source = :s AND external_id = :e"
                ),
                {"s": SOURCE, "e": external_id},
            )
        ).scalar_one()


async def test_reingest_does_not_duplicate() -> None:
    events = [_event("acc-1")]
    first = await ingest_events(events)
    assert first.new_count == 1 and first.new == ["acc-1"]

    second = await ingest_events(events)
    assert second.new_count == 0 and second.seen_count == 1

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT company_id, payload FROM monitored_events "
                        "WHERE external_id = 'acc-1'"
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(rows) == 1  # exactly one row despite two ticks
    assert rows[0]["company_id"] is not None  # CIK resolved to companies.id
    assert rows[0]["payload"]["source_url"].endswith("acc-1.htm")  # url folded in


async def test_summarize_flow_alerts_once_and_is_idempotent() -> None:
    await ingest_events([_event("acc-2", cik=CIK, accession="acc-2", primary_document="d.htm")])
    router = _FakeRouter(
        "Testmon Corp appointed Jane Roe as Chief Financial Officer on March 1, 2026."
    )

    async def fetch(_payload: dict[str, Any]) -> str:
        return FILING_TEXT

    out = await summarize_event(
        SOURCE,
        "acc-2",
        router=router,
        fetch_text=fetch,
        alerter=_fake_alerter,
        budget=DailyBudget(10),
    )
    assert out.status == "summarized"
    assert out.summary and not find_advice_spans(out.summary)  # guardrail-clean
    assert out.source_url.endswith("acc-2.htm")  # criterion: alert carries source_url
    assert out.company == "Testmon Corp"
    assert await _alerted_at("acc-2") is not None

    # Second call must not re-summarize or re-alert — it returns the stored one.
    again = await summarize_event(
        SOURCE,
        "acc-2",
        router=router,
        fetch_text=fetch,
        alerter=_fake_alerter,
        budget=DailyBudget(10),
    )
    assert again.status == "already_alerted"
    assert again.summary == out.summary


async def test_summarize_budget_exceeded_leaves_event_pending() -> None:
    await ingest_events([_event("acc-3", cik=CIK, accession="acc-3", primary_document="d.htm")])

    async def fetch(_payload: dict[str, Any]) -> str:
        return FILING_TEXT

    out = await summarize_event(
        SOURCE, "acc-3", router=_FakeRouter("x"), fetch_text=fetch, budget=DailyBudget(0)
    )
    assert out.status == "budget_exceeded"
    assert await _alerted_at("acc-3") is None  # still pending for a later tick


async def test_summarize_no_text_is_honest() -> None:
    await ingest_events([_event("acc-4")])

    async def empty(_payload: dict[str, Any]) -> str:
        return ""

    out = await summarize_event(
        SOURCE, "acc-4", router=_FakeRouter("x"), fetch_text=empty, budget=DailyBudget(10)
    )
    assert out.status == "no_text"
    assert await _alerted_at("acc-4") is None


async def test_summarize_unknown_event_not_found() -> None:
    async def fetch(_payload: dict[str, Any]) -> str:
        return FILING_TEXT

    out = await summarize_event(
        SOURCE, "missing", router=_FakeRouter("x"), fetch_text=fetch, budget=DailyBudget(10)
    )
    assert out.status == "not_found"
