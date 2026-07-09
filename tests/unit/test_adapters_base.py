"""Tests for the adapter interface and registry (T-007, CONTRACTS.md §8)."""

from __future__ import annotations

from datetime import datetime

import pytest

from adapters.base import (
    DataSourceAdapter,
    get_adapter,
    register_adapter,
    registered_sources,
)
from common.errors import ConfigError
from common.models import Company, Event, Filing, FilingSection, FinancialFact


@register_adapter
class DummyAdapter(DataSourceAdapter):
    """Minimal complete implementation used by registry tests."""

    source = "dummy"

    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
        return [Company(source=self.source, external_id="D1", name="Dummy Co")]

    async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
        return []

    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]:
        return []

    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
        return []

    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
        return []


def test_registry_knows_dummy() -> None:
    assert "dummy" in registered_sources()
    adapter = get_adapter("dummy")
    assert isinstance(adapter, DummyAdapter)


async def test_dummy_adapter_fulfills_interface() -> None:
    adapter = get_adapter("dummy")
    companies = await adapter.list_entities()
    assert companies[0].source == "dummy"


def test_get_adapter_resolves_source_from_app_config() -> None:
    config = {"mode": "dm", "adapters": {"dm": "dummy"}}
    adapter = get_adapter(app_config=config)
    assert isinstance(adapter, DummyAdapter)


def test_unknown_source_raises_config_error_with_known_list() -> None:
    with pytest.raises(ConfigError, match="dummy"):
        get_adapter("no-such-source")


def test_mode_without_adapter_mapping_raises() -> None:
    with pytest.raises(ConfigError, match="no adapter for mode"):
        get_adapter(app_config={"mode": "xx", "adapters": {}})


def test_incomplete_adapter_cannot_be_instantiated() -> None:
    class Incomplete(DataSourceAdapter):
        source = "incomplete"

        async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
            return []

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]


def test_adapter_without_source_cannot_register() -> None:
    class NoSource(DataSourceAdapter):
        async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
            return []

        async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
            return []

        async def extract_facts(
            self, company: Company, filings: list[Filing]
        ) -> list[FinancialFact]:
            return []

        async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
            return []

        async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
            return []

    with pytest.raises(ConfigError, match="source"):
        register_adapter(NoSource)
