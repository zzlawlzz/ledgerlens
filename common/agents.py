"""Agent contracts: WorkerTask / WorkerResult (T-013; CONTRACTS.md §10).

One format for local calls and A2A (T-021) — the orchestrator never knows
whether a worker is in-process or remote.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WorkerStatus = Literal["succeeded", "failed", "no_data", "budget_exceeded"]

DEFAULT_ALLOWED_TOOLS = ["sql_query", "schema_introspect", "rag_search"]


class WorkerBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_iterations: int = 8
    max_tokens: int = 30_000
    deadline_s: float = 90.0


class TaskContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prior_results: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    mode: str = "us"


class WorkerTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    run_id: str
    goal: str
    context: TaskContext = Field(default_factory=TaskContext)
    allowed_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))
    budget: WorkerBudget = Field(default_factory=WorkerBudget)


class WorkerUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0


class WorkerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: WorkerStatus
    answer: str = ""
    evidence: WorkerEvidence = Field(default_factory=WorkerEvidence)
    trace: list[dict[str, Any]] = Field(default_factory=list)  # serialized TraceEvents
    usage: WorkerUsage = Field(default_factory=WorkerUsage)
