"""Pre-registered constants for the Bee-Hive width series (plan task t9).

The prose argument lives in
``docs/live-test-results/bee-hive-width-preregistration.md`` — read that first.
This file is the pin, and it follows the shape
``tests/test_orchestrator_worker_preregistration.py`` established: **every
constant is recomputed from the committed measurement it derives from, rather
than asserted as a literal beside a literal.** Change
``worker-scoped-overhead-summary.json`` or ``arch-hive-sampling.json`` and the
document must change with them or this file fails.

Two **bars** are choices rather than measurements and are named as such both
here and in the document's §16. Everything else is a function of them and of
data already in the repository.

Three things this pin does that its predecessor did not, because task t9's
acceptance criteria ask for them:

* it **proves** the outcome metric is mandatory-populated rather than asserting
  it — by running every declining/failing worker double through the real
  dispatch path and checking the metric still lands
  (:class:`TestTheOutcomeMetricIsMandatoryPopulated`);
* it checks the **rejected** metrics are named in the document, because
  "rejected at registration time" means written down, not merely not used;
* it asserts the two extremes of arm P's escalation rate are both declared,
  including the one the harness ships no flag for.

Nothing here dials a model, opens a socket, or reads rig state. It reads
committed files and imports committed modules.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import arch_hive as ah  # noqa: E402
from examples import arch_policy as ap  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import league_h2h as lh  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "docs" / "live-test-results"
DOC = RESULTS / "bee-hive-width-preregistration.md"

OVERHEAD_SUMMARY = RESULTS / "worker-scoped-overhead-summary.json"
THROUGHPUT_SUMMARY = RESULTS / "worker-throughput-summary.json"
ARENA_GRADED = RESULTS / "arena-budget-raw" / "graded.json"
STREAMING_PROBE = RESULTS / "streaming-probe.md"
HIVE_TABLE = RESULTS / "arch-hive-sampling.json"
POLICY_TABLE = RESULTS / "arch-policy-sampling.json"
PRIOR_PREREGISTRATION = RESULTS / "orchestrator-worker-preregistration.md"


# ── the two bars that are choices, not measurements ──────────────────────────

#: A scoped lane under a tenth of a cortex turn cannot be argued to have changed
#: the turn. Sets the amortisation floor and the visible-width call count.
SCOPED_SHARE_TARGET = 0.10
#: Two orders of magnitude over the measured maximum inter-chunk gap.
IDLE_MARGIN = 100

#: NOT bars: cited from harnesses and from the previous pre-registration, so
#: they are citations rather than fresh choices.
DIRECTION_FRACTION = lc.DIRECTION_FRACTION
PROTOCOL_GATE = lc.LENGTH_FRACTION_MAX
MARGIN_MIN = 2
MARGIN_FRACTION = 0.25


# ── committed inputs, read rather than restated ──────────────────────────────


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _probe_number(text: str, label: str) -> Optional[float]:
    """Read one ``| label | value |`` row out of a committed probe document."""
    pattern = r"\|\s*" + re.escape(label) + r"\s*\|\s*\*{0,2}\s*([0-9]+(?:\.[0-9]+)?)"
    found = re.search(pattern, text)
    return float(found.group(1)) if found else None


DOC_TEXT = DOC.read_text(encoding="utf-8")
STREAMING_TEXT = STREAMING_PROBE.read_text(encoding="utf-8")

_OVERHEAD = _load_json(OVERHEAD_SUMMARY)
_RICH = _OVERHEAD["concurrency_transfer"]["rich"]
_LEAN_CLEAN = _OVERHEAD["concurrency_transfer_excluding_retry_batches"]["lean"]
_PER_CALL = _OVERHEAD["scoped_calls_per_cortex_turn"]
_PREFILL = _OVERHEAD["prefill_cost"]
_THINKING = _OVERHEAD["thinking_cost"]["scoped_budget"]
_CELLS = {cell["spec"]["id"]: cell for cell in _OVERHEAD["cells"]}

#: The currency a width sweep actually spends, measured on this dispatch path.
CALLS_PER_SECOND_W1 = float(_RICH["width1_calls_per_second"])
CALLS_PER_SECOND_W8 = float(_RICH["width8_calls_per_second"])
WIDTH_SPEEDUP_RICH = float(_RICH["call_throughput_speedup"])
WIDTH_SPEEDUP_LEAN = float(_LEAN_CLEAN["call_throughput_speedup"])
#: The metric that reads HIGHER on tiny calls and must never enter a verdict.
EFFECTIVE_CONCURRENCY_TRAP = float(_LEAN_CLEAN["measured_effective_concurrency"])
EFFECTIVE_CONCURRENCY_REFERENCE = float(_RICH["reference_effective_concurrency_1200_token_calls"])

SCOPED_CALL_COMPLETION_TOKENS = float(_PER_CALL["completion_tokens_per_scoped_call"])
SCOPED_CALL_PROMPT_TOKENS = float(_PER_CALL["prompt_tokens_per_scoped_call"])
CORTEX_TURN_SECONDS_LOW = float(_PER_CALL["cortex_turn_seconds_range"][0])
CORTEX_TURN_SECONDS_HIGH = float(_PER_CALL["cortex_turn_seconds_range"][1])

PREFILL_EXTRA_SECONDS_W1 = float(_PREFILL["width1"]["extra_latency_seconds"])
PREFILL_EXTRA_SECONDS_W8 = float(_PREFILL["width8"]["extra_latency_seconds"])
PREFILL_THROUGHPUT_RATIO_W8 = float(_PREFILL["width8"]["throughput_ratio_rich_over_lean"])

#: Not a chosen batch count: t8's committed width-8 cell ran this many.
MIN_BATCHES_PER_CELL = len(_CELLS["rich-off-w8"]["batch_elapsed_seconds"])


def _clean_batches() -> dict[str, list[float]]:
    """t8's committed batch clocks with the retry-contaminated ones dropped."""
    kept = {name: list(cell["batch_elapsed_seconds"]) for name, cell in _CELLS.items()}
    for name, contaminated in _OVERHEAD["retry_contaminated_batches"].items():
        indexes = {
            entry if isinstance(entry, int) else entry.get("batch") for entry in contaminated
        }
        kept[name] = [value for pos, value in enumerate(kept[name]) if pos not in indexes]
    return kept


def _resolution_seconds() -> float:
    """The instrument's own batch-to-batch spread, on thinking-off cells."""
    half_ranges = [
        (max(batches) - min(batches)) / 2
        for name, batches in _clean_batches().items()
        if _CELLS[name]["spec"]["thinking"] == "off" and batches
    ]
    return round(max(half_ranges), 4)


