#!/usr/bin/env python3
"""arch_policy — arm P, the compiled policy: the mind writes the strategy, the
strategy plays.

Plan task **t7** of `error-derived-timeouts-bee-hive-architecture`
(`docs/plans/2026-08-01-error-derived-timeouts-bee-hive-architecture.md`),
covering frame claims ``c18``/``h10`` (the compiled-policy arm, its three
mandatory controls, and escalation rate and call-acceptance as reported axes
that never share a number with outcome), ``c11``/``h15`` (bounded termination,
structurally) and ``c20`` (delegation to an artifact the cortex authored).

The claim this module builds
----------------------------
Every other arm in this family delegates **to a mind**. Arm P delegates **to a
program**:

===============  =================================================
arm              model calls for an N-decision episode
===============  =================================================
``E``/``W``      N — one or more per decision
``M``/``H``      N — plus a delegated drive per decision
``B0``/``B1``    N — one scoped call per decision (or zero, in B0)
**``P``**        **1** — the mind writes a policy, the policy plays
===============  =================================================

The cortex writes a unit-control policy as code **once**, that source runs in a
bounded, network-less container, and every decision after the first model call
costs a function application. The mind intervenes only at **declared
checkpoints**, which is what :data:`ESCALATE` is.

It also asks a question none of the other arms do: *can the mind externalise its
decision procedure?* Writing a policy that plays well is strictly harder than
playing well — it needs the mind to know **why** its moves are right, not only
to make them. A mind that plays at 5 of 6 and writes a policy that plays at 1 of
6 has told us something real about the gap between competence and articulable
competence.

Three controls, and none of them is optional
--------------------------------------------
A compiled strategy that is **syntactically valid and strategically inert** runs
to completion, produces legal moves, and looks exactly like a working arm. So
three controls ship *before* any measured dial, as arms dialable through the
same table, the same jail and the same grader:

=====================  ==========================================================
control                what it exists to separate
=====================  ==========================================================
``PR`` random           *the mind can compile a strategy* from *any program beats
                        per-turn control*. Legal moves, no strategy.
``PH`` hand-written     the ceiling. A compiled policy is measured against a
                        baseline someone wrote deliberately, not against zero.
``PN`` no-op            a policy from the harness. It returns nothing, so
                        anything it scores is the instrument scoring itself.
=====================  ==========================================================

:data:`POLICY_ARMS` holds all four as data and nothing here branches on an arm
id. The four differ in **exactly one field** — :attr:`PolicyArm.source` — which
is what makes the three controls controls rather than three different
experiments: same episode, same jail, same driver, same escalation grain, same
checkpoint role, same grader.

The jail is the whole safety property
-------------------------------------
**Policy source executes only inside the workspace.** Held by construction, in
the four places ``examples/challenge_coding.py`` established — this module is
that grader pointed at a policy instead of a puzzle, and reuses its extractor,
its nonce discipline and its vacuity gate rather than restating them:

1. **This module contains no execution primitive.** No ``exec``, ``eval``,
   ``compile``, ``__import__``, ``subprocess``, ``os.system``, ``runpy``,
   ``ctypes``. ``tests/test_arch_policy.py`` walks this file's AST *and its
   own* and fails if any appears — a suite that ran a policy "just to see what
   it produces" would be the exact hole the criterion names.
2. **There is exactly one execution call site**, :func:`run_in_jail`, which
   hands the program to :meth:`embodiment.workspace.MuseWorkspace.execute` as an
   argv element and nothing else. A test counts the call sites.
3. **No container, no run.** When nothing can be provisioned the verdict is
   :data:`VERDICT_NO_WORKSPACE`, the transition is recorded for the host
   (constraint C3), and the source is simply never executed. There is no
   local-fallback branch to reach.
4. **Committed sources take the identical path.** A control's source is data
   too: it is never imported, never called, never executed here. The only thing
   that distinguishes it from a model's source is where it was written.
5. **The empirical half.** The committed ``sentinel`` fixture's source writes a
   file on whatever machine runs it. After the whole pipeline has graded it, that
   path does not exist on this host — a fact about the pipeline, checked by
   test, rather than a reading of this file's source.

No ``policy=`` and no ``profile=`` is ever passed to headspace, so its
closed-by-default posture — network disabled, no host paths — is the only
posture reachable. That is :mod:`embodiment.workspace`'s property and is proved
there.

What the container is and is not trusted with
---------------------------------------------
Be precise here, because the obvious claim is stronger than the true one.
``challenge_coding``'s container holds nothing an answer could be read off. This
one **does** hold the observations a correct policy derives its answer from —
that is what a policy is. What it does not hold is (a) any truth label, (b) the
harness's own decision rule, and (c) the value of any feature the situation
declares unobserved. (a) and (c) are asserted by test over
:meth:`Situation.to_payload`; (b) is a fact about
:func:`truth_action`, which is host-side and never rendered into a program.

The result line carries a per-run nonce, so a policy's own stdout can never be
mistaken for the driver's, and **two** nonce lines are refused rather than
resolved.

"I don't know" is part of the contract
--------------------------------------
:data:`ESCALATE` is a first-class return, not an exception path. A policy that
declares its own incompleteness is *a router that grades itself*, and this
harness grades it: :func:`is_decidable` says, host-side, whether a situation
could have been decided from what the policy was shown, so every escalation is
classifiable as justified or not and every non-escalation as informed or
over-confident. That is the precedent grader arm ``H``'s routing question has
never had.

The escalation itself is a **worker-harness call** in claim ``c20``'s
vocabulary, not a delegated goal: a typed question over the situation's own
enumerated action space, authored here, answered advisory. It reuses
``arch_hive``'s :func:`~examples.arch_hive.answer_by_worker` and
:class:`~examples.arch_hive.AcceptanceLedger` outright, so an escalation and an
arm-B1 scoped call are measured on one instrument and their acceptance figures
are comparable.

Three axes, three disjoint key sets
-----------------------------------
Issue #33 measured 17 of 23 calls refused on a *shape* error. Here the model
**designs** the contract rather than satisfying one, so the same failure is
available in two new places — and interface failure, routing behaviour and
strategy failure must never share a number:

* :data:`OUTCOME_KEYS` — what the episode scored;
* :data:`ESCALATION_KEYS` — how often the policy asked, and whether it asked
  when it should have. Both extremes are informative: escalate always and the
  arm has collapsed into the worker arm with extra latency; escalate never and
  it may simply be unable to detect its own ignorance;
* :data:`ACCEPTANCE_KEYS` — whether calls were *accepted*: the policy's own
  protocol compliance, and the checkpoint call's, in two separate ledgers.

``tests/test_arch_policy.py`` asserts the three key sets are pairwise disjoint
rather than trusting the habit.

Termination, by construction
----------------------------
There is **no** ``while`` in this file and no thread of its own. Every ``for``
walks a settled sequence. :func:`plan_escalations` clamps the checkpoint count
from the committed budget *before* any call exists; concurrency is borrowed
from :func:`examples.arch_hive.dispatch`, whose single bounded wait, zero-timeout
reads and non-waiting teardown are proved over *its* AST next door. The
authoring lane is one completion with no retry — a retry would make arm P's
headline number 2, so the table refuses an ``authoring_calls`` that disagrees
with the arm.

**t6's structural prerequisite, discharged.** ``arch_arms._drive`` takes
``**kwargs``, so "no subagent seam" cannot be read off its call site. This
module goes further than enumerating: it starts **no drive at all** — arm P
holds no tool loop, so there is no :func:`embodiment.run` call site to inspect —
and **no function here accepts ``**kwargs`` and no call here unpacks one**, so
there is no passthrough hole an edit could route an argument through.

No live dial happens here
-------------------------
Every lane is hermetic or container-only; nothing in this file resolves a dial
or opens a socket to a model. Running the measured series is task ``t10``'s,
under task ``t9``'s pre-registration.

Usage::

    uv run python examples/arch_policy.py plan
    uv run python examples/arch_policy.py contract
    uv run python examples/arch_policy.py config --json
    uv run python examples/arch_policy.py run --provider docker
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment.contract import ModelResponse  # noqa: E402
from embodiment.workspace import (  # noqa: E402
    PROVIDER_DOCKER,
    PROVIDER_FAKE,
    WORKSPACE_TOOL_NAME,
    MuseWorkspace,
)
from examples import arch_arms as aa  # noqa: E402
from examples import arch_hive as ah  # noqa: E402
from examples import challenge_coding as cc  # noqa: E402

__all__ = [
    "ACCEPTANCE_KEYS",
    "ACTIONS",
    "ARM_BASELINE",
    "ARM_COMPILED",
    "ARM_NOOP",
    "ARM_RANDOM",
    "AUTHORING_SYSTEM",
    "COMMITTED_SOURCES",
    "CONTROL_KINDS",
    "ConfigError",
    "DECIDED",
    "DEFAULT_CONFIG_PATH",
    "DOMAIN",
    "ENTRY_POINT",
    "ESCALATE",
    "ESCALATED",
    "ESCALATION_KEYS",
    "ESCALATION_QUESTION",
    "EpisodeSpec",
    "FEATURE_ORDER",
    "FIXTURES_PATH",
    "Fixture",
    "IN_PROTOCOL",
    "M2_KIT",
    "KIND_BASELINE",
    "KIND_COMPILED",
    "KIND_NOOP",
    "KIND_RANDOM",
    "MISSING",
    "OFF_PROTOCOL",
    "OUTCOME_KEYS",
    "ORIGIN_COMMITTED",
    "ORIGIN_MODEL",
    "PAYLOAD_KEYS",
    "POLICY_ARMS",
    "POLICY_ARM_ORDER",
    "POLICY_CONTRACT",
    "POLICY_DRIVER",
    "POLICY_OUTCOMES",
    "PolicyArm",
    "PolicyAttemptRecord",
    "PolicyBudget",
    "PolicyConfig",
    "PolicyDecision",
    "PolicyLedger",
    "PolicyPlay",
    "PolicySource",
    "RAISED",
    "RUNG_DEFAULT",
    "Situation",
    "UNOBSERVED",
    "VERDICTS",
    "VERDICT_CORRECT",
    "VERDICT_NO_RESULT",
    "VERDICT_NO_SOURCE",
    "VERDICT_NO_WORKSPACE",
    "VERDICT_WRONG",
    "assert_senses_identical",
    "authoring_messages",
    "author_policy",
    "build_parser",
    "build_program",
    "classify",
    "demo_episode",
    "escalation_report",
    "extract_source",
    "grade",
    "is_decidable",
    "load_fixtures",
    "load_policy_config",
    "main",
    "mint_nonce",
    "nonce_of",
    "plan_escalations",
    "read_result_lines",
    "render_escalation_prompt",
    "render_plan",
    "run_attempt",
    "run_in_jail",
    "scripted_author",
    "sentinel_path",
    "truth_action",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The committed arm + sampling table. An INPUT, like ``arch-hive-sampling.json``
#: and ``arch-arms-sampling.json``, and deliberately a THIRD file: each loader
#: validates over its own arm order, so P/PR/PH/PN cells in the hive table would
#: be read by nothing and would put two tasks' hands on one file.
DEFAULT_CONFIG_PATH = REPO_ROOT / "docs" / "live-test-results" / "arch-policy-sampling.json"

#: Reused rather than restated: a missing cell raises the same error class every
#: harness in this family raises, so one vocabulary reaches the operator.
ConfigError = aa.ConfigError

ROLE_CORTEX = aa.ROLE_CORTEX
ROLE_WORKER = aa.ROLE_WORKER
ROLE_SENSES = aa.ROLE_SENSES
ROLES = aa.ROLES

#: The rung label this harness stamps on a record when a caller names none.
RUNG_DEFAULT = "P1"

#: Reused from ``challenge_coding`` rather than re-derived. The extractor is
#: paraphrase-tolerant and was hardened against a live capture; the nonce
#: helpers are the forgery defence. Re-implementing either would be a second
#: grader to keep honest, which is precisely what corrections.md §2 warns about.
extract_source = cc.extract_code
mint_nonce = cc.mint_nonce
nonce_of = cc.nonce_of
read_result_lines = cc.read_result_lines

#: Verdicts, sharing ``challenge_coding``'s literals wherever the fact is the
#: same one. A run that produced no policy and a run whose policy was wrong are
#: different facts, and folding them together is how ``exit=stopped`` hid two
#: failures under one code for a whole series.
VERDICT_CORRECT = cc.VERDICT_CORRECT
VERDICT_WRONG = cc.VERDICT_WRONG
#: Nothing that looked like source could be extracted from the model's reply.
VERDICT_NO_SOURCE = cc.VERDICT_NO_CODE
#: The policy ran (or should have) but no readable, single, complete result came.
VERDICT_NO_RESULT = cc.VERDICT_NO_RESULT
#: No container could be provisioned, so nothing ran. **Never** a fallback.
VERDICT_NO_WORKSPACE = cc.VERDICT_NO_WORKSPACE

VERDICTS: tuple[str, ...] = (
    VERDICT_CORRECT,
    VERDICT_WRONG,
    VERDICT_NO_SOURCE,
    VERDICT_NO_RESULT,
    VERDICT_NO_WORKSPACE,
)


# ── the decision the policy makes ────────────────────────────────────────────

#: The function every policy — compiled or committed — must define.
ENTRY_POINT = "decide"

#: The complete, enumerated action space. Small and closed for the same reason
#: arm B's answer spaces are: an out-of-space return is a recorded refusal
#: rather than a value that quietly propagates.
ACTIONS: tuple[str, ...] = ("hold", "advance", "fall_back", "resupply")

#: **The "I don't know" return.** First-class, declared once, reachable from the
#: payload (``situation['escalate']``) so a policy never has to spell a literal,
#: and never a member of :data:`ACTIONS`.
ESCALATE = "?"

#: The value a feature carries when it was **not measured**. No amount of
#: reasoning recovers it, which is what makes escalating the correct move on a
#: situation whose decision turns on one.
UNOBSERVED = -1

#: The features a situation carries, in the order the decision consults them.
FEATURE_ORDER: tuple[str, ...] = ("supply", "hp", "threat", "dist")

#: Exactly what crosses into the container, per situation. A test asserts this
#: is the whole of it — no truth label, no decidability flag, no hidden value.
PAYLOAD_KEYS: tuple[str, ...] = (
    "id",
    "features",
    "actions",
    "escalate",
    "unobserved",
    "seed",
)

#: What each feature means, told to the authoring mind so it can reason about a
#: sensible rule. The harness's own rule (:func:`truth_action`) is NOT told: an
#: arm that handed the model the answer key would measure transcription.
DOMAIN = (
    "Each situation is one unit on a map.\n"
    "  supply  1 if the unit still has supplies, 0 if it has run out\n"
    "  hp      the unit's health, from 0 to 100\n"
    "  threat  how many hostile units can reach it this turn, from 0 to 3\n"
    "  dist    how far it is from its objective; 0 means it is standing on it\n"
    f"Actions, in full: {', '.join(ACTIONS)}"
)


def truth_action(features: Mapping[str, int]) -> str:
    """The graded action for a **complete** feature set. Host-side only.

    Never rendered into a program, never sent to a mind, and never written into
    a payload. It is the harness's rule and the thing a policy is trying to be.

    The rule is deliberately the *obvious* reading of :data:`DOMAIN` rather than
    an arbitrary one: a mind asked to play situation-by-situation would reason
    its way to the same clauses, so "played well, wrote a policy that plays
    badly" stays a statement about articulation rather than about guessing a
    secret. That is a limitation of the demo episode and is named as one — the
    measured series grades against the league lane, whose grader is not ours
    (frame claim ``c22``).
    """
    if features["supply"] == 0:
        return "resupply"
    if features["hp"] < 30:
        return "fall_back"
    if features["threat"] >= 2:
        return "hold"
    return "advance" if features["dist"] > 0 else "hold"


def is_decidable(features: Mapping[str, int]) -> bool:
    """Whether :func:`truth_action` is determined by what the policy was shown.

    The short-circuit structure of the rule, read against
    :data:`UNOBSERVED`: a clause whose feature was not measured blocks, and a
    clause that fires settles it whatever comes later. **This is the grader for
    the escalate return** — the reason a router can be graded here at all.
    """
    if features["supply"] == UNOBSERVED:
        return False
    if features["supply"] == 0:
        return True
    if features["hp"] == UNOBSERVED:
        return False
    if features["hp"] < 30:
        return True
    if features["threat"] == UNOBSERVED:
        return False
    if features["threat"] >= 2:
        return True
    return features["dist"] != UNOBSERVED


def _decisive(features: Mapping[str, int]) -> str:
    """Which feature :func:`truth_action` turns on for a complete feature set."""
    if features["supply"] == 0:
        return "supply"
    if features["hp"] < 30:
        return "hp"
    if features["threat"] >= 2:
        return "threat"
    return "dist"


@dataclass(frozen=True)
class Situation:
    """One decision. Harness-authored, and two-sided on purpose.

    :attr:`features` is what the policy is shown, with :data:`UNOBSERVED` where
    something was not measured. :attr:`hidden` is the complete truth and stays
    on the host: it is what :func:`truth_action` grades against, and it is the
    reason an occluded situation is *genuinely* occluded rather than merely
    labelled as one.
    """

    id: str
    features: Mapping[str, int]
    hidden: Mapping[str, int]
    actions: tuple[str, ...] = ACTIONS

    @property
    def truth(self) -> str:
        return truth_action(self.hidden)

    @property
    def decidable(self) -> bool:
        return is_decidable(self.features)

    def facts(self) -> tuple[str, ...]:
        """Harness-authored fact lines for a checkpoint prompt. No truth in them."""
        lines: list[str] = []
        for name in FEATURE_ORDER:
            value = self.features[name]
            lines.append(f"{name}: " + ("not observed" if value == UNOBSERVED else str(value)))
        return tuple(lines)

    def to_payload(self, *, escalate: str, unobserved: int, seed: int) -> dict[str, Any]:
        """**Everything** that crosses into the container about this situation."""
        return {
            "id": self.id,
            "features": {name: self.features[name] for name in FEATURE_ORDER},
            "actions": list(self.actions),
            "escalate": escalate,
            "unobserved": unobserved,
            "seed": seed,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "features": {name: self.features[name] for name in FEATURE_ORDER},
            "actions": list(self.actions),
            "decidable": self.decidable,
        }


# ── the episode ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EpisodeSpec:
    """The demo episode's shape, read from the committed table."""

    situations: int
    seed: int
    occlusion_every: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "situations": self.situations,
            "seed": self.seed,
            "occlusion_every": self.occlusion_every,
        }


