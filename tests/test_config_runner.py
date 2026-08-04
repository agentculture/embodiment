"""The config reviewer's daemon thread (task t9).

The cited runner's suite, re-pointed at the copy that owns it, plus the three
things this lane does *differently* and must therefore prove:

* **No staleness.** A review that finished ten steps late is still delivered:
  the lifecycle's gate grades a proposal against the configuration actually in
  force when it would apply, and a second, weaker, step-counting check here
  would throw away proposals the gate would have graded properly.
* **No supersession.** There is no version chain to be overtaken by; the
  identity discipline is ``change_id``-must-be-new and it lives in the gate.
* **Deleting the mechanisms deleted their constants.** ``DEFAULT_MAX_LAG`` does
  not exist, and neither does ``max_lag`` on :class:`ConfigLimits`.

Everything else is the inherited discipline: one daemon thread, a bounded join,
a poll-wake read, degrade-never-raise, and two accounting identities that close
arithmetically so a silent loss is a failing test rather than a missing record.
"""

from __future__ import annotations

import ast
import contextlib
import json
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterator

import pytest

from embodiment.capability import Capability, CapabilityCatalog
from embodiment.config_change import ORIGIN_STRATEGIST, TARGET_WORKER_PROMPTS
from embodiment.config_review import (
    CONFIG_EXIT_CHANGES,
    CONFIG_EXIT_DEGRADED,
    CONFIG_EXIT_UNCHANGED,
    MARKER_CHANGES,
    MARKER_HOLD,
    REVIEW_CODES,
    ConfigControls,
    ConfigSnapshot,
)
from embodiment.config_runner import (
    DEFAULT_JOIN_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    LANE_CODES,
    RUNNER_CODES,
    RUNNER_DEGRADED_CLOSER,
    RUNNER_DEGRADED_SEAM,
    RUNNER_DEGRADED_THREAD,
    RUNNER_DEGRADED_WORKER,
    RUNNER_DROPPED_CADENCE,
    RUNNER_DROPPED_LATE,
    RUNNER_DROPPED_OVERFLOW,
    RUNNER_DROPPED_SNAPSHOT,
    THREAD_NAME,
    ConfigLimits,
    ConfigRunner,
)
from embodiment.contract import ModelResponse

_SOURCE = Path(__file__).resolve().parents[1] / "embodiment" / "config_runner.py"
_TIMEOUT = 10.0


# ── doubles ───────────────────────────────────────────────────────────────────


def _catalog() -> CapabilityCatalog:
    return CapabilityCatalog(
        entries=(Capability(capability_id="read_file", kind="tool"),), catalog_id="test-host"
    )


def _snapshot(snapshot_id: str = "s1", **kw: Any) -> ConfigSnapshot:
    return ConfigSnapshot(snapshot_id=snapshot_id, **kw)


def _unit(change_id: str = "c1", section: str = "care") -> dict[str, Any]:
    return {
        "target": TARGET_WORKER_PROMPTS,
        "change_id": change_id,
        "origin": ORIGIN_STRATEGIST,
        "section": section,
        "text": "be careful",
    }


def _changes_turn(change_id: str = "c1") -> ModelResponse:
    return ModelResponse(content=f"{MARKER_CHANGES} " + json.dumps({"changes": [_unit(change_id)]}))


def _hold_turn() -> ModelResponse:
    return ModelResponse(content=MARKER_HOLD)


class _Scripted:
    """A seam that replays answers, counting calls. Thread-safe enough for one worker."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers) or [_hold_turn()]
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        self.calls += 1
        item = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class _Gated(_Scripted):
    """A seam that parks inside the model call until released."""

    def __init__(self, *answers: Any) -> None:
        super().__init__(*answers)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        self.started.set()
        self.release.wait(_TIMEOUT)
        return super().__call__(messages)


class _Wedged:
    """A seam that never returns until the test lets it."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, messages: list[dict[str, Any]]) -> Any:
        self.started.set()
        self.release.wait(_TIMEOUT)
        return _hold_turn()


