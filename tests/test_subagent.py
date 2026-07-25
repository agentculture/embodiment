"""Tests for :mod:`embodiment.subagent` — delegation bounded by arithmetic (task t9).

Four things have to be proved, and the first is the one the plan calls the
hardest part of the lane:

1. **The depth bound is structural.** Recursion terminates because
   :func:`~embodiment.subagent.attenuate` strictly decrements and a drive at
   :data:`~embodiment.subagent.NO_SPAWNS` cannot spawn — provable by reading the
   source, exactly as the loop's three exits are. The AST reads below pin that
   the seam module contains **one** arithmetic operation (a subtraction of the
   literal ``1``), calls ``max`` nowhere, clamps only with ``min``, has no
   augmented assignment, and constructs a
   :class:`~embodiment.subagent.SubagentCall` in exactly one place in the whole
   package. The closed form ``2**a - 1`` is then checked against both a
   simulation of that rule and a live, maximally greedy tree of real drives.
2. **Budgets compose.** ``max_steps`` bounds the parent's model turns AND every
   turn its descendants report, so a subagent is not a fourth way past a stated
   bound. Counted across a whole tree with one shared counter.
3. **Attenuation only, never widening.** A child's allowance is strictly below
   its parent's, a request at or above the parent's is refused, a step request
   is clamped down, and the child's task/executor are the objects the parent
   built — passed through untouched, never widened.
4. **A drive that spawns nothing is byte-identical to today.** Pinned by a
   golden transcript captured from the pre-t9 loop:
   ``tests/goldens/spawn_free_drive.json``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import OK, ContextPacket, ModelResponse, SubResult, Task, ToolCall
from embodiment.loop import (
    DECISION_ALLOW,
    DECISION_REWRITE,
    DEGRADED_SPAWN_FAILED,
    DEGRADED_SPAWN_UNAVAILABLE,
    EXIT_BUDGET,
    EXIT_FINISHED,
    HookDecision,
    LoopControls,
    ToolOutcome,
    UnknownToolError,
    run,
)
from embodiment.subagent import (
    NO_SPAWNS,
    SPAWN_FAILED,
    SPAWN_GRANTED,
    SPAWN_OUTCOMES,
    SPAWN_REFUSALS,
    SPAWN_REFUSED_ALLOWANCE,
    SPAWN_REFUSED_BUDGET,
    SPAWN_REFUSED_SEAM,
    SpawnRecord,
    SpawnRequest,
    SubagentCall,
    SubagentResult,
    as_count,
    attenuate,
    child_call,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SEAM_SRC = _REPO_ROOT / "embodiment" / "subagent.py"
_LOOP_SRC = _REPO_ROOT / "embodiment" / "loop.py"
_GOLDEN_PATH = _REPO_ROOT / "tests" / "goldens" / "spawn_free_drive.json"
_PACKAGE = _REPO_ROOT / "embodiment"


# ── doubles ───────────────────────────────────────────────────────────────────


def _task(**kw: Any) -> Task:
    fields = {"id": "t1", "repo_path": "/repo", "instruction": "do the thing"}
    fields.update(kw)
    return Task(**fields)


def _call(name: str = "delegate", cid: str = "c1", **arguments: Any) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "", **kw: Any) -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls), **kw)


class Scripted:
    """A ``complete`` seam replaying fixed turns, then repeating the last."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        item = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def turns(self) -> int:
        return len(self.calls)


class ChildExecutor:
    """A deliberately NARROWER tool surface than its parent: no ``delegate``."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append(name)
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="child done")
        if name == "read_note":
            return ToolOutcome(result="a note")
        raise UnknownToolError(f"unknown tool: {name}")


class DelegatingExecutor:
    """A parent tool surface whose ``delegate`` verb asks the loop for a child."""

    def __init__(self, **request_kw: Any) -> None:
        self.request_kw = request_kw
        self.requests: list[SpawnRequest] = []
        self.children: list[ChildExecutor] = []
        self.seen: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append(name)
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="parent done")
        if name != "delegate":
            return ToolOutcome(result=f"{name} ok")
        child_executor = ChildExecutor()
        self.children.append(child_executor)
        request = SpawnRequest(
            task=Task(
                id=f"child-{len(self.requests) + 1}",
                repo_path="/repo",
                instruction=arguments.get("instruction", "the sub-task"),
            ),
            executor=child_executor,
            **self.request_kw,
        )
        self.requests.append(request)
        return ToolOutcome(result="delegating", spawn=request)


class RecordingSeam:
    """A ``SubagentFn`` that records the calls it got and replies from a script."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[SubagentCall] = []

    def __call__(self, call: SubagentCall) -> Any:
        self.calls.append(call)
        item = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(call)
        return item


def _sub(task_id: str = "child-1", **kw: Any) -> SubResult:
    fields: dict[str, Any] = {
        "task_id": task_id,
        "engine": "mock",
        "model": "m",
        "status": OK,
        "summary": "the child's answer",
    }
    fields.update(kw)
    return SubResult(**fields)


