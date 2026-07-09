"""Error taxonomy (CONTRACTS.md §13).

Tool errors reach agents as observations (CONTRACTS.md §9), never as crashes;
budget overruns switch the run to partial synthesis instead of failing it.
"""

from __future__ import annotations


class LedgerLensError(Exception):
    """Base class for all domain errors."""


class ConfigError(LedgerLensError):
    """Invalid or incomplete configuration; raised at startup, not mid-run."""


class ToolError(LedgerLensError):
    """Tool call failed; `retryable` tells the agent whether retrying makes sense."""

    def __init__(self, message: str, *, retryable: bool = False, hint: str | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.hint = hint


class SourceUnavailableError(ToolError):
    """External data source is unreachable; ingestion degrades instead of crashing."""

    def __init__(self, message: str, *, retryable: bool = True, hint: str | None = None) -> None:
        super().__init__(message, retryable=retryable, hint=hint)


class BudgetExceededError(LedgerLensError):
    """Run budget (cost/tokens/iterations/time) exhausted; triggers partial synthesis."""


class GuardrailViolation(LedgerLensError):
    """Final answer contained investment advice after re-synthesis."""


class PlanningError(LedgerLensError):
    """Planner produced an unusable plan (schema violation, empty, cyclic deps)."""
