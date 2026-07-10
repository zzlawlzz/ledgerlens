"""WorkerClient interface (T-014; CONTRACTS.md §10).

The orchestrator talks to workers only through this interface — T-021 adds
``A2AWorkerClient`` for remote nodes without touching the API layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from common.agents import WorkerResult, WorkerTask
from common.tracing import TraceBus
from workers.react_worker import run_worker_task


class WorkerClient(ABC):
    @abstractmethod
    async def run(self, task: WorkerTask) -> WorkerResult:
        """Execute one worker task and return its result."""


class LocalWorkerClient(WorkerClient):
    """In-process worker (phase 1); trace events go to the shared bus."""

    def __init__(self, trace_bus: TraceBus | None = None) -> None:
        self._trace_bus = trace_bus

    async def run(self, task: WorkerTask) -> WorkerResult:
        return await run_worker_task(task, trace_bus=self._trace_bus)
