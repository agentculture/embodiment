"""ONE host-visible degradation stream, folded from every lane (task t9).

Constraint **C3** says every degradation records a host-visible transition and
nothing degrades silently. Seven lanes each hold that promise on their own, in
their own vocabulary and their own record shape::

    LoopOutcome.degradations       -> loop.LoopDegradation        (DEGRADED_*)
    MuseOutcome.degradations       -> muse.MuseDegradation        (DEGRADED_*)
    ThreadedMuseRunner.degradations-> muse.MuseDegradation        (DEGRADED_*/DROPPED_*)
    EventEmitter.degradations      -> events.EventDegradation     (DEGRADED_*)
    RecallOutcome.degradation      -> continuity.Degradation      (CODE_*)
    ContinuityLifecycle.events     -> lifecycle.LifecycleEvent    (kind "degraded")
    SpawnRecord.degradations       -> (child's own records)        (child's lane)

So a host that wants to answer *"what went wrong?"* has to know seven
vocabularies, seven containers and six field layouts. That is C3 satisfied
per-lane and defeated in aggregate. This module is the fold: one shape, one
stream, one question.

Child attribution
-----------------
A subagent's degradations ride back on
:class:`~embodiment.subagent.SubagentResult` and are folded into the parent's
ledger wearing the **subagent lane** (:data:`SOURCE_SUBAGENT`) and the child's
own task id in :attr:`LedgerRecord.child_task_id`. A degradation the child's own
loop recorded keeps its minting lane in :attr:`LedgerRecord.source` (a loop code
is a loop code) while carrying the child attribution. This mirrors how
:attr:`_RELEVANT` already lets a relaying lane resolve another lane's codes: the
subagent lane is the relay, the child's own lane is the mint. A host reading the
stream can answer "who degraded?" without heuristics and without parsing reason
text — the child task id is a first-class field, never ``None`` for records that
came from a child.

A reader, not a refactor
------------------------
Nothing here changes how a lane records. ``hook_firings`` and ``degradations``
ride :class:`~embodiment.loop.LoopOutcome` rather than
:class:`~embodiment.contract.TaskResult` **on purpose** — they are the loop's
record of its own conduct, not part of the artifact shape every seam shares —
and the muse's shape is deliberately field-for-field identical to the loop's so
the two already align. Rewriting six green, well-tested modules onto one type
would fight both decisions. This module reads them instead, and the lanes stay
free to record whatever they must.

Never fabricates an absent field
--------------------------------
:class:`LedgerRecord` is the union of what the six shapes carry, and every field
a source does **not** carry stays ``None`` — it is never defaulted to ``0`` or
``""``. :class:`embodiment.continuity.Degradation` has no step index, so a
continuity-sourced record's ``step_index`` is ``None``, and :meth:`to_dict`
omits the key entirely rather than emitting a zero a host could mistake for
"step 0". This is task t7's refusal to stamp ``0.0`` for a missing clock, and
task t10a's reading of a ``0 + 0`` token pair as *unreported*, applied to the
ledger: absent means absent. A genuine zero is different and is preserved — the
loop stamps a real ``step_index`` on every record it mints, so ``0`` there means
step zero and is folded through unchanged.

Attribution is looked up, never guessed
---------------------------------------
Two lanes legitimately relay another lane's records: a
:class:`~embodiment.muse_runner.ThreadedMuseRunner` absorbs a thinking session's
own codes verbatim, and a :class:`~embodiment.lifecycle.ContinuityLifecycle`
re-emits a continuity ``CODE_*`` token as a checkpoint event. So
:attr:`LedgerRecord.source` is resolved from the **code**, against each module's
own exported vocabulary (:func:`known_codes`), falling back to the container's
own lane when a code is not in any vocabulary. Nothing is inferred from a class
name, a field layout or a string prefix.

No invented chronology
----------------------
Records are grouped by lane, each lane in its own recorded order. They are
**not** interleaved into one timeline: no lane stamps a wall-clock time on a
degradation (the pump has no clock and the loop counts steps, not seconds), so
a merged chronology would be invented rather than observed. A host that wants
one supplies its own clock and stamps records as it reads them.

Degradation is not incompletion
-------------------------------
A drive that exits on :data:`~embodiment.loop.EXIT_BUDGET` produces **no**
ledger record, and that is correct: a budget exit is an honest partial the loop
already reports through ``LoopOutcome.exit_reason`` and
``TaskResult.incompletion``, not something that broke. Folding it in here would
make the stream claim breakage that did not happen — the same overclaim, in the
opposite direction, that C3 exists to prevent. A budget exit whose forced final
synthesis *also* failed does record one, under
:data:`~embodiment.loop.DEGRADED_SYNTHESIS`.

…and neither is delivery
------------------------
The same rule, applied to a surface that arrived later. The muse's terminal
drain records what it handed the actor — a count, the delivered insight ids,
zero included — as a
:class:`~embodiment.muse_runner.MuseDelivery` on
:attr:`~embodiment.muse_runner.ThreadedMuseRunner.deliveries`. That record does
**not** fold through here, and its vocabulary
(:data:`~embodiment.muse_runner.DELIVERY_POINTS`) is deliberately outside the
``DEGRADED_`` / ``DROPPED_`` prefixes :data:`_MODULES` harvests, so it cannot
arrive by accident either. A terminal drain delivering three insights is that
lane working; delivering none is the muse having had nothing left. Reporting
either in a stream a host reads to answer *"what went wrong?"* would make every
healthy run look broken — and a stream that cries wolf on success gets ignored,
which costs exactly the C3 visibility it was built for. Counsel that genuinely
was lost keeps its codes (``muse-insight-stale`` / ``-late`` / ``-overflow``),
and they still fire on that path.

Never raises into the host
--------------------------
This is an observability surface; it must not become a new failure source. A
container this module cannot read, or a record whose attributes explode, yields
a :data:`DEGRADED_UNREADABLE_SOURCE` record naming what could not be read —
never an exception, and never a silently shorter list.

Stdlib only, and cheap: no ``embodiment`` submodule is imported at module scope,
so ``import embodiment.ledger`` costs this module alone. Each reader lazily
imports only the lanes whose codes can legitimately appear in what it was
handed; :func:`known_codes` is the one call that reaches all of them.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    # the lanes
    "SOURCE_LOOP",
    "SOURCE_MUSE",
    "SOURCE_MUSE_RUNNER",
    "SOURCE_EVENTS",
    "SOURCE_CONTINUITY",
    "SOURCE_LIFECYCLE",
    "SOURCE_SCOPE",
    "SOURCE_LEDGER",
    "SOURCE_SUBAGENT",
    "SOURCES",
    # this module's own vocabulary (C3 applies to the ledger too)
    "DEGRADED_UNREADABLE_SOURCE",
    # shapes
    "LedgerRecord",
    "CodeEntry",
    # the vocabulary registry
    "known_codes",
    "source_for_code",
    # readers
    "from_loop",
    "from_muse",
    "from_muse_runner",
    "from_events",
    "from_continuity",
    "from_lifecycle",
    "from_scope",
    "from_subagent",
    "read",
]


# ── the lanes ─────────────────────────────────────────────────────────────────

#: The bounded tool loop (:mod:`embodiment.loop`).
SOURCE_LOOP = "loop"
#: One thinking session (:mod:`embodiment.muse`).
SOURCE_MUSE = "muse"
#: The thinking lane's thread (:mod:`embodiment.muse_runner`).
SOURCE_MUSE_RUNNER = "muse_runner"
#: Event emission (:mod:`embodiment.events`).
SOURCE_EVENTS = "events"
#: The memory/coherence seam (:mod:`embodiment.continuity`).
SOURCE_CONTINUITY = "continuity"
#: The lived sequence's checkpoints (:mod:`embodiment.lifecycle`).
SOURCE_LIFECYCLE = "lifecycle"
#: Strategic scope governance: one review (:mod:`embodiment.scope`) and the
#: thread that runs reviews beside the acting loop
#: (:mod:`embodiment.strategist_runner`), folded as ONE lane because the
#: runner re-exports every code the review loop mints (task t3).
SOURCE_SCOPE = "scope"
#: This module. A ledger that cannot read a source says so, in its own stream.
SOURCE_LEDGER = "ledger"
#: A child drive's degradations, carried back on :class:`~embodiment.subagent.SubagentResult`.
#: The child's own lane is preserved in :attr:`LedgerRecord.source`; this lane is
#: the relay, and :attr:`LedgerRecord.child_task_id` names the child.
#: Not in :data:`SOURCES` — it has no codes of its own (it relays the child's).
SOURCE_SUBAGENT = "subagent"

#: Every lane this module folds, in the order :func:`read` emits them.
SOURCES = (
    SOURCE_LOOP,
    SOURCE_MUSE,
    SOURCE_MUSE_RUNNER,
    SOURCE_EVENTS,
    SOURCE_CONTINUITY,
    SOURCE_LIFECYCLE,
    SOURCE_SCOPE,
    SOURCE_LEDGER,
)


# ── this module's own degradation vocabulary (C3) ─────────────────────────────

#: A container, or one record inside it, could not be read. The reason names
#: what was handed over and what went wrong; nothing is dropped silently and
#: nothing is raised.
DEGRADED_UNREADABLE_SOURCE = "ledger-source-unreadable"


# Cap on one record's reason text, mirroring every sibling lane's own cap.
_MAX_REASON_LEN = 500

# source -> (module, constant-name prefixes, read from ``__all__`` only).
#
# The vocabularies are READ from each module rather than transcribed here, so a
# new degradation code joins this registry the moment its constant is defined —
# which is what makes ``tests/test_ledger.py``'s enumeration exhaustive by
# construction rather than a checklist that rots. ``lifecycle`` is the one lane
# read from the whole module namespace: its own fault tokens are private
# ``_FAULT_*`` constants (documented on ``CHECKPOINT_DEGRADED``), and the
# alternative — copying their four values into this file — is the transcription
# this design exists to avoid.
_MODULES: dict[str, tuple[str, tuple[str, ...], bool]] = {
    SOURCE_LOOP: ("embodiment.loop", ("DEGRADED_",), True),
    SOURCE_MUSE: ("embodiment.muse", ("DEGRADED_",), True),
    SOURCE_MUSE_RUNNER: ("embodiment.muse_runner", ("DEGRADED_", "DROPPED_"), True),
    SOURCE_EVENTS: ("embodiment.events", ("DEGRADED_",), True),
    SOURCE_CONTINUITY: ("embodiment.continuity", ("CODE_",), True),
    SOURCE_LIFECYCLE: ("embodiment.lifecycle", ("_FAULT_",), False),
    SOURCE_SCOPE: ("embodiment.strategist_runner", ("DEGRADED_", "DROPPED_"), True),
    SOURCE_LEDGER: (__name__, ("DEGRADED_",), True),
}

# Which vocabularies a given reader consults when attributing a code. A lane
# that relays another's records lists both; every other lane lists only itself.
# Keeping this narrow is what lets ``from_loop`` fold a drive without importing
# the memory subsystem.
_RELEVANT: dict[str, tuple[str, ...]] = {
    SOURCE_LOOP: (SOURCE_LOOP,),
    SOURCE_MUSE: (SOURCE_MUSE,),
    # ``_absorb`` copies a finished session's own codes onto the runner ledger.
    SOURCE_MUSE_RUNNER: (SOURCE_MUSE_RUNNER, SOURCE_MUSE),
    SOURCE_EVENTS: (SOURCE_EVENTS,),
    SOURCE_CONTINUITY: (SOURCE_CONTINUITY,),
    # ``_emit_degradation`` re-emits a continuity ``CODE_*`` as a checkpoint.
    SOURCE_LIFECYCLE: (SOURCE_LIFECYCLE, SOURCE_CONTINUITY),
    SOURCE_SCOPE: (SOURCE_SCOPE,),
    SOURCE_LEDGER: (SOURCE_LEDGER,),
    # The subagent lane relays the child's own codes: a loop degradation from a
    # child keeps ``source=loop`` while carrying ``child_task_id``. The subagent
    # lane itself has no codes (it is not in :data:`_MODULES`), so it is omitted
    # from its own relevance set — only the child's possible minting lanes are
    # searched.
    SOURCE_SUBAGENT: (
        SOURCE_LOOP,
        SOURCE_MUSE,
        SOURCE_MUSE_RUNNER,
        SOURCE_EVENTS,
        SOURCE_CONTINUITY,
        SOURCE_LIFECYCLE,
    ),
}

# code -> constant name, per lane. Populated on first use and never invalidated:
# a module's vocabulary is a set of module-level constants and cannot change
# after import.
_VOCABULARY_CACHE: dict[str, dict[str, str]] = {}


# ── shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CodeEntry:
    """One code in one lane's vocabulary, and the constant that names it."""

    source: str
    code: str
    constant: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "code": self.code, "constant": self.constant}


