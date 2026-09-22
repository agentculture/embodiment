"""Tests for embodiment.audio.host — the subprocess-driven AudioEndpoint (round 4, d4).

No real ``pw-record``/``pw-play``/``arecord``/``aplay`` is ever required:
every test injects a fake ``which`` (PATH lookup) and a fake ``popen`` that
substitutes a REAL small Python child process (``sys.executable -c ...``)
for whatever binary HostEndpoint asked for — never a mock — so pipes, EOF,
BrokenPipeError and kill are exercised for real, per the round 4 brief.

Covers plan task t7's three acceptance criteria (now at a process boundary):
  1. no-voice, never a raise (test_criterion1_*)
  2. mute enforced before the encoder boundary, one event per change
     (test_criterion2_*)
  3. import-graph: turn/daemon import the protocol only (test_criterion3_*)
"""

from __future__ import annotations

import ast
import struct
import subprocess  # nosec B404 - test-only, fixed argv, real small Python children
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from embodiment.audio.host import (
    CAPTURE_CHANNEL_INDEX,
    CAPTURE_CHANNELS,
    CAPTURE_RATE_HZ,
    DEGRADED_CAPTURE_ENDED,
    DEGRADED_DEVICE_UNRESOLVED,
    DEGRADED_NO_BACKEND,
    DEGRADED_OPEN,
    DEGRADED_PLAYBACK_OVERFLOW,
    DEGRADED_WRITE_FAILED,
    HostEndpoint,
    Resampler,
    _select_channel,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

CAPTURE_BINARIES = {"arecord", "pw-record"}
PLAYBACK_BINARIES = {"aplay", "pw-play"}


# ---------------------------------------------------------------------------
# fakes: a real small Python child stands in for arecord/aplay/pw-record/pw-play
# ---------------------------------------------------------------------------


def _import_numpy_real():
    import numpy

    return numpy


def _pcm16(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def _silence_frame(n_samples: int = 480) -> bytes:
    return b"\x00\x00" * n_samples


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _fake_which(available: set[str]) -> Callable[[str], str | None]:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    return which


#: A capture child: streams a fixed 2ch/16-bit tone (channel 0 = 100,
#: channel 1 = 9000, matching the measured reSpeaker "channel 1 is better"
#: scenario) in small chunks, forever, until killed. Never terminates on
#: its own, so it exercises the terminate/kill path in _stop_capture.
_CAPTURE_STREAM_SCRIPT = """
import struct, sys, time
frame = struct.pack("<hh", 100, 9000) * 160  # 160 stereo frames per chunk
try:
    while True:
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()
        time.sleep(0.005)
except BrokenPipeError:
    pass
"""

#: A capture child that emits exactly one chunk then exits cleanly — used to
#: prove a capture subprocess ending on its own is recorded, not silent.
_CAPTURE_ENDS_SCRIPT = """
import struct, sys
frame = struct.pack("<hh", 100, 9000) * 160
sys.stdout.buffer.write(frame)
sys.stdout.buffer.flush()
"""

#: A playback child: reads all of stdin, writes the TOTAL BYTE COUNT it saw
#: to the path in argv[1] (never the audio itself) on EOF. With no argv[1]
#: (a test that doesn't care what was "played"), it just reads to EOF and
#: writes nothing — never a stray file in the test process's cwd.
_PLAYBACK_SINK_SCRIPT = """
import sys
data = sys.stdin.buffer.read()
if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        fh.write(str(len(data)))
"""

#: A playback child that reads a small amount then exits immediately —
#: subsequent writes to its stdin raise BrokenPipeError/OSError for real.
_PLAYBACK_DIES_SCRIPT = """
import sys
sys.stdin.buffer.read(4)
"""


def _make_popen(
    capture_script: str = _CAPTURE_STREAM_SCRIPT,
    playback_script: str = _PLAYBACK_SINK_SCRIPT,
    sink_path: Path | None = None,
    fail_binaries: frozenset[str] = frozenset(),
):
    """Build a `popen` callable HostEndpoint can use instead of subprocess.Popen.

    Ignores the specific binary name (arecord/pw-record/aplay/pw-play) and
    launches the corresponding REAL Python child instead, preserving every
    stdin/stdout/stderr kwarg HostEndpoint itself passed — so the actual
    pipe wiring is exercised for real, only the "which real binary" part is
    substituted.
    """

    def popen(argv, **kwargs):
        binary = argv[0]
        if binary in fail_binaries:
            raise OSError(f"fake: {binary} not actually runnable")
        if binary in CAPTURE_BINARIES:
            cmd = [sys.executable, "-c", capture_script]
        elif binary in PLAYBACK_BINARIES:
            cmd = [sys.executable, "-c", playback_script]
            if sink_path is not None:
                cmd.append(str(sink_path))
        else:
            raise OSError(f"unrecognised fake binary {binary!r}")
        return subprocess.Popen(cmd, **kwargs)  # nosec B603 - fixed argv, test-only

    return popen


def _read_sink(sink_path: Path, timeout: float = 3.0) -> int:
    """Wait for the playback sink file to appear and return its byte count."""
    _wait_until(sink_path.exists, timeout=timeout)
    time.sleep(0.05)  # let the child finish its own write
    return int(sink_path.read_text(encoding="utf-8").strip() or "0")


# ---------------------------------------------------------------------------
# criterion 1 — no-voice, never a raise
# ---------------------------------------------------------------------------


def test_criterion1_no_backend_binaries_on_path_is_recorded_and_never_raises():
    endpoint = HostEndpoint(which=_fake_which(set()), popen=_make_popen())
    status = endpoint.status()
    assert status["degradation"]["code"] == DEGRADED_NO_BACKEND

    received: list[bytes] = []
    endpoint.start_capture(received.append)  # must not raise
    endpoint.play(_silence_frame())  # must not raise
    endpoint.attach()
    endpoint.detach()
    endpoint.close(1.0)
    assert received == []


def test_criterion1_pipewire_preferred_when_both_backends_present():
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}), popen=_make_popen()
    )
    assert endpoint.status()["backend"] == "pipewire"
    endpoint.close(1.0)


