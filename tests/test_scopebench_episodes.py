"""ScopeBench episodes — the machine-gradable world (plan task ``t9``).

The episode schema is deliverable 1 of ``t9``: every episode must carry several
candidate objectives, dependencies between workstreams, limited resources,
actors with different capability/cost profiles, durable constraints, events that
change state over time, at least one locally-attractive-but-globally-wrong
action, and at least one valid "do not intervene" state.

Six of those eight are structural facts about the episode and are checked here.
The last two are **computed facts** — they need the oracle to say whether the
locally attractive move really does forgo value, and whether holding really is
uniquely optimal — so they are checked in ``tests/test_scopebench_oracle.py``.
That split is deliberate: a generator that merely *claims* a trap would be a
generator marking its own homework.

Nothing in this file dials a model. Nothing in ``examples/scope/`` does either,
and ``tests/test_scopebench.py`` proves it structurally.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from examples.scope import episodes as ep

MODULE_PATH = Path(ep.__file__)


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))


# ══════════════════════════════════════════════════════════════════════════════
# the family catalog is data, and the deferred cells are declared
# ══════════════════════════════════════════════════════════════════════════════


class TestFamilyCatalog:
    def test_eight_families_are_declared(self) -> None:
        assert len(ep.FAMILY_ORDER) == 8
        assert set(ep.FAMILY_ORDER) == set(ep.FAMILIES)

    def test_first_cycle_and_deferred_partition_the_catalog(self) -> None:
        assert set(ep.FIRST_CYCLE) | set(ep.DEFERRED) == set(ep.FAMILY_ORDER)
        assert not set(ep.FIRST_CYCLE) & set(ep.DEFERRED)

    def test_every_deferred_family_carries_a_reason(self) -> None:
        for name, reason in ep.DEFERRED.items():
            assert reason.strip(), f"{name} is deferred with no stated reason"

    def test_every_family_states_what_it_stresses(self) -> None:
        for name in ep.FAMILY_ORDER:
            family = ep.FAMILIES[name]
            assert family.stresses.strip()
            assert family.why.strip()

    def test_the_first_cycle_has_at_least_the_two_the_verdict_rule_needs(self) -> None:
        """Condition 1 wants two independent families; a one-family cycle cannot
        satisfy the rule it is being run under."""
        assert len(ep.FIRST_CYCLE) >= 2

    def test_the_non_intervention_family_is_in_the_first_cycle(self) -> None:
        """Condition 3 grades non-intervention. Deferring that family would make
        the condition permanently ABSENT."""
        assert ep.FAMILY_STEADY_STATE in ep.FIRST_CYCLE

    def test_nothing_branches_on_a_family_id(self) -> None:
        """A family is data. A comparison against a family literal would make the
        generator a code branch and the families no longer a uniform set."""
        literals = set(ep.FAMILY_ORDER)
        for node in ast.walk(_tree()):
            if not isinstance(node, ast.Compare):
                continue
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Constant) and operand.value in literals:
                    raise AssertionError(f"a family id is compared: {ast.dump(node)}")

    def test_every_first_cycle_family_has_a_generator(self) -> None:
        for name in ep.FIRST_CYCLE:
            assert name in ep.GENERATORS

    def test_a_deferred_family_has_no_generator(self) -> None:
        for name in ep.DEFERRED:
            assert name not in ep.GENERATORS


# ══════════════════════════════════════════════════════════════════════════════
# generation is deterministic and total
# ══════════════════════════════════════════════════════════════════════════════


def _every_first_cycle_episode() -> list[ep.Episode]:
    seeds = ep.load_seeds()
    return [
        ep.generate(family, seed) for family in ep.FIRST_CYCLE for seed in seeds.for_family(family)
    ]


class TestGeneration:
    @pytest.mark.parametrize("family", ep.FIRST_CYCLE)
    def test_the_same_seed_gives_a_byte_identical_episode(self, family: str) -> None:
        first = ep.generate(family, 11)
        second = ep.generate(family, 11)
        assert first.to_dict() == second.to_dict()

    @pytest.mark.parametrize("family", ep.FIRST_CYCLE)
    def test_different_seeds_give_different_episodes(self, family: str) -> None:
        assert ep.generate(family, 11).to_dict() != ep.generate(family, 12).to_dict()

    def test_an_unknown_family_is_refused_by_name(self) -> None:
        with pytest.raises(ep.EpisodeError, match="nowhere"):
            ep.generate("nowhere", 1)

    def test_a_deferred_family_is_refused_with_its_reason(self) -> None:
        name = next(iter(ep.DEFERRED))
        with pytest.raises(ep.EpisodeError, match="deferred"):
            ep.generate(name, 1)

    def test_the_episode_id_names_its_family_and_seed(self) -> None:
        episode = ep.generate(ep.FAMILY_CRITICAL_PATH, 7)
        assert episode.family == ep.FAMILY_CRITICAL_PATH
        assert episode.seed == 7
        assert "7" in episode.id and ep.FAMILY_CRITICAL_PATH in episode.id

    def test_to_dict_round_trips(self) -> None:
        for episode in _every_first_cycle_episode()[:6]:
            assert ep.Episode.from_dict(episode.to_dict()).to_dict() == episode.to_dict()

    def test_every_episode_serialises_to_json(self) -> None:
        for episode in _every_first_cycle_episode()[:6]:
            json.dumps(episode.to_dict())


# ══════════════════════════════════════════════════════════════════════════════
# the six structural schema properties, on EVERY committed episode
# ══════════════════════════════════════════════════════════════════════════════


class TestStructuralSchema:
    def test_the_eight_properties_are_declared_with_reasons(self) -> None:
        assert len(ep.SCHEMA_PROPERTIES) == 8
        for name in ep.SCHEMA_PROPERTIES:
            assert ep.SCHEMA_WHY[name].strip()

    def test_the_structural_and_verified_properties_partition_the_eight(self) -> None:
        assert set(ep.STRUCTURAL_PROPERTIES) | set(ep.VERIFIED_PROPERTIES) == set(
            ep.SCHEMA_PROPERTIES
        )
        assert not set(ep.STRUCTURAL_PROPERTIES) & set(ep.VERIFIED_PROPERTIES)

    def test_every_committed_episode_satisfies_every_structural_property(self) -> None:
        failures: list[str] = []
        for episode in _every_first_cycle_episode():
            report = ep.structural_report(episode)
            for name in ep.STRUCTURAL_PROPERTIES:
                if not report[name]:
                    failures.append(f"{episode.id}: {name}")
        assert not failures, failures

    def test_a_gutted_episode_fails_the_structural_report(self) -> None:
        """The guard is not vacuous: strip the events and it says so."""
        episode = ep.generate(ep.FAMILY_DISRUPTION, 3)
        stripped = ep.Episode.from_dict({**episode.to_dict(), "events": []})
        assert not ep.structural_report(stripped)[ep.PROPERTY_EVENTS]

    def test_a_single_actor_episode_fails_the_profile_property(self) -> None:
        episode = ep.generate(ep.FAMILY_ALLOCATION, 3)
        raw = episode.to_dict()
        raw["actors"] = raw["actors"][:1]
        assert not ep.structural_report(ep.Episode.from_dict(raw))[ep.PROPERTY_ACTOR_PROFILES]

    def test_an_unlimited_budget_fails_the_resource_property(self) -> None:
        episode = ep.generate(ep.FAMILY_CONTENTION, 3)
        raw = episode.to_dict()
        raw["budget"] = 10**9
        assert not ep.structural_report(ep.Episode.from_dict(raw))[ep.PROPERTY_LIMITED_RESOURCES]


# ══════════════════════════════════════════════════════════════════════════════
# the world is well formed
# ══════════════════════════════════════════════════════════════════════════════


class TestWorldIsWellFormed:
    def test_review_ticks_are_increasing_and_inside_the_horizon(self) -> None:
        for episode in _every_first_cycle_episode():
            ticks = episode.review_ticks
            assert ticks[0] == 0
            assert list(ticks) == sorted(set(ticks))
            assert ticks[-1] < episode.horizon

    def test_every_dependency_names_a_declared_workstream(self) -> None:
        for episode in _every_first_cycle_episode():
            known = {stream.id for stream in episode.workstreams}
            for stream in episode.workstreams:
                assert set(stream.depends_on) <= known
                assert stream.id not in stream.depends_on

    def test_no_dependency_cycle_exists(self) -> None:
        for episode in _every_first_cycle_episode():
            assert ep.dependency_order(episode) is not None, episode.id

    def test_every_objective_requires_declared_workstreams(self) -> None:
        for episode in _every_first_cycle_episode():
            known = {stream.id for stream in episode.workstreams}
            for objective in episode.objectives:
                assert objective.requires
                assert set(objective.requires) <= known

    def test_every_event_lands_inside_the_horizon(self) -> None:
        for episode in _every_first_cycle_episode():
            for event in episode.events:
                assert 0 <= event.tick < episode.horizon
                assert event.kind in ep.EVENT_KINDS

    def test_every_constraint_kind_is_declared(self) -> None:
        for episode in _every_first_cycle_episode():
            for constraint in episode.constraints:
                assert constraint.kind in ep.CONSTRAINT_KINDS
                assert constraint.text.strip(), "a durable constraint with no prose"
                assert constraint.penalty > 0

    def test_the_default_allocation_is_canonical_and_executable(self) -> None:
        for episode in _every_first_cycle_episode():
            allocation = episode.default_allocation
            assert [pair[0] for pair in allocation] == [actor.id for actor in episode.actors]
            for actor_id, target in allocation:
                assert target == ep.IDLE or target in {s.id for s in episode.workstreams}
                assert ep.executable(episode, actor_id, target)

    def test_allocations_are_canonicalised_into_actor_order(self) -> None:
        episode = ep.generate(ep.FAMILY_CONTENTION, 5)
        reversed_pairs = tuple(reversed(episode.default_allocation))
        assert ep.canonical(episode, reversed_pairs) == episode.default_allocation

    def test_an_allocation_naming_an_unknown_actor_is_dropped_by_canonicalisation(self) -> None:
        episode = ep.generate(ep.FAMILY_CONTENTION, 5)
        polluted = (*episode.default_allocation, ("ghost", "anything"))
        assert ep.canonical(episode, polluted) == episode.default_allocation


# ══════════════════════════════════════════════════════════════════════════════
# the committed seeds
# ══════════════════════════════════════════════════════════════════════════════


class TestCommittedSeeds:
    def test_the_seeds_file_is_committed(self) -> None:
        assert ep.SEEDS_PATH.exists(), f"{ep.SEEDS_PATH} must be committed before any live dial"

    def test_the_seeds_file_covers_exactly_the_first_cycle(self) -> None:
        seeds = ep.load_seeds()
        assert set(seeds.families) == set(ep.FIRST_CYCLE)

    def test_every_family_has_the_declared_number_of_unique_seeds(self) -> None:
        seeds = ep.load_seeds()
        for family in ep.FIRST_CYCLE:
            values = seeds.for_family(family)
            assert len(values) == seeds.episodes_per_family
            assert len(set(values)) == len(values)

    def test_the_seed_count_meets_the_pre_registered_minimum(self) -> None:
        assert ep.load_seeds().episodes_per_family >= ep.MIN_EPISODES_PER_FAMILY

    def test_the_deferred_families_are_recorded_in_the_file_too(self) -> None:
        """Condition 7 reports every absent cell; the absence starts here."""
        seeds = ep.load_seeds()
        assert set(seeds.deferred) == set(ep.DEFERRED)

    def test_a_seeds_file_missing_a_family_is_refused(self, tmp_path: Path) -> None:
        raw = json.loads(ep.SEEDS_PATH.read_text(encoding="utf-8"))
        raw["seeds"].pop(ep.FIRST_CYCLE[0])
        path = tmp_path / "seeds.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ep.EpisodeError, match=ep.FIRST_CYCLE[0]):
            ep.load_seeds(path)

    def test_a_seeds_file_with_a_short_family_is_refused(self, tmp_path: Path) -> None:
        raw = json.loads(ep.SEEDS_PATH.read_text(encoding="utf-8"))
        raw["seeds"][ep.FIRST_CYCLE[0]] = raw["seeds"][ep.FIRST_CYCLE[0]][:1]
        path = tmp_path / "seeds.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ep.EpisodeError, match="episodes_per_family"):
            ep.load_seeds(path)

    def test_a_missing_seeds_file_is_refused_by_path(self, tmp_path: Path) -> None:
        with pytest.raises(ep.EpisodeError, match="no ScopeBench seeds"):
            ep.load_seeds(tmp_path / "absent.json")
