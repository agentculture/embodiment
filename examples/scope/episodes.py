"""ScopeBench episodes — the machine-gradable world (plan task ``t9``).

An episode is a **small, closed, deterministic resource-allocation world**. It
exists so that the question "did the strategic layer decide better?" has an
answer a machine can compute, rather than an answer a judge can be persuaded of.

Deliverable 1 of ``t9``, field for field
----------------------------------------
Every episode carries all eight of the required properties, and every one of
them is *checked* rather than asserted in prose:

============================================  ==============================
property                                      where it is checked
============================================  ==============================
several candidate objectives                  :func:`structural_report`
dependencies between workstreams              :func:`structural_report`
limited resources                             :func:`structural_report`
actors with different capability/cost         :func:`structural_report`
durable constraints                           :func:`structural_report`
events that change state over time            :func:`structural_report`
a locally-attractive-but-globally-wrong move  ``oracle.schema_report`` — it
                                              needs the exact optimum to say
                                              the move really forgoes value
a valid "do not intervene" state              ``oracle.schema_report`` — it
                                              needs the optimum to say holding
                                              really is uniquely right
============================================  ==============================

The last two are **computed facts**, deliberately. A generator that declared its
own trap would be marking its own homework; here the solver has to agree, and a
seed whose trap does not survive that check produces an *invalid episode* which
is reported (verdict-rule condition 7) rather than quietly scored.

How the world runs
------------------
Discrete ticks, ``0`` to ``horizon``. At each **review tick** an allocation —
one workstream, or :data:`IDLE`, per actor — may be replaced. Between reviews
the allocation holds. Each tick:

1. every event scheduled at that tick fires (effort jumps, deadlines move,
   values change, an actor is lost);
2. each assigned actor whose workstream is **unblocked and incomplete** applies
   its ``rate`` in effort and spends its ``cost``;
3. an actor assigned to a *blocked* workstream spends and achieves nothing —
   that is the dependency lever, and it is why "work the visible leaf" is a
   trap rather than merely a slower route.

An objective is achieved iff every workstream it requires completed **on or
before its deadline**. Utility is the sum of achieved values minus constraint
penalties. That whole rule is :func:`~examples.scope.oracle.utility`.

Constraints are not feasibility
-------------------------------
A forbidden assignment is *executable*: the actor really can do the work, and a
directive that orders it really is carried out. What it earns is a recorded
violation and a penalty. Keeping the two apart matters: the deterministic
subordinate must apply a strategic decision **exactly**, and a subordinate that
quietly declined an assignment because it looked unwise would be exercising the
judgement this whole stage exists to hold constant.

What *is* infeasible is a physical impossibility — an unknown actor, an unknown
workstream, a skill the actor does not have, an actor that has been lost. Those
are recorded as **protocol** failures, never as strategic ones (``t9``
acceptance criterion 4).

Nothing here dials a model, opens a socket or reads the clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

__all__ = [
    "CONSTRAINT_FORBIDDEN_TAG",
    "CONSTRAINT_KINDS",
    "CONSTRAINT_SPEND_CEILING",
    "CONSTRAINT_UNATTENDED",
    "EVENT_ACTOR_LOST",
    "EVENT_DEADLINE_MOVE",
    "EVENT_EFFORT_JUMP",
    "EVENT_KINDS",
    "EVENT_VALUE_CHANGE",
    "FAMILIES",
    "FAMILY_ALLOCATION",
    "FAMILY_COMMITMENT",
    "FAMILY_CONSTRAINT",
    "FAMILY_CONTENTION",
    "FAMILY_CRITICAL_PATH",
    "FAMILY_DISRUPTION",
    "FAMILY_HANDOFF",
    "FAMILY_ORDER",
    "FAMILY_STEADY_STATE",
    "FIRST_CYCLE",
    "GENERATORS",
    "IDLE",
    "MIN_EPISODES_PER_FAMILY",
    "PROPERTY_ACTOR_PROFILES",
    "PROPERTY_CONSTRAINTS",
    "PROPERTY_DEPENDENCIES",
    "PROPERTY_EVENTS",
    "PROPERTY_LIMITED_RESOURCES",
    "PROPERTY_NON_INTERVENTION",
    "PROPERTY_OBJECTIVES",
    "PROPERTY_TRAP",
    "SCHEMA_PROPERTIES",
    "SCHEMA_WHY",
    "SEEDS_PATH",
    "STRUCTURAL_PROPERTIES",
    "VERIFIED_PROPERTIES",
    "Actor",
    "Allocation",
    "Constraint",
    "DEFERRED",
    "Episode",
    "EpisodeError",
    "Event",
    "Family",
    "Objective",
    "Rand",
    "Seeds",
    "Workstream",
    "canonical",
    "dependency_order",
    "executable",
    "first_cycle_episodes",
    "generate",
    "load_seeds",
    "structural_report",
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The committed seed table. An **input**, on the ``arch-policy-sampling.json``
#: pattern: this module carries no fallback seed and no default count, so a
#: series cannot quietly run on numbers nobody committed.
SEEDS_PATH = REPO_ROOT / "docs" / "live-test-results" / "scopebench-seeds.json"

#: The allocation target meaning "this actor works on nothing this tick".
IDLE = "-"

#: The floor on episodes per family, pre-registered here so the seed table
#: cannot be shrunk after seeing a result. Six is what the family-level sign
#: test in ``scopebench.verdict`` needs to be able to separate at all.
MIN_EPISODES_PER_FAMILY = 6


class EpisodeError(RuntimeError):
    """A generation or seed-table failure, raised with the offending name in it."""


# ── event and constraint vocabularies ─────────────────────────────────────────

#: A workstream turned out to be bigger than anyone thought.
EVENT_EFFORT_JUMP = "effort-jump"
#: An objective's deadline moved (``amount`` may be negative — pulled in).
EVENT_DEADLINE_MOVE = "deadline-move"
#: An objective turned out to be worth more or less than it looked.
EVENT_VALUE_CHANGE = "value-change"
#: An actor became unavailable from this tick onward.
EVENT_ACTOR_LOST = "actor-lost"

EVENT_KINDS: tuple[str, ...] = (
    EVENT_EFFORT_JUMP,
    EVENT_DEADLINE_MOVE,
    EVENT_VALUE_CHANGE,
    EVENT_ACTOR_LOST,
)

#: ``subject`` (an actor id) may not work a workstream carrying tag ``target``.
#: Executable, and penalised — see the module docstring.
CONSTRAINT_FORBIDDEN_TAG = "forbidden-tag"
#: Total spend may not exceed ``bound``.
CONSTRAINT_SPEND_CEILING = "spend-ceiling"
#: Workstream ``subject`` may not sit unassigned for more than ``bound``
#: consecutive ticks while it is unblocked and incomplete.
CONSTRAINT_UNATTENDED = "unattended"

CONSTRAINT_KINDS: tuple[str, ...] = (
    CONSTRAINT_FORBIDDEN_TAG,
    CONSTRAINT_SPEND_CEILING,
    CONSTRAINT_UNATTENDED,
)


# ── the world's shapes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Actor:
    """One executor, with a capability and a cost profile.

    ``rate`` is effort per tick, ``cost`` is resource per tick, and ``skills``
    is the set of workstream kinds it can touch at all. Two actors that differ
    in none of the three would make the allocation decision vacuous, which is
    why :data:`PROPERTY_ACTOR_PROFILES` checks that they differ.
    """

    id: str
    skills: tuple[str, ...]
    rate: int
    cost: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "skills": list(self.skills), "rate": self.rate, "cost": self.cost}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Actor":
        return cls(
            id=str(raw["id"]),
            skills=tuple(str(name) for name in raw.get("skills", ())),
            rate=int(raw["rate"]),
            cost=int(raw["cost"]),
        )


@dataclass(frozen=True)
class Workstream:
    """One unit of work: an effort pool, a kind, its dependencies, its tags."""

    id: str
    kind: str
    effort: int
    depends_on: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "effort": self.effort,
            "depends_on": list(self.depends_on),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Workstream":
        return cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            effort=int(raw["effort"]),
            depends_on=tuple(str(name) for name in raw.get("depends_on", ())),
            tags=tuple(str(name) for name in raw.get("tags", ())),
        )


@dataclass(frozen=True)
class Objective:
    """One candidate objective: what it is worth, when by, and what it needs."""

    id: str
    value: int
    deadline: int
    requires: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "value": self.value,
            "deadline": self.deadline,
            "requires": list(self.requires),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Objective":
        return cls(
            id=str(raw["id"]),
            value=int(raw["value"]),
            deadline=int(raw["deadline"]),
            requires=tuple(str(name) for name in raw["requires"]),
        )


@dataclass(frozen=True)
class Constraint:
    """One durable constraint — a thing that must hold however the work goes.

    ``text`` is the prose a strategist actually reads. It is required and
    non-empty: a constraint the strategist is never told about would measure
    clairvoyance rather than strategy.
    """

    id: str
    kind: str
    subject: str
    target: str
    bound: int
    penalty: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "target": self.target,
            "bound": self.bound,
            "penalty": self.penalty,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Constraint":
        return cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            subject=str(raw.get("subject", "")),
            target=str(raw.get("target", "")),
            bound=int(raw.get("bound", 0)),
            penalty=int(raw["penalty"]),
            text=str(raw["text"]),
        )


@dataclass(frozen=True)
class Event:
    """One scheduled change to the world. Deterministic, and told to nobody early.

    ``text`` is what a snapshot taken *after* the event fires reports as a
    material outcome. Before it fires the strategist is told nothing, which is
    what makes the ``disruption`` family a test of revision rather than of
    foresight.
    """

    tick: int
    kind: str
    subject: str
    amount: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "kind": self.kind,
            "subject": self.subject,
            "amount": self.amount,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Event":
        return cls(
            tick=int(raw["tick"]),
            kind=str(raw["kind"]),
            subject=str(raw["subject"]),
            amount=int(raw["amount"]),
            text=str(raw["text"]),
        )


#: One allocation: ``(actor id, workstream id or IDLE)`` pairs in actor order.
#: A tuple rather than a mapping so it is hashable — the exact solver memoises
#: on it, and a canonical order means two spellings of one decision are one key.
Allocation = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Episode:
    """One machine-gradable episode. Immutable; every field is data.

    ``default_allocation`` is the **explicit host-derived default scope** the
    actor operates under until a strategist issues a directive (issue #51). It
    is part of the episode rather than part of an arm, so every arm inherits the
    identical starting scope and a difference between arms cannot be it.
    """

    id: str
    family: str
    seed: int
    horizon: int
    review_ticks: tuple[int, ...]
    budget: int
    actors: tuple[Actor, ...]
    workstreams: tuple[Workstream, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[Constraint, ...]
    events: tuple[Event, ...]
    default_allocation: Allocation
    brief: str = ""

    def actor(self, actor_id: str) -> Optional[Actor]:
        return next((entry for entry in self.actors if entry.id == actor_id), None)

    def workstream(self, stream_id: str) -> Optional[Workstream]:
        return next((entry for entry in self.workstreams if entry.id == stream_id), None)

    @property
    def actor_ids(self) -> tuple[str, ...]:
        return tuple(entry.id for entry in self.actors)

    @property
    def workstream_ids(self) -> tuple[str, ...]:
        return tuple(entry.id for entry in self.workstreams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "seed": self.seed,
            "horizon": self.horizon,
            "review_ticks": list(self.review_ticks),
            "budget": self.budget,
            "actors": [entry.to_dict() for entry in self.actors],
            "workstreams": [entry.to_dict() for entry in self.workstreams],
            "objectives": [entry.to_dict() for entry in self.objectives],
            "constraints": [entry.to_dict() for entry in self.constraints],
            "events": [entry.to_dict() for entry in self.events],
            "default_allocation": [list(pair) for pair in self.default_allocation],
            "brief": self.brief,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Episode":
        return cls(
            id=str(raw["id"]),
            family=str(raw["family"]),
            seed=int(raw["seed"]),
            horizon=int(raw["horizon"]),
            review_ticks=tuple(int(tick) for tick in raw["review_ticks"]),
            budget=int(raw["budget"]),
            actors=tuple(Actor.from_dict(entry) for entry in raw["actors"]),
            workstreams=tuple(Workstream.from_dict(entry) for entry in raw["workstreams"]),
            objectives=tuple(Objective.from_dict(entry) for entry in raw["objectives"]),
            constraints=tuple(Constraint.from_dict(entry) for entry in raw["constraints"]),
            events=tuple(Event.from_dict(entry) for entry in raw["events"]),
            default_allocation=tuple(
                (str(pair[0]), str(pair[1])) for pair in raw["default_allocation"]
            ),
            brief=str(raw.get("brief", "")),
        )


# ── allocation helpers ────────────────────────────────────────────────────────


def executable(
    episode: Episode,
    actor_id: str,
    target: str,
    lost: frozenset[str] = frozenset(),
) -> bool:
    """Can this actor physically be put on this target at all?

    **Physical possibility only** — never policy. A forbidden-tag assignment is
    executable and penalised; an unskilled one is not executable and is a
    protocol failure. Keeping those apart is ``t9`` acceptance criterion 4.
    """
    actor = episode.actor(actor_id)
    if actor is None or actor_id in lost:
        return False
    if target == IDLE:
        return True
    stream = episode.workstream(target)
    return stream is not None and stream.kind in actor.skills


def canonical(episode: Episode, pairs: Sequence[Sequence[str]]) -> Allocation:
    """Put *pairs* into actor order, dropping unknown actors and duplicates.

    Canonicalisation is **not** repair: an unknown actor is dropped because it
    names nobody in this world, and an actor the caller did not mention idles.
    Every loss is visible to :func:`~examples.scope.subordinate.execute`, which
    records it; this function is only what makes two spellings of one decision
    hash to one key.
    """
    seen: dict[str, str] = {}
    for pair in pairs:
        if len(pair) != 2:
            continue
        actor_id, target = str(pair[0]), str(pair[1])
        if actor_id in seen or episode.actor(actor_id) is None:
            continue
        seen[actor_id] = target
    return tuple((actor_id, seen.get(actor_id, IDLE)) for actor_id in episode.actor_ids)


def dependency_order(episode: Episode) -> Optional[tuple[str, ...]]:
    """A topological order over the workstreams, or ``None`` when one cycles."""
    pending = {stream.id: set(stream.depends_on) for stream in episode.workstreams}
    order: list[str] = []
    while pending:
        ready = sorted(name for name, needs in pending.items() if not needs - set(order))
        if not ready:
            return None
        for name in ready:
            order.append(name)
            pending.pop(name)
    return tuple(order)


# ── the eight schema properties ───────────────────────────────────────────────

PROPERTY_OBJECTIVES = "several_candidate_objectives"
PROPERTY_DEPENDENCIES = "dependencies_between_workstreams"
PROPERTY_LIMITED_RESOURCES = "limited_resources"
PROPERTY_ACTOR_PROFILES = "actors_differ_in_capability_and_cost"
PROPERTY_CONSTRAINTS = "durable_constraints"
PROPERTY_EVENTS = "events_change_state_over_time"
PROPERTY_TRAP = "locally_attractive_globally_wrong_action"
PROPERTY_NON_INTERVENTION = "valid_do_not_intervene_state"

#: The six an episode carries on its own face.
STRUCTURAL_PROPERTIES: tuple[str, ...] = (
    PROPERTY_OBJECTIVES,
    PROPERTY_DEPENDENCIES,
    PROPERTY_LIMITED_RESOURCES,
    PROPERTY_ACTOR_PROFILES,
    PROPERTY_CONSTRAINTS,
    PROPERTY_EVENTS,
)

#: The two only the exact solver can vouch for.
VERIFIED_PROPERTIES: tuple[str, ...] = (PROPERTY_TRAP, PROPERTY_NON_INTERVENTION)

SCHEMA_PROPERTIES: tuple[str, ...] = STRUCTURAL_PROPERTIES + VERIFIED_PROPERTIES

SCHEMA_WHY: Mapping[str, str] = {
    PROPERTY_OBJECTIVES: (
        "at least three candidate objectives, so choosing between them is a decision "
        "rather than a reading"
    ),
    PROPERTY_DEPENDENCIES: (
        "at least one workstream blocked behind another, so the ordering of work can be "
        "wrong independently of how much work is done"
    ),
    PROPERTY_LIMITED_RESOURCES: (
        "the budget is strictly less than running every actor for the whole horizon, so "
        "spending is itself an allocation"
    ),
    PROPERTY_ACTOR_PROFILES: (
        "at least two actors differ in skills, rate or cost, so who does what matters"
    ),
    PROPERTY_CONSTRAINTS: (
        "at least one durable constraint with prose the strategist is actually shown, so "
        "honouring it is a choice rather than an accident"
    ),
    PROPERTY_EVENTS: (
        "at least one scheduled event changes the world mid-episode, so a plan made at "
        "tick 0 can become wrong without anyone making a mistake"
    ),
    PROPERTY_TRAP: (
        "the locally best move at some review strictly forgoes value against the exact "
        "optimum — verified by the solver, never declared by the generator"
    ),
    PROPERTY_NON_INTERVENTION: (
        "at some review, holding the standing allocation reaches the optimum AND at least "
        "one available change is strictly worse — so 'do not intervene' is a valid answer "
        "and churning can genuinely cost. Verified by the solver, not declared"
    ),
}


def structural_report(episode: Episode) -> dict[str, bool]:
    """The six properties an episode carries on its own face. Never raises."""
    full_run = sum(actor.cost for actor in episode.actors) * episode.horizon
    profiles = {(tuple(sorted(a.skills)), a.rate, a.cost) for a in episode.actors}
    return {
        PROPERTY_OBJECTIVES: len(episode.objectives) >= 3,
        PROPERTY_DEPENDENCIES: any(stream.depends_on for stream in episode.workstreams),
        PROPERTY_LIMITED_RESOURCES: episode.budget < full_run,
        PROPERTY_ACTOR_PROFILES: len(episode.actors) >= 2 and len(profiles) >= 2,
        PROPERTY_CONSTRAINTS: any(entry.text.strip() for entry in episode.constraints),
        PROPERTY_EVENTS: bool(episode.events),
    }


# ── the family catalog ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Family:
    """One scenario family, as data. Nothing in this module branches on an id."""

    id: str
    label: str
    stresses: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "stresses": self.stresses, "why": self.why}


FAMILY_CONTENTION = "contention"
FAMILY_CRITICAL_PATH = "critical_path"
FAMILY_ALLOCATION = "allocation"
FAMILY_DISRUPTION = "disruption"
FAMILY_STEADY_STATE = "steady_state"
FAMILY_CONSTRAINT = "constraint"
FAMILY_HANDOFF = "handoff"
FAMILY_COMMITMENT = "commitment"

#: The whole declared catalog — eight families, in presentation order. Declaring
#: all eight and running six is deliberate: condition 7 of the verdict rule
#: reports every absent cell, and an absence nobody declared cannot be reported.
FAMILY_ORDER: tuple[str, ...] = (
    FAMILY_CONTENTION,
    FAMILY_CRITICAL_PATH,
    FAMILY_ALLOCATION,
    FAMILY_DISRUPTION,
    FAMILY_STEADY_STATE,
    FAMILY_CONSTRAINT,
    FAMILY_HANDOFF,
    FAMILY_COMMITMENT,
)

FAMILIES: Mapping[str, Family] = {
    FAMILY_CONTENTION: Family(
        id=FAMILY_CONTENTION,
        label="contention",
        stresses="choosing which objective to abandon",
        why=(
            "more candidate objectives than the actors can finish inside the horizon. "
            "Every objective is individually reachable, so nothing is impossible — the "
            "only failure available is picking the wrong ones"
        ),
    ),
    FAMILY_CRITICAL_PATH: Family(
        id=FAMILY_CRITICAL_PATH,
        label="critical path",
        stresses="ordering work behind a dependency",
        why=(
            "the highest-value objective sits behind a long blocking root, and the "
            "cheapest visible completions do not. Finishing the leaves first is the "
            "locally correct move and loses the episode"
        ),
    ),
    FAMILY_ALLOCATION: Family(
        id=FAMILY_ALLOCATION,
        label="allocation",
        stresses="matching capability and cost to need",
        why=(
            "one workstream only the expensive specialist can touch, and cheap work the "
            "generalist can. Spending the specialist on the cheap work is locally "
            "indistinguishable from progress and exhausts the budget that the "
            "specialist-only work needed"
        ),
    ),
    FAMILY_DISRUPTION: Family(
        id=FAMILY_DISRUPTION,
        label="disruption",
        stresses="revising a plan that was right when it was made",
        why=(
            "a mid-episode event invalidates the tick-0 plan. A strategist that never "
            "revises and a strategist that revises well are indistinguishable in every "
            "other family and separate here"
        ),
    ),
    FAMILY_STEADY_STATE: Family(
        id=FAMILY_STEADY_STATE,
        label="steady state",
        stresses="not intervening",
        why=(
            "the standing plan is already optimal and every re-allocation is strictly "
            "worse. This is the non-intervention control the verdict rule's condition 3 "
            "grades: an architecture that improves by churning is not an improvement"
        ),
    ),
    FAMILY_CONSTRAINT: Family(
        id=FAMILY_CONSTRAINT,
        label="constraint",
        stresses="honouring a durable constraint under pressure",
        why=(
            "the locally best allocation breaches a standing constraint the strategist "
            "was told about. The breach is executable — the subordinate carries it out — "
            "so the failure is strategic, not a refusal by the machinery"
        ),
    ),
    FAMILY_HANDOFF: Family(
        id=FAMILY_HANDOFF,
        label="handoff",
        stresses="moving responsibility mid-flight",
        why=(
            "the right move is to move an in-progress workstream to a different owner and "
            "pay a handoff cost. Deferred: the schema carries no handoff-cost model, and "
            "adding one without a measured cost figure would be inventing the number the "
            "family exists to measure"
        ),
    ),
    FAMILY_COMMITMENT: Family(
        id=FAMILY_COMMITMENT,
        label="commitment",
        stresses="weighing a promise against a better opportunity",
        why=(
            "an earlier commitment constrains a later, higher-value choice. Deferred: "
            "breach cost is a domain judgement, not a benchmark constant, and a guessed "
            "penalty would decide the family's outcome by itself"
        ),
    ),
}

#: The first-cycle subset (task ``t9``, 2026-08-03). Six of eight. The frame
#: parked "which families land in the first cycle" as a plan-stage sequencing
#: decision; this is that decision, made here rather than after seeing data.
FIRST_CYCLE: tuple[str, ...] = (
    FAMILY_CONTENTION,
    FAMILY_CRITICAL_PATH,
    FAMILY_ALLOCATION,
    FAMILY_DISRUPTION,
    FAMILY_STEADY_STATE,
    FAMILY_CONSTRAINT,
)

#: The two that are declared and NOT run, each with the reason it is out. These
#: are condition 7's absent cells and they are reported as such.
DEFERRED: Mapping[str, str] = {
    FAMILY_HANDOFF: (
        "deferred from the first cycle: grading a handoff needs a handoff-cost model the "
        "episode schema does not carry, and a guessed cost would decide the family"
    ),
    FAMILY_COMMITMENT: (
        "deferred from the first cycle: grading a broken promise needs a breach-cost "
        "model that is a domain judgement rather than a benchmark constant"
    ),
}


# ── deterministic pseudo-randomness ───────────────────────────────────────────

_MASK = (1 << 64) - 1


class Rand:
    """A tiny, explicit LCG. Deterministic across interpreters and releases.

    Not :mod:`random`: the committed seed table has to reproduce the same
    episodes on any machine and any Python, and a benchmark whose worlds drift
    with the standard library's internals is not a benchmark.
    """

    def __init__(self, seed: int) -> None:
        self._state = (int(seed) * 0x9E3779B97F4A7C15 + 0x243F6A8885A308D3) & _MASK

    def _next(self) -> int:
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) & _MASK
        return (self._state >> 33) & 0x7FFFFFFF

    def between(self, low: int, high: int) -> int:
        """A value in ``[low, high]``."""
        return low + self._next() % max(1, high - low + 1)


# ── generation ────────────────────────────────────────────────────────────────

_HORIZON = 12
_REVIEWS: tuple[int, ...] = (0, 4, 8)


@dataclass(frozen=True)
class _Draft:
    """A family skeleton before the shared parts are attached."""

    actors: tuple[Actor, ...]
    workstreams: tuple[Workstream, ...]
    objectives: tuple[Objective, ...]
    constraints: tuple[Constraint, ...] = ()
    events: tuple[Event, ...] = ()
    brief: str = ""
    budget: Optional[int] = None
    horizon: int = _HORIZON
    review_ticks: tuple[int, ...] = _REVIEWS


def _default_allocation(draft: _Draft) -> Allocation:
    """The host-derived default scope: every actor on the first thing it can do.

    Deliberately naive and deliberately *not* the greedy control: it is the
    scope a host writes before anybody has thought about the episode, which is
    exactly what "the actor operates under an explicit default until a
    strategist issues one" means. The no-op planner holds it for the whole
    episode, so whatever it scores is the instrument scoring itself.
    """
    order: list[str] = []
    for objective in draft.objectives:
        for name in objective.requires:
            if name not in order:
                order.append(name)
    taken: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for actor in draft.actors:
        target = IDLE
        for name in order:
            stream = next(entry for entry in draft.workstreams if entry.id == name)
            if name not in taken and stream.kind in actor.skills and not stream.depends_on:
                target = name
                taken.add(name)
                break
        pairs.append((actor.id, target))
    return tuple(pairs)


def _spend_ceiling(budget: int) -> Constraint:
    return Constraint(
        id="budget",
        kind=CONSTRAINT_SPEND_CEILING,
        subject="",
        target="",
        bound=budget,
        penalty=12,
        text=(
            f"Total spend across the whole episode must not exceed {budget}. Every actor "
            "assigned to anything spends its cost each tick, including a tick it makes no "
            "progress on."
        ),
    )


def _assemble(family: str, seed: int, draft: _Draft) -> Episode:
    full_run = sum(actor.cost for actor in draft.actors) * draft.horizon
    budget = draft.budget if draft.budget is not None else (full_run * 3) // 4
    constraints = (_spend_ceiling(budget), *draft.constraints)
    return Episode(
        id=f"{family}-{seed}",
        family=family,
        seed=seed,
        horizon=draft.horizon,
        review_ticks=draft.review_ticks,
        budget=budget,
        actors=draft.actors,
        workstreams=draft.workstreams,
        objectives=draft.objectives,
        constraints=constraints,
        events=draft.events,
        default_allocation=_default_allocation(draft),
        brief=draft.brief,
    )


def _contention(rand: Rand) -> _Draft:
    return _Draft(
        actors=(
            Actor(id="ana", skills=("build",), rate=3, cost=2),
            Actor(id="bo", skills=("build", "ops"), rate=2, cost=3),
        ),
        workstreams=(
            Workstream(id="ship", kind="build", effort=rand.between(4, 6)),
            Workstream(id="chore", kind="ops", effort=rand.between(3, 5)),
            Workstream(id="migrate", kind="ops", effort=rand.between(13, 15)),
            Workstream(id="rebuild", kind="build", effort=rand.between(19, 21)),
            Workstream(
                id="cutover", kind="ops", effort=rand.between(5, 7), depends_on=("migrate",)
            ),
        ),
        objectives=(
            Objective(id="release", value=rand.between(7, 9), deadline=12, requires=("ship",)),
            Objective(id="upkeep", value=rand.between(5, 7), deadline=12, requires=("chore",)),
            Objective(
                id="platform",
                value=rand.between(32, 36),
                deadline=12,
                requires=("migrate", "cutover"),
            ),
            Objective(id="refit", value=rand.between(28, 32), deadline=12, requires=("rebuild",)),
        ),
        events=(
            Event(
                tick=2,
                kind=EVENT_VALUE_CHANGE,
                subject="refit",
                amount=-rand.between(3, 5),
                text="the refit's sponsor withdrew part of its budget; it is worth less now",
            ),
            Event(
                tick=5,
                kind=EVENT_DEADLINE_MOVE,
                subject="refit",
                amount=-2,
                text="the refit's deadline was pulled in by two ticks",
            ),
        ),
        budget=54,
        brief=(
            "Four objectives, two people, twelve ticks. Not all four fit. The two that "
            "finish soonest are worth the least; deciding which to abandon is the work."
        ),
    )


def _critical_path(rand: Rand) -> _Draft:
    return _Draft(
        actors=(
            Actor(id="spec", skills=("deep", "light"), rate=3, cost=3),
            Actor(id="gen", skills=("light",), rate=1, cost=1),
        ),
        workstreams=(
            Workstream(id="root", kind="deep", effort=rand.between(17, 20)),
            Workstream(id="branch", kind="deep", effort=rand.between(11, 13), depends_on=("root",)),
            Workstream(id="quick1", kind="light", effort=rand.between(2, 3)),
            Workstream(id="quick2", kind="light", effort=rand.between(2, 3)),
        ),
        objectives=(
            Objective(
                id="platform",
                value=rand.between(34, 40),
                deadline=12,
                requires=("root", "branch"),
            ),
            Objective(id="chore1", value=rand.between(5, 7), deadline=12, requires=("quick1",)),
            Objective(id="chore2", value=rand.between(5, 7), deadline=12, requires=("quick2",)),
        ),
        events=(
            Event(
                tick=5,
                kind=EVENT_VALUE_CHANGE,
                subject="chore2",
                amount=rand.between(1, 2),
                text="the second chore turned out to matter slightly more than expected",
            ),
        ),
        budget=40,
        brief=(
            "One large objective sits behind a long blocking root. Two small ones are "
            "nearly done. Only one person can touch the root."
        ),
    )


def _allocation(rand: Rand) -> _Draft:
    return _Draft(
        actors=(
            Actor(id="spec", skills=("crypto", "build"), rate=4, cost=5),
            Actor(id="gen", skills=("build",), rate=2, cost=1),
        ),
        workstreams=(
            Workstream(id="seal", kind="crypto", effort=rand.between(26, 30)),
            Workstream(id="frame", kind="build", effort=rand.between(7, 9)),
            Workstream(id="trim", kind="build", effort=rand.between(4, 6)),
            Workstream(id="fit", kind="build", effort=rand.between(6, 8), depends_on=("frame",)),
        ),
        objectives=(
            Objective(id="secure", value=rand.between(30, 36), deadline=12, requires=("seal",)),
            Objective(
                id="assembly", value=rand.between(16, 20), deadline=12, requires=("frame", "fit")
            ),
            Objective(id="polish", value=rand.between(6, 8), deadline=12, requires=("trim",)),
        ),
        events=(
            Event(
                tick=6,
                kind=EVENT_EFFORT_JUMP,
                subject="trim",
                amount=rand.between(1, 3),
                text="the trim work grew once someone opened it",
            ),
        ),
        budget=48,
        brief=(
            "One workstream only the expensive specialist can touch. The budget will not "
            "carry the specialist through the whole horizon."
        ),
    )


def _disruption(rand: Rand) -> _Draft:
    return _Draft(
        actors=(
            Actor(id="ana", skills=("build", "ops"), rate=3, cost=3),
            Actor(id="bo", skills=("build",), rate=2, cost=2),
        ),
        workstreams=(
            Workstream(id="pipeline", kind="ops", effort=rand.between(9, 11)),
            Workstream(id="widget", kind="build", effort=rand.between(8, 10)),
            Workstream(id="gadget", kind="build", effort=rand.between(6, 8)),
            Workstream(
                id="polish", kind="build", effort=rand.between(4, 6), depends_on=("widget",)
            ),
        ),
        objectives=(
            Objective(id="ops", value=rand.between(18, 22), deadline=12, requires=("pipeline",)),
            Objective(
                id="product", value=rand.between(24, 28), deadline=12, requires=("widget", "polish")
            ),
            Objective(id="side", value=rand.between(12, 15), deadline=12, requires=("gadget",)),
        ),
        events=(
            Event(
                tick=2,
                kind=EVENT_EFFORT_JUMP,
                subject="widget",
                amount=rand.between(9, 12),
                text="the widget turned out to be far larger than the estimate",
            ),
            Event(
                tick=6,
                kind=EVENT_ACTOR_LOST,
                subject="bo",
                amount=0,
                text="bo was pulled onto an incident and is not coming back this episode",
            ),
        ),
        budget=45,
        brief=(
            "A plan made at tick 0 is correct at tick 0. Something changes at the first "
            "review and it stops being correct."
        ),
    )


def _steady_state(rand: Rand) -> _Draft:
    return _Draft(
        actors=(
            Actor(id="ana", skills=("ops",), rate=2, cost=2),
            Actor(id="bo", skills=("build",), rate=2, cost=3),
        ),
        workstreams=(
            Workstream(id="errand", kind="ops", effort=rand.between(2, 4)),
            Workstream(id="rails", kind="ops", effort=rand.between(21, 23)),
            Workstream(id="hull", kind="build", effort=rand.between(21, 23)),
            Workstream(id="paint", kind="build", effort=rand.between(9, 11), depends_on=("hull",)),
        ),
        objectives=(
            Objective(id="errandry", value=rand.between(3, 5), deadline=12, requires=("errand",)),
            Objective(id="track", value=rand.between(24, 28), deadline=12, requires=("rails",)),
            Objective(id="frame", value=rand.between(26, 30), deadline=12, requires=("hull",)),
            Objective(
                id="finish", value=rand.between(30, 34), deadline=12, requires=("hull", "paint")
            ),
        ),
        constraints=(
            Constraint(
                id="rails-attended",
                kind=CONSTRAINT_UNATTENDED,
                subject="rails",
                target="",
                bound=1,
                penalty=rand.between(18, 22),
                text=(
                    "The rails must not sit unattended for more than one consecutive tick. "
                    "They are the only thing holding the schedule and an idle tick on them "
                    "is not recoverable."
                ),
            ),
        ),
        events=(
            Event(
                tick=5,
                kind=EVENT_VALUE_CHANGE,
                subject="finish",
                amount=rand.between(1, 3),
                text="the finished article is worth slightly more than booked",
            ),
        ),
        budget=54,
        brief=(
            "One tiny errand is the quickest visible win and the standing default is "
            "already pointed at it. Past the opening move the right allocation never "
            "changes again — the world does, and it still does not."
        ),
    )


def _constraint(rand: Rand) -> _Draft:
    return _Draft(
        actors=(
            Actor(id="ana", skills=("build", "audit"), rate=3, cost=3),
            Actor(id="bo", skills=("build",), rate=2, cost=2),
        ),
        workstreams=(
            Workstream(id="ledger", kind="audit", effort=rand.between(4, 6), tags=("sensitive",)),
            Workstream(id="frame", kind="build", effort=rand.between(13, 16)),
            Workstream(id="rig", kind="build", effort=rand.between(8, 10)),
            Workstream(id="fit", kind="build", effort=rand.between(5, 7), depends_on=("frame",)),
        ),
        objectives=(
            Objective(id="audit", value=rand.between(10, 13), deadline=12, requires=("ledger",)),
            Objective(
                id="assembly", value=rand.between(28, 33), deadline=12, requires=("frame", "fit")
            ),
            Objective(id="rigging", value=rand.between(13, 16), deadline=12, requires=("rig",)),
        ),
        constraints=(
            Constraint(
                id="separation",
                kind=CONSTRAINT_FORBIDDEN_TAG,
                subject="ana",
                target="sensitive",
                bound=0,
                penalty=rand.between(22, 26),
                text=(
                    "ana must never be assigned work tagged 'sensitive'. She built the "
                    "system under audit; separation of duties is not negotiable and does "
                    "not lapse because the schedule is tight."
                ),
            ),
        ),
        events=(
            Event(
                tick=5,
                kind=EVENT_VALUE_CHANGE,
                subject="rigging",
                amount=rand.between(1, 3),
                text="the rigging work was revalued upward",
            ),
        ),
        budget=56,
        brief=(
            "The quickest visible win is work one person is barred from touching. The "
            "bar is durable, stated, and carried out if ordered anyway."
        ),
    )


#: Family id to skeleton builder. A **table**, so nothing branches on a family
#: id and adding a family is adding a row rather than editing a function.
GENERATORS: Mapping[str, Callable[[Rand], _Draft]] = {
    FAMILY_CONTENTION: _contention,
    FAMILY_CRITICAL_PATH: _critical_path,
    FAMILY_ALLOCATION: _allocation,
    FAMILY_DISRUPTION: _disruption,
    FAMILY_STEADY_STATE: _steady_state,
    FAMILY_CONSTRAINT: _constraint,
}


def generate(family: str, seed: int) -> Episode:
    """Build the episode for ``(family, seed)``. Deterministic, total, no clock."""
    if family in DEFERRED:
        raise EpisodeError(f"{family!r} is deferred from the first cycle: {DEFERRED[family]}")
    builder = GENERATORS.get(family)
    if builder is None:
        raise EpisodeError(
            f"no generator for family {family!r}; the declared families are "
            f"{list(FAMILY_ORDER)}"
        )
    return _assemble(family, int(seed), builder(Rand(seed)))


# ── the committed seed table ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Seeds:
    """The committed seed table, parsed and validated once. No defaults."""

    path: Optional[Path]
    version: int
    episodes_per_family: int
    seeds: Mapping[str, tuple[int, ...]]
    deferred: Mapping[str, str]
    note: str = ""

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(self.seeds)

    def for_family(self, family: str) -> tuple[int, ...]:
        if family not in self.seeds:
            raise EpisodeError(f"the seed table carries no seeds for family {family!r}")
        return self.seeds[family]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "episodes_per_family": self.episodes_per_family,
            "seeds": {name: list(values) for name, values in self.seeds.items()},
            "deferred": dict(self.deferred),
            "note": self.note,
        }


def load_seeds(path: Optional[Path] = None) -> Seeds:
    """Read and validate the committed seed table. Eager, total, no fallbacks."""
    resolved = Path(path) if path is not None else SEEDS_PATH
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as missing:
        raise EpisodeError(f"no ScopeBench seeds at {resolved}") from missing
    except ValueError as broken:
        raise EpisodeError(f"{resolved} is not readable JSON: {broken}") from broken
    if not isinstance(raw, Mapping):
        raise EpisodeError(f"{resolved}: the seed table must be a JSON object")

    per_family = int(raw.get("episodes_per_family", 0))
    if per_family < MIN_EPISODES_PER_FAMILY:
        raise EpisodeError(
            f"{resolved}: episodes_per_family is {per_family}, below the pre-registered "
            f"minimum of {MIN_EPISODES_PER_FAMILY}"
        )
    table = raw.get("seeds")
    if not isinstance(table, Mapping):
        raise EpisodeError(f"{resolved}: 'seeds' must be an object keyed by family")

    seeds: dict[str, tuple[int, ...]] = {}
    for family in FIRST_CYCLE:
        if family not in table:
            raise EpisodeError(f"{resolved}: no seeds for first-cycle family {family!r}")
        values = tuple(int(value) for value in table[family])
        if len(values) != per_family or len(set(values)) != len(values):
            raise EpisodeError(
                f"{resolved}: family {family!r} carries {len(values)} seeds "
                f"({len(set(values))} unique); episodes_per_family is {per_family}"
            )
        seeds[family] = values
    extra = sorted(set(table) - set(FIRST_CYCLE))
    if extra:
        raise EpisodeError(f"{resolved}: seeds for families outside the first cycle: {extra}")

    deferred = raw.get("deferred")
    if not isinstance(deferred, Mapping) or set(deferred) != set(DEFERRED):
        raise EpisodeError(
            f"{resolved}: 'deferred' must record exactly the deferred families "
            f"{sorted(DEFERRED)} — condition 7 reports every absent cell"
        )
    return Seeds(
        path=resolved,
        version=int(raw.get("version", 0)),
        episodes_per_family=per_family,
        seeds=seeds,
        deferred={str(name): str(reason) for name, reason in deferred.items()},
        note=str(raw.get("note", "")),
    )


def first_cycle_episodes(path: Optional[Path] = None) -> tuple[Episode, ...]:
    """Every committed episode of the first cycle, in family then seed order."""
    seeds = load_seeds(path)
    return tuple(
        generate(family, seed) for family in FIRST_CYCLE for seed in seeds.for_family(family)
    )
