"""Vector store benchmark: pgvector (HNSW) vs Qdrant (HNSW) — T-037.

Runs both stores over the *same* dense corpus and query set and reports:

  * index build time (wall clock),
  * top-k query latency (p50 / p95, single-thread, warm),
  * recall@k against an exact brute-force cosine ground truth,
  * index footprint (pgvector: ``pg_relation_size``; Qdrant: on-disk via the
    collection telemetry).

Corpus = the real ``narrative_chunks`` embeddings pulled from the running
Qdrant (e5-large, 1024d, cosine). ``--scale N`` inflates the corpus with
perturbed copies for load testing — this is synthetic and labelled as such in
the report. Recall is always measured against exact nearest neighbours over
the same indexed set, so it reflects the ANN index's *approximation* quality,
not semantic relevance (which is what the eval harness measures instead).

Reproduce with ``make bench-vector`` (defaults) or, e.g.::

    uv run --group bench python benchmarks/vector/bench.py --scale 90 --queries 200

Connections default to the host-mapped dev stack (localhost); override with
``BENCH_PG_HOST`` / ``BENCH_QDRANT_URL`` to point at another deployment.
"""

from __future__ import annotations

import argparse
import os
import platform
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psycopg
from qdrant_client import QdrantClient, models

from common.config import get_settings
from rag.embedding import ChunkEmbedder
from rag.indexer import COLLECTION, DENSE_VECTOR, META_POINT_ID

HERE = Path(__file__).resolve().parent
DIM = 1024
BENCH_COLLECTION = "bench_vectors"
PG_SCHEMA = "bench"
PG_TABLE = "bench.chunks"

# HNSW parameters held identical across both stores for a fair comparison.
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
HNSW_EF_SEARCH = 64
# Force Qdrant to actually build HNSW even for a small corpus (its default
# indexing_threshold is 20k, below which it answers exactly — not comparable).
QDRANT_INDEXING_THRESHOLD = 100


# --------------------------------------------------------------------------- #
# Corpus + queries
# --------------------------------------------------------------------------- #


def _qdrant_url() -> str:
    return os.environ.get("BENCH_QDRANT_URL") or get_settings().qdrant_url.replace(
        "qdrant", "localhost"
    )


def _pg_conninfo() -> str:
    s = get_settings()
    host = os.environ.get("BENCH_PG_HOST", "localhost")
    port = os.environ.get("BENCH_PG_PORT", "5432")
    return (
        f"host={host} port={port} dbname={s.postgres_db} "
        f"user={s.postgres_user} password={s.postgres_password}"
    )


def load_corpus(url: str) -> tuple[np.ndarray, list[str]]:
    """Pull every content vector (+ a company hint) from the live collection."""
    client = QdrantClient(url=url)
    vectors: list[list[float]] = []
    companies: list[str] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            limit=256,
            with_vectors=True,
            with_payload=["company", "ticker"],
            offset=offset,
        )
        for p in points:
            if p.id == META_POINT_ID:
                continue
            vec = p.vector[DENSE_VECTOR] if isinstance(p.vector, dict) else p.vector
            vectors.append(list(vec))
            payload = p.payload or {}
            hint = payload.get("company") or payload.get("ticker")
            if hint:
                companies.append(str(hint))
        if offset is None:
            break
    client.close()
    return np.asarray(vectors, dtype=np.float32), companies


def scale_corpus(base: np.ndarray, scale: int, *, seed: int = 7) -> np.ndarray:
    """Inflate the corpus with perturbed copies (synthetic load; documented)."""
    if scale <= 1:
        return base
    rng = np.random.default_rng(seed)
    copies = [base]
    for _ in range(scale - 1):
        noise = rng.normal(0.0, 0.01, size=base.shape).astype(np.float32)
        copies.append(base + noise)
    return np.vstack(copies)


