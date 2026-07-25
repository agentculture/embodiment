"""Tests for :mod:`embodiment.loop` — the bounded tool loop (task t4).

Organised around the four things the extraction has to prove:

1. **Injection only** — the loop is driven by an injected ``complete`` callable
   and an injected tool-executor protocol, and imports nothing but stdlib +
   ``embodiment.*`` at module scope (no front module, no colleague, no shell).
2. **Termination is an honesty condition** — exactly three exit paths (model
   finish, an empty tool-call turn, the ``max_steps`` budget), proved both
   behaviourally and structurally (an AST read of ``_work_loop``), plus the
   proof that hooks add none and cannot extend the budget.
3. **The hook lifecycle** — four events, only ``pre_tool`` control-bearing.
4. **Presence and degradation** — an optional :class:`PresenceSink` binding that
   is byte-identical when absent or inactive, and a C3 ledger in which every
   degradation records a host-visible transition.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import (
    ERROR,
    INCOMPLETE,
    NO_RESULT_PRODUCED,
    OK,
    ContextPacket,
    ModelResponse,
    Task,
    TaskResult,
    ToolCall,
    WorkAborted,
)
from embodiment.loop import (
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_OBSERVE,
    DECISION_REWRITE,
    DEGRADED_CONTEXT_OVERFLOW,
    DEGRADED_HOOK_ERROR,
    DEGRADED_MEDIA_REJECTED,
    DEGRADED_OBSERVER,
    DEGRADED_OVERFLOW_EXHAUSTED,
    DEGRADED_PRESENCE,
    DEGRADED_PROGRESS,
    DEGRADED_TOOL_ARGUMENTS,
    EVENT_FINISH,
    EVENT_POST_TOOL,
    EVENT_PRE_TOOL,
    EVENT_TASK_START,
    EXIT_ABORTED,
    EXIT_BUDGET,
    EXIT_FINISHED,
    EXIT_REASONS,
    EXIT_STOPPED,
    HOOK_EVENTS,
    Boundary,
    HookDecision,
    LoopAborted,
    LoopControls,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    run,
)

_LOOP_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "loop.py"


# ── fixtures / doubles ────────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    fields = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    fields.update(kw)
    return Task(**fields)


def _call(name: str = "read_file", **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c{name}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "", **kw: Any) -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls), **kw)


class Scripted:
    """A ``complete`` seam replaying a fixed list of turns (then repeating the last).

    Records every message list it was handed so a test can assert what the loop
    put on the wire.
    """

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
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
    """The minimum an executor must be: one ``execute`` method.

    Optional ledger attributes (``changed`` / ``bytes_written`` / ``sub_results``)
    are opt-in — :class:`MinimalExecutor` below proves the loop drives without them.
    """

    def __init__(self, **outcomes: ToolOutcome) -> None:
        self.outcomes = outcomes
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.changed: set[str] = set()
        self.bytes_written = 0

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))
        if name not in self.outcomes:
            raise UnknownToolError(f"unknown tool: {name}")
        outcome = self.outcomes[name]
        if outcome.changed_file:
            self.changed.add(outcome.changed_file)
        return outcome


class MinimalExecutor:
    """Only ``execute`` — no ledger attributes at all."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="all done")
        return ToolOutcome(result="ok")


def _reading_executor(**extra: ToolOutcome) -> FakeExecutor:
    outcomes = {
        "read_file": ToolOutcome(result="file contents"),
        "write_file": ToolOutcome(result="wrote", changed_file="a.py"),
        "finish": ToolOutcome(result="done", finished=True, finish_summary="all done"),
    }
    outcomes.update(extra)
    return FakeExecutor(**outcomes)


class RecordingHooks:
    """An injected hook runner that records every event and replies from a script."""

    def __init__(self, **by_event: Any) -> None:
        self.by_event = by_event
        self.events: list[tuple[str, Optional[str], Optional[dict[str, Any]]]] = []

    def __call__(self, event: Any) -> Any:
        self.events.append((event.event, event.tool, event.arguments))
        reply = self.by_event.get(event.event)
        if callable(reply):
            return reply(event)
        return reply

    def names(self) -> list[str]:
        return [e[0] for e in self.events]


class FakeSink:
    """A :class:`PresenceSink` stand-in recording every beat the loop drives."""

    def __init__(self, *, active: bool = True, raises: bool = False) -> None:
        self._active = active
        self._raises = raises
        self.acknowledged: list[Optional[ContextPacket]] = []
        self.boundaries: list[tuple[int, bool]] = []
        self.operator: list[str] = []

    @property
    def active(self) -> bool:
        return self._active

    def acknowledge(self, packet: Optional[ContextPacket]) -> list[Any]:
        if self._raises:
            raise RuntimeError("sink exploded")
        self.acknowledged.append(packet)
        return []

    def on_operator_message(self, text: str) -> list[Any]:
        if self._raises:
            raise RuntimeError("sink exploded")
        self.operator.append(text)
        return []

    def on_progress_boundary(self, *, step_count: int = 0, phase_changed: bool = False):
        if self._raises:
            raise RuntimeError("sink exploded")
        self.boundaries.append((step_count, phase_changed))
        return []


def _drive(*responses: Any, **kw: Any):
    """Run the loop over ``responses`` with sane defaults; return the outcome."""
    kw.setdefault("executor", _reading_executor())
    kw.setdefault("max_steps", 10)
    task = kw.pop("task", None) or _task()
    return run(Scripted(*responses), task, **kw)


def _work_ctx() -> Any:
    """A minimal ``_Work`` context, for unit-testing a ``ctx``-taking helper
    without driving a whole loop through :func:`run`."""
    from embodiment.loop import _Work

    task = _task()
    return _Work(
        complete=lambda messages: _turn(_call("finish")),
        executor=_reading_executor(),
        task=task,
        result=TaskResult(task_id=task.id, status=OK),
        messages=[],
        controls=LoopControls(),
    )


# ── 1. injection only ─────────────────────────────────────────────────────────


