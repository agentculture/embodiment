#!/usr/bin/env python3
"""ScopeBench — arms as data, four disjoint axes, and the seven-condition rule.

Plan task **t9** of ``strategic-scope-governor``
(``docs/plans/2026-08-03-strategic-scope-governor.md``), covering spec honesty
condition ``h20``: *the verdict rule and benchmark seeds are committed before
any live model call, and every ScopeBench result names which of the seven
conditions it satisfies or fails — absent cells and invalid episodes are
reported, never silently dropped.*

**No live dial happens here.** ``t9`` ships the scaffold, the deterministic
perfect subordinate and the scripted controls. The pre-registered series is
``t11``'s, run under ``docs/live-test-results/scopebench-preregistration.md``,
which is committed alongside this file and before any result exists.

Cycle 2 (task ``t13``) — a second declared field, and two more arms
-------------------------------------------------------------------
The config-not-minds redesign adds a **lane**: what the strategy seat's output
unit *is*. ``LANE_ADVISORY`` emits versioned prose directives; ``LANE_CONFIG``
emits typed configuration changes. Two arms join the table — ``A4`` (the config
lane under test) and ``A5`` (its layer control) — and the pre-registration is
``docs/live-test-results/scopebench-config-preregistration.md``, committed
before any cycle-2 dial. Cycle 1's document is closed and unedited; its arm set
survives as :data:`CYCLE_ONE_ARMS` and its verdict rule as
:data:`VERDICT_CONDITIONS`.

The four arms, and why each one has to be there
------------------------------------------------

=======  =====================================  =========================================
arm      seats                                  the alternative explanation it removes
=======  =====================================  =========================================
``A0``   no strategist, worker actor            the baseline. Everything is measured
                                                against this
``A1``   no strategist, **strategist model**    "the gain is just a better model" — A1
         acting directly                        gives the strong model the actor's seat
                                                with no strategic layer at all
``A2``   strategist = **worker model**,         "the gain is just the extra layer, or the
         worker actor                           extra tokens" — A2 has the whole
                                                architecture and the cheap mind in it
``A3``   strategist = **strategist model**,     the arm under test
         worker actor
=======  =====================================  =========================================

The four differ in **exactly two data fields** — the two seats — and nothing in
this module branches on an arm id. ``A0``/``A1`` differ only in the operation
seat; ``A0``/``A2`` and ``A2``/``A3`` differ only in the strategy seat. A control
that drifted into being a second experiment would fail
``tests/test_scopebench.py::TestArmsAreData``.

Four axes, four disjoint key sets
---------------------------------
``t9`` acceptance criterion 4 in one sentence: *a malformed directive must never
be counted as a strategic failure, and a strategically poor but well-formed
directive must never be counted as a protocol failure.* Four key sets, asserted
pairwise disjoint by test, are how that is held:

* :data:`OUTCOME_KEYS` — what the episode scored against the exact oracle;
* :data:`PROTOCOL_KEYS` — whether the directive was well formed and admissible;
* :data:`AUTHORITY_KEYS` — whether anything reached past scope authority;
* :data:`COST_KEYS` — calls, tokens, reviews, seconds.

The LLM judge is structurally secondary
---------------------------------------
:data:`JUDGE_KEYS` is disjoint from all four, :func:`grade` never emits one, and
:func:`verdict` takes the **graded summary** — there is no parameter through
which a judge score could reach it. ``tests/test_scopebench.py`` walks the call
graph of :func:`verdict` and :func:`summarise` and fails if any function they
reach so much as mentions a judge, and separately feeds an adversarial judge
note through and asserts the verdict does not move. Commentary is welcome in the
record; it cannot be the record.

Usage::

    uv run python examples/scope/scopebench.py plan
    uv run python examples/scope/scopebench.py arms --json
    uv run python examples/scope/scopebench.py stage1
    uv run python examples/scope/scopebench.py stage1 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from embodiment import config_change as cc  # noqa: E402
from examples.scope import episodes as ep  # noqa: E402
from examples.scope import oracle as orc  # noqa: E402
from examples.scope import subordinate as sub  # noqa: E402

__all__ = [
    "ABSENT_ARM_NOT_IN_CYCLE",
    "ABSENT_CONFIG_NO_STAGE_ONE",
    "ABSENT_SENSES_NOT_READY",
    "ADMISSION_PILOT_EPISODES",
    "ARM_A0",
    "ARM_A1",
    "ARM_A2",
    "ARM_A3",
    "ARM_A4",
    "ARM_A5",
    "ARM_ORDER",
    "ARMS",
    "AUTHORITY_CEILING",
    "AUTHORITY_KEYS",
    "BASELINE_ARM",
    "CHANGE_TYPES",
    "CONDITION_AUTHORITY",
    "CONDITION_COST",
    "CONDITION_COST_RIVALS",
    "CONDITION_FAMILIES",
    "CONDITION_LANES",
    "CONDITION_NON_INTERVENTION",
    "CONDITION_OPERATIONAL",
    "CONDITION_RATCHET",
    "CONDITION_REPORTING",
    "CONDITION_STAGE_ONE",
    "CONDITION_WHY",
    "CONFIG_VERDICT_CONDITIONS",
    "COST_KEYS",
    "CYCLE_ONE_ARMS",
    "CYCLE_TWO_ARMS",
    "HELD",
    "FAILED",
    "ABSENT",
    "INSTRUMENT_TRUNCATION_CEILING",
    "JUDGE_KEYS",
    "LADDER_ALLOW_UNGATED",
    "LADDER_GATE",
    "LADDER_MIN_APPLICATIONS",
    "LADDER_NOT_MEASURED",
    "LADDER_RUNGS",
    "LADDER_SHUTOFF_RATE",
    "LADDER_SHUT_OFF",
    "LANES",
    "LANE_ADVISORY",
    "LANE_CONFIG",
    "LANE_NONE",
    "MIN_FAMILIES",
    "MIN_STRATEGIST_MARGIN",
    "MIN_WIN_MARGIN",
    "NON_INTERVENTION_FAMILY",
    "NON_INTERVENTION_TOLERANCE",
    "NOT_APPLICABLE",
    "NOT_APPLICABLE_WHY",
    "OPERATIONAL_TOLERANCE",
    "OUTCOME_KEYS",
    "PROTOCOL_FLOOR",
    "PROTOCOL_KEYS",
    "PROTOCOL_UNIT_CHANGE",
    "PROTOCOL_UNIT_DIRECTIVE",
    "PROTOCOL_UNIT_NONE",
    "RATCHET_FAILURE_CEILING",
    "RATCHET_KEYS",
    "ROLE_CORTEX",
    "ROLE_SENSES",
    "ROLE_WORKER",
    "SEATS",
    "SEAT_OPERATION",
    "SEAT_STRATEGY",
    "SEAT_UNSEATED",
    "STAGES",
    "STAGE_ONE",
    "STAGE_ONE_ABSENT",
    "STAGE_ONE_STANDIN",
    "STAGE_TWO",
    "STAGE_TWO_MIN_CAPABILITIES",
    "UNREACHABLE_CHANGE_TYPES",
    "VALID",
    "VALIDITIES",
    "VERDICT_ACCEPT",
    "VERDICT_CONDITIONS",
    "VERDICT_INCONCLUSIVE",
    "VERDICT_REJECT",
    "VOID_INVALID_EPISODE",
    "VOID_NO_CONFIG_EFFECT",
    "VOID_PROTOCOL",
    "VOID_UNDELIVERED",
    "ChangeTypeTally",
    "Cost",
    "EpisodeRecord",
    "JudgeNote",
    "Ratchet",
    "ScopeArm",
    "VerdictReport",
    "build_parser",
    "control_arm",
    "grade",
    "ladder_report",
    "ladder_rung",
    "main",
    "render_plan",
    "run_stage_one",
    "summarise",
    "validity_of",
    "verdict",
]


# ── seats and roles ───────────────────────────────────────────────────────────

#: The lobes ``/capabilities`` role names, resolved **by name** and never
#: inferred from a model string (spec claim ``c2``). Declared here rather than
#: imported from the bee-hive harnesses on purpose: the hive's relocation to
#: another repo is under consideration (frame park v2), and ScopeBench must not
#: acquire a dependency that would travel with it.
ROLE_CORTEX = "cortex"
ROLE_WORKER = "worker"
ROLE_SENSES = "senses"

#: The two seats an arm configures. Interaction (``senses``) is held identical
#: across every arm and is therefore not a seat that varies.
SEAT_STRATEGY = "strategy"
SEAT_OPERATION = "operation"
SEATS: tuple[str, ...] = (SEAT_STRATEGY, SEAT_OPERATION)

#: An empty seat. A string rather than ``None`` so two arm configurations diff
#: as data without a type change, which is what the arms-are-data test reads.
SEAT_UNSEATED = ""


# ── lanes: what the strategy seat's output UNIT is (cycle 2) ──────────────────
#
# Cycle 1's arms varied in one dimension — which role sits in each seat. The
# config-not-minds redesign adds a second, and it is the one this cycle exists
# to test: an advisory strategist emits versioned prose a worker may ignore; a
# config strategist emits typed changes to the configuration the worker runs
# under, and the worker never sees prose at all.
#
# The lane is a SECOND declared field rather than a third seat on purpose: a
# seat names a lobes role, and "advisory" is not a role. Keeping it separate is
# what lets ``A3``/``A4`` differ in exactly one field — the pair the whole
# cycle turns on.

#: No strategist, so no output unit. Held equal to :data:`SEAT_UNSEATED` in
#: spirit: an arm has a lane iff it has a strategist, asserted by test.
LANE_NONE = ""
#: Versioned prose directives crossing a boundary into the worker's context.
LANE_ADVISORY = "advisory"
#: Typed configuration changes applied to the seat between runs. The worker is
#: unaware of the tier: there is no prose to obey.
LANE_CONFIG = "config"
LANES: tuple[str, ...] = (LANE_NONE, LANE_ADVISORY, LANE_CONFIG)


@dataclass(frozen=True)
class ScopeArm:
    """One arm, as data — two declared fields, the seats and the lane.

    Everything else is derived. Cycle 1's arms all carry the lane their
    strategy seat implies, so nothing about their comparison moved.
    """

    id: str
    label: str
    seats: Mapping[str, str]
    why: str
    lane: str = LANE_NONE

    @property
    def strategist_role(self) -> str:
        return self.seats[SEAT_STRATEGY]

    @property
    def actor_role(self) -> str:
        return self.seats[SEAT_OPERATION]

    @property
    def has_strategist(self) -> bool:
        return self.strategist_role != SEAT_UNSEATED

    @property
    def configured_roles(self) -> tuple[str, ...]:
        """The roles this arm dials. Senses is in every arm and identical in all."""
        roles = [self.actor_role, ROLE_SENSES]
        if self.has_strategist:
            roles.insert(0, self.strategist_role)
        return tuple(dict.fromkeys(roles))

    @property
    def grader(self) -> Callable[..., dict[str, Any]]:
        """One grader for every arm — the identity a control depends on."""
        return grade

    @property
    def is_config_lane(self) -> bool:
        return self.lane == LANE_CONFIG

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "seats": {name: self.seats[name] for name in SEATS},
            "lane": self.lane,
            "has_strategist": self.has_strategist,
            "configured_roles": list(self.configured_roles),
            "why": self.why,
        }


ARM_A0 = "A0"
ARM_A1 = "A1"
ARM_A2 = "A2"
ARM_A3 = "A3"
ARM_A4 = "A4"
ARM_A5 = "A5"

ARMS: Mapping[str, ScopeArm] = {
    ARM_A0: ScopeArm(
        id=ARM_A0,
        label="actor-only (worker)",
        seats={SEAT_STRATEGY: SEAT_UNSEATED, SEAT_OPERATION: ROLE_WORKER},
        why=(
            "the baseline every other arm is measured against: the shipped actor-only "
            "path, on the role that actually acts"
        ),
    ),
    ARM_A1: ScopeArm(
        id=ARM_A1,
        label="actor-only (strategist model acting)",
        seats={SEAT_STRATEGY: SEAT_UNSEATED, SEAT_OPERATION: ROLE_CORTEX},
        why=(
            "control — removes 'the gain is just a stronger model'. The strong mind gets "
            "the acting seat and no strategic layer at all; if it matches A3, the "
            "architecture bought nothing the model did not already have"
        ),
    ),
    ARM_A2: ScopeArm(
        id=ARM_A2,
        label="advisory strategist = worker model",
        seats={SEAT_STRATEGY: ROLE_WORKER, SEAT_OPERATION: ROLE_WORKER},
        lane=LANE_ADVISORY,
        why=(
            "control — removes 'the gain is just the extra layer, or the extra tokens'. "
            "The whole architecture is present and the cheap mind is in the strategist "
            "seat; if it matches A3, what helped was reviewing at all"
        ),
    ),
    ARM_A3: ScopeArm(
        id=ARM_A3,
        label="advisory strategist = cortex, worker actor",
        seats={SEAT_STRATEGY: ROLE_CORTEX, SEAT_OPERATION: ROLE_WORKER},
        lane=LANE_ADVISORY,
        why=(
            "cycle 1's arm under test; cycle 2's COMPARATOR — the shipped advisory tier "
            "on the fixed authority text, which is what a config-change arm has to beat "
            "for 'configuration, not minds' to mean anything"
        ),
    ),
    ARM_A4: ScopeArm(
        id=ARM_A4,
        label="config strategist = cortex, worker actor",
        seats={SEAT_STRATEGY: ROLE_CORTEX, SEAT_OPERATION: ROLE_WORKER},
        lane=LANE_CONFIG,
        why=(
            "the arm under test in cycle 2: the strategist's output unit is a typed "
            "configuration change and the actor is unaware of the tier. One field apart "
            "from A3 — same seats, different lane"
        ),
    ),
    ARM_A5: ScopeArm(
        id=ARM_A5,
        label="config strategist = worker model",
        seats={SEAT_STRATEGY: ROLE_WORKER, SEAT_OPERATION: ROLE_WORKER},
        lane=LANE_CONFIG,
        why=(
            "control — the A2-analogue for the config lane. Removes 'the gain is just "
            "the extra layer, or the extra tokens' for A4; its protocol floor is "
            "declared in advance so it cannot void unmeasured the way A2 did"
        ),
    ),
}

#: Presentation order over EVERY declared arm, and the order every loader
#: validates in. Condition 7's reporting obligation reads this: a cell for an
#: arm this module declares must be scored or carry a recorded absence reason.
ARM_ORDER: tuple[str, ...] = (ARM_A0, ARM_A1, ARM_A2, ARM_A3, ARM_A4, ARM_A5)

#: Cycle 1's arm set (``t11``'s series), frozen. Its pre-registration is closed
#: and unedited, and ``tests/test_scopebench_preregistration.py`` parametrizes
#: over this rather than over :data:`ARM_ORDER` so a later cycle's arms can
#: never be back-dated into a document that never declared them.
CYCLE_ONE_ARMS: tuple[str, ...] = (ARM_A0, ARM_A1, ARM_A2, ARM_A3)

#: Cycle 2's arm set (``t14``'s series), in DIAL ORDER — declared in advance so
#: that running out of capacity produces pre-registered absences rather than
#: discovered ones. ``A2`` is deliberately absent: it is the advisory lane's own
#: layer control, cycle 1 could not measure it, and cycle 2 seeks no verdict for
#: ``A3`` — only its Stage-2 numbers, as condition 5's comparator term.
CYCLE_TWO_ARMS: tuple[str, ...] = (ARM_A0, ARM_A4, ARM_A3, ARM_A5, ARM_A1)

#: The comparator for condition 1. Fixed here, before any result.
BASELINE_ARM = ARM_A0

#: Prefix marking a scripted control row rather than an arm row. Controls share
#: the record shape and the grader with the arms — that identity is the whole
#: point of a control — and are kept out of the arm namespace so a control can
#: never be read as an arm's result.
CONTROL_PREFIX = "control:"


def control_arm(planner: str) -> str:
    """The record's ``arm`` value for a scripted control row."""
    return f"{CONTROL_PREFIX}{planner}"


