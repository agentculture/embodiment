"""The knowledge block, riding eidetic — no parallel memory system (task ``t8``).

An operator decision, encoded: *"knowledge rides eidetic. Strategist/cortex
maintains it over time."* This module is the composition seam for claims
``c35``/``h24`` and the store half of ``c30``/``h20``.

Issue #2 draws the line this module holds to: **eidetic owns memory
mechanics** — storage, ranking, relevance, ageing, supersession, forgetting —
and **embodiment owns the lived sequence**, when something is written, read or
revisited. So there is no store here. There is no ranking here, no scoring, no
embedding, no lifecycle policy of its own. What is here is *when*: the worker
writes an attributed claim, senses recalls the block, and the strategist runs
eidetic's own maintenance engine over it.

What a knowledge unit becomes in the store
-------------------------------------------
:class:`~embodiment.config_change.KnowledgeChange` (task ``t3``) maps onto
eidetic's :class:`~eidetic.memory.record.Record` field for field. Nothing is
invented; every column below is a field eidetic already declares, and
``tests/test_knowledge.py`` checks that against eidetic's own dataclass rather
than against this table:

===========================  ==========================  =========================
change unit                  eidetic ``Record`` field    why that field
===========================  ==========================  =========================
``origin``                   ``added_by``                eidetic's own words:
                                                         "the agent or caller that
                                                         ingested this record"
``entry_id``                 ``id``                      one identity, so a ledger
                                                         citation resolves in the
                                                         store
``text``                     ``text``                    verbatim, uncapped,
                                                         unnormalised
``supersedes``               ``supersedes``              eidetic's "authoritative
                                                         conflict declaration"
target's seat                ``scope.name``              the designated scope —
                                                         a partition, not a tag
(fixed)                      ``type``                    :data:`KNOWLEDGE_RECORD_TYPE`
(fixed)                      ``lifecycle``               ``"active"``; shadowing and
                                                         archival are maintenance
``reason``, ``change_id``    ``metadata``                NOT provenance — see below
===========================  ==========================  =========================

**Attribution rides ``added_by`` and nothing else.** That is the whole of
``c30``'s mechanism at the store layer, and the reason it is *that* field
rather than a new one is not tidiness. A second attribution key would be
invisible to eidetic's own tooling — ``sweep``, ``recall --json``, the graph
seam, any consumer written against eidetic's contract — which means the store
that owns provenance would hold records whose provenance it could not read. An
invented field is a parallel memory system in miniature.

``metadata`` is used, and the distinction matters: ``change_id`` is a
cross-reference into *embodiment's* applied-change ledger (``t5``), which
eidetic has no field for and should not; ``reason`` is the change's argument
for itself. Neither answers *who wrote this*. A test asserts no
attribution-shaped key ever appears there.

Why an unattributed write is refused WHOLE
-------------------------------------------
The worker→senses knowledge channel runs from the acting tier to the
**operator's ear**, and issue #63 measured what the far end of it does under
pressure: without a grounding clause the senses seat fabricated a sensor
reading 16 of 16 times under one operator push, and fabricated actions it had
never performed. An anonymous entry on that channel would let a fabricating or
compromised acting tier put words in the interaction tier's mouth with nothing
in the record pointing back at the writer — the config-design analogue of the
``t6``/#55 surrender direction. Attribution is the containment, so it is a
refused *shape* and not a lint.

Refused whole, never stripped-and-kept: the pattern is ``scope.py``'s
``directive_from_payload``, reused by citation exactly as ``config_change.py``
reuses it. Writing the entry minus its attribution would let the attempt
succeed at the part that mattered.

And it is enforced **twice** — on write, and again on read. That is not
belt-and-braces for its own sake. A public eidetic record lands in
``<repo-root>/.eidetic/memory``: committed, and travelling with every clone. So
what comes back out of the store is untrusted input on its way to a model
(``recall_bundle``'s own safety note), and a record that arrived by some route
other than :func:`write` must not be laundered into an attributed claim just by
being in the right scope.

The designated scope
---------------------
One scope per seat's knowledge block, derived by :func:`knowledge_scope` and
never free text: ``<agent>.knowledge.<seat>``. This follows eidetic's
scope+visibility convention v1 (``docs/contract.md`` §1 — one agent-personal
scope per repo, named by that repo's ``culture.yaml`` suffix) by *namespacing
under* the agent scope rather than colonising it.

The partition is doing real work, not filing. Because the seat is the scope
name and not a metadata tag, a worker write to ``senses.knowledge``
structurally cannot land in the worker's own block; senses' recall structurally
cannot surface anything but its own; and an ordinary agent-scope ``/recall``
never sees knowledge entries at all. Visibility follows the same convention:
public by default, private one explicit argument away, never inferred.

Composed through a port — and why THAT choice
-----------------------------------------------
The store is an injected two-callable port (:class:`KnowledgeStore`) whose
default binds :func:`embodiment.continuity.remember` and
:func:`embodiment.continuity.recall` — **imported lazily**. This is
``recall_bundle.py``'s posture, deliberately copied rather than
``continuity.py``'s:

* ``continuity`` imports eidetic at module scope because it *is* the eidetic
  seam; reaching it is what costs a host eidetic under deviation ``d2``.
* This module is a layer above that seam. A host that injects its own store, or
  that only wants the schema, should not pay ``d2``'s cost merely by importing
  it — so module-scope imports here are stdlib plus
  :mod:`embodiment.config_change`, and a test parses the file to keep it that
  way.
* The port is what lets the entire path — write, read, maintenance — be
  exercised with **no store at all**, which is the difference between a seam
  that is tested and one that is asserted.

The four store defaults are mirrored rather than imported, for the same
import-cost reason, and pinned against continuity's by a drift test so the
mirror cannot rot.

Strategist maintenance is eidetic's engine, not a policy written here
----------------------------------------------------------------------
:func:`maintain` composes ``eidetic.memory.lifecycle.compute_transitions`` —
eidetic's **pure** consolidation engine, which by its own docstring "performs no
I/O: it never reads the clock, never touches a backend". It owns both verbs the
spec names: within-scope ``supersedes`` shadowing (authoritative; a cross-scope
link never shadows, preserving the no-leak invariant) and age/signal archival,
with text-overlap conflicts returned as **suggestions that are never
auto-applied**. Nothing about that policy is restated here — this module
gathers the records, hands them over, and persists what came back changed.

Two details of the gathering are load-bearing rather than incidental. The
maintenance read includes shadowed and archived records (maintenance judges the
whole block, not the slice senses sees) and it does **not** reinforce — a pass
that bumped ``recall_count`` would move the very signal it is about to judge
against the archival threshold.

**The honest bound on it:** this is a maintenance pass over the records a
*recall* returns, not a whole-scope sweep. eidetic's own ``sweep`` command
enumerates a scope by loading it from the backend, and neither
:mod:`embodiment.continuity` nor this module exposes an enumerate verb — an
empty query matches nothing in every one of eidetic's four modes, so there is
no way to ask recall for "everything". A caller that has the whole block
already (the strategist has its ledger) passes ``records=`` directly. Closing
the gap properly means an enumerate verb on the continuity seam; it is not
closed here and is not claimed to be.

Constraint C3
--------------
Nothing degrades silently and nothing raises into a host's main path. Every
entry point returns a value carrying either a
:class:`~embodiment.config_change.ConfigRefusal` (a unit was offered and turned
away) or a :class:`~embodiment.config_change.ConfigDegradation` (the store
could not do it) — the same two shapes ``config_change.py`` mints, so ``t5``'s
ledger folds ONE stream. A store's own degradation keeps its own code, relayed
rather than re-labelled, so a host branches on one vocabulary.
``KeyboardInterrupt``/``SystemExit`` are the deliberate exception: never raise
means never raise *errors*, not never yield control.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from embodiment.config_change import (
    CHANGE_AUTHORITY,
    CHANGE_INCOMPLETE,
    CHANGE_NO_ORIGIN,
    CHANGE_ORIGIN_FORBIDDEN,
    CHANGE_ORIGINS,
    KNOWLEDGE_TARGETS,
    ConfigDegradation,
    ConfigRefusal,
    KnowledgeChange,
)

__all__ = [
    # the designated scope
    "KNOWLEDGE_RECORD_TYPE",
    "KNOWLEDGE_SCOPE_SEGMENT",
    "KNOWLEDGE_TARGETS",
    "KNOWLEDGE_ORIGINS",
    "LIFECYCLE_ACTIVE",
    "LIFECYCLE_SHADOWED",
    "LIFECYCLE_ARCHIVED",
    "knowledge_scope",
    # mirrored store defaults (pinned against continuity by a drift test)
    "DEFAULT_AGENT_SCOPE",
    "DEFAULT_VISIBILITY",
    "DEFAULT_TOP_K",
    "DEFAULT_MODE",
    "DEFAULT_MAINTENANCE_TOP_K",
    # degradation vocabulary (C3)
    "KNOWLEDGE_WRONG_UNIT",
    "KNOWLEDGE_UNKNOWN_TARGET",
    "KNOWLEDGE_STORE_ERROR",
    "KNOWLEDGE_UNREADABLE_OUTCOME",
    "KNOWLEDGE_UNREADABLE_RECORD",
    "KNOWLEDGE_UNATTRIBUTED_RECORD",
    "KNOWLEDGE_FOREIGN_RECORD",
    "KNOWLEDGE_TRANSITIONS_UNAVAILABLE",
    "KNOWLEDGE_DEGRADATION_CODES",
    # the store port
    "RememberFn",
    "RecallFn",
    "TransitionsFn",
    "KnowledgeStore",
    "TransitionPlan",
    "default_store",
    "default_transitions",
    # shapes
    "KnowledgeEntry",
    "KnowledgeWrite",
    "KnowledgeRead",
    "KnowledgeMaintenance",
    # verbs
    "record_for",
    "entry_from_record",
    "write",
    "read",
    "maintain",
]


# ── the designated scope ──────────────────────────────────────────────────────

#: eidetic's ``Record.type`` for every entry this lane writes. A designated
#: scope holds knowledge; a record of any other type in it did not come from
#: here and is not read as an entry.
KNOWLEDGE_RECORD_TYPE = "knowledge"

#: The middle segment of ``<agent>.knowledge.<seat>``. Namespacing *under* the
#: agent-personal scope rather than colonising it — convention v1 §1.
KNOWLEDGE_SCOPE_SEGMENT = "knowledge"

#: The closed set of writers, re-exported from the change schema rather than
#: copied. One vocabulary; a second copy is a thing that drifts.
KNOWLEDGE_ORIGINS = CHANGE_ORIGINS

#: eidetic's own lifecycle vocabulary (``eidetic.memory.record.Record``).
#: Mirrored, never redefined: this module sets ``active`` on write and reads the
#: other two back out of what eidetic's engine decided.
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_SHADOWED = "shadowed"
LIFECYCLE_ARCHIVED = "archived"


# ── mirrored store defaults ───────────────────────────────────────────────────
#
# Mirrored, not imported, so this module stays import-cheap (see the module
# docstring on why reaching continuity is expensive). Pinned against
# continuity's by ``tests/test_knowledge.py`` so the mirror cannot rot.

#: The agent-personal scope the knowledge scopes hang under. Matches
#: ``continuity.DEFAULT_SCOPE`` — eidetic's own ``--scope`` default. A host
#: passes its resolved identity scope; this is only the fallback.
DEFAULT_AGENT_SCOPE = "default"

#: eidetic ``docs/contract.md`` §2: public, for in-repo team-shared records.
#: Private is one explicit argument away and never inferred.
DEFAULT_VISIBILITY = "public"

DEFAULT_TOP_K = 5
DEFAULT_MODE = "hybrid"

#: A maintenance pass looks at more of the block than a senses read does. Still
#: a bound, not a sweep — see the module docstring's honest note on enumeration.
DEFAULT_MAINTENANCE_TOP_K = 200

#: Cap on recorded reason text, so a runaway value cannot blow up a host's
#: store or artifact. ``config_change.py``'s value, for its reason.
_MAX_REASON_LEN = 500


def knowledge_scope(target: Any, *, agent: str = DEFAULT_AGENT_SCOPE) -> str:
    """The designated eidetic scope for *target*'s knowledge block.

    ``<agent>.knowledge.<seat>`` — derived from the target, never free text, so
    the seat a record belongs to is the store partition it lives in rather than
    a tag on it. Returns ``""`` for anything that is not a knowledge target: a
    caller cannot accidentally address a prompt surface as a scope.
    """
    name = target if isinstance(target, str) else ""
    if name not in KNOWLEDGE_TARGETS:
        return ""
    seat = name.split(".")[0]
    root = (agent or DEFAULT_AGENT_SCOPE).strip() or DEFAULT_AGENT_SCOPE
    return f"{root}.{KNOWLEDGE_SCOPE_SEGMENT}.{seat}"


# ── degradation vocabulary (C3) ───────────────────────────────────────────────
#
# Prefixed ``knowledge-`` so t5's ledger harvests a vocabulary that cannot
# collide with the advisory lane's ``scope-`` codes or the change lane's
# ``config-change-`` ones. One code per distinct FIX. Attribution refusals
# deliberately do NOT get a code of their own — they reuse
# ``config_change``'s ``CHANGE_NO_ORIGIN`` / ``CHANGE_ORIGIN_FORBIDDEN`` /
# ``CHANGE_INCOMPLETE``, because the fix is identical wherever the unit was
# turned away and a second code for the same fix is how a ledger's vocabulary
# stops meaning anything.

#: The offered unit was not a :class:`~embodiment.config_change.KnowledgeChange`.
KNOWLEDGE_WRONG_UNIT = "knowledge-not-a-knowledge-change"
#: The target named no knowledge block (:data:`KNOWLEDGE_TARGETS`).
KNOWLEDGE_UNKNOWN_TARGET = "knowledge-unknown-target"
#: The store raised, or reported its own failure with no code to relay.
KNOWLEDGE_STORE_ERROR = "knowledge-store-error"
#: The store returned a shape this seam cannot read.
KNOWLEDGE_UNREADABLE_OUTCOME = "knowledge-unreadable-outcome"
#: One record in a result could not be read as a record. Dropped, never fatal.
KNOWLEDGE_UNREADABLE_RECORD = "knowledge-unreadable-record"
#: A stored record carried no usable ``added_by``. Dropped — the read-side half
#: of ``c30``, because the store is a committed, clonable surface.
KNOWLEDGE_UNATTRIBUTED_RECORD = "knowledge-unattributed-record"
#: A record in a designated knowledge scope was not of the knowledge type.
KNOWLEDGE_FOREIGN_RECORD = "knowledge-foreign-record"
#: eidetic's lifecycle engine could not be reached or refused the records.
KNOWLEDGE_TRANSITIONS_UNAVAILABLE = "knowledge-transitions-unavailable"

#: The complete set. Every one has a producer in this file and a test that
#: fires it — embodiment#18's lesson: a code nothing can mint is a lie.
KNOWLEDGE_DEGRADATION_CODES = (
    KNOWLEDGE_WRONG_UNIT,
    KNOWLEDGE_UNKNOWN_TARGET,
    KNOWLEDGE_STORE_ERROR,
    KNOWLEDGE_UNREADABLE_OUTCOME,
    KNOWLEDGE_UNREADABLE_RECORD,
    KNOWLEDGE_UNATTRIBUTED_RECORD,
    KNOWLEDGE_FOREIGN_RECORD,
    KNOWLEDGE_TRANSITIONS_UNAVAILABLE,
)

#: Relayed verbatim from ``continuity`` rather than imported, so a missing
#: anchor is refused before the port is called without paying ``d2``'s import
#: cost to name the code. Pinned by the drift test alongside the defaults.
_CODE_NO_STORAGE_ANCHOR = "no-storage-anchor"


# ── the store port ────────────────────────────────────────────────────────────

#: ``remember(record, *, data_dir, scope, visibility, added_by)`` returning
#: something shaped like :class:`embodiment.continuity.RememberOutcome`.
RememberFn = Callable[..., Any]

#: ``recall(query, *, data_dir, scope, visibility, top_k, mode, …)`` returning
#: something shaped like :class:`embodiment.continuity.RecallOutcome`.
RecallFn = Callable[..., Any]

#: ``transitions(records, now)`` returning a :class:`TransitionPlan`. The
#: default composes eidetic's pure lifecycle engine.
TransitionsFn = Callable[[list[dict[str, Any]], str], "TransitionPlan"]


@dataclass(frozen=True)
class KnowledgeStore:
    """The two-callable store port — ``ScopePersistence``'s shape, cited.

    Injected so the whole path runs with no store. The default
    (:func:`default_store`) binds continuity's two seam functions, which is
    where eidetic actually lives.
    """

    remember: RememberFn
    recall: RecallFn


@dataclass(frozen=True)
class TransitionPlan:
    """What eidetic's lifecycle engine decided: changed records, and advice.

    ``changed`` are records whose ``lifecycle`` eidetic flipped, as dicts.
    ``suggestions`` are advisory conflict hints eidetic returns **for human
    confirmation and never auto-applies** — that is eidetic's rule, honoured
    here by never writing them.
    """

    changed: tuple[dict[str, Any], ...] = ()
    suggestions: tuple[dict[str, Any], ...] = ()


def default_store() -> KnowledgeStore:
    """The store port over :mod:`embodiment.continuity`, imported on use.

    Lazy on purpose: reaching continuity is what costs a host eidetic
    (deviation ``d2``), and a host that injects its own port — or never touches
    the knowledge block — should not pay for it just by importing this module.
    """
    from embodiment import continuity

    return KnowledgeStore(remember=continuity.remember, recall=continuity.recall)


def default_transitions() -> TransitionsFn:
    """eidetic's own consolidation engine, adapted to dicts and imported on use.

    ``eidetic.memory.lifecycle.compute_transitions`` is **pure** — its docstring
    states it "performs no I/O: it never reads the clock, never touches a
    backend" — so composing it is the opposite of reimplementing memory. It
    owns both maintenance verbs: within-scope ``supersedes`` shadowing and
    age/signal archival, plus advisory conflict suggestions.

    The adapter is dicts in, dicts out, so this module never handles an eidetic
    ``Record`` and a test can stand in for the engine with a plain function.
    """
    from eidetic.memory.lifecycle import compute_transitions
    from eidetic.memory.record import Record

    def _transitions(records: list[dict[str, Any]], now: str) -> TransitionPlan:
        built = [Record.from_dict(dict(record)) for record in records]
        result = compute_transitions(built, now)
        return TransitionPlan(
            changed=tuple(record.to_dict() for record in result.changed),
            suggestions=tuple(dict(entry) for entry in result.suggestions),
        )

    return _transitions


# ── the shapes ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KnowledgeEntry:
    """One attributed claim, read back out of the block.

    :attr:`origin` is never ``None`` and is always one of
    :data:`KNOWLEDGE_ORIGINS` — an entry that could not say who wrote it is not
    built at all (see :func:`entry_from_record`). :attr:`text` is carried
    verbatim from the store; ``raw`` is the record as eidetic returned it, for a
    host that wants a field this shape does not surface.
    """

    entry_id: str
    text: str
    origin: str
    supersedes: Optional[str] = None
    created: str = ""
    lifecycle: str = LIFECYCLE_ACTIVE
    scope: str = ""
    visibility: str = ""
    raw: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class KnowledgeWrite:
    """The result of offering one knowledge unit to the store.

    Exactly one of :attr:`refusal` (the unit was turned away) and
    :attr:`degradation` (the store could not take it) is ever set on a failure,
    and neither is set on success. The split matters to a host: a refusal is
    *your unit is wrong*, a degradation is *the store is unwell*.
    """

    ok: bool
    entry_id: str = ""
    scope: str = ""
    origin: str = ""
    refusal: Optional[ConfigRefusal] = None
    degradation: Optional[ConfigDegradation] = None
    record: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class KnowledgeRead:
    """What senses got back, and everything that was dropped on the way.

    A dropped record is always *recorded* — an entry silently vanishing from a
    block the operator is being answered from is exactly the silent degradation
    C3 forbids.
    """

    entries: tuple[KnowledgeEntry, ...] = ()
    scope: str = ""
    degradations: tuple[ConfigDegradation, ...] = ()

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def degraded(self) -> bool:
        """True when anything at all was dropped or failed. Never inferred from
        an empty result: a block can be legitimately empty."""
        return bool(self.degradations)


@dataclass(frozen=True)
class KnowledgeMaintenance:
    """What one maintenance pass changed, and what it only suggests.

    :attr:`suggestions` are eidetic's advisory conflict hints. They are returned
    and never acted on — a host or operator decides, which is eidetic's own
    contract for them.
    """

    scope: str = ""
    considered: int = 0
    shadowed: tuple[str, ...] = ()
    archived: tuple[str, ...] = ()
    suggestions: tuple[dict[str, Any], ...] = ()
    degradations: tuple[ConfigDegradation, ...] = ()


# ── coercion helpers (never raise) ────────────────────────────────────────────


def _text(value: Any) -> str:
    """Coerce to text. Never raises — ``config_change._text``, cited."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # an unreadable value is a blank, never a crash
        return ""