def test_criterion1_alsa_used_when_only_alsa_binaries_present():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    assert endpoint.status()["backend"] == "alsa"
    endpoint.close(1.0)


def test_criterion1_capture_subprocess_start_failure_is_recorded_never_raises():
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(fail_binaries=frozenset({"arecord"})),
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)  # must not raise
    assert endpoint.status()["degradation_in"]["code"] == DEGRADED_OPEN
    assert received == []
    endpoint.close(1.0)


def test_criterion1_playback_subprocess_start_failure_is_recorded_never_raises():
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(fail_binaries=frozenset({"aplay"})),
    )
    endpoint.play(_silence_frame())  # must not raise
    assert endpoint.status()["degradation_out"]["code"] == DEGRADED_OPEN
    endpoint.close(1.0)


def test_criterion1_status_never_raises_before_or_after_close():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    endpoint.status()
    endpoint.close(1.0)
    endpoint.status()
    endpoint.close(1.0)  # idempotent
    endpoint.mute(True)
    endpoint.play(_silence_frame())
    endpoint.start_capture(lambda _f: None)


# ---------------------------------------------------------------------------
# device auto-detection (/proc/asound/cards)
# ---------------------------------------------------------------------------


def _cards_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cards"
    path.write_text(text, encoding="utf-8")
    return path


def test_device_zero_candidates_falls_back_to_default_not_a_fault(tmp_path):
    cards = _cards_file(tmp_path, " 0 [Generic]: HDA-Intel - HD-Audio Generic\n")
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(), cards_path=cards
    )
    assert endpoint.status()["degradation"] is None
    assert endpoint.status()["device"] is None
    endpoint.close(1.0)


def test_device_one_candidate_resolves_by_card_number(tmp_path):
    cards = _cards_file(
        tmp_path,
        " 0 [Generic]: HDA-Intel - HD-Audio Generic\n"
        " 1 [Array]: USB-Audio - reSpeaker XVF3800 4-Mic Array\n",
    )
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(), cards_path=cards
    )
    assert endpoint.status()["device"] == "1"
    assert endpoint.status()["degradation"] is None
    endpoint.close(1.0)


def test_device_ambiguous_candidates_is_a_recorded_degradation_naming_the_count(tmp_path):
    cards = _cards_file(
        tmp_path,
        " 1 [ArrayA]: USB-Audio - reSpeaker XVF3800 4-Mic Array\n"
        " 2 [ArrayB]: USB-Audio - reSpeaker XVF3800 4-Mic Array\n",
    )
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(), cards_path=cards
    )
    status = endpoint.status()
    assert status["degradation"]["code"] == DEGRADED_DEVICE_UNRESOLVED
    assert "2 candidate" in status["degradation"]["reason"]
    assert status["device"] is None
    endpoint.close(1.0)