_CURATED_QUERIES = [
    "What are the principal risk factors the company discloses?",
    "How does management describe the company's liquidity and capital resources?",
    "What does the company say about supply chain disruptions?",
    "How is competition characterised in the business overview?",
    "What are the main sources of revenue?",
    "What legal proceedings are the company involved in?",
    "How does the company describe foreign exchange and currency risk?",
    "What forward-looking statements and uncertainties are highlighted?",
    "How does management discuss gross margin trends?",
    "What cybersecurity risks does the company disclose?",
    "How does the company describe its research and development priorities?",
    "What macroeconomic conditions does management flag as headwinds?",
    "How does the company address regulatory and compliance exposure?",
    "What does the company say about dependence on key customers or suppliers?",
    "How does management describe capital expenditure plans?",
    "What is disclosed about the company's debt maturities and covenants?",
    "How does the company describe seasonality in its business?",
    "What intellectual property protections does the company rely on?",
    "How does management explain year-over-year changes in operating expenses?",
    "What environmental and climate-related risks are disclosed?",
]

_TEMPLATES = [
    "What are the biggest risks facing {c}?",
    "How did {c} describe its revenue drivers?",
    "Summarise {c}'s liquidity position.",
    "What does {c} say about competition?",
    "How does {c} discuss its cost structure?",
    "What regulatory risks does {c} disclose?",
    "How does {c} describe demand for its products?",
    "What guidance or outlook does {c} provide?",
]


def build_queries(
    embedder: ChunkEmbedder, companies: list[str], n_total: int, *, seed: int = 11
) -> tuple[list[str], np.ndarray]:
    """~50 curated + rest templated over companies seen in the corpus."""
    rng = np.random.default_rng(seed)
    uniq = sorted(set(companies)) or ["the company"]
    texts = list(_CURATED_QUERIES[: min(50, n_total)])
    while len(texts) < n_total:
        template = _TEMPLATES[rng.integers(len(_TEMPLATES))]
        company = uniq[rng.integers(len(uniq))]
        texts.append(template.format(c=company))
    texts = texts[:n_total]
    matrix = np.asarray(embedder.embed_dense(texts, kind="query"), dtype=np.float32)
    return texts, matrix


# --------------------------------------------------------------------------- #
# Ground truth + metrics
# --------------------------------------------------------------------------- #


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def exact_topk(corpus: np.ndarray, queries: np.ndarray, k: int) -> list[set[int]]:
    """Brute-force cosine ground truth: exact top-k index set per query."""
    c = _normalise(corpus)
    q = _normalise(queries)
    sims = q @ c.T  # [Q, N]
    top = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]
    return [set(int(i) for i in row) for row in top]


def recall_at_k(truth: list[set[int]], got: list[list[int]], k: int) -> float:
    hits = [len(t & set(g[:k])) / k for t, g in zip(truth, got, strict=True)]
    return float(statistics.fmean(hits))


def _percentiles(latencies_ms: list[float]) -> tuple[float, float, float]:
    ordered = sorted(latencies_ms)
    return (
        statistics.fmean(ordered),
        ordered[int(0.50 * (len(ordered) - 1))],
        ordered[int(0.95 * (len(ordered) - 1))],
    )


@dataclass
class StoreResult:
    name: str
    build_s: float
    recall: float
    lat_mean_ms: float
    lat_p50_ms: float
    lat_p95_ms: float
    index_bytes: int
    note: str = ""
    extra: dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# pgvector
# --------------------------------------------------------------------------- #


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v.tolist()) + "]"