def _reason(value: Any) -> str:
    return _text(value)[:_MAX_REASON_LEN]


def _queries(value: Any) -> tuple[str, ...]:
    """``value`` as a tuple of usable queries.

    A bare string is ONE query, not a sequence of characters — ``recall_bundle``'s
    lesson, inherited. Blanks are dropped; there is nothing to ask.
    """
    if isinstance(value, str):
        candidates: list[Any] = [value]
    elif isinstance(value, Sequence):
        candidates = list(value)
    else:
        return ()
    return tuple(entry for entry in candidates if isinstance(entry, str) and entry.strip())


def _degradation(code: str, reason: str, *, seat: str = "", target: str = "") -> ConfigDegradation:
    return ConfigDegradation(code=code, reason=_reason(reason), seat=seat, target=target)


def _refusal(code: str, reason: str, *, change: Any = None, target: str = "") -> ConfigRefusal:
    """Mint one refusal, reading identity defensively off *change*."""
    target = target or _text(getattr(change, "target", "")).strip()
    seat = target.split(".")[0] if target in KNOWLEDGE_TARGETS else ""
    return ConfigRefusal(
        code=code,
        reason=_reason(reason),
        seat=seat,
        target=target,
        change_id=_text(getattr(change, "change_id", "")).strip(),
        origin=_text(getattr(change, "origin", "")).strip(),
    )


