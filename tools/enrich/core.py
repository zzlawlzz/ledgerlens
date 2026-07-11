"""price_enrich tool core (T-033; CONTRACTS.md §9).

End-of-day close prices for one ticker: the ``prices`` table is checked
first (natural durable cache), on a miss the Stooq EOD CSV endpoint (no API
key) is fetched with retries and the rows are upserted back into ``prices``.
Errors come back as *observations* (``{error, retryable}``), never as
exceptions — the analysis degrades honestly instead of crashing the run
(same philosophy as tools/sql/core.py).

Free-tier discipline (BACKLOG T-033): a process-local ``ProviderLimiter``
paces external requests (min interval between calls) and caps them per UTC
day (``config/enrich.yaml``, default 500). A spent daily budget is an
observation error, not a crash; cached ranges keep working.

Live note (2026-07-11): stooq.com fronts ``/q/d/l/`` with a JavaScript
bot-verification page for non-browser clients on some networks. Such a
response is not CSV and is reported as a retryable provider-unavailable
observation — see ``parse_stooq_csv`` returning ``None``.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from common.config import load_yaml_config
from common.db import get_session_factory
from common.errors import ConfigError, SourceUnavailableError, ToolError
from common.logging import get_logger

STOOQ_BASE_URL = "https://stooq.com/q/d/l/"
SOURCE_NAME = "stooq"
CURRENCY = "USD"  # Stooq *.us symbols quote in USD (EOD only per contract)
USER_AGENT = "ledgerlens/0.1"

DEFAULT_MIN_INTERVAL_S = 1.0
DEFAULT_DAILY_LIMIT = 500
DEFAULT_TIMEOUT_S = 10.0
RETRY_ATTEMPTS = 3
RETRY_WAIT_BASE_S = 0.5

# Token budget guard: ~5 years of trading days is already a ~2000-point
# observation; wider ranges belong to aggregate SQL, not a raw EOD series.
MAX_RANGE_DAYS = 1900

# Cache-completeness tolerance at the range edges: requested calendar bounds
# rarely fall on trading days (weekends, exchange holidays, year boundaries),
# so cached rows starting/ending within this many days of the requested
# bounds count as full coverage and skip the provider call.
EDGE_TOLERANCE_DAYS = 7

_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,11}$")

_COMPANY_SQL = text("SELECT id FROM companies WHERE ticker = :ticker ORDER BY id LIMIT 1")
_CACHE_SQL = text(
    "SELECT trade_date, close, currency FROM prices "
    "WHERE company_id = :company_id AND source = :source "
    "AND trade_date >= :date_from AND trade_date <= :date_to "
    "ORDER BY trade_date"
)
_UPSERT_SQL = text(
    "INSERT INTO prices (company_id, trade_date, close, currency, source) "
    "VALUES (:company_id, :trade_date, :close, :currency, :source) "
    "ON CONFLICT (company_id, trade_date, source) "
    "DO UPDATE SET close = EXCLUDED.close, currency = EXCLUDED.currency"
)

# Monkeypatchable seams for hermetic limiter tests.
_sleep = asyncio.sleep


def _loop_time() -> float:
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:  # pragma: no cover — limiter is only used from async code
        return time.monotonic()


def _utc_today() -> date:
    return datetime.now(UTC).date()


class _RetryableStatusError(Exception):
    """5xx/429 from the provider — worth retrying with backoff."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"price provider returned {status_code}")
        self.status_code = status_code


class ProviderLimiter:
    """Paces external calls (min interval) and caps them per UTC day.

    In-process state only: the durable cache is the ``prices`` table itself.
    ``acquire`` returns ``None`` when a request may proceed (after sleeping
    out the pacing interval) or a human-readable error message when the
    daily budget is spent — the caller turns it into an observation error.
    """

    def __init__(self, min_interval_s: float, daily_limit: int) -> None:
        self._min_interval_s = min_interval_s
        self._daily_limit = daily_limit
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._day: date | None = None
        self._count = 0

    @property
    def requests_today(self) -> int:
        return self._count if self._day == _utc_today() else 0

    async def acquire(self) -> str | None:
        async with self._lock:
            today = _utc_today()
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self._daily_limit:
                return (
                    f"daily provider limit reached ({self._daily_limit} requests/day); "
                    "already-cached price ranges remain available"
                )
            self._count += 1
            now = _loop_time()
            delay = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._min_interval_s
        if delay > 0:
            await _sleep(delay)
        return None


_LIMITER: ProviderLimiter | None = None


