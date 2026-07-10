"""Worker as an A2A server (T-021; ARCHITECTURE §6, CONTRACTS §10).

FastAPI app exposing the ReAct worker over the A2A protocol (a2a-sdk 1.1,
protocol v1.0): the agent card at the well-known path, JSON-RPC message
endpoint guarded by a bearer ``A2A_TOKEN``. The payload contract is ours, not
the SDK's: the inbound message carries ``WorkerTask`` JSON in a text part, the
completed task carries ``WorkerResult`` JSON in the ``worker_result`` artifact
— exactly the same models as the in-process client (CONTRACTS §10), so the
orchestrator cannot tell local from remote.

Run: uvicorn workers.a2a_server:app --port 8081
"""

from __future__ import annotations

import os
import uuid
from typing import Any, cast

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    TaskState,
    TaskStatus,
)
from a2a.types import (
    Task as A2ATask,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from common.agents import WorkerResult, WorkerTask
from common.config import get_settings
from common.logging import configure_logging, get_logger
from workers.react_worker import run_worker_task

WORKER_RESULT_ARTIFACT = "worker_result"
RPC_PATH = "/a2a"
CARD_PATH = "/.well-known/agent-card.json"

SKILLS = [
    AgentSkill(
        id="financial_sql_analysis",
        name="Financial SQL analysis",
        description=(
            "Numbers from normalized financial statements (PostgreSQL): metrics, "
            "trends, comparisons over fiscal periods."
        ),
        tags=["sql", "finance"],
    ),
    AgentSkill(
        id="narrative_rag_analysis",
        name="Narrative RAG analysis",
        description=(
            "Narrative 10-K sections (risk factors, MD&A) via hybrid search with "
            "citations; honest no_results on empty corpus."
        ),
        tags=["rag", "citations"],
    ),
]


def build_agent_card(base_url: str) -> AgentCard:
    return AgentCard(
        name=get_settings().worker_node_name,
        description="LedgerLens ReAct worker: financial SQL + narrative RAG analysis",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=SKILLS,
        supported_interfaces=[
            AgentInterface(url=f"{base_url}{RPC_PATH}", protocol_binding="JSONRPC")
        ],
    )


class WorkerAgentExecutor(AgentExecutor):
    """Bridges A2A requests to run_worker_task (WorkerTask in, WorkerResult out)."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        log = get_logger(node="a2a_worker")
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        if context.current_task is None:
            # The SDK requires a full Task object as the FIRST queue event;
            # TaskUpdater only emits status updates, so enqueue it manually.
            await event_queue.enqueue_event(
                A2ATask(
                    id=context.task_id or "",
                    context_id=context.context_id or "",
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message] if context.message else [],
                )
            )
        await updater.start_work()
        try:
            task = self._parse_task(context)
            log.info("a2a_task_received", goal=task.goal[:120], task_id=task.task_id)
            result = await run_worker_task(task)
            result.node = get_settings().worker_node_name
        except Exception as exc:  # noqa: BLE001 — the caller gets a failed task, not a hang
            log.error("a2a_task_crashed", error=str(exc)[:300])
            await updater.failed(
                message=updater.new_agent_message(parts=[Part(text=str(exc)[:500])])
            )
            return
        await updater.add_artifact(
            [Part(text=result.model_dump_json())], name=WORKER_RESULT_ARTIFACT
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()

    @staticmethod
    def _parse_task(context: RequestContext) -> WorkerTask:
        message = context.message
        if message is None or not message.parts:
            raise ValueError("A2A message has no parts; expected WorkerTask JSON")
        payload = message.parts[0].text
        if not payload:
            raise ValueError("A2A message part has no text; expected WorkerTask JSON")
        return WorkerTask.model_validate_json(payload)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Everything except the agent card and health requires the A2A token."""

    OPEN_PATHS = (CARD_PATH, "/healthz")

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path in self.OPEN_PATHS:
            return cast(Response, await call_next(request))
        expected = get_settings().a2a_token
        header = request.headers.get("authorization", "")
        if not expected or header != f"Bearer {expected}":
            return JSONResponse({"error": "missing or invalid A2A token"}, status_code=401)
        return cast(Response, await call_next(request))


def create_app() -> FastAPI:
    configure_logging("a2a_worker")
    card = build_agent_card(os.environ.get("WORKER_BASE_URL", "http://worker:8081"))
    handler = DefaultRequestHandler(
        agent_executor=WorkerAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI(title="LedgerLens A2A worker")
    app.add_middleware(BearerAuthMiddleware)
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, card_url=CARD_PATH),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=RPC_PATH),
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "node": get_settings().worker_node_name})

    return app


app = create_app()


def make_user_message(task: WorkerTask) -> Message:
    """Client-side helper: WorkerTask -> A2A message (shared with tests)."""
    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.ROLE_USER,
        parts=[Part(text=task.model_dump_json())],
    )


def extract_worker_result(artifacts: Any) -> WorkerResult:
    """Client-side helper: collected A2A artifacts -> WorkerResult."""
    for artifact in artifacts:
        if artifact.name == WORKER_RESULT_ARTIFACT and artifact.parts:
            return WorkerResult.model_validate_json(artifact.parts[0].text)
    raise ValueError("A2A task finished without a worker_result artifact")