def demo_episode(spec: EpisodeSpec) -> tuple[Situation, ...]:
    """A deterministic, harness-authored episode. No randomness anywhere.

    Every arm sees byte-identical situations, so a difference in outcome cannot
    be the episode. Shaped like the league lane's unit decisions this arm is
    aimed at — a unit, a handful of observations, a short menu — without
    importing the league, which tasks ``t9``/``t10`` wire.

    Nothing here is a graded environment: a run that scored well against this
    would be mistaken for data. It exists to exercise the *paths*.

    Every ``occlusion_every``-th situation hides the feature its own decision
    turns on, so the correct return there is :data:`ESCALATE` and the harness
    knows it.
    """
    every = max(int(spec.occlusion_every), 1)
    situations: list[Situation] = []
    for index in range(max(int(spec.situations), 0)):
        hidden = {
            "supply": 0 if index % 5 == 0 else 1,
            "hp": 10 + (index * 17) % 90,
            "threat": (index * 5) % 4,
            "dist": (index * 13) % 80,
        }
        shown = dict(hidden)
        if (index + 1) % every == 0:
            shown[_decisive(hidden)] = UNOBSERVED
        situations.append(Situation(id=f"u{index + 1}", features=shown, hidden=hidden))
    return tuple(situations)


# ── the arms, as data ────────────────────────────────────────────────────────

