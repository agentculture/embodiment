"""The config-lane composition over the actor loop (task t9).

Two acceptance criteria, and the first one is the deliverable rather than a
side effect:

1. **``loop.py`` stays zero-diff.** The three pins the README states for the
   advisory lane hold for this lane too, and this file adds the fourth the
   task asks for:

   * ``run_configured`` calls :func:`embodiment.loop.run` **once**, passing only
     keywords ``run`` already declares — so an invented keyword is refused by
     ``run``'s own signature;
   * the host's objects reach ``run`` **by identity** — and this lane goes
     further than the advisory one: the actor's ``complete`` seam is never
     wrapped *even when the governor is armed*, because "the worker is unaware
     of the strategist" means there is no seam between the actor and its model
     for this tier to sit in;
   * an AST walk of the actor's whole transitive import closure shows no scope
     module reachable from ``loop.py`` — **and no config module either**, which
     is this task's fourth pin.

2. **The advisory lane is untouched.** Nothing here imports it, and
   ``tests/test_scope.py`` / ``tests/test_scoped_run.py`` /
   ``tests/test_strategist_runner.py`` stay byte-identical.

And the ``d5`` blocker: the ``verified → proposed`` demotion t4 emits has no
event kind in t5's five-kind vocabulary. It is resolved here as a **documented
mapping** rather than a sixth kind — see :class:`TestTheDemotionIsRepresentable`
— and every clause of that mapping is pinned below.
"""

from __future__ import annotations

import ast
import copy
import inspect
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment import config_run as config_run_module
from embodiment import loop as loop_module
from embodiment.capability import Capability, CapabilityCatalog
from embodiment.config_change import (
    ORIGIN_HOST,
    ORIGIN_STRATEGIST,
    SEAT_WORKER,
    TARGET_WORKER_PROMPTS,
    ConfigRefusal,
    change_from_payload,
)
from embodiment.config_events import (
    CONFIG_EVENT_APPLIED,
    CONFIG_EVENT_DEGRADATION,
    CONFIG_EVENT_KINDS,
    CONFIG_EVENT_PROPOSED,
    CONFIG_EVENT_REJECTED,
    CONFIG_EVENT_VERIFIED,
)
from embodiment.config_ledger import ConfigLedger, ConfigPersistence
from embodiment.config_lifecycle import (
    CHANGE_STALE_VERIFICATION,
    STATE_APPLIED,
    STATE_PROPOSED,
    STATE_VERIFIED,
    ConfigLifecycle,
    ConfigTransition,
    PromptSection,
    SeatConfig,
    VerificationResult,
    compose_prompt,
)
from embodiment.config_review import ConfigSnapshot
from embodiment.config_run import (
    DEMOTION_CODE,
    ConfigContext,
    ConfigGovernor,
    ConfiguredOutcome,
    events_for_refusal,
    events_for_transition,
    run_configured,
)
from embodiment.contract import ModelResponse, Task, ToolCall
from embodiment.loop import EXIT_FINISHED, LoopAborted, ToolOutcome, run

_SOURCE = Path(__file__).resolve().parents[1] / "embodiment" / "config_run.py"
_LOOP_SOURCE = Path(__file__).resolve().parents[1] / "embodiment" / "loop.py"
_PACKAGE = Path(__file__).resolve().parents[1] / "embodiment"


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
        return item


class FakeExecutor:
    """The minimum an executor must be: one ``execute`` method."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="done")
        return ToolOutcome(result="ok")


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog(
        entries=(
            Capability(capability_id="read_file", kind="tool"),
            Capability(capability_id="write", kind="permission"),
        ),
        catalog_id="test-host",
    )


def _prompt_change(change_id: str = "c1", text: str = "be careful", section: str = "care") -> Any:
    change, refusal = change_from_payload(
        {
            "target": TARGET_WORKER_PROMPTS,
            "change_id": change_id,
            "origin": ORIGIN_STRATEGIST,
            "section": section,
            "text": text,
        }
    )
    assert refusal is None
    return change


def _normalize(outcome: Any) -> dict[str, Any]:
    """A drive's whole artifact with the two wall-clock fields zeroed."""
    data = {
        "result": outcome.result.to_dict(),
        "exit_reason": outcome.exit_reason,
        "degradations": [entry.to_dict() for entry in outcome.degradations],
    }
    data["result"]["stats"]["started_at"] = ""
    data["result"]["stats"]["duration_seconds"] = 0.0
    return data


def _passing_verifier(request: Any) -> VerificationResult:
    return VerificationResult(passed=True, suite="unit", summary="green")


