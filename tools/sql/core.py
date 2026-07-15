"""sql_query and schema_introspect tool cores (T-012; CONTRACTS.md §9).

Pure library functions — the MCP wrapper arrives in T-027. Design goals:

- safety: read-only role ``app_ro`` + sqlglot validation (single SELECT/WITH
  statement, no INTO/locks) + 10s statement timeout — a double barrier;
- self-correction: errors come back as *observations* (``{error, hint,
  schema_excerpt}``), never exceptions — hints name the closest identifiers
  and point at ``schema_introspect``.
"""

from __future__ import annotations

import difflib
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlglot import expressions as exp

from common.db import get_ro_session_factory
from common.metrics import METRICS

DEFAULT_ROW_LIMIT = 50
MAX_ROW_LIMIT = 200
STATEMENT_TIMEOUT_MS = 10_000
MAX_CELL_CHARS = 500

# Tables the agent may know about (grants for app_ro match this list).
KNOWN_TABLES = ("latest_facts", "companies", "filings", "financial_facts", "filing_sections")

_TABLE_DESCRIPTIONS = {
    "latest_facts": (
        "PRIMARY entry point: latest version of every financial fact "
        "(restatements resolved), joined with ticker/company_name/form_type"
    ),
    "companies": "Company registry (source, external_id, ticker, name, sector)",
    "filings": "Filing metadata (form_type, period_end, filed_at, source_url)",
    "financial_facts": "Raw facts incl. superseded restatements — prefer latest_facts",
    "filing_sections": "Narrative sections of filings (risk_factors, mdna) — full text",
}

_COLUMN_DESCRIPTIONS = {
    "metric": "canonical metric name — see `metrics` list",
    "value": "numeric value in `unit`",
    "fiscal_period": "'FY' or 'Q1'..'Q3'",
    "period_end": "reporting period end date",
    "ticker": "exchange ticker, e.g. 'AAPL'",
    "company_name": "company display name",
}

EXAMPLE_QUERIES = [
    "SELECT ticker, period_end, value, unit FROM latest_facts "
    "WHERE ticker = 'AAPL' AND metric = 'revenue' AND fiscal_period = 'FY' "
    "ORDER BY period_end DESC LIMIT 5",
    "SELECT ticker, period_end, value FROM latest_facts "
    "WHERE metric = 'revenue' AND fiscal_period = 'FY' AND ticker IN ('AAPL', 'MSFT') "
    "ORDER BY ticker, period_end",
    "SELECT r.ticker, r.period_end, ROUND(n.value / NULLIF(r.value, 0) * 100, 1) AS net_margin_pct "
    "FROM latest_facts r JOIN latest_facts n ON n.company_id = r.company_id "
    "AND n.period_end = r.period_end AND n.fiscal_period = r.fiscal_period "
    "WHERE r.metric = 'revenue' AND n.metric = 'net_income' AND r.fiscal_period = 'FY' "
    "ORDER BY r.ticker, r.period_end",
    # Price dynamics: close_price is a daily metric (one row per trading day),
    # so aggregate per month instead of returning hundreds of daily rows.
    "SELECT date_trunc('month', period_end)::date AS month, "
    "MIN(value) AS low, MAX(value) AS high, "
    "(array_agg(value ORDER BY period_end DESC))[1] AS month_end_close "
    "FROM latest_facts WHERE ticker = 'SBER' AND metric = 'close_price' "
    "GROUP BY 1 ORDER BY 1",
]

_METRIC_EQ = re.compile(r"metric\s*=\s*'([^']+)'", re.IGNORECASE)
_UNDEFINED_COLUMN = re.compile(r'column "?([\w.]+)"? does not exist', re.IGNORECASE)
_UNDEFINED_TABLE = re.compile(r'relation "([\w.]+)" does not exist', re.IGNORECASE)


