"""Tests for :mod:`embodiment.recall_bundle` — the runtime-side fetch (task t4).

The module under test assembles **raw memory material** for the muse to compile.
Four properties carry the task's acceptance, and each has its own section below:

1. **The degrade floor (c35/h25).** A rig with only today's ``eidetic-cli``
   installed runs the whole path, and the bundle says so: ``level == "flat"`` in
   its provenance. Asking for ``graph`` on such a rig does not fail and does not
   quietly pretend — it degrades to flat with a **recorded** transition.
2. **Per-item provenance (c34/h24).** Every item carries the record id it came
   from and a source label, so whatever compiles the bundle can cite exactly
   what it was built from.
3. **The fetch is caller-side.** The muse gains no query verb anywhere: it
   cannot call this module, and this module names no model seam.
4. **Never raise (C3).** A fetch that fails degrades to a recorded transition
   and an empty bundle — the host's path is never interrupted, and no memory is
   ever invented.

Hermetic by construction: the store is faked for every test but one, and the
single live test drives the **real** ``eidetic`` in ``keyword`` mode against a
throwaway ``tmp_path`` store — no embedding endpoint, no network, no socket.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import continuity, ledger, muse, recall_bundle
from embodiment.recall_bundle import (
    DEGRADED_ENRICHMENT_UNAVAILABLE,
    DEGRADED_FETCH_FAILED,
    DEGRADED_ITEM_CAP,
    DEGRADED_NO_QUERY,
    DEGRADED_UNREADABLE_OUTCOME,
    DEGRADED_UNREADABLE_RECORD,
    LEVEL_FLAT,
    LEVEL_GRAPH,
    BundleItem,
    BundleRequest,
    RecallBundle,
    fetch_bundle,
    flat_fetch,
    flat_fetcher,
    item_from_record,
)

MODULE_PATH = Path(recall_bundle.__file__).resolve()
MUSE_PATH = Path(muse.__file__).resolve()


# ---------------------------------------------------------------------------
# helpers — a faked store, and eidetic-shaped records
# ---------------------------------------------------------------------------


def _record(
    record_id: str,
    text: str = "the store was migrated after data loss",
    *,
    record_type: str = "decision",
    links: Optional[list[str]] = None,
    supersedes: Optional[str] = None,
    scope: str = "embodiment",
    visibility: str = "public",
) -> dict[str, Any]:
    """One record in exactly the shape ``continuity.recall`` hands back.

    Field-for-field ``eidetic.memory.record.Record.to_dict()`` — copied from a
    real call rather than invented, so the fake cannot drift into a shape the
    live store never produces.
    """
    return {
        "id": record_id,
        "text": text,
        "type": record_type,
        "hash": "0" * 64,
        "metadata": {"tags": ["memory"]},
        "scope": {"name": scope, "visibility": visibility},
        "score": 0.5,
        "created": "2026-07-01T00:00:00+00:00",
        "last_recall": None,
        "recall_count": 0,
        "links": list(links or []),
        "supersedes": supersedes,
        "lifecycle": "active",
        "signal": 0.5,
        "added_by": None,
    }


class FakeStore:
    """A stand-in for :func:`embodiment.continuity.recall`.

    Answers from a ``{query: [record, …]}`` map and records every call, so a
    test can assert not just what came back but exactly what the store was
    asked for. Nothing is read from disk and nothing is dialled.
    """

    def __init__(
        self,
        answers: Optional[dict[str, list[dict[str, Any]]]] = None,
        *,
        degradation: Optional[continuity.Degradation] = None,
        raises: Optional[BaseException] = None,
        outcome: Any = None,
        ok: bool = True,
    ) -> None:
        self.answers = answers or {}
        self.degradation = degradation
        self.raises = raises
        self.outcome = outcome
        self.ok = ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, query: str, **kwargs: Any) -> Any:
        self.calls.append((query, dict(kwargs)))
        if self.raises is not None:
            raise self.raises
        if self.outcome is not None:
            return self.outcome
        return continuity.RecallOutcome(
            ok=self.ok,
            records=list(self.answers.get(query, [])),
            degradation=self.degradation,
        )


def _request(*queries: str, **kwargs: Any) -> BundleRequest:
    kwargs.setdefault("data_dir", "/nowhere/pinned")
    return BundleRequest(queries=tuple(queries), **kwargs)


def _fetch(store: FakeStore, request: BundleRequest) -> RecallBundle:
    """The full runtime path, with the store faked at the seam."""
    return fetch_bundle(request, fetch=flat_fetcher(store))


def _codes(bundle: RecallBundle) -> list[str]:
    return [d.code for d in bundle.degradations]


# ── AST helpers (prose about a boundary is not the boundary) ─────────────────


def _module_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module.split(".")[0])
    return names


def _all_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    return names


def _called_names(path: Path) -> set[str]:
    """Bare names this module CALLS — prose, imports and attributes excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _called_paths(path: Path) -> set[str]:
    """Dotted calls, receiver included (``re.search``, ``continuity.recall``).

    The receiver is what distinguishes a compiled pattern's ``.search`` from a
    memory store's — a bare attribute name cannot, and a test that cannot tell
    them apart fails for writing ordinary code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        f"{ast.unparse(node.func.value)}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _identifiers(path: Path) -> set[str]:
    """Every identifier in the code — docstrings and comments are invisible."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
    return found


