#!/usr/bin/env python3
"""league-commander-reexam — did the 900 s clock censor task ``t28``'s records?

Plan task **t4**, seeded by [issue #42]'s timeout audit. ``examples/league_commander.py``
ships ``REQUEST_TIMEOUT = 900.0`` against a budget-derived bound of
``MAX_TOKENS / slowest measured cortex rate = 16000 / 21.5 = 744 s`` — a **1.21x**
margin, the narrowest passing one in that audit. That harness produced the
**2.4-4.4x hierarchy-cost figure** this repo cites, so if any of its turns were cut by
the clock the way ``worker_seam.py``'s cell ``C1-E`` was, its cost numbers are inflated
and its correctness numbers understated.

The precedent being checked against: a clock-cut turn is re-run whole and, after retries
are exhausted, discarded — while ``truncated: false`` is recorded throughout. The tells
are ``retries > 0`` per call, and wall clocks matching the retry arithmetic.

**Nothing here dials anything.** It reads only committed records and recomputes every
number it prints, including the published ones. Run it::

    uv run python docs/live-test-results/league-commander-reexam.py

Exit code is ``0`` when the records are clean and ``1`` when any censoring tell fires,
so this is a check and not merely a report. Advisories about the *margin* never change
the exit code: they are about what the constant would survive, not about what happened.

The verdict is written up in [corrections.md](corrections.md).
"""

from __future__ import annotations

import collections
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = REPO_ROOT / "docs" / "live-test-results"
sys.path.insert(0, str(REPO_ROOT))

from examples import league_commander as lc  # noqa: E402

# ── the measured rate this audit's bound derives from ────────────────────────
# Source: corrections.md, "The cortex was never contended — the timeout was eating its
# own turns". Corrected for retry overhead, the cortex's generation rate across ten
# calls spans 21.5-25.4 tok/s. The SLOWEST end is what a bound is derived from: a
# timeout sized at the fast end censors exactly the tail it was meant to admit.
CORTEX_SLOWEST_TOK_S = 21.5
CORTEX_FASTEST_TOK_S = 25.4

#: If plan task ``t1``'s dated rate config has landed, it outranks the literals above.
RATE_CONFIG = RESULTS / "measured-rates.json"

# ── what league-commander.md published, quoted so the recompute can contradict it ────
# Sections "The bill" and "The instrument".
PUBLISHED_TOKENS_PER_MATCH = {"B": 25459, "C": 26253, "A-qwen": 10703, "A-gemma": 5827}
PUBLISHED_RATIO_VS_QWEN = 2.4
PUBLISHED_RATIO_VS_GEMMA = 4.4
PUBLISHED_CALLS = 120
PUBLISHED_LENGTH_FINISHES = 0
PUBLISHED_RETRIES = 0
PUBLISHED_CONTENTION_CALLS = 0

RUNS = (
    ("primary (c-skirmish-1, n=3/arm)", "league-commander", "league-commander-logs"),
    ("escalation E1 (c-frontier-1)", "league-commander-frontier", "league-commander-frontier-logs"),
)


@dataclass
class Run:
    """One committed series: its per-match ledger, its per-call transcripts, its logs."""

    label: str
    matches: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    log_keys: set[str]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"error: missing committed record {path}\nhint: run from a full checkout")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_run(label: str, stem: str, logs: str) -> Run:
    matches = [r for r in _read_jsonl(RESULTS / f"{stem}.jsonl") if r.get("kind") == "match"]
    calls = [r["call"] for r in _read_jsonl(RESULTS / f"{stem}-transcripts.jsonl") if "call" in r]
    log_dir = RESULTS / logs
    log_keys = {p.stem for p in log_dir.glob("*.jsonl")} if log_dir.is_dir() else set()
    return Run(label=label, matches=matches, calls=calls, log_keys=log_keys)


def measured_rates() -> tuple[float, float, str]:
    """The rate band, preferring ``t1``'s committed config over this module's literals."""
    if RATE_CONFIG.exists():
        payload = json.loads(RATE_CONFIG.read_text(encoding="utf-8"))
        cortex = (payload.get("rates") or {}).get("cortex") or {}
        slowest = cortex.get("slowest_tok_s")
        fastest = cortex.get("fastest_tok_s")
        if slowest and fastest:
            return float(slowest), float(fastest), f"{RATE_CONFIG.name} ({payload.get('measured')})"
    return CORTEX_SLOWEST_TOK_S, CORTEX_FASTEST_TOK_S, "corrections.md (no measured-rates.json)"


