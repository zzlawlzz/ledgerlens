"""Unit tests for the EDGAR HTTP client (T-008) — respx, no live network."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic_settings import SettingsConfigDict

from adapters.edgar.client import (
    COMPANYFACTS_URL,
    SUBMISSIONS_URL,
    EdgarClient,
)
from common.config import Settings
from common.errors import ConfigError, SourceUnavailableError, ToolError


class _NoEnvFileSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def make_settings(**overrides: Any) -> Settings:
    overrides.setdefault("edgar_user_agent", "ledgerlens test@example.com")
    return _NoEnvFileSettings(**overrides)


def make_client(tmp_path: Path, **kwargs: Any) -> EdgarClient:
    kwargs.setdefault("settings", make_settings())
    kwargs.setdefault("cache_dir", tmp_path)
    kwargs.setdefault("min_interval_s", 0.0)
    kwargs.setdefault("retry_wait_base_s", 0.01)
    return EdgarClient(**kwargs)


def test_missing_user_agent_is_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    with pytest.raises(ConfigError, match="EDGAR_USER_AGENT"):
        EdgarClient(settings=make_settings(edgar_user_agent=""), cache_dir=tmp_path)


@respx.mock
async def test_user_agent_header_is_sent(tmp_path: Path) -> None:
    route = respx.get(SUBMISSIONS_URL.format(cik=1)).mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with make_client(tmp_path) as client:
        await client.get_submissions(1)
    assert route.calls[0].request.headers["User-Agent"] == "ledgerlens test@example.com"


@respx.mock
async def test_cache_hit_skips_network(tmp_path: Path) -> None:
    route = respx.get(SUBMISSIONS_URL.format(cik=320193)).mock(
        return_value=httpx.Response(200, json={"cik": 320193})
    )
    async with make_client(tmp_path) as client:
        first = await client.get_submissions(320193)
        second = await client.get_submissions(320193)
    assert first == second == {"cik": 320193}
    assert route.call_count == 1


@respx.mock
async def test_expired_cache_refetches(tmp_path: Path) -> None:
    route = respx.get(SUBMISSIONS_URL.format(cik=5)).mock(
        return_value=httpx.Response(200, json={"v": 1})
    )
    async with make_client(tmp_path) as client:
        await client.get_submissions(5)
        # Rewind fetched_at beyond the TTL — deterministic expiry.
        meta_path = next(tmp_path.glob("*.meta.json"))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["fetched_at"] -= meta["ttl_s"] + 1
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        await client.get_submissions(5)
    assert route.call_count == 2


@respx.mock
async def test_retries_on_500_then_success(tmp_path: Path) -> None:
    route = respx.get(COMPANYFACTS_URL.format(cik=320193)).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"facts": {}}),
        ]
    )
    async with make_client(tmp_path) as client:
        data = await client.get_companyfacts(320193)
    assert data == {"facts": {}}
    assert route.call_count == 3


@respx.mock
async def test_5xx_exhausts_retries_to_source_unavailable(tmp_path: Path) -> None:
    route = respx.get(COMPANYFACTS_URL.format(cik=7)).mock(return_value=httpx.Response(503))
    async with make_client(tmp_path) as client:
        with pytest.raises(SourceUnavailableError, match="503"):
            await client.get_companyfacts(7)
    assert route.call_count == 3


@respx.mock
async def test_404_propagates_immediately_without_retry(tmp_path: Path) -> None:
    route = respx.get(SUBMISSIONS_URL.format(cik=999)).mock(return_value=httpx.Response(404))
    async with make_client(tmp_path) as client:
        with pytest.raises(ToolError, match="404") as excinfo:
            await client.get_submissions(999)
    assert route.call_count == 1
    assert excinfo.value.retryable is False


@respx.mock
async def test_rate_limiter_spaces_requests(tmp_path: Path) -> None:
    for cik in (1, 2, 3):
        respx.get(SUBMISSIONS_URL.format(cik=cik)).mock(
            return_value=httpx.Response(200, json={"cik": cik})
        )
    async with make_client(tmp_path, min_interval_s=0.1) as client:
        started = time.perf_counter()
        for cik in (1, 2, 3):
            await client.get_submissions(cik)
        elapsed = time.perf_counter() - started
    # Requests 2 and 3 must each wait >=100 ms after the previous one.
    assert elapsed >= 0.18


@respx.mock
async def test_archive_url_uses_undashed_accession_and_caches_forever(tmp_path: Path) -> None:
    expected_url = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-10k.htm"
    route = respx.get(expected_url).mock(return_value=httpx.Response(200, content=b"<html/>"))
    async with make_client(tmp_path) as client:
        body = await client.get_archive_document(320193, "0000320193-24-000123", "aapl-10k.htm")
        again = await client.get_archive_document(320193, "0000320193-24-000123", "aapl-10k.htm")
    assert body == again == b"<html/>"
    assert route.call_count == 1
    meta = next(tmp_path.glob("*.meta.json"))
    assert '"ttl_s": null' in meta.read_text(encoding="utf-8")


async def test_proxy_applies_only_to_sec_hosts(tmp_path: Path) -> None:
    calls: dict[str, list[str]] = {"sec": [], "direct": []}

    def handler_for(bucket: str) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            calls[bucket].append(str(request.url))
            return httpx.Response(200, content=b"{}")

        return handler

    client = EdgarClient(
        settings=make_settings(edgar_proxy_url="http://vps-fi:3128"),
        cache_dir=tmp_path,
        min_interval_s=0.0,
        sec_transport=httpx.MockTransport(handler_for("sec")),
        direct_transport=httpx.MockTransport(handler_for("direct")),
    )
    async with client:
        await client._get("https://data.sec.gov/submissions/CIK0000000001.json", None)
        await client._get("https://www.sec.gov/files/company_tickers.json", None)
        await client._get("https://example.org/unrelated.json", None)
    assert len(calls["sec"]) == 2 and all("sec.gov" in url for url in calls["sec"])
    assert calls["direct"] == ["https://example.org/unrelated.json"]


async def test_proxy_failure_is_explicit_and_not_retried(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def failing_proxy(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ProxyError("connection refused")

    client = EdgarClient(
        settings=make_settings(edgar_proxy_url="http://vps-fi:3128"),
        cache_dir=tmp_path,
        min_interval_s=0.0,
        retry_wait_base_s=0.01,
        sec_transport=httpx.MockTransport(failing_proxy),
    )
    async with client:
        with pytest.raises(SourceUnavailableError, match="EDGAR_PROXY_URL") as excinfo:
            await client.get_submissions(1)
    assert attempts["count"] == 1  # no silent retry, no direct fallback
    assert excinfo.value.retryable is False
