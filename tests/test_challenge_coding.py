"""The coding rung: its truth functions, its M2 grader kit, and its jail.

Task **t7**. Three things are pinned here, and the third is the one the task
exists for:

1. **The truth functions compute.** Every answer quoted in
   ``docs/challenge-problems.md`` §5 is recomputed here, and cross-checked
   against a *second, independently written* method living in this file — the
   same discipline the doc itself follows. Answers are also read back out of
   the committed markdown, so the doc and the harness cannot drift apart
   silently.
2. **The grader ships the M2 kit** — adversarial fixtures, paraphrase cases, a
   vacuity assertion, and committed raw responses. Four graders shipped
   defective last cycle and every one was found by a human reading data rather
   than by a test; §3–§5 below are the response.
3. **Model-written code cannot execute outside the workspace.** Asserted by
   walking the AST of the harness *and of this file*, by driving the whole
   grading pipeline through a recording ``headspace.api`` double and checking
   where the model's source actually went, and by a sentinel payload that would
   leave a file on disk if anything had ever run it here. §6.

Nothing in this file executes model-written code. That is not an accident of
how the tests happen to be written — it is asserted, in
:class:`TestNoHostExecutionPrimitiveExists`, against this file's own source.
The always-on lane therefore uses a recording/scripted ``headspace.api`` double
that returns the output a real container really produced; the *live* lane
(``EMBODIMENT_LIVE_RIG=1``) runs every fixture in a real docker workspace and
checks the committed output against reality.
"""

from __future__ import annotations

import ast
import json
import os
from itertools import combinations, permutations, product
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.workspace import (
    DEGRADED_ENGINE_UNAVAILABLE,
    PROVIDER_DOCKER,
    PROVIDER_FAKE,
    MuseWorkspace,
)
from examples import challenge_coding as cc
from examples.challenge_coding import (
    KIND_ADVERSARIAL,
    KIND_CORRECT,
    KIND_PARAPHRASE,
    PROBLEM_ORDER,
    PROBLEMS,
    VERDICT_CORRECT,
    VERDICT_NO_CODE,
    VERDICT_NO_RESULT,
    VERDICT_NO_WORKSPACE,
    VERDICT_WRONG,
    build_program,
    extract_code,
    grade_response,
    solve_once,
    truth_parity_subsets,
    truth_preimage_count,
    truth_register_recover,
)

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "challenge-problems.md"
HARNESS_PATH = Path(cc.__file__).resolve()
THIS_PATH = Path(__file__).resolve()


# ── doubles ───────────────────────────────────────────────────────────────────


class _Evidence:
    """headspace's evidence shape, duck-typed — its real type is private."""

    def __init__(self, label: str, excerpt: str) -> None:
        self.label = label
        self.excerpt = excerpt


class _Provenance:
    """Where the workspace seam reads a provisioned id from."""

    def __init__(self, workspace_id: str = "ws-1") -> None:
        self.workspace_id = workspace_id
        self.policy_summary = "network=disabled, filesystem=()"


class _Package:
    """A ``ResultPackage``-shaped object, read duck-typed by the workspace."""

    def __init__(self, *, status: str = "success", output: str = "", summary: str = "") -> None:
        self.status = status
        self.outcome_summary = summary or "job ran in workspace ws-1"
        self.key_findings: list[str] = []
        self.warnings: list[str] = []
        self.provenance = _Provenance()
        self.evidence = [_Evidence("captured output", output)] if output else []


class ScriptedApi:
    """A ``headspace.api`` double that answers a program the way a container would.

    It reads the run nonce **out of the program it was handed** — exactly as the
    real driver does — and substitutes it into a committed stdout template. That
    keeps the double honest: it cannot answer a program it was never given, and
    a harness that stopped embedding the nonce would fail here rather than pass.
    """

    def __init__(
        self,
        stdout: str = "",
        *,
        status: str = "success",
        create_error: Optional[Exception] = None,
        run_error: Optional[Exception] = None,
    ) -> None:
        self.stdout = stdout
        self.status = status
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._create_error = create_error
        self._run_error = run_error

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(("create", (), dict(kwargs)))
        if self._create_error:
            raise self._create_error
        return _Package(summary="workspace ws-1 is ready on fake")

    def run(self, workspace_id: str, command: Any, **kwargs: Any) -> Any:
        self.calls.append(("run", (workspace_id, list(command)), dict(kwargs)))
        if self._run_error:
            raise self._run_error
        program = list(command)[-1]
        nonce = cc.nonce_of(program) or "MISSING"
        return _Package(status=self.status, output=self.stdout.replace("{nonce}", nonce))

    def destroy(self, workspace_id: str, **kwargs: Any) -> Any:
        self.calls.append(("destroy", (workspace_id,), dict(kwargs)))
        return _Package()

    def kwargs_for(self, verb: str) -> list[dict[str, Any]]:
        return [kwargs for name, _, kwargs in self.calls if name == verb]

    def argv_for_runs(self) -> list[list[str]]:
        return [list(args[1]) for name, args, _ in self.calls if name == "run"]


def _workspace(api: Any, **kwargs: Any) -> MuseWorkspace:
    return MuseWorkspace(provider=PROVIDER_FAKE, api=api, max_result_chars=0, **kwargs)


def _stdout_for(problem_id: str, values: list[Any], *, repeat: int = 1, noise: str = "") -> str:
    """The stdout a container prints when the model returned *values* in order."""
    problem = PROBLEMS[problem_id]
    rows = [
        {"i": i, "type": type(value).__name__, "value": value} for i, value in enumerate(values)
    ]
    payload = json.dumps({"entry": problem.entry, "outputs": rows}, sort_keys=True)
    lines = ([noise] if noise else []) + ["{nonce} " + payload] * repeat
    return "\n".join(lines) + ("\n" if lines else "")


def _correct_stdout(problem_id: str, **kwargs: Any) -> str:
    return _stdout_for(problem_id, list(PROBLEMS[problem_id].expected()), **kwargs)


