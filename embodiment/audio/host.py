"""The host loudspeaker/microphone :class:`~embodiment.audio.endpoint.AudioEndpoint`.

Plan task ``t7``, round 4. **Deviation d4 (operator decision):**
``sounddevice``/PortAudio is WITHDRAWN. This module now drives the
microphone and speaker as SUBPROCESSES — ``pw-record``/``pw-play``
(pipewire) or ``arecord``/``aplay`` (ALSA) — the same shape
``../lobes-cli/scripts/realtime-he-accept.py`` and
``../shabbos-goy/shabbos_goy/audio/pipewire.py`` already run in production
against the SAME hardware (the Seeed reSpeaker XVF3800 4-mic array). This is
a rewrite, not a patch: rounds 1-3's sounddevice-specific mechanics (open
retries, ``Raw*Stream``, per-direction stream objects) are gone, but their
LESSONS carry over unchanged and are re-applied against a process boundary
instead of a PortAudio one — named below wherever a round 1-3 finding
recurs in the new shape.

Cited, not imported (both sibling repos are read-only references; this
package still does not depend on either): ``select_channel`` (channel
selection never averages), the capture/playback ``argv`` shapes, and the
mic/speaker device-pairing discipline are the SAME functions, same
reasoning, as ``lobes-cli/scripts/realtime-he-accept.py``'s functions of the
same name (``select_channel``, ``build_capture_argv``,
``build_playback_argv``, ``validate_device_pair``/``DeviceMismatchError``).

The reference shape this module matches
------------------------------------------
Both sibling projects already answered "how does this exact device talk to
Linux": 2-channel capture at a FIXED 16 kHz (the reSpeaker refuses 24 kHz and
48 kHz on both directions — measured, round 3b), channel 1 selected (never a
downmix — the array's own measured evidence is that channel 1 carries less
echo residual and scores the lower WER), and playback at 24 kHz mono with
ALSA's ``plughw:`` layer (or pipewire) doing the down-conversion to whatever
the hardware wants. This module keeps that shape exactly:

- **Capture** always requests :data:`CAPTURE_RATE_HZ` (16000) /
  :data:`CAPTURE_CHANNELS` (2) from the subprocess, regardless of what the
  underlying device natively wants — ``plughw:``/pipewire do that
  conversion, the same way they already do for both sibling projects. What
  THIS module still owns is the channel selection
  (:func:`_select_channel`, cited from lobes-cli, reused unchanged).
- **Playback** always writes :data:`PLAYBACK_RATE_HZ` (24000, the fixed
  contract rate) / mono to the subprocess's stdin — ALSA/pipewire resample
  DOWN as needed, so this module has never owned a 24k -> 16k output
  resampler (round 3b built one; round 4 deleted it outright rather than
  leave a resampler nobody's code path reached).

**Round 6 — decision 15 (operator, issue #85), superseding the original
"resample to 24 kHz in Python" instruction, together with d4:** the ears now
send the device's NATIVE 16 kHz straight to the gateway — ``../shabbos-goy``
already runs this exact array this way, daily, validated by the operator —
rather than this module resampling capture up to the package's historical
24 kHz wire contract. Concretely: :meth:`HostEndpoint._capture_loop` delivers
:data:`CAPTURE_RATE_HZ` frames AS READ (channel-selected, never resampled);
the round 2/3/3b capture resampler (``Resampler``, its windowed-sinc
FIR/polyphase machinery, and their tests) is DELETED, not merely unused —
nothing in this module calls it any more, and a resampler nobody's code path
reaches is a resampler nobody's tests protect. A reference copy of the
polyphase design is kept in this task's own delivery notes/scratchpad, not
in this module. Callers that need to know the rate they are being handed
read :attr:`HostEndpoint.sample_rate` (part of
:class:`~embodiment.audio.endpoint.AudioEndpoint` since this decision) rather
than assuming :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` — the daemon
passes it to the realtime session as ``input_sample_rate``.

No voice, never a raise
------------------------
- :data:`DEGRADED_NO_BACKEND` — neither ``pw-record``/``pw-play`` nor
  ``arecord``/``aplay`` are on ``PATH`` (checked once, at construction, via
  ``shutil.which`` — never a subprocess spawn just to probe for one).
- :data:`DEGRADED_DEVICE_UNRESOLVED` — no explicit device was configured and
  auto-detecting the reSpeaker by name (``/proc/asound/cards``, matching
  ``"XVF3800"``) found more than one candidate. Zero candidates is NOT a
  fault — it falls back to the ALSA/pipewire default device, the same as no
  device being named at all. The candidate COUNT is recorded, never a raw
  device name (config content is not reason-field content, the same
  discipline round 3b applied to a name-substring match).
- :data:`DEGRADED_OPEN` — the capture or playback subprocess could not even
  be started (``OSError`` from ``Popen`` — usually the binary vanishing
  between the ``which`` check and the spawn). Tracked PER DIRECTION
  (round 2 finding 6b's lesson: one direction's fault must never block the
  other), with the SAME cooldown-before-retry discipline round 3 finding 1
  built for a dead output device — a live TTS reply calls
  :meth:`~HostEndpoint.play` roughly every 20 ms, and retrying a `Popen`
  that keeps failing on every single call would be exactly the "200
  attempts across 100 calls" defect round 3 fixed, just with a process spawn
  in place of a PortAudio open.
- :data:`DEGRADED_WRITE_FAILED` — the playback subprocess died mid-reply
  (``BrokenPipeError``/``OSError`` writing to its stdin — round 3 finding 2,
  recurring at a pipe instead of a PortAudio stream). Same fix: close, drop,
  cooldown, never a silent ``callback_errors`` bump.
- :data:`DEGRADED_CAPTURE_ENDED` — the capture subprocess exited (EOF on its
  stdout) without :meth:`~HostEndpoint.stop_capture` asking it to.

All five never raise. The first two are checked once, at construction.

Playback still buffers the WHOLE reply (round 2 finding 1, unchanged)
--------------------------------------------------------------------------
Every chunk :meth:`~HostEndpoint.play` receives is part of one sentence,
still queued in a byte-bounded FIFO (:data:`_PLAYBACK_BUFFER_SECONDS`, 120 s)
rather than a drop-oldest queue — a TTS stream delivering faster than
realtime must never silently lose the middle of a reply. Hitting the bound
refuses the NEW chunk and records one ``degraded`` event per overflow
episode under :data:`DEGRADED_PLAYBACK_OVERFLOW` (round 3 finding 3).

Barge-in is simpler with a process boundary, not just different
---------------------------------------------------------------------
Round 2/3 spent real effort making ``stop_playback()`` cut sounding audio
without a cheap way to interrupt a blocking device write — slicing every
``stream.write()`` to 20 ms, a "generation" stamp checked between slices, an
``abort()``-if-available-else-reopen dance. A subprocess makes the CUT
structurally simpler: closing stdin and SIGKILLing the player stops the
sound at the OS level almost immediately, no per-write slicing needed for
the cut itself. The trade-off: there is no cheap "keep the pipe open across
a barge-in" the way round 3's follow-up fix kept a sounddevice stream open
by calling ``abort()`` — every barge-in costs a fresh process spawn on the
next ``play()``.

Round 5: pacing is a SEPARATE problem the cut alone did not solve
---------------------------------------------------------------------
A live device probe of round 4 found that ``play()`` handed a whole reply to
the writer, which wrote it into the player's stdin as fast as the pipe would
take it — a 3 s reply landed in ``pw-play``'s stdin within 0.3 s. Three
consequences: (a) ``playback_written_samples``/``playing`` stopped
describing what was actually SOUNDING — ``playing`` went ``False`` while
~2.7 s of audio was still buffered in the pipe/player; (b) a barge-in had
nothing left in THIS module's own FIFO to discard, so ``stop_playback()``
had to fall back to killing a process that already held seconds of unplayed
audio; (c) that fallback used :meth:`~HostEndpoint._terminate_process`'s
graceful SIGTERM-then-wait path, which could take up to
:data:`_TERMINATE_TIMEOUT_S` (3 s) — measured at 1.3 s on one run, far past
the ~200 ms barge-in bound the voice works to.

Both halves are fixed. :meth:`HostEndpoint._writer_loop` now PACES itself
against a monotonic clock, writing :data:`_WRITE_SLICE_MS` (20 ms) slices
and staying at most :data:`_PACE_LEAD_S` (100 ms) ahead of real playback
time — slicing across `play()` call boundaries, since a caller's own
chunking has nothing to do with a pacing granularity. This means the FIFO
(:attr:`HostEndpoint._playback_chunks` plus the writer's own
:attr:`~HostEndpoint._writer_pending` tail) genuinely holds "the rest of the
reply" at any moment, so ``written`` approximates played, ``playing`` is
true while audio is actually sounding, and :meth:`~HostEndpoint.stop_playback`
has a real, non-zero count to discard. And ``stop_playback()`` itself no
longer goes through the graceful path at all: it closes stdin and SIGKILLs
the player AT ONCE (:meth:`~HostEndpoint._kill_process_fast`, bounded by
:data:`_BARGE_IN_KILL_TIMEOUT_S`) — a player process is disposable, and the
next ``play()`` simply respawns one. :data:`_PACE_LEAD_S`'s ~100 ms is the
DOCUMENTED, unmeasured overshoot this buys: audio already written into the
pipe/player by the moment of a barge-in can still be heard for about that
long, which is the accepted cost of never letting the player underrun
between writer wake-ups. :meth:`~HostEndpoint.close`, unlike
``stop_playback()``, keeps its graceful natural-EOF drain window — a normal
end of session is not an interruption.

Mute is enforced in the capture path (plan obligation ``o8``), unchanged
-----------------------------------------------------------------------
The drop happens in :meth:`HostEndpoint._capture_loop`, BEFORE channel
selection — the only code path that ever calls the ``on_frame`` callback a
caller registered through
:meth:`~HostEndpoint.start_capture`. Bounded event log
(:data:`_MAX_RETAINED_EVENTS`) + an exact, never-capped mute-transition
counter: round 2 finding 6a's fix, unchanged by the rewrite.

Threads, and what changed about blocking them free (wave 1 lesson 6)
--------------------------------------------------------------------
Two threads this module owns: the capture reader and the playback writer.
Both read/write BLOCKING file objects (a subprocess's stdout/stdin pipe) —
there is no bounded ``queue.get(timeout=...)`` equivalent for a pipe read.
So :meth:`HostEndpoint._stop_capture` TERMINATES the capture subprocess
FIRST, before joining its reader thread: killing the process is what
unblocks the thread's ``stdout.read()`` call, not the ``threading.Event`` the
thread also checks between reads. The writer is simpler: the playback
subprocess is already torn down by :meth:`~HostEndpoint._stop_playback_internal`
(called first, in :meth:`~HostEndpoint.close`) before the writer is joined,
so a blocked ``stdin.write()`` fails fast (a broken pipe) rather than
hanging.

Exit codes and stderr: counted and classified, never copied into a record
---------------------------------------------------------------------------
A dead subprocess's stderr can carry a device path or name (ALSA/pipewire
error text routinely does). :func:`_describe_process_exit` reads it, but —
the same discipline :mod:`embodiment.safe_reason` applies to an exception
message — never returns the text itself: only its length and an 8-hex
fingerprint (:func:`embodiment.safe_reason.name_fingerprint`), plus the
process's own exit code (an integer, never text).

Never ``shell=True``; argv lists only; the binaries' absence is a
degradation, never an exception (``shutil.which`` at construction, never a
speculative spawn); no audio is ever written to disk.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, no user input reaches argv
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from embodiment.audio.endpoint import (
    SAMPLE_RATE_HZ,
    SAMPLE_WIDTH_BYTES,
    EndpointCloseReport,
    EndpointDegradation,
    FrameCallback,
)
from embodiment.safe_reason import describe_exception, name_fingerprint

__all__ = [
    "DEGRADED_NO_BACKEND",
    "DEGRADED_DEVICE_UNRESOLVED",
    "DEGRADED_OPEN",
    "DEGRADED_WRITE_FAILED",
    "DEGRADED_CAPTURE_ENDED",
    "DEGRADED_PLAYBACK_OVERFLOW",
    "CAPTURE_RATE_HZ",
    "CAPTURE_CHANNELS",
    "CAPTURE_CHANNEL_INDEX",
    "PLAYBACK_RATE_HZ",
    "PLAYBACK_CHANNELS",
    "HostEndpoint",
]

#: Neither pipewire's nor ALSA's command-line tools are on PATH.
DEGRADED_NO_BACKEND = "audio-host-no-backend"
#: Auto-detecting the reSpeaker by name matched more than one candidate.
DEGRADED_DEVICE_UNRESOLVED = "audio-host-device-unresolved"
#: The capture or playback subprocess could not be started (tracked per direction).
DEGRADED_OPEN = "audio-host-open-failed"
#: A write to the playback subprocess's stdin raised — it died mid-reply.
DEGRADED_WRITE_FAILED = "audio-host-write-failed"
#: The capture subprocess exited (EOF) without stop_capture() asking it to.
DEGRADED_CAPTURE_ENDED = "audio-host-capture-ended"
#: A play() chunk was refused because the playback buffer is full.
DEGRADED_PLAYBACK_OVERFLOW = "audio-host-playback-overflow"

#: Fixed capture request, regardless of the device's own native rate — the
#: reSpeaker XVF3800 refuses anything else (measured, round 3b); ALSA's
#: ``plughw:``/pipewire perform the conversion for any device that needs one.
#: Since decision 15 (round 6), this is ALSO the rate frames are delivered to
#: ``on_frame`` at — see :attr:`HostEndpoint.sample_rate` — with no resample
#: to :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` in between.
CAPTURE_RATE_HZ = 16000
#: Always request 2 channels: this module selects ONE (see
#: :data:`CAPTURE_CHANNEL_INDEX`), never averages. A device with only one
#: channel still gets a mono request via ``plughw:``'s own upmix; this
#: module's own select-channel step degrades harmlessly on mono input
#: (``_select_channel`` is a passthrough when ``channels <= 1``).
CAPTURE_CHANNELS = 2
#: Which of the (up to) 2 requested channels this module keeps. Cited from
#: ``lobes-cli``'s own measured evidence: channel 1 carries less echo
#: residual and scored the lower WER on this exact hardware; a downmix would
#: mix the worse channel's residual back in.
CAPTURE_CHANNEL_INDEX = 1
#: The fixed wire contract rate — matches
#: :data:`embodiment.audio.endpoint.SAMPLE_RATE_HZ`. ALSA/pipewire resample
#: DOWN to whatever the device wants; this module no longer owns that leg.
PLAYBACK_RATE_HZ = SAMPLE_RATE_HZ
PLAYBACK_CHANNELS = 1

#: How long close()'s GRACEFUL playback teardown waits for SIGTERM before
#: sending SIGKILL. NOT used by stop_playback() (round 5) — a barge-in kills
#: at once (see :data:`_BARGE_IN_KILL_TIMEOUT_S`); a player process is
#: disposable and the next play() simply respawns one.
_TERMINATE_TIMEOUT_S = 3.0

#: How many SECONDS of audio the playback buffer holds before a NEW `play()`
#: chunk is refused (round 2 finding 1, unchanged) — sized in seconds, not
#: bytes or chunk count.
_PLAYBACK_BUFFER_SECONDS = 120.0

#: Capture reader chunk size: ~20 ms at the fixed capture rate/channel count.
_CAPTURE_CHUNK_BYTES = int(CAPTURE_RATE_HZ * 0.02) * CAPTURE_CHANNELS * SAMPLE_WIDTH_BYTES

#: How long a reader/writer thread waits on an idle queue before re-checking
#: its stop signal. Bounds shutdown latency without busy-waiting.
_POLL_INTERVAL_S = 0.1

#: Round 5: the writer paces itself against a monotonic clock in slices this
#: size, rather than dumping a whole `play()` chunk into the pipe/player at
#: once — round 4's own probe measured a 3 s reply written to pw-play's
#: stdin within 0.3 s, meaning `stop_playback()` had nothing left in the
#: FIFO to discard and had to fall back to killing a process that already
#: had ~2.7 s of audio queued past what the OS/player could possibly have
#: sounded yet.
_WRITE_SLICE_MS = 20

#: How far AHEAD of real playback time the writer is allowed to stay written
#: into the pipe/player before pausing (round 5). A judgement call, stated
#: because it is one: large enough that the player's own internal buffering
#: never underruns between writer wake-ups (a stall is audible as a click or
#: gap — the failure mode a lead protects against), small enough that a
#: barge-in's kill only has to discard/lose about this much already-written-
#: but-not-yet-sounding audio, keeping `stop_playback()` well under the
#: 200 ms barge-in bound the voice works to. 100 ms is documented,
#: unmeasured overshoot on a real barge-in — see the module docstring.
_PACE_LEAD_S = 0.1

#: How long stop_playback() waits for SIGKILL to land before giving up and
#: reporting the process as not-confirmed-dead (round 5). Bounds
#: stop_playback() itself, not just the audio's own cutoff — SIGKILL cannot
#: be ignored by the child, so this is a wait for the OS to reap it, not a
#: grace period the child gets to use.
_BARGE_IN_KILL_TIMEOUT_S = 0.15

#: Round 3 finding 1's cooldown, unchanged in shape, now guarding a process
#: spawn instead of a PortAudio open: 2 s doubling to a 30 s cap.
_OPEN_COOLDOWN_BASE_S = 2.0
_OPEN_COOLDOWN_MAX_S = 30.0

#: How many events (mute changes, per-direction recovery) this module keeps
#: in the retained log before evicting the oldest (round 2 finding 6a). The
#: EXACT count of genuine mute transitions is tracked separately and never
#: capped — only the detailed per-event log has a memory bound.
_MAX_RETAINED_EVENTS = 1000


def _default_which(name: str) -> str | None:
    """The ONE place ``shutil.which`` is spelled out — tests inject a fake."""
    return shutil.which(name)


def _select_backend(which: Callable[[str], str | None]) -> str | None:
    """``"pipewire"`` / ``"alsa"`` / ``None`` — checked once, at construction.

    Never spawns anything: a PATH lookup only. Preference order matches both
    sibling projects: pipewire first (this rig runs behind it), ALSA as the
    fallback.
    """
    if which("pw-record") and which("pw-play"):
        return "pipewire"
    if which("arecord") and which("aplay"):
        return "alsa"
    return None


def _find_xvf3800_cards(cards_path: Path) -> list[str]:
    """ALSA card numbers whose ``/proc/asound/cards`` line names the reSpeaker.

    Never raises: a missing or unreadable file (a non-Linux host, a
    container without ``/proc/asound``) degrades to an empty list, which
    :meth:`HostEndpoint._resolve_device` reads as "fall back to the
    ALSA/pipewire default", not as a fault.
    """
    try:
        text = cards_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    for line in text.splitlines():
        if "XVF3800" not in line:
            continue
        head = line.strip().split(None, 1)
        if head and head[0].isdigit():
            found.append(head[0])
    return found


def _build_capture_argv(backend: str, device: object, rate: int, channels: int) -> list[str]:
    """Cited from ``lobes-cli/scripts/realtime-he-accept.py``'s ``build_capture_argv``."""
    if backend == "alsa":
        target = f"plughw:{device},0" if device is not None else "default"
        return [
            "arecord",
            "-D",
            target,
            "-f",
            "S16_LE",
            "-r",
            str(rate),
            "-c",
            str(channels),
            "-t",
            "raw",
            "-q",
        ]
    argv = ["pw-record"]
    if device is not None:
        argv += ["--target", str(device)]
    argv += ["--rate", str(rate), "--channels", str(channels), "--format", "s16", "-"]
    return argv


