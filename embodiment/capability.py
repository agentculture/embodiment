"""The host-declared capability catalog — what a configuration change may SELECT from.

This module exists to answer one question the plan parked as risk ``r2``, and
the answer is the whole reason the file is separate from
:mod:`embodiment.config_change`:

    Honesty condition ``h11`` says *no configuration path can grant the
    strategist an ability the host has not itself wired: tools and permission
    changes select among host-offered capabilities, never mint new ones.* But
    the loop's tool seam is deliberately **not enumerable** —
    :class:`embodiment.loop.ToolExecutor` is one method, ``execute(name,
    arguments)``, and its docstring says in as many words that "the loop never
    constructs an executor and never enumerates tools: what the model may do is
    entirely the host's to decide". So there was nowhere to read a capability id
    from, and "select among host-offered ids" had no referent.

The decision, recorded here because this is where a later reader will look
-------------------------------------------------------------------------
**A capability catalog is a host DECLARATION, not a discovery.** The host
constructs a :class:`CapabilityCatalog` from what it already knows — the tool
schemas it puts on the wire, the permission surface its approval policy owns —
and hands it to the configuration lane. Four consequences, each of them held by
a test in ``tests/test_capability.py`` rather than by this paragraph:

1. **Nothing here reads a ``ToolExecutor``.** There is no ``from_executor``
   constructor and this module imports nothing from ``embodiment`` at all. That
   is not tidiness: deriving a catalog from a wildcard executor would *invent*
   the very authority ``h11`` forbids inventing, and would do it in the one
   place nobody would look. If the host cannot name a capability, the
   configuration lane cannot select it. Fail closed.
2. **A capability is a NAME, never a definition.** :class:`Capability` carries
   an id, a kind and two human-readable labels — and no schema, no parameter
   list, no callable, no endpoint. This is the first of the two layers that make
   "a free-form tool definition is a refused shape" structural: even a payload
   that got past the key ban could not be *carried*. (The second layer is
   :data:`embodiment.config_change.FORBIDDEN_CHANGE_KEYS`.) The shape mirrors
   :class:`embodiment.scope.ScopeResponsibility`, which is two strings for
   exactly the same reason — "there is no field here a dispatcher could bind
   to".
3. **Ids are opaque and host-chosen; they are only meaningful relative to the
   catalog that declared them.** embodiment mints no ids and imposes no naming
   scheme — a host's ids are whatever its own tool surface already calls things,
   which is what makes them stable *across restarts* for free: they are not
   generated, so there is nothing to regenerate. What is *not* free is stability
   **across hosts**, and the honest answer is that it does not exist: two hosts
   with different catalogs mean different ids. So a catalog names itself
   (:attr:`CapabilityCatalog.catalog_id`) and every change unit that selects
   from one records which one, so a unit authored against one host's catalog can
   never be silently applied against another's.
4. **Drift is detectable, not assumed away.** :attr:`CapabilityCatalog.fingerprint`
   is a sha256 over exactly the pairs authority depends on — ``(kind, id)``,
   sorted — so a relabelling does not invalidate anything and a revoked or
   re-kinded capability does. A change unit stamps the fingerprint it was
   validated against; ``config_change.revalidate`` re-checks it before apply.
   That is ``drone.py``'s staleness refusal, moved to runtime: the drone
   re-checks its declared assumptions before any code runs, and a configuration
   unit re-checks its declared capabilities before it is applied.

What this module deliberately does NOT do
-----------------------------------------
It does not decide whether a capability *should* be selected, does not apply
anything, and holds no policy. The host's injected ``ToolExecutor`` and its
approval policy are never bypassed and never consulted here: a selection is a
statement about which of the host's own names are in play, and the host remains
the only thing that can execute any of them (``shell-cli`` still owns the
approval-policy layer). Apply is task ``t4``'s; the ledger is ``t5``'s.

A malformed catalog is VISIBLE (constraint C3)
----------------------------------------------
The catalog is host-built, so a mistake in it is a host bug — and a host bug
that silently normalises itself away is exactly the "looks attentive, is not"
failure C3 exists for. So :attr:`CapabilityCatalog.problems` names blank ids,
unknown kinds and ambiguous duplicates, nothing is dropped on construction, and
an ambiguous id (declared twice under two kinds) declares **nothing**: it fails
closed rather than picking a winner.

Stdlib only: ``dataclasses``, ``hashlib`` and ``typing``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

__all__ = [
    "CAPABILITY_KIND_TOOL",
    "CAPABILITY_KIND_PERMISSION",
    "CAPABILITY_KINDS",
    "CATALOG_SCHEMA_VERSION",
    "Capability",
    "CapabilityCatalog",
    "EMPTY_CATALOG",
    "catalog_fingerprint",
]


# ── the two kinds ─────────────────────────────────────────────────────────────
#
# Two, because the authority lattice has two capability-shaped targets and they
# are not interchangeable: a tools change says what the seat may *call*, a
# permissions change says what it may *do*. Selecting a permission id as a tool
# is a refused shape rather than a coincidence that happens to work.

#: Something the seat may call — one entry of the tool surface the host wires.
CAPABILITY_KIND_TOOL = "tool"
#: Something the seat may do — one entry of the permission surface the host owns.
CAPABILITY_KIND_PERMISSION = "permission"
#: The closed set. A third kind means a third change-unit family, which is a
#: schema task, not a config edit — so this one IS enforced, unlike the
#: conventional vocabularies in ``scope.py``.
CAPABILITY_KINDS = (CAPABILITY_KIND_TOOL, CAPABILITY_KIND_PERMISSION)

#: Stamped on a serialized catalog (``drone.py``'s ``MANIFEST_SCHEMA_VERSION``
#: precedent). Recorded, not enforced on read: every field read is defensive and
#: unknown keys are ignored, so refusing a readable declaration over a number
#: would strand a host's whole capability surface.
CATALOG_SCHEMA_VERSION = 1

#: How the fingerprint renders one entry. Deliberately ``kind`` first: the
#: sort is then by kind and then by id, which is the order a reader of
#: :meth:`CapabilityCatalog.to_dict` would reconstruct by hand.
_FINGERPRINT_ROW = "{kind}\t{capability_id}"


def _text(value: Any) -> str:
    """Coerce *value* to text. Never raises — mirrors ``scope.py``'s ``_plain``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unreadable value is a blank, never a crash
        return ""


