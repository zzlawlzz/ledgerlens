"""Temporary direct LLM client with a router-shaped interface (T-013).

The full Model Router (tiers, fallback, cost accounting) lands in T-016.
Until then this thin factory resolves a task class to the first *cloud* tier
of the routing policy (config/router.yaml) and builds a LangChain chat model
for it — callers already use the router-style entry point, so swapping in
the real router will not change worker code.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from common.config import get_settings, load_yaml_config
from common.errors import ConfigError

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _resolve_cloud_tier(task_class: str) -> dict[str, Any]:
    router_config = load_yaml_config("router")
    tiers = router_config.get("tiers", {})
    chain = router_config.get("policy", {}).get(task_class)
    if not chain:
        raise ConfigError(f"router policy has no task class {task_class!r}")
    for tier_name in chain:
        tier = tiers.get(tier_name)
        # simple_client supports only the cloud (deepseek) tiers; the local
        # tier and true fallback semantics arrive with the full router (T-016).
        if tier and tier.get("provider") == "deepseek":
            return dict(tier)
    raise ConfigError(f"no deepseek tier in the chain for task class {task_class!r}")


def simple_chat_model(task_class: str = "reason") -> BaseChatModel:
    """LangChain chat model for the task class; DeepSeek only (Q-01)."""
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise ConfigError("DEEPSEEK_API_KEY is required for cloud LLM calls (Q-01)")
    tier = _resolve_cloud_tier(task_class)
    thinking_mode = str(tier.get("thinking", "disabled"))
    return ChatOpenAI(
        model=str(tier["model"]),
        api_key=settings.deepseek_api_key,  # type: ignore[arg-type]
        base_url=DEEPSEEK_BASE_URL,
        timeout=float(tier.get("timeout_s", 60)),
        temperature=0.0,
        extra_body={"thinking": {"type": thinking_mode}},
    )