#: Where a policy's source came from. The whole of what varies across the arms.
ORIGIN_MODEL = "model"
ORIGIN_COMMITTED = "committed"
ORIGINS: tuple[str, ...] = (ORIGIN_MODEL, ORIGIN_COMMITTED)

KIND_COMPILED = "compiled"
KIND_RANDOM = "random"
KIND_BASELINE = "baseline"
KIND_NOOP = "noop"

#: The three mandatory controls. Named as a tuple so "all three exist" is a
#: check a test performs rather than a sentence a reader trusts.
CONTROL_KINDS: tuple[str, ...] = (KIND_RANDOM, KIND_BASELINE, KIND_NOOP)


@dataclass(frozen=True)
class PolicySource:
    """Where one arm's policy comes from, and what it is.

    ``text`` is empty for :data:`ORIGIN_MODEL` — the mind writes it, once, at
    play time. For a control it is the committed source below: reviewed, in the
    repository, and executed **only** in the jail like any other.
    """

    kind: str
    origin: str
    text: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "origin": self.origin,
            "chars": len(self.text),
            "why": self.why,
        }


#: The random control. A strategy-shaped program with no strategy: it returns a
#: legal move every time, seeded off the situation id so a re-run reproduces.
#: It never escalates, which is the point — it cannot know that it does not know.
_RANDOM_SOURCE = f'''
import random


def {ENTRY_POINT}(situation):
    """Uniform over the declared action space. Legal, reproducible, inert."""
    rng = random.Random("{{0}}|{{1}}".format(situation["id"], situation["seed"]))
    return rng.choice(list(situation["actions"]))
'''

#: The hand-written baseline. The ceiling a compiled policy is measured against,
#: and the demonstration that the escalate return is usable: it escalates on
#: exactly the situations whose decision turns on something it was not shown.
_BASELINE_SOURCE = f'''
def {ENTRY_POINT}(situation):
    """The rule, written out, escalating where it cannot see."""
    f = situation["features"]
    unobserved = situation["unobserved"]
    if f["supply"] == unobserved:
        return situation["escalate"]
    if f["supply"] == 0:
        return "resupply"
    if f["hp"] == unobserved:
        return situation["escalate"]
    if f["hp"] < 30:
        return "fall_back"
    if f["threat"] == unobserved:
        return situation["escalate"]
    if f["threat"] >= 2:
        return "hold"
    if f["dist"] == unobserved:
        return situation["escalate"]
    return "advance" if f["dist"] > 0 else "hold"
'''

#: The no-op. It returns nothing at all, so whatever it scores is the
#: instrument scoring itself. Deliberately NOT an escalate-everything policy:
#: that would measure the checkpoint mind, which is a different control.
_NOOP_SOURCE = f'''
def {ENTRY_POINT}(situation):
    """Nothing. The floor on every axis, including protocol compliance."""
    return None
'''

COMMITTED_SOURCES: dict[str, PolicySource] = {
    KIND_RANDOM: PolicySource(
        kind=KIND_RANDOM,
        origin=ORIGIN_COMMITTED,
        text=_RANDOM_SOURCE,
        why=(
            "without it, 'the mind can compile a strategy' and 'any program beats "
            "per-turn control' are indistinguishable, and those are different findings"
        ),
    ),
    KIND_BASELINE: PolicySource(
        kind=KIND_BASELINE,
        origin=ORIGIN_COMMITTED,
        text=_BASELINE_SOURCE,
        why=(
            "the ceiling a compiled policy is measured against; also the working "
            "demonstration that the escalate return is usable rather than decorative"
        ),
    ),
    KIND_NOOP: PolicySource(
        kind=KIND_NOOP,
        origin=ORIGIN_COMMITTED,
        text=_NOOP_SOURCE,
        why="without it you cannot tell a policy from the harness",
    ),
}

#: The compiled arm's source: written by the cortex, once, at play time.
_COMPILED_SOURCE = PolicySource(
    kind=KIND_COMPILED,
    origin=ORIGIN_MODEL,
    text="",
    why=(
        "the arm under test. One model call for the whole episode; every decision "
        "after it is a function application, not a turn"
    ),
)

ARM_COMPILED = "P"
ARM_RANDOM = "PR"
ARM_BASELINE = "PH"
ARM_NOOP = "PN"


@dataclass(frozen=True)
class PolicyArm:
    """One arm, as data. Nothing in this module branches on an arm id.

    The four arms differ in **exactly one** field, :attr:`source`. Everything
    else — the roles they configure, how many authoring calls they may make,
    which grader runs — is *derived* from it, so a control cannot drift into
    being a different experiment by an edit to a second field.
    """

    id: str
    label: str
    source: PolicySource
    why: str

    @property
    def origin(self) -> str:
        return self.source.origin

    @property
    def is_control(self) -> bool:
        return self.source.origin == ORIGIN_COMMITTED

    @property
    def authoring_calls(self) -> int:
        """Model calls spent writing the policy. One, or none — never two.

        A retry would make arm P's headline number 2 and would quietly be a
        different arm, so the committed table is checked against this at load.
        """
        return 0 if self.is_control else 1

    @property
    def configured_roles(self) -> tuple[str, ...]:
        """The roles this arm may dial. A control declares no cortex cell."""
        if self.is_control:
            return (ROLE_WORKER, ROLE_SENSES)
        return (ROLE_CORTEX, ROLE_WORKER, ROLE_SENSES)

    @property
    def grader(self) -> Callable[[Mapping[str, str], Sequence[Situation]], dict[str, Any]]:
        """One grader for every arm — the identity a control depends on."""
        return grade

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source.to_dict(),
            "origin": self.origin,
            "is_control": self.is_control,
            "authoring_calls": self.authoring_calls,
            "configured_roles": list(self.configured_roles),
            "why": self.why,
        }


POLICY_ARMS: dict[str, PolicyArm] = {
    ARM_COMPILED: PolicyArm(
        id=ARM_COMPILED,
        label="policy-compiled",
        source=_COMPILED_SOURCE,
        why=(
            "the arm under test: the cortex writes a unit-control policy once and "
            "the policy plays the episode at a function application per decision"
        ),
    ),
    ARM_RANDOM: PolicyArm(
        id=ARM_RANDOM,
        label="policy-random",
        source=COMMITTED_SOURCES[KIND_RANDOM],
        why="control — syntactically valid, strategically inert, and it never escalates",
    ),
    ARM_BASELINE: PolicyArm(
        id=ARM_BASELINE,
        label="policy-baseline",
        source=COMMITTED_SOURCES[KIND_BASELINE],
        why="control — the deliberate hand-written ceiling, escalating where it cannot see",
    ),
    ARM_NOOP: PolicyArm(
        id=ARM_NOOP,
        label="policy-noop",
        source=COMMITTED_SOURCES[KIND_NOOP],
        why="control — returns nothing, so anything it scores is the harness scoring itself",
    ),
}

#: Presentation order, and the order every loader validates in.
POLICY_ARM_ORDER: tuple[str, ...] = (ARM_COMPILED, ARM_RANDOM, ARM_BASELINE, ARM_NOOP)


# ── the contract the policy is written against ───────────────────────────────

POLICY_CONTRACT = (
    "Write one Python function and nothing else:\n\n"
    f"    def {ENTRY_POINT}(situation):\n"
    "        ...\n\n"
    "It is called once per situation, inside a bounded container with no "
    "network. `situation` is a plain dict with exactly these keys:\n\n"
    "    id          a string naming the unit\n"
    "    features    a dict: supply, hp, threat, dist (all integers)\n"
    "    actions     the list of actions you may return, in full\n"
    f'    escalate    the literal {ESCALATE!r} - return it to say "I do not know"\n'
    f"    unobserved  the literal {UNOBSERVED!r} - a feature equal to this was NOT measured\n"
    "    seed        an integer, if you want anything reproducible\n\n"
    "Return exactly one of:\n\n"
    "  * a member of situation['actions'], or\n"
    "  * situation['escalate'], which hands that one decision to a model call.\n\n"
    "Anything else is off protocol and is recorded as such.\n\n"
    "The escalate return is part of the contract, not a failure. A feature "
    "equal to `unobserved` was not measured and no reasoning recovers it: if "
    "your rule turns on a feature you cannot see, escalating is the correct "
    "move and guessing is not. Escalating where you could have decided is "
    "measured too, and so is deciding where you could not have known."
)

AUTHORING_SYSTEM = (
    "You are writing a control policy as code. You will not see the situations "
    "and you will not be asked again: the function you write is called once per "
    "situation and what it returns are the decisions. Reply with one Python "
    "code block."
)


def authoring_messages(episode: Sequence[Situation]) -> list[dict[str, Any]]:
    """The one authoring turn. Carries the contract and the domain, no answers.

    The episode's *size* is named because a policy author may reasonably want
    it; no situation, no feature value and no truth crosses into this prompt.
    """
    body = (
        f"{DOMAIN}\n\n"
        f"{POLICY_CONTRACT}\n\n"
        f"Your function will be run against {len(episode)} situations you will "
        "not see first."
    )
    return [
        {"role": "system", "content": AUTHORING_SYSTEM},
        {"role": "user", "content": body},
    ]


def author_policy(
    mind: Callable[[list[dict[str, Any]]], ModelResponse],
    episode: Sequence[Situation],
) -> tuple[Optional[str], str]:
    """**Exactly one** completion, and there is no second one to find.

    No loop, no retry, no tool schema on the wire: the whole of arm P's model
    cost for an N-decision episode is this function. A test reads that off this
    function's AST rather than off this sentence.

    Returns ``(source or None, the raw reply)``. The raw reply is recorded
    unconditionally — a run that produced no policy is exactly the one worth
    re-reading later.
    """
    reply = mind(authoring_messages(episode))
    text = reply.content or ""
    return extract_source(text, entry=ENTRY_POINT), text


