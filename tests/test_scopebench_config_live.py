"""Cycle 2's live harness, tested without a socket (plan task ``t14``).

``t13`` committed the cycle-2 pre-registration
(``docs/live-test-results/scopebench-config-preregistration.md``) and declared
the arms as data. It could not make them dialable: the live harness lives
outside ``examples/scope/`` so that folder stays hermetic, and ``t13``'s brief
scoped it to that folder. This module tests the extension that closes the gap —
Stage 2, the config lane, the fifth record axis, the two config validity gates
and the admission pilot.

The seam is never real. Every test builds a genuine
:class:`examples.worker_seam.WorkerSeam` and replaces its ``_post``, or hands a
plain callable where the package asks for one. Nothing here opens a socket, and
nothing here is a result: the graded series is the operator's to run.

What is deliberately NOT tested here is anything already pinned by
``tests/test_scopebench_live.py`` (cycle 1's Stage-1 lane, untouched) or by
``tests/test_scopebench.py`` (the hermetic scaffold, unedited by this task).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment.config_change import CHANGE_TARGETS, ORIGIN_STRATEGIST  # noqa: E402
from embodiment.config_lifecycle import compose_prompt  # noqa: E402
from embodiment.config_review import CONFIG_AUTHORITY, MARKER_HOLD  # noqa: E402
from examples import scopebench_live as sl  # noqa: E402
from examples import worker_seam as ws  # noqa: E402
from examples.scope import episodes as ep  # noqa: E402
from examples.scope import oracle as orc  # noqa: E402
from examples.scope import scopebench as sb  # noqa: E402
from examples.scope import subordinate as sub  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "examples" / "scopebench_live.py"


# ── doubles ──────────────────────────────────────────────────────────────────


@pytest.fixture(name="episode")
def _episode() -> ep.Episode:
    return ep.first_cycle_episodes()[0]


def _allocation_reply(episode: ep.Episode) -> str:
    """A readable allocation: the episode's own default, echoed back."""
    return json.dumps(
        {
            "responsibilities": [
                {"owner": owner, "responsibility": target}
                for owner, target in episode.default_allocation
            ]
        }
    )


class _Canned:
    """A seam-shaped callable that replays canned strings, then repeats the last."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]], **_kwargs: Any) -> Any:
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return sl.ws.ModelResponse(
            content=self.replies[index], prompt_tokens=11, completion_tokens=7
        )


class _CannedActor(ws.WorkerSeam):
    """A real ``WorkerSeam`` whose ``_post`` answers with a canned completion."""

    def __init__(self, replies: list[str], **kwargs: Any) -> None:
        super().__init__(
            base_url="http://localhost:9/v1",
            model="canned",
            api_key="k",
            max_tokens=sl.ACTOR_MAX_TOKENS,
            **kwargs,
        )
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        index = min(len(self.bodies) - 1, len(self.replies) - 1)
        return {
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": self.replies[index]},
                }
            ],
            "usage": {"prompt_tokens": 13, "completion_tokens": 5},
        }


def _prompt_change(index: int, text: str = "State the reading you used.") -> str:
    return json.dumps(
        {
            "changes": [
                {
                    "change_id": f"cfg-{index}",
                    "target": "worker.prompts",
                    "origin": ORIGIN_STRATEGIST,
                    "section": f"section-{index}",
                    "text": text,
                    "reason": "the acting seat left an actor idle with work outstanding",
                }
            ]
        }
    )


def _lane(complete: Optional[Any] = None) -> sl.ConfigLane:
    return sl.ConfigLane(complete=complete, role=sb.ROLE_CORTEX, model="canned")


# ── the arms are dialable at all: requirement 1 ──────────────────────────────


class TestTheCycleTwoArmsAreDialable:
    """``d8``'s whole blocker: ``LIVE_ARMS`` did not contain the arm under test."""

    def test_live_arms_is_the_committed_dial_order(self) -> None:
        assert sl.LIVE_ARMS == sb.CYCLE_TWO_ARMS

    def test_the_arm_under_test_can_be_dialled(self) -> None:
        assert sb.ARM_A4 in sl.LIVE_ARMS

    def test_cycle_ones_stage_one_arms_stay_reachable(self) -> None:
        """Cycle 1 is closed, not deleted: its Stage-1 series stays reproducible."""
        assert sl.STAGE_ONE_ARMS == (sb.ARM_A2, sb.ARM_A3)

    def test_the_seated_arms_are_exactly_the_arms_with_a_strategist(self) -> None:
        assert sl.SEATED_ARMS == tuple(
            arm for arm in sb.CYCLE_TWO_ARMS if sb.ARMS[arm].has_strategist
        )

    def test_every_cycle_two_arm_is_a_stage_two_verb_choice(self) -> None:
        parser = sl.build_parser()
        text = parser.format_help()
        assert "stage2" in text
        assert "pilot" in text


# ── clocks: requirement 6 ────────────────────────────────────────────────────


