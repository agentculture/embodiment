"""The three-arm harness's own suite — the oracle pin and the identity claims.

``examples/muse_arms.py`` makes four claims a reader has to be able to check
without dialling anything, and this file is where each is checked:

1. **The oracle is imported unchanged.** Not copied, not re-derived — the very
   function objects ``examples/challenge_subset.py`` holds, asserted by
   identity, and pinned at 76 with the planted trap 72 rejected.
2. **One runner, three arms differing only in wired tools.** The boundary is
   byte-identical across arms, the controls take no arm argument, the runner
   never compares an arm name, and the only per-arm system text is a constant
   imported verbatim from the module that owns the tool.
3. **The graders are honest.** The answer reader never grades a lenient parse;
   the counsel scorer cannot see the answer and is not vacuous.
4. **A degraded call is data.** A transport failure is recorded *and* re-raised,
   so the session degrades rather than being silently re-dialled.

Nothing here reaches the network. The end-to-end runner tests drive the real
:class:`~examples.muse_arms.RecordingSeam` with ``urlopen`` replaced, so the
real body-building, tool-call parsing and turn recording are exercised; arm C
uses headspace's in-memory ``fake`` provider, never a daemon.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.loop import UnknownToolError
from embodiment.muse import MARKER_DONE, MUSE_AUTHORITY, MUSE_TOOL_AUTHORITY
from embodiment.muse_pad import MUSE_PAD_PROTOCOL, MUSE_PAD_TOOL_NAMES, MusePad
from embodiment.workspace import (
    CLOSED_TEXT,
    PROVIDER_FAKE,
    WORKSPACE_PROTOCOL,
    WORKSPACE_TOOL_NAME,
    MuseWorkspace,
)
from examples import challenge_subset, muse_arms
from examples.muse_arms import (
    ARM_A,
    ARM_B,
    ARM_C,
    ARM_LANES,
    ARMS,
    CLASS_ARITHMETIC,
    CLASS_DEGRADED,
    CLASS_DISPLACED,
    COUNSEL_VERDICT_COUNSEL,
    COUNSEL_VERDICT_RESTATED,
    COUNSEL_VERDICT_SILENT,
    LANE_PAD,
    LANE_WORKSPACE,
    PINNED_TRAP,
    PINNED_TRUTH,
    PROBLEM_STATEMENT,
    TASK_FRAMING,
    VERDICT_ABSTAINED,
    VERDICT_CORRECT,
    VERDICT_MALFORMED,
    VERDICT_NO_ANSWER,
    VERDICT_TRAP,
    VERDICT_WRONG,
    ArmTools,
    RecordingSeam,
    analyse,
    build_tools,
    classify_execution,
    closing_messages,
    controls,
    counsel_score,
    host_framing,
    main,
    oracle_pin,
    read_answer,
    run_once,
    strip_answer_lines,
    system_message,
    wire_report,
)

MODULE_PATH = Path(muse_arms.__file__).resolve()


# ── 1. the oracle: imported unchanged, and pinned ─────────────────────────────


class TestOracleIsImportedUnchanged:
    """``truth`` and ``grade`` are the challenge_subset objects, not copies."""

    def test_truth_is_the_very_same_function_object(self) -> None:
        assert muse_arms.truth is challenge_subset.truth

    def test_grade_is_the_very_same_function_object(self) -> None:
        assert muse_arms.grade is challenge_subset.grade

    def test_the_harness_defines_no_truth_or_grade_of_its_own(self) -> None:
        """An import that a local def shadows is not an import. Checked by AST."""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        assert "truth" not in defined
        assert "grade" not in defined

    def test_truth_computes_seventy_six(self) -> None:
        assert muse_arms.truth() == PINNED_TRUTH == 76

    def test_the_trap_is_rejected(self) -> None:
        graded = muse_arms.grade(PINNED_TRAP)
        assert PINNED_TRAP == 72
        assert not graded["is_correct"]
        assert graded["is_trap"]

    def test_the_pin_record_reports_both(self) -> None:
        pin = oracle_pin()
        assert pin["truth"] == 76
        assert pin["trap"] == 72
        assert pin["trap_is_rejected"] is True
        assert pin["truth_is_accepted"] is True
        assert pin["matches_pin"] is True

    def test_a_run_refuses_to_dial_when_the_pin_moves(self, monkeypatch, tmp_path) -> None:
        """The whole series is comparable only while the oracle is byte-stable."""
        monkeypatch.setattr(muse_arms, "PINNED_TRUTH", 77)
        monkeypatch.setenv("COLLEAGUE_API_KEY", "x")
        code = main(["--out", str(tmp_path / "t.jsonl"), "--arms", "A"])
        assert code == 2


class TestProblemStatementDerivation:
    """The problem is derived from the oracle's own text, never retyped."""

    def test_it_is_a_prefix_of_the_oracle_problem(self) -> None:
        assert challenge_subset.PROBLEM.startswith(PROBLEM_STATEMENT)

    def test_it_drops_the_finish_instruction_no_arm_can_follow(self) -> None:
        assert "finish" in challenge_subset.PROBLEM
        assert "finish" not in PROBLEM_STATEMENT

    def test_it_still_carries_the_whole_question(self) -> None:
        assert "no two consecutive integers" in PROBLEM_STATEMENT
        assert "even element-sum" in PROBLEM_STATEMENT


