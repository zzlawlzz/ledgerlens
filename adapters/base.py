"""Pluggable data-source adapter interface and registry (T-007; CONTRACTS.md §8).

Adapters convert a concrete source (EDGAR, MOEX, ...) into domain DTOs and
never write to the database — persistence belongs to ingestion. Error
contract: adapter methods raise only ``SourceUnavailableError`` (source is
down/blocked; ingestion degrades) or ``ToolError`` (bad input, parse failure).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar

from common.config import load_yaml_config
from common.errors import ConfigError
from common.models import Company, Event, Filing, FilingSection, FinancialFact


class DataSourceAdapter(ABC):
    """One financial data source behind a uniform interface (ARCHITECTURE §3.6)."""

    source: ClassVar[str]  # 'edgar' | 'moex' | 'girbo' | 'edisclosure'

    @abstractmethod
    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]:
        """Companies known to the source (optionally narrowed to tickers)."""

    @abstractmethod
    async def fetch_filings(self, company: Company, years: int) -> list[Filing]:
        """Filing metadata for the last ``years`` years."""

    @abstractmethod
    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]:
        """Normalized facts (canonical metrics, common.metrics) for the filings."""

    @abstractmethod
    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]:
        """Narrative sections of one filing — RAG raw material."""

    @abstractmethod
    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]:
        """New disclosure events after ``since`` (monitoring layer B)."""


_REGISTRY: dict[str, type[DataSourceAdapter]] = {}


def register_adapter(cls: type[DataSourceAdapter]) -> type[DataSourceAdapter]:
    """Class decorator: register an adapter under its ``source`` name."""
    source = getattr(cls, "source", None)
    if not isinstance(source, str) or not source:
        raise ConfigError(f"adapter {cls.__name__} must define a non-empty `source: str`")
    _REGISTRY[source] = cls
    return cls


def registered_sources() -> list[str]:
    return sorted(_REGISTRY)


def get_adapter(
    source: str | None = None, app_config: dict[str, Any] | None = None
) -> DataSourceAdapter:
    """Instantiate the adapter for ``source``; default comes from config/app.yaml.

    With no explicit ``source``, resolves ``adapters[mode]`` from the app config —
    the only place where mode is mapped to a concrete implementation.
    """
    if source is None:
        config = app_config if app_config is not None else load_yaml_config("app")
        mode = config.get("mode")
        adapters_map = config.get("adapters", {})
        if mode not in adapters_map:
            raise ConfigError(
                f"app config has no adapter for mode {mode!r} (adapters: {sorted(adapters_map)})"
            )
        source = str(adapters_map[mode])
    adapter_cls = _REGISTRY.get(source)
    if adapter_cls is None:
        raise ConfigError(f"unknown data source {source!r}; registered: {registered_sources()}")
    return adapter_cls()
