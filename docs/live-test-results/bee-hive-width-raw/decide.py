#!/usr/bin/env python3
"""decide — the ``W1`` rung's decision rule, as pure functions over records.

Plan task ``t10``. Every rule here is transcribed from
[`bee-hive-width-preregistration.md`](../bee-hive-width-preregistration.md)
**§9 (the decision rule)** and **§10 (the stop rule)**, and every constant is
imported from ``tests/test_bee_hive_width_preregistration.py`` — the pin —
rather than re-typed. Nothing here dials anything, nothing here reads rig
state, and no function here has a side effect: hand it the same records twice
and it returns the same verdict twice.

The point of the separation is that **the stop rule is applied by code, not by
judgement**. The verdict a rung publishes is whatever :func:`grade` returns
from the committed records, computed once after the rung stops.

The cell record shape it reads (``cells.jsonl``, one JSON object per line)::

    {
      "kind": "bee-hive-width-cell",
      "run_id": "...", "rung": "W1", "arm": "B1",
      "width": 8, "grain": "batch8", "items_per_call": 8, "repetition": 0,
      "started": "...", "finished": "...",
      "cell_wall_seconds": 21.1,
      "batch_elapsed_seconds": [...],      # one per dispatched wave
      "batch_retries": [...],              # transport retries in each wave
      "retries": 0,                        # their sum
      "acceptance": {
        "dispatched": 40,
        "counts": {"accepted": 40, "refused-off-space": 0, ...},
        "prompt_tokens": 0, "completion_tokens": 0
      },
      "senses_config_hash": "..."
    }

Run it against the committed records::

    uv run python docs/live-test-results/bee-hive-width-raw/decide.py
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples import arch_hive as ah  # noqa: E402
from tests import test_bee_hive_width_preregistration as pin  # noqa: E402

# ── the registered constants, imported and never re-typed ────────────────────

WIDTH_LADDER: tuple[int, ...] = pin.WIDTH_LADDER
GRAIN_LADDER: tuple[str, ...] = pin.GRAIN_LADDER
GRAIN_ITEMS: tuple[int, ...] = pin.GRAIN_ITEMS
ITEMS_PER_CELL: int = pin.ITEMS_PER_CELL
REPETITIONS_PER_CELL: int = pin.REPETITIONS_PER_CELL
MIN_BATCHES_PER_CELL: int = pin.MIN_BATCHES_PER_CELL
MIN_CELL_SECONDS: float = pin.MIN_CELL_SECONDS
PROTOCOL_GATE: float = pin.PROTOCOL_GATE
DIRECTION_FRACTION: float = pin.DIRECTION_FRACTION

#: §9's direction rule reuses ``league_commander.direction_required`` rather
#: than re-deriving one. At n = 3 it is 3 — all three repetitions must agree.
direction_required = pin.lc.direction_required

#: §9 gate 1: a cell left with fewer than ``MIN_BATCHES_PER_CELL - 1`` clean
#: batches is VOID.
MIN_CLEAN_BATCHES: int = MIN_BATCHES_PER_CELL - 1

#: The acceptance vocabulary is ``arch_hive``'s, imported so one word means one
#: thing across the harness and its decision rule.
ACCEPTED = ah.ACCEPTED
REFUSALS: tuple[str, ...] = ah.REFUSALS
ABSENCES: tuple[str, ...] = ah.ABSENCES
ABSENT_TRUNCATED = ah.ABSENT_TRUNCATED

# ── the verdict vocabulary (§9), fixed before the data ───────────────────────

SEPARATED_WIDTH = "SEPARATED-WIDTH"
INCONCLUSIVE_WIDTH = "INCONCLUSIVE-WIDTH"
CEILING_WIDTH = "CEILING-WIDTH"
VOID = "VOID"
ABSENT = "ABSENT"

VERDICTS: tuple[str, ...] = (
    SEPARATED_WIDTH,
    INCONCLUSIVE_WIDTH,
    CEILING_WIDTH,
    VOID,
    ABSENT,
)

# ── the gate vocabulary (§9), one name per registered gate ───────────────────

GATE_RETRY = "retry-contaminated"
GATE_TRUNCATED = "truncated"
GATE_REFUSAL = "refusal-gate"
GATE_PLAN_CLAMPED = "plan-clamped"
GATE_PROMPT_DRIFT = "prompt-drift"

#: NOT in §9's numbered list, and named separately for that reason. §9 gate 5
#: covers a *prompt-token* disagreement within a grain row; the brief's fourth
#: gate — "a ``senses_config_hash`` mismatch within a grain row voids every cell
#: in that row" — is the same shape one layer up, and ``arch_hive`` already
#: refuses a run whose tiers disagree about senses
#: (:func:`examples.arch_hive.assert_senses_identical`). It is checked here too
#: so a hash that changed *between* cells cannot pass unnoticed.
GATE_SENSES_DRIFT = "senses-drift"

#: Gates 1-4 are per cell; 5 and the senses check are per grain row.
CELL_GATES: tuple[str, ...] = (GATE_RETRY, GATE_TRUNCATED, GATE_REFUSAL, GATE_PLAN_CLAMPED)
ROW_GATES: tuple[str, ...] = (GATE_PROMPT_DRIFT, GATE_SENSES_DRIFT)

#: §10 rule 2's rungs, named so that "ABSENT by name" is literally by name.
LADDER_ABOVE_W1: tuple[str, ...] = ("W2", "H0", "sweep", "P1")

#: §10 rule 2's registered sentence, quoted so it cannot be softened later.
STOPPED_ANSWER = (
    "width did not separate on the axis where separation is structural; "
    "the flat default stands on evidence."
)

STOP_CONTINUE = "CONTINUE"
STOP_STOP = "STOP"
#: §10 has no branch for "no grain SEPARATED and not every grain is
#: INCONCLUSIVE or VOID" — a row that came back CEILING-WIDTH or ABSENT falls
#: between rules 2 and 3. Reported by this name rather than resolved by
#: judgement; see the report's findings section.
STOP_UNREGISTERED = "STOP-UNREGISTERED"


# ── reading one cell ─────────────────────────────────────────────────────────


def cell_key(cell: Mapping[str, Any]) -> tuple[str, int, int]:
    """``(grain, width, repetition)`` — the identity of one dialled cell."""
    return (
        str(cell.get("grain")),
        int(cell.get("width") or 0),
        int(cell.get("repetition") or 0),
    )


def counts(cell: Mapping[str, Any]) -> dict[str, int]:
    raw = (cell.get("acceptance") or {}).get("counts") or {}
    return {str(name): int(value) for name, value in raw.items()}


def dispatched(cell: Mapping[str, Any]) -> int:
    return int((cell.get("acceptance") or {}).get("dispatched") or 0)


def prompt_tokens(cell: Mapping[str, Any]) -> int:
    return int((cell.get("acceptance") or {}).get("prompt_tokens") or 0)


def completion_tokens(cell: Mapping[str, Any]) -> int:
    return int((cell.get("acceptance") or {}).get("completion_tokens") or 0)


def truncated(cell: Mapping[str, Any]) -> int:
    """§9 gate 2's quantity: ``absent-truncated``, read on its own."""
    return counts(cell).get(ABSENT_TRUNCATED, 0)