@dataclass(frozen=True)
class LedgerRecord:
    """One degradation, in the one shape a host reads.

    Fields
    ------
    source:
        The lane that MINTED the code, from :data:`SOURCES` — resolved against
        that lane's own vocabulary, not from the container it was read out of.
    code:
        The stable machine token, verbatim from the minting lane. A host
        branches on this, never on ``reason``.
    reason:
        Short human-readable cause, capped at 500 characters as every lane caps
        its own.
    step_index:
        The acting loop's step, where the source shape carries one. ``None``
        when it does not — never ``0``.
    model_turns:
        Model turns spent when the degradation happened, where the source shape
        carries one. ``None`` when it does not.
    boundary:
        The checkpoint boundary, for a lifecycle-sourced record. ``None``
        elsewhere, and ``None`` (not ``""``) for a lifecycle event that belongs
        to the instance rather than to a boundary.
    stage:
        Which continuity call degraded (``remember`` / ``recall`` / ``assess`` /
        ``probe``). ``None`` elsewhere.
    subsystem:
        ``eidetic`` or ``coherence``, for a continuity-sourced record. ``None``
        elsewhere. Note this is the *sibling library*, not the embodiment lane —
        that is :attr:`source`.
    exception:
        The caught exception's class name, where the source recorded one.
    child_task_id:
        The child drive's task id, when this record came from a subagent.
        ``None`` for every existing lane and populated for records folded from
        :data:`SOURCE_SUBAGENT`. A host can answer "who degraded?" without
        heuristics or parsing reason text.
    original:
        The record exactly as its lane built it — the live object, untouched, so
        nothing is lost in the fold. Deliberately absent from :meth:`to_dict`:
        serialising it is the lane's own ``to_dict``'s job, and calling a
        foreign method during serialisation is a failure path this surface
        refuses to own.
    """

    source: str
    code: str
    reason: str = ""
    step_index: Optional[int] = None
    model_turns: Optional[int] = None
    boundary: Optional[str] = None
    stage: Optional[str] = None
    subsystem: Optional[str] = None
    exception: Optional[str] = None
    child_task_id: Optional[str] = None
    original: Any = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe fold. Absent fields are OMITTED, never emitted as zeros.

        :attr:`original` is **excluded even when present** — it is a live
        foreign object, and serialising it is its own lane's ``to_dict``'s job.
        That exclusion is what makes this return value JSON-safe
        unconditionally; a reader who needs the untouched record reaches for the
        attribute directly.
        """
        data: dict[str, Any] = {
            "source": self.source,
            "code": self.code,
            "reason": self.reason,
        }
        for name in (
            "step_index",
            "model_turns",
            "boundary",
            "stage",
            "subsystem",
            "exception",
            "child_task_id",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data


# ── the vocabulary registry ───────────────────────────────────────────────────


def _vocabulary(source: str) -> dict[str, str]:
    """``{code: constant name}`` for one lane, read from the module itself."""
    cached = _VOCABULARY_CACHE.get(source)
    if cached is not None:
        return cached
    module_name, prefixes, public_only = _MODULES[source]
    module = importlib.import_module(module_name)
    names = getattr(module, "__all__", ()) if public_only else tuple(vars(module))
    found: dict[str, str] = {}
    for name in names:
        if not name.startswith(prefixes):
            continue
        value = getattr(module, name, None)
        # Only a non-empty string is a code. A tuple of codes, or a set of
        # kinds, shares the prefix and is not one.
        if isinstance(value, str) and value:
            found[value] = name
    _VOCABULARY_CACHE[source] = found
    return found


def known_codes() -> tuple[CodeEntry, ...]:
    """Every degradation code embodiment can record, across every lane.

    Derived from each module's own exported constants, so this is the complete
    set by construction: adding a ``DEGRADED_*`` / ``DROPPED_*`` / ``CODE_*``
    constant to a lane adds it here with no edit to this file.

    Imports all six lanes (that is the whole point of the call), so a host on a
    hot path should prefer the narrower :func:`source_for_code`.
    """
    entries = [
        CodeEntry(source=source, code=code, constant=constant)
        for source in SOURCES
        for code, constant in _vocabulary(source).items()
    ]
    return tuple(sorted(entries, key=lambda entry: (entry.source, entry.code)))


def source_for_code(code: str, *, within: Optional[tuple[str, ...]] = None) -> Optional[str]:
    """Which lane mints *code*, or ``None`` when no lane does.

    *within* narrows the search to a subset of :data:`SOURCES` — how the readers
    avoid importing lanes they were not handed. Omitted, every lane is searched.
    """
    for source in within if within is not None else SOURCES:
        if code in _vocabulary(source):
            return source
    return None


# ── reading one record ────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    """A capped string, or ``""``. Never raises on a value with a hostile repr."""
    if value is None:
        return ""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception as exc:  # an unrenderable value is named, not dropped
        return f"<unrenderable {type(value).__name__}: {type(exc).__name__}>"
    return text[:_MAX_REASON_LEN]


def _optional_text(value: Any) -> Optional[str]:
    """``None`` for an absent or empty value — never a fabricated ``""``."""
    text = _text(value)
    return text or None


def _optional_int(value: Any) -> Optional[int]:
    """``None`` when the source shape carries no such number — never a ``0``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _unreadable(reason: str, original: Any = None) -> LedgerRecord:
    """The ledger's own degradation record. Reading failed; saying so did not."""
    return LedgerRecord(
        source=SOURCE_LEDGER,
        code=DEGRADED_UNREADABLE_SOURCE,
        reason=reason[:_MAX_REASON_LEN],
        original=original,
    )


