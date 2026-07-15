"""MCP server for the web_search tool (T-043; CONTRACTS §9 style).

FastMCP over streamable HTTP wrapping the web_search core, same pattern as
tools/enrich/server.py: identical JSON schema in lib and MCP mode, observation
errors ({error, retryable}) come back as tool RESULTS, never as protocol
errors. ``X-Run-Id`` is bound into the JSON logs for correlation.

Run: python -m tools.web_search.server   (port: MCP_WEBSEARCH_PORT, default 8768)
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from common.logging import configure_logging, get_logger
from tools.web_search import core

server = FastMCP(
    "ledgerlens-web-search",
    instructions=(
        "Search the open web when the loaded corpus cannot answer (recent or "
        "political facts). Results are trust-tagged (high/medium/low) and cached; "
        "prefer high-trust sources and cross-check when none is trusted. Findings "
        "are context and must be cited — never forecasts or recommendations."
    ),
    host="0.0.0.0",  # noqa: S104 — container-internal service
    port=int(os.environ.get("MCP_WEBSEARCH_PORT", "8768")),
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
        get_logger(node="mcp_web_search").info("mcp_tool_called", run_id=run_id)


@server.tool(name="web_search")
async def web_search(
    query: str,
    ctx: Context = None,  # type: ignore[type-arg,assignment]
) -> dict[str, Any]:
    """Search the web for facts the loaded corpus lacks (recent/political news,
    figures not in EDGAR). Returns {results: [{title, url, domain, snippet,
    trust}], citations, trust_summary, source, cached}. Prefer high-trust
    sources; when trust_summary.level is low, say so in the answer and cite the
    URL. Findings are context to be cited — never advice. On error read `error`
    and degrade honestly."""
    if ctx is not None:
        _bind_run_id(ctx)
    return await core.web_search(query)


if __name__ == "__main__":
    configure_logging("mcp_web_search")
    server.run("streamable-http")