def _imported_modules() -> set[str]:
    tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestImportPosture:
    """C1/C2: stdlib + embodiment only, no front module, no colleague, no shell."""

    def test_module_scope_imports_are_stdlib_or_embodiment(self):
        import sys

        for module in _imported_modules():
            top = module.split(".")[0]
            assert (
                top == "__future__" or top in sys.stdlib_module_names or top == "embodiment"
            ), module

    def test_imports_no_front_module(self):
        modules = _imported_modules()
        assert not any(m.startswith(("embodiment.cli", "embodiment.explain")) for m in modules)

    def test_imports_no_presence_engine(self):
        """Presence consumes the loop, never the reverse — the direction is one-way."""
        assert "embodiment.presence_engine" not in _imported_modules()

    def test_imports_no_colleague(self):
        assert not any(m.split(".")[0] == "colleague" for m in _imported_modules())

    def test_no_shell_coupling(self):
        """C6: tools reach the loop ONLY through the injected executor protocol."""
        source = _LOOP_SRC.read_text(encoding="utf-8")
        for token in ("shell_cli", "shell-cli", "subprocess", "os.system", "shlex"):
            assert token not in source, token

    def test_no_bare_suppress_of_everything(self):
        """C3: nothing degrades silently — no ``except: pass``, no bare suppress."""
        tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = node.body
                assert not (len(body) == 1 and isinstance(body[0], ast.Pass)), ast.dump(node)

    def test_executor_is_required(self):
        complete = Scripted(_turn())
        task = _task()
        with pytest.raises(TypeError):
            run(complete, task, max_steps=3)  # type: ignore[call-arg]

    def test_a_minimal_duck_typed_executor_drives_a_whole_run(self):
        executor = MinimalExecutor()
        outcome = _drive(_turn(_call("finish")), executor=executor)
        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.summary == "all done"
        assert executor.seen == [("finish", {})]

    def test_the_loop_never_constructs_its_own_tools(self):
        """No default executor: the host owns the tool surface, always."""
        source = _LOOP_SRC.read_text(encoding="utf-8")
        assert "executor = executor or" not in source

    def test_the_subagent_capability_arrived_through_an_embodiment_import(self):
        """Stated positively: new capability enters as a seam, never a new dep.

        Delegation is the first capability added to this module since the
        extraction, so it is the first test of whether the import pin above is
        load-bearing or decorative. It came from ``embodiment.subagent``, which
        itself imports nothing but stdlib and the contract.
        """
        import sys

        modules = _imported_modules()
        assert "embodiment.subagent" in modules
        seam = Path(_LOOP_SRC).parent / "subagent.py"
        seam_tree = ast.parse(seam.read_text(encoding="utf-8"))
        for node in ast.walk(seam_tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                assert top == "__future__" or top in sys.stdlib_module_names or top == "embodiment"


class TestTheDepthBoundIsPinnedInTheLoopToo:
    """loop.py's half of the structural depth bound (the seam's half is in
    ``tests/test_subagent.py``): this module computes no allowance of its own.

    The bound is only structural if BOTH halves hold. The seam can be as
    careful as it likes about ``attenuate`` while the loop quietly writes
    ``ctx.allowance = 99`` beside it, so the loop's writes are pinned here,
    where the rest of loop.py's AST pins live.
    """

    def test_every_allowance_write_passes_through_attenuate(self):
        tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "allowance" for t in node.targets)
        ]
        assert writes, "expected the loop to decrement an allowance somewhere"
        for node in writes:
            assert isinstance(node.value, ast.Call), ast.dump(node)
            assert isinstance(node.value.func, ast.Name), ast.dump(node)
            assert node.value.func.id == "attenuate", ast.dump(node)

    def test_no_allowance_is_ever_augmented(self):
        """``ctx.allowance += 1`` is the edit this pin exists to stop."""
        for node in ast.walk(ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))):
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Attribute):
                assert node.target.attr != "allowance", ast.dump(node)

    def test_the_zero_gate_is_checked_before_a_child_is_minted(self):
        """A drive at ``NO_SPAWNS`` is turned away before anything is built."""
        tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
        delegate = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_delegate"
        )
        gates = [
            n.lineno
            for n in ast.walk(delegate)
            if isinstance(n, ast.Compare)
            and any(isinstance(c, ast.Name) and c.id == "NO_SPAWNS" for c in n.comparators)
        ]
        mints = [
            n.lineno
            for n in ast.walk(delegate)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "child_call"
        ]
        assert gates and mints and min(gates) < min(mints)


# ── 2. termination ────────────────────────────────────────────────────────────


def _exit_returning_functions() -> set[str]:
    """Every function in ``loop.py`` that returns an ``EXIT_*`` constant.

    Counts a constant appearing ANYWHERE in the returned expression, not only a
    bare ``return EXIT_X``: ``_advance_turn`` returns it inside a tuple, and a
    new exit smuggled in as one element of some other shape would otherwise
    slip past this read entirely.
    """
    tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
    producers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Return) or inner.value is None:
                continue
            for name in ast.walk(inner.value):
                if isinstance(name, ast.Name) and name.id.startswith("EXIT_"):
                    producers.add(node.name)
    return producers


def _work_loop_node() -> ast.FunctionDef:
    tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_work_loop":
            return node
    raise AssertionError("_work_loop not found")


class TestTerminationMatrix:
    """Three exits, no fourth — the honesty condition."""

    def test_model_finish(self):
        outcome = _drive(_turn(_call("read_file", path="a"), _call("finish")))
        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.status == OK
        assert outcome.result.not_finished is False
        assert outcome.result.stopped_without_finish is False

    def test_empty_tool_call_turn(self):
        """A prose turn is nudged once, then the loop stops."""
        outcome = _drive(_turn(content="I think that's it."), max_steps=10)
        assert outcome.exit_reason == EXIT_STOPPED
        assert outcome.result.stopped_without_finish is True
        assert outcome.result.status == INCOMPLETE

    def test_empty_tool_call_turn_takes_the_nudge_first(self):
        complete = Scripted(_turn(content="hmm"), _turn(_call("finish")))
        outcome = run(complete, _task(), executor=_reading_executor(), max_steps=10)
        assert outcome.exit_reason == EXIT_FINISHED
        assert complete.turns == 2
        nudged = complete.calls[1][-1]
        assert nudged["role"] == "user" and "finish" in nudged["content"]

    def test_max_steps_budget(self):
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=3)
        assert outcome.exit_reason == EXIT_BUDGET
        assert outcome.result.not_finished is True
        assert outcome.result.stats.model_turns == 3
        assert outcome.result.status == INCOMPLETE

    @pytest.mark.parametrize("max_steps", [0, 1, 2, 3, 7])
    def test_completions_never_exceed_the_budget(self, max_steps):
        """The synthesis turn is reserved out of max_steps, never added to it."""
        complete = Scripted(_turn(_call("read_file", path="a")))
        outcome = run(complete, _task(), executor=_reading_executor(), max_steps=max_steps)
        assert complete.turns <= max(1, max_steps)
        assert outcome.result.stats.model_turns == complete.turns

    def test_budget_of_zero_still_runs_at_least_one_turn(self):
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=0)
        assert outcome.exit_reason == EXIT_BUDGET
        assert outcome.result.stats.model_turns == 1

    @pytest.mark.parametrize(
        "responses,max_steps",
        [
            ((_turn(_call("finish")),), 5),
            ((_turn(content="prose"),), 5),
            ((_turn(_call("read_file", path="a")),), 2),
            ((_turn(_call("nope")),), 2),
            ((_turn(),), 3),
        ],
    )
    def test_every_scenario_exits_through_one_of_the_three(self, responses, max_steps):
        outcome = _drive(*responses, max_steps=max_steps)
        assert outcome.exit_reason in EXIT_REASONS

    def test_exit_reasons_has_exactly_three_members(self):
        assert EXIT_REASONS == (EXIT_FINISHED, EXIT_STOPPED, EXIT_BUDGET)
        assert len(set(EXIT_REASONS)) == 3

    def test_module_declares_exactly_three_exit_constants(self):
        tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("EXIT_"):
                        names.add(target.id)
        # EXIT_ABORTED is deliberately included: it is NOT a loop exit. The
        # loop never returns it (proved below); only run() sets it when a seam
        # raised and the loop never reached an exit decision at all.
        assert names == {
            "EXIT_FINISHED",
            "EXIT_STOPPED",
            "EXIT_BUDGET",
            "EXIT_REASONS",
            "EXIT_ABORTED",
        }

    def test_work_loop_can_never_return_the_aborted_marker(self):
        """The fourth constant must not become a fourth exit."""
        tree = ast.parse(_LOOP_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_work_loop":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Name):
                        assert inner.value.id != "EXIT_ABORTED"

    def test_exit_reasons_still_excludes_the_aborted_marker(self):
        assert EXIT_ABORTED not in EXIT_REASONS

    def test_work_loop_returns_only_the_three_exit_constants(self):
        """Structural proof that no fourth exit path exists."""
        returned = set()
        for node in ast.walk(_work_loop_node()):
            if isinstance(node, ast.Return):
                assert isinstance(node.value, ast.Name), ast.dump(node)
                returned.add(node.value.id)
        assert returned == {"EXIT_FINISHED", "EXIT_STOPPED", "EXIT_BUDGET"}

    def test_work_loop_raises_nothing_of_its_own(self):
        """The only non-return way out is the injected seam's own exception."""
        assert not [n for n in ast.walk(_work_loop_node()) if isinstance(n, ast.Raise)]

    def test_an_unknown_tool_never_becomes_an_exit_path(self):
        """A broken tool-call channel costs budget, never a fourth exit."""
        outcome = _drive(_turn(_call("nope")), max_steps=4)
        assert outcome.exit_reason == EXIT_BUDGET
        assert outcome.result.stats.model_turns == 4
        assert all(step.ok is False for step in outcome.result.steps)

    def test_delegation_introduced_no_new_exit_producer(self):
        """The subagent seam is a STEP, not a fourth way out.

        Pinned as "which functions may return an ``EXIT_*`` constant" rather
        than as "``_work_loop`` has three returns", because the way a new
        capability would smuggle in an exit is by returning one from a helper
        the loop then propagates. The list is exactly three, and delegation is
        not on it.
        """
        assert _exit_returning_functions() == {
            "_work_loop",
            "_advance_turn",
            "_handle_no_tool_turn",
        }

    def test_the_delegation_helpers_return_no_exit_at_all(self):
        delegation = {"_delegate", "_run_child", "_charge_child", "_stamp_sub_result"}
        assert not (delegation & _exit_returning_functions())

    def test_a_raising_seam_is_not_a_loop_exit_but_a_preserved_partial(self):
        complete = Scripted(_turn(_call("read_file", path="a")), RuntimeError("engine down"))
        task = _task()
        executor = _reading_executor()
        with pytest.raises(LoopAborted) as excinfo:
            run(complete, task, executor=executor, max_steps=9)
        aborted = excinfo.value
        assert isinstance(aborted, WorkAborted)
        assert aborted.result.status == ERROR
        assert "engine down" in (aborted.result.error or "")
        assert len(aborted.result.steps) == 1  # partial work preserved
        assert aborted.outcome.result is aborted.result
        assert isinstance(aborted.__cause__, RuntimeError)


class TestHooksCannotChangeTermination:
    """Hooks add no exit path and cannot extend the budget."""

    def test_hooks_denying_every_event_still_exits_through_the_three(self):
        deny = HookDecision(decision=DECISION_DENY, reason="no")
        hooks = RecordingHooks(task_start=[deny], pre_tool=[deny], post_tool=[deny], finish=[deny])
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=3, hooks=hooks)
        assert outcome.exit_reason in EXIT_REASONS
        assert outcome.result.stats.model_turns == 3

    def test_a_hook_cannot_extend_the_budget(self):
        """Even a hook that fires on every event cannot buy an extra model turn."""
        allow = HookDecision(decision=DECISION_ALLOW)
        hooks = RecordingHooks(
            task_start=[allow], pre_tool=[allow], post_tool=[allow], finish=[allow]
        )
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=2, hooks=hooks)
        assert outcome.result.stats.model_turns == 2
        assert outcome.exit_reason == EXIT_BUDGET

    def test_a_raising_hook_cannot_abort_the_run(self):
        """It fails closed (so the tool is denied) but never propagates."""

        def boom(event: Any) -> Any:
            raise RuntimeError("hook blew up")

        hooks = RecordingHooks(task_start=boom, pre_tool=boom, post_tool=boom, finish=boom)
        outcome = _drive(_turn(_call("finish")), max_steps=2, hooks=hooks)
        assert outcome.exit_reason in EXIT_REASONS
        assert any(d.code == DEGRADED_HOOK_ERROR for d in outcome.degradations)

    def test_a_raising_hook_on_observe_only_events_leaves_the_finish_intact(self):
        def boom(event: Any) -> Any:
            raise RuntimeError("hook blew up")

        hooks = RecordingHooks(task_start=boom, post_tool=boom, finish=boom)
        outcome = _drive(_turn(_call("finish")), hooks=hooks)
        assert outcome.exit_reason == EXIT_FINISHED
        assert any(d.code == DEGRADED_HOOK_ERROR for d in outcome.degradations)

    def test_nudges_consume_the_budget_rather_than_extending_it(self):
        complete = Scripted(_turn(content="prose"))
        outcome = run(
            complete,
            _task(),
            executor=_reading_executor(),
            max_steps=2,
            controls=LoopControls(max_continue_nudges=5, synthesis=False),
        )
        # 2 turns is the whole budget even though 5 nudges were allowed.
        assert complete.turns == 2
        assert outcome.result.stats.model_turns == 2
        assert outcome.exit_reason == EXIT_BUDGET


