"""The daemon application: wiring, one ear, zero clients (plan task ``t15``).

Every seam is injected and faked here. No gateway is dialled, no device is
opened, no key is read: the live rig is ``t21``'s job. The three acceptance
criteria this file proves, and where:

1. *zero clients* — :class:`TestZeroClients`.
2. *one ear, one recorded handover* — :class:`TestOneEar`.
3. *every injected failure degrades and keeps running* — :class:`TestInjectedFailures`.

The rest is the attack surface: stale frames, hostile transcripts, the same
call ten thousand times, a shutdown that must stay bounded, and a marker scan
that proves no record carries speech.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from embodiment.audio.endpoint import EndpointCloseReport, NullEndpoint
from embodiment.audio.features import FeatureExtractor
from embodiment.bus import Bus
from embodiment.contract import ModelResponse
from embodiment.daemon import app as app_module
from embodiment.daemon.app import AppConfig, DaemonApp
from embodiment.daemon.state import DaemonState
from embodiment.memory import RoomMemory
from embodiment.realtime import wire
from embodiment.voice import Voice, VoiceConfig

SPEECH = "שלום גוון"
REPLY = "שלום לך."


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeEndpoint:
    """An :class:`~embodiment.audio.endpoint.AudioEndpoint` that records everything."""

    def __init__(self, *, name: str = "fake", fail_on: str = "", sample_rate: int = 24000) -> None:
        self.name = name
        self._sample_rate = sample_rate
        self.fail_on = fail_on
        self.on_frame: Any = None
        self.attached = False
        self.capturing = False
        self.played: list[bytes] = []
        self.stopped_playback = 0
        self.closed = False
        self._muted = False
        self._playing = False

    def _maybe_fail(self, what: str) -> None:
        if self.fail_on == what:
            raise RuntimeError(f"endpoint refuses to {what}")

    def attach(self) -> None:
        self._maybe_fail("attach")
        self.attached = True

    def detach(self) -> None:
        self.attached = False
        self.capturing = False

    def start_capture(self, on_frame: Any) -> None:
        self._maybe_fail("start_capture")
        self.on_frame = on_frame
        self.capturing = True

    def stop_capture(self) -> None:
        self.capturing = False

    def play(self, frames: bytes) -> None:
        self.played.append(bytes(frames))
        self._playing = True

    def stop_playback(self) -> int:
        self.stopped_playback += 1
        self._playing = False
        return 0

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def mute(self, muted: bool) -> None:
        self._muted = bool(muted)

    @property
    def muted(self) -> bool:
        return self._muted

    def close(self, deadline: float) -> EndpointCloseReport:
        self.closed = True
        self.capturing = False
        self.attached = False
        return EndpointCloseReport(
            capture_thread_stopped=True,
            writer_thread_stopped=True,
            samples_discarded=0,
            elapsed_s=0.0,
        )

    def status(self) -> dict[str, object]:
        return {"name": self.name, "attached": self.attached, "capturing": self.capturing}


class FakeEars:
    """An ears-only realtime client: one async connect, one event stream, one close."""

    def __init__(
        self,
        *,
        connect_ok: bool = True,
        events: tuple[Any, ...] = (),
        connect_delay: float = 0.0,
        close_delay: float = 0.0,
        graceful: bool = True,
        sample_rate: int = 24000,
    ) -> None:
        self.sample_rate = sample_rate
        self.connect_ok = connect_ok
        self.connect_delay = connect_delay
        self.close_delay = close_delay
        self.graceful = graceful
        self.close_deadlines: list[Optional[float]] = []
        self.initial = list(events)
        self.sent: list[bytes] = []
        self.connect_calls = 0
        self.closed = False
        self._queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()

    async def connect(self) -> bool:
        self.connect_calls += 1
        self.closed = False
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        for event in self.initial:
            self._queue.put_nowait(event)
        self._ready.set()
        return self.connect_ok

    def send_audio(self, pcm: bytes) -> bool:
        self.sent.append(bytes(pcm))
        return True

    async def events(self) -> Any:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self, deadline: Optional[float] = None) -> Any:
        self.close_deadlines.append(deadline)
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed = True
        if self._queue is not None:
            self._queue.put_nowait(None)
        return SimpleNamespace(graceful=self.graceful)

    def status(self) -> dict[str, Any]:
        return {"connected": self.connect_ok and not self.closed, "fake": True}

    # test-side driving
    def emit(self, event: Any, *, timeout: float = 15.0) -> None:
        assert self._ready.wait(timeout), "ears never connected"
        assert self._loop is not None and self._queue is not None
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)


class _LiveStdout:
    """A pipe that stays open until the child is told to stop.

    A ``BytesIO`` returns EOF on the first read, which the real endpoint
    correctly reads as "the capture child exited" and degrades on — so a fake
    child needs a stdout that blocks the way a live one does.
    """

    def __init__(self, process: "_FakePopen") -> None:
        self._process = process

    def read(self, size: int = -1) -> bytes:
        while self._process.returncode is None:
            time.sleep(0.005)
        return b""

    def close(self) -> None:
        return None


class _FakePopen:
    """A child process that starts, stays up, produces nothing, and exits when told.

    Stands in for ``pw-record``/``pw-play`` so a test can exercise the paths
    past the PATH probe without spawning a real audio process. Nothing here
    ever reaches PipeWire or ALSA.
    """

    def __init__(self, argv: Any = (), **kwargs: Any) -> None:
        self.argv = argv
        self.returncode: Optional[int] = None
        self.stdout = _LiveStdout(self)
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO()

    def poll(self) -> Optional[int]:
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def silent_pcm(blocks: int = 1) -> bytes:
    return b"\x00\x01" * (800 * blocks)


def make_complete(reply: str = REPLY, *, fail: bool = False) -> Any:
    def complete(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
        if fail:
            raise RuntimeError("gateway is down")
        complete.seen.append(messages)  # type: ignore[attr-defined]
        return ModelResponse(content=reply, prompt_tokens=3, completion_tokens=4)

    complete.seen = []  # type: ignore[attr-defined]
    return complete


class Harness:
    """One fully-wired app over fakes, plus the bus subscription that watches it."""

    def __init__(self, app: DaemonApp, bus: Bus, ears: FakeEars, state: DaemonState) -> None:
        self.app = app
        self.bus = bus
        self.ears = ears
        self.state = state
        self.declared_rates: list[int] = []
        self.subscription = bus.subscribe(include_speech=True)

    def events(self, kind: Optional[str] = None) -> list[Any]:
        self._seen = getattr(self, "_seen", []) + self.subscription.drain()
        if kind is None:
            return list(self._seen)
        return [event for event in self._seen if event.kind == kind]

    def clear(self) -> None:
        """Forget every event so far: the next :meth:`events` starts from now."""
        self.subscription.drain()
        self._seen = []

    def ledger_codes(self) -> list[str]:
        return [record.code for record in self.state.ledger.read_all()]


def _bounded_teardown(call: Any, *, timeout: float = 3.0) -> None:
    thread = threading.Thread(target=lambda: _swallow(call), daemon=True)
    thread.start()
    thread.join(timeout=timeout)


def _swallow(call: Any) -> None:
    try:
        call()
    except Exception:  # noqa: BLE001 - teardown is best-effort in a test harness
        pass


@pytest.fixture
def harness(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    """A factory: ``harness(**overrides)`` builds one wired :class:`DaemonApp`."""
    built: list[DaemonApp] = []

    def build(**overrides: Any) -> Harness:
        index = len(built)
        state = overrides.pop("state", None) or DaemonState(tmp_path / f"state{index}")
        bus = overrides.pop("bus", None) or Bus()
        ears = overrides.pop("ears", None) or FakeEars()
        rates: list[int] = []

        def ears_factory(rate: int) -> Any:
            rates.append(rate)
            built = overrides.get("_ears_for_rate", {}).get(rate)
            return built if built is not None else ears

        overrides.pop("_ears_for_rate", None)
        recall_fn = overrides.pop("recall_fn", None) or (
            lambda query, **kwargs: SimpleNamespace(ok=True, records=[], degradation=None)
        )
        memory = overrides.pop("memory", None) or RoomMemory(
            tmp_path / f"memory{index}",
            scope="gwen",
            recall_fn=recall_fn,
            remember_fn=lambda text, **kwargs: SimpleNamespace(
                ok=True, record_id="r1", degradation=None
            ),
            embed_probe=lambda: False,
        )
        synthesize = overrides.pop("synthesize", None) or (lambda sentence, config: b"\x00\x00")
        complete = overrides.pop("complete", None) or make_complete()
        endpoints = overrides.pop("endpoints", None)

        def endpoint_factory() -> Any:
            if endpoints is None:
                return FakeEndpoint(name="host")
            return endpoints()

        def voice_factory(endpoint: Any) -> Voice:
            return Voice(
                endpoint=endpoint,
                config=VoiceConfig(gateway_url="http://gateway.invalid"),
                bus=bus,
                features=FeatureExtractor(),
                synthesize=synthesize,
            )

        app = DaemonApp(
            config=overrides.pop("config", None) or AppConfig(poll_interval_s=0.01),
            state=state,
            bus=bus,
            memory=memory,
            complete=complete,
            ears_factory=overrides.pop("ears_factory", ears_factory),
            endpoint_factory=overrides.pop("endpoint_factory", endpoint_factory),
            voice_factory=voice_factory,
            **overrides,
        )
        built.append(app)
        # Bounded, because a test may deliberately hand the app a seam that
        # hangs; teardown must not inherit that.
        request.addfinalizer(lambda: _bounded_teardown(lambda: app.close(deadline=2.0)))
        request.addfinalizer(lambda: _bounded_teardown(lambda: memory.close(deadline=1.0)))
        request.addfinalizer(lambda: _bounded_teardown(lambda: bus.close(deadline=1.0)))
        harness_obj = Harness(app, bus, ears, state)
        harness_obj.declared_rates = rates
        return harness_obj

    return build


# ── criterion 1: zero clients ─────────────────────────────────────────────────


class TestZeroClients:
    """With nobody watching, a full turn still happens; a watcher changes nothing."""

    def test_a_full_turn_completes_with_zero_clients(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.status()["clients"]["count"] == 0

        result = h.app.run_turn(SPEECH)

        assert result.spoken == REPLY
        assert h.app.status()["clients"]["count"] == 0
        assert [e.data["text"] for e in h.events("transcript")] == [SPEECH]
        assert [e.data["text"] for e in h.events("reply")] == [REPLY]
        assert h.app.status()["turns"]["completed"] == 1

    def test_attaching_then_detaching_a_client_changes_no_daemon_state(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        _settle(h.app)
        before = _status_without_clients(h.app)

        h.app.attach_client()
        h.app.attach_client(remote=True)
        h.app.detach_client()
        h.app.detach_client(remote=True)

        assert _status_without_clients(h.app) == before

    def test_the_client_count_is_published_as_information(self, harness: Any) -> None:
        h = harness()
        h.app.attach_client()
        h.app.attach_client(remote=True)
        h.app.detach_client()

        counts = [event.data["count"] for event in h.events("clients")]
        assert counts == [1, 2, 1]
        assert [event.data["remote"] for event in h.events("clients")] == [0, 1, 1]

    def test_the_turn_runs_identically_with_and_without_clients(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        first = h.app.run_turn(SPEECH)
        h.app.attach_client()
        h.app.attach_client()
        second = h.app.run_turn(SPEECH)
        assert first.spoken == second.spoken
        assert h.app.status()["turns"]["completed"] == 2

    def test_a_client_count_of_zero_never_blocks_speech(self, harness: Any) -> None:
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.attach_ear("host", endpoint)
        h.app.run_turn(SPEECH)
        assert endpoint.played, "nothing was queued to the speaker"


def _settle(app: DaemonApp, *, timeout: float = 15.0) -> None:
    """Wait until the daemon's own status stops moving.

    The voice paces the last reply on its own thread, so ``status()`` keeps
    changing for a fraction of a second after a turn — and not monotonically:
    the pace worker empties the buffer FIRST and feeds the feature extractor
    AFTER, so "pending_pace_bytes == 0" alone is a window, not quiescence
    (measured: that window flaked this test once in three runs under
    ``-n auto``). Settling on two identical consecutive snapshots is what
    makes the comparison below a statement about clients rather than timing.
    """
    deadline = time.monotonic() + timeout
    previous: Optional[str] = None
    while time.monotonic() < deadline:
        app.pump()
        current = _status_without_clients(app)
        if current == previous:
            return
        previous = current
        time.sleep(0.05)
    raise AssertionError("the daemon never settled")


def _status_without_clients(app: DaemonApp) -> str:
    status = app.status()
    status.pop("clients", None)
    return json.dumps(status, sort_keys=True, ensure_ascii=False)


# ── criterion 2: one ear ──────────────────────────────────────────────────────


class TestOneEar:
    """Exactly one attached endpoint, and the handover is said out loud."""

    def test_a_second_ear_pre_empts_with_exactly_one_handover_event(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        second = FakeEndpoint(name="browser")
        h.app.attach_ear("host", first)
        h.clear()  # forget the first attach
        handover = h.app.attach_ear("browser", second)

        assert handover.attached and handover.preempted and not handover.refused
        assert handover.previous == "host"
        states = [e for e in h.events("state") if e.data.get("component") == "ear"]
        assert len(states) == 1, states
        assert states[0].data["status"] == "handover"
        assert states[0].data["ear"] == "browser"
        assert states[0].data["previous"] == "host"
        assert app_module.APP_EAR_PREEMPTED in h.ledger_codes()

    def test_a_second_ear_is_refused_visibly(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=False, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        second = FakeEndpoint(name="browser")
        h.app.attach_ear("host", first)
        h.clear()
        handover = h.app.attach_ear("browser", second)

        assert not handover.attached and handover.refused
        assert handover.ear == "browser" and handover.previous == "host"
        states = [e for e in h.events("state") if e.data.get("component") == "ear"]
        assert len(states) == 1
        assert states[0].data["status"] == "refused"
        assert app_module.APP_EAR_REFUSED in h.ledger_codes()
        assert h.app.status()["ear"]["active"] == "host"
        assert second.attached is False

    def test_a_pre_empted_ear_stops_capturing_before_the_new_one_starts(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        second = FakeEndpoint(name="browser")
        h.app.attach_ear("host", first)
        h.app.attach_ear("browser", second)
        assert first.capturing is False and first.attached is False
        assert second.capturing is True
        assert h.app.status()["ear"]["active"] == "browser"

    def test_never_two_capture_callbacks_feed_the_ears(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        second = FakeEndpoint(name="browser")
        h.app.attach_ear("host", first)
        h.app.attach_ear("browser", second)

        first.on_frame(silent_pcm())  # the displaced ear's capture thread, one frame late
        second.on_frame(silent_pcm())

        assert len(h.ears.sent) == 1, "a displaced ear still reached the gateway"
        assert h.app.status()["audio"]["frames_from_stale_ear"] == 1
        # One late frame is what a handover looks like, not a fault.
        assert app_module.APP_FRAME_FROM_STALE_EAR not in h.ledger_codes()

    def test_one_late_frame_is_published_but_not_recorded(self, harness: Any) -> None:
        """Seen on the first live stop: the handover swap put one in the ledger."""
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        h.app.attach_ear("host", first)
        h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        h.clear()

        first.on_frame(silent_pcm())

        assert app_module.APP_FRAME_FROM_STALE_EAR not in h.ledger_codes()
        assert h.events("degradation") == []
        stale = [e for e in h.events("state") if e.data.get("status") == "stale-frame"]
        assert len(stale) == 1
        assert stale[0].data["stale_frames"] == 1
        assert h.app.status()["audio"]["frames_from_stale_ear"] == 1

    def test_an_ear_that_will_not_stop_is_recorded(self, harness: Any) -> None:
        """A count that keeps growing past the tolerance IS the fault."""
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        h.app.attach_ear("host", first)
        h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        h.clear()

        frame = silent_pcm()
        for _ in range(app_module._STALE_FRAME_TOLERANCE):
            first.on_frame(frame)
        assert app_module.APP_FRAME_FROM_STALE_EAR not in h.ledger_codes()

        for _ in range(40):  # it still will not stop
            first.on_frame(frame)

        records = [c for c in h.ledger_codes() if c == app_module.APP_FRAME_FROM_STALE_EAR]
        assert len(records) == 1, "one record per runaway ear, not one per frame"
        stale = [e for e in h.events("state") if e.data.get("status") == "stale-frame"]
        assert len(stale) == 2, "published on the first frame and on the crossing only"

    def test_a_new_handover_starts_the_stale_count_again(self, harness: Any) -> None:
        """The per-generation count is kept for one generation, so nothing accrues."""
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="a")
        h.app.attach_ear("a", first)
        second = FakeEndpoint(name="b")
        h.app.attach_ear("b", second)
        first.on_frame(silent_pcm())
        h.app.attach_ear("c", FakeEndpoint(name="c"))
        second.on_frame(silent_pcm())

        assert h.app.status()["audio"]["frames_from_stale_ear"] == 2
        assert app_module.APP_FRAME_FROM_STALE_EAR not in h.ledger_codes()

    def test_a_refused_ear_never_feeds_the_ears(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=False, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        second = FakeEndpoint(name="browser")
        h.app.attach_ear("host", first)
        h.app.attach_ear("browser", second)
        assert second.on_frame is None
        first.on_frame(silent_pcm())
        assert len(h.ears.sent) == 1

    def test_re_attaching_the_same_name_is_still_one_handover(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("host", FakeEndpoint())
        h.clear()
        h.app.attach_ear("host", FakeEndpoint())
        states = [e for e in h.events("state") if e.data.get("component") == "ear"]
        assert len(states) == 1
        assert h.app.status()["ear"]["handovers"] == 1

    def test_detaching_publishes_one_state_event_and_leaves_no_ear(self, harness: Any) -> None:
        h = harness()
        endpoint = FakeEndpoint()
        h.app.attach_ear("host", endpoint)
        h.clear()
        outcome = h.app.detach_ear()
        assert outcome.attached is False and outcome.previous == "host"
        states = [e for e in h.events("state") if e.data.get("component") == "ear"]
        assert len(states) == 1 and states[0].data["status"] == "detached"
        assert h.app.status()["ear"]["active"] is None
        assert endpoint.closed is True

    def test_the_ear_name_is_restricted_to_a_safe_charset(self, harness: Any) -> None:
        h = harness()
        handover = h.app.attach_ear("../../etc/passwd\x00‮", FakeEndpoint())
        assert "/" not in handover.ear and "\x00" not in handover.ear
        assert "‮" not in handover.ear
        blob = json.dumps(h.app.status(), ensure_ascii=False)
        assert "/etc/passwd" not in blob

    def test_hot_mic_on_attach_is_published_and_mutable(self, harness: Any) -> None:
        h = harness()
        endpoint = FakeEndpoint()
        h.app.attach_ear("host", endpoint)
        mics = h.events("mic")
        assert mics and mics[-1].data["hot"] is True
        h.app.set_mute(True)
        assert endpoint.muted is True
        assert h.events("mic")[-1].data["hot"] is False

    def test_an_endpoint_that_refuses_to_capture_is_recorded_not_attached(
        self, harness: Any
    ) -> None:
        h = harness()
        handover = h.app.attach_ear("host", FakeEndpoint(fail_on="start_capture"))
        assert handover.attached is False
        assert app_module.APP_EAR_ATTACH_FAILED in h.ledger_codes()
        assert h.app.status()["ear"]["active"] is None


# ── criterion 3: every injected failure degrades and keeps running ────────────


class TestInjectedFailures:
    """Five injected faults. Each: one ledger record, one bus event, a live daemon."""

    def _assert_degraded_and_alive(self, h: Harness, code: str, *, timeout: float = 15.0) -> None:
        """Both facts, and the daemon still answering. Eventually, not atomically.

        The daemon appends to the crash ledger BEFORE it publishes the
        ``degradation`` event, deliberately (the ledger is the record that
        must survive a kill; see ``DaemonApp._record``). So a poll that stops
        the moment the ledger record appears can drain the bus before the
        publish has run — measured on ``realtime/t15`` at ``28a4b46``:
        4 of 25 runs of the dead-gateway test failed exactly there, with
        ``app-ears-unavailable not published: []``. Waiting for both facts is
        the assertion the criterion actually makes; asserting them
        simultaneously was asserting an ordering the code does not promise.
        """
        deadline = time.monotonic() + timeout
        while True:
            in_ledger = code in h.ledger_codes()
            published = [e.data["code"] for e in h.events("degradation")]
            if in_ledger and code in published:
                break
            assert (
                time.monotonic() < deadline
            ), f"{code}: in_ledger={in_ledger} published={published}"
            time.sleep(0.02)
        assert isinstance(h.app.status(), dict)

    def test_a_dead_gateway_degrades_and_the_daemon_keeps_running(self, harness: Any) -> None:
        h = harness(ears=FakeEars(connect_ok=False))
        h.app.start()
        self._assert_degraded_and_alive(h, app_module.APP_EARS_UNAVAILABLE)
        assert h.app.run_turn(SPEECH).spoken == REPLY

    def test_an_stt_error_degrades_and_the_next_turn_still_runs(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.ears.emit(wire.ServerError(code="stt_failed", message="whisper fell over"))
        self._assert_degraded_and_alive(h, app_module.APP_STT_ERROR)
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert "whisper fell over" not in json.dumps(
            [r.to_dict() for r in h.state.ledger.read_all()]
        )

    def test_a_tts_error_degrades_and_the_next_turn_still_runs(self, harness: Any) -> None:
        def exploding_synth(sentence: str, config: Any) -> bytes:
            raise RuntimeError("tts is down")

        h = harness(synthesize=exploding_synth)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        self._assert_degraded_and_alive(h, "voice-tts-failed")
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert h.app.status()["turns"]["completed"] == 2

    def test_no_device_degrades_and_the_daemon_still_speaks_into_the_void(
        self, harness: Any
    ) -> None:
        def no_device() -> Any:
            raise RuntimeError("PortAudio is not installed")

        h = harness(endpoints=no_device)
        h.app.start()
        self._assert_degraded_and_alive(h, app_module.APP_NO_ENDPOINT)
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert [e.data["text"] for e in h.events("reply")] == [REPLY]
        assert h.app.status()["ear"]["active"] is not None

    def test_no_backend_leaves_a_HOST_ear_that_is_degraded_and_recoverable(
        self, harness: Any
    ) -> None:
        """The case a host actually meets: no audio backend on PATH.

        t7 round 4 replaced the sounddevice import with subprocess audio, so
        "no device" is now "no pipewire or alsa on PATH". The real
        :class:`HostEndpoint` still degrades at ``attach`` rather than
        raising, so the ear is ``host`` and *deaf*, never ``null`` — and a
        later stop/start builds a fresh endpoint that re-probes PATH, which
        is the recovery a NullEndpoint could not offer.
        """
        from embodiment.audio.host import DEGRADED_NO_BACKEND, HostEndpoint

        def no_backend() -> Any:
            return HostEndpoint(which=lambda name: None)

        h = harness(endpoints=no_backend)
        h.app.start()
        status = h.app.status()
        assert status["ear"]["active"] == "host"
        assert status["ear"]["kind"] == "HostEndpoint"
        assert status["ear"]["degraded"] is True
        self._assert_degraded_and_alive(h, DEGRADED_NO_BACKEND)
        assert app_module.APP_NO_ENDPOINT not in h.ledger_codes()
        assert h.app.run_turn(SPEECH).spoken == REPLY

    def test_a_stop_then_start_re_probes_for_a_backend_that_appeared(self, harness: Any) -> None:
        """The recovery the degraded host ear exists for, driven as a host would."""
        from embodiment.audio.host import HostEndpoint

        present = {"path": False}

        def probing() -> Any:
            return HostEndpoint(
                which=lambda name: "/usr/bin/pw-record" if present["path"] else None,
                popen=_FakePopen,
            )

        h = harness(endpoints=probing)
        h.app.start()
        assert h.app.status()["ear"]["degraded"] is True

        present["path"] = True  # the operator installs pipewire
        controls = h.app.controls()
        controls.stop_voice()
        controls.start_voice()
        assert h.app.status()["ear"]["active"] == "host"
        assert h.app.status()["ear"]["degraded"] is False

    def test_a_box_without_proc_asound_cards_degrades_rather_than_raising(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """A container with no ``/proc/asound/cards`` must not take the daemon down."""
        from embodiment.audio.host import HostEndpoint

        missing = tmp_path / "no-such-cards"

        def without_cards() -> Any:
            return HostEndpoint(
                device="usb",
                which=lambda name: f"/usr/bin/{name}",
                cards_path=missing,
                popen=_FakePopen,
            )

        h = harness(endpoints=without_cards)
        h.app.start()
        assert h.app.status()["ear"]["active"] == "host"
        assert isinstance(h.app.status(), dict)
        assert h.app.run_turn(SPEECH).spoken == REPLY

    def test_a_backend_that_cannot_be_spawned_degrades(self, harness: Any) -> None:
        from embodiment.audio.host import DEGRADED_OPEN, HostEndpoint

        def refusing_popen(*args: Any, **kwargs: Any) -> Any:
            raise OSError(2, "No such file or directory")

        def cannot_spawn() -> Any:
            return HostEndpoint(which=lambda name: f"/usr/bin/{name}", popen=refusing_popen)

        h = harness(endpoints=cannot_spawn)
        h.app.start()
        self._assert_degraded_and_alive(h, DEGRADED_OPEN)
        assert h.app.status()["ear"]["active"] == "host"
        assert h.app.run_turn(SPEECH).spoken == REPLY

    def test_a_factory_that_fails_leaves_a_NULL_ear_instead(self, harness: Any) -> None:
        """The other route: nothing to recover, so the stand-in is named as such."""

        def no_factory() -> Any:
            raise RuntimeError("PortAudio is not installed")

        h = harness(endpoints=no_factory)
        h.app.start()
        status = h.app.status()
        assert status["ear"]["active"] == "null"
        assert status["ear"]["kind"] == "NullEndpoint"
        assert status["ear"]["degraded"] is True
        assert app_module.APP_NO_ENDPOINT in h.ledger_codes()

    def test_a_recall_timeout_degrades_and_the_turn_continues(self, harness: Any) -> None:
        def slow_recall(query: str, **kwargs: Any) -> Any:
            time.sleep(1.0)
            return SimpleNamespace(ok=True, records=[], degradation=None)

        h = harness(
            recall_fn=slow_recall,
            config=AppConfig(recall_deadline=0.05, poll_interval_s=0.01),
        )
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert result.spoken == REPLY
        self._assert_degraded_and_alive(h, "recall-deadline-exceeded")
        assert h.app.status()["recall"]["mode"] is None

    def test_the_recall_mode_actually_in_effect_is_reported(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert h.app.status()["recall"]["mode"] == "lexical"
        assert h.app.status()["recall"]["semantic"] is False

    def test_an_endpoint_that_attaches_degraded_says_so(self, harness: Any) -> None:
        """An ear with no driver behind it still attaches — and must not look healthy."""
        h = harness()
        h.app.attach_ear("host", NullEndpoint())
        self._assert_degraded_and_alive(h, "endpoint-null")

    def test_a_model_seam_that_raises_still_speaks_a_fallback(self, harness: Any) -> None:
        h = harness(complete=make_complete(fail=True))
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert result.spoken
        assert any(code.startswith("turn-") for code in h.ledger_codes())
        assert h.app.run_turn(SPEECH).spoken


# ── the ears loop, the turn queue and shutdown ────────────────────────────────


class TestEarsLoop:
    def test_a_transcription_drives_one_turn_end_to_end(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.ears.emit(wire.TranscriptionCompleted(text=SPEECH, item_id="i1"))
        deadline = time.monotonic() + 15.0
        while h.app.status()["turns"]["completed"] < 1:
            assert time.monotonic() < deadline, "no turn ran"
            time.sleep(0.02)
        assert [e.data["text"] for e in h.events("reply")] == [REPLY]

    def test_speech_started_during_playback_is_the_barge_in(self, harness: Any) -> None:
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.start()
        endpoint._playing = True
        h.ears.emit(wire.SpeechStarted(item_id="i1"))
        deadline = time.monotonic() + 15.0
        while endpoint.stopped_playback == 0:
            assert time.monotonic() < deadline, "playback was never stopped"
            time.sleep(0.02)

    def test_a_captured_frame_reaches_the_ears_and_the_bus(self, harness: Any) -> None:
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.start()
        endpoint.on_frame(silent_pcm(2))
        assert len(h.ears.sent) == 1
        features = [e for e in h.events("features") if e.data["direction"] == "in"]
        assert len(features) == 2

    def test_played_audio_is_traced_out_to_the_bus(self, harness: Any) -> None:
        h = harness(synthesize=lambda sentence, config: silent_pcm(4))
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        deadline = time.monotonic() + 15.0
        while True:
            h.app.pump()
            out = [e for e in h.events("features") if e.data["direction"] == "out"]
            if out:
                break
            assert time.monotonic() < deadline, "played audio was never traced"
            time.sleep(0.02)

    def test_the_transcript_text_is_verbatim(self, harness: Any) -> None:
        hostile = "  שלום‮  ‏gwen  "
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(hostile)
        assert [e.data["text"] for e in h.events("transcript")] == [hostile]


class TestEmptyCommits:
    """An empty commit is the segmenter working, not a fault (round 4).

    Measured on the rig: 15 VAD commits in 60 s of the operator speaking,
    7 of them with an empty transcript. At that rate a ledger record each
    would bury the real faults under the sound of a quiet room.
    """

    def test_an_empty_commit_is_counted_and_published_never_recorded(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.clear()
        for _ in range(7):
            assert h.app.submit_transcript("") is False

        assert h.ledger_codes() == [] or "app-transcript-empty" not in h.ledger_codes()
        assert h.events("degradation") == []
        transcripts = h.app.status()["transcripts"]
        assert transcripts["empty_commits"] == 7
        assert transcripts["received"] == 0
        states = [e for e in h.events("state") if e.data.get("status") == "empty-commit"]
        assert len(states) == 7
        assert states[-1].data == {
            "component": "ears",
            "status": "empty-commit",
            "empty_commits": 7,
            "transcripts": 0,
        }

    def test_an_empty_commit_never_takes_a_queue_slot(self, harness: Any) -> None:
        """Seven a minute must not be able to displace a real utterance."""
        h = harness(config=AppConfig(turn_queue_size=2, poll_interval_s=0.01))
        for _ in range(50):
            h.app.submit_transcript("   \u2028\u0085  ")
        assert h.app.status()["turns"]["queued"] == 0
        assert h.app.submit_transcript(SPEECH) is True
        assert h.app.status()["turns"]["queued"] == 1
        assert app_module.APP_TURN_QUEUE_FULL not in h.ledger_codes()

    def test_whitespace_separators_count_as_empty(self, harness: Any) -> None:
        """The separators ``splitlines`` honours are a quiet room, not speech."""
        for text in ("", "   ", "\u2028", "\u2029", "\u0085", "\r\n", "\t"):
            assert app_module.classify_transcript(text) == app_module.TRANSCRIPT_EMPTY
        assert app_module.classify_transcript("\x00") == app_module.TRANSCRIPT_SPEECH
        assert app_module.classify_transcript(SPEECH) == app_module.TRANSCRIPT_SPEECH
        for value in (None, 17, b"bytes", {"a": 1}):
            assert app_module.classify_transcript(value) == app_module.TRANSCRIPT_NOT_TEXT

    def test_a_transcript_that_is_not_text_is_still_a_degradation(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.clear()
        assert h.app.submit_transcript(b"pcm bytes") is False
        assert app_module.APP_TRANSCRIPT_NOT_TEXT in h.ledger_codes()
        published = [e.data["code"] for e in h.events("degradation")]
        assert app_module.APP_TRANSCRIPT_NOT_TEXT in published
        assert h.app.status()["transcripts"]["not_text"] == 1

    def test_an_empty_commit_from_the_ear_starts_no_turn(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.ears.emit(wire.TranscriptionCompleted(text="", item_id="i1"))
        h.ears.emit(wire.TranscriptionCompleted(text=SPEECH, item_id="i2"))
        deadline = time.monotonic() + 15.0
        while h.app.status()["turns"]["completed"] < 1:
            assert time.monotonic() < deadline, "the real utterance never ran"
            time.sleep(0.02)
        time.sleep(0.2)
        status = h.app.status()
        assert status["turns"]["completed"] == 1, "the empty commit started a turn"
        assert status["transcripts"]["empty_commits"] == 1
        assert status["transcripts"]["received"] == 1

    def test_a_queued_transcript_is_counted_exactly_once(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.ears.emit(wire.TranscriptionCompleted(text=SPEECH, item_id="i1"))
        deadline = time.monotonic() + 15.0
        while h.app.status()["turns"]["completed"] < 1:
            assert time.monotonic() < deadline, "no turn ran"
            time.sleep(0.02)
        assert h.app.status()["transcripts"]["received"] == 1

    def test_the_counts_survive_a_status_probe_and_are_json_safe(self, harness: Any) -> None:
        h = harness()
        h.app.submit_transcript("")
        h.app.submit_transcript(None)
        blob = json.loads(json.dumps(h.app.status(), ensure_ascii=False))
        assert blob["transcripts"] == {"received": 0, "empty_commits": 1, "not_text": 1}


class TestDeclaredSampleRate:
    """Decision 15 (#85): the session declares the EAR's rate, never an assumption."""

    def test_a_16k_ear_dials_a_16k_session(self, harness: Any) -> None:
        h = harness(endpoints=lambda: FakeEndpoint(name="host", sample_rate=16000))
        h.app.start()
        assert h.declared_rates == [16000]
        status = h.app.status()
        assert status["ear"]["sample_rate"] == 16000
        assert status["ear"]["declared_sample_rate"] == 16000

    def test_a_24k_ear_dials_a_24k_session(self, harness: Any) -> None:
        h = harness(endpoints=lambda: FakeEndpoint(name="browser", sample_rate=24000))
        h.app.start()
        assert h.declared_rates == [24000]
        assert h.app.status()["ear"]["declared_sample_rate"] == 24000

    def test_a_handover_between_rates_re_dials(self, harness: Any) -> None:
        """The rate is in the session's URL, so it cannot change under a live one."""
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("host", FakeEndpoint(name="host", sample_rate=16000))
        assert h.declared_rates == [16000]
        h.clear()

        h.app.attach_ear("browser", FakeEndpoint(name="browser", sample_rate=24000))

        assert h.declared_rates == [16000, 24000], "the session kept the old rate"
        assert app_module.APP_EARS_REDIALLED in h.ledger_codes()
        status = h.app.status()
        assert status["ear"]["declared_sample_rate"] == 24000
        assert status["ear"]["sessions"] == 2
        assert status["ear"]["redials"] == 1
        dialling = [e for e in h.events("state") if e.data.get("status") == "dialling"]
        assert dialling and dialling[-1].data["input_sample_rate"] == 24000

    def test_a_handover_at_the_same_rate_keeps_the_session(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("a", FakeEndpoint(name="a", sample_rate=16000))
        h.app.attach_ear("b", FakeEndpoint(name="b", sample_rate=16000))
        assert h.declared_rates == [16000], "a same-rate handover re-dialled"
        assert h.app.status()["ear"]["sessions"] == 1
        assert app_module.APP_EARS_REDIALLED not in h.ledger_codes()

    def test_the_real_host_endpoint_declares_its_native_rate(self, harness: Any) -> None:
        """Against the real class, not a fake: 16 kHz native after t7 round 6."""
        from embodiment.audio.host import CAPTURE_RATE_HZ, HostEndpoint

        h = harness(endpoints=lambda: HostEndpoint(which=lambda name: None))
        h.app.start()
        assert CAPTURE_RATE_HZ == 16000
        assert h.declared_rates == [16000]

    def test_the_null_endpoint_declares_its_own_rate(self, harness: Any) -> None:
        from embodiment.audio.endpoint import SAMPLE_RATE_HZ

        h = harness(endpoints=lambda: None)
        h.app.start()
        assert h.app.status()["ear"]["active"] == "null"
        assert h.declared_rates == [SAMPLE_RATE_HZ]

    def test_the_browser_ear_declares_24k_by_contract_not_by_fallback(self, harness: Any) -> None:
        """t14 round 3 gave :class:`RemoteEndpoint` the protocol's ``sample_rate``.

        The distinction this pins is the one that mattered while it was
        missing: before, a browser ear produced 24000 because the *fallback*
        happens to be the wire default, with ``app-ear-rate-unknown`` in the
        ledger saying so. Now it comes from the endpoint itself, and the
        absence of that record is the evidence.
        """
        from embodiment.audio.remote import RemoteEndpoint

        endpoint = RemoteEndpoint(secret="s" * 32, port=0)
        assert app_module._endpoint_rate_or_none(endpoint) == 24000

        h = harness()
        h.app.attach_ear("browser", endpoint)
        try:
            assert h.declared_rates == [24000]
            assert app_module.APP_EAR_RATE_UNKNOWN not in h.ledger_codes()
            status = h.app.status()
            assert status["ear"]["sample_rate"] == 24000
            assert status["ear"]["declared_sample_rate"] == 24000
        finally:
            h.app.detach_ear()

    def test_a_host_to_browser_handover_re_dials_from_16k_to_24k(self, harness: Any) -> None:
        """The two real endpoints, at their two real rates, in one handover."""
        from embodiment.audio.host import CAPTURE_RATE_HZ, HostEndpoint
        from embodiment.audio.remote import RemoteEndpoint

        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("host", HostEndpoint(which=lambda name: None))
        assert h.declared_rates == [CAPTURE_RATE_HZ]

        browser = RemoteEndpoint(secret="s" * 32, port=0)
        h.app.attach_ear("browser", browser)
        try:
            assert h.declared_rates == [CAPTURE_RATE_HZ, 24000]
            assert app_module.APP_EARS_REDIALLED in h.ledger_codes()
            assert app_module.APP_EAR_RATE_UNKNOWN not in h.ledger_codes()
        finally:
            h.app.detach_ear()

    def test_status_carries_both_target_verifications(self, harness: Any) -> None:
        """t7 round 7: the dashboard and t21 assert these BEFORE the first turn.

        A playback stream on the wrong sink is Gwen talking to the monitor; a
        capture stream on the wrong source is Gwen listening to it. Both are
        the endpoint's own verdict, surfaced in a block this module owns.
        """

        class Verifying(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {
                    **super().status(),
                    "playback_target_verified": True,
                    "capture_target_verified": True,
                }

        h = harness()
        h.app.attach_ear("host", Verifying())
        ear = h.app.status()["ear"]
        assert ear["playback_target_verified"] is True
        assert ear["capture_target_verified"] is True

    def test_a_mis_linked_stream_reads_false_not_missing(self, harness: Any) -> None:
        class MisLinked(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {
                    **super().status(),
                    "playback_target_verified": False,
                    "capture_target_verified": True,
                }

        h = harness()
        h.app.attach_ear("host", MisLinked())
        ear = h.app.status()["ear"]
        assert ear["playback_target_verified"] is False
        assert ear["capture_target_verified"] is True

    def test_a_checked_target_reads_true_and_an_unchecked_one_false(self, harness: Any) -> None:
        """``*_target`` answers "was it checked", never the node name."""

        class Verifying(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {
                    **super().status(),
                    "playback_target_verified": True,
                    "capture_target_verified": False,
                }

        h = harness()
        h.app.attach_ear("host", Verifying())
        ear = h.app.status()["ear"]
        assert ear["playback_target"] is True
        assert ear["capture_target"] is True
        assert ear["playback_target_verified"] is True
        assert ear["capture_target_verified"] is False

        h.app.detach_ear()
        h.app.attach_ear("browser", FakeEndpoint())
        ear = h.app.status()["ear"]
        assert ear["playback_target"] is False
        assert ear["capture_target"] is False

    def test_no_node_name_ever_reaches_the_status_block(self, harness: Any) -> None:
        """A target is a boolean here; the name stays inside the endpoint."""

        class Named(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {
                    "playback_target_verified": True,
                    "capture_target_verified": True,
                    "pw_sink_node_name": "alsa_output.usb-Seeed_MARKERNODE",
                }

        h = harness()
        h.app.attach_ear("host", Named())
        ear = dict(h.app.status()["ear"])
        ear.pop("endpoint", None)  # the endpoint's own blob is its own business
        assert "MARKERNODE" not in json.dumps(ear, ensure_ascii=False)
        assert ear["playback_target"] is True

    def test_status_carries_the_playback_conditions(self, harness: Any) -> None:
        """t7 round 8: a reply nobody can hear is a fact about the sink."""

        class Quiet(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {
                    **super().status(),
                    "playback_volume": 0.35,
                    "playback_muted_by_system": False,
                    "playback_latency_ms": 40,
                }

        h = harness()
        h.app.attach_ear("host", Quiet())
        ear = h.app.status()["ear"]
        assert ear["playback_volume"] == 0.35
        assert ear["playback_muted_by_system"] is False
        assert ear["playback_latency_ms"] == 40

    def test_an_endpoint_that_cannot_say_reports_none_not_fine(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("browser", FakeEndpoint())
        ear = h.app.status()["ear"]
        assert ear["playback_volume"] is None
        assert ear["playback_muted_by_system"] is None
        assert ear["playback_latency_ms"] is None

    def test_a_quiet_sink_is_folded_into_the_degradations(self, harness: Any) -> None:
        """Round 8 reports it as a counter, not on a degradation slot."""

        class QuietSink(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {**super().status(), "playback_quiet_count": 2}

        h = harness()
        h.clear()
        h.app.attach_ear("host", QuietSink())

        assert app_module.ENDPOINT_PLAYBACK_QUIET in h.ledger_codes()
        published = [e.data["code"] for e in h.events("degradation")]
        assert app_module.ENDPOINT_PLAYBACK_QUIET in published
        assert h.app.status()["degradations"][app_module.ENDPOINT_PLAYBACK_QUIET] == 1

    def test_a_sink_at_full_volume_folds_nothing(self, harness: Any) -> None:
        class Loud(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {**super().status(), "playback_quiet_count": 0}

        h = harness()
        h.app.attach_ear("host", Loud())
        assert app_module.ENDPOINT_PLAYBACK_QUIET not in h.ledger_codes()

    def test_the_quiet_code_matches_the_endpoints_own(self) -> None:
        """The literal is written out here because the import graph forbids the
        import; this is the test that stops the two drifting apart."""
        from embodiment.audio.host import DEGRADED_PLAYBACK_QUIET

        assert app_module.ENDPOINT_PLAYBACK_QUIET == DEGRADED_PLAYBACK_QUIET

    def test_status_carries_the_ambiguous_counts(self, harness: Any) -> None:
        """t7 round 10: a name match refused because the dump had not settled."""

        class Ambiguous(FakeEndpoint):
            def status(self) -> dict[str, object]:
                return {
                    **super().status(),
                    "playback_target_verified": True,
                    "capture_target_verified": True,
                    "playback_target_ambiguous_count": 2,
                    "capture_target_ambiguous_count": 0,
                }

        h = harness()
        h.app.attach_ear("host", Ambiguous())
        ear = h.app.status()["ear"]
        assert ear["playback_target_ambiguous_count"] == 2
        assert ear["capture_target_ambiguous_count"] == 0
        # A count beside a True verdict still matters: the answer took more
        # than one look, and that is worth seeing rather than smoothing over.
        assert ear["playback_target_verified"] is True

    def test_an_endpoint_that_cannot_count_ambiguity_reads_none(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("browser", FakeEndpoint())
        ear = h.app.status()["ear"]
        assert ear["playback_target_ambiguous_count"] is None
        assert ear["capture_target_ambiguous_count"] is None

    def test_an_endpoint_that_does_not_verify_reads_none_not_false(self, harness: Any) -> None:
        """``None`` is "not applicable", which is not the same claim as "wrong"."""
        h = harness()
        h.app.attach_ear("browser", FakeEndpoint())
        ear = h.app.status()["ear"]
        assert ear["playback_target_verified"] is None
        assert ear["capture_target_verified"] is None

    def test_an_endpoint_that_cannot_say_is_recorded_not_assumed(self, harness: Any) -> None:
        class Mute(FakeEndpoint):
            @property
            def sample_rate(self) -> int:  # type: ignore[override]
                raise RuntimeError("no idea")

        h = harness()
        h.app.attach_ear("odd", Mute())
        assert app_module.APP_EAR_RATE_UNKNOWN in h.ledger_codes()
        assert h.declared_rates == [wire.INPUT_SAMPLE_RATE]

    def test_a_nonsense_rate_is_recorded_not_declared(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("odd", FakeEndpoint(sample_rate=0))
        assert app_module.APP_EAR_RATE_UNKNOWN in h.ledger_codes()
        assert h.declared_rates == [wire.INPUT_SAMPLE_RATE]

    def test_app_py_never_writes_a_sample_rate_down(self) -> None:
        """Structural: every rate comes from the ear, so none is a literal here.

        Narrow on purpose — a blunt search for ``16000`` also finds
        ``max_tokens=16000``, which has nothing to do with audio. This checks
        the lines that actually talk about a rate.
        """
        source = Path(app_module.__file__).read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if not line.lstrip().startswith("#")
            and ("sample_rate" in line or "input_sample_rate" in line)
            and re.search(r"\b\d{4,6}\b", line)
        ]
        assert not offenders, offenders

    def test_the_guard_would_catch_a_written_down_rate(self) -> None:
        """A test of the test: the pattern above really does reject a literal."""
        planted = "    config = RealtimeConfig(input_sample_rate=24000)"
        assert re.search(r"\b\d{4,6}\b", planted)
        assert "sample_rate" in planted


class TestTheMemoryLane:
    """The explicit-ask path, end to end, through the REAL session and store.

    Live, the operator asked Gwen to remember something and she said she
    would. The first run stored nothing and the second stored one record, and
    nothing in ``status()`` could show which had happened. These drive the
    real :class:`~embodiment.session.Session`, the real
    :class:`~embodiment.memory.RoomMemory` and the real files store.
    """

    def _real_memory_harness(self, harness: Any, tmp_path: Path, **over: Any) -> Any:
        """A harness whose memory is a real RoomMemory on a real private store."""
        data_dir = tmp_path / f"store{len(list(tmp_path.glob('store*')))}"
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        return harness(memory=memory, **over), data_dir

    def test_a_spoken_ask_lands_a_record_in_the_private_store(
        self, harness: Any, tmp_path: Path
    ) -> None:
        h, data_dir = self._real_memory_harness(harness, tmp_path)
        h.app.attach_ear("host", FakeEndpoint())

        assert h.app.submit_transcript("תזכרי שהחלב נגמר") is True
        h.app.run_turn("תזכרי שהחלב נגמר")

        files = [p for p in data_dir.rglob("*") if p.is_file()]
        assert files, f"nothing was written under {data_dir}"
        record = files[0]
        assert record.stat().st_size > 0
        assert stat.S_IMODE(record.stat().st_mode) == 0o600, oct(record.stat().st_mode)
        assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700

        memory_status = h.app.status()["memory"]
        assert memory_status["asks_detected"] == 1
        assert memory_status["remembered"] == 1
        assert memory_status["remember_failed"] == 0

    @pytest.mark.parametrize(
        "phrase",
        [
            "תזכרי שהחלב נגמר",
            "גוון, תזכרי שהחלב נגמר",
            "תזכרי, שהחלב נגמר",
        ],
    )
    def test_every_phrasing_the_operator_used_is_remembered(
        self, harness: Any, tmp_path: Path, phrase: str
    ) -> None:
        h, data_dir = self._real_memory_harness(harness, tmp_path)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(phrase)
        assert [p for p in data_dir.rglob("*") if p.is_file()], phrase
        assert h.app.status()["memory"]["remembered"] == 1, phrase

    def test_an_ordinary_utterance_writes_nothing_and_counts_nothing(
        self, harness: Any, tmp_path: Path
    ) -> None:
        h, data_dir = self._real_memory_harness(harness, tmp_path)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn("מה שלומך היום")
        memory_status = h.app.status()["memory"]
        assert memory_status["asks_detected"] == 0
        assert memory_status["ask_not_detected"] == 0, "an ordinary sentence is not a miss"

    def test_an_ask_the_detector_misses_is_visible(self, harness: Any) -> None:
        """Item 4: a spoken ask that matches no pattern must never be silent."""
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.clear()
        h.app.run_turn("אני רוצה שתזכרי את מה שאמרתי")

        assert h.app.status()["memory"]["ask_not_detected"] == 1
        states = [e for e in h.events("state") if e.data.get("status") == "ask-not-detected"]
        assert len(states) == 1
        assert states[0].data["component"] == "memory"
        assert h.app.status()["memory"]["asks_detected"] == 0

    def test_an_ask_during_a_barge_in_still_reaches_the_store(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """Item 4's other candidate: was the ask lost because the turn was cut?

        It is not: the ask is written by ``add_user`` at INTAKE, before senses
        is ever called, so superseding the reply cannot lose the record. Gwen
        does not get to say "I'll remember" and then not remember because she
        was interrupted.
        """
        arrived = threading.Event()
        released = threading.Event()

        def slow_senses(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            arrived.set()
            released.wait(timeout=10)
            return ModelResponse(content=REPLY)

        h, data_dir = self._real_memory_harness(harness, tmp_path, complete=slow_senses)
        h.app.attach_ear("host", FakeEndpoint())

        thread = threading.Thread(target=lambda: h.app.run_turn("תזכרי שהחלב נגמר"), daemon=True)
        thread.start()
        assert arrived.wait(timeout=10)
        h.app._barge_in()
        released.set()
        thread.join(timeout=10)

        assert [p for p in data_dir.rglob("*") if p.is_file()], "the ask was lost to a barge-in"
        status = h.app.status()
        assert status["memory"]["remembered"] == 1
        assert status["turns"]["superseded"] == 1

    def test_a_session_writes_a_transcript_log(self, harness: Any, tmp_path: Path) -> None:
        """Item 3: the per-session log the live runs never created."""
        h, _data_dir = self._real_memory_harness(harness, tmp_path)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)

        sessions_dir = Path(h.state.dir) / "sessions"
        assert sessions_dir.is_dir(), "no per-session transcript directory"
        assert stat.S_IMODE(sessions_dir.stat().st_mode) == 0o700
        logs = [p for p in sessions_dir.iterdir() if p.is_file()]
        assert logs, "no transcript log was written"
        assert stat.S_IMODE(logs[0].stat().st_mode) == 0o600
        assert logs[0].stat().st_size > 0

        transcript_status = h.app.status()["session"]["transcript"]
        assert transcript_status is not None
        assert transcript_status["persistent"] is True
        assert transcript_status["max_bytes"] > 0

    def test_the_summary_is_attempted_and_written_at_close(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """Item 2: no summary record survived either live run."""
        summaries: list[list[dict[str, Any]]] = []

        def summarise(messages: list[dict[str, Any]]) -> str:
            summaries.append(messages)
            return "דיברו על החלב."

        h, data_dir = self._real_memory_harness(harness, tmp_path, summarise=summarise)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        before = len([p for p in data_dir.rglob("*") if p.is_file()])

        h.app.close(deadline=4.0)

        assert summaries, "the summariser was never called"
        memory_status = h.app.status()["memory"]
        assert memory_status["summary_attempted"] == 1
        assert memory_status["summary_written"] == 1
        assert memory_status["summary_skip_reason"] is None
        after = len([p for p in data_dir.rglob("*") if p.is_file()])
        assert after >= before

    def test_a_missing_summariser_is_NAMED_not_merely_absent(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """What the live daemon did, and why nothing could see it."""
        h, _data_dir = self._real_memory_harness(harness, tmp_path)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        h.app.close(deadline=4.0)

        memory_status = h.app.status()["memory"]
        assert memory_status["summary_attempted"] == 1
        assert memory_status["summary_written"] == 0
        assert memory_status["summary_skip_reason"], "the absence was not named"

    def test_a_summariser_that_hangs_does_not_outlast_the_close(
        self, harness: Any, tmp_path: Path
    ) -> None:
        def hanging(messages: list[dict[str, Any]]) -> str:
            time.sleep(30)
            return "never"

        h, _data_dir = self._real_memory_harness(harness, tmp_path, summarise=hanging)
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)

        started = time.monotonic()
        h.app.close(deadline=2.0)
        assert time.monotonic() - started < 8.0
        memory_status = h.app.status()["memory"]
        assert memory_status["summary_written"] == 0
        assert memory_status["summary_skip_reason"]

    def test_main_wires_a_summariser_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The live gap: Session.close writes nothing unless it is GIVEN one."""
        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))
        application = app_module.main()
        try:
            assert application._summarise is not None
            assert callable(application._summarise)
        finally:
            application.close(deadline=2.0)


class TestRecallReachesThePrompt:
    """Round 7: Gwen was told where a key was, and could not say after a restart.

    ``status()["recall"]`` said ``mode: lexical, calls: 1`` and no
    degradation — which is exactly what a recall that found nothing because
    there was nothing to find would say. These drive the real store and
    assert on what actually reached the prompt.
    """

    ASK = "המפתח נמצא במגירה הכחולה"
    QUESTION = "איפה נמצא המפתח?"
    SUMMARIES = (
        "המשתמש פנה בברכת שלום וסיפק מידע לגבי מיקום של מפתח במגירה כחולה.",
        "המשתמש ציין את מיקום המפתח, והצד השני ביקש פירוט מדויק יותר.",
    )

    def _stocked_store(self, tmp_path: Path) -> Path:
        """The shape of the live store: one ask record and two summaries."""
        data_dir = tmp_path / "store"
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        memory.remember(self.ASK, visibility="private", record_type="explicit-ask", deadline=5.0)
        for summary in self.SUMMARIES:
            memory.remember(
                summary, visibility="private", record_type="session-summary", deadline=5.0
            )
        memory.close(deadline=2.0)  # the restart between writing and asking
        return data_dir

    def test_the_prompt_carries_the_record_after_a_restart(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """The live miss, reproduced end to end: ask, restart, then ask about it."""
        data_dir = self._stocked_store(tmp_path)
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        complete = make_complete()
        h = harness(memory=memory, complete=complete)
        h.app.attach_ear("host", FakeEndpoint())
        h.clear()

        h.app.run_turn(self.QUESTION)

        prompts = [m[0]["content"] for m in complete.seen if m and m[0]["role"] == "system"]
        assert prompts, "no system prompt reached the model"
        assert "מגירה" in prompts[-1], "the record never reached the prompt"
        assert "כחול" in prompts[-1]

        recall = h.app.status()["recall"]
        assert recall["last_hits"] >= 1
        assert recall["rendered_total"] >= 1
        assert recall["hits_total"] >= 1
        assert recall["empty_total"] == 0

    def test_the_recall_counters_can_show_a_zero(self, harness: Any, tmp_path: Path) -> None:
        """The counters have to be able to FAIL, or they prove nothing."""
        data_dir = tmp_path / "empty-store"
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        h = harness(memory=memory)
        h.app.attach_ear("host", FakeEndpoint())
        h.clear()

        h.app.run_turn(self.QUESTION)

        recall = h.app.status()["recall"]
        assert recall["last_hits"] == 0
        assert recall["rendered_total"] == 0
        assert recall["empty_total"] == 1
        states = [e for e in h.events("state") if e.data.get("component") == "recall"]
        assert len(states) == 1 and states[0].data["status"] == "empty"

    def test_every_turn_publishes_its_recall_counts(self, harness: Any, tmp_path: Path) -> None:
        data_dir = self._stocked_store(tmp_path)
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        h = harness(memory=memory)
        h.app.attach_ear("host", FakeEndpoint())
        h.clear()

        h.app.run_turn(self.QUESTION)

        states = [e for e in h.events("state") if e.data.get("component") == "recall"]
        assert len(states) == 1
        data = states[0].data
        assert data["status"] == "searched"
        assert data["last_hits"] >= 1
        assert data["rendered"] >= 1
        blob = json.dumps(data, ensure_ascii=False)
        assert "מגירה" not in blob, "a recall event carried text"
        assert "מפתח" not in blob

    def test_the_blind_index_is_named_never_silent(self, harness: Any, tmp_path: Path) -> None:
        """The cause: a keyword search whose tokeniser cannot see the script."""
        data_dir = self._stocked_store(tmp_path)
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        h = harness(memory=memory)
        h.app.attach_ear("host", FakeEndpoint())

        h.app.run_turn(self.QUESTION)

        assert app_module.APP_RECALL_LEXICAL_BLIND in h.ledger_codes()
        assert h.app.status()["recall"]["lexical_fallback_hits"] == 1

    def test_the_upstream_tokeniser_is_why(self) -> None:
        """Pinned against eidetic itself, so this diagnosis cannot rot quietly.

        If a later eidetic tokenises Hebrew, this fails and the workaround —
        and its degradation record — can be removed.
        """
        from eidetic.memory.scoring import _kw_tokenize

        assert _kw_tokenize("the key is in the blue drawer")
        assert _kw_tokenize("המפתח נמצא במגירה הכחולה") == []

    def test_an_ascii_question_needs_no_fallback(self, harness: Any, tmp_path: Path) -> None:
        data_dir = tmp_path / "latin-store"
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        memory.remember("the key is in the blue drawer", visibility="private", deadline=5.0)
        h = harness(memory=memory)
        h.app.attach_ear("host", FakeEndpoint())

        h.app.run_turn("where is the key?")

        assert app_module.APP_RECALL_LEXICAL_BLIND not in h.ledger_codes()
        assert h.app.status()["recall"]["last_hits"] >= 1

    def test_a_recall_deadline_is_counted_as_its_own_thing(
        self, harness: Any, tmp_path: Path
    ) -> None:
        def slow_recall(query: str, **kwargs: Any) -> Any:
            time.sleep(1.0)
            return SimpleNamespace(ok=True, records=[], degradation=None)

        h = harness(
            recall_fn=slow_recall,
            config=AppConfig(recall_deadline=0.05, poll_interval_s=0.01),
        )
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)

        recall = h.app.status()["recall"]
        assert recall["deadline_exceeded"] >= 1
        assert recall["errors"] == 0

    def test_a_memory_that_raises_is_counted_as_an_error(self, harness: Any) -> None:
        class Exploding:
            store_permission_failures = 0
            store_symlinks_skipped = 0
            store_non_files_skipped = 0
            store_root_is_symlink = False
            pending = 0
            abandoned_dropped = 0
            scope = "gwen"
            data_dir = "/dev/null"
            last_recall_mode = None

            def recall(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("no store")

            def remember(self, *args: Any, **kwargs: Any) -> Any:
                return SimpleNamespace(ok=False, record_id=None, degradation=None)

            def close(self, deadline: float = 1.0) -> Any:
                return SimpleNamespace(degradations=(), unconfirmed=())

        h = harness(memory=Exploding())
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)

        recall = h.app.status()["recall"]
        assert recall["errors"] >= 1
        assert app_module.APP_RECALL_FAILED in h.ledger_codes()


class TestSupersededTurns:
    """Live: barge in, she starts thinking, barge in again — and she talks over you.

    The second ``speech_started`` had nothing to stop, because nothing was
    playing yet; the reply then arrived and played over the operator.
    """

    def test_a_barge_in_while_thinking_drops_the_reply(self, harness: Any) -> None:
        released = threading.Event()
        arrived = threading.Event()

        def slow_senses(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            arrived.set()
            released.wait(timeout=10)
            return ModelResponse(content=REPLY)

        endpoint = FakeEndpoint()
        h = harness(complete=slow_senses, endpoints=lambda: endpoint)
        h.app.start()
        played_before = len(endpoint.played)
        h.clear()

        thread = threading.Thread(target=lambda: h.app.run_turn(SPEECH), daemon=True)
        thread.start()
        assert arrived.wait(timeout=10), "the turn never reached senses"

        h.app._barge_in()  # the operator starts speaking again, mid-thought
        released.set()
        thread.join(timeout=10)

        assert len(endpoint.played) == played_before, "the superseded reply was spoken"
        status = h.app.status()
        assert status["turns"]["superseded"] == 1
        assert status["turns"]["completed"] == 1
        replies = h.events("reply")
        assert [e.data["text"] for e in replies] == [REPLY]
        assert replies[0].data["superseded"] is True
        states = [e for e in h.events("state") if e.data.get("status") == "superseded"]
        assert len(states) == 1 and states[0].data["component"] == "turn"

    def test_a_turn_nobody_interrupted_is_spoken_normally(self, harness: Any) -> None:
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.start()
        played_before = len(endpoint.played)
        h.clear()

        h.app.run_turn(SPEECH)

        assert len(endpoint.played) > played_before
        assert h.app.status()["turns"]["superseded"] == 0
        replies = h.events("reply")
        assert replies and replies[0].data.get("superseded") is not True

    def test_speech_started_between_submit_and_the_reply_wins(self, harness: Any) -> None:
        """The race, pinned: whoever wins, no reply may START after it was seen."""
        endpoint = FakeEndpoint()
        seen = threading.Event()

        def racing_senses(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            # speech_started lands in the same breath as the reply
            h.app._on_event(wire.SpeechStarted(item_id="i2"))
            seen.set()
            return ModelResponse(content=REPLY)

        h = harness(complete=racing_senses, endpoints=lambda: endpoint)
        h.app.start()
        played_before = len(endpoint.played)

        h.app.run_turn(SPEECH)

        assert seen.is_set()
        assert len(endpoint.played) == played_before, "a reply started after speech_started"
        assert h.app.status()["turns"]["superseded"] == 1

    def test_the_next_turn_is_not_superseded_by_the_last_barge_in(self, harness: Any) -> None:
        """The mark is per turn, so one barge-in cannot mute the conversation."""
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.start()
        h.app._barge_in()
        h.app.run_turn(SPEECH)
        played_after_first = len(endpoint.played)
        h.app.run_turn(SPEECH)

        assert len(endpoint.played) > played_after_first
        assert h.app.status()["turns"]["superseded"] == 0

    def test_a_barge_in_with_no_turn_in_flight_supersedes_nothing(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        h.app._barge_in()
        h.app._barge_in()
        assert h.app.status()["turns"]["superseded"] == 0


class TestTheGatewayIsNotTrustedWithItsOwnKey:
    """The wave review's one finding: a reply that carries the key back.

    A model reply is persisted (the 0600 transcript, the session record in the
    private store), published (the ``reply`` event every dashboard viewer
    receives) and spoken. A gateway that echoes its bearer token inside
    ``message.content`` — or an error page that quotes it — would put it in
    all three. The realtime client already guards this on its own wire; the
    HTTP seam did not.
    """

    KEY = "GATEWAYKEY-abcdef0123456789"  # nosec B105 - a planted marker
    SECRET = "INSTALLSECRET-9876543210"  # nosec B105 - a planted marker

    def _harness_with_a_leaky_gateway(
        self, harness: Any, tmp_path: Path, reply: str, **over: Any
    ) -> Any:
        memory = RoomMemory(
            tmp_path / "store", scope="gwen", added_by="gwen", embed_probe=lambda: False
        )

        def leaky(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            return ModelResponse(content=reply)

        return harness(
            memory=memory,
            complete=leaky,
            config=AppConfig(poll_interval_s=0.01, api_key=self.KEY),
            redact=(self.SECRET,),
            **over,
        )

    def test_a_key_in_a_reply_reaches_nothing_that_keeps_or_speaks_it(
        self, harness: Any, tmp_path: Path
    ) -> None:
        spoken: list[str] = []

        def recording_synth(sentence: str, config: Any) -> bytes:
            # The real Voice, so the reply event is published the way it is
            # in production — and this is every sentence that reached the
            # synthesiser, which is the closest a test gets to "what she said".
            spoken.append(sentence)
            return b"\x00\x00"

        h = self._harness_with_a_leaky_gateway(
            harness,
            tmp_path,
            f"שלום, המפתח שלך הוא {self.KEY} בבקשה",
            synthesize=recording_synth,
        )
        h.app.attach_ear("host", FakeEndpoint())
        h.clear()

        result = h.app.run_turn(SPEECH)

        # 1. the value the caller gets
        assert self.KEY not in result.spoken
        assert app_module.REPLY_REDACTED in result.spoken
        # 2. what was spoken
        assert spoken and all(self.KEY not in text for text in spoken)
        # 3. the reply event on the bus
        replies = h.events("reply")
        assert replies and all(self.KEY not in e.data["text"] for e in replies)
        # 4. the transcript file on disk
        sessions = Path(h.state.dir) / "sessions"
        logs = [p for p in sessions.iterdir() if p.is_file()]
        assert logs
        for log in logs:
            assert self.KEY not in log.read_text(encoding="utf-8")
        # 5. the counter and the record
        assert h.app.status()["replies_scrubbed"] == 1
        assert app_module.APP_REPLY_SECRET_SCRUBBED in h.ledger_codes()
        # 6. and nothing anywhere else in the status
        assert self.KEY not in json.dumps(h.app.status(), ensure_ascii=False)

    def test_the_install_secret_is_scrubbed_too(self, harness: Any, tmp_path: Path) -> None:
        h = self._harness_with_a_leaky_gateway(harness, tmp_path, f"הנה הסוד {self.SECRET} שלך")
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert self.SECRET not in result.spoken
        assert h.app.status()["replies_scrubbed"] == 1

    def test_a_summary_that_carries_the_key_never_reaches_the_store(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """Session.close writes what the summariser returns straight to disk."""
        data_dir = tmp_path / "store"
        memory = RoomMemory(data_dir, scope="gwen", added_by="gwen", embed_probe=lambda: False)
        h = harness(
            memory=memory,
            config=AppConfig(poll_interval_s=0.01, api_key=self.KEY),
            redact=(self.SECRET,),
            summarise=lambda messages: f"דיברו על המפתח {self.KEY} ועל {self.SECRET}",
        )
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)

        h.app.close(deadline=4.0)

        files = [p for p in data_dir.rglob("*") if p.is_file()]
        assert files, "no summary was written, so this proves nothing"
        for path in files:
            body = path.read_text(encoding="utf-8")
            assert self.KEY not in body
            assert self.SECRET not in body
        assert app_module.REPLY_REDACTED in "".join(p.read_text(encoding="utf-8") for p in files)
        assert h.app.status()["memory"]["summary_written"] == 1
        assert h.app.status()["replies_scrubbed"] == 1

    def test_a_json_escaped_key_is_caught_as_well_as_the_raw_one(
        self, harness: Any, tmp_path: Path
    ) -> None:
        """A key with a quote arrives escaped; a raw filter would never see it."""
        awkward = 'KEY-with"a-quote\\and-a-backslash'
        memory = RoomMemory(
            tmp_path / "store", scope="gwen", added_by="gwen", embed_probe=lambda: False
        )
        escaped = json.dumps(awkward)[1:-1]
        assert escaped != awkward, "this test needs a key JSON has to escape"

        def leaky(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            return ModelResponse(content=f"echoing {escaped} back")

        h = harness(
            memory=memory,
            complete=leaky,
            config=AppConfig(poll_interval_s=0.01, api_key=awkward),
        )
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert escaped not in result.spoken
        assert h.app.status()["replies_scrubbed"] == 1

    def test_an_honest_reply_is_left_exactly_alone(self, harness: Any, tmp_path: Path) -> None:
        """The counter has to be able to stay at zero."""
        h = self._harness_with_a_leaky_gateway(harness, tmp_path, REPLY)
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn(SPEECH)
        assert result.spoken == REPLY
        assert h.app.status()["replies_scrubbed"] == 0
        assert app_module.APP_REPLY_SECRET_SCRUBBED not in h.ledger_codes()

    def test_a_daemon_with_no_secrets_scrubs_nothing(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert h.app.status()["replies_scrubbed"] == 0

    def test_the_marker_matches_the_realtime_clients_own(self) -> None:
        """Two surfaces, one spelling — pinned rather than trusted."""
        from embodiment.realtime.client import REDACTED

        assert app_module.REPLY_REDACTED == REDACTED

    def test_main_hands_the_app_both_secrets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("EMBODIMENT_GATEWAY_KEY", self.KEY)
        application = app_module.main()
        try:
            forms = application._secret_forms
            assert self.KEY in forms
            # the install secret is minted by the guard at first start
            assert len(forms) >= 2, forms
        finally:
            application.close(deadline=2.0)


class TestReviewQuestions:
    """Answers to the review's mandatory questions, as tests where one was owed."""

    # Q5 — what submit_transcript/run_turn do with input nobody intended.

    @pytest.mark.parametrize(
        "payload",
        [None, 17, 3.5, b"pcm bytes", bytearray(b"x"), {"a": 1}, ["a"], object()],
    )
    def test_q5_a_non_string_transcript_is_named_at_both_entry_points(
        self, harness: Any, payload: Any
    ) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.submit_transcript(payload) is False
        assert h.app.run_turn(payload).spoken == ""
        assert app_module.APP_TRANSCRIPT_NOT_TEXT in h.ledger_codes()
        assert h.app.status()["transcripts"]["not_text"] == 1

    def test_q5_a_one_megabyte_transcript_is_survived(self, harness: Any) -> None:
        """A commit that is a megabyte of speech, through the whole turn path."""
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        huge = "א" * 1_000_000

        assert h.app.submit_transcript(huge) is True
        result = h.app.run_turn(huge)

        assert result.spoken == REPLY
        assert h.app.status()["turns"]["failed"] == 0
        json.dumps(h.app.status(), ensure_ascii=False)

    def test_q5_a_lone_surrogate_never_escapes_as_an_exception(self, harness: Any) -> None:
        """Text that cannot be encoded at all: the turn still answers.

        A lone surrogate survives inside a ``str`` but raises on any UTF-8
        encode, which is what a bus publish, a transcript write and a JSON
        status all do — so it reaches more code than an ordinary hostile
        string does.
        """
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        lone = "שלום\ud800עולם"

        assert isinstance(h.app.run_turn(lone).spoken, str)
        assert h.app.status()["turns"]["failed"] == 0
        # status() must stay readable even after one went through.
        assert isinstance(h.app.status(), dict)
        assert h.app.run_turn(SPEECH).spoken == REPLY, "the next turn was lost"

    def test_q5_a_turn_seam_that_raises_is_recorded_and_survived(self, harness: Any) -> None:
        def exploding(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            raise RuntimeError("senses fell over")

        h = harness(complete=exploding)
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.run_turn(SPEECH).spoken  # turn.py's fallback text
        assert h.app.status()["turns"]["completed"] == 1
        assert h.app.run_turn(SPEECH).spoken

    def test_q5_a_voice_that_raises_costs_the_speech_not_the_turn(self, harness: Any) -> None:
        class Hostile:
            feature_frames: list[dict[str, object]] = []
            degradations: list[Any] = []

            @property
            def speaking(self) -> bool:
                return False

            def speak(self, text: str) -> Any:
                raise RuntimeError("no voice")

            def set_endpoint(self, endpoint: Any) -> None:
                return None

            def drain_features(self, max_n: int = 256) -> list[dict[str, object]]:
                return []

            def close(self, deadline: float = 2.0) -> Any:
                return SimpleNamespace(to_dict=lambda: {})

            def status(self) -> dict[str, object]:
                return {}

        h = harness()
        h.app._voice_factory = lambda endpoint: Hostile()
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert app_module.APP_TURN_FAILED in h.ledger_codes()
        assert h.app.status()["turns"]["completed"] == 1

    # Q6 — mute is the endpoint's to enforce, and it must travel with the ear.

    def test_q6_mute_is_carried_across_a_handover(self, harness: Any) -> None:
        """Without this a handover silently un-mutes the microphone."""
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="host")
        h.app.attach_ear("host", first)
        h.app.set_mute(True)
        assert first.muted is True

        second = FakeEndpoint(name="browser")
        h.app.attach_ear("browser", second)

        assert second.muted is True, "the new ear came up hot after a mute"
        assert h.app.status()["ear"]["muted"] is True
        assert h.app.status()["ear"]["mute_intent"] is True
        mics = h.events("mic")
        assert mics[-1].data["hot"] is False

    def test_q6_an_unmuted_daemon_still_starts_hot(self, harness: Any) -> None:
        """Hot mic on start is the INITIAL state and stays that way."""
        h = harness()
        endpoint = FakeEndpoint()
        h.app.attach_ear("host", endpoint)
        assert endpoint.muted is False
        assert h.app.status()["ear"]["mute_intent"] is False
        assert h.events("mic")[-1].data["hot"] is True

    def test_q6_a_mute_with_no_ear_is_honoured_by_the_next_one(self, harness: Any) -> None:
        h = harness()
        h.app.set_mute(True)
        endpoint = FakeEndpoint()
        h.app.attach_ear("host", endpoint)
        assert endpoint.muted is True

    def test_q6_unmuting_travels_too(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("host", FakeEndpoint())
        h.app.set_mute(True)
        h.app.set_mute(False)
        second = FakeEndpoint(name="browser")
        h.app.attach_ear("browser", second)
        assert second.muted is False

    # Q2 — an endpoint that blocks on the way out.

    def test_q2_a_hanging_stop_capture_does_not_outlast_close(self, harness: Any) -> None:
        """Through close() the teardown is bounded; the report says what stuck."""

        class Hanging(FakeEndpoint):
            def stop_capture(self) -> None:
                time.sleep(30)

        h = harness()
        h.app.attach_ear("host", Hanging())
        started = time.monotonic()
        report = h.app.close(deadline=2.0)
        assert time.monotonic() - started < 8.0
        assert report.endpoint_closed is False
        assert "endpoint" in report.unfinished

    def test_q2_a_raising_teardown_is_recorded_not_raised(self, harness: Any) -> None:
        class Raising(FakeEndpoint):
            def stop_capture(self) -> None:
                raise RuntimeError("no")

            def detach(self) -> None:
                raise RuntimeError("no")

            def close(self, deadline: float) -> Any:
                raise RuntimeError("no")

        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("host", Raising())
        handover = h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        assert handover.attached is True, "a raising teardown blocked the handover"
        assert app_module.APP_EAR_DETACH_FAILED in h.ledger_codes()

    # Round 10 — the two unbounded waits Q2 and Q3 named.

    def test_a_hanging_teardown_does_not_hold_up_a_handover(self, harness: Any) -> None:
        """Q2's remainder, closed: the operator asked for a different ear."""

        class Hanging(FakeEndpoint):
            def stop_capture(self) -> None:
                time.sleep(30)

        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        hung = Hanging(name="host")
        h.app.attach_ear("host", hung)
        second = FakeEndpoint(name="browser")

        started = time.monotonic()
        handover = h.app.attach_ear("browser", second)
        elapsed = time.monotonic() - started

        assert handover.attached is True
        assert elapsed < app_module.TEARDOWN_DEADLINE_S + 2.0, elapsed
        assert app_module.APP_EAR_TEARDOWN_TIMEOUT in h.ledger_codes()
        ear = h.app.status()["ear"]
        assert ear["active"] == "browser"
        assert ear["teardown_timeouts"] == 1
        assert ear["unreaped_endpoints"] == 1
        assert second.capturing is True, "the new ear never started"

    def test_an_unreaped_endpoint_is_retried_at_close(self, harness: Any) -> None:
        """Kept, not forgotten — the shape t7 uses for a child it could not reap."""

        class SlowOnce(FakeEndpoint):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                self.closes = 0

            def stop_capture(self) -> None:
                time.sleep(30)

            def close(self, deadline: float) -> Any:
                self.closes += 1
                return super().close(deadline)

        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        hung = SlowOnce(name="host")
        h.app.attach_ear("host", hung)
        h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        assert h.app.status()["ear"]["unreaped_endpoints"] == 1

        h.app.close(deadline=4.0)

        assert hung.closes >= 1, "the unreaped endpoint was never retried"
        assert h.app.status()["ear"]["unreaped_endpoints"] == 0

    def test_a_teardown_that_answers_is_not_recorded_as_a_timeout(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        h.app.attach_ear("host", FakeEndpoint(name="host"))
        h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        ear = h.app.status()["ear"]
        assert ear["teardown_timeouts"] == 0
        assert ear["unreaped_endpoints"] == 0
        assert app_module.APP_EAR_TEARDOWN_TIMEOUT not in h.ledger_codes()

    def test_a_hanging_speaker_does_not_park_the_ears_thread(self, harness: Any) -> None:
        """Q3 closed: the bound is the voice's own, and the ear keeps listening."""

        class HangingVoice:
            feature_frames: list[dict[str, object]] = []
            degradations: list[Any] = []
            speaking = True

            def on_speech_started(self, event: object = None) -> int:
                time.sleep(30)
                return 0

            def speak(self, text: str) -> Any:
                return SimpleNamespace(to_dict=lambda: {})

            def set_endpoint(self, endpoint: Any) -> None:
                return None

            def drain_features(self, max_n: int = 256) -> list[dict[str, object]]:
                return []

            def close(self, deadline: float = 2.0) -> Any:
                return SimpleNamespace(to_dict=lambda: {})

            def status(self) -> dict[str, object]:
                return {}

        h = harness()
        h.app._voice_factory = lambda endpoint: HangingVoice()
        h.app.attach_ear("host", FakeEndpoint())

        started = time.monotonic()
        h.app._on_event(wire.SpeechStarted(item_id="i1"))
        elapsed = time.monotonic() - started

        assert elapsed < app_module.BARGE_IN_STOP_BOUND_S + 1.0, elapsed
        assert app_module.APP_BARGE_IN_STOP_TIMEOUT in h.ledger_codes()
        assert h.app.status()["turns"]["barge_in_stop_timeouts"] == 1

        # and the ears thread carries on: the next event is still classified
        h.app._on_event(wire.TranscriptionCompleted(text=SPEECH, item_id="i2"))
        assert h.app.status()["transcripts"]["received"] == 1

    def test_the_barge_in_bound_comes_from_the_voices_own(self) -> None:
        """Derived, not invented — and a waiter must outlast what it waits on."""
        from embodiment.voice import BARGE_IN_BOUND_S

        assert app_module.BARGE_IN_STOP_BOUND_S > BARGE_IN_BOUND_S
        assert app_module.BARGE_IN_STOP_BOUND_S == (
            BARGE_IN_BOUND_S + app_module.BARGE_IN_STOP_MARGIN_S
        )

    def test_a_speaker_that_stops_promptly_records_nothing(self, harness: Any) -> None:
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.start()
        endpoint._playing = True
        h.app._on_event(wire.SpeechStarted(item_id="i1"))
        assert app_module.APP_BARGE_IN_STOP_TIMEOUT not in h.ledger_codes()
        assert h.app.status()["turns"]["barge_in_stop_timeouts"] == 0

    # Q8 — the recall mode is memory's answer, not a guess.

    def test_q8_the_reported_mode_is_the_one_memory_returned(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(SPEECH)
        assert h.app.status()["recall"]["mode"] == "lexical"
        assert h.app.status()["recall"]["configured_mode"] == "keyword"

    def test_q8_status_holds_up_when_memory_answers_nothing(self, harness: Any) -> None:
        """A memory with none of the counters: reported unknown, never crashed."""

        class Bare:
            def recall(self, *args: Any, **kwargs: Any) -> Any:
                return SimpleNamespace(ok=True, records=[], mode=None, degradations=())

            def remember(self, *args: Any, **kwargs: Any) -> Any:
                return SimpleNamespace(ok=False, record_id=None, degradation=None)

            def close(self, deadline: float = 1.0) -> Any:
                return SimpleNamespace(degradations=(), unconfirmed=())

        h = harness(memory=Bare())
        status = h.app.status()
        assert status["memory"]["store_permission_failures"] is None
        assert status["memory"]["store_root_is_symlink"] is None
        assert status["recall"]["mode"] is None
        json.dumps(status, ensure_ascii=False)


class TestFramesBeforeTheSession:
    """Live finding B: app-capture-failed x3 at start, right after the warm-up."""

    def test_a_frame_during_start_capture_finds_a_session(self, harness: Any) -> None:
        """The endpoint's capture thread is live the moment start_capture returns.

        Measured live on the restart: three ``app-capture-failed`` records
        carrying an ``AttributeError`` whose message is 47 characters — which
        is exactly ``'NoneType' object has no attribute 'send_audio'``. The
        ears client was built AFTER capture started, so the first frames had
        nowhere to go.
        """

        class EagerEndpoint(FakeEndpoint):
            def start_capture(self, on_frame: Any) -> None:
                super().start_capture(on_frame)
                for _ in range(3):  # a real capture thread is already running
                    on_frame(silent_pcm())

        h = harness()
        h.app.attach_ear("host", EagerEndpoint())

        assert app_module.APP_CAPTURE_FAILED not in h.ledger_codes()
        assert len(h.ears.sent) == 3, "the first frames were dropped"
        assert h.app.status()["audio"]["frames_forwarded"] == 3

    def test_a_frame_with_no_session_is_named_and_counted(self, harness: Any) -> None:
        """Belt and braces: a factory that fails leaves no session at all."""

        def refusing_factory(rate: int) -> Any:
            raise RuntimeError("no gateway client")

        h = harness(ears_factory=refusing_factory)
        endpoint = FakeEndpoint()
        h.app.attach_ear("host", endpoint)
        endpoint.on_frame(silent_pcm())

        assert app_module.APP_FRAMES_NO_SESSION in h.ledger_codes()
        assert h.app.status()["audio"]["frames_dropped_no_session"] == 1
        assert app_module.APP_CAPTURE_FAILED not in h.ledger_codes()


class TestVoiceSeams:
    """t12 round 3: one voice, re-pointed; features drained through its own API."""

    def test_a_handover_re_points_the_same_voice_instead_of_rebuilding(
        self, harness: Any, tmp_path: Path
    ) -> None:
        built: list[Voice] = []

        h = harness()
        original_factory = h.app._voice_factory

        def counting_factory(endpoint: Any) -> Voice:
            voice = original_factory(endpoint)
            built.append(voice)
            return voice

        h.app._voice_factory = counting_factory
        h.app.attach_ear("host", FakeEndpoint(name="host"))
        h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        assert len(built) == 1, "the voice was rebuilt for the second ear"
        assert h.app._voice is built[0]

    def test_a_degradation_from_after_the_handover_is_still_recorded(self, harness: Any) -> None:
        """The fold index used to point past a freshly rebuilt voice's empty list."""

        def exploding_synth(sentence: str, config: Any) -> bytes:
            raise RuntimeError("tts is down")

        h = harness(synthesize=exploding_synth)
        h.app.attach_ear("host", FakeEndpoint(name="host"))
        h.app.run_turn(SPEECH)
        assert "voice-tts-failed" in h.ledger_codes()
        h.app.attach_ear("browser", FakeEndpoint(name="browser"))
        h.clear()
        h.app.run_turn(SPEECH)
        published = [e.data["code"] for e in h.events("degradation")]
        assert "voice-tts-failed" in published, published

    def test_attaching_warms_the_playback_link_up(self, harness: Any) -> None:
        """The verdict must exist BEFORE the first reply, not because of it."""
        endpoint = FakeEndpoint()
        h = harness()
        h.app.attach_ear("host", endpoint)

        assert len(endpoint.played) == 1, "no warm-up, or more than one"
        buffer = endpoint.played[0]
        expected = int(app_module.PLAYBACK_RATE_HZ * app_module.WARMUP_SILENCE_S) * 2
        assert len(buffer) == expected
        assert set(buffer) == {0}, "the warm-up must be silence, not a click"
        ear = h.app.status()["ear"]
        assert ear["warmups"] == 1
        assert ear["warmup_failures"] == 0

    def test_the_warm_up_is_not_a_turn_and_not_a_reply(self, harness: Any) -> None:
        h = harness()
        h.clear()
        h.app.attach_ear("host", FakeEndpoint())
        assert h.events("reply") == []
        status = h.app.status()
        assert status["turns"]["completed"] == 0
        assert status["transcripts"]["received"] == 0

    def test_a_warm_up_that_fails_is_counted_never_recorded(self, harness: Any) -> None:
        """A convenience that fails is not a fault; the count is the visibility."""

        class Deaf(FakeEndpoint):
            def play(self, frames: bytes) -> None:
                raise RuntimeError("no player")

        h = harness()
        handover = h.app.attach_ear("host", Deaf())

        assert handover.attached is True, "a failed warm-up must not fail the attach"
        ear = h.app.status()["ear"]
        assert ear["warmups"] == 0
        assert ear["warmup_failures"] == 1
        assert h.events("degradation") == []
        assert h.ledger_codes() == []

    def test_each_handover_warms_the_new_ear_up(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        first = FakeEndpoint(name="a")
        second = FakeEndpoint(name="b")
        h.app.attach_ear("a", first)
        h.app.attach_ear("b", second)
        assert len(first.played) == 1
        assert len(second.played) == 1
        assert h.app.status()["ear"]["warmups"] == 2

    def test_the_pump_drains_through_the_voices_own_api(self, harness: Any) -> None:
        """A voice with NO ``feature_frames`` attribute still gets drained."""

        class DrainOnly:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def drain_features(self, max_n: int = 256) -> list[dict[str, object]]:
                self.calls.append(max_n)
                if len(self.calls) > 1:
                    return []
                return [
                    {
                        "level_db": -30.0,
                        "noise_floor_db": -96.0,
                        "zero_crossing_hz": 120.0,
                        "env": "AAAA",
                    }
                ]

        h = harness()
        h.app._voice = DrainOnly()
        h.app.pump()
        assert h.app._voice.calls == [app_module.MAX_FEATURE_DRAIN]
        out = [e for e in h.events("features") if e.data["direction"] == "out"]
        assert len(out) == 1

    def test_a_voice_whose_drain_raises_is_recorded_once(self, harness: Any) -> None:
        class Hostile:
            def drain_features(self, max_n: int = 256) -> Any:
                raise RuntimeError("no")

        h = harness()
        h.app._voice = Hostile()
        for _ in range(50):
            h.app.pump()
        assert app_module.APP_FEATURES_FAILED in h.ledger_codes()
        records = [c for c in h.ledger_codes() if c == app_module.APP_FEATURES_FAILED]
        assert len(records) == 1, "a per-pump fault flooded the ledger"

    def test_detaching_leaves_a_voice_that_still_publishes_the_reply(self, harness: Any) -> None:
        h = harness()
        endpoint = FakeEndpoint()
        h.app.attach_ear("host", endpoint)
        h.app.detach_ear()
        played_before = len(endpoint.played)  # the attach warm-up, and nothing else
        h.clear()
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert [e.data["text"] for e in h.events("reply")] == [REPLY]
        assert len(endpoint.played) == played_before, "a detached endpoint was played into"


class TestShutdown:
    def test_close_is_idempotent_and_bounded(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        started = time.monotonic()
        first = h.app.close(deadline=2.0)
        second = h.app.close(deadline=2.0)
        assert time.monotonic() - started < 6.0
        assert first.closed is True
        assert second.already_closed is True

    def test_run_returns_zero_when_the_stop_event_is_set(self, harness: Any) -> None:
        h = harness()
        stop = threading.Event()
        returned: list[Any] = []

        def runner() -> None:
            returned.append(h.app.run(stop))

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        time.sleep(0.2)
        stop.set()
        thread.join(timeout=8.0)
        assert not thread.is_alive(), "run() did not return within its bound"
        assert returned == [0]

    def test_close_reports_what_it_left_unfinished(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        report = h.app.close(deadline=2.0)
        assert isinstance(report.to_dict(), dict)
        assert report.ears_stopped is True

    def test_close_returns_within_its_deadline_when_a_seam_hangs(self, harness: Any) -> None:
        """Found by attacking: a seam that ignores its deadline unbounded close()."""

        class HangingMemory:
            store_permission_failures = 0
            store_symlinks_skipped = 0
            pending = 0
            abandoned_dropped = 0
            scope = "gwen"
            data_dir = "/dev/null"
            last_recall_mode = None

            def recall(self, *args: Any, **kwargs: Any) -> Any:
                time.sleep(30)

            def close(self, deadline: float = 1.0) -> Any:
                time.sleep(30)

        h = harness(memory=HangingMemory())
        h.app.start()
        started = time.monotonic()
        report = h.app.close(deadline=1.0)
        assert time.monotonic() - started < 5.0, "close() outran its deadline"
        assert report.memory_closed is False
        assert "memory" in report.unfinished
        assert app_module.APP_SHUTDOWN_INCOMPLETE in h.ledger_codes()

    def test_a_stuck_turn_is_visible_in_status(self, harness: Any) -> None:
        release = threading.Event()

        def blocking_complete(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
            release.wait(timeout=10)
            return ModelResponse(content=REPLY)

        h = harness(complete=blocking_complete)
        h.app.attach_ear("host", FakeEndpoint())
        thread = threading.Thread(target=lambda: h.app.run_turn(SPEECH), daemon=True)
        thread.start()
        deadline = time.monotonic() + 15.0
        while h.app.status()["turns"]["in_flight"] == 0:
            assert time.monotonic() < deadline, "a turn in flight was never visible"
            time.sleep(0.02)
        release.set()
        thread.join(timeout=5)
        assert h.app.status()["turns"]["in_flight"] == 0

    def test_a_stop_during_the_handshake_still_closes_the_ear(self, harness: Any) -> None:
        """Found by a 2-in-25 flake: close() used to skip an ear not yet serving.

        ``start()`` then ``close()`` with the handshake still in flight left
        the ears thread parked in ``events()`` with nothing ever telling it
        to stop — the ears step burned its whole slice on a join that could
        not succeed, and a real client would have left its socket open.
        """
        ears = FakeEars(connect_delay=0.3)
        h = harness(ears=ears)
        h.app.start()
        report = h.app.close(deadline=2.0)
        assert ears.closed is True, "the ear was never told to stop"
        assert report.ears_stopped is True
        assert "ears" not in report.unfinished

    def test_start_then_close_immediately_is_clean_every_time(self, harness: Any) -> None:
        """The flake's own shape, run enough times to catch it if it returns."""
        for _ in range(20):
            h = harness()
            h.app.start()
            report = h.app.close(deadline=2.0)
            assert report.ears_stopped is True
            assert report.unfinished == (), report.unfinished

    def test_the_client_is_given_the_bound_this_module_waits_on(self, harness: Any) -> None:
        """Lesson 1: the clock comes from the quantity it bounds, not the other way.

        Live, the client was handed the whole 5 s shutdown deadline while the
        wait was half the ears step's slice, so the wait timed out first and
        recorded ``close: TimeoutError`` against an ear that was closing
        perfectly well.
        """
        ears = FakeEars()
        h = harness(ears=ears)
        h.app.start()
        h.app.close(deadline=2.0)

        assert ears.close_deadlines, "the ear was never asked to close"
        given = ears.close_deadlines[0]
        assert given is not None
        assert given < 2.0, f"the client outranks its own waiter: {given}"

    def test_a_slow_close_that_finishes_is_not_a_timeout(self, harness: Any) -> None:
        """A close inside the client's own bound must not be recorded as a failure."""
        ears = FakeEars(close_delay=0.35)
        h = harness(ears=ears)
        h.app.start()
        report = h.app.close(deadline=4.0)

        assert ears.closed is True
        assert report.ears_stopped is True
        assert app_module.APP_EARS_THREAD_FAILED not in h.ledger_codes()
        assert app_module.APP_EARS_CLOSE_INCOMPLETE not in h.ledger_codes()

    def test_a_client_that_reports_its_own_close_incomplete_is_recorded(self, harness: Any) -> None:
        """The CLIENT's report is what says the handshake failed, not our clock."""
        h = harness(ears=FakeEars(graceful=False))
        h.app.start()
        h.app.close(deadline=2.0)
        assert app_module.APP_EARS_CLOSE_INCOMPLETE in h.ledger_codes()

    def test_a_close_that_really_never_finishes_is_named(self, harness: Any) -> None:
        """The record still exists for the case it was invented for."""

        class NeverCloses(FakeEars):
            async def close(self, deadline: Optional[float] = None) -> Any:
                self.close_deadlines.append(deadline)
                await asyncio.sleep(30)
                return SimpleNamespace(graceful=True)

        h = harness(ears=NeverCloses())
        h.app.start()
        started = time.monotonic()
        report = h.app.close(deadline=1.0)
        assert time.monotonic() - started < 6.0
        assert report.ears_stopped is False
        assert "ears" in report.unfinished

    def test_a_turn_after_close_is_refused_and_recorded(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.close(deadline=2.0)
        result = h.app.run_turn(SPEECH)
        assert result.spoken == ""
        assert app_module.APP_CLOSED in h.ledger_codes()


class TestStopWritesNothingToStderr:
    """A stop must leave ``daemon.err`` empty — checked in a real child process.

    The futures machinery prints ``RuntimeError: Event loop is closed`` from a
    done-callback that no caller can catch, so an in-process assertion cannot
    see it: it arrives on the interpreter's own stderr, from a thread, at
    whatever moment the collector runs. This test therefore runs a whole
    start/turn/stop in a CHILD and reads what the child printed — which is
    exactly what ``daemon.err`` is.
    """

    SCRIPT = """
import asyncio, io, sys, threading
from types import SimpleNamespace
from embodiment.bus import Bus
from embodiment.contract import ModelResponse
from embodiment.daemon.app import AppConfig, DaemonApp
from embodiment.daemon.state import DaemonState
from embodiment.memory import RoomMemory


class Ears:
    def __init__(self, rate=24000):
        self.q = None
        self.rate = rate

    async def connect(self):
        self.q = asyncio.Queue()
        return True

    def send_audio(self, pcm):
        return True

    async def events(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            yield item

    async def close(self, deadline=None):
        await asyncio.sleep(0.2)
        if self.q is not None:
            self.q.put_nowait(None)
        return SimpleNamespace(graceful=True)

    def status(self):
        return {"connected": True}


root = sys.argv[1]
state = DaemonState(root + "/state")
bus = Bus()
memory = RoomMemory(
    root + "/memory",
    scope="gwen",
    recall_fn=lambda q, **k: SimpleNamespace(ok=True, records=[], degradation=None),
    remember_fn=lambda t, **k: SimpleNamespace(ok=True, record_id="r", degradation=None),
    embed_probe=lambda: False,
)
app = DaemonApp(
    config=AppConfig(poll_interval_s=0.01, shutdown_deadline=2.0),
    state=state,
    bus=bus,
    memory=memory,
    complete=lambda messages, tools=None: ModelResponse(content="shalom"),
    ears_factory=lambda rate: Ears(rate),
    endpoint_factory=None,
    voice_factory=lambda ep: None,
)
stop = threading.Event()
thread = threading.Thread(target=lambda: app.run(stop), daemon=True)
thread.start()
import time as _t
_t.sleep(0.5)
stop.set()
thread.join(timeout=10)
memory.close(deadline=1.0)
bus.close(deadline=1.0)
del app, bus, memory, state
import gc
gc.collect()
_t.sleep(0.3)
print("OK", flush=True)
"""

    def test_a_full_run_and_stop_prints_nothing_to_stderr(self, tmp_path: Path) -> None:
        import subprocess  # noqa: S404 - a child is the only place this is visible

        script = tmp_path / "run_daemon.py"
        script.write_text(self.SCRIPT, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path / "home"),
            "TMPDIR": str(tmp_path / "tmp"),
            "PYTHONPATH": str(Path.cwd()),
        }
        for directory in (tmp_path / "home", tmp_path / "tmp", tmp_path / "state"):
            directory.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(  # noqa: S603 - our own interpreter, our own script
            [sys.executable, str(script), str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        assert "OK" in result.stdout, result.stderr
        assert result.returncode == 0, result.stderr
        assert result.stderr == "", f"a stop wrote to stderr:\n{result.stderr}"


class TestStatus:
    def test_status_is_json_serialisable_and_never_raises(self, harness: Any) -> None:
        h = harness()
        h.app.start()
        json.dumps(h.app.status(), ensure_ascii=False)

    def test_status_carries_every_store_counter(self, harness: Any) -> None:
        memory_status = harness().app.status()["memory"]
        for name in (
            "store_permission_failures",
            "store_symlinks_skipped",
            "store_non_files_skipped",
        ):
            assert memory_status[name] == 0, name
        assert memory_status["store_root_is_symlink"] is False

    def test_a_planted_store_symlink_is_reported_as_a_bool_not_a_count(self, harness: Any) -> None:
        """The flag is a bool; coercing it to an int would hide a tampered store."""

        class Tampered:
            store_permission_failures = 2
            store_symlinks_skipped = 1
            store_non_files_skipped = 3
            store_root_is_symlink = True
            pending = 0
            abandoned_dropped = 0
            scope = "gwen"
            data_dir = "/dev/null"
            last_recall_mode = None

            def close(self, deadline: float = 1.0) -> Any:
                return SimpleNamespace(degradations=(), unconfirmed=())

        memory_status = harness(memory=Tampered()).app.status()["memory"]
        assert memory_status["store_root_is_symlink"] is True
        assert memory_status["store_non_files_skipped"] == 3
        assert memory_status["store_permission_failures"] == 2

    def test_status_carries_the_browser_ear_contract(self, harness: Any) -> None:
        """t18 reads these two; v1 starts no RemoteEndpoint, so they are fixed."""
        status = harness().app.status()
        assert status["realtime_ear_enabled"] is False
        assert status["realtime_ws_url"] is None

    def test_the_browser_ear_fields_follow_the_config_when_one_is_set(self, harness: Any) -> None:
        h = harness(
            config=AppConfig(
                poll_interval_s=0.01,
                realtime_ear_enabled=True,
                realtime_ws_url="ws://127.0.0.1:8765",
            )
        )
        status = h.app.status()
        assert status["realtime_ear_enabled"] is True
        assert status["realtime_ws_url"] == "ws://127.0.0.1:8765"

    def test_ear_active_is_a_name_not_a_flag(self, harness: Any) -> None:
        """Confirmed for the coordinator's predicate: a string, never a bool."""
        h = harness()
        assert h.app.status()["ear"]["active"] is None
        h.app.attach_ear("host", FakeEndpoint())
        active = h.app.status()["ear"]["active"]
        assert isinstance(active, str) and not isinstance(active, bool)
        assert active == "host"
        h.app.detach_ear()
        assert h.app.status()["ear"]["active"] is None

    def test_status_names_the_active_ear_and_the_client_count(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app.attach_client()
        status = h.app.status()
        assert status["ear"]["active"] == "host"
        assert status["clients"]["count"] == 1

    def test_the_controls_bind_to_the_app(self, harness: Any) -> None:
        h = harness()
        controls = h.app.controls()
        started = controls.start_voice()
        assert started["ear"]
        assert controls.set_mute(True)["muted"] is True
        assert controls.status()["ear"]["active"]
        assert controls.stop_voice()["ear"] is None


# ── the attack surface ────────────────────────────────────────────────────────


class TestAttacks:
    def test_no_record_or_log_carries_what_was_said(self, harness: Any, tmp_path: Path) -> None:
        marker = "MARKERCANARY7788"

        def exploding_synth(sentence: str, config: Any) -> bytes:
            raise RuntimeError(f"tts blew up on {marker}")

        h = harness(synthesize=exploding_synth, complete=make_complete(f"reply {marker}"))
        h.app.attach_ear("host", FakeEndpoint())
        h.app.run_turn(f"user said {marker}")

        blob = json.dumps([r.to_dict() for r in h.state.ledger.read_all()], ensure_ascii=False)
        assert marker not in blob, blob
        assert marker not in json.dumps(h.app.status(), ensure_ascii=False)
        non_speech = [e.to_dict() for e in h.events() if e.kind not in ("transcript", "reply")]
        assert marker not in json.dumps(non_speech, ensure_ascii=False)

    def test_a_ten_thousand_character_transcript_is_survived(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        result = h.app.run_turn("א" * 10_000)
        assert result.spoken == REPLY

    def test_a_transcript_of_control_characters_is_survived(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        for text in ("\x00\x01\x02", "  \u0085", "‮​", "```", ""):
            assert isinstance(h.app.run_turn(text).spoken, str)

    def test_a_non_string_transcript_never_raises(self, harness: Any) -> None:
        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        for text in (None, 17, b"bytes", {"a": 1}):
            assert isinstance(h.app.run_turn(text).spoken, str)  # type: ignore[arg-type]

    def test_ten_thousand_frames_do_not_grow_without_bound(self, harness: Any) -> None:
        endpoint = FakeEndpoint()
        h = harness(endpoints=lambda: endpoint)
        h.app.start()
        frame = silent_pcm()
        for _ in range(10_000):
            endpoint.on_frame(frame)
        assert len(h.ears.sent) == 10_000
        assert h.app.status()["audio"]["frames_captured"] == 10_000

    def test_a_bus_that_raises_never_stops_a_turn(self, harness: Any, tmp_path: Path) -> None:
        class HostileBus(Bus):
            def publish(self, kind: str, data: Any = None) -> Any:
                if kind == "turn":
                    raise RuntimeError("bus exploded")
                return super().publish(kind, data)

        bus = HostileBus()
        h = harness(bus=bus)
        h.app.attach_ear("host", FakeEndpoint())
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert app_module.APP_PUBLISH_FAILED in h.ledger_codes()

    def test_an_endpoint_that_raises_on_every_call_is_survived(self, harness: Any) -> None:
        class Hostile(FakeEndpoint):
            def stop_capture(self) -> None:
                raise RuntimeError("no")

            def detach(self) -> None:
                raise RuntimeError("no")

            def close(self, deadline: float) -> EndpointCloseReport:
                raise RuntimeError("no")

        h = harness()
        h.app.attach_ear("host", Hostile())
        outcome = h.app.detach_ear()
        assert outcome.attached is False
        assert app_module.APP_EAR_DETACH_FAILED in h.ledger_codes()

    def test_concurrent_attaches_never_leave_two_ears(self, harness: Any) -> None:
        h = harness(config=AppConfig(preempt_ear=True, poll_interval_s=0.01))
        endpoints = [FakeEndpoint(name=f"e{i}") for i in range(12)]
        barrier = threading.Barrier(len(endpoints))

        def attach(index: int) -> None:
            barrier.wait(timeout=5)
            h.app.attach_ear(f"e{index}", endpoints[index])

        threads = [threading.Thread(target=attach, args=(i,)) for i in range(len(endpoints))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        capturing = [e for e in endpoints if e.capturing]
        assert len(capturing) == 1, [e.name for e in capturing]

    def test_status_survives_every_seam_raising_on_attribute_access(self, harness: Any) -> None:
        """Found by attacking: one hostile seam used to blank the whole report."""

        class Exploding:
            def __getattr__(self, name: str) -> Any:
                raise RuntimeError("no")

        h = harness()
        h.app.attach_ear("host", FakeEndpoint())
        h.app._ears = Exploding()
        h.app._voice = Exploding()
        h.app._server = Exploding()
        status = h.app.status()
        assert status["ear"]["active"] == "host"
        assert status["ears"] == {"unavailable": True}
        assert status["voice"] == {"unavailable": True}
        h.app._ears = FakeEars()
        h.app._voice = None
        h.app._server = None

    def test_the_null_endpoint_is_a_legal_ear(self, harness: Any) -> None:
        h = harness()
        handover = h.app.attach_ear("null", NullEndpoint())
        assert handover.attached is True
        assert h.app.run_turn(SPEECH).spoken == REPLY


class TestModelSeam:
    """:func:`http_complete` — shaped here, dialled for real only in ``t21``."""

    def _capture(self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> list[Any]:
        seen: list[Any] = []

        class FakeResponse:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *args: Any) -> bool:
                return False

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request: Any, timeout: float = 0) -> Any:
            seen.append(request)
            return FakeResponse()

        monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
        return seen

    def test_the_role_is_the_model_and_the_key_is_a_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._capture(
            monkeypatch,
            {
                "choices": [{"message": {"content": "שלום"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 9},
            },
        )
        response = app_module.http_complete(
            [{"role": "user", "content": "היי"}],
            gateway_url="http://gateway.invalid/",
            api_key="SECRETKEY",
            role="senses",
        )
        assert response.content == "שלום"
        assert (response.prompt_tokens, response.completion_tokens) == (7, 9)
        request = seen[0]
        assert request.full_url == "http://gateway.invalid/v1/chat/completions"
        assert "SECRETKEY" not in request.full_url
        assert request.headers["Authorization"] == "Bearer SECRETKEY"
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "senses"
        assert body["messages"] == [{"role": "user", "content": "היי"}]
        assert "tools" not in body

    def test_a_non_http_origin_is_refused_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._capture(monkeypatch, {})
        with pytest.raises(ValueError):
            app_module.http_complete([], gateway_url="file:///etc/passwd")
        assert seen == []

    def test_a_malformed_body_still_yields_a_model_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._capture(monkeypatch, {"choices": []})
        response = app_module.http_complete([], gateway_url="http://gateway.invalid")
        assert response.content == ""


class TestTheTailnetBind:
    """Round 8: the operator reviews the dashboard from a phone over Tailscale."""

    def test_the_env_reaches_the_server_and_guard_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class Recording:
            def __init__(self, *, config: Any, guard: Any, **kwargs: Any) -> None:
                captured["config"] = config
                captured["guard"] = guard

            def start(self) -> None:
                return None

            def shutdown(self, deadline: float) -> None:
                return None

            def status(self) -> dict[str, Any]:
                return {"bind": config_bind(captured), "running": True}

        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv(app_module.ENV_HTTP_BIND, "100.64.0.7")
        monkeypatch.setenv(app_module.ENV_BIND_PUBLIC, "1")
        monkeypatch.setenv(app_module.ENV_ALLOWED_HOSTS, "100.64.0.7:8823, gwen.tailnet.ts.net")
        monkeypatch.setattr(app_module.server_module, "DashboardServer", Recording)

        application = app_module.main()
        try:
            assert captured["config"].bind == "100.64.0.7"
            assert captured["config"].bind_public is True
            allowed = captured["guard"].config.allowed_hosts
            # The guard strips the port off a Host header before comparing,
            # so the allow-list must hold the hostname and the ORIGIN list
            # must keep the port. Backwards would refuse every request.
            assert "100.64.0.7" in allowed
            assert "100.64.0.7:8823" not in allowed
            assert "gwen.tailnet.ts.net" in allowed
            assert "127.0.0.1" in allowed, "loopback must stay allowed"
            origins = captured["guard"].config.allowed_origins
            assert "http://100.64.0.7:8823" in origins
            assert "http://gwen.tailnet.ts.net" in origins
        finally:
            application.close(deadline=2.0)

    def test_a_routable_bind_without_the_flag_never_binds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() never raises, so the refusal here is a recorded degradation."""
        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv(app_module.ENV_HTTP_BIND, "100.64.0.7")
        monkeypatch.delenv(app_module.ENV_BIND_PUBLIC, raising=False)

        application = app_module.main()
        try:
            assert application._server is None
            codes = [r.code for r in application._state.ledger.read_all()]
            assert app_module.APP_BOOTSTRAP_DEGRADED in codes
        finally:
            application.close(deadline=2.0)

    def test_status_reports_the_bind_without_the_hosts_or_the_secret(self, harness: Any) -> None:
        secret = "INSTALLSECRET-abc123"  # nosec B105 - a planted marker
        h = harness(
            config=AppConfig(
                poll_interval_s=0.01,
                bind="100.64.0.7",
                bind_public=True,
                allowed_hosts=("100.64.0.7:8823", "gwen.tailnet.ts.net"),
                api_key=secret,
            )
        )
        http_status = h.app.status()["http"]
        assert http_status["configured_bind"] == "100.64.0.7"
        assert http_status["bind_public"] is True
        assert http_status["allowed_hosts"] == 2
        blob = json.dumps(h.app.status(), ensure_ascii=False)
        assert "gwen.tailnet.ts.net" not in blob, "the allow-list leaked into status"
        assert secret not in blob

    def test_the_servers_own_status_still_passes_through_whole(self, harness: Any) -> None:
        """t16 round 4's control counters must arrive without per-field plumbing."""

        class Server:
            def start(self) -> None:
                return None

            def shutdown(self, deadline: float) -> None:
                return None

            def status(self) -> dict[str, Any]:
                return {
                    "bind": "127.0.0.1",
                    "controls_inflight": 1,
                    "controls_timed_out": 2,
                    "controls_refused_busy": 3,
                    "control_timeout_s": 4.0,
                }

        h = harness(server=Server())
        http_status = h.app.status()["http"]
        assert http_status["controls_inflight"] == 1
        assert http_status["controls_timed_out"] == 2
        assert http_status["controls_refused_busy"] == 3
        assert http_status["control_timeout_s"] == 4.0

    def test_the_start_verb_refuses_a_routable_bind_at_parse_time(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The refusal the operator actually meets, before anything is spawned."""
        from embodiment.cli import main as cli_main

        code = cli_main(["start", "--http-bind", "100.64.0.7"])
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out == ""
        assert captured.err.startswith("error:")
        assert "hint:" in captured.err
        assert "--bind-public" in captured.err
        assert "Traceback" not in captured.err

    def test_the_start_verb_builds_the_env_for_the_child(self) -> None:
        """start re-execs, so a flag only reaches the daemon as environment."""
        from embodiment.cli._commands import start as start_cmd

        args = argparse.Namespace(
            http_bind="100.64.0.7",
            bind_public=True,
            allowed_host=["100.64.0.7:8823", "gwen.tailnet.ts.net"],
        )
        env = start_cmd._http_env(args)
        assert env[app_module.ENV_HTTP_BIND] == "100.64.0.7"
        assert env[app_module.ENV_BIND_PUBLIC] == "1"
        assert env[app_module.ENV_ALLOWED_HOSTS] == "100.64.0.7:8823,gwen.tailnet.ts.net"

    def test_a_loopback_bind_needs_no_flag_and_lists_no_hosts(self) -> None:
        from embodiment.cli._commands import start as start_cmd

        args = argparse.Namespace(http_bind="127.0.0.1", bind_public=False, allowed_host=[])
        env = start_cmd._http_env(args)
        assert env[app_module.ENV_BIND_PUBLIC] == "0"
        assert app_module.ENV_ALLOWED_HOSTS not in env

    def test_an_allowed_host_passes_the_guard_and_an_unlisted_one_does_not(self) -> None:
        """The guard is what actually decides; this is the end of the wire."""
        from embodiment.http import guard as guard_module

        config = app_module.AppConfig(
            allowed_hosts=("100.64.0.7:8823",), bind="100.64.0.7", bind_public=True
        )
        guard = guard_module.Guard(
            guard_module.GuardConfig(
                install_secret="s" * 32,
                allowed_hosts=guard_module.DEFAULT_ALLOWED_HOSTS
                | frozenset(app_module.guard_host_of(h) for h in config.allowed_hosts),
                allowed_origins=frozenset(f"http://{h}" for h in config.allowed_hosts),
            )
        )
        allowed = guard.check(
            "GET",
            "/api/events",
            {"host": "100.64.0.7:8823", "authorization": "Bearer " + "s" * 32},
        )
        assert allowed.allowed is True, allowed.to_dict()

        refused = guard.check(
            "GET",
            "/api/events",
            {"host": "10.1.2.3:8823", "authorization": "Bearer " + "s" * 32},
        )
        assert refused.allowed is False
        assert refused.code == guard_module.REFUSED_HOST_CODE

    def test_both_schemes_are_allowed_origins(self) -> None:
        """A TLS terminator presents https while the Host stays the same.

        Found on the tailnet: over plain http a same-origin EventSource sends
        no Origin and no Sec-Fetch-* (browsers send those only to secure
        contexts), so the cookie can never be vouched for and the stream is
        refused. The way out is TLS in front — and then the control POSTs
        arrive with an https Origin against the same Host.
        """
        origins = app_module.allowed_origins_for(("100.64.0.7:8823",))
        assert "http://100.64.0.7:8823" in origins
        assert "https://100.64.0.7:8823" in origins

    def test_both_schemes_reach_the_guard_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class Recording:
            def __init__(self, *, config: Any, guard: Any, **kwargs: Any) -> None:
                captured["guard"] = guard

            def start(self) -> None:
                return None

            def shutdown(self, deadline: float) -> None:
                return None

            def status(self) -> dict[str, Any]:
                return {}

        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv(app_module.ENV_HTTP_BIND, "100.64.0.7")
        monkeypatch.setenv(app_module.ENV_BIND_PUBLIC, "1")
        monkeypatch.setenv(app_module.ENV_ALLOWED_HOSTS, "spark.tail0be7e0.ts.net")
        monkeypatch.setattr(app_module.server_module, "DashboardServer", Recording)

        application = app_module.main()
        try:
            origins = captured["guard"].config.allowed_origins
            assert "http://spark.tail0be7e0.ts.net" in origins
            assert "https://spark.tail0be7e0.ts.net" in origins
        finally:
            application.close(deadline=2.0)

    def test_an_https_origin_passes_the_guard_on_a_control_post(self) -> None:
        """The end of the wire: the terminator's Origin against the same Host."""
        from embodiment.http import guard as guard_module

        hosts = ("spark.tail0be7e0.ts.net",)
        guard = guard_module.Guard(
            guard_module.GuardConfig(
                install_secret="s" * 32,
                allowed_hosts=guard_module.DEFAULT_ALLOWED_HOSTS
                | frozenset(app_module.guard_host_of(h) for h in hosts),
                allowed_origins=frozenset(app_module.allowed_origins_for(hosts)),
            )
        )
        for scheme in ("http", "https"):
            decision = guard.check(
                "POST",
                "/api/voice/start",
                {
                    "host": "spark.tail0be7e0.ts.net",
                    "origin": f"{scheme}://spark.tail0be7e0.ts.net",
                    "authorization": "Bearer " + "s" * 32,
                },
            )
            assert decision.allowed is True, (scheme, decision.to_dict())

        foreign = guard.check(
            "POST",
            "/api/voice/start",
            {
                "host": "spark.tail0be7e0.ts.net",
                "origin": "https://evil.example",
                "authorization": "Bearer " + "s" * 32,
            },
        )
        assert foreign.allowed is False
        assert foreign.code == guard_module.REFUSED_ORIGIN_CODE

    @pytest.mark.parametrize(
        "bind,expected",
        [
            ("127.0.0.1", False),
            ("localhost", False),
            ("::1", False),
            ("100.64.0.7", True),
            ("0.0.0.0", True),
        ],
    )
    def test_status_says_when_a_secure_context_is_required(
        self, harness: Any, bind: str, expected: bool
    ) -> None:
        """So a host sees it here, not as ten refused stream requests."""
        h = harness(config=AppConfig(poll_interval_s=0.01, bind=bind, bind_public=expected))
        assert h.app.status()["http"]["secure_context_required"] is expected

    @pytest.mark.parametrize(
        "entry,expected",
        [
            ("100.64.0.7:8823", "100.64.0.7"),
            ("100.64.0.7", "100.64.0.7"),
            ("GWEN.tailnet.ts.net:8823", "gwen.tailnet.ts.net"),
            ("[fd7a::1]:8823", "[fd7a::1]"),
            ("[fd7a::1]", "[fd7a::1]"),
        ],
    )
    def test_the_guard_form_drops_the_port(self, entry: str, expected: str) -> None:
        assert app_module.guard_host_of(entry) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", ()),
            ("  ", ()),
            ("a", ("a",)),
            ("a,b", ("a", "b")),
            (" a , b ", ("a", "b")),
            ("a,a,b", ("a", "b")),
            ("a,,b", ("a", "b")),
        ],
    )
    def test_the_allowed_hosts_list_is_parsed_exactly(
        self, raw: str, expected: tuple[str, ...]
    ) -> None:
        assert app_module.parse_allowed_hosts(raw) == expected


def config_bind(captured: dict[str, Any]) -> str:
    return str(getattr(captured.get("config"), "bind", ""))


class TestProcessModel:
    def test_main_is_a_zero_argument_factory_returning_a_runnable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.delenv("EMBODIMENT_GATEWAY_KEY", raising=False)
        monkeypatch.delenv("CULTURE_VLLM_API_KEY", raising=False)
        application = app_module.main()
        try:
            assert callable(getattr(application, "run", None))
            assert callable(getattr(application, "shutdown", None))
        finally:
            application.close(deadline=2.0)

    def test_the_lifecycle_default_target_resolves_to_main(self) -> None:
        from embodiment.daemon import lifecycle

        factory, detail = lifecycle.resolve_target(lifecycle.DEFAULT_TARGET)
        assert detail is None, detail
        assert factory is app_module.main

    def test_a_dashboard_that_cannot_bind_leaves_a_running_daemon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seen for real: port 8823 already taken, DashboardServer raised at __init__."""
        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "state"))

        def refuse(**kwargs: Any) -> Any:
            raise OSError(98, "Address already in use")

        monkeypatch.setattr(app_module.server_module, "DashboardServer", refuse)
        application = app_module.main()
        try:
            assert application._server is None
            codes = [r.code for r in application._state.ledger.read_all()]
            assert app_module.APP_BOOTSTRAP_DEGRADED in codes
            assert isinstance(application.status(), dict)
        finally:
            application.close(deadline=2.0)

    def test_a_server_that_will_not_start_is_recorded_not_fatal(self, harness: Any) -> None:
        class RefusingServer:
            def start(self) -> None:
                raise OSError(98, "Address already in use")

            def shutdown(self, deadline: float) -> None:
                return None

            def status(self) -> dict[str, Any]:
                return {"bound": False}

        h = harness(server=RefusingServer())
        h.app.start()
        assert app_module.APP_HTTP_UNAVAILABLE in h.ledger_codes()
        assert h.app.status()["running"] is True

    def test_main_never_raises_when_the_environment_is_hostile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EMBODIMENT_STATE_DIR", str(tmp_path / "nope" / "deep"))
        monkeypatch.setenv("EMBODIMENT_GATEWAY_URL", "not-a-url")
        application = app_module.main()
        try:
            assert isinstance(application.status(), dict)
        finally:
            application.close(deadline=2.0)
