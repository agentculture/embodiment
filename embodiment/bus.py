"""embodiment.bus — the internal event substrate: MQTT-through-events-cli,
projected in-process to bounded subscriber queues.

Plan task ``t13`` (spec target ``h16``). The operator's decision this module
implements: **MQTT (through events-cli) is the internal events substrate; the
dashboard's server-sent event stream (task ``t16``) is a PROJECTION of it;
browsers never touch MQTT.** :class:`Bus` therefore does two things on every
:meth:`Bus.publish` call, unconditionally: it offers the event to a broker
*when one answers*, and it hands the SAME event to every in-process
:class:`Subscription` *always*. A host with no broker reachable at all still
gets a fully working dashboard — the broker is a fan-out convenience for other
processes on the box, never a requirement for this process's own consumers.

One versioned schema
---------------------
Every event this module accepts and delivers has exactly the shape
``{v, kind, ts, seq, source, data}`` (:class:`Event`), plus an optional
``gap`` field a slow subscriber's own delivery adds (see below — never sent by
a publisher). ``v`` is :data:`SCHEMA_VERSION`; ``seq`` is monotonic per
:class:`Bus` instance, so a consumer can detect a missed event by a gap in the
sequence even without reading ``gap``. ``kind`` is one of :data:`EVENT_KINDS`.
Every event is validated against the required-field contract for its kind
(:data:`_REQUIRED_DATA_FIELDS`, mirrored — same field names, same kinds — in
the committed JSON fixtures under ``tests/fixtures/events/``, which are this
module's CONTRACT with the dashboard web app's own tests (task ``t17``): both
sides validate against the identical files, so the two cannot drift apart
silently. An event that fails validation is never sent to the broker or to any
subscriber; it is refused and recorded as a :class:`BusDegradation` naming the
kind and the failing field (never raised — C3).

Privacy boundary (read this before wiring a new event source)
---------------------------------------------------------------
``transcript`` and ``reply`` are the ONLY kinds allowed to carry speech — the
user's words and the reply text. Every other kind (``state``, ``mic``,
``turn``, ``degradation``, ``features``, ``clients``, ``heartbeat``) carries
only status, counts, and degradation CODES, never text a person said or a
model produced. This module's job is to make that boundary explicit and
machine-checkable, not to enforce WHO may read a ``transcript``/``reply``
event — that enforcement (authenticated subscribers only) belongs to the SSE
projection layer, task ``t16``. What this module DOES provide toward it is
:meth:`Bus.subscribe`'s ``kinds=`` filter: a subscription that never asked for
``transcript`` never receives one, so t16 can hand an unauthenticated
projection a kind-filtered subscription and have the guarantee hold
structurally rather than by remembering to check ``kind`` on every event.

Degradation vocabulary (C3 — never raise, always record)
------------------------------------------------------------
- :data:`DEGRADED_BROKER_UNAVAILABLE` — events-cli/paho-mqtt not importable,
  the broker refused a connection, or a publish failed. Recorded exactly ONCE
  per :class:`Bus` instance (see :meth:`Bus._degrade_broker`'s docstring) —
  never once per event — and in-process delivery is never affected by it.
- :data:`DEGRADED_SCHEMA_INVALID` — an event failed its kind's required-field
  contract, or named an unknown ``kind``.
- :data:`DEGRADED_SECRET_REDACTED` — an event's serialised form contained one
  of the literal values passed to ``Bus(redact=...)``.
- :data:`DEGRADED_OVERSIZE` — an event's serialised form exceeded
  :data:`DEFAULT_MAX_EVENT_BYTES` (or the constructor override).

Reused shapes, not reinvented (lesson 8 — one code path)
------------------------------------------------------------
:func:`fold_degradation` folds any of this package's four existing
degradation shapes — :class:`embodiment.events.EventDegradation`,
:class:`embodiment.turn.TurnDegradation`, :class:`embodiment.continuity.Degradation`
and :class:`embodiment.daemon.state.DegradationRecord` (all four differ in
which attribute carries the human-readable text: ``reason`` on the first
three, ``detail`` on the last) — into the ONE ``{source, code, reason}`` shape
:data:`_REQUIRED_DATA_FIELDS["degradation"]` requires. Every reason string
passed through this module — a folded degradation's, or one built directly for
a ``kind="degradation"`` publish — goes through the SAME sanitiser
(:func:`_sanitize_reason`), which strips Unicode format/bidi characters
(category ``Cf``: RTL/LTR override and embedding marks, zero-width joiners,
etc.) so a planted control character cannot make a degradation record render
as something other than what it says.

Connection approach, reused rather than reinvented
--------------------------------------------------------
This module talks to the broker the same way :mod:`embodiment.events` does —
a lazy import of ``events_cli.core`` (pure-stdlib envelope/topic contract) and
``events_cli.client.EventClient`` (the paho-mqtt transport), degrade-once on
any failure, never a raise. It keeps its OWN pair of lazy loaders
(:func:`_load_envelope_core`, :func:`_load_event_client_class`) rather than
importing :mod:`embodiment.events`'s private ones: the two modules mint
different degradation vocabularies and have different disable semantics (an
:class:`~embodiment.events.EventEmitter` disables ALL further activity on any
failure; a :class:`Bus` disables only its MQTT fan-out and keeps delivering
in-process), so sharing the private functions would couple two independently
evolving degrade paths through an implementation detail neither module's
public contract advertises. The *approach* — lazy import, ``EventClient``,
degrade-once, never raise — is what is reused; the code is not, deliberately.

No thread started here
-----------------------
Per the task brief, this module does not start a background thread. The
heartbeat is driven by :meth:`Bus.tick`, which a host's own daemon loop (or a
test) calls with a wall-clock ``now`` it read itself — the same
"no clock inside the pure parts" discipline
:mod:`embodiment.audio.features` and :mod:`embodiment.presence` already use.
:meth:`Bus.close` is still provided (lesson 6 — shutdown is a feature) because
this module DOES own one resource across calls: the lazily-built MQTT
transport client. ``close`` is idempotent, never raises, and returns a
:class:`BusCloseReport` naming what it did.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

__all__ = [
    "SCHEMA_VERSION",
    "EVENT_KINDS",
    "SPEECH_KINDS",
    "HEARTBEAT_INTERVAL_S",
    "DEFAULT_MAX_EVENT_BYTES",
    "DEFAULT_SUBSCRIBER_QUEUE_SIZE",
    "DEFAULT_SOURCE",
    "DEGRADED_BROKER_UNAVAILABLE",
    "DEGRADED_SCHEMA_INVALID",
    "DEGRADED_SECRET_REDACTED",
    "DEGRADED_OVERSIZE",
    "BusDegradation",
    "BusCloseReport",
    "Event",
    "Subscription",
    "Bus",
    "fold_degradation",
]

#: The schema version stamped on every :class:`Event`. Bump this, never the
#: field names, when the envelope shape itself changes.
SCHEMA_VERSION = 1

#: The nine event kinds this bus knows how to validate and route. A publish
#: naming any other kind is refused and recorded as
#: :data:`DEGRADED_SCHEMA_INVALID` — never silently accepted with an
#: unvalidated shape.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        "state",
        "mic",
        "turn",
        "transcript",
        "reply",
        "degradation",
        "features",
        "clients",
        "heartbeat",
    }
)

#: The ONLY kinds allowed to carry speech (the user's words, or Gwen's reply).
#: See the module docstring's privacy-boundary section.
SPEECH_KINDS: frozenset[str] = frozenset({"transcript", "reply"})

#: Fixed heartbeat interval, in seconds. A **judgement call**, not a measured
#: value: 15s is short enough that a dashboard viewer sees the daemon is alive
#: within one refresh cycle of a typical SSE client's own retry/timeout
#: defaults, and long enough that ten attached dashboards cost under one
#: event/second of heartbeat traffic combined. Revisit with a measurement if a
#: real dashboard's staleness tolerance ever needs to be tighter.
HEARTBEAT_INTERVAL_S: float = 15.0

#: Maximum serialised size (UTF-8 bytes) of one event's JSON form. A
#: **judgement call**: every kind's payload measured in this module's own
#: tests is well under 1 KB (a ``features`` frame's 44-character base64 ``env``
#: is the largest single field), so 8 KiB leaves generous headroom for a
#: longer-than-typical ``transcript``/``reply`` utterance while still bounding
#: what one event can cost a slow consumer or a real-time broker to carry.
DEFAULT_MAX_EVENT_BYTES = 8192

#: Default bound on one subscriber's own queue (number of events, not bytes).
#: A **judgement call**: at the ``features`` publish rate this package's audio
#: pipeline actually produces (~30/s per direction, so up to ~60/s combined),
#: 256 slots is a few seconds of buffering before the drop policy engages —
#: enough to absorb a GC pause or a slow SSE flush without losing a
#: ``transcript``/``degradation`` event, not so large that a genuinely stuck
#: subscriber can accumulate an unbounded amount of memory.
DEFAULT_SUBSCRIBER_QUEUE_SIZE = 256

#: The CloudEvents ``source`` this bus mints when the constructor is not given
#: one explicitly. Mirrors :data:`embodiment.events.DEFAULT_SOURCE`'s shape
#: without importing it, for the same "own degrade path" reason discussed in
#: the module docstring.
DEFAULT_SOURCE = "app://embodiment"

# ── the degradation vocabulary (C3) ─────────────────────────────────────────

#: events-cli/paho-mqtt unavailable, connect failed, or a publish failed.
#: Recorded exactly once per :class:`Bus` instance; see :meth:`Bus._degrade_broker`.
DEGRADED_BROKER_UNAVAILABLE = "bus-broker-unavailable"
#: An unknown ``kind``, or a known kind missing/mistyping a required field.
DEGRADED_SCHEMA_INVALID = "bus-schema-invalid"
#: The event's serialised form contained a literal value from ``redact=``.
DEGRADED_SECRET_REDACTED = "bus-secret-redacted"  # nosec B105
#: The event's serialised form exceeded the configured maximum size.
DEGRADED_OVERSIZE = "bus-event-oversize"

#: Characters in Unicode category ``Cf`` ("format") — includes every bidi
#: override/embedding/isolate control (U+200E/F, U+202A-E, U+2066-69) and
#: zero-width joiners/non-joiners. Stripped from every degradation ``reason``
#: by :func:`_sanitize_reason`, the ONE sanitiser every reason string in this
#: module goes through (lesson 8).
_STRIP_CATEGORY = "Cf"

#: Restricts a token field (``source``/``code`` on a folded degradation) to a
#: safe, loggable charset — the same discipline this package applies to ids
#: elsewhere (lesson 5: ids are hashed or restricted to this exact charset).
_SAFE_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_MAX_TOKEN_LEN = 80
_MAX_REASON_LEN = 500

#: Which ``data`` fields each kind REQUIRES to be present (any JSON value,
#: including ``None`` — ``zero_crossing_hz`` is legitimately absent-as-None for
#: a silent block). Mirrored field-for-field in the committed JSON fixtures
#: under ``tests/fixtures/events/`` — the contract this module and the
#: dashboard web app's tests (task t17) both validate against. Extra fields
#: beyond this set are always allowed; this module never rejects a payload for
#: carrying MORE than the contract, only for carrying less.
_REQUIRED_DATA_FIELDS: dict[str, tuple[str, ...]] = {
    "state": ("component", "status"),
    "mic": ("hot", "ear"),
    "turn": ("phase", "step_count"),
    "transcript": ("role", "text"),
    "reply": ("text",),
    "degradation": ("source", "code", "reason"),
    "features": ("direction", "env", "level_db", "noise_floor_db", "zero_crossing_hz"),
    "clients": ("count", "remote"),
    "heartbeat": (),
}

#: Kinds dropped FIRST under subscriber back-pressure (never ``transcript`` or
#: ``degradation``, see the module docstring). Currently just ``features`` —
#: the high-rate kind named in the brief — kept as a set rather than a single
#: constant so a future high-rate kind can join it in one place.
_DROP_FIRST_KINDS: frozenset[str] = frozenset({"features"})


def _now_rfc3339() -> str:
    """UTC timestamp, millisecond precision, ``Z`` suffix. Matches events-cli's own form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sanitize_reason(text: object) -> str:
    """Strip Unicode format/bidi characters and cap length. The ONE sanitiser (lesson 8)."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) != _STRIP_CATEGORY)
    return cleaned[:_MAX_REASON_LEN]


def _safe_token(value: object) -> str:
    """Restrict a token (a degradation ``source``/``code``) to a safe charset."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    cleaned = "".join(ch for ch in value if ch in _SAFE_TOKEN_CHARS)
    return cleaned[:_MAX_TOKEN_LEN] or "unknown"


def fold_degradation(record: Any, *, source: str) -> dict[str, str]:
    """Fold any of this package's degradation shapes into ``{source, code, reason}``.

    Handles :class:`embodiment.events.EventDegradation`,
    :class:`embodiment.turn.TurnDegradation`,
    :class:`embodiment.continuity.Degradation` and
    :class:`embodiment.daemon.state.DegradationRecord` — the four shapes named
    in the module docstring — via plain ``getattr``, so this function works on
    any of them (or a duck-typed stand-in in a test) without importing any of
    those modules. ``source`` names WHICH subsystem produced *record* (e.g.
    ``"turn"``, ``"continuity"``, ``"daemon"``, ``"events"``) — it is not read
    off *record* itself, because only one of the four shapes
    (:class:`~embodiment.continuity.Degradation`, whose own ``subsystem``
    field means something narrower — ``"eidetic"``/``"coherence"``) carries
    anything like it, and the other three carry nothing a caller did not
    already know when it caught the exception.

    Never raises: an object missing both ``reason`` and ``detail`` folds to an
    empty reason rather than raising ``AttributeError``.
    """
    code = getattr(record, "code", None)
    reason = getattr(record, "reason", None)
    if reason is None:
        reason = getattr(record, "detail", None)
    return {
        "source": _safe_token(source),
        "code": _safe_token(code),
        "reason": _sanitize_reason(reason),
    }


