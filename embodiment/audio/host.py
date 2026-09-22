"""The host loudspeaker/microphone :class:`~embodiment.audio.endpoint.AudioEndpoint`.

Plan task ``t7``. :class:`HostEndpoint` is the ``sounddevice``-backed
implementation of the protocol in :mod:`embodiment.audio.endpoint`: it opens
the machine's actual input and output audio devices through PortAudio. It is
the only module in this repo that may import ``sounddevice`` (the optional
``audio`` extra), and it never does so at module scope — every entry point
(``_import_sounddevice``) calls it from inside a function body, so
``tests/test_zero_deps.py``'s
``test_no_module_imports_an_optional_extra_at_module_scope`` guard, and this
module's own ``import embodiment.audio.host`` in a clean interpreter, both
stay clean whether or not ``sounddevice``/PortAudio are installed. numpy is
approved elsewhere in this package (``embodiment.continuity``, attributed in
``tests/test_zero_deps.py``) but is imported lazily here too, for the same
reason ``embodiment.audio.features`` gives for staying off it entirely: this
module should not assume numpy's cost is already paid by whatever imported it.

Round 2 — six defects an independent probe found by driving this module the
way the daemon actually will (a real TTS-speed playback stream, a real
barge-in, a deadline it was supposed to honour). Each is named where its fix
lives; the probe numbers this round measured are restated in this task's
delivery notes rather than here, so this docstring cannot drift out of sync
with a figure nobody re-measures.

No voice, never a raise
------------------------
Four distinct things can go wrong before a single frame moves, and each gets
its own degradation code so a host can tell them apart (wave 1 lesson 4 — name
the fault, don't just record that one occurred):

- :data:`DEGRADED_IMPORT` — ``sounddevice`` is not installed.
- :data:`DEGRADED_PORTAUDIO` — the ``sounddevice`` *package* imports, but the
  native PortAudio library it wraps does not (``sounddevice`` raises
  ``OSError`` from its own module body in that case).
- :data:`DEGRADED_ENUMERATION` — the import succeeded but
  ``query_devices()`` raised, or returned nothing.
- :data:`DEGRADED_OPEN` — everything above succeeded, but opening the actual
  stream raised. Only discoverable by trying, so it surfaces from
  :meth:`~HostEndpoint.start_capture`/:meth:`~HostEndpoint.play`, never from
  construction — **and, since round 2 finding 6, tracked PER DIRECTION**
  (:attr:`HostEndpoint._degradation_in` / :attr:`~HostEndpoint._degradation_out`)
  rather than in the same field the first three share. The first three are a
  fault in ``sounddevice``/PortAudio itself and legitimately block both
  directions; an output-device-busy failure blocking capture (or vice versa)
  was round 2's finding 6b, not a design intent. A later
  :meth:`~HostEndpoint.start_capture`/:meth:`~HostEndpoint.play` call RETRIES
  the open (never a loop, never a background thread) and, on success, clears
  that direction's degradation and records exactly one recovery event — a
  replugged USB mic no longer needs a daemon restart. **Capture** retries
  unconditionally, once per call, because :meth:`~HostEndpoint.start_capture`
  is called rarely (daemon startup, or a deliberate manual retry) — never
  from a hot loop. **Playback is different and round 3 finding 1 is why**: a
  live TTS reply calls :meth:`~HostEndpoint.play` roughly every 20 ms, and a
  probe against a dead output measured 200 open attempts across 100 calls —
  each one a PortAudio call that can BLOCK, against ``play()``'s own "never
  blocks" promise. So a failed output open (or write — see
  :data:`DEGRADED_WRITE_FAILED` below) starts an exponential cooldown
  (:data:`_OPEN_COOLDOWN_BASE_S` doubling to :data:`_OPEN_COOLDOWN_MAX_S`)
  before the NEXT attempt; every chunk offered while cooling down is counted
  (:attr:`HostEndpoint._playback_dropped_no_device`), never silently
  dropped, and the degradation is recorded ONCE per failure episode with the
  attempt count tracked separately (:attr:`HostEndpoint._output_degrade_attempts`,
  never capped) rather than once per attempt.

The first three are checked once, at construction. All four never raise.

A device that dies mid-playback is not silent either — round 3 finding 2
----------------------------------------------------------------------------
A ``stream.write()`` that starts raising (the USB device unplugged, the ALSA
node gone) used to just increment ``callback_errors`` forever: the writer
kept calling ``write()`` on a dead stream every slice, and
``degradation_out`` stayed ``None`` — a healthy-looking status for a daemon
that has gone mute. :meth:`HostEndpoint._handle_write_failure` now closes and
drops the stream, discards whatever was still queued for it (a device that
just failed IS a device no longer worth queueing for), records
:data:`DEGRADED_WRITE_FAILED` through the same episode/cooldown mechanism as
an open failure, and lets the next :meth:`~HostEndpoint.play` go through the
cooldown-gated reopen above rather than hammering the dead device again.

Mute is enforced in the capture path (plan obligation ``o8``)
----------------------------------------------------------------
The drop happens in :meth:`HostEndpoint._drain_loop`, the ONLY code path that
ever calls the ``on_frame`` callback a caller registered through
:meth:`~HostEndpoint.start_capture`. :meth:`~HostEndpoint.mute` records
exactly one event per genuine change (repeats are a no-op) — and, since round
2 finding 6a, the exact count (:attr:`HostEndpoint._mute_event_count`) is
tracked separately from the bounded event LOG (:attr:`HostEndpoint._events`,
a ``collections.deque(maxlen=...)``), because "every genuine transition is
counted" and "memory stays bounded under 100,000 flips" are two different
promises and conflating them was the bug: the old unbounded ``list`` held
every event ever recorded.

Playback buffers the WHOLE reply — round 2 finding 1
--------------------------------------------------------
A 64-slot drop-oldest queue is right for CAPTURE (stale microphone audio is
worthless — a duplicate or three-blocks-old frame helps nobody) and was
**wrong for PLAYBACK**: every chunk :meth:`~HostEndpoint.play` receives is
part of one sentence, and a TTS stream that delivers faster than realtime (an
entire reply as a burst of 20 ms chunks, exactly how one arrives) hit the
64-slot bound almost immediately and silently ate the middle of Gwen's
speech. Playback is now a byte-bounded FIFO — :data:`_PLAYBACK_BUFFER_SECONDS`
(120 s, a judgement call, stated because it is one: about 5.76 MB of pcm16
audio at 24 kHz mono, generous enough that no real reply should ever hit it,
small enough that a genuinely stuck writer cannot grow this module's memory
without bound) — sized in SECONDS of audio, not chunk count, so it degrades
the same way regardless of how a caller chunks its input. Hitting the bound
refuses the NEW chunk (the buffer's contents are untouched — nothing already
queued is ever silently discarded) and increments
:attr:`HostEndpoint._playback_overflow_count` under
:data:`DEGRADED_PLAYBACK_OVERFLOW`, a NAMED, COUNTED degradation. Round 3
finding 3 closed the gap between that claim and the code: the counter was
real but the code was a dead constant nobody ever recorded an event under.
An overflow now also records ONE ``degraded`` event per episode (the first
overflowing ``play()`` call after a non-overflowing period), the same
episode discipline round 3 finding 1 uses for a dead output — the exact
count stays in :attr:`~HostEndpoint._playback_overflow_count`, which is
already exact; the event log just stops being silent about the fact that
an episode happened at all.

Barge-in: stop_playback() actually cuts — round 2 finding 2
-----------------------------------------------------------
The Protocol had ``play()`` and nothing to stop it; the spec says the daemon
owns barge-in (stop the speaker on ``speech_started`` during playback), which
was structurally impossible. :meth:`HostEndpoint.stop_playback` clears every
queued chunk, computes how many 24 kHz-equivalent samples were discarded
(queued bytes plus whatever remained of the chunk currently being written),
best-effort calls the stream's own ``abort()`` if it offers one (guarded —
not every stream, real or fake, supports hardware-level abort), then closes
and drops the output stream so the NEXT :meth:`~HostEndpoint.play` reopens
cleanly. What actually makes this CUT rather than merely stop queueing is
that the writer thread (:meth:`~HostEndpoint._writer_loop`) never hands the
device more than :data:`_WRITE_SLICE_MS` (20 ms) of audio per
``stream.write()`` call — big buffers are sliced internally — and checks a
per-chunk "generation" stamp between every slice, so at most one slice's
worth of audio can still be sounding by the time a barge-in request is
noticed, regardless of how large the original :meth:`~HostEndpoint.play` call
was.

The review's third MAJOR candidate — investigated, not reproduced. A 2-second
stress test hammering :meth:`~HostEndpoint.play` and
:meth:`~HostEndpoint.stop_playback` from two threads found no orphaned
stream, but did surface a genuine narrow window: :meth:`~HostEndpoint.play`
reads ``self._output_stream`` once, BEFORE resampling, to decide whether to
open a fresh one; a concurrent :meth:`~HostEndpoint.stop_playback` could
close and null that same stream while the resampling ran, leaving ``play()``
about to enqueue a chunk for a stream reference that no longer exists.
Closed structurally rather than left as a timing bet: :meth:`_stop_playback_internal`
now nulls ``self._output_stream`` in the SAME lock acquisition as its
queue-clear and generation bump, and :meth:`play` RE-CHECKS
``self._output_stream is None`` immediately before enqueueing, under that
same lock. Either the append happens-before the clear (the chunk is cleanly
discarded, counted in ``playback_stop_discarded_total``) or the check
happens-after the null (the chunk is refused up front, counted in
``playback_dropped_no_device``) — there is no window left where a chunk is
silently unaccounted for.

close(deadline) actually honours its deadline — round 2 finding 3
-------------------------------------------------------------------
The old code computed a "remaining deadline" and then threw it away
(``_ = max(...)``), and both thread joins were hard-coded at 2.0 s regardless
of what the caller asked for. :meth:`~HostEndpoint.close` now: (1) calls
:meth:`~HostEndpoint.stop_playback` first (which, thanks to finding 2's small
writes, returns almost immediately even mid-playback), then (2) splits
whatever deadline remains across the capture-thread join and the writer-
thread join, each with a real, observed timeout — never a fixed constant —
and (3) returns an :class:`~embodiment.audio.endpoint.EndpointCloseReport`
(also mirrored in :meth:`~HostEndpoint.status`) naming whether each thread was
actually confirmed stopped, how many samples were discarded, and how long
close really took. Lesson 6 restated: a deadline nobody checks is not a bound.
Round 3 finding 4 closed the last gap in that report: a stream that raised
while being stopped/closed DURING ``close()`` used to only bump the generic
``callback_errors`` counter, leaving the fault invisible to the very report
whose whole point is "what could not be released is reported here, never
silently dropped". ``streams_close_failed`` (also added to
:class:`~embodiment.audio.endpoint.EndpointCloseReport` itself, defaulting to
``0`` since not every endpoint implementation has a local stream that can
fail this way) now sums how many of this ``close()`` call's own
stop/close attempts raised, across the playback, capture and output streams.

The resampler: anti-aliased where it counts, continuous everywhere — round 2 findings 4/5
-------------------------------------------------------------------------------------------
:class:`Resampler` replaces the old stateless linear-interpolation-only
function with two paths, chosen once per instance from the requested rates:

- **Exact integer downsample ratios** (48000 -> 24000, 96000 -> 24000, …) go
  through a windowed-sinc low-pass FIR filter (:data:`_FIR_TAPS` taps, a
  Hamming window, cutoff at the OUTPUT Nyquist frequency) before decimating.
  Finding 4: the old linear interpolator applied no filter at all, so a
  15 kHz tone at 48 kHz folded down to a false 9 kHz tone only ~2.7 dB
  quieter than the input after resampling to 24 kHz — landing squarely in
  the band the STT and the feature-extractor's waveform both read. Both the
  FIR state (the trailing raw samples a convolution needs) and the
  decimation phase (which of every M samples is next, when a chunk length
  is not a multiple of M) persist across calls, so filtering stays correct
  and continuous across arbitrarily small chunks, not just within one call.
- **Every other ratio** (upsampling, or a non-integer ratio like
  44100 -> 24000) walks a continuously advancing input-position cursor —
  ``pos = cursor; while pos <= last_valid_index: emit interpolated sample at
  pos; pos += from_rate/to_rate`` — rather than the old
  ``np.linspace(0, n_in - 1, num=n_out)``, which re-anchored its start AND
  end to every call's own local buffer regardless of true continuous
  position. Finding 5: chunking a 44.1 kHz signal into 20 ms pieces and
  resampling each independently produced a signal that measured 42.7 dBFS
  worse than resampling the whole buffer at once — the old formula stretched
  or compressed each chunk's own timeline to always land exactly on that
  chunk's own last sample, wobbling the effective time base at every chunk
  boundary. The cursor-walk has no such re-anchoring: the fractional
  position and a short trailing tail of raw samples carry across calls
  (:attr:`Resampler._pending`, :attr:`~Resampler._cursor`), so a stream fed
  in arbitrarily small pieces resamples indistinguishably from one large
  call.

**Better still, where it applies: ask the device for 24 kHz directly.**
Before falling back to the device's native rate, both
:meth:`~HostEndpoint.start_capture` and :meth:`~HostEndpoint.play` try
opening the stream AT :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ`
first. Most consumer devices accept an arbitrary requested rate (PortAudio
resamples internally, or the device genuinely supports it); when that
succeeds, NO resampling happens in this module at all, which is strictly
better than even a correct filter. Which path is actually in effect
(``"native"`` / ``"fir-decimate"`` / ``"linear"``) is recorded per direction
in :meth:`~HostEndpoint.status` (``resample_path_in``/``resample_path_out``)
— the same discipline this repo's C3 already applies to eidetic's silent
lexical-recall fallback: a host must be able to tell which mode is actually
running, not assume the better one.

``_resample_pcm16`` stays as a one-shot convenience — one code path (lesson
8): it builds a throwaway :class:`Resampler` and calls
:meth:`Resampler.process` once. A single fresh instance starts its cursor at
0 and never re-anchors to an artificial endpoint, so even this one-shot form
does not reproduce finding 5's re-anchoring bug for a single call; genuine
cross-call continuity, though, requires reusing ONE persistent instance
across calls — which is exactly what :meth:`HostEndpoint._drain_loop` and
:meth:`~HostEndpoint._writer_loop` do, each owning its own
:class:`Resampler` for the life of one open stream.

The PortAudio callback thread never blocks, never raises
------------------------------------------------------------
Unchanged from round 1: :meth:`HostEndpoint._on_input_callback` does exactly
one bounded, non-blocking thing — ``put_nowait`` onto a bounded
``queue.Queue``, dropping the OLDEST queued frame (counted) when full. This
discipline is CAPTURE-only; see the playback section above for why the same
shape was wrong for output.

Threads and shutdown (wave 1 lesson 6)
----------------------------------------
Two threads this module owns: the capture drain loop and the output writer
loop, both stopped through a ``threading.Event`` plus a bounded ``join`` whose
timeout :meth:`~HostEndpoint.close` now actually derives from its own
deadline (see above) rather than a hard-coded constant. Counters are
protected by a single ``threading.Lock`` (:attr:`HostEndpoint._counter_lock`)
rather than trusted to the GIL, since a queue-overflow storm can hit ``+=``
from two threads at once and this module wants exact counts.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from typing import Any, Callable

from embodiment.audio.endpoint import (
    CHANNELS,
    SAMPLE_RATE_HZ,
    SAMPLE_WIDTH_BYTES,
    EndpointCloseReport,
    EndpointDegradation,
    FrameCallback,
)
from embodiment.safe_reason import describe_exception

__all__ = [
    "DEGRADED_IMPORT",
    "DEGRADED_PORTAUDIO",
    "DEGRADED_ENUMERATION",
    "DEGRADED_OPEN",
    "DEGRADED_PLAYBACK_OVERFLOW",
    "DEGRADED_WRITE_FAILED",
    "Resampler",
    "HostEndpoint",
]

#: ``sounddevice`` is not installed at all.
DEGRADED_IMPORT = "audio-host-import-failed"
#: ``sounddevice`` imports, but the native PortAudio library it wraps does not.
DEGRADED_PORTAUDIO = "audio-host-portaudio-missing"
#: Device enumeration raised, or returned no devices.
DEGRADED_ENUMERATION = "audio-host-no-devices"
#: Opening the actual input or output stream raised (tracked per direction).
DEGRADED_OPEN = "audio-host-open-failed"
#: A play() chunk was refused because the playback buffer is full (round 2 finding 1).
DEGRADED_PLAYBACK_OVERFLOW = "audio-host-playback-overflow"
#: A write to the output stream raised — the device died mid-playback (round 3 finding 2).
DEGRADED_WRITE_FAILED = "audio-host-write-failed"

#: Round 3 finding 1: how long a NEW open attempt is refused after one fails,
#: starting here and doubling (capped) on each further failure. A judgement
#: call: 2 s means a genuinely transient hiccup (a device claimed for one
#: beat by another process) recovers within a couple of retries, while a
#: truly dead device stops costing an open-syscall's worth of blocking per
#: `play()` call — measured on the real device to matter: PortAudio's own
#: open call can block, and `play()` promises it never blocks the caller.
_OPEN_COOLDOWN_BASE_S = 2.0
#: Upper bound on the backoff above, so a permanently dead device settles at
#: one attempt every 30 s rather than growing without limit.
_OPEN_COOLDOWN_MAX_S = 30.0

#: Default bounded-queue depth for CAPTURE only (playback has its own,
#: seconds-based bound — see :data:`_PLAYBACK_BUFFER_SECONDS`). A judgement
#: call: at ~20 ms/block this bounds buffered mic latency to a little over a
#: second before frames start being dropped, which is short enough that a
#: stalled consumer is audible quickly rather than silently building lag.
_DEFAULT_QUEUE_MAXSIZE = 64

#: How long a drain/writer thread waits on an empty queue before checking the
#: stop signal again. Bounds shutdown latency without busy-waiting.
_POLL_INTERVAL_S = 0.1

#: Fallback device sample rate used only when the device's own
#: ``default_samplerate`` cannot be determined. 48000 Hz is the most common
#: native rate for consumer audio hardware.
_FALLBACK_DEVICE_RATE_HZ = 48000

#: How many SECONDS of audio the playback buffer holds before a NEW `play()`
#: chunk is refused (round 2 finding 1) — sized in seconds, not bytes or chunk
#: count, so it means the same thing regardless of the device's native rate
#: or how a caller chunks its input. A judgement call, stated because it is
#: one: 120 s is generous enough that no realistic reply should ever hit it,
#: and small enough (~5.76 MB of pcm16 mono at the fixed 24 kHz contract
#: rate; proportionally more at a higher native device rate, since the
#: buffer holds device-rate bytes) that a genuinely stuck writer cannot grow
#: this module's memory without bound. The limit is computed in SAMPLES at
#: whatever rate is actually being buffered — see
#: :meth:`HostEndpoint._playback_buffer_limit_samples`.
_PLAYBACK_BUFFER_SECONDS = 120.0

#: The writer never hands the device more than this much audio in one
#: `stream.write()` call (round 2 finding 2) — what makes `stop_playback()`
#: actually cut sounding audio rather than merely stop queueing more of it.
#: 20 ms is the low end of the brief's stated 20-40 ms range: tighter cut
#: latency, still comfortably larger than typical PortAudio callback periods.
_WRITE_SLICE_MS = 20

#: Windowed-sinc FIR low-pass filter length for integer-ratio decimation
#: (round 2 finding 4). 129 (odd, so there is a single centre tap and an
#: integer group delay of 64 samples — ~1.33 ms at 48 kHz, negligible for
#: speech) with a Hamming window gives roughly 53 dB of stopband
#: attenuation, comfortably past the >=40 dB the probe measures at 15 kHz
#: after 48k -> 24k. A judgement call: more taps would reject more, at more
#: CPU per chunk; untested past this repo's own measured numbers.
_FIR_TAPS = 129

#: How many events (mute changes, per-direction recovery) this module keeps
#: in the retained log before evicting the oldest (round 2 finding 6a). The
#: EXACT count of genuine mute transitions is tracked separately
#: (`HostEndpoint._mute_event_count`) and is never capped — only the
#: detailed per-event log has a memory bound.
_MAX_RETAINED_EVENTS = 1000


def _import_sounddevice() -> Any:
    """The ONE place ``import sounddevice`` is spelled out (never at module scope).

    Tests monkeypatch :class:`HostEndpoint`'s ``sounddevice_importer``
    constructor argument (default: this function) to inject a fake
    ``sounddevice``-shaped module or to simulate ``ImportError``/``OSError``,
    without ever needing the real package or PortAudio installed.
    """
    import sounddevice

    return sounddevice


def _import_numpy() -> Any:
    """Lazy numpy import point, mirrored for the same reason as ``sounddevice``'s above."""
    import numpy

    return numpy


