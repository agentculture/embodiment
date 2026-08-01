#!/usr/bin/env python3
"""thor-idle-guard — is Thor free to measure on, right now?

Plan task **t8** of `error-derived-timeouts-bee-hive-architecture`, acceptance
criterion 3: *"runs only post-series or in a declared idle window, never
contending with a live cell."* This script is what that criterion is checked
with, and what its check is committed as — a named, re-runnable script rather
than an inline one-liner nobody can re-derive (repo convention).

What contends
-------------
A measured series is climbing in a sibling worktree
(`../.worktrees.embodiment/t12`, branch `owa/t12`). Its ladder dials cells
serially, one arm at a time:

* arm ``E`` — cortex-alone. Runs on **spark**. Never touches Thor.
* arms ``W`` / ``M`` / ``H`` — dial the **Thor** worker. Contention.

So the question "may I dial Thor" reduces to "is any Thor-dialling cell in
flight", and this script answers it from three independent angles rather than
one, because each is individually blind:

1. **Process table** — the authoritative live signal. A running
   ``run-cell.sh <rung> <arm>`` / ``drive.py --arm <arm>`` names its arm on the
   command line. This sees a cell that is running *now* but has written
   nothing yet.
2. **Ladder decisions** (`ladder-decisions.jsonl`) — the committed record of
   which rungs resolved and which cells the ladder declared ``absent`` without
   dialling. This sees history, but lags a cell in flight.
3. **Thor reachability** (`/v1/models`) — proves the endpoint answers and
   serves the model this probe means to dial, so an "idle" verdict cannot be
   an endpoint that is simply down.

An angle that cannot be read is reported as ``unknown``, never as ``clear``.
The verdict is the *pessimistic* fold: any single angle saying ``busy`` makes
the whole verdict ``busy``.

What this script deliberately cannot see
----------------------------------------
The Thor endpoint is a **lobes gateway**, not a bare vLLM server: it serves no
Prometheus ``/metrics`` (verified — ``404 not found: /metrics``), so there is
**no server-side view of concurrent request load**. This script therefore
cannot prove Thor is idle from Thor's own side; it can only prove that *this
rig's known series* is not dialling it, and that the endpoint answers. Any
third party dialling Thor is invisible here, and the results document says so
rather than implying a stronger check than was run.

Usage::

    # one check, human-readable + JSON to stdout
    python3 thor-idle-guard.py

    # wait for an idle window, bounded, polling every 60s for up to 2h
    python3 thor-idle-guard.py --wait --poll-seconds 60 --max-wait-seconds 7200

    # append the verdict to the committed window log
    python3 thor-idle-guard.py --label before --append window-log.jsonl

Exit codes: ``0`` idle (safe to dial), ``1`` busy, ``2`` the check itself could
not be performed (and therefore ``busy`` by default — never optimistic).
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 -- ps/curl-free: only `ps` below, argv-list, no shell
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

#: The sibling worktree whose series contends for Thor.
SERIES_RAW_DIR = Path(
    "/home/spark/git/.worktrees.embodiment/t12/docs/live-test-results/orchestrator-worker-series-raw"
)
LADDER_DECISIONS = SERIES_RAW_DIR / "ladder-decisions.jsonl"

#: Arms that dial the Thor worker. Arm ``E`` is cortex-alone and does not.
THOR_DIALLING_ARMS = frozenset({"W", "M", "H"})
CORTEX_ONLY_ARMS = frozenset({"E"})

THOR_MODELS_URL = "http://thor.tail0be7e0.ts.net:8000/v1/models"
THOR_MODEL_ID = "unsloth/Qwen3.6-35B-A3B-NVFP4"

#: Command-line fragments that identify a series cell process.
CELL_PROCESS_MARKERS = ("run-cell.sh", "drive.py", "ladder.py")

VERDICT_IDLE = "idle"
VERDICT_BUSY = "busy"
VERDICT_UNKNOWN = "unknown"

EXIT_IDLE = 0
EXIT_BUSY = 1
EXIT_UNCHECKABLE = 2

HTTP_TIMEOUT_SECONDS = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ── angle 1: the process table ────────────────────────────────────────────────


def _ps_lines() -> Optional[list[str]]:
    """Every process command line, or ``None`` if ``ps`` itself could not run."""
    try:
        completed = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell, no user input
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return [line for line in completed.stdout.splitlines() if line.strip()]


def arm_of(command_line: str) -> Optional[str]:
    """The arm a series-cell command line names, or ``None``.

    Handles both spellings the series uses: ``run-cell.sh C2 E ...`` (positional
    rung then arm) and ``drive.py cell --rung C2 --arm E`` (flagged).
    """
    tokens = command_line.split()
    if "--arm" in tokens:
        index = tokens.index("--arm")
        if index + 1 < len(tokens):
            return tokens[index + 1].strip().upper() or None
    for index, token in enumerate(tokens):
        if token.endswith("run-cell.sh") and index + 2 < len(tokens):
            return tokens[index + 2].strip().upper() or None
    return None


def inspect_processes(lines: Optional[list[str]]) -> dict[str, Any]:
    """Classify every live series process. Never guesses: an unnamed arm is
    ``unknown`` and makes the whole angle ``busy``, because a cell whose arm
    cannot be read might be dialling Thor."""
    if lines is None:
        return {
            "angle": "processes",
            "verdict": VERDICT_UNKNOWN,
            "detail": "`ps` could not be run",
            "cells": [],
        }

    cells: list[dict[str, Any]] = []
    for line in lines:
        if not any(marker in line for marker in CELL_PROCESS_MARKERS):
            continue
        # The wrapper shell that *launched* a cell repeats the whole command
        # line; it is the same cell, so duplicates are fine — they cannot
        # change the verdict, only the row count.
        arm = arm_of(line)
        if arm is None and "ladder.py" in line:
            # The sequencer itself holds no cell open; it is recorded because
            # its presence means more cells are coming, which is why an idle
            # window found while it runs is a *window*, not a finished series.
            cells.append({"arm": None, "role": "sequencer", "command": line.strip()[:200]})
            continue
        cells.append({"arm": arm, "role": "cell", "command": line.strip()[:200]})

    cell_rows = [row for row in cells if row["role"] == "cell"]
    thor_arms = [row["arm"] for row in cell_rows if row["arm"] in THOR_DIALLING_ARMS]
    unnamed = [row for row in cell_rows if row["arm"] is None]
    cortex_arms = [row["arm"] for row in cell_rows if row["arm"] in CORTEX_ONLY_ARMS]

    if thor_arms:
        verdict, detail = VERDICT_BUSY, f"Thor-dialling cell(s) in flight: {sorted(set(thor_arms))}"
    elif unnamed:
        verdict, detail = VERDICT_BUSY, f"{len(unnamed)} cell process(es) with no readable --arm"
    elif cortex_arms:
        verdict = VERDICT_IDLE
        detail = f"only cortex-alone arm(s) in flight: {sorted(set(cortex_arms))} — Thor untouched"
    else:
        verdict, detail = VERDICT_IDLE, "no series cell process in flight"

    return {
        "angle": "processes",
        "verdict": verdict,
        "detail": detail,
        "sequencer_running": any(row["role"] == "sequencer" for row in cells),
        "cells": cells,
    }


# ── angle 2: the committed ladder decisions ───────────────────────────────────


def inspect_ladder(path: Path = LADDER_DECISIONS) -> dict[str, Any]:
    """Read the ladder's committed decisions. History, not liveness."""
    if not path.exists():
        return {
            "angle": "ladder",
            "verdict": VERDICT_UNKNOWN,
            "detail": f"no ladder decisions at {path}",
            "decisions": [],
        }
    decisions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decisions.append(json.loads(line))
        except json.JSONDecodeError:
            return {
                "angle": "ladder",
                "verdict": VERDICT_UNKNOWN,
                "detail": f"unparseable line in {path.name}",
                "decisions": decisions,
            }

    summary = [
        {
            "rung": row.get("rung"),
            "verdict": row.get("verdict"),
            "action": row.get("action"),
            "clause": row.get("clause"),
            "absent": row.get("absent") or [],
            "at": row.get("at"),
        }
        for row in decisions
    ]
    # The ladder record is never *evidence of idleness* on its own — a cell can
    # be in flight with nothing written. It is reported so the window
    # declaration can say which rungs resolved and which cells were never
    # dialled, and it is deliberately not allowed to vote `idle`.
    return {
        "angle": "ladder",
        "verdict": VERDICT_UNKNOWN,
        "detail": (
            f"{len(summary)} rung decision(s) recorded; history only — "
            "a cell in flight writes nothing here until it folds"
        ),
        "decisions": summary,
    }


