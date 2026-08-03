"""Directive persistence lanes — durable and session-scoped (task t13).

Spec claim ``c33`` and honesty condition ``h23``: directive persistence is
layered. A **durable** directive survives across drives (and across a process
restart); a **session-scoped** one is temporary persistence *within one
process*, and a session holds its own scope state without ever touching the
durable lane. Sessions are the future seam for per-subagent scoping — this file
pins the lane distinction, not subagent scoping, which is nobody's task yet.

The three acceptance criteria, one section each
-----------------------------------------------
1. §6 — a session-scoped directive never outlives its session and never writes
   the durable lane, with a real durable port wired in the same process so the
   "never writes" half is measured rather than assumed.
2. §5 — a durable directive survives a process restart through the host-visible
   persistence seam. The restart is simulated the honest way: *only the JSON
   payload crosses*. A fresh store, a fresh governor and a fresh register are
   built from bytes that went through ``json.dumps``/``json.loads``, so nothing
   survives by holding a Python reference.
3. §3 / §7 — every scope record, ledger entry and event names its lane.

What this file deliberately does NOT pin
----------------------------------------
**Which component owns durable storage.** The frame parks that as open
vagueness ``v5`` (plan risk ``r5``): host state round-tripped through the scope
projector, or a continuity record. So what ships here is the *lane distinction*
and an injected :class:`~embodiment.scoped_run.ScopePersistence` port — two
host-supplied callables. §8 pins that no storage backend is imported and none is
reachable: the scope modules' transitive import closure stays stdlib-plus-
``embodiment`` exactly as ``tests/test_scope_authority.py`` requires.

A host that wires no port gets **today's behaviour**: every drive starts from
the explicit host default scope, and nothing is written anywhere (§4).
"""

from __future__ import annotations

import ast
import copy
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

import pytest

import embodiment.scope_events as scope_events
from embodiment import ledger
from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.loop import EXIT_FINISHED, ToolOutcome
from embodiment.scope import (
    DEGRADED_REVIEW,
    DROPPED_DUPLICATE,
    DROPPED_INCOMPLETE,
    DROPPED_VERSION_BACKWARD,
    LANE_DURABLE,
    LANE_SCHEMA_VERSION,
    LANE_SESSION,
    SCOPE_EXIT_DIRECTIVE,
    SCOPE_EXIT_UNCHANGED,
    SCOPE_LANES,
    ScopeControls,
    ScopeDegradation,
    ScopeDirective,
    ScopeLoop,
    ScopeOutcome,
    ScopeRegister,
    ScopeRejection,
    ScopeSnapshot,
)
from embodiment.scoped_run import (
    TRANSITION_APPLIED,
    TRANSITION_DEFAULT,
    TRANSITION_DEGRADED,
    TRANSITION_WITHHELD,
    ScopedOutcome,
    ScopeGovernor,
    ScopePersistence,
    ScopeSession,
    ScopeTransition,
    run_scoped,
)

_PACKAGE = Path(__file__).resolve().parents[1] / "embodiment"

#: The three modules the scope frame added. Structural claims run over all of
#: them, exactly as ``tests/test_scope_authority.py`` does.
SCOPE_MODULES = ("scope.py", "strategist_runner.py", "scoped_run.py")

#: A durable payload whose single entry governs nothing — what a corrupted or
#: hand-edited store looks like from this side of the seam.
_TAMPERED: dict[str, Any] = {
    "schema_version": LANE_SCHEMA_VERSION,
    "lane": LANE_DURABLE,
    "accepted": [{"scope_id": "", "objective": "", "version": 1}],
}


# ── doubles ───────────────────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    base: dict[str, Any] = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    base.update(kw)
    return Task(**base)


def _call(name: str = "read_file", **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


class Scripted:
    """The actor's ``complete`` seam: replay turns, record every message list."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append(copy.deepcopy(messages))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]

    @property
    def text(self) -> str:
        """Everything the actor was ever shown, as one searchable string."""
        return json.dumps(self.calls, default=str)


class FakeExecutor:
    """The minimum an executor must be: one ``execute`` method."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append(name)
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="done")
        return ToolOutcome(result="ok")


class FakeStrategist:
    """The runner's surface, with no thread at all. One batch per drain call."""

    def __init__(self, *ready: Any) -> None:
        self.ready = [list(batch) for batch in ready]
        self.considered: list[Any] = []
        self.degradations: list[Any] = []
        self.role = "strategist"
        self.model = "a-strategist-model"

    def start(self) -> bool:
        return True

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        self.considered.append(snapshot)

    def drain(self, *, step_count: int = 0) -> list[Any]:
        if not self.ready:
            return []
        return self.ready.pop(0)

    def degradation(self) -> Any:
        return None


class Store:
    """A host's durable store: the ONLY thing that crosses a simulated restart.

    Deliberately not a file and not a database — the frame parks the storage
    owner (``v5``), so the test double is exactly the seam's two callables over
    a payload the host holds. Every write is deep-copied on the way in and out,
    so a test cannot pass by sharing a mutable object with the lane.
    """

    def __init__(self, payload: Optional[dict[str, Any]] = None) -> None:
        self.payload = copy.deepcopy(payload) if payload is not None else None
        self.writes: list[dict[str, Any]] = []
        self.loads = 0

    def load(self) -> Any:
        self.loads += 1
        return copy.deepcopy(self.payload)

    def save(self, payload: dict[str, Any]) -> None:
        self.writes.append(copy.deepcopy(payload))
        self.payload = copy.deepcopy(payload)

    @property
    def port(self) -> ScopePersistence:
        return ScopePersistence(load=self.load, save=self.save)


