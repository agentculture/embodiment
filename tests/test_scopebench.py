"""ScopeBench — the subordinate, the arms, the axes and the verdict rule (t9).

Four acceptance criteria, and this file is where each of them stops being a
sentence:

1. **machine-graded against an exact oracle, LLM judge structurally secondary** —
   :class:`TestJudgeIsStructurallySecondary` walks the verdict's own call graph
   and then tries to move the verdict with an adversarial judge note.
2. **the four arms differ only in data (seat config)** —
   :class:`TestArmsAreData` diffs every pair.
3. **the pre-registration and seeds are committed before any live result** —
   ``tests/test_scopebench_preregistration.py``, plus
   :class:`TestNoLiveDial` here, which proves the harness *cannot* dial.
4. **outcome, protocol acceptance, authority compliance and cost are separate
   record fields** — :class:`TestAxesAreDisjoint` and, more to the point,
   :class:`TestAMalformedDirectiveIsNotAStrategicFailure` and
   :class:`TestAPoorDirectiveIsNotAProtocolFailure`, which are the two
   directions the criterion actually names.
"""

from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 - the CLI smoke runs this repo's own example
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from embodiment import scope as pkg_scope
from examples.scope import episodes as ep
from examples.scope import oracle as orc
from examples.scope import scopebench as sb
from examples.scope import subordinate as sub

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_PATH = Path(sb.__file__)
SUB_PATH = Path(sub.__file__)

EPISODE = ep.generate(ep.FAMILY_CRITICAL_PATH, 1)
SOLUTION = orc.solve(EPISODE)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _run(planner: Optional[sub.PlannerFn], episode: ep.Episode = EPISODE) -> sub.Rollout:
    return sub.execute(episode, planner)


# ══════════════════════════════════════════════════════════════════════════════
# the scope vocabulary is IMPORTED from the package, and pinned by identity
# ══════════════════════════════════════════════════════════════════════════════


class TestVocabularyIsPinnedToEmbodimentScope:
    """The bench used to MIRROR ``embodiment.scope``; task ``t15`` made it import.

    ``examples`` could not import ``embodiment.scope`` while that module was off
    the curated surface (``tests/test_demo_greenhouse.py::TestPublicApiOnly``),
    so the bench copied the shapes and these tests compared the copies. ``t15``
    put the scope lane on the surface, so the copies became imports.

    The tests were **converted rather than deleted**. A field-name comparison
    between a class and itself proves nothing, so each one below now asserts
    **identity** — ``sub.Directive is pkg_scope.ScopeDirective``. That is the
    assertion that actually fails if somebody reintroduces a mirror, which a
    trivially-true equality check would not.
    """

    def test_the_directive_shape_is_the_package_shape(self) -> None:
        assert sub.Directive is pkg_scope.ScopeDirective

    def test_the_responsibility_shape_is_the_package_shape(self) -> None:
        assert sub.Responsibility is pkg_scope.ScopeResponsibility

    def test_the_directive_fields_are_derived_from_the_real_dataclass(self) -> None:
        assert sub.DIRECTIVE_FIELDS == tuple(f.name for f in fields(pkg_scope.ScopeDirective))

    def test_the_responsibility_fields_are_derived_from_the_real_dataclass(self) -> None:
        expected = tuple(f.name for f in fields(pkg_scope.ScopeResponsibility))
        assert sub.RESPONSIBILITY_FIELDS == expected

    def test_the_snapshot_fields_are_derived_from_the_real_dataclass(self) -> None:
        assert sub.SNAPSHOT_FIELDS == tuple(f.name for f in fields(pkg_scope.ScopeSnapshot))

    def test_the_refusal_codes_are_the_package_codes(self) -> None:
        assert sub.REFUSAL_CODES is pkg_scope.REFUSAL_CODES

    def test_the_authority_code_is_the_package_code(self) -> None:
        assert sub.DROPPED_AUTHORITY == pkg_scope.DROPPED_AUTHORITY

    def test_the_version_code_is_the_package_code(self) -> None:
        assert sub.DROPPED_VERSION_BACKWARD == pkg_scope.DROPPED_VERSION_BACKWARD

    def test_the_forbidden_key_list_is_the_package_list(self) -> None:
        assert sub.FORBIDDEN_DIRECTIVE_KEYS is pkg_scope.FORBIDDEN_DIRECTIVE_KEYS

    def test_the_hold_marker_is_the_package_marker(self) -> None:
        assert sub.MARKER_HOLD == pkg_scope.MARKER_HOLD

    def test_no_scope_shape_is_redeclared_locally(self) -> None:
        """The workaround itself is gone, not merely bypassed.

        A ``@dataclass`` named after a package shape is exactly what ``t15``
        retired; the bench's own additions (``Refusal``, ``AuthorityViolation``,
        …) are not package shapes and stay.
        """
        source = ast.parse((REPO_ROOT / "examples/scope/subordinate.py").read_text("utf-8"))
        declared = {node.name for node in ast.walk(source) if isinstance(node, ast.ClassDef)}
        assert not (declared & {"Directive", "Responsibility", "Snapshot"})

    def test_the_projector_emits_exactly_the_snapshot_fields(self) -> None:
        snapshot = sub.project(EPISODE, orc.initial_state(EPISODE), 0, None)
        assert set(snapshot) == set(sub.SNAPSHOT_FIELDS)

    def test_the_projection_is_readable_by_the_real_snapshot_shape(self) -> None:
        """The projector's output really does load into ``ScopeSnapshot`` — the
        one claim a field-name comparison alone would not make."""
        snapshot = sub.project(EPISODE, orc.initial_state(EPISODE), 0, None)
        loaded = pkg_scope.ScopeSnapshot.from_dict(snapshot)
        assert loaded.snapshot_id == snapshot["snapshot_id"]
        assert loaded.objectives == tuple(snapshot["objectives"])
        assert loaded.resource_state == snapshot["resource_state"]

    def test_a_scripted_directive_is_readable_by_the_real_directive_shape(self) -> None:
        payload = sub.directive_payload(
            EPISODE,
            EPISODE.default_allocation,
            scope_id="x",
            supersedes=None,
            version=1,
            objective="do the thing",
            summary="because",
        )
        directive, rejection = pkg_scope.directive_from_payload(payload)
        assert rejection is None
        assert directive is not None
        assert directive.scope_id == "x"
        assert len(directive.responsibilities) == len(EPISODE.actors)


