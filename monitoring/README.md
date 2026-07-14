# Monitoring layer B — EDGAR 8-K → alert (T-035)

Layer B watches for freshly filed **8-K** "current reports" (the form US
companies use to disclose material events between quarterly reports) and pushes
a short, guardrail-checked summary to an alert channel (Telegram, or a dry-run
log when no token is set).

The scheduling and HTTP glue live in **n8n** (a no-code workflow engine); the
summarization, guardrail, dedup and budget live in the **orchestrator**, so
every alert passes the same non-advice guardrail and daily budget as the rest
of the platform, and the whole pipeline is testable without secrets.

```
n8n (every 30 min)
  └─ Fetch recent 8-K events   GET data.sec.gov/submissions/CIK*.json  (EDGAR UA)
  └─ Ingest events (dedup)     POST app:8000/api/monitor/ingest-events  → { new: [...] }
  └─ Fan out new events
  └─ Summarize + alert         POST app:8000/api/monitor/summarize      → Telegram / dry-run
```

## Endpoints (orchestrator)

Both require the header `X-Monitor-Token: $MONITOR_TOKEN` when `MONITOR_TOKEN`
is set (empty in dev disables the check).

* `POST /api/monitor/ingest-events` — body `{ "events": [ {source, external_id,
  event_type, company_external_id, occurred_at, source_url, payload}, ... ] }`.
  Inserts into `monitored_events` with `UNIQUE(source, external_id)`; returns
  only the newly-inserted events: `{ new: [external_id...], new_count, seen_count }`.
* `POST /api/monitor/summarize` — body `{ "source", "external_id" }`. Fetches the
  8-K text through the source adapter, summarizes it (`summarize_event` router
  tier), runs the non-advice guardrail, stores `summary` + `alerted_at`, and
  dispatches the alert. Idempotent: an already-alerted event is not re-sent.

## Bring layer B up

n8n is **opt-in** — it is not part of `docker compose up`, so the core demo
stack (and n8n's image pull) is untouched unless you ask for it.

```bash
# 1) set the token both sides share, and (optionally) Telegram creds, in .env:
#    MONITOR_TOKEN=<random>            # empty = auth disabled (dev)
#    TELEGRAM_BOT_TOKEN=...            # empty = dry-run: alerts go to the app log
#    TELEGRAM_CHAT_ID=...
#    N8N_ENCRYPTION_KEY=<random>      # stable key so credentials survive restarts

# 2) start n8n (the app stack must already be up)
docker compose --profile monitoring up -d n8n

# 3) import the versioned workflow onto a clean n8n volume (≤10 min, criterion 4)
docker compose exec n8n n8n import:workflow --input=/workflows/edgar_8k.json

# 4) open http://localhost:5678, review the workflow, then Activate it
#    (or trigger a manual "Execute workflow" run to test immediately).
```

The workflow JSON carries a **stable `id`** (`ledgerlens8kmon1`), so the import
is an idempotent upsert — re-running step 3 refreshes the workflow in place
instead of erroring or creating a duplicate. (Current `n8n` rejects an import
with no `id`: `NOT NULL constraint failed: workflow_entity.id`.) The compose
service also pins `N8N_LISTEN_ADDRESS=0.0.0.0` so the editor server comes up on
IPv4-only Docker hosts (n8n's `::` default crash-loops there).

**Verified (criterion 4):** on a clean `n8n-data` volume, steps 2–3 above
restore the workflow in ~15 s end-to-end (image cached), well under the 10-min
bound; `n8n list:workflow` then shows `ledgerlens8kmon1`. A truly cold machine
adds the one-time n8n image pull (~2.5 GB) on top.

## Add / change a watched company

The watchlist lives in the **Fetch recent 8-K events** Code node (a small array
of `{ cik, ticker }`). Edit it there (CIK is the 10-digit SEC id, e.g. Apple is
`0000320193`) and save. `lookbackDays` in the same node bounds how far back a
tick considers filings "new" (dedup makes overlap harmless).

## Change the alert channel

Alerts are dispatched by the orchestrator, not by an n8n Telegram node, so the
guardrail and budget always gate them. Set `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID` in `.env` and restart the app. With no token the alert is a
**dry-run** — logged (`alert_dry_run`), not sent — which is what CI and the
secretless demo use.

## If events spam the channel

* Dedup is by `(source, external_id)` (the accession number) — a filing is
  summarized and alerted **once**. Re-ticks are cheap no-ops.
* The daily cap is `layer_b.max_summaries_per_day` in `config/budgets.yaml`
  (default 50). Once hit, `summarize` returns `budget_exceeded` and leaves the
  event pending (no alert) until the next day — the alert never fires twice.
* To pause entirely: deactivate the workflow in n8n, or stop the service
  (`docker compose --profile monitoring stop n8n`). Pending events remain in
  `monitored_events` and are picked up when you resume.

## RU branch (существенные факты) — TODO

The Russian equivalent of 8-K monitoring (существенные факты) would be sourced
from **e-disclosure.ru**, whose adapter is a stub in this build (T-032). When
that adapter implements `poll_events` / `fetch_event_text`, add a second n8n
branch (or a `source: 'edisclosure'` watchlist) that posts to the same
endpoints — the orchestrator side is already source-agnostic (the text fetch
goes through `adapters.base.get_adapter(source).fetch_event_text`).

## Tests

* `tests/unit/test_monitoring.py` — alert formatting/dispatch, dry-run, daily
  budget, guardrail safety net (no DB/network).
* `tests/integration/test_monitoring_integration.py` (`-m slow`) — dedup on
  re-tick, full summarize+alert flow, idempotency, budget/no-text/not-found.
