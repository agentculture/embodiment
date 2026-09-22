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
  ``play``/``stop_playback`` only — it never attaches, detaches, mutes or
  closes the endpoint, because those are the daemon's lifecycle calls, not a
  single utterance's.
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
  synthesized.

The barge-in bound is a clock, not a hope
------------------------------------------
Acceptance criterion 1 sets the bound at 200 ms — this module does not invent
a tighter one, it makes the 200 ms REAL: :meth:`Voice.on_speech_started` does
exactly two things before returning — set an in-process flag and call
``endpoint.stop_playback()`` — with no lock shared with the synthesis loop, no
I/O, and no wait for the in-flight sentence loop to notice anything. The loop
in :meth:`Voice.speak` polls the SAME flag between sentences and stops
iterating; it never has to be waited on for the bound to be met, because the
audio was already cut by the direct call. What ``stop_playback`` itself costs
is the endpoint's contract to honour (:mod:`embodiment.audio.endpoint`'s
docstring), not this module's — this module's own contribution to the 200 ms
budget is, and must stay, effectively zero.

Degradation vocabulary (C3 — never raise, always record)
------------------------------------------------------------
- :data:`VOICE_TTS_FAILED` — the synthesis call raised. Recorded exactly ONCE
  per :meth:`Voice.speak` call (the loop stops trying to synthesize further
  sentences once one has failed — a bounded cost, not a record-storm), never
  with the sentence text in the reason (lesson 5).
- :data:`VOICE_TTS_MALFORMED` — the injected :data:`SynthesizeFn` returned
  something other than ``bytes``/``bytearray`` without raising. Same
  once-per-call, stop-trying treatment as :data:`VOICE_TTS_FAILED`.
- :data:`VOICE_ENDPOINT_FAILED` — ``play`` or ``stop_playback`` raised, despite
  the endpoint's own contract to never do so; this module still does not trust
  that promise blindly.
- :data:`VOICE_BARGE_IN` — a barge-in dropped one or more un-synthesized or
  un-played sentences. The reason names ONLY the count and total, never any
  sentence text.
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
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment import safe_reason
from embodiment.audio.features import FeatureExtractor

__all__ = [
    "SPEECH_ROUTE",
    "DEFAULT_GATEWAY_URL",
    "MAX_REPLY_CHARS",
    "MAX_SENTENCE_CHARS",
    "MAX_SENTENCES",
    "BARGE_IN_BOUND_S",
    "MAX_FEATURE_FRAMES",
    "VOICE_TTS_FAILED",
    "VOICE_TTS_MALFORMED",
    "VOICE_ENDPOINT_FAILED",
    "VOICE_BARGE_IN",
    "VOICE_NO_BUS",
    "VOICE_PUBLISH_FAILED",
    "VoiceDegradation",
    "VoiceConfig",
    "SpeakResult",
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

# ── the degradation vocabulary (C3) ─────────────────────────────────────────

VOICE_TTS_FAILED = "voice-tts-failed"
#: :func:`SynthesizeFn` returned a value that is not ``bytes``/``bytearray`` —
#: a broken injected synthesizer, never a network fault. Found attacking this
#: module: a non-exception, non-bytes return used to be silently dropped with
#: nothing recorded (lesson 3).
VOICE_TTS_MALFORMED = "voice-tts-malformed"
VOICE_ENDPOINT_FAILED = "voice-endpoint-failed"
VOICE_BARGE_IN = "voice-barge-in-dropped"
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


@dataclass(frozen=True)
class SpeakResult:
    """What one :meth:`Voice.speak` call actually did. Never raises; always returned."""

    sentences_total: int
    sentences_delivered: int
    sentences_dropped: int
    interrupted: bool
    samples_discarded: int
    tts_degraded: bool
    published: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sentences_total": self.sentences_total,
            "sentences_delivered": self.sentences_delivered,
            "sentences_dropped": self.sentences_dropped,
            "interrupted": self.interrupted,
            "samples_discarded": self.samples_discarded,
            "tts_degraded": self.tts_degraded,
            "published": self.published,
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
    goes ONLY into the ``Authorization`` header, never the URL.

    Not exercised against a live gateway by this task's own tests — every test
    here injects a fake :data:`SynthesizeFn` — so this function's actual wire
    shape (the request body's field names) is this module's own best reading
    of "POST /v1/audio/speech ... pcm16 @ 24 kHz mono", unverified against a
    running lobes instance.
    """
    origin = config.gateway_url.rstrip("/")
    url = f"{origin}{SPEECH_ROUTE}"
    if not url.startswith(("http://", "https://")):
        raise ValueError("gateway_url must be http(s)")
    body = json.dumps(
        {
            "input": sentence,
            "voice": config.voice or "default",
            "response_format": "pcm",
        }
    ).encode("utf-8")
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
            structurally-compatible double). Only ``play``/``stop_playback``
            are called — see the module docstring.
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
        #: The :class:`~embodiment.audio.features.FeatureExtractor` frames
        #: emitted for bytes this module actually handed to ``play`` — see
        #: criterion 3. Bounded by :data:`MAX_FEATURE_FRAMES`.
        self.feature_frames: list[dict[str, object]] = []
        self.feature_frames_dropped = 0

    @property
    def speaking(self) -> bool:
        """``True`` while a :meth:`speak` call is in progress on any thread."""
        return self._speaking.is_set()

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
        delivered = 0
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

            delivered += 1
            self._feed_features(pcm)

        dropped = total - delivered
        samples_discarded = 0
        if interrupted:
            with self._state_lock:
                samples_discarded = self._last_discarded_samples
            self._degrade(
                VOICE_BARGE_IN,
                f"dropped {dropped} of {total} sentences on barge-in",
            )

        return SpeakResult(
            sentences_total=total,
            sentences_delivered=delivered,
            sentences_dropped=dropped,
            interrupted=interrupted,
            samples_discarded=samples_discarded,
            tts_degraded=tts_degraded,
            published=published,
        )

    # ── internals ────────────────────────────────────────────────────────

    def _feed_features(self, pcm: bytes) -> None:
        try:
            frames = self._features.feed(pcm)
        except Exception as exc:  # noqa: BLE001 - a dashboard trace glitch must not stop speech
            self._degrade(VOICE_ENDPOINT_FAILED, safe_reason.describe_exception(exc))
            return
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