def _adapt(record: Any, *, lane: str) -> LedgerRecord:
    """Fold one lane record (loop / muse / runner / events / continuity)."""
    code = _text(getattr(record, "code", ""))
    return LedgerRecord(
        source=source_for_code(code, within=_RELEVANT[lane]) or lane,
        code=code,
        reason=_text(getattr(record, "reason", "")),
        step_index=_optional_int(getattr(record, "step_index", None)),
        model_turns=_optional_int(getattr(record, "model_turns", None)),
        stage=_optional_text(getattr(record, "stage", None)),
        subsystem=_optional_text(getattr(record, "subsystem", None)),
        exception=_optional_text(getattr(record, "exception", None)),
        original=record,
    )


# Keys a lifecycle degradation event's ``data`` carries that fold into named
# fields. Anything else becomes the reason for a fault that recorded no text.
_LIFECYCLE_FOLDED = frozenset({"code", "reason", "subsystem", "stage", "exception", "error"})


def _adapt_lifecycle_event(event: Any) -> LedgerRecord:
    """Fold one ``LifecycleEvent`` of kind ``degraded``.

    ``detail`` is the stable token — either one of lifecycle's own faults or a
    ``continuity.CODE_*`` relayed verbatim — and ``data`` carries the rest.
    """
    data = getattr(event, "data", None)
    fields: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    code = _text(getattr(event, "detail", "")) or _text(fields.get("code"))
    reason = _text(fields.get("reason")) or _text(fields.get("error"))
    if not reason:
        # A fault that recorded structured detail instead of prose (``trace-lost``
        # carries only a ``task_id``). Rendered from the source's own data, so
        # the reason is reported rather than invented.
        extras = sorted((key, fields[key]) for key in fields if key not in _LIFECYCLE_FOLDED)
        reason = _text(", ".join(f"{key}={value}" for key, value in extras))
    return LedgerRecord(
        source=source_for_code(code, within=_RELEVANT[SOURCE_LIFECYCLE]) or SOURCE_LIFECYCLE,
        code=code,
        reason=reason,
        boundary=_optional_text(getattr(event, "boundary", None)),
        stage=_optional_text(fields.get("stage")),
        subsystem=_optional_text(fields.get("subsystem")),
        exception=_optional_text(fields.get("exception")),
        original=event,
    )


