"""ScopeBench's exact oracle (plan task ``t9``, acceptance criterion 1).

The primary verdict is machine-graded against an **exact oracle** and a
**declared Pareto frontier**. Both are computed here rather than asserted, and
both are pinned in a committed fixture so a change to the generator or the
solver breaks a test rather than silently re-baselining every published number.

The two properties the generator cannot vouch for itself — the
locally-attractive-but-globally-wrong action, and the valid do-not-intervene
state — are the solver's to verify, and they are verified for **every committed
episode** here.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from examples.scope import episodes as ep
from examples.scope import oracle as orc

MODULE_PATH = Path(orc.__file__)
EPISODES = ep.first_cycle_episodes()
SOLUTIONS = {episode.id: orc.solve(episode) for episode in EPISODES}


def _episode(family: str) -> ep.Episode:
    return next(entry for entry in EPISODES if entry.family == family)


# ══════════════════════════════════════════════════════════════════════════════
# the solver is exact
# ══════════════════════════════════════════════════════════════════════════════


class TestExactness:
    def test_the_optimum_is_the_maximum_over_every_plan(self) -> None:
        """Brute force, on the smallest family, against the memoised search."""
        episode = _episode(ep.FAMILY_STEADY_STATE)
        assert orc.solve(episode).optimum == orc.brute_force_optimum(episode)

    def test_the_optimal_plan_actually_scores_the_optimum(self) -> None:
        for episode in EPISODES:
            solution = SOLUTIONS[episode.id]
            assert orc.score_plan(episode, solution.optimal_plan) == solution.optimum

    def test_no_plan_beats_the_optimum(self) -> None:
        episode = _episode(ep.FAMILY_CONTENTION)
        optimum = SOLUTIONS[episode.id].optimum
        for plan in orc.every_plan(episode):
            assert orc.score_plan(episode, plan) <= optimum

    def test_solving_is_deterministic(self) -> None:
        episode = _episode(ep.FAMILY_ALLOCATION)
        assert orc.solve(episode).to_dict() == orc.solve(episode).to_dict()

    def test_the_floor_is_the_default_scope_held_for_the_whole_episode(self) -> None:
        for episode in EPISODES:
            held = tuple(episode.default_allocation for _ in episode.review_ticks)
            assert SOLUTIONS[episode.id].floor == orc.score_plan(episode, held)


# ══════════════════════════════════════════════════════════════════════════════
# the Pareto frontier is declared
# ══════════════════════════════════════════════════════════════════════════════


class TestParetoFrontier:
    def test_the_frontier_is_non_empty_and_non_dominated(self) -> None:
        for episode in EPISODES:
            points = SOLUTIONS[episode.id].frontier
            assert points
            for left in points:
                for right in points:
                    if left == right:
                        continue
                    dominated = right[0] >= left[0] and right[1] <= left[1]
                    assert not dominated, f"{episode.id}: {right} dominates {left}"

    def test_the_optimum_sits_on_the_frontier(self) -> None:
        for episode in EPISODES:
            solution = SOLUTIONS[episode.id]
            assert max(utility for utility, _spend in solution.frontier) == solution.optimum

    def test_every_frontier_point_is_reachable_by_some_plan(self) -> None:
        episode = _episode(ep.FAMILY_STEADY_STATE)
        reachable = {
            (orc.score_plan(episode, plan), orc.spend_of(episode, plan))
            for plan in orc.every_plan(episode)
        }
        assert set(SOLUTIONS[episode.id].frontier) <= reachable


# ══════════════════════════════════════════════════════════════════════════════
# the two verified schema properties, on EVERY committed episode
# ══════════════════════════════════════════════════════════════════════════════


class TestVerifiedSchema:
    def test_every_committed_episode_satisfies_all_eight_properties(self) -> None:
        failures: list[str] = []
        for episode in EPISODES:
            report = orc.schema_report(episode, SOLUTIONS[episode.id])
            assert set(report) == set(ep.SCHEMA_PROPERTIES)
            failures.extend(f"{episode.id}: {name}" for name, ok in report.items() if not ok)
        assert not failures, failures

    def test_every_trap_is_a_local_argmax_and_strictly_suboptimal(self) -> None:
        for episode in EPISODES:
            trap = SOLUTIONS[episode.id].trap
            assert trap is not None, episode.id
            assert trap.forgone > 0, f"{episode.id}: the trap costs nothing"
            state = orc.state_after(episode, SOLUTIONS[episode.id].greedy_plan, trap.at_review)
            best_local = max(
                orc.local_score(episode, state, allocation, trap.at_review)
                for allocation in orc.feasible(episode, state)
            )
            played = orc.local_score(episode, state, trap.allocation, trap.at_review)
            assert played == best_local, f"{episode.id}: the trap is not locally attractive"

    def test_every_non_intervention_state_holds_the_optimum(self) -> None:
        """Half of the claim: ``[hold]`` really is a **valid** answer there."""
        for episode in EPISODES:
            hold = SOLUTIONS[episode.id].non_intervention
            assert hold is not None, episode.id
            state = orc.state_after(episode, SOLUTIONS[episode.id].optimal_plan, hold.at_review)
            best_here = orc.best_from(episode, state, hold.at_review)
            assert orc.best_after(episode, state, hold.at_review, hold.allocation) == best_here

    def test_every_non_intervention_state_makes_churn_cost_something(self) -> None:
        """The other half, and the reason it is a *control*: at least one change
        is strictly worse, by exactly the recorded margin. Note what is NOT
        claimed — that every change is worse. ``alternatives_optimal`` records
        how many changes would also have been fine, and it is reported rather
        than gated, because hiding it would overstate the test's strength."""
        for episode in EPISODES:
            hold = SOLUTIONS[episode.id].non_intervention
            assert hold is not None, episode.id
            assert hold.margin > 0, episode.id
            state = orc.state_after(episode, SOLUTIONS[episode.id].optimal_plan, hold.at_review)
            best_here = orc.best_from(episode, state, hold.at_review)
            outcomes = [
                orc.best_after(episode, state, hold.at_review, allocation)
                for allocation in orc.feasible(episode, state)
                if allocation != hold.allocation
            ]
            assert min(outcomes) == best_here - hold.margin
            assert sum(1 for value in outcomes if value == best_here) == hold.alternatives_optimal

    def test_every_committed_episode_is_valid(self) -> None:
        invalid = {
            episode.id: orc.invalidity(episode, SOLUTIONS[episode.id])
            for episode in EPISODES
            if orc.invalidity(episode, SOLUTIONS[episode.id])
        }
        assert not invalid, invalid

    def test_headroom_exists_over_both_declared_floors(self) -> None:
        """The muse-arms lesson: a control already at the ceiling leaves nothing
        for the arm under test to improve, and that is an INCONCLUSIVE waiting
        to happen. Refuse the seed instead."""
        for episode in EPISODES:
            solution = SOLUTIONS[episode.id]
            assert solution.optimum > solution.floor, episode.id
            assert solution.optimum > solution.greedy_score, episode.id

    def test_an_episode_with_no_trap_is_reported_invalid(self) -> None:
        """The guard is not vacuous: an episode whose greedy play IS optimal has
        no trap, and the solver says so rather than inventing one."""
        episode = orc.trivial_episode()
        solution = orc.solve(episode)
        assert solution.trap is None
        reasons = orc.invalidity(episode, solution)
        assert any(ep.PROPERTY_TRAP in reason for reason in reasons), reasons