def _build_playback_argv(backend: str, device: object, rate: int, channels: int) -> list[str]:
    """Cited from ``lobes-cli/scripts/realtime-he-accept.py``'s ``build_playback_argv``."""
    if backend == "alsa":
        target = f"plughw:{device},0" if device is not None else "default"
        return [
            "aplay",
            "-D",
            target,
            "-f",
            "S16_LE",
            "-r",
            str(rate),
            "-c",
            str(channels),
            "-t",
            "raw",
            "-q",
        ]
    argv = ["pw-play"]
    if device is not None:
        argv += ["--target", str(device)]
    argv += ["--rate", str(rate), "--channels", str(channels), "--format", "s16", "-"]
    return argv


def _describe_process_exit(proc: "subprocess.Popen[bytes]") -> str:
    """The exit code and stderr's LENGTH+FINGERPRINT — never the stderr text itself.

    ALSA/pipewire error text routinely names a device path; this module
    applies the same discipline :mod:`embodiment.safe_reason` applies to an
    exception message.
    """
    code = proc.poll()
    text = ""
    try:
        if proc.stderr is not None:
            raw = proc.stderr.read()
            if raw:
                text = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - reading a dead process's stderr is best-effort
        text = ""
    return f"exit={code} stderr: {len(text)} chars, fp:{name_fingerprint(text)}"


