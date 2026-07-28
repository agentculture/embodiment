"""Tests for the association-work 2x2 (plan task t18).

Two jobs, in this order:

1. **Pin the pre-registered decision rule.** The experiment's whole integrity is
   that the threshold was fixed before the first dial. A threshold that can be
   nudged afterwards makes the promotion gate a rubber stamp, so the constants
   are asserted here by value: moving one means editing a test that says so out
   loud, in a diff a reviewer sees.
2. **Prove the harnesses actually drive the loop.** The three executive
   harnesses were committed with ``Task(system=..., tools=...)`` and
   ``run(task=…, bench=…)`` — neither of which the contract has — so every
   invocation raised ``TypeError`` before its first model call, and no test
   caught it because every test graded ``truth``/``grade`` and none ran ``main``.
   These tests drive the whole harness with a scripted seam.

The live class is ``TestLive``-prefixed so the module's ``_no_network`` bomb
lets it through, matching ``tests/test_muse_challenge.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from embodiment.contract import ModelResponse, ToolCall
from examples import association_work as aw
from examples import challenge_entropic, challenge_register, challenge_subset


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No in-process test may dial anything. Live classes opt out by name."""
    if "TestLive" in request.node.nodeid:
        return
    import urllib.request

    def _bomb(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the hermetic path must never dial a network")

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)


def _scripted(answer: Any, *, turns_before_answer: int = 0) -> Any:
    """A seam that calls ``finish`` with *answer*. Never touches a network."""
    state = {"n": 0}

    def complete(_messages: list[dict[str, Any]]) -> ModelResponse:
        state["n"] += 1
        if state["n"] <= turns_before_answer:
            return ModelResponse(content="still working")
        return ModelResponse(
            content="",
            tool_calls=[ToolCall(id=str(state["n"]), name="finish", arguments={"answer": answer})],
        )

    return complete


def _cell(name: str, axis: str, *, n: int, passes: int, errors: int = 0) -> aw.Cell:
    runs = [
        {"passed": index < passes, "transport_error": index >= n - errors} for index in range(n)
    ]
    return aw.Cell(name, axis, "model", runs)


# ── 1. the pre-registered rule, pinned by value ──────────────────────────────


class TestPreRegisteredThresholds:
    """The numbers were fixed before the first dial. Changing one is loud.

    An experiment whose threshold moves after a result is not evidence, it is a
    story told about a number. These assertions exist so that moving a threshold
    is a deliberate, reviewable act rather than an edit nobody notices.
    """

    def test_min_interaction_is_the_pre_registered_value(self) -> None:
        assert aw.MIN_INTERACTION == 0.40

    def test_min_reflective_gap_is_the_pre_registered_value(self) -> None:
        assert aw.MIN_REFLECTIVE_GAP == 0.25

    def test_transport_error_ceiling_is_one_third(self) -> None:
        assert aw.MAX_ERROR_FRACTION == pytest.approx(1.0 / 3.0)

    def test_the_two_axes_run_at_their_own_temperatures(self) -> None:
        """Recorded separately — an earlier series ran both at 0.2 unnoticed."""
        assert aw.REFLECTIVE_TEMPERATURE == 0.7
        assert aw.EXECUTIVE_TEMPERATURE == 0.3
        assert aw.REFLECTIVE_TEMPERATURE != aw.EXECUTIVE_TEMPERATURE

    def test_token_budget_is_generous_enough_for_a_thinking_model(self) -> None:
        """The rig's README records ``content: None`` on a budget spent thinking."""
        assert aw.MAX_TOKENS >= 6000

    def test_executive_axis_uses_the_constrained_entropic_variant(self) -> None:
        """Variant 3 grades on reporting under-determination — a reflective move."""
        assert "entropic3b" in aw.EXECUTIVE_PROBLEMS
        assert "entropic3" not in aw.EXECUTIVE_PROBLEMS

    def test_every_decision_blocks_or_supports_explicitly(self) -> None:
        assert aw.DECISIONS == (
            aw.DECISION_SUPPORTS,
            aw.DECISION_NEGATIVE,
            aw.DECISION_INCONCLUSIVE,
        )


