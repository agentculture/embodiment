"""embodiment.scope_events — translate scope activity into ObserverFn-shaped events.

Task t5, issue #51. Claims ``c9``/``c17``/``c35`` and honesty conditions
``h8``/``h12`` all say the same thing from different angles: the ``scope.*``
event kinds ride the **host-wired** ``ObserverFn`` — the exact seam
:mod:`embodiment.events`' ``EventEmitter`` already satisfies for the actor
loop's own ``LoopEvent`` stream. Embodiment produces its scope activity; it
does not own the external event fabric, and this module is the producer, not
a second fabric of its own.

Why a NEW module, and why it holds no state
--------------------------------------------
:mod:`embodiment.scope` and :mod:`embodiment.strategist_runner` must import no
event fabric at all (acceptance criterion 1) — the first ships pure shapes and
a bounded review loop, the second a thread; neither owns observability.
:mod:`embodiment.scoped_run` is where scope activity actually happens (a
directive applied, a review drained, a lane degraded), so that module is the
one call site that both sees the activity and holds the host's ``observer=``
keyword already. This module supplies the *translation* — pure functions,
``ScopeOutcome`` / ``ScopeTransition`` / ``ScopeSnapshot`` / ``ScopeReport`` /
``ScopeDegradation``-shaped object in, an :class:`ScopeEvent` out — so that
translation is unit-testable on its own, decoupled from the threading and
composition mechanics.

Every function here is a pure, defensive read: no I/O, no lock, no thread, and
— like every helper in :mod:`embodiment.scope` and
:mod:`embodiment.strategist_runner` — no attribute read that can raise.  A
translator fed a hostile or malformed object still returns a valid
:class:`ScopeEvent` rather than raising into whatever called it, because the
one caller that matters (:mod:`embodiment.scoped_run`) calls these functions
from inside the acting loop's own call stack: an exception here would be an
exception in the acting loop's main path, which constraint C3 forbids
absolutely.

``ScopeEvent`` is re-declared, not imported
--------------------------------------------
:data:`~embodiment.loop.ObserverFn` is ``Callable[[LoopEvent], None]``, but
:class:`~embodiment.events.EventEmitter.__call__` never checks
``isinstance(event, LoopEvent)`` — it only ever reads ``event.kind``,
``event.detail`` and ``event.data``. :class:`ScopeEvent` below is
field-for-field identical to :class:`~embodiment.loop.LoopEvent` for exactly
that reason: a host's *existing* ``EventEmitter`` (or any other
``ObserverFn``) accepts one with no adapter, no ``isinstance`` check to defeat
and no second wiring path to build. It is re-declared rather than imported so
this module needs no dependency on :mod:`embodiment.loop` at all — the same
"deliberately re-declared rather than imported" move :mod:`embodiment.loop`
itself makes for ``PresenceSink``, for the same reason: the two sides of a
seam should each be free to stay importable without pulling the other in.

No dependency on ``embodiment.scoped_run`` either — and why
--------------------------------------------------------------
:mod:`embodiment.scoped_run` imports this module to notify a host observer as
scope activity happens, so this module must not import
:mod:`embodiment.scoped_run` back (a cycle). The six ``ScopeTransition.kind``
literals it mints, and the two lane-level ``strategist_runner`` codes this
module dispatches on (``DROPPED_STALE`` / ``DROPPED_SUPERSEDED``), are
therefore CITED as private string constants below rather than imported — the
same citation discipline :mod:`embodiment.strategist_runner` already uses for
``embodiment/muse_runner.py``'s mechanics. ``tests/test_scope_events.py``
pins every literal against the real constant it mirrors, so a rename on either
side fails a test rather than silently misrouting an event.

Live versus batched delivery — stated, not hidden
----------------------------------------------------
Most ``scope.*`` events fire THE MOMENT :mod:`embodiment.scoped_run` observes
the activity — a snapshot offered, a review drained, a directive applied or
withheld — because :func:`~embodiment.scoped_run.run_scoped` is still on the
stack at every one of those points. One category is not live: the strategist
LANE's own relayed ledger
(:attr:`~embodiment.scoped_run.ScopedOutcome.scope_degradations`, sourced from
:attr:`~embodiment.strategist_runner.StrategistRunner.degradations`) is only
ever read once, at the end of a drive — that mirrors exactly where
:mod:`embodiment.scoped_run` itself already reads it. So ``scope.directive.stale``
and ``scope.directive.superseded`` (and any lane-level ``scope.degradation``)
arrive at drive-end, batched, not mid-drive. Nothing about this is silent: it is
what it is because live per-boundary streaming of the lane's OWN ledger is not
implemented in v1, not because the information does not exist yet.

The actual model and role, never the register (embodiment#54)
-------------------------------------------------------------------
:class:`~embodiment.scope.ScopeDegradation` — the shape the strategist lane's
own ledger holds — carries no ``model``/``role`` field at all (it is
deliberately field-for-field identical to
:class:`~embodiment.loop.LoopDegradation`, so :mod:`embodiment.ledger`'s
harvest folds one shape rather than three). A lane-level entry therefore
cannot name its own contributing strategist by itself. But a lane's
``model``/``role`` are host-declared ONCE, at
:class:`~embodiment.strategist_runner.StrategistRunner` construction, and
stay constant for the lane's whole life — so :func:`for_lane_degradation`
takes them as caller-supplied parameters, and :mod:`embodiment.scoped_run`
reads them once (:func:`strategist_identity`) and passes them to every
lane-level call. This is never the register: every event this module builds
is a translation of what :mod:`embodiment.scoped_run` (via ``drain()``) or
:class:`~embodiment.strategist_runner.StrategistRunner` (a lane-lifetime
constant) actually saw, never of
:attr:`~embodiment.strategist_runner.StrategistRunner.active_directive`.

With no strategist configured, :mod:`embodiment.scoped_run` never calls any
lane-facing builder here at all — :func:`strategist_identity` is only ever
consulted with a real strategist object, and every event this module can
still produce (the host default scope's ``scope.directive.applied``) carries
empty ``model``/``role`` by construction. That is the single-model,
absent-identity, byte-identical rule (colleague#352) extended to this tier:
nothing here can name a strategist that was never configured.

Stdlib only (constraint C1): ``dataclasses``, ``typing``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "SCOPE_EVENT_SNAPSHOT",
    "SCOPE_EVENT_REVIEW_STARTED",
    "SCOPE_EVENT_REVIEW_COMPLETED",
    "SCOPE_EVENT_DIRECTIVE_PROPOSED",
    "SCOPE_EVENT_DIRECTIVE_APPLIED",
    "SCOPE_EVENT_DIRECTIVE_REJECTED",
    "SCOPE_EVENT_DIRECTIVE_STALE",
    "SCOPE_EVENT_DIRECTIVE_SUPERSEDED",
    "SCOPE_EVENT_REPORT",
    "SCOPE_EVENT_DEGRADATION",
    "SCOPE_EVENT_KINDS",
    "ScopeEvent",
    "strategist_identity",
    "snapshot_event",
    "review_started_event",
    "review_completed_event",
    "directive_proposed_event",
    "report_event",
    "for_transition",
    "for_lane_degradation",
]

# ── the vocabulary (issue #51) ─────────────────────────────────────────────────

SCOPE_EVENT_SNAPSHOT = "scope.snapshot"
SCOPE_EVENT_REVIEW_STARTED = "scope.review.started"
SCOPE_EVENT_REVIEW_COMPLETED = "scope.review.completed"
SCOPE_EVENT_DIRECTIVE_PROPOSED = "scope.directive.proposed"
SCOPE_EVENT_DIRECTIVE_APPLIED = "scope.directive.applied"
SCOPE_EVENT_DIRECTIVE_REJECTED = "scope.directive.rejected"
SCOPE_EVENT_DIRECTIVE_STALE = "scope.directive.stale"
SCOPE_EVENT_DIRECTIVE_SUPERSEDED = "scope.directive.superseded"
SCOPE_EVENT_REPORT = "scope.report"
SCOPE_EVENT_DEGRADATION = "scope.degradation"

#: The complete set. A host's ``EventEmitter`` maps each onto
#: ``f"embodiment.{kind}"`` exactly as it does for the actor loop's own kinds.
SCOPE_EVENT_KINDS = (
    SCOPE_EVENT_SNAPSHOT,
    SCOPE_EVENT_REVIEW_STARTED,
    SCOPE_EVENT_REVIEW_COMPLETED,
    SCOPE_EVENT_DIRECTIVE_PROPOSED,
    SCOPE_EVENT_DIRECTIVE_APPLIED,
    SCOPE_EVENT_DIRECTIVE_REJECTED,
    SCOPE_EVENT_DIRECTIVE_STALE,
    SCOPE_EVENT_DIRECTIVE_SUPERSEDED,
    SCOPE_EVENT_REPORT,
    SCOPE_EVENT_DEGRADATION,
)

# ── cited literals (see the module docstring: not imported, to avoid a cycle) ──

#: Mirrors ``embodiment.scoped_run.TRANSITION_DEFAULT``.
_TRANSITION_KIND_DEFAULT = "scope-default-applied"
#: Mirrors ``embodiment.scoped_run.TRANSITION_APPLIED``.
_TRANSITION_KIND_APPLIED = "scope-directive-applied"
#: Mirrors ``embodiment.scoped_run.TRANSITION_HELD``.
_TRANSITION_KIND_HELD = "scope-held"
#: Mirrors ``embodiment.scoped_run.TRANSITION_WITHHELD``.
_TRANSITION_KIND_WITHHELD = "scope-withheld"
#: Mirrors ``embodiment.scoped_run.TRANSITION_DEGRADED``.
_TRANSITION_KIND_DEGRADED = "scope-lane-degraded"
#: Mirrors ``embodiment.scoped_run.TRANSITION_UNPROJECTED``.
_TRANSITION_KIND_UNPROJECTED = "scope-unprojected"

#: Mirrors ``embodiment.strategist_runner.DROPPED_STALE``.
_LANE_CODE_STALE = "strategist-review-stale"
#: Mirrors ``embodiment.strategist_runner.DROPPED_SUPERSEDED``.
_LANE_CODE_SUPERSEDED = "strategist-directive-superseded"


@dataclass(frozen=True)
class ScopeEvent:
    """One scope-lane occurrence, offered to the injected observer.

    Field-for-field identical to :class:`~embodiment.loop.LoopEvent` (see the
    module docstring) so a host's existing ``ObserverFn`` — an
    :class:`~embodiment.events.EventEmitter` included — accepts one with no
    adapter. ``kind`` is always one of :data:`SCOPE_EVENT_KINDS`; branch on it,
    never on ``detail``'s free text.
    """

    kind: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ── defensive reads (never raise, mirroring scope.py / strategist_runner.py) ──


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


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Best-effort ``int``; anything uncoercible falls back to *default*."""
    try:
        return int(value)
    except Exception:  # noqa: BLE001  # a junk number is the default, never a crash
        return default