class TestTheHarnessStillIntroducesNoClock:
    """Every bound in front of a dial is ``worker_seam``'s, derived from the rates."""

    @staticmethod
    def _module_level_numbers() -> list[str]:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        found: list[str] = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            if isinstance(node.value.value, (int, float)):
                found.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        return found

    def test_no_module_level_timeout_literal(self) -> None:
        hits = [
            name
            for name in self._module_level_numbers()
            if any(hint in name for hint in ("TIMEOUT", "DEADLINE", "BACKOFF", "SLEEP", "WAIT"))
        ]
        assert hits == []

    def test_the_review_wait_bound_is_derived_from_the_seams_own_bounds(self) -> None:
        assert sl.REVIEW_WAIT_TIMEOUT == sl.CONFIG_REVIEW_TURNS * ws.STREAM_TOTAL_TIMEOUT

    def test_the_review_wait_bound_is_not_shorter_than_one_bounded_call(self) -> None:
        assert sl.REVIEW_WAIT_TIMEOUT >= ws.STREAM_TOTAL_TIMEOUT

    def test_the_actor_budget_is_deviation_d16s_measured_floor(self) -> None:
        assert sl.ACTOR_MAX_TOKENS == 16000
        assert "ACTOR_MAX_TOKENS" in self._module_level_numbers()

    def test_the_actor_samples_exactly_as_the_strategist_does(self) -> None:
        assert sl.ACTOR_TEMPERATURE == sl.STRATEGIST_TEMPERATURE

    def test_streaming_stays_the_transport(self) -> None:
        assert ws.DEFAULT_STREAM is True


# ── the actor: how it decides, and where its answer may NOT land ─────────────


class TestReadAllocation:
    def test_a_batch_of_pairs_is_read(self) -> None:
        pairs, kind, _detail = sl.read_allocation(
            '{"responsibilities": [{"owner": "a", "responsibility": "w"}]}'
        )
        assert kind == sl.REPLY_ALLOCATION
        assert pairs == (("a", "w"),)

    def test_prose_around_the_object_does_not_stop_it(self) -> None:
        pairs, kind, _ = sl.read_allocation(
            'Here is my plan.\n{"responsibilities": [{"owner": "a", "responsibility": "-"}]}'
        )
        assert kind == sl.REPLY_ALLOCATION
        assert pairs == (("a", "-"),)

    def test_an_unreadable_reply_yields_no_pairs(self) -> None:
        pairs, kind, detail = sl.read_allocation("I would rather not say.")
        assert kind == sl.REPLY_UNREADABLE
        assert pairs == ()
        assert detail

    def test_an_empty_reply_is_unreadable_and_says_so(self) -> None:
        _pairs, kind, detail = sl.read_allocation("")
        assert kind == sl.REPLY_UNREADABLE
        assert "no content" in detail

    def test_a_well_formed_object_with_no_responsibilities_is_still_read(self) -> None:
        pairs, kind, _ = sl.read_allocation('{"responsibilities": []}')
        assert kind == sl.REPLY_ALLOCATION
        assert pairs == ()


class TestTheActorsAnswerNeverTouchesTheProtocolAxis:
    """§9's table: for ``A0``/``A1`` nothing is offered, so nothing can void."""

    def test_an_ungoverned_episode_offers_nothing(self, episode: ep.Episode) -> None:
        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, _calls = sl.play_episode(episode, actor)
        assert rollout.offered == 0
        assert rollout.accepted == 0
        assert rollout.holds == 0

    def test_the_plan_is_the_actors_own(self, episode: ep.Episode) -> None:
        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, _calls = sl.play_episode(episode, actor)
        assert len(rollout.plan) == len(episode.review_ticks)

    def test_an_unreadable_actor_reply_is_recorded_not_scored_as_an_offer(
        self, episode: ep.Episode
    ) -> None:
        actor = sl.LiveActor(seam=_CannedActor(["nope"]), episode=episode, system_prompt="s")
        rollout, calls = sl.play_episode(episode, actor)
        assert rollout.offered == 0
        assert all(call.kind == sl.REPLY_UNREADABLE for call in calls)

    def test_an_unexecutable_pair_lands_on_protocol_never_on_outcome(
        self, episode: ep.Episode
    ) -> None:
        reply = json.dumps(
            {"responsibilities": [{"owner": "nobody-at-all", "responsibility": "x"}]}
        )
        actor = sl.LiveActor(seam=_CannedActor([reply]), episode=episode, system_prompt="s")
        rollout, _calls = sl.play_episode(episode, actor)
        assert rollout.unexecutable
        assert rollout.offered == 0

    def test_a_dead_actor_seam_abandons_the_episode(self, episode: ep.Episode) -> None:
        class _Dead(_CannedActor):
            def _post(self, body: dict[str, Any]) -> dict[str, Any]:
                raise ws.WorkerTransportError("no route to host")

        actor = sl.LiveActor(seam=_Dead(["x"]), episode=episode, system_prompt="s")
        with pytest.raises(sl.ActorUnavailable):
            sl.play_episode(episode, actor)