class TestDecide:
    """The rule is a pure function of four cells, and it is total."""

    def test_a_concentrated_muse_advantage_supports_promotion(self) -> None:
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=12),
            _cell("reflective_cortex", "reflective", n=12, passes=3),
            _cell("executive_muse", "executive", n=9, passes=1),
            _cell("executive_cortex", "executive", n=9, passes=6),
        )
        assert verdict["decision"] == aw.DECISION_SUPPORTS
        assert verdict["d_reflective"] == pytest.approx(0.75)
        assert verdict["interaction"] > aw.MIN_INTERACTION

    def test_a_uniformly_better_muse_is_a_negative(self) -> None:
        """The map claims a SPLIT. A muse better at everything is not one."""
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=9),
            _cell("reflective_cortex", "reflective", n=12, passes=3),
            _cell("executive_muse", "executive", n=9, passes=6),
            _cell("executive_cortex", "executive", n=9, passes=2),
        )
        assert verdict["decision"] == aw.DECISION_NEGATIVE

    def test_a_uniformly_worse_muse_is_a_negative(self) -> None:
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=2),
            _cell("reflective_cortex", "reflective", n=12, passes=9),
            _cell("executive_muse", "executive", n=9, passes=1),
            _cell("executive_cortex", "executive", n=9, passes=8),
        )
        assert verdict["decision"] == aw.DECISION_NEGATIVE

    def test_no_difference_anywhere_is_a_negative_not_a_ceiling(self) -> None:
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=6),
            _cell("reflective_cortex", "reflective", n=12, passes=6),
            _cell("executive_muse", "executive", n=9, passes=4),
            _cell("executive_cortex", "executive", n=9, passes=4),
        )
        assert verdict["decision"] == aw.DECISION_NEGATIVE

    def test_a_big_interaction_carried_only_by_the_executive_axis_fails_p2(self) -> None:
        """ "The muse is terrible at acting" is not "the muse reflects better"."""
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=6),
            _cell("reflective_cortex", "reflective", n=12, passes=6),
            _cell("executive_muse", "executive", n=9, passes=0),
            _cell("executive_cortex", "executive", n=9, passes=9),
        )
        assert verdict["interaction"] >= aw.MIN_INTERACTION
        assert verdict["decision"] == aw.DECISION_NEGATIVE
        failed = [c["condition"] for c in verdict["conditions"] if not c["met"]]
        assert any("P2" in name for name in failed)

    def test_both_minds_at_the_same_ceiling_is_inconclusive(self) -> None:
        """An axis with no resolution left cannot contribute a difference."""
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=12),
            _cell("reflective_cortex", "reflective", n=12, passes=12),
            _cell("executive_muse", "executive", n=9, passes=0),
            _cell("executive_cortex", "executive", n=9, passes=6),
        )
        assert verdict["decision"] == aw.DECISION_INCONCLUSIVE

    def test_both_minds_at_the_same_floor_is_inconclusive(self) -> None:
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=12),
            _cell("reflective_cortex", "reflective", n=12, passes=3),
            _cell("executive_muse", "executive", n=9, passes=0),
            _cell("executive_cortex", "executive", n=9, passes=0),
        )
        assert verdict["decision"] == aw.DECISION_INCONCLUSIVE

    def test_a_cell_full_of_transport_errors_is_inconclusive(self) -> None:
        """A dead endpoint is not a measurement of a mind."""
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=12, passes=12),
            _cell("reflective_cortex", "reflective", n=12, passes=3),
            _cell("executive_muse", "executive", n=9, passes=0, errors=9),
            _cell("executive_cortex", "executive", n=9, passes=6),
        )
        assert verdict["decision"] == aw.DECISION_INCONCLUSIVE

    def test_an_empty_cell_is_inconclusive(self) -> None:
        verdict = aw.decide(
            aw.Cell("reflective_muse", "reflective", "m", []),
            _cell("reflective_cortex", "reflective", n=12, passes=3),
            _cell("executive_muse", "executive", n=9, passes=0),
            _cell("executive_cortex", "executive", n=9, passes=6),
        )
        assert verdict["decision"] == aw.DECISION_INCONCLUSIVE

    def test_an_interaction_just_under_the_threshold_is_a_negative(self) -> None:
        """The boundary is pinned so a later nudge has to face this test."""
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=10, passes=8),
            _cell("reflective_cortex", "reflective", n=10, passes=5),
            _cell("executive_muse", "executive", n=10, passes=4),
            _cell("executive_cortex", "executive", n=10, passes=5),
        )
        assert verdict["interaction"] == pytest.approx(0.40)
        assert verdict["decision"] == aw.DECISION_SUPPORTS

        near = aw.decide(
            _cell("reflective_muse", "reflective", n=10, passes=8),
            _cell("reflective_cortex", "reflective", n=10, passes=5),
            _cell("executive_muse", "executive", n=10, passes=5),
            _cell("executive_cortex", "executive", n=10, passes=5),
        )
        assert near["interaction"] == pytest.approx(0.30)
        assert near["decision"] == aw.DECISION_NEGATIVE

    def test_every_condition_is_reported_with_its_detail(self) -> None:
        verdict = aw.decide(
            _cell("reflective_muse", "reflective", n=4, passes=4),
            _cell("reflective_cortex", "reflective", n=4, passes=1),
            _cell("executive_muse", "executive", n=3, passes=0),
            _cell("executive_cortex", "executive", n=3, passes=2),
        )
        names = [condition["condition"] for condition in verdict["conditions"]]
        assert len(names) == 6, "four validity conditions and two promotion conditions"
        assert all(condition["detail"] for condition in verdict["conditions"])
        assert {c["kind"] for c in verdict["conditions"]} == {"validity", "promotion"}


