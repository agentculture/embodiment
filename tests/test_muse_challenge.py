"""Tests for the muse-challenges-cortex golden (task t17).

**The grader is verified here before a single live number is read.** That
ordering is the whole integrity of the harness: task t12 found that a grader
which accepts its own trap answer is exactly the bug a golden exists to prevent,
so the headline test in this file is that a *restatement fails*, and it runs
with no live rig at all.

Five fixture classes are graded, all committed in ``examples/muse_challenge.py``
so anyone can inspect what the thresholds were tuned against:

* a genuine challenge — must PASS;
* a restatement of the cortex's own conclusion — must FAIL (the headline);
* a restatement with challenge vocabulary bolted on — must FAIL, and must fail
  on the near-copy gate, which is why the gates are ordered;
* fluent contrarianism aimed at nothing — must FAIL. This one earned its place:
  it *passed* on ``cache_ttl`` during development because "push back" hit an
  anchor spelled ``push``. The anchor was wrong, not the response;
* **agreement wearing challenge vocabulary** — must FAIL. This one earned its
  place the hard way: it *passed* on every case until the negation and agreement
  gates landed. "There is no risk… the assumption of stability is a safe one" is
  endorsement, and a grader that scores it as counsel is the exact bug a golden
  exists to prevent. Found by adversarial review of the committed grader, before
  the final live numbers were taken.

The live class is ``TestLive``-prefixed so the module's ``_no_network`` bomb
lets it through — a live class named anything else keeps the bomb and its
"live" assertions then pass against the fixture instead of a real endpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from embodiment.muse import COUNSEL_KIND_DURABLE, MUSE_AUTHORITY
from examples import muse_challenge as golden

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No in-process test may dial anything. Live classes opt out by name."""
    if "TestLive" in request.node.nodeid:
        return
    import urllib.request

    def _bomb(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the hermetic path must never dial a network")

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)


# ── the headline: a restatement fails the grader ──────────────────────────────


class TestARestatementFailsTheGrader:
    """The trap answer. If this ever passes, every number downstream is noise."""

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_restatement_is_graded_restated(self, case: golden.CortexResult) -> None:
        verdict = golden.grade(golden.RESTATEMENT_FIXTURES[case.id], case)
        assert verdict["passed"] is False
        assert verdict["verdict"] == golden.VERDICT_RESTATED
        assert verdict["anchors_hit"] == []

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_restatement_reads_as_the_cortex_s_own_words(self, case: golden.CortexResult) -> None:
        """Not merely 'failed' — failed for the stated reason."""
        verdict = golden.grade(golden.RESTATEMENT_FIXTURES[case.id], case)
        assert verdict["shared_bigram_fraction"] > golden.MAX_SHARED_BIGRAMS
        assert "near-copy" in verdict["reason"]

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_agreement_alone_is_never_a_challenge(self, case: golden.CortexResult) -> None:
        """A short, novel, wholly agreeable reply still makes no challenge move."""
        verdict = golden.grade("I agree. This looks right to me. Go ahead.", case)
        assert verdict["passed"] is False
        assert verdict["verdict"] == golden.VERDICT_RESTATED
        assert verdict["moves"] == []


# ── a genuine challenge passes ────────────────────────────────────────────────


class TestAGenuineChallengePassesTheGrader:
    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_hand_written_challenge_passes(self, case: golden.CortexResult) -> None:
        verdict = golden.grade(golden.CHALLENGE_FIXTURES[case.id], case)
        assert verdict["passed"] is True
        assert verdict["verdict"] == golden.VERDICT_CHALLENGED
        assert verdict["anchors_hit"]

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_it_names_the_unstated_premise(self, case: golden.CortexResult) -> None:
        """The move the brief asks for by name, not merely any of the three."""
        verdict = golden.grade(golden.CHALLENGE_FIXTURES[case.id], case)
        assert golden.MOVE_ASSUMPTION in verdict["moves"]
        assert verdict["anchors_by_move"].get(golden.MOVE_ASSUMPTION)

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_challenge_is_not_a_near_copy(self, case: golden.CortexResult) -> None:
        verdict = golden.grade(golden.CHALLENGE_FIXTURES[case.id], case)
        assert verdict["shared_bigram_fraction"] <= golden.MAX_SHARED_BIGRAMS


# ── the two ways a grader gets fooled ─────────────────────────────────────────