class TestTheAdvisoryLaneStillOffersDirectives:
    """``A3`` at Stage 2: the strategist offers, the actor allocates."""

    def test_the_strategists_directive_is_the_offer(self, episode: ep.Episode) -> None:
        payload = sub.directive_payload(
            episode,
            episode.default_allocation,
            scope_id="live-1",
            supersedes=f"{episode.id}-default",
            version=1,
            objective="hold the line",
            summary="s",
        )

        def advisory(_context: sub.PlannerContext) -> dict[str, Any]:
            return payload

        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, _calls = sl.play_episode(episode, actor, advisory=advisory)
        assert rollout.offered == len(episode.review_ticks)
        # the second and third offers repeat a scope_id, which the register refuses
        assert rollout.accepted == 1

    def test_a_hold_is_a_hold_and_not_an_offer(self, episode: ep.Episode) -> None:
        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, _calls = sl.play_episode(episode, actor, advisory=lambda _c: None)
        assert rollout.holds == len(episode.review_ticks)
        assert rollout.offered == 0

    def test_the_actor_is_shown_the_standing_scope(self, episode: ep.Episode) -> None:
        seam = _CannedActor([_allocation_reply(episode)])
        actor = sl.LiveActor(seam=seam, episode=episode, system_prompt="s")
        sl.play_episode(episode, actor)
        user = seam.bodies[0]["messages"][-1]["content"]
        assert f"{episode.id}-default" in user


# ── the seat configuration the actor runs under ──────────────────────────────


class TestTheSeededBaselineIsOneSourceOfTheActorsPrompt:
    """Traps ``T3`` and ``T8``: seed through the gate, never the constructor."""

    def test_the_seed_lands_through_the_gate(self) -> None:
        lane = _lane()
        assert lane.lifecycle.effective(sl.ACTOR_SEAT).prompt

    def test_the_ledger_explains_the_whole_configuration(self) -> None:
        """Trap ``T8``: a constructor-seeded baseline makes the digests disagree."""
        lane = _lane()
        assert lane.ledger_sha() == lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha
        assert lane.ledger_sha()

    def test_the_baseline_is_captured_after_the_seed(self) -> None:
        lane = _lane()
        anchor = lane.baseline.get(sl.ACTOR_SEAT)
        assert anchor is not None
        assert anchor.config_sha == lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha

    def test_the_actor_prompt_is_the_composed_seat_configuration(self) -> None:
        lane = _lane()
        run = lane.begin(0)
        assert sl.actor_system_prompt(run.config) == compose_prompt(run.config)
        lane.end(run)

    def test_every_arm_starts_from_a_byte_identical_baseline(self) -> None:
        assert _lane().ledger_sha() == _lane(_Canned([MARKER_HOLD])).ledger_sha()

    def test_an_unstrategised_lane_wires_no_runner(self) -> None:
        assert _lane().runner is None

    def test_a_strategised_lane_disables_the_cadence_gap(self) -> None:
        """Trap ``T2``: the default of 2 measured one review across six drives."""
        lane = _lane(_Canned([MARKER_HOLD]))
        assert sl.CONFIG_REVIEW_GAP == 0
        assert lane.runner is not None
        lane.close()


class TestTheSeatIdleGateHolds:
    def test_nothing_applies_while_the_seat_has_a_run_open(self) -> None:
        lane = _lane()
        run = lane.begin(0)
        before = lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha
        lane.lifecycle.propose(json.loads(_prompt_change(1))["changes"][0])
        lane.lifecycle.advance()
        assert lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha == before
        assert lane.lifecycle.deferrals
        lane.end(run)

    def test_it_lands_once_the_seat_is_idle(self) -> None:
        lane = _lane()
        run = lane.begin(0)
        lane.lifecycle.propose(json.loads(_prompt_change(1))["changes"][0])
        before = lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha
        lane.end(run)
        report = lane.lifecycle.advance()
        assert report.applied
        assert lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha != before


class TestABoundaryReviewProposesVerifiesAppliesAndRatchets:
    """The whole config lane, driven the way the series drives it."""

    @staticmethod
    def _reviewed(replies: list[str]) -> tuple[sl.ConfigLane, sl.ReviewTally]:
        lane = _lane(_Canned(replies))
        run = lane.begin(0)
        lane.end(run)
        tally = lane.review(sl.blank_snapshot("s1"), step_index=1)
        return lane, tally

    def test_a_change_unit_is_offered_and_accepted(self) -> None:
        lane, tally = self._reviewed([_prompt_change(1)])
        assert tally.units_offered == 1
        assert tally.units_accepted == 1
        lane.close()

    def test_it_is_applied(self) -> None:
        lane, tally = self._reviewed([_prompt_change(1)])
        assert tally.applied == ("cfg-1",)
        lane.close()

    def test_the_ratchet_is_checked_once_per_apply(self) -> None:
        lane, tally = self._reviewed([_prompt_change(1)])
        assert tally.ratchet_checks == 1
        assert tally.ratchet_failures == 0
        lane.close()

    def test_the_ratchet_compares_against_the_fixed_baseline(self) -> None:
        lane, tally = self._reviewed([_prompt_change(1)])
        anchor = lane.baseline.get(sl.ACTOR_SEAT)
        assert anchor is not None
        assert tally.ratchet_baseline_sha == anchor.config_sha
        assert tally.ratchet_drifted is True
        lane.close()

    def test_a_hold_is_a_real_answer_and_applies_nothing(self) -> None:
        lane, tally = self._reviewed([MARKER_HOLD])
        assert tally.holds == 1
        assert tally.applied == ()
        assert tally.units_offered == 0
        lane.close()

    def test_a_review_that_never_ran_is_visible_as_such(self) -> None:
        """``T1``'s lesson: a lane that reviewed nothing must not read as healthy."""
        lane, tally = self._reviewed([MARKER_HOLD])
        assert tally.reviews_started >= 1
        lane.close()

    def test_the_ledger_keeps_explaining_the_configuration_after_an_apply(self) -> None:
        lane, _tally = self._reviewed([_prompt_change(1)])
        assert lane.ledger_sha() == lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha
        lane.close()

    def test_a_verification_failure_is_not_a_protocol_failure(self) -> None:
        """A unit that is admissible and fails the suite is a gate judgement."""
        lane = _lane(_Canned([_prompt_change(1, "x" * (sl.PROMPT_GROWTH_LIMIT + 50))]))
        run = lane.begin(0)
        lane.end(run)
        tally = lane.review(sl.blank_snapshot("s1"), step_index=1)
        assert tally.units_accepted == 1
        assert tally.units_refused == 0
        assert tally.failed_verification == ("cfg-1",)
        assert tally.applied == ()
        lane.close()

    def test_revert_is_exercised_even_when_nothing_was_applied(self) -> None:
        lane, _tally = self._reviewed([MARKER_HOLD])
        outcome = lane.revert()
        assert outcome is not None
        assert outcome.matches_baseline is True
        assert lane.lifecycle.effective(sl.ACTOR_SEAT).config_sha == lane.baseline_sha()
        lane.close()