def _drive(*responses: Any, **kw: Any) -> Any:
    kw.setdefault("executor", DelegatingExecutor())
    kw.setdefault("max_steps", 8)
    task = kw.pop("task", None) or _task()
    return run(Scripted(*responses), task, **kw)


# ── 1. the allowance is arithmetic ────────────────────────────────────────────


class TestTheAllowanceArithmetic:
    """``attenuate`` is the depth bound; nothing else may produce an allowance."""

    @pytest.mark.parametrize("allowance", list(range(0, 12)))
    def test_a_child_is_always_strictly_below_its_parent(self, allowance: int) -> None:
        if allowance > NO_SPAWNS:
            assert attenuate(allowance) < allowance
        else:
            assert attenuate(allowance) == NO_SPAWNS

    def test_zero_is_the_fixed_point(self) -> None:
        assert attenuate(NO_SPAWNS) == NO_SPAWNS
        assert attenuate(attenuate(NO_SPAWNS)) == NO_SPAWNS

    @pytest.mark.parametrize("allowance", [0, 1, 2, 5, 9, 40])
    def test_the_chain_reaches_zero_in_at_most_allowance_generations(self, allowance: int) -> None:
        """THE termination argument, as a number rather than a promise."""
        remaining = allowance
        generations = 0
        while remaining > NO_SPAWNS:
            remaining = attenuate(remaining)
            generations += 1
            assert generations <= allowance, "the decrement failed to terminate"
        assert generations == allowance

    @pytest.mark.parametrize(
        "value,expected",
        [(0, 0), (3, 3), (-1, 0), (-99, 0), (None, 0), ("nope", 0), ("4", 4), (2.9, 2)],
    )
    def test_a_malformed_allowance_degrades_toward_no_delegation(
        self, value: Any, expected: int
    ) -> None:
        """The safe direction: unreadable means none, never unbounded."""
        assert as_count(value) == expected


def _simulate(allowance: int) -> tuple[int, int]:
    """Descendants and depth of a maximally greedy tree, under the mint rule.

    Mirrors ``_delegate`` exactly: while the allowance is positive, mint a child
    at ``attenuate(allowance)`` and decrement the parent by the same function.
    """
    remaining = as_count(allowance)
    descendants = 0
    depth = 0
    while remaining > NO_SPAWNS:
        child = attenuate(remaining)
        remaining = attenuate(remaining)
        sub_descendants, sub_depth = _simulate(child)
        descendants += 1 + sub_descendants
        depth = max(depth, 1 + sub_depth)
    return descendants, depth


class TestTheSubtreeIsBoundedNotJustTheNextGeneration:
    """The allowance bounds the WHOLE subtree — the claim c47 actually makes."""

    @pytest.mark.parametrize("allowance", [0, 1, 2, 3, 4, 5, 6])
    def test_the_greedy_tree_matches_the_closed_form(self, allowance: int) -> None:
        descendants, depth = _simulate(allowance)
        assert descendants == 2**allowance - 1
        assert depth == allowance

    def test_the_bound_is_fixed_before_the_first_spawn_happens(self) -> None:
        """A finite number, knowable from the root's declaration alone."""
        assert [_simulate(a)[0] for a in range(7)] == [0, 1, 3, 7, 15, 31, 63]


# ── 2. no widening, anywhere ──────────────────────────────────────────────────


class TestAttenuationOnly:
    """A child's reach only ever shrinks relative to its parent's."""

    def _child(self, **request_kw: Any) -> SubagentCall:
        request = SpawnRequest(task=_task(id="child"), executor=ChildExecutor(), **request_kw)
        return child_call(
            request,
            parent_allowance=3,
            parent_task_id="p",
            parent_lineage=("root",),
            turns_available=10,
        )

    def test_the_default_child_allowance_is_one_below_the_parent(self) -> None:
        assert self._child().allowance == 2

    @pytest.mark.parametrize("requested", [3, 4, 99, 10**6])
    def test_a_requested_allowance_at_or_above_the_parents_is_refused(self, requested: int) -> None:
        """The acceptance criterion, stated as the seam's own arithmetic."""
        child = self._child(allowance=requested)
        assert child.allowance == 2
        assert child.allowance < 3

    def test_a_requested_allowance_below_the_ceiling_is_honoured(self) -> None:
        """Narrowing further is the one thing a request may do."""
        assert self._child(allowance=0).allowance == NO_SPAWNS
        assert self._child(allowance=1).allowance == 1

    @pytest.mark.parametrize("requested", [11, 50, None])
    def test_a_step_request_can_never_exceed_what_the_parent_has_left(
        self, requested: Optional[int]
    ) -> None:
        assert self._child(max_steps=requested).max_steps == 10

    def test_a_step_request_below_what_remains_is_honoured(self) -> None:
        assert self._child(max_steps=2).max_steps == 2

    def test_the_task_and_executor_are_passed_through_untouched(self) -> None:
        """Tool boundaries are the injected surface's job, not this seam's."""
        executor = ChildExecutor()
        task = _task(id="child")
        child = child_call(
            SpawnRequest(task=task, executor=executor),
            parent_allowance=2,
            parent_task_id="p",
            parent_lineage=(),
            turns_available=4,
        )
        assert child.task is task
        assert child.executor is executor

    def test_lineage_is_the_depth_and_names_every_ancestor(self) -> None:
        child = child_call(
            SpawnRequest(task=_task(id="c"), executor=ChildExecutor()),
            parent_allowance=2,
            parent_task_id="p",
            parent_lineage=("root", "mid"),
            turns_available=4,
        )
        assert child.lineage == ("root", "mid", "p")
        assert child.depth == 3
        assert child.parent_task_id == "p"

    def test_a_root_call_has_no_parent(self) -> None:
        child = child_call(
            SpawnRequest(task=_task(id="c"), executor=ChildExecutor()),
            parent_allowance=1,
            parent_task_id="",
            parent_lineage=(),
            turns_available=1,
        )
        assert child.depth == 1 and child.parent_task_id == ""


