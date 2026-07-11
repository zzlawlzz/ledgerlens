# Observability (T-034)

Grafana dashboards over the observability tables written by the orchestrator
and the eval harness (`runs`, `steps`, `llm_calls`, `tool_calls`, `eval_runs`,
`eval_results` — schema in `db/versions/002_observability.py`). Everything is
provisioning-as-code: the datasource and the dashboards live in this directory
and are mounted into the Grafana container, so `make up` brings Grafana up
already wired — no manual import.

## How to bring it up

1. Set the two Grafana variables in `.env` (see `.env.example`):
   - `GRAFANA_ADMIN_PASSWORD` — admin login; empty means the compose default
     `admin`.
   - `GRAFANA_RO_PASSWORD` — password for the `grafana_ro` Postgres role the
     datasource connects with. Required by migration 003.
2. Apply migrations (creates the `grafana_ro` role idempotently):

   ```bash
   make db-up db-migrate
   ```

3. Start the stack:

   ```bash
   make up          # or: make up-no-local / docker compose up -d grafana
   ```

4. Open http://localhost:3001 (port 3000 is taken by the web UI).

## Access model

- **Anonymous access is enabled with the Viewer role** — this is intentional,
  for showing the dashboards to visitors without handing out credentials.
  Viewers cannot edit dashboards or settings.
- Editing requires the admin login (`admin` / `GRAFANA_ADMIN_PASSWORD`).
- The datasource connects as **`grafana_ro`**, a SELECT-only role over the
  observability tables (created by `db/versions/003_grafana_ro.py`). It has
  no write privileges and no access to domain tables — that role is the real
  security boundary, since any Grafana user with datasource access can issue
  arbitrary SQL through the query API.

## Dashboards

| Dashboard | File | What it shows |
|---|---|---|
| LedgerLens / Operations | `dashboards/operations.json` | Run counts and statuses over time, run latency p50/p95, cost per run and per day, cost split by task_class and provider, fallback share, local-vs-cloud LLM split, tool latency p95, tool/LLM errors |
| LedgerLens / Session drill-down | `dashboards/session.json` | One run by `run_id` (template variable listing recent runs): step timeline, LLM calls, tool calls, total cost, final status, guardrail verdict |
| LedgerLens / Quality | `dashboards/quality.json` | Eval pass rate by category per eval run, metric trends from `eval_runs.summary`, eval cost per run, recent failed cases |

## Layout

```
observability/
  datasources/postgres.yaml   # datasource provisioning (grafana_ro, ${ENV} secrets)
  dashboards.yaml             # file provider: loads dashboards/ into "LedgerLens" folder
  dashboards/*.json           # the dashboards themselves
```

Mounts (see the `grafana` service in `docker-compose.yml`):

- `datasources/` -> `/etc/grafana/provisioning/datasources`
- `dashboards.yaml` -> `/etc/grafana/provisioning/dashboards/ledgerlens.yaml`
- `dashboards/` -> `/var/lib/grafana/dashboards`

Secrets never live in these files: `${POSTGRES_DB}` and
`${GRAFANA_RO_PASSWORD}` in `datasources/postgres.yaml` are expanded by
Grafana from the container environment (passed via docker-compose from
`.env`). A unit test (`tests/unit/test_grafana_provisioning.py`) enforces
this.

## How to add a panel

1. Log in as admin at http://localhost:3001, edit a dashboard, add the panel
   (SQL against the observability tables; use `$__timeFilter(created_at)` /
   `$__timeGroup(created_at, $__interval)` macros for time-scoped panels).
2. Dashboard settings -> JSON Model, copy the JSON.
3. Paste it into the matching file in `dashboards/` (keep `uid` and `title`
   stable; `allowUiUpdates` is off, the repo file is the source of truth).
   The provider re-reads files every 30 s — no restart needed.
4. `uv run pytest tests/unit/test_grafana_provisioning.py` to sanity-check.

If a new panel needs a table `grafana_ro` cannot read yet, extend the GRANT
list in a new migration (pattern: `db/versions/003_grafana_ro.py`).