def run_pgvector(corpus: np.ndarray, queries: np.ndarray, k: int) -> StoreResult:
    conn = psycopg.connect(_pg_conninfo(), autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {PG_SCHEMA}")
        cur.execute(f"CREATE TABLE {PG_TABLE} (id bigint PRIMARY KEY, embedding vector({DIM}))")

        with cur.copy(f"COPY {PG_TABLE} (id, embedding) FROM STDIN") as copy:
            for i, v in enumerate(corpus):
                copy.write_row((i, _vec_literal(v)))

        t0 = time.perf_counter()
        cur.execute(
            f"CREATE INDEX ON {PG_TABLE} USING hnsw (embedding vector_cosine_ops) "
            f"WITH (m = {HNSW_M}, ef_construction = {HNSW_EF_CONSTRUCTION})"
        )
        build_s = time.perf_counter() - t0

        cur.execute(f"SET hnsw.ef_search = {HNSW_EF_SEARCH}")
        latencies: list[float] = []
        got: list[list[int]] = []
        for v in queries:
            lit = _vec_literal(v)
            t = time.perf_counter()
            cur.execute(
                f"SELECT id FROM {PG_TABLE} ORDER BY embedding <=> %s::vector LIMIT %s",
                (lit, k),
            )
            rows = cur.fetchall()
            latencies.append((time.perf_counter() - t) * 1000.0)
            got.append([int(r[0]) for r in rows])

        cur.execute(
            "SELECT pg_relation_size(indexrelid) FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indrelid WHERE c.relname = 'chunks' "
            "AND c.relnamespace = %s::regnamespace",
            (PG_SCHEMA,),
        )
        row = cur.fetchone()
        index_bytes = int(row[0]) if row else 0

        mean, p50, p95 = _percentiles(latencies)
        truth = exact_topk(corpus, queries, k)
        return StoreResult(
            name="pgvector (HNSW)",
            build_s=build_s,
            recall=recall_at_k(truth, got, k),
            lat_mean_ms=mean,
            lat_p50_ms=p50,
            lat_p95_ms=p95,
            index_bytes=index_bytes,
            note=f"m={HNSW_M}, ef_construction={HNSW_EF_CONSTRUCTION}, ef_search={HNSW_EF_SEARCH}",
        )
    finally:
        # Always drop the temp schema, even if the build crashed mid-way — a
        # half-loaded 100k-row table must not linger and fill the volume.
        try:
            cur.execute(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE")
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        conn.close()


# --------------------------------------------------------------------------- #
# Qdrant
# --------------------------------------------------------------------------- #


def run_qdrant(url: str, corpus: np.ndarray, queries: np.ndarray, k: int) -> StoreResult:
    client = QdrantClient(url=url, timeout=120)
    try:
        if client.collection_exists(BENCH_COLLECTION):
            client.delete_collection(BENCH_COLLECTION)
        client.create_collection(
            collection_name=BENCH_COLLECTION,
            vectors_config=models.VectorParams(
                size=DIM,
                distance=models.Distance.COSINE,
                hnsw_config=models.HnswConfigDiff(m=HNSW_M, ef_construct=HNSW_EF_CONSTRUCTION),
            ),
            optimizers_config=models.OptimizersConfigDiff(
                indexing_threshold=QDRANT_INDEXING_THRESHOLD
            ),
        )

        t0 = time.perf_counter()
        batch = 512
        for start in range(0, len(corpus), batch):
            chunk = corpus[start : start + batch]
            client.upsert(
                collection_name=BENCH_COLLECTION,
                points=[
                    models.PointStruct(id=start + i, vector=v.tolist()) for i, v in enumerate(chunk)
                ],
                wait=True,
            )
        # Wait for the HNSW build to drain before timing queries.
        deadline = time.perf_counter() + 300
        while time.perf_counter() < deadline:
            info = client.get_collection(BENCH_COLLECTION)
            if info.status == models.CollectionStatus.GREEN and (
                info.indexed_vectors_count or 0
            ) >= len(corpus):
                break
            time.sleep(0.5)
        build_s = time.perf_counter() - t0

        latencies: list[float] = []
        got: list[list[int]] = []
        search = models.SearchParams(hnsw_ef=HNSW_EF_SEARCH)
        for v in queries:
            t = time.perf_counter()
            res = client.query_points(
                collection_name=BENCH_COLLECTION,
                query=v.tolist(),
                limit=k,
                search_params=search,
                with_payload=False,
            )
            latencies.append((time.perf_counter() - t) * 1000.0)
            got.append([int(pt.id) for pt in res.points])

        info = client.get_collection(BENCH_COLLECTION)
        indexed = int(info.indexed_vectors_count or 0)

        mean, p50, p95 = _percentiles(latencies)
        truth = exact_topk(corpus, queries, k)
        return StoreResult(
            name="Qdrant (HNSW)",
            build_s=build_s,
            recall=recall_at_k(truth, got, k),
            lat_mean_ms=mean,
            lat_p50_ms=p50,
            lat_p95_ms=p95,
            index_bytes=0,  # Qdrant does not expose a per-collection byte size over the API
            note=(
                f"m={HNSW_M}, ef_construct={HNSW_EF_CONSTRUCTION}, hnsw_ef={HNSW_EF_SEARCH}, "
                f"indexing_threshold={QDRANT_INDEXING_THRESHOLD}"
            ),
            extra={"indexed_vectors": float(indexed)},
        )
    finally:
        try:
            client.delete_collection(BENCH_COLLECTION)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
        client.close()


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def _mb(nbytes: int) -> str:
    return "n/a" if nbytes == 0 else f"{nbytes / 1e6:.1f} MB"


def _charts(results: list[StoreResult]) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    names = [r.name for r in results]
    written: list[str] = []

    def bar(filename: str, title: str, ylabel: str, values: list[float]) -> None:
        fig, ax = plt.subplots(figsize=(5, 3.2))
        bars = ax.bar(names, values, color=["#4c78a8", "#f58518"][: len(names)])
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.bar_label(bars, fmt="%.2f", padding=2)
        fig.tight_layout()
        fig.savefig(HERE / filename, dpi=120)
        plt.close(fig)
        written.append(filename)

    bar(
        "latency_p95.png",
        "Query latency p95 (top-k, single thread)",
        "ms",
        [r.lat_p95_ms for r in results],
    )
    bar("recall.png", "Recall@k vs exact cosine", "recall", [r.recall for r in results])
    bar("build_time.png", "Index build time", "seconds", [r.build_s for r in results])
    return written


def write_report(
    results: list[StoreResult], *, corpus_n: int, real_n: int, scale: int, queries_n: int, k: int
) -> None:
    charts = _charts(results)
    lines: list[str] = []
    lines.append("# Vector store benchmark — pgvector vs Qdrant (T-037)\n")
    lines.append(
        "_Auto-generated by `benchmarks/vector/bench.py` "
        f"({time.strftime('%Y-%m-%d %H:%M:%S')})._\n"
    )
    lines.append("## Setup\n")
    lines.append(
        f"- **Corpus:** {corpus_n} vectors, {DIM}-dim, cosine "
        f"(real `narrative_chunks` embeddings = {real_n}"
        + (f", ×{scale} synthetic perturbed copies for load" if scale > 1 else "")
        + ")."
    )
    lines.append(
        f"- **Queries:** {queries_n} e5-large query embeddings "
        "(≈50 curated finance questions + templated over corpus companies)."
    )
    lines.append(f"- **k:** {k}. **Ground truth:** exact brute-force cosine over the same set.")
    lines.append(
        f"- **HNSW (both):** m={HNSW_M}, ef_construction={HNSW_EF_CONSTRUCTION}, "
        f"ef_search={HNSW_EF_SEARCH}."
    )
    lines.append(
        f"- **Hardware:** {platform.platform()}; "
        f"{os.cpu_count()} logical CPUs, numpy {np.__version__}. "
        "_(dev machine — replace with the target node's spec when publishing.)_\n"
    )
    lines.append(
        "> Recall here measures ANN **approximation** quality (agreement with exact "
        "nearest neighbours on the identical set), not semantic relevance — the eval "
        "harness covers relevance. Synthetic vectors (scale > 1) are perturbations, so "
        "they stress the index, not the retriever.\n"
    )

    lines.append("## Results\n")
    lines.append(f"| Store | Build | Recall@{k} | Latency mean | p50 | p95 | Index size |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|")
    for r in results:
        lines.append(
            f"| {r.name} | {r.build_s:.2f}s | {r.recall:.3f} | {r.lat_mean_ms:.2f} ms | "
            f"{r.lat_p50_ms:.2f} ms | {r.lat_p95_ms:.2f} ms | {_mb(r.index_bytes)} |"
        )
    lines.append("")
    for r in results:
        lines.append(
            f"- **{r.name}** — {r.note}."
            + (f" indexed_vectors={int(r.extra['indexed_vectors'])}." if r.extra else "")
        )
    lines.append("")

    if charts:
        lines.append("## Charts\n")
        for c in charts:
            lines.append(f"![{c}]({c})")
        lines.append("")
    else:
        lines.append("_Charts skipped: matplotlib not installed (run with `--group bench`)._\n")

    lines.append("## Notes & limitations\n")
    lines.append("- Single-thread, warm-cache latency; no concurrency modelling.")
    lines.append(
        "- Qdrant `indexing_threshold` is lowered so HNSW is actually built at small N "
        "(its default 20k would answer exactly and skew the comparison)."
    )
    lines.append(
        "- Qdrant exposes no per-collection byte size over the API; pgvector index size "
        "is `pg_relation_size`. Compare build time / recall / latency, not raw bytes."
    )
    lines.append(
        "- Hybrid (dense+BM25) is Qdrant-only in this stack and is out of scope here "
        "(dense-vs-dense keeps the comparison apples-to-apples)."
    )

    (HERE / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description="pgvector vs Qdrant vector-store benchmark")
    parser.add_argument("--scale", type=int, default=1, help="synthetic corpus multiplier")
    parser.add_argument("--queries", type=int, default=200, help="number of query vectors")
    parser.add_argument("--k", type=int, default=10, help="top-k")
    parser.add_argument("--only", choices=["pgvector", "qdrant"], help="run a single store")
    args = parser.parse_args()

    url = _qdrant_url()
    print(f"[bench] loading corpus from {url} ...")
    base, companies = load_corpus(url)
    print(f"[bench] real corpus: {len(base)} vectors, dim {base.shape[1]}")
    corpus = scale_corpus(base, args.scale)
    if args.scale > 1:
        print(f"[bench] scaled corpus: {len(corpus)} vectors (x{args.scale} synthetic)")
    # Rough footprint: raw data + HNSW graph, across both stores.
    est_gb = len(corpus) * DIM * 4 * 2.5 / 1e9
    if len(corpus) > 50_000:
        print(
            f"[bench] WARNING: this will write ~{est_gb:.1f} GB of data+index to the "
            "Postgres and Qdrant volumes (Docker disk). Ensure there is room — a "
            "full volume wedges the stack. Cleanup runs even on failure, but plan disk."
        )

    print("[bench] embedding queries (e5-large, CPU - first call loads the model) ...")
    embedder = ChunkEmbedder()
    query_texts, queries = build_queries(embedder, companies, args.queries)
    print(f"[bench] {len(query_texts)} query vectors")

    results: list[StoreResult] = []
    if args.only != "qdrant":
        print("[bench] running pgvector ...")
        results.append(run_pgvector(corpus, queries, args.k))
    if args.only != "pgvector":
        print("[bench] running Qdrant ...")
        results.append(run_qdrant(url, corpus, queries, args.k))

    write_report(
        results,
        corpus_n=len(corpus),
        real_n=len(base),
        scale=args.scale,
        queries_n=len(query_texts),
        k=args.k,
    )
    print(f"[bench] wrote {HERE / 'REPORT.md'}")
    for r in results:
        print(
            f"  {r.name:16} build={r.build_s:6.2f}s recall@{args.k}={r.recall:.3f} "
            f"p50={r.lat_p50_ms:6.2f}ms p95={r.lat_p95_ms:6.2f}ms"
        )


if __name__ == "__main__":
    main()