class TestRevertCannotMatchAFixedBaselineBitForBit:
    """**A pre-dial finding, recorded rather than smoothed over.**

    §7's condition-8 second clause reads: *at the end of each family run,
    ``revert_to_baseline`` is exercised and the restored ``config_sha`` is
    compared to the baseline's.* The shipped lane cannot satisfy that comparison
    once a **new prompt section** exists, and the reason is documented and
    deliberate in two package modules this task must not edit:

    * ``config_lifecycle._apply_prompt`` — "Empty text keeps the section
      *declared*"; there is no delete verb, by construction (task ``t4``).
    * ``config_revert`` — a section introduced beyond the baseline is cleared to
      empty text, ``compose_prompt`` filters it so the seat's **behaviour**
      matches baseline exactly, but the row survives in ``canonical_text`` and
      therefore in ``config_sha``. ``residual_prompt_sections`` names it and
      ``matches_baseline`` reads ``False`` rather than claiming an exact match.

    So the harness records the pre-registered comparison **faithfully**, which
    means a config-lane family whose strategist writes any new prompt section
    reports ``revert_restored == False`` and condition 8 **FAILS** — on a
    digest, not on drift. That is an operator decision (an amendment under §19,
    made before any committed record exists, or an accepted FAILED), and these
    tests exist so the decision is made against a demonstrated fact.
    """

    @staticmethod
    def _reverted() -> tuple[sl.ConfigLane, Any]:
        lane = _lane(_Canned([_prompt_change(1)]))
        run = lane.begin(0)
        lane.end(run)
        lane.review(sl.blank_snapshot("s1"), step_index=1)
        return lane, lane.revert()

    def test_the_seats_composed_prompt_is_restored_exactly(self) -> None:
        lane, _outcome = self._reverted()
        anchor = lane.baseline.get(sl.ACTOR_SEAT)
        assert anchor is not None
        assert compose_prompt(lane.lifecycle.effective(sl.ACTOR_SEAT)) == compose_prompt(anchor)
        lane.close()

    def test_but_the_digest_does_not_match_and_the_lane_says_so(self) -> None:
        lane, outcome = self._reverted()
        assert outcome.matches_baseline is False
        assert outcome.residual_prompt_sections == ("section-1",)
        lane.close()

    def test_the_record_reports_the_pre_registered_comparison_faithfully(self) -> None:
        lane, outcome = self._reverted()
        block = sl.ratchet_block(sl.ReviewTally(), actor_sha="a", ledger_sha="a", revert=outcome)
        assert block["revert_attempted"] is True
        assert block["revert_restored"] is False
        lane.close()

    def test_the_residual_rides_the_record_so_the_reason_is_readable(self) -> None:
        lane, outcome = self._reverted()
        assert "section-1" in json.dumps(sl.revert_detail(outcome))
        lane.close()

    def test_the_two_facts_are_recorded_separately(self) -> None:
        """The digest and the prompt bytes are different claims, both recorded."""
        lane, outcome = self._reverted()
        detail = sl.revert_detail(outcome, compose_restored=lane.compose_matches_baseline())
        assert detail is not None
        assert detail["matches_baseline"] is False
        assert detail["compose_prompt_restored"] is True
        assert sl.REVERT_DIGEST_NOTE in detail["note"]
        lane.close()


