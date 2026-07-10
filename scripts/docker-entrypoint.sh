#!/bin/sh
# App entrypoint (T-015/T-021): apply migrations, then serve.
# With arguments (compose `command`, e.g. the A2A worker) exec them instead
# of the default orchestrator API. Migrations stay idempotent across services.
set -e

echo "applying database migrations..."
alembic upgrade head

if [ "$#" -gt 0 ]; then
    echo "starting: $*"
    exec "$@"
fi

echo "starting orchestrator API..."
exec uvicorn orchestrator.api:app --host 0.0.0.0 --port 8000