def refusal_fraction(cell: Mapping[str, Any]) -> Optional[float]:
    """§9 gate 3: refusals over the **truncation-free** denominator.

    The denominator is dispatched calls that were not ``absent-*``. ``None``
    when that denominator is zero — a cell where every call was an instrument
    event has no refusal fraction, and either 0.0 or 1.0 would be read as one.
    """
    tally = counts(cell)
    refused = sum(tally.get(name, 0) for name in REFUSALS)
    absent = sum(tally.get(name, 0) for name in ABSENCES)
    denominator = dispatched(cell) - absent
    if denominator <= 0:
        return None
    return refused / denominator


def clean_batches(cell: Mapping[str, Any]) -> tuple[int, float]:
    """§9 gate 1's remedy: drop every retry-contaminated batch **whole**.

    Returns ``(kept_batches, kept_seconds)``. Whole batches rather than whole
    calls because a retried call's neighbours shared its batch clock — t8's
    committed correction, where one 20 s sleep in 141 calls would have
    published a 0.25x "concurrency makes scoped calls slower" speedup.
    """
    elapsed = [float(value) for value in (cell.get("batch_elapsed_seconds") or [])]
    retries = [int(value) for value in (cell.get("batch_retries") or [])]
    if len(retries) != len(elapsed):
        # No per-batch retry record: fall back to the cell total, which is the
        # conservative reading (one retry anywhere contaminates everything).
        total = int(cell.get("retries") or 0)
        retries = [total] + [0] * (len(elapsed) - 1) if elapsed else []
    kept = [seconds for seconds, retried in zip(elapsed, retries) if retried == 0]
    return len(kept), float(sum(kept))


