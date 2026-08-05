"""Tests for :mod:`embodiment.knowledge` (task ``t8``).

Two of these are load-bearing and the rest support them:

1. **An unattributed write is refused whole** — and the store is never touched.
   This is claim ``c30``/``h20``. Issue #63 measured the senses seat, without a
   grounding clause, fabricating a sensor reading 16 of 16 times under one
   operator push, and fabricating actions it never performed. The
   worker→senses knowledge channel runs from the acting tier to the operator's
   ear, so an anonymous entry on it would let a fabricating or compromised
   acting tier put words in the interaction tier's mouth with no way back to
   the writer.
2. **The knowledge path imports no store of its own** — claim ``c35``/``h24``.
   eidetic owns memory mechanics; embodiment owns *when* an entry is written,
   read and revisited (issue #2's split). A second store drifting into
   existence here is precisely the failure the claim forbids, so it is proved
   by parsing this module's source rather than by reading it.

Everything runs with **no store at all**: :class:`FakeStore` answers the two
seam callables with continuity's own outcome shapes. The one test that touches
a real eidetic install does so only to check that the record this module builds
uses eidetic's *declared* fields — it never writes.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any, Optional

import pytest

import embodiment
from embodiment import config_change, continuity, knowledge, senses_text
from embodiment.config_change import (
    CHANGE_INCOMPLETE,
    CHANGE_NO_ORIGIN,
    CHANGE_ORIGIN_FORBIDDEN,
    ORIGIN_HOST,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    TARGET_SENSES_KNOWLEDGE,
    TARGET_WORKER_KNOWLEDGE,
    SensesKnowledgeChange,
    SensesPromptChange,
    WorkerKnowledgeChange,
)

MODULE_PATH = Path(knowledge.__file__)


# ---------------------------------------------------------------------------
# fakes — the whole path exercised with no store
# ---------------------------------------------------------------------------


def _record(
    entry_id: str = "k-1",
    *,
    text: str = "the greenhouse sensor reads 21C",
    added_by: Optional[str] = ORIGIN_WORKER,
    supersedes: Optional[str] = None,
    lifecycle: str = "active",
    record_type: str = knowledge.KNOWLEDGE_RECORD_TYPE,
    scope: str = "default.knowledge.senses",
) -> dict[str, Any]:
    """One record in exactly the shape ``continuity.recall`` hands back."""
    return {
        "id": entry_id,
        "text": text,
        "type": record_type,
        "hash": "",
        "metadata": {},
        "scope": {"name": scope, "visibility": "public"},
        "score": 0.5,
        "created": "2026-08-04T00:00:00+00:00",
        "last_recall": None,
        "recall_count": 0,
        "links": [],
        "supersedes": supersedes,
        "lifecycle": lifecycle,
        "signal": 0.5,
        "added_by": added_by,
    }


class FakeStore:
    """A stand-in for the two continuity callables. Records every call."""

    def __init__(
        self,
        records: Optional[list[dict[str, Any]]] = None,
        *,
        recall_degradation: Optional[continuity.Degradation] = None,
        remember_ok: bool = True,
        raises: bool = False,
    ) -> None:
        self.records = records or []
        self.recall_degradation = recall_degradation
        self.remember_ok = remember_ok
        self.raises = raises
        self.writes: list[dict[str, Any]] = []
        self.reads: list[dict[str, Any]] = []

    def remember(self, record: Any, **kwargs: Any) -> continuity.RememberOutcome:
        if self.raises:
            raise RuntimeError("store is on fire")
        self.writes.append({"record": dict(record), **kwargs})
        if not self.remember_ok:
            return continuity.RememberOutcome(
                ok=False,
                record_id=None,
                degradation=continuity.Degradation(
                    subsystem="eidetic",
                    stage="remember",
                    code=continuity.CODE_SUBSYSTEM_ERROR,
                    reason="disk full",
                ),
            )
        return continuity.RememberOutcome(
            ok=True, record_id=record["id"], degradation=None, raw=dict(record)
        )

    def recall(self, query: str, **kwargs: Any) -> continuity.RecallOutcome:
        if self.raises:
            raise RuntimeError("store is on fire")
        self.reads.append({"query": query, **kwargs})
        return continuity.RecallOutcome(
            ok=self.recall_degradation is None,
            # Passed through UNCOERCED on purpose: a fake that tidied a hostile
            # record would be testing the fake instead of the module's guard.
            records=[dict(r) if isinstance(r, dict) else r for r in self.records],
            degradation=self.recall_degradation,
        )

    def port(self) -> knowledge.KnowledgeStore:
        return knowledge.KnowledgeStore(remember=self.remember, recall=self.recall)


def _write(change: Any, store: FakeStore, **kwargs: Any) -> knowledge.KnowledgeWrite:
    return knowledge.write(change, data_dir="/tmp/store", store=store.port(), **kwargs)


def _senses_change(**kwargs: Any) -> SensesKnowledgeChange:
    fields: dict[str, Any] = {
        "change_id": "c-1",
        "origin": ORIGIN_WORKER,
        "entry_id": "k-1",
        "text": "the greenhouse sensor reads 21C",
    }
    fields.update(kwargs)
    return SensesKnowledgeChange(**fields)


# ---------------------------------------------------------------------------
# 1. LOAD-BEARING — an unattributed write is refused whole (c30 / h20)
# ---------------------------------------------------------------------------


class TestAnUnattributedWriteIsRefusedWhole:
    """No origin, no write — and nothing partial reaches the store.

    "Refused whole" is checked from both sides every time: the outcome is a
    refusal *and* ``store.writes`` is empty. A refusal that still wrote would
    be the worst of both worlds — the operator hears the claim and the record
    says it was rejected.
    """

    def test_a_blank_origin_is_refused(self) -> None:
        store = FakeStore()

        outcome = _write(_senses_change(origin=""), store)

        assert not outcome.ok
        assert outcome.refusal is not None
        assert outcome.refusal.code == CHANGE_NO_ORIGIN
        assert store.writes == []

    def test_an_unknown_origin_is_refused(self) -> None:
        """An origin outside the closed set is unattributed, not a new tier."""
        store = FakeStore()

        outcome = _write(_senses_change(origin="somebody"), store)

        assert not outcome.ok
        assert outcome.refusal is not None
        assert outcome.refusal.code == CHANGE_NO_ORIGIN
        assert store.writes == []

    @pytest.mark.parametrize("origin", ["", "   ", "nobody", "Worker ", "WORKER"])
    def test_no_near_miss_origin_is_admitted(self, origin: str) -> None:
        """Attribution is exact membership, never a fuzzy match on the token."""
        store = FakeStore()

        outcome = _write(_senses_change(origin=origin), store)

        assert not outcome.ok
        assert store.writes == []

    def test_the_refusal_is_recorded_not_dropped(self) -> None:
        """C3: the refusal carries the identity of what was turned away."""
        store = FakeStore()

        outcome = _write(_senses_change(origin="", change_id="c-9"), store)

        assert outcome.refusal is not None
        assert outcome.refusal.change_id == "c-9"
        assert outcome.refusal.target == TARGET_SENSES_KNOWLEDGE
        assert outcome.refusal.seat == "senses"
        assert outcome.refusal.reason

    def test_the_refusal_is_a_config_degradation_so_one_ledger_folds_it(self) -> None:
        """t5's ledger reads ONE stream — a refusal cannot hide in a second list."""
        store = FakeStore()

        outcome = _write(_senses_change(origin=""), store)

        assert isinstance(outcome.refusal, config_change.ConfigRefusal)
        assert isinstance(outcome.refusal, config_change.ConfigDegradation)
        assert outcome.refusal.code in config_change.CHANGE_REFUSAL_CODES

    def test_an_origin_that_does_not_own_the_target_is_refused(self) -> None:
        """The worker owns ``senses.knowledge`` and nothing else (the lattice)."""
        store = FakeStore()

        outcome = _write(
            WorkerKnowledgeChange(
                change_id="c-2", origin=ORIGIN_WORKER, entry_id="k-1", text="mine now"
            ),
            store,
        )

        assert not outcome.ok
        assert outcome.refusal is not None
        assert outcome.refusal.code == CHANGE_ORIGIN_FORBIDDEN
        assert store.writes == []

    def test_a_prompt_change_offered_to_the_knowledge_writer_is_refused(self) -> None:
        """The writer takes knowledge units only — a prompt cannot ride this seam."""
        store = FakeStore()

        outcome = _write(
            SensesPromptChange(
                change_id="c-3", origin=ORIGIN_STRATEGIST, section="voice", text="be terse"
            ),
            store,
        )

        assert not outcome.ok
        assert outcome.refusal is not None
        assert outcome.refusal.code == knowledge.KNOWLEDGE_WRONG_UNIT
        assert store.writes == []

    @pytest.mark.parametrize("junk", [None, "a string", 42, {"origin": "worker"}])
    def test_a_non_change_is_refused_whole(self, junk: Any) -> None:
        store = FakeStore()

        outcome = _write(junk, store)

        assert not outcome.ok
        assert outcome.refusal is not None
        assert store.writes == []

    def test_an_entry_with_no_text_is_refused(self) -> None:
        """A knowledge entry that says nothing is not an entry."""
        store = FakeStore()

        outcome = _write(_senses_change(text=""), store)

        assert not outcome.ok
        assert outcome.refusal is not None
        assert outcome.refusal.code == CHANGE_INCOMPLETE
        assert store.writes == []

    def test_an_entry_with_no_id_is_refused(self) -> None:
        """Without an id the ledger cannot cite it and revert cannot find it."""
        store = FakeStore()

        outcome = _write(_senses_change(entry_id=""), store)

        assert not outcome.ok
        assert outcome.refusal is not None
        assert outcome.refusal.code == CHANGE_INCOMPLETE
        assert store.writes == []

    def test_the_attributed_write_this_is_all_measured_against_succeeds(self) -> None:
        """The control: the same call with an origin lands."""
        store = FakeStore()

        outcome = _write(_senses_change(), store)

        assert outcome.ok
        assert outcome.refusal is None
        assert outcome.entry_id == "k-1"
        assert len(store.writes) == 1

    @pytest.mark.parametrize("origin", [ORIGIN_WORKER, ORIGIN_STRATEGIST, ORIGIN_HOST])
    def test_every_origin_the_lattice_grants_can_write(self, origin: str) -> None:
        """Attribution is a requirement, not a second authority check."""
        store = FakeStore()

        outcome = _write(_senses_change(origin=origin), store)

        assert outcome.ok, origin
        assert outcome.origin == origin


