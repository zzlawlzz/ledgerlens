"""Central configuration (CONTRACTS.md §5).

Single entry point for env settings and YAML configs: no module outside
``common`` reads ``os.environ`` or ``config/*.yaml`` directly.

CLI: ``python -m common.config --validate`` prints the effective configuration
with secrets masked, or the list of missing required variables (exit code 1).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

_SECRET_FIELD_MARKERS = ("api_key", "password", "token", "encryption_key")
_ENV_VAR_PATTERN = re.compile(r"\$\{(?P<name>[A-Z][A-Z0-9_]*)(?::(?P<default>[^}]*))?\}")
MASK = "***"


class Settings(BaseSettings):
    """Env-backed settings; field names map 1:1 to variables in .env.example."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_mode: str = "us"
    app_env: str = "dev"
    budget_profile: str = "dev"
    # Comma-separated origins allowed to call the API cross-origin (empty =
    # none; deny-by-default). Set when the frontend is served from another
    # origin, e.g. a static Pages host, so its small JSON/SSE calls are allowed.
    cors_allow_origins: str = ""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ledgerlens"
    postgres_user: str = "app"
    postgres_password: str = ""
    postgres_ro_password: str = ""

    qdrant_url: str = "http://localhost:6333"
    ollama_base_url: str = "http://localhost:11434"
    local_model: str = "qwen3.5:27b"

    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    alphavantage_api_key: str = ""  # EOD prices, price_enrich (T-033, Q-19)

    edgar_user_agent: str = ""
    edgar_proxy_url: str = ""

    daily_cost_cap_usd: float = 1.5

    a2a_token: str = ""
    worker_node_name: str = "local"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Optional egress proxy for Telegram alerts (T-035). api.telegram.org is
    # blocked from some networks (e.g. RU IPs) — route sendMessage through an
    # HTTP proxy when set; empty means a direct connection. Same idea as
    # edgar_proxy_url but scoped to the alert channel only, so DeepSeek/EDGAR
    # traffic is unaffected.
    telegram_proxy_url: str = ""

    grafana_admin_password: str = ""
    grafana_ro_password: str = ""
    n8n_encryption_key: str = ""
    monitor_token: str = ""  # shared secret n8n <-> /api/monitor/* (T-035)

    def cloud_llm_providers(self) -> list[str]:
        """Names of cloud providers whose API key is present."""
        keys = {
            "deepseek": self.deepseek_api_key,
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "google": self.google_api_key,
        }
        return [name for name, key in keys.items() if key]

    def has_local_llm(self) -> bool:
        return bool(self.ollama_base_url and self.local_model)

    def missing_required(self) -> list[str]:
        """Human-readable problems that must be fixed before the app can start."""
        problems: list[str] = []
        if not self.cloud_llm_providers() and not self.has_local_llm():
            problems.append(
                "no LLM provider configured: set at least one of DEEPSEEK_API_KEY / "
                "ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY, or configure "
                "OLLAMA_BASE_URL + LOCAL_MODEL"
            )
        if self.app_mode == "us" and not self.edgar_user_agent:
            problems.append(
                "EDGAR_USER_AGENT is required in us mode (SEC fair-use policy, "
                "CONTRACTS.md §5 / Q-11)"
            )
        if self.app_mode not in ("us", "ru"):
            problems.append(f"APP_MODE must be 'us' or 'ru', got {self.app_mode!r}")
        if self.budget_profile not in ("dev", "demo"):
            problems.append(f"BUDGET_PROFILE must be 'dev' or 'demo', got {self.budget_profile!r}")
        return problems

    def masked_dump(self) -> dict[str, str]:
        """All fields as strings with secret values masked (safe to print/log)."""
        dump: dict[str, str] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if _is_secret_field(name) and value:
                dump[name] = MASK
            else:
                dump[name] = str(value) if value != "" else "(empty)"
        return dump


def _is_secret_field(name: str) -> bool:
    return any(marker in name for marker in _SECRET_FIELD_MARKERS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings; tests use `reset_settings_cache`."""
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def _substitution_context() -> dict[str, str]:
    """``${VAR}`` sources: settings fields (UPPER_CASE) overridden by os.environ."""
    settings = get_settings()
    context = {name.upper(): str(getattr(settings, name)) for name in type(settings).model_fields}
    context.update(os.environ)
    return context


def _substitute_env(value: Any, context: dict[str, str], *, source: str) -> Any:
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            name = match.group("name")
            if name in context:
                return context[name]
            default = match.group("default")
            if default is not None:
                return default
            raise ConfigError(f"{source}: variable ${{{name}}} is not set and has no default")

        return _ENV_VAR_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v, context, source=source) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(item, context, source=source) for item in value]
    return value


def load_yaml_config(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load ``config/<name>.yaml`` with ``${VAR}`` / ``${VAR:default}`` substitution."""
    path = (config_dir or CONFIG_DIR) / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level YAML structure must be a mapping")
    result = _substitute_env(raw, _substitution_context(), source=str(path))
    assert isinstance(result, dict)
    return result


KNOWN_CONFIGS = (
    "app",
    "router",
    "prices",
    "rag",
    "enrich",
    "budgets",
    "workers",
    "guardrail_patterns",
    "eval-thresholds",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m common.config")
    parser.add_argument(
        "--validate", action="store_true", help="validate settings and config files"
    )
    args = parser.parse_args(argv)
    if not args.validate:
        parser.print_help()
        return 0

    settings = get_settings()
    problems = settings.missing_required()
    if problems:
        print("Configuration is INVALID; missing/incorrect variables:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("Effective settings (secrets masked):")
    for key, value in settings.masked_dump().items():
        print(f"  {key} = {value}")
    print("Config files:")
    for name in KNOWN_CONFIGS:
        try:
            data = load_yaml_config(name)
            print(f"  {name}.yaml: OK ({len(data)} top-level keys)")
        except ConfigError as exc:
            print(f"  {name}.yaml: ERROR — {exc}")
            return 1
    print("Configuration is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
