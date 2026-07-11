"""CI-only baseline diff for the eval gate (T-030).

`eval.run`'s own regression section compares against the previous row in
the (self-hosted, persistent) `eval_runs` table — in CI the database is
fresh and empty every run, so there is no "previous row". Instead, the
`eval` workflow downloads the last successful main-branch run's
`report.json` as a baseline artifact and this script diffs against that.

A category whose pass_rate drops more than REGRESSION_PP percentage points
is a regression: exit 1 and print a markdown table for the PR comment. No
baseline (first run ever, or artifact not found) is not a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REGRESSION_PP = 0.05


def compare(current_path: Path, baseline_path: Path | None) -> tuple[str, bool]:
    current = json.loads(current_path.read_text(encoding="utf-8"))
    if baseline_path is None or not baseline_path.exists():
        return "No baseline available (first run) — nothing to compare.", False

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    cur_categories = current.get("categories", {})
    base_categories = baseline.get("categories", {})

    lines = ["| category | baseline | current | Δ | |", "|---|---|---|---|---|"]
    regressed = False
    for category in sorted(set(cur_categories) | set(base_categories)):
        cur_rate = cur_categories.get(category, {}).get("pass_rate")
        base_rate = base_categories.get(category, {}).get("pass_rate")
        if cur_rate is None or base_rate is None:
            lines.append(f"| {category} | {base_rate} | {cur_rate} | n/a | new/removed |")
            continue
        delta = cur_rate - base_rate
        flag = ""
        if delta < -REGRESSION_PP:
            flag = "REGRESSION"
            regressed = True
        lines.append(f"| {category} | {base_rate:.2f} | {cur_rate:.2f} | {delta:+.2f} | {flag} |")
    return "\n".join(lines), regressed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/eval_baseline_compare.py")
    parser.add_argument(
        "--current", required=True, type=Path, help="path to this run's report.json"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="path to the baseline report.json, if downloaded",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the markdown table here")
    args = parser.parse_args(argv)

    table, regressed = compare(args.current, args.baseline)
    print(table)
    if args.out:
        args.out.write_text(table, encoding="utf-8")
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
