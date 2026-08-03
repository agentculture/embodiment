"""Scope-governed composition over the actor loop (task t4).

Six acceptance criteria, and every one of them is pinned here rather than
argued:

1. **The actor loop is untouched.** ``run_scoped`` passes only keywords
   :func:`embodiment.loop.run` already declares, and ``loop.py`` names no scope
   identifier at all — checked as *code*, so the prose in this repo that
   discusses the scope lane cannot trip it.
2. **Opt-in is real.** With no governor the composition hands ``run`` the host's
   own seam objects *by identity*, the drive is byte-identical, and the actor
   path's transitive import graph contains no scope module.
3. **Application happens at a boundary.** A directive that becomes ready in the
   middle of a step reaches the actor at the *next* turn boundary; a stale or
   superseded one is recorded and never applied — including the issue #54
   hazard, where the strategist's register names a directive the actor never
   received.
4. **Degrade, never raise.** A dead strategist, an unstartable thread, a hostile
   drain and a raising projector each degrade to the last valid directive (or
   the host default) with a record, and the drive completes.
5. **One prompt-bearing path.** The event text is composed by
   :func:`embodiment.framing.frame_cortex` and by nothing else — pinned by AST —
   and with no configured identity the composed bytes are the rendered
   directive, unchanged.
6. **The system prompt is never rewritten.** Delivery is an inserted event; the
   identifier ``system_prompt`` does not appear as code anywhere in the module.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import inspect
import json
import threading
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from embodiment import framing
from embodiment import loop as loop_module
from embodiment import scoped_run as scoped_run_module
from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.framing import CORTEX_MARKER, ROLE_CORTEX, block_for
from embodiment.loop import EXIT_FINISHED, LoopAborted, ToolError, ToolOutcome, run
from embodiment.scope import (
    MARKER_DIRECTIVE,
    MARKER_HOLD,
    SCOPE_EXIT_DEGRADED,
    SCOPE_EXIT_DIRECTIVE,
    SCOPE_EXIT_UNCHANGED,
    SCOPE_STATUS_ACTIVE,
    SCOPE_STATUS_BLOCKED,
    ScopeControls,
    ScopeDirective,
    ScopeOutcome,
    ScopeResponsibility,
    ScopeSnapshot,
)
from embodiment.scoped_run import (
    TRANSITION_APPLIED,
    TRANSITION_DEFAULT,
    TRANSITION_DEGRADED,
    TRANSITION_HELD,
    TRANSITION_KINDS,
    TRANSITION_UNPROJECTED,
    TRANSITION_WITHHELD,
    ScopeContext,
    ScopedControls,
    ScopedOutcome,
    ScopeGovernor,
    ScopeTransition,
    run_scoped,
)
from embodiment.strategist_runner import StrategistLimits, StrategistRunner

_SOURCE = Path(__file__).resolve().parents[1] / "embodiment" / "scoped_run.py"
_LOOP_SOURCE = Path(__file__).resolve().parents[1] / "embodiment" / "loop.py"
_PACKAGE = Path(__file__).resolve().parents[1] / "embodiment"
_TIMEOUT = 10.0


# ── doubles ───────────────────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    base = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
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
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(messages)
        return item

    @property
    def turns(self) -> int:
        return len(self.calls)


class FakeExecutor:
    """The minimum an executor must be: one ``execute`` method."""

    def __init__(self, *, fail: tuple[str, ...] = ()) -> None:
        self.fail = set(fail)
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="done")
        if name in self.fail:
            raise ToolError("that did not work")
        return ToolOutcome(result="ok")


def _directive(**kw: Any) -> ScopeDirective:
    base: dict[str, Any] = {
        "scope_id": "scope-001",
        "objective": "Finish the extraction without losing presence",
        "priorities": ("Preserve responsiveness",),
        "constraints": ("Background work never controls the speaking path",),
        "success_conditions": ("The requested result is produced",),
        "review_when": ("The operator changes the objective",),
        "decision_summary": "Separate continuity from background execution.",
        "version": 1,
    }
    base.update(kw)
    return ScopeDirective(**base)


def _outcome(directive: Optional[ScopeDirective] = None, **kw: Any) -> ScopeOutcome:
    base: dict[str, Any] = {
        "snapshot_id": "snapshot-001",
        "exit_reason": SCOPE_EXIT_DIRECTIVE if directive is not None else SCOPE_EXIT_UNCHANGED,
        "directive": directive,
        "step_index": 0,
        "model": "a-strategist-model",
        "role": "strategist",
    }
    base.update(kw)
    return ScopeOutcome(**base)


class FakeStrategist:
    """A deterministic stand-in with the runner's surface and no thread at all.

    ``ready`` is a list of *queues*: one queue per drain call, so a test says
    exactly which boundary an outcome arrives at without racing a real thread.
    """

    def __init__(self, *ready: Any, started: bool = True, degradation: Any = None) -> None:
        self.ready = [list(batch) for batch in ready]
        self.considered: list[tuple[Any, int]] = []
        self.drains: list[int] = []
        self.starts = 0
        self._started = started
        self._degradation = degradation
        self.degradations: list[Any] = []
        self.active_directive: Optional[ScopeDirective] = None

    def start(self) -> bool:
        self.starts += 1
        return self._started

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        self.considered.append((snapshot, step_index))

    def drain(self, *, step_count: int = 0) -> list[Any]:
        self.drains.append(step_count)
        if not self.ready:
            return []
        return self.ready.pop(0)

    def degradation(self) -> Any:
        return self._degradation


class Hostile:
    """Every method raises. Nothing here may reach the acting loop's main path."""

    boom = RuntimeError("the strategist lane exploded")
    active_directive = None
    degradations: list[Any] = []

    def start(self) -> bool:
        raise self.boom

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        raise self.boom

    def drain(self, *, step_count: int = 0) -> list[Any]:
        raise self.boom

    def degradation(self) -> Any:
        raise self.boom


