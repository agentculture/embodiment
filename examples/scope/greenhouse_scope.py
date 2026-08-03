#!/usr/bin/env python3
"""greenhouse_scope — the non-colleague demo for the strategist seat (task t8).

``examples/greenhouse.py`` shows an actor gaining a *presence*. This file
shows the SAME kind of tiny host gaining a *strategist* above that actor: a
seam that issues typed, versioned, supersedable directives owning objectives
and priorities, while the acting loop stays exactly what it always was.

The scenario, in one sentence
------------------------------
A small greenhouse has two zones. The actor's own habit is to tend
``fern-bed`` first — a locally attractive default with nothing wrong with it
in isolation. ``orchid-bed`` is the zone that is actually critical this
morning (moisture far below its threshold). Left alone, the actor keeps
doing the locally-correct, globally-wrong thing forever: this is
docs/relationships.md's "no owner for salience" gap, made concrete. Once a
directive names ``orchid-bed`` the priority, the actor's very next boundary
changes what it does — and a later directive can supersede the first and
change it again. That is the whole demonstration.

Why this file never imports ``embodiment.scoped_run``
--------------------------------------------------------
It would be the obvious way to write this — construct a
:class:`~embodiment.scoped_run.ScopeGovernor`, call
:func:`~embodiment.scoped_run.run_scoped`, done. It is not done that way,
because ``embodiment.scope``, ``embodiment.scoped_run`` and
``embodiment.strategist_runner`` are not yet on ``embodiment.__all__``'s
curated public surface (task t15 adds them), and
``tests/test_demo_greenhouse.py::TestPublicApiOnly`` refuses any file under
``examples/`` (scanned recursively) that imports an undocumented submodule.

Two sibling tasks in this same plan hit the identical wall and each recorded
its own workaround rather than guessing at one: ``examples/scope/seats.py``
(task t7) returns a plain ``dict`` instead of constructing
:class:`~embodiment.scoped_run.ScopeGovernor` itself, and
``examples/scope/subordinate.py`` (task t9) mirrors
:class:`~embodiment.scope.ScopeDirective`'s fields rather than importing the
class. This module follows the SAME discipline for the same reason: every
shape below (:class:`Directive`, :class:`Responsibility`, :class:`Outcome`,
:class:`Snapshot`, :class:`LaneDegradation`) is a small, duck-typed,
field-compatible stand-in, pinned against the real dataclasses by
``tests/test_demo_scope_greenhouse.py`` so the mirror cannot silently drift.
Task t15 retires this workaround (and t7's, and t9's) in one pass once the
export lands.

What lives here versus what a HOST would really do
------------------------------------------------------
A host application — unlike a file under ``examples/`` — is free of that
restriction and can import ``embodiment.scoped_run`` directly. So the
pieces below are exactly what a real host supplies to
:class:`~embodiment.scoped_run.ScopeGovernor`, and this module's own test
suite is where the real ``ScopeGovernor``/``run_scoped`` are imported and
driven end to end against these pieces — the identical split
``tests/test_scope_seats.py`` already uses for ``seats.governed_by``. Copy
this file's shape into a host and the only change needed is
``from embodiment.scoped_run import ScopeGovernor, run_scoped`` at the top.

The known hazard this demo is built to avoid (issue #54)
------------------------------------------------------------
``StrategistRunner.active_directive`` (a lane's own issued chain) can name a
directive the actor never received — one the runner legitimately withheld as
stale or superseded. :func:`render_report` below reads only
``ScopedOutcome.active`` — what was actually APPLIED to the actor boundary by
boundary — and never a "register"-shaped property. ``ScriptedStrategist``
below exposes its own issued list (:attr:`ScriptedStrategist.issued`) purely
so the demo's OWN tests can show the discrepancy and prove the render never
uses it.

No live model call anywhere in this file (hard rule; hermetic in CI): the
actor is a pure function of its prompt (:func:`scripted_actor`, mirroring
``examples/greenhouse.py``'s ``scripted_cortex``) and the strategist is a
scripted, threadless stand-in (:class:`ScriptedStrategist`) that answers from
a fixed list of moves, never a network or a clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from embodiment import ModelResponse, Task, ToolCall, ToolError, ToolOutcome, UnknownToolError
from examples.scope import seats

__all__ = [
    "ZONE_FERN",
    "ZONE_ORCHID",
    "ZONES",
    "DEFAULT_ZONE",
    "MOISTURE",
    "THRESHOLD",
    "DIRECTIVE_FIELDS",
    "RESPONSIBILITY_FIELDS",
    "EXIT_DIRECTIVE",
    "EXIT_UNCHANGED",
    "STRATEGIST_ROLE",
    "SCRIPTED_STRATEGIST_MODEL",
    "Responsibility",
    "Directive",
    "Outcome",
    "Snapshot",
    "LaneDegradation",
    "GreenhouseRounds",
    "ScriptedStrategist",
    "scripted_actor",
    "zone_projector",
    "default_directive",
    "build_task",
    "capabilities_fixture",
    "governor_kwargs",
    "render_report",
]


# ── the world: two zones, one habit, one emergency ────────────────────────────

ZONE_FERN = "fern-bed"
ZONE_ORCHID = "orchid-bed"
ZONES: tuple[str, ...] = (ZONE_FERN, ZONE_ORCHID)

#: The actor's own habitual target with no scope wired at all — the "locally
#: attractive" default the plan's Before/After language names.
DEFAULT_ZONE = ZONE_FERN

#: Deterministic simulated readings. Orchid is genuinely the emergency here:
#: 12% is well under its threshold, fern is comfortably over its own.
MOISTURE = {ZONE_FERN: 42, ZONE_ORCHID: 12}
THRESHOLD = {ZONE_FERN: 30, ZONE_ORCHID: 25}


# ── mirrored shapes (see the module docstring: not imported, on purpose) ─────

#: ``embodiment.scope.ScopeDirective``'s field order. Pinned by test against
#: the real dataclass so this mirror cannot silently drift from it.
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

#: ``embodiment.scope.ScopeResponsibility``'s field order. Pinned by test.
RESPONSIBILITY_FIELDS: tuple[str, ...] = ("owner", "responsibility")

#: Cited literals — mirror ``embodiment.scope.SCOPE_EXIT_DIRECTIVE`` /
#: ``SCOPE_EXIT_UNCHANGED`` exactly (pinned by test), so a locally-built
#: :class:`Outcome` reads to ``embodiment.scoped_run._Governed._consume`` the
#: same way a real ``embodiment.scope.ScopeOutcome`` would.
EXIT_DIRECTIVE = "directive"
EXIT_UNCHANGED = "unchanged"

#: Mirrors ``embodiment.strategist_runner.STRATEGIST_ROLE`` — the provenance
#: label a host passes as ``StrategistRunner(..., role=...)``. Pinned by test.
STRATEGIST_ROLE = "strategist"
SCRIPTED_STRATEGIST_MODEL = "scripted-greenhouse-strategist"


@dataclass(frozen=True)
class Responsibility:
    """Mirrors ``embodiment.scope.ScopeResponsibility`` field for field."""

    owner: str = ""
    responsibility: str = ""


@dataclass(frozen=True)
class Directive:
    """Mirrors ``embodiment.scope.ScopeDirective`` field for field.

    Read the field list as an exclusion as much as an inclusion, exactly as
    the real shape's own docstring says: there is no ``tool``, no
    ``arguments``, no ``command`` and no ``approve`` here — a directive can
    only ever carry scope.
    """

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


@dataclass(frozen=True)
class Outcome:
    """A locally-built stand-in for ``embodiment.scope.ScopeOutcome``.

    Only the fields ``embodiment.scoped_run`` actually reads off a drained
    review (``directive``, ``snapshot_id``, ``role``, ``model``,
    ``exit_reason``, ``step_index``, ``tokens``, ``latency``) — read duck-typed
    there, never by ``isinstance``, which is what makes this stand-in usable
    against the REAL ``run_scoped()`` without ever importing its type.
    """

    directive: Optional[Directive] = None
    exit_reason: str = EXIT_UNCHANGED
    snapshot_id: str = ""
    role: str = STRATEGIST_ROLE
    model: str = SCRIPTED_STRATEGIST_MODEL
    step_index: int = 0
    tokens: Optional[int] = None
    latency: Optional[float] = None


@dataclass(frozen=True)
class Snapshot:
    """A minimal, host-built stand-in for ``embodiment.scope.ScopeSnapshot``.

    Only ``snapshot_id`` / ``current_directive`` are read by
    ``embodiment.scope_events`` (duck-typed); the rest is this demo's OWN
    reading of the greenhouse world, never anything embodiment supplies —
    exactly the split issue #2's compose-don't-reimplement rule requires.
    """

    snapshot_id: str = ""
    current_directive: Optional[str] = None
    active_workstreams: tuple[str, ...] = ()
    requested_decision: Optional[str] = None


@dataclass(frozen=True)
class LaneDegradation:
    """Mirrors ``embodiment.scope.ScopeDegradation`` field for field.

    ``embodiment.ledger.from_scope`` reads ``code`` / ``reason`` /
    ``step_index`` / ``model_turns`` duck-typed off exactly this shape.
    """

    code: str
    reason: str
    step_index: int = 0
    model_turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "step_index": self.step_index,
            "model_turns": self.model_turns,
        }


# ── the tool surface: no shell, a pure in-memory world ────────────────────────


class GreenhouseRounds:
    """The scope demo's own tool executor. Three tools, no shell.

    ``checked`` / ``tended`` are the state :func:`zone_projector` reads to
    build a projection from what actually happened — the demo's own world
    state, never anything embodiment infers on this host's behalf.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.checked: dict[str, int] = {}
        self.tended: list[str] = []
        self.failures: dict[str, int] = {}

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))
        if name == "check_zone":
            return self._check_zone(str(arguments.get("zone", "")))
        if name == "tend_zone":
            return self._tend_zone(arguments)
        if name == "finish":
            summary = str(arguments.get("summary", "")).strip()
            return ToolOutcome(result="round closed", finished=True, finish_summary=summary)
        raise UnknownToolError(f"this greenhouse round has no tool called {name!r}")

    def _check_zone(self, zone: str) -> ToolOutcome:
        if zone not in ZONES:
            # A ToolError costs one self-correcting step, never the round.
            self.failures[zone] = self.failures.get(zone, 0) + 1
            raise ToolError(f"no zone {zone!r} in this greenhouse")
        reading = MOISTURE[zone]
        self.checked[zone] = reading
        return ToolOutcome(result=f"{zone} reads {reading}% moisture")

    def _tend_zone(self, arguments: dict[str, Any]) -> ToolOutcome:
        zone = str(arguments.get("zone", ""))
        action = str(arguments.get("action", "watered"))
        self.tended.append(zone)
        return ToolOutcome(result=f"tended {zone} ({action})")