def items_per_second(cell: Mapping[str, Any], *, clean: bool = True) -> Optional[float]:
    """§9's outcome metric. **Acceptance is never in the numerator.**

    ``clean=False`` is the raw registered form, ``ITEMS_PER_CELL /
    cell_wall_seconds``. ``clean=True`` applies gate 1's whole-batch drop and
    divides the *retained* items by the *retained* batch clock — the same
    quantity when nothing was retried, which is the case the rung expects.
    """
    if not clean:
        seconds = float(cell.get("cell_wall_seconds") or 0.0)
        return None if seconds <= 0 else ITEMS_PER_CELL / seconds
    kept_batches, kept_seconds = clean_batches(cell)
    if kept_batches <= 0 or kept_seconds <= 0:
        return None
    width = int(cell.get("width") or 0)
    per_call = int(cell.get("items_per_call") or 0)
    kept_items = kept_batches * width * per_call
    return kept_items / kept_seconds


def cell_gates(cell: Mapping[str, Any]) -> tuple[str, ...]:
    """§9 gates 1-4, in registered order. Empty means the cell grades."""
    fired: list[str] = []

    kept_batches, _kept_seconds = clean_batches(cell)
    if kept_batches < MIN_CLEAN_BATCHES:
        fired.append(GATE_RETRY)

    if truncated(cell) > 0:
        fired.append(GATE_TRUNCATED)

    fraction = refusal_fraction(cell)
    if fraction is not None and fraction > PROTOCOL_GATE:
        fired.append(GATE_REFUSAL)

    per_call = int(cell.get("items_per_call") or 0)
    if dispatched(cell) * per_call != ITEMS_PER_CELL:
        fired.append(GATE_PLAN_CLAMPED)

    return tuple(fired)


# ── one cell, graded ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CellReading:
    """One cell's record reduced to what §9 reads. Never a correctness claim."""

    grain: str
    width: int
    repetition: int
    items_per_call: int
    cell_wall_seconds: float
    batches: int
    clean_batches: int
    retries: int
    dispatched: int
    counts: Mapping[str, int]
    refusal_fraction: Optional[float]
    prompt_tokens: int
    completion_tokens: int
    senses_config_hash: str
    gates: tuple[str, ...]
    items_per_second: Optional[float]
    items_per_second_raw: Optional[float]

    @property
    def graded(self) -> bool:
        """Gates passed **and** the metric landed. Both, or it does not grade."""
        return not self.gates and self.items_per_second is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "grain": self.grain,
            "width": self.width,
            "repetition": self.repetition,
            "items_per_call": self.items_per_call,
            "cell_wall_seconds": round(self.cell_wall_seconds, 4),
            "batches": self.batches,
            "clean_batches": self.clean_batches,
            "retries": self.retries,
            "dispatched": self.dispatched,
            "counts": dict(self.counts),
            "refusal_fraction": (
                None if self.refusal_fraction is None else round(self.refusal_fraction, 4)
            ),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "senses_config_hash": self.senses_config_hash,
            "gates": list(self.gates),
            "graded": self.graded,
            "items_per_second": (
                None if self.items_per_second is None else round(self.items_per_second, 4)
            ),
            "items_per_second_raw": (
                None if self.items_per_second_raw is None else round(self.items_per_second_raw, 4)
            ),
        }