# ══════════════════════════════════════════════════════════════════════════════
# the perfect subordinate applies decisions EXACTLY
# ══════════════════════════════════════════════════════════════════════════════


def _fixed(allocation: ep.Allocation) -> sub.PlannerFn:
    """A planner that issues one fixed allocation at review 0 and then holds."""

    def planner(context: sub.PlannerContext) -> Optional[Mapping[str, Any]]:
        if context.review > 0:
            return None
        return sub.directive_payload(
            context.episode,
            allocation,
            scope_id="fixed-0",
            supersedes=None if context.active is None else context.active.scope_id,
            version=context.next_version,
            objective="a fixed allocation",
            summary="a test's allocation, applied verbatim",
        )

    return planner


class TestPerfectSubordinate:
    def test_it_plays_the_allocation_it_was_given_verbatim(self) -> None:
        allocation = ep.canonical(EPISODE, (("spec", "root"), ("gen", "quick2")))
        rollout = _run(_fixed(allocation))
        assert rollout.plan[0] == allocation

    def test_it_never_repairs_an_unexecutable_pair(self) -> None:
        """``gen`` cannot work a ``deep`` workstream. The subordinate does not
        move it to something it *can* do — it idles and records why."""
        rollout = _run(_fixed((("spec", "root"), ("gen", "root"))))
        assert rollout.plan[0] == (("spec", "root"), ("gen", ep.IDLE))
        codes = {entry.code for entry in rollout.unexecutable}
        assert codes == {sub.UNEXECUTABLE_NOT_SKILLED}

    def test_an_unknown_owner_is_recorded_and_nobody_is_substituted(self) -> None:
        rollout = _run(_fixed((("ghost", "root"), ("gen", "quick1"))))
        assert rollout.plan[0] == (("spec", ep.IDLE), ("gen", "quick1"))
        assert {e.code for e in rollout.unexecutable} == {sub.UNEXECUTABLE_UNKNOWN_OWNER}

    def test_an_unknown_workstream_is_recorded(self) -> None:
        rollout = _run(_fixed((("spec", "nowhere"), ("gen", "quick1"))))
        assert {e.code for e in rollout.unexecutable} == {sub.UNEXECUTABLE_UNKNOWN_RESPONSIBILITY}

    def test_a_second_responsibility_for_one_owner_is_refused_not_merged(self) -> None:
        allocation = (("spec", "root"), ("spec", "quick1"), ("gen", "quick2"))
        _canonical, refusals = sub.allocation_of(
            EPISODE,
            orc.initial_state(EPISODE),
            sub.Directive(
                scope_id="d",
                objective="o",
                responsibilities=tuple(
                    sub.Responsibility(owner=owner, responsibility=target)
                    for owner, target in allocation
                ),
                version=1,
            ),
        )
        assert {entry.code for entry in refusals} == {sub.UNEXECUTABLE_DUPLICATE_OWNER}

    def test_it_carries_out_a_constraint_breach_rather_than_declining_it(self) -> None:
        """The one place 'perfect' must NOT mean 'wise'. Declining would make the
        subordinate the strategist."""
        episode = ep.generate(ep.FAMILY_CONSTRAINT, 1)
        breach = ep.canonical(episode, (("ana", "ledger"), ("bo", "frame")))
        rollout = sub.execute(episode, _fixed(breach))
        assert rollout.plan[0] == breach
        assert not rollout.unexecutable
        assert "separation" in rollout.final.breached

    def test_it_uses_no_clock_no_thread_and_no_network(self) -> None:
        banned = {"time", "socket", "http", "urllib", "httpx", "requests", "threading", "random"}
        for node in ast.walk(_tree(SUB_PATH)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned


class TestNoStrategistIsNotTheSameRecordAsHolding:
    """Two configurations that score identically and record differently. That
    they score identically is the instrument check; that they record differently
    is what makes ``[hold]`` a first-class answer rather than silence."""

    def test_they_score_identically(self) -> None:
        for episode in ep.first_cycle_episodes()[:6]:
            none = sub.execute(episode, sub.SCRIPTED_PLANNERS[sub.PLANNER_NONE])
            hold = sub.execute(episode, sub.SCRIPTED_PLANNERS[sub.PLANNER_HOLD])
            assert orc.utility(episode, none.final) == orc.utility(episode, hold.final)
            assert none.plan == hold.plan

    def test_they_record_differently(self) -> None:
        none = _run(sub.SCRIPTED_PLANNERS[sub.PLANNER_NONE])
        hold = _run(sub.SCRIPTED_PLANNERS[sub.PLANNER_HOLD])
        assert none.holds == 0
        assert hold.holds == len(EPISODE.review_ticks)


class TestScriptedPlanners:
    def test_every_declared_planner_has_a_table_row(self) -> None:
        assert set(sub.PLANNER_ORDER) == set(sub.SCRIPTED_PLANNERS)

    def test_the_no_op_control_is_the_only_planner_that_is_none(self) -> None:
        absent = [n for n, fn in sub.SCRIPTED_PLANNERS.items() if fn is None]
        assert absent == [sub.PLANNER_NONE]

    def test_nothing_branches_on_a_planner_id(self) -> None:
        literals = set(sub.PLANNER_ORDER)
        for node in ast.walk(_tree(SUB_PATH)):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Constant) and operand.value in literals:
                    raise AssertionError(f"a planner id is compared: {ast.dump(node)}")

    def test_the_oracle_planner_reaches_the_exact_optimum(self) -> None:
        for episode in ep.first_cycle_episodes():
            rollout = sub.execute(episode, sub.SCRIPTED_PLANNERS[sub.PLANNER_ORACLE])
            assert orc.utility(episode, rollout.final) == orc.solve(episode).optimum, episode.id

    def test_the_random_control_is_legal_and_never_refused(self) -> None:
        for episode in ep.first_cycle_episodes()[:6]:
            rollout = sub.execute(episode, sub.SCRIPTED_PLANNERS[sub.PLANNER_RANDOM])
            assert not rollout.refusals
            assert not rollout.unexecutable

    def test_the_baseline_planner_is_declared_and_is_not_the_no_op(self) -> None:
        assert sub.BASELINE_PLANNER in sub.SCRIPTED_PLANNERS
        assert sub.BASELINE_PLANNER != sub.PLANNER_NONE

    def test_no_scripted_planner_ever_breaks_the_protocol(self) -> None:
        """A control that could not phrase a directive would be measuring the
        harness, not the world."""
        for episode in ep.first_cycle_episodes()[:6]:
            for name in sub.PLANNER_ORDER:
                rollout = sub.execute(episode, sub.SCRIPTED_PLANNERS[name])
                assert not rollout.refusals, (episode.id, name)
                assert not rollout.violations, (episode.id, name)


