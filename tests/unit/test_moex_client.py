"""Unit tests for the MOEX ISS HTTP client (T-032) — respx, no live network."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from adapters.moex.client import (
    BOARD_SECURITIES_URL,
    HISTORY_URL,
    MoexClient,
    parse_block,
)
from common.errors import SourceUnavailableError, ToolError

BOARD_URL = BOARD_SECURITIES_URL.format(board="TQBR") + "?iss.meta=off"


def make_client(tmp_path: Path, **kwargs: Any) -> MoexClient:
    kwargs.setdefault("cache_dir", tmp_path)
    kwargs.setdefault("min_interval_s", 0.0)
    kwargs.setdefault("retry_wait_base_s", 0.01)
    return MoexClient(**kwargs)


def _history_page(
    dates_and_closes: list[tuple[str, float]], index: int, total: int
) -> dict[str, Any]:
    return {
        "history": {
            "columns": ["TRADEDATE", "SECID", "CLOSE"],
            "data": [[d, "SBER", close] for d, close in dates_and_closes],
        },
        "history.cursor": {
            "columns": ["INDEX", "TOTAL", "PAGESIZE"],
            "data": [[index, total, 100]],
        },
    }


def test_parse_block_zips_columns_with_rows() -> None:
    payload = {"securities": {"columns": ["SECID", "SHORTNAME"], "data": [["SBER", "Сбербанк"]]}}
    assert parse_block(payload, "securities") == [{"SECID": "SBER", "SHORTNAME": "Сбербанк"}]
    assert parse_block(payload, "missing") == []


def test_parse_block_rejects_ragged_rows() -> None:
    payload = {"history": {"columns": ["A", "B"], "data": [[1]]}}
    with pytest.raises(ToolError, match="row width"):
        parse_block(payload, "history")


@respx.mock
async def test_cache_hit_skips_network(tmp_path: Path) -> None:
    route = respx.get(BOARD_URL).mock(
        return_value=httpx.Response(200, json={"securities": {"columns": [], "data": []}})
    )
    async with make_client(tmp_path) as client:
        first = await client.get_board_securities()
        second = await client.get_board_securities()
    assert first == second
    assert route.call_count == 1


@respx.mock
async def test_retries_on_500_then_success(tmp_path: Path) -> None:
    route = respx.get(BOARD_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json={"securities": {"columns": [], "data": []}}),
        ]
    )
    async with make_client(tmp_path) as client:
        data = await client.get_board_securities()
    assert data == {"securities": {"columns": [], "data": []}}
    assert route.call_count == 3


@respx.mock
async def test_5xx_exhausts_retries_to_source_unavailable(tmp_path: Path) -> None:
    route = respx.get(BOARD_URL).mock(return_value=httpx.Response(503))
    async with make_client(tmp_path) as client:
        with pytest.raises(SourceUnavailableError, match="503"):
            await client.get_board_securities()
    assert route.call_count == 3


@respx.mock
async def test_network_error_becomes_source_unavailable(tmp_path: Path) -> None:
    route = respx.get(BOARD_URL).mock(side_effect=httpx.ConnectError("blocked"))
    async with make_client(tmp_path) as client:
        with pytest.raises(SourceUnavailableError, match="network error"):
            await client.get_board_securities()
    assert route.call_count == 3


@respx.mock
async def test_404_propagates_immediately_without_retry(tmp_path: Path) -> None:
    route = respx.get(BOARD_URL).mock(return_value=httpx.Response(404))
    async with make_client(tmp_path) as client:
        with pytest.raises(ToolError, match="404") as excinfo:
            await client.get_board_securities()
    assert route.call_count == 1
    assert excinfo.value.retryable is False


@respx.mock
async def test_history_pagination_follows_cursor(tmp_path: Path) -> None:
    page0 = _history_page([("2020-01-03", 100.0)], index=0, total=2)
    page1 = _history_page([("2020-01-06", 101.0)], index=100, total=2)
    # TOTAL=2 with PAGESIZE=100 stops after the second request.
    page0["history.cursor"]["data"] = [[0, 101, 1]]
    page1["history.cursor"]["data"] = [[1, 101, 100]]
    route = respx.route(
        method="GET", url__startswith=HISTORY_URL.format(board="TQBR", secid="SBER")
    ).mock(side_effect=[httpx.Response(200, json=page0), httpx.Response(200, json=page1)])
    async with make_client(tmp_path) as client:
        rows = await client.get_history("SBER", date(2020, 1, 1), date(2020, 1, 31))
    assert [row["TRADEDATE"] for row in rows] == ["2020-01-03", "2020-01-06"]
    assert route.call_count == 2
    assert "start=1" in str(route.calls[1].request.url)


@respx.mock
async def test_closed_history_window_is_cached_forever(tmp_path: Path) -> None:
    page = _history_page([("2020-01-03", 100.0)], index=0, total=1)
    respx.route(method="GET", url__startswith=HISTORY_URL.format(board="TQBR", secid="SBER")).mock(
        return_value=httpx.Response(200, json=page)
    )
    async with make_client(tmp_path) as client:
        await client.get_history_page("SBER", date(2020, 1, 1), date(2020, 1, 31))
    meta = next(tmp_path.glob("*.meta.json"))
    assert '"ttl_s": null' in meta.read_text(encoding="utf-8")


@respx.mock
async def test_open_history_window_has_finite_ttl(tmp_path: Path) -> None:
    page = _history_page([], index=0, total=0)
    respx.route(method="GET", url__startswith=HISTORY_URL.format(board="TQBR", secid="SBER")).mock(
        return_value=httpx.Response(200, json=page)
    )
    till = date.today() + timedelta(days=1)
    async with make_client(tmp_path) as client:
        await client.get_history_page("SBER", date(2020, 1, 1), till)
    meta = json.loads(next(tmp_path.glob("*.meta.json")).read_text(encoding="utf-8"))
    assert meta["ttl_s"] is not None