@dataclass(frozen=True)
class Capability:
    """One thing the host has already wired, named so a change unit can select it.

    Read the field list as an exclusion as much as an inclusion, exactly as
    :class:`embodiment.scope.ScopeDirective`'s is. There is no ``schema``, no
    ``parameters``, no ``handler`` and no ``endpoint`` here, so a catalog cannot
    be the vehicle for minting a capability any more than a change unit can be.

    Fields
    ------
    capability_id:
        The host's own name for this capability, opaque to embodiment. Required
        in practice — a blank id is reported by
        :attr:`CapabilityCatalog.problems` and declares nothing.
    kind:
        One of :data:`CAPABILITY_KINDS`.
    label / description:
        Human-readable, and deliberately **outside the fingerprint**: renaming a
        label is a documentation change, not an authority change, and
        invalidating live change units over one would train hosts to ignore
        staleness refusals.
    """

    capability_id: str = ""
    kind: str = ""
    label: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        for name in ("capability_id", "kind", "label", "description"):
            object.__setattr__(self, name, _text(getattr(self, name)).strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Capability":
        """Coerce a raw payload. Never raises; unknown keys are ignored."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            capability_id=_text(data.get("capability_id")),
            kind=_text(data.get("kind")),
            label=_text(data.get("label")),
            description=_text(data.get("description")),
        )


def _as_capabilities(value: Any) -> tuple[Capability, ...]:
    """Coerce a raw entries payload. Never raises.

    A bare string becomes **nothing**, not a one-element tuple: unlike a list of
    priorities, a capability declaration is never a scalar, so wrapping one
    would fabricate a capability out of a host's typo.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        entry if isinstance(entry, Capability) else Capability.from_dict(entry) for entry in value
    )