def _lifecycle(**kw: Any) -> ConfigLifecycle:
    kw.setdefault("verifier", _passing_verifier)
    kw.setdefault("catalog", _catalog())
    return ConfigLifecycle(**kw)


# ── AST helpers, cited from tests/test_scoped_run.py ──────────────────────────


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(name: str, path: Path = _SOURCE) -> ast.FunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _calls_named(name: str, tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
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


# ── pin 1: run's own signature refuses an invented keyword ────────────────────


class TestTheActorLoopIsUntouched:
    """Pin 1 and pin 3, in the form ``tests/test_scoped_run.py`` states them."""

    def test_loop_names_no_config_identifier_as_code(self) -> None:
        """Pin 4: prose about the config lane is fine; an import or a name is not."""
        offenders = set()
        for node in ast.walk(_tree(_LOOP_SOURCE)):
            if isinstance(node, ast.Import):
                offenders |= {a.name for a in node.names if "config" in a.name}
            elif isinstance(node, ast.ImportFrom) and "config" in (node.module or ""):
                offenders.add(node.module or "")
            elif isinstance(node, ast.Name) and "config" in node.id.lower():
                offenders.add(node.id)
        assert not offenders, f"loop.py reaches the config lane: {sorted(offenders)}"

    def test_the_composition_passes_only_keywords_run_declares(self) -> None:
        accepted = set(inspect.signature(loop_module.run).parameters)
        calls = _calls_named("run", _function("run_configured"))
        assert len(calls) == 1, "there must be exactly ONE call to run(), so it is reviewable"
        passed = {keyword.arg for keyword in calls[0].keywords if keyword.arg is not None}
        assert passed <= accepted, f"run_configured invents parameters: {sorted(passed - accepted)}"

    def test_an_unknown_keyword_is_refused_by_runs_own_signature(self) -> None:
        seam = Scripted(_turn(_call("finish")))
        task = _task()
        executor = FakeExecutor()  # every argument built first: python:S5778
        with pytest.raises(TypeError) as caught:
            run_configured(
                seam,
                task,
                executor=executor,
                max_steps=3,
                not_a_run_parameter=True,
            )
        assert "not_a_run_parameter" in str(caught.value)

    def test_the_only_star_star_forwarding_is_the_actor_keywords(self) -> None:
        call = _calls_named("run", _function("run_configured"))[0]
        forwarded = [keyword.value for keyword in call.keywords if keyword.arg is None]
        assert len(forwarded) == 1
        assert isinstance(forwarded[0], ast.Name)
        assert forwarded[0].id == "actor_kwargs"

    def test_the_actor_import_closure_holds_no_config_module(self) -> None:
        """Pin 4 proper: the whole transitive closure, not just loop.py itself."""
        closure = _actor_import_closure()
        forbidden = {name for name in closure if "config" in name or "capability" in name}
        assert not forbidden, f"the actor path imports the config lane: {sorted(forbidden)}"

    def test_the_actor_closure_still_holds_no_scope_module(self) -> None:
        """The advisory lane's own pin, re-asserted from this side of the wave."""
        closure = _actor_import_closure()
        forbidden = {name for name in closure if "scope" in name or "strategist" in name}
        assert not forbidden, f"the actor path imports the scope lane: {sorted(forbidden)}"

    def test_the_actor_closure_is_real_and_not_an_empty_set(self) -> None:
        """The test-of-the-test: the walk must actually reach loop's siblings."""
        assert "embodiment.contract" in _actor_import_closure()

    def test_this_lane_never_imports_the_advisory_lane(self) -> None:
        banned = {
            "embodiment.scope",
            "embodiment.scoped_run",
            "embodiment.strategist_runner",
            "embodiment.scope_events",
        }
        for name in ("config_run.py", "config_review.py", "config_runner.py"):
            leaked = _imported_modules(_PACKAGE / name) & banned
            assert not leaked, f"{name} couples to the advisory lane: {sorted(leaked)}"

    def test_only_the_composition_reaches_the_actor_loop(self) -> None:
        """The review loop and the runner hold no actor vocabulary at all."""
        for name in ("config_review.py", "config_runner.py"):
            assert "embodiment.loop" not in _imported_modules(_PACKAGE / name)
        assert "embodiment.loop" in _imported_modules(_SOURCE)


# ── pin 2: the host's objects are forwarded by identity ───────────────────────


class TestOptInIsReal:
    """An unarmed governor is byte-identical to calling ``run()`` directly."""

    def test_the_drive_is_byte_identical_to_run(self) -> None:
        script = (_turn(_call("read_file", path="a")), _turn(_call("finish")))
        plain = run(Scripted(*script), _task(), executor=FakeExecutor(), max_steps=5)
        configured = run_configured(
            Scripted(*script), _task(), executor=FakeExecutor(), max_steps=5
        )
        assert _normalize(configured.outcome) == _normalize(plain)

    def test_the_seam_sees_byte_identical_message_lists(self) -> None:
        script = (_turn(_call("read_file", path="a")), _turn(_call("finish")))
        plain_seam = Scripted(*script)
        configured_seam = Scripted(*script)
        run(plain_seam, _task(), executor=FakeExecutor(), max_steps=5)
        run_configured(configured_seam, _task(), executor=FakeExecutor(), max_steps=5)
        assert configured_seam.calls == plain_seam.calls

    def test_the_hosts_own_seam_objects_reach_run_by_identity(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        def spy(complete, task, **kw):
            seen["complete"] = complete
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        monkeypatch.setattr(config_run_module, "run", spy)
        seam = Scripted(_turn(_call("finish")))
        executor = FakeExecutor()
        progress: list[Any] = []
        inbox = lambda: ()  # noqa: E731  # a one-line seam double
        run_configured(
            seam,
            _task(),
            executor=executor,
            max_steps=3,
            progress=progress.append,
            operator_inbox=inbox,
            system_prompt="host prompt",
        )
        assert seen["complete"] is seam
        assert seen["executor"] is executor
        assert seen["progress"] is progress.append or seen["progress"] == progress.append
        assert seen["operator_inbox"] is inbox
        assert seen["system_prompt"] == "host prompt"

    def test_an_unarmed_governor_is_the_same_as_none(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        def spy(complete, task, **kw):
            seen["complete"] = complete
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        monkeypatch.setattr(config_run_module, "run", spy)
        seam = Scripted(_turn(_call("finish")))
        executor = FakeExecutor()
        inbox = lambda: ()  # noqa: E731  # a one-line seam double
        run_configured(
            seam,
            _task(),
            executor=executor,
            max_steps=3,
            governor=ConfigGovernor(),
            operator_inbox=inbox,
        )
        assert seen["complete"] is seam
        assert seen["executor"] is executor
        assert seen["operator_inbox"] is inbox

    def test_an_unarmed_drive_records_nothing(self) -> None:
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
        )
        assert configured.transitions == ()
        assert configured.events == ()
        assert configured.applied == ()
        assert configured.seat == ""

    def test_an_aborted_drive_still_raises_the_loops_own_exception(self) -> None:
        boom = RuntimeError("the seam died")
        seam = Scripted(boom)
        task = _task()
        executor = FakeExecutor()
        with pytest.raises(LoopAborted):
            run_configured(seam, task, executor=executor, max_steps=3)


class TestTheActorsModelSeamIsNeverWrapped:
    """This lane's own pin: the worker is unaware, so nothing sits at its seam."""

    def test_the_complete_seam_reaches_run_by_identity_even_when_armed(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        def spy(complete, task, **kw):
            seen["complete"] = complete
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        monkeypatch.setattr(config_run_module, "run", spy)
        seam = Scripted(_turn(_call("finish")))
        life = _lifecycle()
        run_configured(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
        )
        assert seen["complete"] is seam

    def test_the_module_never_builds_a_message(self) -> None:
        """No dict literal carrying a ``role`` key: nothing is inserted anywhere."""
        offenders = []
        for node in ast.walk(_tree(_SOURCE)):
            if isinstance(node, ast.Dict):
                keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
                if "role" in keys and "content" in keys:
                    offenders.append(node.lineno)
        assert not offenders, f"config_run.py builds an actor message at {offenders}"

    def test_no_framing_module_is_imported(self) -> None:
        assert "embodiment.framing" not in _imported_modules(_SOURCE)


# ── the seat's configuration actually reaches the actor ───────────────────────


class TestTheSeatRunsUnderItsConfiguration:
    def test_the_composed_prompt_is_what_the_actor_gets(self) -> None:
        config = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection(section="care", text="be careful"),)
        )
        life = _lifecycle(seats={SEAT_WORKER: config})
        seam = Scripted(_turn(_call("finish")))
        run_configured(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
        )
        assert seam.calls[0][0]["content"].endswith(compose_prompt(config))

    def test_a_host_prompt_survives_beside_the_configured_one(self) -> None:
        config = SeatConfig(
            seat=SEAT_WORKER, prompt=(PromptSection(section="care", text="be careful"),)
        )
        life = _lifecycle(seats={SEAT_WORKER: config})
        seam = Scripted(_turn(_call("finish")))
        run_configured(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
            system_prompt="host baseline",
        )
        system = seam.calls[0][0]["content"]
        assert "host baseline" in system and "be careful" in system

    def test_an_empty_seat_config_leaves_the_hosts_prompt_by_identity(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        def spy(complete, task, **kw):
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        monkeypatch.setattr(config_run_module, "run", spy)
        host_prompt = "host baseline"
        run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=_lifecycle()),
            system_prompt=host_prompt,
        )
        assert seen["system_prompt"] is host_prompt

    def test_the_run_is_opened_and_closed_so_the_seat_is_idle_afterwards(self) -> None:
        life = _lifecycle()
        governor = ConfigGovernor(lifecycle=life)
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=governor,
        )
        assert configured.run_id
        assert life.is_idle(SEAT_WORKER)
        assert life.open_runs() == ()

    def test_the_seat_is_idle_again_even_when_the_drive_aborts(self) -> None:
        life = _lifecycle()
        seam = Scripted(RuntimeError("dead seam"))
        task = _task()
        executor = FakeExecutor()
        governor = ConfigGovernor(lifecycle=life)
        with pytest.raises(LoopAborted) as caught:
            run_configured(seam, task, executor=executor, max_steps=3, governor=governor)
        assert life.is_idle(SEAT_WORKER)
        assert isinstance(caught.value.configured_outcome, ConfiguredOutcome)

    def test_a_host_executor_factory_mints_the_executor_not_this_module(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}
        replacement = FakeExecutor()

        def spy(complete, task, **kw):
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        monkeypatch.setattr(config_run_module, "run", spy)
        run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(
                lifecycle=_lifecycle(), executor_for=lambda config: replacement
            ),
        )
        assert seen["executor"] is replacement

    def test_a_raising_executor_factory_leaves_the_hosts_executor_by_identity(
        self, monkeypatch
    ) -> None:
        seen: dict[str, Any] = {}
        executor = FakeExecutor()

        def spy(complete, task, **kw):
            seen.update(kw)
            return loop_module.run(complete, task, **kw)

        def boom(config: Any) -> Any:
            raise RuntimeError("no executor for you")

        monkeypatch.setattr(config_run_module, "run", spy)
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=executor,
            max_steps=3,
            governor=ConfigGovernor(lifecycle=_lifecycle(), executor_for=boom),
        )
        assert seen["executor"] is executor
        assert any(entry.kind == CONFIG_EVENT_DEGRADATION for entry in configured.events)


