"""The browser ear: a ``websockets`` server implementing ``AudioEndpoint`` (plan task ``t14``).

:class:`RemoteEndpoint` is the inbound half of the lobes ``/v1/realtime`` wire:
where :mod:`embodiment.realtime.client` DIALS a lobes gateway as a client, this
module LISTENS as a server, so a browser tab (or, later, a robot relay
speaking the same wire) can be Gwen's ears and mouth. It satisfies the same
:class:`~embodiment.audio.endpoint.AudioEndpoint` protocol
:mod:`embodiment.audio.host` does, so the daemon composing endpoints can swap
one for the other without touching anything above this seam. It also
duck-types the protocol's read-only ``sample_rate`` member added on
``realtime/t7`` (840b751), reporting a fixed 24000 — deliberately NOT merged
into this branch for that, since the protocol's own member test binds only
once both branches land on ``phase-b``.

v1 ships no robot support
--------------------------
Only a browser is an exercised, supported consumer today — see the new
section this task adds to ``README.md``. What ships is the *seam*
(``AudioEndpoint``, spoken over the lobes wire, inbound): a robot relay is a
future implementation of that same seam, arrived at by writing a new small
adapter, never a reason to touch the daemon that composes endpoints.

Why this module cannot literally reuse ``wire.py``'s codec
------------------------------------------------------------
:mod:`embodiment.realtime.wire` is a CLIENT-role module: it *encodes* the two
client→server events (``input_audio_buffer.append``, ``session.update``) and
*decodes* server→client events. This module needs the mirror image — decode
the client events a browser sends, encode the server events a browser
expects back (``session.created``, ``session.updated``,
``response.audio.delta``) — and ``wire.py`` has no decoder or encoder for
either, because nothing on the client side of this package ever needed one.
Rather than invent a second, independent vocabulary, every constant this
module's wire shapes are built from — the event type strings
(:data:`~embodiment.realtime.wire.APPEND_EVENT_TYPE`,
:data:`~embodiment.realtime.wire.SESSION_UPDATE_EVENT_TYPE`), the declared
format (:data:`~embodiment.realtime.wire.AUDIO_FORMAT`,
:data:`~embodiment.realtime.wire.INPUT_SAMPLE_RATE`,
:data:`~embodiment.realtime.wire.CHANNELS`,
:data:`~embodiment.realtime.wire.TURN_DETECTION`,
:data:`~embodiment.realtime.wire.AEC_MODE`,
:data:`~embodiment.realtime.wire.LANGUAGE`) — is imported FROM ``wire.py``,
never restated. Only the JSON envelope plumbing around those constants is new,
and it is new because the shape it needs did not exist anywhere in this
package before this task. (Flagged in this task's delivery report as a
brief/reality gap: "reuse wire.py; do not write a second codec" could not be
followed to the letter, because the codec this task needs is not the codec
``wire.py`` owns.)

Authentication: the first message, never the URL (round 2 correction)
-------------------------------------------------------------------------
A browser's native ``WebSocket`` constructor cannot set a custom header, so
the bearer-header scheme :mod:`embodiment.realtime.client` uses when DIALING
OUT is not available to a page dialing IN — but a query parameter is the
WRONG substitute: a connect URL lands in a reverse proxy's and
``cloudflared``'s access logs, in browser history, and in a ``Referer``
header on the next same-origin navigation, none of which this repo's privacy
constraints permit for a credential (and it is exactly why ``t16``'s daemon
guard takes the install secret as a bearer header rather than a query
parameter). So the handshake carries no secret at all: ``process_request``
only enforces the pre-handshake fail-closed case (no secret configured at
all — :data:`DEGRADED_NO_SECRET`, HTTP ``503``) and the one-peer-at-a-time
limit. Once the WebSocket handshake completes, :meth:`RemoteEndpoint`
accepts exactly ONE JSON message before anything else is processed:
``{"type": "auth", "secret": "..."}``, read under a bounded deadline
(:attr:`RemoteEndpointConfig.auth_deadline`, default 5 s). A wrong secret, a
missing one, a malformed first message, a first message of any other
``type``, or nothing arriving inside the deadline, all close the socket with
WebSocket close code ``1008`` (policy violation) and record exactly one
:data:`DEGRADED_UNAUTHORIZED` — never with the supplied value in the reason,
which stays a static phrase (wave 1 lesson 5), and never having accepted a
single audio frame: :meth:`_on_client_message` is not reachable until
:meth:`_authenticate` returns ``True``. The secret comparison is
constant-time (:func:`hmac.compare_digest`). A ``?secret=...`` query
parameter, if a misconfigured client still sends one, is never read for
authentication — it is simply ignored — but its presence IS counted
(:data:`DEGRADED_SECRET_IN_URL`, ``secret_in_url_count`` in :meth:`status`)
so a client still doing it the old, log-leaking way is visible to a host
without being treated as a security event on its own.

One connection at a time
-------------------------
A second browser dialing in while one is already attached is refused
(``503``) rather than silently multiplexed: ``play()`` has exactly one peer
to address, and multiplexing playback to N tabs is a feature nobody asked
this task to build. This is a v1 engineering scope choice, not the "one
active ear at a time" daemon policy :mod:`embodiment.audio.endpoint`
describes (that policy is about which *endpoint* is attached; this is about
how many sockets one endpoint instance accepts).

One OS thread runs both directions
-------------------------------------
Unlike :class:`~embodiment.audio.host.HostEndpoint` (a real capture thread and
a real writer thread, because PortAudio's own API demands two blocking
streams), this module needs only ONE thread: an ``asyncio`` event loop, since
both directions here are already non-blocking I/O over the same socket. That
loop runs the ``websockets`` server, a per-connection reader, and one
playback sender task. :meth:`RemoteEndpoint.close`'s
:class:`~embodiment.audio.endpoint.EndpointCloseReport` therefore reports the
SAME observed thread-join outcome in both
``capture_thread_stopped``/``writer_thread_stopped`` — an honest
simplification stated here rather than left to look like two independently
verified threads.

Never raises, never blocks, always records (C3 / wave 1 lessons 2, 3, 6)
----------------------------------------------------------------------------
Every public method is synchronous, non-blocking and never raises — the same
footing :mod:`embodiment.audio.endpoint` documents for every implementation.
:meth:`play` enqueues into a byte-bounded FIFO (:data:`_PLAYBACK_BUFFER_SECONDS`,
the same judgement call and rationale as ``host.py``'s) and refuses only the
NEW chunk on overflow, counted under :data:`DEGRADED_PLAYBACK_OVERFLOW`.
:meth:`stop_playback` cuts by clearing the queue and tracking the one chunk
genuinely in flight separately (mirroring ``host.py``'s
``_playback_active_remaining_bytes`` split), so at most one
:data:`_WRITE_SLICE_MS`-sized slice can still be crossing the wire after a
barge-in request. Mute is enforced in the one place a decoded frame can reach
a caller's callback — before it is ever handed to ``on_frame`` — never as a
filter applied afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from embodiment.audio.endpoint import (
    CHANNELS,
    SAMPLE_RATE_HZ,
    SAMPLE_WIDTH_BYTES,
    EndpointCloseReport,
    EndpointDegradation,
    FrameCallback,
)
from embodiment.realtime import wire

__all__ = [
    "DEGRADED_TRANSPORT_MISSING",
    "DEGRADED_BIND_FAILED",
    "DEGRADED_START_TIMEOUT",
    "DEGRADED_NO_SECRET",
    "DEGRADED_UNAUTHORIZED",
    "DEGRADED_SECRET_IN_URL",
    "DEGRADED_PLAYBACK_OVERFLOW",
    "AUTH_EVENT_TYPE",
    "RemoteEndpointConfig",
    "RemoteEndpoint",
]

#: ``websockets`` could not be imported (an approved but optional-at-runtime
#: transport — see the lazy import inside :meth:`RemoteEndpoint.attach`).
DEGRADED_TRANSPORT_MISSING = "audio-remote-transport-missing"
#: The listening socket itself could not be opened (port in use, no
#: permission, an unreachable host).
DEGRADED_BIND_FAILED = "audio-remote-bind-failed"
#: The server thread did not confirm it was listening inside its start
#: deadline.
DEGRADED_START_TIMEOUT = "audio-remote-start-timeout"
#: Constructed with an empty secret. Every connection is refused (fail
#: closed), never accepted unauthenticated.
DEGRADED_NO_SECRET = "audio-remote-no-secret-configured"  # nosec B105 - a code, not a password
#: The first message after the handshake was missing, malformed, the wrong
#: ``type``, or carried a secret that did not match — or the deadline for it
#: elapsed with nothing arriving. One code for all four: the host-visible
#: fact is "this peer never authenticated", not which sub-case it was.
DEGRADED_UNAUTHORIZED = "audio-remote-unauthorized"
#: A connecting client sent ``?secret=...`` on the connect URL. Never read
#: for authentication (round 2: a URL is the wrong place for a credential —
#: see the module docstring) but counted so a client still doing this the
#: log-leaking way is visible.
DEGRADED_SECRET_IN_URL = "audio-remote-secret-in-url"  # nosec B105 - a code, not a password
#: A ``play()`` chunk was refused because the playback buffer is full.
DEGRADED_PLAYBACK_OVERFLOW = "audio-remote-playback-overflow"

#: The ``type`` of the one JSON message :meth:`RemoteEndpoint._authenticate`
#: accepts, and the only message processed before it returns ``True``.
AUTH_EVENT_TYPE = "auth"

#: The query parameter name a misconfigured client might still carry a
#: secret in. Never read for authentication (see module docstring) — its
#: presence is only ever counted, under :data:`DEGRADED_SECRET_IN_URL`.
SECRET_QUERY_PARAM = "secret"  # nosec B105 - a parameter name, not a password

#: How long :meth:`RemoteEndpoint._authenticate` waits, after the WebSocket
#: handshake completes, for the one ``{"type": "auth", ...}`` message before
#: giving up and refusing the connection. A judgement call: generous for a
#: browser tab that dials in and immediately sends its one auth frame, small
#: enough that a connection that never authenticates is not held open
#: indefinitely.
_DEFAULT_AUTH_DEADLINE_S = 5.0

#: How long the WebSocket opening handshake itself (from the TCP accept
#: through the ``101`` response) is allowed to take — explicitly passed to
#: ``serve()`` as ``open_timeout`` rather than left at the library's own
#: unstated default (this repo's own lesson: a clock nobody named becomes the
#: measurement), and — round 5 — sized against what it actually bounds
#: rather than inherited from that library default: an HTTP upgrade over a
#: LAN or a tailnet is ONE round trip, not the multi-hop, possibly-congested
#: path the library's 10 s assumes nothing about. 3.0 s is generous for one
#: round trip on a healthy local link and small enough that an aborted
#: handshake (round 4 finding 1) only ever costs a later peer a few seconds,
#: not the better part of a human breath. Still a per-endpoint config field:
#: an operator on a slower link (a real WAN hop, not the LAN/tailnet this
#: default assumes) raises it explicitly rather than this default silently
#: covering for them.
_DEFAULT_HANDSHAKE_OPEN_TIMEOUT_S = 3.0

#: Round 4 finding 1's backstop. ``process_response`` (see
#: :meth:`RemoteEndpoint._process_response`) releases a pending claim
#: IMMEDIATELY for the common case — the peer's disconnect was already
#: noticed by the time that hook runs. It cannot cover every case: a peer
#: that disconnects in the narrow window between that check and the
#: server's actual attempt to WRITE the ``101`` response fails OUTSIDE any
#: hook this module can register, landing in the ``websockets`` library's
#: own bare aborted-handshake path with no callback at all (confirmed by a
#: reproduction: forcing an RST immediately after sending a well-formed
#: upgrade request left the claim held with ``process_response`` never
#: firing). So a pending claim older than the handshake's own bound is ALSO
#: treated as abandoned by the next ``process_request`` call — this margin
#: is the slack added on top of :data:`_DEFAULT_HANDSHAKE_OPEN_TIMEOUT_S`,
#: sized (round 5) for the library's own bookkeeping between "handshake
#: failed" and this process's next ``process_request`` call landing, not for
#: network latency (that is what the timeout above already bounds) — 0.5 s
#: is comfortably more than that bookkeeping needs on a healthy host, small
#: enough that the total worst case (3.5 s) stays a handful of seconds, not
#: the 11 s the pre-round-5 default (10 s + 1 s) cost every legitimate peer
#: behind one aborted handshake.
_PENDING_CLAIM_MARGIN_S = 0.5

#: The rate :attr:`RemoteEndpoint.sample_rate` reports — what
#: :meth:`AudioEndpoint.start_capture`'s ``on_frame`` callback actually
#: receives from THIS endpoint. Unlike :class:`~embodiment.audio.host.HostEndpoint`
#: (which resamples from whatever rate the device negotiates and reports that
#: chosen rate), this endpoint never resamples: a browser encodes
#: ``input_audio_buffer.append`` at the fixed wire contract rate
#: (:data:`~embodiment.realtime.wire.INPUT_SAMPLE_RATE`, itself
#: :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ`), and what
#: :meth:`_on_append` hands ``on_frame`` is that base64 payload decoded
#: byte-for-byte — so the delivered rate IS the wire rate, always, not a
#: measurement.
_REMOTE_SAMPLE_RATE_HZ = SAMPLE_RATE_HZ

#: How long :meth:`RemoteEndpoint.attach` waits for the server thread to
#: confirm it is listening (or has failed) before giving up and recording
#: :data:`DEGRADED_START_TIMEOUT`. A judgement call: generous for a bind that
#: should be near-instant on a healthy host, small enough that a genuinely
#: wedged start is noticed inside one human breath.
_DEFAULT_START_DEADLINE_S = 5.0

#: The default :meth:`RemoteEndpoint.detach` deadline — ``detach`` has no
#: caller-supplied deadline in the Protocol, unlike ``close``. A judgement
#: call, generous enough that a live send in flight finishes.
_DEFAULT_DETACH_DEADLINE_S = 5.0

#: Seconds of 24 kHz pcm16 audio the playback FIFO holds before a NEW
#: ``play()`` chunk is refused. The same judgement call and rationale as
#: :data:`embodiment.audio.host._PLAYBACK_BUFFER_SECONDS`: generous enough
#: that no realistic reply should ever hit it (~5.76 MB), small enough that a
#: stuck peer cannot grow this module's memory without bound.
_PLAYBACK_BUFFER_SECONDS = 120.0

#: The sender never hands the socket more than this much audio in one
#: outbound frame — what makes :meth:`RemoteEndpoint.stop_playback` actually
#: cut rather than merely stop queueing more (mirrors
#: :data:`embodiment.audio.host._WRITE_SLICE_MS`). 20 ms, the low end of the
#: brief's stated 20-40 ms range.
_WRITE_SLICE_MS = 20
_SLICE_SAMPLES = max(1, int(SAMPLE_RATE_HZ * _WRITE_SLICE_MS / 1000.0))
_SLICE_BYTES = _SLICE_SAMPLES * SAMPLE_WIDTH_BYTES

#: How long the sender loop's wait for new playback data is bounded to before
#: re-checking state, even with nothing to wake it — the same discipline as
#: ``host.py``'s ``_POLL_INTERVAL_S``, so a lost wakeup cannot park the
#: sender forever.
_SENDER_POLL_S = 0.2

#: How long a peer-less sender pauses between attempts to find a connection
#: before retrying, once audio is already queued and waiting for a browser to
#: attach.
_NO_PEER_RETRY_S = 0.05

#: How many ``_enqueue_control`` sends (currently: one per ``session.update``
#: echo) may be in flight at once (round 4 finding 3). Derived from the
#: quantity it bounds, not picked independently (wave 1 lesson 1): a control
#: reply is ONE small JSON frame, so a healthy peer never has more than one
#: or two outstanding at a time — this cap only ever engages against a burst
#: aimed at a peer that never reads, where an unbounded fire-and-forget task
#: per message would otherwise pile up without limit. A NEW send past the cap
#: is dropped and counted (``control_sends_dropped``), never queued.
_MAX_INFLIGHT_CONTROL_SENDS = 4


def _new_id(prefix: str) -> str:
    """A short, non-secret identifier. Never derived from anything client-supplied."""

    return f"{prefix}_{secrets.token_hex(12)}"


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _slices(data: bytes, size: int) -> list[bytes]:
    """*data* cut into ``size``-byte pieces, the last one possibly shorter."""
    if size <= 0:
        return [data]
    return [data[i : i + size] for i in range(0, len(data), size)]


@dataclass(frozen=True)
class RemoteEndpointConfig:
    """What one :class:`RemoteEndpoint` listens as.

    ``secret`` is required to actually accept a connection — an empty value
    is a valid, honest construction (a caller building the object before a
    secret is provisioned) but leaves the endpoint permanently refusing
    (:data:`DEGRADED_NO_SECRET`), never accepting unauthenticated.
    """

    secret: str = ""
    host: str = "127.0.0.1"
    port: int = 8765
    start_deadline: float = _DEFAULT_START_DEADLINE_S
    auth_deadline: float = _DEFAULT_AUTH_DEADLINE_S
    handshake_open_timeout: float = _DEFAULT_HANDSHAKE_OPEN_TIMEOUT_S
    ping_interval: float = 20.0
    ping_timeout: float = 20.0
    close_handshake_timeout: float = 5.0


class RemoteEndpoint:
    """The lobes ``/v1/realtime`` wire, served inbound, satisfying ``AudioEndpoint``.

    Args:
        secret: the install secret (or a per-endpoint secret) a connecting
            browser must present as ``?secret=...`` on the connect URL.
        host: interface to bind. Defaults to loopback — this endpoint is
            reached through whatever tunnel/reverse-proxy terminates TLS and
            enforces the operator's allow-list; loopback is not itself
            authentication (see ``CLAUDE.md``), which is exactly why
            ``secret`` is mandatory rather than optional.
        port: listening port. 8765 is an arbitrary default (a judgement call,
            unmeasured against any real deployment) meant to be overridden by
            whatever composes this endpoint.
        clock: zero-argument callable returning a monotonic float, used ONLY
            for the pending-claim staleness check (round 6). Defaults to
            :func:`time.monotonic`; tests inject a fake, manually-advanced
            clock so the staleness backstop's timing logic can be proven
            without any real sleeping — a real socket can still be aborted
            for real, while whether the resulting claim reads as "stale" is
            driven deterministically rather than by racing a wall clock
            against a loaded CI box.
    """

    def __init__(
        self,
        *,
        secret: str = "",
        host: str = "127.0.0.1",
        port: int = 8765,
        start_deadline: float = _DEFAULT_START_DEADLINE_S,
        auth_deadline: float = _DEFAULT_AUTH_DEADLINE_S,
        handshake_open_timeout: float = _DEFAULT_HANDSHAKE_OPEN_TIMEOUT_S,
        ping_interval: float = 20.0,
        ping_timeout: float = 20.0,
        close_handshake_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = RemoteEndpointConfig(
            secret=secret or "",
            host=host,
            port=port,
            start_deadline=start_deadline,
            auth_deadline=auth_deadline,
            handshake_open_timeout=handshake_open_timeout,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
            close_handshake_timeout=close_handshake_timeout,
        )

        self._clock = clock
        self._lock = threading.Lock()
        self._attached = False
        self._closed = False
        self._capturing = False
        self._muted = False
        self._on_frame: FrameCallback | None = None

        self._thread: threading.Thread | None = None
        self._loop: Any = None
        self._stop_event: Any = None
        self._playback_wake: Any = None
        self._server: Any = None
        self._bound_port: int = port
        self._connection: Any = None
        self._connection_pending = False
        #: The exact connection object that currently holds the pending
        #: claim — round 4 finding 1's ownership guard, so a later
        #: connection's own handshake outcome can never release a DIFFERENT
        #: connection's still-legitimate claim.
        self._pending_connection: Any = None
        #: When the current claim was made, or ``None`` once a REAL running
        #: handler has confirmed it (see :meth:`_handle_connection`'s entry) —
        #: the staleness backstop only ever fires while this is set.
        self._connection_pending_since: float | None = None
        self._connected = False

        self._session_id = ""
        self._response_id = _new_id("resp")

        self._degradation: EndpointDegradation | None = None
        self._last_close_report: EndpointCloseReport | None = None

        self._playback_chunks: "deque[bytes]" = deque()
        self._playback_queued_bytes = 0
        self._playback_active_bytes = 0
        self._playback_sent_bytes = 0
        self._playback_stop_discarded_total = 0
        self._playback_overflow_count = 0
        self._playing = False

        self._frames_received = 0
        self._frames_muted_dropped = 0
        self._frames_malformed = 0
        self._unknown_frames = 0
        self._session_updates_received = 0
        self._callback_errors = 0
        self._send_errors = 0
        self._connection_drop_count = 0
        self._unauthorized_count = 0
        self._rejected_busy_count = 0
        self._secret_in_url_count = 0
        self._path_parse_errors = 0
        self._handshake_aborted_count = 0
        self._server_thread_fault_count = 0
        self._control_sends_dropped = 0
        self._control_sends_inflight = 0
        self._bind_error: EndpointDegradation | None = None

    # -- lifecycle -----------------------------------------------------

    def attach(self) -> None:
        if self._attached:
            return
        self._closed = False
        self._bind_error = None
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(ready,),
            name="embodiment-audio-remote",
            daemon=True,
        )
        self._thread.start()
        confirmed = ready.wait(timeout=max(0.0, self.config.start_deadline))
        if not confirmed:
            self._degradation = EndpointDegradation(
                DEGRADED_START_TIMEOUT, "server did not confirm listening in time"
            )
            return
        if self._bind_error is not None:
            self._degradation = self._bind_error
            return
        if not self.config.secret:
            self._degradation = EndpointDegradation(
                DEGRADED_NO_SECRET, "no secret configured; every connection is refused"
            )
        self._attached = True

    def detach(self) -> None:
        self._shutdown(_DEFAULT_DETACH_DEADLINE_S)

    def close(self, deadline: float) -> EndpointCloseReport:
        return self._shutdown(deadline)

    def _shutdown(self, deadline: float) -> EndpointCloseReport:
        deadline = max(0.0, float(deadline))
        start = time.monotonic()
        if self._closed:
            return self._last_close_report or EndpointCloseReport(True, True, 0, 0.0)

        samples_discarded = self.stop_playback()

        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None:
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass  # loop already stopped/closing

        thread = self._thread
        stopped = True
        if thread is not None:
            remaining = max(0.0, deadline - (time.monotonic() - start))
            thread.join(timeout=remaining)
            stopped = not thread.is_alive()

        self._attached = False
        self._closed = True
        self._connected = False
        elapsed = time.monotonic() - start
        report = EndpointCloseReport(
            capture_thread_stopped=stopped,
            writer_thread_stopped=stopped,
            samples_discarded=samples_discarded,
            elapsed_s=elapsed,
        )
        self._last_close_report = report
        return report

    # -- the server thread -----------------------------------------------

    def _run(self, ready: threading.Event) -> None:
        try:
            asyncio.run(self._serve(ready))
        except Exception as exc:  # noqa: BLE001 - the thread's top: record, never raise out
            # Round 4 finding 2: EVERY fault out of _serve is counted, not
            # just the first — a second fault after an earlier bind failure
            # (e.g. one raised during that failure's own teardown) used to
            # leave no trace at all, because the old guard only ever wrote
            # the FIRST EndpointDegradation and counted nothing.
            with self._lock:
                self._server_thread_fault_count += 1
            if self._bind_error is None:
                self._bind_error = EndpointDegradation(
                    DEGRADED_BIND_FAILED, f"{type(exc).__name__}: server thread raised"
                )
            ready.set()

    async def _serve(self, ready: threading.Event) -> None:

        try:
            from websockets.asyncio.server import serve
        except ImportError as exc:
            self._bind_error = EndpointDegradation(
                DEGRADED_TRANSPORT_MISSING, f"{type(exc).__name__}: websockets not installed"
            )
            ready.set()
            return

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._playback_wake = asyncio.Event()

        try:
            server = await serve(
                self._handle_connection,
                self.config.host,
                self.config.port,
                process_request=self._process_request,
                process_response=self._process_response,
                open_timeout=self.config.handshake_open_timeout,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.ping_timeout,
                close_timeout=self.config.close_handshake_timeout,
            )
        except OSError as exc:
            self._bind_error = EndpointDegradation(
                DEGRADED_BIND_FAILED, f"{type(exc).__name__}: could not bind listening socket"
            )
            ready.set()
            return

        self._server = server
        try:
            self._bound_port = server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        except (AttributeError, IndexError, OSError):
            self._bound_port = self.config.port
        sender = asyncio.get_running_loop().create_task(self._sender_loop())
        ready.set()
        try:
            await self._stop_event.wait()
        finally:
            sender.cancel()
            try:
                await sender
            except asyncio.CancelledError:
                pass  # the expected outcome of cancelling it above
            except Exception:  # noqa: BLE001 - an unexpected teardown fault, still recorded
                with self._lock:
                    self._send_errors += 1
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=5.0)
            except asyncio.TimeoutError:
                with self._lock:
                    self._send_errors += 1
            except Exception:  # noqa: BLE001 - best effort, still recorded
                with self._lock:
                    self._send_errors += 1

    # -- the HTTP-level gate (before any WebSocket frame exists) -------

    def _process_request(self, connection: Any, request: Any) -> Any:
        """Pre-handshake: only the fail-closed and one-peer-at-a-time checks.

        The secret itself is NEVER checked here (round 2 correction — see the
        module docstring): a query parameter is the wrong place for a
        credential, so the handshake carries none. A ``?secret=...`` query
        parameter, if a client sends one anyway, is ignored for
        authentication and only counted.
        """
        if not self.config.secret:
            return connection.respond(503, "no secret configured\n")

        try:
            query = urlsplit(request.path).query
            leaked = bool(parse_qs(query).get(SECRET_QUERY_PARAM))
        except Exception:  # noqa: BLE001 - a malformed path is not itself a refusal
            with self._lock:
                self._path_parse_errors += 1
            leaked = False
        if leaked:
            with self._lock:
                self._secret_in_url_count += 1
            self._degradation = EndpointDegradation(
                DEGRADED_SECRET_IN_URL,
                "a secret query parameter was ignored; the endpoint authenticates"
                " on the first message instead",
            )

        with self._lock:
            if self._connection is not None:
                self._rejected_busy_count += 1
                return connection.respond(503, "endpoint already has an active peer\n")
            if self._connection_pending and not self._pending_claim_is_stale_locked():
                self._rejected_busy_count += 1
                return connection.respond(503, "endpoint already has an active peer\n")
            if self._connection_pending:
                # Stale: the previous claim's handshake never reached the
                # handler and process_response never got a chance to notice
                # (round 4 finding 1's backstop — see
                # :data:`_PENDING_CLAIM_MARGIN_S`). Reclaim it for THIS
                # connection instead of refusing a perfectly good one.
                self._handshake_aborted_count += 1
            self._connection_pending = True
            self._connection_pending_since = self._clock()
            self._pending_connection = connection

        return None  # allow the handshake to proceed

    def _pending_claim_is_stale_locked(self) -> bool:
        """``True`` when the current pending claim outlived the handshake's
        own bound — must be called with :attr:`_lock` already held."""
        since = self._connection_pending_since
        if since is None:  # a REAL handler is running it now; never stale
            return False
        bound = self.config.handshake_open_timeout + _PENDING_CLAIM_MARGIN_S
        return (self._clock() - since) > bound

    def _process_response(self, connection: Any, request: Any, response: Any) -> Any:
        """Round 4 finding 1: release a pending claim the handler will never see.

        Runs at the end of EVERY handshake attempt this process's
        ``process_request`` saw — successful or not — including one that
        approved a client that then vanished before the ``101`` response
        could be sent. ``_handle_connection`` is only ever invoked for a
        handshake that reaches :data:`~websockets.protocol.State.OPEN`; a
        peer that aborts after being approved but before that never runs the
        handler at all, so nothing would otherwise clear
        :attr:`_connection_pending`, and this endpoint would refuse every
        later connection as "already has an active peer" until the process
        restarted. Only ever touches the claim THIS connection object itself
        made (``_pending_connection is connection``) — never another,
        still-legitimate connection's. Never alters the response.
        """
        from websockets.protocol import State

        with self._lock:
            if (
                self._pending_connection is connection
                and self._connection_pending
                and connection.state is not State.CONNECTING
            ):
                self._connection_pending = False
                self._connection_pending_since = None
                self._pending_connection = None
                self._handshake_aborted_count += 1
        return None

    # -- one connection's lifetime ---------------------------------------

    async def _authenticate(self, connection: Any) -> bool:
        """Read exactly one ``{"type": "auth", "secret": ...}`` message.

        Never raises. ``True`` only when the secret matched
        (constant-time); every other outcome — timeout, malformed JSON, the
        wrong ``type``, a missing/wrong secret, the peer vanishing mid-read —
        closes the socket with WebSocket close code 1008 and records exactly
        one :data:`DEGRADED_UNAUTHORIZED`, whose reason is always a static
        phrase (wave 1 lesson 5: never the value the peer supplied).
        """
        try:
            raw = await asyncio.wait_for(connection.recv(), timeout=self.config.auth_deadline)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            await self._refuse_unauthenticated(connection, "no auth message inside the deadline")
            return False
        except Exception:  # noqa: BLE001 - the peer vanished before authenticating
            await self._refuse_unauthenticated(connection, "connection ended before authenticating")
            return False

        try:
            text = raw if isinstance(raw, str) else bytes(raw).decode("utf-8")
            payload = json.loads(text)
        except Exception:  # noqa: BLE001 - not this client's job to parse garbage
            await self._refuse_unauthenticated(connection, "first message was not valid JSON")
            return False
        if not isinstance(payload, dict) or payload.get("type") != AUTH_EVENT_TYPE:
            await self._refuse_unauthenticated(connection, "first message was not an auth event")
            return False

        supplied = payload.get("secret")
        if not isinstance(supplied, str) or not supplied or not self._secret_matches(supplied):
            await self._refuse_unauthenticated(connection, "auth secret missing or did not match")
            return False
        return True

    def _secret_matches(self, supplied: str) -> bool:
        """Constant-time compare. Never raises: ``hmac.compare_digest(str, str)``
        rejects any non-ASCII character with a ``TypeError`` (found by round 2's
        own bidi/control-character attack), so both sides compare as bytes
        instead — bytes comparison has no such restriction, for any input."""
        try:
            return hmac.compare_digest(
                supplied.encode("utf-8", "surrogatepass"),
                self.config.secret.encode("utf-8", "surrogatepass"),
            )
        except Exception:  # noqa: BLE001 - an unencodable secret is simply not a match
            return False

    async def _refuse_unauthenticated(self, connection: Any, reason: str) -> None:
        with self._lock:
            self._unauthorized_count += 1
        self._degradation = EndpointDegradation(DEGRADED_UNAUTHORIZED, reason)
        try:
            await connection.close(code=1008, reason="unauthorized")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the peer is already gone; nothing to report to it
            with self._lock:
                self._send_errors += 1

    async def _handle_connection(self, connection: Any) -> None:
        # The handler is running: this handshake definitely reached OPEN, so
        # the claim is no longer eligible for the staleness backstop above —
        # only THIS handler's own `finally` releases it from here on, no
        # matter how long authentication or the session itself take (round 4
        # finding 1; keeps round 2's "refused even mid-auth" guarantee).
        with self._lock:
            if self._pending_connection is connection:
                self._connection_pending_since = None
        self._session_id = _new_id("sess")
        self._response_id = _new_id("resp")
        try:
            if not await self._authenticate(connection):
                return

            with self._lock:
                self._connection = connection
                self._connected = True
            try:
                await connection.send(self._session_created_json())
            except Exception:  # noqa: BLE001 - the peer vanished before it heard anything
                with self._lock:
                    self._send_errors += 1
            try:
                async for raw in connection:
                    self._on_client_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - any transport fault just ends this connection
                with self._lock:
                    self._connection_drop_count += 1
        finally:
            with self._lock:
                self._connection_pending = False
                if self._pending_connection is connection:
                    self._pending_connection = None
                if self._connection is connection:
                    self._connection = None
                    self._connected = False

    def _on_client_message(self, raw: Any) -> None:
        """Decode one browser-sent frame. Never raises; every fault is counted."""
        try:
            text = raw if isinstance(raw, str) else bytes(raw).decode("utf-8")
            payload = json.loads(text)
        except Exception:  # noqa: BLE001 - a frame this client did not write
            with self._lock:
                self._frames_malformed += 1
            return
        if not isinstance(payload, dict):
            with self._lock:
                self._frames_malformed += 1
            return

        kind = payload.get("type")
        if kind == wire.APPEND_EVENT_TYPE:
            self._on_append(payload)
        elif kind == wire.SESSION_UPDATE_EVENT_TYPE:
            with self._lock:
                self._session_updates_received += 1
            self._enqueue_control(self._session_updated_json(payload))
        else:
            with self._lock:
                self._unknown_frames += 1

    def _on_append(self, payload: dict[str, Any]) -> None:
        audio_b64 = payload.get("audio")
        if not isinstance(audio_b64, str):
            with self._lock:
                self._frames_malformed += 1
            return
        try:
            frame = base64.b64decode(audio_b64, validate=False)
        except Exception:  # noqa: BLE001 - malformed base64 from the peer
            with self._lock:
                self._frames_malformed += 1
            return

        with self._lock:
            self._frames_received += 1
            muted = self._muted
            callback = self._on_frame if self._capturing else None
        if muted:
            with self._lock:
                self._frames_muted_dropped += 1
            return
        if callback is None:
            return
        try:
            callback(frame)
        except Exception:  # noqa: BLE001 - a caller's callback must never kill this reader
            with self._lock:
                self._callback_errors += 1

    def _enqueue_control(self, frame_json: str) -> None:
        """Fire-and-forget: send one small control-plane reply, best effort.

        Bounded (round 4 finding 3): at most :data:`_MAX_INFLIGHT_CONTROL_SENDS`
        of these may be outstanding at once. A burst past the cap (a peer
        that stops reading while still sending ``session.update``) drops the
        NEW send and counts it, rather than piling up one fire-and-forget
        task per message without limit.
        """
        loop = self._loop
        connection = self._connection
        if loop is None or connection is None:
            return
        with self._lock:
            if self._control_sends_inflight >= _MAX_INFLIGHT_CONTROL_SENDS:
                self._control_sends_dropped += 1
                return
            self._control_sends_inflight += 1
        try:
            loop.call_soon_threadsafe(self._schedule_send, connection, frame_json)
        except RuntimeError:
            with self._lock:
                self._control_sends_inflight -= 1

    def _schedule_send(self, connection: Any, frame_json: str) -> None:

        async def _send() -> None:
            try:
                await connection.send(frame_json)
            except Exception:  # noqa: BLE001 - a control reply the peer never gets
                with self._lock:
                    self._send_errors += 1
            finally:
                with self._lock:
                    self._control_sends_inflight -= 1

        asyncio.get_running_loop().create_task(_send())

    # -- capture ---------------------------------------------------------

    def start_capture(self, on_frame: FrameCallback) -> None:
        self._on_frame = on_frame
        self._capturing = True

    def stop_capture(self) -> None:
        self._capturing = False

    # -- playback ----------------------------------------------------------

    def play(self, frames: bytes) -> None:
        if not isinstance(frames, (bytes, bytearray)):
            return
        frames = bytes(frames)
        if not frames or self._closed:
            return

        limit = int(_PLAYBACK_BUFFER_SECONDS * SAMPLE_RATE_HZ) * SAMPLE_WIDTH_BYTES
        with self._lock:
            in_flight = self._playback_queued_bytes + self._playback_active_bytes
            if in_flight + len(frames) > limit:
                self._playback_overflow_count += 1
                return
            for piece in _slices(frames, _SLICE_BYTES):
                self._playback_chunks.append(piece)
                self._playback_queued_bytes += len(piece)
            self._playing = True

        loop = self._loop
        wake = self._playback_wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def stop_playback(self) -> int:
        with self._lock:
            discarded_bytes = self._playback_queued_bytes + self._playback_active_bytes
            discarded_samples = discarded_bytes // SAMPLE_WIDTH_BYTES
            self._playback_chunks.clear()
            self._playback_queued_bytes = 0
            self._playback_active_bytes = 0
            # SAMPLES, not bytes — matches embodiment.audio.host.HostEndpoint's
            # own ``_playback_stop_discarded_total`` convention, so a host
            # reading ``status()`` from either endpoint reads the same unit.
            self._playback_stop_discarded_total += discarded_samples
            self._playing = False
        return discarded_samples

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def sample_rate(self) -> int:
        """The rate frames reach ``on_frame`` at: always :data:`_REMOTE_SAMPLE_RATE_HZ`.

        Duck-typed against the ``AudioEndpoint`` protocol's read-only
        ``sample_rate`` member (added on ``realtime/t7``, not merged into
        this branch — see the module docstring). Not measured: this endpoint
        never resamples, so the delivered rate is the declared wire rate by
        construction, the same way :class:`~embodiment.audio.endpoint.NullEndpoint`
        reports :data:`~embodiment.audio.endpoint.SAMPLE_RATE_HZ` rather than
        a measurement of anything.
        """
        return _REMOTE_SAMPLE_RATE_HZ

    async def _sender_loop(self) -> None:

        while True:
            chunk = None
            with self._lock:
                if self._playback_chunks:
                    chunk = self._playback_chunks.popleft()
                    self._playback_queued_bytes -= len(chunk)
                    self._playback_active_bytes = len(chunk)

            if chunk is None:
                try:
                    await asyncio.wait_for(self._playback_wake.wait(), timeout=_SENDER_POLL_S)
                except asyncio.TimeoutError:
                    pass
                self._playback_wake.clear()
                continue

            connection = self._connection
            if connection is None:
                # Nothing to send to yet — keep the chunk queued (front) and
                # wait for a peer; playback is never silently dropped just
                # because nobody has connected.
                with self._lock:
                    self._playback_chunks.appendleft(chunk)
                    self._playback_queued_bytes += len(chunk)
                    self._playback_active_bytes = 0
                await asyncio.sleep(_NO_PEER_RETRY_S)
                continue

            try:
                await connection.send(self._audio_delta_json(chunk))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the peer vanished mid-send
                with self._lock:
                    self._send_errors += 1
                    self._playback_active_bytes = 0
                continue

            with self._lock:
                self._playback_sent_bytes += len(chunk)
                self._playback_active_bytes = 0
                if not self._playback_chunks:
                    self._playing = False

    # -- mute ----------------------------------------------------------------

    def mute(self, muted: bool) -> None:
        self._muted = bool(muted)

    @property
    def muted(self) -> bool:
        return self._muted

    # -- wire encoding (server role; see module docstring) ------------------

    def _session_created_json(self) -> str:
        return json.dumps(
            {
                "type": "session.created",
                "session_id": self._session_id,
                "event_id": _new_id("event"),
                "timestamp_ms": _timestamp_ms(),
                "config": {
                    "input_audio_format": wire.AUDIO_FORMAT,
                    "input_sample_rate": wire.INPUT_SAMPLE_RATE,
                    "channels": CHANNELS,
                    "turn_detection": wire.TURN_DETECTION,
                    "aec_mode": wire.AEC_MODE,
                    "language": wire.LANGUAGE,
                },
            }
        )

    def _session_updated_json(self, payload: dict[str, Any]) -> str:
        session = payload.get("session")
        applied: dict[str, Any] = {}
        if isinstance(session, dict) and isinstance(session.get("language"), str):
            applied["language"] = session["language"]
        return json.dumps(
            {
                "type": "session.updated",
                "session_id": self._session_id,
                "event_id": _new_id("event"),
                "timestamp_ms": _timestamp_ms(),
                "session": applied,
            }
        )

    def _audio_delta_json(self, chunk: bytes) -> str:
        return json.dumps(
            {
                "type": "response.audio.delta",
                "session_id": self._session_id,
                "event_id": _new_id("event"),
                "timestamp_ms": _timestamp_ms(),
                "response_id": self._response_id,
                "delta": base64.b64encode(chunk).decode("ascii"),
            }
        )

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._lock:
            counters = {
                "frames_received": self._frames_received,
                "frames_muted_dropped": self._frames_muted_dropped,
                "frames_malformed": self._frames_malformed,
                "unknown_frames": self._unknown_frames,
                "session_updates_received": self._session_updates_received,
                "callback_errors": self._callback_errors,
                "send_errors": self._send_errors,
                "connection_drop_count": self._connection_drop_count,
                "unauthorized_connections": self._unauthorized_count,
                "secret_in_url_count": self._secret_in_url_count,
                "path_parse_errors": self._path_parse_errors,
                # round 5: exposed beside its sibling so a probe reading
                # this dict sees both one-peer-at-a-time counts together —
                # how many were refused outright vs. how many were an
                # abandoned handshake reclaimed instead of refused.
                "rejected_busy_count": self._rejected_busy_count,
                "handshake_aborted_count": self._handshake_aborted_count,
                "server_thread_fault_count": self._server_thread_fault_count,
                "control_sends_dropped": self._control_sends_dropped,
                "playback_overflow_count": self._playback_overflow_count,
                "playback_sent_bytes": self._playback_sent_bytes,
                "playback_stop_discarded_total": self._playback_stop_discarded_total,
                "playback_queued_bytes": self._playback_queued_bytes,
            }
        return {
            "attached": self._attached,
            "capturing": self._capturing,
            "connected": self._connected,
            "playing": self._playing,
            "muted": self._muted,
            "closed": self._closed,
            "host": self.config.host,
            "port": self._bound_port,
            "degradation": self._degradation.to_dict() if self._degradation else None,
            "close_report": self._last_close_report.to_dict() if self._last_close_report else None,
            **counters,
        }

    @property
    def bound_port(self) -> int:
        """The port actually listening — resolves a ``port=0`` request to its real value."""
        return self._bound_port

    def __repr__(self) -> str:
        return (
            f"RemoteEndpoint(host={self.config.host!r}, port={self.config.port!r}, "
            f"attached={self._attached}, connected={self._connected})"
        )
