"""embodiment.daemon.app — the daemon: ears → perception → memory → turn → voice.

Task ``t15`` of the ``realtime-embodiment-app`` plan. Everything the earlier
waves built exists in isolation; this module is the wiring, and nothing else.
It owns no transport, no audio device, no model client and no HTTP handler —
each of those is a *seam* it is handed, so every one of them can be faked
(which is how this module is tested; the live rig is ``t21``'s job).

What runs, and on which thread
------------------------------
* **The ears thread.** One daemon thread with its own asyncio loop, running
  :meth:`connect` and then the event stream of an ears-only realtime client
  (:mod:`embodiment.realtime.client`). It never sends ``response.create`` —
  this daemon runs the turn itself, because memory (and later vision and
  tools) must reach the prompt. The thread does no work beyond classifying an
  event: a transcription is *queued*, never answered inline, so a slow turn
  cannot make the ear deaf.
* **The turn thread.** One daemon thread draining that bounded queue and
  running :meth:`DaemonApp.run_turn` — perception, recall, the model, the
  voice — one turn at a time.
* **The caller's thread.** :meth:`DaemonApp.run` blocks on the host's
  ``stop_event`` and, every ``poll_interval_s``, calls :meth:`DaemonApp.pump`:
  the bus's heartbeat/broker clock plus the drain of the played-audio feature
  frames the voice has traced since the last tick.
* **The endpoint's own capture thread** calls the frame callback this module
  installs. That callback is bound to the *generation* of the ear that was
  attached when capture started, so a displaced ear's late frame is dropped
  and counted rather than mixed into the live session (see "one ear").

One ear, and the handover is said out loud
------------------------------------------
Exactly one :class:`~embodiment.audio.endpoint.AudioEndpoint` is attached at
any moment — lobes has not validated concurrent realtime sessions, so two ears
would not merely be untidy, they would be two sessions. A second
:meth:`DaemonApp.attach_ear` either **pre-empts** (detach the first, attach the
second) or is **refused**, chosen by :attr:`AppConfig.preempt_ear`, and either
way it publishes exactly ONE ``state`` event naming both ears and appends one
ledger record (:data:`APP_EAR_PREEMPTED` / :data:`APP_EAR_REFUSED`). The whole
attach runs under one lock and the previous ear is fully torn down — capture
stopped, voice closed, endpoint detached and closed — before the new one is
installed, so there is never a moment with two live capture callbacks.

Hot mic on attach, mute in the capture path
-------------------------------------------
Attaching an ear starts capture immediately (``CLAUDE.md``: "Hot mic on
``start``, always visible, always mutable") and publishes a ``mic`` event
saying so. :meth:`DaemonApp.set_mute` calls the endpoint's own ``mute``, which
each endpoint enforces *before encode* in its capture path — never in a UI —
and publishes the new state.

Clients are information, never input
------------------------------------
:meth:`DaemonApp.attach_client` / :meth:`DaemonApp.detach_client` maintain a
count that is published as a ``clients`` event and reported by
:meth:`DaemonApp.status`. Nothing on the turn or voice path reads it: the
daemon answers whether anyone is watching or not, and attaching a client
writes nothing to disk, touches no session, and leaves ``status()`` — minus
its own ``clients`` block — byte-identical.

Degrade, never raise; and never silently
----------------------------------------
No public method here raises for an environment problem. Every fault goes
through the ONE recording path, :meth:`DaemonApp._record`: it counts the code,
appends one ledger record (crash-durable, t4) and publishes one ``degradation``
bus event. A sub-module's own fault keeps that sub-module's own code
(``voice-tts-failed``, ``recall-deadline-exceeded``, ``turn-budget-exhausted``)
because that is the name a host would look for; codes this module invents for
its own faults are prefixed ``app-``. Exception text never becomes a reason:
:func:`embodiment.safe_reason.describe_exception` is the only renderer, so no
record, log line, event or status field can carry what was said, what the model
replied, a key, or an attacker's own string.

What is NOT here
----------------
No reconnect. A dropped realtime session is recorded and the daemon keeps
running deaf until it is restarted — reconnection is a policy with its own
clocks and is not in this task. No session rotation: one
:class:`~embodiment.session.Session` per daemon run, closed at shutdown. No
live dial: :func:`http_complete`'s wire shape is this module's best reading of
an OpenAI-compatible ``/v1/chat/completions`` on the lobes gateway and is
unverified against a running one (plan task ``t21``).
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from embodiment import safe_reason
from embodiment.audio.endpoint import NullEndpoint
from embodiment.audio.features import FeatureExtractor
from embodiment.bus import Bus, fold_degradation
from embodiment.contract import ModelResponse
from embodiment.daemon.state import DaemonState, resolve_state_dir
from embodiment.http import guard as guard_module
from embodiment.http import server as server_module
from embodiment.memory import PRIVATE, RoomMemory, render_recalled
from embodiment.perception import perceive
from embodiment.realtime import wire
from embodiment.realtime.client import RealtimeConfig, RealtimeEars
from embodiment.session import Session
from embodiment.tools import ToolRegistry, bind_tools
from embodiment.turn import SYSTEM_PROMPT, TurnConfig, TurnResult
from embodiment.turn import turn as run_one_turn
from embodiment.voice import Voice, VoiceConfig, http_synthesize

__all__ = [
    "APP_SOURCE",
    "CHAT_ROUTE",
    "WINDOW_HEADER",
    "MAX_REASON_CHARS",
    "MAX_DEGRADATION_CODES",
    "MAX_FEATURE_DRAIN",
    "APP_BOOTSTRAP_DEGRADED",
    "APP_CAPTURE_FAILED",
    "APP_CLOSED",
    "APP_EARS_STREAM_ENDED",
    "APP_EARS_THREAD_FAILED",
    "APP_EARS_UNAVAILABLE",
    "APP_EAR_ATTACH_FAILED",
    "APP_EAR_DETACH_FAILED",
    "APP_EAR_PREEMPTED",
    "APP_EAR_REFUSED",
    "APP_FEATURES_FAILED",
    "APP_FRAME_FROM_STALE_EAR",
    "APP_HTTP_UNAVAILABLE",
    "APP_NO_ENDPOINT",
    "APP_PUBLISH_FAILED",
    "APP_SHUTDOWN_INCOMPLETE",
    "APP_STT_ERROR",
    "APP_STT_FRAME_MALFORMED",
    "APP_TRANSCRIPT_EMPTY",
    "APP_TRANSCRIPT_NOT_TEXT",
    "APP_TURN_FAILED",
    "APP_TURN_QUEUE_FULL",
    "AppConfig",
    "AppCloseReport",
    "EarHandover",
    "DaemonApp",
    "http_complete",
    "main",
]

#: The ``source`` every degradation this module records for itself carries.
APP_SOURCE = "app"

#: The gateway route :func:`http_complete` posts to. Unverified against a live
#: lobes instance — see the module docstring's "What is NOT here".
CHAT_ROUTE = "/v1/chat/completions"

#: The header that introduces the conversation window inside the system prompt.
#: The window is the only way prior turns can reach the model:
#: :func:`embodiment.turn.turn` takes exactly one utterance, verbatim, and the
#: verbatim invariant forbids splicing anything into it.
WINDOW_HEADER = "השיחה עד כה:"

#: Cap on any reason string this module records. A **judgement call**, matched
#: to the cap the sibling modules already use for the same field.
MAX_REASON_CHARS = 300

#: How many DISTINCT degradation codes :meth:`DaemonApp.status` keeps a count
#: for. A **judgement call**: this module's own vocabulary is 20 codes and the
#: sibling vocabularies it folds are fixed too, so 128 is generous headroom
#: while still bounding memory against a sub-module that invents codes.
MAX_DEGRADATION_CODES = 128

#: The share of the ears' shutdown slice that may be spent waiting for the
#: ears thread to publish its event loop. A **judgement call**: the loop is
#: created on the thread's first statement, so this only ever covers thread
#: start-up, and a quarter of the slice leaves three quarters for the close
#: and the join themselves.
_EARS_LOOP_WAIT_SHARE = 0.25

#: The share of the ears' shutdown slice spent waiting for the ear's own
#: ``close`` before the thread join. A **judgement call**: half, so a close
#: that stalls still leaves the join half a slice to see the thread finish.
_EARS_CLOSE_WAIT_SHARE = 0.5

#: How many traced played-audio feature frames one :meth:`DaemonApp.pump` may
#: publish. A **judgement call**: the voice's own buffer holds at most 2048
#: frames, and 256 per tick (≈10 ticks to drain a full buffer at the default
#: 0.5 s poll) bounds how long one pump can spend publishing.
MAX_FEATURE_DRAIN = 256

# ── the degradation vocabulary this module owns (C3) ─────────────────────────

#: ``main()`` could not build one of its own parts; the daemon runs degraded.
APP_BOOTSTRAP_DEGRADED = "app-bootstrap-degraded"
#: The ears client answered ``connect() is False`` — a dead or refusing gateway.
APP_EARS_UNAVAILABLE = "app-ears-unavailable"
#: The event stream ended while the daemon was still meant to be listening.
APP_EARS_STREAM_ENDED = "app-ears-stream-ended"
#: The ears thread itself failed; the daemon keeps running, deaf.
APP_EARS_THREAD_FAILED = "app-ears-thread-failed"
#: The gateway sent an ``error`` event — an STT fault, said out loud by lobes.
APP_STT_ERROR = "app-stt-error"
#: A frame arrived that did not decode to a known event.
APP_STT_FRAME_MALFORMED = "app-stt-frame-malformed"
#: No audio endpoint could be built; a :class:`NullEndpoint` stands in.
APP_NO_ENDPOINT = "app-no-audio-endpoint"
#: A second ear displaced the first (``preempt_ear`` policy).
APP_EAR_PREEMPTED = "app-ear-preempted"
#: A second ear was turned away (``preempt_ear`` off).
APP_EAR_REFUSED = "app-ear-refused"
#: Attaching an ear failed; nothing is attached.
APP_EAR_ATTACH_FAILED = "app-ear-attach-failed"
#: Detaching an ear raised; it is dropped anyway.
APP_EAR_DETACH_FAILED = "app-ear-detach-failed"
#: A captured frame could not be forwarded to the ears.
APP_CAPTURE_FAILED = "app-capture-failed"
#: A frame arrived from an endpoint that is no longer the ear. Recorded once
#: per generation; the running count is in ``status()``.
APP_FRAME_FROM_STALE_EAR = "app-frame-from-stale-ear"
#: The feature extractor failed on captured or played audio.
APP_FEATURES_FAILED = "app-features-failed"
#: The transcript the ear delivered was not text.
APP_TRANSCRIPT_NOT_TEXT = "app-transcript-not-text"
#: The transcript the ear delivered was empty; no turn was run.
APP_TRANSCRIPT_EMPTY = "app-transcript-empty"
#: The turn queue was full; this utterance was dropped rather than queued.
APP_TURN_QUEUE_FULL = "app-turn-queue-full"
#: The turn path itself failed. The daemon keeps running.
APP_TURN_FAILED = "app-turn-failed"
#: A bus publish raised. The event is lost; the turn is not.
APP_PUBLISH_FAILED = "app-publish-failed"
#: The dashboard server could not start; the daemon runs without it.
APP_HTTP_UNAVAILABLE = "app-http-unavailable"
#: Something was still unfinished when the shutdown deadline expired.
APP_SHUTDOWN_INCOMPLETE = "app-shutdown-incomplete"
#: Something was asked of the app after it closed.
APP_CLOSED = "app-closed"

_SAFE_NAME_FALLBACK = "ear"


@dataclass(frozen=True)
class AppConfig:
    """Everything the daemon runs under. No secret is ever logged from here."""

    gateway_url: str = "http://localhost:8001"
    api_key: str = field(default="", repr=False)
    language: str = wire.LANGUAGE
    #: ``True``: a second ear displaces the first. ``False``: it is refused.
    preempt_ear: bool = True
    #: The recall clock, derived from the quantity it bounds: how long a spoken
    #: turn may wait for memory before answering without it.
    recall_deadline: float = 0.25
    recall_top_k: int = 5
    recall_mode: str = "keyword"
    #: How often :meth:`DaemonApp.run` pumps the bus clock and the out-features.
    poll_interval_s: float = 0.5
    #: The whole-app shutdown bound; lifecycle's watchdog is the backstop.
    shutdown_deadline: float = 5.0
    #: Utterances that may wait for the turn thread before one is dropped.
    turn_queue_size: int = 8
    #: The model seam's own bound, in seconds.
    completion_deadline: float = 60.0
    #: The role the speaker resolves by NAME on the gateway (never by model).
    role: str = "senses"
    system_prompt: str = SYSTEM_PROMPT
    memory_scope: str = "gwen"
    added_by: str = "gwen"
    session_summary_deadline: float = 5.0
    #: Whether a browser/robot ear (:class:`~embodiment.audio.remote.RemoteEndpoint`)
    #: is started by the daemon. **Always False in v1**, by the operator's
    #: decision — the field exists so the dashboard has a stable contract to
    #: read rather than a key that appears later (t18).
    realtime_ear_enabled: bool = False
    #: The URL such an ear would be reached on. ``None`` in v1, for the same
    #: reason. Never carries a secret: the install secret goes in a header,
    #: never in a URL (``embodiment.audio.remote``'s own rule).
    realtime_ws_url: Optional[str] = None
    http_enabled: bool = True
    bind: str = "127.0.0.1"
    port: int = server_module.DEFAULT_PORT
    bind_public: bool = False


@dataclass(frozen=True)
class EarHandover:
    """What one :meth:`DaemonApp.attach_ear` / :meth:`detach_ear` did."""

    ear: str
    attached: bool
    refused: bool = False
    preempted: bool = False
    previous: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ear": self.ear,
            "attached": self.attached,
            "refused": self.refused,
            "preempted": self.preempted,
            "previous": self.previous,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AppCloseReport:
    """What :meth:`DaemonApp.close` finished, and what it did not."""

    closed: bool
    already_closed: bool = False
    ears_stopped: bool = True
    turn_thread_stopped: bool = True
    turns_abandoned: int = 0
    voice_closed: bool = True
    endpoint_closed: bool = True
    server_stopped: bool = True
    session_closed: bool = True
    memory_closed: bool = True
    bus_closed: bool = True
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "closed": self.closed,
            "already_closed": self.already_closed,
            "ears_stopped": self.ears_stopped,
            "turn_thread_stopped": self.turn_thread_stopped,
            "turns_abandoned": self.turns_abandoned,
            "voice_closed": self.voice_closed,
            "endpoint_closed": self.endpoint_closed,
            "server_stopped": self.server_stopped,
            "session_closed": self.session_closed,
            "memory_closed": self.memory_closed,
            "bus_closed": self.bus_closed,
            "elapsed_s": round(self.elapsed_s, 3),
        }

    @property
    def unfinished(self) -> tuple[str, ...]:
        """Every part that did not finish inside the deadline."""
        pairs = (
            ("ears", self.ears_stopped),
            ("turn_thread", self.turn_thread_stopped),
            ("voice", self.voice_closed),
            ("endpoint", self.endpoint_closed),
            ("server", self.server_stopped),
            ("session", self.session_closed),
            ("memory", self.memory_closed),
            ("bus", self.bus_closed),
        )
        return tuple(name for name, done in pairs if not done)


def _safe_name(value: object) -> str:
    """One sanitiser for every ear name: the ``[A-Za-z0-9._-]`` charset, capped."""
    return safe_reason.safe_label(value, fallback=_SAFE_NAME_FALLBACK)


def _safe_reason_text(value: object) -> str:
    """One sanitiser for every reason this module records."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return safe_reason.scrub(text)[:MAX_REASON_CHARS]


