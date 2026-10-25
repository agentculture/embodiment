"""embodiment.http.server — the daemon's HTTP surface: static, stream, control.

Task ``t16`` of the ``realtime-embodiment-app`` plan. One stdlib
``ThreadingHTTPServer``, four things served, and a guard
(:mod:`embodiment.http.guard`) in front of the two that matter:

======================  ========  ==================================
route                   guarded   what it is
======================  ========  ==================================
``GET /``               no        ``web/dist/index.html``
``GET /assets/…``       no        the dashboard's own files
``GET /api/events``     **yes**   the SSE projection of the event bus
``GET /api/status``     **yes**   the daemon's status snapshot
``POST /api/voice/start``  **yes**   start listening
``POST /api/voice/stop``   **yes**   stop listening
``POST /api/mic/mute``     **yes**   ``{"muted": true|false}``
``POST /api/chat``         **yes**   ``{"message", "conversation"?}``: a typed turn; with a
                                      conversation id it continues that conversation
``GET /api/library``       **yes**   the catalog: ``{"items": [...]}``
``GET /api/library/{id}``  **yes**   ``{"item": {...}, "passages": [...]}``
``GET /api/library/search?q=``  **yes**  ``{"hits": [...]}``
``GET /api/todos``         **yes**   ``{"groups": [...]}``
``POST /api/todos/{id}``   **yes**   ``{"done": true|false}``: mark one to-do (d16)
``GET /api/conversations``      **yes**  past conversations: ``{"conversations": [...]}``
``GET /api/conversations/{id}`` **yes**  ``{"conversation": {...}, "turns": [...]}``
======================  ========  ==================================

Static assets are unguarded because the dashboard must load before it can
present a credential; they carry no secret, which
``tests/test_http_server.py`` proves by planting a marker. **The stream is
guarded**: it carries the transcript, so it is as sensitive as any write.

Loopback bind unless the operator says otherwise
------------------------------------------------
:func:`resolve_bind` refuses a routable bind address without ``--bind-public``
and raises :class:`~embodiment.cli._errors.CliError` with a hint, which is the
repo's error contract. It is deliberately conservative: ``localhost`` and any
address ``ipaddress`` calls loopback pass, and **everything else fails**,
including a hostname that might well resolve to 127.0.0.1. A name this module
would have to resolve to classify is a name whose answer can change between
the check and the bind, so it is refused rather than looked up.

The gateway key is never here
------------------------------
Nothing in this module reads a model-gateway key: the daemon talks to lobes,
the browser talks to the daemon, and the two conversations do not share a
credential. The key appears in :attr:`ServerConfig.redact` for one purpose —
an event whose serialised form contains a configured secret is **dropped from
the stream** and recorded as :data:`SECRET_IN_STREAM_CODE`, so a leak
introduced upstream is stopped here and is visible rather than streamed to
every attached dashboard. That is defence in depth behind
:class:`embodiment.bus.Bus`'s own ``redact=``, not a substitute for it.

Degrade, never raise; and never go quiet
-----------------------------------------
No request produces a traceback and none produces a 500 for a *foreseeable*
environment state. A missing ``web/dist`` serves a one-line text page and
records :data:`NO_DASHBOARD_CODE`. An unbound control callable answers 503 and
records :data:`CONTROL_UNBOUND_CODE` — t15 binds them; until then the surface
exists and says so. A control callable that raises is a 500, a recorded
:data:`CONTROL_FAILED_CODE` **described through**
:func:`embodiment.safe_reason.describe_exception`, and a still-serving daemon.
Every one of those is counted in :meth:`DashboardServer.status`, and the
records themselves are bounded (:data:`MAX_DEGRADATION_CODES` distinct codes,
first occurrence kept, the rest counted) so a hostile client cannot turn the
ledger into a memory leak.

Shutdown is a feature
----------------------
:meth:`DashboardServer.shutdown` is idempotent, never raises, returns inside
its deadline and reports what it left: it sets the stop flag first, closes
every live stream subscription (which wakes each SSE thread out of its bounded
``get``), stops the accept loop under a bounded join, and closes the listening
socket. Handler threads are daemon threads, so a wedged client can never keep
the process alive.

What is bounded, and why each bound exists
-------------------------------------------
Concurrent SSE streams (:data:`DEFAULT_MAX_STREAMS`) — each one owns a thread
and a bus subscription, so this is the real resource. A stream's slot is freed
when its next **write** fails, which is the one measurement a reader should
not have to make themselves: a client that vanished without closing (a laptop
lid, a dropped link) keeps its slot until the next event or the next keepalive
tries to reach it, so the worst-case hold is :attr:`ServerConfig.keepalive_s`
plus the socket timeout, not forever. A client that is connected and has
stopped reading is dropped on the socket timeout and recorded as
:data:`STREAM_WRITE_TIMEOUT_CODE`; an ordinary disconnect is counted only.
Request body bytes (:data:`DEFAULT_MAX_BODY_BYTES`) — read only after
``Content-Length`` has been checked, never after. The socket's own timeout — a client that opens a
connection and says nothing must not hold a thread. Every number here is a
**judgement call**, not a measurement; they are named constants so the task
that measures them has one place to change.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import threading
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote

from embodiment.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from embodiment.daemon.text_turns import (
    MAX_CLIP_BYTES,
    MAX_TEXT_CHARS,
    SPEAK_DEADLINE_S,
    STT_DEADLINE_S,
)
from embodiment.http import guard as guard_module
from embodiment.safe_reason import describe_exception

__all__ = [
    "DEFAULT_PORT",
    "DEFAULT_MAX_STREAMS",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_STREAM_POLL_S",
    "DEFAULT_KEEPALIVE_S",
    "DEFAULT_SOCKET_TIMEOUT_S",
    "MAX_DEGRADATION_CODES",
    "NO_DASHBOARD_CODE",
    "CONTROL_UNBOUND_CODE",
    "CONTROL_FAILED_CODE",
    "CONTROL_TIMEOUT_CODE",
    "CONTROL_BUSY_CODE",
    "DEFAULT_CONTROL_TIMEOUT_S",
    "DEFAULT_CONTROL_WORKERS",
    "DEFAULT_TEXT_WORKERS",
    "CHAT_RECALL_S",
    "CHAT_COMPLETION_S",
    "CHAT_MAX_COMPLETIONS",
    "CONTROL_SLACK_S",
    "DEFAULT_CHAT_TIMEOUT_S",
    "DEFAULT_SPEAK_TIMEOUT_S",
    "DEFAULT_CLIP_TIMEOUT_S",
    "TEXT_BODY_MAX_BYTES",
    "CLIP_READ_DEADLINE_S",
    "CLIP_UPLINK_FLOOR_BYTES_PER_S",
    "ACCESS_STREAM_MAX_S",
    "LENGTH_REQUIRED_CODE",
    "TEXT_REFUSED_CODE",
    "STREAM_EXPIRED_REASON",
    "MAX_CONTROL_INFLIGHT",
    "MAX_TEXT_INFLIGHT",
    "LIBRARY_ROUTE",
    "CONVERSATIONS_ROUTE",
    "CONVERSATION_NOT_FOUND_CODE",
    "LIBRARY_SEARCH_ROUTE",
    "TODOS_ROUTE",
    "LIBRARY_NOT_CONFIGURED_CODE",
    "MEMORY_UNAVAILABLE_CODE",
    "TODOS_UNAVAILABLE_CODE",
    "BAD_REQUEST_CODE",
    "BODY_TOO_LARGE_CODE",
    "TOO_MANY_STREAMS_CODE",
    "SECRET_IN_STREAM_CODE",
    "STREAM_FAILED_CODE",
    "STREAM_WRITE_TIMEOUT_CODE",
    "STATIC_REFUSED_CODE",
    "STATIC_UNREADABLE_CODE",
    "NO_BUS_CODE",
    "STREAM_ROUTE",
    "NO_DASHBOARD_PAGE",
    "Controls",
    "ServerConfig",
    "ServerCloseReport",
    "DashboardServer",
    "resolve_bind",
    "default_dist_dir",
    "redaction_forms",
    "ControlOutcome",
    "is_loopback_address",
]

#: The daemon's default port. A **judgement call**: high, fixed, and not a
#: port anything else on this rig listens on.
DEFAULT_PORT = 8823

#: Concurrent SSE streams. A **judgement call**: one operator with a couple of
#: browser tabs and a spare for a robot relay, bounded because each stream
#: costs a thread and a bus subscription.
DEFAULT_MAX_STREAMS = 8

#: Largest request body read. A **judgement call**: the biggest body any route
#: here accepts is ``{"muted": true}``.
DEFAULT_MAX_BODY_BYTES = 8192

#: How long an idle SSE thread blocks in ``Subscription.get`` before looking at
#: the stop flag again. Bounds shutdown latency, not throughput.
DEFAULT_STREAM_POLL_S = 0.5

#: How often an idle stream writes a ``:`` comment. A **judgement call**: far
#: under the 60 s that proxies and phones typically reap an idle connection at,
#: and cheap (one line).
DEFAULT_KEEPALIVE_S = 15.0

#: Per-connection socket timeout. A client that connects and sends nothing —
#: or stops reading mid-stream — releases its thread instead of pinning it.
DEFAULT_SOCKET_TIMEOUT_S = 30.0

#: How long a request waits for a control callable before giving up on it.
#: Derived from the quantity it bounds rather than picked round (``CLAUDE.md``
#: lesson 1): the bounded thing is a browser click on Start/Stop/Mute, so this
#: is the outer edge of "the page still feels like it answered". It does NOT
#: bound what the daemon does — a control that needs longer should return
#: promptly and report progress on the event stream.
DEFAULT_CONTROL_TIMEOUT_S = 5.0

#: Threads available to run control callables. A **judgement call**: four
#: controls exist and an operator clicks one at a time, so this is headroom,
#: not capacity.
DEFAULT_CONTROL_WORKERS = 4

#: How many control calls may be outstanding — running plus abandoned — before
#: new ones are refused. A timed-out callable cannot be killed (Python offers
#: no such thing), only abandoned, so without this bound a wedged control
#: would accumulate one leaked task per retry. A **judgement call**, sized at
#: twice the worker count.
MAX_CONTROL_INFLIGHT = 8

#: Threads available to the text routes (``/api/chat``, ``/api/speak``,
#: ``/api/clip``) — a pool of their OWN. One typed turn may hold a worker for
#: the chat bound (~65 s) and a clip for longer, so on the control pool four of
#: them would queue the privacy mute past its 5 s bound (review round d6). A
#: **judgement call**, the same headroom as the control pool: typed turns
#: serialise on the daemon's conversation anyway.
DEFAULT_TEXT_WORKERS = 4

#: How many text calls may be outstanding — running plus abandoned — before new
#: ones are refused with :data:`CONTROL_BUSY_CODE`. Same reasoning and the same
#: twice-the-workers sizing as :data:`MAX_CONTROL_INFLIGHT`, counted apart from it.
MAX_TEXT_INFLIGHT = 8

# ── the text-and-clip routes (task t10): each clock named for what it bounds ──

#: How long one chat turn waits for its recall before the first completion: the head
#: start (``recall_events.DEFAULT_HEAD_START_S``), which mirrors
#: ``embodiment.memory.DEFAULT_DEADLINE`` (0.25 s; a test pins the equality) - copied,
#: not imported, so this module does not load the memory layer. The recall itself runs
#: alongside the completion under the daemon's derived bound (t23,
#: ``daemon.app.derive_recall_deadline``, capped under one completion), so it adds
#: nothing serial past this.
CHAT_RECALL_S = 0.25

#: Bounds ONE ``senses`` completion. A **judgement call** on where the operator has
#: stopped waiting, now checked against a measurement: whole-completion time at
#: ``max_tokens`` 200 was median 3983 ms, p90 4337, max 5085 (n = 30, lapse l5 in
#: ``docs/live-test-results/2026-10-23-recall-gemma2.md``), so 30 s does not censor it.
CHAT_COMPLETION_S = 30.0

#: How many completions one chat request may cost: the turn's reply, plus the second one
#: t13 may add (a follow-up on a source). Sized for the larger.
CHAT_MAX_COMPLETIONS = 2

#: Added to every derived bound below: queueing on the control pool and the response
#: write. A **judgement call**.
CONTROL_SLACK_S = 5.0

#: Bounds ``/api/chat``: the recall, then every completion it may cost, then the slack
#: (0.25 + 2 * 30 + 5). The default 5 s control bound would 503 every real chat turn.
DEFAULT_CHAT_TIMEOUT_S = CHAT_RECALL_S + CHAT_MAX_COMPLETIONS * CHAT_COMPLETION_S + CONTROL_SLACK_S

#: Bounds ``/api/speak``: ``text_turns.speak_message`` stops synthesising at
#: ``SPEAK_DEADLINE_S`` and returns what it has; the request waits that plus the slack.
DEFAULT_SPEAK_TIMEOUT_S = SPEAK_DEADLINE_S + CONTROL_SLACK_S

#: Bounds ``/api/clip``: transcription (``STT_DEADLINE_S``), then the typed turn the
#: transcript runs as (the chat bound), then the slack.
DEFAULT_CLIP_TIMEOUT_S = STT_DEADLINE_S + DEFAULT_CHAT_TIMEOUT_S + CONTROL_SLACK_S

#: Bounds a JSON text body (chat, speak). ``MAX_TEXT_CHARS`` characters at the UTF-8
#: worst case of 4 bytes each, plus 1 KiB of JSON framing. The generic 8 KiB bound would
#: turn a 4000-character CJK message into a 413 instead of the text layer's own refusal.
TEXT_BODY_MAX_BYTES = MAX_TEXT_CHARS * 4 + 1024

#: The slowest uplink a clip upload is sized for: a phone on a poor day. A
#: **judgement call**, in bytes per second.
CLIP_UPLINK_FLOOR_BYTES_PER_S = 140_000

#: Bounds reading a clip body off the socket, in total. The per-recv socket timeout
#: (30 s) alone lets a client trickle ``MAX_CLIP_BYTES`` for hours. Derived: the byte
#: cap at the uplink floor (11 585 536 B / 140 KB/s = ~82.8 s; it was 120 s for 16 MiB).
CLIP_READ_DEADLINE_S = MAX_CLIP_BYTES / CLIP_UPLINK_FLOOR_BYTES_PER_S

# ── the library and to-do read routes (task t6): each clock named for what it bounds ──

#: Bounds ONE library catalog or reader read: a local, indexed read. A **judgement
#: call** on where a page load has stopped being a page load.
LIBRARY_READ_BOUND_S = 2.0

#: Bounds ONE library search: it embeds the query (gemma2-embed) and scans the index.
#: Derived from the recall cap in ``daemon.app.derive_recall_deadline`` (embed p90 161 ms
#: + 0.13 ms/record + 0.15 s, capped at 3 s) - copied, not imported, so this module does
#: not load the daemon.
LIBRARY_SEARCH_BOUND_S = 3.0

#: Bounds ONE to-do read: the held library to-do lines reconciled with Gwen's marks;
#: sized to the search.
TODOS_READ_BOUND_S = 3.0

#: The request waits the callable's own bound plus the slack, never the 5 s control default.
DEFAULT_LIBRARY_TIMEOUT_S = LIBRARY_READ_BOUND_S + CONTROL_SLACK_S
DEFAULT_LIBRARY_SEARCH_TIMEOUT_S = LIBRARY_SEARCH_BOUND_S + CONTROL_SLACK_S
DEFAULT_TODOS_TIMEOUT_S = TODOS_READ_BOUND_S + CONTROL_SLACK_S

#: Longest search query accepted, in characters. A **judgement call**: a spoken-length
#: question, far under what an embedder takes.
LIBRARY_QUERY_MAX_CHARS = 200

#: Longest item id accepted, in digits (a SQLite-sized integer fits in 18).
LIBRARY_ID_MAX_DIGITS = 12

#: The library has no callable bound (or the daemon has no library).
LIBRARY_NOT_CONFIGURED_CODE = "library-not-configured"
#: The to-do callable is not bound.
MEMORY_UNAVAILABLE_CODE = "memory-unavailable"
#: The to-dos' library source could not be read (embodiment#105, c22): the daemon's
#: ``todos()`` answers it as ``code`` and the route refuses with 503, never an empty list.
TODOS_UNAVAILABLE_CODE = "library_unavailable"
#: A library path or query was malformed.
LIBRARY_BAD_REQUEST_CODE = "library-bad-request"
#: No library item has that id.
LIBRARY_NOT_FOUND_CODE = "library-item-not-found"
#: A callable answered a shape the contract does not allow.
LIBRARY_BAD_SHAPE_CODE = "library-bad-shape"

LIBRARY_ROUTE = "/api/library"
LIBRARY_SEARCH_ROUTE = "/api/library/search"
TODOS_ROUTE = "/api/todos"
#: The history pane (operator, 2026-10-24): read-only, from the session logs.
CONVERSATIONS_ROUTE = "/api/conversations"
CONVERSATION_NOT_FOUND_CODE = "conversation-not-found"
#: The clip route's way to name the conversation a recorded question continues.
CONVERSATION_HEADER = "X-Conversation"
#: The page's word for a clip: ``recorded`` (its mic) or ``uploaded`` (a file); d7.
CLIP_ORIGIN_HEADER = "X-Clip-Origin"
#: The page's claim of when the clip was recorded (or the file's date), epoch ms; d12.
CLIP_AT_HEADER = "X-Clip-At"
HISTORY_NOT_CONFIGURED_CODE = "history-not-configured"

#: How long an Access-admitted stream may stay open when the verifier reported no
#: ``exp``. Conservative by choice: an assertion of unknown lifetime is not trusted to
#: outlive a few minutes; the cost of closing early is one reconnect, which presents a
#: fresh assertion. A **judgement call**.
ACCESS_STREAM_MAX_S = 300.0

#: Largest chunk read from a clip body at a time.
_CLIP_CHUNK_BYTES = 64 * 1024

#: ``Content-Length`` was absent (or the body is chunked) on a route that must know the
#: size before reading.
LENGTH_REQUIRED_CODE = "http-length-required"
#: A text/clip route answered a refusal from the text layer (the layer recorded why).
TEXT_REFUSED_CODE = "http-text-route-refused"
#: Why a stream ended at its assertion's expiry (a frame comment, not a fault).
STREAM_EXPIRED_REASON = "session-expired"

#: Distinct degradation codes kept with their first occurrence. Same bound and
#: same reasoning as :mod:`embodiment.bus`: generous for this module's own
#: fixed vocabulary, closed against a client that could invent codes.
MAX_DEGRADATION_CODES = 64

# ── the degradation vocabulary (C3) ─────────────────────────────────────────

#: ``web/dist`` is missing, or is not a directory: no dashboard is built.
NO_DASHBOARD_CODE = "http-no-dashboard"
#: A control route was called and the daemon has not bound its callable.
CONTROL_UNBOUND_CODE = "http-control-unbound"
#: A bound control callable raised.
CONTROL_FAILED_CODE = "http-control-failed"
#: A control callable did not return within :attr:`ServerConfig.control_timeout_s`.
#: The request is answered; the callable is abandoned, not killed.
CONTROL_TIMEOUT_CODE = "http-control-timeout"
#: :data:`MAX_CONTROL_INFLIGHT` control calls are already outstanding — which
#: is what a wedged control looks like after a few retries.
CONTROL_BUSY_CODE = "http-control-busy"
#: The body was absent, was not JSON, or did not match the route's contract.
BAD_REQUEST_CODE = "http-bad-request"
#: ``Content-Length`` exceeded :attr:`ServerConfig.max_body_bytes`.
BODY_TOO_LARGE_CODE = "http-body-too-large"
#: :attr:`ServerConfig.max_streams` streams are already open.
TOO_MANY_STREAMS_CODE = "http-too-many-streams"
#: An event's serialised form contained a configured secret. Dropped.
SECRET_IN_STREAM_CODE = "http-secret-in-stream"  # nosec B105 - a code, not a secret
#: A stream ended on a write error that was not an ordinary disconnect.
STREAM_FAILED_CODE = "http-stream-failed"
#: A stream write blocked past the socket timeout: the client is connected and
#: has stopped reading. Distinct from a disconnect, which is ordinary and is
#: only counted — this one means a dashboard is wedged, which a host would
#: otherwise see as a healthy-looking stream that has quietly stopped.
STREAM_WRITE_TIMEOUT_CODE = "http-stream-write-timeout"
#: A static path pointed outside ``web/dist`` (traversal, or an escaping
#: symlink). Refused, never served.
STATIC_REFUSED_CODE = "http-static-refused"
#: A file inside ``web/dist`` exists and could not be read.
STATIC_UNREADABLE_CODE = "http-static-unreadable"
#: ``/events`` was requested and no event bus is wired in.
NO_BUS_CODE = "http-no-event-bus"

#: What is served when no dashboard is built. Deliberately plain text and
#: deliberately a 200: the surface is up, the dashboard is simply not built.
NO_DASHBOARD_PAGE = (
    "embodiment: no dashboard is built.\n\n"
    "This daemon is running and its API is up, but there is no web/dist to "
    "serve. The dashboard is a separate build; the state is recorded as "
    f"{NO_DASHBOARD_CODE} and shown by 'embodiment status'.\n"
)

#: Content types served for the extensions a built dashboard actually has.
#: An unknown extension is served as an opaque download rather than guessed
#: at, and every response carries ``X-Content-Type-Options: nosniff``.
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"

_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": _JSON_CONTENT_TYPE,
    ".map": _JSON_CONTENT_TYPE,
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"

#: Unicode categories that may not appear in a request path. Same set as the
#: guard's: a NUL, a newline or a bidi override in a path is not a filename.
_FORBIDDEN_PATH_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: Longest request path this module will even classify.
_MAX_PATH_CHARS = 2048

#: The SSE route. Under ``/api/`` like every other non-static route, agreed
#: with the dashboard (task t17), which connects to exactly this path.
STREAM_ROUTE = "/api/events"

CHAT_ROUTE = "/api/chat"
SPEAK_ROUTE = "/api/speak"
CLIP_ROUTE = "/api/clip"
_TEXT_ROUTES = (CHAT_ROUTE, SPEAK_ROUTE, CLIP_ROUTE)
#: End the live conversation and start a new one (operator, 2026-10-24).
NEW_CONVERSATION_ROUTE = "/api/conversation/new"
#: A turn held the conversation for the whole wait: nothing was ended.
CONVERSATION_BUSY_CODE = "conversation-busy"
_CONTROL_ROUTES = (
    "/api/voice/start",
    "/api/voice/stop",
    "/api/mic/mute",
    NEW_CONVERSATION_ROUTE,
) + _TEXT_ROUTES


def _guard_status(guard: Any) -> dict[str, Any]:
    """The guard's own status (Access counters), or nothing for a guard without one."""
    status = getattr(guard, "status", None)
    if not callable(status):
        return {}
    try:
        value = status()
    except Exception:  # noqa: BLE001  # a status read never fails the snapshot
        return {}
    return value if isinstance(value, dict) else {}


