"""embodiment.config_report — effective-config introspection, derived from the
applied-change ledger ALONE.

Task ``t7`` (issue #75, spec claims ``c32``/``h22``): "an operator-visible
introspection surface reports each seat's current effective configuration with
provenance — which change produced it, when, under which gate verdict — derived
from the applied-change ledger (one source of truth, no parallel bookkeeping),
so any operator-visible utterance can be traced to the configuration active
when it was produced ... a config state the ledger cannot explain is itself a
recorded degradation (C3)."

This module reads :class:`embodiment.config_ledger.ConfigLedger` (or anything
duck-typed like it — a bare sequence of :class:`~embodiment.config_ledger.
LedgerEntry` works too) and NOTHING ELSE. There is no second store here, and
nothing this module returns is cached: every call to :func:`build_config_report`
replays :attr:`~embodiment.config_ledger.ConfigLedger.entries` from position
zero. That is the module's whole answer to "one source of truth, no parallel
bookkeeping" — a cache or an accumulator that outlived one call would be
exactly the second bookkeeping structure the task's own instruction warns
against, so there is not one.

Three questions the acceptance criterion asks, and where each answer comes from
------------------------------------------------------------------------------
**"Which change produced it"** — the ``change_id``/``target``/``origin`` of the
ledger entry a seat's current section/entry/capability-surface was last folded
from. Recorded per element, not just per seat: two prompt sections on the same
seat can honestly have been produced by two different changes.

**"When"** — there is no clock in the ledger, exactly as
:mod:`embodiment.config_lifecycle`'s own docstring states for
:class:`~embodiment.config_lifecycle.ConfigTransition` ("t5's ledger stamps
time where time is available, and order is what this layer can honestly
record"). The ledger does not even have "where time is available" —
:class:`~embodiment.config_ledger.LedgerEntry` carries a caller-supplied
``step_index`` and nothing else. So "when" here is two honest things, never an
invented wall clock: :attr:`ChangeProvenance.ledger_index` (this entry's own
position in ``ledger.entries`` — a total order over everything the ledger has
ever recorded) and :attr:`ChangeProvenance.step_index` (the host's own coarse
marker, carried through unchanged, ``0`` if the host never supplied one).

**"Under which gate verdict"** — the ledger's own vocabulary is exactly two
states, :data:`~embodiment.config_ledger.LEDGER_STATE_APPLIED` and
:data:`~embodiment.config_ledger.LEDGER_STATE_REVERTED`
(:data:`~embodiment.config_ledger.LEDGER_STATES`). By construction, the only
way an entry reaches ``applied`` is a host calling ``ConfigLedger.
record_applied`` after the propose→verify→apply gate (task ``t4``'s
:class:`~embodiment.config_lifecycle.ConfigLifecycle`) actually installs a
change — so an ``applied`` entry already IS the gate's verdict: this passed and
is in force. :class:`~embodiment.config_lifecycle.ConfigTransition` carries a
richer ``verdict`` (``"passed"``/``"failed"``/``"stale"``, plus a suite
summary) — and this module deliberately does not read it. That stream lives on
a *different* object (``ConfigLifecycle.transitions``, not the ledger), and
reading it here would be precisely the "gap filled from elsewhere" the task
forbids: two objects that are usually in step but are not the same fact, kept
in step only by whatever composes them (task ``t9``'s job, not this one). So
:attr:`ChangeProvenance.state` carries the ledger's own two-word vocabulary and
nothing richer — an honestly weaker claim than ``ConfigTransition.verdict``,
and the only one derivable from the ledger alone.

Why :func:`~embodiment.config_lifecycle.apply_change` IS reused, and why that
is not "elsewhere" data
------------------------------------------------------------------------------
Folding one applied unit into a running configuration (replace-or-append a
prompt section, upsert-and-supersede a knowledge entry, assign a whole
capability surface) is exactly what task ``t4``'s
:func:`~embodiment.config_lifecycle.apply_change` already does, proven by its
own test suite ("walks every entry of ``CHANGE_UNITS`` and asserts each one
moves the digest"). Re-deriving those three fold rules a second time here would
be a second, silently-driftable DEFINITION of what a change does — the same
"parallel bookkeeping" the task warns against, just moved from data into logic.
So this module imports the pure, stateless half of ``config_lifecycle``
(:class:`~embodiment.config_lifecycle.SeatConfig` and
:func:`~embodiment.config_lifecycle.apply_change` — no thread, no clock, no IO,
no store, and critically no memory of anything outside one call's own
arguments) and uses it purely as computation, never as a second SOURCE of what
happened. The only source of what happened is the sequence of
:class:`~embodiment.config_ledger.LedgerEntry` records this module is handed.
``tests/test_config_report.py``'s ``TestCitedNotCoupled`` pins the whole import
closure so this stays checkable rather than merely asserted in prose.

Reverted entries do not undo the fold — they narrate it
------------------------------------------------------------------------------
:class:`~embodiment.config_ledger.LedgerEntry`'s own docstring: "reverting does
not erase that the change once governed, only that it no longer does (task
t7's introspection needs both facts to explain a seat's history, not only its
current state)." A ``reverted`` entry is therefore never folded into a seat's
CURRENT values here — folding only ever consumes ``applied`` entries.
Restoring a prior value is expected to arrive as its own NEW ``applied`` entry
(task ``t6``'s revert-to-baseline mechanics), which this module folds exactly
like any other applied change, last-applied-wins. What a ``reverted`` marker
DOES do is appear in :attr:`SeatEffectiveConfig.history` (every entry, applied
and reverted, in ledger order) — so a reader can see both that a change once
governed a section AND that it was later marked reverted, even when nothing
has yet re-applied a replacement. A ``reverted`` entry whose ``change_id`` was
never recorded ``applied`` earlier in this ledger, for this seat, is exactly "a
config state the ledger cannot explain": recorded as
:data:`CONFIG_REPORT_ORPHAN_REVERT`, never guessed at.

What "unexplained" means here (constraint C3)
------------------------------------------------------------------------------
Five things this module cannot fold, each its own code so the fix is legible:
a raw entry unreadable as any :class:`~embodiment.config_ledger.LedgerEntry`
shape at all (:data:`CONFIG_REPORT_MALFORMED_ENTRY`); an entry naming a seat
outside :data:`~embodiment.config_change.CHANGE_SEATS`
(:data:`CONFIG_REPORT_UNKNOWN_SEAT`); an applied entry naming a target outside
:data:`~embodiment.config_change.CHANGE_TARGETS`
(:data:`CONFIG_REPORT_UNKNOWN_TARGET`); an applied entry whose recorded
``unit`` cannot be read back as that target's own typed shape
(:data:`CONFIG_REPORT_MALFORMED_UNIT`); and the orphan-revert case above
(:data:`CONFIG_REPORT_ORPHAN_REVERT`). Every one is recorded as a
:class:`~embodiment.config_change.ConfigRefusal` (which IS a
:class:`~embodiment.config_change.ConfigDegradation` — the same "one stream"
discipline ``config_ledger.py`` already holds), attached to the seat it is
about when one is known, or to :attr:`ConfigReport.unexplained` when it is not
(an unreadable entry, or one naming a seat nobody recognizes). The ledger
position stays visible — not as a fabricated new field, but named plainly in
``reason`` text — and the host-supplied ``step_index`` is carried through
unchanged where one was recorded. NEVER raises, ever: a hostile ``ledger``
argument (``None``, a string, a list of garbage) reads as an empty report with
degradations recorded for whatever could not be read, never an exception into
the caller's main path.

Not a CLI verb (yet)
------------------------------------------------------------------------------
The plan is explicit: "a host-callable report function (and a CLI verb later
if warranted)". This ships the function only —
``tests/announcement_checklist.py``'s import-closure check for
``cli/_commands/`` still holds with this module unreached from there.

Stdlib only, plus :mod:`embodiment.capability`, :mod:`embodiment.config_change`,
:mod:`embodiment.config_ledger` and the pure half of
:mod:`embodiment.config_lifecycle` (constraint C1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Optional, Sequence

from embodiment.capability import CAPABILITY_KIND_PERMISSION, CAPABILITY_KIND_TOOL
from embodiment.config_change import (
    CHANGE_SEATS,
    CHANGE_TARGETS,
    CHANGE_UNITS,
    CapabilitySelection,
    ConfigChange,
    ConfigRefusal,
    KnowledgeChange,
    PromptChange,
)
from embodiment.config_ledger import LEDGER_STATE_REVERTED, LEDGER_STATES
from embodiment.config_lifecycle import SeatConfig, apply_change

__all__ = [
    "CONFIG_REPORT_MALFORMED_ENTRY",
    "CONFIG_REPORT_UNKNOWN_SEAT",
    "CONFIG_REPORT_UNKNOWN_TARGET",
    "CONFIG_REPORT_MALFORMED_UNIT",
    "CONFIG_REPORT_ORPHAN_REVERT",
    "CONFIG_REPORT_DEGRADATION_CODES",
    "ChangeProvenance",
    "PromptSectionReport",
    "KnowledgeEntryReport",
    "CapabilitySurfaceReport",
    "SeatEffectiveConfig",
    "ConfigReport",
    "build_config_report",
    "effective_config",
    "render_text",
]


# ── this module's own degradation vocabulary (C3) ───────────────────────────
#
# Prefixed `config-report-` so it cannot collide with `config-change-*`,
# `config-seat-*` or `config-ledger-*` even though all four fold into the same
# ConfigDegradation stream (config_change.py's rule, inherited here too).

#: A raw ledger row could not be read as any known shape at all.
CONFIG_REPORT_MALFORMED_ENTRY = "config-report-malformed-entry"
#: An entry names a seat outside :data:`~embodiment.config_change.CHANGE_SEATS`.
CONFIG_REPORT_UNKNOWN_SEAT = "config-report-unknown-seat"
#: An applied entry names a target outside
#: :data:`~embodiment.config_change.CHANGE_TARGETS`.
CONFIG_REPORT_UNKNOWN_TARGET = "config-report-unknown-target"
#: An applied entry's recorded unit cannot be read back as its target's shape.
CONFIG_REPORT_MALFORMED_UNIT = "config-report-malformed-unit"
#: A reverted entry names a change_id this ledger never recorded applied.
CONFIG_REPORT_ORPHAN_REVERT = "config-report-orphan-revert"

CONFIG_REPORT_DEGRADATION_CODES = (
    CONFIG_REPORT_MALFORMED_ENTRY,
    CONFIG_REPORT_UNKNOWN_SEAT,
    CONFIG_REPORT_UNKNOWN_TARGET,
    CONFIG_REPORT_MALFORMED_UNIT,
    CONFIG_REPORT_ORPHAN_REVERT,
)

#: Cap on a recorded reason's text (``scope.py`` / ``config_change.py``'s value).
_MAX_REASON_LEN = 500


# ── coercion helpers (never raise; own copies, see the sibling modules) ─────


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unreadable value is a blank, never a crash
        return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:  # noqa: BLE001  # a junk number is the default, never a crash
        return default


# ── the recorded shapes ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChangeProvenance:
    """Which change produced one element, when, under which gate verdict.

    ``ledger_index`` and ``step_index`` are the whole of "when" this module can
    honestly report — see the module docstring. ``state`` is the ledger's own
    two-word verdict vocabulary
    (:data:`~embodiment.config_ledger.LEDGER_STATES`), not the richer
    ``passed``/``failed``/``stale`` a live ``ConfigLifecycle`` verification
    records — reading that would be the second bookkeeping structure this
    module exists to avoid.
    """

    change_id: str = ""
    seat: str = ""
    target: str = ""
    origin: str = ""
    reason: str = ""
    ledger_index: int = 0
    step_index: int = 0
    state: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "seat": self.seat,
            "target": self.target,
            "origin": self.origin,
            "reason": self.reason,
            "ledger_index": self.ledger_index,
            "step_index": self.step_index,
            "state": self.state,
        }


@dataclass(frozen=True)
class PromptSectionReport:
    """One prompt section as it stands now, with what produced it."""

    section: str = ""
    text: str = ""
    provenance: ChangeProvenance = field(default_factory=ChangeProvenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "text": self.text,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class KnowledgeEntryReport:
    """One knowledge entry as it stands now, with what produced it."""

    entry_id: str = ""
    text: str = ""
    origin: str = ""
    provenance: ChangeProvenance = field(default_factory=ChangeProvenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "text": self.text,
            "origin": self.origin,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class CapabilitySurfaceReport:
    """A whole capability surface (tools or permissions) and its one producer.

    Never a delta (``config_change.py``'s decision, inherited): a selection
    replaces the whole surface, so one provenance record covers all of it.
    ``provenance is None`` means exactly one thing — the ledger never recorded
    any change to this surface — never that something is wrong.
    """

    kind: str = ""
    capability_ids: tuple[str, ...] = ()
    provenance: Optional[ChangeProvenance] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "capability_ids": list(self.capability_ids),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
        }


@dataclass(frozen=True)
class SeatEffectiveConfig:
    """One seat's current effective configuration, with per-element provenance.

    ``config_sha`` is computed by handing the reconstructed values to
    :class:`~embodiment.config_lifecycle.SeatConfig` — the SAME digest a live
    :class:`~embodiment.config_lifecycle.ConfigLifecycle` computes for the seat
    it is actually driving, so a host running both can assert the two agree
    (``tests/test_config_report.py``'s parity test does exactly that).
    ``history`` is every ledger entry naming this seat, in ledger order —
    applied AND reverted — so a reader can see the whole narrative, not only
    where it currently stands. ``unexplained`` is this seat's own C3 stream.
    """

    seat: str = ""
    prompt: tuple[PromptSectionReport, ...] = ()
    knowledge: tuple[KnowledgeEntryReport, ...] = ()
    tools: CapabilitySurfaceReport = field(
        default_factory=lambda: CapabilitySurfaceReport(kind=CAPABILITY_KIND_TOOL)
    )
    permissions: CapabilitySurfaceReport = field(
        default_factory=lambda: CapabilitySurfaceReport(kind=CAPABILITY_KIND_PERMISSION)
    )
    config_sha: str = ""
    history: tuple[ChangeProvenance, ...] = ()
    unexplained: tuple[ConfigRefusal, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "prompt": [entry.to_dict() for entry in self.prompt],
            "knowledge": [entry.to_dict() for entry in self.knowledge],
            "tools": self.tools.to_dict(),
            "permissions": self.permissions.to_dict(),
            "config_sha": self.config_sha,
            "history": [entry.to_dict() for entry in self.history],
            "unexplained": [entry.to_dict() for entry in self.unexplained],
        }


@dataclass(frozen=True)
class ConfigReport:
    """Every requested seat's effective configuration, in one host-callable object."""

    seats: tuple[SeatEffectiveConfig, ...] = ()
    unexplained: tuple[ConfigRefusal, ...] = ()

    def seat(self, name: Any) -> Optional[SeatEffectiveConfig]:
        """The named seat's report, or ``None`` when it was not requested."""
        wanted = _text(name).strip()
        for entry in self.seats:
            if entry.seat == wanted:
                return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seats": [entry.to_dict() for entry in self.seats],
            "unexplained": [entry.to_dict() for entry in self.unexplained],
        }


# ── reading raw ledger rows, defensively (never raise) ──────────────────────


@dataclass(frozen=True)
class _Parsed:
    """One raw ledger row, coerced to plain fields. Private to this module."""

    state: str
    change_id: str
    seat: str
    target: str
    origin: str
    reason: str
    step_index: int
    unit: Any


def _field(raw: Any, name: str, default: Any = "") -> Any:
    """Read *name* off *raw*, dict or object alike. Never raises."""
    if isinstance(raw, dict):
        return raw.get(name, default)
    try:
        return getattr(raw, name, default)
    except Exception:  # noqa: BLE001  # an unreadable attribute is simply absent
        return default


def _read_entry(raw: Any) -> Optional[_Parsed]:
    """Read one raw ledger row. ``None`` means unreadable — never raises."""
    state = _text(_field(raw, "state")).strip()
    if state not in LEDGER_STATES:
        return None
    change_id = _text(_field(raw, "change_id")).strip()
    if not change_id:
        return None
    return _Parsed(
        state=state,
        change_id=change_id,
        seat=_text(_field(raw, "seat")).strip(),
        target=_text(_field(raw, "target")).strip(),
        origin=_text(_field(raw, "origin")).strip(),
        reason=_text(_field(raw, "reason")),
        step_index=_int(_field(raw, "step_index"), 0),
        unit=_field(raw, "unit", {}),
    )


def _entries_of(ledger: Any) -> tuple[Any, ...]:
    """The raw rows to replay: ``ledger.entries``, or *ledger* itself when it is
    already a sequence (a bare list of :class:`~embodiment.config_ledger.
    LedgerEntry` works without constructing a :class:`~embodiment.config_ledger.
    ConfigLedger`). Never raises."""
    if isinstance(ledger, (list, tuple)):
        return tuple(ledger)
    try:
        entries = getattr(ledger, "entries", None)
    except Exception:  # noqa: BLE001  # a hostile ledger reads as having none
        entries = None
    if callable(entries):
        try:
            entries = entries()
        except Exception:  # noqa: BLE001  # a raising accessor reads as having none
            entries = None
    if isinstance(entries, (list, tuple)):
        return tuple(entries)
    return ()


# ── rebuilding one typed unit from a recorded row (replay, not admission) ───


def _rebuild_change(
    target: str, change_id: str, origin: str, reason: str, unit: Any
) -> tuple[Optional[ConfigChange], str]:
    """Rebuild the minimal typed unit one applied ledger row recorded.

    This is REPLAY, not admission: the ledger's ``applied`` state is already
    the fact that admission happened once, at write time (task ``t3``'s
    :func:`~embodiment.config_change.change_from_payload`), so this does not
    re-run it — doing so would need a live capability catalog just to explain
    history that already happened, which is backwards. Never raises; an
    unreadable unit is reported, not guessed at. ``(change, problem)`` —
    exactly one is truthy.
    """
    cls = CHANGE_UNITS.get(target)
    if cls is None:
        return None, f"{target!r} is not a known configuration target"
    payload = unit if isinstance(unit, dict) else {}
    names = {entry.name for entry in fields(cls)} - {"change_id", "origin", "reason"}
    kwargs: dict[str, Any] = {"change_id": change_id, "origin": origin, "reason": reason}
    for name in names:
        if name in payload:
            kwargs[name] = payload[name]
    try:
        change = cls(**kwargs)
    except Exception as exc:  # noqa: BLE001  # an unreadable unit is unexplained, never a crash
        return None, f"could not rebuild a {cls.__name__} from the recorded unit: {exc}"
    if isinstance(change, PromptChange) and not change.section:
        return None, "the recorded unit names no prompt section"
    if isinstance(change, KnowledgeChange) and (not change.entry_id or not change.text):
        return None, "the recorded unit names no knowledge entry_id, or no text"
    if isinstance(change, CapabilitySelection) and not isinstance(
        payload.get("capability_ids"), (list, tuple)
    ):
        return None, "the recorded unit carries no capability_ids list"
    return change, ""


def _slot_key(change: ConfigChange) -> str:
    """The provenance-map key the slot *change* touches lives under."""
    if isinstance(change, PromptChange):
        return f"prompt:{change.section}"
    if isinstance(change, KnowledgeChange):
        return f"knowledge:{change.entry_id}"
    if isinstance(change, CapabilitySelection):
        return "permissions" if change.KIND == CAPABILITY_KIND_PERMISSION else "tools"
    return ""  # pragma: no cover - CHANGE_UNITS has no fourth family today


def _refuse(
    code: str,
    reason: str,
    *,
    seat: str = "",
    target: str = "",
    change_id: str = "",
    origin: str = "",
    step_index: int = 0,
) -> ConfigRefusal:
    return ConfigRefusal(
        code=code,
        reason=reason[:_MAX_REASON_LEN],
        step_index=step_index,
        seat=seat,
        target=target,
        change_id=change_id,
        origin=origin,
    )


# ── the per-call working state (private, discarded after one build) ─────────


class _SeatWork:
    """Everything one seat accumulates while replaying the ledger.

    Private, per-call, and discarded once :func:`build_config_report` returns
    — this is deliberately NOT held anywhere between calls, which is what
    keeps the ledger the only source of truth (see the module docstring).
    """

    def __init__(self, seat: str) -> None:
        self.seat = seat
        self.config = SeatConfig(seat=seat)
        self.provenance: dict[str, ChangeProvenance] = {}
        self.history: list[ChangeProvenance] = []
        self.unexplained: list[ConfigRefusal] = []
        self.seen_applied: set[str] = set()


def _fold_revert(work: _SeatWork, parsed: _Parsed, index: int) -> None:
    """Record one ``reverted`` marker against *work*. It never undoes the fold.

    Split out of :func:`build_config_report` so the replay reads as a two-way
    dispatch; the rule it carries is the module docstring's: "reverting does not
    erase that the change once governed, only that it no longer does". The
    marker is already in ``work.history`` by the time this runs, which is the
    whole of what a revert does to a seat's CURRENT values — nothing. All this
    adds is the one case the ledger cannot explain: a revert of something this
    ledger never recorded applied for this seat.
    """
    if parsed.change_id in work.seen_applied:
        return
    work.unexplained.append(
        _refuse(
            CONFIG_REPORT_ORPHAN_REVERT,
            f"ledger position {index} marks {parsed.change_id!r} reverted, but "
            f"this ledger never recorded it applied for {parsed.seat!r}; the "
            "current configuration cannot be explained as an undo of anything "
            "the ledger shows happening",
            seat=parsed.seat,
            target=parsed.target,
            change_id=parsed.change_id,
            origin=parsed.origin,
            step_index=parsed.step_index,
        )
    )


def _fold_applied(
    work: _SeatWork, parsed: _Parsed, index: int, provenance: ChangeProvenance
) -> None:
    """Fold one ``applied`` row into *work* — the only state that moves the fold.

    Split out of :func:`build_config_report` for legibility only; every rule
    here is unchanged. Two of the module's five C3 codes are raised from this
    frame (an unknown target, an unreadable unit), and in both cases nothing is
    folded — a half-applied configuration would be exactly the state the ledger
    cannot explain. The actual fold is
    :func:`~embodiment.config_lifecycle.apply_change`, reused rather than
    re-derived (see the module docstring on why that is computation, not a
    second source of what happened).
    """
    work.seen_applied.add(parsed.change_id)
    if parsed.target not in CHANGE_TARGETS:
        work.unexplained.append(
            _refuse(
                CONFIG_REPORT_UNKNOWN_TARGET,
                f"ledger position {index} applies {parsed.change_id!r} against the "
                f"target {parsed.target!r}, which is not one of "
                f"{', '.join(CHANGE_TARGETS)}; nothing was folded for it",
                seat=parsed.seat,
                target=parsed.target,
                change_id=parsed.change_id,
                origin=parsed.origin,
                step_index=parsed.step_index,
            )
        )
        return
    change, problem = _rebuild_change(
        parsed.target, parsed.change_id, parsed.origin, parsed.reason, parsed.unit
    )
    if change is None:
        work.unexplained.append(
            _refuse(
                CONFIG_REPORT_MALFORMED_UNIT,
                f"ledger position {index} applies {parsed.change_id!r} but its "
                f"recorded unit could not be read as a {parsed.target} change: "
                f"{problem}; nothing was folded for it",
                seat=parsed.seat,
                target=parsed.target,
                change_id=parsed.change_id,
                origin=parsed.origin,
                step_index=parsed.step_index,
            )
        )
        return
    if isinstance(change, KnowledgeChange) and change.supersedes:
        work.provenance.pop(f"knowledge:{change.supersedes}", None)
    work.config = apply_change(work.config, change)
    work.provenance[_slot_key(change)] = provenance


def _finish(work: _SeatWork) -> SeatEffectiveConfig:
    config = work.config
    prompt = tuple(
        PromptSectionReport(
            section=section.section,
            text=section.text,
            provenance=work.provenance.get(f"prompt:{section.section}", ChangeProvenance()),
        )
        for section in config.prompt
    )
    knowledge = tuple(
        KnowledgeEntryReport(
            entry_id=entry.entry_id,
            text=entry.text,
            origin=entry.origin,
            provenance=work.provenance.get(f"knowledge:{entry.entry_id}", ChangeProvenance()),
        )
        for entry in config.knowledge
    )
    tools = CapabilitySurfaceReport(
        kind=CAPABILITY_KIND_TOOL,
        capability_ids=config.tools,
        provenance=work.provenance.get("tools"),
    )
    permissions = CapabilitySurfaceReport(
        kind=CAPABILITY_KIND_PERMISSION,
        capability_ids=config.permissions,
        provenance=work.provenance.get("permissions"),
    )
    return SeatEffectiveConfig(
        seat=work.seat,
        prompt=prompt,
        knowledge=knowledge,
        tools=tools,
        permissions=permissions,
        config_sha=config.config_sha,
        history=tuple(work.history),
        unexplained=tuple(work.unexplained),
    )


# ── the host-callable report ─────────────────────────────────────────────────


def build_config_report(ledger: Any, *, seats: Sequence[str] = CHANGE_SEATS) -> ConfigReport:
    """Fold *ledger* into each requested seat's effective configuration.

    Recomputed fresh from :func:`_entries_of` on every call — nothing here is
    cached, so there is no second store to drift out of step with the ledger.
    Never raises: a hostile *ledger* (``None``, a string, a list of garbage)
    reads as an empty report, with degradations recorded for whatever could
    not be read rather than an exception into the caller's main path.

    Args:
        ledger: a :class:`~embodiment.config_ledger.ConfigLedger`, or anything
            duck-typed like one (an ``.entries`` sequence), or a bare sequence
            of :class:`~embodiment.config_ledger.LedgerEntry`-shaped rows.
        seats: which seats to report on. Defaults to the whole authority
            lattice, :data:`~embodiment.config_change.CHANGE_SEATS`. A ledger
            row naming a seat outside this parameter is simply not this call's
            business (not an error); a row naming a seat outside the WHOLE
            lattice is a recorded degradation regardless of *seats*.
    """
    wanted = tuple(_text(name).strip() for name in seats if _text(name).strip())
    if not wanted:
        wanted = CHANGE_SEATS
    works = {name: _SeatWork(name) for name in wanted}
    top_unexplained: list[ConfigRefusal] = []

    for index, raw in enumerate(_entries_of(ledger)):
        parsed = _read_entry(raw)
        if parsed is None:
            top_unexplained.append(
                _refuse(
                    CONFIG_REPORT_MALFORMED_ENTRY,
                    f"ledger position {index} could not be read as a ledger entry "
                    "(no readable state/change_id); nothing was folded for it",
                    step_index=index,
                )
            )
            continue
        if parsed.seat not in CHANGE_SEATS:
            top_unexplained.append(
                _refuse(
                    CONFIG_REPORT_UNKNOWN_SEAT,
                    f"ledger position {index} names the seat {parsed.seat!r}, which is "
                    f"not one of {', '.join(CHANGE_SEATS)}; the ledger cannot explain a "
                    "configuration for a seat outside the authority lattice",
                    seat=parsed.seat,
                    target=parsed.target,
                    change_id=parsed.change_id,
                    origin=parsed.origin,
                    step_index=parsed.step_index,
                )
            )
            continue
        work = works.get(parsed.seat)
        if work is None:
            continue  # a known seat that simply was not requested — not an error

        provenance = ChangeProvenance(
            change_id=parsed.change_id,
            seat=parsed.seat,
            target=parsed.target,
            origin=parsed.origin,
            reason=parsed.reason,
            ledger_index=index,
            step_index=parsed.step_index,
            state=parsed.state,
        )
        work.history.append(provenance)

        if parsed.state == LEDGER_STATE_REVERTED:
            # A revert marker narrates history; it never undoes the fold.
            _fold_revert(work, parsed, index)
            continue
        _fold_applied(work, parsed, index, provenance)

    seat_reports = tuple(_finish(works[name]) for name in wanted)
    return ConfigReport(seats=seat_reports, unexplained=tuple(top_unexplained))


def effective_config(ledger: Any, seat: Any) -> SeatEffectiveConfig:
    """Convenience: one seat's report, via :func:`build_config_report`."""
    name = _text(seat).strip()
    report = build_config_report(ledger, seats=(name,) if name else CHANGE_SEATS)
    found = report.seat(name)
    return found if found is not None else SeatEffectiveConfig(seat=name)


# ── a legible rendering (operator-visible, never raises) ────────────────────


def _render_surface(label: str, surface: CapabilitySurfaceReport) -> str:
    """One capability surface's line, provenance or the honest absence of it.

    ``provenance is None`` is stated in words rather than left as a blank field:
    it means exactly one thing — the ledger never recorded any change to this
    surface — and never that something is wrong (see
    :class:`CapabilitySurfaceReport`).
    """
    if surface.provenance is None:
        return f"  {label}={list(surface.capability_ids)} (never touched by the ledger)"
    return (
        f"  {label}={list(surface.capability_ids)} <- "
        f"{surface.provenance.change_id} @ ledger#{surface.provenance.ledger_index} "
        f"state={surface.provenance.state}"
    )


def _render_unexplained(
    header: str, degradations: Sequence[ConfigRefusal], indent: str
) -> list[str]:
    """One C3 stream under its own header. Nothing unexplained renders nothing.

    Shared by the per-seat stream and the ledger-wide one, which differ only in
    their header and indent — an empty section header would claim a stream
    exists where none does.
    """
    if not degradations:
        return []
    lines = [header]
    for degradation in degradations:
        lines.append(f"{indent}[{degradation.code}] {degradation.reason}")
    return lines


def _render_seat_lines(seat: SeatEffectiveConfig) -> list[str]:
    """One seat's block of :func:`render_text` — every element with its producer.

    ``(unexplained)`` / ``?`` stand in wherever the provenance map names no
    producer for an element — :func:`_finish`'s blank :class:`ChangeProvenance`
    default. Written out rather than left as an empty field, on the same rule as
    :func:`_render_surface`: an absent producer is said, not implied.
    """
    lines = [f"== {seat.seat or '(unnamed seat)'} config_sha={seat.config_sha[:12]} =="]
    if not seat.prompt and not seat.knowledge:
        lines.append("  prompt: (none recorded)")
    for entry in seat.prompt:
        lines.append(
            f"  prompt[{entry.section}] <- {entry.provenance.change_id or '(unexplained)'} "
            f"@ ledger#{entry.provenance.ledger_index} state={entry.provenance.state or '?'}"
        )
    for entry in seat.knowledge:
        lines.append(
            f"  knowledge[{entry.entry_id}] origin={entry.origin} <- "
            f"{entry.provenance.change_id or '(unexplained)'} "
            f"@ ledger#{entry.provenance.ledger_index} state={entry.provenance.state or '?'}"
        )
    for label, surface in (("tools", seat.tools), ("permissions", seat.permissions)):
        lines.append(_render_surface(label, surface))
    lines.extend(
        _render_unexplained(f"  UNEXPLAINED ({len(seat.unexplained)}):", seat.unexplained, "    ")
    )
    return lines


def render_text(report: ConfigReport) -> str:
    """A legible, greppable dump of *report* — provenance included. Never raises."""
    if not isinstance(report, ConfigReport):
        return ""
    lines: list[str] = []
    for seat in report.seats:
        lines.extend(_render_seat_lines(seat))
    lines.extend(
        _render_unexplained(
            f"== ledger-wide UNEXPLAINED ({len(report.unexplained)}) ==",
            report.unexplained,
            "  ",
        )
    )
    return "\n".join(lines)