def _relayed(outcome: Any, *, fallback: str, seat: str, target: str) -> ConfigDegradation:
    """Adopt a store degradation, keeping its OWN code where it has one.

    Relayed rather than re-labelled so a host branches on one vocabulary: a
    missing storage anchor reads as ``no-storage-anchor`` here exactly as it
    does out of :mod:`embodiment.continuity`.
    """
    degradation = getattr(outcome, "degradation", None)
    code = _text(getattr(degradation, "code", "")).strip()
    reason = _text(getattr(degradation, "reason", "")).strip()
    return _degradation(
        code or fallback, reason or "the store reported a failure", seat=seat, target=target
    )


def _anchored(data_dir: Any) -> bool:
    """continuity's trap #1, checked before the port is called.

    A ``data_dir`` is mandatory *there*; refusing here as well means a host that
    injected its own port gets the same refusal rather than a store silently
    resolved from the host application's cwd.
    """
    if data_dir is None:
        return False
    return bool(_text(data_dir).strip())


# ── building the record ───────────────────────────────────────────────────────


def record_for(
    change: KnowledgeChange,
    *,
    agent: str = DEFAULT_AGENT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    created: str = "",
) -> dict[str, Any]:
    """The eidetic record *change* becomes. Exported so it can be inspected.

    Every top-level key here is a field eidetic's own ``Record`` declares, and
    ``tests/test_knowledge.py`` asserts that against eidetic's dataclass rather
    than against this docstring. ``added_by`` carries the attribution and is the
    only field that does.

    ``metadata`` carries the ledger cross-reference and the change's reason.
    Neither is provenance; both are things eidetic legitimately has no field for
    and offers ``metadata`` to hold.
    """
    metadata: dict[str, Any] = {}
    if change.change_id:
        metadata["change_id"] = change.change_id
    if change.reason:
        metadata["reason"] = _reason(change.reason)

    record: dict[str, Any] = {
        "id": change.entry_id,
        "text": change.text,
        "type": KNOWLEDGE_RECORD_TYPE,
        "added_by": change.origin,
        "lifecycle": LIFECYCLE_ACTIVE,
        "metadata": metadata,
        "scope": {
            "name": knowledge_scope(change.target, agent=agent),
            "visibility": visibility,
        },
    }
    if change.supersedes:
        record["supersedes"] = change.supersedes
    if created:
        record["created"] = created
    return record