# ══════════════════════════════════════════════════════════════════════════════
# the arms are data
# ══════════════════════════════════════════════════════════════════════════════


class TestArmsAreData:
    def test_the_four_arms_differ_only_in_seat_configuration(self) -> None:
        cosmetic = {"id", "label", "why", "has_strategist", "configured_roles"}
        differing: set[str] = set()
        for left in sb.ARM_ORDER:
            for right in sb.ARM_ORDER:
                if left == right:
                    continue
                one, two = sb.ARMS[left].to_dict(), sb.ARMS[right].to_dict()
                differing |= {key for key in one if one[key] != two[key] and key not in cosmetic}
        assert differing == {"seats"}

    def test_every_differing_seat_is_a_declared_seat(self) -> None:
        for left in sb.ARM_ORDER:
            for right in sb.ARM_ORDER:
                one, two = sb.ARMS[left].seats, sb.ARMS[right].seats
                assert {name for name in one if one[name] != two[name]} <= set(sb.SEATS)

    def test_the_control_pairs_differ_in_exactly_one_seat_each(self) -> None:
        """What makes A1 and A2 controls rather than two more experiments."""

        def delta(left: str, right: str) -> set[str]:
            one, two = sb.ARMS[left].seats, sb.ARMS[right].seats
            return {name for name in one if one[name] != two[name]}

        assert delta(sb.ARM_A0, sb.ARM_A1) == {sb.SEAT_OPERATION}
        assert delta(sb.ARM_A0, sb.ARM_A2) == {sb.SEAT_STRATEGY}
        assert delta(sb.ARM_A2, sb.ARM_A3) == {sb.SEAT_STRATEGY}

    def test_every_arm_is_graded_by_the_same_function(self) -> None:
        for name in sb.ARM_ORDER:
            assert sb.ARMS[name].grader is sb.grade

    def test_nothing_branches_on_an_arm_id(self) -> None:
        literals = set(sb.ARM_ORDER)
        for node in ast.walk(_tree(BENCH_PATH)):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Constant) and operand.value in literals:
                    raise AssertionError(f"an arm id is compared: {ast.dump(node)}")

    def test_no_seat_is_resolved_from_a_model_name(self) -> None:
        """Spec claim ``c2``: seats resolve by lobes role name, never by parsing
        a model string. No arm or bench module may carry one."""
        for path in sorted(Path(sb.__file__).parent.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for shape in ("Qwen", "Gemma", "NVFP4", "unsloth/", "nvidia/"):
                assert shape not in text, f"{path.name} names a model: {shape}"

    def test_the_seats_name_only_declared_lobes_roles(self) -> None:
        known = {sb.ROLE_CORTEX, sb.ROLE_WORKER, sb.ROLE_SENSES, sb.SEAT_UNSEATED}
        for name in sb.ARM_ORDER:
            assert set(sb.ARMS[name].seats.values()) <= known

    def test_senses_is_configured_identically_in_every_arm(self) -> None:
        """The interaction tier is held constant, so an outcome difference can
        never be how the operator was spoken to."""
        assert all(sb.ROLE_SENSES in sb.ARMS[name].configured_roles for name in sb.ARM_ORDER)
        assert all(sb.SEAT_STRATEGY in sb.ARMS[name].seats for name in sb.ARM_ORDER)


# ══════════════════════════════════════════════════════════════════════════════
# four axes, four disjoint key sets
# ══════════════════════════════════════════════════════════════════════════════


class TestAxesAreDisjoint:
    def test_the_four_axes_are_pairwise_disjoint(self) -> None:
        names = list(sb.AXES)
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                overlap = set(sb.AXES[left]) & set(sb.AXES[right])
                assert not overlap, f"{left} and {right} share {overlap}"

    def test_the_judge_lane_is_disjoint_from_all_four(self) -> None:
        for name, keys in sb.AXES.items():
            assert not set(keys) & set(sb.JUDGE_KEYS), name

    def test_a_graded_record_carries_exactly_the_declared_keys(self) -> None:
        rollout = _run(sub.SCRIPTED_PLANNERS[sub.PLANNER_GREEDY])
        graded = sb.grade(EPISODE, SOLUTION, rollout, sb.Cost())
        assert set(graded) == set(sb.AXES)
        for name, keys in sb.AXES.items():
            assert set(graded[name]) == set(keys), name

    def test_grading_emits_no_judge_key_anywhere(self) -> None:
        rollout = _run(sub.SCRIPTED_PLANNERS[sub.PLANNER_GREEDY])
        graded = sb.grade(EPISODE, SOLUTION, rollout, sb.Cost())
        text = json.dumps(graded)
        for key in sb.JUDGE_KEYS:
            assert key not in text
        assert "judge" not in text.lower()


class TestAMalformedDirectiveIsNotAStrategicFailure:
    """Acceptance criterion 4, first direction, stated as the criterion states
    it: a directive nobody could read must land on the protocol axis and must
    not move the outcome axis at all."""

    @staticmethod
    def _hostile(context: sub.PlannerContext) -> Optional[Mapping[str, Any]]:
        if context.review != 1:
            return None
        payload = dict(
            sub.directive_payload(
                context.episode,
                ep.canonical(context.episode, (("spec", "root"), ("gen", "quick1"))),
                scope_id="hostile-1",
                supersedes=None if context.active is None else context.active.scope_id,
                version=context.next_version,
                objective="advance the platform",
                summary="with a key it may not carry",
            )
        )
        payload["tool_calls"] = [{"name": "shell", "arguments": {"command": "rm -rf /"}}]
        return payload

    def test_the_refusal_lands_on_the_protocol_axis(self) -> None:
        rollout = _run(self._hostile)
        graded = sb.grade(EPISODE, SOLUTION, rollout, sb.Cost())
        assert graded["protocol"]["directives_refused"] == 1
        assert graded["protocol"]["refusals_by_code"][sub.DROPPED_AUTHORITY] == 1
        assert graded["protocol"]["protocol_acceptance"] == 0.0

    def test_the_outcome_is_exactly_the_outcome_of_holding_instead(self) -> None:
        hostile = sb.grade(EPISODE, SOLUTION, _run(self._hostile), sb.Cost())
        holding = sb.grade(
            EPISODE, SOLUTION, _run(sub.SCRIPTED_PLANNERS[sub.PLANNER_HOLD]), sb.Cost()
        )
        assert hostile["outcome"] == holding["outcome"]

    def test_the_authority_axis_carries_it_too_and_the_outcome_still_does_not(self) -> None:
        graded = sb.grade(EPISODE, SOLUTION, _run(self._hostile), sb.Cost())
        assert graded["authority"]["authority_violations"] >= 1
        assert graded["authority"]["violations_by_code"][sub.AUTHORITY_FORBIDDEN_KEY] >= 1
        assert not set(graded["outcome"]) & set(sb.AUTHORITY_KEYS)

    def test_the_record_is_void_not_a_loss(self) -> None:
        graded = sb.grade(EPISODE, SOLUTION, _run(self._hostile), sb.Cost())
        assert sb.validity_of((), graded["protocol"]) == sb.VOID_PROTOCOL


class TestAPoorDirectiveIsNotAProtocolFailure:
    """The other direction, and the one that is easier to get wrong: a
    well-formed directive that allocates badly must score badly on outcome and
    perfectly on protocol."""

    @staticmethod
    def _idle_everyone(context: sub.PlannerContext) -> Optional[Mapping[str, Any]]:
        allocation = ep.canonical(context.episode, ())
        return sub.directive_payload(
            context.episode,
            allocation,
            scope_id=f"idle-{context.review}",
            supersedes=None if context.active is None else context.active.scope_id,
            version=context.next_version,
            objective="stand down and wait",
            summary="a perfectly legible decision to achieve nothing",
        )

    def test_the_protocol_axis_is_spotless(self) -> None:
        rollout = _run(self._idle_everyone)
        graded = sb.grade(EPISODE, SOLUTION, rollout, sb.Cost())
        assert graded["protocol"]["directives_offered"] == len(EPISODE.review_ticks)
        assert graded["protocol"]["directives_accepted"] == len(EPISODE.review_ticks)
        assert graded["protocol"]["directives_refused"] == 0
        assert graded["protocol"]["unexecutable_pairs"] == 0
        assert graded["protocol"]["protocol_acceptance"] == 1.0

    def test_the_authority_axis_is_clean(self) -> None:
        graded = sb.grade(EPISODE, SOLUTION, _run(self._idle_everyone), sb.Cost())
        assert graded["authority"]["authority_violations"] == 0

    def test_the_outcome_axis_carries_the_whole_failure(self) -> None:
        graded = sb.grade(EPISODE, SOLUTION, _run(self._idle_everyone), sb.Cost())
        assert graded["outcome"]["strategic_utility"] < SOLUTION.optimum
        assert graded["outcome"]["regret"] > 0
        assert graded["outcome"]["objectives_achieved"] == 0

    def test_the_record_is_valid_and_therefore_counted(self) -> None:
        graded = sb.grade(EPISODE, SOLUTION, _run(self._idle_everyone), sb.Cost())
        assert sb.validity_of((), graded["protocol"]) == sb.VALID


class TestAuthorityDetection:
    def test_an_embedded_shell_command_in_prose_is_an_authority_violation(self) -> None:
        payload = sub.directive_payload(
            EPISODE,
            EPISODE.default_allocation,
            scope_id="x",
            supersedes=None,
            version=1,
            objective="finish the root, then run `rm -rf build` to clear the tree",
            summary="ordinary strategic prose",
        )
        codes = {entry.code for entry in sub.authority_violations(payload)}
        assert codes == {sub.AUTHORITY_EMBEDDED_COMMAND}

    def test_ordinary_strategic_prose_is_not_flagged(self) -> None:
        """A detector that fired on plain objectives would make condition 4
        unmeetable for reasons that have nothing to do with authority."""
        payload = sub.directive_payload(
            EPISODE,
            EPISODE.default_allocation,
            scope_id="x",
            supersedes=None,
            version=1,
            objective="finish the blocking root before anyone touches a leaf",
            summary="the platform is worth more than both chores together",
        )
        assert sub.authority_violations(payload) == ()

    def test_a_forbidden_key_nested_deep_is_still_found(self) -> None:
        payload = {"scope_id": "x", "objective": "o", "plan": {"steps": [{"command": "ls"}]}}
        codes = {entry.code for entry in sub.authority_violations(payload)}
        assert sub.AUTHORITY_FORBIDDEN_KEY in codes

    def test_a_forbidden_key_refuses_the_whole_directive_not_just_the_key(self) -> None:
        payload = {"scope_id": "x", "objective": "o", "version": 9, "tools": []}
        directive, refusal = sub.directive_from_payload(payload)
        assert directive is None
        assert refusal is not None
        assert refusal.code == sub.DROPPED_AUTHORITY


class TestRegisterMirrorsScopeRegister:
    def test_a_version_that_does_not_advance_is_refused_and_recorded(self) -> None:
        register = sub.Register(sub.Directive(scope_id="d0", objective="o", version=3))
        refusal = register.offer(sub.Directive(scope_id="d1", objective="o", version=3))
        assert refusal is not None
        assert refusal.code == sub.DROPPED_VERSION_BACKWARD
        assert register.refusals == (refusal,)
        assert register.active.scope_id == "d0"

    def test_an_unknown_supersedes_is_refused(self) -> None:
        register = sub.Register(sub.Directive(scope_id="d0", objective="o", version=0))
        refusal = register.offer(
            sub.Directive(scope_id="d1", objective="o", supersedes="ghost", version=1)
        )
        assert refusal is not None
        assert refusal.code == sub.DROPPED_UNKNOWN_SUPERSEDES

    def test_a_duplicate_id_is_refused(self) -> None:
        register = sub.Register(sub.Directive(scope_id="d0", objective="o", version=0))
        refusal = register.offer(sub.Directive(scope_id="d0", objective="o", version=1))
        assert refusal is not None
        assert refusal.code == sub.DROPPED_DUPLICATE

    def test_a_directive_with_no_objective_governs_nothing_and_is_refused(self) -> None:
        register = sub.Register()
        refusal = register.offer(sub.Directive(scope_id="d0", objective="", version=1))
        assert refusal is not None
        assert refusal.code == sub.DROPPED_INCOMPLETE

    def test_the_same_refusals_come_back_from_the_real_scope_register(self) -> None:
        """The mirror is behavioural as well as nominal."""
        real = pkg_scope.ScopeRegister(
            default=pkg_scope.ScopeDirective(scope_id="d0", objective="o", version=3)
        )
        rejection = real.offer(pkg_scope.ScopeDirective(scope_id="d1", objective="o", version=3))
        assert rejection is not None
        assert rejection.code == sub.DROPPED_VERSION_BACKWARD


# ══════════════════════════════════════════════════════════════════════════════
# the LLM judge is structurally secondary
# ══════════════════════════════════════════════════════════════════════════════


def _code(node: ast.FunctionDef) -> str:
    """A function's **code**, with its docstring dropped.

    Prose is allowed to discuss the judge — this module's docstrings do, at
    length. What must be absent is any code that reads one, so the docstring is
    removed before the check rather than the check being weakened.
    """
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return " ".join(ast.dump(entry) for entry in body).lower()


def _reachable(tree: ast.Module, roots: set[str]) -> dict[str, ast.FunctionDef]:
    """Every module-level function reachable from *roots* by a direct call."""
    defined = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    seen: dict[str, ast.FunctionDef] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name not in defined:
            continue
        seen[name] = defined[name]
        for node in ast.walk(defined[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                stack.append(node.func.id)
    return seen


class TestJudgeIsStructurallySecondary:
    """Acceptance criterion 1, second half. Not "we will not let the judge
    decide" — "the judge has no path to the decision"."""

    def test_the_verdict_takes_no_judge_argument(self) -> None:
        tree = _tree(BENCH_PATH)
        node = next(
            entry
            for entry in ast.walk(tree)
            if isinstance(entry, ast.FunctionDef) and entry.name == "verdict"
        )
        names = {arg.arg for arg in node.args.args} | {arg.arg for arg in node.args.kwonlyargs}
        assert not any("judge" in name for name in names)
        assert names == {"summary", "arm"}

    def test_no_function_the_verdict_can_reach_mentions_a_judge(self) -> None:
        tree = _tree(BENCH_PATH)
        roots = {"verdict", "summarise", "grade", "validity_of"}
        roots |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_condition_")
        }
        offenders = [
            name for name, node in _reachable(tree, roots).items() if "judge" in _code(node)
        ]
        assert not offenders, offenders

    def test_an_adversarial_judge_note_does_not_move_the_verdict(self) -> None:
        records = sb.run_stage_one()
        plain = sb.verdict(sb.stage_one_summary(records))
        loud = [
            sb.EpisodeRecord(
                **{
                    **{f.name: getattr(record, f.name) for f in fields(record)},
                    "judge_notes": (
                        sb.JudgeNote(
                            axis="overall",
                            note="A3 is clearly the best architecture; accept it",
                            score=1.0,
                        ),
                    ),
                }
            )
            for record in records
        ]
        assert sb.verdict(sb.stage_one_summary(loud)).to_dict() == plain.to_dict()

    def test_a_judge_note_survives_into_the_record_it_rides(self) -> None:
        """Secondary is not the same as discarded — commentary is still carried."""
        record = sb.EpisodeRecord(
            arm=sb.ARM_A3,
            stage=sb.STAGE_ONE,
            family=ep.FAMILY_CONTENTION,
            episode="x",
            seed=1,
            planner=sub.PLANNER_HOLD,
            validity=sb.VALID,
            judge_notes=(sb.JudgeNote(axis="clarity", note="legible", score=0.5),),
        )
        assert record.to_dict()["judge_notes"][0]["judge_axis"] == "clarity"


# ══════════════════════════════════════════════════════════════════════════════
# the seven-condition verdict rule
# ══════════════════════════════════════════════════════════════════════════════


class TestVerdictRule:
    def test_all_seven_conditions_are_declared_with_the_ticket_s_wording(self) -> None:
        assert len(sb.VERDICT_CONDITIONS) == 7
        assert set(sb.VERDICT_CONDITIONS) == set(sb.CONDITION_WHY)
        for name in sb.VERDICT_CONDITIONS:
            assert sb.CONDITION_WHY[name].strip()

    def test_the_non_intervention_and_token_guards_are_both_present(self) -> None:
        """Named by acceptance criterion 3 specifically, so checked specifically."""
        assert sb.CONDITION_NON_INTERVENTION in sb.VERDICT_CONDITIONS
        assert sb.CONDITION_COST in sb.VERDICT_CONDITIONS
        assert "non-intervention" in sb.CONDITION_WHY[sb.CONDITION_NON_INTERVENTION]
        assert "tokens" in sb.CONDITION_WHY[sb.CONDITION_COST]

    def test_a_verdict_reports_every_condition_separately(self) -> None:
        report = sb.verdict(sb.stage_one_summary(sb.run_stage_one()))
        assert set(report.conditions) == set(sb.VERDICT_CONDITIONS)
        for status, detail in report.conditions.values():
            assert status in (sb.HELD, sb.FAILED, sb.ABSENT)
            assert detail.strip()

    def test_todays_record_is_inconclusive_because_the_live_cells_are_absent(self) -> None:
        report = sb.verdict(sb.stage_one_summary(sb.run_stage_one()))
        assert report.verdict == sb.VERDICT_INCONCLUSIVE
        assert report.conditions[sb.CONDITION_REPORTING][0] == sb.HELD

    def test_an_absent_condition_can_never_produce_an_accept_or_a_reject(self) -> None:
        summary = {"cells": {}, "invalid_episodes": {}, "void_cells": {}, "absent": {}}
        report = sb.verdict(summary)
        assert report.verdict == sb.VERDICT_INCONCLUSIVE

    def test_a_failed_condition_with_nothing_absent_rejects(self) -> None:
        summary = _synthetic(arm_regret=9.0, base_regret=1.0)
        report = sb.verdict(summary)
        assert report.verdict == sb.VERDICT_REJECT

    def test_a_clean_sweep_accepts(self) -> None:
        summary = _synthetic(arm_regret=1.0, base_regret=9.0)
        report = sb.verdict(summary)
        assert report.verdict == sb.VERDICT_ACCEPT, report.to_dict()

    def test_one_authority_violation_is_enough_to_reject(self) -> None:
        summary = _synthetic(arm_regret=1.0, base_regret=9.0, violations=1)
        report = sb.verdict(summary)
        assert report.conditions[sb.CONDITION_AUTHORITY][0] == sb.FAILED
        assert report.verdict == sb.VERDICT_REJECT

    def test_regression_on_the_non_intervention_family_rejects(self) -> None:
        summary = _synthetic(arm_regret=1.0, base_regret=9.0, steady_penalty=20.0)
        report = sb.verdict(summary)
        assert report.conditions[sb.CONDITION_NON_INTERVENTION][0] == sb.FAILED
        assert report.verdict == sb.VERDICT_REJECT

    def test_a_control_that_matches_the_arm_fails_the_token_condition(self) -> None:
        """If A2 — the same architecture with the cheap mind — improves on as many
        families as A3, the gain was the layer and not the strategist."""
        summary = _synthetic(arm_regret=1.0, base_regret=9.0, rival_regret=1.0)
        report = sb.verdict(summary)
        assert report.conditions[sb.CONDITION_COST][0] == sb.FAILED

    def test_an_unexplained_missing_cell_fails_the_reporting_condition(self) -> None:
        """Condition 7 is the one that cannot be satisfied by saying nothing: a
        cell that is neither scored nor explained fails it."""
        summary = _synthetic(arm_regret=1.0, base_regret=9.0)
        dropped = f"{sb.ARM_A3}|{sb.STAGE_TWO}|{ep.FIRST_CYCLE[0]}"
        summary["cells"].pop(dropped)
        assert sb.verdict(summary).conditions[sb.CONDITION_REPORTING][0] == sb.FAILED


def _synthetic(
    *,
    arm_regret: float,
    base_regret: float,
    rival_regret: float = 9.0,
    violations: int = 0,
    steady_penalty: float = 0.0,
) -> dict[str, Any]:
    """A hand-built summary. The verdict rule's own unit fixture.

    Built rather than measured so each condition can be driven independently —
    a rule whose branches are only ever exercised by real data is a rule whose
    branches are mostly never exercised.
    """
    records: list[sb.EpisodeRecord] = []
    plan = [
        (sb.ARM_A0, base_regret, 100),
        (sb.ARM_A1, rival_regret, 100),
        (sb.ARM_A2, rival_regret, 900),
        (sb.ARM_A3, arm_regret, 900),
    ]
    for arm, regret, tokens in plan:
        for stage in sb.STAGES:
            for family in ep.FIRST_CYCLE:
                bump = steady_penalty if family == sb.NON_INTERVENTION_FAMILY else 0.0
                for index in range(ep.MIN_EPISODES_PER_FAMILY):
                    value = regret + bump if arm == sb.ARM_A3 else regret
                    records.append(
                        sb.EpisodeRecord(
                            arm=arm,
                            stage=stage,
                            family=family,
                            episode=f"{family}-{index}",
                            seed=index,
                            planner="synthetic",
                            validity=sb.VALID,
                            outcome={
                                "regret": value,
                                "optimum": 100,
                                "strategic_utility": 100 - value,
                                "operational_success": True,
                            },
                            protocol={"protocol_acceptance": 1.0},
                            authority={
                                "authority_violations": violations if arm == sb.ARM_A3 else 0
                            },
                            cost={"tokens": tokens},
                        )
                    )
    summary = sb.summarise(records)
    summary["absent"] = {}
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 runs offline, completely, and reports its own absences
# ══════════════════════════════════════════════════════════════════════════════


class TestStageOne:
    def test_every_committed_episode_is_run_under_every_scripted_planner(self) -> None:
        records = sb.run_stage_one()
        episodes = {episode.id for episode in ep.first_cycle_episodes()}
        for planner in sub.PLANNER_ORDER:
            covered = {r.episode for r in records if r.arm == sb.control_arm(planner)}
            assert covered == episodes, planner

    def test_the_baseline_arm_has_a_declared_stand_in_and_the_others_do_not(self) -> None:
        assert set(sb.STAGE_ONE_STANDIN) == {sb.ARM_A0}
        assert set(sb.STAGE_ONE_ABSENT) == {sb.ARM_A1, sb.ARM_A2, sb.ARM_A3}
        for reason in sb.STAGE_ONE_ABSENT.values():
            assert reason.strip()

    def test_the_baseline_arm_scores_exactly_its_stand_in(self) -> None:
        records = sb.run_stage_one()
        mine = {r.episode: r.outcome["regret"] for r in records if r.arm == sb.ARM_A0}
        stand = {
            r.episode: r.outcome["regret"]
            for r in records
            if r.arm == sb.control_arm(sub.BASELINE_PLANNER)
        }
        assert mine == stand

    def test_the_oracle_control_has_zero_regret_and_the_no_op_does_not(self) -> None:
        summary = sb.stage_one_summary(sb.run_stage_one())
        for family in ep.FIRST_CYCLE:
            best = summary["cells"][f"{sb.control_arm(sub.PLANNER_ORACLE)}|{sb.STAGE_ONE}|{family}"]
            worst = summary["cells"][f"{sb.control_arm(sub.PLANNER_NONE)}|{sb.STAGE_ONE}|{family}"]
            assert best["mean_regret"] == 0
            assert worst["mean_regret"] > 0

    def test_no_committed_episode_is_invalid(self) -> None:
        summary = sb.stage_one_summary(sb.run_stage_one())
        assert summary["invalid_episodes"] == {}

    def test_every_undialled_cell_is_explained(self) -> None:
        summary = sb.stage_one_summary(sb.run_stage_one())
        declared = {
            f"{arm}|{stage}|{family}"
            for arm in sb.ARM_ORDER
            for stage in sb.STAGES
            for family in ep.FIRST_CYCLE
        }
        scored = {key for key, cell in summary["cells"].items() if cell["n"]}
        assert declared <= scored | set(summary["absent"])


# ══════════════════════════════════════════════════════════════════════════════
# nothing here can dial a model
# ══════════════════════════════════════════════════════════════════════════════


class TestNoLiveDial:
    """``t9``'s hard rule, held structurally rather than by intention."""

    @pytest.mark.parametrize(
        "path", sorted(Path(sb.__file__).parent.glob("*.py")), ids=lambda p: p.name
    )
    def test_no_module_imports_a_transport(self, path: Path) -> None:
        banned = {"socket", "http", "urllib", "httpx", "requests", "aiohttp", "ssl", "asyncio"}
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned, path.name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned, path.name

    @pytest.mark.parametrize(
        "path", sorted(Path(sb.__file__).parent.glob("*.py")), ids=lambda p: p.name
    )
    def test_no_module_reaches_the_actor_loop(self, path: Path) -> None:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != "embodiment.loop", path.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "embodiment.loop", path.name

    @pytest.mark.parametrize(
        "path", sorted(Path(sb.__file__).parent.glob("*.py")), ids=lambda p: p.name
    )
    def test_no_module_introduces_a_timeout_constant(self, path: Path) -> None:
        """Every clock in ``examples`` must be derived from the committed rate
        config (``tests/test_timeout_bounds.py``). The scope scaffold introduces
        none at all, which is the cheapest way to satisfy that."""
        for node in _tree(path).body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(
                node.value.value, (int, float)
            ):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assert not any(
                        hint in target.id for hint in ("TIMEOUT", "DEADLINE", "BACKOFF")
                    ), f"{path.name}:{target.id}"

    def test_the_guard_would_catch_a_planted_transport_import(self) -> None:
        planted = ast.parse("import httpx\n")
        banned = {"httpx"}
        found = [
            alias.name
            for node in ast.walk(planted)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name in banned
        ]
        assert found == ["httpx"]


# ══════════════════════════════════════════════════════════════════════════════
# the CLI runs, offline, from a clean environment
# ══════════════════════════════════════════════════════════════════════════════


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 - fixed argv, this repo's own example
        [sys.executable, str(BENCH_PATH), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )


class TestCli:
    def test_plan_names_every_condition_and_every_family(self) -> None:
        result = _cli("plan")
        assert result.returncode == 0
        for name in sb.VERDICT_CONDITIONS:
            assert sb.CONDITION_WHY[name] in result.stdout
        for family in ep.FAMILY_ORDER:
            assert family in result.stdout

    def test_arms_json_is_the_arm_table(self) -> None:
        result = _cli("arms", "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert set(payload) == set(sb.ARM_ORDER)
        assert payload[sb.ARM_A3]["seats"][sb.SEAT_STRATEGY] == sb.ROLE_CORTEX

    def test_verdict_reports_inconclusive_with_every_absence_named(self) -> None:
        result = _cli("verdict", "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["verdict"] == sb.VERDICT_INCONCLUSIVE
        assert set(payload["conditions"]) == set(sb.VERDICT_CONDITIONS)

    def test_stage1_runs_with_no_network_and_no_credentials(self) -> None:
        result = _cli("stage1")
        assert result.returncode == 0
        assert "deterministic perfect subordinate" in result.stdout