# ── 3. the structural proof (AST) ─────────────────────────────────────────────


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


class TestTheDepthBoundIsStructural:
    """Proved by reading the source, the same way the three exits are."""

    def test_the_seam_contains_exactly_one_arithmetic_operation(self) -> None:
        """One subtraction of the literal 1 — and therefore no way to grow."""
        ops = [n for n in ast.walk(_tree(_SEAM_SRC)) if isinstance(n, ast.BinOp)]
        assert len(ops) == 1, [ast.dump(op) for op in ops]
        assert isinstance(ops[0].op, ast.Sub), ast.dump(ops[0])
        assert isinstance(ops[0].right, ast.Constant) and ops[0].right.value == 1

    def test_that_one_operation_lives_inside_attenuate(self) -> None:
        inside = [
            n
            for n in ast.walk(_function(_tree(_SEAM_SRC), "attenuate"))
            if isinstance(n, ast.BinOp)
        ]
        assert len(inside) == 1

    def test_the_seam_has_no_augmented_assignment(self) -> None:
        """``allowance += 1`` cannot be written here; there is no ``+=`` at all."""
        assert not [n for n in ast.walk(_tree(_SEAM_SRC)) if isinstance(n, ast.AugAssign)]

    def test_the_seam_never_calls_max(self) -> None:
        """``max`` is how a number grows past a ceiling. It is not called here."""
        assert not _calls_named(_tree(_SEAM_SRC), "max")

    def test_every_clamp_in_the_seam_is_a_min(self) -> None:
        tree = _tree(_SEAM_SRC)
        mins = _calls_named(tree, "min")
        assert len(mins) == 1
        assert _calls_named(_function(tree, "_narrow"), "min") == mins

    def test_a_subagent_call_is_constructed_in_exactly_one_place_in_the_package(self) -> None:
        """The only object that carries a child's reach has one producer."""
        found: list[str] = []
        for path in sorted(_PACKAGE.rglob("*.py")):
            for node in _calls_named(_tree(path), "SubagentCall"):
                found.append(f"{path.name}:{node.lineno}")
        assert len(found) == 1, found
        assert found[0].startswith("subagent.py")

    def test_that_construction_takes_its_allowance_from_the_decrement(self) -> None:
        """``allowance=_narrow(request.allowance, attenuate(parent_allowance))``."""
        tree = _tree(_SEAM_SRC)
        construction = _calls_named(_function(tree, "child_call"), "SubagentCall")[0]
        by_name = {kw.arg: kw.value for kw in construction.keywords}
        allowance = by_name["allowance"]
        assert isinstance(allowance, ast.Call) and allowance.func.id == "_narrow"
        assert _calls_named(allowance, "attenuate"), ast.dump(allowance)

    # loop.py's half of the same bound — that it computes no allowance of its
    # own, never augments one, and checks the zero gate before minting — is
    # pinned beside the rest of loop.py's AST reads, in
    # ``tests/test_loop.py::TestTheDepthBoundIsPinnedInTheLoopToo``.

    def test_the_seam_imports_only_stdlib_and_the_contract(self) -> None:
        import sys

        modules: set[str] = set()
        for node in ast.walk(_tree(_SEAM_SRC)):
            if isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules <= {"__future__", "dataclasses", "typing", "embodiment.contract"}, modules
        for module in modules:
            top = module.split(".")[0]
            assert top == "__future__" or top in sys.stdlib_module_names or top == "embodiment"

    def test_the_guard_would_notice_a_widening_edit(self) -> None:
        """The AST reads above are only worth something if they can fail."""
        widened = ast.parse("def attenuate(a):\n    return a + 1\n")
        ops = [n for n in ast.walk(widened) if isinstance(n, ast.BinOp)]
        assert ops and not isinstance(ops[0].op, ast.Sub)
        assert _calls_named(ast.parse("x = max(a, b)\n"), "max")


