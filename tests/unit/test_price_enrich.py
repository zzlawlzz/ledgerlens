"""Unit tests for the price_enrich tool core (T-033) — no live network, no DB.

The CSV fixture ``tests/fixtures/stooq/aapl_2024_jan.csv`` carries genuine
Stooq values for AAPL Jan 2024, live-verified on 2026-07-11 via the Stooq
historical-data page (the raw ``/q/d/l/`` endpoint answers non-browser
clients with a JavaScript bot-verification page on this network — see the
challenge-response test below, which encodes exactly that live behaviour).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from tools.enrich import core
from tools.enrich.core import (
    ProviderLimiter,
    parse_stooq_csv,
    price_enrich,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "stooq" / "aapl_2024_jan.csv"

STOOQ_CHALLENGE_HTML = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
    "<noscript>This site requires JavaScript to verify your browser.</noscript>"
    "<script>/* sha-256 proof-of-work */</script></body></html>"
)


# --- fakes -------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeSession:
    """Keys queries off SQL text; records upsert parameter lists."""

    def __init__(
        self,
        company_rows: list[tuple[Any, ...]],
        price_rows: list[tuple[Any, ...]],
    ) -> None:
        self.company_rows = company_rows
        self.price_rows = price_rows
        self.upserts: list[list[dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        sql = str(statement)
        if "FROM companies" in sql:
            return FakeResult(self.company_rows)
        if "FROM prices" in sql:
            return FakeResult(self.price_rows)
        if "INSERT INTO prices" in sql:
            self.upserts.append(list(params or []))
            return FakeResult([])
        raise AssertionError(f"unexpected SQL in test: {sql}")

    async def commit(self) -> None:
        self.commits += 1

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


def _factory(session: FakeSession) -> Any:
    return lambda: session


def _fast_limiter() -> ProviderLimiter:
    return ProviderLimiter(min_interval_s=0.0, daily_limit=10_000)


@pytest.fixture(autouse=True)
def _no_retry_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "RETRY_WAIT_BASE_S", 0.0)


# --- CSV parsing -------------------------------------------------------------


def test_fixture_csv_parses_to_close_series() -> None:
    points = parse_stooq_csv(FIXTURE.read_text(encoding="utf-8"))
    assert points is not None and len(points) == 21
    assert points[0] == (date(2024, 1, 2), 183.731)
    assert points[-1] == (date(2024, 1, 31), 182.503)


def test_parse_skips_malformed_rows_and_handles_no_data() -> None:
    csv_text = "Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,3,4.5,100\nbroken,line\n"
    points = parse_stooq_csv(csv_text)
    assert points == [(date(2024, 1, 2), 4.5)]
    assert parse_stooq_csv("No data") == []


def test_parse_returns_none_for_html_challenge_page() -> None:
    # Live finding (2026-07-11): stooq serves a JS bot-check to non-browsers.
    assert parse_stooq_csv(STOOQ_CHALLENGE_HTML) is None
    assert parse_stooq_csv("") is None


# --- validation --------------------------------------------------------------


async def test_invalid_inputs_become_observation_errors() -> None:
    bad_ticker = await price_enrich("no such ticker", "2024-01-01", "2024-01-31")
    assert "error" in bad_ticker and bad_ticker["retryable"] is False

    bad_date = await price_enrich("AAPL", "january", "2024-01-31")
    assert "ISO date" in bad_date["error"]

    swapped = await price_enrich("AAPL", "2024-02-01", "2024-01-01")
    assert "after" in swapped["error"]

    too_wide = await price_enrich("AAPL", "2000-01-01", "2024-01-01")
    assert "range" in too_wide["error"]


# --- cache path --------------------------------------------------------------


@respx.mock
async def test_cache_hit_returns_cached_without_network() -> None:
    price_rows = [
        (date(2024, 1, 2), 183.731, "USD"),
        (date(2024, 1, 16), 181.742, "USD"),
        (date(2024, 1, 31), 182.503, "USD"),
    ]
    session = FakeSession(company_rows=[(1,)], price_rows=price_rows)
    result = await price_enrich(
        "AAPL",
        "2024-01-01",
        "2024-01-31",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert result["cached"] is True and result["source"] == "db"
    assert [point["date"] for point in result["series"]] == [
        "2024-01-02",
        "2024-01-16",
        "2024-01-31",
    ]
    assert result["series"][0]["close"] == 183.731
    assert result["series"][0]["currency"] == "USD"
    assert session.upserts == []  # nothing to write back


@respx.mock
async def test_partial_cache_is_a_miss_and_refetches() -> None:
    # January-only cache must not answer a full-year question (edge check).
    price_rows = [(date(2024, 1, 2), 183.731, "USD"), (date(2024, 1, 31), 182.503, "USD")]
    session = FakeSession(company_rows=[(1,)], price_rows=price_rows)
    route = respx.get(url__startswith=core.STOOQ_BASE_URL).mock(
        return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
    )
    result = await price_enrich(
        "AAPL",
        "2024-01-01",
        "2024-12-31",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert route.called
    assert result["cached"] is False and result["source"] == "stooq"


async def test_unknown_ticker_is_observation_error_without_provider_call() -> None:
    session = FakeSession(company_rows=[], price_rows=[])
    with respx.mock:  # no routes: any HTTP call would fail the test
        result = await price_enrich(
            "ZZZZ",
            "2024-01-01",
            "2024-01-31",
            session_factory=_factory(session),
            limiter=_fast_limiter(),
        )
    assert "companies registry" in result["error"]
    assert result["retryable"] is False


# --- provider path -----------------------------------------------------------


@respx.mock
async def test_cache_miss_fetches_parses_and_upserts() -> None:
    session = FakeSession(company_rows=[(7,)], price_rows=[])
    route = respx.get(url__startswith=core.STOOQ_BASE_URL).mock(
        return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
    )
    result = await price_enrich(
        "aapl",
        "2024-01-01",
        "2024-01-31",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert route.call_count == 1
    request_url = str(route.calls[0].request.url)
    assert "s=aapl.us" in request_url and "d1=20240101" in request_url
    assert result == {
        "series": [
            {"date": point_date.isoformat(), "close": close, "currency": "USD"}
            for point_date, close in parse_stooq_csv(FIXTURE.read_text(encoding="utf-8")) or []
        ],
        "source": "stooq",
        "cached": False,
    }
    assert len(session.upserts) == 1 and len(session.upserts[0]) == 21
    assert session.upserts[0][0]["company_id"] == 7
    assert session.upserts[0][0]["source"] == "stooq"
    assert session.commits == 1


@respx.mock
async def test_provider_5xx_becomes_retryable_observation() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    route = respx.get(url__startswith=core.STOOQ_BASE_URL).mock(return_value=httpx.Response(503))
    result = await price_enrich(
        "AAPL",
        "2024-01-01",
        "2024-01-31",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert route.call_count == core.RETRY_ATTEMPTS
    assert result["retryable"] is True and "503" in result["error"]


@respx.mock
async def test_challenge_page_becomes_retryable_observation() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    respx.get(url__startswith=core.STOOQ_BASE_URL).mock(
        return_value=httpx.Response(200, text=STOOQ_CHALLENGE_HTML)
    )
    result = await price_enrich(
        "AAPL",
        "2024-01-01",
        "2024-01-31",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert result["retryable"] is True and "non-CSV" in result["error"]


@respx.mock
async def test_empty_provider_answer_is_honest_empty_series() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    respx.get(url__startswith=core.STOOQ_BASE_URL).mock(
        return_value=httpx.Response(200, text="No data")
    )
    result = await price_enrich(
        "AAPL",
        "2024-01-01",
        "2024-01-31",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert result["series"] == [] and result["cached"] is False
    assert "message" in result
    assert session.upserts == []


# --- rate limiting -----------------------------------------------------------


async def test_daily_limit_reached_is_observation_error() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    with respx.mock:  # exhausted budget must not touch the network
        result = await price_enrich(
            "AAPL",
            "2024-01-01",
            "2024-01-31",
            session_factory=_factory(session),
            limiter=ProviderLimiter(min_interval_s=0.0, daily_limit=0),
        )
    assert "daily provider limit reached" in result["error"]
    assert result["retryable"] is False


async def test_limiter_daily_counter_and_utc_rollover(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_utc_today", lambda: date(2026, 7, 11))
    limiter = ProviderLimiter(min_interval_s=0.0, daily_limit=2)
    assert await limiter.acquire() is None
    assert await limiter.acquire() is None
    message = await limiter.acquire()
    assert message is not None and "daily provider limit reached" in message
    # A new UTC day resets the counter.
    monkeypatch.setattr(core, "_utc_today", lambda: date(2026, 7, 12))
    assert await limiter.acquire() is None


async def test_limiter_paces_one_request_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([100.0, 100.1])
    sleeps: list[float] = []

    async def _record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(core, "_loop_time", lambda: next(clock))
    monkeypatch.setattr(core, "_sleep", _record_sleep)
    limiter = ProviderLimiter(min_interval_s=1.0, daily_limit=10)
    assert await limiter.acquire() is None  # first call goes straight through
    assert sleeps == []
    assert await limiter.acquire() is None  # 0.1s later: must wait the rest
    assert sleeps == [pytest.approx(0.9)]