def retry_rungs() -> list[tuple[str, float]]:
    """Wall clocks a clock-cut call cannot avoid landing at or beyond.

    ``gateway_seam`` starts its stopwatch **before** the first attempt and never resets
    it, so a call that timed out ``k`` times before succeeding records at least
    ``k * (timeout + wait)``. A call that exhausts every attempt records exactly
    ``(retries + 1) * timeout + retries * wait`` — the identity that matched
    ``worker_seam``'s two lost calls to 0.4 s.
    """
    step = lc.REQUEST_TIMEOUT + lc.RETRY_WAIT_SECONDS
    rungs = [
        (f"{k} timeout(s) then success (floor)", k * step) for k in range(1, lc.MAX_RETRIES + 1)
    ]
    exhausted = (lc.MAX_RETRIES + 1) * lc.REQUEST_TIMEOUT + lc.MAX_RETRIES * lc.RETRY_WAIT_SECONDS
    rungs.append(("all attempts exhausted (exact)", exhausted))
    return rungs


def _linear_fit(xs: Iterable[float], ys: Iterable[float]) -> tuple[float, float, float]:
    """Least-squares ``seconds = intercept + slope * tokens``; returns slope, intercept, r2."""
    xs, ys = list(xs), list(ys)
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    total = sum((y - mean_y) ** 2 for y in ys)
    return slope, intercept, (1 - residual / total) if total else 0.0


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def check_reconciliation(runs: list[Run]) -> list[str]:
    """A crashed match leaves per-call transcript lines and NO ledger row.

    ``run_series`` wraps ``play_match`` in no ``try``: an exhausted-retry call raises
    straight through it, so ``append_line(out, record)`` never runs — while every call
    the match already paid for is already on disk in the transcript file. Orphaned
    transcript keys are therefore the signature of a discarded match, and a per-key
    count mismatch is the signature of one replayed after a crash.
    """
    rule("1. Ledger / transcript / arena-log reconciliation")
    findings: list[str] = []
    for run in runs:
        ledger = collections.Counter()
        for match in run.matches:
            ledger[match["match_key"]] += sum((match.get("calls_by_level") or {}).values())
        transcript = collections.Counter(call["match_key"] for call in run.calls)
        orphans = sorted(set(transcript) - set(ledger))
        mismatched = sorted(k for k in set(ledger) | set(transcript) if ledger[k] != transcript[k])
        missing_logs = sorted(set(ledger) - run.log_keys)
        extra_logs = sorted(run.log_keys - set(ledger))
        print(f"\n{run.label}")
        print(f"  matches {len(run.matches)}  ledger calls {sum(ledger.values())}", end="")
        print(f"  transcript calls {sum(transcript.values())}  arena logs {len(run.log_keys)}")
        print(f"  orphan transcript keys (discarded match): {orphans or 'none'}")
        print(f"  per-key count mismatches (replayed match): {mismatched or 'none'}")
        print(f"  arena logs missing / extra: {missing_logs or 'none'} / {extra_logs or 'none'}")
        if orphans:
            findings.append(
                f"{run.label}: {len(orphans)} orphan transcript key(s) — a match was discarded"
            )
        if mismatched:
            findings.append(
                f"{run.label}: {len(mismatched)} match(es) counted differently in "
                "ledger vs transcripts"
            )
    return findings