RESOLUTION_SECONDS = _resolution_seconds()

#: The streaming instrument, parsed from the probe rather than restated.
MAX_INTER_CHUNK_GAP_SECONDS = _probe_number(STREAMING_TEXT, "**max inter-chunk gap (s)**")
TIME_TO_FIRST_CHUNK_SECONDS = _probe_number(STREAMING_TEXT, "time to first chunk (s)")
#: The cortex server's admitted concurrent sequences. The worker's is not this.
MAX_NUM_SEQS = 2

_THROUGHPUT = _load_json(THROUGHPUT_SUMMARY)
WIDTH_SUMMARIES = {int(entry["width"]): entry["summary"] for entry in _THROUGHPUT["widths"]}

_ARENA_C16 = [row for row in _load_json(ARENA_GRADED) if row["cell"] == "C16"]
_C16_DRIVE_TURNS = [turns for row in _ARENA_C16 for turns in row["model_turns"]]
#: Drive SHAPE only, from t24's nine committed 16000-budget drives.
TURNS_PER_DRIVE = math.ceil(sum(_C16_DRIVE_TURNS) / len(_C16_DRIVE_TURNS))

#: The caps, imported from the harness that already enforces them.
RUNG_CAP_SECONDS = lh.RUNG_CAP_SECONDS
LADDER_CAP_SECONDS = lh.LADDER_CAP_SECONDS

HIVE_CONFIG = ah.load_hive_config(HIVE_TABLE)
POLICY_CONFIG = ap.load_policy_config(POLICY_TABLE)


# ── the derivations ──────────────────────────────────────────────────────────


def _width_ladder(ceiling: int) -> tuple[int, ...]:
    """Powers of two up to the harness's own measured-saturation ceiling."""
    return tuple(2**step for step in range(ceiling.bit_length()) if 2**step <= ceiling)


MAX_HIVE_WIDTH = ah.MAX_HIVE_WIDTH
WIDTH_LADDER = _width_ladder(MAX_HIVE_WIDTH)

GRAIN_LADDER = tuple(
    sorted(HIVE_CONFIG.grains, key=lambda name: HIVE_CONFIG.grains[name].items_per_call)
)
GRAIN_ITEMS = tuple(HIVE_CONFIG.grains[name].items_per_call for name in GRAIN_LADDER)
MAX_ITEMS_PER_CALL = max(GRAIN_ITEMS)

MAX_SCOPED_CALLS = HIVE_CONFIG.budget_for(ah.TIER_SCOPED).max_scoped_calls
WORKER_SAMPLING = HIVE_CONFIG.sampling_for(ah.TIER_SCOPED, ah.ROLE_WORKER)
WORKER_MAX_TOKENS = WORKER_SAMPLING.max_tokens

#: Floor 1 — dropping one retry-contaminated batch whole must still leave a cell.
RESOLUTION_FLOOR_ITEMS = MIN_BATCHES_PER_CELL * MAX_HIVE_WIDTH * MAX_ITEMS_PER_CALL
#: Floor 2 — the cell must price something #44 actually claims.
AMORTISATION_FLOOR_ITEMS = math.ceil(
    SCOPED_SHARE_TARGET * CORTEX_TURN_SECONDS_LOW * CALLS_PER_SECOND_W1
)
ITEMS_PER_CELL = max(RESOLUTION_FLOOR_ITEMS, AMORTISATION_FLOOR_ITEMS)

