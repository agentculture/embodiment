"""The realtime ears: one lobes ``/v1/realtime`` session, and never an exception.

What this is
------------
:class:`RealtimeEars` discovers a lobes gateway's session lane, dials it,
streams captured PCM16 to it, and hands back the typed events
:mod:`embodiment.realtime.wire` decodes. It is the **perception seam** of the
presence loop: audio in, turn boundaries and transcripts out. It never speaks,
never decides, and never raises into the daemon that owns it.

Ears only
---------
The session is transcription-only by construction. This module has no encoder
for the two client events that arm a server-side reply, and contains no string
naming either of them — ``tests/test_realtime_wire.py::TestEarsOnly`` parses
this file and fails on the literals. The daemon takes the transcript, recalls,
calls its own model and speaks through the batch speech route; the socket is a
listening ear, never a second voice. Barge-in is the daemon's too: on a speech
onset during playback it stops its own speaker.

Never raises, always records
----------------------------
Every public method returns a value. Each distinct fault produces **exactly
one** :class:`RealtimeDegradation` naming that fault — an absent advert is not
a handshake failure, a refused upgrade is not a dead port, a mid-session drop
is not a malformed frame — and leaves the caller running. Repeating faults
(a stalled queue dropping its two-hundredth frame, audio pushed into a dead
socket) record once per session, because a record that arrives thirty times a
second is a log, not a signal. The counters keep counting; only the record is
once.

There is deliberately **no reconnect loop** here. :attr:`connected` and
:attr:`degraded` say where the session stands and the caller decides what to
do about it — a lifecycle that retries belongs above this seam, where it can
see whether retrying is still the right thing.

Every wait is bounded
---------------------
Discovery, the handshake and the close each take a caller-set deadline.
The defaults are *chosen*, not measured, except where noted:

* ``discovery_deadline`` 5.0 s. ``GET /capabilities`` is computed in-process by
  the gateway with no model call behind it; measured on this rig it answers in
  12–37 ms over five samples. 5 s is two orders of magnitude of headroom, which
  is what a deadline should be — large enough that it never fires on a healthy
  box, small enough that a wedged one is noticed inside one human breath.
* ``handshake_deadline`` 10.0 s. Bounds a TCP connect, the gateway's relay to
  its local bridge, and the bridge allocating a session. Chosen, not measured:
  the live dial in the test suite is the only sample.
* ``close_deadline`` 5.0 s, and a caller may pass a smaller one per call.

The load-bearing rule these obey: *a clock sized against the wrong quantity
silently becomes the measurement.* None of these three bounds a model
completion, so none of them can censor one.

The liveness clock — the fourth, and the one with teeth
--------------------------------------------------------
The three deadlines above all bound something that is *starting*. None of them
bounds a session that has already started and then goes quiet, and that is the
failure a voice app actually meets: a pulled cable, a NAT table entry expiring,
a gateway killed on another host. There is no FIN in any of those, so a reader
parked in ``recv`` learns nothing, ever. Left to the transport's defaults the
client would keep-alive at 20 s + 20 s and Gwen would be **deaf for up to 40
seconds with** :attr:`connected` **True and** :meth:`status` **looking
healthy** — which is this repo's third lesson verbatim: a healthy-looking status
is not evidence that anything was heard.

So the clock is named, owned and configured here rather than inherited. It has
**three** terms, not two, and the third is the one that is easy to miss:

* ``ping_interval`` 2.0 s — the peer can vanish the instant after a pong, so a
  whole interval can pass before the question is even asked.
* ``ping_timeout`` 4.0 s — how long the pong has to come back. Deliberately
  twice the interval: the pong comes from a bridge sharing a GPU box with two
  language models and a transcriber, and a momentarily blocked event loop must
  not read as a dead peer.
* ``close_handshake_timeout`` 2.0 s — after the keep-alive gives up, the
  transport still waits for a closing handshake the vanished peer will never
  send. **This was measured, not assumed**: against a deaf peer the age of the
  drop record was 0.6 / 1.4 / 2.4 s for close timeouts of 0.2 / 1.0 / 2.0 s
  with a 0.2 + 0.2 keep-alive — exactly the sum. Left at the transport's own
  10 s default it would dominate the other two, and a carefully argued 6 s
  bound would really have been 16.

Worst case, therefore, **8 s** from a peer vanishing to `events()` ending with
the record on the ledger — :attr:`RealtimeConfig.liveness_bound`, which is the
number a host should read, never one term of it. The three are *chosen*, not
measured, and the quantity they are sized against is a **spoken turn**: the
server confirms a turn boundary after 600 ms of silence and a short exchange is
a few seconds, so 40 s of deafness is several whole turns lost invisibly and 8 s
is at most one. Cost is negligible — a ping frame every 2 s against 48 000
bytes/s of audio.

:meth:`status` publishes the bound (``liveness_bound_s``) beside
``last_event_age_s`` and ``latency_s``, so a host can *see* deafness rather than
infer it. Read ``last_event_age_s`` carefully: keep-alive pongs are handled
inside the transport and never arrive as events, so a large value means only
that nobody has spoken — a silent room is silent. The health claim is the
bound and the drop record, not the age.

The secret
----------
The bearer key rides one HTTP header on one handshake and reaches nothing
else. It is absent from the connect URL, from every degradation record, from
``repr`` of the config and of the ears, and from :meth:`status`. A server that
echoes it back inside a refusal body gets it redacted out on the way into the
record: :func:`_safe` is the ONE sanitiser every reason string goes through.

Backpressure
------------
:meth:`send_audio` never blocks and never grows without bound. It is designed
to be called from a capture callback, so it takes a lock, appends to a
byte-bounded queue and returns. When the socket stalls, the **oldest** frames
are dropped — in a live conversation the newest audio is the audio that still
matters — and every drop is counted and surfaced in :meth:`status`. The bound
defaults to five seconds of wire audio, derived from the rate rather than
picked: ``24000 Hz x 2 bytes x 5 s``.

The transport import
--------------------
``websockets`` is an approved base dependency but is imported **lazily**, inside
the connect path, for two reasons that are the same reason: this module must be
importable, and its degradation vocabulary readable, on a host where the
transport is missing or broken — an ``ImportError`` at module scope would be an
exception into the daemon at import time, which is exactly what this module
exists not to do. The side effect is that ``tests/test_zero_deps.py``'s
measured runtime-import set stays green with no edit; that is a consequence of
the design, not its motive, and the two-line change that would let the import
move to module scope is named in this task's report.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Mapping, Optional

from embodiment.realtime import wire

__all__ = [
    "RealtimeConfig",
    "RealtimeDegradation",
    "RealtimeEars",
    "CloseReport",
    "REDACTED",
    "ADVERT_ABSENT",
    "AUDIO_DROPPED",
    "AUDIO_NOT_BYTES",
    "AUDIO_NOT_CONFIGURED",
    "CLOSE_INCOMPLETE",
    "DISCOVERY_FAILED",
    "DISCOVERY_MALFORMED",
    "FRAME_MALFORMED",
    "HANDSHAKE_FAILED",
    "HANDSHAKE_TIMEOUT",
    "NOT_CONNECTED",
    "ROLE_INFEASIBLE",
    "SERVER_ERROR",
    "SESSION_DROPPED",
    "TRANSPORT_MISSING",
    "UNAUTHORIZED",
    "UPGRADE_REQUIRED",
]

# ── the degradation vocabulary ───────────────────────────────────────────────
#
# One code per fault a host would look for, each naming the fault rather than
# its symptom: a gateway that never advertised the lane is not "connection
# failed", and a lane declared off on this box is not "not configured".

#: Discovery reached the gateway and no session lane was advertised at all.
ADVERT_ABSENT = "realtime-advert-absent"
#: The lane is declared off on this box. The record names the peer that hosts
#: it, when the gateway referred one — the session never crosses machines, so
#: a referral is information for the operator, not a route this client takes.
ROLE_INFEASIBLE = "realtime-role-infeasible"
#: Discovery could not be completed: DNS, a refused port, a timeout, a non-2xx.
DISCOVERY_FAILED = "realtime-discovery-failed"
#: Discovery answered with something that was not a capabilities object.
DISCOVERY_MALFORMED = "realtime-discovery-malformed"
#: The gateway serves no audio surface at all (a text-only fleet).
AUDIO_NOT_CONFIGURED = "realtime-audio-not-configured"
#: The route exists and the handshake was not one — a protocol-level refusal.
UPGRADE_REQUIRED = "realtime-upgrade-required"
#: The gateway's bearer gate refused the key, or there was none to send.
UNAUTHORIZED = "realtime-unauthorized"
#: The handshake failed for a reason with its own name above: a dead port, a
#: reset, an unclassified status.
HANDSHAKE_FAILED = "realtime-handshake-failed"
#: The handshake did not complete inside its deadline.
HANDSHAKE_TIMEOUT = "realtime-handshake-timeout"
#: An established session ended without this client closing it.
SESSION_DROPPED = "realtime-session-dropped"
#: A frame arrived that was not a JSON object. The session stays open.
FRAME_MALFORMED = "realtime-frame-malformed"
#: The server reported a named error event. The reason carries the code.
SERVER_ERROR = "realtime-server-error"
#: Captured audio was discarded because the send queue was at its bound.
AUDIO_DROPPED = "realtime-audio-dropped"
#: Something that was not a buffer of bytes was handed to :meth:`send_audio`.
AUDIO_NOT_BYTES = "realtime-audio-not-bytes"
#: Audio or events were asked for after the session was lost or shut.
NOT_CONNECTED = "realtime-not-connected"
#: The ``websockets`` transport could not be imported.
TRANSPORT_MISSING = "realtime-transport-missing"
#: :meth:`close` hit its deadline with work still outstanding.
CLOSE_INCOMPLETE = "realtime-close-incomplete"

#: What replaces the key anywhere it would otherwise be written down.
REDACTED = "[redacted]"

#: Reasons are bounded: a server can hand back a large body and a record is a
#: line in a ledger, not a transcript of the failure.
_MAX_REASON_LEN = 240

#: Five seconds of wire audio at the declared rate — derived, not picked.
_QUEUE_SECONDS = 5
DEFAULT_MAX_QUEUE_BYTES = wire.INPUT_SAMPLE_RATE * wire.BYTES_PER_SAMPLE * _QUEUE_SECONDS

#: How long the writer sleeps when the queue is empty. Well under one 32 ms VAD
#: chunk, so a poll can never be the reason a turn boundary moves.
DEFAULT_POLL_INTERVAL = 0.01

#: The liveness clock — see the module docstring. Sized against a SPOKEN TURN,
#: not against a network, and summing to an 8 s worst case where the transport's
#: own defaults sum to 40 s of silent deafness.
DEFAULT_PING_INTERVAL = 2.0
#: Twice the interval on purpose. The pong comes from a bridge that shares a GPU
#: box with two language models and a transcriber, and a blocked event loop must
#: not read as a dead peer.
DEFAULT_PING_TIMEOUT = 4.0
#: The THIRD term, and the one that is easy to miss: after the keep-alive gives
#: up, the transport still waits this long for a closing handshake the vanished
#: peer will never send. Measured on a deaf peer, the age of the drop record is
#: ping_interval + ping_timeout + this, exactly — 0.2/0.2/{0.2,1.0,2.0} produced
#: 0.6/1.4/2.4 s. Left at the transport's own 10 s default it would DOMINATE the
#: other two and a carefully argued 6 s bound would really be 16.
DEFAULT_CLOSE_HANDSHAKE_TIMEOUT = 2.0

_ENV_KEY = "EMBODIMENT_GATEWAY_KEY"
_ENV_KEY_FALLBACK = "CULTURE_VLLM_API_KEY"
_ENV_URL = "EMBODIMENT_GATEWAY_URL"
_DEFAULT_GATEWAY = "http://localhost:8001"


# ── values ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RealtimeDegradation:
    """One recorded, host-visible realtime degradation.

    Field-for-field a prefix of :class:`embodiment.loop.LoopDegradation` and
    its siblings, so a host folds ONE shape.
    """

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class CloseReport:
    """What :meth:`RealtimeEars.close` finished, and what it left.

    ``graceful`` is ``False`` when the socket did not shut inside the deadline
    or the writer had to be cancelled with frames still queued. The queued and
    dropped counts are what was left unfinished — shutdown that reports nothing
    is shutdown a host cannot audit.
    """

    graceful: bool = True
    queued_frames: int = 0
    queued_bytes: int = 0
    dropped_frames: int = 0
    deadline_exceeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "graceful": self.graceful,
            "queued_frames": self.queued_frames,
            "queued_bytes": self.queued_bytes,
            "dropped_frames": self.dropped_frames,
            "deadline_exceeded": self.deadline_exceeded,
        }


@dataclass(frozen=True)
class RealtimeConfig:
    """What one session runs under. Every field is a default, not a discovery.

    ``gateway_url`` is the origin discovery reads and, unless ``realtime_url``
    overrides it, the origin the session dials — same host, same port, scheme
    lifted to ``ws``/``wss``. ``realtime_url`` exists for a deployment that
    fronts the two routes separately; empty means *same origin*, which is the
    rule and what the rig runs.

    ``api_key`` carries ``repr=False``: the one place a secret is easy to leak
    is a dataclass's free repr.
    """

    gateway_url: str = _DEFAULT_GATEWAY
    realtime_url: str = ""
    api_key: str = field(default="", repr=False)
    language: str = wire.LANGUAGE
    aec_mode: str = wire.AEC_MODE
    input_sample_rate: int = wire.INPUT_SAMPLE_RATE
    discovery_deadline: float = 5.0
    handshake_deadline: float = 10.0
    close_deadline: float = 5.0
    ping_interval: float = DEFAULT_PING_INTERVAL
    ping_timeout: float = DEFAULT_PING_TIMEOUT
    #: Bounds the WebSocket CLOSING handshake — distinct from ``close_deadline``,
    #: which bounds :meth:`RealtimeEars.close` itself. This one is a term of the
    #: liveness bound; that one is a term of shutdown.
    close_handshake_timeout: float = DEFAULT_CLOSE_HANDSHAKE_TIMEOUT
    max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES
    poll_interval: float = DEFAULT_POLL_INTERVAL

    @property
    def liveness_bound(self) -> float:
        """Worst-case seconds between a peer vanishing and this client knowing.

        All three terms, because all three elapse in sequence: one whole
        ``ping_interval`` (the peer can die the instant after a pong), one whole
        ``ping_timeout``, and then ``close_handshake_timeout`` waiting for a
        close frame that never comes. This is the number a host should read;
        any single term of it understates the deafness.
        """
        return (
            float(self.ping_interval)
            + float(self.ping_timeout)
            + float(self.close_handshake_timeout)
        )

    @classmethod
    def from_env(
        cls, env: Optional[Mapping[str, str]] = None, **overrides: Any
    ) -> "RealtimeConfig":
        """Resolve from an environment mapping; *overrides* win over both.

        ``EMBODIMENT_GATEWAY_KEY`` is this package's own name and takes
        precedence; ``CULTURE_VLLM_API_KEY`` is the mesh-wide fallback so a box
        already configured for its lobes gateway needs no second variable. No
        key resolves to the empty string, never to an error — whether the
        gateway's gate is armed is the gateway's business, and a refusal is a
        named degradation, not a missing-configuration exception.
        """
        if env is None:  # pragma: no cover - exercised by the live dial only
            import os

            env = os.environ
        base = cls(
            gateway_url=env.get(_ENV_URL) or _DEFAULT_GATEWAY,
            api_key=env.get(_ENV_KEY) or env.get(_ENV_KEY_FALLBACK) or "",
        )
        return replace(base, **overrides) if overrides else base


# ── sanitising ───────────────────────────────────────────────────────────────


def _safe(text: object, key: str = "") -> str:
    """The ONE sanitiser every reason string goes through.

    Redacts the bearer key, collapses newlines (a record is one line), strips
    control characters that would let a hostile body forge log structure, and
    truncates. Never returns ``None`` and never raises.
    """
    try:
        value = text if isinstance(text, str) else str(text)
    except Exception:  # pragma: no cover - a __str__ that raises
        return "unprintable"
    if key:
        value = value.replace(key, REDACTED)
    value = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in value)
    value = " ".join(value.split())
    if len(value) > _MAX_REASON_LEN:
        value = value[:_MAX_REASON_LEN] + "..."
    return value


def _peer_origin(body: Mapping[str, Any]) -> str:
    """The referral a gateway attaches to a declared-off lane, or ``""``."""
    error = body.get("error")
    if isinstance(error, Mapping):
        hosted = error.get("hosted_by")
        if isinstance(hosted, str):
            return hosted
    hosted = body.get("hosted_by")
    return hosted if isinstance(hosted, str) else ""


def _error_code(body: Mapping[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, Mapping):
        code = error.get("code") or error.get("type")
        if isinstance(code, str):
            return code
    return ""


def _error_message(body: Mapping[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return ""


def _as_json_object(raw: object) -> dict[str, Any]:
    """Best-effort JSON object from a response body. ``{}`` on anything else."""
    try:
        if isinstance(raw, (bytes, bytearray, memoryview)):
            raw = bytes(raw).decode("utf-8", "replace")
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except (ValueError, RecursionError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ── the ears ─────────────────────────────────────────────────────────────────


class RealtimeEars:
    """One ears-only realtime session.

    Lifecycle: :meth:`connect`, then :meth:`send_audio` from wherever capture
    runs and :meth:`events` from wherever the loop runs, then :meth:`close`.
    Every one of them is safe to call at any point in that sequence, including
    out of order — a daemon's shutdown path must not depend on how far the
    startup path got.
    """

    def __init__(
        self,
        config: Optional[RealtimeConfig] = None,
        *,
        on_degrade: Any = None,
    ) -> None:
        self.config = config or RealtimeConfig()
        self._on_degrade = on_degrade
        self._degradations: list[RealtimeDegradation] = []
        self._recorded_once: set[str] = set()
        self._lock = threading.Lock()
        self._queue: deque[bytes] = deque()
        self._queued_bytes = 0
        self._dropped_frames = 0
        self._dropped_bytes = 0
        self._ws: Any = None
        self._writer: Optional[asyncio.Task[None]] = None
        self._connected = False
        self._lost = False
        self._closing = False
        self._closed = False
        self._session_id = ""
        self._last_event_at: Optional[float] = None

    # -- state a host reads ------------------------------------------------

    @property
    def last_event_age(self) -> Optional[float]:
        """Seconds since the last event arrived, or ``None`` before the first.

        **Not a health signal on its own.** Keep-alive pongs are handled inside
        the transport and never surface here, so a large age means only that
        nobody has spoken — a silent room is silent. Liveness is
        :attr:`RealtimeConfig.liveness_bound` plus the drop record.
        """
        if self._last_event_at is None:
            return None
        return max(0.0, time.monotonic() - self._last_event_at)

    @property
    def latency(self) -> Optional[float]:
        """The transport's last measured ping/pong round trip, in seconds.

        ``None`` when no session is up or no round trip has completed yet.
        Read from the transport rather than timed here: the keep-alive is the
        transport's to run, and a second clock beside it would drift.
        """
        value = getattr(self._ws, "latency", None)
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def degraded(self) -> bool:
        return bool(self._degradations)

    @property
    def degradations(self) -> tuple[RealtimeDegradation, ...]:
        return tuple(self._degradations)

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def dropped_bytes(self) -> int:
        return self._dropped_bytes

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    @property
    def session_id(self) -> str:
        return self._session_id

    def status(self) -> dict[str, Any]:
        """A JSON-safe snapshot. Carries no key, no transcript, no frame.

        The three liveness fields exist so a host can *see* deafness rather
        than infer it from ``connected``: ``liveness_bound_s`` is the worst
        case this session was configured to tolerate, ``last_event_age_s`` is
        how long since anybody spoke (see :attr:`last_event_age` — silence is
        not sickness), and ``latency_s`` is the transport's last round trip.
        """
        with self._lock:
            queued_frames = len(self._queue)
            queued_bytes = self._queued_bytes
        age = self.last_event_age
        latency = self.latency
        return {
            "gateway": self.config.gateway_url,
            "connected": self._connected,
            "degraded": self.degraded,
            "session_id": self._session_id,
            "queued_frames": queued_frames,
            "queued_bytes": queued_bytes,
            "dropped_frames": self._dropped_frames,
            "dropped_bytes": self._dropped_bytes,
            "max_queue_bytes": self.config.max_queue_bytes,
            "ping_interval_s": self.config.ping_interval,
            "ping_timeout_s": self.config.ping_timeout,
            "close_handshake_timeout_s": self.config.close_handshake_timeout,
            "liveness_bound_s": self.config.liveness_bound,
            "last_event_age_s": None if age is None else round(age, 3),
            "latency_s": None if latency is None else round(latency, 4),
            "degradations": [d.to_dict() for d in self._degradations],
        }

    def __repr__(self) -> str:
        return (
            f"RealtimeEars(gateway={self.config.gateway_url!r}, "
            f"connected={self._connected}, degradations={len(self._degradations)})"
        )

    # -- recording ---------------------------------------------------------

    def _record(self, code: str, reason: str = "", *, once: bool = False) -> None:
        """Append one degradation. ``once`` collapses a repeating fault.

        The counters behind a collapsed fault keep counting — only the record
        is suppressed, so a host still sees the magnitude in :meth:`status`.
        """
        if once:
            if code in self._recorded_once:
                return
            self._recorded_once.add(code)
        record = RealtimeDegradation(code=code, reason=_safe(reason, self.config.api_key))
        self._degradations.append(record)
        if self._on_degrade is not None:
            try:
                self._on_degrade(record)
            except Exception as exc:  # noqa: BLE001 - the record is already stored
                # The degradation IS recorded above, unconditionally and first.
                # Only the host's optional notification hook failed, and a hook
                # that raises must not take down the ear it was watching.
                self._degradations.append(
                    RealtimeDegradation(
                        code="realtime-degrade-hook-failed",
                        reason=_safe(type(exc).__name__, self.config.api_key),
                    )
                )

    # -- discovery ---------------------------------------------------------

    def _fetch_capabilities(self) -> str:
        """Blocking, keyless ``GET /capabilities``. Runs off the event loop."""
        origin = self.config.gateway_url.rstrip("/")
        url = f"{origin}{wire.CAPABILITIES_PATH}"
        if not url.startswith(("http://", "https://")):
            raise ValueError("gateway_url must be http(s)")
        request = urllib.request.Request(url, method="GET")  # nosec B310 - scheme checked above
        with urllib.request.urlopen(  # nosec B310 - scheme checked above
            request, timeout=self.config.discovery_deadline
        ) as response:
            return response.read().decode("utf-8", "replace")

    async def _discover(self) -> bool:
        """True when this gateway advertises a usable session lane."""
        try:
            body = await asyncio.wait_for(
                asyncio.to_thread(self._fetch_capabilities),
                timeout=self.config.discovery_deadline,
            )
        except asyncio.TimeoutError:
            self._record(DISCOVERY_FAILED, "capabilities did not answer inside its deadline")
            return False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._record(DISCOVERY_FAILED, f"{type(exc).__name__}: {exc}")
            return False

        try:
            parsed = json.loads(body)
        except (ValueError, RecursionError):
            self._record(DISCOVERY_MALFORMED, "capabilities body did not parse as JSON")
            return False
        if not isinstance(parsed, dict):
            self._record(DISCOVERY_MALFORMED, "capabilities body was not an object")
            return False

        advert = parsed.get(wire.STT_ROLE)
        if not isinstance(advert, Mapping):
            self._record(ADVERT_ABSENT, f"no {wire.STT_ROLE!r} role in the capabilities advert")
            return False
        if not advert.get("feasible"):
            peer = _peer_origin(advert)
            self._record(
                ROLE_INFEASIBLE,
                f"{wire.STT_ROLE} is declared off on this gateway"
                + (f"; hosted_by {peer}" if peer else "; no peer referral"),
            )
            return False
        # A LIST, specifically. A string containing the token, or a mapping
        # keyed by it, would both satisfy ``in`` and neither is an advert — an
        # accidental match here dials a gateway that never claimed the lane.
        raw_responsibilities = advert.get("responsibilities")
        responsibilities = (
            tuple(raw_responsibilities) if isinstance(raw_responsibilities, (list, tuple)) else ()
        )
        if wire.REALTIME_RESPONSIBILITY not in responsibilities:
            self._record(
                ADVERT_ABSENT,
                f"{wire.STT_ROLE} does not advertise {wire.REALTIME_RESPONSIBILITY}",
            )
            return False
        return True

    # -- the handshake -----------------------------------------------------

    def _classify_refusal(self, status: int, body: Mapping[str, Any]) -> tuple[str, str]:
        message = _error_message(body)
        if status == 401 or status == 403:
            return UNAUTHORIZED, f"gateway refused the bearer key (HTTP {status})"
        if status == 426:
            return UPGRADE_REQUIRED, f"HTTP {status}: the route wanted a websocket upgrade"
        if status == 404 and _error_code(body) == "role_infeasible":
            peer = _peer_origin(body)
            return ROLE_INFEASIBLE, "HTTP 404 role_infeasible" + (
                f"; hosted_by {peer}" if peer else "; no peer referral"
            )
        if status == 404:
            return AUDIO_NOT_CONFIGURED, f"HTTP 404: {message or 'no audio surface here'}"
        return HANDSHAKE_FAILED, f"HTTP {status}: {message or 'handshake refused'}"

    async def connect(self) -> bool:
        """Discover, dial, and start the writer. ``False`` on any fault.

        Never raises. Exactly one degradation is recorded for whichever fault
        stopped it, and the caller keeps running either way.
        """
        if self._connected:
            return True
        self._closed = False
        self._closing = False
        if not await self._discover():
            return False

        try:
            from websockets.asyncio.client import connect as ws_connect
            from websockets.exceptions import InvalidStatus
        except ImportError as exc:  # pragma: no cover - an approved dependency
            self._record(TRANSPORT_MISSING, f"{type(exc).__name__}: {exc}")
            return False

        origin = self.config.realtime_url or self.config.gateway_url
        url = wire.realtime_url(
            origin,
            input_sample_rate=self.config.input_sample_rate,
            aec_mode=self.config.aec_mode,
            language=self.config.language,
        )
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        deadline = self.config.handshake_deadline
        try:
            self._ws = await asyncio.wait_for(
                ws_connect(
                    url,
                    additional_headers=headers,
                    open_timeout=deadline,
                    # The liveness clock, stated rather than inherited. Left to
                    # the transport's defaults this is 20 + 20 + 10 and a peer
                    # that vanishes without a FIN leaves the ear deaf for up to
                    # 50 s while `connected` stays True.
                    ping_interval=self.config.ping_interval,
                    ping_timeout=self.config.ping_timeout,
                    close_timeout=self.config.close_handshake_timeout,
                ),
                timeout=deadline,
            )
        except InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            body = _as_json_object(getattr(getattr(exc, "response", None), "body", b""))
            code, reason = self._classify_refusal(int(status or 0), body)
            self._record(code, reason)
            return False
        except (asyncio.TimeoutError, TimeoutError):
            self._record(HANDSHAKE_TIMEOUT, f"no handshake inside {deadline}s")
            return False
        except (OSError, ValueError) as exc:
            self._record(HANDSHAKE_FAILED, f"{type(exc).__name__}: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001 - a transport this client does not know
            # Recorded, never re-raised: an unfamiliar transport failure is
            # still a failure the host must see, and still not a traceback the
            # daemon should wear.
            self._record(HANDSHAKE_FAILED, f"{type(exc).__name__}: {exc}")
            return False

        self._connected = True
        self._lost = False
        self._last_event_at = time.monotonic()
        self._writer = asyncio.get_running_loop().create_task(self._drain_forever())
        return True

    # -- audio out ---------------------------------------------------------

    def send_audio(self, pcm: bytes) -> bool:
        """Queue one captured PCM16 frame. Non-blocking; never raises.

        ``False`` means the frame did not enter the queue: it was not bytes,
        the session is gone, or the queue was at its bound (in which case the
        oldest frames were dropped to make room, or — for a frame larger than
        the whole bound — this frame itself was refused).

        Before :meth:`connect` succeeds, valid audio is *buffered*, bounded, so
        a capture thread that starts first loses nothing.
        """
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            self._record(AUDIO_NOT_BYTES, f"send_audio got {type(pcm).__name__}", once=True)
            return False
        frame = bytes(pcm)
        if not frame:
            return True
        if self._lost or self._closed:
            self._record(NOT_CONNECTED, "audio arrived after the session ended", once=True)
            return False

        bound = max(0, int(self.config.max_queue_bytes))
        if len(frame) > bound:
            with self._lock:
                self._dropped_frames += 1
                self._dropped_bytes += len(frame)
            self._record(
                AUDIO_DROPPED,
                f"a {len(frame)}-byte frame exceeds the whole {bound}-byte bound",
                once=True,
            )
            return False

        dropped = 0
        with self._lock:
            self._queue.append(frame)
            self._queued_bytes += len(frame)
            while self._queued_bytes > bound and self._queue:
                oldest = self._queue.popleft()
                self._queued_bytes -= len(oldest)
                dropped += 1
                # Counted UNDER the lock. ``x += 1`` on an attribute is a
                # read-modify-write, so counting outside it silently loses
                # increments once more than one capture thread is feeding —
                # and a drop counter that under-reports is the silent
                # degradation this whole bound exists to make visible.
                self._dropped_frames += 1
                self._dropped_bytes += len(oldest)
        if dropped:
            self._record(
                AUDIO_DROPPED,
                f"send queue at its {bound}-byte bound; oldest frames discarded",
                once=True,
            )
            return False
        return True

    async def _drain_forever(self) -> None:
        """Send queued frames until the session ends. Owns no other state."""
        poll = max(0.001, float(self.config.poll_interval))
        while not self._closing:
            frame = None
            with self._lock:
                if self._queue:
                    frame = self._queue.popleft()
                    self._queued_bytes -= len(frame)
            if frame is None:
                await asyncio.sleep(poll)
                continue
            try:
                await self._ws.send(wire.encode_audio_append(frame))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport fault ends the session
                # Recorded by _mark_lost, which is idempotent — the reader may
                # have noticed the same drop first.
                self._mark_lost(type(exc).__name__)
                return

    # -- events in ---------------------------------------------------------

    def _mark_lost(self, detail: str) -> None:
        """One record for one lost session, whoever noticed it first."""
        self._connected = False
        if self._closing or self._closed:
            return
        self._lost = True
        self._record(SESSION_DROPPED, f"session ended without a local close ({detail})", once=True)

    async def events(self) -> AsyncIterator[wire.ServerEvent]:
        """Yield decoded server events until the session ends.

        The stream *ends*; it never raises. A malformed frame is recorded and
        yielded as :class:`~embodiment.realtime.wire.MalformedEvent` so a host
        sees the gap rather than a silent skip, and the session continues. A
        server error event is recorded and yielded too: the server said it out
        loud, and swallowing it here would make this client the quiet one.
        """
        if not self._connected or self._ws is None:
            self._record(NOT_CONNECTED, "events requested with no live session", once=True)
            return
        try:
            async for raw in self._ws:
                self._last_event_at = time.monotonic()
                event = wire.decode_server_event(raw)
                if isinstance(event, wire.MalformedEvent):
                    self._record(FRAME_MALFORMED, event.reason, once=True)
                elif isinstance(event, wire.ServerError):
                    self._record(SERVER_ERROR, f"server error {event.code!r}")
                elif isinstance(event, wire.SessionCreated) and event.session_id:
                    self._session_id = event.session_id
                yield event
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport fault ends the stream
            # Recorded by _mark_lost below; the close reason is deliberately
            # NOT read off the exception — a server close reason is text this
            # client did not write and could carry anything, including speech.
            self._mark_lost(type(exc).__name__)
            return
        self._mark_lost("clean close by the peer")

    # -- shutdown ----------------------------------------------------------

    async def close(self, deadline: Optional[float] = None) -> CloseReport:
        """Shut the session down inside *deadline*. Idempotent; never raises.

        Reports what it left unfinished: frames still queued, bytes with them,
        and whether the deadline fired. Queued audio is **discarded**, not
        flushed — a shutdown that waits to push five seconds of stale audio
        into a socket is a shutdown that misses its deadline.
        """
        budget = float(deadline if deadline is not None else self.config.close_deadline)
        self._closing = True
        with self._lock:
            queued_frames = len(self._queue)
            queued_bytes = self._queued_bytes
            self._queue.clear()
            self._queued_bytes = 0

        if self._closed:
            return CloseReport(graceful=True, dropped_frames=self._dropped_frames)

        exceeded = False
        writer = self._writer
        if writer is not None:
            writer.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(_swallow(writer)), timeout=budget)
            except (asyncio.TimeoutError, TimeoutError):
                exceeded = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - teardown answers to nobody
                self._record(
                    CLOSE_INCOMPLETE,
                    f"writer teardown raised {type(exc).__name__}",
                    once=True,
                )
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=budget)
            except (asyncio.TimeoutError, TimeoutError):
                exceeded = True
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError) as exc:
                # An already-dead peer is a closed one, so this is not a second
                # SESSION_DROPPED — but it is still a close that did not run to
                # completion, and C3 says a host hears about it.
                self._record(
                    CLOSE_INCOMPLETE,
                    f"socket close raised {type(exc).__name__}",
                    once=True,
                )

        self._connected = False
        self._closed = True
        self._writer = None
        self._ws = None
        if exceeded:
            self._record(CLOSE_INCOMPLETE, f"close did not finish inside {budget}s")
        return CloseReport(
            graceful=not exceeded,
            queued_frames=queued_frames,
            queued_bytes=queued_bytes,
            dropped_frames=self._dropped_frames,
            deadline_exceeded=exceeded,
        )


async def _swallow(task: "asyncio.Task[Any]") -> None:
    """Await a cancelled writer to completion so it is reaped, not left pending.

    Only :class:`asyncio.CancelledError` is absorbed, and only because it is
    the cancellation *this* module just issued — a caller's cancellation is a
    different event and must propagate. Any other fault reaches
    :meth:`RealtimeEars.close`, which records it: ``_drain_forever`` already
    catches everything it can name, so anything arriving here is a surprise and
    deserves to be written down rather than dropped.
    """
    try:
        await task
    except asyncio.CancelledError:
        return
