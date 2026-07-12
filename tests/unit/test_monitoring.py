"""Unit tests for monitoring layer B (T-035): alert formatting, dry-run, budget,
and the guardrail safety net on summaries — all without DB or network."""

from __future__ import annotations

from typing import Any

import pytest

from common.config import Settings
from orchestrator.alerting import format_alert, send_alert
from orchestrator.guardrail import GuardVerdict, find_advice_spans
from orchestrator.monitoring import DailyBudget, _guardrailed_summary

# --- alert formatting ------------------------------------------------------


def test_format_alert_carries_source_url_and_headers() -> None:
    body = format_alert(
        company="Test Corp",
        event_type="8-K",
        summary="The company appointed a new CFO effective March 1.",
        source_url="https://www.sec.gov/Archives/edgar/data/1/x.htm",
    )
    assert "Test Corp" in body
    assert "8-K" in body
    assert "https://www.sec.gov/Archives/edgar/data/1/x.htm" in body
    assert "new CFO" in body


def test_format_alert_omits_empty_source() -> None:
    body = format_alert(company="C", event_type="8-K", summary="Something happened.", source_url="")
    assert "Source:" not in body


# --- alert dispatch --------------------------------------------------------


async def test_send_alert_dry_run_without_credentials() -> None:
    settings = Settings(telegram_bot_token="", telegram_chat_id="")
    result = await send_alert("hello", settings=settings)
    assert result.delivered is False
    assert result.channel == "dry-run"


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.posted: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.posted.append((url, json))
        return _FakeResponse()


async def test_send_alert_delivers_when_credentials_present() -> None:
    settings = Settings(telegram_bot_token="tok", telegram_chat_id="42")
    client = _FakeClient()
    result = await send_alert("hi there", settings=settings, client=client)  # type: ignore[arg-type]
    assert result.delivered is True
    assert result.channel == "telegram"
    assert client.posted and client.posted[0][1]["chat_id"] == "42"
    assert client.posted[0][1]["text"] == "hi there"


@pytest.mark.parametrize("proxy", ["http://proxy:8888", ""])
async def test_send_alert_passes_proxy_to_owned_client(
    monkeypatch: pytest.MonkeyPatch, proxy: str
) -> None:
    """When no client is injected, the configured telegram_proxy_url (or None)
    is handed to the httpx client the function builds (T-035, RU-IP egress)."""
    captured: dict[str, Any] = {}

    class _CapClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
            return _FakeResponse()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("orchestrator.alerting.httpx.AsyncClient", _CapClient)
    settings = Settings(telegram_bot_token="tok", telegram_chat_id="42", telegram_proxy_url=proxy)
    result = await send_alert("hi", settings=settings)
    assert result.delivered is True
    assert captured["proxy"] == (proxy or None)


# --- daily budget ----------------------------------------------------------


def test_daily_budget_caps_within_a_day() -> None:
    budget = DailyBudget(max_per_day=2)
    assert budget.try_spend() is True
    assert budget.try_spend() is True
    assert budget.try_spend() is False  # third call over cap


def test_daily_budget_zero_blocks_everything() -> None:
    assert DailyBudget(max_per_day=0).try_spend() is False


# --- guardrailed summary ---------------------------------------------------


class _FakeRouter:
    """chat() replays queued texts; chat_structured() is the guard LLM stub."""

    def __init__(self, texts: list[str], guard_advice: bool = False) -> None:
        self._texts = list(texts)
        self._last = texts[-1] if texts else ""
        self._guard = GuardVerdict(advice=guard_advice)
        self.chat_calls = 0

    async def chat(self, task_class: str, messages: Any) -> Any:
        assert task_class == "summarize_event"
        self.chat_calls += 1
        text = self._texts.pop(0) if self._texts else self._last
        return type("R", (), {"text": text})()

    async def chat_structured(self, task_class: str, messages: Any, schema: Any) -> Any:
        assert task_class == "guard"
        return self._guard


async def test_clean_summary_passes_through() -> None:
    clean = "The company appointed a new chief financial officer effective March 1, 2026."
    assert not find_advice_spans(clean)  # sanity: no advice phrasing
    router = _FakeRouter([clean], guard_advice=False)
    result = await _guardrailed_summary("8-K body text", router=router)
    assert result == clean
    assert router.chat_calls == 1  # no retry needed


async def test_persistent_advice_falls_back_to_safe_line() -> None:
    # Router keeps returning advice-laden text; the deterministic regex stage
    # catches it both times -> the safe fallback line is emitted, never advice.
    advice = "You should buy the stock now before it rises further."
    assert find_advice_spans(advice)  # regex catches it
    router = _FakeRouter([advice, advice], guard_advice=True)
    result = await _guardrailed_summary("8-K body text", router=router)
    assert not find_advice_spans(result)  # the emitted summary is clean
    assert "material event" in result  # the fallback line
    assert router.chat_calls == 2  # summarize + one retry
