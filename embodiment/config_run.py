"""Config-governed composition over the actor loop — ``run()``, unmodified (task t9).

:mod:`embodiment.config_review` is the config strategist's reasoning and
:mod:`embodiment.config_runner` is its thread. This module is the third piece
and the only one the actor ever meets: it composes :func:`embodiment.loop.run`
**through the seams that function already exposes**, so a host gains a
configuration tier without the acting loop changing by one line.

The diff to ``loop.py`` from this work is zero
----------------------------------------------
That is an acceptance criterion, not an aspiration, and it is held four ways —
the three the README already states for the advisory lane, plus one this lane
adds:

* :func:`run_configured` calls :func:`embodiment.loop.run` **once**, passing only
  keywords ``run`` already declares. Everything a host would have handed ``run``
  is forwarded verbatim through ``**actor_kwargs``, so an unknown keyword is
  refused by ``run``'s own signature rather than absorbed here — this function
  structurally cannot invent a parameter.
* With the lane off, **every** host object reaches ``run`` by identity: not a
  copy, not a shim. With the lane on, the host's ``complete`` seam *still*
  reaches ``run`` by identity, which is stronger than the advisory lane manages
  and is the whole architectural point (below).
* ``loop.py`` names no scope identifier at all, and now no config identifier
  either. ``tests/test_config_run.py`` reads its AST to say so, and walks the
  actor's whole transitive import closure to prove no config module is reachable
  from it.

The worker is UNAWARE — so there is no seam to sit in
------------------------------------------------------
The advisory lane wrapped the host's ``complete`` in order to append a rendered
directive at the turn boundary. That mechanism is what the redesign deletes:
*"with the worker unaware there is no prose to obey — unawareness is the
mechanism, not a detail"*. So this module wraps ``complete`` **never**. It has
no message builder, imports no framing module, and there is no dict literal
carrying ``role``/``content`` anywhere in it; ``tests/test_config_run.py`` pins
all three by AST.

What reaches the actor instead is *configuration*: the composed system prompt of
the seat's effective :class:`~embodiment.config_lifecycle.SeatConfig`, chosen
once, before the drive begins, and constant for its whole life. Nothing arrives
mid-drive, because nothing is delivered at all.

The whole host pattern, and where a change actually lands
-----------------------------------------------------------
::

    outcome = run_configured(complete, task, executor=…, max_steps=…,
                             governor=ConfigGovernor(lifecycle=life,
                                                     reviewer=runner,
                                                     projector=project,
                                                     ledger=ledger))

Inside, in order:

1. :meth:`~embodiment.config_lifecycle.ConfigLifecycle.advance` — **before** the
   run opens, while every seat is idle. Whatever the gate now allows lands here.
2. :meth:`~embodiment.config_lifecycle.ConfigLifecycle.begin_run` — the seat's
   configuration is pinned to a frozen handle and the seat is marked busy.
3. ``run(...)`` — with the composed prompt, the host's own ``complete``, and the
   reviewer fed observationally through ``progress``.
4. :meth:`~embodiment.config_lifecycle.ConfigLifecycle.end_run`, then
   ``advance`` again — so a proposal the reviewer produced *during* the drive
   lands *after* it.

Steps 1 and 4 are the same call and that is deliberate: configuration changes
only ever between runs. The gate would refuse a mid-run apply anyway — it checks
seat-idle itself — so this is the recording guarantee rather than the safety
one, and both layers are wanted. ``end_run`` runs in a ``finally``: a drive that
aborts must not leave its seat marked busy forever, which would be a lane that
silently never receives configuration again.

Delivery is CONFIGURATION, and the effect is still a sample
-------------------------------------------------------------
The change is exact. What the seat does with it is not: a rewritten prompt still
routes through a model and the response remains a sample. The frame's non-goal
forbids the other wording, guarded because ``h3``/``d5`` showed "provably never
executes" hardening into an overclaim. Nothing in this module or its docs claims
a deterministic *effect*.

Deviation ``d5``, resolved here: the demotion is a MAPPING, not a sixth kind
-----------------------------------------------------------------------------
``t4``'s gate emits a ``verified → proposed`` transition when a proposal's
verification turns out to be evidence about a baseline that has since moved
(:data:`~embodiment.config_lifecycle.CHANGE_STALE_VERIFICATION`). ``t5``'s
:mod:`embodiment.config_events` has five change-state kinds and none of them is
named ``demoted``. The plan named this module as where that is resolved, and it
is resolved as a **documented mapping**:

* A demotion is **not a new state**. ``_demote_stale_verification`` stores the
  proposal back in :data:`~embodiment.config_lifecycle.STATE_PROPOSED`, and it
  must be verified again before it can apply. The kind that names that state is
  :data:`~embodiment.config_events.CONFIG_EVENT_PROPOSED`, so the demotion is a
  **re-entry into ``proposed``** and translates as one.
* What distinguishes it from a first proposal is the ``code`` field — already in
  :mod:`embodiment.config_events`' envelope, not invented here — set to
  :data:`DEMOTION_CODE`. A host branches on that, never on ``detail``'s free
  text, which is the contract that module states.
* The refusal ``t4`` records alongside the transition translates to
  :data:`~embodiment.config_events.CONFIG_EVENT_DEGRADATION`, so the fix lands on
  the fault stream where a host looks for one.

Why a mapping rather than a sixth kind. The spec fixes the vocabulary at five
change-state kinds (``c8``, graded by ``h8``); widening it is a larger departure
from the committed frame than mapping onto a kind that already means exactly
what happened. And a sixth kind would have to be added to
:mod:`embodiment.config_events` — a module a sibling task in this wave may be
editing, on a lane whose whole discipline is that the schema modules stay leaves.
``tests/test_config_run.py``'s ``TestTheDemotionIsRepresentable`` holds every
clause of this in executable form, including the end-to-end path through the
real gate.

Two streams, and exactly one event per fact
---------------------------------------------
The lifecycle keeps three append-only streams and this module reads all three
with a cursor, so a lifecycle shared across drives never re-emits history:

* :attr:`~embodiment.config_lifecycle.ConfigLifecycle.transitions` → the
  **state** stream: ``proposed`` / ``verified`` / ``applied`` / ``rejected``.
  One transition normally produces one event. The single exception is a
  suite that *ran and failed*, which produces two, because two things happened:
  a verification completed (``verified``, ``passed=False`` — the case
  :func:`~embodiment.config_events.verified_event` documents itself as firing
  for) and the proposal became terminal (``rejected``).
* :attr:`~embodiment.config_lifecycle.ConfigLifecycle.degradations` → the
  **fault** stream, uniformly ``config.degradation``. Every refusal carries a
  code naming its fix, and that is what a host reads when something did not
  happen.
* :attr:`~embodiment.config_lifecycle.ConfigLifecycle.deferrals` → **not
  events**, on purpose, and carried on :attr:`ConfiguredOutcome.deferrals`
  instead. A deferral is the gate *working* — the seat was busy — and t4's
  deviation ``d4`` already recorded the reasoning: a stream that rises during
  normal operation is a stream nobody reads, which costs exactly the C3
  visibility it was built for. Nothing is dropped; it is counted and returned.

The ledger is called for its DURABILITY, not for its event
------------------------------------------------------------
:meth:`~embodiment.config_ledger.ConfigLedger.record_applied` returns a
``config.change.applied`` event as a convenience for callers with no other
stream. This composition has one — the transition stream above — so the returned
event is deliberately discarded and the transition remains the single source of
every state event. Emitting both would double-count applies, which is the
plausible-well-formed-wrong-answer failure family (#56) rather than extra
information. The ledger's own :attr:`~embodiment.config_ledger.ConfigLedger.
degradations` *are* drained, because a load or save failure is a fault nothing
else reports.

Capability changes route through the HOST's executor factory, or nowhere
-------------------------------------------------------------------------
A tools or permissions unit selects host-declared capability ids. Turning a
selection into an actual tool surface is the host's, always: ``h11`` says no
configuration path may grant an ability the host has not itself wired, and this
module constructs no executor and holds no approval path. The seam is
:attr:`ConfigGovernor.executor_for` — ``(SeatConfig) -> ToolExecutor`` — called
once per drive with the pinned configuration. Absent, the host's own executor is
forwarded **by identity**; raising or returning nothing degrades to the same
thing, with a record.

Degrade, never raise
--------------------
Every call into host, reviewer, lifecycle or ledger code is guarded and every
guard records a :class:`~embodiment.config_change.ConfigDegradation` (constraint
C3). A dead reviewer, a raising projector, a hostile ledger and a raising
executor factory all leave the actor running under the configuration it started
with, and the drive completes. Nothing here degrades silently and nothing here
raises into the acting loop's main path.

**No new ``config-`` degradation constant is minted in this module.** Its records
carry :mod:`embodiment.config_review`'s and :mod:`embodiment.config_runner`'s
codes where they fit, and this module's own three where they do not — declared
immediately below so a ledger reader has one place to look.

Stdlib only beyond this tier's own modules and :mod:`embodiment.loop`
(constraint C1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional

# Direct submodule imports, NOT ``from embodiment import config_events``: the
# latter names the package hub, whose lazy re-export surface pulls the whole
# closure in.
import embodiment.config_events as config_events
from embodiment.config_change import SEAT_WORKER, ConfigChange, ConfigDegradation
from embodiment.config_lifecycle import (
    CHANGE_STALE_VERIFICATION,
    STATE_APPLIED,
    STATE_PROPOSED,
    STATE_REJECTED,
    STATE_VERIFIED,
    ConfigDeferral,
    ConfigLifecycle,
    ConfigTransition,
    SeatConfig,
    compose_prompt,
)
from embodiment.contract import Task, TaskResult
from embodiment.loop import CompleteFn, LoopAborted, LoopOutcome, ToolExecutor, run

__all__ = [
    # this module's own three records (C3)
    "RUN_DEGRADED_PROJECTOR",
    "RUN_DEGRADED_REVIEWER",
    "RUN_DEGRADED_HOST_SEAM",
    "RUN_CODES",
    # the d5 mapping
    "DEMOTION_CODE",
    "events_for_transition",
    "events_for_refusal",
    # shapes
    "ConfigContext",
    "ConfigProjectorFn",
    "ExecutorFactoryFn",
    "ConfigGovernor",
    "ConfiguredOutcome",
    # composition
    "run_configured",
]


# ── this module's own vocabulary (C3) ─────────────────────────────────────────
#
# Three, prefixed ``config-run-`` so a ledger reader can tell a composition
# fault from an admission refusal (``config-change-``), a review fault
# (``config-review-``), a lane fault (``config-runner-``) or a persistence
# fault (``config-ledger-``). One code per distinct FIX.

#: The host's projector raised, so no snapshot was built. The actor is
#: unaffected: a projector that cannot describe the rig costs a review, never a
#: drive.
RUN_DEGRADED_PROJECTOR = "config-run-projector-failed"
#: The reviewer lane raised on ``consider``, ``drain`` or ``degradation``, or
#: reported itself dead. The drive continues under the configuration it started
#: with.
RUN_DEGRADED_REVIEWER = "config-run-reviewer-failed"
#: A host seam this module composes — the executor factory, the ledger, the
#: lifecycle — raised where it was called. Named separately because the fix is
#: in the host's own code rather than in this tier.
RUN_DEGRADED_HOST_SEAM = "config-run-host-seam-failed"

#: The complete set this module mints. There is no fourth.
RUN_CODES = (
    RUN_DEGRADED_PROJECTOR,
    RUN_DEGRADED_REVIEWER,
    RUN_DEGRADED_HOST_SEAM,
)


# ── the d5 mapping ────────────────────────────────────────────────────────────

#: The marker that tells a ``config.change.proposed`` event apart from a first
#: proposal: it is the code ``t4``'s gate mints when it demotes a proposal whose
#: verification is stale. Aliased here rather than re-spelled, so the marker a
#: host branches on and the refusal the gate records can never drift apart.
DEMOTION_CODE = CHANGE_STALE_VERIFICATION

#: Cap on a recorded reason's text. The tier's value.
_MAX_REASON_LEN = 500


def _read(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` that cannot raise. A hostile object reads as absent."""
    try:
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001  # an unreadable attribute is simply absent
        return default