# ── reading a container ───────────────────────────────────────────────────────


def _is_sequence(value: Any) -> bool:
    """A plain sequence of records, as opposed to one record or a container."""
    return isinstance(value, (list, tuple, set, frozenset))


def _records_of(source: Any) -> list[Any]:
    """Every raw degradation record inside *source*, in its own recorded order.

    Accepts, in this order: a container exposing ``degradations``; an outcome
    exposing a single optional ``degradation``; an exception carrying an
    ``outcome`` (``LoopAborted``); one bare record; or a sequence of any of
    those. Anything else raises, and the caller turns that into a
    :data:`DEGRADED_UNREADABLE_SOURCE` record.
    """
    if source is None:
        return []
    if hasattr(source, "degradations"):
        return list(source.degradations)
    if hasattr(source, "degradation"):
        single = source.degradation
        return [] if single is None else [single]
    if hasattr(source, "outcome"):
        return _records_of(source.outcome)
    if hasattr(source, "code"):
        return [source]
    if _is_sequence(source):
        found: list[Any] = []
        for item in source:
            found.extend(_records_of(item))
        return found
    raise TypeError(f"cannot read degradations from {type(source).__name__}")


def _fold(source: Any, *, lane: str) -> list[LedgerRecord]:
    """Read one container into ledger records. Never raises (C3)."""
    try:
        raw = _records_of(source)
    except Exception as exc:  # an unreadable source is recorded, never raised
        return [_unreadable(f"{lane}: {type(exc).__name__}: {exc}", source)]
    folded: list[LedgerRecord] = []
    for record in raw:
        try:
            folded.append(_adapt(record, lane=lane))
        except Exception as exc:  # noqa: BLE001  # one bad record never loses the rest
            folded.append(_unreadable(f"{lane} record: {type(exc).__name__}: {exc}", record))
    return folded


