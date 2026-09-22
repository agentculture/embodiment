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
import json
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
    DEGRADED_CAPTURE_ENDED,
    DEGRADED_DEVICE_MISMATCH,
    DEGRADED_DEVICE_UNRESOLVED,
    DEGRADED_NO_BACKEND,
    DEGRADED_OPEN,
    DEGRADED_PIPEWIRE_UNAVAILABLE,
    DEGRADED_PLAYBACK_OVERFLOW,
    DEGRADED_PLAYBACK_QUIET,
    DEGRADED_WRITE_FAILED,
    PLAYBACK_VOLUME_FLOOR,
    PLAYER_LATENCY_MS,
    HostEndpoint,
    _build_playback_argv,
    _pw_find_stream_node,
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


# ---------------------------------------------------------------------------
# round 7: a fake `pw-dump` graph — a device (array) with a Sink+Source, a
# SECOND device (the HDMI monitor's sink) that is NOT the array, and the
# ability to synthesize a Stream node + Link for a just-spawned capture/
# playback child, either linked correctly or (to prove the mismatch is
# caught) linked to the wrong node, or not linked at all.
# ---------------------------------------------------------------------------

PW_SINK_ID = 48
PW_SOURCE_ID = 49
PW_HDMI_SINK_ID = 62
PW_ARRAY_DEVICE_ID = 1
PW_HDMI_DEVICE_ID = 2


def _pw_device_nodes() -> list[dict]:
    return [
        {
            "id": PW_SINK_ID,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "alsa_output.usb-Seeed_XVF3800.stereo",
                    "node.description": "reSpeaker XVF3800 4-Mic Array",
                    "device.id": PW_ARRAY_DEVICE_ID,
                }
            },
        },
        {
            "id": PW_SOURCE_ID,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Source",
                    "node.name": "alsa_input.usb-Seeed_XVF3800.stereo",
                    "node.description": "reSpeaker XVF3800 4-Mic Array",
                    "device.id": PW_ARRAY_DEVICE_ID,
                }
            },
        },
        {
            "id": PW_HDMI_SINK_ID,
            "type": "PipeWire:Interface:Node",
            "info": {
                "props": {
                    "media.class": "Audio/Sink",
                    "node.name": "alsa_output.pci-0000_00_01.0.hdmi-stereo",
                    "node.description": "NVIDIA HDMI",
                    "device.id": PW_HDMI_DEVICE_ID,
                }
            },
        },
    ]


class FakePwDumpState:
    """Controls what the fake `pw-dump` binary reports.

    ``resolvable=False`` simulates `pw-dump` being missing/unparsable.
    ``default_link_playback``/``default_link_capture`` (a node id, or
    ``None`` for "never links") decide what a NEWLY spawned pw-play/
    pw-record child is reported as linked to — default: the array's own
    sink/source, so every EXISTING test (which never configures this) keeps
    exercising the "verified" path without change.
    """

    def __init__(
        self,
        *,
        resolvable: bool = True,
        default_link_playback: int | None = PW_SINK_ID,
        default_link_capture: int | None = PW_SOURCE_ID,
    ):
        self.resolvable = resolvable
        self.default_link_playback = default_link_playback
        self.default_link_capture = default_link_capture
        self._streams: list[dict] = []
        self._next_id = 5000

    def note_spawned(self, pid: int, *, playback: bool) -> None:
        media_class = "Stream/Output/Audio" if playback else "Stream/Input/Audio"
        linked_to = self.default_link_playback if playback else self.default_link_capture
        self._streams.append({"pid": pid, "media_class": media_class, "linked_to": linked_to})

    def dump_json(self) -> bytes | None:
        if not self.resolvable:
            return None
        objs = list(_pw_device_nodes())
        for stream in self._streams:
            stream_id = self._next_id
            self._next_id += 1
            objs.append(
                {
                    "id": stream_id,
                    "type": "PipeWire:Interface:Node",
                    "info": {
                        "props": {
                            "media.class": stream["media_class"],
                            "application.process.id": stream["pid"],
                            "application.name": (
                                "pw-play"
                                if stream["media_class"] == "Stream/Output/Audio"
                                else "pw-record"
                            ),
                            "node.name": f"stream-{stream_id}",
                        }
                    },
                }
            )
            if stream["linked_to"] is not None:
                link_id = self._next_id
                self._next_id += 1
                if stream["media_class"] == "Stream/Output/Audio":
                    out_id, in_id = stream_id, stream["linked_to"]
                else:
                    out_id, in_id = stream["linked_to"], stream_id
                objs.append(
                    {
                        "id": link_id,
                        "type": "PipeWire:Interface:Link",
                        "info": {"output-node-id": out_id, "input-node-id": in_id},
                    }
                )
        return json.dumps(objs).encode("utf-8")


