"""Embedding step of ingestion (T-018): sections -> chunks -> Qdrant (+pgvector).

Per company: read its narrative sections with filing/company payload, chunk
them with the embedding model's tokenizer, replace the section's rows in
``section_chunks`` and its points in Qdrant. Deterministic ids keep re-runs
idempotent; ``write_pgvector`` additionally stores dense vectors in the
``section_chunks.embedding`` column (benchmark material, T-037).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.config import load_yaml_config
from common.logging import get_logger
from rag.chunking import chunk_text
from rag.embedding import ChunkEmbedder
from rag.indexer import NarrativeIndexer

_SELECT_SECTIONS = text(
    """
    SELECT fs.id AS section_id, fs.section, fs.text,
           f.id AS filing_id, f.form_type, f.period_end, f.source_url,
           c.id AS company_id, c.ticker
    FROM filing_sections fs
    JOIN filings f ON f.id = fs.filing_id
    JOIN companies c ON c.id = f.company_id
    WHERE c.id = :company_id
    ORDER BY fs.id
    """
)

_DELETE_CHUNKS = text("DELETE FROM section_chunks WHERE section_id = :section_id")

_INSERT_CHUNK = text(
    """
    INSERT INTO section_chunks (section_id, chunk_index, text, embedding)
    VALUES (:section_id, :chunk_index, :text, :embedding)
    """
)


@dataclass
class EmbedReport:
    sections: int = 0
    chunks: int = 0


async def embed_company_sections(
    company_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    embedder: ChunkEmbedder | None = None,
    indexer: NarrativeIndexer | None = None,
    write_pgvector: bool = False,
) -> EmbedReport:
    """Chunk, embed and index every narrative section of one company."""
    log = get_logger(node="ingestion_embed")
    chunking_config = load_yaml_config("rag")["chunking"]
    embedder = embedder or ChunkEmbedder()
    indexer = indexer or NarrativeIndexer(embedder=embedder)
    await indexer.ensure_collection()
    token_counter = embedder.token_counter()
    report = EmbedReport()

    async with session_factory() as session:
        rows = (await session.execute(_SELECT_SECTIONS, {"company_id": company_id})).all()
        for row in rows:
            chunks = chunk_text(
                row.text,
                chunk_size_tokens=int(chunking_config["chunk_size_tokens"]),
                overlap_tokens=int(chunking_config["overlap_tokens"]),
                token_counter=token_counter,
            )
            texts = [chunk.text for chunk in chunks]
            dense = embedder.embed_dense(texts) if texts else []
            await session.execute(_DELETE_CHUNKS, {"section_id": row.section_id})
            for i, chunk in enumerate(chunks):
                embedding_literal: Any = (
                    "[" + ",".join(f"{v:.7f}" for v in dense[i]) + "]" if write_pgvector else None
                )
                await session.execute(
                    _INSERT_CHUNK,
                    {
                        "section_id": row.section_id,
                        "chunk_index": chunk.index,
                        "text": chunk.text,
                        "embedding": embedding_literal,
                    },
                )
            payload_base = {
                "company_id": row.company_id,
                "ticker": row.ticker,
                "filing_id": row.filing_id,
                "form_type": row.form_type,
                "period_end": str(row.period_end) if row.period_end else None,
                "section": row.section,
                "source_url": row.source_url,
            }
            indexed = await indexer.index_section_chunks(
                row.section_id, chunks, payload_base, dense_vectors=dense
            )
            report.sections += 1
            report.chunks += indexed
        await session.commit()
    log.info("company_embedded", company_id=company_id, **report.__dict__)
    return report
