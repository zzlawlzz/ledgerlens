"""Rebuild the Qdrant index from stored sections (T-018; ``make reindex``).

Usage: ``uv run python -m ingestion.reindex``

Drops the collection (so the new embedding-model pin is written) and
re-embeds every company's sections from the database — no source traffic.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from common.db import get_session_factory
from common.logging import configure_logging, get_logger
from ingestion.embed import embed_company_sections
from rag.embedding import ChunkEmbedder
from rag.indexer import NarrativeIndexer


async def run_reindex() -> int:
    configure_logging("reindex")
    log = get_logger(node="reindex")
    embedder = ChunkEmbedder()
    indexer = NarrativeIndexer(embedder=embedder)
    await indexer.reindex_reset()
    session_factory = get_session_factory()
    async with session_factory() as session:
        company_ids = [
            int(row[0]) for row in await session.execute(text("SELECT id FROM companies"))
        ]
    total_chunks = 0
    for company_id in company_ids:
        report = await embed_company_sections(
            company_id, session_factory, embedder=embedder, indexer=indexer
        )
        total_chunks += report.chunks
    log.info("reindex_finished", companies=len(company_ids), chunks=total_chunks)
    print(f"reindexed {len(company_ids)} companies, {total_chunks} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run_reindex()))
