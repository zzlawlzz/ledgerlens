#!/bin/sh
# Idempotent model pull for the ollama service (T-017).
# Runs as a one-shot compose service; OLLAMA_HOST points at the server.
set -e

MODEL="${LOCAL_MODEL:?LOCAL_MODEL must be set}"

if ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "model $MODEL already present — skipping pull"
else
    echo "pulling $MODEL ..."
    ollama pull "$MODEL"
fi
