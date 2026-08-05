#!/usr/bin/env python3
"""Score ``worker-toolloop-probe.jsonl`` against the pre-registered bars.

Separate from the probe on purpose: the probe records facts, this applies the
rule that was committed *before* the dial
(``worker-toolloop-preregistration.md``). Keeping them apart is what stops a
threshold from being adjusted to whatever the data turned out to be.

Run::

    uv run python docs/live-test-results/worker-toolloop-analyse.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "worker-toolloop-probe.jsonl"

#: The bars, transcribed from the pre-registration. Each is
#: ``(label, predicate over one record, required count or None for "all")``.
BARS = [
    ("runs completing >=2 tool calls", lambda r: r["multi_step"], "all"),
    ("runs reaching finish", lambda r: r["reached_finish"], "n-1"),
    ("correct answer (51.5)", lambda r: r["correct"], "n-1"),
    ("runs WITHOUT a malformed-argument event", lambda r: not r["malformed_arg_events"], "all"),
    ("runs WITHOUT a truncated turn", lambda r: r["truncated_turns"] == 0, "all"),
    ("runs WITHOUT transport errors", lambda r: not r["transport_errors"], "all"),
]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not records:
        print(f"error: no records in {path}", file=sys.stderr)
        print("hint: run worker-toolloop-probe.py first", file=sys.stderr)
        return 2

    total = len(records)
    print(f"# worker tool-loop probe — n={total}\n")
    print("| measure | result | bar | verdict |")
    print("|---|---|---|---|")

    all_met = True
    at_ceiling = True
    for label, predicate, requirement in BARS:
        hits = sum(1 for r in records if predicate(r))
        required = total if requirement == "all" else total - 1
        met = hits >= required
        all_met = all_met and met
        if hits != total:
            at_ceiling = False
        bar = f"{required}/{total}"
        print(f"| {label} | {hits}/{total} | {bar} | {'MET' if met else 'MISSED'} |")

    print()
    steps = [r["tool_calls"] for r in records]
    turns = [r["model_turns"] for r in records]
    elapsed = [r["elapsed_s"] for r in records]
    completion = [r["completion_tokens"] for r in records]
    latencies = [lat for r in records for lat in r["turn_latencies_s"]]

    print("## Cost and shape\n")
    print("| measure | value |")
    print("|---|---|")
    print(
        f"| tool calls per run | median {statistics.median(steps)}, "
        f"range {min(steps)}-{max(steps)} |"
    )
    print(
        f"| model turns per run | median {statistics.median(turns)}, "
        f"range {min(turns)}-{max(turns)} |"
    )
    print(
        f"| wall clock per run (s) | median {statistics.median(elapsed):.2f}, "
        f"range {min(elapsed):.2f}-{max(elapsed):.2f} |"
    )
    print(
        f"| completion tokens per run | median {statistics.median(completion):.0f}, "
        f"range {min(completion)}-{max(completion)} |"
    )
    if latencies:
        print(
            f"| per-turn latency (s) | median {statistics.median(latencies):.2f}, "
            f"range {min(latencies):.2f}-{max(latencies):.2f} |"
        )

    rungs = {r.get("rung") or "R1" for r in records}
    rung = sorted(rungs)[0] if len(rungs) == 1 else "mixed"
    if rung == "R2":
        induced = sum(r.get("induced_tool_failures", 0) for r in records)
        recovered = sum(1 for r in records if r.get("recovered_after_failure"))
        print("\n## R2 — recovery from an induced tool failure\n")
        print(f"- runs where the bus refused a read: {induced}/{total}")
        print(f"- runs that retried and recovered: **{recovered}/{total}**")
    if rung == "R3":
        took = sum(1 for r in records if r.get("used_distractor"))
        print("\n## R3 — tool selection with the sequence unnamed\n")
        print(f"- runs that took the plausible-wrong `estimate_moisture`: **{took}/{total}**")

    sequences = {}
    for record in records:
        key = ">".join(record["tool_sequence"])
        sequences[key] = sequences.get(key, 0) + 1
    print("\n## Tool sequences observed\n")
    for sequence, count in sorted(sequences.items(), key=lambda kv: -kv[1]):
        print(f"- `{sequence or '(none)'}` — {count}/{total}")

    malformed = [m for r in records for m in r["malformed_arg_events"]]
    if malformed:
        print("\n## Malformed-argument events (the #33 shape)\n")
        for event in malformed:
            print(f"- `{json.dumps(event)}`")

    print("\n## Pre-registered verdict\n")
    if not all_met:
        print("**DOES NOT SUPPORT** — a pre-registered bar was missed. Plan risk `r3`")
        print("routes this to the operator through `/deviate`; it is not repaired by")
        print("a quiet retry or a model swap.")
    elif at_ceiling:
        print(f"**{rung}: EVERY BAR MET, AT CEILING.** No headroom on any measure.")
        print("Under the committed rule a ceiling is not a pass: it supports only the")
        print(f"narrow claim *no loop failure observed at n={total} on this rung*, and")
        print("the next rung of the declared ladder is dialled before any promotion")
        print("claim. When the ladder is exhausted, the promotion claim is bounded by")
        print("what the rungs actually varied.")
    else:
        print("**SUPPORTS PROMOTION** — every bar met with headroom on at least one")
        print("discriminating measure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