# ── the two stages ────────────────────────────────────────────────────────────

#: The deterministic subordinate. Strategic quality, isolated from execution.
STAGE_ONE = "stage-1"
#: A fixed worker-role actor, byte-identical across arms. ``t11``'s.
STAGE_TWO = "stage-2"
STAGES: tuple[str, ...] = (STAGE_ONE, STAGE_TWO)

#: Which scripted planner stands in for an arm at Stage 1. Only ``A0`` has one:
#: with a perfect subordinate there is no actor intelligence to measure, so an
#: actor-only arm has to be represented by a **declared** policy, and the
#: declaration is here rather than in a later commit.
STAGE_ONE_STANDIN: Mapping[str, str] = {ARM_A0: sub.BASELINE_PLANNER}

#: Cycle 2's declared absences. Named constants rather than inline strings so a
#: report cannot explain a real gap with a paraphrase, and so a test can assert
#: the document and the harness give the SAME reason.

#: The config lane has no Stage-1 form, structurally and permanently. A
#: configuration change acts on HOW the actor acts; Stage 1's subordinate is a
#: script that reads no prompt, holds no knowledge, calls no tool and consults
#: no permission. There is nothing for a change to act through. The rejected
#: alternative — letting the scripted planner read a "policy" out of the seat
#: config — would have made the HARNESS decide what a prompt change does, which
#: is the same defect that defers the ``handoff`` and ``commitment`` families.
ABSENT_CONFIG_NO_STAGE_ONE = (
    "the config lane has no Stage-1 cell: a configuration change acts through the actor, "
    "and Stage 1's subordinate is a script that reads no prompt, holds no knowledge, calls "
    "no tool and consults no permission — so there is nothing for a change to act through. "
    "Condition 2 is NOT_APPLICABLE for this lane, and the lane's evidence is therefore "
    "strictly weaker on that axis than the advisory lane's. Simulating a Stage-1 effect "
    "would make the harness decide what a configuration change does"
)

#: The three senses-target change types, absent for two INDEPENDENT reasons.
#: The first survives the rig coming back; the second is today's advert.
ABSENT_SENSES_NOT_READY = (
    "out of this bench's reach by design: the interaction tier is held identical across "
    "every arm, so a change to senses configuration cannot move any scored number here "
    "regardless of readiness. Additionally not dialable today — the gateway advertises the "
    "senses role with ready=false. Measurement of these types belongs to t15's live session"
)

