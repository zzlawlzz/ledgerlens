"""The demo-limit exception handler records refusals for observability (T-036 §1).

Each rejected public-demo request must leave one ``demo_rejections`` row (kind +
status), and a bookkeeping failure must never change the refusal the caller sees.
Both are verified here without a database by monkeypatching the persistence call.
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from fastapi import Request

import orchestrator.api as api
from orchestrator.demo_limits import DemoLimitError


async def test_handler_records_kind_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[str, int]] = []

    async def _spy(kind: str, status_code: int) -> None:
        recorded.append((kind, status_code))

    monkeypatch.setattr(api, "record_demo_rejection", _spy)

    exc = DemoLimitError(429, "slow down", kind="rate_limited")
    response = await api._demo_limit_handler(cast(Request, None), exc)

    assert recorded == [("rate_limited", 429)]
    assert response.status_code == 429
    assert json.loads(bytes(response.body)) == {"detail": "slow down"}


async def test_handler_survives_bookkeeping_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(kind: str, status_code: int) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(api, "record_demo_rejection", _boom)

    exc = DemoLimitError(413, "too long", kind="question_too_long")
    response = await api._demo_limit_handler(cast(Request, None), exc)

    # The refusal still reaches the caller even though recording blew up.
    assert response.status_code == 413
    assert json.loads(bytes(response.body)) == {"detail": "too long"}
