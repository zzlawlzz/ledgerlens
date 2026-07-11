"""Deterministic grounding guard tests (T-041 candidate 2)."""

from __future__ import annotations

from workers.react_worker import _strip_ungrounded

CITED = (
    "- Supply chain concentration in Asia raises disruption risk "
    "[AAPL 10-K 2025-09-27, risk_factors]"
)
UNCITED_FACT = "- Apple also faces tariffs of 25% announced in March 2026 affecting 40% of imports"
HEADING = "Main risks disclosed:"
GAP = "No data on litigation risks is present in the loaded corpus."


def test_uncited_factual_lines_are_dropped() -> None:
    answer = "\n".join([HEADING, CITED, UNCITED_FACT])
    result = _strip_ungrounded(answer)
    assert CITED in result
    assert HEADING in result
    assert "tariffs of 25%" not in result


def test_answer_without_any_markers_is_untouched() -> None:
    answer = "Apple's revenue was $416.161 billion in fiscal 2025, up 6.4% year over year."
    assert _strip_ungrounded(answer) == answer


def test_gap_statements_survive() -> None:
    answer = "\n".join([CITED, GAP])
    result = _strip_ungrounded(answer)
    assert GAP in result


def test_overaggressive_cut_falls_back_to_input() -> None:
    # A marker exists but every line is a long uncited factual paragraph:
    # dropping everything would leave nothing -> fail-open.
    long_fact = "The company reported 12 new factories across 3 countries in 2026, " * 3
    answer = long_fact + "\n" + long_fact + "\n[AAPL 10-K 2025-09-27, risk_factors]"
    result = _strip_ungrounded(answer)
    assert result  # never empty


def test_multi_sentence_cited_paragraph_kept_whole() -> None:
    paragraph = (
        "Apple depends on outsourcing partners concentrated in Asia; disruptions "
        "would materially hurt sales of 2 product lines [AAPL 10-K 2024-09-28, risk_factors]"
    )
    assert _strip_ungrounded(paragraph) == paragraph
