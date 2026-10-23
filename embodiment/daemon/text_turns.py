"""embodiment.daemon.text_turns — typed turns, speak-a-message and clip transcription (t14).

What this module is
--------------------
The three handlers the chat page's routes (t10) call, with every outside
thing an injected seam so the module has no daemon, no socket and no disk:

- :meth:`TextTurns.handle_text` — a typed turn. It joins the daemon's CURRENT
  session (operator decision c58: same session, same transcript log as a
  spoken turn), runs the injected ``run_text_turn`` and returns the reply as
  TEXT. It never reaches an audio endpoint: no ``play()``, no ``Voice``.
- :meth:`TextTurns.speak_message` — the per-message speak button. Synthesises
  the message through the gateway's tts lane and RETURNS the audio (a WAV
  built in memory) for the requesting browser. Nothing is written anywhere.
- :meth:`TextTurns.handle_clip` — a recorded or uploaded clip. Checked against
  a byte cap and a duration cap BEFORE transcription, transcribed in memory
  through ``POST /v1/audio/transcriptions``, then run as a typed turn.

Seams (all injected; defaults talk to the gateway named by the config)
------------------------------------------------------------------------
- ``run_text_turn(text) -> str | object with .spoken`` — the daemon's text-only
  turn (recall, ``senses``, no speaker). It must NOT add to the session: this
  module does ``session.add_user`` before and ``session.add_assistant`` after,
  so the explicit-ask path and the transcript log see a typed turn exactly as
  they see a spoken one.
- ``session`` — the daemon's session, or a zero-argument callable returning it
  (the daemon creates it lazily). ``None`` runs the turn sessionless, recorded.
- ``tts(sentence, VoiceConfig) -> pcm16 mono 24 kHz bytes`` — :data:`embodiment.voice.SynthesizeFn`.
- ``stt(audio, filename, content_type) -> transcript str``.
- ``record(code, reason)`` — the daemon's degradation sink (C3). Every
  degradation here is also kept on :attr:`TextTurns.degradations`.

Results, not exceptions
------------------------
Nothing here raises. Each handler returns a frozen result carrying ``ok``, a
``code`` (empty on success) and an ``http_status`` hint the route may use
(413 over a cap, 415 unsupported, 400 malformed, 502 upstream, 503 no lane).

How a clip's duration is known without a dependency
-----------------------------------------------------
:func:`probe_clip` reads only the container header, in memory, stdlib only:
WAV through :mod:`wave`; Ogg (Opus/Vorbis) from the last page's granule
position. WebM and MP4, which is what browsers' ``MediaRecorder`` usually
produces, write no trustworthy duration while streaming, so for those the
duration is UNKNOWN and the hard bound is :data:`MAX_CLIP_BYTES` alone (plus a
caller-declared duration, which can only refuse MORE). That is a real weakness,
stated in the result (``duration_s=None``) and not hidden.

Privacy: raw audio is never written to disk (CLAUDE.md). The clip lives in a
``bytes`` object for the length of one call; this module imports no
``tempfile`` and opens no file, and a test pins both.
"""

from __future__ import annotations

import io
import json
import math
import struct
import time
import urllib.request
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from embodiment import safe_reason
from embodiment.audio.features import SAMPLE_RATE_HZ
from embodiment.voice import (
    _REDIRECT_REFUSING_OPENER,
    BYTES_PER_SAMPLE,
    DEFAULT_GATEWAY_URL,
    VoiceConfig,
    _read_bounded,
    http_synthesize,
    split_sentences,
)

__all__ = [
    "TRANSCRIPTION_ROUTE",
    "MAX_TEXT_CHARS",
    "MAX_CLIP_BYTES",
    "MAX_CLIP_SECONDS",
    "MAX_TRANSCRIPT_BYTES",
    "MAX_SPEAK_AUDIO_BYTES",
    "STT_DEADLINE_S",
    "STT_SECONDS_PER_AUDIO_SECOND",
    "SPEAK_DEADLINE_S",
    "TEXT_TURN_FAILED",
    "TEXT_TURN_NO_SESSION",
    "TEXT_TURN_SESSION_FAILED",
    "SPEAK_NO_TTS",
    "SPEAK_TTS_FAILED",
    "SPEAK_NO_AUDIO",
    "SPEAK_TRUNCATED",
    "CLIP_STT_FAILED",
    "CLIP_STT_MALFORMED",
    "CLIP_EMPTY_TRANSCRIPT",
    "TextTurnsConfig",
    "TextTurnResult",
    "SpeakResult",
    "ClipResult",
    "ClipProbe",
    "TextTurns",
    "probe_clip",
    "http_transcribe",
    "pcm_to_wav",
]

