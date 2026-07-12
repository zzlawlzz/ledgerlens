# Demo deployment (T-036)

Runbook for the public demo: a stranger can play with LedgerLens without hitting
EDGAR, without draining the budget, and without breaking the install.

Status of the T-036 sub-parts:

| Part | What | State |
|------|------|-------|
| §2 | `make seed` / `make snapshot` — corpus without EDGAR | **done** (this doc) |
| §1 | `BUDGET_PROFILE=demo` rate/cost limits | pending |
| §3 | Security-pass checklist | pending |
| §4 | `cloudflared` overlay + public TLS | pending (owner: CF domain) |
| §5 | UI "public demo" banner | pending |

---

## Seed the corpus without EDGAR (§2)

The demo corpus is the frozen 10-ticker set (T-028) — the *same* snapshot the
eval-in-CI job restores, so the demo shows exactly the validated corpus. It ships
as the long-retention `eval-demo-snapshot` GitHub Actions artifact (a `pg_dump` +
a Qdrant snapshot, ~24 MB), never as EDGAR traffic and never committed to git.

### Restore on a clean machine

```bash
make seed
```

This will:

1. Fetch `snapshot/eval_demo/` via `scripts/fetch_demo_snapshot.sh` (latest
   successful `eval-snapshot.yml` run) if the files are absent.
2. `docker compose up -d --wait postgres qdrant`.
3. `alembic upgrade head` (schema).
4. `eval_snapshot.py restore --clean` — truncate the demo domain tables, drop the
   Qdrant collection, then load the snapshot. `--clean` makes re-seeding idempotent
   against a stack that already holds a corpus; only the demo domain tables
   (`companies`, `filings`, `filing_sections`, `section_chunks`, `financial_facts`)
   and the `narrative_chunks` collection are touched — operational tables are left
   alone.

Requires the GitHub CLI (`gh`) authenticated for the artifact fetch, or drop the two
snapshot files into `snapshot/eval_demo/` manually.

### Refresh the snapshot after a re-ingest

When the corpus or the embedding model changes, regenerate the snapshot:

```bash
make demo-ingest          # live EDGAR (disk-cached) + embeddings
make snapshot             # export snapshot/eval_demo/
gh workflow run eval-snapshot.yml   # rebuild the canonical release artifact
```

`make snapshot` writes `snapshot/eval_demo/{eval_demo.pgdump,eval_demo_qdrant.snapshot}`
from the running stack. The CI artifact is the source of truth for `make seed`; the
`eval-snapshot.yml` workflow does the same export on a clean EDGAR ingest.

### Override the location

`make seed SNAPSHOT_DIR=/path/to/snapshot` — both targets honour `SNAPSHOT_DIR`
(default `snapshot/eval_demo`).

---

## Remaining sub-parts

- **§1 budget profile**, **§3 security-pass**, **§4 Cloudflare Tunnel overlay**,
  **§5 UI banner** — see BACKLOG.md T-036. §4 needs the owner's Cloudflare domain
  (Q-05); until then the fallback is direct entry on the white IP with Caddy TLS.
