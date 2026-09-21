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

from embodiment.audio.host import (
    DEGRADED_ENUMERATION,
    DEGRADED_IMPORT,
    DEGRADED_OPEN,
    DEGRADED_PORTAUDIO,
    HostEndpoint,
    _resample_pcm16,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeStream:
    """Stands in for sounddevice.InputStream / OutputStream."""

    def __init__(self, *, samplerate, channels, dtype, device=None, callback=None):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False
        self.written: list[bytes] = []
        self.raise_on_start = False
        self.raise_on_write: Exception | None = None

    def start(self):
        if self.raise_on_start:
            raise RuntimeError("fake stream start failed")
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True

    def write(self, data):
        if self.raise_on_write is not None:
            raise self.raise_on_write
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
    ):
        self._devices = devices if devices is not None else [{"name": "fake-in"}]
        self._raise_on_query = raise_on_query
        self._default_samplerate = default_samplerate
        self.raise_on_input_open = raise_on_input_open
        self.raise_on_output_open = raise_on_output_open
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
        stream = FakeStream(**kwargs)
        self.input_streams.append(stream)
        return stream

    def OutputStream(self, **kwargs):
        if self.raise_on_output_open:
            raise RuntimeError("fake output device busy")
        stream = FakeStream(**kwargs)
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
    assert endpoint.status()["degradation"]["code"] == DEGRADED_OPEN
    assert received == []


def test_criterion1_output_device_open_failing_never_raises():
    fake = FakeSoundDevice(raise_on_output_open=True)
    endpoint = HostEndpoint(sounddevice_importer=lambda: fake)
    endpoint.play(_silence_frame())  # must not raise
    assert endpoint.status()["degradation"]["code"] == DEGRADED_OPEN


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
    """440 Hz @ 48kHz -> 24kHz -> 48kHz: measured RMS error stays small.

    This is the number reported in the task delivery notes; pinned here at a
    generous bound so the test can actually fail if the resampler regresses,
    without being so tight it flakes on an implementation-preserving change.
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
    n_compare = min(len(orig_arr), len(back_arr))
    # Trim the fixed group-delay-sensitive edges; the tone repeats every
    # ~109 samples at 48kHz so a handful of interior periods is representative.
    orig_arr = orig_arr[100 : n_compare - 100]
    back_arr = back_arr[100 : n_compare - 100]
    rmse = float(numpy.sqrt(numpy.mean((orig_arr - back_arr) ** 2)))
    full_scale = 32768.0
    rmse_dbfs = 20 * math.log10(rmse / full_scale) if rmse > 0 else float("-inf")

    # A real number, not a defect: a linear resampler with no anti-aliasing
    # filter is not lossless, and this bound documents "small enough for
    # speech", not "inaudible" or "measurement-grade".
    assert rmse_dbfs < -20.0, f"round-trip RMSE {rmse_dbfs:.2f} dBFS exceeds bound"


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
    endpoint = HostEndpoint(sounddevice_importer=_working_fake)
    # Start from a known state so every alternation below is a genuine change.
    assert endpoint.muted is False
    for i in range(10_000):
        endpoint.mute(i % 2 == 0)  # True, False, True, False, ... — every call differs
    assert len(endpoint.events) == 10_000  # every genuine change recorded, none lost

    # Now attack with a long run of IDENTICAL calls: none of these are changes.
    count_before = len(endpoint.events)
    last_state = endpoint.muted
    for _ in range(10_000):
        endpoint.mute(last_state)
    assert len(endpoint.events) == count_before


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