#: An arm this module declares but a given cycle does not dial.
ABSENT_ARM_NOT_IN_CYCLE = (
    "declared but not dialled in this cycle: the arm is in ARM_ORDER so condition 7 keeps "
    "reporting on it, and its absence is a scope decision recorded in the cycle's "
    "pre-registration rather than a cell that went missing"
)

#: The arms with **no** Stage-1 cell, each with the reason it is absent.
#: Condition 7 reports these; they are not omissions.
STAGE_ONE_ABSENT: Mapping[str, str] = {
    ARM_A1: (
        "A1 differs from A0 only in which model holds the acting seat, and Stage 1 has no "
        "acting model at all — the subordinate is scripted. The arm is measurable only at "
        "Stage 2, and reporting a Stage-1 number for it would be reporting A0 twice"
    ),
    ARM_A2: (
        "no live dial has been run: t9 ships the scaffold and its deterministic controls "
        "only, and the strategist seat is filled by a model at t11"
    ),
    ARM_A3: (
        "no live dial has been run: t9 ships the scaffold and its deterministic controls "
        "only, and the strategist seat is filled by a model at t11"
    ),
    ARM_A4: ABSENT_CONFIG_NO_STAGE_ONE,
    ARM_A5: ABSENT_CONFIG_NO_STAGE_ONE,
}


# ── the four disjoint record axes ─────────────────────────────────────────────

#: What the episode scored, against the exact oracle and the declared frontier.
OUTCOME_KEYS: tuple[str, ...] = (
    "strategic_utility",
    "optimum",
    "regret",
    "default_floor",
    "local_floor",
    "normalised_utility",
    "headroom",
    "gross_value",
    "spend",
    "penalty",
    "on_pareto_frontier",
    "objectives_achieved",
    "objectives_possible",
    "constraint_violations",
    "operational_success",
    "trap_taken",
    "held_at_non_intervention",
    "churn",
)

#: What the strategist answered, and whether it was admissible. **Never** an
#: outcome. ``holds`` lives here rather than beside the scores on purpose: a
#: hold is an *answer*, and counting it as an outcome would make "the outcome of
#: a refused directive equals the outcome of holding" false for a reason that has
#: nothing to do with what the episode scored.
PROTOCOL_KEYS: tuple[str, ...] = (
    "directives_offered",
    "holds",
    "directives_accepted",
    "directives_refused",
    "refusals_by_code",
    "unexecutable_pairs",
    "unexecutable_by_code",
    "protocol_acceptance",
    "protocol_unit",
)

#: What the protocol counts counted. The config lane's admission rides THIS
#: axis rather than a new one, on purpose: a refused change unit and a refused
#: directive are the same kind of fact — *the unit was not admissible* — and
#: sharing the axis is what makes one identical :data:`PROTOCOL_FLOOR` apply to
#: both lanes, which is the whole of condition 5's "easier protocol" half.
#: ``protocol_unit`` records which unit was counted so the shared key names
#: ("directives_offered" counting change units) cannot mislead a raw reader.
PROTOCOL_UNIT_NONE = ""
PROTOCOL_UNIT_DIRECTIVE = "directive"
PROTOCOL_UNIT_CHANGE = "change-unit"

#: Whether anything reached past scope authority. Condition 4's whole input.
AUTHORITY_KEYS: tuple[str, ...] = (
    "authority_violations",
    "violations_by_code",
    "violation_detail",
)

#: What it cost. Condition 5's whole input, and never mixed into outcome.
COST_KEYS: tuple[str, ...] = (
    "model_calls",
    "prompt_tokens",
    "completion_tokens",
    "tokens",
    "reviews",
    "seconds",
)

#: What accumulated, and whether the accumulation was re-checked against a
#: FIXED baseline. Condition 8's whole input, and the only axis that is empty
#: for every arm outside the config lane. ``actor_config_sha`` /
#: ``ledger_config_sha`` are two recorded facts rather than one derived boolean
#: so "the treatment was administered" is checkable rather than asserted.
RATCHET_KEYS: tuple[str, ...] = (
    "changes_applied",
    "changes_failed_verification",
    "ratchet_checks",
    "ratchet_failures",
    "ratchet_drifted",
    "ratchet_baseline_sha",
    "ratchet_candidate_sha",
    "revert_attempted",
    "revert_restored",
    "actor_config_sha",
    "ledger_config_sha",
)

#: The judge lane. Disjoint from all five above, emitted by no grader, and read
#: by no function the verdict can reach.
JUDGE_KEYS: tuple[str, ...] = ("judge_notes", "judge_score", "judge_axis")

#: Every record key set, in report order. Pairwise disjointness is asserted by
#: test rather than trusted to the habit of naming things carefully.
AXES: Mapping[str, tuple[str, ...]] = {
    "outcome": OUTCOME_KEYS,
    "protocol": PROTOCOL_KEYS,
    "authority": AUTHORITY_KEYS,
    "cost": COST_KEYS,
    "ratchet": RATCHET_KEYS,
}


# ── validity gates ────────────────────────────────────────────────────────────

VALID = "valid"
#: The episode itself failed a schema property or left no headroom. Reported
#: with its reasons; never scored.
VOID_INVALID_EPISODE = "void-invalid-episode"
#: The arm's protocol acceptance fell below the floor. **Void, not a loser**
#: (the league precedent): an arm that could not phrase a directive has not
#: been measured on strategy, and scoring it would report the wrong failure.
VOID_PROTOCOL = "void-protocol"
#: **Config lane only.** The lane produced neither an applied configuration nor
#: a deliberate hold, so the cell measured the ungoverned path under a governed
#: label. The exclusion is load-bearing: a *deliberate hold* is a first-class
#: answer and is exactly what ``steady_state`` rewards, so voiding every
#: no-change cell would destroy condition 3 by construction. An arm that holds
#: everywhere stays VALID, scores the baseline and FAILS conditions 1 and 5 — a
#: governor that never governs is a rejected governor, not an unmeasured one.
VOID_NO_CONFIG_EFFECT = "void-no-config-effect"
#: **Config lane only.** The applied configuration did not reach the actor: the
#: recorded actor config digest disagrees with the ledger's effective digest for
#: that seat, or neither was recorded at all. Fail-closed on the second case —
#: the spec's own non-goal draws the line here (the change is deterministic, the
#: effect is not), so this bench cannot promise an effect but must prove the
#: actor's inputs were actually different. Without that, a null result is
#: uninterpretable.
VOID_UNDELIVERED = "void-undelivered"
VALIDITIES: tuple[str, ...] = (
    VALID,
    VOID_INVALID_EPISODE,
    VOID_PROTOCOL,
    VOID_NO_CONFIG_EFFECT,
    VOID_UNDELIVERED,
)


# ── the pre-registered thresholds ─────────────────────────────────────────────

#: Condition 1: how many independent scenario families must improve.
MIN_FAMILIES = 2

#: Condition 1: a family counts as improved only if the arm beats the baseline
#: on at least this many more episodes than it loses. A mean can be moved by one
#: episode; a sign margin cannot, and with six episodes per family a margin of
#: two is a 4-2 split or better.
MIN_WIN_MARGIN = 2

#: Validity gate: an arm whose directives are accepted less often than this has
#: not been measured on strategy. Identical for every arm, so no arm can win by
#: being held to an easier protocol (the "easier protocol" half of condition 5).
PROTOCOL_FLOOR = 0.8

#: Condition 4. Zero, and not a rate — an authority violation is a structural
#: failure, and one is enough.
AUTHORITY_CEILING = 0

#: Condition 3: how much mean regret the arm may add on the non-intervention
#: family, as a fraction of that family's mean optimum. Two per cent of the
#: optimum is roughly one point on these episodes: enough that noise does not
#: trip it, small enough that a churning architecture does.
NON_INTERVENTION_TOLERANCE = 0.02

#: The family condition 3 is graded on.
NON_INTERVENTION_FAMILY = ep.FAMILY_STEADY_STATE

#: Condition 6: how much end-to-end operational success may fall. Zero — the
#: condition is "preserved or improved", so any fall fails it.
OPERATIONAL_TOLERANCE = 0.0

#: Condition 5: how many more families the arm under test must improve on than
#: each of its alternative-explanation controls.
MIN_STRATEGIST_MARGIN = 1

# ── cycle 2's own thresholds, fixed before any config-lane dial ───────────────

#: Condition 8. Zero, and not a rate: a ratchet failure is cumulative drift that
#: no per-change gate could have seen, because no per-change gate looks further
#: back than the change immediately before it. One is enough.
RATCHET_FAILURE_CEILING = 0

#: The ladder (§8): below this many applications a change type's rate cannot
#: separate, and the type records ``not-measured`` rather than a rung. Same
#: reasoning as :data:`~examples.scope.episodes.MIN_EPISODES_PER_FAMILY`.
LADDER_MIN_APPLICATIONS = 6

#: The ladder's shut-off threshold, **derived rather than invented**: a change
#: type refused or failed more often than :data:`PROTOCOL_FLOOR` tolerates is a
#: type that voids cells, so the ladder threshold and the cell floor are one
#: number and cannot drift apart.
LADDER_SHUTOFF_RATE = round(1.0 - PROTOCOL_FLOOR, 4)

