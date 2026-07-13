"""Regression: the /agui endpoint must not leak a demo concurrency slot when
request setup fails after admission (T-036 §1 defect-fix #3).

The AG-UI ``RunAgentInput.thread_id`` is an unbounded string, but the internal
``ChatRequest.session_id`` is capped at 128 chars. Building ``ChatRequest`` used
to happen *after* ``_admit`` had already taken a concurrency slot and created the
run, and it sat outside the slot-release guard — so a client sending an overlong
``thread_id`` raised ``ValidationError`` mid-setup, permanently leaking the slot
and orphaning the run in ``running``. On the public demo (``max_concurrent_runs``
= 2) two such requests wedge the demo at "busy" 429 until a restart.

These tests drive the real endpoint via ``TestClient`` on paths that error
*before* the SSE stream begins (so the client does not block on the stream).
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("POSTGRES_PASSWORD", "test")  # import-time Settings load

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from fastapi.testclient import TestClient

from orchestrator import api
from orchestrator.demo_limits import DemoLimiter


def _body(thread_id: str, content: str = "What is Apple revenue?") -> dict[str, object]:
    return RunAgentInput(
        thread_id=thread_id,
        run_id="r1",
        state={},
        messages=[UserMessage(id="m1", role="user", content=content)],
        tools=[],
        context=[],
        forwarded_props={},
    ).model_dump(mode="json")


def _demo_limiter() -> DemoLimiter:
    return DemoLimiter(
        runs_per_hour_per_ip=100,
        max_question_chars=500,
        max_concurrent_runs=2,
    )


def test_agui_overlong_thread_id_returns_422_without_leaking_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = _demo_limiter()
    monkeypatch.setattr(api, "get_demo_limiter", lambda: limiter)
    created: list[object] = []

    async def _fake_create_run(*args: object, **kwargs: object) -> uuid.UUID:
        created.append(1)
        return uuid.uuid4()

    monkeypatch.setattr(api, "create_run", _fake_create_run)

    client = TestClient(api.app)
    resp = client.post("/agui", json=_body("t" * 200))

    assert resp.status_code == 422  # clean rejection, not a 500 mid-stream
    assert limiter._active == 0  # slot not leaked
    assert created == []  # no orphan run created before validation


def test_agui_setup_failure_after_admit_releases_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any failure between admission and the background task taking ownership of
    the slot must release it (the widened guard). Here subscription fails after
    the run is created — the old guard only wrapped create_run and would leak."""
    limiter = _demo_limiter()
    monkeypatch.setattr(api, "get_demo_limiter", lambda: limiter)

    async def _fake_create_run(*args: object, **kwargs: object) -> uuid.UUID:
        return uuid.uuid4()

    def _boom(_run_id: str) -> object:
        raise RuntimeError("subscribe failed")

    monkeypatch.setattr(api, "create_run", _fake_create_run)
    monkeypatch.setattr(api, "_subscribe_run_events", _boom)

    client = TestClient(api.app, raise_server_exceptions=False)
    resp = client.post("/agui", json=_body("short-thread"))

    assert resp.status_code == 500  # setup error surfaced
    assert limiter._active == 0  # slot released despite the failure
