"""Narrative section chunking (T-018; config/rag.yaml).

Paragraph-first packing: paragraphs are grouped into chunks of about
``chunk_size_tokens`` with ``overlap_tokens`` of trailing context carried into
the next chunk. Sentences are never split; a paragraph longer than the chunk
budget is split on sentence boundaries. Token counting is injected — the real
counter comes from the embedding model's tokenizer (rag.embedding), tests may
use a plain word counter.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

TokenCounter = Callable[[str], int]

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
# Sentence end: punctuation followed by whitespace and an uppercase/digit start.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-Я0-9«\"(])")
_MIN_PARAGRAPH_CHARS = 40  # shorter fragments are glued to a neighbour


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    token_count: int


def default_token_counter(text: str) -> int:
    """Cheap fallback: whitespace words (≈0.75 of BPE tokens for English)."""
    return max(1, len(text.split()))


def _paragraphs(text: str) -> list[str]:
    """Paragraphs with tiny fragments merged into their predecessor."""
    raw = [p.strip() for p in _PARAGRAPH_SPLIT.split(text) if p.strip()]
    merged: list[str] = []
    for paragraph in raw:
        if merged and len(paragraph) < _MIN_PARAGRAPH_CHARS:
            merged[-1] = f"{merged[-1]}\n{paragraph}"
        elif not merged and len(paragraph) < _MIN_PARAGRAPH_CHARS:
            merged.append(paragraph)  # keep a leading heading as its own seed
        else:
            merged.append(paragraph)
    return merged


def _split_oversized(paragraph: str, budget: int, count: TokenCounter) -> list[str]:
    """Split one paragraph on sentence boundaries into <=budget pieces."""
    sentences = _SENTENCE_SPLIT.split(paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and count(candidate) > budget:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_text(
    text: str,
    *,
    chunk_size_tokens: int = 800,
    overlap_tokens: int = 150,
    token_counter: TokenCounter | None = None,
) -> list[Chunk]:
    """Pack paragraphs into overlapping chunks without breaking sentences."""
    count = token_counter or default_token_counter
    units: list[str] = []
    for paragraph in _paragraphs(text):
        if count(paragraph) > chunk_size_tokens:
            units.extend(_split_oversized(paragraph, chunk_size_tokens, count))
        else:
            units.append(paragraph)

    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = count(unit)
        if current and current_tokens + unit_tokens > chunk_size_tokens:
            body = "\n\n".join(current)
            chunks.append(Chunk(index=len(chunks), text=body, token_count=count(body)))
            # Overlap: carry trailing units into the next chunk.
            carried: list[str] = []
            carried_tokens = 0
            for previous in reversed(current):
                previous_tokens = count(previous)
                if carried_tokens + previous_tokens > overlap_tokens:
                    break
                carried.insert(0, previous)
                carried_tokens += previous_tokens
            current = carried
            current_tokens = carried_tokens
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        body = "\n\n".join(current)
        if chunks and count(body) < max(32, overlap_tokens // 2):
            # A tail made only of carried overlap adds no new content.
            tail_is_new = body not in chunks[-1].text
            if tail_is_new:
                chunks.append(Chunk(index=len(chunks), text=body, token_count=count(body)))
        else:
            chunks.append(Chunk(index=len(chunks), text=body, token_count=count(body)))
    return chunks