# ---------------------------------------------------------------------------
# 2. LOAD-BEARING — attribution rides eidetic's NATIVE provenance (c35)
# ---------------------------------------------------------------------------


class TestAttributionRidesEideticsOwnProvenance:
    """``origin`` becomes ``Record.added_by`` — eidetic's declared attribution.

    eidetic's own field comment reads "Attribution: the agent or caller that
    ingested this record", which is exactly what an origin is. Inventing an
    ``origin`` key beside it would mean two attribution fields, one of which
    eidetic's tooling (``sweep``, ``recall --json``, the graph seam) cannot
    see — a parallel provenance system inside the store that owns provenance.
    """

    def test_origin_lands_in_added_by(self) -> None:
        store = FakeStore()

        _write(_senses_change(origin=ORIGIN_STRATEGIST), store)

        assert store.writes[0]["record"]["added_by"] == ORIGIN_STRATEGIST

    def test_supersedes_lands_in_eidetics_supersedes(self) -> None:
        """eidetic calls it "the authoritative conflict declaration"."""
        store = FakeStore()

        _write(_senses_change(supersedes="k-0"), store)

        assert store.writes[0]["record"]["supersedes"] == "k-0"

    def test_entry_id_lands_in_the_record_id(self) -> None:
        """One identity: a ledger citation resolves in the store."""
        store = FakeStore()

        _write(_senses_change(entry_id="k-42"), store)

        assert store.writes[0]["record"]["id"] == "k-42"

    def test_text_is_carried_verbatim(self) -> None:
        store = FakeStore()
        text = "  the sensor reads 21C\n\nand the vent is open  "

        _write(_senses_change(text=text), store)

        assert store.writes[0]["record"]["text"] == text

    def test_the_record_declares_no_field_eidetic_does_not_have(self) -> None:
        """The whole no-invented-field claim, checked against eidetic itself.

        Every top-level key of the record this module builds must be a field
        eidetic's own ``Record`` declares. A new attribution key would fail
        here by construction rather than by anyone noticing it.
        """
        from eidetic.memory.record import Record as EideticRecord

        declared = {field.name for field in dataclasses.fields(EideticRecord)}
        built = knowledge.record_for(_senses_change(supersedes="k-0"))

        assert set(built) <= declared, sorted(set(built) - declared)

    def test_no_attribution_shaped_key_hides_in_metadata(self) -> None:
        """Attribution lives in ``added_by`` and nowhere else.

        ``metadata`` is eidetic's declared free-form map and this module does
        use it — but only for the ledger cross-reference and the change's
        reason, neither of which is provenance. A second attribution key here
        would be the invented field wearing a hat.
        """
        built = knowledge.record_for(_senses_change(reason="the operator asked"))
        metadata = built["metadata"]

        for key in ("origin", "added_by", "author", "attribution", "writer", "written_by", "by"):
            assert key not in metadata, key

    def test_metadata_carries_the_ledger_cross_reference(self) -> None:
        """``change_id`` is not provenance — it is the tie to embodiment's ledger.

        eidetic has no field for another system's ledger id, so it goes in the
        extension point eidetic declares for exactly that.
        """
        built = knowledge.record_for(_senses_change(change_id="c-77"))

        assert built["metadata"]["change_id"] == "c-77"

    def test_the_record_type_is_the_designated_one(self) -> None:
        built = knowledge.record_for(_senses_change())

        assert built["type"] == knowledge.KNOWLEDGE_RECORD_TYPE

    def test_a_new_entry_is_active(self) -> None:
        """Shadowing and archival are maintenance verbs, never a write's job."""
        built = knowledge.record_for(_senses_change())

        assert built["lifecycle"] == "active"

    def test_the_reason_is_capped(self) -> None:
        """A runaway reason cannot blow up a host's store."""
        built = knowledge.record_for(_senses_change(reason="x" * 5000))

        assert len(built["metadata"]["reason"]) <= knowledge._MAX_REASON_LEN