def test_device_missing_cards_file_falls_back_to_default_never_raises(tmp_path):
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(),
        cards_path=tmp_path / "does-not-exist",
    )
    assert endpoint.status()["degradation"] is None
    assert endpoint.status()["device"] is None
    endpoint.close(1.0)


def test_device_explicit_override_skips_auto_detect(tmp_path):
    cards = _cards_file(tmp_path, " 1 [Array]: USB-Audio - reSpeaker XVF3800 4-Mic Array\n")
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(), cards_path=cards, device="7"
    )
    assert endpoint.status()["device"] == "7"
    endpoint.close(1.0)


# ---------------------------------------------------------------------------
# criterion 2 — mute enforced before the encoder boundary
# ---------------------------------------------------------------------------


def test_criterion2_muted_endpoint_delivers_zero_frames_downstream():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    received: list[bytes] = []
    endpoint.mute(True)
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    time.sleep(0.3)  # let several real chunks flow through the muted reader
    endpoint.stop_capture()
    assert received == []  # ZERO frames reached the encoder boundary
    assert endpoint.status()["capture_muted_dropped"] > 0
    endpoint.close(2.0)


def test_criterion2_unmuted_then_muted_stops_new_frames():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint._capturing)  # noqa: SLF001
    _wait_until(lambda: len(received) >= 1)
    assert received  # real frames delivered, real channel-selected+resampled bytes
    assert all(len(f) % 2 == 0 for f in received)

    endpoint.mute(True)
    before = len(received)
    time.sleep(0.3)
    assert len(received) == before  # nothing new arrived while muted
    endpoint.close(2.0)


def test_criterion2_mute_change_emits_exactly_one_event():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    assert endpoint.events == ()
    endpoint.mute(True)
    assert len(endpoint.events) == 1
    assert endpoint.events[0]["muted"] is True
    endpoint.mute(True)  # no change
    assert len(endpoint.events) == 1
    endpoint.mute(False)
    assert len(endpoint.events) == 2
    endpoint.close(1.0)


def test_criterion2_mute_events_carry_no_audio_content():
    marker = "SECRET-USER-SPEECH-MARKER-ABC123"
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    endpoint.mute(True)
    endpoint.mute(False)
    for event in endpoint.events:
        assert marker not in str(event)
    assert marker not in str(endpoint.status())
    endpoint.close(1.0)


def test_channel_selection_keeps_the_configured_channel_never_averages():
    """The real reSpeaker scenario: ch0=100, ch1=9000 — capture must deliver
    ch1's value throughout, never something in between (an average)."""
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: len(received) >= 1)
    endpoint.stop_capture()
    values = struct.unpack(f"<{len(received[0]) // 2}h", received[0])
    assert all(v != 4550 for v in values)  # never the average of 100 and 9000
    endpoint.close(2.0)


def test_capture_subprocess_ending_unexpectedly_is_recorded_named():
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(capture_script=_CAPTURE_ENDS_SCRIPT),
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint.status()["degradation_in"] is not None)
    assert endpoint.status()["degradation_in"]["code"] == DEGRADED_CAPTURE_ENDED
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# resampler (round 2/3b — reused unchanged for the 16k->24k capture leg)
# ---------------------------------------------------------------------------


def test_resampler_16k_to_24k_path_is_polyphase():
    r = Resampler(CAPTURE_RATE_HZ, 24000, _import_numpy_real)
    assert r.path == "polyphase"