# ── changes land BETWEEN runs, never under a working seat ─────────────────────


class TestChangesLandBetweenRuns:
    """The per-seat invariant: a seat mid-run never has its configuration moved.

    ``run_configured`` advances the gate at *both* ends — before ``begin_run``
    while every seat is idle, and again after ``end_run``. So a proposal that is
    already pending lands **before** the drive and the actor runs under it; a
    proposal the reviewer produces **during** the drive lands after it.
    """

    def test_a_pending_proposal_lands_before_the_drive_opens(self) -> None:
        life = _lifecycle()
        life.propose(_prompt_change("c1", text="LANDED-FIRST"))
        seam = Scripted(_turn(_call("finish")))
        configured = run_configured(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
        )
        assert "c1" in configured.applied
        assert life.proposal("c1").state == STATE_APPLIED
        assert "LANDED-FIRST" in seam.calls[0][0]["content"]

    def test_a_change_proposed_during_the_drive_never_reaches_that_drive(self) -> None:
        from embodiment.config_review import CONFIG_EXIT_CHANGES, ConfigOutcome

        life = _lifecycle()
        reviewer = FakeReviewer(
            ConfigOutcome(
                exit_reason=CONFIG_EXIT_CHANGES,
                changes=(_prompt_change("c9", text="ONLY-AFTERWARDS"),),
            )
        )
        seam = Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish")))
        configured = run_configured(
            seam,
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(
                lifecycle=life, reviewer=reviewer, projector=lambda ctx: ConfigSnapshot("s1")
            ),
        )
        assert "c9" in configured.applied
        assert not any("ONLY-AFTERWARDS" in str(messages) for messages in seam.calls)

    def test_the_pinned_config_is_constant_across_the_whole_drive(self) -> None:
        from embodiment.config_review import CONFIG_EXIT_CHANGES, ConfigOutcome

        life = _lifecycle()
        reviewer = FakeReviewer(
            ConfigOutcome(exit_reason=CONFIG_EXIT_CHANGES, changes=(_prompt_change("c9"),))
        )
        configured = run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(
                lifecycle=life, reviewer=reviewer, projector=lambda ctx: ConfigSnapshot("s1")
            ),
        )
        # Pinned at the empty baseline, and the seat has moved on since.
        assert configured.config_sha == SeatConfig(seat=SEAT_WORKER).config_sha
        assert life.effective(SEAT_WORKER).config_sha != configured.config_sha

    def test_the_gate_is_never_advanced_while_this_drives_seat_is_busy(self) -> None:
        """The ordering itself, made observable.

        The gate holds seat-idle independently, so moving ``advance`` inside the
        drive changes nothing about what gets *applied* — which is precisely why
        it needs its own test. What it changes is what the host is told: every
        pending proposal would fall out of the applied path and into a deferral,
        and a stream of deferrals for a lane behaving correctly is the noise t4's
        deviation ``d4`` refused. The mutation proof for this task found this one
        uncovered; it is covered now.
        """
        idle_at_each_advance: list[bool] = []

        class Watched:
            def __init__(self, inner: ConfigLifecycle) -> None:
                self._inner = inner

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def advance(self, **kw: Any) -> Any:
                idle_at_each_advance.append(self._inner.is_idle(SEAT_WORKER))
                return self._inner.advance(**kw)

        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        configured = run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(lifecycle=Watched(life)),
        )
        assert idle_at_each_advance, "the gate was never advanced at all"
        assert all(idle_at_each_advance), "the gate was advanced under a working seat"
        assert configured.deferrals == ()

    def test_the_second_drive_runs_under_what_the_first_one_applied(self) -> None:
        life = _lifecycle()
        life.propose(_prompt_change("c1", text="LANDED"))
        governor = ConfigGovernor(lifecycle=life)
        run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=governor,
        )
        seam = Scripted(_turn(_call("finish")))
        run_configured(seam, _task(), executor=FakeExecutor(), max_steps=3, governor=governor)
        assert "LANDED" in seam.calls[0][0]["content"]

    def test_the_same_lifecycle_never_re_emits_an_earlier_drives_history(self) -> None:
        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        governor = ConfigGovernor(lifecycle=life)
        first = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=governor,
        )
        second = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=governor,
        )
        assert first.transitions and second.transitions == ()


