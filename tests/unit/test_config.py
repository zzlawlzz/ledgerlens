"""Unit tests for common.config (T-003)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic_settings import SettingsConfigDict

from common.config import (
    KNOWN_CONFIGS,
    MASK,
    Settings,
    get_settings,
    load_yaml_config,
    main,
    reset_settings_cache,
)
from common.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _NoEnvFileSettings(Settings):
    """Settings that ignore the local .env file — deterministic in tests."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def make_settings(**overrides: Any) -> Settings:
    return _NoEnvFileSettings(**overrides)


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    reset_settings_cache()
    yield
    reset_settings_cache()


# --- ${VAR} substitution in YAML -------------------------------------------


def test_yaml_env_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SUB_URL", "http://example:9000")
    (tmp_path / "sample.yaml").write_text(
        "url: ${TEST_SUB_URL}\n"
        "port: '${TEST_SUB_PORT:5432}'\n"
        "nested:\n"
        "  items: ['${TEST_SUB_URL}/a', plain]\n",
        encoding="utf-8",
    )
    data = load_yaml_config("sample", config_dir=tmp_path)
    assert data["url"] == "http://example:9000"
    assert data["port"] == "5432"  # default used
    assert data["nested"]["items"] == ["http://example:9000/a", "plain"]


def test_yaml_substitution_uses_settings_fields(tmp_path: Path) -> None:
    # LOCAL_MODEL is not in os.environ here, but Settings has a default for it.
    (tmp_path / "sample.yaml").write_text("model: ${LOCAL_MODEL}\n", encoding="utf-8")
    data = load_yaml_config("sample", config_dir=tmp_path)
    assert data["model"] == get_settings().local_model


def test_yaml_missing_var_without_default_raises(tmp_path: Path) -> None:
    (tmp_path / "sample.yaml").write_text("value: ${NO_SUCH_VAR_EVER}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="NO_SUCH_VAR_EVER"):
        load_yaml_config("sample", config_dir=tmp_path)


def test_yaml_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_yaml_config("nope", config_dir=tmp_path)


def test_default_config_files_load() -> None:
    for name in KNOWN_CONFIGS:
        data = load_yaml_config(name)
        assert isinstance(data, dict) and data, f"{name}.yaml is empty"


# --- Settings validation -----------------------------------------------------


def _no_llm_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for var in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return make_settings(local_model="", edgar_user_agent="ua")


def test_no_llm_provider_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _no_llm_settings(monkeypatch)
    problems = settings.missing_required()
    assert any("no LLM provider" in p for p in problems)


def test_llm_provider_via_cloud_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _no_llm_settings(monkeypatch)
    with_key = settings.model_copy(update={"deepseek_api_key": "sk-x"})
    assert with_key.cloud_llm_providers() == ["deepseek"]
    assert not any("no LLM provider" in p for p in with_key.missing_required())


def test_edgar_user_agent_required_in_us_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    settings = make_settings(edgar_user_agent="", deepseek_api_key="sk-x")
    assert any("EDGAR_USER_AGENT" in p for p in settings.missing_required())
    ru = settings.model_copy(update={"app_mode": "ru"})
    assert not any("EDGAR_USER_AGENT" in p for p in ru.missing_required())


def test_invalid_mode_and_profile_reported() -> None:
    settings = make_settings(
        app_mode="xx",
        budget_profile="prod",
        deepseek_api_key="sk-x",
        edgar_user_agent="ua",
    )
    problems = settings.missing_required()
    assert any("APP_MODE" in p for p in problems)
    assert any("BUDGET_PROFILE" in p for p in problems)


def test_settings_override_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "ru")
    reset_settings_cache()
    assert get_settings().app_mode == "ru"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
    first = get_settings()
    reset_settings_cache()
    assert get_settings() is not first


# --- Secret masking ----------------------------------------------------------


def test_masked_dump_hides_secrets() -> None:
    settings = make_settings(
        deepseek_api_key="sk-secret",
        postgres_password="pg-secret",
        a2a_token="tok",
        n8n_encryption_key="enc",
    )
    dump = settings.masked_dump()
    assert dump["deepseek_api_key"] == MASK
    assert dump["postgres_password"] == MASK
    assert dump["a2a_token"] == MASK
    assert dump["n8n_encryption_key"] == MASK
    assert dump["app_mode"] == "us"
    flat = " ".join(dump.values())
    for secret in ("sk-secret", "pg-secret"):
        assert secret not in flat


# --- .env.example sync -------------------------------------------------------


def _env_example_names() -> set[str]:
    names: set[str] = set()
    for line in (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            names.add(match.group(1))
    return names


def test_env_example_in_sync_with_settings() -> None:
    settings_fields = {name.upper() for name in Settings.model_fields}
    env_names = _env_example_names()
    assert env_names == settings_fields, (
        f"only in .env.example: {sorted(env_names - settings_fields)}; "
        f"only in Settings: {sorted(settings_fields - env_names)}"
    )


# --- CLI ----------------------------------------------------------------------


def test_validate_cli_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-cli-test")
    monkeypatch.setenv("EDGAR_USER_AGENT", "ledgerlens test@example.com")
    monkeypatch.setenv("APP_MODE", "us")
    reset_settings_cache()
    assert main(["--validate"]) == 0
    out = capsys.readouterr().out
    assert "Configuration is valid." in out
    assert "sk-cli-test" not in out  # secret masked


def test_validate_cli_reports_problems(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_MODE", "bogus")
    reset_settings_cache()
    assert main(["--validate"]) == 1
    assert "APP_MODE" in capsys.readouterr().out
