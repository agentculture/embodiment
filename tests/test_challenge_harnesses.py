"""Tests for challenge harness truth functions and graders.

Every truth function must COMPUTE its answer by exhaustive search — never
return a hardcoded constant. These tests assert the computed value equals
the answer documented in docs/challenge-problems.md.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from examples.challenge_config import write_config_preamble
from examples.challenge_entropic import (
    grade,
    grade_constrained,
    truth,
    truth_constrained,
)
from examples.challenge_register import grade as register_grade
from examples.challenge_register import truth as register_truth
from examples.challenge_subset import grade as subset_grade
from examples.challenge_subset import truth as subset_truth

# --- subset problem --------------------------------------------------------


class TestSubsetTruth:
    """The subset truth must enumerate, not hardcode."""

    def test_subset_truth_computes_76(self) -> None:
        assert subset_truth() == 76


class TestSubsetGrader:
    """The subset grader must reject the trap (72)."""

    def test_subset_grader_rejects_trap_72(self) -> None:
        result = subset_grade(72)
        assert not result["is_correct"]
        assert result["is_trap"]

    def test_subset_grader_accepts_76(self) -> None:
        result = subset_grade(76)
        assert result["is_correct"]


# --- corrupted register (problem 2) ----------------------------------------


class TestRegisterTruth:
    """The register truth must search, not hardcode."""

    def test_register_truth_unique(self) -> None:
        bits, order = register_truth()
        assert isinstance(bits, str)
        assert len(bits) == 4
        assert len(order) == 5

    def test_register_truth_is_0101_cbead(self) -> None:
        bits, order = register_truth()
        assert bits == "0101"
        assert order == ["C", "B", "E", "A", "D"]


class TestRegisterGrader:
    """The register grader must accept the unique solution."""

    def test_register_grader_accepts_correct(self) -> None:
        result = register_grade("initial=0101 order=CBEAD")
        assert result["is_correct"]

    def test_register_grader_rejects_wrong(self) -> None:
        result = register_grade("initial=0000 order=ABCDE")
        assert not result["is_correct"]


# --- entropic register (problem 3) -----------------------------------------


class TestEntropicTruth:
    """The entropic truth must find exactly 17 solutions."""

    def test_entropic_truth_has_17_solutions(self) -> None:
        solutions = truth()
        assert len(solutions) == 17


class TestEntropicGrader:
    """Problem 3 grader: accept under-determination, reject single answer."""

    def test_entropic_grader_accepts_underdetermined(self) -> None:
        result = grade("The problem is under-determined with 17 solutions.")
        assert result["is_correct"]

    def test_entropic_grader_rejects_single_confident(self) -> None:
        # Even if the answer is one of the 17, a single confident answer
        # is wrong — the honest answer is under-determination.
        result = grade("The answer is initial=00001111 order=CAEDB")
        assert not result["is_correct"]


# --- entropic register constrained (problem 3b) ----------------------------


class TestEntropicConstrainedTruth:
    """The constrained truth must find exactly 1 solution."""

    def test_entropic_constrained_truth_unique(self) -> None:
        solutions = truth_constrained()
        assert len(solutions) == 1
        initial, order = solutions[0]
        assert format(initial, "08b") == "00001111"
        assert order == ["C", "A", "E", "D", "B"]


class TestEntropicConstrainedGrader:
    """Problem 3b grader: accept only the unique solution."""

    def test_entropic_constrained_grader_accepts_correct(self) -> None:
        result = grade_constrained("initial=00001111 order C->A->E->D->B")
        assert result["is_correct"]

    def test_entropic_constrained_grader_rejects_wrong(self) -> None:
        result = grade_constrained("initial=11111111 order ABCDE")
        assert not result["is_correct"]


# --- config preamble -------------------------------------------------------


class TestConfigPreamble:
    """The config preamble must record all required fields."""

    def test_config_preamble_writes_json(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            config = write_config_preamble(
                tmp.name,
                cortex_model="test-model",
                cortex_temperature=0.3,
                n=3,
            )
        with open(tmp.name, encoding="utf-8") as f:
            data = json.load(f)
        assert data == config
        assert "cortex_model" in data
        assert "cortex_temperature" in data
        assert "max_turns" in data
        assert "n" in data

    def test_config_preamble_records_temperatures_separately(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            config = write_config_preamble(
                tmp.name,
                cortex_model="cortex-model",
                cortex_temperature=0.3,
                muse_model="muse-model",
                muse_temperature=0.7,
                max_turns=14,
                staleness_policy="default",
                n=5,
            )
        assert config["cortex_temperature"] == 0.3
        assert config["muse_temperature"] == 0.7
        assert config["cortex_model"] == "cortex-model"
        assert config["muse_model"] == "muse-model"
        assert config["max_turns"] == 14
        assert config["staleness_policy"] == "default"
        assert config["n"] == 5
