"""embodiment.recall_bundle — raw memory material, fetched by the runtime (task t4).

The muse is being given memory. Not a memory *capability* — memory as **material**:
the runtime fetches records out of the store and hands them over as context, and
the muse compiles them into something worth saying. This module is the fetch half
of that arrangement, and it is deliberately the boring half.

Three boundaries define it, and each is held by the mechanism rather than by
prose.

**1. eidetic is fetch-only. The store never synthesises; the muse compiles.**
Nothing here summarises, ranks, rewrites, merges or interprets a record. A
record's ``text`` is carried byte-for-byte from what the store returned — no
strip, no clip, no normalisation (:func:`item_from_record`). Even the item cap
(:attr:`BundleRequest.max_items`) *drops whole items and records the drop*
rather than shortening anything. If this module ever appears to be improving the
material, that is a bug: compilation is the muse's job, one layer up.

**2. The runtime performs the fetch, not the muse.** There is no query verb
anywhere on the muse's side and this module supplies none: it is called by a
host or by embodiment's own runtime, and it returns a value. It holds no model
seam — no ``complete``, no messages, no prompt — so there is nothing here a
model could be handed. That asymmetry is issue #2's rule ("memory and coherence
are *runtime*, not tools the model may pick arbitrarily") applied literally, and
``tests/test_recall_bundle.py`` pins it from both sides.

**3. Flat today, graph when a sibling ships it — and the bundle says which.**
Every bundle declares its **enrichment level** in provenance:

* :data:`LEVEL_FLAT` — what today's ``eidetic-cli`` can actually do: search over
  its four modes, returning records that carry their own ``links`` and
  ``supersedes`` fields. No traversal happens; link ids that no fetched record
  satisfies are reported as :attr:`RecallBundle.unresolved_links` rather than
  quietly resolved or quietly dropped.
* :data:`LEVEL_GRAPH` — the composite fetch (graph traversal plus the vector
  lines hanging off traversed nodes) proposed to eidetic-cli. **It does not
  exist yet**, here or there. Asking for it today is not an error and is not a
  failure: the fetch degrades to flat, marks the bundle flat, and records the
  transition (:data:`DEGRADED_ENRICHMENT_UNAVAILABLE`).

That is the degrade floor, and it is the point: **embodiment must never block on
a sibling's unbuilt surface.** When the composite fetch lands, a host swaps in
an adapter (:data:`FetchFn`) that returns a bundle marked ``graph``; nothing
else in this module, or in its consumers, has to change.

Provenance survives the fetch
-----------------------------
Every :class:`BundleItem` carries the **record id** it came from and a **source
label** naming how it was reached. That is not decoration: a compiled memory has
to be able to cite what it was built from (issue #2's perception → action →
durable-memory provenance rule), and a durable record written afterwards carries
those ids in its own ``links`` field. :attr:`RecallBundle.record_ids` is that
citation surface, ready to hand.

The labels are also a **safety** surface. Public eidetic records are committed
into repos and travel with every clone, so store content is *untrusted input* on
its way to a model. A renderer cannot frame text as data-not-instruction unless
it knows the text came from the store — so the label rides on the item, from
fetch to render, and no consumer has to infer it.

Never raises, never invents
---------------------------
Constraint **C3**: a fetch that fails degrades to a recorded transition and an
empty bundle. A dead store, a hostile adapter, an unreadable record, a missing
storage anchor — all of them come back as a :class:`BundleDegradation` on
:attr:`RecallBundle.degradations`, and the host's path is never interrupted.
``KeyboardInterrupt``/``SystemExit`` are the deliberate exception: never raise
means never raise *errors*, not never yield control.

The other half of that promise is that a degraded fetch produces **nothing**. No
placeholder record, no "memory unavailable" note that could be read as recall.
An empty bundle plus a recorded reason is the honest answer; an invented memory
would be the worst outcome this lane could produce.

``level`` alone never reads as success — that is
:mod:`embodiment.continuity`'s trap #2 in a new coat. A bundle whose fetch
collapsed still says ``flat`` (the floor is the floor); what makes it legible is
:attr:`RecallBundle.degraded` and the records behind it.

Composed, not reimplemented
---------------------------
The default adapter calls :func:`embodiment.continuity.recall` and reads what it
returns. Store resolution, ranking, lifecycle filtering, reinforcement and the
mandatory ``data_dir`` anchor are all continuity's (and beneath it eidetic's) —
none of it is restated here, including the refusal to touch an unpinned store.
The store seam is injectable (:data:`RecallFn`), which is what lets the whole
path be exercised with no store at all.

That import is **lazy**, so ``import embodiment.recall_bundle`` costs this module
alone: a host that injects its own store seam never pays for eidetic, and a host
that never fetches never pays at all. The four store defaults are mirrored here
rather than imported for the same reason, and pinned against continuity's by a
drift test so the mirror cannot rot.

Reading the degradations
------------------------
:class:`BundleDegradation` is field-compatible with every other lane's record, so
``ledger.from_continuity(bundle)`` folds a bundle today with no new reader:
codes relayed from the store keep their own ``CODE_*`` token and attribute to the
continuity lane exactly. This module's own ``bundle-*`` codes have no lane of
their own yet and fall back to the container's — a dedicated ``recall_bundle``
lane is one entry in ``ledger._MODULES`` whenever that file is next opened.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Sequence, Union

__all__ = [
    # enrichment levels
    "LEVEL_FLAT",
    "LEVEL_GRAPH",
    "LEVELS",
    "graph_available",
    # per-item source labels
    "SOURCE_RECALL",
    "SOURCE_LINK",
    "SOURCE_TRAVERSAL",
    "SOURCE_VECTOR",
    "SOURCE_HOST",
    "SOURCE_LABELS",
    # adapters
    "ADAPTER_FLAT_RECALL",
    # store defaults, mirrored from continuity and pinned against it
    "DEFAULT_SCOPE",
    "DEFAULT_VISIBILITY",
    "DEFAULT_TOP_K",
    "DEFAULT_MODE",
    "DEFAULT_MAX_ITEMS",
    # degradation vocabulary (C3)
    "DEGRADED_NO_QUERY",
    "DEGRADED_FETCH_FAILED",
    "DEGRADED_UNREADABLE_OUTCOME",
    "DEGRADED_UNREADABLE_RECORD",
    "DEGRADED_ENRICHMENT_UNAVAILABLE",
    "DEGRADED_ITEM_CAP",
    "STAGE_FETCH",
    "STAGE_ASSEMBLE",
    # shapes
    "BundleRequest",
    "BundleItem",
    "BundleProvenance",
    "BundleDegradation",
    "RecallBundle",
    # seams
    "RecallFn",
    "FetchFn",
    # fetching
    "fetch_bundle",
    "flat_fetch",
    "flat_fetcher",
    "item_from_record",
    "provenance_for",
]

_StrPath = Union[str, "os.PathLike[str]"]


# ── enrichment levels ─────────────────────────────────────────────────────────

#: Records plus the ``links``/``supersedes`` fields they already carry, fetched
#: through eidetic's existing search modes. The floor, and everything this
#: module can deliver today.
LEVEL_FLAT = "flat"

#: Traversal plus the vector lines hanging off traversed nodes — the composite
#: fetch proposed to eidetic-cli. Declared so a bundle can *name* what it does
#: not have; nothing in this module produces it.
LEVEL_GRAPH = "graph"

#: The closed vocabulary, floor first.
LEVELS = (LEVEL_FLAT, LEVEL_GRAPH)


def graph_available() -> bool:
    """Whether a graph-level fetch ships in this package.

    Returns ``True`` when eidetic 0.13.0+ is importable and provides
    :func:`eidetic.memory.traverse.discover`.  The import is lazy: reaching
    eidetic is what costs a host, and a host that never asks for graph should
    not pay for it just by importing this module.
    """
    try:
        from eidetic.memory.traverse import discover  # noqa: F401
    except Exception:
        return False
    return True


# ── per-item source labels ────────────────────────────────────────────────────

#: Returned directly by a recall query. The only label a flat fetch produces.
SOURCE_RECALL = "eidetic-recall"
#: Fetched because another record linked to it (a link-resolving adapter's
#: label). Reserved: no adapter here resolves links.
SOURCE_LINK = "eidetic-link"
#: Reached by graph traversal. Reserved for the composite fetch.
SOURCE_TRAVERSAL = "eidetic-graph"
#: A vector line hanging off a traversed node. Reserved for the composite fetch.
SOURCE_VECTOR = "eidetic-vector"
#: Material the runtime supplied itself rather than reading from the store — a
#: host's own prior decisions, for instance. Labelled distinctly so a renderer
#: never frames host material as store material, or the reverse.
SOURCE_HOST = "host"

#: Every label a :class:`BundleItem` may carry.
SOURCE_LABELS = (SOURCE_RECALL, SOURCE_LINK, SOURCE_TRAVERSAL, SOURCE_VECTOR, SOURCE_HOST)

#: The built-in adapter's name, stamped into provenance so a bundle says which
#: fetch produced it.
ADAPTER_FLAT_RECALL = "eidetic-flat-recall"


# ── store defaults, mirrored from continuity ──────────────────────────────────
#
# Mirrored rather than imported so this module's import stays stdlib-cheap (see
# the module docstring). ``tests/test_recall_bundle.py`` pins each against
# ``embodiment.continuity``'s, so the mirror fails loudly instead of drifting.

#: eidetic's own ``--scope`` default.
DEFAULT_SCOPE = "default"
#: eidetic ``docs/contract.md`` §2: public, for in-repo team-shared records.
DEFAULT_VISIBILITY = "public"
#: Hits per query, per the CLI's own default.
DEFAULT_TOP_K = 5
#: eidetic's default search mode (vector+keyword blend).
DEFAULT_MODE = "hybrid"

#: Items one bundle may carry before the fetch stops adding. A *count* cap, not
#: a character budget: a consumer's rendering budget is its own concern (and its
#: own recorded truncation), and clipping text here would destroy exactly the
#: material the fetch exists to deliver. ``0`` disables the cap.
DEFAULT_MAX_ITEMS = 20


# ── degradation vocabulary (C3) ───────────────────────────────────────────────

#: Nothing usable to search with: no queries at all, or blank ones skipped.
DEGRADED_NO_QUERY = "bundle-no-query"
#: The store seam or the fetch adapter raised. The bundle comes back empty.
DEGRADED_FETCH_FAILED = "bundle-fetch-failed"
#: A store outcome or adapter result whose shape could not be read.
DEGRADED_UNREADABLE_OUTCOME = "bundle-outcome-unreadable"
#: One record could not be read as a record. The rest of the fetch survives.
DEGRADED_UNREADABLE_RECORD = "bundle-record-unreadable"
#: The requested enrichment level was not delivered — the degrade floor, made
#: visible. Not a failure: material was still fetched, at a lower level.
DEGRADED_ENRICHMENT_UNAVAILABLE = "bundle-enrichment-unavailable"
#: :attr:`BundleRequest.max_items` dropped whole items. Never a silent clip.
DEGRADED_ITEM_CAP = "bundle-item-cap"

#: Reading the store.
STAGE_FETCH = "fetch"
#: Assembling what was read into a bundle.
STAGE_ASSEMBLE = "assemble"

# Cap on one degradation's reason text, mirroring every sibling lane's cap.
_MAX_REASON_LEN = 500


# ── shapes ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BundleDegradation:
    """One recorded, host-visible fetch degradation (constraint C3).

    Field-compatible with :class:`embodiment.continuity.Degradation` and
    :class:`embodiment.loop.LoopDegradation` so the degradation ledger folds one
    shape rather than yet another.

    A degradation relayed from the store keeps that subsystem's own ``code``
    **verbatim** — the muse runner's ``_absorb`` precedent. Nothing is re-coded
    into this module's vocabulary on the way through, so a host branching on
    :data:`embodiment.continuity.CODE_NO_STORAGE_ANCHOR` still sees it.

    Fields
    ------
    code:
        The stable machine token. Branch on this, never on ``reason``.
    reason:
        Short human-readable cause, capped at 500 characters.
    stage:
        :data:`STAGE_FETCH` or :data:`STAGE_ASSEMBLE`.
    subsystem:
        The sibling library, when the degradation came from one (``eidetic``).
        ``None`` for this module's own records.
    exception:
        The caught exception's class name, when there was one.
    query:
        The query being served when it happened, where one applies.
    record_id:
        The record involved, where one could be identified.
    original:
        The source record exactly as its own lane built it, kept whole.
        Deliberately absent from :meth:`to_dict` — serialising a foreign object
        is that lane's own job.
    """

    code: str
    reason: str
    stage: str = STAGE_FETCH
    subsystem: Optional[str] = None
    exception: Optional[str] = None
    query: Optional[str] = None
    record_id: Optional[str] = None
    original: Any = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "reason": self.reason, "stage": self.stage}
        for name in ("subsystem", "exception", "query", "record_id"):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data


@dataclass(frozen=True)
class BundleItem:
    """One piece of raw material, with the provenance to cite it.

    ``text`` is the record's text **verbatim** — this module never shortens,
    strips or rewrites it. ``raw`` keeps the source record whole, so a consumer
    that needs a field this shape does not name (``score``, ``signal``,
    ``metadata``) reads it there rather than going back to the store.
    """

    record_id: str
    source: str
    text: str
    record_type: str = ""
    query: str = ""
    links: tuple[str, ...] = ()
    supersedes: Optional[str] = None
    created: str = ""
    scope: str = ""
    visibility: str = ""
    raw: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe fold. ``raw`` is omitted; ``record_id`` cites the source."""
        return {
            "record_id": self.record_id,
            "source": self.source,
            "text": self.text,
            "record_type": self.record_type,
            "query": self.query,
            "links": list(self.links),
            "supersedes": self.supersedes,
            "created": self.created,
            "scope": self.scope,
            "visibility": self.visibility,
        }


