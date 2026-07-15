# Demo deployment (T-036)

Runbook for the public demo: a stranger can play with LedgerLens without hitting
EDGAR, without draining the budget, and without breaking the install.

Status of the T-036 sub-parts:

| Part | What | State |
|------|------|-------|
| §2 | `make seed` / `make snapshot` — corpus without EDGAR | **done** (this doc) |
| §1 | `BUDGET_PROFILE=demo` rate/cost limits | **done** (`orchestrator/demo_limits.py`) |
| §3 | Security-pass checklist | **done** (`SECURITY.md` + overlay) |
| §4 | `cloudflared` overlay + public TLS | **done** (`cloudflared` in the overlay) |
| §5 | UI "public demo" banner | **done** (`BUDGET_PROFILE=demo` gated) |

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

## Go live (§4 — Cloudflare Tunnel)

> **Note — this repo's own public demo no longer uses the Cloudflare Tunnel path
> below.** It migrated to a workstation backend behind a small VPS door
> (reverse-tunnel + nginx, DNS-only + Let's Encrypt, no CDN proxy in the SSE
> path) because Cloudflare's proxy truncated SSE responses in-browser. See
> [migration notes](../../docs/migration-workstation.md). The Cloudflare Tunnel
> runbook here remains a valid generic option for self-hosting a public demo.

The public entry point is a `cloudflared` connector in the demo overlay. It dials
**out** to Cloudflare (no inbound port on the host), and the tunnel's dashboard
ingress routes only the demo hostname → `web:80`. Nothing else has a public route;
the host firewall (ufw) denies all inbound (see SECURITY.md).

1. Create a tunnel in the Cloudflare Zero Trust dashboard (Networks → Tunnels),
   add a public hostname `app.<domain>` → `HTTP` → `web:80`, and copy the
   connector **token**.
2. Put it in the (gitignored) `.env`: `CLOUDFLARE_TUNNEL_TOKEN=<token>`.
3. Bring up the demo with both overlays merged:

   ```bash
   docker compose -f docker-compose.yml -f deploy/demo/docker-compose.demo.yml up -d
   ```

   This runs the app/worker/MCP in `BUDGET_PROFILE=demo` (admission limits + tight
   run-budget active), adds `no-new-privileges` to every service, and starts
   `cloudflared`. An empty token makes the connector exit, so the demo is opt-in.
4. Verify: `docker logs platform-cloudflared-1` shows *Registered tunnel
   connection*, and `https://app.<domain>` serves the UI over Cloudflare's TLS.

Fallback without a domain: direct entry on the white IP with Caddy TLS.

The current public demo is served at **`https://ledgerlens.space/app/`** (frontend
on GitHub Pages) with the API at **`https://api.ledgerlens.space`** (VPS door →
tunnel → workstation backend). The old `app.ledgerlens.space` host is retired.