# ---------------------------------------------------------------------------
# 1. the degrade floor — flat today, graph when a sibling ships it
# ---------------------------------------------------------------------------


class TestTheDegradeFloor:
    """h25: a rig with only current eidetic-cli runs the full path, marked flat."""

    def test_the_level_vocabulary_is_exactly_two(self):
        assert recall_bundle.LEVELS == (LEVEL_FLAT, LEVEL_GRAPH)
        assert LEVEL_FLAT == "flat" and LEVEL_GRAPH == "graph"

    def test_flat_is_the_default_request_level(self):
        assert BundleRequest().level == LEVEL_FLAT

    def test_a_flat_fetch_marks_itself_flat_in_provenance(self):
        store = FakeStore({"migration": [_record("rec-1")]})
        bundle = _fetch(store, _request("migration"))

        assert bundle.level == LEVEL_FLAT
        assert bundle.provenance.level == LEVEL_FLAT
        assert bundle.provenance.requested_level == LEVEL_FLAT
        assert bundle.provenance.adapter == recall_bundle.ADAPTER_FLAT_RECALL
        assert not bundle.degradations

    def test_asking_for_graph_today_degrades_to_flat_and_says_so(self):
        """The floor: embodiment never blocks on a sibling's unbuilt surface."""
        store = FakeStore({"migration": [_record("rec-1")]})
        bundle = _fetch(store, _request("migration", level=LEVEL_GRAPH))

        assert bundle.level == LEVEL_FLAT
        assert bundle.provenance.requested_level == LEVEL_GRAPH
        assert [i.record_id for i in bundle.items] == ["rec-1"]
        assert DEGRADED_ENRICHMENT_UNAVAILABLE in _codes(bundle)

    def test_the_enrichment_degradation_names_both_levels(self):
        store = FakeStore()
        bundle = _fetch(store, _request("q", level=LEVEL_GRAPH))
        recorded = [d for d in bundle.degradations if d.code == DEGRADED_ENRICHMENT_UNAVAILABLE]

        assert len(recorded) == 1
        assert LEVEL_GRAPH in recorded[0].reason and LEVEL_FLAT in recorded[0].reason

    def test_no_graph_adapter_is_shipped_and_none_is_claimed(self):
        """Nothing here pretends to traverse; the composite fetch is not built."""
        assert recall_bundle.graph_available() is False

    def test_a_host_adapter_reporting_graph_is_honoured_unchanged(self):
        """The swap: when eidetic ships the composite fetch, only this changes."""

        def graph_adapter(request: BundleRequest) -> RecallBundle:
            return RecallBundle(
                provenance=recall_bundle.provenance_for(
                    request, level=LEVEL_GRAPH, adapter="host-graph"
                ),
                items=(
                    BundleItem(
                        record_id="rec-9",
                        source=recall_bundle.SOURCE_TRAVERSAL,
                        text="reached by traversal",
                    ),
                ),
            )

        bundle = fetch_bundle(_request("migration", level=LEVEL_GRAPH), fetch=graph_adapter)

        assert bundle.level == LEVEL_GRAPH
        assert bundle.provenance.adapter == "host-graph"
        assert not bundle.degradations

    def test_an_adapter_delivering_less_than_asked_is_recorded_not_hidden(self):
        def honest_but_flat(request: BundleRequest) -> RecallBundle:
            return RecallBundle(provenance=recall_bundle.provenance_for(request, level=LEVEL_FLAT))

        bundle = fetch_bundle(_request("q", level=LEVEL_GRAPH), fetch=honest_but_flat)

        assert bundle.level == LEVEL_FLAT
        assert DEGRADED_ENRICHMENT_UNAVAILABLE in _codes(bundle)

    def test_an_unknown_level_is_refused_at_the_floor(self):
        store = FakeStore({"q": [_record("rec-1")]})
        bundle = _fetch(store, _request("q", level="telepathy"))

        assert bundle.level == LEVEL_FLAT
        assert bundle.provenance.requested_level == "telepathy"
        assert DEGRADED_ENRICHMENT_UNAVAILABLE in _codes(bundle)