# ── angle 3: Thor answers, and serves the model this probe dials ──────────────


def inspect_endpoint(url: str = THOR_MODELS_URL, model: str = THOR_MODEL_ID) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        # Fixed http URL from this module's own constant; audited once, here.
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as failure:
        return {
            "angle": "endpoint",
            "verdict": VERDICT_UNKNOWN,
            "detail": f"{type(failure).__name__}: {failure}",
            "models": [],
        }
    models = [str(entry.get("id")) for entry in (payload.get("data") or [])]
    if model not in models:
        return {
            "angle": "endpoint",
            "verdict": VERDICT_BUSY,
            "detail": f"{model!r} is not served here; served: {models}",
            "models": models,
        }
    return {
        "angle": "endpoint",
        "verdict": VERDICT_IDLE,
        "detail": f"reachable and serving {model!r}",
        "models": models,
        "note": "no /metrics on this gateway — server-side request load is NOT observable",
    }


# ── the pessimistic fold ──────────────────────────────────────────────────────


def fold(angles: list[dict[str, Any]]) -> dict[str, Any]:
    """Any ``busy`` angle makes the verdict ``busy``. ``unknown`` never votes idle.

    The endpoint angle is the only one that can *withhold* an idle verdict on
    its own (an unreachable endpoint is not a window); the process angle is the
    only one that can *grant* one.
    """
    by_angle = {row["angle"]: row for row in angles}
    if any(row["verdict"] == VERDICT_BUSY for row in angles):
        busy = [row["angle"] for row in angles if row["verdict"] == VERDICT_BUSY]
        return {"verdict": VERDICT_BUSY, "because": f"angle(s) report busy: {busy}"}
    processes = by_angle.get("processes", {})
    endpoint = by_angle.get("endpoint", {})
    if processes.get("verdict") != VERDICT_IDLE:
        return {"verdict": VERDICT_UNKNOWN, "because": "the process angle could not be read"}
    if endpoint.get("verdict") != VERDICT_IDLE:
        return {"verdict": VERDICT_UNKNOWN, "because": "Thor did not confirm it serves the model"}
    return {
        "verdict": VERDICT_IDLE,
        "because": "no Thor-dialling cell in flight and the endpoint serves the model",
    }


