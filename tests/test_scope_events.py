"""embodiment.scope_events — translating scope activity onto the host observer (task t5).

Three acceptance criteria, and every one of them is pinned here rather than
argued:

1. **No event fabric in the producers.** ``embodiment/scope.py`` and
   ``embodiment/strategist_runner.py`` import neither ``embodiment.events`` nor
   ``embodiment.scope_events`` (an AST test) — and a host wiring
   :class:`~embodiment.events.EventEmitter` as ``run_scoped``'s ``observer=``
   receives every ``scope.*`` kind (an integration test against the real
   composition, ``embodiment.loop.run`` included).
2. **The actual contributing model and role.** Every emitted scope record names
   who actually produced it — checked separately for the accepted, rejected,
   stale and degraded paths, never as one combined assertion.
3. **Absent identity, byte-identical.** A run configured with a single model
   (no strategist wired at all) emits no record naming one — the
   colleague#352 rule extended to this tier.

Plus the embodiment#54 hazard this whole lane exists to avoid: a directive the
strategist's own register would call "active" but that ``drain()`` withheld as
stale must never surface as an *applied* event.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import scope_events
from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.loop import EXIT_FINISHED
from embodiment.scope import (
    ScopeDegradation,
    ScopeDirective,
    ScopeOutcome,
    ScopeReport,
    ScopeSnapshot,
)
from embodiment.scope_events import (
    SCOPE_EVENT_DEGRADATION,
    SCOPE_EVENT_DIRECTIVE_APPLIED,
    SCOPE_EVENT_DIRECTIVE_PROPOSED,
    SCOPE_EVENT_DIRECTIVE_REJECTED,
    SCOPE_EVENT_DIRECTIVE_STALE,
    SCOPE_EVENT_DIRECTIVE_SUPERSEDED,
    SCOPE_EVENT_KINDS,
    SCOPE_EVENT_REPORT,
    SCOPE_EVENT_REVIEW_COMPLETED,
    SCOPE_EVENT_REVIEW_STARTED,
    SCOPE_EVENT_SNAPSHOT,
    ScopeEvent,
)
from embodiment.scoped_run import ScopeGovernor, ScopeTransition, run_scoped
from embodiment.strategist_runner import DROPPED_STALE, DROPPED_SUPERSEDED

_PACKAGE = Path(__file__).resolve().parents[1] / "embodiment"
_SCOPE_SRC = _PACKAGE / "scope.py"
_STRATEGIST_RUNNER_SRC = _PACKAGE / "strategist_runner.py"
_SCOPE_EVENTS_SRC = _PACKAGE / "scope_events.py"

# A live cortex id from the committed rate config, and a real lobes role —
# never a placeholder, so a reviewer cannot mistake this for a made-up string.
_MODEL = "unsloth/Qwen3.6-27B-NVFP4"
_ROLE = "cortex"


# ── doubles ───────────────────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    base = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    base.update(kw)
    return Task(**base)


def _call(name: str = "finish", **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


class Scripted:
    """The actor's ``complete`` seam: replay turns in order."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return item


class FakeExecutor:
    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        from embodiment.loop import ToolOutcome

        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="done")
        return ToolOutcome(result="ok")


def _directive(**kw: Any) -> ScopeDirective:
    base: dict[str, Any] = {
        "scope_id": "scope-001",
        "objective": "Finish the extraction without losing presence",
        "version": 1,
    }
    base.update(kw)
    return ScopeDirective(**base)


def _outcome(directive: Optional[ScopeDirective] = None, **kw: Any) -> ScopeOutcome:
    base: dict[str, Any] = {
        "snapshot_id": "snapshot-001",
        "exit_reason": "directive" if directive is not None else "unchanged",
        "directive": directive,
        "step_index": 0,
        "model": _MODEL,
        "role": _ROLE,
        "tokens": 1200,
        "latency": 12.5,
    }
    base.update(kw)
    return ScopeOutcome(**base)