@dataclass(frozen=True)
class BundleProvenance:
    """How a bundle was fetched — the enrichment level, and what was asked.

    ``level`` is what the fetch actually delivered; ``requested_level`` is what
    the caller asked for. They differ exactly when the degrade floor was used,
    and a :data:`DEGRADED_ENRICHMENT_UNAVAILABLE` record says so alongside.
    """

    level: str = LEVEL_FLAT
    requested_level: str = LEVEL_FLAT
    adapter: str = ""
    queries: tuple[str, ...] = ()
    mode: str = DEFAULT_MODE
    scope: str = DEFAULT_SCOPE
    visibility: str = DEFAULT_VISIBILITY
    top_k: int = DEFAULT_TOP_K

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "requested_level": self.requested_level,
            "adapter": self.adapter,
            "queries": list(self.queries),
            "mode": self.mode,
            "scope": self.scope,
            "visibility": self.visibility,
            "top_k": self.top_k,
        }


@dataclass(frozen=True)
class BundleRequest:
    """What to fetch, and from where. Plain data — it performs nothing.

    ``data_dir`` is passed straight to the store seam, which owns the refusal to
    touch an unpinned store (continuity's trap #1). This module restates none of
    that policy: an absent anchor comes back as continuity's own recorded
    degradation, relayed.

    ``queries`` accepts a bare string as a convenience — it is read as one query
    rather than iterated into characters.

    ``max_items`` caps how many items the bundle carries; the drop is recorded
    (:data:`DEGRADED_ITEM_CAP`), never silent. Search filters, the hybrid
    ``alpha`` and case sensitivity are deliberately **not** exposed: a host that
    needs them supplies its own :data:`FetchFn` rather than getting a
    half-passthrough that looks complete and is not.
    """

    queries: Union[str, Sequence[str]] = ()
    data_dir: Optional[_StrPath] = None
    scope: str = DEFAULT_SCOPE
    visibility: str = DEFAULT_VISIBILITY
    top_k: int = DEFAULT_TOP_K
    mode: str = DEFAULT_MODE
    level: str = LEVEL_FLAT
    max_items: int = DEFAULT_MAX_ITEMS
    reinforce: bool = True
    include_shadowed: bool = False
    include_archived: bool = False


