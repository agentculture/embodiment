"""The config strategist's bounded review loop (task t9).

Five things are pinned here, and the first two are the ones a reader should not
have to take on trust:

1. **Termination is structural.** ``_review_loop`` has exactly four ``return``
   statements, no ``raise``, no ``try`` and no second loop, and exactly one
   statement in the whole module advances the turn counter. Read as AST, the way
   ``tests/test_scope.py`` reads the lane this one is cited from.
2. **The authority text is DERIVED, not transcribed** — embodiment#58's lesson.
   Every admission rule the schema and the gate actually enforce appears in the
   prompt because it is built from those same constants, so a rule added
   downstream cannot go unstated.
3. **Degrade, never raise.** A dead seam, a hostile snapshot, a truncated batch,
   an unparseable object and a well-formed empty batch each produce a recorded
   :class:`~embodiment.config_change.ConfigDegradation` and a clean exit.
4. **Every code this module declares can be fired**, and nothing else is minted.
5. **Nothing here reaches the advisory lane or the actor loop.**
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from embodiment.capability import Capability, CapabilityCatalog
from embodiment.config_change import (
    CHANGE_AUTHORITY,
    CHANGE_ORIGINS,
    CHANGE_TARGETS,
    FORBIDDEN_CHANGE_KEYS,
    ORIGIN_STRATEGIST,
    ORIGIN_WORKER,
    TARGET_SENSES_PROMPTS,
    TARGET_WORKER_PROMPTS,
    TARGET_WORKER_TOOLS,
    ConfigRefusal,
    declared_keys,
)
from embodiment.config_lifecycle import LIFECYCLE_RULES, KnowledgeEntry, PromptSection, SeatConfig
from embodiment.config_review import (
    CONFIG_AUTHORITY,
    CONFIG_EXIT_BUDGET,
    CONFIG_EXIT_CHANGES,
    CONFIG_EXIT_DEGRADED,
    CONFIG_EXIT_REASONS,
    CONFIG_EXIT_UNCHANGED,
    MARKER_CHANGES,
    MARKER_HOLD,
    REVIEW_CODES,
    REVIEW_DEGRADED_EMPTY,
    REVIEW_DEGRADED_MALFORMED,
    REVIEW_DEGRADED_SEAM,
    REVIEW_DEGRADED_TRUNCATED,
    REVIEW_DEGRADED_UNREADABLE,
    SNAPSHOT_HEADER,
    ConfigControls,
    ConfigOutcome,
    ConfigReviewLoop,
    ConfigSnapshot,
)
from embodiment.contract import ModelResponse

_SOURCE = Path(__file__).resolve().parents[1] / "embodiment" / "config_review.py"


# ── doubles ───────────────────────────────────────────────────────────────────


class Seam:
    """A scripted reviewer seam: replay answers, record every message list."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        self.calls.append([dict(entry) for entry in messages])
        item = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog(
        entries=(
            Capability(capability_id="read_file", kind="tool"),
            Capability(capability_id="write_file", kind="tool"),
        ),
        catalog_id="test-host",
    )


def _unit(**kw: Any) -> dict[str, Any]:
    base = {
        "target": TARGET_WORKER_PROMPTS,
        "change_id": "c1",
        "origin": ORIGIN_STRATEGIST,
        "section": "care",
        "text": "be careful with migrations",
    }
    base.update(kw)
    return base


def _batch(*units: dict[str, Any]) -> str:
    return f"{MARKER_CHANGES} " + json.dumps({"changes": list(units)})


def _loop(*answers: Any, **kw: Any) -> tuple[ConfigReviewLoop, Seam]:
    seam = Seam(*answers)
    kw.setdefault("catalog", _catalog())
    return ConfigReviewLoop(seam, **kw), seam