# ── 2. the harnesses actually drive the loop ─────────────────────────────────


class TestExecutiveHarnessesDrive:
    """Each ``run_once`` drives the real loop against a scripted seam.

    Before this, ``main`` raised ``TypeError`` on its first line of work and the
    committed tests could not tell — they exercised ``truth`` and ``grade``, both
    of which were fine. A grader that works inside a harness that cannot run is
    not a measurement instrument.
    """

    def test_subset_harness_grades_a_correct_answer(self) -> None:
        graded = challenge_subset.run_once(_scripted(76))
        assert graded["is_correct"]
        assert graded["exit_reason"] == "finished"
        assert graded["tool_calls"] == ["finish"]

    def test_subset_harness_rejects_the_planted_trap(self) -> None:
        graded = challenge_subset.run_once(_scripted(72))
        assert not graded["is_correct"]
        assert graded["is_trap"]

    def test_subset_harness_records_no_answer_without_inventing_one(self) -> None:
        def prose(_messages: list[dict[str, Any]]) -> ModelResponse:
            return ModelResponse(content="I would rather not say a number.")

        graded = challenge_subset.run_once(prose, max_steps=2)
        assert graded["verdict"] == "NO ANSWER"
        assert not graded["is_correct"]
        assert graded["lenient_answer"] is None

    def test_register_harness_grades_the_unique_solution(self) -> None:
        graded = challenge_register.run_once(_scripted("initial=0101 order=CBEAD"))
        assert graded["is_correct"]

    def test_register_harness_rejects_a_wrong_order(self) -> None:
        graded = challenge_register.run_once(_scripted("initial=0101 order=ABCDE"))
        assert not graded["is_correct"]

    def test_entropic_3b_harness_grades_the_unique_solution(self) -> None:
        graded = challenge_entropic.run_once(
            _scripted("initial=00001111 order C->A->E->D->B"), variant="3b"
        )
        assert graded["is_correct"]
        assert graded["variant"] == "3b"

    def test_entropic_3_harness_grades_under_determination(self) -> None:
        graded = challenge_entropic.run_once(
            _scripted("The problem is under-determined: 17 solutions."), variant="3"
        )
        assert graded["is_correct"]

    def test_each_attempt_gets_a_fresh_bench(self) -> None:
        """A shared bench let run 2's tool log carry run 1's calls."""
        first = challenge_subset.run_once(_scripted(76))
        second = challenge_subset.run_once(_scripted(76))
        assert first["tool_calls"] == second["tool_calls"] == ["finish"]

    def test_every_harness_records_its_degradations(self) -> None:
        """C3: a run that degraded says so, in the same shape everywhere."""
        for graded in (
            challenge_subset.run_once(_scripted(76)),
            challenge_register.run_once(_scripted("initial=0101 order=CBEAD")),
            challenge_entropic.run_once(_scripted("x"), variant="3b"),
        ):
            assert "degradations" in graded
            assert isinstance(graded["degradations"], list)


class TestExecutiveRunsFoldTransportFailures:
    """A raising seam becomes a recorded, ungraded run — never a lost series."""

    def test_a_raising_seam_is_recorded_not_propagated(self) -> None:
        def dead(_messages: list[dict[str, Any]]) -> ModelResponse:
            raise OSError("connection refused")

        records: list[dict[str, Any]] = []
        runs = aw.executive_runs(
            lambda _problem: dead,
            model="m",
            repeats=1,
            sink=records.append,
            problems=("subset",),
        )
        assert len(runs) == 1
        assert runs[0]["transport_error"] is True
        assert runs[0]["passed"] is False
        assert "error" in runs[0]
        assert records == runs, "every run reaches the sink as it happens"

    def test_a_graded_run_is_not_marked_a_transport_error(self) -> None:
        runs = aw.executive_runs(
            lambda _problem: _scripted(76),
            model="m",
            repeats=1,
            sink=lambda _record: None,
            problems=("subset",),
        )
        assert runs[0]["transport_error"] is False
        assert runs[0]["passed"] is True


