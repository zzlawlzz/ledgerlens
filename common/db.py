"""Async database access (T-005): engines and session factories.

Two engines: ``app`` (read-write, default) and ``app_ro`` (read-only role) —
the sql_query tool must use only the read-only engine (CONTRACTS.md §6).
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from common.config import Settings, get_settings
from common.errors import ConfigError


def build_dsn(
    settings: Settings | None = None,
    *,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """postgresql+asyncpg DSN; defaults to the read-write ``app`` credentials."""
    settings = settings or get_settings()
    effective_user = user if user is not None else settings.postgres_user
    effective_password = password if password is not None else settings.postgres_password
    if not effective_password:
        raise ConfigError(
            f"database password for user {effective_user!r} is empty — "
            "set POSTGRES_PASSWORD / POSTGRES_RO_PASSWORD in .env"
        )
    return (
        f"postgresql+asyncpg://{effective_user}:{effective_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def build_ro_dsn(settings: Settings | None = None) -> str:
    """DSN for the read-only ``app_ro`` role (sql_query tool)."""
    settings = settings or get_settings()
    return build_dsn(settings, user="app_ro", password=settings.postgres_ro_password)


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    return create_async_engine(build_dsn(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_ro_engine() -> AsyncEngine:
    return create_async_engine(build_ro_dsn(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache(maxsize=1)
def get_ro_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_ro_engine(), expire_on_commit=False)


def reset_db_caches() -> None:
    """Clear cached engines/factories (tests); does not dispose open pools."""
    get_engine.cache_clear()
    get_ro_engine.cache_clear()
    get_session_factory.cache_clear()
    get_ro_session_factory.cache_clear()