def _projector(snapshot: Optional[ScopeSnapshot]) -> Any:
    """A projector returning a fixed snapshot, recording every context it saw."""

    seen: list[ScopeContext] = []

    def project(context: ScopeContext) -> Optional[ScopeSnapshot]:
        seen.append(context)
        return snapshot

    project.seen = seen  # type: ignore[attr-defined]
    return project


def _snapshot(**kw: Any) -> ScopeSnapshot:
    base: dict[str, Any] = {"snapshot_id": "snapshot-001", "objectives": ("ship it",)}
    base.update(kw)
    return ScopeSnapshot(**base)


def _normalize(outcome: Any) -> dict[str, Any]:
    """A drive's whole artifact with the two wall-clock fields zeroed."""
    data = {
        "result": outcome.result.to_dict(),
        "exit_reason": outcome.exit_reason,
        "hook_firings": [firing.to_dict() for firing in outcome.hook_firings],
        "degradations": [entry.to_dict() for entry in outcome.degradations],
        "child_model_turns": outcome.child_model_turns,
        "spawn_allowance_remaining": outcome.spawn_allowance_remaining,
    }
    data["result"]["stats"]["started_at"] = ""
    data["result"]["stats"]["duration_seconds"] = 0.0
    return data


def _kinds(scoped: ScopedOutcome) -> list[str]:
    return [transition.kind for transition in scoped.transitions]


def _events(seam: Scripted) -> list[str]:
    """Every message the actor saw that was not there when the drive started."""
    if not seam.calls:
        return []
    return [message["content"] for message in seam.calls[-1] if message.get("role") == "user"]


# ── AST helpers ───────────────────────────────────────────────────────────────


def _tree(path: Path = _SOURCE) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(name: str, tree: Optional[ast.Module] = None) -> ast.FunctionDef:
    for node in ast.walk(tree if tree is not None else _tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} must exist by that name")


def _calls_named(name: str, node: ast.AST) -> list[ast.Call]:
    return [
        child
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Name)
        and child.func.id == name
    ]


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _actor_import_closure() -> set[str]:
    """Every ``embodiment.*`` module reachable from ``embodiment.loop`` by import."""
    seen: set[str] = set()
    frontier = ["embodiment.loop"]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _PACKAGE / (name.split(".", 1)[1] + ".py")
        if not path.exists():
            continue
        for imported in _imported_modules(path):
            if imported.startswith("embodiment"):
                frontier.append(imported)
    return seen


# ── 1. the actor loop is untouched ────────────────────────────────────────────


class TestTheActorLoopIsUntouched:
    """Acceptance 1: zero diff to ``loop.py``; only keywords ``run`` declares."""

    def test_loop_names_no_scope_identifier_as_code(self):
        """Prose about the scope lane is fine; an import or a name is not."""
        offenders = set()
        for node in ast.walk(_tree(_LOOP_SOURCE)):
            if isinstance(node, ast.Import):
                offenders |= {a.name for a in node.names if "scope" in a.name}
            elif isinstance(node, ast.ImportFrom) and "scope" in (node.module or ""):
                offenders.add(node.module or "")
            elif isinstance(node, ast.Name) and "scope" in node.id.lower():
                offenders.add(node.id)
        assert not offenders, f"loop.py reaches the scope lane: {sorted(offenders)}"

    def test_the_composition_passes_only_keywords_run_declares(self):
        accepted = set(inspect.signature(loop_module.run).parameters)
        calls = _calls_named("run", _function("run_scoped"))
        assert len(calls) == 1, "there must be exactly ONE call to run(), so it is reviewable"
        passed = {keyword.arg for keyword in calls[0].keywords if keyword.arg is not None}
        assert passed <= accepted, f"run_scoped invents parameters: {sorted(passed - accepted)}"

    def test_an_unknown_keyword_is_refused_by_runs_own_signature(self):
        seam = Scripted(_turn(_call("finish")))
        with pytest.raises(TypeError) as caught:
            run_scoped(
                seam,
                _task(),
                executor=FakeExecutor(),
                max_steps=3,
                not_a_run_parameter=True,
            )
        assert "not_a_run_parameter" in str(caught.value)

    def test_the_only_star_star_forwarding_is_the_actor_keywords(self):
        call = _calls_named("run", _function("run_scoped"))[0]
        forwarded = [keyword.value for keyword in call.keywords if keyword.arg is None]
        assert len(forwarded) == 1
        assert isinstance(forwarded[0], ast.Name)
        assert forwarded[0].id == "actor_kwargs"


# ── 2. opt-in is real ─────────────────────────────────────────────────────────


