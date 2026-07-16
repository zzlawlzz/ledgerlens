# LedgerLens app image (T-015): API + worker in one container (phase 1).
# Multi-stage: uv resolves the locked environment, runtime runs as non-root.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# UV_HTTP_TIMEOUT raised from the 30s default: dependency downloads run over a
# slow RU link, where the odd package (e.g. google-auth) otherwise times out and
# fails the whole layer whenever uv.lock changes and it must re-fetch everything.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=180
WORKDIR /app

# Dependency layer — cached until pyproject/uv.lock change. The uv download cache
# is a persistent buildkit cache mount, so a lock change (or a timed-out retry on
# the slow RU link) re-uses already-fetched wheels instead of re-downloading all.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

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
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/app/scripts/docker-entrypoint.sh"]