class TestTheGraderIsNotFooled:
    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_challenge_words_bolted_onto_a_restatement_still_fail(
        self, case: golden.CortexResult
    ) -> None:
        """It makes a 'move' by vocabulary; the near-copy gate fires first."""
        verdict = golden.grade(golden.BOLTED_ON_FIXTURES[case.id], case)
        assert verdict["passed"] is False
        assert verdict["verdict"] == golden.VERDICT_RESTATED
        assert golden.MOVE_ASSUMPTION in verdict["moves"], "the trap must look like a move"
        assert "near-copy" in verdict["reason"]

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_fluent_contrarianism_aimed_at_nothing_fails(self, case: golden.CortexResult) -> None:
        """Empty scepticism is not counsel. This caught a real anchor bug."""
        verdict = golden.grade(golden.GENERIC_FIXTURE, case)
        assert verdict["passed"] is False
        assert verdict["verdict"] == golden.VERDICT_UNTARGETED
        assert verdict["moves"], "the trap must look like a move"
        assert verdict["anchors_hit"] == []

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_list_of_the_graders_own_targets_is_not_a_challenge(
        self, case: golden.CortexResult
    ) -> None:
        """The hole an adversarial probe found AFTER the first live series.

        Concatenating every anchor term behind a contrast marker made a move,
        hit 32 anchors and scored CHALLENGED — a pass with no argument in it.
        The anchors never reach the mind (see the leak tests), so no live run
        could produce this; it is a defect in what the grader *claims to
        measure*, not an exploit anyone had. Fixed by a density backstop.
        """
        salad = " ".join(sum((list(v) for v in case.anchors().values()), []))
        verdict = golden.grade(f"However, consider: {salad}. That is my counsel.", case)
        assert verdict["passed"] is False
        assert verdict["verdict"] == golden.VERDICT_UNARGUED
        assert verdict["moves"], "the trap must look like a move"
        assert verdict["anchors_hit"], "and it must hit anchors — that is the trap"

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_genuine_challenge_is_nowhere_near_the_density_limit(
        self, case: golden.CortexResult
    ) -> None:
        """The gate must be a backstop, not a judgement about density.

        A first draft at 0.30 left the rollback fixture 12 percent of headroom,
        which would have cost real passes. Pin the margin so a later tightening
        has to face the measurement.
        """
        verdict = golden.grade(golden.CHALLENGE_FIXTURES[case.id], case)
        assert verdict["verdict"] == golden.VERDICT_CHALLENGED
        density = len(verdict["anchors_hit"]) / verdict["response_words"]
        assert density < golden.MAX_ANCHOR_DENSITY * 0.65, (
            f"{case.id} scores {density:.3f} against a {golden.MAX_ANCHOR_DENSITY} "
            "limit — too little headroom for the gate to be a backstop"
        )

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_agreement_wearing_challenge_vocabulary_fails(self, case: golden.CortexResult) -> None:
        """The attack that actually broke a version of this grader.

        Heavily re-worded so the near-copy gate cannot fire, endorsing the
        conclusion, and using challenge markers *in the negative* — "there is no
        risk", "the assumption is a safe one" — while naming an anchor in
        passing. It scored CHALLENGED until the negation and agreement gates
        landed. Found by adversarial review, before the final numbers were taken.
        """
        verdict = golden.grade(golden.AGREEING_FIXTURES[case.id], case)
        assert verdict["passed"] is False
        assert verdict["verdict"] == golden.VERDICT_RESTATED
        assert "agreement" in verdict["reason"]
        assert verdict["shared_bigram_fraction"] <= golden.MAX_SHARED_BIGRAMS
        assert verdict["anchors_hit"], "the trap must hit an anchor, or it proves nothing"

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_negated_polarity_marker_is_not_a_challenge_move(
        self, case: golden.CortexResult
    ) -> None:
        """Challenge vocabulary in the negative is agreement — for the markers
        negation actually flips."""
        assert golden.grade("There is no risk here at all.", case)["moves"] == []
        assert golden.grade("Nothing here fails under any load.", case)["moves"] == []
        assert golden.grade("No assumption here needs checking.", case)["moves"] == []
        assert golden.grade("The risk here is real and unpriced.", case)["moves"]

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_a_negated_gap_marker_is_still_a_challenge_move(
        self, case: golden.CortexResult
    ) -> None:
        """ "It hasn't considered X" is a challenge, not agreement.

        A blanket negation rule scored exactly this — from a real live response —
        as agreement. Negating "consider" does not make a sentence agree;
        negating "risk" does. Hence :data:`golden._POLARITY_SENSITIVE`.
        """
        assert golden.grade("It hasn't considered the second-order effect.", case)["moves"]
        assert golden.grade("The reasoning never states what carries it.", case)["moves"]
        assert golden.grade("This does not show what it claims to.", case)["moves"]

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_partial_agreement_still_counts_as_a_challenge(self, case: golden.CortexResult) -> None:
        """ "I agree, BUT you are assuming X" must survive the agreement gate.

        The gate would be a blunt instrument if it killed every response that
        conceded anything — and conceding then pushing back is what good counsel
        usually looks like.
        """
        anchor = case.assumption_anchors[0]
        response = (
            f"I agree the direction is right, but the argument is assuming "
            f"something it never states about {anchor}s, and that is what carries "
            "the weight here."
        )
        verdict = golden.grade(response, case)
        assert verdict["contrast_markers"], "the concession must be qualified"
        assert verdict["verdict"] == golden.VERDICT_CHALLENGED

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_silence_is_graded_silent_not_passed(self, case: golden.CortexResult) -> None:
        for empty in ("", "   \n\t "):
            verdict = golden.grade(empty, case)
            assert verdict["verdict"] == golden.VERDICT_SILENT
            assert verdict["passed"] is False

    def test_a_stem_never_matches_inside_an_unrelated_word(self) -> None:
        """``push`` must not fire on ``push back``'s neighbour, ``pushover``…

        …and more importantly ``peak`` must never fire inside ``speak``. Token
        prefix matching, not substring matching, is what keeps the anchors from
        rewarding an accident.
        """
        assert golden.phrase_present(["speaking", "plainly"], "peak") is False
        assert golden.phrase_present(["peaks", "in", "traffic"], "peak") is True
        assert golden.phrase_present(["rests", "on", "a", "premise"], "rests on") is True
        assert golden.phrase_present(["rests", "heavily", "on"], "rests on") is False