def _classify_resample_path(from_rate: int, to_rate: int) -> str:
    """``"native"`` / ``"fir-decimate"`` / ``"linear"`` — one classifier, two callers.

    Shared by :class:`Resampler` (to decide ITS OWN algorithm) and
    :class:`HostEndpoint` (to report which one is in effect), so the two
    can never silently disagree (wave 1 lesson 8).
    """
    if from_rate == to_rate:
        return "native"
    if from_rate > to_rate and to_rate > 0 and from_rate % to_rate == 0:
        return "fir-decimate"
    return "linear"


def _windowed_sinc_lowpass(np: Any, taps: int, cutoff_hz: float, sample_rate_hz: float) -> Any:
    """A normalised, Hamming-windowed sinc low-pass FIR kernel.

    ``cutoff_hz`` is the frequency the filter passes; ``sample_rate_hz`` is
    the rate the FIR operates at (the INPUT rate, before decimation). Unity
    DC gain (the kernel sums to 1) so a steady input level is preserved.
    """
    fc = cutoff_hz / sample_rate_hz  # normalised cutoff, fraction of sample_rate_hz
    m = taps - 1
    n = np.arange(taps, dtype=np.float64)
    kernel = 2.0 * fc * np.sinc(2.0 * fc * (n - m / 2.0))
    window = np.hamming(taps)
    kernel = kernel * window
    total = np.sum(kernel)
    if total != 0:
        kernel = kernel / total
    return kernel


