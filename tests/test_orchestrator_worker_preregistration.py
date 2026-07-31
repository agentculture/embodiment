"""Pre-registered constants for the orchestrator-worker series (plan task t11).

The prose argument lives in
``docs/live-test-results/orchestrator-worker-preregistration.md`` — read that
first. This file is the pin, and it differs from every pin that came before it
in the one way task t11 was asked to differ: **it recomputes each constant from
the committed measurement it derives from, rather than asserting a literal
beside a literal.**

``tests/test_league_commander_preregistration.py`` pins by equality — a value is
written twice and the two copies are compared. That catches an edit; it cannot
catch a *wrong* number, because both copies were typed by the same hand. Here
the fan-out width is computed from ``worker-throughput-summary.json``, the
attempt count from the committed latency probe plus the committed drive shapes
plus the committed caps, and the document is then checked to state exactly what
came out. Change the measurement and the pre-registration must change with it or
this file fails. That is ``M3``'s machine-checkable requirement
(``docs/plans/next-cycle-candidates.md``).

Four **bars** are choices rather than measurements and are named as such both
here and in the document's §7. Everything else is a function of them and of
data already in the repository.

Nothing here dials a model, opens a socket, or reads rig state. It reads
committed files and imports committed modules.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import arch_arms as aa  # noqa: E402
from examples import arch_league as al  # noqa: E402
from examples import challenge_coding as cc  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import league_h2h as lh  # noqa: E402
from examples import orchestrator_tools as ot  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "docs" / "live-test-results"
DOC = RESULTS / "orchestrator-worker-preregistration.md"

THROUGHPUT_SUMMARY = RESULTS / "worker-throughput-summary.json"
ARENA_GRADED = RESULTS / "arena-budget-raw" / "graded.json"
TOOLCALL_PROBE = RESULTS / "cortex-toolcall-probe.md"
VISION_PROBE = RESULTS / "cortex-vision-probe.md"
VIDEO_PROBE = RESULTS / "video-perception-probe.md"
SAMPLING_TABLE = RESULTS / "arch-arms-sampling.json"


# ── the four bars that are choices, not measurements ─────────────────────────

#: The smallest correct-count gap that counts as separation. One attempt's
#: difference is a coin flip at any n this rig affords.
MARGIN_MIN = 2
#: How the separation margin grows with n.
MARGIN_FRACTION = 0.25
#: A fan-out width must return at least this share of its nominal parallelism.
PARALLEL_EFFICIENCY_BAR = 0.75
#: A width's marginal stream must add at least this share of the best marginal
#: stream observed, or the width is past saturation.
MARGINAL_GAIN_BAR = 0.25

#: NOT a fifth bar: reused from league-commander's committed pre-registration
#: and imported from its harness, so it is a citation rather than a choice.
DIRECTION_FRACTION = 4 / 5


# ── committed inputs, read rather than restated ──────────────────────────────


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _probe_number(text: str, label: str) -> Optional[float]:
    """Read one ``| label | value |`` row out of a committed probe document."""
    pattern = r"\|\s*" + re.escape(label) + r"\s*\|\s*\*{0,2}\s*([0-9]+(?:\.[0-9]+)?)"
    found = re.search(pattern, text)
    return float(found.group(1)) if found else None


def _probe_ratio(text: str, label: str) -> Optional[tuple[int, int]]:
    """Read one ``| label | **a / b** |`` row out of a committed probe."""
    pattern = r"\|\s*" + re.escape(label) + r"\s*\|\s*\*{0,2}\s*(\d+)\s*/\s*(\d+)"
    found = re.search(pattern, text)
    return (int(found.group(1)), int(found.group(2))) if found else None


TOOLCALL_TEXT = TOOLCALL_PROBE.read_text(encoding="utf-8")
DOC_TEXT = DOC.read_text(encoding="utf-8")

#: The cortex's measured turn cost — the binding constraint on the whole series.
CORTEX_TURN_MEDIAN_SECONDS = _probe_number(TOOLCALL_TEXT, "median latency")
CORTEX_TURN_TAIL_SECONDS = _probe_number(TOOLCALL_TEXT, "slowest run")
CORTEX_TURN_FASTEST_SECONDS = _probe_number(TOOLCALL_TEXT, "fastest run")
#: The protocol result: a ceiling, which is why §14 makes it a gate not a metric.
PROTOCOL_CLEAN, PROTOCOL_N = _probe_ratio(TOOLCALL_TEXT, "closed through the `finish` tool")

_THROUGHPUT = _load_json(THROUGHPUT_SUMMARY)
WIDTH_SUMMARIES: dict[int, Mapping[str, Any]] = {
    int(entry["width"]): entry["summary"] for entry in _THROUGHPUT["widths"]
}
MEASURED_WIDTHS: tuple[int, ...] = tuple(sorted(WIDTH_SUMMARIES))
#: A floor, not an estimate: measured at max_tokens=1200 on short prompts.
WORKER_CALL_MEDIAN_SECONDS = float(WIDTH_SUMMARIES[1]["latency_seconds_median"])

#: The nine 16000-budget cortex drives t24 committed, used for drive SHAPE only.
_ARENA_C16 = [row for row in _load_json(ARENA_GRADED) if row["cell"] == "C16"]
_C16_DRIVE_TURNS = [turns for row in _ARENA_C16 for turns in row["model_turns"]]
_C16_MATCH_COMPLETIONS = [int(row["cortex_completions"]) for row in _ARENA_C16]

#: The caps, imported from the harness that already enforces them.
RUNG_CAP_SECONDS = lh.RUNG_CAP_SECONDS
LADDER_CAP_SECONDS = lh.LADDER_CAP_SECONDS

_CONFIG = aa.load_config(SAMPLING_TABLE)


# ── the derivations ──────────────────────────────────────────────────────────


def parallel_efficiency(width: int) -> float:
    """Share of nominal parallelism the rig actually returned at ``width``."""
    return float(WIDTH_SUMMARIES[width]["effective_concurrency"]) / width


def marginal_gain(lower: int, upper: int) -> float:
    """Aggregate tok/s bought per stream added between two measured widths."""
    low = float(WIDTH_SUMMARIES[lower]["aggregate_tokens_per_second"])
    high = float(WIDTH_SUMMARIES[upper]["aggregate_tokens_per_second"])
    return (high - low) / (upper - lower)


def width_by_efficiency(bar: float = PARALLEL_EFFICIENCY_BAR) -> int:
    """The widest measured width returning at least ``bar`` of its parallelism."""
    return max(width for width in MEASURED_WIDTHS if parallel_efficiency(width) >= bar)


def width_by_marginal_gain(bar: float = MARGINAL_GAIN_BAR) -> int:
    """The widest measured width still buying a real share of the best margin."""
    steps = list(zip(MEASURED_WIDTHS, MEASURED_WIDTHS[1:]))
    gains = {upper: marginal_gain(lower, upper) for lower, upper in steps}
    floor = bar * max(gains.values())
    return max(
        width for width in MEASURED_WIDTHS if width == MEASURED_WIDTHS[0] or gains[width] >= floor
    )


#: Both rules must agree, from the same data, without sharing a method.
FANOUT_WIDTH = width_by_efficiency()

#: What one grant can actually fund: partition() gives a zero slice past this.
MAX_FUNDABLE_WIDTH = ot.DEFAULT_FANOUT_MAX_STEPS // ot.MIN_UNIT_GRANT
#: Three roles per side in c-frontier-1 — the width the series will really dial.
REALISED_FANOUT_WIDTH_L1 = len(al.LEAGUE_LADDER[0].roles)

#: Cortex turns one drive actually takes at a 16000 budget, from t24's records.
TURNS_PER_DRIVE = math.ceil(sum(_C16_DRIVE_TURNS) / len(_C16_DRIVE_TURNS))
#: The committed M/E max_steps ratio, applied rather than guessed at.
ORCH_TURN_MULTIPLIER = _CONFIG.budget_for(aa.ARM_MANAGER).max_steps / (
    _CONFIG.budget_for(aa.ARM_EXISTING).max_steps
)
ORCH_TURNS_PER_DRIVE = math.ceil(TURNS_PER_DRIVE * ORCH_TURN_MULTIPLIER)

#: The same shapes for a three-turn league match.
CORTEX_TURNS_PER_MATCH = math.ceil(sum(_C16_MATCH_COMPLETIONS) / len(_C16_MATCH_COMPLETIONS))
ORCH_TURNS_PER_MATCH = math.ceil(CORTEX_TURNS_PER_MATCH * ORCH_TURN_MULTIPLIER)


def attempt_set_seconds(cortex_turns: int, worker_turns: int, cortex_seconds: float) -> float:
    """One attempt of each of the four arms, at a stated cortex clock."""
    return cortex_turns * cortex_seconds + worker_turns * WORKER_CALL_MEDIAN_SECONDS


def affordable_n(cortex_turns: int, worker_turns: int) -> int:
    """The n the median clock buys inside the rung cap AND the tail inside the ladder cap.

    Two constraints, because one is not enough: sizing on the median alone
    ignores a tail measured at 3.6x the median, and sizing on the tail alone
    would refuse a series the rig can plainly afford most of the time.
    """
    median = attempt_set_seconds(cortex_turns, worker_turns, CORTEX_TURN_MEDIAN_SECONDS)
    tail = attempt_set_seconds(cortex_turns, worker_turns, CORTEX_TURN_TAIL_SECONDS)
    return min(int(RUNG_CAP_SECONDS // median), int(LADDER_CAP_SECONDS // tail))


#: 23 cortex turns (E + M + H) and 5 worker turns (W) per attempt-set.
CHALLENGE_CORTEX_TURNS = TURNS_PER_DRIVE + 2 * ORCH_TURNS_PER_DRIVE
ATTEMPTS_PER_CELL = affordable_n(CHALLENGE_CORTEX_TURNS, TURNS_PER_DRIVE)

#: What the same arithmetic gives if every drive instead exhausted its budget.
_BUDGET_CORTEX_TURNS = sum(
    _CONFIG.budget_for(arm).max_steps for arm in (aa.ARM_EXISTING, aa.ARM_MANAGER, aa.ARM_HYBRID)
)
ATTEMPTS_FLOOR = affordable_n(
    _BUDGET_CORTEX_TURNS, _CONFIG.budget_for(aa.ARM_WORKER_SOLO).max_steps
)

LEAGUE_MATCHES = affordable_n(
    CORTEX_TURNS_PER_MATCH + 2 * ORCH_TURNS_PER_MATCH, CORTEX_TURNS_PER_MATCH
)
LEAGUE_MATCHES_RESERVE = len(al.LEAGUE_LADDER[0].seeds)


def margin_required(attempted: int) -> int:
    """The correct-count gap that counts as separation at this attempt count."""
    return max(MARGIN_MIN, math.ceil(MARGIN_FRACTION * attempted))


def min_attempts_gradeable() -> int:
    """The smallest cell that could separate at all: a >= margin_required(a)."""
    return next(a for a in range(1, 1000) if a >= margin_required(a))


MIN_ATTEMPTS_GRADEABLE = min_attempts_gradeable()


def repetitions_for(problems: int) -> int:
    """How many times a rung is replayed, so every cell lands the same attempts."""
    return max(1, ATTEMPTS_PER_CELL // problems)


def direction_required(matches: int) -> int:
    """Reused from league-commander rather than restated."""
    return lc.direction_required(matches)


# ── the rung classifier, as the pre-registration's reference implementation ──

VERDICT_SEPARATED = aa.VERDICT_SEPARATED
VERDICT_INCONCLUSIVE = aa.VERDICT_INCONCLUSIVE
VERDICT_ABSENT = aa.VERDICT_ABSENT
VERDICT_CEILING = "CEILING"
VERDICT_FLOOR = "FLOOR"
VERDICT_REFUSED = "REFUSED"
VERDICTS = (
    VERDICT_SEPARATED,
    VERDICT_INCONCLUSIVE,
    VERDICT_CEILING,
    VERDICT_FLOOR,
    VERDICT_REFUSED,
    VERDICT_ABSENT,
)

EXCLUSION_TRUNCATED = aa.EXCLUSION_TRUNCATED
EXCLUSION_DEGENERATE = aa.EXCLUSION_DEGENERATE
EXCLUSION_BELOW_MINIMUM = "below-minimum-attempts"


def _exclusions(cells: Mapping[str, Mapping[str, int]], *, heterogeneous: bool) -> dict[str, str]:
    excluded: dict[str, str] = {}
    for arm, cell in cells.items():
        if int(cell.get("attempted", 0)) < MIN_ATTEMPTS_GRADEABLE:
            excluded[arm] = EXCLUSION_BELOW_MINIMUM
        elif int(cell.get("truncated_calls", 0)) > 0:
            excluded[arm] = EXCLUSION_TRUNCATED
        elif arm == aa.ARM_HYBRID and not heterogeneous:
            excluded[arm] = EXCLUSION_DEGENERATE
    return excluded


def classify_rung(cells: Mapping[str, Mapping[str, int]], *, heterogeneous: bool) -> dict[str, Any]:
    """The rung rule of §4, in the order §4 states it.

    ``CEILING`` is evaluated **before** the missing-control refusal on purpose:
    §5 dials arm E first and does not dial the other three when its cell tops
    out, so a ceiling rung legitimately has one cell and must not be reported as
    a rung that failed to find its controls.
    """
    excluded = _exclusions(cells, heterogeneous=heterogeneous)
    if not cells:
        return {"verdict": VERDICT_ABSENT, "excluded": excluded}

    control = cells.get(aa.ARM_EXISTING)
    if control is not None and aa.ARM_EXISTING not in excluded:
        attempted = int(control["attempted"])
        if attempted - int(control["correct"]) < margin_required(attempted):
            return {"verdict": VERDICT_CEILING, "excluded": excluded, "control": dict(control)}

    gradeable = [arm for arm in aa.ARM_ORDER if arm in cells and arm not in excluded]
    missing = [arm for arm in aa.FLAT_ARMS if arm not in gradeable]
    if missing:
        return {"verdict": VERDICT_REFUSED, "excluded": excluded, "missing_controls": missing}

    if all(int(cells[arm]["correct"]) == 0 for arm in gradeable):
        return {"verdict": VERDICT_FLOOR, "excluded": excluded, "graded_arms": gradeable}

    scores = {arm: int(cells[arm]["correct"]) for arm in gradeable}
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    margin = ordered[0][1] - (ordered[1][1] if len(ordered) > 1 else ordered[0][1])
    needed = margin_required(min(int(cells[arm]["attempted"]) for arm in gradeable))
    return {
        "verdict": VERDICT_SEPARATED if margin >= needed else VERDICT_INCONCLUSIVE,
        "excluded": excluded,
        "graded_arms": gradeable,
        "scores": scores,
        "margin": margin,
        "margin_required": needed,
        "leader": ordered[0][0] if margin >= needed else None,
    }


#: The climb order of §3/§5. C1..C4 are the harness's own ladder; K1 is the
#: coding rung, whose problems and verified answers are already committed.
LADDER_A: tuple[str, ...] = tuple(rung.id for rung in aa.LADDER) + ("K1",)
#: Verdicts that let the climb continue.
CLIMB_ON = (VERDICT_CEILING, VERDICT_REFUSED, VERDICT_INCONCLUSIVE)
#: Verdicts that stop it. FLOOR stops it because every rung above is harder.
CLIMB_STOPS = (VERDICT_SEPARATED, VERDICT_FLOOR)

#: The coding rung's difficulty mapping, declared here BEFORE the run task wires
#: it into ``arch_arms.PROBLEMS`` — so heterogeneity is a rule, not a reading.
CODING_DIFFICULTY = {
    "parity_subsets": aa.DIFFICULTY_SIMPLE,
    "preimage_count": aa.DIFFICULTY_COMPLEX,
    "register_recover": aa.DIFFICULTY_COMPLEX,
}

#: The perception routes of §12, in the order stage 1 breaks ties.
PERCEPTION_ROUTES = ("text", "native", "described", "both")

#: Reused from league-commander for the same "the instrument ate the turn"
#: family rather than invented here.
PROTOCOL_GATE = 0.10


def _doc_states(name: str, value: Any) -> bool:
    """Does the committed document state this constant at this value?"""
    rendered = f"{value:g}" if isinstance(value, float) else str(value)
    candidates = (
        f"`{name}` = {rendered}",
        f"`{name}` = **{rendered}**",
        f"| `{name}` | {rendered} |",
        f"`{name}` = {rendered} //",
    )
    return any(candidate in DOC_TEXT for candidate in candidates)


# ── the tests ────────────────────────────────────────────────────────────────


class TestTheInputsAreCommittedAndParse:
    """The vacuity assertion (M2 kit item 3): proof the derivation read data.

    Without this, every recomputation below could be silently deriving its
    numbers from a parse that returned nothing and a default that filled in.
    """

    def test_every_input_file_is_committed(self) -> None:
        for path in (
            THROUGHPUT_SUMMARY,
            ARENA_GRADED,
            TOOLCALL_PROBE,
            VISION_PROBE,
            VIDEO_PROBE,
            SAMPLING_TABLE,
            DOC,
        ):
            assert path.is_file(), path

    def test_the_latency_probe_parsed_and_is_not_a_default(self) -> None:
        assert CORTEX_TURN_MEDIAN_SECONDS == pytest.approx(57.2)
        assert CORTEX_TURN_TAIL_SECONDS == pytest.approx(204.3)
        assert CORTEX_TURN_FASTEST_SECONDS == pytest.approx(25.3)
        assert CORTEX_TURN_FASTEST_SECONDS < CORTEX_TURN_MEDIAN_SECONDS
        assert CORTEX_TURN_MEDIAN_SECONDS < CORTEX_TURN_TAIL_SECONDS

    def test_the_parser_returns_none_on_a_label_that_is_not_there(self) -> None:
        # The adversarial fixture: a parser that always finds something would
        # make every assertion above vacuous.
        assert _probe_number(TOOLCALL_TEXT, "median lateness") is None
        assert _probe_ratio(TOOLCALL_TEXT, "closed through the `submit` tool") is None

    def test_the_protocol_result_is_the_ceiling_the_probe_reported(self) -> None:
        assert (PROTOCOL_CLEAN, PROTOCOL_N) == (10, 10)

    def test_the_throughput_series_carries_all_four_widths(self) -> None:
        assert MEASURED_WIDTHS == (1, 2, 8, 14)
        assert WORKER_CALL_MEDIAN_SECONDS == pytest.approx(10.869)

    def test_the_arena_drive_shapes_are_the_committed_16000_budget_cell(self) -> None:
        assert len(_ARENA_C16) == 3
        assert len(_C16_DRIVE_TURNS) == 9
        assert all(row["max_tokens"] == 16000 for row in _ARENA_C16)


class TestFanOutWidthIsDerivedFromSaturation:
    """c48 / h35 — the width comes from the measurement, never from the advert."""

    def test_two_independent_rules_select_the_same_width(self) -> None:
        assert width_by_efficiency() == width_by_marginal_gain()
        assert FANOUT_WIDTH == 8

    def test_width_fourteen_fails_both_rules(self) -> None:
        assert parallel_efficiency(14) < PARALLEL_EFFICIENCY_BAR
        gains = {8: marginal_gain(2, 8), 14: marginal_gain(8, 14)}
        assert gains[14] < MARGINAL_GAIN_BAR * max(marginal_gain(1, 2), gains[8])

    def test_width_eight_clears_both_rules(self) -> None:
        assert parallel_efficiency(8) >= PARALLEL_EFFICIENCY_BAR
        assert marginal_gain(2, 8) >= MARGINAL_GAIN_BAR * marginal_gain(1, 2)

    def test_the_operator_claim_is_recorded_as_falsified(self) -> None:
        claim = _THROUGHPUT["operator_claim"]
        assert claim["per_stream_reading_reproduced"] is False
        assert claim["aggregate_reading_reproduced"] is False

    def test_the_series_never_dials_above_the_harness_ceiling(self) -> None:
        assert FANOUT_WIDTH <= ot.MAX_FANOUT_WIDTH
        # The ceiling stays 14 and this task does not edit it: it refuses, it
        # never sets a width.
        assert ot.MAX_FANOUT_WIDTH == 14

    def test_the_committed_grant_funds_every_unit_at_the_registered_width(self) -> None:
        slices = ot.partition(ot.DEFAULT_FANOUT_MAX_STEPS, FANOUT_WIDTH)
        assert len(slices) == FANOUT_WIDTH
        assert sum(slices) == ot.DEFAULT_FANOUT_MAX_STEPS
        assert min(slices) >= ot.MIN_UNIT_GRANT
        assert slices == (2, 2, 2, 2, 1, 1, 1, 1)

    def test_the_committed_grant_cannot_fund_width_fourteen(self) -> None:
        assert MAX_FUNDABLE_WIDTH == 12
        assert FANOUT_WIDTH <= MAX_FUNDABLE_WIDTH
        assert ot.MAX_FANOUT_WIDTH > MAX_FUNDABLE_WIDTH
        starved = ot.partition(ot.DEFAULT_FANOUT_MAX_STEPS, ot.MAX_FANOUT_WIDTH)
        assert sum(1 for grant in starved if grant < ot.MIN_UNIT_GRANT) == 2

    def test_the_grant_is_never_clamped_by_the_orchestrated_arms_budget(self) -> None:
        for arm in (aa.ARM_MANAGER, aa.ARM_HYBRID):
            assert _CONFIG.budget_for(arm).max_steps >= ot.DEFAULT_FANOUT_MAX_STEPS

    def test_the_realised_width_is_three_and_is_declared_as_such(self) -> None:
        # The honest limit: the only fanning rung fields three roles per side,
        # so no claim this series makes about width is a claim about eight.
        assert REALISED_FANOUT_WIDTH_L1 == 3
        assert REALISED_FANOUT_WIDTH_L1 < FANOUT_WIDTH
        assert al.LEAGUE_LADDER[0].roles == ("defender", "harvester", "scout")


class TestCellSizingIsDerivedFromMeasuredLatency:
    """The ~57 s cortex turn decides n; n is not decided and then justified."""

    def test_the_drive_shapes_are_recomputed_not_typed(self) -> None:
        assert TURNS_PER_DRIVE == 5
        assert max(_C16_DRIVE_TURNS) == TURNS_PER_DRIVE
        assert ORCH_TURN_MULTIPLIER == pytest.approx(24 / 14)
        assert ORCH_TURNS_PER_DRIVE == 9
        assert CORTEX_TURNS_PER_MATCH == 13
        assert ORCH_TURNS_PER_MATCH == 23

    def test_the_orchestrated_arms_are_modelled_conservatively(self) -> None:
        # Every charged turn is costed as a cortex turn even though child turns
        # run on the faster worker, so the estimate is an upper bound.
        assert ORCH_TURNS_PER_DRIVE > TURNS_PER_DRIVE
        assert CORTEX_TURN_MEDIAN_SECONDS > WORKER_CALL_MEDIAN_SECONDS

    def test_the_attempt_set_costs_what_the_document_states(self) -> None:
        assert CHALLENGE_CORTEX_TURNS == 23
        median = attempt_set_seconds(
            CHALLENGE_CORTEX_TURNS, TURNS_PER_DRIVE, CORTEX_TURN_MEDIAN_SECONDS
        )
        tail = attempt_set_seconds(
            CHALLENGE_CORTEX_TURNS, TURNS_PER_DRIVE, CORTEX_TURN_TAIL_SECONDS
        )
        assert round(median, 1) == 1369.9
        assert round(tail, 1) == 4753.2
        assert f"{round(median, 1)} s" in DOC_TEXT
        assert f"{round(tail, 1)} s" in DOC_TEXT

    def test_the_two_constraints_and_the_n_they_buy(self) -> None:
        median = attempt_set_seconds(
            CHALLENGE_CORTEX_TURNS, TURNS_PER_DRIVE, CORTEX_TURN_MEDIAN_SECONDS
        )
        tail = attempt_set_seconds(
            CHALLENGE_CORTEX_TURNS, TURNS_PER_DRIVE, CORTEX_TURN_TAIL_SECONDS
        )
        assert int(RUNG_CAP_SECONDS // median) == 7
        assert int(LADDER_CAP_SECONDS // tail) == 6
        assert ATTEMPTS_PER_CELL == 6
        assert f"min({int(RUNG_CAP_SECONDS // median)}, {int(LADDER_CAP_SECONDS // tail)})" in (
            DOC_TEXT
        )

    def test_the_budget_exhaustion_floor(self) -> None:
        assert _BUDGET_CORTEX_TURNS == 62
        assert ATTEMPTS_FLOOR == 2
        assert ATTEMPTS_FLOOR < ATTEMPTS_PER_CELL

    def test_repetitions_fall_out_of_rung_width(self) -> None:
        expected = {1: 6, 2: 3, 3: 2}
        for problems, reps in expected.items():
            assert repetitions_for(problems) == reps
            assert reps * problems == ATTEMPTS_PER_CELL

    def test_every_committed_rung_lands_the_same_attempt_count(self) -> None:
        for rung in aa.LADDER:
            width = len(rung.problems)
            assert repetitions_for(width) * width == ATTEMPTS_PER_CELL
        assert repetitions_for(len(cc.PROBLEM_ORDER)) * len(cc.PROBLEM_ORDER) == ATTEMPTS_PER_CELL

    def test_the_league_rung_is_sized_by_the_same_model(self) -> None:
        assert LEAGUE_MATCHES == 2
        assert LEAGUE_MATCHES_RESERVE == 5
        assert LEAGUE_MATCHES < LEAGUE_MATCHES_RESERVE
        assert al.LEAGUE_LADDER[0].seeds == lc.SEEDS

    def test_the_full_ladder_does_not_fit_the_ladder_cap_and_says_so(self) -> None:
        median = attempt_set_seconds(
            CHALLENGE_CORTEX_TURNS, TURNS_PER_DRIVE, CORTEX_TURN_MEDIAN_SECONDS
        )
        hours = len(LADDER_A) * ATTEMPTS_PER_CELL * median / 3600
        assert hours > LADDER_CAP_SECONDS / 3600
        assert f"{hours:.1f} h" in DOC_TEXT

    def test_the_request_timeout_margin_over_the_measured_tail(self) -> None:
        margin = ws.REQUEST_TIMEOUT / CORTEX_TURN_TAIL_SECONDS
        assert margin > 1.0
        assert f"{margin:.2f}" in DOC_TEXT
        assert ws.MAX_TRANSPORT_RETRIES == 3


class TestTheDecisionRuleIsDerivedNotAsserted:
    def test_the_margin_grows_with_n_and_never_drops_below_the_bar(self) -> None:
        for attempted in range(1, 40):
            assert margin_required(attempted) >= MARGIN_MIN
            assert margin_required(attempted) >= math.ceil(MARGIN_FRACTION * attempted)
        assert margin_required(ATTEMPTS_PER_CELL) == 2
        assert margin_required(40) == 10

    def test_the_pre_registered_margin_is_never_weaker_than_the_harness(self) -> None:
        # arch-arms-sampling.json ships separation_margin=1 as PROVISIONAL and
        # names t11 as the authority. This rule is stricter at every n, so the
        # harness can never report SEPARATED where this document would not.
        harness_margin = int(_CONFIG.decision["separation_margin"])
        assert harness_margin == 1
        for attempted in range(1, 40):
            assert margin_required(attempted) >= harness_margin

    def test_the_minimum_gradeable_cell_is_computed(self) -> None:
        assert MIN_ATTEMPTS_GRADEABLE == 2
        assert 1 < margin_required(1)
        assert MIN_ATTEMPTS_GRADEABLE >= margin_required(MIN_ATTEMPTS_GRADEABLE)

    def test_the_direction_rule_is_reused_not_reinvented(self) -> None:
        assert DIRECTION_FRACTION == pytest.approx(lc.DIRECTION_FRACTION)
        assert direction_required(LEAGUE_MATCHES) == LEAGUE_MATCHES
        assert direction_required(5) == 4
        assert direction_required(1) == 1

    def test_the_verdict_vocabulary_is_closed_and_names_ceiling_and_floor(self) -> None:
        assert VERDICTS == (
            "SEPARATED",
            "INCONCLUSIVE",
            "CEILING",
            "FLOOR",
            "REFUSED",
            "ABSENT",
        )
        assert VERDICT_CEILING not in aa.VERDICTS  # the harness cannot say it yet
        assert set(CLIMB_ON).isdisjoint(CLIMB_STOPS)


def _cell(attempted: int, correct: int, truncated: int = 0) -> dict[str, int]:
    return {"attempted": attempted, "correct": correct, "truncated_calls": truncated}


def _full(scores: Mapping[str, int], attempted: int = 6) -> dict[str, dict[str, int]]:
    return {arm: _cell(attempted, correct) for arm, correct in scores.items()}


class TestTheClassifierAppliesTheRuleInTheOrderStated:
    def test_a_control_at_the_top_is_ceiling_never_a_tie(self) -> None:
        row = classify_rung(_full({"E": 6, "W": 6, "M": 6, "H": 6}), heterogeneous=True)
        assert row["verdict"] == VERDICT_CEILING

    def test_a_control_one_below_the_top_is_still_ceiling(self) -> None:
        # 5/6 leaves one point of headroom and the margin needs two, so nothing
        # could out-score the control by the required amount even in principle.
        row = classify_rung(_full({"E": 5, "W": 3, "M": 3, "H": 3}), heterogeneous=True)
        assert row["verdict"] == VERDICT_CEILING

    def test_a_control_with_headroom_is_graded(self) -> None:
        row = classify_rung(_full({"E": 4, "W": 4, "M": 4, "H": 4}), heterogeneous=True)
        assert row["verdict"] == VERDICT_INCONCLUSIVE
        assert row["margin"] == 0

    def test_the_ceiling_pre_check_needs_only_arm_E(self) -> None:
        # §5 dials E first and stops. That rung must read CEILING, not REFUSED.
        row = classify_rung({"E": _cell(6, 6)}, heterogeneous=True)
        assert row["verdict"] == VERDICT_CEILING

    def test_a_missing_control_refuses_rather_than_grading(self) -> None:
        for missing in (aa.ARM_EXISTING, aa.ARM_WORKER_SOLO):
            cells = _full({"E": 2, "W": 4, "M": 5, "H": 5})
            cells.pop(missing)
            row = classify_rung(cells, heterogeneous=True)
            assert row["verdict"] == VERDICT_REFUSED
            assert missing in row["missing_controls"]

    def test_a_truncated_control_refuses_too(self) -> None:
        cells = _full({"E": 2, "W": 4, "M": 5, "H": 5})
        cells["W"] = _cell(6, 4, truncated=1)
        row = classify_rung(cells, heterogeneous=True)
        assert row["verdict"] == VERDICT_REFUSED
        assert row["excluded"]["W"] == EXCLUSION_TRUNCATED

    def test_a_rung_nobody_solved_is_floor_not_inconclusive(self) -> None:
        row = classify_rung(_full({"E": 0, "W": 0, "M": 0, "H": 0}), heterogeneous=True)
        assert row["verdict"] == VERDICT_FLOOR

    def test_separation_needs_the_derived_margin(self) -> None:
        one = classify_rung(_full({"E": 2, "W": 2, "M": 3, "H": 2}), heterogeneous=True)
        assert one["verdict"] == VERDICT_INCONCLUSIVE
        assert one["margin"] == 1 and one["margin_required"] == 2
        two = classify_rung(_full({"E": 2, "W": 2, "M": 4, "H": 2}), heterogeneous=True)
        assert two["verdict"] == VERDICT_SEPARATED
        assert two["leader"] == aa.ARM_MANAGER

    def test_the_hybrid_is_excluded_by_rule_on_a_degenerate_rung(self) -> None:
        cells = _full({"E": 2, "W": 2, "M": 2, "H": 5})
        degenerate = classify_rung(cells, heterogeneous=False)
        assert degenerate["excluded"]["H"] == EXCLUSION_DEGENERATE
        assert aa.ARM_HYBRID not in degenerate["graded_arms"]
        assert degenerate["verdict"] == VERDICT_INCONCLUSIVE
        mixed = classify_rung(cells, heterogeneous=True)
        assert mixed["verdict"] == VERDICT_SEPARATED
        assert mixed["leader"] == aa.ARM_HYBRID

    def test_a_cell_too_small_to_separate_is_not_graded(self) -> None:
        cells = _full({"E": 1, "W": 1, "M": 1, "H": 1}, attempted=1)
        row = classify_rung(cells, heterogeneous=True)
        assert row["verdict"] == VERDICT_REFUSED
        assert set(row["excluded"]) == {"E", "W", "M", "H"}

    def test_an_empty_rung_is_absent(self) -> None:
        assert classify_rung({}, heterogeneous=True)["verdict"] == VERDICT_ABSENT

    def test_the_harness_refusal_agrees_with_the_reference_rule(self) -> None:
        # The same missing-control case, run through the shipped analyse().
        records = [
            {
                "kind": aa.KIND_CELL,
                "arm": arm,
                "rung": "C4",
                "route": aa.ROUTE_TEXT,
                "attempted": 6,
                "correct": correct,
                "truncated_calls": 0,
                "calls": 6,
            }
            for arm, correct in (("E", 2), ("M", 5), ("H", 5))
        ]
        analysis = aa.analyse(records, config=_CONFIG)
        assert analysis["rungs_refused"] == ["C4"]
        assert analysis["verdict"] == aa.VERDICT_ABSENT


class TestTheStopRule:
    def test_the_ladder_is_the_harness_ladder_plus_the_coding_rung(self) -> None:
        assert LADDER_A == ("C1", "C2", "C3", "C4", "K1")
        assert LADDER_A[: len(aa.LADDER)] == tuple(rung.id for rung in aa.LADDER)

    def test_a_ceiling_continues_the_climb_and_a_separation_stops_it(self) -> None:
        assert VERDICT_CEILING in CLIMB_ON
        assert VERDICT_INCONCLUSIVE in CLIMB_ON
        assert VERDICT_REFUSED in CLIMB_ON
        assert VERDICT_SEPARATED in CLIMB_STOPS
        assert VERDICT_FLOOR in CLIMB_STOPS

    def test_every_verdict_either_continues_or_stops_the_climb(self) -> None:
        assert set(CLIMB_ON) | set(CLIMB_STOPS) | {VERDICT_ABSENT} == set(VERDICTS)

    def test_the_caps_are_imported_from_the_harness_that_enforces_them(self) -> None:
        assert RUNG_CAP_SECONDS == lh.RUNG_CAP_SECONDS == 10800.0
        assert LADDER_CAP_SECONDS == lh.LADDER_CAP_SECONDS == 28800.0
        assert RUNG_CAP_SECONDS < LADDER_CAP_SECONDS


class TestHeterogeneityIsComputedNotDeclared:
    def test_the_committed_rungs_compute_their_own_heterogeneity(self) -> None:
        computed = {rung.id: rung.heterogeneous(aa.PROBLEMS) for rung in aa.LADDER}
        assert computed == {"C1": False, "C2": False, "C3": False, "C4": True}

    def test_only_the_mixed_battery_grades_the_hybrid_on_the_challenge_lane(self) -> None:
        graded = [rung.id for rung in aa.LADDER if rung.heterogeneous(aa.PROBLEMS)]
        assert graded == ["C4"]

    def test_the_document_names_a_reason_for_every_hybrid_graded_rung(self) -> None:
        for rung_id in ("C4", "K1", "L1"):
            assert f"| `{rung_id}` |" in DOC_TEXT
        assert "degenerate" in DOC_TEXT

    def test_the_coding_rung_is_heterogeneous_under_its_declared_mapping(self) -> None:
        registry = {
            problem_id: aa.Problem(
                id=problem_id,
                statement="",
                difficulty=difficulty,
                failure_class=aa.FAILURE_CAPACITY,
                source="examples/challenge_coding.py",
                tools=(),
                make_bench=dict,
                grade=lambda _raw: {},
            )
            for problem_id, difficulty in CODING_DIFFICULTY.items()
        }
        rung = aa.Rung(id="K1", problems=cc.PROBLEM_ORDER, why="the coding rung")
        assert rung.heterogeneous(registry) is True
        assert rung.difficulties(registry) == frozenset(aa.DIFFICULTIES)

    def test_the_mapping_is_grounded_in_the_coding_harness_own_rungs(self) -> None:
        # Not a plausible-looking literal: every id resolves to a committed
        # problem whose own rung label ranks the same way.
        assert set(CODING_DIFFICULTY) == set(cc.PROBLEMS)
        ranked = [cc.PROBLEMS[problem_id].rung for problem_id in cc.PROBLEM_ORDER]
        assert tuple(ranked) == cc.RUNGS
        assert CODING_DIFFICULTY[cc.PROBLEM_ORDER[0]] == aa.DIFFICULTY_SIMPLE
        assert CODING_DIFFICULTY[cc.PROBLEM_ORDER[-1]] == aa.DIFFICULTY_COMPLEX

    def test_the_league_rung_fields_enough_roles_to_route(self) -> None:
        # A two-role scenario would collapse the hybrid into the manager.
        assert REALISED_FANOUT_WIDTH_L1 >= 3


class TestToolSurfacesAreExactlyWhatTheDocumentEnumerates:
    def test_finish_lives_on_the_orchestrator_surfaces_and_nowhere_else(self) -> None:
        for problem in aa.PROBLEMS.values():
            for keeps_work in (True, False):
                names = [
                    entry["function"]["name"]
                    for entry in aa.orchestration_schema(problem, keeps_work=keeps_work)
                ]
                assert set(aa.FINAL_AUTHORITY_TOOLS) <= set(names)
            worker = aa.worker_tools_for(problem)
            assert set(aa.FINAL_AUTHORITY_TOOLS).isdisjoint(worker)
            assert worker[-1] == aa.WORKER_TERMINAL_TOOL

    def test_the_manager_holds_no_ground_work_verb(self) -> None:
        for problem in aa.PROBLEMS.values():
            names = tuple(
                entry["function"]["name"]
                for entry in aa.orchestration_schema(problem, keeps_work=False)
            )
            assert names == ("delegate", "note", "finish")

    def test_the_hybrid_holds_the_bench_and_the_orchestration_verbs(self) -> None:
        for problem in aa.PROBLEMS.values():
            names = [
                entry["function"]["name"]
                for entry in aa.orchestration_schema(problem, keeps_work=True)
            ]
            bench = [
                entry["function"]["name"]
                for entry in problem.tools
                if entry["function"]["name"] not in aa.FINAL_AUTHORITY_TOOLS
            ]
            assert names == bench + ["delegate", "note", "finish"]

    def test_every_bench_the_document_enumerates_is_the_committed_bench(self) -> None:
        for problem_id, problem in aa.PROBLEMS.items():
            bench = [
                entry["function"]["name"]
                for entry in problem.tools
                if entry["function"]["name"] not in aa.FINAL_AUTHORITY_TOOLS
            ]
            assert bench, problem_id
            for name in bench:
                assert f"`{name}`" in DOC_TEXT, (problem_id, name)

    def test_the_league_surfaces_are_what_the_document_states(self) -> None:
        surfaces = {arm: al.seat_tools(aa.ARMS[arm]) for arm in aa.ARM_ORDER}
        assert surfaces[aa.ARM_EXISTING] == ("order", "note")
        assert surfaces[aa.ARM_WORKER_SOLO] == ("order", "note")
        assert surfaces[aa.ARM_MANAGER] == ("delegate_units", "order", "note")
        assert surfaces[aa.ARM_HYBRID] == surfaces[aa.ARM_MANAGER]
        assert al.UNIT_TOOLS == ("report",)
        assert al.LEAGUE_FINAL_AUTHORITY_TOOLS == ("order",)

    def test_the_league_final_authority_verb_is_off_the_unit_surface(self) -> None:
        unit = [entry["function"]["name"] for entry in al.unit_schema()]
        assert set(al.LEAGUE_FINAL_AUTHORITY_TOOLS).isdisjoint(unit)


class TestTheSamplingTableIsTheCommittedInput:
    def test_every_arm_pins_every_role_it_configures(self) -> None:
        for arm_id, arm in aa.ARMS.items():
            for role in arm.configured_roles:
                sampling = _CONFIG.sampling_for(arm_id, role)
                assert sampling.max_tokens == 16000
                assert sampling.temperature == pytest.approx(0.3)
                assert sampling.thinking in ("on", "off")

    def test_the_acting_minds_think_and_senses_does_not(self) -> None:
        for arm_id, arm in aa.ARMS.items():
            for role in arm.acting_roles:
                assert _CONFIG.sampling_for(arm_id, role).thinking == "on"
            assert _CONFIG.sampling_for(arm_id, aa.ROLE_SENSES).thinking == "off"

    def test_senses_is_identical_across_arms_and_hashed(self) -> None:
        digest = aa.assert_senses_identical(_CONFIG)
        assert digest and isinstance(digest, str)

    def test_budgets_are_sufficient_rather_than_equal(self) -> None:
        flat = {arm: _CONFIG.budget_for(arm) for arm in aa.FLAT_ARMS}
        orchestrated = {arm: _CONFIG.budget_for(arm) for arm in ("M", "H")}
        for budget in flat.values():
            assert (budget.max_steps, budget.worker_max_steps, budget.spawn_allowance) == (
                14,
                0,
                0,
            )
        for budget in orchestrated.values():
            assert (budget.max_steps, budget.worker_max_steps, budget.spawn_allowance) == (
                24,
                6,
                3,
            )

    def test_the_cortex_model_is_resolved_and_never_hard_coded(self) -> None:
        raw = _load_json(SAMPLING_TABLE)
        assert raw["roles"]["cortex"]["model"] is None
        assert raw["roles"]["worker"]["model"] == "unsloth/Qwen3.6-35B-A3B-NVFP4"
        assert "unsloth/Qwen3.6-27B-NVFP4" in DOC_TEXT

    def test_reasoning_tokens_are_absent_on_this_rig_and_recorded_as_none(self) -> None:
        # t3 measured no reasoning/content token split at any width, so the
        # series reports None, never a folded zero.
        assert all(
            summary["reasoning_tokens_reported"] == 0 for summary in WIDTH_SUMMARIES.values()
        )
        assert aa.reasoning_tokens_from({}) == (None, aa.TOKEN_DETAIL_ABSENT)
        assert "`None`, never folded into a `0`" in DOC_TEXT


class TestCapabilityFactsComeFromProbesNotTheAdvert:
    """c43 / h32 — for as long as the two disagree, the probe is the source."""

    def test_the_vision_probe_is_cited_and_carries_its_guess_proof_control(self) -> None:
        text = VISION_PROBE.read_text(encoding="utf-8")
        assert "4, purple" in text
        assert "cortex-vision-probe.md" in DOC_TEXT

    def test_the_advert_gap_is_recorded_rather_than_relied_on(self) -> None:
        assert "/capabilities" in DOC_TEXT
        assert "not used" in DOC_TEXT

    def test_the_video_probe_and_its_correction_are_cited(self) -> None:
        text = VIDEO_PROBE.read_text(encoding="utf-8")
        assert "video_url" in text
        assert "video-perception-probe.md" in DOC_TEXT

    def test_the_toolcall_probe_is_cited_as_a_gate_not_an_outcome(self) -> None:
        assert "cortex-toolcall-probe.md" in DOC_TEXT
        assert "never an outcome metric" in DOC_TEXT
        assert f"{PROTOCOL_CLEAN} / {PROTOCOL_N}" in DOC_TEXT

    def test_the_protocol_gate_is_reused_from_league_commander(self) -> None:
        from tests import test_league_commander_preregistration as lcp

        assert PROTOCOL_GATE == lcp.LENGTH_FRACTION_MAX


class TestTheDocumentStatesTheRecomputedConstants:
    """If the derivation moves, the prose must move with it or this fails."""

    def test_every_derived_constant_appears_at_its_computed_value(self) -> None:
        derived = {
            "FANOUT_WIDTH": FANOUT_WIDTH,
            "MAX_FUNDABLE_WIDTH": MAX_FUNDABLE_WIDTH,
            "REALISED_FANOUT_WIDTH_L1": REALISED_FANOUT_WIDTH_L1,
            "ATTEMPTS_PER_CELL": ATTEMPTS_PER_CELL,
            "ATTEMPTS_FLOOR": ATTEMPTS_FLOOR,
            "MIN_ATTEMPTS_GRADEABLE": MIN_ATTEMPTS_GRADEABLE,
            "LEAGUE_MATCHES": LEAGUE_MATCHES,
            "LEAGUE_MATCHES_RESERVE": LEAGUE_MATCHES_RESERVE,
            "TURNS_PER_DRIVE": TURNS_PER_DRIVE,
            "ORCH_TURNS_PER_DRIVE": ORCH_TURNS_PER_DRIVE,
            "CORTEX_TURNS_PER_MATCH": CORTEX_TURNS_PER_MATCH,
            "ORCH_TURNS_PER_MATCH": ORCH_TURNS_PER_MATCH,
            "CORTEX_TURN_MEDIAN_SECONDS": CORTEX_TURN_MEDIAN_SECONDS,
            "CORTEX_TURN_TAIL_SECONDS": CORTEX_TURN_TAIL_SECONDS,
            "WORKER_CALL_MEDIAN_SECONDS": WORKER_CALL_MEDIAN_SECONDS,
            "RUNG_CAP_SECONDS": int(RUNG_CAP_SECONDS),
            "LADDER_CAP_SECONDS": int(LADDER_CAP_SECONDS),
            "MAX_FANOUT_WIDTH": ot.MAX_FANOUT_WIDTH,
            "DEFAULT_FANOUT_MAX_STEPS": ot.DEFAULT_FANOUT_MAX_STEPS,
            "MIN_UNIT_GRANT": ot.MIN_UNIT_GRANT,
            "MAX_TRANSPORT_RETRIES": ws.MAX_TRANSPORT_RETRIES,
        }
        missing = [name for name, value in derived.items() if not _doc_states(name, value)]
        assert not missing, f"the document does not state: {missing}"

    def test_every_chosen_bar_appears_and_is_labelled_a_choice(self) -> None:
        bars = {
            "MARGIN_MIN": MARGIN_MIN,
            "MARGIN_FRACTION": MARGIN_FRACTION,
            "PARALLEL_EFFICIENCY_BAR": PARALLEL_EFFICIENCY_BAR,
            "MARGINAL_GAIN_BAR": MARGINAL_GAIN_BAR,
            "DIRECTION_FRACTION": DIRECTION_FRACTION,
        }
        missing = [name for name, value in bars.items() if not _doc_states(name, value)]
        assert not missing, f"the document does not state: {missing}"
        assert "choices, not measurements" in DOC_TEXT

    def test_the_margin_rule_is_written_out_not_only_evaluated(self) -> None:
        assert "margin_required(a) = max(MARGIN_MIN, ceil(MARGIN_FRACTION * a))" in DOC_TEXT

    def test_the_document_names_the_routing_exemption_and_its_limit(self) -> None:
        assert "exempt from the information-matching rule" in DOC_TEXT
        assert "It relaxes information matching. It does not touch fog scoping." in DOC_TEXT
        for route in PERCEPTION_ROUTES:
            assert f"`{route}`" in DOC_TEXT

    def test_the_document_names_every_arm_and_the_refusal(self) -> None:
        for arm in aa.ARM_ORDER:
            assert f"**{arm}**" in DOC_TEXT
        assert "No verdict issues without both flat controls" in DOC_TEXT

    def test_the_document_names_every_rung_on_the_ladder(self) -> None:
        for rung_id in LADDER_A:
            assert f"`{rung_id}`" in DOC_TEXT
        assert "`L1`" in DOC_TEXT


class TestTheAccountingRuleIsQuotedVerbatim:
    """The spec requires the rule to appear in the pre-registration verbatim."""

    @staticmethod
    def _quoted() -> str:
        block = re.search(
            r"<!-- FANOUT_ACCOUNTING_RULE:BEGIN -->\s*```text\n(.*?)```",
            DOC_TEXT,
            re.DOTALL,
        )
        assert block is not None, "the accounting-rule block is missing"
        return block.group(1)

    def test_the_block_matches_the_harness_string_character_for_character(self) -> None:
        assert self._quoted().rstrip("\n") == ot.FANOUT_ACCOUNTING_RULE.rstrip("\n")

    def test_the_quoted_rule_is_not_empty(self) -> None:
        assert len(self._quoted().strip()) > 500

    def test_the_partition_clause_holds_for_the_registered_width(self) -> None:
        # Clause 1, exercised rather than trusted.
        for grant in range(0, 40):
            slices = ot.partition(grant, FANOUT_WIDTH)
            assert sum(slices) == grant
            assert len(slices) == FANOUT_WIDTH

    def test_the_degradation_vocabulary_the_document_names_is_the_harness_one(self) -> None:
        for code in ot.FANOUT_DEGRADATIONS:
            assert f"`{code}`" in DOC_TEXT
        for refusal in ot.FANOUT_REFUSALS:
            assert f"`{refusal}`" in DOC_TEXT
        assert f"`{ot.FANOUT_EXIT_PARTIAL}`" in DOC_TEXT


class TestThisIsCommittedBeforeTheFirstMeasuredDial:
    def test_no_architecture_series_artifact_exists_yet(self) -> None:
        # The claim the document opens with, checkable rather than asserted.
        stray = sorted(
            path.name
            for path in RESULTS.iterdir()
            if path.name.startswith(("arch-arms", "arch-league"))
            and path.suffix in (".jsonl", ".json")
            and path.name != "arch-arms-sampling.json"
        )
        assert stray == [], f"a measured artifact already exists: {stray}"

    def test_the_sampling_table_is_an_input_and_stays_provisional(self) -> None:
        raw = _load_json(SAMPLING_TABLE)
        assert raw["read_by"] == "examples/arch_arms.py"
        assert any("PROVISIONAL" in line for line in raw["decision"]["why"])
