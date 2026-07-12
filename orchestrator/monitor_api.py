"""HTTP surface for monitoring layer B (T-035).

Two endpoints called by the n8n workflow, guarded by a shared internal token
(``MONITOR_TOKEN``). The business logic lives in ``orchestrator.monitoring``;
this module only wires the real dependencies (EDGAR text fetch, router, daily
budget) and enforces auth + a single-summary concurrency limit.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from adapters.edgar.client import EdgarClient
from adapters.edgar.sections import clean_10k_html
from common.config import get_settings, load_yaml_config
from common.logging import get_logger
from model_router.router import RouterClient
from orchestrator.monitoring import (
    DailyBudget,
    IngestEvent,
    ingest_events,
    summarize_event,
)

_log = get_logger(node="monitoring")

monitor_router = APIRouter(prefix="/api/monitor", tags=["monitoring"])

# Lazily-built process singletons (one orchestrator instance in the demo).
_router_client: RouterClient | None = None
_edgar_client: EdgarClient | None = None
_budget: DailyBudget | None = None
_summarize_lock = asyncio.Semaphore(1)  # ≤1 summarize in flight (ТЗ item 5)


def _require_token(provided: str | None) -> None:
    """Enforce the shared secret. When MONITOR_TOKEN is unset (dev default),
    auth is disabled — mirrors the optional-secret ergonomics of local dev."""
    expected = get_settings().monitor_token
    if not expected:
        return
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid or missing monitor token")


def _get_router() -> RouterClient:
    global _router_client
    if _router_client is None:
        _router_client = RouterClient()
    return _router_client


def _get_budget() -> DailyBudget:
    global _budget
    if _budget is None:
        layer_b = load_yaml_config("budgets").get("layer_b", {})
        _budget = DailyBudget(int(layer_b.get("max_summaries_per_day", 50)))
    return _budget


def _get_edgar_client() -> EdgarClient:
    global _edgar_client
    if _edgar_client is None:
        _edgar_client = EdgarClient()
    return _edgar_client


async def _edgar_text_fetcher(payload: dict[str, Any]) -> str:
    """Fetch + flatten an 8-K primary document to plain text.

    Needs ``cik``, ``accession`` and ``primary_document`` in the event payload
    (n8n copies them straight from the submissions index). Missing coordinates
    yield an empty string → the caller reports ``no_text`` honestly.
    """
    cik = payload.get("cik")
    accession = payload.get("accession")
    primary_document = payload.get("primary_document")
    if not (cik and accession and primary_document):
        return ""
    html = await _get_edgar_client().get_archive_document(
        cik, str(accession), str(primary_document)
    )
    return clean_10k_html(html)


# --- request/response models ----------------------------------------------


class IngestEventsRequest(BaseModel):
    events: list[IngestEvent] = Field(default_factory=list)


class IngestEventsResponse(BaseModel):
    new: list[str]
    new_count: int
    seen_count: int


class SummarizeRequest(BaseModel):
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)


# --- endpoints -------------------------------------------------------------


@monitor_router.post("/ingest-events", response_model=IngestEventsResponse)
async def ingest_events_endpoint(
    body: IngestEventsRequest,
    x_monitor_token: str | None = Header(default=None),
) -> IngestEventsResponse:
    _require_token(x_monitor_token)
    outcome = await ingest_events(body.events)
    return IngestEventsResponse(
        new=outcome.new, new_count=outcome.new_count, seen_count=outcome.seen_count
    )


@monitor_router.post("/summarize")
async def summarize_endpoint(
    body: SummarizeRequest,
    x_monitor_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_token(x_monitor_token)
    async with _summarize_lock:
        outcome = await summarize_event(
            body.source,
            body.external_id,
            router=_get_router(),
            fetch_text=_edgar_text_fetcher,
            budget=_get_budget(),
        )
    return {
        "status": outcome.status,
        "external_id": outcome.external_id,
        "company": outcome.company,
        "event_type": outcome.event_type,
        "source_url": outcome.source_url,
        "summary": outcome.summary,
        "alert": (
            None
            if outcome.alert is None
            else {
                "delivered": outcome.alert.delivered,
                "channel": outcome.alert.channel,
                "detail": outcome.alert.detail,
            }
        ),
    }
