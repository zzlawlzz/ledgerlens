"""web_search tool core (T-043; CONTRACTS §9 style).

When the loaded corpus can't answer (financial or political facts the agent
lacks), search the open web, tag each source by a domain-trust tier, and cache
the findings in ``web_documents`` so a repeat query never hits the network
again. Cache-first, mirroring ``price_enrich``'s discipline:

    web_documents cache  →  Tavily search API (TAVILY_API_KEY, free tier)
                         →  scrape DuckDuckGo (no key; often bot-blocked from a
                            server IP — that is exactly why Tavily is primary)
                         →  DeepSeek fallback (model knowledge, low trust)

Errors are *observations* (``{error, retryable}``), never exceptions — the run
degrades honestly. Trust:
- ``high``   — a tier-1 primary/official source (sec.gov, *.gov, IR sites, wire
               services) is present;
- ``medium`` — no tier-1, but ≥2 sources cross-check the finding;
- ``low``    — a single unverified source, or the DeepSeek fallback (not
               live-verified).
Scraped buy/sell language is still caught downstream by the non-advice guardrail
(orchestrator/guardrail.py); web citations carry ``trust`` + ``source_type:web``
so the UI badges them and the grounding pass keeps them.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.config import get_settings, load_yaml_config
from common.db import get_session_factory
from common.errors import ConfigError, SourceUnavailableError
from common.logging import get_logger

# Primary backend: the Tavily search API (free tier) when TAVILY_API_KEY is set —
# designed for programmatic access, so it returns reliably where server-side
# scraping gets bot-blocked. Falls back to scraping, then DeepSeek.
TAVILY_URL = "https://api.tavily.com/search"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
# A browser-like UA: DuckDuckGo serves its HTML SERP to browsers, not to bots.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_MAX_RESULTS = 6
DEFAULT_TTL_DAYS = 14
DEFAULT_TIMEOUT_S = 12.0
DEFAULT_MIN_INTERVAL_S = 2.0
DEFAULT_DAILY_LIMIT = 60
MAX_QUERY_CHARS = 400
SNIPPET_MAX = 500
TITLE_MAX = 200

# Fallback trust tiers if config/web_search.yaml is missing. Matched as domain
# suffixes: an entry "gov" matches "sec.gov"; "reuters.com" matches
# "www.reuters.com".
DEFAULT_TIERS: dict[str, list[str]] = {
    "tier1": [
        "gov",
        "sec.gov",
        "europa.eu",
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "apnews.com",
    ],
    "tier2": [
        "wikipedia.org",
        "cnbc.com",
        "forbes.com",
        "marketwatch.com",
        "investopedia.com",
        "nytimes.com",
        "theguardian.com",
        "bbc.com",
        "bbc.co.uk",
    ],
}

_sleep = asyncio.sleep

_CACHE_SQL = text(
    "SELECT url, domain, title, snippet, trust FROM web_documents "
    "WHERE query_norm = :q AND retrieved_at > :fresh_after "
    "ORDER BY retrieved_at DESC"
)
_UPSERT_SQL = text(
    "INSERT INTO web_documents (query_norm, url, domain, title, snippet, trust, retrieved_at) "
    "VALUES (:query_norm, :url, :domain, :title, :snippet, :trust, :retrieved_at) "
    "ON CONFLICT (query_norm, url) DO UPDATE SET "
    "title = EXCLUDED.title, snippet = EXCLUDED.snippet, trust = EXCLUDED.trust, "
    "retrieved_at = EXCLUDED.retrieved_at"
)


def _config() -> dict[str, Any]:
    try:
        raw = load_yaml_config("web_search")
    except ConfigError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _trust_tiers(config: dict[str, Any]) -> dict[str, list[str]]:
    tiers = config.get("trust", {})
    if not isinstance(tiers, dict):
        return DEFAULT_TIERS
    return {
        "tier1": list(tiers.get("tier1", DEFAULT_TIERS["tier1"])),
        "tier2": list(tiers.get("tier2", DEFAULT_TIERS["tier2"])),
    }


def _domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _matches(domain: str, suffix: str) -> bool:
    suffix = suffix.lower().lstrip("*.")
    return domain == suffix or domain.endswith("." + suffix)


def trust_for_domain(domain: str, tiers: dict[str, list[str]]) -> str:
    """high / medium / low for a domain against the tier lists."""
    if any(_matches(domain, s) for s in tiers.get("tier1", [])):
        return "high"
    if any(_matches(domain, s) for s in tiers.get("tier2", [])):
        return "medium"
    return "low"


def _decode_ddg_url(href: str) -> str:
    """DuckDuckGo wraps external links as //duckduckgo.com/l/?uddg=<encoded>."""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs and qs["uddg"]:
            return qs["uddg"][0]
    return href


