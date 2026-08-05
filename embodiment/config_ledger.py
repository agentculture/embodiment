"""embodiment.config_ledger — the applied-change ledger and its fail-closed port.

Task ``t5`` (issue #75, spec claims ``c8``/``h8``, ``c31``/``h21``). Two
requirements land in this module:

* "every applied configuration change is revertible and its effect observable
  under C3 ... emit config-change events (proposed/validated/applied/
  rejected/reverted) through the existing ``scope_events`` translation
  pattern; persist the applied-change ledger through the ``ScopePersistence``
  port" (``c8``/``h8`` — the event half lives in :mod:`embodiment.config_events`,
  cited above it in this same task; this module is the ledger and the port).
* "the config-change ledger schema is versioned and fail-closed against
  advisory-era persisted state: a host whose ``ScopePersistence`` payload
  holds an advisory directive chain gets a recorded refusal or ignore with a
  version hint — never a silent reinterpretation of old state under new
  semantics, never a crash into the host's main path" (``c31``/``h21``).

A NEW persistence port, re-declared rather than imported
-----------------------------------------------------------
:class:`ConfigPersistence` is structurally identical to
:class:`~embodiment.scoped_run.ScopePersistence` — the same two optional
callables, ``load: () -> payload`` and ``save: (payload) -> None`` — and the
same discipline: ``load`` is called once when the ledger opens; anything it
*raises* is recorded and disables writing for the rest of the drive, because
overwriting a store that could not be read would destroy the durable lane
rather than degrade it; ``save`` is called after the ledger's own state
changes, and a raise there is recorded once and disables further writes:
durability is lost, delivery is not.

It is **re-declared, not imported**, for the same reason
:class:`~embodiment.scope_events.ScopeEvent` re-declares
:class:`~embodiment.loop.LoopEvent` rather than importing it, and for the same
reason :mod:`embodiment.config_change` (task ``t3``) imports nothing from
``embodiment.scope``, ``embodiment.scoped_run`` or ``embodiment.strategist_runner``
at all: the config-not-minds tier has to stay import-clean of the advisory
lane so the advisory lane stays byte-stable as the comparator arm task ``t13``
measures the config-change arm against, and an import edge is a reason to
edit it. ``tests/test_config_ledger.py``'s ``TestCitedNotCoupled`` proves the
same closure this module's sibling already proves for
:mod:`embodiment.config_change`. A host that wants to point both lanes at one
store constructs two small, independent port objects — cheap, because the
port is two fields and a property.

Why the schema needs its OWN marker, not just a version number
-------------------------------------------------------------------
The advisory lane's own persisted payload
(:meth:`embodiment.scope.ScopeRegister.to_dict`) already carries a
``schema_version`` key, currently ``1`` — see
``docs/live-test-results/scope-live-session-1-state.json``, the REAL fixture
this module's tests replay verbatim (task ``t5``'s instruction: "Build the
advisory-era fixture from a real ``examples/scope_live_session.py --state``
payload, not a hand-written stub"). If this module also called its version key
``schema_version`` and also started counting from ``1``, an advisory-era
payload would read as *version-plausible* by pure numeric coincidence — the
version number matches, so a check that only compared numbers would let it
through and then fail confusingly (or worse, partially) trying to read
``accepted`` entries shaped like :class:`~embodiment.scope.ScopeDirective` as
if they were :class:`~embodiment.config_change.ConfigChange` records. So this
module's payload carries two markers, not one: :data:`CONFIG_LEDGER_KIND`
(a string this schema owns and the advisory payload never sets) and
:data:`CONFIG_LEDGER_SCHEMA_VERSION`. Either one being absent or wrong is
refused — never partially read, never silently reinterpreted — and the
refusal names the advisory shape specifically when the payload looks like one
(:func:`_mismatch_reason`), because "here is what I actually found and why it
does not match" is a stronger fix-hint than "unknown version".

Refusals fold into ``config_change``'s vocabulary, not a third one
-----------------------------------------------------------------------
Every degradation this module records is a
:class:`~embodiment.config_change.ConfigDegradation` — the exact shape task
``t3``'s module docstring names as "the shapes your records fold into" — so a
host counting degradations across the whole config-not-minds tier (admission
refusals from :mod:`embodiment.config_change`, and ledger-level failures from
here) reads ONE stream, never two.

What this module does NOT do
--------------------------------
No thread, no clock, no background review, no per-seat quiescence gate (task
``t4``'s), no revert-to-baseline policy or ratchet re-evaluation (task
``t6``'s), no rendering of a seat's effective configuration (task ``t7``'s,
"derived from the applied-change ledger alone" — :attr:`ConfigLedger.entries`
is exactly the "alone" this ledger offers, and nothing here interprets it into
a seat's live prompt or tool surface). :meth:`ConfigLedger.record_applied` and
:meth:`ConfigLedger.record_reverted` are the only two verbs that mutate state,
because those are the only two states an *applied-change* ledger has anything
to say about; ``proposed``/``verified``/``rejected`` are pure translations a
caller gets straight from :mod:`embodiment.config_events` without this module
in the loop at all.

Stdlib only, plus :mod:`embodiment.config_change` and
:mod:`embodiment.config_events` (constraint C1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import embodiment.config_events as config_events
from embodiment.config_change import ConfigChange, ConfigDegradation

__all__ = [
    "CONFIG_LEDGER_KIND",
    "CONFIG_LEDGER_SCHEMA_VERSION",
    "LEDGER_STATE_APPLIED",
    "LEDGER_STATE_REVERTED",
    "LEDGER_STATES",
    "CONFIG_LEDGER_DEGRADED_LOAD_FAILED",
    "CONFIG_LEDGER_DEGRADED_SAVE_FAILED",
    "CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION",
    "CONFIG_LEDGER_DEGRADED_MALFORMED_ENTRIES",
    "CONFIG_LEDGER_DEGRADED_UNKNOWN_REVERT",
    "CONFIG_LEDGER_DEGRADED_ALREADY_REVERTED",
    "CONFIG_LEDGER_DEGRADATION_CODES",
    "ConfigPersistence",
    "LedgerEntry",
    "ConfigLedger",
]

# ── the payload markers (c31/h21) ───────────────────────────────────────────────

#: This schema's own name. Absent from every advisory-era payload, by
#: construction — :meth:`embodiment.scope.ScopeRegister.to_dict` never sets it.
CONFIG_LEDGER_KIND = "config-ledger"
#: Stamped on every persisted payload. Refused, never guessed at, when it does
#: not match exactly — this ledger fails CLOSED on drift, unlike
#: ``embodiment.capability``'s catalog version (which is lenient on read
#: because refusing a readable *declaration* over a number would strand a
#: host's whole capability surface; a config-change ledger has the opposite
#: honesty condition, stated explicitly by ``h21``).
CONFIG_LEDGER_SCHEMA_VERSION = 1

# ── the two ledger states ───────────────────────────────────────────────────────

#: A change unit is now in force for the seat it targets.
LEDGER_STATE_APPLIED = "applied"
#: A previously applied change was reverted. The entry that recorded it stays in
#: the ledger's history — reverting does not erase that the change once
#: governed, only that it no longer does (task ``t7``'s introspection needs both
#: facts to explain a seat's history, not only its current state).
LEDGER_STATE_REVERTED = "reverted"
LEDGER_STATES = (LEDGER_STATE_APPLIED, LEDGER_STATE_REVERTED)

# ── the ledger's own degradation vocabulary ─────────────────────────────────────
#
# Prefixed ``config-ledger-`` so it cannot collide with config_change.py's
# ``config-change-`` codes even though both fold into the same
# ConfigDegradation stream — one code per distinct FIX, config_change.py's rule,
# inherited here.

#: ``ConfigPersistence.load()`` raised. Writing is disabled for the drive.
CONFIG_LEDGER_DEGRADED_LOAD_FAILED = "config-ledger-load-failed"
#: ``ConfigPersistence.save()`` raised. Further writes are disabled.
CONFIG_LEDGER_DEGRADED_SAVE_FAILED = "config-ledger-save-failed"
#: The payload was unreadable, or its ``kind``/``config_schema_version`` marker
#: did not match exactly — including every advisory-era payload, which never
#: carries this schema's ``kind`` marker at all.
CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION = "config-ledger-unknown-schema-version"
#: The markers matched but ``entries`` was present and **not** a sequence. A
#: distinct code from an unknown version on purpose: the fix differs. An unknown
#: version means *this is not our payload*; a malformed ``entries`` means it is
#: ours and it is damaged, which is the case where overwriting would destroy
#: history rather than replace a stranger's file.
CONFIG_LEDGER_DEGRADED_MALFORMED_ENTRIES = "config-ledger-malformed-entries"
#: A revert named a ``change_id`` this ledger has no applied record of.
CONFIG_LEDGER_DEGRADED_UNKNOWN_REVERT = "config-ledger-revert-unknown-change"
#: A revert named a ``change_id`` that was already reverted. A no-op, recorded
#: rather than silently repeated.
CONFIG_LEDGER_DEGRADED_ALREADY_REVERTED = "config-ledger-already-reverted"

CONFIG_LEDGER_DEGRADATION_CODES = (
    CONFIG_LEDGER_DEGRADED_LOAD_FAILED,
    CONFIG_LEDGER_DEGRADED_SAVE_FAILED,
    CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION,
    CONFIG_LEDGER_DEGRADED_MALFORMED_ENTRIES,
    CONFIG_LEDGER_DEGRADED_UNKNOWN_REVERT,
    CONFIG_LEDGER_DEGRADED_ALREADY_REVERTED,
)

#: Cap on a recorded reason's text (``scope.py`` / ``config_change.py``'s value).
_MAX_REASON_LEN = 500


# ── coercion helpers (never raise; own copies, see the module docstring) ───────


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


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    """``getattr`` that cannot raise. A hostile ``change`` reads as absent.

    ``record_applied``/``record_reverted`` are the two verbs a live drive calls
    while a caller is still assembling its own lifecycle (task ``t4`` has not
    landed in this wave) — reading defensively here is what keeps a
    not-quite-``ConfigChange`` object a recorded degradation-shaped read rather
    than an exception into whatever called this ledger, matching every other
    admission path in this tier (``embodiment.scope``'s ``_attr``,
    ``embodiment.scope_events``'s ``_read``).
    """
    try:
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001  # an unreadable attribute is simply absent
        return default


def _to_dict(change: Any) -> dict[str, Any]:
    """``change.to_dict()``, defensively. A hostile change renders as ``{}``."""
    try:
        result = change.to_dict()
    except Exception:  # noqa: BLE001  # an unrenderable change carries no unit, not a crash
        return {}
    return result if isinstance(result, dict) else {}


# ── the persistence port (re-declared, not imported — see the module docstring) ─


@dataclass(frozen=True)
class ConfigPersistence:
    """The config ledger's host-visible seam: two callables, and no backend.

    Structurally identical to :class:`~embodiment.scoped_run.ScopePersistence`
    and re-declared rather than imported. Frozen for the same reason that class
    is: a host cannot bolt a third capability onto a port after construction, so
    the surface stays these two fields forever.

    Fields
    ------
    load:
        ``() -> payload`` — called **once**, when the ledger is constructed.
        Anything it returns that this module cannot read as a valid,
        current-schema ledger payload restores an EMPTY ledger with one
        recorded :class:`~embodiment.config_change.ConfigDegradation`
        (:data:`CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION`) and disables writing
        for the rest of the drive. Anything it *raises* is recorded
        (:data:`CONFIG_LEDGER_DEGRADED_LOAD_FAILED`) and disables writing for
        the same reason: a store this module could not even read is not safe
        to overwrite.
    save:
        ``(payload) -> None`` — called after :meth:`ConfigLedger.record_applied`
        or :meth:`ConfigLedger.record_reverted` actually changes the ledger's
        state. A raise is recorded once
        (:data:`CONFIG_LEDGER_DEGRADED_SAVE_FAILED`) and disables further
        writes: durability is lost, delivery is not, and the record says
        exactly that.

    Both are optional. A port with neither is not a lane at all — the ledger
    still works, in memory, for the life of the process.
    """

    load: Optional[Callable[[], Any]] = None
    save: Optional[Callable[[dict[str, Any]], None]] = None

    @property
    def wired(self) -> bool:
        """Whether this port can do anything. ``False`` ⇒ no durable ledger."""
        return self.load is not None or self.save is not None


# ── the ledger entry ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerEntry:
    """One applied-or-reverted fact, with enough provenance to explain itself.

    ``unit`` is the applied change's own :meth:`~embodiment.config_change.
    ConfigChange.to_dict` — carried verbatim, not re-derived, so a reader (task
    ``t7``'s introspection) never has to trust a second summary of what a
    change actually said. A reverted entry copies its prior applied entry's
    ``seat``/``target``/``origin``/``unit`` (never re-reads a live
    :class:`~embodiment.config_change.ConfigChange` that may no longer exist)
    so the ledger alone still explains what was reverted after the fact.
    """

    state: str
    change_id: str
    seat: str
    target: str
    origin: str
    reason: str = ""
    step_index: int = 0
    unit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "change_id": self.change_id,
            "seat": self.seat,
            "target": self.target,
            "origin": self.origin,
            "reason": self.reason,
            "step_index": self.step_index,
            "unit": dict(self.unit),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["LedgerEntry"]:
        """Rebuild one entry. Never raises; an unreadable entry is ``None``.

        A ledger payload that passed the top-level schema check can still
        carry a corrupted or hand-edited entry — this is where THAT gets
        caught, per-entry, rather than failing the whole ledger load over one
        bad row.
        """
        if not isinstance(data, dict):
            return None
        state = _text(data.get("state")).strip()
        if state not in LEDGER_STATES:
            return None
        change_id = _text(data.get("change_id")).strip()
        if not change_id:
            return None
        unit = data.get("unit")
        if not isinstance(unit, dict):
            unit = {}
        return cls(
            state=state,
            change_id=change_id,
            seat=_text(data.get("seat")),
            target=_text(data.get("target")),
            origin=_text(data.get("origin")),
            reason=_text(data.get("reason")),
            step_index=_int(data.get("step_index"), 0),
            unit=dict(unit),
        )


# ── fail-closed payload reading (c31/h21) ───────────────────────────────────────


def _mismatch_reason(data: dict[str, Any], kind: Any, version: Any) -> str:
    """Name the fix. Special-cased for the advisory shape, because that is the
    one this task's fixture proves and the one operators will actually hit."""
    looks_advisory = (
        kind is None
        and isinstance(data.get("accepted"), list)
        and "lane" in data
        and "schema_version" in data
    )
    if looks_advisory:
        accepted_count = len(data.get("accepted") or [])
        return (
            "the persisted payload looks like an advisory-era "
            "ScopePersistence/ScopeRegister payload (schema_version="
            f"{data.get('schema_version')!r}, lane={data.get('lane')!r}, "
            f"{accepted_count} accepted directive(s)), not a config-ledger "
            f"payload (kind={CONFIG_LEDGER_KIND!r}, config_schema_version="
            f"{CONFIG_LEDGER_SCHEMA_VERSION}); the config-not-minds tier does "
            "not read an advisory directive chain under new semantics — point "
            "this host's ConfigPersistence at a fresh store, or migrate the "
            "payload, before wiring the config lane; nothing was "
            "reinterpreted, the ledger loaded empty this drive"
        )
    return (
        f"the persisted payload names kind={kind!r}, config_schema_version="
        f"{version!r}; this build expects kind={CONFIG_LEDGER_KIND!r}, version="
        f"{CONFIG_LEDGER_SCHEMA_VERSION} and refuses to guess at an unknown or "
        "missing version — migrate the payload to the current schema, or "
        "point ConfigPersistence at a fresh store; nothing was reinterpreted, "
        "the ledger loaded empty this drive"
    )


def _ledger_from_payload(
    data: Any,
) -> tuple[tuple[LedgerEntry, ...], Optional[ConfigDegradation]]:
    """Read a persisted payload. Never raises. ``(entries, degradation)``.

    Exactly one of two outcomes: a (possibly empty) tuple of entries with no
    degradation, or an empty tuple with exactly ONE degradation naming the fix
    — the countable form of "refused, never a silent reinterpretation".

    ``None`` is read as "nothing persisted yet" — the conventional signal a
    fresh store gives on its first read (a file that does not exist yet, a key
    nobody has written) — and restores an empty ledger with NO degradation,
    the same leniency :meth:`embodiment.scope.ScopeRegister.from_dict` already
    extends to it. That is a deliberately narrower exception than that
    method's: everything else unreadable (a string, a number, a list, a bool,
    or a dict with the wrong ``kind``/``config_schema_version``) is refused
    fail-closed below, which is the whole point of ``h21`` — only the
    "nothing here yet" case is not itself evidence of anything wrong.
    """
    if data is None:
        return (), None
    if not isinstance(data, dict):
        return (), ConfigDegradation(
            code=CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION,
            reason=(
                f"the persisted payload was {type(data).__name__}, not a mapping; "
                "a config-ledger payload is always a JSON object, so this is "
                "refused rather than guessed at — point ConfigPersistence at a "
                "fresh store, or migrate the payload, before wiring the config "
                "lane; nothing was reinterpreted, the ledger loaded empty this drive"
            )[:_MAX_REASON_LEN],
        )
    kind = data.get("kind")
    version = data.get("config_schema_version")
    if kind != CONFIG_LEDGER_KIND or version != CONFIG_LEDGER_SCHEMA_VERSION:
        return (), ConfigDegradation(
            code=CONFIG_LEDGER_DEGRADED_UNKNOWN_VERSION,
            reason=_mismatch_reason(data, kind, version)[:_MAX_REASON_LEN],
        )
    if "entries" not in data:
        # A fresh store that has been written to once with no entries yet. Not
        # damage, so not a degradation.
        return (), None
    raw_entries = data["entries"]
    if not isinstance(raw_entries, (list, tuple)):
        # Ours, and damaged. Refusing here is what stops the next applied change
        # from overwriting whatever history the payload still holds — the fix
        # differs from an unknown version, so the code does too.
        return (), ConfigDegradation(
            code=CONFIG_LEDGER_DEGRADED_MALFORMED_ENTRIES,
            reason=(
                "the persisted payload carries this ledger's kind and schema "
                f"version but its 'entries' is {type(raw_entries).__name__}, not "
                "a list — the ledger loaded empty and writing is disabled for "
                "this drive so the damaged payload is not overwritten; repair or "
                "replace the store, then re-arm"
            )[:_MAX_REASON_LEN],
        )
    entries = tuple(
        entry for entry in (LedgerEntry.from_dict(raw) for raw in raw_entries) if entry is not None
    )
    return entries, None


def _revert_lookup(
    entries: list[LedgerEntry], change_id: str
) -> tuple[Optional[LedgerEntry], bool]:
    """The most recent entry naming *change_id*, if it is still applied.

    Walking from the end finds the change's CURRENT state: if the most recent
    entry naming it already reverted it, ``(None, True)`` — nothing further to
    do. If the most recent entry applied it, that entry is what a revert
    reverts, ``(entry, False)``. Never having been applied is ``(None, False)``.
    """
    for entry in reversed(entries):
        if entry.change_id == change_id:
            if entry.state == LEDGER_STATE_REVERTED:
                return None, True
            return entry, False
    return None, False


# ── the ledger ────────────────────────────────────────────────────────────────


class ConfigLedger:
    """The applied-change ledger: history of applied/reverted changes.

    Persists through a host-owned :class:`ConfigPersistence` port when one is
    supplied; runs in memory for the life of the process otherwise. Fail-closed
    on load: an unreadable or wrongly-versioned payload is refused with exactly
    one recorded :class:`~embodiment.config_change.ConfigDegradation`, writing
    is disabled for the drive, and the ledger starts empty — never a crash,
    never a silent reinterpretation (``c31``/``h21``).
    """

    def __init__(self, persistence: Optional[ConfigPersistence] = None) -> None:
        self._persistence = persistence
        self._entries: list[LedgerEntry] = []
        self._degradations: list[ConfigDegradation] = []
        self._write_disabled = False
        self._load()

    # -- read-only views ----------------------------------------------------

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        """The whole recorded history, in the order it happened."""
        return tuple(self._entries)

    @property
    def degradations(self) -> tuple[ConfigDegradation, ...]:
        """Ledger-level degradations: load/save failures, a schema refusal."""
        return tuple(self._degradations)

    @property
    def write_disabled(self) -> bool:
        """Whether persistence writes are disabled for the rest of this drive."""
        return self._write_disabled

    # -- loading (called once, at construction) ------------------------------

    def _load(self) -> None:
        if self._persistence is None or self._persistence.load is None:
            return
        try:
            payload = self._persistence.load()
        except Exception as exc:  # noqa: BLE001  # a raising load degrades, never crashes
            self._degrade(
                CONFIG_LEDGER_DEGRADED_LOAD_FAILED,
                f"ConfigPersistence.load() raised {exc!r}; overwriting a store "
                "that could not be read would destroy it rather than degrade it, "
                "so writing is disabled for the rest of this drive and the "
                "ledger starts empty",
            )
            self._write_disabled = True
            return
        entries, degradation = _ledger_from_payload(payload)
        if degradation is not None:
            self._degradations.append(degradation)
            # A payload this module COULD read, but not as its own schema, is
            # exactly the case h21 forbids reinterpreting — so writing stays
            # off too, the same as the raise path: this drive does not touch
            # a store it could not confidently make sense of.
            self._write_disabled = True
            return
        self._entries = list(entries)

    def _degrade(self, code: str, reason: str) -> ConfigDegradation:
        entry = ConfigDegradation(code=code, reason=reason[:_MAX_REASON_LEN])
        self._degradations.append(entry)
        return entry

    # -- the two ledger-mutating verbs ---------------------------------------

    def record_applied(
        self, change: ConfigChange, *, step_index: int = 0
    ) -> config_events.ConfigEvent:
        """Record that *change* is now in force. Persists; returns its event."""
        entry = LedgerEntry(
            state=LEDGER_STATE_APPLIED,
            change_id=_text(_attr(change, "change_id")),
            seat=_text(_attr(change, "seat")),
            target=_text(_attr(change, "target")),
            origin=_text(_attr(change, "origin")),
            reason=_text(_attr(change, "reason")),
            step_index=step_index,
            unit=_to_dict(change),
        )
        self._entries.append(entry)
        self._persist()
        return config_events.applied_event(change, step_index=step_index)

    def record_reverted(
        self, change_id: str, *, reason: str = "", step_index: int = 0
    ) -> config_events.ConfigEvent:
        """Record that the applied change named *change_id* is reverted.

        A ``change_id`` this ledger never applied, or already reverted, is a
        recorded degradation rather than a raise — reverting nothing is not an
        error the caller should have to guard against, but it is not silent
        either.
        """
        change_id = _text(change_id).strip()
        prior, already = _revert_lookup(self._entries, change_id)
        if prior is None:
            code = (
                CONFIG_LEDGER_DEGRADED_ALREADY_REVERTED
                if already
                else CONFIG_LEDGER_DEGRADED_UNKNOWN_REVERT
            )
            what = "already reverted" if already else "has no applied record of"
            degradation = self._degrade(
                code,
                f"revert requested for change_id {change_id!r}, which this "
                f"ledger {what}; nothing was reverted",
            )
            return config_events.degradation_event(degradation, step_index=step_index)
        revert_reason = reason or f"reverted {change_id}"
        entry = LedgerEntry(
            state=LEDGER_STATE_REVERTED,
            change_id=change_id,
            seat=prior.seat,
            target=prior.target,
            origin=prior.origin,
            reason=revert_reason,
            step_index=step_index,
            unit=prior.unit,
        )
        self._entries.append(entry)
        self._persist()
        return config_events.reverted_event(
            change_id,
            seat=prior.seat,
            target=prior.target,
            origin=prior.origin,
            reason=revert_reason,
            step_index=step_index,
        )

    # -- persistence ----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The whole ledger as a JSON-ready payload — this schema's currency."""
        return {
            "kind": CONFIG_LEDGER_KIND,
            "config_schema_version": CONFIG_LEDGER_SCHEMA_VERSION,
            "entries": [entry.to_dict() for entry in self._entries],
        }

    def _persist(self) -> None:
        if self._write_disabled or self._persistence is None or self._persistence.save is None:
            return
        try:
            self._persistence.save(self.to_dict())
        except Exception as exc:  # noqa: BLE001  # a raising save degrades, never crashes
            self._degrade(
                CONFIG_LEDGER_DEGRADED_SAVE_FAILED,
                f"ConfigPersistence.save() raised {exc!r}; durability is lost "
                "for the rest of this drive and further writes are disabled, "
                "delivery is not affected",
            )
            self._write_disabled = True
