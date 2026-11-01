"""embodiment.mesh_client — Gwen's own mesh client: stdlib IRC to agentirc.

One connection per daemon, ``asyncio`` streams only, no ``culture`` CLI
subprocess. The daemon runs the client on its own loop thread
(:meth:`MeshClient.start`) and is the only sender as the configured nick
(``spark-gwen`` by default); :meth:`send`, :meth:`create_thread` and
:meth:`reply_thread` are safe to call from any thread.

The wire, as agentirc answers it (read from ``agentirc/client.py`` and
``agentirc/protocol.py``, not guessed):

* **Nick.** agentirc refuses any nick that does not start with
  ``<servername>-`` and reserves ``system-``, both with ``432``
  (ERR_ERRONEUSNICKNAME); a nick already held gets ``433``
  (ERR_NICKNAMEINUSE). The nick comes from :class:`MeshConfig` only — never
  from a model name. ``433`` records ``mesh-nick-taken`` and ``432`` records
  ``mesh-nick-refused``; either way the client sends nothing more and stays
  off until the daemon restarts. A ghost of Gwen's own previous connection that
  the server has not yet reaped also answers ``433`` — that is reported as a
  taken nick too, because the client cannot tell the two apart.
* **Capabilities.** agentirc advertises ``message-tags`` (and its bot cap). The
  client requests ``message-tags`` at registration so delivered PRIVMSGs carry
  ``msgid``, ``time`` and, inside a thread, ``agentirc.io/thread``. Registration
  is not gated on ``CAP END`` there; the client sends it anyway.
* **Threads.** ``THREAD CREATE #chan <name> :text`` and ``THREAD REPLY #chan
  <name> :text`` (``agentirc/skills/threads.py``); names match
  :data:`THREAD_NAME_RE`. Delivered thread messages carry the tag and, for
  compatibility, a ``[thread:<name>] `` text prefix, which is stripped here.
* **No reply tags.** agentirc builds every relayed PRIVMSG with its own
  ``msgid``/``time`` tags and drops client-sent tags, and has no
  ``echo-message`` — so ``+draft/reply`` does not exist on this wire and a
  sender never learns its own message's ``msgid``. Matching an answer to an ask
  goes through a thread name, which the sender chooses.
* **Servers visible.** agentirc has no ``LINKS``. What it does answer is
  ``WHO #channel``: each ``352`` carries the member's server name (a federated
  member's real peer server, not this one). :meth:`MeshClient.servers` is the
  server named in ``001``'s prefix plus every server in the latest completed
  ``WHO`` of each joined channel, refreshed when someone joins, parts or quits.
  It is the servers *visible on this connection's channels*, not the topology.
* **Line length.** agentirc rejects an inbound line over 8192 bytes
  (``MAX_INBOUND_LINE``), so :data:`MAX_LINE_BYTES` is that number and every
  outbound line is chunked under it, CRLF included. Text never reaches the wire
  with a CR, LF or NUL in it: each line becomes its own message.

* **Presence** (culture ``protocol/extensions/presence.md``; agentirc
  ``skills/presence.py``; asked in embodiment#121). After ``001`` the client sets
  user mode ``+A`` — agentirc flips a row to ``offline`` on a TCP close only for a
  ``+A``/``+C`` client, so without it a crashed daemon stays listed in its last
  state — and publishes ``PRESENCE :{"state": ..., "since": ...}``, again on every
  state change. The state comes only from code boundaries, never from a model:
  :class:`PresenceGauge` holds ``thinking`` while any completion through the
  daemon's chat seam is in flight and ``idle`` otherwise; a graceful close sends
  ``draining`` before ``QUIT``. ``offline`` is the server's to set. A server that
  answers ``421`` to ``PRESENCE`` or ``501`` to the mode is recorded once
  (``mesh-presence-unsupported`` / ``mesh-mode-refused``); after a ``421`` that
  connection publishes no more PRESENCE, and the client goes on.

* **Account (SASL PLAIN; agentirc 9.14 ``docs/accounts.md``; embodiment#121 part 2).**
  With a secret configured (:data:`ENV_AGENT_SECRET`, taken out of the
  environment once by :func:`take_agent_secret`), the client opens with
  ``CAP LS 302``, sends NICK and USER, asks ``CAP REQ :sasl message-tags``,
  answers ``AUTHENTICATE +`` with ``base64("\\0<nick>\\0<secret>")`` in
  400-byte chunks, and sends ``CAP END`` on ``903``; the server holds ``001``
  until then. ``904``/``905``/``908``/``902`` are permanent: ``mesh-sasl-refused``,
  and the client stays off until the daemon restarts (culture's own agents exit
  78; Gwen's daemon keeps her voice and page, so only the mesh lane stops). ``906``
  (aborted) is retried with the usual backoff. A server that does not advertise
  ``sasl`` records ``mesh-sasl-unavailable`` once and is joined as before (no flag
  day). Without a secret the wire is byte-identical to the unauthenticated one.
  The secret is never in a record, a status, a ``repr`` or a log.

Clocks, each derived from what it bounds:

* ``connect_deadline`` bounds the TCP connect, ``register_deadline`` the wait
  for ``001``. The reconnect delay doubles from ``backoff_initial`` and is
  capped at ``backoff_max``, so :attr:`MeshConfig.reconnect_bound` —
  ``backoff_max + connect_deadline + register_deadline`` — is the worst-case
  time between the server returning and Gwen being registered again.
* ``ping_interval`` of inbound silence sends a PING; ``ping_timeout`` more
  without any line declares the connection dropped. agentirc pings an idle
  client after 60 s and reaps it at 180 s, so a client that never sent its own
  PING would learn of a silently dead server only from the kernel; the client
  side bound is :attr:`MeshConfig.liveness_bound`.
* ``presence_heartbeat`` re-sends a busy state's PRESENCE. agentirc's
  ``heartbeat_interval_seconds`` defaults to 30 and its stale threshold to 90 (a
  busy row older than that is flagged ``presumed_hung``), so 25 s keeps a row
  fresh with one beat of slack. ``idle`` never goes stale and is not re-sent.

Degrade, never raise: a refused connect, a drop, a timeout, a failing
callback — each is a :class:`MeshDegradation` on a bounded ledger and a
running daemon. Reasons are fixed vocabulary or
:func:`embodiment.safe_reason.describe_exception`; they never carry message
text, a nick a remote party chose, or a secret.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Mapping, Optional

from embodiment.safe_reason import describe_exception

__all__ = [
    "DEFAULT_CHANNELS",
    "DEFAULT_NICK",
    "DEFAULT_SERVER",
    "ENV_AGENT_SECRET",
    "ENV_CHANNELS",
    "ENV_MESH",
    "ENV_NICK",
    "ENV_SERVER",
    "MAX_LINE_BYTES",
    "PRESENCE_BUSY",
    "PRESENCE_STATES",
    "THREAD_NAME_RE",
    "IrcLine",
    "MeshClient",
    "MeshCloseReport",
    "MeshConfig",
    "MeshDegradation",
    "MeshMessage",
    "PresenceGauge",
    "parse_line",
    "take_agent_secret",
]

ENV_MESH = "EMBODIMENT_MESH"
ENV_NICK = "EMBODIMENT_MESH_NICK"
ENV_SERVER = "EMBODIMENT_MESH_SERVER"
ENV_CHANNELS = "EMBODIMENT_MESH_CHANNELS"
#: Gwen's mesh account secret, injected by ``grant run --inject`` in her unit
#: (culture#506). Read ONCE and removed from the environment: :func:`take_agent_secret`.
ENV_AGENT_SECRET = "CULTURE_AGENT_SECRET"  # nosec B105 - an env var NAME, not a secret

DEFAULT_NICK = "spark-gwen"
DEFAULT_SERVER = "localhost:6667"
DEFAULT_CHANNELS = ("#general",)

#: agentirc's ``MAX_INBOUND_LINE`` (``agentirc/_internal/constants.py``).
MAX_LINE_BYTES = 8192
#: agentirc's thread-name regex (``agentirc/skills/threads.py``).
THREAD_NAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,30}[a-zA-Z0-9])?$")
_NICK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-\[\]\\`^{}|]{0,63}$")
_CHANNEL_RE = re.compile(r"^#[^\s,\x00\x07]{1,63}$")
_THREAD_PREFIX_RE = re.compile(r"^\[thread:([a-zA-Z0-9-]{1,32})\] ?(.*)$", re.DOTALL)
_SYSTEM_PREFIX = "system-"
_OFF_VALUES = frozenset({"off", "0", "false", "no"})
_LEDGER_MAX = 64
_OUTBOX_MAX = 256
_THREAD_TAG = "agentirc.io/thread"
_TAG_UNESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}

#: How a cancelled read or writer task may end; anything else propagates and is recorded.
_QUIET_ENDINGS = (
    asyncio.CancelledError,
    asyncio.IncompleteReadError,
    ConnectionError,
    OSError,
    ValueError,
)

_TERMINAL = frozenset({"nick-taken", "nick-refused", "sasl-refused", "stopped"})
#: Numerics that end a SASL attempt for good (agentirc closes the connection after them).
_SASL_PERMANENT = frozenset({"902", "904", "905", "908"})
#: IRCv3 SASL's chunk size: a chunk of exactly this length means more follows.
SASL_CHUNK = 400


def take_agent_secret(env: Optional[Any] = None) -> str:
    """Remove :data:`ENV_AGENT_SECRET` from *env* (``os.environ``) and return it, stripped.

    Called once at the top of the daemon's ``main()``, before any child process is
    started, so no child ever inherits it (culture#506, embodiment#121)."""
    target = os.environ if env is None else env
    value = target.pop(ENV_AGENT_SECRET, "") or ""
    return value.strip()


def sasl_plain_lines(nick: str, secret: str) -> list[str]:
    """The ``AUTHENTICATE`` lines carrying PLAIN's ``\\0<nick>\\0<secret>``, 400-chunked."""
    payload = base64.b64encode(b"\0" + nick.encode() + b"\0" + secret.encode()).decode("ascii")
    chunks = [payload[i : i + SASL_CHUNK] for i in range(0, len(payload), SASL_CHUNK)]
    lines = [f"AUTHENTICATE {chunk}" for chunk in chunks]
    if len(payload) % SASL_CHUNK == 0:
        lines.append("AUTHENTICATE +")  # a payload that is a multiple of 400 ends with "+"
    return lines


#: The six states of culture's PRESENCE contract (``protocol/extensions/presence.md``).
PRESENCE_STATES = ("idle", "listening", "thinking", "working", "draining", "offline")
#: The states agentirc flags ``presumed_hung`` when their heartbeat stops.
PRESENCE_BUSY = frozenset({"listening", "thinking", "working", "draining"})


def _unsent_messages(outbox: "asyncio.Queue[str]") -> int:
    """Empty *outbox*; the message lines in it (PRIVMSG/THREAD). A queued PRESENCE line
    is not a lost message, so it is not a dropped send."""
    left = 0
    while not outbox.empty():
        if outbox.get_nowait().startswith(("PRIVMSG", "THREAD")):
            left += 1
    return left


def _utc_now() -> str:
    """ISO-8601 UTC at second precision with ``Z``, the form agentirc stamps itself."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


# ── values ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MeshDegradation:
    """One host-visible mesh degradation; the same ``code``/``reason`` shape as the rest."""

    code: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason}


@dataclass(frozen=True)
class MeshCloseReport:
    """What :meth:`MeshClient.close` finished. ``graceful`` is derived, never stored."""

    deadline_exceeded: bool = False
    close_error: bool = False
    dropped_sends: int = 0

    @property
    def graceful(self) -> bool:
        return not self.deadline_exceeded and not self.close_error

    def to_dict(self) -> dict[str, Any]:
        return {
            "graceful": self.graceful,
            "deadline_exceeded": self.deadline_exceeded,
            "close_error": self.close_error,
            "dropped_sends": self.dropped_sends,
        }


@dataclass(frozen=True)
class MeshMessage:
    """One inbound PRIVMSG delivered to the callback.

    ``channel`` is ``None`` for a direct message. ``mention`` is true for a
    direct message or when the text names Gwen's nick as a whole word.
    ``addressed`` is narrower: a direct message, or a line that *starts* with her nick
    (``spark-gwen: ...``, ``spark-gwen, ...``, ``@spark-gwen ...``). A notice that only
    names her ("spark-gwen just joined #general") is a mention, not an address (t19 live,
    2026-10-31: Gwen answered three join notices on #general).
    ``msgid``/``server_time``/``thread`` come from the IRCv3 tags and are empty
    when the server sent none.
    """

    sender: str
    target: str
    text: str
    channel: Optional[str]
    direct: bool
    mention: bool
    addressed: bool = False
    msgid: str = ""
    server_time: str = ""
    thread: str = ""


@dataclass(frozen=True)
class IrcLine:
    tags: dict[str, str]
    prefix: str
    command: str
    params: list[str]

    @property
    def nick(self) -> str:
        return self.prefix.split("!", 1)[0].split("@", 1)[0]


@dataclass(frozen=True)
class MeshConfig:
    """What the mesh client runs under. The nick is configuration, never inferred."""

    enabled: bool = True
    nick: str = DEFAULT_NICK
    host: str = "localhost"
    port: int = 6667
    channels: tuple[str, ...] = DEFAULT_CHANNELS
    connect_deadline: float = 5.0
    register_deadline: float = 10.0
    backoff_initial: float = 1.0
    backoff_max: float = 30.0
    ping_interval: float = 60.0
    ping_timeout: float = 30.0
    #: Under agentirc's 30 s ``heartbeat_interval_seconds`` (stale at 90 s).
    presence_heartbeat: float = 25.0
    close_deadline: float = 3.0
    #: A configuration problem found by :meth:`from_env`; the client records it
    #: and stays off rather than raising.
    error: str = ""
    #: The mesh account secret (SASL PLAIN). Never read by :meth:`from_env` (pass it
    #: from :func:`take_agent_secret`), never in ``repr``, never compared.
    sasl_secret: str = field(default="", repr=False, compare=False)

    @property
    def reconnect_bound(self) -> float:
        """Worst-case seconds from the server returning to Gwen registered again."""
        return (
            float(self.backoff_max) + float(self.connect_deadline) + float(self.register_deadline)
        )

    @property
    def liveness_bound(self) -> float:
        """Worst-case seconds between the server going silent and the client knowing."""
        return float(self.ping_interval) + float(self.ping_timeout)

    def backoff(self, attempt: int) -> float:
        """Delay before reconnect *attempt* (0-based): doubling, capped at ``backoff_max``."""
        step = float(self.backoff_initial) * (2 ** min(max(attempt, 0), 32))
        return min(float(self.backoff_max), step)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **overrides: Any) -> "MeshConfig":
        """Resolve from the shared env names; never raises (a bad value sets ``error``)."""
        if env is None:
            env = os.environ
        enabled = (env.get(ENV_MESH) or "on").strip().lower() not in _OFF_VALUES
        nick = (env.get(ENV_NICK) or DEFAULT_NICK).strip()
        host, port, error = "localhost", 6667, ""
        parsed = _parse_server(env.get(ENV_SERVER) or DEFAULT_SERVER)
        if parsed is None:
            error = "mesh-config-invalid"
        else:
            host, port = parsed
        raw_channels = env.get(ENV_CHANNELS)
        channels = DEFAULT_CHANNELS if raw_channels is None else _parse_channels(raw_channels)
        base = cls(enabled=enabled, nick=nick, host=host, port=port, channels=channels, error=error)
        return replace(base, **overrides) if overrides else base


def _parse_server(value: str) -> Optional[tuple[str, int]]:
    host, sep, port_text = value.strip().rpartition(":")
    if not sep or not host or not port_text.isdigit():
        return None
    port = int(port_text)
    if not 0 < port < 65536:
        return None
    return host, port


def _parse_channels(value: str) -> tuple[str, ...]:
    seen: list[str] = []
    for part in value.split(","):
        name = part.strip()
        if _CHANNEL_RE.match(name) and name not in seen:
            seen.append(name)
    return tuple(seen)


# ── wire ────────────────────────────────────────────────────────────────────


def _unescape_tag(value: str) -> str:
    out: list[str] = []
    chars = iter(value)
    for char in chars:
        if char == "\\":
            nxt = next(chars, "")
            out.append(_TAG_UNESCAPES.get(nxt, nxt))
        else:
            out.append(char)
    return "".join(out)


def parse_line(raw: str) -> Optional[IrcLine]:
    """One IRC line (tags, prefix, command, params); ``None`` when there is no command."""
    try:
        line = raw.rstrip("\r\n")
        tags: dict[str, str] = {}
        if line.startswith("@"):
            tag_text, _, line = line[1:].partition(" ")
            for item in tag_text.split(";"):
                if item:
                    key, _, value = item.partition("=")
                    tags[key] = _unescape_tag(value)
        line = line.lstrip(" ")
        prefix = ""
        if line.startswith(":"):
            prefix, _, line = line[1:].partition(" ")
        line = line.lstrip(" ")
        trailing: Optional[str] = None
        if " :" in line:
            line, _, trailing = line.partition(" :")
        elif line.startswith(":"):
            line, trailing = "", line[1:]
        parts = line.split()
        if not parts:
            return None
        params = parts[1:]
        if trailing is not None:
            params.append(trailing)
        return IrcLine(tags=tags, prefix=prefix, command=parts[0].upper(), params=params)
    except (AttributeError, IndexError, TypeError, ValueError):  # a non-str or a bad shape
        return None


def _clean_lines(text: str) -> list[str]:
    lines = []
    for line in re.split(r"\r\n|\r|\n", text.replace("\x00", "")):
        if line.strip():
            lines.append(line)
    return lines


def _chunks(text: str, budget: int) -> list[str]:
    """Split *text* into pieces of at most *budget* UTF-8 bytes, on character boundaries."""
    out: list[str] = []
    current: list[str] = []
    used = 0
    for char in text:
        size = len(char.encode("utf-8"))
        if used + size > budget and current:
            out.append("".join(current))
            current, used = [], 0
        current.append(char)
        used += size
    if current:
        out.append("".join(current))
    return out


def _framed(head: str, text: str) -> list[str]:
    """``head :chunk`` lines whose bytes, CRLF included, fit :data:`MAX_LINE_BYTES`."""
    budget = MAX_LINE_BYTES - len(f"{head} :".encode("utf-8")) - 2
    return [f"{head} :{chunk}" for line in _clean_lines(text) for chunk in _chunks(line, budget)]


# ── the client ──────────────────────────────────────────────────────────────


class _Dropped(Exception):
    """The connection ended; ``code`` says how."""

    def __init__(self, code: str, reason: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


def _raise_if_pump_ended(pump: "asyncio.Future[None]") -> None:
    """The writer pump ended (raised or returned): the connection is dropped."""
    if not pump.done():
        return
    exc = pump.exception() if not pump.cancelled() else None
    raise _Dropped("connection-dropped", describe_exception(exc) if exc else "writer ended")


class MeshClient:
    """Gwen's one connection to agentirc. Thread-safe API; runs on its own loop thread.

    *on_message* is called on the mesh loop thread, once per delivered PRIVMSG;
    it must hand off and return quickly (a slow callback stalls reading and
    PING answers). A raising callback is recorded as ``mesh-callback-failed``.
    """

    def __init__(
        self,
        config: MeshConfig,
        on_message: Optional[Callable[[MeshMessage], None]] = None,
    ) -> None:
        self._config = config
        self._on_message = on_message
        self._lock = threading.Lock()
        self._state = "off" if not config.enabled else "idle"
        self._ledger: deque[MeshDegradation] = deque(maxlen=_LEDGER_MAX)
        self._streak: set[str] = set()
        self._counts = {
            "connect_attempts": 0,
            "registrations": 0,
            "reconnects": 0,
            "sent": 0,
            "received": 0,
            "delivered": 0,
            "dropped_sends": 0,
            "presence_sent": 0,
        }
        self._presence = ("idle", _utc_now())
        self._presence_wake: Optional[asyncio.Event] = None
        #: "off" (no secret), "pending", "authenticated", "unavailable" or "refused".
        self._sasl = "pending" if config.sasl_secret else "off"
        self._sasl_phase = ""
        self._sasl_caps: list[str] = []
        #: Set by a ``421 PRESENCE``; reset on each new connection.
        self._presence_unsupported = False
        self._joined: set[str] = set()
        self._server_name = ""
        self._who_done: dict[str, frozenset[str]] = {}
        self._who_pending: dict[str, set[str]] = {}
        self._message_tags = False
        self._close_error = False
        self._close_budget = config.close_deadline
        self._allowed = {name.casefold(): name for name in config.channels}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop: Optional[asyncio.Event] = None
        self._outbox: Optional[asyncio.Queue[str]] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._closed = False

    # ── read side (any thread) ─────────────────────────────────────────────
    @property
    def config(self) -> MeshConfig:
        return self._config

    @property
    def degradations(self) -> tuple[MeshDegradation, ...]:
        with self._lock:
            return tuple(self._ledger)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def servers(self) -> tuple[str, ...]:
        """Servers visible on this connection: ``001``'s server plus each channel's ``WHO``."""
        with self._lock:
            if self._state != "registered":
                return ()
            names = {self._server_name} if self._server_name else set()
            for seen in self._who_done.values():
                names.update(seen)
            return tuple(sorted(names))

    def status(self) -> dict[str, Any]:
        servers = self.servers()
        with self._lock:
            return {
                "enabled": self._config.enabled,
                "state": self._state,
                "nick": self._config.nick,
                "server": f"{self._config.host}:{self._config.port}",
                "server_name": self._server_name,
                "channels": list(self._config.channels),
                "joined": sorted(self._joined),
                "servers": list(servers),
                "message_tags": self._message_tags,
                "presence": {"state": self._presence[0], "since": self._presence[1]},
                "sasl": self._sasl,
                "counts": dict(self._counts),
                "reconnect_bound": self._config.reconnect_bound,
                "liveness_bound": self._config.liveness_bound,
                "degradations": [d.to_dict() for d in self._ledger],
            }

    def __repr__(self) -> str:
        return f"MeshClient(nick={self._config.nick!r}, state={self.state!r})"

    # ── recording ──────────────────────────────────────────────────────────
    def _record(self, code: str, reason: str = "", *, streak: bool = False) -> None:
        with self._lock:
            if streak:
                if code in self._streak:
                    return
                self._streak.add(code)
            self._ledger.append(MeshDegradation(code, reason))

    def _set_state(self, state: str) -> None:
        with self._lock:
            if self._state != "closed":
                self._state = state

    def _count(self, key: str, by: int = 1) -> None:
        with self._lock:
            self._counts[key] += by

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        """Start the loop thread. Idempotent; off, invalid config or a bad nick start nothing."""
        with self._lock:
            if self._thread is not None or self._closed or not self._config.enabled:
                return
        if self._config.error:
            self._record(self._config.error)
            self._set_state("off")
            return
        nick = self._config.nick
        if not _NICK_RE.match(nick) or nick.startswith(_SYSTEM_PREFIX):
            self._record("mesh-nick-invalid")
            self._set_state("off")
            return
        loop = asyncio.new_event_loop()
        stop = asyncio.Event()
        thread = threading.Thread(target=self._thread_main, name="embodiment-mesh", daemon=True)
        with self._lock:
            self._loop, self._stop, self._thread = loop, stop, thread
            self._state = "connecting"
        thread.start()

    def _thread_main(self) -> None:
        loop = self._loop
        assert loop is not None  # nosec B101 - set by start() before the thread runs
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._main())
        except Exception as exc:  # noqa: BLE001  # degrade, never raise
            self._record("mesh-loop-failed", describe_exception(exc))
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
            except Exception as exc:  # noqa: BLE001  # teardown must not raise either
                self._record("mesh-teardown-failed", describe_exception(exc))

    def close(self, deadline: Optional[float] = None) -> MeshCloseReport:
        """Stop within *deadline* seconds (default ``close_deadline``). Never raises."""
        try:
            budget = self._config.close_deadline if deadline is None else max(0.0, deadline)
            with self._lock:
                self._closed = True
                thread, loop, stop = self._thread, self._loop, self._stop
                self._close_budget = budget
                if thread is not None:
                    self._state = "closed"
                dropped = self._counts["dropped_sends"]
            if thread is None:
                return MeshCloseReport(dropped_sends=dropped)
            if loop is not None and stop is not None:
                try:
                    loop.call_soon_threadsafe(stop.set)
                except RuntimeError:  # the loop is already closed: nothing to stop
                    pass
            thread.join(budget)
            exceeded = thread.is_alive()
            if exceeded:
                self._record("mesh-close-incomplete", "deadline exceeded")
            with self._lock:
                return MeshCloseReport(
                    deadline_exceeded=exceeded,
                    close_error=self._close_error,
                    dropped_sends=self._counts["dropped_sends"],
                )
        except Exception:  # noqa: BLE001  # close never raises
            return MeshCloseReport(close_error=True)

    # ── presence (any thread) ──────────────────────────────────────────────
    def set_presence(self, state: str) -> bool:
        """Publish *state* (one of :data:`PRESENCE_STATES`). ``False``: unknown state.

        The same state twice is no change and sends nothing (edge-triggered); a
        new one is sent now if registered, else after the next ``001``."""
        if state not in PRESENCE_STATES:
            return False
        with self._lock:
            if self._presence[0] == state:
                return True
            self._presence = (state, _utc_now())
            loop, wake = self._loop, self._presence_wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # loop closed: the next session publishes it
                pass
        return True

    @property
    def sasl_state(self) -> str:
        """``off`` (no secret), ``pending``, ``authenticated``, ``unavailable``, ``refused``."""
        with self._lock:
            return self._sasl

    def presence(self) -> dict[str, Any]:
        """What was last set to publish: ``state``, ``since``, and lines ``sent``."""
        with self._lock:
            state, since = self._presence
            return {"state": state, "since": since, "sent": self._counts["presence_sent"]}

    def _presence_line(self) -> str:
        with self._lock:
            state, since = self._presence
        payload = json.dumps({"state": state, "since": since}, separators=(",", ":"))
        return f"PRESENCE :{payload}"

    async def _beat(self, outbox: "asyncio.Queue[str]", wake: asyncio.Event) -> None:
        """Send PRESENCE on each change, and every ``presence_heartbeat`` while busy."""
        while True:
            with self._lock:
                busy = self._presence[0] in PRESENCE_BUSY
            timeout = float(self._config.presence_heartbeat) if busy else None
            try:
                await asyncio.wait_for(wake.wait(), timeout=timeout)
            except TimeoutError:
                pass
            wake.clear()
            if not self._presence_unsupported:
                outbox.put_nowait(self._presence_line())

    # ── send (any thread) ──────────────────────────────────────────────────
    def send(self, target: str, text: str) -> bool:
        """PRIVMSG an allow-listed channel or a nick. ``True`` means accepted for sending."""
        if not self._target_ok(target):
            return False
        return self._submit(_framed(f"PRIVMSG {target}", text))

    def create_thread(self, channel: str, name: str, text: str) -> bool:
        """``THREAD CREATE``; further lines of *text* follow as ``THREAD REPLY``."""
        if not self._thread_ok(channel, name):
            return False
        # Sized for the LONGER head (CREATE is one byte over REPLY), so the first line
        # fits too; the rest are re-headed as REPLY, which only makes them shorter.
        create = f"THREAD CREATE {channel} {name}"
        lines = _framed(create, text)
        if not lines:
            return False
        head = f"{create} :"
        rest = [f"THREAD REPLY {channel} {name} :" + line[len(head) :] for line in lines[1:]]
        return self._submit([lines[0], *rest])

    def reply_thread(self, channel: str, name: str, text: str) -> bool:
        """``THREAD REPLY`` into an existing thread."""
        if not self._thread_ok(channel, name):
            return False
        return self._submit(_framed(f"THREAD REPLY {channel} {name}", text))

    def _target_ok(self, target: str) -> bool:
        if not isinstance(target, str):
            return False
        if target.startswith("#"):
            return target.casefold() in self._allowed
        return bool(_NICK_RE.match(target))

    def _thread_ok(self, channel: str, name: str) -> bool:
        return (
            isinstance(channel, str)
            and isinstance(name, str)
            and channel.startswith("#")
            and channel.casefold() in self._allowed
            and bool(THREAD_NAME_RE.match(name))
        )

    def _submit(self, lines: list[str]) -> bool:
        if not lines:
            return False
        with self._lock:
            if self._state != "registered":
                return False
            loop = self._loop
        if loop is None:
            return False
        try:
            loop.call_soon_threadsafe(self._enqueue, lines)
        except RuntimeError:  # loop closed between the check and the call
            return False
        return True

    def _enqueue(self, lines: list[str]) -> None:
        outbox = self._outbox
        if outbox is None or self.state != "registered":
            self._count("dropped_sends", len(lines))
            return
        if outbox.qsize() + len(lines) > _OUTBOX_MAX:
            self._count("dropped_sends", len(lines))
            self._record("mesh-send-dropped", "outbox full", streak=True)
            return
        for line in lines:
            outbox.put_nowait(line)

    # ── the loop thread ────────────────────────────────────────────────────
    async def _main(self) -> None:
        stop = self._stop
        assert stop is not None  # nosec B101 - set by start()
        attempt = 0
        while not stop.is_set():
            outcome = await self._session()
            if outcome in _TERMINAL:
                return
            if outcome == "registered":
                attempt = 0
            delay = self._config.backoff(attempt)
            attempt += 1
            self._set_state("backoff")
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            self._set_state("connecting")

    async def _session(self) -> str:
        """One connection, start to end. Returns how it ended."""
        cfg = self._config
        stop = self._stop
        assert stop is not None  # nosec B101 - set by start()
        self._count("connect_attempts")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(cfg.host, cfg.port, limit=MAX_LINE_BYTES * 8),
                timeout=cfg.connect_deadline,
            )
        except OSError as exc:  # TimeoutError and ConnectionError are OSErrors
            self._record("mesh-connect-failed", describe_exception(exc), streak=True)
            return "failed"
        self._writer = writer
        registered = False
        try:
            if cfg.sasl_secret:
                # IRCv3 negotiation: the server holds 001 until CAP END (agentirc 9.14).
                self._sasl_phase, self._sasl_caps = "ls", []
                with self._lock:
                    if self._sasl != "refused":
                        self._sasl = "pending"
                writer.write(b"CAP LS 302\r\n")
                writer.write(f"NICK {cfg.nick}\r\n".encode())
                writer.write(b"USER gwen 0 * :Gwen\r\n")
            else:
                writer.write(b"CAP REQ :message-tags\r\n")
                writer.write(f"NICK {cfg.nick}\r\n".encode())
                writer.write(b"USER gwen 0 * :Gwen\r\n")
                writer.write(b"CAP END\r\n")
            await writer.drain()
            await self._register(reader, writer)
            registered = True
            await self._run_registered(reader, writer)
            return "stopped"
        except _Dropped as drop:
            if drop.code in _TERMINAL:
                if drop.code == "sasl-refused":
                    with self._lock:
                        self._sasl = "refused"
                if drop.code != "stopped":
                    self._set_state(drop.code)
                    self._record(f"mesh-{drop.code}", drop.reason)
                return drop.code
            self._record(f"mesh-{drop.code}", drop.reason, streak=not registered)
            return "registered" if registered else "failed"
        except (OSError, ValueError, asyncio.IncompleteReadError) as exc:
            code = "mesh-connection-dropped" if registered else "mesh-connect-failed"
            self._record(code, describe_exception(exc), streak=not registered)
            return "registered" if registered else "failed"
        finally:
            await self._teardown(writer, quit_first=registered and stop.is_set())

    async def _readline(self, reader: asyncio.StreamReader, wait_s: float) -> Optional[str]:
        """One line, or ``None`` after *wait_s*; EOF raises ``_Dropped``; stop raises it too."""
        stop = self._stop
        assert stop is not None  # nosec B101 - set by start()
        read = asyncio.ensure_future(reader.readline())
        halt = asyncio.ensure_future(stop.wait())
        try:
            done, _ = await asyncio.wait(
                {read, halt}, timeout=max(0.0, wait_s), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            halt.cancel()
        if read in done:
            raw = read.result()
            if not raw:
                raise _Dropped("connection-dropped", "eof")
            return raw.decode("utf-8", errors="replace")
        read.cancel()
        try:
            await read
        except _QUIET_ENDINGS:
            pass
        if halt in done:
            raise _Dropped("stopped")
        return None

    async def _register(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        end = time.monotonic() + self._config.register_deadline
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise _Dropped("register-timeout", "no 001 within register_deadline")
            raw = await self._readline(reader, remaining)
            if raw is None:
                continue
            msg = parse_line(raw)
            if msg is None:
                continue
            if msg.command == "001":
                self._on_welcome(msg)
                return
            if msg.command == "433":
                raise _Dropped("nick-taken", "433 ERR_NICKNAMEINUSE")
            if msg.command == "432":
                raise _Dropped("nick-refused", "432 ERR_ERRONEUSNICKNAME")
            if self._sasl_phase and self._sasl_step(msg, writer):
                continue
            if msg.command in _SASL_PERMANENT and not self._config.sasl_secret:
                # The nick now has an account and this daemon holds no secret: retrying
                # would only feed the server's failure limiter.
                raise _Dropped("sasl-refused", f"{msg.command}: the nick needs an account")
            self._handle_common(msg, writer)

    # ── SASL PLAIN (embodiment#121 part 2) ─────────────────────────────────
    def _sasl_step(self, msg: IrcLine, writer: asyncio.StreamWriter) -> bool:
        """One negotiation line; ``True`` when it was SASL's. Raises ``_Dropped`` on a
        refusal: permanent numerics end the lane, ``906`` (aborted) is retried."""
        command = msg.command
        if command in _SASL_PERMANENT:
            self._sasl_phase = ""
            raise _Dropped("sasl-refused", f"{command} from the server")
        if command == "906":
            self._sasl_phase = ""
            raise _Dropped("sasl-aborted", "906 ERR_SASLABORTED")
        if command == "CAP" and len(msg.params) >= 3:
            return self._sasl_cap(msg, writer)
        if command == "AUTHENTICATE" and self._sasl_phase == "auth":
            if msg.params and msg.params[0] == "+":
                for line in sasl_plain_lines(self._config.nick, self._config.sasl_secret):
                    writer.write((line + "\r\n").encode("ascii"))
            return True
        if command == "903":
            self._sasl_phase = ""
            with self._lock:
                self._sasl = "authenticated"
            writer.write(b"CAP END\r\n")
            return True
        return command == "900"  # "you are now logged in as": nothing to do

    def _sasl_cap(self, msg: IrcLine, writer: asyncio.StreamWriter) -> bool:
        sub_command = msg.params[1].upper()
        if sub_command == "LS" and self._sasl_phase == "ls":
            self._sasl_caps.extend(msg.params[-1].split())
            if len(msg.params) >= 4 and msg.params[2] == "*":
                return True  # a multi-line LS: more follows
            offered = any(c == "sasl" or c.startswith("sasl=") for c in self._sasl_caps)
            if offered:
                self._sasl_phase = "req"
                writer.write(b"CAP REQ :sasl message-tags\r\n")
            else:
                self._sasl_without()
                writer.write(b"CAP REQ :message-tags\r\nCAP END\r\n")
            return True
        if sub_command == "ACK" and self._sasl_phase == "req":
            self._on_cap(msg)
            if "sasl" in msg.params[2].split():
                self._sasl_phase = "auth"
                writer.write(b"AUTHENTICATE PLAIN\r\n")
            return True
        if sub_command == "NAK" and self._sasl_phase == "req":
            self._sasl_without()
            writer.write(b"CAP REQ :message-tags\r\nCAP END\r\n")
            return True
        return False

    def _sasl_without(self) -> None:
        """The server offers no SASL: join unauthenticated, as before (no flag day)."""
        self._sasl_phase = ""
        with self._lock:
            self._sasl = "unavailable"
        self._record("mesh-sasl-unavailable", "the server does not offer sasl", streak=True)

    def _on_welcome(self, msg: IrcLine) -> None:
        """001: registered. Counted (a second one is a reconnect); the streak resets."""
        with self._lock:
            self._server_name = msg.prefix
            self._counts["registrations"] += 1
            if self._counts["registrations"] > 1:
                self._counts["reconnects"] += 1
            self._streak.clear()
            if self._state != "closed":
                self._state = "registered"

    async def _run_registered(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        outbox: asyncio.Queue[str] = asyncio.Queue()
        self._outbox = outbox
        wake = asyncio.Event()
        self._presence_wake = wake  # before the first line, so no change falls between
        self._presence_unsupported = False
        writer.write(f"MODE {self._config.nick} +A\r\n".encode())
        writer.write((self._presence_line() + "\r\n").encode("utf-8"))
        self._count("presence_sent")
        for channel in self._config.channels:
            writer.write(f"JOIN {channel}\r\n".encode())
        await writer.drain()
        pump = asyncio.ensure_future(self._pump(outbox, writer))
        beat = asyncio.ensure_future(self._beat(outbox, wake))
        try:
            await self._read_registered(reader, writer, pump)
        finally:
            self._presence_wake = None
            beat.cancel()
            try:
                await beat
            except _QUIET_ENDINGS:
                pass
            self._outbox = None
            left = _unsent_messages(outbox)
            if left:
                self._count("dropped_sends", left)
            pump.cancel()
            try:
                await pump
            except _QUIET_ENDINGS:
                pass

    async def _pump(self, outbox: "asyncio.Queue[str]", writer: asyncio.StreamWriter) -> None:
        """Write queued lines. A message line taken off the queue is counted exactly once:
        ``sent`` when its drain completes, ``dropped_sends`` when a failed or cancelled
        drain ends the pump with it in flight (teardown counts only what is still queued;
        review of #119, lesson 3)."""
        while True:
            line = await outbox.get()
            counted = line.startswith(("PRIVMSG", "THREAD"))
            presence = line.startswith("PRESENCE ")
            try:
                writer.write((line + "\r\n").encode("utf-8"))
                await writer.drain()
            except BaseException:
                if counted:
                    self._count("dropped_sends")
                raise
            if counted:
                self._count("sent")
            elif presence:
                self._count("presence_sent")

    async def _read_registered(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        pump: "asyncio.Future[None]",
    ) -> None:
        cfg = self._config
        ping_pending = False
        while True:
            _raise_if_pump_ended(pump)
            timeout = cfg.ping_timeout if ping_pending else cfg.ping_interval
            raw = await self._readline(reader, timeout)
            if raw is None:
                if ping_pending:
                    raise _Dropped("connection-dropped", "liveness bound exceeded")
                writer.write(b"PING :embodiment\r\n")
                await writer.drain()
                ping_pending = True
                continue
            ping_pending = False
            msg = parse_line(raw)
            if msg is None:
                continue
            if msg.command == "433":
                raise _Dropped("nick-taken", "433 ERR_NICKNAMEINUSE")
            self._handle_common(msg, writer)

    def _handle_common(self, msg: IrcLine, writer: asyncio.StreamWriter) -> None:
        own = self._config.nick.casefold()
        command = msg.command
        if command == "PING":
            token = msg.params[-1] if msg.params else ""
            writer.write(f"PONG :{token}\r\n".encode("utf-8"))
            return
        if command in ("PART", "QUIT"):
            if msg.nick.casefold() != own:
                self._rewho_joined(writer)
            return
        # command -> (the fewest params it needs, its handler)
        handlers: dict[str, tuple[int, Callable[[], None]]] = {
            "CAP": (3, lambda: self._on_cap(msg)),
            "JOIN": (1, lambda: self._on_join(msg, writer, own)),
            "352": (5, lambda: self._on_who_reply(msg)),
            "315": (2, lambda: self._on_who_end(msg)),
            "PRIVMSG": (2, lambda: self._deliver(msg)),
            "421": (2, lambda: self._on_unknown_command(msg)),
            "501": (1, lambda: self._record("mesh-mode-refused", "501", streak=True)),
        }
        entry = handlers.get(command)
        if entry is not None and len(msg.params) >= entry[0]:
            entry[1]()

    def _on_unknown_command(self, msg: IrcLine) -> None:
        if msg.params[1].upper() == "PRESENCE":
            self._presence_unsupported = True  # this connection publishes no more
            self._record("mesh-presence-unsupported", "421 PRESENCE", streak=True)

    def _on_cap(self, msg: IrcLine) -> None:
        if msg.params[1].upper() == "ACK" and "message-tags" in msg.params[2].split():
            with self._lock:
                self._message_tags = True

    def _on_join(self, msg: IrcLine, writer: asyncio.StreamWriter, own: str) -> None:
        channel = msg.params[0]
        if channel.casefold() not in self._allowed:
            return
        if msg.nick.casefold() == own:
            with self._lock:
                self._joined.add(self._allowed[channel.casefold()])
        writer.write(f"WHO {channel}\r\n".encode("utf-8"))

    def _rewho_joined(self, writer: asyncio.StreamWriter) -> None:
        with self._lock:
            joined = sorted(self._joined)
        for channel in joined:
            writer.write(f"WHO {channel}\r\n".encode("utf-8"))

    def _on_who_reply(self, msg: IrcLine) -> None:
        key = msg.params[1].casefold()
        with self._lock:
            self._who_pending.setdefault(key, set()).add(msg.params[4])

    def _on_who_end(self, msg: IrcLine) -> None:
        key = msg.params[1].casefold()
        with self._lock:
            self._who_done[key] = frozenset(self._who_pending.pop(key, set()))

    def _deliver(self, msg: IrcLine) -> None:
        own = self._config.nick
        sender = msg.nick
        if not sender or sender.casefold() == own.casefold():
            return
        target, text = msg.params[0], msg.params[1]
        if target.startswith("#"):
            name = self._allowed.get(target.casefold())
            if name is None:
                return
            channel: Optional[str] = name
            direct = False
        elif target.casefold() == own.casefold():
            channel, direct = None, True
        else:
            return
        self._count("received")
        thread = msg.tags.get(_THREAD_TAG, "")
        prefixed = _THREAD_PREFIX_RE.match(text)
        if prefixed and (not thread or prefixed.group(1) == thread):
            thread = prefixed.group(1)
            text = prefixed.group(2)
        mention = direct or bool(
            re.search(rf"(?<![\w-]){re.escape(own)}(?![\w-])", text, re.IGNORECASE)
        )
        addressed = direct or bool(
            re.match(rf"\s*(?:@{re.escape(own)}\b|{re.escape(own)}\s*[:,])", text, re.IGNORECASE)
        )
        message = MeshMessage(
            sender=sender,
            target=target,
            text=text,
            channel=channel,
            direct=direct,
            mention=mention,
            addressed=addressed,
            msgid=msg.tags.get("msgid", ""),
            server_time=msg.tags.get("time", ""),
            thread=thread,
        )
        callback = self._on_message
        if callback is None:
            return
        try:
            callback(message)
        except Exception as exc:  # noqa: BLE001  # a consumer's fault is a record
            self._record("mesh-callback-failed", describe_exception(exc))
            return
        self._count("delivered")

    async def _teardown(self, writer: asyncio.StreamWriter, *, quit_first: bool) -> None:
        self._writer = None
        with self._lock:
            self._joined.clear()
            self._who_done.clear()
            self._who_pending.clear()
            self._message_tags = False
            budget = self._close_budget
        try:
            if quit_first:
                with self._lock:
                    self._presence = ("draining", _utc_now())
                if not self._presence_unsupported:
                    writer.write((self._presence_line() + "\r\n").encode("utf-8"))
                writer.write(b"QUIT :Gwen is leaving\r\n")
                await asyncio.wait_for(writer.drain(), timeout=max(0.01, budget / 2))
        except OSError:  # TimeoutError and ConnectionError are OSErrors
            pass
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=max(0.01, budget / 2))
        except OSError:  # TimeoutError and ConnectionError are OSErrors
            pass
        except Exception:  # noqa: BLE001  # a raising close is reported, not raised
            with self._lock:
                self._close_error = True


class PresenceGauge:
    """Gwen's ``thinking`` / ``idle`` from the one boundary that shows it: a completion.

    The daemon wraps every completion through its chat seam in :meth:`busy`; the
    gauge counts the ones in flight (a typed turn, a spoken turn and the judge can
    overlap) and tells the attached client only on an edge: the first in is
    ``thinking``, the last out is ``idle``. No client attached is a no-op, so the
    seam never depends on the mesh being up. Thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight = 0
        self._client: Any = None
        #: A client whose ``set_presence`` raised: counted, never raised into the seam.
        self.failures = 0

    def attach(self, client: Any) -> None:
        """Report to *client* (``None`` detaches); it is told the current state now."""
        with self._lock:
            self._client = client
            if client is not None:
                self._tell(client, "thinking" if self._inflight else "idle")

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    @contextlib.contextmanager
    def busy(self) -> Iterator[None]:
        self._step(+1)
        try:
            yield
        finally:
            self._step(-1)

    def _step(self, by: int) -> None:
        # The edge is published UNDER the lock: two threads crossing it (the last call
        # ending, the judge starting) are told to the client in the order they happened,
        # so the row can never read idle while a call is in flight (review of 0.25.0).
        with self._lock:
            before = self._inflight
            self._inflight = max(0, before + by)
            edge = (before == 0) != (self._inflight == 0)
            if edge and self._client is not None:
                self._tell(self._client, "thinking" if self._inflight else "idle")

    def _tell(self, client: Any, state: str) -> None:
        """Hand *client* the state; caller holds the lock. ``set_presence`` only takes the
        client's own lock and never calls back here, so the nesting cannot deadlock."""
        try:
            client.set_presence(state)
        except Exception:  # noqa: BLE001  # presence must never fail a completion
            self.failures += 1
