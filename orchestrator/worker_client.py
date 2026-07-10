"""WorkerClient interface + implementations (T-014/T-021; CONTRACTS.md §10).

The orchestrator talks to workers only through this interface; the payload
contract (WorkerTask in, WorkerResult out) is identical in-process and over
A2A, so the dispatcher cannot tell local from remote.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from common.agents import WorkerResult, WorkerTask
from common.errors import ToolError
from common.logging import get_logger
from common.tracing import TraceBus
from workers.react_worker import run_worker_task

A2A_EXTRA_TIMEOUT_S = 10.0


class WorkerClient(ABC):
    # True when the worker publishes its TraceEvents live on the orchestrator
    # bus; False makes the orchestrator relay result.trace after the fact
    # (remote workers — keeps the stream and llm_calls/tool_calls accounting
    # complete, T-023).
    relays_trace = True

    @abstractmethod
    async def run(self, task: WorkerTask) -> WorkerResult:
        """Execute one worker task and return its result."""


class LocalWorkerClient(WorkerClient):
    """In-process worker (phase 1); trace events go to the shared bus."""

    def __init__(self, trace_bus: TraceBus | None = None) -> None:
        self._trace_bus = trace_bus

    async def run(self, task: WorkerTask) -> WorkerResult:
        return await run_worker_task(task, trace_bus=self._trace_bus)


class A2AWorkerClient(WorkerClient):
    """Remote worker over A2A (T-021): card discovery, bearer auth, timeouts.

    Transport failures raise retryable ``ToolError`` — the orchestrator's
    assess/replan loop decides what to do, the client never hangs (timeout =
    task deadline + 10s).
    """

    relays_trace = False  # remote trace arrives in result.trace, relayed by the graph

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client: object | None = None
        self._httpx_client: object | None = None
        self._log = get_logger(node="a2a_client")

    async def _connect(self) -> object:
        if self._client is not None:
            return self._client
        import httpx
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory

        self._httpx_client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"}, timeout=30.0
        )
        try:
            card = await A2ACardResolver(self._httpx_client, self._base_url).get_agent_card()
        except Exception as exc:
            raise ToolError(f"A2A worker at {self._base_url} is unreachable: {exc}") from exc
        self._log.info(
            "a2a_card_resolved",
            worker=card.name,
            skills=[skill.id for skill in card.skills],
            url=self._base_url,
        )
        factory = ClientFactory(ClientConfig(httpx_client=self._httpx_client, streaming=True))
        self._client = factory.create(card)
        return self._client

    async def healthcheck(self) -> bool:
        try:
            await self._connect()
        except ToolError:
            return False
        return True

    async def run(self, task: WorkerTask) -> WorkerResult:
        from a2a.types import SendMessageRequest

        from workers.a2a_server import extract_worker_result, make_user_message

        timeout_s = task.budget.deadline_s + A2A_EXTRA_TIMEOUT_S
        client = await self._connect()
        request = SendMessageRequest(message=make_user_message(task))
        final_state = None
        artifacts: list[object] = []
        try:
            async with asyncio.timeout(timeout_s):
                async for response in client.send_message(request):  # type: ignore[attr-defined]
                    if response.HasField("task"):
                        final_state = response.task.status.state
                        artifacts.extend(response.task.artifacts)
                    elif response.HasField("status_update"):
                        final_state = response.status_update.status.state
                    elif response.HasField("artifact_update"):
                        artifacts.append(response.artifact_update.artifact)
        except TimeoutError as exc:
            raise ToolError(
                f"A2A worker timed out after {timeout_s:.0f}s (deadline + margin)"
            ) from exc
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — transport errors are retryable
            self._reset()
            raise ToolError(f"A2A transport error: {exc}") from exc
        from a2a.types import TaskState

        if final_state is None:
            raise ToolError("A2A worker returned no task state in the stream")
        if final_state != TaskState.TASK_STATE_COMPLETED:
            raise ToolError(f"A2A task ended in state {TaskState.Name(final_state)}")
        return extract_worker_result(artifacts)

    def _reset(self) -> None:
        self._client = None
        if self._httpx_client is not None:
            client = self._httpx_client
            self._httpx_client = None
            try:
                asyncio.get_running_loop().create_task(client.aclose())  # type: ignore[attr-defined]
            except RuntimeError:
                pass