# ── 3. hook lifecycle ─────────────────────────────────────────────────────────


class TestHookLifecycle:
    """Four events; only ``pre_tool`` is control-bearing."""

    def test_hook_events_vocabulary(self):
        assert HOOK_EVENTS == (EVENT_TASK_START, EVENT_PRE_TOOL, EVENT_POST_TOOL, EVENT_FINISH)

    def test_all_four_events_fire_in_order(self):
        hooks = RecordingHooks()
        _drive(_turn(_call("read_file", path="a"), _call("finish")), hooks=hooks)
        assert hooks.names() == [
            EVENT_TASK_START,
            EVENT_PRE_TOOL,
            EVENT_POST_TOOL,
            EVENT_PRE_TOOL,
            EVENT_POST_TOOL,
            EVENT_FINISH,
        ]

    def test_task_start_fires_once_before_the_loop(self):
        hooks = RecordingHooks()
        _drive(_turn(_call("read_file", path="a")), max_steps=3, hooks=hooks)
        assert hooks.names().count(EVENT_TASK_START) == 1
        assert hooks.names()[0] == EVENT_TASK_START

    def test_finish_fires_once_on_every_exit(self):
        for responses, steps in (
            ((_turn(_call("finish")),), 5),
            ((_turn(content="prose"),), 5),
            ((_turn(_call("read_file", path="a")),), 2),
        ):
            hooks = RecordingHooks()
            _drive(*responses, max_steps=steps, hooks=hooks)
            assert hooks.names().count(EVENT_FINISH) == 1
            assert hooks.names()[-1] == EVENT_FINISH

    def test_finish_fires_even_on_the_aborted_path(self):
        hooks = RecordingHooks()
        complete = Scripted(RuntimeError("down"))
        task = _task()
        executor = _reading_executor()
        with pytest.raises(LoopAborted):
            run(
                complete,
                task,
                executor=executor,
                max_steps=3,
                hooks=hooks,
            )
        assert hooks.names().count(EVENT_FINISH) == 1

    def test_pre_tool_sees_the_tool_and_arguments(self):
        hooks = RecordingHooks()
        _drive(_turn(_call("read_file", path="a.py")), max_steps=1, hooks=hooks)
        pre = [e for e in hooks.events if e[0] == EVENT_PRE_TOOL][0]
        assert pre[1] == "read_file"
        assert pre[2] == {"path": "a.py"}

    def test_pre_tool_deny_skips_execution_and_feeds_the_reason_back(self):
        executor = _reading_executor()
        hooks = RecordingHooks(pre_tool=[HookDecision(decision=DECISION_DENY, reason="nope")])
        outcome = _drive(
            _turn(_call("read_file", path="a")), max_steps=1, hooks=hooks, executor=executor
        )
        assert executor.seen == []  # never executed
        step = outcome.result.steps[0]
        assert step.ok is False and step.result == "nope"

    def test_pre_tool_deny_still_fires_post_tool(self):
        """post_tool observes the attempt; a denial is still a lifecycle event."""
        hooks = RecordingHooks(pre_tool=[HookDecision(decision=DECISION_DENY, reason="nope")])
        _drive(_turn(_call("read_file", path="a")), max_steps=1, hooks=hooks)
        assert EVENT_POST_TOOL in hooks.names()

    def test_pre_tool_rewrite_swaps_the_arguments(self):
        executor = _reading_executor()
        hooks = RecordingHooks(
            pre_tool=[HookDecision(decision=DECISION_REWRITE, arguments={"path": "safe.py"})]
        )
        _drive(
            _turn(_call("read_file", path="secret.py")),
            max_steps=1,
            hooks=hooks,
            executor=executor,
        )
        assert executor.seen == [("read_file", {"path": "safe.py"})]

    def test_first_decisive_pre_tool_decision_wins_and_short_circuits(self):
        decisions = [
            HookDecision(decision=DECISION_ALLOW, source="a"),
            HookDecision(decision=DECISION_OBSERVE, source="b"),
            HookDecision(decision=DECISION_DENY, reason="first deny", source="c"),
            HookDecision(decision=DECISION_REWRITE, arguments={"path": "x"}, source="d"),
        ]
        hooks = RecordingHooks(pre_tool=decisions)
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=1, hooks=hooks)
        assert outcome.result.steps[0].result == "first deny"
        fired = [f.source for f in outcome.hook_firings if f.event == EVENT_PRE_TOOL]
        # Allow/observe are recorded on the way to the decisive one; "d" is not reached.
        assert fired == ["a", "b", "c"]

    @pytest.mark.parametrize("event", [EVENT_TASK_START, EVENT_POST_TOOL, EVENT_FINISH])
    def test_observe_only_events_cannot_alter_control_flow(self, event):
        executor = _reading_executor()
        hooks = RecordingHooks(**{event: [HookDecision(decision=DECISION_DENY, reason="ignored")]})
        outcome = _drive(
            _turn(_call("read_file", path="a"), _call("finish")), hooks=hooks, executor=executor
        )
        assert outcome.exit_reason == EXIT_FINISHED
        assert [name for name, _ in executor.seen] == ["read_file", "finish"]
        assert all(step.ok for step in outcome.result.steps)

    @pytest.mark.parametrize("event", [EVENT_TASK_START, EVENT_POST_TOOL, EVENT_FINISH])
    def test_observe_only_rewrite_never_changes_arguments(self, event):
        executor = _reading_executor()
        hooks = RecordingHooks(
            **{event: [HookDecision(decision=DECISION_REWRITE, arguments={"path": "hacked"})]}
        )
        _drive(_turn(_call("read_file", path="real")), max_steps=1, hooks=hooks, executor=executor)
        assert executor.seen == [("read_file", {"path": "real"})]

    def test_every_firing_is_recorded_in_order(self):
        hooks = RecordingHooks(
            task_start=[HookDecision(decision=DECISION_ALLOW, source="ts")],
            pre_tool=[HookDecision(decision=DECISION_OBSERVE, source="pre")],
            post_tool=[HookDecision(decision=DECISION_ALLOW, source="post")],
            finish=[HookDecision(decision=DECISION_ALLOW, source="fin")],
        )
        outcome = _drive(_turn(_call("finish")), hooks=hooks)
        assert [f.event for f in outcome.hook_firings] == [
            EVENT_TASK_START,
            EVENT_PRE_TOOL,
            EVENT_POST_TOOL,
            EVENT_FINISH,
        ]
        assert [f.source for f in outcome.hook_firings] == ["ts", "pre", "post", "fin"]

    def test_a_single_decision_may_be_returned_unwrapped(self):
        hooks = RecordingHooks(pre_tool=HookDecision(decision=DECISION_DENY, reason="solo"))
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=1, hooks=hooks)
        assert outcome.result.steps[0].result == "solo"

    def test_a_raising_hook_fails_closed_to_a_deny(self):
        def boom(event: Any) -> Any:
            if event.event == EVENT_PRE_TOOL:
                raise RuntimeError("kaboom")
            return None

        executor = _reading_executor()
        hooks = RecordingHooks(pre_tool=boom)
        outcome = _drive(
            _turn(_call("read_file", path="a")), max_steps=1, hooks=hooks, executor=executor
        )
        assert executor.seen == []
        firing = [f for f in outcome.hook_firings if f.event == EVENT_PRE_TOOL][0]
        assert firing.decision == DECISION_DENY and "kaboom" in firing.reason
        assert any(d.code == DEGRADED_HOOK_ERROR for d in outcome.degradations)

    def test_no_hooks_means_no_firings_at_all(self):
        outcome = _drive(_turn(_call("finish")))
        assert outcome.hook_firings == []