# ---------------------------------------------------------------------------
# 2. per-item provenance — every item cites its record
# ---------------------------------------------------------------------------


class TestPerItemProvenance:
    """h24: whatever compiles this can cite exactly what it was built from."""

    def test_every_item_carries_its_record_id_and_a_source_label(self):
        store = FakeStore({"a": [_record("rec-1"), _record("rec-2")], "b": [_record("rec-3")]})
        bundle = _fetch(store, _request("a", "b"))

        assert [i.record_id for i in bundle.items] == ["rec-1", "rec-2", "rec-3"]
        for item in bundle.items:
            assert item.record_id
            assert item.source in recall_bundle.SOURCE_LABELS

    def test_a_flat_fetch_labels_everything_as_recalled_and_nothing_as_traversed(self):
        store = FakeStore({"a": [_record("rec-1")]})
        bundle = _fetch(store, _request("a"))

        assert {i.source for i in bundle.items} == {recall_bundle.SOURCE_RECALL}
        assert recall_bundle.SOURCE_TRAVERSAL not in {i.source for i in bundle.items}
        assert recall_bundle.SOURCE_VECTOR not in {i.source for i in bundle.items}

    def test_an_item_records_which_query_surfaced_it(self):
        store = FakeStore({"a": [_record("rec-1")], "b": [_record("rec-2")]})
        bundle = _fetch(store, _request("a", "b"))

        assert {i.record_id: i.query for i in bundle.items} == {"rec-1": "a", "rec-2": "b"}

    def test_record_ids_is_the_citation_surface(self):
        """What a durable record's ``links`` are built from downstream (t6)."""
        store = FakeStore({"a": [_record("rec-1"), _record("rec-2")]})
        bundle = _fetch(store, _request("a"))

        assert bundle.record_ids == ("rec-1", "rec-2")

    def test_links_and_supersedes_ride_through_verbatim(self):
        store = FakeStore({"a": [_record("rec-1", links=["rec-7", "rec-8"], supersedes="rec-0")]})
        item = _fetch(store, _request("a")).items[0]

        assert item.links == ("rec-7", "rec-8")
        assert item.supersedes == "rec-0"

    def test_unresolved_links_name_what_flat_did_not_traverse(self):
        """The flat/graph boundary, made concrete rather than asserted in prose."""
        store = FakeStore({"a": [_record("rec-1", links=["rec-2", "rec-7"]), _record("rec-2")]})
        bundle = _fetch(store, _request("a"))

        assert bundle.unresolved_links == ("rec-7",)

    def test_the_record_text_is_carried_verbatim_never_summarised(self):
        text = "  Ragged   text\nwith a newline and trailing space  "
        store = FakeStore({"a": [_record("rec-1", text)]})
        item = _fetch(store, _request("a")).items[0]

        assert item.text == text

    def test_nothing_in_this_module_clips_a_record(self):
        """Budgeting is the consumer's (task t5); the fetch delivers all of it."""
        long_text = "x" * 5000
        store = FakeStore({"a": [_record("rec-1", long_text)]})

        assert _fetch(store, _request("a")).items[0].text == long_text

    def test_the_source_record_survives_whole_on_the_item(self):
        raw = _record("rec-1")
        store = FakeStore({"a": [raw]})
        item = _fetch(store, _request("a")).items[0]

        assert item.raw == raw
        assert item.record_type == "decision"
        assert (item.scope, item.visibility) == ("embodiment", "public")
        assert item.created == "2026-07-01T00:00:00+00:00"

    def test_the_same_record_from_two_queries_is_carried_once(self):
        shared = _record("rec-1")
        store = FakeStore({"a": [shared], "b": [shared, _record("rec-2")]})
        bundle = _fetch(store, _request("a", "b"))

        assert bundle.record_ids == ("rec-1", "rec-2")
        assert bundle.items[0].query == "a", "first sighting wins, and says which query"

    def test_an_item_carries_no_callable_and_no_decision_field(self):
        """Material, not authority: there is nothing here to bind an action to."""
        store = FakeStore({"a": [_record("rec-1")]})
        item = _fetch(store, _request("a")).items[0]

        for name in ("decision", "approve", "deny", "rewrite", "execute", "arguments"):
            assert not hasattr(item, name), name
        for name, value in vars(item).items():
            assert not callable(value), name

    def test_item_from_record_is_the_reusable_mapping(self):
        """A future graph adapter reuses this rather than copying the mapping."""
        item = item_from_record(
            _record("rec-1"), source=recall_bundle.SOURCE_HOST, query="prior decisions"
        )

        assert item is not None
        assert (item.record_id, item.source, item.query) == (
            "rec-1",
            recall_bundle.SOURCE_HOST,
            "prior decisions",
        )

    @pytest.mark.parametrize(
        "bad",
        [
            None,
            "a string",
            {"text": "no id at all", "type": "note"},
            {"id": "", "text": "empty id", "type": "note"},
            {"id": "rec-1", "type": "note"},
            {"id": 17, "text": "numeric id", "type": "note"},
        ],
    )
    def test_item_from_record_reads_an_unusable_record_as_absent(self, bad: Any):
        assert item_from_record(bad) is None


