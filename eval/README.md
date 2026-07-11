# Eval harness (T-029)

`eval/run.py` drives the golden dataset (`eval/golden/`) through the real
stack end-to-end — orchestrator → workers → MCP tools → Postgres/Qdrant —
scores every case, writes `eval_runs`/`eval_results`, and emits a report.

```
uv run python -m eval.run --profile ci --base-url http://localhost:8000
uv run python -m eval.run --profile full --base-url http://localhost:8000
```

`--profile ci` runs the 10 balanced `ci`-tagged cases (cheap, every PR);
`--profile full` runs all 41 (nightly — see T-030). Compose must already be
up (`make up`) with the demo corpus ingested (`make demo-ingest`).

## Scoring

- **numeric_sql / ratio** — structural (`eval.scoring.score_numeric`): the
  expected value/ratio is matched against the answer's `key_values` first,
  then against the leading 6 significant digits of the answer text (formats
  vary — "$416.161 billion" vs "416,161,000,000"). Deterministic, no judge.
- **narrative_rag** — structural `must_contain`/citation-presence
  (`sec.gov` in a citation's `source_url`) plus an LLM-judge faithfulness
  rubric (RAGAS-style: does the answer only state what the retrieved
  citations actually say).
- **multi_step** — structural `min_plan_steps` (executed worker steps in the
  `steps` table), `key_numbers`, `must_contain`, citation presence, plus an
  LLM-judge GEval-style correctness/groundedness rubric.
- **guardrail** — structural `must_not_contain` plus an LLM-judge rubric
  checking the answer gives no buy/sell/hold/price-target advice.
- **no_data** — structural `must_not_invent` (the answer must not contain
  the listed real-world figures) plus an LLM-judge honesty rubric checking
  the answer explicitly admits the gap.

Structural checks are strict (pass/fail per case). Judge scores are noisy
per case — the gate in `config/eval-thresholds.yaml` applies to the
**category average**, never to a single case. The judge prompt is versioned
in `prompts/judge.md`; every judge call is `task_class=judge`
(`config/router.yaml` pins it to `cloud_strong`).

## Cost

Judge-call cost is metered against `run_cost_cap_usd` in
`config/eval-thresholds.yaml` (`eval.run.CostTracker`) — once exceeded, the
harness stops issuing new judge calls for the rest of the run and marks the
remaining judge scores as skipped (visible in `report.json`). Worker/
orchestrator cost comes straight from each run's `usage.cost_usd`
(`llm_calls`, the same source of truth as the rest of the app).

## Thresholds and exit code

`config/eval-thresholds.yaml` — `numeric_accuracy`, `citation_coverage`,
`faithfulness`, `guardrail_block`, `nodata_honesty`, `avg_cost_usd_per_case`.
The harness computes the aggregate for each metric present in the run and
exits non-zero if any threshold is violated — this is the exit code T-030
wires into CI.

`config/eval-thresholds.yaml`'s `non_blocking` list names metrics that are
still measured, reported, and shown in every `THRESHOLD VIOLATIONS` section,
but excluded from the exit-code decision — a tracked, known-failing gate
that shouldn't turn every build red before its fix lands. Currently just
`faithfulness` (0.33-0.39 measured vs 0.7 threshold as of 2026-07-11 —
narrative synthesis pads answers with facts beyond the retrieved chunks;
see BACKLOG T-041). Remove a metric from this list once its fix is verified
to hold the threshold on a full-profile run.

## Output

- `eval_runs` / `eval_results` rows (`git_sha`, `profile`, `summary` JSONB;
  per-case `passed`/`scores`/`run_id`/`details`).
- `eval/reports/<UTC timestamp>/report.json` — full machine-readable dump
  (summary, violations, every case's scores/details).
- `eval/reports/<UTC timestamp>/report.md` — category table, metrics vs
  thresholds, regressions vs the previous run of the same profile (pulled
  from the last `eval_runs` row), threshold violations, and the top ~15
  failing cases with question/expected/run_id/details for triage.

## Network retry

`_ask()` retries a case once on an `httpx.HTTPError` (transient network
failure to `/api/chat`); it does not retry a run that came back but reported
`status != succeeded/budget_exceeded` — that is a real result to score
(includes it as a fail), not a network blip.