# ── 4. tool execution ─────────────────────────────────────────────────────────


class TestToolExecution:
    def test_a_tool_error_costs_one_step_not_the_run(self):
        executor = FakeExecutor(finish=ToolOutcome(result="done", finished=True))

        def raiser(name: str, arguments: dict[str, Any]) -> ToolOutcome:
            raise ToolError("bad path")

        executor.execute = raiser  # type: ignore[assignment]
        outcome = _drive(_turn(_call("read_file", path="x")), max_steps=1, executor=executor)
        assert outcome.result.steps[0].ok is False
        assert "bad path" in outcome.result.steps[0].result

    @pytest.mark.parametrize("exc", [KeyError("path"), TypeError("nope"), ValueError("bad")])
    def test_malformed_argument_errors_are_self_correcting_steps(self, exc):
        class Raiser:
            def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
                raise exc

        outcome = _drive(_turn(_call("read_file")), max_steps=1, executor=Raiser())
        assert outcome.result.steps[0].ok is False
        assert "bad tool arguments" in outcome.result.steps[0].result

    def test_an_unexpected_executor_error_is_a_genuine_abort(self):
        class Raiser:
            def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
                raise OSError("disk on fire")

        turn = _turn(_call("read_file"))
        executor = Raiser()
        with pytest.raises(LoopAborted):
            _drive(turn, max_steps=1, executor=executor)

    def test_a_finish_does_not_cancel_the_rest_of_the_turn(self):
        executor = _reading_executor()
        outcome = _drive(_turn(_call("finish"), _call("read_file", path="a")), executor=executor)
        assert [name for name, _ in executor.seen] == ["finish", "read_file"]
        assert outcome.exit_reason == EXIT_FINISHED

    def test_changed_files_and_bytes_come_from_the_executor_ledger(self):
        executor = _reading_executor()
        executor.bytes_written = 42
        outcome = _drive(
            _turn(_call("write_file", path="a.py"), _call("finish")), executor=executor
        )
        assert outcome.result.changed_files == ["a.py"]
        assert outcome.result.stats.bytes_written == 42
        assert outcome.result.stats.files_changed == 1

    def test_a_ledgerless_executor_reports_nothing_rather_than_raising(self):
        outcome = _drive(_turn(_call("finish")), executor=MinimalExecutor())
        assert outcome.result.changed_files == []
        assert outcome.result.stats.bytes_written == 0

    def test_finish_carries_destination_and_announcement(self):
        executor = FakeExecutor(
            finish=ToolOutcome(
                result="done",
                finished=True,
                finish_summary="shipped",
                destination="frame-slug",
                announcement="it shipped",
            )
        )
        outcome = _drive(_turn(_call("finish")), executor=executor)
        assert outcome.result.destination == "frame-slug"
        assert outcome.result.announcement == "it shipped"

    def test_a_media_part_rides_a_follow_up_user_message(self):
        executor = FakeExecutor(
            view_media=ToolOutcome(
                result="an image", media_part={"type": "image_url", "image_url": {"url": "x"}}
            ),
            finish=ToolOutcome(result="done", finished=True, finish_summary="s"),
        )
        complete = Scripted(_turn(_call("view_media")), _turn(_call("finish")))
        run(complete, _task(), executor=executor, max_steps=4)
        follow_up = complete.calls[1][-1]
        assert follow_up["role"] == "user"
        assert follow_up["content"][1] == {"type": "image_url", "image_url": {"url": "x"}}

    def test_tool_arguments_are_serialized_as_json_on_the_wire(self):
        complete = Scripted(_turn(_call("read_file", path="a")), _turn(_call("finish")))
        run(complete, _task(), executor=_reading_executor(), max_steps=4)
        assistant = [m for m in complete.calls[1] if m.get("role") == "assistant"][0]
        assert assistant["tool_calls"][0]["function"]["arguments"] == '{"path": "a"}'

    def test_step_trace_records_index_tool_arguments_and_result(self):
        outcome = _drive(_turn(_call("read_file", path="a"), _call("finish")))
        first = outcome.result.steps[0]
        assert (first.index, first.tool, first.arguments) == (0, "read_file", {"path": "a"})
        assert first.result == "file contents"
        assert outcome.result.stats.tool_counts == {"read_file": 1, "finish": 1}


