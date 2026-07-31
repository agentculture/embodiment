#!/usr/bin/env python3
"""The pin for task t27's head-to-head series (``examples/league_h2h.py``).

The prose argument lives in
``docs/live-test-results/league-h2h-preregistration.md`` — read that first.
This file is the **pin**, not the argument: every threshold, every rung, every
budget and the whole decision rule are asserted **by value**, so that moving one
after a result exists is a deliberate edit a reviewer sees in a diff. That is
the same discipline as ``tests/test_devague_legs_preregistration.py`` and
``tests/test_association_work.py::TestPreRegisteredThresholds``, and it is why
task t18's ``INCONCLUSIVE`` was reportable rather than embarrassing.

Nothing here dials a model. The end-to-end coverage runs the harness against
``tests/fake_league.py`` with the hermetic scripted seams, and the committed
identical-mind control artifact is read from disk.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples import league_h2h  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "examples" / "league_h2h.py"
FAKE_LEAGUE = REPO_ROOT / "tests" / "fake_league.py"
FAKE_BIN = f"{sys.executable} {FAKE_LEAGUE}"
RESULTS = REPO_ROOT / "docs" / "live-test-results"
CONTROL_LOG = RESULTS / "league-h2h-scripted-control.jsonl"
CONTROL_CONFIG = RESULTS / "league-h2h-scripted-control-config.json"


# ── the pre-registered constants ─────────────────────────────────────────────


class TestPreRegisteredArms:
    """Three arms, and an arm is a MODEL PAIR — never a code branch."""

    def test_the_three_arms_are_exactly_these_model_pairs(self) -> None:
        assert {name: (arm.cortex, arm.muse) for name, arm in league_h2h.ARMS.items()} == {
            "full-gemma": (
                "nvidia/Gemma-4-31B-IT-NVFP4",
                "nvidia/Gemma-4-31B-IT-NVFP4",
            ),
            "mixed": (
                "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP",
                "nvidia/Gemma-4-31B-IT-NVFP4",
            ),
            "full-qwen": (
                "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP",
                "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP",
            ),
        }

    def test_mixed_is_the_control_because_it_is_what_ships_today(self) -> None:
        assert league_h2h.CONTROL_ARM == "mixed"

    def test_full_arms_use_one_model_for_both_roles(self) -> None:
        assert league_h2h.ARMS["full-gemma"].cortex == league_h2h.ARMS["full-gemma"].muse
        assert league_h2h.ARMS["full-qwen"].cortex == league_h2h.ARMS["full-qwen"].muse
        assert league_h2h.ARMS["mixed"].cortex != league_h2h.ARMS["mixed"].muse

    def test_senses_is_gemma_12b_and_is_recorded_not_dialled(self) -> None:
        # The operator was explicit that senses stays Gemma 4 12B in all three
        # arms. This seat has no senses lane, so the constant is recorded for
        # completeness and the harness must never put it on a wire.
        assert league_h2h.SENSES_MODEL == "coolthor/gemma-4-12B-it-NVFP4A16"
        wired = {a.cortex for a in league_h2h.ARMS.values()}
        wired |= {a.muse for a in league_h2h.ARMS.values()}
        assert league_h2h.SENSES_MODEL not in wired, "senses is a record, not a seam"
        assert "senses" not in league_h2h.Arm.__dataclass_fields__

    def test_pairings_are_the_three_round_robin_meetings(self) -> None:
        assert league_h2h.PAIRINGS == (
            ("gemma-vs-mixed", "full-gemma", "mixed"),
            ("mixed-vs-qwen", "mixed", "full-qwen"),
            ("qwen-vs-gemma", "full-qwen", "full-gemma"),
        )

    def test_every_arm_meets_every_other_arm_exactly_once(self) -> None:
        met = [frozenset((a, b)) for _, a, b in league_h2h.PAIRINGS]
        assert len(met) == len(set(met)) == 3
        assert set().union(*met) == set(league_h2h.ARMS)


class TestPreRegisteredBudgets:
    """SUFFICIENT, not merely equal — and identical in every seat of every arm.

    The brief this task started from said "budget 3000+ for any Qwen seat".
    That number was corrected to 16000 **before the first dial**, on measured
    evidence from two sibling tasks: at ``max_tokens=6000`` this cortex returns
    ``finish_reason=length`` with empty content and ~12,857 characters of
    reasoning, and only answers correctly at 16000. An identical cap that
    truncates one arm measures truncation, not skill — the single confound most
    likely to have silently invalidated this whole head-to-head.
    """

    def test_budgets_are_fixed_by_value(self) -> None:
        assert league_h2h.MAX_STEPS == 8
        assert league_h2h.MAX_TOKENS == 16000
        assert league_h2h.MUSE_MAX_TOKENS == 16000
        assert league_h2h.TOKEN_CEILING_AVAILABLE == 32000
        assert league_h2h.CORTEX_TEMPERATURE == 0.3
        assert league_h2h.MUSE_TEMPERATURE == 0.7
        assert league_h2h.MUSE_MAX_TURNS == 2
        assert league_h2h.MATCH_TURNS == 3

    def test_the_budget_clears_the_measured_truncation_floor(self) -> None:
        # 6000 is measured-insufficient on this rig; 16000 is measured-enough.
        # Anything at or below 6000 would measure truncation instead of skill.
        assert league_h2h.MAX_TOKENS > 6000
        assert league_h2h.MAX_TOKENS >= 16000
        assert league_h2h.TOKEN_CEILING_AVAILABLE > league_h2h.MAX_TOKENS

    def test_the_muse_lane_is_budgeted_as_generously_as_the_cortex(self) -> None:
        # In the full-qwen arm the muse IS the thinking model. Capping it short
        # would truncate one arm's counsel for an instrument reason.
        assert league_h2h.MUSE_MAX_TOKENS == league_h2h.MAX_TOKENS

    def test_both_seats_get_the_same_budget_object(self) -> None:
        seams = {
            name: league_h2h.build_seams(
                arm, live=True, base_url="http://localhost:8001/v1", api_key="k"
            )
            for name, arm in league_h2h.ARMS.items()
        }
        cortex_budgets = {(s[0].max_tokens, s[0].temperature) for s in seams.values()}
        muse_budgets = {(s[1].max_tokens, s[1].temperature) for s in seams.values()}
        assert cortex_budgets == {(16000, 0.3)}
        assert muse_budgets == {(16000, 0.7)}

    def test_the_request_timeout_leaves_room_for_a_slow_thinking_turn(self) -> None:
        # A >600s response on a busy shared GPU is contention, not a result.
        assert league_h2h.REQUEST_TIMEOUT >= 1800.0


class TestTruncationIsAnInstrumentEventNeverALoss:
    """``finish_reason == "length"`` is recorded, reported, and disqualifying."""

    def test_the_truncated_finish_reason_is_named_once(self) -> None:
        assert league_h2h.FINISH_TRUNCATED == "length"

    def test_the_meter_counts_truncations_separately_from_empty_turns(self) -> None:
        meter = league_h2h.Meter(role="cortex", model="m")
        assert meter.to_dict()["truncated"] == 0
        assert "empty_content" in meter.to_dict()

    def test_a_truncated_turn_is_counted_and_announced(self, capsys: Any) -> None:
        seam = league_h2h.MeteredSeam(
            base_url="http://x/v1",
            model="m",
            api_key="k",
            role="cortex",
            max_tokens=league_h2h.MAX_TOKENS,
            temperature=0.3,
        )
        seam._post = lambda body: {  # type: ignore[method-assign]
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 16000},
        }
        seam([{"role": "user", "content": "hi"}])
        assert seam.meter.truncated == 1
        assert seam.meter.finish_reasons["length"] == 1
        # Nothing degrades silently (C3).
        assert "truncated" in capsys.readouterr().err

    def test_a_normal_turn_counts_no_truncation(self) -> None:
        seam = league_h2h.MeteredSeam(
            base_url="http://x/v1",
            model="m",
            api_key="k",
            role="cortex",
            max_tokens=league_h2h.MAX_TOKENS,
            temperature=0.3,
        )
        seam._post = lambda body: {  # type: ignore[method-assign]
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        seam([{"role": "user", "content": "hi"}])
        assert seam.meter.truncated == 0

    def test_the_exclusion_rule_is_stated_in_the_runner(self) -> None:
        # Structural: a truncated match must be excluded from the decision, not
        # scored as a loss. The runner's own docstring is the contract, and the
        # branch that enforces it reads truncated_turns off the match record.
        source = HARNESS.read_text(encoding="utf-8")
        assert 'if record["truncated_turns"]:' in source
        assert "excluded from the decision" in (league_h2h.run_rung.__doc__ or "")

    def test_only_the_model_id_differs_between_arms(self) -> None:
        cortex, muse = league_h2h.build_seams(
            league_h2h.ARMS["full-qwen"], live=True, base_url="http://x/v1", api_key="k"
        )
        other_cortex, other_muse = league_h2h.build_seams(
            league_h2h.ARMS["full-gemma"], live=True, base_url="http://x/v1", api_key="k"
        )
        assert cortex.endpoint == other_cortex.endpoint
        assert cortex.tools == other_cortex.tools == league_h2h.TOOL_SCHEMA
        # The muse lane passes NO tool schema at all. That absence is the whole
        # of "tools-off" and it must hold in every arm.
        assert muse.tools is None
        assert other_muse.tools is None
        assert cortex.model != other_cortex.model


class TestPreRegisteredLadder:
    """Four rungs, rising, and board size is not one of the knobs."""

    def test_the_ladder_is_these_four_rungs_in_this_order(self) -> None:
        assert [r.id for r in league_h2h.LADDER] == ["L1", "L2", "L3", "L4"]
        assert [r.scenario for r in league_h2h.LADDER] == [
            "skirmish-1",
            "recon-1",
            "skirmish-2",
            "skirmish-2",
        ]

    def test_seeds_are_fixed_and_distinct(self) -> None:
        seeds = [r.seed for r in league_h2h.LADDER]
        assert seeds == [4242, 4243, 4244, 4245]
        assert len(set(seeds)) == len(seeds)

    def test_the_handicaps_escalate_and_never_relax(self) -> None:
        caps = [r.max_actions for r in league_h2h.LADDER]
        assert caps == [None, None, 2, 2]
        fog = [r.fogged for r in league_h2h.LADDER]
        assert fog == [False, False, False, True]

    def test_the_fogged_rung_declares_leagues_own_fairness_metadata(self) -> None:
        hardest = league_h2h.LADDER_BY_ID["L4"]
        assert hardest.map_read == "fog"
        assert hardest.unit_comms == "off"

    def test_rosters_match_the_scenarios_own_roles(self) -> None:
        # recon-1 fields explorer/planner/harvester/defender; the two skirmishes
        # field scout/harvester/defender. A roster naming a role the scenario
        # does not know still registers, and the unit it makes can do nothing.
        assert league_h2h.LADDER_BY_ID["L2"].roster == (
            "explorer",
            "planner",
            "harvester",
            "defender",
        )
        for rung_id in ("L1", "L3", "L4"):
            assert league_h2h.LADDER_BY_ID[rung_id].roster == (
                "scout",
                "harvester",
                "defender",
            )

    def test_board_size_is_not_a_knob_anywhere_in_the_rung_contract(self) -> None:
        # "+5 in size" is not expressible: league ships three fixed scenarios
        # and `match new` has no grid parameter. The substitution is the
        # scenario ladder, --max-actions and fog. This asserts the rung carries
        # no size field at all, so nobody later reads one in.
        fields = set(league_h2h.Rung.__dataclass_fields__)
        assert not fields & {"width", "height", "grid", "size", "board_size"}

    def test_every_rung_plays_six_matches_three_pairings_both_colours(self) -> None:
        for rung in league_h2h.LADDER:
            plan = league_h2h.rung_matches(rung)
            assert len(plan) == 6
            names = [name for name, _, _ in plan]
            assert sorted(set(names)) == ["gemma-vs-mixed", "mixed-vs-qwen", "qwen-vs-gemma"]
            for name, _, _ in league_h2h.PAIRINGS:
                colours = {(blue.id, red.id) for pairing, blue, red in plan if pairing == name}
                assert len(colours) == 2, "both arms must play both colours"
                (first, second), (third, fourth) = sorted(colours)
                assert (first, second) == (fourth, third)


class TestPreRegisteredDecisionRule:
    """Fixed before the first dial, and applied without amendment."""

    def test_tie_breaks_are_this_priority_order(self) -> None:
        assert league_h2h.TIE_BREAKS == (
            "outcome_total",
            "cooperation_v1",
            "fewer_rejections_and_cap_violations",
        )

    def test_a_pairing_needs_both_colours_to_separate(self) -> None:
        assert league_h2h.MATCHES_PER_PAIRING == 2

    def test_a_rung_needs_two_of_three_pairings_to_separate(self) -> None:
        assert league_h2h.MIN_SEPARATED_PAIRINGS == 2
        assert len(league_h2h.PAIRINGS) == 3

    def test_the_verdict_vocabulary_is_closed(self) -> None:
        assert league_h2h.VERDICTS == ("SEPARATED", "INCONCLUSIVE", "ABSENT")

    def test_wall_clock_caps_are_fixed(self) -> None:
        # Sized for the amended 16000-token budget (amendment 2, pre-dial): a
        # thinking cortex with five times the headroom takes correspondingly
        # longer, and the rule is to report unrun rungs ABSENT rather than
        # shrink the budget to fit the clock.
        assert league_h2h.RUNG_CAP_SECONDS == 10800.0
        assert league_h2h.LADDER_CAP_SECONDS == 28800.0

    def test_transport_retry_is_bounded_and_counted(self) -> None:
        assert league_h2h.MAX_TRANSPORT_RETRIES == 3
        assert league_h2h.RETRY_SLEEP_SECONDS == 20.0


# ── the decision rule, exercised ─────────────────────────────────────────────


def _report(
    *,
    blue_arm: str,
    red_arm: str,
    totals: tuple[int, int] = (0, 0),
    coops: tuple[int, int] = (0, 0),
    rejections: tuple[int, int] = (0, 0),
    cap: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    report = {
        "arms": {"blue": blue_arm, "red": red_arm},
        "score": {
            "outcome": {"blue": {"total": totals[0]}, "red": {"total": totals[1]}},
            "cooperation": {"blue": {"score": coops[0]}, "red": {"score": coops[1]}},
        },
        "rejections": {"blue": rejections[0], "red": rejections[1]},
        "cap_dropped": {"blue": cap[0], "red": cap[1]},
    }
    report["result"] = league_h2h.decide_match(report)
    return report


class TestMatchDecision:
    def test_outcome_total_decides_first(self) -> None:
        result = _report(blue_arm="mixed", red_arm="full-qwen", totals=(3, 1), coops=(0, 99))[
            "result"
        ]
        assert result["decided_by"] == "outcome_total"
        assert result["winner_arm"] == "mixed"

    def test_cooperation_breaks_a_zero_zero_tie(self) -> None:
        result = _report(blue_arm="mixed", red_arm="full-qwen", coops=(80, 90))["result"]
        assert result["decided_by"] == "cooperation_v1"
        assert result["winner_arm"] == "full-qwen"

    def test_faults_break_the_next_tie_and_fewer_is_better(self) -> None:
        result = _report(blue_arm="mixed", red_arm="full-qwen", coops=(70, 70), rejections=(0, 2))[
            "result"
        ]
        assert result["decided_by"] == "fewer_rejections_and_cap_violations"
        assert result["winner_arm"] == "mixed"

    def test_cap_violations_count_as_faults(self) -> None:
        result = _report(blue_arm="mixed", red_arm="full-qwen", coops=(70, 70), cap=(3, 0))[
            "result"
        ]
        assert result["winner_arm"] == "full-qwen"

    def test_a_total_tie_is_a_draw_with_no_winner(self) -> None:
        result = _report(blue_arm="mixed", red_arm="full-qwen")["result"]
        assert result["winner_arm"] is None
        assert result["decided_by"] is None


class TestPairingDecision:
    def test_winning_both_colours_separates(self) -> None:
        matches = [
            _report(blue_arm="full-gemma", red_arm="mixed", coops=(90, 80)),
            _report(blue_arm="mixed", red_arm="full-gemma", coops=(80, 90)),
        ]
        graded = league_h2h.decide_pairing(matches, first_arm="full-gemma")
        assert graded["separated"] is True
        assert graded["winner_arm"] == "full-gemma"

    def test_a_colour_split_is_not_a_separation(self) -> None:
        # Both matches won by blue: the classic map-bias signature. The strict
        # rule must refuse to call this a difference between the arms.
        matches = [
            _report(blue_arm="full-gemma", red_arm="mixed", coops=(90, 80)),
            _report(blue_arm="mixed", red_arm="full-gemma", coops=(90, 80)),
        ]
        graded = league_h2h.decide_pairing(matches, first_arm="full-gemma")
        assert graded["separated"] is False
        assert graded["winner_arm"] is None

    def test_one_played_colour_can_never_separate(self) -> None:
        matches = [_report(blue_arm="full-gemma", red_arm="mixed", coops=(90, 80))]
        graded = league_h2h.decide_pairing(matches, first_arm="full-gemma")
        assert graded["separated"] is False
        assert graded["winner_arm"] is None

    def test_net_margin_cancels_a_constant_colour_bias(self) -> None:
        # A pure +10 blue bias and no arm difference at all.
        matches = [
            _report(blue_arm="full-gemma", red_arm="mixed", coops=(60, 50)),
            _report(blue_arm="mixed", red_arm="full-gemma", coops=(60, 50)),
        ]
        graded = league_h2h.decide_pairing(matches, first_arm="full-gemma")
        assert graded["net_margin"]["cooperation_v1"] == 0.0

    def test_net_margin_survives_a_colour_bias_that_hides_a_real_gap(self) -> None:
        # +10 blue bias with full-gemma genuinely 4 better: 64-50 and 60-54.
        matches = [
            _report(blue_arm="full-gemma", red_arm="mixed", coops=(64, 50)),
            _report(blue_arm="mixed", red_arm="full-gemma", coops=(60, 54)),
        ]
        graded = league_h2h.decide_pairing(matches, first_arm="full-gemma")
        assert graded["net_margin"]["cooperation_v1"] == 8.0
        # ...and the strict rule still refuses to call it, which is the point:
        # net margin is an effect size, not the verdict.
        assert graded["separated"] is False


class TestRungDecision:
    @staticmethod
    def _pairings(separated: dict[str, str]) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "separated": name in separated,
                "winner_arm": separated.get(name),
                "matches": 2,
                "wins": {},
                "first_arm": first,
                "net_margin": {},
            }
            for name, first, _ in league_h2h.PAIRINGS
        }

    def test_two_separated_pairings_separate_the_rung(self) -> None:
        graded = league_h2h.decide_rung(
            self._pairings({"gemma-vs-mixed": "full-gemma", "qwen-vs-gemma": "full-gemma"})
        )
        assert graded["verdict"] == "SEPARATED"
        assert graded["implied_ordering"][0] == "full-gemma"

    def test_one_separated_pairing_is_inconclusive(self) -> None:
        graded = league_h2h.decide_rung(self._pairings({"gemma-vs-mixed": "full-gemma"}))
        assert graded["verdict"] == "INCONCLUSIVE"

    def test_no_separated_pairing_is_inconclusive(self) -> None:
        assert league_h2h.decide_rung(self._pairings({}))["verdict"] == "INCONCLUSIVE"

    def test_a_rock_paper_scissors_result_is_reported_as_cyclic(self) -> None:
        graded = league_h2h.decide_rung(
            self._pairings(
                {
                    "gemma-vs-mixed": "full-gemma",
                    "mixed-vs-qwen": "mixed",
                    "qwen-vs-gemma": "full-qwen",
                }
            )
        )
        assert graded["cyclic"] is True
        assert graded["verdict"] == "SEPARATED"


# ── the pure pieces ──────────────────────────────────────────────────────────


class TestCapTruncation:
    def test_no_cap_leaves_orders_untouched(self) -> None:
        orders = {"plan": "p", "actions": [{"unit_id": "u1"}, {"unit_id": "u2"}]}
        trimmed, dropped = league_h2h.apply_cap(orders, None)
        assert trimmed is orders
        assert dropped == 0

    def test_under_the_cap_is_untouched(self) -> None:
        orders = {"plan": "p", "actions": [{"unit_id": "u1"}]}
        trimmed, dropped = league_h2h.apply_cap(orders, 2)
        assert trimmed["actions"] == orders["actions"]
        assert dropped == 0

    def test_over_the_cap_truncates_to_the_seats_own_priority_order(self) -> None:
        orders = {
            "plan": "p",
            "actions": [{"unit_id": "u1"}, {"unit_id": "u2"}, {"unit_id": "u3"}],
        }
        trimmed, dropped = league_h2h.apply_cap(orders, 2)
        assert [a["unit_id"] for a in trimmed["actions"]] == ["u1", "u2"]
        assert dropped == 1
        # The original is not mutated: the overrun has to stay visible.
        assert len(orders["actions"]) == 3


class TestFoggedView:
    BRIEF = {
        "match_id": "m",
        "scenario": "skirmish-2",
        "turn": 0,
        "turn_limit": 16,
        "status": "active",
        "winner": None,
        "team": "blue",
        "cells_seen": 15,
        "known_units": [
            {"unit": "blue-u1", "team": "blue", "role": "scout", "pos": [0, 0]},
            {"unit": "blue-u2", "team": "blue", "role": "harvester", "pos": [1, 0]},
        ],
        "known_resource_nodes": [],
        "known_control_points": [],
    }
    SHOW = {
        "state": {
            "units": [
                {"id": "blue-u1", "team_id": "blue", "alive": True, "carrying": 2},
                {"id": "blue-u2", "team_id": "blue", "alive": True, "carrying": 0},
                {"id": "red-u1", "team_id": "red", "alive": True, "carrying": 0},
            ],
            "control_points": [{"id": "cp-relay", "pos": [7, 5]}],
        },
        "legal_actions": {
            "blue-u1": {"move": [[0, 1]]},
            "red-u1": {"move": [[13, 10]]},
        },
        "last_turn_rejections": [],
    }

    def test_unseen_control_points_are_absent(self) -> None:
        view = league_h2h.fogged_view(self.BRIEF, self.SHOW, "blue")
        assert view["control_points"] == []
        assert view["fog"] is True
        assert view["cells_seen"] == 15

    def test_enemy_units_never_leak_in(self) -> None:
        view = league_h2h.fogged_view(self.BRIEF, self.SHOW, "blue")
        assert [u["id"] for u in view["my_units"]] == ["blue-u1", "blue-u2"]
        assert "red-u1" not in view["legal_actions"]

    def test_own_unit_legality_is_carried_over_and_nothing_else(self) -> None:
        view = league_h2h.fogged_view(self.BRIEF, self.SHOW, "blue")
        assert set(view["legal_actions"]) == {"blue-u1"}

    def test_carrying_comes_from_the_teams_own_units(self) -> None:
        view = league_h2h.fogged_view(self.BRIEF, self.SHOW, "blue")
        assert view["my_units"][0]["carrying"] == 2


class TestPromptsAreSymmetric:
    def test_the_system_prompt_takes_no_arm_argument(self) -> None:
        # Structural, not incidental: the two seats in a match must receive the
        # same system text or the comparison is not a comparison.
        import inspect

        params = list(inspect.signature(league_h2h.system_prompt).parameters)
        assert params == ["rung"]

    def test_the_directive_takes_no_team_argument(self) -> None:
        import inspect

        assert list(inspect.signature(league_h2h.directive_for).parameters) == ["rung"]

    def test_the_cap_and_fog_framings_appear_only_on_their_own_rungs(self) -> None:
        easy = league_h2h.system_prompt(league_h2h.LADDER_BY_ID["L1"])
        hard = league_h2h.system_prompt(league_h2h.LADDER_BY_ID["L4"])
        assert "HANDICAP" not in easy
        assert "FOG" not in easy
        assert "HANDICAP" in hard
        assert "FOG" in hard
        assert league_h2h.BASE_SYSTEM in easy
        assert league_h2h.BASE_SYSTEM in hard


class TestBothTeamsAreModelSeats:
    """Deviation d10, asserted structurally rather than believed."""

    def test_the_scripted_rival_policy_is_never_reached(self) -> None:
        source = HARNESS.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "rival_orders" not in names, "round-robin means BOTH teams are model seats"

    def test_league_seat_is_imported_and_not_copied(self) -> None:
        source = HARNESS.read_text(encoding="utf-8")
        assert "from examples.league_seat import" in source
        for reused in ("Seat", "LeagueCli", "TOOL_SCHEMA", "parse_completion"):
            assert reused in source

    def test_the_seat_turn_signature_carries_the_arm_not_the_colour(self) -> None:
        # The team is a field of SeatConfig alongside the arm, so the SAME code
        # path runs for blue and for red with only the model ids different.
        fields = set(league_h2h.SeatConfig.__dataclass_fields__)
        assert {"arm", "team"} <= fields

    def test_league_seat_py_is_not_modified_by_this_lane(self) -> None:
        """No commit that edits the h2h harness may also edit the seat it reuses.

        **Scoped to this lane's own commits (task t24).** It used to read the
        whole branch — ``git diff --name-only main...HEAD`` — and assert the seat
        appeared nowhere in it. That is not the claim in this test's name, and on
        this repo it is not even close to it: the entire muse cycle lives on one
        branch that has never merged, so ``main...HEAD`` is every file every task
        in the cycle has touched. Any *other* lane with a legitimate reason to
        edit ``league_seat.py`` failed a test about the head-to-head lane.

        Task t24 was authorised to add ``--trace-out`` to the seat — without it
        ``finish_reason`` is unreachable and its arena token-budget series cannot
        be run at all (embodiment#37) — and this test failed on that change while
        the h2h lane had not been touched at all.

        So it now asks what it says: walk the commits that modified
        ``examples/league_h2h.py`` and assert none of them also modified
        ``examples/league_seat.py``. Deviation d10's guarantee — the h2h lane
        reuses the seat rather than forking or bending it — is unchanged and is
        now enforced against the lane that could actually break it. The two
        structural tests above (imported-not-copied, and the scripted rival never
        reached) are the rest of that guarantee and are untouched.

        A branch with no h2h commits vacuously passes, which is correct: a lane
        that did not act cannot have modified anything.
        """
        commits = subprocess.run(  # nosec B603 B607
            ["git", "log", "--format=%H", "main...HEAD", "--", "examples/league_h2h.py"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        for sha in commits.stdout.split():
            files = subprocess.run(  # nosec B603 B607
                ["git", "show", "--name-only", "--format=", sha],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            touched = set(files.stdout.split())
            assert "examples/league_seat.py" not in touched, (
                f"commit {sha[:12]} modifies BOTH the h2h harness and the seat it "
                "reuses — d10 says this lane imports the seat, it does not bend it"
            )


# ── the committed identical-mind control ─────────────────────────────────────


class TestScriptedControlArtifact:
    """The offline control that makes the live tie-breaks readable.

    Running the ladder with **no** ``--live`` puts the SAME hermetic scripted
    mind in all three arms, so every number it produces is map and seed bias
    with the models removed. That artifact is committed, and the two facts
    asserted here are the two the pre-registration leans on: the colour bias is
    real and at the fogged rung it is large, and the paired ``net_margin``
    cancels it exactly.
    """

    @staticmethod
    def _records(kind: str) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in CONTROL_LOG.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == kind
        ]

    def test_the_control_artifact_is_committed(self) -> None:
        assert CONTROL_LOG.is_file()
        assert CONTROL_CONFIG.is_file()

    def test_the_control_played_every_rung_and_every_match(self) -> None:
        rungs = self._records("rung")
        assert [r["rung"] for r in rungs] == ["L1", "L2", "L3", "L4"]
        assert all(r["matches_played"] == 6 for r in rungs)
        assert len(self._records("match")) == 24

    def test_identical_minds_never_separate(self) -> None:
        # If they did, the instrument would be measuring the map.
        assert all(r["verdict"] == "INCONCLUSIVE" for r in self._records("rung"))

    def test_the_colour_bias_is_real_and_large_under_fog(self) -> None:
        fogged = [m for m in self._records("match") if m["rung"] == "L4"]
        margins = {m["result"]["margins"]["cooperation_v1"] for m in fogged}
        # Two byte-identical minds, and blue is 45 cooperation points ahead in
        # every single fogged match purely by which corner it started in.
        assert margins == {45}

    def test_the_paired_net_margin_cancels_that_bias_exactly(self) -> None:
        for rung in self._records("rung"):
            for name, pairing in rung["pairings"].items():
                assert pairing["net_margin"] == {
                    "outcome_total": 0.0,
                    "cooperation_v1": 0.0,
                    "faults": 0.0,
                }, f"{rung['rung']}/{name}"

    def test_the_control_confirms_outcome_total_ties_at_three_turns(self) -> None:
        # Pre-registered expectation: no roster crosses to a control point from
        # its home corner in three turns, so the primary metric ties at zero and
        # the tie-breaks carry the result. Recorded before the live run, not
        # discovered after it.
        for match in self._records("match"):
            assert match["result"]["margins"]["outcome_total"] == 0


# ── end to end, hermetic ─────────────────────────────────────────────────────


def _run(*argv: str, home: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env.pop("EIDETIC_DATA_DIR", None)
    env.pop(league_h2h.API_KEY_ENV, None)
    proc = subprocess.run(  # nosec B603
        [sys.executable, str(HARNESS), *argv],
        capture_output=True,
        text=True,
        cwd=str(home),
        env=env,
        timeout=900,
        check=False,
    )
    detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-2000:]}\nstderr:{proc.stderr[-2000:]}"
    assert proc.returncode == 0, detail
    return json.loads(proc.stdout)


class TestHermeticEndToEnd:
    """One whole head-to-head match against the fake arena. No network."""

    @pytest.fixture(scope="class")
    def played(self, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
        home = tmp_path_factory.mktemp("h2h")
        return _run(
            "match",
            "--rung",
            "L1",
            "--blue",
            "full-gemma",
            "--red",
            "full-qwen",
            "--home",
            str(home / "run"),
            "--log",
            str(home / "run" / "log.jsonl"),
            "--league-bin",
            FAKE_BIN,
            home=home,
        )

    def test_both_teams_are_declared_stateless_model_seats(self, played: dict[str, Any]) -> None:
        assert played["driver_kinds"] == {"blue": "stateless", "red": "stateless"}
        assert played["arms"] == {"blue": "full-gemma", "red": "full-qwen"}

    def test_both_teams_actually_drove_a_loop_every_turn(self, played: dict[str, Any]) -> None:
        log = Path(played["workdir"]).parent / "log.jsonl"
        seats = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "seat-turn"
        ]
        assert {s["team"] for s in seats} == {"blue", "red"}
        per_team = {c: sorted(s["turn"] for s in seats if s["team"] == c) for c in ("blue", "red")}
        assert per_team["blue"] == per_team["red"]
        assert all(s["drive"]["model_turns"] > 0 for s in seats)

    def test_each_seat_records_its_own_models(self, played: dict[str, Any]) -> None:
        assert played["models"]["blue"]["cortex"] == league_h2h.ARMS["full-gemma"].cortex
        assert played["models"]["red"]["cortex"] == league_h2h.ARMS["full-qwen"].cortex

    def test_cost_is_reported_per_colour_for_both_roles(self, played: dict[str, Any]) -> None:
        for colour in ("blue", "red"):
            cost = played["cost"][colour]
            assert cost["cortex"]["calls"] > 0
            assert cost["muse"]["calls"] > 0
            assert "completion_tokens" in cost
            assert cost["truncated"] == 0

    def test_truncation_is_hoisted_onto_the_match_record(self, played: dict[str, Any]) -> None:
        # Hoisted because a non-zero value disqualifies the match from the
        # decision; a reader must not have to dig for it.
        assert played["truncated_turns"] == 0
        assert played["max_tokens"] == league_h2h.MAX_TOKENS

    def test_a_transcript_is_committed_not_only_a_verdict(self, played: dict[str, Any]) -> None:
        log = Path(played["workdir"]).parent / "log.jsonl"
        seats = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "seat-turn"
        ]
        assert all(s["transcript"] for s in seats)
        first = seats[0]["transcript"][0]
        assert {"role", "model", "finish_reason", "tool_calls"} <= set(first)

    def test_the_config_preamble_lands_before_any_result(self, played: dict[str, Any]) -> None:
        log = Path(played["workdir"]).parent / "log.jsonl"
        first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert first["kind"] == "config"
        assert first["config"]["max_tokens"] == league_h2h.MAX_TOKENS
        assert first["config"]["muse_max_tokens"] == league_h2h.MUSE_MAX_TOKENS
        assert first["config"]["senses_dialled"] is False
        assert "sufficient" in first["config"]["budget_fairness"]

    def test_the_match_is_graded_by_the_pre_registered_rule(self, played: dict[str, Any]) -> None:
        assert played["result"]["decided_by"] in (None, *league_h2h.TIE_BREAKS)
        assert set(played["result"]["margins"]) == {
            "outcome_total",
            "cooperation_v1",
            "faults",
        }


class TestThePreRegistrationDocumentAgreesWithThePin:
    """The prose and the constants must not drift apart.

    A pre-registration whose document says one number and whose harness uses
    another is worse than none: it reads as a commitment and is not one.
    """

    DOC = RESULTS / "league-h2h-preregistration.md"

    def test_the_document_is_committed(self) -> None:
        assert self.DOC.is_file()

    def test_it_names_the_amended_budget_and_the_number_it_replaced(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        assert str(league_h2h.MAX_TOKENS) in text
        assert str(league_h2h.TOKEN_CEILING_AVAILABLE) in text
        # The correction is recorded, not folded in silently.
        assert "3000" in text
        assert "amendment 1" in text.lower()

    def test_it_records_the_wall_clock_amendment_too(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        assert "amendment 2" in text.lower()
        assert str(int(league_h2h.RUNG_CAP_SECONDS)) in text
        assert str(int(league_h2h.LADDER_CAP_SECONDS)) in text

    def test_it_names_every_rung_and_seed(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        for rung in league_h2h.LADDER:
            assert f"**{rung.id}**" in text
            assert str(rung.seed) in text
            assert rung.scenario in text

    def test_it_names_every_arm_and_every_pairing(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        for arm in league_h2h.ARMS.values():
            assert f"`{arm.id}`" in text
            assert arm.cortex in text
            assert arm.muse in text
        for name, _, _ in league_h2h.PAIRINGS:
            assert f"`{name}`" in text

    def test_it_states_that_board_size_is_not_a_knob(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        assert "Board size is not a knob" in text
        assert "+5 in size" in text

    def test_it_states_the_verdict_vocabulary_and_the_absent_rule(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        for verdict in league_h2h.VERDICTS:
            assert f"`{verdict}`" in text
        assert "reported `ABSENT` by name" in text


class TestResumeNeverReplaysACompletedMatch:
    """Pre-registration rule 3, implemented rather than only asserted in prose.

    On a shared rig a run gets cut short by contention. Re-rolling a match that
    already has an answer is how a series quietly becomes best-of-N, so the
    resumed match is folded back in exactly as it was recorded.
    """

    def test_an_absent_log_resumes_from_nothing(self, tmp_path: Path) -> None:
        assert league_h2h.played_matches(tmp_path / "missing.jsonl") == {}

    def test_only_match_records_are_read_back(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        log.write_text(
            "\n".join(
                [
                    json.dumps({"kind": "config", "config": {}}),
                    json.dumps({"kind": "seat-turn", "match_id": "x", "turn": 0}),
                    json.dumps({"kind": "match", "match_id": "h2h-L1-a-vs-b"}),
                    "not json at all",
                    json.dumps({"kind": "rung", "rung": "L1"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        found = league_h2h.played_matches(log)
        assert set(found) == {"h2h-L1-a-vs-b"}

    def test_a_fully_resumed_rung_plays_nothing_and_still_grades(self, tmp_path: Path) -> None:
        rung = league_h2h.LADDER_BY_ID["L1"]
        prior = {}
        for name, blue, red in league_h2h.rung_matches(rung):
            match_id = league_h2h.match_id_for(rung, blue.id, red.id)
            record = _report(blue_arm=blue.id, red_arm=red.id, coops=(90, 80))
            record.update({"kind": "match", "match_id": match_id, "truncated_turns": 0})
            prior[match_id] = record
        summary = league_h2h.run_rung(
            rung,
            home=tmp_path,
            log_path=tmp_path / "out.jsonl",
            live=False,
            base_url="http://x/v1",
            api_key="",
            league_bin="false",
            league_timeout=1.0,
            resume=prior,
        )
        # Nothing was dialled and nothing was replayed, yet the rung is graded.
        assert len(summary["matches_resumed"]) == 6
        assert summary["matches_played"] == 6
        assert summary["complete"] is True
        # Blue wins every match here, which is the colour-bias signature: the
        # strict rule must refuse to separate on it.
        assert summary["verdict"] == "INCONCLUSIVE"

    def test_a_resumed_truncated_match_stays_excluded(self, tmp_path: Path) -> None:
        rung = league_h2h.LADDER_BY_ID["L1"]
        prior = {}
        for index, (name, blue, red) in enumerate(league_h2h.rung_matches(rung)):
            match_id = league_h2h.match_id_for(rung, blue.id, red.id)
            record = _report(blue_arm=blue.id, red_arm=red.id, coops=(90, 80))
            record.update(
                {"kind": "match", "match_id": match_id, "truncated_turns": 1 if index == 0 else 0}
            )
            prior[match_id] = record
        summary = league_h2h.run_rung(
            rung,
            home=tmp_path,
            log_path=tmp_path / "out.jsonl",
            live=False,
            base_url="http://x/v1",
            api_key="",
            league_bin="false",
            league_timeout=1.0,
            resume=prior,
        )
        assert len(summary["matches_excluded_for_truncation"]) == 1
        assert summary["truncated_turns"] == 1


class TestTheInstrumentCheck:
    """`smoke` asks the one question that could invalidate the series.

    A cortex whose tool calls this harness cannot read would stage no orders,
    score nothing, and lose every match — and the write-up would report that as
    a quality difference between the models. So it is checked first, on the
    real schema, and reported as an instrument check rather than as data.
    """

    def test_it_checks_every_distinct_cortex_model_exactly_once(self) -> None:
        seen: list[str] = []

        class _Recorder(league_h2h.MeteredSeam):
            def _post(self, body: dict[str, Any]) -> dict[str, Any]:
                seen.append(body["model"])
                assert body["tools"] == league_h2h.TOOL_SCHEMA
                assert body["max_tokens"] == league_h2h.MAX_TOKENS
                # tool_choice is broken on this rig and must never be sent.
                assert "tool_choice" not in body
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "function": {
                                            "name": "order",
                                            "arguments": '{"unit_id":"blue-u1","action":"hold"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }

        original = league_h2h.MeteredSeam
        league_h2h.MeteredSeam = _Recorder  # type: ignore[misc]
        try:
            checked = league_h2h.smoke(base_url="http://x/v1", api_key="k")
        finally:
            league_h2h.MeteredSeam = original  # type: ignore[misc]

        assert sorted(seen) == sorted({arm.cortex for arm in league_h2h.ARMS.values()})
        assert len(seen) == 2, "two distinct cortex models across three arms"
        assert checked["every_cortex_can_call_a_tool"] is True
        assert checked["note"] == "instrument check, not data"

    def test_a_model_that_cannot_call_a_tool_fails_the_check(self) -> None:
        class _Mute(league_h2h.MeteredSeam):
            def _post(self, body: dict[str, Any]) -> dict[str, Any]:
                return {
                    "choices": [
                        {"message": {"content": "I would move west."}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 5},
                }

        original = league_h2h.MeteredSeam
        league_h2h.MeteredSeam = _Mute  # type: ignore[misc]
        try:
            checked = league_h2h.smoke(base_url="http://x/v1", api_key="k")
        finally:
            league_h2h.MeteredSeam = original  # type: ignore[misc]
        assert checked["every_cortex_can_call_a_tool"] is False

    def test_the_smoke_board_is_answerable_with_one_order(self) -> None:
        view = league_h2h.SMOKE_VIEW
        assert len(view["my_units"]) == 1
        assert view["legal_actions"]["blue-u1"]["move"]


class TestTheVerdictIsReproducibleFromTheArtifactAlone:
    """`analyse` re-grades a committed log with the same functions.

    A verdict only the process that produced the data can produce is not
    checkable by a reader. This one is: the committed identical-mind control is
    re-graded from disk and must agree with what the runner wrote into it.
    """

    def test_analysing_the_control_reproduces_its_own_verdicts(self) -> None:
        found = league_h2h.analyse(CONTROL_LOG)
        written = {
            r["rung"]: r
            for r in (
                json.loads(line) for line in CONTROL_LOG.read_text(encoding="utf-8").splitlines()
            )
            if r.get("kind") == "rung"
        }
        assert found["verdict"] == "INCONCLUSIVE"
        for rung in found["rungs"]:
            assert rung["verdict"] == written[rung["rung"]]["verdict"], rung["rung"]
            assert rung["matches_played"] == written[rung["rung"]]["matches_played"]
            for name, pairing in rung["pairings"].items():
                assert (
                    pairing["net_margin"] == written[rung["rung"]]["pairings"][name]["net_margin"]
                )

    def test_an_unplayed_rung_analyses_as_absent_by_name(self, tmp_path: Path) -> None:
        log = tmp_path / "partial.jsonl"
        log.write_text(json.dumps({"kind": "config", "config": {}}) + "\n", encoding="utf-8")
        found = league_h2h.analyse(log)
        # Every rung, named — not silently missing from the table.
        assert found["rungs_absent"] == [r.id for r in league_h2h.LADDER]
        assert found["verdict"] == "INCONCLUSIVE"

    def test_a_half_played_rung_analyses_as_absent_not_as_a_result(self, tmp_path: Path) -> None:
        rung = league_h2h.LADDER_BY_ID["L1"]
        lines = []
        for name, blue, red in league_h2h.rung_matches(rung)[:2]:
            record = _report(blue_arm=blue.id, red_arm=red.id, coops=(90, 80))
            record.update(
                {
                    "kind": "match",
                    "rung": "L1",
                    "match_id": league_h2h.match_id_for(rung, blue.id, red.id),
                    "truncated_turns": 0,
                    "cost": {c: {"completion_tokens": 0, "seconds": 0.0} for c in ("blue", "red")},
                }
            )
            lines.append(json.dumps(record))
        log = tmp_path / "half.jsonl"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        found = league_h2h.analyse(log)
        assert found["rungs"][0]["verdict"] == "ABSENT"
        assert found["rungs"][0]["matches_played"] == 2
        assert found["rungs"][0]["matches_planned"] == 6


class TestARequestedSubsetStillReportsTheRestAbsent:
    def test_a_one_rung_invocation_does_not_read_as_a_complete_ladder(self, tmp_path: Path) -> None:
        args = league_h2h.build_parser().parse_args(
            [
                "ladder",
                "--rungs",
                "L1",
                "--home",
                str(tmp_path / "home"),
                "--log",
                str(tmp_path / "log.jsonl"),
                "--league-bin",
                FAKE_BIN,
            ]
        )
        report = league_h2h.run_ladder(args)
        assert report["rungs_requested"] == ["L1"]
        assert report["rungs_absent"] == ["L2", "L3", "L4"]


class TestTheCommittedSeriesMatchesWhatTheWriteUpClaims:
    """The results document is checked against its own artifact.

    Every headline number in ``league-h2h.md`` is recomputed here from the
    committed JSONL. A write-up that drifts from its data is the failure mode
    this repo's corrections list keeps recording, so it is a test rather than a
    proofreading pass.
    """

    LOG = RESULTS / "league-h2h.jsonl"
    DOC = RESULTS / "league-h2h.md"

    @classmethod
    def _records(cls, kind: str) -> list[dict[str, Any]]:
        return [r for r in league_h2h.read_log(cls.LOG) if r.get("kind") == kind]

    def test_the_series_and_its_write_up_are_committed(self) -> None:
        assert self.LOG.is_file()
        assert self.DOC.is_file()

    def test_the_verdict_is_separated_at_l1_with_l2_to_l4_absent(self) -> None:
        found = league_h2h.analyse(self.LOG)
        assert found["verdict"] == "SEPARATED"
        assert found["separated_at"] == "L1"
        assert found["rungs_absent"] == ["L2", "L3", "L4"]

    def test_all_three_pairings_separated_without_a_cycle(self) -> None:
        rung = self._records("rung")[0]
        assert rung["separated_count"] == 3
        assert rung["cyclic"] is False
        assert rung["implied_ordering"] == ["full-qwen", "mixed", "full-gemma"]

    def test_nothing_truncated_which_is_what_amendment_1_bought(self) -> None:
        seats = self._records("seat-turn")
        assert len(seats) == 36
        truncated = sum(s["cost"][role]["truncated"] for s in seats for role in ("cortex", "muse"))
        assert truncated == 0
        # And the Qwen cortex really did spend past the originally briefed cap,
        # so the amendment was not academic.
        qwen = [s for s in seats if s["arm"] in ("mixed", "full-qwen")]
        worst = max(s["cost"]["cortex"]["completion_tokens"] for s in qwen)
        assert worst > 3000, "the 3000-token brief would have truncated this"

    def test_no_transport_retry_or_failure_so_no_number_is_a_contention_artifact(
        self,
    ) -> None:
        seats = self._records("seat-turn")
        for field in ("retries", "failures"):
            assert sum(s["cost"][role][field] for s in seats for role in ("cortex", "muse")) == 0
        assert all(m["transport_failure"] is None for m in self._records("match"))

    def test_the_game_metric_tied_zero_zero_in_every_match(self) -> None:
        for match in self._records("match"):
            outcome = match["score"]["outcome"]
            assert outcome["blue"]["total"] == outcome["red"]["total"] == 0

    def test_the_separation_is_message_utility_and_the_doc_says_so(self) -> None:
        rates = {arm: [0, 0] for arm in league_h2h.ARMS}
        for seat in self._records("seat-turn"):
            rates[seat["arm"]][1] += 1
            if seat["orders"].get("messages"):
                rates[seat["arm"]][0] += 1
        assert rates["full-gemma"] == [0, 12]
        assert rates["mixed"] == [4, 12]
        assert rates["full-qwen"] == [9, 12]
        text = self.DOC.read_text(encoding="utf-8")
        assert "0 / 12" in text
        assert "4 / 12" in text
        assert "9 / 12" in text
        assert "interface compliance, not on" in text

    def test_the_cost_column_in_the_doc_matches_the_artifact(self) -> None:
        cost = league_h2h.analyse(self.LOG)["cost"]["per_arm"]
        assert cost["full-gemma"]["completion_tokens_per_match"] == 950.0
        assert cost["mixed"]["completion_tokens_per_match"] == 12818.8
        assert cost["full-qwen"]["completion_tokens_per_match"] == 18409.8
        text = self.DOC.read_text(encoding="utf-8")
        for number in ("950", "12,819", "18,410"):
            assert number in text

    def test_every_committed_seat_turn_carries_its_raw_transcript(self) -> None:
        for seat in self._records("seat-turn"):
            assert seat["transcript"], seat["match_id"]
            assert any(t["role"] == "cortex" for t in seat["transcript"])

    def test_the_defective_rejections_counter_is_pinned_as_defective(self) -> None:
        """The harness's own faults counter disagrees with league's, 0 against 2.

        Pinned rather than repaired: the pre-registration is committed and the
        series is run, so swapping an instrument now would be indistinguishable
        from tuning one to a result. This test exists so that fixing it is a
        visible diff against a documented defect rather than a silent
        improvement to a published number.
        """
        mine = 0
        theirs = 0
        for match in self._records("match"):
            for colour in ("blue", "red"):
                mine += int(match["rejections"][colour])
                theirs += int(
                    match["score"]["cooperation"][colour]["components"]["discipline"]["rejected"]
                )
        assert (mine, theirs) == (0, 2), "the recorded defect changed shape"
        # It never decided anything: every match fell to the second tie-break.
        assert {m["result"]["decided_by"] for m in self._records("match")} == {"cooperation_v1"}
        assert "defective instrument" in self.DOC.read_text(encoding="utf-8").lower()