@dataclass(frozen=True)
class RecallBundle:
    """Raw material for compilation, plus how it was obtained.

    Read :attr:`degraded` (or the records behind it) to learn whether anything
    went wrong — :attr:`level` never answers that question. A bundle whose fetch
    collapsed entirely still reports the floor level and simply carries no items.
    """

    provenance: BundleProvenance = field(default_factory=BundleProvenance)
    items: tuple[BundleItem, ...] = ()
    degradations: tuple[BundleDegradation, ...] = ()

    def __len__(self) -> int:
        return len(self.items)

    @property
    def level(self) -> str:
        """The enrichment level actually delivered."""
        return self.provenance.level

    @property
    def degraded(self) -> bool:
        """True iff anything about this fetch degraded. Never inferred elsewhere."""
        return bool(self.degradations)

    @property
    def record_ids(self) -> tuple[str, ...]:
        """The ids this bundle was built from, in order, each once.

        The citation surface: what a compiled memory names as its sources, and
        what a durable record written afterwards carries in its ``links``.
        """
        seen: dict[str, None] = {}
        for item in self.items:
            if item.record_id:
                seen.setdefault(item.record_id, None)
        return tuple(seen)

    @property
    def unresolved_links(self) -> tuple[str, ...]:
        """Link ids no item in this bundle satisfies — what flat did not traverse.

        The flat/graph boundary as data rather than as documentation: at
        :data:`LEVEL_FLAT` these are reported and left alone, because resolving
        them is exactly the traversal this level does not perform.
        """
        present = set(self.record_ids)
        unresolved: dict[str, None] = {}
        for item in self.items:
            for link in item.links:
                if link and link not in present:
                    unresolved.setdefault(link, None)
        return tuple(unresolved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "item_count": len(self.items),
            "record_ids": list(self.record_ids),
            "unresolved_links": list(self.unresolved_links),
            "degradations": [d.to_dict() for d in self.degradations],
        }