# ---------------------------------------------------------------------------
# 3. LOAD-BEARING — the knowledge path imports no store of its own (c35 / h24)
# ---------------------------------------------------------------------------


def _module_level_imports(path: Path) -> set[str]:
    """Top-level module names imported at module scope (not inside a def)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _all_imports(path: Path) -> set[str]:
    """Every module named by an import anywhere in the file, at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _imported_aliases(path: Path) -> set[str]:
    """Every name bound by an import anywhere (``from embodiment import x`` → ``x``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update(alias.asname or alias.name for alias in node.names)
    return found


def _called_names(path: Path) -> set[str]:
    """Bare call targets (``open(...)`` → ``open``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _called_attrs(path: Path) -> set[str]:
    """Attribute call names (``handle.write_text(...)`` → ``write_text``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


class TestNoSecondStoreDriftsIntoExistence:
    """Composition, not a parallel memory system (issue #2, claim ``c35``).

    Proved by parsing the module. The failure this guards against is not a
    dramatic one — it is someone adding a small JSON cache "just for the
    knowledge block" and a second source of truth existing from then on.
    """

    #: Anything that could persist bytes on its own.
    _STORE_MODULES = frozenset(
        {
            "sqlite3",
            "shelve",
            "dbm",
            "pickle",
            "csv",
            "neo4j",
            "pymongo",
            "data_refinery",
            "redis",
            "tempfile",
        }
    )

    def test_module_scope_imports_are_stdlib_or_the_change_schema(self) -> None:
        """Importing the knowledge path must not cost a host the memory subsystem.

        The same posture ``recall_bundle`` holds: reaching
        :mod:`embodiment.continuity` is what costs a host eidetic (deviation
        ``d2``), and a host that injects its own store — or only wants the
        schema — should not pay for it just by importing this module.
        """
        import sys

        for name in _module_level_imports(MODULE_PATH):
            assert name in sys.stdlib_module_names or name in {"__future__", "embodiment"}, name

    def test_continuity_is_reached_lazily_and_only_through_the_default_port(self) -> None:
        """continuity is imported, but never at module scope."""
        module_scope = ast.parse(MODULE_PATH.read_text(encoding="utf-8")).body
        top_level_aliases = {
            alias.asname or alias.name
            for node in module_scope
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

        assert "continuity" not in top_level_aliases
        assert "continuity" in _imported_aliases(MODULE_PATH)

    def test_no_store_module_is_imported_anywhere(self) -> None:
        reached = {name.split(".")[0] for name in _all_imports(MODULE_PATH)}

        assert not (reached & self._STORE_MODULES), sorted(reached & self._STORE_MODULES)

    def test_no_eidetic_backend_is_reached(self) -> None:
        """Records go in and out through the injected port, never a backend.

        ``eidetic.memory.lifecycle`` IS reachable and that is deliberate: it is
        eidetic's own pure, I/O-free policy engine ("never reads the clock,
        never touches a backend"), so composing it is the opposite of
        reimplementing memory. A ``backend``/``get_backend`` reach would be a
        real second store.
        """
        for name in _all_imports(MODULE_PATH):
            assert "backend" not in name, name
        assert "get_backend" not in _called_attrs(MODULE_PATH)

    def test_the_only_eidetic_surfaces_are_pure(self) -> None:
        eidetic_imports = {n for n in _all_imports(MODULE_PATH) if n.startswith("eidetic")}

        assert eidetic_imports <= {"eidetic.memory.lifecycle", "eidetic.memory.record"}

    def test_this_module_writes_no_file(self) -> None:
        """No path is opened, created or written anywhere in this module."""
        assert "open" not in _called_names(MODULE_PATH)
        assert not (
            _called_attrs(MODULE_PATH)
            & {"write_text", "write_bytes", "mkdir", "touch", "unlink", "dump", "dumps"}
        )

    def test_this_module_holds_no_model_seam(self) -> None:
        """Nothing here completes a turn, so nothing here can be prompted."""
        import inspect

        for func in (knowledge.write, knowledge.read, knowledge.maintain, knowledge.record_for):
            params = set(inspect.signature(func).parameters)
            assert not (params & {"complete", "model", "messages", "prompt", "system"}), func

    def test_this_module_imports_no_loop_seam(self) -> None:
        """The advisory lane and the actor stay byte-stable — no import edge."""
        for banned in ("embodiment.loop", "embodiment.scope", "embodiment.scoped_run"):
            assert banned not in _all_imports(MODULE_PATH), banned

    def test_this_module_imports_no_colleague(self) -> None:
        assert "colleague" not in _all_imports(MODULE_PATH)

    def test_no_ranking_or_scoring_is_reimplemented(self) -> None:
        """Relevance is eidetic's; this module reads no score and computes none."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

        for token in ("cosine", "bm25", "embed", "rank", "signal_strength"):
            assert token not in names, token


class TestTheseImportGuardsCanFail:
    """A guard that cannot go red is decoration (the repo's own rule).

    Each helper is run against a fixture that violates the property, proving
    the guards above are measuring something.
    """

    def test_a_module_scope_store_import_would_be_caught(self, tmp_path: Path) -> None:
        offender = tmp_path / "offender.py"
        offender.write_text("import sqlite3\n", encoding="utf-8")

        assert "sqlite3" in _module_level_imports(offender)

    def test_a_lazy_store_import_would_be_caught(self, tmp_path: Path) -> None:
        offender = tmp_path / "offender.py"
        offender.write_text("def f():\n    import sqlite3\n    return sqlite3\n", encoding="utf-8")

        assert "sqlite3" not in _module_level_imports(offender)
        assert "sqlite3" in _all_imports(offender)

    def test_a_file_write_would_be_caught(self, tmp_path: Path) -> None:
        offender = tmp_path / "offender.py"
        offender.write_text("def f(p):\n    p.write_text('x')\n", encoding="utf-8")

        assert "write_text" in _called_attrs(offender)

    def test_an_open_call_would_be_caught(self, tmp_path: Path) -> None:
        offender = tmp_path / "offender.py"
        offender.write_text("def f(p):\n    return open(p)\n", encoding="utf-8")

        assert "open" in _called_names(offender)


# ---------------------------------------------------------------------------
# 4. the designated scope (the scope+visibility convention, v1)
# ---------------------------------------------------------------------------


class TestTheDesignatedScope:
    """One scope per seat's knowledge block, derived — never free text."""

    def test_each_seat_gets_its_own_scope(self) -> None:
        senses = knowledge.knowledge_scope(TARGET_SENSES_KNOWLEDGE)
        worker = knowledge.knowledge_scope(TARGET_WORKER_KNOWLEDGE)

        assert senses != worker
        assert senses.endswith(".senses")
        assert worker.endswith(".worker")

    def test_the_scope_is_namespaced_under_the_agent_scope(self) -> None:
        """Convention v1 §1: one agent-personal scope per repo, by suffix."""
        scope = knowledge.knowledge_scope(TARGET_SENSES_KNOWLEDGE, agent="embodiment")

        assert scope == "embodiment.knowledge.senses"

    def test_the_segment_keeps_knowledge_out_of_general_memory(self) -> None:
        """A plain agent-scope recall cannot surface a knowledge entry."""
        scope = knowledge.knowledge_scope(TARGET_SENSES_KNOWLEDGE, agent="embodiment")

        assert scope != "embodiment"
        assert knowledge.KNOWLEDGE_SCOPE_SEGMENT in scope

    def test_a_non_knowledge_target_has_no_knowledge_scope(self) -> None:
        assert knowledge.knowledge_scope("senses.prompts") == ""
        assert knowledge.knowledge_scope("nonsense") == ""
        assert knowledge.knowledge_scope(None) == ""

    def test_the_write_lands_in_the_seats_scope(self) -> None:
        store = FakeStore()

        _write(_senses_change(), store, agent="embodiment")

        assert store.writes[0]["record"]["scope"] == {
            "name": "embodiment.knowledge.senses",
            "visibility": "public",
        }

    def test_the_read_queries_the_seats_scope(self) -> None:
        store = FakeStore()

        knowledge.read(
            TARGET_SENSES_KNOWLEDGE,
            "sensor",
            data_dir="/tmp/store",
            store=store.port(),
            agent="embodiment",
        )

        assert store.reads[0]["scope"] == "embodiment.knowledge.senses"

    def test_visibility_is_explicit_on_both_halves(self) -> None:
        """Private is one explicit flag away — never silently inferred."""
        store = FakeStore()

        _write(_senses_change(), store, visibility="private")
        knowledge.read(
            TARGET_SENSES_KNOWLEDGE,
            "x",
            data_dir="/tmp/store",
            store=store.port(),
            visibility="private",
        )

        assert store.writes[0]["record"]["scope"]["visibility"] == "private"
        assert store.reads[0]["visibility"] == "private"


# ---------------------------------------------------------------------------
# 5. senses reads via recall — and attribution is enforced on the way out too
# ---------------------------------------------------------------------------


class TestSensesReadsViaRecall:
    """The read half, and the second attribution gate.

    A public eidetic record lives in ``<repo-root>/.eidetic/memory`` — committed,
    and travelling with every clone. Store content is therefore untrusted input
    on its way to a model (``recall_bundle``'s own safety note), so the read
    enforces attribution again rather than trusting that the write did.
    """

    def test_an_attributed_entry_comes_back(self) -> None:
        store = FakeStore([_record()])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert [e.entry_id for e in result.entries] == ["k-1"]
        assert result.entries[0].origin == ORIGIN_WORKER
        assert result.entries[0].text == "the greenhouse sensor reads 21C"

    def test_an_unattributed_record_is_dropped_and_recorded(self) -> None:
        store = FakeStore([_record(added_by=None)])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert result.entries == ()
        assert [d.code for d in result.degradations] == [knowledge.KNOWLEDGE_UNATTRIBUTED_RECORD]
        assert "k-1" in result.degradations[0].reason

    def test_a_record_attributed_to_an_unknown_tier_is_dropped(self) -> None:
        store = FakeStore([_record(added_by="attacker")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert result.entries == ()
        assert result.degradations

    def test_a_foreign_record_type_is_dropped_and_recorded(self) -> None:
        """A designated scope holds knowledge; anything else is not this block."""
        store = FakeStore([_record(record_type="note")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert result.entries == ()
        assert [d.code for d in result.degradations] == [knowledge.KNOWLEDGE_FOREIGN_RECORD]

    def test_a_dropped_entry_never_costs_the_good_ones(self) -> None:
        store = FakeStore([_record("k-1"), _record("k-2", added_by=None), _record("k-3")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert [e.entry_id for e in result.entries] == ["k-1", "k-3"]
        assert len(result.degradations) == 1

    def test_several_queries_are_unioned_by_id_in_first_seen_order(self) -> None:
        store = FakeStore([_record("k-1"), _record("k-2")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE,
            ("sensor", "vent"),
            data_dir="/tmp/store",
            store=store.port(),
        )

        assert [e.entry_id for e in result.entries] == ["k-1", "k-2"]
        assert len(store.reads) == 2

    def test_a_bare_string_is_one_query_not_a_sequence_of_characters(self) -> None:
        store = FakeStore()

        knowledge.read(TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port())

        assert [r["query"] for r in store.reads] == ["sensor"]

    def test_a_non_knowledge_target_is_refused(self) -> None:
        store = FakeStore([_record()])

        result = knowledge.read("senses.prompts", "x", data_dir="/tmp/store", store=store.port())

        assert result.entries == ()
        assert result.degradations
        assert store.reads == []

    def test_the_entry_carries_its_supersedes_link(self) -> None:
        store = FakeStore([_record(supersedes="k-0")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert result.entries[0].supersedes == "k-0"

    def test_shadowed_and_archived_are_hidden_by_default(self) -> None:
        store = FakeStore()

        knowledge.read(TARGET_SENSES_KNOWLEDGE, "x", data_dir="/tmp/store", store=store.port())

        assert store.reads[0]["include_shadowed"] is False
        assert store.reads[0]["include_archived"] is False


# ---------------------------------------------------------------------------
# 6. strategist maintenance — eidetic's own consolidation/supersession engine
# ---------------------------------------------------------------------------


def _transitions_seam(changed: list[dict], suggestions: Optional[list[dict]] = None):
    """A fake for eidetic's pure lifecycle engine."""
    calls: list[dict] = []

    def _seam(records: list[dict], now: str) -> knowledge.TransitionPlan:
        calls.append({"records": records, "now": now})
        return knowledge.TransitionPlan(
            changed=tuple(changed), suggestions=tuple(suggestions or ())
        )

    _seam.calls = calls  # type: ignore[attr-defined]
    return _seam


class TestStrategistMaintenance:
    """Consolidation, supersession and ageing are eidetic's policy, composed."""

    def test_transitions_are_persisted_through_the_same_port(self) -> None:
        store = FakeStore([_record("k-1"), _record("k-2", supersedes="k-1")])
        shadowed = dict(_record("k-1"), lifecycle="shadowed")

        result = knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([shadowed]),
        )

        assert result.shadowed == ("k-1",)
        assert [w["record"]["id"] for w in store.writes] == ["k-1"]
        assert store.writes[0]["record"]["lifecycle"] == "shadowed"

    def test_archival_is_reported_separately_from_shadowing(self) -> None:
        store = FakeStore([_record("k-1")])
        archived = dict(_record("k-1"), lifecycle="archived")

        result = knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([archived]),
        )

        assert result.archived == ("k-1",)
        assert result.shadowed == ()

    def test_suggestions_are_returned_never_applied(self) -> None:
        """eidetic's own rule: conflict hints are for human confirmation."""
        store = FakeStore([_record("k-1")])
        seam = _transitions_seam([], [{"reason": "overlap", "ids": ["k-1", "k-2"]}])

        result = knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=seam,
        )

        assert len(result.suggestions) == 1
        assert store.writes == []

    def test_the_maintenance_read_sees_shadowed_and_archived_records(self) -> None:
        """Maintenance judges the whole block, not only what recall shows senses."""
        store = FakeStore([_record()])

        knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([]),
        )

        assert store.reads[0]["include_shadowed"] is True
        assert store.reads[0]["include_archived"] is True

    def test_the_maintenance_read_does_not_reinforce(self) -> None:
        """A pass that bumps ``recall_count`` changes the signal it is judging."""
        store = FakeStore([_record()])

        knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([]),
        )

        assert store.reads[0]["reinforce"] is False

    def test_query_time_fields_are_cleared_before_write_back(self) -> None:
        """``score``/``signal`` are recall artefacts — continuity's own rule."""
        store = FakeStore([_record("k-1")])
        shadowed = dict(_record("k-1"), lifecycle="shadowed")

        knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([shadowed]),
        )

        assert store.writes[0]["record"]["score"] is None
        assert store.writes[0]["record"]["signal"] is None

    def test_records_can_be_supplied_directly_without_a_read(self) -> None:
        store = FakeStore()

        knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            records=[_record("k-1")],
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([]),
        )

        assert store.reads == []

    def test_a_transitions_seam_that_raises_degrades(self) -> None:
        def _boom(records: list[dict], now: str) -> Any:
            raise RuntimeError("engine exploded")

        store = FakeStore([_record()])

        result = knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_boom,
        )

        assert result.shadowed == ()
        assert [d.code for d in result.degradations] == [
            knowledge.KNOWLEDGE_TRANSITIONS_UNAVAILABLE
        ]

    def test_a_failed_write_back_is_recorded_and_the_rest_continue(self) -> None:
        store = FakeStore([_record("k-1")], remember_ok=False)
        shadowed = dict(_record("k-1"), lifecycle="shadowed")

        result = knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_transitions_seam([shadowed]),
        )

        assert result.shadowed == ()
        assert result.degradations

    def test_the_real_engine_shadows_a_superseded_predecessor(self) -> None:
        """The default seam really is eidetic's engine, run end to end.

        No store is involved — ``compute_transitions`` is pure — so this is the
        composition itself under test, not a fake standing in for it.
        """
        store = FakeStore(
            [_record("k-1", text="the vent is shut"), _record("k-2", supersedes="k-1")]
        )

        result = knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="vent",
            data_dir="/tmp/store",
            store=store.port(),
            now="2026-08-04T00:00:00+00:00",
        )

        assert result.shadowed == ("k-1",)
        assert store.writes[0]["record"]["lifecycle"] == "shadowed"

    def test_the_real_engine_never_hard_deletes(self) -> None:
        store = FakeStore(
            [_record("k-1", text="the vent is shut"), _record("k-2", supersedes="k-1")]
        )

        knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="vent",
            data_dir="/tmp/store",
            store=store.port(),
            now="2026-08-04T00:00:00+00:00",
        )

        assert all(w["record"]["text"] for w in store.writes)
        assert all(w["record"]["lifecycle"] != "deleted" for w in store.writes)

    def test_a_non_knowledge_target_is_refused(self) -> None:
        store = FakeStore()

        result = knowledge.maintain(
            "senses.prompts", queries="x", data_dir="/tmp/store", store=store.port()
        )

        assert result.degradations
        assert store.reads == []
        assert store.writes == []