class TestTheReportNamesWhichArmsItSeeksAVerdictFor:
    """Condition 5's rivals include ``A2``, which cycle 2 declares not dialled."""

    def test_only_the_config_arms_are_verdict_arms(self) -> None:
        assert sl.VERDICT_ARMS == (sb.ARM_A4, sb.ARM_A5)

    def test_every_verdict_arm_is_in_the_dial_order(self) -> None:
        assert set(sl.VERDICT_ARMS) <= set(sb.CYCLE_TWO_ARMS)

    def test_every_verdict_arms_rivals_are_dialled_this_cycle(self) -> None:
        """The property that makes ``A4``'s condition 5 evaluable at all."""
        for arm in sl.VERDICT_ARMS:
            rivals = set(sb.CONDITION_COST_RIVALS[sb.ARMS[arm].lane]) - {arm}
            assert rivals <= set(sb.CYCLE_TWO_ARMS), arm

    def test_a_comparator_arms_rivals_are_not(self) -> None:
        """Which is why ``A0``/``A1``/``A3`` are INCONCLUSIVE by construction."""
        rivals = set(sb.CONDITION_COST_RIVALS[sb.LANE_ADVISORY])
        assert sb.ARM_A2 in rivals
        assert sb.ARM_A2 not in sb.CYCLE_TWO_ARMS


class TestTheSnapshotIsHostFactAndNeverTheOracle:
    """A projection carrying the grader would be the harness scoring itself."""

    @staticmethod
    def _snapshot(episode: ep.Episode) -> Any:
        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, calls = sl.play_episode(episode, actor)
        lane = _lane()
        return sl.episode_snapshot(episode, rollout, calls, lane.lifecycle.effective(sl.ACTOR_SEAT))

    def test_it_never_names_the_optimum(self, episode: ep.Episode) -> None:
        text = json.dumps(self._snapshot(episode).to_dict())
        assert "optimum" not in text
        assert "regret" not in text

    def test_it_carries_what_the_seat_actually_did(self, episode: ep.Episode) -> None:
        assert self._snapshot(episode).observations

    def test_it_carries_the_seats_current_configuration(self, episode: ep.Episode) -> None:
        assert self._snapshot(episode).seats

    def test_it_declares_the_capability_surface_it_actually_has(self, episode: ep.Episode) -> None:
        assert self._snapshot(episode).capabilities == sl.build_catalog().ids


# ── the fifth record axis: requirement 3 ─────────────────────────────────────


def _ratchet(**kwargs: Any) -> dict[str, Any]:
    return sb.Ratchet(**kwargs).to_dict()


class TestTheRatchetAxisIsPopulated:
    def test_every_declared_key_is_present(self) -> None:
        tally = sl.ReviewTally()
        block = sl.ratchet_block(tally, actor_sha="a", ledger_sha="a")
        assert set(block) == set(sb.RATCHET_KEYS)

    def test_the_two_digests_are_recorded_as_two_facts(self) -> None:
        block = sl.ratchet_block(sl.ReviewTally(), actor_sha="a", ledger_sha="b")
        assert block["actor_config_sha"] == "a"
        assert block["ledger_config_sha"] == "b"

    def test_an_arm_outside_the_config_lane_records_an_empty_ratchet(self) -> None:
        block = sl.ratchet_block(None, actor_sha="a", ledger_sha="a")
        assert block["ratchet_checks"] == 0
        assert block["changes_applied"] == 0


class TestConditionEightReadsTheAxis:
    """The load-bearing clause: zero checks is ``ABSENT``, never "it held"."""

    @staticmethod
    def _summary(**ratchet: Any) -> dict[str, Any]:
        record = sb.EpisodeRecord(
            arm=sb.ARM_A4,
            stage=sb.STAGE_TWO,
            family=ep.FIRST_CYCLE[0],
            episode="contention-1",
            seed=1,
            planner="live",
            validity=sb.VALID,
            outcome={
                "regret": 1,
                "optimum": 10,
                "strategic_utility": 9,
                "operational_success": True,
            },
            protocol={"protocol_acceptance": 1.0},
            authority={"authority_violations": 0},
            cost={"tokens": 10},
            ratchet=_ratchet(**ratchet),
        )
        return sb.summarise([record])

    def test_zero_checks_is_absent(self) -> None:
        report = sb.verdict(self._summary(), sb.ARM_A4, conditions=sb.CONFIG_VERDICT_CONDITIONS)
        assert report.conditions[sb.CONDITION_RATCHET][0] == sb.ABSENT

    def test_zero_checks_forces_inconclusive(self) -> None:
        report = sb.verdict(self._summary(), sb.ARM_A4, conditions=sb.CONFIG_VERDICT_CONDITIONS)
        assert report.verdict == sb.VERDICT_INCONCLUSIVE

    def test_a_check_that_ran_and_held_is_held(self) -> None:
        report = sb.verdict(
            self._summary(
                ratchet_checks=2,
                changes_applied=2,
                revert_attempted=True,
                revert_restored=True,
            ),
            sb.ARM_A4,
            conditions=sb.CONFIG_VERDICT_CONDITIONS,
        )
        assert report.conditions[sb.CONDITION_RATCHET][0] == sb.HELD

    def test_a_ratchet_failure_fails_the_condition(self) -> None:
        report = sb.verdict(
            self._summary(ratchet_checks=1, ratchet_failures=1, changes_applied=1),
            sb.ARM_A4,
            conditions=sb.CONFIG_VERDICT_CONDITIONS,
        )
        assert report.conditions[sb.CONDITION_RATCHET][0] == sb.FAILED