@contextlib.contextmanager
def _runner(complete: Any, **kw: Any) -> Iterator[ConfigRunner]:
    """A runner that is ALWAYS closed — and never left parked on a gate."""
    kw.setdefault("catalog", _catalog())
    runner = ConfigRunner(complete, **kw)
    try:
        yield runner
    finally:
        release = getattr(complete, "release", None)
        if isinstance(release, threading.Event):
            release.set()
        runner.close(timeout=_TIMEOUT)


def _one_review(runner: ConfigRunner, snapshot: Any, *, step: int = 0) -> None:
    """Offer one snapshot and wait, boundedly, for the lane to go quiet."""
    runner.consider(snapshot, step_index=step)
    assert runner.wait_idle(_TIMEOUT), "the reviewer lane never went idle"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


# ── 1. the reviewerless default ───────────────────────────────────────────────


class TestTheReviewerlessDefault:
    def test_constructing_a_runner_starts_no_thread(self) -> None:
        before = {t.ident for t in threading.enumerate()}
        with _runner(_Scripted()) as runner:
            assert runner.thread_started is False
            assert {t.ident for t in threading.enumerate()} == before

    def test_the_one_thread_is_a_named_daemon(self) -> None:
        seen: list[dict[str, Any]] = []

        def factory(**kw: Any) -> threading.Thread:
            seen.append(dict(kw))
            return threading.Thread(**kw)

        with _runner(_Scripted(), thread_factory=factory) as runner:
            _one_review(runner, _snapshot())
        assert len(seen) == 1
        assert seen[0]["name"] == THREAD_NAME
        assert seen[0]["daemon"] is True

    def test_many_reviews_still_use_one_thread(self) -> None:
        seen: list[dict[str, Any]] = []

        def factory(**kw: Any) -> threading.Thread:
            seen.append(dict(kw))
            return threading.Thread(**kw)

        with _runner(_Scripted(), thread_factory=factory) as runner:
            for index in range(4):
                _one_review(runner, _snapshot(f"s{index}"), step=index * 5)
        assert len(seen) == 1


# ── 2. consider / drain never block and never raise ───────────────────────────