def _schema_excerpt() -> str:
    return (
        "Tables: latest_facts (MAIN entry: company_id, ticker, company_name, metric, "
        "value, unit, period_start, period_end, fiscal_year, fiscal_period, standard, "
        "form_type, filed_at, source_url); companies; filings; financial_facts (raw, "
        "prefer latest_facts); filing_sections. Call schema_introspect for details "
        "and example queries."
    )


def _error_observation(error: str, hint: str) -> dict[str, Any]:
    return {"error": error, "hint": hint, "schema_excerpt": _schema_excerpt()}


def _closest(unknown: str, candidates: list[str]) -> list[str]:
    return difflib.get_close_matches(unknown, candidates, n=3, cutoff=0.4)


def _validate_and_rewrite(sql: str, row_limit: int) -> tuple[str, int] | dict[str, Any]:
    """Return (rewritten SQL, effective row limit), or an error observation.

    The query is given ``effective + 1`` rows to fetch — the extra row only
    signals truncation and is never returned to the caller.
    """
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as error:
        return _error_observation(
            f"SQL parse error: {error}",
            "Fix the syntax; use plain PostgreSQL SELECT. "
            "Call schema_introspect to see tables and example queries.",
        )
    parsed: list[exp.Expr] = [s for s in statements if s is not None]
    if len(parsed) != 1:
        return _error_observation(
            f"expected exactly one statement, got {len(parsed)}",
            "Send a single SELECT statement without trailing semicolons or extra commands.",
        )
    statement = parsed[0]
    if not isinstance(statement, exp.Query):
        return _error_observation(
            f"only SELECT/WITH queries are allowed, got {statement.key.upper()}",
            "This tool is read-only. Rewrite the request as a SELECT over latest_facts.",
        )
    # A top-level SELECT/WITH can still smuggle writes through a data-modifying
    # CTE, e.g. ``WITH t AS (INSERT ... RETURNING *) SELECT * FROM t``. The
    # ``app_ro`` role blocks these at execution, but reject them here too so the
    # parse barrier holds and the agent gets a self-correcting hint rather than
    # an opaque permission-denied.
    dml = statement.find(exp.Insert, exp.Update, exp.Delete, exp.Merge)
    if dml is not None:
        return _error_observation(
            f"data-modifying statements are not allowed ({dml.key.upper()} found)",
            "This tool is read-only, including inside CTEs. Rewrite the request as "
            "a plain SELECT over latest_facts.",
        )
    select = statement
    if isinstance(select, exp.Select) and select.args.get("into"):
        return _error_observation(
            "SELECT INTO is not allowed",
            "Remove INTO — results are returned directly, not stored.",
        )
    if select.args.get("locks"):
        return _error_observation(
            "locking clauses (FOR UPDATE/SHARE) are not allowed",
            "Remove the locking clause — this is a read-only tool.",
        )
    effective_limit = row_limit
    existing = select.args.get("limit")
    if existing is not None:
        try:
            effective_limit = min(int(existing.expression.this), row_limit)
        except (TypeError, ValueError, AttributeError):
            effective_limit = row_limit
    rewritten = select.limit(effective_limit + 1)
    return rewritten.sql(dialect="postgres"), effective_limit


def _serialize_cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + "…"
    return value


