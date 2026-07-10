"""Unit tests for 10-K section extraction (T-010).

Real fixtures: three issuers with different EDGAR HTML generators
(AAPL — compact inline XBRL; MSFT — layout tables + per-page running
headers + multi-span headings; NVDA — plain). Start/end markers were
verified manually against the recorded documents.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from adapters.edgar.sections import (
    clean_10k_html,
    extract_sections_from_html,
    extract_sections_from_text,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "10k"

# Manually verified boundary markers per issuer (start prefix, end suffix window).
MARKERS = {
    "aapl": {
        "risk_factors": (
            "Item 1A. Risk Factors\nThe following summarizes factors",
            "investor confidence and employee retention.",
        ),
        "mdna": (
            "Item 7. Management’s Discussion and Analysis",
            "financial condition and operating results.",
        ),
    },
    "msft": {
        "risk_factors": (
            "ITEM 1A. RISK FACTORS\nOur operations and financial results",
            "worker representatives.",
        ),
        "mdna": (
            "ITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS",
            "Chief Accounting Officer",
        ),
    },
    "nvda": {
        "risk_factors": (
            "Item 1A. Risk Factors\nThe following risk factors",
            "corporate actions they desire.",
        ),
        "mdna": (
            "Item 7. Management's Discussion and Analysis",
            "accounting pronouncements.",
        ),
    },
}


def _load_10k(ticker: str) -> bytes:
    return gzip.decompress((FIXTURES_DIR / f"{ticker}_10k.htm.gz").read_bytes())


@pytest.mark.parametrize("ticker", sorted(MARKERS))
def test_both_sections_extracted_with_manual_markers(ticker: str) -> None:
    result = extract_sections_from_html(_load_10k(ticker))
    assert set(result.sections) == {"risk_factors", "mdna"}, result.warnings
    for section, (start_marker, end_marker) in MARKERS[ticker].items():
        text = result.sections[section]
        assert text.startswith(start_marker), f"{ticker}/{section} start: {text[:80]!r}"
        assert end_marker in text[-300:], f"{ticker}/{section} end: {text[-300:]!r}"
        assert 2_000 < len(text) < 1_500_000


# --- synthetic documents -----------------------------------------------------


def _paragraphs(sentence: str, chars: int) -> str:
    return "<p>" + (sentence * (chars // len(sentence) + 1))[:chars] + "</p>"


def _synthetic_10k(risk_chars: int = 6000, mdna_chars: int = 6000) -> str:
    return f"""
    <html><body>
    <p>INDEX</p>
    <p>Item 1A. Risk Factors</p>
    <p>Item 7. Management's Discussion and Analysis</p>
    <p>Item 1A. Risk Factors</p>
    {_paragraphs("Competition may harm our business. ", risk_chars)}
    <p>Item 1B. Unresolved Staff Comments</p>
    <p>Item 7. Management's Discussion and Analysis of Financial Condition</p>
    {_paragraphs("Revenue increased due to strong demand. ", mdna_chars)}
    <p>Item 8. Financial Statements and Supplementary Data</p>
    </body></html>
    """


def test_toc_trap_first_occurrence_is_skipped() -> None:
    """Item 1A appears in the TOC first — the body occurrence must win."""
    result = extract_sections_from_html(_synthetic_10k())
    risk = result.sections["risk_factors"]
    assert "Competition may harm" in risk
    # TOC occurrence has ~no body: extraction must not have picked it.
    assert not risk.startswith("Item 1A. Risk Factors\nItem 7.")


def test_short_stub_section_is_skipped_with_warning() -> None:
    """MD&A incorporated by reference (JPM/XOM style) -> warning, no section."""
    html = _synthetic_10k(mdna_chars=200)
    result = extract_sections_from_html(html)
    assert "mdna" not in result.sections
    assert any("mdna" in w and "skipped" in w for w in result.warnings)
    assert "risk_factors" in result.sections  # the other section is unaffected


def test_broken_html_returns_warnings_not_exceptions() -> None:
    result = extract_sections_from_html(b"\x00\xff<not <valid <<html")
    assert result.sections == {}
    assert result.warnings  # heading not found (or parse failure) — never raises


def test_business_section_extracted_only_with_flag() -> None:
    html = f"""
    <html><body>
    <p>Item 1. Business</p>
    {_paragraphs("We design products. ", 6000)}
    <p>Item 1A. Risk Factors</p>
    {_paragraphs("Risks exist. ", 6000)}
    <p>Item 1B. Unresolved Staff Comments</p>
    <p>Item 7. Management's Discussion and Analysis</p>
    {_paragraphs("Results improved. ", 6000)}
    <p>Item 8. Financial Statements</p>
    </body></html>
    """
    default = extract_sections_from_html(html)
    assert "business" not in default.sections
    with_flag = extract_sections_from_html(html, include_business=True)
    assert "business" in with_flag.sections
    assert "We design products." in with_flag.sections["business"]


def test_running_page_headers_do_not_end_section() -> None:
    """Bare 'Item 1A' page headers (MSFT style) must not truncate the body."""
    html = f"""
    <html><body>
    <p>Item 1A. Risk Factors</p>
    {_paragraphs("First page of risks. ", 3000)}
    <p>Item 1A</p>
    {_paragraphs("Second page of risks. ", 3000)}
    <p>Item 1B. Unresolved Staff Comments</p>
    </body></html>
    """
    result = extract_sections_from_html(html)
    risk = result.sections["risk_factors"]
    assert "First page of risks." in risk and "Second page of risks." in risk


def test_tables_are_linearized_with_pipe_cells() -> None:
    text = clean_10k_html(
        "<table><tr><td>Revenue</td><td>391,035</td></tr>"
        "<tr><td>Net income</td><td>93,736</td></tr></table>"
    )
    assert "Revenue | 391,035" in text
    assert "Net income | 93,736" in text


def test_scripts_styles_and_nbsp_are_cleaned() -> None:
    text = clean_10k_html(
        "<html><head><style>p{color:red}</style><script>alert(1)</script></head>"
        "<body><p>Total&nbsp;revenue</p></body></html>"
    )
    assert "alert" not in text and "color" not in text
    assert "Total revenue" in text


def test_extract_from_text_reports_missing_headings() -> None:
    result = extract_sections_from_text("no items here at all")
    assert result.sections == {}
    assert {"risk_factors: heading not found", "mdna: heading not found"} <= set(result.warnings)