def _provider_config() -> dict[str, Any]:
    try:
        raw = load_yaml_config("enrich").get("provider", {})
    except ConfigError:  # config file missing: fall back to safe defaults
        raw = {}
    return raw if isinstance(raw, dict) else {}


def get_limiter() -> ProviderLimiter:
    """Process-wide limiter configured from config/enrich.yaml."""
    global _LIMITER
    if _LIMITER is None:
        config = _provider_config()
        _LIMITER = ProviderLimiter(
            min_interval_s=float(config.get("min_interval_s", DEFAULT_MIN_INTERVAL_S)),
            daily_limit=int(config.get("daily_request_limit", DEFAULT_DAILY_LIMIT)),
        )
    return _LIMITER


def reset_limiter() -> None:
    """Drop the cached limiter (tests)."""
    global _LIMITER
    _LIMITER = None


def _validate_date(value: Any, name: str) -> str | None:
    """Same teaching style as tools/rag/core.py date validation."""
    if value in (None, ""):
        return f"{name} is required (ISO date, YYYY-MM-DD)"
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}"
    return None


def parse_stooq_csv(payload: str) -> list[tuple[date, float]] | None:
    """Stooq EOD CSV -> [(date, close)]; ``None`` when the payload is not CSV.

    ``None`` covers HTML bot-verification pages and other unexpected bodies
    (retryable provider problem); an empty list is a *valid* answer meaning
    the provider knows no rows for this symbol/range.
    """
    stripped = payload.strip()
    if not stripped or stripped.startswith("<"):
        return None
    lines = stripped.splitlines()
    header = [column.strip() for column in lines[0].split(",")]
    if "Close" not in header or header[0] != "Date":
        return [] if "no data" in lines[0].lower() else None
    close_index = header.index("Close")
    points: list[tuple[date, float]] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= close_index:
            continue
        try:
            points.append((date.fromisoformat(parts[0].strip()), float(parts[close_index])))
        except ValueError:
            continue
    return points


async def _fetch_stooq_csv(
    ticker: str, date_from: date, date_to: date, limiter: ProviderLimiter
) -> str:
    """One provider download with pacing, daily cap and retries.

    Raises ``ToolError``/``SourceUnavailableError`` — the caller converts
    them into observation errors. Every retry attempt consumes one unit of
    the daily budget (it is a real external request).
    """
    config = _provider_config()
    base_url = str(config.get("base_url", STOOQ_BASE_URL))
    timeout_s = float(config.get("timeout_s", DEFAULT_TIMEOUT_S))
    params = {
        "s": f"{ticker.lower()}.us",
        "d1": date_from.strftime("%Y%m%d"),
        "d2": date_to.strftime("%Y%m%d"),
        "i": "d",
    }

    async def _once() -> str:
        limit_message = await limiter.acquire()
        if limit_message is not None:
            raise ToolError(limit_message, retryable=False)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=timeout_s),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(base_url, params=params)
        if response.status_code >= 500 or response.status_code == 429:
            raise _RetryableStatusError(response.status_code)
        if response.status_code >= 400:
            raise ToolError(f"price provider returned HTTP {response.status_code}", retryable=False)
        return response.text

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=RETRY_WAIT_BASE_S, max=8),
            retry=retry_if_exception_type((httpx.TransportError, _RetryableStatusError)),
            reraise=True,
        ):
            with attempt:
                return await _once()
    except _RetryableStatusError as exc:
        raise SourceUnavailableError(
            f"{exc} — still failing after {RETRY_ATTEMPTS} attempts"
        ) from exc
    except httpx.TransportError as exc:
        raise SourceUnavailableError(
            f"price provider unreachable after {RETRY_ATTEMPTS} attempts: {exc}"
        ) from exc
    raise SourceUnavailableError("price provider retry loop exhausted")  # pragma: no cover


def _serialize(points: list[tuple[date, float]]) -> list[dict[str, Any]]:
    return [
        {"date": point_date.isoformat(), "close": close, "currency": CURRENCY}
        for point_date, close in points
    ]


def _covers_range(points: list[tuple[date, float]], date_from: date, date_to: date) -> bool:
    """Cached rows count as a hit when they reach both requested edges.

    Presence of *any* rows is not enough: a January-only cache must not
    answer a full-year question. Trading calendars never start exactly on
    the requested calendar bound, hence the edge tolerance.
    """
    if not points:
        return False
    first, last = points[0][0], points[-1][0]
    return (first - date_from).days <= EDGE_TOLERANCE_DAYS and (
        date_to - last
    ).days <= EDGE_TOLERANCE_DAYS


