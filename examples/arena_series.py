#!/usr/bin/env python3
"""arena_series — drive the pre-registered 2x2 arena matrix, serially and resumably.

Task **t19**. The design, the predictions and the decision rule are committed
in ``docs/live-test-results/arena-series-preregistration.md`` and this module
executes them; it does not decide anything. Read that file first — if the two
disagree, the pre-registration is authoritative and this is the bug.

The matrix::

                   muse off     muse on
    --arm resident   RO           RM
    --arm command    CO           CM

Every cell is played at the same seeds, so a cell-to-cell difference cannot be
a scenario difference.

Serial on purpose
-----------------
The cortex is local and single. Parallel matches would contend for it and every
latency figure in the results would be fiction. This runner never runs two
matches at once, and says so here rather than leaving it to be inferred from
the absence of a thread.

Resumable on purpose
--------------------
The full series is hours of wall clock and the operator runs it unattended.
Something will go wrong. Each completed match is appended to the JSONL as it
finishes, and a re-run **skips every match already recorded** by its
``match_key``. Rule 2 of the pre-registration — nothing is re-run to get a
better number — is enforced here rather than trusted: a completed match is
never replayed, only skipped.

Continuity across matches
-------------------------
A separate arm, not a matrix cell. Match 1 receives an operator directive naming
an objective; match 2 runs in the **same eidetic store** with a fresh scratchpad,
a fresh arena and **no directive**, and must recover the objective from memory
alone. The ``control`` pair runs the identical match 2 against an **empty
store** and must fail — without it, a pass proves nothing, because the objective
could have come from the prompt or the model's prior.

Running it::

    python examples/arena_series.py --root /tmp/series --out results.jsonl --live
    python examples/arena_series.py --root /tmp/series --out results.jsonl --live  # resumes

Hermetic by default; ``--live`` is the only path that reaches a network.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 — fixed argv, no shell
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.challenge_config import write_config_preamble  # noqa: E402

SEAT = Path(__file__).resolve().parent / "league_seat.py"

DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")
DEFAULT_CORTEX = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
DEFAULT_MUSE = "nvidia/Gemma-4-31B-IT-NVFP4"
API_KEY_ENV = "COLLEAGUE_API_KEY"

ARM_RESIDENT = "resident"
ARM_COMMAND = "command"
ARMS = (ARM_RESIDENT, ARM_COMMAND)

#: Cell ids as the pre-registration names them. Kept as a literal map rather
#: than derived, so a reader can check this file against that one by eye.
CELLS = {
    (ARM_RESIDENT, False): "RO",
    (ARM_RESIDENT, True): "RM",
    (ARM_COMMAND, False): "CO",
    (ARM_COMMAND, True): "CM",
}

#: Pre-registered. Repetition i of every cell uses SEEDS[i].
SEEDS = (4242, 4243, 4244)
#: Pre-registered n per cell. Small, and stated as small.
DEFAULT_N = 3
DEFAULT_MAX_TURNS = 3

#: The objective the continuity arm must recover from memory alone.
OBJECTIVE = "cp-west"
DIRECTIVE = f"Operator directive: take {OBJECTIVE} and hold it; ignore cp-east."

PAIR_MEMORY = "memory"
PAIR_CONTROL = "control"
PAIRS = (PAIR_MEMORY, PAIR_CONTROL)

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNREADABLE = "UNREADABLE"
VERDICTS = (VERDICT_PASS, VERDICT_FAIL, VERDICT_UNREADABLE)


# ── the unit of work ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MatchSpec:
    """One match. ``key`` is what resumption dedupes on."""

    kind: str  # "matrix" | "continuity"
    cell: str
    arm: str
    muse: bool
    seed: int
    rep: int
    match_id: str
    #: Continuity only: which half of the pair, and whether a directive is given.
    pair: str = ""
    leg: int = 0
    directive: str = ""
    share_store_with: str = ""

    @property
    def key(self) -> str:
        return self.match_id


def matrix_specs(n: int) -> Iterator[MatchSpec]:
    """The 2x2, in a fixed order so a resumed run continues where it stopped."""
    for arm in ARMS:
        for muse in (False, True):
            cell = CELLS[(arm, muse)]
            for rep in range(n):
                yield MatchSpec(
                    kind="matrix",
                    cell=cell,
                    arm=arm,
                    muse=muse,
                    seed=SEEDS[rep % len(SEEDS)],
                    rep=rep,
                    match_id=f"{cell.lower()}-r{rep}",
                )


def continuity_specs(n: int) -> Iterator[MatchSpec]:
    """Pairs: leg 1 gets the directive, leg 2 must recover it from the store.

    The ``control`` pair runs leg 2 against a store leg 1 never wrote to.
    """
    for pair in PAIRS:
        for rep in range(n):
            seed = SEEDS[rep % len(SEEDS)]
            base = f"cont-{pair}-r{rep}"
            yield MatchSpec(
                kind="continuity",
                cell=f"CONT-{pair.upper()}",
                arm=ARM_COMMAND,
                muse=False,
                seed=seed,
                rep=rep,
                match_id=f"{base}-leg1",
                pair=pair,
                leg=1,
                directive=DIRECTIVE,
                # The control pair's leg 1 writes to its OWN store, which leg 2
                # then does not share — that is the whole control.
                share_store_with=base if pair == PAIR_MEMORY else "",
            )
            yield MatchSpec(
                kind="continuity",
                cell=f"CONT-{pair.upper()}",
                arm=ARM_COMMAND,
                muse=False,
                seed=seed,
                rep=rep,
                match_id=f"{base}-leg2",
                pair=pair,
                leg=2,
                directive="",  # the point: no directive on leg 2
                share_store_with=base if pair == PAIR_MEMORY else "",
            )


# ── running one match ─────────────────────────────────────────────────────────


def _store_for(root: Path, spec: MatchSpec) -> Path:
    """Leg 2 of a memory pair shares leg 1's store; everything else is its own."""
    if spec.share_store_with:
        return root / "stores" / spec.share_store_with / "memory"
    return root / "stores" / spec.match_id / "memory"