# ── the two config validity gates: requirement 5 ─────────────────────────────


class TestTheConfigValidityGates:
    def test_nothing_applied_and_no_hold_voids_the_cell(self) -> None:
        verdict = sb.validity_of(
            (),
            {"protocol_acceptance": 1.0, "holds": 0},
            _ratchet(actor_config_sha="a", ledger_config_sha="a"),
            sb.LANE_CONFIG,
        )
        assert verdict == sb.VOID_NO_CONFIG_EFFECT

    def test_a_deliberate_hold_keeps_the_cell_valid(self) -> None:
        verdict = sb.validity_of(
            (),
            {"protocol_acceptance": 1.0, "holds": 1},
            _ratchet(actor_config_sha="a", ledger_config_sha="a"),
            sb.LANE_CONFIG,
        )
        assert verdict == sb.VALID

    def test_a_digest_disagreement_is_undelivered(self) -> None:
        verdict = sb.validity_of(
            (),
            {"protocol_acceptance": 1.0, "holds": 1},
            _ratchet(actor_config_sha="a", ledger_config_sha="b"),
            sb.LANE_CONFIG,
        )
        assert verdict == sb.VOID_UNDELIVERED

    def test_an_unrecorded_digest_fails_closed(self) -> None:
        verdict = sb.validity_of(
            (),
            {"protocol_acceptance": 1.0, "holds": 1},
            _ratchet(),
            sb.LANE_CONFIG,
        )
        assert verdict == sb.VOID_UNDELIVERED

    def test_the_harness_records_the_two_digests_from_two_sources(self) -> None:
        """One comes off the pinned ``SeatRun``, the other off the ledger alone."""
        lane = _lane()
        run = lane.begin(0)
        block = sl.ratchet_block(None, actor_sha=run.config_sha, ledger_sha=lane.ledger_sha())
        lane.end(run)
        assert block["actor_config_sha"] == block["ledger_config_sha"]

    def test_an_advisory_arm_is_never_reached_by_the_config_gates(self) -> None:
        assert (
            sb.validity_of(
                (), {"protocol_acceptance": 1.0, "holds": 0}, _ratchet(), sb.LANE_ADVISORY
            )
            == sb.VALID
        )


# ── the admission pilot: requirement 4 ───────────────────────────────────────


class TestTheAdmissionPilot:
    def test_the_pilot_length_is_the_committed_one(self) -> None:
        assert sl.PILOT_EPISODES == sb.ADMISSION_PILOT_EPISODES

    def test_the_pilot_writes_outside_the_committed_results(self) -> None:
        assert sl.RESULTS_DIR not in sl.PILOT_DIR.parents
        assert sl.REPO_ROOT not in sl.PILOT_DIR.parents

    def test_an_arm_that_clears_the_floor_is_admitted(self) -> None:
        verdict = sl.pilot_verdict(sb.ARM_A4, offered=10, accepted=9, episodes=3)
        assert verdict["verdict"] == sl.PILOT_ADMITTED

    def test_an_arm_below_the_floor_is_declared_void_in_advance(self) -> None:
        verdict = sl.pilot_verdict(sb.ARM_A4, offered=10, accepted=5, episodes=3)
        assert verdict["verdict"] == sl.PILOT_VOID_PROTOCOL
        assert verdict["reason"]

    def test_the_floor_is_the_committed_one_and_identical_for_every_arm(self) -> None:
        assert sl.pilot_verdict(sb.ARM_A4, offered=10, accepted=8, episodes=3)["floor"] == (
            sb.PROTOCOL_FLOOR
        )
        assert sl.pilot_verdict(sb.ARM_A3, offered=10, accepted=8, episodes=3)["floor"] == (
            sb.PROTOCOL_FLOOR
        )

    def test_an_arm_that_offered_nothing_is_not_admitted_by_default(self) -> None:
        """No offers is no evidence, and evidence is what the pilot is for."""
        verdict = sl.pilot_verdict(sb.ARM_A4, offered=0, accepted=0, episodes=3)
        assert verdict["verdict"] == sl.PILOT_NO_EVIDENCE

    def test_the_pilot_is_never_reported_as_a_result(self) -> None:
        verdict = sl.pilot_verdict(sb.ARM_A4, offered=10, accepted=9, episodes=3)
        assert verdict["kind"] == "scopebench-config-admission-pilot"
        assert "instrument check" in verdict["note"]


# ── the ladder, and the two conditional change types ─────────────────────────


