"""Tests for :mod:`embodiment.events` — the optional events-cli ``ObserverFn`` (#4).

Organised around what the issue names as requirements:

1. **Absent by default, byte-identical.** Importing/using ``embodiment.events``
   must not change what ``embodiment.loop.run`` does when no observer — or a
   plain, non-events observer — is wired in.
2. **Lazy, direct import.** ``embodiment/events.py`` imports nothing third-party
   at module scope (structurally proven by AST, alongside the pre-existing
   dynamic ``tests/test_zero_deps.py`` guard this file does not touch).
3. **Never raise, degrade once.** Every failure mode named in the issue —
   events-cli/paho unavailable, a broker absent at start, a connect failure, a
   mid-run publish failure, a raising client — degrades to exactly ONE recorded
   transition and disables the emitter for the rest of its life; nothing is
   ever raised into a caller.
4. **A stable, meaningful id.** Two emissions of "the same" occurrence produce
   the identical envelope id; different occurrences (almost) never collide.
5. **Real envelope + topic contract.** Every published envelope is built
   through the REAL ``events_cli.core.Envelope``/``type_to_topic`` — nothing
   here hand-rolls the wire shape or a topic string.

Every test is hermetic: no test in this file ever imports
``events_cli.client`` or ``paho``, opens a socket, or requires Docker/a running
broker. A fake stands in for the transport client throughout; only the
pure-stdlib ``events_cli.core`` envelope/topic module is used for real, because
it does no I/O of its own.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.events import (
    DEFAULT_SOURCE,
    DEGRADED_CONNECT,
    DEGRADED_EMIT,
    DEGRADED_PUBLISH,
    DEGRADED_UNAVAILABLE,
    EVENT_TYPE_PREFIX,
    EventDegradation,
    EventEmitter,
)
from embodiment.loop import LoopEvent, ToolOutcome, run

_EVENTS_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "events.py"

# ── real, I/O-free events-cli surfaces (never the transport) ──────────────────
events_core = pytest.importorskip(
    "events_cli.core", reason="events-cli must be installed to validate the real envelope contract"
)
from events_cli.core import Envelope  # noqa: E402

# ── fakes / doubles ─────────────────────────────────────────────────────────


class _FakeResult:
    """A minimal stand-in for ``events_cli.client.PublishResult``."""

    def __init__(self, ok: bool, reason: str = "ok", connected: bool = True) -> None:
        self.ok = ok
        self.reason = reason
        self.connected = connected
        self.mid = 1


class FakeClient:
    """A fake transport: never touches a socket, records every call, scriptable.

    ``fail_from`` (1-indexed) makes every ``publish_event`` from that call
    onward return ``ok=False`` — used to simulate a broker that goes away
    mid-run. ``raises`` makes ``publish_event`` raise instead of returning.
    """

    def __init__(
        self,
        *,
        ok: bool = True,
        fail_from: Optional[int] = None,
        raises: Optional[BaseException] = None,
        reason: str = "no_conn",
    ) -> None:
        self._ok = ok
        self._fail_from = fail_from
        self._raises = raises
        self._reason = reason
        self.published: list[tuple[Any, str]] = []
        self.closed = False

    def publish_event(self, envelope: Any, topic: str, **_kw: Any) -> _FakeResult:
        self.published.append((envelope, topic))
        if self._raises is not None:
            raise self._raises
        n = len(self.published)
        ok = self._ok and (self._fail_from is None or n < self._fail_from)
        return _FakeResult(ok=ok, reason=self._reason)

    def close(self) -> None:
        self.closed = True


class RaisingCloseClient(FakeClient):
    def close(self) -> None:
        raise RuntimeError("close exploded")


def _event(kind: str = "step", detail: str = "read_file", **data: Any) -> LoopEvent:
    return LoopEvent(kind=kind, detail=detail, data=data)


def _stable(result_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop the one field that legitimately varies run to run: wall-clock duration."""
    result_dict = dict(result_dict)
    stats = dict(result_dict.get("stats") or {})
    stats.pop("duration_seconds", None)
    stats.pop("started_at", None)
    result_dict["stats"] = stats
    return result_dict


