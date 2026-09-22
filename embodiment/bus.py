"""embodiment.bus — the internal event substrate: MQTT-through-events-cli,
projected in-process to bounded subscriber queues.

Plan task ``t13`` (spec target ``h16``), round 2 — five defects an independent
probe found in round 1 are fixed here (see the section headed "Round 2" below
for what changed and why). The operator's decision this module implements:
**MQTT (through events-cli) is the internal events substrate; the dashboard's
server-sent event stream (task ``t16``) is a PROJECTION of it; browsers never
touch MQTT.** :class:`Bus` therefore does two things on every
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
:class:`Bus` instance and assigned in the SAME critical section as in-process
delivery (round 2, defect 3 — see below), so two publishing threads can never
disagree with a subscriber about delivery order. ``kind`` is one of
:data:`EVENT_KINDS`. Every event is validated against the required-field
contract for its kind (:data:`_REQUIRED_DATA_FIELDS`, mirrored — same field
names, same kinds — in the committed JSON fixtures under
``tests/fixtures/events/``, which are this module's CONTRACT with the
dashboard web app's own tests (task ``t17``): both sides validate against the
identical files, so the two cannot drift apart silently. An event that fails
validation is never sent to the broker or to any subscriber; it is refused and
recorded as a :class:`BusDegradation` naming the kind and the failing field
(never raised — C3).

Privacy boundary (read this before wiring a new event source)
---------------------------------------------------------------
``transcript`` and ``reply`` are the ONLY kinds allowed to carry speech — the
user's words and the reply text. Every other kind (``state``, ``mic``,
``turn``, ``degradation``, ``features``, ``clients``, ``heartbeat``) carries
only status, counts, and degradation CODES, never text a person said or a
model produced.

**Round 2, defect 6b: speech is opt-in on a subscription, not opt-out.**
:meth:`Bus.subscribe`'s DEFAULT (``kinds=None``, ``include_speech=False``)
excludes ``transcript``/``reply`` — lesson 7 (private by default) applies to a
subscription's own reach, not just to what is written to disk. A caller that
genuinely wants everything passes ``include_speech=True``; a caller that names
``kinds=`` explicitly (including naming ``transcript``/``reply`` in it) has
already made the deliberate ask, so an explicit ``kinds=`` is honoured as-is
without needing ``include_speech`` too. This module's job is still only to
make the boundary explicit and machine-checkable, not to authenticate WHO may
open a speech-carrying subscription — that enforcement belongs to the SSE
projection layer, task ``t16``.

Degradation vocabulary (C3 — never raise, always record)
------------------------------------------------------------
- :data:`DEGRADED_BROKER_UNAVAILABLE` — events-cli/paho-mqtt not importable,
  the broker refused a connection, or a publish failed.
- :data:`DEGRADED_BROKER_RECOVERED` — round 2, defect 6a: a bounded retry
  (:meth:`Bus.tick`-driven) reached the broker again after it had degraded.
- :data:`DEGRADED_SCHEMA_INVALID` — an event failed its kind's required-field
  contract, or named an unknown ``kind``.
- :data:`DEGRADED_SECRET_REDACTED` — an event's serialised form contained one
  of the literal values passed to ``Bus(redact=...)``.
- :data:`DEGRADED_OVERSIZE` — an event's serialised form exceeded
  :data:`DEFAULT_MAX_EVENT_BYTES` (or the constructor override).
- :data:`DEGRADED_PROTECTED_DROP` — round 2, defect 5: a subscriber's bounded
  queue had to evict a NON-``features`` event (a ``transcript``/``degradation``/
  etc.) as a last resort because no ``features`` event was queued to sacrifice
  instead. The brief said "never" for this; the honest version implemented
  here is "never SILENTLY" — see :data:`_DROP_FIRST_KINDS` and
  :class:`Subscription`.

Every degradation CODE is recorded and counted in bounded memory regardless of
how many times it fires — see "Round 2, defect 4" below.

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
etc.).

Connection approach, reused rather than reinvented
--------------------------------------------------------
This module talks to the broker the same way :mod:`embodiment.events` does —
a lazy import of ``events_cli.core`` (pure-stdlib envelope/topic contract) and
``events_cli.client.EventClient`` (the paho-mqtt transport), degrade on
failure, never a raise. It keeps its OWN pair of lazy loaders
(:func:`_load_envelope_core`, :func:`_load_event_client_class`) rather than
importing :mod:`embodiment.events`'s private ones — see the round-1 rationale,
unchanged.

Round 2 — what an independent probe found, and the fix
------------------------------------------------------------
1. **Speech leaked into a degradation reason.** A broker client (or paho
   itself) can raise an exception, or return a ``PublishResult`` whose
   ``.reason``, whose TEXT is attacker/caller-controlled — a broker error
   message can legally echo back the payload it choked on. The round-1 code
   interpolated ``{exc}`` at four sites and copied ``result.reason`` verbatim
   into a degradation ``reason``, which is delivered to every subscriber of
   ``kind="degradation"``, authenticated or not. Fixed at the time with a
   private ``_describe_exception`` returning only the class name and an
   ``OSError`` errno name — since replaced by the shared
   :func:`~embodiment.safe_reason.describe_exception` (round 4, below);
   ``result.reason`` still goes through :func:`_reason_or_generic`, a
   different problem (see round 4's note on why the two are not merged).
   Neither path ever includes ``str(exc)``, ``repr(exc)`` or ``exc.args``.
2. **A slow broker blocked the publisher.** ``publish()`` used to call the
   broker client synchronously, so a hot ``features`` publish (~30-60/s from
   the audio path) blocked on however long the broker took. Fixed: broker
   fan-out now goes through a bounded queue (:data:`DEFAULT_BROKER_QUEUE_SIZE`)
   drained by ONE lazily-started daemon worker thread
   (:meth:`Bus._broker_worker`), using the SAME drop-oldest-``features``-first
   policy as a subscriber (:func:`_drop_policy`, factored out so both queues
   share one implementation — lesson 8). In-process delivery stays fully
   synchronous inside :meth:`publish` — only the network hop moved off the
   caller's thread.
3. **Delivery could arrive out of ``seq`` order under concurrency.** ``seq``
   used to be assigned under the lock, then the lock released before
   in-process delivery — two threads could then deliver in the opposite order
   from the one their ``seq`` values imply. Fixed: seq assignment and
   in-process delivery are now ONE critical section (see :meth:`publish`); the
   lock is never held across the broker enqueue or an ``on_degrade`` call.
4. **``degradations`` was unbounded.** 50,000 invalid publishes used to hold
   50,000 :class:`BusDegradation` records — a hostile or buggy publisher could
   exhaust the daemon's memory. Fixed: :attr:`Bus.degradations` now keeps only
   the FIRST occurrence per distinct CODE (capped at
   :data:`_MAX_DEGRADATION_CODES` distinct codes, itself far above this
   module's fixed vocabulary), :attr:`Bus.degradation_counts` tracks the total
   occurrences per code, and :attr:`Bus.degradations_dropped` counts anything
   that hit the distinct-code cap. A failing ``on_degrade`` hook is likewise
   counted (:attr:`Bus.hook_errors`) rather than silently swallowed — which
   also means that swallow is no longer "silent" in
   ``tests/test_no_silent_degradation.py``'s own terms, and its allow-list
   entry for ``Bus._degrade`` was removed accordingly.
5. **Transcripts could be dropped with ``drops`` still reading zero.**
   :class:`Subscription`'s single ``_drops`` counter conflated two different
   things: the PENDING count that rides onto the next delivered event's
   ``gap`` field (and resets once reported) and a lifetime total a host would
   read via ``.drops``. Reading ``.drops`` after fully draining a subscription
   always saw the just-reset pending count — 0 — even though real drops had
   happened. Fixed: a separate lifetime :attr:`Subscription.drops` (never
   resets) and :attr:`Subscription.drops_by_kind`. Separately: the fallback
   policy (evict the oldest event of ANY kind when no ``features`` event is
   queued to sacrifice) can still evict a ``transcript``/``degradation`` — an
   unbounded queue would be worse — but it is no longer silent:
   :attr:`Subscription.protected_drops` counts it, and :class:`Bus` records ONE
   bounded, counted :data:`DEGRADED_PROTECTED_DROP` (via the same bounded
   ledger from defect 4) naming that a protected kind was sacrificed.
6. Two questions answered by measurement/code, not just fixed:
   a. **The broker degraded once and was never retried.** Fixed: bounded
      retry driven by :meth:`Bus.tick` (no clock of its own — see "no thread
      started here" below), at most once every
      :data:`DEFAULT_BROKER_RETRY_INTERVAL_S` seconds, recording
      :data:`DEGRADED_BROKER_RECOVERED` on success.
   b. **``subscribe()`` defaulted to everything, including speech.** Fixed —
      see the privacy-boundary section above.

Round 3 — a 27B review reproduced three more behavioural defects
------------------------------------------------------------------
1. **``close()`` never actually disabled the broker.** The round-2 worker
   loop only checked ``_broker_stop`` when its queue was EMPTY
   (``while not queue and not stop: wait()``) — a non-empty queue skipped
   that check entirely and kept draining, ignoring a concurrent ``close()``
   for as long as the backlog took to send. Worse, ``close()`` cleared
   ``self._broker_client`` to ``None`` unconditionally while the worker could
   still be mid-drain, so the worker's very next ``_ensure_broker_client()``
   call rebuilt a SECOND client via ``client_factory`` and kept publishing
   through it — measured: 6 queued events against a 0.5s-per-call client,
   ``close(deadline=0.3)`` reported ``unsent=4``, yet all 6 had reached the
   broker 3.5s later through a rebuilt client the report never mentioned.
   Fixed: :attr:`Bus._closed` (a :class:`threading.Event`, distinct from the
   temporary, retry-eligible ``_broker_disabled``) is set FIRST in
   :meth:`close`, before anything else, and is honoured at the top of
   :meth:`_publish_broker_sync`, :meth:`_ensure_broker_core`,
   :meth:`_ensure_broker_client` AND :meth:`tick` — a closed bus builds no
   client, ever, after ``close()`` was called. The worker loop now checks
   ``_broker_stop`` BEFORE looking at the queue, every iteration, so a
   non-empty queue no longer masks a pending stop. The one event already
   mid-flight when ``close()`` runs may still complete (an in-progress
   blocking network call cannot be aborted from outside), but nothing past it
   ever gets dequeued — making :attr:`BusCloseReport.broker_events_unsent`
   genuinely true, not a snapshot a moment before more sending happened.
2. **``publish()`` after ``close()`` was silently accepted.** It returned a
   real :class:`Event`, burned a ``seq``, delivered to zero subscribers (they
   were already closed) and recorded nothing — C3 violated by omission.
   Fixed: :meth:`publish` checks :attr:`Bus._closed` FIRST, before validating
   anything or touching ``seq``, and refuses with one bounded, counted
   :data:`DEGRADED_PUBLISH_AFTER_CLOSE`.
3. **``on_degrade`` fired per OCCURRENCE, not per new distinct code.** The
   docstring on :meth:`Bus.__init__`'s ``on_degrade`` parameter always said
   "whenever a NEW distinct code is first recorded"; the round-2
   implementation called the hook on every :meth:`_degrade` invocation
   regardless — measured: 1000 invalid publishes (one distinct code) meant
   1000 hook calls. Fixed to match the DOCSTRING's contract, not the code's:
   the hook now fires exactly once per distinct code, the moment it is FIRST
   recorded; :attr:`Bus.degradation_counts` already carries every subsequent
   occurrence, so nothing is lost, only de-duplicated at the hook boundary —
   the same reasoning :mod:`embodiment.daemon.state`'s own bounded ledger
   already applies to what it persists.

Round 4 — adopted the shared sanitiser
-----------------------------------------
:mod:`embodiment.safe_reason` landed on ``realtime/phase-b`` (wave-1 lesson
5's package-wide fix — ``turn.py``, ``tools.py`` and ``memory.py`` all broke
the same "no speech in a record" rule the same way, ``str(exc)``, and this
module's own round-2 defect 1 was a fourth instance of exactly that). This
module's private ``_describe_exception`` is now
:func:`~embodiment.safe_reason.describe_exception` at every
exception-to-reason site — it does the same job (class name, ``OSError``
errno NAME, never the message) plus more this module never had: the bounded
``__cause__``/``__context__`` chain, a message-length fact and an 8-hex
fingerprint for correlating repeats without the text, and an operator escape
hatch (``EMBODIMENT_UNSAFE_REASONS=1``) that is off by default and loud about
what turning it on costs. ``tests/test_safe_reason.py``'s AST guard now scans
this module automatically, because it scans every module whose source
mentions ``safe_reason`` — adoption is what binds the guard, not a
maintained list.

``_reason_or_generic`` is UNCHANGED and deliberately not folded into this
adoption: it solves a different problem. ``describe_exception`` sanitises an
EXCEPTION this module caught; ``_reason_or_generic`` sanitises a
``PublishResult.reason`` STRING a broker client handed back with no
exception involved at all — there is no ``BaseException`` for
``describe_exception`` to describe. The two are cousins (both refuse to
trust dependency-supplied text), not the same function.

No thread started for the pure parts
-------------------------------------
Per the round-1 brief, the HEARTBEAT and RETRY decisions read no clock of
their own — both are driven by :meth:`Bus.tick`, which a host's own daemon
loop (or a test) calls with a wall-clock ``now`` it read itself, the same
"no clock inside the pure parts" discipline :mod:`embodiment.audio.features`
and :mod:`embodiment.presence` already use. Round 2 DOES add one background
thread — the broker worker — because defect 2 requires it: a network hop
cannot be made non-blocking without either a thread or an async runtime, and
this package has no async runtime. The worker is lazily started, owned
entirely by :class:`Bus`, and :meth:`Bus.close` stops it with a bounded join
that reports what it left unsent (lesson 6 — shutdown is a feature).
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

from embodiment.safe_reason import describe_exception

__all__ = [
    "SCHEMA_VERSION",
    "EVENT_KINDS",
    "SPEECH_KINDS",
    "HEARTBEAT_INTERVAL_S",
    "DEFAULT_MAX_EVENT_BYTES",
    "DEFAULT_SUBSCRIBER_QUEUE_SIZE",
    "DEFAULT_BROKER_QUEUE_SIZE",
    "DEFAULT_BROKER_RETRY_INTERVAL_S",
    "DEFAULT_SOURCE",
    "DEGRADED_BROKER_UNAVAILABLE",
    "DEGRADED_BROKER_RECOVERED",
    "DEGRADED_SCHEMA_INVALID",
    "DEGRADED_SECRET_REDACTED",
    "DEGRADED_OVERSIZE",
    "DEGRADED_PROTECTED_DROP",
    "DEGRADED_PUBLISH_AFTER_CLOSE",
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
#: See the module docstring's privacy-boundary section. Excluded from a
#: subscription by default (round 2, defect 6b) — pass
#: ``Bus.subscribe(include_speech=True)`` or an explicit ``kinds=`` to get them.
SPEECH_KINDS: frozenset[str] = frozenset({"transcript", "reply"})

#: Fixed heartbeat interval, in seconds. A **judgement call**, not a measured
#: value: 15s is short enough that a dashboard viewer sees the daemon is alive
#: within one refresh cycle of a typical SSE client's own retry/timeout
#: defaults, and long enough that ten attached dashboards cost under one
#: event/second of heartbeat traffic combined. Revisit with a measurement if a
#: real dashboard's staleness tolerance ever needs to be tighter.
HEARTBEAT_INTERVAL_S: float = 15.0

#: Maximum serialised size (UTF-8 bytes) of one event's full envelope JSON
#: form. A **judgement call**: every kind's payload measured in this module's
#: own tests is well under 1 KB, so 8 KiB leaves generous headroom for a
#: longer-than-typical ``transcript``/``reply`` utterance while still bounding
#: what one event can cost a slow consumer or a real-time broker to carry.
DEFAULT_MAX_EVENT_BYTES = 8192

#: Default bound on one subscriber's own queue (number of events, not bytes).
#: A **judgement call**: at the ``features`` publish rate this package's audio
#: pipeline actually produces (~30/s per direction, so up to ~60/s combined),
#: 256 slots is a few seconds of buffering before the drop policy engages —
#: enough to absorb a GC pause or a slow SSE flush without losing a
#: ``transcript``/``degradation`` event under normal load, not so large that a
#: genuinely stuck subscriber can accumulate an unbounded amount of memory.
DEFAULT_SUBSCRIBER_QUEUE_SIZE = 256

#: Default bound on the ONE broker fan-out queue (round 2, defect 2), shared by
#: every publish regardless of how many in-process subscribers exist. A
#: **judgement call**: sized the same order of magnitude as one subscriber's
#: queue — a few seconds of ``features``-rate buffering — because it feeds a
#: single external consumer (the broker) rather than N dashboards, so it does
#: not need N times the headroom.
DEFAULT_BROKER_QUEUE_SIZE = 512

#: How often (seconds) a disabled broker connection is retried, driven by
#: :meth:`Bus.tick`. A **judgement call**: long enough that a broker that is
#: genuinely down for a while is not hammered by reconnect attempts (each of
#: which costs one degradation-ledger increment), short enough that an
#: operator who restarts the broker mid-session sees the daemon recover within
#: a few dashboard refresh cycles.
DEFAULT_BROKER_RETRY_INTERVAL_S: float = 5.0

#: The CloudEvents ``source`` this bus mints when the constructor is not given
#: one explicitly. Mirrors :data:`embodiment.events.DEFAULT_SOURCE`'s shape
#: without importing it, for the same "own degrade path" reason discussed in
#: the module docstring.
DEFAULT_SOURCE = "app://embodiment"

# ── the degradation vocabulary (C3) ─────────────────────────────────────────

#: events-cli/paho-mqtt unavailable, connect failed, or a publish failed.
DEGRADED_BROKER_UNAVAILABLE = "bus-broker-unavailable"
#: A bounded retry (:meth:`Bus.tick`) reached the broker again after it had
#: degraded. Round 2, defect 6a.
DEGRADED_BROKER_RECOVERED = "bus-broker-recovered"
#: An unknown ``kind``, or a known kind missing/mistyping a required field.
DEGRADED_SCHEMA_INVALID = "bus-schema-invalid"
#: The event's serialised form contained a literal value from ``redact=``.
DEGRADED_SECRET_REDACTED = "bus-secret-redacted"  # nosec B105
#: The event's serialised form exceeded the configured maximum size.
DEGRADED_OVERSIZE = "bus-event-oversize"
#: A subscriber's bounded queue evicted a non-``features`` event as a last
#: resort (round 2, defect 5). Never the payload of the evicted event, only
#: its kind — see :meth:`Bus._deliver_and_record_locked`.
DEGRADED_PROTECTED_DROP = "bus-protected-drop"
#: ``publish()`` was called after :meth:`Bus.close`. Round 3, defect 2.
DEGRADED_PUBLISH_AFTER_CLOSE = "bus-publish-after-close"

#: Characters in Unicode category ``Cf`` ("format") — includes every bidi
#: override/embedding/isolate control (U+200E/F, U+202A-E, U+2066-69) and
#: zero-width joiners/non-joiners. Stripped from every degradation ``reason``
#: by :func:`_sanitize_reason`, the ONE sanitiser every reason string in this
#: module goes through (lesson 8).
_STRIP_CATEGORY = "Cf"

#: Restricts a token field (``source``/``code`` on a folded degradation, and —
#: round 2 — a broker's own ``PublishResult.reason``) to a safe, loggable
#: charset — the same discipline this package applies to ids elsewhere
#: (lesson 5: ids are hashed or restricted to this exact charset).
_SAFE_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
_MAX_TOKEN_LEN = 80
_MAX_REASON_LEN = 500

#: How many DISTINCT degradation codes :class:`Bus` keeps a first-occurrence
#: record for (round 2, defect 4). A **judgement call**: this module's own
#: fixed vocabulary is 6 codes today; 64 is generous headroom for a future
#: addition while still bounding memory against a hostile/buggy caller that
#: somehow produces many distinct codes.
_MAX_DEGRADATION_CODES = 64

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

#: Kinds dropped FIRST under queue back-pressure — a subscriber's own queue
#: (:class:`Subscription`) AND the shared broker fan-out queue both use this
#: same policy (:func:`_drop_policy`, lesson 8: one code path). Currently just
#: ``features`` — the high-rate kind named in the brief — kept as a set rather
#: than a single constant so a future high-rate kind can join it in one place.
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
    """Restrict a token to a safe charset. Used for ids and codes this module
    itself constructs (where losing a stray character is harmless)."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    cleaned = "".join(ch for ch in value if ch in _SAFE_TOKEN_CHARS)
    return cleaned[:_MAX_TOKEN_LEN] or "unknown"