class Resampler:
    """A stateful, per-direction resampler (round 2 findings 4 and 5).

    ONE instance per direction: the capture drain thread owns one, the
    playback writer owns another. Never share an instance across threads or
    between unrelated streams — its internal state (the FIR history, or the
    carried fractional cursor) describes ONE continuous audio stream and is
    meaningless shared between two.

    - ``from_rate == to_rate``: identity, a bytes passthrough.
    - An exact integer downsample ratio (``from_rate > to_rate`` and
      ``from_rate % to_rate == 0``): windowed-sinc FIR low-pass, then
      decimate, both with state carried across calls.
    - Everything else (upsampling, or a non-integer ratio): a continuously
      advancing linear-interpolation cursor, carried across calls.

    :func:`_resample_pcm16` is a thin one-shot wrapper over a throwaway
    instance — the only resampling code path in this module (lesson 8).
    """

    __slots__ = (
        "_from_rate",
        "_to_rate",
        "_np_importer",
        "_path",
        "_ratio",
        "_kernel",
        "_fir_state",
        "_decim_phase",
        "_pending",
        "_cursor",
    )

    def __init__(self, from_rate: int, to_rate: int, numpy_importer: Callable[[], Any]) -> None:
        self._from_rate = from_rate
        self._to_rate = to_rate
        self._np_importer = numpy_importer
        self._path = _classify_resample_path(from_rate, to_rate)
        self._ratio = int(from_rate // to_rate) if self._path == "fir-decimate" else 0
        self._kernel: Any = None
        self._fir_state: Any = None
        self._decim_phase = 0
        self._pending: Any = None
        self._cursor = 0.0

    @property
    def path(self) -> str:
        return self._path

    def process(self, pcm_bytes: bytes, *, flush: bool = False) -> bytes:
        """Resample one chunk. Never raises: malformed/degenerate input degrades to ``b""``."""
        if not pcm_bytes or self._from_rate <= 0 or self._to_rate <= 0:
            return b""
        usable = pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % SAMPLE_WIDTH_BYTES)]
        if not usable:
            return b""
        if self._path == "native":
            return bytes(usable)

        np = self._np_importer()
        new_samples = np.frombuffer(usable, dtype="<i2").astype(np.float64)
        if self._path == "fir-decimate":
            return self._process_fir(np, new_samples)
        return self._process_linear(np, new_samples, flush=flush)

    def _process_fir(self, np: Any, new_samples: Any) -> bytes:
        n = int(new_samples.shape[0])
        if n == 0:
            return b""
        taps = _FIR_TAPS
        if self._kernel is None:
            # Cutoff at the OUTPUT Nyquist frequency: everything above it
            # would alias once decimated, so it is what the filter must
            # reject (round 2 finding 4).
            cutoff_hz = self._to_rate / 2.0
            self._kernel = _windowed_sinc_lowpass(np, taps, cutoff_hz, float(self._from_rate))
        if self._fir_state is None:
            self._fir_state = np.zeros(taps - 1, dtype=np.float64)

        combined = np.concatenate([self._fir_state, new_samples])
        filtered = np.convolve(combined, self._kernel, mode="valid")  # length == n

        ratio = self._ratio
        idx = np.arange(self._decim_phase, n, ratio)
        out = filtered[idx] if idx.size else np.array([], dtype=np.float64)
        self._decim_phase = int(idx[-1] + ratio - n) if idx.size else int(self._decim_phase - n)
        self._fir_state = combined[-(taps - 1) :] if taps > 1 else combined[0:0]

        out = np.clip(np.round(out), -32768, 32767).astype("<i2")
        return out.tobytes()

    def _process_linear(self, np: Any, new_samples: Any, *, flush: bool) -> bytes:
        pending = self._pending if self._pending is not None else np.array([], dtype=np.float64)
        combined = np.concatenate([pending, new_samples])
        length = int(combined.shape[0])
        if length == 0:
            return b""
        max_pos = length - 1
        ratio = self._from_rate / self._to_rate

        positions: list[float] = []
        pos = self._cursor
        while pos <= max_pos:
            positions.append(pos)
            pos += ratio

        if positions:
            pos_arr = np.array(positions, dtype=np.float64)
            idx0 = np.clip(np.floor(pos_arr).astype(np.int64), 0, max_pos)
            idx1 = np.clip(idx0 + 1, 0, max_pos)
            frac = np.where(idx1 > idx0, pos_arr - idx0, 0.0)
            out = combined[idx0] * (1.0 - frac) + combined[idx1] * frac
        else:
            out = np.array([], dtype=np.float64)

        if flush:
            self._pending = None
            self._cursor = 0.0
        else:
            # Keep a short trailing tail so the next call can continue the
            # SAME continuous cursor walk without re-anchoring (finding 5).
            keep_n = min(length, max(2, math.ceil(ratio) + 2))
            keep_start = max(0, length - keep_n)
            self._pending = combined[keep_start:]
            self._cursor = pos - keep_start

        out = np.clip(np.round(out), -32768, 32767).astype("<i2")
        return out.tobytes()


