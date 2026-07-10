"""Model Router (T-016; CONTRACTS.md §11): tiered LLM routing with fallback.

Every LLM call in the system goes through ``RouterClient.chat``:

- tiers come from ``config/router.yaml``; tiers whose provider has no API key
  (or no local model configured) are dropped at startup with a warning;
- per task class the policy lists a primary -> fallback chain; a tier failure
  (timeout / connection / 5xx / 429) is retried twice inside the tier, then
  the next tier is tried with ``fallback_used=true``;
- every outcome (including failed tier attempts) is published as an
  ``llm_call`` TraceEvent — the DB subscriber prices it via config/prices.yaml;
- ``RouterChatModel`` wraps a task class as a LangChain-compatible chat model
  so LangGraph agents (bind_tools) run through the router transparently.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common.config import Settings, get_settings, load_yaml_config
from common.errors import ConfigError, SourceUnavailableError, ToolError
from common.logging import current_context, get_logger
from common.tracing import TraceBus

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
TIER_RETRY_ATTEMPTS = 2
TIER_RETRY_BACKOFF_S = 0.5


@dataclass
class RouterTier:
    name: str
    provider: str
    model: str
    timeout_s: float
    thinking: str | None
    chat_model: BaseChatModel


@dataclass
class RouterResponse:
    """Outcome of a routed call (CONTRACTS §11)."""

    message: AIMessage
    provider: str
    model: str
    fallback_used: bool
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.message.content if isinstance(self.message.content, str) else ""

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [dict(call) for call in self.message.tool_calls]


def _default_model_factory(tier_spec: dict[str, Any], settings: Settings) -> BaseChatModel:
    """Build the LangChain chat model for one tier (openai-compatible providers)."""
    from langchain_openai import ChatOpenAI

    provider = str(tier_spec["provider"])
    timeout = float(tier_spec.get("timeout_s", 60))
    if provider == "deepseek":
        extra_body = {"thinking": {"type": str(tier_spec.get("thinking", "disabled"))}}
        return ChatOpenAI(
            model=str(tier_spec["model"]),
            api_key=settings.deepseek_api_key,  # type: ignore[arg-type]
            base_url=DEEPSEEK_BASE_URL,
            timeout=timeout,
            temperature=0.0,
            extra_body=extra_body,
        )
    if provider == "ollama":
        return ChatOpenAI(
            model=str(tier_spec["model"]),
            api_key="ollama",  # type: ignore[arg-type]
            base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
            timeout=timeout,
            temperature=0.0,
        )
    if provider == "openai":
        return ChatOpenAI(
            model=str(tier_spec["model"]),
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            timeout=timeout,
            temperature=0.0,
        )
    raise ConfigError(
        f"provider {provider!r} is configured in router.yaml but not wired yet "
        "(add its langchain package and factory branch when a tier needs it)"
    )


def _tier_is_available(tier_spec: dict[str, Any], settings: Settings) -> bool:
    provider = str(tier_spec.get("provider", ""))
    if provider == "ollama":
        return settings.has_local_llm()
    key_by_provider = {
        "deepseek": settings.deepseek_api_key,
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.google_api_key,
    }
    return bool(key_by_provider.get(provider))


def _is_retryable_error(error: BaseException) -> bool:
    """Timeout / connection / 5xx / 429 — worth retrying and falling back."""
    if isinstance(error, TimeoutError | ConnectionError | asyncio.TimeoutError):
        return True
    name = type(error).__name__
    retryable_names = (
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
        "APIStatusError",
    )
    return any(marker in name for marker in retryable_names)


class RouterClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        trace_bus: TraceBus | None = None,
        router_config: dict[str, Any] | None = None,
        model_factory: Any = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._bus = trace_bus
        self._log = get_logger(node="model_router")
        config = router_config if router_config is not None else load_yaml_config("router")
        factory = model_factory or _default_model_factory
        self._policy: dict[str, list[str]] = {
            task_class: list(chain) for task_class, chain in config.get("policy", {}).items()
        }
        self._tiers: dict[str, RouterTier] = {}
        for name, spec in config.get("tiers", {}).items():
            if not _tier_is_available(spec, self._settings):
                self._log.warning(
                    "tier_dropped_no_credentials", tier=name, provider=spec.get("provider")
                )
                continue
            try:
                chat_model = factory(spec, self._settings)
            except ConfigError as exc:
                self._log.warning("tier_dropped_not_wired", tier=name, detail=str(exc))
                continue
            self._tiers[name] = RouterTier(
                name=name,
                provider=str(spec["provider"]),
                model=str(spec["model"]),
                timeout_s=float(spec.get("timeout_s", 60)),
                thinking=spec.get("thinking"),
                chat_model=chat_model,
            )
        if not self._tiers:
            raise ConfigError("no usable router tiers: configure DEEPSEEK_API_KEY or a local model")

    def available_tiers(self) -> list[str]:
        return list(self._tiers)

    def chain_for(self, task_class: str) -> list[RouterTier]:
        chain = self._policy.get(task_class)
        if not chain:
            raise ConfigError(f"router policy has no task class {task_class!r}")
        return [self._tiers[name] for name in chain if name in self._tiers]

    async def _publish_llm_call(self, payload: dict[str, Any]) -> None:
        """llm_call events flow only when a run context exists (bus + run_id)."""
        if self._bus is None or not current_context().get("run_id"):
            return
        await self._bus.publish("llm_call", payload)

    async def chat(
        self,
        task_class: str,
        messages: Sequence[BaseMessage] | list[tuple[str, str]],
        *,
        tools: Sequence[Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> RouterResponse:
        chain = self.chain_for(task_class)
        if not chain:
            raise ConfigError(f"no available tiers for task class {task_class!r}")
        fallback_used = False
        last_error: BaseException | None = None
        for tier in chain:
            runnable: Any = tier.chat_model
            if tools:
                runnable = runnable.bind_tools(list(tools))
            if response_format:
                runnable = runnable.bind(response_format=response_format)
            for attempt in range(1, TIER_RETRY_ATTEMPTS + 1):
                started = time.perf_counter()
                try:
                    message = await asyncio.wait_for(
                        runnable.ainvoke(list(messages)), timeout=tier.timeout_s
                    )
                except Exception as error:  # noqa: BLE001 — classified below
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    last_error = error
                    await self._publish_llm_call(
                        {
                            "task_class": task_class,
                            "provider": tier.provider,
                            "model": tier.model,
                            "tokens_in": 0,
                            "tokens_out": 0,
                            "latency_ms": latency_ms,
                            "fallback_used": fallback_used,
                            "error": f"{type(error).__name__}: {error}"[:300],
                        }
                    )
                    if not _is_retryable_error(error):
                        raise
                    self._log.warning(
                        "tier_attempt_failed",
                        tier=tier.name,
                        attempt=attempt,
                        error=str(error)[:200],
                    )
                    if attempt < TIER_RETRY_ATTEMPTS:
                        await asyncio.sleep(TIER_RETRY_BACKOFF_S * attempt)
                    continue
                latency_ms = int((time.perf_counter() - started) * 1000)
                if not isinstance(message, AIMessage):  # defensive: contract of chat models
                    raise ToolError(f"unexpected model output type {type(message).__name__}")
                usage_meta = message.usage_metadata
                usage = {
                    "tokens_in": int(usage_meta["input_tokens"]) if usage_meta else 0,
                    "tokens_out": int(usage_meta["output_tokens"]) if usage_meta else 0,
                }
                await self._publish_llm_call(
                    {
                        "task_class": task_class,
                        "provider": tier.provider,
                        "model": tier.model,
                        "tokens_in": usage["tokens_in"],
                        "tokens_out": usage["tokens_out"],
                        "latency_ms": latency_ms,
                        "fallback_used": fallback_used,
                        "tool_calls": [call["name"] for call in message.tool_calls],
                    }
                )
                return RouterResponse(
                    message=message,
                    provider=tier.provider,
                    model=tier.model,
                    fallback_used=fallback_used,
                    usage=usage,
                )
            fallback_used = True  # this tier is exhausted — move down the chain
        raise SourceUnavailableError(
            f"all router tiers failed for task class {task_class!r}: {last_error}"
        )

    async def chat_structured[SchemaT: BaseModel](
        self,
        task_class: str,
        messages: Sequence[BaseMessage] | list[tuple[str, str]],
        schema: type[SchemaT],
    ) -> SchemaT:
        """JSON-mode call validated by a pydantic schema, with one repair retry."""
        instruction = HumanMessage(
            content=(
                "Respond ONLY with a JSON object matching this JSON schema "
                "(no prose, no markdown):\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )
        )
        request: list[Any] = list(messages) + [instruction]
        response = await self.chat(task_class, request, response_format={"type": "json_object"})
        try:
            return schema.model_validate_json(response.text)
        except ValidationError as first_error:
            repair: list[Any] = request + [
                response.message,
                HumanMessage(
                    content=(
                        f"Your JSON failed validation: {first_error}. "
                        "Return ONLY the corrected JSON object."
                    )
                ),
            ]
            second = await self.chat(task_class, repair, response_format={"type": "json_object"})
            try:
                return schema.model_validate_json(second.text)
            except ValidationError as second_error:
                raise ToolError(
                    f"structured output failed validation twice: {second_error}"
                ) from second_error


class RouterChatModel(BaseChatModel):
    """LangChain-compatible facade over RouterClient for one task class."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_class: str
    router: RouterClient
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "ledgerlens-router"

    @property
    def model_name(self) -> str:
        return f"router:{self.task_class}"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> RouterChatModel:
        return self.model_copy(update={"bound_tools": list(tools)})

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("RouterChatModel is async-only; use ainvoke/astream")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = await self.router.chat(self.task_class, messages, tools=self.bound_tools or None)
        return ChatResult(generations=[ChatGeneration(message=response.message)])