def default_dist_dir() -> Path:
    """Where the built dashboard is, in whichever layout this package is in.

    The dashboard ships **inside** the package (task ``t19``, a hatch
    force-include), so the build sits at two different depths depending on how
    embodiment arrived:

    * an installed wheel — ``<site-packages>/embodiment/web/dist``, one level
      up from this module's own directory;
    * a dev checkout — ``<repo>/web/dist``, two levels up, beside the package
      rather than inside it.

    Both are probed, installed first, and a candidate counts only when its
    ``index.html`` exists: an empty ``web/dist`` left behind by a failed build
    is not a dashboard. When neither is built the **dev-tree** path is
    returned, so the recorded :data:`NO_DASHBOARD_CODE` state and
    ``status()['dist_dir']`` still name somewhere a human would look rather
    than a path that could never have existed.

    Found by installing the real wheel, not by a test: resolving only the dev
    depth meant every install reported no dashboard while the dashboard was
    sitting inside the package.
    """
    here = Path(__file__).resolve()
    installed = here.parents[1] / "web" / "dist"
    dev_tree = here.parents[2] / "web" / "dist"
    for candidate in (installed, dev_tree):
        if (candidate / "index.html").is_file():
            return candidate
    return dev_tree


def is_loopback_address(address: str) -> bool:
    """Whether *address* is unambiguously loopback, with no name resolution.

    ``localhost`` and any literal ``ipaddress`` calls loopback qualify.
    Anything else — a wildcard, a LAN address, a hostname — does not, because
    a name whose answer this module would have to look up is a name whose
    answer can change between the check and the bind.
    """
    value = str(address).strip().lower()
    if value in {"localhost", "localhost.localdomain"}:
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return bool(ipaddress.ip_address(value).is_loopback)
    except ValueError:
        return False