# ── 4. budgets compose ────────────────────────────────────────────────────────


class _Rig:
    """A recursive host: its ``SubagentFn`` drives a child through the real loop.

    One shared counter counts EVERY completion anywhere in the tree, which is
    what makes "the parent's ``max_steps`` bounds total turns including
    children" a measured statement rather than an assertion about intent.
    """

    def __init__(self, *, child_turns: int = 1, greedy: bool = False) -> None:
        self.child_turns = child_turns
        self.greedy = greedy
        self.completions = 0
        self.drives: list[tuple[str, int, int]] = []  # (task id, allowance, depth)
        self.max_depth = 0

    # -- the child's own model seam ------------------------------------------
    def _complete(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.completions += 1
        last = messages[-1]
        content = last.get("content")
        text = content if isinstance(content, str) else ""
        if self.greedy and "refused-allowance" not in text:
            return _turn(_call("delegate", cid=f"c{self.completions}"))
        if len([m for m in messages if m.get("role") == "tool"]) >= self.child_turns:
            return _turn(_call("finish", cid=f"c{self.completions}"))
        return _turn(_call("read_note", cid=f"c{self.completions}"))

    # -- the injected seam ---------------------------------------------------
    def __call__(self, call: SubagentCall) -> SubagentResult:
        self.drives.append((call.task.id, call.allowance, call.depth))
        self.max_depth = max(self.max_depth, call.depth)
        executor = DelegatingExecutor() if self.greedy else call.executor
        outcome = run(
            self._complete,
            call.task,
            executor=executor,
            max_steps=call.max_steps,
            subagent=self,
            spawn_allowance=call.allowance,
            lineage=call.lineage,
            controls=LoopControls(synthesis=False, incompletion=False, write_intent=False),
        )
        return SubagentResult(
            sub_result=_sub(
                task_id=call.task.id,
                summary=outcome.result.summary,
                status=outcome.result.status,
            ),
            model_turns=outcome.result.stats.model_turns + outcome.child_model_turns,
            degradations=list(outcome.degradations),
            exit_reason=outcome.exit_reason,
        )


class TestBudgetsCompose:
    """``max_steps`` keeps meaning what it says once children exist."""

    @pytest.mark.parametrize("max_steps", [2, 3, 4, 6, 9, 14])
    def test_the_parents_budget_bounds_every_turn_in_the_tree(self, max_steps: int) -> None:
        rig = _Rig(child_turns=3)
        parent = Scripted(_turn(_call("delegate")), _turn(_call("finish", cid="cf")))
        executor = DelegatingExecutor()

        def counted(messages: list[dict[str, Any]]) -> ModelResponse:
            rig.completions += 1
            return parent(messages)

        outcome = run(
            counted,
            _task(),
            executor=executor,
            max_steps=max_steps,
            subagent=rig,
            spawn_allowance=2,
            controls=LoopControls(synthesis=False),
        )
        assert rig.completions <= max_steps, "a child bought turns the parent never had"
        spent = outcome.result.stats.model_turns + outcome.child_model_turns
        assert spent == rig.completions
        assert spent <= max_steps

    def test_a_child_is_offered_only_what_the_parent_has_left(self) -> None:
        seam = RecordingSeam(SubagentResult(model_turns=0))
        run(
            Scripted(_turn(_call("delegate")), _turn(_call("finish", cid="cf"))),
            _task(),
            executor=DelegatingExecutor(),
            max_steps=6,
            subagent=seam,
            spawn_allowance=1,
            controls=LoopControls(synthesis=False),
        )
        # 6 turns of reading budget, one already spent on the delegating turn.
        assert seam.calls[0].max_steps == 5

    def test_the_synthesis_reserve_is_never_lent_to_a_child(self) -> None:
        seam = RecordingSeam(SubagentResult(model_turns=0))
        run(
            Scripted(_turn(_call("delegate")), _turn(_call("finish", cid="cf"))),
            _task(),
            executor=DelegatingExecutor(),
            max_steps=6,
            subagent=seam,
            spawn_allowance=1,
            controls=LoopControls(synthesis=True),
        )
        # Reading budget is 5 with synthesis armed; one turn already spent.
        assert seam.calls[0].max_steps == 4

    def test_child_turns_are_reported_apart_from_the_parents_own(self) -> None:
        """The artifact never claims the parent took turns it did not take."""
        seam = RecordingSeam(SubagentResult(model_turns=4))
        outcome = run(
            Scripted(_turn(_call("delegate")), _turn(_call("finish", cid="cf"))),
            _task(),
            executor=DelegatingExecutor(),
            max_steps=12,
            subagent=seam,
            spawn_allowance=1,
            controls=LoopControls(synthesis=False),
        )
        assert outcome.result.stats.model_turns == 2
        assert outcome.child_model_turns == 4

    def test_a_child_that_eats_the_budget_ends_the_parent_on_budget(self) -> None:
        seam = RecordingSeam(SubagentResult(model_turns=99))
        outcome = run(
            Scripted(_turn(_call("delegate"))),
            _task(),
            executor=DelegatingExecutor(),
            max_steps=6,
            subagent=seam,
            spawn_allowance=1,
            controls=LoopControls(synthesis=False),
        )
        assert outcome.exit_reason == EXIT_BUDGET
        assert outcome.result.stats.model_turns == 1

    def test_an_overspending_seam_is_charged_in_full_and_recorded(self) -> None:
        """Clamping would make the bound quietly mean less than it says."""
        seam = RecordingSeam(SubagentResult(model_turns=40))
        outcome = run(
            Scripted(_turn(_call("delegate"))),
            _task(),
            executor=DelegatingExecutor(),
            max_steps=6,
            subagent=seam,
            spawn_allowance=1,
            controls=LoopControls(synthesis=False),
        )
        assert outcome.child_model_turns == 40
        assert any(d.code == DEGRADED_SPAWN_FAILED for d in outcome.degradations)
        assert any("of a 5-turn grant" in d.reason for d in outcome.degradations)

    def test_no_turns_left_refuses_the_spawn_rather_than_borrowing(self) -> None:
        outcome = run(
            Scripted(_turn(_call("delegate"))),
            _task(),
            executor=DelegatingExecutor(),
            max_steps=1,
            subagent=RecordingSeam(SubagentResult(model_turns=1)),
            spawn_allowance=3,
            controls=LoopControls(synthesis=False),
        )
        assert [r.outcome for r in outcome.spawns] == [SPAWN_REFUSED_BUDGET]
        assert outcome.child_model_turns == 0


# ── 5. the bound holds on real, recursive drives ──────────────────────────────


class TestRecursionTerminates:
    """A maximally greedy tree of REAL drives, run to completion."""

    @pytest.mark.parametrize("allowance", [0, 1, 2, 3, 4])
    def test_a_greedy_tree_matches_the_structural_bound(self, allowance: int) -> None:
        rig = _Rig(greedy=True)
        executor = DelegatingExecutor()
        outcome = run(
            rig._complete,
            _task(),
            executor=executor,
            max_steps=400,
            subagent=rig,
            spawn_allowance=allowance,
            controls=LoopControls(synthesis=False, incompletion=False, write_intent=False),
        )
        assert outcome.exit_reason == EXIT_FINISHED
        assert len(rig.drives) == 2**allowance - 1, "the subtree bound was not the closed form"
        assert rig.max_depth == allowance, "the depth bound was not the root's allowance"
        assert outcome.spawn_allowance_remaining == NO_SPAWNS or allowance == 0

    def test_the_greedy_tree_spends_what_the_ledger_says_it_spent(self) -> None:
        rig = _Rig(greedy=True)
        outcome = run(
            rig._complete,
            _task(),
            executor=DelegatingExecutor(),
            max_steps=400,
            subagent=rig,
            spawn_allowance=3,
            controls=LoopControls(synthesis=False, incompletion=False, write_intent=False),
        )
        spent = outcome.result.stats.model_turns + outcome.child_model_turns
        assert spent == rig.completions
        assert spent <= 400

    def test_a_tight_budget_bounds_the_tree_even_with_allowance_to_spare(self) -> None:
        """The two bounds are independent: either one alone terminates the tree."""
        rig = _Rig(greedy=True)
        outcome = run(
            rig._complete,
            _task(),
            executor=DelegatingExecutor(),
            max_steps=5,
            subagent=rig,
            spawn_allowance=8,
            controls=LoopControls(synthesis=False, incompletion=False, write_intent=False),
        )
        assert rig.completions <= 5
        assert outcome.result.stats.model_turns + outcome.child_model_turns <= 5


# ── 6. allowance zero blocks spawning ─────────────────────────────────────────


class TestZeroAllowanceCannotSpawn:
    """The floor of the decrement, and the default every host starts from."""

    def test_the_default_drive_may_not_delegate(self) -> None:
        seam = RecordingSeam(SubagentResult())
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            subagent=seam,
            controls=LoopControls(synthesis=False),
        )
        assert seam.calls == [], "a default drive spawned a child"
        assert [r.outcome for r in outcome.spawns] == [SPAWN_REFUSED_ALLOWANCE]

    def test_an_exhausted_allowance_stops_the_next_spawn(self) -> None:
        seam = RecordingSeam(SubagentResult(model_turns=0))
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("delegate", cid="c2")),
            _turn(_call("finish", cid="cf")),
            subagent=seam,
            spawn_allowance=1,
            max_steps=9,
            controls=LoopControls(synthesis=False),
        )
        assert [r.outcome for r in outcome.spawns] == [SPAWN_GRANTED, SPAWN_REFUSED_ALLOWANCE]
        assert len(seam.calls) == 1
        assert outcome.spawn_allowance_remaining == NO_SPAWNS

    def test_a_refusal_on_the_bound_is_not_recorded_as_a_degradation(self) -> None:
        """The design working is not a breakage; a ledger must not claim one."""
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            subagent=RecordingSeam(SubagentResult()),
            controls=LoopControls(synthesis=False),
        )
        assert outcome.spawns[0].outcome == SPAWN_REFUSED_ALLOWANCE
        assert outcome.degradations == []

    def test_the_refusal_reaches_the_acting_model_as_text(self) -> None:
        """A model whose delegation vanished would wait forever for the work."""
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            subagent=RecordingSeam(SubagentResult()),
            controls=LoopControls(synthesis=False),
        )
        step = outcome.result.steps[0]
        assert step.ok is False
        assert "refused-allowance" in step.result