def check_retry_tells(runs: list[Run]) -> list[str]:
    """``retries > 0`` is the first tell, and the ledger is authoritative for it.

    A transcript line is written only on the success path, so an exhausted call appears
    in the ledger and never in the transcripts. Both are checked; the ledger's
    ``retries`` / ``errors`` / ``contention_calls`` are the sums that cannot be dodged.
    """
    rule("2. The retry tells — retries, errors, contention")
    findings: list[str] = []
    for run in runs:
        led_retries = sum(int(m.get("retries") or 0) for m in run.matches)
        led_contention = sum(int(m.get("contention_calls") or 0) for m in run.matches)
        led_errors = sum(len(m.get("errors") or []) for m in run.matches)
        call_retries = [c for c in run.calls if int(c.get("retries") or 0) > 0]
        call_errors = [c for c in run.calls if c.get("error")]
        call_contention = [c for c in run.calls if c.get("contention")]
        print(f"\n{run.label}")
        print(f"  ledger:      retries {led_retries}   contention calls {led_contention}", end="")
        print(f"   match errors {led_errors}")
        print(
            f"  per call:    retries>0 {len(call_retries)}   error set {len(call_errors)}", end=""
        )
        print(f"   contention {len(call_contention)}")
        if led_retries or call_retries:
            findings.append(
                f"{run.label}: {led_retries or len(call_retries)} retry/retries recorded"
            )
        if led_errors or call_errors:
            findings.append(f"{run.label}: transport error(s) recorded on a call")
    return findings


def check_retry_arithmetic(runs: list[Run], tolerance: float = 5.0) -> list[str]:
    """No committed wall clock may reach the first rung — nor the timeout itself."""
    rule("3. Wall clocks against the retry arithmetic")
    rungs = retry_rungs()
    print(f"\nharness constants: REQUEST_TIMEOUT={lc.REQUEST_TIMEOUT}s", end="")
    print(f"  MAX_RETRIES={lc.MAX_RETRIES}  RETRY_WAIT_SECONDS={lc.RETRY_WAIT_SECONDS}s")
    print("  (note: this harness waits 30 s between attempts, not worker_seam's 20 s)")
    for name, value in rungs:
        print(f"    {name:<38} {value:>8.1f} s")
    findings: list[str] = []
    for run in runs:
        seconds = [c["seconds"] for c in run.calls]
        walls = [m.get("wall_seconds") or 0.0 for m in run.matches]
        over = [c for c in run.calls if c["seconds"] >= lc.REQUEST_TIMEOUT]
        near = [
            (c, name)
            for c in run.calls
            for name, value in rungs
            if abs(c["seconds"] - value) <= tolerance
        ]
        worst = max(seconds)
        print(f"\n{run.label}")
        print(
            f"  slowest single CALL      {worst:>8.1f} s"
            f"   ({worst / lc.REQUEST_TIMEOUT:.1%} of timeout)"
        )
        print(f"  slowest whole MATCH      {max(walls):>8.1f} s   (many calls plus arena CLI time)")
        print(f"  calls at or over the timeout: {len(over)}")
        print(f"  calls within {tolerance:.0f}s of a retry rung: {len(near)}")
        if over:
            findings.append(f"{run.label}: {len(over)} call(s) at or over REQUEST_TIMEOUT")
        if near:
            findings.append(f"{run.label}: {len(near)} call(s) matching the retry arithmetic")
    return findings


def check_finish_reasons(runs: list[Run]) -> list[str]:
    """``length`` is truncation; ``none`` in the ledger is a call that never returned."""
    rule("4. finish_reason distribution")
    findings: list[str] = []
    for run in runs:
        ledger = collections.Counter()
        for match in run.matches:
            ledger.update(match.get("finish_reasons") or {})
        per_call = collections.Counter(c.get("finish_reason") or "none" for c in run.calls)
        print(f"\n{run.label}")
        print(f"  ledger:   {dict(sorted(ledger.items()))}")
        print(f"  per call: {dict(sorted(per_call.items()))}")
        if ledger.get("length") or per_call.get("length"):
            findings.append(
                f"{run.label}: {ledger.get('length', 0)} `length` finish(es) — truncation"
            )
        if ledger.get("none") or per_call.get("none"):
            findings.append(f"{run.label}: {ledger.get('none', 0)} call(s) with no finish_reason")
    return findings


def check_budget(runs: list[Run], slowest: float) -> None:
    """How close the token budget — the thing the clock is supposed to let bind — came."""
    rule("5. Token budget headroom")
    for run in runs:
        worst = max(run.calls, key=lambda c: c["completion_tokens"])
        tokens = worst["completion_tokens"]
        print(f"\n{run.label}")
        print(f"  largest completion   {tokens:>6d} tok of {lc.MAX_TOKENS} budget", end="")
        print(f"   ({tokens / lc.MAX_TOKENS:.1%})")
        print(f"    {worst['arm']} {worst['match_key']} {worst['level']} at {worst['seconds']}s")
        print(f"  that many tokens at {slowest} tok/s would need {tokens / slowest:.1f} s")