# ── the ledger discipline ─────────────────────────────────────────────────────


class TestTheLedgerRecordsEveryApply:
    def test_an_applied_change_reaches_the_ledger(self) -> None:
        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        ledger = ConfigLedger()
        run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life, ledger=ledger),
        )
        assert [entry.change_id for entry in ledger.entries] == ["c1"]

    def test_a_ledger_degradation_reaches_the_hosts_observer(self) -> None:
        """A save that fails DURING the drive is a fault this drive must report."""

        def refuse(payload: dict[str, Any]) -> None:
            raise OSError("the store is read-only")

        seen: list[Any] = []
        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        ledger = ConfigLedger(ConfigPersistence(save=refuse))
        run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life, ledger=ledger),
            observer=seen.append,
        )
        kinds = [entry.kind for entry in seen if hasattr(entry, "kind")]
        assert CONFIG_EVENT_DEGRADATION in kinds

    def test_a_degradation_the_ledger_recorded_before_the_drive_is_not_re_emitted(self) -> None:
        """The cursor rule: a host already heard about its own construction."""
        life = _lifecycle()
        ledger = ConfigLedger(ConfigPersistence(load=lambda: {"schema_version": 1, "lane": "d"}))
        assert ledger.degradations  # the advisory-era payload was refused at construction
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life, ledger=ledger),
        )
        assert configured.degradations == ()

    def test_an_applied_change_with_no_recoverable_unit_is_a_recorded_degradation(self) -> None:
        """C3: a config state the ledger cannot explain is itself a degradation."""

        class Amnesiac:
            """A gate that applies a change and then cannot say what it was."""

            def __init__(self, inner: ConfigLifecycle) -> None:
                self._inner = inner

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def proposal(self, change_id: Any) -> Any:
                return None

        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        ledger = ConfigLedger()
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=Amnesiac(life), ledger=ledger),
        )
        assert "c1" in configured.applied
        assert ledger.entries == ()
        assert any(entry.code == "config-run-host-seam-failed" for entry in configured.degradations)

    def test_a_hostile_ledger_never_reaches_the_actors_main_path(self) -> None:
        class Hostile:
            entries = ()

            @property
            def degradations(self) -> Any:
                raise RuntimeError("hostile ledger")

            def record_applied(self, change: Any, **kw: Any) -> Any:
                raise RuntimeError("hostile ledger")

        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life, ledger=Hostile()),
        )
        assert configured.outcome.exit_reason == EXIT_FINISHED
        assert any(entry.kind == CONFIG_EVENT_DEGRADATION for entry in configured.events)


