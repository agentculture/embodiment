"""Pre-registered thresholds for the League commander experiment (plan task t28).

The prose argument lives in
``docs/live-test-results/league-commander-preregistration.md`` — read that
first. This file is the pin, not the argument: every literal constant the
measured run must honour is defined here, at module scope, and asserted by
value below, following ``tests/test_muse_latency_preregistration.py`` and
``tests/test_devague_legs_preregistration.py`` exactly.

The harness (``examples/league_commander.py``) defines its own copies, and
:class:`TestHarnessMatchesThePreRegistration` asserts they are equal — so
changing a threshold after the first dial means editing a test that says so out
loud in a diff a reviewer sees, which is the whole discipline.

Nothing here executes a live run, dials the rig, or reads network state. It
asserts literal Python values against literal Python values.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import league_commander as lc  # noqa: E402

# --- The arms --------------------------------------------------------------
# B is the operator's proposal. C is the MIRROR and is mandatory: B moves both
# who holds final authority and which model holds it, so without C a B win is
# unattributable. The two flat arms are what say whether hierarchy helps at all.
ARM_B = "B"
ARM_C = "C"
ARM_A_QWEN = "A-qwen"
ARM_A_GEMMA = "A-gemma"
ARMS = (ARM_B, ARM_C, ARM_A_QWEN, ARM_A_GEMMA)
HIERARCHICAL_ARMS = (ARM_B, ARM_C)
FLAT_ARMS = (ARM_A_QWEN, ARM_A_GEMMA)

# --- The models, addressed by id, never inferred from a name ---------------
QWEN = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
GEMMA = "nvidia/Gemma-4-31B-IT-NVFP4"

#: ``arm -> (commander model or None, unit model)``. ``None`` is the flat arm's
#: whole claim: no commander exists, one model decides every unit.
ARM_MODELS = {
    ARM_B: (GEMMA, QWEN),
    ARM_C: (QWEN, GEMMA),
    ARM_A_QWEN: (None, QWEN),
    ARM_A_GEMMA: (None, GEMMA),
}

# --- The arena -------------------------------------------------------------
SCENARIO = "c-skirmish-1"
ESCALATION_SCENARIO = "c-frontier-1"
#: The FIXED house-bot opponent, identical in every arm.
OPPONENT_DRIVER = "bot"
#: league's own word for "a fresh drive per decision point", which is what the
#: harness does. Recorded in league's match-log header, so the arm's residency
#: is checkable from the arena's artifact and not only from ours.
OUR_DRIVER = "stateless"
SEEDS = (101, 102, 103, 104, 105)
N_MATCHES = 5
ESCALATION_N = 3

# --- The wiring ------------------------------------------------------------
#: Strictly positive or no child can exist (``NO_SPAWNS`` is the default).
#: Exactly 1: the child is minted with ``attenuate(1) == 0``, so depth is
#: bounded at 1 and the whole subtree at ``2**1 - 1 == 1``.
SPAWN_ALLOWANCE = 1
COMMANDER_MAX_STEPS = 6
UNIT_MAX_STEPS = 3
FLAT_MAX_STEPS = 4

# --- What goes on the wire -------------------------------------------------
#: Mandated for any Qwen level; used for BOTH models so the cap is not an
#: asymmetry anyone has to reason about.
MAX_TOKENS = 16000
#: Identical in every arm. Non-zero because ``--seed`` is metadata only on
#: ``c-skirmish-1`` (verified: two seeds, byte-identical initial state), so
#: sampling is the ONLY source of between-match variation within an arm.
TEMPERATURE = 0.7
#: What this document REGISTERED, and what both committed series ran under. It
#: stays here unchanged: a pre-registration's numbers are not edited after the
#: dial, and the re-exam's whole verdict is stated against this clock.
REQUEST_TIMEOUT = 900.0
#: What the harness ships TODAY, per **Amendment 1** (appended 2026-08-01,
#: after both runs and after the write-up published). The registered value was
#: derived at the Qwen cortex rate while the same constant also fronts Gemma 4
#: 31B at the same budget; at Gemma's measured rate 900.0 was 0.68x its bound.
#: No published figure changes — the re-exam proved no call was cut — but the
#: constant was wrong on its own terms and the next run would have paid for it.
#: `tests/test_timeout_bounds.py` is the gate that now recomputes it.
REQUEST_TIMEOUT_AMENDED = 1600.0
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 30.0
#: Above this, a call is CONTENTION, not a result.
CONTENTION_SECONDS = 600.0

# --- The decision rule -----------------------------------------------------
#: Mean-margin difference that counts as an effect.
MARGIN_EFFECT = 3.0
#: Mean-margin difference below which "no effect" may be claimed — but only
#: when the two arms' margin sets differ, i.e. the instrument had variance.
NO_EFFECT_BAND = 1.0
#: Matches out of ``N_MATCHES`` that must point the same way.
DIRECTION_MIN = 4
#: The same bar at any n, derived from its own inputs rather than written as a
#: second literal, so escalation E1's smaller n cannot get an easier rule.
DIRECTION_FRACTION = DIRECTION_MIN / N_MATCHES
#: Escalation E3's threshold on the secondary metric (total blue unit grade).
GRADE_EFFECT = 200.0

# --- Validity gates --------------------------------------------------------
LENGTH_FRACTION_MAX = 0.10
NO_ORDER_FRACTION_MAX = 0.20
SPAWN_GRANT_MIN_FRACTION = 0.90

# --- The verdict vocabulary ------------------------------------------------
VERDICT_EFFECT = "EFFECT"
VERDICT_NO_EFFECT = "NO_EFFECT"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_VOID = "VOID"
VERDICT_ABSENT = "ABSENT"
VERDICTS = (
    VERDICT_EFFECT,
    VERDICT_NO_EFFECT,
    VERDICT_INCONCLUSIVE,
    VERDICT_VOID,
    VERDICT_ABSENT,
)

#: Why an arm was voided. Reported in full; excluded from H1/H2.
VOID_TRUNCATED = "TRUNCATED"
VOID_DEGRADED = "DEGRADED"
VOID_NOT_HIERARCHICAL = "NOT_HIERARCHICAL"
VOID_REASONS = (VOID_TRUNCATED, VOID_DEGRADED, VOID_NOT_HIERARCHICAL)


class TestTheArmsArePinned:
    def test_four_arms_named_B_C_and_two_flat_baselines(self) -> None:
        assert ARMS == ("B", "C", "A-qwen", "A-gemma")

    def test_the_mirror_arm_exists(self) -> None:
        # Without C, a B result cannot separate architecture from assignment.
        assert ARM_C in ARMS
        assert HIERARCHICAL_ARMS == (ARM_B, ARM_C)

    def test_a_flat_baseline_exists_for_both_models(self) -> None:
        assert FLAT_ARMS == (ARM_A_QWEN, ARM_A_GEMMA)

    def test_B_and_C_are_exact_mirrors(self) -> None:
        commander_b, unit_b = ARM_MODELS[ARM_B]
        commander_c, unit_c = ARM_MODELS[ARM_C]
        assert (commander_b, unit_b) == (GEMMA, QWEN)
        assert (commander_c, unit_c) == (unit_b, commander_b)

    def test_the_flat_arms_have_no_commander(self) -> None:
        assert ARM_MODELS[ARM_A_QWEN] == (None, QWEN)
        assert ARM_MODELS[ARM_A_GEMMA] == (None, GEMMA)

    def test_every_arm_declares_models(self) -> None:
        assert set(ARM_MODELS) == set(ARMS)


class TestTheArenaIsPinned:
    def test_scenario_and_escalation_scenario(self) -> None:
        assert SCENARIO == "c-skirmish-1"
        assert ESCALATION_SCENARIO == "c-frontier-1"

    def test_the_opponent_is_the_fixed_house_bot(self) -> None:
        assert OPPONENT_DRIVER == "bot"

    def test_our_residency_is_declared_to_the_arena(self) -> None:
        assert OUR_DRIVER == "stateless"

    def test_seeds_and_n(self) -> None:
        assert SEEDS == (101, 102, 103, 104, 105)
        assert N_MATCHES == 5
        assert len(SEEDS) == N_MATCHES
        assert ESCALATION_N == 3


class TestTheWiringIsPinned:
    def test_the_spawn_allowance_is_strictly_positive(self) -> None:
        # NO_SPAWNS is the default and forbids a child entirely.
        assert SPAWN_ALLOWANCE == 1
        assert SPAWN_ALLOWANCE > 0

    def test_the_allowance_bounds_depth_at_one(self) -> None:
        from embodiment.subagent import attenuate

        assert attenuate(SPAWN_ALLOWANCE) == 0
        assert 2**SPAWN_ALLOWANCE - 1 == 1

    def test_step_budgets(self) -> None:
        assert COMMANDER_MAX_STEPS == 6
        assert UNIT_MAX_STEPS == 3
        assert FLAT_MAX_STEPS == 4

    def test_a_child_can_never_be_offered_more_than_the_parent_has(self) -> None:
        assert UNIT_MAX_STEPS < COMMANDER_MAX_STEPS


class TestTheWireIsPinned:
    def test_sixteen_thousand_tokens_for_any_qwen_level(self) -> None:
        assert MAX_TOKENS == 16000

    def test_the_same_cap_applies_to_both_models(self) -> None:
        # One number, so an arm difference can never be a cap difference.
        assert MAX_TOKENS == 16000

    def test_temperature_is_identical_in_every_arm_and_non_zero(self) -> None:
        assert TEMPERATURE == 0.7
        assert TEMPERATURE > 0.0

    def test_the_timeout_sits_above_the_contention_line(self) -> None:
        assert CONTENTION_SECONDS == 600.0
        assert REQUEST_TIMEOUT == 900.0
        assert REQUEST_TIMEOUT > CONTENTION_SECONDS

    def test_retry_policy(self) -> None:
        assert MAX_RETRIES == 3
        assert RETRY_WAIT_SECONDS == 30.0


class TestTheDecisionRuleIsPinned:
    def test_margin_effect_threshold(self) -> None:
        assert MARGIN_EFFECT == 3.0

    def test_no_effect_band(self) -> None:
        assert NO_EFFECT_BAND == 1.0

    def test_the_bands_do_not_overlap(self) -> None:
        # Anything between the two bands is INCONCLUSIVE by construction.
        assert NO_EFFECT_BAND < MARGIN_EFFECT

    def test_direction_min(self) -> None:
        assert DIRECTION_MIN == 4
        assert DIRECTION_MIN <= N_MATCHES

    def test_the_direction_bar_is_derived_at_any_n(self) -> None:
        assert DIRECTION_FRACTION == 0.8
        assert lc.direction_required(N_MATCHES) == DIRECTION_MIN
        assert lc.direction_required(ESCALATION_N) == 3
        # Never easier than the bar it was derived from.
        assert lc.direction_required(1) == 1

    def test_the_secondary_metric_threshold(self) -> None:
        assert GRADE_EFFECT == 200.0


class TestTheValidityGatesArePinned:
    def test_truncation_gate(self) -> None:
        assert LENGTH_FRACTION_MAX == 0.10

    def test_no_order_gate(self) -> None:
        assert NO_ORDER_FRACTION_MAX == 0.20

    def test_the_hierarchy_gate(self) -> None:
        assert SPAWN_GRANT_MIN_FRACTION == 0.90

    def test_every_void_reason_is_declared(self) -> None:
        assert VOID_REASONS == ("TRUNCATED", "DEGRADED", "NOT_HIERARCHICAL")


class TestTheVerdictVocabularyIsPinned:
    def test_absent_is_a_verdict(self) -> None:
        # An arm that did not run is reported, not omitted.
        assert VERDICT_ABSENT in VERDICTS

    def test_inconclusive_is_distinct_from_no_effect(self) -> None:
        assert VERDICT_INCONCLUSIVE != VERDICT_NO_EFFECT
        assert {VERDICT_INCONCLUSIVE, VERDICT_NO_EFFECT} <= set(VERDICTS)

    def test_every_verdict_is_declared(self) -> None:
        assert VERDICTS == ("EFFECT", "NO_EFFECT", "INCONCLUSIVE", "VOID", "ABSENT")


class TestHarnessMatchesThePreRegistration:
    """The harness's own copies, asserted equal to the pinned literals."""

    def test_arms(self) -> None:
        assert lc.ARMS == ARMS
        assert lc.HIERARCHICAL_ARMS == HIERARCHICAL_ARMS
        assert lc.FLAT_ARMS == FLAT_ARMS

    def test_models(self) -> None:
        assert lc.QWEN == QWEN
        assert lc.GEMMA == GEMMA
        assert lc.ARM_MODELS == ARM_MODELS

    def test_arena(self) -> None:
        assert lc.SCENARIO == SCENARIO
        assert lc.ESCALATION_SCENARIO == ESCALATION_SCENARIO
        assert lc.OPPONENT_DRIVER == OPPONENT_DRIVER
        assert lc.OUR_DRIVER == OUR_DRIVER
        assert lc.SEEDS == SEEDS
        assert lc.N_MATCHES == N_MATCHES
        assert lc.ESCALATION_N == ESCALATION_N

    def test_wiring(self) -> None:
        assert lc.SPAWN_ALLOWANCE == SPAWN_ALLOWANCE
        assert lc.COMMANDER_MAX_STEPS == COMMANDER_MAX_STEPS
        assert lc.UNIT_MAX_STEPS == UNIT_MAX_STEPS
        assert lc.FLAT_MAX_STEPS == FLAT_MAX_STEPS

    def test_wire(self) -> None:
        assert lc.MAX_TOKENS == MAX_TOKENS
        assert lc.TEMPERATURE == TEMPERATURE
        # The one registered value the harness deliberately no longer matches;
        # see REQUEST_TIMEOUT_AMENDED and TestAmendmentOne below.
        assert lc.REQUEST_TIMEOUT == REQUEST_TIMEOUT_AMENDED
        assert lc.MAX_RETRIES == MAX_RETRIES
        assert lc.RETRY_WAIT_SECONDS == RETRY_WAIT_SECONDS
        assert lc.CONTENTION_SECONDS == CONTENTION_SECONDS

    def test_decision_rule(self) -> None:
        assert lc.MARGIN_EFFECT == MARGIN_EFFECT
        assert lc.NO_EFFECT_BAND == NO_EFFECT_BAND
        assert lc.DIRECTION_MIN == DIRECTION_MIN
        assert lc.DIRECTION_FRACTION == DIRECTION_FRACTION
        assert lc.GRADE_EFFECT == GRADE_EFFECT

    def test_validity_gates(self) -> None:
        assert lc.LENGTH_FRACTION_MAX == LENGTH_FRACTION_MAX
        assert lc.NO_ORDER_FRACTION_MAX == NO_ORDER_FRACTION_MAX
        assert lc.SPAWN_GRANT_MIN_FRACTION == SPAWN_GRANT_MIN_FRACTION

    def test_verdicts(self) -> None:
        assert lc.VERDICTS == VERDICTS
        assert lc.VOID_REASONS == VOID_REASONS