def _text(value: Any) -> str:
    """Best-effort ``str``; never raises, never returns ``None``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unstringable value renders as empty
        return ""


def _with_code(event: config_events.ConfigEvent, code: str) -> config_events.ConfigEvent:
    """Fill the envelope's already-declared ``code`` key on a built event.

    :mod:`embodiment.config_events` owns the envelope and ``code`` is one of its
    defaulted keys, so this fills a declared field rather than widening the
    shape — ``tests/test_config_run.py`` pins that the demoted and the first
    proposal carry the *same* key set.
    """
    return replace(event, data={**event.data, "code": code})


def events_for_transition(transition: Any) -> tuple[config_events.ConfigEvent, ...]:
    """Translate one :class:`~embodiment.config_lifecycle.ConfigTransition`.

    Pure, defensive and never raising — :mod:`embodiment.config_events`'
    discipline, which is in turn ``scope_events.py``'s. An unreadable or unknown
    transition translates to ``()`` rather than to a guessed kind: a fabricated
    event is worse than a missing one, because a host cannot tell it apart from
    a real one.

    The mapping, in full:

    ===============================  ==========================================
    ``to_state`` (and ``verdict``)   event(s)
    ===============================  ==========================================
    ``proposed``, from ``""``        ``proposed``
    ``proposed``, from ``verified``  ``proposed`` + :data:`DEMOTION_CODE` (d5)
    ``verified``                     ``verified`` (``passed=True``)
    ``applied``                      ``applied``
    ``rejected``, verdict ``failed`` ``verified`` (``passed=False``) +
                                     ``rejected``
    ``rejected``, any other verdict  ``rejected``
    anything else                    ``()``
    ===============================  ==========================================
    """
    to_state = _text(_read(transition, "to_state")).strip()
    from_state = _text(_read(transition, "from_state")).strip()
    verdict = _text(_read(transition, "verdict")).strip()

    if to_state == STATE_PROPOSED:
        event = config_events.proposed_event(transition)
        return (_with_code(event, DEMOTION_CODE) if from_state == STATE_VERIFIED else event,)
    if to_state == STATE_VERIFIED:
        return (config_events.verified_event(transition, passed=True, suite="configured"),)
    if to_state == STATE_APPLIED:
        return (config_events.applied_event(transition),)
    if to_state == STATE_REJECTED:
        rejected = config_events.rejected_event(transition)
        if verdict != "failed":
            return (rejected,)
        # A suite that RAN and reported failure completed a verification. Saying
        # only "rejected" would lose the fact that evidence exists, which is the
        # one thing separating a graded change from an unexamined one.
        return (
            config_events.verified_event(transition, passed=False, suite="configured"),
            rejected,
        )
    return ()


def events_for_refusal(refusal: Any) -> tuple[config_events.ConfigEvent, ...]:
    """Translate one recorded refusal or degradation onto the fault stream.

    Uniformly :data:`~embodiment.config_events.CONFIG_EVENT_DEGRADATION`: every
    record here carries a code naming its fix, and that is what the kind means.
    The state stream above is where "what happened to the change" lives, so a
    refusal is never emitted twice under two kinds.
    """
    if refusal is None:
        return ()
    return (config_events.degradation_event(refusal),)


# ── the projection seam ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigContext:
    """What the host's projector is handed at one boundary.

    Everything here is either the host's own (:attr:`task`), this module's honest
    observation of the acting loop (:attr:`calls_by_name`,
    :attr:`failures_by_name`, :attr:`step_count`), or what the lifecycle says is
    in force (:attr:`config`, :attr:`seat`). Nothing here is interpreted:
    turning any of it into observations, problems or a capability offer is the
    projector's job, because embodiment cannot infer which domain facts those
    are (issue #2's compose-don't-reimplement rule).
    """

    task: Task
    seat: str = ""
    config: SeatConfig = field(default_factory=SeatConfig)
    step_count: int = 0
    calls_by_name: dict[str, int] = field(default_factory=dict)
    failures_by_name: dict[str, int] = field(default_factory=dict)
    operator_messages: tuple[str, ...] = ()


#: The host's projector: a context in, a
#: :class:`~embodiment.config_review.ConfigSnapshot` (or ``None``) out.
#:
#: ``None`` means "nothing worth a review", and it is the normal answer. Typed
#: loosely on purpose — this module never constructs a snapshot and never reads
#: one, so it needs no import to describe it.
ConfigProjectorFn = Callable[[ConfigContext], Any]

#: The host's executor factory: the pinned configuration in, a
#: :class:`~embodiment.loop.ToolExecutor` out. See the module docstring on
#: ``h11`` — this is the only path from a capability selection to an actual tool
#: surface, and it is the host's code end to end.
ExecutorFactoryFn = Callable[[SeatConfig], ToolExecutor]


# ── the governor ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfigGovernor:
    """Everything the config lane needs, in one host-constructed object.

    Grouped rather than spread across :func:`run_configured`'s signature so the
    composition stays reviewable and lands well under the parameter limit.

    Args:
        lifecycle: the propose → verify → apply gate
            (:class:`~embodiment.config_lifecycle.ConfigLifecycle`). Without one
            no configuration is pinned and nothing can be applied.
        reviewer: the background lane — a
            :class:`~embodiment.config_runner.ConfigRunner`, or anything with
            its ``consider`` / ``drain`` / ``degradation`` surface. ``None``
            means no reviewer: the seat still runs under its configured prompt,
            and nothing proposes a change.
        projector: the host's :data:`ConfigProjectorFn`. Without one no snapshot
            is ever built — this module never infers what the rig means. It is
            called at every **boundary**, and a boundary is *one completed tool
            step, or the end of the drive* — never a model turn. A drive that
            answers in one turn without calling a tool therefore has exactly one
            boundary, its last. Say this out loud rather than leaving it to
            inference: when it was unstated and the drive-end boundary did not
            exist, a correct-looking wiring produced a tier that proposed nothing
            with every counter at zero (seam trap T1).
        ledger: the applied-change ledger
            (:class:`~embodiment.config_ledger.ConfigLedger`). Absent, applies
            are still recorded as transitions and events; what is lost is
            durability, and the record says so.
        seat: which seat the acting loop occupies. The three-tier design puts the
            worker there, which is why that is the default; a host running the
            cortex as its actor names it explicitly.
        executor_for: the host's :data:`ExecutorFactoryFn`, or ``None`` — see the
            module docstring on ``h11``.
    """

    lifecycle: Optional[Any] = None
    reviewer: Optional[Any] = None
    projector: Optional[ConfigProjectorFn] = None
    ledger: Optional[Any] = None
    seat: str = SEAT_WORKER
    executor_for: Optional[ExecutorFactoryFn] = None

    @property
    def armed(self) -> bool:
        """Whether the lane does anything at all. ``False`` ⇒ pure pass-through.

        A ledger alone does not arm it: with no lifecycle there is nothing to
        record, and arming would be a claim rather than a lane. Neither does an
        executor factory, whose only input is a configuration a lifecycle owns.
        """
        return self.lifecycle is not None or self.reviewer is not None


# ── the outcome ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConfiguredOutcome:
    """A config-governed drive: the actor's own outcome, plus what configuration did.

    :attr:`outcome` is the object :func:`embodiment.loop.run` returned — the same
    object, not a copy — so a host that only wants the drive reads it and gets
    exactly what an ungoverned drive would have produced.
    """

    outcome: LoopOutcome
    seat: str = ""
    run_id: str = ""
    config_sha: str = ""
    config: Optional[SeatConfig] = None
    """The configuration the seat was PINNED to for this drive — constant for its
    whole life, whatever landed afterwards."""
    transitions: tuple[ConfigTransition, ...] = ()
    degradations: tuple[ConfigDegradation, ...] = ()
    deferrals: tuple[ConfigDeferral, ...] = ()
    """The gate working: a seat was busy, so nothing was changed under it. Not on
    the event stream, by t4's deviation ``d4`` — see the module docstring."""
    applied: tuple[str, ...] = ()
    events: tuple[Any, ...] = ()
    """Every event offered to the host's observer, in order — kept so a host that
    wired no observer can still read what happened."""
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def result(self) -> TaskResult:
        return self.outcome.result

    @property
    def exit_reason(self) -> str:
        return self.outcome.exit_reason

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready record. The loop outcome is NOT folded in: it has its own."""
        return {
            "seat": self.seat,
            "run_id": self.run_id,
            "config_sha": self.config_sha,
            "config": self.config.to_dict() if self.config is not None else None,
            "transitions": [entry.to_dict() for entry in self.transitions],
            "degradations": [entry.to_dict() for entry in self.degradations],
            "deferrals": [entry.to_dict() for entry in self.deferrals],
            "applied": list(self.applied),
            "events": [
                {"kind": _read(entry, "kind", ""), "detail": _read(entry, "detail", "")}
                for entry in self.events
            ],
            "counts": dict(self.counts),
        }


# ── the public entry point ────────────────────────────────────────────────────


def run_configured(
    complete: CompleteFn,
    task: Task,
    *,
    executor: ToolExecutor,
    max_steps: int,
    governor: Optional[ConfigGovernor] = None,
    **actor_kwargs: Any,
) -> ConfiguredOutcome:
    """Drive :func:`embodiment.loop.run` under an optional configuration lane.

    Args:
        complete: the actor's model seam, exactly as ``run`` takes it. It is
            **never** wrapped — armed or not — because the acting seat is
            unaware of this tier by construction.
        task: the work item.
        executor: the tool surface, injected and forwarded untouched unless the
            host wired :attr:`ConfigGovernor.executor_for`. What the model may do
            stays entirely the host's: this layer holds no approval path and
            constructs no executor.
        max_steps: the model-turn budget. The config lane spends none of it: a
            review runs on its own thread and a change costs no turn at all.
        governor: the config lane (:class:`ConfigGovernor`). ``None``, or one
            with neither a lifecycle nor a reviewer, leaves the drive
            byte-identical to ``run`` — the host's own ``complete``,
            ``executor``, ``progress``, ``operator_inbox`` and ``system_prompt``
            are passed through by identity and nothing is recorded.
        **actor_kwargs: every other keyword ``run`` declares (``system_prompt``,
            ``hooks``, ``observer``, ``presence``, ``continuity``, ``subagent``,
            ``controls``, ``model``, …), forwarded verbatim. An unknown keyword
            is refused by ``run``'s own signature, which is what makes "no new
            ``run`` parameter" a structural fact rather than a promise.

    The unit of a **boundary**, because the whole tier's liveness depends on it
    and leaving it to inference cost a live session: the strategist is offered a
    projection at *each completed tool step, and once at the end of the drive*.
    It is **not** offered one per model turn. So strategist activity scales with
    tool use plus drives — never with conversation turns — and the honest health
    check is ``counts["boundaries_projected"]``, which is at least 1 for any
    drive that ran **with both a reviewer and a projector wired**.

    That precondition is not pedantry, and stating it loosely was caught in
    review: :attr:`ConfigGovernor.armed` is ``lifecycle is not None or reviewer
    is not None`` and never mentions the projector, so a lane can read ``armed``
    and still project nothing — with a lifecycle but no projector, or with a
    reviewer but no projector. Those are legitimate shapes rather than faults: a
    seat can be *configured* without being *reviewed*. A counter promising
    ``>= 1`` for them would be exactly the kind of overclaim T1 was.
    ``tests/test_three_tier.py`` pins both halves.

    Returns:
        A :class:`ConfiguredOutcome` carrying ``run``'s own outcome object plus
        the configuration record: what was pinned, what landed, what was refused.

    Raises:
        LoopAborted: whatever ``run`` raises, unchanged — the host's failure
            surfacing, never this layer's. The configuration record is attached
            to the exception as ``configured_outcome`` so it is not lost on the
            path where it is most interesting.
    """
    # Read, never popped: the actor's OWN LoopEvent stream still reaches this
    # same observer through ``run`` below, unaffected by config also using it.
    lane = _Configured(
        governor if governor is not None else ConfigGovernor(),
        task,
        observer=actor_kwargs.get("observer"),
    )
    lane.open()
    host_progress = actor_kwargs.pop("progress", None)
    host_inbox = actor_kwargs.pop("operator_inbox", None)
    host_prompt = actor_kwargs.pop("system_prompt", None)
    try:
        outcome = run(
            complete,
            task,
            executor=lane.executor(executor),
            max_steps=max_steps,
            system_prompt=lane.system_prompt(host_prompt),
            progress=lane.progress(host_progress),
            operator_inbox=lane.inbox(host_inbox),
            **actor_kwargs,
        )
    except LoopAborted as exc:
        exc.configured_outcome = lane.finish(exc.outcome)  # type: ignore[attr-defined]
        raise
    return lane.finish(outcome)


# ── the lane ──────────────────────────────────────────────────────────────────


class _Configured:
    """One drive's configuration state. Constructed per call, never reused.

    Every method that touches host, reviewer, lifecycle or ledger code is guarded
    and records a :class:`~embodiment.config_change.ConfigDegradation` on the way
    out. The guarantee is compositional: there is no outer ``try`` anywhere here,
    because each place that can fault carries its own.
    """

    def __init__(
        self,
        governor: ConfigGovernor,
        task: Task,
        *,
        observer: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self._gov = governor
        self._on = governor.armed
        self._task = task
        self._seat = _text(governor.seat).strip() or SEAT_WORKER
        self._life = governor.lifecycle
        self._reviewer = governor.reviewer
        self._ledger = governor.ledger
        self._run: Any = None
        self._config: Optional[SeatConfig] = None
        self._steps = 0
        self._calls: dict[str, int] = {}
        self._failures: dict[str, int] = {}
        self._operator: list[str] = []
        self._snapshot: Any = None
        self._applied: list[str] = []
        self._records: list[ConfigDegradation] = []
        self._events: list[Any] = []
        self._transitions: list[ConfigTransition] = []
        self._deferrals: list[ConfigDeferral] = []
        # Cursors into the lifecycle's and ledger's append-only streams, taken
        # BEFORE anything this drive does, so a lifecycle shared across drives
        # never re-emits an earlier drive's history.
        self._seen = {"transitions": 0, "degradations": 0, "deferrals": 0, "ledger": 0}
        self._observer = observer
        self._observer_failed = False
        self._counts = {
            "steps_observed": 0,
            "boundaries_projected": 0,
            "snapshots_offered": 0,
            "snapshots_unchanged": 0,
            "snapshots_absent": 0,
            "projector_failures": 0,
            "offers_failed": 0,
            "outcomes_drained": 0,
            "changes_proposed": 0,
            "changes_applied": 0,
            "ledger_writes": 0,
        }

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self) -> None:
        """Land what the gate allows, then pin the seat's configuration.

        The order is load-bearing and it is the whole per-seat invariant: nothing
        can be applied once :meth:`~embodiment.config_lifecycle.ConfigLifecycle.
        begin_run` has marked the seat busy, so everything that *can* land must
        land first.
        """
        if not self._on:
            return
        self._cursors()
        self._advance("before the drive opened, while the seat was idle")
        self._begin()
        self._collect()

    def _cursors(self) -> None:
        """Snap the read cursors to the current end of every host stream."""
        self._seen["transitions"] = len(self._stream("transitions"))
        self._seen["degradations"] = len(self._stream("degradations"))
        self._seen["deferrals"] = len(self._stream("deferrals"))
        self._seen["ledger"] = len(self._ledger_degradations())

    def _begin(self) -> None:
        """Open the run handle the seat is pinned to. Never raises."""
        if self._life is None:
            return
        try:
            self._run = self._life.begin_run(self._seat)
            self._config = _read(self._run, "config")
        except Exception as exc:  # noqa: BLE001  # a host gate never reaches the main path
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"the configuration lifecycle raised opening a run for the {self._seat} "
                f"seat ({type(exc).__name__}: {exc}); the drive continues unconfigured",
            )

    def finish(self, outcome: LoopOutcome) -> ConfiguredOutcome:
        """Close the run, land what the gate now allows, and fold the record.

        ``end_run`` is unconditional. A drive that aborted must not leave its
        seat marked busy: a seat this lifecycle still believes is working would
        hold its own configuration hostage forever, which is the one failure this
        gate can produce and must therefore never produce silently.

        The drive's END is a boundary, and this is where it is taken. It used to
        be that the ONLY boundaries were tool steps, because ``_offer`` was
        reached exclusively from ``_note_step`` — so a drive whose actor answered
        in one turn without calling a tool projected nothing, reviewed nothing
        and proposed nothing while ``armed`` read ``True`` and no degradation was
        recorded anywhere. That was seam trap T1, and a conversational host
        answers many turns exactly that way.

        Taken here, before ``end_run``, so the snapshot describes the drive that
        just ran. It cannot change THIS drive: the review is asynchronous and the
        gate below runs immediately, which is the point — configuration identity
        stays constant within a single drive, and turn N's projection configures
        turn N+1.
        """
        if self._on:
            self._offer()
            self._end()
            self._advance("after the drive closed and the seat went idle")
            self._drain()
            self._collect()
        return ConfiguredOutcome(
            outcome=outcome,
            seat=self._seat if self._on else "",
            run_id=_text(_read(self._run, "run_id", "")),
            config_sha=_text(_read(self._config, "config_sha", "")),
            config=self._config,
            transitions=tuple(self._transitions),
            degradations=tuple(self._records),
            deferrals=tuple(self._deferrals),
            applied=tuple(self._applied),
            events=tuple(self._events),
            counts=dict(self._counts),
        )

    def _end(self) -> None:
        """Close the run handle. Never raises."""
        if self._life is None or self._run is None:
            return
        try:
            self._life.end_run(self._run)
        except Exception as exc:  # noqa: BLE001  # a host gate never reaches the main path
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"the configuration lifecycle raised closing this drive's run "
                f"({type(exc).__name__}: {exc}); the seat may still read as busy",
            )

    def _advance(self, when: str) -> None:
        """Run one bounded gate pass and record what it applied. Never raises."""
        if self._life is None:
            return
        try:
            report = self._life.advance()
        except Exception as exc:  # noqa: BLE001  # a host gate never reaches the main path
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"the configuration gate raised {when} ({type(exc).__name__}: {exc}); "
                "no configuration changed and the drive is unaffected",
            )
            return
        for change_id in tuple(_read(report, "applied", ()) or ()):
            self._applied.append(_text(change_id))
            self._counts["changes_applied"] += 1
            self._record_applied(_text(change_id))

    def _record_applied(self, change_id: str) -> None:
        """Write one applied change to the host's ledger. Never raises.

        The event the ledger returns is deliberately unused — the transition
        stream is this composition's single source of state events, and emitting
        both would double-count the apply. What the ledger is called for is
        durability, and its own degradations are drained separately.
        """
        if self._ledger is None or self._life is None:
            return
        change = self._applied_unit(change_id)
        if change is None:
            return
        try:
            self._ledger.record_applied(change)
        except Exception as exc:  # noqa: BLE001  # a host store never reaches the main path
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"the applied-change ledger raised recording {change_id!r} "
                f"({type(exc).__name__}: {exc}); the change is in force and this drive "
                "lost its durable record of that, not the change itself",
            )
            return
        self._counts["ledger_writes"] += 1

    def _applied_unit(self, change_id: str) -> Optional[ConfigChange]:
        """The typed unit behind an applied change id, or ``None`` — **recorded**.

        Deliberately not a silent absent-read. The gate has just told this lane
        that *change_id* is in force; if the unit behind it cannot be recovered,
        the ledger loses a row for a change that really did move a seat's
        configuration, and t7's introspection would then meet a config state the
        ledger cannot explain — which C3 says is itself a degradation rather
        than a gap to fill from elsewhere.
        """
        try:
            proposal = self._life.proposal(change_id)
            change = getattr(proposal, "change", None)
        except Exception as exc:  # noqa: BLE001  # a host gate never reaches the main path
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"the configuration gate raised looking up applied change {change_id!r} "
                f"({type(exc).__name__}: {exc}); it is in force and unrecorded in the "
                "ledger",
            )
            return None
        if isinstance(change, ConfigChange):
            return change
        self._degrade(
            RUN_DEGRADED_HOST_SEAM,
            f"the configuration gate reported {change_id!r} applied but holds no typed "
            "change unit for it; it is in force and unrecorded in the ledger",
        )
        return None

    # ── the wrapped seams ────────────────────────────────────────────────────
    def executor(self, inner: ToolExecutor) -> ToolExecutor:
        """The host's tool surface, or the one the host's factory mints for it.

        Never this module's. With no factory wired the host's object is returned
        **by identity**, which is what keeps ``h11``'s "no configuration path
        grants an ability the host has not itself wired" structural.
        """
        factory = self._gov.executor_for
        if not self._on or factory is None or self._config is None:
            return inner
        try:
            built = factory(self._config)
        except Exception as exc:  # noqa: BLE001  # a host factory never reaches the main path
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"the host's executor factory raised ({type(exc).__name__}: {exc}); the "
                "drive runs on the executor the host passed in, unchanged",
            )
            return inner
        if built is None:
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                "the host's executor factory returned nothing for this seat's "
                "configuration; the drive runs on the executor the host passed in",
            )
            return inner
        return built

    def system_prompt(self, inner: Optional[str]) -> Optional[str]:
        """The seat's configured prompt, composed with whatever the host passed.

        Three cases, and the first is the one that matters for the identity pin:

        * the lane is off, or the seat's configuration composes to nothing — the
          host's own object is returned **by identity**, so a host that has not
          configured a prompt sees exactly what it passed;
        * the host passed nothing — the composed configuration is the prompt;
        * both — the host's text stays first and the configuration follows it.
          Nothing the host wrote is dropped: a host prompt is a baseline, and
          silently discarding it would be a change nobody proposed.
        """
        if not self._on or self._config is None:
            return inner
        composed = compose_prompt(self._config)
        if not composed:
            return inner
        host = _text(inner)
        # The host's text is forwarded BYTE-FOR-BYTE, not stripped. This module
        # documents that nothing the host wrote is dropped, and trailing
        # whitespace in a prompt is content a host may have chosen — a prompt
        # layer is not entitled to edit it. Only the emptiness *test* ignores
        # whitespace, so a prompt that is nothing but blanks still composes to
        # the configuration alone rather than to a leading void.
        return f"{host}\n\n{composed}" if host.strip() else composed

    def progress(self, inner: Any) -> Any:
        """Watch what the actor actually did. Observe-only, and never raises."""
        if not self._on:
            return inner

        def observed(step_index: Any, tool: Any, arguments: Any, ok: Any) -> None:
            self._note_step(step_index, tool, ok)
            if inner is not None:
                inner(step_index, tool, arguments, ok)

        return observed

    def inbox(self, inner: Any) -> Any:
        """Tee operator intent. The host's callable is still what ``run`` polls."""
        if not self._on or inner is None:
            return inner

        def observed() -> Any:
            pending = inner()
            self._note_operator(pending)
            return pending

        return observed

    # ── observation ──────────────────────────────────────────────────────────
    def _note_step(self, step_index: Any, tool: Any, ok: Any) -> None:
        """Fold one acting step, then offer a projection. Never raises."""
        try:
            name = _text(tool)
            if not name:
                return  # the loop's phase-notice sentinel, not a step
            self._steps = max(self._steps, int(step_index) + 1)
            self._calls[name] = self._calls.get(name, 0) + 1
            if not ok:
                self._failures[name] = self._failures.get(name, 0) + 1
            self._counts["steps_observed"] += 1
        except Exception as exc:  # noqa: BLE001  # a telemetry read never breaks a drive
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"an acting step could not be read ({type(exc).__name__}: {exc}); the "
                "observation was lost and the drive is unaffected",
            )
            return
        self._offer()
        self._operator.clear()

    def _note_operator(self, pending: Any) -> None:
        """Fold whatever the host's inbox returned. Never raises."""
        try:
            for message in pending or ():
                self._operator.append(_text(message))
        except Exception as exc:  # noqa: BLE001  # a telemetry read never breaks a drive
            self._degrade(
                RUN_DEGRADED_HOST_SEAM,
                f"operator intent could not be read ({type(exc).__name__}: {exc}); the "
                "observation was lost and the loop's own routing is unaffected",
            )

    def _offer(self) -> None:
        """Project the rig and offer it to the reviewer. Never raises."""
        if self._reviewer is None or self._gov.projector is None:
            return
        self._counts["boundaries_projected"] += 1
        try:
            snapshot = self._gov.projector(self._context())
        except Exception as exc:  # noqa: BLE001  # a host projector never reaches the main path
            self._counts["projector_failures"] += 1
            self._degrade(
                RUN_DEGRADED_PROJECTOR,
                f"the host's projector raised ({type(exc).__name__}: {exc}); no snapshot "
                "went up and the actor is unaffected",
            )
            return
        if snapshot is None:
            self._counts["snapshots_absent"] += 1
            return
        if self._same(snapshot):
            self._counts["snapshots_unchanged"] += 1
            return
        self._snapshot = snapshot
        try:
            self._reviewer.consider(snapshot, step_index=self._steps)
        except Exception as exc:  # noqa: BLE001  # a reviewer lane never reaches the main path
            self._counts["offers_failed"] += 1
            self._degrade(
                RUN_DEGRADED_REVIEWER,
                f"the reviewer lane raised being offered a snapshot "
                f"({type(exc).__name__}: {exc}); the drive continues unconfigured",
            )
            return
        self._counts["snapshots_offered"] += 1

    def _same(self, snapshot: Any) -> bool:
        """Whether *snapshot* equals the last one offered. An uncomparable one is new."""
        try:
            return bool(self._snapshot is not None and snapshot == self._snapshot)
        except Exception:  # noqa: BLE001  # an uncomparable snapshot is treated as new
            return False

    def _context(self) -> ConfigContext:
        """What the projector sees. Copies, so a host cannot mutate lane state."""
        return ConfigContext(
            task=self._task,
            seat=self._seat,
            config=self._config if self._config is not None else SeatConfig(seat=self._seat),
            step_count=self._steps,
            calls_by_name=dict(self._calls),
            failures_by_name=dict(self._failures),
            operator_messages=tuple(self._operator),
        )

    # ── draining the reviewer ────────────────────────────────────────────────
    def _drain(self) -> None:
        """Turn finished reviews into proposals. Never raises.

        Drained after the drive, not during it, and the reason is the same one
        that puts ``advance`` at the two ends: a proposal made mid-drive would be
        deferred at every gate pass until the seat went idle anyway, and a stream
        of deferrals for a lane behaving correctly is the noise ``d4`` refused.
        """
        if self._reviewer is None or self._life is None:
            return
        try:
            ready = self._reviewer.drain(step_count=self._steps)
        except Exception as exc:  # noqa: BLE001  # a reviewer lane never reaches the main path
            self._degrade(
                RUN_DEGRADED_REVIEWER,
                f"the reviewer lane raised being drained ({type(exc).__name__}: {exc}); "
                "whatever it had produced is lost and the drive is unaffected",
            )
            return
        for outcome in ready or ():
            self._counts["outcomes_drained"] += 1
            self._propose(outcome)
        self._note_lane()
        self._advance("after the reviewer's proposals were admitted")

    def _propose(self, outcome: Any) -> None:
        """Offer one review's units to the gate. Never raises."""
        for change in tuple(_read(outcome, "changes", ()) or ()):
            try:
                admitted = self._life.propose(change)
            except Exception as exc:  # noqa: BLE001  # a host gate never reaches the main path
                self._degrade(
                    RUN_DEGRADED_HOST_SEAM,
                    f"the configuration gate raised admitting a change "
                    f"({type(exc).__name__}: {exc}); the proposal was lost",
                )
                continue
            if admitted is not None:
                self._counts["changes_proposed"] += 1

    def _note_lane(self) -> None:
        """Relay the reviewer lane's own state. Never raises."""
        try:
            reason = self._reviewer.degradation()
        except Exception as exc:  # noqa: BLE001  # a reviewer lane never reaches the main path
            self._degrade(
                RUN_DEGRADED_REVIEWER,
                f"the reviewer lane raised being asked whether it was healthy "
                f"({type(exc).__name__}: {exc}); it is treated as dead",
            )
            return
        if reason:
            self._degrade(
                RUN_DEGRADED_REVIEWER,
                f"the reviewer lane stopped: {_text(reason)}; the drive continues under "
                "the configuration it started with",
            )

    # ── collecting what the host's own objects recorded ──────────────────────
    def _stream(self, name: str) -> tuple[Any, ...]:
        """One of the lifecycle's append-only streams. Never raises."""
        if self._life is None:
            return ()
        try:
            value = getattr(self._life, name)
        except Exception:  # noqa: BLE001  # an unreadable stream reads as empty
            return ()
        return tuple(value) if isinstance(value, (list, tuple)) else ()

    def _ledger_degradations(self) -> tuple[Any, ...]:
        """The ledger's own degradations. Never raises."""
        if self._ledger is None:
            return ()
        try:
            value = self._ledger.degradations
        except Exception:  # noqa: BLE001  # an unreadable ledger reads as empty
            return ()
        return tuple(value) if isinstance(value, (list, tuple)) else ()

    def _collect(self) -> None:
        """Read every stream forward from its cursor and emit what is new."""
        for transition in self._stream("transitions")[self._seen["transitions"] :]:
            self._transitions.append(transition)
            for event in events_for_transition(transition):
                self._notify(event)
        self._seen["transitions"] = len(self._stream("transitions"))

        for refusal in self._stream("degradations")[self._seen["degradations"] :]:
            self._records.append(refusal)
            for event in events_for_refusal(refusal):
                self._notify(event)
        self._seen["degradations"] = len(self._stream("degradations"))

        for deferral in self._stream("deferrals")[self._seen["deferrals"] :]:
            self._deferrals.append(deferral)
        self._seen["deferrals"] = len(self._stream("deferrals"))

        for entry in self._ledger_degradations()[self._seen["ledger"] :]:
            self._records.append(entry)
            for event in events_for_refusal(entry):
                self._notify(event)
        self._seen["ledger"] = len(self._ledger_degradations())

    # ── recording (C3) ───────────────────────────────────────────────────────
    def _degrade(self, code: str, reason: str) -> None:
        """Record one composition fault, and offer it to the host's observer."""
        entry = ConfigDegradation(
            code=code,
            reason=_text(reason)[:_MAX_REASON_LEN],
            step_index=self._steps,
            seat=self._seat,
        )
        self._records.append(entry)
        self._notify(config_events.degradation_event(entry, step_index=self._steps))

    def _notify(self, event: Optional[Any]) -> None:
        """Offer one translated event to the host's observer. Never raises (C3).

        Mirrors :mod:`embodiment.loop`'s own ``_observe``: a raising observer is
        disabled for the rest of the drive rather than retried at every
        occurrence. The event is kept on :attr:`ConfiguredOutcome.events` either
        way, so a host that lost its observer has not lost the record.
        """
        if event is None:
            return
        self._events.append(event)
        if self._observer is None or self._observer_failed:
            return
        try:
            self._observer(event)
        except Exception:  # noqa: BLE001  # an observer must never abort a drive
            self._observer_failed = True


#: Every dataclass this module declares, for a host that wants to walk them.
_SHAPES = (ConfigContext, ConfigGovernor, ConfiguredOutcome)

#: Re-exported for a host wiring the gate directly; named here so a reader of
#: this module can see the whole tier's entry points in one place.
_LIFECYCLE = ConfigLifecycle