class TestTheLadderRowsAreBuiltFromRecords:
    def test_every_change_type_gets_a_row(self) -> None:
        rows = sb.ladder_report(sl.ladder_tallies([]))
        assert [row["change_type"] for row in rows] == list(CHANGE_TARGETS)

    def test_the_two_capability_types_are_not_measured_on_this_surface(self) -> None:
        rows = {row["change_type"]: row for row in sb.ladder_report(sl.ladder_tallies([]))}
        assert rows["worker.tools"]["rung"] == sb.LADDER_NOT_MEASURED
        assert rows["worker.permissions"]["rung"] == sb.LADDER_NOT_MEASURED

    def test_the_reason_is_the_declared_capability_shortfall(self) -> None:
        rows = {row["change_type"]: row for row in sb.ladder_report(sl.ladder_tallies([]))}
        assert str(sb.STAGE_TWO_MIN_CAPABILITIES) in rows["worker.tools"]["absent_reason"]

    def test_applications_are_counted_per_target(self) -> None:
        record = {
            "arm": sb.ARM_A4,
            "validity": sb.VALID,
            "change_types": {"worker.prompts": {"applications": 3}},
        }
        tallies = {entry.target: entry for entry in sl.ladder_tallies([record, record])}
        assert tallies["worker.prompts"].applications == 6

    def test_a_voided_cell_is_charged_to_the_types_it_carried(self) -> None:
        record = {
            "arm": sb.ARM_A4,
            "validity": sb.VOID_NO_CONFIG_EFFECT,
            "change_types": {"worker.prompts": {"applications": 0, "refusals": 1}},
        }
        tallies = {entry.target: entry for entry in sl.ladder_tallies([record])}
        assert tallies["worker.prompts"].voided_cells == 1


# ── the config strategist's framing ──────────────────────────────────────────


class TestTheConfigAuthorityIsTheShippedOne:
    def test_the_host_framing_is_appended_never_substituted(self) -> None:
        lane = _lane(_Canned([MARKER_HOLD]))
        run = lane.begin(0)
        lane.end(run)
        lane.review(sl.blank_snapshot("s1"), step_index=1)
        assert lane.runner is not None
        seam = lane.runner._loop._complete  # noqa: SLF001 - reading the double back
        assert isinstance(seam, _Canned)
        system = seam.calls[0][0]["content"]
        assert system.startswith(CONFIG_AUTHORITY)
        assert sl.STRATEGIST_FRAMING in system
        lane.close()

    def test_the_framing_names_no_strategy(self) -> None:
        lowered = sl.STRATEGIST_FRAMING.lower()
        assert "greedy" not in lowered
        assert "optimum" not in lowered


# ── the record shape the series writes ───────────────────────────────────────


class TestTheStageTwoRecord:
    @staticmethod
    def _record(episode: ep.Episode, lane: sl.ConfigLane, arm: str) -> dict[str, Any]:
        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, calls = sl.play_episode(episode, actor)
        return sl.build_record(
            arm=arm,
            episode=episode,
            rollout=rollout,
            actor_calls=calls,
            cost=sb.Cost(model_calls=3, prompt_tokens=9, completion_tokens=3),
            tally=None,
            actor_sha="a",
            ledger_sha="a",
        )

    def test_it_is_a_stage_two_record(self, episode: ep.Episode) -> None:
        record = self._record(episode, _lane(), sb.ARM_A0)
        assert record["stage"] == sb.STAGE_TWO

    def test_an_unstrategised_arm_counts_no_protocol_unit(self, episode: ep.Episode) -> None:
        record = self._record(episode, _lane(), sb.ARM_A0)
        assert record["protocol"]["protocol_unit"] == sb.PROTOCOL_UNIT_NONE
        assert record["protocol"]["protocol_acceptance"] is None

    def test_a_config_arm_counts_change_units(self, episode: ep.Episode) -> None:
        record = self._record(episode, _lane(), sb.ARM_A4)
        assert record["protocol"]["protocol_unit"] == sb.PROTOCOL_UNIT_CHANGE

    def test_an_advisory_arm_counts_directives(self, episode: ep.Episode) -> None:
        record = self._record(episode, _lane(), sb.ARM_A3)
        assert record["protocol"]["protocol_unit"] == sb.PROTOCOL_UNIT_DIRECTIVE

    def test_the_five_axes_stay_disjoint_in_a_real_record(self, episode: ep.Episode) -> None:
        record = self._record(episode, _lane(), sb.ARM_A4)
        for axis, keys in (
            ("outcome", sb.OUTCOME_KEYS),
            ("protocol", sb.PROTOCOL_KEYS),
            ("authority", sb.AUTHORITY_KEYS),
            ("cost", sb.COST_KEYS),
            ("ratchet", sb.RATCHET_KEYS),
        ):
            assert set(record[axis]) == set(keys), axis

    def test_no_judge_key_reaches_a_record(self, episode: ep.Episode) -> None:
        record = self._record(episode, _lane(), sb.ARM_A4)
        for axis in ("outcome", "protocol", "authority", "cost", "ratchet"):
            assert not set(record[axis]) & set(sb.JUDGE_KEYS)


# ── the fingerprint: what may differ between cycle-2 arms ────────────────────