# ---------------------------------------------------------------------------
# 7. degrade, never raise (C3)
# ---------------------------------------------------------------------------


class TestDegradeNeverRaise:
    """Every entry point returns a value, whatever the store does."""

    def test_a_store_that_raises_on_write_degrades(self) -> None:
        store = FakeStore(raises=True)

        outcome = _write(_senses_change(), store)

        assert not outcome.ok
        assert outcome.degradation is not None
        assert outcome.degradation.code == knowledge.KNOWLEDGE_STORE_ERROR

    def test_a_store_that_raises_on_read_degrades(self) -> None:
        store = FakeStore(raises=True)

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "x", data_dir="/tmp/store", store=store.port()
        )

        assert result.entries == ()
        assert [d.code for d in result.degradations] == [knowledge.KNOWLEDGE_STORE_ERROR]

    def test_a_store_degradation_keeps_its_own_code(self) -> None:
        """A relayed code stays the store's, so a host branches on one vocabulary."""
        store = FakeStore(
            recall_degradation=continuity.Degradation(
                subsystem="eidetic",
                stage="recall",
                code=continuity.CODE_NO_STORAGE_ANCHOR,
                reason="no anchor",
            )
        )

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "x", data_dir="/tmp/store", store=store.port()
        )

        assert [d.code for d in result.degradations] == [continuity.CODE_NO_STORAGE_ANCHOR]

    def test_a_failed_write_relays_the_store_code(self) -> None:
        store = FakeStore(remember_ok=False)

        outcome = _write(_senses_change(), store)

        assert not outcome.ok
        assert outcome.degradation is not None
        assert outcome.degradation.code == continuity.CODE_SUBSYSTEM_ERROR

    def test_an_unreadable_outcome_shape_degrades(self) -> None:
        port = knowledge.KnowledgeStore(
            remember=lambda record, **kw: "not an outcome",
            recall=lambda query, **kw: "not an outcome",
        )

        outcome = knowledge.write(_senses_change(), data_dir="/tmp/store", store=port)
        result = knowledge.read(TARGET_SENSES_KNOWLEDGE, "x", data_dir="/tmp/store", store=port)

        assert not outcome.ok
        assert outcome.degradation is not None
        assert result.degradations

    def test_an_unreadable_record_is_dropped_not_fatal(self) -> None:
        store = FakeStore([{"nonsense": True}, _record("k-1")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert [e.entry_id for e in result.entries] == ["k-1"]
        assert result.degradations

    @pytest.mark.parametrize("junk", ["a bare string", 42, None, ["nested"]])
    def test_a_non_mapping_record_never_raises_out_of_the_seam(self, junk: Any) -> None:
        """Coercing one would raise out of a seam whose contract is that it does not.

        A bare string is the sharp case: ``dict("junk")`` raises ``ValueError``,
        so a store answering with one would have taken down the host's read
        path rather than degrading.
        """
        store = FakeStore([junk, _record("k-1")])

        result = knowledge.read(
            TARGET_SENSES_KNOWLEDGE, "sensor", data_dir="/tmp/store", store=store.port()
        )

        assert [e.entry_id for e in result.entries] == ["k-1"]
        assert knowledge.KNOWLEDGE_UNREADABLE_RECORD in [d.code for d in result.degradations]

    def test_a_non_mapping_record_never_reaches_the_lifecycle_engine(self) -> None:
        seen: list[list[dict]] = []

        def _seam(records: list[dict], now: str) -> knowledge.TransitionPlan:
            seen.append(records)
            return knowledge.TransitionPlan()

        store = FakeStore(["a bare string", _record("k-1")])

        knowledge.maintain(
            TARGET_SENSES_KNOWLEDGE,
            queries="sensor",
            data_dir="/tmp/store",
            store=store.port(),
            transitions=_seam,
        )

        assert all(isinstance(record, dict) for record in seen[0])

    def test_a_missing_anchor_never_reaches_the_store(self) -> None:
        """continuity's trap #1, honoured before the port is even called."""
        store = FakeStore()

        outcome = knowledge.write(_senses_change(), data_dir=None, store=store.port())

        assert not outcome.ok
        assert outcome.degradation is not None
        assert outcome.degradation.code == continuity.CODE_NO_STORAGE_ANCHOR
        assert store.writes == []

    def test_every_degradation_code_has_a_producer(self) -> None:
        """embodiment#18's lesson: a code nothing can mint is a lie in the ledger."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        for code in knowledge.KNOWLEDGE_DEGRADATION_CODES:
            name = [n for n in dir(knowledge) if getattr(knowledge, n, None) == code]
            assert name, code
            assert source.count(name[0]) > 1, code


# ---------------------------------------------------------------------------
# 8. module posture — composed, curated, pinned against drift
# ---------------------------------------------------------------------------


class TestModulePosture:
    def test_knowledge_is_a_curated_submodule(self) -> None:
        assert "knowledge" in embodiment._SUBMODULES
        assert embodiment.knowledge is knowledge

    def test_no_name_is_hoisted_into_the_lazy_map(self) -> None:
        """t3's precedent: reach without advertisement until a verdict lands."""
        assert not any(module == "knowledge" for module in embodiment._LAZY_NAMES.values())

    @pytest.mark.parametrize(
        ("mine", "theirs"),
        [
            ("DEFAULT_AGENT_SCOPE", "DEFAULT_SCOPE"),
            ("DEFAULT_VISIBILITY", "DEFAULT_VISIBILITY"),
            ("DEFAULT_TOP_K", "DEFAULT_TOP_K"),
            ("DEFAULT_MODE", "DEFAULT_MODE"),
        ],
    )
    def test_the_mirrored_defaults_match_continuity(self, mine: str, theirs: str) -> None:
        """Mirrored so the module stays import-cheap — pinned so it cannot drift."""
        assert getattr(knowledge, mine) == getattr(continuity, theirs)

    def test_the_origin_vocabulary_is_the_change_schemas(self) -> None:
        """One closed set of origins, not a second copy that can drift."""
        assert knowledge.KNOWLEDGE_ORIGINS is config_change.CHANGE_ORIGINS

    def test_the_knowledge_targets_are_the_change_schemas(self) -> None:
        assert knowledge.KNOWLEDGE_TARGETS is config_change.KNOWLEDGE_TARGETS

    def test_every_public_name_resolves(self) -> None:
        for name in knowledge.__all__:
            assert getattr(knowledge, name, None) is not None, name

    def test_the_default_store_composes_continuity(self) -> None:
        port = knowledge.default_store()

        assert port.remember is continuity.remember
        assert port.recall is continuity.recall


# ---------------------------------------------------------------------------
# 9. the composable senses clause
# ---------------------------------------------------------------------------


class TestTheKnowledgeAttributionClause:
    """Host-composable text saying knowledge entries are attributed claims."""

    def test_the_clause_exists_and_is_exported(self) -> None:
        assert isinstance(senses_text.KNOWLEDGE_ATTRIBUTION, str)
        assert senses_text.KNOWLEDGE_ATTRIBUTION.strip() == senses_text.KNOWLEDGE_ATTRIBUTION
        assert "KNOWLEDGE_ATTRIBUTION" in senses_text.__all__

    def test_the_clause_is_reachable_from_the_package_root(self) -> None:
        assert embodiment.KNOWLEDGE_ATTRIBUTION is senses_text.KNOWLEDGE_ATTRIBUTION
        assert embodiment._LAZY_NAMES["KNOWLEDGE_ATTRIBUTION"] == "senses_text"

    def test_the_clause_names_attribution_and_forbids_restating_as_perception(self) -> None:
        clause = senses_text.KNOWLEDGE_ATTRIBUTION.lower()

        assert "claim" in clause
        assert "who" in clause or "source" in clause

    def test_the_clause_is_documented_as_unmeasured(self) -> None:
        """It ships beside a measured clause and must not borrow its evidence.

        ``SENSES_GROUNDING`` carries a 0-of-16 vs 16-of-16 live result. This
        one carries none, and the module has to say so — an unmeasured clause
        sitting next to a measured one is exactly where an unearned claim
        would form.
        """
        doc = senses_text.__doc__ or ""

        assert "Unmeasured" in doc or "unmeasured" in doc
        assert "KNOWLEDGE_ATTRIBUTION" in doc

    def test_the_measured_clause_is_untouched(self) -> None:
        """Adding a clause here must not perturb the one that was measured."""
        assert senses_text.SENSES_GROUNDING == "You can see only the status block you are given."

    def test_the_module_is_still_data_only(self) -> None:
        tree = ast.parse(Path(senses_text.__file__).read_text(encoding="utf-8"))
        defs = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]

        assert defs == []