# ── the grading key is unreachable by the mind under test ─────────────────────


class TestTheGradingKeyNeverReachesTheMind:
    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_no_anchor_appears_in_the_cortex_text(self, case: golden.CortexResult) -> None:
        """An anchor is only evidence of NEW material if the cortex never wrote it."""
        assert golden.anchor_leaks(case) == []

    @pytest.mark.parametrize("framing", sorted(golden.FRAMINGS))
    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_no_anchor_reaches_the_wire(self, case: golden.CortexResult, framing: str) -> None:
        """Asserted on the real messages, not on a reconstruction of them.

        Covers the authority text and the host framing too — an anchor the muse
        can read off its own instructions is not evidence of anything, and one
        (``how long``, from the counsel-kind marker prose) was caught this way.
        """
        messages = golden.probe_messages(case, framing=framing)
        assert messages, "the loop put nothing on the wire"
        wire = golden.word_tokens("\n".join(str(m["content"]) for m in messages))
        for phrases in case.anchors().values():
            for phrase in phrases:
                assert not golden.phrase_present(wire, phrase), f"{phrase!r} leaked to the muse"

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_the_assumption_prose_is_never_rendered(self, case: golden.CortexResult) -> None:
        assert case.assumption, "every case must document its planted premise"
        assert case.assumption not in case.prompt_text()
        wire = "\n".join(str(m["content"]) for m in golden.probe_messages(case))
        assert case.assumption not in wire

    @pytest.mark.parametrize("framing", sorted(golden.FRAMINGS))
    def test_the_framing_is_byte_identical_for_every_case(self, framing: str) -> None:
        """The prompt may differ between cases ONLY by the cortex result itself."""
        systems = {
            golden.probe_messages(case, framing=framing)[0]["content"] for case in golden.CASES
        }
        assert len(systems) == 1
        assert systems.pop().startswith(MUSE_AUTHORITY), "the authority boundary comes first"

    def test_the_control_arm_is_the_charter_and_nothing_else(self) -> None:
        """``bare`` must add no task prose, or it is not a control."""
        bare = golden.probe_messages(golden.CASES[0], framing=golden.FRAMING_BARE)[0]["content"]
        assert bare == MUSE_AUTHORITY
        asked = golden.probe_messages(golden.CASES[0], framing=golden.FRAMING_TASK)[0]["content"]
        assert asked != bare
        assert golden.CHALLENGE_FRAMING in asked

    @pytest.mark.parametrize("framing", sorted(golden.FRAMINGS))
    def test_the_two_arms_differ_only_in_the_system_message(self, framing: str) -> None:
        """Same boundary, same case, same everything the muse is told about it."""
        case = golden.CASES[0]
        assert (
            golden.probe_messages(case, framing=framing)[1]
            == golden.probe_messages(case, framing=golden.FRAMING_TASK)[1]
        )

    @pytest.mark.parametrize("case", golden.CASES, ids=lambda c: c.id)
    def test_the_whole_cortex_result_reaches_the_muse_unclipped(
        self, case: golden.CortexResult
    ) -> None:
        wire = "\n".join(str(m["content"]) for m in golden.probe_messages(case))
        assert case.conclusion in wire
        assert case.reasoning[-60:] in wire


