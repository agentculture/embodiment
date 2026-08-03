"""Tests for embodiment.strategist_runner — the strategist's THREAD (task t2).

:mod:`embodiment.scope` (task t1) holds the strategic *reasoning*: pure shapes,
directive validation, and a bounded review loop with no thread, no clock and no
timer. This module holds only the **thread mechanics** that let that loop run
beside the acting loop instead of inside it.

The mechanics are **cited, not depended on**. ``embodiment/muse_runner.py`` is
the reference — the single replaceable pending slot, the poll-wake read, the
bounded join that never hangs on a parked blocking read — and this lane owns its
own copy outright. Nothing here imports it, and an AST test says so.

Two deliberate deltas from the muse, and they are what most of this file tests:

* **The output is authority-bearing.** A muse insight that never arrives is lost
  counsel. A strategic review that never arrives is a lost *authority event*, so
  every displaced, skipped, stale, superseded, overflowed or late result is
  counted — and two accounting identities prove the counters close with zero
  unexplained losses.
* **The policy is designed, not inherited.** The muse's
  ``DEFAULT_STALE_LAG = 5`` was set expecting a slow advisor and measured false.
  Every default here carries a derivation from the committed rate config, and a
  structural test reads those derivations out of the source.

Every test is deterministic by construction — there is not a single ``sleep``:

* the strategist's injected seam announces it has STARTED through a
  :class:`threading.Event` and blocks on a second one, so a test can assert
  "the actor drained while the strategist was provably mid-review" without
  timing;
* :meth:`StrategistRunner.wait_idle` is a bounded wait on a real condition;
* every wait is bounded by :data:`_TIMEOUT`, so a broken implementation FAILS
  rather than hanging CI.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import inspect
import json
import re
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

from embodiment.contract import ModelResponse
from embodiment.scope import (
    DEGRADED_REVIEW,
    MARKER_DIRECTIVE,
    MARKER_HOLD,
    SCOPE_EXIT_DEGRADED,
    SCOPE_EXIT_DIRECTIVE,
    SCOPE_EXIT_UNCHANGED,
    ScopeControls,
    ScopeDegradation,
    ScopeDirective,
    ScopeOutcome,
    ScopeRegister,
    ScopeSnapshot,
    ScopeToolBench,
)
from embodiment.strategist_runner import (
    DEFAULT_JOIN_TIMEOUT,
    DEFAULT_MAX_FAILED_REVIEWS,
    DEFAULT_MAX_LAG,
    DEFAULT_MAX_PENDING,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_REVIEW_GAP,
    DEGRADED_CLOSER,
    DEGRADED_SEAM,
    DEGRADED_THREAD,
    DEGRADED_WORKER,
    DROPPED_CADENCE,
    DROPPED_LATE,
    DROPPED_OVERFLOW,
    DROPPED_SNAPSHOT,
    DROPPED_STALE,
    DROPPED_SUPERSEDED,
    LANE_CODES,
    MAX_LEDGER,
    RUNNER_CODES,
    STRATEGIST_ROLE,
    THREAD_NAME,
    StrategistLimits,
    StrategistRunner,
)

#: Every wait in this file is bounded by this, so a broken implementation fails
#: the assertion instead of hanging the suite. Generous on purpose: CI under
#: load must never turn a correct implementation red.
_TIMEOUT = 5.0

_RUNNER_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "strategist_runner.py"


# ── doubles ───────────────────────────────────────────────────────────────────


def _resp(content: str = "", **kw: Any) -> ModelResponse:
    return ModelResponse(content=content, **kw)


def _snapshot(**kw: Any) -> ScopeSnapshot:
    base: dict[str, Any] = {
        "snapshot_id": "snapshot-001",
        "objectives": ["ship the extraction"],
    }
    base.update(kw)
    return ScopeSnapshot(**base)


def _payload(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scope_id": "scope-001",
        "supersedes": None,
        "version": 1,
        "objective": "Finish the extraction without losing conversational presence",
        "priorities": ["Preserve responsiveness"],
        "constraints": ["Background work must not control the speaking path"],
        "responsibilities": [{"owner": "worker", "responsibility": "Plan the inspection"}],
        "success_conditions": ["The requested result is produced"],
        "review_when": ["The operator changes the objective"],
        "decision_summary": "Separate conversational continuity from background execution.",
    }
    base.update(kw)
    return base


def _directive_turn(**kw: Any) -> ModelResponse:
    return _resp(f"Here is my reading.\n{MARKER_DIRECTIVE}\n{json.dumps(_payload(**kw))}")


def _hold_turn() -> ModelResponse:
    return _resp(MARKER_HOLD)


class _Scripted:
    """A strategist seam that replays scripted replies, recording its own thread."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.threads: list[threading.Thread] = []
        self.seen: list[list[dict[str, Any]]] = []

    def _next(self, messages: list[dict[str, Any]]) -> Any:
        self.calls += 1
        self.threads.append(threading.current_thread())
        self.seen.append([dict(m) for m in messages])
        reply = self.replies.pop(0) if self.replies else _hold_turn()
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        return self._next(messages)


class _Gated(_Scripted):
    """A scripted seam that ANNOUNCES it started and then waits to be released.

    The whole determinism story: a test waits on :attr:`started` to know the
    strategist is provably inside a completion, asserts whatever must hold while
    it is thinking, then sets :attr:`release`. No sleep, no polling, no guess.
    """

    def __init__(self, *replies: Any) -> None:
        super().__init__(*replies)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.started.set()
        assert self.release.wait(_TIMEOUT), "the gated strategist seam was never released"
        return self._next(messages)