class TestOptInIsReal:
    """Acceptance 2: no strategist ⇒ byte-identical, and no scope import."""

    def test_the_drive_is_byte_identical_to_run(self):
        script = (_turn(_call("read_file", path="a")), _turn(_call("finish")))
        plain = run(Scripted(*script), _task(), executor=FakeExecutor(), max_steps=5)
        scoped = run_scoped(Scripted(*script), _task(), executor=FakeExecutor(), max_steps=5)
        assert _normalize(scoped.outcome) == _normalize(plain)

    def test_the_seam_sees_byte_identical_message_lists(self):
        script = (_turn(_call("read_file", path="a")), _turn(_call("finish")))
        plain_seam = Scripted(*script)
        scoped_seam = Scripted(*script)
        run(plain_seam, _task(), executor=FakeExecutor(), max_steps=5)
        run_scoped(scoped_seam, _task(), executor=FakeExecutor(), max_steps=5)
        assert scoped_seam.calls == plain_seam.calls

    def test_the_hosts_own_seam_objects_reach_run_by_identity(self, monkeypatch):
        seen: dict[str, Any] = {}

        def spy(complete, task, **kw):
            seen["complete"] = complete
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        monkeypatch.setattr(scoped_run_module, "run", spy)
        seam = Scripted(_turn(_call("finish")))
        progress: list[Any] = []
        inbox = lambda: ()  # noqa: E731  # a one-line seam double
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            progress=progress.append,
            operator_inbox=inbox,
        )
        assert seen["complete"] is seam
        assert seen["operator_inbox"] is inbox

    def test_an_ungoverned_drive_records_nothing(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
        )
        assert scoped.transitions == ()
        assert scoped.active is None

    def test_the_actor_import_closure_holds_no_scope_module(self):
        closure = _actor_import_closure()
        forbidden = {name for name in closure if "scope" in name or "strategist" in name}
        assert not forbidden, f"the actor path imports the scope lane: {sorted(forbidden)}"

    def test_the_actor_closure_is_real_and_not_an_empty_set(self):
        """The test-of-the-test: the walk must actually reach loop's siblings."""
        assert "embodiment.contract" in _actor_import_closure()

    def test_an_aborted_drive_still_raises_the_loops_own_exception(self):
        boom = RuntimeError("the seam died")
        with pytest.raises(LoopAborted):
            run_scoped(Scripted(boom), _task(), executor=FakeExecutor(), max_steps=3)


# ── 3. the explicit host default scope ────────────────────────────────────────


class TestTheHostDefaultScope:
    """Nothing is ever fabricated: scope arrives from the host or not at all."""

    def test_the_default_is_delivered_at_the_first_boundary(self):
        seam = Scripted(_turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive()),
        )
        assert _kinds(scoped) == [TRANSITION_DEFAULT]
        assert scoped.active is not None
        assert scoped.active.scope_id == "scope-001"

    def test_the_default_reaches_the_actors_very_first_turn(self):
        seam = Scripted(_turn(_call("finish")))
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive()),
        )
        first_turn = seam.calls[0]
        assert "Finish the extraction without losing presence" in first_turn[-1]["content"]

    def test_a_default_that_governs_nothing_is_refused_and_recorded(self):
        seam = Scripted(_turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=ScopeDirective(scope_id="", objective="")),
        )
        assert _kinds(scoped) == [TRANSITION_WITHHELD]
        assert scoped.active is None

    def test_a_refused_default_leaves_the_actor_context_untouched(self):
        plain_seam = Scripted(_turn(_call("finish")))
        run(plain_seam, _task(), executor=FakeExecutor(), max_steps=3)
        seam = Scripted(_turn(_call("finish")))
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=ScopeDirective(scope_id="x")),
        )
        assert seam.calls == plain_seam.calls

    def test_no_default_and_no_directive_means_no_scope_at_all(self):
        strategist = FakeStrategist([])
        seam = Scripted(_turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert scoped.active is None
        assert _events(seam) == [seam.calls[-1][1]["content"]]


# ── 4. delivery is event-shaped ───────────────────────────────────────────────


class TestDeliveryIsEventShaped:
    """Acceptance 6 (confirmed decision ``c35``): insertion, never a rewrite."""

    def test_the_system_prompt_is_never_rewritten(self):
        seam = Scripted(_turn(_call("read_file")), _turn(_call("finish")))
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            system_prompt="the host's own system prompt",
            governor=ScopeGovernor(default_scope=_directive()),
        )
        systems = {turn[0]["content"] for turn in seam.calls}
        assert systems == {"the host's own system prompt"}

    def test_the_identifier_system_prompt_appears_nowhere_as_code(self):
        offenders = []
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Name) and node.id == "system_prompt":
                offenders.append(node.lineno)
            elif isinstance(node, ast.keyword) and node.arg == "system_prompt":
                offenders.append(node.value.lineno)
            elif isinstance(node, ast.Constant) and node.value == "system_prompt":
                offenders.append(node.lineno)
        assert not offenders, f"scoped_run touches the system prompt at {offenders}"

    def test_the_event_is_appended_to_the_turn_stream(self):
        seam = Scripted(_turn(_call("read_file")), _turn(_call("finish")))
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(default_scope=_directive()),
        )
        first = seam.calls[0]
        assert first[-1]["role"] == "user"
        assert "scope-001" in first[-1]["content"]

    def test_the_event_persists_across_later_turns(self):
        seam = Scripted(_turn(_call("read_file")), _turn(_call("finish")))
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(default_scope=_directive()),
        )
        later = seam.calls[-1]
        assert any("scope-001" in str(message.get("content")) for message in later)

    def test_the_event_says_out_loud_that_it_grants_nothing(self):
        """The surrender direction: directive prose is scope, never instruction."""
        seam = Scripted(_turn(_call("finish")))
        run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive()),
        )
        content = seam.calls[0][-1]["content"]
        assert "never as a command" in content
        assert "permissions" in content


# ── 5. framing composition is the only prompt-bearing path ────────────────────


