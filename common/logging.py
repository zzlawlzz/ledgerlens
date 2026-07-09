"""structlog JSON logging with run/step correlation (CONTRACTS.md §4).

Mandatory fields: ts, level, event, service (+ run_id/step_id/node when bound).
``run_id``/``step_id`` live in contextvars and propagate to every log line and
TraceEvent published within the same task context.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_SECRET_KEY_MARKERS = ("api_key", "apikey", "password", "token", "secret", "encryption_key")
MASK = "***"


def _mask_secrets_in(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (MASK if _is_secret_key(key) and inner else _mask_secrets_in(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_mask_secrets_in(item) for item in value]
    return value


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and any(marker in key.lower() for marker in _SECRET_KEY_MARKERS)


def _mask_secrets_processor(
    logger: structlog.typing.WrappedLogger,
    method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    masked = _mask_secrets_in(dict(event_dict))
    assert isinstance(masked, dict)
    return masked


def configure_logging(service: str, *, level: int = logging.INFO) -> None:
    """Configure structlog for JSON output to stdout; idempotent per process."""

    def _add_service(
        logger: structlog.typing.WrappedLogger,
        method_name: str,
        event_dict: structlog.typing.EventDict,
    ) -> structlog.typing.EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            _add_service,
            _mask_secrets_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(**initial_values: Any) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(**initial_values)
    return logger


def bind_run_context(
    run_id: str | None = None, step_id: str | None = None, node: str | None = None
) -> None:
    """Bind correlation ids to the current context (CONTRACTS.md §4)."""
    values = {
        key: value
        for key, value in (("run_id", run_id), ("step_id", step_id), ("node", node))
        if value is not None
    }
    if values:
        structlog.contextvars.bind_contextvars(**values)


def unbind_run_context(*keys: str) -> None:
    structlog.contextvars.unbind_contextvars(*(keys or ("run_id", "step_id", "node")))


def current_context() -> dict[str, Any]:
    return dict(structlog.contextvars.get_contextvars())
