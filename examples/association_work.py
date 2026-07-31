"""The association-work experiment: is the muse/cortex role split real?

Plan task **t18**. ``docs/relationships.md`` §3 parks a function map — senses
notice, cortex acts, muse reflects — behind a promotion gate. This harness is
the gate's measurement.

The question is **not** "is the muse good". It is whether the split is real. A
map that says "the muse reflects, the cortex acts" earns promotion only if the
muse's advantage is *concentrated on reflective work*. A muse that is uniformly
better, or uniformly worse, across both kinds of work makes the map decoration.

So the design is a 2x2 interaction over two already-committed, already-verified
graders. Nothing here grades anything itself:

======================  ==========================  ==========================
                        reflective work             executive work
======================  ==========================  ==========================
muse model              cell 1                      cell 2
cortex model            cell 3                      cell 4
======================  ==========================  ==========================

* **reflective** — ``examples/muse_challenge.py``'s three committed cortex
  results, graded by its five-gate v4 grader (``CHALLENGED``).
* **executive** — the three committed constrained problems
  (``challenge_subset``, ``challenge_register``, ``challenge_entropic``
  variant 3b), each with its own mechanical ``grade()``.

**The decision rule lives in this module as constants and is evaluated by**
:func:`decide`. That is deliberate. A threshold chosen after seeing a result
turns a promotion gate into a rubber stamp, so the threshold is committed
before the first dial and moving it later is a visible diff with a test
standing in front of it (``tests/test_association_work.py``).

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/association_work.py --live \\
        --n-reflective 4 --n-executive 3 --out results/association_work.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import challenge_entropic, challenge_register, challenge_subset  # noqa: E402
from examples.challenge_config import write_config_preamble  # noqa: E402
from examples.muse_challenge import run_case  # noqa: E402
from examples.muse_challenge import (  # noqa: E402
    CASES,
    FRAMING_TASK,
    MAX_ANCHOR_DENSITY,
    MAX_SHARED_BIGRAMS,
    VERDICT_CHALLENGED,
)
from examples.muse_challenge import gateway as muse_gateway  # noqa: E402

API_KEY_ENV = "COLLEAGUE_API_KEY"
DEFAULT_BASE_URL = os.environ.get("EMBODIMENT_BASE_URL", "http://localhost:8001/v1")


# ── the pre-registered configuration ─────────────────────────────────────────

#: The two minds, by model id. Named here rather than resolved by role so the
#: record says exactly what was on the wire.
CORTEX_MODEL = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
MUSE_MODEL = "nvidia/Gemma-4-31B-IT-NVFP4"

#: Temperature is held constant WITHIN an axis and differs BETWEEN them: the
#: comparison that carries the result is a difference of differences, so what
#: matters is that both minds meet each axis on identical terms. The reflective
#: value is the muse golden's deliberate 0.7; the executive value is the acting
#: harnesses' 0.3.
REFLECTIVE_TEMPERATURE = 0.7
EXECUTIVE_TEMPERATURE = 0.3

#: Completion budget per turn, both minds, both axes. 6000 rather than the muse
#: golden's 1600 because one of the two minds is a THINKING model: it spends
#: this budget on a reasoning field before writing any content, and the rig's
#: README records it returning ``content: None`` on a budget that ran out
#: mid-thought. A cell graded on a truncation would measure the harness.
MAX_TOKENS = 16000

#: The muse loop's thinking turns per boundary (the golden's value).
REFLECTIVE_MAX_TURNS = 3
#: The acting loop's model-turn budget per executive attempt.
EXECUTIVE_MAX_STEPS = 14

#: Host framing for the reflective axis. ``task`` — which names the kind of
#: work wanted — rather than the golden's ``bare`` control, because the
#: executive axis states its problem outright and an interaction between two
#: axes must put the same question to both minds on each. The consequence is
#: recorded as a limitation: this series does not measure whether either mind
#: reflects UNPROMPTED. The muse golden already has that number for the muse.
REFLECTIVE_FRAMING = FRAMING_TASK

#: Which grader each axis uses, verbatim, so a later grader revision is
#: visible against this record rather than silently applied to it.
REFLECTIVE_GRADER = (
    "examples/muse_challenge.py grade() v4 — five gates: near-copy "
    f"(<= {MAX_SHARED_BIGRAMS}), unqualified agreement, unnegated challenge "
    f"move, targeting, anchor density (<= {MAX_ANCHOR_DENSITY}). Pass = "
    f"{VERDICT_CHALLENGED}."
)
EXECUTIVE_GRADER = (
    "examples/challenge_subset.py grade(), challenge_register.py grade(), "
    "challenge_entropic.py grade_constrained() — mechanical, each pinned by "
    "tests/test_challenge_harnesses.py against an exhaustively computed truth()."
)

#: The executive problems, in order. ``entropic`` runs variant **3b** (the
#: constrained, unique-solution form) rather than variant 3: variant 3 grades
#: on reporting that a problem is UNDER-DETERMINED, which is a reflective move,
#: and putting it on the executive axis would blur the very contrast under test.
EXECUTIVE_PROBLEMS = ("subset", "register", "entropic3b")


# ── the pre-registered decision rule ─────────────────────────────────────────

#: The interaction that supports promotion, in points of pass-rate. Chosen
#: before the first dial, from what this series can resolve: cells are binary
#: at n = 9..12, so one flip is worth ~0.08-0.11 and a difference of two
#: differences below ~0.4 is inside the noise. The precedent is committed:
#: `designed-problem.md` claimed a muse effect from a 1-cell gap at n = 4 and
#: had to retract it.
MIN_INTERACTION = 0.40

#: The muse must actually be BETTER at reflective work, not merely less bad at
#: it. Without this an interaction could be carried entirely by the executive
#: axis — "the muse is terrible at acting" — which is not what the map claims.
#: 0.25 is three runs of twelve.
MIN_REFLECTIVE_GAP = 0.25

#: A cell whose runs failed in TRANSPORT more than this often is not a
#: measurement of a mind. The axis is then inconclusive, never a result.
MAX_ERROR_FRACTION = 1.0 / 3.0

DECISION_SUPPORTS = "SUPPORTS-PROMOTION"
DECISION_NEGATIVE = "NEGATIVE"
DECISION_INCONCLUSIVE = "INCONCLUSIVE"
#: Every outcome this rule can return. Two of the three block promotion, and a
#: negative is a complete result — not a prompt to re-run until it passes.
DECISIONS = (DECISION_SUPPORTS, DECISION_NEGATIVE, DECISION_INCONCLUSIVE)


@dataclass
class Cell:
    """One quadrant: how many runs, how many passed, how many never ran."""

    name: str
    axis: str
    model: str
    runs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.runs)

    @property
    def passes(self) -> int:
        return sum(1 for run in self.runs if run.get("passed"))

    @property
    def errors(self) -> int:
        """Runs that failed in transport rather than producing a graded answer."""
        return sum(1 for run in self.runs if run.get("transport_error"))

    @property
    def rate(self) -> float:
        return round(self.passes / self.n, 4) if self.n else 0.0

    @property
    def error_fraction(self) -> float:
        return round(self.errors / self.n, 4) if self.n else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "cell": self.name,
            "axis": self.axis,
            "model": self.model,
            "n": self.n,
            "passes": self.passes,
            "rate": self.rate,
            "transport_errors": self.errors,
            "error_fraction": self.error_fraction,
        }


def _at_a_wall(left: float, right: float) -> bool:
    """True when both cells sit on the same floor or the same ceiling.

    An axis where both minds score 0/n, or both score n/n, cannot discriminate
    between them; a difference of zero there is an absence of resolution, not a
    finding of no difference. Reported as inconclusive rather than folded into
    an interaction it would silently inflate.
    """
    return left == right and left in (0.0, 1.0)


def decide(
    reflective_muse: Cell,
    reflective_cortex: Cell,
    executive_muse: Cell,
    executive_cortex: Cell,
) -> dict[str, Any]:
    """Apply the pre-registered rule. Pure: no IO, no clock, no model.

    Returns the four rates, both differences, the interaction, one of
    :data:`DECISIONS`, and the list of conditions with their verdicts — so a
    reader sees which condition decided, not just the word.
    """
    cells = (reflective_muse, reflective_cortex, executive_muse, executive_cortex)
    d_reflective = round(reflective_muse.rate - reflective_cortex.rate, 4)
    d_executive = round(executive_muse.rate - executive_cortex.rate, 4)
    interaction = round(d_reflective - d_executive, 4)

    conditions: list[dict[str, Any]] = []

    def check(name: str, met: bool, detail: str, kind: str) -> bool:
        conditions.append({"condition": name, "met": bool(met), "detail": detail, "kind": kind})
        return bool(met)

    empty = [cell.name for cell in cells if cell.n == 0]
    v1 = check(
        "V1 every cell has data",
        not empty,
        f"cells with no runs: {empty or 'none'}",
        "validity",
    )
    noisy = [cell.name for cell in cells if cell.error_fraction > MAX_ERROR_FRACTION]
    v2 = check(
        "V2 transport failures under one third",
        not noisy,
        f"cells over the limit: {noisy or 'none'}",
        "validity",
    )
    v3 = check(
        "V3 reflective axis can discriminate",
        not _at_a_wall(reflective_muse.rate, reflective_cortex.rate),
        f"muse {reflective_muse.rate} vs cortex {reflective_cortex.rate}",
        "validity",
    )
    v4 = check(
        "V4 executive axis can discriminate",
        not _at_a_wall(executive_muse.rate, executive_cortex.rate),
        f"muse {executive_muse.rate} vs cortex {executive_cortex.rate}",
        "validity",
    )
    p1 = check(
        f"P1 interaction >= {MIN_INTERACTION}",
        interaction >= MIN_INTERACTION,
        f"interaction {interaction}",
        "promotion",
    )
    p2 = check(
        f"P2 reflective gap >= {MIN_REFLECTIVE_GAP}",
        d_reflective >= MIN_REFLECTIVE_GAP,
        f"muse - cortex on reflective work {d_reflective}",
        "promotion",
    )

    valid = v1 and v2 and v3 and v4
    if not valid:
        decision = DECISION_INCONCLUSIVE
    elif p1 and p2:
        decision = DECISION_SUPPORTS
    else:
        decision = DECISION_NEGATIVE

    return {
        "cells": [cell.summary() for cell in cells],
        "d_reflective": d_reflective,
        "d_executive": d_executive,
        "interaction": interaction,
        "decision": decision,
        "conditions": conditions,
        "rule": {
            "min_interaction": MIN_INTERACTION,
            "min_reflective_gap": MIN_REFLECTIVE_GAP,
            "max_error_fraction": round(MAX_ERROR_FRACTION, 4),
        },
    }


# ── running the cells ────────────────────────────────────────────────────────


def reflective_runs(
    complete: Callable[..., Any],
    *,
    model: str,
    repeats: int,
    sink: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """Grade *complete* on every reflective case, *repeats* times.

    :meth:`~embodiment.muse.MuseLoop.think` never raises, so a dead endpoint
    arrives as a degradation record and a ``SILENT`` verdict rather than an
    exception — which is why this half has no try/except and the executive half
    does.
    """
    out: list[dict[str, Any]] = []
    for repeat in range(repeats):
        for case in CASES:
            started = time.time()
            report = run_case(
                case,
                complete,
                max_turns=REFLECTIVE_MAX_TURNS,
                framing=REFLECTIVE_FRAMING,
            )
            record = report.to_dict()
            record.update(
                {
                    "axis": "reflective",
                    "model": model,
                    "repeat": repeat,
                    "problem": case.id,
                    "elapsed_seconds": round(time.time() - started, 1),
                    "passed": report.grade.get("verdict") == VERDICT_CHALLENGED,
                    # A degraded thinking session is a transport failure: the
                    # mind never answered, so the cell has no measurement of it.
                    "transport_error": bool(report.degradations) and not report.response,
                }
            )
            out.append(record)
            sink(record)
    return out


def cells_from_records(
    records: list[dict[str, Any]],
    *,
    muse_model: str = MUSE_MODEL,
    cortex_model: str = CORTEX_MODEL,
) -> dict[str, Cell]:
    """Rebuild the four cells from committed raw records.

    The committed JSONL — not this process's memory — is the artifact, so the
    decision has to be reproducible from it alone. A run that crashes mid-series
    still leaves every completed run on disk, and a reader who distrusts the
    reported number can recompute it with ``--analyse``.
    """
    cells: dict[str, Cell] = {}
    for axis in ("reflective", "executive"):
        for label, model in (("muse", muse_model), ("cortex", cortex_model)):
            runs = [r for r in records if r.get("axis") == axis and r.get("model") == model]
            if runs:
                cells[f"{axis}_{label}"] = Cell(f"{axis}_{label}", axis, model, runs)
    return cells


def failure_modes(cell: Cell) -> dict[str, int]:
    """Split an executive cell's failures into protocol and reasoning.

    Both count as not-correct — a mind that cannot complete the work has not
    completed it — but "could not drive the tool loop" and "drove it and reasoned
    wrongly" are different findings, and the pre-registration promised the split.

    A run is a PROTOCOL failure when nothing gradeable was submitted: a transport
    error, an exit that is not ``finished``, an empty summary, or a summary the
    harness could not read as an answer at all (``NO ANSWER``). Everything else
    that failed reached the grader with an answer and was wrong.

    **This predicate is a disclosure, not a gate**: it changes no pass count and
    is not read by :func:`decide`. Its exact wording was settled after the
    pre-registration commit and before any executive run existed, which is
    recorded in the results document rather than left for a reader to notice.

    **KNOWN DEFECT — it under-counts reasoning failures, and the direction is
    flattering.** A budget exit is charged as *protocol* even when the
    transcript ends on a definite, wrong answer that simply never went through
    the ``finish`` tool. In the 2026-07-29 series this reported ``0`` reasoning
    failures while 5 of the muse's 6 failures stated a wrong final answer in
    prose. It is left as it was rather than retuned after the data — retuning a
    measurement to fit the result it just produced is the move this whole
    harness exists to avoid — and the corrected reading is recorded in
    ``docs/live-test-results/association-work.md``. **Read the transcripts; do
    not read this counter as a claim about how a model reasons.**
    """
    modes = {"passed": 0, "protocol": 0, "reasoning": 0}
    for run in cell.runs:
        if run.get("passed"):
            modes["passed"] += 1
        elif (
            run.get("transport_error")
            or run.get("exit_reason") != "finished"
            or not str(run.get("raw_summary") or "").strip()
            or str(run.get("verdict") or "").startswith("NO ANSWER")
        ):
            modes["protocol"] += 1
        else:
            modes["reasoning"] += 1
    return modes


def _executive_once(problem: str, complete: Callable[..., Any]) -> dict[str, Any]:
    """One executive attempt, dispatched to the harness that owns the problem."""
    if problem == "subset":
        return challenge_subset.run_once(complete, max_steps=EXECUTIVE_MAX_STEPS)
    if problem == "register":
        return challenge_register.run_once(complete, max_steps=EXECUTIVE_MAX_STEPS)
    if problem == "entropic3b":
        return challenge_entropic.run_once(complete, variant="3b", max_steps=EXECUTIVE_MAX_STEPS)
    raise ValueError(f"unknown executive problem {problem!r}")


def executive_runs(
    gateway_for: Callable[[str], Callable[..., Any]],
    *,
    model: str,
    repeats: int,
    sink: Callable[[dict[str, Any]], None],
    problems: tuple[str, ...] = EXECUTIVE_PROBLEMS,
) -> list[dict[str, Any]]:
    """Grade a model on every executive problem, *repeats* times.

    *gateway_for* is handed a problem name and returns the acting seam for it —
    each harness owns its own tool schema, so the seam cannot be built once.

    The acting loop DOES raise when the injected seam raises
    (``LoopAborted``), so a transport failure is caught here and recorded as an
    ungraded run rather than ending the series. It counts as "not correct" —
    a mind that cannot complete the work has not completed it — and it is also
    counted separately, because a cell full of transport errors is not a
    measurement of a mind and :func:`decide` refuses to read it as one.
    """
    out: list[dict[str, Any]] = []
    for repeat in range(repeats):
        for problem in problems:
            started = time.time()
            try:
                graded = _executive_once(problem, gateway_for(problem))
                transport_error = False
            except Exception as exc:  # noqa: BLE001  # every fault is recorded alike
                graded = {
                    "verdict": "HARNESS ERROR",
                    "is_correct": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
                transport_error = True
            graded.update(
                {
                    "axis": "executive",
                    "model": model,
                    "repeat": repeat,
                    "problem": problem,
                    "elapsed_seconds": round(time.time() - started, 1),
                    "passed": bool(graded.get("is_correct")),
                    "transport_error": transport_error,
                }
            )
            out.append(graded)
            sink(graded)
    return out


def _tools_for(problem: str) -> list[dict[str, Any]]:
    if problem == "subset":
        return challenge_subset.TOOLS
    if problem == "register":
        return challenge_register.TOOLS
    if problem == "entropic3b":
        return challenge_entropic.TOOLS
    raise ValueError(f"unknown executive problem {problem!r}")


def executive_gateway_for(problem: str, base_url: str, model: str, key: str) -> Callable[..., Any]:
    """The acting seam for *problem*, at the pre-registered executive settings."""
    module = {
        "subset": challenge_subset,
        "register": challenge_register,
        "entropic3b": challenge_entropic,
    }[problem]
    return module.gateway(
        base_url,
        model,
        key,
        tools=_tools_for(problem),
        temperature=EXECUTIVE_TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--live", action="store_true", help="dial both real minds")
    parser.add_argument(
        "--analyse",
        action="store_true",
        help="dial nothing; re-apply the committed rule to the committed --out records",
    )
    parser.add_argument("--n-reflective", type=int, default=4, help="repeats per reflective case")
    parser.add_argument("--n-executive", type=int, default=3, help="repeats per executive problem")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cortex-model", default=CORTEX_MODEL)
    parser.add_argument("--muse-model", default=MUSE_MODEL)
    parser.add_argument(
        "--axis",
        choices=("both", "reflective", "executive"),
        default="both",
        help="run one axis only; the decision needs both, so a partial run reports no decision",
    )
    parser.add_argument(
        "--out",
        default="results/association_work.jsonl",
        help="raw responses, one JSON object per run — committed beside the results doc",
    )
    parser.add_argument(
        "--results",
        default="results/association_work_config.json",
        help="where the config preamble is written, BEFORE the first result line",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def config_record(args: argparse.Namespace) -> dict[str, Any]:
    """The whole pre-registered configuration, as one record."""
    return write_config_preamble(
        str(Path(args.results).expanduser()),
        cortex_model=args.cortex_model,
        cortex_temperature=EXECUTIVE_TEMPERATURE,
        muse_model=args.muse_model,
        muse_temperature=REFLECTIVE_TEMPERATURE,
        max_turns=EXECUTIVE_MAX_STEPS,
        staleness_policy="none — neither axis runs a background muse lane",
        n=args.n_reflective * len(CASES) + args.n_executive * len(EXECUTIVE_PROBLEMS),
        extra={
            "experiment": "association-work 2x2 (plan task t18)",
            "axis": args.axis,
            "reflective_temperature": REFLECTIVE_TEMPERATURE,
            "executive_temperature": EXECUTIVE_TEMPERATURE,
            "reflective_max_turns": REFLECTIVE_MAX_TURNS,
            "executive_max_steps": EXECUTIVE_MAX_STEPS,
            "max_tokens": MAX_TOKENS,
            "reflective_framing": REFLECTIVE_FRAMING,
            "reflective_cases": [case.id for case in CASES],
            "executive_problems": list(EXECUTIVE_PROBLEMS),
            "reflective_grader": REFLECTIVE_GRADER,
            "executive_grader": EXECUTIVE_GRADER,
            "n_reflective_per_cell": args.n_reflective * len(CASES),
            "n_executive_per_cell": args.n_executive * len(EXECUTIVE_PROBLEMS),
            "min_interaction": MIN_INTERACTION,
            "min_reflective_gap": MIN_REFLECTIVE_GAP,
            "max_error_fraction": round(MAX_ERROR_FRACTION, 4),
            "decision_rule": (
                "SUPPORTS-PROMOTION iff V1 every cell has data, V2 no cell over "
                "one third transport errors, V3 and V4 neither axis has both "
                f"minds at the same floor/ceiling, P1 interaction >= "
                f"{MIN_INTERACTION}, and P2 reflective gap >= "
                f"{MIN_REFLECTIVE_GAP}. Any other completed 2x2 is NEGATIVE; a "
                "validity failure is INCONCLUSIVE. Both block promotion."
            ),
        },
    )


def _analyse(args: argparse.Namespace) -> int:
    """Re-apply the committed rule to the committed records. Dials nothing."""
    path = Path(args.out).expanduser()
    if not path.exists():
        print(f"error: no records at {path}", file=sys.stderr)
        print("hint: run the series with --live first, or pass --out", file=sys.stderr)
        return 1
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    cells = cells_from_records(records, muse_model=args.muse_model, cortex_model=args.cortex_model)
    report: dict[str, Any] = {
        "source": str(path),
        "records": len(records),
        "cells": {name: cell.summary() for name, cell in cells.items()},
        "failure_modes": {
            name: failure_modes(cell) for name, cell in cells.items() if cell.axis == "executive"
        },
    }
    if len(cells) == 4:
        report["analysis"] = decide(
            cells["reflective_muse"],
            cells["reflective_cortex"],
            cells["executive_muse"],
            cells["executive_cortex"],
        )
    else:
        report["analysis"] = {
            "decision": None,
            "why": "a partial run cannot be judged against a rule about an interaction",
        }
    print(json.dumps(report, indent=2, default=str))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.analyse:
        return _analyse(args)

    key = ""
    if args.live:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            print(f"error: {API_KEY_ENV} is not set", file=sys.stderr)
            print("hint: export it before running --live", file=sys.stderr)
            return 2
    else:
        print("error: this harness has no scripted mode", file=sys.stderr)
        print("hint: the 2x2 is a live measurement; pass --live", file=sys.stderr)
        return 1

    for path in (args.results, args.out):
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    config = config_record(args)

    out_path = Path(args.out).expanduser()
    handle = out_path.open("a", encoding="utf-8")

    def sink(record: dict[str, Any]) -> None:
        handle.write(json.dumps(record, default=str) + "\n")
        handle.flush()

    cells: dict[str, Cell] = {}
    try:
        if args.axis in ("both", "reflective"):
            for label, model in (("muse", args.muse_model), ("cortex", args.cortex_model)):
                complete = muse_gateway(
                    args.base_url,
                    model,
                    key,
                    temperature=REFLECTIVE_TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )
                runs = reflective_runs(complete, model=model, repeats=args.n_reflective, sink=sink)
                cells[f"reflective_{label}"] = Cell(
                    f"reflective_{label}", "reflective", model, runs
                )
        if args.axis in ("both", "executive"):
            for label, model in (("muse", args.muse_model), ("cortex", args.cortex_model)):

                def gateway_for(problem: str, _model: str = model) -> Callable[..., Any]:
                    return executive_gateway_for(problem, args.base_url, _model, key)

                runs = executive_runs(gateway_for, model=model, repeats=args.n_executive, sink=sink)
                cells[f"executive_{label}"] = Cell(f"executive_{label}", "executive", model, runs)
    finally:
        handle.close()

    report: dict[str, Any] = {
        "config": config,
        "raw_responses": str(out_path),
        "cells": {name: cell.summary() for name, cell in cells.items()},
    }
    if len(cells) == 4:
        report["analysis"] = decide(
            cells["reflective_muse"],
            cells["reflective_cortex"],
            cells["executive_muse"],
            cells["executive_cortex"],
        )
    else:
        report["analysis"] = {
            "decision": None,
            "why": "a partial run cannot be judged against a rule about an interaction",
        }

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