# ── 5. the initial prompt ─────────────────────────────────────────────────────


class TestInitialPrompt:
    def test_instruction_context_constraints_goal_and_acceptance(self):
        complete = Scripted(_turn(_call("finish")))
        task = _task(
            context="some context",
            constraints=["be brief"],
            goal="ship it",
            acceptance=["tests pass"],
        )
        run(complete, task, executor=_reading_executor(), max_steps=2)
        user = complete.calls[0][1]["content"]
        assert "do the thing" in user
        assert "some context" in user
        assert "- be brief" in user
        assert "ship it" in user
        assert "- tests pass" in user

    def test_no_attachments_keeps_the_first_turn_a_plain_string(self):
        complete = Scripted(_turn(_call("finish")))
        run(complete, _task(), executor=_reading_executor(), max_steps=2)
        assert isinstance(complete.calls[0][1]["content"], str)

    def test_an_unreadable_attachment_degrades_to_a_placeholder(self):
        complete = Scripted(_turn(_call("finish")))
        task = _task(attachments=[{"path": "/nope/missing.png", "media_type": "image/png"}])
        outcome = run(complete, task, executor=_reading_executor(), max_steps=2)
        parts = complete.calls[0][1]["content"]
        assert parts[0]["type"] == "text"
        assert "unreadable" in parts[1]["text"]
        assert any(d.code == "attachment-unreadable" for d in outcome.degradations)

    def test_the_system_prompt_is_the_hosts_to_supply(self):
        complete = Scripted(_turn(_call("finish")))
        run(complete, _task(), executor=_reading_executor(), max_steps=2, system_prompt="be Gwen")
        assert complete.calls[0][0] == {"role": "system", "content": "be Gwen"}

    def test_a_default_system_prompt_names_no_tool_surface(self):
        """C6: the loop must not assume a shell/file tool surface exists."""
        complete = Scripted(_turn(_call("finish")))
        run(complete, _task(), executor=_reading_executor(), max_steps=2)
        system = complete.calls[0][0]["content"]
        for token in ("run_command", "write_file", "edit_file", "shell"):
            assert token not in system


# ── 6. presence binding ───────────────────────────────────────────────────────


class TestPresenceBinding:
    def test_acknowledge_is_called_once_before_the_first_step(self):
        sink = FakeSink()
        packet = ContextPacket(original="do the thing")
        _drive(
            _turn(_call("read_file", path="a"), _call("finish")),
            task=_task(context_packet=packet),
            presence=sink,
        )
        assert sink.acknowledged == [packet]

    def test_acknowledge_precedes_every_progress_boundary(self):
        order: list[str] = []

        class Ordered(FakeSink):
            def acknowledge(self, packet):
                order.append("ack")
                return super().acknowledge(packet)

            def on_progress_boundary(self, **kw):
                order.append("boundary")
                return super().on_progress_boundary(**kw)

        _drive(_turn(_call("read_file", path="a"), _call("finish")), presence=Ordered())
        assert order[0] == "ack"
        assert order.count("ack") == 1

    def test_one_progress_boundary_per_step(self):
        sink = FakeSink()
        _drive(
            _turn(_call("read_file", path="a"), _call("read_file", path="b"), _call("finish")),
            presence=sink,
        )
        steps = [b for b in sink.boundaries if not b[1]]
        assert len(steps) == 3
        assert [s[0] for s in steps] == [1, 2, 3]

    def test_a_denied_step_still_reports_a_boundary(self):
        sink = FakeSink()
        hooks = RecordingHooks(pre_tool=[HookDecision(decision=DECISION_DENY, reason="no")])
        _drive(_turn(_call("read_file", path="a")), max_steps=1, presence=sink, hooks=hooks)
        assert [b for b in sink.boundaries if not b[1]] == [(1, False)]

    def test_operator_messages_are_routed_to_the_sink(self):
        sink = FakeSink()
        inbox = [["are you there?"], [], ["still working?"]]

        def poll() -> list[str]:
            return inbox.pop(0) if inbox else []

        _drive(_turn(_call("read_file", path="a")), max_steps=4, presence=sink, operator_inbox=poll)
        assert sink.operator == ["are you there?", "still working?"]

    def test_no_inbox_means_no_operator_routing(self):
        sink = FakeSink()
        _drive(_turn(_call("finish")), presence=sink)
        assert sink.operator == []

    def test_no_sink_and_an_inactive_sink_are_byte_identical(self):
        def build():
            return _turn(_call("read_file", path="a"), _call("finish"))

        bare = _drive(build())
        inactive = _drive(build(), presence=FakeSink(active=False))
        assert _comparable(bare) == _comparable(inactive)

    def test_an_active_sink_changes_nothing_the_host_can_observe(self):
        def build():
            return _turn(_call("read_file", path="a"), _call("finish"))

        bare = _drive(build())
        attended = _drive(build(), presence=FakeSink())
        assert _comparable(bare) == _comparable(attended)

    def test_an_inactive_sink_is_never_called(self):
        sink = FakeSink(active=False)
        _drive(_turn(_call("read_file", path="a"), _call("finish")), presence=sink)
        assert (sink.acknowledged, sink.boundaries, sink.operator) == ([], [], [])

    def test_a_raising_sink_degrades_and_never_aborts(self):
        sink = FakeSink(raises=True)
        outcome = _drive(_turn(_call("read_file", path="a"), _call("finish")), presence=sink)
        assert outcome.exit_reason == EXIT_FINISHED
        assert any(d.code == DEGRADED_PRESENCE for d in outcome.degradations)

    def test_the_real_presence_engine_satisfies_the_binding(self):
        from embodiment.presence import UpdateCadence
        from embodiment.presence_engine import PresenceEngine, PresenceIO

        spoken: list[str] = []
        engine = PresenceEngine(
            io=PresenceIO(render=spoken.append),
            cadence=UpdateCadence(every_steps=1, max_updates=10),
        )
        outcome = _drive(
            _turn(_call("read_file", path="a"), _call("finish")),
            task=_task(context_packet=ContextPacket(original="do the thing", ack="on it")),
            presence=engine,
        )
        assert outcome.exit_reason == EXIT_FINISHED
        assert spoken  # the pump actually spoke through the injected IO


def _comparable(outcome: Any) -> dict[str, Any]:
    """A result dict with the wall-clock fields blanked, for equality assertions."""
    data = outcome.result.to_dict()
    stats = dict(data["stats"])
    stats["started_at"] = ""
    stats["duration_seconds"] = 0.0
    data["stats"] = stats
    return data


# ── 7. degradation is observable (C3) ─────────────────────────────────────────


