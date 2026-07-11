"""MCP server for the price_enrich tool (T-033; CONTRACTS §9, ARCHITECTURE §3.3).

FastMCP over streamable HTTP wrapping the enrich core, same pattern as
tools/sql/server.py: identical JSON schema in lib and MCP mode, observation
errors ({error, retryable}) come back as tool RESULTS, never as protocol
errors. ``X-Run-Id`` is bound into the JSON logs for correlation.

Run: python -m tools.enrich.server   (port: MCP_ENRICH_PORT, default 8767)
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from common.logging import configure_logging, get_logger
from tools.enrich import core

server = FastMCP(
    "ledgerlens-enrich",
    instructions=(
        "End-of-day close prices for one ticker over a date range: the prices "
        "table first, the Stooq EOD provider on a miss. Price series are "
        "context for dynamics only — never forecasts or recommendations."
    ),
    host="0.0.0.0",  # noqa: S104 — container-internal service
    port=int(os.environ.get("MCP_ENRICH_PORT", "8767")),
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
        get_logger(node="mcp_enrich").info("mcp_tool_called", run_id=run_id)


@server.tool(name="price_enrich")
async def price_enrich(
    ticker: str,
    date_from: str,
    date_to: str,
    ctx: Context = None,  # type: ignore[type-arg,assignment]
) -> dict[str, Any]:
    """End-of-day close prices for one ticker between two ISO dates.
    Returns {series: [{date, close, currency}], source, cached}. Use the
    series only as context for price dynamics — never for forecasts or
    buy/sell recommendations. On error, read `error` and degrade honestly."""
    if ctx is not None:
        _bind_run_id(ctx)
    return await core.price_enrich(ticker, date_from, date_to)


if __name__ == "__main__":
    configure_logging("mcp_enrich")
    server.run("streamable-http")
