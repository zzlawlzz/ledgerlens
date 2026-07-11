"""MCP tool servers contract (T-027): schemas per CONTRACTS §9, lib≡MCP.

Requires the compose stack (mcp-sql on :8765, mcp-rag on :8766) — skipped
when the servers are unreachable.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

pytestmark = pytest.mark.slow

SQL_URL = "http://localhost:8765/mcp"
RAG_URL = "http://localhost:8766/mcp"


async def _list_tools(url: str) -> dict[str, Any]:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
    return {tool.name: tool for tool in listed.tools}


async def _call(url: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    assert not result.isError, f"MCP protocol error for {tool}: {result}"
    text = next(part.text for part in result.content if hasattr(part, "text"))
    parsed = json.loads(text)
    assert isinstance(parsed, dict)
    return parsed


async def _reachable(url: str) -> bool:
    try:
        await _list_tools(url)
    except Exception:  # noqa: BLE001 — any transport failure means "not running"
        return False
    return True


@pytest.fixture(autouse=True)
async def _require_servers() -> None:
    if not await _reachable(SQL_URL) or not await _reachable(RAG_URL):
        pytest.skip("MCP servers are not running (docker compose up mcp-sql mcp-rag)")


async def test_list_tools_matches_contract_schemas() -> None:
    sql_tools = await _list_tools(SQL_URL)
    assert set(sql_tools) == {"sql_query", "schema_introspect"}
    sql_schema = sql_tools["sql_query"].inputSchema
    assert sql_schema["required"] == ["sql"]
    assert set(sql_schema["properties"]) >= {"sql", "row_limit"}
    assert sql_tools["schema_introspect"].inputSchema.get("required", []) == []

    rag_tools = await _list_tools(RAG_URL)
    assert set(rag_tools) == {"rag_search"}
    rag_schema = rag_tools["rag_search"].inputSchema
    assert rag_schema["required"] == ["query"]
    assert set(rag_schema["properties"]) >= {"query", "top_k", "filters"}


async def test_lib_and_mcp_results_identical_on_reference_calls() -> None:
    from tools.rag.core import rag_search as lib_rag_search
    from tools.sql.core import sql_query as lib_sql_query

    # 1) a successful query
    sql = (
        "SELECT ticker, metric FROM latest_facts WHERE ticker='AAPL' "
        "AND metric='revenue' AND fiscal_period='FY' ORDER BY period_end DESC LIMIT 1"
    )
    lib_ok = await lib_sql_query(sql, 5)
    mcp_ok = await _call(SQL_URL, "sql_query", {"sql": sql, "row_limit": 5})
    assert lib_ok == mcp_ok

    # 2) a teaching error (comes back as a RESULT, not a protocol error)
    bad = "SELECT nope FROM latest_facts"
    lib_err = await lib_sql_query(bad, 5)
    mcp_err = await _call(SQL_URL, "sql_query", {"sql": bad, "row_limit": 5})
    assert "error" in mcp_err and "hint" in mcp_err
    assert lib_err.get("hint") == mcp_err.get("hint")

    # 3) a narrative search (same model, same corpus -> same chunks/scores)
    filters: dict[str, object] = {"tickers": ["AAPL"]}
    args = {"query": "supply chain risks", "top_k": 2, "filters": filters}
    lib_rag = await lib_rag_search("supply chain risks", 2, filters)
    mcp_rag = await _call(RAG_URL, "rag_search", args)
    assert mcp_rag.get("no_results") is False
    lib_ids = [c["citation"]["chunk_id"] for c in lib_rag["chunks"]]
    mcp_ids = [c["citation"]["chunk_id"] for c in mcp_rag["chunks"]]
    assert lib_ids == mcp_ids