def read_cell(cell: Mapping[str, Any]) -> CellReading:
    """One record, reduced. Pure: no file, no clock, no network."""
    kept_batches, _kept_seconds = clean_batches(cell)
    elapsed = list(cell.get("batch_elapsed_seconds") or [])
    return CellReading(
        grain=str(cell.get("grain")),
        width=int(cell.get("width") or 0),
        repetition=int(cell.get("repetition") or 0),
        items_per_call=int(cell.get("items_per_call") or 0),
        cell_wall_seconds=float(cell.get("cell_wall_seconds") or 0.0),
        batches=len(elapsed),
        clean_batches=kept_batches,
        retries=int(cell.get("retries") or 0),
        dispatched=dispatched(cell),
        counts=counts(cell),
        refusal_fraction=refusal_fraction(cell),
        prompt_tokens=prompt_tokens(cell),
        completion_tokens=completion_tokens(cell),
        senses_config_hash=str(cell.get("senses_config_hash") or ""),
        gates=cell_gates(cell),
        items_per_second=items_per_second(cell, clean=True),
        items_per_second_raw=items_per_second(cell, clean=False),
    )


# ── one grain row, graded ────────────────────────────────────────────────────


@dataclass(frozen=True)
class WidthReading:
    """One (grain, width) column: the paired comparison against the control."""

    width: int
    repetitions: tuple[int, ...]
    items_per_second: tuple[float, ...]
    paired: int
    wins: int
    required: int
    separated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "repetitions": list(self.repetitions),
            "items_per_second": [round(value, 4) for value in self.items_per_second],
            "paired_repetitions": self.paired,
            "wins": self.wins,
            "direction_required": self.required,
            "separated": self.separated,
        }


@dataclass(frozen=True)
class GrainDecision:
    """One grain's verdict, and everything §9 read to reach it."""

    grain: str
    items_per_call: int
    verdict: str
    reason: str
    row_gates: tuple[str, ...] = ()
    control_median_seconds: Optional[float] = None
    control_headroom: Optional[float] = None
    separated_at_width: Optional[int] = None
    widths: tuple[WidthReading, ...] = ()
    cells: tuple[CellReading, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "bee-hive-width-decision",
            "rung": "W1",
            "grain": self.grain,
            "items_per_call": self.items_per_call,
            "verdict": self.verdict,
            "reason": self.reason,
            "row_gates": list(self.row_gates),
            "control_median_seconds": (
                None
                if self.control_median_seconds is None
                else round(self.control_median_seconds, 4)
            ),
            "control_headroom_over_min_cell": (
                None if self.control_headroom is None else round(self.control_headroom, 2)
            ),
            "min_cell_seconds": round(MIN_CELL_SECONDS, 4),
            "separated_at_width": self.separated_at_width,
            "widths": [reading.to_dict() for reading in self.widths],
            "cells": [reading.to_dict() for reading in self.cells],
        }


def _row_gates(readings: Sequence[CellReading]) -> tuple[str, ...]:
    """§9 gate 5 plus the senses check, over the cells that survived 1-4.

    Computed over surviving cells on purpose: "a difference means the two cells
    did not do the same work" is a statement about cells that otherwise grade.
    A cell already VOID for truncation should not also void its neighbours.
    """
    survivors = [reading for reading in readings if not reading.gates]
    fired: list[str] = []
    if len({reading.prompt_tokens for reading in survivors}) > 1:
        fired.append(GATE_PROMPT_DRIFT)
    if len({reading.senses_config_hash for reading in survivors}) > 1:
        fired.append(GATE_SENSES_DRIFT)
    return tuple(fired)


def _control_median(readings: Sequence[CellReading]) -> Optional[float]:
    control = [
        reading.cell_wall_seconds
        for reading in readings
        if reading.width == 1 and reading.graded and reading.cell_wall_seconds > 0
    ]
    return statistics.median(control) if control else None