# ── d5: the demotion is representable ─────────────────────────────────────────


class TestTheDemotionIsRepresentable:
    """Deviation ``d5``, resolved as a **documented mapping**, not a sixth kind.

    ``ConfigLifecycle._demote_stale_verification`` records a ``verified →
    proposed`` transition, and t5's vocabulary has five kinds with nothing
    named ``demoted``. The mapping this module ships:

    * the demotion is a **re-entry into ``proposed``** — the proposal really is
      in :data:`~embodiment.config_lifecycle.STATE_PROPOSED` afterwards and must
      be verified again — so it translates to
      :data:`~embodiment.config_events.CONFIG_EVENT_PROPOSED`;
    * what distinguishes it from a first proposal is the already-declared
      ``code`` field, set to
      :data:`~embodiment.config_lifecycle.CHANGE_STALE_VERIFICATION` — a
      machine-branchable marker, never free text in ``detail``;
    * the refusal t4 records alongside it translates to
      :data:`~embodiment.config_events.CONFIG_EVENT_DEGRADATION`, so the fix is
      on the fault stream where a host looks for one.
    """

    @staticmethod
    def _demotion() -> ConfigTransition:
        return ConfigTransition(
            sequence=4,
            change_id="c2",
            seat=SEAT_WORKER,
            target=TARGET_WORKER_PROMPTS,
            origin=ORIGIN_STRATEGIST,
            from_state=STATE_VERIFIED,
            to_state=STATE_PROPOSED,
            reason="the configuration moved between verify and apply",
            verdict="stale",
        )

    def test_the_demotion_translates_to_a_declared_kind(self) -> None:
        events = events_for_transition(self._demotion())
        assert [entry.kind for entry in events] == [CONFIG_EVENT_PROPOSED]
        assert events[0].kind in CONFIG_EVENT_KINDS

    def test_the_demotion_is_distinguishable_from_a_first_proposal(self) -> None:
        demoted = events_for_transition(self._demotion())[0]
        first = events_for_transition(
            ConfigTransition(change_id="c2", to_state=STATE_PROPOSED, from_state="")
        )[0]
        assert demoted.data["code"] == DEMOTION_CODE == CHANGE_STALE_VERIFICATION
        assert first.data["code"] == ""
        assert demoted.kind == first.kind

    def test_the_marker_is_a_declared_envelope_key_not_an_invented_one(self) -> None:
        """The mapping fills ``code``; it never widens the envelope."""
        demoted = events_for_transition(self._demotion())[0]
        first = events_for_transition(ConfigTransition(change_id="c2", to_state=STATE_PROPOSED))[0]
        assert set(demoted.data) == set(first.data)

    def test_no_sixth_kind_was_minted(self) -> None:
        assert len(CONFIG_EVENT_KINDS) == 6  # five states + one degradation
        assert not any("demot" in kind for kind in CONFIG_EVENT_KINDS)

    def test_the_demotion_happens_end_to_end_and_is_observed(self) -> None:
        """The real t4 path: two proposals, the second stale by the time it applies."""
        seen: list[Any] = []
        life = _lifecycle()
        first = life.propose(_prompt_change("c1", text="first", section="care"))
        second = life.propose(_prompt_change("c2", text="second", section="speed"))
        assert first is not None and second is not None
        life.verify("c1")
        life.verify("c2")
        life.apply("c1")
        outcome = life.apply("c2")
        assert outcome.refusal is not None
        assert life.proposal("c2").state == STATE_PROPOSED
        for transition in life.transitions:
            seen.extend(events_for_transition(transition))
        demotions = [
            entry
            for entry in seen
            if entry.kind == CONFIG_EVENT_PROPOSED and entry.data["code"] == DEMOTION_CODE
        ]
        assert len(demotions) == 1
        assert demotions[0].data["change_id"] == "c2"