# ---------------------------------------------------------------------------
# 3. the full path, end to end
# ---------------------------------------------------------------------------


class TestTheFullPathWithAFakedStore:
    """Acceptance 1, hermetically: no real store, no network, no interpreter."""

    def test_the_whole_path_runs_and_the_bundle_is_marked_flat(self):
        store = FakeStore({"data loss": [_record("rec-1"), _record("rec-2")]})
        bundle = _fetch(store, _request("data loss"))

        assert bundle.provenance.level == LEVEL_FLAT
        assert bundle.provenance.queries == ("data loss",)
        assert len(bundle.items) == 2
        assert not bundle.degraded

    def test_the_store_is_asked_exactly_what_the_request_says(self):
        store = FakeStore()
        request = _request(
            "q",
            data_dir="/tmp/pinned-store",  # nosec B108 - a string, never touched
            scope="embodiment",
            visibility="private",
            top_k=9,
            mode="keyword",
            reinforce=False,
            include_shadowed=True,
            include_archived=True,
        )
        _fetch(store, request)
        query, kwargs = store.calls[0]

        assert query == "q"
        assert kwargs == {
            "data_dir": "/tmp/pinned-store",  # nosec B108 - a string, never touched
            "scope": "embodiment",
            "visibility": "private",
            "top_k": 9,
            "mode": "keyword",
            "reinforce": False,
            "include_shadowed": True,
            "include_archived": True,
        }

    def test_one_call_per_query_in_the_order_given(self):
        store = FakeStore()
        _fetch(store, _request("first", "second", "third"))

        assert [query for query, _ in store.calls] == ["first", "second", "third"]

    def test_a_single_string_query_is_not_iterated_as_characters(self):
        store = FakeStore()
        fetch_bundle(BundleRequest(queries="migration", data_dir="/x"), fetch=flat_fetcher(store))

        assert [query for query, _ in store.calls] == ["migration"]

    def test_the_bundle_folds_to_json(self):
        store = FakeStore({"a": [_record("rec-1", links=["rec-2"])]})
        data = _fetch(store, _request("a")).to_dict()

        assert json.loads(json.dumps(data))["provenance"]["level"] == LEVEL_FLAT
        assert data["items"][0]["record_id"] == "rec-1"
        assert data["items"][0]["source"] == recall_bundle.SOURCE_RECALL
        assert data["item_count"] == 1

    def test_the_bundle_is_frozen(self):
        bundle = _fetch(FakeStore(), _request("a"))
        with pytest.raises(FrozenInstanceError):
            bundle.items = ()  # type: ignore[misc]

    def test_the_default_adapter_needs_no_injection_at_all(self):
        """A host that supplies nothing still gets a bundle, never an exception."""
        bundle = fetch_bundle(BundleRequest(queries=("q",)))

        assert bundle.level == LEVEL_FLAT
        assert bundle.items == ()
        assert continuity.CODE_NO_STORAGE_ANCHOR in _codes(bundle), "no anchor, no store touched"


