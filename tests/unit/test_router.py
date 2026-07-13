"""Unit tests for the Model Router (T-016) — fake providers, no network."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict

from common.config import Settings
from common.errors import ConfigError, SourceUnavailableError, ToolError
from common.logging import bind_run_context, reset_run_context
from common.tracing import TraceBus, TraceEvent
from model_router.router import TIER_RETRY_ATTEMPTS, RouterChatModel, RouterClient


class _NoEnvFileSettings(Settings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def make_settings(**overrides: Any) -> Settings:
    overrides.setdefault("deepseek_api_key", "sk-test")
    overrides.setdefault("edgar_user_agent", "ua")
    return _NoEnvFileSettings(**overrides)


class _FailingModel(FakeMessagesListChatModel):
    """Raises a retryable error on every call."""

    calls: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise TimeoutError("tier is down")


class _EchoModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self


ROUTER_CONFIG = {
    "tiers": {
        "local": {"provider": "ollama", "model": "test-local", "timeout_s": 5},
        "cloud_cheap": {"provider": "deepseek", "model": "deepseek-v4-flash", "timeout_s": 5},
        "cloud_strong": {"provider": "deepseek", "model": "deepseek-v4-pro", "timeout_s": 5},
    },
    "policy": {
        "route": ["local", "cloud_cheap"],
        "synthesize": ["cloud_strong", "cloud_cheap"],
        "judge": ["cloud_strong"],
    },
    "retries": {"attempts": 2, "backoff": "exponential"},
}


def _ai(text: str) -> AIMessage:
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    )


def make_router(
    models: dict[str, BaseChatModel],
    *,
    settings: Settings | None = None,
    trace_bus: TraceBus | None = None,
    config: dict[str, Any] | None = None,
) -> RouterClient:
    def factory(spec: dict[str, Any], _settings: Settings) -> BaseChatModel:
        name = str(spec["model"])
        if name in models:
            return models[name]
        # Tiers a given test does not exercise still get built by the router.
        return _EchoModel(responses=[_ai(f"unused tier {name}")])

    return RouterClient(
        settings or make_settings(),
        trace_bus=trace_bus,
        router_config=config or ROUTER_CONFIG,
        model_factory=factory,
    )


async def test_fallback_to_next_tier_and_both_calls_traced() -> None:
    failing = _FailingModel(responses=[_ai("never")])
    working = _EchoModel(responses=[_ai("answer from cheap")])
    bus = TraceBus()
    events: list[TraceEvent] = []

    async def _collect(event: TraceEvent) -> None:
        events.append(event)

    bus.subscribe(_collect)
    router = make_router({"test-local": failing, "deepseek-v4-flash": working}, trace_bus=bus)
    tokens = bind_run_context(run_id="r-router")
    try:
        response = await router.chat("route", [("user", "classify this")])
    finally:
        reset_run_context(tokens)
    assert response.text == "answer from cheap"
    assert response.fallback_used is True
    assert response.provider == "deepseek"
    assert failing.calls == TIER_RETRY_ATTEMPTS  # all in-tier retries before falling back
    llm_events = [e for e in events if e.event == "llm_call"]
    failed = [e for e in llm_events if "error" in e.payload]
    succeeded = [e for e in llm_events if "error" not in e.payload]
    assert len(failed) == TIER_RETRY_ATTEMPTS and len(succeeded) == 1
    assert succeeded[0].payload["fallback_used"] is True
    assert succeeded[0].payload["tokens_in"] == 11


async def test_tier_without_key_is_dropped() -> None:
    settings = make_settings(deepseek_api_key="", local_model="test-local")
    router = make_router(
        {"test-local": _EchoModel(responses=[_ai("local answer")])}, settings=settings
    )
    assert router.available_tiers() == ["local"]
    response = await router.chat("route", [("user", "hi")])
    assert response.provider == "ollama"
    assert response.fallback_used is False


async def test_no_usable_tier_for_task_class_raises() -> None:
    settings = make_settings(deepseek_api_key="", local_model="test-local")
    router = make_router({"test-local": _EchoModel(responses=[_ai("x")])}, settings=settings)
    # 'judge' policy allows only cloud_strong, which was dropped (no key).
    with pytest.raises(ConfigError, match="judge"):
        await router.chat("judge", [("user", "hi")])


async def test_all_tiers_failing_raises_source_unavailable() -> None:
    failing_a = _FailingModel(responses=[_ai("never")])
    failing_b = _FailingModel(responses=[_ai("never")])
    router = make_router({"test-local": failing_a, "deepseek-v4-flash": failing_b})
    with pytest.raises(SourceUnavailableError, match="route"):
        await router.chat("route", [("user", "hi")])


async def test_no_tiers_at_all_is_config_error() -> None:
    settings = make_settings(deepseek_api_key="", local_model="")
    with pytest.raises(ConfigError, match="no usable router tiers"):
        make_router({}, settings=settings)


class _Verdict(BaseModel):
    advice: bool
    spans: list[str]


async def test_structured_output_with_repair_retry() -> None:
    model = _EchoModel(
        responses=[
            AIMessage(content='{"advice": "not-a-bool"}'),
            AIMessage(content='{"advice": false, "spans": []}'),
        ]
    )
    router = make_router(
        {"deepseek-v4-pro": model, "deepseek-v4-flash": model},
        settings=make_settings(local_model=""),  # cloud-only setup
    )
    verdict = await router.chat_structured("judge", [("user", "check")], _Verdict)
    assert verdict.advice is False


async def test_structured_output_fails_after_two_invalid() -> None:
    model = _EchoModel(responses=[AIMessage(content="not json at all")])
    router = make_router(
        {"deepseek-v4-pro": model, "deepseek-v4-flash": model},
        settings=make_settings(local_model=""),
    )
    with pytest.raises(ToolError, match="twice"):
        await router.chat_structured("judge", [("user", "check")], _Verdict)


async def test_router_chat_model_delegates_and_binds_tools() -> None:
    model = _EchoModel(responses=[_ai("via facade")])
    router = make_router(
        {"deepseek-v4-pro": model, "deepseek-v4-flash": model},
        settings=make_settings(local_model=""),
    )
    facade = RouterChatModel(task_class="synthesize", router=router)

    def dummy_tool(x: int) -> int:
        """Doubles a number."""
        return x * 2

    bound = facade.bind_tools([dummy_tool])
    assert bound is not facade and bound.bound_tools
    result: BaseMessage = await bound.ainvoke([("user", "hello")])
    assert isinstance(result, AIMessage)
    assert result.content == "via facade"