def _directive(**kw: Any) -> ScopeDirective:
    base: dict[str, Any] = {
        "scope_id": "scope-001",
        "objective": "Finish the extraction without losing presence",
        "priorities": ("Preserve responsiveness",),
        "version": 1,
    }
    base.update(kw)
    return ScopeDirective(**base)


def _outcome(directive: Optional[ScopeDirective] = None, **kw: Any) -> ScopeOutcome:
    base: dict[str, Any] = {
        "snapshot_id": "snapshot-001",
        "exit_reason": SCOPE_EXIT_DIRECTIVE if directive is not None else SCOPE_EXIT_UNCHANGED,
        "directive": directive,
        "model": "a-strategist-model",
        "role": "strategist",
    }
    base.update(kw)
    return ScopeOutcome(**base)


def _snapshot(**kw: Any) -> ScopeSnapshot:
    base: dict[str, Any] = {"snapshot_id": "snapshot-001", "objectives": ("ship it",)}
    base.update(kw)
    return ScopeSnapshot(**base)


def _script() -> tuple[ModelResponse, ...]:
    return (_turn(_call("read_file", path="a.py")), _turn(_call("finish")))


def _drive(
    governor: ScopeGovernor,
    *,
    actor: Optional[Scripted] = None,
    observer: Any = None,
) -> tuple[ScopedOutcome, Scripted]:
    seam = actor if actor is not None else Scripted(*_script())
    kwargs: dict[str, Any] = {}
    if observer is not None:
        kwargs["observer"] = observer
    scoped = run_scoped(
        seam,
        _task(),
        executor=FakeExecutor(),
        max_steps=8,
        governor=governor,
        **kwargs,
    )
    return scoped, seam


def _applied(scoped: ScopedOutcome) -> list[str]:
    return [entry.scope_id for entry in scoped.applications]


def _kinds(scoped: ScopedOutcome) -> list[str]:
    return [entry.kind for entry in scoped.transitions]


def _tree(name: str) -> ast.Module:
    return ast.parse((_PACKAGE / name).read_text(encoding="utf-8"))