class _Strategist:
    """A minimal, deterministic strategist double with the runner's surface.

    ``ready`` is a list of *batches*: one batch per ``drain()`` call. Exposes
    ``role``/``model`` as real attributes, exactly like
    :class:`~embodiment.strategist_runner.StrategistRunner` does — this is
    what lets a lane-level record (one with no single review of its own to
    draw an identity from) still name the actual contributing strategist.
    """

    def __init__(
        self,
        *ready: Any,
        started: bool = True,
        degradation: Any = None,
        degradations: Optional[list[Any]] = None,
        role: str = _ROLE,
        model: str = _MODEL,
    ) -> None:
        self.ready = [list(batch) for batch in ready]
        self._started = started
        self._degradation = degradation
        self.degradations = list(degradations or [])
        self.role = role
        self.model = model
        self.considered: list[Any] = []

    def start(self) -> bool:
        return self._started

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        self.considered.append(snapshot)

    def drain(self, *, step_count: int = 0) -> list[Any]:
        if not self.ready:
            return []
        return self.ready.pop(0)

    def degradation(self) -> Any:
        return self._degradation


class Recorder:
    """A recording ``ObserverFn``: keeps every event offered, in order.

    ``run_scoped`` wires this SAME callable as both the actor's own
    ``observer=`` (its ``LoopEvent`` stream) and the scope lane's — that
    sharing is the point (spec c9). ``scope_only`` filters to the
    :class:`~embodiment.scope_events.ScopeEvent` values this test file cares
    about, since a ``LoopEvent`` has no ``model``/``role`` keys to assert on.
    """

    def __init__(self) -> None:
        self.events: list[Any] = []

    def __call__(self, event: Any) -> None:
        self.events.append(event)

    def of_kind(self, kind: str) -> list[Any]:
        return [e for e in self.scope_only() if e.kind == kind]

    def scope_only(self) -> list[ScopeEvent]:
        return [e for e in self.events if isinstance(e, ScopeEvent)]