def strategist_identity(strategist: Any) -> tuple[str, str]:
    """The lane's own ``(role, model)`` — read defensively, empty when absent.

    Role and model are host-declared once, at
    :class:`~embodiment.strategist_runner.StrategistRunner` construction, and
    constant for the lane's whole life. Reading them here is what lets a
    LANE-level record — one with no single review to draw its own identity
    from, such as a stopped lane or a ``scope_degradations`` ledger entry —
    still name the actual contributing strategist instead of leaving every
    such record silently anonymous. ``strategist is None`` (no strategist
    configured at all) reads as ``("", "")``, which is exactly what keeps a
    single-model run from ever naming one.
    """
    if strategist is None:
        return "", ""
    return _text(_read(strategist, "role")), _text(_read(strategist, "model"))


#: :func:`_build`'s envelope, defaulted. Keeps that function's signature to
#: ``(kind, **fields)`` — two named parameters plus a variadic catch-all,
#: mirroring :meth:`embodiment.scoped_run._Governed._record`'s own
#: ``(kind, **stamp)`` shape — rather than the thirteen-plus named keywords
#: this envelope's field count would otherwise need (``python:S107``; see
#: ``docs/sonar-dispositions.md``'s note on the same pressure in
#: ``ThreadedMuseRunner.__init__``, and ``StrategistLimits``' answer to it).
_ENVELOPE_DEFAULTS: dict[str, Any] = {
    "model": "",
    "role": "",
    "lane": "",
    "scope_id": "",
    "snapshot_id": "",
    "version": None,
    "previous_version": None,
    "supersedes": None,
    "turn_index": 0,
    "step_index": 0,
    "applied": False,
    "reason": "",
    "tokens": None,
    "latency": None,
}