# ── seams ─────────────────────────────────────────────────────────────────────

#: The store seam: ``recall(query, *, data_dir, scope, visibility, top_k, mode,
#: reinforce, include_shadowed, include_archived)`` returning something shaped
#: like :class:`embodiment.continuity.RecallOutcome` (``ok``, ``records``,
#: ``degradation``). :func:`embodiment.continuity.recall` satisfies it as-is;
#: anything else that answers that shape does too, which is how the whole path
#: is exercised with no store.
RecallFn = Callable[..., Any]

#: A whole fetch adapter: a request in, a :class:`RecallBundle` out. This is the
#: swap point for eidetic-cli's composite fetch — a graph adapter is one of
#: these, and nothing else changes when it arrives.
FetchFn = Callable[[BundleRequest], RecallBundle]


# ── assembling ────────────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    """A capped string for a recorded reason. Never raises on a hostile value."""
    if isinstance(value, str):
        return value[:_MAX_REASON_LEN]
    return str(value)[:_MAX_REASON_LEN]


def _usable_queries(queries: Any) -> tuple[tuple[str, ...], int]:
    """``(queries worth issuing, how many were dropped)``.

    A bare string is one query, not a sequence of characters — the convenience
    that would otherwise be a footgun. Anything blank or not a string is
    dropped, and the count is returned so the caller can *record* the drop
    rather than swallow it.
    """
    if isinstance(queries, str):
        candidates: list[Any] = [queries]
    elif isinstance(queries, Sequence):
        candidates = list(queries)
    else:
        return (), 0
    kept = tuple(q for q in candidates if isinstance(q, str) and q.strip())
    return kept, len(candidates) - len(kept)


