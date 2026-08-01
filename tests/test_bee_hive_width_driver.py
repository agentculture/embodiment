"""The ``W1`` driver's decision rule and clock, proved against **synthetic** states.

Plan task ``t10``. The previous cycle proved its ladder against eight fabricated
states before it dialled anything; this does the same, for the same reason: a
decision rule first exercised by real data is a decision rule whose branches
were chosen after seeing the data.

Every state below is constructed, not measured. The rules under test are
transcribed in ``docs/live-test-results/bee-hive-width-raw/decide.py`` from
``docs/live-test-results/bee-hive-width-preregistration.md`` §9 and §10, and
every constant they use is imported from
``tests/test_bee_hive_width_preregistration.py`` — the pin.

Nothing here dials a model, opens a socket, or reads rig state.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Sequence

import pytest

from examples import arch_arms as aa
from examples import arch_hive as ah
from examples import worker_seam as ws

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW = REPO_ROOT / "docs" / "live-test-results" / "bee-hive-width-raw"

sys.path.insert(0, str(REPO_ROOT))


def _load(name: str) -> ModuleType:
    """Import a sibling script from ``bee-hive-width-raw/`` by path.

    The directory is deliberately not a package: it holds one series' run
    scripts beside that series' records, and adding an ``__init__.py`` would
    make it importable from anywhere and invite exactly that.
    """
    path = RAW / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"bee_hive_width_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


clock = _load("clock")
decide = _load("decide")
drive = _load("drive")

ITEMS_PER_CELL = decide.ITEMS_PER_CELL
GRAIN_LADDER = decide.GRAIN_LADDER
GRAIN_ITEMS = decide.GRAIN_ITEMS
WIDTH_LADDER = decide.WIDTH_LADDER

HASH_A = "a" * 64
HASH_B = "b" * 64


# ── fabricated states ────────────────────────────────────────────────────────


def cell(
    *,
    grain: str = "batch8",
    width: int = 1,
    repetition: int = 0,
    seconds: float = 100.0,
    retries: Optional[Sequence[int]] = None,
    dispatched: Optional[int] = None,
    counts: Optional[dict[str, int]] = None,
    prompt_tokens: int = 96000,
    completion_tokens: int = 5000,
    senses_config_hash: str = HASH_A,
    run_id: str = "run-1",
    started: str = "2026-08-01T10:00:00+00:00",
    finished: str = "2026-08-01T10:01:40+00:00",
) -> dict[str, Any]:
    """One fabricated cell record, shaped exactly as ``drive.py`` appends it.

    Defaults describe a clean, graded cell: no retries, every call accepted,
    and a plan that dispatched exactly ``ITEMS_PER_CELL`` items.
    """
    items_per_call = GRAIN_ITEMS[GRAIN_LADDER.index(grain)]
    planned = ITEMS_PER_CELL // items_per_call
    calls = planned if dispatched is None else dispatched
    batches = max(1, planned // width)
    elapsed = [seconds / batches] * batches
    batch_retries = list(retries) if retries is not None else [0] * batches
    tally = counts if counts is not None else {decide.ACCEPTED: calls}
    return {
        "kind": clock.KIND_CELL,
        "run_id": run_id,
        "rung": "W1",
        "arm": "B1",
        "width": width,
        "grain": grain,
        "items_per_call": items_per_call,
        "repetition": repetition,
        "started": started,
        "finished": finished,
        "cell_wall_seconds": seconds,
        "batch_elapsed_seconds": elapsed,
        "batch_retries": batch_retries,
        "retries": sum(batch_retries),
        "acceptance": {
            "dispatched": calls,
            "counts": tally,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
        "senses_config_hash": senses_config_hash,
    }


def row(grain: str, rates: dict[int, Sequence[float]], **kw: Any) -> list[dict[str, Any]]:
    """A whole grain row from ``{width: [seconds per repetition]}``."""
    return [
        cell(grain=grain, width=width, repetition=index, seconds=seconds, **kw)
        for width, series in rates.items()
        for index, seconds in enumerate(series)
    ]


#: The separated shape: every width above 1 finishes the same 320 items faster,
#: in all three repetitions.
SEPARATED_ROW = {1: [200.0, 205.0, 198.0], 2: [110.0, 108.0, 106.0], 8: [40.0, 41.0, 39.0]}

#: The inconclusive shape: width 8 wins twice and loses once, so at n = 3 —
#: where ``direction_required(3)`` is 3 — the direction rule does not fire.
INCONCLUSIVE_ROW = {1: [200.0, 205.0, 198.0], 8: [190.0, 195.0, 210.0]}


# ── the metric itself ────────────────────────────────────────────────────────


class TestTheOutcomeMetric:
    def test_items_per_second_is_items_over_the_cell_clock(self) -> None:
        record = cell(seconds=160.0)
        assert decide.items_per_second(record, clean=False) == pytest.approx(320 / 160.0)
        assert decide.items_per_second(record, clean=True) == pytest.approx(320 / 160.0)

    def test_acceptance_is_never_in_the_numerator(self) -> None:
        """A cell that refused a third of its calls still reports the same rate.

        §4's whole lesson from ``league-h2h``: an outcome metric that folds in
        an interface-compliance figure measures compliance, not the thing.
        """
        refusing = cell(
            seconds=160.0,
            counts={decide.ACCEPTED: 27, "refused-off-space": 13},
        )
        assert decide.items_per_second(refusing) == pytest.approx(320 / 160.0)

    def test_the_metric_lands_for_every_declining_shape(self) -> None:
        for outcome in ("refused-off-space", "refused-empty", "absent-timeout"):
            record = cell(seconds=80.0, counts={outcome: 40})
            assert decide.items_per_second(record) is not None

    def test_a_cell_with_no_clock_has_no_rate(self) -> None:
        assert decide.items_per_second(cell(seconds=0.0), clean=False) is None


# ── §9, gate by gate ─────────────────────────────────────────────────────────


class TestTheValidityGates:
    def test_a_clean_cell_fires_no_gate(self) -> None:
        assert decide.cell_gates(cell()) == ()

    def test_gate_1_drops_retry_contaminated_batches_whole(self) -> None:
        """One retry contaminates its batch's clock, so the batch goes whole."""
        record = cell(grain="batch8", width=8, seconds=100.0, retries=[0, 0, 20, 0, 0])
        kept, seconds = decide.clean_batches(record)
        assert kept == 4
        assert seconds == pytest.approx(80.0)
        # 4 clean batches is exactly MIN_BATCHES_PER_CELL - 1, so it survives.
        assert decide.cell_gates(record) == ()
        assert decide.items_per_second(record) == pytest.approx((4 * 8 * 8) / 80.0)

    def test_gate_1_voids_a_cell_left_with_too_few_clean_batches(self) -> None:
        record = cell(grain="batch8", width=8, seconds=100.0, retries=[0, 1, 1, 0, 0])
        assert decide.clean_batches(record)[0] == 3
        assert decide.GATE_RETRY in decide.cell_gates(record)

    def test_gate_2_voids_any_cell_with_a_truncated_call(self) -> None:
        record = cell(counts={decide.ACCEPTED: 39, decide.ABSENT_TRUNCATED: 1})
        assert decide.GATE_TRUNCATED in decide.cell_gates(record)

    def test_gate_3_voids_a_cell_over_the_protocol_gate(self) -> None:
        # 5 refusals in 40 truncation-free calls = 0.125 > PROTOCOL_GATE 0.10.
        record = cell(counts={decide.ACCEPTED: 35, "refused-off-space": 5})
        assert decide.refusal_fraction(record) == pytest.approx(0.125)
        assert decide.GATE_REFUSAL in decide.cell_gates(record)

    def test_gate_3_is_not_tripped_exactly_at_the_gate(self) -> None:
        """The rule is *exceeds*, so 0.10 itself passes."""
        record = cell(counts={decide.ACCEPTED: 36, "refused-empty": 4})
        assert decide.refusal_fraction(record) == pytest.approx(0.10)
        assert decide.GATE_REFUSAL not in decide.cell_gates(record)

    def test_gate_3s_denominator_excludes_every_absence(self) -> None:
        """An instrument event must not be able to inflate a refusal rate.

        20 absences, 20 live calls, 2 of them refused: 0.10 on the registered
        truncation-free denominator, and a misleading 0.05 on ``dispatched``.
        """
        record = cell(
            counts={
                decide.ACCEPTED: 18,
                "refused-off-space": 2,
                "absent-timeout": 15,
                "absent-transport": 5,
            }
        )
        assert decide.refusal_fraction(record) == pytest.approx(0.10)
        assert decide.GATE_REFUSAL not in decide.cell_gates(record)

    def test_gate_3_has_no_fraction_when_every_call_was_an_absence(self) -> None:
        record = cell(counts={"absent-transport": 40})
        assert decide.refusal_fraction(record) is None
        assert decide.GATE_REFUSAL not in decide.cell_gates(record)

    def test_gate_4_voids_a_clamped_plan(self) -> None:
        record = cell(grain="batch8", dispatched=39)
        assert decide.GATE_PLAN_CLAMPED in decide.cell_gates(record)

    def test_gate_5_voids_the_whole_row_on_a_prompt_token_difference(self) -> None:
        cells = row("batch8", SEPARATED_ROW)
        cells[-1]["acceptance"]["prompt_tokens"] += 1
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict == decide.VOID
        assert decision.row_gates == (decide.GATE_PROMPT_DRIFT,)

    def test_a_senses_hash_mismatch_voids_the_whole_row(self) -> None:
        cells = row("batch8", SEPARATED_ROW)
        cells[2]["senses_config_hash"] = HASH_B
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict == decide.VOID
        assert decision.row_gates == (decide.GATE_SENSES_DRIFT,)

    def test_an_already_void_cell_does_not_drag_its_row_down(self) -> None:
        """Gate 5 compares cells that otherwise grade — see ``_row_gates``."""
        cells = row("batch8", SEPARATED_ROW)
        cells[-1]["acceptance"]["counts"] = {decide.ACCEPTED: 39, decide.ABSENT_TRUNCATED: 1}
        cells[-1]["acceptance"]["prompt_tokens"] += 5000
        decision = decide.grade_grain("batch8", cells)
        assert decision.row_gates == ()
        assert decision.verdict == decide.SEPARATED_WIDTH

    def test_a_gated_cell_stays_in_the_artifact(self) -> None:
        """§9: a gated cell is excluded from the verdict **only**."""
        cells = row("batch8", SEPARATED_ROW)
        cells[-1]["acceptance"]["counts"] = {decide.ACCEPTED: 39, decide.ABSENT_TRUNCATED: 1}
        decision = decide.grade_grain("batch8", cells)
        assert len(decision.cells) == len(cells)
        gated = [reading for reading in decision.cells if reading.gates]
        assert len(gated) == 1
        assert gated[0].items_per_second is not None


