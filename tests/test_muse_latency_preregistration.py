"""Pre-registered thresholds for the tool-session latency probe (plan task t16).

The prose argument lives in
``docs/live-test-results/muse-latency-preregistration.md`` — read that first.
This file is the pin, not the argument: every literal constant the measured run
must honour is defined here, at module scope, and asserted by value below,
following ``tests/test_devague_legs_preregistration.py`` exactly.

The same two-step the devague-legs pre-registration used applies here, and for
the same reason. This file lands **before** ``examples/muse_latency.py``, so for
the duration of the pre-registration it is where the constants live. When the
probe lands it defines its own copies and
:class:`TestHarnessMatchesThePreRegistration` asserts they are equal — so
changing a threshold after the first dial means editing a test that says so out
loud in a diff a reviewer sees, which is the whole discipline.

Nothing here executes a live run, dials the rig, or reads network state. It
asserts literal Python values against literal Python values, plus one
derivation (``KEEP_THRESHOLD`` from ``SERIES_CONFIDENCE`` and ``MIN_DRIVES``)
recomputed from its inputs so the number cannot drift away from its reasoning.
"""

from __future__ import annotations

import math

import pytest

# --- The arms (lane 1) -----------------------------------------------------
# ``pad-4`` is the SHIPPED tools-on configuration. ``primed-4`` is declared an
# upper bound, never the shipped profile: it appends one sentence to the host
# framing so the pad is actually used, because a muse that declines to call a
# tool answers a different question than the one c31 asks.
ARM_OFF_2 = "off-2"
ARM_OFF_4 = "off-4"
ARM_PAD_4 = "pad-4"
ARM_PRIMED_4 = "primed-4"
ARMS = (ARM_OFF_2, ARM_OFF_4, ARM_PAD_4, ARM_PRIMED_4)

#: Which arm's numbers decide the verdict, and the fallback when the shipped
#: arm never puts a tool on the wire (validity gate V3).
GOVERNING_ARM_PRIMARY = ARM_PAD_4
GOVERNING_ARM_FALLBACK = ARM_PRIMED_4

#: Arms whose label claims tools, and which V3 therefore holds to account.
TOOLS_ON_ARMS = (ARM_PAD_4, ARM_PRIMED_4)

#: The one sentence appended to the host framing in the upper-bound arm. Fixed
#: now so it cannot be reworded after seeing which wording produces tool calls.
PRIMING = (
    "Before you comment, record your thinking on the pad: write an intend entry "
    "for what you are about to consider, and an observe or conclude entry for "
    "what you make of it. Use the tools; do not describe using them."
)

# --- The cell configuration (lane 1) ---------------------------------------
#: ``proof.py``'s ``MUSE_MAX_TURNS`` — the budget the 4-of-4 late-drop baseline
#: was measured under, and the budget lane 2's drives run at.
MUSE_MAX_TURNS_BASELINE = 2
#: ``MuseControls``' own default — the budget a tools-on default would ship at.
MUSE_MAX_TURNS_DEFAULT = 4
#: ``MuseControls.max_tool_rounds``' default.
MUSE_MAX_TOOL_ROUNDS = 3
#: ``proof.py``'s ``MUSE_MAX_TOKENS`` and ``DEFAULT_TEMPERATURE``, so this probe
#: and the baseline harness put the same numbers on the wire.
MUSE_MAX_TOKENS = 1200
TEMPERATURE = 0.3

#: (tools_on, host_framing_key, max_turns) per arm.
ARM_CONFIG = {
    ARM_OFF_2: (False, "none", MUSE_MAX_TURNS_BASELINE),
    ARM_OFF_4: (False, "none", MUSE_MAX_TURNS_DEFAULT),
    ARM_PAD_4: (True, "pad", MUSE_MAX_TURNS_DEFAULT),
    ARM_PRIMED_4: (True, "pad+priming", MUSE_MAX_TURNS_DEFAULT),
}

# --- Sample sizes (both lanes) ---------------------------------------------
#: The acceptance criterion's own floors: "a series under four drives or an arm
#: under n=6 reports INCONCLUSIVE".
MIN_DRIVES = 4
MIN_SESSIONS_PER_ARM = 6
#: What the probe actually runs.
N_DRIVES = 4
N_SEQUENCES = 2
BOUNDARY_STEPS = (3, 7, 11, 15)
N_SESSIONS_PER_ARM = N_SEQUENCES * len(BOUNDARY_STEPS)
#: The subset the conservative estimator uses — the two largest boundary steps,
#: because the session at risk is by construction the last one started.
LATE_BOUNDARY_STEPS = BOUNDARY_STEPS[-2:]