def _snapshot(**kw: Any) -> ConfigSnapshot:
    base: dict[str, Any] = {
        "snapshot_id": "s1",
        "summary": "the worker keeps failing the same migration",
        "observations": ("read_file x4", "run_tests failed twice"),
        "problems": ("the worker does not read the migration guide first",),
        "seats": (
            SeatConfig(
                seat="worker",
                prompt=(PromptSection(section="care", text="be quick"),),
                knowledge=(KnowledgeEntry(entry_id="k1", origin="worker", text="db is postgres"),),
                tools=("read_file",),
            ),
        ),
        "capabilities": ("read_file", "write_file"),
    }
    base.update(kw)
    return ConfigSnapshot(**base)


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in config_review.py")


def _codes(outcome: ConfigOutcome) -> list[str]:
    return [entry.code for entry in outcome.degradations]


# ── 1. termination is structural ──────────────────────────────────────────────


class TestTerminationIsStructural:
    """The cited loop's whole argument, re-proved on the copy that owns it."""

    def test_the_loop_has_exactly_four_returns(self) -> None:
        body = _function("_review_loop")
        returns = [node for node in ast.walk(body) if isinstance(node, ast.Return)]
        assert len(returns) == 4

    def test_each_return_is_one_of_the_four_declared_exits(self) -> None:
        body = _function("_review_loop")
        names = {
            node.value.id
            for node in ast.walk(body)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Name)
        }
        assert names == {
            "CONFIG_EXIT_DEGRADED",
            "CONFIG_EXIT_CHANGES",
            "CONFIG_EXIT_UNCHANGED",
            "CONFIG_EXIT_BUDGET",
        }

    def test_the_loop_carries_no_raise_no_try_and_no_second_loop(self) -> None:
        body = _function("_review_loop")
        for node in ast.walk(body):
            assert not isinstance(node, (ast.Raise, ast.Try, ast.For))
        whiles = [node for node in ast.walk(body) if isinstance(node, ast.While)]
        assert len(whiles) == 1

    def test_exactly_one_statement_in_the_module_advances_the_turn_counter(self) -> None:
        advances = [
            node
            for node in ast.walk(_tree())
            if isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Attribute)
            and node.target.attr == "turns"
        ]
        assert len(advances) == 1
        assert _function("_model_turn").lineno < advances[0].lineno

    def test_the_counter_is_never_decremented(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        assert "turns -=" not in source

    def test_a_seam_that_never_answers_usefully_stops_at_the_budget(self) -> None:
        loop, seam = _loop(ModelResponse(content="still thinking about it"))
        outcome = loop.review(_snapshot())
        assert outcome.exit_reason == CONFIG_EXIT_BUDGET
        assert outcome.turns == ConfigControls().max_turns == len(seam.calls)

    def test_a_host_budget_of_zero_still_runs_exactly_one_turn(self) -> None:
        """``max(1, …)``: a zero budget is a configuration nobody means."""
        loop, seam = _loop(ModelResponse(content="thinking"), controls=ConfigControls(max_turns=0))
        outcome = loop.review(_snapshot())
        assert outcome.turns == 1 and len(seam.calls) == 1


# ── 2. the four exits ─────────────────────────────────────────────────────────


class TestTheFourExits:
    def test_a_batch_of_one_produces_changes(self) -> None:
        loop, _ = _loop(ModelResponse(content=_batch(_unit())))
        outcome = loop.review(_snapshot(), step_index=7)
        assert outcome.exit_reason == CONFIG_EXIT_CHANGES
        assert [change.change_id for change in outcome.changes] == ["c1"]
        assert outcome.step_index == 7
        assert outcome.turns == 1

    def test_a_hold_is_a_first_class_answer(self) -> None:
        loop, _ = _loop(ModelResponse(content=f"the prompts still fit. {MARKER_HOLD}"))
        outcome = loop.review(_snapshot())
        assert outcome.exit_reason == CONFIG_EXIT_UNCHANGED
        assert outcome.changes == ()
        assert outcome.degradations == ()

    def test_a_dead_seam_degrades_and_never_raises(self) -> None:
        loop, _ = _loop(RuntimeError("connection refused"))
        outcome = loop.review(_snapshot())
        assert outcome.exit_reason == CONFIG_EXIT_DEGRADED
        assert _codes(outcome) == [REVIEW_DEGRADED_SEAM]

    def test_a_seam_returning_none_is_the_same_fault_class(self) -> None:
        loop, _ = _loop(None)
        outcome = loop.review(_snapshot())
        assert outcome.exit_reason == CONFIG_EXIT_DEGRADED
        assert _codes(outcome) == [REVIEW_DEGRADED_SEAM]

    def test_the_exit_set_is_closed(self) -> None:
        assert len(CONFIG_EXIT_REASONS) == 4
        assert len(set(CONFIG_EXIT_REASONS)) == 4

    @pytest.mark.parametrize("reason", CONFIG_EXIT_REASONS)
    def test_every_declared_exit_is_reachable(self, reason: str) -> None:
        scripts: dict[str, Any] = {
            CONFIG_EXIT_CHANGES: ModelResponse(content=_batch(_unit())),
            CONFIG_EXIT_UNCHANGED: ModelResponse(content=MARKER_HOLD),
            CONFIG_EXIT_BUDGET: ModelResponse(content="hmm"),
            CONFIG_EXIT_DEGRADED: RuntimeError("dead"),
        }
        loop, _ = _loop(scripts[reason])
        assert loop.review(_snapshot()).exit_reason == reason


# ── 3. what a turn may carry ──────────────────────────────────────────────────


class TestTheTwoAcceptedShapes:
    def test_a_bare_unit_is_read_as_a_batch_of_one(self) -> None:
        loop, _ = _loop(ModelResponse(content=f"{MARKER_CHANGES} {json.dumps(_unit())}"))
        outcome = loop.review(_snapshot())
        assert outcome.exit_reason == CONFIG_EXIT_CHANGES
        assert len(outcome.changes) == 1

    def test_prose_around_the_object_is_fine(self) -> None:
        payload = json.dumps({"changes": [_unit()]})
        loop, _ = _loop(ModelResponse(content=f"Here is my reasoning.\n{payload}\nThat is all."))
        assert loop.review(_snapshot()).exit_reason == CONFIG_EXIT_CHANGES

    def test_a_brace_inside_a_string_cannot_unbalance_the_scan(self) -> None:
        unit = _unit(text="use the {placeholder} syntax")
        loop, _ = _loop(ModelResponse(content=_batch(unit)))
        outcome = loop.review(_snapshot())
        assert outcome.changes[0].text == "use the {placeholder} syntax"

    def test_an_object_naming_neither_shape_is_recorded(self) -> None:
        loop, _ = _loop(ModelResponse(content=json.dumps({"thoughts": "many"})))
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_MALFORMED in _codes(outcome)

    def test_a_changes_field_that_is_not_a_list_is_recorded(self) -> None:
        loop, _ = _loop(ModelResponse(content=json.dumps({"changes": "one please"})))
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_MALFORMED in _codes(outcome)

    def test_a_well_formed_empty_batch_says_so_rather_than_reading_as_a_hold(self) -> None:
        loop, _ = _loop(ModelResponse(content=json.dumps({"changes": []})))
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_EMPTY in _codes(outcome)
        assert outcome.exit_reason == CONFIG_EXIT_BUDGET

    def test_an_announced_batch_that_never_arrives_is_a_recorded_truncation(self) -> None:
        loop, _ = _loop(ModelResponse(content=f"{MARKER_CHANGES} {{'changes': ["))
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_MALFORMED in _codes(outcome)

    def test_a_turn_with_no_json_and_no_marker_is_a_reviewer_still_thinking(self) -> None:
        loop, _ = _loop(ModelResponse(content="let me consider the failure pattern"))
        outcome = loop.review(_snapshot())
        assert outcome.degradations == ()

    def test_unparseable_json_is_one_recorded_class(self) -> None:
        loop, _ = _loop(ModelResponse(content='{"changes": [oops]}'))
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_MALFORMED in _codes(outcome)

    def test_a_non_object_payload_is_recorded(self) -> None:
        loop, _ = _loop(ModelResponse(content="the answer is {}"))
        outcome = loop.review(_snapshot())
        # An empty object names neither shape.
        assert REVIEW_DEGRADED_MALFORMED in _codes(outcome)


class TestAdmissionIsT3s:
    def test_a_refused_unit_costs_a_turn_and_the_reviewer_may_correct_itself(self) -> None:
        loop, seam = _loop(
            ModelResponse(content=_batch(_unit(change_id=""))),
            ModelResponse(content=_batch(_unit())),
        )
        outcome = loop.review(_snapshot())
        assert outcome.exit_reason == CONFIG_EXIT_CHANGES
        assert outcome.turns == 2 == len(seam.calls)
        assert len(outcome.refusals) == 1

    def test_a_refusal_is_the_same_object_in_both_streams(self) -> None:
        loop, _ = _loop(ModelResponse(content=_batch(_unit(change_id=""))))
        outcome = loop.review(_snapshot())
        assert outcome.refusals
        for refusal in outcome.refusals:
            assert isinstance(refusal, ConfigRefusal)
            assert any(entry is refusal for entry in outcome.degradations)

    def test_a_worker_originated_prompt_write_is_refused_whole(self) -> None:
        """The authority lattice is t3's and this loop does not soften it."""
        loop, _ = _loop(
            ModelResponse(content=_batch(_unit(target=TARGET_SENSES_PROMPTS, origin=ORIGIN_WORKER)))
        )
        outcome = loop.review(_snapshot())
        assert outcome.changes == ()
        assert outcome.refusals

    def test_a_smuggled_command_key_is_refused_whole(self) -> None:
        loop, _ = _loop(ModelResponse(content=_batch(_unit(command="rm -rf /"))))
        outcome = loop.review(_snapshot())
        assert outcome.changes == ()
        assert outcome.refusals[0].code == "config-change-authority-violation"

    def test_a_capability_unit_needs_the_hosts_catalog(self) -> None:
        loop, _ = _loop(
            ModelResponse(
                content=_batch(
                    {
                        "target": TARGET_WORKER_TOOLS,
                        "change_id": "c2",
                        "origin": ORIGIN_STRATEGIST,
                        "capability_ids": ["read_file"],
                    }
                )
            ),
            catalog=None,
        )
        outcome = loop.review(_snapshot())
        assert outcome.changes == ()
        assert outcome.refusals[0].code == "config-change-no-catalog"

    def test_a_declared_capability_id_is_admitted(self) -> None:
        loop, _ = _loop(
            ModelResponse(
                content=_batch(
                    {
                        "target": TARGET_WORKER_TOOLS,
                        "change_id": "c2",
                        "origin": ORIGIN_STRATEGIST,
                        "capability_ids": ["read_file"],
                    }
                )
            )
        )
        assert loop.review(_snapshot()).exit_reason == CONFIG_EXIT_CHANGES

    def test_a_batch_over_the_cap_is_clipped_and_the_loss_is_named(self) -> None:
        units = [_unit(change_id=f"c{index}", section=f"s{index}") for index in range(6)]
        loop, _ = _loop(
            ModelResponse(content=_batch(*units)), controls=ConfigControls(max_changes=2)
        )
        outcome = loop.review(_snapshot())
        assert len(outcome.changes) == 2
        assert REVIEW_DEGRADED_TRUNCATED in _codes(outcome)


# ── 4. the derived authority text (embodiment#58) ─────────────────────────────


class TestTheAuthorityTextIsDerived:
    """A rule a proposer cannot read is a rule that only produces refusals."""

    @pytest.mark.parametrize("target", CHANGE_TARGETS)
    def test_every_target_is_named_with_its_whole_vocabulary(self, target: str) -> None:
        assert target in CONFIG_AUTHORITY
        for key in declared_keys(target):
            assert key in CONFIG_AUTHORITY

    @pytest.mark.parametrize("origin", CHANGE_ORIGINS)
    def test_every_origin_states_exactly_what_it_may_write(self, origin: str) -> None:
        assert origin in CONFIG_AUTHORITY
        for target in CHANGE_AUTHORITY[origin]:
            assert target in CONFIG_AUTHORITY

    @pytest.mark.parametrize("rule", LIFECYCLE_RULES)
    def test_every_gate_rule_is_stated_verbatim(self, rule: str) -> None:
        assert rule in CONFIG_AUTHORITY

    def test_the_change_id_must_be_new_rule_is_stated(self) -> None:
        """embodiment#58 by name: 47 of 93 proposals were refused on an unstated rule."""
        assert "change_id must be new" in CONFIG_AUTHORITY

    @pytest.mark.parametrize("key", FORBIDDEN_CHANGE_KEYS)
    def test_every_forbidden_key_is_listed(self, key: str) -> None:
        assert key in CONFIG_AUTHORITY

    def test_both_answer_shapes_are_stated(self) -> None:
        assert MARKER_HOLD in CONFIG_AUTHORITY
        assert MARKER_CHANGES in CONFIG_AUTHORITY
        assert '{"changes": [' in CONFIG_AUTHORITY

    def test_it_claims_no_deterministic_effect(self) -> None:
        """The frame's non-goal: the change is deterministic, the effect is not."""
        lowered = CONFIG_AUTHORITY.lower()
        assert "determinist" not in lowered
        assert "sample from a model" in lowered

    def test_it_says_the_acting_seat_never_reads_it(self) -> None:
        assert "Nothing you write is delivered to the acting seat as text" in CONFIG_AUTHORITY

    def test_the_authority_is_first_and_host_framing_only_appended(self) -> None:
        loop, seam = _loop(ModelResponse(content=MARKER_HOLD), system="the rig is a greenhouse")
        loop.review(_snapshot())
        system = seam.calls[0][0]["content"]
        assert system.startswith(CONFIG_AUTHORITY)
        assert system.endswith("the rig is a greenhouse")

    def test_no_host_framing_leaves_the_authority_byte_identical(self) -> None:
        loop, seam = _loop(ModelResponse(content=MARKER_HOLD))
        loop.review(_snapshot())
        assert seam.calls[0][0]["content"] == CONFIG_AUTHORITY


# ── 5. the snapshot is DATA ───────────────────────────────────────────────────


class TestTheSnapshotIsRenderedAsData:
    def test_it_is_framed_and_fenced(self) -> None:
        loop, seam = _loop(ModelResponse(content=MARKER_HOLD))
        loop.review(_snapshot())
        rendered = seam.calls[0][1]["content"]
        assert SNAPSHOT_HEADER in rendered
        assert rendered.rstrip().endswith("--- END RIG SNAPSHOT ---")

    def test_the_seats_current_configuration_is_shown(self) -> None:
        loop, seam = _loop(ModelResponse(content=MARKER_HOLD))
        loop.review(_snapshot())
        rendered = seam.calls[0][1]["content"]
        assert "seat worker" in rendered
        assert "be quick" in rendered
        assert "db is postgres" in rendered
        assert "read_file" in rendered

    def test_the_declared_capability_ids_are_shown(self) -> None:
        loop, seam = _loop(ModelResponse(content=MARKER_HOLD))
        loop.review(_snapshot())
        assert "declared capability ids" in seam.calls[0][1]["content"]

    def test_a_hostile_snapshot_field_is_named_rather_than_raised(self) -> None:
        class Hostile:
            snapshot_id = "s1"

            @property
            def summary(self) -> str:
                raise RuntimeError("hostile field")

        loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
        outcome = loop.review(Hostile())
        assert REVIEW_DEGRADED_UNREADABLE in _codes(outcome)
        assert outcome.exit_reason == CONFIG_EXIT_UNCHANGED

    def test_a_clipped_field_is_recorded_not_hidden(self) -> None:
        loop, _ = _loop(
            ModelResponse(content=MARKER_HOLD),
            controls=ConfigControls(max_context_chars=10),
        )
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_TRUNCATED in _codes(outcome)

    def test_an_entry_budget_that_bit_is_recorded(self) -> None:
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD), controls=ConfigControls(max_entries=1))
        outcome = loop.review(_snapshot())
        assert REVIEW_DEGRADED_TRUNCATED in _codes(outcome)

    def test_a_none_snapshot_still_reviews_rather_than_raising(self) -> None:
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
        assert loop.review(None).exit_reason == CONFIG_EXIT_UNCHANGED

    def test_the_snapshot_copies_what_it_is_handed(self) -> None:
        entries = ["one"]
        state = {"disk": "full"}
        snapshot = ConfigSnapshot("s1", observations=entries, resource_state=state)
        entries.append("two")
        state["disk"] = "empty"
        assert snapshot.observations == ("one",)
        assert snapshot.resource_state == {"disk": "full"}

    def test_a_bare_string_list_field_is_one_entry_not_many_characters(self) -> None:
        assert ConfigSnapshot("s1", observations="hello").observations == ("hello",)

    def test_it_serializes(self) -> None:
        data = _snapshot().to_dict()
        assert data["snapshot_id"] == "s1"
        assert data["seats"][0]["seat"] == "worker"
        assert json.dumps(data)


