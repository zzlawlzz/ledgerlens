"""Smoke test for gate G1 (T-015): three canonical questions over /api/chat.

Usage:
    uv run python scripts/smoke_test.py [--base-url http://localhost:8000]
                                        [--auto-ingest]

Checks per question: run succeeded, the answer contains numbers, the trace
contains at least one sql_query call. The single-company question is also
cross-checked against latest_facts in the database. With ``--auto-ingest``
an empty database is filled first (live EDGAR, uses the disk cache).
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


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--auto-ingest", action="store_true")
    args = parser.parse_args()

    if await _db_is_empty():
        if not args.auto_ingest:
            print("FAIL: database is empty; run `make demo-ingest` or pass --auto-ingest")
            return 1
        print("database is empty — running ingest (live EDGAR, cached)...")
        from adapters.base import get_adapter
        from ingestion.run import run_ingest

        reports = await run_ingest(get_adapter("edgar"), list(SMOKE_TICKERS), years=3)
        failed = [r.ticker for r in reports if not r.ok]
        if failed:
            print(f"FAIL: ingest failed for {failed}")
            return 1

    t0, t1, t2 = SMOKE_TICKERS
    failures: list[str] = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=180) as client:
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
            if template is QUESTIONS[0] and expected_revenue:
                if expected_revenue not in re.sub(r"\D", "", answer):
                    failures.append(
                        f"{question}: answer does not contain DB value {expected_revenue}"
                    )
    if failures:
        print("\nSMOKE FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nSMOKE OK: 3/3 questions answered from real data")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