def _make_popen(
    capture_script: str = _CAPTURE_STREAM_SCRIPT,
    playback_script: str = _PLAYBACK_SINK_SCRIPT,
    sink_path: Path | None = None,
    fail_binaries: frozenset[str] = frozenset(),
    pw_state: FakePwDumpState | None = None,
    wpctl_output: str | None = None,
    wpctl_fail: bool = False,
    wpctl_argv: list[list[str]] | None = None,
    playback_argv: list[list[str]] | None = None,
):
    """Build a `popen` callable HostEndpoint can use instead of subprocess.Popen.

    Ignores the specific binary name (arecord/pw-record/aplay/pw-play) and
    launches the corresponding REAL Python child instead, preserving every
    stdin/stdout/stderr kwarg HostEndpoint itself passed — so the actual
    pipe wiring is exercised for real, only the "which real binary" part is
    substituted. `pw-dump` is answered from *pw_state* (a fresh, default
    "everything resolves to the array" state when not given), also via a
    real child that just prints the fixture JSON — never a mock.

    Round 8: `wpctl` is answered from *wpctl_output* (defaults to a loud,
    unmuted "Volume: 1.00\\n" so no EXISTING pipewire test starts seeing a
    surprise quiet-degradation event); *wpctl_fail* makes the fake binary
    itself unresolvable/erroring. *wpctl_argv*/*playback_argv*, when given,
    collect every argv this fake popen was called with for that binary, so a
    test can assert on the flags HostEndpoint actually built.
    """
    state = pw_state if pw_state is not None else FakePwDumpState()
    volume_text = "Volume: 1.00\n" if wpctl_output is None else wpctl_output

    def popen(argv, **kwargs):
        binary = argv[0]
        if binary == "pw-dump":
            payload = state.dump_json()
            if payload is None:
                script = "import sys; sys.exit(1)"
            else:
                script = f"import sys; sys.stdout.buffer.write({payload!r})"
            return subprocess.Popen(  # nosec B603 - fixed argv, test-only
                [sys.executable, "-c", script], **kwargs
            )
        if binary == "wpctl":
            if wpctl_argv is not None:
                wpctl_argv.append(list(argv))
            if wpctl_fail:
                raise OSError("fake: wpctl not actually runnable")
            script = f"import sys; sys.stdout.write({volume_text!r})"
            return subprocess.Popen(  # nosec B603 - fixed argv, test-only
                [sys.executable, "-c", script], **kwargs
            )
        if binary in fail_binaries:
            raise OSError(f"fake: {binary} not actually runnable")
        if binary in CAPTURE_BINARIES:
            cmd = [sys.executable, "-c", capture_script]
        elif binary in PLAYBACK_BINARIES:
            if playback_argv is not None:
                playback_argv.append(list(argv))
            cmd = [sys.executable, "-c", playback_script]
            if sink_path is not None:
                cmd.append(str(sink_path))
        else:
            raise OSError(f"unrecognised fake binary {binary!r}")
        proc = subprocess.Popen(cmd, **kwargs)  # nosec B603 - fixed argv, test-only
        state.note_spawned(proc.pid, playback=binary in PLAYBACK_BINARIES)
        return proc

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


# ---------------------------------------------------------------------------
# round 7 (BLOCKER): pipewire target resolution + verify-don't-trust
# ---------------------------------------------------------------------------


def test_round7_playback_targets_the_arrays_node_name_not_a_card_index():
    """The exact defect: --target must be a resolved pipewire node.name,
    never an ALSA card index (an unresolvable index silently falls back to
    the system default sink — how a reply ended up on the HDMI monitor)."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(),
        device="XVF3800",
    )
    assert endpoint.status()["backend"] == "pipewire"
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    assert endpoint.status()["playback_target_verified"] is True
    assert endpoint.status()["degradation_out"] is None
    endpoint.close(2.0)


def test_round7_auto_detected_alsa_card_number_is_never_used_as_a_pipewire_needle(tmp_path):
    """An auto-detected card index (e.g. "1") is a terrible pipewire
    node.name substring — it can match an UNRELATED node by accident (a
    digit inside some other device's name/path). Auto-detect must always
    fall back to the fixed "XVF3800" needle for pipewire, never the ALSA
    card number it found. Uses a cards fixture so this does not depend on
    the real box's own /proc/asound/cards content."""
    cards = _cards_file(tmp_path, " 1 [Array]: USB-Audio - reSpeaker XVF3800 4-Mic Array\n")
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(),
        cards_path=cards,
    )
    assert endpoint._device == "1"  # noqa: SLF001 - auto-detected card number
    assert endpoint.status()["degradation"] is None  # resolved via "XVF3800", not "1"
    endpoint.close(2.0)