class TestReflectiveRunsGradeWithTheCommittedGrader:
    """The reflective half grades through ``muse_challenge`` and nothing else."""

    def test_a_scripted_challenge_passes_and_carries_its_raw_response(self) -> None:
        from examples.muse_challenge import scripted_muse

        records: list[dict[str, Any]] = []
        runs = aw.reflective_runs(
            scripted_muse(mode="challenge"), model="m", repeats=1, sink=records.append
        )
        assert len(runs) == 3, "three committed cases"
        assert all(run["passed"] for run in runs)
        assert all(run["response"] for run in runs), "the raw text is kept for regrading"
        assert records == runs

    def test_a_scripted_restatement_fails(self) -> None:
        from examples.muse_challenge import scripted_muse

        runs = aw.reflective_runs(
            scripted_muse(mode="restatement"), model="m", repeats=1, sink=lambda _r: None
        )
        assert not any(run["passed"] for run in runs)


class TestConfigPreambleIsWrittenBeforeResults:
    """The record that makes the run reproducible, and it is written first."""

    def test_the_preamble_carries_the_rule_and_both_temperatures(self, tmp_path: Path) -> None:
        args = aw.build_parser().parse_args(
            ["--results", str(tmp_path / "c.json"), "--n-reflective", "4", "--n-executive", "3"]
        )
        config = aw.config_record(args)
        written = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
        assert written == config
        assert config["min_interaction"] == aw.MIN_INTERACTION
        assert config["min_reflective_gap"] == aw.MIN_REFLECTIVE_GAP
        assert config["cortex_temperature"] == aw.EXECUTIVE_TEMPERATURE
        assert config["muse_temperature"] == aw.REFLECTIVE_TEMPERATURE
        assert config["n_reflective_per_cell"] == 12
        assert config["n_executive_per_cell"] == 9
        assert "SUPPORTS-PROMOTION" in config["decision_rule"]
        assert config["reflective_grader"].startswith("examples/muse_challenge.py")

    def test_there_is_no_scripted_mode_to_mistake_for_a_measurement(self) -> None:
        assert aw.main([]) == 1


# ── the live lane is opt-in, and every live class says so in its name ────────


def test_every_skip_gated_class_in_this_module_is_live_prefixed() -> None:
    """A live class not named ``TestLive*`` keeps the network bomb and lies."""
    import sys

    for name, value in vars(sys.modules[__name__]).items():
        if isinstance(value, type) and getattr(value, "pytestmark", None):
            assert name.startswith("TestLive"), f"{name} is skip-gated but not TestLive-prefixed"


LIVE_ENABLED = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"
LIVE_KEY = os.environ.get(aw.API_KEY_ENV, "")


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason=f"{aw.API_KEY_ENV} is not set")
class TestLiveAssociationWork:
    """One live attempt per axis, against the real rig.

    Asserts what the harness controls — that a boundary reaches a real endpoint,
    that the attempt terminates, and that whatever comes back is graded by the
    committed grader — and **not** that either mind passes. Whether they do is a
    measurement, recorded in ``docs/live-test-results/association-work.md``;
    pinning it here would turn a finding into a flaky assertion about a mood.
    """

    def test_the_reflective_axis_reaches_a_real_endpoint(self) -> None:
        base_url = os.environ.get("EMBODIMENT_BASE_URL", aw.DEFAULT_BASE_URL)
        complete = aw.muse_gateway(
            base_url,
            aw.MUSE_MODEL,
            LIVE_KEY,
            temperature=aw.REFLECTIVE_TEMPERATURE,
            max_tokens=aw.MAX_TOKENS,
        )
        runs = aw.reflective_runs(complete, model=aw.MUSE_MODEL, repeats=1, sink=lambda _r: None)
        assert len(runs) == 3
        assert all("verdict" in run["grade"] for run in runs)

    def test_the_executive_axis_reaches_a_real_endpoint(self) -> None:
        base_url = os.environ.get("EMBODIMENT_BASE_URL", aw.DEFAULT_BASE_URL)
        runs = aw.executive_runs(
            lambda problem: aw.executive_gateway_for(problem, base_url, aw.CORTEX_MODEL, LIVE_KEY),
            model=aw.CORTEX_MODEL,
            repeats=1,
            sink=lambda _r: None,
            problems=("subset",),
        )
        assert len(runs) == 1
        assert "verdict" in runs[0]