def scripted_author(
    source: str,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A hermetic stand-in for the authoring mind. **Never data.**

    It replies with a source someone already wrote, so the compiled arm's
    *paths* can be exercised with no model. A run driven by this is not
    evidence about whether a mind can compile a policy, and is labelled
    ``scripted`` wherever it is reported.
    """

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        return ModelResponse(
            content=f"```python\n{source.strip()}\n```",
            prompt_tokens=len(POLICY_CONTRACT) // 4,
            completion_tokens=len(source) // 4,
        )

    return complete


# ── the program: the policy's source, then a driver that knows no answers ────

#: The driver, appended to the policy source. Substituted with ``replace``
#: rather than ``format`` because it is full of braces.
#:
#: Four properties are load-bearing and each is asserted by test:
#:
#: * it carries the situations the policy is meant to reason from and **no truth
#:   label and no hidden feature value** — see the module docstring for the
#:   precise, weaker claim this makes compared with ``challenge_coding``'s;
#: * it writes through ``sys.__stdout__``, so a policy that rebinds ``print``
#:   cannot hide the result;
#: * it reports ``type(value).__name__`` and lets the host compare **JSON**, so
#:   an object whose ``__eq__`` is always ``True`` cannot pass by comparison;
#: * it times each call, because "~0 ms per decision" is the arm's efficiency
#:   claim and a claim nobody measured is a slogan.
POLICY_DRIVER = """
import json as _j, sys as _s, time as _tm
_N = "__NONCE__"
_E = "__ENTRY__"
_S = _j.loads(__SITUATIONS__)
_fn = globals().get(_E)
if not callable(_fn):
    _out = {"entry": _E, "decisions": None, "error": "no callable named " + _E}
else:
    _rows = []
    for _i, _sit in enumerate(_S):
        _row = {"i": _i, "id": _sit["id"]}
        _t0 = _tm.perf_counter()
        try:
            _v = _fn(_sit)
        except BaseException as _x:
            _row["seconds"] = _tm.perf_counter() - _t0
            _row["error"] = type(_x).__name__
            _rows.append(_row)
            continue
        _row["seconds"] = _tm.perf_counter() - _t0
        _row["type"] = type(_v).__name__
        try:
            _j.dumps(_v)
        except BaseException:
            _row["unserialisable"] = True
        else:
            _row["value"] = _v
        _rows.append(_row)
    _out = {"entry": _E, "decisions": _rows}
_s.__stdout__.write(_N + " " + _j.dumps(_out, sort_keys=True) + "\\n")
_s.__stdout__.flush()
"""


def build_program(
    source: str,
    episode: Sequence[Situation],
    nonce: str,
    *,
    seed: int,
) -> str:
    """The policy source followed by the driver. The only thing ever executed."""
    payload = [
        situation.to_payload(escalate=ESCALATE, unobserved=UNOBSERVED, seed=seed)
        for situation in episode
    ]
    driver = (
        POLICY_DRIVER.replace("__NONCE__", nonce)
        .replace("__ENTRY__", ENTRY_POINT)
        .replace("__SITUATIONS__", repr(json.dumps(payload)))
    )
    return source.rstrip("\n") + "\n" + driver


def run_in_jail(program: str, workspace: MuseWorkspace) -> str:
    """**The only execution path in this harness.**

    One argv, handed to the workspace tool: no policy, no mount, no
    environment, no host path. Everything else about the container's reach is
    :mod:`embodiment.workspace`'s property and is proved there.
    """
    return str(workspace.execute(WORKSPACE_TOOL_NAME, {"command": ["python3", "-c", program]}))


# ── what the policy returned, classified ─────────────────────────────────────

#: The policy returned a member of the situation's declared action space.
DECIDED = "decided"
#: The policy returned the escalate sentinel. **In protocol** — this is the
#: contract working, not failing.
ESCALATED = "escalated"
#: The policy returned something that is neither. The compiled artifact's own
#: interface failure, and the axis that tells it apart from a losing strategy.
OFF_PROTOCOL = "off-protocol"
#: The policy raised on this situation.
RAISED = "raised"
#: No row came back for this situation at all.
MISSING = "missing"

POLICY_OUTCOMES: tuple[str, ...] = (DECIDED, ESCALATED, OFF_PROTOCOL, RAISED, MISSING)

#: The two returns the contract permits. Everything else is a protocol failure,
#: and a policy that declares its own incompleteness is inside the contract.
IN_PROTOCOL: tuple[str, ...] = (DECIDED, ESCALATED)


@dataclass(frozen=True)
class PolicyDecision:
    """One situation's outcome from the jail. **No correctness claim here.**

    Whether the action is *right* is graded later, on the host, from the merged
    decision map — and never from this record.
    """

    situation: str
    outcome: str
    action: str
    returned: str
    type_name: str
    seconds: float
    detail: str

    @property
    def in_protocol(self) -> bool:
        return self.outcome in IN_PROTOCOL

    def to_dict(self) -> dict[str, Any]:
        return {
            "situation": self.situation,
            "outcome": self.outcome,
            "action": self.action,
            "returned": self.returned,
            "type": self.type_name,
            "seconds": round(self.seconds, 6),
            "detail": self.detail,
        }


def classify(
    payload: Optional[Mapping[str, Any]],
    episode: Sequence[Situation],
) -> tuple[PolicyDecision, ...]:
    """Read the driver's rows against each situation's own declared space.

    **Never raises.** A row this cannot read is data about the arm. The rule is
    total and stated once: a return counts as a decision only if it is a member
    of that situation's enumerated actions; the sentinel is an escalation;
    anything else is off protocol, never a nearest match and never a default.
    """
    raw = (payload or {}).get("decisions")
    rows = raw if isinstance(raw, list) else []
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("id"), str):
            by_id.setdefault(row["id"], row)

    out: list[PolicyDecision] = []
    for situation in episode:
        row = by_id.get(situation.id) or {}
        seconds = float(row.get("seconds") or 0.0)
        if not row:
            out.append(_decision(situation, MISSING, "", "", "", seconds, "no row came back"))
            continue
        if row.get("error"):
            detail = f"the policy raised {row['error']}"
            out.append(_decision(situation, RAISED, "", "", "", seconds, detail))
            continue
        if row.get("unserialisable"):
            detail = "the policy returned something that will not serialise"
            out.append(_decision(situation, OFF_PROTOCOL, "", "", "", seconds, detail))
            continue
        if "value" not in row:
            out.append(
                _decision(situation, MISSING, "", "", "", seconds, "the row carried no value")
            )
            continue
        value = row["value"]
        returned = repr(value)
        type_name = str(row.get("type") or type(value).__name__)
        if value == ESCALATE:
            out.append(_decision(situation, ESCALATED, "", returned, type_name, seconds, ""))
        elif isinstance(value, str) and value in situation.actions:
            out.append(_decision(situation, DECIDED, value, returned, type_name, seconds, ""))
        else:
            detail = f"{returned} is not an allowed action and is not the escalate return"
            out.append(_decision(situation, OFF_PROTOCOL, "", returned, type_name, seconds, detail))
    return tuple(out)


def _decision(
    situation: Situation,
    outcome: str,
    action: str,
    returned: str,
    type_name: str,
    seconds: float,
    detail: str,
) -> PolicyDecision:
    return PolicyDecision(
        situation=situation.id,
        outcome=outcome,
        action=action,
        returned=returned,
        type_name=type_name,
        seconds=seconds,
        detail=detail,
    )


class PolicyLedger:
    """The compiled artifact's own protocol compliance — an acceptance axis.

    Kept in its own ledger with its own vocabulary, for the reason
    :class:`~examples.arch_hive.AcceptanceLedger` is: a policy that returns
    garbage and one that returns wrong-but-legal moves fail for different
    reasons, and a harness that folded the two into outcome would report a
    broken artifact as a losing one.
    """

    def __init__(self, decisions: Sequence[PolicyDecision] = ()) -> None:
        self.decisions: tuple[PolicyDecision, ...] = tuple(decisions)

    def counts(self) -> dict[str, int]:
        tally = {name: 0 for name in POLICY_OUTCOMES}
        for decision in self.decisions:
            tally[decision.outcome] = tally.get(decision.outcome, 0) + 1
        return tally

    def rate(self) -> Optional[float]:
        """In-protocol / total, or ``None`` when nothing came back at all."""
        if not self.decisions:
            return None
        kept = sum(1 for decision in self.decisions if decision.in_protocol)
        return kept / len(self.decisions)

    def seconds(self) -> float:
        return sum(decision.seconds for decision in self.decisions)

    def slowest(self) -> float:
        times = [decision.seconds for decision in self.decisions]
        return max(times) if times else 0.0

    def to_dict(self) -> dict[str, Any]:
        rate = self.rate()
        return {
            "situations": len(self.decisions),
            "counts": self.counts(),
            "acceptance_rate": None if rate is None else round(rate, 4),
            "decide_seconds_total": round(self.seconds(), 6),
            "decide_seconds_slowest": round(self.slowest(), 6),
            "returns": [decision.to_dict() for decision in self.decisions],
        }


# ── the declared checkpoint: escalation as a worker-harness call ─────────────

#: The checkpoint question's name. One question, authored here, never by a mind.
ESCALATION_QUESTION = "unit_action"

#: What a checkpoint call asks. Harness-authored and identical on every call.
ESCALATION_ASK = (
    "The control policy could not decide this unit's action. Which listed " "action should it take?"
)


def render_escalation_prompt(situations: Sequence[Situation]) -> str:
    """One checkpoint call's user turn, from harness-authored parts only.

    Nothing the policy wrote reaches this string. The policy returned a
    sentinel; the words come from :data:`ESCALATION_ASK` and from each
    situation's own observations. The layout is
    ``arch_hive.render_prompt``'s, deliberately, so both harnesses' calls are
    parsed by one reader — :func:`examples.arch_hive.parse_answers`.
    """
    lines: list[str] = [ESCALATION_ASK, ""]
    for situation in situations:
        lines.append(f"item {situation.id}:")
        # Bound to a name first: the structural termination test asks that every
        # iteration walk a SETTLED sequence, and a name is settled where a fresh
        # call is only settled if you already know what it returns.
        facts = situation.facts()
        for fact in facts:
            lines.append(f"  {fact}")
        lines.append(f"  allowed answers: {', '.join(situation.actions)}")
        lines.append("")
    lines.append(ah.ANSWER_PROTOCOL)
    return "\n".join(lines)


def plan_escalations(
    situations: Sequence[Situation],
    *,
    grain: ah.ScopeGrain,
    budget: int,
    stem: str,
) -> tuple[tuple[ah.ScopedCall, ...], tuple[str, ...]]:
    """Chunk escalated *situations* at the declared grain, clamped by *budget*.

    Returns ``(calls, refused_situation_ids)``. The clamp happens here, before
    any call object exists and long before any thread does: the number of
    checkpoint calls an episode can make is knowable from the plan alone, which
    is the step-count bound claim ``h15`` asks for.

    A situation beyond the budget is *named back*, never silently dropped — a
    policy that escalated more than the episode could afford must be visible as
    having done so, because that is the collapse the escalation axis exists to
    catch.
    """
    per_call = max(int(grain.items_per_call), 1)
    affordable = max(int(budget), 0)
    chunks: list[tuple[Situation, ...]] = []
    for start in range(0, len(situations), per_call):
        chunks.append(tuple(situations[start : start + per_call]))
    kept = chunks[:affordable]
    dropped = chunks[affordable:]

    calls: list[ah.ScopedCall] = []
    for index, chunk in enumerate(kept, start=1):
        calls.append(
            ah.ScopedCall(
                id=f"{stem}-{index}",
                question=ESCALATION_QUESTION,
                item_ids=tuple(situation.id for situation in chunk),
                prompt=render_escalation_prompt(chunk),
                spaces=tuple(situation.actions for situation in chunk),
            )
        )
    refused = tuple(situation.id for chunk in dropped for situation in chunk)
    return tuple(calls), refused


# ── the escalation report: an axis of its own, at both extremes ──────────────


def escalation_report(
    decisions: Sequence[PolicyDecision],
    episode: Sequence[Situation],
    *,
    calls: int,
    not_dispatched: Sequence[str],
    collapses_at: float,
) -> dict[str, Any]:
    """How often the policy asked, and whether it asked when it should have.

    Both extremes are informative and both are reported rather than judged
    here: a policy that escalates everything has collapsed into the worker arm
    with extra latency, and one that escalates nothing may simply be unable to
    detect its own ignorance. The four cells below are the whole of that, and
    they are gradeable because :func:`is_decidable` knows, host-side, which
    situations could have been decided from what the policy was shown.

    Nothing in this mapping is a correctness figure.
    """
    by_id = {situation.id: situation for situation in episode}
    asked = [decision for decision in decisions if decision.outcome == ESCALATED]
    quiet = [decision for decision in decisions if decision.outcome != ESCALATED]
    asked_undecidable = sum(
        1
        for decision in asked
        if decision.situation in by_id and not by_id[decision.situation].decidable
    )
    asked_decidable = len(asked) - asked_undecidable
    quiet_undecidable = sum(
        1
        for decision in quiet
        if decision.situation in by_id and not by_id[decision.situation].decidable
    )
    quiet_decidable = len(quiet) - quiet_undecidable
    undecidable = sum(1 for situation in episode if not situation.decidable)
    total = len(decisions)
    rate = (len(asked) / total) if total else None
    return {
        "situations": total,
        "escalated": len(asked),
        "escalation_rate": None if rate is None else round(rate, 4),
        "collapsed": bool(rate is not None and rate >= collapses_at),
        "undecidable": undecidable,
        "escalated_undecidable": asked_undecidable,
        "escalated_decidable": asked_decidable,
        "unescalated_undecidable": quiet_undecidable,
        "unescalated_decidable": quiet_decidable,
        "escalation_precision": (round(asked_undecidable / len(asked), 4) if asked else None),
        "escalation_recall": (round(asked_undecidable / undecidable, 4) if undecidable else None),
        "checkpoint_calls": calls,
        "not_dispatched": list(not_dispatched),
    }


# ── grading: outcome, and outcome only ───────────────────────────────────────


def grade(decisions: Mapping[str, str], episode: Sequence[Situation]) -> dict[str, Any]:
    """Outcome, and outcome only. No escalation or acceptance figure is here.

    It takes the **merged** decision map — the policy's own returns plus
    whatever a checkpoint call resolved — because outcome is outcome. How a
    decision was reached is the escalation axis's business, and keeping the two
    apart is what lets a fully-escalating arm and a fully-compiled one be
    compared on the same number.
    """
    correct = sum(1 for situation in episode if decisions.get(situation.id, "") == situation.truth)
    decided = sum(1 for situation in episode if decisions.get(situation.id, ""))
    return {
        "attempted": len(episode),
        "decided": decided,
        "correct": correct,
        "wrong": decided - correct,
        "undecided": len(episode) - decided,
        "is_correct": bool(episode) and correct == len(episode),
    }


# ── the committed configuration ──────────────────────────────────────────────


def _required(raw: Mapping[str, Any], key: str, where: str) -> Any:
    """Read a required key. There is no default — that is the whole point."""
    if not isinstance(raw, Mapping) or key not in raw:
        raise ConfigError(
            f"{where}: missing required key {key!r}. The policy table is the only "
            "source for it; this harness carries no default to fall back on."
        )
    return raw[key]


@dataclass(frozen=True)
class PolicyBudget:
    """One arm's budgets. ``authoring_calls`` is checked against the arm itself."""

    authoring_calls: int
    max_escalation_calls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "authoring_calls": self.authoring_calls,
            "max_escalation_calls": self.max_escalation_calls,
        }