@dataclass(frozen=True)
class BusDegradation:
    """One recorded, host-visible transition on a :class:`Bus` (constraint C3)."""

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class BusCloseReport:
    """What :meth:`Bus.close` actually did. Never raises; always returned."""

    closed_broker_client: bool
    subscribers_closed: int
    elapsed_s: float


@dataclass(frozen=True)
class Event:
    """One envelope: ``{v, kind, ts, seq, source, data}``, plus an optional ``gap``.

    ``gap`` is never set by :meth:`Bus.publish` — it is added by
    :meth:`Subscription.get` ONLY, when THAT subscriber's own queue dropped
    one or more events since its last delivery (see the module docstring's
    slow-subscriber section). Two subscribers of the same published event can
    therefore observe the identical event with different ``gap`` values (or
    none at all) — ``gap`` describes a subscriber's delivery history, not a
    property of the occurrence itself.
    """

    v: int
    kind: str
    ts: str
    seq: int
    source: str
    data: dict[str, Any]
    gap: int = 0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "v": self.v,
            "kind": self.kind,
            "ts": self.ts,
            "seq": self.seq,
            "source": self.source,
            "data": self.data,
        }
        if self.gap:
            out["gap"] = self.gap
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _validate_data(kind: str, data: Any) -> Optional[str]:
    """Return the name of the first missing/invalid required field, or ``None``.

    Presence-only for most fields (any JSON value, including ``None``, counts
    as present) plus a few targeted type checks for fields whose type is part
    of the contract a consumer will branch on (``role``/``hot`` etc.) — this is
    intentionally a light, hand-rolled check, not a general JSON-Schema
    validator: the committed fixtures are the authoritative contract text for
    both this module and the dashboard's own tests, and this function only
    needs to reject what would break that contract.
    """
    if not isinstance(data, dict):
        return "data"
    required = _REQUIRED_DATA_FIELDS.get(kind, ())
    for field_name in required:
        if field_name not in data:
            return field_name
    if kind == "transcript":
        if data.get("role") not in ("user", "assistant"):
            return "role"
        if not isinstance(data.get("text"), str):
            return "text"
    if kind == "reply" and not isinstance(data.get("text"), str):
        return "text"
    if kind == "mic" and not isinstance(data.get("hot"), bool):
        return "hot"
    if kind == "degradation":
        if not isinstance(data.get("code"), str) or not data.get("code"):
            return "code"
        if not isinstance(data.get("reason"), str):
            return "reason"
        if not isinstance(data.get("source"), str) or not data.get("source"):
            return "source"
    if kind == "features" and data.get("direction") not in ("in", "out"):
        return "direction"
    if kind == "clients":
        if not isinstance(data.get("count"), int) or isinstance(data.get("count"), bool):
            return "count"
    return None