def _select_channel(np: Any, raw: bytes, channels: int, channel_index: int) -> bytes:
    """Pick ONE channel out of interleaved multi-channel pcm16 — never average.

    Cited from ``lobes-cli/scripts/realtime-he-accept.py``'s ``select_channel``
    (same reasoning: the reSpeaker XVF3800's channel 1 carries less echo
    residual and scored the lowest WER; a downmix mixes the worse channel's
    residual back in). Never raises: malformed input degrades to ``b""``.
    """
    if channels <= 1:
        return raw
    frame_bytes = SAMPLE_WIDTH_BYTES * channels
    usable = raw[: len(raw) - (len(raw) % frame_bytes)]
    if not usable:
        return b""
    idx = min(max(channel_index, 0), channels - 1)
    samples = np.frombuffer(usable, dtype="<i2").reshape(-1, channels)
    return np.ascontiguousarray(samples[:, idx]).tobytes()


def _import_numpy() -> Any:
    """Lazy numpy import point — never at module scope. Used only by
    :func:`_select_channel` (round 6 deleted the capture resampler that was
    this function's other caller — see the module docstring, decision 15);
    numpy itself is an existing approved runtime import
    (``embodiment.continuity``, via ``coherence-cli`` — see ``tests/test_zero_deps.py``),
    so this module pays nothing extra by using it, but stays lazy on its own merits: a
    host that never captures audio should not pay to import it either."""
    import numpy

    return numpy