def check_rates(runs: list[Run], slowest: float) -> list[str]:
    """Every model dialled through this one timeout, and the bound each implies.

    Two estimates are printed because neither alone is honest. The **fastest implied
    rate** (``completion_tokens / seconds``) is a *lower* bound on true generation rate:
    queue wait and prompt processing can only push it down. The **regression slope**
    separates fixed per-call cost from per-token cost, but is only trustworthy where the
    token range is wide — its r2 is printed so a narrow fit can be discounted.
    """
    rule("6. Per-model rate, non-generation overhead, and the bound each implies")
    calls = [c for run in runs for c in run.calls]
    by_model: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for call in calls:
        by_model[call["model"]].append(call)

    advisories: list[str] = []
    worst_overhead = 0.0
    for model, group in sorted(by_model.items()):
        tokens = [c["completion_tokens"] for c in group]
        seconds = [c["seconds"] for c in group]
        implied = [t / s for t, s in zip(tokens, seconds) if s > 0]
        fastest_implied = max(implied)
        slope, intercept, r_squared = _linear_fit(tokens, seconds)
        overheads = sorted(
            ((s - t / fastest_implied, c) for t, s, c in zip(tokens, seconds, group)),
            key=lambda pair: pair[0],
            reverse=True,
        )
        print(f"\n{model}")
        print(f"  n={len(group)}  completion tokens {min(tokens)}..{max(tokens)}")
        print(f"  fastest implied rate      {fastest_implied:>6.2f} tok/s", end="")
        print(f"   (median {statistics.median(implied):.2f})")
        print(f"  regression                {1 / slope:>6.2f} tok/s", end="")
        print(f"   fixed cost {intercept:.2f} s   r2 {r_squared:.3f}")
        bound = lc.MAX_TOKENS / fastest_implied
        print(f"  bound at the fastest implied rate: {lc.MAX_TOKENS}/{fastest_implied:.2f}", end="")
        print(f" = {bound:.0f} s  -> {lc.REQUEST_TIMEOUT}s is {lc.REQUEST_TIMEOUT / bound:.2f}x")
        print("  largest non-generation overhead (wall minus tokens/fastest rate):")
        for value, call in overheads[:3]:
            print(
                f"    {value:>7.1f} s   {call['arm']} {call['match_key']} {call['level']}", end=""
            )
            print(f"  seconds={call['seconds']} completion={call['completion_tokens']}")
        worst_overhead = max(worst_overhead, overheads[0][0])
        if lc.REQUEST_TIMEOUT < bound:
            advisories.append(
                f"{model}: at its own measured {fastest_implied:.1f} tok/s a full "
                f"{lc.MAX_TOKENS}-token completion needs {bound:.0f} s — "
                f"REQUEST_TIMEOUT={lc.REQUEST_TIMEOUT:.0f}s is BELOW bound for this model"
            )

    rule("7. What the 1.21x margin would actually survive")
    bound = lc.MAX_TOKENS / slowest
    slack = lc.REQUEST_TIMEOUT - bound
    print(f"\n  budget-derived bound      {lc.MAX_TOKENS}/{slowest} = {bound:.1f} s")
    print(f"  shipped timeout           {lc.REQUEST_TIMEOUT:.1f} s", end="")
    print(f"   -> margin {lc.REQUEST_TIMEOUT / bound:.2f}x")
    print(f"  slack for everything else {slack:.1f} s")
    print(f"  worst overhead measured   {worst_overhead:.1f} s   in THIS series")
    total = bound + worst_overhead
    print(f"  bound + worst overhead    {total:.1f} s", end="")
    print(
        f"   vs timeout {lc.REQUEST_TIMEOUT:.1f} s"
        f" -> {'CUT' if total > lc.REQUEST_TIMEOUT else 'fits'}"
    )
    if total > lc.REQUEST_TIMEOUT:
        advisories.append(
            f"the {lc.REQUEST_TIMEOUT / bound:.2f}x margin is generation-only: this series' worst "
            f"measured non-generation overhead ({worst_overhead:.1f} s) exceeds its "
            f"{slack:.1f} s of slack"
        )
    return advisories


