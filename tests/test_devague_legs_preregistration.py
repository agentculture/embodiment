"""Pre-registered thresholds for the devague-legs experiment (issue #20, plan task t6).

The prose argument lives in
``docs/live-test-results/devague-legs-preregistration.md`` — read that first.
This file is the pin, not the argument: every literal constant the eventual
run (task t9) must honour is defined here, at module scope, and asserted by
value below, exactly as ``tests/test_association_work.py::TestPreRegisteredThresholds``
pins ``examples/association_work.py``'s constants.

The difference from that precedent is deliberate and stated once, here, so it
is not mistaken for an oversight: task t6 pre-registers the experiment
*before* task t9 builds its harness (``examples/`` is t9's to own — see the
plan's task instruction for t6, "Do not create the experiment harness
itself"). With no harness module yet to hold the constants, this test file
plays that role for the duration of the pre-registration. When t9 lands its
harness it either imports these names directly, or defines its own copies and
asserts them equal to these in the same diff. Either way, changing a
threshold after this commit means editing a test that says so out loud in a
diff a reviewer sees — the whole point of the discipline this file exists to
enforce.

Nothing in this file executes a live run, calls the ``devague`` CLI, or reads
network/rig state. It reads two already-committed JSON ledgers
(``.devague/deliveries/*.json``) to ground the ``/deviate`` leg's case pools
in real history, and otherwise asserts literal Python values against literal
Python values.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# --- Instrument pin (requirement 6 of the t6 brief; frame claims c13, c41,
# honesty conditions h14, h35) ---------------------------------------------
PINNED_DEVAGUE_VERSION = "0.22.0"

# --- The leg split (requirement 1; next-cycle-candidates.md:118-215) -------
LEG_ASSIGNMENTS = {
    "scope": "cortex-reads-muse-proposes",
    "think": "either",
    "challenge": "muse",
    "spec_to_plan": "cortex",
    "assign_to_workforce": "cortex",
    "deviate": "muse",
}
CHEAPEST_FIRST_LEG = "deviate"

# --- Experiment 1: the same-mind control on /think + /spec-to-plan
# (requirement 2) -----------------------------------------------------------
# Fixed now, not chosen after seeing which topic makes an arm look better.
# See the pre-registration doc's "Topics, fixed now" section for the
# screening that ruled out #4 and #6 (both already delivered despite still
# showing OPEN on GitHub) before these two were picked.
TOPIC_ISSUES = (5, 9)
N_TOPICS = len(TOPIC_ISSUES)

ARM_CROSS = "cross"  # Qwen plans from Gemma's human-confirmed frame
ARM_CONTROL = "control"  # Qwen plans from its own frame, solo end to end
ARMS = (ARM_CROSS, ARM_CONTROL)

# --- Gate DVs and their literal thresholds (requirements 3 and 4) ----------
MAX_GATE_ITERATIONS = 5
MIN_GATE_ITERATION_MARGIN = 1
MIN_GAP_MARGIN = 2
# Reused verbatim from association-work-preregistration.md's V2 — the same
# honest fraction, not re-derived, because the reasoning (a comparison should
# not be quietly carried by a arm's tooling failures) is unchanged by domain.
MAX_ERROR_FRACTION = 1.0 / 3.0

# --- The decision vocabulary (requirement 5) -------------------------------
DECISION_SUPPORTS_CROSS = "SUPPORTS-CROSS"
DECISION_SUPPORTS_CONTROL = "SUPPORTS-CONTROL"
DECISION_NEGATIVE = "NEGATIVE"
DECISION_INCONCLUSIVE = "INCONCLUSIVE"
DECISIONS = (
    DECISION_SUPPORTS_CROSS,
    DECISION_SUPPORTS_CONTROL,
    DECISION_NEGATIVE,
    DECISION_INCONCLUSIVE,
)

# --- Experiment 2: the /deviate cheapest-first slice -----------------------
# Both pools are real, already-committed history — six approved deviations
# and six delivered-and-untouched tasks, drawn from this repo's own
# .devague/deliveries/ ledger. Ground truth cannot move after this commit
# because the ledger they cite is itself already committed.
DEVIATE_POSITIVE_CASES = (
    "gwen-loop-presence-continuity:d1",
    "gwen-loop-presence-continuity:d2",
    "gwen-loop-presence-continuity:d3",
    "gwen-loop-presence-continuity:d4",
    "gwen-loop-presence-continuity:d5",
    "function-first-loops-muse-redesign:d1",
)
DEVIATE_NEGATIVE_CASES = (
    "gwen-loop-presence-continuity:t1",
    "function-first-loops-muse-redesign:t1",
    "gwen-loop-presence-continuity:t3",
    "function-first-loops-muse-redesign:t2",
    "gwen-loop-presence-continuity:t5",
    "function-first-loops-muse-redesign:t3",
)

# --- The lapse protocol vocabulary (requirement 7) -------------------------
# Pinned so a future devague upgrade that renames or drops a code is a loud,
# explained diff here rather than a silent gap in the run protocol.
LAPSE_CODES = (
    "assumption-for-measurement",
    "grader-unverified",
    "control-absent",
    "n-below-claim",
    "instrument-changed-mid-series",
    "provenance-missing",
)

# --- The Gemma-proposes-human-confirms pipeline (requirement 8) ------------
LLM_ORIGIN_STATUS = "proposed"
HUMAN_CONFIRM_STATUS = "confirmed"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DELIVERY_LEDGERS = {
    "gwen-loop-presence-continuity": (
        _REPO_ROOT / ".devague/deliveries/gwen-loop-presence-continuity.json"
    ),
    "function-first-loops-muse-redesign": (
        _REPO_ROOT / ".devague/deliveries/function-first-loops-muse-redesign.json"
    ),
}


class TestPreRegisteredThresholds:
    """The numbers were fixed before the first dial. Changing one is loud.

    Mirrors ``tests/test_association_work.py::TestPreRegisteredThresholds``:
    an experiment whose threshold moves after a result is not evidence, it is
    a story told about a number. These assertions exist so that moving a
    threshold is a deliberate, reviewable act rather than an edit nobody
    notices.
    """

    def test_pinned_devague_version_is_0_22_0(self) -> None:
        assert PINNED_DEVAGUE_VERSION == "0.22.0"

    def test_the_leg_split_matches_next_cycle_candidates_tier_2b(self) -> None:
        assert LEG_ASSIGNMENTS == {
            "scope": "cortex-reads-muse-proposes",
            "think": "either",
            "challenge": "muse",
            "spec_to_plan": "cortex",
            "assign_to_workforce": "cortex",
            "deviate": "muse",
        }

    def test_deviate_is_named_the_cheapest_first_leg(self) -> None:
        assert CHEAPEST_FIRST_LEG == "deviate"
        assert LEG_ASSIGNMENTS[CHEAPEST_FIRST_LEG] == "muse"

    def test_topic_issues_are_fixed_before_the_first_dial(self) -> None:
        assert TOPIC_ISSUES == (5, 9)
        assert N_TOPICS == 2

    def test_arms_are_cross_and_control(self) -> None:
        assert ARMS == ("cross", "control")

    def test_gate_iteration_thresholds(self) -> None:
        assert MAX_GATE_ITERATIONS == 5
        assert MIN_GATE_ITERATION_MARGIN == 1

    def test_gap_margin_threshold(self) -> None:
        assert MIN_GAP_MARGIN == 2

    def test_error_fraction_is_reused_from_association_work(self) -> None:
        assert MAX_ERROR_FRACTION == pytest.approx(1.0 / 3.0)

    def test_decision_vocabulary_is_closed(self) -> None:
        assert DECISIONS == (
            "SUPPORTS-CROSS",
            "SUPPORTS-CONTROL",
            "NEGATIVE",
            "INCONCLUSIVE",
        )

    def test_deviate_case_pools_are_six_and_six_and_disjoint(self) -> None:
        assert len(DEVIATE_POSITIVE_CASES) == 6
        assert len(DEVIATE_NEGATIVE_CASES) == 6
        assert set(DEVIATE_POSITIVE_CASES).isdisjoint(DEVIATE_NEGATIVE_CASES)

    def test_deviate_case_pools_are_the_committed_cases(self) -> None:
        assert DEVIATE_POSITIVE_CASES == (
            "gwen-loop-presence-continuity:d1",
            "gwen-loop-presence-continuity:d2",
            "gwen-loop-presence-continuity:d3",
            "gwen-loop-presence-continuity:d4",
            "gwen-loop-presence-continuity:d5",
            "function-first-loops-muse-redesign:d1",
        )
        assert DEVIATE_NEGATIVE_CASES == (
            "gwen-loop-presence-continuity:t1",
            "function-first-loops-muse-redesign:t1",
            "gwen-loop-presence-continuity:t3",
            "function-first-loops-muse-redesign:t2",
            "gwen-loop-presence-continuity:t5",
            "function-first-loops-muse-redesign:t3",
        )

    def test_lapse_codes_match_devague_0_22_0s_six_codes(self) -> None:
        assert LAPSE_CODES == (
            "assumption-for-measurement",
            "grader-unverified",
            "control-absent",
            "n-below-claim",
            "instrument-changed-mid-series",
            "provenance-missing",
        )

    def test_gemma_proposes_human_confirms_status_vocabulary(self) -> None:
        assert LLM_ORIGIN_STATUS == "proposed"
        assert HUMAN_CONFIRM_STATUS == "confirmed"
        assert LLM_ORIGIN_STATUS != HUMAN_CONFIRM_STATUS


class TestDeviateCasePoolsAreGroundedInCommittedHistory:
    """Grounded, not invented: no case in either pool is a plausible-looking
    literal — each resolves to a real record already sitting in this repo's
    ``.devague/deliveries/`` ledger. This is the vacuity assertion the M2
    lesson (next-cycle-candidates.md:79-101) asks for, applied to our own
    pre-registration rather than only to graders we write for others: proof
    the case pool actually means what the doc claims it means, not an
    assumption that it does.
    """

    @staticmethod
    def _load(plan: str) -> dict:
        return json.loads(_DELIVERY_LEDGERS[plan].read_text(encoding="utf-8"))

    def test_every_positive_case_is_an_approved_deviation(self) -> None:
        for case in DEVIATE_POSITIVE_CASES:
            plan, dev_id = case.split(":")
            record = self._load(plan)
            deviations = {d["id"]: d for d in record["deviations"]}
            assert dev_id in deviations, f"{case} is not in {plan}'s delivery ledger"
            assert deviations[dev_id]["status"] == "approved", case

    def test_every_negative_case_is_untouched_by_any_deviation(self) -> None:
        for case in DEVIATE_NEGATIVE_CASES:
            plan, task_ref = case.split(":")
            record = self._load(plan)
            touched: set[str] = set()
            for deviation in record["deviations"]:
                if deviation.get("task_ref"):
                    touched.add(deviation["task_ref"])
                touched.update(deviation.get("affects", []))
            assert task_ref not in touched, f"{case} is touched by a deviation"

    def test_the_two_delivery_ledgers_exist_and_are_readable(self) -> None:
        for path in _DELIVERY_LEDGERS.values():
            assert path.is_file(), path
            record = json.loads(path.read_text(encoding="utf-8"))
            assert record["deviations"], "expected at least one recorded deviation"
