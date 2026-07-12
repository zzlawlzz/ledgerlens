# Vector store benchmark — pgvector vs Qdrant (T-037)

Compares the two candidate vector stores on the **same** dense corpus and query
set, so the choice recorded in ADR-2 rests on measured numbers rather than
folklore. `bench.py` writes `REPORT.md` (+ PNG charts) in this directory.

## What it measures

| Metric | How |
|---|---|
| Index build time | wall clock around `CREATE INDEX` (pgvector) / upsert+HNSW drain (Qdrant) |
| Query latency p50/p95 | single-thread, warm, top-k per query |
| Recall@k | agreement of the ANN top-k with an **exact** brute-force cosine top-k over the identical set |
| Index footprint | pgvector: `pg_relation_size`; Qdrant exposes no per-collection byte size over its API |

Both stores use identical HNSW parameters (`m=16`, `ef_construction=64`,
`ef_search=64`) for an apples-to-apples comparison. Qdrant's `indexing_threshold`
is lowered so it actually builds HNSW at small N (its 20k default answers
exactly and would flatter its recall/latency).

## Corpus and queries

- **Corpus:** the real `narrative_chunks` embeddings (e5-large, 1024-dim,
  cosine) pulled live from Qdrant — no re-embedding. `--scale N` inflates it
  with Gaussian-perturbed copies for load testing; these are **synthetic** and
  labelled as such in the report.
- **Queries:** ~50 curated finance questions + templated variants over the
  company names in the corpus, embedded with the e5-large *query* prefix.

> **Recall here is ANN *approximation* quality**, not semantic relevance — it
> asks "did the index return the same neighbours as exact search?". Retrieval
> relevance is the eval harness's job, not this benchmark's.

## Run it

```bash
make bench-vector                                   # real corpus (~1.1k vectors), fast smoke
make bench-vector BENCH_ARGS="--scale 20 --queries 200"   # ~22k vectors, meaningful ANN load
uv run --group bench python benchmarks/vector/bench.py --scale 20 --queries 200
```

Connections default to the host-mapped dev stack (`localhost:5432`,
`localhost:6333`); override with `BENCH_PG_HOST` / `BENCH_PG_PORT` /
`BENCH_QDRANT_URL` to point at another deployment. `matplotlib` (charts) lives
in the optional `bench` dependency group — hence `uv run --group bench`; without
it the run still completes and the report notes charts were skipped.

## ⚠️ Disk

Each vector costs ~4 KB of data plus its HNSW graph in **both** stores. A
`--scale 90` (~100k) run writes on the order of **1 GB** to the Postgres and
Qdrant Docker volumes; on a near-full Docker disk that can wedge the whole WSL2
backend. The script prints an estimate and warns above ~50k, and it always
drops its temp schema/collection on exit (even on failure) — but check free
space before large runs. On this dev machine, stay at `--scale ≤ 20`; reserve
the ~100k load run for the target node with headroom.

## Scope

Dense-vs-dense only. Hybrid (dense + BM25) is a Qdrant-only capability in this
stack, so it is out of scope here to keep the comparison symmetric; hybrid
retrieval quality is covered by the eval harness.