class HostEndpoint:
    """Subprocess-driven :class:`~embodiment.audio.endpoint.AudioEndpoint` (round 4, d4).

    Args:
        device: names the SAME physical device for BOTH directions (the
            array's hardware AEC needs its own output as the far-end
            reference — lobes-cli refuses a split mic/speaker pair for
            exactly this reason, and this module structurally cannot
            construct one, since there is only ever one setting). An ALSA
            card number (``"1"``) for the ``alsa`` backend, or a pipewire
            target name/id for ``pipewire``. ``None`` (the default)
            auto-detects the reSpeaker via ``/proc/asound/cards``,
            falling back to the ALSA/pipewire default device if none (or
            more than one) is found.
        backend: force ``"pipewire"`` or ``"alsa"``. ``None`` (the default)
            auto-selects via :func:`_select_backend`.
        which: injection seam for :func:`shutil.which` (tests fake PATH
            lookups without touching the real ``PATH``).
        popen: injection seam for :class:`subprocess.Popen` (tests substitute
            a real small Python child process — never a mock — per the
            round 4 brief).
        cards_path: injection seam for ``/proc/asound/cards``.
        queue_maxsize: unused placeholder kept for signature stability with
            earlier rounds; the playback bound is seconds-based (see
            :data:`_PLAYBACK_BUFFER_SECONDS`) and capture has no bounded
            queue any more — the reader thread does the whole pipeline
            inline (round 4: no PortAudio realtime-callback constraint to
            keep a queue between).
        numpy_importer: injection seam for the lazy numpy import.
    """

    def __init__(
        self,
        *,
        device: object = None,
        backend: str | None = None,
        which: Callable[[str], str | None] = _default_which,
        popen: Callable[..., "subprocess.Popen[bytes]"] = subprocess.Popen,
        cards_path: Path = Path("/proc/asound/cards"),
        queue_maxsize: int = 0,
        numpy_importer: Callable[[], Any] = _import_numpy,
    ) -> None:
        self._which = which
        self._popen = popen
        self._cards_path = cards_path
        self._np_importer = numpy_importer

        self._counter_lock = threading.RLock()
        self._muted = False
        self._events: "deque[dict[str, object]]" = deque(maxlen=_MAX_RETAINED_EVENTS)
        self._mute_event_count = 0
        self._attached = False
        self._capturing = False
        self._closed = False
        self._on_frame: FrameCallback | None = None

        self._capture_proc: "subprocess.Popen[bytes] | None" = None
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()

        self._playback_proc: "subprocess.Popen[bytes] | None" = None
        self._playback_chunks: "deque[bytes]" = deque()
        self._playback_queued_bytes = 0
        self._writer_pending = b""
        self._playback_written_samples = 0
        self._playback_total_pushed_samples = 0
        self._playback_stop_discarded_total = 0
        self._playback_overflow_count = 0
        self._playback_overflow_episode_active = False
        self._playback_generation = 0
        self._playing = False
        self._writer_thread: threading.Thread | None = None
        self._writer_stop = threading.Event()

        self._capture_muted_dropped = 0
        self._callback_errors = 0
        self._playback_dropped_no_device = 0
        self._output_degrade_attempts = 0
        self._out_open_cooldown_until = 0.0
        self._out_open_backoff_s = _OPEN_COOLDOWN_BASE_S

        self._last_close_report: EndpointCloseReport | None = None

        self._degradation: EndpointDegradation | None = None
        self._degradation_in: EndpointDegradation | None = None
        self._degradation_out: EndpointDegradation | None = None

        self._backend = backend or _select_backend(self._which)
        if self._backend is None:
            self._degradation = EndpointDegradation(
                DEGRADED_NO_BACKEND, "no pipewire or alsa audio backend found on PATH"
            )

        self._device: object = device
        if self._degradation is None and device is None:
            matches = _find_xvf3800_cards(self._cards_path)
            if len(matches) == 1:
                self._device = matches[0]
            elif len(matches) > 1:
                self._device = None
                self._degradation = EndpointDegradation(
                    DEGRADED_DEVICE_UNRESOLVED,
                    f"device auto-detect matched {len(matches)} candidates, need exactly 1",
                )
            # zero matches: fall back to the ALSA/pipewire default (None), not a fault.

    # -- shared helpers ------------------------------------------------

    def _record_event(self, event: dict[str, object]) -> None:
        with self._counter_lock:
            self._events.append(event)
            if event.get("type") == "mute":
                self._mute_event_count += 1

    def _enter_output_degradation(self, code: str, exc: BaseException, action: str) -> None:
        """Round 3 finding 1/2, unchanged: one recorded episode + a cooldown."""
        is_new_episode = self._degradation_out is None
        reason = f"{action}: {describe_exception(exc)}"
        self._degradation_out = EndpointDegradation(code, reason)
        if is_new_episode:
            self._record_event({"type": "degraded", "code": code, "direction": "out"})
        with self._counter_lock:
            self._output_degrade_attempts += 1
        self._out_open_cooldown_until = time.monotonic() + self._out_open_backoff_s
        self._out_open_backoff_s = min(_OPEN_COOLDOWN_MAX_S, self._out_open_backoff_s * 2.0)

    def _terminate_process(
        self, proc: "subprocess.Popen[bytes]", timeout: float = _TERMINATE_TIMEOUT_S
    ) -> int:
        """Terminate, wait, kill if needed. Returns 1 if it never confirmed dead."""
        if proc.poll() is not None:
            return 0
        try:
            proc.terminate()
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
        try:
            proc.wait(timeout=max(0.0, timeout))
            return 0
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=max(0.0, timeout))
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
            return 0 if proc.poll() is not None else 1

    def _kill_process_fast(
        self, proc: "subprocess.Popen[bytes]", timeout: float = _BARGE_IN_KILL_TIMEOUT_S
    ) -> int:
        """SIGKILL at once, no SIGTERM grace period (round 5's barge-in path).

        A player process is disposable: the next play() simply respawns one.
        Waiting out a SIGTERM grace period before escalating (as
        :meth:`_terminate_process` does for a graceful close) is exactly what
        made round 4's own barge-in miss its 200 ms bound. Returns 1 if the
        OS never confirmed the kill within *timeout* (SIGKILL itself cannot
        be ignored, so this bounds the WAIT for reaping, not a grace period
        the child gets to use).
        """
        if proc.poll() is not None:
            return 0
        try:
            proc.kill()
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
        try:
            proc.wait(timeout=max(0.0, timeout))
            return 0
        except subprocess.TimeoutExpired:
            return 1

    # -- lifecycle ---------------------------------------------------------

    def attach(self) -> None:
        if self._attached:
            return
        self._closed = False
        self._attached = True

    def detach(self) -> None:
        self.stop_playback()
        self._stop_capture(timeout=2.0)
        self._stop_writer(timeout=2.0)
        self._attached = False

    def close(self, deadline: float) -> EndpointCloseReport:
        deadline = max(0.0, float(deadline))
        start = time.monotonic()

        # A normal end of session (unlike stop_playback()'s barge-in) gets a
        # brief chance to exit on its own after EOF — up to a THIRD of the
        # deadline, so plenty is still left for the capture/writer joins
        # below even if the player uses its whole share.
        samples_discarded, playback_close_failures = self._stop_playback_internal(
            drain_timeout=deadline / 3.0
        )

        remaining = max(0.0, deadline - (time.monotonic() - start))
        capture_stopped, capture_close_failures = self._stop_capture(timeout=remaining / 2.0)

        remaining = max(0.0, deadline - (time.monotonic() - start))
        writer_stopped = self._stop_writer(timeout=remaining)

        self._attached = False
        self._closed = True
        elapsed = time.monotonic() - start
        report = EndpointCloseReport(
            capture_thread_stopped=capture_stopped,
            writer_thread_stopped=writer_stopped,
            samples_discarded=samples_discarded,
            elapsed_s=elapsed,
            streams_close_failed=playback_close_failures + capture_close_failures,
        )
        self._last_close_report = report
        return report

    # -- capture -------------------------------------------------------

    def start_capture(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        if self._degradation is not None or self._closed:
            return
        if self._capturing:
            return

        argv = _build_capture_argv(self._backend, self._device, CAPTURE_RATE_HZ, CAPTURE_CHANNELS)
        was_degraded = self._degradation_in is not None
        try:
            proc = self._popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            self._degradation_in = EndpointDegradation(
                DEGRADED_OPEN, f"capture subprocess failed to start: {describe_exception(exc)}"
            )
            return

        if was_degraded:
            self._degradation_in = None
            self._record_event({"type": "recovered", "direction": "in"})

        self._capture_proc = proc
        self._capture_stop.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(proc,),
            name="embodiment-audio-host-capture",
            daemon=True,
        )
        self._capture_thread.start()
        self._capturing = True
        self._attached = True

    def stop_capture(self) -> None:
        self._stop_capture(timeout=2.0)

    def _stop_capture(self, timeout: float) -> tuple[bool, int]:
        """Terminate the subprocess FIRST — that is what unblocks the reader's
        pipe read (see the module docstring's threads section)."""
        self._capture_stop.set()
        proc = self._capture_proc
        self._capture_proc = None
        half = max(0.0, timeout) / 2.0
        close_failures = 0
        if proc is not None:
            close_failures = self._terminate_process(proc, timeout=half)

        thread = self._capture_thread
        self._capture_thread = None
        stopped = True
        if thread is not None:
            thread.join(timeout=half)
            stopped = not thread.is_alive()
        self._capturing = False
        return stopped, close_failures

    def _capture_loop(self, proc: "subprocess.Popen[bytes]") -> None:
        """This module's own thread: read, mute-check, select channel, deliver.

        Mute drops bytes here, BEFORE channel selection and BEFORE delivery
        — before encode, matching plan obligation o8. Frames are delivered
        at :data:`CAPTURE_RATE_HZ` (16 kHz), the device's own native rate —
        decision 15 (operator, issue #85): NO resample to
        :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` happens here any
        more (see the module docstring). Never raises out.
        """
        stdout = proc.stdout
        ended_cleanly = False
        while not self._capture_stop.is_set():
            try:
                raw = stdout.read(_CAPTURE_CHUNK_BYTES) if stdout is not None else b""
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                break
            if not raw:
                ended_cleanly = True
                break

            if self._muted:
                with self._counter_lock:
                    self._capture_muted_dropped += 1
                continue

            try:
                np = self._np_importer()
                selected = _select_channel(np, raw, CAPTURE_CHANNELS, CAPTURE_CHANNEL_INDEX)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                continue

            callback = self._on_frame
            if callback is None:
                continue
            try:
                callback(selected)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1

        if ended_cleanly and not self._capture_stop.is_set():
            # The subprocess exited (EOF) without stop_capture() asking it to
            # — a real fault (device unplugged, subprocess crashed), not a
            # teardown. Recorded and named — see status()'s degradation_in.
            self._degradation_in = EndpointDegradation(
                DEGRADED_CAPTURE_ENDED, f"capture subprocess ended: {_describe_process_exit(proc)}"
            )

    # -- playback --------------------------------------------------------

    def _playback_buffer_limit_samples(self) -> int:
        return int(_PLAYBACK_BUFFER_SECONDS * PLAYBACK_RATE_HZ)

    def play(self, frames: bytes) -> None:
        if not isinstance(frames, (bytes, bytearray)):
            return
        if self._degradation is not None or self._closed:
            return

        if self._playback_proc is None:
            if (
                self._degradation_out is not None
                and time.monotonic() < self._out_open_cooldown_until
            ):
                # Round 3 finding 1, recurring at a process spawn: a dead
                # player does NOT get retried on every play() call.
                with self._counter_lock:
                    self._playback_dropped_no_device += 1
                return

            was_degraded = self._degradation_out is not None
            argv = _build_playback_argv(
                self._backend, self._device, PLAYBACK_RATE_HZ, PLAYBACK_CHANNELS
            )
            try:
                proc = self._popen(
                    argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
            except OSError as exc:
                self._enter_output_degradation(
                    DEGRADED_OPEN, exc, "playback subprocess failed to start"
                )
                with self._counter_lock:
                    self._playback_dropped_no_device += 1
                return
            if was_degraded:
                self._degradation_out = None
                self._out_open_backoff_s = _OPEN_COOLDOWN_BASE_S
                self._record_event({"type": "recovered", "direction": "out"})
            self._playback_proc = proc
            self._start_writer()
            self._attached = True

        chunk_bytes = bytes(frames)
        if not chunk_bytes:
            return

        limit_samples = self._playback_buffer_limit_samples()
        with self._counter_lock:
            if self._playback_proc is None:
                # A concurrent stop_playback()/write failure tore down the
                # process while this call was above this lock — never
                # silently queue a chunk for a player that no longer exists
                # (round 3's play()/stop_playback() interleaving fix,
                # recurring at a process reference instead of a stream one).
                self._playback_dropped_no_device += 1
                return
            in_flight = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            if in_flight + len(chunk_bytes) // SAMPLE_WIDTH_BYTES > limit_samples:
                if not self._playback_overflow_episode_active:
                    self._playback_overflow_episode_active = True
                    self._record_event(
                        {"type": "degraded", "code": DEGRADED_PLAYBACK_OVERFLOW, "direction": "out"}
                    )
                self._playback_overflow_count += 1
                return
            self._playback_overflow_episode_active = False
            self._playback_chunks.append(chunk_bytes)
            self._playback_queued_bytes += len(chunk_bytes)
            self._playback_total_pushed_samples += len(chunk_bytes) // SAMPLE_WIDTH_BYTES
            self._playing = True

    def stop_playback(self) -> int:
        """Barge-in: close stdin, SIGKILL AT ONCE (round 5 — no SIGTERM grace period).

        No drain grace period, no polite terminate-then-wait — that is the
        whole point of a barge-in: stop the sound NOW. A player process is
        disposable; the next :meth:`play` simply respawns one. Measured
        target: returns in under 200 ms with :attr:`playing` already
        ``False``. The writer's pacing (:data:`_PACE_LEAD_S`) means only
        about 100 ms of already-written-but-not-yet-sounding audio can
        possibly still be heard after this returns — a documented,
        unmeasured overshoot, not an unbounded one.

        :meth:`close`, by contrast, gives the player a brief chance to exit
        naturally on EOF first (see :meth:`_stop_playback_internal`'s
        ``drain_timeout``), since a normal end of session is not an
        interruption.
        """
        discarded, _close_failures = self._stop_playback_internal(drain_timeout=0.0)
        return discarded

    def _stop_playback_internal(self, *, drain_timeout: float = 0.0) -> tuple[int, int]:
        with self._counter_lock:
            discarded = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._writer_pending = b""
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
            proc = self._playback_proc
            self._playback_proc = None

        close_failures = 0
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
            if drain_timeout > 0.0:
                try:
                    proc.wait(timeout=drain_timeout)
                except subprocess.TimeoutExpired:
                    close_failures = self._terminate_process(proc)
            else:
                # Barge-in: SIGKILL at once, no SIGTERM grace period (round 5).
                close_failures = self._kill_process_fast(proc)
        return discarded, close_failures

    @property
    def playing(self) -> bool:
        return self._playing

    def _handle_write_failure(self, exc: BaseException) -> None:
        """Round 3 finding 2, recurring at a pipe: never silent, never hammered again."""
        with self._counter_lock:
            proc = self._playback_proc
            self._playback_proc = None
            discarded = (
                self._playback_queued_bytes + len(self._writer_pending)
            ) // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._writer_pending = b""
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
        if proc is not None:
            self._terminate_process(proc)
        self._enter_output_degradation(DEGRADED_WRITE_FAILED, exc, "playback write failed")

    def _start_writer(self) -> None:
        if self._writer_thread is not None:
            return
        self._writer_stop.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="embodiment-audio-host-writer", daemon=True
        )
        self._writer_thread.start()

    def _stop_writer(self, timeout: float = 2.0) -> bool:
        self._writer_stop.set()
        thread = self._writer_thread
        self._writer_thread = None
        stopped = True
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
            stopped = not thread.is_alive()
        return stopped

    def _writer_loop(self) -> None:
        """Write PACED 20 ms slices, staying at most :data:`_PACE_LEAD_S` ahead
        of real playback time (round 5) — never a whole `play()` chunk dumped
        into the pipe at once. This is what makes ``written``/``playing``
        describe what is actually sounding, and what leaves
        :meth:`stop_playback` something real to discard from the FIFO
        instead of a player already holding seconds of unplayed audio.

        Slicing happens ACROSS chunk boundaries (chunks queued by separate
        `play()` calls are concatenated into :attr:`_writer_pending`, a flat
        byte buffer), because a caller's own chunking has nothing to do with
        a 20 ms pacing granularity.
        """
        slice_bytes = int(PLAYBACK_RATE_HZ * _WRITE_SLICE_MS / 1000) * SAMPLE_WIDTH_BYTES
        clock_start = 0.0
        samples_written_for_clock = 0
        active_proc: "subprocess.Popen[bytes] | None" = None

        while not self._writer_stop.is_set():
            with self._counter_lock:
                proc = self._playback_proc
                if proc is not active_proc:
                    # A fresh process (first play(), or a respawn after a
                    # barge-in/write-failure): pacing restarts from now,
                    # never carries a stale clock across processes.
                    active_proc = proc
                    clock_start = time.monotonic()
                    samples_written_for_clock = 0

                slice_: bytes | None = None
                if proc is not None and proc.stdin is not None:
                    while len(self._writer_pending) < slice_bytes:
                        try:
                            extra = self._playback_chunks.popleft()
                        except IndexError:
                            break
                        self._playback_queued_bytes -= len(extra)
                        self._writer_pending += extra
                    if self._writer_pending:
                        slice_ = self._writer_pending[:slice_bytes]

            if slice_ is None:
                time.sleep(_POLL_INTERVAL_S)
                continue

            target_time = clock_start + samples_written_for_clock / PLAYBACK_RATE_HZ
            lead = target_time - time.monotonic()
            if lead > _PACE_LEAD_S:
                time.sleep(min(_POLL_INTERVAL_S, lead - _PACE_LEAD_S))
                continue

            try:
                proc.stdin.write(slice_)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except Exception as exc:
                self._handle_write_failure(exc)
                active_proc = None  # force a fresh clock for whatever comes next
                continue

            with self._counter_lock:
                # Only advance state if this write's bytes are still the
                # front of `_writer_pending` — a concurrent stop_playback()/
                # write-failure may have cleared it while this thread was
                # blocked inside write() above.
                if self._writer_pending[: len(slice_)] == slice_:
                    self._writer_pending = self._writer_pending[len(slice_) :]
                self._playback_written_samples += len(slice_) // SAMPLE_WIDTH_BYTES
                self._playing = bool(self._playback_chunks) or bool(self._writer_pending)
            samples_written_for_clock += len(slice_) // SAMPLE_WIDTH_BYTES

    # -- mute --------------------------------------------------------------

    def mute(self, muted: bool) -> None:
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self._record_event({"type": "mute", "muted": muted})

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        """The retained (bounded) event log, in order. Never carries audio content."""
        with self._counter_lock:
            return tuple(self._events)

    @property
    def sample_rate(self) -> int:
        """The rate (Hz) of frames delivered to ``on_frame`` — :data:`CAPTURE_RATE_HZ`.

        Decision 15 (operator, issue #85): the ears send the device's
        NATIVE 16 kHz; a caller (the daemon) reads this and passes it to the
        realtime session as ``input_sample_rate`` instead of assuming
        :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` (24 kHz). See the
        module docstring.
        """
        return CAPTURE_RATE_HZ

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._counter_lock:
            counters = {
                "capture_muted_dropped": self._capture_muted_dropped,
                "callback_errors": self._callback_errors,
                "mute_event_count": self._mute_event_count,
                "events_retained": len(self._events),
                "playback_overflow_count": self._playback_overflow_count,
                "playback_written_samples": self._playback_written_samples,
                "playback_total_pushed_samples": self._playback_total_pushed_samples,
                "playback_stop_discarded_total": self._playback_stop_discarded_total,
                "playback_queued_bytes": self._playback_queued_bytes,
                "playback_dropped_no_device": self._playback_dropped_no_device,
                "output_degrade_attempts": self._output_degrade_attempts,
            }
        return {
            "attached": self._attached,
            "capturing": self._capturing,
            "playing": self._playing,
            "muted": self._muted,
            "closed": self._closed,
            "backend": self._backend,
            "device": self._device,
            "degradation": self._degradation.to_dict() if self._degradation else None,
            "degradation_in": self._degradation_in.to_dict() if self._degradation_in else None,
            "degradation_out": self._degradation_out.to_dict() if self._degradation_out else None,
            "close_report": self._last_close_report.to_dict() if self._last_close_report else None,
            **counters,
        }
