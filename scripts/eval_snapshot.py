"""Demo-corpus snapshot for eval-in-CI (T-030).

CI must not hit EDGAR on every run, so the eval workflow restores a cached
snapshot of the frozen 10-ticker demo corpus instead of re-ingesting.
Snapshotting happens against a running ``docker compose`` stack (postgres +
qdrant reachable on their published ports):

    uv run python scripts/eval_snapshot.py export --dir snapshot/eval_demo
    uv run python scripts/eval_snapshot.py restore --dir snapshot/eval_demo

``export`` is run manually (workflow_dispatch, after a real `make demo-ingest`
covering all 10 tickers) and its output uploaded as a long-retention GitHub
Actions artifact. ``restore`` runs at the start of every eval CI job, before
`eval.run`, against a freshly-migrated (schema-only) database and an empty
Qdrant.

Only the domain tables that hold ingested facts/narrative are dumped —
operational tables (runs/steps/llm_calls/tool_calls/checkpoints/eval_runs/
eval_results/monitored_events/prices) start empty in CI and are not part of
the demo corpus.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

DOMAIN_TABLES = ["companies", "filings", "filing_sections", "section_chunks", "financial_facts"]
QDRANT_COLLECTION = "narrative_chunks"
PG_DUMP_NAME = "eval_demo.pgdump"
QDRANT_SNAPSHOT_NAME = "eval_demo_qdrant.snapshot"


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def export_snapshot(out_dir: Path, qdrant_url: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    dump_cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "app",
        "-d",
        "ledgerlens",
        "--no-owner",
        "--no-privileges",
        "--data-only",
        "-F",
        "c",
        "-f",
        f"/tmp/{PG_DUMP_NAME}",
    ]
    for table in DOMAIN_TABLES:
        dump_cmd[-2:-2] = ["-t", table]
    _run(dump_cmd)
    _run(["docker", "compose", "cp", f"postgres:/tmp/{PG_DUMP_NAME}", str(out_dir / PG_DUMP_NAME)])

    resp = httpx.post(f"{qdrant_url}/collections/{QDRANT_COLLECTION}/snapshots", timeout=120)
    resp.raise_for_status()
    snapshot_name = resp.json()["result"]["name"]
    download = httpx.get(
        f"{qdrant_url}/collections/{QDRANT_COLLECTION}/snapshots/{snapshot_name}", timeout=120
    )
    download.raise_for_status()
    (out_dir / QDRANT_SNAPSHOT_NAME).write_bytes(download.content)
    print(f"exported {out_dir / PG_DUMP_NAME} and {out_dir / QDRANT_SNAPSHOT_NAME}")


def restore_snapshot(in_dir: Path, qdrant_url: str) -> None:
    pg_dump_path = in_dir / PG_DUMP_NAME
    qdrant_snapshot_path = in_dir / QDRANT_SNAPSHOT_NAME
    if not pg_dump_path.exists() or not qdrant_snapshot_path.exists():
        raise FileNotFoundError(f"snapshot files missing under {in_dir}")

    _run(["docker", "compose", "cp", str(pg_dump_path), f"postgres:/tmp/{PG_DUMP_NAME}"])
    _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "pg_restore",
            "-U",
            "app",
            "-d",
            "ledgerlens",
            "--data-only",
            "--disable-triggers",
            f"/tmp/{PG_DUMP_NAME}",
        ]
    )

    with httpx.Client(timeout=120) as client:
        with qdrant_snapshot_path.open("rb") as fh:
            resp = client.post(
                f"{qdrant_url}/collections/{QDRANT_COLLECTION}/snapshots/upload",
                files={"snapshot": (qdrant_snapshot_path.name, fh, "application/octet-stream")},
            )
        resp.raise_for_status()
    print(f"restored {pg_dump_path} and {qdrant_snapshot_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/eval_snapshot.py")
    parser.add_argument("action", choices=["export", "restore"])
    parser.add_argument(
        "--dir", required=True, help="snapshot directory (export target / restore source)"
    )
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    args = parser.parse_args(argv)

    path = Path(args.dir)
    if args.action == "export":
        export_snapshot(path, args.qdrant_url)
    else:
        restore_snapshot(path, args.qdrant_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