class Subscription:
    """A bounded, thread-safe per-consumer queue an SSE handler (task t16) drains.

    Never grows unbounded: once ``maxsize`` events are queued, further
    delivery drops the OLDEST event still eligible to be dropped rather than
    blocking the publisher or growing memory. ``features`` events are dropped
    FIRST — see the module docstring — so a slow dashboard loses live
    waveform frames long before it loses a spoken turn or a degradation
    record. Every drop is counted, and the count rides onto the ``gap`` field
    of the NEXT event this subscription actually delivers, so a consumer sees
    "missed N events" rather than a silent gap in ``seq``.
    """

    __slots__ = ("_kinds", "_maxsize", "_cv", "_queue", "_drops", "_closed")

    def __init__(self, *, kinds: Optional[Iterable[str]] = None, maxsize: int) -> None:
        self._kinds = frozenset(kinds) if kinds is not None else None
        self._maxsize = max(1, int(maxsize))
        self._cv = threading.Condition()
        self._queue: deque[Event] = deque()
        self._drops = 0
        self._closed = False

    @property
    def kinds(self) -> Optional[frozenset[str]]:
        return self._kinds

    def qsize(self) -> int:
        with self._cv:
            return len(self._queue)

    @property
    def drops(self) -> int:
        with self._cv:
            return self._drops

    def _offer(self, event: Event) -> None:
        """Called by :class:`Bus` for every published event. Never raises."""
        if self._kinds is not None and event.kind not in self._kinds:
            return
        with self._cv:
            if self._closed:
                return
            if len(self._queue) >= self._maxsize:
                if not self._make_room_locked(event.kind):
                    return
            self._queue.append(event)
            self._cv.notify()

    def _make_room_locked(self, incoming_kind: str) -> bool:
        """Evict one queued event to make room; return False to drop *incoming* instead.

        Policy (see the module docstring): a ``features`` event arriving while
        full is dropped OUTRIGHT (never displaces anything). Anything else
        arriving while full first tries to evict the OLDEST queued
        ``features`` event (freeing room without ever touching a
        ``transcript``/``degradation``/etc.); only when no ``features`` event
        is queued at all does it fall back to evicting the oldest event of any
        kind, so the queue still cannot grow unbounded.
        """
        if incoming_kind in _DROP_FIRST_KINDS:
            self._drops += 1
            return False
        for index, queued in enumerate(self._queue):
            if queued.kind in _DROP_FIRST_KINDS:
                del self._queue[index]
                self._drops += 1
                return True
        self._queue.popleft()
        self._drops += 1
        return True

    def get(self, timeout: Optional[float] = None) -> Optional[Event]:
        """Block up to *timeout* seconds (``None`` = forever) for the next event.

        Returns ``None`` on timeout or once :meth:`close` has been called and
        the queue has drained. A returned event carries ``gap`` > 0 when one
        or more events were dropped from this subscription since its last
        delivery.
        """
        with self._cv:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not self._queue and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._cv.wait(timeout=remaining)
            if not self._queue:
                return None
            event = self._queue.popleft()
            gap, self._drops = self._drops, 0
            if gap:
                event = replace(event, gap=gap)
            return event

    def drain(self) -> list[Event]:
        """Return every currently-queued event without blocking, resetting ``gap``."""
        out: list[Event] = []
        while True:
            event = self.get(timeout=0.0)
            if event is None:
                return out
            out.append(event)

    def close(self) -> None:
        """Idempotent; wakes any blocked :meth:`get` with ``None``. Never raises."""
        with self._cv:
            self._closed = True
            self._cv.notify_all()