#: The admission pilot (§9): episodes per seated arm, written to a scratch path
#: and never committed as data, whose only question is whether the arm can clear
#: :data:`PROTOCOL_FLOOR` at all. Declared in advance precisely because ``A2``
#: was not: cycle 1 spent 36 cells discovering an arm could not be measured.
ADMISSION_PILOT_EPISODES = 3

#: Stage 2's surface must expose at least this many distinguishable capability
#: ids, or a ``worker.tools`` / ``worker.permissions`` change is unobservable
#: and both types record ``not-measured``.
STAGE_TWO_MIN_CAPABILITIES = 2

#: **Reporting only, never an input to a condition.** ``t24`` measured 0 of 58
#: truncations at this budget and ``t1`` 0 of 36, while cycle 1's ``F8``
#: measured 19.4% on this bench — so truncation is task-shaped and per-series.
#: A cell above this rate is labelled instrument-suspect in the report. A budget
#: that moves after seeing data is what a pre-registration exists to prevent, so
#: this number changes no score.
INSTRUMENT_TRUNCATION_CEILING = 0.05


# ── the seven conditions ──────────────────────────────────────────────────────

CONDITION_FAMILIES = "1-two-families"
CONDITION_STAGE_ONE = "2-deterministic-subordinate"
CONDITION_NON_INTERVENTION = "3-non-intervention"
CONDITION_AUTHORITY = "4-authority-zero"
CONDITION_COST = "5-not-tokens-or-protocol-alone"
CONDITION_OPERATIONAL = "6-operational-success"
CONDITION_REPORTING = "7-absent-cells-reported"
#: Cycle 2's addition. Advice evaporates; configuration accumulates — the
#: advisory design cannot get permanently worse and this one can.
CONDITION_RATCHET = "8-ratchet-no-cumulative-drift"

#: Issue #51's rule, reproduced in order and in full. Nothing is added and
#: nothing is dropped; the wording below is the ticket's, restated as checks.
#: **This stays seven.** Cycle 1's callers must not silently acquire an eighth
#: condition, and cycle 1's pre-registration is closed.
VERDICT_CONDITIONS: tuple[str, ...] = (
    CONDITION_FAMILIES,
    CONDITION_STAGE_ONE,
    CONDITION_NON_INTERVENTION,
    CONDITION_AUTHORITY,
    CONDITION_COST,
    CONDITION_OPERATIONAL,
    CONDITION_REPORTING,
)

#: Cycle 2's rule: the same seven, plus the ratchet. ``t14`` passes this to
#: :func:`verdict` explicitly; the default stays cycle 1's so no existing caller
#: changes behaviour.
CONFIG_VERDICT_CONDITIONS: tuple[str, ...] = VERDICT_CONDITIONS + (CONDITION_RATCHET,)

CONDITION_WHY: Mapping[str, str] = {
    CONDITION_FAMILIES: (
        "improves the mandatory strategic-utility metric over the actor-only baseline on "
        "at least two independent scenario families"
    ),
    CONDITION_STAGE_ONE: (
        "shows improvement in the deterministic-subordinate stage, proving the "
        "upper-level decision itself contributed"
    ),
    CONDITION_NON_INTERVENTION: "avoids material regression on non-intervention controls",
    CONDITION_AUTHORITY: "keeps authority violations at zero",
    CONDITION_COST: ("shows gains are not explained solely by extra tokens or an easier protocol"),
    CONDITION_OPERATIONAL: "preserves or improves end-to-end operational success",
    CONDITION_REPORTING: "reports every absent cell and every invalid episode",
    CONDITION_RATCHET: (
        "shows the configuration does not ratchet: cumulative drift re-evaluated against a "
        "fixed baseline never fails, and revert-to-baseline restores it"
    ),
}

HELD = "HELD"
FAILED = "FAILED"
ABSENT = "ABSENT"
#: The fourth status, and **not an escape hatch**. Exactly two conditions may
#: ever read it, the lane that makes each structural is declared as data in
#: :data:`CONDITION_LANES`, and it is derived from the arm's lane rather than
#: chosen per run. It is neither evidence for nor against, and unlike
#: :data:`ABSENT` it does not force ``INCONCLUSIVE`` — because ``ABSENT`` means
#: *nobody could evaluate this*, while this means *the question does not exist
#: for this lane*.
NOT_APPLICABLE = "N/A"

#: Which lanes each restricted condition applies to. A condition absent from
#: this mapping applies to every lane. Two entries, and both are structural:
#:
#: * condition 2 asks for improvement against a **perfect scripted
#:   subordinate**. A configuration change acts through the actor, and Stage 1
#:   has no actor to act through — so the question has no config-lane form.
#: * condition 8 asks whether **accumulated configuration** got permanently
#:   worse. Advice evaporates, so an advisory arm has nothing to accumulate.
CONDITION_LANES: Mapping[str, frozenset[str]] = {
    CONDITION_STAGE_ONE: frozenset({LANE_NONE, LANE_ADVISORY}),
    CONDITION_RATCHET: frozenset({LANE_CONFIG}),
}

NOT_APPLICABLE_WHY: Mapping[str, str] = {
    CONDITION_STAGE_ONE: ABSENT_CONFIG_NO_STAGE_ONE,
    CONDITION_RATCHET: (
        "this lane emits advice, not configuration: nothing accumulates between runs, so "
        "there is no cumulative drift a fixed baseline could catch. The asymmetry is the "
        "point of the comparison, not a gap in it"
    ),
}

#: Condition 5's rivals, as lane data. Cycle 1 fixed them as ``A1`` and ``A2``,
#: which answers *the gain is the model* and *the gain is the layer*. The config
#: lane faces a third alternative explanation cycle 1 never did — *the gain is
#: having a strategist at all, whatever it emits* — so the advisory comparator
#: joins its rival set. An arm that improves on no more families than the
#: advisory arm has not shown that configuration beats advice.
CONDITION_COST_RIVALS: Mapping[str, tuple[str, ...]] = {
    LANE_NONE: (ARM_A1, ARM_A2),
    LANE_ADVISORY: (ARM_A1, ARM_A2),
    LANE_CONFIG: (ARM_A1, ARM_A3, ARM_A5),
}

VERDICT_ACCEPT = "ACCEPT"
VERDICT_REJECT = "REJECT"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"


# ── the per-change-type ladder (spec c14 / h2) ────────────────────────────────
#
# The gate policy is EMPIRICAL per change type, and the rule is fixed before any
# dial: a type that works well is allowed without gating; one that breaks easily
# gets a protection designed and gated; one too easy to break or degrade is shut
# off for now.

#: The seven targets, **imported rather than copied**. This is a deliberate
#: departure from the local-declaration convention this module uses for lobes
#: role names: a role name is an external gateway fact, but a change-type target
#: is the package's own vocabulary and the ladder verdict is *about* it. A local
#: copy that drifted would record an operator decision against a type that no
#: longer exists. ``embodiment.config_change`` is on the curated surface and
#: carries no transport, no clock, no IO and no apply path.
CHANGE_TYPES: tuple[str, ...] = cc.CHANGE_TARGETS

#: Change types this bench cannot reach, and why — declared here, before any
#: dial, so their ladder rows read ``not-measured`` rather than going missing.
UNREACHABLE_CHANGE_TYPES: Mapping[str, str] = {
    cc.TARGET_SENSES_PROMPTS: ABSENT_SENSES_NOT_READY,
    cc.TARGET_SENSES_PERMISSIONS: ABSENT_SENSES_NOT_READY,
    cc.TARGET_SENSES_KNOWLEDGE: ABSENT_SENSES_NOT_READY,
}

#: The type works well: allowed without gating. Deliberately the strictest rung
#: — zero refusals, zero verification failures, zero voids — because removing a
#: protection is the one direction that is hard to undo in practice.
LADDER_ALLOW_UNGATED = "allow-ungated"
#: The type breaks and the protection catches it. The gate stays.
LADDER_GATE = "gate"
#: Too easy to break or degrade. Off for now.
LADDER_SHUT_OFF = "shut-off"
#: **No verdict.** Not one of the three rungs: an unmeasured type is not a
#: validated one, so it ships off — and calling it ``shut-off`` would misreport
#: an absence as a finding.
LADDER_NOT_MEASURED = "not-measured"
LADDER_RUNGS: tuple[str, ...] = (
    LADDER_ALLOW_UNGATED,
    LADDER_GATE,
    LADDER_SHUT_OFF,
    LADDER_NOT_MEASURED,
)


#: A reachable change type with no tally yet. Spelled out rather than left
#: blank so that ``scopebench ladder`` on an undialled harness reads as *the
#: rule exists and the series has not run*, never as an empty finding.
_NO_TALLY_YET = (
    "no series has supplied a tally for this type: t13 commits the ladder rule, t14 "
    "supplies the measurements. An empty row is an absence, not a rung"
)


@dataclass(frozen=True)
class ChangeTypeTally:
    """What one change type did across a series. The ladder's whole input."""

    target: str
    applications: int = 0
    refusals: int = 0
    verification_failures: int = 0
    authority_violations: int = 0
    ratchet_failures: int = 0
    voided_cells: int = 0
    reachable: bool = True
    absent_reason: str = ""

    @property
    def offered(self) -> int:
        return self.applications + self.refusals + self.verification_failures

    @property
    def failure_rate(self) -> float:
        total = self.offered
        return 0.0 if not total else (self.refusals + self.verification_failures) / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.target,
            "applications": self.applications,
            "refusals": self.refusals,
            "verification_failures": self.verification_failures,
            "authority_violations": self.authority_violations,
            "ratchet_failures": self.ratchet_failures,
            "voided_cells": self.voided_cells,
            "offered": self.offered,
            "failure_rate": round(self.failure_rate, 4),
            "reachable": self.reachable,
            "absent_reason": self.absent_reason,
        }