def entry_from_record(record: Any) -> tuple[Optional[KnowledgeEntry], Optional[str]]:
    """Read one store record as an entry. ``(entry, refusal_code)``.

    Exactly one of the two is ``None``. The refusal code is the *reason it was
    not read*, so a caller records the drop rather than swallowing it:

    * :data:`KNOWLEDGE_UNREADABLE_RECORD` — not a record shape at all;
    * :data:`KNOWLEDGE_FOREIGN_RECORD` — in the scope, but not a knowledge entry;
    * :data:`KNOWLEDGE_UNATTRIBUTED_RECORD` — no usable ``added_by``.

    The last is ``c30`` enforced on the way OUT. Store content is untrusted
    input: a public eidetic record is committed and travels with every clone, so
    a record that reached the scope by some route other than :func:`write` must
    not become an attributed claim just by sitting there.
    """
    if not isinstance(record, Mapping):
        return None, KNOWLEDGE_UNREADABLE_RECORD

    entry_id = record.get("id")
    text = record.get("text")
    if not isinstance(entry_id, str) or not entry_id.strip():
        return None, KNOWLEDGE_UNREADABLE_RECORD
    if not isinstance(text, str):
        return None, KNOWLEDGE_UNREADABLE_RECORD

    if _text(record.get("type")).strip() != KNOWLEDGE_RECORD_TYPE:
        return None, KNOWLEDGE_FOREIGN_RECORD

    origin = _text(record.get("added_by")).strip()
    if origin not in KNOWLEDGE_ORIGINS:
        return None, KNOWLEDGE_UNATTRIBUTED_RECORD

    scope_data = record.get("scope")
    scope_name = ""
    visibility = ""
    if isinstance(scope_data, Mapping):
        raw_name = scope_data.get("name")
        raw_visibility = scope_data.get("visibility")
        scope_name = raw_name if isinstance(raw_name, str) else ""
        visibility = raw_visibility if isinstance(raw_visibility, str) else ""

    supersedes = record.get("supersedes")
    created = record.get("created")
    lifecycle = record.get("lifecycle")

    return (
        KnowledgeEntry(
            entry_id=entry_id,
            text=text,
            origin=origin,
            supersedes=supersedes if isinstance(supersedes, str) else None,
            created=created if isinstance(created, str) else "",
            lifecycle=lifecycle if isinstance(lifecycle, str) else LIFECYCLE_ACTIVE,
            scope=scope_name,
            visibility=visibility,
            raw=record,
        ),
        None,
    )