class TestNonBlocking:
    def test_consider_returns_while_the_reviewer_is_mid_review(self) -> None:
        seam = _Gated(_hold_turn())
        with _runner(seam) as runner:
            runner.consider(_snapshot("s1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot("s2"), step_index=9)
            assert runner.drain(step_count=9) == []
            seam.release.set()

    def test_drain_returns_immediately_with_a_wedged_seam(self) -> None:
        seam = _Wedged()
        with _runner(seam) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            assert runner.drain() == []

    def test_close_never_hangs_on_a_wedged_seam(self) -> None:
        seam = _Wedged()
        runner = ConfigRunner(seam, catalog=_catalog())
        runner.consider(_snapshot(), step_index=1)
        assert seam.started.wait(_TIMEOUT)
        runner.close(timeout=0.05)
        assert runner.closed
        seam.release.set()

    def test_a_none_snapshot_is_a_no_op(self) -> None:
        with _runner(_Scripted()) as runner:
            runner.consider(None)
            assert runner.thread_started is False
            assert runner.counts["snapshots_offered"] == 0


# ── 3. what a review produces ─────────────────────────────────────────────────


class TestWhatIsDelivered:
    def test_a_change_reaches_the_drain(self) -> None:
        with _runner(_Scripted(_changes_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            ready = runner.drain(step_count=1)
        assert len(ready) == 1
        assert ready[0].exit_reason == CONFIG_EXIT_CHANGES
        assert [c.change_id for c in ready[0].changes] == ["c1"]

    def test_a_hold_is_delivered_and_counted_as_a_real_answer(self) -> None:
        with _runner(_Scripted(_hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            ready = runner.drain(step_count=1)
            assert ready[0].exit_reason == CONFIG_EXIT_UNCHANGED
            assert runner.counts["holds_delivered"] == 1
            assert runner.counts["changes_delivered"] == 0

    def test_a_second_drain_returns_nothing(self) -> None:
        with _runner(_Scripted(_changes_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.drain(step_count=1)
            assert runner.drain(step_count=1) == []

    def test_the_review_loops_own_records_are_relayed_not_reinvented(self) -> None:
        with _runner(_Scripted(RuntimeError("dead port"))) as runner:
            _one_review(runner, _snapshot(), step=1)
            codes = [entry.code for entry in runner.degradations]
        assert any(code in REVIEW_CODES for code in codes)

    def test_a_relayed_refusal_is_counted(self) -> None:
        bad = ModelResponse(
            content=f"{MARKER_CHANGES} " + json.dumps({"changes": [_unit(change_id="")]})
        )
        with _runner(_Scripted(bad), controls=ConfigControls(max_turns=1)) as runner:
            _one_review(runner, _snapshot(), step=1)
            runner.drain(step_count=1)
            assert runner.counts["refusals_relayed"] == 1


# ── 4. the two deletions ──────────────────────────────────────────────────────


class TestTheDeletedMechanisms:
    """What the spec's kept/deleted list retires, proved absent."""

    def test_no_staleness_constant_exists(self) -> None:
        import embodiment.config_runner as module

        assert not hasattr(module, "DEFAULT_MAX_LAG")

    def test_the_limits_object_carries_five_fields_and_no_max_lag(self) -> None:
        names = {entry.name for entry in fields(ConfigLimits)}
        assert "max_lag" not in names
        assert names == {
            "max_pending",
            "max_failed_reviews",
            "join_timeout",
            "poll_interval",
            "review_gap",
        }

    def test_a_very_late_review_is_still_delivered(self) -> None:
        """The cited runner would have dropped this one at lag > 12."""
        with _runner(_Scripted(_changes_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            ready = runner.drain(step_count=9999)
        assert len(ready) == 1

    def test_no_version_or_supersession_vocabulary_is_declared(self) -> None:
        assert not any("supersede" in code for code in RUNNER_CODES)
        assert not any("stale" in code for code in RUNNER_CODES)

    def test_the_runner_mints_eight_codes(self) -> None:
        assert len(RUNNER_CODES) == 8
        assert len(set(RUNNER_CODES)) == 8
        assert all(code.startswith("config-runner-") for code in RUNNER_CODES)

    def test_the_lane_vocabulary_is_the_runners_plus_the_reviews(self) -> None:
        assert set(LANE_CODES) == set(RUNNER_CODES) | set(REVIEW_CODES)


# ── 5. cadence, displacement, overflow ────────────────────────────────────────


class TestCadenceAndDisplacement:
    def test_a_snapshot_inside_the_gap_is_skipped_and_recorded(self) -> None:
        with _runner(_Scripted(), limits=ConfigLimits(review_gap=5)) as runner:
            _one_review(runner, _snapshot("s1"), step=1)
            runner.consider(_snapshot("s2"), step_index=2)
            assert runner.counts["snapshots_skipped_cadence"] == 1
            assert RUNNER_DROPPED_CADENCE in [e.code for e in runner.degradations]

    def test_an_escalation_always_passes_the_gap(self) -> None:
        with _runner(_Scripted(), limits=ConfigLimits(review_gap=5)) as runner:
            _one_review(runner, _snapshot("s1"), step=1)
            _one_review(
                runner,
                _snapshot("s2", requested_decision="should the worker read the guide first?"),
                step=2,
            )
            assert runner.counts["snapshots_skipped_cadence"] == 0
            assert runner.counts["reviews_started"] == 2

    def test_a_zero_gap_disables_cadence(self) -> None:
        with _runner(_Scripted(), limits=ConfigLimits(review_gap=0)) as runner:
            _one_review(runner, _snapshot("s1"), step=1)
            _one_review(runner, _snapshot("s2"), step=2)
            assert runner.counts["reviews_started"] == 2

    def test_a_displaced_snapshot_is_recorded(self) -> None:
        seam = _Gated(_hold_turn())
        with _runner(seam, limits=ConfigLimits(review_gap=0)) as runner:
            runner.consider(_snapshot("s1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot("s2"), step_index=2)
            runner.consider(_snapshot("s3"), step_index=3)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
        assert runner.counts["snapshots_displaced"] == 1
        assert RUNNER_DROPPED_SNAPSHOT in [e.code for e in runner.degradations]

    def test_an_overflowing_buffer_names_the_review_it_lost(self) -> None:
        with _runner(
            _Scripted(_hold_turn()),
            limits=ConfigLimits(max_pending=1, review_gap=0),
        ) as runner:
            _one_review(runner, _snapshot("s1"), step=1)
            _one_review(runner, _snapshot("s2"), step=2)
            assert runner.counts["reviews_dropped_overflow"] == 1
            overflow = [e for e in runner.degradations if e.code == RUNNER_DROPPED_OVERFLOW]
            assert overflow and overflow[0].step_index == 1

    def test_a_zero_length_buffer_floors_at_one(self) -> None:
        with _runner(_Scripted(), limits=ConfigLimits(max_pending=0)) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.drain(step_count=1)


# ── 6. degrade, never raise ───────────────────────────────────────────────────


class TestDegradeNeverRaise:
    def test_a_thread_that_will_not_start_degrades_at_once(self) -> None:
        def factory(**kw: Any) -> Any:
            raise RuntimeError("no threads today")

        with _runner(_Scripted(), thread_factory=factory) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert RUNNER_DEGRADED_THREAD in (runner.degradation() or "")
        assert runner.counts["snapshots_dropped_late"] == 1

    def test_a_dead_seam_stops_the_lane_after_the_tolerated_run(self) -> None:
        with _runner(_Scripted(RuntimeError("dead"))) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert RUNNER_DEGRADED_SEAM in (runner.degradation() or "")
            ready = runner.drain(step_count=1)
        # The degraded review is still delivered — it is the record saying so.
        assert ready and ready[0].exit_reason == CONFIG_EXIT_DEGRADED

    def test_a_baseexception_in_the_worker_is_recorded_not_silent(self) -> None:
        """C3: a dead worker leaves a record.

        The re-raise is the point — the record must not cost the exception its
        meaning — so pytest's unraisable-thread-exception warning is the
        EXPECTED shape here, not noise to be silenced elsewhere.
        """

        class Cancel(BaseException):
            pass

        def seam(messages: list[dict[str, Any]]) -> Any:
            raise Cancel("the host cancelled mid-review")

        with _runner(seam) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert runner.wait_idle(_TIMEOUT)
            assert RUNNER_DEGRADED_WORKER in (runner.degradation() or "")

    def test_offering_to_a_closed_lane_is_recorded(self) -> None:
        runner = ConfigRunner(_Scripted(), catalog=_catalog())
        runner.close()
        runner.consider(_snapshot(), step_index=1)
        assert runner.counts["snapshots_dropped_late"] == 1
        assert RUNNER_DROPPED_LATE in [e.code for e in runner.degradations]

    def test_an_undrained_review_is_recorded_at_close(self) -> None:
        runner = ConfigRunner(_Scripted(_changes_turn()), catalog=_catalog())
        _one_review(runner, _snapshot(), step=1)
        runner.close(timeout=_TIMEOUT)
        assert runner.counts["reviews_dropped_late"] == 1

    def test_a_queued_snapshot_is_recorded_at_close(self) -> None:
        seam = _Gated(_hold_turn(), _hold_turn())
        runner = ConfigRunner(seam, catalog=_catalog(), limits=ConfigLimits(review_gap=0))
        runner.consider(_snapshot("s1"), step_index=1)
        assert seam.started.wait(_TIMEOUT)
        runner.consider(_snapshot("s2"), step_index=2)
        runner.close(timeout=0.05)
        seam.release.set()
        assert runner.counts["snapshots_dropped_late"] == 1

    def test_close_is_idempotent(self) -> None:
        runner = ConfigRunner(_Scripted(), catalog=_catalog())
        runner.close()
        before = dict(runner.counts)
        runner.close()
        assert runner.counts == before

    def test_a_dead_thread_is_reported_as_dead_rather_than_healthy(self) -> None:
        class Corpse:
            def start(self) -> None:
                return None

            def is_alive(self) -> bool:
                return False

        with _runner(_Scripted(), thread_factory=lambda **kw: Corpse()) as runner:
            runner.consider(_snapshot("s1"), step_index=1)
            runner.consider(_snapshot("s2"), step_index=9)
            assert RUNNER_DEGRADED_WORKER in (runner.degradation() or "")

    def test_a_hostile_snapshot_never_reaches_the_actors_thread(self) -> None:
        class Hostile:
            @property
            def snapshot_id(self) -> str:
                raise RuntimeError("hostile")

            @property
            def requested_decision(self) -> str:
                raise RuntimeError("hostile")

        with _runner(_Scripted()) as runner:
            runner.consider(Hostile(), step_index=1)  # must not raise
            assert runner.wait_idle(_TIMEOUT)


class TestClosers:
    def test_a_closer_runs_once_at_close(self) -> None:
        ran: list[int] = []
        runner = ConfigRunner(_Scripted(), catalog=_catalog(), closers=(lambda: ran.append(1),))
        runner.close()
        runner.close()
        assert ran == [1]

    def test_a_raising_closer_is_recorded_rather_than_propagated(self) -> None:
        def boom() -> None:
            raise RuntimeError("teardown failed")

        runner = ConfigRunner(_Scripted(), catalog=_catalog(), closers=(boom,))
        runner.close()
        assert RUNNER_DEGRADED_CLOSER in [e.code for e in runner.degradations]

    def test_closers_run_after_the_late_drop_accounting(self) -> None:
        seen: list[int] = []
        runner = ConfigRunner(_Scripted(_changes_turn()), catalog=_catalog())
        runner._closers = (lambda: seen.append(runner.counts["reviews_dropped_late"]),)
        _one_review(runner, _snapshot(), step=1)
        runner.close(timeout=_TIMEOUT)
        assert seen == [1]


# ── 7. the accounting identities ──────────────────────────────────────────────


class TestTheAccountingIdentitiesClose:
    """A number that does not close is a silent loss, and this says which."""

    @staticmethod
    def _intake(runner: ConfigRunner) -> None:
        state = runner.state()
        counts = state["counts"]
        assert counts["snapshots_offered"] == (
            counts["reviews_started"]
            + counts["snapshots_displaced"]
            + counts["snapshots_skipped_cadence"]
            + counts["snapshots_dropped_late"]
            + state["snapshots_pending"]
        )

    @staticmethod
    def _outcome(runner: ConfigRunner) -> None:
        state = runner.state()
        counts = state["counts"]
        assert counts["reviews_completed"] == (
            counts["reviews_delivered"]
            + counts["reviews_dropped_late"]
            + counts["reviews_dropped_overflow"]
            + state["reviews_buffered"]
        )

    def test_a_plain_run_closes_both(self) -> None:
        with _runner(_Scripted(_changes_turn()), limits=ConfigLimits(review_gap=0)) as runner:
            for index in range(3):
                _one_review(runner, _snapshot(f"s{index}"), step=index + 1)
                runner.drain(step_count=index + 1)
            self._intake(runner)
            self._outcome(runner)

    def test_a_run_with_cadence_displacement_and_overflow_closes_both(self) -> None:
        seam = _Gated(_hold_turn(), _hold_turn(), _hold_turn())
        with _runner(seam, limits=ConfigLimits(max_pending=1, review_gap=2)) as runner:
            runner.consider(_snapshot("s1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot("s2"), step_index=2)
            runner.consider(_snapshot("s3"), step_index=3)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            _one_review(runner, _snapshot("s4"), step=20)
            self._intake(runner)
            self._outcome(runner)

    def test_a_closed_lane_still_closes_both(self) -> None:
        runner = ConfigRunner(_Scripted(_changes_turn()), catalog=_catalog())
        _one_review(runner, _snapshot(), step=1)
        runner.close(timeout=_TIMEOUT)
        self._intake(runner)
        self._outcome(runner)


# ── 8. the measurement surface ────────────────────────────────────────────────


class TestTheMeasurementSurface:
    def test_relative_latency_is_none_without_both_halves(self) -> None:
        with _runner(_Scripted()) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.state()["relative_latency"] is None

    def test_relative_latency_reports_the_ratio_once_both_are_measured(self) -> None:
        ticks = iter([0.0, 4.0])
        with _runner(_Scripted(), clock=lambda: next(ticks)) as runner:
            _one_review(runner, _snapshot(), step=1)
            runner.note_actor_step(2.0)
            assert runner.state()["relative_latency"] == pytest.approx(2.0)

    @pytest.mark.parametrize("value", [float("nan"), 0, -1, "x", None])
    def test_a_junk_step_duration_is_ignored_rather_than_poisoning_the_mean(
        self, value: Any
    ) -> None:
        with _runner(_Scripted()) as runner:
            runner.note_actor_step(value)
            assert runner.state()["relative_latency"] is None

    def test_the_state_fold_returns_copies(self) -> None:
        with _runner(_Scripted()) as runner:
            state = runner.state()
            state["counts"]["snapshots_offered"] = 999
            assert runner.counts["snapshots_offered"] == 0

    def test_an_undeclared_model_stays_empty(self) -> None:
        with _runner(_Scripted()) as runner:
            assert runner.model == ""
            assert runner.role == "strategist"


class TestHostileLimits:
    def test_a_hostile_limits_object_degrades_field_by_field(self) -> None:
        class Hostile:
            @property
            def max_pending(self) -> int:
                raise RuntimeError("hostile")

            max_failed_reviews = "not a number"
            join_timeout = float("nan")
            poll_interval = -1.0
            review_gap = "five"

        with _runner(_Scripted(), limits=Hostile()) as runner:
            limits = runner.state()["limits"]
        assert limits["join_timeout"] == DEFAULT_JOIN_TIMEOUT
        assert limits["poll_interval"] == DEFAULT_POLL_INTERVAL
        assert limits["max_pending"] >= 1
        assert limits["max_failed_reviews"] >= 1
        assert limits["review_gap"] >= 0

    def test_no_limits_object_is_the_derived_defaults(self) -> None:
        with _runner(_Scripted()) as runner:
            assert runner.state()["limits"] == {
                field.name: getattr(ConfigLimits(), field.name) for field in fields(ConfigLimits)
            }


# ── 9. the derivations are readable, and the clocks are declared ──────────────


class TestTheDerivationsAreStated:
    def test_every_default_carries_a_derivation_comment(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        for name in (
            "DEFAULT_MAX_PENDING",
            "DEFAULT_MAX_FAILED_REVIEWS",
            "DEFAULT_JOIN_TIMEOUT",
            "DEFAULT_POLL_INTERVAL",
            "DEFAULT_REVIEW_GAP",
            "MAX_LEDGER",
        ):
            head, _, _ = source.partition(f"\n{name} = ")
            assert "DERIVATION:" in head.rsplit("#:", 1)[-1] or "DERIVATION:" in head[-1200:], name

    def test_the_three_quantities_are_named_with_their_committed_figures(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        for token in ("T_review_min", "T_review_max", "T_actor_step", "timeout-rate-measurements"):
            assert token in source

    def test_the_join_bound_is_the_stated_multiple_of_the_poll_interval(self) -> None:
        assert DEFAULT_JOIN_TIMEOUT == 2 * DEFAULT_POLL_INTERVAL


# ── 10. cited, not coupled ────────────────────────────────────────────────────


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
            "embodiment.muse",
            "embodiment.muse_runner",
            "embodiment.loop",
        }
        assert not (self._imported() & banned)

    def test_the_citation_is_named_in_prose(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        assert "strategist_runner.py" in source
        assert "muse_runner.py" in source

    def test_the_module_constructs_no_tool_surface(self) -> None:
        source = _SOURCE.read_text(encoding="utf-8")
        assert "ToolExecutor" not in source
        assert "tool_calls" not in source
