#!/usr/bin/env python3
"""clock — the ``W1`` rung's stopwatch, read from committed records.

Plan task ``t10``. The pre-registration's
[§10](../bee-hive-width-preregistration.md#10-the-stop-rule) rule 7 names the
failure this file exists to prevent: rung ``C2`` of the previous ladder *"ran
3651.7 s past cap and no decision was written at all"*. A clock kept in an
agent's head is the same failure waiting to happen, so this one is kept
nowhere but in ``cells.jsonl``: every number below is recomputed from committed
cell records, and the file is the only input.

Two clocks, both published, and the difference between them is meaningful:

``dialled_seconds``
    The sum of every cell's own ``cell_wall_seconds``. What the rung spent
    *inside* dispatch, and the quantity
    [§8](../bee-hive-width-preregistration.md#8-cell-sizing-derived-from-measured-throughput)'s
    ``BLOCK_SECONDS_CEILING`` models.

``span_seconds``
    The operator's stopwatch: for each drive invocation (``run_id``), the
    distance from its first cell's ``started`` to its last cell's ``finished``,
    summed across invocations. Per-invocation rather than end-to-end so that
    stopping for the night and resuming does not charge the rung for the night.
    It includes planning, item construction and inter-cell overhead, so it is
    always at least ``dialled_seconds``.

**The cap is charged against ``span_seconds``**, the larger and more honest of
the two: a rung that is inside its cap only because the gaps between its cells
were not counted has not stayed inside its cap.

Nothing here dials anything. Run it::

    uv run python docs/live-test-results/bee-hive-width-raw/clock.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests import test_bee_hive_width_preregistration as pin  # noqa: E402

#: Both caps come from the pin, which imports them from
#: ``examples/league_h2h.py`` so a ladder and its clock cannot drift apart.
RUNG_CAP_SECONDS: float = pin.RUNG_CAP_SECONDS
LADDER_CAP_SECONDS: float = pin.LADDER_CAP_SECONDS

#: The registered conservative per-cell ceiling: every cell priced at the finest
#: grain and the width-1 rate. Used only when nothing comparable has been
#: measured yet — see :func:`estimate_seconds`.
CELL_SECONDS_CEILING: float = pin.CELL_SECONDS_CEILING

#: The record kind :mod:`drive` appends and this module reads.
KIND_CELL = "bee-hive-width-cell"

CELLS_PATH = HERE / "cells.jsonl"


def load_cells(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Every committed cell record, in file order. A missing file is ``[]``.

    A missing file is not an error: before the first cell lands there is
    nothing to read, and the clock's answer at that point ("0 s spent") is
    correct rather than absent.
    """
    resolved = CELLS_PATH if path is None else Path(path)
    if not resolved.exists():
        return []
    cells: list[dict[str, Any]] = []
    for line in resolved.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        if record.get("kind") == KIND_CELL:
            cells.append(record)
    return cells


def _moment(raw: Any) -> Optional[float]:
    """An ISO-8601 stamp as a POSIX timestamp, or ``None`` when unreadable."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except ValueError:
        return None


def span_seconds(cells: Sequence[dict[str, Any]]) -> float:
    """Wall clock across every drive invocation, summed per ``run_id``.

    Grouping by ``run_id`` rather than taking one global span is the whole
    point: two invocations an hour apart span an hour, and the rung did not
    spend that hour.
    """
    by_run: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        by_run.setdefault(str(cell.get("run_id") or ""), []).append(cell)
    total = 0.0
    for group in by_run.values():
        starts = [moment for moment in (_moment(c.get("started")) for c in group) if moment]
        ends = [moment for moment in (_moment(c.get("finished")) for c in group) if moment]
        if starts and ends:
            total += max(0.0, max(ends) - min(starts))
    return total


def dialled_seconds(cells: Sequence[dict[str, Any]]) -> float:
    """The sum of every cell's own dispatch clock."""
    return float(sum(float(cell.get("cell_wall_seconds") or 0.0) for cell in cells))


def estimate_seconds(cells: Sequence[dict[str, Any]], *, width: int, grain: str) -> float:
    """What the next cell at ``(width, grain)`` should be budgeted at.

    The **largest** clock already measured for that exact cell, or — when none
    has been — the registered :data:`CELL_SECONDS_CEILING`. Largest rather than
    mean so that a cell is never started on an estimate half its neighbours
    have already exceeded, and registered-ceiling rather than a guess so the
    first block is costed at the number §8 committed to.
    """
    seen = [
        float(cell.get("cell_wall_seconds") or 0.0)
        for cell in cells
        if int(cell.get("width") or 0) == int(width) and str(cell.get("grain")) == str(grain)
    ]
    return max(seen) if seen else CELL_SECONDS_CEILING


@dataclass(frozen=True)
class RungClock:
    """One reading of the rung's two clocks against both caps."""

    cells: int
    dialled_seconds: float
    span_seconds: float
    rung_cap_seconds: float
    ladder_cap_seconds: float

    @property
    def charged_seconds(self) -> float:
        """The clock the caps are charged against — see the module docstring."""
        return self.span_seconds

    @property
    def rung_remaining(self) -> float:
        return self.rung_cap_seconds - self.charged_seconds

    @property
    def ladder_remaining(self) -> float:
        return self.ladder_cap_seconds - self.charged_seconds

    @property
    def over_rung_cap(self) -> bool:
        return self.charged_seconds > self.rung_cap_seconds

    @property
    def over_ladder_cap(self) -> bool:
        return self.charged_seconds > self.ladder_cap_seconds

    def affords(self, estimate: float) -> bool:
        """Is there room for one more cell of ``estimate`` seconds?

        Checked against **both** caps: the rung cap binds first at these
        numbers, but a stop rule that only ever looked at one of them would be
        the same omission §10 rule 7 was written about.
        """
        after = self.charged_seconds + max(0.0, float(estimate))
        return after <= self.rung_cap_seconds and after <= self.ladder_cap_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "bee-hive-width-clock",
            "cells": self.cells,
            "dialled_seconds": round(self.dialled_seconds, 3),
            "span_seconds": round(self.span_seconds, 3),
            "charged_seconds": round(self.charged_seconds, 3),
            "rung_cap_seconds": self.rung_cap_seconds,
            "ladder_cap_seconds": self.ladder_cap_seconds,
            "rung_remaining_seconds": round(self.rung_remaining, 3),
            "ladder_remaining_seconds": round(self.ladder_remaining, 3),
            "over_rung_cap": self.over_rung_cap,
            "over_ladder_cap": self.over_ladder_cap,
        }


def read_clock(cells: Optional[Iterable[dict[str, Any]]] = None) -> RungClock:
    """The clock, from committed records only."""
    rows = list(load_cells()) if cells is None else list(cells)
    return RungClock(
        cells=len(rows),
        dialled_seconds=dialled_seconds(rows),
        span_seconds=span_seconds(rows),
        rung_cap_seconds=RUNG_CAP_SECONDS,
        ladder_cap_seconds=LADDER_CAP_SECONDS,
    )


def main(argv: Optional[list[str]] = None) -> int:
    """Print the clock as JSON. Exit ``1`` when either cap has been passed."""
    del argv
    clock = read_clock()
    print(json.dumps(clock.to_dict(), indent=2))
    return 1 if (clock.over_rung_cap or clock.over_ladder_cap) else 0


if __name__ == "__main__":  # pragma: no cover — script entry
    raise SystemExit(main(sys.argv[1:]))
