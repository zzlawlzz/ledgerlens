"""EDGAR XBRL fact extraction and metric normalization (T-009).

Turns a ``companyfacts`` document into ``FinancialFact`` DTOs in canonical
metrics (common.metrics). Selection rules (BACKLOG T-009):

- tag chains are walked in priority order; the first tag yielding at least
  one valid entry wins for the whole metric;
- annual: ``fp == FY`` + form 10-K; duration entries must span a full year
  (330–400 days) — instant (balance) entries have no ``start``;
- quarterly: ``fp in {Q1,Q2,Q3}`` + form 10-Q; duration entries must span a
  single quarter (60–120 days) — this drops 6/9-month YTD rows that EDGAR
  reports under the same fiscal period; Q4 is never synthesized (v1);
- restatements: the same (metric, period) may arrive from several filings —
  every occurrence is kept with its own accession; the "latest version"
  choice belongs to the ``latest_facts`` view (CONTRACTS §6);
- validation: numeric value, period_end year within 2000–2035, known unit;
  suspicious values (negative revenue) produce a warning but are kept.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from common.metrics import METRICS
from common.models import FinancialFact

UNIT_NORMALIZATION = {"USD": "USD", "shares": "shares", "USD/shares": "USD/share"}
ANNUAL_DURATION_DAYS = (330, 400)
QUARTER_DURATION_DAYS = (60, 120)
PERIOD_END_YEAR_RANGE = (2000, 2035)
QUARTER_PERIODS = ("Q1", "Q2", "Q3")


@dataclass
class ExtractionStats:
    """Per-run material for dictionary expansion (logged by the adapter)."""

    found: dict[str, str] = field(default_factory=dict)  # metric -> tag used
    missing: list[str] = field(default_factory=list)
    facts_total: int = 0
    skipped_unknown_filing: int = 0
    warnings: list[str] = field(default_factory=list)


def _split_tag(tag: str) -> tuple[str, str]:
    if ":" in tag:
        namespace, name = tag.split(":", 1)
        return namespace, name
    return "us-gaap", tag


def _parse_date(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _entry_matches_period_rules(
    entry: dict[str, Any], end: date, fiscal_period: str, form: str
) -> bool:
    start = _parse_date(entry.get("start")) if "start" in entry else None
    if fiscal_period == "FY":
        if not form.startswith("10-K"):
            return False
        if start is None:  # instant (balance sheet) value
            return True
        duration = (end - start).days
        return ANNUAL_DURATION_DAYS[0] <= duration <= ANNUAL_DURATION_DAYS[1]
    if fiscal_period in QUARTER_PERIODS:
        if not form.startswith("10-Q"):
            return False
        if start is None:
            return True
        duration = (end - start).days
        return QUARTER_DURATION_DAYS[0] <= duration <= QUARTER_DURATION_DAYS[1]
    return False


def _facts_from_tag(
    metric_canonical: str,
    tag: str,
    tag_data: dict[str, Any],
    allowed_accessions: set[str] | None,
    stats: ExtractionStats,
) -> list[FinancialFact]:
    facts: list[FinancialFact] = []
    seen: set[tuple[str, str, date, str, str]] = set()
    for unit_raw, entries in tag_data.get("units", {}).items():
        unit = UNIT_NORMALIZATION.get(unit_raw)
        if unit is None:
            continue
        for entry in entries:
            fiscal_period = str(entry.get("fp") or "")
            form = str(entry.get("form") or "")
            accession = str(entry.get("accn") or "")
            end = _parse_date(entry.get("end"))
            if end is None or not accession:
                stats.warnings.append(f"{metric_canonical}: entry without end/accn skipped")
                continue
            if not PERIOD_END_YEAR_RANGE[0] <= end.year <= PERIOD_END_YEAR_RANGE[1]:
                stats.warnings.append(
                    f"{metric_canonical}: period_end {end} outside sane range, skipped"
                )
                continue
            try:
                value = Decimal(str(entry["val"]))
            except (KeyError, InvalidOperation, TypeError):
                stats.warnings.append(
                    f"{metric_canonical}: non-numeric value {entry.get('val')!r} skipped"
                )
                continue
            if not _entry_matches_period_rules(entry, end, fiscal_period, form):
                continue
            if allowed_accessions is not None and accession not in allowed_accessions:
                stats.skipped_unknown_filing += 1
                continue
            dedup_key = (accession, metric_canonical, end, fiscal_period, unit)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            if metric_canonical == "revenue" and value < 0:
                stats.warnings.append(f"revenue < 0 for {end} ({value}) — kept, but suspicious")
            start = _parse_date(entry.get("start")) if "start" in entry else None
            facts.append(
                FinancialFact(
                    source_filing_id=accession,
                    metric=metric_canonical,
                    value=value,
                    unit=unit,
                    period_start=start,
                    period_end=end,
                    # EDGAR's `fy` is the REPORT's fiscal year, so comparatives
                    # inherit the wrong label (a FY2023 figure inside the
                    # FY2025 10-K arrives as fy=2025). Derived from the fact's
                    # own period below, once the company FYE month is known.
                    fiscal_year=None,
                    fiscal_period=fiscal_period,
                    standard="US-GAAP",
                    source_tag=tag,
                )
            )
    _assign_fiscal_years(facts)
    return facts


def _assign_fiscal_years(facts: list[FinancialFact]) -> None:
    """Label each fact with the fiscal year its own period belongs to.

    The company's fiscal-year-end month is the modal month of annual (FY)
    period ends; a period ending after the FYE month falls into the NEXT
    fiscal year (NVDA Q1 FY2026 ends in April 2025; AAPL Q1 FY2025 ends in
    December 2024).
    """
    fy_months = [fact.period_end.month for fact in facts if fact.fiscal_period == "FY"]
    if not fy_months:
        return
    fye_month = Counter(fy_months).most_common(1)[0][0]
    for fact in facts:
        year = fact.period_end.year
        fact.fiscal_year = year if fact.period_end.month <= fye_month else year + 1


def extract_financial_facts(
    companyfacts: dict[str, Any],
    *,
    allowed_accessions: set[str] | None = None,
) -> tuple[list[FinancialFact], ExtractionStats]:
    """Extract canonical facts; the first tag with data wins per metric.

    ``allowed_accessions`` limits facts to known filings (FK integrity in
    ingestion); skipped occurrences are counted in stats.
    """
    stats = ExtractionStats()
    all_facts: list[FinancialFact] = []
    namespaces = companyfacts.get("facts", {})
    for metric in METRICS.values():
        metric_facts: list[FinancialFact] = []
        for tag in metric.gaap_tags:
            namespace, name = _split_tag(tag)
            tag_data = namespaces.get(namespace, {}).get(name)
            if not tag_data:
                continue
            metric_facts = _facts_from_tag(
                metric.canonical, tag, tag_data, allowed_accessions, stats
            )
            if metric_facts:
                stats.found[metric.canonical] = tag
                break
        if metric_facts:
            all_facts.extend(metric_facts)
        else:
            stats.missing.append(metric.canonical)
    stats.facts_total = len(all_facts)
    return all_facts, stats