# ── 7. the seam's own failure modes (C3) ──────────────────────────────────────


class TestDelegationFailsVisibly:
    """Every non-nominal delegation records a host-visible transition."""

    def _run(self, seam: Any) -> Any:
        return _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            subagent=seam,
            spawn_allowance=2,
            max_steps=9,
            controls=LoopControls(synthesis=False),
        )

    def test_a_spawn_with_no_seam_wired_degrades(self) -> None:
        outcome = self._run(None)
        assert [r.outcome for r in outcome.spawns] == [SPAWN_REFUSED_SEAM]
        assert [d.code for d in outcome.degradations] == [DEGRADED_SPAWN_UNAVAILABLE]

    def test_an_unwired_host_is_turned_away_before_anything_is_minted(self) -> None:
        """No record may claim a grant to a child that was never built."""
        outcome = self._run(None)
        record = outcome.spawns[0]
        assert record.child_task_id is None
        assert record.allowance_granted is None
        assert record.steps_granted is None
        assert "allowance_granted" not in record.to_dict()
        # ...and the allowance is intact, because no spawn happened.
        assert outcome.spawn_allowance_remaining == 2

    def test_a_seam_returning_none_degrades_rather_than_faking_a_child(self) -> None:
        outcome = self._run(RecordingSeam(None))
        assert [r.outcome for r in outcome.spawns] == [SPAWN_REFUSED_SEAM]
        assert [d.code for d in outcome.degradations] == [DEGRADED_SPAWN_UNAVAILABLE]

    def test_a_raising_seam_costs_one_step_and_never_aborts_the_drive(self) -> None:
        outcome = self._run(RecordingSeam(RuntimeError("child harness down")))
        assert outcome.exit_reason == EXIT_FINISHED
        assert [r.outcome for r in outcome.spawns] == [SPAWN_FAILED]
        assert [d.code for d in outcome.degradations] == [DEGRADED_SPAWN_FAILED]
        assert "child harness down" in outcome.result.steps[0].result

    def test_a_seam_returning_the_wrong_shape_degrades(self) -> None:
        outcome = self._run(RecordingSeam("just a string"))
        assert [r.outcome for r in outcome.spawns] == [SPAWN_FAILED]
        assert [d.code for d in outcome.degradations] == [DEGRADED_SPAWN_FAILED]

    def test_a_child_that_ran_but_carried_no_artifact_is_still_charged(self) -> None:
        outcome = self._run(RecordingSeam(SubagentResult(sub_result=None, model_turns=2)))
        assert outcome.spawns[0].outcome == SPAWN_GRANTED
        assert outcome.child_model_turns == 2
        assert outcome.result.sub_results == []

    def test_a_non_subresult_artifact_is_recorded_rather_than_stored(self) -> None:
        outcome = self._run(RecordingSeam(SubagentResult(sub_result={"nope": 1}, model_turns=1)))
        assert outcome.spawns[0].outcome == SPAWN_GRANTED
        assert outcome.result.sub_results == []
        assert [d.code for d in outcome.degradations] == [DEGRADED_SPAWN_FAILED]

    def test_a_spawn_attempt_spends_the_allowance_even_when_it_fails(self) -> None:
        """A flaky seam cannot retry its way past the bound."""
        seam = RecordingSeam(RuntimeError("down"))
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("delegate", cid="c2")),
            _turn(_call("finish", cid="cf")),
            subagent=seam,
            spawn_allowance=1,
            max_steps=9,
            controls=LoopControls(synthesis=False),
        )
        assert [r.outcome for r in outcome.spawns] == [SPAWN_FAILED, SPAWN_REFUSED_ALLOWANCE]