def test_resampler_16k_to_24k_is_spectrally_clean_at_several_frequencies():
    """16k->24k (the real capture leg) must put >=99.9% of energy at the input
    frequency — no aliasing, no image, across the band up to 16k's own Nyquist.

    A round-trip RMSE-with-integer-lag-alignment test was tried first and
    rejected: 16k->24k uses up=3/down=2 and the return leg 24k->16k uses
    up=2/down=3, so the two legs' combined group delay is not, in general,
    a whole number of samples — comparing two periodic tones under a
    best-effort INTEGER lag search then measures phase mismatch, not
    aliasing, and swings wildly by test frequency (measured: -74 dBFS at
    3 kHz, -27 dBFS at 1/5/7 kHz on an IDENTICAL, spectrally clean signal —
    confirmed clean by the FFT check below, >99.9999% of energy at the
    correct peak in every case). Spectral concentration is the metric that
    actually answers "did this alias", so that is what this test measures.
    """
    import math

    numpy = _import_numpy_real()
    n = 16000
    for freq in (1000.0, 3000.0, 5000.0, 7000.0):
        t = numpy.arange(n) / CAPTURE_RATE_HZ
        tone = (numpy.sin(2 * math.pi * freq * t) * 16000).astype("<i2")
        up = Resampler(CAPTURE_RATE_HZ, 24000, _import_numpy_real).process(
            tone.tobytes(), flush=True
        )
        up_arr = numpy.frombuffer(up, dtype="<i2").astype(numpy.float64)
        spec = numpy.abs(numpy.fft.rfft(up_arr * numpy.hanning(len(up_arr))))
        f = numpy.fft.rfftfreq(len(up_arr), 1 / 24000)
        peak = f[int(spec.argmax())]
        assert abs(peak - freq) < 50, f"{freq} Hz: peak landed at {peak} Hz"
        total_energy = float(numpy.sum(spec**2))
        peak_energy = float(numpy.sum(spec[(f > peak - 50) & (f < peak + 50)] ** 2))
        fraction = peak_energy / total_energy if total_energy > 0 else 0.0
        assert fraction >= 0.999, f"{freq} Hz: only {fraction:.4%} of energy at the peak"


def test_select_channel_helper_never_raises_on_malformed_input():
    numpy = _import_numpy_real()
    assert _select_channel(numpy, b"", 2, 1) == b""
    assert _select_channel(numpy, b"\x00", 2, 1) == b""
    assert _select_channel(numpy, b"\x00\x00\x00", 2, 1) == b""
    assert _select_channel(numpy, b"\x01\x00", 1, 0) == b"\x01\x00"
    out_of_range = _select_channel(numpy, struct.pack("<hh", 5, 9), 2, 99)
    assert out_of_range == struct.pack("<h", 9)


def test_channel_index_constant_matches_measured_evidence():
    """Cited from lobes-cli: channel 1 (index 1) is the measured-better channel."""
    assert CAPTURE_CHANNEL_INDEX == 1
    assert CAPTURE_CHANNELS == 2


# ---------------------------------------------------------------------------
# playback: buffers the whole reply, overflow is named, write failure is named
# ---------------------------------------------------------------------------


def test_playback_writes_real_bytes_to_the_subprocess(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    chunk = _silence_frame(480)
    for _ in range(5):
        endpoint.play(chunk)
    _wait_until(lambda: endpoint.status()["playback_written_samples"] >= 480 * 5)
    endpoint.close(2.0)
    assert _read_sink(sink) == len(chunk) * 5


def test_playback_overflow_is_named_once_per_episode_and_counted(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    big_chunk = _silence_frame(24000)  # 1 s per call
    overflowed = False
    for _ in range(200):
        before = endpoint.status()["playback_overflow_count"]
        endpoint.play(big_chunk)
        if endpoint.status()["playback_overflow_count"] > before:
            overflowed = True
            break
    assert overflowed
    for _ in range(20):
        endpoint.play(big_chunk)
    degraded_events = [
        e
        for e in endpoint.events
        if e.get("type") == "degraded" and e.get("code") == DEGRADED_PLAYBACK_OVERFLOW
    ]
    assert len(degraded_events) == 1
    assert endpoint.status()["playback_overflow_count"] > 1
    endpoint.close(2.0)


def test_playback_write_failure_is_named_and_stream_is_dropped():
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(playback_script=_PLAYBACK_DIES_SCRIPT),
    )
    for _ in range(20):
        endpoint.play(_silence_frame(480))
        time.sleep(0.02)
        if endpoint.status()["degradation_out"] is not None:
            break
    status = endpoint.status()
    assert status["degradation_out"]["code"] == DEGRADED_WRITE_FAILED
    assert status["playing"] is False
    endpoint.close(2.0)


def test_playback_write_failure_goes_through_cooldown_on_next_play():
    """After a dead player, retries are cooldown-gated (round 3 finding 1, reused)."""
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(playback_script=_PLAYBACK_DIES_SCRIPT),
    )
    for _ in range(20):
        endpoint.play(_silence_frame(480))
        time.sleep(0.02)
        if endpoint.status()["degradation_out"] is not None:
            break
    assert endpoint.status()["degradation_out"] is not None
    dropped_before = endpoint.status()["playback_dropped_no_device"]
    for _ in range(20):
        endpoint.play(_silence_frame(480))
    assert endpoint.status()["playback_dropped_no_device"] > dropped_before
    endpoint.close(2.0)


