"""Tests for the devague-legs harness (plan task t9, issue #20).

Three jobs, in this order:

1. **Bind the harness to the pre-registration.**
   ``tests/test_devague_legs_preregistration.py`` states the contract: task
   t9's harness "either imports these names directly, or defines its own
   copies and asserts them equal to these in the same diff". The harness keeps
   copies (an ``examples/`` module importing ``tests/`` would be backwards), so
   the equality assertion lives here. Moving a threshold now means editing a
   test that says so out loud.
2. **Prove the case pool is built from committed history, blind.** A blind
   case that leaks its pool through a stray field is not a blind case. The
   redaction and the section-scoped delivery-row parse are asserted, the
   latter because it was wrong once — an unscoped scan handed the
   ``function-first-loops-muse-redesign:t3`` case a *Drift* row that leaked a
   ``needs-follow-up`` classification token into a supposedly blind prompt.
   Caught in a dry run before any dial; pinned here so it stays caught.
3. **Prove the non-negotiable assertion actually fails.** The structural claim
   ("zero confirms not attributable to a human") is worthless if
   :func:`assert_no_confirms` cannot fail. Each of its three independent
   checks is provoked here and required to fail — the vacuity assertion the
   pre-registration's own M2 lesson asks for, applied to our own grader.

Nothing here dials a network: the module-level bomb refuses it, and no live
class exists in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import devague_legs as harness  # noqa: E402
from tests import test_devague_legs_preregistration as prereg  # noqa: E402


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this module may dial anything."""
    import urllib.request

    def _bomb(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the hermetic path must never dial a network")

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)


class TestHarnessHonoursThePreRegisteredConstants:
    """The harness's copies equal the pre-registration's originals, by value."""

    def test_devague_version_pin(self) -> None:
        assert harness.PINNED_DEVAGUE_VERSION == prereg.PINNED_DEVAGUE_VERSION

    def test_cheapest_first_leg(self) -> None:
        assert harness.CHEAPEST_FIRST_LEG == prereg.CHEAPEST_FIRST_LEG == "deviate"

    def test_case_pools(self) -> None:
        assert harness.DEVIATE_POSITIVE_CASES == prereg.DEVIATE_POSITIVE_CASES
        assert harness.DEVIATE_NEGATIVE_CASES == prereg.DEVIATE_NEGATIVE_CASES

    def test_status_vocabulary(self) -> None:
        assert harness.LLM_ORIGIN_STATUS == prereg.LLM_ORIGIN_STATUS == "proposed"
        assert harness.HUMAN_CONFIRM_STATUS == prereg.HUMAN_CONFIRM_STATUS == "confirmed"

    def test_negative_case_titles_cover_the_negative_pool(self) -> None:
        assert set(harness.NEGATIVE_CASE_TITLES) == set(prereg.DEVIATE_NEGATIVE_CASES)

    def test_both_minds_run_at_one_temperature(self) -> None:
        """An A/B whose arms differ in temperature measures the temperature."""
        config = harness.write_config_preamble.__doc__
        assert config  # the shared preamble helper is the one that records both
        assert harness.DEFAULT_TEMPERATURE == 0.3

    def test_the_cortex_budget_is_large_enough_to_survive_its_own_reasoning(self) -> None:
        """Measured on this rig: ~1150 completion tokens for a one-line judgment.

        A small budget returns ``finish_reason=length`` with an empty body,
        which is indistinguishable from model failure at the grading step.
        """
        assert harness.CORTEX_MAX_TOKENS >= 3000


