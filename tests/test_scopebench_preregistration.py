"""The ScopeBench pre-registration is committed, and the harness obeys it (t9).

``t9`` acceptance criterion 3: *the preregistration doc and generated seeds are
committed before any live result, and the verdict rule names all seven
conditions including the non-intervention and token-cost guards.*

The prose argument lives in
``docs/live-test-results/scopebench-preregistration.md``. This file is the
**pin**, following ``tests/test_muse_latency_preregistration.py`` and
``tests/test_devague_legs_preregistration.py`` exactly: every threshold the
measured run must honour is asserted here as a literal against the harness
constant, and asserted to appear in the document. Changing a threshold after the
first dial therefore means editing a test that says so out loud in a diff a
reviewer sees, which is the whole discipline.

Nothing here dials a rig, reads network state, or runs a live series.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from examples.scope import episodes as ep
from examples.scope import oracle as orc
from examples.scope import scopebench as sb
from examples.scope import subordinate as sub

DOC_PATH = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "live-test-results"
    / "scopebench-preregistration.md"
)


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


# ── the artifacts exist, before any result ────────────────────────────────────


class TestTheArtifactsAreCommitted:
    def test_the_pre_registration_is_committed(self) -> None:
        assert DOC_PATH.exists()
        assert DOC_PATH.stat().st_size > 4000

    def test_the_seed_table_is_committed(self) -> None:
        assert ep.SEEDS_PATH.exists()
        json.loads(ep.SEEDS_PATH.read_text(encoding="utf-8"))

    def test_the_oracle_fixture_is_committed(self) -> None:
        assert orc.FIXTURES_PATH.exists()
        json.loads(orc.FIXTURES_PATH.read_text(encoding="utf-8"))

    def test_the_result_exists_and_cites_the_pre_registration(self) -> None:
        """**This is the deliberate update ``t9`` asked for.**

        The original assertion was ``not results.exists()`` — the ordering claim
        checked rather than asserted, with a docstring saying that when ``t11``
        publishes, this test is the one that has to be changed on purpose. ``t11``
        has published, so the pin is inverted rather than deleted: the result now
        has to exist *and* name the document that governed it, which keeps the
        ordering auditable instead of merely historical.
        """
        results = DOC_PATH.parent / "scopebench.md"
        assert results.exists(), "t11 published; the results doc must be committed"
        assert DOC_PATH.name in results.read_text(encoding="utf-8")

    def test_the_raw_records_live_where_the_result_says_they_do(self) -> None:
        """Amendment 4: a directory, not the single ``scopebench.jsonl`` §1 named.

        Recorded in the pre-registration's amendments rather than left as a
        broken reference — one file per (arm, stage) is what an arm-at-a-time
        run produces, and a run that dies partway then keeps every episode it
        completed.
        """
        raw = DOC_PATH.parent / "scopebench-raw"
        assert raw.is_dir()
        assert sorted(path.name for path in raw.glob("*-stage1.jsonl"))

    def test_the_document_still_states_it_preceded_the_first_dial(self, doc: str) -> None:
        """The claim the inverted pin above protects. It must never be edited away."""
        assert "committed with the harness and before the first measured" in doc

    def test_the_document_says_it_precedes_the_first_dial(self, doc: str) -> None:
        assert "committed with the harness and before the first measured" in doc
        assert "Nothing in this document is a live result" in doc


# ── the seven conditions, named ───────────────────────────────────────────────


class TestTheVerdictRuleNamesAllSeven:
    def test_the_harness_declares_exactly_seven(self) -> None:
        assert len(sb.VERDICT_CONDITIONS) == 7

    @pytest.mark.parametrize("condition", sb.VERDICT_CONDITIONS)
    def test_every_condition_appears_in_the_document(self, condition: str, doc: str) -> None:
        assert sb.CONDITION_WHY[condition].split(",")[0][:40] in doc

    def test_the_non_intervention_guard_is_named(self, doc: str) -> None:
        assert "non-intervention" in doc
        assert sb.NON_INTERVENTION_FAMILY in doc

    def test_the_token_cost_guard_is_named(self, doc: str) -> None:
        assert "extra tokens" in doc
        assert "easier\nprotocol" in doc or "easier protocol" in doc

    def test_the_three_verdicts_are_all_documented(self, doc: str) -> None:
        for word in (sb.VERDICT_ACCEPT, sb.VERDICT_REJECT, sb.VERDICT_INCONCLUSIVE):
            assert word in doc


# ── the thresholds are pinned, both ways ──────────────────────────────────────

#: The pre-registered values. Literals on the left, harness on the right — the
#: point of the file. A change to either side without the other fails here.
PINNED = {
    "MIN_FAMILIES": 2,
    "MIN_WIN_MARGIN": 2,
    "PROTOCOL_FLOOR": 0.8,
    "AUTHORITY_CEILING": 0,
    "NON_INTERVENTION_TOLERANCE": 0.02,
    "OPERATIONAL_TOLERANCE": 0.0,
    "MIN_STRATEGIST_MARGIN": 1,
}


class TestThresholdsArePinned:
    @pytest.mark.parametrize("name", sorted(PINNED))
    def test_the_harness_carries_the_pre_registered_value(self, name: str) -> None:
        assert getattr(sb, name) == PINNED[name]

    @pytest.mark.parametrize("name", sorted(PINNED))
    def test_the_document_carries_the_same_value(self, name: str, doc: str) -> None:
        assert f"`{name}`" in doc
        assert f"| {PINNED[name]} |" in doc

    def test_the_episode_floor_is_pinned_on_both_sides(self, doc: str) -> None:
        assert ep.MIN_EPISODES_PER_FAMILY == 6
        assert "`MIN_EPISODES_PER_FAMILY`" in doc
        assert ep.load_seeds().episodes_per_family >= ep.MIN_EPISODES_PER_FAMILY


# ── the design the document describes is the design that shipped ──────────────


class TestTheDocumentMatchesTheHarness:
    @pytest.mark.parametrize("family", ep.FAMILY_ORDER)
    def test_every_declared_family_is_in_the_document(self, family: str, doc: str) -> None:
        assert f"`{family}`" in doc

    @pytest.mark.parametrize("family", sorted(ep.DEFERRED))
    def test_every_deferred_family_is_marked_deferred(self, family: str, doc: str) -> None:
        row = next(line for line in doc.splitlines() if line.startswith(f"| `{family}`"))
        assert "DEFERRED" in row

    @pytest.mark.parametrize("arm", sb.ARM_ORDER)
    def test_every_arm_and_its_seats_are_in_the_document(self, arm: str, doc: str) -> None:
        row = next(line for line in doc.splitlines() if line.startswith(f"| `{arm}` |"))
        for seat in sb.SEATS:
            value = sb.ARMS[arm].seats[seat]
            assert (f"`{value}`" in row) if value else ("*(none)*" in row)

    @pytest.mark.parametrize("stage", sb.STAGES)
    def test_both_stages_are_described(self, stage: str, doc: str) -> None:
        assert stage.replace("-", " ").title().replace(" ", " ") in doc or stage in doc

    @pytest.mark.parametrize("axis", sorted(sb.AXES))
    def test_every_axis_is_described(self, axis: str, doc: str) -> None:
        assert f"**{axis}**" in doc

    def test_the_stage_one_stand_in_is_declared_in_the_document(self, doc: str) -> None:
        assert f"`{sub.BASELINE_PLANNER}` planner" in doc
        assert sb.STAGE_ONE_STANDIN[sb.ARM_A0] == sub.BASELINE_PLANNER

    def test_the_clairvoyance_limitation_is_recorded(self, doc: str) -> None:
        """The one caveat a reader could otherwise be misled by."""
        assert "clairvoyant" in doc
        assert "upper bound" in doc

    def test_what_the_design_cannot_answer_is_its_own_section(self, doc: str) -> None:
        assert "## 12. What this design cannot answer" in doc

    def test_the_publication_rule_covers_negatives(self, doc: str) -> None:
        assert "same prominence" in doc
        assert sb.VERDICT_INCONCLUSIVE in doc

    def test_the_calibration_table_is_labelled_as_not_a_result(self, doc: str) -> None:
        assert "not a result" in doc
        assert "no model was called" in doc.lower()


class TestTheCalibrationTableStillHolds:
    """The numbers printed in the document are recomputed here. They are
    deterministic and hermetic, so a drift is a real change and not noise."""

    def test_the_published_control_numbers_are_reproducible(self, doc: str) -> None:
        summary = sb.stage_one_summary(sb.run_stage_one())
        for planner in sub.PLANNER_ORDER:
            row = next(line for line in doc.splitlines() if line.startswith(f"| `{planner}` |"))
            printed = [cell.strip() for cell in row.strip("|").split("|")][1:]
            for index, family in enumerate(ep.FIRST_CYCLE):
                key = f"{sb.control_arm(planner)}|{sb.STAGE_ONE}|{family}"
                actual = summary["cells"][key]["mean_regret"]
                assert f"{actual:.1f}" == printed[index], (planner, family)

    def test_the_headroom_claim_in_the_document_is_true(self) -> None:
        """The document claims ``revising`` beats ``greedy`` on every family.
        That claim is what says the bench has room for a strategist to help."""
        summary = sb.stage_one_summary(sb.run_stage_one())
        for family in ep.FIRST_CYCLE:
            better = summary["cells"][
                f"{sb.control_arm(sub.PLANNER_REVISING)}|{sb.STAGE_ONE}|{family}"
            ]
            baseline = summary["cells"][
                f"{sb.control_arm(sub.BASELINE_PLANNER)}|{sb.STAGE_ONE}|{family}"
            ]
            assert better["mean_regret"] < baseline["mean_regret"], family