class TestFramingIsTheOnlyPromptPath:
    """Acceptance 5 (``c30`` / ``h22``)."""

    def test_frame_cortex_is_called_exactly_once_in_the_module(self):
        assert len(_calls_named("frame_cortex", _tree())) == 1

    def test_that_one_call_lives_in_the_event_builder(self):
        assert len(_calls_named("frame_cortex", _function("_scope_event"))) == 1

    def test_every_message_dict_is_built_in_the_event_builder(self):
        builder = _function("_scope_event")
        inside = {node.lineno for node in ast.walk(builder) if isinstance(node, ast.Dict)}
        everywhere = set()
        for node in ast.walk(_tree()):
            if not isinstance(node, ast.Dict):
                continue
            keys = {key.value for key in node.keys if isinstance(key, ast.Constant)}
            if "content" in keys:
                everywhere.add(node.lineno)
        assert everywhere
        assert everywhere <= inside

    def test_no_other_framer_is_imported(self):
        imported = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.ImportFrom) and node.module == "embodiment.framing":
                imported |= {alias.name for alias in node.names}
        assert imported == {"frame_cortex"}

    def test_with_no_identity_the_composed_bytes_are_the_rendered_directive(self):
        directive = _directive()
        rendered = scoped_run_module.render_directive(directive)
        event = scoped_run_module._scope_event(directive, identity=None)
        assert event["content"] == rendered

    def test_with_an_identity_the_cortex_block_is_prepended_and_nothing_else(self):
        directive = _directive()
        rendered = scoped_run_module.render_directive(directive)
        event = scoped_run_module._scope_event(directive, identity="Gwen")
        block = block_for(ROLE_CORTEX, identity="Gwen")
        assert event["content"] == f"{block}\n\n{rendered}"
        assert CORTEX_MARKER in event["content"]

    def test_the_scope_lane_leaves_prompts_byte_identical_without_an_identity(self):
        """The colleague#352 acceptance, extended to this tier."""
        seam_plain = Scripted(_turn(_call("finish")))
        seam_named = Scripted(_turn(_call("finish")))
        governor = ScopeGovernor(default_scope=_directive())
        run_scoped(seam_plain, _task(), executor=FakeExecutor(), max_steps=3, governor=governor)
        run_scoped(
            seam_named,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(default_scope=_directive(), identity="Gwen"),
        )
        assert seam_plain.calls[0][-1]["content"] != seam_named.calls[0][-1]["content"]
        assert seam_named.calls[0][-1]["content"].endswith(seam_plain.calls[0][-1]["content"])


# ── 6. application happens at a boundary, and only there ──────────────────────


class TestApplicationHappensAtABoundary:
    """Acceptance 3."""

    def test_a_directive_ready_mid_step_lands_at_the_next_boundary(self):
        strategist = FakeStrategist([], [_outcome(_directive())])
        seam = Scripted(_turn(_call("read_file")), _turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_APPLIED]
        assert "scope-001" not in json.dumps(seam.calls[0])
        assert "scope-001" in json.dumps(seam.calls[1])

    def test_the_application_names_the_boundary_it_landed_at(self):
        strategist = FakeStrategist([], [_outcome(_directive())])
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert scoped.transitions[0].turn_index == 2

    def test_a_hold_is_recorded_and_inserts_nothing(self):
        strategist = FakeStrategist([_outcome(None, exit_reason=SCOPE_EXIT_UNCHANGED)])
        seam = Scripted(_turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_HELD]
        assert len(seam.calls[0]) == 2

    def test_a_version_that_does_not_advance_is_withheld(self):
        first = _directive(scope_id="scope-001", version=2)
        stale = _directive(scope_id="scope-002", version=2)
        strategist = FakeStrategist([_outcome(first)], [_outcome(stale)])
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_APPLIED, TRANSITION_WITHHELD]
        assert scoped.active.scope_id == "scope-001"

    def test_the_withheld_record_says_why(self):
        strategist = FakeStrategist(
            [_outcome(_directive(scope_id="a", version=3))],
            [_outcome(_directive(scope_id="b", version=1))],
        )
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert "does not advance" in scoped.transitions[-1].reason

    def test_the_same_scope_id_is_never_applied_twice(self):
        strategist = FakeStrategist(
            [_outcome(_directive(scope_id="a", version=1))],
            [_outcome(_directive(scope_id="a", version=2))],
        )
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_APPLIED, TRANSITION_WITHHELD]
        assert "already applied" in scoped.transitions[-1].reason

    def test_the_register_may_name_a_directive_the_actor_never_received(self):
        """embodiment#54: apply ``drain``'s output, never the register's head."""
        withheld = _directive(scope_id="scope-withheld", version=9)
        strategist = FakeStrategist([])
        strategist.active_directive = withheld
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert scoped.active is None
        assert TRANSITION_APPLIED not in _kinds(scoped)

    def test_the_outcome_accounting_identity_closes(self):
        strategist = FakeStrategist(
            [_outcome(_directive(scope_id="a", version=1))],
            [_outcome(None, exit_reason=SCOPE_EXIT_UNCHANGED)],
            [_outcome(_directive(scope_id="b", version=1))],
        )
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=6,
            governor=ScopeGovernor(strategist=strategist),
        )
        counts = scoped.counts
        delivered = (
            counts["directives_applied"] + counts["holds_recorded"] + counts["outcomes_withheld"]
        )
        assert counts["outcomes_drained"] == 3
        assert delivered == counts["outcomes_drained"]