# ── the whole transition vocabulary is translated ─────────────────────────────


class TestEveryTransitionTranslates:
    @pytest.mark.parametrize(
        "to_state,expected",
        [
            (STATE_PROPOSED, [CONFIG_EVENT_PROPOSED]),
            (STATE_VERIFIED, [CONFIG_EVENT_VERIFIED]),
            (STATE_APPLIED, [CONFIG_EVENT_APPLIED]),
        ],
    )
    def test_each_state_maps_to_its_kind(self, to_state: str, expected: list[str]) -> None:
        events = events_for_transition(ConfigTransition(change_id="c1", to_state=to_state))
        assert [entry.kind for entry in events] == expected

    def test_a_failed_suite_records_both_facts(self) -> None:
        """A suite that RAN and failed is a completed verification and a rejection."""
        events = events_for_transition(
            ConfigTransition(
                change_id="c1",
                from_state=STATE_PROPOSED,
                to_state="rejected",
                verdict="failed",
            )
        )
        assert [entry.kind for entry in events] == [CONFIG_EVENT_VERIFIED, CONFIG_EVENT_REJECTED]
        assert events[0].data["passed"] is False

    def test_a_stale_catalog_rejection_is_only_a_rejection(self) -> None:
        """Nothing was verified, so nothing claims a verification happened."""
        events = events_for_transition(
            ConfigTransition(change_id="c1", to_state="rejected", verdict="stale")
        )
        assert [entry.kind for entry in events] == [CONFIG_EVENT_REJECTED]

    def test_an_unknown_state_translates_to_nothing_rather_than_guessing(self) -> None:
        assert events_for_transition(ConfigTransition(change_id="c1", to_state="teleported")) == ()

    def test_a_hostile_transition_never_raises(self) -> None:
        class Hostile:
            @property
            def to_state(self) -> str:
                raise RuntimeError("hostile")

        assert events_for_transition(Hostile()) == ()

    def test_a_refusal_translates_to_the_degradation_kind(self) -> None:
        events = events_for_refusal(
            ConfigRefusal(code="config-change-duplicate-proposal", reason="dup", change_id="c1")
        )
        assert [entry.kind for entry in events] == [CONFIG_EVENT_DEGRADATION]
        assert events[0].data["code"] == "config-change-duplicate-proposal"

    def test_every_declared_kind_except_reverted_is_reachable_from_this_module(self) -> None:
        """``reverted`` is t6's; every other kind has a producer here."""
        produced = set()
        for to_state, verdict in (
            (STATE_PROPOSED, ""),
            (STATE_VERIFIED, "passed"),
            (STATE_APPLIED, "passed"),
            ("rejected", "failed"),
        ):
            for event in events_for_transition(
                ConfigTransition(change_id="c", to_state=to_state, verdict=verdict)
            ):
                produced.add(event.kind)
        for event in events_for_refusal(ConfigRefusal(code="x", reason="y")):
            produced.add(event.kind)
        assert produced == set(CONFIG_EVENT_KINDS) - {"config.change.reverted"}