def test_round7_resolution_ignores_which_sink_is_the_pipewire_default():
    """wpctl status on the real box: the HDMI is the DEFAULT sink, the array
    is not. Resolution must match by NAME, never by default-ness."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}), popen=_make_popen()
    )
    assert endpoint.status()["degradation"] is None
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    assert endpoint.status()["playback_target_verified"] is True
    endpoint.close(2.0)


def test_round7_playback_mismatch_is_named_counted_and_kills_the_stream():
    """A fixture where the stream links to the HDMI sink instead of the
    array — the exact live scenario — must be caught, not trusted."""
    state = FakePwDumpState(default_link_playback=PW_HDMI_SINK_ID)
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)

    status = endpoint.status()
    assert status["playback_target_verified"] is False
    assert status["degradation_out"]["code"] == DEGRADED_DEVICE_MISMATCH
    assert status["playback_target_mismatch_count"] == 1
    assert status["playing"] is False  # killed at once, never left sounding
    endpoint.close(2.0)


def test_round7_playback_never_links_within_the_verify_budget_is_a_mismatch():
    """A link that never appears (a race, or a genuinely failed route) must
    time out to a mismatch, not hang or silently pass."""
    state = FakePwDumpState(default_link_playback=None)
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    t0 = time.perf_counter()
    endpoint.play(_silence_frame(480))
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0  # bounded by the verify budget, not indefinite
    assert endpoint.status()["playback_target_verified"] is False
    assert endpoint.status()["degradation_out"]["code"] == DEGRADED_DEVICE_MISMATCH
    endpoint.close(2.0)


def test_round7_capture_mismatch_is_named_and_counted():
    state = FakePwDumpState(default_link_capture=PW_HDMI_SINK_ID)  # any wrong id
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint.status()["capture_target_verified"] is not None)

    status = endpoint.status()
    assert status["capture_target_verified"] is False
    assert status["degradation_in"]["code"] == DEGRADED_DEVICE_MISMATCH
    assert status["capture_target_mismatch_count"] == 1
    endpoint.close(2.0)


def test_round7_capture_verified_on_the_happy_path():
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}), popen=_make_popen()
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: endpoint.status()["capture_target_verified"] is not None)
    assert endpoint.status()["capture_target_verified"] is True
    assert endpoint.status()["degradation_in"] is None
    endpoint.close(2.0)


def test_round7_pw_dump_missing_falls_back_to_alsa_when_available():
    state = FakePwDumpState(resolvable=False)
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    assert endpoint.status()["backend"] == "alsa"
    assert endpoint.status()["degradation"] is None
    degraded = [e for e in endpoint.events if e.get("code") == DEGRADED_PIPEWIRE_UNAVAILABLE]
    assert len(degraded) == 1
    endpoint.close(2.0)


def test_round7_pw_dump_missing_with_no_alsa_fallback_is_a_construction_fault():
    state = FakePwDumpState(resolvable=False)
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play"}),  # no arecord/aplay
        popen=_make_popen(pw_state=state),
    )
    assert endpoint.status()["degradation"]["code"] == DEGRADED_PIPEWIRE_UNAVAILABLE
    received: list[bytes] = []
    endpoint.start_capture(received.append)  # never raises
    endpoint.play(_silence_frame())  # never raises
    endpoint.close(1.0)


def test_round7_resolution_matches_by_device_serial():
    """The operator's configured device may be a serial rather than a name."""

    def dump_with_serial() -> list[dict]:
        objs = _pw_device_nodes()
        objs[0]["info"]["props"]["device.serial"] = "usb-Seeed_Studio_reSpeaker_XVF3800_114993"
        objs[1]["info"]["props"]["device.serial"] = "usb-Seeed_Studio_reSpeaker_XVF3800_114993"
        return objs

    class SerialState(FakePwDumpState):
        def dump_json(self):
            if not self.resolvable:
                return None
            objs = dump_with_serial()
            for stream in self._streams:
                stream_id = self._next_id
                self._next_id += 1
                objs.append(
                    {
                        "id": stream_id,
                        "type": "PipeWire:Interface:Node",
                        "info": {
                            "props": {
                                "media.class": stream["media_class"],
                                "application.process.id": stream["pid"],
                                "node.name": f"stream-{stream_id}",
                            }
                        },
                    }
                )
                if stream["linked_to"] is not None:
                    link_id = self._next_id
                    self._next_id += 1
                    if stream["media_class"] == "Stream/Output/Audio":
                        out_id, in_id = stream_id, stream["linked_to"]
                    else:
                        out_id, in_id = stream["linked_to"], stream_id
                    objs.append(
                        {
                            "id": link_id,
                            "type": "PipeWire:Interface:Link",
                            "info": {"output-node-id": out_id, "input-node-id": in_id},
                        }
                    )
            return json.dumps(objs).encode("utf-8")

    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=SerialState()),
        device="114993",
    )
    assert endpoint.status()["degradation"] is None
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    assert endpoint.status()["playback_target_verified"] is True
    endpoint.close(2.0)


def test_round7_split_device_sink_and_source_different_devices_is_unresolved():
    def broken_device_nodes() -> list[dict]:
        objs = _pw_device_nodes()
        objs[1]["info"]["props"]["device.id"] = PW_HDMI_DEVICE_ID  # source claims the OTHER device
        return objs

    class SplitState(FakePwDumpState):
        def dump_json(self):
            return json.dumps(broken_device_nodes()).encode("utf-8")

    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=SplitState()),
    )
    assert endpoint.status()["degradation"]["code"] == DEGRADED_DEVICE_UNRESOLVED
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# round 9 finding 1: identifying OUR child's stream node via client.id ->
# Client pipewire.sec.pid, not just an application.name match — measured
# live, a bare pw-play sets NEITHER pid field on its own Stream node, only
# on the Client object that owns it.
# ---------------------------------------------------------------------------