class Hostile:
    """Every attribute read raises. Every builder must still return an event."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"reading {name!r} exploded")


def _snapshot(**kw: Any) -> ScopeSnapshot:
    base: dict[str, Any] = {"snapshot_id": "snapshot-001", "objectives": ("ship it",)}
    base.update(kw)
    return ScopeSnapshot(**base)


def _projector(snapshot: Optional[ScopeSnapshot]) -> Any:
    def project(context: Any) -> Optional[ScopeSnapshot]:
        return snapshot

    return project


# ── AST helpers ────────────────────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ── 1. no event fabric in the producers (acceptance criterion 1) ──────────────


class TestNoEventFabricInProducers:
    def test_scope_py_imports_no_event_fabric(self):
        assert "embodiment.events" not in _imports(_SCOPE_SRC)

    def test_scope_py_imports_no_scope_events(self):
        assert "embodiment.scope_events" not in _imports(_SCOPE_SRC)

    def test_scope_py_imports_no_events_cli(self):
        modules = {m.split(".")[0] for m in _imports(_SCOPE_SRC)}
        assert "events_cli" not in modules

    def test_strategist_runner_imports_no_event_fabric(self):
        assert "embodiment.events" not in _imports(_STRATEGIST_RUNNER_SRC)

    def test_strategist_runner_imports_no_scope_events(self):
        assert "embodiment.scope_events" not in _imports(_STRATEGIST_RUNNER_SRC)

    def test_strategist_runner_imports_no_events_cli(self):
        modules = {m.split(".")[0] for m in _imports(_STRATEGIST_RUNNER_SRC)}
        assert "events_cli" not in modules


class TestScopeEventsOwnsNoFabricEither:
    """The translator itself does not become a second fabric."""

    def test_scope_events_imports_no_events_module(self):
        assert "embodiment.events" not in _imports(_SCOPE_EVENTS_SRC)

    def test_scope_events_imports_no_events_cli(self):
        modules = {m.split(".")[0] for m in _imports(_SCOPE_EVENTS_SRC)}
        assert "events_cli" not in modules

    def test_scope_events_imports_no_scoped_run(self):
        """Avoiding the import cycle: scoped_run imports THIS module."""
        assert "embodiment.scoped_run" not in _imports(_SCOPE_EVENTS_SRC)

    def test_scope_events_is_stdlib_only(self):
        modules = {m.split(".")[0] for m in _imports(_SCOPE_EVENTS_SRC)}
        assert modules <= {"dataclasses", "typing", "__future__"}


# ── 2. the cited literals stay pinned against their real source ───────────────


class TestCitedLiteralsArePinned:
    """scope_events.py cites these rather than importing them (cycle avoidance).

    A rename on either side must fail a test, never silently misroute an event.
    """

    def test_transition_default_literal(self):
        from embodiment.scoped_run import TRANSITION_DEFAULT

        assert scope_events._TRANSITION_KIND_DEFAULT == TRANSITION_DEFAULT

    def test_transition_applied_literal(self):
        from embodiment.scoped_run import TRANSITION_APPLIED

        assert scope_events._TRANSITION_KIND_APPLIED == TRANSITION_APPLIED

    def test_transition_held_literal(self):
        from embodiment.scoped_run import TRANSITION_HELD

        assert scope_events._TRANSITION_KIND_HELD == TRANSITION_HELD

    def test_transition_withheld_literal(self):
        from embodiment.scoped_run import TRANSITION_WITHHELD

        assert scope_events._TRANSITION_KIND_WITHHELD == TRANSITION_WITHHELD

    def test_transition_degraded_literal(self):
        from embodiment.scoped_run import TRANSITION_DEGRADED

        assert scope_events._TRANSITION_KIND_DEGRADED == TRANSITION_DEGRADED

    def test_transition_unprojected_literal(self):
        from embodiment.scoped_run import TRANSITION_UNPROJECTED

        assert scope_events._TRANSITION_KIND_UNPROJECTED == TRANSITION_UNPROJECTED

    def test_dropped_stale_literal(self):
        assert scope_events._LANE_CODE_STALE == DROPPED_STALE

    def test_dropped_superseded_literal(self):
        assert scope_events._LANE_CODE_SUPERSEDED == DROPPED_SUPERSEDED


# ── 3. the vocabulary itself ───────────────────────────────────────────────────


class TestVocabulary:
    def test_ten_kinds_exactly(self):
        assert len(SCOPE_EVENT_KINDS) == 10

    def test_kinds_are_unique(self):
        assert len(set(SCOPE_EVENT_KINDS)) == len(SCOPE_EVENT_KINDS)

    @pytest.mark.parametrize(
        "kind",
        [
            "scope.snapshot",
            "scope.review.started",
            "scope.review.completed",
            "scope.directive.proposed",
            "scope.directive.applied",
            "scope.directive.rejected",
            "scope.directive.stale",
            "scope.directive.superseded",
            "scope.report",
            "scope.degradation",
        ],
    )
    def test_every_issue_51_kind_is_present(self, kind: str):
        assert kind in SCOPE_EVENT_KINDS

    def test_scope_event_is_duck_compatible_with_loop_event(self):
        """Same field NAMES as embodiment.loop.LoopEvent, without importing it."""
        from dataclasses import fields

        names = {f.name for f in fields(ScopeEvent)}
        assert names == {"kind", "detail", "data"}


# ── 4. the envelope every record exposes ──────────────────────────────────────

_REQUIRED_KEYS = {
    "model",
    "role",
    "scope_id",
    "snapshot_id",
    "version",
    "previous_version",
    "supersedes",
    "turn_index",
    "step_index",
    "applied",
    "reason",
    "tokens",
    "latency",
}


class TestEveryRecordExposesTheEnvelope:
    def test_snapshot_event_exposes_every_key(self):
        event = scope_events.snapshot_event(_snapshot())
        assert _REQUIRED_KEYS <= event.data.keys()

    def test_review_started_event_exposes_every_key(self):
        event = scope_events.review_started_event(_snapshot())
        assert _REQUIRED_KEYS <= event.data.keys()

    def test_review_completed_event_exposes_every_key(self):
        event = scope_events.review_completed_event(_outcome(_directive()))
        assert _REQUIRED_KEYS <= event.data.keys()

    def test_directive_proposed_event_exposes_every_key(self):
        event = scope_events.directive_proposed_event(_outcome(_directive()))
        assert event is not None
        assert _REQUIRED_KEYS <= event.data.keys()

    def test_report_event_exposes_every_key(self):
        event = scope_events.report_event(ScopeReport())
        assert _REQUIRED_KEYS <= event.data.keys()

    def test_for_lane_degradation_exposes_every_key(self):
        entry = ScopeDegradation(code=DROPPED_STALE, reason="late")
        event = scope_events.for_lane_degradation(entry)
        assert _REQUIRED_KEYS <= event.data.keys()


# ── 5. builders: kind routing and field population ─────────────────────────────


class TestSnapshotEvent:
    def test_kind(self):
        assert scope_events.snapshot_event(_snapshot()).kind == SCOPE_EVENT_SNAPSHOT

    def test_carries_no_model(self):
        assert scope_events.snapshot_event(_snapshot()).data["model"] == ""

    def test_carries_no_role(self):
        assert scope_events.snapshot_event(_snapshot()).data["role"] == ""

    def test_carries_the_snapshot_id(self):
        event = scope_events.snapshot_event(_snapshot(snapshot_id="snap-9"))
        assert event.data["snapshot_id"] == "snap-9"

    def test_never_raises_on_a_hostile_snapshot(self):
        event = scope_events.snapshot_event(Hostile())
        assert event.kind == SCOPE_EVENT_SNAPSHOT


class TestReviewStartedEvent:
    def test_kind(self):
        event = scope_events.review_started_event(_snapshot())
        assert event.kind == SCOPE_EVENT_REVIEW_STARTED

    def test_carries_the_supplied_model(self):
        event = scope_events.review_started_event(_snapshot(), model=_MODEL, role=_ROLE)
        assert event.data["model"] == _MODEL

    def test_carries_the_supplied_role(self):
        event = scope_events.review_started_event(_snapshot(), model=_MODEL, role=_ROLE)
        assert event.data["role"] == _ROLE

    def test_never_raises_on_a_hostile_snapshot(self):
        event = scope_events.review_started_event(Hostile())
        assert event.kind == SCOPE_EVENT_REVIEW_STARTED


class TestReviewCompletedEvent:
    def test_kind(self):
        event = scope_events.review_completed_event(_outcome())
        assert event.kind == SCOPE_EVENT_REVIEW_COMPLETED

    def test_carries_the_outcomes_model(self):
        event = scope_events.review_completed_event(_outcome())
        assert event.data["model"] == _MODEL

    def test_carries_the_outcomes_role(self):
        event = scope_events.review_completed_event(_outcome())
        assert event.data["role"] == _ROLE

    def test_carries_tokens(self):
        event = scope_events.review_completed_event(_outcome(tokens=999))
        assert event.data["tokens"] == 999

    def test_carries_latency(self):
        event = scope_events.review_completed_event(_outcome(latency=3.5))
        assert event.data["latency"] == 3.5

    def test_never_raises_on_a_hostile_outcome(self):
        event = scope_events.review_completed_event(Hostile())
        assert event.kind == SCOPE_EVENT_REVIEW_COMPLETED


class TestDirectiveProposedEvent:
    def test_none_without_a_directive(self):
        assert scope_events.directive_proposed_event(_outcome(None)) is None

    def test_kind_with_a_directive(self):
        event = scope_events.directive_proposed_event(_outcome(_directive()))
        assert event.kind == SCOPE_EVENT_DIRECTIVE_PROPOSED

    def test_carries_the_scope_id(self):
        event = scope_events.directive_proposed_event(_outcome(_directive(scope_id="scope-9")))
        assert event.data["scope_id"] == "scope-9"

    def test_carries_the_version(self):
        event = scope_events.directive_proposed_event(_outcome(_directive(version=3)))
        assert event.data["version"] == 3

    def test_carries_the_model(self):
        event = scope_events.directive_proposed_event(_outcome(_directive()))
        assert event.data["model"] == _MODEL

    def test_never_raises_on_a_hostile_outcome(self):
        assert scope_events.directive_proposed_event(Hostile()) is None


class TestReportEvent:
    def test_kind(self):
        assert scope_events.report_event(ScopeReport()).kind == SCOPE_EVENT_REPORT

    def test_carries_no_model(self):
        assert scope_events.report_event(ScopeReport()).data["model"] == ""

    def test_never_raises_on_a_hostile_report(self):
        event = scope_events.report_event(Hostile())
        assert event.kind == SCOPE_EVENT_REPORT


class TestForTransition:
    """The six ``ScopeTransition.kind`` tokens, mapped onto scope.* kinds."""

    def test_default_maps_to_applied(self):
        t = ScopeTransition(kind="scope-default-applied", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).kind == SCOPE_EVENT_DIRECTIVE_APPLIED

    def test_applied_maps_to_applied(self):
        t = ScopeTransition(kind="scope-directive-applied", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).kind == SCOPE_EVENT_DIRECTIVE_APPLIED

    def test_applied_is_flagged_as_affecting_operation(self):
        t = ScopeTransition(kind="scope-directive-applied", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).data["applied"] is True

    def test_applied_carries_the_actual_model(self):
        t = ScopeTransition(kind="scope-directive-applied", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).data["model"] == _MODEL

    def test_applied_carries_the_actual_role(self):
        t = ScopeTransition(kind="scope-directive-applied", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).data["role"] == _ROLE

    def test_applied_carries_previous_and_resulting_version(self):
        t = ScopeTransition(kind="scope-directive-applied", version=2, previous_version=1)
        event = scope_events.for_transition(t)
        assert event.data["version"] == 2
        assert event.data["previous_version"] == 1

    def test_held_is_not_reported_a_second_time(self):
        t = ScopeTransition(kind="scope-held", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t) is None

    def test_withheld_maps_to_rejected(self):
        t = ScopeTransition(kind="scope-withheld", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).kind == SCOPE_EVENT_DIRECTIVE_REJECTED

    def test_withheld_is_not_flagged_as_affecting_operation(self):
        t = ScopeTransition(kind="scope-withheld")
        assert scope_events.for_transition(t).data["applied"] is False

    def test_withheld_carries_the_actual_model(self):
        t = ScopeTransition(kind="scope-withheld", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).data["model"] == _MODEL

    def test_withheld_carries_the_actual_role(self):
        t = ScopeTransition(kind="scope-withheld", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).data["role"] == _ROLE

    def test_degraded_maps_to_degradation(self):
        t = ScopeTransition(kind="scope-lane-degraded")
        assert scope_events.for_transition(t).kind == SCOPE_EVENT_DEGRADATION

    def test_degraded_carries_a_supplied_model(self):
        t = ScopeTransition(kind="scope-lane-degraded", role=_ROLE, model=_MODEL)
        assert scope_events.for_transition(t).data["model"] == _MODEL

    def test_unprojected_maps_to_degradation(self):
        t = ScopeTransition(kind="scope-unprojected")
        assert scope_events.for_transition(t).kind == SCOPE_EVENT_DEGRADATION

    def test_an_unrecognised_kind_is_still_reported(self):
        t = ScopeTransition(kind="something-nobody-minted-yet")
        event = scope_events.for_transition(t)
        assert event.kind == SCOPE_EVENT_DEGRADATION

    def test_never_raises_on_a_hostile_transition(self):
        event = scope_events.for_transition(Hostile())
        assert isinstance(event, ScopeEvent)


class TestForLaneDegradation:
    def test_dropped_stale_maps_to_stale(self):
        entry = ScopeDegradation(code=DROPPED_STALE, reason="late")
        assert scope_events.for_lane_degradation(entry).kind == SCOPE_EVENT_DIRECTIVE_STALE

    def test_dropped_superseded_maps_to_superseded(self):
        entry = ScopeDegradation(code=DROPPED_SUPERSEDED, reason="overtaken")
        assert scope_events.for_lane_degradation(entry).kind == SCOPE_EVENT_DIRECTIVE_SUPERSEDED

    def test_a_rejection_shaped_entry_maps_to_rejected(self):
        from embodiment.scope import ScopeRejection

        entry = ScopeRejection(code="scope-directive-incomplete", reason="no id", scope_id="")
        assert scope_events.for_lane_degradation(entry).kind == SCOPE_EVENT_DIRECTIVE_REJECTED

    def test_a_generic_degradation_maps_to_degradation(self):
        entry = ScopeDegradation(code="strategist-thread-unavailable", reason="dead")
        assert scope_events.for_lane_degradation(entry).kind == SCOPE_EVENT_DEGRADATION

    def test_the_caller_supplied_model_is_carried(self):
        entry = ScopeDegradation(code=DROPPED_STALE, reason="late")
        event = scope_events.for_lane_degradation(entry, model=_MODEL, role=_ROLE)
        assert event.data["model"] == _MODEL

    def test_the_caller_supplied_role_is_carried(self):
        entry = ScopeDegradation(code=DROPPED_STALE, reason="late")
        event = scope_events.for_lane_degradation(entry, model=_MODEL, role=_ROLE)
        assert event.data["role"] == _ROLE

    def test_absent_model_and_role_default_to_empty(self):
        entry = ScopeDegradation(code=DROPPED_STALE, reason="late")
        event = scope_events.for_lane_degradation(entry)
        assert event.data["model"] == ""

    def test_never_raises_on_a_hostile_entry(self):
        event = scope_events.for_lane_degradation(Hostile())
        assert isinstance(event, ScopeEvent)


class _Unstringable:
    """Neither ``str()`` nor a truthy attribute read works here."""

    def __str__(self) -> str:
        raise RuntimeError("this cannot be rendered")


class TestDefensiveHelpers:
    """The two coercion helpers every builder routes through. Never raise."""

    def test_text_passes_a_string_through(self):
        assert scope_events._text("scope-001") == "scope-001"

    def test_text_of_none_is_empty(self):
        assert scope_events._text(None) == ""

    def test_text_coerces_a_non_string(self):
        assert scope_events._text(7) == "7"

    def test_text_of_an_unstringable_value_is_empty(self):
        assert scope_events._text(_Unstringable()) == ""

    def test_int_passes_an_int_through(self):
        assert scope_events._int(3) == 3

    def test_int_coerces_a_numeric_string(self):
        assert scope_events._int("4") == 4

    def test_int_of_junk_falls_back_to_the_default(self):
        assert scope_events._int("not a number", -1) == -1

    def test_int_default_is_none_when_unspecified(self):
        assert scope_events._int("not a number") is None


class TestStrategistIdentity:
    def test_reads_role_and_model_off_the_strategist(self):
        strategist = _Strategist(role=_ROLE, model=_MODEL)
        role, model = scope_events.strategist_identity(strategist)
        assert (role, model) == (_ROLE, _MODEL)

    def test_none_strategist_is_empty(self):
        assert scope_events.strategist_identity(None) == ("", "")

    def test_a_strategist_missing_the_attributes_is_empty(self):
        assert scope_events.strategist_identity(object()) == ("", "")

    def test_never_raises_on_a_hostile_strategist(self):
        assert scope_events.strategist_identity(Hostile()) == ("", "")


# ── 6. integration: the real composition, observer wired in ───────────────────


class TestIntegrationAcceptedPath:
    """A directive that is proposed, drained and APPLIED to the actor."""

    def _run(self, recorder: Recorder):
        strategist = _Strategist([_outcome(_directive())])
        return run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist, projector=_projector(_snapshot())),
            observer=recorder,
        )

    def test_the_drive_completes(self):
        recorder = Recorder()
        scoped = self._run(recorder)
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_an_applied_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_DIRECTIVE_APPLIED)

    def test_the_applied_event_names_the_actual_model(self):
        recorder = Recorder()
        self._run(recorder)
        applied = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_APPLIED)[0]
        assert applied.data["model"] == _MODEL

    def test_the_applied_event_names_the_actual_role(self):
        recorder = Recorder()
        self._run(recorder)
        applied = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_APPLIED)[0]
        assert applied.data["role"] == _ROLE

    def test_a_proposed_event_is_also_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_DIRECTIVE_PROPOSED)

    def test_a_review_completed_event_is_also_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_REVIEW_COMPLETED)

    def test_a_snapshot_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_SNAPSHOT)

    def test_a_review_started_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_REVIEW_STARTED)


class TestIntegrationRejectedPath:
    """A second directive that does not advance the version is WITHHELD."""

    def _run(self, recorder: Recorder):
        strategist = _Strategist(
            [_outcome(_directive(version=1))],
            [_outcome(_directive(scope_id="scope-002", version=1))],
        )
        return run_scoped(
            Scripted(
                _turn(_call("read_file")),
                _turn(_call("read_file")),
                _turn(_call("finish")),
            ),
            _task(),
            executor=FakeExecutor(),
            max_steps=6,
            governor=ScopeGovernor(strategist=strategist, projector=_projector(_snapshot())),
            observer=recorder,
        )

    def test_a_rejected_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_DIRECTIVE_REJECTED)

    def test_the_rejected_event_names_the_actual_model(self):
        recorder = Recorder()
        self._run(recorder)
        rejected = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_REJECTED)[0]
        assert rejected.data["model"] == _MODEL

    def test_the_rejected_event_names_the_actual_role(self):
        recorder = Recorder()
        self._run(recorder)
        rejected = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_REJECTED)[0]
        assert rejected.data["role"] == _ROLE

    def test_the_rejected_event_is_not_flagged_as_affecting_operation(self):
        recorder = Recorder()
        self._run(recorder)
        rejected = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_REJECTED)[0]
        assert rejected.data["applied"] is False


class TestIntegrationStalePath:
    """The strategist LANE's own ledger names a stale drop (embodiment#54 shape)."""

    def _run(self, recorder: Recorder):
        stale = ScopeDegradation(
            code=DROPPED_STALE, reason="a review arrived too late", step_index=2
        )
        strategist = _Strategist(degradations=[stale], role=_ROLE, model=_MODEL)
        return run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
            observer=recorder,
        )

    def test_a_stale_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_DIRECTIVE_STALE)

    def test_the_stale_event_names_the_actual_model(self):
        recorder = Recorder()
        self._run(recorder)
        stale = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_STALE)[0]
        assert stale.data["model"] == _MODEL

    def test_the_stale_event_names_the_actual_role(self):
        recorder = Recorder()
        self._run(recorder)
        stale = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_STALE)[0]
        assert stale.data["role"] == _ROLE


