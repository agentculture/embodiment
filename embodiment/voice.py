"""embodiment.voice — sentence-split synthesis, playback, and barge-in (plan task ``t12``).

What this module does
----------------------
Turns a reply string into spoken audio: split the text into sentences, ask the
lobes gateway (``POST /v1/audio/speech``) to synthesize each one to pcm16 mono
24 kHz bytes, and hand each chunk to an :class:`~embodiment.audio.endpoint.AudioEndpoint`'s
:meth:`~embodiment.audio.endpoint.AudioEndpoint.play`. On a barge-in signal
(the daemon's own reading of a ``speech_started`` event off the ears socket —
see :mod:`embodiment.realtime.wire`) arriving mid-utterance, output stops and
whatever text was never spoken is dropped and recorded, never guessed at or
silently continued.

The seams this module depends on, and does NOT own
----------------------------------------------------
- **The endpoint.** :class:`~embodiment.audio.endpoint.AudioEndpoint` (task
  ``t7``) as it stands on ``realtime/phase-b`` today. This module calls
  ``play``/``stop_playback``/``playing`` only — it never attaches, detaches,
  mutes or closes the endpoint, because those are the daemon's lifecycle
  calls, not a single utterance's.
- **The speech-onset signal.** This module has no socket and no event loop.
  :meth:`Voice.on_speech_started` is a plain method a caller invokes — fed by
  whatever reads :class:`embodiment.realtime.wire.SpeechStarted` off
  :class:`embodiment.realtime.client.RealtimeEars.events` (task ``t15`` wires
  that). Passing the event object itself is optional and unused beyond
  presence — the method takes a barge-in as a fact, not a payload to inspect.
- **The bus.** ``bus`` is any object with a ``publish(kind, data)`` method
  shaped like :class:`embodiment.bus.Bus` — injected, never imported, so this
  module adds no dependency on ``events_cli``/``paho-mqtt`` and a test can pass
  a bare double. ``bus=None`` is a supported, degrading configuration (C3): no
  bus configured records :data:`VOICE_NO_BUS` and moves on, never raises.
- **The feature extractor.** :class:`embodiment.audio.features.FeatureExtractor`
  (task ``t8``). Fed the EXACT bytes handed to ``play`` — never the bytes TTS
  returned before a failed or dropped play — so the oscilloscope trace a
  dashboard draws reflects what was actually spoken, not what was merely
  synthesized. See "Feeding is paced in real time" below — this is what round
  2 of this task fixed.

The barge-in bound is a clock, not a hope
------------------------------------------
Acceptance criterion 1 sets the bound at 200 ms — this module does not invent
a tighter one, it makes the 200 ms REAL: :meth:`Voice.on_speech_started` does a
small, fixed amount of work before returning — set an in-process flag, drop
the (in-memory, not-yet-paced) pacing buffer under its own lock, and call
``endpoint.stop_playback()`` — with no lock shared with the synthesis loop, no
I/O, and no wait for the in-flight sentence loop or the pacing thread to
notice anything. Both the sentence loop (:meth:`Voice.speak`) and the pacing
thread poll the SAME flag and stop on their own schedule; :meth:`on_speech_started`
never waits for either of them, because the audio was already cut by the
direct call. What ``stop_playback`` itself costs is the endpoint's contract to
honour (:mod:`embodiment.audio.endpoint`'s docstring), not this module's —
this module's own contribution to the 200 ms budget is, and must stay,
effectively zero.

Feeding is paced in real time (round 2, defect 1)
----------------------------------------------------
Round 1 fed the :class:`~embodiment.audio.features.FeatureExtractor` the
instant :meth:`~embodiment.audio.endpoint.AudioEndpoint.play` was CALLED. That
is correct for a fake player that "plays" synchronously in the caller's own
call, and WRONG for any real endpoint (the whole point of
:meth:`~embodiment.audio.endpoint.AudioEndpoint.play`'s own contract: "never
blocks" — see :mod:`embodiment.audio.endpoint`): ``play()`` queues and
returns at once, the audio actually sounds later, and a barge-in shortly
after can discard everything that was ever queued. Measured against a
queueing double shaped like ``HostEndpoint``: three 1 s sentences queued and
fed in 6 ms, then a barge-in discarded all 72000 samples — yet the trace
still said 3 s had been spoken. That is exactly the case criterion 3 exists
for ("reflects what was actually spoken"), and it was false whenever a
barge-in happened.

Fixed by decoupling QUEUEING from TRACING: :meth:`Voice.speak` still queues a
sentence to the endpoint (and returns) as soon as synthesis and ``play()``
succeed, but the bytes handed to ``play`` are appended to an internal pacing
buffer instead of being fed immediately. ONE background thread
(:meth:`Voice._pace_worker`, lazily started, owned entirely by this class)
drains that buffer :data:`PACE_SLICE_BYTES` (:data:`PACE_SLICE_S` = 20 ms) at
a time, sleeping :data:`PACE_SLICE_S` between slices, feeding each slice to
the extractor as it goes — so :attr:`Voice.feature_frames` tracks what a
listener would actually have heard by now, to within one slice, not what was
merely queued. It only feeds while :attr:`~embodiment.audio.endpoint.AudioEndpoint.playing`
reads ``True`` (a transient ``False`` pauses rather than drops — see
:meth:`Voice._pace_tick` — bounded by :data:`PACE_STALL_TICKS` so a
permanently-stuck endpoint cannot hold queued audio in limbo forever), and it
stops on the SAME interrupt flag :meth:`on_speech_started` sets, immediately —
whatever is still in the pacing buffer at that instant (or, symmetrically,
whatever :meth:`speak` queued a moment too late to make it into the buffer at
all) is never fed and is counted, exactly, on :attr:`Voice.queued_not_traced`
— a sample count, visible on both :class:`SpeakResult` (a snapshot at the
moment ``speak()`` returns) and :meth:`Voice.status` (live, since pacing and a
barge-in both continue/can happen after ``speak()`` has already returned).

The pacing thread is a resource like any other this package hands out
(lesson 6, shutdown is a feature): :meth:`Voice.close` stops it with a
bounded join and reports what it left unfinished, via
:class:`VoiceCloseReport` — the daemon (task ``t15``) is expected to call it
on its own shutdown path, the same way it will call
:meth:`~embodiment.audio.endpoint.AudioEndpoint.close`.

Naming: "queued", not "delivered"
------------------------------------
:attr:`SpeakResult.sentences_queued` (renamed from an earlier
``sentences_delivered``) counts sentences handed successfully to
``endpoint.play()`` — which, per the paragraph above, is NOT the same as
having been heard. Whether a queued sentence was actually, audibly delivered
is only knowable through the paced trace (:attr:`Voice.feature_frames`) and
:attr:`Voice.queued_not_traced`, which is exactly why the rename exists: a
field called "delivered" that only meant "queued" was the shape of the
defect this round fixes, so it does not get to keep that name.

Degradation vocabulary (C3 — never raise, always record)
------------------------------------------------------------
- :data:`VOICE_TTS_FAILED` — the synthesis call raised. Recorded exactly ONCE
  per :meth:`Voice.speak` call (the loop stops trying to synthesize further
  sentences once one has failed — a bounded cost, not a record-storm), never
  with the sentence text in the reason (lesson 5).
- :data:`VOICE_TTS_MALFORMED` — the injected :data:`SynthesizeFn` returned
  something other than ``bytes``/``bytearray`` without raising. Same
  once-per-call, stop-trying treatment as :data:`VOICE_TTS_FAILED`.
- :data:`VOICE_TTS_OVERSIZE` — one sentence's synthesized audio exceeded
  :data:`MAX_SENTENCE_AUDIO_BYTES` and was truncated — bounds the pacing
  buffer (and memory) against a misbehaving synthesizer, independent of the
  TEXT-length bounds :func:`split_sentences` already applies.
- :data:`VOICE_ENDPOINT_FAILED` — ``play``, ``stop_playback`` or ``playing``
  raised, despite the endpoint's own contract to never do so; this module
  still does not trust that promise blindly.
- :data:`VOICE_BARGE_IN` — a barge-in dropped one or more un-synthesized or
  un-queued sentences. The reason names ONLY the count and total, never any
  sentence text.
- :data:`VOICE_PACE_STALLED` — the pacing buffer held audio for more than
  :data:`PACE_STALL_TICKS` consecutive slices while the endpoint never once
  reported ``playing``; the remainder is dropped and counted rather than held
  forever (nothing here may block without a deadline — lesson 2).
- :data:`VOICE_NO_BUS` — no bus was configured, so the reply text could not be
  published as an event.
- :data:`VOICE_PUBLISH_FAILED` — the injected bus's own ``publish`` raised.

No speech in a record (lesson 5)
---------------------------------
Every degradation reason built here is a fixed template plus integers (counts,
lengths) or :func:`embodiment.safe_reason.describe_exception`'s output — never
the reply text, a sentence, or the gateway API key. The key is read only into
an HTTP ``Authorization`` header inside :func:`http_synthesize`'s request
object; it is never interpolated into a URL, a log line or an exception.

The request body (round 2, defect 2)
---------------------------------------
:func:`http_synthesize` used to send ``"voice": "default"`` whenever
``config.voice`` was empty. Reading lobes' own
``lobes/realtime/audio_facade.py::parse_speech_request`` (not a live call —
this module still has no live gateway in its own tests) shows that a PRESENT
``voice`` value is read as a voice NAME or a ``.wav`` path for cloning, and
``settings.default_voice`` is only used when the key is absent or ``None`` —
so the literal string ``"default"`` used to reach the synthesizer (Chatterbox)
as a voice it does not have, rather than as "use your own default". Fixed:
the ``"voice"`` key is now omitted entirely from the request body when
``config.voice`` is empty. ``"response_format": "pcm"`` is unchanged — lobes'
``SUPPORTED_FORMATS`` is ``("wav", "pcm")``.

Sentence splitting is a bounded, best-effort heuristic
--------------------------------------------------------
:func:`split_sentences` is a plain regex split on ``.``/``!``/``?``/full-width
equivalents followed by whitespace — it does not attempt to special-case
abbreviations ("Dr.", "e.g.") because a wrong split here costs one extra short
utterance, not a correctness bug; a worse outcome would be to over-fit a
heuristic to English and mis-split Hebrew (the shipped voice's language,
CLAUDE.md). It is bounded on both axes so a hostile or pathological reply
cannot make this module do unbounded work: :data:`MAX_REPLY_CHARS` truncates
the whole reply before splitting, and :data:`MAX_SENTENCES` caps how many
utterances one reply produces.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment import safe_reason
from embodiment.audio.features import SAMPLE_RATE_HZ, FeatureExtractor

__all__ = [
    "SPEECH_ROUTE",
    "DEFAULT_GATEWAY_URL",
    "MAX_REPLY_CHARS",
    "MAX_SENTENCE_CHARS",
    "MAX_SENTENCES",
    "MAX_SENTENCE_AUDIO_BYTES",
    "BARGE_IN_BOUND_S",
    "MAX_FEATURE_FRAMES",
    "PACE_SLICE_S",
    "PACE_SLICE_BYTES",
    "PACE_STALL_TICKS",
    "BYTES_PER_SAMPLE",
    "VOICE_TTS_FAILED",
    "VOICE_TTS_MALFORMED",
    "VOICE_TTS_OVERSIZE",
    "VOICE_ENDPOINT_FAILED",
    "VOICE_BARGE_IN",
    "VOICE_PACE_STALLED",
    "VOICE_NO_BUS",
    "VOICE_PUBLISH_FAILED",
    "VoiceDegradation",
    "VoiceConfig",
    "SpeakResult",
    "VoiceCloseReport",
    "SynthesizeFn",
    "split_sentences",
    "http_synthesize",
    "Voice",
]

#: The gateway route this module POSTs to. pcm16 mono 24 kHz in, per CLAUDE.md.
SPEECH_ROUTE = "/v1/audio/speech"

#: Mirrors :mod:`embodiment.realtime.client`'s own default origin (that module's
#: ``_DEFAULT_GATEWAY`` is private, so the value is restated here rather than
#: imported — same rig, same default, independently owned constants).
DEFAULT_GATEWAY_URL = "http://localhost:8001"

#: Hard cap on the whole reply before splitting. A **judgement call**: no
#: measured spoken reply in this package's live-test evidence approaches this;
#: it exists to bound the cost of a pathological or hostile input, not to
#: constrain a normal one.
MAX_REPLY_CHARS = 4000

#: Hard cap on one sentence handed to synthesis. A **judgement call**: keeps
#: one HTTP call's body small and bounds one synthesized chunk's memory.
MAX_SENTENCE_CHARS = 500

#: Hard cap on how many sentences one reply is split into. A **judgement
#: call**: a reply needing more than this is almost certainly not natural
#: speech (e.g. a wall of single-character "sentences" from repeated
#: delimiters) and is truncated rather than turned into hundreds of HTTP calls.
MAX_SENTENCES = 64

#: pcm16 mono: 2 bytes per sample. Matches
#: :data:`embodiment.audio.endpoint.SAMPLE_WIDTH_BYTES`; restated here rather
#: than imported, since this module otherwise has no reason to import
#: ``embodiment.audio.endpoint`` (it only duck-types the endpoint — see the
#: module docstring's seams section).
BYTES_PER_SAMPLE = 2

#: Hard cap on ONE sentence's synthesized audio, in bytes. A **judgement
#: call**: 10 s of pcm16 mono 24 kHz — bounds the pacing buffer (round 2)
#: against a misbehaving :data:`SynthesizeFn` that returns an unreasonably
#: long clip for one short sentence, independent of the TEXT-length bounds
#: above.
MAX_SENTENCE_AUDIO_BYTES = 10 * SAMPLE_RATE_HZ * BYTES_PER_SAMPLE

#: The barge-in acceptance bound (criterion 1) — see the module docstring's
#: "barge-in bound is a clock" section for what this actually bounds.
#: Documented here so a test asserts against the named constant, not a bare
#: literal.
BARGE_IN_BOUND_S = 0.2

#: Bound on how many :class:`~embodiment.audio.features.FeatureExtractor`
#: frames :class:`Voice` retains on :attr:`Voice.feature_frames`. A
#: **judgement call**: one reply rarely produces more than a few dozen ~33 ms
#: blocks; this bounds memory against a pathological long reply without
#: dropping anything a normal utterance would produce.
MAX_FEATURE_FRAMES = 2048

#: How much queued pcm the pacing thread feeds per tick, and how long it
#: sleeps between ticks. Dictated by round 2's brief ("feed 20 ms of the
#: queued pcm every 20 ms"), not a value this module chose freely.
PACE_SLICE_S = 0.02
PACE_SLICE_SAMPLES = int(SAMPLE_RATE_HZ * PACE_SLICE_S)
PACE_SLICE_BYTES = PACE_SLICE_SAMPLES * BYTES_PER_SAMPLE

#: How many consecutive pacing ticks may see the endpoint report
#: ``playing=False`` (or raise) while the pacing buffer is non-empty before
#: the remainder is dropped and counted as :data:`VOICE_PACE_STALLED`. A
#: **judgement call**: 100 ticks * 20 ms = 2 s — generous enough that a brief
#: gap between two ``play()`` calls (endpoint drains one queued chunk fully
#: before the next arrives) never trips it, bounded so a genuinely stuck
#: endpoint cannot hold audio in limbo forever (lesson 2).
PACE_STALL_TICKS = 100

# ── the degradation vocabulary (C3) ─────────────────────────────────────────

VOICE_TTS_FAILED = "voice-tts-failed"
#: :func:`SynthesizeFn` returned a value that is not ``bytes``/``bytearray`` —
#: a broken injected synthesizer, never a network fault. Found attacking this
#: module: a non-exception, non-bytes return used to be silently dropped with
#: nothing recorded (lesson 3).
VOICE_TTS_MALFORMED = "voice-tts-malformed"
#: One sentence's audio exceeded :data:`MAX_SENTENCE_AUDIO_BYTES` and was
#: truncated before queueing.
VOICE_TTS_OVERSIZE = "voice-tts-oversize"
VOICE_ENDPOINT_FAILED = "voice-endpoint-failed"
VOICE_BARGE_IN = "voice-barge-in-dropped"
#: The pacing buffer stalled — see :data:`PACE_STALL_TICKS`.
VOICE_PACE_STALLED = "voice-pace-stalled"
VOICE_NO_BUS = "voice-no-bus"
VOICE_PUBLISH_FAILED = "voice-publish-failed"

#: Splits on a sentence terminator (ASCII or full-width) followed by
#: whitespace. Deliberately simple — see the module docstring.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


@dataclass(frozen=True)
class VoiceDegradation:
    """One recorded, host-visible transition (C3). See the vocabulary above."""

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class VoiceConfig:
    """What one :class:`Voice` synthesizes under.

    ``api_key`` carries ``repr=False`` — the same discipline
    :class:`embodiment.realtime.client.RealtimeConfig` uses — so a dataclass's
    free repr (in a log, a debugger, a traceback) cannot leak it.
    """

    gateway_url: str = DEFAULT_GATEWAY_URL
    api_key: str = field(default="", repr=False)
    voice: str = ""
    #: Bounds the ``POST /v1/audio/speech`` call. A **judgement call**: no live
    #: measurement of this route exists yet (t21 gets one); 10 s is generous
    #: headroom for a short-sentence TTS call while still bounding a hung
    #: connection.
    speech_deadline: float = 10.0
    max_reply_chars: int = MAX_REPLY_CHARS
    max_sentence_chars: int = MAX_SENTENCE_CHARS
    max_sentences: int = MAX_SENTENCES
    max_sentence_audio_bytes: int = MAX_SENTENCE_AUDIO_BYTES


@dataclass(frozen=True)
class SpeakResult:
    """What one :meth:`Voice.speak` call actually did. Never raises; always returned.

    ``sentences_queued`` counts sentences successfully handed to
    ``endpoint.play()`` — NOT sentences a listener actually heard; see the
    module docstring's "Naming" section. ``queued_not_traced`` is a snapshot
    of :attr:`Voice.queued_not_traced` at the moment this result was built —
    it can still grow after ``speak()`` returns (a later barge-in, or the
    pacing thread finishing a drop it started), so a caller that wants the
    live number reads :meth:`Voice.status` instead.
    """

    sentences_total: int
    sentences_queued: int
    sentences_dropped: int
    interrupted: bool
    samples_discarded: int
    queued_not_traced: int
    tts_degraded: bool
    published: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sentences_total": self.sentences_total,
            "sentences_queued": self.sentences_queued,
            "sentences_dropped": self.sentences_dropped,
            "interrupted": self.interrupted,
            "samples_discarded": self.samples_discarded,
            "queued_not_traced": self.queued_not_traced,
            "tts_degraded": self.tts_degraded,
            "published": self.published,
        }


@dataclass(frozen=True)
class VoiceCloseReport:
    """What :meth:`Voice.close` actually did. Never raises; always returned (lesson 6)."""

    pace_thread_stopped: bool
    elapsed_s: float
    queued_not_traced: int

    def to_dict(self) -> dict[str, object]:
        return {
            "pace_thread_stopped": self.pace_thread_stopped,
            "elapsed_s": self.elapsed_s,
            "queued_not_traced": self.queued_not_traced,
        }


#: A synthesis call: one sentence and the config it runs under, in; pcm16 mono
#: 24 kHz bytes out. Raises on failure — :class:`Voice` is the ONE place that
#: catches it (lesson 8: one code path) and folds it into a recorded
#: degradation via :func:`embodiment.safe_reason.describe_exception`.
SynthesizeFn = Callable[[str, VoiceConfig], bytes]


def split_sentences(
    text: object,
    *,
    max_reply_chars: int = MAX_REPLY_CHARS,
    max_sentence_chars: int = MAX_SENTENCE_CHARS,
    max_sentences: int = MAX_SENTENCES,
) -> list[str]:
    """Split *text* into bounded, non-empty sentences. Never raises.

    Non-``str`` input, or input that is empty/whitespace-only after
    stripping, returns ``[]`` — an empty reply has nothing to say, which is
    not an error. See the module docstring for why this is a plain regex
    split rather than an abbreviation-aware one.
    """
    if not isinstance(text, str):
        return []
    bounded = text[: max(0, int(max_reply_chars))]
    stripped = bounded.strip()
    if not stripped:
        return []
    parts = _SENTENCE_END.split(stripped)
    sentences = [p.strip() for p in parts if p.strip()]
    if not sentences:
        sentences = [stripped]
    capped_count = max(1, int(max_sentences))
    capped_len = max(1, int(max_sentence_chars))
    return [s[:capped_len] for s in sentences[:capped_count]]


def http_synthesize(sentence: str, config: VoiceConfig) -> bytes:
    """The default :data:`SynthesizeFn`: ``POST /v1/audio/speech`` on the lobes gateway.

    Raises on any failure — network, non-2xx, or a malformed origin — so
    :class:`Voice` can fold every failure through the ONE
    :func:`~embodiment.safe_reason.describe_exception` call site. The api key
    goes ONLY into the ``Authorization`` header, never the URL. The ``voice``
    field is omitted entirely when ``config.voice`` is empty — see the module
    docstring's "The request body" section for why sending the literal string
    ``"default"`` is wrong.

    Not exercised against a live gateway by this task's own tests — every test
    here injects a fake :data:`SynthesizeFn` — so this function's actual wire
    shape is this module's own best reading of "POST /v1/audio/speech ...
    pcm16 @ 24 kHz mono" plus a reading of lobes' own
    ``parse_speech_request``, unverified against a running lobes instance.
    """
    origin = config.gateway_url.rstrip("/")
    url = f"{origin}{SPEECH_ROUTE}"
    if not url.startswith(("http://", "https://")):
        raise ValueError("gateway_url must be http(s)")
    payload: dict[str, str] = {"input": sentence, "response_format": "pcm"}
    if config.voice:
        payload["voice"] = config.voice
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(  # nosec B310 - scheme checked above
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(  # nosec B310 - scheme checked above
        request, timeout=config.speech_deadline
    ) as response:
        return response.read()


class Voice:
    """Sentence-split synthesis, playback, and barge-in for one reply at a time.

    Args:
        endpoint: an :class:`~embodiment.audio.endpoint.AudioEndpoint` (or a
            structurally-compatible double). Only ``play``/``stop_playback``/
            ``playing`` are called — see the module docstring.
        config: a :class:`VoiceConfig`. Defaults to one with an unreachable
            placeholder gateway; a real host supplies its own.
        bus: anything with a ``publish(kind, data)`` method, or ``None``. See
            the module docstring's bus section.
        features: a :class:`~embodiment.audio.features.FeatureExtractor`. A
            fresh one is created when omitted.
        synthesize: the :data:`SynthesizeFn` to call. Defaults to
            :func:`http_synthesize`; tests inject a fake.
    """

    def __init__(
        self,
        *,
        endpoint: Any,
        config: Optional[VoiceConfig] = None,
        bus: Optional[Any] = None,
        features: Optional[FeatureExtractor] = None,
        synthesize: Optional[SynthesizeFn] = None,
    ) -> None:
        self._endpoint = endpoint
        self._config = config or VoiceConfig()
        self._bus = bus
        self._features = features if features is not None else FeatureExtractor()
        self._synthesize = synthesize or http_synthesize

        self._interrupted = threading.Event()
        self._speaking = threading.Event()
        self._state_lock = threading.Lock()
        self._last_discarded_samples = 0

        #: Bounded (lesson: nothing here grows without limit). Each entry is a
        #: recorded, host-visible transition (C3).
        self.degradations: list[VoiceDegradation] = []
        self.degradation_counts: dict[str, int] = {}
        #: The :class:`~embodiment.audio.features.FeatureExtractor` frames for
        #: bytes this module has, so far, PACED into the extractor at real
        #: time — see criterion 3 and the module docstring's pacing section.
        #: Bounded by :data:`MAX_FEATURE_FRAMES`.
        self.feature_frames: list[dict[str, object]] = []
        self.feature_frames_dropped = 0
        #: Samples that were queued to ``play()`` but never made it into the
        #: paced trace (a barge-in, a close, or a stalled endpoint cut them
        #: off first). Live — grows even after a ``speak()`` call has
        #: returned. See :class:`SpeakResult` for a point-in-time snapshot.
        self.queued_not_traced = 0

        # ── the real-time pacing thread (round 2) ───────────────────────
        self._pace_buffer = bytearray()
        self._pace_cv = threading.Condition()
        self._pace_thread: Optional[threading.Thread] = None
        self._pace_stop = threading.Event()
        self._pace_stall_ticks = 0

    @property
    def speaking(self) -> bool:
        """``True`` while a :meth:`speak` call is in progress on any thread."""
        return self._speaking.is_set()

    def status(self) -> dict[str, object]:
        """A plain, JSON-serialisable snapshot. Never raises (C3)."""
        try:
            with self._pace_cv:
                pending_bytes = len(self._pace_buffer)
            with self._state_lock:
                degradation_counts = dict(self.degradation_counts)
                feature_frame_count = len(self.feature_frames)
                feature_frames_dropped = self.feature_frames_dropped
                queued_not_traced = self.queued_not_traced
            return {
                "speaking": self.speaking,
                "pending_pace_bytes": pending_bytes,
                "feature_frame_count": feature_frame_count,
                "feature_frames_dropped": feature_frames_dropped,
                "queued_not_traced": queued_not_traced,
                "degradation_counts": degradation_counts,
            }
        except Exception as exc:  # noqa: BLE001 - a status probe must never raise
            self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
            return {"speaking": False, "queued_not_traced": 0}

    # ── barge-in ─────────────────────────────────────────────────────────

    def on_speech_started(self, event: object = None) -> int:
        """A speech-onset signal arrived (barge-in). Stop output NOW.

        Safe to call from any thread, at any time, including when nothing is
        speaking (then it is a harmless no-op that still returns 0 — the same
        idempotence :meth:`~embodiment.audio.endpoint.AudioEndpoint.stop_playback`
        itself promises). *event* is accepted and ignored beyond presence —
        see the module docstring. Never raises (C3): an endpoint whose
        ``stop_playback`` itself raises is recorded as
        :data:`VOICE_ENDPOINT_FAILED` and this still returns 0.

        Also drops whatever is still sitting in the real-time pacing buffer,
        immediately, under its own lock — a fast, in-memory operation, so it
        costs nothing measurable against the 200 ms bound — and counts it on
        :attr:`queued_not_traced`, so criterion 3's trace is corrected the
        instant a barge-in is reported rather than whenever the pacing thread
        next happens to notice.

        Returns the number of 24 kHz samples the endpoint discarded.
        """
        self._interrupted.set()
        discarded = 0
        try:
            discarded = self._endpoint.stop_playback()
        except Exception as exc:  # noqa: BLE001 - endpoint promised never to raise; degrade anyway
            self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
            discarded = 0
        if not isinstance(discarded, int) or isinstance(discarded, bool) or discarded < 0:
            discarded = 0
        with self._state_lock:
            self._last_discarded_samples = discarded
        self._drop_pace_buffer()
        return discarded

    # ── speaking ─────────────────────────────────────────────────────────

    def speak(self, text: object) -> SpeakResult:
        """Speak one reply. Never raises (C3, verbatim per the instruction).

        Publishes the reply text as an event FIRST — before any synthesis or
        playback is attempted — so criterion 2 ("a TTS failure yields one
        degradation record and the reply text is still published as an
        event") holds even for a reply that fails on its very first sentence,
        and so a crash mid-playback still leaves the transcript-facing record
        behind it.

        Returns as soon as every sentence has been synthesized and QUEUED to
        the endpoint (or dropped/failed) — it does NOT wait for real-time
        playback or pacing to finish; see :attr:`SpeakResult.sentences_queued`
        and the module docstring's "Naming" section.
        """
        self._interrupted.clear()
        with self._state_lock:
            self._last_discarded_samples = 0
        self._speaking.set()
        try:
            reply_text = text if isinstance(text, str) else ""
            published = self._publish_reply(reply_text)
            return self._speak(reply_text, published=published)
        finally:
            self._speaking.clear()

    def _speak(self, text: str, *, published: bool) -> SpeakResult:
        sentences = split_sentences(
            text,
            max_reply_chars=self._config.max_reply_chars,
            max_sentence_chars=self._config.max_sentence_chars,
            max_sentences=self._config.max_sentences,
        )
        total = len(sentences)
        queued = 0
        tts_degraded = False
        interrupted = False

        for sentence in sentences:
            if self._interrupted.is_set():
                interrupted = True
                break

            pcm = b""
            if not tts_degraded:
                try:
                    pcm = self._synthesize(sentence, self._config)
                except Exception as exc:  # noqa: BLE001 - fold every synth failure the same way
                    tts_degraded = True
                    self._degrade(VOICE_TTS_FAILED, safe_reason.describe_exception(exc))
                    pcm = b""
                else:
                    if not isinstance(pcm, (bytes, bytearray)):
                        tts_degraded = True
                        self._degrade(
                            VOICE_TTS_MALFORMED,
                            f"synthesize() returned {safe_reason.safe_label(type(pcm).__name__)}"
                            ", not bytes",
                        )
                        pcm = b""
                    else:
                        pcm = bytes(pcm)
                        cap = self._config.max_sentence_audio_bytes
                        if len(pcm) > cap:
                            self._degrade(
                                VOICE_TTS_OVERSIZE,
                                f"{len(pcm)} bytes truncated to {cap}",
                            )
                            pcm = pcm[:cap]

            if not pcm:
                continue

            if self._interrupted.is_set():
                interrupted = True
                break

            try:
                self._endpoint.play(pcm)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - endpoint promised never to raise; degrade anyway
                self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
                continue

            queued += 1
            self._enqueue_pace(pcm)

        dropped = total - queued
        samples_discarded = 0
        if interrupted:
            with self._state_lock:
                samples_discarded = self._last_discarded_samples
            self._degrade(
                VOICE_BARGE_IN,
                f"dropped {dropped} of {total} sentences on barge-in",
            )

        with self._state_lock:
            queued_not_traced = self.queued_not_traced

        return SpeakResult(
            sentences_total=total,
            sentences_queued=queued,
            sentences_dropped=dropped,
            interrupted=interrupted,
            samples_discarded=samples_discarded,
            queued_not_traced=queued_not_traced,
            tts_degraded=tts_degraded,
            published=published,
        )

    # ── shutdown (lesson 6) ─────────────────────────────────────────────

    def close(self, deadline: float = 2.0) -> VoiceCloseReport:
        """Idempotent, never raises, returns within *deadline* seconds.

        Stops the pacing thread with a bounded join and drops (counting)
        whatever was left in the pacing buffer, so a host that shuts down
        mid-utterance still gets an honest, final accounting rather than a
        thread left silently running past its owner's lifetime.
        """
        start = time.monotonic()
        try:
            self._pace_stop.set()
            self._drop_pace_buffer()
            with self._pace_cv:
                self._pace_cv.notify_all()
            thread = self._pace_thread
            stopped = True
            if thread is not None:
                remaining = max(0.0, deadline - (time.monotonic() - start))
                thread.join(timeout=remaining)
                stopped = not thread.is_alive()
            elapsed = time.monotonic() - start
            with self._state_lock:
                queued_not_traced = self.queued_not_traced
            return VoiceCloseReport(
                pace_thread_stopped=stopped,
                elapsed_s=elapsed,
                queued_not_traced=queued_not_traced,
            )
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
            return VoiceCloseReport(
                pace_thread_stopped=False,
                elapsed_s=time.monotonic() - start,
                queued_not_traced=0,
            )

    # ── internals: pacing (round 2) ─────────────────────────────────────

    def _enqueue_pace(self, pcm: bytes) -> None:
        """Queue *pcm* (already handed to ``play()``) for real-time tracing.

        If a barge-in raced this call — the interrupt flag was set between
        ``play()`` succeeding and this running — the bytes are counted as
        untraced directly rather than appended to a buffer nothing will ever
        drain again (the barge-in's own drain already ran, or is about to,
        and won't see this late arrival).
        """
        with self._pace_cv:
            if self._interrupted.is_set() or self._pace_stop.is_set():
                with self._state_lock:
                    self.queued_not_traced += len(pcm) // BYTES_PER_SAMPLE
                return
            self._pace_buffer.extend(pcm)
            self._pace_cv.notify_all()
        self._ensure_pace_thread()

    def _drop_pace_buffer(self) -> int:
        """Clear the pacing buffer NOW, counting whatever was in it. Idempotent."""
        with self._pace_cv:
            dropped_bytes = len(self._pace_buffer)
            if dropped_bytes:
                self._pace_buffer.clear()
            self._pace_cv.notify_all()
        if dropped_bytes:
            with self._state_lock:
                self.queued_not_traced += dropped_bytes // BYTES_PER_SAMPLE
        return dropped_bytes

    def _ensure_pace_thread(self) -> None:
        with self._pace_cv:
            if self._pace_thread is not None and self._pace_thread.is_alive():
                return
            if self._pace_stop.is_set():
                return
            self._pace_thread = threading.Thread(
                target=self._pace_worker, name="embodiment-voice-pace", daemon=True
            )
            self._pace_thread.start()

    def _pace_worker(self) -> None:
        """Drains the pacing buffer :data:`PACE_SLICE_BYTES` at a time, at
        :data:`PACE_SLICE_S` real-time cadence, off the caller's thread.

        Runs for the lifetime of this :class:`Voice` (lazily started, stopped
        only by :meth:`close`) rather than once per :meth:`speak` call, so a
        reply queued while the previous one is still draining keeps being
        paced by the SAME thread and timeline.
        """
        while True:
            with self._pace_cv:
                while not self._pace_buffer and not self._pace_stop.is_set():
                    self._pace_cv.wait(timeout=0.5)
                if self._pace_stop.is_set() and not self._pace_buffer:
                    return
            self._pace_tick()

    def _pace_tick(self) -> None:
        """One paced feed step. See the module docstring's pacing section."""
        if self._interrupted.is_set() or self._pace_stop.is_set():
            self._drop_pace_buffer()
            self._pace_stall_ticks = 0
            return

        if not self._endpoint_playing_safe():
            self._pace_stall_ticks += 1
            if self._pace_stall_ticks > PACE_STALL_TICKS:
                dropped = self._drop_pace_buffer()
                if dropped:
                    self._degrade(
                        VOICE_PACE_STALLED,
                        f"dropped {dropped} bytes: endpoint never reported playing",
                    )
                self._pace_stall_ticks = 0
            self._interrupted.wait(timeout=PACE_SLICE_S)
            return
        self._pace_stall_ticks = 0

        with self._pace_cv:
            if not self._pace_buffer:
                return
            chunk = bytes(self._pace_buffer[:PACE_SLICE_BYTES])
            del self._pace_buffer[:PACE_SLICE_BYTES]
        self._feed_features(chunk)
        self._interrupted.wait(timeout=PACE_SLICE_S)

    def _endpoint_playing_safe(self) -> bool:
        try:
            return bool(self._endpoint.playing)
        except Exception as exc:  # noqa: BLE001 - endpoint promised never to raise; degrade anyway
            self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
            return False

    # ── internals ────────────────────────────────────────────────────────

    def _feed_features(self, pcm: bytes) -> None:
        try:
            frames = self._features.feed(pcm)
        except Exception as exc:  # noqa: BLE001 - a dashboard trace glitch must not stop speech
            self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
            return
        with self._state_lock:
            for frame in frames:
                if len(self.feature_frames) >= MAX_FEATURE_FRAMES:
                    self.feature_frames_dropped += 1
                    continue
                self.feature_frames.append(frame)

    def _publish_reply(self, text: str) -> bool:
        if self._bus is None:
            self._degrade(VOICE_NO_BUS, "no bus configured; reply not published")
            return False
        try:
            event = self._bus.publish("reply", {"text": text})
        except (
            Exception
        ) as exc:  # noqa: BLE001 - an injected bus is not trusted to keep its own contract
            self._degrade(VOICE_PUBLISH_FAILED, safe_reason.describe_exception(exc))
            return False
        return event is not None

    def _degrade(self, code: str, reason: str) -> None:
        with self._state_lock:
            self.degradation_counts[code] = self.degradation_counts.get(code, 0) + 1
            self.degradations.append(VoiceDegradation(code=code, reason=reason))