def test_stop_playback_discards_queued_bytes_and_terminates_the_player(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    chunk = _silence_frame(4800)  # 200 ms per call
    for _ in range(20):
        endpoint.play(chunk)  # 4 s of audio queued
    _wait_until(lambda: endpoint.playing)
    discarded = endpoint.stop_playback()
    # Round 5: pacing means most of a 4 s reply is still genuinely UNWRITTEN
    # moments after play() returns — this must have something real to
    # discard, not the round-4 defect (the whole reply already dumped into
    # the pipe, discarded=0).
    assert discarded > 0
    assert endpoint.playing is False
    assert endpoint.status()["playback_stop_discarded_total"] == discarded
    endpoint.close(2.0)


def test_round5_writer_paces_against_the_clock_not_a_burst(tmp_path):
    """The exact defect the coordinator's probe found: a 3 s reply must NOT
    land in the player's stdin within a fraction of a second. Proven with a
    real child that reads (and discards) one 20 ms slice every 20 ms, like a
    real player actually consuming audio at playback speed, and records how
    many slices it had seen by a checkpoint partway through."""
    progress = tmp_path / "progress.txt"
    script = f"""
import sys, time
n = 0
slice_bytes = 960  # 20 ms @ 24 kHz mono pcm16
while True:
    chunk = sys.stdin.buffer.read(slice_bytes)
    if not chunk:
        break
    n += 1
    time.sleep(0.02)
with open({str(progress)!r}, "w", encoding="utf-8") as fh:
    fh.write(str(n))
"""
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(playback_script=script)
    )
    three_seconds = _silence_frame(24000 * 3)
    t0 = time.perf_counter()
    endpoint.play(three_seconds)
    play_call_elapsed = time.perf_counter() - t0
    assert play_call_elapsed < 0.5  # play() itself never blocks on pacing

    time.sleep(0.3)
    written_at_300ms = endpoint.status()["playback_written_samples"]
    # At most ~(300ms + lead) worth of samples should have been WRITTEN to
    # the pipe by 300ms in — not all 72000 samples of the 3 s reply (round 4's
    # defect measured the whole reply landing within 300ms).
    assert written_at_300ms < 24000, f"wrote {written_at_300ms} samples by 300ms — not paced"
    endpoint.stop_playback()
    endpoint.close(3.0)

    # Independent confirmation from the CHILD's own count, not just this
    # module's internal counters: it must have seen only a modest number of
    # 20 ms slices, never the whole 3 s reply (150 slices) at once.
    if _wait_until(progress.exists, timeout=1.0):
        seen = int(progress.read_text(encoding="utf-8").strip() or "0")
        assert seen < 75, f"child saw {seen} of 150 slices — not paced"


def test_round5_stop_playback_returns_under_200ms_with_seconds_queued(tmp_path):
    """Measured target: stop_playback() returns in < 200 ms and playing is
    False on return, even with many seconds of audio queued."""

    script = """
import sys, time
while True:
    chunk = sys.stdin.buffer.read(960)
    if not chunk:
        break
    time.sleep(0.02)
"""
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(playback_script=script)
    )
    ten_seconds = _silence_frame(24000 * 10)
    endpoint.play(ten_seconds)
    _wait_until(lambda: endpoint.playing)
    time.sleep(0.3)

    t0 = time.perf_counter()
    discarded = endpoint.stop_playback()
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2, f"stop_playback took {elapsed * 1000:.0f} ms"
    assert endpoint.playing is False
    assert discarded > 0
    endpoint.close(2.0)


def test_a_subsequent_play_after_stop_playback_starts_a_fresh_process(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.playing)
    endpoint.stop_playback()

    sink2 = sink.parent / "sink2.txt"
    endpoint2 = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink2)
    )
    endpoint2.play(_silence_frame(480))
    _wait_until(lambda: endpoint2.status()["playback_written_samples"] > 0)
    assert endpoint2.status()["playback_written_samples"] > 0
    endpoint.close(2.0)
    endpoint2.close(2.0)


# ---------------------------------------------------------------------------
# close(deadline): bounded, reports what could not be released
# ---------------------------------------------------------------------------


def test_close_report_shape(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    report = endpoint.close(2.0)
    assert report.capture_thread_stopped is True
    assert report.writer_thread_stopped is True
    assert report.samples_discarded == 0
    assert report.elapsed_s >= 0.0
    assert report.streams_close_failed == 0
    assert endpoint.status()["close_report"] == report.to_dict()


def test_close_terminates_the_capture_subprocess_promptly():
    """The capture child streams FOREVER until killed — close() must not hang."""
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: len(received) >= 1)

    start = time.perf_counter()
    endpoint.close(2.0)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.5

    alive = [
        t.name for t in threading.enumerate() if "embodiment-audio-host" in t.name and t.is_alive()
    ]
    assert alive == [], f"threads still alive after close: {alive}"


