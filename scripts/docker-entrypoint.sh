#!/bin/sh
# App entrypoint (T-015): apply migrations, then serve the API.
set -e

echo "applying database migrations..."
alembic upgrade head

echo "starting orchestrator API..."
exec uvicorn orchestrator.api:app --host 0.0.0.0 --port 8000