# ── 2. one runner, three arms differing only in wired tools ───────────────────


def _function(name: str) -> ast.FunctionDef:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {MODULE_PATH}")


class TestOneRunner:
    """The arm is data. It reaches the runner as a key, never as a branch."""

    def test_arm_lanes_covers_exactly_the_arms(self) -> None:
        assert tuple(ARM_LANES) == ARMS == (ARM_A, ARM_B, ARM_C)

    def test_the_lanes_are_nested_a_subset_of_b_subset_of_c(self) -> None:
        assert ARM_LANES[ARM_A] == ()
        assert ARM_LANES[ARM_B] == (LANE_PAD,)
        assert ARM_LANES[ARM_C] == (LANE_PAD, LANE_WORKSPACE)

    @pytest.mark.parametrize("name", ["run_once", "build_tools", "host_framing"])
    def test_no_function_on_the_run_path_compares_an_arm_name(self, name: str) -> None:
        for node in ast.walk(_function(name)):
            if isinstance(node, ast.Compare):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                assert "arm" not in names, f"{name} branches on the arm name"

    def test_the_runner_holds_no_arm_literal(self) -> None:
        for node in ast.walk(_function("run_once")):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in ARMS

    def test_controls_and_boundary_take_no_arm_argument(self) -> None:
        for name in ("controls", "boundary"):
            args = _function(name).args
            assert not args.args, f"{name} varies by arm"
            assert not args.kwonlyargs, f"{name} varies by arm"


class TestTheArmsPutTheSameThingOnTheWire:
    """Captured off three real loops, not reconstructed."""

    def test_the_user_message_is_byte_identical(self) -> None:
        report = wire_report()
        users = {arm: report["captures"][arm]["user_message"] for arm in ARMS}
        assert len(set(users.values())) == 1
        assert PROBLEM_STATEMENT in users[ARM_A]

    def test_the_reconstruction_matches_what_is_sent(self) -> None:
        report = wire_report()
        assert report["system_matches_reconstruction"] == {arm: True for arm in ARMS}

    def test_arm_a_is_the_tools_off_seam_exactly(self) -> None:
        assert build_tools(ARM_A, pad_dir=None, provider=PROVIDER_FAKE) is None
        assert system_message(ARM_A) == f"{MUSE_AUTHORITY}\n\n{TASK_FRAMING}"

    def test_every_added_block_is_an_imported_constant(self) -> None:
        """The harness writes no per-arm prose. This is that claim, exactly."""
        assert host_framing(ARM_A) == TASK_FRAMING
        assert host_framing(ARM_B) == f"{TASK_FRAMING}\n\n{MUSE_PAD_PROTOCOL}"
        assert host_framing(ARM_C) == (
            f"{TASK_FRAMING}\n\n{MUSE_PAD_PROTOCOL}\n\n{WORKSPACE_PROTOCOL}"
        )

    def test_the_tool_authority_arrives_only_where_tools_do(self) -> None:
        report = wire_report()
        assert MUSE_TOOL_AUTHORITY not in report["captures"][ARM_A]["system_message"]
        for arm in (ARM_B, ARM_C):
            assert MUSE_TOOL_AUTHORITY in report["captures"][arm]["system_message"]

    def test_the_task_framing_is_first_and_identical_everywhere(self) -> None:
        for arm in ARMS:
            assert host_framing(arm).startswith(TASK_FRAMING)

    def test_the_schema_on_the_wire_is_exactly_the_wired_lanes(self) -> None:
        report = wire_report()
        assert report["captures"][ARM_A]["tool_names"] == []
        assert report["captures"][ARM_B]["tool_names"] == list(MUSE_PAD_TOOL_NAMES)
        assert report["captures"][ARM_C]["tool_names"] == [
            *MUSE_PAD_TOOL_NAMES,
            WORKSPACE_TOOL_NAME,
        ]

    def test_no_arm_is_offered_finish(self) -> None:
        """The muse has no final-answer tool in any arm — that is the seam's rule."""
        report = wire_report()
        for arm in ARMS:
            assert "finish" not in report["captures"][arm]["tool_names"]

    def test_the_controls_are_one_object_shared_by_every_arm(self) -> None:
        """No arm argument, so no arm can be handed different bounds.

        This asserted ``controls() == controls()``, which can only fail if the
        factory is nondeterministic — it never checked the property its own
        name claims. What actually makes the controls shared is that the
        factory takes no parameters at all: there is no seam through which an
        arm could ask for its own, so the bounds cannot differ by arm.
        """
        assert len(inspect.signature(muse_arms.controls).parameters) == 0

        built = controls()
        assert built.max_turns == muse_arms.MAX_TURNS
        assert built.max_quiet_turns == muse_arms.MAX_QUIET_TURNS
        assert built.max_context_chars == muse_arms.BOUNDARY_CHARS
        assert built.max_insight_chars == muse_arms.INSIGHT_CHARS
        assert built.max_tool_rounds == muse_arms.MAX_TOOL_ROUNDS
        assert built.max_tool_result_chars == muse_arms.MAX_TOOL_RESULT_CHARS