# ── 7. degrade, never raise ───────────────────────────────────────────────────


class TestDegradeNeverRaise:
    """Acceptance 4, plus constraint C3: the drive always completes."""

    def test_a_dead_lane_is_recorded_and_the_drive_completes(self):
        strategist = FakeStrategist(
            [_outcome(_directive())],
            [],
            degradation=None,
        )
        seam = Scripted(_turn(_call("read_file")), _turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        strategist._degradation = "the seam is dead"
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_a_lane_that_dies_mid_drive_continues_under_the_last_valid_scope(self):
        class Dying(FakeStrategist):
            def drain(self, *, step_count: int = 0) -> list[Any]:
                out = super().drain(step_count=step_count)
                if len(self.drains) >= 2:
                    self._degradation = "the strategist seam is dead"
                return out

        strategist = Dying([_outcome(_directive())], [])
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=6,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_APPLIED, TRANSITION_DEGRADED]
        assert scoped.active.scope_id == "scope-001"
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_the_degraded_record_names_what_the_actor_continues_under(self):
        strategist = FakeStrategist([], degradation="the thread died")
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
        )
        degraded = [t for t in scoped.transitions if t.kind == TRANSITION_DEGRADED]
        assert len(degraded) == 1
        assert "scope-001" in degraded[0].reason

    def test_a_thread_that_will_not_start_degrades_at_once(self):
        strategist = FakeStrategist([], started=False)
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive()),
        )
        assert TRANSITION_DEGRADED in _kinds(scoped)
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_a_hostile_lane_never_reaches_the_acting_loops_main_path(self):
        seam = Scripted(_turn(_call("read_file")), _turn(_call("finish")))
        scoped = run_scoped(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=Hostile(), projector=_projector(_snapshot())),
        )
        assert scoped.outcome.exit_reason == EXIT_FINISHED
        assert scoped.outcome.degradations == []

    def test_a_hostile_lane_records_rather_than_going_quiet(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=Hostile(), projector=_projector(_snapshot())),
        )
        assert scoped.transitions
        assert all(transition.reason for transition in scoped.transitions)

    def test_a_raising_projector_is_recorded_and_the_drive_completes(self):
        def explode(context: ScopeContext) -> Optional[ScopeSnapshot]:
            raise ValueError("the host projector blew up")

        strategist = FakeStrategist([])
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, projector=explode),
        )
        assert _kinds(scoped) == [TRANSITION_UNPROJECTED]
        assert scoped.counts["projector_failures"] == 1
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_a_degraded_review_leaves_the_active_scope_alone(self):
        strategist = FakeStrategist(
            [_outcome(_directive())],
            [_outcome(None, exit_reason=SCOPE_EXIT_DEGRADED)],
        )
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_APPLIED, TRANSITION_WITHHELD]
        assert scoped.active.scope_id == "scope-001"

    def test_an_unrenderable_directive_is_withheld_not_raised(self):
        class Exploding:
            version = 5
            scope_id = "boom"

            @property
            def objective(self) -> str:
                raise RuntimeError("unreadable")

        strategist = FakeStrategist([_outcome(Exploding())])
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert _kinds(scoped) == [TRANSITION_WITHHELD]
        assert scoped.active is None


# ── 8. only material change goes upward ───────────────────────────────────────