@dataclass(frozen=True)
class PolicyPlay:
    """One arm's play table: the checkpoint grain and who answers a checkpoint."""

    arm: str
    grain: str
    escalation_role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "grain": self.grain,
            "escalation_role": self.escalation_role,
        }


@dataclass(frozen=True)
class PolicyConfig:
    """The whole committed configuration, parsed and validated once.

    Structurally compatible with ``arch_arms``' ``ArchConfig`` where the seam
    builders touch it (:meth:`sampling_for`, :meth:`wire_extra`), so
    ``arch_arms.ScriptedSeams`` builds policy minds unchanged — one metering
    vocabulary across all three harnesses, and no copy of a builder here.
    """

    path: Optional[Path]
    version: int
    roles: Mapping[str, aa.RoleDial]
    thinking_modes: Mapping[str, Mapping[str, Any]]
    sampling: Mapping[str, Mapping[str, aa.Sampling]]
    budgets: Mapping[str, PolicyBudget]
    plays: Mapping[str, PolicyPlay]
    grains: Mapping[str, ah.ScopeGrain]
    episode: EpisodeSpec
    batch_timeout_seconds: float
    max_concurrency: int
    decision: Mapping[str, Any]

    def role(self, name: str) -> aa.RoleDial:
        if name not in self.roles:
            raise ConfigError(f"roles: no entry for {name!r}; known roles are {sorted(self.roles)}")
        return self.roles[name]

    def sampling_for(self, arm: str, role: str) -> aa.Sampling:
        if arm not in self.sampling:
            raise ConfigError(f"sampling: no cell for arm {arm!r}")
        cells = self.sampling[arm]
        if role not in cells:
            raise ConfigError(
                f"sampling.{arm}: no cell for role {role!r}. Every role an arm "
                "configures needs its own temperature, thinking mode and max_tokens."
            )
        return cells[role]

    def budget_for(self, arm: str) -> PolicyBudget:
        if arm not in self.budgets:
            raise ConfigError(f"budgets: no entry for arm {arm!r}")
        return self.budgets[arm]

    def play_for(self, arm: str) -> PolicyPlay:
        if arm not in self.plays:
            raise ConfigError(f"policy: no entry for arm {arm!r}")
        return self.plays[arm]

    def grain(self, name: str) -> ah.ScopeGrain:
        if name not in self.grains:
            raise ConfigError(
                f"grains: no entry for {name!r}; known grains are {sorted(self.grains)}"
            )
        return self.grains[name]

    def wire_extra(self, thinking: str) -> dict[str, Any]:
        if thinking not in self.thinking_modes:
            raise ConfigError(
                f"thinking_wire.modes: no entry for mode {thinking!r}; "
                f"known modes are {sorted(self.thinking_modes)}"
            )
        return dict(self.thinking_modes[thinking])

    def senses_hashes(self) -> dict[str, str]:
        dial = self.role(ROLE_SENSES).to_dict()
        out: dict[str, str] = {}
        for arm in POLICY_ARM_ORDER:
            payload = {"role": dial, "sampling": self.sampling_for(arm, ROLE_SENSES).to_dict()}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            out[arm] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "version": self.version,
            "roles": {name: dial.to_dict() for name, dial in self.roles.items()},
            "thinking_wire": {mode: dict(keys) for mode, keys in self.thinking_modes.items()},
            "sampling": {
                arm: {role: cell.to_dict() for role, cell in cells.items()}
                for arm, cells in self.sampling.items()
            },
            "budgets": {arm: budget.to_dict() for arm, budget in self.budgets.items()},
            "policy": {arm: play.to_dict() for arm, play in self.plays.items()},
            "grains": {name: grain.to_dict() for name, grain in self.grains.items()},
            "episode": self.episode.to_dict(),
            "dispatch": {
                "batch_timeout_seconds": self.batch_timeout_seconds,
                "max_concurrency": self.max_concurrency,
            },
            "decision": dict(self.decision),
        }