# ── 3a. the answer reader ─────────────────────────────────────────────────────


class TestReadAnswer:
    """The only verdict path, and it never grades a lenient parse."""

    def test_the_truth_is_correct(self) -> None:
        record = read_answer("thinking hard\nANSWER: 76")
        assert record["verdict"] == VERDICT_CORRECT
        assert record["answer"] == 76
        assert record["confidently_wrong"] is False
        assert record["graded"]["is_correct"] is True

    def test_the_trap_is_its_own_verdict(self) -> None:
        record = read_answer("ANSWER: 72")
        assert record["verdict"] == VERDICT_TRAP
        assert record["confidently_wrong"] is True
        assert record["graded"]["is_trap"] is True

    def test_any_other_integer_is_wrong(self) -> None:
        record = read_answer("ANSWER: 89")
        assert record["verdict"] == VERDICT_WRONG
        assert record["confidently_wrong"] is True

    @pytest.mark.parametrize("payload", ["none", "None.", "no answer", "unsure", "I don't know"])
    def test_an_explicit_refusal_is_not_confidently_wrong(self, payload: str) -> None:
        record = read_answer(f"ANSWER: {payload}")
        assert record["verdict"] == VERDICT_ABSTAINED
        assert record["confidently_wrong"] is False

    def test_a_non_integer_payload_is_malformed_not_guessed_at(self) -> None:
        record = read_answer("ANSWER: about seventy-six")
        assert record["verdict"] == VERDICT_MALFORMED
        assert record["answer"] is None

    def test_silence_is_no_answer(self) -> None:
        record = read_answer("I thought about it and stopped.")
        assert record["verdict"] == VERDICT_NO_ANSWER
        assert record["confidently_wrong"] is False

    def test_the_last_answer_line_wins(self) -> None:
        record = read_answer("ANSWER: 72\nwait, recount\nANSWER: 76")
        assert record["answer"] == 76

    def test_a_lenient_parse_is_recorded_and_never_the_verdict(self) -> None:
        """An exhaustive search mentioning 76 must not score as an answer."""
        record = read_answer("candidates: 72, 74, 76 ... I cannot decide.")
        assert record["verdict"] == VERDICT_NO_ANSWER
        assert record["lenient_answer"] == 76
        assert record["answer"] is None

    def test_the_marker_is_matched_case_insensitively_with_slack(self) -> None:
        assert read_answer("answer :  76 ")["answer"] == 76


# ── 3b. the counsel scorer: blind to the answer, and not vacuous ──────────────

#: Adversarial fixtures, committed. A grader is only worth its failures.
FIXTURE_BARE_ANSWER = "ANSWER: 76"
FIXTURE_RESTATEMENT = (
    "The question asks how many subsets of one to ten contain no two consecutive "
    "integers and have an even element sum. So we need to count the subsets of "
    "one to ten with no two consecutive integers whose element sum is even."
)
FIXTURE_COUNSEL = (
    "GUIDANCE: the obvious route assumes the even-sum half is exactly half of the "
    "non-consecutive total, and that premise is never checked. It fails whenever "
    "the parity classes are unequal, which they are here. Instead of halving, build "
    "a recurrence that carries parity as a state.\n"
    "ANSWER: 76"
)
#: Agreement wearing challenge words, in the vocabulary the negation filter
#: actually covers (``_POLARITY_SENSITIVE``). The filter is partial **by
#: design** — half the assumption vocabulary is inherently negative and would
#: filter itself — so this fixture pins the covered half rather than pretending
#: the whole surface is closed. See :data:`FIXTURE_EXEMPT_NEGATION` for the hole.
FIXTURE_NEGATED_VOCABULARY = (
    "There is no risk at all, no danger, and no counterexample. It does not fail "
    "and it does not break."
)

#: The known hole, committed as a fixture rather than left to be rediscovered:
#: negated vocabulary the filter deliberately exempts still scores as counsel.
#: Direction of the error: it **flatters** the response. Recorded here so a
#: reader of an arm's counsel numbers knows which way they lean.
FIXTURE_EXEMPT_NEGATION = (
    "There is no unstated assumption anywhere; I would not change a thing about "
    "how this was reasoned."
)