#: The gateway route the stt lane answers on (OpenAI-compatible, multipart).
TRANSCRIPTION_ROUTE = "/v1/audio/transcriptions"

#: Bounds one typed message (and one transcript run as a turn). A **judgement
#: call**: matches :data:`embodiment.voice.MAX_REPLY_CHARS`, the longest text
#: this package already treats as sane; bounds prompt size against a paste.
MAX_TEXT_CHARS = 4000

#: Bounds an uploaded clip's duration, in seconds. A **judgement call** on what a
#: clip is for — a spoken question or a short memo, not a recording session —
#: cut from 300 s to 60 s by t23 once the stt clock behind it was measured: it
#: bounds stt latency (see :data:`STT_DEADLINE_S`) and the memory one clip may
#: hold. The dashboard's recorder stops itself under it (``MAX_CLIP_SECONDS`` in
#: ``web/src/components/AudioControls.tsx``, pinned equal by a test).
MAX_CLIP_SECONDS = 60.0

#: The heaviest UNCOMPRESSED audio a client may upload, in bytes per second:
#: 48 kHz, 16-bit, stereo (a phone or desktop WAV at its native rate). The
#: gateway's own pcm16 mono 24 kHz is a quarter of it; compressed containers
#: (WebM/Opus, Ogg/Opus, MP4) are two orders smaller — 60 s of Opus is a few
#: hundred KB.
_HEAVIEST_UNCOMPRESSED_BYTES_PER_S = 48_000 * 2 * 2

#: Room for a WAV header and its optional chunks (``LIST``, ``fact``) beyond the
#: samples. The canonical header is 44 bytes; 64 KiB is generous for metadata
#: and still under 1% of the cap.
_CONTAINER_HEADROOM_BYTES = 64 * 1024

#: Bounds an uploaded clip's size in bytes, and is the ONLY bound for a
#: container whose duration cannot be read (WebM/MP4). Sized to hold
#: :data:`MAX_CLIP_SECONDS` of the heaviest uncompressed upload and no more:
#: 60 s * 192 000 B/s = 11.52 MB, plus the header headroom (11 585 536 B). The
#: old 16 MiB was sized for 300 s of pcm16 mono 24 kHz.
MAX_CLIP_BYTES = int(MAX_CLIP_SECONDS * _HEAVIEST_UNCOMPRESSED_BYTES_PER_S) + (
    _CONTAINER_HEADROOM_BYTES
)

#: Bounds the stt response body read. A transcript of :data:`MAX_CLIP_SECONDS`
#: of speech is far under this (a few KB); it only stops a hostile gateway.
MAX_TRANSCRIPT_BYTES = 256 * 1024

#: Bounds the audio ``speak_message`` returns: 120 s of pcm16 mono 24 kHz
#: (5.76 MB). A **judgement call** — a chat message read aloud is seconds.
MAX_SPEAK_AUDIO_BYTES = 120 * SAMPLE_RATE_HZ * BYTES_PER_SAMPLE

#: Seconds of transcription per second of audio on the local ``stt`` lane
#: (``ivrit-ai/whisper-large-v3-turbo``), MEASURED: the max of n = 28 synthetic
#: Hebrew clips of 1-8.8 s (median 0.314, p90 0.497, max 0.614), lapse l3 in
#: ``docs/live-test-results/2026-10-23-recall-gemma2.md``. The max, not the
#: median, because this sizes a deadline. Speed only, synthetic speech, clips
#: far shorter than the cap: a 60 s clip at this rate is an extrapolation.
#: Replaced ``_STT_ASSUMED_REALTIME_FACTOR = 10.0`` (an assumption, lapse l3).
STT_SECONDS_PER_AUDIO_SECOND = 0.614

#: Bounds the whole transcription call, connect to last body byte. Derived from
#: the quantity it bounds (lesson 1): the longest accepted clip at the measured
#: worst speed, plus 10 s for connect and model queueing (a **judgement call**):
#: 10 + 60 * 0.614 = 46.84 s.
STT_DEADLINE_S = 10.0 + MAX_CLIP_SECONDS * STT_SECONDS_PER_AUDIO_SECOND