class TestDegradationIsObservable:
    def test_context_overflow_shrinks_and_retries(self):
        attempts: list[int] = []

        def flaky(messages: list[dict[str, Any]]) -> ModelResponse:
            attempts.append(len(messages))
            if len(attempts) == 1:
                raise RuntimeError("This model's maximum context length is 4096 tokens")
            return _turn(_call("finish"))

        outcome = _drive(flaky, controls=LoopControls(context_budget=200))
        assert outcome.exit_reason == EXIT_FINISHED
        assert len(attempts) == 2
        codes = [d.code for d in outcome.degradations]
        assert DEGRADED_CONTEXT_OVERFLOW in codes

    def test_an_exhausted_overflow_preserves_the_partial_and_records_it(self):
        overflow = RuntimeError("maximum context length exceeded")
        controls = LoopControls(context_budget=50, max_overflow_retries=2)
        with pytest.raises(LoopAborted) as excinfo:
            _drive(overflow, controls=controls)
        codes = [d.code for d in excinfo.value.outcome.degradations]
        assert DEGRADED_OVERFLOW_EXHAUSTED in codes
        assert excinfo.value.result.status == ERROR

    def test_overflow_retries_are_bounded(self):
        calls: list[int] = []

        def always_overflow(messages: list[dict[str, Any]]) -> ModelResponse:
            calls.append(1)
            raise RuntimeError("maximum context length is 4096 tokens")

        controls = LoopControls(context_budget=4000, max_overflow_retries=2)
        with pytest.raises(LoopAborted):
            _drive(always_overflow, controls=controls)
        assert len(calls) <= 3  # first attempt + at most two retries

    def test_a_non_degradable_error_is_never_retried(self):
        calls: list[int] = []

        def boom(messages: list[dict[str, Any]]) -> ModelResponse:
            calls.append(1)
            raise RuntimeError("something else entirely")

        controls = LoopControls(context_budget=4000)
        with pytest.raises(LoopAborted):
            _drive(boom, controls=controls)
        assert len(calls) == 1

    def test_media_rejection_flattens_and_retries_text_only(self):
        attempts: list[Any] = []

        def refusing(messages: list[dict[str, Any]]) -> ModelResponse:
            attempts.append(messages[1]["content"])
            if len(attempts) == 1:
                raise RuntimeError("HTTP 400: At most 0 image(s) may be provided in one prompt")
            return _turn(_call("finish"))

        png = Path(__file__).resolve().parents[1] / "tests" / "_fixture.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        try:
            task = _task(attachments=[{"path": str(png), "media_type": "image/png"}])
            outcome = run(refusing, task, executor=_reading_executor(), max_steps=4)
        finally:
            png.unlink()
        assert len(attempts) == 2
        assert isinstance(attempts[1], str)  # flattened
        assert any(d.code == DEGRADED_MEDIA_REJECTED for d in outcome.degradations)
        assert outcome.result.media == {"attachments": [{"path": str(png), "status": "dropped"}]}

    def test_a_raising_progress_sink_is_recorded_not_fatal(self):
        def boom(*args: Any) -> None:
            raise RuntimeError("sink down")

        outcome = _drive(_turn(_call("finish")), progress=boom)
        assert outcome.exit_reason == EXIT_FINISHED
        assert any(d.code == DEGRADED_PROGRESS for d in outcome.degradations)

    def test_a_raising_observer_is_recorded_once_then_left_alone(self):
        seen: list[Any] = []

        def boom(event: Any) -> None:
            seen.append(event)
            raise RuntimeError("observer down")

        outcome = _drive(_turn(_call("read_file", path="a"), _call("finish")), observer=boom)
        assert outcome.exit_reason == EXIT_FINISHED
        assert len(seen) == 1  # disabled after the first failure
        assert [d.code for d in outcome.degradations].count(DEGRADED_OBSERVER) == 1

    def test_every_degradation_reaches_the_observer(self):
        events: list[Any] = []
        hooks = RecordingHooks(pre_tool=lambda e: (_ for _ in ()).throw(RuntimeError("no")))
        outcome = _drive(
            _turn(_call("read_file", path="a")), max_steps=1, hooks=hooks, observer=events.append
        )
        degradations = [e for e in events if e.kind == "degradation"]
        assert len(degradations) == len(outcome.degradations)

    def test_progress_receives_raw_arguments_not_a_rendered_label(self):
        seen: list[tuple[Any, ...]] = []
        _drive(
            _turn(_call("read_file", path="a"), _call("finish")), progress=lambda *a: seen.append(a)
        )
        steps = [s for s in seen if s[1]]
        assert steps[0] == (0, "read_file", {"path": "a"}, True)

    def test_a_phase_notice_uses_the_empty_tool_sentinel(self):
        seen: list[tuple[Any, ...]] = []
        _drive(_turn(_call("finish")), progress=lambda *a: seen.append(a))
        phases = [s for s in seen if s[1] == ""]
        assert phases and isinstance(phases[0][2], str)


# ── 8. result shaping ─────────────────────────────────────────────────────────


class TestResultShaping:
    def test_usage_and_turn_accounting(self):
        complete = Scripted(
            _turn(
                _call("read_file", path="a"),
                prompt_tokens=10,
                completion_tokens=4,
                reasoning="thinking",
                content="reading",
            ),
            _turn(_call("finish"), prompt_tokens=20, completion_tokens=6),
        )
        outcome = run(complete, _task(), executor=_reading_executor(), max_steps=5)
        assert outcome.result.usage.prompt_tokens == 30
        assert outcome.result.usage.completion_tokens == 10
        assert outcome.result.stats.model_turns == 2
        assert outcome.result.stats.reasoning_chars == len("thinking")
        assert outcome.result.stats.answer_chars == len("reading")

    def test_stats_are_finalized_on_every_exit_path(self):
        for responses, steps in (
            ((_turn(_call("finish")),), 5),
            ((_turn(content="prose"),), 5),
            ((_turn(_call("read_file", path="a")),), 2),
        ):
            outcome = _drive(*responses, max_steps=steps)
            assert outcome.result.stats.request == "do the thing"
            assert outcome.result.stats.started_at
            assert outcome.result.stats.duration_seconds >= 0

    def test_model_id_is_recorded_when_threaded(self):
        outcome = _drive(_turn(_call("finish")), model="gwen-cortex")
        assert outcome.result.stats.model == "gwen-cortex"

    def test_summary_falls_back_to_last_substantive_content(self):
        outcome = _drive(
            _turn(_call("read_file", path="a"), content="I found the bug in a.py"),
            max_steps=1,
            controls=LoopControls(synthesis=False),
        )
        assert outcome.result.summary == "I found the bug in a.py"

    def test_summary_falls_back_to_the_sentinel_when_nothing_was_produced(self):
        outcome = _drive(
            _turn(_call("read_file", path="a")),
            max_steps=1,
            controls=LoopControls(synthesis=False),
        )
        assert outcome.result.summary == NO_RESULT_PRODUCED

    def test_forced_synthesis_turns_a_budget_exhaustion_into_a_partial(self):
        turns = [_turn(_call("read_file", path="a")), _turn(content="here is what I found")]
        complete = Scripted(*turns)
        outcome = run(complete, _task(), executor=_reading_executor(), max_steps=2)
        assert outcome.result.summary == "here is what I found"
        assert complete.turns == 2  # one reading turn + the reserved synthesis turn

    def test_synthesis_never_runs_when_nothing_was_read(self):
        complete = Scripted(_turn(content=""))
        run(complete, _task(), executor=_reading_executor(), max_steps=1)
        assert complete.turns == 1

    def test_an_empty_finish_summary_is_synthesized(self):
        executor = FakeExecutor(
            read_file=ToolOutcome(result="contents"),
            finish=ToolOutcome(result="done", finished=True, finish_summary=""),
        )
        complete = Scripted(
            _turn(_call("read_file", path="a")),
            _turn(_call("finish")),
            _turn(content="the actual answer"),
        )
        outcome = run(complete, _task(), executor=executor, max_steps=5)
        assert outcome.result.summary == "the actual answer"

    def test_a_failing_synthesis_turn_degrades_rather_than_raising(self):
        complete = Scripted(_turn(_call("read_file", path="a")), RuntimeError("dead"))
        outcome = run(complete, _task(), executor=_reading_executor(), max_steps=2)
        assert outcome.exit_reason == EXIT_BUDGET
        assert any(d.code == "synthesis-failed" for d in outcome.degradations)

    def test_literal_finish_markup_is_recovered(self):
        content = (
            "<tool_call>\nfunction=finish>\n<parameter=summary>\n"
            "The whole report lives here.\n</parameter>\n</function>\n</tool_call>"
        )
        outcome = _drive(_turn(content=content), max_steps=3)
        assert outcome.exit_reason == EXIT_FINISHED
        assert outcome.result.summary == "The whole report lives here."
        assert outcome.result.finish_recovered == "literal-markup"

    def test_ordinary_prose_is_never_mistaken_for_a_finish(self):
        outcome = _drive(_turn(content="I will now read the file."), max_steps=3)
        assert outcome.exit_reason == EXIT_STOPPED
        assert outcome.result.finish_recovered is None

    def test_incompletion_is_flagged_on_a_no_deliverable_run(self):
        outcome = _drive(
            _turn(_call("read_file", path="a")),
            max_steps=1,
            controls=LoopControls(synthesis=False),
        )
        assert outcome.result.incompletion is not None
        assert outcome.result.incompletion.reason == "budget-exhausted"
        assert outcome.result.status == INCOMPLETE

    def test_a_clean_finish_carries_no_incompletion_record(self):
        outcome = _drive(_turn(_call("write_file", path="a.py"), _call("finish")))
        assert outcome.result.incompletion is None
        assert outcome.result.status == OK

    def test_incompletion_can_be_switched_off(self):
        outcome = _drive(
            _turn(_call("read_file", path="a")),
            max_steps=1,
            controls=LoopControls(synthesis=False, incompletion=False),
        )
        assert outcome.result.incompletion is None

    def test_a_read_intent_run_delivering_prose_is_complete(self):
        outcome = _drive(
            _turn(_call("read_file", path="a"), content="the findings"),
            max_steps=1,
            controls=LoopControls(synthesis=False, write_intent=False),
        )
        assert outcome.result.incompletion is None

    def test_zero_steps_is_its_own_incompletion_reason(self):
        outcome = _drive(_turn(content=""), max_steps=2, controls=LoopControls(synthesis=False))
        assert outcome.result.incompletion is not None
        assert outcome.result.incompletion.reason == "no-progress-zero-steps"

    def test_the_result_round_trips_through_json(self):
        from embodiment.contract import TaskResult

        outcome = _drive(_turn(_call("read_file", path="a"), _call("finish")))
        assert TaskResult.from_dict(outcome.result.to_dict()) == outcome.result