def _resample_pcm16(
    pcm_bytes: bytes, from_rate: int, to_rate: int, numpy_importer: Callable[[], Any]
) -> bytes:
    """One-shot resample: a thin wrapper over a throwaway :class:`Resampler` (lesson 8).

    For genuine cross-call continuity (a real device feeding chunks over
    time), construct and reuse ONE :class:`Resampler` instance instead — this
    function starts fresh every call.
    """
    return Resampler(from_rate, to_rate, numpy_importer).process(pcm_bytes, flush=True)


class HostEndpoint:
    """``sounddevice``-backed :class:`~embodiment.audio.endpoint.AudioEndpoint`.

    Args:
        input_device: forwarded to ``sounddevice`` as the input ``device=``
            argument. ``None`` selects the system default.
        output_device: as ``input_device``, for output.
        queue_maxsize: bounded CAPTURE queue depth (see
            :data:`_DEFAULT_QUEUE_MAXSIZE`). Playback has its own,
            seconds-based bound (:data:`_PLAYBACK_BUFFER_SECONDS`).
        sounddevice_importer: zero-argument callable returning the
            ``sounddevice``-shaped module. Defaults to
            :func:`_import_sounddevice`; tests pass a fake or a raiser.
        numpy_importer: as ``sounddevice_importer``, for numpy.
    """

    def __init__(
        self,
        *,
        input_device: object = None,
        output_device: object = None,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
        sounddevice_importer: Callable[[], Any] = _import_sounddevice,
        numpy_importer: Callable[[], Any] = _import_numpy,
    ) -> None:
        self._input_device = input_device
        self._output_device = output_device
        self._queue_maxsize = queue_maxsize
        self._sd_importer = sounddevice_importer
        self._np_importer = numpy_importer

        # RLock, not Lock: round 3 needs a nested acquisition (the overflow
        # path records an event, under the same lock that guards the
        # playback-queue decision, from inside an already-held lock).
        self._counter_lock = threading.RLock()
        self._muted = False
        self._events: "deque[dict[str, object]]" = deque(maxlen=_MAX_RETAINED_EVENTS)
        self._mute_event_count = 0
        self._attached = False
        self._capturing = False
        self._closed = False
        self._on_frame: FrameCallback | None = None

        self._sd: Any = None
        self._device_rate_in: int | None = None
        self._device_rate_out: int | None = None
        self._resample_path_in: str | None = None
        self._resample_path_out: str | None = None
        self._resampler_in: Resampler | None = None
        self._resampler_out: Resampler | None = None

        self._input_stream: Any = None
        self._capture_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=queue_maxsize)
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()

        self._output_stream: Any = None
        self._playback_chunks: "deque[bytes]" = deque()
        self._playback_queued_bytes = 0
        self._playback_active_remaining_bytes = 0
        self._playback_written_samples = 0
        self._playback_total_pushed_samples = 0
        self._playback_stop_discarded_total = 0
        self._playback_overflow_count = 0
        self._playback_generation = 0
        self._playing = False
        self._writer_thread: threading.Thread | None = None
        self._writer_stop = threading.Event()

        self._capture_dropped = 0
        self._capture_muted_dropped = 0
        self._callback_errors = 0
        self._native_rate_declined_in = 0
        self._native_rate_declined_out = 0

        self._degradation: EndpointDegradation | None = self._probe()
        self._degradation_in: EndpointDegradation | None = None
        self._degradation_out: EndpointDegradation | None = None
        self._last_close_report: EndpointCloseReport | None = None

        # round 3 finding 1: a cooldown before retrying a dead OUTPUT device,
        # so `play()` never attempts (and potentially blocks on) an open on
        # every call. Scoped to output only: capture's start_capture() is
        # called rarely (daemon startup, or a deliberate manual retry), never
        # from a per-chunk hot loop, so it keeps round 2's simpler
        # retry-once-per-call behaviour.
        self._out_open_cooldown_until = 0.0
        self._out_open_backoff_s = _OPEN_COOLDOWN_BASE_S
        self._output_degrade_attempts = 0
        self._playback_dropped_no_device = 0
        self._playback_overflow_episode_active = False

    # -- construction-time probe (never raises) --------------------------

    def _probe(self) -> EndpointDegradation | None:
        try:
            sd = self._sd_importer()
        except OSError as exc:
            return EndpointDegradation(
                DEGRADED_PORTAUDIO,
                f"PortAudio native library unavailable: {describe_exception(exc)}",
            )
        except ImportError as exc:
            return EndpointDegradation(
                DEGRADED_IMPORT, f"sounddevice is not installed: {describe_exception(exc)}"
            )
        except Exception as exc:
            return EndpointDegradation(
                DEGRADED_IMPORT, f"sounddevice import raised: {describe_exception(exc)}"
            )

        try:
            devices = sd.query_devices()
        except Exception as exc:
            return EndpointDegradation(
                DEGRADED_ENUMERATION, f"device enumeration raised: {describe_exception(exc)}"
            )
        if not devices:
            return EndpointDegradation(DEGRADED_ENUMERATION, "no audio devices found")

        self._sd = sd
        return None

    def _query_default_rate(self, device: object) -> int | None:
        """The device's own native rate, or ``None`` (recorded) when it cannot be read."""
        if self._sd is None:
            return None
        try:
            info = self._sd.query_devices(device)
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            return None
        try:
            rate = info["default_samplerate"] if isinstance(info, dict) else None
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            rate = None
        if not rate:
            return None
        try:
            return int(round(float(rate)))
        except (TypeError, ValueError):
            return None

    def _record_event(self, event: dict[str, object]) -> None:
        with self._counter_lock:
            self._events.append(event)
            if event.get("type") == "mute":
                self._mute_event_count += 1

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
        if self._output_stream is not None:
            self._safe_stream_close(self._output_stream)
            self._output_stream = None
        self._attached = False

    def close(self, deadline: float) -> EndpointCloseReport:
        deadline = max(0.0, float(deadline))
        start = time.monotonic()

        samples_discarded, playback_close_failures = self._stop_playback_internal()

        remaining = max(0.0, deadline - (time.monotonic() - start))
        capture_stopped, capture_close_failures = self._stop_capture(timeout=remaining / 2.0)

        remaining = max(0.0, deadline - (time.monotonic() - start))
        writer_stopped = self._stop_writer(timeout=remaining)

        output_close_failures = 0
        if self._output_stream is not None:
            output_close_failures = self._safe_stream_close(self._output_stream)
            self._output_stream = None

        self._attached = False
        self._closed = True
        elapsed = time.monotonic() - start
        report = EndpointCloseReport(
            capture_thread_stopped=capture_stopped,
            writer_thread_stopped=writer_stopped,
            samples_discarded=samples_discarded,
            elapsed_s=elapsed,
            streams_close_failed=(
                playback_close_failures + capture_close_failures + output_close_failures
            ),
        )
        self._last_close_report = report
        return report

    # -- capture -------------------------------------------------------

    def _open_input_stream(self) -> tuple[Any, int, str]:
        """Try the fixed 24 kHz contract rate first; fall back to the device's own rate.

        When the 24 kHz open succeeds, NO resampling happens anywhere in this
        module — strictly better than even a correct filter (round 2
        finding 4).
        """
        try:
            stream = self._sd.InputStream(
                samplerate=SAMPLE_RATE_HZ,
                channels=CHANNELS,
                dtype="int16",
                device=self._input_device,
                callback=self._on_input_callback,
            )
            stream.start()
            return stream, SAMPLE_RATE_HZ, "native"
        except Exception:
            # Not a fault: many devices simply decline an arbitrary requested
            # rate. Recorded (C3) rather than a bare `except: pass`, so a host
            # can see how often the native-24kHz-first attempt is declined —
            # see `resample_path_in` in status() for which path actually ran.
            with self._counter_lock:
                self._native_rate_declined_in += 1

        rate = self._query_default_rate(self._input_device) or _FALLBACK_DEVICE_RATE_HZ
        stream = self._sd.InputStream(
            samplerate=rate,
            channels=CHANNELS,
            dtype="int16",
            device=self._input_device,
            callback=self._on_input_callback,
        )
        stream.start()
        return stream, rate, _classify_resample_path(rate, SAMPLE_RATE_HZ)

    def start_capture(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        if self._degradation is not None or self._closed:
            return
        if self._capturing:
            return

        was_degraded = self._degradation_in is not None
        try:
            stream, rate, path = self._open_input_stream()
        except Exception as exc:
            self._degradation_in = EndpointDegradation(
                DEGRADED_OPEN, f"input device open failed: {describe_exception(exc)}"
            )
            return

        if was_degraded:
            self._degradation_in = None
            self._record_event({"type": "recovered", "direction": "in"})

        self._device_rate_in = rate
        self._resample_path_in = path
        self._resampler_in = Resampler(rate, SAMPLE_RATE_HZ, self._np_importer)
        self._input_stream = stream
        self._capture_queue = queue.Queue(maxsize=self._queue_maxsize)
        self._capture_stop.clear()
        self._capture_thread = threading.Thread(
            target=self._drain_loop, name="embodiment-audio-host-capture", daemon=True
        )
        self._capture_thread.start()
        self._capturing = True
        self._attached = True

    def stop_capture(self) -> None:
        self._stop_capture(timeout=2.0)

    def _stop_capture(self, timeout: float) -> tuple[bool, int]:
        self._capture_stop.set()
        thread = self._capture_thread
        self._capture_thread = None
        stopped = True
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
            stopped = not thread.is_alive()
        stream = self._input_stream
        self._input_stream = None
        close_failures = 0
        if stream is not None:
            close_failures = self._safe_stream_close(stream)
        self._capturing = False
        return stopped, close_failures

    def _on_input_callback(
        self, indata: object, frames: int, time_info: object, status: object
    ) -> None:
        """PortAudio's own thread. Never blocks, never raises (see module docstring)."""
        try:
            raw = bytes(indata)
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            return
        q = self._capture_queue
        try:
            q.put_nowait(raw)
            return
        except queue.Full:
            pass
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            return
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        with self._counter_lock:
            self._capture_dropped += 1
        try:
            q.put_nowait(raw)
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1

    def _drain_loop(self) -> None:
        """This module's own thread: resample, enforce mute, deliver. Never raises out."""
        while not self._capture_stop.is_set():
            try:
                raw = self._capture_queue.get(timeout=_POLL_INTERVAL_S)
            except queue.Empty:
                continue
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                continue

            if self._muted:
                with self._counter_lock:
                    self._capture_muted_dropped += 1
                continue

            resampler = self._resampler_in
            try:
                resampled = resampler.process(raw) if resampler is not None else raw
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1
                continue

            callback = self._on_frame
            if callback is None:
                continue
            try:
                callback(resampled)
            except Exception:
                with self._counter_lock:
                    self._callback_errors += 1

    # -- playback --------------------------------------------------------

    def _open_output_stream(self) -> tuple[Any, int, str]:
        """As :meth:`_open_input_stream`, preferring the fixed 24 kHz rate."""
        try:
            stream = self._sd.OutputStream(
                samplerate=SAMPLE_RATE_HZ,
                channels=CHANNELS,
                dtype="int16",
                device=self._output_device,
            )
            stream.start()
            return stream, SAMPLE_RATE_HZ, "native"
        except Exception:
            # Same not-a-fault fallback as `_open_input_stream` — recorded.
            with self._counter_lock:
                self._native_rate_declined_out += 1

        rate = self._query_default_rate(self._output_device) or _FALLBACK_DEVICE_RATE_HZ
        stream = self._sd.OutputStream(
            samplerate=rate,
            channels=CHANNELS,
            dtype="int16",
            device=self._output_device,
        )
        stream.start()
        return stream, rate, _classify_resample_path(SAMPLE_RATE_HZ, rate)

    def _enter_output_degradation(self, code: str, exc: BaseException, action: str) -> None:
        """Round 3 findings 1/2: one recorded episode + a cooldown, for open OR write failures.

        Shared by :meth:`play`'s open path and :meth:`_handle_write_failure`
        (lesson 8 — one code path for "the output direction just broke"): an
        episode-start event is appended only on the FIRST failure since the
        last recovery, the exact attempt count is tracked separately
        (:attr:`_output_degrade_attempts`, never capped), and the retry
        cooldown backs off exponentially so a permanently dead device costs
        this module one open (or, for a write failure, one already-attempted
        write) per backoff period rather than one per `play()` call.
        """
        is_new_episode = self._degradation_out is None
        reason = f"{action}: {describe_exception(exc)}"
        self._degradation_out = EndpointDegradation(code, reason)
        if is_new_episode:
            self._record_event({"type": "degraded", "code": code, "direction": "out"})
        with self._counter_lock:
            self._output_degrade_attempts += 1
        self._out_open_cooldown_until = time.monotonic() + self._out_open_backoff_s
        self._out_open_backoff_s = min(_OPEN_COOLDOWN_MAX_S, self._out_open_backoff_s * 2.0)

    def play(self, frames: bytes) -> None:
        if not isinstance(frames, (bytes, bytearray)):
            return
        if self._degradation is not None or self._closed:
            return

        if self._output_stream is None:
            if (
                self._degradation_out is not None
                and time.monotonic() < self._out_open_cooldown_until
            ):
                # Round 3 finding 1: a dead device does NOT get retried (and
                # potentially block the caller) on every single play() call —
                # every chunk offered while cooling down is counted, never
                # silently dropped.
                with self._counter_lock:
                    self._playback_dropped_no_device += 1
                return

            was_degraded = self._degradation_out is not None
            try:
                stream, rate, path = self._open_output_stream()
            except Exception as exc:
                self._enter_output_degradation(DEGRADED_OPEN, exc, "output device open failed")
                with self._counter_lock:
                    self._playback_dropped_no_device += 1
                return
            if was_degraded:
                self._degradation_out = None
                self._out_open_backoff_s = _OPEN_COOLDOWN_BASE_S
                self._record_event({"type": "recovered", "direction": "out"})
            self._device_rate_out = rate
            self._resample_path_out = path
            self._resampler_out = Resampler(SAMPLE_RATE_HZ, rate, self._np_importer)
            self._output_stream = stream
            self._start_writer()
            self._attached = True

        resampler = self._resampler_out
        try:
            resampled = resampler.process(bytes(frames)) if resampler is not None else bytes(frames)
        except Exception:
            with self._counter_lock:
                self._callback_errors += 1
            return
        if not resampled:
            return

        chunk_bytes = len(resampled)
        limit_samples = self._playback_buffer_limit_samples()
        with self._counter_lock:
            if self._output_stream is None:
                # A concurrent stop_playback()/write failure closed the
                # stream while this call was resampling above: this re-check,
                # done under the SAME lock that governs both the queue and
                # the stream reference, is what closes the play()/
                # stop_playback() interleaving the round 3 review raised — a
                # chunk is never silently queued onto a device that no
                # longer exists.
                self._playback_dropped_no_device += 1
                return
            in_flight = (
                self._playback_queued_bytes + self._playback_active_remaining_bytes
            ) // SAMPLE_WIDTH_BYTES
            if in_flight + chunk_bytes // SAMPLE_WIDTH_BYTES > limit_samples:
                # Refuse the NEW chunk only — everything already buffered is
                # untouched (round 2 finding 1: never silently eat a sentence).
                if not self._playback_overflow_episode_active:
                    self._playback_overflow_episode_active = True
                    self._record_event(
                        {"type": "degraded", "code": DEGRADED_PLAYBACK_OVERFLOW, "direction": "out"}
                    )
                self._playback_overflow_count += 1
                return
            self._playback_overflow_episode_active = False
            self._playback_chunks.append(resampled)
            self._playback_queued_bytes += chunk_bytes
            self._playback_total_pushed_samples += chunk_bytes // SAMPLE_WIDTH_BYTES
            self._playing = True

    def stop_playback(self) -> int:
        """Barge-in (round 2 finding 2): discard queued + in-flight audio; cut what sounds."""
        discarded, _close_failures = self._stop_playback_internal()
        return discarded

    def _stop_playback_internal(self) -> tuple[int, int]:
        """As :meth:`stop_playback`, also reporting stream close failures (round 3 finding 4).

        Round 3's not-reproduced follow-up measured 345 stream opens in a
        2 s play()/stop_playback() stress run, because every barge-in used to
        close the output stream unconditionally and the next `play()` had to
        reopen it. The stream is now kept open across a barge-in whenever it
        offers a cheap ``abort()`` that succeeds — only a device with no
        abort support, or whose abort itself raises, pays for a close and a
        cooldown-gated reopen (round 3 finding 1) on its NEXT `play()` call.
        """
        with self._counter_lock:
            queued_samples = self._playback_queued_bytes // SAMPLE_WIDTH_BYTES
            active_samples = self._playback_active_remaining_bytes // SAMPLE_WIDTH_BYTES
            discarded = queued_samples + active_samples
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._playback_active_remaining_bytes = 0
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
            # The stream reference is nulled UNCONDITIONALLY, in THE SAME
            # lock acquisition as the generation bump above — this is what
            # closes the race a concurrent play() could otherwise hit (read
            # `self._output_stream` as non-None, then raced to append a
            # chunk to it after this call moved on): play()'s own re-check,
            # a few lines below in `play()`, happens under this same lock.
            # Reinstating the SAME object below (if abort succeeds) is a
            # SEPARATE, later lock acquisition, guarded so it can never
            # clobber a stream a concurrent play() opened in between.
            stream = self._output_stream
            self._output_stream = None

        close_failures = 0
        if stream is not None:
            abort = getattr(stream, "abort", None)
            aborted = False
            if callable(abort):
                try:
                    abort()
                    aborted = True
                except Exception:
                    with self._counter_lock:
                        self._callback_errors += 1
            if aborted:
                with self._counter_lock:
                    if self._output_stream is None:
                        self._output_stream = stream
            else:
                close_failures = self._safe_stream_close(stream)
        return discarded, close_failures

    def _handle_write_failure(self, exc: BaseException) -> None:
        """Round 3 finding 2: a dead device mid-playback is never silent.

        Closes and drops the stream, discards whatever was still buffered
        for it (a device that just failed a write is not a device to keep
        queueing for), and records :data:`DEGRADED_WRITE_FAILED` through the
        same episode/cooldown mechanism as an open failure — so the next
        `play()` goes through :meth:`play`'s cooldown-gated reopen rather
        than hammering a dead device again.
        """
        with self._counter_lock:
            stream = self._output_stream
            self._output_stream = None
            discarded = (
                self._playback_queued_bytes + self._playback_active_remaining_bytes
            ) // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._playback_active_remaining_bytes = 0
            self._playback_stop_discarded_total += discarded
            self._playback_generation += 1
            self._playing = False
        if stream is not None:
            self._safe_stream_close(stream)
        self._enter_output_degradation(DEGRADED_WRITE_FAILED, exc, "output write failed")

    @property
    def playing(self) -> bool:
        return self._playing

    def _playback_buffer_limit_samples(self) -> int:
        """The playback bound in SAMPLES at the rate actually being buffered.

        The buffer holds resampled, device-rate bytes, so the byte bound must
        be computed at the device's own rate to mean the stated
        :data:`_PLAYBACK_BUFFER_SECONDS` regardless of what that rate is.
        """
        rate = self._device_rate_out or SAMPLE_RATE_HZ
        return int(_PLAYBACK_BUFFER_SECONDS * rate)

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
        while not self._writer_stop.is_set():
            with self._counter_lock:
                try:
                    chunk = self._playback_chunks.popleft()
                except IndexError:
                    chunk = None
                if chunk is not None:
                    self._playback_queued_bytes -= len(chunk)
                    self._playback_active_remaining_bytes = len(chunk)
                gen = self._playback_generation

            if chunk is None:
                time.sleep(_POLL_INTERVAL_S)
                continue

            self._write_chunk_sliced(chunk, gen)

            with self._counter_lock:
                self._playback_active_remaining_bytes = 0
                self._playing = bool(self._playback_chunks) or (
                    self._playback_active_remaining_bytes > 0
                )

    def _write_chunk_sliced(self, chunk: bytes, gen: int) -> None:
        """Write one queued chunk in small slices, checking for a cut every slice.

        This is what makes :meth:`stop_playback` an actual barge-in cut
        rather than a mere stop-queueing (round 2 finding 2): at most one
        slice's worth of audio can still be sounding after a stop request.
        """
        rate = self._device_rate_out or SAMPLE_RATE_HZ
        slice_samples = max(1, int(rate * _WRITE_SLICE_MS / 1000.0))
        slice_bytes = slice_samples * SAMPLE_WIDTH_BYTES

        offset = 0
        n = len(chunk)
        while offset < n:
            if self._writer_stop.is_set() or self._playback_generation != gen:
                return
            end = min(offset + slice_bytes, n)
            piece = chunk[offset:end]
            stream = self._output_stream
            if stream is None:
                # Defensive only: `_output_stream` and `_playback_generation`
                # always change together (see `_stop_playback_internal` and
                # `_handle_write_failure`), so a matching generation implies a
                # live stream reference — this branch should be unreachable,
                # but a nullable value is never trusted blindly (lesson 3).
                with self._counter_lock:
                    self._callback_errors += 1
                return
            try:
                stream.write(piece)
            except Exception as exc:
                self._handle_write_failure(exc)
                return
            offset = end
            with self._counter_lock:
                self._playback_written_samples += len(piece) // SAMPLE_WIDTH_BYTES
                self._playback_active_remaining_bytes = n - offset

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

    # -- introspection -----------------------------------------------------

    def _safe_stream_close(self, stream: Any) -> int:
        """Stop and close *stream*, never raising. Returns how many of the two raised.

        Round 3 finding 4: previously this only bumped ``callback_errors``,
        so a stream that failed to stop/close cleanly was invisible to
        :class:`~embodiment.audio.endpoint.EndpointCloseReport`. Callers that
        are building a close report sum this return value into
        ``streams_close_failed``; callers that are not (``detach()``, a
        mid-life `stop_playback()`) still get the ``callback_errors`` bump,
        unchanged.
        """
        failures = 0
        try:
            stream.stop()
        except Exception:
            failures += 1
            with self._counter_lock:
                self._callback_errors += 1
        try:
            stream.close()
        except Exception:
            failures += 1
            with self._counter_lock:
                self._callback_errors += 1
        return failures

    def status(self) -> dict[str, object]:
        with self._counter_lock:
            counters = {
                "capture_dropped": self._capture_dropped,
                "capture_muted_dropped": self._capture_muted_dropped,
                "callback_errors": self._callback_errors,
                "native_rate_declined_in": self._native_rate_declined_in,
                "native_rate_declined_out": self._native_rate_declined_out,
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
            "degradation": self._degradation.to_dict() if self._degradation else None,
            "degradation_in": self._degradation_in.to_dict() if self._degradation_in else None,
            "degradation_out": self._degradation_out.to_dict() if self._degradation_out else None,
            "device_rate_in_hz": self._device_rate_in,
            "device_rate_out_hz": self._device_rate_out,
            "resample_path_in": self._resample_path_in,
            "resample_path_out": self._resample_path_out,
            "close_report": self._last_close_report.to_dict() if self._last_close_report else None,
            **counters,
        }
