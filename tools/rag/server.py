"""MCP server for the RAG tool (T-027; CONTRACTS §9, ARCHITECTURE §3.3).

FastMCP over streamable HTTP wrapping the T-019 core. Same schema and
behaviour as the lib mode, including honest ``no_results`` and
observation-style errors. Embedding/reranker models are process-local
(cache under data/cache/fastembed) and load lazily on the first call.

Run: python -m tools.rag.server   (port: MCP_RAG_PORT, default 8766)
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from common.logging import configure_logging, get_logger
from tools.rag import core

server = FastMCP(
    "ledgerlens-rag",
    instructions=(
        "Hybrid search over narrative 10-K sections (risk factors, MD&A) "
        "with citations; honest no_results when the corpus has no answer."
    ),
    host="0.0.0.0",  # noqa: S104 — container-internal service
    port=int(os.environ.get("MCP_RAG_PORT", "8766")),
    stateless_http=True,
)

_EMBEDDER: Any = None
_INDEXER: Any = None


def _shared_components() -> tuple[Any, Any]:
    """One embedder/indexer per process — model loads are expensive."""
    global _EMBEDDER, _INDEXER
    if _EMBEDDER is None:
        from rag.embedding import ChunkEmbedder
        from rag.indexer import NarrativeIndexer

        _EMBEDDER = ChunkEmbedder()
        _INDEXER = NarrativeIndexer(embedder=_EMBEDDER)
    return _EMBEDDER, _INDEXER


@server.tool(name="rag_search")
async def rag_search(
    query: str,
    top_k: int = core.DEFAULT_TOP_K,
    filters: dict[str, Any] | None = None,
    ctx: Context = None,  # type: ignore[type-arg,assignment]
) -> dict[str, Any]:
    """Hybrid search over narrative 10-K sections (risk_factors, mdna).
    Returns text chunks with citations. Optional filters: tickers,
    form_types, sections, period_from/period_to (ISO dates). If no_results
    is true, honestly say the data is not loaded."""
    if ctx is not None:
        try:
            request = ctx.request_context.request
            run_id = request.headers.get("x-run-id") if request else None
        except (AttributeError, ValueError):
            run_id = None
        if run_id:
            get_logger(node="mcp_rag").info("mcp_tool_called", run_id=run_id)
    embedder, indexer = _shared_components()
    return await core.rag_search(query, top_k, filters, indexer=indexer, embedder=embedder)


if __name__ == "__main__":
    configure_logging("mcp_rag")
    server.run("streamable-http")