def recompute_published(run: Run) -> list[str]:
    """Recompute the published bill from the raw ledger rather than trusting the table."""
    rule("8. Recomputing the published 2.4-4.4x from the committed ledger")
    per_arm: dict[str, dict[str, float]] = collections.defaultdict(
        lambda: {"matches": 0, "prompt": 0, "completion": 0}
    )
    for match in run.matches:
        bucket = per_arm[match["arm"]]
        bucket["matches"] += 1
        for level in (match.get("tokens_by_level") or {}).values():
            bucket["prompt"] += level["prompt"]
            bucket["completion"] += level["completion"]

    findings: list[str] = []
    print(
        f"\n  {'arm':<9} {'n':>2} {'prompt':>8} {'completion':>11}"
        f" {'tok/match':>10} {'published':>10}"
    )
    per_match: dict[str, float] = {}
    for arm in ("B", "C", "A-qwen", "A-gemma"):
        bucket = per_arm[arm]
        value = (bucket["prompt"] + bucket["completion"]) / bucket["matches"]
        per_match[arm] = value
        published = PUBLISHED_TOKENS_PER_MATCH[arm]
        flag = "" if abs(value - published) < 1.0 else "   <-- DIFFERS"
        print(f"  {arm:<9} {int(bucket['matches']):>2} {int(bucket['prompt']):>8}", end="")
        print(f" {int(bucket['completion']):>11} {value:>10.1f} {published:>10}{flag}")
        if flag:
            findings.append(
                f"{arm}: recomputed {value:.1f} tok/match against published {published}"
            )

    for base, published in (
        ("A-qwen", PUBLISHED_RATIO_VS_QWEN),
        ("A-gemma", PUBLISHED_RATIO_VS_GEMMA),
    ):
        ratio = per_match["B"] / per_match[base]
        agrees = abs(round(ratio, 1) - published) < 0.05
        print(f"\n  B / {base:<8} = {ratio:.3f}  -> published {published}x", end="")
        print(f"   {'agrees' if agrees else 'DIFFERS'}")
        if not agrees:
            findings.append(f"B/{base} recomputes to {ratio:.3f}, published {published}")

    calls = sum(sum((m.get("calls_by_level") or {}).values()) for m in run.matches)
    print(f"\n  calls {calls} (published {PUBLISHED_CALLS})", end="")
    print(f"   length finishes {PUBLISHED_LENGTH_FINISHES}", end="")
    print(f"   retries {PUBLISHED_RETRIES}   contention {PUBLISHED_CONTENTION_CALLS}")
    if calls != PUBLISHED_CALLS:
        findings.append(f"primary run has {calls} calls, published {PUBLISHED_CALLS}")
    return findings


def main(argv: Optional[list[str]] = None) -> int:
    del argv
    slowest, fastest, source = measured_rates()
    print(__doc__.splitlines()[0])
    print(f"\nrate band {slowest}-{fastest} tok/s, from {source}")
    print(f"records under {RESULTS}")

    runs = [load_run(*spec) for spec in RUNS]

    censoring: list[str] = []
    censoring += check_reconciliation(runs)
    censoring += check_retry_tells(runs)
    censoring += check_retry_arithmetic(runs)
    censoring += check_finish_reasons(runs)
    check_budget(runs, slowest)
    advisories = check_rates(runs, slowest)
    censoring += recompute_published(runs[0])

    rule("VERDICT")
    if censoring:
        print("\n  DIRTY — the clock censored these records:")
        for item in censoring:
            print(f"    - {item}")
        print("\n  The 2.4-4.4x figures need the C1-E treatment: raise the constant,")
        print("  re-run the affected cells whole, publish the lost repetitions as evidence.")
    else:
        print("\n  CLEAN — no committed call was cut by the 900 s clock.")
        print("  Zero retries, zero transport errors, zero `length` finishes, zero calls")
        print("  at or over the timeout, no orphaned transcript keys, and the published")
        print("  2.4-4.4x figures recompute exactly from the raw ledger.")
    if advisories:
        print("\n  ADVISORY (about the constant, not about these records):")
        for item in advisories:
            print(f"    - {item}")
    return 1 if censoring else 0


if __name__ == "__main__":
    raise SystemExit(main())
