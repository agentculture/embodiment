"""Tests for ``examples/proof.py``'s delivery reporting (plan task t18, part B).

``proof.py`` produced the 2-of-7 delivery baseline in
``docs/live-test-results/proof.md`` while reporting only
``ThreadedMuseRunner.snapshot()["counts"]`` — so the published number could not
be asked the one question t3's kind-aware delivery exists to answer: *which kind
of counsel was discarded?* It also wrote no configuration preamble at all, and
its temperature was a literal inside ``gateway``.

These tests cover the reporting added to close that, and they pin the finding
that matters most about it: **a degradation code that never fires reports
nothing, not a zero.** A fold that emitted ``0`` for an unfired code would make
"did not happen this run" indistinguishable from a number somebody measured.

They also carry the guard that caught embodiment#18. Two of the runner's nine
codes — ``DROPPED_COMPILATION_STARVED`` and ``DROPPED_COUNSEL_DISPLACED`` — were
declared, exported and had **no producer anywhere in the package**, so a host
branching exhaustively over ``RUNNER_CODES`` got two arms nothing could reach.
Both now have production emit sites (``muse_runner``'s background-compilation
work class), and the pin below was inverted rather than deleted: it now asserts
that *every* code in ``RUNNER_CODES`` has a producer, which is the check that
would have failed the original merge.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

import embodiment
from embodiment import muse_runner
from embodiment.muse import MuseDegradation
from examples import delivery_series, proof

#: ``{code: constant name}`` for the runner's whole declared vocabulary.
_CONSTANT_NAMES = {
    getattr(muse_runner, name): name
    for name in muse_runner.__all__
    if name.startswith(("DEGRADED_", "DROPPED_"))
}


def _record_call_arguments() -> dict[str, list[str]]:
    """``{identifier: [where]}`` for every name passed to ``_record``/``_degrade``.

    A real AST walk over the package, not a text scan: only a **call argument**
    counts, so a constant's declaration, its ``__all__`` entry and its place in
    the ``RUNNER_CODES`` tuple are never mistaken for a producer. That
    distinction is the whole point — embodiment#18 was two codes that appeared
    in all three of those places and in no call site.
    """
    root = Path(embodiment.__file__).resolve().parent
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called not in {"_record", "_degrade"}:
                continue
            for argument in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(argument, ast.Name):
                    identifier = argument.id
                elif isinstance(argument, ast.Attribute):
                    identifier = argument.attr
                else:
                    continue
                found.setdefault(identifier, []).append(f"{path.name}:{node.lineno}")
    return found


class TestFoldCodes:
    """The ledger folded by code, with absence preserved."""

    def test_no_muse_state_folds_to_nothing(self) -> None:
        assert proof._fold_codes(None) == {}

    def test_a_run_with_no_degradations_folds_to_nothing(self) -> None:
        assert proof._fold_codes({"degradations": []}) == {}

    def test_codes_are_tallied_by_name(self) -> None:
        state: dict[str, Any] = {
            "degradations": [
                MuseDegradation(code=muse_runner.DROPPED_STALE, reason="a"),
                MuseDegradation(code=muse_runner.DROPPED_STALE, reason="b"),
                MuseDegradation(code=muse_runner.DROPPED_LATE, reason="c"),
            ]
        }
        assert proof._fold_codes(state) == {
            muse_runner.DROPPED_STALE: 2,
            muse_runner.DROPPED_LATE: 1,
        }

    def test_a_code_that_never_fired_is_absent_rather_than_zero(self) -> None:
        """Absence and zero are different claims, and only one of them is true.

        A code that did not fire in a run must not appear in the fold at all.
        Reporting ``0`` would read as a measurement of that lane when nothing
        measured it — and, before embodiment#18 was fixed, would have read as
        "this run did not trip it" for two codes nothing could trip.
        """
        folded = proof._fold_codes(
            {"degradations": [MuseDegradation(code=muse_runner.DROPPED_STALE, reason="a")]}
        )
        assert muse_runner.DROPPED_COMPILATION_STARVED not in folded
        assert muse_runner.DROPPED_COUNSEL_DISPLACED not in folded


class TestEveryRunnerCodeHasAProducer:
    """Declared vocabulary must be REACHABLE vocabulary (embodiment#18).

    This class was ``TestUnproducedCodes``. It asserted that
    ``DROPPED_COMPILATION_STARVED`` and ``DROPPED_COUNSEL_DISPLACED`` were never
    passed to ``_record`` / ``_degrade``, so that wiring a producer for either
    would break it and force the results documents to be corrected rather than
    left to go stale. A producer was wired (``muse_runner``'s background
    compilation work class), it broke, and this is that correction — inverted
    into the stronger claim, which is the one that would have failed the merge
    that created the defect: **every** code in ``RUNNER_CODES`` has at least one
    call site.

    Reachability is not the same as coverage. ``tests/test_ledger.py``'s
    PROVOKERS table already required every code to appear in *a record*; both
    dead codes satisfied it by being recorded directly in a test. This checks
    the other half — that production code passes the constant somewhere — and
    task t3 closes the remaining gap by banning provokers that reach a private
    attribute.
    """

    ISSUE_18_CODES = (
        muse_runner.DROPPED_COMPILATION_STARVED,
        muse_runner.DROPPED_COUNSEL_DISPLACED,
    )

    @pytest.mark.parametrize("code", muse_runner.RUNNER_CODES)
    def test_the_code_is_part_of_the_declared_vocabulary(self, code: str) -> None:
        assert code in _CONSTANT_NAMES
        assert code in muse_runner.RUNNER_CODES

    @pytest.mark.parametrize("code", muse_runner.RUNNER_CODES)
    def test_some_module_in_the_package_records_it(self, code: str) -> None:
        """Something in ``embodiment`` passes the constant to ``_record``/``_degrade``."""
        name = _CONSTANT_NAMES[code]
        producers = _record_call_arguments().get(name, [])
        assert producers, (
            f"{name} is declared and exported but nothing in the package records it — "
            "a host branching exhaustively over RUNNER_CODES would get an arm that "
            "cannot fire. Wire an emit site, or drop the constant (embodiment#18)."
        )

    @pytest.mark.parametrize("code", ISSUE_18_CODES)
    def test_the_two_issue_18_codes_are_produced_by_the_runner(self, code: str) -> None:
        """The regression pin, named so a reader can find the history."""
        where = _record_call_arguments()[_CONSTANT_NAMES[code]]
        assert any(location.startswith("muse_runner.py:") for location in where), where


class TestPerKindAttribution:
    """Only ``drain`` attributes a kind. The other drop paths cannot.

    Declared in the pre-registration before the run, so that the gap in the
    per-kind report reads as a known limit of the instrument rather than as an
    excuse constructed afterwards.
    """

    def test_stale_drops_and_deliveries_carry_a_kind(self) -> None:
        source = __import__("inspect").getsource(muse_runner.ThreadedMuseRunner.drain)
        assert "_kind_dropped" in source
        assert "_kind_delivered" in source

    def test_late_and_overflow_drops_do_not(self) -> None:
        inspect = __import__("inspect")
        late = inspect.getsource(muse_runner.ThreadedMuseRunner._drop_late)
        deliver = inspect.getsource(muse_runner.ThreadedMuseRunner._deliver)
        assert "_kind_dropped" not in late
        assert "_kind_dropped" not in deliver


class TestProofConfigConstants:
    """The settings the baseline left as literals are now named and recordable."""

    def test_the_temperature_is_a_named_constant(self) -> None:
        assert proof.DEFAULT_TEMPERATURE == 0.3

    def test_the_muse_controls_are_named(self) -> None:
        assert proof.MUSE_MAX_TURNS == 2
        assert proof.MUSE_MAX_TOKENS == 1200

    def test_the_staleness_threshold_is_importable_for_the_record(self) -> None:
        assert proof.DEFAULT_STALE_LAG == 5

    def test_the_gateway_sends_the_temperature_it_was_given(self) -> None:
        """It used to accept one and send a hardcoded 0.3."""
        import inspect

        source = inspect.getsource(proof.gateway)
        assert '"temperature": temperature' in source
        assert '"temperature": 0.3' not in source


class TestDeliveryFold:
    """The per-kind fold across runs (``examples/delivery_series.py``)."""

    @staticmethod
    def _report(**counts: int) -> dict[str, Any]:
        base = {
            "insights_delivered": 0,
            "insights_dropped_stale": 0,
            "insights_dropped_late": 0,
            "insights_dropped_overflow": 0,
        }
        base.update(counts)
        return {"muse_counts": base}

    def test_it_reproduces_the_published_baseline_exactly(self) -> None:
        """2 of 7 — the number in proof.md. If the fold cannot restate the
        baseline, it cannot be trusted to restate its successor."""
        folded = delivery_series.fold(
            [
                self._report(
                    insights_delivered=2,
                    insights_dropped_stale=2,
                    insights_dropped_late=3,
                )
            ]
        )
        assert folded["insights_produced"] == 7
        assert folded["delivery_fraction"] == pytest.approx(0.2857, abs=1e-4)
        assert folded["baseline"] == delivery_series.BASELINE_COUNTS

    def test_late_and_overflow_drops_are_reported_unattributed(self) -> None:
        """They cannot carry a kind, so the fold must not pretend they do."""
        folded = delivery_series.fold(
            [
                {
                    "muse_counts": {
                        "insights_delivered": 1,
                        "insights_dropped_stale": 0,
                        "insights_dropped_late": 2,
                        "insights_dropped_overflow": 1,
                    },
                    "muse_kind_delivered": {"step": 1},
                }
            ]
        )
        assert folded["unattributable_drops"] == {"late": 2, "overflow": 1}
        assert folded["kind_delivered"] == {"step": 1}
        assert folded["kind_dropped_stale"] == {}

    def test_counts_sum_across_runs(self) -> None:
        folded = delivery_series.fold(
            [
                self._report(insights_delivered=3, insights_dropped_late=1),
                self._report(
                    insights_delivered=1, insights_dropped_stale=2, insights_dropped_late=1
                ),
            ]
        )
        assert folded["runs"] == 2
        assert folded["counts"]["insights_delivered"] == 4
        assert folded["insights_produced"] == 8

    def test_a_failed_run_is_counted_and_excluded_from_the_totals(self) -> None:
        folded = delivery_series.fold(
            [self._report(insights_delivered=2), {"harness_error": "boom"}]
        )
        assert folded["runs"] == 2
        assert folded["harness_errors"] == 1
        assert folded["counts"]["insights_delivered"] == 2

    def test_no_insights_reports_no_fraction_rather_than_zero(self) -> None:
        """A fraction with an empty denominator is not zero, it is absent."""
        assert delivery_series.fold([self._report()])["delivery_fraction"] is None

    def test_an_unfired_code_stays_absent_from_the_fold(self) -> None:
        folded = delivery_series.fold(
            [
                {
                    "muse_counts": {"insights_delivered": 1},
                    "muse_degradation_codes": {muse_runner.DROPPED_STALE: 1},
                }
            ]
        )
        assert folded["degradation_codes"] == {muse_runner.DROPPED_STALE: 1}
        assert muse_runner.DROPPED_COUNSEL_DISPLACED not in folded["degradation_codes"]