def resolve_bind(address: str, *, bind_public: bool) -> str:
    """The address to bind, or a :class:`CliError` naming the flag that allows it.

    The one place in this module that raises: it runs before anything is
    listening, on the CLI's own path, and refusing to start is the correct
    answer to "expose this to the network?" asked implicitly.
    """
    if bind_public or is_loopback_address(address):
        return address
    raise CliError(
        code=EXIT_USER_ERROR,
        message=(
            f"refusing to bind {address!r}: it is not a loopback address, so the "
            "dashboard, the event stream (which carries the transcript) and the "
            "control API would be reachable from the network"
        ),
        remediation=(
            "bind 127.0.0.1 (the default), or pass --bind-public to accept that "
            "the install secret, the Host/Origin allow-list and Cloudflare Access "
            "are the only things in front of this daemon"
        ),
    )


@dataclass(frozen=True)
class Controls:
    """The daemon's verbs, injected. ``None`` means "t15 has not bound it".

    Each callable returns a JSON-serialisable dict, which is echoed back under
    ``result``. **None of them may block**, and that is now enforced rather
    than merely documented (round 3 review): a call runs on a bounded pool and
    the request waits at most :attr:`ServerConfig.control_timeout_s` before
    answering 503 :data:`CONTROL_TIMEOUT_CODE`. A callable that overruns is
    *abandoned*, never killed — Python cannot kill a thread — so overrunning
    repeatedly exhausts :data:`MAX_CONTROL_INFLIGHT` and the refusal changes to
    :data:`CONTROL_BUSY_CODE`. Both are recorded; neither stops the daemon
    serving the dashboard, the stream, or anything else.
    """

    start_voice: Optional[Callable[[], dict[str, Any]]] = None
    #: ``new_conversation() -> {"started", "ended"}``: end the live conversation.
    new_conversation: Optional[Callable[[], dict[str, Any]]] = None
    stop_voice: Optional[Callable[[], dict[str, Any]]] = None
    set_mute: Optional[Callable[[bool], dict[str, Any]]] = None
    status: Optional[Callable[[], dict[str, Any]]] = None
    #: ``chat(message) -> {"ok", "reply", "sources", "turn_serial", "code", "http_status"}``.
    #: ``message`` is the JSON value of ``"message"`` as received (any type; the text layer
    #: refuses a non-string). Runs under :attr:`ServerConfig.chat_timeout_s`. A second
    #: argument, only when the body names one, is the conversation id it continues.
    chat: Optional[Callable[..., dict[str, Any]]] = None
    #: ``speak(text) -> {"ok", "audio": bytes, "content_type", "code", "http_status"}``.
    #: Runs under :attr:`ServerConfig.speak_timeout_s`.
    speak: Optional[Callable[[Any], dict[str, Any]]] = None
    #: ``clip(audio, declared_duration_s) -> {"ok", "transcript", "reply", "code",
    #: "http_status"}``. ``declared_duration_s`` is ``None`` unless the client sent a
    #: finite, non-negative ``X-Clip-Duration-S``. Runs under
    #: :attr:`ServerConfig.clip_timeout_s`; the body is already inside ``MAX_CLIP_BYTES``.
    #: A third argument, only when ``X-Conversation`` or ``X-Clip-Origin`` is sent, is
    #: the conversation id (or ``None``); a fourth, only with ``X-Clip-Origin``, is it.
    clip: Optional[Callable[..., dict[str, Any]]] = None
    #: ``library_list() -> {"items": [{id, title, kind, created_at, indexed}]}``.
    library_list: Optional[Callable[[], dict[str, Any]]] = None
    #: ``library_get(item_id) -> {"item": {...}, "passages": [...]}``; an empty dict or
    #: ``None`` means no such item (404).
    library_get: Optional[Callable[[int], Optional[dict[str, Any]]]] = None
    #: ``library_search(q) -> {"hits": [...]}``. ``q`` is decoded and length-capped.
    library_search: Optional[Callable[[str], dict[str, Any]]] = None
    #: ``todos() -> {"groups": [{recording_id, title, date, items}]}``.
    todos: Optional[Callable[[], dict[str, Any]]] = None
    #: ``todo_set(todo_id, done) -> {"todo": {...} | None, "code"}``: the page's checkbox
    #: (d16); ``POST /api/todos/{id}`` with ``{"done": true|false}``.
    todo_set: Optional[Callable[[str, bool], dict[str, Any]]] = None
    #: ``conversations() -> {"conversations": [{id, title, started, last, turns, current}]}``.
    conversations: Optional[Callable[[], dict[str, Any]]] = None
    #: ``conversation(id) -> {"conversation": {...}, "turns": [...]}``; ``None`` is a 404.
    conversation: Optional[Callable[[str], Optional[dict[str, Any]]]] = None


@dataclass(frozen=True)
class ServerConfig:
    """Everything the surface needs. No globals, no environment reads."""

    bind: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    bind_public: bool = False
    dist_dir: Optional[Path] = None
    #: Literal secrets that must never appear in a streamed event. The install
    #: secret and the model-gateway key belong here; see the module docstring.
    redact: tuple[str, ...] = ()
    #: Whether the stream carries ``transcript``/``reply``. It does by default
    #: — the dashboard's whole point — which is why ``/events`` is guarded.
    include_speech: bool = True
    max_streams: int = DEFAULT_MAX_STREAMS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    control_timeout_s: float = DEFAULT_CONTROL_TIMEOUT_S
    control_workers: int = DEFAULT_CONTROL_WORKERS
    #: Workers of the text routes' own pool (chat, speak, clip).
    text_workers: int = DEFAULT_TEXT_WORKERS
    chat_timeout_s: float = DEFAULT_CHAT_TIMEOUT_S
    speak_timeout_s: float = DEFAULT_SPEAK_TIMEOUT_S
    clip_timeout_s: float = DEFAULT_CLIP_TIMEOUT_S
    library_timeout_s: float = DEFAULT_LIBRARY_TIMEOUT_S
    library_search_timeout_s: float = DEFAULT_LIBRARY_SEARCH_TIMEOUT_S
    todos_timeout_s: float = DEFAULT_TODOS_TIMEOUT_S
    text_body_max_bytes: int = TEXT_BODY_MAX_BYTES
    clip_max_bytes: int = MAX_CLIP_BYTES
    clip_read_deadline_s: float = CLIP_READ_DEADLINE_S
    access_stream_max_s: float = ACCESS_STREAM_MAX_S
    #: Epoch seconds. Injectable so a test can move the clock instead of waiting on it.
    wall_clock: Callable[[], float] = time.time
    stream_poll_s: float = DEFAULT_STREAM_POLL_S
    keepalive_s: float = DEFAULT_KEEPALIVE_S
    socket_timeout_s: float = DEFAULT_SOCKET_TIMEOUT_S


