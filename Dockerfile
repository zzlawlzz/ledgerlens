# LedgerLens app image (T-015): API + worker in one container (phase 1).
# Multi-stage: uv resolves the locked environment, runtime runs as non-root.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer — cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Application layer.
COPY common ./common
COPY adapters ./adapters
COPY ingestion ./ingestion
COPY tools ./tools
COPY model_router ./model_router
COPY rag ./rag
COPY workers ./workers
COPY orchestrator ./orchestrator
COPY eval ./eval
COPY config ./config
COPY prompts ./prompts
COPY db ./db
COPY alembic.ini ./
COPY scripts ./scripts
COPY README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/app/scripts/docker-entrypoint.sh"]