# ── the actor: a pure function of its prompt ──────────────────────────────────
#
# Mirrors ``examples/greenhouse.py``'s ``scripted_cortex`` shape: nothing is
# memoised, nothing is hard-coded about which zone matters. The target zone is
# read fresh from the transcript every turn, so a directive that changes the
# priority mid-round genuinely changes what the next turn does.

#: A directive's rendered priority reads "put <zone> first" — this demo's own
#: chosen phrasing (see :func:`default_directive` and the test fixtures), never
#: a coupling to ``embodiment.scoped_run.render_directive``'s exact heading
#: text. Only a scope message would ever contain this literal phrase.
_PRIORITY_RE = re.compile(r"put (fern-bed|orchid-bed) first")
_READING_RE = re.compile(r"(fern-bed|orchid-bed) reads (\d+)% moisture")
_TENDED_RE = re.compile(r"tended (fern-bed|orchid-bed)")


def _content_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _target_zone(transcript: str) -> str:
    """The zone the MOST RECENTLY inserted scope message names, or the habit."""
    matches = _PRIORITY_RE.findall(transcript)
    return matches[-1] if matches else DEFAULT_ZONE


def scripted_actor(messages: list[dict[str, Any]]) -> ModelResponse:
    """The hermetic actor: check the target zone, tend it, then finish.

    The target is recomputed from the transcript every turn, so a directive
    arriving mid-round genuinely changes the next tool call — this IS
    acceptance criterion 1's "directive changing actor behaviour at a
    boundary", made concrete rather than asserted.
    """
    transcript = "\n".join(_content_text(message) for message in messages)
    target = _target_zone(transcript)
    readings = dict(_READING_RE.findall(transcript))
    tended = set(_TENDED_RE.findall(transcript))
    if target not in readings:
        return _call("check_zone", zone=target)
    if target not in tended:
        reading = int(readings[target])
        action = "watered" if reading < THRESHOLD[target] else "checked"
        return _call("tend_zone", zone=target, action=action)
    summary = ", ".join(f"{zone}={reading}%" for zone, reading in sorted(readings.items()))
    return _call("finish", summary=f"tended {target} under the active scope (readings: {summary})")


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="", tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=dict(arguments))]
    )