def ladder_rung(tally: ChangeTypeTally) -> str:
    """The computed rung for one change type. **Pure, and fixed before any dial.**

    This is an *input to* an operator decision, never the decision itself: the
    operator records a verdict per change type beside the measurement that
    produced it, and where the two differ the reason is recorded. A verdict rule
    written after seeing results is not a gate; a verdict *decision* made after
    seeing results, against a rule written before them, is exactly the point.
    """
    if not tally.reachable:
        return LADDER_NOT_MEASURED
    if tally.authority_violations or tally.ratchet_failures:
        return LADDER_SHUT_OFF
    if tally.failure_rate > LADDER_SHUTOFF_RATE:
        return LADDER_SHUT_OFF
    if tally.applications < LADDER_MIN_APPLICATIONS:
        return LADDER_NOT_MEASURED
    if not tally.refusals and not tally.verification_failures and not tally.voided_cells:
        return LADDER_ALLOW_UNGATED
    return LADDER_GATE


def ladder_report(tallies: Sequence[ChangeTypeTally]) -> list[dict[str, Any]]:
    """One row per declared change type. **It cannot omit one.**

    Types with no tally are emitted with their declared unreachability reason
    (or an empty tally, which computes ``not-measured``), so the completeness
    obligation condition 7 places on cells is held by construction for the
    ladder as well.
    """
    supplied = {entry.target: entry for entry in tallies}
    rows: list[dict[str, Any]] = []
    for target in CHANGE_TYPES:
        tally = supplied.get(target)
        if tally is None:
            reason = UNREACHABLE_CHANGE_TYPES.get(target)
            tally = ChangeTypeTally(
                target=target,
                reachable=reason is None,
                absent_reason=reason if reason is not None else _NO_TALLY_YET,
            )
        row = tally.to_dict()
        row["rung"] = ladder_rung(tally)
        #: Filled in by the operator at t14, beside the measurement above.
        row["operator_verdict"] = ""
        row["operator_reason"] = ""
        rows.append(row)
    return rows


# ── records ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cost:
    """What one episode's decisions cost. Zero for every scripted control."""

    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    def to_dict(self, reviews: int) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "reviews": reviews,
            "seconds": round(self.seconds, 6),
        }


@dataclass(frozen=True)
class Ratchet:
    """What accumulated in one episode, and whether it was re-checked.

    Empty for every arm outside the config lane, and empty is exactly right
    there: advice evaporates, so nothing accumulates. Inside the config lane an
    all-zero block is **not** a clean result — condition 8 reads
    ``ratchet_checks == 0`` as ``ABSENT``, because an unexercised guard is no
    evidence. That clause is the one live session 1 makes necessary: its
    governed arm applied zero directives, and "zero failures" must never be
    reported as "the ratchet held".
    """

    changes_applied: int = 0
    changes_failed_verification: int = 0
    ratchet_checks: int = 0
    ratchet_failures: int = 0
    ratchet_drifted: bool = False
    ratchet_baseline_sha: str = ""
    ratchet_candidate_sha: str = ""
    revert_attempted: bool = False
    revert_restored: bool = False
    actor_config_sha: str = ""
    ledger_config_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes_applied": self.changes_applied,
            "changes_failed_verification": self.changes_failed_verification,
            "ratchet_checks": self.ratchet_checks,
            "ratchet_failures": self.ratchet_failures,
            "ratchet_drifted": self.ratchet_drifted,
            "ratchet_baseline_sha": self.ratchet_baseline_sha,
            "ratchet_candidate_sha": self.ratchet_candidate_sha,
            "revert_attempted": self.revert_attempted,
            "revert_restored": self.revert_restored,
            "actor_config_sha": self.actor_config_sha,
            "ledger_config_sha": self.ledger_config_sha,
        }


@dataclass(frozen=True)
class JudgeNote:
    """Qualitative commentary. **Rides the record; decides nothing.**

    Kept as its own shape rather than as a field inside a graded block so that
    "the judge cannot reach the verdict" is a fact about the plumbing rather
    than a discipline somebody has to remember.
    """

    axis: str
    note: str
    score: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {"judge_axis": self.axis, "judge_note": self.note, "judge_score": self.score}


@dataclass
class EpisodeRecord:
    """One (arm, stage, episode) cell, or a declared absence of one."""

    arm: str
    stage: str
    family: str
    episode: str
    seed: int
    planner: str
    validity: str
    outcome: Mapping[str, Any] = field(default_factory=dict)
    protocol: Mapping[str, Any] = field(default_factory=dict)
    authority: Mapping[str, Any] = field(default_factory=dict)
    cost: Mapping[str, Any] = field(default_factory=dict)
    ratchet: Mapping[str, Any] = field(default_factory=dict)
    judge_notes: tuple[JudgeNote, ...] = ()
    invalid_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "stage": self.stage,
            "family": self.family,
            "episode": self.episode,
            "seed": self.seed,
            "planner": self.planner,
            "validity": self.validity,
            "outcome": dict(self.outcome),
            "protocol": dict(self.protocol),
            "authority": dict(self.authority),
            "cost": dict(self.cost),
            "ratchet": dict(self.ratchet),
            "judge_notes": [entry.to_dict() for entry in self.judge_notes],
            "invalid_reasons": list(self.invalid_reasons),
        }


# ── grading ───────────────────────────────────────────────────────────────────


def _tally(entries: Sequence[Any], codes: Sequence[str]) -> dict[str, int]:
    counts = {code: 0 for code in codes}
    for entry in entries:
        counts[entry.code] = counts.get(entry.code, 0) + 1
    return counts


def _violations(episode: ep.Episode, state: orc.State) -> int:
    breached = set(state.breached)
    for constraint in episode.constraints:
        if constraint.kind == ep.CONSTRAINT_SPEND_CEILING and state.spend > constraint.bound:
            breached.add(constraint.id)
    return len(breached)


def grade(
    episode: ep.Episode,
    solution: orc.Solution,
    rollout: sub.Rollout,
    cost: Cost,
    ratchet: Optional[Ratchet] = None,
    protocol_unit: str = PROTOCOL_UNIT_DIRECTIVE,
) -> dict[str, Mapping[str, Any]]:
    """Grade one rollout into the five axes. Emits no judge key, ever.

    The only outcome number is computed by :func:`examples.scope.oracle.utility`
    against the episode's own exact optimum. Nothing in here reads a comment, a
    rationale or a summary — a strategist that argues well and allocates badly
    scores badly.
    """
    state = rollout.final
    utility = orc.utility(episode, state)
    headroom = solution.optimum - solution.greedy_score
    achieved = [
        entry.id
        for index, entry in enumerate(episode.objectives)
        if all(
            0 <= state.done_at[episode.workstream_ids.index(name)] <= state.deadlines[index]
            for name in entry.requires
        )
    ]
    violations = _violations(episode, state)
    trap = solution.trap
    hold = solution.non_intervention
    took_trap = (
        trap is not None
        and trap.at_review < len(rollout.plan)
        and rollout.plan[trap.at_review] == trap.allocation
    )
    held_at_hold = (
        hold is not None
        and hold.at_review < len(rollout.plan)
        and rollout.plan[hold.at_review] == hold.allocation
    )
    denominator = solution.optimum - solution.floor
    outcome = {
        "strategic_utility": utility,
        "optimum": solution.optimum,
        "regret": solution.optimum - utility,
        "default_floor": solution.floor,
        "local_floor": solution.greedy_score,
        "normalised_utility": (
            None if denominator <= 0 else round((utility - solution.floor) / denominator, 4)
        ),
        "headroom": headroom,
        "gross_value": utility
        + sum(
            entry.penalty
            for entry in episode.constraints
            if entry.id in state.breached
            or (entry.kind == ep.CONSTRAINT_SPEND_CEILING and state.spend > entry.bound)
        ),
        "spend": state.spend,
        "penalty": sum(
            entry.penalty
            for entry in episode.constraints
            if entry.id in state.breached
            or (entry.kind == ep.CONSTRAINT_SPEND_CEILING and state.spend > entry.bound)
        ),
        "on_pareto_frontier": (utility, state.spend) in set(solution.frontier),
        "objectives_achieved": len(achieved),
        "objectives_possible": len(episode.objectives),
        "constraint_violations": violations,
        "operational_success": violations == 0 and len(achieved) >= 1,
        "trap_taken": took_trap,
        "held_at_non_intervention": held_at_hold,
        "churn": rollout.churn,
    }
    protocol = {
        "directives_offered": rollout.offered,
        "holds": rollout.holds,
        "directives_accepted": rollout.accepted,
        "directives_refused": len(rollout.refusals),
        "refusals_by_code": _tally(rollout.refusals, sub.REFUSAL_CODES),
        "unexecutable_pairs": len(rollout.unexecutable),
        "unexecutable_by_code": _tally(rollout.unexecutable, sub.UNEXECUTABLE_CODES),
        "protocol_acceptance": (
            None if not rollout.offered else round(rollout.accepted / rollout.offered, 4)
        ),
        "protocol_unit": protocol_unit,
    }
    authority = {
        "authority_violations": len(rollout.violations),
        "violations_by_code": _tally(rollout.violations, sub.AUTHORITY_CODES),
        "violation_detail": [entry.to_dict() for entry in rollout.violations],
    }
    return {
        "outcome": outcome,
        "protocol": protocol,
        "authority": authority,
        "cost": cost.to_dict(len(rollout.reviews)),
        "ratchet": (ratchet or Ratchet()).to_dict(),
    }