def _readable_id(record: Any) -> Optional[str]:
    """The record's id when it has a usable one — for naming it in a degradation."""
    if isinstance(record, Mapping):
        value = record.get("id")
        if isinstance(value, str) and value.strip():
            return value
    return None


def item_from_record(
    record: Any,
    *,
    source: str = SOURCE_RECALL,
    query: str = "",
) -> Optional[BundleItem]:
    """Map one store record onto a :class:`BundleItem`, or ``None`` if unreadable.

    The single place the record shape is read, exported so a future adapter (the
    composite fetch, a link resolver, a host assembling its own material) reuses
    it instead of copying the mapping. ``text`` is carried verbatim.

    ``None`` means *this record could not be read as a record* — a caller
    records that as :data:`DEGRADED_UNREADABLE_RECORD` and keeps going. No
    exception is raised and no partial item is invented: an item with no id
    could not be cited, and an item with no text is not material.
    """
    if not isinstance(record, Mapping):
        return None
    record_id = record.get("id")
    text = record.get("text")
    if not isinstance(record_id, str) or not record_id.strip():
        return None
    if not isinstance(text, str):
        return None

    scope_data = record.get("scope")
    scope_name = ""
    visibility = ""
    if isinstance(scope_data, Mapping):
        scope_name = scope_data.get("name") if isinstance(scope_data.get("name"), str) else ""
        raw_visibility = scope_data.get("visibility")
        visibility = raw_visibility if isinstance(raw_visibility, str) else ""

    raw_links = record.get("links")
    links = tuple(link for link in raw_links if isinstance(link, str)) if _is_seq(raw_links) else ()
    supersedes = record.get("supersedes")
    record_type = record.get("type")
    created = record.get("created")

    return BundleItem(
        record_id=record_id,
        source=source,
        text=text,
        record_type=record_type if isinstance(record_type, str) else "",
        query=query,
        links=links,
        supersedes=supersedes if isinstance(supersedes, str) else None,
        created=created if isinstance(created, str) else "",
        scope=scope_name,
        visibility=visibility,
        raw=record,
    )


def _is_seq(value: Any) -> bool:
    """A list/tuple of things, as opposed to a string or a scalar."""
    return isinstance(value, (list, tuple))


def provenance_for(
    request: BundleRequest,
    *,
    level: str = LEVEL_FLAT,
    adapter: str = "",
) -> BundleProvenance:
    """Stamp provenance for one fetch of *request*.

    Exported for the same reason as :func:`item_from_record`: an adapter should
    record *what it delivered* through one shared stamp rather than assembling a
    provenance of its own. ``level`` is the adapter's honest claim about what it
    achieved — never what the caller hoped for.
    """
    queries, _ = _usable_queries(request.queries)
    return BundleProvenance(
        level=level,
        requested_level=request.level,
        adapter=adapter,
        queries=queries,
        mode=request.mode,
        scope=request.scope,
        visibility=request.visibility,
        top_k=request.top_k,
    )


# ── the flat adapter — today's eidetic, and nothing more ──────────────────────


def _default_recall() -> RecallFn:
    """The store seam, imported on use.

    Lazy on purpose: reaching :mod:`embodiment.continuity` is what costs a host
    eidetic (deviation d2), and a host that injects its own seam — or never
    fetches — should not pay for it just by importing this module.
    """
    from embodiment import continuity

    return continuity.recall


def _relay(degradation: Any, *, query: str) -> Optional[BundleDegradation]:
    """Adopt a store degradation, keeping its own code and its own object."""
    if degradation is None:
        return None
    code = getattr(degradation, "code", "")
    return BundleDegradation(
        code=_text(code) if code else DEGRADED_UNREADABLE_OUTCOME,
        reason=_text(getattr(degradation, "reason", "")),
        stage=STAGE_FETCH,
        subsystem=_optional_text(getattr(degradation, "subsystem", None)),
        exception=_optional_text(getattr(degradation, "exception", None)),
        query=query,
        original=degradation,
    )


def _optional_text(value: Any) -> Optional[str]:
    return _text(value) if isinstance(value, str) and value else None