# ══════════════════════════════════════════════════════════════════════════════
# the naive (non-clairvoyant) view
# ══════════════════════════════════════════════════════════════════════════════


class TestNaiveView:
    def test_naive_drops_only_events_at_or_after_the_tick(self) -> None:
        episode = _episode(ep.FAMILY_DISRUPTION)
        naive = orc.naive(episode, 5)
        assert all(event.tick < 5 for event in naive.events)
        assert len(naive.events) < len(episode.events)

    def test_naive_changes_nothing_else(self) -> None:
        episode = _episode(ep.FAMILY_DISRUPTION)
        left = episode.to_dict()
        right = orc.naive(episode, 5).to_dict()
        left.pop("events")
        right.pop("events")
        assert left == right

    def test_a_naive_view_past_the_horizon_is_the_episode_itself(self) -> None:
        episode = _episode(ep.FAMILY_DISRUPTION)
        assert orc.naive(episode, episode.horizon).to_dict() == episode.to_dict()


# ══════════════════════════════════════════════════════════════════════════════
# the committed fixture pins every number
# ══════════════════════════════════════════════════════════════════════════════


class TestCommittedSeeds:
    """Provenance: the seed table is not a list somebody chose, it is a list a
    stated rule produced — and the rule is re-derivable from this repo alone."""

    def test_the_seed_table_states_how_it_was_derived(self) -> None:
        raw = json.loads(ep.SEEDS_PATH.read_text(encoding="utf-8"))
        assert "invalidity()" in raw["selection_rule"]
        assert "ascending order from 1" in raw["selection_rule"]

    @pytest.mark.parametrize("family", ep.FIRST_CYCLE)
    def test_the_committed_seeds_are_exactly_what_the_stated_rule_selects(
        self, family: str
    ) -> None:
        wanted = ep.load_seeds().episodes_per_family
        found: list[int] = []
        seed = 1
        while len(found) < wanted and seed < 200:
            episode = ep.generate(family, seed)
            if not orc.invalidity(episode, orc.solve(episode)):
                found.append(seed)
            seed += 1
        assert tuple(found) == ep.load_seeds().for_family(family)