# ── 8. what the parent records ────────────────────────────────────────────────


class TestTheParentsAccounting:
    """``LoopOutcome.spawns`` is the loop's record of its own delegation."""

    def _granted(self, **kw: Any) -> Any:
        seam = RecordingSeam(
            SubagentResult(
                sub_result=_sub(),
                model_turns=3,
                degradations=[{"code": "child-lane", "reason": "the child's own"}],
                exit_reason=EXIT_FINISHED,
            )
        )
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            subagent=seam,
            spawn_allowance=2,
            max_steps=9,
            controls=LoopControls(synthesis=False),
            **kw,
        )
        return seam, outcome

    def test_a_granted_spawn_records_both_bounds_it_handed_over(self) -> None:
        _seam, outcome = self._granted()
        record = outcome.spawns[0]
        assert record.outcome == SPAWN_GRANTED and record.granted
        assert record.allowance_granted == 1
        assert record.steps_granted == 8
        assert record.model_turns == 3
        assert record.child_task_id == "child-1"
        assert record.parent_task_id == "t1"
        assert record.step_index == 0
        assert record.tool == "delegate"

    def test_the_child_artifact_is_stamped_with_its_parent(self) -> None:
        """``SubResult.parent`` is structural — the loop minted this child."""
        _seam, outcome = self._granted()
        assert [s.parent for s in outcome.result.sub_results] == ["t1"]
        assert [s.task_id for s in outcome.result.sub_results] == ["child-1"]

    def test_a_requested_role_reaches_the_child_and_its_artifact(self) -> None:
        executor = DelegatingExecutor(role="subagent")
        seam = RecordingSeam(SubagentResult(sub_result=_sub(), model_turns=1))
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            executor=executor,
            subagent=seam,
            spawn_allowance=2,
            max_steps=9,
            controls=LoopControls(synthesis=False),
        )
        assert seam.calls[0].role == "subagent"
        assert outcome.result.sub_results[0].role == "subagent"

    def test_the_childs_degradations_are_parked_for_the_ledger_lane(self) -> None:
        """Task t10 folds these with child attribution; t9 only carries them."""
        _seam, outcome = self._granted()
        assert outcome.spawns[0].degradations == (
            {"code": "child-lane", "reason": "the child's own"},
        )
        assert outcome.degradations == [], "a child's records must not land in the parent's lane"

    def test_a_narrowing_request_is_visible_next_to_what_was_granted(self) -> None:
        executor = DelegatingExecutor(allowance=99)
        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            executor=executor,
            subagent=RecordingSeam(SubagentResult(model_turns=1)),
            spawn_allowance=2,
            max_steps=9,
            controls=LoopControls(synthesis=False),
        )
        record = outcome.spawns[0]
        assert record.allowance_requested == 99
        assert record.allowance_granted == 1

    def test_the_record_serialises_without_fabricating_absent_numbers(self) -> None:
        refused = SpawnRecord(outcome=SPAWN_REFUSED_ALLOWANCE, tool="delegate")
        data = refused.to_dict()
        assert "allowance_granted" not in data
        assert data["model_turns"] == 0
        granted = SpawnRecord(outcome=SPAWN_GRANTED, allowance_granted=0)
        assert granted.to_dict()["allowance_granted"] == 0

    def test_the_outcome_vocabulary_is_closed(self) -> None:
        assert set(SPAWN_REFUSALS) < set(SPAWN_OUTCOMES)
        assert SPAWN_GRANTED not in SPAWN_REFUSALS
        assert len(set(SPAWN_OUTCOMES)) == len(SPAWN_OUTCOMES) == 5

    def test_a_spawn_is_offered_to_the_observer(self) -> None:
        events: list[Any] = []
        _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            subagent=RecordingSeam(SubagentResult(model_turns=1)),
            spawn_allowance=2,
            max_steps=9,
            observer=events.append,
            controls=LoopControls(synthesis=False),
        )
        spawns = [e for e in events if e.kind == "spawn"]
        assert [e.detail for e in spawns] == [SPAWN_GRANTED]

    def test_an_executor_ledger_and_a_minted_child_both_survive(self) -> None:
        """The executor's own ``sub_results`` must not erase the loop's."""

        class LedgerExecutor(DelegatingExecutor):
            sub_results = [_sub(task_id="executor-side")]

        outcome = _drive(
            _turn(_call("delegate")),
            _turn(_call("finish", cid="cf")),
            executor=LedgerExecutor(),
            subagent=RecordingSeam(SubagentResult(sub_result=_sub(), model_turns=1)),
            spawn_allowance=2,
            max_steps=9,
            controls=LoopControls(synthesis=False),
        )
        assert [s.task_id for s in outcome.result.sub_results] == ["child-1", "executor-side"]