def flat_fetch(request: BundleRequest, *, recall_fn: Optional[RecallFn] = None) -> RecallBundle:
    """Fetch at :data:`LEVEL_FLAT` — the floor, over today's eidetic recall.

    One recall per query, in the order given, deduplicated by record id (the
    first sighting keeps the query that surfaced it). Records carry their own
    ``links``/``supersedes``; nothing is traversed and no link is resolved.

    Never raises. A store that raises, answers with an unreadable shape, or
    reports its own degradation is recorded per query and the remaining queries
    still run — one bad query never loses the rest.
    """
    seam = recall_fn if recall_fn is not None else _default_recall()
    queries, dropped = _usable_queries(request.queries)
    items: dict[str, BundleItem] = {}
    degradations: list[BundleDegradation] = []

    if dropped:
        degradations.append(
            BundleDegradation(
                code=DEGRADED_NO_QUERY,
                reason=f"{dropped} unusable quer{'y' if dropped == 1 else 'ies'} skipped",
                stage=STAGE_FETCH,
            )
        )

    for query in queries:
        try:
            outcome = seam(
                query,
                data_dir=request.data_dir,
                scope=request.scope,
                visibility=request.visibility,
                top_k=request.top_k,
                mode=request.mode,
                reinforce=request.reinforce,
                include_shadowed=request.include_shadowed,
                include_archived=request.include_archived,
            )
        except Exception as exc:  # noqa: BLE001 - a store failure never reaches the host
            degradations.append(
                BundleDegradation(
                    code=DEGRADED_FETCH_FAILED,
                    reason=_text(f"{query!r}: {exc}" if str(exc) else f"{query!r}: {type(exc)}"),
                    stage=STAGE_FETCH,
                    exception=type(exc).__name__,
                    query=query,
                )
            )
            continue
        degradations.extend(_read_outcome(outcome, query=query, items=items))

    return RecallBundle(
        provenance=provenance_for(request, level=LEVEL_FLAT, adapter=ADAPTER_FLAT_RECALL),
        items=tuple(items.values()),
        degradations=tuple(degradations),
    )


def _read_outcome(
    outcome: Any,
    *,
    query: str,
    items: dict[str, BundleItem],
) -> list[BundleDegradation]:
    """Read one store outcome into *items*; return what degraded reading it.

    Defensive by design: the seam is injected, so its answer is read through
    ``getattr`` and type checks rather than trusted to be a
    :class:`~embodiment.continuity.RecallOutcome`.
    """
    degradations: list[BundleDegradation] = []
    records = getattr(outcome, "records", None)
    if not _is_seq(records):
        return [
            BundleDegradation(
                code=DEGRADED_UNREADABLE_OUTCOME,
                reason=_text(
                    f"{query!r}: the store returned {type(outcome).__name__}, not records"
                ),
                stage=STAGE_FETCH,
                query=query,
            )
        ]

    relayed = _relay(getattr(outcome, "degradation", None), query=query)
    if relayed is not None:
        degradations.append(relayed)

    for index, record in enumerate(records):
        item = item_from_record(record, source=SOURCE_RECALL, query=query)
        if item is None:
            degradations.append(
                BundleDegradation(
                    code=DEGRADED_UNREADABLE_RECORD,
                    reason=_text(f"{query!r}: record #{index} could not be read as a record"),
                    stage=STAGE_ASSEMBLE,
                    query=query,
                    record_id=_readable_id(record),
                )
            )
            continue
        items.setdefault(item.record_id, item)
    return degradations


def flat_fetcher(recall_fn: Optional[RecallFn] = None) -> FetchFn:
    """Build the flat adapter over *recall_fn* as a plain :data:`FetchFn`.

    ``fetch_bundle(request, fetch=flat_fetcher(my_store))`` is how a host (or a
    test) points the floor adapter at a different store seam without reaching
    into this module.
    """

    def fetch(request: BundleRequest) -> RecallBundle:
        return flat_fetch(request, recall_fn=recall_fn)

    return fetch


# ── the graph adapter — eidetic 0.13.0+ ───────────────────────────────────────


def _default_traverse() -> Any:
    """The traverse engine, imported on use.

    Lazy on purpose: reaching eidetic's traverse module is what costs a host,
    and a host that never asks for graph should not pay for it.
    """
    from eidetic.memory import traverse

    return traverse


def _default_backend() -> Any:
    """The store backend, imported on use. Lazy for the same reason."""
    from eidetic.memory.backend import get_backend

    return get_backend


def _default_scope() -> Any:
    """The scope module, imported on use."""
    from eidetic.memory import scope

    return scope