def catalog_fingerprint(entries: Sequence[Any]) -> str:
    """A sha256 over exactly the ``(kind, id)`` pairs authority depends on.

    Order-independent (the rows are sorted) and label-independent (labels are
    not rows), so the digest changes when — and only when — what the host offers
    changes. Never raises: junk entries coerce to blanks and are hashed as such.
    """
    rows = sorted(
        _FINGERPRINT_ROW.format(kind=cap.kind, capability_id=cap.capability_id)
        for cap in _as_capabilities(tuple(entries) if isinstance(entries, (list, tuple)) else ())
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilityCatalog:
    """Everything one host offers, declared by that host and nothing else.

    Args:
        entries: the declaration. Coerced on construction, never validated into
            silence — see :attr:`problems`.
        catalog_id: the host's name for this catalog. Change units record it, so
            a unit authored against one host's declaration cannot be applied
            against another's even when the two happen to fingerprint alike.

    Frozen and copied on construction, so a host that keeps mutating the list it
    built the catalog from cannot change a catalog a validation already holds —
    the same reason :class:`embodiment.scope.ScopeSnapshot` copies.
    """

    entries: tuple[Capability, ...] = ()
    catalog_id: str = ""
    _index: dict[str, tuple[str, ...]] = field(
        default_factory=dict, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", _as_capabilities(self.entries))
        object.__setattr__(self, "catalog_id", _text(self.catalog_id).strip())
        index: dict[str, list[str]] = {}
        for cap in self.entries:
            index.setdefault(cap.capability_id, []).append(cap.kind)
        object.__setattr__(self, "_index", {name: tuple(kinds) for name, kinds in index.items()})

    # ── reading the declaration ──────────────────────────────────────────────

    @property
    def ids(self) -> tuple[str, ...]:
        """Every declared id, in declaration order, blanks excluded."""
        return tuple(cap.capability_id for cap in self.entries if cap.capability_id)

    def ids_of_kind(self, kind: str) -> tuple[str, ...]:
        """Every declared id of *kind*, in declaration order."""
        wanted = _text(kind).strip()
        return tuple(
            cap.capability_id for cap in self.entries if cap.capability_id and cap.kind == wanted
        )

    def get(self, capability_id: Any) -> Optional[Capability]:
        """The first entry declaring *capability_id*, or ``None``."""
        wanted = _text(capability_id).strip()
        if not wanted:
            return None
        for cap in self.entries:
            if cap.capability_id == wanted:
                return cap
        return None

    def declares(self, capability_id: Any, kind: Any = None) -> bool:
        """Whether this host offers *capability_id* — optionally, as *kind*.

        **Ambiguity fails closed.** An id declared twice under two different
        kinds declares neither: picking a winner would let a host's mistake
        decide whether a permission is selectable as a tool.
        """
        wanted = _text(capability_id).strip()
        if not wanted:
            return False
        kinds = self._index.get(wanted)
        if not kinds:
            return False
        if len(set(kinds)) > 1:
            return False
        if kind is None:
            return True
        return kinds[0] == _text(kind).strip()

    @property
    def fingerprint(self) -> str:
        """This declaration's content hash — see the module docstring's point 4."""
        return catalog_fingerprint(self.entries)

    @property
    def problems(self) -> tuple[str, ...]:
        """Everything wrong with this declaration, named (constraint C3).

        A host wires one of these by hand; a mistake in it that normalised
        itself away silently would be a capability surface nobody could explain.
        Reported rather than raised, because a catalog is data a host builds at
        startup and crashing its main path is precisely what this package does
        not do.
        """
        found: list[str] = []
        for position, cap in enumerate(self.entries):
            if not cap.capability_id:
                found.append(f"entry {position} has a blank capability_id")
            if cap.kind not in CAPABILITY_KINDS:
                found.append(
                    f"entry {position} ({cap.capability_id or 'unnamed'!s}) declares kind "
                    f"{cap.kind!r}, which is not one of {CAPABILITY_KINDS}"
                )
        for name, kinds in sorted(self._index.items()):
            if len(kinds) > 1 and name:
                found.append(
                    f"{name!r} is declared {len(kinds)} times as {sorted(set(kinds))}; "
                    "an ambiguous id declares nothing"
                )
        return tuple(found)

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CATALOG_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "fingerprint": self.fingerprint,
            "entries": [cap.to_dict() for cap in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "CapabilityCatalog":
        """Coerce a raw payload. Never raises; unknown keys are ignored.

        ``fingerprint`` is *recomputed*, never read back: a digest a payload
        carries is a claim about the payload, and trusting it would let a stale
        or hand-edited file assert that a capability surface had not moved.
        """
        if not isinstance(data, dict):
            return EMPTY_CATALOG
        return cls(
            entries=_as_capabilities(data.get("entries")),
            catalog_id=_text(data.get("catalog_id")),
        )


#: A host that declares nothing offers nothing — and every tools or permissions
#: change unit validated against this is refused. That is the fail-closed floor
#: ``h11`` needs: absence of a declaration is never read as permission.
EMPTY_CATALOG = CapabilityCatalog()
