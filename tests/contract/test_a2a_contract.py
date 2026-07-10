"""A2A contract tests (T-021): agent card, auth, payload round-trip."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from common.agents import WorkerResult, WorkerTask
from common.config import reset_settings_cache
from workers.a2a_server import (
    CARD_PATH,
    RPC_PATH,
    WORKER_RESULT_ARTIFACT,
    WorkerAgentExecutor,
    build_agent_card,
    create_app,
    extract_worker_result,
    make_user_message,
)


def test_agent_card_declares_both_skills_and_jsonrpc() -> None:
    card = build_agent_card("http://worker:8081")
    skill_ids = [skill.id for skill in card.skills]
    assert skill_ids == ["financial_sql_analysis", "narrative_rag_analysis"]
    assert card.capabilities.streaming is True
    assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
    assert card.supported_interfaces[0].url.endswith(RPC_PATH)
    assert card.version  # SDK protobuf constructed without errors = schema-valid


def test_task_payload_round_trip() -> None:
    task = WorkerTask(task_id="t1", run_id="r1", goal="test goal")
    message = make_user_message(task)

    class FakeContext:
        pass

    context: Any = FakeContext()
    context.message = message
    parsed = WorkerAgentExecutor._parse_task(context)
    assert parsed == task


def test_extract_worker_result_finds_artifact() -> None:
    from a2a.types import Artifact, Part

    result = WorkerResult(task_id="t1", status="succeeded", answer="42", node="local")
    artifacts = [
        Artifact(artifact_id="a0", name="other", parts=[Part(text="junk")]),
        Artifact(
            artifact_id="a1",
            name=WORKER_RESULT_ARTIFACT,
            parts=[Part(text=result.model_dump_json())],
        ),
    ]
    extracted = extract_worker_result(artifacts)
    assert extracted == result
    with pytest.raises(ValueError, match="worker_result"):
        extract_worker_result([])


@pytest.fixture
def a2a_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("A2A_TOKEN", "test-token-123")
    reset_settings_cache()
    yield create_app()
    reset_settings_cache()


async def test_rpc_without_token_is_401_and_card_is_open(a2a_app: Any) -> None:
    transport = httpx.ASGITransport(app=a2a_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
        card_response = await client.get(CARD_PATH)
        assert card_response.status_code == 200
        assert "financial_sql_analysis" in card_response.text

        no_token = await client.post(RPC_PATH, json={"jsonrpc": "2.0", "method": "x", "id": 1})
        assert no_token.status_code == 401

        bad_token = await client.post(
            RPC_PATH,
            json={"jsonrpc": "2.0", "method": "x", "id": 1},
            headers={"Authorization": "Bearer wrong"},
        )
        assert bad_token.status_code == 401

        good_token = await client.post(
            RPC_PATH,
            json={"jsonrpc": "2.0", "method": "message/send", "id": 1, "params": {}},
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert good_token.status_code != 401  # auth passed; may fail deeper (bad params)


async def test_unreachable_worker_fails_fast_with_tool_error() -> None:
    from common.errors import ToolError
    from orchestrator.worker_client import A2AWorkerClient

    client = A2AWorkerClient("http://127.0.0.1:9", "token")
    assert await client.healthcheck() is False
    task = WorkerTask(task_id="t1", run_id="r1", goal="g")
    with pytest.raises(ToolError, match="unreachable"):
        await client.run(task)