def _db_error_observation(error: DBAPIError) -> dict[str, Any]:
    message = str(error.orig) if error.orig else str(error)
    first_line = message.splitlines()[0] if message else "database error"
    column_match = _UNDEFINED_COLUMN.search(message)
    if column_match:
        unknown = column_match.group(1).split(".")[-1]
        known_columns = [
            "ticker",
            "company_name",
            "metric",
            "value",
            "unit",
            "period_end",
            "period_start",
            "fiscal_year",
            "fiscal_period",
            "form_type",
            "filed_at",
            "source_url",
            "company_id",
            "standard",
        ]
        suggestions = _closest(unknown, known_columns + list(METRICS))
        hint = (
            f"column '{unknown}' does not exist"
            + (f"; did you mean: {', '.join(suggestions)}?" if suggestions else "")
            + " If you meant a financial metric, filter latest_facts with"
            " metric = '<name>' instead of using it as a column."
            " Call schema_introspect for the full schema."
        )
        return _error_observation(first_line, hint)
    table_match = _UNDEFINED_TABLE.search(message)
    if table_match:
        unknown = table_match.group(1)
        suggestions = _closest(unknown, list(KNOWN_TABLES))
        hint = (
            f"table '{unknown}' does not exist"
            + (f"; did you mean: {', '.join(suggestions)}?" if suggestions else "")
            + " Call schema_introspect to list available tables."
        )
        return _error_observation(first_line, hint)
    if "statement timeout" in message or "QueryCanceled" in type(error.orig).__name__ + message:
        return _error_observation(
            f"query exceeded the {STATEMENT_TIMEOUT_MS // 1000}s timeout",
            "Simplify the query: add WHERE filters (ticker, metric, fiscal_period), "
            "avoid cross joins, and rely on latest_facts.",
        )
    if "permission denied" in message:
        return _error_observation(
            first_line,
            "This tool is read-only and sees only the domain tables. "
            "Rewrite the query as a SELECT over latest_facts.",
        )
    return _error_observation(
        first_line, "Adjust the query; call schema_introspect if unsure about the schema."
    )


def _empty_result_hint(sql: str) -> str | None:
    metric_match = _METRIC_EQ.search(sql)
    if metric_match and metric_match.group(1) not in METRICS:
        return (
            f"metric '{metric_match.group(1)}' is not a canonical metric. "
            f"Available metrics: {', '.join(sorted(METRICS))}."
        )
    return None


async def sql_query(
    sql: str,
    row_limit: int = DEFAULT_ROW_LIMIT,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, Any]:
    """Execute a read-only query under app_ro; errors become observations."""
    row_limit = max(1, min(int(row_limit), MAX_ROW_LIMIT))
    validated = _validate_and_rewrite(sql, row_limit)
    if isinstance(validated, dict):
        return validated
    rewritten, effective_limit = validated
    factory = session_factory or get_ro_session_factory()
    try:
        async with factory() as session:
            await session.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            result = await session.execute(text(rewritten))
            columns = list(result.keys())
            raw_rows = result.fetchall()
    except DBAPIError as error:
        return _db_error_observation(error)
    truncated = len(raw_rows) > effective_limit
    raw_rows = raw_rows[:effective_limit]
    rows = [[_serialize_cell(cell) for cell in row] for row in raw_rows]
    response: dict[str, Any] = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
    if not rows:
        hint = _empty_result_hint(sql)
        if hint:
            response["hint"] = hint
    return response


_INTROSPECT_CACHE: dict[str, Any] | None = None


async def schema_introspect(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Tables + canonical metrics + example queries; cached per process."""
    global _INTROSPECT_CACHE
    if _INTROSPECT_CACHE is not None and not refresh:
        return _INTROSPECT_CACHE
    factory = session_factory or get_ro_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ANY(:tables) "
                "ORDER BY table_name, ordinal_position"
            ),
            {"tables": list(KNOWN_TABLES)},
        )
        columns_by_table: dict[str, list[dict[str, str]]] = {}
        for table_name, column_name, data_type in rows:
            columns_by_table.setdefault(table_name, []).append(
                {
                    "name": column_name,
                    "type": data_type,
                    "description": _COLUMN_DESCRIPTIONS.get(column_name, ""),
                }
            )
    _INTROSPECT_CACHE = {
        "tables": [
            {
                "name": table,
                "description": _TABLE_DESCRIPTIONS.get(table, ""),
                "columns": columns_by_table.get(table, []),
            }
            for table in KNOWN_TABLES
        ],
        "metrics": [
            {
                "name": metric.canonical,
                "description": metric.description,
                "unit_hint": metric.unit_hint,
            }
            for metric in METRICS.values()
        ],
        "examples": list(EXAMPLE_QUERIES),
    }
    return _INTROSPECT_CACHE


def reset_introspect_cache() -> None:
    global _INTROSPECT_CACHE
    _INTROSPECT_CACHE = None