def load_policy_config(path: Optional[Path] = None) -> PolicyConfig:
    """Read and validate the committed policy table. Eager, total, no defaults."""
    resolved = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise ConfigError(f"no policy table at {resolved}") from missing
    except ValueError as broken:
        raise ConfigError(f"{resolved} is not readable JSON: {broken}") from broken
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{resolved}: the policy table must be a JSON object")

    roles_raw = _required(raw, "roles", str(resolved))
    roles = {name: aa.RoleDial.from_dict(name, entry) for name, entry in roles_raw.items()}
    for name in ROLES:
        if name not in roles:
            raise ConfigError(f"{resolved}: roles is missing {name!r}")

    wire = _required(raw, "thinking_wire", str(resolved))
    modes_raw = _required(wire, "modes", "thinking_wire")
    modes = {mode: dict(keys) for mode, keys in modes_raw.items()}

    grains_raw = _required(raw, "grains", str(resolved))
    grains: dict[str, ah.ScopeGrain] = {}
    for name, entry in grains_raw.items():
        if not isinstance(entry, Mapping) or "items_per_call" not in entry:
            continue
        grains[name] = ah.ScopeGrain(
            id=name,
            items_per_call=int(entry["items_per_call"]),
            why=str(entry.get("why") or ""),
        )
    if not grains:
        raise ConfigError(f"{resolved}: grains declares no grain with an items_per_call")

    sampling_raw = _required(raw, "sampling", str(resolved))
    budgets_raw = _required(raw, "budgets", str(resolved))
    play_raw = _required(raw, "policy", str(resolved))

    sampling: dict[str, dict[str, aa.Sampling]] = {}
    budgets: dict[str, PolicyBudget] = {}
    plays: dict[str, PolicyPlay] = {}
    for arm_id in POLICY_ARM_ORDER:
        arm = POLICY_ARMS[arm_id]
        if arm_id not in sampling_raw:
            raise ConfigError(f"sampling: no cell for arm {arm_id!r}")
        cells: dict[str, aa.Sampling] = {}
        for role in arm.configured_roles:
            if role not in sampling_raw[arm_id]:
                raise ConfigError(f"sampling.{arm_id}: no cell for role {role!r}")
            cell = aa.Sampling.from_dict(
                sampling_raw[arm_id][role], where=f"sampling.{arm_id}.{role}"
            )
            if cell.thinking not in modes:
                raise ConfigError(
                    f"sampling.{arm_id}.{role}: thinking mode {cell.thinking!r} has no "
                    f"thinking_wire.modes entry; known modes are {sorted(modes)}"
                )
            cells[role] = cell
        sampling[arm_id] = cells

        budget_cell = _required(budgets_raw, arm_id, "budgets")
        granted = int(_required(budget_cell, "authoring_calls", f"budgets.{arm_id}"))
        # The one cross-check the table cannot be trusted to make itself: a
        # control granted an authoring call, or arm P granted a retry, would be
        # a different arm reported under this arm's name.
        if granted != arm.authoring_calls:
            raise ConfigError(
                f"budgets.{arm_id}: authoring_calls is {granted}, but arm {arm_id!r} "
                f"structurally makes {arm.authoring_calls}. A control that authors, "
                "or a compiled arm that retries, is a different arm."
            )
        budgets[arm_id] = PolicyBudget(
            authoring_calls=granted,
            max_escalation_calls=int(
                _required(budget_cell, "max_escalation_calls", f"budgets.{arm_id}")
            ),
        )

        play_cell = _required(play_raw, arm_id, "policy")
        grain_name = str(_required(play_cell, "grain", f"policy.{arm_id}"))
        if grain_name not in grains:
            raise ConfigError(
                f"policy.{arm_id}: grain {grain_name!r} has no grains entry; "
                f"known grains are {sorted(grains)}"
            )
        role_name = str(_required(play_cell, "escalation_role", f"policy.{arm_id}"))
        if role_name not in ROLES:
            raise ConfigError(
                f"policy.{arm_id}: escalation_role {role_name!r} is not a declared "
                f"role; known roles are {list(ROLES)}"
            )
        if role_name not in arm.configured_roles:
            raise ConfigError(
                f"policy.{arm_id}: escalation_role {role_name!r} has no sampling "
                f"cell in this arm; its configured roles are {list(arm.configured_roles)}"
            )
        plays[arm_id] = PolicyPlay(arm=arm_id, grain=grain_name, escalation_role=role_name)

    episode_raw = _required(raw, "episode", str(resolved))
    episode = EpisodeSpec(
        situations=int(_required(episode_raw, "situations", "episode")),
        seed=int(_required(episode_raw, "seed", "episode")),
        occlusion_every=int(_required(episode_raw, "occlusion_every", "episode")),
    )

    dispatch_raw = _required(raw, "dispatch", str(resolved))
    timeout = float(_required(dispatch_raw, "batch_timeout_seconds", "dispatch"))
    if not 0 < timeout < float("inf"):
        raise ConfigError(
            f"dispatch.batch_timeout_seconds must be finite and positive, got {timeout!r}"
        )

    return PolicyConfig(
        path=resolved,
        version=int(raw.get("version") or 0),
        roles=roles,
        thinking_modes=modes,
        sampling=sampling,
        budgets=budgets,
        plays=plays,
        grains=grains,
        episode=episode,
        batch_timeout_seconds=timeout,
        max_concurrency=int(_required(dispatch_raw, "max_concurrency", "dispatch")),
        decision=dict(_required(raw, "decision", str(resolved))),
    )


def assert_senses_identical(config: PolicyConfig) -> str:
    """Refuse a run whose arms disagree about senses. Returns the one hash."""
    hashes = config.senses_hashes()
    distinct = sorted(set(hashes.values()))
    if len(distinct) != 1:
        grouped: dict[str, list[str]] = {}
        for arm, digest in hashes.items():
            grouped.setdefault(digest, []).append(arm)
        detail = "; ".join(
            f"{digest[:12]}… = arms {sorted(arms)}" for digest, arms in sorted(grouped.items())
        )
        raise ConfigError(
            "senses configuration differs between policy arms and would confound "
            f"them all at once: {detail}. Check sampling.<arm>.senses and roles.senses."
        )
    return distinct[0]


# ── the attempt record: three axes, three disjoint key namespaces ────────────

KIND_ATTEMPT = "policy-attempt"

#: What the episode scored. Criterion 3's first block.
OUTCOME_KEYS: tuple[str, ...] = ("graded", "decisions", "is_correct", "verdict")

#: How often the policy asked, and whether it asked when it should have.
#: Deliberately NOT folded into outcome: a fully-escalating arm and a
#: fully-compiled one can score identically and are not the same arm.
ESCALATION_KEYS: tuple[str, ...] = ("escalation", "escalated", "escalation_calls")

#: Whether calls were **accepted** — two ledgers, because the compiled artifact's
#: own protocol compliance and the checkpoint call's are different failures.
#: Issue #33's lesson, kept structural in both places it can now recur.
ACCEPTANCE_KEYS: tuple[str, ...] = ("policy_acceptance", "escalation_acceptance")


@dataclass
class PolicyAttemptRecord:
    """One arm's attempt at one episode, and everything it cost.

    Outcome, escalation and acceptance are three blocks with pairwise disjoint
    keys. A reader can quote a refusal rate without touching a correctness
    number, quote an escalation rate without touching either, and never has to
    wonder which of the three a figure came from.
    """

    arm: str
    label: str
    kind: str
    origin: str
    rung: str
    route: str
    grain: str
    escalation_role: str
    verdict: str = VERDICT_NO_SOURCE
    is_correct: bool = False
    decisions: dict[str, str] = field(default_factory=dict)
    graded: dict[str, Any] = field(default_factory=dict)
    escalation: dict[str, Any] = field(default_factory=dict)
    escalated: int = 0
    escalation_calls: int = 0
    policy_acceptance: dict[str, Any] = field(default_factory=dict)
    escalation_acceptance: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    policy_source: str = ""
    spoof_suspected: bool = False
    vacuity: dict[str, Any] = field(default_factory=dict)
    jail: dict[str, Any] = field(default_factory=dict)
    authoring_cost: dict[str, Any] = field(default_factory=dict)
    escalation_cost: dict[str, Any] = field(default_factory=dict)
    scripted_author: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_ATTEMPT,
            "arm": self.arm,
            "label": self.label,
            "source_kind": self.kind,
            "origin": self.origin,
            "rung": self.rung,
            "route": self.route,
            "grain": self.grain,
            "escalation_role": self.escalation_role,
            "verdict": self.verdict,
            "is_correct": self.is_correct,
            "decisions": dict(self.decisions),
            "graded": dict(self.graded),
            "escalation": dict(self.escalation),
            "escalated": self.escalated,
            "escalation_calls": self.escalation_calls,
            "policy_acceptance": dict(self.policy_acceptance),
            "escalation_acceptance": dict(self.escalation_acceptance),
            "raw_response": self.raw_response,
            "policy_source": self.policy_source,
            "spoof_suspected": self.spoof_suspected,
            "vacuity": dict(self.vacuity),
            "jail": dict(self.jail),
            "authoring_cost": dict(self.authoring_cost),
            "escalation_cost": dict(self.escalation_cost),
            "scripted_author": self.scripted_author,
        }


# ── running one attempt ──────────────────────────────────────────────────────


