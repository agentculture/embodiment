#!/usr/bin/env python3
"""seats — seat wiring: which lobes role backs which tier (plan task ``t7``).

Three tiers, three seats, three lobes roles — the recorded operator decision
(spec claim ``c2``, decision ``q1``), as a table before it is code:

=============  ===============  ==============  ==============================
tier           seat              lobes role      reference model, in CLAUDE.md
=============  ===============  ==============  ==============================
Strategy       ``strategist``    ``cortex``      dense 27B (local)
Operation      ``actor``         ``worker``      35B-A3B (proxied)
Interaction    ``senses``        ``senses``      12B (host-supplied)
=============  ===============  ==============  ==============================

The actual model ids are deliberately not named here (or anywhere else in this
module — a test walks the whole file to say so): they live in the host's
``/capabilities`` payload and in CLAUDE.md's own rig table, never in code this
module's seat resolution could read back.

The **seat** is who is speaking (the provenance label a host records on a
:class:`~embodiment.scope.ScopeOutcome` or a
:class:`~embodiment.scoped_run.ScopeTransition`); the **lobes role** is which
gateway entry actually serves it. They are named separately on purpose: s11
of the spec's scope exploration records that "worker" alone is already
overloaded three ways in this repo (the bee-hive's Worker role,
``drone.py``'s worker, and ``presence_engine.py``'s "the worker that owns the
bounded tool loop" — the CORTEX). Keeping the seat name (``actor``) distinct
from the lobes role name (``worker``) sidesteps that collision rather than
adding a fourth meaning to an already-overloaded word.

What this module resolves, and how (honesty condition ``h2``)
---------------------------------------------------------------
:func:`resolve_seats` reads a lobes ``/capabilities``-shaped payload — a
mapping of role name to role entry, exactly the shape the gateway returns
(``docs/live-test-results/*/capabilities.json`` are committed examples) — and
looks each seat's role up **by the payload's own dict key**. Nothing here
reads ``entry["model"]`` (or any other field) to decide *which* seat a role
belongs to; the model id is carried through afterward as data a resolved
:class:`SeatDial` happens to also record, exactly the way
``embodiment/strategist_runner.py`` and ``embodiment/scope.py`` already treat
model and role as host-declared record fields rather than inputs to a
decision. ``tests/test_scope_seats.py`` proves this behaviourally, not just by
grep: swapping the worker entry's ``model`` field to look exactly like the
cortex entry's does not move it into the strategist seat.

What this module does NOT do
-----------------------------
* **It never dials the gateway.** *capabilities* is host-supplied — fetching
  ``/capabilities`` is the host's job (or a committed fixture's, in tests);
  resolving what it says is this module's. No module under ``examples/scope/``
  imports a transport (``tests/test_scopebench.py``'s ``TestNoLiveDial`` already
  parametrizes over every file in this directory, this one included).
* **It introduces no timeout constant.** Building the strategist's actual
  transport (an HTTP seam with a request timeout derived from a measured rate)
  is explicitly task ``t10``'s job, once the worker role has a dated rate entry
  of its own; wiring one here ahead of that measurement would be exactly the
  unmeasured, invented constant the CLAUDE.md load-bearing lesson warns
  against. A host that already has a strategist seam built (by whatever
  means) hands it to :func:`governed_by`.
* **It resolves the senses seat for completeness, and stops there.** The senses
  coordination loop stays in the host (spec non-goal, c30); this module reads
  the ``senses`` role's readiness like the other two but never constructs
  anything from it.

Degrade, never raise (constraint C3; acceptance criterion 2)
--------------------------------------------------------------
A missing role, a not-ready role, or a malformed role entry each leave the
affected seat ``None`` and add a :class:`SeatDegradation` naming exactly which
of the three happened. Nothing here raises, including under a hostile payload
(``capabilities`` that is not a mapping at all, or a role entry whose values
refuse to stringify or booleanize) — every read is guarded. A host that then
wires :func:`governed_by` gets a governor with ``strategist=None`` — the
*same* unarmed, byte-identical-to-``run()`` composition an actor-only host
gets by never touching the scope lane at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

__all__ = [
    "DEGRADED_ROLE_ABSENT",
    "DEGRADED_ROLE_MALFORMED",
    "DEGRADED_ROLE_NOT_READY",
    "ROLE_CORTEX",
    "ROLE_SENSES",
    "ROLE_WORKER",
    "SEAT_ACTOR",
    "SEAT_ROLES",
    "SEAT_SENSES",
    "SEAT_STRATEGIST",
    "SEATS",
    "SeatDegradation",
    "SeatDial",
    "SeatResolution",
    "governed_by",
    "resolve_seats",
]


# ── the lobes roles: /capabilities dict keys, never inferred from a model ────
#
# Declared locally rather than imported from ``examples/scope/scopebench.py``
# or ``examples/arch_arms.py`` (both of which declare the identical triple
# themselves) — the established convention in this repo for a handful of
# stable role-name strings is independent declaration per harness, not a
# shared import, so that no example family acquires a dependency on another's
# relocation (``scopebench.py``'s own comment on the same point). Cross-module
# agreement is pinned by test instead of by import.
ROLE_CORTEX = "cortex"
ROLE_WORKER = "worker"
ROLE_SENSES = "senses"

# ── the seats: who is speaking, independent of which role serves them ────────
#
# ``SEAT_STRATEGIST`` is deliberately the same string as
# ``embodiment.strategist_runner.STRATEGIST_ROLE`` — the provenance label a
# host passes as ``StrategistRunner(..., role=...)`` — so a resolved seat and
# the runner's own default agree without this module importing that one just
# for a string constant. ``tests/test_scope_seats.py`` pins the two names
# against each other so they cannot drift silently.
SEAT_STRATEGIST = "strategist"
SEAT_ACTOR = "actor"
SEAT_SENSES = "senses"
SEATS: tuple[str, ...] = (SEAT_STRATEGIST, SEAT_ACTOR, SEAT_SENSES)

#: The recorded operator decision (spec claim ``c2``, decision ``q1``), as
#: data: seat name -> lobes role name. Read by :func:`resolve_seats` to know
#: which ``/capabilities`` key to look under for each seat -- the ONLY use
#: this mapping is put to, so "resolved by role name" is this table, not a
#: separate piece of logic that could disagree with it.
SEAT_ROLES: Mapping[str, str] = {
    SEAT_STRATEGIST: ROLE_CORTEX,
    SEAT_ACTOR: ROLE_WORKER,
    SEAT_SENSES: ROLE_SENSES,
}

#: The role's ``/capabilities`` key is absent from the payload entirely.
DEGRADED_ROLE_ABSENT = "seat-role-absent"
#: The key is present but its value is not a mapping this module can read.
DEGRADED_ROLE_MALFORMED = "seat-role-malformed"
#: The role is advertised (a well-formed entry exists) but ``ready`` is falsy.
DEGRADED_ROLE_NOT_READY = "seat-role-not-ready"


@dataclass(frozen=True)
class SeatDegradation:
    """One seat that did not resolve, and exactly why. Recorded, never raised."""

    code: str
    seat: str
    role: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "seat": self.seat, "role": self.role, "reason": self.reason}


@dataclass(frozen=True)
class SeatDial:
    """One resolved seat: what a host needs to actually build its own seam.

    Every field here is carried straight off the ``/capabilities`` entry as
    DATA. This module reads none of them back to decide anything — that is
    exactly what honesty condition ``h2`` forbids, and
    ``tests/test_scope_seats.py`` proves it behaviourally rather than trusting
    this sentence.
    """

    seat: str
    role: str
    model: str
    endpoint: str
    hosted_by: str = ""
    proxied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "role": self.role,
            "model": self.model,
            "endpoint": self.endpoint,
            "hosted_by": self.hosted_by,
            "proxied": self.proxied,
        }


@dataclass(frozen=True)
class SeatResolution:
    """The whole seat-to-role resolution for one ``/capabilities`` payload.

    Never partially built by a raise: every seat is either a :class:`SeatDial`
    or ``None``, and every ``None`` has a matching :class:`SeatDegradation`
    in :attr:`degradations` naming which of the three ways it failed.
    """

    strategist: Optional[SeatDial] = None
    actor: Optional[SeatDial] = None
    senses: Optional[SeatDial] = None
    degradations: tuple[SeatDegradation, ...] = ()

    @property
    def has_strategist(self) -> bool:
        return self.strategist is not None

    @property
    def actor_only(self) -> bool:
        """``True`` whenever the strategy tier did not resolve.

        This is the property a host reads to decide whether it is running the
        byte-identical, unmodified ``run()`` path or a governed one -- and it
        is exactly the flag :func:`governed_by` acts on.
        """
        return not self.has_strategist

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategist": self.strategist.to_dict() if self.strategist is not None else None,
            "actor": self.actor.to_dict() if self.actor is not None else None,
            "senses": self.senses.to_dict() if self.senses is not None else None,
            "actor_only": self.actor_only,
            "degradations": [entry.to_dict() for entry in self.degradations],
        }


# ── guarded reads: every one of these degrades, none of them raises ──────────


def _has_role(capabilities: Any, role: str) -> bool:
    """Whether *role* is a key of *capabilities*. A hostile mapping reads ``False``."""
    if not isinstance(capabilities, Mapping):
        return False
    try:
        return role in capabilities
    except Exception:  # noqa: BLE001 -- a hostile ``__contains__`` degrades, never raises
        return False


def _get(mapping: Any, key: str, default: Any = None) -> Any:
    """``.get`` that cannot raise -- a hostile mapping degrades to *default*."""
    try:
        return mapping.get(key, default)
    except Exception:  # noqa: BLE001 -- a hostile ``.get`` degrades, never raises
        return default


def _text(value: Any) -> str:
    """Best-effort string coercion. A hostile ``__str__`` degrades to ``""``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001 -- a hostile value coerces to "", never raises
        return ""


