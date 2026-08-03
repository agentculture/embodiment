"""The non-colleague strategist demo, end to end (task t8, issue #2's DoD).

``examples/scope/greenhouse_scope.py`` cannot import ``embodiment.scoped_run``
(it is not yet on ``embodiment``'s curated public surface -- see that module's
docstring), so THIS file is where the real
:class:`~embodiment.scoped_run.ScopeGovernor` and
:func:`~embodiment.scoped_run.run_scoped` are imported and driven end to end
against the demo's own pieces -- the identical split
``tests/test_scope_seats.py`` already uses for ``seats.governed_by``.

Three acceptance criteria, and this file is where each is pinned:

1. A scripted strategist drives the REAL ``run_scoped()`` and a directive
   changes the actor's behaviour at the very next boundary
   (``TestGovernedDriveChangesActorBehaviour``).
2. ``examples/greenhouse.py`` stays untouched
   (``TestPlainGreenhouseDemoUntouched``).
3. Every after-state property is visible in the demo's OWN rendered output:
   versioned directives and supersession
   (``TestGovernedDriveChangesActorBehaviour``), a host-observable degradation
   (``TestDegradedStrategistContinuesUnderTheDefault``), and the issue #54
   hazard -- a withheld directive never reaching the actor, and the demo
   showing what was APPLIED, never the strategist's own issued chain
   (``TestWithheldDirectiveNeverGovernsTheActor``).
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from embodiment import ledger
from embodiment.scope import ScopeDirective, ScopeResponsibility
from embodiment.scoped_run import (
    TRANSITION_APPLIED,
    TRANSITION_DEFAULT,
    TRANSITION_DEGRADED,
    TRANSITION_HELD,
    TRANSITION_WITHHELD,
    ScopeGovernor,
    run_scoped,
)
from embodiment.strategist_runner import STRATEGIST_ROLE as REAL_STRATEGIST_ROLE
from examples.scope import greenhouse_scope as demo

REPO_ROOT = Path(__file__).resolve().parents[1]
GREENHOUSE_DEMO = REPO_ROOT / "examples" / "greenhouse.py"


# ── the mirror is pinned against the real shapes, not just asserted ──────────


class TestMirroredShapesMatchTheRealOnes:
    """``examples/scope/greenhouse_scope.py`` mirrors real dataclasses (see its
    module docstring on why it cannot import them) -- pinned here so a rename
    on either side fails a test rather than silently drifting apart."""

    def test_directive_fields_match_scopedirective(self) -> None:
        real = tuple(f.name for f in fields(ScopeDirective))
        assert demo.DIRECTIVE_FIELDS == real
        assert tuple(f.name for f in fields(demo.Directive)) == real

    def test_responsibility_fields_match_scoperesponsibility(self) -> None:
        real = tuple(f.name for f in fields(ScopeResponsibility))
        assert demo.RESPONSIBILITY_FIELDS == real
        assert tuple(f.name for f in fields(demo.Responsibility)) == real

    def test_exit_reason_literals_match_the_real_constants(self) -> None:
        from embodiment.scope import SCOPE_EXIT_DIRECTIVE, SCOPE_EXIT_UNCHANGED

        assert demo.EXIT_DIRECTIVE == SCOPE_EXIT_DIRECTIVE
        assert demo.EXIT_UNCHANGED == SCOPE_EXIT_UNCHANGED

    def test_strategist_role_literal_matches_the_real_default(self) -> None:
        assert demo.STRATEGIST_ROLE == REAL_STRATEGIST_ROLE


# ── the tool surface ──────────────────────────────────────────────────────────


class TestGreenhouseRounds:
    def test_check_zone_records_a_reading(self) -> None:
        rounds = demo.GreenhouseRounds()
        outcome = rounds.execute("check_zone", {"zone": demo.ZONE_ORCHID})
        assert f"{demo.ZONE_ORCHID} reads" in outcome.result
        assert rounds.checked[demo.ZONE_ORCHID] == demo.MOISTURE[demo.ZONE_ORCHID]

    def test_an_unknown_zone_is_a_self_correcting_tool_error(self) -> None:
        rounds = demo.GreenhouseRounds()
        with pytest.raises(demo.ToolError):
            rounds.execute("check_zone", {"zone": "compost-heap"})
        assert rounds.failures["compost-heap"] == 1

    def test_tend_zone_records_the_zone_tended(self) -> None:
        rounds = demo.GreenhouseRounds()
        outcome = rounds.execute("tend_zone", {"zone": demo.ZONE_FERN, "action": "checked"})
        assert "tended fern-bed" in outcome.result
        assert rounds.tended == [demo.ZONE_FERN]

    def test_finish_ends_the_round(self) -> None:
        rounds = demo.GreenhouseRounds()
        outcome = rounds.execute("finish", {"summary": "all done"})
        assert outcome.finished is True
        assert outcome.finish_summary == "all done"

    def test_an_unknown_tool_is_a_self_correcting_step(self) -> None:
        rounds = demo.GreenhouseRounds()
        with pytest.raises(demo.UnknownToolError):
            rounds.execute("dig_up_everything", {})


# ── the scripted actor: a pure function of its prompt ─────────────────────────


def _messages(*extra_user_texts: str) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": "you tend a greenhouse"},
        {"role": "user", "content": demo.build_task().instruction},
    ]
    for text in extra_user_texts:
        msgs.append({"role": "user", "content": text})
    return msgs


class TestScriptedActor:
    def test_with_no_directive_it_targets_the_habitual_default(self) -> None:
        response = demo.scripted_actor(_messages())
        assert response.tool_calls[0].name == "check_zone"
        assert response.tool_calls[0].arguments["zone"] == demo.DEFAULT_ZONE

    def test_a_directive_naming_orchid_switches_the_target(self) -> None:
        response = demo.scripted_actor(_messages("put orchid-bed first"))
        assert response.tool_calls[0].arguments["zone"] == demo.ZONE_ORCHID

    def test_the_most_recent_directive_wins(self) -> None:
        response = demo.scripted_actor(_messages("put orchid-bed first", "put fern-bed first"))
        assert response.tool_calls[0].arguments["zone"] == demo.ZONE_FERN

    def test_after_a_reading_it_tends_rather_than_re_checking(self) -> None:
        msgs = _messages()
        msgs.append({"role": "tool", "content": "fern-bed reads 42% moisture"})
        response = demo.scripted_actor(msgs)
        assert response.tool_calls[0].name == "tend_zone"
        assert response.tool_calls[0].arguments["zone"] == demo.ZONE_FERN
        assert response.tool_calls[0].arguments["action"] == "checked"

    def test_a_dry_zone_is_watered_not_merely_checked(self) -> None:
        msgs = _messages("put orchid-bed first")
        msgs.append({"role": "tool", "content": "orchid-bed reads 12% moisture"})
        response = demo.scripted_actor(msgs)
        assert response.tool_calls[0].arguments["action"] == "watered"

    def test_once_tended_it_finishes(self) -> None:
        msgs = _messages()
        msgs.append({"role": "tool", "content": "fern-bed reads 42% moisture"})
        msgs.append({"role": "tool", "content": "tended fern-bed (checked)"})
        response = demo.scripted_actor(msgs)
        assert response.tool_calls[0].name == "finish"
        assert "fern-bed" in response.tool_calls[0].arguments["summary"]

    def test_reverting_to_an_already_read_zone_skips_the_re_check(self) -> None:
        """turn1 read fern; a later directive reverts priority to fern after
        orchid was targeted -- the existing reading is reused, not re-fetched."""
        msgs = _messages("put fern-bed first")
        msgs.append({"role": "tool", "content": "fern-bed reads 42% moisture"})
        msgs.append({"role": "user", "content": "put orchid-bed first"})
        msgs.append({"role": "tool", "content": "orchid-bed reads 12% moisture"})
        msgs.append({"role": "user", "content": "put fern-bed first"})
        response = demo.scripted_actor(msgs)
        assert response.tool_calls[0].name == "tend_zone"
        assert response.tool_calls[0].arguments["zone"] == demo.ZONE_FERN


# ── the scripted strategist: consider/drain/degradations, no thread at all ──


class TestScriptedStrategist:
    def test_a_drain_with_nothing_considered_is_empty(self) -> None:
        strategist = demo.ScriptedStrategist([demo.default_directive()])
        assert strategist.drain() == []

    def test_one_consider_then_drain_yields_the_first_move(self) -> None:
        move = demo.Directive(scope_id="s1", objective="o", version=1)
        strategist = demo.ScriptedStrategist([move])
        strategist.consider(demo.Snapshot(snapshot_id="snap-1"), step_index=1)
        outcomes = strategist.drain(step_count=1)
        assert len(outcomes) == 1
        assert outcomes[0].directive is move
        assert outcomes[0].exit_reason == demo.EXIT_DIRECTIVE
        assert outcomes[0].role == demo.STRATEGIST_ROLE
        assert strategist.issued == [move]

    def test_a_none_move_is_a_scripted_hold(self) -> None:
        strategist = demo.ScriptedStrategist([None])
        strategist.consider(demo.Snapshot(snapshot_id="snap-1"))
        outcomes = strategist.drain()
        assert outcomes[0].directive is None
        assert outcomes[0].exit_reason == demo.EXIT_UNCHANGED

    def test_exhausting_the_script_holds_forever_after(self) -> None:
        move = demo.Directive(scope_id="s1", objective="o", version=1)
        strategist = demo.ScriptedStrategist([move])
        strategist.consider(demo.Snapshot(snapshot_id="snap-1"))
        strategist.drain()
        strategist.consider(demo.Snapshot(snapshot_id="snap-2"))
        outcomes = strategist.drain()
        assert outcomes[0].directive is None
        assert outcomes[0].exit_reason == demo.EXIT_UNCHANGED

    def test_a_second_consider_before_a_drain_displaces_the_first(self) -> None:
        strategist = demo.ScriptedStrategist([None, None])
        strategist.consider(demo.Snapshot(snapshot_id="snap-1"), step_index=1)
        strategist.consider(demo.Snapshot(snapshot_id="snap-2"), step_index=2)
        assert len(strategist.degradations) == 1
        assert strategist.degradations[0].step_index == 1

    def test_start_ok_false_reports_a_failed_start(self) -> None:
        strategist = demo.ScriptedStrategist([], start_ok=False)
        assert strategist.start() is False

    def test_start_ok_true_is_the_default(self) -> None:
        strategist = demo.ScriptedStrategist([])
        assert strategist.start() is True

    def test_degrade_after_stops_the_lane_once_reached(self) -> None:
        strategist = demo.ScriptedStrategist([None], degrade_after=1)
        assert strategist.degradation() is None
        strategist.consider(demo.Snapshot(snapshot_id="snap-1"))
        strategist.drain()
        assert strategist.degradation() is not None

    def test_never_raises_on_a_none_snapshot(self) -> None:
        strategist = demo.ScriptedStrategist([demo.default_directive()])
        strategist.consider(None)
        assert strategist.drain() == []


# ── the projector: built from the demo's own live world state ───────────────


class TestZoneProjector:
    def test_reflects_the_active_directive(self) -> None:
        rounds = demo.GreenhouseRounds()
        project = demo.zone_projector(rounds)

        class _Ctx:
            active = demo.default_directive()
            report = None
            turn_index = 1

        snapshot = project(_Ctx())
        assert snapshot is not None
        assert snapshot.current_directive == "standing-rotation"

    def test_reflects_what_was_actually_checked_and_tended(self) -> None:
        rounds = demo.GreenhouseRounds()
        rounds.execute("check_zone", {"zone": demo.ZONE_FERN})

        class _Ctx:
            active = None
            report = None
            turn_index = 2

        project = demo.zone_projector(rounds)
        snapshot = project(_Ctx())
        assert any("fern-bed: checked=True" in line for line in snapshot.active_workstreams)
        assert any("orchid-bed: checked=False" in line for line in snapshot.active_workstreams)

    def test_snapshots_at_different_turns_are_not_equal(self) -> None:
        rounds = demo.GreenhouseRounds()
        project = demo.zone_projector(rounds)

        class _Ctx1:
            active = None
            report = None
            turn_index = 1

        class _Ctx2:
            active = None
            report = None
            turn_index = 2

        assert project(_Ctx1()) != project(_Ctx2())


# ── seat wiring (task t7) feeding the governor kwargs ────────────────────────


class TestGovernorKwargs:
    def test_a_strategist_is_seated_when_the_cortex_role_resolves(self) -> None:
        rounds = demo.GreenhouseRounds()
        strategist = demo.ScriptedStrategist([])
        kwargs = demo.governor_kwargs(rounds, strategist)
        assert kwargs["strategist"] is strategist

    def test_the_default_scope_is_the_hosts_own(self) -> None:
        rounds = demo.GreenhouseRounds()
        kwargs = demo.governor_kwargs(rounds, None)
        assert kwargs["default_scope"].scope_id == "standing-rotation"

    def test_the_kwargs_build_a_real_armed_governor(self) -> None:
        rounds = demo.GreenhouseRounds()
        kwargs = demo.governor_kwargs(rounds, demo.ScriptedStrategist([]))
        governor = ScopeGovernor(**kwargs)
        assert governor.armed is True


# ── the full drive: the REAL run_scoped(), driven end to end ────────────────


def _observer(collected: list[Any]) -> Any:
    def observe(event: Any) -> None:
        collected.append(event)

    return observe


class TestGovernedDriveChangesActorBehaviour:
    """Acceptance criterion 1 and criterion 3's versioning/supersession half.

    A directive naming ``orchid-bed`` applies at the next boundary and the
    actor's very next tool call changes because of it; a second directive
    supersedes the first and the actor changes again. Every after-state
    property the plan names is asserted directly off the demo's own render.
    """

    def _drive(self) -> tuple[Any, demo.GreenhouseRounds, demo.ScriptedStrategist, list[Any]]:
        rounds = demo.GreenhouseRounds()
        move1 = demo.Directive(
            scope_id="care-orchid-v1",
            supersedes=None,
            objective="the orchid bed's moisture crossed its threshold overnight",
            priorities=("put orchid-bed first",),
            constraints=("never skip a zone's moisture check before tending it",),
            responsibilities=(demo.Responsibility(owner="actor", responsibility="orchid-bed"),),
            success_conditions=("orchid-bed is checked and tended",),
            review_when=("orchid-bed is tended",),
            decision_summary="orchid-bed is critically dry this morning",
            version=1,
        )
        move2 = demo.Directive(
            scope_id="care-fern-v2",
            supersedes="care-orchid-v1",
            objective="return to the standing rotation now that the orchid bed is safe",
            priorities=("put fern-bed first",),
            constraints=("never skip a zone's moisture check before tending it",),
            responsibilities=(demo.Responsibility(owner="actor", responsibility="fern-bed"),),
            success_conditions=("fern-bed is checked and tended",),
            review_when=("a zone's moisture crosses its threshold",),
            decision_summary="orchid-bed handled; fern-bed resumes priority",
            version=2,
        )
        strategist = demo.ScriptedStrategist([move1, move2])
        governor = ScopeGovernor(**demo.governor_kwargs(rounds, strategist))
        events: list[Any] = []
        outcome = run_scoped(
            demo.scripted_actor,
            demo.build_task(),
            executor=rounds,
            max_steps=8,
            governor=governor,
            observer=_observer(events),
            system_prompt="Tend the greenhouse. Check a zone, tend it, then finish.",
        )
        return outcome, rounds, strategist, events

    def test_the_drive_finishes(self) -> None:
        outcome, _rounds, _strategist, _events = self._drive()
        assert outcome.exit_reason == "finished"

    def test_the_actor_checked_both_zones(self) -> None:
        """The behaviour change: the actor's habit alone would never touch
        orchid-bed at all -- it only does because the directive said so."""
        _outcome, rounds, _strategist, _events = self._drive()
        assert demo.ZONE_FERN in rounds.checked
        assert demo.ZONE_ORCHID in rounds.checked

    def test_the_final_tended_zone_reflects_the_superseding_directive(self) -> None:
        _outcome, rounds, _strategist, _events = self._drive()
        assert rounds.tended == [demo.ZONE_FERN]

    def test_the_default_scope_seats_at_version_zero(self) -> None:
        outcome, _rounds, _strategist, _events = self._drive()
        defaulted = [t for t in outcome.transitions if t.kind == TRANSITION_DEFAULT]
        assert [t.version for t in defaulted] == [0]

    def test_versioned_directives_are_applied_in_order(self) -> None:
        outcome, _rounds, _strategist, _events = self._drive()
        applied = [t for t in outcome.transitions if t.kind == TRANSITION_APPLIED]
        assert [t.version for t in applied] == [1, 2]

    def test_the_second_directive_supersedes_the_first(self) -> None:
        outcome, _rounds, _strategist, _events = self._drive()
        applied = [t for t in outcome.transitions if t.kind == TRANSITION_APPLIED]
        supersession = next(t for t in applied if t.scope_id == "care-fern-v2")
        assert supersession.supersedes == "care-orchid-v1"
        assert supersession.previous_version == 1

    def test_a_hold_is_recorded_as_a_real_answer(self) -> None:
        outcome, _rounds, _strategist, _events = self._drive()
        assert any(t.kind == TRANSITION_HELD for t in outcome.transitions)

    def test_active_at_drive_end_is_the_superseding_directive(self) -> None:
        outcome, _rounds, _strategist, _events = self._drive()
        assert outcome.active.scope_id == "care-fern-v2"
        assert outcome.active.version == 2

    def test_scope_events_are_observable_by_the_host(self) -> None:
        _outcome, _rounds, _strategist, events = self._drive()
        kinds = {event.kind for event in events}
        assert "scope.directive.applied" in kinds
        assert "scope.report" in kinds
        assert "scope.snapshot" in kinds

    def test_the_rendered_report_shows_every_after_state_property(self) -> None:
        outcome, _rounds, strategist, events = self._drive()
        text = demo.render_report(outcome, events, issued=strategist.issued)
        assert "care-orchid-v1" in text
        assert "care-fern-v2" in text
        assert "supersedes=care-orchid-v1" in text
        assert f"[{TRANSITION_HELD}]" in text
        assert "active at drive end (RECEIVED, never the register)" in text
        assert "care-fern-v2 (version 2)" in text


class TestDegradedStrategistContinuesUnderTheDefault:
    """Acceptance criterion 3's degradation half: a failing strategist never
    stops the drive, and the failure is host-observable."""

    def _drive(self) -> tuple[Any, demo.GreenhouseRounds, list[Any]]:
        rounds = demo.GreenhouseRounds()
        strategist = demo.ScriptedStrategist([demo.default_directive()], start_ok=False)
        governor = ScopeGovernor(**demo.governor_kwargs(rounds, strategist))
        events: list[Any] = []
        outcome = run_scoped(
            demo.scripted_actor,
            demo.build_task(),
            executor=rounds,
            max_steps=8,
            governor=governor,
            observer=_observer(events),
        )
        return outcome, rounds, events

    def test_the_drive_still_completes(self) -> None:
        outcome, _rounds, _events = self._drive()
        assert outcome.exit_reason == "finished"

    def test_the_actor_never_leaves_the_default_zone(self) -> None:
        _outcome, rounds, _events = self._drive()
        assert list(rounds.checked) == [demo.ZONE_FERN]

    def test_a_degradation_transition_is_recorded(self) -> None:
        outcome, _rounds, _events = self._drive()
        kinds = {t.kind for t in outcome.transitions}
        assert TRANSITION_DEGRADED in kinds
        assert TRANSITION_DEFAULT in kinds

    def test_the_degradation_is_observable_by_the_host(self) -> None:
        _outcome, _rounds, events = self._drive()
        kinds = {event.kind for event in events}
        assert "scope.degradation" in kinds

    def test_the_rendered_report_names_the_degradation(self) -> None:
        outcome, _rounds, events = self._drive()
        text = demo.render_report(outcome, events)
        assert f"[{TRANSITION_DEGRADED}]" in text


class TestWithheldDirectiveNeverGovernsTheActor:
    """The issue #54 hazard, made concrete: a version-tied directive is
    withheld and never reaches the actor, even though the strategist's own
    issued chain names it. The demo shows ``outcome.active``, never that
    issued chain."""

    def _drive(self) -> tuple[Any, demo.GreenhouseRounds, demo.ScriptedStrategist]:
        rounds = demo.GreenhouseRounds()
        move1 = demo.Directive(
            scope_id="care-fern-v1",
            supersedes=None,
            objective="reaffirm the standing rotation",
            priorities=("put fern-bed first",),
            responsibilities=(demo.Responsibility(owner="actor", responsibility="fern-bed"),),
            success_conditions=("fern-bed is checked and tended",),
            review_when=("a zone's moisture crosses its threshold",),
            decision_summary="no change of priority yet",
            version=1,
        )
        stray = demo.Directive(
            scope_id="stray-orchid-v1",
            supersedes=None,
            objective="a stray re-issue naming a different zone at the same version",
            priorities=("put orchid-bed first",),
            responsibilities=(demo.Responsibility(owner="actor", responsibility="orchid-bed"),),
            success_conditions=("orchid-bed is checked and tended",),
            review_when=("orchid-bed is tended",),
            decision_summary="this must never reach the actor",
            version=1,
        )
        strategist = demo.ScriptedStrategist([move1, stray])
        governor = ScopeGovernor(**demo.governor_kwargs(rounds, strategist))
        outcome = run_scoped(
            demo.scripted_actor,
            demo.build_task(),
            executor=rounds,
            max_steps=8,
            governor=governor,
        )
        return outcome, rounds, strategist

    def test_the_stray_directive_was_issued_by_the_strategist(self) -> None:
        _outcome, _rounds, strategist = self._drive()
        assert strategist.issued[-1].scope_id == "stray-orchid-v1"

    def test_but_it_never_governed_the_actor(self) -> None:
        outcome, _rounds, _strategist = self._drive()
        assert outcome.active.scope_id != "stray-orchid-v1"
        assert outcome.active.scope_id == "care-fern-v1"

    def test_the_actor_never_acted_on_the_withheld_zone(self) -> None:
        _outcome, rounds, _strategist = self._drive()
        assert demo.ZONE_ORCHID not in rounds.checked

    def test_the_withholding_is_recorded(self) -> None:
        outcome, _rounds, _strategist = self._drive()
        withheld = [t for t in outcome.transitions if t.kind == TRANSITION_WITHHELD]
        assert withheld
        assert any("does not advance" in t.reason for t in withheld)

    def test_the_rendered_report_shows_applied_not_issued(self) -> None:
        outcome, _rounds, strategist = self._drive()
        text = demo.render_report(outcome, issued=strategist.issued)
        assert "active at drive end (RECEIVED, never the register): care-fern-v1" in text
        assert "strategist's last ISSUED directive" in text
        assert "stray-orchid-v1" in text
        # The two lines disagree ON PURPOSE -- that disagreement is the hazard.
        applied_line = next(line for line in text.splitlines() if line.startswith("active at"))
        issued_line = next(line for line in text.splitlines() if "ISSUED directive" in line)
        assert "care-fern-v1" in applied_line
        assert "stray-orchid-v1" in issued_line


class TestLedgerFoldsScopeDegradations:
    """``embodiment.ledger.read(scope=...)`` folds a strategist lane's OWN
    ledger through the public surface -- exercised for real, not asserted."""

    def test_a_displaced_snapshot_folds_into_the_unified_ledger(self) -> None:
        strategist = demo.ScriptedStrategist([None, None])
        strategist.consider(demo.Snapshot(snapshot_id="snap-1"), step_index=1)
        strategist.consider(demo.Snapshot(snapshot_id="snap-2"), step_index=2)
        records = ledger.read(scope=strategist.degradations)
        assert len(records) == 1
        assert records[0].code == "scripted-strategist-snapshot-displaced"


# ── acceptance criterion 2: the plain demo is untouched ──────────────────────


class TestPlainGreenhouseDemoUntouched:
    def test_examples_greenhouse_py_names_no_scope_lane_identifier(self) -> None:
        """The actor-only demo stays actor-only: no scope-lane name appears."""
        source = GREENHOUSE_DEMO.read_text(encoding="utf-8")
        for needle in ("ScopeGovernor", "run_scoped", "ScopeDirective", "strategist_runner"):
            assert needle not in source, f"greenhouse.py must stay actor-only: found {needle!r}"


# ── no live model call anywhere in the demo module ───────────────────────────


class TestNoLiveDial:
    def test_greenhouse_scope_imports_no_transport(self) -> None:
        import ast

        source = (REPO_ROOT / "examples" / "scope" / "greenhouse_scope.py").read_text(
            encoding="utf-8"
        )
        banned = {"socket", "http", "urllib", "httpx", "requests", "aiohttp", "ssl", "threading"}
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned
