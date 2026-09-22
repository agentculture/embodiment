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

import asyncio
import io
import json
import os
import re
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
        h.clear()
        assert h.app.run_turn(SPEECH).spoken == REPLY
        assert [e.data["text"] for e in h.events("reply")] == [REPLY]
        assert not endpoint.played, "a detached endpoint was played into"


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