def _truthy(value: Any) -> bool:
    """Best-effort truthiness. A hostile ``__bool__`` reads as not-ready."""
    try:
        return bool(value)
    except Exception:  # noqa: BLE001 -- a hostile value reads False, never raises
        return False


def _resolve_one(
    capabilities: Any, seat: str, role: str, degradations: list[SeatDegradation]
) -> Optional[SeatDial]:
    """Resolve one seat, by ``/capabilities`` dict key -- ``role`` -- only.

    Nothing below reads ``entry.get("model")`` (or any other field) to decide
    whether this entry belongs in *seat*; the lookup key is the only thing
    that determines that, which is the whole of what honesty condition ``h2``
    requires. The model, once a seat DOES resolve, rides along as a record
    field on :class:`SeatDial` -- read by a host afterward, not by this
    function beforehand.
    """
    if not _has_role(capabilities, role):
        degradations.append(
            SeatDegradation(
                DEGRADED_ROLE_ABSENT,
                seat,
                role,
                f"no {role!r} role on the gateway's /capabilities advert -- "
                f"the {seat} seat is not wired",
            )
        )
        return None
    entry = _get(capabilities, role)
    if not isinstance(entry, Mapping):
        degradations.append(
            SeatDegradation(
                DEGRADED_ROLE_MALFORMED,
                seat,
                role,
                f"the {role!r} role entry on /capabilities is not a mapping this "
                f"module can read -- the {seat} seat is not wired",
            )
        )
        return None
    if not _truthy(_get(entry, "ready", False)):
        degradations.append(
            SeatDegradation(
                DEGRADED_ROLE_NOT_READY,
                seat,
                role,
                f"the {role!r} role is advertised but ready=False -- "
                f"the {seat} seat is not wired",
            )
        )
        return None
    return SeatDial(
        seat=seat,
        role=role,
        model=_text(_get(entry, "model")),
        endpoint=_text(_get(entry, "endpoint")),
        hosted_by=_text(_get(entry, "hosted_by")),
        proxied=_truthy(_get(entry, "proxied", False)),
    )