class TestTheFullPathAgainstTodaysEidetic:
    """Acceptance 1, live: current ``eidetic-cli``, nothing else installed.

    ``keyword`` mode is eidetic's fully offline search (``memory/scoring.py``),
    so this dials no embedding endpoint. The store is a throwaway ``tmp_path``.
    """

    @pytest.fixture()
    def store_dir(self, tmp_path: Path) -> Path:
        if not continuity.eidetic_available():  # pragma: no cover - eidetic is a base dep
            pytest.skip("eidetic is not importable in this environment")
        data_dir = tmp_path / "memory"
        for record in (
            {
                "id": "rec-1",
                "text": "the store was migrated after data loss",
                "type": "decision",
                "links": ["rec-2", "rec-absent"],
                "supersedes": "rec-0",
            },
            {"id": "rec-2", "text": "migrated stores keep their provenance", "type": "gotcha"},
        ):
            outcome = continuity.remember(record, data_dir=data_dir, scope="embodiment")
            assert outcome.ok, outcome.degradation
        return data_dir

    def test_a_rig_with_only_current_eidetic_runs_the_whole_path(self, store_dir: Path):
        bundle = fetch_bundle(
            BundleRequest(
                queries=("migrated",),
                data_dir=store_dir,
                scope="embodiment",
                mode="keyword",
                top_k=5,
            )
        )

        assert bundle.provenance.level == LEVEL_FLAT
        assert bundle.provenance.adapter == recall_bundle.ADAPTER_FLAT_RECALL
        assert set(bundle.record_ids) == {"rec-1", "rec-2"}
        assert not bundle.degradations
        assert "rec-absent" in bundle.unresolved_links

    def test_the_live_bundle_carries_the_links_eidetic_round_trips(self, store_dir: Path):
        bundle = fetch_bundle(
            BundleRequest(
                queries=("data loss",), data_dir=store_dir, scope="embodiment", mode="keyword"
            )
        )
        item = next(i for i in bundle.items if i.record_id == "rec-1")

        assert item.links == ("rec-2", "rec-absent")
        assert item.supersedes == "rec-0"
        assert item.source == recall_bundle.SOURCE_RECALL


# ---------------------------------------------------------------------------
# 4. never raise — every failure is a recorded transition (C3)
# ---------------------------------------------------------------------------