class ClientPidState(FakePwDumpState):
    """Streams carry NO ``application.process.id`` of their own (the
    production shape on the real box) — only a ``client.id`` pointing at a
    ``PipeWire:Interface:Client`` object that carries the real pid via
    ``pipewire.sec.pid``. *foreign* optionally adds a SECOND, unrelated
    pw-play Stream node ahead of ours in the dump (to prove ordering alone
    cannot pick the wrong one): ``"resolvable"`` gives it its own genuine
    (wrong) client pid, ``"no_pid"`` gives it no pid anywhere at all — and in
    that same mode, OUR own stream is ALSO given no resolvable pid anywhere,
    so neither candidate can be picked by anything but a (now-ambiguous)
    name match.
    """

    def __init__(self, *, foreign: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.foreign = foreign

    def dump_json(self) -> bytes | None:
        if not self.resolvable:
            return None
        objs = list(_pw_device_nodes())
        next_id = self._next_id

        if self.foreign is not None:
            foreign_stream_id = next_id
            next_id += 1
            foreign_client_id = None
            if self.foreign == "resolvable":
                foreign_client_id = next_id
                next_id += 1
                objs.append(
                    {
                        "id": foreign_client_id,
                        "type": "PipeWire:Interface:Client",
                        "info": {
                            "props": {
                                "pipewire.sec.pid": 999999,  # unrelated to any real pid
                                "application.name": "pw-play",
                            }
                        },
                    }
                )
            objs.append(
                {
                    "id": foreign_stream_id,
                    "type": "PipeWire:Interface:Node",
                    "info": {
                        "props": {
                            "media.class": "Stream/Output/Audio",
                            "application.name": "pw-play",
                            "node.name": f"foreign-{foreign_stream_id}",
                            **({"client.id": foreign_client_id} if foreign_client_id else {}),
                        }
                    },
                }
            )

        for stream in self._streams:
            stream_id = next_id
            next_id += 1
            client_id = None
            if self.foreign != "no_pid":
                client_id = next_id
                next_id += 1
                objs.append(
                    {
                        "id": client_id,
                        "type": "PipeWire:Interface:Client",
                        "info": {
                            "props": {
                                "pipewire.sec.pid": stream["pid"],
                                "application.name": (
                                    "pw-play"
                                    if stream["media_class"] == "Stream/Output/Audio"
                                    else "pw-record"
                                ),
                            }
                        },
                    }
                )
            objs.append(
                {
                    "id": stream_id,
                    "type": "PipeWire:Interface:Node",
                    "info": {
                        "props": {
                            "media.class": stream["media_class"],
                            "application.name": (
                                "pw-play"
                                if stream["media_class"] == "Stream/Output/Audio"
                                else "pw-record"
                            ),
                            "node.name": f"stream-{stream_id}",
                            **({"client.id": client_id} if client_id is not None else {}),
                            # deliberately NO application.process.id here —
                            # the production shape measured live.
                        }
                    },
                }
            )
            if stream["linked_to"] is not None:
                link_id = next_id
                next_id += 1
                if stream["media_class"] == "Stream/Output/Audio":
                    out_id, in_id = stream_id, stream["linked_to"]
                else:
                    out_id, in_id = stream["linked_to"], stream_id
                objs.append(
                    {
                        "id": link_id,
                        "type": "PipeWire:Interface:Link",
                        "info": {"output-node-id": out_id, "input-node-id": in_id},
                    }
                )
        self._next_id = next_id
        return json.dumps(objs).encode("utf-8")


def test_round9_client_id_resolves_the_pid_when_the_node_omits_it():
    """A node with NO application.process.id of its own is still identified
    correctly via its client.id -> the Client object's pipewire.sec.pid."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=ClientPidState()),
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    status = endpoint.status()
    assert status["playback_target_verified"] is True
    assert status["playback_target_ambiguous_count"] == 0
    endpoint.close(2.0)


def test_round9_two_pwplay_streams_only_the_client_pid_path_picks_ours():
    """A SECOND, unrelated pw-play stream (its own resolvable-but-wrong
    client pid) sits ahead of ours in the dump. If the old name-match
    fallback were still in play it would return the FIRST pw-play node
    (the foreign one) and could confirm the wrong stream; the client-pid
    path must pick OURS regardless of order."""
    state = ClientPidState(foreign="resolvable")
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    status = endpoint.status()
    assert status["playback_target_verified"] is True
    assert status["playback_target_ambiguous_count"] == 0
    endpoint.close(2.0)


def test_round9_two_pwplay_streams_with_no_pid_anywhere_is_ambiguous_never_verified():
    """Two pw-play streams, NEITHER carrying a resolvable pid anywhere (not
    on the node, not on a Client object) -> the name match yields two
    candidates, which must be reported ambiguous and NEVER verified=True.
    Only OUR tracked subprocess is killed as a mismatch; the unrelated
    foreign stream (which this module never even names) is never touched."""
    state = ClientPidState(foreign="no_pid")
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    status = endpoint.status()
    assert status["playback_target_verified"] is False
    assert status["playback_target_ambiguous_count"] == 1
    assert status["degradation_out"]["code"] == DEGRADED_DEVICE_MISMATCH
    assert status["playing"] is False  # our own stream killed, never left sounding
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# round 9 finding 3: a wedged pw-dump must not stall verification past its
# own budget — each pw-dump call inside _verify_pipewire_link is bounded by
# whatever remains of the 300 ms verify window, not the full 5 s default.
# ---------------------------------------------------------------------------


def test_round9_verify_pw_dump_calls_are_bounded_by_the_remaining_verify_budget():
    """A `pw-dump` that would itself take longer than what remains of the
    verify budget must not be allowed to run that long — each call inside
    `_verify_pipewire_link` is capped at the REMAINING budget, not the full
    5 s `_PW_DUMP_TIMEOUT_S` default."""
    from embodiment.audio.host import _PW_DUMP_TIMEOUT_S

    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}), popen=_make_popen()
    )
    seen_timeouts: list[float] = []
    real_run_pw_dump = endpoint._run_pw_dump  # noqa: SLF001 - test-only introspection

    def spy(timeout=_PW_DUMP_TIMEOUT_S):
        seen_timeouts.append(timeout)
        return real_run_pw_dump(timeout=timeout)

    endpoint._run_pw_dump = spy  # noqa: SLF001 - test-only monkeypatch
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    assert seen_timeouts, "expected _verify_pipewire_link to call _run_pw_dump at least once"
    assert all(t <= 0.3 + 1e-9 for t in seen_timeouts), seen_timeouts
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# round 9 finding 2: a wpctl timeout whose post-kill cleanup ALSO raises must
# still count as exactly ONE fault on playback_volume_unparsable_count.
# ---------------------------------------------------------------------------


class _TimeoutTwiceProc:
    """A fake Popen whose `communicate()` raises TimeoutExpired every call —
    the FIRST time (the real wpctl call) and again on the post-kill cleanup
    `communicate()` — the exact double-failure shape round 9 finding 2 is
    about: ONE genuine fault (the timeout), whose cleanup ALSO raises."""

    def __init__(self, argv):
        self._argv = argv
        self.returncode: int | None = None

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd=self._argv, timeout=timeout or 0)

    def kill(self):
        pass


def test_round9_wpctl_timeout_increments_unparsable_count_exactly_once():
    def popen(argv, **kwargs):
        if argv[0] == "wpctl":
            return _TimeoutTwiceProc(argv)
        return _make_popen()(argv, **kwargs)

    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}), popen=popen
    )
    status = endpoint.status()
    assert status["playback_volume"] is None
    assert status["playback_volume_unparsable_count"] == 1
    assert status["callback_errors"] == 1  # the cleanup-after-kill's own raise, recorded once
    endpoint.close(2.0)


# ---------------------------------------------------------------------------
# round 10: a name match must never fire on an UNSETTLED dump — the
# coordinator's live probe caught a foreign pw-play (already running,
# targeting the HDMI sink) fooling the FIRST poll, before our own stream
# node had even appeared, non-deterministically (fixtures mirror the shape
# of scratchpad/pwdump_live.json: a Client object + a Stream node whose
# pid is reachable only via that Client's client.id).
# ---------------------------------------------------------------------------


def _client_obj(obj_id: int, pid: int, name: str = "pw-play") -> dict:
    return {
        "id": obj_id,
        "type": "PipeWire:Interface:Client",
        "info": {"props": {"pipewire.sec.pid": pid, "application.name": name}},
    }


def _stream_node_obj(
    obj_id: int,
    *,
    client_id: int | None = None,
    name: str = "pw-play",
    media_class: str = "Stream/Output/Audio",
) -> dict:
    props: dict[str, object] = {
        "media.class": media_class,
        "application.name": name,
        "node.name": f"n{obj_id}",
    }
    if client_id is not None:
        props["client.id"] = client_id
    return {"id": obj_id, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def test_round10_foreign_only_dump_is_not_found_never_the_foreign_node():
    """Our node has not appeared yet; a foreign pw-play's Client pid HAS —
    the dump reports a resolvable Client pid at all, so a name match must
    not fire and the foreign node must never be returned as ours."""
    foreign_client_id, foreign_node_id = 100, 101
    dump = [
        _client_obj(foreign_client_id, 999999),
        _stream_node_obj(foreign_node_id, client_id=foreign_client_id),
    ]
    node, ambiguous = _pw_find_stream_node(dump, "Stream/Output/Audio", pid=12345)
    assert node is None
    assert ambiguous is False


def test_round10_our_node_appears_one_poll_later_and_is_picked():
    """The very next dump — same foreign stream, now alongside ours — must
    resolve to OUR node, never the foreign one, regardless of list order."""
    our_pid = 12345
    foreign_client_id, foreign_node_id = 100, 101
    our_client_id, our_node_id = 200, 201
    dump = [
        _client_obj(foreign_client_id, 999999),
        _stream_node_obj(foreign_node_id, client_id=foreign_client_id),
        _client_obj(our_client_id, our_pid),
        _stream_node_obj(our_node_id, client_id=our_client_id),
    ]
    node, ambiguous = _pw_find_stream_node(dump, "Stream/Output/Audio", pid=our_pid)
    assert node is not None
    assert node["id"] == our_node_id
    assert ambiguous is False


class TransientForeignThenOursState(FakePwDumpState):
    """The FIRST `pw-dump` call reports only a foreign pw-play (its own
    resolvable, unrelated Client pid) with no sign of our own stream yet;
    every later call also includes ours, correctly linked to the resolved
    sink — reproducing the exact race the coordinator's live probe hit."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._calls = 0
        self._foreign_client_id = 9001
        self._foreign_node_id = 9002

    def dump_json(self) -> bytes | None:
        if not self.resolvable:
            return None
        self._calls += 1
        objs = list(_pw_device_nodes())
        objs.append(_client_obj(self._foreign_client_id, 999999))
        objs.append(_stream_node_obj(self._foreign_node_id, client_id=self._foreign_client_id))
        if self._calls == 1:
            return json.dumps(objs).encode("utf-8")

        next_id = self._next_id
        for stream in self._streams:
            stream_id = next_id
            next_id += 1
            client_id = next_id
            next_id += 1
            objs.append(_client_obj(client_id, stream["pid"]))
            objs.append(
                _stream_node_obj(stream_id, client_id=client_id, media_class=stream["media_class"])
            )
            if stream["linked_to"] is not None:
                link_id = next_id
                next_id += 1
                if stream["media_class"] == "Stream/Output/Audio":
                    out_id, in_id = stream_id, stream["linked_to"]
                else:
                    out_id, in_id = stream["linked_to"], stream_id
                objs.append(
                    {
                        "id": link_id,
                        "type": "PipeWire:Interface:Link",
                        "info": {"output-node-id": out_id, "input-node-id": in_id},
                    }
                )
        self._next_id = next_id
        return json.dumps(objs).encode("utf-8")


def test_round10_verify_pipewire_link_survives_a_transient_foreign_only_dump():
    """_verify_pipewire_link polls PAST the first dump (foreign stream only,
    ours not yet visible) and settles True once ours appears — never
    treating the foreign stream as ours, never killing our own player over
    a stream that was never ours."""
    state = TransientForeignThenOursState()
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(pw_state=state),
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: endpoint.status()["playback_target_verified"] is not None)
    status = endpoint.status()
    assert status["playback_target_verified"] is True
    assert status["degradation_out"] is None
    assert status["playback_target_ambiguous_count"] == 0
    assert status["playback_target_mismatch_count"] == 0
    endpoint.close(2.0)