# ── the worker's write ────────────────────────────────────────────────────────


def _refuse_write(change: Any) -> Optional[ConfigRefusal]:
    """Every reason this unit may not be written, in contract order.

    Attribution is checked before anything else about the unit's content,
    because it is the check whose failure means *do not let this reach the
    operator's ear* rather than *this unit is malformed*.
    """
    if not isinstance(change, KnowledgeChange):
        return _refusal(
            KNOWLEDGE_WRONG_UNIT,
            "this seam writes knowledge units only; got "
            f"{type(change).__name__}. A prompt, a capability selection or a raw "
            "payload cannot ride the knowledge channel — author it through the "
            "surface that owns it",
            change=change,
        )

    target = change.target
    if target not in KNOWLEDGE_TARGETS:
        return _refusal(
            KNOWLEDGE_UNKNOWN_TARGET,
            f"{target!r} is not a knowledge block; the knowledge targets are "
            f"{', '.join(KNOWLEDGE_TARGETS)}",
            change=change,
            target=target,
        )

    origin = change.origin
    if origin not in KNOWLEDGE_ORIGINS:
        return _refusal(
            CHANGE_NO_ORIGIN,
            f"the entry named the origin {origin!r}; a knowledge entry reaches the "
            "operator's ear through the senses seat, so it must be attributed to one "
            f"of {', '.join(KNOWLEDGE_ORIGINS)}. The whole entry was refused rather "
            "than written unattributed",
            change=change,
        )

    if target not in CHANGE_AUTHORITY[origin]:
        return _refusal(
            CHANGE_ORIGIN_FORBIDDEN,
            f"{origin!r} does not own {target!r}; that origin may write "
            f"{', '.join(sorted(CHANGE_AUTHORITY[origin])) or 'nothing'}",
            change=change,
        )

    if not change.entry_id:
        return _refusal(
            CHANGE_INCOMPLETE,
            "the entry named no entry_id; an entry the ledger cannot cite cannot be "
            "superseded, reverted or explained",
            change=change,
        )

    if not change.text:
        return _refusal(
            CHANGE_INCOMPLETE,
            "the entry carried no text; an entry that states nothing is not a claim",
            change=change,
        )

    return None