class TestNeverRaises:
    """A presence layer that fails loudly into an app's main path is worse than none."""

    def test_a_store_that_raises_degrades_to_an_empty_bundle(self):
        store = FakeStore(raises=RuntimeError("neo4j went away"))
        bundle = _fetch(store, _request("a"))

        assert bundle.items == ()
        assert DEGRADED_FETCH_FAILED in _codes(bundle)
        assert bundle.degradations[0].exception == "RuntimeError"
        assert "neo4j went away" in bundle.degradations[0].reason

    @pytest.mark.parametrize(
        "exc", [RuntimeError("x"), ValueError("y"), OSError("z"), TypeError("w")]
    )
    def test_every_exception_class_degrades_rather_than_propagates(self, exc: Exception):
        bundle = _fetch(FakeStore(raises=exc), _request("a"))

        assert bundle.degraded
        assert DEGRADED_FETCH_FAILED in _codes(bundle)

    def test_a_keyboard_interrupt_still_reaches_the_host(self):
        """Never raise means never raise *errors*, not never yield control."""
        with pytest.raises(KeyboardInterrupt):
            _fetch(FakeStore(raises=KeyboardInterrupt()), _request("a"))

    def test_a_hostile_adapter_degrades_like_a_hostile_store(self):
        def explode(_request: BundleRequest) -> RecallBundle:
            raise RuntimeError("adapter is broken")

        bundle = fetch_bundle(_request("a"), fetch=explode)

        assert bundle.items == ()
        assert DEGRADED_FETCH_FAILED in _codes(bundle)
        assert bundle.level == LEVEL_FLAT

    def test_a_failed_fetch_still_names_the_adapter_that_failed(self):
        """A callable object is an adapter too, and provenance names it."""

        class BrokenAdapter:
            def __call__(self, request: BundleRequest) -> RecallBundle:
                raise RuntimeError("nope")

        bundle = fetch_bundle(_request("a"), fetch=BrokenAdapter())

        assert bundle.provenance.adapter == "BrokenAdapter"
        assert DEGRADED_FETCH_FAILED in _codes(bundle)

    def test_a_wordless_exception_still_produces_a_reason(self):
        bundle = _fetch(FakeStore(raises=RuntimeError()), _request("a"))

        assert DEGRADED_FETCH_FAILED in _codes(bundle)
        assert bundle.degradations[0].reason, "a silent exception is still recorded"

    def test_an_adapter_returning_the_wrong_shape_is_recorded(self):
        bundle = fetch_bundle(_request("a"), fetch=lambda request: {"items": []})

        assert bundle.items == ()
        assert DEGRADED_UNREADABLE_OUTCOME in _codes(bundle)

    def test_a_store_outcome_of_the_wrong_shape_is_recorded(self):
        bundle = _fetch(FakeStore(outcome=object()), _request("a"))

        assert bundle.items == ()
        assert DEGRADED_UNREADABLE_OUTCOME in _codes(bundle)

    def test_a_continuity_degradation_is_relayed_with_its_own_code(self):
        """The store's vocabulary is absorbed verbatim, never re-coded here."""
        degradation = continuity.Degradation(
            subsystem="eidetic",
            stage="recall",
            code=continuity.CODE_NO_STORAGE_ANCHOR,
            reason="no data_dir was supplied",
        )
        bundle = _fetch(FakeStore(ok=False, degradation=degradation), _request("a"))
        relayed = bundle.degradations[0]

        assert relayed.code == continuity.CODE_NO_STORAGE_ANCHOR
        assert relayed.subsystem == "eidetic"
        assert relayed.original is degradation

    def test_a_relayed_degradation_does_not_cost_the_records_that_did_arrive(self):
        """Recall's reinforce-failed path: records returned, write-back recorded."""
        degradation = continuity.Degradation(
            subsystem="eidetic",
            stage="recall",
            code=continuity.CODE_REINFORCE_FAILED,
            reason="write-back failed",
        )
        store = FakeStore({"a": [_record("rec-1")]}, degradation=degradation)
        bundle = _fetch(store, _request("a"))

        assert bundle.record_ids == ("rec-1",)
        assert continuity.CODE_REINFORCE_FAILED in _codes(bundle)

    def test_one_unreadable_record_never_loses_the_rest(self):
        store = FakeStore({"a": [_record("rec-1"), {"broken": True}, _record("rec-3")]})
        bundle = _fetch(store, _request("a"))

        assert bundle.record_ids == ("rec-1", "rec-3")
        assert DEGRADED_UNREADABLE_RECORD in _codes(bundle)

    def test_an_unreadable_record_degradation_names_what_it_could_read(self):
        store = FakeStore({"a": [{"id": "rec-2", "type": "note"}]})
        recorded = _fetch(store, _request("a")).degradations[0]

        assert recorded.code == DEGRADED_UNREADABLE_RECORD
        assert recorded.record_id == "rec-2"
        assert recorded.query == "a"

    def test_a_hostile_degradation_is_relayed_rather_than_dropped(self):
        """A store shape this seam cannot read is still reported, not swallowed."""

        class OddDegradation:
            code = "store-specific-code"
            reason = 12345

        bundle = _fetch(FakeStore(ok=False, degradation=OddDegradation()), _request("a"))
        relayed = bundle.degradations[0]

        assert relayed.code == "store-specific-code"
        assert relayed.reason == "12345"

    def test_an_uninjected_fetch_with_nothing_to_ask_names_the_builtin_adapter(self):
        """No seam, no query, no store touched — and provenance still says who."""
        bundle = fetch_bundle(BundleRequest())

        assert bundle.provenance.adapter == recall_bundle.ADAPTER_FLAT_RECALL
        assert DEGRADED_NO_QUERY in _codes(bundle)
        assert bundle.items == ()

    def test_a_queries_field_that_is_not_a_sequence_reads_as_no_query(self):
        """Config-driven requests exist; ``None`` is data, not a programming error."""
        store = FakeStore()
        bundle = fetch_bundle(BundleRequest(queries=None, data_dir="/x"), fetch=flat_fetcher(store))

        assert store.calls == []
        assert DEGRADED_NO_QUERY in _codes(bundle)

    def test_the_adapter_records_its_own_skips_when_called_directly(self):
        """A host that bypasses ``fetch_bundle`` is not thereby left in the dark."""
        store = FakeStore({"real": [_record("rec-1")]})
        bundle = flat_fetch(_request("real", "   "), recall_fn=store)

        assert bundle.record_ids == ("rec-1",)
        assert DEGRADED_NO_QUERY in _codes(bundle)

    def test_no_query_means_no_store_call_at_all(self):
        store = FakeStore()
        bundle = _fetch(store, _request())

        assert store.calls == []
        assert bundle.items == ()
        assert DEGRADED_NO_QUERY in _codes(bundle)

    def test_blank_queries_are_skipped_and_the_skip_is_recorded(self):
        store = FakeStore({"real": [_record("rec-1")]})
        bundle = _fetch(store, _request("real", "   ", ""))

        assert [query for query, _ in store.calls] == ["real"]
        assert bundle.record_ids == ("rec-1",)
        assert DEGRADED_NO_QUERY in _codes(bundle)

    def test_the_item_cap_truncates_loudly_or_not_at_all(self):
        store = FakeStore({"a": [_record(f"rec-{n}") for n in range(6)]})
        bundle = _fetch(store, _request("a", max_items=2))

        assert bundle.record_ids == ("rec-0", "rec-1")
        capped = [d for d in bundle.degradations if d.code == DEGRADED_ITEM_CAP]
        assert len(capped) == 1
        assert "6" in capped[0].reason and "2" in capped[0].reason

    def test_an_uncapped_bundle_keeps_everything(self):
        store = FakeStore({"a": [_record(f"rec-{n}") for n in range(6)]})
        bundle = _fetch(store, _request("a", max_items=0))

        assert len(bundle.items) == 6
        assert DEGRADED_ITEM_CAP not in _codes(bundle)

    def test_a_degraded_bundle_never_invents_material(self):
        bundle = _fetch(FakeStore(raises=RuntimeError("down")), _request("a"))

        assert bundle.items == ()
        assert bundle.record_ids == ()
        assert bundle.unresolved_links == ()

    def test_level_alone_never_reads_as_success(self):
        """continuity's trap #2, restated: read ``degradations``, not ``level``."""
        bundle = _fetch(FakeStore(raises=RuntimeError("down")), _request("a"))

        assert bundle.level == LEVEL_FLAT
        assert bundle.degraded is True

    def test_every_degradation_folds_to_json(self):
        bundle = _fetch(FakeStore(raises=RuntimeError("down")), _request("a"))
        data = bundle.degradations[0].to_dict()

        assert json.loads(json.dumps(data))["code"] == DEGRADED_FETCH_FAILED
        assert "original" not in data, "the source object is kept, never serialised here"

    def test_a_runaway_reason_is_capped(self):
        bundle = _fetch(FakeStore(raises=RuntimeError("x" * 5000)), _request("a"))

        assert len(bundle.degradations[0].reason) <= 500


