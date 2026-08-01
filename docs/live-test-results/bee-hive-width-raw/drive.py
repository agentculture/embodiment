#!/usr/bin/env python3
"""drive — dial the ``W1`` width rung, one cell at a time, and commit each record.

Plan task ``t10``, executing
[`bee-hive-width-preregistration.md`](../bee-hive-width-preregistration.md)'s
rung ``W1``: **arm ``B1``'s scoped lane only**, varying width and grain inside
one arm. It dials no cortex and holds no drive — §5: *"``W1`` runs no drive, and
that is why it is not bound by ``max_scoped_calls``"*. It calls
:func:`examples.arch_hive.plan_calls` directly with a budget of
``ITEMS_PER_CELL / items_per_call``.

One cell is ``ITEMS_PER_CELL`` = 320 items at one (width, grain), dispatched as
``320 / items_per_call / width`` consecutive waves. A **wave is a batch** in
t8's sense — the unit gate 1 drops whole — so its own wall clock is recorded
beside it.

Three things this file does that the shipped harness does not do for it, each
because the pre-registration requires it:

1. **The thinking wire key goes on the wire and is asserted there.** §6: *"Every
   scoped worker call in this series puts ``{"chat_template_kwargs":
   {"enable_thinking": false}}`` on the wire, and the run asserts it on the
   wire rather than assuming it."* :func:`examples.arch_hive.build_worker_factory`
   builds a bare :class:`examples.worker_seam.WorkerSeam`, which has no seam for
   extra wire keys — so this file subclasses it exactly as
   ``examples/worker_scoped_overhead.py`` already does, and asserts
   ``last_body``. Every other construction argument is copied from
   ``build_worker_factory``, ``stream=False`` included and for its reason: **this
   lane's clock is the rung's outcome metric.**
2. **Per-batch retry counts are recorded.**
   :class:`examples.arch_hive.ScopedResult` carries no ``retries`` field, so the
   seam that made each call is kept and its ``meter.retries`` read after the
   wave lands. Without it §9 gate 1 cannot be applied at all.
3. **The item set is fixed before any width is dialled**, so a width can only
   move the clock (§5). Items are laid out in 40 blocks of 8, block ``b``
   carrying question ``QUESTION_ORDER[b % 3]``; every registered grain
   (1, 2, 4, 8 items per call) divides 8, so no chunk ever straddles two
   questions and the call set is byte-identical at every width.

Usage::

    export EMBODIMENT_LIVE_RIG=1 COLLEAGUE_API_KEY=...
    uv run python docs/live-test-results/bee-hive-width-raw/drive.py --plan
    uv run python docs/live-test-results/bee-hive-width-raw/drive.py --probe
    uv run python docs/live-test-results/bee-hive-width-raw/drive.py --block 0
    uv run python docs/live-test-results/bee-hive-width-raw/drive.py \\
        --grain batch8 --width 8 --repetition 0
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(HERE))

import clock as rung_clock  # noqa: E402  — sibling script, not a package
from examples import arch_arms as aa  # noqa: E402
from examples import arch_hive as ah  # noqa: E402
from examples import worker_seam as ws  # noqa: E402
from tests import test_bee_hive_width_preregistration as pin  # noqa: E402

# ── the registered constants, imported ───────────────────────────────────────

WIDTH_LADDER: tuple[int, ...] = pin.WIDTH_LADDER
GRAIN_LADDER: tuple[str, ...] = pin.GRAIN_LADDER
GRAIN_ITEMS: tuple[int, ...] = pin.GRAIN_ITEMS
ITEMS_PER_CELL: int = pin.ITEMS_PER_CELL
REPETITIONS_PER_CELL: int = pin.REPETITIONS_PER_CELL
REPETITIONS_RESERVE: int = pin.REPETITIONS_RESERVE
MAX_ITEMS_PER_CALL: int = pin.MAX_ITEMS_PER_CALL

TIER = ah.TIER_SCOPED  # B1 — the arm under test, and the only arm W1 dials

CELLS_PATH = rung_clock.CELLS_PATH
RUNS_PATH = HERE / "runs.jsonl"

KIND_RUN = "bee-hive-width-run"
KIND_PROBE = "bee-hive-width-probe"

#: §6's baseline, quoted so the idle probe has something to be read against.
#: Measured non-streaming on this exact path by t8; **not** a bar this run must
#: hit, only the number a contended rig would visibly miss.
CALLS_PER_SECOND_W1: float = pin.CALLS_PER_SECOND_W1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the item set: fixed once, identical at every width ───────────────────────

#: 40 blocks of ``MAX_ITEMS_PER_CALL`` items, so every registered grain divides
#: a block and no scoped call ever straddles two questions.
BLOCKS = ITEMS_PER_CELL // MAX_ITEMS_PER_CALL


def cell_items() -> tuple[ah.HiveItem, ...]:
    """The rung's 320 items. Deterministic, harness-authored, width-invariant.

    Shaped like ``arch_hive.demo_items`` — a unit, two fact lines, a short menu
    for the slots question — but laid out in question-pure blocks of 8 and
    carrying **no ``truth``**: ``W1`` grades a clock, never an answer, and an
    artifact that carried a truth would invite someone to grade one.
    """
    items: list[ah.HiveItem] = []
    for block in range(BLOCKS):
        question = ah.QUESTION_ORDER[block % len(ah.QUESTION_ORDER)]
        declared = ah.HIVE_QUESTIONS[question]
        options = ah._DEMO_OPTIONS if declared.kind == ah.SPACE_SLOTS else ()
        for offset in range(MAX_ITEMS_PER_CALL):
            index = block * MAX_ITEMS_PER_CALL + offset
            items.append(
                ah.HiveItem(
                    id=f"u{index + 1}",
                    question=question,
                    facts=(
                        f"unit {index + 1} is idle at t={index * 3}",
                        f"it is holding {index % 3} resource(s)",
                    ),
                    options=options,
                )
            )
    return tuple(items)


def plan_cell(grain: ah.ScopeGrain) -> tuple[ah.ScopedCall, ...]:
    """One cell's whole call set, at the declared grain. **Width plays no part.**

    ``plan_calls`` is called once per question over that question's own items,
    with a budget of exactly that question's chunk count — so the clamp cannot
    fire and §9's ``plan-clamped`` gate is a check on this function rather than
    a thing it risks. The three lists are then interleaved round-robin so no
    wave is made entirely of the longest prompt.
    """
    items = cell_items()
    per_call = grain.items_per_call
    lists: list[tuple[ah.ScopedCall, ...]] = []
    for index, question in enumerate(ah.QUESTION_ORDER, start=1):
        mine = [item for item in items if item.question == question]
        calls, refused = ah.plan_calls(
            mine,
            question=question,
            grain=grain,
            budget=len(mine) // per_call,
            stem=f"q{index}",
        )
        if refused:
            raise RuntimeError(
                f"plan_calls refused {len(refused)} items at grain {grain.id!r}; the item "
                "layout and the budget disagree, and a clamped plan is a VOID cell"
            )
        lists.append(calls)
    interleaved = [call for group in zip_longest(*lists) for call in group if call is not None]
    return tuple(interleaved)


# ── the seam: build_worker_factory's construction, plus the thinking wire ────


class WireSeam(ws.WorkerSeam):
    """``WorkerSeam`` with extra wire keys, merged inside :meth:`_post`.

    A verbatim reuse of ``examples/worker_scoped_overhead.py``'s ``_WireSeam``,
    for its stated reason: merging inside ``_post`` puts the keys on the
    *existing* retry path rather than a parallel one, and ``last_body`` lets a
    run assert what went on the wire instead of trusting a branch.
    """

    def __init__(self, *args: Any, wire_extra: Optional[Mapping[str, Any]] = None, **kw: Any):
        super().__init__(*args, **kw)
        self.wire_extra: dict[str, Any] = dict(wire_extra or {})
        self.last_body: Optional[dict[str, Any]] = None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        merged = {**body, **self.wire_extra}
        self.last_body = merged
        return super()._post(merged)


def build_factory(
    *,
    dial: aa.Dial,
    sampling: aa.Sampling,
    wire_extra: Mapping[str, Any],
) -> Callable[[ah.ScopedCall], WireSeam]:
    """One fresh seam per scoped call — decision ``c42``, as in ``arch_hive``.

    Every keyword matches :func:`examples.arch_hive.build_worker_factory`,
    ``stream=False`` included. That is not a style choice: **this lane's clock
    is the width rung's outcome metric** and every baseline it is sized against
    was measured non-streaming on this exact path
    ([§2](../bee-hive-width-preregistration.md#2-the-instrument-this-series-runs-on-streaming)).
    ``tools`` is never passed — there is nothing to pass.
    """

    def factory(call: ah.ScopedCall) -> WireSeam:
        return WireSeam(
            base_url=dial.base_url,
            model=dial.model,
            api_key=dial.api_key,
            role=f"hive-{call.question}",
            max_tokens=sampling.max_tokens,
            temperature=sampling.temperature,
            stream=False,
            wire_extra=wire_extra,
        )

    return factory


# ── one cell ─────────────────────────────────────────────────────────────────


def dial_cell(
    *,
    config: ah.HiveConfig,
    dial: aa.Dial,
    grain_name: str,
    width: int,
    repetition: int,
    run_id: str,
    senses_hash: str,
) -> dict[str, Any]:
    """Dial one cell and return its record. Never raises on a model's behalf.

    Wave by wave: ``width`` calls dispatched concurrently, its wall clock
    recorded, its seams' ``meter.retries`` summed, then the next wave. Waves are
    consecutive rather than one big dispatch so that gate 1 has a batch to drop
    and so §9's ``batch_elapsed_seconds`` exists at all.
    """
    grain = config.grain(grain_name)
    sampling = config.sampling_for(TIER, ah.ROLE_WORKER)
    wire_extra = config.wire_extra(sampling.thinking)
    factory = build_factory(dial=dial, sampling=sampling, wire_extra=wire_extra)

    calls = plan_cell(grain)
    waves = [calls[start : start + width] for start in range(0, len(calls), width)]

    seams: dict[str, WireSeam] = {}

    def answer(call: ah.ScopedCall) -> ah.ScopedResult:
        seam = factory(call)
        seams[call.id] = seam
        return ah.answer_by_worker(call, mind=seam)

    ledger = ah.AcceptanceLedger()
    batch_elapsed: list[float] = []
    batch_retries: list[int] = []
    started = _now()
    cell_started = time.perf_counter()
    for wave in waves:
        wave_started = time.perf_counter()
        results = ah.dispatch(
            wave,
            answer_fn=answer,
            max_workers=width,
            timeout=config.batch_timeout_seconds,
        )
        batch_elapsed.append(time.perf_counter() - wave_started)
        ledger.extend(results)
        batch_retries.append(sum(seams[call.id].meter.retries for call in wave if call.id in seams))
    cell_wall = time.perf_counter() - cell_started
    finished = _now()

    # §6: assert the thinking key ON THE WIRE, never assume it.
    bodies = [seam.last_body for seam in seams.values() if seam.last_body is not None]
    on_the_wire = [body.get("chat_template_kwargs") for body in bodies]
    wire_ok = bool(on_the_wire) and all(
        sent == wire_extra.get("chat_template_kwargs") for sent in on_the_wire
    )

    finish_reasons: dict[str, int] = {}
    truncated = 0
    for seam in seams.values():
        for reason, count in seam.meter.finish_reasons.items():
            finish_reasons[reason] = finish_reasons.get(reason, 0) + count
        truncated += seam.meter.truncated

    acceptance = ledger.to_dict()
    acceptance.pop("calls", None)  # the per-call detail rides in calls.jsonl
    return {
        "kind": rung_clock.KIND_CELL,
        "run_id": run_id,
        "rung": "W1",
        "arm": TIER,
        "width": width,
        "grain": grain_name,
        "items_per_call": grain.items_per_call,
        "repetition": repetition,
        "started": started,
        "finished": finished,
        "cell_wall_seconds": round(cell_wall, 4),
        "batch_elapsed_seconds": [round(value, 4) for value in batch_elapsed],
        "batch_retries": batch_retries,
        "retries": sum(batch_retries),
        "items_per_cell": ITEMS_PER_CELL,
        "calls_planned": len(calls),
        "batches": len(waves),
        "acceptance": acceptance,
        "finish_reasons": finish_reasons,
        "truncated_turns": truncated,
        "senses_config_hash": senses_hash,
        "worker_model": dial.model,
        "worker_base_url": dial.base_url,
        "sampling": sampling.to_dict(),
        "thinking_wire": dict(wire_extra),
        "thinking_wire_asserted": wire_ok,
        "transport": ws.TRANSPORT_BLOCKING,
        "batch_timeout_seconds": config.batch_timeout_seconds,
    }, [result.to_dict() for result in ledger.results]


# ── appending, one line at a time ────────────────────────────────────────────


def append(path: Path, record: Mapping[str, Any]) -> None:
    """One JSON object, one line, flushed. A killed run keeps what it measured."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=False) + "\n")
        handle.flush()


def already_dialled(
    cells: Sequence[Mapping[str, Any]], *, grain: str, width: int, rep: int
) -> bool:
    return any(
        str(cell.get("grain")) == grain
        and int(cell.get("width") or 0) == width
        and int(cell.get("repetition") or 0) == rep
        for cell in cells
    )


# ── the idle probe ───────────────────────────────────────────────────────────


def idle_probe(*, dial: aa.Dial, config: ah.HiveConfig, calls: int = 5) -> dict[str, Any]:
    """Sequential minimum-size scoped calls, to see whether the rig is busy.

    Not a gate and not a bar: it records what the lane looked like immediately
    before (and after) a block, so a cell dialled against a contended Thor can
    be identified rather than published as a width effect.
    """
    sampling = config.sampling_for(TIER, ah.ROLE_WORKER)
    wire_extra = config.wire_extra(sampling.thinking)
    factory = build_factory(dial=dial, sampling=sampling, wire_extra=wire_extra)
    grain = config.grain(GRAIN_LADDER[0])
    probe_call = plan_cell(grain)[0]

    latencies: list[float] = []
    retries = 0
    failures = 0
    for _ in range(calls):
        seam = factory(probe_call)
        started = time.perf_counter()
        try:
            ah.answer_by_worker(probe_call, mind=seam)
        finally:
            latencies.append(time.perf_counter() - started)
            retries += seam.meter.retries
            failures += seam.meter.failures
    ordered = sorted(latencies)
    median = ordered[len(ordered) // 2] if ordered else 0.0
    return {
        "kind": KIND_PROBE,
        "at": _now(),
        "calls": calls,
        "latency_seconds": [round(value, 4) for value in latencies],
        "median_seconds": round(median, 4),
        "calls_per_second_serial": round(1.0 / median, 4) if median > 0 else None,
        "reference_calls_per_second_w1": CALLS_PER_SECOND_W1,
        "retries": retries,
        "failures": failures,
        "worker_model": dial.model,
        "worker_base_url": dial.base_url,
    }


# ── the registered order ─────────────────────────────────────────────────────


def block_order() -> tuple[tuple[str, int], ...]:
    """§10 rule 1: grains ascend, and within each grain widths ascend from 1."""
    return tuple((grain, width) for grain in GRAIN_LADDER for width in WIDTH_LADDER)


# ── entry ────────────────────────────────────────────────────────────────────


def _resolve(config: ah.HiveConfig) -> aa.Dial:
    resolution = aa.resolve_dial(config, ah.ROLE_WORKER)
    if resolution.dial is None:
        detail = "; ".join(record.detail for record in resolution.degradations)
        raise SystemExit(f"error: the worker dial is ABSENT and will not be substituted: {detail}")
    return resolution.dial


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", action="store_true", help="print the cell plan and dial nothing")
    parser.add_argument("--probe", action="store_true", help="run the idle probe and record it")
    parser.add_argument("--block", type=int, default=None, help="dial one whole repetition block")
    parser.add_argument("--grain", choices=list(GRAIN_LADDER), default=None)
    parser.add_argument("--width", type=int, choices=list(WIDTH_LADDER), default=None)
    parser.add_argument("--repetition", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="re-dial a cell already recorded")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = ah.load_hive_config()
    senses_hash = ah.assert_senses_identical(config)

    if args.plan:
        for grain_name in GRAIN_LADDER:
            grain = config.grain(grain_name)
            calls = plan_cell(grain)
            items = sum(len(call.item_ids) for call in calls)
            print(
                f"{grain_name:>7}  items_per_call={grain.items_per_call}  calls={len(calls)}  "
                f"items={items}  waves={{"
                + ", ".join(f"w{width}: {len(calls) // width}" for width in WIDTH_LADDER)
                + "}"
            )
        return 0

    aa.require_live_rig()
    dial = _resolve(config)
    run_id = f"w1-{uuid.uuid4().hex[:8]}"

    if args.probe:
        record = idle_probe(dial=dial, config=config)
        append(RUNS_PATH, record)
        print(json.dumps(record, indent=2))
        return 0

    if args.block is None and (args.grain is None or args.width is None):
        print("error: pass --block N, or --grain G --width W --repetition R", file=sys.stderr)
        return 1

    targets: list[tuple[str, int, int]]
    if args.block is not None:
        targets = [(grain, width, args.block) for grain, width in block_order()]
    else:
        repetition = 0 if args.repetition is None else args.repetition
        targets = [(args.grain, int(args.width), repetition)]

    before = idle_probe(dial=dial, config=config)
    append(RUNS_PATH, {**before, "phase": "before", "run_id": run_id})
    print(json.dumps(before), file=sys.stderr)

    dialled = 0
    for grain_name, width, repetition in targets:
        cells = rung_clock.load_cells()
        if not args.force and already_dialled(cells, grain=grain_name, width=width, rep=repetition):
            print(f"skip {grain_name} w{width} r{repetition}: already recorded", file=sys.stderr)
            continue
        reading = rung_clock.read_clock(cells)
        estimate = rung_clock.estimate_seconds(cells, width=width, grain=grain_name)
        if not reading.affords(estimate):
            print(
                f"stop: {grain_name} w{width} r{repetition} would run past a cap "
                f"(charged {reading.charged_seconds:.1f}s + estimate {estimate:.1f}s vs rung cap "
                f"{reading.rung_cap_seconds:.1f}s); everything above is ABSENT by name",
                file=sys.stderr,
            )
            break
        record, calls = dial_cell(
            config=config,
            dial=dial,
            grain_name=grain_name,
            width=width,
            repetition=repetition,
            run_id=run_id,
            senses_hash=senses_hash,
        )
        append(CELLS_PATH, record)
        append(
            HERE / "calls.jsonl",
            {
                "kind": "bee-hive-width-calls",
                "run_id": run_id,
                "grain": grain_name,
                "width": width,
                "repetition": repetition,
                "calls": calls,
            },
        )
        dialled += 1
        print(
            f"{grain_name} w{width} r{repetition}: {record['cell_wall_seconds']}s  "
            f"{ITEMS_PER_CELL / max(record['cell_wall_seconds'], 1e-9):.3f} items/s  "
            f"retries={record['retries']}  counts={record['acceptance']['counts']}",
            file=sys.stderr,
        )

    after = idle_probe(dial=dial, config=config)
    append(RUNS_PATH, {**after, "phase": "after", "run_id": run_id, "cells_dialled": dialled})
    print(json.dumps(after), file=sys.stderr)
    print(json.dumps(rung_clock.read_clock().to_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover — script entry
    raise SystemExit(main(sys.argv[1:]))