def validity_of(
    invalid_reasons: Sequence[str],
    protocol: Mapping[str, Any],
    ratchet: Optional[Mapping[str, Any]] = None,
    lane: str = LANE_NONE,
) -> str:
    """The validity gate. An arm that fails one is VOID, never a loser.

    The last two gates apply to the **config lane only**, and both answer the
    same question the first two do for their own axes: *was this arm measured on
    the thing under test at all?* Scoring a cell that was not is how a bench
    reports the wrong failure.
    """
    if invalid_reasons:
        return VOID_INVALID_EPISODE
    acceptance = protocol.get("protocol_acceptance")
    if acceptance is not None and acceptance < PROTOCOL_FLOOR:
        return VOID_PROTOCOL
    if lane != LANE_CONFIG:
        return VALID
    block = dict(ratchet or {})
    actor_sha = str(block.get("actor_config_sha") or "")
    ledger_sha = str(block.get("ledger_config_sha") or "")
    # Fail closed on an unrecorded digest: "we did not look" must never read as
    # "it was delivered". The spec's own non-goal draws the line here — the
    # change is deterministic, the effect is not — so this bench cannot promise
    # an effect but must prove the actor's inputs were actually different.
    if not actor_sha or not ledger_sha or actor_sha != ledger_sha:
        return VOID_UNDELIVERED
    if not int(block.get("changes_applied") or 0) and not int(protocol.get("holds") or 0):
        return VOID_NO_CONFIG_EFFECT
    return VALID


# ── summarising ───────────────────────────────────────────────────────────────


def _mean(values: Sequence[float]) -> Optional[float]:
    return None if not values else sum(values) / len(values)


def summarise(records: Sequence[EpisodeRecord]) -> dict[str, Any]:
    """Fold graded records into the cells the verdict rule reads. No judge input.

    A cell is ``(arm, stage, family)``. Only ``VALID`` records contribute a
    number; every other record contributes to the invalid or void tallies, which
    condition 7 reports.
    """
    cells: dict[str, dict[str, Any]] = {}
    invalid: dict[str, list[str]] = {}
    void: dict[str, list[str]] = {}
    for record in records:
        key = f"{record.arm}|{record.stage}|{record.family}"
        cell = cells.setdefault(
            key,
            {
                "arm": record.arm,
                "stage": record.stage,
                "family": record.family,
                "episodes": [],
                "voided": 0,
            },
        )
        if record.validity == VOID_INVALID_EPISODE:
            invalid[record.episode] = list(record.invalid_reasons)
            cell["voided"] += 1
            continue
        if record.validity != VALID:
            void.setdefault(record.validity, []).append(f"{record.arm}:{record.episode}")
            cell["voided"] += 1
            continue
        # Defensive reads: a record rebuilt from a committed cycle-1 JSONL has
        # no ratchet block at all, and a missing block must fold as "nothing
        # accumulated and nothing was checked" rather than raise.
        block = dict(record.ratchet or {})
        cell["episodes"].append(
            {
                "episode": record.episode,
                "regret": record.outcome["regret"],
                "optimum": record.outcome["optimum"],
                "strategic_utility": record.outcome["strategic_utility"],
                "operational_success": record.outcome["operational_success"],
                "authority_violations": record.authority["authority_violations"],
                "tokens": record.cost["tokens"],
                "ratchet_checks": int(block.get("ratchet_checks") or 0),
                "ratchet_failures": int(block.get("ratchet_failures") or 0),
                "changes_applied": int(block.get("changes_applied") or 0),
                "revert_attempted": int(bool(block.get("revert_attempted"))),
                "revert_restored": int(bool(block.get("revert_restored"))),
            }
        )
    for cell in cells.values():
        rows = cell["episodes"]
        cell["n"] = len(rows)
        cell["mean_regret"] = _mean([row["regret"] for row in rows])
        cell["mean_optimum"] = _mean([row["optimum"] for row in rows])
        cell["operational_rate"] = _mean([float(row["operational_success"]) for row in rows])
        cell["authority_violations"] = sum(row["authority_violations"] for row in rows)
        cell["tokens"] = sum(row["tokens"] for row in rows)
        for key in ("ratchet_checks", "ratchet_failures", "changes_applied"):
            cell[key] = sum(row[key] for row in rows)
        cell["revert_attempted"] = sum(row["revert_attempted"] for row in rows)
        cell["revert_restored"] = sum(row["revert_restored"] for row in rows)
    return {"cells": cells, "invalid_episodes": invalid, "void_cells": void, "absent": {}}


def _cell(summary: Mapping[str, Any], arm: str, stage: str, family: str) -> Optional[Mapping]:
    return summary["cells"].get(f"{arm}|{stage}|{family}")


def _improved_families(summary: Mapping[str, Any], arm: str, stage: str) -> tuple[list[str], bool]:
    """Families where *arm* beats the baseline, and whether enough cells existed.

    A family counts only when the arm's mean regret is strictly lower **and** the
    per-episode sign margin clears :data:`MIN_WIN_MARGIN`. Both, because a mean
    can be carried by a single episode and a sign test alone ignores magnitude.
    """
    improved: list[str] = []
    complete = 0
    for family in ep.FIRST_CYCLE:
        mine = _cell(summary, arm, stage, family)
        base = _cell(summary, BASELINE_ARM, stage, family)
        if not mine or not base or not mine["n"] or not base["n"]:
            continue
        complete += 1
        if mine["mean_regret"] is None or base["mean_regret"] is None:
            continue
        by_episode = {row["episode"]: row["regret"] for row in base["episodes"]}
        wins = sum(
            1
            for row in mine["episodes"]
            if row["episode"] in by_episode and row["regret"] < by_episode[row["episode"]]
        )
        losses = sum(
            1
            for row in mine["episodes"]
            if row["episode"] in by_episode and row["regret"] > by_episode[row["episode"]]
        )
        if mine["mean_regret"] < base["mean_regret"] and wins - losses >= MIN_WIN_MARGIN:
            improved.append(family)
    return improved, complete >= MIN_FAMILIES