# --- Thresholds ------------------------------------------------------------
#: The probability a reachable target should have of being met when the
#: mechanism is working.
SERIES_CONFIDENCE = 0.8
#: Per-drive completion probability required to KEEP the zero-late-drop target.
#: DERIVED: at most one session per drive can strand, so a zero-late-drop series
#: across MIN_DRIVES drives succeeds with probability p ** MIN_DRIVES; requiring
#: that to reach SERIES_CONFIDENCE gives p >= 0.8 ** 0.25 = 0.9457..., rounded
#: up to two decimals.
KEEP_THRESHOLD = 0.95
#: Reused verbatim from ``association-work-preregistration.md``'s V2 and
#: ``devague-legs-preregistration.md``.
MAX_ERROR_FRACTION = 1 / 3
#: An arm labelled tools-on must put tool calls on the wire in at least this
#: fraction of its sessions, or the label is a claim rather than a condition.
MIN_TOOL_EXERCISE_FRACTION = 0.5

DECISIONS = ("KEEP", "REPLACE", "INCONCLUSIVE")


class TestPreRegisteredThresholds:
    """Every literal, asserted by value. Editing one edits this file."""

    def test_the_four_arms_are_fixed(self) -> None:
        assert ARMS == ("off-2", "off-4", "pad-4", "primed-4")
        assert TOOLS_ON_ARMS == ("pad-4", "primed-4")
        assert set(ARM_CONFIG) == set(ARMS)

    def test_the_governing_arm_is_the_shipped_one_with_a_named_fallback(self) -> None:
        assert GOVERNING_ARM_PRIMARY == "pad-4"
        assert GOVERNING_ARM_FALLBACK == "primed-4"
        assert GOVERNING_ARM_PRIMARY in TOOLS_ON_ARMS
        assert GOVERNING_ARM_FALLBACK in TOOLS_ON_ARMS

    def test_the_cell_configuration_is_fixed(self) -> None:
        assert ARM_CONFIG == {
            "off-2": (False, "none", 2),
            "off-4": (False, "none", 4),
            "pad-4": (True, "pad", 4),
            "primed-4": (True, "pad+priming", 4),
        }
        assert MUSE_MAX_TURNS_BASELINE == 2
        assert MUSE_MAX_TURNS_DEFAULT == 4
        assert MUSE_MAX_TOOL_ROUNDS == 3
        assert MUSE_MAX_TOKENS == 1200
        assert TEMPERATURE == pytest.approx(0.3)

    def test_the_priming_sentence_is_fixed_before_the_dial(self) -> None:
        assert PRIMING.startswith("Before you comment, record your thinking on the pad")
        assert "Use the tools; do not describe using them." in PRIMING

    def test_the_sample_sizes_meet_the_acceptance_criterion_floors(self) -> None:
        assert MIN_DRIVES == 4
        assert MIN_SESSIONS_PER_ARM == 6
        assert N_DRIVES >= MIN_DRIVES
        assert N_SESSIONS_PER_ARM >= MIN_SESSIONS_PER_ARM
        assert N_SESSIONS_PER_ARM == N_SEQUENCES * len(BOUNDARY_STEPS) == 8
        assert BOUNDARY_STEPS == (3, 7, 11, 15)
        assert LATE_BOUNDARY_STEPS == (11, 15)

    def test_the_keep_threshold_is_derived_not_chosen(self) -> None:
        derived = SERIES_CONFIDENCE ** (1 / MIN_DRIVES)
        assert derived == pytest.approx(0.94574, abs=1e-5)
        # Rounded UP to two decimals: a threshold below its own derivation would
        # let a target through that fails the series more often than
        # SERIES_CONFIDENCE allows.
        assert KEEP_THRESHOLD == math.ceil(derived * 100) / 100 == 0.95

    def test_the_remaining_thresholds_are_fixed(self) -> None:
        assert SERIES_CONFIDENCE == pytest.approx(0.8)
        assert MAX_ERROR_FRACTION == pytest.approx(1 / 3)
        assert MIN_TOOL_EXERCISE_FRACTION == pytest.approx(0.5)

    def test_every_outcome_is_publishable(self) -> None:
        assert DECISIONS == ("KEEP", "REPLACE", "INCONCLUSIVE")
        assert "INCONCLUSIVE" in DECISIONS