#: Bounds one whole ``speak_message`` (every sentence's synthesis together).
#: A **judgement call**: a message of a few sentences at the voice's ~seconds
#: per call; synthesis stops and the audio so far is returned past this.
SPEAK_DEADLINE_S = 60.0

TEXT_TURN_FAILED = "text-turn-failed"
TEXT_TURN_NO_SESSION = "text-turn-no-session"
TEXT_TURN_SESSION_FAILED = "text-turn-session-failed"
SPEAK_NO_TTS = "speak-no-tts"
SPEAK_TTS_FAILED = "speak-tts-failed"
SPEAK_NO_AUDIO = "speak-no-audio"
SPEAK_TRUNCATED = "speak-truncated"
CLIP_STT_FAILED = "clip-stt-failed"
CLIP_STT_MALFORMED = "clip-stt-malformed"
CLIP_EMPTY_TRANSCRIPT = "clip-empty-transcript"

# Refusal codes carry no degradation (a refusal is the caller's input, not a fault).
REFUSED_NOT_TEXT = "refused-not-text"
REFUSED_EMPTY = "refused-empty"
REFUSED_TEXT_TOO_LONG = "refused-text-too-long"
REFUSED_CLIP_TOO_BIG = "refused-clip-too-big"
REFUSED_CLIP_TOO_LONG = "refused-clip-too-long"
REFUSED_CLIP_UNSUPPORTED = "refused-clip-unsupported"
REFUSED_CLIP_UNREADABLE = "refused-clip-unreadable"

_MAX_DEGRADATIONS = 256


@dataclass(frozen=True)
class TextTurnsConfig:
    """Where the default stt/tts seams reach. ``api_key`` has ``repr=False``."""

    gateway_url: str = DEFAULT_GATEWAY_URL
    api_key: str = field(default="", repr=False)
    #: Sent as the ``model`` form field; empty omits it so the gateway uses its stt role.
    stt_model: str = ""
    #: Sent as ``language``; Gwen is Hebrew-only in v1.
    language: str = "he"
    voice: str = ""
    max_text_chars: int = MAX_TEXT_CHARS
    max_clip_bytes: int = MAX_CLIP_BYTES
    max_clip_seconds: float = MAX_CLIP_SECONDS
    stt_deadline: float = STT_DEADLINE_S
    speak_deadline: float = SPEAK_DEADLINE_S

    def voice_config(self) -> VoiceConfig:
        return VoiceConfig(gateway_url=self.gateway_url, api_key=self.api_key, voice=self.voice)


@dataclass(frozen=True)
class TextTurnResult:
    """A typed turn's outcome. ``reply`` is text; nothing was played."""

    ok: bool
    reply: str = ""
    code: str = ""
    http_status: int = 200


@dataclass(frozen=True)
class SpeakResult:
    """``audio`` is a complete in-memory WAV (pcm16 mono 24 kHz) for the browser."""

    ok: bool
    audio: bytes = b""
    content_type: str = "audio/wav"
    sentences: int = 0
    truncated: bool = False
    code: str = ""
    http_status: int = 200


@dataclass(frozen=True)
class ClipProbe:
    """What the header said. ``duration_s`` is ``None`` when it cannot be read."""

    kind: str  # wav | ogg | webm | mp4 | mp3 | flac | unknown
    content_type: str
    extension: str
    duration_s: Optional[float]
    unreadable: bool = False


@dataclass(frozen=True)
class ClipResult:
    """A clip's outcome. ``transcript`` is text only; ``reply`` is the typed turn's."""

    ok: bool
    transcript: str = ""
    reply: str = ""
    duration_s: Optional[float] = None
    code: str = ""
    http_status: int = 200


SttFn = Callable[[bytes, str, str], str]
TtsFn = Callable[[str, VoiceConfig], bytes]


# ── clip header probing (stdlib, in memory) ─────────────────────────────────


def _probe_wav(data: bytes) -> Optional[float]:
    with wave.open(io.BytesIO(data), "rb") as handle:
        rate = handle.getframerate()
        return handle.getnframes() / rate if rate > 0 else None