# ── 6. the vocabulary is complete and firable ─────────────────────────────────


class TestTheVocabulary:
    """embodiment#18: a code nothing can mint is a lie in the ledger."""

    @staticmethod
    def _fire(code: str) -> list[str]:
        """Drive the one input that makes *code* fire, and report what was recorded."""

        class Hostile:
            snapshot_id = "s1"

            @property
            def summary(self) -> str:
                raise RuntimeError("hostile field")

        if code == REVIEW_DEGRADED_SEAM:
            loop, _ = _loop(RuntimeError("dead port"))
            return _codes(loop.review(_snapshot()))
        if code == REVIEW_DEGRADED_UNREADABLE:
            loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
            return _codes(loop.review(Hostile()))
        if code == REVIEW_DEGRADED_TRUNCATED:
            loop, _ = _loop(
                ModelResponse(content=MARKER_HOLD), controls=ConfigControls(max_context_chars=5)
            )
            return _codes(loop.review(_snapshot()))
        if code == REVIEW_DEGRADED_MALFORMED:
            loop, _ = _loop(ModelResponse(content='{"changes": [oops]}'))
            return _codes(loop.review(_snapshot()))
        loop, _ = _loop(ModelResponse(content=json.dumps({"changes": []})))
        return _codes(loop.review(_snapshot()))

    @pytest.mark.parametrize("code", REVIEW_CODES)
    def test_every_declared_code_can_be_fired(self, code: str) -> None:
        assert code in self._fire(code)

    def test_every_code_is_prefixed_for_its_lane(self) -> None:
        assert all(code.startswith("config-review-") for code in REVIEW_CODES)
        assert len(set(REVIEW_CODES)) == len(REVIEW_CODES)

    def test_a_record_carries_the_step_it_was_about(self) -> None:
        loop, _ = _loop(RuntimeError("dead"))
        outcome = loop.review(_snapshot(), step_index=11)
        assert outcome.degradations[0].step_index == 11

    def test_the_outcome_serializes(self) -> None:
        loop, _ = _loop(ModelResponse(content=_batch(_unit())))
        data = loop.review(_snapshot()).to_dict()
        assert data["exit_reason"] == CONFIG_EXIT_CHANGES
        assert json.dumps(data)