def test_round10_old_behaviour_would_have_failed_a_and_c():
    """Documents the regression this round fixes: round 9's own algorithm
    (name match built from ALL pw-play/pw-record nodes, unfiltered by
    whether their pid resolved to someone else) DOES return the foreign
    node as a match on a foreign-only dump — the exact live failure mode.
    Reverting the round 10 filter locally reproduces (a)'s and (c)'s
    failure; this test pins what that reverted algorithm returns, so the
    fix above must differ from it."""
    foreign_client_id, foreign_node_id = 100, 101
    dump = [
        _client_obj(foreign_client_id, 999999),
        _stream_node_obj(foreign_node_id, client_id=foreign_client_id),
    ]

    def old_algorithm(dump, media_class, pid):
        candidates = [
            {"id": o["id"], "props": o["info"]["props"]}
            for o in dump
            if o.get("type") == "PipeWire:Interface:Node"
            and o["info"]["props"].get("media.class") == media_class
        ]
        client_pids = {}
        for o in dump:
            if o.get("type") != "PipeWire:Interface:Client":
                continue
            props = o["info"]["props"]
            raw = props.get("pipewire.sec.pid", props.get("application.process.id"))
            if raw is not None:
                client_pids[o["id"]] = int(raw)
        for node in candidates:
            client_pid = client_pids.get(node["props"].get("client.id"))
            if client_pid is not None and client_pid == pid:
                return node, False
        name_matches = [
            node
            for node in candidates
            if str(node["props"].get("application.name") or "") in ("pw-play", "pw-record")
        ]
        if len(name_matches) == 1:
            return name_matches[0], False
        if len(name_matches) > 1:
            return None, True
        return None, False

    old_node, old_ambiguous = old_algorithm(dump, "Stream/Output/Audio", pid=12345)
    assert old_node is not None and old_node["id"] == foreign_node_id  # the bug, pinned
    assert old_ambiguous is False

    fixed_node, fixed_ambiguous = _pw_find_stream_node(dump, "Stream/Output/Audio", pid=12345)
    assert fixed_node is None  # round 10: never the foreign node
    assert fixed_ambiguous is False


# ---------------------------------------------------------------------------
# round 8: pipewire sink volume, read (never set) via wpctl
# ---------------------------------------------------------------------------


def test_round8_quiet_sink_volume_is_reported_and_recorded():
    """0.41 (the operator's own measured reading) -> degradation + the value."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output="Volume: 0.41\n"),
    )
    status = endpoint.status()
    assert status["playback_volume"] == 0.41
    assert status["playback_muted_by_system"] is False
    assert status["playback_quiet_count"] == 1
    quiet = [e for e in endpoint.events if e.get("code") == DEGRADED_PLAYBACK_QUIET]
    assert len(quiet) == 1
    assert quiet[0]["direction"] == "out"
    endpoint.close(2.0)


def test_round8_loud_sink_volume_is_reported_with_no_degradation():
    """1.0 -> no degradation."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output="Volume: 1.00\n"),
    )
    status = endpoint.status()
    assert status["playback_volume"] == 1.00
    assert status["playback_muted_by_system"] is False
    assert status["playback_quiet_count"] == 0
    assert [e for e in endpoint.events if e.get("code") == DEGRADED_PLAYBACK_QUIET] == []
    endpoint.close(2.0)


def test_round8_volume_at_the_floor_boundary_is_not_quiet_but_below_it_is():
    at_floor = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output=f"Volume: {PLAYBACK_VOLUME_FLOOR:.2f}\n"),
    )
    assert at_floor.status()["playback_quiet_count"] == 0
    at_floor.close(1.0)

    below_floor = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output=f"Volume: {PLAYBACK_VOLUME_FLOOR - 0.01:.2f}\n"),
    )
    assert below_floor.status()["playback_quiet_count"] == 1
    below_floor.close(1.0)


