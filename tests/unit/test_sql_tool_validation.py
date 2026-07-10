"""Unit tests for sql_query validation (T-012) — no database needed."""

from __future__ import annotations

from typing import Any

import pytest

from tools.sql.core import _validate_and_rewrite, sql_query


class _ExplodingFactory:
    """Fails the test if the tool reaches the database after a validation error."""

    def __call__(self) -> Any:
        raise AssertionError("query must be rejected before execution")


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE companies SET name = 'x'",
        "DELETE FROM financial_facts",
        "DROP TABLE companies",
        "INSERT INTO companies (source, external_id, name) VALUES ('a', 'b', 'c')",
        "COPY companies TO '/tmp/x'",
        "TRUNCATE companies",
    ],
)
async def test_write_statements_rejected_before_execution(sql: str) -> None:
    observation = await sql_query(sql, session_factory=_ExplodingFactory())  # type: ignore[arg-type]
    assert "error" in observation
    assert "read-only" in observation["hint"] or "SELECT" in observation["hint"]
    assert "schema_excerpt" in observation


async def test_multiple_statements_rejected() -> None:
    observation = await sql_query(
        "SELECT 1; DELETE FROM companies",
        session_factory=_ExplodingFactory(),  # type: ignore[arg-type]
    )
    assert "error" in observation and "one statement" in observation["error"]


async def test_select_into_and_locks_rejected() -> None:
    into = await sql_query(
        "SELECT * INTO new_table FROM companies",
        session_factory=_ExplodingFactory(),  # type: ignore[arg-type]
    )
    assert "error" in into and "INTO" in into["error"]
    lock = await sql_query(
        "SELECT * FROM companies FOR UPDATE",
        session_factory=_ExplodingFactory(),  # type: ignore[arg-type]
    )
    assert "error" in lock and "lock" in lock["error"].lower()


async def test_parse_error_gets_teaching_hint() -> None:
    observation = await sql_query(
        "SELEC ticker FRM latest_facts",
        session_factory=_ExplodingFactory(),  # type: ignore[arg-type]
    )
    assert "error" in observation
    assert "schema_introspect" in observation["hint"]


def test_limit_is_injected_when_missing() -> None:
    validated = _validate_and_rewrite("SELECT * FROM latest_facts", row_limit=50)
    assert isinstance(validated, tuple)
    rewritten, effective = validated
    assert "LIMIT 51" in rewritten  # effective + 1 extra row for truncation detection
    assert effective == 50


def test_user_limit_smaller_than_row_limit_is_respected() -> None:
    validated = _validate_and_rewrite("SELECT * FROM latest_facts LIMIT 10", row_limit=50)
    assert isinstance(validated, tuple)
    rewritten, effective = validated
    assert effective == 10
    assert "LIMIT 11" in rewritten


def test_user_limit_larger_than_row_limit_is_clamped() -> None:
    validated = _validate_and_rewrite("SELECT * FROM latest_facts LIMIT 9999", row_limit=50)
    assert isinstance(validated, tuple)
    _, effective = validated
    assert effective == 50


def test_with_cte_select_is_allowed() -> None:
    validated = _validate_and_rewrite(
        "WITH revenue AS (SELECT * FROM latest_facts WHERE metric = 'revenue') "
        "SELECT ticker, value FROM revenue",
        row_limit=50,
    )
    assert isinstance(validated, tuple)