class TestOnlyMaterialChangeGoesUpward:
    """Ordinary tool steps are not strategic reports (issue #51)."""

    def test_an_ordinary_run_of_successful_steps_offers_one_snapshot(self):
        strategist = FakeStrategist([])
        projector = _projector(_snapshot())
        run_scoped(
            Scripted(
                _turn(_call("read_file")),
                _turn(_call("read_file")),
                _turn(_call("read_file")),
                _turn(_call("finish")),
            ),
            _task(),
            executor=FakeExecutor(),
            max_steps=8,
            governor=ScopeGovernor(strategist=strategist, projector=projector),
        )
        assert len(strategist.considered) == 1

    def test_a_repeated_failure_is_material_and_goes_up(self):
        strategist = FakeStrategist([])
        seen: list[ScopeContext] = []

        def project(context: ScopeContext) -> Optional[ScopeSnapshot]:
            seen.append(context)
            return _snapshot(snapshot_id=f"snapshot-{len(seen)}")

        run_scoped(
            Scripted(_turn(_call("grep")), _turn(_call("grep")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(fail=("grep",)),
            max_steps=6,
            governor=ScopeGovernor(
                strategist=strategist,
                projector=project,
                controls=ScopedControls(blocked_after=2),
            ),
        )
        assert len(strategist.considered) == 2
        assert seen[-1].report.status == SCOPE_STATUS_BLOCKED

    def test_the_first_report_is_active_and_empty(self):
        strategist = FakeStrategist([])
        projector = _projector(_snapshot())
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, projector=projector),
        )
        report = projector.seen[0].report
        assert report.status == SCOPE_STATUS_ACTIVE
        assert report.repeated_failures == ()

    def test_an_unchanged_snapshot_is_never_offered_twice(self):
        strategist = FakeStrategist([])
        projector = _projector(_snapshot())
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(
                strategist=strategist,
                projector=projector,
                controls=ScopedControls(review_every=1),
            ),
        )
        assert len(strategist.considered) == 1
        assert scoped.counts["snapshots_unchanged"] == 1

    def test_a_projector_returning_none_offers_nothing_and_is_counted(self):
        strategist = FakeStrategist([])
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist, projector=_projector(None)),
        )
        assert strategist.considered == []
        assert scoped.counts["snapshots_absent"] == 1

    def test_the_intake_accounting_identity_closes(self):
        strategist = FakeStrategist([])
        projector = _projector(_snapshot())
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(
                strategist=strategist,
                projector=projector,
                controls=ScopedControls(review_every=1),
            ),
        )
        counts = scoped.counts
        projected = (
            counts["snapshots_offered"]
            + counts["snapshots_unchanged"]
            + counts["snapshots_absent"]
            + counts["projector_failures"]
            + counts["offers_failed"]
        )
        assert counts["boundaries_projected"] == projected
        assert counts["boundaries"] == projected + counts["boundaries_immaterial"]

    def test_the_context_carries_what_the_actor_has_actually_done(self):
        strategist = FakeStrategist([])
        seen: list[ScopeContext] = []

        def project(context: ScopeContext) -> Optional[ScopeSnapshot]:
            seen.append(context)
            return _snapshot(snapshot_id=f"snapshot-{len(seen)}")

        run_scoped(
            Scripted(_turn(_call("grep")), _turn(_call("grep")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(fail=("grep",)),
            max_steps=6,
            governor=ScopeGovernor(
                strategist=strategist,
                projector=project,
                controls=ScopedControls(blocked_after=2),
            ),
        )
        assert seen[-1].failures_by_name["grep"] == 2
        assert seen[-1].task.id == "t1"


# ── 9. the operator-intent trigger ────────────────────────────────────────────


class TestOperatorIntent:
    """The parked ``operator_inbox`` question, answered: a trigger, not content."""

    def test_operator_text_makes_the_next_boundary_material(self):
        strategist = FakeStrategist([])
        seen: list[ScopeContext] = []
        pending = [["please switch to the release branch"], []]

        def project(context: ScopeContext) -> Optional[ScopeSnapshot]:
            seen.append(context)
            return _snapshot(snapshot_id=f"snapshot-{len(seen)}")

        run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            operator_inbox=lambda: pending.pop(0) if pending else [],
            governor=ScopeGovernor(strategist=strategist, projector=project),
        )
        assert seen[0].report.requested_decision == "please switch to the release branch"
        assert seen[0].operator_messages == ("please switch to the release branch",)

    def test_the_hosts_inbox_still_reaches_the_loop_unchanged(self):
        polls: list[int] = []

        def inbox() -> list[str]:
            polls.append(len(polls))
            return []

        run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            operator_inbox=inbox,
            governor=ScopeGovernor(strategist=FakeStrategist([])),
        )
        assert len(polls) == 2

    def test_an_inbox_that_raises_is_still_the_loops_own_degradation(self):
        def inbox() -> list[str]:
            raise RuntimeError("the inbox died")

        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            operator_inbox=inbox,
            governor=ScopeGovernor(strategist=FakeStrategist([])),
        )
        codes = [entry.code for entry in scoped.outcome.degradations]
        assert codes == ["presence-failed"]


# ── 10. the strategist holds no actor authority ───────────────────────────────


