"""Unit tests for the price_enrich tool core (T-033) — no live network, no DB.

The JSON fixture ``tests/fixtures/alphavantage/aapl_recent.json`` carries
genuine Alpha Vantage TIME_SERIES_DAILY values for AAPL (recorded live on
2026-07-12, trimmed to 21 recent trading days). Alpha Vantage's free tier
serves only ``outputsize=compact`` (last ~100 trading days) and returns
HTTP 200 even for rate-limit/error bodies — the parser inspects the JSON
keys, which these tests exercise directly.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from common.config import reset_settings_cache
from tools.enrich import core
from tools.enrich.core import (
    ProviderLimiter,
    parse_alphavantage_json,
    price_enrich,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "alphavantage" / "aapl_recent.json"
FIXTURE_TEXT = FIXTURE.read_text(encoding="utf-8")
# The fixture window (see module docstring).
RANGE_FROM, RANGE_TO = date(2026, 6, 1), date(2026, 7, 12)

RATE_LIMIT_BODY = json.dumps(
    {"Information": "Thank you for using Alpha Vantage! ... 25 requests per day ..."}
)
ERROR_BODY = json.dumps({"Error Message": "Invalid API call. Please retry ..."})


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


def _fixture_points() -> list[tuple[date, float]]:
    parsed = parse_alphavantage_json(FIXTURE_TEXT, RANGE_FROM, RANGE_TO)
    assert parsed is not None
    return parsed


@pytest.fixture(autouse=True)
def _no_retry_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "RETRY_WAIT_BASE_S", 0.0)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    reset_settings_cache()


# --- JSON parsing ------------------------------------------------------------


def test_fixture_json_parses_to_close_series() -> None:
    points = _fixture_points()
    assert len(points) == 21
    assert points[0] == (date(2026, 6, 10), 291.58)
    assert points[-1] == (date(2026, 7, 10), 315.32)
    assert points == sorted(points)  # ascending by date


def test_parse_filters_to_requested_range() -> None:
    # Narrow the window: only rows inside [from, to] survive.
    points = parse_alphavantage_json(FIXTURE_TEXT, date(2026, 7, 1), date(2026, 7, 10))
    assert points is not None
    assert all(date(2026, 7, 1) <= day <= date(2026, 7, 10) for day, _ in points)
    assert len(points) < 21


def test_parse_rate_limit_note_is_retryable_none() -> None:
    assert parse_alphavantage_json(RATE_LIMIT_BODY, RANGE_FROM, RANGE_TO) is None
    assert parse_alphavantage_json("not json", RANGE_FROM, RANGE_TO) is None


def test_parse_error_message_is_empty_series() -> None:
    # Unknown/uncovered symbol -> honest "no rows", not a retry.
    assert parse_alphavantage_json(ERROR_BODY, RANGE_FROM, RANGE_TO) == []


# --- validation --------------------------------------------------------------


async def test_invalid_inputs_become_observation_errors() -> None:
    bad_ticker = await price_enrich("no such ticker", "2026-06-01", "2026-07-10")
    assert "error" in bad_ticker and bad_ticker["retryable"] is False

    bad_date = await price_enrich("AAPL", "january", "2026-07-10")
    assert "ISO date" in bad_date["error"]

    swapped = await price_enrich("AAPL", "2026-07-10", "2026-06-01")
    assert "after" in swapped["error"]

    too_wide = await price_enrich("AAPL", "2000-01-01", "2026-01-01")
    assert "range" in too_wide["error"]


# --- cache path --------------------------------------------------------------


@respx.mock
async def test_cache_hit_returns_cached_without_network() -> None:
    price_rows = [
        (date(2026, 6, 10), 291.58, "USD"),
        (date(2026, 6, 25), 300.00, "USD"),
        (date(2026, 7, 10), 315.32, "USD"),
    ]
    session = FakeSession(company_rows=[(1,)], price_rows=price_rows)
    result = await price_enrich(
        "AAPL",
        "2026-06-10",
        "2026-07-10",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert result["cached"] is True and result["source"] == "db"
    assert [point["date"] for point in result["series"]] == [
        "2026-06-10",
        "2026-06-25",
        "2026-07-10",
    ]
    assert result["series"][0]["close"] == 291.58
    assert result["series"][0]["currency"] == "USD"
    assert session.upserts == []  # nothing to write back


@respx.mock
async def test_partial_cache_is_a_miss_and_refetches() -> None:
    # A two-day cache must not answer the full-month question (edge check).
    price_rows = [(date(2026, 6, 10), 291.58, "USD"), (date(2026, 7, 10), 315.32, "USD")]
    session = FakeSession(company_rows=[(1,)], price_rows=price_rows)
    route = respx.get(url__startswith=core.ALPHAVANTAGE_BASE_URL).mock(
        return_value=httpx.Response(200, text=FIXTURE_TEXT)
    )
    result = await price_enrich(
        "AAPL",
        "2026-01-01",
        "2026-07-10",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert route.called
    assert result["cached"] is False and result["source"] == "alphavantage"


async def test_unknown_ticker_is_observation_error_without_provider_call() -> None:
    session = FakeSession(company_rows=[], price_rows=[])
    with respx.mock:  # no routes: any HTTP call would fail the test
        result = await price_enrich(
            "ZZZZ",
            "2026-06-01",
            "2026-07-10",
            session_factory=_factory(session),
            limiter=_fast_limiter(),
        )
    assert "companies registry" in result["error"]
    assert result["retryable"] is False


# --- provider path -----------------------------------------------------------


@respx.mock
async def test_cache_miss_fetches_parses_and_upserts() -> None:
    session = FakeSession(company_rows=[(7,)], price_rows=[])
    route = respx.get(url__startswith=core.ALPHAVANTAGE_BASE_URL).mock(
        return_value=httpx.Response(200, text=FIXTURE_TEXT)
    )
    result = await price_enrich(
        "aapl",
        "2026-06-01",
        "2026-07-12",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert route.call_count == 1
    request_url = str(route.calls[0].request.url)
    assert "symbol=AAPL" in request_url and "function=TIME_SERIES_DAILY" in request_url
    assert "apikey=test-key" in request_url
    assert result == {
        "series": [
            {"date": point_date.isoformat(), "close": close, "currency": "USD"}
            for point_date, close in _fixture_points()
        ],
        "source": "alphavantage",
        "cached": False,
    }
    assert len(session.upserts) == 1 and len(session.upserts[0]) == 21
    assert session.upserts[0][0]["company_id"] == 7
    assert session.upserts[0][0]["source"] == "alphavantage"
    assert session.commits == 1


@respx.mock
async def test_provider_5xx_becomes_retryable_observation() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    route = respx.get(url__startswith=core.ALPHAVANTAGE_BASE_URL).mock(
        return_value=httpx.Response(503)
    )
    result = await price_enrich(
        "AAPL",
        "2026-06-01",
        "2026-07-10",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert route.call_count == core.RETRY_ATTEMPTS
    assert result["retryable"] is True and "503" in result["error"]


@respx.mock
async def test_rate_limit_body_becomes_retryable_observation() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    respx.get(url__startswith=core.ALPHAVANTAGE_BASE_URL).mock(
        return_value=httpx.Response(200, text=RATE_LIMIT_BODY)
    )
    result = await price_enrich(
        "AAPL",
        "2026-06-01",
        "2026-07-10",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert result["retryable"] is True and "rate limit" in result["error"]


@respx.mock
async def test_error_message_body_is_honest_empty_series() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    respx.get(url__startswith=core.ALPHAVANTAGE_BASE_URL).mock(
        return_value=httpx.Response(200, text=ERROR_BODY)
    )
    result = await price_enrich(
        "AAPL",
        "2026-06-01",
        "2026-07-10",
        session_factory=_factory(session),
        limiter=_fast_limiter(),
    )
    assert result["series"] == [] and result["cached"] is False
    assert "message" in result
    assert session.upserts == []


async def test_missing_api_key_is_observation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    # Empty env value overrides the .env file (env has priority in
    # pydantic-settings), so the key reads as unset without a network call.
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "")
    reset_settings_cache()
    with respx.mock:
        result = await price_enrich(
            "AAPL",
            "2026-06-01",
            "2026-07-10",
            session_factory=_factory(session),
            limiter=_fast_limiter(),
        )
    assert "not configured" in result["error"] and result["retryable"] is False


# --- rate limiting -----------------------------------------------------------


async def test_daily_limit_reached_is_observation_error() -> None:
    session = FakeSession(company_rows=[(1,)], price_rows=[])
    with respx.mock:  # exhausted budget must not touch the network
        result = await price_enrich(
            "AAPL",
            "2026-06-01",
            "2026-07-10",
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