def _build(kind: str, *, detail: str = "", **fields: Any) -> ScopeEvent:
    """The one place a :class:`ScopeEvent` is constructed.

    Every key in :data:`_ENVELOPE_DEFAULTS` is present on EVERY event this
    module builds, honestly ``None``/``""``/``False`` where a given
    occurrence has nothing to say — the same idiom
    :class:`~embodiment.scope.ScopeOutcome` already uses for
    ``tokens``/``latency``: a key that is always there, a value that is
    sometimes an honest "unknown" rather than a fabricated zero. *fields*
    overrides the defaults; an unrecognised key is still carried (this is a
    private helper with every call site in this same module, all covered by
    ``tests/test_scope_events.py``'s envelope-key tests).
    """
    data: dict[str, Any] = dict(_ENVELOPE_DEFAULTS)
    data.update(fields)
    data["model"] = _text(data["model"])
    data["role"] = _text(data["role"])
    data["lane"] = _text(data["lane"])
    data["scope_id"] = _text(data["scope_id"])
    data["snapshot_id"] = _text(data["snapshot_id"])
    data["turn_index"] = _int(data["turn_index"], 0)
    data["step_index"] = _int(data["step_index"], 0)
    data["applied"] = bool(data["applied"])
    data["reason"] = _text(data["reason"])
    return ScopeEvent(kind=kind, detail=_text(detail) or data["reason"], data=data)