@dataclass(frozen=True)
class ServerCloseReport:
    """What :meth:`DashboardServer.shutdown` actually did."""

    already_closed: bool
    streams_closed: int
    serve_thread_stopped: bool
    elapsed_s: float
    #: Control callables still running when the deadline came. They are
    #: abandoned on daemon threads, never waited for — reported so a host can
    #: see that something of its own did not finish.
    controls_unfinished: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "already_closed": self.already_closed,
            "streams_closed": self.streams_closed,
            "serve_thread_stopped": self.serve_thread_stopped,
            "elapsed_s": self.elapsed_s,
            "controls_unfinished": self.controls_unfinished,
        }


@dataclass
class _Counters:
    """Everything :meth:`DashboardServer.status` reports as a number."""

    requests_served: int = 0
    requests_refused: int = 0
    refusals_by_code: dict[str, int] = field(default_factory=dict)
    streams_started: int = 0
    streams_finished: int = 0
    streams_refused: int = 0
    streams_disconnected: int = 0
    streams_timed_out: int = 0
    streams_expired: int = 0
    events_streamed: int = 0
    events_dropped_for_secret: int = 0
    controls_timed_out: int = 0
    controls_refused_busy: int = 0


@dataclass
class _Lane:
    """One bounded pool and its outstanding count (guarded by the server's lock).

    Two exist: ``control`` (start, stop, mute, status) and ``text`` (chat, speak,
    clip). Apart, so a slow typed turn can never queue the privacy mute.
    """

    name: str
    pool: ThreadPoolExecutor
    max_inflight: int
    inflight: int = 0


