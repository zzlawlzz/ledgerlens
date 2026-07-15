"""Demo Telegram notification unit tests (T-044).

Pure formatter + gating exercised directly; delivery is stubbed so no network
and no secrets are touched.
"""

from __future__ import annotations

from typing import Any

import pytest

from common.config import Settings
from orchestrator import demo_notify
from orchestrator.demo_limits import DemoLimiter


def _demo_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "budget_profile": "demo",
        "demo_notify_telegram": True,
        "telegram_bot_token": "tok",
        "telegram_chat_id": "42",
    }
    base.update(over)
    return Settings(**base)


# ---------------------------------------------------------------- formatter


def test_format_includes_question_cost_and_daily_total() -> None:
    text = demo_notify.format_run_notification(
        question="What was AAPL revenue in FY2025?",
        mode="us",
        status="succeeded",
        partial=False,
        cost_usd=0.0123,
        tokens_in=1000,
        tokens_out=200,
        latency_ms=8400,
        daily_spent=0.25,
        daily_cap=1.5,
    )
    assert "✅" in text and "succeeded" in text
    assert "What was AAPL revenue" in text
    assert "$0.0123" in text and "1000+200" in text and "8.4s" in text
    assert "today: $0.2500 / $1.50" in text


def test_format_partial_and_failed_labels() -> None:
    partial = demo_notify.format_run_notification(
        question="q",
        mode="us",
        status="succeeded",
        partial=True,
        cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        daily_spent=None,
        daily_cap=None,
    )
    assert "partial" in partial
    failed = demo_notify.format_run_notification(
        question="q",
        mode="us",
        status="failed",
        partial=False,
        cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        daily_spent=None,
        daily_cap=None,
    )
    assert "❌" in failed and "failed" in failed
    assert "today:" not in failed  # omitted when no daily figure


def test_format_truncates_long_question() -> None:
    text = demo_notify.format_run_notification(
        question="x " * 200,
        mode="us",
        status="succeeded",
        partial=False,
        cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        latency_ms=0,
        daily_spent=None,
        daily_cap=None,
    )
    assert "…" in text
    # The question line stays bounded (preview cap + framing quotes).
    q_line = next(line for line in text.splitlines() if line.startswith("«"))
    assert len(q_line) <= 170


# ---------------------------------------------------------------- gating


def test_notifications_enabled_requires_demo_flag_and_creds() -> None:
    assert demo_notify.notifications_enabled(_demo_settings()) is True
    assert demo_notify.notifications_enabled(_demo_settings(budget_profile="dev")) is False
    assert demo_notify.notifications_enabled(_demo_settings(demo_notify_telegram=False)) is False
    assert demo_notify.notifications_enabled(_demo_settings(telegram_bot_token="")) is False
    assert demo_notify.notifications_enabled(_demo_settings(telegram_chat_id="")) is False


# ---------------------------------------------------------------- dispatch


@pytest.mark.asyncio
async def test_notify_run_finished_sends_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def _fake_send(text: str, **_: Any) -> Any:
        sent.append(text)

    monkeypatch.setattr(demo_notify, "send_alert", _fake_send)
    await demo_notify.notify_run_finished(
        question="q",
        mode="us",
        status="succeeded",
        partial=False,
        cost_usd=0.01,
        tokens_in=1,
        tokens_out=2,
        latency_ms=100,
        settings=_demo_settings(),
    )
    assert len(sent) == 1 and "succeeded" in sent[0]


@pytest.mark.asyncio
async def test_notify_run_finished_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def _fake_send(text: str, **_: Any) -> Any:
        sent.append(text)

    monkeypatch.setattr(demo_notify, "send_alert", _fake_send)
    await demo_notify.notify_run_finished(
        question="q",
        mode="us",
        status="succeeded",
        partial=False,
        cost_usd=0.01,
        tokens_in=1,
        tokens_out=2,
        latency_ms=100,
        settings=_demo_settings(demo_notify_telegram=False),
    )
    assert sent == []  # gated off => no delivery attempt


@pytest.mark.asyncio
async def test_notify_run_finished_swallows_send_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(text: str, **_: Any) -> Any:
        raise RuntimeError("proxy down")

    monkeypatch.setattr(demo_notify, "send_alert", _boom)
    # Must not raise — a broken notification can never fail the run.
    await demo_notify.notify_run_finished(
        question="q",
        mode="us",
        status="succeeded",
        partial=False,
        cost_usd=0.01,
        tokens_in=1,
        tokens_out=2,
        latency_ms=100,
        settings=_demo_settings(),
    )


# ---------------------------------------------------------------- limiter report


def test_daily_report_off_demo_is_none() -> None:
    assert DemoLimiter().daily_report() == (None, None)


def test_daily_report_on_demo_tracks_spend() -> None:
    limiter = DemoLimiter(max_concurrent_runs=2, daily_cost_cap_usd=1.5)
    limiter.record_cost(0.3)
    spent, cap = limiter.daily_report()
    assert spent == pytest.approx(0.3) and cap == pytest.approx(1.5)