# ── 9. context windowing ──────────────────────────────────────────────────────


class TestContextWindowing:
    def test_no_budget_means_no_windowing(self):
        complete = Scripted(*[_turn(_call("read_file", path=f"f{i}")) for i in range(4)])
        run(complete, _task(), executor=_reading_executor(), max_steps=4)
        assert len(complete.calls[-1]) > len(complete.calls[0])

    def test_a_budget_trims_the_running_history(self):
        long_result = "x" * 4000
        executor = FakeExecutor(read_file=ToolOutcome(result=long_result))
        complete = Scripted(*[_turn(_call("read_file", path=f"f{i}")) for i in range(6)])
        run(
            complete,
            _task(),
            executor=executor,
            max_steps=6,
            controls=LoopControls(context_budget=500),
        )
        assert len(complete.calls[-1]) < 13

    def test_a_custom_token_counter_is_used(self):
        counted: list[int] = []

        def counter(messages: list[dict[str, Any]]) -> int:
            counted.append(len(messages))
            return len(messages)

        _drive(
            _turn(_call("finish")),
            controls=LoopControls(context_budget=100, count_tokens=counter),
        )
        assert counted


# ── 10. continuity seam (task t14's injection point) ──────────────────────────


class TestContinuitySeam:
    def test_the_three_boundaries_are_offered_in_order(self):
        seen: list[str] = []
        _drive(
            _turn(_call("read_file", path="a"), _call("finish")),
            continuity=lambda b: seen.append(b.name),
        )
        assert seen[0] == "before-action"
        assert seen[-1] == "before-memory"
        assert "before-completion" in seen

    def test_before_action_names_the_tool_about_to_run(self):
        seen: list[Boundary] = []
        _drive(_turn(_call("read_file", path="a")), max_steps=1, continuity=seen.append)
        action = [b for b in seen if b.name == "before-action"][0]
        assert action.tool == "read_file" and action.arguments == {"path": "a"}

    def test_the_seam_cannot_alter_control_flow(self):
        executor = _reading_executor()
        _drive(
            _turn(_call("read_file", path="a"), _call("finish")),
            executor=executor,
            continuity=lambda b: "deny",  # a return value the loop must ignore
        )
        assert [name for name, _ in executor.seen] == ["read_file", "finish"]

    def test_a_raising_seam_degrades_and_never_aborts(self):
        def boom(boundary: Boundary) -> None:
            raise RuntimeError("eidetic down")

        outcome = _drive(_turn(_call("finish")), continuity=boom)
        assert outcome.exit_reason == EXIT_FINISHED
        assert any(d.code == "continuity-failed" for d in outcome.degradations)

    def test_no_seam_is_byte_identical(self):
        def build():
            return _turn(_call("read_file", path="a"), _call("finish"))

        assert _comparable(_drive(build())) == _comparable(
            _drive(build(), continuity=lambda b: None)
        )


# ── 11. seam robustness (the C3 claims, exercised) ────────────────────────────


