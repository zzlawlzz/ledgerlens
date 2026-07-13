"""rag_search rerank soft-timeout + RRF-fusion fallback (T-041/Q-22).

On a starved node the CPU cross-encoder can outlast the worker's step deadline,
which aborts rag_search mid-rerank and yields zero citations. When
``search.rerank.timeout_s`` is set, a rerank overrun falls back to the fusion
order (already retrieved) so citations survive. Default (``timeout_s: null``)
keeps the exact synchronous behaviour these tests also pin down.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

import tools.rag.core as core
from rag.embedding import ChunkEmbedder
from rag.indexer import NarrativeIndexer
from tools.rag.core import rag_search


class _FakeSparse:
    indices = [1, 2]
    values = [0.5, 0.5]


class _FakeEmbedder:
    def __init__(self, rerank_fn: Callable[[str, list[str]], list[float]]) -> None:
        self._rerank_fn = rerank_fn
        self.rerank_calls = 0

    def embed_dense(self, texts: list[str], kind: str = "query") -> list[list[float]]:
        return [[0.1, 0.2, 0.3]]

    def embed_sparse(self, texts: list[str]) -> list[_FakeSparse]:
        return [_FakeSparse()]

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.rerank_calls += 1
        return self._rerank_fn(query, documents)


class _FakePoint:
    def __init__(self, pid: str, text: str, score: float) -> None:
        self.id = pid
        self.score = score
        self.payload = {
            "text": text,
            "ticker": "AAPL",
            "form_type": "10-K",
            "period_end": "2024-09-28",
            "section": "risk_factors",
            "source_url": "https://sec.gov/x",
            "filing_id": 1,
        }


class _FakeResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeClient:
    def __init__(self, points: list[_FakePoint]) -> None:
        self._points = points

    async def query_points(self, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._points)


class _FakeIndexer:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.client = _FakeClient(points)


def _patch_config(monkeypatch: Any, timeout_s: float | None) -> None:
    cfg = {
        "search": {
            "prefetch_limit": 20,
            "rerank": {"enabled": True, "timeout_s": timeout_s},
            "score_threshold": 0.3,
        }
    }
    monkeypatch.setattr(core, "load_yaml_config", lambda _name: cfg)


# Fusion order: p_high before p_low (scores 0.9 > 0.5). A reranker that reverses
# it lets us tell which ranking the result used.
def _fusion_points() -> list[_FakePoint]:
    return [_FakePoint("p_high", "alpha", 0.9), _FakePoint("p_low", "beta", 0.5)]


async def _run(embedder: _FakeEmbedder, points: list[_FakePoint]) -> dict[str, Any]:
    # The fakes are structural stand-ins; cast to satisfy strict typing (DI).
    return await rag_search(
        "risks",
        indexer=cast(NarrativeIndexer, _FakeIndexer(points)),
        embedder=cast(ChunkEmbedder, embedder),
    )


async def test_rerank_timeout_falls_back_to_fusion_order(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, timeout_s=0.01)

    def _slow(_q: str, _docs: list[str]) -> list[float]:
        time.sleep(0.4)  # outlasts the 0.01s soft budget
        return [-5.0, 5.0]  # would reorder p_low first — must NOT be applied

    embedder = _FakeEmbedder(_slow)
    result = await _run(embedder, _fusion_points())

    assert result["no_results"] is False
    chunk_ids = [c["citation"]["chunk_id"] for c in result["chunks"]]
    assert chunk_ids == ["p_high", "p_low"]  # RRF fusion order preserved
    assert result["chunks"][0]["score"] == 0.9  # fusion score, not sigmoid


async def test_rerank_within_timeout_uses_rerank_order(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, timeout_s=5.0)

    # Fast reranker that reverses fusion: p_low gets the higher logit.
    embedder = _FakeEmbedder(lambda _q, _docs: [0.0, 5.0])
    result = await _run(embedder, _fusion_points())

    assert result["no_results"] is False
    chunk_ids = [c["citation"]["chunk_id"] for c in result["chunks"]]
    assert chunk_ids == ["p_low", "p_high"]  # rerank order applied
    assert embedder.rerank_calls == 1


async def test_no_timeout_config_runs_synchronously(monkeypatch: Any) -> None:
    # timeout_s: null (the shipped default) -> synchronous rerank, unchanged path.
    _patch_config(monkeypatch, timeout_s=None)

    embedder = _FakeEmbedder(lambda _q, _docs: [0.0, 5.0])
    result = await _run(embedder, _fusion_points())

    chunk_ids = [c["citation"]["chunk_id"] for c in result["chunks"]]
    assert chunk_ids == ["p_low", "p_high"]
    assert embedder.rerank_calls == 1


async def test_below_threshold_preserved_on_timeout_path(monkeypatch: Any) -> None:
    # A completed (in-budget) rerank whose best score is under the floor must
    # still yield no_results — the anti-hallucination gate is not bypassed.
    _patch_config(monkeypatch, timeout_s=5.0)

    embedder = _FakeEmbedder(lambda _q, _docs: [-5.0, -5.0])  # sigmoid ~0.007
    result = await _run(embedder, _fusion_points())

    assert result["no_results"] is True
    assert result["chunks"] == []