def parse_ddg_html(
    html: str, max_results: int, tiers: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Parse a DuckDuckGo HTML SERP into ranked, trust-tagged results (pure)."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in soup.select("div.result"):
        link = block.select_one("a.result__a")
        if link is None:
            continue
        url = _decode_ddg_url(str(link.get("href", "")))
        if not url.startswith("http"):
            continue
        domain = _domain_of(url)
        if not domain or domain in seen or "duckduckgo.com" in domain:
            continue
        seen.add(domain)
        snippet_el = block.select_one(".result__snippet")
        results.append(
            {
                "title": link.get_text(" ", strip=True)[:TITLE_MAX],
                "url": url,
                "domain": domain,
                "snippet": (snippet_el.get_text(" ", strip=True) if snippet_el else "")[
                    :SNIPPET_MAX
                ],
                "trust": trust_for_domain(domain, tiers),
            }
        )
        if len(results) >= max_results:
            break
    # Trusted sources first so the agent (and citations) lead with them.
    return _rank(results)


def parse_tavily(
    data: dict[str, Any], max_results: int, tiers: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Tavily /search JSON -> ranked, trust-tagged results (pure). Tavily returns
    a real extract per result in ``content``, richer than a SERP snippet."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data.get("results") or []:
        url = str(item.get("url", ""))
        if not url.startswith("http"):
            continue
        domain = _domain_of(url)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        results.append(
            {
                "title": str(item.get("title") or "")[:TITLE_MAX],
                "url": url,
                "domain": domain,
                "snippet": str(item.get("content") or "")[:SNIPPET_MAX],
                "trust": trust_for_domain(domain, tiers),
            }
        )
        if len(results) >= max_results:
            break
    return _rank(results)


def _rank(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: order.get(r["trust"], 3))
    return results


def trust_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-source trust into a confidence verdict for the answer."""
    if not results:
        return {"level": "none", "note": "no sources found", "sources": 0}
    if any(r["trust"] == "high" for r in results):
        return {
            "level": "high",
            "note": "a trusted primary source was found",
            "sources": len(results),
        }
    if len(results) >= 2:
        return {
            "level": "medium",
            "note": "no single trusted source; cross-checked across multiple sources",
            "sources": len(results),
        }
    return {"level": "low", "note": "single unverified source", "sources": len(results)}


def _citation(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": None,
        "form_type": "web",
        "period": None,
        "section": result["domain"],
        "source_url": result["url"],
        "snippet": result["snippet"][:200],
        "title": result["title"],
        "trust": result["trust"],
        "source_type": "web",
    }


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())[:MAX_QUERY_CHARS]


def _observation(error: str, retryable: bool, hint: str | None = None) -> dict[str, Any]:
    obs: dict[str, Any] = {"error": error, "retryable": retryable}
    if hint:
        obs["hint"] = hint
    return obs


class _Limiter:
    """Min-interval + per-UTC-day cap on outbound searches (process-local)."""

    def __init__(self, min_interval_s: float, daily_limit: int) -> None:
        self._min_interval_s = min_interval_s
        self._daily_limit = daily_limit
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._day = datetime.now(UTC).date()
        self._count = 0

    async def acquire(self) -> str | None:
        async with self._lock:
            today = datetime.now(UTC).date()
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= self._daily_limit:
                return (
                    f"daily web-search limit reached ({self._daily_limit}/day); "
                    "cached results still work"
                )
            self._count += 1
            now = time.monotonic()
            delay = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._min_interval_s
        if delay > 0:
            await _sleep(delay)
        return None


_LIMITER: _Limiter | None = None


def get_limiter() -> _Limiter:
    global _LIMITER
    if _LIMITER is None:
        config = _config()
        _LIMITER = _Limiter(
            float(config.get("min_interval_s", DEFAULT_MIN_INTERVAL_S)),
            int(config.get("daily_request_limit", DEFAULT_DAILY_LIMIT)),
        )
    return _LIMITER


def reset_limiter() -> None:
    global _LIMITER
    _LIMITER = None


async def _scrape(
    query: str, timeout_s: float, max_results: int, tiers: dict[str, list[str]]
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=timeout_s),
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True,
    ) as client:
        response = await client.post(DDG_HTML_URL, data={"q": query, "kl": "wt-wt"})
    if response.status_code != 200:
        raise SourceUnavailableError(f"web search returned HTTP {response.status_code}")
    return parse_ddg_html(response.text, max_results, tiers)


async def _tavily_search(
    query: str, api_key: str, timeout_s: float, max_results: int, tiers: dict[str, list[str]]
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=timeout_s)) as client:
        response = await client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": max_results, "search_depth": "basic"},
        )
    if response.status_code != 200:
        raise SourceUnavailableError(f"tavily returned HTTP {response.status_code}")
    return parse_tavily(response.json(), max_results, tiers)


async def _search(
    query: str, timeout_s: float, max_results: int, tiers: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], str | None]:
    """Reliable-first backend chain: Tavily API (if TAVILY_API_KEY) → DDG scrape.
    Returns (results, error); an empty list + error means both backends failed."""
    api_key = get_settings().tavily_api_key
    if api_key:
        try:
            results = await _tavily_search(query, api_key, timeout_s, max_results, tiers)
            if results:
                return results, None
        except (SourceUnavailableError, httpx.HTTPError, ValueError):
            pass  # fall through to the scraper
    try:
        return await _scrape(query, timeout_s, max_results, tiers), None
    except (SourceUnavailableError, httpx.HTTPError) as error:
        return [], str(error)


async def _deepseek_fallback(query: str, router: Any) -> dict[str, Any] | None:
    """Last resort: DeepSeek from model knowledge (clearly low-trust)."""
    if router is None:
        try:
            from model_router.router import RouterClient

            router = RouterClient()
        except Exception:  # noqa: BLE001 — no router => no fallback, not a crash
            return None
    messages = [
        {
            "role": "system",
            "content": (
                "You are a factual research assistant. Answer the query concisely from your "
                "knowledge. State facts only, no investment advice. If you are unsure or the "
                "information may be outdated, say so. Do not fabricate sources or URLs."
            ),
        },
        {"role": "user", "content": query},
    ]
    try:
        response = await router.chat("web_search", messages)
    except Exception:  # noqa: BLE001
        return None
    answer = getattr(response, "text", "") or ""
    if not answer.strip():
        return None
    return {
        "results": [],
        "answer": answer.strip(),
        "citations": [],
        "trust_summary": {
            "level": "low",
            "note": "from model knowledge, not verified against a live source",
            "sources": 0,
        },
        "source": "deepseek",
        "cached": False,
    }


async def _read_cache(
    factory: async_sessionmaker[AsyncSession], query_norm: str, ttl_days: int
) -> list[dict[str, Any]]:
    fresh_after = datetime.now(UTC) - timedelta(days=ttl_days)
    async with factory() as session:
        rows = (
            await session.execute(_CACHE_SQL, {"q": query_norm, "fresh_after": fresh_after})
        ).fetchall()
    return [
        {"url": url, "domain": domain, "title": title, "snippet": snippet, "trust": trust}
        for url, domain, title, snippet, trust in rows
    ]


async def _write_cache(
    factory: async_sessionmaker[AsyncSession], query_norm: str, results: list[dict[str, Any]]
) -> None:
    now = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            _UPSERT_SQL,
            [
                {
                    "query_norm": query_norm,
                    "url": r["url"],
                    "domain": r["domain"],
                    "title": r["title"],
                    "snippet": r["snippet"],
                    "trust": r["trust"],
                    "retrieved_at": now,
                }
                for r in results
            ],
        )
        await session.commit()


async def web_search(
    query: str,
    *,
    max_results: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    router: Any = None,
    limiter: _Limiter | None = None,
) -> dict[str, Any]:
    """Search the web with a durable cache. Returns an observation dict.

    On success: ``{"results": [{title, url, domain, snippet, trust}],
    "citations": [...], "trust_summary": {...}, "source": "db"|"web"|"deepseek",
    "cached": bool}``. On failure: ``{"error", "retryable"}``.
    """
    log = get_logger(node="web_search")
    if not isinstance(query, str) or not query.strip():
        return _observation("query is required", retryable=False)
    if len(query) > MAX_QUERY_CHARS:
        return _observation(f"query longer than {MAX_QUERY_CHARS} chars", retryable=False)

    config = _config()
    tiers = _trust_tiers(config)
    ttl_days = int(config.get("ttl_days", DEFAULT_TTL_DAYS))
    timeout_s = float(config.get("timeout_s", DEFAULT_TIMEOUT_S))
    limit = max_results or int(config.get("max_results", DEFAULT_MAX_RESULTS))
    query_norm = normalize_query(query)
    factory = session_factory or get_session_factory()

    # 1) durable cache
    try:
        cached = await _read_cache(factory, query_norm, ttl_days)
    except (SQLAlchemyError, OSError) as error:
        log.warning("web_cache_unavailable", error=str(error)[:200])
        cached = []
    if cached:
        return {
            "results": cached,
            "citations": [_citation(r) for r in cached],
            "trust_summary": trust_summary(cached),
            "source": "db",
            "cached": True,
        }

    # 2) live search: Tavily API (reliable) → DDG scrape fallback (rate-limited)
    limit_message = await (limiter or get_limiter()).acquire()
    results: list[dict[str, Any]]
    scrape_error: str | None
    if limit_message is not None:
        results, scrape_error = [], limit_message
    else:
        results, scrape_error = await _search(query, timeout_s, limit, tiers)

    if results:
        try:
            await _write_cache(factory, query_norm, results)
        except (SQLAlchemyError, OSError) as error:
            log.warning("web_cache_write_failed", error=str(error)[:200])
        return {
            "results": results,
            "citations": [_citation(r) for r in results],
            "trust_summary": trust_summary(results),
            "source": "web",
            "cached": False,
        }

    # 3) DeepSeek fallback (model knowledge, low trust)
    fallback = await _deepseek_fallback(query, router)
    if fallback is not None:
        return fallback

    return _observation(
        scrape_error or "web search found no usable results",
        retryable=True,
        hint="Continue the analysis and state honestly that no reliable web source was found.",
    )