# ── the strategist: scripted, threadless, no live dial ────────────────────────


class ScriptedStrategist:
    """A deterministic stand-in for ``StrategistRunner``'s public surface.

    Implements exactly the methods ``embodiment.scoped_run.ScopeGovernor``
    calls on ``strategist`` — ``start`` / ``consider`` / ``drain`` /
    ``degradation`` / ``degradations``, plus the ``role`` / ``model`` record
    fields — read duck-typed there, never by ``isinstance``. That is what lets
    this class drive the REAL ``run_scoped()`` end to end in this demo's own
    tests, and what lets a host's real ``StrategistRunner`` be a drop-in
    replacement for it later.

    ``moves`` is the whole script: each entry is a :class:`Directive` (issued
    as :data:`EXIT_DIRECTIVE`) or ``None`` (a scripted hold,
    :data:`EXIT_UNCHANGED`). One move is issued per ``drain()`` call that has
    a pending considered snapshot; once the script runs out, every further
    drain answers with a hold — a strategist that ran out of things to say
    reporting "the current scope still fits" rather than inventing a change.

    Mirrors the real runner's "one in-flight review, single replaceable
    pending slot" mechanic (spec claim c7) in miniature: a second
    :meth:`consider` before a :meth:`drain` displaces whatever was waiting,
    and the displacement is recorded on :attr:`degradations` rather than
    silently lost — the same "zero silent losses" discipline, held here too.
    """

    def __init__(
        self,
        moves: Sequence[Optional[Directive]],
        *,
        role: str = STRATEGIST_ROLE,
        model: str = SCRIPTED_STRATEGIST_MODEL,
        start_ok: bool = True,
        degrade_after: Optional[int] = None,
    ) -> None:
        self._moves = list(moves)
        self._role = role
        self._model = model
        self._start_ok = start_ok
        self._degrade_after = degrade_after
        self._cursor = 0
        self._drains = 0
        self._pending: Optional[tuple[Any, int]] = None
        self._ledger: list[LaneDegradation] = []
        #: Every move ever HANDED to ``drain()``, regardless of whether the
        #: composition layer went on to apply it. The demo's own window onto
        #: the issue #54 hazard: this can differ from what actually governed
        #: the actor (:attr:`~embodiment.scoped_run.ScopedOutcome.active`).
        self.issued: list[Directive] = []

    @property
    def role(self) -> str:
        return self._role

    @property
    def model(self) -> str:
        return self._model

    @property
    def degradations(self) -> list[LaneDegradation]:
        return list(self._ledger)

    def start(self) -> bool:
        return self._start_ok

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        if snapshot is None:
            return
        if self._pending is not None:
            _, displaced_step = self._pending
            self._ledger.append(
                LaneDegradation(
                    code="scripted-strategist-snapshot-displaced",
                    reason="a newer projection arrived before the pending one was reviewed",
                    step_index=displaced_step,
                )
            )
        self._pending = (snapshot, step_index)

    def drain(self, *, step_count: int = 0) -> list[Outcome]:
        if self._pending is None:
            return []
        snapshot, step_index = self._pending
        self._pending = None
        self._drains += 1
        snapshot_id = str(getattr(snapshot, "snapshot_id", "") or "")
        if self._cursor >= len(self._moves):
            return [self._hold(snapshot_id, step_index)]
        move = self._moves[self._cursor]
        self._cursor += 1
        if move is None:
            return [self._hold(snapshot_id, step_index)]
        self.issued.append(move)
        return [
            Outcome(
                directive=move,
                exit_reason=EXIT_DIRECTIVE,
                snapshot_id=snapshot_id,
                role=self._role,
                model=self._model,
                step_index=step_index,
            )
        ]

    def _hold(self, snapshot_id: str, step_index: int) -> Outcome:
        return Outcome(
            directive=None,
            exit_reason=EXIT_UNCHANGED,
            snapshot_id=snapshot_id,
            role=self._role,
            model=self._model,
            step_index=step_index,
        )

    def degradation(self) -> Optional[str]:
        if self._degrade_after is not None and self._drains >= self._degrade_after:
            return "the scripted strategist's lane stopped after its script ran out"
        return None