def _fenced(source: str) -> str:
    return f"Here you go.\n\n```python\n{source}\n```\n"


# ══════════════════════════════════════════════════════════════════════════════
# 1. the truth functions compute, and agree with the committed document
# ══════════════════════════════════════════════════════════════════════════════


def _independent_parity_subsets(n: int) -> int:
    """Method A from the doc: enumerate every subset. Written out here, again."""
    return sum(
        1
        for r in range(n + 1)
        for subset in combinations(range(1, n + 1), r)
        if all(b - a > 1 for a, b in zip(subset, subset[1:])) and sum(subset) % 2 == 0
    )


def _fib(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


class TestParitySubsetsTruth:
    """Enumerated, never asserted — and anchored to problem 1's published 76."""

    @pytest.mark.parametrize("n", list(range(0, 17)))
    def test_it_agrees_with_an_exhaustive_enumeration(self, n: int) -> None:
        assert truth_parity_subsets(n) == _independent_parity_subsets(n)

    def test_it_reproduces_problem_1s_verified_answer(self) -> None:
        assert truth_parity_subsets(10) == 76

    def test_the_empty_set_counts(self) -> None:
        """A solver that iterates non-empty subsets is off by one everywhere."""
        assert truth_parity_subsets(0) == 1

    def test_it_is_not_the_halving_trap(self) -> None:
        assert truth_parity_subsets(10) != _fib(12) // 2 == 72

    @pytest.mark.parametrize("n", [0, 1, 2, 3, 5, 10, 20, 45, 90])
    def test_the_two_parity_classes_sum_to_a_fibonacci_number(self, n: int) -> None:
        even = truth_parity_subsets(n)
        odd = sum(
            1
            for r in range(n + 1)
            for subset in combinations(range(1, min(n, 16) + 1), r)
            if all(b - a > 1 for a, b in zip(subset, subset[1:])) and sum(subset) % 2
        )
        if n <= 16:
            assert even + odd == _fib(n + 2)
        else:  # the identity still has to hold; derive the odd half from it
            assert _fib(n + 2) - even > 0


def _independent_preimage_count(sequence: str, target: int) -> int:
    """Method B from the doc: analytic per-routine preimage relations, backwards."""

    def pre_c(ys: set[int]) -> set[int]:
        return {x for y in ys if y <= 127 for x in (2 * y, 2 * y + 1)}

    def pre_d(ys: set[int]) -> set[int]:
        return {x for y in ys if not y & 0x20 for x in (y, y | 0x20)}

    back = {
        "A": lambda ys: {(y - 47) % 256 for y in ys},
        "B": lambda ys: {y ^ 0xAA for y in ys},
        "C": pre_c,
        "D": pre_d,
        "E": lambda ys: {(y * 171) % 256 for y in ys},
    }
    current = {target}
    for name in reversed(sequence):
        current = back[name](current)
    return len(current)


class TestPreimageCountTruth:
    """Forward over all 256 starts, checked against the backward relations."""

    @pytest.mark.parametrize("length", [1, 2])
    def test_the_two_methods_agree_exhaustively_for_short_sequences(self, length: int) -> None:
        for tup in product("ABCDE", repeat=length):
            sequence = "".join(tup)
            for target in range(256):
                assert truth_preimage_count(sequence, target) == _independent_preimage_count(
                    sequence, target
                ), (sequence, target)

    def test_every_permutation_partitions_the_256_states(self) -> None:
        """Every start lands somewhere: the counts over all targets sum to 256."""
        for perm in permutations("ABCDE"):
            sequence = "".join(perm)
            assert sum(truth_preimage_count(sequence, t) for t in range(256)) == 256

    def test_a_permutation_can_only_ever_yield_zero_two_or_four(self) -> None:
        """One lossy C and one lossy D, so the count is 0, 2 or 4 — never 1."""
        seen = {
            truth_preimage_count("".join(perm), t)
            for perm in permutations("ABCDE")
            for t in range(0, 256, 17)
        }
        assert seen <= {0, 2, 4} and seen == {0, 2, 4}

    def test_the_lossy_edges_have_no_preimage(self) -> None:
        assert truth_preimage_count("C", 200) == 0, "LSR cannot produce y > 127"
        assert truth_preimage_count("D", 32) == 0, "AND 0xDF cannot set bit 5"


def _bits(value: int, width: int) -> list[int]:
    return [int(c) for c in format(value, f"0{width}b")]


def _unbits(bits: list[int]) -> int:
    return int("".join(str(b) for b in bits), 2)


def _independent_register_recover(
    width: int,
    records: list[list[Any]],
    distance: int,
    require_before: Optional[list[str]] = None,
    require_adjacent: Optional[list[str]] = None,
) -> list[tuple[str, str]]:
    """Method B from the doc: bit-list ops, order-major, allowed-set membership."""
    if width == 4:
        ops = {
            "A": lambda b: _bits((_unbits(b) + 0b0011) % 16, 4),
            "B": lambda b: [x ^ y for x, y in zip(b, [1, 0, 1, 1])],
            "C": lambda b: b[1:] + b[:1],
            "D": lambda b: _bits((_unbits(b[2:] + [0, 0]) + _unbits(b)) % 16, 4),
            "E": lambda b: b[::-1],
        }
    else:
        ops = {
            "A": lambda b: _bits((_unbits(b) + 0b00101111) % 256, 8),
            "B": lambda b: [x ^ y for x, y in zip(b, [1, 0, 1, 0, 1, 0, 1, 0])],
            "C": lambda b: [0] + b[:-1],
            "D": lambda b: [x & y for x, y in zip(b, [1, 1, 0, 1, 1, 1, 1, 1])],
            "E": lambda b: _bits((_unbits(b[1:] + [0]) + _unbits(b)) % 256, 8),
        }
    allowed = {
        int(position): {
            state
            for state in range(1 << width)
            if bin(state ^ int(recorded, 2)).count("1") == distance
        }
        for position, recorded in records
    }
    found: list[tuple[str, str]] = []
    for perm in permutations("ABCDE"):
        order = "".join(perm)
        if require_before and order.index(require_before[0]) > order.index(require_before[1]):
            continue
        if require_adjacent and "".join(require_adjacent) not in order:
            continue
        for start in range(1 << width):
            state = _bits(start, width)
            ok = True
            for step, name in enumerate(order, start=1):
                if width == 8 and name == "C" and state[-1] == 0:
                    ok = False
                    break
                state = ops[name](state)
                if step in allowed and _unbits(state) not in allowed[step]:
                    ok = False
                    break
            if ok:
                found.append((format(start, f"0{width}b"), order))
    return sorted(found)


#: The three instances this file's own problems 2, 3 and 3b already answered.
CANONICAL_RECORDS_4 = [[1, "0010"], [3, "0000"], [5, "1111"]]
CANONICAL_RECORDS_8 = [[1, "11000111"], [3, "10101011"], [5, "11101000"]]


class TestRegisterRecoverTruth:
    """The strongest check available: it must reproduce problems 2, 3 and 3b."""

    def test_it_reproduces_problem_2(self) -> None:
        found = truth_register_recover(4, CANONICAL_RECORDS_4, 1, require_before=["A", "D"])
        assert found == [("0101", "CBEAD")]

    def test_it_reproduces_problem_3s_under_determination(self) -> None:
        found = truth_register_recover(8, CANONICAL_RECORDS_8, 2)
        assert len(found) == 17
        assert len({initial for initial, _ in found}) == 13

    def test_it_reproduces_problem_3b(self) -> None:
        found = truth_register_recover(8, CANONICAL_RECORDS_8, 2, require_adjacent=["C", "A"])
        assert found == [("00001111", "CAEDB")]

    def test_an_unsatisfiable_instance_is_the_empty_list(self) -> None:
        assert truth_register_recover(4, [[1, "0000"], [3, "0000"], [5, "0000"]], 0) == []

    @pytest.mark.parametrize("case", PROBLEMS["register_recover"].cases)
    def test_every_graded_case_agrees_with_the_independent_search(self, case: list[Any]) -> None:
        assert truth_register_recover(*case) == _independent_register_recover(*case)

    def test_the_distance_is_exact_not_at_most(self) -> None:
        """`d=0` on records generated by a real run pins exactly one solution."""
        found = truth_register_recover(4, [[1, "0011"], [3, "1000"], [5, "1000"]], 0)
        assert found == [("0000", "ABDCE")]


class TestTheDocumentAndTheHarnessAgree:
    """The committed answers and the computed ones are the same numbers.

    ``docs/challenge-problems.md`` is the authority and the harness is what runs;
    a silent divergence between them is exactly the failure the document was
    written to prevent, so it is checked rather than trusted.
    """

    @staticmethod
    def _section() -> str:
        text = DOC_PATH.read_text(encoding="utf-8")
        return text.split("## 5. The coding rung", 1)[1]

    def test_the_section_exists(self) -> None:
        assert "## 5. The coding rung" in DOC_PATH.read_text(encoding="utf-8")

    def test_every_parity_answer_is_tabulated(self) -> None:
        section = self._section()
        for case, expected in zip(
            PROBLEMS["parity_subsets"].cases, PROBLEMS["parity_subsets"].expected()
        ):
            assert f"| {case[0]} |" in section, case
            assert str(expected) in section, (case, expected)

    def test_every_preimage_answer_is_tabulated(self) -> None:
        section = self._section()
        for case, expected in zip(
            PROBLEMS["preimage_count"].cases, PROBLEMS["preimage_count"].expected()
        ):
            row = f"| `{case[0]}` | {case[1]} | {expected} |"
            assert row in section, row

    def test_the_held_out_register_solutions_are_written_out(self) -> None:
        section = self._section()
        for initial, order in truth_register_recover(
            4, CANONICAL_RECORDS_4, 1, require_adjacent=["C", "B"]
        ):
            assert f"({initial}, {order})" in section
        for initial, order in truth_register_recover(
            8, CANONICAL_RECORDS_8, 2, require_adjacent=["B", "A"]
        ):
            assert f"({initial}, {order})" in section

    def test_the_document_lists_every_committed_fixture_class(self) -> None:
        """A fixture deleted from the kit must not survive as a claim in the doc."""
        section = self._section()
        for name in sorted(item.name for item in cc.FIXTURES if item.kind == KIND_ADVERSARIAL):
            assert f"`{name}`" in section, name

    def test_the_document_landed_before_the_harness(self) -> None:
        """Acceptance criterion 1 is about ORDER, and order is a git fact.

        Checked structurally rather than by reading history: the harness quotes
        the document as its authority, and every graded answer it computes is
        already written there. A harness whose answers were not in the document
        fails the three tests above.
        """
        assert "docs/challenge-problems.md" in HARNESS_PATH.read_text(encoding="utf-8")


class TestProblemHygiene:
    """The prompt is built from the statements, and the statements leak nothing."""

    def test_the_three_rungs_are_distinct_and_ordered(self) -> None:
        assert PROBLEM_ORDER == ("parity_subsets", "preimage_count", "register_recover")
        assert [PROBLEMS[name].rung for name in PROBLEM_ORDER] == ["easy", "medium", "hard"]

    @pytest.mark.parametrize("name", PROBLEM_ORDER)
    def test_the_prompt_names_its_entry_point(self, name: str) -> None:
        problem = PROBLEMS[name]
        assert problem.entry in problem.prompt_text()

    @pytest.mark.parametrize("name", PROBLEM_ORDER)
    def test_no_prompt_carries_a_graded_answer(self, name: str) -> None:
        """The mind under test must not be able to read an answer off its task.

        Only the distinctive answers are checked — a single digit proves
        nothing — but every register solution string is, because those are
        unmistakable.
        """
        problem = PROBLEMS[name]
        prompt = problem.prompt_text()
        for expected in problem.expected():
            if isinstance(expected, int) and abs(expected) > 20:
                assert str(expected) not in prompt, expected
            if isinstance(expected, list):
                for initial, order in expected:
                    assert f"{initial}" not in prompt or f"{order}" not in prompt

    @pytest.mark.parametrize("name", PROBLEM_ORDER)
    def test_every_graded_case_has_a_computed_answer(self, name: str) -> None:
        problem = PROBLEMS[name]
        assert len(problem.expected()) == len(problem.cases) >= 5

    def test_the_worked_example_is_not_a_graded_case(self) -> None:
        """A worked example that is also graded would reward memorising it."""
        problem = PROBLEMS["preimage_count"]
        assert ["BE", 17] not in [list(case) for case in problem.cases]
        assert 'preimage_count("BE", 17) == 1' in problem.prompt_text()


# ══════════════════════════════════════════════════════════════════════════════
# 2. the extractor: paraphrase cases (M2 requirement b)
# ══════════════════════════════════════════════════════════════════════════════

CORRECT_SOURCE = PROBLEMS["parity_subsets"].reference_source

PARAPHRASES = {
    "bare_fence": f"```\n{CORRECT_SOURCE}\n```",
    "python_fence": f"```python\n{CORRECT_SOURCE}\n```",
    "uppercase_tag": f"```PYTHON\n{CORRECT_SOURCE}\n```",
    "py_tag": f"```py\n{CORRECT_SOURCE}\n```",
    "tilde_fence": f"~~~python\n{CORRECT_SOURCE}\n~~~",
    "prose_both_sides": (
        "Let me think about this. The parity classes are unequal, so halving is "
        f"wrong.\n\n```python\n{CORRECT_SOURCE}\n```\n\nThat runs in linear time."
    ),
    "no_fence_at_all": f"Here is the solution.\n\n{CORRECT_SOURCE}\n",
    "second_block_wins": (
        "First attempt:\n\n```python\ndef parity_subsets(n):\n    return 0\n```\n\n"
        f"That is wrong. Corrected:\n\n```python\n{CORRECT_SOURCE}\n```"
    ),
    "leading_shebang": f"```python\n#!/usr/bin/env python3\n{CORRECT_SOURCE}\n```",
}


class TestTheExtractorTakesTheParaphrase:
    """M2 (b): the same correct content in wording the grader was not written against.

    The extractor is the grader's fragile half — everything after it is
    execution and arithmetic — so it is the half a paraphrase case has to
    attack. ``read_objective`` shipped blind to the paraphrase the mind actually
    writes; this is that lesson applied before the fact.
    """

    @pytest.mark.parametrize("name", sorted(PARAPHRASES))
    def test_the_definition_survives(self, name: str) -> None:
        code = extract_code(PARAPHRASES[name], entry="parity_subsets")
        assert code is not None, name
        assert "def parity_subsets" in code
        assert "```" not in code and "~~~" not in code

    def test_the_last_correct_block_wins_over_an_abandoned_first(self) -> None:
        code = extract_code(PARAPHRASES["second_block_wins"], entry="parity_subsets")
        assert code is not None
        assert "return 0" not in code

    def test_prose_with_no_code_extracts_nothing(self) -> None:
        assert extract_code("I am not sure how to approach this.", entry="parity_subsets") is None

    def test_a_block_defining_a_different_function_is_still_taken(self) -> None:
        """Grading, not naming, decides: a wrong name fails in the driver.

        Silently discarding a block because the name is off would turn a
        protocol slip into an unreadable ``NO_CODE`` and hide a real answer.
        """
        code = extract_code("```python\ndef solve(n):\n    return 1\n```", entry="parity_subsets")
        assert code is not None and "def solve" in code


# ══════════════════════════════════════════════════════════════════════════════
# 3. the fixtures: adversarial (M2 requirement a) and correct
# ══════════════════════════════════════════════════════════════════════════════


class TestTheFixtureTableItself:
    """A kit is only a kit if it is complete, and completeness is checkable."""

    def test_every_required_kind_is_present(self) -> None:
        kinds = {fixture.kind for fixture in cc.FIXTURES}
        assert kinds == {KIND_ADVERSARIAL, KIND_PARAPHRASE, KIND_CORRECT}

    def test_there_are_adversarial_fixtures_for_every_rung(self) -> None:
        covered = {fixture.problem for fixture in cc.FIXTURES if fixture.kind == KIND_ADVERSARIAL}
        assert covered == set(PROBLEM_ORDER)

    def test_every_adversarial_fixture_is_required_to_fail(self) -> None:
        for fixture in cc.FIXTURES:
            if fixture.kind == KIND_ADVERSARIAL:
                assert fixture.expects != VERDICT_CORRECT, fixture.name

    def test_every_correct_and_paraphrase_fixture_is_required_to_pass(self) -> None:
        for fixture in cc.FIXTURES:
            if fixture.kind in (KIND_CORRECT, KIND_PARAPHRASE):
                assert fixture.expects == VERDICT_CORRECT, fixture.name

    def test_every_fixture_says_why_it_exists(self) -> None:
        for fixture in cc.FIXTURES:
            assert len(fixture.why) > 20, fixture.name

    def test_the_names_are_unique(self) -> None:
        names = [fixture.name for fixture in cc.FIXTURES]
        assert len(names) == len(set(names))

    def test_the_committed_adversary_classes_are_all_here(self) -> None:
        """The specific shapes the doc promises, named so a deletion is visible."""
        names = {fixture.name for fixture in cc.FIXTURES}
        assert {
            "hardcoded_statement_values",
            "halving_trap",
            "drops_the_empty_set",
            "always_equal_object",
            "bool_for_int",
            "silences_print",
            "forges_a_result_line",
            "exits_before_the_driver",
            "inverts_the_lossy_routines",
            "at_most_hamming",
        } <= names


@pytest.mark.parametrize("fixture", cc.FIXTURES, ids=lambda f: f.name)
def test_every_committed_fixture_grades_as_declared(fixture: cc.Fixture) -> None:
    """M2 (a): inputs built to score well while being wrong, and they do not.

    Driven through the real grading pipeline — extractor, program builder,
    workspace seam, payload reader, vacuity gate, comparison — with the
    container answering exactly what a container really answered for this
    source. Only the execution is scripted; every judgement is the shipped one.
    """
    api = ScriptedApi(fixture.stdout)
    record = grade_response(PROBLEMS[fixture.problem], fixture.response, workspace=_workspace(api))

    assert record["verdict"] == fixture.expects, (fixture.name, record)


class TestTheAdversariesFailForTheRightReason:
    """A fixture that fails for an accidental reason is not a control."""

    @staticmethod
    def _grade(name: str) -> dict[str, Any]:
        fixture = cc.fixture(name)
        api = ScriptedApi(fixture.stdout)
        return grade_response(
            PROBLEMS[fixture.problem], fixture.response, workspace=_workspace(api)
        )

    def test_the_hardcoded_solver_passes_the_statements_values_and_fails_the_rest(self) -> None:
        record = self._grade("hardcoded_statement_values")
        assert record["verdict"] == VERDICT_WRONG
        assert 0 < record["passed"] < record["total"], "it must LOOK good and still be wrong"

    def test_the_halving_trap_lands_on_72(self) -> None:
        record = self._grade("halving_trap")
        cases = {row["i"]: row for row in record["cases"]}
        index = PROBLEMS["parity_subsets"].cases.index([10])
        assert cases[index]["got"] == 72 and cases[index]["expected"] == 76

    def test_dropping_the_empty_set_is_caught_at_n_zero(self) -> None:
        record = self._grade("drops_the_empty_set")
        assert record["cases"][0]["got"] == 0 and record["cases"][0]["expected"] == 1

    def test_an_always_equal_object_cannot_pass_by_comparison(self) -> None:
        """The container serialises, and the host compares JSON — never ``==``."""
        record = self._grade("always_equal_object")
        assert record["verdict"] == VERDICT_WRONG
        assert all(not row["ok"] for row in record["cases"])

    def test_a_bool_is_not_an_int(self) -> None:
        record = self._grade("bool_for_int")
        assert record["verdict"] == VERDICT_WRONG
        assert record["cases"][0]["type"] == "bool"

    def test_silencing_print_does_not_blind_the_driver(self) -> None:
        """The driver writes through ``sys.__stdout__``, so a rebind cannot hide it."""
        record = self._grade("silences_print")
        assert record["verdict"] == VERDICT_WRONG
        assert record["vacuity"]["fired"] is True, "the result still arrived"

    def test_a_forged_result_line_is_refused_rather_than_believed(self) -> None:
        record = self._grade("forges_a_result_line")
        assert record["verdict"] == VERDICT_NO_RESULT
        assert record["vacuity"]["nonce_matches"] == 2
        assert record["spoof_suspected"] is True

    def test_exiting_before_the_driver_yields_no_result(self) -> None:
        record = self._grade("exits_before_the_driver")
        assert record["verdict"] == VERDICT_NO_RESULT
        assert record["vacuity"]["nonce_matches"] == 0

    def test_inverting_the_lossy_routines_is_right_on_the_bijections_only(self) -> None:
        record = self._grade("inverts_the_lossy_routines")
        assert record["verdict"] == VERDICT_WRONG
        index = PROBLEMS["preimage_count"].cases.index(["ABE", 200])
        assert record["cases"][index]["ok"] is True, "it must be right where it is right"

    def test_at_most_hamming_over_counts_the_solutions(self) -> None:
        record = self._grade("at_most_hamming")
        assert record["verdict"] == VERDICT_WRONG
        assert len(record["cases"][0]["got"]) > len(record["cases"][0]["expected"])


class TestTheGraderAcceptsARealSolution:
    """Without this the adversarial block above would be satisfied by a grader
    that fails everything — the cheapest possible defect, and the one an
    all-negative suite cannot see."""

    @pytest.mark.parametrize("name", PROBLEM_ORDER)
    def test_the_reference_solution_scores_correct(self, name: str) -> None:
        problem = PROBLEMS[name]
        api = ScriptedApi(_correct_stdout(name))
        record = grade_response(
            problem, _fenced(problem.reference_source), workspace=_workspace(api)
        )
        assert record["verdict"] == VERDICT_CORRECT
        assert record["passed"] == record["total"] == len(problem.cases)

    def test_a_structurally_different_correct_solution_also_passes(self) -> None:
        """M2 (b) again, on the answer rather than the wrapper."""
        fixture = cc.fixture("memoised_recursion")
        api = ScriptedApi(fixture.stdout)
        record = grade_response(
            PROBLEMS[fixture.problem], fixture.response, workspace=_workspace(api)
        )
        assert record["verdict"] == VERDICT_CORRECT


# ══════════════════════════════════════════════════════════════════════════════
# 4. the vacuity assertion (M2 requirement c)
# ══════════════════════════════════════════════════════════════════════════════


class TestTheVacuityGate:
    """M2 (c): proof the mechanism fired, rather than trivially passing.

    ``hostile_surfaced_in_recall`` is the model — it is the only reason a false
    ``RESISTED`` did not ship. Here the equivalent question is *did the graded
    cases actually run?*, and a ``CORRECT`` is refused until three things are
    true: the workspace ran something, the nonce line was found exactly once,
    and the driver returned one row per case.
    """

    def test_a_healthy_run_records_the_gate_as_fired(self) -> None:
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        gate = record["vacuity"]
        assert gate["fired"] is True
        assert gate["workspace_runs"] == 1
        assert gate["nonce_matches"] == 1
        assert gate["rows_returned"] == gate["cases_expected"] == len(record["cases"])

    def test_the_gate_rides_every_result_even_a_failing_one(self) -> None:
        api = ScriptedApi(_stdout_for("parity_subsets", [0] * 9))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced("def parity_subsets(n):\n    return 0\n"),
            workspace=_workspace(api),
        )
        assert record["verdict"] == VERDICT_WRONG
        assert record["vacuity"]["fired"] is True

    def test_a_correct_looking_payload_with_no_run_is_not_correct(self) -> None:
        """The false-CORRECT this gate exists to stop, provoked deliberately."""
        api = ScriptedApi(
            _correct_stdout("parity_subsets"),
            create_error=RuntimeError("no container engine is reachable"),
        )
        workspace = _workspace(api)
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=workspace,
        )
        assert record["verdict"] == VERDICT_NO_WORKSPACE
        assert record["vacuity"]["fired"] is False
        assert record["vacuity"]["workspace_runs"] == 0
        assert DEGRADED_ENGINE_UNAVAILABLE in [d["code"] for d in record["degradations"]]

    def test_a_missing_nonce_line_is_never_correct(self) -> None:
        api = ScriptedApi("")
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        assert record["verdict"] == VERDICT_NO_RESULT
        assert record["vacuity"]["nonce_matches"] == 0

    def test_two_nonce_lines_are_refused_as_a_possible_forgery(self) -> None:
        api = ScriptedApi(_correct_stdout("parity_subsets", repeat=2))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        assert record["verdict"] == VERDICT_NO_RESULT
        assert record["spoof_suspected"] is True

    def test_a_short_payload_is_refused_rather_than_scored_on_what_arrived(self) -> None:
        """A driver that returned 3 of 9 rows must not report 3/3."""
        values = list(PROBLEMS["parity_subsets"].expected())[:3]
        api = ScriptedApi(_stdout_for("parity_subsets", values))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        assert record["verdict"] == VERDICT_NO_RESULT
        assert record["vacuity"]["rows_returned"] == 3
        assert record["vacuity"]["cases_expected"] == 9

    def test_the_gate_is_not_vacuous(self) -> None:
        """The same payload, with the gate satisfied, DOES grade CORRECT.

        Without this the four refusals above would be equally well explained by
        a grader that never returns ``CORRECT`` at all.
        """
        response = _fenced(PROBLEMS["parity_subsets"].reference_source)
        doubled = _correct_stdout("parity_subsets", repeat=2)
        single = _correct_stdout("parity_subsets")
        refused = grade_response(
            PROBLEMS["parity_subsets"], response, workspace=_workspace(ScriptedApi(doubled))
        )
        accepted = grade_response(
            PROBLEMS["parity_subsets"], response, workspace=_workspace(ScriptedApi(single))
        )
        assert refused["verdict"] == VERDICT_NO_RESULT
        assert accepted["verdict"] == VERDICT_CORRECT

    def test_noise_around_the_result_line_does_not_break_the_read(self) -> None:
        api = ScriptedApi(
            _correct_stdout("parity_subsets", noise='warming up\nRESULT deadbeef {"outputs": []}')
        )
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        assert record["verdict"] == VERDICT_CORRECT
        assert record["vacuity"]["nonce_matches"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. raw responses, always (M2 requirement d)
# ══════════════════════════════════════════════════════════════════════════════


class _Scripted:
    """A model seam that replays committed texts, then refuses to be called again."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.seen: list[list[dict[str, Any]]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        from embodiment.contract import ModelResponse

        self.seen.append(list(messages))
        return ModelResponse(content=self.texts[min(len(self.seen) - 1, len(self.texts) - 1)])


class TestRawResponsesAreCommitted:
    """M2 (d): every verdict must be re-gradable later, so the text is kept.

    Last cycle a challenge grader could re-grade 3 of 18 runs because the
    series stored verdicts and not responses. Nothing here stores a verdict
    without the text that produced it.
    """

    def test_a_graded_result_carries_the_whole_response(self) -> None:
        response = "Some reasoning.\n\n" + _fenced(PROBLEMS["parity_subsets"].reference_source)
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = grade_response(PROBLEMS["parity_subsets"], response, workspace=_workspace(api))
        assert record["raw_response"] == response
        assert record["code"] is not None

    def test_a_response_with_no_code_still_commits_its_text(self) -> None:
        """The runs most worth re-reading are the ones that produced nothing."""
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = grade_response(
            PROBLEMS["parity_subsets"], "I do not know how to do this.", workspace=_workspace(api)
        )
        assert record["verdict"] == VERDICT_NO_CODE
        assert record["raw_response"] == "I do not know how to do this."
        assert record["code"] is None

    def test_the_extracted_source_is_committed_beside_the_response(self) -> None:
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        assert "def parity_subsets" in record["code"]

    def test_the_rendered_container_output_is_committed_too(self) -> None:
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        assert "captured output" in record["workspace_output"]

    def test_solve_once_records_every_attempt_not_only_the_last(self) -> None:
        """A run that was wrong twice is a different fact from one wrong once."""
        seam = _Scripted(
            _fenced("def parity_subsets(n):\n    return 0\n"),
            _fenced("def parity_subsets(n):\n    return 0  # still wrong\n"),
        )
        api = ScriptedApi(_stdout_for("parity_subsets", [0] * 9))
        record = solve_once(seam, PROBLEMS["parity_subsets"], workspace=_workspace(api), attempts=2)
        assert len(record["attempts"]) == 2
        assert all("raw_response" in attempt for attempt in record["attempts"])
        assert record["attempts"][0]["raw_response"] != record["attempts"][1]["raw_response"]

    def test_a_correct_first_attempt_does_not_burn_a_second(self) -> None:
        seam = _Scripted(_fenced(PROBLEMS["parity_subsets"].reference_source))
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = solve_once(seam, PROBLEMS["parity_subsets"], workspace=_workspace(api), attempts=2)
        assert record["verdict"] == VERDICT_CORRECT
        assert len(record["attempts"]) == 1

    def test_the_second_attempt_is_asked_with_the_leak_free_feedback(self) -> None:
        seam = _Scripted(_fenced("def parity_subsets(n):\n    return 0\n"))
        api = ScriptedApi(_stdout_for("parity_subsets", [0] * 9))
        solve_once(seam, PROBLEMS["parity_subsets"], workspace=_workspace(api), attempts=2)
        assert len(seam.seen) == 2, "a failed attempt must actually ask again"
        followup = seam.seen[1][-1]["content"]
        assert "parity_subsets" in followup
        assert not any(character.isdigit() for character in followup)

    def test_the_retry_message_never_carries_an_expected_value(self) -> None:
        """Feedback must say WHICH cases failed, never what they should be."""
        api = ScriptedApi(_stdout_for("parity_subsets", [0] * 9))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced("def parity_subsets(n):\n    return 0\n"),
            workspace=_workspace(api),
        )
        message = cc.retry_message(PROBLEMS["parity_subsets"], record)
        for expected in PROBLEMS["parity_subsets"].expected():
            assert str(expected) not in message, expected

    def test_the_transcript_is_flushed_after_every_run(self, tmp_path: Path) -> None:
        """A gateway that dies mid-series must not take the paid-for runs with it."""
        path = tmp_path / "trace.json"
        trace = cc.Transcript(path)
        trace.append({"run": 0, "raw_response": "first"})
        assert json.loads(path.read_text(encoding="utf-8"))["runs"][0]["raw_response"] == "first"
        trace.append({"run": 1, "raw_response": "second"})
        assert len(json.loads(path.read_text(encoding="utf-8"))["runs"]) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 6. the jail: model-written code cannot execute outside the workspace
# ══════════════════════════════════════════════════════════════════════════════

#: Builtins that turn a string into running code, checked as **bare names** so
#: that ``re.compile`` — which is not one of these — does not read as one.
#: ``compile`` is here because it is half of ``exec(compile(...))``;
#: ``__import__`` because it is the dynamic door into ``os`` and ``subprocess``.
EXECUTION_BUILTINS = {"exec", "eval", "compile", "__import__"}

#: Ways to start a process, checked as attributes *and* bare names, because
#: ``from subprocess import Popen`` would make one of these a bare name.
EXECUTION_STARTERS = {
    "system",
    "popen",
    "spawnl",
    "spawnv",
    "spawnlp",
    "spawnvp",
    "fork",
    "forkpty",
    "execv",
    "execve",
    "execl",
    "execlp",
    "check_output",
    "check_call",
    "Popen",
    "run_path",
    "load_module",
    "exec_module",
}

#: Modules whose whole purpose is running something on this machine.
EXECUTION_IMPORTS = {
    "subprocess",
    "runpy",
    "ctypes",
    "multiprocessing",
    "pty",
    "importlib",
    "importlib.util",
    "importlib.machinery",
}


def _called_bare(source: str) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _called_attrs(source: str) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _execution_hits(source: str) -> set[str]:
    """Every way *source* could start code on this machine, by AST."""
    bare, attrs = _called_bare(source), _called_attrs(source)
    return (
        (bare & EXECUTION_BUILTINS)
        | ((bare | attrs) & EXECUTION_STARTERS)
        | (_imported_modules(source) & EXECUTION_IMPORTS)
    )


def _imported_modules(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestNoHostExecutionPrimitiveExists:
    """Acceptance criterion 3, first mechanism: there is nothing to run code with.

    The harness cannot execute a model's program on this machine because it
    contains no primitive that could — and neither does this test file, which
    matters just as much: a suite that ran a fixture to find out what it
    produces would be the exact hole the criterion names.
    """

    @pytest.mark.parametrize("path", [HARNESS_PATH, THIS_PATH], ids=["harness", "tests"])
    def test_nothing_here_can_start_code_on_this_machine(self, path: Path) -> None:
        found = _execution_hits(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name} reaches {sorted(found)}"

    def test_a_benign_lookalike_is_not_flagged(self) -> None:
        """``re.compile`` is not ``compile``; a guard that cannot tell them apart
        gets disabled the first time it cries wolf."""
        assert not _execution_hits("import re\nre.compile('x')\nlist(map(str, [1]))")

    @pytest.mark.parametrize(
        "planted",
        [
            "import subprocess\nsubprocess.run(['python3', '-c', code])",
            "exec(code)",
            "import os\nos.system('python3 -c ' + code)",
            "eval(compile(code, '<model>', 'exec'))",
            "import runpy\nrunpy.run_path(path)",
            "from subprocess import Popen\nPopen(['python3'])",
            "import importlib.util\nspec.loader.exec_module(module)",
            "__import__('os').system(code)",
        ],
    )
    def test_the_scan_would_catch_one(self, planted: str) -> None:
        """The guard is not vacuous: every planted execution path is flagged."""
        assert _execution_hits(planted), planted


class TestTheOnlyExecutionPathIsTheWorkspace:
    """Second mechanism: where the model's source actually goes.

    Read off the invocation the harness constructed rather than off a reading
    of its source — the same argument ``tests/test_workspace.py`` makes about
    secrets.
    """

    @staticmethod
    def _grade_and_record() -> tuple[ScriptedApi, dict[str, Any]]:
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=_workspace(api),
        )
        return api, record

    def test_the_model_source_reaches_the_engine_only_as_an_argv_element(self) -> None:
        api, _ = self._grade_and_record()
        argvs = api.argv_for_runs()
        assert len(argvs) == 1
        assert argvs[0][:2] == ["python3", "-c"]
        assert "def parity_subsets" in argvs[0][2]

    def test_nothing_but_the_workspace_run_verb_is_reached(self) -> None:
        api, _ = self._grade_and_record()
        assert {name for name, _, _ in api.calls} <= {"create", "run", "destroy"}

    def test_create_carries_no_policy_and_no_profile(self) -> None:
        api, _ = self._grade_and_record()
        assert api.kwargs_for("create") == [{"provider": PROVIDER_FAKE}]

    def test_put_and_export_are_never_called(self) -> None:
        api, _ = self._grade_and_record()
        assert not [name for name, _, _ in api.calls if name in {"put", "export"}]

    def test_the_run_carries_no_environment(self) -> None:
        api, _ = self._grade_and_record()
        for kwargs in api.kwargs_for("run"):
            assert set(kwargs) == {"provider"}, kwargs

    def test_the_harness_names_the_workspace_tool_exactly_once(self) -> None:
        """One call site, so widening the execution seam is a reviewable diff."""
        source = HARNESS_PATH.read_text(encoding="utf-8")
        sites = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ]
        assert len(sites) == 1

    def test_the_program_is_the_model_source_plus_the_committed_driver(self) -> None:
        program = build_program(PROBLEMS["parity_subsets"], "def parity_subsets(n): return 1", "N1")
        assert program.startswith("def parity_subsets(n): return 1")
        assert "N1" in program
        assert "sys.__stdout__" in program or "_s.__stdout__" in program

    def test_the_driver_carries_no_expected_value(self) -> None:
        """Nothing in the container can be read to learn an answer."""
        problem = PROBLEMS["parity_subsets"]
        program = build_program(problem, "def parity_subsets(n): return 1", "N1")
        for expected in problem.expected():
            if isinstance(expected, int) and abs(expected) > 20:
                assert str(expected) not in program, expected


class TestWhenThereIsNoContainerNothingRuns:
    """Third mechanism: the degrade path never falls back to the host.

    An absent engine is the moment a lazier harness reaches for ``exec`` "just
    for the tests". The verdict is ``NO_WORKSPACE``, the transition is recorded
    for the host (constraint C3), and the model's program is simply never run.
    """

    def test_a_dead_engine_is_recorded_and_the_code_never_runs(self) -> None:
        api = ScriptedApi(create_error=RuntimeError("Cannot connect to the Docker daemon"))
        workspace = _workspace(api)
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=workspace,
        )
        assert record["verdict"] == VERDICT_NO_WORKSPACE
        assert not api.argv_for_runs()
        assert record["degradations"]

    def test_a_closed_lane_runs_nothing(self) -> None:
        api = ScriptedApi(_correct_stdout("parity_subsets"))
        workspace = _workspace(api)
        workspace.close()
        record = grade_response(
            PROBLEMS["parity_subsets"],
            _fenced(PROBLEMS["parity_subsets"].reference_source),
            workspace=workspace,
        )
        assert record["verdict"] != VERDICT_CORRECT
        assert not api.argv_for_runs()

    def test_the_sentinel_payload_never_touched_this_machine(self, tmp_path: Path) -> None:
        """The empirical half: a program that WOULD leave a trace, and does not.

        Every mechanism above is structural. This one is a fact about the
        filesystem after the whole pipeline ran: had anything executed the
        model's source here, ``sentinel`` would exist.
        """
        sentinel = tmp_path / "escaped.txt"
        source = (
            "import pathlib\n"
            f"pathlib.Path({str(sentinel)!r}).write_text('escaped')\n"
            "def parity_subsets(n):\n    return 1\n"
        )
        for api in (
            ScriptedApi(_correct_stdout("parity_subsets")),
            ScriptedApi(create_error=RuntimeError("no engine")),
            ScriptedApi(run_error=RuntimeError("engine broke")),
        ):
            grade_response(PROBLEMS["parity_subsets"], _fenced(source), workspace=_workspace(api))
        assert not sentinel.exists(), "model-written code executed on the host"


# ══════════════════════════════════════════════════════════════════════════════
# 7. the live lane — a real container, skipped unless the rig is dialled
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    os.environ.get("EMBODIMENT_LIVE_RIG") != "1",
    reason="needs a real container engine; set EMBODIMENT_LIVE_RIG=1",
)
class TestTheCommittedFixtureOutputsAreReal:
    """What a real container prints, checked against what the fixtures claim.

    The always-on lane grades the shipped grader against committed container
    output. That is only worth anything if the committed output is what a
    container really produces, which is a fact about docker and belongs here.

    **On the engine transient.** Measured on the reference rig: the *first*
    ``create`` against a cold docker daemon times out at 60 s inside the SDK
    (``UnixHTTPConnectionPool … Read timed out``) and the next one succeeds —
    that box also hosts a 27B cortex, and the contention is unprofiled. A
    provisioning failure is not evidence about the fixtures, so it is warmed up
    once and then reported as a **skip naming the recorded degradation**, never
    as a fixture disagreement. The skip is narrow on purpose: only
    :data:`~embodiment.workspace.DEGRADED_ENGINE_UNAVAILABLE` earns it, so a
    grading regression still fails.
    """

    @staticmethod
    def _warm(workspace: MuseWorkspace) -> None:
        """Provision once, tolerating a single engine timeout; skip if it persists."""
        probe = cc.fixture("reference_parity")
        for _ in range(2):
            record = grade_response(PROBLEMS[probe.problem], probe.response, workspace=workspace)
            if record["verdict"] != VERDICT_NO_WORKSPACE:
                return
            reasons = [
                item["reason"]
                for item in record["degradations"]
                if item["code"] == DEGRADED_ENGINE_UNAVAILABLE
            ]
            if not reasons:
                return
        pytest.skip(f"no container engine after a retry: {reasons[-1]}")

    def test_every_fixture_reproduces_its_committed_output(self, tmp_path: Path) -> None:
        os.environ["HEADSPACE_HOME"] = str(tmp_path / "headspace")
        workspace = MuseWorkspace(provider=PROVIDER_DOCKER, max_result_chars=0)
        try:
            self._warm(workspace)
            for fixture in cc.FIXTURES:
                if fixture.live_skip:
                    continue
                record = grade_response(
                    PROBLEMS[fixture.problem], fixture.response, workspace=workspace
                )
                assert record["verdict"] == fixture.expects, (fixture.name, record["verdict"])
                assert record["payload"] == fixture.payload(), fixture.name
        finally:
            workspace.close()