# ── observability rides the host's own observer ───────────────────────────────


class TestObservabilityRidesTheHostsObserver:
    def test_the_actors_own_events_still_reach_the_same_observer(self) -> None:
        seen: list[Any] = []
        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
            observer=seen.append,
        )
        kinds = {entry.kind for entry in seen if hasattr(entry, "kind")}
        assert {"turn", "step", "exit"} <= kinds, "the actor's own LoopEvents were displaced"
        assert CONFIG_EVENT_APPLIED in kinds

    def test_a_raising_observer_never_aborts_the_drive(self) -> None:
        def boom(event: Any) -> None:
            raise RuntimeError("hostile observer")

        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
            observer=boom,
        )
        assert configured.outcome.exit_reason == EXIT_FINISHED
        assert "c1" in configured.applied

    def test_the_outcome_serializes_without_the_loop_object(self) -> None:
        life = _lifecycle()
        life.propose(_prompt_change("c1"))
        configured = run_configured(
            Scripted(_turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=3,
            governor=ConfigGovernor(lifecycle=life),
        )
        data = configured.to_dict()
        assert data["applied"] == ["c1"]
        assert "outcome" not in data


# ── the reviewer lane, wired end to end ───────────────────────────────────────


class FakeReviewer:
    """A ``ConfigRunner``-shaped double: offer in, outcomes out, no thread."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.offered: list[Any] = []
        self.closed = False

    def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
        self.offered.append(snapshot)

    def drain(self, *, step_count: int = 0) -> list[Any]:
        ready, self.outcomes = self.outcomes, []
        return ready

    def degradation(self) -> Optional[str]:
        return None

    @property
    def degradations(self) -> list[Any]:
        return []


class TestTheReviewerLane:
    def test_a_drained_change_becomes_a_proposal(self) -> None:
        from embodiment.config_review import CONFIG_EXIT_CHANGES, ConfigOutcome

        life = _lifecycle()
        reviewer = FakeReviewer(
            ConfigOutcome(exit_reason=CONFIG_EXIT_CHANGES, changes=(_prompt_change("c9"),))
        )
        configured = run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(
                lifecycle=life, reviewer=reviewer, projector=lambda ctx: ConfigSnapshot("s1")
            ),
        )
        assert "c9" in configured.applied
        assert reviewer.offered

    def test_the_projector_is_handed_what_the_actor_has_done(self) -> None:
        seen: list[ConfigContext] = []
        life = _lifecycle()
        run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(
                lifecycle=life,
                reviewer=FakeReviewer(),
                projector=lambda ctx: seen.append(ctx) or ConfigSnapshot(f"s{len(seen)}"),
            ),
        )
        assert seen
        assert seen[-1].calls_by_name.get("read_file") == 1
        assert seen[-1].seat == SEAT_WORKER

    def test_a_raising_projector_is_recorded_and_the_drive_completes(self) -> None:
        def boom(ctx: Any) -> Any:
            raise RuntimeError("projector down")

        configured = run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(
                lifecycle=_lifecycle(), reviewer=FakeReviewer(), projector=boom
            ),
        )
        assert configured.outcome.exit_reason == EXIT_FINISHED
        assert configured.counts["projector_failures"] >= 1

    def test_a_hostile_reviewer_never_reaches_the_actors_main_path(self) -> None:
        class Hostile:
            def consider(self, snapshot: Any, *, step_index: int = 0) -> None:
                raise RuntimeError("hostile reviewer")

            def drain(self, *, step_count: int = 0) -> Any:
                raise RuntimeError("hostile reviewer")

            def degradation(self) -> Any:
                raise RuntimeError("hostile reviewer")

        configured = run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(
                lifecycle=_lifecycle(),
                reviewer=Hostile(),
                projector=lambda ctx: ConfigSnapshot("s1"),
            ),
        )
        assert configured.outcome.exit_reason == EXIT_FINISHED
        assert configured.counts["offers_failed"] >= 1

    def test_the_host_progress_callable_sees_exactly_what_the_loop_sends(self) -> None:
        """Phase notices included: the wrapper observes, it never filters."""
        plain: list[tuple[Any, ...]] = []
        run(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            progress=lambda index, tool, args, ok: plain.append((index, tool, ok)),
        )
        governed: list[tuple[Any, ...]] = []
        run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(lifecycle=_lifecycle()),
            progress=lambda index, tool, args, ok: governed.append((index, tool, ok)),
        )
        assert governed == plain
        assert "read_file" in [tool for _, tool, _ in governed]

    def test_the_host_inbox_still_reaches_the_loop_unchanged(self) -> None:
        polled: list[int] = []

        def inbox() -> tuple[str, ...]:
            polled.append(1)
            return ("hello",)

        configured = run_configured(
            Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish"))),
            _task(),
            executor=FakeExecutor(),
            max_steps=5,
            governor=ConfigGovernor(lifecycle=_lifecycle()),
            operator_inbox=inbox,
        )
        assert polled
        assert configured.outcome.exit_reason == EXIT_FINISHED


# ── the governor's own shape ──────────────────────────────────────────────────


class TestTheGovernor:
    def test_an_empty_governor_is_unarmed(self) -> None:
        assert ConfigGovernor().armed is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"lifecycle": "sentinel"},
            {"reviewer": "sentinel"},
        ],
    )
    def test_any_wired_lane_arms_it(self, kwargs: dict[str, Any]) -> None:
        assert ConfigGovernor(**kwargs).armed is True

    def test_a_ledger_alone_does_not_arm_it(self) -> None:
        """Nothing to record without a lifecycle; arming would be a claim, not a lane."""
        assert ConfigGovernor(ledger=ConfigLedger()).armed is False

    def test_the_seat_defaults_to_the_worker(self) -> None:
        assert ConfigGovernor().seat == SEAT_WORKER

    def test_the_host_origin_is_never_this_modules_to_claim(self) -> None:
        """This module proposes nothing of its own — it only relays the reviewer's."""
        source = _SOURCE.read_text(encoding="utf-8")
        assert f'"{ORIGIN_HOST}"' not in source
        assert f'"{ORIGIN_STRATEGIST}"' not in source