#: The generic fallback :func:`_reason_or_generic` uses when a transport's own
#: text is not already clean. Contains no character outside the safe token
#: charset by construction (checked by ``tests/test_bus.py``), so it can never
#: itself be mistaken for a partially-filtered leak.
_GENERIC_TRANSPORT_REASON = "ok-false"


def _reason_or_generic(value: object) -> str:
    """Surface a transport-controlled reason ONLY when it is already a clean
    safe token; otherwise a fixed generic string. Round 2, defect 1.

    Unlike :func:`_safe_token`, this does NOT strip-then-keep-the-rest — that
    still lets an attacker's own text characters (letters/digits/hyphens/
    underscores/dots) survive verbatim, defeating the point for a marker made
    entirely of "safe" characters. A real broker's ``PublishResult.reason`` is
    always a short slug (e.g. ``"no_conn"``), which passes through unchanged;
    anything that required ANY alteration to become safe is untrusted and is
    replaced wholesale, never partially kept.
    """
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if not text:
        return _GENERIC_TRANSPORT_REASON
    token = _safe_token(text)
    if token != text or token == "unknown":  # nosec B105
        return _GENERIC_TRANSPORT_REASON
    return token


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
    broker_worker_stopped: bool
    broker_events_unsent: int


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


def _iter_strings(obj: Any) -> Iterable[str]:
    """Yield every string reachable inside *obj* (dict keys AND values, list/tuple
    items, recursively). Used for secret scanning — see :func:`_contains_secret`.
    """
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_strings(key)
            yield from _iter_strings(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_strings(item)


def _contains_secret(payload: Any, secrets: Iterable[str]) -> bool:
    """True if any of *secrets* is a substring of any raw string inside *payload*.

    Round 2: scans the raw Python values, NOT a JSON-serialised string. A
    round-1 defect checked ``secret in event.to_json()`` instead — JSON
    escapes ``"`` and ``\\`` inside string values, so a secret containing
    either character (independently verified: ``ab"cd-KEY`` was NOT redacted)
    could never match the escaped text even though the unredacted secret sat
    right there in the event's own ``data`` dict. Comparing raw strings to raw
    strings has no escaping to defeat it.
    """
    secrets = [s for s in secrets if s]
    if not secrets:
        return False
    for text in _iter_strings(payload):
        for secret in secrets:
            if secret in text:
                return True
    return False


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


def _drop_policy(
    queue: "deque[Event]", maxsize: int, incoming_kind: str
) -> tuple[bool, Optional[str], bool]:
    """The ONE bounded-queue eviction policy shared by :class:`Subscription` and
    :class:`Bus`'s broker fan-out queue (round 2, defect 2 — lesson 8: one code
    path instead of two queues quietly drifting apart).

    Returns ``(accept, dropped_kind, protected)``:

    - ``accept`` — ``True`` if *incoming* should be appended to *queue* (the
      caller still does the actual ``append``; this function only decides and,
      when eviction is needed, mutates *queue* to make room).
    - ``dropped_kind`` — the ``kind`` of the event actually dropped by THIS
      call, or ``None`` if nothing was (queue had room).
    - ``protected`` — ``True`` when the dropped event was NOT a
      :data:`_DROP_FIRST_KINDS` member — i.e. a ``transcript``/``degradation``/
      etc. had to be sacrificed as a LAST RESORT because no ``features`` event
      was queued to evict instead.

    Policy: an incoming drop-first-kind event arriving at capacity is dropped
    OUTRIGHT (never evicts anything — ``accept=False``). Anything else
    arriving at capacity first evicts the OLDEST queued drop-first-kind event
    (freeing room without ever touching a protected kind); only when no
    drop-first event is queued at all does it fall back to evicting the oldest
    event of ANY kind, so the queue still cannot grow unbounded — this is the
    "last resort, never SILENTLY" path (round 2, defect 5).
    """
    if len(queue) < maxsize:
        return True, None, False
    if incoming_kind in _DROP_FIRST_KINDS:
        return False, incoming_kind, False
    for index, queued in enumerate(queue):
        if queued.kind in _DROP_FIRST_KINDS:
            evicted = queue[index]
            del queue[index]
            return True, evicted.kind, False
    evicted = queue[0]
    queue.popleft()
    return True, evicted.kind, True


class Subscription:
    """A bounded, thread-safe per-consumer queue an SSE handler (task t16) drains.

    Never grows unbounded: once ``maxsize`` events are queued, further
    delivery drops the OLDEST event still eligible to be dropped rather than
    blocking the publisher or growing memory, via :func:`_drop_policy`.
    ``features`` events are dropped FIRST — see the module docstring — so a
    slow dashboard loses live waveform frames long before it loses a spoken
    turn or a degradation record. Every drop is counted PENDING (rides onto
    the ``gap`` field of the next event this subscription actually delivers,
    then resets) AND cumulatively (:attr:`drops`, :attr:`drops_by_kind`,
    :attr:`protected_drops` — round 2, defect 5: these never reset, so
    draining a subscription and then reading ``.drops`` still reports the
    true lifetime total rather than the just-reset pending count).
    """

    __slots__ = (
        "_kinds",
        "_maxsize",
        "_cv",
        "_queue",
        "_pending_drops",
        "_lifetime_drops",
        "_drops_by_kind",
        "_protected_drops",
        "_closed",
    )

    def __init__(self, *, kinds: Optional[Iterable[str]] = None, maxsize: int) -> None:
        self._kinds = frozenset(kinds) if kinds is not None else None
        self._maxsize = max(1, int(maxsize))
        self._cv = threading.Condition()
        self._queue: deque[Event] = deque()
        self._pending_drops = 0
        self._lifetime_drops = 0
        self._drops_by_kind: dict[str, int] = {}
        self._protected_drops = 0
        self._closed = False

    @property
    def kinds(self) -> Optional[frozenset[str]]:
        return self._kinds

    def qsize(self) -> int:
        with self._cv:
            return len(self._queue)

    @property
    def drops(self) -> int:
        """Lifetime total events dropped from this subscription. Never resets."""
        with self._cv:
            return self._lifetime_drops

    @property
    def drops_by_kind(self) -> dict[str, int]:
        """Lifetime drops broken down by the KIND of event that was dropped."""
        with self._cv:
            return dict(self._drops_by_kind)

    @property
    def protected_drops(self) -> int:
        """Lifetime count of drops that sacrificed a NON-``features`` event
        (round 2, defect 5's "never silently" case)."""
        with self._cv:
            return self._protected_drops

    def _record_drop_locked(self, dropped_kind: str, *, protected: bool) -> None:
        """Call with ``self._cv``'s lock already held."""
        self._pending_drops += 1
        self._lifetime_drops += 1
        self._drops_by_kind[dropped_kind] = self._drops_by_kind.get(dropped_kind, 0) + 1
        if protected:
            self._protected_drops += 1

    def _offer(self, event: Event) -> Optional[str]:
        """Called by :class:`Bus`, with ``Bus._lock`` already held, for every
        published event. Never raises. Returns the KIND of a protected event
        this call had to evict (round 2, defect 5), or ``None``.
        """
        if self._kinds is not None and event.kind not in self._kinds:
            return None
        with self._cv:
            if self._closed:
                return None
            accept, dropped_kind, protected = _drop_policy(self._queue, self._maxsize, event.kind)
            if dropped_kind is not None:
                self._record_drop_locked(dropped_kind, protected=protected)
            if accept:
                self._queue.append(event)
                self._cv.notify()
        return dropped_kind if protected else None

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
            gap, self._pending_drops = self._pending_drops, 0
            if gap:
                event = replace(event, gap=gap)
            return event

    def drain(self) -> list[Event]:
        """Return every currently-queued event without blocking, resetting the
        PENDING (not lifetime) drop counter."""
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
        broker_queue_size: see :data:`DEFAULT_BROKER_QUEUE_SIZE`.
        broker_retry_interval_s: see :data:`DEFAULT_BROKER_RETRY_INTERVAL_S`.
        host, port: forwarded to a lazily-constructed real
            ``events_cli.client.EventClient``. Ignored when ``client`` or
            ``client_factory`` is supplied.
        client, client_factory: as :class:`embodiment.events.EventEmitter` —
            an injection seam for tests and advanced hosts.
        on_degrade: optional callback invoked whenever a NEW distinct
            :class:`BusDegradation` code is first recorded. Best-effort: an
            exception from it is counted (:attr:`hook_errors`), never
            propagated.
    """

    def __init__(
        self,
        *,
        source: str = DEFAULT_SOURCE,
        redact: Iterable[str] = (),
        max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES,
        subscriber_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE,
        broker_queue_size: int = DEFAULT_BROKER_QUEUE_SIZE,
        broker_retry_interval_s: float = DEFAULT_BROKER_RETRY_INTERVAL_S,
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

        #: Set exactly once, first thing, by :meth:`close` (round 3, defect 1).
        #: Distinct from ``_broker_disabled``, which is a TEMPORARY,
        #: retry-eligible state ``tick()`` may clear again — ``_closed`` never
        #: clears. Honoured at the top of :meth:`publish`, :meth:`tick`,
        #: :meth:`_publish_broker_sync`, :meth:`_ensure_broker_core` and
        #: :meth:`_ensure_broker_client`: once set, no new client is ever
        #: built and no new work is ever accepted.
        self._closed = threading.Event()

        # ── bounded degradation ledger (round 2, defect 4) ──────────────────
        self.degradations: list[BusDegradation] = []
        self.degradation_counts: dict[str, int] = {}
        self.degradations_dropped = 0
        self.hook_errors = 0
        self._degradation_seen: set[str] = set()

        # ── broker fan-out: connection state, guarded by self._broker_lock ──
        self._broker_lock = threading.Lock()
        self._broker_disabled = False
        self._broker_client: Optional[Any] = None
        self._envelope_cls: Optional[Any] = None
        self._type_to_topic: Optional[Any] = None
        self._broker_retry_interval_s = max(0.0, float(broker_retry_interval_s))
        self._broker_next_retry_at: Optional[float] = None

        # ── broker fan-out: the queue + worker thread, guarded by _broker_cv ─
        self._broker_queue_maxsize = max(1, int(broker_queue_size))
        self._broker_cv = threading.Condition()
        self._broker_queue: deque[Event] = deque()
        self._broker_thread: Optional[threading.Thread] = None
        self._broker_stop = threading.Event()
        self.broker_events_dropped = 0

        self._last_heartbeat: Optional[float] = None

    # ── public: publish / subscribe ─────────────────────────────────────────

    def publish(self, kind: str, data: Optional[dict[str, Any]] = None) -> Optional[Event]:
        """Validate, then deliver *data* as one ``kind`` event. Never raises (C3).

        Returns the delivered :class:`Event`, or ``None`` when the publish was
        refused — every refusal is recorded on :attr:`degradations` naming the
        reason, never silent.
        """
        if self._closed.is_set():
            # Round 3, defect 2: checked FIRST, before any validation or seq
            # assignment, so a publish after close() costs nothing — no seq
            # burned, no subscriber touched — and still records (C3).
            self._degrade(DEGRADED_PUBLISH_AFTER_CLOSE, "publish() called after close()")
            return None

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

        # Round 2, defect 3: seq assignment and in-process delivery are ONE
        # critical section, so two publishing threads can never deliver out of
        # the order their seq values imply. A validation failure discovered
        # here just costs a harmless gap in the seq sequence (no event was
        # ever going to be delivered for it). The lock is never held across
        # _degrade/on_degrade or the broker enqueue — both happen below, after
        # release.
        reject_code: Optional[str] = None
        reject_reason = ""
        protected_kinds: list[str] = []
        event: Optional[Event] = None
        with self._lock:
            self._seq += 1
            candidate = Event(
                v=SCHEMA_VERSION,
                kind=kind,
                ts=_now_rfc3339(),
                seq=self._seq,
                source=self._source,
                data=payload,
            )
            try:
                serialised = candidate.to_json()
            except (TypeError, ValueError) as exc:
                reject_code = DEGRADED_SCHEMA_INVALID
                reject_reason = f"{kind}: not JSON-serialisable: {describe_exception(exc)}"
            else:
                size = len(serialised.encode("utf-8"))
                if _contains_secret(payload, self._redact):
                    reject_code = DEGRADED_SECRET_REDACTED
                    reject_reason = f"{kind}: payload matched a redacted value"
                elif size > self._max_event_bytes:
                    reject_code = DEGRADED_OVERSIZE
                    reject_reason = f"{kind}: {size} bytes exceeds {self._max_event_bytes}"
                else:
                    event = candidate
                    for sub in self._subscribers:
                        protected = sub._offer(event)  # noqa: SLF001  # one unit
                        if protected is not None:
                            protected_kinds.append(protected)

        if reject_code is not None:
            self._degrade(reject_code, reject_reason)
            return None

        for protected_kind in protected_kinds:
            self._degrade(
                DEGRADED_PROTECTED_DROP,
                f"dropped a {_safe_token(protected_kind)} event under subscriber back-pressure",
            )

        assert event is not None  # nosec B101
        self._enqueue_broker(event)
        return event

    def subscribe(
        self,
        kinds: Optional[Iterable[str]] = None,
        *,
        include_speech: bool = False,
        maxsize: Optional[int] = None,
    ) -> Subscription:
        """Register and return a new :class:`Subscription`.

        Private by default (round 2, defect 6b, lesson 7): with ``kinds=None``
        (the default) the subscription receives every kind EXCEPT
        :data:`SPEECH_KINDS` (``transcript``/``reply``). Pass
        ``include_speech=True`` to receive everything, or name ``kinds=``
        explicitly — naming ``transcript``/``reply`` there is itself the
        deliberate ask, so an explicit ``kinds=`` is always honoured exactly
        (``include_speech`` is only consulted when ``kinds`` is omitted).
        """
        if kinds is None:
            resolved: Optional[Iterable[str]] = (
                None if include_speech else (EVENT_KINDS - SPEECH_KINDS)
            )
        else:
            resolved = kinds
        sub = Subscription(kinds=resolved, maxsize=maxsize or self._subscriber_queue_size)
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

    # ── public: heartbeat + broker retry + shutdown ─────────────────────────

    def tick(self, now: float) -> Optional[Event]:
        """Externally-driven heartbeat AND broker-retry check. Reads no clock
        of its own — both decisions are driven entirely by the *now* the
        caller supplies (see the module docstring's "no thread started for
        the pure parts" section).

        Emits a ``heartbeat`` event at most once per :data:`HEARTBEAT_INTERVAL_S`.
        Independently, when the broker is currently disabled, attempts a
        bounded reconnect at most once per :data:`DEFAULT_BROKER_RETRY_INTERVAL_S`
        (round 2, defect 6a) and records :data:`DEGRADED_BROKER_RECOVERED` on
        success. A no-op, returning ``None``, once :meth:`close` has been
        called (round 3, defect 1) — a closed bus neither emits nor retries.
        """
        if self._closed.is_set():
            return None
        self._maybe_retry_broker(now)
        with self._lock:
            due = (
                self._last_heartbeat is None or (now - self._last_heartbeat) >= HEARTBEAT_INTERVAL_S
            )
            if not due:
                return None
            self._last_heartbeat = now
        return self.publish("heartbeat", {})

    def close(self, deadline: float = 2.0) -> BusCloseReport:
        """Idempotent, never raises, returns within *deadline* seconds (lesson 6).

        Stops the broker worker thread with a bounded join and reports how
        many queued events it left unsent (round 2, defect 2; made TRUE in
        round 3, defect 1 — see the module docstring). ``self._closed`` is set
        FIRST, before anything else, so a publish/tick racing this call sees
        a closed bus rather than slipping in more work.
        """
        start = time.monotonic()
        self._closed.set()
        with self._lock:
            subs = list(self._subscribers)
            self._subscribers.clear()
        for sub in subs:
            sub.close()

        with self._broker_cv:
            self._broker_stop.set()
            self._broker_cv.notify_all()
        thread = self._broker_thread
        if thread is not None:
            remaining = max(0.0, deadline - (time.monotonic() - start))
            thread.join(timeout=remaining)
        worker_stopped = thread is None or not thread.is_alive()
        with self._broker_cv:
            unsent = len(self._broker_queue)

        closed_client = False
        with self._broker_lock:
            client = self._broker_client
            self._broker_client = None
        if client is not None:
            try:
                client.close()
                closed_client = True
            except Exception:  # nosec B110 # noqa: BLE001  # teardown must never raise
                pass
        elapsed = time.monotonic() - start
        return BusCloseReport(
            closed_broker_client=closed_client,
            subscribers_closed=len(subs),
            elapsed_s=elapsed,
            broker_worker_stopped=worker_stopped,
            broker_events_unsent=unsent,
        )

    # ── internals: the bounded degradation ledger (round 2, defect 4) ──────

    def _degrade(self, code: str, reason: str) -> None:
        """Record *code*/*reason* in the bounded ledger (defect 4). Round 3,
        defect 3: ``on_degrade`` fires exactly once per NEW distinct code —
        the moment it is first recorded — matching this class's own
        docstring, not once per occurrence. Every subsequent occurrence of
        the same code still increments :attr:`degradation_counts`; only the
        hook call is de-duplicated, the same way
        :mod:`embodiment.daemon.state`'s bounded ledger already treats a
        repeated code as one thing that happened N times rather than N
        separate things.
        """
        record = BusDegradation(code=code, reason=_sanitize_reason(reason))
        is_new = False
        with self._lock:
            self.degradation_counts[code] = self.degradation_counts.get(code, 0) + 1
            if code not in self._degradation_seen:
                if len(self._degradation_seen) >= _MAX_DEGRADATION_CODES:
                    self.degradations_dropped += 1
                else:
                    self._degradation_seen.add(code)
                    self.degradations.append(record)
                    is_new = True
        if is_new and self._on_degrade is not None:
            try:
                self._on_degrade(record)
            except Exception:  # noqa: BLE001  # a hook must never raise; count, don't swallow
                with self._lock:
                    self.hook_errors += 1

    # ── internals: MQTT fan-out (round 2: async, off the caller's thread) ──

    def _enqueue_broker(self, event: Event) -> None:
        with self._broker_lock:
            disabled = self._broker_disabled
        if disabled:
            return
        accept = False
        with self._broker_cv:
            accept, dropped_kind, _protected = _drop_policy(
                self._broker_queue, self._broker_queue_maxsize, event.kind
            )
            if dropped_kind is not None:
                self.broker_events_dropped += 1
            if accept:
                self._broker_queue.append(event)
                self._broker_cv.notify()
        if accept:
            self._ensure_broker_thread()

    def _ensure_broker_thread(self) -> None:
        with self._broker_cv:
            if self._broker_thread is not None and self._broker_thread.is_alive():
                return
            if self._broker_stop.is_set():
                return
            self._broker_thread = threading.Thread(
                target=self._broker_worker, name="embodiment-bus-broker", daemon=True
            )
            self._broker_thread.start()

    def _broker_worker(self) -> None:
        """Drains :attr:`_broker_queue`, one event at a time, off the caller's
        thread (round 2, defect 2).

        Round 3, defect 1: ``_broker_stop`` is checked FIRST, every
        iteration, REGARDLESS of whether the queue is empty. The round-2
        version only checked it inside the empty-queue wait loop, so a
        non-empty queue masked a pending stop entirely and the worker kept
        draining a full backlog after ``close()`` had already returned.
        Whatever remains queued the moment stop is observed is abandoned —
        never dequeued, never sent — which is what makes
        :attr:`BusCloseReport.broker_events_unsent` true rather than a
        snapshot. The one event already popped and mid-``_publish_broker_sync``
        when stop fires may still complete (an in-flight network call cannot
        be aborted from here), but nothing past it ever starts.
        """
        while True:
            with self._broker_cv:
                if self._broker_stop.is_set():
                    return
                while not self._broker_queue and not self._broker_stop.is_set():
                    self._broker_cv.wait(timeout=1.0)
                if self._broker_stop.is_set():
                    return
                if not self._broker_queue:
                    continue
                event = self._broker_queue.popleft()
            self._publish_broker_sync(event)

    def _publish_broker_sync(self, event: Event) -> None:
        """The actual network call. Runs ONLY on the broker worker thread."""
        if self._closed.is_set():
            # Round 3, defect 1: checked FIRST, before even a CACHED client is
            # used — a closed bus must never publish again, even through a
            # client built before close() ran.
            return
        with self._broker_lock:
            if self._broker_disabled:
                return
        if not self._ensure_broker_core():
            return
        client = self._ensure_broker_client()
        if client is None:
            return
        with self._broker_lock:
            envelope_cls = self._envelope_cls
            type_to_topic = self._type_to_topic
        if envelope_cls is None or type_to_topic is None:
            return
        try:
            envelope = envelope_cls.new(
                type=f"embodiment.bus.{event.kind}",
                source=self._source,
                data=event.to_dict(),
                time=event.ts,
                id=f"evt-bus-{self._source}-{event.seq}",
            )
            topic = type_to_topic(envelope.type)
            result = client.publish_event(envelope, topic)
            if not bool(getattr(result, "ok", False)):
                # Round 2, defect 1: a broker's PublishResult.reason is
                # transport-controlled text we did not construct, and MUST NOT
                # be trusted verbatim — a hostile/buggy client can put whatever
                # it wants there, including an echo of the payload it choked
                # on. _reason_or_generic only surfaces it when restricting it
                # to a safe charset changes NOTHING (a real client's reason is
                # always a short slug like "no_conn"); anything that had to be
                # altered is dropped to a fixed, generic message instead of
                # partially leaked.
                reason = _reason_or_generic(getattr(result, "reason", None))
                self._degrade_broker(f"publish failed: {reason}")
        except Exception as exc:  # noqa: BLE001  # the worker thread must never crash
            self._degrade_broker(describe_exception(exc))

    def _ensure_broker_core(self) -> bool:
        if self._closed.is_set():  # round 3, defect 1
            return False
        with self._broker_lock:
            if self._envelope_cls is not None:
                return True
            if self._broker_disabled:
                return False
            try:
                envelope_cls, type_to_topic, _now = _load_envelope_core()
            except Exception as exc:  # noqa: BLE001  # degrade, never raise
                self._degrade_broker_locked(describe_exception(exc))
                return False
            self._envelope_cls = envelope_cls
            self._type_to_topic = type_to_topic
            return True

    def _ensure_broker_client(self) -> Optional[Any]:
        if self._closed.is_set():  # round 3, defect 1: never rebuild after close
            return None
        with self._broker_lock:
            if self._broker_client is not None:
                return self._broker_client
            if self._broker_disabled:
                return None
            try:
                if self._client is not None:
                    self._broker_client = self._client
                elif self._client_factory is not None:
                    self._broker_client = self._client_factory()
                else:
                    event_client_cls = _load_event_client_class()
                    self._broker_client = event_client_cls(host=self._host, port=self._port)
            except Exception as exc:  # noqa: BLE001  # degrade, never raise
                self._degrade_broker_locked(describe_exception(exc))
                return None
            return self._broker_client

    def _degrade_broker_locked(self, reason: str) -> None:
        """Record :data:`DEGRADED_BROKER_UNAVAILABLE`. Call with
        ``self._broker_lock`` already held. Disabling is idempotent within one
        down-period: repeated calls while already disabled are no-ops, so a
        broker down for a whole run still costs a BOUNDED number of ledger
        entries (one distinct code, a growing count — see defect 4), never one
        unbounded record per event.
        """
        if self._broker_disabled:
            return
        self._broker_disabled = True
        self._degrade(DEGRADED_BROKER_UNAVAILABLE, reason)

    def _degrade_broker(self, reason: str) -> None:
        with self._broker_lock:
            self._degrade_broker_locked(reason)

    def _maybe_retry_broker(self, now: float) -> None:
        """Round 2, defect 6a: bounded retry, driven by the caller's *now*.

        At most one retry attempt per :data:`DEFAULT_BROKER_RETRY_INTERVAL_S`
        (or the constructor override) while the broker is disabled. A
        successful reconnect records :data:`DEGRADED_BROKER_RECOVERED` and
        re-enables fan-out; a failed one re-disables via the same
        :meth:`_degrade_broker_locked` path a normal publish failure uses, so
        it costs one more bounded ledger count, never a fresh unbounded record.
        """
        with self._broker_lock:
            if not self._broker_disabled:
                return
            if self._broker_next_retry_at is not None and now < self._broker_next_retry_at:
                return
            self._broker_next_retry_at = now + self._broker_retry_interval_s
            self._broker_disabled = False
            self._broker_client = None
            self._envelope_cls = None
            self._type_to_topic = None
        recovered = self._ensure_broker_core() and self._ensure_broker_client() is not None
        if recovered:
            self._degrade(DEGRADED_BROKER_RECOVERED, "broker reachable again")