# ── the projector: built from THIS demo's own world state ────────────────────


def zone_projector(rounds: GreenhouseRounds) -> Callable[[Any], Optional[Snapshot]]:
    """Build a projector closed over *rounds* — the demo's own live world.

    embodiment cannot infer which domain facts are objectives, commitments or
    resources (issue #2's compose-don't-reimplement rule) — that reading is
    entirely this host's, and it is built from what the tool executor actually
    recorded, never from anything embodiment supplies.
    """

    def project(context: Any) -> Optional[Snapshot]:
        active = getattr(context, "active", None)
        report = getattr(context, "report", None)
        turn_index = getattr(context, "turn_index", 0)
        current = getattr(active, "scope_id", None) if active is not None else None
        workstreams = tuple(
            f"{zone}: checked={zone in rounds.checked} tended={zone in rounds.tended}"
            for zone in ZONES
        )
        requested = getattr(report, "requested_decision", None) if report is not None else None
        return Snapshot(
            snapshot_id=f"greenhouse-round-r{turn_index}",
            current_directive=current,
            active_workstreams=workstreams,
            requested_decision=requested,
        )

    return project


# ── the host's own default scope, and its work item ───────────────────────────


def default_directive() -> Directive:
    """The explicit HOST-DERIVED default every drive starts under.

    Never a strategist's decision and never inferred — issue #51 requires the
    actor to operate under an explicit scope from the very first boundary, and
    this is that scope: the standing rotation, fern-bed first.
    """
    return Directive(
        scope_id="standing-rotation",
        supersedes=None,
        objective="run the standing morning rotation",
        priorities=("put fern-bed first",),
        constraints=("never skip a zone's moisture check before tending it",),
        responsibilities=(Responsibility(owner="actor", responsibility=DEFAULT_ZONE),),
        success_conditions=("the prioritized zone is checked and tended",),
        review_when=("a zone's moisture crosses its threshold",),
        decision_summary="the host's standing default, in force until the strategist reviews",
        version=0,
    )