def _error_observation(error: str, retryable: bool, hint: str | None = None) -> dict[str, Any]:
    observation: dict[str, Any] = {"error": error, "retryable": retryable}
    if hint:
        observation["hint"] = hint
    return observation


async def price_enrich(
    ticker: str,
    date_from: str,
    date_to: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    limiter: ProviderLimiter | None = None,
) -> dict[str, Any]:
    """EOD close series per CONTRACTS.md §9: cache (``prices``) then Stooq.

    Returns ``{"series": [{date, close, currency}], "source": "db"|"stooq",
    "cached": bool}`` or an observation error ``{"error", "retryable"}``.
    """
    log = get_logger(node="price_enrich")
    if not isinstance(ticker, str) or not _TICKER_RE.match(ticker.strip()):
        return _error_observation(
            f"invalid ticker {ticker!r}",
            retryable=False,
            hint="Pass one exchange ticker like 'AAPL' (letters/digits/dots, no spaces).",
        )
    normalized_ticker = ticker.strip().upper()
    for field_name, value in (("date_from", date_from), ("date_to", date_to)):
        problem = _validate_date(value, field_name)
        if problem:
            return _error_observation(problem, retryable=False)
    parsed_from = date.fromisoformat(str(date_from))
    parsed_to = date.fromisoformat(str(date_to))
    if parsed_from > parsed_to:
        return _error_observation(
            f"date_from {parsed_from} is after date_to {parsed_to}", retryable=False
        )
    if (parsed_to - parsed_from).days > MAX_RANGE_DAYS:
        return _error_observation(
            f"date range wider than {MAX_RANGE_DAYS} days",
            retryable=False,
            hint="Ask for at most ~5 years of daily prices per call.",
        )

    factory = session_factory or get_session_factory()
    company_id: int | None = None
    cached_points: list[tuple[date, float]] = []
    try:
        async with factory() as session:
            row = (await session.execute(_COMPANY_SQL, {"ticker": normalized_ticker})).first()
            if row is None:
                return _error_observation(
                    f"ticker '{normalized_ticker}' is not in the companies registry",
                    retryable=False,
                    hint=(
                        "Price data is only tracked for loaded companies; check available "
                        "tickers with sql_query (SELECT DISTINCT ticker FROM companies)."
                    ),
                )
            company_id = int(row[0])
            cache_rows = (
                await session.execute(
                    _CACHE_SQL,
                    {
                        "company_id": company_id,
                        "source": SOURCE_NAME,
                        "date_from": parsed_from,
                        "date_to": parsed_to,
                    },
                )
            ).fetchall()
            cached_points = [
                (trade_date, float(close) if isinstance(close, Decimal) else float(close))
                for trade_date, close, _currency in cache_rows
            ]
    except (SQLAlchemyError, OSError) as error:
        # DB trouble must not take price context down with it: serve the
        # provider data uncached (and skip the upsert below).
        log.warning("price_cache_unavailable", error=str(error)[:200])
        company_id = None
        cached_points = []

    if _covers_range(cached_points, parsed_from, parsed_to):
        return {"series": _serialize(cached_points), "source": "db", "cached": True}

    try:
        payload = await _fetch_stooq_csv(
            normalized_ticker, parsed_from, parsed_to, limiter or get_limiter()
        )
    except ToolError as error:
        return _error_observation(str(error), retryable=error.retryable, hint=error.hint)

    points = parse_stooq_csv(payload)
    if points is None:
        return _error_observation(
            "price provider returned an unexpected non-CSV response "
            "(possibly a bot-verification page)",
            retryable=True,
            hint="Price data is unavailable right now; continue the analysis without it.",
        )
    if not points:
        return {
            "series": [],
            "source": SOURCE_NAME,
            "cached": False,
            "message": "provider has no price rows for this ticker/range",
        }
    points.sort(key=lambda point: point[0])

    if company_id is not None:
        try:
            async with factory() as session:
                await session.execute(
                    _UPSERT_SQL,
                    [
                        {
                            "company_id": company_id,
                            "trade_date": point_date,
                            "close": close,
                            "currency": CURRENCY,
                            "source": SOURCE_NAME,
                        }
                        for point_date, close in points
                    ],
                )
                await session.commit()
        except (SQLAlchemyError, OSError, DBAPIError) as error:
            # A failed cache write only costs a future provider call.
            log.warning("price_cache_write_failed", error=str(error)[:200])

    return {"series": _serialize(points), "source": SOURCE_NAME, "cached": False}