class _HttpServer(ThreadingHTTPServer):
    """A ``ThreadingHTTPServer`` that carries its :class:`DashboardServer`."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type, app: "DashboardServer") -> None:
        self.app = app
        super().__init__(address, handler)

    def handle_error(
        self, request: Any, client_address: Any
    ) -> None:  # pragma: no cover - exercised only by a handler bug
        """A handler that escaped its own try/except is recorded, not printed.

        The base class prints a traceback to stderr. A background daemon has
        nowhere useful to print, and a traceback can carry request data.
        """
        del request, client_address
        self.app._degrade("http-handler-crashed", "a request handler raised out of its own guard")


class _Handler(BaseHTTPRequestHandler):
    """One request. Every path through here answers or closes; none raises."""

    protocol_version = "HTTP/1.1"
    server_version = "embodiment"
    sys_version = ""

    @property
    def _app(self) -> "DashboardServer":
        return self.server.app  # type: ignore[attr-defined]

    def setup(self) -> None:  # noqa: D102  # stdlib hook
        super().setup()
        try:
            self.connection.settimeout(self._app.config.socket_timeout_s)
        except OSError as exc:
            del exc  # a socket that cannot take a timeout is about to fail anyway

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002  # stdlib signature
        """Never write an access log.

        A request line carries a path, a path can carry a query string, and a
        query string is attacker-controlled text. What a host needs instead is
        counted in :meth:`DashboardServer.status` — requests served, requests
        refused, refusals by code — which is the same information without the
        strings.
        """

    # ── methods ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802  # stdlib hook
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802  # stdlib hook
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802  # stdlib hook
        self._dispatch("POST")

    # ── routing ──────────────────────────────────────────────────────────────

    def _dispatch(self, method: str) -> None:
        app = self._app
        try:
            decision = app.guard.check(method, self.path, self.headers)
            if not decision.allowed:
                app._count_refusal(decision.code)
                self._refuse_unread(decision.status, decision.code, decision.reason)
                return
            app._count_request()
            self._route(method, decision)
        except Exception as exc:  # noqa: BLE001  # a handler that raises kills a thread silently
            app._degrade(
                "http-request-failed",
                f"a request could not be answered: {describe_exception(exc)}",
            )
            self._safe_error(500, "http-request-failed", "the request could not be answered")

    def _route(self, method: str, decision: Any = None) -> None:
        route = self.path.split("?", 1)[0].split("#", 1)[0]
        if route == STREAM_ROUTE:
            if method != "GET":
                self._refuse_unread(405, "http-method-not-allowed", "the stream is GET only")
                return
            self._stream(decision)
            return
        if route == "/api/status":
            if method not in ("GET", "HEAD"):
                self._refuse_unread(405, "http-method-not-allowed", "status is GET only")
                return
            self._status(head=method == "HEAD")
            return
        if _is_library_route(route):
            self._library_route(route, method)
            return
        if route in _CONTROL_ROUTES:
            if method != "POST":
                self._refuse_unread(405, "http-method-not-allowed", "this control is POST only")
                return
            if route in _TEXT_ROUTES:
                self._text_route(route)
            else:
                self._control(route)
            return
        if route.startswith("/api/"):
            self._refuse_unread(404, "http-unknown-route", "no such API route")
            return
        if method == "POST":
            self._refuse_unread(405, "http-method-not-allowed", "nothing here accepts a POST")
            return
        self._static(route, head=method == "HEAD")

    # ── responses ────────────────────────────────────────────────────────────

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        head: bool = False,
        extra: Optional[dict[str, str]] = None,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                # Said out loud, not only decided: a keep-alive client that is
                # not told will queue its next request on a socket about to
                # be closed under it (finding 8).
                self.send_header("Connection", "close")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if not head:
                self.wfile.write(body)
        except OSError as exc:
            del exc  # the client is gone; there is nobody left to answer
            self.close_connection = True

    def _send_json(self, status: int, payload: dict[str, Any], *, head: bool = False) -> None:
        try:
            body = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._app._degrade(
                "http-response-unserialisable",
                f"a response could not be encoded: {describe_exception(exc)}",
            )
            body = b'{"error": {"code": "http-response-unserialisable"}}'
            status = 500
        self._send(status, body, _JSON_CONTENT_TYPE, head=head)

    def _send_error(self, status: int, code: str, message: str) -> None:
        """``error.code`` and, for the web client's contract, ``code`` at the top too."""
        self._send_json(status, {"code": code, "error": {"code": code, "message": message}})

    def _refuse_unread(self, status: int, code: str, message: str) -> None:
        """An error answered BEFORE the request body was read.

        ``protocol_version`` is HTTP/1.1, so the socket stays open after an
        answer — and a body nobody read is still sitting in it, where the
        next ``handle_one_request`` parses it as a request line (the reviewer
        reproduced this live: a refused POST, then a valid GET on the same
        connection, answered 400 for the body's bytes). Draining the body
        instead would mean reading attacker-shaped bytes under the socket
        timeout for a request already refused; closing costs one reconnect
        and reads nothing. The unread bytes are discarded with the socket.
        """
        self.close_connection = True
        self._send_error(status, code, message)

    def _safe_error(self, status: int, code: str, message: str) -> None:
        """Answer even when the normal path is what failed. Never raises."""
        try:
            self._refuse_unread(status, code, message)
        except Exception as exc:  # noqa: BLE001  # last resort; the connection is closing
            del exc
            self.close_connection = True

    # ── the control API ──────────────────────────────────────────────────────

    def _read_body(
        self,
        *,
        limit: Optional[int] = None,
        require_length: bool = False,
        read_deadline_s: Optional[float] = None,
    ) -> Optional[bytes]:
        """The body, or ``None`` when it was refused (the answer is already sent).

        ``limit`` overrides the generic bound; ``require_length`` refuses a request with no
        ``Content-Length`` (or a chunked one) with 411 before reading; ``read_deadline_s``
        bounds the whole read, not each ``recv``.
        """
        limit = self._app.config.max_body_bytes if limit is None else limit
        if require_length and (
            self.headers.get("Content-Length") is None
            or self.headers.get("Transfer-Encoding") is not None
        ):
            self._app._degrade(LENGTH_REQUIRED_CODE, "an upload carried no usable Content-Length")
            self._refuse_unread(411, LENGTH_REQUIRED_CODE, "a Content-Length is required")
            return None
        raw_length = self.headers.get("Content-Length", "0") or "0"
        try:
            length = int(str(raw_length).strip())
        except ValueError:
            self._app._degrade(BAD_REQUEST_CODE, "a request carried an unparseable Content-Length")
            self._refuse_unread(400, BAD_REQUEST_CODE, "Content-Length is not a number")
            return None
        if length < 0 or length > limit:
            self._app._degrade(
                BODY_TOO_LARGE_CODE, f"a request body exceeded the {limit}-byte bound"
            )
            self.close_connection = True
            self._send_error(413, BODY_TOO_LARGE_CODE, "the request body is too large")
            return None
        try:
            if read_deadline_s is None:
                return self.rfile.read(length) if length else b""
            return self._read_bounded(length, read_deadline_s)
        except OSError as exc:
            self._app._degrade(
                BAD_REQUEST_CODE, f"a request body could not be read: {describe_exception(exc)}"
            )
            self.close_connection = True
            return None

    def _control(self, route: str) -> None:
        app = self._app
        controls = app.controls
        if route == "/api/mic/mute":
            body = self._read_body()
            if body is None:
                return
            muted = _parse_muted(body)
            if muted is None:
                self._send_error(
                    400, BAD_REQUEST_CODE, 'the body must be {"muted": true} or {"muted": false}'
                )
                return
            self._invoke(route, controls.set_mute, muted)
            return
        if route == NEW_CONVERSATION_ROUTE:
            self._new_conversation()
            return
        callable_ = controls.start_voice if route.endswith("start") else controls.stop_voice
        self._invoke(route, callable_)

    def _new_conversation(self) -> None:
        """A busy refusal is a 409, not a 200: the page must not reset on it."""
        outcome = self._app.call_control(
            "api.conversation.new", self._app.controls.new_conversation
        )
        if not outcome.ok:
            self._send_error(outcome.status, outcome.code, outcome.message)
            return
        result = outcome.result or {}
        if result.get("started") is False:
            self._send_error(409, CONVERSATION_BUSY_CODE, "a turn is in flight; try again")
            return
        self._send_json(200, {"ok": True, "result": result})

    def _read_bounded(self, length: int, deadline_s: float) -> Optional[bytes]:
        """*length* bytes inside a TOTAL deadline. ``None`` (answered) when it is not met.

        The deadline is enforced on every read, not between them: ``rfile.read(n)``
        blocks until *n* bytes arrive, so a client dripping one byte under the socket
        timeout could hold one 64 KiB read for hours (review round d6). Each read is
        a single ``read1`` (at most one ``recv``) under a socket timeout of the time
        left, and the connection's own timeout is restored afterwards.
        """
        end = time.monotonic() + deadline_s
        chunks: list[bytes] = []
        remaining = length
        restore = self._socket_timeout()
        try:
            while remaining > 0:
                chunk = self._read_chunk(min(_CLIP_CHUNK_BYTES, remaining), end)
                if chunk is None:
                    return self._refuse_slow_upload()
                if not chunk:
                    self._app._degrade(
                        BAD_REQUEST_CODE, "an upload ended before its Content-Length"
                    )
                    self.close_connection = True
                    self._send_error(400, BAD_REQUEST_CODE, "the upload was cut short")
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            self._set_socket_timeout(restore)
        return b"".join(chunks)

    def _read_chunk(self, size: int, end: float) -> Optional[bytes]:
        """Up to *size* bytes from ONE read, or ``None`` when the deadline passed first."""
        left = end - time.monotonic()
        if left <= 0:
            return None
        self._set_socket_timeout(left)
        read = getattr(self.rfile, "read1", self.rfile.read)
        try:
            return read(size)
        except TimeoutError:
            return None

    def _refuse_slow_upload(self) -> None:
        self._app._degrade(BAD_REQUEST_CODE, "an upload was not received in time")
        self.close_connection = True
        self._send_error(408, BAD_REQUEST_CODE, "the upload took too long")
        return None

    def _socket_timeout(self) -> Optional[float]:
        gettimeout = getattr(self.connection, "gettimeout", None)
        return gettimeout() if callable(gettimeout) else None

    def _set_socket_timeout(self, value: Optional[float]) -> None:
        settimeout = getattr(self.connection, "settimeout", None)
        if callable(settimeout):
            settimeout(value)

    # ── library and to-dos (task t6) ─────────────────────────────────────────

    def _todo_set_route(self, route: str, method: str) -> None:
        """``POST /api/todos/{id}`` ``{"done": bool}``: mark one to-do (d16)."""
        if method != "POST":
            self._refuse_unread(405, "http-method-not-allowed", "marking a to-do is POST only")
            return
        try:
            todo_id = unquote(route[len(TODOS_ROUTE) + 1 :], errors="strict")
        except UnicodeDecodeError:
            todo_id = ""
        body = self._read_body()
        if body is None:
            return
        payload = _parse_object(body)
        done = payload.get("done") if payload is not None else None
        if not todo_id or "/" in todo_id or not isinstance(done, bool):
            self._send_error(400, BAD_REQUEST_CODE, 'the body must be {"done": true|false}')
            return
        outcome = self._app.call_control(
            "api.todos.set", self._app.controls.todo_set, todo_id, done
        )
        if not outcome.ok:
            self._send_error(outcome.status, outcome.code, outcome.message)
            return
        result = outcome.result or {}
        refused = _TODO_REFUSALS.get(result.get("code") or "")
        if refused is not None or result.get("todo") is None:
            status, code, message = refused or _TODO_REFUSALS["todo-unknown"]
            self._send_error(status, code, message)
            return
        self._send_json(200, {"ok": True, "result": result})

    def _library_route(self, route: str, method: str) -> None:
        if route.startswith(TODOS_ROUTE + "/"):  # the one POST here: marking a to-do (d16)
            self._todo_set_route(route, method)
            return
        if method != "GET":
            self._refuse_unread(405, "http-method-not-allowed", "this route is GET only")
            return
        app = self._app
        config = app.config
        controls = app.controls
        if route == CONVERSATIONS_ROUTE or route.startswith(CONVERSATIONS_ROUTE + "/"):
            self._history_route(route)
            return
        if route == TODOS_ROUTE:
            self._read_route(
                "api.todos",
                controls.todos,
                (),
                config.todos_timeout_s,
                "groups",
                MEMORY_UNAVAILABLE_CODE,
                refusals=_TODOS_READ_REFUSALS,
            )
            return
        if route == LIBRARY_ROUTE:
            self._read_route(
                "api.library", controls.library_list, (), config.library_timeout_s, "items"
            )
            return
        if route == LIBRARY_SEARCH_ROUTE:
            query = _parse_query(self.path)
            if query is None:
                self._send_error(400, LIBRARY_BAD_REQUEST_CODE, "q must be 1-200 characters")
                return
            self._read_route(
                "api.library.search",
                controls.library_search,
                (query,),
                config.library_search_timeout_s,
                "hits",
            )
            return
        item_id = _parse_item_id(route[len(LIBRARY_ROUTE) + 1 :])
        if item_id is None:
            self._send_error(400, LIBRARY_BAD_REQUEST_CODE, "the item id must be an integer")
            return
        self._read_route(
            "api.library.item",
            controls.library_get,
            (item_id,),
            config.library_timeout_s,
            "passages",
            item=True,
        )

    def _history_route(self, route: str) -> None:
        """The history pane's two reads, on the text pool under the library deadline."""
        app = self._app
        controls = app.controls
        timeout_s = app.config.library_timeout_s
        if route == CONVERSATIONS_ROUTE:
            self._read_route(
                "api.conversations",
                controls.conversations,
                (),
                timeout_s,
                "conversations",
                HISTORY_NOT_CONFIGURED_CODE,
            )
            return
        conversation_id = route[len(CONVERSATIONS_ROUTE) + 1 :]
        action = controls.conversation
        if action is None:
            app._degrade(HISTORY_NOT_CONFIGURED_CODE, "api.conversation is not configured")
            self._send_error(
                503, HISTORY_NOT_CONFIGURED_CODE, "this is not configured on this daemon"
            )
            return
        outcome = app.call_text("api.conversation", action, conversation_id, timeout_s=timeout_s)
        if not outcome.ok:
            self._send_error(outcome.status, outcome.code, outcome.message)
            return
        result = outcome.result
        if not result:
            self._send_error(404, CONVERSATION_NOT_FOUND_CODE, "no such conversation")
            return
        if not (isinstance(result, dict) and isinstance(result.get("turns"), list)):
            app._degrade(LIBRARY_BAD_SHAPE_CODE, "api.conversation answered a bad shape")
            self._send_error(502, LIBRARY_BAD_SHAPE_CODE, "the answer was malformed")
            return
        self._send_json(200, result)

    def _read_route(
        self,
        label: str,
        action: Optional[Callable[..., Any]],
        args: tuple[Any, ...],
        timeout_s: float,
        list_key: str,
        missing_code: str = LIBRARY_NOT_CONFIGURED_CODE,
        *,
        item: bool = False,
        refusals: Optional[dict[str, tuple[int, str]]] = None,
    ) -> None:
        """One GET read on the text pool: pass through what the callable returned,
        after checking it has the contract's shape. A ``code`` the callable answered
        that *refusals* names is sent as that error instead."""
        app = self._app
        if action is None:
            app._degrade(missing_code, f"{label} is not configured")
            self._send_error(503, missing_code, "this is not configured on this daemon")
            return
        outcome = app.call_text(label, action, *args, timeout_s=timeout_s)
        if not outcome.ok:
            self._send_error(outcome.status, outcome.code, outcome.message)
            return
        result = outcome.result
        code = result.get("code") if isinstance(result, dict) else None
        refused = (refusals or {}).get(code) if isinstance(code, str) else None
        if refused is not None:
            self._send_error(refused[0], code, refused[1])
            return
        if item and not result:
            self._send_error(404, LIBRARY_NOT_FOUND_CODE, "no such library item")
            return
        valid = isinstance(result, dict) and isinstance(result.get(list_key), list)
        if valid and item:
            valid = isinstance(result.get("item"), dict)
        if not valid:
            app._degrade(LIBRARY_BAD_SHAPE_CODE, f"{_safe_token(label)} answered a bad shape")
            self._send_error(502, LIBRARY_BAD_SHAPE_CODE, "the answer was malformed")
            return
        self._send_json(200, result)

    def _text_route(self, route: str) -> None:
        app = self._app
        config = app.config
        controls = app.controls
        if route == CLIP_ROUTE:
            body = self._read_body(
                limit=config.clip_max_bytes,
                require_length=True,
                read_deadline_s=config.clip_read_deadline_s,
            )
            if body is None:
                return
            args = (body, *_clip_extras(self.headers))
            outcome = app.call_text(
                "api.clip", controls.clip, *args, timeout_s=config.clip_timeout_s
            )
            self._answer_text(outcome, ("transcript", "reply"), serial=True, sources=True)
            return
        body = self._read_body(limit=config.text_body_max_bytes)
        if body is None:
            return
        key = "message" if route == CHAT_ROUTE else "text"
        payload = _parse_object(body)
        if payload is None:
            self._send_error(400, BAD_REQUEST_CODE, f'the body must be {{"{key}": ...}}')
            return
        value = payload.get(key)
        if route == CHAT_ROUTE:
            # An optional "conversation" continues an earlier conversation (d5); without
            # one the turn joins the live session, as before.
            conversation = payload.get("conversation")
            args = (
                (value, conversation)
                if isinstance(conversation, str) and conversation
                else (value,)
            )
            outcome = app.call_text(
                "api.chat", controls.chat, *args, timeout_s=config.chat_timeout_s
            )
            self._answer_text(outcome, ("reply",), serial=True, sources=True)
            return
        outcome = app.call_text(
            "api.speak", controls.speak, value, timeout_s=config.speak_timeout_s
        )
        self._answer_speak(outcome)

    def _text_refusal(self, outcome: ControlOutcome) -> bool:
        """Answer a transport-level failure (unbound, slow, raised). ``True`` if answered."""
        if outcome.ok:
            return False
        self._send_error(outcome.status, outcome.code, outcome.message)
        return True

    def _layer_refusal(self, result: dict[str, Any]) -> bool:
        """Answer ``ok`` not true from the text layer with its own code and status."""
        if result.get("ok") is True:
            return False
        code = result.get("code")
        code = _safe_token(code) if isinstance(code, str) and code else TEXT_REFUSED_CODE
        status = result.get("http_status")
        if not isinstance(status, int) or isinstance(status, bool) or not 400 <= status <= 599:
            status = 502
        self._app._degrade(TEXT_REFUSED_CODE, f"a text route answered {status} ({code})")
        self._send_error(status, code, "the request was refused")
        return True

    def _answer_text(
        self,
        outcome: ControlOutcome,
        fields: tuple[str, ...],
        *,
        serial: bool = False,
        sources: bool = False,
    ) -> None:
        """A text route's 200. ``serial`` adds ``turn_serial`` (int or null; chat and
        clip both run a typed turn), ``sources`` the chat's cited records."""
        if self._text_refusal(outcome) or self._layer_refusal(outcome.result):
            return
        result = outcome.result
        reply: dict[str, Any] = {"ok": True, "code": ""}
        for name in fields:
            value = result.get(name, "")
            reply[name] = value if isinstance(value, str) else ""
        if sources:
            cited = result.get("sources")
            reply["sources"] = cited if isinstance(cited, list) else []
        if serial:
            number = result.get("turn_serial")
            usable = isinstance(number, int) and not isinstance(number, bool)
            reply["turn_serial"] = number if usable else None
        self._send_json(200, reply)

    def _answer_speak(self, outcome: ControlOutcome) -> None:
        if self._text_refusal(outcome) or self._layer_refusal(outcome.result):
            return
        audio = outcome.result.get("audio")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            self._app._degrade(TEXT_REFUSED_CODE, "a speak route had no audio to send")
            self._send_error(502, "speak-no-audio", "no audio was produced")
            return
        kind = outcome.result.get("content_type")
        kind = kind if isinstance(kind, str) and kind.startswith("audio/") else "audio/wav"
        self._send(200, bytes(audio), _safe_header_value(kind))

    def _invoke(self, route: str, action: Optional[Callable[..., Any]], *args: Any) -> None:
        label = route.replace("/", ".").strip(".")
        outcome = self._app.call_control(label, action, *args)
        if not outcome.ok:
            self._send_error(outcome.status, outcome.code, outcome.message)
            return
        self._send_json(200, {"ok": True, "result": outcome.result})

    def _status(self, *, head: bool) -> None:
        app = self._app
        # The same bounded path as every other control (lesson 8, one code
        # path): a status snapshot that hangs is exactly as able to pin a
        # thread as a start/stop that hangs.
        outcome = app.call_control("api.status", app.controls.status)
        if not outcome.ok:
            self._send_error(outcome.status, outcome.code, outcome.message)
            return
        self._send_json(200, {"daemon": outcome.result, "http": app.status()}, head=head)

    # ── static ───────────────────────────────────────────────────────────────

    def _static(self, route: str, *, head: bool) -> None:
        app = self._app
        dist = app.dist_dir
        if dist is None or not dist.is_dir():
            app._degrade(NO_DASHBOARD_CODE, "no web/dist directory is present to serve")
            self._send(
                200, NO_DASHBOARD_PAGE.encode("utf-8"), "text/plain; charset=utf-8", head=head
            )
            return
        resolved = _resolve_static(dist, route)
        if resolved is _REFUSED:
            app._degrade(STATIC_REFUSED_CODE, "a static path pointed outside web/dist")
            self._send_error(403, STATIC_REFUSED_CODE, "that path is not inside the dashboard")
            return
        if resolved is None or not isinstance(resolved, Path):
            self._send_error(404, "http-not-found", "no such file")
            return
        try:
            body = resolved.read_bytes()
        except OSError as exc:
            app._degrade(
                STATIC_UNREADABLE_CODE,
                f"a dashboard file could not be read: {describe_exception(exc)}",
            )
            self._send_error(404, "http-not-found", "no such file")
            return
        content_type = _CONTENT_TYPES.get(resolved.suffix.lower(), _DEFAULT_CONTENT_TYPE)
        self._send(200, body, content_type, head=head)

    # ── the stream ───────────────────────────────────────────────────────────

    def _stream(self, decision: Any = None) -> None:
        app = self._app
        bus = app.bus
        if bus is None:
            app._degrade(NO_BUS_CODE, "the stream was requested and no event bus is wired in")
            self._send_error(503, NO_BUS_CODE, "no event bus is attached to this server")
            return
        if app.closed:
            self._send_error(503, "http-shutting-down", "the server is shutting down")
            return
        if not app._acquire_stream():
            app._degrade(
                TOO_MANY_STREAMS_CODE,
                f"the {app.config.max_streams}-stream bound refused another subscriber",
            )
            self._send_error(503, TOO_MANY_STREAMS_CODE, "too many event streams are open")
            return
        subscription = None
        try:
            subscription = bus.subscribe(include_speech=app.config.include_speech)
            app._track(subscription)
            self._pump(subscription, self._expiry(decision))
        except Exception as exc:  # noqa: BLE001  # a dead stream must not kill the daemon
            app._degrade(STREAM_FAILED_CODE, f"a stream ended: {describe_exception(exc)}")
        finally:
            if subscription is not None:
                app._untrack(subscription)
                try:
                    bus.unsubscribe(subscription)
                except Exception as exc:  # noqa: BLE001  # teardown of a detached subscription
                    app._degrade(
                        STREAM_FAILED_CODE,
                        f"a stream could not be unsubscribed: {describe_exception(exc)}",
                    )
            app._release_stream()
            self.close_connection = True

    def _expiry(self, decision: Any) -> Optional[float]:
        """When this stream must end (epoch seconds), or ``None`` for "never".

        A secret-admitted stream does not expire. An Access-admitted one ends when the
        assertion's ``exp`` passes - or, when the verifier reported none, after
        :attr:`ServerConfig.access_stream_max_s` (the conservative choice).
        """
        if getattr(decision, "admitted_by", "") != guard_module.ADMITTED_BY_ACCESS:
            return None
        config = self._app.config
        exp = getattr(decision, "expires_at", None)
        if isinstance(exp, (int, float)) and math.isfinite(exp):
            return float(exp)
        return config.wall_clock() + config.access_stream_max_s

    def _pump(self, subscription: Any, expires_at: Optional[float] = None) -> None:
        """Write events until the client goes, the server stops, it breaks, or its token expires."""
        app = self._app
        self._open_stream_response()
        if not self._write(": stream open\n\n"):
            return
        last_write: Optional[float] = time.monotonic()
        while last_write is not None and not app.stopping:
            last_write = self._pump_once(subscription, expires_at, last_write)

    def _pump_once(
        self, subscription: Any, expires_at: Optional[float], last_write: float
    ) -> Optional[float]:
        """One wait and what it brought. The new last-write time, or ``None`` to stop."""
        wait_s = self._stream_wait(expires_at)
        if wait_s is None:
            return None
        event = subscription.get(timeout=wait_s)
        now = time.monotonic()
        if event is None:
            if now - last_write < self._app.config.keepalive_s:
                return last_write
            return now if self._write(": keepalive\n\n") else None
        written = self._emit(event)
        if written is False:
            return None
        return now if written else last_write

    def _open_stream_response(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True

    def _stream_wait(self, expires_at: Optional[float]) -> Optional[float]:
        """How long the next ``get`` may wait; ``None`` once the token expired (said so)."""
        config = self._app.config
        if expires_at is None:
            return config.stream_poll_s
        left = expires_at - config.wall_clock()
        if left <= 0:
            self._app._count_stream_expired()
            self._write(f": {STREAM_EXPIRED_REASON}\n\n")
            return None
        return min(config.stream_poll_s, max(0.01, left))

    def _emit(self, event: Any) -> Optional[bool]:
        """Write one event: ``True`` written, ``None`` dropped (a secret), ``False`` gone."""
        app = self._app
        payload = event.to_json()
        if any(form in payload for form in app.redaction_forms):
            app._count_secret_drop()
            app._degrade(
                SECRET_IN_STREAM_CODE,
                "an event carrying a configured secret was dropped from the stream",
            )
            return None
        frame = f"event: {_safe_token(event.kind)}\nid: {int(event.seq)}\ndata: {payload}\n\n"
        if not self._write(frame):
            return False
        app._count_event()
        return True

    def _write(self, text: str) -> bool:
        """``True`` when the client took it. A disconnect is normal, not a fault."""
        try:
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()
            return True
        except TimeoutError:
            # The client is still connected and has stopped reading: its
            # receive window is full and the write blocked past the socket
            # timeout. Recorded, because a wedged dashboard looks exactly
            # like a healthy one from the outside.
            self._app._count_stream_timeout()
            self._app._degrade(
                STREAM_WRITE_TIMEOUT_CODE,
                "a stream was dropped after a write blocked past the socket timeout",
            )
            return False
        except (BrokenPipeError, ConnectionResetError):
            # An ordinary disconnect. Counted, not recorded: a browser tab
            # closing is not a degradation.
            self._app._count_disconnect()
            return False
        except OSError as exc:
            self._app._degrade(
                STREAM_FAILED_CODE, f"a stream write failed: {describe_exception(exc)}"
            )
            return False


_REFUSED = object()


def redaction_forms(secrets: Any) -> tuple[str, ...]:
    """Every literal spelling *secrets* can take inside a serialised event.

    Round 3 review: the filter compared the RAW secret against
    ``Event.to_json()``. A secret containing ``"`` or ``\\`` is escaped in the
    payload — ``he"llo`` is written ``he\\"llo`` — so the raw substring never
    appeared and the event streamed out intact. Both spellings are checked
    now. ``json.dumps(secret)[1:-1]`` is the string as JSON would write it,
    with the surrounding quotes stripped; it is only added when it differs, so
    an ordinary key stays one comparison.

    ``Bus`` serialises with ``ensure_ascii=False`` and this helper's encoded
    form is ASCII-escaped, which is why both are kept rather than one: they
    are the two spellings a non-ASCII secret can arrive in.
    """
    forms: list[str] = []
    for secret in secrets or ():
        text = str(secret)
        if not text:
            continue
        forms.append(text)
        try:
            encoded = json.dumps(text)[1:-1]
        except (TypeError, ValueError):
            continue
        if encoded != text:
            forms.append(encoded)
    return tuple(forms)


@dataclass(frozen=True)
class ControlOutcome:
    """What one bounded control call produced. ``ok`` decides which half is set."""

    ok: bool
    result: dict[str, Any]
    status: int
    code: str
    message: str


#: How a refused mark is answered (review of PR #103): a save that failed is not a
#: success the page could keep showing, and a malformed id is the caller's error.
#: ``todos()`` codes that are refusals, not a list: the page shows the code (c22).
_TODOS_READ_REFUSALS: dict[str, tuple[int, str]] = {
    TODOS_UNAVAILABLE_CODE: (503, "the to-dos could not be read from the library"),
    LIBRARY_NOT_CONFIGURED_CODE: (503, "the to-dos come from the library, not configured here"),
}

_TODO_REFUSALS: dict[str, tuple[int, str, str]] = {
    "todo-unknown": (404, "todo-unknown", "no such to-do"),
    "todo-bad-request": (400, "todo-bad-request", "not a to-do id"),
    "todo-not-saved": (503, "todo-not-saved", "the to-do's state could not be saved"),
}


def _is_library_route(route: str) -> bool:
    """The guarded reads (the library, the to-dos, the history), and marking a to-do."""
    if route in (LIBRARY_ROUTE, TODOS_ROUTE, CONVERSATIONS_ROUTE):
        return True
    return route.startswith((LIBRARY_ROUTE + "/", CONVERSATIONS_ROUTE + "/", TODOS_ROUTE + "/"))


def _parse_item_id(segment: str) -> Optional[int]:
    """``{id}`` from a library path: percent-decoded, ASCII digits only, length-capped."""
    try:
        text = unquote(segment, errors="strict") if segment.isascii() else ""
    except UnicodeDecodeError:
        return None
    if not text.isascii() or not text.isdigit() or len(text) > LIBRARY_ID_MAX_DIGITS:
        return None
    return int(text)


def _parse_query(path: str) -> Optional[str]:
    """``q`` from the request path's query string, or ``None`` when absent, empty,
    repeated, undecodable or over :data:`LIBRARY_QUERY_MAX_CHARS`."""
    _route, _sep, rest = path.partition("?")
    rest = rest.split("#", 1)[0]
    values = [pair[2:] for pair in rest.split("&") if pair.startswith("q=")]
    if len(values) != 1 or len(values[0]) > LIBRARY_QUERY_MAX_CHARS * 12:
        return None
    try:
        text = unquote(values[0].replace("+", " "), errors="strict").strip()
    except UnicodeDecodeError:
        return None
    if not text or len(text) > LIBRARY_QUERY_MAX_CHARS or not text.isprintable():
        return None
    return text


def _safe_token(kind: object) -> str:
    """A restricted token: an SSE event name, or a control's label in a record.

    One sanitiser for both (lesson 8). An SSE event name that could carry a
    newline would inject a frame; a label that could carry anything at all
    would be the one attacker-shaped string in an otherwise fixed reason. Both
    are ``[A-Za-z0-9._-]`` and capped.
    """
    text = str(kind)
    return (
        "".join(character for character in text if character.isalnum() or character in "._-")[:40]
        or "event"
    )


def _parse_object(body: bytes) -> Optional[dict[str, Any]]:
    """A JSON object, or ``None`` ("refuse this")."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _clip_extras(headers: Any) -> tuple[Any, ...]:
    """The clip callable's arguments after the body: the declared duration, then —
    only when sent — the conversation it continues (d5), the clip's origin (d7) and
    the page's time for it (d12, epoch seconds). Trailing ones that were not sent are
    left off. The daemon checks the origin word; anything unknown is logged ``clip``."""
    tail = [
        headers.get(CONVERSATION_HEADER) or None,
        headers.get(CLIP_ORIGIN_HEADER) or None,
        _clip_at(headers.get(CLIP_AT_HEADER)),
    ]
    while tail and tail[-1] is None:
        tail.pop()
    return (_declared_duration(headers.get("X-Clip-Duration-S")), *tail)


def _clip_at(raw: Any) -> Optional[float]:
    """``X-Clip-At`` (epoch milliseconds) as epoch seconds, or None if unusable."""
    try:
        value = float(raw) / 1000.0
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _declared_duration(raw: Optional[str]) -> Optional[float]:
    """``X-Clip-Duration-S`` as a finite, non-negative float; anything else is ``None``."""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _safe_header_value(value: str) -> str:
    """A content type with no control characters (it came from a callable)."""
    return "".join(ch for ch in value if ch.isprintable())[:100] or "audio/wav"


def _parse_muted(body: bytes) -> Optional[bool]:
    """``{"muted": bool}`` and nothing else. ``None`` means "refuse this"."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError and JSONDecodeError both derive from it
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("muted")
    return value if isinstance(value, bool) else None


def _resolve_static(dist: Path, route: str) -> Any:
    """The file *route* names inside *dist*.

    Returns a :class:`Path` to serve, ``None`` for "no such file", or
    :data:`_REFUSED` for a path that pointed outside the directory. The check
    is done on the **real** path, after ``unquote`` and after symlinks, so
    ``%2e%2e``, ``..`` and a symlink out of ``dist`` are all one refusal.
    """
    if len(route) > _MAX_PATH_CHARS:
        return None
    try:
        decoded = unquote(route, errors="replace")
    except Exception as exc:  # noqa: BLE001  # an undecodable path names no file
        del exc
        return None
    if any(unicodedata.category(ch) in _FORBIDDEN_PATH_CATEGORIES for ch in decoded):
        return _REFUSED
    parts = [part for part in decoded.split("/") if part not in ("", ".")]
    if not parts:
        parts = ["index.html"]
    if any(part == ".." or "\\" in part for part in parts):
        return _REFUSED
    candidate = dist.joinpath(*parts)
    try:
        real_root = os.path.realpath(dist)
        real_target = os.path.realpath(candidate)
    except OSError as exc:
        del exc  # an unresolvable path is not a file we will serve
        return None
    if real_target != real_root and not real_target.startswith(real_root + os.sep):
        return _REFUSED
    resolved = Path(real_target)
    return resolved if resolved.is_file() else None


class DashboardServer:
    """The HTTP surface. Constructed bound, started explicitly, stopped once.

    Construction binds the socket, so a port already in use or a refused bind
    address is a :class:`CliError` the CLI can render — before anything claims
    to be serving. Everything after that degrades and records instead.
    """

    def __init__(
        self,
        *,
        config: ServerConfig,
        guard: guard_module.Guard,
        bus: Any = None,
        controls: Optional[Controls] = None,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.config = config
        self.guard = guard
        self.bus = bus
        self.controls = controls or Controls()
        self._on_degrade = on_degrade

        self.dist_dir: Optional[Path] = (
            Path(config.dist_dir) if config.dist_dir is not None else default_dist_dir()
        )

        #: Every literal spelling a configured secret can take in a streamed
        #: event, computed once. See :func:`redaction_forms`.
        self.redaction_forms = redaction_forms(config.redact)

        self._lock = threading.Lock()
        self._counters = _Counters()
        self.degradations: list[dict[str, str]] = []
        self.degradation_counts: dict[str, int] = {}
        self.degradations_dropped = 0
        self.hook_errors = 0

        self._streams_open = 0
        self._subscriptions: set[Any] = set()

        #: Control callables run here, never on the request thread, so a
        #: blocking one costs an abandoned pool task rather than a pinned
        #: connection. Bounded by ``control_workers`` and, for the abandoned
        #: ones, by :data:`MAX_CONTROL_INFLIGHT`.
        self._control_lane = _Lane(
            "control",
            ThreadPoolExecutor(
                max_workers=max(1, int(config.control_workers)),
                thread_name_prefix="embodiment-control",
            ),
            MAX_CONTROL_INFLIGHT,
        )
        #: The text routes' own pool (review round d6): a typed turn holds a
        #: worker for up to the chat bound, which must never be the mute's wait.
        self._text_lane = _Lane(
            "text",
            ThreadPoolExecutor(
                max_workers=max(1, int(config.text_workers)),
                thread_name_prefix="embodiment-text",
            ),
            MAX_TEXT_INFLIGHT,
        )

        self._stopping = threading.Event()
        self._closed = False
        self._close_lock = threading.Lock()
        self._serve_thread: Optional[threading.Thread] = None

        bind = resolve_bind(config.bind, bind_public=config.bind_public)
        try:
            self._httpd: Optional[_HttpServer] = _HttpServer(
                (bind, int(config.port)), _Handler, self
            )
        except OSError as exc:
            raise CliError(
                code=EXIT_ENV_ERROR,
                message=(
                    f"could not bind {bind}:{config.port} for the dashboard: "
                    f"{describe_exception(exc)}"
                ),
                remediation=("stop whatever is already listening there, or choose another port"),
            ) from exc

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def port(self) -> int:
        """The bound port — the real one, when ``port=0`` asked for any."""
        if self._httpd is None:
            return int(self.config.port)
        return int(self._httpd.server_address[1])

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        """Serve in a background daemon thread. Idempotent; never raises."""
        with self._close_lock:
            if self._closed or self._httpd is None or self._serve_thread is not None:
                return
            thread = threading.Thread(target=self._serve, name="embodiment-http", daemon=True)
            self._serve_thread = thread
        thread.start()

    def serve_forever(self) -> None:
        """Serve on the calling thread until :meth:`shutdown`. Never raises."""
        self._serve()

    def _serve(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        try:
            httpd.serve_forever(poll_interval=0.1)
        except Exception as exc:  # noqa: BLE001  # the accept loop dying must be visible
            self._degrade(
                "http-accept-loop-failed", f"the accept loop ended: {describe_exception(exc)}"
            )

    def shutdown(self, deadline: float = 2.0) -> ServerCloseReport:
        """Stop serving within *deadline*. Idempotent, never raises, reports.

        Order matters: the stop flag first (so a stream thread that wakes next
        exits rather than writing into a closing socket), then every live
        subscription closed (which wakes each stream out of its bounded
        ``get`` immediately rather than after one poll interval), then the
        accept loop, then the listening socket.
        """
        started = time.monotonic()
        with self._close_lock:
            if self._closed:
                return ServerCloseReport(
                    already_closed=True,
                    streams_closed=0,
                    serve_thread_stopped=True,
                    elapsed_s=time.monotonic() - started,
                )
            self._closed = True
            thread, self._serve_thread = self._serve_thread, None
            httpd, self._httpd = self._httpd, None
        self._stopping.set()

        with self._lock:
            subscriptions = list(self._subscriptions)
            self._subscriptions.clear()
        for subscription in subscriptions:
            try:
                subscription.close()
            except Exception as exc:  # noqa: BLE001  # a subscription that will not close is noted
                self._degrade(
                    STREAM_FAILED_CODE,
                    f"a stream subscription would not close: {describe_exception(exc)}",
                )

        with self._lock:
            unfinished = self._control_lane.inflight + self._text_lane.inflight
        for lane in (self._control_lane, self._text_lane):
            self._close_lane(lane)

        stopped = self._stop_accept_loop(httpd, thread, deadline, started)
        return ServerCloseReport(
            already_closed=False,
            streams_closed=len(subscriptions),
            serve_thread_stopped=stopped,
            elapsed_s=time.monotonic() - started,
            controls_unfinished=unfinished,
        )

    def _close_lane(self, lane: _Lane) -> None:
        try:
            # Never ``wait=True``: an abandoned control would hold shutdown for
            # as long as it likes, which is the defect this whole seam exists
            # to close. Queued-but-unstarted calls are cancelled; a running one
            # keeps its daemon thread and dies with the process.
            lane.pool.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:  # noqa: BLE001  # a pool that will not close is recorded
            self._degrade(
                "http-control-pool-close-failed",
                f"the {lane.name} pool would not close: {describe_exception(exc)}",
            )

    def _stop_accept_loop(
        self,
        httpd: Optional[_HttpServer],
        thread: Optional[threading.Thread],
        deadline: float,
        started: float,
    ) -> bool:
        """Bounded joins only. ``True`` when the serving thread really ended."""
        if httpd is None:
            return True

        def remaining() -> float:
            return max(0.05, deadline - (time.monotonic() - started))

        if thread is not None and thread is threading.current_thread():
            # shutdown() from inside a handler would deadlock on its own loop.
            httpd.__dict__["_BaseServer__shutdown_request"] = True
        else:
            stopper = threading.Thread(target=httpd.shutdown, daemon=True)
            stopper.start()
            stopper.join(remaining())
        try:
            httpd.server_close()
        except Exception as exc:  # noqa: BLE001  # a socket that will not close is recorded
            self._degrade(
                "http-socket-close-failed",
                f"the listening socket would not close: {describe_exception(exc)}",
            )
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(remaining())
        return not thread.is_alive()

    # ── controls, on a deadline the caller does not have to trust ────────────

    def call_control(
        self,
        label: str,
        action: Optional[Callable[..., Any]],
        *args: Any,
        timeout_s: Optional[float] = None,
    ) -> ControlOutcome:
        """Run *action* under :attr:`ServerConfig.control_timeout_s`. Never raises.

        The contract ``Controls`` states — "none of them may block" — is
        enforced here rather than trusted. The callable runs on the control
        pool; the caller waits the deadline and then answers without it. An
        overrunning callable is **abandoned, not cancelled**: Python cannot
        kill a running thread, so it keeps its pool slot until it returns on
        its own, which is exactly why the outstanding count is bounded and why
        exhausting it is its own recorded refusal rather than a slow leak
        nobody sees.

        Four refusals, each naming the fault a host would look for:
        :data:`CONTROL_UNBOUND_CODE` (t15 has not bound it),
        :data:`CONTROL_BUSY_CODE` (too many outstanding),
        :data:`CONTROL_TIMEOUT_CODE` (it did not answer in time) and
        :data:`CONTROL_FAILED_CODE` (it raised).
        """
        return self._call_on(self._control_lane, label, action, args, timeout_s)

    def call_text(
        self,
        label: str,
        action: Optional[Callable[..., Any]],
        *args: Any,
        timeout_s: Optional[float] = None,
    ) -> ControlOutcome:
        """:meth:`call_control` for the text routes, on their OWN bounded pool.

        Same four refusals with the same codes and shapes; only the pool and its
        outstanding bound (:data:`MAX_TEXT_INFLIGHT`) differ, so a saturated
        text pool answers busy without costing the mute or stop a worker.
        """
        return self._call_on(self._text_lane, label, action, args, timeout_s)

    def _call_on(
        self,
        lane: _Lane,
        label: str,
        action: Optional[Callable[..., Any]],
        args: tuple[Any, ...],
        timeout_s: Optional[float],
    ) -> ControlOutcome:
        if action is None:
            self._degrade(CONTROL_UNBOUND_CODE, f"{_safe_token(label)} is not bound")
            return ControlOutcome(
                ok=False,
                result={},
                status=503,
                code=CONTROL_UNBOUND_CODE,
                message="this control is not bound to a running daemon yet",
            )
        if not self._admit(lane):
            self._degrade(
                CONTROL_BUSY_CODE,
                f"{lane.max_inflight} {lane.name} calls are already outstanding; "
                "an earlier one has not returned",
            )
            return ControlOutcome(
                ok=False,
                result={},
                status=503,
                code=CONTROL_BUSY_CODE,
                message="too many control calls are outstanding",
            )

        try:
            future: Future[Any] = lane.pool.submit(action, *args)
        except RuntimeError as exc:
            # The pool is shut down: this arrives during teardown.
            self._release(lane)
            self._degrade(
                CONTROL_FAILED_CODE,
                f"{_safe_token(label)} could not be started: {describe_exception(exc)}",
            )
            return ControlOutcome(
                ok=False,
                result={},
                status=503,
                code=CONTROL_FAILED_CODE,
                message="the server is shutting down",
            )
        future.add_done_callback(lambda _future: self._release(lane))
        return self._await_control(label, future, timeout_s)

    def _admit(self, lane: _Lane) -> bool:
        """Take one outstanding slot on *lane*, or count the busy refusal."""
        with self._lock:
            if lane.inflight < lane.max_inflight:
                lane.inflight += 1
                return True
            self._counters.controls_refused_busy += 1
            return False

    def _await_control(
        self, label: str, future: "Future[Any]", timeout_s: Optional[float] = None
    ) -> ControlOutcome:
        """Wait the deadline, then answer with or without the callable.

        ``wait()`` rather than ``future.result(timeout=…)`` deliberately: in
        3.11+ ``concurrent.futures.TimeoutError`` *is* ``TimeoutError``, so a
        control that itself raised ``TimeoutError`` (a socket deadline inside
        the daemon) would be reported as a control that never answered. Asking
        whether the future is done separates the two.
        """
        deadline = max(
            0.0, float(self.config.control_timeout_s if timeout_s is None else timeout_s)
        )
        done, _pending = wait([future], timeout=deadline)
        if not done:
            with self._lock:
                self._counters.controls_timed_out += 1
            self._degrade(
                CONTROL_TIMEOUT_CODE,
                f"{_safe_token(label)} did not return within {deadline}s and was abandoned",
            )
            return ControlOutcome(
                ok=False,
                result={},
                status=503,
                code=CONTROL_TIMEOUT_CODE,
                message="the control did not answer in time",
            )
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001  # a control's fault is not the server's death
            self._degrade(
                CONTROL_FAILED_CODE, f"{_safe_token(label)} raised: {describe_exception(exc)}"
            )
            return ControlOutcome(
                ok=False,
                result={},
                status=500,
                code=CONTROL_FAILED_CODE,
                message="the control failed; see the daemon's log",
            )
        return ControlOutcome(
            ok=True,
            result=result if isinstance(result, dict) else {},
            status=200,
            code="",
            message="",
        )

    def _release(self, lane: _Lane) -> None:
        """One outstanding call on *lane* finished — on time or long after."""
        with self._lock:
            lane.inflight = max(0, lane.inflight - 1)

    # ── bookkeeping ──────────────────────────────────────────────────────────

    def _degrade(self, code: str, reason: str) -> None:
        """Record one transition. Bounded, deduped, and never raises."""
        notify = False
        with self._lock:
            known = code in self.degradation_counts
            if not known and len(self.degradation_counts) >= MAX_DEGRADATION_CODES:
                self.degradations_dropped += 1
            else:
                self.degradation_counts[code] = self.degradation_counts.get(code, 0) + 1
                if not known:
                    self.degradations.append({"code": code, "reason": reason})
                    notify = True
        if notify and self._on_degrade is not None:
            try:
                self._on_degrade(code, reason)
            except Exception:  # noqa: BLE001  # a broken sink is counted, not raised
                with self._lock:
                    self.hook_errors += 1

    def _count_request(self) -> None:
        with self._lock:
            self._counters.requests_served += 1

    def _count_refusal(self, code: str) -> None:
        with self._lock:
            self._counters.requests_refused += 1
            self._counters.refusals_by_code[code] = self._counters.refusals_by_code.get(code, 0) + 1

    def _count_event(self) -> None:
        with self._lock:
            self._counters.events_streamed += 1

    def _count_disconnect(self) -> None:
        with self._lock:
            self._counters.streams_disconnected += 1

    def _count_stream_expired(self) -> None:
        with self._lock:
            self._counters.streams_expired += 1

    def _count_stream_timeout(self) -> None:
        with self._lock:
            self._counters.streams_timed_out += 1

    def _count_secret_drop(self) -> None:
        with self._lock:
            self._counters.events_dropped_for_secret += 1

    def _acquire_stream(self) -> bool:
        with self._lock:
            if self._streams_open >= max(1, int(self.config.max_streams)):
                self._counters.streams_refused += 1
                return False
            self._streams_open += 1
            self._counters.streams_started += 1
            return True

    def _release_stream(self) -> None:
        with self._lock:
            self._streams_open = max(0, self._streams_open - 1)
            self._counters.streams_finished += 1

    def _track(self, subscription: Any) -> None:
        with self._lock:
            self._subscriptions.add(subscription)

    def _untrack(self, subscription: Any) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)

    def status(self) -> dict[str, Any]:
        """A JSON-serialisable snapshot. Carries no secret and no path a
        client chose — only this server's own configuration and counters."""
        with self._lock:
            counters = dict(vars(self._counters))
            counters["refusals_by_code"] = dict(self._counters.refusals_by_code)
            degradations = [dict(entry) for entry in self.degradations]
            counts = dict(self.degradation_counts)
            dropped = self.degradations_dropped
            hook_errors = self.hook_errors
            streams_open = self._streams_open
            controls_inflight = self._control_lane.inflight
            text_inflight = self._text_lane.inflight
        return {
            "bind": self.config.bind,
            "port": self.port,
            "public_bind": bool(self.config.bind_public),
            "dashboard": "present" if self.dist_dir and self.dist_dir.is_dir() else "absent",
            "dist_dir": str(self.dist_dir) if self.dist_dir is not None else None,
            "include_speech": bool(self.config.include_speech),
            "max_streams": int(self.config.max_streams),
            "streams_open": streams_open,
            "controls_inflight": controls_inflight,
            "text_inflight": text_inflight,
            "control_timeout_s": float(self.config.control_timeout_s),
            "closed": self._closed,
            "stopping": self._stopping.is_set(),
            "degradations": degradations,
            "degradation_counts": counts,
            "degradations_dropped": dropped,
            "hook_errors": hook_errors,
            **counters,
            # The guard's Access counters (t5): admitted, refused per code.
            **_guard_status(self.guard),
        }