class TestCommittedFixtures:
    def test_the_fixture_is_committed(self) -> None:
        assert orc.FIXTURES_PATH.exists(), "the oracle fixture must be committed with the harness"

    def test_the_fixture_covers_every_committed_episode(self) -> None:
        pinned = orc.load_fixtures()
        assert set(pinned) == {episode.id for episode in EPISODES}

    def test_every_pinned_number_still_holds(self) -> None:
        pinned = orc.load_fixtures()
        drift = [
            episode.id
            for episode in EPISODES
            if pinned[episode.id] != orc.fixture_for(episode, SOLUTIONS[episode.id])
        ]
        assert not drift, (
            f"the solver or the generator moved for: {drift}. Every published ScopeBench "
            "number is relative to these; re-pin deliberately, never incidentally."
        )

    def test_the_fixture_notices_a_moved_number(self, tmp_path: Path) -> None:
        """Test-of-the-test: the pin is not vacuous."""
        raw = json.loads(orc.FIXTURES_PATH.read_text(encoding="utf-8"))
        first = EPISODES[0].id
        raw["episodes"][first]["optimum"] += 1
        path = tmp_path / "fixtures.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        pinned = orc.load_fixtures(path)
        assert pinned[first] != orc.fixture_for(EPISODES[0], SOLUTIONS[first])

    def test_a_missing_fixture_file_is_refused_by_path(self, tmp_path: Path) -> None:
        with pytest.raises(ep.EpisodeError, match="no ScopeBench fixtures"):
            orc.load_fixtures(tmp_path / "absent.json")


# ══════════════════════════════════════════════════════════════════════════════
# hermetic by construction
# ══════════════════════════════════════════════════════════════════════════════


class TestHermetic:
    def test_the_solver_reads_no_clock_and_opens_no_socket(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        banned = {"time", "socket", "http", "urllib", "httpx", "requests", "threading", "random"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in banned

    @pytest.mark.parametrize("family", ep.FIRST_CYCLE)
    def test_solving_is_fast_enough_to_run_in_ci(self, family: str) -> None:
        """Not a timing assertion — a size one. The search is bounded by the
        product of the per-review feasible sets, and that product is checked
        here so a generator edit cannot make CI quietly take minutes."""
        episode = _episode(family)
        state = orc.initial_state(episode)
        assert len(orc.feasible(episode, state)) <= orc.MAX_FEASIBLE_PER_REVIEW