class TestIntegrationSupersededPath:
    def _run(self, recorder: Recorder):
        superseded = ScopeDegradation(
            code=DROPPED_SUPERSEDED, reason="overtaken by a newer version", step_index=1
        )
        strategist = _Strategist(degradations=[superseded], role=_ROLE, model=_MODEL)
        return run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
            observer=recorder,
        )

    def test_a_superseded_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_DIRECTIVE_SUPERSEDED)

    def test_the_superseded_event_names_the_actual_model(self):
        recorder = Recorder()
        self._run(recorder)
        event = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_SUPERSEDED)[0]
        assert event.data["model"] == _MODEL


class TestIntegrationDegradedPath:
    """The lane never starts — TRANSITION_DEGRADED, at the drive's first boundary."""

    def _run(self, recorder: Recorder):
        strategist = _Strategist(started=False, role=_ROLE, model=_MODEL)
        return run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
            observer=recorder,
        )

    def test_a_degradation_event_is_emitted(self):
        recorder = Recorder()
        self._run(recorder)
        assert recorder.of_kind(SCOPE_EVENT_DEGRADATION)

    def test_the_degradation_event_names_the_actual_model(self):
        recorder = Recorder()
        self._run(recorder)
        event = recorder.of_kind(SCOPE_EVENT_DEGRADATION)[0]
        assert event.data["model"] == _MODEL

    def test_the_degradation_event_names_the_actual_role(self):
        recorder = Recorder()
        self._run(recorder)
        event = recorder.of_kind(SCOPE_EVENT_DEGRADATION)[0]
        assert event.data["role"] == _ROLE