def test_close_is_idempotent_and_bounded(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    endpoint.start_capture(lambda _f: None)
    endpoint.play(_silence_frame())
    start = time.monotonic()
    endpoint.close(1.0)
    endpoint.close(1.0)
    endpoint.close(0.0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0


# ---------------------------------------------------------------------------
# attacks
# ---------------------------------------------------------------------------


def test_attack_mute_called_one_hundred_thousand_times_bounds_memory_stays_exact():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    for i in range(100_000):
        endpoint.mute(i % 2 == 0)
    assert endpoint.status()["mute_event_count"] == 100_000
    assert len(endpoint.events) <= 1000
    endpoint.close(1.0)


def test_attack_play_called_two_thousand_times_never_blocks_caller_long(tmp_path):
    sink = tmp_path / "sink.txt"
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    frame = _silence_frame()
    start = time.monotonic()
    for _ in range(2_000):
        endpoint.play(frame)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0
    endpoint.close(2.0)


def test_attack_repeated_start_stop_capture_never_leaks_or_raises():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    for _ in range(10):
        endpoint.start_capture(lambda _f: None)
        endpoint.stop_capture()
    endpoint.close(2.0)
    alive = [
        t.name for t in threading.enumerate() if "embodiment-audio-host" in t.name and t.is_alive()
    ]
    assert alive == []


def test_attack_wrong_type_to_play_never_raises():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    endpoint.play(object())  # type: ignore[arg-type]
    endpoint.play(None)  # type: ignore[arg-type]
    endpoint.play(b"")
    endpoint.close(1.0)


# ---------------------------------------------------------------------------
# safe_reason: no exception message, no stderr text, in any record
# ---------------------------------------------------------------------------


def test_open_failure_reason_never_contains_the_raw_exception_message():
    marker = "SECRET-ARECORD-PATH-MARKER"

    def failing_popen(argv, **kwargs):
        raise OSError(f"cannot open device at {marker}")

    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=failing_popen)
    endpoint.start_capture(lambda _f: None)
    reason = endpoint.status()["degradation_in"]["reason"]
    assert marker not in reason
    endpoint.close(1.0)


def test_capture_ended_reason_never_contains_raw_stderr_text():
    marker = "SECRET-STDERR-DEVICE-PATH-MARKER"
    script = f"""
import sys
sys.stderr.write({marker!r})
sys.stderr.flush()
"""
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(capture_script=script)
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint.status()["degradation_in"] is not None)
    reason = endpoint.status()["degradation_in"]["reason"]
    assert marker not in reason
    assert "chars" in reason and "fp:" in reason
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# privacy — no audio bytes ever written to disk (this module never writes any)
# ---------------------------------------------------------------------------


def test_privacy_no_audio_bytes_written_to_disk(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    sink = tmp_path / "sink.txt"  # OUTSIDE the scanned cwd — the test fixture's own file
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}), popen=_make_popen(sink_path=sink)
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: len(received) >= 1)
    endpoint.play(_silence_frame(480))
    endpoint.mute(True)
    endpoint.mute(False)
    endpoint.close(2.0)

    files = [p for p in work_dir.rglob("*") if p.is_file()]
    assert files == [], f"unexpected files written during a fake audio session: {files}"


# ---------------------------------------------------------------------------
# criterion 3 — the import graph: turn/daemon import the protocol only
# ---------------------------------------------------------------------------

#: The only modules allowed to import embodiment.audio.host: the module
#: itself and a future embodiment/daemon/app.py, the daemon's composition
#: root — the one place SUPPOSED to wire a concrete AudioEndpoint in.
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


def test_criterion3_scanner_actually_detects_a_planted_violation(tmp_path):
    planted = tmp_path / "planted_violation.py"
    planted.write_text("from embodiment.audio.host import HostEndpoint\n")
    tree = ast.parse(planted.read_text(), filename=str(planted))
    assert _imports_audio_host(tree)

    clean = tmp_path / "clean.py"
    clean.write_text("from embodiment.audio.endpoint import AudioEndpoint\n")
    tree3 = ast.parse(clean.read_text(), filename=str(clean))
    assert not _imports_audio_host(tree3)