class TestAmendmentOne:
    """The one place the harness deliberately departs from what was registered.

    A pre-registration is append-only, so a constant that legitimately changes
    after the dial produces exactly this shape: a registered value that stays
    true of the records, an amended value that is true of the code, and a dated
    section saying which is which. What must never happen is the third option —
    the document quietly edited to match the code, so that no reader can tell a
    correction from an original intention.
    """

    DOC = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "live-test-results"
        / "league-commander-preregistration.md"
    ).read_text(encoding="utf-8")

    def test_the_amendment_is_appended_and_dated_not_edited_in(self) -> None:
        assert "## Amendment 1" in self.DOC
        registered_at = self.DOC.index("## Fixed configuration")
        amended_at = self.DOC.index("## Amendment 1")
        assert amended_at > registered_at, "an amendment that precedes what it amends"
        # The registered value survives verbatim in the section that registered it.
        assert "**900**" in self.DOC or "900" in self.DOC[registered_at:amended_at]

    def test_the_amendment_states_the_raise_and_its_derivation(self) -> None:
        section = self.DOC[self.DOC.index("## Amendment 1") :]
        for needle in ("900.0", "1600.0", "12.1", "179.3", "1322", "timeout-rate-measurements"):
            assert needle in section, f"the amendment never mentions {needle!r}"

    def test_it_is_only_a_raise_never_a_quiet_lowering(self) -> None:
        assert lc.REQUEST_TIMEOUT > REQUEST_TIMEOUT

    def test_nothing_else_on_the_wire_moved(self) -> None:
        """The amendment's blast radius, asserted rather than promised."""
        assert lc.MAX_TOKENS == MAX_TOKENS
        assert lc.TEMPERATURE == TEMPERATURE
        assert lc.MAX_RETRIES == MAX_RETRIES
        assert lc.RETRY_WAIT_SECONDS == RETRY_WAIT_SECONDS
        assert lc.CONTENTION_SECONDS == CONTENTION_SECONDS

    def test_the_amendment_says_no_published_figure_changes(self) -> None:
        section = self.DOC[self.DOC.index("## Amendment 1") :]
        assert "384" in section, "the re-exam's call count is the basis for 'nothing changes'"
        assert "2.4" in section, "the published cost range's lower bound must survive the amendment"
        assert "4.4" in section, "the published cost range's upper bound must survive the amendment"