# ── 7. accounting a host can rely on ──────────────────────────────────────────


class TestTheMeasurementSurface:
    def test_tokens_are_summed_when_reported(self) -> None:
        loop, _ = _loop(
            ModelResponse(content="thinking", prompt_tokens=100, completion_tokens=20),
            ModelResponse(content=MARKER_HOLD, prompt_tokens=110, completion_tokens=5),
        )
        assert loop.review(_snapshot()).tokens == 235

    def test_an_unreported_pair_stays_none_rather_than_zero(self) -> None:
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
        assert loop.review(_snapshot()).tokens is None

    def test_no_clock_means_no_fabricated_latency(self) -> None:
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
        assert loop.review(_snapshot()).latency is None

    def test_an_injected_clock_measures_the_review(self) -> None:
        ticks = iter([10.0, 12.5])
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD), clock=lambda: next(ticks))
        assert loop.review(_snapshot()).latency == 2.5

    def test_a_raising_clock_is_an_unmeasured_latency_not_a_crash(self) -> None:
        def boom() -> float:
            raise RuntimeError("no clock")

        loop, _ = _loop(ModelResponse(content=MARKER_HOLD), clock=boom)
        assert loop.review(_snapshot()).latency is None

    def test_the_review_index_advances(self) -> None:
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
        assert loop.review(_snapshot()).review_index == 1
        assert loop.review(_snapshot()).review_index == 2
        assert loop.reviews == 2

    def test_provenance_rides_the_outcome(self) -> None:
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD), model="a-model", role="strategist")
        outcome = loop.review(_snapshot())
        assert (outcome.model, outcome.role) == ("a-model", "strategist")

    def test_an_undeclared_model_stays_empty(self) -> None:
        """A single-model run must not claim another mind exists (colleague#352)."""
        loop, _ = _loop(ModelResponse(content=MARKER_HOLD))
        assert loop.review(_snapshot()).model == ""


