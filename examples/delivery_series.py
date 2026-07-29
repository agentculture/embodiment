"""Part B of live measurement series A: per-kind delivery, n runs (plan task t18).

Re-measures the 2-of-7 delivery baseline in ``docs/live-test-results/proof.md``
**per counsel kind**, using the harness that produced the baseline
(``examples/proof.py``) so the comparison is against the same instrument rather
than a new one.

It runs ``proof.py`` as a subprocess rather than importing it: ``proof.main``
parses ``sys.argv`` and prints, and one process per run is also what produced
the baseline — a four-run series inside one process would share a muse runner
and a thread, which the baseline did not.

Every run's whole JSON report is appended to ``--out``, so the raw record is
committed and the fold can be recomputed from it.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/delivery_series.py --n 4 \\
        --out docs/live-test-results/delivery-per-kind.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - fixed argv, no shell, no user input
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
PROOF = REPO_ROOT / "examples" / "proof.py"

#: The counters the baseline published, so the fold reports the same fields.
BASELINE_COUNTS = {
    "insights_delivered": 2,
    "insights_dropped_stale": 2,
    "insights_dropped_late": 3,
    "insights_dropped_overflow": 0,
}


def run_once(*, identity: Optional[str], max_steps: int, results: Path) -> dict[str, Any]:
    """One ``proof.py --muse`` run, returned as its parsed JSON report."""
    argv = [
        sys.executable,
        str(PROOF),
        "--muse",
        "--json",
        "--max-steps",
        str(max_steps),
        "--results",
        str(results),
    ]
    if identity:
        argv += ["--identity", identity]
    started = time.time()
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False
        argv,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = round(time.time() - started, 1)
    if completed.returncode != 0:
        return {
            "harness_error": completed.stderr[-800:],
            "returncode": completed.returncode,
            "elapsed_seconds": elapsed,
        }
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "harness_error": f"unparseable report: {exc}",
            "stdout_tail": completed.stdout[-800:],
            "elapsed_seconds": elapsed,
        }
    report["elapsed_seconds"] = elapsed
    return report


def fold(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum the delivery accounting across runs, keeping absence as absence.

    A code with no producer reports nothing rather than zero — the same rule
    ``proof._fold_codes`` follows, for the same reason: a zero would make "never
    implemented" read as "did not happen".
    """
    counts: dict[str, int] = {}
    delivered: dict[str, int] = {}
    dropped: dict[str, int] = {}
    codes: dict[str, int] = {}
    errors = 0
    for report in reports:
        if report.get("harness_error"):
            errors += 1
            continue
        for key, value in (report.get("muse_counts") or {}).items():
            counts[key] = counts.get(key, 0) + int(value)
        for key, value in (report.get("muse_kind_delivered") or {}).items():
            delivered[key] = delivered.get(key, 0) + int(value)
        for key, value in (report.get("muse_kind_dropped") or {}).items():
            dropped[key] = dropped.get(key, 0) + int(value)
        for key, value in (report.get("muse_degradation_codes") or {}).items():
            codes[key] = codes.get(key, 0) + int(value)
    produced = (
        counts.get("insights_delivered", 0)
        + counts.get("insights_dropped_stale", 0)
        + counts.get("insights_dropped_late", 0)
        + counts.get("insights_dropped_overflow", 0)
    )
    return {
        "runs": len(reports),
        "harness_errors": errors,
        "counts": counts,
        "insights_produced": produced,
        "delivery_fraction": (
            round(counts.get("insights_delivered", 0) / produced, 4) if produced else None
        ),
        # Only ``drain`` attributes a kind, so these cover deliveries and STALE
        # drops. Late and overflow drops are unattributable by construction and
        # are reported as totals, never assigned to a kind.
        "kind_delivered": delivered,
        "kind_dropped_stale": dropped,
        "unattributable_drops": {
            "late": counts.get("insights_dropped_late", 0),
            "overflow": counts.get("insights_dropped_overflow", 0),
        },
        "degradation_codes": codes,
        "baseline": BASELINE_COUNTS,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--n", type=int, default=4, help="number of proof.py runs")
    parser.add_argument("--identity", default="Gwen", help="matches the baseline run")
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--out", default="results/delivery_series.jsonl")
    parser.add_argument("--results", default="results/proof_config.json")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not os.environ.get("COLLEAGUE_API_KEY", "").strip():
        print("error: COLLEAGUE_API_KEY is not set", file=sys.stderr)
        print("hint: export it before running the series", file=sys.stderr)
        return 2

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results_path = Path(args.results).expanduser()
    results_path.parent.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    with out_path.open("a", encoding="utf-8") as handle:
        for index in range(max(1, args.n)):
            report = run_once(
                identity=args.identity, max_steps=args.max_steps, results=results_path
            )
            report["run_index"] = index
            reports.append(report)
            handle.write(json.dumps(report, default=str) + "\n")
            handle.flush()

    print(json.dumps({"raw": str(out_path), "fold": fold(reports)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