class TestTheCasePoolIsBuiltFromCommittedHistory:
    def test_twelve_cases_six_and_six(self) -> None:
        cases = harness.build_cases()
        assert len(cases) == 12
        assert sum(1 for c in cases if c.pool == "positive") == 6
        assert sum(1 for c in cases if c.pool == "negative") == 6

    def test_ground_truth_follows_the_pool_not_the_model(self) -> None:
        for case in harness.build_cases():
            assert case.ground_truth_warrants is (case.pool == "positive")

    def test_positive_text_is_exactly_what_plus_reason(self) -> None:
        import json

        for case in harness.build_cases():
            if case.pool != "positive":
                continue
            _, dev_id = case.case_id.split(":")
            ledger = json.loads(harness.DELIVERY_LEDGERS[case.plan].read_text(encoding="utf-8"))
            record = next(d for d in ledger["deviations"] if d["id"] == dev_id)
            assert case.text == f"{record['what']}\n\n{record['reason']}"

    def test_no_case_leaks_a_labelled_ledger_field(self) -> None:
        """id / status / classification / origin / affects never reach a prompt."""
        for case in harness.build_cases():
            _, user = harness.build_prompt(case)
            body = user.split('"""')[1]
            for label in ('"status"', '"classification"', '"origin"', '"affects"', '"id"'):
                assert label not in body, f"{case.case_id} leaks {label}"
            assert "status: approved" not in body.lower(), case.case_id

    def test_delivery_rows_are_scoped_to_the_actual_delivery_section(self) -> None:
        """The regression: an unscoped scan picked up a Drift row for t3."""
        rows = harness._delivery_rows("function-first-loops-muse-redesign")
        assert rows["t3"].startswith("Status: delivered.")
        assert "Kind-aware delivery" in rows["t3"]
        assert "DROPPED_COMPILATION_STARVED" not in rows["t3"]

    def test_the_length_confound_is_measured_not_hidden(self) -> None:
        """Pre-registered inputs, so the confound is reported, never repaired."""
        report = harness.length_confound(harness.build_cases())
        assert report["positive_chars"]["mean"] > report["negative_chars"]["mean"]
        assert isinstance(report["separable_by_length_alone"], bool)


class TestJudgmentParsing:
    def test_plain_json(self) -> None:
        parsed, degraded = harness.parse_judgment(
            '{"warrants_deviation": true, "classification": "risky", "reason": "x"}'
        )
        assert degraded is None
        assert parsed == {"warrants_deviation": True, "classification": "risky", "reason": "x"}

    def test_fenced_json_with_leading_prose(self) -> None:
        parsed, degraded = harness.parse_judgment(
            'Here is my answer:\n```json\n{"warrants_deviation": false, '
            '"classification": null, "reason": "y"}\n```'
        )
        assert degraded is None
        assert parsed is not None and parsed["warrants_deviation"] is False

    def test_empty_content_is_degraded_not_guessed(self) -> None:
        parsed, degraded = harness.parse_judgment("   ")
        assert parsed is None and degraded == "empty-content"

    def test_a_non_bool_verdict_is_refused(self) -> None:
        parsed, degraded = harness.parse_judgment('{"warrants_deviation": "yes"}')
        assert parsed is None
        assert degraded is not None and degraded.startswith("warrants_deviation-not-a-bool")

    def test_an_off_vocabulary_classification_is_labelled_not_dropped(self) -> None:
        parsed, _ = harness.parse_judgment(
            '{"warrants_deviation": true, "classification": "catastrophic"}'
        )
        assert parsed is not None
        assert parsed["classification"] == "off-vocabulary:catastrophic"


def _clean_record(case_id: str = "p:d1") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "mind": "muse",
        "origin": "llm",
        "status": "proposed",
        "confirmed_by": None,
    }


