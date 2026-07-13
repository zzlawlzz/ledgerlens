"""rag_search tool core (T-019; CONTRACTS.md §9).

Pipeline: dense + sparse query -> server-side RRF fusion (Qdrant Query API)
-> top-``prefetch_limit`` candidates -> CPU cross-encoder rerank -> top-k with
citations. Anti-hallucination: empty candidates, or (with rerank enabled) a
best score below the calibrated threshold, return ``no_results=true`` — the
agent must answer "no data" instead of inventing.

Library function only; the MCP wrapper arrives in T-027.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date
from typing import Any

from qdrant_client import models

from common.config import load_yaml_config
from common.logging import get_logger
from rag.embedding import ChunkEmbedder
from rag.indexer import COLLECTION, DENSE_VECTOR, SPARSE_VECTOR, NarrativeIndexer

MAX_TOP_K = 10
# T-041: 5 starved "list every risk" questions — retrieval had strictly fewer
# candidate chunks than the risk types a 10-K discloses, and the model padded
# the gap from training knowledge despite the prompt forbidding it.
DEFAULT_TOP_K = 8
SNIPPET_CHARS = 300


def _build_filter(filters: dict[str, Any] | None) -> models.Filter:
    """Tool filters -> Qdrant filter; the meta point is always excluded."""
    must: list[models.Condition] = []
    filters = filters or {}
    if tickers := filters.get("tickers"):
        must.append(
            models.FieldCondition(
                key="ticker", match=models.MatchAny(any=[str(t).upper() for t in tickers])
            )
        )
    if form_types := filters.get("form_types"):
        must.append(
            models.FieldCondition(
                key="form_type", match=models.MatchAny(any=[str(f) for f in form_types])
            )
        )
    if sections := filters.get("sections"):
        must.append(
            models.FieldCondition(
                key="section", match=models.MatchAny(any=[str(s) for s in sections])
            )
        )
    period_from, period_to = filters.get("period_from"), filters.get("period_to")
    if period_from or period_to:
        must.append(
            models.FieldCondition(
                key="period_end",
                range=models.DatetimeRange(
                    gte=date.fromisoformat(str(period_from)) if period_from else None,
                    lte=date.fromisoformat(str(period_to)) if period_to else None,
                ),
            )
        )
    return models.Filter(
        must=must or None,
        must_not=[models.FieldCondition(key="meta", match=models.MatchValue(value=True))],
    )


def _fusion_query() -> Any:
    """RRF fusion query across qdrant-client API generations (T-002 note)."""
    if hasattr(models, "RrfQuery"):
        return models.RrfQuery(rrf=models.Rrf())
    return models.FusionQuery(fusion=models.Fusion.RRF)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _citation(payload: dict[str, Any], point_id: Any) -> dict[str, Any]:
    return {
        "ticker": payload.get("ticker"),
        "form_type": payload.get("form_type"),
        "period": payload.get("period_end"),
        "section": payload.get("section"),
        "source_url": payload.get("source_url"),
        "filing_id": payload.get("filing_id"),
        "chunk_id": str(point_id),
        "snippet": str(payload.get("text", ""))[:SNIPPET_CHARS],
    }


def _validate_date(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}"
    return None


async def rag_search(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    filters: dict[str, Any] | None = None,
    *,
    indexer: NarrativeIndexer | None = None,
    embedder: ChunkEmbedder | None = None,
) -> dict[str, Any]:
    """Hybrid narrative search with citations (observation-style errors)."""
    if not query or not query.strip():
        return {"error": "query must be a non-empty string", "no_results": True, "chunks": []}
    for field in ("period_from", "period_to"):
        problem = _validate_date((filters or {}).get(field), field)
        if problem:
            return {"error": problem, "no_results": True, "chunks": []}
    search_config = load_yaml_config("rag")["search"]
    top_k = max(1, min(int(top_k), MAX_TOP_K))
    prefetch_limit = int(search_config.get("prefetch_limit", 20))
    rerank_cfg = search_config.get("rerank", {})
    rerank_enabled = bool(rerank_cfg.get("enabled", True))
    rerank_timeout_s = rerank_cfg.get("timeout_s")
    score_threshold = float(search_config.get("score_threshold", 0.0))

    embedder = embedder or ChunkEmbedder()
    indexer = indexer or NarrativeIndexer(embedder=embedder)
    dense_query = embedder.embed_dense([query], kind="query")[0]
    sparse_query = embedder.embed_sparse([query])[0]
    query_filter = _build_filter(filters)

    response = await indexer.client.query_points(
        collection_name=COLLECTION,
        prefetch=[
            models.Prefetch(
                query=dense_query,
                using=DENSE_VECTOR,
                limit=prefetch_limit,
                filter=query_filter,
            ),
            models.Prefetch(
                query=models.SparseVector(indices=sparse_query.indices, values=sparse_query.values),
                using=SPARSE_VECTOR,
                limit=prefetch_limit,
                filter=query_filter,
            ),
        ],
        query=_fusion_query(),
        limit=prefetch_limit,
        with_payload=True,
    )
    candidates = [point for point in response.points if point.payload]
    if not candidates:
        return {"chunks": [], "no_results": True, "message": "no data in the loaded corpus"}

    fusion_fallback = [(point, float(point.score or 0.0)) for point in candidates[:top_k]]
    if rerank_enabled:
        texts = [str(point.payload.get("text", "")) for point in candidates]  # type: ignore[union-attr]
        try:
            if rerank_timeout_s:
                # T-041/Q-22: bound the CPU cross-encoder so a starved node
                # cannot let it outlast the worker's step deadline (which would
                # abort rag_search mid-rerank -> zero citations). to_thread keeps
                # the blocking model call off the event loop while we time it.
                logits = await asyncio.wait_for(
                    asyncio.to_thread(embedder.rerank, query, texts),
                    float(rerank_timeout_s),
                )
            else:
                logits = embedder.rerank(query, texts)
        except TimeoutError:
            # Graceful degradation: the reranker did not finish in time. Fall
            # back to the RRF fusion order (already retrieved, best-first) so
            # citations survive; skip the rerank-sigmoid floor since fusion
            # scores are not comparable to it.
            get_logger(node="rag_search").warning(
                "rerank_timeout",
                timeout_s=float(rerank_timeout_s),
                candidates=len(candidates),
            )
            ranked = fusion_fallback
        else:
            scores = [_sigmoid(s) for s in logits]
            order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
            ranked = [(candidates[i], scores[i]) for i in order[:top_k]]
            if ranked and ranked[0][1] < score_threshold:
                get_logger(node="rag_search").info(
                    "below_threshold", best=round(ranked[0][1], 4), threshold=score_threshold
                )
                return {
                    "chunks": [],
                    "no_results": True,
                    "message": "nothing relevant enough in the loaded corpus",
                }
    else:
        ranked = fusion_fallback

    chunks = [
        {
            "text": str(point.payload.get("text", "")),  # type: ignore[union-attr]
            "score": round(score, 4),
            "citation": _citation(point.payload or {}, point.id),
        }
        for point, score in ranked
    ]
    return {"chunks": chunks, "no_results": False}