# ── §9, the per-grain verdicts ───────────────────────────────────────────────


class TestTheGrainVerdicts:
    def test_a_separated_grain(self) -> None:
        decision = decide.grade_grain("batch8", row("batch8", SEPARATED_ROW))
        assert decision.verdict == decide.SEPARATED_WIDTH
        assert decision.separated_at_width == 8

    def test_separation_reports_the_widest_width_that_qualifies(self) -> None:
        """Both 2 and 8 qualify; §9 reports the widest."""
        cells = row("batch8", {1: [200.0] * 3, 2: [150.0] * 3, 8: [100.0] * 3})
        decision = decide.grade_grain("batch8", cells)
        assert decision.separated_at_width == 8
        assert [reading.separated for reading in decision.widths] == [False, True, False, True]

    def test_an_inconclusive_grain_is_published_as_inconclusive(self) -> None:
        decision = decide.grade_grain("batch8", row("batch8", INCONCLUSIVE_ROW))
        assert decision.verdict == decide.INCONCLUSIVE_WIDTH
        assert decision.separated_at_width is None

    def test_two_of_three_is_not_separation_at_n_equals_three(self) -> None:
        """``direction_required(3)`` is 3: a 2-of-3 trend is INCONCLUSIVE."""
        assert decide.direction_required(3) == 3
        cells = row("batch8", {1: [200.0, 200.0, 200.0], 8: [100.0, 100.0, 300.0]})
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict == decide.INCONCLUSIVE_WIDTH
        width8 = next(r for r in decision.widths if r.width == 8)
        assert (width8.wins, width8.paired, width8.required) == (2, 3, 3)

    def test_a_ceiling_grain(self) -> None:
        """A control clock below the instrument's resolution is CEILING."""
        cells = row("batch8", {1: [0.3, 0.3, 0.3], 8: [0.1, 0.1, 0.1]})
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict == decide.CEILING_WIDTH
        assert decision.control_headroom is not None
        assert decision.control_headroom < 1.0

    def test_the_registered_cell_size_puts_the_ceiling_out_of_reach(self) -> None:
        """§9's own arithmetic: the shortest control cell is 47x the floor."""
        cells = row("batch8", {1: [21.1] * 3, 8: [21.0] * 3})
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict != decide.CEILING_WIDTH
        assert decision.control_headroom == pytest.approx(21.1 / decide.MIN_CELL_SECONDS, rel=1e-3)

    def test_a_row_whose_control_is_void_is_void(self) -> None:
        cells = row("batch8", SEPARATED_ROW)
        for record in cells:
            if record["width"] == 1:
                record["acceptance"]["counts"] = {
                    decide.ACCEPTED: 39,
                    decide.ABSENT_TRUNCATED: 1,
                }
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict == decide.VOID
        assert "control" in decision.reason

    def test_a_row_with_no_graded_cell_is_void(self) -> None:
        cells = row("batch8", SEPARATED_ROW, dispatched=39)
        decision = decide.grade_grain("batch8", cells)
        assert decision.verdict == decide.VOID
        assert decide.GATE_PLAN_CLAMPED in decision.reason

    def test_an_undialled_grain_is_absent_by_name(self) -> None:
        decision = decide.grade_grain("unit", [])
        assert decision.verdict == decide.ABSENT

    def test_grade_returns_one_decision_per_registered_grain_in_order(self) -> None:
        decisions = decide.grade(row("batch8", SEPARATED_ROW))
        assert tuple(d.grain for d in decisions) == GRAIN_LADDER
        assert [d.verdict for d in decisions] == [
            decide.ABSENT,
            decide.ABSENT,
            decide.ABSENT,
            decide.SEPARATED_WIDTH,
        ]