class TestSeamRobustness:
    """Every "never raises into the host" claim above, driven at least once."""

    def test_hook_event_payload_is_json_ready(self):
        from embodiment.loop import HookEvent

        payload = HookEvent(
            event=EVENT_PRE_TOOL, task=_task(), tool="read_file", arguments={"path": "a"}
        ).payload()
        assert json.loads(json.dumps(payload))["tool"] == "read_file"
        assert payload["task_id"] == "t1" and payload["repo_path"] == "/repo"

    def test_firing_and_degradation_records_serialize(self):
        hooks = RecordingHooks(pre_tool=[HookDecision(decision=DECISION_DENY, reason="no")])
        outcome = _drive(
            _turn(_call("read_file", path="a")), max_steps=1, hooks=hooks, progress=_boom_sink
        )
        assert outcome.hook_firings[0].to_dict()["decision"] == DECISION_DENY
        assert outcome.degradations[0].to_dict()["code"] == DEGRADED_PROGRESS

    @pytest.mark.parametrize("reply", ["deny", b"deny", {"decision": "deny"}, 7, object()])
    def test_a_junk_hook_reply_is_ignored_rather_than_trusted(self, reply):
        executor = _reading_executor()
        hooks = RecordingHooks(pre_tool=reply)
        _drive(_turn(_call("read_file", path="a")), max_steps=1, hooks=hooks, executor=executor)
        assert executor.seen == [("read_file", {"path": "a"})]

    def test_non_decision_items_in_a_sequence_are_dropped(self):
        hooks = RecordingHooks(
            pre_tool=["junk", HookDecision(decision=DECISION_DENY, reason="real", source="s")]
        )
        outcome = _drive(_turn(_call("read_file", path="a")), max_steps=1, hooks=hooks)
        assert [f.source for f in outcome.hook_firings] == ["s"]

    def test_a_sink_whose_active_probe_raises_disarms_the_lane(self):
        class Hostile(FakeSink):
            @property
            def active(self) -> bool:
                raise RuntimeError("no idea")

        sink = Hostile()
        outcome = _drive(_turn(_call("finish")), presence=sink)
        assert outcome.exit_reason == EXIT_FINISHED
        assert sink.acknowledged == []
        assert any(d.code == DEGRADED_PRESENCE for d in outcome.degradations)

    def test_a_raising_operator_inbox_degrades(self):
        def poll():
            raise RuntimeError("mic down")

        outcome = _drive(_turn(_call("finish")), presence=FakeSink(), operator_inbox=poll)
        assert outcome.exit_reason == EXIT_FINISHED
        assert any("operator inbox" in d.reason for d in outcome.degradations)

    def test_a_sink_that_chokes_on_an_operator_message_degrades(self):
        class Choker(FakeSink):
            def on_operator_message(self, text: str):
                raise RuntimeError("lost the thread")

        sink = Choker()
        outcome = _drive(_turn(_call("finish")), presence=sink, operator_inbox=lambda: ["hello?"])
        assert outcome.exit_reason == EXIT_FINISHED
        assert any(d.code == DEGRADED_PRESENCE for d in outcome.degradations)

    def test_an_inbox_without_a_sink_still_drains(self):
        drained: list[int] = []

        def poll():
            drained.append(1)
            return ["hello"]

        events: list[Any] = []
        _drive(_turn(_call("finish")), operator_inbox=poll, observer=events.append)
        assert drained
        assert any(e.kind == "operator" for e in events)

    def test_string_arguments_pass_through_to_the_wire_unchanged(self):
        from embodiment.loop import _arguments_json

        ctx = _work_ctx()
        assert _arguments_json(ctx, "read_file", '{"path": "a"}') == '{"path": "a"}'
        assert _arguments_json(ctx, "read_file", {"path": "a"}) == '{"path": "a"}'
        assert ctx.degradations == []

    def test_unserializable_tool_call_arguments_are_coerced_to_valid_json_and_degrade(self):
        """``ToolCall.arguments`` is typed ``dict[str, Any]``: nothing upstream
        constrains its VALUES to JSON primitives, so a seam is free to hand back
        a bare ``Path``. ``json.dumps`` would raise ``TypeError`` on it; the loop
        must coerce and record, never abort (constraint C3)."""
        from embodiment.loop import _arguments_json

        ctx = _work_ctx()
        result = _arguments_json(ctx, "read_file", {"path": Path("/nope/missing")})
        parsed = json.loads(result)  # still valid, replayable JSON
        assert parsed["path"] == str(Path("/nope/missing"))
        assert [d.code for d in ctx.degradations] == [DEGRADED_TOOL_ARGUMENTS]
        assert "read_file" in ctx.degradations[0].reason

    def test_a_tool_call_argument_hostile_to_both_json_and_str_never_raises(self):
        """The ``default=str`` fallback itself calls arbitrary ``__str__`` — an
        adversarial value can make THAT raise too. The last-resort branch names
        only the tool and the value's type, a call that cannot itself fail."""
        from embodiment.loop import _arguments_json

        class Hostile:
            def __str__(self) -> str:  # pragma: no cover - exercised via json.dumps
                raise RuntimeError("even str() refuses")

        ctx = _work_ctx()
        result = _arguments_json(ctx, "read_file", {"weird": Hostile()})
        parsed = json.loads(result)
        assert "_unserializable_arguments" in parsed
        assert "read_file" in parsed["_unserializable_arguments"]
        assert [d.code for d in ctx.degradations] == [DEGRADED_TOOL_ARGUMENTS]

    def test_a_drive_with_unserializable_tool_call_arguments_still_finishes(self):
        """End to end: a seam that hands back a non-JSON-serializable argument
        value must not abort the drive — the assistant turn replays with the
        value coerced to a string, and the loop reaches its normal finish."""
        complete = Scripted(
            _turn(_call("read_file", path=Path("/nope"))),
            _turn(_call("finish")),
        )
        outcome = run(complete, _task(), executor=_reading_executor(), max_steps=4)
        assert outcome.exit_reason == EXIT_FINISHED
        replayed = complete.calls[1]
        assistant = next(m for m in replayed if m["role"] == "assistant")
        parsed = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
        assert parsed["path"] == str(Path("/nope"))
        assert any(d.code == DEGRADED_TOOL_ARGUMENTS for d in outcome.degradations)

    @pytest.mark.parametrize(
        "content",
        [
            "function=finish> but no parameter block",
            "function=finish><parameter=summary>unterminated",
            "function=finish><parameter=summary>   </parameter>",
        ],
    )
    def test_incomplete_finish_markup_is_not_a_finish(self, content):
        outcome = _drive(_turn(content=content), max_steps=3)
        assert outcome.exit_reason == EXIT_STOPPED

    def test_the_window_floor_is_recorded_and_gives_up(self):
        overflow = RuntimeError("maximum context length exceeded")
        controls = LoopControls(context_budget=1, max_overflow_retries=5)
        with pytest.raises(LoopAborted) as excinfo:
            _drive(overflow, controls=controls)
        reasons = [d.reason for d in excinfo.value.outcome.degradations]
        assert any("floor" in r for r in reasons)

    def test_a_media_refusal_with_nothing_to_flatten_is_not_retried(self):
        refusal = RuntimeError("HTTP 400: At most 0 image(s) may be provided")
        with pytest.raises(LoopAborted) as excinfo:
            _drive(refusal)
        assert not [
            d for d in excinfo.value.outcome.degradations if d.code == DEGRADED_MEDIA_REJECTED
        ]

    def test_an_unreadable_bytes_written_ledger_degrades(self):
        executor = _reading_executor()
        executor.bytes_written = "not a number"  # type: ignore[assignment]
        outcome = _drive(_turn(_call("finish")), executor=executor)
        assert outcome.result.stats.bytes_written == 0
        assert any("bytes_written" in d.reason for d in outcome.degradations)

    def test_an_unreadable_changed_ledger_degrades(self):
        executor = _reading_executor()
        executor.changed = 7  # type: ignore[assignment]
        outcome = _drive(_turn(_call("finish")), executor=executor)
        assert outcome.result.changed_files == []
        assert any("changed" in d.reason for d in outcome.degradations)

    def test_sub_results_are_snapshotted_when_the_executor_keeps_them(self):
        from embodiment.contract import SubResult

        child = SubResult(task_id="c1", engine="mock", model="m", status=OK, summary="did it")
        executor = _reading_executor()
        executor.sub_results = [child]  # type: ignore[attr-defined]
        outcome = _drive(_turn(_call("finish")), executor=executor)
        assert outcome.result.sub_results == [child]

    def test_a_meta_finish_with_no_changes_is_write_no_changes(self):
        executor = FakeExecutor(
            read_file=ToolOutcome(result="contents"),
            finish=ToolOutcome(
                result="done", finished=True, finish_summary="I will implement this next."
            ),
        )
        outcome = _drive(_turn(_call("read_file", path="a"), _call("finish")), executor=executor)
        assert outcome.result.incompletion is not None
        assert outcome.result.incompletion.reason == "write-no-changes"

    def test_a_read_intent_stop_with_no_prose_is_an_empty_deliverable(self):
        complete = Scripted(_turn(_call("read_file", path="a")), _turn(), _turn())
        outcome = run(
            complete,
            _task(),
            executor=_reading_executor(),
            max_steps=4,
            controls=LoopControls(synthesis=False, write_intent=False),
        )
        assert outcome.exit_reason == EXIT_STOPPED
        assert outcome.result.incompletion is not None
        assert outcome.result.incompletion.reason == "empty-deliverable"


def _boom_sink(*args: Any) -> None:
    raise RuntimeError("sink down")


def _never_finishes(_messages):
    return _turn(_call("noop"))


class TestAbortIsReportedHonestly:
    """An abort must not masquerade as a budget exhaustion.

    Found by an independent review (`ask-colleague review`) of this branch:
    `outcome` defaulted to EXIT_BUDGET before the try, so a seam raising three
    steps into a twenty-step drive reported exit_reason="budget" — the loop
    claiming it had spent a budget it had barely touched.
    """

    def test_a_raising_seam_reports_aborted_not_budget(self):
        def explode(_messages):
            raise RuntimeError("the endpoint died")

        task = _task()
        executor = FakeExecutor()
        with pytest.raises(LoopAborted) as caught:
            run(explode, task, executor=executor, max_steps=20)
        assert caught.value.outcome.exit_reason == EXIT_ABORTED
        assert caught.value.outcome.exit_reason != EXIT_BUDGET

    def test_a_genuine_budget_exit_still_says_budget(self):
        """The fix must not make every partial look like an abort."""
        outcome = run(_never_finishes, _task(), executor=FakeExecutor(), max_steps=2)
        assert outcome.exit_reason == EXIT_BUDGET

    def test_the_partial_work_still_survives_an_abort(self):
        calls = iter([_turn(_call("noop")), _turn(_call("noop"))])

        def one_then_die(_messages):
            try:
                return next(calls)
            except StopIteration:
                raise RuntimeError("died mid-drive") from None

        task = _task()
        executor = FakeExecutor()
        with pytest.raises(LoopAborted) as caught:
            run(one_then_die, task, executor=executor, max_steps=20)
        assert caught.value.outcome.result.steps, "partial work was lost"
