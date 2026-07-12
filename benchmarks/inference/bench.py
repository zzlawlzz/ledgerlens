"""Inference benchmark: local CPU models vs cloud API (T-037 §1).

Runs the same 20-prompt suite (``prompts.py``, four task classes) against one
or more chat models and reports, per model and per task class:

  * **TTFT** — time to first streamed token (responsiveness),
  * **tok/s** — output tokens / generation time (throughput after first token),
  * **latency** — full wall-clock per call (p50 / p95),
  * **cost** — USD, priced from ``config/prices.yaml`` for API tiers; local
    (ollama) calls are $0 at the meter, with an amortisation note in the report
    (T-037 §1: local cost is an energy/amortisation *estimate*, not measured),
  * **quality** — an LLM judge scores each answer 1–5 against a per-task rubric.

Two runner families, matching ``config/router.yaml`` tiers:

  (a) **cloud API** — ``deepseek-v4-flash`` (cloud_cheap) and ``deepseek-v4-pro``
      (cloud_strong). Reproducible anywhere with ``DEEPSEEK_API_KEY``; this is
      the default run.
  (b) **local CPU** — ollama models on the home 2×EPYC node, named with
      ``--local-models qwen3.5:27b,llama3.2:4b`` (requires ``OLLAMA_BASE_URL``).
      Left to the home node — this dev box does not host the CPU candidates.

Reproduce the API part with ``make bench-inference`` (or add
``BENCH_ARGS='--local-models qwen3.5:27b,llama3.2:4b'`` on the EPYC node).
GPU (vLLM) inference is **not** measured — excluded by owner decision Q-07;
the report says so without quoting anyone else's numbers as our own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from common.config import get_settings, load_yaml_config

from .prompts import PROMPTS, RUBRIC, TASK_CLASSES, BenchPrompt

HERE = Path(__file__).resolve().parent
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Cloud tiers to benchmark by default, resolved from config/router.yaml so the
# pins stay in one place. (label, router-tier-name).
DEFAULT_API_TIERS = (("cloud_cheap", "cloud_cheap"), ("cloud_strong", "cloud_strong"))


# --------------------------------------------------------------------------- #
# Model construction (mirrors model_router tier factory, + streaming usage)
# --------------------------------------------------------------------------- #


@dataclass
class Candidate:
    label: str
    provider: str
    model: str
    timeout_s: float
    thinking: str | None
    chat_model: Any


def _chat_model(provider: str, model: str, timeout_s: float, thinking: str | None) -> Any:
    """Build an openai-compatible LangChain chat model for one candidate.

    Mirrors ``model_router.router._default_model_factory`` but is kept local so
    the benchmark does not depend on orchestrator internals.
    """
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if provider == "deepseek":
        return ChatOpenAI(
            model=model,
            api_key=settings.deepseek_api_key,  # type: ignore[arg-type]
            base_url=DEEPSEEK_BASE_URL,
            timeout=timeout_s,
            temperature=0.0,
            extra_body={"thinking": {"type": thinking or "disabled"}},
        )
    if provider == "ollama":
        return ChatOpenAI(
            model=model,
            api_key="ollama",  # type: ignore[arg-type]
            base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
            timeout=timeout_s,
            temperature=0.0,
        )
    raise SystemExit(f"[bench] provider {provider!r} not wired in the inference benchmark")


def resolve_candidates(args: argparse.Namespace) -> list[Candidate]:
    settings = get_settings()
    router = load_yaml_config("router")
    tiers = router.get("tiers", {})
    out: list[Candidate] = []

    if not args.local_only:
        if not settings.deepseek_api_key:
            print("[bench] no DEEPSEEK_API_KEY — skipping cloud API candidates")
        else:
            for label, tier_name in DEFAULT_API_TIERS:
                spec = tiers.get(tier_name)
                if not spec:
                    continue
                model = str(spec["model"])
                thinking = spec.get("thinking")
                timeout_s = float(spec.get("timeout_s", 60))
                out.append(
                    Candidate(
                        label=label,
                        provider="deepseek",
                        model=model,
                        timeout_s=timeout_s,
                        thinking=thinking,
                        chat_model=_chat_model("deepseek", model, timeout_s, thinking),
                    )
                )

    for model in args.local_models:
        model = model.strip()
        if not model:
            continue
        out.append(
            Candidate(
                label=f"ollama:{model}",
                provider="ollama",
                model=model,
                timeout_s=args.local_timeout,
                thinking=None,
                chat_model=_chat_model("ollama", model, args.local_timeout, None),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Pricing (config/prices.yaml — same source as orchestrator persistence)
# --------------------------------------------------------------------------- #


def _price_table() -> dict[str, dict[str, float]]:
    return {
        str(k): {kk: float(vv) for kk, vv in v.items()}
        for k, v in load_yaml_config("prices").get("models", {}).items()
    }


def call_cost_usd(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    """USD for one call; 0.0 for local (ollama), priced from prices.yaml for API."""
    if provider == "ollama":
        return 0.0
    spec = _price_table().get(model)
    if not spec:
        return 0.0
    return round(tokens_in / 1e6 * spec["in_per_1m"] + tokens_out / 1e6 * spec["out_per_1m"], 6)


# --------------------------------------------------------------------------- #
# One streamed call
# --------------------------------------------------------------------------- #


@dataclass
class CallResult:
    prompt_id: str
    task_class: str
    ttft_s: float
    total_s: float
    tokens_in: int
    tokens_out: int
    tok_per_s: float
    cost_usd: float
    text: str
    error: str | None = None


async def run_call(cand: Candidate, prompt: BenchPrompt) -> CallResult:
    messages = [("system", prompt.system), ("user", prompt.user)]
    t0 = time.perf_counter()
    ttft: float | None = None
    parts: list[str] = []
    usage: dict[str, int] | None = None
    try:
        # stream_usage=True makes langchain-openai emit a final chunk carrying
        # usage_metadata; the DeepSeek endpoint honours it, ollama may not (we
        # fall back to a token estimate below).
        async for chunk in cand.chat_model.astream(messages, stream_usage=True):
            content = chunk.content if isinstance(chunk.content, str) else ""
            if content and ttft is None:
                ttft = time.perf_counter() - t0
            if content:
                parts.append(content)
            meta = getattr(chunk, "usage_metadata", None)
            if meta:
                usage = {
                    "in": int(meta.get("input_tokens", 0)),
                    "out": int(meta.get("output_tokens", 0)),
                }
    except Exception as exc:  # noqa: BLE001 — one bad call must not sink the run
        total = time.perf_counter() - t0
        return CallResult(
            prompt_id=prompt.id,
            task_class=prompt.task_class,
            ttft_s=ttft or total,
            total_s=total,
            tokens_in=0,
            tokens_out=0,
            tok_per_s=0.0,
            cost_usd=0.0,
            text="",
            error=f"{type(exc).__name__}: {exc}"[:200],
        )

    total = time.perf_counter() - t0
    text = "".join(parts)
    ttft = ttft if ttft is not None else total
    tokens_in = usage["in"] if usage else 0
    # Estimate output tokens when the endpoint gave no usage (≈4 chars/token).
    tokens_out = usage["out"] if usage else max(1, round(len(text) / 4))
    gen_s = max(total - ttft, 1e-6)
    tok_per_s = tokens_out / gen_s
    return CallResult(
        prompt_id=prompt.id,
        task_class=prompt.task_class,
        ttft_s=round(ttft, 4),
        total_s=round(total, 4),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tok_per_s=round(tok_per_s, 2),
        cost_usd=call_cost_usd(cand.provider, cand.model, tokens_in, tokens_out),
        text=text,
        error=None,
    )


# --------------------------------------------------------------------------- #
# Quality judge
# --------------------------------------------------------------------------- #

_JUDGE_SYSTEM = (
    "You are a strict grader. Given a task rubric, the QUESTION and an ANSWER, "
    "score the answer from 1 (fails the rubric) to 5 (fully satisfies it). "
    "Reply with only the integer."
)


async def judge_answers(
    judge: Candidate, results: dict[str, list[CallResult]]
) -> dict[str, dict[str, float]]:
    """Score every answer; returns {label: {task_class: mean_score}}."""
    prompt_by_id = {p.id: p for p in PROMPTS}
    scores: dict[str, dict[str, list[float]]] = {}
    for label, calls in results.items():
        scores[label] = {}
        for call in calls:
            if call.error or not call.text.strip():
                scores[label].setdefault(call.task_class, []).append(1.0)
                continue
            prompt = prompt_by_id[call.prompt_id]
            user = (
                f"RUBRIC: {RUBRIC[call.task_class]}\n\n"
                f"QUESTION:\n{prompt.user}\n\nANSWER:\n{call.text}\n\nScore (1-5):"
            )
            try:
                msg = await judge.chat_model.ainvoke([("system", _JUDGE_SYSTEM), ("user", user)])
                raw = msg.content if isinstance(msg.content, str) else ""
                m = re.search(r"[1-5]", raw)
                score = float(m.group()) if m else 1.0
            except Exception:  # noqa: BLE001 — a judge miss defaults to neutral-low
                score = 1.0
            scores[label].setdefault(call.task_class, []).append(score)
    return {
        label: {tc: round(statistics.mean(v), 2) for tc, v in by_tc.items()}
        for label, by_tc in scores.items()
    }


# --------------------------------------------------------------------------- #
# Aggregation + report
# --------------------------------------------------------------------------- #


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


@dataclass
class ModelSummary:
    label: str
    provider: str
    model: str
    n_calls: int
    n_errors: int
    ttft_p50_s: float
    ttft_p95_s: float
    lat_p50_s: float
    lat_p95_s: float
    tok_per_s_median: float
    total_cost_usd: float
    cost_per_1k_calls_usd: float
    quality_by_class: dict[str, float] = field(default_factory=dict)
    quality_overall: float = 0.0


def summarize(cand: Candidate, calls: list[CallResult], quality: dict[str, float]) -> ModelSummary:
    ok = [c for c in calls if not c.error]
    ttft = [c.ttft_s for c in ok]
    lat = [c.total_s for c in ok]
    toks = [c.tok_per_s for c in ok if c.tok_per_s > 0]
    total_cost = sum(c.cost_usd for c in calls)
    n = len(calls) or 1
    overall = round(statistics.mean(quality.values()), 2) if quality else 0.0
    return ModelSummary(
        label=cand.label,
        provider=cand.provider,
        model=cand.model,
        n_calls=len(calls),
        n_errors=len(calls) - len(ok),
        ttft_p50_s=round(_pct(ttft, 50), 3),
        ttft_p95_s=round(_pct(ttft, 95), 3),
        lat_p50_s=round(_pct(lat, 50), 3),
        lat_p95_s=round(_pct(lat, 95), 3),
        tok_per_s_median=round(statistics.median(toks), 1) if toks else 0.0,
        total_cost_usd=round(total_cost, 6),
        cost_per_1k_calls_usd=round(total_cost / n * 1000, 4),
        quality_by_class=quality,
        quality_overall=overall,
    )


def _charts(summaries: list[ModelSummary]) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    labels = [s.label for s in summaries]
    colors = ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2"][: len(labels)]
    written: list[str] = []

    def bar(filename: str, title: str, ylabel: str, values: list[float], fmt: str) -> None:
        fig, ax = plt.subplots(figsize=(5, 3.2))
        bars = ax.bar(labels, values, color=colors)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.bar_label(bars, fmt=fmt, padding=2)
        ax.tick_params(axis="x", labelrotation=15)
        fig.tight_layout()
        fig.savefig(HERE / filename, dpi=120)
        plt.close(fig)
        written.append(filename)

    bar(
        "ttft_p95.png",
        "TTFT p95 (time to first token)",
        "seconds",
        [s.ttft_p95_s for s in summaries],
        "%.2f",
    )
    bar(
        "tok_per_s.png",
        "Throughput (median tok/s)",
        "tokens/s",
        [s.tok_per_s_median for s in summaries],
        "%.0f",
    )
    bar(
        "cost_per_1k.png",
        "Cost per 1000 calls",
        "USD",
        [s.cost_per_1k_calls_usd for s in summaries],
        "%.2f",
    )
    bar(
        "quality.png",
        "Judge quality (mean, 1-5)",
        "score",
        [s.quality_overall for s in summaries],
        "%.2f",
    )
    return written


def write_report(summaries: list[ModelSummary], *, repeats: int, judged: bool) -> None:
    charts = _charts(summaries)
    lines: list[str] = []
    lines.append("# Inference benchmark — local CPU vs cloud API (T-037 §1)\n")
    lines.append(
        "_Auto-generated by `benchmarks/inference/bench.py` "
        f"({time.strftime('%Y-%m-%d %H:%M:%S')})._\n"
    )
    lines.append("## Setup\n")
    lines.append(
        f"- **Suite:** {len(PROMPTS)} prompts across {len(TASK_CLASSES)} task classes "
        f"({', '.join(TASK_CLASSES)}), {repeats} repeat(s) each — see `prompts.py`."
    )
    lines.append(
        "- **Metrics:** TTFT (first streamed token), tok/s (output tokens / "
        "generation time), full latency (p50/p95), USD cost, judge quality (1–5)."
    )
    lines.append(
        "- **Pricing:** API from `config/prices.yaml` (same table the orchestrator "
        "meters with); local (ollama) is $0 at the meter — see amortisation note below."
    )
    lines.append(
        f"- **Hardware:** {platform.platform()}; {os.cpu_count()} logical CPUs. "
        "_(Replace with the EPYC node's spec when the local candidates are run.)_\n"
    )

    lines.append("## Results\n")
    lines.append(
        "| Model | Provider | Calls | Err | TTFT p50 | TTFT p95 | Lat p50 | Lat p95 "
        "| tok/s (med) | $/1k calls | Quality |"
    )
    lines.append("|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for s in summaries:
        q = f"{s.quality_overall:.2f}" if judged else "—"
        lines.append(
            f"| `{s.model}` | {s.provider} | {s.n_calls} | {s.n_errors} | "
            f"{s.ttft_p50_s:.2f}s | {s.ttft_p95_s:.2f}s | {s.lat_p50_s:.2f}s | "
            f"{s.lat_p95_s:.2f}s | {s.tok_per_s_median:.0f} | "
            f"${s.cost_per_1k_calls_usd:.2f} | {q} |"
        )
    lines.append("")

    if judged:
        lines.append("### Quality by task class (judge score 1–5)\n")
        lines.append("| Model | " + " | ".join(TASK_CLASSES) + " |")
        lines.append("|---|" + "|".join("--:" for _ in TASK_CLASSES) + "|")
        for s in summaries:
            cells = " | ".join(f"{s.quality_by_class.get(tc, 0.0):.2f}" for tc in TASK_CLASSES)
            lines.append(f"| `{s.model}` | {cells} |")
        lines.append("")

    if charts:
        lines.append("## Charts\n")
        for c in charts:
            lines.append(f"![{c}]({c})")
        lines.append("")

    lines.append("## ADR-3 — local model choice\n")
    lines.append(
        "> Fill in once the local candidates run on the home node. The default "
        "`LOCAL_MODEL` in `config/router.yaml` should be the CPU candidate whose "
        "tok/s and TTFT keep the cheap task classes (route/extract/guard) under "
        "the `local` tier's `timeout_s`, at acceptable judge quality. Cloud tiers "
        "handle plan/synthesize regardless (see policy)."
    )
    lines.append("")

    lines.append("## Notes & limitations\n")
    lines.append(
        "- **No GPU / vLLM column (Q-07).** GPU inference was excluded by owner "
        "decision; we do not publish anyone else's GPU throughput as our own. "
        "The local column is CPU-only (2×EPYC 7551), API column is the paid tier."
    )
    lines.append(
        "- **Local $ is an estimate, not a meter.** Ollama calls cost $0 in the "
        "table (no API charge). Real local cost is electricity + hardware "
        "amortisation on an already-owned node — treat the API $/1k as the "
        "marginal-cost comparison, not a claim that local is free."
    )
    lines.append(
        "- **TTFT/tok/s are streaming measurements.** tok/s = output tokens ÷ "
        "(latency − TTFT); when an endpoint returns no token usage, output "
        "tokens are estimated at ≈4 chars/token (flagged; DeepSeek returns real "
        "usage, some ollama builds do not)."
    )
    lines.append(
        "- Single-thread, warm client; no concurrency modelling. Outputs are "
        "length-bounded by each prompt's system message so tok/s compares "
        "generation speed, not verbosity."
    )
    lines.append("")

    (HERE / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


async def _run(args: argparse.Namespace) -> None:
    candidates = resolve_candidates(args)
    if not candidates:
        raise SystemExit(
            "[bench] no candidates — set DEEPSEEK_API_KEY for the API run, or pass "
            "--local-models on a node with OLLAMA_BASE_URL."
        )
    print(f"[bench] candidates: {', '.join(c.label for c in candidates)}")

    results: dict[str, list[CallResult]] = {}
    raw_calls: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        calls: list[CallResult] = []
        for _ in range(args.repeats):
            for prompt in PROMPTS:
                res = await run_call(cand, prompt)
                calls.append(res)
                flag = "!" if res.error else " "
                print(
                    f"[{cand.label:>16}] {flag}{prompt.id:<11} "
                    f"ttft={res.ttft_s:.2f}s lat={res.total_s:.2f}s "
                    f"out={res.tokens_out:>4} {res.tok_per_s:>6.1f}tok/s ${res.cost_usd:.5f}"
                )
        results[cand.label] = calls
        raw_calls[cand.label] = [asdict(c) for c in calls]

    quality: dict[str, dict[str, float]] = {}
    if not args.no_judge:
        judge = next((c for c in candidates if c.label == "cloud_strong"), None)
        if judge is None and get_settings().deepseek_api_key:
            spec = load_yaml_config("router")["tiers"]["cloud_strong"]
            judge = Candidate(
                "judge",
                "deepseek",
                str(spec["model"]),
                float(spec.get("timeout_s", 120)),
                spec.get("thinking"),
                _chat_model("deepseek", str(spec["model"]), 120, spec.get("thinking")),
            )
        if judge is None:
            print("[bench] no judge model available (needs DEEPSEEK_API_KEY) — skipping quality")
        else:
            n_ans = sum(len(v) for v in results.values())
            print(f"[bench] judging {n_ans} answers via {judge.model} ...")
            quality = await judge_answers(judge, results)

    summaries = [summarize(c, results[c.label], quality.get(c.label, {})) for c in candidates]
    write_report(summaries, repeats=args.repeats, judged=bool(quality))

    (HERE / "results.json").write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "hardware": platform.platform(),
                "summaries": [asdict(s) for s in summaries],
                "calls": raw_calls,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[bench] wrote {HERE / 'REPORT.md'} and results.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inference benchmark: local CPU vs cloud API (T-037)"
    )
    parser.add_argument(
        "--repeats", type=int, default=1, help="repeats of the 20-prompt suite per model"
    )
    parser.add_argument(
        "--local-models",
        type=lambda s: s.split(","),
        default=[],
        help="comma-separated ollama model tags to benchmark (needs OLLAMA_BASE_URL)",
    )
    parser.add_argument("--local-only", action="store_true", help="skip the cloud API tiers")
    parser.add_argument(
        "--local-timeout", type=float, default=120.0, help="per-call timeout for local models"
    )
    parser.add_argument(
        "--no-judge", action="store_true", help="skip the quality judge (saves API cost)"
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