class TestEventDegradation:
    def test_to_dict(self) -> None:
        record = EventDegradation(code=DEGRADED_PUBLISH, reason="publish failed: no_conn")
        assert record.to_dict() == {"code": DEGRADED_PUBLISH, "reason": "publish failed: no_conn"}


# ── 1. absent by default / byte-identical ──────────────────────────────────


class TestAbsentByDefault:
    def _task(self) -> Task:
        return Task(id="t1", repo_path="/repo", instruction="do the thing")

    class _Executor:
        def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
            return ToolOutcome(result="done", finished=True, finish_summary="all done")

    def _complete(self, _messages: list[dict[str, Any]]) -> ModelResponse:
        return ModelResponse(
            content="", tool_calls=[ToolCall(id="c1", name="finish", arguments={})]
        )

    def test_run_without_observer_is_unaffected_by_this_module_existing(self) -> None:
        # embodiment.events is imported (at the top of this file already); a run
        # with no observer= must produce byte-identical output regardless.
        outcome_a = run(self._complete, self._task(), executor=self._Executor(), max_steps=5)
        outcome_b = run(self._complete, self._task(), executor=self._Executor(), max_steps=5)
        assert _stable(outcome_a.result.to_dict()) == _stable(outcome_b.result.to_dict())
        assert outcome_a.exit_reason == outcome_b.exit_reason
        assert outcome_a.degradations == outcome_b.degradations == []

    def test_constructing_an_emitter_touches_no_real_transport(self) -> None:
        # Merely building an EventEmitter (never called) must not import or
        # touch events_cli.client/paho at all.
        emitter = EventEmitter(
            client_factory=lambda: (_ for _ in ()).throw(AssertionError("dialed"))
        )
        assert emitter.active is True
        assert emitter.degradations == []


# ── 2. lazy, direct import (structural) ─────────────────────────────────────


class TestLazyImportDiscipline:
    def test_module_never_imports_events_cli_at_top_level(self) -> None:
        tree = ast.parse(_EVENTS_SRC.read_text(encoding="utf-8"))

        def _module_level_names(node: ast.stmt) -> list[str]:
            if isinstance(node, ast.Import):
                return [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom):
                return [node.module or ""]
            return []

        top_level_imports: list[str] = []
        for node in tree.body:  # only true module-level statements, not nested defs
            top_level_imports.extend(_module_level_names(node))
            if isinstance(node, ast.If):  # the TYPE_CHECKING guard is inert at runtime
                continue
        assert not any(name.startswith("events_cli") for name in top_level_imports)

    def test_events_cli_only_named_inside_function_bodies(self) -> None:
        tree = ast.parse(_EVENTS_SRC.read_text(encoding="utf-8"))
        offending = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                if name and name.startswith("events_cli"):
                    offending.append(node)
        # Every events_cli import must be reachable only via a FunctionDef ancestor.
        function_bodies = {
            id(n)
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(fn)
        }
        for node in offending:
            assert id(node) in function_bodies, "events_cli imported outside a function body"

    def test_load_event_client_class_resolves_the_real_class(self) -> None:
        # Calls the lazy loader directly (no instance constructed, no socket
        # opened) to prove the import path itself is correct.
        from events_cli.client import EventClient

        import embodiment.events as events_mod

        assert events_mod._load_event_client_class() is EventClient


# ── 3. never raise, degrade once ────────────────────────────────────────────


