"""Unit tests for the error taxonomy (T-004, CONTRACTS.md §13)."""

from common.errors import (
    BudgetExceededError,
    ConfigError,
    GuardrailViolation,
    LedgerLensError,
    PlanningError,
    SourceUnavailableError,
    ToolError,
)


def test_taxonomy_inherits_from_base() -> None:
    for exc_type in (
        ConfigError,
        ToolError,
        SourceUnavailableError,
        BudgetExceededError,
        GuardrailViolation,
        PlanningError,
    ):
        assert issubclass(exc_type, LedgerLensError)
        assert issubclass(exc_type, Exception)


def test_tool_error_retryable_flag() -> None:
    assert ToolError("x").retryable is False
    assert ToolError("x", retryable=True).retryable is True
    assert ToolError("x").hint is None
    assert ToolError("x", hint="call schema_introspect").hint == "call schema_introspect"


def test_source_unavailable_is_retryable_tool_error() -> None:
    error = SourceUnavailableError("edgar down")
    assert isinstance(error, ToolError)
    assert error.retryable is True
    assert str(error) == "edgar down"
    assert SourceUnavailableError("x", retryable=False).retryable is False