def run_attempt(
    *,
    arm: PolicyArm,
    config: PolicyConfig,
    seams: Any,
    episode: Sequence[Situation],
    senses_hash: str,
    workspace: MuseWorkspace,
    rung: str = RUNG_DEFAULT,
    route: str = aa.ROUTE_TEXT,
    task_id: str = "policy-1",
) -> PolicyAttemptRecord:
    """Resolve a policy, run it in the jail, serve its checkpoints, grade it.

    Straight-line: one authoring completion at most, one container run at most,
    a budget-clamped batch of checkpoint calls, then a host-side grade. The
    arguments are **named, not** ``**kwargs``, and so is every call below —
    there is no passthrough anywhere in this module for an edit to route
    something through.
    """
    play = config.play_for(arm.id)
    budget = config.budget_for(arm.id)
    grain = config.grain(play.grain)
    log: aa.CallLog = seams.log
    mark = len(log.records)
    ctx = aa.CallContext(
        arm=arm.id,
        rung=rung,
        problem=task_id,
        route=route,
        senses_hash=senses_hash,
        live=bool(getattr(seams, "live", False)),
    )
    record = PolicyAttemptRecord(
        arm=arm.id,
        label=arm.label,
        kind=arm.source.kind,
        origin=arm.origin,
        rung=rung,
        route=route,
        grain=grain.id,
        escalation_role=play.escalation_role,
    )

    # 1. the source. Committed, or one completion — never both, never twice.
    source, raw_reply = _resolve_source(arm, seams=seams, ctx=ctx, episode=episode)
    authoring = list(log.since(mark))
    record.authoring_cost = aa.fold_cost(authoring)
    record.raw_response = raw_reply
    if not source:
        record.verdict = VERDICT_NO_SOURCE
        record.graded = grade({}, episode)
        record.escalation = escalation_report(
            (),
            episode,
            calls=0,
            not_dispatched=(),
            collapses_at=_collapse_threshold(config),
        )
        record.policy_acceptance = PolicyLedger().to_dict()
        record.escalation_acceptance = ah.AcceptanceLedger().to_dict()
        record.escalation_cost = aa.fold_cost([])
        record.jail = {"provider": workspace.provider, "runs": 0, "output": "", "degradations": []}
        record.vacuity = _vacuity(0, 0, None, len(episode))
        return record
    record.policy_source = source

    # 2. the jail. The only place any policy source is ever executed.
    nonce = mint_nonce()
    program = build_program(source, episode, nonce, seed=config.episode.seed)
    runs_before = workspace.counts().runs
    degradations_before = len(workspace.degradations)
    output = run_in_jail(program, workspace)
    runs = workspace.counts().runs - runs_before
    fresh = workspace.degradations[degradations_before:]
    record.jail = {
        "provider": workspace.provider,
        "runs": runs,
        "nonce": nonce,
        "output": output,
        "degradations": [entry.to_dict() for entry in fresh],
    }

    lines = read_result_lines(output, nonce)
    payload = _payload_from(lines, nonce)
    record.spoof_suspected = len(lines) > 1

    # 3. what the policy returned, against each situation's own declared space.
    returns = classify(payload, episode)
    ledger = PolicyLedger(returns)
    record.policy_acceptance = ledger.to_dict()
    rows = (payload or {}).get("decisions")
    rows_returned = len(rows) if isinstance(rows, list) else None
    record.vacuity = _vacuity(runs, len(lines), rows_returned, len(episode))

    # 4. the declared checkpoints, bounded before any call exists.
    escalated = [situation for situation in episode if _asked(returns, situation.id)]
    calls, not_dispatched = plan_escalations(
        escalated,
        grain=grain,
        budget=budget.max_escalation_calls,
        stem=f"{task_id}-{arm.id}",
    )
    mark_escalation = len(log.records)
    results = _serve(calls, config=config, seams=seams, ctx=ctx, role=play.escalation_role)
    acceptance = ah.AcceptanceLedger()
    acceptance.extend(results)
    record.escalation_acceptance = acceptance.to_dict()
    record.escalation_calls = acceptance.dispatched
    record.escalated = len(escalated)
    record.escalation = escalation_report(
        returns,
        episode,
        calls=acceptance.dispatched,
        not_dispatched=not_dispatched,
        collapses_at=_collapse_threshold(config),
    )
    record.escalation_cost = aa.fold_cost(log.since(mark_escalation))

    # 5. outcome, from the merged decision map and nothing else.
    decisions = _merge(returns, results)
    record.decisions = decisions
    record.graded = arm.grader(decisions, episode)
    record.is_correct = bool(record.graded.get("is_correct"))
    record.verdict = _verdict(runs, record.vacuity, record.is_correct)
    return record


def _resolve_source(
    arm: PolicyArm,
    *,
    seams: Any,
    ctx: aa.CallContext,
    episode: Sequence[Situation],
) -> tuple[Optional[str], str]:
    """Read the source off the arm. Nothing here branches on an arm id.

    A committed source costs nothing and reaches no seam. A model source costs
    exactly one completion, made with ``tools=None``: the cortex writes a
    program, it does not act, so no tool schema is ever on this wire.
    """
    if arm.origin == ORIGIN_COMMITTED:
        return arm.source.text, ""
    mind = seams.build(ROLE_CORTEX, ctx, None)
    return author_policy(mind, episode)


def _serve(
    calls: Sequence[ah.ScopedCall],
    *,
    config: PolicyConfig,
    seams: Any,
    ctx: aa.CallContext,
    role: str,
) -> tuple[ah.ScopedResult, ...]:
    """Answer the checkpoint calls. Concurrency is borrowed, not rebuilt.

    :func:`examples.arch_hive.dispatch` makes exactly one bounded wait, reads
    every result with a zero timeout and tears its pool down without waiting —
    all proved over *its* AST in ``tests/test_arch_hive.py``. Reimplementing
    that here would be a second bound to keep honest.

    :func:`examples.arch_hive.answer_by_worker` is reused for the same reason
    and one more: it already reclassifies a refusal that coincides with a
    truncated turn as an absence (issue #37), so a small token budget cannot be
    published as a broken schema.
    """
    return ah.dispatch(
        calls,
        answer_fn=lambda call: ah.answer_by_worker(call, mind=seams.build(role, ctx, None)),
        max_workers=config.max_concurrency,
        timeout=config.batch_timeout_seconds,
    )


def _asked(returns: Sequence[PolicyDecision], situation_id: str) -> bool:
    for decision in returns:
        if decision.situation == situation_id:
            return decision.outcome == ESCALATED
    return False


def _merge(
    returns: Sequence[PolicyDecision],
    results: Sequence[ah.ScopedResult],
) -> dict[str, str]:
    """The decision map: the policy's own actions, plus resolved checkpoints.

    A checkpoint answer that came back refused, absent or out of space
    contributes nothing — it is not a decision, and it is already counted in the
    acceptance ledger. Nothing here writes a default.
    """
    decisions: dict[str, str] = {}
    for decision in returns:
        if decision.outcome == DECIDED:
            decisions[decision.situation] = decision.action
    for result in results:
        for situation_id, answer in zip(result.item_ids, result.answers):
            if answer:
                decisions[situation_id] = answer
    return decisions


def _payload_from(lines: Sequence[str], nonce: str) -> Optional[dict]:
    """The driver's payload, or ``None``. Two nonce lines are refused, not resolved."""
    if len(lines) != 1:
        return None
    try:
        candidate = json.loads(lines[0][len(nonce) + 1 :])
    except (TypeError, ValueError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _vacuity(
    runs: int,
    nonce_matches: int,
    rows_returned: Optional[int],
    expected: int,
) -> dict[str, Any]:
    """The M2 vacuity gate: a verdict is refused unless something really ran."""
    return {
        "jail_runs": runs,
        "nonce_matches": nonce_matches,
        "rows_returned": rows_returned,
        "situations_expected": expected,
        "fired": bool(runs >= 1 and nonce_matches == 1 and rows_returned == expected),
    }


def _verdict(runs: int, vacuity: Mapping[str, Any], is_correct: bool) -> str:
    if runs < 1:
        return VERDICT_NO_WORKSPACE
    if not vacuity.get("fired"):
        return VERDICT_NO_RESULT
    return VERDICT_CORRECT if is_correct else VERDICT_WRONG


# ── the committed fixtures: the M2 kit's evidence ────────────────────────────

#: Committed EVIDENCE, not an input. Every ``stdout`` in it is what a **real
#: docker workspace** printed for that source, with the per-run nonce written
#: back as ``{nonce}``. That is what lets the always-on suite grade the shipped
#: classifier without executing anything: the execution was real and happened
#: once; every judgement the tests make is the shipped one.
FIXTURES_PATH = REPO_ROOT / "docs" / "live-test-results" / "arch-policy-fixtures.json"

#: The four M2 requirements and the mechanism that discharges each, named here
#: so the discipline is greppable from the harness rather than only from a plan.
M2_KIT = {
    "adversarial_fixtures": "FIXTURES entries: raiser, no_entry, off_protocol, chatty, sentinel",
    "paraphrase_case": "the committed control sources, extracted through challenge_coding's reader",
    "vacuity_assertion": "the 'vacuity' record on every attempt; a verdict needs fired=True",
    "committed_raw_responses": "record.raw_response and record.policy_source, always populated",
}


@dataclass(frozen=True)
class Fixture:
    """One committed source and the container output it really produced.

    :attr:`expects` is **hand-declared** and is the claim the test checks. It is
    never computed from the code under test — the M2 kit's rule, because four
    graders shipped defective last cycle and every one was caught by a human
    reading data rather than by a test agreeing with itself.
    """

    name: str
    proves: str
    source: str
    stdout: str
    expects: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "proves": self.proves,
            "source": self.source,
            "expects": dict(self.expects),
        }


def load_fixtures(path: Optional[Path] = None) -> tuple[Fixture, ...]:
    """Read the committed fixture table. Eager, total, no defaults."""
    resolved = Path(path) if path is not None else FIXTURES_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise ConfigError(f"no fixture table at {resolved}") from missing
    except ValueError as broken:
        raise ConfigError(f"{resolved} is not readable JSON: {broken}") from broken
    entries = _required(raw, "fixtures", str(resolved))
    out: list[Fixture] = []
    for entry in entries:
        out.append(
            Fixture(
                name=str(_required(entry, "name", str(resolved))),
                proves=str(_required(entry, "proves", str(resolved))),
                source=str(_required(entry, "source", str(resolved))),
                stdout=str(_required(entry, "stdout", str(resolved))),
                expects=dict(_required(entry, "expects", str(resolved))),
            )
        )
    if not out:
        raise ConfigError(f"{resolved}: the fixture table declares no fixture")
    return tuple(out)