def write(
    change: Any,
    *,
    data_dir: Any = None,
    store: Optional[KnowledgeStore] = None,
    agent: str = DEFAULT_AGENT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    now: Optional[str] = None,
) -> KnowledgeWrite:
    """Write one attributed claim into a seat's knowledge block.

    The worker's one and only writable target is ``senses.knowledge``; the
    strategist and the host may write either block. That lattice is
    ``config_change.CHANGE_AUTHORITY``'s, read here rather than restated.

    **An unattributed unit is refused whole and recorded** — nothing partial
    reaches the store, and the refusal names what was turned away. See the
    module docstring for why that is a shape rule and not a lint.

    Never raises: a hostile port, a dead store, or an outcome of the wrong shape
    all come back as ``KnowledgeWrite(ok=False, …)``.
    """
    refusal = _refuse_write(change)
    if refusal is not None:
        return KnowledgeWrite(
            ok=False,
            entry_id=_text(getattr(change, "entry_id", "")).strip(),
            origin=_text(getattr(change, "origin", "")).strip(),
            refusal=refusal,
        )

    scope = knowledge_scope(change.target, agent=agent)
    seat = change.seat

    if not _anchored(data_dir):
        return KnowledgeWrite(
            ok=False,
            entry_id=change.entry_id,
            scope=scope,
            origin=change.origin,
            degradation=_degradation(
                _CODE_NO_STORAGE_ANCHOR,
                "no data_dir was supplied; the store is never resolved from the host "
                "application's cwd, so the write was refused before touching it",
                seat=seat,
                target=change.target,
            ),
        )

    record = record_for(change, agent=agent, visibility=visibility, created=_text(now))
    port = store if store is not None else default_store()

    try:
        outcome = port.remember(
            record,
            data_dir=data_dir,
            scope=scope,
            visibility=visibility,
            added_by=change.origin,
        )
    except Exception as exc:  # noqa: BLE001  # a store failure never reaches the host
        return KnowledgeWrite(
            ok=False,
            entry_id=change.entry_id,
            scope=scope,
            origin=change.origin,
            degradation=_degradation(
                KNOWLEDGE_STORE_ERROR,
                f"the store raised {type(exc).__name__}: {exc}",
                seat=seat,
                target=change.target,
            ),
        )

    ok = getattr(outcome, "ok", None)
    if not isinstance(ok, bool):
        return KnowledgeWrite(
            ok=False,
            entry_id=change.entry_id,
            scope=scope,
            origin=change.origin,
            degradation=_degradation(
                KNOWLEDGE_UNREADABLE_OUTCOME,
                f"the store returned {type(outcome).__name__}, which carries no readable "
                "`ok`; the write cannot be reported as having happened",
                seat=seat,
                target=change.target,
            ),
        )

    if not ok:
        return KnowledgeWrite(
            ok=False,
            entry_id=change.entry_id,
            scope=scope,
            origin=change.origin,
            degradation=_relayed(
                outcome, fallback=KNOWLEDGE_STORE_ERROR, seat=seat, target=change.target
            ),
        )

    return KnowledgeWrite(
        ok=True,
        entry_id=change.entry_id,
        scope=scope,
        origin=change.origin,
        record=record,
    )


# ── the senses read ───────────────────────────────────────────────────────────