def build_task(task_id: str = "round-1") -> Task:
    """The work item. Deliberately zone-agnostic: the instruction never names
    a zone, so any zone the actor targets came from scope, not from the ask."""
    return Task(
        id=task_id,
        repo_path="",
        instruction="Do the standard morning greenhouse round.",
        context="",
        engine="greenhouse-scope-demo",
    )


# ── wiring through t7's seat resolution ───────────────────────────────────────


def capabilities_fixture() -> dict[str, Any]:
    """A ``/capabilities``-shaped payload with all three seats ready.

    Never dialled — a committed fixture, exactly the shape
    ``docs/live-test-results/*/capabilities.json`` carries
    (``examples/scope/seats.py``'s own docstring).
    """
    return {
        seats.ROLE_CORTEX: {
            "model": "demo-cortex",
            "endpoint": "http://localhost:8001",
            "ready": True,
        },
        seats.ROLE_WORKER: {
            "model": "demo-worker",
            "endpoint": "http://localhost:8001",
            "ready": True,
            "proxied": True,
        },
        seats.ROLE_SENSES: {
            "model": "demo-senses",
            "endpoint": "http://localhost:8001",
            "ready": True,
        },
    }


def governor_kwargs(
    rounds: GreenhouseRounds, strategist: Optional[Any], *, identity: Optional[str] = None
) -> dict[str, Any]:
    """Everything a host hands ``ScopeGovernor(**...)`` for this demo.

    Routed through :func:`examples.scope.seats.resolve_seats` /
    :func:`~examples.scope.seats.governed_by` (task t7), never constructed
    here directly — see the module docstring for why. A missing or not-ready
    ``cortex`` role drops *strategist* from the returned mapping even when the
    caller passed one in, which is exactly ``governed_by``'s own "a missing
    role degrades to actor-only" property, exercised for real here.
    """
    resolution = seats.resolve_seats(capabilities_fixture())
    return seats.governed_by(
        resolution,
        strategist=strategist,
        projector=zone_projector(rounds),
        default_scope=default_directive(),
        identity=identity,
    )