class TestCounselScorer:
    """Scored with the answer stripped first, so it cannot see correctness."""

    def test_stripping_removes_only_answer_lines(self) -> None:
        assert strip_answer_lines("keep me\nANSWER: 76\nkeep me too") == "keep me\nkeep me too"

    def test_the_score_is_identical_for_a_right_and_a_wrong_answer(self) -> None:
        """Independence from correctness, as a mechanism rather than a promise."""
        body = FIXTURE_COUNSEL.replace("ANSWER: 76", "")
        right = counsel_score(body + "\nANSWER: 76")
        wrong = counsel_score(body + "\nANSWER: 72")
        bare = counsel_score(body)
        assert right == wrong == bare

    def test_a_bare_answer_scores_silent(self) -> None:
        assert counsel_score(FIXTURE_BARE_ANSWER)["verdict"] == COUNSEL_VERDICT_SILENT

    def test_a_restatement_of_the_problem_scores_restated(self) -> None:
        assert counsel_score(FIXTURE_RESTATEMENT)["verdict"] == COUNSEL_VERDICT_RESTATED

    def test_real_counsel_scores_counsel(self) -> None:
        score = counsel_score(FIXTURE_COUNSEL)
        assert score["verdict"] == COUNSEL_VERDICT_COUNSEL
        assert score["moves"]

    def test_negated_challenge_vocabulary_is_not_counsel(self) -> None:
        """ "There is no risk" is agreement wearing challenge words."""
        assert counsel_score(FIXTURE_NEGATED_VOCABULARY)["verdict"] == COUNSEL_VERDICT_RESTATED

    def test_the_negation_filter_is_partial_and_the_hole_is_pinned(self) -> None:
        """The filter's exemptions are load-bearing, so this is a limit, not a bug.

        Pinned as a *passing* assertion of the wrong-looking behaviour, because
        a limitation nobody wrote down gets rediscovered as a defect. The error
        flatters the response, which is the direction a reader of the counsel
        numbers needs to know.
        """
        assert counsel_score(FIXTURE_EXEMPT_NEGATION)["verdict"] == COUNSEL_VERDICT_COUNSEL

    def test_the_scorer_is_not_vacuous(self) -> None:
        """A grader that passes everything measures nothing."""
        verdicts = {
            counsel_score(fixture)["verdict"]
            for fixture in (
                FIXTURE_BARE_ANSWER,
                FIXTURE_RESTATEMENT,
                FIXTURE_COUNSEL,
                FIXTURE_NEGATED_VOCABULARY,
            )
        }
        assert len(verdicts) >= 3

    def test_it_never_claims_to_judge_soundness(self) -> None:
        assert "not machine-graded" in counsel_score(FIXTURE_COUNSEL)["counsel_soundness"]


# ── 3c. rule R-C1: what an arm C execution was for ────────────────────────────


def _call(command: Any, *, result: str = "", ran: bool = True) -> dict[str, Any]:
    return {
        "name": WORKSPACE_TOOL_NAME,
        "arguments": {"command": command},
        "result": result,
        "ran": ran,
    }


class TestClassifyExecution:
    """Fixed before any measured run; task t18 applies it, never invents it."""

    def test_a_whole_problem_solver_is_reasoning_displaced(self) -> None:
        command = [
            "python3",
            "-c",
            "from itertools import combinations\n"
            "print(sum(1 for r in range(11) for s in combinations(range(1, 11), r) "
            "if all(b - a > 1 for a, b in zip(s, s[1:])) and sum(s) % 2 == 0))",
        ]
        verdict = classify_execution(_call(command, result="captured output:\n76"))
        assert verdict["class"] == CLASS_DISPLACED
        assert len(verdict["signals"]) == 4

    def test_a_partial_check_is_arithmetic_offloaded(self) -> None:
        verdict = classify_execution(
            _call(["python3", "-c", "print(1 + 3 + 5 + 7)"], result="captured output:\n16")
        )
        assert verdict["class"] == CLASS_ARITHMETIC
        assert verdict["audit_required"] is False

    def test_a_small_n_instance_is_arithmetic_offloaded(self) -> None:
        verdict = classify_execution(
            _call(["python3", "-c", "print(fib(5))"], result="captured output:\n8")
        )
        assert verdict["class"] == CLASS_ARITHMETIC

    def test_a_call_that_never_ran_is_degraded_not_classified(self) -> None:
        verdict = classify_execution(
            _call(["python3"], result="no workspace is available", ran=False)
        )
        assert verdict["class"] == CLASS_DEGRADED
        assert verdict["class"] not in (CLASS_ARITHMETIC, CLASS_DISPLACED)

    def test_the_named_hole_flags_itself_for_audit(self) -> None:
        """Arithmetic-offloaded, yet the output carried the truth. Hand-audit it."""
        verdict = classify_execution(
            _call(["python3", "-c", "print(f(10))"], result="captured output:\n76")
        )
        assert verdict["class"] == CLASS_ARITHMETIC
        assert verdict["result_contains_truth"] is True
        assert verdict["audit_required"] is True

    def test_the_output_never_decides_the_class(self) -> None:
        """Same command, different outputs, same classification."""
        command = ["python3", "-c", "print(2 + 2)"]
        assert (
            classify_execution(_call(command, result="4"))["class"]
            == classify_execution(_call(command, result="76"))["class"]
        )

    def test_a_bare_seventy_six_needs_word_boundaries(self) -> None:
        assert classify_execution(_call(["x"], result="176"))["result_contains_truth"] is False

    def test_a_malformed_argv_yields_no_signals(self) -> None:
        verdict = classify_execution(_call("not-a-list", ran=False))
        assert verdict["command"] == []
        assert verdict["class"] == CLASS_DEGRADED