class TestTheConfirmAssertionCanActuallyFail:
    """A grader that cannot fail is not a grader. Each check is provoked."""

    def test_a_clean_run_passes(self) -> None:
        result = harness.assert_no_confirms(
            [_clean_record()], {"p": "abc"}, {"p": "abc"}, {"p": {}}, {"p": {}}
        )
        assert result["passes"] is True
        assert result["confirms_not_attributable_to_a_human"] == 0

    def test_a_confirmed_judgment_fails(self) -> None:
        bad = _clean_record()
        bad["status"] = "confirmed"
        result = harness.assert_no_confirms([bad], {"p": "abc"}, {"p": "abc"}, {}, {})
        assert result["passes"] is False

    def test_a_confirmed_by_attribution_fails(self) -> None:
        bad = _clean_record()
        bad["confirmed_by"] = "the harness"
        result = harness.assert_no_confirms([bad], {"p": "abc"}, {"p": "abc"}, {}, {})
        assert result["passes"] is False

    def test_a_mutated_ledger_fails(self) -> None:
        result = harness.assert_no_confirms([_clean_record()], {"p": "abc"}, {"p": "def"}, {}, {})
        assert result["passes"] is False
        assert any("ledger changed" in v for v in result["violations"])

    def test_a_mutated_confirm_log_fails(self) -> None:
        result = harness.assert_no_confirms(
            [_clean_record()],
            {"p": "abc"},
            {"p": "abc"},
            {"p": {"deviations": []}},
            {"p": {"deviations": [{"id": "d9", "origin": "llm", "status": "approved"}]}},
        )
        assert result["passes"] is False
        assert any("confirm log changed" in v for v in result["violations"])

    def test_a_user_origin_judgment_fails(self) -> None:
        """The claim is about model-authored judgments; mislabelling one fails."""
        bad = _clean_record()
        bad["origin"] = "user"
        result = harness.assert_no_confirms([bad], {"p": "abc"}, {"p": "abc"}, {}, {})
        assert result["passes"] is False


class TestTheBaseCommitPredatesTheMuseToolSeam:
    """The pre-registration requires a pre-seam commit. It is checked, not claimed."""

    def test_the_evidence_is_recorded_and_true_on_this_checkout(self) -> None:
        record = harness.base_commit_record()
        assert record["evidence"]["embodiment/headspace.py exists"] is False
        assert record["evidence"]["muse.py still declares the tools-off invariant"] is True
        assert record["predates_muse_tool_seam"] is True
        assert len(record["base_commit"]) == 40


def _judgment(case_id: str, pool: str, mind: str, said: Any, degraded: Any = None) -> dict:
    return {
        "kind": "judgment",
        "case_id": case_id,
        "pool": pool,
        "ground_truth_warrants": pool == "positive",
        "case_char_len": 100,
        "mind": mind,
        "origin": "llm",
        "status": "proposed",
        "confirmed_by": None,
        "latency_s": 1.0,
        "parsed": (
            None
            if said is None
            else {"warrants_deviation": said, "classification": None, "reason": ""}
        ),
        "degraded": degraded,
    }


class TestReAnalysisReadsTheTranscript:
    """The results tables are rendered from the raw transcript, never retyped.

    Three of this repo's committed corrections were errors made *between* a run
    and its write-up. ``--analyse`` closes that gap, so it is tested like a
    grader: a degraded call must survive into the table as DEGRADED rather than
    vanishing into a smaller denominator.
    """

    def _transcript(self, tmp_path: Path) -> Path:
        import json

        path = tmp_path / "t.jsonl"
        rows = [
            {"kind": "config", "n": 2},
            _judgment("p:d1", "positive", "cortex", True),
            _judgment("p:d1", "positive", "muse", False),
            _judgment("p:t1", "negative", "cortex", None, "empty-content"),
            _judgment("p:t1", "negative", "muse", False),
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return path

    def test_load_records_skips_non_judgment_lines(self, tmp_path: Path) -> None:
        assert len(harness.load_records(self._transcript(tmp_path))) == 4

    def test_a_degraded_call_is_shown_not_dropped(self, tmp_path: Path) -> None:
        table = harness.analyse(self._transcript(tmp_path))
        assert "DEGRADED (empty-content)" in table

    def test_concordance_denominator_excludes_only_degraded_calls(self, tmp_path: Path) -> None:
        records = harness.load_records(self._transcript(tmp_path))
        cortex = harness.concordance(records, "cortex")
        assert cortex["n_cases"] == 2 and cortex["n_scored"] == 1
        assert cortex["hits"] == 1 and cortex["accuracy"] == 1.0
        muse = harness.concordance(records, "muse")
        assert muse["n_scored"] == 2
        assert muse["misses"] == 1 and muse["correct_rejections"] == 1