def _imported_modules(name: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(name)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _import_closure(start: str) -> set[str]:
    seen: set[str] = set()
    frontier = [f"embodiment.{start[:-3]}"]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        leaf = name.split(".", 1)[1] + ".py"
        if not (_PACKAGE / leaf).exists():
            continue
        for imported in _imported_modules(leaf):
            if imported.startswith("embodiment"):
                frontier.append(imported)
    return seen


def _closure_imports(start: str) -> set[str]:
    reached: set[str] = set()
    for name in _import_closure(start):
        leaf = name.split(".", 1)[1] + ".py"
        if (_PACKAGE / leaf).exists():
            reached |= _imported_modules(leaf)
    return reached


# ══ 1. the lane vocabulary ════════════════════════════════════════════════════


class TestTheLaneVocabulary:
    """Two lanes, named, closed and distinct."""

    def test_the_two_lanes_are_different_values(self):
        assert LANE_DURABLE != LANE_SESSION

    def test_the_lane_set_is_exactly_the_two(self):
        assert set(SCOPE_LANES) == {LANE_DURABLE, LANE_SESSION}

    def test_a_register_defaults_to_the_session_lane(self):
        """Today's behaviour, named: an in-process chain is session-scoped."""
        assert ScopeRegister().lane == LANE_SESSION

    def test_a_register_takes_the_lane_it_is_given(self):
        assert ScopeRegister(lane=LANE_DURABLE).lane == LANE_DURABLE

    def test_an_unknown_lane_name_is_carried_rather_than_rewritten(self):
        """``ScopeReport.status``' precedent: a host extends, it is not overruled."""
        assert ScopeRegister(lane="rehearsal").lane == "rehearsal"

    def test_an_unreadable_lane_falls_back_to_the_session_default(self):
        assert ScopeRegister(lane=None).lane == LANE_SESSION  # type: ignore[arg-type]

    def test_the_schema_version_is_a_whole_number(self):
        assert isinstance(LANE_SCHEMA_VERSION, int)


# ══ 2. the register serializes and restores ═══════════════════════════════════


class TestTheRegisterRoundTrips:
    """The payload IS the seam. It must survive ``json.dumps`` and come back."""

    def _chain(self) -> ScopeRegister:
        register = ScopeRegister(lane=LANE_DURABLE)
        register.offer(_directive(scope_id="scope-001", version=1))
        register.offer(_directive(scope_id="scope-002", version=2, supersedes="scope-001"))
        return register

    def test_an_empty_register_serializes(self):
        payload = ScopeRegister().to_dict()
        assert payload["accepted"] == []

    def test_the_payload_names_its_lane(self):
        assert ScopeRegister(lane=LANE_DURABLE).to_dict()["lane"] == LANE_DURABLE

    def test_the_payload_carries_its_schema_version(self):
        assert ScopeRegister().to_dict()["schema_version"] == LANE_SCHEMA_VERSION

    def test_the_payload_is_json_serializable(self):
        payload = self._chain().to_dict()
        assert json.loads(json.dumps(payload)) == payload

    def test_the_restored_register_holds_the_same_active_directive(self):
        restored = ScopeRegister.from_dict(json.loads(json.dumps(self._chain().to_dict())))
        assert restored.active is not None
        assert restored.active.scope_id == "scope-002"

    def test_the_restored_register_keeps_the_whole_chain(self):
        restored = ScopeRegister.from_dict(self._chain().to_dict())
        assert restored.known == ("scope-001", "scope-002")

    def test_the_restored_register_keeps_the_version(self):
        restored = ScopeRegister.from_dict(self._chain().to_dict())
        assert restored.version == 2

    def test_the_restored_register_keeps_its_lane(self):
        restored = ScopeRegister.from_dict(self._chain().to_dict())
        assert restored.lane == LANE_DURABLE

    def test_an_explicit_lane_outranks_the_payloads(self):
        """The caller knows which store it read; a payload cannot relabel itself."""
        restored = ScopeRegister.from_dict(self._chain().to_dict(), lane=LANE_SESSION)
        assert restored.lane == LANE_SESSION

    def test_the_round_trip_preserves_every_directive_field(self):
        original = self._chain().active
        restored = ScopeRegister.from_dict(json.loads(json.dumps(self._chain().to_dict())))
        assert restored.active == original

    def test_a_restored_register_still_admits_the_next_directive(self):
        restored = ScopeRegister.from_dict(self._chain().to_dict())
        refusal = restored.offer(
            _directive(scope_id="scope-003", version=3, supersedes="scope-002")
        )
        assert refusal is None

    def test_a_restored_register_still_refuses_a_backward_version(self):
        restored = ScopeRegister.from_dict(self._chain().to_dict())
        refusal = restored.offer(_directive(scope_id="scope-009", version=1))
        assert refusal is not None
        assert refusal.code == DROPPED_VERSION_BACKWARD


class TestARestoreIsAReplayNotAProposal:
    """A persisted chain is a record of decisions already admitted.

    Provenance (``supersedes``) is deliberately *not* re-checked on restore, for
    the reason ``scoped_run``'s docstring already gives about the applied chain:
    a lane legitimately withholds a directive, so the chain it persists can name
    a predecessor it never applied. Re-validating provenance against the shorter
    chain would strand the actor under old scope forever — the exact failure the
    check exists to prevent.
    """

    def _gapped(self) -> dict[str, Any]:
        return {
            "schema_version": LANE_SCHEMA_VERSION,
            "lane": LANE_DURABLE,
            "accepted": [
                _directive(scope_id="scope-001", version=1).to_dict(),
                _directive(scope_id="scope-003", version=3, supersedes="scope-002").to_dict(),
            ],
        }

    def test_a_chain_with_a_withheld_predecessor_restores_whole(self):
        restored = ScopeRegister.from_dict(self._gapped())
        assert restored.known == ("scope-001", "scope-003")

    def test_its_active_directive_is_the_head_of_the_persisted_chain(self):
        restored = ScopeRegister.from_dict(self._gapped())
        assert restored.active is not None
        assert restored.active.scope_id == "scope-003"

    def test_no_refusal_is_recorded_for_the_gap(self):
        restored = ScopeRegister.from_dict(self._gapped())
        assert restored.rejections == ()


class TestATamperedPayloadIsValidatedNotTrusted:
    """The host owns storage, so what comes back is checked — and refusals recorded."""

    def _payload(self, *entries: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": LANE_SCHEMA_VERSION,
            "lane": LANE_DURABLE,
            "accepted": list(entries),
        }

    def test_an_entry_that_governs_nothing_is_refused(self):
        payload = self._payload({"scope_id": "", "objective": "", "version": 1})
        restored = ScopeRegister.from_dict(payload)
        assert [entry.code for entry in restored.rejections] == [DROPPED_INCOMPLETE]

    def test_a_refused_entry_never_becomes_the_active_scope(self):
        payload = self._payload({"scope_id": "", "objective": "", "version": 1})
        assert ScopeRegister.from_dict(payload).active is None

    def test_a_duplicated_entry_is_refused(self):
        entry = _directive(scope_id="scope-001", version=1).to_dict()
        restored = ScopeRegister.from_dict(self._payload(entry, entry))
        assert [entry.code for entry in restored.rejections] == [DROPPED_DUPLICATE]

    def test_a_chain_whose_versions_move_backward_is_refused_at_the_offender(self):
        payload = self._payload(
            _directive(scope_id="scope-001", version=5).to_dict(),
            _directive(scope_id="scope-002", version=2).to_dict(),
        )
        restored = ScopeRegister.from_dict(payload)
        assert [entry.code for entry in restored.rejections] == [DROPPED_VERSION_BACKWARD]

    def test_the_older_entry_never_displaces_the_newer_one(self):
        payload = self._payload(
            _directive(scope_id="scope-001", version=5).to_dict(),
            _directive(scope_id="scope-002", version=2).to_dict(),
        )
        restored = ScopeRegister.from_dict(payload)
        assert restored.version == 5

    @pytest.mark.parametrize("payload", [None, "", 3, [], {"accepted": "nonsense"}, object()])
    def test_an_unreadable_payload_restores_an_empty_register_and_never_raises(self, payload: Any):
        assert ScopeRegister.from_dict(payload).active is None

    def test_an_unknown_key_is_ignored_rather_than_refused(self):
        """Forward compatibility, which scope.py already promises for every shape."""
        payload = self._payload(_directive().to_dict())
        payload["written_by_a_later_release"] = {"anything": True}
        assert ScopeRegister.from_dict(payload).active is not None

    def test_a_hostile_accepted_entry_does_not_take_the_lane_down(self):
        payload = self._payload({"scope_id": "ok", "objective": "o", "version": "not-a-number"})
        restored = ScopeRegister.from_dict(payload)
        assert restored.active is not None


# ══ 3. every record names its lane ════════════════════════════════════════════


class TestRecordsNameTheirLane:
    """Criterion 3, at the record level: nothing is anonymous about which lane."""

    def test_a_degradation_carries_a_lane_field(self):
        assert "lane" in {entry.name for entry in fields(ScopeDegradation)}

    def test_a_rejection_carries_the_lane_of_the_register_that_refused_it(self):
        register = ScopeRegister(lane=LANE_DURABLE)
        refusal = register.offer(ScopeDirective(scope_id="", objective=""))
        assert refusal is not None
        assert refusal.lane == LANE_DURABLE

    def test_a_session_lane_register_stamps_the_session_lane(self):
        register = ScopeRegister(lane=LANE_SESSION)
        refusal = register.offer(ScopeDirective(scope_id="", objective=""))
        assert refusal is not None
        assert refusal.lane == LANE_SESSION

    def test_the_rejection_serializes_its_lane(self):
        entry = ScopeRejection(code=DROPPED_INCOMPLETE, reason="x", lane=LANE_DURABLE)
        assert entry.to_dict()["lane"] == LANE_DURABLE

    def test_the_degradation_serializes_its_lane(self):
        entry = ScopeDegradation(code=DEGRADED_REVIEW, reason="x", lane=LANE_SESSION)
        assert entry.to_dict()["lane"] == LANE_SESSION

    def test_a_reviews_degradation_names_the_lane_of_the_chain_it_was_admitting_into(self):
        def dead(messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("the port is down")

        loop = ScopeLoop(
            dead,
            controls=ScopeControls(max_turns=1),
            register=ScopeRegister(lane=LANE_DURABLE),
        )
        outcome = loop.review(_snapshot())
        assert [entry.lane for entry in outcome.degradations] == [LANE_DURABLE]

    def test_a_ledger_fold_keeps_the_lane_on_the_folded_record(self):
        """The ledger reads shapes, not fields — so the lane rides ``original``."""
        entry = ScopeRejection(code=DROPPED_INCOMPLETE, reason="x", lane=LANE_DURABLE)
        record = ledger.from_scope(entry)[0]
        assert record.original.lane == LANE_DURABLE

    def test_a_transition_carries_a_lane_field(self):
        assert "lane" in {entry.name for entry in fields(ScopeTransition)}

    def test_the_transition_serializes_its_lane(self):
        assert ScopeTransition(kind=TRANSITION_APPLIED, lane=LANE_DURABLE).to_dict()["lane"] == (
            LANE_DURABLE
        )


# ══ 4. the persistence port ═══════════════════════════════════════════════════


class TestThePortIsInjectedAndOptional:
    """The seam is two host callables. Embodiment supplies neither."""

    def test_the_port_is_a_frozen_dataclass(self):
        assert is_dataclass(ScopePersistence)

    def test_a_bare_port_holds_no_callable(self):
        assert ScopePersistence().load is None

    def test_a_bare_port_holds_no_writer(self):
        assert ScopePersistence().save is None

    def test_the_port_cannot_be_rewired_after_construction(self):
        port = ScopePersistence()
        with pytest.raises(Exception):
            port.load = print  # type: ignore[misc]

    def test_a_port_with_no_callables_is_not_wired(self):
        assert ScopePersistence().wired is False

    def test_a_port_with_both_callables_is_wired(self):
        assert Store().port.wired is True

    def test_the_governor_holds_it_as_an_injected_field_defaulting_to_absent(self):
        assert ScopeGovernor().persistence is None

    def test_a_governor_with_no_lane_state_is_still_inert(self):
        assert ScopeGovernor().armed is False

    def test_a_wired_port_arms_the_lane(self):
        assert ScopeGovernor(persistence=Store().port).armed is True

    def test_a_session_arms_the_lane(self):
        assert ScopeGovernor(session=ScopeSession("s1")).armed is True


class TestWithNoPortTodaysBehaviourIsUnchanged:
    """The floor: no port, no writes, and the host default is where a drive starts."""

    def test_a_governed_drive_with_no_port_names_the_session_lane(self):
        scoped, _ = _drive(ScopeGovernor(default_scope=_directive()))
        assert {entry.lane for entry in scoped.transitions} == {LANE_SESSION}

    def test_the_host_default_still_governs_the_drive(self):
        scoped, _ = _drive(ScopeGovernor(default_scope=_directive()))
        assert _applied(scoped) == ["scope-001"]

    def test_the_default_still_arrives_as_a_default_transition(self):
        scoped, _ = _drive(ScopeGovernor(default_scope=_directive()))
        assert [entry.kind for entry in scoped.applications] == [TRANSITION_DEFAULT]

    def test_an_ungoverned_drive_records_nothing_at_all(self):
        scoped, _ = _drive(ScopeGovernor())
        assert scoped.transitions == ()

    def test_the_outcome_names_the_lane_the_drive_ran_in(self):
        scoped, _ = _drive(ScopeGovernor(default_scope=_directive()))
        assert scoped.lane == LANE_SESSION


class TestAFailingPortDegradesAndNeverAborts:
    """Constraint C3: a persistence port is host code, and host code may fail."""

    class Exploding:
        def __init__(self) -> None:
            self.saves = 0

        def load(self) -> Any:
            raise OSError("the store is unreachable")

        def save(self, payload: dict[str, Any]) -> None:
            self.saves += 1
            raise OSError("the disk is full")

    def _governor(self, port: ScopePersistence, **kw: Any) -> ScopeGovernor:
        return ScopeGovernor(persistence=port, **kw)

    def test_a_raising_load_still_completes_the_drive(self):
        store = self.Exploding()
        scoped, _ = _drive(self._governor(ScopePersistence(load=store.load, save=store.save)))
        assert scoped.exit_reason == EXIT_FINISHED

    def test_a_raising_load_is_recorded_as_a_degradation(self):
        store = self.Exploding()
        scoped, _ = _drive(self._governor(ScopePersistence(load=store.load, save=store.save)))
        assert TRANSITION_DEGRADED in _kinds(scoped)

    def test_the_record_names_the_lane_that_failed(self):
        store = self.Exploding()
        scoped, _ = _drive(self._governor(ScopePersistence(load=store.load, save=store.save)))
        degraded = next(e for e in scoped.transitions if e.kind == TRANSITION_DEGRADED)
        assert degraded.lane == LANE_DURABLE

    def test_a_store_that_could_not_be_read_is_never_overwritten(self):
        """Clobbering a store we could not read would destroy the durable lane."""
        store = self.Exploding()
        _drive(
            self._governor(
                ScopePersistence(load=store.load, save=store.save),
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert store.saves == 0

    def test_a_raising_save_still_completes_the_drive(self):
        store = self.Exploding()
        scoped, _ = _drive(
            self._governor(
                ScopePersistence(save=store.save),
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert scoped.exit_reason == EXIT_FINISHED

    def test_a_raising_save_is_recorded(self):
        store = self.Exploding()
        scoped, _ = _drive(
            self._governor(
                ScopePersistence(save=store.save),
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert TRANSITION_DEGRADED in _kinds(scoped)

    def test_the_directive_still_governed_the_actor_despite_the_write_failing(self):
        """Durability failed; delivery did not. The record must not conflate them."""
        store = self.Exploding()
        scoped, actor = _drive(
            self._governor(
                ScopePersistence(save=store.save),
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert _applied(scoped) == ["scope-001"]

    def test_a_half_wired_port_reads_nothing_and_writes_nothing(self):
        """A port with neither callable is not a lane; it degrades to session."""
        scoped, _ = _drive(
            ScopeGovernor(persistence=ScopePersistence(), default_scope=_directive())
        )
        assert scoped.lane == LANE_SESSION

    def test_a_read_only_port_still_governs_the_drive(self):
        store = Store()
        scoped, _ = _drive(
            ScopeGovernor(persistence=ScopePersistence(load=store.load), default_scope=_directive())
        )
        assert _applied(scoped) == ["scope-001"]

    def test_a_read_only_port_records_no_degradation(self):
        """A host that wired no writer asked for no durability. Not a failure."""
        store = Store()
        scoped, _ = _drive(
            ScopeGovernor(persistence=ScopePersistence(load=store.load), default_scope=_directive())
        )
        assert TRANSITION_DEGRADED not in _kinds(scoped)

    def test_a_port_whose_own_attributes_explode_is_not_a_lane(self):
        class Hostile:
            @property
            def load(self) -> Any:
                raise RuntimeError("not even readable")

            @property
            def save(self) -> Any:
                raise RuntimeError("not even readable")

        scoped, _ = _drive(ScopeGovernor(persistence=Hostile(), default_scope=_directive()))
        assert scoped.lane == LANE_SESSION

    def test_a_tampered_persisted_entry_is_recorded_on_restore(self):
        scoped, _ = _drive(ScopeGovernor(persistence=Store(_TAMPERED).port))
        withheld = [entry for entry in scoped.transitions if entry.kind == TRANSITION_WITHHELD]
        assert "refused on restore" in withheld[0].reason

    def test_a_tampered_persisted_entry_never_governs_the_drive(self):
        scoped, _ = _drive(ScopeGovernor(persistence=Store(_TAMPERED).port))
        assert scoped.active is None

    def test_a_directive_the_lane_cannot_record_still_governs_and_is_recorded(self):
        """A hostile strategist's incomplete directive: delivered, not persisted.

        ``_consume`` gates on the applied id and version; completeness is the
        lane chain's own check. The two disagreeing is a real (if narrow) state,
        and what it must never be is silent — the actor ends up governed by scope
        the durable lane will not carry forward, and the record says exactly that.
        """
        store, broken = Store(), ScopeDirective(scope_id="scope-broken", objective="", version=3)
        scoped, _ = _drive(self._breaking(store, broken))
        degraded = next(e for e in scoped.transitions if e.kind == TRANSITION_DEGRADED)
        assert "refused to record" in degraded.reason

    def test_that_directive_is_absent_from_the_persisted_chain(self):
        store, broken = Store(), ScopeDirective(scope_id="scope-broken", objective="", version=3)
        _drive(self._breaking(store, broken))
        assert store.writes == []

    @staticmethod
    def _breaking(store: Store, directive: ScopeDirective) -> ScopeGovernor:
        return ScopeGovernor(
            persistence=store.port,
            strategist=FakeStrategist([_outcome(directive)]),
            projector=lambda context: _snapshot(),
        )


# ══ 5. durable: across a process restart ══════════════════════════════════════


class TestADurableDirectiveSurvivesARestart:
    """Acceptance 2. The restart is simulated by letting ONLY the JSON cross."""

    def _first_drive(self, store: Store) -> ScopedOutcome:
        governor = ScopeGovernor(
            strategist=FakeStrategist([_outcome(_directive(scope_id="scope-durable", version=4))]),
            projector=lambda context: _snapshot(),
            persistence=store.port,
        )
        scoped, _ = _drive(governor)
        return scoped

    def _restarted(self, store: Store) -> Store:
        """A brand-new process: a new store built from bytes, nothing shared."""
        assert store.payload is not None
        return Store(json.loads(json.dumps(store.payload)))

    def test_the_first_drive_applies_the_directive(self):
        store = Store()
        assert _applied(self._first_drive(store)) == ["scope-durable"]

    def test_the_first_drive_writes_the_durable_lane(self):
        store = Store()
        self._first_drive(store)
        assert len(store.writes) == 1

    def test_the_written_payload_names_the_durable_lane(self):
        store = Store()
        self._first_drive(store)
        assert store.writes[0]["lane"] == LANE_DURABLE

    def test_the_written_payload_carries_the_applied_directive(self):
        store = Store()
        self._first_drive(store)
        assert [entry["scope_id"] for entry in store.writes[0]["accepted"]] == ["scope-durable"]

    def test_a_fresh_register_built_from_the_payload_holds_the_directive(self):
        """The seam alone, with no governor anywhere near it."""
        store = Store()
        self._first_drive(store)
        restored = ScopeRegister.from_dict(json.loads(json.dumps(store.payload)))
        assert restored.active is not None
        assert restored.active.scope_id == "scope-durable"

    def test_the_next_process_starts_under_the_restored_directive(self):
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        scoped, _ = _drive(ScopeGovernor(persistence=restarted.port))
        assert scoped.active is not None
        assert scoped.active.scope_id == "scope-durable"

    def test_the_restored_directive_reaches_the_actors_context(self):
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        _, actor = _drive(ScopeGovernor(persistence=restarted.port))
        assert "scope-durable" in actor.text

    def test_the_restored_directive_is_recorded_as_applied_in_the_durable_lane(self):
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        scoped, _ = _drive(ScopeGovernor(persistence=restarted.port))
        applications = scoped.applications
        assert [entry.kind for entry in applications] == [TRANSITION_APPLIED]

    def test_every_transition_of_the_restarted_drive_names_the_durable_lane(self):
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        scoped, _ = _drive(ScopeGovernor(persistence=restarted.port))
        assert {entry.lane for entry in scoped.transitions} == {LANE_DURABLE}

    def test_the_restored_version_still_refuses_an_older_directive(self):
        """Supersession survives the restart, which is the point of persisting it."""
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        scoped, _ = _drive(
            ScopeGovernor(
                persistence=restarted.port,
                strategist=FakeStrategist([_outcome(_directive(scope_id="old", version=2))]),
                projector=lambda context: _snapshot(),
            )
        )
        assert TRANSITION_WITHHELD in _kinds(scoped)

    def test_the_restarted_drive_rewrites_nothing_when_it_applies_nothing(self):
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        _drive(ScopeGovernor(persistence=restarted.port))
        assert restarted.writes == []

    def test_a_third_drive_persists_the_next_directive_onto_the_chain(self):
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        _drive(
            ScopeGovernor(
                persistence=restarted.port,
                strategist=FakeStrategist([_outcome(_directive(scope_id="scope-next", version=9))]),
                projector=lambda context: _snapshot(),
            )
        )
        ids = [entry["scope_id"] for entry in restarted.writes[-1]["accepted"]]
        assert ids == ["scope-durable", "scope-next"]

    def test_the_host_default_yields_to_a_restored_directive(self):
        """A default is what governs UNTIL a directive arrives; one already has."""
        store = Store()
        self._first_drive(store)
        restarted = self._restarted(store)
        scoped, _ = _drive(
            ScopeGovernor(persistence=restarted.port, default_scope=_directive(scope_id="fallback"))
        )
        assert _applied(scoped) == ["scope-durable"]

    def test_an_empty_durable_store_still_seats_the_host_default(self):
        store = Store()
        scoped, _ = _drive(
            ScopeGovernor(persistence=store.port, default_scope=_directive(scope_id="fallback"))
        )
        assert _applied(scoped) == ["fallback"]

    def test_the_default_seated_into_an_empty_durable_lane_is_persisted(self):
        store = Store()
        _drive(ScopeGovernor(persistence=store.port, default_scope=_directive(scope_id="fallback")))
        assert [entry["scope_id"] for entry in store.writes[-1]["accepted"]] == ["fallback"]


# ══ 6. session-scoped: within one process, never durable ══════════════════════


class TestASessionHoldsItsOwnScopeState:
    """Acceptance 1. A session persists inside the process and nowhere else."""

    def test_a_session_names_the_session_lane(self):
        assert ScopeSession("s1").lane == LANE_SESSION

    def test_a_session_keeps_the_id_it_was_given(self):
        assert ScopeSession("s1").session_id == "s1"

    def test_a_new_session_holds_no_scope(self):
        assert ScopeSession("s1").active is None

    def test_a_session_can_be_seeded_with_a_default(self):
        session = ScopeSession("s1", default=_directive())
        assert session.active is not None

    def test_a_directive_applied_under_a_session_is_held_by_it(self):
        session = ScopeSession("s1")
        _drive(
            ScopeGovernor(
                session=session,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert session.active is not None
        assert session.active.scope_id == "scope-001"

    def test_a_second_drive_in_the_same_process_starts_under_it(self):
        session = ScopeSession("s1")
        _drive(
            ScopeGovernor(
                session=session,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        scoped, _ = _drive(ScopeGovernor(session=session))
        assert scoped.active is not None
        assert scoped.active.scope_id == "scope-001"

    def test_the_second_drive_actually_shows_it_to_the_actor(self):
        session = ScopeSession("s1", default=_directive())
        _, actor = _drive(ScopeGovernor(session=session))
        assert "scope-001" in actor.text

    def test_every_session_transition_names_the_session_lane(self):
        session = ScopeSession("s1", default=_directive())
        scoped, _ = _drive(ScopeGovernor(session=session))
        assert {entry.lane for entry in scoped.transitions} == {LANE_SESSION}

    def test_two_sessions_hold_independent_scope(self):
        """The property per-subagent scoping will one day be built on."""
        first, second = ScopeSession("s1", default=_directive()), ScopeSession("s2")
        _drive(ScopeGovernor(session=first))
        assert second.active is None


class TestASessionNeverWritesTheDurableLane:
    """Acceptance 1's second half, measured against a REAL durable port."""

    def _both(self, session: ScopeSession, store: Store, **kw: Any) -> ScopeGovernor:
        return ScopeGovernor(session=session, persistence=store.port, **kw)

    def test_a_session_drive_writes_nothing_to_the_store(self):
        session, store = ScopeSession("s1"), Store()
        _drive(
            self._both(
                session,
                store,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert store.writes == []

    def test_a_session_drive_never_even_reads_the_store(self):
        session, store = ScopeSession("s1"), Store()
        _drive(self._both(session, store, default_scope=_directive()))
        assert store.loads == 0

    def test_the_session_directive_is_absent_from_the_durable_payload(self):
        session, store = ScopeSession("s1"), Store(ScopeRegister(lane=LANE_DURABLE).to_dict())
        _drive(
            self._both(
                session,
                store,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert store.payload is not None
        assert store.payload["accepted"] == []

    def test_a_session_drive_still_names_the_session_lane_on_its_records(self):
        session, store = ScopeSession("s1"), Store()
        scoped, _ = _drive(self._both(session, store, default_scope=_directive()))
        assert scoped.lane == LANE_SESSION


class TestASessionDirectiveNeverOutlivesItsSession:
    """Acceptance 1's first half: ``close()`` is the end of that scope."""

    def _used(self) -> ScopeSession:
        session = ScopeSession("s1")
        _drive(
            ScopeGovernor(
                session=session,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        return session

    def test_closing_a_session_discards_its_active_scope(self):
        session = self._used()
        session.close()
        assert session.active is None

    def test_closing_a_session_discards_its_chain(self):
        session = self._used()
        session.close()
        assert session.to_dict()["accepted"] == []

    def test_a_closed_session_reports_itself_closed(self):
        session = self._used()
        session.close()
        assert session.open is False

    def test_closing_twice_is_harmless(self):
        session = self._used()
        session.close()
        session.close()
        assert session.active is None

    def test_a_drive_under_a_closed_session_starts_under_no_scope(self):
        session = self._used()
        session.close()
        scoped, _ = _drive(ScopeGovernor(session=session))
        assert scoped.active is None

    def test_the_closed_session_is_recorded_rather_than_silently_empty(self):
        session = self._used()
        session.close()
        scoped, _ = _drive(ScopeGovernor(session=session))
        assert TRANSITION_WITHHELD in _kinds(scoped)

    def test_the_record_names_the_session_that_closed(self):
        session = self._used()
        session.close()
        scoped, _ = _drive(ScopeGovernor(session=session))
        withheld = next(e for e in scoped.transitions if e.kind == TRANSITION_WITHHELD)
        assert "s1" in withheld.reason

    def test_a_drive_under_a_closed_session_falls_back_to_the_host_default(self):
        session = self._used()
        session.close()
        scoped, _ = _drive(
            ScopeGovernor(session=session, default_scope=_directive(scope_id="fallback"))
        )
        assert _applied(scoped) == ["fallback"]

    def test_a_hostile_session_object_degrades_rather_than_aborting_the_drive(self):
        """A session is host state; reading it is host code, and host code faults."""

        class Hostile:
            @property
            def register(self) -> Any:
                raise RuntimeError("this session is not readable")

            @property
            def session_id(self) -> Any:
                raise RuntimeError("nor is its name")

        scoped, _ = _drive(
            ScopeGovernor(session=Hostile(), default_scope=_directive(scope_id="fallback"))
        )
        assert _applied(scoped) == ["fallback"]

    def test_the_unreadable_session_is_named_in_the_record_as_unreadable(self):
        class Hostile:
            @property
            def register(self) -> Any:
                raise RuntimeError("this session is not readable")

        scoped, _ = _drive(ScopeGovernor(session=Hostile()))
        withheld = next(e for e in scoped.transitions if e.kind == TRANSITION_WITHHELD)
        assert "unreadable" in withheld.reason

    @pytest.mark.parametrize("given,expected", [(7, "7"), (None, ""), ("s1", "s1")])
    def test_a_session_id_is_rendered_rather_than_demanded_as_a_string(self, given, expected):
        assert ScopeSession(given).session_id == expected

    def test_an_unrenderable_session_id_reads_as_empty_rather_than_raising(self):
        class Unstringable:
            def __str__(self) -> str:
                raise RuntimeError("no")

        assert ScopeSession(Unstringable()).session_id == ""  # type: ignore[arg-type]

    def test_a_closed_session_accepts_no_new_directive(self):
        session = self._used()
        session.close()
        _drive(
            ScopeGovernor(
                session=session,
                strategist=FakeStrategist([_outcome(_directive(scope_id="after-close"))]),
                projector=lambda context: _snapshot(),
            )
        )
        assert session.active is None


# ══ 7. events name the lane ═══════════════════════════════════════════════════


class Observer:
    """A host's ``ObserverFn`` — the SAME one the actor's own events ride.

    ``scope`` filters to :data:`~embodiment.scope_events.SCOPE_EVENT_KINDS`,
    because the actor loop's own ``LoopEvent`` stream reaches this object too and
    those events have no scope lane to name — which is itself the point: the
    scope lane rides the host's existing observer rather than a second fabric.
    """

    def __init__(self) -> None:
        self.events: list[Any] = []

    def __call__(self, event: Any) -> None:
        self.events.append(event)

    @property
    def scope(self) -> list[Any]:
        return [entry for entry in self.events if entry.kind in scope_events.SCOPE_EVENT_KINDS]

    @property
    def lanes(self) -> set[str]:
        return {entry.data.get("lane") for entry in self.scope}

    def of(self, kind: str) -> list[Any]:
        return [entry for entry in self.events if entry.kind == kind]


class TestEveryEventNamesItsLane:
    """Criterion 3 at the event level: the envelope carries a ``lane`` key."""

    def test_the_envelope_declares_a_lane(self):
        assert "lane" in scope_events._ENVELOPE_DEFAULTS

    def test_a_transition_event_carries_the_transitions_lane(self):
        transition = ScopeTransition(kind=TRANSITION_APPLIED, scope_id="s", lane=LANE_DURABLE)
        event = scope_events.for_transition(transition)
        assert event is not None
        assert event.data["lane"] == LANE_DURABLE

    def test_a_snapshot_event_carries_the_lane_it_is_told(self):
        event = scope_events.snapshot_event(_snapshot(), lane=LANE_SESSION)
        assert event.data["lane"] == LANE_SESSION

    def test_a_review_started_event_carries_the_lane_it_is_told(self):
        event = scope_events.review_started_event(_snapshot(), lane=LANE_DURABLE)
        assert event.data["lane"] == LANE_DURABLE

    def test_a_review_completed_event_carries_the_lane_it_is_told(self):
        event = scope_events.review_completed_event(_outcome(_directive()), lane=LANE_DURABLE)
        assert event.data["lane"] == LANE_DURABLE

    def test_a_proposed_event_carries_the_lane_it_is_told(self):
        event = scope_events.directive_proposed_event(_outcome(_directive()), lane=LANE_SESSION)
        assert event is not None
        assert event.data["lane"] == LANE_SESSION

    def test_a_report_event_carries_the_lane_it_is_told(self):
        from embodiment.scope import ScopeReport

        event = scope_events.report_event(ScopeReport(), lane=LANE_SESSION)
        assert event.data["lane"] == LANE_SESSION

    def test_a_lane_degradation_prefers_the_lane_the_entry_names(self):
        entry = ScopeRejection(code=DROPPED_INCOMPLETE, reason="x", lane=LANE_DURABLE)
        event = scope_events.for_lane_degradation(entry, lane=LANE_SESSION)
        assert event.data["lane"] == LANE_DURABLE

    def test_a_lane_degradation_falls_back_to_the_relaying_drives_lane(self):
        """The runner re-mints a rejection and drops its lane; the drive still knows."""
        entry = ScopeDegradation(code=DEGRADED_REVIEW, reason="x")
        event = scope_events.for_lane_degradation(entry, lane=LANE_DURABLE)
        assert event.data["lane"] == LANE_DURABLE

    def test_an_unreadable_lane_renders_as_empty_rather_than_raising(self):
        event = scope_events.snapshot_event(_snapshot(), lane=None)  # type: ignore[arg-type]
        assert event.data["lane"] == ""


class TestALiveDriveEmitsLaneBearingEvents:
    """End to end: what a host's own observer actually receives."""

    def test_every_event_of_a_session_drive_names_the_session_lane(self):
        observer = Observer()
        session = ScopeSession("s1", default=_directive())
        _drive(
            ScopeGovernor(
                session=session,
                strategist=FakeStrategist([_outcome(_directive(scope_id="s2", version=7))]),
                projector=lambda context: _snapshot(),
            ),
            observer=observer,
        )
        assert observer.lanes == {LANE_SESSION}

    def test_every_event_of_a_durable_drive_names_the_durable_lane(self):
        observer = Observer()
        store = Store()
        _drive(
            ScopeGovernor(
                persistence=store.port,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            ),
            observer=observer,
        )
        assert observer.lanes == {LANE_DURABLE}

    def test_the_session_drive_really_emitted_scope_events(self):
        """Non-vacuity: an empty stream would satisfy the two tests above."""
        observer = Observer()
        _drive(ScopeGovernor(session=ScopeSession("s1", default=_directive())), observer=observer)
        assert observer.scope

    def test_the_applied_event_still_reaches_the_observer(self):
        observer = Observer()
        store = Store()
        _drive(
            ScopeGovernor(
                persistence=store.port,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            ),
            observer=observer,
        )
        assert observer.of(scope_events.SCOPE_EVENT_DIRECTIVE_APPLIED)


# ══ 8. the storage owner is NOT embodiment's to choose (v5 / r5) ══════════════


class TestNoStorageBackendShips:
    """The frame parks the durable-lane owner; this file must not settle it."""

    @pytest.mark.parametrize("name", SCOPE_MODULES)
    def test_the_module_imports_no_storage_backend(self, name: str):
        forbidden = {"sqlite3", "pickle", "shelve", "socket", "urllib", "http", "subprocess"}
        roots = {module.split(".")[0] for module in _imported_modules(name)}
        assert not (roots & forbidden), f"{name}: {sorted(roots & forbidden)}"

    @pytest.mark.parametrize("name", SCOPE_MODULES)
    def test_the_module_reaches_no_memory_subsystem(self, name: str):
        assert "embodiment.continuity" not in _import_closure(name)

    @pytest.mark.parametrize("name", SCOPE_MODULES)
    def test_the_closure_carries_no_storage_dependency(self, name: str):
        forbidden = {"neo4j", "pymongo", "numpy", "httpx", "data_refinery", "requests"}
        roots = {module.split(".")[0] for module in _closure_imports(name)}
        assert not (roots & forbidden), sorted(roots & forbidden)

    def test_the_composition_layer_opens_no_path_and_no_file(self):
        called = {
            node.func.id
            for node in ast.walk(_tree("scoped_run.py"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "open" not in called

    def test_the_port_is_the_only_way_the_durable_lane_is_written(self):
        """No store, no write: the lane cannot invent a place to put a directive."""
        session = ScopeSession("s1")
        scoped, _ = _drive(
            ScopeGovernor(
                session=session,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert scoped.counts["durable_writes"] == 0

    def test_a_durable_drive_counts_its_writes(self):
        store = Store()
        scoped, _ = _drive(
            ScopeGovernor(
                persistence=store.port,
                strategist=FakeStrategist([_outcome(_directive())]),
                projector=lambda context: _snapshot(),
            )
        )
        assert scoped.counts["durable_writes"] == 1
