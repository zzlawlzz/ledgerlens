"""Unit tests for the RAG chunker (T-018)."""

from __future__ import annotations

from rag.chunking import Chunk, chunk_text, default_token_counter


def _words(n: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def _sentences(count: int, words_each: int, prefix: str) -> str:
    return " ".join(f"{_words(words_each, f'{prefix}{i}w').capitalize()}." for i in range(count))


def test_small_text_is_one_chunk() -> None:
    chunks = chunk_text("One short paragraph.\n\nAnother one.", chunk_size_tokens=100)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "Another one." in chunks[0].text


def test_chunks_respect_token_budget_and_overlap() -> None:
    paragraphs = "\n\n".join(_sentences(3, 10, f"p{i}") for i in range(12))
    chunks = chunk_text(paragraphs, chunk_size_tokens=100, overlap_tokens=30)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= 100 + 30  # budget plus tolerance for one unit
    # Overlap: the start of each next chunk repeats the tail of the previous.
    for previous, current in zip(chunks, chunks[1:], strict=False):
        first_paragraph = current.text.split("\n\n")[0]
        assert first_paragraph in previous.text


def test_sentences_are_never_split() -> None:
    text = _sentences(40, 12, "s")
    chunks = chunk_text(text, chunk_size_tokens=60, overlap_tokens=10)
    for chunk in chunks:
        # Every chunk ends exactly at a sentence boundary.
        assert chunk.text.rstrip().endswith(".")


def test_tiny_paragraphs_are_merged() -> None:
    text = "A real paragraph with enough words to stand on its own here.\n\nOK\n\nYes"
    chunks = chunk_text(text, chunk_size_tokens=100)
    assert len(chunks) == 1
    assert "OK" in chunks[0].text and "Yes" in chunks[0].text


def test_indices_are_sequential() -> None:
    text = "\n\n".join(_sentences(2, 15, f"p{i}") for i in range(20))
    chunks = chunk_text(text, chunk_size_tokens=80, overlap_tokens=20)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_custom_token_counter_is_used() -> None:
    calls: list[str] = []

    def counting(text: str) -> int:
        calls.append(text)
        return default_token_counter(text)

    chunk_text("Some text to count.\n\nMore text follows here.", token_counter=counting)
    assert calls  # the injected counter was actually consulted


def test_chunk_is_frozen_dataclass() -> None:
    chunk = Chunk(index=0, text="x", token_count=1)
    try:
        chunk.text = "y"  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised
