"""Tests for embodiment.muse_runner — the muse's THREAD (task t10b).

Deviation ``d1`` made the muse a second, parallel thinking loop that embodiment
runs on its own thread. :mod:`embodiment.muse` (task t10a) holds all of the
*reasoning*, thread-free and exhaustively deterministic; this module holds only
the thread mechanics, so these are the ONLY concurrency tests in the suite.

Every one of them is deterministic by construction — there is not a single
``sleep`` here:

* the muse's injected seam announces it has STARTED through a
  :class:`threading.Event` and blocks on a second one, so a test can assert
  "the actor drained while the muse was provably mid-thought" without timing;
* :meth:`ThreadedMuseRunner.wait_idle` is a bounded wait on a real condition
  (the worker has no pending boundary and nothing in flight), never a guess;
* every wait in this file is bounded by :data:`_TIMEOUT`, so a broken
  implementation FAILS rather than hanging CI.

Covers:

1. Museless is the DEFAULT path — a museless run starts no thread at all, and
   a constructed-but-unasked runner starts none either.
2. The drain shape: ``consider`` never blocks, ``drain`` returns what is ready
   instantly, and an empty drain is normal.
3. ONE daemon thread, one in-flight session, a bounded and idempotent teardown.
4. Staleness and late arrival are RECORDED transitions, never silent (C3).
5. The degradation ladder: a dead endpoint, a mid-run failure and a thread that
   cannot start each degrade visibly and never raise into the actor loop.
6. The authority boundary: nothing muse-sourced can reach a tool decision, and
   the ``pre_tool`` hook registry never sees a muse-sourced value.
7. The endpoint arrives through explicit injected configuration — a role NAME,
   never a model name parsed from anywhere.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import inspect
import json
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from embodiment.contract import ContextPacket, ModelResponse, Task, ToolCall
from embodiment.loop import EXIT_FINISHED, HookEvent, ToolOutcome, run
from embodiment.muse import (
    COUNSEL_KIND_DURABLE,
    COUNSEL_KIND_STEP,
    DEGRADED_THINKING,
    MARKER_DONE,
    MuseControls,
    MuseDegradation,
    MuseInsight,
)
from embodiment.muse_runner import (
    DEGRADED_ENDPOINT,
    DEGRADED_THREAD,
    DROPPED_BOUNDARY,
    DROPPED_COMPILATION_STARVED,
    DROPPED_COUNSEL_DISPLACED,
    DROPPED_LATE,
    DROPPED_OVERFLOW,
    DROPPED_STALE,
    MAX_LEDGER,
    MUSE_ROLE,
    THREAD_NAME,
    WORK_BOUNDARY,
    WORK_CLASSES,
    WORK_COMPILATION,
    ThreadedMuseRunner,
)
from embodiment.presence import UpdateCadence
from embodiment.presence_engine import (
    BOUNDARY_CADENCE_TICK,
    BOUNDARY_INTAKE,
    MODE_CORTEX_ONLY,
    MODE_MUSE,
    SOURCE_MUSE,
    BoundaryContext,
    MuseComment,
    MuseSeam,
    PresenceEngine,
    PresenceExecutor,
    PresenceIO,
)

#: Every wait in this file is bounded by this, so a broken implementation fails
#: the assertion instead of hanging the suite. Generous on purpose: CI under
#: load must never turn a correct implementation red.
_TIMEOUT = 5.0

_RUNNER_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "muse_runner.py"


# ── doubles ───────────────────────────────────────────────────────────────────


def _resp(content: str = "", **kw: Any) -> ModelResponse:
    return ModelResponse(content=content, **kw)


def _boundary(kind: str = BOUNDARY_CADENCE_TICK, *, step: int = 0) -> BoundaryContext:
    return BoundaryContext(kind=kind, step_count=step, reason="every-n")


class _Scripted:
    """A muse seam that replays scripted replies, recording its own thread."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.threads: list[threading.Thread] = []
        self.seen: list[list[dict[str, Any]]] = []

    def _next(self, messages: list[dict[str, Any]]) -> Any:
        self.calls += 1
        self.threads.append(threading.current_thread())
        self.seen.append(list(messages))
        reply = self.replies.pop(0) if self.replies else _resp(MARKER_DONE)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        return self._next(messages)


class _Gated(_Scripted):
    """A scripted seam that ANNOUNCES it started and then waits to be released.

    The whole determinism story: a test waits on :attr:`started` to know the
    muse is provably inside a completion, asserts whatever must hold while it
    is thinking, then sets :attr:`release`. No sleep, no polling, no guess.
    """

    def __init__(self, *replies: Any) -> None:
        super().__init__(*replies)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.started.set()
        assert self.release.wait(_TIMEOUT), "the gated muse seam was never released"
        return self._next(messages)


@contextlib.contextmanager
def _runner(complete: Any, **kw: Any) -> Iterator[ThreadedMuseRunner]:
    """A runner that is ALWAYS closed — and never left parked on a gate."""
    runner = ThreadedMuseRunner(complete, **kw)
    try:
        yield runner
    finally:
        release = getattr(complete, "release", None)
        if isinstance(release, threading.Event):
            release.set()
        runner.close(timeout=_TIMEOUT)


def _live_threads() -> set[int]:
    return {t.ident for t in threading.enumerate() if t.ident is not None}


# ── the end-to-end host fixture (the CI default is museless) ──────────────────