def resolve_seats(capabilities: Any) -> SeatResolution:
    """Resolve the three seats from a lobes ``/capabilities``-shaped payload.

    *capabilities* is host-supplied: a mapping of role name to role entry,
    exactly the shape the gateway's ``/capabilities`` endpoint returns. This
    module never dials that endpoint itself -- fetching it is the host's job
    (or a committed fixture's, in ``tests/test_scope_seats.py``); resolving
    what it says is this function's.

    Never raises, on any input. A missing role, a not-ready role, or an
    entirely malformed *capabilities* argument (``None``, a list, a bare
    string) each leave the affected seat(s) ``None`` with a recorded
    :class:`SeatDegradation` -- never an exception (acceptance criterion 2).
    """
    degradations: list[SeatDegradation] = []
    strategist = _resolve_one(
        capabilities, SEAT_STRATEGIST, SEAT_ROLES[SEAT_STRATEGIST], degradations
    )
    actor = _resolve_one(capabilities, SEAT_ACTOR, SEAT_ROLES[SEAT_ACTOR], degradations)
    senses = _resolve_one(capabilities, SEAT_SENSES, SEAT_ROLES[SEAT_SENSES], degradations)
    return SeatResolution(
        strategist=strategist,
        actor=actor,
        senses=senses,
        degradations=tuple(degradations),
    )


