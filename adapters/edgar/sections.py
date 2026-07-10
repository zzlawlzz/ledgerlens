"""EDGAR narrative section extraction from 10-K HTML (T-010).

Pipeline: parse (bs4+lxml) -> clean (drop script/style/ix:header, linearize
tables with " | " cells, normalize whitespace) -> locate section boundaries
by heading regexes on the cleaned text.

TOC trap: the first "Item 1A" occurrence is often the table of contents.
We take the occurrence with the largest body before the next item heading;
candidates with fewer than ``MIN_SECTION_CHARS`` are ignored.

Broken HTML never raises: the extractor returns whatever sections it could
find plus human-readable warnings — one bad filing must not stop the rest
(per-filing isolation is exercised by ingestion, T-011).
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

MIN_SECTION_CHARS = 2_000
MAX_SECTION_CHARS = 1_500_000

# Some issuers render headings inside layout tables — after linearization the
# title may carry a cell separator: "Item 1A. | Risk Factors". The optional
# escaped pipe below absorbs it; TOC rows also match but lose on body length.
_SEP = r"\s*[.—:\-–]?\s*(?:\|\s*)?"

# Section start patterns (case-insensitive, at line start after cleaning).
_SECTION_HEADINGS: dict[str, re.Pattern[str]] = {
    "risk_factors": re.compile(
        rf"^[ \t>]*item\s*1a{_SEP}risk\s+factors", re.IGNORECASE | re.MULTILINE
    ),
    "mdna": re.compile(
        rf"^[ \t>]*item\s*7{_SEP}management[’']?s?\s+discussion",
        re.IGNORECASE | re.MULTILINE,
    ),
    "business": re.compile(rf"^[ \t>]*item\s*1{_SEP}business\b", re.IGNORECASE | re.MULTILINE),
}

_SECTION_TITLES = {
    "risk_factors": "Item 1A. Risk Factors",
    "mdna": "Item 7. Management's Discussion and Analysis",
    "business": "Item 1. Business",
}

# Any item heading at line start — candidate end boundary of a section.
# The item number is captured: repeats of the CURRENT item (page running
# headers, e.g. bare "Item 1A" on every MSFT page) must not end the section.
_NEXT_ITEM = re.compile(
    r"^[ \t>]*item\s*(\d{1,2}[ab]?)\.?(?=[\s—:\-–|.])", re.IGNORECASE | re.MULTILINE
)

_SECTION_ITEM_NUMBER = {"risk_factors": "1a", "mdna": "7", "business": "1"}

_BLOCK_TAGS = ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article")

_WHITESPACE_RUN = re.compile(r"[ \t\xa0]+")
_NEWLINE_RUN = re.compile(r"\n{3,}")


@dataclass
class SectionExtractionResult:
    sections: dict[str, str] = field(default_factory=dict)  # section -> text
    warnings: list[str] = field(default_factory=list)


def clean_10k_html(html: str | bytes) -> str:
    """HTML -> plain text: no script/style/ix:header, tables linearized.

    Newlines are inserted only at block boundaries (p/div/li/h*/br/table rows);
    inline elements are glued together — issuers like MSFT wrap single words in
    multiple spans, and a naive per-element separator would split headings.
    """
    with warnings.catch_warnings():
        # 12MB inline-XBRL 10-Ks start with an <?xml?> declaration; the lxml
        # HTML parser handles them fine — silence the advisory warning.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(re.compile(r"^ix:header$", re.IGNORECASE)):
        tag.decompose()  # hidden inline-XBRL metadata block
    for table in soup.find_all("table"):
        rows: list[str] = []
        for tr in table.find_all("tr"):
            cells = [
                _WHITESPACE_RUN.sub(" ", cell.get_text(" ", strip=True))
                for cell in tr.find_all(["td", "th"])
            ]
            row = " | ".join(cell for cell in cells if cell)
            if row:
                rows.append(row)
        table.replace_with("\n" + "\n".join(rows) + "\n")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_after("\n")
    text = soup.get_text()
    text = text.replace("\xa0", " ").replace("​", "")
    text = _WHITESPACE_RUN.sub(" ", text)
    lines = (line.strip() for line in text.split("\n"))
    text = "\n".join(lines)
    return _NEWLINE_RUN.sub("\n\n", text).strip()


def _section_end(text: str, start: int, current_item: str) -> int:
    """Section body end: the next heading of a DIFFERENT item, or EOF."""
    for match in _NEXT_ITEM.finditer(text, pos=start):
        if match.group(1).lower() != current_item:
            return match.start()
    return len(text)


def _best_occurrence(
    text: str, pattern: re.Pattern[str], current_item: str
) -> tuple[int, int] | None:
    """(start, end) of the occurrence with the largest body — skips the TOC."""
    best: tuple[int, int] | None = None
    best_length = 0
    for match in pattern.finditer(text):
        end = _section_end(text, match.end(), current_item)
        length = end - match.start()
        if length > best_length:
            best = (match.start(), end)
            best_length = length
    return best


def extract_sections_from_text(
    text: str, *, include_business: bool = False
) -> SectionExtractionResult:
    result = SectionExtractionResult()
    wanted = ["risk_factors", "mdna"] + (["business"] if include_business else [])
    for section in wanted:
        occurrence = _best_occurrence(
            text, _SECTION_HEADINGS[section], _SECTION_ITEM_NUMBER[section]
        )
        if occurrence is None:
            result.warnings.append(f"{section}: heading not found")
            continue
        body = text[occurrence[0] : occurrence[1]].strip()
        if len(body) < MIN_SECTION_CHARS:
            result.warnings.append(
                f"{section}: only {len(body)} chars — looks like a TOC entry, skipped"
            )
            continue
        if len(body) > MAX_SECTION_CHARS:
            result.warnings.append(f"{section}: {len(body)} chars exceeds sanity limit, skipped")
            continue
        result.sections[section] = body
    return result


def extract_sections_from_html(
    html: str | bytes, *, include_business: bool = False
) -> SectionExtractionResult:
    """Never raises on malformed input — returns warnings instead."""
    try:
        text = clean_10k_html(html)
    except Exception as exc:  # noqa: BLE001 — one broken filing must not stop the batch
        result = SectionExtractionResult()
        result.warnings.append(f"HTML parsing failed: {exc}")
        return result
    return extract_sections_from_text(text, include_business=include_business)


def section_title(section: str) -> str:
    return _SECTION_TITLES.get(section, section)