# ── the harness runs end to end with a scripted mind ──────────────────────────


class TestTheHarnessRunsEndToEnd:
    def test_a_scripted_challenger_is_graded_challenged(self) -> None:
        mind = golden.scripted_muse(mode="challenge")
        for case in golden.CASES:
            report = golden.run_case(case, mind)
            assert report.grade["verdict"] == golden.VERDICT_CHALLENGED
            assert report.exit_reason == "concluded"
            assert report.degradations == []

    def test_a_scripted_restater_is_reported_as_a_failure(self) -> None:
        """The harness must be able to produce a negative, or it proves nothing."""
        mind = golden.scripted_muse(mode="restatement")
        reports = [golden.run_case(case, mind) for case in golden.CASES]
        assert all(r.grade["verdict"] == golden.VERDICT_RESTATED for r in reports)
        summary = golden.summarise(reports)
        assert summary["challenged"] == 0
        assert summary["verdicts"][golden.VERDICT_RESTATED] == len(golden.CASES)

    def test_the_counsel_is_durable_kind(self) -> None:
        """A challenge outlives the step, so it is durable counsel (task t2)."""
        report = golden.run_case(golden.CASES[0], golden.scripted_muse())
        assert report.kinds
        assert set(report.kinds) == {COUNSEL_KIND_DURABLE}
        assert report.durable == len(report.kinds)

    def test_the_scripted_mind_is_a_function_of_its_prompt(self) -> None:
        """Handed no case, it says nothing — so an unfed boundary fails loudly."""
        response = golden.scripted_muse()([{"role": "user", "content": "unrelated"}])
        assert response.content.strip() == "[done]"

    def test_a_dead_seam_degrades_and_is_recorded_never_raised(self) -> None:
        def broken(_messages: list[dict[str, Any]]) -> Any:
            raise ConnectionRefusedError("simulated dead muse endpoint")

        report = golden.run_case(golden.CASES[0], broken)
        assert report.exit_reason == "degraded"
        assert report.degradations, "a degradation must be visible to the host (C3)"
        assert report.grade["verdict"] == golden.VERDICT_SILENT
        assert report.grade["passed"] is False


# ── the config preamble is written before the first result line ───────────────


