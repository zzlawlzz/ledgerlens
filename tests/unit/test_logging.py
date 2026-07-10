"""Unit tests for common.logging (T-004, CONTRACTS.md §4)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import structlog

from common.logging import MASK, bind_run_context, configure_logging, get_logger, unbind_run_context


@pytest.fixture(autouse=True)
def _clean_contextvars() -> Iterator[None]:
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _last_json_line(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert isinstance(data, dict)
    return data


def test_log_line_is_json_with_mandatory_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("test-service")
    get_logger(node="unit").info("something_happened", detail=1)
    data = _last_json_line(capsys)
    assert data["event"] == "something_happened"
    assert data["level"] == "info"
    assert data["service"] == "test-service"
    assert data["node"] == "unit"
    assert data["detail"] == 1
    assert "ts" in data


def test_run_context_is_bound_and_unbound(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("svc")
    bind_run_context(run_id="r-1", step_id="s-1", node="worker")
    get_logger().info("in_context")
    data = _last_json_line(capsys)
    assert data["run_id"] == "r-1"
    assert data["step_id"] == "s-1"
    assert data["node"] == "worker"

    unbind_run_context()
    get_logger().info("out_of_context")
    data = _last_json_line(capsys)
    assert "run_id" not in data
    assert "step_id" not in data


def test_secret_values_are_masked(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("svc")
    get_logger().info(
        "config_loaded",
        deepseek_api_key="sk-super-secret",
        payload={"nested": {"a2a_token": "tok-123"}, "plain": "visible"},
        items=[{"grafana_admin_password": "pw"}],
    )
    raw = capsys.readouterr().out
    for secret in ("sk-super-secret", "tok-123", '"pw"'):
        assert secret not in raw
    data = json.loads(raw.strip().splitlines()[-1])
    assert data["deepseek_api_key"] == MASK
    assert data["payload"]["nested"]["a2a_token"] == MASK
    assert data["payload"]["plain"] == "visible"
    assert data["items"][0]["grafana_admin_password"] == MASK


def test_usage_counters_are_not_masked(capsys: pytest.CaptureFixture[str]) -> None:
    """tokens_in/tokens_out are metrics, not credentials (T-004 fix)."""
    configure_logging("svc")
    get_logger().info("usage", usage={"tokens_in": 100, "tokens_out": 20}, token="cred")
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["usage"]["tokens_in"] == 100
    assert data["usage"]["tokens_out"] == 20
    assert data["token"] == MASK