def http_complete(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict[str, Any]]] = None,
    gateway_url: str = "http://localhost:8001",
    api_key: str = "",
    role: str = "senses",
    max_tokens: int = 16000,
    deadline: float = 60.0,
) -> ModelResponse:
    """``POST /v1/chat/completions`` on the lobes gateway, resolving by ROLE name.

    The role goes in ``model`` because lobes resolves a role **by name** and
    never by parsing a model name (``CLAUDE.md``, "the realtime interface").
    The key goes only into the ``Authorization`` header, never the URL.

    Raises on any failure — network, non-2xx, malformed body — so
    :func:`embodiment.turn.turn` can fold it through its own single
    exception-to-reason site. **Unverified against a live gateway**: every
    test injects a fake seam, and the live dial is plan task ``t21``.
    """
    origin = gateway_url.rstrip("/")
    url = f"{origin}{CHAT_ROUTE}"
    if not url.startswith(("http://", "https://")):
        raise ValueError("gateway_url must be http(s)")
    payload: dict[str, Any] = {
        "model": role,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(  # nosec B310 - scheme checked above
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(  # nosec B310 - scheme checked above
        request, timeout=deadline
    ) as response:
        raw = json.loads(response.read().decode("utf-8"))
    choices = raw.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    usage = raw.get("usage") or {}
    return ModelResponse.from_dict(
        {
            "content": message.get("content") or "",
            "reasoning": message.get("reasoning") or message.get("reasoning_content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
        }
    )


class DaemonApp:
    """The wiring: one ear, one turn at a time, everything published."""

    def __init__(
        self,
        *,
        state: DaemonState,
        bus: Any,
        memory: Any,
        complete: Callable[..., Any],
        ears: Any,
        config: Optional[AppConfig] = None,
        endpoint_factory: Optional[Callable[[], Any]] = None,
        voice_factory: Optional[Callable[[Any], Any]] = None,
        session_factory: Optional[Callable[[], Any]] = None,
        summarise: Optional[Callable[..., Any]] = None,
        server: Any = None,
        tools: Optional[ToolRegistry] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or AppConfig()
        self._state = state
        self._bus = bus
        self._memory = memory
        self._tools = tools if tools is not None else ToolRegistry()
        self._complete = (
            complete
            if getattr(complete, "__embodiment_bound_registry__", None) is self._tools
            else bind_tools(_as_seam(complete), self._tools)
        )
        self._ears = ears
        self._endpoint_factory = endpoint_factory
        self._voice_factory = voice_factory or (lambda endpoint: None)
        self._session_factory = session_factory
        self._summarise = summarise
        self._server = server
        self._clock = clock

        self._lock = threading.RLock()
        self._ear_lock = threading.RLock()
        self._ear_name: Optional[str] = None
        self._ear_endpoint: Any = None
        self._voice: Any = None
        self._generation = 0
        self._handovers = 0
        self._refusals = 0
        self._stale_frames = 0
        self._frames_captured = 0
        self._frames_forwarded = 0
        self._features_in = FeatureExtractor()

        self._clients = 0
        self._remote_clients = 0

        self._turns_completed = 0
        self._turns_in_flight = 0
        self._turns_dropped = 0
        self._turns_failed = 0
        self._recall_mode: Optional[str] = None
        self._recall_calls = 0
        self._degradation_counts: dict[str, int] = {}
        self._publish_errors = 0
        self._ledger_errors = 0

        self._session: Any = None
        self._voice_folded = 0
        self._session_folded = 0

        self._started = False
        self._closed = False
        self._stopping = False
        self._close_report: Optional[AppCloseReport] = None

        self._turn_queue: "queue.Queue[str]" = queue.Queue(
            maxsize=max(1, int(self._config.turn_queue_size))
        )
        self._turn_thread: Optional[threading.Thread] = None
        self._ears_thread: Optional[threading.Thread] = None
        self._ears_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ears_connected = False
        self._ears_closed = threading.Event()
        self._worker_stop = threading.Event()

    # ── the ONE recording path ───────────────────────────────────────────

    def _record(
        self, code: str, reason: object, *, source: str = APP_SOURCE, once: bool = False
    ) -> None:
        """Count it, append it to the crash ledger, publish it. Never raises.

        The ledger is appended BEFORE the event is published, and that order
        is deliberate: the ledger is the crash record (t4 ``fsync``-s every
        append), so a process killed between the two keeps the durable fact
        and loses only the notification. A reader that has seen the ledger
        record has therefore not necessarily seen the event yet — they are
        eventually both, never atomically.

        *once* is for the per-frame paths: a capture that fails once fails
        30 times a second, and a ledger that is deliberately unbounded (t4)
        must not be the thing that fills the disk. The FIRST occurrence of
        that code is recorded in full and every repeat only bumps the count
        in :meth:`status` — the same "degrade once, count after" discipline
        :mod:`embodiment.realtime.client` and :mod:`embodiment.events` use.
        """
        safe_code = _safe_name(code)
        safe_text = _safe_reason_text(reason)
        with self._lock:
            seen = self._degradation_counts.get(safe_code)
            if seen is None and len(self._degradation_counts) >= MAX_DEGRADATION_CODES:
                safe_code = "app-degradation-codes-capped"
                seen = self._degradation_counts.get(safe_code)
            self._degradation_counts[safe_code] = (seen or 0) + 1
            if once and seen:
                return
        try:
            self._state.ledger.append(safe_code, safe_text)
        except Exception:  # noqa: BLE001 - the ledger promises never to raise; count anyway
            with self._lock:
                self._ledger_errors += 1
        self._publish_degradation(_safe_name(source), safe_code, safe_text)

    def _publish_degradation(self, source: str, code: str, reason: str) -> None:
        """The one publish that must never re-enter :meth:`_record`."""
        try:
            self._bus.publish("degradation", {"source": source, "code": code, "reason": reason})
        except Exception:  # noqa: BLE001 - an injected bus is not trusted; count, never recurse
            with self._lock:
                self._publish_errors += 1

    def _fold(self, source: str, record: Any) -> None:
        """Record a sibling module's own degradation, keeping its own code."""
        folded = fold_degradation(record, source=source)
        self._record(folded["code"], folded["reason"], source=folded["source"])

    def _publish(self, kind: str, data: dict[str, Any]) -> None:
        """Publish one event. A publish failure is recorded, never raised."""
        try:
            self._bus.publish(kind, data)
        except Exception as exc:  # noqa: BLE001 - an injected bus is not trusted
            with self._lock:
                self._publish_errors += 1
            self._record(APP_PUBLISH_FAILED, f"{_safe_name(kind)}: {_describe(exc)}", once=True)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Bring the daemon up: the ear, the dashboard, the ears loop. Idempotent."""
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        self._log("started", target="embodiment.daemon.app")
        self._publish("state", {"component": "daemon", "status": "starting"})
        self._attach_default_ear()
        self._start_server()
        self._start_turn_thread()
        self._start_ears_thread()
        self._publish("state", {"component": "daemon", "status": "running"})

    def run(self, stop_event: threading.Event) -> int:
        """Block until *stop_event*, then shut down within a bound. Never raises."""
        self.start()
        try:
            while not stop_event.wait(max(0.01, float(self._config.poll_interval_s))):
                self.pump()
        except Exception as exc:  # noqa: BLE001 - a daemon loop must not die on a fault
            self._record(APP_TURN_FAILED, _describe(exc))
        finally:
            self.close(deadline=self._config.shutdown_deadline)
        return 0

    def pump(self) -> None:
        """One tick of the daemon's own clock: out-features, then the bus clock."""
        self._drain_out_features()
        try:
            self._bus.tick(self._clock())
        except Exception as exc:  # noqa: BLE001 - an injected bus is not trusted
            self._record(APP_PUBLISH_FAILED, f"tick: {_describe(exc)}")

    def shutdown(self, deadline: float = 5.0) -> AppCloseReport:
        """The name :class:`embodiment.daemon.lifecycle.DaemonRunner` calls."""
        return self.close(deadline=deadline)

    def close(self, deadline: float = 5.0) -> AppCloseReport:
        """Stop everything within *deadline*, reporting what is unfinished.

        Idempotent and never raises. The order is deliberate: the ear stops
        first (nothing new arrives), then the ears session, then the turn
        worker, then the parts a turn depends on, and the bus last — so every
        degradation recorded on the way out is still published.
        """
        with self._lock:
            if self._closed:
                report = self._close_report or AppCloseReport(closed=True, already_closed=True)
                return AppCloseReport(**{**report.to_dict(), "already_closed": True})
            self._closed = True
            self._stopping = True
        started = self._clock()
        budget = max(0.1, float(deadline))
        self._worker_stop.set()

        def step(fraction: float, call: Callable[[float], bool]) -> bool:
            """One shutdown step, under the slice of the budget it is allowed."""
            share = self._share(started, budget, fraction)
            return self._bounded(lambda: call(share), share)

        endpoint_closed = step(0.20, lambda d: self.detach_ear(publish=False).attached is False)
        ears_stopped = step(0.35, self._stop_ears)
        turn_stopped = step(0.50, self._stop_turn_thread)
        abandoned = self._turn_queue.qsize()
        voice_closed = step(0.60, self._close_voice)
        server_stopped = step(0.75, self._stop_server)
        session_closed = step(0.90, self._close_session)
        memory_closed = step(0.97, self._close_memory)

        self._publish("state", {"component": "daemon", "status": "stopped"})
        bus_closed = step(1.0, self._close_bus)

        report = AppCloseReport(
            closed=True,
            already_closed=False,
            ears_stopped=ears_stopped,
            turn_thread_stopped=turn_stopped,
            turns_abandoned=abandoned,
            voice_closed=voice_closed,
            endpoint_closed=endpoint_closed,
            server_stopped=server_stopped,
            session_closed=session_closed,
            memory_closed=memory_closed,
            bus_closed=bus_closed,
            elapsed_s=self._clock() - started,
        )
        with self._lock:
            self._close_report = report
        if report.unfinished:
            self._record(APP_SHUTDOWN_INCOMPLETE, "unfinished: " + ",".join(report.unfinished))
        self._log("stopped", unfinished=list(report.unfinished))
        return report

    def _bounded(self, call: Callable[[], bool], deadline: float) -> bool:
        """Run one shutdown step on a worker thread, bounded by *deadline*.

        Lesson 2, found by attacking this module: a seam that ignores the
        deadline it was given (a memory layer whose ``close`` hangs, a server
        that will not stop) made :meth:`close` itself unbounded — the one
        method the daemon's own watchdog is relying on to come back. Each step
        now gets a thread of its own and a join; a step that does not finish
        is reported as unfinished rather than waited for.
        """
        box: list[bool] = []

        def work() -> None:
            try:
                box.append(bool(call()))
            except Exception as exc:  # noqa: BLE001 - a step that raises is a step that failed
                self._record(APP_SHUTDOWN_INCOMPLETE, _describe(exc))
                box.append(False)

        worker = threading.Thread(target=work, name="embodiment-shutdown", daemon=True)
        worker.start()
        worker.join(timeout=max(0.05, deadline))
        return bool(box and box[0])

    def _share(self, started: float, budget: float, fraction: float) -> float:
        """The deadline for the step that must be done by *fraction* of the budget."""
        return max(0.05, (started + budget * fraction) - self._clock())

    # ── the ear registry: exactly one ────────────────────────────────────

    def attach_ear(self, name: object, endpoint: Any) -> EarHandover:
        """Make *endpoint* the one active ear. Never raises.

        A second attach pre-empts or is refused by policy; either way exactly
        one ``state`` event and one ledger record describe the handover.
        """
        ear = _safe_name(name)
        with self._ear_lock:
            if self._closed:
                self._record(APP_CLOSED, f"attach_ear after close: {ear}")
                return EarHandover(ear=ear, attached=False, refused=True, reason="closed")
            previous = self._ear_name
            if previous is not None and not self._config.preempt_ear:
                self._refusals += 1
                self._publish(
                    "state",
                    {
                        "component": "ear",
                        "status": "refused",
                        "ear": ear,
                        "previous": previous,
                    },
                )
                self._record(APP_EAR_REFUSED, f"{ear} refused; {previous} keeps the session")
                return EarHandover(
                    ear=ear,
                    attached=False,
                    refused=True,
                    previous=previous,
                    reason="one ear at a time",
                )
            if previous is not None:
                self._teardown_ear()
            if not self._install_ear(ear, endpoint):
                return EarHandover(
                    ear=ear, attached=False, previous=previous, reason="attach failed"
                )
            if previous is not None:
                self._handovers += 1
                self._publish(
                    "state",
                    {
                        "component": "ear",
                        "status": "handover",
                        "ear": ear,
                        "previous": previous,
                    },
                )
                self._record(APP_EAR_PREEMPTED, f"{previous} displaced by {ear}")
            else:
                self._publish(
                    "state",
                    {"component": "ear", "status": "attached", "ear": ear, "previous": None},
                )
            self._log("ear-attached", ear=ear, previous=previous)
            self._publish_mic()
            return EarHandover(
                ear=ear, attached=True, preempted=previous is not None, previous=previous
            )

    def detach_ear(self, name: object = None, *, publish: bool = True) -> EarHandover:
        """Drop the active ear. Idempotent; never raises."""
        with self._ear_lock:
            previous = self._ear_name
            if previous is None:
                return EarHandover(ear="", attached=False, previous=None, reason="no ear")
            self._teardown_ear()
            if publish:
                self._publish(
                    "state",
                    {
                        "component": "ear",
                        "status": "detached",
                        "ear": previous,
                        "previous": previous,
                    },
                )
                self._publish_mic()
            self._log("ear-detached", ear=previous)
            return EarHandover(ear="", attached=False, previous=previous)

    def _install_ear(self, ear: str, endpoint: Any) -> bool:
        """Attach, start capture, build the voice. Returns whether it took."""
        generation = self._generation + 1
        try:
            endpoint.attach()
            endpoint.start_capture(self._frame_callback(generation))
        except Exception as exc:  # noqa: BLE001 - an endpoint is not trusted to keep its word
            self._record(APP_EAR_ATTACH_FAILED, f"{ear}: {_describe(exc)}")
            self._safely(endpoint.detach, APP_EAR_DETACH_FAILED, ear)
            return False
        self._generation = generation
        self._ear_name = ear
        self._ear_endpoint = endpoint
        self._features_in.reset()
        self._ensure_voice(ear, endpoint)
        self._fold_endpoint(endpoint)
        return True

    def _ensure_voice(self, ear: str, endpoint: Any) -> None:
        """ONE :class:`~embodiment.voice.Voice` for the daemon's life, re-pointed.

        Built by the injected factory the first time an ear attaches, and
        afterwards handed the new endpoint with ``set_endpoint`` (t12 round 3)
        rather than rebuilt. Three reasons, all of them defects in the
        rebuild-per-ear shape this replaces:

        * a new :class:`Voice` starts its degradation list empty while this
          app's fold index still pointed past it, so the new voice's first
          degradations were silently skipped until its count caught up;
        * every rebuild started a new pacing thread and a new timeline, which
          is exactly what that module's pacing worker is written to avoid; and
        * ``queued_not_traced`` and the degradation counts — the numbers that
          say what a handover cost — were thrown away with the old object.
        """
        voice = self._voice
        if voice is None:
            try:
                self._voice = self._voice_factory(endpoint)
            except Exception as exc:  # noqa: BLE001 - a factory is a seam, not a promise
                self._voice = None
                self._record(APP_EAR_ATTACH_FAILED, f"{ear} voice: {_describe(exc)}")
            return
        self._safely(lambda: voice.set_endpoint(endpoint), APP_EAR_ATTACH_FAILED, f"{ear} voice")

    def _fold_endpoint(self, endpoint: Any) -> None:
        """Record what the endpoint says about ITSELF at the moment it attaches.

        An endpoint that cannot import its driver, cannot find a device, or
        cannot open a stream still *attaches* — it degrades rather than
        raising, exactly as this package requires. Without this, ``status()``
        would show an ear called ``host`` with nothing behind it and the
        ledger would say nothing (``CLAUDE.md``: a healthy-looking ``status``
        is not evidence that Gwen heard anything).
        """
        probed = _probe(endpoint) or {}
        for key in ("degradation", "degradation_in", "degradation_out"):
            record = probed.get(key)
            if isinstance(record, dict) and record.get("code"):
                self._record(record.get("code", ""), record.get("reason", ""), source="endpoint")

    def _teardown_ear(self) -> None:
        """Stop the current ear completely. Every failure is recorded, none raised."""
        endpoint, ear = self._ear_endpoint, self._ear_name or ""
        self._ear_name = None
        self._ear_endpoint = None
        self._generation += 1
        voice = self._voice
        if voice is not None:
            # The voice outlives the ear: it is pointed at a NullEndpoint
            # rather than closed, so a reply with no ear attached is still
            # PUBLISHED through the one code path that publishes replies, and
            # the next attach costs a re-point instead of a rebuild.
            self._fold_voice(voice)
            self._safely(
                lambda: voice.set_endpoint(NullEndpoint()),
                APP_EAR_DETACH_FAILED,
                f"{ear} voice",
            )
        if endpoint is None:
            return
        self._safely(endpoint.stop_capture, APP_EAR_DETACH_FAILED, ear)
        self._safely(endpoint.detach, APP_EAR_DETACH_FAILED, ear)
        self._safely(lambda: endpoint.close(1.0), APP_EAR_DETACH_FAILED, ear)

    def _safely(self, call: Callable[[], Any], code: str, what: str) -> bool:
        """Run *call*, recording any failure under *code*. Never raises."""
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - every teardown step is best-effort
            self._record(code, f"{_safe_name(what)}: {_describe(exc)}")
            return False
        return True

    def _attach_default_ear(self) -> None:
        """The host ear — degraded if there is no device — or a recorded stand-in.

        Which one a host should expect, stated because the two look similar in
        the ledger and are not the same fact:

        * **No driver, no device, or a stream that will not open** (no
          ``sounddevice`` on this box, no PortAudio, no microphone): the
          factory still returns a :class:`~embodiment.audio.host.HostEndpoint`
          and ``attach()`` degrades rather than raising. The ear is
          ``host``, ``status()["ear"]["degraded"]`` is ``True``, and the
          endpoint's own code (``audio-host-import-failed``,
          ``audio-host-portaudio-missing``, ``audio-host-no-devices``,
          ``audio-host-open-failed``) is in the ledger. This is deliberate: a
          host ear that is present-but-deaf can be *recovered* without
          restarting the daemon — install the driver or plug the device in,
          then ``POST /api/voice/stop`` and ``/api/voice/start``, which builds
          a fresh endpoint. A :class:`NullEndpoint` could not recover, because
          nothing would ever build a real one again.
        * **The factory itself failed or was never given** (it raised, or
          returned ``None``): there is nothing to recover, so a
          :class:`NullEndpoint` stands in under the ear name ``null`` with
          :data:`APP_NO_ENDPOINT` recorded. The daemon still hears nothing and
          still speaks into the void, which is the same behaviour by a
          different route — hence one code path, not two.

        Either way ``status()["ear"]`` names which (``kind``) and says it is
        degraded, because "a healthy-looking ``status`` is not evidence that
        Gwen heard anything" (``CLAUDE.md``).
        """
        endpoint: Any = None
        if self._endpoint_factory is not None:
            try:
                endpoint = self._endpoint_factory()
            except Exception as exc:  # noqa: BLE001 - no device is a degradation, not a crash
                self._record(APP_NO_ENDPOINT, _describe(exc))
                endpoint = None
        if endpoint is None:
            if self._endpoint_factory is not None:
                pass  # the failure above is already recorded
            self._record(APP_NO_ENDPOINT, "no audio endpoint; the daemon speaks into the void")
            endpoint = NullEndpoint()
            self.attach_ear("null", endpoint)
            return
        self.attach_ear("host", endpoint)

    def _frame_callback(self, generation: int) -> Callable[[bytes], None]:
        """A capture callback bound to ONE ear generation. Never raises at the device."""

        def on_frame(pcm: bytes) -> None:
            if generation != self._generation:
                with self._lock:
                    self._stale_frames += 1
                self._record(
                    APP_FRAME_FROM_STALE_EAR,
                    f"generation {generation} is no longer the ear",
                    once=True,
                )
                return
            with self._lock:
                self._frames_captured += 1
            try:
                self._ears.send_audio(pcm)
            except Exception as exc:  # noqa: BLE001 - the ears client is a seam
                self._record(APP_CAPTURE_FAILED, _describe(exc), once=True)
            else:
                with self._lock:
                    self._frames_forwarded += 1
            self._publish_features("in", self._features_in, pcm)

        return on_frame

    def _publish_features(self, direction: str, extractor: Any, pcm: bytes) -> None:
        try:
            frames = extractor.feed(pcm)
        except Exception as exc:  # noqa: BLE001 - a dashboard trace must not stop the ear
            self._record(APP_FEATURES_FAILED, f"{direction}: {_describe(exc)}", once=True)
            return
        for frame in frames:
            self._publish("features", {"direction": direction, **frame})

    def _drain_out_features(self) -> None:
        """Publish what the voice traced from PLAYED audio since the last pump.

        Through :meth:`~embodiment.voice.Voice.drain_features` only (t12 round
        3), never by slicing ``feature_frames`` from outside: that list is
        appended to under the voice's own lock by its pacing thread, and a
        caller reaching past that lock is a race waiting for a slow pump.
        """
        voice = self._voice
        if voice is None:
            return
        try:
            taken = voice.drain_features(MAX_FEATURE_DRAIN)
        except Exception as exc:  # noqa: BLE001 - the voice is a seam like any other
            self._record(APP_FEATURES_FAILED, f"out: {_describe(exc)}", once=True)
            return
        for frame in taken or ():
            if isinstance(frame, dict):
                self._publish("features", {"direction": "out", **frame})

    # ── clients: published, never read by the turn ───────────────────────

    def attach_client(self, *, remote: bool = False) -> int:
        """One more viewer or browser ear. Information only."""
        with self._lock:
            self._clients += 1
            if remote:
                self._remote_clients += 1
            counts = (self._clients, self._remote_clients)
        self._publish("clients", {"count": counts[0], "remote": counts[1]})
        return counts[0]

    def detach_client(self, *, remote: bool = False) -> int:
        """One fewer viewer. Never negative; information only."""
        with self._lock:
            self._clients = max(0, self._clients - 1)
            if remote:
                self._remote_clients = max(0, self._remote_clients - 1)
            counts = (self._clients, self._remote_clients)
        self._publish("clients", {"count": counts[0], "remote": counts[1]})
        return counts[0]

    # ── the mic ──────────────────────────────────────────────────────────

    def set_mute(self, muted: bool) -> dict[str, Any]:
        """Mute or unmute the capture path (the endpoint enforces it before encode)."""
        endpoint = self._ear_endpoint
        wanted = bool(muted)
        if endpoint is None:
            self._record(APP_NO_ENDPOINT, "mute requested with no ear attached")
            return {"muted": wanted, "ear": self._ear_name, "applied": False}
        applied = self._safely(lambda: endpoint.mute(wanted), APP_CAPTURE_FAILED, "mute")
        self._publish_mic()
        return {"muted": self._muted(), "ear": self._ear_name, "applied": applied}

    def _muted(self) -> bool:
        endpoint = self._ear_endpoint
        if endpoint is None:
            return True
        try:
            return bool(endpoint.muted)
        except Exception as exc:  # noqa: BLE001 - an endpoint probe is not trusted
            self._record(APP_CAPTURE_FAILED, f"muted: {_describe(exc)}")
            return True

    def _publish_mic(self) -> None:
        ear = self._ear_name
        self._publish("mic", {"hot": ear is not None and not self._muted(), "ear": ear})

    # ── the turn ─────────────────────────────────────────────────────────

    def submit_transcript(self, text: object) -> bool:
        """Queue one heard utterance for the turn thread. Never blocks, never raises."""
        if self._closed:
            self._record(APP_CLOSED, "transcript after close")
            return False
        if not isinstance(text, str):
            self._record(APP_TRANSCRIPT_NOT_TEXT, _safe_name(type(text).__name__))
            return False
        try:
            self._turn_queue.put_nowait(text)
        except queue.Full:
            with self._lock:
                self._turns_dropped += 1
            self._record(
                APP_TURN_QUEUE_FULL, f"{self._turn_queue.maxsize} already waiting", once=True
            )
            return False
        return True

    def run_turn(self, text: object) -> TurnResult:
        """ONE spoken turn, start to finish. Never raises; the daemon survives it.

        The ONE code path: the turn thread calls exactly this.
        """
        if self._closed:
            self._record(APP_CLOSED, "run_turn after close")
            return TurnResult(spoken="")
        if not isinstance(text, str):
            self._record(APP_TRANSCRIPT_NOT_TEXT, _safe_name(type(text).__name__))
            return TurnResult(spoken="")
        if not text.strip():
            self._record(APP_TRANSCRIPT_EMPTY, f"{len(text)} character(s), none spoken")
            return TurnResult(spoken="")
        with self._lock:
            self._turns_in_flight += 1
        try:
            return self._run_turn(text)
        except Exception as exc:  # noqa: BLE001 - a turn fault must not kill the daemon
            with self._lock:
                self._turns_failed += 1
            self._record(APP_TURN_FAILED, _describe(exc))
            return TurnResult(spoken="")
        finally:
            with self._lock:
                self._turns_in_flight -= 1

    def _run_turn(self, text: str) -> TurnResult:
        self._publish("turn", {"phase": "heard", "step_count": 0})
        packet, record = perceive(text)
        spoken_in = packet.original if isinstance(packet.original, str) else text
        if getattr(record, "degraded", False):
            self._record("app-perception-degraded", "intake degraded")
        self._publish("transcript", {"role": "user", "text": spoken_in})

        session = self._ensure_session()
        if session is not None:
            self._safely(lambda: session.add_user(spoken_in), APP_TURN_FAILED, "add_user")

        recalled = self._recall(spoken_in)
        window = self._window(session)
        config = TurnConfig(
            role=self._config.role,
            system_prompt=_compose_prompt(self._config.system_prompt, window, recalled),
        )
        self._publish("turn", {"phase": "thinking", "step_count": 0})
        result = run_one_turn(spoken_in, self._complete, tools=self._tools, config=config)
        for degradation in result.degradations:
            self._fold("turn", degradation)
        if session is not None:
            self._safely(
                lambda: session.add_assistant(result.spoken), APP_TURN_FAILED, "add_assistant"
            )
            self._fold_session(session)
        with self._lock:
            self._turns_completed += 1
        self._publish("turn", {"phase": "spoken", "step_count": result.steps})
        self._speak(result.spoken)
        return result

    def _speak(self, spoken: str) -> None:
        voice = self._voice
        if voice is None:
            self._record(APP_NO_ENDPOINT, "nothing to speak through; the reply was not voiced")
            self._publish("reply", {"text": spoken})
            return
        self._safely(lambda: voice.speak(spoken), APP_TURN_FAILED, "speak")
        self._fold_voice(voice)

    def _recall(self, text: str) -> str:
        """The ONE place recall reaches a prompt, bounded by its own deadline."""
        try:
            result = self._memory.recall(
                text,
                deadline=self._config.recall_deadline,
                mode=self._config.recall_mode,
                top_k=self._config.recall_top_k,
                visibility=PRIVATE,
            )
        except Exception as exc:  # noqa: BLE001 - memory is a seam; a turn never waits on it
            self._record("app-recall-failed", _describe(exc))
            return ""
        with self._lock:
            self._recall_calls += 1
            self._recall_mode = getattr(result, "mode", None)
        for degradation in getattr(result, "degradations", ()) or ():
            self._fold("memory", degradation)
        try:
            return render_recalled(getattr(result, "records", []) or [])
        except Exception as exc:  # noqa: BLE001 - a render fault must not lose the turn
            self._record("app-recall-failed", f"render: {_describe(exc)}")
            return ""

    def _window(self, session: Any) -> list[dict[str, str]]:
        if session is None:
            return []
        try:
            messages = session.messages()
        except Exception as exc:  # noqa: BLE001 - the session is a seam
            self._record(APP_TURN_FAILED, f"window: {_describe(exc)}")
            return []
        return [m for m in messages if isinstance(m, dict)][:-1]

    def _ensure_session(self) -> Any:
        with self._lock:
            if self._session is not None:
                return self._session
        try:
            factory = self._session_factory or (
                lambda: Session(self._state, self._memory, added_by=self._config.added_by)
            )
            session = factory()
        except Exception as exc:  # noqa: BLE001 - a turn happens with or without a session
            self._record(APP_TURN_FAILED, f"session: {_describe(exc)}")
            return None
        with self._lock:
            self._session = session
        return session

    def _fold_voice(self, voice: Any) -> None:
        """Record every voice degradation this app has not already recorded."""
        records = list(getattr(voice, "degradations", ()) or ())
        with self._lock:
            start, self._voice_folded = self._voice_folded, len(records)
        for degradation in records[start:]:
            self._fold("voice", degradation)

    def _fold_session(self, session: Any) -> None:
        try:
            records = list(session.degradations)
        except Exception as exc:  # noqa: BLE001 - the session is a seam
            self._record(APP_TURN_FAILED, f"session degradations: {_describe(exc)}")
            return
        with self._lock:
            start, self._session_folded = self._session_folded, len(records)
        for degradation in records[start:]:
            self._fold("session", degradation)

    # ── the threads ──────────────────────────────────────────────────────

    def _start_turn_thread(self) -> None:
        thread = threading.Thread(target=self._turn_worker, name="embodiment-turn", daemon=True)
        self._turn_thread = thread
        thread.start()

    def _turn_worker(self) -> None:
        while not self._worker_stop.is_set():
            try:
                text = self._turn_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as exc:  # noqa: BLE001 - a queue fault must not kill the worker
                self._record(APP_TURN_FAILED, _describe(exc))
                continue
            self.run_turn(text)

    def _stop_turn_thread(self, deadline: float) -> bool:
        thread = self._turn_thread
        if thread is None:
            return True
        thread.join(timeout=max(0.05, deadline))
        return not thread.is_alive()

    def _start_ears_thread(self) -> None:
        thread = threading.Thread(target=self._ears_main, name="embodiment-ears", daemon=True)
        self._ears_thread = thread
        thread.start()

    def _ears_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._ears_loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._listen())
        except Exception as exc:  # noqa: BLE001 - the ear may die; the daemon may not
            self._record(APP_EARS_THREAD_FAILED, _describe(exc))
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001 - a loop that will not close is counted, not raised
                self._record(APP_EARS_THREAD_FAILED, "event loop would not close")
            self._ears_loop = None

    async def _listen(self) -> None:
        try:
            connected = await self._ears.connect()
        except Exception as exc:  # noqa: BLE001 - the client promises False, not an exception
            self._record(APP_EARS_UNAVAILABLE, _describe(exc))
            return
        if self._stopping:
            # A stop that arrived while the handshake was in flight. Without
            # this the thread would go on to serve an event stream nobody is
            # going to close (found by a 2-in-25 flake on close(): the ears
            # step burned its whole slice joining a thread parked in
            # ``events()``), and the real client would leave its WebSocket
            # open behind it.
            await self._shut_ears_down()
            return
        if not connected:
            self._ears_connected = False
            self._record(APP_EARS_UNAVAILABLE, "the gateway did not give us a session")
            self._publish("state", {"component": "ears", "status": "unavailable"})
            return
        self._ears_connected = True
        self._publish("state", {"component": "ears", "status": "listening"})
        try:
            async for event in self._ears.events():
                if self._stopping:
                    break
                self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport fault ends the stream
            self._record(APP_EARS_STREAM_ENDED, _describe(exc))
        finally:
            self._ears_connected = False
            # Whatever ended the stream — a stop, a peer close, a transport
            # fault — the client is closed from inside its own loop, which is
            # the only thread that can close it gracefully.
            await self._shut_ears_down()
        if not self._stopping:
            self._record(APP_EARS_STREAM_ENDED, "the event stream ended; no reconnect in t15")
            self._publish("state", {"component": "ears", "status": "ended"})

    def _on_event(self, event: Any) -> None:
        """Classify ONE server event. Runs on the ears thread; never blocks on a turn."""
        try:
            if isinstance(event, wire.TranscriptionCompleted):
                self.submit_transcript(event.text)
            elif isinstance(event, wire.SpeechStarted):
                self._barge_in()
            elif isinstance(event, wire.ServerError):
                self._record(APP_STT_ERROR, f"code={_safe_name(event.code)}")
            elif isinstance(event, wire.MalformedEvent):
                self._record(APP_STT_FRAME_MALFORMED, "a frame did not decode")
            elif isinstance(event, wire.SessionCreated):
                self._publish("state", {"component": "ears", "status": "session"})
            elif isinstance(event, wire.SessionClosed):
                self._record(APP_EARS_STREAM_ENDED, "the server closed the session")
        except Exception as exc:  # noqa: BLE001 - one bad event never ends the stream
            self._record(APP_EARS_THREAD_FAILED, _describe(exc))

    def _barge_in(self) -> None:
        """Speech onset while we are talking: stop our own speaker. We own barge-in."""
        voice, endpoint = self._voice, self._ear_endpoint
        speaking = bool(getattr(voice, "speaking", False))
        playing = False
        if endpoint is not None:
            try:
                playing = bool(endpoint.playing)
            except Exception as exc:  # noqa: BLE001 - an endpoint probe is not trusted
                self._record(APP_CAPTURE_FAILED, f"playing: {_describe(exc)}")
        if voice is None or not (speaking or playing):
            return
        self._safely(voice.on_speech_started, APP_TURN_FAILED, "barge-in")
        self._fold_voice(voice)

    async def _shut_ears_down(self) -> None:
        """Close the ears from INSIDE their own loop. Idempotent; never raises.

        The ONE place ``ears.close`` is awaited, and it is guarded by an
        event rather than left to whoever gets there first: the listening
        coroutine closes the ear when its own loop ends, and :meth:`_stop_ears`
        schedules this from the outside, so without the guard both could run
        and the loser's coroutine would be destroyed pending — noise that
        looks exactly like a leak when it is not.
        """
        if self._ears_closed.is_set():
            return
        self._ears_closed.set()
        try:
            await self._ears.close(self._config.shutdown_deadline)
        except Exception as exc:  # noqa: BLE001 - the client promises a report, not silence
            self._record(APP_EARS_THREAD_FAILED, f"close: {_describe(exc)}")

    def _stop_ears(self, deadline: float) -> bool:
        """Tell the ear to stop and wait, bounded, for its thread. Never raises.

        Three guards, each for a way this step was measured burning its whole
        slice for nothing (1-2 runs in 40, which is what surfaced as a flaky
        ``close()`` report):

        * a thread that has already finished needs nothing scheduled — the
          old code still built a coroutine and waited on it;
        * a loop that is not running (not started yet, or already stopped)
          never runs what is scheduled on it, so
          ``run_coroutine_threadsafe(...).result()`` waits out its whole
          timeout on a future that cannot resolve. ``is_running`` is checked
          first and the future is cancelled if it does not land; and
        * the wait for that future takes only a SHARE of the slice, so the
          join afterwards still has time to observe the thread finishing —
          the old code could spend the entire slice on the close and then
          report the thread unstopped when it had in fact stopped.
        """
        thread = self._ears_thread
        if thread is None or not thread.is_alive():
            return True
        loop = self._await_ears_loop(deadline)
        if (
            loop is not None
            and loop.is_running()
            and not loop.is_closed()
            and not self._ears_closed.is_set()
        ):
            future = None
            try:
                future = asyncio.run_coroutine_threadsafe(self._shut_ears_down(), loop)
                future.result(timeout=max(0.05, deadline * _EARS_CLOSE_WAIT_SHARE))
            except Exception as exc:  # noqa: BLE001 - a close that will not finish is recorded
                self._record(APP_EARS_THREAD_FAILED, f"close: {_describe(exc)}", once=True)
                if future is not None:
                    future.cancel()
        thread.join(timeout=max(0.05, deadline))
        return not thread.is_alive()

    def _await_ears_loop(self, deadline: float) -> Optional[asyncio.AbstractEventLoop]:
        """The ears loop, waiting a slice of *deadline* for the thread to make one.

        ``start()`` immediately followed by ``close()`` used to find the loop
        absent, skip the close entirely, and then join a thread that was only
        just getting going — so the ear was never told to stop. A bounded
        wait closes that window from this side; :meth:`_listen`'s own
        ``_stopping`` check closes it from the other.
        """
        thread = self._ears_thread
        if thread is None or not thread.is_alive():
            return self._ears_loop
        limit = self._clock() + max(0.05, deadline) * _EARS_LOOP_WAIT_SHARE
        while self._ears_loop is None and self._clock() < limit:
            time.sleep(0.005)
        return self._ears_loop

    # ── the dashboard ────────────────────────────────────────────────────

    def controls(self) -> server_module.Controls:
        """The four control callables the dashboard server binds to."""
        return server_module.Controls(
            start_voice=self._control_start_voice,
            stop_voice=self._control_stop_voice,
            set_mute=self._control_set_mute,
            status=self.status,
        )

    def _control_start_voice(self) -> dict[str, Any]:
        if self._ear_name is not None:
            return {"ear": self._ear_name, "handover": None}
        self._attach_default_ear()
        return {"ear": self._ear_name, "handover": None}

    def _control_stop_voice(self) -> dict[str, Any]:
        handover = self.detach_ear()
        return {"ear": self._ear_name, "handover": handover.to_dict()}

    def _control_set_mute(self, muted: bool) -> dict[str, Any]:
        return self.set_mute(muted)

    def _start_server(self) -> None:
        """Start the dashboard, if one was handed in.

        The server is constructed with ``controls=app.controls()`` (see
        :func:`main`) rather than bound here — :class:`DashboardServer` takes
        its controls at construction and has no rebinding seam.
        """
        server = self._server
        if server is None or not self._config.http_enabled:
            return
        try:
            server.start()
        except Exception as exc:  # noqa: BLE001 - no dashboard is a degradation, not a crash
            self._record(APP_HTTP_UNAVAILABLE, _describe(exc))

    def _stop_server(self, deadline: float) -> bool:
        server = self._server
        if server is None:
            return True
        try:
            server.shutdown(max(0.05, deadline))
        except Exception as exc:  # noqa: BLE001 - a server that will not stop is recorded
            self._record(APP_HTTP_UNAVAILABLE, f"shutdown: {_describe(exc)}")
            return False
        return True

    # ── the rest of shutdown ─────────────────────────────────────────────

    def _close_voice(self, deadline: float) -> bool:
        voice = self._voice
        if voice is None:
            return True
        self._fold_voice(voice)
        return self._safely(lambda: voice.close(max(0.05, deadline)), APP_TURN_FAILED, "voice")

    def _close_session(self, deadline: float) -> bool:
        session = self._session
        if session is None:
            return True
        try:
            report = session.close(self._summarise, deadline=max(0.05, deadline))
        except Exception as exc:  # noqa: BLE001 - the session is a seam
            self._record(APP_TURN_FAILED, f"session close: {_describe(exc)}")
            return False
        for degradation in getattr(report, "degradations", ()) or ():
            self._fold("session", degradation)
        return True

    def _close_memory(self, deadline: float) -> bool:
        try:
            report = self._memory.close(deadline=max(0.05, deadline))
        except Exception as exc:  # noqa: BLE001 - memory is a seam
            self._record("app-memory-close-failed", _describe(exc))
            return False
        for degradation in getattr(report, "degradations", ()) or ():
            self._fold("memory", degradation)
        return not getattr(report, "unconfirmed", ())

    def _close_bus(self, deadline: float) -> bool:
        try:
            self._bus.close(max(0.05, deadline))
        except Exception:  # noqa: BLE001 - the bus is closing; there is nowhere left to publish
            with self._lock:
                self._publish_errors += 1
            return False
        return True

    def _log(self, event: str, **fields: Any) -> None:
        """One operational-log line. Never carries transcript text."""
        try:
            self._state.operational_log.write(event, **fields)
        except Exception:  # noqa: BLE001 - the log promises never to raise; count anyway
            with self._lock:
                self._ledger_errors += 1

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """A JSON-safe snapshot of every part. Never raises; never carries speech."""
        try:
            return self._status()
        except Exception as exc:  # noqa: BLE001 - a status probe must never raise
            self._record("app-status-failed", _describe(exc))
            return {"running": False, "detail": "status probe failed"}

    def _status(self) -> dict[str, Any]:
        with self._lock:
            counts = dict(self._degradation_counts)
            clients = {"count": self._clients, "remote": self._remote_clients}
            turns = {
                "completed": self._turns_completed,
                "in_flight": self._turns_in_flight,
                "dropped": self._turns_dropped,
                "failed": self._turns_failed,
                "queued": self._turn_queue.qsize(),
            }
            audio = {
                "frames_captured": self._frames_captured,
                "frames_forwarded": self._frames_forwarded,
                "frames_from_stale_ear": self._stale_frames,
            }
            recall_mode = self._recall_mode
            recall_calls = self._recall_calls
            publish_errors = self._publish_errors
            ledger_errors = self._ledger_errors
            session = self._session
        return {
            "running": self._started and not self._closed,
            "closed": self._closed,
            # The browser-ear contract t18 reads. Fixed at False/None in v1 —
            # this daemon starts no RemoteEndpoint — but present, so the web
            # app branches on a value rather than on a missing key.
            "realtime_ear_enabled": bool(self._config.realtime_ear_enabled),
            "realtime_ws_url": self._config.realtime_ws_url or None,
            "ear": {
                "active": self._ear_name,
                "kind": (
                    _safe_name(type(self._ear_endpoint).__name__)
                    if self._ear_endpoint is not None
                    else None
                ),
                "degraded": _endpoint_degraded(self._ear_endpoint),
                "generation": self._generation,
                "handovers": self._handovers,
                "refusals": self._refusals,
                "preempt_policy": self._config.preempt_ear,
                "muted": self._muted(),
                "endpoint": _probe(self._ear_endpoint),
            },
            "clients": clients,
            "turns": turns,
            "audio": audio,
            "recall": {
                "mode": recall_mode,
                "calls": recall_calls,
                "deadline_s": self._config.recall_deadline,
                "configured_mode": self._config.recall_mode,
                "semantic": _semantic_available(self._memory),
            },
            "memory": _memory_status(self._memory),
            "session": _session_status(session),
            "ears": _probe(self._ears),
            "voice": _probe(self._voice),
            "http": _probe(self._server),
            "bus": {
                "degradation_counts": dict(getattr(self._bus, "degradation_counts", {}) or {}),
                "hook_errors": int(getattr(self._bus, "hook_errors", 0) or 0),
            },
            "state": self._state.status(),
            "degradations": counts,
            "publish_errors": publish_errors,
            "ledger_errors": ledger_errors,
        }


# ── module-level helpers ─────────────────────────────────────────────────────


def _describe(exc: BaseException) -> str:
    """The ONE exception renderer: a class name, never a message."""
    return safe_reason.describe_exception(exc)


def _as_seam(complete: Callable[..., Any]) -> Callable[..., Any]:
    """Accept both seam shapes: ``seam(messages, tools=...)`` and ``f(messages)``."""

    def seam(messages: list[dict[str, Any]], tools: Any = None) -> Any:
        try:
            return complete(messages, tools=tools)
        except TypeError:
            return complete(messages)

    return seam


def _compose_prompt(base: str, window: list[dict[str, str]], recalled: str) -> str:
    """The system prompt for ONE turn: the base, the window, then recalled memory.

    Recall enters here and nowhere else — :func:`embodiment.memory.render_recalled`
    has already attributed and fenced it as data. It never enters the user text,
    because that text is the caller's own words, verbatim (the verbatim invariant).
    """
    parts = [base]
    if window:
        lines = [WINDOW_HEADER]
        for message in window:
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            lines.append(f"{role}: {content}")
        parts.append("\n".join(lines))
    if recalled:
        parts.append(recalled)
    return "\n\n".join(parts)


def _probe(subject: Any) -> Optional[dict[str, Any]]:
    """``subject.status()`` if it has one, else ``None``. Never raises."""
    if subject is None:
        return None
    try:
        probe = getattr(subject, "status", None)
        if not callable(probe):
            return None
        result = probe()
    except Exception:  # noqa: BLE001 - a sub-status that fails is reported as unavailable
        return {"unavailable": True}
    return result if isinstance(result, dict) else {"unavailable": True}


def _memory_status(memory: Any) -> dict[str, Any]:
    """The store counters ``CLAUDE.md`` requires a host to be able to see.

    ``store_root_is_symlink`` is a BOOL and the rest are counts, so the two
    are coerced separately: reporting a planted symlink as the integer ``1``
    (or, worse, as ``None`` because it failed an ``isinstance(int)`` check)
    would be a tampered store reported as an ordinary one.
    """
    counters = (
        "store_permission_failures",
        "store_symlinks_skipped",
        "store_non_files_skipped",
        "pending",
        "abandoned_dropped",
    )
    out: dict[str, Any] = {}
    for name in counters:
        try:
            value = getattr(memory, name, None)
        except Exception:  # noqa: BLE001 - a counter probe is not trusted either
            value = None
        out[name] = value if isinstance(value, int) and not isinstance(value, bool) else None
    try:
        flag = getattr(memory, "store_root_is_symlink", None)
    except Exception:  # noqa: BLE001 - a probe that fails cannot clear the store
        flag = None
    out["store_root_is_symlink"] = flag if isinstance(flag, bool) else None
    try:
        out["scope"] = str(getattr(memory, "scope", ""))
        out["data_dir"] = str(getattr(memory, "data_dir", ""))
    except Exception:  # noqa: BLE001 - a path probe that fails is reported as unknown
        out["scope"] = out.get("scope", "")
        out["data_dir"] = ""
    return out


def _endpoint_degraded(endpoint: Any) -> Optional[bool]:
    """Whether the attached endpoint reports ANY degradation about itself."""
    if endpoint is None:
        return None
    probed = _probe(endpoint) or {}
    return any(
        isinstance(probed.get(key), dict) and probed[key].get("code")
        for key in ("degradation", "degradation_in", "degradation_out")
    )


def _semantic_available(memory: Any) -> bool:
    """Whether the LAST recall actually ran semantic.

    Not a claim about what the rig could do: the embedder is down on this rig,
    eidetic falls back to lexical SILENTLY (``CLAUDE.md``, C3), and this field
    exists so a host reads the mode that was actually in effect rather than
    the one that was asked for.
    """
    try:
        return bool(getattr(memory, "last_recall_mode", None) == "semantic")
    except Exception:  # noqa: BLE001 - a probe that fails means "we cannot claim it"
        return False


def _session_status(session: Any) -> Optional[dict[str, Any]]:
    if session is None:
        return None
    try:
        return {
            "turns_seen": int(session.turns_seen),
            "closed": bool(session.closed),
            "degradation_counts": dict(session.degradation_counts),
        }
    except Exception:  # noqa: BLE001 - a session probe that fails is reported as unavailable
        return {"unavailable": True}


def _default_endpoint_factory() -> Callable[[], Any]:
    """The host mic+speaker, imported lazily so the optional extra stays optional."""

    def factory() -> Any:
        from embodiment.audio.host import HostEndpoint

        return HostEndpoint()

    return factory


def main() -> DaemonApp:
    """Build the daemon from the environment. Zero arguments; never raises.

    This is :data:`embodiment.daemon.lifecycle.DEFAULT_TARGET`'s factory: the
    daemon process imports it, calls it, and hands the result to
    :class:`~embodiment.daemon.lifecycle.DaemonRunner`. Every part that cannot
    be built degrades to a working stand-in and is recorded, because a daemon
    that refuses to start is a daemon whose host learns nothing.
    """
    state = DaemonState(resolve_state_dir())
    realtime = RealtimeConfig.from_env()
    secret = guard_module.load_or_create_install_secret(state.dir)
    if secret.code:
        state.ledger.append(secret.code, _safe_reason_text(secret.detail))
    bus = Bus(redact=tuple(s for s in (realtime.api_key, secret.secret) if s))
    config = AppConfig(gateway_url=realtime.gateway_url, api_key=realtime.api_key)

    data_dir = Path(state.dir) / "memory" if state.dir is not None else Path(".")
    memory = RoomMemory(data_dir, scope=config.memory_scope, added_by=config.added_by)
    tools = ToolRegistry()

    def seam(messages: list[dict[str, Any]], tools: Any = None) -> ModelResponse:
        return http_complete(
            messages,
            tools=tools,
            gateway_url=config.gateway_url,
            api_key=config.api_key,
            role=config.role,
            deadline=config.completion_deadline,
        )

    def voice_factory(endpoint: Any) -> Voice:
        return Voice(
            endpoint=endpoint,
            config=VoiceConfig(gateway_url=config.gateway_url, api_key=config.api_key),
            bus=bus,
            features=FeatureExtractor(),
            synthesize=http_synthesize,
        )

    app = DaemonApp(
        config=config,
        state=state,
        bus=bus,
        memory=memory,
        complete=bind_tools(seam, tools),
        tools=tools,
        ears=RealtimeEars(realtime),
        endpoint_factory=_default_endpoint_factory(),
        voice_factory=voice_factory,
    )
    try:
        app._server = server_module.DashboardServer(
            config=server_module.ServerConfig(
                bind=config.bind,
                port=config.port,
                bind_public=config.bind_public,
                redact=tuple(s for s in (config.api_key, secret.secret) if s),
            ),
            guard=guard_module.Guard(
                guard_module.GuardConfig(install_secret=secret.secret),
            ),
            bus=bus,
            controls=app.controls(),
        )
    except Exception as exc:  # noqa: BLE001 - no dashboard is a degradation, not a crash
        app._record(APP_BOOTSTRAP_DEGRADED, f"dashboard: {_describe(exc)}")
    return app