class TestTheCycleTwoFingerprint:
    @staticmethod
    def _fingerprint(arm: str) -> dict[str, Any]:
        dial = sl.SeatDialConfig(
            arm=arm,
            seat=sb.SEAT_OPERATION,
            role=sb.ARMS[arm].actor_role,
            model=f"model-{sb.ARMS[arm].actor_role}",
            base_url="http://x/v1",
            api_key="k",
        )
        return sl.stage_two_fingerprint(arm, actor=dial, strategist=None)

    def test_only_the_declared_fields_differ(self) -> None:
        a4 = self._fingerprint(sb.ARM_A4)
        a3 = self._fingerprint(sb.ARM_A3)
        differing = {key for key in a4 if json.dumps(a4[key]) != json.dumps(a3[key])}
        assert differing <= {"arm", "lane", "strategy_role", "strategy_model", "strategy_endpoint"}

    def test_the_lane_is_recorded(self) -> None:
        assert self._fingerprint(sb.ARM_A4)["lane"] == sb.LANE_CONFIG
        assert self._fingerprint(sb.ARM_A0)["lane"] == sb.LANE_NONE

    def test_the_baseline_configuration_digest_rides_the_fingerprint(self) -> None:
        assert self._fingerprint(sb.ARM_A0)["baseline_config_sha"] == _lane().baseline_sha()

    def test_the_actor_budget_and_sampling_are_identical(self) -> None:
        a0 = self._fingerprint(sb.ARM_A0)
        a1 = self._fingerprint(sb.ARM_A1)
        assert a0["actor_max_tokens"] == a1["actor_max_tokens"]
        assert a0["temperature"] == a1["temperature"]


# ── absences: condition 7's obligation, for cycle 2's own cells ──────────────


class TestCycleTwoAbsences:
    def test_an_absence_is_never_claimed_for_a_scored_cell(self) -> None:
        record = sb.EpisodeRecord(
            arm=sb.ARM_A4,
            stage=sb.STAGE_TWO,
            family=ep.FIRST_CYCLE[0],
            episode="contention-1",
            seed=1,
            planner="live",
            validity=sb.VALID,
            outcome={
                "regret": 1,
                "optimum": 10,
                "strategic_utility": 9,
                "operational_success": True,
            },
            protocol={},
            authority={"authority_violations": 0},
            cost={"tokens": 1},
            ratchet={},
        )
        summary = sl.summarise_config([record])
        assert f"{sb.ARM_A4}|{sb.STAGE_TWO}|{ep.FIRST_CYCLE[0]}" not in summary["absent"]

    def test_the_config_lanes_stage_one_absence_is_the_declared_one(self) -> None:
        summary = sl.summarise_config([])
        key = f"{sb.ARM_A4}|{sb.STAGE_ONE}|{ep.FIRST_CYCLE[0]}"
        assert summary["absent"][key] == sb.ABSENT_CONFIG_NO_STAGE_ONE

    def test_a2_is_declared_but_not_dialled_in_this_cycle(self) -> None:
        summary = sl.summarise_config([])
        key = f"{sb.ARM_A2}|{sb.STAGE_TWO}|{ep.FIRST_CYCLE[0]}"
        assert summary["absent"][key] == sb.ABSENT_ARM_NOT_IN_CYCLE

    def test_every_declared_cell_is_scored_or_explained(self) -> None:
        summary = sl.summarise_config([])
        for arm in sb.ARM_ORDER:
            for stage in sb.STAGES:
                for family in ep.FIRST_CYCLE:
                    assert f"{arm}|{stage}|{family}" in summary["absent"]


# ── the scaffold this task must not have touched ─────────────────────────────


class TestTheHermeticScaffoldIsUnchanged:
    def test_no_scope_module_imports_a_transport(self) -> None:
        banned = {"socket", "http", "urllib", "httpx", "requests", "aiohttp", "ssl", "asyncio"}
        for path in sorted((REPO_ROOT / "examples" / "scope").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(a.name.split(".")[0] not in banned for a in node.names), path.name
                elif isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[0] not in banned, path.name

    def test_the_live_harness_is_the_only_module_that_dials(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        assert "urllib.request" in source


# ── the CLI, offline ─────────────────────────────────────────────────────────


class TestTheCli:
    def test_stage_two_requires_an_arm(self) -> None:
        parser = sl.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["stage2"])

    def test_stage_two_takes_every_cycle_two_arm(self) -> None:
        for arm in sb.CYCLE_TWO_ARMS:
            args = sl.build_parser().parse_args(["stage2", "--arm", arm])
            assert args.arm == arm

    def test_the_pilot_only_takes_a_seated_arm(self) -> None:
        parser = sl.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["pilot", "--arm", sb.ARM_A0])

    def test_the_report_can_apply_the_cycle_two_rule(self) -> None:
        args = sl.build_parser().parse_args(["report", "--rule", "config"])
        assert args.rule == "config"

    def test_the_default_rule_stays_cycle_ones(self) -> None:
        assert sl.build_parser().parse_args(["report"]).rule == "default"


class TestTheOracleIsStillTheGrader:
    """A live arm and a scripted control are graded by one function."""

    def test_the_grader_is_scopebenchs(self, episode: ep.Episode) -> None:
        assert sb.ARMS[sb.ARM_A4].grader is sb.grade

    def test_the_stage_two_record_is_graded_by_it(self, episode: ep.Episode) -> None:
        actor = sl.LiveActor(
            seam=_CannedActor([_allocation_reply(episode)]),
            episode=episode,
            system_prompt="s",
        )
        rollout, _calls = sl.play_episode(episode, actor)
        solution = orc.solve(episode)
        graded = sb.grade(episode, solution, rollout, sb.Cost())
        assert graded["outcome"]["optimum"] == solution.optimum