# ── the readers ───────────────────────────────────────────────────────────────


def from_loop(source: Any) -> list[LedgerRecord]:
    """Fold a :class:`~embodiment.loop.LoopOutcome`'s degradation ledger.

    Also accepts a :class:`~embodiment.loop.LoopAborted` (the partial's ledger
    is on its ``outcome``), a bare ``LoopDegradation``, or a sequence of any.
    """
    return _fold(source, lane=SOURCE_LOOP)


def from_muse(source: Any) -> list[LedgerRecord]:
    """Fold a :class:`~embodiment.muse.MuseOutcome`'s degradation ledger."""
    return _fold(source, lane=SOURCE_MUSE)


def from_muse_runner(source: Any) -> list[LedgerRecord]:
    """Fold a :class:`~embodiment.muse_runner.ThreadedMuseRunner`'s ledger.

    The runner absorbs a finished session's own codes verbatim, so a fold of one
    runner legitimately produces both ``muse_runner``- and ``muse``-sourced
    records. Which is which is looked up, never guessed from the container.
    """
    return _fold(source, lane=SOURCE_MUSE_RUNNER)


def from_events(source: Any) -> list[LedgerRecord]:
    """Fold an :class:`~embodiment.events.EventEmitter`'s degradation ledger."""
    return _fold(source, lane=SOURCE_EVENTS)


