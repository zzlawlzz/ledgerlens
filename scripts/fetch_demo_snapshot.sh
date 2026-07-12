#!/usr/bin/env bash
# Fetch the frozen demo-corpus snapshot (pg_dump + Qdrant snapshot) so `make seed`
# can populate a clean machine without touching EDGAR. The snapshot is produced by
# the `eval-snapshot.yml` workflow and stored as the long-retention `eval-demo-snapshot`
# GitHub Actions artifact; this pulls the newest successful build via `gh`.
#
# Usage: scripts/fetch_demo_snapshot.sh [DEST_DIR]   (default: snapshot/eval_demo)
set -euo pipefail

DEST="${1:-snapshot/eval_demo}"

if ! command -v gh >/dev/null 2>&1; then
  echo "error: GitHub CLI (gh) not found — install it or place the snapshot files in ${DEST}/ manually." >&2
  echo "       (expected: eval_demo.pgdump and eval_demo_qdrant.snapshot)" >&2
  exit 1
fi

echo "Locating the latest successful eval-snapshot run…"
run_id="$(gh run list --workflow=eval-snapshot.yml --status success --limit 1 --json databaseId --jq '.[0].databaseId')"
if [ -z "${run_id}" ] || [ "${run_id}" = "null" ]; then
  echo "error: no successful eval-snapshot.yml run found. Dispatch one with:" >&2
  echo "         gh workflow run eval-snapshot.yml" >&2
  exit 1
fi

echo "Downloading eval-demo-snapshot from run ${run_id} into ${DEST}/ …"
mkdir -p "${DEST}"
gh run download "${run_id}" --name eval-demo-snapshot --dir "${DEST}"
echo "Snapshot ready in ${DEST}/:"
ls -la "${DEST}"