# ── §10, the stop rule ───────────────────────────────────────────────────────


class TestTheStopRule:
    def test_inconclusive_at_every_grain_stops_the_whole_ladder(self) -> None:
        cells: list[dict[str, Any]] = []
        for grain in GRAIN_LADDER:
            cells.extend(row(grain, INCONCLUSIVE_ROW))
        stop = decide.stop_rule(decide.grade(cells))
        assert stop.decision == decide.STOP_STOP
        assert stop.absent_rungs == ("W2", "H0", "sweep", "P1")
        assert stop.published_answer == (
            "width did not separate on the axis where separation is structural; "
            "the flat default stands on evidence."
        )

    def test_void_at_every_grain_also_stops_the_ladder(self) -> None:
        cells: list[dict[str, Any]] = []
        for grain in GRAIN_LADDER:
            cells.extend(row(grain, INCONCLUSIVE_ROW, dispatched=1))
        stop = decide.stop_rule(decide.grade(cells))
        assert stop.decision == decide.STOP_STOP
        assert set(stop.verdicts.values()) == {decide.VOID}

    def test_a_mix_of_inconclusive_and_void_still_stops(self) -> None:
        cells = row("unit", INCONCLUSIVE_ROW) + row("pair", INCONCLUSIVE_ROW, dispatched=1)
        cells += row("batch4", INCONCLUSIVE_ROW) + row("batch8", INCONCLUSIVE_ROW, dispatched=1)
        stop = decide.stop_rule(decide.grade(cells))
        assert stop.decision == decide.STOP_STOP

    def test_separation_at_any_single_grain_continues(self) -> None:
        cells = row("unit", INCONCLUSIVE_ROW) + row("pair", INCONCLUSIVE_ROW)
        cells += row("batch4", INCONCLUSIVE_ROW) + row("batch8", SEPARATED_ROW)
        stop = decide.stop_rule(decide.grade(cells))
        assert stop.decision == decide.STOP_CONTINUE
        assert stop.absent_rungs == ()
        assert stop.published_answer is None

    def test_a_combination_section_10_does_not_cover_is_named_not_resolved(self) -> None:
        """A CEILING row matches neither rule 2 nor rule 3."""
        cells = row("unit", INCONCLUSIVE_ROW) + row("pair", INCONCLUSIVE_ROW)
        cells += row("batch4", INCONCLUSIVE_ROW)
        cells += row("batch8", {1: [0.3] * 3, 8: [0.1] * 3})
        stop = decide.stop_rule(decide.grade(cells))
        assert stop.decision == decide.STOP_UNREGISTERED
        assert stop.published_answer is None
        assert "batch8=CEILING-WIDTH" in stop.reason

    def test_an_entirely_undialled_rung_is_unregistered_not_stopped(self) -> None:
        stop = decide.stop_rule(decide.grade([]))
        assert stop.decision == decide.STOP_UNREGISTERED

    def test_decide_bundles_grains_and_the_stop_rule(self) -> None:
        verdict = decide.decide(row("batch8", SEPARATED_ROW))
        assert verdict["kind"] == "bee-hive-width-verdict"
        assert len(verdict["grains"]) == len(GRAIN_LADDER)
        assert verdict["stop"]["decision"] == decide.STOP_CONTINUE