# ── builders: the actor-observation side (snapshot, report, review lifecycle) ──


def snapshot_event(
    snapshot: Any, *, turn_index: int = 0, step_count: int = 0, lane: str = ""
) -> ScopeEvent:
    """A :class:`~embodiment.scope.ScopeSnapshot` was offered for strategic review.

    Carries no ``model``/``role``: a snapshot is the composition layer's own
    observation of the actor, built and offered before any strategist reads it.

    *lane* is the persistence lane the offering drive runs in (task t13). It is
    the CALLER's to supply for the same reason ``model``/``role`` are on the
    lane-facing builders: a snapshot has no lane of its own, the drive that
    offered it does, and a drive has exactly one.
    """
    snapshot_id = _text(_read(snapshot, "snapshot_id"))
    return _build(
        SCOPE_EVENT_SNAPSHOT,
        lane=lane,
        scope_id=_text(_read(snapshot, "current_directive")),
        snapshot_id=snapshot_id,
        turn_index=turn_index,
        step_index=step_count,
        reason=f"scope snapshot {snapshot_id!r} offered for strategic review",
    )


def review_started_event(
    snapshot: Any, *, step_index: int = 0, model: str = "", role: str = "", lane: str = ""
) -> ScopeEvent:
    """One snapshot was successfully handed to the strategist lane for review.

    Fires the moment ``consider()`` accepts the snapshot without raising — the
    honest proxy this layer has for "a review begins", since the lane may
    still queue or displace it (recorded separately, on the lane's own
    ledger) before a worker thread actually starts reasoning about it.
    """
    snapshot_id = _text(_read(snapshot, "snapshot_id"))
    return _build(
        SCOPE_EVENT_REVIEW_STARTED,
        model=model,
        role=role,
        lane=lane,
        scope_id=_text(_read(snapshot, "current_directive")),
        snapshot_id=snapshot_id,
        step_index=step_index,
        reason=f"a strategic review of snapshot {snapshot_id!r} was offered to the lane",
    )