class _Wedged:
    """A seam that NEVER returns until the test releases it. The parked-call case."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.calls += 1
        self.started.set()
        self.release.wait(30.0)
        return _hold_turn()


@contextlib.contextmanager
def _runner(complete: Any, **kw: Any) -> Iterator[StrategistRunner]:
    """A runner that is ALWAYS closed — and never left parked on a gate."""
    runner = StrategistRunner(complete, **kw)
    try:
        yield runner
    finally:
        release = getattr(complete, "release", None)
        if isinstance(release, threading.Event):
            release.set()
        runner.close(timeout=_TIMEOUT)


def _live_threads() -> set[int]:
    return {t.ident for t in threading.enumerate() if t.ident is not None}


def _one_review(runner: StrategistRunner, snapshot: ScopeSnapshot, *, step: int = 0) -> None:
    """Offer one snapshot and wait, boundedly, for the lane to go quiet."""
    runner.consider(snapshot, step_index=step)
    assert runner.wait_idle(_TIMEOUT), "the strategist lane never went idle"


# ── 1. the strategistless default ────────────────────────────────────────────


class TestTheStrategistlessDefault:
    """No strategist configured means no thread — the tested default path."""

    def test_a_constructed_runner_starts_no_thread(self) -> None:
        before = _live_threads()
        with _runner(_Scripted()) as runner:
            assert runner.thread_started is False
            assert _live_threads() == before

    def test_the_thread_starts_only_on_the_first_snapshot(self) -> None:
        with _runner(_Scripted()) as runner:
            assert runner.thread_started is False
            _one_review(runner, _snapshot())
            assert runner.thread_started is True

    def test_a_none_snapshot_is_not_work(self) -> None:
        with _runner(_Scripted()) as runner:
            runner.consider(None)
            assert runner.thread_started is False
            assert runner.counts["snapshots_offered"] == 0

    def test_the_thread_is_a_named_daemon(self) -> None:
        seen: list[dict[str, Any]] = []

        def factory(**kw: Any) -> Any:
            seen.append(dict(kw))
            return threading.Thread(**kw)

        with _runner(_Scripted(), thread_factory=factory) as runner:
            _one_review(runner, _snapshot())
        assert seen and seen[0]["name"] == THREAD_NAME
        assert seen[0]["daemon"] is True


# ── 2. consider / drain never block and never raise ──────────────────────────


class TestNonBlocking:
    """The acting loop never waits on the strategist, and never sees a raise."""

    def test_consider_returns_while_the_strategist_is_mid_review(self) -> None:
        seam = _Gated(_hold_turn())
        with _runner(seam) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            # Provably inside the model call. Both verbs still return.
            runner.consider(_snapshot(snapshot_id="snapshot-002"), step_index=2)
            assert runner.drain(step_count=2) == []
            seam.release.set()

    def test_drain_returns_immediately_with_a_wedged_seam(self) -> None:
        seam = _Wedged()
        with _runner(seam) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            for step in range(2, 12):
                assert runner.drain(step_count=step) == []
            assert runner.degradation() is None
            seam.release.set()

    def test_consider_never_raises_on_a_hostile_snapshot(self) -> None:
        class Hostile:
            @property
            def snapshot_id(self) -> str:
                raise RuntimeError("no")

            @property
            def objectives(self) -> Any:
                raise RuntimeError("no")

        with _runner(_Scripted(_hold_turn())) as runner:
            runner.consider(Hostile(), step_index=3)
            assert runner.wait_idle(_TIMEOUT)
            # The review still ran and the lane is alive.
            assert runner.counts["reviews_completed"] == 1

    def test_a_raising_seam_never_reaches_the_caller(self) -> None:
        with _runner(_Scripted(RuntimeError("dead port"))) as runner:
            _one_review(runner, _snapshot())
            delivered = runner.drain()
        assert [o.exit_reason for o in delivered] == [SCOPE_EXIT_DEGRADED]
        assert any(d.code == DEGRADED_REVIEW for d in delivered[0].degradations)

    def test_an_empty_drain_is_the_normal_case(self) -> None:
        with _runner(_Scripted()) as runner:
            assert runner.drain() == []
            assert runner.drain(step_count=99) == []

    def test_a_hostile_snapshot_is_named_absent_when_it_is_displaced(self) -> None:
        """The record path reads the host's object too, and must not raise there."""

        class Hostile:
            @property
            def snapshot_id(self) -> str:
                raise RuntimeError("no")

        seam = _Gated(_hold_turn(), _hold_turn())
        with _runner(seam) as runner:
            runner.consider(_snapshot(snapshot_id="s-1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(Hostile(), step_index=3)
            runner.consider(_snapshot(snapshot_id="s-3"), step_index=5)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
        record = next(d for d in runner.degradations if d.code == DROPPED_SNAPSHOT)
        assert "''" in record.reason  # unreadable id renders as absent, not as a crash

    def test_start_is_idempotent(self) -> None:
        with _runner(_Scripted()) as runner:
            assert runner.start() is True
            assert runner.start() is True
            assert runner.thread_started is True

    def test_start_refuses_after_close(self) -> None:
        runner = StrategistRunner(_Scripted())
        runner.close(timeout=_TIMEOUT)
        assert runner.start() is False


# ── 3. one review in flight, one replaceable pending slot ────────────────────


class TestOneReviewInFlight:
    """At most one review runs; a newer snapshot replaces the waiting one."""

    def test_a_newer_snapshot_displaces_the_waiting_one(self) -> None:
        seam = _Gated(_hold_turn(), _hold_turn())
        with _runner(seam) as runner:
            runner.consider(_snapshot(snapshot_id="s-1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            # Steps 3 and 5, not 2 and 3: both must clear the default cadence
            # gap, or the second offer would be skipped rather than displaced
            # and this would silently test the wrong mechanism.
            runner.consider(_snapshot(snapshot_id="s-2"), step_index=3)
            runner.consider(_snapshot(snapshot_id="s-3"), step_index=5)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
        counts = runner.counts
        assert counts["snapshots_displaced"] == 1
        assert counts["snapshots_skipped_cadence"] == 0
        codes = [d.code for d in runner.degradations]
        assert DROPPED_SNAPSHOT in codes

    def test_the_displaced_snapshot_is_named_in_its_record(self) -> None:
        seam = _Gated(_hold_turn(), _hold_turn())
        with _runner(seam) as runner:
            runner.consider(_snapshot(snapshot_id="s-1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot(snapshot_id="displaced-me"), step_index=7)
            runner.consider(_snapshot(snapshot_id="s-3"), step_index=8)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
        record = next(d for d in runner.degradations if d.code == DROPPED_SNAPSHOT)
        assert "displaced-me" in record.reason
        assert record.step_index == 7

    def test_only_one_review_is_ever_in_flight(self) -> None:
        seen: list[int] = []
        lock = threading.Lock()
        inside = [0]

        class Counting(_Gated):
            def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
                with lock:
                    inside[0] += 1
                    seen.append(inside[0])
                try:
                    return super().__call__(messages)
                finally:
                    with lock:
                        inside[0] -= 1

        seam = Counting(_hold_turn(), _hold_turn(), _hold_turn())
        with _runner(seam) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot(snapshot_id="s-2"), step_index=3)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
        assert seen and max(seen) == 1
        assert runner.counts["reviews_started"] == 2


# ── 4. zero silent losses — the counted proof ────────────────────────────────


def _intake_closes(runner: StrategistRunner) -> tuple[int, int]:
    """``(offered, accounted)`` for the OFFER side of the lane."""
    counts = runner.counts
    accounted = (
        counts["reviews_started"]
        + counts["snapshots_displaced"]
        + counts["snapshots_skipped_cadence"]
        + counts["snapshots_dropped_late"]
        + runner.state()["snapshots_pending"]
    )
    return counts["snapshots_offered"], accounted


def _outcomes_close(runner: StrategistRunner) -> tuple[int, int]:
    """``(completed, accounted)`` for the RESULT side of the lane."""
    counts = runner.counts
    accounted = (
        counts["reviews_delivered"]
        + counts["reviews_dropped_stale"]
        + counts["reviews_dropped_late"]
        + counts["reviews_dropped_overflow"]
        + counts["directives_superseded"]
        + runner.state()["reviews_buffered"]
    )
    return counts["reviews_completed"], accounted


class TestZeroSilentLosses:
    """Every authority event is either delivered or COUNTED as lost."""

    def test_the_counters_close_on_a_healthy_lane(self) -> None:
        with _runner(_Scripted(_directive_turn(), _hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert len(runner.drain(step_count=1)) == 1
            # Step 3, not 2: the default cadence gap is a designed constant, and
            # a healthy-lane test that ignored it would be testing a lane the
            # shipped defaults do not produce.
            _one_review(runner, _snapshot(snapshot_id="s-2"), step=3)
            assert len(runner.drain(step_count=3)) == 1
            offered, accounted = _intake_closes(runner)
            assert offered == accounted == 2
            completed, out_accounted = _outcomes_close(runner)
            assert completed == out_accounted == 2

    def test_the_counters_close_across_every_loss_path(self) -> None:
        """One scenario that fires displacement, cadence, staleness and overflow."""
        limits = StrategistLimits(max_pending=1, max_lag=1, review_gap=0)
        seam = _Gated(_hold_turn(), _hold_turn(), _hold_turn())
        with _runner(seam, limits=limits) as runner:
            runner.consider(_snapshot(snapshot_id="s-1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot(snapshot_id="s-2"), step_index=2)
            runner.consider(_snapshot(snapshot_id="s-3"), step_index=3)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            # Nothing drained: the buffered result is now far behind the actor.
            assert runner.drain(step_count=99) == []
            offered, accounted = _intake_closes(runner)
            assert offered == accounted
            completed, out_accounted = _outcomes_close(runner)
            assert completed == out_accounted
        assert runner.counts["reviews_dropped_stale"] >= 1

    def test_a_stranded_result_is_counted_at_close(self) -> None:
        runner = StrategistRunner(_Scripted(_hold_turn()))
        _one_review(runner, _snapshot(), step=1)
        assert runner.state()["reviews_buffered"] == 1
        runner.close(timeout=_TIMEOUT)
        assert runner.counts["reviews_dropped_late"] == 1
        completed, accounted = _outcomes_close(runner)
        assert completed == accounted == 1

    def test_a_queued_snapshot_is_counted_at_close(self) -> None:
        """Closed while one review was parked and a second snapshot waited."""
        seam = _Gated(_hold_turn(), _hold_turn())
        runner = StrategistRunner(seam)
        try:
            runner.consider(_snapshot(snapshot_id="s-1"), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_snapshot(snapshot_id="never-run"), step_index=5)
            runner.close(timeout=0.05)  # the first review is still parked
            assert runner.counts["snapshots_dropped_late"] == 1
            record = next(d for d in runner.degradations if d.code == DROPPED_LATE)
            assert "never-run" in record.reason
            offered, accounted = _intake_closes(runner)
            assert offered == accounted == 2
        finally:
            seam.release.set()
            runner.close(timeout=_TIMEOUT)

    def test_an_offer_after_close_is_recorded_not_ignored(self) -> None:
        runner = StrategistRunner(_Scripted())
        runner.close(timeout=_TIMEOUT)
        runner.consider(_snapshot(snapshot_id="too-late"), step_index=4)
        assert runner.counts["snapshots_offered"] == 1
        assert runner.counts["snapshots_dropped_late"] == 1
        record = next(d for d in runner.degradations if d.code == DROPPED_LATE)
        assert "too-late" in record.reason

    def test_overflow_discards_the_oldest_and_records_it(self) -> None:
        limits = StrategistLimits(max_pending=1, max_lag=0, review_gap=0)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            _one_review(runner, _snapshot(snapshot_id="s-2"), step=2)
            delivered = runner.drain(step_count=2)
        assert runner.counts["reviews_dropped_overflow"] == 1
        assert [o.snapshot_id for o in delivered] == ["s-2"]
        assert DROPPED_OVERFLOW in [d.code for d in runner.degradations]


# ── 5. staleness and supersession are AUTHORITY events ───────────────────────


class TestStalenessAndSupersession:
    """A stale or superseded directive is never applied — and never silent."""

    def test_a_review_further_behind_than_max_lag_is_dropped(self) -> None:
        limits = StrategistLimits(max_lag=2, review_gap=0)
        with _runner(_Scripted(_directive_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.drain(step_count=10) == []
        assert runner.counts["reviews_dropped_stale"] == 1
        record = next(d for d in runner.degradations if d.code == DROPPED_STALE)
        assert "10" in record.reason

    def test_a_review_within_max_lag_survives(self) -> None:
        limits = StrategistLimits(max_lag=12, review_gap=0)
        with _runner(_Scripted(_directive_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(), step=1)
            delivered = runner.drain(step_count=12)
        assert [o.exit_reason for o in delivered] == [SCOPE_EXIT_DIRECTIVE]

    def test_max_lag_zero_disables_staleness(self) -> None:
        limits = StrategistLimits(max_lag=0, review_gap=0)
        with _runner(_Scripted(_directive_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert len(runner.drain(step_count=10_000)) == 1

    def test_a_superseded_directive_is_dropped_and_recorded(self) -> None:
        """Two reviews buffered; the older directive is no longer the chain head."""
        limits = StrategistLimits(max_lag=0, review_gap=0)
        replies = [
            _directive_turn(scope_id="scope-a", version=1),
            _directive_turn(scope_id="scope-b", version=2, supersedes="scope-a"),
        ]
        with _runner(_Scripted(*replies), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            _one_review(runner, _snapshot(snapshot_id="s-2"), step=2)
            delivered = runner.drain(step_count=2)
        assert [o.directive.scope_id for o in delivered if o.directive] == ["scope-b"]
        assert runner.counts["directives_superseded"] == 1
        record = next(d for d in runner.degradations if d.code == DROPPED_SUPERSEDED)
        assert "scope-a" in record.reason

    def test_a_hold_is_delivered_and_counted_as_non_intervention(self) -> None:
        with _runner(_Scripted(_hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            delivered = runner.drain(step_count=1)
        assert [o.exit_reason for o in delivered] == [SCOPE_EXIT_UNCHANGED]
        assert runner.counts["holds_delivered"] == 1
        assert runner.counts["directives_delivered"] == 0


# ── 6. cadence — designed for a deep thinker ─────────────────────────────────


class TestCadence:
    """A slow, expensive reviewer is not offered a review at every step."""

    def test_a_snapshot_inside_the_gap_is_skipped_and_recorded(self) -> None:
        limits = StrategistLimits(review_gap=4)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            runner.consider(_snapshot(snapshot_id="s-2"), step_index=2)
            assert runner.counts["snapshots_skipped_cadence"] == 1
            assert runner.counts["reviews_started"] == 1
        assert DROPPED_CADENCE in [d.code for d in runner.degradations]

    def test_a_snapshot_past_the_gap_is_reviewed(self) -> None:
        limits = StrategistLimits(review_gap=4)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            _one_review(runner, _snapshot(snapshot_id="s-2"), step=5)
            assert runner.counts["reviews_started"] == 2

    def test_an_escalation_always_gets_through(self) -> None:
        limits = StrategistLimits(review_gap=100)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            _one_review(
                runner,
                _snapshot(snapshot_id="s-2", requested_decision="which objective wins?"),
                step=2,
            )
            assert runner.counts["reviews_started"] == 2
            assert runner.counts["snapshots_skipped_cadence"] == 0

    def test_a_host_that_supplies_no_step_is_not_gated(self) -> None:
        """Cadence is a step DISTANCE; with no steps there is none to measure."""
        limits = StrategistLimits(review_gap=100)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"))
            _one_review(runner, _snapshot(snapshot_id="s-2"))
            assert runner.counts["reviews_started"] == 2

    def test_gap_zero_disables_cadence(self) -> None:
        limits = StrategistLimits(review_gap=0)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            _one_review(runner, _snapshot(snapshot_id="s-2"), step=2)
            assert runner.counts["reviews_started"] == 2


# ── 7. bounded teardown ──────────────────────────────────────────────────────


class TestBoundedTeardown:
    """``close`` never hangs — not even on a seam that will never return."""

    def test_close_returns_while_the_seam_is_parked(self) -> None:
        seam = _Wedged()
        runner = StrategistRunner(seam)
        try:
            runner.consider(_snapshot(), step_index=1)
            assert seam.started.wait(_TIMEOUT)
            runner.close(timeout=0.05)  # the call is still parked; close returns
            assert runner.closed is True
        finally:
            seam.release.set()
            runner.close(timeout=_TIMEOUT)

    def test_close_is_idempotent(self) -> None:
        runner = StrategistRunner(_Scripted(_hold_turn()))
        _one_review(runner, _snapshot(), step=1)
        runner.close(timeout=_TIMEOUT)
        before = dict(runner.counts)
        runner.close(timeout=_TIMEOUT)
        runner.close(timeout=_TIMEOUT)
        assert runner.counts == before

    def test_the_worker_thread_is_reaped(self) -> None:
        runner = StrategistRunner(_Scripted(_hold_turn()))
        _one_review(runner, _snapshot(), step=1)
        assert THREAD_NAME in {t.name for t in threading.enumerate()}
        runner.close(timeout=_TIMEOUT)
        assert THREAD_NAME not in {t.name for t in threading.enumerate()}

    def test_a_closer_runs_once_after_the_join(self) -> None:
        calls: list[str] = []
        runner = StrategistRunner(_Scripted(_hold_turn()), closers=(lambda: calls.append("x"),))
        _one_review(runner, _snapshot(), step=1)
        runner.close(timeout=_TIMEOUT)
        runner.close(timeout=_TIMEOUT)
        assert calls == ["x"]

    def test_a_raising_closer_is_recorded_never_raised(self) -> None:
        def boom() -> None:
            raise RuntimeError("the workspace would not die")

        runner = StrategistRunner(_Scripted(), closers=(boom,))
        runner.close(timeout=_TIMEOUT)
        assert DEGRADED_CLOSER in [d.code for d in runner.degradations]

    def test_the_context_manager_closes(self) -> None:
        with StrategistRunner(_Scripted(_hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
        assert runner.closed is True

    def test_close_from_the_worker_thread_does_not_self_join(self) -> None:
        """The bounded join skips the CURRENT thread rather than deadlocking."""
        holder: dict[str, Any] = {}

        def seam(messages: list[dict[str, Any]]) -> ModelResponse:
            holder["runner"].close(timeout=_TIMEOUT)
            return _hold_turn()

        runner = StrategistRunner(seam)
        holder["runner"] = runner
        runner.consider(_snapshot(), step_index=1)
        assert runner.wait_idle(_TIMEOUT)
        runner.close(timeout=_TIMEOUT)
        assert runner.closed is True


# ── 8. the degradation ladder ────────────────────────────────────────────────


class TestDegradationLadder:
    """Every failure degrades visibly and never raises into the actor's path."""

    def test_a_thread_that_cannot_start_degrades(self) -> None:
        def factory(**kw: Any) -> Any:
            raise RuntimeError("no threads today")

        with _runner(_Scripted(), thread_factory=factory) as runner:
            runner.consider(_snapshot(), step_index=1)
            assert runner.degradation() is not None
            assert DEGRADED_THREAD in runner.degradation()
        assert runner.counts["snapshots_dropped_late"] == 1

    def test_consecutive_failed_reviews_stop_the_lane(self) -> None:
        limits = StrategistLimits(max_failed_reviews=1, review_gap=0)
        with _runner(_Scripted(RuntimeError("dead"), RuntimeError("dead")), limits=limits) as r:
            _one_review(r, _snapshot(), step=1)
            assert r.degradation() is not None
            assert DEGRADED_SEAM in r.degradation()
            r.consider(_snapshot(snapshot_id="s-2"), step_index=2)
            assert r.counts["reviews_started"] == 1

    def test_a_successful_review_resets_the_failure_run(self) -> None:
        limits = StrategistLimits(max_failed_reviews=2, review_gap=0)
        replies = [RuntimeError("blip"), _hold_turn(), RuntimeError("blip")]
        with _runner(_Scripted(*replies), limits=limits) as runner:
            for step in (1, 2, 3):
                _one_review(runner, _snapshot(snapshot_id=f"s-{step}"), step=step)
            assert runner.degradation() is None

    def test_a_worker_bug_is_recorded_never_silent(self) -> None:
        class Exploding:
            def review(self, snapshot: Any, **kw: Any) -> Any:
                raise RuntimeError("a bug in this module, not in the seam")

        runner = StrategistRunner(_Scripted())
        runner._loop = Exploding()  # the only way to reach this module's own bug path
        try:
            runner.consider(_snapshot(), step_index=1)
            assert runner.wait_idle(_TIMEOUT)
            assert DEGRADED_WORKER in (runner.degradation() or "")
        finally:
            runner.close(timeout=_TIMEOUT)

    def test_the_ledger_is_bounded_and_the_counters_are_not(self) -> None:
        runner = StrategistRunner(_Scripted())
        for index in range(MAX_LEDGER + 20):
            runner._record(DROPPED_STALE, f"record {index}")
        assert len(runner.degradations) == MAX_LEDGER
        assert runner.counts["degradations_recorded"] == MAX_LEDGER + 20
        runner.close(timeout=_TIMEOUT)


# ── 9. the citation is not a dependency (AST) ────────────────────────────────


def _imported_modules() -> set[str]:
    tree = ast.parse(_RUNNER_SRC.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestTheCitationIsNotADependency:
    """The mechanics are COPIED from the muse; nothing here imports it."""

    def test_the_module_is_named_strategist_runner(self) -> None:
        assert _RUNNER_SRC.is_file()

    def test_nothing_from_the_muse_is_imported(self) -> None:
        modules = _imported_modules()
        assert "embodiment.muse" not in modules
        assert "embodiment.muse_runner" not in modules
        assert not any(m.startswith("embodiment.muse") for m in modules)

    def test_no_muse_name_is_BOUND_or_USED(self) -> None:
        """Prose may CITE the muse; no identifier may come from it.

        A text scan would be the wrong test here: citing the reference by name in
        a docstring is the method working, not a leak. What must not survive the
        copy is a *binding* — a name resolved at runtime — so this walks the AST
        instead of the characters.
        """
        tree = ast.parse(_RUNNER_SRC.read_text(encoding="utf-8"))
        identifiers = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        leaked = {name for name in identifiers if name.lower().startswith("muse")}
        assert not leaked, leaked
        import embodiment.strategist_runner as mod

        assert not [name for name in vars(mod) if name.lower().startswith("muse")]

    def test_the_actor_loop_is_not_in_scope(self) -> None:
        import embodiment.strategist_runner as mod

        forbidden = {
            "HookDecision",
            "HookEvent",
            "DECISION_ALLOW",
            "DECISION_DENY",
            "DECISION_REWRITE",
            "PresenceExecutor",
            "ToolExecutor",
        }
        assert not (set(vars(mod)) & forbidden)
        assert "embodiment.loop" not in _imported_modules()

    def test_no_presence_or_event_fabric_import(self) -> None:
        modules = _imported_modules()
        assert "embodiment.presence_engine" not in modules
        assert "embodiment.presence" not in modules
        assert "embodiment.events" not in modules

    def test_only_stdlib_and_embodiment(self) -> None:
        stdlib_ok = {"__future__", "collections", "dataclasses", "math", "threading", "typing"}
        for module in _imported_modules():
            top = module.split(".")[0]
            assert module in stdlib_ok or top == "embodiment", module

    def test_no_colleague_import(self) -> None:
        assert not any(m.split(".")[0] == "colleague" for m in _imported_modules())

    def test_no_front_module_import(self) -> None:
        assert not any(
            m.startswith(("embodiment.cli", "embodiment.explain")) for m in _imported_modules()
        )


# ── 10. the constants are DERIVED, never inherited ───────────────────────────

#: Every tuning default must carry a derivation in the source above it.
_DERIVED = (
    "DEFAULT_MAX_PENDING",
    "DEFAULT_MAX_LAG",
    "DEFAULT_MAX_FAILED_REVIEWS",
    "DEFAULT_JOIN_TIMEOUT",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_REVIEW_GAP",
    "MAX_LEDGER",
)


def _derivation_for(name: str) -> str:
    """The ``#:`` comment block immediately above *name*'s assignment."""
    lines = _RUNNER_SRC.read_text(encoding="utf-8").splitlines()
    index = next(
        (i for i, line in enumerate(lines) if re.match(rf"^{name}\s*(?::[^=]+)?=", line)),
        None,
    )
    assert index is not None, f"{name} must be a module-level assignment"
    block: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
        block.insert(0, lines[cursor].lstrip("# ").rstrip())
        cursor -= 1
    return "\n".join(block)


class TestTheConstantsAreDerived:
    """No staleness or cadence number is copied across from a fast advisor."""

    def test_the_muse_staleness_default_is_not_inherited(self) -> None:
        """``DEFAULT_STALE_LAG = 5`` is cited in prose and nowhere in the code."""
        assert DEFAULT_MAX_LAG != 5
        tree = ast.parse(_RUNNER_SRC.read_text(encoding="utf-8"))
        bound = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "DEFAULT_STALE_LAG" not in bound | used

    @pytest.mark.parametrize("name", _DERIVED)
    def test_each_default_carries_a_derivation(self, name: str) -> None:
        block = _derivation_for(name)
        assert len(block) > 120, f"{name} has no derivation comment: {block!r}"

    @pytest.mark.parametrize("name", _DERIVED)
    def test_each_derivation_cites_a_measurement(self, name: str) -> None:
        block = _derivation_for(name).lower()
        assert any(token in block for token in ("tok/s", "measured", "s /", "review")), block
        assert any(character.isdigit() for character in block), block

    def test_the_module_cites_the_committed_rate_config(self) -> None:
        source = _RUNNER_SRC.read_text(encoding="utf-8")
        assert "timeout-rate-measurements.json" in source

    def test_the_defaults_are_a_deep_thinker_profile(self) -> None:
        """The strategist LAGS the actor, so its results must outlive more steps."""
        assert DEFAULT_MAX_LAG > 5
        assert DEFAULT_REVIEW_GAP >= 1
        assert DEFAULT_MAX_PENDING < 32
        assert DEFAULT_POLL_INTERVAL >= 1.0
        assert DEFAULT_JOIN_TIMEOUT >= 2 * DEFAULT_POLL_INTERVAL
        assert DEFAULT_MAX_FAILED_REVIEWS >= 1


# ── 11. the S107 debt is not inherited ───────────────────────────────────────


class TestNoS107Debt:
    """The five tuning scalars ship behind a default-constructed limits object."""

    def test_the_constructor_is_under_the_thirteen_parameter_limit(self) -> None:
        params = [p for p in inspect.signature(StrategistRunner.__init__).parameters if p != "self"]
        assert len(params) < 13, params

    def test_the_tuning_scalars_are_not_constructor_parameters(self) -> None:
        params = set(inspect.signature(StrategistRunner.__init__).parameters)
        scalars = {
            "max_pending",
            "max_lag",
            "max_failed_reviews",
            "max_failed_sessions",
            "join_timeout",
            "poll_interval",
        }
        assert not (params & scalars), params & scalars
        assert "limits" in params

    def test_the_limits_object_is_default_constructed(self) -> None:
        limits = StrategistLimits()
        assert limits.max_pending == DEFAULT_MAX_PENDING
        assert limits.max_lag == DEFAULT_MAX_LAG
        assert limits.max_failed_reviews == DEFAULT_MAX_FAILED_REVIEWS
        assert limits.join_timeout == DEFAULT_JOIN_TIMEOUT
        assert limits.poll_interval == DEFAULT_POLL_INTERVAL
        assert limits.review_gap == DEFAULT_REVIEW_GAP

    def test_omitting_limits_is_the_same_as_the_default_object(self) -> None:
        with _runner(_Scripted()) as bare, _runner(_Scripted(), limits=StrategistLimits()) as named:
            assert bare.state()["limits"] == named.state()["limits"]

    def test_a_hostile_limits_object_degrades_to_the_defaults(self) -> None:
        class Junk:
            max_pending = "many"
            max_lag = None
            max_failed_reviews = "some"
            join_timeout = "soon"
            poll_interval = "often"
            review_gap = object()

        with _runner(_Scripted(_hold_turn()), limits=Junk()) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert len(runner.drain(step_count=1)) == 1


# ── 12. the authority boundary ───────────────────────────────────────────────


class TestAuthorityBoundary:
    """The runner carries scope; it holds no decision vocabulary at all."""

    def test_no_shape_here_carries_a_decision_field(self) -> None:
        banned = {"decision", "deny", "rewrite", "approve", "allow", "arguments", "tool_calls"}
        for shape in (StrategistLimits,):
            names = {f.name for f in dataclasses.fields(shape)}
            assert not (names & banned), shape.__name__

    def test_the_runner_builds_no_executor(self) -> None:
        source = _RUNNER_SRC.read_text(encoding="utf-8")
        for token in ("ToolExecutor", "execute(", "subprocess", "os.system"):
            assert token not in source, token

    def test_a_delivered_outcome_carries_only_scope(self) -> None:
        with _runner(_Scripted(_directive_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            delivered = runner.drain(step_count=1)
        assert isinstance(delivered[0], ScopeOutcome)
        assert isinstance(delivered[0].directive, ScopeDirective)
        banned = {"tool", "tools", "arguments", "command", "approve"}
        assert not (set(delivered[0].directive.to_dict()) & banned)


# ── 13. every declared code has a producer ───────────────────────────────────


def _record_call_arguments() -> dict[str, list[int]]:
    """``{identifier: [line]}`` for every name passed to ``_record`` / ``_degrade``."""
    tree = ast.parse(_RUNNER_SRC.read_text(encoding="utf-8"))
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called not in {"_record", "_degrade"}:
            continue
        for argument in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(argument, ast.Name):
                found.setdefault(argument.id, []).append(node.lineno)
    return found


class TestEveryDeclaredCodeHasAProducer:
    """Declared vocabulary must be REACHABLE vocabulary (embodiment#18)."""

    @pytest.mark.parametrize(
        "constant",
        [
            "DEGRADED_THREAD",
            "DEGRADED_WORKER",
            "DEGRADED_SEAM",
            "DEGRADED_CLOSER",
            "DROPPED_STALE",
            "DROPPED_LATE",
            "DROPPED_OVERFLOW",
            "DROPPED_SNAPSHOT",
            "DROPPED_SUPERSEDED",
            "DROPPED_CADENCE",
        ],
    )
    def test_the_module_records_it(self, constant: str) -> None:
        producers = _record_call_arguments().get(constant, [])
        assert producers, (
            f"{constant} is declared and exported but nothing in this module records it — "
            "a host branching exhaustively over RUNNER_CODES would get an arm that "
            "cannot fire (embodiment#18)."
        )

    def test_runner_codes_is_exactly_what_this_lane_mints(self) -> None:
        minted = {
            DEGRADED_THREAD,
            DEGRADED_WORKER,
            DEGRADED_SEAM,
            DEGRADED_CLOSER,
            DROPPED_STALE,
            DROPPED_LATE,
            DROPPED_OVERFLOW,
            DROPPED_SNAPSHOT,
            DROPPED_SUPERSEDED,
            DROPPED_CADENCE,
        }
        assert set(RUNNER_CODES) == minted
        assert len(RUNNER_CODES) == len(minted)

    def test_every_code_is_prefixed_for_its_lane(self) -> None:
        for code in RUNNER_CODES:
            assert code.startswith("strategist-"), code

    def test_the_lane_vocabulary_covers_what_it_relays(self) -> None:
        """``_absorb`` copies a review's own scope codes onto this ledger."""
        assert set(RUNNER_CODES) <= set(LANE_CODES)
        assert DEGRADED_REVIEW in LANE_CODES

    def test_the_exported_names_are_harvestable(self) -> None:
        import embodiment.strategist_runner as mod

        harvested = {
            getattr(mod, name) for name in mod.__all__ if name.startswith(("DEGRADED_", "DROPPED_"))
        }
        assert set(LANE_CODES) == harvested


# ── 14. pull-only observability ──────────────────────────────────────────────


class TestPullOnlyObservability:
    """No push surface: no callback, no listener registry, no host code on the thread."""

    def test_the_constructor_takes_no_observer(self) -> None:
        params = set(inspect.signature(StrategistRunner.__init__).parameters)
        assert not (params & {"observer", "on_degradation", "listener", "emit", "sink"})

    def test_state_returns_copies(self) -> None:
        with _runner(_Scripted(_hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            state = runner.state()
            state["counts"]["reviews_started"] = 999
            assert runner.counts["reviews_started"] == 1

    def test_state_names_the_role_and_model(self) -> None:
        with _runner(_Scripted(), role="cortex", model="a/b-c") as runner:
            state = runner.state()
        assert state["role"] == "cortex"
        assert state["model"] == "a/b-c"

    def test_the_active_directive_is_readable(self) -> None:
        with _runner(_Scripted(_directive_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.active_directive is not None
            assert runner.active_directive.scope_id == "scope-001"
            assert isinstance(runner.register, ScopeRegister)

    def test_the_register_holds_what_was_ISSUED_not_what_was_RECEIVED(self) -> None:
        """The one semantic wrinkle a host must not get wrong.

        ``ScopeLoop.review`` admits a directive into the chain on the worker
        thread, before this runner ever sees the outcome. A directive the runner
        then withholds as stale is therefore still the head of the chain —
        which is why a host applies ``drain``'s output and never the register.
        """
        limits = StrategistLimits(max_lag=2, review_gap=0)
        with _runner(_Scripted(_directive_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.drain(step_count=50) == []  # withheld: far too stale
            assert runner.counts["reviews_dropped_stale"] == 1
            # Issued and in the chain all the same.
            assert runner.active_directive is not None
            assert runner.active_directive.scope_id == "scope-001"

    def test_a_host_register_seats_the_default_scope(self) -> None:
        default = ScopeDirective(scope_id="host-default", objective="keep the lights on", version=0)
        register = ScopeRegister(default=default)
        with _runner(_Scripted(), register=register) as runner:
            assert runner.active_directive is not None
            assert runner.active_directive.scope_id == "host-default"


# ── 15. the measurement surface (the reason the muse constant went stale) ────


class TestMeasurement:
    """The lane instruments itself so its own constants can be RE-derived."""

    def test_relative_latency_is_none_without_a_clock(self) -> None:
        with _runner(_Scripted(_hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.state()["relative_latency"] is None

    def test_relative_latency_needs_both_halves(self) -> None:
        ticks = iter([0.0, 60.0, 60.0, 120.0])
        with _runner(_Scripted(_hold_turn()), clock=lambda: next(ticks)) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.state()["relative_latency"] is None
            runner.note_actor_step(30.0)
            assert runner.state()["relative_latency"] == pytest.approx(2.0)

    def test_a_junk_step_duration_is_ignored(self) -> None:
        with _runner(_Scripted()) as runner:
            for junk in ("soon", None, float("nan"), -1.0, 0.0):
                runner.note_actor_step(junk)
            assert runner.state()["relative_latency"] is None

    def test_a_zero_measurement_reports_absent_rather_than_a_ratio(self) -> None:
        """A clock that measured 0.0 is not a latency; dividing by it would invent one."""
        ticks = iter([5.0, 5.0])
        with _runner(_Scripted(_hold_turn()), clock=lambda: next(ticks)) as runner:
            _one_review(runner, _snapshot(), step=1)
            runner.note_actor_step(30.0)
            assert runner.state()["relative_latency"] is None

    def test_a_junk_limits_interval_falls_back_to_the_derived_default(self) -> None:
        """The non-finite and non-positive arms of the float coercion."""

        class Junk:
            poll_interval = float("nan")
            join_timeout = -1.0

        with _runner(_Scripted(), limits=Junk()) as runner:
            limits = runner.state()["limits"]
        assert limits["poll_interval"] == DEFAULT_POLL_INTERVAL
        assert limits["join_timeout"] == DEFAULT_JOIN_TIMEOUT

    def test_the_observed_lag_is_reported(self) -> None:
        limits = StrategistLimits(max_lag=0, review_gap=0)
        with _runner(_Scripted(_hold_turn(), _hold_turn()), limits=limits) as runner:
            _one_review(runner, _snapshot(snapshot_id="s-1"), step=1)
            runner.drain(step_count=4)
            _one_review(runner, _snapshot(snapshot_id="s-2"), step=5)
            runner.drain(step_count=6)
            state = runner.state()
        assert state["max_observed_lag"] == 3
        assert state["mean_observed_lag"] == pytest.approx(2.0)

    def test_tool_rounds_are_summed(self) -> None:
        with _runner(_Scripted(_hold_turn())) as runner:
            _one_review(runner, _snapshot(), step=1)
            assert runner.counts["tool_rounds"] == 0


# ── 16. explicit configuration — a role NAME, never a model name ─────────────


class TestExplicitConfiguration:
    """Roles resolve BY NAME from the host's own configuration, never inferred."""

    def test_the_constructor_takes_no_endpoint(self) -> None:
        params = set(inspect.signature(StrategistRunner.__init__).parameters)
        assert not (params & {"endpoint", "url", "base_url", "api_key", "port"})
        assert "complete" in params
        assert "role" in params

    def test_the_module_names_no_model_and_sniffs_no_name(self) -> None:
        source = _RUNNER_SRC.read_text(encoding="utf-8").lower()
        for token in ("qwen", "gemma", "gpt-", "llama", "nvfp4", "openai", "vllm"):
            assert token not in source, token
        for token in ("startswith(", "model_name", "infer_role", "guess"):
            assert token not in source, token

    def test_the_role_is_a_name_the_host_supplies(self) -> None:
        with _runner(_Scripted(), role="strategy") as runner:
            assert runner.role == "strategy"
        with _runner(_Scripted()) as runner:
            assert runner.role == STRATEGIST_ROLE == "strategist"

    def test_the_model_is_host_declared_and_empty_by_default(self) -> None:
        """An unconfigured lane names no model — a single-model run claims nothing."""
        with _runner(_Scripted()) as runner:
            assert runner.model == ""

    def test_controls_and_a_bench_pass_through_to_the_loop(self) -> None:
        controls = ScopeControls(max_turns=1)
        bench = ScopeToolBench()
        with _runner(_Scripted(_hold_turn()), controls=controls, tools=bench) as runner:
            _one_review(runner, _snapshot(), step=1)
            delivered = runner.drain(step_count=1)
        assert delivered[0].exit_reason == SCOPE_EXIT_UNCHANGED

    def test_relayed_scope_degradations_reach_this_ledger(self) -> None:
        with _runner(_Scripted(RuntimeError("dead port"))) as runner:
            _one_review(runner, _snapshot(), step=1)
        codes = [d.code for d in runner.degradations]
        assert DEGRADED_REVIEW in codes
        assert all(isinstance(d, ScopeDegradation) for d in runner.degradations)