def from_continuity(source: Any) -> list[LedgerRecord]:
    """Fold a continuity outcome, status, or bare degradation.

    ``RememberOutcome`` / ``RecallOutcome`` / ``AssessOutcome`` each carry one
    optional ``degradation``; ``ContinuityStatus`` carries a tuple. Both shapes
    (and a sequence mixing them) read the same way.
    """
    return _fold(source, lane=SOURCE_CONTINUITY)


def from_lifecycle(source: Any) -> list[LedgerRecord]:
    """Fold a :class:`~embodiment.lifecycle.ContinuityLifecycle`'s degraded events.

    Only events of kind ``degraded`` are folded — the rest of the checkpoint
    ledger (recalls, assessments, skips) is the lived sequence, not a
    degradation stream, and reporting it here would overclaim.
    """
    try:
        degraded_kind = importlib.import_module("embodiment.lifecycle").CHECKPOINT_DEGRADED
        if source is None:
            candidates: list[Any] = []
        elif hasattr(source, "events"):
            candidates = list(source.events)
        elif hasattr(source, "kind"):
            candidates = [source]
        else:
            candidates = list(source)
    except Exception as exc:  # an unreadable source is recorded, never raised
        return [_unreadable(f"{SOURCE_LIFECYCLE}: {type(exc).__name__}: {exc}", source)]
    folded: list[LedgerRecord] = []
    for event in candidates:
        try:
            if getattr(event, "kind", None) != degraded_kind:
                continue
            folded.append(_adapt_lifecycle_event(event))
        except Exception as exc:  # noqa: BLE001  # one bad event never loses the rest
            folded.append(
                _unreadable(f"{SOURCE_LIFECYCLE} event: {type(exc).__name__}: {exc}", event)
            )
    return folded