# ── the constants are the pin's, not this file's ─────────────────────────────


class TestNothingWasRetyped:
    def test_every_registered_constant_comes_from_the_pin(self) -> None:
        from tests import test_bee_hive_width_preregistration as pin

        assert decide.WIDTH_LADDER is pin.WIDTH_LADDER
        assert decide.GRAIN_LADDER is pin.GRAIN_LADDER
        assert decide.ITEMS_PER_CELL == pin.ITEMS_PER_CELL == 320
        assert decide.REPETITIONS_PER_CELL == pin.REPETITIONS_PER_CELL == 3
        assert decide.PROTOCOL_GATE == pin.PROTOCOL_GATE == 0.10
        assert decide.MIN_CELL_SECONDS == pin.MIN_CELL_SECONDS
        assert clock.RUNG_CAP_SECONDS == pin.RUNG_CAP_SECONDS == 10800.0
        assert clock.LADDER_CAP_SECONDS == pin.LADDER_CAP_SECONDS == 28800.0

    def test_the_direction_rule_is_league_commanders(self) -> None:
        from examples import league_commander as lc

        assert decide.direction_required is lc.direction_required


# ── the clock ────────────────────────────────────────────────────────────────


class TestTheClock:
    def test_an_empty_rung_has_spent_nothing(self) -> None:
        reading = clock.read_clock([])
        assert reading.charged_seconds == 0.0
        assert not reading.over_rung_cap

    def test_dialled_seconds_sums_the_cell_clocks(self) -> None:
        cells = [cell(seconds=100.0), cell(seconds=50.0)]
        assert clock.dialled_seconds(cells) == pytest.approx(150.0)

    def test_span_is_charged_per_invocation_so_an_overnight_gap_is_free(self) -> None:
        cells = [
            cell(
                run_id="run-1",
                started="2026-08-01T10:00:00+00:00",
                finished="2026-08-01T10:10:00+00:00",
            ),
            cell(
                run_id="run-2",
                started="2026-08-02T09:00:00+00:00",
                finished="2026-08-02T09:05:00+00:00",
            ),
        ]
        assert clock.span_seconds(cells) == pytest.approx(900.0)

    def test_the_caps_are_charged_against_the_span(self) -> None:
        cells = [
            cell(
                seconds=10.0,
                started="2026-08-01T10:00:00+00:00",
                finished="2026-08-01T10:10:00+00:00",
            )
        ]
        reading = clock.read_clock(cells)
        assert reading.dialled_seconds == pytest.approx(10.0)
        assert reading.charged_seconds == pytest.approx(600.0)

    def test_a_rung_past_its_cap_says_so(self) -> None:
        cells = [
            cell(
                started="2026-08-01T10:00:00+00:00",
                finished="2026-08-01T14:00:00+00:00",
            )
        ]
        reading = clock.read_clock(cells)
        assert reading.over_rung_cap
        assert not reading.over_ladder_cap
        assert reading.rung_remaining < 0

    def test_affords_checks_both_caps(self) -> None:
        reading = clock.read_clock([])
        assert reading.affords(clock.CELL_SECONDS_CEILING)
        assert not reading.affords(clock.RUNG_CAP_SECONDS + 1.0)

    def test_the_first_cell_is_costed_at_the_registered_ceiling(self) -> None:
        assert clock.estimate_seconds([], width=8, grain="batch8") == clock.CELL_SECONDS_CEILING

    def test_a_measured_cell_is_costed_at_its_own_worst_reading(self) -> None:
        cells = [
            cell(grain="batch8", width=8, repetition=0, seconds=30.0),
            cell(grain="batch8", width=8, repetition=1, seconds=44.0),
            cell(grain="unit", width=8, repetition=0, seconds=900.0),
        ]
        assert clock.estimate_seconds(cells, width=8, grain="batch8") == pytest.approx(44.0)

    def test_the_clock_reads_only_committed_records(self, tmp_path: Path) -> None:
        path = tmp_path / "cells.jsonl"
        path.write_text("", encoding="utf-8")
        assert clock.load_cells(path) == []
        assert clock.load_cells(tmp_path / "missing.jsonl") == []


