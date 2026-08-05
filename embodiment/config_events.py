"""embodiment.config_events — translate config-lane activity into ObserverFn events.

Task ``t5`` (issue #75, spec claims ``c8``/``h8``). The requirement this module
carries: "emit config-change events (proposed/validated/applied/rejected/
reverted) through the existing ``scope_events`` translation pattern" — the
verifier state was later renamed ``verified`` in the plan's own instruction for
task ``t4`` ("implement propose/verified/applied as distinct recorded states
(extend c8's event kinds with 'verified')"), so the five kinds this module
mints are ``proposed`` / ``verified`` / ``applied`` / ``rejected`` / ``reverted``,
plus one ``degradation`` kind for a ledger-level failure that names no single
change (a persistence load/save failure, an unreadable payload).

Why THE PATTERN and not the module
-----------------------------------
"Follow ``scope_events.py``'s translation pattern" is an instruction about
*shape* — pure functions, defensive reads, one envelope builder — not an
instruction to import that module. :mod:`embodiment.config_change` (task
``t3``) established the discipline this whole tier holds to: no import of
``embodiment.scope``, ``embodiment.scoped_run``, ``embodiment.strategist_runner``
or ``embodiment.scope_events``, proved by ``tests/test_config_change.py``'s
``TestCitedNotCoupled`` — because the advisory lane has to stay byte-stable as
the comparator arm the config-change arm will be measured against (task t13),
and an import edge is a reason to edit it. This module inherits that same
discipline rather than re-litigating it: it imports
:mod:`embodiment.config_change` (the shapes it translates) and nothing else
from this package.

:class:`ConfigEvent` is therefore **re-declared**, field-for-field identical to
:class:`~embodiment.scope_events.ScopeEvent` (itself re-declared from
:class:`~embodiment.loop.LoopEvent`, for the identical reason stated in that
module's own docstring): a host's existing ``ObserverFn`` — an
:class:`~embodiment.events.EventEmitter` included — accepts one with no
adapter, and the two sides of a seam each stay free to remain importable
without pulling the other in.

No thread, no clock, no store, no observer reference
-------------------------------------------------------
Every function here is a pure, defensive read: no I/O, no lock, no thread, and
no attribute read that can raise — the same idiom
:mod:`embodiment.scope_events` and :mod:`embodiment.config_change` both use,
for the identical reason (constraint C3: a translator fed a hostile or
malformed object still returns a valid :class:`ConfigEvent` rather than
raising into whatever called it). This module holds no ``observer=`` keyword
of its own and calls nothing: the *composition* that owns an observer and
decides when to call these builders is a later task's job (task ``t4``'s
propose→verify→apply lifecycle calls the state-transition builders directly;
:mod:`embodiment.config_ledger`, task ``t5``'s other half, calls
:func:`applied_event` / :func:`reverted_event` / :func:`degradation_event`
itself and returns what they built, never forwarding to a callback of its
own) — mirroring exactly how :mod:`embodiment.scope_events` holds no
dependency on :mod:`embodiment.scoped_run`, the one module that actually wires
a host's observer.

``proposed_event`` / ``rejected_event`` translate REAL t3 shapes
--------------------------------------------------------------------
:func:`proposed_event` takes a real :class:`~embodiment.config_change.ConfigChange`
and :func:`rejected_event` a real :class:`~embodiment.config_change.ConfigRefusal`
— both already exist, built and returned by
:func:`~embodiment.config_change.change_from_payload` /
:func:`~embodiment.config_change.admit_changes`. There was nothing to invent
for those two states.

``verified`` / ``applied`` / ``reverted`` have no shipped shape to translate
yet — task ``t4`` (the propose→verify→apply lifecycle with per-seat
quiescence) has not landed in this wave, so :func:`verified_event` and
:func:`applied_event` take a :class:`~embodiment.config_change.ConfigChange`
plus the few scalars a verification/apply step actually knows
(``passed``/``suite`` for verify; nothing extra for apply, since "this change
is now in force" needs no more than the change's own identity), and
:func:`reverted_event` takes plain scalars rather than an object at all — a
revert names a ``change_id`` that may no longer have a live
:class:`~embodiment.config_change.ConfigChange` instance behind it (task
``t6``'s revert-to-baseline works from the ledger's own history, task ``t5``'s
:mod:`embodiment.config_ledger`). A later task composing these into a fuller
lifecycle object is free to call these builders exactly as written.

Stdlib only, plus :mod:`embodiment.config_change` (constraint C1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from embodiment.config_change import ConfigChange, ConfigDegradation, ConfigRefusal

__all__ = [
    "CONFIG_EVENT_PROPOSED",
    "CONFIG_EVENT_VERIFIED",
    "CONFIG_EVENT_APPLIED",
    "CONFIG_EVENT_REJECTED",
    "CONFIG_EVENT_REVERTED",
    "CONFIG_EVENT_DEGRADATION",
    "CONFIG_EVENT_KINDS",
    "ConfigEvent",
    "proposed_event",
    "verified_event",
    "applied_event",
    "rejected_event",
    "reverted_event",
    "degradation_event",
]

# ── the vocabulary (task t5, c8/h8) ─────────────────────────────────────────────

CONFIG_EVENT_PROPOSED = "config.change.proposed"
CONFIG_EVENT_VERIFIED = "config.change.verified"
CONFIG_EVENT_APPLIED = "config.change.applied"
CONFIG_EVENT_REJECTED = "config.change.rejected"
CONFIG_EVENT_REVERTED = "config.change.reverted"
#: A ledger-level failure that names no single change unit — a persistence
#: load/save failure, an unreadable or wrongly-versioned payload (c31/h21).
CONFIG_EVENT_DEGRADATION = "config.degradation"

#: The complete set. A host's ``EventEmitter`` maps each onto
#: ``f"embodiment.{kind}"`` exactly as it does for the actor loop's own kinds
#: and for ``embodiment.scope_events``' ``scope.*`` kinds.
CONFIG_EVENT_KINDS = (
    CONFIG_EVENT_PROPOSED,
    CONFIG_EVENT_VERIFIED,
    CONFIG_EVENT_APPLIED,
    CONFIG_EVENT_REJECTED,
    CONFIG_EVENT_REVERTED,
    CONFIG_EVENT_DEGRADATION,
)


@dataclass(frozen=True)
class ConfigEvent:
    """One config-lane occurrence, offered to the injected observer.

    Field-for-field identical to :class:`~embodiment.scope_events.ScopeEvent`
    (see the module docstring) so a host's existing ``ObserverFn`` accepts one
    with no adapter. ``kind`` is always one of :data:`CONFIG_EVENT_KINDS`;
    branch on it, never on ``detail``'s free text.
    """

    kind: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ── defensive reads (never raise; own copies — see the module docstring) ──────


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


def _int(value: Any, default: int = 0) -> int:
    """Best-effort ``int``; anything uncoercible falls back to *default*."""
    try:
        return int(value)
    except Exception:  # noqa: BLE001  # a junk number is the default, never a crash
        return default


#: :func:`_build`'s envelope, defaulted — every key present on every event this
#: module builds, honestly ``""``/``0``/``False``/``None`` where a given
#: occurrence has nothing to say (the same idiom ``scope_events._build`` uses).
_ENVELOPE_DEFAULTS: dict[str, Any] = {
    "change_id": "",
    "seat": "",
    "target": "",
    "origin": "",
    "code": "",
    "reason": "",
    "step_index": 0,
    "model_turns": 0,
    "model": "",
    "role": "",
    "applied": False,
    "passed": None,
}


def _build(kind: str, *, detail: str = "", **fields: Any) -> ConfigEvent:
    """The one place a :class:`ConfigEvent` is constructed."""
    data: dict[str, Any] = dict(_ENVELOPE_DEFAULTS)
    data.update(fields)
    data["change_id"] = _text(data["change_id"])
    data["seat"] = _text(data["seat"])
    data["target"] = _text(data["target"])
    data["origin"] = _text(data["origin"])
    data["code"] = _text(data["code"])
    data["reason"] = _text(data["reason"])
    data["step_index"] = _int(data["step_index"], 0)
    data["model_turns"] = _int(data["model_turns"], 0)
    data["model"] = _text(data["model"])
    data["role"] = _text(data["role"])
    data["applied"] = bool(data["applied"])
    if data["passed"] is not None:
        data["passed"] = bool(data["passed"])
    return ConfigEvent(kind=kind, detail=_text(detail) or data["reason"], data=data)


# ── builders: the change-unit lifecycle ────────────────────────────────────────


def proposed_event(
    change: ConfigChange, *, step_index: int = 0, model: str = "", role: str = ""
) -> ConfigEvent:
    """A change unit was offered and admitted — not yet verified or applied."""
    return _build(
        CONFIG_EVENT_PROPOSED,
        change_id=_read(change, "change_id"),
        seat=_read(change, "seat"),
        target=_read(change, "target"),
        origin=_read(change, "origin"),
        reason=_read(change, "reason") or "a configuration change was proposed",
        step_index=step_index,
        model=model,
        role=role,
    )


def verified_event(
    change: ConfigChange,
    *,
    passed: bool,
    suite: str = "",
    reason: str = "",
    step_index: int = 0,
) -> ConfigEvent:
    """A proposed change was run against its per-type verification suite.

    Fires either way — a pass and a fail both completed a verification, exactly
    as :meth:`~embodiment.scope_events.review_completed_event` fires "regardless
    of what ``scoped_run`` goes on to do with it". ``passed`` is what a caller
    branches on; the drone-smoke precedent is that a failed suite is recorded,
    never silently retried and never silently applied.
    """
    verdict = "passed" if passed else "failed"
    return _build(
        CONFIG_EVENT_VERIFIED,
        change_id=_read(change, "change_id"),
        seat=_read(change, "seat"),
        target=_read(change, "target"),
        origin=_read(change, "origin"),
        reason=reason or f"the {suite or 'verification'} suite {verdict} for this change",
        step_index=step_index,
        passed=bool(passed),
    )


def applied_event(change: ConfigChange, *, step_index: int = 0) -> ConfigEvent:
    """A verified change unit is now in force for the seat it targets."""
    return _build(
        CONFIG_EVENT_APPLIED,
        change_id=_read(change, "change_id"),
        seat=_read(change, "seat"),
        target=_read(change, "target"),
        origin=_read(change, "origin"),
        reason=_read(change, "reason") or "the change is now in force",
        step_index=step_index,
        applied=True,
    )


def rejected_event(refusal: ConfigRefusal, *, step_index: int = 0) -> ConfigEvent:
    """A change unit was refused whole — admission time, never partial.

    Takes a real :class:`~embodiment.config_change.ConfigRefusal`, the object
    :func:`~embodiment.config_change.change_from_payload` already returns for
    every refused offer, so nothing here re-derives what was refused.
    """
    return _build(
        CONFIG_EVENT_REJECTED,
        change_id=_read(refusal, "change_id"),
        seat=_read(refusal, "seat"),
        target=_read(refusal, "target"),
        origin=_read(refusal, "origin"),
        code=_read(refusal, "code"),
        reason=_read(refusal, "reason"),
        step_index=step_index,
        applied=False,
    )


def reverted_event(
    change_id: str,
    *,
    seat: str = "",
    target: str = "",
    origin: str = "",
    reason: str = "",
    step_index: int = 0,
) -> ConfigEvent:
    """A previously applied change was reverted. Plain scalars — see the module
    docstring on why this builder takes no object."""
    return _build(
        CONFIG_EVENT_REVERTED,
        change_id=_text(change_id),
        seat=seat,
        target=target,
        origin=origin,
        reason=reason or "reverted to the prior configuration",
        step_index=step_index,
        applied=False,
    )


def degradation_event(entry: ConfigDegradation, *, step_index: int = 0) -> ConfigEvent:
    """Translate one ledger-level :class:`~embodiment.config_change.ConfigDegradation`.

    Duck-typed on purpose: a plain :class:`ConfigDegradation` carries no
    ``change_id``/``origin`` (only its :class:`ConfigRefusal` subclass does), so
    ``_read`` defaults both to ``""`` rather than raising on the missing field.
    """
    return _build(
        CONFIG_EVENT_DEGRADATION,
        change_id=_read(entry, "change_id", ""),
        seat=_read(entry, "seat"),
        target=_read(entry, "target"),
        origin=_read(entry, "origin", ""),
        code=_read(entry, "code"),
        reason=_read(entry, "reason"),
        step_index=step_index,
        model_turns=_int(_read(entry, "model_turns"), 0),
        applied=False,
    )
