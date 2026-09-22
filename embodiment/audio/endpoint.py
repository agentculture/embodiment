"""The ``AudioEndpoint`` seam: one interface for "ears and a mouth" (plan task ``t7``).

This module defines a shape, not a device. :class:`AudioEndpoint` is a
:class:`typing.Protocol` that any concrete "how does audio actually reach and
leave this process" implementation satisfies: a host loudspeaker/microphone
pair (:mod:`embodiment.audio.host`, subprocess-driven — ``pw-record``/
``pw-play`` or ``arecord``/``aplay``, deviation d4), a browser speaking over a
websocket, a robot's relay. Nothing here imports a real audio library, opens a
socket, spawns a process, or touches a device — that keeps this module free to
be imported by :mod:`embodiment.turn` and the daemon composition root without
either one paying for, or depending on, any of it.

The contract every implementation must meet
--------------------------------------------
- **Playback is fixed and never negotiated**: :meth:`AudioEndpoint.play`
  always takes pcm16, mono, 24000 Hz, little-endian bytes — the same format
  :mod:`embodiment.audio.features` contracts against (task ``t8``). An
  implementation whose device wants a different native rate resamples (or,
  as :mod:`embodiment.audio.host` does, lets ALSA/pipewire resample) at its
  own boundary; nothing above this interface ever sees another playback rate.
- **Capture's rate is READ, never assumed — decision 15 (operator, issue
  #85).** :meth:`AudioEndpoint.start_capture` delivers pcm16 mono frames at
  whatever rate :attr:`AudioEndpoint.sample_rate` reports, which need not be
  24000 Hz: the reSpeaker XVF3800 sends its NATIVE 16 kHz straight through
  (:mod:`embodiment.audio.host` reports 16000), because resampling it up to
  a historical 24 kHz assumption in Python bought nothing the daemon's own
  realtime session (``input_sample_rate``) cannot already declare. This
  superseded the original single-fixed-rate criterion for BOTH directions;
  see :mod:`embodiment.audio.host`'s module docstring for the full
  reasoning. A caller reads ``sample_rate`` once, before or right after
  :meth:`~AudioEndpoint.start_capture`, and passes it on — never hard-codes
  24000 for the capture direction.
- **Capture is push, not pull.** :meth:`AudioEndpoint.start_capture` takes a
  callback; the endpoint calls it with frames as they arrive. There is no
  polling method, because an endpoint that has to be polled either drops
  frames between polls or has to buffer unboundedly, and this interface
  refuses to specify which.
- **Mute is a capture-path property, not a filter someone downstream
  applies.** :meth:`AudioEndpoint.mute` is part of this interface — not a
  wrapper the caller composes on top of ``start_capture`` — precisely because
  the point of the daemon's mute rule (plan obligation ``o8``: a muted daemon
  sends no audio to the gateway) is that dropping happens *inside* the
  endpoint, before any frame reaches the callback the daemon registered. An
  implementation that instead filtered frames after receiving them from some
  unmuted lower layer would have already handed a filterable stream to
  whatever called ``start_capture`` — trusting a caller not to look is not the
  same guarantee as the frames never having left the capture boundary.
- **One active ear at a time is the daemon's rule, not this interface's.**
  :meth:`AudioEndpoint.attach`/:meth:`~AudioEndpoint.detach` exist so an
  endpoint can hold and release whatever exclusive resource it owns (a device
  handle, a socket) on the daemon's schedule; the *policy* of only ever having
  one endpoint attached lives in whatever composes endpoints, not here. This
  interface's only obligation is that ``detach`` returns with the resource
  actually released, promptly, so a caller enforcing "one at a time" is not
  left holding a device the previous ear never gave back.
- **Never raises.** Every method degrades to a recorded
  :class:`EndpointDegradation` (surfaced through :meth:`AudioEndpoint.status`)
  rather than raising — an audio endpoint sits on the same "no-voice, never a
  raise" footing as the rest of this package's degrade-and-record discipline
  (C3). :class:`NullEndpoint` is the concrete, always-successful instance of
  that footing: a host with no configured hardware and no fallback still gets
  a real object that satisfies the protocol, so callers never special-case
  ``None``.
- **Shutdown is a feature.** :meth:`AudioEndpoint.close` takes a deadline,
  never raises, is idempotent, and returns having released everything it can
  within that deadline — returning an :class:`EndpointCloseReport` (also
  mirrored in :meth:`~AudioEndpoint.status`) that says exactly what it left
  unfinished, rather than silently leaving a thread or device parked open
  (round 2 finding 3: a deadline that is accepted but not honoured, and a
  join whose outcome nobody checks, is not a bound at all).
- **Barge-in needs a way to cut playback, not just stop queueing more of
  it.** :meth:`AudioEndpoint.stop_playback` discards everything queued *and*
  cuts what is currently sounding, returning how many 24 kHz samples were
  discarded. This is a separate verb from :meth:`~AudioEndpoint.close`
  because barge-in happens mid-conversation, arbitrarily often, while the
  endpoint stays attached and ready for the next reply — closing and
  reopening the whole endpoint on every interruption would be the wrong
  granularity (round 2 finding 2).

What this module deliberately does not do
-------------------------------------------
No concrete implementation lives here — see :mod:`embodiment.audio.host` for
the first one. No resampling, no threading, no queueing: those are
implementation concerns a concrete endpoint owns. No opinion on transport
(local device vs. websocket vs. robot relay) — the whole point of a Protocol
is that :mod:`embodiment.turn` and the daemon depend on this module only, so a
future endpoint can be swapped in without touching either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

__all__ = [
    "SAMPLE_RATE_HZ",
    "SAMPLE_WIDTH_BYTES",
    "CHANNELS",
    "EndpointDegradation",
    "EndpointCloseReport",
    "FrameCallback",
    "AudioEndpoint",
    "NullEndpoint",
    "DEGRADED_NO_ENDPOINT",
]

#: The one frame format every :class:`AudioEndpoint` speaks, in and out.
#: Matches :data:`embodiment.audio.features.SAMPLE_RATE_HZ`.
SAMPLE_RATE_HZ = 24000

#: pcm16 == 2 bytes/sample.
SAMPLE_WIDTH_BYTES = 2

#: Mono only. A stereo endpoint downmixes at its own boundary.
CHANNELS = 1

#: :meth:`NullEndpoint.status`'s degradation code: there is no real ear or
#: mouth behind this instance. Named so a host inspecting ``status()`` can
#: tell "endpoint present but degraded" apart from "no endpoint was ever
#: configured", which is exactly what :class:`NullEndpoint` stands in for.
DEGRADED_NO_ENDPOINT = "endpoint-null"


@dataclass(frozen=True)
class EndpointDegradation:
    """One recorded, host-visible transition (C3) when an endpoint stops working.

    ``code`` names the fault a host would look for (module-name-prefixed by
    convention — see concrete endpoints for their own prefixes);  ``reason``
    is a short, speech-free description (never audio content, never a device
    name pulled from unchecked user input).
    """

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class EndpointCloseReport:
    """What :meth:`AudioEndpoint.close` actually managed within its deadline (C3/lesson 6).

    ``capture_thread_stopped``/``writer_thread_stopped`` are ``True`` only
    when that thread was confirmed stopped (or was never running) before the
    deadline elapsed — never assumed. ``samples_discarded`` is what
    :meth:`~AudioEndpoint.stop_playback` reported when ``close`` invoked it as
    its first step. ``elapsed_s`` is the real time ``close`` actually took, so
    a caller can tell "honoured the deadline" from "ran over it and gave up
    anyway" — both are honest outcomes; only the second is the same object
    silently pretending it did not happen. ``streams_close_failed`` (round 3)
    is how many of this endpoint's underlying streams raised while being
    stopped/closed DURING this ``close`` call — previously that only bumped a
    generic error counter, against this dataclass's own "what could not be is
    reported there" promise. ``children_unreaped`` (round 7b) is how many
    child processes this ``close`` could not confirm dead even after
    escalating to SIGKILL — left as zombies until a LATER stop/close
    happens to reap them, rather than silently forgotten. Both default to
    ``0`` so an implementation that has no such concept (or another endpoint
    that constructs this report, e.g. a remote/browser endpoint with no
    local stream or child process to fail) never has to name an argument it
    has nothing to say about.
    """

    capture_thread_stopped: bool
    writer_thread_stopped: bool
    samples_discarded: int
    elapsed_s: float
    streams_close_failed: int = 0
    children_unreaped: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "capture_thread_stopped": self.capture_thread_stopped,
            "writer_thread_stopped": self.writer_thread_stopped,
            "samples_discarded": self.samples_discarded,
            "elapsed_s": self.elapsed_s,
            "streams_close_failed": self.streams_close_failed,
            "children_unreaped": self.children_unreaped,
        }


#: A capture callback: called with one chunk of pcm16/mono/24 kHz bytes per
#: delivery. Endpoints call this synchronously from whatever thread frames
#: arrive on; a callback that raises must never be allowed to kill that
#: thread (concrete endpoints catch and record — see
#: :mod:`embodiment.audio.host`).
FrameCallback = Callable[[bytes], None]


@runtime_checkable
class AudioEndpoint(Protocol):
    """Ears and a mouth: capture, playback, mute, and the attach/detach/close lifecycle.

    ``runtime_checkable`` so a composition root can assert
    ``isinstance(candidate, AudioEndpoint)`` as a structural sanity check;
    Protocol's runtime check only inspects method *names*, never signatures,
    so this is a smoke check, not a substitute for a real implementation
    review.
    """

    def attach(self) -> None:
        """Acquire whatever exclusive resource this endpoint owns. Idempotent, never raises."""
        ...

    def detach(self) -> None:
        """Release that resource promptly. Idempotent, never raises."""
        ...

    def start_capture(self, on_frame: FrameCallback) -> None:
        """Begin delivering pcm16 mono frames, at :attr:`sample_rate` Hz, to ``on_frame``.

        Never raises. An endpoint that cannot capture (no hardware, a
        degraded state) is a no-op: ``on_frame`` is simply never called, and
        the reason is visible through :meth:`status`.
        """
        ...

    def stop_capture(self) -> None:
        """Stop delivering frames. Idempotent, never raises."""
        ...

    @property
    def sample_rate(self) -> int:
        """The rate (Hz) of frames this endpoint delivers to ``on_frame`` (decision 15).

        Read this — never assume :data:`SAMPLE_RATE_HZ` — before passing
        captured audio on to something that needs to know its rate (e.g. the
        daemon declaring a realtime session's ``input_sample_rate``). Stable
        for the life of one endpoint instance; never raises.
        """
        ...

    def play(self, frames: bytes) -> None:
        """Play one chunk of pcm16/mono/24 kHz bytes. Never blocks, never raises.

        The whole reply is buffered (bounded in seconds, not in chunk count):
        every chunk handed to ``play`` is part of one sentence, so an
        implementation must never silently drop a chunk from the middle of a
        reply the way a drop-oldest queue would. A bound that is actually hit
        refuses the NEW chunk and records a counted degradation instead —
        see :mod:`embodiment.audio.host` for the concrete policy.
        """
        ...

    def stop_playback(self) -> int:
        """Barge-in: discard everything queued and cut what is sounding right now.

        Returns the number of 24 kHz samples discarded (0 if nothing was
        playing). Never raises. Idempotent: calling it with nothing queued or
        sounding is a harmless no-op that returns 0. After this returns, a
        subsequent :meth:`play` starts a fresh reply — an implementation may
        need to reopen its output device to guarantee that.
        """
        ...

    @property
    def playing(self) -> bool:
        """``True`` while anything is queued or actively sounding."""
        ...

    def mute(self, muted: bool) -> None:
        """Set the mute state. Enforced in the capture path, before ``on_frame``.

        Setting to the current value is a no-op that records nothing further;
        an actual change records exactly one transition.
        """
        ...

    @property
    def muted(self) -> bool:
        """The current mute state."""
        ...

    def close(self, deadline: float) -> EndpointCloseReport:
        """Release everything this endpoint holds within ``deadline`` seconds.

        Idempotent, never raises. Returns an :class:`EndpointCloseReport`
        naming exactly what was released within the deadline — what could not
        be is reported there and through :meth:`status`, never silently
        dropped.
        """
        ...

    def status(self) -> dict[str, object]:
        """A plain, JSON-serialisable snapshot: attached/capturing/muted state,
        any recorded :class:`EndpointDegradation`, and endpoint-specific counters
        (e.g. dropped-frame counts). Never raises."""
        ...


class NullEndpoint:
    """The recorded no-voice state's stand-in: satisfies :class:`AudioEndpoint`, does nothing.

    Every method is a harmless no-op. ``start_capture`` records the callback
    but never calls it — there is no source of frames — so a daemon wired to
    a ``NullEndpoint`` behaves exactly like one that received a degraded real
    endpoint: silent, but never ``None``, so it never has to special-case the
    absence of an endpoint separately from the presence of a degraded one.
    """

    __slots__ = (
        "_muted",
        "_on_frame",
        "_attached",
        "_capturing",
        "_discarded_samples",
        "_close_deadline",
    )

    def __init__(self) -> None:
        self._muted = False
        self._on_frame: FrameCallback | None = None
        self._attached = False
        self._capturing = False
        self._discarded_samples = 0
        self._close_deadline: float | None = None

    def attach(self) -> None:
        self._attached = True

    def detach(self) -> None:
        self._attached = False
        self._capturing = False

    def start_capture(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        self._capturing = True

    def stop_capture(self) -> None:
        self._capturing = False

    @property
    def sample_rate(self) -> int:
        """No real source, so :data:`SAMPLE_RATE_HZ` — the only value a
        no-configured-hardware default can honestly report."""
        return SAMPLE_RATE_HZ

    def play(self, frames: bytes) -> None:
        """Nowhere to play, so the samples are counted as discarded.

        "Does nothing" and "says nothing about what it did" are different
        promises (C3). A daemon degraded onto this endpoint has a real
        question — how much reply audio went nowhere — and the running total
        in ``status()`` (``samples_dropped_total``) is the only honest answer
        to it. It is deliberately NOT the protocol's ``samples_discarded``,
        which counts queued audio a stop dropped; nothing is ever queued here.
        """
        # Never raises, on anything: this endpoint is what a degraded daemon
        # falls back to, and it is handed whatever the caller had. Something
        # that is not a buffer counts as zero samples rather than an error.
        buffered = isinstance(frames, (bytes, bytearray, memoryview))
        self._discarded_samples += (len(frames) // SAMPLE_WIDTH_BYTES) if buffered else 0

    def stop_playback(self) -> int:
        """Always ``0``: this endpoint never QUEUES anything for a stop to drop.

        The protocol's number is undelivered queued audio, and the running
        total of what went nowhere is a different fact — ``status()`` carries
        that as ``samples_dropped_total``.
        """
        return 0

    @property
    def playing(self) -> bool:
        return False

    def mute(self, muted: bool) -> None:
        self._muted = bool(muted)

    @property
    def muted(self) -> bool:
        return self._muted

    def close(self, deadline: float) -> EndpointCloseReport:
        """Nothing to wait for — but the budget it was given is kept.

        A caller reading the report can then tell a close that had no work
        from a close whose deadline was never honoured, without having to
        know which endpoint it held.
        """
        self._attached = False
        self._capturing = False
        self._close_deadline = float(deadline)
        return EndpointCloseReport(
            capture_thread_stopped=True,
            writer_thread_stopped=True,
            samples_discarded=0,
            elapsed_s=0.0,
        )

    def status(self) -> dict[str, object]:
        return {
            "attached": self._attached,
            "capturing": self._capturing,
            "playing": False,
            "muted": self._muted,
            "degradation": EndpointDegradation(
                DEGRADED_NO_ENDPOINT, "no audio endpoint configured"
            ).to_dict(),
            "samples_dropped_total": self._discarded_samples,
            "close_deadline_s": self._close_deadline,
        }
