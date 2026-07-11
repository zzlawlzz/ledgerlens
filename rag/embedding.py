"""Chunk embeddings (T-018; ADR-6): multilingual-e5-large dense + BM25 sparse via fastembed.

Models are lazy-loaded ONNX on CPU; weights are cached under
``data/cache/fastembed`` (gitignored, lives on the project drive — the system
drive has no room for ~2GB of model files). The embedding model also provides
the token counter used by the chunker, so chunk sizes are measured in the
same tokens the model consumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

from common.config import PROJECT_ROOT, load_yaml_config
from common.logging import get_logger

FASTEMBED_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "fastembed"


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


class ChunkEmbedder:
    """Dense + sparse embeddings with the configuration from config/rag.yaml."""

    def __init__(self, embedding_config: dict[str, Any] | None = None) -> None:
        config = embedding_config or load_yaml_config("rag")["embedding"]
        self.dense_model_name = str(config["model"])
        self.sparse_model_name = str(config["sparse_model"])
        self.dim = int(config["dim"])
        self._log = get_logger(node="embedding")

    @cached_property
    def _dense(self) -> Any:
        from fastembed import TextEmbedding

        self._log.info("loading_dense_model", model=self.dense_model_name)
        return TextEmbedding(self.dense_model_name, cache_dir=str(FASTEMBED_CACHE_DIR))

    @cached_property
    def _sparse(self) -> Any:
        from fastembed import SparseTextEmbedding

        self._log.info("loading_sparse_model", model=self.sparse_model_name)
        return SparseTextEmbedding(self.sparse_model_name, cache_dir=str(FASTEMBED_CACHE_DIR))

    def embed_dense(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        """Dense vectors; ``kind`` is "passage" (documents) or "query".

        E5-family models need the query:/passage: prefixes — fastembed applies
        them inside ``query_embed``/``passage_embed``; models without that
        distinction fall back to plain ``embed``.
        """
        method = getattr(self._dense, f"{kind}_embed", None) or self._dense.embed
        # Small batches: 800-token chunks at the default batch size blow the
        # ONNX arena past 1GB and OOM when the compose stack shares the RAM.
        vectors = [vector.tolist() for vector in method(texts, batch_size=8)]
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"dense model {self.dense_model_name} returned dim {len(vectors[0])}, "
                f"config expects {self.dim} — fix config/rag.yaml or re-run migrations"
            )
        return vectors

    def embed_sparse(self, texts: list[str]) -> list[SparseVector]:
        return [
            SparseVector(
                indices=[int(i) for i in emb.indices.tolist()],
                values=[float(v) for v in emb.values.tolist()],
            )
            for emb in self._sparse.embed(texts)
        ]

    @cached_property
    def _reranker(self) -> Any:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        rerank_config = load_yaml_config("rag")["search"]["rerank"]
        model_name = str(rerank_config["model"])
        self._log.info("loading_reranker", model=model_name)
        return TextCrossEncoder(model_name, cache_dir=str(FASTEMBED_CACHE_DIR))

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Cross-encoder relevance scores (raw logits) for query/document pairs."""
        return [float(score) for score in self._reranker.rerank(query, documents)]

    def token_counter(self) -> Any:
        """Token counter backed by the dense model's own tokenizer.

        Falls back to a word counter when fastembed internals change — the
        chunker only needs an approximation to size chunks consistently.
        """
        try:
            tokenizer = self._dense.model.tokenizer  # fastembed internal, best effort
        except AttributeError:
            self._log.warning("model_tokenizer_unavailable", fallback="word count")
            from rag.chunking import default_token_counter

            return default_token_counter

        def _count(text: str) -> int:
            return max(1, len(tokenizer.encode(text).ids))

        return _count
