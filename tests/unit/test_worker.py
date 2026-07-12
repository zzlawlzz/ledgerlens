"""Unit tests for the ReAct worker (T-013) — scripted LLM, fake tools, no DB."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage

from common.agents import WorkerBudget, WorkerResult, WorkerTask, WorkerUsage
from workers.react_worker import load_worker_prompt, run_worker_task


class ScriptedChatModel(FakeMessagesListChatModel):
    """Returns scripted messages; sticks to the last one when exhausted."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self


def _tool_call_message(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def _final_message(text: str) -> AIMessage:
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 20, "output_tokens": 7, "total_tokens": 27},
    )


def _thought_with_tool_call(text: str, name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    """A ReAct action message that also narrates intent ("I'll search ...").

    DeepSeek reasoner emits reasoning text alongside the tool call it requests;
    that text is a Thought, not the final answer.
    """
    return AIMessage(
        content=text,
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


FAKE_TOOLS = {
    "sql_query": lambda **kwargs: _fake_sql(**kwargs),
    "schema_introspect": lambda **kwargs: _fake_schema(),
}


async def _fake_sql(sql: str, row_limit: int = 50) -> dict[str, Any]:
    return {"columns": ["value"], "rows": [["5000"]], "row_count": 1, "truncated": False}


async def _fake_schema() -> dict[str, Any]:
    return {"tables": [], "metrics": [], "examples": []}


def _task(**overrides: Any) -> WorkerTask:
    defaults: dict[str, Any] = {
        "task_id": "step-1",
        "run_id": "run-1",
        "goal": "What was the revenue?",
    }
    defaults.update(overrides)
    return WorkerTask(**defaults)


def _script(messages: list[BaseMessage]) -> ScriptedChatModel:
    return ScriptedChatModel(responses=messages)


async def test_happy_path_with_tool_call_and_usage() -> None:
    model = _script(
        [
            _tool_call_message("sql_query", {"sql": "SELECT value FROM latest_facts"}, "c1"),
            _final_message("Revenue was 5000 USD in FY2024."),
        ]
    )
    result = await run_worker_task(_task(), model=model, tool_impls=dict(FAKE_TOOLS))
    assert result.status == "succeeded"
    assert "5000" in result.answer
    assert result.usage.tokens_in == 30 and result.usage.tokens_out == 12
    assert result.usage.tool_calls == 1
    assert result.evidence.facts and result.evidence.facts[0]["row_count"] == 1
    event_names = [event["event"] for event in result.trace]
    assert "tool_call_started" in event_names and "tool_call_finished" in event_names
    # llm_call events are published by the router (T-016); an injected scripted
    # model bypasses it, so only the thought is expected here.
    assert "agent_thought" in event_names


async def test_no_data_marker_maps_to_no_data_status() -> None:
    model = _script([_final_message("NO_DATA: only FY2023 is loaded for this company")])
    result = await run_worker_task(_task(), model=model, tool_impls=dict(FAKE_TOOLS))
    assert result.status == "no_data"
    assert result.answer.startswith("only FY2023")


async def test_iteration_limit_returns_budget_exceeded() -> None:
    # The model always asks for another tool call — it never finishes.
    model = _script([_tool_call_message("schema_introspect", {}, "loop")])
    result = await run_worker_task(
        _task(budget=WorkerBudget(max_iterations=2, deadline_s=30)),
        model=model,
        tool_impls=dict(FAKE_TOOLS),
    )
    assert result.status == "budget_exceeded"
    assert result.answer  # partial answer / explanation, not empty
    assert result.usage.tool_calls >= 1


async def test_deadline_returns_budget_exceeded() -> None:
    import asyncio

    async def _slow_sql(sql: str, row_limit: int = 50) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {"columns": [], "rows": [], "row_count": 0, "truncated": False}

    model = _script(
        [
            _tool_call_message("sql_query", {"sql": "SELECT 1"}, "c1"),
            _final_message("never reached"),
        ]
    )
    result = await run_worker_task(
        _task(budget=WorkerBudget(max_iterations=8, deadline_s=0.5)),
        model=model,
        tool_impls={**FAKE_TOOLS, "sql_query": _slow_sql},
    )
    assert result.status == "budget_exceeded"


async def test_model_failure_returns_failed_result() -> None:
    class ExplodingModel(ScriptedChatModel):
        def _generate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM API down")

    result = await run_worker_task(
        _task(),
        model=ExplodingModel(responses=[_final_message("x")]),
        tool_impls=dict(FAKE_TOOLS),
    )
    assert result.status == "failed"
    assert "LLM API down" in result.answer


async def test_tool_call_thought_never_leaks_as_answer() -> None:
    """Root cause of GH ci-eval faithfulness=0.0 on narrative cases (T-041).

    Under load the synthesis turn can come back empty; if the preceding
    action message's narration ("I'll search Apple's 10-K ...") were captured
    as the answer, the worker would report a placeholder as ``succeeded`` —
    citations retrieved, but the answer text contradicting them (faithfulness
    0.0). A tool-calling message is a ReAct action, never a final answer.
    """
    model = _script(
        [
            _thought_with_tool_call(
                "I'll search Apple's 10-K for supply chain risks",
                "sql_query",
                {"sql": "SELECT value FROM latest_facts"},
                "c1",
            ),
            _final_message(""),  # synthesis turn returns empty (rate-limited)
        ]
    )
    result = await run_worker_task(_task(), model=model, tool_impls=dict(FAKE_TOOLS))
    assert "I'll search" not in result.answer
    assert result.status == "failed"


def test_worker_result_serializes_per_contract() -> None:
    result = WorkerResult(
        task_id="t",
        status="succeeded",
        answer="42",
        usage=WorkerUsage(tokens_in=1, tokens_out=2, tool_calls=3),
    )
    restored = WorkerResult.model_validate_json(result.model_dump_json())
    assert restored == result
    payload = restored.model_dump()
    assert set(payload) == {"task_id", "status", "answer", "evidence", "trace", "usage", "node"}


def test_prompt_loads_with_mandatory_blocks() -> None:
    prompt = load_worker_prompt()
    assert "не советник" in prompt  # canonical non-advice block (CONTRACTS §12)
    assert "NO_DATA:" in prompt
    assert "schema_introspect" in prompt
    assert not prompt.startswith("---")  # version header stripped