# ── the driver, hermetically ─────────────────────────────────────────────────


def _scripted_payload(body: dict[str, Any]) -> dict[str, Any]:
    """A canned completion that answers in-space, read off the prompt itself.

    ``arch_hive.scripted_worker``'s rule, applied to a raw request body so the
    whole real path — ``WireSeam._post``, the retry loop, ``parse_completion``,
    ``parse_answers``, the ledger — runs with no socket.
    """
    prompt = str((body.get("messages") or [{}])[-1].get("content") or "")
    lines: list[str] = []
    for block in prompt.split("item ")[1:]:
        item_id = block.split(":", 1)[0].strip()
        allowed = ah._allowed_from(block)
        lines.append(f"{item_id} = {allowed[0]}" if allowed else f"{item_id} = ?")
    return {
        "choices": [{"message": {"content": "\n".join(lines)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 240, "completion_tokens": 4 * len(lines)},
    }


class TestThePlanCannotBeClamped:
    def test_every_grain_plans_exactly_items_per_cell(self) -> None:
        config = ah.load_hive_config()
        for grain_name, per_call in zip(GRAIN_LADDER, GRAIN_ITEMS):
            calls = drive.plan_cell(config.grain(grain_name))
            assert len(calls) * per_call == ITEMS_PER_CELL
            assert sum(len(call.item_ids) for call in calls) == ITEMS_PER_CELL

    def test_the_registered_widths_all_divide_the_call_count(self) -> None:
        """No wave is ever short, so ``batch_elapsed_seconds`` is uniform."""
        config = ah.load_hive_config()
        for grain_name in GRAIN_LADDER:
            calls = drive.plan_cell(config.grain(grain_name))
            for width in WIDTH_LADDER:
                assert len(calls) % width == 0

    def test_the_coarsest_grain_at_the_widest_width_hits_the_batch_floor(self) -> None:
        """320 is ``MIN_BATCHES_PER_CELL x MAX_HIVE_WIDTH x MAX_ITEMS_PER_CALL``."""
        config = ah.load_hive_config()
        calls = drive.plan_cell(config.grain("batch8"))
        assert len(calls) // 8 == decide.MIN_BATCHES_PER_CELL

    def test_no_call_straddles_two_questions(self) -> None:
        config = ah.load_hive_config()
        items = {item.id: item for item in drive.cell_items()}
        for grain_name in GRAIN_LADDER:
            for call in drive.plan_cell(config.grain(grain_name)):
                asked = {items[item_id].question for item_id in call.item_ids}
                assert asked == {call.question}

    def test_the_item_set_is_deterministic_and_carries_no_truth(self) -> None:
        first, second = drive.cell_items(), drive.cell_items()
        assert first == second
        assert len(first) == ITEMS_PER_CELL
        assert len({item.id for item in first}) == ITEMS_PER_CELL
        assert {item.truth for item in first} == {""}

    def test_all_three_registered_questions_are_asked(self) -> None:
        asked = {item.question for item in drive.cell_items()}
        assert asked == set(ah.QUESTION_ORDER)


class TestTheSeamMatchesTheShippedFactory:
    def test_it_dials_non_streaming_without_tools(self) -> None:
        seam = drive.build_factory(
            dial=aa.Dial(role="worker", model="m", base_url="http://x/v1", api_key="k"),
            sampling=aa.Sampling(temperature=0.3, thinking="off", max_tokens=256),
            wire_extra={},
        )(_a_call())
        assert seam.stream is False
        assert seam.tools is None
        assert seam.max_tokens == 256
        assert seam.temperature == 0.3

    def test_every_construction_argument_matches_build_worker_factory(self) -> None:
        dial = aa.Dial(role="worker", model="m", base_url="http://x/v1", api_key="k")
        sampling = aa.Sampling(temperature=0.3, thinking="off", max_tokens=256)
        call = _a_call()
        shipped = ah.build_worker_factory(dial=dial, sampling=sampling)(call)
        mine = drive.build_factory(dial=dial, sampling=sampling, wire_extra={})(call)
        for field in ("endpoint", "model", "max_tokens", "temperature", "tools", "stream"):
            assert getattr(mine, field) == getattr(shipped, field), field
        assert mine.meter.role == shipped.meter.role

    def test_the_thinking_key_goes_on_the_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """§6: asserted on the wire, never assumed."""
        config = ah.load_hive_config()
        wire = config.wire_extra(config.sampling_for("B1", "worker").thinking)
        assert wire == {"chat_template_kwargs": {"enable_thinking": False}}

        monkeypatch.setattr(ws.WorkerSeam, "_post", lambda self, body: _scripted_payload(body))
        seam = drive.build_factory(
            dial=aa.Dial(role="worker", model="m", base_url="http://x/v1", api_key="k"),
            sampling=aa.Sampling(temperature=0.3, thinking="off", max_tokens=256),
            wire_extra=wire,
        )(_a_call())
        seam([{"role": "user", "content": "item u1:\n  allowed answers: clear, risky"}])
        assert seam.last_body is not None
        assert seam.last_body["chat_template_kwargs"] == {"enable_thinking": False}
        # Non-streaming bodies carry no ``stream`` key at all — the clock this
        # rung reports is a blocking-transport clock.
        assert "stream" not in seam.last_body


class TestTheRegisteredOrder:
    def test_grains_ascend_and_width_one_leads_every_grain(self) -> None:
        order = drive.block_order()
        assert len(order) == len(GRAIN_LADDER) * len(WIDTH_LADDER)
        assert [grain for grain, _ in order[:: len(WIDTH_LADDER)]] == list(GRAIN_LADDER)
        for index in range(0, len(order), len(WIDTH_LADDER)):
            chunk = order[index : index + len(WIDTH_LADDER)]
            assert [width for _, width in chunk] == list(WIDTH_LADDER)
            assert chunk[0][1] == 1


class TestOneCellEndToEnd:
    def test_a_scripted_cell_passes_its_own_gates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The driver's own record shape, run through §9 with no socket."""
        monkeypatch.setattr(ws.WorkerSeam, "_post", lambda self, body: _scripted_payload(body))
        config = ah.load_hive_config()
        record, calls = drive.dial_cell(
            config=config,
            dial=aa.Dial(role="worker", model="m", base_url="http://x/v1", api_key="k"),
            grain_name="batch8",
            width=8,
            repetition=0,
            run_id="hermetic",
            senses_hash=HASH_A,
        )
        assert record["batches"] == decide.MIN_BATCHES_PER_CELL
        assert record["calls_planned"] == 40
        assert record["acceptance"]["dispatched"] == 40
        assert record["acceptance"]["counts"][decide.ACCEPTED] == 40
        assert record["thinking_wire_asserted"] is True
        assert record["transport"] == "blocking"
        assert record["retries"] == 0
        assert len(record["batch_elapsed_seconds"]) == decide.MIN_BATCHES_PER_CELL
        assert len(calls) == 40
        assert decide.cell_gates(record) == ()
        assert decide.items_per_second(record) is not None

    def test_a_truncated_turn_reaches_the_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A budget-exhausted turn must arrive as ``absent-truncated``, not a refusal."""

        def cut(self: Any, body: dict[str, Any]) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "thinking"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 240, "completion_tokens": 256},
            }

        monkeypatch.setattr(ws.WorkerSeam, "_post", cut)
        config = ah.load_hive_config()
        record, _calls = drive.dial_cell(
            config=config,
            dial=aa.Dial(role="worker", model="m", base_url="http://x/v1", api_key="k"),
            grain_name="batch8",
            width=8,
            repetition=0,
            run_id="hermetic",
            senses_hash=HASH_A,
        )
        assert record["acceptance"]["counts"][decide.ABSENT_TRUNCATED] == 40
        assert decide.GATE_TRUNCATED in decide.cell_gates(record)


def _a_call() -> Any:
    return ah.ScopedCall(
        id="s1",
        question="looks_risky",
        item_ids=("u1",),
        prompt="item u1:\n  allowed answers: clear, risky, unclear",
        spaces=(("clear", "risky", "unclear"),),
    )