# ── 8. cited, not coupled ─────────────────────────────────────────────────────


class TestCitedNotCoupled:
    @staticmethod
    def _imported() -> set[str]:
        reached: set[str] = set()
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Import):
                reached.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                reached.add(node.module)
        return reached

    def test_neither_the_advisory_lane_nor_the_actor_loop_is_imported(self) -> None:
        banned = {
            "embodiment.scope",
            "embodiment.scoped_run",
            "embodiment.strategist_runner",
            "embodiment.scope_events",
            "embodiment.loop",
        }
        assert not (self._imported() & banned)

    def test_the_closure_is_this_tiers_own_three_modules(self) -> None:
        internal = {name for name in self._imported() if name.startswith("embodiment")}
        assert internal == {
            "embodiment.capability",
            "embodiment.config_change",
            "embodiment.config_lifecycle",
        }

    def test_scope_py_is_cited_in_prose(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        assert "scope.py" in source, "the cited loop's origin is uncited"

    def test_no_tool_bench_surface_exists(self) -> None:
        """Deliberately not copied — see the module docstring."""
        source = _SOURCE.read_text(encoding="utf-8")
        assert "ToolBench" not in source.replace("ScopeToolBench``", "")

    def test_the_module_constructs_no_tool_surface(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        assert "tool_calls" not in source


class TestTheseChecksCanFail:
    """A guard nobody proved can fail is a guard nobody has."""

    def test_a_fifth_return_would_be_caught(self) -> None:
        body = ast.parse(
            "def _review_loop(ctx):\n"
            "    while ctx.turns < ctx.budget:\n"
            "        return 1\n"
            "    return 2\n"
            "    return 3\n"
            "    return 4\n"
            "    return 5\n"
        )
        returns = [node for node in ast.walk(body) if isinstance(node, ast.Return)]
        assert len(returns) == 5, "the return-counting check itself is broken"

    def test_an_unstated_rule_would_be_caught(self) -> None:
        assert "a rule nobody wrote down" not in CONFIG_AUTHORITY
