"""EDGAR HTTP client (T-008): polite fair-use access to SEC endpoints.

Behaviours required by ARCHITECTURE.md §5.1 / CONTRACTS.md §5:
- mandatory ``User-Agent`` from ``EDGAR_USER_AGENT`` (SEC policy, Q-11);
- global async rate limiter: >=100 ms between requests;
- disk cache under ``data/cache/edgar`` (URL-hash key, per-endpoint TTL:
  Archives documents never expire, companyfacts/tickers refresh daily,
  submissions hourly);
- tenacity retries on 5xx/429/network errors; other 4xx are never retried;
- optional egress proxy ``EDGAR_PROXY_URL`` applied ONLY to ``*.sec.gov``
  hosts; a proxy failure raises a clear error — never a silent direct retry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
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

from common.config import PROJECT_ROOT, Settings, get_settings
from common.errors import ConfigError, SourceUnavailableError, ToolError

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{filename}"

TTL_SUBMISSIONS_S = 3600.0  # the filings list gains new entries daily
TTL_COMPANYFACTS_S = 24 * 3600.0  # closed periods are immutable; refresh daily
TTL_TICKERS_S = 24 * 3600.0
TTL_FOREVER: float | None = None  # Archives documents never change

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "edgar"


class _RetryableStatusError(Exception):
    """5xx/429 from SEC — worth retrying with backoff."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"EDGAR returned {status_code} for {url}")
        self.status_code = status_code


class RateLimiter:
    """Spaces request start times at least ``min_interval_s`` apart (global)."""

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            delay = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._min_interval_s
        if delay > 0:
            await asyncio.sleep(delay)


class DiskCache:
    """Body + sidecar meta file per URL; ``ttl_s=None`` means never expires."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._root / f"{key}.bin", self._root / f"{key}.meta.json"

    def get(self, url: str) -> bytes | None:
        body_path, meta_path = self._paths(url)
        if not (body_path.is_file() and meta_path.is_file()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        ttl_s = meta.get("ttl_s")
        if ttl_s is not None and time.time() - float(meta.get("fetched_at", 0)) > float(ttl_s):
            return None
        try:
            return body_path.read_bytes()
        except OSError:
            return None

    def put(self, url: str, content: bytes, ttl_s: float | None) -> None:
        body_path, meta_path = self._paths(url)
        body_path.write_bytes(content)
        meta_path.write_text(
            json.dumps({"url": url, "fetched_at": time.time(), "ttl_s": ttl_s}),
            encoding="utf-8",
        )


def _is_sec_host(url: str) -> bool:
    host = httpx.URL(url).host
    return host == "sec.gov" or host.endswith(".sec.gov")


class EdgarClient:
    """Cached, rate-limited, retrying access to SEC EDGAR."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache_dir: Path | None = None,
        min_interval_s: float = 0.1,
        retry_attempts: int = 3,
        retry_wait_base_s: float = 0.5,
        timeout_s: float = 60.0,  # Archives 10-Ks reach ~12MB — generous read timeout
        direct_transport: httpx.AsyncBaseTransport | None = None,
        sec_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = settings or get_settings()
        if not settings.edgar_user_agent:
            raise ConfigError(
                "EDGAR_USER_AGENT is required to talk to SEC EDGAR "
                "(fair-use policy; CONTRACTS.md §5, Q-11)"
            )
        headers = {"User-Agent": settings.edgar_user_agent}
        timeout = httpx.Timeout(timeout_s, connect=10.0)
        self._proxy_url = settings.edgar_proxy_url or None
        self._direct = httpx.AsyncClient(
            headers=headers, timeout=timeout, transport=direct_transport
        )
        if sec_transport is not None:
            self._sec = httpx.AsyncClient(headers=headers, timeout=timeout, transport=sec_transport)
        elif self._proxy_url:
            # Proxy applies ONLY to *.sec.gov (see _client_for); other hosts stay direct.
            self._sec = httpx.AsyncClient(headers=headers, timeout=timeout, proxy=self._proxy_url)
        else:
            self._sec = self._direct
        self._cache = DiskCache(cache_dir or DEFAULT_CACHE_DIR)
        self._rate_limiter = RateLimiter(min_interval_s)
        self._retry_attempts = retry_attempts
        self._retry_wait_base_s = retry_wait_base_s

    # --- transport ---------------------------------------------------------

    def _client_for(self, url: str) -> httpx.AsyncClient:
        return self._sec if _is_sec_host(url) else self._direct

    async def _fetch_once(self, url: str) -> bytes:
        await self._rate_limiter.wait()
        try:
            response = await self._client_for(url).get(url)
        except httpx.ProxyError as exc:
            raise SourceUnavailableError(
                f"EDGAR egress proxy failed (EDGAR_PROXY_URL={self._proxy_url}): {exc}; "
                "refusing to fall back to a direct connection",
                retryable=False,
            ) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise _RetryableStatusError(response.status_code, url)
        if response.status_code >= 400:
            raise ToolError(f"EDGAR returned {response.status_code} for {url}", retryable=False)
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
                f"EDGAR network error for {url} after {self._retry_attempts} attempts: {exc}"
            ) from exc
        self._cache.put(url, content, ttl_s)
        return content

    async def _get_json(self, url: str, ttl_s: float | None) -> dict[str, Any]:
        data = json.loads(await self._get(url, ttl_s))
        if not isinstance(data, dict):
            raise ToolError(f"EDGAR returned non-object JSON for {url}", retryable=False)
        return data

    # --- public API ---------------------------------------------------------

    async def get_company_tickers(self) -> dict[str, Any]:
        """www.sec.gov/files/company_tickers.json — ticker -> CIK mapping."""
        return await self._get_json(COMPANY_TICKERS_URL, TTL_TICKERS_S)

    async def get_submissions(self, cik: int | str) -> dict[str, Any]:
        """Company filings index (data.sec.gov/submissions)."""
        return await self._get_json(SUBMISSIONS_URL.format(cik=int(cik)), TTL_SUBMISSIONS_S)

    async def get_companyfacts(self, cik: int | str) -> dict[str, Any]:
        """All XBRL facts of a company (data.sec.gov/api/xbrl/companyfacts)."""
        return await self._get_json(COMPANYFACTS_URL.format(cik=int(cik)), TTL_COMPANYFACTS_S)

    async def get_archive_document(self, cik: int | str, accession: str, filename: str) -> bytes:
        """One document from the EDGAR Archives (immutable — cached forever)."""
        url = ARCHIVES_URL.format(
            cik=int(cik), accession=accession.replace("-", ""), filename=filename
        )
        return await self._get(url, TTL_FOREVER)

    # --- lifecycle -----------------------------------------------------------

    async def aclose(self) -> None:
        await self._direct.aclose()
        if self._sec is not self._direct:
            await self._sec.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