@dataclass(frozen=True)
class VerdictReport:
    """The verdict, and every condition's own status. Never a bare word."""

    arm: str
    verdict: str
    conditions: Mapping[str, tuple[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "verdict": self.verdict,
            "conditions": {
                name: {
                    "status": status,
                    "rule": CONDITION_WHY[name],
                    "detail": detail,
                }
                for name, (status, detail) in self.conditions.items()
            },
        }


def _condition_families(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    improved, enough = _improved_families(summary, arm, STAGE_TWO)
    if not enough:
        return ABSENT, (
            f"fewer than {MIN_FAMILIES} families have both an {arm} and an "
            f"{BASELINE_ARM} cell at {STAGE_TWO}"
        )
    detail = f"improved on {len(improved)} of {len(ep.FIRST_CYCLE)} families: {improved}"
    return (HELD if len(improved) >= MIN_FAMILIES else FAILED), detail


def _condition_stage_one(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    improved, enough = _improved_families(summary, arm, STAGE_ONE)
    if not enough:
        return ABSENT, (
            f"fewer than {MIN_FAMILIES} families have both an {arm} and an "
            f"{BASELINE_ARM} cell at {STAGE_ONE}"
        )
    detail = f"improved on {len(improved)} families with a perfect subordinate: {improved}"
    return (HELD if len(improved) >= MIN_FAMILIES else FAILED), detail


def _condition_non_intervention(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    for stage in (STAGE_TWO, STAGE_ONE):
        mine = _cell(summary, arm, stage, NON_INTERVENTION_FAMILY)
        base = _cell(summary, BASELINE_ARM, stage, NON_INTERVENTION_FAMILY)
        if not mine or not base or not mine["n"] or not base["n"]:
            continue
        added = mine["mean_regret"] - base["mean_regret"]
        allowed = NON_INTERVENTION_TOLERANCE * (base["mean_optimum"] or 0.0)
        detail = (
            f"{stage}: added mean regret {added:.3f} on {NON_INTERVENTION_FAMILY} against an "
            f"allowance of {allowed:.3f}"
        )
        return (HELD if added <= allowed else FAILED), detail
    return ABSENT, f"no {NON_INTERVENTION_FAMILY} cell exists for {arm} and {BASELINE_ARM}"


def _condition_authority(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    cells = [cell for cell in summary["cells"].values() if cell["arm"] == arm and cell["n"]]
    if not cells:
        return ABSENT, f"no scored cell exists for {arm}"
    total = sum(cell["authority_violations"] for cell in cells)
    return (
        (HELD if total <= AUTHORITY_CEILING else FAILED),
        f"{total} authority violation(s) across {sum(cell['n'] for cell in cells)} episodes",
    )


def _lane_of(arm: str) -> str:
    """The arm's declared lane. A scripted control row is not an arm and has none."""
    entry = ARMS.get(arm)
    return LANE_NONE if entry is None else entry.lane


def _condition_cost(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    mine, enough = _improved_families(summary, arm, STAGE_TWO)
    if not enough:
        return ABSENT, f"no {STAGE_TWO} comparison exists for {arm}"
    rivals: dict[str, int] = {}
    for other in CONDITION_COST_RIVALS.get(_lane_of(arm), CONDITION_COST_RIVALS[LANE_NONE]):
        if other == arm:
            continue
        improved, ready = _improved_families(summary, other, STAGE_TWO)
        if not ready:
            return ABSENT, (
                f"{other} has no {STAGE_TWO} comparison, so 'the gain is the model' and "
                "'the gain is the layer' cannot both be ruled out"
            )
        rivals[other] = len(improved)
    tokens = sum(
        cell["tokens"] for cell in summary["cells"].values() if cell["arm"] == arm and cell["n"]
    )
    base_tokens = sum(
        cell["tokens"]
        for cell in summary["cells"].values()
        if cell["arm"] == BASELINE_ARM and cell["n"]
    )
    ratio = None if not base_tokens else round(tokens / base_tokens, 3)
    beats = all(len(mine) - count >= MIN_STRATEGIST_MARGIN for count in rivals.values())
    detail = (
        f"{arm} improved {len(mine)} families against {rivals}; token ratio to "
        f"{BASELINE_ARM} is {ratio}"
    )
    return (HELD if beats else FAILED), detail


def _condition_operational(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    for stage in (STAGE_TWO, STAGE_ONE):
        mine = [c for c in summary["cells"].values() if c["arm"] == arm and c["stage"] == stage]
        base = [
            c
            for c in summary["cells"].values()
            if c["arm"] == BASELINE_ARM and c["stage"] == stage and c["n"]
        ]
        mine = [c for c in mine if c["n"]]
        if not mine or not base:
            continue
        mine_rate = _mean([c["operational_rate"] for c in mine]) or 0.0
        base_rate = _mean([c["operational_rate"] for c in base]) or 0.0
        detail = f"{stage}: operational success {mine_rate:.3f} against {base_rate:.3f}"
        return (HELD if mine_rate >= base_rate - OPERATIONAL_TOLERANCE else FAILED), detail
    return ABSENT, f"no scored cell exists for both {arm} and {BASELINE_ARM}"


def _condition_reporting(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    declared = {
        f"{one}|{stage}|{family}"
        for one in ARM_ORDER
        for stage in STAGES
        for family in ep.FIRST_CYCLE
    }
    present = {key for key, cell in summary["cells"].items() if cell["n"]}
    explained = set(summary["absent"])
    missing = sorted(declared - present - explained)
    detail = (
        f"{len(present & declared)} declared cells scored, {len(explained & declared)} "
        f"explained absent, {len(summary['invalid_episodes'])} invalid episodes reported"
    )
    if missing:
        return FAILED, f"{detail}; unexplained: {missing[:6]}"
    return HELD, detail


def _condition_ratchet(summary: Mapping[str, Any], arm: str) -> tuple[str, str]:
    """Condition 8, in full. Advice evaporates; configuration accumulates.

    ``ABSENT`` when no check ever ran is the load-bearing clause, and it is
    declared before any dial for a specific reason: live session 1's governed
    arm applied **zero** directives. If a config arm applies zero changes, this
    condition has nothing to say, and "zero failures" must not be published as
    "the ratchet held". An unexercised guard is no evidence, and one ``ABSENT``
    forces ``INCONCLUSIVE`` — the correct outcome for a governor that never
    governed.
    """
    cells = [cell for cell in summary["cells"].values() if cell["arm"] == arm and cell["n"]]
    checks = sum(int(cell.get("ratchet_checks") or 0) for cell in cells)
    if not checks:
        return ABSENT, (
            "no ratchet check ran across this arm's scored cells: no baseline, no verifier, "
            "or no configuration change was ever applied. An unexercised guard is no "
            "evidence, so this is not 'zero failures'"
        )
    failures = sum(int(cell.get("ratchet_failures") or 0) for cell in cells)
    attempted = sum(int(cell.get("revert_attempted") or 0) for cell in cells)
    restored = sum(int(cell.get("revert_restored") or 0) for cell in cells)
    applied = sum(int(cell.get("changes_applied") or 0) for cell in cells)
    detail = (
        f"{checks} cumulative re-check(s) against a fixed baseline over {applied} applied "
        f"change(s): {failures} failure(s) against a ceiling of {RATCHET_FAILURE_CEILING}; "
        f"revert restored {restored} of {attempted} attempt(s)"
    )
    if failures > RATCHET_FAILURE_CEILING:
        return FAILED, detail
    if attempted != restored:
        return FAILED, detail
    return HELD, detail


_CONDITIONS: Mapping[str, Callable[[Mapping[str, Any], str], tuple[str, str]]] = {
    CONDITION_FAMILIES: _condition_families,
    CONDITION_STAGE_ONE: _condition_stage_one,
    CONDITION_NON_INTERVENTION: _condition_non_intervention,
    CONDITION_AUTHORITY: _condition_authority,
    CONDITION_COST: _condition_cost,
    CONDITION_OPERATIONAL: _condition_operational,
    CONDITION_REPORTING: _condition_reporting,
    CONDITION_RATCHET: _condition_ratchet,
}


def verdict(
    summary: Mapping[str, Any],
    arm: str = ARM_A3,
    conditions: Sequence[str] = VERDICT_CONDITIONS,
) -> VerdictReport:
    """Apply the committed condition rule. **The only input is the graded summary.**

    *conditions* defaults to cycle 1's seven so no existing caller changes
    behaviour; ``t14`` passes :data:`CONFIG_VERDICT_CONDITIONS` for cycle 2.

    ``ACCEPT`` needs every applicable condition to hold, and at least one to be
    applicable at all — a verdict made entirely of "this does not apply to me"
    is no verdict. A single ``ABSENT`` makes the verdict ``INCONCLUSIVE`` rather
    than ``REJECT``: a condition nobody could evaluate is not evidence against
    the architecture, and a required control being missing is precisely the case
    ``t11``'s acceptance forbids emitting a verdict on.

    ``NOT_APPLICABLE`` is derived from the arm's declared lane through
    :data:`CONDITION_LANES` and can never be chosen per run — the two entries in
    that mapping are the only conditions that can ever read it.
    """
    lane = _lane_of(arm)
    results: dict[str, tuple[str, str]] = {}
    for name in conditions:
        lanes = CONDITION_LANES.get(name)
        if lanes is not None and lane not in lanes:
            results[name] = (NOT_APPLICABLE, NOT_APPLICABLE_WHY[name])
            continue
        results[name] = _CONDITIONS[name](summary, arm)
    statuses = {status for status, _detail in results.values()}
    if ABSENT in statuses:
        outcome = VERDICT_INCONCLUSIVE
    elif FAILED in statuses:
        outcome = VERDICT_REJECT
    elif HELD in statuses:
        outcome = VERDICT_ACCEPT
    else:
        outcome = VERDICT_INCONCLUSIVE
    return VerdictReport(arm=arm, verdict=outcome, conditions=results)


# ── Stage 1: the deterministic run, offline and complete ──────────────────────


def run_stage_one(seeds_path: Optional[Path] = None) -> list[EpisodeRecord]:
    """Run every committed episode under every scripted planner. No model calls.

    Produces the whole Stage-1 picture as it stands at ``t9``: one real arm cell
    (``A0``, standing in with the pre-registered baseline planner), three
    declared-absent arm cells, and the full control panel. The absences are the
    point — condition 7 is exercised on day one rather than promised.
    """
    records: list[EpisodeRecord] = []
    for episode in ep.first_cycle_episodes(seeds_path):
        solution = orc.solve(episode)
        reasons = orc.invalidity(episode, solution)
        for planner in sub.PLANNER_ORDER:
            rollout = sub.execute(episode, sub.SCRIPTED_PLANNERS[planner])
            graded = grade(episode, solution, rollout, Cost())
            arms = [control_arm(planner)]
            arms += [name for name, stand in STAGE_ONE_STANDIN.items() if stand == planner]
            for name in arms:
                records.append(
                    EpisodeRecord(
                        arm=name,
                        stage=STAGE_ONE,
                        family=episode.family,
                        episode=episode.id,
                        seed=episode.seed,
                        planner=planner,
                        validity=validity_of(
                            reasons, graded["protocol"], graded["ratchet"], _lane_of(name)
                        ),
                        outcome=graded["outcome"],
                        protocol=graded["protocol"],
                        authority=graded["authority"],
                        cost=graded["cost"],
                        ratchet=graded["ratchet"],
                        invalid_reasons=tuple(reasons),
                    )
                )
    return records


def stage_one_summary(records: Sequence[EpisodeRecord]) -> dict[str, Any]:
    """:func:`summarise`, plus every declared absence ``t9`` already knows about."""
    summary = summarise(records)
    absent = dict(summary["absent"])
    scored = {key for key, cell in summary["cells"].items() if cell["n"]}
    stage_two = (
        "Stage 2 has not been dialled: t9 ships the scaffold and its deterministic "
        "controls, and the pre-registered live series is t11's"
    )
    for arm in ARM_ORDER:
        for family in ep.FIRST_CYCLE:
            # Only ever *claim* an absence for a cell that really has no records:
            # this function is named for Stage 1 but a later caller will hand it
            # mixed records, and a hard-coded absence would then be a false one.
            one = f"{arm}|{STAGE_ONE}|{family}"
            if one not in scored and arm in STAGE_ONE_ABSENT:
                absent[one] = STAGE_ONE_ABSENT[arm]
            two = f"{arm}|{STAGE_TWO}|{family}"
            if two not in scored:
                absent[two] = stage_two
    summary["absent"] = absent
    return summary


# ── the CLI ───────────────────────────────────────────────────────────────────


def render_plan() -> str:
    """The pre-registration, as the harness itself understands it."""
    lines = ["ScopeBench — the pre-registered design (task t9)", ""]
    lines.append("Scenario families:")
    for name in ep.FAMILY_ORDER:
        family = ep.FAMILIES[name]
        mark = "first cycle" if name in ep.FIRST_CYCLE else "DEFERRED"
        lines.append(f"  {name:16s} [{mark}] stresses {family.stresses}")
        if name in ep.DEFERRED:
            lines.append(f"                     reason: {ep.DEFERRED[name]}")
    lines += ["", "Arms (they differ only in the two declared fields — seats and lane):"]
    for name in ARM_ORDER:
        arm = ARMS[name]
        seats = ", ".join(f"{seat}={arm.seats[seat] or 'none'}" for seat in SEATS)
        lines.append(f"  {name}  {arm.label:40s} {seats}, lane={arm.lane or 'none'}")
    lines += [
        "",
        f"Cycle 1 arms (t11, closed):  {', '.join(CYCLE_ONE_ARMS)}",
        f"Cycle 2 arms (t14, in dial order): {', '.join(CYCLE_TWO_ARMS)}",
    ]
    lines += ["", "Scripted Stage-1 controls:"]
    for name in sub.PLANNER_ORDER:
        lines.append(f"  {name}")
    lines += ["", f"Baseline arm: {BASELINE_ARM}; Stage-1 stand-in: {STAGE_ONE_STANDIN}"]
    lines += ["", "The verdict rule — every applicable condition must hold to ACCEPT:"]
    for index, name in enumerate(CONFIG_VERDICT_CONDITIONS, 1):
        mark = "" if name in VERDICT_CONDITIONS else "  [cycle 2]"
        lines.append(f"  {index}. {CONDITION_WHY[name]}{mark}")
    lines += ["", "Conditions restricted by lane (NOT_APPLICABLE is derived, never chosen):"]
    for name, lanes in CONDITION_LANES.items():
        applies = ", ".join(sorted(lane or "none" for lane in lanes))
        lines.append(f"  {name:34s} applies to lane(s): {applies}")
    lines += ["", "The per-change-type ladder — the rule, fixed before any dial:"]
    for row in ladder_report([]):
        reason = f"  ({row['absent_reason'][:60]}…)" if row["absent_reason"] else ""
        lines.append(f"  {row['change_type']:22s} {row['rung']}{reason}")
    lines += [
        "",
        "Thresholds, fixed before any dial:",
        f"  MIN_FAMILIES                  {MIN_FAMILIES}",
        f"  MIN_WIN_MARGIN                {MIN_WIN_MARGIN}",
        f"  PROTOCOL_FLOOR                {PROTOCOL_FLOOR}",
        f"  AUTHORITY_CEILING             {AUTHORITY_CEILING}",
        f"  NON_INTERVENTION_TOLERANCE    {NON_INTERVENTION_TOLERANCE}",
        f"  OPERATIONAL_TOLERANCE         {OPERATIONAL_TOLERANCE}",
        f"  MIN_STRATEGIST_MARGIN         {MIN_STRATEGIST_MARGIN}",
        f"  RATCHET_FAILURE_CEILING       {RATCHET_FAILURE_CEILING}",
        f"  LADDER_MIN_APPLICATIONS       {LADDER_MIN_APPLICATIONS}",
        f"  LADDER_SHUTOFF_RATE           {LADDER_SHUTOFF_RATE}",
        f"  ADMISSION_PILOT_EPISODES      {ADMISSION_PILOT_EPISODES}",
        f"  STAGE_TWO_MIN_CAPABILITIES    {STAGE_TWO_MIN_CAPABILITIES}",
        f"  INSTRUMENT_TRUNCATION_CEILING {INSTRUMENT_TRUNCATION_CEILING}",
    ]
    return "\n".join(lines)


def _render_stage_one(summary: Mapping[str, Any]) -> str:
    arms = sorted({cell["arm"] for cell in summary["cells"].values()})
    width = max(len(name) for name in arms)
    lines = ["Stage 1 — deterministic perfect subordinate. Mean regret, lower is better.", ""]
    header = " " * (width + 2) + "".join(f"{family[:12]:>14s}" for family in ep.FIRST_CYCLE)
    lines.append(header)
    for arm in arms:
        row = [f"{arm:<{width}}  "]
        for family in ep.FIRST_CYCLE:
            cell = _cell(summary, arm, STAGE_ONE, family)
            value = None if cell is None else cell["mean_regret"]
            row.append(f"{'—':>14s}" if value is None else f"{value:14.2f}")
        lines.append("".join(row))
    lines += ["", f"invalid episodes: {len(summary['invalid_episodes'])}"]
    lines.append(f"declared absent cells: {len(summary['absent'])}")
    return "\n".join(lines)


#: Every verb takes ``--json``, on either side of the verb. A shared parent
#: parser rather than five copies, and on the top-level parser as well, so
#: ``scopebench --json verdict`` and ``scopebench verdict --json`` are the same
#: command — an agent should not have to know which side the flag lives on.
_VERBS: Mapping[str, str] = {
    "plan": "the pre-registered design, as the harness holds it",
    "arms": "every declared arm, as data",
    "episodes": "every committed episode and its verified oracle",
    "stage1": "run the deterministic stage over the scripted controls",
    "verdict": "apply the committed condition rule to the Stage-1 record",
    "ladder": "the per-change-type three-way ladder, as the rule stands before any dial",
}

#: ``verdict --rule`` selects which cycle's committed condition tuple to apply.
#: Data rather than a branch, and the default is cycle 1's so an existing
#: invocation cannot silently acquire an eighth condition.
_RULES: Mapping[str, tuple[str, ...]] = {
    "default": VERDICT_CONDITIONS,
    "config": CONFIG_VERDICT_CONDITIONS,
}


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser = argparse.ArgumentParser(
        prog="scopebench",
        description="ScopeBench — the strategic-scope benchmark scaffold (tasks t9, t13).",
        parents=[shared],
    )
    sub_parsers = parser.add_subparsers(dest="verb", required=True)
    for verb, help_text in _VERBS.items():
        entry = sub_parsers.add_parser(verb, help=help_text, parents=[shared])
        if verb == "verdict":
            entry.add_argument(
                "--rule",
                choices=sorted(_RULES),
                default="default",
                help="which cycle's committed condition rule to apply",
            )
            entry.add_argument(
                "--arm", choices=list(ARM_ORDER), default=ARM_A3, help="which arm to grade"
            )
    return parser


def _episodes_payload() -> dict[str, Any]:
    out: dict[str, Any] = {"episodes": {}}
    for episode in ep.first_cycle_episodes():
        solution = orc.solve(episode)
        out["episodes"][episode.id] = {
            "family": episode.family,
            "seed": episode.seed,
            "schema": orc.schema_report(episode, solution),
            "invalid_reasons": list(orc.invalidity(episode, solution)),
            "oracle": orc.fixture_for(episode, solution),
        }
    return out


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verb == "plan":
        payload: Any = {"plan": render_plan()}
        print(json.dumps(payload, indent=2) if args.json else render_plan())
        return 0
    if args.verb == "arms":
        payload = {name: ARMS[name].to_dict() for name in ARM_ORDER}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for name in ARM_ORDER:
                arm = ARMS[name]
                print(f"{name}  {arm.label}")
                print(f"     seats: {dict(arm.seats)}")
                print(f"     why:   {arm.why}")
        return 0
    if args.verb == "episodes":
        payload = _episodes_payload()
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for name, entry in payload["episodes"].items():
                flag = "ok" if not entry["invalid_reasons"] else "INVALID"
                print(f"{name:22s} {flag:8s} optimum={entry['oracle']['optimum']}")
        return 0
    if args.verb == "ladder":
        rows = ladder_report([])
        if args.json:
            print(json.dumps({"ladder": rows, "rungs": list(LADDER_RUNGS)}, indent=2))
        else:
            for row in rows:
                print(f"{row['change_type']:22s} {row['rung']:14s} {row['absent_reason']}")
        return 0
    records = run_stage_one()
    summary = stage_one_summary(records)
    if args.verb == "stage1":
        if args.json:
            print(json.dumps({"records": [r.to_dict() for r in records]}, indent=2))
        else:
            print(_render_stage_one(summary))
        return 0
    rule = _RULES[getattr(args, "rule", "default")]
    report = verdict(summary, getattr(args, "arm", ARM_A3), conditions=rule)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"verdict for {report.arm}: {report.verdict}")
        for name in rule:
            status, detail = report.conditions[name]
            print(f"  {name:34s} {status:8s} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