def seat_argv(spec: MatchSpec, root: Path, args: argparse.Namespace) -> list[str]:
    """The exact command line, so a reader can reproduce one match by hand."""
    store = _store_for(root, spec)
    workdir = root / "arenas" / spec.match_id
    argv = [
        sys.executable,
        str(SEAT),
        "play",
        "--arm",
        spec.arm,
        "--store",
        str(store),
        "--workdir",
        str(workdir),
        "--match-id",
        spec.match_id,
        "--seed",
        str(spec.seed),
        "--max-turns",
        str(args.max_turns),
        "--json",
    ]
    if spec.muse:
        argv.append("--muse")
    # An empty --directive is meaningful (leg 2 gets none), so it is passed
    # explicitly rather than omitted, which would fall back to the default.
    argv += ["--directive", spec.directive]
    if args.live:
        argv += [
            "--live",
            "--base-url",
            args.base_url,
            "--cortex-model",
            args.cortex_model,
            "--muse-model",
            args.muse_model,
        ]
    return argv


def run_match(spec: MatchSpec, root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Run one match and fold its report into one flat record.

    Never raises on a match failure: a match that dies is recorded with its
    error and the series continues. Rule 1 — a degraded match is data.
    """
    argv = seat_argv(spec, root, args)
    started = time.time()
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        argv, capture_output=True, text=True, timeout=args.match_timeout, check=False
    )
    elapsed = round(time.time() - started, 1)

    record: dict[str, Any] = {
        "match_key": spec.key,
        "kind": spec.kind,
        "cell": spec.cell,
        "arm": spec.arm,
        "muse": spec.muse,
        "seed": spec.seed,
        "rep": spec.rep,
        "pair": spec.pair,
        "leg": spec.leg,
        "directive_given_to_seat": bool(spec.directive),
        "seconds": elapsed,
        "returncode": proc.returncode,
    }
    if proc.returncode != 0:
        record["error"] = proc.stderr[-2000:]
        record["ok"] = False
        return record
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        record["error"] = f"unparsable seat report: {exc}"
        record["ok"] = False
        return record

    match = report.get("match") or {}
    turns = report.get("turns") or []
    record.update(
        {
            "ok": True,
            "replay_sha256": match.get("replay_sha256"),
            "driver_kinds": match.get("driver_kinds") or {},
            "turns_played": match.get("turns_played"),
            "status": match.get("status"),
            "winner": match.get("winner"),
            "score": match.get("score"),
            "degradations": report.get("degradations") or [],
            "aborted_turns": sum(1 for t in turns if (t.get("drive") or {}).get("aborted")),
            "turns": [
                {
                    "directive_given": t.get("directive_given"),
                    "objective_seen": t.get("objective_seen"),
                    "pid": t.get("pid"),
                    "orders": t.get("orders"),
                }
                for t in turns
            ],
        }
    )
    return record


# ── the continuity grader ─────────────────────────────────────────────────────


def grade_continuity(leg2: dict[str, Any]) -> tuple[str, str]:
    """Did leg 2 recover the objective from the store alone?

    Reads leg 2 only: leg 1's job was to write the memory, and whether it did
    is visible in leg 2 or nowhere. ``UNREADABLE`` exists so an ambiguous run
    cannot fall into the flattering arm — the defect t18 found in its own
    classifier.
    """
    if not leg2.get("ok"):
        return VERDICT_UNREADABLE, f"leg 2 did not complete: {leg2.get('error', '')[:200]}"
    turns = leg2.get("turns") or []
    if not turns:
        return VERDICT_UNREADABLE, "leg 2 played no turns"
    if any(t.get("directive_given") for t in turns):
        return (
            VERDICT_UNREADABLE,
            "leg 2 was given a directive — the arm is void, it could not have " "needed memory",
        )
    seen = [t.get("objective_seen") for t in turns]
    if all(s == OBJECTIVE for s in seen):
        return (
            VERDICT_PASS,
            f"every leg-2 turn carried {OBJECTIVE!r} with no directive given; the "
            f"only source was the store",
        )
    if not any(seen):
        return VERDICT_FAIL, "leg 2 carried no objective at all"
    return (
        VERDICT_UNREADABLE,
        f"leg 2 carried an inconsistent objective across turns: {seen}",
    )


# ── the series ────────────────────────────────────────────────────────────────


def completed_keys(out: Path) -> set[str]:
    """Match keys already recorded. A completed match is skipped, never replayed."""
    if not out.exists():
        return set()
    keys: set[str] = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            keys.add(str(json.loads(line)["match_key"]))
        except (json.JSONDecodeError, KeyError):
            continue
    return keys


def _refuse_a_committable_store(root: Path) -> None:
    """A match's memories must never land somewhere that can be committed."""
    target = root if root.is_dir() else root.parent
    try:
        proc = subprocess.run(  # nosec B603 B607 — fixed argv, no shell
            ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if proc.returncode == 0 and proc.stdout.strip() == "true":
        raise SystemExit(
            f"error: --root {root} is inside a git work tree; a match's memories "
            "would be committed\nhint: use a scratch path, e.g. --root /tmp/series"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", required=True, help="scratch root for stores and arenas")
    parser.add_argument("--out", required=True, help="JSONL, appended to; resumed from")
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--match-timeout", type=int, default=2400)
    parser.add_argument("--config-out", default=None)
    parser.add_argument("--reset", action="store_true", help="delete root and out first")
    parser.add_argument(
        "--only",
        choices=("matrix", "continuity", "all"),
        default="all",
        help="run one half of the series",
    )
    parser.add_argument("--warmup", action="store_true", help="play one throwaway match first")
    live = parser.add_argument_group("live rig (opt-in; nothing here is a default)")
    live.add_argument("--live", action="store_true")
    live.add_argument("--base-url", default=DEFAULT_BASE_URL)
    live.add_argument("--cortex-model", default=DEFAULT_CORTEX)
    live.add_argument("--muse-model", default=DEFAULT_MUSE)
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    out = Path(args.out).expanduser()
    if args.reset:
        shutil.rmtree(root, ignore_errors=True)
        out.unlink(missing_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    _refuse_a_committable_store(root)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.live and not os.environ.get(API_KEY_ENV, "").strip():
        print(f"error: --live needs {API_KEY_ENV} in the environment", file=sys.stderr)
        return 2

    if args.config_out:
        write_config_preamble(
            args.config_out,
            cortex_model=args.cortex_model if args.live else "scripted",
            cortex_temperature=0.3,
            muse_model=args.muse_model if args.live else "scripted",
            muse_temperature=0.7,
            max_turns=args.max_turns,
            n=args.n,
            extra={
                "series": "arena_series",
                "seeds": list(SEEDS),
                "cells": sorted(CELLS.values()),
                "objective": OBJECTIVE,
                "live": bool(args.live),
                "preregistration": "docs/live-test-results/arena-series-preregistration.md",
            },
        )

    specs: list[MatchSpec] = []
    if args.only in ("matrix", "all"):
        specs.extend(matrix_specs(args.n))
    if args.only in ("continuity", "all"):
        specs.extend(continuity_specs(args.n))

    done = completed_keys(out)
    todo = [s for s in specs if s.key not in done]
    print(
        f"series: {len(specs)} match(es); {len(done)} already recorded; " f"{len(todo)} to run",
        file=sys.stderr,
    )

    if args.warmup and todo:
        warm = MatchSpec(
            kind="warmup",
            cell="WARMUP",
            arm=ARM_COMMAND,
            muse=True,
            seed=SEEDS[0],
            rep=0,
            match_id="warmup",
            directive=DIRECTIVE,
        )
        started = time.time()
        run_match(warm, root, args)
        # Not data. Reported so a reader can see whether the rig was cold, since
        # /capabilities cannot tell a caller that (lobes-cli#146).
        print(f"warmup: {round(time.time() - started, 1)}s (not data)", file=sys.stderr)

    for index, spec in enumerate(todo, start=1):
        print(f"[{index}/{len(todo)}] {spec.cell} {spec.match_id} ...", file=sys.stderr)
        record = run_match(spec, root, args)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        print(
            f"    {'ok' if record.get('ok') else 'FAILED'} "
            f"{record.get('seconds')}s turns={record.get('turns_played')} "
            f"replay={str(record.get('replay_sha256'))[:12]}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
