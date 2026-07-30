"""The muse's TOOL loop, proved structurally (task t10).

``tests/test_muse.py`` proves ``_think_loop`` terminates by AST rather than by
scenario, because a background thinking lane that runs forever is both a
resource leak and a lie. Wiring tools to that lane adds a **second** loop —
model turn, tool calls, tool results, model turn again — and a second loop is a
second place termination can be lost. So it gets the same treatment, in its own
file, at the same standard:

* exactly ONE ``while``, bounded by a comparison rather than ``while True``;
* no ``for`` inside it, so there is no second iteration path;
* every ``return`` drawn from the declared ``MUSE_TOOL_EXIT_*`` constants and
  nothing else;
* no ``raise`` and no ``try`` of its own — a fault is recorded one frame down
  and read here as an ordinary exit;
* and the property that matters most: **the tool loop spends the same turn
  counter the thinking loop spends**, against a ceiling drawn with ``min`` from
  the session's own budget. Tool rounds therefore cannot add a way to exceed
  ``max_turns`` — not "should not", *cannot*, because there is no arithmetic in
  the module that could produce a larger bound.

Everything here reads the source. Nothing here runs the loop; the executable
counterpart lives in ``tests/test_muse.py``'s ``TestTheToolSeam``, and the two
are deliberately separate — a behavioural budget test proves one scenario, this
file proves there is no other.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

import pytest

from embodiment.muse import (
    MUSE_EXIT_REASONS,
    MUSE_TOOL_EXIT_ANSWERED,
    MUSE_TOOL_EXIT_BUDGET,
    MUSE_TOOL_EXIT_DEGRADED,
    MUSE_TOOL_EXIT_REASONS,
    MUSE_TOOL_EXIT_ROUNDS,
)

_MUSE_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "muse.py"

#: The two bounded loops this module ships, by name. Both are private; both are
#: named here so a rename has to come past this file.
_TOOL_LOOP = "_tool_loop"
_THINK_LOOP = "_think_loop"
#: The one function allowed to advance the shared turn counter.
_TURN_SPENDER = "_model_turn"
#: The function that computes the tool loop's ceiling.
_CEILING = "_tool_ceiling"

#: The session attribute both loops compare against their bound.
_COUNTER = "turns"
#: The session attribute holding the whole session's model-turn budget.
_BUDGET = "budget"


# ── reading the source ────────────────────────────────────────────────────────


def _tree() -> ast.Module:
    return ast.parse(_MUSE_SRC.read_text(encoding="utf-8"))


def _functions() -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(_tree()) if isinstance(n, ast.FunctionDef)}


def _function(name: str) -> ast.FunctionDef:
    node = _functions().get(name)
    assert node is not None, f"{name} must exist by that name; the pins here read it"
    return node


def _attribute_targets(node: ast.AST, attr: str) -> list[ast.AST]:
    """Every statement in *node* that ASSIGNS to ``<something>.<attr>``."""
    found: list[ast.AST] = []
    for child in ast.walk(node):
        if isinstance(child, ast.AugAssign):
            target = child.target
            if isinstance(target, ast.Attribute) and target.attr == attr:
                found.append(child)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Attribute) and target.attr == attr:
                    found.append(child)
    return found


def _enclosing(name_of: str) -> Optional[str]:
    """The function a source line belongs to — used to say WHERE a write lives."""
    for fname, node in _functions().items():
        for child in ast.walk(node):
            if isinstance(child, ast.AugAssign):
                target = child.target
                if isinstance(target, ast.Attribute) and target.attr == name_of:
                    return fname
    return None


# ── 1. the tool loop's own exit vocabulary ────────────────────────────────────


class TestTheToolExitVocabulary:
    """Four exits, declared as constants, and no fifth — the same rule as ``_think_loop``."""

    def test_exit_reasons_has_exactly_four_members(self):
        assert MUSE_TOOL_EXIT_REASONS == (
            MUSE_TOOL_EXIT_ANSWERED,
            MUSE_TOOL_EXIT_ROUNDS,
            MUSE_TOOL_EXIT_BUDGET,
            MUSE_TOOL_EXIT_DEGRADED,
        )
        assert len(set(MUSE_TOOL_EXIT_REASONS)) == 4

    def test_module_declares_exactly_four_tool_exit_constants(self):
        names = set()
        for node in _tree().body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("MUSE_TOOL_EXIT_"):
                        names.add(target.id)
        assert names == {
            "MUSE_TOOL_EXIT_ANSWERED",
            "MUSE_TOOL_EXIT_ROUNDS",
            "MUSE_TOOL_EXIT_BUDGET",
            "MUSE_TOOL_EXIT_DEGRADED",
            "MUSE_TOOL_EXIT_REASONS",
        }

    def test_the_two_exit_vocabularies_are_disjoint(self):
        """A tool round ending is never mistakable for the SESSION ending.

        The thinking loop's exits reach a host's ledger; the tool loop's stay
        inside one thinking turn. Sharing a string would make a record ambiguous
        about which loop it describes, so the values are prefixed and the
        disjointness is pinned rather than assumed.
        """
        assert not (set(MUSE_EXIT_REASONS) & set(MUSE_TOOL_EXIT_REASONS))


# ── 2. the tool loop is one counter-bounded while ─────────────────────────────


class TestTheToolLoopIsBounded:
    """The structural termination proof, mirroring ``test_muse.py``:318-325."""

    def test_the_tool_loop_is_one_counter_bounded_while(self):
        node = _function(_TOOL_LOOP)
        whiles = [n for n in ast.walk(node) if isinstance(n, ast.While)]
        fors = [n for n in ast.walk(node) if isinstance(n, ast.For)]
        assert len(whiles) == 1 and not fors
        # ``while True:`` would be an ast.Constant test — the bound must be a
        # comparison against the ceiling, so exhausting it is the only outcome.
        assert isinstance(whiles[0].test, ast.Compare), ast.dump(whiles[0].test)

    def test_the_tool_loop_returns_only_the_declared_exit_constants(self):
        """Structural proof that no fifth exit path exists."""
        returned = set()
        for node in ast.walk(_function(_TOOL_LOOP)):
            if isinstance(node, ast.Return):
                assert isinstance(node.value, ast.Name), ast.dump(node)
                returned.add(node.value.id)
        assert returned == {
            "MUSE_TOOL_EXIT_ANSWERED",
            "MUSE_TOOL_EXIT_ROUNDS",
            "MUSE_TOOL_EXIT_BUDGET",
            "MUSE_TOOL_EXIT_DEGRADED",
        }

    def test_the_tool_loop_raises_nothing_of_its_own(self):
        assert not [n for n in ast.walk(_function(_TOOL_LOOP)) if isinstance(n, ast.Raise)]

    def test_the_tool_loop_catches_nothing_of_its_own(self):
        """Every fault is handled in a helper that RECORDS it; the loop just exits."""
        assert not [n for n in ast.walk(_function(_TOOL_LOOP)) if isinstance(n, ast.Try)]

    def test_the_ceiling_is_computed_once_before_the_loop(self):
        """A bound rewritten inside its own loop is not a bound."""
        node = _function(_TOOL_LOOP)
        loop = next(n for n in ast.walk(node) if isinstance(n, ast.While))
        written_inside = {
            t.id
            for child in ast.walk(loop)
            if isinstance(child, (ast.Assign, ast.AugAssign))
            for t in (child.targets if isinstance(child, ast.Assign) else [child.target])
            if isinstance(t, ast.Name)
        }
        bound = loop.test.comparators[0]
        assert isinstance(bound, ast.Name), ast.dump(bound)
        assert bound.id not in written_inside, f"{bound.id} is rewritten inside its own loop"


# ── 3. one counter bounds BOTH loops ──────────────────────────────────────────


class TestOneCounterBoundsBothLoops:
    """The property that makes a tool loop safe: it spends the SAME budget.

    A tool loop with a budget of its own would be a second way to buy model
    turns, and ``max_turns`` would quietly stop meaning what it says. Instead
    every model call — thinking turn or tool round — advances one counter, and
    the tool loop's ceiling is drawn with ``min`` from the session budget, so it
    can only ever be *lower*.
    """

    def test_both_loops_compare_the_same_turn_counter(self):
        for name in (_THINK_LOOP, _TOOL_LOOP):
            loop = next(n for n in ast.walk(_function(name)) if isinstance(n, ast.While))
            left = loop.test.left
            assert isinstance(left, ast.Attribute), f"{name}: {ast.dump(left)}"
            assert left.attr == _COUNTER, f"{name} is not bounded by ctx.{_COUNTER}"

    def test_exactly_one_statement_advances_the_turn_counter(self):
        writes = _attribute_targets(_tree(), _COUNTER)
        assert len(writes) == 1, [ast.dump(w) for w in writes]
        write = writes[0]
        assert isinstance(write, ast.AugAssign), ast.dump(write)
        assert isinstance(write.op, ast.Add), ast.dump(write)
        assert isinstance(write.value, ast.Constant) and write.value.value == 1

    def test_the_turn_counter_is_advanced_where_the_model_is_called(self):
        assert _enclosing(_COUNTER) == _TURN_SPENDER

    def test_nothing_decrements_the_turn_counter(self):
        for node in ast.walk(_tree()):
            if isinstance(node, ast.AugAssign):
                target = node.target
                if isinstance(target, ast.Attribute) and target.attr == _COUNTER:
                    assert not isinstance(node.op, ast.Sub), ast.dump(node)

    def test_the_session_budget_is_never_rewritten(self):
        """``ctx.budget`` is set once, when the session is constructed, and read after."""
        assert _attribute_targets(_tree(), _BUDGET) == []

    def test_the_ceiling_is_drawn_with_min_from_the_session_budget(self):
        """The one line that makes "tool rounds cannot outspend the budget" true."""
        node = _function(_CEILING)
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
        assert len(returns) == 1, ast.dump(node)
        call = returns[0].value
        assert isinstance(call, ast.Call), ast.dump(returns[0])
        assert isinstance(call.func, ast.Name) and call.func.id == "min", ast.dump(call)
        reached = {child.attr for child in ast.walk(call) if isinstance(child, ast.Attribute)}
        assert _BUDGET in reached, f"the ceiling ignores ctx.{_BUDGET}: {ast.dump(call)}"


# ── 4. no third loop slipped in beside them ───────────────────────────────────


class TestNoOtherIterationPath:
    """Two bounded loops, named and proved. A third would be unproved by construction."""

    def test_the_module_has_exactly_two_while_loops(self):
        owners = []
        for name, node in _functions().items():
            for child in ast.walk(node):
                if isinstance(child, ast.While):
                    owners.append(name)
        assert sorted(owners) == sorted([_THINK_LOOP, _TOOL_LOOP]), owners

    def test_no_while_in_the_module_is_unbounded(self):
        for node in ast.walk(_tree()):
            if isinstance(node, ast.While):
                assert isinstance(node.test, ast.Compare), ast.dump(node.test)

    @pytest.mark.parametrize("name", [_THINK_LOOP, _TOOL_LOOP, _TURN_SPENDER, _CEILING])
    def test_the_proof_is_not_vacuous(self, name: str):
        """Every function these pins read must exist; a rename must go red here."""
        assert _function(name).name == name