def check(label: str = "") -> dict[str, Any]:
    angles = [
        inspect_processes(_ps_lines()),
        inspect_ladder(),
        inspect_endpoint(),
    ]
    folded = fold(angles)
    return {
        "kind": "thor-idle-check",
        "label": label,
        "at": _now(),
        "verdict": folded["verdict"],
        "because": folded["because"],
        "angles": angles,
    }


def exit_code_for(verdict: str) -> int:
    if verdict == VERDICT_IDLE:
        return EXIT_IDLE
    if verdict == VERDICT_BUSY:
        return EXIT_BUSY
    return EXIT_UNCHECKABLE


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--label", default="", help="a name for this check, e.g. before / after")
    parser.add_argument("--append", default=None, help="append the verdict as JSONL to this path")
    parser.add_argument(
        "--wait", action="store_true", help="poll until idle (bounded by --max-wait-seconds)"
    )
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--max-wait-seconds", type=float, default=7200.0)
    parser.add_argument("--quiet", action="store_true", help="JSON only, no human summary")
    return parser


def _emit(report: dict[str, Any], args: argparse.Namespace) -> None:
    if not args.quiet:
        print(f"[{report['at']}] verdict={report['verdict']} — {report['because']}", file=sys.stderr)
        for angle in report["angles"]:
            print(f"  {angle['angle']:<10} {angle['verdict']:<8} {angle['detail']}", file=sys.stderr)
    if args.append:
        with open(args.append, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(report) + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    report = check(args.label)
    _emit(report, args)

    if args.wait and report["verdict"] != VERDICT_IDLE:
        deadline = time.monotonic() + args.max_wait_seconds
        # Bounded by construction: a fixed number of polls, no unbounded while.
        polls = max(1, int(args.max_wait_seconds // max(args.poll_seconds, 1.0)))
        for _ in range(polls):
            if time.monotonic() >= deadline:
                break
            time.sleep(args.poll_seconds)
            report = check(args.label)
            _emit(report, args)
            if report["verdict"] == VERDICT_IDLE:
                break

    print(json.dumps(report, indent=2))
    return exit_code_for(report["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())
