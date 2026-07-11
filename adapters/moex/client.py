"""MOEX ISS HTTP client (T-032): cached, rate-limited access to iss.moex.com.

ISS answers with JSON blocks shaped ``{"columns": [...], "data": [[...], ...]}``;
``parse_block`` turns a block into row dicts. Behaviours mirror the EDGAR
client (ARCHITECTURE.md §5.3, §5.6):

- disk cache under ``data/cache/moex`` (URL-hash key, per-endpoint TTL:
  history windows of closed trading days never expire, board snapshots
  refresh hourly);
- global async rate limiter: >=100 ms between request starts (delayed data
  is free and unmetered, but bulk paging should stay polite);
- tenacity retries with backoff on 5xx/429/network errors — access from
  foreign IPs is known to be unstable (§5.6); other 4xx raise ``ToolError``
  and are never retried.

No API key or registration is required for delayed data. License: ISS data
is provided for informational use only (README, data sources section).

``DiskCache`` and ``RateLimiter`` are reused from the EDGAR client: they are
source-agnostic plumbing. If a third adapter needs them, move both to a
shared ``adapters`` helper module in a dedicated refactoring change.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from adapters.edgar.client import DiskCache, RateLimiter
from common.config import PROJECT_ROOT
from common.errors import SourceUnavailableError, ToolError

BASE_URL = "https://iss.moex.com"
BOARD_SECURITIES_URL = BASE_URL + "/iss/engines/stock/markets/shares/boards/{board}/securities.json"
HISTORY_URL = (
    BASE_URL + "/iss/history/engines/stock/markets/shares/boards/{board}/securities/{secid}.json"
)
DEFAULT_BOARD = "TQBR"  # T+ main board: shares and depositary receipts

TTL_BOARD_S = 3600.0  # marketdata (last price, capitalization) moves intraday
TTL_HISTORY_OPEN_S = 3600.0  # a window touching today still gains rows
TTL_FOREVER: float | None = None  # closed trading days are immutable

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "moex"
USER_AGENT = "ledgerlens/0.1 (MOEX ISS adapter, T-032)"


def parse_block(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """One ISS block ``{"columns": [...], "data": [[...], ...]}`` as row dicts."""
    block = payload.get(name)
    if not isinstance(block, dict):
        return []
    columns = block.get("columns") or []
    rows = block.get("data") or []
    try:
        return [dict(zip(columns, row, strict=True)) for row in rows]
    except ValueError as exc:
        raise ToolError(
            f"ISS block {name!r}: row width does not match its columns: {exc}", retryable=False
        ) from exc


class _RetryableStatusError(Exception):
    """5xx/429 from ISS — worth retrying with backoff."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"MOEX ISS returned {status_code} for {url}")
        self.status_code = status_code


class MoexClient:
    """Cached, rate-limited, retrying access to MOEX ISS (stock shares board)."""

    def __init__(
        self,
        *,
        board: str = DEFAULT_BOARD,
        cache_dir: Path | None = None,
        min_interval_s: float = 0.1,
        retry_attempts: int = 3,
        retry_wait_base_s: float = 0.5,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._board = board
        self._http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            transport=transport,
        )
        self._cache = DiskCache(cache_dir or DEFAULT_CACHE_DIR)
        self._rate_limiter = RateLimiter(min_interval_s)
        self._retry_attempts = retry_attempts
        self._retry_wait_base_s = retry_wait_base_s

    @property
    def board(self) -> str:
        return self._board

    # --- transport ----------------------------------------------------------

    async def _fetch_once(self, url: str) -> bytes:
        await self._rate_limiter.wait()
        response = await self._http.get(url)
        if response.status_code >= 500 or response.status_code == 429:
            raise _RetryableStatusError(response.status_code, url)
        if response.status_code >= 400:
            raise ToolError(f"MOEX ISS returned {response.status_code} for {url}", retryable=False)
        return response.content

    async def _get(self, url: str, ttl_s: float | None) -> bytes:
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        content = b""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._retry_attempts),
                wait=wait_exponential(multiplier=self._retry_wait_base_s, max=8),
                retry=retry_if_exception_type((httpx.TransportError, _RetryableStatusError)),
                reraise=True,
            ):
                with attempt:
                    content = await self._fetch_once(url)
        except _RetryableStatusError as exc:
            raise SourceUnavailableError(
                f"{exc} — still failing after {self._retry_attempts} attempts"
            ) from exc
        except httpx.TransportError as exc:
            raise SourceUnavailableError(
                f"MOEX ISS network error for {url} after {self._retry_attempts} attempts: {exc}"
            ) from exc
        self._cache.put(url, content, ttl_s)
        return content

    async def _get_json(self, url: str, ttl_s: float | None) -> dict[str, Any]:
        data = json.loads(await self._get(url, ttl_s))
        if not isinstance(data, dict):
            raise ToolError(f"MOEX ISS returned non-object JSON for {url}", retryable=False)
        return data

    # --- public API -----------------------------------------------------------

    async def get_board_securities(self) -> dict[str, Any]:
        """Board snapshot: ``securities`` + ``marketdata`` + ``dataversion`` blocks."""
        url = BOARD_SECURITIES_URL.format(board=self._board) + "?iss.meta=off"
        return await self._get_json(url, TTL_BOARD_S)

    async def get_history_page(
        self, secid: str, date_from: date, date_till: date, *, start: int = 0
    ) -> dict[str, Any]:
        """One page (<=100 rows) of daily trading results for ``secid``."""
        url = (
            HISTORY_URL.format(board=self._board, secid=secid)
            + f"?iss.meta=off&from={date_from.isoformat()}&till={date_till.isoformat()}"
            + f"&start={start}"
        )
        ttl_s = TTL_FOREVER if date_till < date.today() else TTL_HISTORY_OPEN_S
        return await self._get_json(url, ttl_s)

    async def get_history(
        self, secid: str, date_from: date, date_till: date
    ) -> list[dict[str, Any]]:
        """All daily rows in the window, following the ``history.cursor`` pages."""
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = await self.get_history_page(secid, date_from, date_till, start=start)
            page = parse_block(payload, "history")
            rows.extend(page)
            cursor = parse_block(payload, "history.cursor")
            if not cursor or not page:
                break
            total = int(cursor[0].get("TOTAL") or 0)
            page_size = int(cursor[0].get("PAGESIZE") or len(page))
            start = int(cursor[0].get("INDEX") or start) + page_size
            if start >= total:
                break
        return rows

    # --- lifecycle ------------------------------------------------------------

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