def _width_readings(readings: Sequence[CellReading]) -> tuple[WidthReading, ...]:
    """The paired direction rule, per width, against width 1."""
    control: dict[int, float] = {
        reading.repetition: float(reading.items_per_second or 0.0)
        for reading in readings
        if reading.width == 1 and reading.graded
    }
    out: list[WidthReading] = []
    for width in WIDTH_LADDER:
        cells = {
            reading.repetition: float(reading.items_per_second or 0.0)
            for reading in readings
            if reading.width == width and reading.graded
        }
        repetitions = tuple(sorted(cells))
        rates = tuple(cells[rep] for rep in repetitions)
        if width == 1:
            out.append(
                WidthReading(
                    width=width,
                    repetitions=repetitions,
                    items_per_second=rates,
                    paired=len(repetitions),
                    wins=0,
                    required=direction_required(len(repetitions)),
                    separated=False,
                )
            )
            continue
        paired = tuple(rep for rep in repetitions if rep in control)
        wins = sum(1 for rep in paired if cells[rep] > control[rep])
        required = direction_required(len(paired))
        out.append(
            WidthReading(
                width=width,
                repetitions=repetitions,
                items_per_second=rates,
                paired=len(paired),
                wins=wins,
                required=required,
                separated=bool(paired) and wins >= required,
            )
        )
    return tuple(out)


def grade_grain(grain: str, cells: Iterable[Mapping[str, Any]]) -> GrainDecision:
    """One grain row's verdict, by §9, applied without amendment."""
    readings = tuple(
        sorted((read_cell(cell) for cell in cells), key=lambda r: (r.width, r.repetition))
    )
    items_per_call = GRAIN_ITEMS[GRAIN_LADDER.index(grain)] if grain in GRAIN_LADDER else 0

    if not readings:
        return GrainDecision(
            grain=grain,
            items_per_call=items_per_call,
            verdict=ABSENT,
            reason="no cell in this grain row was dialled",
        )

    row = _row_gates(readings)
    if row:
        return GrainDecision(
            grain=grain,
            items_per_call=items_per_call,
            verdict=VOID,
            reason=f"row gate fired: {', '.join(row)} — every cell in the row is VOID",
            row_gates=row,
            cells=readings,
        )

    graded = [reading for reading in readings if reading.graded]
    if not graded:
        fired = sorted({gate for reading in readings for gate in reading.gates})
        return GrainDecision(
            grain=grain,
            items_per_call=items_per_call,
            verdict=VOID,
            reason=f"no cell in the row graded; gates fired: {', '.join(fired) or 'metric absent'}",
            cells=readings,
        )

    control_median = _control_median(readings)
    if control_median is None:
        return GrainDecision(
            grain=grain,
            items_per_call=items_per_call,
            verdict=VOID,
            reason="the width-1 control did not grade, so no width has anything to exceed",
            cells=readings,
        )

    headroom = control_median / MIN_CELL_SECONDS
    if control_median < MIN_CELL_SECONDS:
        return GrainDecision(
            grain=grain,
            items_per_call=items_per_call,
            verdict=CEILING_WIDTH,
            reason=(
                f"the control's own clock is {control_median:.4f} s, below the instrument's "
                f"resolution floor of {MIN_CELL_SECONDS:.4f} s"
            ),
            control_median_seconds=control_median,
            control_headroom=headroom,
            cells=readings,
        )

    widths = _width_readings(readings)
    winners = [reading.width for reading in widths if reading.separated]
    if winners:
        widest = max(winners)
        winner = next(reading for reading in widths if reading.width == widest)
        return GrainDecision(
            grain=grain,
            items_per_call=items_per_call,
            verdict=SEPARATED_WIDTH,
            reason=(
                f"width {widest} exceeded width 1 on {winner.wins} of {winner.paired} paired "
                f"repetitions (required {winner.required})"
            ),
            control_median_seconds=control_median,
            control_headroom=headroom,
            separated_at_width=widest,
            widths=widths,
            cells=readings,
        )

    above = [reading for reading in widths if reading.width > 1]
    best = max(above, key=lambda r: r.wins, default=None)
    detail = (
        f"best was width {best.width} at {best.wins} of {best.paired} (required {best.required})"
        if best is not None
        else "no width above the control graded"
    )
    return GrainDecision(
        grain=grain,
        items_per_call=items_per_call,
        verdict=INCONCLUSIVE_WIDTH,
        reason=f"the direction rule did not fire at any width; {detail}",
        control_median_seconds=control_median,
        control_headroom=headroom,
        widths=widths,
        cells=readings,
    )


