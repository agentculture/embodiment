"""Tests for embodiment.presence_engine — the cortex+muse presence pump (t7).

Covers:

1. ``PresenceIO`` keeps EXACTLY eight plain callables, all defaulted to no-ops;
   ``narrate`` is the ONE exception-swallowing callback and every other
   callback's failure stays visible to the host.
2. The beats drive through injected callbacks with no TTY, no thread and NO
   CLOCK — the cadence is step/phase-based and the only timestamp the engine can
   write comes from an injected ``clock`` (default ``None`` ⇒ no ``at`` key at
   all, never a fabricated zero).
3. The museless run is the DEFAULT tested path (c42): every beat works with no
   muse configured and makes zero muse calls. The optional muse attaches as an
   advisory seam whose arrival, silence and failure are each recorded (C3).
4. AST guards: the engine imports no front module (the ported import-graph pin),
   no clock/thread module, and no ``colleague``.
5. The engine exposes NO external presence event stream (c33) — ``snapshot()``
   is a pull-only artifact fold, not a pub/sub surface.
6. Fault-injection hardening (t8): each of the four named fault classes (dead
   port, request error, overflow, lossy JSON) degrades every public
   PresenceSink entry point (``acknowledge``, ``on_operator_message``,
   ``on_progress_boundary``) visibly and never raises, reusing t7's existing
   ``_degrade_muse`` mechanism rather than a second one.
7. The TERMINAL boundary (issue #17, plan task t4): the loop's last beat, fired
   immediately before the forced synthesis turn, **drains without considering**
   — it delivers counsel into the one turn that can still use it and starts no
   session of its own, because a session started there is precisely the one
   that strands undrained at close.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from embodiment.contract import SENSES_CHAT_KINDS, ContextPacket, ModelResponse, Task, ToolCall
from embodiment.loop import ToolOutcome, run
from embodiment.presence import UpdateCadence
from embodiment.presence_engine import (
    BOUNDARY_CADENCE_TICK,
    BOUNDARY_INTAKE,
    BOUNDARY_OPERATOR_INPUT,
    BOUNDARY_SYNTHESIS,
    MODE_CORTEX_ONLY,
    MODE_MUSE,
    MODE_OFF,
    SOURCE_CORTEX,
    SOURCE_MUSE,
    SOURCE_OPERATOR,
    SOURCE_PACKET,
    BoundaryContext,
    MuseComment,
    MusePullSeam,
    MuseSeam,
    PresenceEngine,
    PresenceExecutor,
    PresenceIO,
    PresenceSink,
    PresenceTurn,
    build_presence_executor,
)

_ENGINE_SRC = Path(__file__).resolve().parents[1] / "embodiment" / "presence_engine.py"


# ── fakes ─────────────────────────────────────────────────────────────────────


class _RecordingIO:
    """A front stand-in: every PresenceIO callback records what it saw."""

    def __init__(
        self,
        pending: Optional[list[str]] = None,
        flight: str = "flight: editing foo.py",
        state: str = "step 3/40",
    ) -> None:
        self.dispatched: list[str] = []
        self.guided: list[str] = []
        self.rendered: list[str] = []
        self.narrated: list[str] = []
        self.reads = 0
        self.polls = 0
        self._pending = list(pending or [])
        self._flight = flight
        self._state = state
        self.narrate_raises = False
        self.render_raises = False
        self.poll_raises = False
        self.guide_raises = False

    # individual callbacks -----------------------------------------------------
    def _read(self) -> str:
        self.reads += 1
        return self._flight

    def _poll(self) -> Optional[str]:
        self.polls += 1
        if self.poll_raises:
            raise RuntimeError("stdin gone")
        return self._pending.pop(0) if self._pending else None

    def _render(self, line: str) -> None:
        if self.render_raises:
            raise RuntimeError("tty gone")
        self.rendered.append(line)

    def _narrate(self, line: str) -> None:
        self.narrated.append(line)
        if self.narrate_raises:
            raise RuntimeError("tts proxy 502")

    def _guide(self, text: str) -> None:
        if self.guide_raises:
            raise RuntimeError("flight plane unwritable")
        self.guided.append(text)

    def io(self) -> PresenceIO:
        return PresenceIO(
            dispatch_to_cortex=self.dispatched.append,
            append_guidance=self._guide,
            read_flight=self._read,
            render=self._render,
            poll_operator_input=self._poll,
            feed_tail=lambda: self._flight,
            task_state=lambda: self._state,
            narrate=self._narrate,
        )


class _FakeMuse:
    """An advisory muse seam stand-in (what task t10 must conform to).

    ``raises`` is either a plain ``bool`` (``True`` raises a generic
    ``RuntimeError`` — the original, still-used shape) or a specific
    ``BaseException`` instance, so fault-injection tests can pin the exact
    exception TYPE a muse endpoint might raise (a dead port, a request error,
    an overflow, lossy JSON) while the engine's degrade-and-record handling
    stays exactly the same either way (t8).
    """

    def __init__(self, comments: Any = None, *, raises: Any = False) -> None:
        self.comments = list(comments or [])
        self.raises = raises
        self.boundaries: list[BoundaryContext] = []

    def __call__(self, boundary: BoundaryContext) -> Optional[MuseComment]:
        self.boundaries.append(boundary)
        if self.raises:
            if isinstance(self.raises, BaseException):
                raise self.raises
            raise RuntimeError("muse endpoint refused connection")
        if not self.comments:
            return None
        return self.comments.pop(0)


class _DrainMuse:
    """A DRAIN-shaped muse stand-in — the seam task t10b's runner implements.

    ``consider`` starts thinking somewhere else and returns immediately;
    ``drain`` hands back whatever is ready *now* (an empty drain is normal);
    ``degradation`` reports the lane having stopped for good, WITHOUT raising.
    """

    def __init__(
        self,
        ready: Any = None,
        *,
        consider_raises: Any = False,
        drain_raises: Any = False,
        degradation: Any = None,
        degradation_raises: bool = False,
    ) -> None:
        self.ready = list(ready or [])
        self.considered: list[BoundaryContext] = []
        self.drained_at: list[int] = []
        self.consider_raises = consider_raises
        self.drain_raises = drain_raises
        self._degradation = degradation
        self.degradation_raises = degradation_raises

    def consider(self, boundary: BoundaryContext) -> None:
        self.considered.append(boundary)
        if self.consider_raises:
            raise self.consider_raises

    def drain(self, *, step_count: int = 0) -> list[MuseComment]:
        self.drained_at.append(step_count)
        if self.drain_raises:
            raise self.drain_raises
        ready, self.ready = self.ready, []
        return ready

    def degradation(self) -> Optional[str]:
        if self.degradation_raises:
            raise RuntimeError("the seam's own health probe exploded")
        return self._degradation


def _engine(
    io: Optional[_RecordingIO] = None,
    *,
    muse: Any = None,
    cadence: Optional[UpdateCadence] = None,
    clock: Any = None,
    enabled: bool = True,
    speaker: Optional[str] = None,
    history_provider: Any = None,
) -> tuple[PresenceEngine, _RecordingIO]:
    io = io if io is not None else _RecordingIO()
    kwargs: dict[str, Any] = {}
    if speaker is not None:
        kwargs["speaker"] = speaker
    engine = PresenceEngine(
        io=io.io(),
        muse=muse,
        cadence=cadence,
        clock=clock,
        enabled=enabled,
        history_provider=history_provider,
        **kwargs,
    )
    return engine, io


# ── 1. PresenceIO: eight no-op callables ──────────────────────────────────────


class TestPresenceIO:
    """The injected IO surface: eight plain callables, all defaulted to no-ops."""

    def test_exactly_eight_callables_named_as_the_contract_says(self):
        names = [f.name for f in dataclasses.fields(PresenceIO)]
        assert names == [
            "dispatch_to_cortex",
            "append_guidance",
            "read_flight",
            "render",
            "poll_operator_input",
            "feed_tail",
            "task_state",
            "narrate",
        ]
        assert len(names) == 8

    def test_every_default_is_a_callable_no_op(self):
        io = PresenceIO()
        assert io.dispatch_to_cortex("x") is None
        assert io.append_guidance("x") is None
        assert io.read_flight() == ""
        assert io.render("x") is None
        assert io.poll_operator_input() is None
        assert io.feed_tail() == ""
        assert io.task_state() is None
        assert io.narrate("x") is None

    def test_a_fully_defaulted_engine_drives_without_a_front(self):
        engine = PresenceEngine()
        assert engine.active is True
        assert engine.acknowledge(ContextPacket(original="do x", ack="on it")) != []
        assert engine.on_operator_message("hurry up") != []
        assert engine.snapshot()["chat"]


class TestBuildPresenceExecutor:
    """build_presence_executor binds the acting callbacks to the IO surface."""

    def test_binds_the_three_acting_callbacks(self):
        io = _RecordingIO()
        ex = build_presence_executor(io.io())
        assert isinstance(ex, PresenceExecutor)
        assert [f.name for f in dataclasses.fields(PresenceExecutor)] == [
            "dispatch_to_cortex",
            "guide_cortex",
            "read_flight",
        ]
        ex.dispatch_to_cortex("build it")
        ex.guide_cortex("look at config first")
        assert ex.read_flight() == io._flight
        assert io.dispatched == ["build it"]
        assert io.guided == ["look at config first"]

    def test_an_explicit_executor_overrides_the_one_built_from_io(self):
        io = _RecordingIO()
        seen: list[str] = []
        engine = PresenceEngine(
            io=io.io(),
            executor=PresenceExecutor(
                dispatch_to_cortex=seen.append,
                guide_cortex=seen.append,
                read_flight=lambda: "",
            ),
        )
        engine.acknowledge(ContextPacket(original="do x"))
        assert seen == ["do x"]
        assert io.dispatched == []

    def test_executor_carries_no_control_bearing_surface(self):
        # c41: the muse proposes, never decides. Nothing on the acting surface
        # can deny or rewrite a tool call — there is no such field to bind.
        names = {f.name for f in dataclasses.fields(PresenceExecutor)}
        assert not (names & {"deny", "rewrite", "pre_tool", "hooks", "approve"})


# ── 2. the beats, museless (the DEFAULT tested path, c42) ─────────────────────


class TestMuselessBeats:
    """Every beat works with no muse configured, making zero muse calls."""

    def test_default_mode_is_cortex_only_and_not_a_degradation(self):
        engine, _ = _engine()
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is False
        assert engine.active is True

    def test_acknowledge_dispatches_the_operators_verbatim_words(self):
        engine, io = _engine()
        original = "fix the null-deref bug in parser.py\n\n  (second line kept)"
        engine.acknowledge(ContextPacket(original=original, ack="on it — parser.py"))
        # The VERBATIM invariant: cortex receives the operator's own text, never
        # model output and never a normalized reading.
        assert io.dispatched == [original]

    def test_acknowledge_renders_the_packets_own_ack_with_no_model_call(self):
        engine, io = _engine()
        turns = engine.acknowledge(ContextPacket(original="do x", ack="on it — starting now"))
        assert [t.kind for t in turns] == ["ack"]
        assert [t.source for t in turns] == [SOURCE_PACKET]
        assert io.rendered == ["presence: on it — starting now"]
        # Museless: nothing was asked of any model, and no flight was read.
        assert io.reads == 0

    def test_acknowledge_without_an_ack_renders_nothing(self):
        engine, io = _engine()
        assert engine.acknowledge(ContextPacket(original="do x")) == []
        assert io.rendered == []
        assert io.dispatched == ["do x"]

    def test_acknowledge_tolerates_a_missing_packet(self):
        engine, io = _engine()
        assert engine.acknowledge(None) == []
        assert io.dispatched == []

    def test_operator_message_relays_verbatim_and_renders_the_relay(self):
        engine, io = _engine()
        turns = engine.on_operator_message("focus on the config module")
        assert io.guided == ["focus on the config module"]
        assert io.rendered == ["→ cortex: focus on the config module"]
        assert turns[0].injection == {
            "text": "focus on the config module",
            "source": SOURCE_OPERATOR,
        }
        assert engine.snapshot()["injections"] == [turns[0].injection]

    def test_blank_operator_message_is_a_no_op(self):
        engine, io = _engine()
        assert engine.on_operator_message("   ") == []
        assert engine.on_operator_message("") == []
        assert io.guided == []

    def test_progress_boundary_fires_a_structural_update_from_task_state(self):
        cadence = UpdateCadence(every_steps=2, on_phase_change=False, max_updates=4)
        engine, io = _engine(cadence=cadence)
        assert engine.on_progress_boundary(step_count=1) == []
        turns = engine.on_progress_boundary(step_count=2)
        assert [t.kind for t in turns] == ["update"]
        assert io.rendered == ["presence: still working — step 3/40"]
        # Sourced to cortex, never to a mind that does not exist in this run.
        assert engine.snapshot()["chat"] == [
            {"kind": "update", "text": "still working — step 3/40", "source": SOURCE_CORTEX}
        ]

    def test_structural_update_falls_back_to_flight_then_feed_tail(self):
        cadence = UpdateCadence(every_steps=1, on_phase_change=False)
        io = _RecordingIO(state="", flight="reading loop.py\nwriting loop.py")
        engine, io = _engine(io, cadence=cadence)
        engine.on_progress_boundary(step_count=1)
        assert io.rendered == ["presence: still working — writing loop.py"]
        assert io.reads == 1

    def test_a_fired_update_with_nothing_to_say_still_consumes_budget(self):
        cadence = UpdateCadence(every_steps=1, on_phase_change=False, max_updates=1)
        io = _RecordingIO(state="", flight="")
        engine, io = _engine(io, cadence=cadence)
        assert engine.on_progress_boundary(step_count=1) == []
        assert io.rendered == []
        # Budget consumed by the silent attempt — the next boundary is capped.
        engine.on_progress_boundary(step_count=2)
        assert any("update cap reached" in line for line in io.rendered)

    def test_updates_cap_and_the_cap_is_recorded_exactly_once(self):
        cadence = UpdateCadence(every_steps=2, on_phase_change=False, max_updates=2)
        engine, io = _engine(cadence=cadence)
        engine.on_progress_boundary(step_count=2)  # update 1
        engine.on_progress_boundary(step_count=4)  # update 2
        engine.on_progress_boundary(step_count=6)  # capped → recorded once
        engine.on_progress_boundary(step_count=8)  # already recorded → silent
        updates = [line for line in io.rendered if "still working" in line]
        assert len(updates) == 2
        capped = [c for c in engine.snapshot()["chat"] if c.get("capped")]
        assert len(capped) == 1
        assert sum("update cap reached" in line for line in io.rendered) == 1

    def test_live_operator_input_wins_over_a_proactive_update(self):
        cadence = UpdateCadence(every_steps=1, on_phase_change=False)
        io = _RecordingIO(pending=["are you almost done?"])
        engine, io = _engine(io, cadence=cadence)
        engine.on_progress_boundary(step_count=2)
        assert io.guided == ["are you almost done?"]
        assert not any("still working" in line for line in io.rendered)

    def test_phase_change_fires_an_update(self):
        engine, io = _engine(cadence=UpdateCadence(every_steps=99))
        engine.on_progress_boundary(step_count=1, phase_changed=True)
        assert any("still working" in line for line in io.rendered)

    def test_speaker_labels_the_rendered_lines(self):
        engine, io = _engine(speaker="Gwen")
        engine.acknowledge(ContextPacket(original="x", ack="on it"))
        assert io.rendered == ["Gwen: on it"]


# ── 3. the optional muse seam ─────────────────────────────────────────────────


class TestMuseSeam:
    """The muse is advisory: it proposes text and guidance, it never decides."""

    def test_muse_comment_is_rendered_and_recorded_as_chat(self):
        muse = _FakeMuse([MuseComment(text="the parser test is the risky one", tokens=41)])
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        assert engine.mode == MODE_MUSE
        turns = engine.on_progress_boundary(step_count=1)
        assert io.rendered == ["presence: the parser test is the risky one"]
        assert [t.source for t in turns] == [SOURCE_MUSE]
        snap = engine.snapshot()
        assert snap["chat"] == [
            {
                "kind": "update",
                "text": "the parser test is the risky one",
                "source": SOURCE_MUSE,
            }
        ]
        assert [(r.point, r.degraded, r.tokens) for r in snap["records"]] == [
            (f"muse:{BOUNDARY_CADENCE_TICK}", False, 41)
        ]

    def test_muse_guidance_rides_the_advisory_channel_only(self):
        muse = _FakeMuse([MuseComment(guidance="check the None branch first")])
        engine, io = _engine(muse=muse)
        turns = engine.on_operator_message("hurry")
        # The operator's words AND the muse's guidance both arrive as plain
        # advisory text through append_guidance — never as a tool-call decision.
        assert io.guided == ["hurry", "check the None branch first"]
        assert turns[-1].injection == {
            "text": "check the None branch first",
            "source": SOURCE_MUSE,
        }
        assert engine.snapshot()["injections"][-1]["source"] == SOURCE_MUSE

    def test_muse_sees_the_boundary_context(self):
        muse = _FakeMuse()
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.acknowledge(ContextPacket(original="do x"))
        engine.on_operator_message("faster")
        engine.on_progress_boundary(step_count=1)
        kinds = [b.kind for b in muse.boundaries]
        assert kinds == [BOUNDARY_INTAKE, BOUNDARY_OPERATOR_INPUT, BOUNDARY_CADENCE_TICK]
        tick = muse.boundaries[-1]
        assert tick.step_count == 1
        assert tick.task_state == "step 3/40"
        assert tick.flight == io._flight
        assert tick.feed_tail == io._flight
        assert tick.reason == "every-n"
        assert muse.boundaries[0].packet is not None
        assert muse.boundaries[1].operator_input == "faster"

    def test_a_silent_muse_is_still_a_recorded_invocation(self):
        muse = _FakeMuse([])  # returns None
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        turns = engine.on_progress_boundary(step_count=1)
        # A muse with nothing to add does not cancel the beat — the structural
        # line still fires, sourced honestly to cortex.
        assert [t.source for t in turns] == [SOURCE_CORTEX]
        assert io.rendered == ["presence: still working — step 3/40"]
        records = engine.snapshot()["records"]
        assert [(r.point, r.degraded) for r in records] == [
            (f"muse:{BOUNDARY_CADENCE_TICK}", False)
        ]

    def test_a_silent_muse_at_intake_says_nothing_at_all(self):
        muse = _FakeMuse([])
        engine, io = _engine(muse=muse)
        assert engine.acknowledge(ContextPacket(original="do x")) == []
        assert io.rendered == []

    def test_a_muse_comment_replaces_the_structural_update(self):
        muse = _FakeMuse([MuseComment(text="rethinking the fix")])
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        assert io.rendered == ["presence: rethinking the fix"]

    def test_a_mute_muse_still_yields_the_structural_update(self):
        muse = _FakeMuse([MuseComment(guidance="only guidance, no narration")])
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        assert "presence: still working — step 3/40" in io.rendered

    def test_muse_chat_kinds_stay_inside_the_shared_vocabulary(self):
        for kind in (BOUNDARY_INTAKE, BOUNDARY_OPERATOR_INPUT, BOUNDARY_CADENCE_TICK):
            muse = _FakeMuse([MuseComment(text="hi")])
            engine, _ = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
            if kind == BOUNDARY_INTAKE:
                engine.acknowledge(ContextPacket(original="x"))
            elif kind == BOUNDARY_OPERATOR_INPUT:
                engine.on_operator_message("x")
            else:
                engine.on_progress_boundary(step_count=1)
            entry = engine.snapshot()["chat"][-1]
            assert entry["kind"] in SENSES_CHAT_KINDS


class TestMuseDegradation:
    """C3: a failing muse degrades to cortex-only, visibly and once."""

    def test_a_raising_muse_records_the_failure_and_the_transition(self):
        muse = _FakeMuse(raises=True)
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        records = engine.snapshot()["records"]
        assert [(r.point, r.degraded) for r in records] == [
            (f"muse:{BOUNDARY_CADENCE_TICK}", True),
            ("muse:degraded-off", True),
        ]
        transition = [c for c in engine.snapshot()["chat"] if c.get("degraded") == "muse"]
        assert len(transition) == 1
        assert "muse endpoint refused connection" in transition[0]["detail"]
        assert any("muse unavailable" in line for line in io.rendered)

    def test_the_degradation_notice_never_lands_ahead_of_the_beat(self):
        # The operator hears the acknowledgment first; a muse notice comments on
        # a beat, so it can never precede it in either the render or the ledger.
        muse = _FakeMuse(raises=True)
        engine, io = _engine(muse=muse)
        engine.acknowledge(ContextPacket(original="x", ack="on it"))
        assert io.rendered == [
            "presence: on it",
            "presence: (muse unavailable — continuing cortex-only)",
        ]
        chat = engine.snapshot()["chat"]
        assert chat[0]["text"] == "on it"
        assert chat[1]["degraded"] == SOURCE_MUSE

    def test_the_muse_is_not_retried_after_it_degrades(self):
        muse = _FakeMuse(raises=True)
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        engine.on_progress_boundary(step_count=2)
        engine.on_progress_boundary(step_count=3)
        assert len(muse.boundaries) == 1
        assert sum("muse unavailable" in line for line in io.rendered) == 1

    def test_presence_survives_a_degraded_muse(self):
        muse = _FakeMuse(raises=True)
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)  # muse dies here
        engine.on_progress_boundary(step_count=2)  # structural presence continues
        assert any("still working — step 3/40" in line for line in io.rendered)

    def test_a_degraded_muse_never_silently_disappears(self):
        muse = _FakeMuse(raises=True)
        engine, _ = _engine(muse=muse)
        engine.acknowledge(ContextPacket(original="x"))
        # Nothing about the failure is inferred — it is in the ledger verbatim.
        assert any(r.degraded for r in engine.records)


# ── 3a. the DRAIN-shaped seam (t10b) ──────────────────────────────────────────


class TestDrainSeam:
    """The revised seam: consider-then-drain, so the pump never waits on a mind.

    Deviation ``d1`` put the muse on its own thread, which means an insight is
    almost never ready at the boundary that asked for it. The engine therefore
    *offers* every boundary (``consider``) and *collects* whatever finished in
    the meantime (``drain``) — and an empty drain is the normal case, not a
    fault. The engine itself stays thread-free: it only ever makes two
    non-blocking calls.
    """

    def test_a_boundary_is_offered_then_whatever_is_ready_is_collected(self):
        muse = _DrainMuse()
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=4)
        assert [b.kind for b in muse.considered] == [BOUNDARY_CADENCE_TICK]
        assert muse.drained_at == [4]

    def test_an_empty_drain_is_normal_and_the_beat_still_lands(self):
        muse = _DrainMuse()
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        turns = engine.on_progress_boundary(step_count=1)
        assert [t.source for t in turns] == [SOURCE_CORTEX]
        assert io.rendered == ["presence: still working — step 3/40"]
        assert engine.muse_degraded is False
        assert [(r.point, r.degraded) for r in engine.records] == [
            (f"muse:{BOUNDARY_CADENCE_TICK}", False)
        ]

    def test_an_insight_drained_at_a_later_boundary_still_lands(self):
        # The whole point of the drain: an insight reasoned about step 1 arrives
        # at step 2, and the pump renders and injects it there.
        muse = _DrainMuse()
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        muse.ready.append(MuseComment(text="the parser is the risk", guidance="read parser.py"))
        engine.on_progress_boundary(step_count=2)
        assert "presence: the parser is the risk" in io.rendered
        assert io.guided == ["read parser.py"]

    def test_several_ready_comments_all_land_in_order(self):
        muse = _DrainMuse([MuseComment(text="first"), MuseComment(text="second", tokens=12)])
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        turns = engine.on_progress_boundary(step_count=1)
        assert [t.source for t in turns] == [SOURCE_MUSE, SOURCE_MUSE]
        assert io.rendered == ["presence: first", "presence: second"]
        assert [(r.point, r.tokens) for r in engine.records] == [
            (f"muse:{BOUNDARY_CADENCE_TICK}", None),
            (f"muse:{BOUNDARY_CADENCE_TICK}", 12),
        ]

    def test_a_raising_consider_degrades_visibly_and_never_propagates(self):
        muse = _DrainMuse(consider_raises=ConnectionRefusedError("dead port"))
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        assert any("muse unavailable" in line for line in io.rendered)

    def test_a_raising_drain_degrades_visibly_and_never_propagates(self):
        muse = _DrainMuse(drain_raises=TimeoutError("the drain wedged"))
        engine, io = _engine(muse=muse)
        engine.on_operator_message("hurry")
        assert engine.muse_degraded is True
        assert io.guided == ["hurry"]  # the engine's own work still landed

    def test_a_seam_reporting_itself_degraded_is_unbound_after_its_last_words(self):
        muse = _DrainMuse([MuseComment(text="last thought")], degradation="the endpoint died")
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        # The last words land FIRST; the notice comments on the beat, so it can
        # never precede it.
        assert io.rendered == [
            "presence: last thought",
            "presence: (muse unavailable — continuing cortex-only)",
        ]
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        detail = [c for c in engine.snapshot()["chat"] if c.get("degraded") == SOURCE_MUSE][0]
        assert "the endpoint died" in detail["detail"]

    def test_a_seam_that_reports_degraded_is_never_consulted_again(self):
        muse = _DrainMuse(degradation="gone")
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        engine.on_progress_boundary(step_count=2)
        engine.on_progress_boundary(step_count=3)
        assert len(muse.considered) == 1
        assert sum("muse unavailable" in line for line in io.rendered) == 1

    def test_a_raising_health_probe_degrades_like_any_other_fault(self):
        muse = _DrainMuse(degradation_raises=True)
        engine, io = _engine(muse=muse)
        engine.acknowledge(ContextPacket(original="x", ack="on it"))
        assert engine.muse_degraded is True
        assert any("muse unavailable" in line for line in io.rendered)

    def test_a_drain_returning_junk_is_tolerated(self):
        muse = _DrainMuse()
        muse.ready = None  # type: ignore[assignment]
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        assert io.rendered == ["presence: still working — step 3/40"]


class TestDegradationNoticeOrdering:
    """A muse notice comments on a beat — it can never land ahead of one."""

    def test_the_notice_follows_the_structural_update_it_comments_on(self):
        muse = _FakeMuse(raises=True)
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.on_progress_boundary(step_count=1)
        assert io.rendered == [
            "presence: still working — step 3/40",
            "presence: (muse unavailable — continuing cortex-only)",
        ]

    def test_the_notice_follows_the_relay_it_comments_on(self):
        muse = _FakeMuse(raises=True)
        engine, io = _engine(muse=muse)
        engine.on_operator_message("hurry")
        assert io.rendered == [
            "→ cortex: hurry",
            "presence: (muse unavailable — continuing cortex-only)",
        ]


# ── 3b. fault-injection hardening across every public entry point (t8) ────────

#: The four fault classes named in the build brief (C3): a dead port, a
#: request error, a context/window overflow, and lossy/malformed JSON. A real
#: muse seam is a network call (t10's contract) and any of these is a
#: plausible exception it raises; the engine's existing generic
#: ``except Exception`` in ``_muse_turns_for`` already degrades every one of
#: them identically — these tests PROVE that across the three public
#: PresenceSink entry points, pinning the exact exception type rather than a
#: generic RuntimeError stand-in.
_FAULT_CLASSES = [
    ConnectionRefusedError("dead port: connection refused"),
    TimeoutError("request error: timed out"),
    OverflowError("overflow: context window exceeded"),
    json.JSONDecodeError("lossy JSON", "doc", 0),
]
_FAULT_IDS = ["dead-port", "request-error", "overflow", "lossy-json"]


class TestPublicEntryPointsNeverRaise:
    """Every public PresenceSink entry point degrades, never raises (C3, t8).

    Reuses t7's existing degradation-recording mechanism (``_degrade_muse``,
    the ``muse:<boundary>`` / ``muse:degraded-off`` record pair, the rendered
    notice, the mode transition to cortex-only) — no second ledger is
    invented here, exactly as t9 (a later wave) will need one consistent shape
    to build a degradation ledger over.
    """

    @pytest.mark.parametrize("fault", _FAULT_CLASSES, ids=_FAULT_IDS)
    def test_acknowledge_never_raises_and_degrades_visibly(self, fault):
        muse = _FakeMuse(raises=fault)
        engine, io = _engine(muse=muse)
        # Must not raise, regardless of fault type.
        turns = engine.acknowledge(ContextPacket(original="fix the bug", ack="on it"))
        assert isinstance(turns, list)
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        assert any(r.degraded for r in engine.records)
        assert any("muse unavailable" in line for line in io.rendered)

    @pytest.mark.parametrize("fault", _FAULT_CLASSES, ids=_FAULT_IDS)
    def test_on_operator_message_never_raises_and_degrades_visibly(self, fault):
        muse = _FakeMuse(raises=fault)
        engine, io = _engine(muse=muse)
        turns = engine.on_operator_message("hurry up")
        assert isinstance(turns, list)
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        assert any(r.degraded for r in engine.records)
        assert any("muse unavailable" in line for line in io.rendered)
        # The operator's own relay still landed — a failing muse never costs
        # the engine's own (non-muse) work for this beat.
        assert io.guided == ["hurry up"]

    @pytest.mark.parametrize("fault", _FAULT_CLASSES, ids=_FAULT_IDS)
    def test_on_progress_boundary_never_raises_and_degrades_visibly(self, fault):
        muse = _FakeMuse(raises=fault)
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        turns = engine.on_progress_boundary(step_count=1)
        assert isinstance(turns, list)
        assert engine.mode == MODE_CORTEX_ONLY
        assert engine.muse_degraded is True
        assert any(r.degraded for r in engine.records)
        assert any("muse unavailable" in line for line in io.rendered)

    @pytest.mark.parametrize("fault", _FAULT_CLASSES, ids=_FAULT_IDS)
    def test_presence_keeps_working_after_any_fault_class(self, fault):
        # A degraded muse drops to cortex-only PERMANENTLY (never retried) —
        # structural presence (acknowledge, relay, structural updates) must
        # keep functioning on every subsequent beat regardless of which fault
        # class caused the drop.
        muse = _FakeMuse(raises=fault)
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.acknowledge(ContextPacket(original="do x", ack="on it"))
        engine.on_operator_message("keep going")
        engine.on_progress_boundary(step_count=1)
        assert "presence: on it" in io.rendered
        assert "→ cortex: keep going" in io.rendered
        assert any("still working" in line for line in io.rendered)


# ── 4. no clock ───────────────────────────────────────────────────────────────


class TestNoClock:
    """The engine never reads a clock; a timestamp only ever comes injected."""

    def test_module_imports_no_clock_or_thread_module(self):
        tree = ast.parse(_ENGINE_SRC.read_text(encoding="utf-8"))
        forbidden = {"time", "threading", "datetime", "subprocess", "asyncio", "sched"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, node.module

    def test_no_clock_means_no_at_key_rather_than_a_fabricated_zero(self):
        cadence = UpdateCadence(every_steps=1, on_phase_change=False, max_updates=1)
        engine, io = _engine(cadence=cadence)
        engine.on_progress_boundary(step_count=1)
        engine.on_progress_boundary(step_count=2)  # capped
        engine.on_operator_message("hello")
        capped = [c for c in engine.snapshot()["chat"] if c.get("capped")][0]
        assert "at" not in capped
        assert "at" not in engine.snapshot()["injections"][0]

    def test_an_injected_clock_stamps_the_records(self):
        cadence = UpdateCadence(every_steps=1, on_phase_change=False, max_updates=1)
        engine, io = _engine(cadence=cadence, clock=lambda: 1234.5)
        engine.on_progress_boundary(step_count=1)
        engine.on_progress_boundary(step_count=2)  # capped
        engine.on_operator_message("hello")
        capped = [c for c in engine.snapshot()["chat"] if c.get("capped")][0]
        assert capped["at"] == 1234.5
        assert engine.snapshot()["injections"][0]["at"] == 1234.5

    def test_cadence_never_consults_wall_clock_state(self):
        # Identical step/phase inputs produce identical decisions, always.
        cadence = UpdateCadence(every_steps=4, on_phase_change=False)
        first, io_a = _engine(cadence=cadence)
        second, io_b = _engine(cadence=cadence)
        for step in range(1, 10):
            first.on_progress_boundary(step_count=step)
            second.on_progress_boundary(step_count=step)
        assert io_a.rendered == io_b.rendered


# ── 5. narrate is the ONE swallowing callback ─────────────────────────────────


class TestNarrate:
    """A voice hook can never disturb the text path; nothing else is swallowed."""

    def test_narrate_runs_after_render_with_the_same_text(self):
        engine, io = _engine()
        engine.acknowledge(ContextPacket(original="x", ack="on it"))
        assert io.rendered == ["presence: on it"]
        assert io.narrated == ["on it"]

    def test_a_raising_narrate_never_disturbs_the_text_path(self):
        io = _RecordingIO()
        io.narrate_raises = True
        engine, io = _engine(io, cadence=UpdateCadence(every_steps=1))
        engine.acknowledge(ContextPacket(original="x", ack="on it"))
        engine.on_progress_boundary(step_count=1)
        assert io.rendered == ["presence: on it", "presence: still working — step 3/40"]
        assert io.narrated == ["on it", "still working — step 3/40"]

    def test_relay_lines_are_rendered_but_not_narrated(self):
        engine, io = _engine()
        engine.on_operator_message("focus on config")
        assert io.rendered == ["→ cortex: focus on config"]
        assert io.narrated == []

    def test_a_raising_render_stays_visible(self):
        io = _RecordingIO()
        io.render_raises = True
        engine, io = _engine(io)
        packet = ContextPacket(original="x", ack="on it")
        with pytest.raises(RuntimeError, match="tty gone"):
            engine.acknowledge(packet)

    def test_a_raising_poll_stays_visible(self):
        io = _RecordingIO()
        io.poll_raises = True
        engine, io = _engine(io)
        with pytest.raises(RuntimeError, match="stdin gone"):
            engine.on_progress_boundary(step_count=1)

    def test_a_raising_guidance_appender_stays_visible(self):
        io = _RecordingIO()
        io.guide_raises = True
        engine, io = _engine(io)
        with pytest.raises(RuntimeError, match="flight plane unwritable"):
            engine.on_operator_message("hurry")


# ── 6. the off lane ───────────────────────────────────────────────────────────


class TestOffLane:
    """A disarmed engine is a strict no-op everywhere."""

    def test_every_beat_is_a_strict_no_op(self):
        muse = _FakeMuse([MuseComment(text="never spoken")])
        io = _RecordingIO(pending=["hello?"])
        engine, io = _engine(io, muse=muse, enabled=False)
        assert engine.mode == MODE_OFF
        assert engine.active is False
        assert engine.acknowledge(ContextPacket(original="x", ack="on it")) == []
        assert engine.on_progress_boundary(step_count=5, phase_changed=True) == []
        assert engine.on_operator_message("anything") == []
        assert io.dispatched == [] and io.guided == [] and io.rendered == []
        assert io.polls == 0 and muse.boundaries == []
        assert engine.snapshot() == {"records": [], "chat": [], "injections": []}


# ── 7. history provider ───────────────────────────────────────────────────────


class TestHistoryProvider:
    """Optional history threading; a broken provider degrades visibly, once."""

    def test_history_reaches_the_muse(self):
        muse = _FakeMuse()
        engine, _ = _engine(muse=muse, history_provider=lambda: [{"role": "user", "text": "hi"}])
        engine.acknowledge(ContextPacket(original="x"))
        assert muse.boundaries[0].history == [{"role": "user", "text": "hi"}]

    def test_a_raising_provider_is_recorded_and_then_left_alone(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError("history store gone")

        muse = _FakeMuse()
        engine, _ = _engine(muse=muse, history_provider=boom)
        engine.acknowledge(ContextPacket(original="x"))
        engine.on_operator_message("still there?")
        assert calls["n"] == 1  # latched off after the first failure
        assert [(r.point, r.degraded) for r in engine.records if r.point == "history"] == [
            ("history", True)
        ]
        assert muse.boundaries[0].history is None

    def test_no_provider_means_no_history(self):
        muse = _FakeMuse()
        engine, _ = _engine(muse=muse)
        engine.acknowledge(ContextPacket(original="x"))
        assert muse.boundaries[0].history is None


# ── 8. AST guards + the loop-only boundary ────────────────────────────────────


def _imported_modules() -> set[str]:
    tree = ast.parse(_ENGINE_SRC.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_presence_engine_imports_no_front_module():
    """The ported import-graph pin: fronts depend on the engine, never reverse."""
    modules = _imported_modules()
    forbidden = {
        "embodiment.cli",
        "embodiment.cli._commands",
        "embodiment.cli._output",
        "embodiment.cli._errors",
        "embodiment.explain",
        "embodiment.explain.catalog",
        "embodiment.__main__",
    }
    assert not (modules & forbidden), (
        "presence_engine must not import any front module — fronts depend on it, "
        f"never the reverse (found: {modules & forbidden})"
    )
    assert not any(m.startswith(("embodiment.cli", "embodiment.explain")) for m in modules)


def test_presence_engine_imports_no_colleague():
    modules = _imported_modules()
    assert not any(m.split(".")[0] == "colleague" for m in modules)


def test_presence_engine_imports_only_stdlib_and_embodiment():
    stdlib_ok = {"__future__", "dataclasses", "typing"}
    for module in _imported_modules():
        top = module.split(".")[0]
        assert module in stdlib_ok or top == "embodiment", module


class TestNoPresenceEventStream:
    """c33: presence stays loop-internal — no pub/sub, no emitter, no listeners."""

    def test_engine_exposes_no_stream_surface(self):
        forbidden = {
            "subscribe",
            "unsubscribe",
            "emit",
            "publish",
            "broadcast",
            "add_listener",
            "remove_listener",
            "listeners",
            "on_event",
            "events",
            "event_stream",
        }
        assert not (set(dir(PresenceEngine)) & forbidden)

    def test_module_defines_no_emitter_helper(self):
        import embodiment.presence_engine as mod

        forbidden = {"emit", "publish", "subscribe", "broadcast", "EventStream", "PresenceEvent"}
        assert not (set(vars(mod)) & forbidden)

    def test_snapshot_is_a_pull_only_artifact_fold(self):
        engine, _ = _engine()
        engine.acknowledge(ContextPacket(original="x", ack="on it"))
        snap = engine.snapshot()
        assert set(snap) == {"records", "chat", "injections"}
        # Pulling twice is idempotent and mutating the copy cannot reach back in.
        snap["chat"].append({"kind": "talk", "text": "forged"})
        snap["chat"][0]["text"] = "tampered"
        assert engine.snapshot()["chat"] == [
            {"kind": "ack", "text": "on it", "source": SOURCE_PACKET}
        ]


# ── 9. the seams t4 and t10 must conform to ───────────────────────────────────


class TestSeamContracts:
    """The published shapes the not-yet-built cortex loop and muse plug into."""

    def test_engine_satisfies_the_presence_sink_protocol(self):
        engine, _ = _engine()
        assert isinstance(engine, PresenceSink)

    def test_a_bare_callable_satisfies_the_muse_pull_seam_protocol(self):
        # t7's synchronous shape, still accepted (t10b keeps the compat lane).
        assert isinstance(_FakeMuse(), MusePullSeam)
        assert isinstance(lambda boundary: None, MusePullSeam)

    def test_the_primary_seam_is_the_drain_shape(self):
        assert isinstance(_DrainMuse(), MuseSeam)
        assert not isinstance(_FakeMuse(), MuseSeam)
        members = {name for name in vars(MuseSeam) if not name.startswith("_")}
        assert members == {"consider", "drain", "degradation"}

    def test_every_exported_name_resolves(self):
        import embodiment.presence_engine as mod

        for name in mod.__all__:
            assert hasattr(mod, name), name

    def test_presence_sink_names_exactly_the_loop_facing_beats(self):
        members = {
            name for name in vars(PresenceSink) if not name.startswith("_") and name != "active"
        }
        assert members == {"acknowledge", "on_operator_message", "on_progress_boundary"}

    def test_progress_boundary_signature_is_keyword_only(self):
        sig = inspect.signature(PresenceEngine.on_progress_boundary)
        params = [p for name, p in sig.parameters.items() if name != "self"]
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params)
        assert [p.name for p in params] == ["step_count", "phase_changed"]

    def test_muse_comment_defaults_are_inert(self):
        comment = MuseComment()
        assert comment.text == "" and comment.guidance == ""
        assert comment.tokens is None and comment.latency is None

    def test_boundary_context_is_frozen(self):
        boundary = BoundaryContext(kind=BOUNDARY_INTAKE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            boundary.kind = "other"

    def test_presence_turn_defaults(self):
        turn = PresenceTurn(kind="ack", source="packet")
        assert turn.chat_entry is None and turn.injection is None


# ── 10. the terminal boundary: drain without consider (issue #17, t4) ─────────


class _ThreeBeatSink:
    """A sink written against the THREE-beat protocol, with no terminal beat.

    The compatibility case: every sink that exists today has exactly this shape,
    so the loop must keep driving it exactly as it does now.
    """

    def __init__(self) -> None:
        self.boundaries: list[tuple[int, bool]] = []

    @property
    def active(self) -> bool:
        return True

    def acknowledge(self, packet: Optional[ContextPacket]) -> list[Any]:
        return []

    def on_operator_message(self, text: str) -> list[Any]:
        return []

    def on_progress_boundary(self, *, step_count: int = 0, phase_changed: bool = False):
        self.boundaries.append((step_count, phase_changed))
        return []


class _LaggingMuse:
    """A muse whose counsel is ready one drain AFTER the boundary that prompted it.

    That single beat of lag is the whole of issue #17. Every ordinary boundary
    gets its counsel back at the next beat; the LAST boundary considered has no
    next beat, so before this fix its counsel had nowhere to arrive and was
    dropped ``muse-insight-late`` at close — measured once per run, 4 of 4.
    """

    def __init__(self) -> None:
        self.considered: list[BoundaryContext] = []
        self.drained_at: list[int] = []
        self._in_flight: list[MuseComment] = []
        self._ready: list[MuseComment] = []

    def consider(self, boundary: BoundaryContext) -> None:
        self.considered.append(boundary)
        self._in_flight.append(
            MuseComment(guidance=f"counsel on {boundary.kind}@{boundary.step_count}")
        )

    def drain(self, *, step_count: int = 0) -> list[MuseComment]:
        self.drained_at.append(step_count)
        ready, self._ready = self._ready, self._in_flight
        self._in_flight = []
        return ready

    def degradation(self) -> Optional[str]:
        return None


class _SynthesisHost:
    """A host driving the REAL loop into its forced final synthesis turn.

    The scripted model never calls ``finish``, so the drive spends its whole
    reading budget, exits on budget with no summary, and ``run`` fires the ONE
    synthesis turn it reserved. That turn's phase notice is the terminal
    boundary — the last moment counsel can still change the output.

    ``append_guidance`` appends the advisory text to the LIVE message list the
    loop hands ``complete`` (``complete`` is handed ``ctx.messages`` itself), so
    a test can read exactly what the synthesis turn saw.
    """

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.guided: list[str] = []
        self.rendered: list[str] = []
        self.messages_at_turn: list[list[dict[str, Any]]] = []
        self._live: Optional[list[dict[str, Any]]] = None

    # the model seam ----------------------------------------------------------
    def complete(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self._live = messages
        self.messages_at_turn.append([dict(m) for m in messages])
        turn = len(self.messages_at_turn)
        return ModelResponse(
            tool_calls=[ToolCall(id=f"c{turn}", name="read_file", arguments={"path": "a.py"})]
        )

    # the tool surface --------------------------------------------------------
    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.executed.append(name)
        return ToolOutcome(result=f"{name} ok")

    # the presence IO ---------------------------------------------------------
    def _guide(self, text: str) -> None:
        self.guided.append(text)
        if self._live is not None:
            self._live.append({"role": "user", "content": text})

    def io(self) -> PresenceIO:
        return PresenceIO(
            append_guidance=self._guide,
            render=self.rendered.append,
            task_state=lambda: f"step {len(self.executed)}",
        )

    def drive(self, presence: Any, *, max_steps: int = 2) -> Any:
        return run(
            self.complete,
            Task(
                id="t4",
                repo_path="/repo",
                instruction="do the thing",
                context_packet=ContextPacket(original="do the thing", ack="on it"),
            ),
            executor=self,
            max_steps=max_steps,
            presence=presence,
        )

    def synthesis_turn(self) -> list[dict[str, Any]]:
        """The messages the LAST completion — the synthesis turn — was handed."""
        return self.messages_at_turn[-1]


class TestTerminalBoundaryDrainsWithoutConsidering:
    """Issue #17: the last counsel of a drive must not be structurally lost.

    Measured before the fix: exactly one ``muse-insight-late`` drop per run in 4
    of 4 runs — 25% of all counsel produced
    (``docs/live-test-results/muse-cycle-baseline.md``). The insight that
    stranded was the one started by the ``consider()`` on the loop's final
    boundary, so draining harder cannot reach zero: that session must not start.
    """

    def test_the_terminal_beat_drains_and_starts_no_session(self):
        muse = _DrainMuse(ready=[MuseComment(text="you never ran the tests", tokens=7)])
        engine, io = _engine(muse=muse)
        turns = engine.on_terminal_boundary(step_count=13)
        assert muse.considered == []  # THE contract: no session starts here
        assert muse.drained_at == [13]
        assert [t.source for t in turns] == [SOURCE_MUSE]
        assert io.rendered == ["presence: you never ran the tests"]

    def test_every_other_beat_still_considers(self):
        muse = _DrainMuse()
        engine, _ = _engine(muse=muse, cadence=UpdateCadence(every_steps=1))
        engine.acknowledge(ContextPacket(original="do x"))
        engine.on_operator_message("faster")
        engine.on_progress_boundary(step_count=1)
        engine.on_terminal_boundary(step_count=2)
        assert [b.kind for b in muse.considered] == [
            BOUNDARY_INTAKE,
            BOUNDARY_OPERATOR_INPUT,
            BOUNDARY_CADENCE_TICK,
        ]
        assert len(muse.drained_at) == 4  # every beat drains; only three consider

    def test_the_terminal_boundary_carries_its_own_kind(self):
        muse = _DrainMuse(ready=[MuseComment(text="one last thought")])
        engine, _ = _engine(muse=muse)
        engine.on_terminal_boundary(step_count=4)
        snap = engine.snapshot()
        assert [(r.point, r.degraded) for r in snap["records"]] == [
            (f"muse:{BOUNDARY_SYNTHESIS}", False)
        ]
        assert snap["chat"][-1]["kind"] in SENSES_CHAT_KINDS

    def test_the_terminal_beat_is_never_cadence_gated_or_capped(self):
        """Delivery is not narration: the cap bounds chatter, never the drain."""
        muse = _DrainMuse()
        engine, io = _engine(muse=muse, cadence=UpdateCadence(every_steps=1, max_updates=1))
        engine.on_progress_boundary(step_count=1)
        engine.on_progress_boundary(step_count=2)  # capped
        assert any("update cap reached" in line for line in io.rendered)
        muse.ready = [MuseComment(text="the last word")]
        engine.on_terminal_boundary(step_count=3)
        assert io.rendered[-1] == "presence: the last word"

    def test_the_terminal_beat_never_speaks_for_the_cortex(self):
        """An empty terminal drain says nothing — it is a delivery beat only."""
        muse = _DrainMuse()
        engine, io = _engine(muse=muse)
        assert engine.on_terminal_boundary(step_count=9) == []
        assert not any("still working" in line for line in io.rendered)

    def test_a_museless_terminal_beat_is_a_strict_no_op(self):
        engine, io = _engine()
        assert engine.on_terminal_boundary(step_count=3) == []
        assert (io.rendered, io.guided, io.reads) == ([], [], 0)

    def test_a_disarmed_engine_ignores_the_terminal_beat(self):
        muse = _DrainMuse(ready=[MuseComment(text="never heard")])
        engine, io = _engine(muse=muse, enabled=False)
        assert engine.on_terminal_boundary(step_count=1) == []
        assert (muse.considered, muse.drained_at, io.rendered) == ([], [], [])

    def test_a_failing_terminal_drain_degrades_and_never_raises(self):
        muse = _DrainMuse(drain_raises=RuntimeError("muse endpoint refused connection"))
        engine, io = _engine(muse=muse)
        assert engine.on_terminal_boundary(step_count=2) == []
        assert engine.mode == MODE_CORTEX_ONLY and engine.muse_degraded is True
        points = [(r.point, r.degraded) for r in engine.records]
        assert (f"muse:{BOUNDARY_SYNTHESIS}", True) in points
        assert any("muse unavailable" in line for line in io.rendered)

    def test_terminal_boundary_signature_is_keyword_only(self):
        sig = inspect.signature(PresenceEngine.on_terminal_boundary)
        params = [p for name, p in sig.parameters.items() if name != "self"]
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params)
        assert [p.name for p in params] == ["step_count"]

    def test_the_terminal_beat_is_an_optional_fourth_beat_not_a_protocol_change(self):
        """A three-beat sink is still a ``PresenceSink``; the beat is probed for."""
        assert isinstance(_ThreeBeatSink(), PresenceSink)
        members = {
            name for name in vars(PresenceSink) if not name.startswith("_") and name != "active"
        }
        assert "on_terminal_boundary" not in members


class TestTheLoopDrivesTheTerminalBoundary:
    """End to end through the real loop: the synthesis turn is the last chance."""

    def test_no_session_starts_on_the_synthesis_boundary(self):
        host = _SynthesisHost()
        muse = _LaggingMuse()
        engine = PresenceEngine(io=host.io(), muse=muse, cadence=UpdateCadence(every_steps=1))
        host.drive(engine)
        assert BOUNDARY_SYNTHESIS not in [b.kind for b in muse.considered]
        # Every considered boundary drained; the terminal beat drained ONE more.
        assert len(muse.drained_at) == len(muse.considered) + 1
        assert [r.point for r in engine.records].count(f"muse:{BOUNDARY_SYNTHESIS}") == 1

    def test_drained_counsel_reaches_the_synthesis_turns_messages(self):
        """The counsel that used to strand at close now lands in the last turn."""
        host = _SynthesisHost()
        muse = _LaggingMuse()
        engine = PresenceEngine(io=host.io(), muse=muse, cadence=UpdateCadence(every_steps=1))
        outcome = host.drive(engine)
        # One reading turn (the reserved synthesis turn is held out of the
        # reading budget) plus the synthesis turn itself.
        assert outcome.result.stats.model_turns == 2
        assert len(host.messages_at_turn) == 2
        last_counsel = f"counsel on {muse.considered[-1].kind}@{muse.considered[-1].step_count}"
        assert host.guided[-1] == last_counsel
        contents = [str(m.get("content", "")) for m in host.synthesis_turn()]
        assert last_counsel in contents

    def test_a_three_beat_sink_keeps_todays_behaviour_exactly(self):
        host = _SynthesisHost()
        sink = _ThreeBeatSink()
        host.drive(sink)
        # The optional beat is probed for, never required: an older sink still
        # sees the synthesis phase as an ordinary phase-changed boundary.
        assert (1, True) in sink.boundaries
