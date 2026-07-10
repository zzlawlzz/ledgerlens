"""Qdrant collection management and chunk indexing (T-018; ADR-2).

Collection ``narrative_chunks``: named dense vector (e5-large, cosine) + named
sparse vector (BM25). Point ids are deterministic (uuid5 of
``section_id:chunk_index``) so re-indexing is idempotent. The embedding model
name is pinned in a dedicated meta point — a model change without reindexing
fails fast at startup with a ``make reindex`` instruction.
"""

from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from common.config import Settings, get_settings
from common.errors import ConfigError
from common.logging import get_logger
from rag.chunking import Chunk
from rag.embedding import ChunkEmbedder

COLLECTION = "narrative_chunks"
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"
_NAMESPACE = uuid.UUID("6f7c1f52-8b1e-4c3a-9be1-1c0ffee0c0de")
META_POINT_ID = str(uuid.uuid5(_NAMESPACE, "collection-meta"))


def chunk_point_id(section_id: int, chunk_index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{section_id}:{chunk_index}"))


class NarrativeIndexer:
    def __init__(
        self,
        embedder: ChunkEmbedder | None = None,
        client: AsyncQdrantClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self.embedder = embedder or ChunkEmbedder()
        self.client = client or AsyncQdrantClient(url=settings.qdrant_url)
        self._log = get_logger(node="indexer")

    async def ensure_collection(self) -> None:
        """Create the collection on first use; verify the embedding model pin."""
        if not await self.client.collection_exists(COLLECTION):
            await self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=self.embedder.dim, distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config={SPARSE_VECTOR: models.SparseVectorParams()},
            )
            await self.client.upsert(
                collection_name=COLLECTION,
                points=[
                    models.PointStruct(
                        id=META_POINT_ID,
                        vector={DENSE_VECTOR: [0.0] * self.embedder.dim},
                        payload={
                            "meta": True,
                            "embedding_model": self.embedder.dense_model_name,
                            "sparse_model": self.embedder.sparse_model_name,
                        },
                    )
                ],
            )
            self._log.info("collection_created", collection=COLLECTION)
            return
        points = await self.client.retrieve(COLLECTION, ids=[META_POINT_ID])
        pinned = points[0].payload.get("embedding_model") if points and points[0].payload else None
        if pinned != self.embedder.dense_model_name:
            raise ConfigError(
                f"collection {COLLECTION} was built with embedding model {pinned!r}, "
                f"config now says {self.embedder.dense_model_name!r} — run `make reindex` "
                "to rebuild the index with the new model"
            )

    async def reindex_reset(self) -> None:
        """Drop the collection (make reindex): next ensure_collection recreates it."""
        if await self.client.collection_exists(COLLECTION):
            await self.client.delete_collection(COLLECTION)
            self._log.info("collection_dropped", collection=COLLECTION)

    async def index_section_chunks(
        self,
        section_id: int,
        chunks: list[Chunk],
        payload_base: dict[str, Any],
        dense_vectors: list[list[float]] | None = None,
    ) -> int:
        """Replace all points of a section with the given chunks (idempotent)."""
        await self.client.delete(
            collection_name=COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="section_id", match=models.MatchValue(value=section_id)
                        )
                    ]
                )
            ),
        )
        if not chunks:
            return 0
        texts = [chunk.text for chunk in chunks]
        dense = dense_vectors if dense_vectors is not None else self.embedder.embed_dense(texts)
        sparse = self.embedder.embed_sparse(texts)
        points = [
            models.PointStruct(
                id=chunk_point_id(section_id, chunk.index),
                vector={
                    DENSE_VECTOR: dense[i],
                    SPARSE_VECTOR: models.SparseVector(
                        indices=sparse[i].indices, values=sparse[i].values
                    ),
                },
                payload={
                    **payload_base,
                    "section_id": section_id,
                    "chunk_index": chunk.index,
                    "text": chunk.text,
                },
            )
            for i, chunk in enumerate(chunks)
        ]
        await self.client.upsert(collection_name=COLLECTION, points=points)
        return len(points)

    async def chunk_count(self) -> int:
        """Number of content points (the meta point excluded)."""
        result = await self.client.count(
            COLLECTION,
            count_filter=models.Filter(
                must_not=[models.FieldCondition(key="meta", match=models.MatchValue(value=True))]
            ),
            exact=True,
        )
        return int(result.count)