def _load_envelope_core() -> tuple[Any, Any, Any]:
    """Lazy import of events-cli's pure-stdlib envelope/topic core. See module docstring."""
    from events_cli.core import Envelope, now_rfc3339, type_to_topic

    return Envelope, type_to_topic, now_rfc3339


def _load_event_client_class() -> Any:
    """Lazy import of the real paho-mqtt-backed transport client."""
    from events_cli.client import EventClient

    return EventClient


class Bus:
    """The internal event substrate. MQTT fan-out (when a broker answers) plus
    ALWAYS-on in-process delivery to every live :class:`Subscription`.

    Args:
        source: the CloudEvents ``source`` minted on every MQTT envelope, and
            the ``source`` on every :class:`Event`. Defaults to
            :data:`DEFAULT_SOURCE`.
        redact: literal secret values (the gateway key, the install secret,
            …). An event whose serialised JSON contains any of these is
            refused and recorded as :data:`DEGRADED_SECRET_REDACTED` — never
            sent to the broker or to any subscriber.
        max_event_bytes: see :data:`DEFAULT_MAX_EVENT_BYTES`.
        subscriber_queue_size: the default ``maxsize`` a new
            :meth:`subscribe` call uses when it does not override one.
        host, port: forwarded to a lazily-constructed real
            ``events_cli.client.EventClient``. Ignored when ``client`` or
            ``client_factory`` is supplied.
        client, client_factory: as :class:`embodiment.events.EventEmitter` —
            an injection seam for tests and advanced hosts.
        on_degrade: optional callback invoked whenever a
            :class:`BusDegradation` is recorded. Best-effort: an exception
            from it is swallowed, never propagated.
    """

    def __init__(
        self,
        *,
        source: str = DEFAULT_SOURCE,
        redact: Iterable[str] = (),
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        subscriber_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client: Optional[Any] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        on_degrade: Optional[Callable[[BusDegradation], None]] = None,
    ) -> None:
        self._source = source
        self._redact: tuple[str, ...] = tuple(s for s in redact if s)
        self._max_event_bytes = max(1, int(max_event_bytes))
        self._subscriber_queue_size = max(1, int(subscriber_queue_size))
        self._host = host
        self._port = port
        self._client = client
        self._client_factory = client_factory
        self._on_degrade = on_degrade

        self._lock = threading.Lock()
        self._seq = 0
        self._subscribers: list[Subscription] = []
        self.degradations: list[BusDegradation] = []

        self._broker_disabled = False
        self._broker_client: Optional[Any] = None
        self._envelope_cls: Optional[Any] = None
        self._type_to_topic: Optional[Any] = None

        self._last_heartbeat: Optional[float] = None

    # ── public: publish / subscribe ─────────────────────────────────────────

    def publish(self, kind: str, data: Optional[dict[str, Any]] = None) -> Optional[Event]:
        """Validate, then deliver *data* as one ``kind`` event. Never raises (C3).

        Returns the delivered :class:`Event`, or ``None`` when the publish was
        refused — every refusal is recorded on :attr:`degradations` naming the
        reason, never silent.
        """
        payload = dict(data) if isinstance(data, dict) else {}
        if kind == "degradation" and isinstance(payload.get("reason"), str):
            payload["reason"] = _sanitize_reason(payload["reason"])

        if kind not in EVENT_KINDS:
            self._degrade(DEGRADED_SCHEMA_INVALID, f"unknown kind: {_safe_token(kind)}")
            return None

        bad_field = _validate_data(kind, payload)
        if bad_field is not None:
            self._degrade(DEGRADED_SCHEMA_INVALID, f"{kind}: invalid or missing '{bad_field}'")
            return None

        with self._lock:
            self._seq += 1
            seq = self._seq
        event = Event(
            v=SCHEMA_VERSION,
            kind=kind,
            ts=_now_rfc3339(),
            seq=seq,
            source=self._source,
            data=payload,
        )

        try:
            serialised = event.to_json()
        except (TypeError, ValueError) as exc:
            self._degrade(DEGRADED_SCHEMA_INVALID, f"{kind}: not JSON-serialisable: {exc}")
            return None

        for secret in self._redact:
            if secret in serialised:
                self._degrade(DEGRADED_SECRET_REDACTED, f"{kind}: payload matched a redacted value")
                return None

        if len(serialised.encode("utf-8")) > self._max_event_bytes:
            self._degrade(
                DEGRADED_OVERSIZE,
                f"{kind}: {len(serialised.encode('utf-8'))} bytes exceeds "
                f"{self._max_event_bytes}",
            )
            return None

        self._publish_broker(event)
        self._deliver_in_process(event)
        return event

    def subscribe(
        self, kinds: Optional[Iterable[str]] = None, *, maxsize: Optional[int] = None
    ) -> Subscription:
        """Register and return a new :class:`Subscription`.

        ``kinds=None`` (the default) receives every kind, including
        ``transcript``/``reply`` — a caller that must NOT see speech passes an
        explicit ``kinds=`` set that omits them (see the module docstring's
        privacy boundary).
        """
        sub = Subscription(kinds=kinds, maxsize=maxsize or self._subscriber_queue_size)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        """Stop delivering to *subscription* and close it. Idempotent; never raises."""
        with self._lock:
            try:
                self._subscribers.remove(subscription)
            except ValueError:
                pass
        subscription.close()

    # ── public: heartbeat + shutdown ────────────────────────────────────────

    def tick(self, now: float) -> Optional[Event]:
        """Externally-driven heartbeat. Emits at most once per
        :data:`HEARTBEAT_INTERVAL_S`, driven entirely by the *now* the caller
        supplies — this method reads no clock of its own (see the module
        docstring's "no thread started here" section).
        """
        with self._lock:
            due = (
                self._last_heartbeat is None or (now - self._last_heartbeat) >= HEARTBEAT_INTERVAL_S
            )
            if not due:
                return None
            self._last_heartbeat = now
        return self.publish("heartbeat", {})

    def close(self, deadline: float = 2.0) -> BusCloseReport:
        """Idempotent, never raises, returns within *deadline* seconds (lesson 6)."""
        start = time.monotonic()
        with self._lock:
            subs = list(self._subscribers)
            self._subscribers.clear()
        for sub in subs:
            sub.close()

        closed_client = False
        client = self._broker_client
        self._broker_client = None
        if client is not None:
            try:
                client.close()
                closed_client = True
            except Exception:  # nosec B110 # noqa: BLE001 - teardown must never raise
                pass
        elapsed = time.monotonic() - start
        return BusCloseReport(
            closed_broker_client=closed_client,
            subscribers_closed=len(subs),
            elapsed_s=elapsed,
        )

    # ── internals: MQTT fan-out ─────────────────────────────────────────────

    def _publish_broker(self, event: Event) -> None:
        if self._broker_disabled:
            return
        if not self._ensure_broker_core():
            return
        client = self._ensure_broker_client()
        if client is None:
            return
        try:
            envelope = self._envelope_cls.new(
                type=f"embodiment.bus.{event.kind}",
                source=self._source,
                data=event.to_dict(),
                time=event.ts,
                id=f"evt-bus-{self._source}-{event.seq}",
            )
            topic = self._type_to_topic(envelope.type)
            result = client.publish_event(envelope, topic)
            if not bool(getattr(result, "ok", False)):
                reason = getattr(result, "reason", "publish returned ok=False")
                self._degrade_broker(f"publish failed: {reason}")
        except Exception as exc:  # noqa: BLE001 - MQTT fan-out must never abort a publish
            self._degrade_broker(f"{type(exc).__name__}: {exc}")

    def _ensure_broker_core(self) -> bool:
        if self._envelope_cls is not None:
            return True
        try:
            envelope_cls, type_to_topic, _now = _load_envelope_core()
        except Exception as exc:  # noqa: BLE001 - degrade, never raise
            self._degrade_broker(f"{type(exc).__name__}: {exc}")
            return False
        self._envelope_cls = envelope_cls
        self._type_to_topic = type_to_topic
        return True

    def _ensure_broker_client(self) -> Optional[Any]:
        if self._broker_client is not None:
            return self._broker_client
        try:
            if self._client is not None:
                self._broker_client = self._client
            elif self._client_factory is not None:
                self._broker_client = self._client_factory()
            else:
                event_client_cls = _load_event_client_class()
                self._broker_client = event_client_cls(host=self._host, port=self._port)
        except Exception as exc:  # noqa: BLE001 - degrade, never raise
            self._degrade_broker(f"{type(exc).__name__}: {exc}")
            return None
        return self._broker_client

    def _degrade_broker(self, reason: str) -> None:
        """Record :data:`DEGRADED_BROKER_UNAVAILABLE` exactly ONCE per instance.

        After the first call every subsequent publish skips the MQTT attempt
        entirely (``self._broker_disabled``) — the whole point being that a
        broker down for a whole run costs this bus ONE recorded transition,
        never one per published event. In-process delivery is untouched by
        this: :meth:`publish` always calls :meth:`_deliver_in_process`
        regardless of what happened here.
        """
        if self._broker_disabled:
            return
        self._broker_disabled = True
        self._degrade(DEGRADED_BROKER_UNAVAILABLE, reason)

    def _degrade(self, code: str, reason: str) -> None:
        record = BusDegradation(code=code, reason=_sanitize_reason(reason))
        with self._lock:
            self.degradations.append(record)
        if self._on_degrade is not None:
            try:
                self._on_degrade(record)
            except Exception:  # nosec B110 # noqa: BLE001 - a hook must never raise either
                pass

    def _deliver_in_process(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            sub._offer(event)  # noqa: SLF001 - Bus and Subscription are one unit