def sentinel_path(path: Optional[Path] = None) -> str:
    """The host path the ``sentinel`` fixture's source tries to write.

    Committed in the fixture table rather than spelled here, so the test that
    checks it never appears and the source that tries to create it quote one
    string.
    """
    resolved = Path(path) if path is not None else FIXTURES_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as broken:
        raise ConfigError(f"no readable fixture table at {resolved}") from broken
    return str(_required(raw, "sentinel_path", str(resolved)))


def _collapse_threshold(config: PolicyConfig) -> float:
    value = config.decision.get("escalation_rate_collapses_at")
    if not isinstance(value, (int, float)):
        raise ConfigError(
            "decision: missing escalation_rate_collapses_at. The rate at which an "
            "arm is published as having collapsed into the worker arm is a "
            "pre-declared number, never a judgement made after the run."
        )
    return float(value)


# ── CLI ──────────────────────────────────────────────────────────────────────


def render_plan(config: Optional[PolicyConfig] = None) -> str:
    lines = [
        "arch_policy — arm P, the compiled policy: the mind writes it once, it plays",
        "",
        "arms (the four differ in exactly one field: source):",
    ]
    for arm_id in POLICY_ARM_ORDER:
        arm = POLICY_ARMS[arm_id]
        role = "CONTROL" if arm.is_control else "UNDER TEST"
        lines.append(
            f"  {arm.id:<3} {arm.label:<17} {role:<11} kind={arm.source.kind:<9} "
            f"authoring_calls={arm.authoring_calls}"
        )
        lines.append(f"      {arm.why}")
    lines += [
        "",
        "the three controls are mandatory, and each separates something different:",
    ]
    for kind in CONTROL_KINDS:
        lines.append(f"  {kind:<9} {COMMITTED_SOURCES[kind].why}")
    lines += [
        "",
        f"entry point:      def {ENTRY_POINT}(situation)",
        f"action space:     {list(ACTIONS)}",
        f'escalate return:  {ESCALATE!r}  (first-class "I do not know")',
        f"unobserved value: {UNOBSERVED!r}",
        "",
        "three reported axes, never one number:",
        f"  outcome     {list(OUTCOME_KEYS)}",
        f"  escalation  {list(ESCALATION_KEYS)}",
        f"  acceptance  {list(ACCEPTANCE_KEYS)}",
    ]
    if config is not None:
        lines += ["", f"policy table: {config.path}", "", "arms as configured:"]
        for arm_id in POLICY_ARM_ORDER:
            play = config.play_for(arm_id)
            budget = config.budget_for(arm_id)
            lines.append(
                f"  {arm_id:<3} grain={play.grain} checkpoint={play.escalation_role} "
                f"max_escalation_calls={budget.max_escalation_calls}"
            )
        spec = config.episode
        lines += [
            "",
            f"episode: {spec.situations} situations, seed {spec.seed}, "
            f"every {spec.occlusion_every}th occluded",
        ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--config", default=None, help="path to the policy table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="the arms, the controls and the axes")
    plan.add_argument("--json", action="store_true")

    contract = subparsers.add_parser("contract", help="what a policy is written against")
    contract.add_argument("--json", action="store_true")

    config_cmd = subparsers.add_parser("config", help="the policy table as it was read")
    config_cmd.add_argument("--json", action="store_true")
    config_cmd.add_argument("--config", default=None, help="path to the policy table")

    episode = subparsers.add_parser("episode", help="the demo episode, as the host holds it")
    episode.add_argument("--json", action="store_true")
    episode.add_argument("--config", default=None, help="path to the policy table")

    runner = subparsers.add_parser("run", help="run the committed policies in a real jail")
    runner.add_argument("--arm", action="append", default=None, help="restrict to these arms")
    runner.add_argument(
        "--provider",
        default=PROVIDER_DOCKER,
        choices=(PROVIDER_DOCKER, PROVIDER_FAKE),
        help=(
            "which workspace backend to run in. 'fake' provisions but executes "
            "nothing, so every arm lands NO_RESULT — it exercises the plumbing only."
        ),
    )
    runner.add_argument("--config", default=None, help="path to the policy table")

    fixtures = subparsers.add_parser(
        "fixtures", help="re-run the committed fixtures for real and check the table"
    )
    fixtures.add_argument(
        "--provider",
        default=PROVIDER_DOCKER,
        choices=(PROVIDER_DOCKER, PROVIDER_FAKE),
        help="'docker' re-captures for real; 'fake' provisions and executes nothing",
    )
    fixtures.add_argument("--config", default=None, help="path to the policy table")
    return parser


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 2


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config) if getattr(args, "config", None) else None

    if args.command == "plan":
        if args.json:
            print(
                json.dumps(
                    {
                        "arms": {arm: POLICY_ARMS[arm].to_dict() for arm in POLICY_ARM_ORDER},
                        "arm_order": list(POLICY_ARM_ORDER),
                        "control_kinds": list(CONTROL_KINDS),
                        "entry_point": ENTRY_POINT,
                        "actions": list(ACTIONS),
                        "escalate": ESCALATE,
                        "unobserved": UNOBSERVED,
                        "outcome_keys": list(OUTCOME_KEYS),
                        "escalation_keys": list(ESCALATION_KEYS),
                        "acceptance_keys": list(ACCEPTANCE_KEYS),
                        "verdicts": list(VERDICTS),
                    },
                    indent=2,
                )
            )
        else:
            print(render_plan())
        return 0

    if args.command == "contract":
        if args.json:
            print(
                json.dumps(
                    {
                        "entry_point": ENTRY_POINT,
                        "domain": DOMAIN,
                        "contract": POLICY_CONTRACT,
                        "system": AUTHORING_SYSTEM,
                        "payload_keys": list(PAYLOAD_KEYS),
                        "driver": POLICY_DRIVER,
                    },
                    indent=2,
                )
            )
        else:
            print(DOMAIN)
            print()
            print(POLICY_CONTRACT)
        return 0

    try:
        config = load_policy_config(config_path)
    except ConfigError as broken:
        return _fail(str(broken), f"check the policy table at {config_path or DEFAULT_CONFIG_PATH}")

    if args.command == "config":
        payload = config.to_dict()
        try:
            payload["senses_config_hash"] = assert_senses_identical(config)
        except ConfigError as drifted:
            return _fail(str(drifted), "make every arm's senses cell identical, then re-run")
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(render_plan(config))
            print()
            print(f"senses_config_hash: {payload['senses_config_hash']}")
        return 0

    episode = demo_episode(config.episode)

    if args.command == "episode":
        payload = {
            "kind": "policy-episode",
            "spec": config.episode.to_dict(),
            "situations": [situation.to_dict() for situation in episode],
            "undecidable": [s.id for s in episode if not s.decidable],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for situation in episode:
                mark = " (undecidable)" if not situation.decidable else ""
                print(f"{situation.id}: {dict(situation.features)}{mark}")
        return 0

    if args.command == "fixtures":
        try:
            fixtures = load_fixtures()
        except ConfigError as broken:
            return _fail(str(broken), f"check the fixture table at {FIXTURES_PATH}")
        workspace = MuseWorkspace(provider=args.provider, max_result_chars=0)
        rows: list[dict[str, Any]] = []
        try:
            for fixture in fixtures:
                nonce = mint_nonce()
                program = build_program(fixture.source, episode, nonce, seed=config.episode.seed)
                output = run_in_jail(program, workspace)
                lines = read_result_lines(output, nonce)
                returns = classify(_payload_from(lines, nonce), episode)
                counts = PolicyLedger(returns).counts()
                declared = dict(fixture.expects.get("counts") or {})
                rows.append(
                    {
                        "name": fixture.name,
                        "proves": fixture.proves,
                        "nonce_matches": len(lines),
                        "counts": counts,
                        "declared": declared,
                        "agrees": counts == declared,
                        "captured": "\n".join(
                            line.replace(nonce, "{nonce}")
                            for line in output.splitlines()
                            if line.startswith(nonce) or "thinking about" in line
                        ),
                    }
                )
        finally:
            workspace.close()
        disagreed = [row["name"] for row in rows if not row["agrees"]]
        print(
            json.dumps(
                {
                    "kind": "policy-fixtures",
                    "provider": args.provider,
                    "sentinel_path": sentinel_path(),
                    "sentinel_on_host": Path(sentinel_path()).exists(),
                    "disagreed": disagreed,
                    "fixtures": rows,
                },
                indent=2,
            )
        )
        return 1 if disagreed else 0

    arms = tuple(args.arm) if args.arm else POLICY_ARM_ORDER
    unknown = [name for name in arms if name not in POLICY_ARMS]
    if unknown:
        return _fail(
            f"unknown arm(s) {unknown}",
            f"known arms: {', '.join(POLICY_ARM_ORDER)}",
        )
    try:
        senses_hash = assert_senses_identical(config)
    except ConfigError as drifted:
        return _fail(str(drifted), "make every arm's senses cell identical, then re-run")

    workspace = MuseWorkspace(provider=args.provider, max_result_chars=0)
    records: list[dict[str, Any]] = []
    try:
        for arm_id in arms:
            arm = POLICY_ARMS[arm_id]
            # The compiled arm has no mind to write it here: this lane is
            # hermetic. The baseline source stands in so the PATH is exercised,
            # and the record says so — it is not evidence about arm P.
            minds = {
                ROLE_CORTEX: scripted_author(COMMITTED_SOURCES[KIND_BASELINE].text),
                ROLE_WORKER: ah.scripted_worker(),
            }
            seams = aa.ScriptedSeams(minds, config=config, log=aa.CallLog())
            record = run_attempt(
                arm=arm,
                config=config,
                seams=seams,
                episode=episode,
                senses_hash=senses_hash,
                workspace=workspace,
            )
            record.scripted_author = not arm.is_control
            records.append(record.to_dict())
    finally:
        workspace.close()
    print(json.dumps({"kind": "policy-run", "live": False, "attempts": records}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