# ── 9. the spawn-free path is byte-identical to today ─────────────────────────


class _GoldenExecutor:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.changed: set[str] = set()
        self.bytes_written = 11

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.seen.append((name, dict(arguments)))
        if name == "read_file":
            return ToolOutcome(result="file contents")
        if name == "write_file":
            self.changed.add("a.py")
            return ToolOutcome(result="wrote 11 bytes", changed_file="a.py")
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="")
        raise UnknownToolError(f"unknown tool: {name}")


class _GoldenHooks:
    def __call__(self, event: Any) -> Any:
        if event.event == "pre_tool" and event.tool == "write_file":
            return [HookDecision(decision=DECISION_REWRITE, arguments={"path": "a.py"}, source="h")]
        return [HookDecision(decision=DECISION_ALLOW, source="h")]


class _GoldenProgress:
    def __init__(self) -> None:
        self.seen: list[tuple[int, str, str, bool]] = []
        self.raised = False

    def __call__(self, index: int, tool: str, arguments: Any, ok: bool) -> None:
        if not self.raised:
            self.raised = True
            raise RuntimeError("sink cold")
        self.seen.append((index, tool, repr(arguments), ok))


class _GoldenSink:
    def __init__(self) -> None:
        self.seen: list[str] = []

    @property
    def active(self) -> bool:
        return True

    def acknowledge(self, packet: Optional[ContextPacket]) -> Any:
        self.seen.append(f"ack:{packet.original if packet else ''}")
        return []

    def on_operator_message(self, text: str) -> Any:
        self.seen.append(f"op:{text}")
        return []

    def on_progress_boundary(self, *, step_count: int = 0, phase_changed: bool = False) -> Any:
        self.seen.append(f"beat:{step_count}:{phase_changed}")
        return []