class TestConfigPreambleComesFirst:
    def test_the_preamble_exists_before_the_mind_is_ever_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        results = tmp_path / "config.json"
        seen: list[bool] = []
        real = golden.scripted_muse(mode="challenge")

        def probing(*, mode: str = "challenge") -> Any:
            def complete(messages: list[dict[str, Any]]) -> Any:
                seen.append(results.exists())
                return real(messages)

            return complete

        monkeypatch.setattr(golden, "scripted_muse", probing)
        assert golden.main(["--results", str(results), "--json"]) == 0
        capsys.readouterr()
        assert seen, "the mind was never called, so the ordering was never observed"
        assert all(seen), "the mind ran before the configuration was recorded"

    def test_the_preamble_records_the_run_s_settings(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        results = tmp_path / "config.json"
        golden.main(["--results", str(results), "--json", "--max-turns", "2", "--n", "2"])
        capsys.readouterr()
        config = json.loads(results.read_text(encoding="utf-8"))
        assert config["max_turns"] == 2
        assert config["n"] == 2 * len(golden.CASES)
        assert config["muse_model"] == "scripted:challenge"
        assert config["muse_temperature"] is None

    def test_the_preamble_records_the_arm_and_the_criterion(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The arm is the experimental condition — an unrecorded one is a hidden
        variable, which is the mistake the first live series made with
        temperature."""
        results = tmp_path / "config.json"
        golden.main(["--results", str(results), "--json", "--framing", golden.FRAMING_BARE])
        capsys.readouterr()
        config = json.loads(results.read_text(encoding="utf-8"))
        assert config["framing"] == golden.FRAMING_BARE
        assert config["criterion"] == golden.CRITERION
        assert config["max_shared_bigrams"] == golden.MAX_SHARED_BIGRAMS
        assert config["cases"] == sorted(golden.CASES_BY_ID)

    def test_a_live_run_without_a_key_is_an_environment_error(self, tmp_path: Path) -> None:
        """Without a key the live path is an environment error, not a half dial."""
        results = tmp_path / "config.json"
        with pytest.MonkeyPatch.context() as patch:
            patch.delenv(golden.API_KEY_ENV, raising=False)
            assert golden.main(["--live", "--results", str(results)]) == 2
        assert not results.exists(), "a refused run must not leave a config behind"


# ── output discipline ─────────────────────────────────────────────────────────


class TestOutputDiscipline:
    def test_json_mode_emits_exactly_one_object_carrying_the_criterion(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        golden.main(["--results", str(tmp_path / "c.json"), "--json", "--case", "ab_test"])
        report = json.loads(capsys.readouterr().out)
        assert set(report) == {"config", "criterion", "runs", "summary"}
        assert report["criterion"] == golden.CRITERION
        assert [run["case"] for run in report["runs"]] == ["ab_test"]
        assert report["summary"]["framings"] == [golden.FRAMING_TASK]
        assert report["summary"]["counsel_kinds"] == {COUNSEL_KIND_DURABLE: 1}

    def test_text_mode_states_the_criterion_rather_than_leaving_it_implied(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        golden.main(["--results", str(tmp_path / "c.json")])
        out = capsys.readouterr().out
        assert golden.CRITERION in out
        assert golden.VERDICT_CHALLENGED in out

    def test_the_key_is_read_from_the_environment_and_never_printed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv(golden.API_KEY_ENV, "sk-not-a-real-key")
        golden.main(["--results", str(tmp_path / "c.json"), "--json"])
        captured = capsys.readouterr()
        assert "sk-not-a-real-key" not in captured.out + captured.err
        source = (REPO_ROOT / "examples" / "muse_challenge.py").read_text(encoding="utf-8")
        assert golden.API_KEY_ENV in source
        assert "sk-" not in source

    def test_the_key_alone_never_switches_the_harness_live(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """With ``urlopen`` bombed, a default run still completes in-process."""
        monkeypatch.setenv(golden.API_KEY_ENV, "sk-not-a-real-key")
        assert golden.main(["--results", str(tmp_path / "c.json"), "--json"]) == 0
        assert golden.build_parser().parse_args([]).live is False


# ── the live lane is opt-in, and every live class says so in its name ─────────


def test_every_skip_gated_class_in_this_module_is_live_prefixed() -> None:
    """A live class not named ``TestLive*`` keeps the network bomb and lies."""
    import sys

    for name, value in vars(sys.modules[__name__]).items():
        if isinstance(value, type) and getattr(value, "pytestmark", None):
            assert name.startswith("TestLive"), f"{name} is skip-gated but not TestLive-prefixed"


LIVE_ENABLED = os.environ.get("EMBODIMENT_LIVE_RIG") == "1"
LIVE_KEY = os.environ.get(golden.API_KEY_ENV, "")


def _gateway_answers(base_url: str) -> bool:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(  # nosec B310
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {LIVE_KEY}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


@pytest.mark.skipif(not LIVE_ENABLED, reason="set EMBODIMENT_LIVE_RIG=1 to test the real rig")
@pytest.mark.skipif(not LIVE_KEY, reason=f"{golden.API_KEY_ENV} is not set")
class TestLiveMuseChallenge:
    """The golden against the real muse (Gemma 4 31B) through the lobes gateway.

    Deliberately asserts what this harness controls — that the boundary reaches
    a real endpoint, that the session terminates, and that whatever comes back
    is graded by the same verified grader — and **not** that the muse passes.
    Whether it does is a measurement, recorded in
    ``docs/live-test-results/muse-challenge.md``; pinning it here would turn a
    finding into a flaky assertion about a model's mood.
    """

    @staticmethod
    def _seam(temperature: float = golden.DEFAULT_TEMPERATURE) -> Any:
        base_url = os.environ.get("EMBODIMENT_BASE_URL", golden.DEFAULT_BASE_URL)
        if not _gateway_answers(base_url):
            pytest.skip(f"no lobes gateway answering at {base_url}")
        return golden.gateway(
            base_url,
            os.environ.get("EMBODIMENT_MUSE_MODEL", golden.DEFAULT_MUSE),
            LIVE_KEY,
            temperature=temperature,
        )

    def test_the_live_muse_answers_this_boundary_at_all(self) -> None:
        report = golden.run_case(golden.CASES[0], self._seam())
        # It really dialled: a real seam reports usage; the bomb would have
        # raised and the scripted mind reports no tokens.
        assert report.tokens, "no token usage came back — this did not reach a model"
        assert report.exit_reason in {"concluded", "quiet", "budget"}
        assert report.degradations == []
        assert report.grade["verdict"] in golden.VERDICTS

    def test_the_live_verdict_is_produced_by_the_verified_grader(self) -> None:
        report = golden.run_case(golden.CASES[-1], self._seam())
        regraded = golden.grade(report.response, golden.CASES[-1])
        assert regraded == report.grade