def test_round8_system_muted_sink_is_flagged_even_when_loud():
    """ "[MUTED]" -> flag (and a degradation: a muted sink is inaudible
    regardless of its stored volume number)."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output="Volume: 1.00 [MUTED]\n"),
    )
    status = endpoint.status()
    assert status["playback_volume"] == 1.00
    assert status["playback_muted_by_system"] is True
    assert status["playback_quiet_count"] == 1
    endpoint.close(2.0)


def test_round8_unparsable_wpctl_output_is_none_and_counted():
    """garbage -> None + counter, never raises."""
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output="not even close to the expected shape\n"),
    )
    status = endpoint.status()
    assert status["playback_volume"] is None
    assert status["playback_muted_by_system"] is None
    assert status["playback_volume_unparsable_count"] == 1
    assert status["playback_quiet_count"] == 0  # unmeasurable is not the same as quiet
    endpoint.close(2.0)


def test_round8_wpctl_binary_missing_is_none_and_counted_never_raises():
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_fail=True),
    )
    status = endpoint.status()
    assert status["playback_volume"] is None
    assert status["playback_volume_unparsable_count"] == 1
    endpoint.close(2.0)


def test_round8_no_wpctl_read_on_the_alsa_backend():
    """The alsa backend has no wpctl equivalent wired up this round — stays
    an honest None rather than a half-implemented amixer guess."""
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    status = endpoint.status()
    assert status["playback_volume"] is None
    assert status["playback_muted_by_system"] is None
    endpoint.close(1.0)


def test_round8_wpctl_is_queried_against_the_resolved_sink_id():
    calls: list[list[str]] = []
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(wpctl_output="Volume: 0.41\n", wpctl_argv=calls),
    )
    assert len(calls) == 1
    assert calls[0][:2] == ["wpctl", "get-volume"]
    assert calls[0][2] == str(PW_SINK_ID)
    endpoint.close(1.0)


# ---------------------------------------------------------------------------
# round 8 addendum: the player's own buffer, requested explicitly
# ---------------------------------------------------------------------------


def test_round8_addendum_pipewire_playback_argv_carries_the_latency_flag():
    argv = _build_playback_argv("pipewire", "the-sink-node", 24000, 1)
    assert "--latency" in argv
    assert argv[argv.index("--latency") + 1] == f"{PLAYER_LATENCY_MS}ms"


def test_round8_addendum_alsa_playback_argv_carries_the_buffer_time_flag():
    argv = _build_playback_argv("alsa", "1", 24000, 1)
    assert "--buffer-time" in argv
    assert argv[argv.index("--buffer-time") + 1] == str(PLAYER_LATENCY_MS * 1000)


def test_round8_addendum_playback_latency_ms_is_exposed_on_status():
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    assert endpoint.status()["playback_latency_ms"] == PLAYER_LATENCY_MS
    endpoint.close(1.0)


def test_round8_addendum_actual_playback_spawn_uses_the_latency_flag():
    calls: list[list[str]] = []
    endpoint = HostEndpoint(
        which=_fake_which({"pw-record", "pw-play", "arecord", "aplay"}),
        popen=_make_popen(playback_argv=calls),
    )
    endpoint.play(_silence_frame(480))
    _wait_until(lambda: len(calls) >= 1)
    assert "--latency" in calls[0]
    endpoint.close(2.0)


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


def test_round7b_finding1_concurrent_mute_from_the_same_state_emits_one_event():
    """Regression FENCE, not a repro: the reviewer hammered the old
    check-then-act mute() 3000 times through a barrier and got 0 duplicates
    (the window was a few bytecodes under the GIL) — this test passes
    before AND after the fix. Its job is to stay green once mute() is
    locked, catching a future regression that reintroduces the race, not to
    demonstrate the race exists."""
    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    n_threads = 8
    barrier = threading.Barrier(n_threads)

    def flip_to_true():
        barrier.wait(timeout=5.0)
        endpoint.mute(True)

    threads = [threading.Thread(target=flip_to_true) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    mute_events = [e for e in endpoint.events if e.get("type") == "mute"]
    assert len(mute_events) == 1, f"expected exactly one mute event, got {len(mute_events)}"
    assert endpoint.status()["mute_event_count"] == 1
    assert endpoint.muted is True
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


def test_round6_sample_rate_reports_the_native_capture_rate_not_24k():
    """Decision 15 (issue #85): HostEndpoint delivers capture frames at their
    NATIVE 16 kHz — no resample to embodiment.audio.endpoint.SAMPLE_RATE_HZ."""
    from embodiment.audio.host import CAPTURE_RATE_HZ

    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    assert endpoint.sample_rate == CAPTURE_RATE_HZ == 16000
    endpoint.close(1.0)


def test_round6_capture_frames_are_never_resampled():
    """Delivered frame byte count must be EXACTLY read_chunk_bytes/channels —
    the deterministic result of channel selection alone. A 16k->24k resample
    (round 4/5's deleted capture leg) would have scaled this by 3/2 instead."""
    from embodiment.audio.host import _CAPTURE_CHUNK_BYTES

    endpoint = HostEndpoint(which=_fake_which({"arecord", "aplay"}), popen=_make_popen())
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: len(received) >= 1)
    endpoint.stop_capture()
    expected = _CAPTURE_CHUNK_BYTES // CAPTURE_CHANNELS
    assert len(received[0]) == expected, f"got {len(received[0])} bytes, expected {expected}"
    endpoint.close(2.0)


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


_CAPTURE_IGNORES_SIGTERM_SCRIPT = """
import signal, struct, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
frame = struct.pack("<hh", 100, 9000) * 160
while True:
    sys.stdout.buffer.write(frame)
    sys.stdout.buffer.flush()
    time.sleep(0.005)
"""


def test_round7b_finding2_unreaped_child_is_tracked_and_reaped_later():
    """A child that ignores SIGTERM forces the SIGKILL escalation; with a
    ~0 s wait budget the OS cannot confirm the death in time, so the Popen
    must land in children_unreaped rather than being silently dropped (a
    zombie until this process happens to reap it). A LATER close() with a
    real budget, after the (already SIGKILLed) child has had time to
    actually exit, must reap it and report 0.
    """
    endpoint = HostEndpoint(
        which=_fake_which({"arecord", "aplay"}),
        popen=_make_popen(capture_script=_CAPTURE_IGNORES_SIGTERM_SCRIPT),
    )
    received: list[bytes] = []
    endpoint.start_capture(received.append)
    _wait_until(lambda: len(received) >= 1)

    report = endpoint.close(0.0)  # ~0s budget: SIGKILL fires, confirmation cannot land in time
    assert report.children_unreaped >= 1
    assert endpoint.status()["close_report"]["children_unreaped"] == report.children_unreaped

    # The child is already SIGKILLed (unignorable) — give the OS a moment
    # to actually finish tearing it down, then a real close() must reap it.
    time.sleep(0.3)
    report2 = endpoint.close(2.0)
    assert report2.children_unreaped == 0


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

#: The only module UNCONDITIONALLY allowed to import embodiment.audio.host:
#: the module itself. `embodiment.daemon.app` gets a NARROWER exception —
#: see `_find_host_import_violations_outside_main` — never a blanket
#: allow-list entry for the whole file.
_ALLOWED_HOST_IMPORTERS = {"embodiment.audio.host"}

#: The daemon's composition root, and the ONLY function in it allowed to
#: import a concrete AudioEndpoint (round 7b finding 3). t15's `main()`
#: wires `HostEndpoint` there; `DaemonApp` itself is built against the
#: `AudioEndpoint` protocol only. Named explicitly rather than "app.py may
#: import host.py anywhere in the file", which would also have passed if
#: `DaemonApp` imported it at module scope.
_DAEMON_APP_MODULE = "embodiment.daemon.app"
_DAEMON_APP_ALLOWED_FUNCTION = "main"


def _module_name_for(py_file: Path, package_root: Path) -> str:
    parts = py_file.relative_to(package_root.parent).parts
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts = parts[:-1] + (parts[-1][:-3],)
    return ".".join(parts)


def _is_host_import_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(
            a.name == "embodiment.audio.host" or a.name.startswith("embodiment.audio.host.")
            for a in node.names
        )
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module == "embodiment.audio.host":
            return True
        if module == "embodiment.audio" and any(a.name == "host" for a in node.names):
            return True
    return False


def _imports_audio_host(tree: ast.AST) -> bool:
    return any(_is_host_import_node(node) for node in ast.walk(tree))


def _find_host_import_violations_outside_main(tree: ast.AST) -> list[str]:
    """Every `embodiment.audio.host` import whose NEAREST enclosing
    FunctionDef/AsyncFunctionDef is not literally named "main" (module
    scope, a class body, or any other function/nested function all count as
    outside). Returns a line-numbered description per violation, or `[]`.
    """
    violations: list[str] = []

    def walk(node: ast.AST, enclosing_function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            child_enclosing = enclosing_function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_enclosing = child.name
            if _is_host_import_node(child) and enclosing_function != _DAEMON_APP_ALLOWED_FUNCTION:
                where = enclosing_function or "module scope"
                violations.append(f"line {getattr(child, 'lineno', '?')} (in {where})")
            walk(child, child_enclosing)

    walk(tree, None)
    return violations


def test_criterion3_no_module_outside_audio_imports_host_except_the_named_allowlist():
    """embodiment.daemon.app is the daemon's composition root — the one
    place SUPPOSED to wire a concrete AudioEndpoint in — but ONLY inside
    `main()` (round 7b finding 3): t15's `main()` constructs `HostEndpoint`
    there, while `DaemonApp` itself depends on the `AudioEndpoint` protocol
    only. A module-scope (or any other function's) host import in app.py
    still fails this test, unlike a blanket per-file allow-list entry."""
    package_root = REPO_ROOT / "embodiment"
    offenders = []
    for py_file in sorted(package_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        module_name = _module_name_for(py_file, package_root)
        if module_name in _ALLOWED_HOST_IMPORTERS:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        if module_name == _DAEMON_APP_MODULE:
            if _find_host_import_violations_outside_main(tree):
                offenders.append(module_name)
            continue
        if _imports_audio_host(tree):
            offenders.append(module_name)

    assert offenders == [], (
        f"{offenders} import embodiment.audio.host directly; only "
        f"{sorted(_ALLOWED_HOST_IMPORTERS)} unconditionally, and "
        f"{_DAEMON_APP_MODULE} inside {_DAEMON_APP_ALLOWED_FUNCTION}() only, may — "
        "everything else must depend on embodiment.audio.endpoint.AudioEndpoint only."
    )


def test_criterion3_daemon_app_host_import_allowed_only_inside_main():
    """Round 7b finding 3, proven with planted fixtures since daemon/app.py
    does not exist in this worktree (t15's own branch)."""
    inside_main = (
        "def main():\n    from embodiment.audio.host import HostEndpoint\n    return HostEndpoint\n"
    )
    assert _find_host_import_violations_outside_main(ast.parse(inside_main)) == []

    module_scope = "from embodiment.audio.host import HostEndpoint\n\ndef main():\n    pass\n"
    assert _find_host_import_violations_outside_main(ast.parse(module_scope))

    other_function = (
        "def setup():\n"
        "    from embodiment.audio.host import HostEndpoint\n"
        "    return HostEndpoint\n"
        "\n"
        "def main():\n"
        "    pass\n"
    )
    assert _find_host_import_violations_outside_main(ast.parse(other_function))

    nested_inside_main_but_not_named_main = (
        "def main():\n"
        "    def _load():\n"
        "        from embodiment.audio.host import HostEndpoint\n"
        "        return HostEndpoint\n"
        "    return _load()\n"
    )
    assert _find_host_import_violations_outside_main(
        ast.parse(nested_inside_main_but_not_named_main)
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