class TestDegradation:
    def test_events_cli_unavailable_degrades(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import embodiment.events as events_mod

        def _boom() -> Any:
            raise ImportError("no module named events_cli")

        monkeypatch.setattr(events_mod, "_load_envelope_core", _boom)
        emitter = EventEmitter()
        emitter(_event())
        assert emitter.active is False
        assert [d.code for d in emitter.degradations] == [DEGRADED_UNAVAILABLE]

    def test_no_factory_builds_the_default_client_through_the_lazy_loader(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No client, no client_factory: _ensure_client must fall back to the
        # lazily-loaded client class. Substitute it with a fake CLASS (not an
        # instance) so this stays hermetic — no socket is ever opened — while
        # still proving the default-construction branch itself runs and wires
        # host/port through.
        import embodiment.events as events_mod

        built: dict[str, Any] = {}

        class _FakeEventClientClass:
            def __init__(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
                built["host"] = host
                built["port"] = port

            def publish_event(self, envelope: Any, topic: str, **_kw: Any) -> _FakeResult:
                return _FakeResult(ok=True)

            def close(self) -> None:
                pass

        monkeypatch.setattr(events_mod, "_load_event_client_class", lambda: _FakeEventClientClass)
        emitter = EventEmitter(host="10.0.0.5", port=1884)
        emitter(_event())
        assert emitter.active is True
        assert built == {"host": "10.0.0.5", "port": 1884}

    def test_broker_absent_at_start_degrades_once(self) -> None:
        client = FakeClient(ok=False, reason="no_conn")
        emitter = EventEmitter(client_factory=lambda: client)
        for _ in range(5):
            emitter(_event())
        assert emitter.active is False
        assert [d.code for d in emitter.degradations] == [DEGRADED_PUBLISH]
        # Only the FIRST publish attempt was ever made — the rest short-circuited.
        assert len(client.published) == 1

    def test_connect_failure_degrades_once_and_never_redials(self) -> None:
        calls = {"n": 0}

        def factory() -> Any:
            calls["n"] += 1
            raise ConnectionRefusedError("broker refused")

        emitter = EventEmitter(client_factory=factory)
        for _ in range(5):
            emitter(_event())
        assert emitter.active is False
        assert [d.code for d in emitter.degradations] == [DEGRADED_CONNECT]
        assert calls["n"] == 1  # never re-dialled

    def test_mid_run_publish_failure_degrades_once(self) -> None:
        client = FakeClient(ok=True, fail_from=3)  # 3rd publish onward fails
        emitter = EventEmitter(client_factory=lambda: client)
        for i in range(6):
            emitter(_event(kind="step", step_index=i))
        assert emitter.active is False
        assert [d.code for d in emitter.degradations] == [DEGRADED_PUBLISH]
        # Two ok publishes, then the failing third — nothing after it.
        assert len(client.published) == 3

    def test_a_raising_client_degrades_and_never_raises_into_the_caller(self) -> None:
        client = FakeClient(raises=RuntimeError("paho blew up"))
        emitter = EventEmitter(client_factory=lambda: client)
        emitter(_event())  # must not raise
        assert emitter.active is False
        assert [d.code for d in emitter.degradations] == [DEGRADED_EMIT]

    def test_degrade_once_property_n_steps_one_transition(self) -> None:
        client = FakeClient(ok=False)
        emitter = EventEmitter(client_factory=lambda: client)
        for i in range(50):
            emitter(_event(kind="step", step_index=i))
        assert len(emitter.degradations) == 1

    def test_on_degrade_hook_invoked_exactly_once(self) -> None:
        seen: list[EventDegradation] = []
        client = FakeClient(ok=False)
        emitter = EventEmitter(client_factory=lambda: client, on_degrade=seen.append)
        for _ in range(10):
            emitter(_event())
        assert len(seen) == 1
        assert seen[0].code == DEGRADED_PUBLISH

    def test_a_raising_on_degrade_hook_is_swallowed(self) -> None:
        client = FakeClient(ok=False)

        def _bad_hook(_record: EventDegradation) -> None:
            raise RuntimeError("notification hook exploded")

        emitter = EventEmitter(client_factory=lambda: client, on_degrade=_bad_hook)
        emitter(_event())  # must not raise despite the hook raising
        assert emitter.active is False
        assert len(emitter.degradations) == 1

    def test_close_never_raises_when_the_client_close_raises(self) -> None:
        client = RaisingCloseClient()
        emitter = EventEmitter(client_factory=lambda: client)
        emitter(_event())
        emitter.close()  # must not raise
        assert client.closed is False  # close() itself raised before setting it

    def test_close_is_a_noop_without_a_client(self) -> None:
        emitter = EventEmitter()
        emitter.close()  # never built a client; must not raise

    def test_context_manager_closes_on_exit(self) -> None:
        client = FakeClient()
        with EventEmitter(client_factory=lambda: client) as emitter:
            emitter(_event())
        assert client.closed is True


# ── 4. stable, meaningful id ─────────────────────────────────────────────────


class TestStableId:
    def test_same_occurrence_same_run_id_produces_the_same_envelope_id(self) -> None:
        client_a = FakeClient()
        client_b = FakeClient()
        emitter_a = EventEmitter(client_factory=lambda: client_a, run_id="run-x")
        emitter_b = EventEmitter(client_factory=lambda: client_b, run_id="run-x")
        emitter_a(_event(kind="step", step_index=3))
        emitter_b(_event(kind="step", step_index=3))
        assert client_a.published[0][0].id == client_b.published[0][0].id

    def test_different_step_index_produces_a_different_id(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="run-x")
        emitter(_event(kind="step", step_index=1))
        emitter(_event(kind="step", step_index=2))
        ids = [envelope.id for envelope, _topic in client.published]
        assert ids[0] != ids[1]

    def test_different_run_id_produces_a_different_id(self) -> None:
        client_a = FakeClient()
        client_b = FakeClient()
        EventEmitter(client_factory=lambda: client_a, run_id="run-a")(
            _event(kind="step", step_index=1)
        )
        EventEmitter(client_factory=lambda: client_b, run_id="run-b")(
            _event(kind="step", step_index=1)
        )
        assert client_a.published[0][0].id != client_b.published[0][0].id

    def test_different_kind_produces_a_different_id(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="run-x")
        emitter(_event(kind="step", step_index=1))
        emitter(_event(kind="hook", step_index=1))
        ids = [envelope.id for envelope, _topic in client.published]
        assert ids[0] != ids[1]

    def test_kinds_with_no_stable_counter_fall_back_to_ordinal(self) -> None:
        # "phase" carries no step_index/model_turns; two distinct phase events
        # from the SAME emitter still get distinct ids (via the ordinal), and
        # the id is well-formed either way.
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="run-x")
        emitter(_event(kind="phase", detail="thinking"))
        emitter(_event(kind="phase", detail="thinking"))
        ids = [envelope.id for envelope, _topic in client.published]
        assert ids[0] != ids[1]

    def test_id_is_well_formed_for_the_real_envelope_grammar(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="run-x")
        emitter(_event(kind="degradation", step_index=1))
        envelope, _topic = client.published[0]
        # Round-trips through the REAL validator without raising.
        Envelope.from_dict(envelope.to_dict())


# ── 5. real envelope + topic contract ───────────────────────────────────────


class TestEnvelopeAndTopicContract:
    def test_publishes_a_real_valid_envelope(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="run-x")
        emitter(_event(kind="step", detail="read_file", step_index=2, ok=True))
        envelope, topic = client.published[0]
        assert isinstance(envelope, Envelope)
        assert envelope.type == f"{EVENT_TYPE_PREFIX}step"
        assert envelope.run_id == "run-x"
        assert envelope.data["detail"] == "read_file"
        assert envelope.data["step_index"] == 2
        assert envelope.data["ok"] is True
        # The topic came from the REAL canonical mapping, not a hand-rolled string.
        assert topic == "events/embodiment/step"

    def test_detail_is_defensively_truncated(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client)
        emitter(_event(kind="turn", detail="x" * 10_000))
        envelope, _topic = client.published[0]
        assert len(envelope.data["detail"]) <= 500

    def test_source_defaults_when_no_identity_configured(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client)
        emitter(_event())
        assert emitter.source == DEFAULT_SOURCE
        assert client.published[0][0].source == DEFAULT_SOURCE

    def test_explicit_source_wins_outright(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, source="agent://builder")
        emitter(_event())
        assert emitter.source == "agent://builder"
        assert client.published[0][0].source == "agent://builder"

    def test_source_resolves_from_repo_path_identity(self, tmp_path: Path) -> None:
        repo = tmp_path / "consuming-rig"
        repo.mkdir()
        (repo / "culture.yaml").write_text("nick: gwen\n", encoding="utf-8")
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, repo_path=repo)
        assert emitter.source == "agent://gwen"
        emitter(_event())
        assert client.published[0][0].source == "agent://gwen"

    def test_repo_path_with_no_resolvable_identity_falls_back_to_default(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "no-identity-rig"
        repo.mkdir()
        emitter = EventEmitter(repo_path=repo)
        assert emitter.source == DEFAULT_SOURCE

    def test_run_id_defaults_to_a_fresh_value_per_instance(self) -> None:
        a = EventEmitter()
        b = EventEmitter()
        assert a.run_id != b.run_id
        assert a.run_id  # non-empty

    def test_run_id_explicit_override_is_respected(self) -> None:
        emitter = EventEmitter(run_id="my-run-42")
        assert emitter.run_id == "my-run-42"

    def test_every_event_from_one_emitter_shares_its_run_id(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="shared-run")
        emitter(_event(kind="step", step_index=1))
        emitter(_event(kind="step", step_index=2))
        assert all(env.run_id == "shared-run" for env, _t in client.published)

    def test_topic_mapping_matches_every_loop_kind(self) -> None:
        kinds = ("turn", "step", "hook", "phase", "degradation", "operator", "exit")
        for i, kind in enumerate(kinds):
            client = FakeClient()
            emitter = EventEmitter(client_factory=lambda c=client: c, run_id=f"run-{i}")
            emitter(_event(kind=kind))
            assert client.published[0][1] == f"events/embodiment/{kind}"


# ── 6. real loop.run() integration ──────────────────────────────────────────


class TestLoopIntegration:
    """Wire :class:`EventEmitter` as a real ``observer=`` on a real drive."""

    def _task(self) -> Task:
        return Task(id="t1", repo_path="/repo", instruction="do the thing")

    class _Executor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
            self.calls += 1
            if name == "finish":
                return ToolOutcome(result="done", finished=True, finish_summary="all done")
            return ToolOutcome(result="read some file")

        def _complete(self, messages: list[dict[str, Any]]) -> ModelResponse:
            if any(m.get("role") == "tool" for m in messages):
                return ModelResponse(
                    content="", tool_calls=[ToolCall(id="c2", name="finish", arguments={})]
                )
            return ModelResponse(
                content="", tool_calls=[ToolCall(id="c1", name="read_file", arguments={})]
            )

    def test_emitter_plugs_in_as_observer_without_changing_the_result(self) -> None:
        exec_a = self._Executor()
        baseline = run(exec_a._complete, self._task(), executor=exec_a, max_steps=5)

        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="drive-1")
        exec_b = self._Executor()
        with_events = run(
            exec_b._complete, self._task(), executor=exec_b, max_steps=5, observer=emitter
        )

        assert _stable(baseline.result.to_dict()) == _stable(with_events.result.to_dict())
        assert baseline.exit_reason == with_events.exit_reason
        # But the emitter genuinely saw and published real loop activity.
        assert len(client.published) > 0
        kinds = {env.type for env, _topic in client.published}
        assert f"{EVENT_TYPE_PREFIX}step" in kinds
        assert f"{EVENT_TYPE_PREFIX}exit" in kinds

    def test_every_published_envelope_round_trips_through_the_real_validator(self) -> None:
        client = FakeClient()
        emitter = EventEmitter(client_factory=lambda: client, run_id="drive-2")
        executor = self._Executor()
        run(executor._complete, self._task(), executor=executor, max_steps=5, observer=emitter)
        assert client.published  # the drive actually produced events
        for envelope, _topic in client.published:
            # Raises on any contract violation; also proves JSON round-trip.
            Envelope.from_dict(json.loads(envelope.to_json()))

    def test_a_dead_broker_degrades_once_across_a_real_multi_step_drive(self) -> None:
        client = FakeClient(ok=False)
        emitter = EventEmitter(client_factory=lambda: client, run_id="drive-3")
        executor = self._Executor()
        run(executor._complete, self._task(), executor=executor, max_steps=5, observer=emitter)
        assert len(emitter.degradations) == 1
        assert len(client.published) == 1  # disabled after the first attempt