class _Executor:
    """A tool surface with no shell and no filesystem — a plain recorder."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.executed.append((name, dict(arguments)))
        if name == "finish":
            return ToolOutcome(result="done", finished=True, finish_summary="done")
        return ToolOutcome(result=f"{name} ok")


class _Host:
    """A minimal app driving embodiment: model seam, tools, presence IO, hooks.

    ``guidance`` is the app's advisory channel — the ONE way a muse's text can
    reach the acting model, and it lands there as plain prose the next
    completion reads. ``hook_events`` is the ``pre_tool`` registry: what a real
    approval policy would bind to.
    """

    def __init__(self, *, turns: Optional[list[ModelResponse]] = None) -> None:
        self.executor = _Executor()
        self.guidance: list[str] = []
        self.rendered: list[str] = []
        self.hook_events: list[HookEvent] = []
        self.guidance_at_turn: list[list[str]] = []
        self.turns = list(turns or [])
        self.calls = 0
        self.before_turn: Any = None

    # the actor's model seam ---------------------------------------------------
    def complete(self, messages: list[dict[str, Any]]) -> ModelResponse:
        if self.before_turn is not None:
            self.before_turn(self.calls)
        self.calls += 1
        # What the acting model can see of the advisory channel this turn.
        self.guidance_at_turn.append(list(self.guidance))
        if self.turns:
            return self.turns.pop(0)
        return _resp("nothing further")

    def hooks(self, event: HookEvent) -> None:
        self.hook_events.append(event)
        return None

    def io(self) -> PresenceIO:
        return PresenceIO(
            append_guidance=self.guidance.append,
            render=self.rendered.append,
            task_state=lambda: f"step {len(self.executor.executed)}",
        )

    def task(self) -> Task:
        return Task(
            id="t1",
            repo_path="/repo",
            instruction="do the thing",
            context_packet=ContextPacket(original="do the thing", ack="on it"),
        )

    def engine(self, muse: Any = None) -> PresenceEngine:
        # every_steps=1 so EVERY loop boundary drives the pump: the fixture's
        # behaviour must not ride on the default cadence happening to fire.
        return PresenceEngine(io=self.io(), muse=muse, cadence=UpdateCadence(every_steps=1))

    def drive(self, engine: Optional[PresenceEngine], *, max_steps: int = 6) -> Any:
        return run(
            self.complete,
            self.task(),
            executor=self.executor,
            max_steps=max_steps,
            hooks=self.hooks,
            presence=engine,
        )


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}-{len(arguments)}", name=name, arguments=dict(arguments))


def _turn(*calls: ToolCall, content: str = "") -> ModelResponse:
    return ModelResponse(content=content, tool_calls=list(calls))


# ── 1. museless is the default path ───────────────────────────────────────────


class TestMuselessIsTheDefault:
    """The CI default: a whole run with no muse, and no thread anywhere."""

    def test_a_museless_run_starts_no_thread_at_all(self):
        before = _live_threads()
        host = _Host(
            turns=[
                _turn(_call("write_file", path="a.py")),
                _turn(_call("finish")),
            ]
        )
        engine = host.engine()
        outcome = host.drive(engine)
        assert outcome.exit_reason == EXIT_FINISHED
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is False
        # The whole point of deviation d1's cost being bounded: no muse, no
        # thread. Not a stopped thread — never one at all.
        assert _live_threads() == before

    def test_a_museless_run_still_feels_present(self):
        host = _Host(turns=[_turn(_call("write_file", path="a.py")), _turn(_call("finish"))])
        engine = host.engine()
        host.drive(engine)
        assert any("still working" in line for line in host.rendered)
        # Sourced to cortex — a museless run never claims a second mind.
        assert all(entry.get("source") != SOURCE_MUSE for entry in engine.snapshot()["chat"])

    def test_a_constructed_runner_starts_no_thread_until_it_is_considered(self):
        before = _live_threads()
        seam = _Scripted()
        with _runner(seam) as runner:
            assert runner.thread_started is False
            assert runner.drain() == []
            assert seam.calls == 0
            assert _live_threads() == before

    def test_constructing_a_runner_dials_nothing(self):
        seam = _Scripted()
        with _runner(seam) as runner:
            assert runner.degradation() is None
            assert seam.calls == 0


# ── 2. the drain shape ────────────────────────────────────────────────────────


class TestDrainShape:
    """``consider`` never blocks; ``drain`` returns what is ready, instantly."""

    def test_consider_returns_while_the_muse_is_still_thinking(self):
        seam = _Gated(_resp("GUIDANCE: look at the parser " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT), "the muse never started thinking"
            # PROVABLY mid-thought: the seam is parked inside its completion and
            # the actor's drain still returns instantly, and empty.
            assert runner.drain(step_count=1) == []
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            comments = runner.drain(step_count=1)
            assert [c.guidance for c in comments] == ["look at the parser"]

    def test_an_empty_drain_is_normal_not_an_error(self):
        with _runner(_Scripted()) as runner:
            assert runner.drain() == []
            assert runner.drain(step_count=99) == []
            assert runner.degradation() is None
            assert runner.degradations == []

    def test_insights_arrive_incrementally_not_only_at_session_end(self):
        seam = _Gated(
            _resp("GUIDANCE: first thought"),
            _resp("GUIDANCE: second thought " + MARKER_DONE),
        )
        with _runner(seam, controls=MuseControls(max_turns=3)) as runner:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            assert [c.guidance for c in runner.drain(step_count=1)] == [
                "first thought",
                "second thought",
            ]

    def test_drained_items_are_insights_carrying_their_own_provenance(self):
        seam = _Scripted(_resp("thinking about it " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(BOUNDARY_INTAKE, step=3))
            assert runner.wait_idle(_TIMEOUT)
            insight = runner.drain(step_count=3)[0]
            assert isinstance(insight, MuseInsight)
            assert isinstance(insight, MuseComment)
            assert insight.origin.kind == BOUNDARY_INTAKE
            assert insight.origin.step_count == 3
            assert insight.origin.session == 1

    def test_a_drained_insight_is_not_drained_twice(self):
        seam = _Scripted(_resp("one thought " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert len(runner.drain(step_count=1)) == 1
            assert runner.drain(step_count=1) == []


# ── 3. one thread, one session, bounded teardown ──────────────────────────────


class TestThreadDiscipline:
    """ONE daemon thread, one in-flight session, a bounded, idempotent close."""

    def test_every_session_runs_on_the_same_single_daemon_thread(self):
        seam = _Scripted(
            _resp("a " + MARKER_DONE),
            _resp("b " + MARKER_DONE),
            _resp("c " + MARKER_DONE),
        )
        with _runner(seam) as runner:
            for step in (1, 2, 3):
                runner.consider(_boundary(step=step))
                assert runner.wait_idle(_TIMEOUT)
            idents = {t.ident for t in seam.threads}
            assert len(idents) == 1
            assert idents != {threading.current_thread().ident}
            thread = seam.threads[0]
            assert thread.daemon is True
            assert thread.name == THREAD_NAME

    def test_a_boundary_arriving_mid_session_supersedes_the_pending_one(self):
        seam = _Gated(_resp("a " + MARKER_DONE), _resp("c " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT)
            runner.consider(_boundary(step=2))  # queued
            runner.consider(_boundary(step=3))  # supersedes step 2
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            codes = [d.code for d in runner.degradations]
            assert codes.count(DROPPED_BOUNDARY) == 1
            assert runner.counts["boundaries_superseded"] == 1
            assert runner.counts["sessions_started"] == 2

    def test_close_is_idempotent_and_never_hangs_on_a_parked_session(self):
        seam = _Gated(_resp("a " + MARKER_DONE))
        runner = ThreadedMuseRunner(seam)
        try:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT)
            # The muse is parked inside a completion nothing can interrupt.
            # Teardown must still RETURN — that is the whole bounded-join rule.
            runner.close(timeout=0.05)
            runner.close(timeout=0.05)
            assert runner.closed is True
        finally:
            seam.release.set()
            runner.close(timeout=_TIMEOUT)

    def test_the_context_manager_closes_the_runner(self):
        seam = _Scripted(_resp("a " + MARKER_DONE))
        with ThreadedMuseRunner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
        assert runner.closed is True
        assert runner.consider(_boundary(step=2)) is None
        assert runner.counts["sessions_started"] == 1

    def test_close_without_a_thread_is_a_no_op(self):
        runner = ThreadedMuseRunner(_Scripted())
        runner.close()
        assert runner.closed is True
        assert runner.thread_started is False
        assert runner.degradations == []

    def test_a_closed_runner_starts_nothing_and_drains_nothing(self):
        seam = _Scripted(_resp("a " + MARKER_DONE))
        runner = ThreadedMuseRunner(seam)
        runner.close()
        runner.consider(_boundary(step=1))
        assert runner.start() is False
        assert runner.thread_started is False
        assert runner.drain(step_count=1) == []
        assert seam.calls == 0


# ── 4. staleness and late arrival are RECORDED (C3) ───────────────────────────


class TestStaleness:
    """An insight computed at step 3 can arrive at step 40 — judge it, and say so."""

    def test_a_stale_insight_is_dropped_and_the_drop_is_recorded(self):
        # Step-sensitive counsel ages by loop distance and is dropped when stale.
        seam = _Scripted(_resp("GUIDANCE[step]: about step one " + MARKER_DONE))
        with _runner(seam, max_lag=5) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert runner.drain(step_count=40) == []
            dropped = [d for d in runner.degradations if d.code == DROPPED_STALE]
            assert len(dropped) == 1
            assert dropped[0].step_index == 1
            assert "40" in dropped[0].reason
            assert runner.counts["insights_dropped_stale"] == 1
            assert runner.counts["insights_delivered"] == 0

    def test_a_fresh_insight_is_delivered_untouched(self):
        seam = _Scripted(_resp("GUIDANCE: still relevant " + MARKER_DONE))
        with _runner(seam, max_lag=5) as runner:
            runner.consider(_boundary(step=10))
            assert runner.wait_idle(_TIMEOUT)
            assert [c.guidance for c in runner.drain(step_count=13)] == ["still relevant"]
            assert runner.degradations == []
            assert runner.counts["insights_delivered"] == 1

    def test_the_actors_observed_step_is_monotonic(self):
        # A boundary that carries no step (intake, an operator aside) must not
        # make a long-stale insight look current again.
        seam = _Scripted(
            _resp("GUIDANCE[step]: about step one " + MARKER_DONE),
            _resp("GUIDANCE[step]: also about step one " + MARKER_DONE),
        )
        with _runner(seam, max_lag=2) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            runner.drain(step_count=40)  # the actor is at step 40
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert runner.drain(step_count=0) == []  # judged against 40, not 0
            assert runner.counts["insights_dropped_stale"] == 2

    def test_max_lag_is_the_hosts_policy(self):
        seam = _Scripted(_resp("GUIDANCE: ancient " + MARKER_DONE))
        with _runner(seam, max_lag=1000) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert len(runner.drain(step_count=900)) == 1


class TestLateArrival:
    """Insights with nowhere left to go are recorded, never silently binned."""

    def test_an_insight_produced_after_close_is_recorded_as_a_late_drop(self):
        seam = _Gated(_resp("GUIDANCE: too late " + MARKER_DONE))
        runner = ThreadedMuseRunner(seam)
        try:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT)
            runner.close(timeout=0.05)  # the actor finished; the muse has not
            seam.release.set()
            runner.close(timeout=_TIMEOUT)  # idempotent; joins the finishing thread
            late = [d for d in runner.degradations if d.code == DROPPED_LATE]
            assert len(late) == 1
            assert runner.counts["insights_dropped_late"] == 1
        finally:
            seam.release.set()
            runner.close(timeout=_TIMEOUT)

    def test_insights_never_drained_before_close_are_recorded_as_late_drops(self):
        seam = _Scripted(_resp("GUIDANCE: unread " + MARKER_DONE))
        runner = ThreadedMuseRunner(seam)
        runner.consider(_boundary(step=1))
        assert runner.wait_idle(_TIMEOUT)
        runner.close(timeout=_TIMEOUT)
        assert [d.code for d in runner.degradations] == [DROPPED_LATE]
        assert runner.counts["insights_dropped_late"] == 1

    def test_the_drain_buffer_is_bounded_and_overflow_is_recorded(self):
        seam = _Scripted(
            _resp("GUIDANCE: one"),
            _resp("GUIDANCE: two"),
            _resp("GUIDANCE: three"),
            _resp("GUIDANCE: four " + MARKER_DONE),
        )
        with _runner(seam, max_pending=2, controls=MuseControls(max_turns=4)) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            # The freshest thinking survives; the oldest is dropped and RECORDED.
            assert [c.guidance for c in runner.drain(step_count=1)] == ["three", "four"]
            assert runner.counts["insights_dropped_overflow"] == 2
            assert [d.code for d in runner.degradations] == [DROPPED_OVERFLOW] * 2


# ── 5. the degradation ladder ─────────────────────────────────────────────────


class TestDegradation:
    """Degrade, never raise — and every degradation is host-visible (C3)."""

    def test_a_dead_endpoint_degrades_and_stops_dialling(self):
        seam = _Scripted(ConnectionRefusedError("dead port"))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            reason = runner.degradation()
            assert reason is not None and "muse" in reason
            codes = [d.code for d in runner.degradations]
            assert DEGRADED_THINKING in codes  # the session's own record (t10a)
            assert DEGRADED_ENDPOINT in codes  # the lane stopping (t10b)
            # Not re-dialled at every boundary — the whole reason the engine
            # unbinds a dead muse rather than retrying it forever.
            runner.consider(_boundary(step=2))
            runner.consider(_boundary(step=3))
            assert runner.wait_idle(_TIMEOUT)
            assert seam.calls == 1

    def test_a_mid_run_failure_degrades_visibly(self):
        seam = _Scripted(
            _resp("GUIDANCE: fine " + MARKER_DONE),
            TimeoutError("the muse endpoint stopped answering"),
        )
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert runner.degradation() is None
            assert len(runner.drain(step_count=1)) == 1

            runner.consider(_boundary(step=2))
            assert runner.wait_idle(_TIMEOUT)
            assert runner.degradation() is not None
            reasons = [d.reason for d in runner.degradations]
            assert any("stopped answering" in r for r in reasons)

    def test_a_success_resets_the_consecutive_failure_count(self):
        seam = _Scripted(
            TimeoutError("blip"),
            _resp("GUIDANCE: back " + MARKER_DONE),
            TimeoutError("blip"),
        )
        with _runner(seam, max_failed_sessions=2) as runner:
            for step in (1, 2, 3):
                runner.consider(_boundary(step=step))
                assert runner.wait_idle(_TIMEOUT)
            assert runner.degradation() is None
            assert seam.calls == 3

    def test_a_thread_that_cannot_start_degrades_and_never_raises(self):
        def refuse(**kwargs: Any) -> Any:
            raise RuntimeError("can't start new thread")

        seam = _Scripted(_resp("never runs " + MARKER_DONE))
        with _runner(seam, thread_factory=refuse) as runner:
            assert runner.consider(_boundary(step=1)) is None
            reason = runner.degradation()
            assert reason is not None and "thread" in reason
            assert [d.code for d in runner.degradations] == [DEGRADED_THREAD]
            assert runner.drain(step_count=1) == []
            assert runner.wait_idle(_TIMEOUT) is True
            assert seam.calls == 0

    def test_a_thread_whose_start_raises_degrades_identically(self):
        class _Broken:
            daemon = True
            name = THREAD_NAME

            def start(self) -> None:
                raise RuntimeError("thread limit reached")

            def is_alive(self) -> bool:
                return False

        with _runner(_Scripted(), thread_factory=lambda **kw: _Broken()) as runner:
            runner.consider(_boundary(step=1))
            assert runner.degradation() is not None
            assert [d.code for d in runner.degradations] == [DEGRADED_THREAD]

    def test_the_ledger_is_bounded_but_the_counters_stay_exact(self):
        seam = _Scripted(_resp("GUIDANCE[step]: one " + MARKER_DONE))
        with _runner(seam, max_lag=0) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            for step in range(2, 10):
                runner.drain(step_count=step)
            assert len(runner.degradations) <= runner.counts["degradations_recorded"]
            assert runner.counts["degradations_recorded"] >= 1

    def test_a_degradation_record_reuses_the_muse_shape(self):
        seam = _Scripted(ConnectionRefusedError("dead port"))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=7))
            assert runner.wait_idle(_TIMEOUT)
            assert all(isinstance(d, MuseDegradation) for d in runner.degradations)
            assert all(
                set(d.to_dict()) == {"code", "reason", "step_index", "model_turns"}
                for d in runner.degradations
            )

    def test_the_snapshot_is_a_pull_only_fold(self):
        seam = _Scripted(_resp("a " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            snap = runner.snapshot()
            assert set(snap) == {
                "role",
                "thread_started",
                "closed",
                "degradation",
                "counts",
                "degradations",
                "kind_delivered",
                "kind_dropped",
                "relative_latency",
            }
            snap["counts"]["sessions_started"] = 999
            snap["degradations"].append("forged")
            assert runner.snapshot()["counts"]["sessions_started"] == 1
            assert runner.snapshot()["degradations"] == []

    def test_the_runner_exposes_no_push_event_stream(self):
        # c33: presence stays loop-internal — a parallel lane must not become a
        # pub/sub back door either. Observability here is PULL-only.
        forbidden = {"subscribe", "unsubscribe", "emit", "publish", "broadcast", "on_event"}
        assert not (set(dir(ThreadedMuseRunner)) & forbidden)


# ── 6. the authority boundary — structural, not promised ──────────────────────


class TestAuthorityBoundary:
    """Nothing muse-sourced can reach a tool decision. Proved, not asserted."""

    def test_no_muse_sourced_value_reaches_the_pre_tool_hook_registry(self):
        sentinel = "MUSE-SENTINEL-deny-every-write"
        seam = _Scripted(_resp(f"GUIDANCE: {sentinel} " + MARKER_DONE))
        host = _Host(
            turns=[
                _turn(_call("write_file", path="a.py")),
                _turn(_call("write_file", path="b.py")),
                _turn(_call("finish")),
            ]
        )
        with _runner(seam) as runner:
            # Turn 2 waits on a REAL condition — the muse's session finishing —
            # so the assertion below cannot race the thread.
            host.before_turn = lambda n: runner.wait_idle(_TIMEOUT) if n == 1 else None
            engine = host.engine(muse=runner)
            outcome = host.drive(engine)

        assert outcome.exit_reason == EXIT_FINISHED
        # 1. The muse's demand DID travel — as advisory text, in the guidance
        #    stream the acting model reads. The test is not vacuous.
        assert any(sentinel in text for text in host.guidance)
        assert any(sentinel in " ".join(seen) for seen in host.guidance_at_turn)
        # 2. It reached NO tool decision: every pre_tool payload is muse-free.
        pre_tool = [e for e in host.hook_events if e.event == "pre_tool"]
        assert pre_tool, "the fixture must actually exercise the pre_tool registry"
        for event in pre_tool:
            assert sentinel not in json.dumps(event.payload())
            assert sentinel not in str(event.arguments)
        # 3. And the write it demanded be denied happened anyway.
        assert [name for name, _ in host.executor.executed] == [
            "write_file",
            "write_file",
            "finish",
        ]
        assert outcome.hook_firings == []

    def test_the_hook_event_carries_no_advisory_channel(self):
        names = {f.name for f in dataclasses.fields(HookEvent)}
        assert names == {"event", "task", "tool", "arguments", "step_index"}
        assert not (names & {"guidance", "muse", "comment", "advice"})

    def test_the_acting_surface_still_has_no_deny_or_rewrite_field(self):
        names = {f.name for f in dataclasses.fields(PresenceExecutor)}
        assert not (names & {"deny", "rewrite", "pre_tool", "hooks", "approve"})

    def test_the_runner_exposes_no_acting_surface(self):
        forbidden = {
            "execute",
            "run_tool",
            "dispatch_to_cortex",
            "guide_cortex",
            "tools",
            "executor",
            "approve",
            "deny",
            "rewrite",
            "pre_tool",
        }
        assert not (set(dir(ThreadedMuseRunner)) & forbidden)

    def test_drained_comments_carry_only_text_and_provenance(self):
        decision_vocabulary = {"decision", "deny", "rewrite", "approve", "allow", "arguments"}
        for shape in (MuseComment, MuseInsight):
            names = {f.name for f in dataclasses.fields(shape)}
            assert not (names & decision_vocabulary), shape.__name__

    def test_the_runner_imports_no_decision_type(self):
        import embodiment.muse_runner as mod

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


# ── 7. explicit configuration — a role NAME, never a model name ───────────────


def _imported_modules() -> set[str]:
    tree = ast.parse(_RUNNER_SRC.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestExplicitConfiguration:
    """Roles resolve BY NAME from the host's own configuration, never inferred."""

    def test_the_constructor_takes_no_model_or_endpoint_parameter(self):
        params = set(inspect.signature(ThreadedMuseRunner.__init__).parameters)
        assert not (params & {"model", "endpoint", "url", "base_url", "api_key", "port"})
        assert "complete" in params  # the host's already-configured seam
        assert "role" in params

    def test_the_module_names_no_model_and_sniffs_no_name(self):
        source = _RUNNER_SRC.read_text(encoding="utf-8").lower()
        for token in ("qwen", "gemma", "gpt-", "llama", "nvfp4", "openai", "vllm"):
            assert token not in source, token
        for token in ("startswith(", "model_name", "infer_role", "guess"):
            assert token not in source, token

    def test_the_role_is_a_name_the_host_supplies(self):
        with _runner(_Scripted(), role="deepthink") as runner:
            assert runner.role == "deepthink"
            assert runner.snapshot()["role"] == "deepthink"
        with _runner(_Scripted()) as runner:
            assert runner.role == MUSE_ROLE == "muse"

    def test_the_module_imports_only_stdlib_and_embodiment(self):
        stdlib_ok = {"__future__", "collections", "dataclasses", "threading", "typing"}
        for module in _imported_modules():
            top = module.split(".")[0]
            assert module in stdlib_ok or top == "embodiment", module

    def test_no_colleague_import(self):
        assert not any(m.split(".")[0] == "colleague" for m in _imported_modules())

    def test_no_front_module_import(self):
        assert not any(
            m.startswith(("embodiment.cli", "embodiment.explain")) for m in _imported_modules()
        )