def _gather(
    port: KnowledgeStore,
    queries: tuple[str, ...],
    *,
    data_dir: Any,
    scope: str,
    seat: str,
    target: str,
    visibility: str,
    top_k: int,
    mode: str,
    include_shadowed: bool,
    include_archived: bool,
    reinforce: bool,
) -> tuple[list[dict[str, Any]], list[ConfigDegradation]]:
    """Run each query, union the records by id, record every failure.

    One bad query never costs the rest — the remaining queries still run, which
    is ``recall_bundle``'s rule and for its reason: a partial block is a usable
    block, an exception is not.
    """
    found: dict[str, dict[str, Any]] = {}
    degradations: list[ConfigDegradation] = []

    for query in queries:
        try:
            outcome = port.recall(
                query,
                data_dir=data_dir,
                scope=scope,
                visibility=visibility,
                top_k=top_k,
                mode=mode,
                include_shadowed=include_shadowed,
                include_archived=include_archived,
                reinforce=reinforce,
            )
        except Exception as exc:  # noqa: BLE001  # a store failure never reaches the host
            degradations.append(
                _degradation(
                    KNOWLEDGE_STORE_ERROR,
                    f"the store raised {type(exc).__name__} on query {query!r}: {exc}",
                    seat=seat,
                    target=target,
                )
            )
            continue

        records = getattr(outcome, "records", None)
        if not isinstance(records, (list, tuple)):
            degradations.append(
                _degradation(
                    KNOWLEDGE_UNREADABLE_OUTCOME,
                    f"the store returned {type(outcome).__name__} for query {query!r}, "
                    "which carries no readable `records`",
                    seat=seat,
                    target=target,
                )
            )
            continue

        if getattr(outcome, "degradation", None) is not None:
            degradations.append(
                _relayed(outcome, fallback=KNOWLEDGE_STORE_ERROR, seat=seat, target=target)
            )

        for record in records:
            # A non-mapping never gets past here. Coercing one would raise out
            # of a seam whose whole contract is that it does not, and carrying
            # one forward would put it in front of the lifecycle engine.
            if not isinstance(record, Mapping):
                degradations.append(
                    _degradation(
                        KNOWLEDGE_UNREADABLE_RECORD,
                        f"the store returned a {type(record).__name__} where a record was "
                        f"expected for query {query!r}; it was dropped",
                        seat=seat,
                        target=target,
                    )
                )
                continue
            record_id = record.get("id")
            key = record_id if isinstance(record_id, str) and record_id else repr(record)
            found.setdefault(key, dict(record))

    return list(found.values()), degradations


def read(
    target: Any,
    queries: Any,
    *,
    data_dir: Any = None,
    store: Optional[KnowledgeStore] = None,
    agent: str = DEFAULT_AGENT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    top_k: int = DEFAULT_TOP_K,
    mode: str = DEFAULT_MODE,
    include_shadowed: bool = False,
    include_archived: bool = False,
) -> KnowledgeRead:
    """Read a seat's knowledge block — senses' half of the channel.

    Ranking, relevance and freshness are entirely eidetic's: *mode* and *top_k*
    pass through untouched and no score is read here. Shadowed and archived
    entries stay hidden unless asked for, which is eidetic's own recall default.

    **Attribution is enforced again on the way out.** An entry whose stored
    ``added_by`` is not one of :data:`KNOWLEDGE_ORIGINS` is dropped and the drop
    is recorded — see :func:`entry_from_record` for why the store's contents are
    treated as untrusted input.

    Never raises. Every drop and every store failure lands on
    :attr:`KnowledgeRead.degradations`; the entries that could be read still
    come back.
    """
    name = _text(target).strip()
    scope = knowledge_scope(name, agent=agent)
    seat = name.split(".")[0] if name in KNOWLEDGE_TARGETS else ""

    if not scope:
        return KnowledgeRead(
            degradations=(
                _degradation(
                    KNOWLEDGE_UNKNOWN_TARGET,
                    f"{name!r} is not a knowledge block; the knowledge targets are "
                    f"{', '.join(KNOWLEDGE_TARGETS)}",
                    target=name,
                ),
            )
        )

    if not _anchored(data_dir):
        return KnowledgeRead(
            scope=scope,
            degradations=(
                _degradation(
                    _CODE_NO_STORAGE_ANCHOR,
                    "no data_dir was supplied; the store is never resolved from the "
                    "host application's cwd, so nothing was read",
                    seat=seat,
                    target=name,
                ),
            ),
        )

    port = store if store is not None else default_store()
    records, degradations = _gather(
        port,
        _queries(queries),
        data_dir=data_dir,
        scope=scope,
        seat=seat,
        target=name,
        visibility=visibility,
        top_k=top_k,
        mode=mode,
        include_shadowed=include_shadowed,
        include_archived=include_archived,
        reinforce=True,
    )

    entries: list[KnowledgeEntry] = []
    for record in records:
        entry, dropped = entry_from_record(record)
        if entry is not None:
            entries.append(entry)
            continue
        entry_id = record.get("id") if isinstance(record, Mapping) else None
        degradations.append(
            _degradation(
                _text(dropped),
                f"record {entry_id!r} in {scope} was not read as a knowledge entry "
                "and was dropped",
                seat=seat,
                target=name,
            )
        )

    return KnowledgeRead(entries=tuple(entries), scope=scope, degradations=tuple(degradations))


# ── the strategist's maintenance ──────────────────────────────────────────────


def _persist(
    port: KnowledgeStore,
    plan: TransitionPlan,
    *,
    data_dir: Any,
    scope: str,
    seat: str,
    target: str,
    visibility: str,
) -> tuple[list[str], list[str], list[ConfigDegradation]]:
    """Write back every record eidetic's engine transitioned.

    ``score`` and ``signal`` are cleared first: both are recall-time artefacts,
    and continuity's own reinforcement path clears them for the same reason —
    storing a query-time score would persist a number that was only ever true of
    one query.
    """
    shadowed: list[str] = []
    archived: list[str] = []
    degradations: list[ConfigDegradation] = []

    for record in plan.changed:
        if not isinstance(record, Mapping):
            degradations.append(
                _degradation(
                    KNOWLEDGE_UNREADABLE_RECORD,
                    f"the lifecycle engine returned {type(record).__name__} where a "
                    "record was expected; it was not written back",
                    seat=seat,
                    target=target,
                )
            )
            continue

        payload = dict(record)
        payload["score"] = None
        payload["signal"] = None
        entry_id = _text(payload.get("id")).strip()
        lifecycle = _text(payload.get("lifecycle")).strip()

        try:
            outcome = port.remember(
                payload,
                data_dir=data_dir,
                scope=scope,
                visibility=visibility,
                added_by=_text(payload.get("added_by")).strip() or None,
            )
        except Exception as exc:  # noqa: BLE001  # a store failure never reaches the host
            degradations.append(
                _degradation(
                    KNOWLEDGE_STORE_ERROR,
                    f"writing the {lifecycle!r} transition of {entry_id!r} raised "
                    f"{type(exc).__name__}: {exc}",
                    seat=seat,
                    target=target,
                )
            )
            continue

        if getattr(outcome, "ok", None) is not True:
            degradations.append(
                _relayed(outcome, fallback=KNOWLEDGE_STORE_ERROR, seat=seat, target=target)
            )
            continue

        if lifecycle == LIFECYCLE_SHADOWED:
            shadowed.append(entry_id)
        elif lifecycle == LIFECYCLE_ARCHIVED:
            archived.append(entry_id)

    return shadowed, archived, degradations