def review_completed_event(outcome: Any, *, lane: str = "") -> ScopeEvent:
    """A :class:`~embodiment.scope.ScopeOutcome` finished — drained, not yet applied.

    Fires for every drained outcome regardless of what
    :mod:`embodiment.scoped_run` goes on to do with it: a hold, an applied
    directive and a withheld one all completed a review. ``model``/``role``,
    ``tokens`` and ``latency`` come straight off the outcome, which is the
    ONE place they are ever knowingly measured.
    """
    directive = _read(outcome, "directive")
    exit_reason = _text(_read(outcome, "exit_reason"))
    return _build(
        SCOPE_EVENT_REVIEW_COMPLETED,
        model=_text(_read(outcome, "model")),
        role=_text(_read(outcome, "role")),
        lane=lane,
        scope_id=_text(_read(directive, "scope_id")) if directive is not None else "",
        snapshot_id=_text(_read(outcome, "snapshot_id")),
        version=_int(_read(directive, "version")) if directive is not None else None,
        supersedes=_read(directive, "supersedes") if directive is not None else None,
        step_index=_int(_read(outcome, "step_index"), 0) or 0,
        reason=f"review exited {exit_reason!r}",
        tokens=_read(outcome, "tokens"),
        latency=_read(outcome, "latency"),
    )


def directive_proposed_event(outcome: Any, *, lane: str = "") -> Optional[ScopeEvent]:
    """The strategist proposed a directive — ``None`` when the outcome has none.

    A hold (``outcome.directive is None``) proposes nothing; that is what
    :func:`review_completed_event` already reported.
    """
    directive = _read(outcome, "directive")
    if directive is None:
        return None
    return _build(
        SCOPE_EVENT_DIRECTIVE_PROPOSED,
        model=_text(_read(outcome, "model")),
        role=_text(_read(outcome, "role")),
        lane=lane,
        scope_id=_text(_read(directive, "scope_id")),
        snapshot_id=_text(_read(outcome, "snapshot_id")),
        version=_int(_read(directive, "version")),
        supersedes=_read(directive, "supersedes"),
        step_index=_int(_read(outcome, "step_index"), 0) or 0,
        reason=(
            _text(_read(directive, "decision_summary")) or "the strategist proposed a directive"
        ),
        tokens=_read(outcome, "tokens"),
        latency=_read(outcome, "latency"),
    )


def report_event(
    report: Any, *, turn_index: int = 0, step_count: int = 0, lane: str = ""
) -> ScopeEvent:
    """A :class:`~embodiment.scope.ScopeReport` was built and judged material.

    Carries no ``model``/``role``: a report is the composition layer's own
    honest observation of the acting loop, never a strategist's output.
    """
    status = _text(_read(report, "status")) or "active"
    return _build(
        SCOPE_EVENT_REPORT,
        lane=lane,
        scope_id=_text(_read(report, "scope_id")),
        turn_index=turn_index,
        step_index=step_count,
        reason=f"status={status}",
        detail=f"scope report: status={status}",
    )


# ── dispatchers: the composition-layer decision, and the lane's own ledger ────