def _probe_ogg(data: bytes) -> Optional[float]:
    """Duration from the last page's granule position and the stream's rate."""
    head = data[:512]
    if b"OpusHead" in head:
        rate = 48000  # Opus granules are always 48 kHz
    elif b"\x01vorbis" in head:
        at = head.index(b"\x01vorbis")
        if len(head) < at + 16:
            return None
        rate = struct.unpack_from("<I", head, at + 12)[0]
    else:
        return None
    last = data.rfind(b"OggS", max(0, len(data) - 65536))
    if last < 0 or len(data) < last + 14 or rate <= 0:
        return None
    granule = struct.unpack_from("<q", data, last + 6)[0]
    return granule / rate if granule >= 0 else None


def probe_clip(data: bytes) -> ClipProbe:
    """Classify *data* by magic bytes and read its duration if the header has one.

    Never raises. A header that claims a format and fails to parse is
    ``unreadable`` (refused); an unrecognised one is ``unknown`` (refused,
    415) — the gateway is not asked to guess at arbitrary bytes.
    """
    try:
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            try:
                return ClipProbe("wav", "audio/wav", "wav", _probe_wav(data))
            except (wave.Error, EOFError, ZeroDivisionError, struct.error):
                return ClipProbe("wav", "audio/wav", "wav", None, unreadable=True)
        if data[:4] == b"OggS":
            return ClipProbe("ogg", "audio/ogg", "ogg", _probe_ogg(data))
        if data[:4] == b"\x1a\x45\xdf\xa3":
            return ClipProbe("webm", "audio/webm", "webm", None)
        if data[4:8] == b"ftyp":
            return ClipProbe("mp4", "audio/mp4", "mp4", None)
        if data[:4] == b"fLaC":
            return ClipProbe("flac", "audio/flac", "flac", None)
        if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return ClipProbe("mp3", "audio/mpeg", "mp3", None)
    except Exception:  # noqa: BLE001  # a probe must not be the thing that raises
        return ClipProbe("unknown", "", "", None, unreadable=True)
    return ClipProbe("unknown", "", "", None)


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE_HZ) -> bytes:
    """Wrap pcm16 mono in a WAV container, in memory, so a browser can play it."""
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(BYTES_PER_SAMPLE)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return out.getvalue()


# ── the default stt seam ────────────────────────────────────────────────────


def _multipart(fields: dict[str, str], filename: str, content_type: str, audio: bytes):
    boundary = f"embodiment-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        disposition = f'Content-Disposition: form-data; name="{name}"'
        parts.append(f"--{boundary}\r\n{disposition}\r\n\r\n{value}\r\n".encode())
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
        ).encode()
    )
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def http_transcribe(
    audio: bytes,
    filename: str,
    content_type: str,
    config: TextTurnsConfig,
    *,
    opener: Optional[Any] = None,
) -> str:
    """``POST /v1/audio/transcriptions``: the clip goes in the request body, in memory.

    Raises on any failure. Redirects are refused (the bearer key stays on the
    configured origin) and the response body is read under a hard byte cap and
    a total deadline, the same discipline as :func:`embodiment.voice.http_synthesize`.
    """
    url = f"{config.gateway_url.rstrip('/')}{TRANSCRIPTION_ROUTE}"
    if not url.startswith(("http://", "https://")):
        raise ValueError("gateway_url must be http(s)")
    fields = {"response_format": "json"}
    if config.stt_model:
        fields["model"] = config.stt_model
    if config.language:
        fields["language"] = config.language
    body, ctype = _multipart(fields, filename, content_type, audio)
    headers = {"Content-Type": ctype}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(  # nosec B310 - scheme checked above
        url, data=body, headers=headers, method="POST"
    )
    active = opener if opener is not None else _REDIRECT_REFUSING_OPENER
    with active.open(  # nosec B310 - scheme checked above, redirects refused
        request, timeout=config.stt_deadline
    ) as response:
        raw = _read_bounded(
            response, max_bytes=MAX_TRANSCRIPT_BYTES, deadline_s=config.stt_deadline
        )
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("transcription response over cap")
    payload = json.loads(raw.decode("utf-8"))
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise ValueError("transcription response has no text")
    return text


# ── the handlers ────────────────────────────────────────────────────────────