class TestTheDegradationsAreHostVisible:
    """C3 in aggregate: the existing ledger reader folds a bundle unchanged."""

    def test_the_ledger_folds_a_bundle_without_a_dedicated_reader(self):
        degradation = continuity.Degradation(
            subsystem="eidetic",
            stage="recall",
            code=continuity.CODE_SUBSYSTEM_ERROR,
            reason="store exploded",
            exception="RuntimeError",
        )
        bundle = _fetch(FakeStore(ok=False, degradation=degradation), _request("a"))
        records = ledger.from_continuity(bundle)

        assert [r.code for r in records] == [continuity.CODE_SUBSYSTEM_ERROR]
        assert records[0].source == ledger.SOURCE_CONTINUITY
        assert records[0].subsystem == "eidetic"

    def test_this_modules_own_codes_collide_with_no_existing_lane(self):
        """A future ledger lane can adopt them verbatim (task t10's neighbourhood)."""
        existing = {entry.code for entry in ledger.known_codes()}
        mine = {
            getattr(recall_bundle, name)
            for name in recall_bundle.__all__
            if name.startswith("DEGRADED_")
        }

        assert mine and not (mine & existing)


# ---------------------------------------------------------------------------
# 5. the fetch is caller-side — the muse gains no query verb
# ---------------------------------------------------------------------------