def from_scope(source: Any) -> list[LedgerRecord]:
    """Fold a scope-governance degradation ledger (task t3).

    Accepts a :class:`~embodiment.scope.ScopeOutcome` (one review), a
    :class:`~embodiment.strategist_runner.StrategistRunner` (the accumulated
    lane), a bare :class:`~embodiment.scope.ScopeDegradation` /
    :class:`~embodiment.scope.ScopeRejection`, or a sequence of any — every
    shape exposes ``degradations`` or ``code`` on the same terms every other
    reader's shapes do.

    The vocabulary is read from :mod:`embodiment.strategist_runner`, which
    re-exports every code :mod:`embodiment.scope` mints alongside its own ten,
    so this ONE lane harvests the whole scope-governance vocabulary rather than
    two — a review-level code and a lane-level code attribute to the same
    source.
    """
    return _fold(source, lane=SOURCE_SCOPE)


def from_subagent(source: Any, *, child_task_id: Optional[str] = None) -> list[LedgerRecord]:
    """Fold a child drive's degradations with child attribution.

    *source* is a sequence of raw degradation records from a
    :class:`~embodiment.subagent.SubagentResult`. Each record keeps its
    minting lane in :attr:`LedgerRecord.source` (a loop code is a loop code)
    while carrying *child_task_id* so a host can answer "who degraded?"
    without heuristics.

    When *source* is a :class:`~embodiment.subagent.SpawnRecord`, its
    :attr:`~embodiment.subagent.SpawnRecord.degradations` and
    :attr:`~embodiment.subagent.SpawnRecord.child_task_id` are read
    automatically.
    """
    subagent_module = importlib.import_module("embodiment.subagent")
    spawn_record_cls = subagent_module.SpawnRecord

    if source is None:
        return []
    if isinstance(source, spawn_record_cls):
        child_id = source.child_task_id
        raw = list(source.degradations)
    else:
        child_id = child_task_id
        raw = list(source) if _is_sequence(source) else [source]

    folded: list[LedgerRecord] = []
    for record in raw:
        try:
            adapted = _adapt(record, lane=SOURCE_SUBAGENT)
            folded.append(
                LedgerRecord(
                    source=adapted.source,
                    code=adapted.code,
                    reason=adapted.reason,
                    step_index=adapted.step_index,
                    model_turns=adapted.model_turns,
                    boundary=adapted.boundary,
                    stage=adapted.stage,
                    subsystem=adapted.subsystem,
                    exception=adapted.exception,
                    child_task_id=child_id,
                    original=adapted.original,
                )
            )
        except Exception as exc:  # noqa: BLE001  # one bad record never loses the rest
            folded.append(
                _unreadable(
                    f"{SOURCE_SUBAGENT} record: {type(exc).__name__}: {exc}",
                    record,
                )
            )
    return folded