class TextTurns:
    """Typed turns, per-message speech and clip transcription. Never raises."""

    def __init__(
        self,
        *,
        run_text_turn: Callable[[str], Any],
        session: Any = None,
        tts: Optional[TtsFn] = None,
        stt: Optional[SttFn] = None,
        config: Optional[TextTurnsConfig] = None,
        record: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run_text_turn = run_text_turn
        self._session = session
        self._config = config or TextTurnsConfig()
        self._tts = tts if tts is not None else http_synthesize
        self._stt = stt if stt is not None else self._default_stt
        self._record_sink = record
        self._clock = clock
        self.degradations: list[tuple[str, str]] = []
        self.sink_failures = 0

    def _default_stt(self, audio: bytes, filename: str, content_type: str) -> str:
        return http_transcribe(audio, filename, content_type, self._config)

    def _record(self, code: str, reason: str) -> None:
        """Keep the degradation and hand it to the daemon's sink; the sink may fail."""
        if len(self.degradations) < _MAX_DEGRADATIONS:
            self.degradations.append((code, reason))
        if self._record_sink is None:
            return
        try:
            self._record_sink(code, reason)
        except Exception:  # noqa: BLE001  # the sink is a seam; the handler still returns
            self.sink_failures += 1  # counted, and the degradation is still on self.degradations

    def _current_session(self) -> Any:
        session = self._session
        if session is not None and callable(session):
            try:
                session = session()
            except Exception as exc:  # noqa: BLE001
                self._record(TEXT_TURN_SESSION_FAILED, safe_reason.describe_exception(exc))
                return None
        return session

    # -- typed turns ---------------------------------------------------------

    def handle_text(self, text: object) -> TextTurnResult:
        """Run one typed message as a turn in the current session; reply as text."""
        try:
            return self._handle_text(text)
        except Exception as exc:  # noqa: BLE001  # degrade, never raise
            self._record(TEXT_TURN_FAILED, safe_reason.describe_exception(exc))
            return TextTurnResult(False, code=TEXT_TURN_FAILED, http_status=500)

    def _handle_text(self, text: object) -> TextTurnResult:
        if not isinstance(text, str):
            return TextTurnResult(False, code=REFUSED_NOT_TEXT, http_status=400)
        message = text.strip()
        if not message:
            return TextTurnResult(False, code=REFUSED_EMPTY, http_status=400)
        if len(message) > self._config.max_text_chars:
            return TextTurnResult(False, code=REFUSED_TEXT_TOO_LONG, http_status=413)

        session = self._current_session()
        if session is None:
            self._record(TEXT_TURN_NO_SESSION, "typed turn ran without a session")
        else:
            self._session_call(lambda: session.add_user(message))

        try:
            result = self._run_text_turn(message)
        except Exception as exc:  # noqa: BLE001
            self._record(TEXT_TURN_FAILED, safe_reason.describe_exception(exc))
            return TextTurnResult(False, code=TEXT_TURN_FAILED, http_status=500)
        reply = result if isinstance(result, str) else getattr(result, "spoken", None)
        if not isinstance(reply, str) or not reply.strip():
            self._record(TEXT_TURN_FAILED, "turn produced no reply text")
            return TextTurnResult(False, code=TEXT_TURN_FAILED, http_status=500)

        if session is not None:
            self._session_call(lambda: session.add_assistant(reply))
        return TextTurnResult(True, reply=reply)

    def _session_call(self, call: Callable[[], Any]) -> None:
        try:
            call()
        except Exception as exc:  # noqa: BLE001  # the session is a seam
            self._record(TEXT_TURN_SESSION_FAILED, safe_reason.describe_exception(exc))

    # -- the speak button ----------------------------------------------------

    def speak_message(self, text: object) -> SpeakResult:
        """Synthesise *text* through the tts lane and return the audio. Writes nothing."""
        try:
            return self._speak_message(text)
        except Exception as exc:  # noqa: BLE001
            self._record(SPEAK_TTS_FAILED, safe_reason.describe_exception(exc))
            return SpeakResult(False, code=SPEAK_TTS_FAILED, http_status=502)

    def _speak_message(self, text: object) -> SpeakResult:
        if not isinstance(text, str):
            return SpeakResult(False, code=REFUSED_NOT_TEXT, http_status=400)
        sentences = split_sentences(text)
        if not sentences:
            return SpeakResult(False, code=REFUSED_EMPTY, http_status=400)
        voice_config = self._config.voice_config()
        started = self._clock()
        pcm = bytearray()
        used = 0
        truncated = False
        for sentence in sentences:
            if self._clock() - started > self._config.speak_deadline:
                truncated = True
                break
            try:
                chunk = self._tts(sentence, voice_config)
            except Exception as exc:  # noqa: BLE001
                self._record(SPEAK_TTS_FAILED, safe_reason.describe_exception(exc))
                if not pcm:
                    return SpeakResult(False, code=SPEAK_TTS_FAILED, http_status=502)
                truncated = True
                break
            if not isinstance(chunk, (bytes, bytearray)) or not chunk:
                self._record(SPEAK_TTS_FAILED, "tts returned no audio bytes")
                if not pcm:
                    return SpeakResult(False, code=SPEAK_TTS_FAILED, http_status=502)
                truncated = True
                break
            room = MAX_SPEAK_AUDIO_BYTES - len(pcm)
            if len(chunk) > room:
                pcm += bytes(chunk[: room - room % BYTES_PER_SAMPLE])
                truncated = True
                break
            pcm += chunk
            used += 1
        if not pcm:
            self._record(SPEAK_NO_AUDIO, "no sentence produced audio")
            return SpeakResult(False, code=SPEAK_NO_AUDIO, http_status=502)
        if truncated:
            self._record(SPEAK_TRUNCATED, f"spoke {used} of {len(sentences)} sentences")
        return SpeakResult(True, audio=pcm_to_wav(bytes(pcm)), sentences=used, truncated=truncated)

    # -- clips ---------------------------------------------------------------

    def handle_clip(self, audio: object, *, declared_duration_s: object = None) -> ClipResult:
        """Transcribe a clip in memory and run the transcript as a typed turn.

        Refusals (413/415/400) happen BEFORE any call to the stt lane. The clip
        is never written anywhere; it is dropped when this call returns.
        """
        try:
            return self._handle_clip(audio, declared_duration_s)
        except Exception as exc:  # noqa: BLE001
            self._record(CLIP_STT_FAILED, safe_reason.describe_exception(exc))
            return ClipResult(False, code=CLIP_STT_FAILED, http_status=502)

    def _handle_clip(self, audio: object, declared: object) -> ClipResult:
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            return ClipResult(False, code=REFUSED_EMPTY, http_status=400)
        cfg = self._config
        if len(audio) > cfg.max_clip_bytes:
            return ClipResult(False, code=REFUSED_CLIP_TOO_BIG, http_status=413)
        data = bytes(audio)
        probe = probe_clip(data)
        if probe.unreadable:
            return ClipResult(False, code=REFUSED_CLIP_UNREADABLE, http_status=400)
        if probe.kind == "unknown":
            return ClipResult(False, code=REFUSED_CLIP_UNSUPPORTED, http_status=415)
        duration = probe.duration_s
        # A caller-declared duration is untrusted and can only refuse MORE.
        if isinstance(declared, (int, float)) and not isinstance(declared, bool):
            if not math.isnan(declared) and declared > cfg.max_clip_seconds:
                return ClipResult(False, code=REFUSED_CLIP_TOO_LONG, http_status=413)
        if duration is not None and duration > cfg.max_clip_seconds:
            return ClipResult(
                False, duration_s=duration, code=REFUSED_CLIP_TOO_LONG, http_status=413
            )

        try:
            transcript = self._stt(data, f"clip.{probe.extension}", probe.content_type)
        except Exception as exc:  # noqa: BLE001
            self._record(CLIP_STT_FAILED, safe_reason.describe_exception(exc))
            return ClipResult(False, duration_s=duration, code=CLIP_STT_FAILED, http_status=502)
        if not isinstance(transcript, str):
            self._record(CLIP_STT_MALFORMED, "stt returned a non-text transcript")
            return ClipResult(False, duration_s=duration, code=CLIP_STT_MALFORMED, http_status=502)
        transcript = transcript.strip()
        if not transcript:
            self._record(CLIP_EMPTY_TRANSCRIPT, "the clip transcribed to nothing")
            return ClipResult(
                False, duration_s=duration, code=CLIP_EMPTY_TRANSCRIPT, http_status=422
            )
        turn = self.handle_text(transcript)
        return ClipResult(
            turn.ok,
            transcript=transcript,
            reply=turn.reply,
            duration_s=duration,
            code=turn.code,
            http_status=turn.http_status,
        )