def grade(cells: Iterable[Mapping[str, Any]]) -> tuple[GrainDecision, ...]:
    """Every grain in :data:`GRAIN_LADDER`, in registered ascending order."""
    rows: dict[str, list[Mapping[str, Any]]] = {grain: [] for grain in GRAIN_LADDER}
    for cell in cells:
        grain = str(cell.get("grain"))
        if grain in rows:
            rows[grain].append(cell)
    return tuple(grade_grain(grain, rows[grain]) for grain in GRAIN_LADDER)


# ── §10, the stop rule ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class StopDecision:
    """What §10 decided, and which rungs are ABSENT by name because of it."""

    decision: str
    reason: str
    absent_rungs: tuple[str, ...]
    published_answer: Optional[str]
    verdicts: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "bee-hive-width-stop",
            "rung": "W1",
            "decision": self.decision,
            "reason": self.reason,
            "absent_rungs": list(self.absent_rungs),
            "published_answer": self.published_answer,
            "verdicts": dict(self.verdicts),
        }


def stop_rule(decisions: Sequence[GrainDecision]) -> StopDecision:
    """§10 rules 2 and 3, applied by code.

    Rule 3 first (any ``SEPARATED-WIDTH`` continues), then rule 2 (every grain
    ``INCONCLUSIVE-WIDTH`` or ``VOID`` stops the whole ladder). A combination
    matching neither is reported :data:`STOP_UNREGISTERED` rather than resolved
    by judgement — §10 has no branch for it, and inventing one after the data
    is exactly what a pre-registration exists to prevent.
    """
    verdicts = {decision.grain: decision.verdict for decision in decisions}

    separated = sorted(grain for grain, verdict in verdicts.items() if verdict == SEPARATED_WIDTH)
    if separated:
        return StopDecision(
            decision=STOP_CONTINUE,
            reason=(
                "§10 rule 3: SEPARATED-WIDTH at "
                f"{', '.join(separated)} — the ladder continues to W2, then H0, then the sweep"
            ),
            absent_rungs=(),
            published_answer=None,
            verdicts=verdicts,
        )

    stopping = {INCONCLUSIVE_WIDTH, VOID}
    if verdicts and all(verdict in stopping for verdict in verdicts.values()):
        return StopDecision(
            decision=STOP_STOP,
            reason=("§10 rule 2: INCONCLUSIVE-WIDTH or VOID at every grain stops the whole ladder"),
            absent_rungs=LADDER_ABOVE_W1,
            published_answer=STOPPED_ANSWER,
            verdicts=verdicts,
        )

    unmatched = sorted(
        f"{grain}={verdict}" for grain, verdict in verdicts.items() if verdict not in stopping
    )
    return StopDecision(
        decision=STOP_UNREGISTERED,
        reason=(
            "§10 has no branch for this combination: no grain SEPARATED and not every grain is "
            f"INCONCLUSIVE-WIDTH or VOID ({', '.join(unmatched)}). Reported by name rather "
            "than resolved after the data."
        ),
        absent_rungs=LADDER_ABOVE_W1,
        published_answer=None,
        verdicts=verdicts,
    )


# ── the whole rung, decided ──────────────────────────────────────────────────


def decide(cells: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Grain decisions plus the stop rule, as one committable object."""
    decisions = grade(cells)
    stop = stop_rule(decisions)
    return {
        "kind": "bee-hive-width-verdict",
        "rung": "W1",
        "grains": [decision.to_dict() for decision in decisions],
        "stop": stop.to_dict(),
    }


def main(argv: Optional[list[str]] = None) -> int:
    """Decide the committed records and print the verdict as JSON."""
    del argv
    sys.path.insert(0, str(HERE))
    import clock  # noqa: E402  — sibling script, not a package

    verdict = decide(clock.load_cells())
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover — script entry
    raise SystemExit(main(sys.argv[1:]))