_READERS = {
    SOURCE_LOOP: from_loop,
    SOURCE_MUSE: from_muse,
    SOURCE_MUSE_RUNNER: from_muse_runner,
    SOURCE_EVENTS: from_events,
    SOURCE_CONTINUITY: from_continuity,
    SOURCE_LIFECYCLE: from_lifecycle,
    SOURCE_SCOPE: from_scope,
}


def read(
    *,
    loop: Any = None,
    muse: Any = None,
    muse_runner: Any = None,
    events: Any = None,
    continuity: Any = None,
    lifecycle: Any = None,
    scope: Any = None,
    subagent: Any = None,
) -> list[LedgerRecord]:
    """Fold everything a host was handed into ONE stream.

    Every argument is optional and keyword-only: the caller names the lane, so
    no container is ever classified by guesswork, and a lane left out is never
    imported. Each accepts one object or a sequence of them::

        records = ledger.read(loop=outcome, muse_runner=runner, lifecycle=checkpoints)
        for record in records:
            log(record.to_dict())

    Records are grouped by lane in :data:`SOURCES` order, each lane in its own
    recorded order. They are deliberately not interleaved by time — see the
    module docstring.
    """
    folded: list[LedgerRecord] = []
    for lane in SOURCES:
        given = {
            SOURCE_LOOP: loop,
            SOURCE_MUSE: muse,
            SOURCE_MUSE_RUNNER: muse_runner,
            SOURCE_EVENTS: events,
            SOURCE_CONTINUITY: continuity,
            SOURCE_LIFECYCLE: lifecycle,
            SOURCE_SCOPE: scope,
        }.get(lane)
        if given is None:
            continue
        folded.extend(_READERS[lane](given))
    # Subagent is not in SOURCES (it has no codes of its own), so handle it
    # separately. It appears after the standard lanes in the output.
    if subagent is not None:
        folded.extend(from_subagent(subagent))
    # Also fold child degradations from loop spawns when a loop outcome is
    # provided but no explicit subagent argument. The child's degradations ride
    # on SpawnRecord.degradations inside outcome.spawns.
    if subagent is None and loop is not None:
        _fold_spawn_degradations(loop, folded)
    return folded


def _fold_spawn_degradations(loop_source: Any, folded: list[LedgerRecord]) -> None:
    """Fold child degradations from a loop outcome's spawn records.

    Each granted spawn carries the child's own degradation records on
    :attr:`~embodiment.subagent.SpawnRecord.degradations` and a
    :attr:`~embodiment.subagent.SpawnRecord.child_task_id`. These are folded
    through :func:`from_subagent` so they carry child attribution.
    """
    spawns = getattr(loop_source, "spawns", None)
    if not spawns:
        return
    for spawn in spawns:
        if not getattr(spawn, "granted", False):
            continue
        child_id = getattr(spawn, "child_task_id", None)
        if child_id is None:
            continue
        child_records = from_subagent(spawn)
        folded.extend(child_records)