def maintain(
    target: Any,
    *,
    queries: Any = (),
    records: Optional[Sequence[Mapping[str, Any]]] = None,
    data_dir: Any = None,
    store: Optional[KnowledgeStore] = None,
    transitions: Optional[TransitionsFn] = None,
    agent: str = DEFAULT_AGENT_SCOPE,
    visibility: str = DEFAULT_VISIBILITY,
    top_k: int = DEFAULT_MAINTENANCE_TOP_K,
    now: Optional[str] = None,
) -> KnowledgeMaintenance:
    """Run eidetic's consolidation engine over a seat's knowledge block.

    This is the strategist's *maintain it over time* verb, and every decision it
    makes is eidetic's: within-scope ``supersedes`` shadowing (authoritative),
    age/signal archival, and text-overlap conflict **suggestions that are
    returned and never applied**. No policy is written here.

    Two properties of the gathering are deliberate. The read includes shadowed
    and archived records — maintenance judges the whole block, not the slice
    senses sees — and it does **not** reinforce, because bumping ``recall_count``
    would move the very signal the archival rule is about to weigh.

    *records* short-circuits the read for a caller that already holds the block.
    Supply it or *queries*; see the module docstring for the honest bound on
    what a query-driven pass can reach.

    Never raises. A lifecycle engine that is missing or explodes degrades to
    :data:`KNOWLEDGE_TRANSITIONS_UNAVAILABLE` with nothing written.
    """
    name = _text(target).strip()
    scope = knowledge_scope(name, agent=agent)
    seat = name.split(".")[0] if name in KNOWLEDGE_TARGETS else ""

    if not scope:
        return KnowledgeMaintenance(
            degradations=(
                _degradation(
                    KNOWLEDGE_UNKNOWN_TARGET,
                    f"{name!r} is not a knowledge block; the knowledge targets are "
                    f"{', '.join(KNOWLEDGE_TARGETS)}",
                    target=name,
                ),
            )
        )

    port = store if store is not None else default_store()
    degradations: list[ConfigDegradation] = []

    if records is not None:
        gathered = [dict(record) for record in records if isinstance(record, Mapping)]
    elif not _anchored(data_dir):
        return KnowledgeMaintenance(
            scope=scope,
            degradations=(
                _degradation(
                    _CODE_NO_STORAGE_ANCHOR,
                    "no data_dir was supplied; the store is never resolved from the "
                    "host application's cwd, so no maintenance ran",
                    seat=seat,
                    target=name,
                ),
            ),
        )
    else:
        gathered, degradations = _gather(
            port,
            _queries(queries),
            data_dir=data_dir,
            scope=scope,
            seat=seat,
            target=name,
            visibility=visibility,
            top_k=top_k,
            mode=DEFAULT_MODE,
            include_shadowed=True,
            include_archived=True,
            reinforce=False,
        )

    moment = _text(now).strip() or datetime.now(timezone.utc).isoformat()

    try:
        seam = transitions if transitions is not None else default_transitions()
        plan = seam(gathered, moment)
    except Exception as exc:  # noqa: BLE001  # an absent engine never reaches the host
        degradations.append(
            _degradation(
                KNOWLEDGE_TRANSITIONS_UNAVAILABLE,
                f"eidetic's lifecycle engine could not run ({type(exc).__name__}: {exc}); "
                "no transition was applied and the block is unchanged",
                seat=seat,
                target=name,
            )
        )
        return KnowledgeMaintenance(
            scope=scope, considered=len(gathered), degradations=tuple(degradations)
        )

    changed = getattr(plan, "changed", None)
    suggestions = getattr(plan, "suggestions", ())
    if not isinstance(changed, (list, tuple)):
        degradations.append(
            _degradation(
                KNOWLEDGE_TRANSITIONS_UNAVAILABLE,
                f"the lifecycle engine returned {type(plan).__name__}, which carries no "
                "readable `changed`; no transition was applied",
                seat=seat,
                target=name,
            )
        )
        return KnowledgeMaintenance(
            scope=scope, considered=len(gathered), degradations=tuple(degradations)
        )

    shadowed, archived, write_degradations = _persist(
        port,
        TransitionPlan(changed=tuple(changed), suggestions=tuple(suggestions or ())),
        data_dir=data_dir,
        scope=scope,
        seat=seat,
        target=name,
        visibility=visibility,
    )
    degradations.extend(write_degradations)

    return KnowledgeMaintenance(
        scope=scope,
        considered=len(gathered),
        shadowed=tuple(shadowed),
        archived=tuple(archived),
        suggestions=tuple(suggestions or ()),
        degradations=tuple(degradations),
    )