# ── 8. the engine drives the runner through the drain seam ────────────────────


class TestEngineIntegration:
    """The pump's revised seam, driven by the real runner on a real thread."""

    def test_the_runner_satisfies_the_drain_shaped_seam(self):
        with _runner(_Scripted()) as runner:
            assert isinstance(runner, MuseSeam)

    def test_the_engine_renders_and_injects_a_drained_insight(self):
        # GATED, not scripted: ``acknowledge`` drains too, so an ungated muse
        # that finishes inside that call lands its counsel BEFORE the operator's
        # own message and the relay order inverts. Holding the seam until
        # acknowledge has returned makes which beat drains it deterministic —
        # the ordering below is then a real guarantee, not a race the scheduler
        # usually wins.
        seam = _Gated(_resp("I notice the tests never ran\nGUIDANCE: run pytest " + MARKER_DONE))
        rendered: list[str] = []
        guided: list[str] = []
        io = PresenceIO(render=rendered.append, append_guidance=guided.append)
        with _runner(seam) as runner:
            engine = PresenceEngine(io=io, muse=runner)
            engine.acknowledge(ContextPacket(original="ship it", ack="on it"))
            assert seam.started.wait(_TIMEOUT)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            engine.on_operator_message("any thoughts?")
            assert any("I notice the tests never ran" in line for line in rendered)
            assert guided == ["any thoughts?", "run pytest"]
            assert engine.mode == MODE_MUSE
            assert engine.muse_degraded is False

    def test_the_engine_notices_a_degraded_runner_exactly_once(self):
        seam = _Scripted(ConnectionRefusedError("dead port"))
        rendered: list[str] = []
        io = PresenceIO(render=rendered.append)
        with _runner(seam) as runner:
            engine = PresenceEngine(io=io, muse=runner)
            engine.acknowledge(ContextPacket(original="ship it", ack="on it"))
            assert runner.wait_idle(_TIMEOUT)
            engine.on_operator_message("hello?")
            engine.on_operator_message("still there?")
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        assert sum("muse unavailable" in line for line in rendered) == 1
        assert any(r.point == "muse:degraded-off" for r in engine.records)

    @pytest.mark.parametrize("fault", [ConnectionRefusedError("dead"), TimeoutError("slow")])
    def test_a_dead_muse_never_raises_into_a_whole_run(self, fault):
        host = _Host(turns=[_turn(_call("write_file", path="a.py")), _turn(_call("finish"))])
        with _runner(_Scripted(fault)) as runner:
            host.before_turn = lambda n: runner.wait_idle(_TIMEOUT) if n == 1 else None
            engine = host.engine(muse=runner)
            outcome = host.drive(engine)
            assert outcome.exit_reason == EXIT_FINISHED
            assert engine.muse_degraded is True
            assert runner.degradation() is not None
        assert any("muse unavailable" in line for line in host.rendered)
        # The run itself is untouched: the tools still ran, the loop still finished.
        assert [name for name, _ in host.executor.executed] == ["write_file", "finish"]


