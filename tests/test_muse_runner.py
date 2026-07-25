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
    DROPPED_LATE,
    DROPPED_OVERFLOW,
    DROPPED_STALE,
    MUSE_ROLE,
    THREAD_NAME,
    ThreadedMuseRunner,
)
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

    def _next(self) -> Any:
        self.calls += 1
        self.threads.append(threading.current_thread())
        reply = self.replies.pop(0) if self.replies else _resp(MARKER_DONE)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        return self._next()


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
        return self._next()


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
        engine = PresenceEngine(io=host.io())
        outcome = host.drive(engine)
        assert outcome.exit_reason == EXIT_FINISHED
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is False
        # The whole point of deviation d1's cost being bounded: no muse, no
        # thread. Not a stopped thread — never one at all.
        assert _live_threads() == before

    def test_a_museless_run_still_feels_present(self):
        host = _Host(turns=[_turn(_call("write_file", path="a.py")), _turn(_call("finish"))])
        engine = PresenceEngine(io=host.io())
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
        seam = _Scripted(_resp("GUIDANCE: about step one " + MARKER_DONE))
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
        seam = _Scripted(_resp("GUIDANCE: about step one " + MARKER_DONE))
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
        seam = _Scripted(_resp("GUIDANCE: one " + MARKER_DONE))
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
            assert all(set(d.to_dict()) == {"code", "reason", "step_index", "model_turns"}
                       for d in runner.degradations)

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
            engine = PresenceEngine(io=host.io(), muse=runner)
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
        seam = _Scripted(_resp("I notice the tests never ran\nGUIDANCE: run pytest " + MARKER_DONE))
        rendered: list[str] = []
        guided: list[str] = []
        io = PresenceIO(render=rendered.append, append_guidance=guided.append)
        with _runner(seam) as runner:
            engine = PresenceEngine(io=io, muse=runner)
            engine.acknowledge(ContextPacket(original="ship it", ack="on it"))
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
            engine = PresenceEngine(io=host.io(), muse=runner)
            outcome = host.drive(engine)
            assert outcome.exit_reason == EXIT_FINISHED
            assert engine.muse_degraded is True
            assert runner.degradation() is not None
        assert any("muse unavailable" in line for line in host.rendered)
        # The run itself is untouched: the tools still ran, the loop still finished.
        assert [name for name, _ in host.executor.executed] == ["write_file", "finish"]