# ── 4. the composite bench ────────────────────────────────────────────────────


class TestArmTools:
    """Two lanes on one bench, dispatched by schema membership."""

    def _tools(self) -> ArmTools:
        return ArmTools(
            [(LANE_PAD, MusePad()), (LANE_WORKSPACE, MuseWorkspace(provider=PROVIDER_FAKE))]
        )

    def test_the_schema_is_both_lanes_in_wiring_order(self) -> None:
        names = [(tool["function"]["name"]) for tool in self._tools().schema]
        assert names == [*MUSE_PAD_TOOL_NAMES, WORKSPACE_TOOL_NAME]

    def test_a_pad_call_reaches_the_pad(self) -> None:
        tools = self._tools()
        tools.execute("intend", {"text": "check the recurrence at n=5"})
        assert tools.lane(LANE_PAD).counts().kinds["intend"] == 1
        assert tools.calls[0]["lane"] == LANE_PAD

    def test_a_workspace_call_reaches_the_workspace(self) -> None:
        tools = self._tools()
        tools.execute(WORKSPACE_TOOL_NAME, {"command": ["python3", "-c", "print(1)"]})
        assert tools.calls[0]["lane"] == LANE_WORKSPACE
        assert tools.calls[0]["ran"] is True

    def test_an_unwired_name_is_refused_once_here_and_never_reaches_a_lane(self) -> None:
        tools = self._tools()
        with pytest.raises(UnknownToolError):
            tools.execute("finish", {"answer": 76})
        assert tools.off_protocol == 1
        # The pad would have counted it too had the call been delegated.
        assert tools.lane(LANE_PAD).counts().off_protocol_calls == 0
        assert tools.calls[-1]["error"]

    def test_a_refused_workspace_argv_is_recorded_as_not_run(self) -> None:
        tools = self._tools()
        tools.execute(WORKSPACE_TOOL_NAME, {"command": "python3 -c 'print(1)'"})
        assert tools.calls[-1]["ran"] is False

    def test_a_call_into_a_closed_lane_is_recorded_as_not_run(self) -> None:
        """The refusal list drifted from the module it reads once already.

        ``CLOSED_TEXT`` was missing from it, so a call the lane had refused
        because the drive ended would have been counted as a call that ran —
        inflating the very number arm C is graded on. It is imported now
        rather than retyped, so the two cannot part again silently.
        """
        tools = self._tools()
        tools.lane(LANE_WORKSPACE).close()
        result = tools.execute(WORKSPACE_TOOL_NAME, {"command": ["python3", "-c", "print(1)"]})
        assert CLOSED_TEXT in str(result)
        assert tools.calls[-1]["ran"] is False

    def test_every_call_is_recorded_with_its_arguments_and_result(self) -> None:
        tools = self._tools()
        tools.execute("intend", {"text": "one"})
        tools.execute("observe", {"text": "two"})
        assert [call["name"] for call in tools.calls] == ["intend", "observe"]
        assert tools.calls[0]["arguments"] == {"text": "one"}
        assert tools.calls[0]["result"]

    def test_counts_carry_both_lanes(self) -> None:
        tools = self._tools()
        tools.execute("intend", {"text": "one"})
        counts = tools.counts()
        assert counts["lanes"] == [LANE_PAD, LANE_WORKSPACE]
        assert counts[LANE_PAD]["kinds"]["intend"] == 1
        assert counts[LANE_WORKSPACE]["runs"] == 0
        assert counts["by_name"] == {"intend": 1}