class _GoldenInbox:
    def __init__(self) -> None:
        self.polls = 0

    def __call__(self) -> Optional[list[str]]:
        self.polls += 1
        return ["are you there?"] if self.polls == 1 else None


def golden_drive() -> dict[str, Any]:
    """One representative spawn-free drive, rendered as a comparable transcript.

    Deliberately broad: a tool call, a prose turn that gets nudged, a rewriting
    hook, an unknown tool, a summary-less finish that forces synthesis, a
    degradation, presence beats and an operator aside. If ANY of that shifted,
    the delegation work changed a path it had no business touching.
    """
    complete = Scripted(
        _turn(_call("read_file", "c1", path="a.py")),
        _turn(content="Let me think about this."),
        _turn(_call("write_file", "c2", path="hacked.py"), _call("nope", "c3")),
        _turn(_call("finish", "c4")),
        _turn(content="The file was read and rewritten."),
    )
    executor = _GoldenExecutor()
    progress = _GoldenProgress()
    sink = _GoldenSink()
    inbox = _GoldenInbox()
    observed: list[tuple[str, str, dict[str, Any]]] = []
    task = Task(
        id="golden-1",
        repo_path="/repo",
        instruction="do the thing",
        context="some context",
        constraints=["be brief"],
        goal="the thing is done",
        acceptance=["it works"],
        context_packet=ContextPacket(original="do the thing, please"),
    )
    outcome = run(
        complete,
        task,
        executor=executor,
        max_steps=6,
        hooks=_GoldenHooks(),
        progress=progress,
        observer=lambda event: observed.append((event.kind, event.detail, dict(event.data))),
        presence=sink,
        operator_inbox=inbox,
        controls=LoopControls(max_continue_nudges=1),
        model="golden-model",
        continued_from="golden-0",
    )
    result = outcome.result.to_dict()
    for key in ("started_at", "duration_seconds"):
        result["stats"].pop(key, None)
    return {
        "completions": complete.calls,
        "exit_reason": outcome.exit_reason,
        "result": result,
        "hook_firings": [f.to_dict() for f in outcome.hook_firings],
        "degradations": [d.to_dict() for d in outcome.degradations],
        "progress": progress.seen,
        "presence": sink.seen,
        "observed": observed,
        "executor_seen": [[name, args] for name, args in executor.seen],
    }


def _normalise(value: Any) -> Any:
    """JSON round-trip, so tuple-vs-list is not mistaken for a real difference."""
    return json.loads(json.dumps(value, sort_keys=True))


class TestSpawningNothingChangedNothing:
    """The golden was captured from the loop as it stood BEFORE this task."""

    def test_the_transcript_is_byte_identical_to_the_pre_subagent_loop(self) -> None:
        expected = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
        assert _normalise(golden_drive()) == expected

    def test_the_golden_covers_more_than_a_happy_path(self) -> None:
        """A golden that only pins a trivial run pins almost nothing."""
        expected = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
        assert expected["exit_reason"] == EXIT_FINISHED
        assert len(expected["completions"]) == 5
        assert expected["degradations"], "the golden records no degradation at all"
        assert any(step["ok"] is False for step in expected["result"]["steps"])
        assert expected["hook_firings"]

    def test_a_spawn_free_drive_records_no_delegation_at_all(self) -> None:
        outcome = _drive(
            _turn(_call("read_note")),
            _turn(_call("finish", cid="cf")),
            executor=ChildExecutor(),
            subagent=RecordingSeam(SubagentResult()),
            spawn_allowance=5,
        )
        assert outcome.spawns == []
        assert outcome.child_model_turns == 0
        assert outcome.result.sub_results == []
        assert outcome.spawn_allowance_remaining == 5

    def test_the_tool_outcome_default_is_the_strict_no_op(self) -> None:
        assert ToolOutcome(result="x").spawn is None
