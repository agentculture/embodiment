"""embodiment.events — an optional ObserverFn that publishes onto events-cli's fabric.

Resolves agentculture/embodiment#4. The issue was filed when embodiment carried
a hard zero-dependency constraint (C1) and argued for an injected port or a
subprocess seam specifically because ``import events_cli`` would pull in
``paho-mqtt``. Deviation d2 makes direct imports of sibling AgentCulture CLIs
an approved pattern, and ``events-cli>=0.10`` is a base dependency of this
package alongside it — so this module does the thing the issue's own analysis
called "the honest shape": a direct import of ``events_cli``, never a
subprocess adapter and never an injected ``Protocol`` a host must implement.
:mod:`embodiment.continuity` made the same move under ``d2``; both talk to
their sibling CLI in-process.

"Direct" does not mean "at module scope", though — and the reason is *not* the
one first written here. That reason was "the zero-deps guard has no allow-list
yet", which stopped being true the moment ``d2`` landed: the guard is now a
human gate over a pinned approved set, and adding ``events_cli`` to the runtime
import set would be a one-line, reviewable change rather than a wall.

The import stays lazy on its own merits. This emitter is optional in a way
continuity is not: continuity is constructed whenever a host uses the lifecycle
at all, whereas nothing here runs unless a host explicitly wires
``run(observer=...)``. A host that never emits an event should not pay to
import an MQTT client, and ``import embodiment.events`` should stay cheap for
the many hosts that only want the name in a type annotation. That is exactly
the discipline ``events_cli.client`` itself uses for ``paho-mqtt`` (its own
docstring: "paho is imported ONLY inside this module, and only when the client
is actually constructed"); this module mirrors it one layer up.
:func:`_load_envelope_core` and :func:`_load_event_client_class` are the only
two places ``events_cli`` is named, and neither runs until an
:class:`EventEmitter` actually builds or sends an event.

Shaped as an ObserverFn, not a new hook
----------------------------------------
:mod:`embodiment.loop` already ships an observability seam —
:data:`~embodiment.loop.ObserverFn`, taken by :func:`~embodiment.loop.run` as
``observer=`` — built exactly for "offer this loop's own activity to something
outside it". :class:`EventEmitter` is a ``Callable[[LoopEvent], None]`` that
satisfies that protocol structurally, so a host wires it in with one keyword
argument rather than embodiment inventing a second, parallel event hook::

    from embodiment.events import EventEmitter
    from embodiment.loop import run

    emitter = EventEmitter(repo_path=my_repo_root)
    outcome = run(complete, task, executor=executor, max_steps=20, observer=emitter)
    emitter.close()

With no ``observer=`` at all — the default — nothing about ``run()`` changes:
this module is never imported by :mod:`embodiment.loop`, and a host that never
constructs an :class:`EventEmitter` never touches ``events_cli`` in any form.
That is the acceptance criterion issue #4 names explicitly: an eventless run
stays byte-identical to today's behaviour.

What gets emitted, and why
----------------------------
:class:`~embodiment.loop.LoopEvent` already carries a stable ``kind`` token
(``turn`` / ``step`` / ``hook`` / ``phase`` / ``degradation`` / ``operator`` /
``exit``) — this module does not invent a second vocabulary on top of the
loop's own; it maps ``kind`` onto a dotted event type mechanically,
``f"embodiment.{kind}"`` (:data:`EVENT_TYPE_PREFIX`), so every existing and
future ``LoopEvent`` kind gets a corresponding event type for free and no list
here can silently fall behind the loop's own. Per issue #4's own framing,
embodiment does not own the event *vocabulary* — ``events-cli`` does, by
owning the envelope and the canonical dotted-type-to-topic mapping
(:mod:`events_cli.core.topics`) — this module is only ever a producer, never
inventing a topic string of its own (:meth:`EventEmitter._emit` always routes
through ``events_cli.core.type_to_topic``).

Deliberately NOT built here (issue #4's non-goals, unchanged): this module
does not know ``reterminal-cli`` or ``harmonics-cli`` exist, does not claim to
define a "presence event stream" (that stayed a non-goal per the converged
spec's claim c33), and does not imply replay — retained delivery through
events-cli is the *last value*, never history (events-cli#7).

The id, and why it is not ``events_cli.core.new_event_id()``
----------------------------------------------------------------
events-cli's contract is explicit: delivery is at-least-once, so "every
consumer that acts on an event must be idempotent, keyed on the envelope id"
— the obligation ON A PRODUCER is to mint an id a consumer *can* dedupe
against. A fresh random ULID (what ``Envelope.new()`` mints by default) is
unique per call, which is right for a human running ``events emit`` once, but
wrong here: if the SAME logical occurrence were ever offered to this emitter
twice — a host wrapping ``observer=`` and double-firing it, a future replay of
a recorded ``LoopEvent`` stream — two random ids would turn one real
occurrence into two "different" events, defeating the exact dedup the
contract requires. :meth:`EventEmitter._event_id` instead derives the id
deterministically from what actually identifies one occurrence: this
emitter's own ``run_id`` (stable for its whole lifetime, and itself carried on
the envelope so a consumer can group a run's events too), the event's
``kind``, and the most stable counter the loop already attaches to that kind
(``step_index`` for step/hook events, ``model_turns`` for turn events),
falling back to a per-emitter monotonic ordinal only for the kinds that carry
neither (``phase`` / ``operator`` / ``exit``). Two calls with identical inputs
always produce the identical id; different occurrences practically never
collide (a SHA-256 digest, 128 bits of it).

Never raise, degrade once (C3)
--------------------------------
Every failure mode — ``events_cli``/``paho-mqtt`` not importable, a
constructor that raises (``MqttDependencyError`` or a config ``ValueError``),
a broker that is down at construction, a mid-run publish failure, or an
injected client whose methods raise outright — is caught, recorded as exactly
ONE :class:`EventDegradation`, and the emitter then DISABLES itself for the
rest of its life: no further construction attempt, no further publish
attempt, ever. This mirrors two patterns already in this package:
:mod:`embodiment.loop`'s own observer handling ("a raising observer is
recorded ONCE and then disabled for the rest of the drive") and
:mod:`embodiment.presence_engine`'s ``_degrade_muse`` ("record the failed
invocation, the transition itself, and the reason; render one notice; then
unbind the seam so a dead endpoint is not re-dialled at every step"). A dead
broker costs this module exactly one recorded transition per run, never one
per loop step.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from embodiment.identity import resolve_identity

if TYPE_CHECKING:  # pragma: no cover - typing only; never imported at runtime here
    from embodiment.loop import LoopEvent

__all__ = [
    "DEFAULT_SOURCE",
    "EVENT_TYPE_PREFIX",
    "DEGRADED_UNAVAILABLE",
    "DEGRADED_CONNECT",
    "DEGRADED_PUBLISH",
    "DEGRADED_EMIT",
    "EventDegradation",
    "EventEmitter",
]

#: Every dotted event type this module mints starts with this prefix —
#: embodiment names its OWN activity; it never invents a type in someone
#: else's namespace.
EVENT_TYPE_PREFIX = "embodiment."

#: The CloudEvents ``source`` used when no consuming rig's identity resolves
#: and none is passed explicitly. A URI (events-cli's ``_check_source``
#: requires a scheme), and an honest one: it names the PRODUCER when the
#: caller configured no identity of its own, never a guessed one.
DEFAULT_SOURCE = "app://embodiment"

# ── the degradation vocabulary (C3) ─────────────────────────────────────────

#: ``events_cli`` (or its own ``paho-mqtt`` dependency) could not be imported.
DEGRADED_UNAVAILABLE = "events-cli-unavailable"
#: Building or connecting the transport client raised.
DEGRADED_CONNECT = "connect-failed"
#: A publish attempt returned a clean, non-``ok`` result (no exception).
DEGRADED_PUBLISH = "publish-failed"
#: Anything else while building or sending one event — an invalid envelope, a
#: bad topic mapping, or an injected client whose method raised outright.
DEGRADED_EMIT = "emit-failed"

#: How many characters of a ``LoopEvent``'s free-text ``detail`` ride onto the
#: envelope's ``data.detail``. Defensive only — nothing in events-cli bounds a
#: data value's length, but an unbounded model/tool string should not dictate
#: the size of a message a real-time broker has to carry.
_MAX_DETAIL_CHARS = 500


@dataclass(frozen=True)
class EventDegradation:
    """One recorded, host-visible transition (C3) when emission stops working.

    Exactly one of these is ever produced per :class:`EventEmitter` instance —
    see the module docstring's "degrade once" discipline — but
    :attr:`EventEmitter.degradations` is still a list, the same shape
    :class:`~embodiment.loop.LoopOutcome` uses for its own ledger: a caller
    that always iterates has one fewer special case than one that sometimes
    gets ``None`` and sometimes one record.
    """

    code: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


def _load_envelope_core() -> tuple[Any, Any, Any]:
    """Import events-cli's pure-stdlib envelope core. Lazy (see module docstring).

    ``events_cli.core`` does no I/O and carries no third-party dependency of
    its own — unlike :mod:`events_cli.client`, importing it never risks a
    socket or a ``paho-mqtt`` ``ImportError``. Split out from
    :func:`_load_event_client_class` so a host that injects its own
    ``client``/``client_factory`` (as every hermetic test in this package's
    suite does) still gets the REAL envelope and topic-mapping contract
    without ever importing the transport at all.
    """
    from events_cli.core import Envelope, now_rfc3339, type_to_topic

    return Envelope, type_to_topic, now_rfc3339


def _load_event_client_class() -> Any:
    """Import the real transport client. Lazy; reached only with no injected one."""
    from events_cli.client import EventClient

    return EventClient


def _default_source(repo_path: Optional[str | Path]) -> str:
    """``agent://<resolved identity>``, or :data:`DEFAULT_SOURCE` absent one.

    Reuses :func:`embodiment.identity.resolve_identity` — the SAME value a
    consuming rig's own identity resolves to — rather than inventing a
    parallel identity notion, per issue #4's own instruction.
    """
    if repo_path is None:
        return DEFAULT_SOURCE
    identity = resolve_identity(repo_path)
    return f"agent://{identity}" if identity else DEFAULT_SOURCE


class EventEmitter:
    """An :data:`~embodiment.loop.ObserverFn` that publishes onto events-cli.

    Optional and absent by default: a host must construct one and pass it as
    ``observer=`` to :func:`embodiment.loop.run` for anything in this class to
    run at all. See the module docstring for the full design rationale.

    Args:
        source: the CloudEvents ``source`` for every envelope this instance
            mints. Explicit wins outright; absent, resolved from
            ``repo_path`` via :func:`~embodiment.identity.resolve_identity`;
            absent both, :data:`DEFAULT_SOURCE`.
        repo_path: the consuming rig's repo root, forwarded to
            :func:`~embodiment.identity.resolve_identity`. Never this
            package's own ``culture.yaml`` — see that function's docstring.
        run_id: groups every event this instance emits
            (``events_cli.core.Envelope.run_id``) and seeds the deterministic
            id derivation below. A fresh :func:`uuid.uuid4` hex string when
            omitted.
        host: forwarded to a lazily-constructed real
            ``events_cli.client.EventClient``. Ignored when ``client`` or
            ``client_factory`` is supplied.
        port: as ``host``.
        client: a pre-built client (mainly a test double, or a host that
            already manages one client's lifecycle itself). When supplied,
            ``client_factory`` and ``host``/``port`` are never consulted.
        client_factory: a zero-argument callable building a client on first
            use. Lets a test simulate a construction/connect failure by
            raising, and lets an advanced host customize construction (e.g.
            ``availability_topic``) without this module knowing about it.
        on_degrade: optional callback invoked exactly once, the moment this
            emitter disables itself. Best-effort: an exception from it is
            swallowed, because a broken notification hook must never block
            the disable transition it is being told about.
    """

    def __init__(
        self,
        *,
        source: Optional[str] = None,
        repo_path: Optional[str | Path] = None,
        run_id: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client: Optional[Any] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        on_degrade: Optional[Callable[[EventDegradation], None]] = None,
    ) -> None:
        self._source = source if source else _default_source(repo_path)
        self._run_id = run_id or uuid.uuid4().hex
        self._host = host
        self._port = port
        self._client: Optional[Any] = client
        self._client_factory = client_factory
        self._on_degrade = on_degrade

        self._envelope_cls: Optional[Any] = None
        self._type_to_topic: Optional[Any] = None
        self._now_rfc3339: Optional[Any] = None

        self._disabled = False
        self._sequence = 0
        self.degradations: list[EventDegradation] = []

    # ── public state ─────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """True while this emitter is still willing to attempt a publish."""
        return not self._disabled

    @property
    def source(self) -> str:
        return self._source

    @property
    def run_id(self) -> str:
        return self._run_id

    # ── the ObserverFn contract ─────────────────────────────────────────

    def __call__(self, event: LoopEvent) -> None:
        """Offer one :class:`~embodiment.loop.LoopEvent`. Never raises (C3)."""
        if self._disabled:
            return
        try:
            self._emit(event)
        except Exception as exc:  # noqa: BLE001  # an ObserverFn must never abort a drive
            self._degrade(DEGRADED_EMIT, f"{type(exc).__name__}: {exc}")

    def close(self) -> None:
        """Best-effort teardown of a lazily-built transport client. Never raises.

        A host that constructs an :class:`EventEmitter` owns calling this once
        the drive it was wired into is done — mirroring
        ``events_cli.client.EventClient.close``'s own "safe to call from a
        ``finally``" contract. A no-op when no client was ever built (the
        emitter degraded before its first publish, or was never called).
        """
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # nosec B110 # noqa: BLE001  # teardown must never raise either
            pass

    def __enter__(self) -> "EventEmitter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ── internals ────────────────────────────────────────────────────────

    def _emit(self, event: LoopEvent) -> None:
        if not self._ensure_core():
            return
        client = self._ensure_client()
        if client is None:
            return
        envelope = self._build_envelope(event)
        topic = self._type_to_topic(envelope.type)
        result = client.publish_event(envelope, topic)
        if not bool(getattr(result, "ok", False)):
            reason = getattr(result, "reason", "publish returned ok=False")
            self._degrade(DEGRADED_PUBLISH, f"publish failed: {reason}")

    def _ensure_core(self) -> bool:
        """Lazily resolve the envelope/topic seam once; ``False`` degrades."""
        if self._envelope_cls is not None:
            return True
        try:
            envelope_cls, type_to_topic, now_rfc3339 = _load_envelope_core()
        except Exception as exc:  # degrade, never raise
            self._degrade(DEGRADED_UNAVAILABLE, f"{type(exc).__name__}: {exc}")
            return False
        self._envelope_cls = envelope_cls
        self._type_to_topic = type_to_topic
        self._now_rfc3339 = now_rfc3339
        return True

    def _ensure_client(self) -> Optional[Any]:
        """Return the transport client, building it at most once ever."""
        if self._client is not None:
            return self._client
        try:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                event_client_cls = _load_event_client_class()
                self._client = event_client_cls(host=self._host, port=self._port)
        except Exception as exc:  # degrade, never raise
            self._degrade(DEGRADED_CONNECT, f"{type(exc).__name__}: {exc}")
            return None
        return self._client

    def _build_envelope(self, event: LoopEvent) -> Any:
        detail = (event.detail or "")[:_MAX_DETAIL_CHARS]
        data: dict[str, Any] = {"detail": detail}
        data.update(event.data)
        return self._envelope_cls.new(
            type=f"{EVENT_TYPE_PREFIX}{event.kind}",
            source=self._source,
            data=data,
            run_id=self._run_id,
            id=self._event_id(event),
            time=self._now_rfc3339(),
        )

    def _event_id(self, event: LoopEvent) -> str:
        """A stable id derived from this occurrence, not a fresh random one.

        See the module docstring's "The id, and why it is not
        events_cli.core.new_event_id()" section.
        """
        self._sequence += 1
        marker = f"seq={self._sequence}"
        for key in ("step_index", "model_turns"):
            if key in event.data:
                marker = f"{key}={event.data[key]}"
                break
        basis = f"{self._run_id}|{event.kind}|{marker}"
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
        return f"evt-{digest}"

    def _degrade(self, code: str, reason: str) -> None:
        """Record ONE transition, disable the emitter, notify at most once."""
        self._disabled = True
        record = EventDegradation(code=code, reason=reason)
        self.degradations.append(record)
        if self._on_degrade is not None:
            try:
                self._on_degrade(record)
            except Exception:  # nosec B110 # noqa: BLE001  # a hook must never raise either
                pass
