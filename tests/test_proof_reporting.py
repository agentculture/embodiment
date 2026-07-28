"""Tests for ``examples/proof.py``'s delivery reporting (plan task t18, part B).

``proof.py`` produced the 2-of-7 delivery baseline in
``docs/live-test-results/proof.md`` while reporting only
``ThreadedMuseRunner.snapshot()["counts"]`` — so the published number could not
be asked the one question t3's kind-aware delivery exists to answer: *which kind
of counsel was discarded?* It also wrote no configuration preamble at all, and
its temperature was a literal inside ``gateway``.

These tests cover the reporting added to close that, and they pin the finding
that matters most about it: **a degradation code that never fires reports
nothing, not a zero.** Two of the runner's nine codes have no producer anywhere
in ``embodiment``, and a fold that emitted ``0`` for them would make
"never implemented" indistinguishable from "did not happen this run".
"""

from __future__ import annotations

from typing import Any

import pytest

from embodiment import muse_runner
from embodiment.muse import MuseDegradation
from examples import proof


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

        ``DROPPED_COMPILATION_STARVED`` and ``DROPPED_COUNSEL_DISPLACED`` have no
        producer in ``embodiment`` at all. Reporting ``0`` for them would read as
        "this run did not trip it" when the truth is "nothing can trip it".
        """
        folded = proof._fold_codes(
            {"degradations": [MuseDegradation(code=muse_runner.DROPPED_STALE, reason="a")]}
        )
        assert muse_runner.DROPPED_COMPILATION_STARVED not in folded
        assert muse_runner.DROPPED_COUNSEL_DISPLACED not in folded


class TestUnproducedCodes:
    """The two t3 codes are declared, exported — and never emitted.

    Recorded as a test rather than only as prose so that wiring a producer for
    either one breaks this test and forces the results document to be corrected
    instead of quietly going stale.
    """

    UNPRODUCED = (
        muse_runner.DROPPED_COMPILATION_STARVED,
        muse_runner.DROPPED_COUNSEL_DISPLACED,
    )

    @pytest.mark.parametrize("code", UNPRODUCED)
    def test_the_code_is_part_of_the_declared_vocabulary(self, code: str) -> None:
        assert code in muse_runner.RUNNER_CODES

    @pytest.mark.parametrize("code", UNPRODUCED)
    def test_no_module_in_the_package_records_it(self, code: str) -> None:
        """Nothing calls ``_record``/``_degrade`` with either constant."""
        import inspect
        import pkgutil

        import embodiment

        name = {
            muse_runner.DROPPED_COMPILATION_STARVED: "DROPPED_COMPILATION_STARVED",
            muse_runner.DROPPED_COUNSEL_DISPLACED: "DROPPED_COUNSEL_DISPLACED",
        }[code]
        callers: list[str] = []
        for module in pkgutil.iter_modules(embodiment.__path__):
            source = inspect.getsource(__import__(f"embodiment.{module.name}", fromlist=["_"]))
            for line in source.splitlines():
                stripped = line.strip()
                if name not in stripped and code not in stripped:
                    continue
                # Declaration, export list and the RUNNER_CODES tuple are not
                # producers; a producer passes it to _record or _degrade.
                if "_record(" in stripped or "_degrade(" in stripped:
                    callers.append(f"{module.name}: {stripped}")
        assert not callers, (
            f"{name} now has a producer — the results document says it has none. "
            f"Update docs/live-test-results/delivery-per-kind.md. Found: {callers}"
        )


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