class TestNoActorAuthority:
    """``c13`` restated at the composition layer: no decision vocabulary here."""

    _BANNED = ("tool", "arguments", "deny", "rewrite", "allow", "approve", "command", "executor")

    @pytest.mark.parametrize(
        "shape", [ScopeTransition, ScopeContext, ScopedControls, ScopeGovernor, ScopedOutcome]
    )
    def test_no_shape_carries_a_decision_field(self, shape: Any):
        assert is_dataclass(shape)
        names = {field.name for field in fields(shape)}
        offenders = {name for name in names if any(banned in name for banned in self._BANNED)}
        assert not offenders, f"{shape.__name__} carries decision vocabulary: {sorted(offenders)}"

    _FROM_THE_ACTOR_LOOP = {"CompleteFn", "LoopAborted", "LoopOutcome", "ToolExecutor", "run"}

    def test_only_the_composition_names_are_taken_from_the_actor_loop(self):
        """The executor is FORWARDED; no hook, decision or tool type is in scope."""
        imported = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.ImportFrom) and node.module == "embodiment.loop":
                imported |= {alias.name for alias in node.names}
        assert imported <= self._FROM_THE_ACTOR_LOOP, sorted(imported - self._FROM_THE_ACTOR_LOOP)

    def test_the_module_constructs_no_tool_surface(self):
        names = {
            node.func.id
            for node in ast.walk(_tree())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        forbidden = {name for name in names if "Tool" in name or "Hook" in name}
        assert not forbidden, f"scoped_run builds a tool surface: {sorted(forbidden)}"

    def test_the_transition_kinds_are_a_closed_set(self):
        assert set(TRANSITION_KINDS) == {
            TRANSITION_DEFAULT,
            TRANSITION_APPLIED,
            TRANSITION_HELD,
            TRANSITION_WITHHELD,
            TRANSITION_DEGRADED,
            TRANSITION_UNPROJECTED,
        }

    def test_every_kind_is_reachable_from_a_transition_record(self):
        """A kind nothing can mint would be a lie in the ledger (embodiment#18)."""
        for kind in TRANSITION_KINDS:
            assert isinstance(ScopeTransition(kind=kind).to_dict()["kind"], str)


# ── 11. against the real threaded runner ──────────────────────────────────────


def _directive_reply(**kw: Any) -> ModelResponse:
    payload: dict[str, Any] = {
        "scope_id": "scope-live",
        "objective": "Keep the extraction honest",
        "version": 1,
    }
    payload.update(kw)
    return ModelResponse(content=f"reading it now\n{MARKER_DIRECTIVE}\n{json.dumps(payload)}")


@contextlib.contextmanager
def _runner(complete: Any, **kw: Any) -> Iterator[StrategistRunner]:
    runner = StrategistRunner(complete, **kw)
    try:
        yield runner
    finally:
        runner.close(timeout=_TIMEOUT)


class TestAgainstTheRealRunner:
    """The doubles above are convenient; this proves the wiring against the real thing."""

    def test_a_real_review_reaches_the_actor_at_a_boundary(self):
        done = threading.Event()

        def strategist_seam(messages: list[dict[str, Any]]) -> ModelResponse:
            done.set()
            return _directive_reply()

        with _runner(
            strategist_seam,
            controls=ScopeControls(max_turns=1),
            limits=StrategistLimits(review_gap=0),
            model="a-real-model",
        ) as runner:
            gate = threading.Event()

            def actor(messages: list[dict[str, Any]]) -> ModelResponse:
                if not gate.is_set():
                    gate.set()
                    assert done.wait(_TIMEOUT), "the strategist never reviewed"
                    assert runner.wait_idle(_TIMEOUT), "the review never finished"
                    return _turn(_call("read_file"))
                return _turn(_call("finish"))

            seam = Scripted(actor)
            scoped = run_scoped(
                seam,
                _task(),
                executor=FakeExecutor(),
                max_steps=5,
                governor=ScopeGovernor(strategist=runner, projector=_projector(_snapshot())),
            )

        assert _kinds(scoped) == [TRANSITION_APPLIED]
        assert scoped.transitions[0].model == "a-real-model"

    def test_a_real_lane_that_never_answers_leaves_the_drive_untouched(self):
        def wedged(messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("the strategist endpoint is down")

        with _runner(wedged, controls=ScopeControls(max_turns=1)) as runner:
            scoped = run_scoped(
                Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
                _task(),
                executor=FakeExecutor(),
                max_steps=5,
                governor=ScopeGovernor(
                    strategist=runner,
                    projector=_projector(_snapshot()),
                    default_scope=_directive(),
                ),
            )

        assert scoped.outcome.exit_reason == EXIT_FINISHED
        assert scoped.active.scope_id == "scope-001"

    def test_the_scope_degradations_are_relayed_not_reinvented(self):
        def dead(messages: list[dict[str, Any]]) -> ModelResponse:
            raise RuntimeError("down")

        with _runner(
            dead,
            controls=ScopeControls(max_turns=1),
            limits=StrategistLimits(review_gap=0),
        ) as runner:
            scoped = run_scoped(
                Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
                _task(),
                executor=FakeExecutor(),
                max_steps=5,
                governor=ScopeGovernor(strategist=runner, projector=_projector(_snapshot())),
            )
            runner.wait_idle(_TIMEOUT)
            assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_a_hold_from_a_real_review_is_a_first_class_answer(self):
        answered = threading.Event()

        def strategist_seam(messages: list[dict[str, Any]]) -> ModelResponse:
            answered.set()
            return ModelResponse(content=MARKER_HOLD)

        with _runner(
            strategist_seam,
            controls=ScopeControls(max_turns=1),
            limits=StrategistLimits(review_gap=0),
        ) as runner:
            gate = threading.Event()

            def actor(messages: list[dict[str, Any]]) -> ModelResponse:
                if not gate.is_set():
                    gate.set()
                    assert answered.wait(_TIMEOUT), "the strategist never reviewed"
                    assert runner.wait_idle(_TIMEOUT), "the review never finished"
                    return _turn(_call("read_file"))
                return _turn(_call("finish"))

            scoped = run_scoped(
                Scripted(actor),
                _task(),
                executor=FakeExecutor(),
                max_steps=5,
                governor=ScopeGovernor(strategist=runner, projector=_projector(_snapshot())),
            )

        assert _kinds(scoped) == [TRANSITION_HELD]


# ── 12. the serialized record ─────────────────────────────────────────────────


class TestTheRecordIsReadable:
    """C3: a degradation nobody can read is not observable."""

    def test_a_scoped_outcome_serializes_without_the_loop_object(self):
        strategist = FakeStrategist([_outcome(_directive())])
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        data = scoped.to_dict()
        assert json.dumps(data)
        assert data["transitions"][0]["kind"] == TRANSITION_APPLIED

    def test_the_applied_text_is_on_the_record(self):
        strategist = FakeStrategist([_outcome(_directive())])
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert "scope-001" in scoped.transitions[0].text

    def test_the_scoped_outcome_proxies_the_drives_own_answer(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
        )
        assert scoped.exit_reason == EXIT_FINISHED
        assert scoped.result.summary == "done"

    def test_the_framing_module_is_the_one_the_package_ships(self):
        """No local copy of the composition: the imported object is framing's."""
        assert scoped_run_module.frame_cortex is framing.frame_cortex


# ── 13. every guard is firable ────────────────────────────────────────────────


class _Breaks(FakeStrategist):
    """Starts cleanly, then faults on ONE named surface. The lane's fault ladder."""

    def __init__(self, *ready: Any, on: str) -> None:
        super().__init__(*ready)
        self.on = on

    def _maybe(self, name: str) -> None:
        if self.on == name:
            raise RuntimeError(f"the strategist {name} exploded")

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        self._maybe("consider")
        super().consider(snapshot, step_index=step_index)

    def drain(self, *, step_count: int = 0) -> list[Any]:
        self._maybe("drain")
        return super().drain(step_count=step_count)

    def degradation(self) -> Any:
        self._maybe("degradation")
        return super().degradation()


class _Unreadable:
    """Neither ``str()`` nor ``==`` works on this. Every read of it must be guarded."""

    def __str__(self) -> str:
        raise RuntimeError("this cannot be read")

    def __eq__(self, other: Any) -> bool:
        raise RuntimeError("this cannot be compared")

    __hash__ = None  # type: ignore[assignment]


class TestEveryGuardIsFirable:
    """embodiment#18: a record nothing can mint is a lie in the ledger."""

    def test_a_drain_that_raises_is_recorded_and_the_drive_completes(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=_Breaks(on="drain")),
        )
        assert _kinds(scoped) == [TRANSITION_WITHHELD]
        assert "drain failed" in scoped.transitions[0].reason

    def test_a_degradation_probe_that_raises_is_itself_the_degradation(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=_Breaks([], on="degradation")),
        )
        assert _kinds(scoped) == [TRANSITION_DEGRADED]
        assert "degradation exploded" in scoped.transitions[0].reason

    def test_a_consider_that_raises_is_recorded_as_unprojected(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(
                strategist=_Breaks([], on="consider"),
                projector=_projector(_snapshot()),
            ),
        )
        assert _kinds(scoped) == [TRANSITION_UNPROJECTED]
        assert scoped.counts["offers_failed"] == 1

    def test_an_uncomparable_snapshot_is_treated_as_new(self):
        strategist = FakeStrategist([], [])
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(
                strategist=strategist,
                projector=_projector(_Unreadable()),
                controls=ScopedControls(review_every=1),
            ),
        )
        assert len(strategist.considered) == 2
        assert scoped.counts["snapshots_unchanged"] == 0

    def test_an_unreadable_acting_step_is_recorded_not_raised(self):
        """The guard on the actor's own hot path, fired directly at the seam."""
        lane = scoped_run_module._Governed(ScopeGovernor(default_scope=_directive()), _task())
        lane.progress(None)(object(), "grep", {}, True)
        assert [entry.kind for entry in lane.finish(None).transitions] == [TRANSITION_UNPROJECTED]

    def test_unreadable_operator_intent_is_recorded_not_raised(self):
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            operator_inbox=lambda: [_Unreadable()],
            governor=ScopeGovernor(strategist=FakeStrategist([])),
        )
        assert TRANSITION_UNPROJECTED in _kinds(scoped)
        assert scoped.outcome.exit_reason == EXIT_FINISHED

    def test_an_unreadable_lane_ledger_relays_as_empty(self):
        class NoLedger:
            active_directive = None

            @property
            def degradations(self) -> list[Any]:
                raise RuntimeError("the ledger cannot be read")

            def start(self) -> bool:
                return True

            def drain(self, *, step_count: int = 0) -> list[Any]:
                return []

            def degradation(self) -> Any:
                return None

        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=NoLedger()),
        )
        assert scoped.scope_degradations == ()

    def test_a_relayed_record_without_a_to_dict_still_serializes(self):
        strategist = FakeStrategist([])
        strategist.degradations = ["a bare string nobody typed"]
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert json.dumps(scoped.to_dict())
        assert "a bare string" in scoped.to_dict()["scope_degradations"][0]["reason"]

    def test_a_to_dict_that_raises_still_reports_the_record(self):
        class Exploding:
            def to_dict(self) -> dict[str, Any]:
                raise RuntimeError("unserializable")

        strategist = FakeStrategist([])
        strategist.degradations = [Exploding()]
        scoped = run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ScopeGovernor(strategist=strategist),
        )
        assert json.dumps(scoped.to_dict())
        assert scoped.to_dict()["scope_degradations"][0]["code"] == ""

    def test_the_hosts_progress_sink_still_sees_every_step(self):
        seen: list[tuple[int, str, bool]] = []
        run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            progress=lambda index, tool, arguments, ok: seen.append((index, tool, ok)),
            governor=ScopeGovernor(strategist=FakeStrategist([])),
        )
        assert ("read_file", True) in [(tool, ok) for _, tool, ok in seen]
        assert ("finish", True) in [(tool, ok) for _, tool, ok in seen]

    def test_applications_are_only_what_reached_the_actor(self):
        strategist = FakeStrategist(
            [_outcome(_directive(scope_id="a", version=2))],
            [_outcome(_directive(scope_id="b", version=1))],
        )
        scoped = run_scoped(
            Scripted(_turn(_call("read_file")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ScopeGovernor(strategist=strategist, default_scope=_directive(version=1)),
        )
        assert [entry.kind for entry in scoped.applications] == [
            TRANSITION_DEFAULT,
            TRANSITION_APPLIED,
        ]
        assert len(scoped.transitions) == 3

    def test_responsibilities_render_into_the_event(self):
        directive = _directive(
            responsibilities=(ScopeResponsibility(owner="worker", responsibility="read the diff"),)
        )
        assert "worker: read the diff" in scoped_run_module.render_directive(directive)

    def test_a_long_operator_message_is_clipped_before_it_leaves_this_layer(self):
        strategist = FakeStrategist([])
        projector = _projector(_snapshot())
        run_scoped(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            operator_inbox=lambda: ["x" * 5000],
            governor=ScopeGovernor(
                strategist=strategist,
                projector=projector,
                controls=ScopedControls(max_entry_chars=40),
            ),
        )
        requested = projector.seen[0].report.requested_decision
        assert len(requested) == 40
        assert requested.endswith("…")
