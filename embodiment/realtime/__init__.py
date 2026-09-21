"""The realtime ears: a lobes ``/v1/realtime`` session, ears-only and bounded.

Two modules, split so the half that can be tested without a socket is:

* :mod:`embodiment.realtime.wire` — pure codec. The connect-URL config, the two
  client encoders, and a total decoder that returns a typed value for every
  frame a server could send, including ones it has never seen.
* :mod:`embodiment.realtime.client` — :class:`~embodiment.realtime.client.RealtimeEars`:
  keyless discovery, the bearer handshake, bounded audio out, decoded events
  in, and a close that reports what it left. Every fault is one named
  degradation and a still-running caller; nothing here raises.

The session never speaks. lobes can run a whole spoken turn server-side; the
daemon declines it so memory, vision and tools can enter the prompt first. What
this package delivers is a transcript and a turn boundary — what is done with
them is the daemon's.

Nothing here imports ``lobes``: the gateway is reached over the network only.
"""

from __future__ import annotations

from embodiment.realtime.client import (
    CloseReport,
    RealtimeConfig,
    RealtimeDegradation,
    RealtimeEars,
)
from embodiment.realtime.wire import (
    AEC_MODE,
    INPUT_SAMPLE_RATE,
    LANGUAGE,
    REALTIME_RESPONSIBILITY,
    SERVER_ERROR_CODES,
    MalformedEvent,
    ServerError,
    ServerEvent,
    SessionClosed,
    SessionCreated,
    SessionUpdated,
    SpeechStarted,
    SpeechStopped,
    TranscriptionCompleted,
    UnknownEvent,
    decode_server_event,
    realtime_url,
)

__all__ = [
    "RealtimeEars",
    "RealtimeConfig",
    "RealtimeDegradation",
    "CloseReport",
    "decode_server_event",
    "realtime_url",
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
    "SERVER_ERROR_CODES",
    "REALTIME_RESPONSIBILITY",
    "INPUT_SAMPLE_RATE",
    "AEC_MODE",
    "LANGUAGE",
]
