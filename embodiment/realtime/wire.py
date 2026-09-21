"""The lobes ``/v1/realtime`` wire codec — pure, stdlib-only, ears-only.

What this module is
-------------------
One JSON event in, one typed value out; one value in, one JSON text frame out.
It opens no socket, reads no clock, holds no state and imports nothing outside
the standard library. :mod:`embodiment.realtime.client` owns the socket; this
module owns the *shapes* that travel over it, so the shapes can be tested
against fixtures with no network at all.

The wire it speaks is lobes' base64 JSON event wire: client→server
``input_audio_buffer.append`` carrying base64 PCM16, server→client typed JSON
events. The session's configuration is **connect-URL query parameters**, not a
first message — :func:`session_query` builds them and :func:`realtime_url`
puts them on the URL.

Ears only, structurally
-----------------------
This codec can encode exactly two client events: an audio append and a
language declaration. It has no encoder for the two client events that would
turn a listening session into a talking one, and it contains no string naming
either of them — ``tests/test_realtime_wire.py::TestEarsOnly`` parses this
file and ``client.py`` and fails on the literals, which are spelled only in
that test. A session that never sends them is transcription-only, which is the
contract the daemon depends on: the daemon owns the turn, so that memory,
vision and tools can enter the prompt before anything is said back.

Never raise on a server you do not fully know
---------------------------------------------
:func:`decode_server_event` returns a value for **every** input. A JSON object
whose ``type`` this module does not consume decodes to :class:`UnknownEvent`
with the payload intact; anything that is not a JSON object at all decodes to
:class:`MalformedEvent` carrying a short *category*, never the frame. A field
of the wrong type degrades that field, never the event: a server that grows a
key, renames one, or ships a null where an int was promised is a server this
client keeps listening to. Raising here would put an exception on the one path
between a microphone and a daemon that must not stop.

No speech in a record
---------------------
:class:`TranscriptionCompleted` carries the transcript, because the transcript
is the entire product of the session. Its ``__repr__`` does not: an event that
gets logged, formatted into a traceback, or dropped into a degradation reason
must not carry what somebody said. Only the character count survives.

Sample rate, format and language are the rig's, declared not discovered:
PCM16 mono little-endian at 24000 Hz, server-side VAD, ``aec_mode=aec``
(echo cancellation is owned at the client edge; lobes accepts the declaration
and runs no DSP behind it), ``language=he``.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

__all__ = [
    "REALTIME_PATH",
    "CAPABILITIES_PATH",
    "STT_ROLE",
    "REALTIME_RESPONSIBILITY",
    "AUDIO_FORMAT",
    "INPUT_SAMPLE_RATE",
    "BYTES_PER_SAMPLE",
    "CHANNELS",
    "TURN_DETECTION",
    "AEC_MODE",
    "LANGUAGE",
    "APPEND_EVENT_TYPE",
    "SESSION_UPDATE_EVENT_TYPE",
    "SERVER_ERROR_CODES",
    "session_query",
    "realtime_url",
    "encode_audio_append",
    "encode_session_update",
    "decode_server_event",
    "ServerEvent",
    "SessionCreated",
    "SessionUpdated",
    "SessionClosed",
    "SpeechStarted",
    "SpeechStopped",
    "TranscriptionCompleted",
    "ServerError",
    "UnknownEvent",
    "MalformedEvent",
]

# --- routes and discovery tokens --------------------------------------------

#: The session route, served by the gateway (never the bridge port directly).
REALTIME_PATH = "/v1/realtime"
#: The keyless discovery route. Control-plane: it is never bearer-gated.
CAPABILITIES_PATH = "/capabilities"
#: The role that owns the session lane — a box that dropped it cannot serve one.
STT_ROLE = "stt"
#: The responsibility token an ``stt`` advert carries only when the audio
#: overlay is actually wired AND the lane is feasible on that box.
REALTIME_RESPONSIBILITY = "realtime_vad_session"

# --- the declared session config ---------------------------------------------

AUDIO_FORMAT = "pcm16"
#: 24000 Hz is the wire default and what the rig speaks; lobes resamples to the
#: transcriber's own rate server-side, so the client never resamples.
INPUT_SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2
CHANNELS = 1
TURN_DETECTION = "server_vad"
#: Declared, not implemented here: echo cancellation belongs to the capture
#: hardware. Declaring it is how the session records whose job it is.
AEC_MODE = "aec"
LANGUAGE = "he"

# --- event names -------------------------------------------------------------

APPEND_EVENT_TYPE = "input_audio_buffer.append"
SESSION_UPDATE_EVENT_TYPE = "session.update"

_SESSION_CREATED = "session.created"
_SESSION_UPDATED = "session.updated"
_SESSION_CLOSED = "session.closed"
_SPEECH_STARTED = "input_audio_buffer.speech_started"
_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"
_TRANSCRIPTION_COMPLETED = "conversation.item.input_audio_transcription.completed"
_ERROR = "error"

#: Every error code the server documents. A code outside this set still decodes
#: to :class:`ServerError` — the set is what a host can *enumerate*, never a
#: filter that would drop an error because it is new.
SERVER_ERROR_CODES: tuple[str, ...] = (
    "invalid_session_config",
    "vad_unavailable",
    "invalid_wire_event",
    "stt_forward_failed",
    "generate_failed",
    "tts_failed",
    "response_timeout",
)

#: A decode failure's reason is one of these categories. Never the frame: a
#: server frame is data this client did not write, and a reason string ends up
#: in logs.
_MALFORMED_NOT_JSON = "frame did not parse as JSON"
_MALFORMED_NOT_OBJECT = "frame parsed to a non-object JSON value"
_MALFORMED_NOT_TEXT = "frame was not decodable text"


# ── the connect URL ──────────────────────────────────────────────────────────


def session_query(
    *,
    input_sample_rate: int = INPUT_SAMPLE_RATE,
    aec_mode: str = AEC_MODE,
    language: str = LANGUAGE,
) -> str:
    """The connect-URL query string declaring the whole session config.

    Every parameter is sent explicitly, including the ones whose value equals
    the server's own default. A session that relies on a default is a session
    whose configuration is written down on the other machine.
    """
    return urlencode(
        {
            "input_audio_format": AUDIO_FORMAT,
            "input_sample_rate": int(input_sample_rate),
            "input_channels": CHANNELS,
            "turn_detection": TURN_DETECTION,
            "aec_mode": aec_mode,
            "language": language,
        }
    )


def realtime_url(
    origin: str,
    *,
    input_sample_rate: int = INPUT_SAMPLE_RATE,
    aec_mode: str = AEC_MODE,
    language: str = LANGUAGE,
) -> str:
    """``ws(s)://<same origin>/v1/realtime?<config>`` for an http(s) *origin*.

    Scheme and only the scheme changes: same host, same port, **same
    security**. ``https`` and an already-``wss`` origin both stay encrypted —
    silently downgrading a caller's TLS to carry a microphone in the clear is
    the one transformation this function must never make. Any path on *origin*
    is discarded: the session lives at the origin, and a caller who passed the
    discovery route's URL meant the same gateway.

    An *origin* with no scheme at all (``host:port``) is read as a netloc
    rather than as a scheme, which is what a caller who wrote it meant. This
    function never raises: a nonsensical origin produces a nonsensical URL and
    the dial that follows fails with a named degradation, which is a better
    report than a ``ValueError`` out of a config accessor.
    """
    parts = urlsplit(origin.strip())
    scheme = "wss" if parts.scheme in ("https", "wss") else "ws"
    netloc = parts.netloc
    if not netloc:
        # No "//" in the input: urlsplit read "localhost:8001" as scheme
        # "localhost" + path "8001". Put it back together.
        netloc = (f"{parts.scheme}:{parts.path}" if parts.scheme else parts.path).strip("/")
    query = session_query(input_sample_rate=input_sample_rate, aec_mode=aec_mode, language=language)
    return f"{scheme}://{netloc}{REALTIME_PATH}?{query}"


# ── the two client encoders ──────────────────────────────────────────────────


def encode_audio_append(pcm: bytes) -> str:
    """One audio frame as the JSON text the session accepts.

    Chunking is the caller's choice: a frame need not align to a whole sample,
    let alone a whole VAD chunk — the server reassembles the stream. An empty
    buffer is a valid frame carrying zero bytes of audio, not an error.
    """
    return json.dumps({"type": APPEND_EVENT_TYPE, "audio": base64.b64encode(pcm).decode("ascii")})


def encode_session_update(*, language: str = LANGUAGE) -> str:
    """Re-declare the session's language mid-stream.

    The server reads three fields from this event and ``language`` is the only
    one an ears-only client has any use for; the other two declare tools, which
    this client does not have. ``turn_detection`` and ``aec_mode`` are **not**
    readable here — they are connect-URL parameters (:func:`session_query`) and
    a server told about them this way would visibly ignore them, which is why
    this encoder does not offer them.
    """
    return json.dumps({"type": SESSION_UPDATE_EVENT_TYPE, "session": {"language": language}})


# ── the decoded server events ────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerEvent:
    """The base every decoded event shares: who, which, when.

    ``session_id``/``event_id`` are empty and ``timestamp_ms`` is ``None`` when
    the frame omitted them or typed them wrongly — an absent value, never a
    fabricated zero that would read as "the start of the session".
    """

    session_id: str = ""
    event_id: str = ""
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class SessionCreated(ServerEvent):
    """The handshake succeeded and the server states the effective config."""

    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionUpdated(ServerEvent):
    """The echo of what a session update actually changed — only that."""

    applied: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionClosed(ServerEvent):
    """The server ended the session, and says why."""

    reason: str = ""


@dataclass(frozen=True)
class SpeechStarted(ServerEvent):
    """A VAD onset. ``at_ms`` is 32 ms-quantised audio-stream time — a
    different clock from ``timestamp_ms``, which is the server's process
    clock. Never mix them."""

    item_id: str = ""
    at_ms: int | None = None


@dataclass(frozen=True)
class SpeechStopped(ServerEvent):
    """A committed turn boundary.

    ``reason`` is ``"silence"`` (the VAD's own confirmation) or ``"max_turn"``
    (the force-commit on a stream that never falls silent). The second is an
    ordinary boundary, not an error: the turn goes on to be transcribed
    exactly like any other.
    """

    item_id: str = ""
    at_ms: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class TranscriptionCompleted(ServerEvent):
    """The committed turn's text.

    The one event that carries speech. Its ``__repr__`` deliberately does not
    — see the module docstring.
    """

    item_id: str = ""
    text: str = ""

    def __repr__(self) -> str:
        return (
            f"TranscriptionCompleted(session_id={self.session_id!r}, "
            f"item_id={self.item_id!r}, chars={len(self.text)})"
        )


@dataclass(frozen=True)
class ServerError(ServerEvent):
    """A named server-side failure. ``code`` is the contract; ``message`` is
    free text where a sub-reason is named, and is never parsed here."""

    code: str = ""
    message: str = ""
    item_id: str = ""


@dataclass(frozen=True)
class UnknownEvent(ServerEvent):
    """A well-formed event this client does not consume.

    Not an error. An ears-only session should never see the conversation
    surface's events at all, so one arriving means the server is doing
    something this client was not built for — which is worth handing to the
    host intact, not worth raising over.
    """

    event_type: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MalformedEvent(ServerEvent):
    """A frame that was not a JSON object. ``reason`` is a category, never the
    frame's own bytes."""

    reason: str = ""


# ── decoding ─────────────────────────────────────────────────────────────────


def _text(value: object, default: str = "") -> str:
    """A string field, or *default* — a non-string never becomes ``str(x)``."""
    return value if isinstance(value, str) else default


def _whole(value: object) -> int | None:
    """An integer field, or ``None``. ``bool`` is not an integer here."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _mapping(value: object) -> Mapping[str, Any]:
    """A nested object field, copied, or an empty mapping."""
    return dict(value) if isinstance(value, dict) else {}


def decode_server_event(raw: object) -> ServerEvent:
    """Decode one received frame. Total: every input returns a value.

    *raw* is whatever the socket handed back — ``str`` for a text frame,
    ``bytes`` for a binary one (which this wire never carries, so it is
    attempted as UTF-8 and categorised if it is not).
    """
    if isinstance(raw, (bytes, bytearray, memoryview)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return MalformedEvent(reason=_MALFORMED_NOT_TEXT)
    if not isinstance(raw, str):
        return MalformedEvent(reason=_MALFORMED_NOT_TEXT)
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return MalformedEvent(reason=_MALFORMED_NOT_JSON)
    if not isinstance(payload, dict):
        return MalformedEvent(reason=_MALFORMED_NOT_OBJECT)

    common = {
        "session_id": _text(payload.get("session_id")),
        "event_id": _text(payload.get("event_id")),
        "timestamp_ms": _whole(payload.get("timestamp_ms")),
    }
    kind = _text(payload.get("type"))

    if kind == _SESSION_CREATED:
        return SessionCreated(**common, config=_mapping(payload.get("config")))
    if kind == _SESSION_UPDATED:
        return SessionUpdated(**common, applied=_mapping(payload.get("session")))
    if kind == _SESSION_CLOSED:
        return SessionClosed(**common, reason=_text(payload.get("reason")))
    if kind == _SPEECH_STARTED:
        return SpeechStarted(
            **common,
            item_id=_text(payload.get("item_id")),
            at_ms=_whole(payload.get("at_ms")),
        )
    if kind == _SPEECH_STOPPED:
        return SpeechStopped(
            **common,
            item_id=_text(payload.get("item_id")),
            at_ms=_whole(payload.get("at_ms")),
            reason=_text(payload.get("reason")),
        )
    if kind == _TRANSCRIPTION_COMPLETED:
        return TranscriptionCompleted(
            **common,
            item_id=_text(payload.get("item_id")),
            text=_text(payload.get("text")),
        )
    if kind == _ERROR:
        return ServerError(
            **common,
            code=_text(payload.get("code")),
            message=_text(payload.get("message")),
            item_id=_text(payload.get("item_id")),
        )
    return UnknownEvent(**common, event_type=kind, payload=dict(payload))