def graph_fetch(request: BundleRequest, *, recall_fn: Optional[RecallFn] = None) -> RecallBundle:
    """Fetch at :data:`LEVEL_GRAPH` — traversal over the memory graph.

    Uses :func:`eidetic.memory.traverse.discover` (pure, no IO) with injected
    ``fetch`` and ``can_serve`` callables backed by
    :meth:`StoreBackend.get_many` and :func:`eidetic.memory.scope.can_serve`.

    Seeds are the records returned by the flat recall query. The traversal
    follows ``links`` and ``supersedes`` edges breadth-first. Discovered
    records carry :data:`SOURCE_TRAVERSAL` and their hop depth.

    If the traverse module is not available, degrades to flat with a
    :data:`DEGRADED_ENRICHMENT_UNAVAILABLE` record. A traversal that hits
    ``max_depth`` or ``max_nodes`` sets :attr:`TraversalResult.truncated`,
    which becomes a :data:`DEGRADED_BUNDLE_TRUNCATED` degradation.

    Never raises.
    """
    # Lazy import: only pay for traverse when graph is actually requested.
    try:
        traverse_mod = _default_traverse()
        get_backend = _default_backend()
        scope_mod = _default_scope()
    except Exception:
        # Traverse not available — degrade to flat.
        seam = recall_fn if recall_fn is not None else _default_recall()
        flat = flat_fetch(request, recall_fn=seam)
        flat.provenance = provenance_for(request, level=LEVEL_FLAT, adapter=ADAPTER_FLAT_RECALL)
        degradations = list(flat.degradations) + [
            BundleDegradation(
                code=DEGRADED_ENRICHMENT_UNAVAILABLE,
                reason=(
                    f"{LEVEL_GRAPH!r} enrichment was requested; "
                    f"eidetic traverse is not available, fell back to {LEVEL_FLAT!r}"
                ),
                stage=STAGE_FETCH,
            )
        ]
        return RecallBundle(
            provenance=flat.provenance,
            items=flat.items,
            degradations=tuple(degradations),
        )

    # Run the flat fetch first to get seeds.
    seam = recall_fn if recall_fn is not None else _default_recall()
    flat = flat_fetch(request, recall_fn=seam)
    seeds = [item.raw for item in flat.items if item.raw is not None]

    if not seeds:
        # No seeds — nothing to traverse, return flat result.
        return RecallBundle(
            provenance=provenance_for(request, level=LEVEL_GRAPH, adapter="eidetic-graph"),
            items=flat.items,
            degradations=flat.degradations,
        )

    # Build the injected callables for discover().
    backend = get_backend(data_dir=request.data_dir) if request.data_dir else None
    scope = scope_mod.Scope(name=request.scope) if hasattr(scope_mod, "Scope") else None

    def _fetch_one(rid: str) -> Any:
        """Resolve one id via the store backend."""
        if backend is None or scope is None:
            return None
        try:
            results = backend.get_many([rid], scope)
            return results.get(rid)
        except Exception:
            return None

    def _can_serve(record: Any) -> bool:
        """Check if a record is servable (public or same-scope private)."""
        if scope is None:
            return True
        try:
            record_scope = getattr(record, "scope", None)
            if record_scope is None:
                return True
            return scope_mod.can_serve(scope, record_scope)
        except Exception:
            return True

    # Run the traversal.
    max_depth = 3
    max_nodes = request.max_items or DEFAULT_MAX_ITEMS
    try:
        result = traverse_mod.discover(
            seeds,
            _fetch_one,
            _can_serve,
            max_depth,
            max_nodes,
        )
    except Exception as exc:
        # Traversal failed — degrade to flat.
        degradations = list(flat.degradations) + [
            BundleDegradation(
                code=DEGRADED_FETCH_FAILED,
                reason=_text(f"traversal failed: {exc}" if str(exc) else type(exc).__name__),
                stage=STAGE_FETCH,
                exception=type(exc).__name__,
            )
        ]
        return RecallBundle(
            provenance=provenance_for(request, level=LEVEL_FLAT, adapter=ADAPTER_FLAT_RECALL),
            items=flat.items,
            degradations=tuple(degradations),
        )

    # Assemble traversal-discovered items.
    items: dict[str, BundleItem] = dict(flat.items)
    degradations = list(flat.degradations)

    for node in result.nodes:
        item = item_from_record(
            node.record.to_dict() if hasattr(node.record, "to_dict") else node.record,
            source=SOURCE_TRAVERSAL,
        )
        if item is not None:
            items.setdefault(item.record_id, item)
        else:
            degradations.append(
                BundleDegradation(
                    code=DEGRADED_UNREADABLE_RECORD,
                    reason=_text(f"traversal node could not be read as a record"),
                    stage=STAGE_ASSEMBLE,
                )
            )

    # Record truncation if the traversal was cut short.
    if result.truncated:
        degradations.append(
            BundleDegradation(
                code=DEGRADED_ITEM_CAP,
                reason=(
                    f"traversal was truncated at depth {max_depth} / "
                    f"{max_nodes} nodes; more material may exist"
                ),
                stage=STAGE_FETCH,
            )
        )

    return RecallBundle(
        provenance=provenance_for(request, level=LEVEL_GRAPH, adapter="eidetic-graph"),
        items=tuple(items.values()),
        degradations=tuple(degradations),
    )


def graph_fetcher(recall_fn: Optional[RecallFn] = None) -> FetchFn:
    """Build the graph adapter over *recall_fn* as a plain :data:`FetchFn`.

    ``fetch_bundle(request, fetch=graph_fetcher(my_store))`` is how a host
    points the graph adapter at a different store seam.
    """

    def fetch(request: BundleRequest) -> RecallBundle:
        return graph_fetch(request, recall_fn=recall_fn)

    return fetch