# ── the fake transport (no network anywhere in this file) ─────────────────────


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeHTTP:
    """A scripted ``urlopen``. Records every body; the last payload repeats."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: Optional[float] = None) -> _FakeResponse:
        self.bodies.append(json.loads(request.data.decode("utf-8")))
        payload = self.payloads[min(len(self.bodies) - 1, len(self.payloads) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return _FakeResponse(json.dumps(payload).encode("utf-8"))


def _reply(
    content: str = "",
    *,
    tool_calls: Optional[list[dict[str, Any]]] = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    message: dict[str, Any] = {"content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def _tool_call(name: str, arguments: str, call_id: str = "c1") -> dict[str, Any]:
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _seam(monkeypatch, *payloads: Any) -> tuple[RecordingSeam, _FakeHTTP]:
    http = _FakeHTTP(*payloads)
    monkeypatch.setattr(muse_arms.urllib.request, "urlopen", http)
    seam = RecordingSeam(base_url="http://localhost:8001/v1", model="m", api_key="k")
    return seam, http


# ── 5. the recording seam ─────────────────────────────────────────────────────


class TestRecordingSeam:
    """Every prompt and every response, and never a silent retry."""

    def test_it_records_the_whole_prompt_and_the_whole_reply(self, monkeypatch) -> None:
        seam, _ = _seam(monkeypatch, _reply("hello"))
        seam.floor([{"role": "user", "content": "hi"}])
        turn = seam.turns[0]
        assert turn["messages"] == [{"role": "user", "content": "hi"}]
        assert turn["raw_content"] == "hello"
        assert turn["usage"] == {"prompt_tokens": 11, "completion_tokens": 7}
        assert turn["latency_s"] is not None

    def test_the_tools_off_body_carries_no_schema(self, monkeypatch) -> None:
        seam, http = _seam(monkeypatch, _reply("x"))
        seam.floor([{"role": "user", "content": "hi"}])
        assert "tools" not in http.bodies[0]

    def test_the_tools_on_body_differs_only_by_the_schema(self, monkeypatch) -> None:
        seam, http = _seam(monkeypatch, _reply("x"))
        messages = [{"role": "user", "content": "hi"}]
        schema = [{"type": "function", "function": {"name": "t"}}]
        seam.floor(messages)
        seam.tools(messages, schema)
        off, on = http.bodies
        assert on.pop("tools") == schema
        assert off == on

    def test_a_transport_failure_is_recorded_and_re_raised(self, monkeypatch) -> None:
        """A degraded call is data. Nothing is retried for a better number."""
        seam, http = _seam(monkeypatch, OSError("connection refused"))
        with pytest.raises(OSError):
            seam.floor([{"role": "user", "content": "hi"}])
        assert len(seam.turns) == 1
        assert "connection refused" in seam.turns[0]["transport_error"]
        assert len(http.bodies) == 1

    def test_malformed_tool_arguments_are_named_not_fatal(self, monkeypatch) -> None:
        seam, _ = _seam(monkeypatch, _reply("", tool_calls=[_tool_call("intend", "{not json")]))
        response = seam.floor([{"role": "user", "content": "hi"}])
        assert response.tool_calls[0].arguments == {}
        assert seam.turns[0]["argument_parse_errors"]

    def test_a_truncated_turn_keeps_its_finish_reason(self, monkeypatch) -> None:
        seam, _ = _seam(monkeypatch, _reply("cut off", finish_reason="length"))
        seam.floor([{"role": "user", "content": "hi"}])
        assert seam.turns[0]["finish_reason"] == "length"


# ── 6. the runner, end to end, with no network ────────────────────────────────


class TestRunOnce:
    """The same call for every arm; the arm only chooses the wiring."""

    def test_arm_a_runs_tools_off_and_grades_its_answer(self, monkeypatch) -> None:
        seam, http = _seam(
            monkeypatch, _reply(f"GUIDANCE: check parity\nANSWER: 76\n{MARKER_DONE}")
        )
        run = run_once(ARM_A, 0, seam=seam, pad_dir=None, provider=PROVIDER_FAKE)
        record = run.to_dict()
        assert record["answer"]["verdict"] == VERDICT_CORRECT
        assert record["lanes"] == []
        assert "tools" not in http.bodies[0]
        assert record["turns"][0]["messages"][0]["content"] == system_message(ARM_A)

    def test_arm_b_reaches_the_pad_and_reports_protocol_adherence(
        self, monkeypatch, tmp_path
    ) -> None:
        seam, _ = _seam(
            monkeypatch,
            _reply("", tool_calls=[_tool_call("intend", json.dumps({"text": "try a recurrence"}))]),
            _reply(f"the pad says I never checked.\nANSWER: 72\n{MARKER_DONE}"),
        )
        run = run_once(ARM_B, 0, seam=seam, pad_dir=tmp_path, provider=PROVIDER_FAKE)
        record = run.to_dict()
        pad = record["lane_counts"][LANE_PAD]
        assert pad["kinds"] == {"intend": 1, "observe": 0, "conclude": 0, "revise": 0}
        assert pad["open_intents"] == 1
        assert record["answer"]["verdict"] == VERDICT_TRAP
        assert record["answer"]["confidently_wrong"] is True
        assert record["pad_lines"], "the pad's own on-disk record is committed"

    def test_arm_c_records_and_classifies_its_executions(self, monkeypatch, tmp_path) -> None:
        seam, _ = _seam(
            monkeypatch,
            _reply(
                "",
                tool_calls=[
                    _tool_call(
                        WORKSPACE_TOOL_NAME,
                        json.dumps({"command": ["python3", "-c", "print(1 + 3 + 5)"]}),
                    )
                ],
            ),
            _reply(f"nine. GUIDANCE: the halving premise is unchecked.\nANSWER: 76\n{MARKER_DONE}"),
        )
        run = run_once(ARM_C, 0, seam=seam, pad_dir=tmp_path, provider=PROVIDER_FAKE)
        record = run.to_dict()
        assert len(record["executions"]) == 1
        assert record["executions"][0]["class"] == CLASS_ARITHMETIC
        assert record["executions"][0]["rule"] == "R-C1"
        assert record["lane_counts"][LANE_WORKSPACE]["runs"] == 1

    def test_a_transport_failure_degrades_the_run_and_is_reported(self, monkeypatch) -> None:
        seam, http = _seam(monkeypatch, OSError("no route to host"))
        run = run_once(ARM_A, 0, seam=seam, pad_dir=None, provider=PROVIDER_FAKE)
        record = run.to_dict()
        assert record["exit_reason"] == "degraded"
        assert record["degradations"]
        assert record["transport_errors"]
        assert record["answer"]["verdict"] == VERDICT_NO_ANSWER
        assert len(http.bodies) == 1, "a degraded call must not be re-dialled"

    def test_every_prompt_and_response_lands_in_the_record(self, monkeypatch) -> None:
        seam, _ = _seam(monkeypatch, _reply("thinking"), _reply(f"ANSWER: 76\n{MARKER_DONE}"))
        record = run_once(ARM_A, 0, seam=seam, pad_dir=None, provider=PROVIDER_FAKE).to_dict()
        assert len(record["turns"]) >= 2
        for turn in record["turns"]:
            assert turn["messages"]
            assert "raw_content" in turn

    def test_the_counsel_score_ignores_the_answer(self, monkeypatch) -> None:
        seam, _ = _seam(monkeypatch, _reply(f"ANSWER: 76\n{MARKER_DONE}"))
        record = run_once(ARM_A, 0, seam=seam, pad_dir=None, provider=PROVIDER_FAKE).to_dict()
        assert record["answer"]["verdict"] == VERDICT_CORRECT
        assert record["counsel"]["verdict"] == COUNSEL_VERDICT_SILENT

    def test_belief_change_is_unaskable_without_an_execution(self, monkeypatch) -> None:
        seam, _ = _seam(monkeypatch, _reply(f"ANSWER: 76\n{MARKER_DONE}"))
        record = run_once(ARM_A, 0, seam=seam, pad_dir=None, provider=PROVIDER_FAKE).to_dict()
        assert record["belief_changed_after_execution"] is None


# ── 6b. the closing turn: one constant, put to every arm alike ────────────────


class TestClosingTurn:
    """Why it exists: the smoke run measured a tool arm writing zero prose."""

    def test_it_appends_to_the_muses_own_history_and_rebuilds_nothing(self) -> None:
        history = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "tool_call_id": "c1", "name": "intend", "content": "n1 recorded"},
        ]
        messages = closing_messages([{"messages": history, "raw_content": "last words"}])
        assert messages[: len(history)] == history
        assert messages[-2] == {"role": "assistant", "content": "last words"}
        assert messages[-1] == {"role": "user", "content": muse_arms.CLOSING_PROMPT}

    def test_unresolved_tool_calls_are_not_echoed(self) -> None:
        """An assistant turn with calls and no results is a malformed exchange."""
        messages = closing_messages(
            [{"messages": [{"role": "user", "content": "u"}], "raw_content": ""}]
        )
        assert [message["role"] for message in messages] == ["user", "user"]

    def test_there_is_nothing_to_close_when_no_turn_happened(self) -> None:
        assert closing_messages([]) == []

    def test_it_rescues_an_arm_that_wrote_only_tool_calls(self, monkeypatch, tmp_path) -> None:
        """The measured failure: six pad writes, zero prose, no answer at all."""
        pad_call = _tool_call("intend", json.dumps({"text": "I will try a recurrence"}))
        seam, _ = _seam(
            monkeypatch,
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("", tool_calls=[pad_call]),
            _reply("ANSWER: 72"),
        )
        record = run_once(ARM_B, 0, seam=seam, pad_dir=tmp_path, provider=PROVIDER_FAKE).to_dict()
        assert record["closing_turn_used"] is True
        assert record["answer"]["verdict"] == VERDICT_TRAP

    def test_the_closing_turn_is_tools_off_for_every_arm(self, monkeypatch, tmp_path) -> None:
        seam, http = _seam(
            monkeypatch,
            _reply("", tool_calls=[_tool_call("intend", json.dumps({"text": "x"}))]),
            _reply(f"ANSWER: 76\n{MARKER_DONE}"),
        )
        run_once(ARM_B, 0, seam=seam, pad_dir=tmp_path, provider=PROVIDER_FAKE)
        assert seam.turns[-1]["closing"] is True
        assert "tools" not in http.bodies[-1]

    def test_the_prompt_is_one_constant_with_no_arm_in_it(self) -> None:
        for arm in ARMS:
            assert arm not in muse_arms.CLOSING_PROMPT.split()
        assert muse_arms.ANSWER_PROTOCOL_LINE in muse_arms.CLOSING_PROMPT
        assert "none" in muse_arms.CLOSING_PROMPT

    def test_it_is_not_asked_after_a_transport_failure(self, monkeypatch) -> None:
        """Re-dialling straight after a dead call is a retry in all but name."""
        seam, http = _seam(monkeypatch, OSError("dead"))
        record = run_once(ARM_A, 0, seam=seam, pad_dir=None, provider=PROVIDER_FAKE).to_dict()
        assert len(http.bodies) == 1
        assert record["closing_turn_used"] is False
        assert "not re-dialling" in record["closing_error"]


# ── 7. the CLI: dry run, the fake-provider guard, and analysis ────────────────


class TestCli:
    def test_dry_run_dials_nothing_and_reports_the_identity_claims(self, capsys) -> None:
        assert main(["--dry-run"]) == 0
        out = capsys.readouterr().out
        assert '"user_message_identical": true' in out
        assert TASK_FRAMING.splitlines()[0] in out

    def test_the_fake_provider_is_refused_without_smoke(self, capsys, tmp_path) -> None:
        code = main(["--provider", PROVIDER_FAKE, "--out", str(tmp_path / "t.jsonl")])
        assert code == 1
        assert "fabricates success" in capsys.readouterr().err

    def test_an_unknown_arm_is_refused(self) -> None:
        with pytest.raises(SystemExit):
            main(["--arms", "D", "--dry-run"])

    def test_analyse_renders_every_table_from_the_transcript(self, tmp_path, capsys) -> None:
        transcript = tmp_path / "t.jsonl"
        rows = [
            {"kind": "config", "muse_model": "m", "measured": True, "workspace_provider": "docker"},
            {"kind": "oracle", **oracle_pin()},
        ]
        for arm, verdict_text in ((ARM_A, "ANSWER: 72"), (ARM_C, "ANSWER: 76")):
            run = muse_arms.ArmRun(arm=arm, repeat=0)
            run.turns = [
                {
                    "seq": 0,
                    "raw_content": verdict_text,
                    "tool_calls": [],
                    "transport_error": None,
                    "finish_reason": "stop",
                    "messages": [],
                }
            ]
            rows.append(run.to_dict())
        transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        text = analyse(transcript)
        for heading in ("Final answers", "Pad protocol", "Arm C executions", "Counsel quality"):
            assert heading in text
        assert "AUDIT REQUIRED" in text
        assert main(["--analyse", "--out", str(transcript)]) == 0
        assert "Final answers" in capsys.readouterr().out

    def test_a_multiline_command_cannot_break_the_results_table(self, tmp_path) -> None:
        """A real execution was a five-line payload. Rows must survive one."""
        transcript = tmp_path / "t.jsonl"
        run = muse_arms.ArmRun(arm=ARM_C, repeat=0)
        run.turns = [
            {
                "seq": 0,
                "raw_content": "ANSWER: 76",
                "tool_calls": [{"name": WORKSPACE_TOOL_NAME}],
                "transport_error": None,
                "finish_reason": "stop",
                "messages": [],
            }
        ]
        run.tool_calls = [
            {
                "name": WORKSPACE_TOOL_NAME,
                "arguments": {"command": ["python3", "-c", "a = 1\nb = 2 | 3\nprint(a)"]},
                "result": "captured output:\n3",
                "ran": True,
            }
        ]
        rows = [
            {"kind": "config", "measured": True},
            {"kind": "oracle", **oracle_pin()},
            run.to_dict(),
        ]
        transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        assert "\n" not in "".join(analyse(transcript).splitlines())
        rendered = muse_arms.execution_table([run.to_dict()])
        header, _rule, row = rendered
        assert row.count("|") - row.count("\\|") == header.count("|")
        assert "\\|" in row, "a pipe inside a command must be escaped, never raw"

    def test_analyse_shouts_when_the_transcript_was_a_wiring_run(self, tmp_path) -> None:
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            json.dumps({"kind": "config", "measured": False}) + "\n", encoding="utf-8"
        )
        assert "NOT MEASURED" in analyse(transcript)
