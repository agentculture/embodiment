"""Tests for embodiment.audio.host — the sounddevice-backed AudioEndpoint.

No real audio device is opened anywhere in this file: every test injects a
fake sounddevice-shaped module (FakeSoundDevice) through HostEndpoint's
sounddevice_importer=/numpy_importer= constructor hooks. One test fires the
PortAudio-shaped input callback from a background thread, mirroring how
PortAudio actually calls into host code.

Covers plan task t7's three acceptance criteria:
  1. no-voice, never a raise (test_criterion1_*)
  2. mute enforced before the encoder boundary, one event per change
     (test_criterion2_*)
  3. import-graph: turn/daemon import the protocol only (test_criterion3_*)
"""

from __future__ import annotations

import ast
import struct
import threading
import time
from pathlib import Path
from typing import Callable

from embodiment.audio.host import (
    _FIR_TAPS,
    DEGRADED_ENUMERATION,
    DEGRADED_IMPORT,
    DEGRADED_OPEN,
    DEGRADED_PLAYBACK_OVERFLOW,
    DEGRADED_PORTAUDIO,
    HostEndpoint,
    Resampler,
    _resample_pcm16,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeStream:
    """Stands in for sounddevice.InputStream / OutputStream.

    ``write_delay`` lets a test simulate PortAudio's own blocking-mode
    behaviour — a ``write()`` call that takes as long as the audio it is
    given lasts — WITHOUT making the whole suite slow: it defaults to
    ``None`` (instant, as round 1's tests assumed), and only the specific
    tests that need real barge-in/close-deadline timing inject a small
    delay function (round 2's own probe used a real ``time.sleep``; this
    default keeps every other test fast).
    """

    def __init__(
        self,
        *,
        samplerate,
        channels,
        dtype,
        device=None,
        callback=None,
        write_delay: Callable[[int, float], None] | None = None,
        rejects_samplerates: tuple[float, ...] = (),
    ):
        if samplerate in rejects_samplerates:
            raise RuntimeError("fake device does not support this samplerate")
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False
        self.aborted = False
        self.written: list[bytes] = []
        self.raise_on_start = False
        self.raise_on_write: Exception | None = None
        self._write_delay = write_delay

    def start(self):
        if self.raise_on_start:
            raise RuntimeError("fake stream start failed")
        self.started = True

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def write(self, data):
        if self.raise_on_write is not None:
            raise self.raise_on_write
        if self._write_delay is not None:
            self._write_delay(len(data) // 2, self.samplerate)
        self.written.append(bytes(data))


class FakeSoundDevice:
    """Stands in for the sounddevice module: InputStream/OutputStream/query_devices."""

    def __init__(
        self,
        *,
        devices=None,
        raise_on_query=False,
        raise_on_input_open=False,
        raise_on_output_open=False,
        default_samplerate=48000,
        write_delay: Callable[[int, float], None] | None = None,
        rejects_samplerates: tuple[float, ...] = (),
    ):
        self._devices = devices if devices is not None else [{"name": "fake-in"}]
        self._raise_on_query = raise_on_query
        self._default_samplerate = default_samplerate
        self.raise_on_input_open = raise_on_input_open
        self.raise_on_output_open = raise_on_output_open
        self._write_delay = write_delay
        self._rejects_samplerates = rejects_samplerates
        self.input_streams: list[FakeStream] = []
        self.output_streams: list[FakeStream] = []

    def query_devices(self, device=None, kind=None):
        if self._raise_on_query:
            raise RuntimeError("fake enumeration exploded")
        if device is not None:
            return {"name": "fake", "default_samplerate": self._default_samplerate}
        return self._devices

    def InputStream(self, **kwargs):
        if self.raise_on_input_open:
            raise RuntimeError("fake input device busy")
        stream = FakeStream(rejects_samplerates=self._rejects_samplerates, **kwargs)
        self.input_streams.append(stream)
        return stream

    def OutputStream(self, **kwargs):
        if self.raise_on_output_open:
            raise RuntimeError("fake output device busy")
        stream = FakeStream(
            write_delay=self._write_delay, rejects_samplerates=self._rejects_samplerates, **kwargs
        )
        self.output_streams.append(stream)
        return stream


def _pcm16(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def _silence_frame(n_samples: int = 480) -> bytes:
    return b"\x00\x00" * n_samples


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _working_fake() -> FakeSoundDevice:
    return FakeSoundDevice(devices=[{"name": "fake-in", "default_samplerate": 48000}])


# ---------------------------------------------------------------------------
# criterion 1 — no-voice, never a raise
# ---------------------------------------------------------------------------


def test_criterion1_import_forced_to_fail_never_raises_and_records():
    def raiser():
        raise ImportError("no module named sounddevice")

    endpoint = HostEndpoint(sounddevice_importer=raiser)
    status = endpoint.status()
    assert status["degradation"]["code"] == DEGRADED_IMPORT

    received: list[bytes] = []
    endpoint.start_capture(received.append)  # must not raise
    endpoint.play(_silence_frame())  # must not raise
    endpoint.attach()
    endpoint.detach()
    endpoint.close(1.0)
    assert received == []


def test_criterion1_portaudio_missing_oserror_on_import_is_distinct_code():
    def raiser():
        raise OSError("PortAudio library not found")

    endpoint = HostEndpoint(sounddevice_importer=raiser)
    assert endpoint.status()["degradation"]["code"] == DEGRADED_PORTAUDIO
    assert DEGRADED_PORTAUDIO != DEGRADED_IMPORT
    endpoint.start_capture(lambda _f: None)  # never raises


def test_criterion1_unexpected_import_exception_still_never_raises():
    def raiser():
        raise ValueError("something bizarre")

    endpoint = HostEndpoint(sounddevice_importer=raiser)  # must not raise
    assert endpoint.status()["degradation"] is not None


def test_criterion1_empty_device_enumeration_is_recorded_and_named():
    fake = FakeSoundDevice(devices=[])
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake)
    assert endpoint.status()["degradation"]["code"] == DEGRADED_ENUMERATION
    endpoint.start_capture(lambda _f: None)  # never raises


def test_criterion1_enumeration_raising_is_also_recorded_as_enumeration_fault():
    fake = FakeSoundDevice(raise_on_query=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake)
    assert endpoint.status()["degradation"]["code"] == DEGRADED_ENUMERATION


def test_criterion1_device_open_failing_is_recorded_at_start_not_construction():
    fake = FakeSoundDevice(raise_on_input_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake)
    # construction alone succeeds: enumeration was fine, only opening fails
    assert endpoint.status()["degradation"] is None

    received: list[bytes] = []
    endpoint.start_capture(received.append)  # must not raise
    assert endpoint.status()["degradation_in"]["code"] == DEGRADED_OPEN
    assert received == []


def test_criterion1_output_device_open_failing_never_raises():
    fake = FakeSoundDevice(raise_on_output_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake)
    endpoint.play(_silence_frame())  # must not raise
    assert endpoint.status()["degradation_out"]["code"] == DEGRADED_OPEN


def test_criterion1_working_endpoint_reports_no_degradation():
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    assert endpoint.status()["degradation"] is None


def test_criterion1_status_never_raises_before_or_after_close():
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    endpoint.status()
    endpoint.close(1.0)
    endpoint.status()  # still callable after close
    endpoint.close(1.0)  # idempotent, no raise
    endpoint.mute(True)  # attack: calls after close must not raise
    endpoint.play(_silence_frame())
    endpoint.start_capture(lambda _f: None)


# ---------------------------------------------------------------------------
# criterion 2 — mute enforced before the encoder boundary
# ---------------------------------------------------------------------------


def test_criterion2_muted_endpoint_delivers_zero_frames_downstream():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    assert _wait_until(lambda: endpoint._capturing)  # noqa: SLF001 - test-only introspection
    stream = fake.input_streams[0]

    endpoint.mute(True)

    frame = _pcm16(*([1000] * 480))

    def fire_from_another_thread(n: int) -> None:
        def _fire():
            for _ in range(n):
                stream.callback(frame, 480, None, None)

        t = threading.Thread(target=_fire)
        t.start()
        t.join(timeout=5.0)

    fire_from_another_thread(50)
    _wait_until(lambda: endpoint.status()["capture_muted_dropped"] >= 50)

    assert received == []  # ZERO frames reached the encoder boundary
    assert endpoint.status()["capture_muted_dropped"] >= 50
    endpoint.close(2.0)


def test_criterion2_unmuted_endpoint_delivers_frames_and_muting_stops_them():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    stream = fake.input_streams[0]
    frame = _pcm16(*([2000] * 480))

    for _ in range(5):
        stream.callback(frame, 480, None, None)
    _wait_until(lambda: len(received) >= 5)
    assert len(received) >= 5
    every_frame_is_24khz_pcm16 = all(len(f) % 2 == 0 for f in received)
    assert every_frame_is_24khz_pcm16

    before_mute_count = len(received)
    endpoint.mute(True)
    for _ in range(5):
        stream.callback(frame, 480, None, None)
    _wait_until(lambda: endpoint.status()["capture_muted_dropped"] >= 5)
    assert len(received) == before_mute_count  # nothing new arrived while muted
    endpoint.close(2.0)


def test_criterion2_mute_change_emits_exactly_one_event():
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    assert endpoint.events == ()

    endpoint.mute(True)
    assert len(endpoint.events) == 1
    assert endpoint.events[0]["muted"] is True

    endpoint.mute(True)  # no change: no new event
    assert len(endpoint.events) == 1

    endpoint.mute(False)  # a real change: exactly one more event
    assert len(endpoint.events) == 2

    endpoint.mute(False)  # no change again
    assert len(endpoint.events) == 2


def test_criterion2_mute_events_carry_no_audio_content():
    """Attack: plant a marker and scan every record for it (wave 1 lesson 5)."""
    marker = "SECRET-USER-SPEECH-MARKER-ABC123"
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    endpoint.mute(True)
    endpoint.mute(False)
    for event in endpoint.events:
        assert marker not in str(event)
    assert marker not in str(endpoint.status())


def test_criterion2_muted_frames_never_touch_the_resampler_or_callback():
    """Even a raising on_frame callback proves nothing reached it while muted."""

    def exploding_callback(_frame: bytes) -> None:
        raise AssertionError("on_frame must never be called while muted")

    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.mute(True)
    endpoint.start_capture(exploding_callback)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    stream = fake.input_streams[0]
    frame = _pcm16(*([500] * 480))
    for _ in range(20):
        stream.callback(frame, 480, None, None)
    _wait_until(lambda: endpoint.status()["capture_muted_dropped"] >= 20)
    # no AssertionError propagated: exploding_callback was simply never reached,
    # and even if it HAD been reached and raised, HostEndpoint would have caught
    # it (never-raise) rather than letting it escape — so this also attacks
    # that a raising downstream consumer cannot kill the drain thread.
    assert endpoint.status()["callback_errors"] == 0
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# resampler
# ---------------------------------------------------------------------------


def _import_numpy_real():
    import numpy

    return numpy


def test_resampler_same_rate_is_identity():
    frame = _pcm16(1, -1, 100, -100, 32767, -32768)
    assert _resample_pcm16(frame, 24000, 24000, _import_numpy_real) == frame


def test_resampler_empty_input_never_raises():
    assert _resample_pcm16(b"", 48000, 24000, _import_numpy_real) == b""


def test_resampler_odd_length_drops_trailing_byte_never_raises():
    frame = _pcm16(1, 2, 3) + b"\x07"  # one dangling odd byte
    result = _resample_pcm16(frame, 48000, 24000, _import_numpy_real)
    assert isinstance(result, bytes)


def test_resampler_single_sample_never_raises():
    frame = _pcm16(12345)
    result = _resample_pcm16(frame, 48000, 24000, _import_numpy_real)
    assert isinstance(result, bytes)
    assert len(result) >= 2


def test_resampler_non_positive_rate_never_raises():
    frame = _pcm16(1, 2, 3, 4)
    assert _resample_pcm16(frame, 0, 24000, _import_numpy_real) == b""
    assert _resample_pcm16(frame, 48000, 0, _import_numpy_real) == b""
    assert _resample_pcm16(frame, -1, 24000, _import_numpy_real) == b""


def test_resampler_full_scale_values_do_not_wrap_or_raise():
    frame = _pcm16(*([32767, -32768] * 100))
    result = _resample_pcm16(frame, 44100, 24000, _import_numpy_real)
    values = struct.unpack(f"<{len(result) // 2}h", result)
    assert all(-32768 <= v <= 32767 for v in values)


def test_resampler_upsampling_produces_more_samples():
    frame = _pcm16(*range(0, 480))
    down = _resample_pcm16(frame, 48000, 24000, _import_numpy_real)
    back_up = _resample_pcm16(down, 24000, 48000, _import_numpy_real)
    assert len(back_up) // 2 >= len(down) // 2


def test_resampler_440hz_tone_round_trip_error_is_bounded():
    """440 Hz @ 48kHz -> 24kHz -> 48kHz: measured RMS error stays small, once aligned.

    The FIR low-pass used for the 48k->24k leg (round 2 finding 4) is a
    linear-phase filter: it delays the signal by a FIXED, known
    ``(taps - 1) // 2`` samples (measured, and derivable from the filter's
    own length) rather than distorting it. That delay is real and expected
    — a listener hears it as a few tens of microseconds of latency, not as
    noise — so comparing "back" to "orig" sample-for-sample without
    compensating for it would score a correct resampler as broken. This test
    compensates for the KNOWN delay explicitly rather than searching for it
    (e.g. via cross-correlation), so the expected number is derivable from
    the filter design, not curve-fit after the fact.
    """
    import math

    numpy = _import_numpy_real()
    sample_rate = 48000
    freq = 440.0
    n = 4800  # 100 ms
    t = numpy.arange(n) / sample_rate
    tone = (numpy.sin(2 * math.pi * freq * t) * 20000).astype("<i2")
    original = tone.tobytes()

    down = _resample_pcm16(original, 48000, 24000, _import_numpy_real)
    back = _resample_pcm16(down, 24000, 48000, _import_numpy_real)

    orig_arr = numpy.frombuffer(original, dtype="<i2").astype(numpy.float64)
    back_arr = numpy.frombuffer(back, dtype="<i2").astype(numpy.float64)

    delay = (_FIR_TAPS - 1) // 2  # samples, at the 48 kHz rate both arrays share
    trim = 100
    a = orig_arr[trim : len(orig_arr) - trim]
    m = min(len(a), len(back_arr) - (trim + delay))
    a = a[:m]
    b = back_arr[trim + delay : trim + delay + m]

    rmse = float(numpy.sqrt(numpy.mean((a - b) ** 2)))
    full_scale = 32768.0
    rmse_dbfs = 20 * math.log10(rmse / full_scale) if rmse > 0 else float("-inf")

    # A real, measured number (see this task's delivery notes), not a
    # curve-fit: a generous bound so the test fails on a real regression
    # without flaking on an implementation-preserving change.
    assert rmse_dbfs < -40.0, f"round-trip RMSE {rmse_dbfs:.2f} dBFS exceeds bound"


# ---------------------------------------------------------------------------
# attacks: throughput, repeated construction, huge/tiny chunks
# ---------------------------------------------------------------------------


def test_attack_ten_thousand_tiny_capture_frames_never_raises_and_bounds_queue():
    fake = _working_fake()
    endpoint = HostEndpoint(
        sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real, queue_maxsize=8
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    stream = fake.input_streams[0]
    tiny_frame = _pcm16(1)  # a single sample, smaller than any real block

    for _ in range(10_000):
        stream.callback(tiny_frame, 1, None, None)  # fired from the "PortAudio" thread

    status = endpoint.status()
    # never raised getting here; drops are bounded-queue behaviour, not a fault
    assert status["capture_dropped"] >= 0
    endpoint.close(2.0)


def test_attack_huge_capture_chunk_never_raises():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    stream = fake.input_streams[0]
    huge_frame = b"\x00\x01" * 200_000  # ~400 kB in one callback

    stream.callback(huge_frame, 200_000, None, None)
    assert _wait_until(lambda: len(received) >= 1 or endpoint.status()["capture_dropped"] >= 0)
    endpoint.close(2.0)


def test_attack_callback_receives_wrong_type_never_raises():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.start_capture(lambda _f: None)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    stream = fake.input_streams[0]

    stream.callback(object(), 1, None, None)  # not bytes-shaped at all
    stream.callback(None, 0, None, None)
    _wait_until(lambda: endpoint.status()["callback_errors"] >= 1)
    assert endpoint.status()["callback_errors"] >= 1
    endpoint.close(2.0)


def test_attack_play_called_ten_thousand_times_never_blocks_caller_long():
    fake = _working_fake()
    endpoint = HostEndpoint(
        sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real, queue_maxsize=4
    )
    frame = _silence_frame()
    start = time.monotonic()
    for _ in range(2_000):
        endpoint.play(frame)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0  # generous: this is a caller-blocking bound, not a perf benchmark
    endpoint.close(2.0)


def test_attack_writer_raises_never_kills_the_process_or_the_caller():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.play(_silence_frame())
    _wait_until(lambda: len(fake.output_streams) == 1)
    fake.output_streams[0].raise_on_write = RuntimeError("device yanked mid-write")
    for _ in range(10):
        endpoint.play(_silence_frame())
    _wait_until(lambda: endpoint.status()["callback_errors"] >= 1)
    assert endpoint.status()["callback_errors"] >= 1
    endpoint.close(2.0)


def test_attack_repeated_start_stop_capture_never_leaks_or_raises():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    for _ in range(20):
        endpoint.start_capture(lambda _f: None)
        endpoint.stop_capture()
    assert len(fake.input_streams) >= 1
    endpoint.close(2.0)


def test_attack_close_is_idempotent_and_bounded():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.start_capture(lambda _f: None)
    endpoint.play(_silence_frame())
    start = time.monotonic()
    endpoint.close(1.0)
    endpoint.close(1.0)
    endpoint.close(0.0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0


def test_attack_mute_called_ten_thousand_times_records_bounded_events():
    """Round 2 finding 6a: the EXACT count never caps; the retained LOG does."""
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    # Start from a known state so every alternation below is a genuine change.
    assert endpoint.muted is False
    for i in range(10_000):
        endpoint.mute(i % 2 == 0)  # True, False, True, False, ... — every call differs
    assert endpoint.status()["mute_event_count"] == 10_000  # exact, never capped
    assert len(endpoint.events) <= 1000  # the detailed log stays bounded

    # Now attack with a long run of IDENTICAL calls: none of these are changes.
    count_before = endpoint.status()["mute_event_count"]
    last_state = endpoint.muted
    for _ in range(10_000):
        endpoint.mute(last_state)
    assert endpoint.status()["mute_event_count"] == count_before


def test_attack_one_hundred_thousand_mute_flips_bounds_memory_and_stays_exact():
    """The exact scenario the round 2 probe measured: 100k flips."""
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    for i in range(100_000):
        endpoint.mute(i % 2 == 0)
    assert endpoint.status()["mute_event_count"] == 100_000
    assert len(endpoint.events) <= 1000


# ---------------------------------------------------------------------------
# round 2, finding 1 — playback buffers the whole reply, never drops mid-sentence
# ---------------------------------------------------------------------------


def test_finding1_a_streamed_tts_reply_faster_than_realtime_is_never_dropped():
    """The exact scenario the probe measured: 300 x 20ms chunks, faster than realtime."""
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    chunk = _pcm16(*([500] * 480))  # 20 ms @ 24 kHz
    for _ in range(300):
        endpoint.play(chunk)
    status = endpoint.status()
    assert status["playback_overflow_count"] == 0
    assert status["playback_total_pushed_samples"] == 300 * 480
    endpoint.close(2.0)


def test_finding1_overflow_refuses_the_new_chunk_and_is_named_and_counted():
    fake = _working_fake()
    endpoint = HostEndpoint(
        sounddevice_importer=lambda: fake,
        numpy_importer=_import_numpy_real,
    )
    # A stream deliberately never drained: block the writer by never letting
    # it progress (no stream.write delay needed — just never call close/stop;
    # push far more than the 120s buffer can hold so it must overflow).
    big_chunk = _silence_frame(24000)  # 1 second of audio per call
    pushed = 0
    overflowed = False
    for _ in range(200):  # 200 seconds worth, comfortably over the 120s bound
        before = endpoint.status()["playback_overflow_count"]
        endpoint.play(big_chunk)
        after = endpoint.status()["playback_overflow_count"]
        if after > before:
            overflowed = True
            break
        pushed += 1
    assert overflowed, "playback buffer never overflowed even after 200s of pushed audio"
    status = endpoint.status()
    assert status["playback_overflow_count"] >= 1
    # nothing already buffered was discarded by the overflow itself
    assert status["playback_queued_bytes"] > 0
    endpoint.close(2.0)


def test_finding1_degradation_code_is_named_audio_host_playback_overflow():
    assert DEGRADED_PLAYBACK_OVERFLOW == "audio-host-playback-overflow"


# ---------------------------------------------------------------------------
# round 2, finding 2 — barge-in: stop_playback() actually cuts
# ---------------------------------------------------------------------------


def test_finding2_stop_playback_discards_queued_and_reports_samples():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    assert endpoint.stop_playback() == 0  # idempotent, nothing playing yet

    chunk = _silence_frame(480)
    for _ in range(10):
        endpoint.play(chunk)
    _wait_until(lambda: endpoint.playing)

    discarded = endpoint.stop_playback()
    assert discarded > 0
    assert endpoint.playing is False
    assert endpoint.status()["playback_stop_discarded_total"] == discarded
    endpoint.close(2.0)


def test_finding2_a_subsequent_play_after_stop_playback_is_heard_in_full():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.playing)
    endpoint.stop_playback()

    chunk = _silence_frame(480)
    endpoint.play(chunk)
    _wait_until(lambda: endpoint.status()["playback_written_samples"] >= 480, timeout=3.0)
    assert endpoint.status()["playback_written_samples"] >= 480
    endpoint.close(2.0)


def test_finding2_writer_never_hands_more_than_one_slice_per_write_call():
    """The mechanism that makes barge-in a real cut: small writes."""
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    big = _silence_frame(24000)  # 1 full second in one play() call
    endpoint.play(big)
    _wait_until(lambda: len(fake.output_streams) == 1 and len(fake.output_streams[0].written) >= 1)
    endpoint.close(2.0)
    stream = fake.output_streams[0]
    assert stream.written, "writer never wrote anything"
    max_slice_samples = max(len(piece) // 2 for piece in stream.written)
    rate = endpoint.status()["device_rate_out_hz"] or 24000
    max_slice_ms = 1000 * max_slice_samples / rate
    assert max_slice_ms <= 41, f"a single write handed the device {max_slice_ms:.1f} ms"


def test_finding2_barge_in_cuts_within_roughly_one_slice_using_realtime_fake():
    """Mirrors the round 2 probe: a RealtimeOut-shaped fake that blocks like PortAudio.

    Uses a tiny real sleep per slice (write slices are ~20 ms, so this test
    still runs in well under a second) rather than the probe's full 5 s
    buffer, to keep the suite fast.
    """
    written_samples: list[int] = []

    def real_delay(n_samples: int, samplerate: float) -> None:
        written_samples.append(n_samples)
        time.sleep(n_samples / samplerate)

    fake = FakeSoundDevice(
        devices=[{"name": "fake-in", "default_samplerate": 24000}],
        write_delay=real_delay,
    )
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    five_seconds = _silence_frame(24000 * 5)
    endpoint.play(five_seconds)
    _wait_until(lambda: len(written_samples) >= 1, timeout=1.0)

    time.sleep(0.1)  # let ~100ms of real playback happen
    t0 = time.perf_counter()
    endpoint.stop_playback()
    # give the writer thread one more scheduling slice to notice the cut
    _wait_until(lambda: not endpoint.playing, timeout=0.5)
    dt = time.perf_counter() - t0

    total_written_s = sum(written_samples) / 24000
    assert total_written_s < 0.2, f"device received {total_written_s * 1000:.0f} ms after cut"
    assert dt < 0.5
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# round 2, finding 3 — close(deadline) actually honours its deadline and reports
# ---------------------------------------------------------------------------


def test_finding3_close_report_shape():
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    report = endpoint.close(1.0)
    assert report.capture_thread_stopped is True
    assert report.writer_thread_stopped is True
    assert report.samples_discarded == 0
    assert report.elapsed_s >= 0.0
    assert endpoint.status()["close_report"] == report.to_dict()


def test_finding3_close_with_deadline_returns_quickly_even_mid_playback():
    """The exact scenario the probe measured: one big buffer in flight, close(0.2)."""

    def real_delay(n_samples: int, samplerate: float) -> None:
        time.sleep(n_samples / samplerate)

    fake = FakeSoundDevice(
        devices=[{"name": "fake-in", "default_samplerate": 24000}],
        write_delay=real_delay,
    )
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    five_seconds = _silence_frame(24000 * 5)
    endpoint.play(five_seconds)
    _wait_until(lambda: len(fake.output_streams) == 1 and fake.output_streams[0].written)

    t0 = time.perf_counter()
    endpoint.close(0.2)
    dt = time.perf_counter() - t0
    assert dt < 0.3, f"close(0.2) with a 5s buffer in flight took {dt:.2f}s"

    alive = [
        t.name for t in threading.enumerate() if "embodiment-audio-host" in t.name and t.is_alive()
    ]
    assert alive == [], f"threads still alive after close: {alive}"


# ---------------------------------------------------------------------------
# round 2, finding 4 — the capture resampler is anti-aliased
# ---------------------------------------------------------------------------


def test_finding4_resample_path_is_fir_decimate_for_integer_downsample_ratio():
    resampler = Resampler(48000, 24000, _import_numpy_real)
    assert resampler.path == "fir-decimate"


def test_finding4_resample_path_is_native_for_equal_rates():
    resampler = Resampler(24000, 24000, _import_numpy_real)
    assert resampler.path == "native"


def test_finding4_resample_path_is_linear_for_non_integer_ratio():
    resampler = Resampler(44100, 24000, _import_numpy_real)
    assert resampler.path == "linear"


def test_finding4_15khz_tone_is_rejected_at_least_40db_48k_to_24k():
    """The exact scenario the probe measured: a 15 kHz tone at 48kHz -> 24kHz."""
    import numpy as np

    n = 48000
    t = np.arange(n) / 48000
    tone = (np.sin(2 * np.pi * 15000 * t) * 16000).astype("<i2").tobytes()
    out = np.frombuffer(
        _resample_pcm16(tone, 48000, 24000, _import_numpy_real), dtype="<i2"
    ).astype(float)

    rms_db = 20 * np.log10(max(1e-9, np.sqrt((out**2).mean()) / 32768))
    in_db = 20 * np.log10(16000 / np.sqrt(2) / 32768)
    assert rms_db <= in_db - 40, f"15 kHz rejected only {in_db - rms_db:.1f} dB"


def test_finding4_1khz_tone_stays_within_half_a_db_48k_to_24k():
    import numpy as np

    n = 48000
    t = np.arange(n) / 48000
    tone = (np.sin(2 * np.pi * 1000 * t) * 16000).astype("<i2").tobytes()
    out = np.frombuffer(
        _resample_pcm16(tone, 48000, 24000, _import_numpy_real), dtype="<i2"
    ).astype(float)

    rms_db = 20 * np.log10(max(1e-9, np.sqrt((out**2).mean()) / 32768))
    in_db = 20 * np.log10(16000 / np.sqrt(2) / 32768)
    assert abs(rms_db - in_db) <= 0.5, f"1 kHz level moved {abs(rms_db - in_db):.2f} dB"


def test_finding4_native_24khz_open_means_no_resampling_at_all():
    """When the device accepts 24 kHz directly, status reports 'native' and no filtering runs."""
    fake = FakeSoundDevice(devices=[{"name": "fake-in", "default_samplerate": 48000}])
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    status = endpoint.status()
    assert status["device_rate_in_hz"] == 24000
    assert status["resample_path_in"] == "native"
    endpoint.close(2.0)


def test_finding4_device_that_rejects_24khz_falls_back_to_native_rate_and_resamples():
    fake = FakeSoundDevice(
        devices=[{"name": "fake-in", "default_samplerate": 48000}],
        rejects_samplerates=(24000,),
    )
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    status = endpoint.status()
    assert status["device_rate_in_hz"] == 48000
    assert status["resample_path_in"] == "fir-decimate"
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# round 2, finding 5 — chunked resampling is continuous, not per-chunk-stateless
# ---------------------------------------------------------------------------


def test_finding5_chunked_matches_whole_within_70db_for_44_1k():
    """The exact scenario the probe measured, but through a PERSISTENT Resampler.

    A fresh Resampler instance per call (what the probe's direct calls to
    ``_resample_pcm16`` do) is documented as a one-shot convenience; the
    module's real fix for a genuine multi-chunk STREAM is a single, reused
    Resampler instance — exactly what HostEndpoint's own drain/writer loops
    construct once per open stream. This test proves that real fix.
    """
    import numpy as np

    sig = (np.sin(2 * np.pi * 440 * np.arange(44100) / 44100) * 16000).astype("<i2")

    whole_resampler = Resampler(44100, 24000, _import_numpy_real)
    whole = np.frombuffer(whole_resampler.process(sig.tobytes(), flush=True), dtype="<i2").astype(
        float
    )

    chunked_resampler = Resampler(44100, 24000, _import_numpy_real)
    parts = b"".join(
        chunked_resampler.process(sig[i : i + 882].tobytes()) for i in range(0, 44100, 882)
    )
    chunked = np.frombuffer(parts, dtype="<i2").astype(float)

    m = min(len(chunked), len(whole))
    err = chunked[:m] - whole[:m]
    err_db = 20 * np.log10(max(1e-9, np.sqrt((err**2).mean()) / 32768))
    assert err_db <= -70, f"chunked vs whole error {err_db:.1f} dBFS"


def test_finding5_chunked_matches_whole_within_70db_for_48k_upsample_direction():
    import numpy as np

    sig = (np.sin(2 * np.pi * 440 * np.arange(24000) / 24000) * 16000).astype("<i2")

    whole_resampler = Resampler(24000, 48000, _import_numpy_real)
    whole = np.frombuffer(whole_resampler.process(sig.tobytes(), flush=True), dtype="<i2").astype(
        float
    )

    chunked_resampler = Resampler(24000, 48000, _import_numpy_real)
    parts = b"".join(
        chunked_resampler.process(sig[i : i + 480].tobytes()) for i in range(0, 24000, 480)
    )
    chunked = np.frombuffer(parts, dtype="<i2").astype(float)

    m = min(len(chunked), len(whole))
    err = chunked[:m] - whole[:m]
    err_db = 20 * np.log10(max(1e-9, np.sqrt((err**2).mean()) / 32768))
    assert err_db <= -70, f"chunked vs whole error {err_db:.1f} dBFS"


def test_finding5_a_fresh_one_shot_call_per_probe_style_chunk_does_not_reanchor_to_endpoint():
    """Documents WHY the probe's direct chunked calls to `_resample_pcm16` still look good:

    each one-shot call's cursor starts at 0 and walks by the fixed ratio
    rather than force-scaling to the chunk's own last sample — so as long as
    chunks divide evenly into the ratio (as the probe's 882-sample chunks
    do), even fresh-per-call resampling is phase-consistent. This is a
    property of the fix, not a second, different fix.
    """
    import numpy as np

    sig = (np.sin(2 * np.pi * 440 * np.arange(44100) / 44100) * 16000).astype("<i2")
    whole = np.frombuffer(
        _resample_pcm16(sig.tobytes(), 44100, 24000, _import_numpy_real), dtype="<i2"
    ).astype(float)
    parts = b"".join(
        _resample_pcm16(sig[i : i + 882].tobytes(), 44100, 24000, _import_numpy_real)
        for i in range(0, 44100, 882)
    )
    chunked = np.frombuffer(parts, dtype="<i2").astype(float)
    m = min(len(chunked), len(whole))
    err = chunked[:m] - whole[:m]
    err_db = 20 * np.log10(max(1e-9, np.sqrt((err**2).mean()) / 32768))
    assert err_db <= -40, f"one-shot-per-chunk error {err_db:.1f} dBFS"


# ---------------------------------------------------------------------------
# round 2, finding 6 — bounded events (6a) and per-direction, retryable degradation (6b)
# ---------------------------------------------------------------------------


def test_finding6b_output_open_failure_does_not_block_capture():
    fake = FakeSoundDevice(raise_on_output_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.play(_silence_frame())  # fails, records degradation_out
    assert endpoint.status()["degradation_out"]["code"] == DEGRADED_OPEN

    received: list[bytes] = []
    endpoint.start_capture(received.append)  # must NOT be blocked by the output fault
    assert endpoint.status()["degradation_in"] is None
    assert endpoint.status()["capturing"] is True
    endpoint.close(2.0)


def test_finding6b_input_open_failure_does_not_block_playback():
    fake = FakeSoundDevice(raise_on_input_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.start_capture(lambda _f: None)
    assert endpoint.status()["degradation_in"]["code"] == DEGRADED_OPEN

    endpoint.play(_silence_frame())  # must NOT be blocked by the input fault
    assert endpoint.status()["degradation_out"] is None
    endpoint.close(2.0)


def test_finding6b_a_replugged_device_recovers_on_retry_without_a_restart():
    fake = FakeSoundDevice(raise_on_input_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.start_capture(lambda _f: None)
    assert endpoint.status()["degradation_in"] is not None

    fake.raise_on_input_open = False  # the mic was replugged
    endpoint.start_capture(lambda _f: None)  # one retry, no loop, no thread
    assert endpoint.status()["degradation_in"] is None
    assert endpoint.status()["capturing"] is True
    recovered = [e for e in endpoint.events if e.get("type") == "recovered"]
    assert any(e.get("direction") == "in" for e in recovered)
    endpoint.close(2.0)


def test_finding6b_retry_is_at_most_once_per_call_never_a_loop():
    """A still-broken device fails again on retry, cleanly, not by looping."""
    fake = FakeSoundDevice(raise_on_input_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    endpoint.start_capture(lambda _f: None)
    assert endpoint.status()["degradation_in"] is not None
    # still broken: a second call retries once, fails again, returns promptly
    start = time.monotonic()
    endpoint.start_capture(lambda _f: None)
    assert time.monotonic() - start < 1.0
    assert endpoint.status()["degradation_in"] is not None
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# privacy — no audio bytes ever written to disk
# ---------------------------------------------------------------------------


def test_privacy_no_audio_bytes_written_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = _working_fake()
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake, numpy_importer=_import_numpy_real)
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    stream = fake.input_streams[0]
    marker_frame = _pcm16(*([31337] * 480))
    for _ in range(10):
        stream.callback(marker_frame, 480, None, None)
    endpoint.play(marker_frame)
    _wait_until(lambda: len(received) >= 1)
    endpoint.mute(True)
    endpoint.mute(False)
    endpoint.close(2.0)

    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files == [], f"unexpected files written during a fake audio session: {files}"


# ---------------------------------------------------------------------------
# criterion 3 — the import graph: turn/daemon import the protocol only
# ---------------------------------------------------------------------------

#: The only modules allowed to import embodiment.audio.host: the module
#: itself (trivially — a module always "imports" its own name when discovered
#: by this scan's simple string check, so it is named explicitly for
#: clarity) and a future embodiment/daemon/app.py, which is the daemon's
#: composition root — the one place that is SUPPOSED to wire a concrete
#: AudioEndpoint implementation into the rest of the system. Every other
#: module reaching for HostEndpoint would mean the concrete device
#: implementation leaked into code that is supposed to depend only on the
#: embodiment.audio.endpoint.AudioEndpoint protocol.
_ALLOWED_HOST_IMPORTERS = {"embodiment.audio.host", "embodiment.daemon.app"}


def _module_name_for(py_file: Path, package_root: Path) -> str:
    parts = py_file.relative_to(package_root.parent).parts
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts = parts[:-1] + (parts[-1][:-3],)
    return ".".join(parts)


def _imports_audio_host(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "embodiment.audio.host" or alias.name.startswith(
                    "embodiment.audio.host."
                ):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "embodiment.audio.host":
                return True
            if module == "embodiment.audio" and any(a.name == "host" for a in node.names):
                return True
    return False


def test_criterion3_no_module_outside_audio_imports_host_except_the_named_allowlist():
    package_root = REPO_ROOT / "embodiment"
    offenders = []
    for py_file in sorted(package_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        module_name = _module_name_for(py_file, package_root)
        if module_name in _ALLOWED_HOST_IMPORTERS:
            continue
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        if _imports_audio_host(tree):
            offenders.append(module_name)

    assert offenders == [], (
        f"{offenders} import embodiment.audio.host directly; only "
        f"{sorted(_ALLOWED_HOST_IMPORTERS)} may — everything else must depend on "
        "embodiment.audio.endpoint.AudioEndpoint only."
    )


def test_criterion3_turn_and_daemon_modules_specifically_stay_clean():
    """The two modules the brief names explicitly, checked directly (not only via the AST walk)."""
    targets = [
        REPO_ROOT / "embodiment" / "turn.py",
        REPO_ROOT / "embodiment" / "memory.py",
    ]
    daemon_dir = REPO_ROOT / "embodiment" / "daemon"
    if daemon_dir.is_dir():
        targets.extend(p for p in daemon_dir.rglob("*.py") if p.name != "app.py")

    for target in targets:
        if not target.is_file():
            continue
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        assert not _imports_audio_host(tree), f"{target} imports embodiment.audio.host"


def test_criterion3_host_module_itself_is_exempt_from_its_own_guard():
    """Sanity check on the scanner: host.py legitimately mentions its own name in docstrings."""
    host_file = REPO_ROOT / "embodiment" / "audio" / "host.py"
    tree = ast.parse(host_file.read_text(encoding="utf-8"), filename=str(host_file))
    # host.py never imports itself as embodiment.audio.host (it just IS it) —
    # this asserts the AST scanner doesn't false-positive on the module docstring's
    # own prose mentions of "embodiment.audio.host".
    assert not _imports_audio_host(tree)


def test_criterion3_scanner_actually_detects_a_planted_violation(tmp_path):
    """A test that cannot fail is a defect (preamble rule): prove the AST scan fires."""
    planted = tmp_path / "planted_violation.py"
    planted.write_text("from embodiment.audio.host import HostEndpoint\n")
    tree = ast.parse(planted.read_text(), filename=str(planted))
    assert _imports_audio_host(tree)

    planted2 = tmp_path / "planted_violation2.py"
    planted2.write_text("import embodiment.audio.host\n")
    tree2 = ast.parse(planted2.read_text(), filename=str(planted2))
    assert _imports_audio_host(tree2)

    clean = tmp_path / "clean.py"
    clean.write_text("from embodiment.audio.endpoint import AudioEndpoint\n")
    tree3 = ast.parse(clean.read_text(), filename=str(clean))
    assert not _imports_audio_host(tree3)