def governed_by(
    seats: SeatResolution,
    *,
    strategist: Optional[Any] = None,
    projector: Optional[Any] = None,
    default_scope: Optional[Any] = None,
    identity: Optional[str] = None,
    controls: Optional[Any] = None,
) -> dict[str, Any]:
    """The kwargs a host passes straight to ``ScopeGovernor(**...)``.

    Returns a plain ``dict`` rather than constructing
    :class:`~embodiment.scoped_run.ScopeGovernor` itself: that module (like
    ``embodiment.scope`` and ``embodiment.strategist_runner``) is not yet on
    ``embodiment``'s curated public surface, and
    ``tests/test_demo_greenhouse.py::TestPublicApiOnly`` refuses any
    ``examples/`` import of an undocumented submodule -- the identical
    constraint ``examples/scope/subordinate.py`` already documents for
    ``embodiment.scope``'s shapes. A host application (unlike a file under
    ``examples/``) is free to do
    ``ScopeGovernor(**governed_by(seats, strategist=my_runner))`` directly;
    ``tests/test_scope_seats.py`` proves the returned mapping is exactly the
    real class's constructor keywords, and separately constructs the real
    :class:`~embodiment.scoped_run.ScopeGovernor` from it end to end.

    *strategist* is host-constructed -- typically an
    :class:`~embodiment.strategist_runner.StrategistRunner` already dialled to
    :attr:`SeatResolution.strategist`'s :class:`SeatDial` -- and is seated only
    when :attr:`SeatResolution.has_strategist` is true. A missing or not-ready
    cortex role means *strategist* is never seated even when the caller passed
    one in: the returned kwargs govern actor-only, which is what makes "a
    missing role degrades to actor-only" a property of the *composition*, not
    merely a fact recorded on :class:`SeatResolution` (acceptance criterion 2).
    Never raises: this function does no I/O and builds no object with a
    validating constructor.
    """
    return {
        "strategist": strategist if seats.has_strategist else None,
        "projector": projector,
        "default_scope": default_scope,
        "identity": identity,
        "controls": controls,
    }
