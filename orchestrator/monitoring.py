"""Monitoring layer B core (T-035): dedup ingest + guardrailed summarize/alert.

Layer B is scheduled by n8n (``monitoring/workflows/edgar_8k.json``): every tick
it fetches the EDGAR submissions index for the watchlist, filters recent 8-K
filings, and calls two orchestrator endpoints wired to the functions here:

* ``ingest_events`` — deduplicates against ``monitored_events``
  (UNIQUE(source, external_id)); returns only the genuinely new events.
* ``summarize_event`` — for one new event: fetch the 8-K text, summarize it with
  the ``summarize_event`` router tier, gate the summary through the non-advice
  guardrail, persist it, and dispatch the alert (Telegram or dry-run log).

Both are dependency-injected (router / text fetcher / alerter / budget) so the
whole pipeline is unit-testable without network, secrets, or a live LLM.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from common.db import get_session_factory
from common.logging import get_logger
from orchestrator.alerting import AlertResult, format_alert, send_alert
from orchestrator.guardrail import check_advice, find_advice_spans

_log = get_logger(node="monitoring")
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "summarize_event.md"
_MAX_TEXT_CHARS = 12000  # bound the LLM input: 8-K bodies vary, this caps cost

TextFetcher = Callable[[dict[str, Any]], Awaitable[str]]
Alerter = Callable[[str], Awaitable[AlertResult]]


def _load_prompt() -> str:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if raw.startswith("---"):
        return raw.partition("---")[2].partition("---")[2].strip()
    return raw.strip()


# --- ingest ----------------------------------------------------------------


class IngestEvent(BaseModel):
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)  # accession number for edgar — dedup key
    event_type: str = Field(min_length=1)  # '8-K' or item codes
    company_external_id: str | None = None  # CIK (edgar) → resolves company_id
    occurred_at: datetime | None = None
    source_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass
class IngestOutcome:
    new: list[str]  # external_ids inserted on this call
    new_count: int
    seen_count: int  # events submitted that were already known


async def ingest_events(events: list[IngestEvent], *, session_factory: Any = None) -> IngestOutcome:
    """Insert new events, ignore duplicates (UNIQUE(source, external_id)).

    ``source_url`` is folded into the stored payload so ``summarize_event`` can
    reach the document later without a second call from n8n.
    """
    factory = session_factory or get_session_factory()
    inserted: list[str] = []
    async with factory() as session:
        for event in events:
            payload = dict(event.payload)
            if event.source_url:
                payload.setdefault("source_url", event.source_url)
            result = await session.execute(
                text(
                    "INSERT INTO monitored_events "
                    "(source, external_id, company_id, event_type, occurred_at, payload) "
                    "VALUES (:source, :external_id, "
                    "(SELECT id FROM companies WHERE source = :source "
                    "AND external_id = :company_external_id), "
                    ":event_type, :occurred_at, CAST(:payload AS jsonb)) "
                    "ON CONFLICT (source, external_id) DO NOTHING "
                    "RETURNING external_id"
                ),
                {
                    "source": event.source,
                    "external_id": event.external_id,
                    "company_external_id": event.company_external_id,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "payload": json.dumps(payload),
                },
            )
            if result.scalar_one_or_none() is not None:
                inserted.append(event.external_id)
        await session.commit()
    seen = len(events) - len(inserted)
    _log.info("monitor_ingest", submitted=len(events), new=len(inserted), seen=seen)
    return IngestOutcome(new=inserted, new_count=len(inserted), seen_count=seen)


# --- daily budget ----------------------------------------------------------


class DailyBudget:
    """In-process cap on layer-B summaries per day (single API instance; the
    demo runs one orchestrator, so no cross-process coordination is needed)."""

    def __init__(self, max_per_day: int) -> None:
        self._max = max_per_day
        self._date: date | None = None
        self._count = 0

    def try_spend(self) -> bool:
        today = datetime.now(UTC).date()
        if today != self._date:
            self._date, self._count = today, 0
        if self._count >= self._max:
            return False
        self._count += 1
        return True


# --- summarize + alert -----------------------------------------------------


@dataclass
class SummarizeOutcome:
    status: str  # summarized | already_alerted | not_found | no_text | budget_exceeded
    external_id: str
    company: str = ""
    event_type: str = ""
    source_url: str = ""
    summary: str = ""
    alert: AlertResult | None = None


async def _guardrailed_summary(text_body: str, *, router: Any) -> str:
    """Summarize the filing and ensure the result carries no advice phrasing.

    A factual 8-K summary normally passes; if the guardrail trips we retry once
    with the offending spans, then deterministically fall back to a clean line.
    """
    system = _load_prompt()
    messages = [("system", system), ("user", f"8-K FILING TEXT:\n\n{text_body}")]
    response = await router.chat("summarize_event", messages)
    summary: str = response.text.strip()

    verdict = await check_advice(summary, router) if summary else None
    if verdict and verdict.advice:
        _log.info("monitor_summary_guardrail_retry", spans=verdict.spans[:3])
        retry_messages = messages + [
            ("assistant", summary),
            (
                "user",
                "The draft contained advice-like phrasing: "
                f"{verdict.spans}. Rewrite as a purely factual description of "
                "what the filing discloses, with no evaluation or recommendation.",
            ),
        ]
        summary = (await router.chat("summarize_event", retry_messages)).text.strip()

    if not summary or find_advice_spans(summary):
        # Deterministic safe fallback — never emit advice through an alert.
        summary = (
            "Company filed an 8-K disclosing a material event. See the source document for details."
        )
    return summary


async def summarize_event(
    source: str,
    external_id: str,
    *,
    router: Any,
    fetch_text: TextFetcher,
    session_factory: Any = None,
    alerter: Alerter | None = None,
    budget: DailyBudget | None = None,
) -> SummarizeOutcome:
    factory = session_factory or get_session_factory()
    send = alerter or send_alert

    async with factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT me.event_type, me.payload, me.alerted_at, me.summary, "
                        "COALESCE(c.name, c.ticker, me.external_id) AS company "
                        "FROM monitored_events me "
                        "LEFT JOIN companies c ON c.id = me.company_id "
                        "WHERE me.source = :source AND me.external_id = :external_id"
                    ),
                    {"source": source, "external_id": external_id},
                )
            )
            .mappings()
            .first()
        )

    if row is None:
        return SummarizeOutcome(status="not_found", external_id=external_id)

    payload: dict[str, Any] = dict(row["payload"] or {})
    source_url = str(payload.get("source_url", ""))
    company = str(row["company"])
    event_type = str(row["event_type"])

    if row["alerted_at"] is not None:
        return SummarizeOutcome(
            status="already_alerted",
            external_id=external_id,
            company=company,
            event_type=event_type,
            source_url=source_url,
            summary=str(row["summary"] or ""),
        )

    if budget is not None and not budget.try_spend():
        _log.warning("monitor_budget_exceeded", source=source, external_id=external_id)
        return SummarizeOutcome(
            status="budget_exceeded",
            external_id=external_id,
            company=company,
            event_type=event_type,
            source_url=source_url,
        )

    body = (await fetch_text(payload)).strip()
    if not body:
        return SummarizeOutcome(
            status="no_text",
            external_id=external_id,
            company=company,
            event_type=event_type,
            source_url=source_url,
        )

    summary = await _guardrailed_summary(body[:_MAX_TEXT_CHARS], router=router)
    alert_text = format_alert(
        company=company, event_type=event_type, summary=summary, source_url=source_url
    )
    alert_result = await send(alert_text)

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE monitored_events SET summary = :summary, alerted_at = now() "
                "WHERE source = :source AND external_id = :external_id"
            ),
            {"summary": summary, "source": source, "external_id": external_id},
        )
        await session.commit()

    _log.info(
        "monitor_summarized",
        source=source,
        external_id=external_id,
        alert_channel=alert_result.channel,
        delivered=alert_result.delivered,
    )
    return SummarizeOutcome(
        status="summarized",
        external_id=external_id,
        company=company,
        event_type=event_type,
        source_url=source_url,
        summary=summary,
        alert=alert_result,
    )
