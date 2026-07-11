"""Smoke test for gates G1/G2 (T-015/T-026) against the full compose stack.

Usage:
    uv run python scripts/smoke_test.py [--base-url http://localhost:8000]
                                        [--web-url http://localhost:3000]
                                        [--auto-ingest]

G1 core: three canonical numeric questions over /api/chat — run succeeded,
numbers in the answer, tool calls in the trace, the single-company answer
cross-checked against latest_facts. G2 additions: ingest fills embeddings
too, every run carries a guardrail event, a narrative question returns
sec.gov citations, /agui speaks the AG-UI protocol, and the web UI responds.
With ``--auto-ingest`` an empty database is filled first (live EDGAR via the
disk cache + chunk/embed into Qdrant).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from typing import Any

import httpx
from sqlalchemy import text

from common.db import get_session_factory

SMOKE_TICKERS = ("AAPL", "MSFT", "NVDA")

QUESTIONS = [
    "What was the revenue of {t0} in its most recent fiscal year?",
    "Compare the latest fiscal-year net income of {t0} and {t1}.",
    "How did the revenue of {t2} change over the last 3 fiscal years?",
]


async def _db_is_empty() -> bool:
    factory = get_session_factory()
    async with factory() as session:
        count = (await session.execute(text("SELECT count(*) FROM financial_facts"))).scalar_one()
    return int(count) == 0


async def _expected_latest_revenue(ticker: str) -> str | None:
    factory = get_session_factory()
    async with factory() as session:
        value = (
            await session.execute(
                text(
                    "SELECT value FROM latest_facts WHERE ticker = :t AND "
                    "metric = 'revenue' AND fiscal_period = 'FY' "
                    "ORDER BY period_end DESC LIMIT 1"
                ),
                {"t": ticker},
            )
        ).scalar()
    if value is None:
        return None
    return str(value).split(".")[0]


async def _ask(client: httpx.AsyncClient, question: str) -> tuple[dict[str, Any], list[str]]:
    events: list[dict[str, Any]] = []
    async with client.stream("POST", "/api/chat", json={"question": question}) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    names = [event["event"] for event in events]
    final = events[-1] if events else {}
    return final, names


async def _check_agui(client: httpx.AsyncClient) -> str | None:
    """One short question over /agui: protocol events must bracket the run."""
    body = {
        "threadId": "smoke-agui",
        "runId": "smoke-agui-run",
        "state": {},
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "content": f"What was {SMOKE_TICKERS[0]} revenue in its most recent fiscal year?",
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    types: list[str] = []
    async with client.stream("POST", "/agui", json=body) as response:
        if response.status_code != 200:
            return f"/agui returned {response.status_code}"
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                types.append(json.loads(line[len("data: ") :]).get("type", ""))
    if not types or types[0] != "RUN_STARTED" or types[-1] != "RUN_FINISHED":
        return f"/agui event bracket broken: first={types[:1]}, last={types[-1:]}"
    if "TEXT_MESSAGE_CONTENT" not in types:
        return "/agui produced no answer text events"
    return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--auto-ingest", action="store_true")
    args = parser.parse_args()

    if await _db_is_empty():
        if not args.auto_ingest:
            print("FAIL: database is empty; run `make demo-ingest` or pass --auto-ingest")
            return 1
        print("database is empty — running ingest+embed (live EDGAR, cached)...")
        from adapters.base import get_adapter
        from ingestion.run import run_ingest

        reports = await run_ingest(get_adapter("edgar"), list(SMOKE_TICKERS), years=3, embed=True)
        failed = [r.ticker for r in reports if not r.ok]
        if failed:
            print(f"FAIL: ingest failed for {failed}")
            return 1
        if sum(r.chunks for r in reports) == 0:
            print("FAIL: ingest produced no narrative chunks (RAG would be empty)")
            return 1

    t0, t1, t2 = SMOKE_TICKERS
    failures: list[str] = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=1500) as client:
        health = await client.get("/healthz")
        if health.status_code != 200:
            print(f"FAIL: /healthz returned {health.status_code}")
            return 1
        expected_revenue = await _expected_latest_revenue(t0)
        for template in QUESTIONS:
            question = template.format(t0=t0, t1=t1, t2=t2)
            print(f"\n>> {question}")
            final, names = await _ask(client, question)
            status = final.get("payload", {}).get("status")
            answer = str(final.get("payload", {}).get("answer", ""))
            print(f"   status={status}; answer={answer[:160]!r}")
            if final.get("event") != "run_finished" or status != "succeeded":
                failures.append(f"{question}: status={status}")
                continue
            if not re.search(r"\d", answer):
                failures.append(f"{question}: no numbers in answer")
            if "tool_call_started" not in names:
                failures.append(f"{question}: no tool calls in trace")
            if "guardrail" not in names:
                failures.append(f"{question}: no guardrail event in trace (T-022)")
            if template is QUESTIONS[0] and expected_revenue:
                if expected_revenue not in re.sub(r"\D", "", answer):
                    failures.append(
                        f"{question}: answer does not contain DB value {expected_revenue}"
                    )

        # G2: narrative question must come back with sec.gov citations.
        narrative = f"What are the main risks {t0} discloses in its latest 10-K?"
        print(f"\n>> {narrative}")
        final, names = await _ask(client, narrative)
        payload = final.get("payload", {})
        print(f"   status={payload.get('status')}; citations={len(payload.get('citations', []))}")
        if final.get("event") != "run_finished" or payload.get("status") != "succeeded":
            failures.append(f"narrative: status={payload.get('status')}")
        elif not any(
            "sec.gov" in str(c.get("source_url", "")) for c in payload.get("citations", [])
        ):
            failures.append("narrative: no sec.gov citations in the answer")

        # G2: the AG-UI protocol endpoint.
        print("\n>> /agui protocol check")
        agui_problem = await _check_agui(client)
        if agui_problem:
            failures.append(agui_problem)
        else:
            print("   agui OK")

    # G2: the web UI is served by nginx.
    async with httpx.AsyncClient(timeout=30) as plain:
        try:
            web = await plain.get(args.web_url)
            if web.status_code != 200 or '<div id="root"' not in web.text:
                failures.append(f"web UI at {args.web_url}: status={web.status_code}")
            else:
                print(f">> web UI OK at {args.web_url}")
        except httpx.HTTPError as exc:
            failures.append(f"web UI unreachable at {args.web_url}: {exc}")

    if failures:
        print("\nSMOKE FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nSMOKE OK: numeric x3 + narrative + agui + web, all from real data")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