class TestTheMuseGainsNoQueryVerb:
    """h14: memory reaches the muse as injected context, never as a capability."""

    _QUERY_VERBS = frozenset(
        {
            "fetch",
            "fetch_bundle",
            "flat_fetch",
            "flat_fetcher",
            "recall",
            "remember",
            "traverse",
        }
    )

    #: Modules a query would have to go through. Named as receivers, so a
    #: compiled pattern's ``.search`` is not mistaken for a store's.
    _MEMORY_RECEIVERS = ("continuity.", "recall_bundle.", "eidetic.", "store.")

    def test_the_muse_module_calls_no_fetch_verb(self):
        """The runtime fetches; the muse is handed the result, if anything."""
        assert not (_called_names(MUSE_PATH) & self._QUERY_VERBS)

    def test_the_muse_module_calls_nothing_on_a_memory_module(self):
        """t5 renders an injected bundle; it must never reach for one itself."""
        reached = [
            path for path in _called_paths(MUSE_PATH) if path.startswith(self._MEMORY_RECEIVERS)
        ]

        assert not reached, reached

    def test_the_muse_loop_exposes_no_query_surface(self):
        assert not (set(dir(muse.MuseLoop)) & self._QUERY_VERBS)

    def test_the_muse_constructor_takes_no_fetch_seam(self):
        params = set(inspect.signature(muse.MuseLoop.__init__).parameters)

        assert not (params & self._QUERY_VERBS)

    def test_this_module_never_imports_the_muse(self):
        assert "muse" not in _all_imports(MODULE_PATH)

    def test_this_module_holds_no_model_seam(self):
        """Nothing here completes a turn, so nothing here can be prompted."""
        for func in (fetch_bundle, flat_fetch, item_from_record):
            params = set(inspect.signature(func).parameters)
            assert not (params & {"complete", "model", "messages", "prompt", "system"}), func

    def test_this_module_names_no_acting_vocabulary(self):
        identifiers = _identifiers(MODULE_PATH)
        for token in (
            "tool_calls",
            "ToolCall",
            "ToolExecutor",
            "MuseCompleteFn",
            "MuseLoop",
            "approve",
            "deny",
            "rewrite",
            "execute",
        ):
            assert token not in identifiers, token

    def test_this_module_opens_no_socket_and_spawns_nothing(self):
        imported = set(_all_imports(MODULE_PATH))

        assert not (imported & {"subprocess", "socket", "httpx", "requests", "urllib", "shlex"})


# ---------------------------------------------------------------------------
# 6. module posture — cheap, composed, pinned against drift
# ---------------------------------------------------------------------------


class TestModulePosture:
    """The seam composes eidetic through continuity; it reimplements neither."""

    def test_module_scope_imports_are_stdlib_only(self):
        """Importing the fetch must not cost a host the memory subsystem."""
        import sys

        for name in _module_level_imports(MODULE_PATH):
            assert name in sys.stdlib_module_names or name == "__future__", name

    def test_continuity_is_reached_lazily_and_is_the_only_sibling(self):
        assert "continuity" not in _module_level_imports(MODULE_PATH)
        assert "embodiment" in _all_imports(MODULE_PATH)

    def test_no_store_logic_is_reimplemented_here(self):
        """Composition, not a second store: eidetic's internals stay eidetic's."""
        assert "eidetic" not in _all_imports(MODULE_PATH)

    def test_this_module_imports_no_colleague(self):
        assert "colleague" not in _all_imports(MODULE_PATH)

    @pytest.mark.parametrize(
        ("mine", "theirs"),
        [
            ("DEFAULT_SCOPE", "DEFAULT_SCOPE"),
            ("DEFAULT_VISIBILITY", "DEFAULT_VISIBILITY"),
            ("DEFAULT_TOP_K", "DEFAULT_TOP_K"),
            ("DEFAULT_MODE", "DEFAULT_MODE"),
        ],
    )
    def test_the_mirrored_defaults_match_continuity(self, mine: str, theirs: str):
        """Mirrored so the module stays import-cheap — pinned so it cannot drift."""
        assert getattr(recall_bundle, mine) == getattr(continuity, theirs)

    def test_every_public_name_resolves(self):
        for name in recall_bundle.__all__:
            assert getattr(recall_bundle, name) is not None, name

    def test_the_source_label_vocabulary_is_closed(self):
        assert recall_bundle.SOURCE_RECALL in recall_bundle.SOURCE_LABELS
        assert len(set(recall_bundle.SOURCE_LABELS)) == len(recall_bundle.SOURCE_LABELS)

    def test_an_empty_bundle_is_falsy_and_a_full_one_is_not(self):
        store = FakeStore({"a": [_record("rec-1")]})

        assert not _fetch(FakeStore(), _request("a")).items
        assert len(_fetch(store, _request("a"))) == 1