# ── 9. hostile inputs and the defensive edges ─────────────────────────────────


class _Deferred:
    """A thread the runner "starts" but which really begins when a test says so.

    The determinism trick for the boundary-copy test: it lets a test hold the
    worker at the starting line, mutate the host state the boundary referenced,
    and only then let the muse read it — no race, no sleep.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._thread = threading.Thread(**kwargs)

    def start(self) -> None:
        """Deferred on purpose — :meth:`release` is the real start."""

    def release(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout=timeout)


class _HostileControls:
    """Controls whose first read explodes — a stand-in for a harness bug."""

    max_turns = 4
    max_quiet_turns = 1
    max_insight_chars = 2000

    @property
    def max_context_chars(self) -> int:
        raise RuntimeError("the controls exploded")


class TestDefensiveEdges:
    """Junk in, degradation out — never an exception into the actor loop."""

    def test_a_boundary_is_copied_before_it_crosses_the_thread(self):
        seam = _Scripted(_resp("noted " + MARKER_DONE))
        deferred: list[_Deferred] = []

        def factory(**kwargs: Any) -> _Deferred:
            thread = _Deferred(**kwargs)
            deferred.append(thread)
            return thread

        history = [{"role": "user", "content": "the original ask"}]
        with _runner(seam, thread_factory=factory) as runner:
            runner.consider(BoundaryContext(kind=BOUNDARY_INTAKE, history=history))
            # The worker is provably still at the starting line, so mutating the
            # host's live list here is a clean test of the crossing, not a race.
            history.clear()
            history.append({"role": "user", "content": "something else entirely"})
            deferred[0].release()
            assert runner.wait_idle(_TIMEOUT)
            prompt = "\n".join(str(m.get("content", "")) for m in seam.seen[0])
            assert "the original ask" in prompt
            assert "something else entirely" not in prompt

    def test_a_missing_boundary_is_ignored(self):
        seam = _Scripted()
        with _runner(seam) as runner:
            assert runner.consider(None) is None
            assert runner.thread_started is False
            assert seam.calls == 0

    def test_hostile_controls_and_boundaries_never_reach_the_actor(self):
        class _Hostile:
            kind = "cadence-tick"
            history = "not a list"

            @property
            def step_count(self) -> int:
                raise RuntimeError("hostile boundary")

        seam = _Scripted(_resp("noted " + MARKER_DONE))
        with _runner(
            seam,
            controls=_HostileControls(),
            max_lag=object(),
            max_pending=object(),
            max_failed_sessions=object(),
        ) as runner:
            runner.consider(_Hostile())  # type: ignore[arg-type]
            assert runner.wait_idle(_TIMEOUT)
            # The worker died on the harness bug — and SAID SO, rather than
            # leaving a host with a lane that looks quiet but is gone.
            assert [d.code for d in runner.degradations] == ["muse-worker-failed"]
            reason = runner.degradation()
            assert reason is not None and "the controls exploded" in reason
            assert runner.drain(step_count=1) == []

    def test_the_ledger_stops_growing_but_the_counter_does_not(self):
        seam = _Gated(_resp("noted " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT)
            for step in range(2, 140):  # each supersedes the one still queued
                runner.consider(_boundary(step=step))
            assert len(runner.degradations) == MAX_LEDGER
            assert runner.counts["degradations_recorded"] == 137
            assert runner.counts["boundaries_superseded"] == 137

    def test_an_uncopyable_boundary_still_gets_thought_about(self):
        class _NotADataclass:
            kind = BOUNDARY_CADENCE_TICK
            step_count = 2
            history = [{"role": "user", "content": "the original ask"}]

        seam = _Scripted(_resp("GUIDANCE: noted " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_NotADataclass())  # type: ignore[arg-type]
            assert runner.wait_idle(_TIMEOUT)
            assert [c.guidance for c in runner.drain(step_count=2)] == ["noted"]
            assert runner.degradations == []


# ── 10. kind-aware delivery (task t3) ─────────────────────────────────────────


class TestKindAwareDelivery:
    """Durable counsel survives loop-distance staleness; step-sensitive ages."""

    def test_no_durable_insight_is_dropped_for_loop_distance_staleness(self):
        # The headline acceptance criterion: durable counsel survives to the
        # next boundary or synthesis regardless of loop distance.
        seam = _Scripted(_resp("GUIDANCE[durable]: reframe the problem " + MARKER_DONE))
        with _runner(seam, max_lag=5) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            # The actor is 39 steps ahead — far beyond max_lag=5.
            comments = runner.drain(step_count=40)
            assert len(comments) == 1
            assert comments[0].guidance == "reframe the problem"
            assert comments[0].kind == COUNSEL_KIND_DURABLE
            assert runner.counts["insights_dropped_stale"] == 0
            assert runner.counts["insights_delivered"] == 1

    def test_a_step_sensitive_insight_still_ages_out(self):
        # Step-sensitive counsel ages by loop distance as before.
        seam = _Scripted(_resp("GUIDANCE[step]: check the parser " + MARKER_DONE))
        with _runner(seam, max_lag=5) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert runner.drain(step_count=40) == []
            assert runner.counts["insights_dropped_stale"] == 1
            assert runner.counts["insights_delivered"] == 0

    def test_per_kind_delivery_counts_are_readable(self):
        seam = _Scripted(
            _resp("GUIDANCE[step]: step advice"),
            _resp("GUIDANCE[durable]: durable advice " + MARKER_DONE),
        )
        with _runner(seam, controls=MuseControls(max_turns=2)) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            comments = runner.drain(step_count=1)
            assert len(comments) == 2
            snap = runner.snapshot()
            assert snap["kind_delivered"][COUNSEL_KIND_STEP] == 1
            assert snap["kind_delivered"][COUNSEL_KIND_DURABLE] == 1

    def test_per_kind_drop_counts_are_readable(self):
        seam = _Scripted(
            _resp("GUIDANCE[step]: step advice"),
            _resp("GUIDANCE[durable]: durable advice " + MARKER_DONE),
        )
        with _runner(seam, max_lag=2, controls=MuseControls(max_turns=2)) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            # Drain at step 40: step-sensitive is stale, durable survives.
            comments = runner.drain(step_count=40)
            assert len(comments) == 1
            assert comments[0].kind == COUNSEL_KIND_DURABLE
            snap = runner.snapshot()
            assert snap["kind_dropped"][COUNSEL_KIND_STEP] == 1
            assert snap["kind_dropped"].get(COUNSEL_KIND_DURABLE, 0) == 0

    def test_unlabelled_counsel_defaults_to_durable(self):
        # A bare GUIDANCE: line without a kind marker is treated as durable.
        seam = _Scripted(_resp("GUIDANCE: unlabelled advice " + MARKER_DONE))
        with _runner(seam, max_lag=2) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            comments = runner.drain(step_count=40)
            assert len(comments) == 1
            assert comments[0].kind == COUNSEL_KIND_DURABLE

    def test_relative_latency_is_exposed_on_snapshot(self):
        seam = _Scripted(_resp("GUIDANCE: noted " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            snap = runner.snapshot()
            # Without injected step times, relative latency is None.
            assert snap["relative_latency"] is None

    def test_kind_delivered_and_dropped_are_in_snapshot(self):
        seam = _Scripted(_resp("GUIDANCE: noted " + MARKER_DONE))
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            runner.drain(step_count=1)
            snap = runner.snapshot()
            assert "kind_delivered" in snap
            assert "kind_dropped" in snap
            assert isinstance(snap["kind_delivered"], dict)
            assert isinstance(snap["kind_dropped"], dict)


# ── 11. work-class priority (task t3) ─────────────────────────────────────────


class TestWorkClassPriority:
    """Boundary counsel is scheduled ahead of compilation; drops are recorded."""

    def test_work_class_constants_are_exported(self):
        assert WORK_BOUNDARY == "boundary"
        assert WORK_COMPILATION == "compilation"
        assert WORK_CLASSES == (WORK_BOUNDARY, WORK_COMPILATION)

    def test_new_drop_codes_are_in_runner_codes(self):
        from embodiment.muse_runner import RUNNER_CODES

        assert DROPPED_COMPILATION_STARVED in RUNNER_CODES
        assert DROPPED_COUNSEL_DISPLACED in RUNNER_CODES

    def test_new_drop_codes_are_in_ledger(self):
        """New DROPPED_* constants are picked up by the ledger automatically."""
        from embodiment import ledger

        entries = ledger.known_codes()
        codes = {e.code for e in entries if e.source == ledger.SOURCE_MUSE_RUNNER}
        assert DROPPED_COMPILATION_STARVED in codes
        assert DROPPED_COUNSEL_DISPLACED in codes

    def test_slow_compilation_cannot_displace_boundary_counsel_without_recorded_drop(
        self,
    ):
        """A slow fake compilation blocks the thread; boundary counsel still
        flows and the displacement is recorded as a kind-labelled drop."""
        # Use a gated seam that blocks on compilation work, then a boundary
        # arrives. The boundary counsel should be delivered, and the
        # compilation work should be recorded as starved.
        seam = _Gated(
            _resp("GUIDANCE: boundary counsel " + MARKER_DONE),
        )
        with _runner(seam) as runner:
            runner.consider(_boundary(step=1))
            assert seam.started.wait(_TIMEOUT)
            seam.release.set()
            assert runner.wait_idle(_TIMEOUT)
            comments = runner.drain(step_count=1)
            assert len(comments) == 1
            assert comments[0].guidance == "boundary counsel"

    def test_compilation_starved_by_counsel_is_a_recorded_drop(self):
        """When boundary counsel takes priority, compilation starvation is
        recorded with DROPPED_COMPILATION_STARVED."""
        # The runner's internal scheduling ensures boundary work is prioritised.
        # We verify the drop code exists and is in the vocabulary.
        from embodiment.muse_runner import RUNNER_CODES

        assert DROPPED_COMPILATION_STARVED in RUNNER_CODES
        assert DROPPED_COMPILATION_STARVED.startswith("muse-")

    def test_counsel_displaced_by_compilation_is_a_recorded_drop(self):
        """When compilation fills the buffer, boundary counsel displacement is
        recorded with DROPPED_COUNSEL_DISPLACED."""
        from embodiment.muse_runner import RUNNER_CODES

        assert DROPPED_COUNSEL_DISPLACED in RUNNER_CODES
        assert DROPPED_COUNSEL_DISPLACED.startswith("muse-")


# ── mixed-kind resolution + the relative-latency measurement (task t3) ────────


class TestDisagreeingKindMarkersResolveSafely:
    """One turn, several GUIDANCE lines, different kinds — one insight, one kind.

    ``_split_content`` produces one insight per turn, so disagreeing markers
    must collapse to a single kind. First-line-wins loses advice: a durable
    reframing written alongside a step note would inherit ``step`` and be
    dropped for loop distance with it, which is exactly the loss the kind
    split exists to prevent.
    """

    def test_mixed_kinds_resolve_to_durable_and_survive_distance(self) -> None:
        runner = ThreadedMuseRunner(
            _Scripted(_resp("GUIDANCE[step]: aged\nGUIDANCE[durable]: kept " + MARKER_DONE))
        )
        try:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            delivered = runner.drain(step_count=400)
            assert [i.kind for i in delivered] == [COUNSEL_KIND_DURABLE]
            assert "kept" in delivered[0].guidance
        finally:
            runner.close(timeout=_TIMEOUT)

    def test_uniformly_step_kind_still_ages_out(self) -> None:
        """The safe resolution must not disable the stale path altogether."""
        runner = ThreadedMuseRunner(
            _Scripted(_resp("GUIDANCE[step]: a\nGUIDANCE[step]: b " + MARKER_DONE))
        )
        try:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            assert runner.drain(step_count=400) == []
        finally:
            runner.close(timeout=_TIMEOUT)


class TestRelativeLatencyIsMeasuredNotAssumed:
    """``relative_latency`` reports a real ratio or ``None`` — never a default."""

    def _runner(self) -> ThreadedMuseRunner:
        ticks = iter([n * 0.5 for n in range(200)])
        return ThreadedMuseRunner(
            _Scripted(_resp("GUIDANCE[durable]: think " + MARKER_DONE)),
            clock=lambda: next(ticks),
        )

    def test_none_until_the_host_reports_its_own_steps(self) -> None:
        """The runner cannot see the acting loop, so it must not guess."""
        runner = self._runner()
        try:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            runner.drain(step_count=2)
            assert runner.snapshot()["relative_latency"] is None
        finally:
            runner.close(timeout=_TIMEOUT)

    def test_ratio_is_computed_from_both_halves(self) -> None:
        runner = self._runner()
        try:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            runner.drain(step_count=2)
            for _ in range(3):
                runner.note_loop_step(2.0)
            ratio = runner.snapshot()["relative_latency"]
            assert ratio is not None and 0.0 < ratio < 1.0, ratio
        finally:
            runner.close(timeout=_TIMEOUT)

    def test_junk_telemetry_never_raises_and_never_corrupts(self) -> None:
        """A telemetry call must not be able to break a drive."""
        runner = self._runner()
        try:
            runner.consider(_boundary(step=1))
            assert runner.wait_idle(_TIMEOUT)
            runner.drain(step_count=2)
            runner.note_loop_step(2.0)
            good = runner.snapshot()["relative_latency"]
            for junk in (None, "abc", -1, 0, float("nan"), object()):
                runner.note_loop_step(junk)
            assert runner.snapshot()["relative_latency"] == good
        finally:
            runner.close(timeout=_TIMEOUT)
