"""MCP server for the SQL tools (T-027; CONTRACTS §9, ARCHITECTURE §3.3).

FastMCP over streamable HTTP wrapping the T-012 core functions. The transport
changes, the behaviour does not: the same JSON schemas, and the teaching
errors ({error, hint, schema_excerpt}) come back as tool RESULTS, never as
protocol errors. The ``X-Run-Id`` request header is bound into the JSON logs
for cross-service correlation of tool calls.

Run: python -m tools.sql.server   (port: MCP_SQL_PORT, default 8765)
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from common.logging import configure_logging, get_logger
from tools.sql import core

server = FastMCP(
    "ledgerlens-sql",
    instructions=(
        "Read-only SQL access to normalized financial statements. "
        "Main entry point: the latest_facts view."
    ),
    host="0.0.0.0",  # noqa: S104 — container-internal service
    port=int(os.environ.get("MCP_SQL_PORT", "8765")),
    stateless_http=True,
)


def _bind_run_id(ctx: Context) -> None:  # type: ignore[type-arg]
    """Best-effort X-Run-Id extraction for log correlation."""
    try:
        request = ctx.request_context.request
        run_id = request.headers.get("x-run-id") if request else None
    except (AttributeError, ValueError):
        run_id = None
    if run_id:
        get_logger(node="mcp_sql").info("mcp_tool_called", run_id=run_id)


@server.tool(name="sql_query")
async def sql_query(sql: str, row_limit: int = 50, ctx: Context = None) -> dict[str, Any]:  # type: ignore[type-arg,assignment]
    """Execute a read-only PostgreSQL SELECT. Main entry point: the
    latest_facts view (ticker, metric, value, unit, period_end,
    fiscal_period). On error, read `hint` and fix the query."""
    if ctx is not None:
        _bind_run_id(ctx)
    return await core.sql_query(sql, row_limit)


@server.tool(name="schema_introspect")
async def schema_introspect(ctx: Context = None) -> dict[str, Any]:  # type: ignore[type-arg,assignment]
    """List tables/columns, the canonical metric dictionary and example SQL
    queries. Call when unsure about the schema."""
    if ctx is not None:
        _bind_run_id(ctx)
    return await core.schema_introspect()


if __name__ == "__main__":
    configure_logging("mcp_sql")
    server.run("streamable-http")