# ── the runtime entry point ───────────────────────────────────────────────────


def _adapter_name(fetch: Optional[FetchFn]) -> str:
    """A name for whatever adapter was used, for provenance on a failed fetch."""
    if fetch is None:
        return ADAPTER_FLAT_RECALL
    name = getattr(fetch, "__name__", "")
    return name if isinstance(name, str) and name else type(fetch).__name__


def fetch_bundle(request: BundleRequest, *, fetch: Optional[FetchFn] = None) -> RecallBundle:
    """Fetch raw memory material for *request*. The runtime's entry point.

    Called by a host or by embodiment's own runtime — never by a model, which
    has no way to reach it. Returns a :class:`RecallBundle` in every case:

    * no usable query → an empty bundle plus :data:`DEGRADED_NO_QUERY`, with the
      store never touched;
    * *fetch* omitted → the built-in flat adapter over
      :func:`embodiment.continuity.recall`;
    * *fetch* supplied → that adapter, trusted to report its own level honestly;
    * a level the fetch could not deliver → the material it *could* deliver,
      plus :data:`DEGRADED_ENRICHMENT_UNAVAILABLE` naming both levels;
    * more items than :attr:`BundleRequest.max_items` → the cap applied and
      :data:`DEGRADED_ITEM_CAP` recorded;
    * an adapter that raises or answers with the wrong shape → an empty bundle
      plus a recorded transition.

    Never raises, and never invents material to fill an empty bundle.
    """
    queries, dropped = _usable_queries(request.queries)
    opening: list[BundleDegradation] = []
    if dropped:
        opening.append(
            BundleDegradation(
                code=DEGRADED_NO_QUERY,
                reason=f"{dropped} unusable quer{'y' if dropped == 1 else 'ies'} skipped",
                stage=STAGE_FETCH,
            )
        )
    if not queries:
        opening.append(
            BundleDegradation(
                code=DEGRADED_NO_QUERY,
                reason="no usable query was supplied; the store was not touched",
                stage=STAGE_FETCH,
            )
        )
        return RecallBundle(
            provenance=provenance_for(request, level=LEVEL_FLAT, adapter=_adapter_name(fetch)),
            degradations=tuple(opening),
        )

    # The adapter sees only queries worth issuing, and the skip is already
    # recorded above — so a drop is reported exactly once however deep it went.
    narrowed = replace(request, queries=queries)
    adapter = fetch if fetch is not None else flat_fetcher()

    try:
        bundle: Any = adapter(narrowed)
    except Exception as exc:  # noqa: BLE001 - an adapter failure never reaches the host
        opening.append(
            BundleDegradation(
                code=DEGRADED_FETCH_FAILED,
                reason=_text(f"the fetch adapter failed: {exc}" if str(exc) else f"{type(exc)}"),
                stage=STAGE_FETCH,
                exception=type(exc).__name__,
            )
        )
        return _floor(request, fetch, opening)

    if not isinstance(bundle, RecallBundle):
        opening.append(
            BundleDegradation(
                code=DEGRADED_UNREADABLE_OUTCOME,
                reason=_text(
                    f"the fetch adapter returned {type(bundle).__name__}, not a RecallBundle"
                ),
                stage=STAGE_ASSEMBLE,
            )
        )
        return _floor(request, fetch, opening)

    return _finish(request, bundle, opening)


def _floor(
    request: BundleRequest,
    fetch: Optional[FetchFn],
    degradations: list[BundleDegradation],
) -> RecallBundle:
    """An empty bundle at the floor level — a failed fetch invents nothing."""
    return RecallBundle(
        provenance=provenance_for(request, level=LEVEL_FLAT, adapter=_adapter_name(fetch)),
        degradations=tuple(degradations),
    )


def _finish(
    request: BundleRequest,
    bundle: RecallBundle,
    opening: list[BundleDegradation],
) -> RecallBundle:
    """Apply the item cap and the enrichment check to an adapter's bundle."""
    degradations = [*opening, *bundle.degradations]
    items = bundle.items

    cap = request.max_items
    if isinstance(cap, int) and cap > 0 and len(items) > cap:
        degradations.append(
            BundleDegradation(
                code=DEGRADED_ITEM_CAP,
                reason=(
                    f"the fetch assembled {len(items)} items; the bundle is capped at "
                    f"{cap}, so {len(items) - cap} were dropped whole"
                ),
                stage=STAGE_ASSEMBLE,
            )
        )
        items = items[:cap]

    delivered = bundle.provenance.level
    if delivered != request.level:
        degradations.append(
            BundleDegradation(
                code=DEGRADED_ENRICHMENT_UNAVAILABLE,
                reason=(
                    f"{request.level!r} enrichment was requested; the fetch delivered "
                    f"{delivered!r}. Material was still fetched, at the lower level."
                ),
                stage=STAGE_FETCH,
            )
        )

    return RecallBundle(provenance=bundle.provenance, items=items, degradations=tuple(degradations))