# ── rendering: every after-state property, in one place ──────────────────────


def render_report(outcome: Any, events: Sequence[Any] = (), *, issued: Sequence[Any] = ()) -> str:
    """Render a governed drive's full after-state. This IS the demo's output.

    Reads *outcome* (an ``embodiment.scoped_run.ScopedOutcome``, or anything
    duck-typed to it) plus the observer's collected *events*. *issued* is
    OPTIONAL and exists only to make the issue #54 hazard visible by contrast:
    what :attr:`ScriptedStrategist.issued` last handed over versus what
    ``outcome.active`` says actually governed. The report always reads
    ``outcome.active`` as the answer to "what governs?" — never *issued*.
    """
    lines: list[str] = []
    lines.append(f"exit: {outcome.exit_reason}")
    lines.append(f"summary: {outcome.result.summary}")
    lines.append("")
    lines.append("scope transitions (what actually governed the actor, never the register):")
    for transition in outcome.transitions:
        detail = f"  [{transition.kind}] scope={transition.scope_id or '(none)'}"
        detail += f" version={transition.version}"
        if transition.supersedes:
            detail += f" supersedes={transition.supersedes}"
        if transition.reason:
            detail += f" -- {transition.reason}"
        lines.append(detail)
    lines.append("")
    if outcome.active is not None:
        lines.append(
            f"active at drive end (RECEIVED, never the register): "
            f"{outcome.active.scope_id} (version {outcome.active.version})"
        )
    else:
        lines.append("active at drive end: none")
    if issued:
        last_issued = issued[-1]
        lines.append(
            f"strategist's last ISSUED directive (may be withheld -- embodiment#54): "
            f"{getattr(last_issued, 'scope_id', '(unknown)')}"
        )
    lines.append("")
    if outcome.scope_degradations:
        lines.append("strategist-lane degradations (relayed, never re-minted):")
        for entry in outcome.scope_degradations:
            code = getattr(entry, "code", "")
            reason = getattr(entry, "reason", "")
            lines.append(f"  {code}: {reason}")
    else:
        lines.append("strategist-lane degradations: none")
    lines.append("")
    scope_events = [
        event for event in events if str(getattr(event, "kind", "")).startswith("scope.")
    ]
    if scope_events:
        lines.append("observed scope.* events (the same host-wired observer as the actor's own):")
        for event in scope_events:
            lines.append(f"  {event.kind}: {event.detail}")
    return "\n".join(lines)