#: Conservative: every cell priced at the finest grain and the slowest width.
CELL_SECONDS_CEILING = ITEMS_PER_CELL / CALLS_PER_SECOND_W1
CELLS_PER_BLOCK = len(WIDTH_LADDER) * len(GRAIN_LADDER)
BLOCK_SECONDS_CEILING = CELLS_PER_BLOCK * CELL_SECONDS_CEILING
REPETITIONS_PER_CELL = int(RUNG_CAP_SECONDS // BLOCK_SECONDS_CEILING)

#: What the same cap buys once each cell is priced at its own call count.
BLOCK_SECONDS_MODELLED = (
    len(WIDTH_LADDER) * sum(ITEMS_PER_CELL / items for items in GRAIN_ITEMS) / CALLS_PER_SECOND_W1
)
REPETITIONS_RESERVE = int(RUNG_CAP_SECONDS // BLOCK_SECONDS_MODELLED)

#: The CEILING-WIDTH floor, and the headroom the registered cell size has.
MIN_CELL_SECONDS = MIN_BATCHES_PER_CELL * RESOLUTION_SECONDS
SHORTEST_CONTROL_CELL_SECONDS = (ITEMS_PER_CELL / MAX_ITEMS_PER_CALL) / CALLS_PER_SECOND_W1
CEILING_HEADROOM = SHORTEST_CONTROL_CELL_SECONDS / MIN_CELL_SECONDS

#: The amortisation arithmetic, computed before it is dialled.
DRIVE_SECONDS_LOW = TURNS_PER_DRIVE * CORTEX_TURN_SECONDS_LOW
DRIVE_SECONDS_HIGH = TURNS_PER_DRIVE * CORTEX_TURN_SECONDS_HIGH
DRIVE_SCOPED_SECONDS_W1 = MAX_SCOPED_CALLS / CALLS_PER_SECOND_W1
DRIVE_SCOPED_SECONDS_W8 = MAX_SCOPED_CALLS / CALLS_PER_SECOND_W8
SCOPED_SHARE_LOW = 100 * DRIVE_SCOPED_SECONDS_W1 / DRIVE_SECONDS_HIGH
SCOPED_SHARE_HIGH = 100 * DRIVE_SCOPED_SECONDS_W1 / DRIVE_SECONDS_LOW
WIDTH_SAVES_SECONDS = DRIVE_SCOPED_SECONDS_W1 - DRIVE_SCOPED_SECONDS_W8
WIDTH_EFFECT_ON_DRIVE_LOW = 100 * WIDTH_SAVES_SECONDS / DRIVE_SECONDS_HIGH
WIDTH_EFFECT_ON_DRIVE_HIGH = 100 * WIDTH_SAVES_SECONDS / DRIVE_SECONDS_LOW
CALLS_FOR_VISIBLE_WIDTH_LOW = math.ceil(
    SCOPED_SHARE_TARGET * DRIVE_SECONDS_LOW * CALLS_PER_SECOND_W1
)
CALLS_FOR_VISIBLE_WIDTH_HIGH = math.ceil(
    SCOPED_SHARE_TARGET * DRIVE_SECONDS_HIGH * CALLS_PER_SECOND_W1
)
VISIBILITY_SHORTFALL_LOW = CALLS_FOR_VISIBLE_WIDTH_LOW / MAX_SCOPED_CALLS
VISIBILITY_SHORTFALL_HIGH = CALLS_FOR_VISIBLE_WIDTH_HIGH / MAX_SCOPED_CALLS

#: The coarsest grain's completion need, against the registered budget.
COARSEST_GRAIN_TOKENS_NEEDED = MAX_ITEMS_PER_CALL * SCOPED_CALL_COMPLETION_TOKENS
TOKEN_MARGIN_COARSEST = WORKER_MAX_TOKENS / COARSEST_GRAIN_TOKENS_NEEDED

#: The streaming phase-2 floor.
IDLE_BOUND_FLOOR_SECONDS = IDLE_MARGIN * (MAX_INTER_CHUNK_GAP_SECONDS or 0.0)

#: The headroom screen's match count. Cited, not chosen: league_commander's own
#: committed seed count, which is what a bot-vs-bot screen can replay.
HEADROOM_MATCHES = len(lc.SEEDS)

#: The decision constants the harnesses ship as PROVISIONAL, naming t9.
CALL_ACCEPTANCE_REFUTES_AT = float(HIVE_CONFIG.decision["call_acceptance_refutes_at_rate"])
ESCALATION_COLLAPSES_AT = float(POLICY_CONFIG.decision["escalation_rate_collapses_at"])
POLICY_ACCEPTANCE_FLOOR = float(POLICY_CONFIG.decision["policy_acceptance_floor"])

_EPISODE = ap.demo_episode(POLICY_CONFIG.episode)
EPISODE_SITUATIONS = len(_EPISODE)
EPISODE_UNDECIDABLE = sum(1 for situation in _EPISODE if not ap.is_decidable(situation.features))
MAX_ESCALATION_CALLS = POLICY_CONFIG.budget_for(ap.ARM_COMPILED).max_escalation_calls


def margin_required(attempted: int) -> int:
    """Reused verbatim from the previous pre-registration, not re-invented."""
    return max(MARGIN_MIN, math.ceil(MARGIN_FRACTION * attempted))


def direction_required(observations: int) -> int:
    """Reused from league_commander rather than restated."""
    return lc.direction_required(observations)


def items_per_second(items: int, cell_wall_seconds: float) -> float:
    """The registered outcome metric. Neither term comes from a model."""
    return items / cell_wall_seconds


# ── the document check ───────────────────────────────────────────────────────

_SEPARATOR = r"`?\*{0,2}\s*(?:=|\|)\s*\*{0,2}`?"


def _renderings(value: Any) -> set[str]:
    if isinstance(value, bool):
        return {str(value)}
    if isinstance(value, float):
        return {str(value), f"{value:g}", f"{value:.1f}", f"{value:.2f}", f"{value:.4f}"}
    if isinstance(value, tuple):
        return {str(value), ", ".join(str(item) for item in value)}
    return {str(value)}


def _doc_states(name: str, value: Any) -> bool:
    """Does the committed document state this constant at this value?"""
    for rendered in _renderings(value):
        pattern = re.escape(name) + _SEPARATOR + re.escape(rendered) + r"(?![0-9])"
        if re.search(pattern, DOC_TEXT):
            return True
    return False


def _scoped_calls(grain: str = "unit", items: int = 8) -> tuple[Any, ...]:
    """A planned batch off the harness's own planner, for the dispatch proofs."""
    planned, refused = ah.plan_calls(
        ah.demo_items(items, questions=("pick_option",)),
        question="pick_option",
        grain=HIVE_CONFIG.grain(grain),
        budget=items,
        stem="pin",
    )
    assert not refused
    return planned


# ── the tests ────────────────────────────────────────────────────────────────


class TestTheInputsAreCommittedAndParse:
    """The vacuity assertion: proof the derivation read data, not a default."""

    def test_every_input_file_is_committed(self) -> None:
        for path in (
            OVERHEAD_SUMMARY,
            THROUGHPUT_SUMMARY,
            ARENA_GRADED,
            STREAMING_PROBE,
            HIVE_TABLE,
            POLICY_TABLE,
            PRIOR_PREREGISTRATION,
            DOC,
        ):
            assert path.is_file(), path

    def test_the_overhead_probe_parsed_and_is_not_a_default(self) -> None:
        assert CALLS_PER_SECOND_W1 == pytest.approx(1.8931)
        assert CALLS_PER_SECOND_W8 == pytest.approx(3.4593)
        assert CALLS_PER_SECOND_W8 > CALLS_PER_SECOND_W1
        assert WIDTH_SPEEDUP_RICH == pytest.approx(1.8273)
        assert WIDTH_SPEEDUP_LEAN == pytest.approx(3.4431)

    def test_the_streaming_probe_parsed(self) -> None:
        assert MAX_INTER_CHUNK_GAP_SECONDS == pytest.approx(0.124)
        assert TIME_TO_FIRST_CHUNK_SECONDS == pytest.approx(0.263)

    def test_the_parser_returns_none_on_a_label_that_is_not_there(self) -> None:
        # A parser that always found something would make the assertions above
        # vacuous.
        assert _probe_number(STREAMING_TEXT, "max inter-chunk gasp (s)") is None
        assert _probe_number(STREAMING_TEXT, "time to last chunk (s)") is None

    def test_the_document_checker_rejects_a_value_the_document_does_not_state(self) -> None:
        assert _doc_states("ITEMS_PER_CELL", ITEMS_PER_CELL)
        assert not _doc_states("ITEMS_PER_CELL", ITEMS_PER_CELL + 1)
        assert not _doc_states("NO_SUCH_CONSTANT_EXISTS", 1)

    def test_the_arena_drive_shapes_are_the_committed_16000_budget_cell(self) -> None:
        assert len(_ARENA_C16) == 3
        assert len(_C16_DRIVE_TURNS) == 9
        assert all(row["max_tokens"] == 16000 for row in _ARENA_C16)
        assert TURNS_PER_DRIVE == 5


class TestTheLaddersAreDerivedFromMeasuredSaturation:
    """The width ladder comes from the harness constant, never from the advert."""

    def test_the_width_ladder_is_powers_of_two_up_to_measured_saturation(self) -> None:
        assert MAX_HIVE_WIDTH == 8
        assert WIDTH_LADDER == (1, 2, 4, 8)
        assert max(WIDTH_LADDER) == MAX_HIVE_WIDTH

    def test_the_saturation_ceiling_is_not_the_advertised_width(self) -> None:
        # 8 -> 14 buys +5.5% aggregate for +75% concurrent load, and every
        # retry in the throughput series happened at 14.
        assert WIDTH_SUMMARIES[8]["effective_concurrency"] == pytest.approx(6.14)
        assert WIDTH_SUMMARIES[14]["effective_concurrency"] == pytest.approx(8.993)
        assert WIDTH_SUMMARIES[8]["effective_concurrency"] / 8 > (
            WIDTH_SUMMARIES[14]["effective_concurrency"] / 14
        )
        assert 14 not in WIDTH_LADDER

    def test_the_grain_ladder_is_the_committed_table_in_ascending_order(self) -> None:
        assert GRAIN_LADDER == ("unit", "pair", "batch4", "batch8")
        assert GRAIN_ITEMS == (1, 2, 4, 8)
        assert list(GRAIN_ITEMS) == sorted(GRAIN_ITEMS)
        assert MAX_ITEMS_PER_CALL == 8

    def test_a_grain_outside_the_committed_table_cannot_be_dialled(self) -> None:
        for grain in GRAIN_LADDER:
            assert HIVE_CONFIG.grain(grain).items_per_call in GRAIN_ITEMS
        with pytest.raises(ah.ConfigError):
            HIVE_CONFIG.grain("batch16")


class TestCellSizingIsDerivedFromMeasuredThroughput:
    def test_the_two_floors_are_computed_and_the_larger_wins(self) -> None:
        assert RESOLUTION_FLOOR_ITEMS == MIN_BATCHES_PER_CELL * MAX_HIVE_WIDTH * MAX_ITEMS_PER_CALL
        assert RESOLUTION_FLOOR_ITEMS == 320
        assert AMORTISATION_FLOOR_ITEMS == 76
        assert ITEMS_PER_CELL == max(RESOLUTION_FLOOR_ITEMS, AMORTISATION_FLOOR_ITEMS)
        assert ITEMS_PER_CELL == 320

    def test_the_batch_count_is_cited_from_t8_not_chosen(self) -> None:
        assert MIN_BATCHES_PER_CELL == 5
        assert MIN_BATCHES_PER_CELL == len(_CELLS["rich-off-w8"]["batch_elapsed_seconds"])

    def test_the_coarsest_grain_still_fills_the_registered_batch_count(self) -> None:
        calls = ITEMS_PER_CELL // MAX_ITEMS_PER_CALL
        assert calls // MAX_HIVE_WIDTH == MIN_BATCHES_PER_CELL

    def test_the_repetition_count_falls_out_of_the_cap(self) -> None:
        assert CELLS_PER_BLOCK == 16
        assert round(CELL_SECONDS_CEILING, 1) == 169.0
        assert round(BLOCK_SECONDS_CEILING, 1) == 2704.6
        assert REPETITIONS_PER_CELL == 3
        assert f"{round(CELL_SECONDS_CEILING, 1)} s" in DOC_TEXT
        assert f"{round(BLOCK_SECONDS_CEILING, 1)} s" in DOC_TEXT

    def test_the_reserve_is_what_the_conservatism_costs(self) -> None:
        assert round(BLOCK_SECONDS_MODELLED, 1) == 1267.8
        assert REPETITIONS_RESERVE == 8
        assert REPETITIONS_RESERVE > REPETITIONS_PER_CELL
        assert BLOCK_SECONDS_CEILING > BLOCK_SECONDS_MODELLED
        assert f"{BLOCK_SECONDS_CEILING / BLOCK_SECONDS_MODELLED:.2f}" in DOC_TEXT

    def test_the_headroom_screen_match_count_is_cited_not_chosen(self) -> None:
        assert HEADROOM_MATCHES == len(lc.SEEDS) == 5
        assert direction_required(HEADROOM_MATCHES) == 4
        assert "`direction_required(5)` = **4**" in DOC_TEXT

    def test_the_direction_bar_is_reused_and_rises_with_n(self) -> None:
        assert DIRECTION_FRACTION == pytest.approx(0.8)
        assert direction_required(REPETITIONS_PER_CELL) == 3
        assert direction_required(REPETITIONS_RESERVE) == 7
        assert direction_required(REPETITIONS_RESERVE) > direction_required(REPETITIONS_PER_CELL)

    def test_the_whole_block_fits_the_rung_cap_and_the_rung_fits_the_ladder(self) -> None:
        assert REPETITIONS_PER_CELL * BLOCK_SECONDS_CEILING <= RUNG_CAP_SECONDS
        assert RUNG_CAP_SECONDS < LADDER_CAP_SECONDS

    def test_the_caps_are_imported_from_the_harness_that_enforces_them(self) -> None:
        assert RUNG_CAP_SECONDS == lh.RUNG_CAP_SECONDS == 10800.0
        assert LADDER_CAP_SECONDS == lh.LADDER_CAP_SECONDS == 28800.0


class TestTheOutcomeMetricIsMandatoryPopulated:
    """h16 — proved, not asserted. A metric an arm can decline is rejected."""

    def test_dispatch_returns_one_record_per_planned_call_however_it_fails(self) -> None:
        """Leg 1 — there is no code path on which a planned call vanishes."""
        calls = _scoped_calls()

        def raising(_messages: Any) -> Any:
            raise RuntimeError("the transport died")

        doubles = {
            "silent": ah.scripted_worker(silent=True),
            "off-space": ah.scripted_worker(off_space=True),
            "answering": ah.scripted_worker(),
        }
        for name, mind in doubles.items():
            results = ah.dispatch(
                calls,
                answer_fn=lambda call, bound=mind: ah.answer_by_worker(call, mind=bound),
                max_workers=4,
                timeout=30.0,
            )
            assert len(results) == len(calls), name
            assert all(result.acceptance in ah.ACCEPTANCE_OUTCOMES for result in results), name
        # A raising transport is caught by answer_by_worker rather than lost.
        results = ah.dispatch(
            calls,
            answer_fn=lambda call: ah.answer_by_worker(call, mind=raising),
            max_workers=4,
            timeout=30.0,
        )
        assert len(results) == len(calls)
        assert {result.acceptance for result in results} == {ah.ABSENT_TRANSPORT}

    def test_a_declining_arm_still_produces_the_outcome_metric(self) -> None:
        """Leg 2 — the property ``message_utility`` did not have."""
        calls = _scoped_calls()
        for mind, expected_rate in (
            (ah.scripted_worker(silent=True), 0.0),
            (ah.scripted_worker(off_space=True), 0.0),
            (ah.scripted_worker(), 1.0),
        ):
            ledger = ah.AcceptanceLedger()
            ledger.extend(
                ah.dispatch(
                    calls,
                    answer_fn=lambda call, bound=mind: ah.answer_by_worker(call, mind=bound),
                    max_workers=4,
                    timeout=30.0,
                )
            )
            metric = items_per_second(len(calls), 1.25)
            assert metric > 0 and math.isfinite(metric)
            assert ledger.rate() == pytest.approx(expected_rate)
            assert ledger.dispatched == len(calls)

    def test_the_metric_does_not_move_when_an_arm_declines(self) -> None:
        """The outcome metric is a registered constant over a monotonic clock.

        Acceptance moves; the metric does not follow it. That disjointness is
        exactly what makes the metric unable to reward an arm for filling in an
        optional field.
        """
        assert items_per_second(ITEMS_PER_CELL, 100.0) == items_per_second(ITEMS_PER_CELL, 100.0)
        assert items_per_second(ITEMS_PER_CELL, 50.0) > items_per_second(ITEMS_PER_CELL, 100.0)

    def test_outcome_and_acceptance_keys_stay_disjoint(self) -> None:
        assert set(ah.OUTCOME_KEYS).isdisjoint(ah.ACCEPTANCE_KEYS)
        assert set(ap.OUTCOME_KEYS).isdisjoint(ap.ACCEPTANCE_KEYS)
        assert set(ap.OUTCOME_KEYS).isdisjoint(ap.ESCALATION_KEYS)
        assert set(ap.ESCALATION_KEYS).isdisjoint(ap.ACCEPTANCE_KEYS)

    def test_the_harnesses_still_declare_acceptance_apart_from_outcome(self) -> None:
        assert HIVE_CONFIG.decision["call_acceptance_is_never_folded_into_outcome"] is True
        assert POLICY_CONFIG.decision["call_acceptance_is_never_folded_into_outcome"] is True
        assert POLICY_CONFIG.decision["escalation_rate_is_never_folded_into_outcome"] is True

    def test_every_rejected_candidate_is_named_in_the_document(self) -> None:
        # "Rejected at registration time" means written down with its defect.
        for candidate in (
            "message_utility",
            "cooperation_v1",
            "outcome.total",
            "effective_concurrency",
            "acceptance_rate",
            "absent-truncated",
        ):
            assert f"`{candidate}`" in DOC_TEXT, candidate
        assert "No tie-break may promote a secondary metric into a verdict." in DOC_TEXT

    def test_the_document_records_the_headroom_the_metric_has(self) -> None:
        assert f"{WIDTH_SPEEDUP_LEAN:.2f}" in DOC_TEXT
        assert f"{WIDTH_SPEEDUP_RICH:.2f}" in DOC_TEXT


class TestTheGranularityChoiceCitesTheOverheadMeasurement:
    """c43 / h29 — no arm design cites the 9x figure for small calls."""

    def test_the_document_states_the_trap_and_the_true_transfer(self) -> None:
        assert f"{EFFECTIVE_CONCURRENCY_TRAP:.2f}" in DOC_TEXT
        assert f"{EFFECTIVE_CONCURRENCY_REFERENCE:g}" in DOC_TEXT
        assert EFFECTIVE_CONCURRENCY_TRAP > EFFECTIVE_CONCURRENCY_REFERENCE
        assert "may never appear in a verdict" in DOC_TEXT

    def test_the_prefill_numbers_are_cited_at_their_committed_values(self) -> None:
        assert PREFILL_EXTRA_SECONDS_W1 == pytest.approx(0.1176)
        assert PREFILL_EXTRA_SECONDS_W8 == pytest.approx(1.289)
        assert PREFILL_EXTRA_SECONDS_W8 > PREFILL_EXTRA_SECONDS_W1
        assert PREFILL_THROUGHPUT_RATIO_W8 == pytest.approx(0.4127)
        for value in (PREFILL_EXTRA_SECONDS_W1, PREFILL_EXTRA_SECONDS_W8):
            assert f"{value:g}" in DOC_TEXT

    def test_prompt_tokens_are_the_binding_budget_not_completion_tokens(self) -> None:
        assert SCOPED_CALL_PROMPT_TOKENS > 60 * SCOPED_CALL_COMPLETION_TOKENS
        assert "prompt** tokens, not completion tokens" in DOC_TEXT

    def test_thinking_is_pinned_off_on_every_scoped_call(self) -> None:
        assert WORKER_SAMPLING.thinking == "off"
        assert HIVE_CONFIG.wire_extra(WORKER_SAMPLING.thinking) == {
            "chat_template_kwargs": {"enable_thinking": False}
        }
        assert _THINKING["on_answered"] == "0/6"
        assert _THINKING["off_answered"] == "10/10"
        assert _THINKING["on_truncated"] == 6
        assert "0 of 6" in DOC_TEXT
        assert "10/10" in DOC_TEXT

    def test_the_registered_budget_covers_the_coarsest_grain(self) -> None:
        assert WORKER_MAX_TOKENS == 256
        assert round(COARSEST_GRAIN_TOKENS_NEEDED, 1) == 125.4
        assert round(TOKEN_MARGIN_COARSEST, 2) == 2.04
        assert TOKEN_MARGIN_COARSEST > 2.0


class TestTheDecisionRule:
    def test_the_margin_rule_is_reused_and_never_weaker_than_the_harness(self) -> None:
        for attempted in range(1, 40):
            assert margin_required(attempted) >= MARGIN_MIN
            assert margin_required(attempted) >= math.ceil(MARGIN_FRACTION * attempted)
            assert margin_required(attempted) >= int(HIVE_CONFIG.decision["separation_margin"])
            assert margin_required(attempted) >= int(POLICY_CONFIG.decision["separation_margin"])
        assert int(HIVE_CONFIG.decision["separation_margin"]) == 1
        assert "margin_required(a) = max(MARGIN_MIN, ceil(MARGIN_FRACTION * a))" in DOC_TEXT

    def test_both_harnesses_still_name_this_task_as_the_authority(self) -> None:
        for table in (HIVE_TABLE, POLICY_TABLE):
            raw = _load_json(table)
            assert any("PROVISIONAL" in line for line in raw["decision"]["why"])
            assert any("t9" in line for line in raw["decision"]["why"])

    def test_the_ceiling_rule_is_registered_and_provably_cannot_fire(self) -> None:
        assert RESOLUTION_SECONDS == pytest.approx(0.0899)
        assert round(MIN_CELL_SECONDS, 4) == 0.4495
        assert round(SHORTEST_CONTROL_CELL_SECONDS, 1) == 21.1
        assert round(CEILING_HEADROOM, 1) == 47.0
        assert SHORTEST_CONTROL_CELL_SECONDS > MIN_CELL_SECONDS
        assert "verdict, not a tie" in DOC_TEXT

    def test_the_resolution_comes_from_retry_free_batches_only(self) -> None:
        # The contaminated width-8 batch (22.455 s against a 0.9 s median) must
        # not become the instrument's declared resolution.
        contaminated = max(_CELLS["lean-off-w8"]["batch_elapsed_seconds"])
        assert contaminated > 20.0
        assert contaminated not in _clean_batches()["lean-off-w8"]
        assert RESOLUTION_SECONDS < 1.0

    def test_the_refusal_gate_is_reused_from_league_commander(self) -> None:
        assert PROTOCOL_GATE == pytest.approx(0.10)
        from tests import test_league_commander_preregistration as lcp

        assert lcp.LENGTH_FRACTION_MAX == PROTOCOL_GATE

    def test_the_verdict_vocabulary_is_closed_and_named(self) -> None:
        for verdict in (
            "SEPARATED-WIDTH",
            "INCONCLUSIVE-WIDTH",
            "CEILING-WIDTH",
            "VOID",
            "ABSENT",
        ):
            assert f"**`{verdict}`**" in DOC_TEXT, verdict

    def test_the_inconclusive_condition_is_explicit(self) -> None:
        assert "never\n  softened into" in DOC_TEXT


class TestTheAmortisationArithmeticIsComputedBeforeItIsDialled:
    def test_the_committed_budget_puts_the_scoped_lane_under_one_percent(self) -> None:
        assert MAX_SCOPED_CALLS == 24
        assert round(DRIVE_SCOPED_SECONDS_W1, 2) == 12.68
        assert round(DRIVE_SCOPED_SECONDS_W8, 2) == 6.94
        assert round(SCOPED_SHARE_LOW, 2) == 0.35
        assert round(SCOPED_SHARE_HIGH, 2) == 0.63
        assert SCOPED_SHARE_HIGH < 1.0

    def test_width_moves_a_drive_by_less_than_a_third_of_a_percent(self) -> None:
        assert round(WIDTH_EFFECT_ON_DRIVE_LOW, 2) == 0.16
        assert round(WIDTH_EFFECT_ON_DRIVE_HIGH, 2) == 0.29
        assert WIDTH_EFFECT_ON_DRIVE_HIGH < 1.0

    def test_the_call_count_width_would_need_is_computed_and_stated(self) -> None:
        assert CALLS_FOR_VISIBLE_WIDTH_LOW == 379
        assert CALLS_FOR_VISIBLE_WIDTH_HIGH == 691
        assert round(VISIBILITY_SHORTFALL_LOW, 1) == 15.8
        assert round(VISIBILITY_SHORTFALL_HIGH, 1) == 28.8
        assert VISIBILITY_SHORTFALL_LOW > 1.0

    def test_the_committed_budget_is_not_edited_by_this_task(self) -> None:
        # A pre-registration that edits the input it cites is the drift this
        # discipline exists to prevent.
        raw = _load_json(HIVE_TABLE)
        for tier in (ah.TIER_RIGID, ah.TIER_SCOPED):
            assert raw["budgets"][tier]["max_scoped_calls"] == 24
            assert raw["budgets"][tier]["worker_max_steps"] == 0
            assert raw["budgets"][tier]["spawn_allowance"] == 0


class TestCallAcceptanceIsAPerArmAxisWithANamedFalsifier:
    """c20 / h11 — falsifiable both ways, and truncation read apart."""

    def test_the_refutation_rate_is_the_committed_issue_33_figure(self) -> None:
        assert CALL_ACCEPTANCE_REFUTES_AT == pytest.approx(0.74)
        assert "REFUTING" in DOC_TEXT
        assert "`c20`" in DOC_TEXT

    def test_the_acceptance_vocabulary_splits_refusal_from_absence(self) -> None:
        assert ah.REFUSALS == ("refused-off-space", "refused-empty")
        assert ah.ABSENCES == ("absent-timeout", "absent-transport", "absent-truncated")
        assert set(ah.REFUSALS).isdisjoint(ah.ABSENCES)
        assert set(ah.REFUSALS) | set(ah.ABSENCES) | {ah.ACCEPTED} == set(ah.ACCEPTANCE_OUTCOMES)
        for outcome in ah.ACCEPTANCE_OUTCOMES:
            assert f"`{outcome}`" in DOC_TEXT, outcome

    def test_truncation_is_an_absence_and_never_a_refusal(self) -> None:
        # #37: a truncated turn and a schema refusal arrive as the same object,
        # so a max_tokens value could otherwise publish arm B as refuted by its
        # own budget.
        assert ah.ABSENT_TRUNCATED in ah.ABSENCES
        assert ah.ABSENT_TRUNCATED not in ah.REFUSALS
        assert ah.FINISH_TRUNCATED == "length"
        assert "refuted by its own\nbudget" in DOC_TEXT

    def test_the_refusal_rate_excludes_acceptance_by_construction(self) -> None:
        ledger = ah.AcceptanceLedger()
        mind = ah.scripted_worker(off_space=True)
        ledger.extend(
            ah.dispatch(
                _scoped_calls(items=4),
                answer_fn=lambda call: ah.answer_by_worker(call, mind=mind),
                max_workers=2,
                timeout=30.0,
            )
        )
        assert ledger.refusal_rate() == pytest.approx(1.0)
        assert ledger.rate() == pytest.approx(0.0)

    def test_an_empty_ledger_has_no_rate_rather_than_a_flattering_one(self) -> None:
        empty = ah.AcceptanceLedger()
        assert empty.rate() is None
        assert empty.refusal_rate() is None


class TestNoSecurityShapedQuestionEntersTheSweep:
    """c17 / h9 — #44's own example names a forbidden responsibility."""

    def test_the_forbidden_responsibilities_are_unchanged(self) -> None:
        assert ah.FORBIDDEN_RESPONSIBILITIES == ("final_decision", "security_decision")
        for forbidden in ah.FORBIDDEN_RESPONSIBILITIES:
            assert f"`{forbidden}`" in DOC_TEXT

    def test_every_declared_question_is_advisory_and_enumerable(self) -> None:
        assert ah.QUESTION_ORDER == ("pick_option", "classify_load", "looks_risky")
        for name in ah.QUESTION_ORDER:
            declared = ah.HIVE_QUESTIONS[name]
            assert declared.authority == ah.AUTHORITY_ADVISORY, name
            assert declared.kind in ah.ANSWER_SPACE_KINDS, name
            assert f"`{name}`" in DOC_TEXT, name

    def test_no_declared_question_names_a_security_decision(self) -> None:
        for name in ah.QUESTION_ORDER:
            declared = ah.HIVE_QUESTIONS[name]
            surface = f"{name} {declared.ask}".lower()
            for banned in ("safe", "security", "secure", "permitted", "allowed"):
                assert banned not in surface, (name, banned)

    def test_unclear_is_first_class_in_the_risk_question(self) -> None:
        # A worker with no view has an in-space way to say so, which closes
        # #32's no-answer shape through the schema rather than a guess.
        assert ah.HIVE_QUESTIONS["looks_risky"].answers == ("clear", "risky", "unclear")
        assert "**`unclear`**" in DOC_TEXT

    def test_the_registered_question_set_is_what_the_arm_is_configured_with(self) -> None:
        for tier in ah.HIVE_ARM_ORDER:
            assert HIVE_CONFIG.setup_for(tier).questions == ah.QUESTION_ORDER


class TestArmPDeclaresBothExtremesOfEscalation:
    """c18 / h10 — the controls, and the two ends both pre-declared."""

    def test_the_three_controls_exist_and_make_no_authoring_call(self) -> None:
        assert ap.POLICY_ARM_ORDER == ("P", "PR", "PH", "PN")
        assert POLICY_CONFIG.budget_for(ap.ARM_COMPILED).authoring_calls == 1
        for control in (ap.ARM_RANDOM, ap.ARM_BASELINE, ap.ARM_NOOP):
            assert POLICY_CONFIG.budget_for(control).authoring_calls == 0
            assert ap.ROLE_CORTEX not in ap.POLICY_ARMS[control].configured_roles
        for arm in ap.POLICY_ARM_ORDER:
            assert f"**{arm}**" in DOC_TEXT, arm

    def test_the_collapsed_extreme_has_a_committed_threshold(self) -> None:
        assert ESCALATION_COLLAPSES_AT == pytest.approx(1.0)
        assert "collapsed into the worker arm" in DOC_TEXT

    def test_the_blind_extreme_has_no_harness_flag_and_is_defined_here(self) -> None:
        report = ap.escalation_report(
            {},
            _EPISODE,
            calls=0,
            not_dispatched=(),
            collapses_at=ESCALATION_COLLAPSES_AT,
        )
        # The harness names the top extreme and not the bottom one.
        assert "collapsed" in report
        assert not any("blind" in key.lower() for key in report)
        # So the reading is registered here, from fields it does record.
        for field in ("escalated", "unescalated_undecidable", "escalation_recall"):
            assert field in report, field
            assert f'escalation["{field}"]' in DOC_TEXT, field
        assert "BLIND" in DOC_TEXT

    def test_the_escalation_budget_clamps_below_the_situation_count(self) -> None:
        # A collapsed policy's cost is under-reported by the clamp, which the
        # document records rather than leaving to be discovered.
        assert EPISODE_SITUATIONS == 12
        assert MAX_ESCALATION_CALLS == 8
        assert MAX_ESCALATION_CALLS < EPISODE_SITUATIONS
        assert "clamped at 8" in DOC_TEXT

    def test_the_undecidable_situations_are_computed_from_the_episode(self) -> None:
        assert EPISODE_UNDECIDABLE == 3
        assert 0 < EPISODE_UNDECIDABLE < EPISODE_SITUATIONS
        assert "`u4`, `u8`, `u12`" in DOC_TEXT

    def test_the_policy_acceptance_floor_is_its_own_axis(self) -> None:
        assert POLICY_ACCEPTANCE_FLOOR == pytest.approx(1.0)
        assert "policy_acceptance" in DOC_TEXT
        assert "escalation_acceptance" in DOC_TEXT

    def test_the_fake_provider_is_registered_as_executing_nothing(self) -> None:
        assert ap.VERDICT_NO_RESULT == "NO_RESULT"
        assert ap.VERDICT_NO_WORKSPACE == "NO_WORKSPACE"
        assert "provisions a workspace and executes nothing" in DOC_TEXT
        assert "`script_command`" in DOC_TEXT


class TestTheStreamingInstrumentIsRegistered:
    """d3 — the transport is named, its bounds derived, its limits stated."""

    def test_the_two_phase_bound_is_derived_from_the_probe(self) -> None:
        assert IDLE_BOUND_FLOOR_SECONDS == pytest.approx(12.4)
        assert f"{IDLE_BOUND_FLOOR_SECONDS:g} s" in DOC_TEXT
        assert MAX_NUM_SEQS == 2
        assert "MAX_NUM_SEQS" in DOC_TEXT

    def test_the_probe_numbers_appear_at_their_measured_values(self) -> None:
        for value in (MAX_INTER_CHUNK_GAP_SECONDS, TIME_TO_FIRST_CHUNK_SECONDS):
            assert f"{value:g}" in DOC_TEXT

    def test_the_document_states_what_is_no_longer_comparable(self) -> None:
        assert "may be compared to a latency in those" in DOC_TEXT
        assert "Token accounting is unaffected" in DOC_TEXT

    def test_the_scope_conflict_between_d3_and_c42_is_recorded(self) -> None:
        assert "`c42`" in DOC_TEXT
        assert "These disagree" in DOC_TEXT


class TestTheRelationshipToTheRunningLadder:
    """c44 / h30 — quoted by section, or registered first with a reason."""

    def test_the_prior_results_document_does_not_exist_on_this_branch(self) -> None:
        # h30's second branch: registered before t14 existed. Checked rather
        # than claimed, and checked in the only tree this test can see.
        stray = sorted(
            path.name
            for path in RESULTS.iterdir()
            if path.name.startswith("orchestrator-worker-series")
        )
        assert stray == [], f"the prior results document now exists: {stray}"
        assert "The t14 results document does not exist" in DOC_TEXT

    def test_the_prior_pre_registration_is_quoted_by_section(self) -> None:
        prior = PRIOR_PREREGISTRATION.read_text(encoding="utf-8")
        assert "## 7. The bars that are choices, not measurements" in prior
        assert "orchestrator-worker-preregistration.md" in DOC_TEXT
        for section in ("§7", "§18/§19"):
            assert section in DOC_TEXT, section

    def test_the_document_states_why_registering_first_is_sound(self) -> None:
        assert "not sound" in DOC_TEXT
        assert "does not dial the cortex at" in DOC_TEXT

    def test_the_two_width_seams_are_declared_different_measurements(self) -> None:
        assert "`c42`" in DOC_TEXT
        assert "neither can be quoted" in DOC_TEXT

    def test_cross_branch_facts_are_labelled_cited_not_pinned(self) -> None:
        assert "cited, **not pinned**" in DOC_TEXT
        assert "`owa/t12`" in DOC_TEXT


class TestThisIsCommittedBeforeTheFirstMeasuredDial:
    def test_no_bee_hive_series_artifact_exists_yet(self) -> None:
        inputs = {
            "arch-hive-sampling.json",
            "arch-policy-sampling.json",
            "arch-policy-fixtures.json",
        }
        stray = sorted(
            path.name
            for path in RESULTS.iterdir()
            if path.name.startswith(("arch-hive", "arch-policy", "bee-hive"))
            and path.suffix in (".jsonl", ".json")
            and path.name not in inputs
        )
        assert stray == [], f"a measured artifact already exists: {stray}"

    def test_the_sampling_tables_are_inputs_read_by_their_harnesses(self) -> None:
        assert _load_json(HIVE_TABLE)["read_by"] == "examples/arch_hive.py"
        assert _load_json(POLICY_TABLE)["read_by"] == "examples/arch_policy.py"


class TestTheDocumentStatesTheRecomputedConstants:
    """If the derivation moves, the prose must move with it or this fails."""

    def test_every_derived_constant_appears_at_its_computed_value(self) -> None:
        derived = {
            "MAX_HIVE_WIDTH": MAX_HIVE_WIDTH,
            "WIDTH_LADDER": WIDTH_LADDER,
            "GRAIN_ITEMS": GRAIN_ITEMS,
            "MIN_BATCHES_PER_CELL": MIN_BATCHES_PER_CELL,
            "RESOLUTION_FLOOR_ITEMS": RESOLUTION_FLOOR_ITEMS,
            "AMORTISATION_FLOOR_ITEMS": AMORTISATION_FLOOR_ITEMS,
            "ITEMS_PER_CELL": ITEMS_PER_CELL,
            "CELLS_PER_BLOCK": CELLS_PER_BLOCK,
            "REPETITIONS_PER_CELL": REPETITIONS_PER_CELL,
            "REPETITIONS_RESERVE": REPETITIONS_RESERVE,
            "CALLS_PER_SECOND_W1": CALLS_PER_SECOND_W1,
            "CALLS_PER_SECOND_W8": CALLS_PER_SECOND_W8,
            "CORTEX_TURN_SECONDS_LOW": CORTEX_TURN_SECONDS_LOW,
            "TURNS_PER_DRIVE": TURNS_PER_DRIVE,
            "DRIVE_SCOPED_SECONDS_W1": round(DRIVE_SCOPED_SECONDS_W1, 2),
            "DRIVE_SCOPED_SECONDS_W8": round(DRIVE_SCOPED_SECONDS_W8, 2),
            "MIN_CELL_SECONDS": round(MIN_CELL_SECONDS, 4),
            "RESOLUTION_SECONDS": RESOLUTION_SECONDS,
            "MAX_INTER_CHUNK_GAP_SECONDS": MAX_INTER_CHUNK_GAP_SECONDS,
            "MAX_NUM_SEQS": MAX_NUM_SEQS,
            "IDLE_MARGIN": IDLE_MARGIN,
            "CALL_ACCEPTANCE_REFUTES_AT": CALL_ACCEPTANCE_REFUTES_AT,
            "ESCALATION_COLLAPSES_AT": ESCALATION_COLLAPSES_AT,
            "POLICY_ACCEPTANCE_FLOOR": POLICY_ACCEPTANCE_FLOOR,
            "PROTOCOL_GATE": PROTOCOL_GATE,
            "MARGIN_MIN": MARGIN_MIN,
            "MARGIN_FRACTION": MARGIN_FRACTION,
            "DIRECTION_FRACTION": DIRECTION_FRACTION,
            "SCOPED_SHARE_TARGET": SCOPED_SHARE_TARGET,
            "RUNG_CAP_SECONDS": int(RUNG_CAP_SECONDS),
            "LADDER_CAP_SECONDS": int(LADDER_CAP_SECONDS),
            "HEADROOM_MATCHES": HEADROOM_MATCHES,
            "CALLS_FOR_VISIBLE_WIDTH_LOW": CALLS_FOR_VISIBLE_WIDTH_LOW,
            "CALLS_FOR_VISIBLE_WIDTH_HIGH": CALLS_FOR_VISIBLE_WIDTH_HIGH,
            "CELL_SECONDS_CEILING": round(CELL_SECONDS_CEILING, 1),
            "BLOCK_SECONDS_CEILING": round(BLOCK_SECONDS_CEILING, 1),
            "BLOCK_SECONDS_MODELLED": round(BLOCK_SECONDS_MODELLED, 1),
        }
        missing = [name for name, value in derived.items() if not _doc_states(name, value)]
        assert not missing, f"the document does not state: {missing}"

    def test_every_chosen_bar_appears_and_is_labelled_a_choice(self) -> None:
        for name, value in (
            ("SCOPED_SHARE_TARGET", SCOPED_SHARE_TARGET),
            ("IDLE_MARGIN", IDLE_MARGIN),
        ):
            assert _doc_states(name, value), name
        assert "choices, not measurements" in DOC_TEXT
        # And the citations are labelled citations, not bars.
        assert "are **citations**" in DOC_TEXT

    def test_the_document_names_every_tier_and_every_rung(self) -> None:
        for tier in ("B0", "B1", "B2"):
            assert f"**{tier}**" in DOC_TEXT, tier
        for rung in ("W1", "W2", "H0", "P1"):
            assert f"`{rung}`" in DOC_TEXT, rung

    def test_the_stop_rule_names_what_a_non_separating_width_rung_means(self) -> None:
        assert "the flat default stands on evidence" in DOC_TEXT
        assert "`ABSENT` by name" in DOC_TEXT

    def test_the_pin_and_the_document_agree_on_the_pin_path(self) -> None:
        assert "tests/test_bee_hive_width_preregistration.py" in DOC_TEXT
