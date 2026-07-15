"""web_search tool unit tests (T-043).

Pure parsers/scorers exercised directly; the orchestration (cache → scrape →
DeepSeek fallback) with respx-mocked HTTP, a fake DB session factory and a fake
router. No network, no DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import respx

from tools.web_search import core
from tools.web_search.core import (
    _decode_ddg_url,
    normalize_query,
    parse_ddg_html,
    trust_for_domain,
    trust_summary,
    web_search,
)

TIERS = {
    "tier1": ["gov", "sec.gov", "reuters.com"],
    "tier2": ["wikipedia.org", "cnbc.com"],
}


def _ddg_link(url: str) -> str:
    """A DuckDuckGo redirect wrapper the way the SERP emits it."""
    return f"//duckduckgo.com/l/?uddg={quote(url, safe='')}&rut=abc"


def _sample_html(pairs: list[tuple[str, str, str]]) -> str:
    blocks = "".join(
        f"""
        <div class="result results_links web-result">
          <h2 class="result__title">
            <a class="result__a" href="{_ddg_link(url)}">{title}</a>
          </h2>
          <a class="result__snippet" href="{url}">{snippet}</a>
        </div>
        """
        for url, title, snippet in pairs
    )
    return f"<html><body>{blocks}</body></html>"


# ---------------------------------------------------------------- pure helpers


def test_trust_for_domain_tiers() -> None:
    assert trust_for_domain("sec.gov", TIERS) == "high"  # exact tier1
    assert trust_for_domain("investor.whitehouse.gov", TIERS) == "high"  # *.gov suffix
    assert trust_for_domain("www.reuters.com", TIERS) == "high"  # normalized elsewhere
    assert trust_for_domain("reuters.com", TIERS) == "high"
    assert trust_for_domain("en.wikipedia.org", TIERS) == "medium"  # tier2 suffix
    assert trust_for_domain("some-blog.example", TIERS) == "low"


def test_decode_ddg_url() -> None:
    target = "https://www.reuters.com/business/apple-ceo"
    assert _decode_ddg_url(_ddg_link(target)) == target
    # A direct protocol-relative link is normalized to https.
    assert _decode_ddg_url("//example.com/x") == "https://example.com/x"
    assert _decode_ddg_url("https://example.com/y") == "https://example.com/y"


def test_parse_ddg_html_ranks_and_dedupes() -> None:
    html = _sample_html(
        [
            ("https://blog.example.com/a", "Some blog", "unverified claim"),
            ("https://www.reuters.com/x", "Reuters report", "reuters says X"),
            ("https://www.reuters.com/dup", "Reuters dup", "same domain, dropped"),
            ("https://en.wikipedia.org/wiki/Y", "Wikipedia Y", "wiki summary"),
        ]
    )
    results = parse_ddg_html(html, max_results=6, tiers=TIERS)
    domains = [r["domain"] for r in results]
    # www. is stripped; other subdomains (en.) are kept as the display domain.
    assert "reuters.com" in domains and "en.wikipedia.org" in domains
    assert domains.count("reuters.com") == 1  # deduped by domain
    # Trusted (high) sources sort first.
    assert results[0]["trust"] == "high"
    assert results[-1]["trust"] == "low"
    # www. stripped, snippet + title carried.
    reuters = next(r for r in results if r["domain"] == "reuters.com")
    assert reuters["url"].startswith("https://www.reuters.com")
    assert reuters["snippet"] == "reuters says X"


def test_parse_ddg_html_respects_max_results() -> None:
    html = _sample_html([(f"https://d{i}.example.com/p", f"T{i}", f"s{i}") for i in range(10)])
    assert len(parse_ddg_html(html, max_results=3, tiers=TIERS)) == 3


def test_trust_summary_levels() -> None:
    assert trust_summary([])["level"] == "none"
    assert trust_summary([{"trust": "high"}, {"trust": "low"}])["level"] == "high"
    assert trust_summary([{"trust": "medium"}, {"trust": "low"}])["level"] == "medium"
    assert trust_summary([{"trust": "low"}])["level"] == "low"


def test_normalize_query() -> None:
    assert normalize_query("  Apple   CEO  2025 ") == "apple ceo 2025"


# --------------------------------------------------------------- orchestration


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, cache_rows: list[Any], writes: list[Any]) -> None:
        self._cache_rows = cache_rows
        self._writes = writes

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def execute(self, _stmt: Any, params: Any = None) -> _FakeResult:
        if isinstance(params, list):  # upsert
            self._writes.extend(params)
            return _FakeResult([])
        return _FakeResult(self._cache_rows)

    async def commit(self) -> None:
        return None


def _factory(cache_rows: list[Any], writes: list[Any]) -> Any:
    def make() -> _FakeSession:
        return _FakeSession(cache_rows, writes)

    return make


def _limiter() -> core._Limiter:
    return core._Limiter(min_interval_s=0.0, daily_limit=10000)


@pytest.mark.asyncio
async def test_empty_query_is_observation_error() -> None:
    result = await web_search("   ", session_factory=_factory([], []))
    assert result["error"] and result["retryable"] is False


@pytest.mark.asyncio
async def test_cache_hit_skips_network() -> None:
    # A row shaped like the SELECT: (url, domain, title, snippet, trust).
    cache = [("https://www.reuters.com/x", "reuters.com", "R", "cached snippet", "high")]
    with respx.mock:
        route = respx.post(core.DDG_HTML_URL).mock(return_value=httpx.Response(200, text=""))
        result = await web_search(
            "apple ceo", session_factory=_factory(cache, []), limiter=_limiter()
        )
    assert result["source"] == "db"
    assert result["cached"] is True
    assert result["citations"][0]["source_type"] == "web"
    assert route.call_count == 0  # cache hit => no scrape


@pytest.mark.asyncio
async def test_cache_miss_scrapes_and_upserts() -> None:
    writes: list[Any] = []
    html = _sample_html([("https://www.sec.gov/x", "SEC filing", "official filing text")])
    with respx.mock:
        respx.post(core.DDG_HTML_URL).mock(return_value=httpx.Response(200, text=html))
        result = await web_search(
            "apple 10-k 2025", session_factory=_factory([], writes), limiter=_limiter()
        )
    assert result["source"] == "web"
    assert result["cached"] is False
    assert result["results"][0]["domain"] == "sec.gov"
    assert result["trust_summary"]["level"] == "high"
    # The scraped rows were written back to the cache.
    assert writes and writes[0]["url"] == "https://www.sec.gov/x"
    assert writes[0]["trust"] == "high"


@pytest.mark.asyncio
async def test_scrape_blocked_falls_back_to_deepseek() -> None:
    router = SimpleNamespace(
        chat=lambda *_a, **_k: _coro(SimpleNamespace(text="Model knowledge answer.")),
    )
    with respx.mock:
        respx.post(core.DDG_HTML_URL).mock(return_value=httpx.Response(403, text="blocked"))
        result = await web_search(
            "some obscure query",
            session_factory=_factory([], []),
            router=router,
            limiter=_limiter(),
        )
    assert result["source"] == "deepseek"
    assert result["trust_summary"]["level"] == "low"
    assert "Model knowledge" in result["answer"]


async def _coro(value: Any) -> Any:
    return value