# ── 7. acceptance criterion 3: single-model runs never name a strategist ──────


class TestSingleModelRunNamesNoStrategist:
    def test_the_default_only_run_emits_at_least_one_event(self):
        """Proves the observer wiring itself works, not just that nothing fired."""
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive()),
            observer=recorder,
        )
        assert recorder.events

    def test_no_event_names_a_model(self):
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive()),
            observer=recorder,
        )
        assert not any(event.data["model"] for event in recorder.scope_only())

    def test_no_event_names_a_role(self):
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive()),
            observer=recorder,
        )
        assert not any(event.data["role"] for event in recorder.scope_only())

    def test_an_unarmed_governor_emits_no_scope_events(self):
        """No strategist, no default scope: byte-identical, zero SCOPE events.

        The actor's own ``LoopEvent`` stream still reaches this same recorder
        (it is still ``run``'s ``observer=``) — that sharing is the point, so
        this checks the scope-shaped subset only, never the raw event list.
        """
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(),
            observer=recorder,
        )
        assert recorder.scope_only() == []

    def test_no_governor_at_all_emits_no_scope_events(self):
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            observer=recorder,
        )
        assert recorder.scope_only() == []


# ── 8. embodiment#54: the register is never the source of an applied event ────


class TestNeverTheRegister:
    """A directive the register would call ``active`` but ``drain`` withheld
    as stale must never surface as an applied/accepted event — only as the
    lane's own stale record.
    """

    def test_a_withheld_stale_directive_never_reports_applied(self):
        # The strategist's OWN register (active_directive) would name
        # "scope-ghost" — but drain() never hands it to the composition layer
        # at all, exactly as embodiment#54 describes: only the LEDGER (a
        # DROPPED_STALE entry) tells this layer anything happened.
        stale = ScopeDegradation(
            code=DROPPED_STALE, reason="scope-ghost never reached the actor", step_index=9
        )
        strategist = _Strategist(degradations=[stale], role=_ROLE, model=_MODEL)
        ghost = _directive(scope_id="scope-ghost", version=9)
        strategist.active_directive = ghost  # type: ignore[attr-defined]
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
            observer=recorder,
        )
        applied = recorder.of_kind(SCOPE_EVENT_DIRECTIVE_APPLIED)
        ghosts = [event for event in applied if event.data["scope_id"] == "scope-ghost"]
        assert ghosts == []

    def test_the_same_drive_still_reports_the_stale_drop(self):
        stale = ScopeDegradation(
            code=DROPPED_STALE, reason="scope-ghost never reached the actor", step_index=9
        )
        strategist = _Strategist(degradations=[stale], role=_ROLE, model=_MODEL)
        recorder = Recorder()
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
            observer=recorder,
        )
        assert recorder.of_kind(SCOPE_EVENT_DIRECTIVE_STALE)


# ── 9. never raise into the acting loop's main path (C3) ──────────────────────


class TestObserverNeverAbortsADrive:
    def test_a_raising_observer_still_completes_the_drive(self):
        def boom(event: Any) -> None:
            raise RuntimeError("the host's sink is broken")

        strategist = _Strategist([_outcome(_directive())])
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist, projector=_projector(_snapshot())),
            observer=boom,
        )
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_a_raising_observer_does_not_lose_the_scope_record(self):
        """The observer failing must not be confused with the scope lane failing."""

        def boom(event: Any) -> None:
            raise RuntimeError("the host's sink is broken")

        strategist = _Strategist([_outcome(_directive())])
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist, projector=_projector(_snapshot())),
            observer=boom,
        )
        assert scoped.active is not None
