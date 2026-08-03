"""The deterministic perfect subordinate, and its scripted controls (Stage 1).

``t9`` deliverable 2. A scripted executor that applies strategic decisions
**exactly**, so that what Stage 1 measures is the quality of the decision and
nothing else. Four confounds are held at zero by construction:

===========================  ================================================
confound                     why it cannot appear here
===========================  ================================================
tool-use failure             there are no tools. An allocation is applied
                             arithmetically by :func:`~examples.scope.oracle.advance`
prompt-protocol failure      the executor reads a typed directive, not prose
actor intelligence           the executor has none. It never re-orders, never
                             repairs, never substitutes, and never declines
                             an assignment it thinks is unwise
latency                      there is no clock anywhere in this module
===========================  ================================================

"Perfect" is about **execution**, never about judgement
-------------------------------------------------------
A perfect subordinate does exactly what it was told. So an allocation that
breaches a durable constraint is *carried out* and the breach is priced against
the strategist — a subordinate that quietly refused it would be making the
strategic decision itself, which is the one thing this stage exists to isolate.

What it does refuse is the **physically impossible**: an unknown owner, an
unknown workstream, a skill the owner does not have, a second responsibility for
an owner already assigned, an owner that has been lost. Those are recorded as
:data:`UNEXECUTABLE_CODES` — a **protocol** axis, never an outcome one — and the
owner idles rather than being helpfully re-tasked (``t9`` acceptance
criterion 4).

Vocabulary borrowed from ``embodiment.scope``, and pinned to it
---------------------------------------------------------------
:class:`Directive`, :class:`Responsibility`, :func:`project` and
:data:`REFUSAL_CODES` mirror ``embodiment.scope``'s shapes field for field, and
``tests/test_scopebench.py`` asserts the mirrors against the real
``dataclasses.fields()`` and the real constants. They are **mirrored rather than
imported** for one mechanical reason: ``embodiment.scope`` is not on the
package's curated public surface yet, and
``tests/test_demo_greenhouse.py::TestPublicApiOnly`` refuses any ``examples``
import of an undocumented submodule. When the scope lane joins the surface these
mirrors become imports and the pinning tests become redundant; until then the
tests are what stop the two drifting.

``ScopeResponsibility`` is why this design works at all: it is already
``owner -> responsibility``, which is already an allocation. The strategist
allocates responsibility, the subordinate executes the allocation, and strategic
quality is the quality of the allocation sequence.

Nothing here dials a model, reads a clock, opens a socket or starts a thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from examples.scope import episodes as ep
from examples.scope import oracle as orc

__all__ = [
    "AUTHORITY_CODES",
    "AUTHORITY_EMBEDDED_COMMAND",
    "AUTHORITY_FORBIDDEN_KEY",
    "BASELINE_PLANNER",
    "DIRECTIVE_FIELDS",
    "DROPPED_AUTHORITY",
    "DROPPED_DUPLICATE",
    "DROPPED_INCOMPLETE",
    "DROPPED_UNKNOWN_SUPERSEDES",
    "DROPPED_VERSION_BACKWARD",
    "FORBIDDEN_DIRECTIVE_KEYS",
    "MARKER_HOLD",
    "PLANNER_GREEDY",
    "PLANNER_HOLD",
    "PLANNER_NONE",
    "PLANNER_ORACLE",
    "PLANNER_ORDER",
    "PLANNER_RANDOM",
    "PLANNER_REVISING",
    "PLANNER_STATIC",
    "REFUSAL_CODES",
    "RESPONSIBILITY_FIELDS",
    "SCRIPTED_PLANNERS",
    "SNAPSHOT_FIELDS",
    "UNEXECUTABLE_CODES",
    "UNEXECUTABLE_DUPLICATE_OWNER",
    "UNEXECUTABLE_NOT_SKILLED",
    "UNEXECUTABLE_OWNER_LOST",
    "UNEXECUTABLE_UNKNOWN_OWNER",
    "UNEXECUTABLE_UNKNOWN_RESPONSIBILITY",
    "AuthorityViolation",
    "Directive",
    "PlannerContext",
    "PlannerFn",
    "Refusal",
    "Register",
    "Responsibility",
    "Rollout",
    "ReviewRecord",
    "allocation_of",
    "authority_violations",
    "default_directive",
    "directive_from_payload",
    "directive_payload",
    "execute",
    "project",
]


# ── the mirrored scope vocabulary ─────────────────────────────────────────────

#: ``embodiment.scope.MARKER_HOLD`` — the strategist's first-class "nothing
#: should change" answer, which ScopeBench grades rather than treats as silence.
MARKER_HOLD = "[hold]"

#: ``embodiment.scope.ScopeDirective``'s fields, in order. Pinned by test.
DIRECTIVE_FIELDS: tuple[str, ...] = (
    "scope_id",
    "supersedes",
    "objective",
    "priorities",
    "constraints",
    "responsibilities",
    "success_conditions",
    "review_when",
    "decision_summary",
    "version",
)

#: ``embodiment.scope.ScopeResponsibility``'s fields. Pinned by test.
RESPONSIBILITY_FIELDS: tuple[str, ...] = ("owner", "responsibility")

#: ``embodiment.scope.ScopeSnapshot``'s fields. Pinned by test — the projector
#: below builds exactly these and nothing else, so what a live strategist reads
#: at Stage 2 is the same projection Stage 1 graded against.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "snapshot_id",
    "current_directive",
    "objectives",
    "commitments",
    "active_workstreams",
    "dependencies",
    "resource_state",
    "material_outcomes",
    "repeated_failures",
    "conflicts",
    "uncertainties",
    "requested_decision",
)

DROPPED_INCOMPLETE = "scope-directive-incomplete"
DROPPED_DUPLICATE = "scope-directive-duplicate-id"
DROPPED_UNKNOWN_SUPERSEDES = "scope-directive-unknown-supersedes"
DROPPED_VERSION_BACKWARD = "scope-directive-version-backward"
DROPPED_AUTHORITY = "scope-directive-authority-violation"

#: The five grounds a directive is refused on. Pinned against
#: ``embodiment.scope.REFUSAL_CODES`` by test.
REFUSAL_CODES: tuple[str, ...] = (
    DROPPED_INCOMPLETE,
    DROPPED_DUPLICATE,
    DROPPED_UNKNOWN_SUPERSEDES,
    DROPPED_VERSION_BACKWARD,
    DROPPED_AUTHORITY,
)

#: ``embodiment.scope.FORBIDDEN_DIRECTIVE_KEYS`` — keys a directive may never
#: carry at any depth. Pinned by test as a **set equality**, so neither list can
#: gain or lose a key without the other.
FORBIDDEN_DIRECTIVE_KEYS: tuple[str, ...] = (
    "tool",
    "tools",
    "tool_call",
    "tool_calls",
    "toolcalls",
    "arguments",
    "tool_arguments",
    "function_call",
    "command",
    "commands",
    "shell",
    "exec",
    "run",
    "edit",
    "edits",
    "file_edits",
    "patch",
    "diff",
    "write",
    "approve",
    "approved",
    "approval",
    "approvals",
    "deny",
    "denied",
    "allow",
    "allowed",
    "permit",
    "veto",
    "rewrite",
    "speak",
    "say",
    "reply",
    "utterance",
    "narration",
)


# ── the shapes ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Responsibility:
    """One ``owner -> responsibility`` allocation. Two strings and nothing else.

    Deliberately not widened. There is no field here a dispatcher could bind to
    and no callable a directive could carry, which is what keeps "allocates
    responsibility" from becoming "performs the work".
    """

    owner: str = ""
    responsibility: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"owner": self.owner, "responsibility": self.responsibility}


@dataclass(frozen=True)
class Directive:
    """One strategic decision, shaped exactly like ``ScopeDirective``."""

    scope_id: str = ""
    supersedes: Optional[str] = None
    objective: str = ""
    priorities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    responsibilities: tuple[Responsibility, ...] = ()
    success_conditions: tuple[str, ...] = ()
    review_when: tuple[str, ...] = ()
    decision_summary: str = ""
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "supersedes": self.supersedes,
            "objective": self.objective,
            "priorities": list(self.priorities),
            "constraints": list(self.constraints),
            "responsibilities": [entry.to_dict() for entry in self.responsibilities],
            "success_conditions": list(self.success_conditions),
            "review_when": list(self.review_when),
            "decision_summary": self.decision_summary,
            "version": self.version,
        }


@dataclass(frozen=True)
class Refusal:
    """A directive that was produced and refused, or a pair that could not run.

    Recorded, never dropped — the same discipline ``ScopeRegister`` holds, for
    the same reason: a strategist whose decision vanished silently leaves the
    subordinate working under scope somebody thought they had replaced.
    """

    code: str
    detail: str
    scope_id: str = ""
    owner: str = ""
    responsibility: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "scope_id": self.scope_id,
            "owner": self.owner,
            "responsibility": self.responsibility,
        }


#: The directive payload carried a key that reaches past scope authority.
AUTHORITY_FORBIDDEN_KEY = "authority-forbidden-key"
#: The directive's prose embeds something shaped like an operational instruction
#: — the *surrender* direction the spec's Stage 0 tests cover. Detected here so
#: an arm that writes one is recorded as an authority failure rather than being
#: silently scored on outcome.
AUTHORITY_EMBEDDED_COMMAND = "authority-embedded-command"

AUTHORITY_CODES: tuple[str, ...] = (AUTHORITY_FORBIDDEN_KEY, AUTHORITY_EMBEDDED_COMMAND)

#: Shapes that read as an operational instruction rather than as scope. Kept
#: small and explicit: a detector that fires on ordinary strategic prose would
#: make condition 4 unmeetable for reasons that have nothing to do with
#: authority, so this looks for shell syntax and command invocation, not for
#: imperative mood.
_COMMAND_SHAPES = (
    re.compile(r"```"),
    re.compile(r"""(?:^|[\s;|&`'"(])(?:sudo|rm|curl|wget|chmod|git|pip|uv|bash|sh)\s+-{0,2}\w"""),
    re.compile(r"\$\("),
    re.compile(r"[;|&]{2}"),
    re.compile(r"\b\w+\.py\b\s+--"),
)

#: The directive fields whose prose is scanned. The whole of the free text a
#: directive can carry, so nothing is exempt by being in a less obvious field.
_PROSE_FIELDS = (
    "objective",
    "priorities",
    "constraints",
    "success_conditions",
    "review_when",
    "decision_summary",
)


@dataclass(frozen=True)
class AuthorityViolation:
    """One recorded authority failure. Its own axis; never an outcome number."""

    code: str
    detail: str
    where: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "where": self.where}


def _walk_keys(payload: Any, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).strip().lower() in FORBIDDEN_DIRECTIVE_KEYS:
                found.append(str(key))
            found.extend(_walk_keys(value, depth + 1))
    elif isinstance(payload, (list, tuple)):
        for entry in payload:
            found.extend(_walk_keys(entry, depth + 1))
    return found


def _prose(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name in _PROSE_FIELDS:
        value = payload.get(name)
        if isinstance(value, str):
            out.append((name, value))
        elif isinstance(value, (list, tuple)):
            out.extend((name, entry) for entry in value if isinstance(entry, str))
    return out


def authority_violations(payload: Any) -> tuple[AuthorityViolation, ...]:
    """Every authority failure in *payload*. Never raises; total on any input."""
    found: list[AuthorityViolation] = []
    for key in _walk_keys(payload):
        found.append(
            AuthorityViolation(
                code=AUTHORITY_FORBIDDEN_KEY,
                detail=f"the payload carried the key {key!r}",
                where=key,
            )
        )
    if isinstance(payload, Mapping):
        for name, text in _prose(payload):
            for shape in _COMMAND_SHAPES:
                if shape.search(text):
                    found.append(
                        AuthorityViolation(
                            code=AUTHORITY_EMBEDDED_COMMAND,
                            detail=f"{name} reads as an operational instruction: {text[:120]!r}",
                            where=name,
                        )
                    )
                    break
    return tuple(found)


# ── reading and admitting a directive ─────────────────────────────────────────


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(entry) for entry in value)
    return ()


def directive_from_payload(payload: Any) -> tuple[Optional[Directive], Optional[Refusal]]:
    """Read a raw payload as a directive. Exactly one of the pair is ``None``.

    A payload carrying a forbidden key is refused **whole**, not stripped:
    honouring the rest would let the attempt succeed at the part that mattered
    and would leave a strategist probing the boundary at no cost.
    """
    keys = _walk_keys(payload)
    if keys:
        identity = payload.get("scope_id") if isinstance(payload, Mapping) else None
        return None, Refusal(
            code=DROPPED_AUTHORITY,
            detail=(
                f"the payload carried the key {keys[0]!r}, which reaches past scope "
                "authority into action authority; the whole directive was refused"
            ),
            scope_id=str(identity or ""),
        )
    if not isinstance(payload, Mapping):
        return None, Refusal(code=DROPPED_INCOMPLETE, detail="the payload was not an object")
    responsibilities = tuple(
        Responsibility(
            owner=str(entry.get("owner", "")),
            responsibility=str(entry.get("responsibility", "")),
        )
        for entry in payload.get("responsibilities", ())
        if isinstance(entry, Mapping)
    )
    supersedes = payload.get("supersedes")
    return (
        Directive(
            scope_id=str(payload.get("scope_id", "")),
            supersedes=None if supersedes is None else str(supersedes),
            objective=str(payload.get("objective", "")),
            priorities=_as_tuple(payload.get("priorities")),
            constraints=_as_tuple(payload.get("constraints")),
            responsibilities=responsibilities,
            success_conditions=_as_tuple(payload.get("success_conditions")),
            review_when=_as_tuple(payload.get("review_when")),
            decision_summary=str(payload.get("decision_summary", "")),
            version=int(payload.get("version", 0) or 0),
        ),
        None,
    )


class Register:
    """The directive chain, mirroring ``embodiment.scope.ScopeRegister``.

    Five refusal grounds, checked in the order below, each recorded:
    incomplete, duplicate id, unknown supersedes, version not advancing, and the
    authority refusal :func:`directive_from_payload` already made.
    """

    def __init__(self, default: Optional[Directive] = None) -> None:
        self._active: Optional[Directive] = None
        self._known: list[str] = []
        self._accepted: list[Directive] = []
        self._refusals: list[Refusal] = []
        if default is not None:
            self.offer(default)

    @property
    def active(self) -> Optional[Directive]:
        return self._active

    @property
    def version(self) -> int:
        return self._active.version if self._active is not None else -1

    @property
    def accepted(self) -> tuple[Directive, ...]:
        return tuple(self._accepted)

    @property
    def refusals(self) -> tuple[Refusal, ...]:
        return tuple(self._refusals)

    def offer(self, directive: Directive) -> Optional[Refusal]:
        """Admit *directive*, or say why not. The refusal is recorded either way."""
        refusal = self._refuse(directive)
        if refusal is not None:
            self._refusals.append(refusal)
            return refusal
        self._active = directive
        self._known.append(directive.scope_id)
        self._accepted.append(directive)
        return None

    def _refuse(self, directive: Directive) -> Optional[Refusal]:
        stamp = {"scope_id": directive.scope_id}
        if not directive.scope_id.strip() or not directive.objective.strip():
            missing = "scope_id" if not directive.scope_id.strip() else "objective"
            return Refusal(
                code=DROPPED_INCOMPLETE,
                detail=f"the directive carried no {missing}; it governs nothing",
                **stamp,
            )
        if directive.scope_id in self._known:
            return Refusal(
                code=DROPPED_DUPLICATE,
                detail=f"scope id {directive.scope_id!r} is already in the chain",
                **stamp,
            )
        if directive.supersedes is not None and directive.supersedes not in self._known:
            return Refusal(
                code=DROPPED_UNKNOWN_SUPERSEDES,
                detail=f"it supersedes {directive.supersedes!r}, which was never issued",
                **stamp,
            )
        if directive.version <= self.version:
            return Refusal(
                code=DROPPED_VERSION_BACKWARD,
                detail=(
                    f"version {directive.version} does not advance on the active "
                    f"directive's {self.version}; applying it could restore older scope"
                ),
                **stamp,
            )
        return None


# ── turning a directive into an allocation ────────────────────────────────────

UNEXECUTABLE_UNKNOWN_OWNER = "scopebench-unknown-owner"
UNEXECUTABLE_UNKNOWN_RESPONSIBILITY = "scopebench-unknown-responsibility"
UNEXECUTABLE_NOT_SKILLED = "scopebench-owner-not-skilled"
UNEXECUTABLE_DUPLICATE_OWNER = "scopebench-duplicate-owner"
UNEXECUTABLE_OWNER_LOST = "scopebench-owner-lost"

#: The five ways a well-formed directive can still name work nobody can do.
#: **Protocol**, never outcome: an arm that phrases its allocation badly is a
#: broken interface, not a bad strategy, and folding the two would report the
#: first as the second.
UNEXECUTABLE_CODES: tuple[str, ...] = (
    UNEXECUTABLE_UNKNOWN_OWNER,
    UNEXECUTABLE_UNKNOWN_RESPONSIBILITY,
    UNEXECUTABLE_NOT_SKILLED,
    UNEXECUTABLE_DUPLICATE_OWNER,
    UNEXECUTABLE_OWNER_LOST,
)


def allocation_of(
    episode: ep.Episode,
    state: orc.State,
    directive: Optional[Directive],
) -> tuple[ep.Allocation, tuple[Refusal, ...]]:
    """Read a directive's responsibilities as an allocation. **No repair.**

    An owner whose pair cannot run idles. It is never re-tasked onto something
    else, never swapped with another owner, and never given the workstream the
    executor thinks was meant — every one of those would be the subordinate
    making a strategic decision.
    """
    if directive is None:
        return ep.canonical(episode, ()), ()
    refusals: list[Refusal] = []
    pairs: list[tuple[str, str]] = []
    claimed: set[str] = set()
    for entry in directive.responsibilities:
        owner, target = entry.owner, entry.responsibility
        stamp = {"scope_id": directive.scope_id, "owner": owner, "responsibility": target}
        if episode.actor(owner) is None:
            refusals.append(
                Refusal(UNEXECUTABLE_UNKNOWN_OWNER, f"no actor named {owner!r}", **stamp)
            )
            continue
        if owner in claimed:
            refusals.append(
                Refusal(
                    UNEXECUTABLE_DUPLICATE_OWNER,
                    f"{owner!r} was already given a responsibility by this directive",
                    **stamp,
                )
            )
            continue
        claimed.add(owner)
        if target == ep.IDLE:
            pairs.append((owner, ep.IDLE))
            continue
        if episode.workstream(target) is None:
            refusals.append(
                Refusal(
                    UNEXECUTABLE_UNKNOWN_RESPONSIBILITY,
                    f"no workstream named {target!r}",
                    **stamp,
                )
            )
            continue
        if owner in state.lost:
            refusals.append(
                Refusal(UNEXECUTABLE_OWNER_LOST, f"{owner!r} is no longer available", **stamp)
            )
            continue
        if not ep.executable(episode, owner, target):
            refusals.append(
                Refusal(
                    UNEXECUTABLE_NOT_SKILLED,
                    f"{owner!r} cannot work a {episode.workstream(target).kind!r} workstream",
                    **stamp,
                )
            )
            continue
        pairs.append((owner, target))
    return ep.canonical(episode, pairs), tuple(refusals)


def directive_payload(
    episode: ep.Episode,
    allocation: ep.Allocation,
    *,
    scope_id: str,
    supersedes: Optional[str],
    version: int,
    objective: str,
    summary: str,
) -> dict[str, Any]:
    """Build a directive payload from an allocation. The scripted planners' pen."""
    return {
        "scope_id": scope_id,
        "supersedes": supersedes,
        "objective": objective,
        "priorities": [target for _owner, target in allocation if target != ep.IDLE],
        "constraints": [entry.text for entry in episode.constraints],
        "responsibilities": [
            {"owner": owner, "responsibility": target} for owner, target in allocation
        ],
        "success_conditions": [
            f"{entry.id} is achieved by tick {entry.deadline}" for entry in episode.objectives
        ],
        "review_when": ["the next scheduled review", "a workstream completes or is blocked"],
        "decision_summary": summary,
        "version": version,
    }


def default_directive(episode: ep.Episode) -> Directive:
    """The explicit **host-derived default scope** every arm starts under.

    Part of the episode rather than part of an arm, so no arm can be advantaged
    by a friendlier starting scope. Version ``0``, so the first real directive
    has to advance on it.
    """
    return Directive(
        scope_id=f"{episode.id}-default",
        supersedes=None,
        objective=episode.brief or "carry out the standing plan",
        priorities=[target for _owner, target in episode.default_allocation if target != ep.IDLE],
        constraints=tuple(entry.text for entry in episode.constraints),
        responsibilities=tuple(
            Responsibility(owner=owner, responsibility=target)
            for owner, target in episode.default_allocation
        ),
        success_conditions=tuple(
            f"{entry.id} is achieved by tick {entry.deadline}" for entry in episode.objectives
        ),
        review_when=("the next scheduled review",),
        decision_summary=(
            "the host's standing default scope, in force until a strategist replaces it"
        ),
        version=0,
    )


# ── the projector: episode state to a ScopeSnapshot-shaped mapping ────────────


def project(
    episode: ep.Episode,
    state: orc.State,
    review: int,
    active: Optional[Directive],
) -> dict[str, Any]:
    """The **host-supplied scope projector**, ScopeBench's own.

    embodiment ships none of this deliberately (spec ``h5``): only the host knows
    which domain facts constitute an objective, a commitment or a resource. This
    is ScopeBench being that host — and it is the same projection a live
    strategist reads at Stage 2, so Stage 1's grading and Stage 2's inputs cannot
    drift apart.

    It shows the world **as of now**: no future event appears, so a strategist is
    never handed foresight the arms are being compared on.
    """
    names = episode.workstream_ids
    remaining = {name: state.remaining[index] for index, name in enumerate(names)}
    done = {name: state.done_at[index] >= 0 for index, name in enumerate(names)}
    owned = {
        target: owner
        for owner, target in (
            () if active is None else ((r.owner, r.responsibility) for r in active.responsibilities)
        )
    }
    objectives = [
        (
            f"{entry.id}: worth {state.values[index]}, due by tick {state.deadlines[index]}, "
            f"needs {' + '.join(entry.requires)}"
        )
        for index, entry in enumerate(episode.objectives)
    ]
    workstreams = [
        (
            f"{entry.id} ({entry.kind}): "
            + ("complete" if done[entry.id] else f"{remaining[entry.id]} effort left")
            + (f", owned by {owned[entry.id]}" if entry.id in owned else ", unowned")
            + (f", tagged {'/'.join(entry.tags)}" if entry.tags else "")
        )
        for entry in episode.workstreams
    ]
    dependencies = [
        f"{entry.id} cannot progress until {need} completes"
        for entry in episode.workstreams
        for need in entry.depends_on
        if not done[need]
    ]
    outcomes = [event.text for event in episode.events if event.tick < state.tick]
    outcomes += [f"{name} completed" for name in names if done[name]]
    stalled = [
        entry.id
        for index, entry in enumerate(episode.workstreams)
        if not done[entry.id] and state.unattended[index] > 0
    ]
    ticks_left = episode.horizon - state.tick
    capacity = (
        sum(actor.rate for actor in episode.actors if actor.id not in state.lost) * ticks_left
    )
    outstanding = sum(remaining[name] for name in names if not done[name])
    conflicts = []
    if outstanding > capacity:
        conflicts.append(
            f"{outstanding} effort is still outstanding and at most {capacity} can be "
            f"delivered in the {ticks_left} ticks left; not everything can be finished"
        )
    for target, owner in owned.items():
        if done.get(target):
            conflicts.append(f"{owner} is allocated to {target}, which is already complete")
    at_risk = [
        entry.id
        for index, entry in enumerate(episode.objectives)
        if not all(done[name] for name in entry.requires)
        and state.deadlines[index] - state.tick <= 4
    ]
    return {
        "snapshot_id": f"{episode.id}-r{review}",
        "current_directive": None if active is None else active.scope_id,
        "objectives": objectives,
        "commitments": [entry.text for entry in episode.constraints],
        "active_workstreams": workstreams,
        "dependencies": dependencies,
        "resource_state": {
            "tick": state.tick,
            "ticks_remaining": ticks_left,
            "budget": episode.budget,
            "spent": state.spend,
            "actors": {
                actor.id: {
                    "skills": list(actor.skills),
                    "rate": actor.rate,
                    "cost": actor.cost,
                    "available": actor.id not in state.lost,
                }
                for actor in episode.actors
            },
        },
        "material_outcomes": outcomes,
        "repeated_failures": [f"{name} made no progress last tick" for name in stalled],
        "conflicts": conflicts,
        "uncertainties": [
            "the world may change again; nothing here forecasts a change that has not "
            "happened yet",
            *(
                [f"{name} is close to its deadline and not finished" for name in at_risk]
                if at_risk
                else []
            ),
        ],
        "requested_decision": (
            None
            if not conflicts
            else "something has to give: say which objectives this system is now pursuing"
        ),
    }


# ── the perfect subordinate ───────────────────────────────────────────────────


@dataclass(frozen=True)
class PlannerContext:
    """What a planner is handed. Read-only, and the same for every planner."""

    episode: ep.Episode
    state: orc.State
    review: int
    active: Optional[Directive]
    snapshot: Mapping[str, Any]
    next_version: int


#: A planner returns a directive payload, or ``None`` to hold. ``None`` as the
#: *planner itself* means no strategist is configured at all.
PlannerFn = Callable[[PlannerContext], Optional[Mapping[str, Any]]]


@dataclass(frozen=True)
class ReviewRecord:
    """One review boundary: what was offered, what was admitted, what ran."""

    review: int
    tick: int
    offered: bool
    held: bool
    accepted: bool
    allocation: ep.Allocation
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "review": self.review,
            "tick": self.tick,
            "offered": self.offered,
            "held": self.held,
            "accepted": self.accepted,
            "allocation": [list(pair) for pair in self.allocation],
            "changed": self.changed,
        }


@dataclass
class Rollout:
    """What one episode run produced. Four axes, kept apart from here on."""

    episode_id: str
    final: orc.State
    plan: orc.Plan
    reviews: list[ReviewRecord] = field(default_factory=list)
    offered: int = 0
    accepted: int = 0
    holds: int = 0
    refusals: list[Refusal] = field(default_factory=list)
    unexecutable: list[Refusal] = field(default_factory=list)
    violations: list[AuthorityViolation] = field(default_factory=list)
    directives: list[Directive] = field(default_factory=list)

    @property
    def churn(self) -> int:
        """How many reviews changed the standing allocation."""
        return sum(1 for record in self.reviews if record.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "plan": [[list(pair) for pair in step] for step in self.plan],
            "reviews": [record.to_dict() for record in self.reviews],
            "offered": self.offered,
            "accepted": self.accepted,
            "holds": self.holds,
            "churn": self.churn,
            "refusals": [entry.to_dict() for entry in self.refusals],
            "unexecutable": [entry.to_dict() for entry in self.unexecutable],
            "violations": [entry.to_dict() for entry in self.violations],
            "directives": [entry.to_dict() for entry in self.directives],
        }


def execute(episode: ep.Episode, planner: Optional[PlannerFn]) -> Rollout:
    """Run one episode under *planner*, applying every decision exactly.

    ``planner=None`` is the **no strategist configured** shape: no review ever
    happens, the host default scope governs throughout, and the record says so
    (``offered == holds == 0``). That is Stage 1's actor-only wiring, and it is
    structurally different from a strategist that reviews and answers
    :data:`MARKER_HOLD` every time — which scores identically and records
    differently, exactly as it should.
    """
    register = Register(default_directive(episode))
    rollout = Rollout(
        episode_id=episode.id,
        final=orc.initial_state(episode),
        plan=(),
    )
    state = orc.initial_state(episode)
    plan: list[ep.Allocation] = []
    previous: Optional[ep.Allocation] = None
    for review, tick in enumerate(episode.review_ticks):
        offered = held = accepted = False
        if planner is not None:
            payload = planner(
                PlannerContext(
                    episode=episode,
                    state=state,
                    review=review,
                    active=register.active,
                    snapshot=project(episode, state, review, register.active),
                    next_version=register.version + 1,
                )
            )
            if payload is None:
                held = True
                rollout.holds += 1
            else:
                offered = True
                rollout.offered += 1
                rollout.violations.extend(authority_violations(payload))
                directive, refusal = directive_from_payload(payload)
                if refusal is not None:
                    rollout.refusals.append(refusal)
                else:
                    refusal = register.offer(directive)
                    if refusal is not None:
                        rollout.refusals.append(refusal)
                    else:
                        accepted = True
                        rollout.accepted += 1
                        rollout.directives.append(directive)
        allocation, unexecutable = allocation_of(episode, state, register.active)
        rollout.unexecutable.extend(unexecutable)
        rollout.reviews.append(
            ReviewRecord(
                review=review,
                tick=tick,
                offered=offered,
                held=held,
                accepted=accepted,
                allocation=allocation,
                changed=previous is not None and allocation != previous,
            )
        )
        plan.append(allocation)
        previous = allocation
        state = orc.advance(episode, state, allocation, orc.window_end(episode, review))
    rollout.final = state
    rollout.plan = tuple(plan)
    return rollout


# ── the scripted planners: Stage 1's deterministic controls ───────────────────

PLANNER_NONE = "none"
PLANNER_HOLD = "hold"
PLANNER_GREEDY = "greedy"
PLANNER_RANDOM = "random"
PLANNER_STATIC = "static"
PLANNER_REVISING = "revising"
PLANNER_ORACLE = "oracle"

#: The pre-registered **actor-only baseline** for Stage 1, fixed here rather
#: than chosen after seeing a result.
#:
#: ``greedy`` and not ``none``, deliberately, and the choice costs the arm under
#: test: an actor loop with no strategist does not sit still under a stale
#: default — it pursues whatever progress is locally visible, which is what
#: ``greedy`` plays. Comparing against ``none`` would use a strictly weaker
#: baseline and inflate every measured gain. ``none`` is retained as the no-op
#: instrument control, where anything it scores is the harness scoring itself.
BASELINE_PLANNER = PLANNER_GREEDY


def _payload_for(
    context: PlannerContext,
    allocation: ep.Allocation,
    name: str,
    summary: str,
) -> dict[str, Any]:
    active = context.active
    return directive_payload(
        context.episode,
        allocation,
        scope_id=f"{name}-{context.episode.id}-r{context.review}",
        supersedes=None if active is None else active.scope_id,
        version=context.next_version,
        objective=context.episode.brief or "pursue the objectives worth the most",
        summary=summary,
    )


def _hold(_context: PlannerContext) -> Optional[Mapping[str, Any]]:
    return None


def _greedy(context: PlannerContext) -> Optional[Mapping[str, Any]]:
    allocation = orc.greedy_allocation(context.episode, context.state, context.review)
    return _payload_for(
        context,
        allocation,
        PLANNER_GREEDY,
        "put everyone on whatever finishes soonest",
    )


def _random(context: PlannerContext) -> Optional[Mapping[str, Any]]:
    options = orc.feasible(context.episode, context.state)
    rand = ep.Rand(context.episode.seed * 1009 + context.review)
    allocation = options[rand.between(0, len(options) - 1)]
    return _payload_for(
        context,
        allocation,
        PLANNER_RANDOM,
        "a legal allocation chosen without regard to what it achieves",
    )


def _static(context: PlannerContext) -> Optional[Mapping[str, Any]]:
    if context.review > 0:
        return None
    naive = orc.naive(context.episode, context.state.tick)
    plan = orc.plan_from(naive, context.state, context.review)
    if not plan:
        return None
    return _payload_for(
        context,
        plan[0],
        PLANNER_STATIC,
        "the best plan against the world as it looks at tick 0, and never revised",
    )


def _revising(context: PlannerContext) -> Optional[Mapping[str, Any]]:
    naive = orc.naive(context.episode, context.state.tick)
    plan = orc.plan_from(naive, context.state, context.review)
    if not plan:
        return None
    return _payload_for(
        context,
        plan[0],
        PLANNER_REVISING,
        "the best plan against the world as it looks right now, recomputed each review",
    )


def _oracle(context: PlannerContext) -> Optional[Mapping[str, Any]]:
    plan = orc.plan_from(context.episode, context.state, context.review)
    if not plan:
        return None
    return _payload_for(
        context,
        plan[0],
        PLANNER_ORACLE,
        "the exact optimum, computed with knowledge of events that have not happened",
    )


#: The scripted planners, as a **table**. Nothing in this module branches on a
#: planner id, so adding one is adding a row.
SCRIPTED_PLANNERS: Mapping[str, Optional[PlannerFn]] = {
    PLANNER_NONE: None,
    PLANNER_HOLD: _hold,
    PLANNER_GREEDY: _greedy,
    PLANNER_RANDOM: _random,
    PLANNER_STATIC: _static,
    PLANNER_REVISING: _revising,
    PLANNER_ORACLE: _oracle,
}

#: Presentation order, weakest instrument first, unachievable ceiling last.
PLANNER_ORDER: tuple[str, ...] = (
    PLANNER_NONE,
    PLANNER_HOLD,
    PLANNER_RANDOM,
    PLANNER_GREEDY,
    PLANNER_STATIC,
    PLANNER_REVISING,
    PLANNER_ORACLE,
)


def planner_ids() -> Sequence[str]:
    """The scripted planner ids, in presentation order."""
    return PLANNER_ORDER