def for_transition(transition: Any) -> Optional[ScopeEvent]:
    """Translate one ``embodiment.scoped_run.ScopeTransition``-shaped object.

    ``None`` for a HELD transition only: a hold is already reported at the
    review level by :func:`review_completed_event`, and repeating it here
    would be the same fact wearing a second hat. Every other kind — including
    one this function does not recognise — is reported, never dropped.
    """
    kind = _text(_read(transition, "kind"))
    scope_id = _text(_read(transition, "scope_id"))
    snapshot_id = _text(_read(transition, "snapshot_id"))
    supersedes = _read(transition, "supersedes")
    role = _text(_read(transition, "role"))
    model = _text(_read(transition, "model"))
    turn_index = _int(_read(transition, "turn_index"), 0) or 0
    step_index = _int(_read(transition, "step_count"), 0) or 0
    reason = _text(_read(transition, "reason"))
    version = _int(_read(transition, "version"))
    # The lane is the transition's OWN (task t13): a record already names the
    # persistence lane it belongs to, so nothing here has to be told.
    lane = _text(_read(transition, "lane"))

    if kind in (_TRANSITION_KIND_DEFAULT, _TRANSITION_KIND_APPLIED):
        return _build(
            SCOPE_EVENT_DIRECTIVE_APPLIED,
            model=model,
            role=role,
            lane=lane,
            scope_id=scope_id,
            snapshot_id=snapshot_id,
            version=version,
            previous_version=_int(_read(transition, "previous_version")),
            supersedes=supersedes,
            turn_index=turn_index,
            step_index=step_index,
            applied=True,
            reason=reason,
        )
    if kind == _TRANSITION_KIND_HELD:
        return None
    if kind == _TRANSITION_KIND_WITHHELD:
        return _build(
            SCOPE_EVENT_DIRECTIVE_REJECTED,
            model=model,
            role=role,
            lane=lane,
            scope_id=scope_id,
            snapshot_id=snapshot_id,
            version=version,
            supersedes=supersedes,
            turn_index=turn_index,
            step_index=step_index,
            applied=False,
            reason=reason,
        )
    # TRANSITION_DEGRADED, TRANSITION_UNPROJECTED, or an unrecognised kind:
    # reported as a degradation rather than silently dropped.
    return _build(
        SCOPE_EVENT_DEGRADATION,
        model=model,
        role=role,
        lane=lane,
        scope_id=scope_id,
        snapshot_id=snapshot_id,
        turn_index=turn_index,
        step_index=step_index,
        applied=False,
        reason=reason,
    )


def for_lane_degradation(
    entry: Any, *, model: str = "", role: str = "", lane: str = ""
) -> ScopeEvent:
    """Translate one relayed strategist-lane ledger entry (a ``ScopeDegradation``
    or ``ScopeRejection``, read duck-typed — this module imports neither type).

    *model* / *role* are the CALLER's job to supply (:func:`strategist_identity`
    off the lane that produced them) because the ledger entry itself never
    carries them — see the module docstring's "the actual model and role,
    never the register" section.

    *lane* is a **fallback**, and the entry's own ``lane`` wins whenever it has
    one (task t13). A :class:`~embodiment.scope.ScopeDegradation` minted against
    a register names its lane; one that reached this ledger through
    :meth:`~embodiment.strategist_runner.StrategistRunner._absorb` was re-minted
    on the way and lost it — the same pre-existing relay narrowing that already
    blanks a rejection's ``scope_id``. In that case the lane of the drive
    relaying the entry is what the caller supplies, which is honest because a
    :class:`~embodiment.scoped_run.ScopedOutcome` is a per-drive artifact and a
    drive runs in exactly one lane.
    """
    code = _text(_read(entry, "code"))
    reason = _text(_read(entry, "reason"))
    step_index = _int(_read(entry, "step_index"), 0) or 0
    # ``ScopeRejection`` is the only shape on this ledger with a ``scope_id``
    # field; a plain ``ScopeDegradation`` has none, so ``_read`` defaults to
    # ``None`` rather than an empty string. That distinction is the whole
    # discriminator, and it needs no import of either type to use.
    scope_id = _read(entry, "scope_id", None)
    if code == _LANE_CODE_STALE:
        kind = SCOPE_EVENT_DIRECTIVE_STALE
    elif code == _LANE_CODE_SUPERSEDED:
        kind = SCOPE_EVENT_DIRECTIVE_SUPERSEDED
    elif scope_id is not None:
        kind = SCOPE_EVENT_DIRECTIVE_REJECTED
    else:
        kind = SCOPE_EVENT_DEGRADATION
    return _build(
        kind,
        model=model,
        role=role,
        lane=_text(_read(entry, "lane", "")) or lane,
        scope_id=_text(scope_id) if scope_id is not None else "",
        version=_int(_read(entry, "version")),
        supersedes=_read(entry, "supersedes"),
        step_index=step_index,
        applied=False,
        reason=reason or code,
    )
