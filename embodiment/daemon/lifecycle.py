"""embodiment.daemon.lifecycle — how the daemon is started, found, reported on, stopped.

Task ``t5`` of the ``realtime-embodiment-app`` plan. The daemon *application*
is task ``t15``'s (``embodiment/daemon/app.py``); what this module owns is the
lifecycle around one: the exclusive claim on being the running daemon, the
detached spawn, the truthful report, and the bounded stop.

The seam t15 plugs into
-----------------------
A daemon application is any object with ``run(stop_event) -> int | None``
(:class:`Runnable`). It is named to :func:`start` as a dotted path,
``package.module:attribute``, where the attribute is a **zero-argument factory
returning the runnable**. :data:`DEFAULT_TARGET` is
``embodiment.daemon.app:main``, task t15's module. A target that cannot be
imported fails as a clean environment error naming it, never a traceback, and
nothing is spawned. An optional ``shutdown(deadline)`` method is called on the
way out.

Single instance, and why a pidfile is not enough
------------------------------------------------
A pidfile containing a number has two failure modes this module refuses to
inherit. Two ``start`` invocations can each read "no pidfile" and both spawn;
and a pid outlives its process, so a *reused* pid makes a dead daemon look
alive (and makes ``stop`` signal a stranger). Both are closed by making the
pidfile an **exclusive lock**: :class:`PidFile` holds ``fcntl.flock`` with
``LOCK_EX | LOCK_NB`` for the daemon's whole life, so

* a second ``start`` cannot win the race — it loses the lock, full stop; and
* **liveness is the lock, never the number.** A reader asks the kernel whether
  anyone holds it, so pid reuse is structurally irrelevant: a reused pid never
  holds *this* file's lock. The pid in the record is used only to address a
  signal to a daemon the lock has already proved alive, and even then a pid
  that must never be signalled (``<= 1``, this process, its parent) is refused
  and recorded rather than obeyed.

The lock is acquired by the *parent* and inherited by the child through
``pass_fds``: ``flock`` belongs to the open file description, which fork/exec
duplicates, so the child keeps the lock after the parent closes its copy. That
ordering is what makes the claim atomic — the daemon is single-instance from
before the child exists, not from whenever it gets around to locking.

A *stale* pidfile — one whose lock is free — is a previous process's leftover.
:func:`start` reclaims it and records one degradation naming the dead pid
(:data:`STALE_PIDFILE_RECLAIMED_CODE`), because a nonzero count of those is
evidence of unclean deaths, not housekeeping to do quietly (C3).

Clean stop and unclean death are different facts
------------------------------------------------
The daemon rewrites its own pidfile record to ``state: "exited"`` on the way
out. So a free lock plus an ``exited`` record reads as **stopped**; a free lock
with the record still saying ``running`` reads as **dead (unclean)** and
``status`` carries the last ledger entries with it. A ``SIGKILL``-ed daemon
cannot write that marker, which is correct: an escalated stop *is* an unclean
death, and it is recorded as one at both ends.

Which candidate directory speaks
--------------------------------
:func:`status` and :func:`stop` look in every entry of
``candidate_state_dirs()`` so a daemon that fell back during bootstrap is still
found. That search **promotes a live daemon and nothing else**: a live lock in
any candidate is the answer (and the report names the directory it was found
in), but with nothing live the answer is the *primary* directory's own state.
Another candidate's record rides along as a secondary note
(:attr:`StatusReport.other_candidates`), and :func:`start` reclaims a stale one
exactly as it reclaims the primary's. The machine-wide fallback directory is
shared by every invocation on the box and nothing but a ``start`` ever clears
it, so letting a corpse there win the headline made ``embodiment status``
report ``dead (unclean)`` on a healthy machine until someone deleted a file in
``/tmp`` by hand.

Daemonisation: ``Popen(start_new_session=True)``, not a double fork
-------------------------------------------------------------------
Chosen deliberately. A double fork inherits the CLI process's imported
modules, threads, atexit handlers and open files; ``Popen`` re-execs a clean
interpreter with only the fds we hand it. ``start_new_session=True`` is the
``setsid`` the double fork existed to perform — new session, no controlling
terminal — so closing the launching terminal cannot take the daemon with it.
``stdin`` is ``/dev/null``; ``stdout`` and ``stderr`` both go to
``<state dir>/daemon.err`` (0600), never the operator's terminal. The child's
cwd is the state directory, which is private and provably outside any git
checkout — eidetic resolves its store from the cwd, so a daemon inheriting a
repo cwd could write room conversation into a committed store (CLAUDE.md,
"Privacy of the room").

A pid is not an identity
------------------------
``stop`` re-proves, **immediately before each signal**, that the process it is
about to signal is still the daemon: the lock is still held, the pidfile still
names the same pid, and that pid's *process start time* (``/proc/<pid>/stat``
field 22, fixed for the life of a process) still matches the one recorded when
the daemon started. Evaluating liveness once and then signalling is the bug
this closes: if the daemon exits in the gap and the kernel recycles its pid to
another process of the same user, that stranger receives the ``SIGTERM`` and
then the ``SIGKILL``. Neither :func:`_signallable` nor an ``EPERM`` catches it,
because the stranger is ours to signal.

A mismatch is never a signal: it records :data:`STOP_TARGET_CHANGED_CODE` — a
named code and a fixed reason, never anything about the stranger — and falls
through to :func:`_wait_for_release`, which decides "stopped" on the lock's
truth rather than on ours. Where ``/proc`` cannot answer (not Linux, hardened
``/proc``), the check degrades to today's pid-only behaviour and says so, in
the ledger (:data:`IDENTITY_UNVERIFIABLE_CODE`) and in
``StopResult.identity_verified`` / ``StatusReport.identity_verified``, rather
than refusing to stop the daemon.

This narrows the window to the microseconds between the ``/proc`` read and the
``kill``; it does not close it. ``os.pidfd_open`` would, and is **not
available on this interpreter** — so this is the Linux fact underneath it,
used directly.

Bounded stop, from both ends
----------------------------
:func:`stop` sends ``SIGTERM``, waits at most *timeout* for the lock to go
free, then escalates to ``SIGKILL`` and **records that it had to**
(:data:`STOP_ESCALATED_CODE`), then waits *kill_grace* more; a process that
survives even that is recorded as unconfirmed rather than reported stopped.
Inside the daemon, :class:`DaemonRunner` arms a watchdog thread the moment a
stop is requested: if the graceful path has not finished within
*shutdown_deadline* it writes what is unfinished to the ledger **first** and
then hard-exits with ``os._exit``. That is what bounds a target parked in a
blocking read — PEP 475 restarts an interrupted ``read``, so a signal alone can
never unpark one — and what stops a non-daemon worker thread from holding the
interpreter open. The watchdog polls plain booleans rather than waiting on a
``threading.Event``, so its bound does not depend on any lock a signal handler
might have interrupted.

Never raise, record instead (C3)
--------------------------------
No public function here raises for an environment problem: :func:`start`,
:func:`stop` and :func:`status` return frozen result objects carrying a
degradation ``code`` and a ``detail``, and every failure worth remembering is
appended to t4's :class:`~embodiment.daemon.state.DegradationLedger`. The CLI
verbs are the only layer that raises, and they raise ``CliError``.

No speech in a record
---------------------
Nothing this module writes to disk or returns to a caller carries the target's
output. **Every caught exception goes through**
:func:`embodiment.safe_reason.describe_exception`, never ``str(exc)`` — an
exception message is the *dependency's* text, and dependencies quote their
input back, so a store raising ``could not write {record}`` would put the
user's words in the ledger. What lands instead is the class name, the cause
chain, an ``errno`` name, the message's *length* and a fingerprint of it.
``tests/test_safe_reason.py``'s AST guard scans this module — it scans anything
that imports the sanitiser — so the rule survives the next edit rather than
this paragraph.

A child that dies at import leaves its traceback in ``daemon.err`` and
the result carries the *path* and the exit code, never the text. Thread names
and reasons are sanitised to ``[A-Za-z0-9._-]``; the embedded ``DaemonState``
snapshot is reduced to counts and paths, dropping the free-form
``last_write_error`` and ``ledger.last`` strings a reader can get from the
ledger itself. Thread names are not recorded at all — only a count and a
digest — because a name is chosen by the daemon application and a host that
names a worker after what it is working on would otherwise put that text
straight into a degradation record.

Platform
--------
POSIX only: ``fcntl.flock``, ``os.kill``, process sessions. The rig this
daemon is built for is Linux; there is no Windows fallback and pretending
otherwise would be a lie about the single-instance guarantee.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
import re
import signal
import subprocess  # nosec B404 - fixed argv, shell=False; see the daemonisation note
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from embodiment.daemon.state import (
    LEDGER_FILENAME,
    DaemonState,
    DegradationLedger,
    candidate_state_dirs,
    resolve_state_dir,
)
from embodiment.safe_reason import describe_exception

__all__ = [
    "PIDFILE_NAME",
    "DAEMON_STDERR_NAME",
    "DEFAULT_TARGET",
    "PIDFILE_SCHEMA",
    "DEFAULT_SHUTDOWN_DEADLINE",
    "DEFAULT_STOP_TIMEOUT",
    "DEFAULT_KILL_GRACE",
    "DEFAULT_START_CONFIRM_TIMEOUT",
    "DEFAULT_STDERR_LOG_MAX_BYTES",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "STATE_DEAD_UNCLEAN",
    "STATE_UNAVAILABLE",
    "STALE_PIDFILE_RECLAIMED_CODE",
    "STOP_ESCALATED_CODE",
    "STOP_UNCONFIRMED_CODE",
    "STOP_TARGET_CHANGED_CODE",
    "IDENTITY_UNVERIFIABLE_CODE",
    "SIGNAL_FAILED_CODE",
    "REFUSED_PID_CODE",
    "HARD_EXIT_CODE",
    "THREADS_LINGERING_CODE",
    "TARGET_UNAVAILABLE_CODE",
    "TARGET_INVALID_CODE",
    "TARGET_FAILED_CODE",
    "SHUTDOWN_FAILED_CODE",
    "CHILD_EXITED_EARLY_CODE",
    "START_UNCONFIRMED_CODE",
    "START_BUSY_CODE",
    "NO_STATE_DIR_CODE",
    "STDERR_LOG_TRUNCATED_CODE",
    "DAEMON_EXIT_HARD",
    "DAEMON_EXIT_TARGET_UNAVAILABLE",
    "DAEMON_EXIT_TARGET_FAILED",
    "Runnable",
    "PidFile",
    "StartResult",
    "StopResult",
    "StatusReport",
    "DaemonRunner",
    "resolve_target",
    "run_daemon",
    "start",
    "stop",
    "status",
]

#: The daemon's exclusive claim, in the state directory t4 resolves.
PIDFILE_NAME = "daemon.pid"

#: Where the child's stdout and stderr land. Not the operational log: this is
#: the raw fd a crashing interpreter writes its traceback to.
DAEMON_STDERR_NAME = "daemon.err"

#: The daemon application, built by task ``t15``. A target that cannot be
#: imported makes ``start`` fail cleanly, naming it.
DEFAULT_TARGET = "embodiment.daemon.app:main"

#: Bumped when the pidfile record's shape changes; an unknown schema is read
#: as unusable rather than guessed at.
PIDFILE_SCHEMA = 1

#: How long the daemon's own graceful shutdown gets before the watchdog writes
#: what is unfinished and hard-exits. CHOSEN, not measured: it is the largest
#: value that leaves :data:`DEFAULT_STOP_TIMEOUT` room to observe the exit
#: inside the plan's 5 s budget.
DEFAULT_SHUTDOWN_DEADLINE = 2.0

#: How long :func:`stop` waits for ``SIGTERM`` to take effect before
#: escalating. Strictly greater than the deadline above, so a daemon that
#: honours its own bound is never killed for being slow.
DEFAULT_STOP_TIMEOUT = 3.0

#: How long :func:`stop` waits after ``SIGKILL`` before reporting the stop
#: unconfirmed. ``DEFAULT_STOP_TIMEOUT + DEFAULT_KILL_GRACE`` is the whole
#: bound, and it is under the plan's 5 s.
DEFAULT_KILL_GRACE = 1.0

#: How long :func:`start` waits for the child to take its record to
#: ``running``. A counter that increments, not ``armed == True``: start does
#: not claim success until the daemon says it is running.
DEFAULT_START_CONFIRM_TIMEOUT = 3.0

#: ``daemon.err`` is the child's raw fd, so it cannot be bounded *within* a
#: run. It is bounded *across* runs: a file larger than this is truncated at
#: the next ``start``, with one degradation naming the discarded byte count.
DEFAULT_STDERR_LOG_MAX_BYTES = 1_000_000

STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_DEAD_UNCLEAN = "dead (unclean)"
STATE_UNAVAILABLE = "state unavailable"

STALE_PIDFILE_RECLAIMED_CODE = "lifecycle-stale-pidfile-reclaimed"
STOP_ESCALATED_CODE = "lifecycle-stop-escalated"
STOP_UNCONFIRMED_CODE = "lifecycle-stop-unconfirmed"
STOP_TARGET_CHANGED_CODE = "lifecycle-stop-target-changed"
IDENTITY_UNVERIFIABLE_CODE = "lifecycle-identity-unverifiable"
SIGNAL_FAILED_CODE = "lifecycle-signal-failed"
REFUSED_PID_CODE = "lifecycle-refused-pid"
HARD_EXIT_CODE = "lifecycle-hard-exit"
THREADS_LINGERING_CODE = "lifecycle-threads-lingering"
TARGET_UNAVAILABLE_CODE = "lifecycle-target-unavailable"
TARGET_INVALID_CODE = "lifecycle-target-invalid"
TARGET_FAILED_CODE = "lifecycle-target-failed"
SHUTDOWN_FAILED_CODE = "lifecycle-shutdown-failed"
CHILD_EXITED_EARLY_CODE = "lifecycle-child-exited-early"
START_UNCONFIRMED_CODE = "lifecycle-start-unconfirmed"
START_BUSY_CODE = "lifecycle-start-busy"
NO_STATE_DIR_CODE = "lifecycle-no-state-dir"
STDERR_LOG_TRUNCATED_CODE = "lifecycle-stderr-log-truncated"

#: Exit codes the daemon *process* uses. Distinct from the CLI's 0/1/2 policy:
#: these are what ``stop`` and ``status`` report back about the child.
DAEMON_EXIT_HARD = 3
DAEMON_EXIT_TARGET_UNAVAILABLE = 2
DAEMON_EXIT_TARGET_FAILED = 4

_PRIVATE_FILE_MODE = 0o600
_POLL_INTERVAL = 0.02
_ACQUIRE_ATTEMPTS = 6
_ACQUIRE_RETRY_INTERVAL = 0.05

#: The one prefix that marks a candidate state directory as unusable rather
#: than merely absent. ``_scan`` keys its error on it, so the wording is
#: load-bearing and lives in one place.
_UNUSABLE_PREFIX = "unusable state directory"

#: Where ``starttime`` (``/proc/<pid>/stat`` field 22) sits once the line has
#: been split after its last ``)``: the remaining fields begin at field 3, so
#: 22 - 3 = 19. See :func:`_parse_start_time` for why the split is not a plain
#: ``.split()``.
_STARTTIME_INDEX_AFTER_COMM = 19

#: A pidfile bigger than this was not written by us; refuse to parse it.
_MAX_PIDFILE_BYTES = 64_000

#: ``package.module:attribute``. No dots in the attribute, no separators, no
#: leading dash (which would otherwise reach the child interpreter as a flag),
#: nothing outside the identifier charset — so a target string can never carry
#: a path traversal, a shell metacharacter, a NUL or a bidi run.
_TARGET_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
_MAX_TARGET_LEN = 200

#: Everything recorded goes through this, once (one sanitiser, one code path).
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_TOKEN_LEN = 60


@runtime_checkable
class Runnable(Protocol):
    """What a daemon application is: one bounded, stoppable ``run``.

    ``run`` is handed a :class:`threading.Event` that is set when a stop has
    been requested, and is expected to return once it notices. Returning an
    ``int`` sets the process exit code; ``None`` means ``0``. A target may also
    expose ``shutdown(deadline: float) -> None``, called after ``run`` returns;
    neither call is trusted to respect its deadline, which is why
    :class:`DaemonRunner`'s watchdog exists.
    """

    def run(self, stop_event: threading.Event) -> Optional[int]:  # pragma: no cover - protocol
        ...


def _safe_token(value: object, *, limit: int = _MAX_TOKEN_LEN) -> str:
    """The ONE sanitiser: ``[A-Za-z0-9._-]``, length-capped, never empty.

    Every free-form fragment this module records — a thread name, a stop
    reason — passes through here, so no record can carry a path, a NUL, a
    newline, a bidi control or an unbounded string.
    """
    text = _SAFE_TOKEN_RE.sub("-", str(value))[:limit]
    return text or "unnamed"


# ── the pidfile, as an exclusive lock ────────────────────────────────────────


class PidFile:
    """The daemon's exclusive claim: a locked file carrying a small JSON record.

    :meth:`acquire` returns ``True`` only if this process took the lock. It is
    never a blocking wait — a brief bounded retry absorbs the microsecond-wide
    window in which a concurrent :func:`status` holds the lock for its probe,
    and anything longer than that means a real daemon owns it.

    :meth:`close` is idempotent and never raises (lesson: shutdown is a
    feature). Closing the last descriptor for the open file description is what
    releases the lock, which is exactly why :func:`start` may close its own
    copy while the child keeps the claim.
    """

    def __init__(self, path: str | Path, *, fd: Optional[int] = None) -> None:
        self._path = Path(path)
        self._fd = fd
        #: Why the last :meth:`acquire` failed, if it did. Never raises out.
        self.last_error: Optional[str] = None

    @classmethod
    def from_fd(cls, path: str | Path, fd: int) -> "PidFile":
        """Wrap a descriptor inherited from a parent process (see ``pass_fds``)."""
        return cls(path, fd=fd)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def fd(self) -> Optional[int]:
        return self._fd

    def acquire(self) -> bool:
        """Take the exclusive lock, creating the file 0600. Never raises.

        ``O_NOFOLLOW`` is load-bearing, not decoration: this call *truncates*
        and rewrites whatever it opens, so a symlink planted at ``daemon.pid``
        would otherwise turn ``embodiment start`` into a write primitive
        pointed at any file the user can write. The state directory is 0700
        and ours, which makes that a same-user attack rather than an open one
        — a reason to refuse it cheaply, not a reason to allow it.
        :attr:`last_error` carries why an acquisition failed.
        """
        if self._fd is not None:
            return True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, _PRIVATE_FILE_MODE)
        except OSError as exc:
            self.last_error = f"could not open {self._path}: {describe_exception(exc)}"
            return False
        try:
            os.fchmod(fd, _PRIVATE_FILE_MODE)
        except OSError:
            pass  # narrow except; the mode is re-asserted, not depended on
        for attempt in range(_ACQUIRE_ATTEMPTS):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.last_error = "another process holds the daemon lock"
                if attempt + 1 < _ACQUIRE_ATTEMPTS:
                    time.sleep(_ACQUIRE_RETRY_INTERVAL)
                    continue
            except OSError as exc:
                self.last_error = f"could not lock {self._path}: {describe_exception(exc)}"
                break
            else:
                self._fd = fd
                return True
        try:
            os.close(fd)
        except OSError:
            pass  # narrow except; closing a descriptor we never locked
        return False

    def read(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """The current record, or ``(None, why not)``. Never raises."""
        if self._fd is not None:
            try:
                raw = os.pread(self._fd, _MAX_PIDFILE_BYTES + 1, 0)
            except OSError as exc:
                return None, describe_exception(exc)
            return _decode_record(raw)
        return read_pid_record(self._path)

    def write(self, record: dict[str, Any]) -> Optional[str]:
        """Replace the record in place. Returns a detail on failure, else ``None``."""
        if self._fd is None:
            return "pidfile is not held by this process"
        try:
            data = json.dumps(record, sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            return f"unserialisable pidfile record: {describe_exception(exc)}"
        try:
            os.ftruncate(self._fd, 0)
            os.pwrite(self._fd, data, 0)
            os.fsync(self._fd)
        except OSError as exc:
            return describe_exception(exc)
        return None

    def unlink(self) -> None:
        """Remove the file. Never raises. Used only before a spawn that failed."""
        try:
            self._path.unlink()
        except OSError:
            pass  # narrow except; already gone is the outcome asked for

    def close(self) -> None:
        """Release the lock and the descriptor. Idempotent; never raises."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass  # narrow except; an already-closed descriptor is the state we want


def _decode_record(raw: bytes) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Parse a pidfile's bytes defensively. Never raises."""
    if not raw:
        return None, "pidfile is empty"
    if len(raw) > _MAX_PIDFILE_BYTES:
        return None, f"pidfile is implausibly large ({len(raw)} bytes); refusing to parse it"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"unreadable pidfile: {describe_exception(exc)}"
    if not isinstance(data, dict):
        return None, "pidfile does not hold a JSON object"
    schema = data.get("schema")
    if schema != PIDFILE_SCHEMA:
        return None, f"pidfile schema {schema!r} is not {PIDFILE_SCHEMA}"
    return data, None


def read_pid_record(path: str | Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read a pidfile without touching its lock. Never raises, never writes."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, describe_exception(exc)
    return _decode_record(raw)


def _probe_locked(path: Path) -> tuple[Optional[bool], Optional[str]]:
    """Is anyone holding *path*'s lock? ``(None, detail)`` when unknowable.

    Opened read-only, so this can never create the file — :func:`status` must
    not write. The lock is taken and released immediately when it is free; the
    window is microseconds, and :meth:`PidFile.acquire`'s bounded retry exists
    to absorb exactly it.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, describe_exception(exc)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True, None
        except OSError as exc:
            return None, describe_exception(exc)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError as exc:
            return False, describe_exception(exc)
        return False, None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass  # narrow except; the probe's descriptor, nothing depends on it


def _parse_start_time(stat_line: str) -> Optional[int]:
    """Field 22 (``starttime``) of a ``/proc/<pid>/stat`` line. Never raises.

    Parsed **after the last ``)``**, never by splitting the whole line on
    whitespace: field 2 is ``comm``, a process can set it to almost anything,
    and ``prctl(PR_SET_NAME)`` happily accepts spaces and parentheses. A naive
    split reads a field of the process's own choosing as the start time, which
    is the one number this check must not let a process control. After the
    last ``)`` the remaining fields start at field 3, so field 22 is at index
    :data:`_STARTTIME_INDEX_AFTER_COMM`.

    Returns ``None`` — absent, never a guess — for anything that does not parse.
    """
    head, separator, after = stat_line.rpartition(")")
    if not separator or not head:
        return None
    fields = after.split()
    if len(fields) <= _STARTTIME_INDEX_AFTER_COMM:
        return None
    try:
        return int(fields[_STARTTIME_INDEX_AFTER_COMM])
    except ValueError:
        return None


def _process_start_time(pid: int) -> Optional[int]:
    """When *pid*'s process started, in clock ticks since boot. Never raises.

    The second half of a process identity. A pid alone is a slot the kernel
    reuses; a pid **and** the moment that process started is unique for as long
    as the process lives, so a recycled pid can never match the pair recorded
    for the daemon.

    ``None`` means *unknowable here* — no ``/proc`` (not Linux), a hardened
    ``/proc``, or the process is already gone — and callers degrade rather
    than refuse. It is never confused with "does not match".
    """
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    return _parse_start_time(raw)


def _record_pid(record: Optional[dict[str, Any]]) -> Optional[int]:
    """The pid in a record, if it is plausibly one. Never raises."""
    if not record:
        return None
    pid = record.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    if pid <= 0 or pid > 2**31:
        return None
    return pid


def _signallable(pid: Optional[int]) -> Optional[str]:
    """``None`` if *pid* may be signalled, else why it must never be."""
    if pid is None:
        return "the pidfile names no usable pid"
    if pid <= 1:
        return f"refusing to signal pid {pid}"
    if pid == os.getpid():
        return "refusing to signal this process"
    if pid == os.getppid():
        return "refusing to signal this process's parent"
    return None


# ── the target seam ──────────────────────────────────────────────────────────


def _validate_target(dotted: str) -> Optional[str]:
    """Format check only — no import, no filesystem. Returns a detail or ``None``."""
    if not isinstance(dotted, str) or not dotted:
        return "target must be a non-empty 'package.module:attribute' string"
    if len(dotted) > _MAX_TARGET_LEN:
        return f"target is too long ({len(dotted)} chars; the limit is {_MAX_TARGET_LEN})"
    if not _TARGET_RE.match(dotted):
        return (
            "target must look like 'package.module:attribute' using only "
            "letters, digits and underscores"
        )
    return None


def resolve_target(dotted: str) -> tuple[Optional[Callable[[], Any]], Optional[str]]:
    """Import *dotted* and return its zero-argument factory. Never raises.

    Returns ``(factory, None)`` or ``(None, why not)``. The format is checked
    *before* anything is imported, so a hostile string never reaches the import
    machinery. Importing a module runs its code, which is why this happens in
    the daemon child and never in the CLI process (:func:`start` checks only
    that the module is *findable*).
    """
    invalid = _validate_target(dotted)
    if invalid is not None:
        return None, invalid
    module_name, _, attribute = dotted.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return None, f"cannot import {module_name}: {describe_exception(exc)}"
    except Exception as exc:  # noqa: BLE001  # a target's own import-time failure
        return None, f"{module_name} failed at import: {describe_exception(exc)}"
    factory = getattr(module, attribute, None)
    if factory is None:
        return None, f"{module_name} has no attribute {attribute!r}"
    if not callable(factory):
        return None, f"{module_name}:{attribute} is not callable"
    return factory, None


def _target_is_findable(dotted: str, child_path: Optional[str] = None) -> Optional[str]:
    """Is the target's module importable *without* importing it? Never raises.

    The pre-flight :func:`start` runs so a missing daemon application is a
    clean error rather than a child that dies at import. It searches the path
    the **child** will use, not this process's: *child_path* is the
    ``PYTHONPATH`` the child inherits, prepended to ``sys.path`` for the
    duration of the lookup and removed again, so the check answers the
    question actually being asked.
    """
    module_name = dotted.partition(":")[0]
    extra = [entry for entry in (child_path or "").split(os.pathsep) if entry]
    original = list(sys.path)
    if extra:
        sys.path[:0] = extra
        importlib.invalidate_caches()
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, AttributeError, ValueError) as exc:
        return f"cannot locate {module_name}: {describe_exception(exc)}"
    finally:
        if extra:
            sys.path[:] = original
            importlib.invalidate_caches()
    if spec is None:
        return f"no module named {module_name}"
    return None


# ── finding a daemon across every candidate state dir ────────────────────────


@dataclass(frozen=True)
class _Candidate:
    """One state directory a daemon could be living in, as found on disk."""

    dir: Path
    exists: bool
    readable: bool
    pidfile: Path
    pidfile_exists: bool
    locked: Optional[bool]
    record: Optional[dict[str, Any]]
    detail: Optional[str]


def _scan(override: Optional[str | Path] = None) -> tuple[list[_Candidate], Optional[str]]:
    """Every candidate state dir, in the order a writer would have used them.

    ``status`` and ``stop`` must find a daemon that fell back to t4's
    deterministic temp directory during bootstrap, so neither may look only at
    the preferred path.

    Returns ``(candidates, why there are none)``. Two "never raise" cases were
    found by attacking this function and are handled rather than propagated:

    * an override that cannot be *resolved* — an embedded NUL, or a path the
      OS rejects outright;
    * an override that is **blank**. ``Path("")`` resolves to the *current
      working directory*, so ``--state-dir ''`` would silently make the daemon
      claim a pidfile in whatever directory the operator happened to be in,
      including a git checkout. That is exactly the cwd-dependence t4 exists to
      prevent, so it is refused instead of resolved.

    Per-candidate probing is guarded too: a single unusable path (too long,
    a vanished mount) marks that candidate and lets the scan continue to the
    next one, because the *other* candidate may well hold a live daemon.
    """
    if isinstance(override, str) and not override.strip():
        return [], "state directory override is blank; pass a path or omit the flag"
    try:
        directories = candidate_state_dirs(override)
    except (ValueError, OSError, RuntimeError) as exc:
        return [], f"{_UNUSABLE_PREFIX}: {describe_exception(exc)}"
    found: list[_Candidate] = []
    seen: set[Path] = set()
    for directory in directories:
        if directory in seen:
            continue
        seen.add(directory)
        found.append(_probe_candidate(directory))
    if found and (found[0].detail or "").startswith(_UNUSABLE_PREFIX):
        return found, found[0].detail
    return found, None


def _probe_candidate(directory: Path) -> _Candidate:
    """Look at one candidate state directory. Never raises."""
    pidfile = directory / PIDFILE_NAME
    detail: Optional[str] = None
    try:
        exists = directory.is_dir()
        readable = exists and os.access(directory, os.R_OK | os.X_OK)
        pidfile_exists = readable and pidfile.is_file()
    except (OSError, ValueError) as exc:
        return _Candidate(
            dir=directory,
            exists=False,
            readable=False,
            pidfile=pidfile,
            pidfile_exists=False,
            locked=None,
            record=None,
            detail=f"{_UNUSABLE_PREFIX}: {describe_exception(exc)}",
        )
    if exists and not readable:
        detail = f"{directory} exists but is not readable"
    locked: Optional[bool] = None
    record: Optional[dict[str, Any]] = None
    if pidfile_exists:
        locked, lock_detail = _probe_locked(pidfile)
        record, record_detail = read_pid_record(pidfile)
        detail = lock_detail or record_detail or detail
    return _Candidate(
        dir=directory,
        exists=exists,
        readable=readable,
        pidfile=pidfile,
        pidfile_exists=pidfile_exists,
        locked=locked,
        record=record,
        detail=detail,
    )


def _classify(candidate: _Candidate) -> tuple[str, str]:
    """``(state, detail)`` for ONE candidate, judged on its own.

    Judging each candidate separately is what keeps a dead record in the
    machine-wide fallback directory from speaking for the directory the
    operator actually named — see :func:`status`.
    """
    if (candidate.detail or "").startswith(_UNUSABLE_PREFIX):
        return STATE_UNAVAILABLE, candidate.detail or ""
    if not candidate.exists:
        return STATE_UNAVAILABLE, (
            "no state directory exists yet, so a daemon running without "
            "persistence could not be seen from here"
        )
    if not candidate.readable:
        return STATE_UNAVAILABLE, candidate.detail or f"{candidate.dir} is not readable"
    if not candidate.pidfile_exists:
        return STATE_STOPPED, "no daemon has left a pidfile in this state directory"
    if candidate.locked is True:
        return STATE_RUNNING, ""
    if candidate.record is None:
        return STATE_DEAD_UNCLEAN, candidate.detail or "a pidfile is present but unreadable"
    if candidate.record.get("state") == "exited":
        return STATE_STOPPED, "the daemon exited and said so"
    return STATE_DEAD_UNCLEAN, "a pidfile says 'running' but nothing holds its lock"


def _identity_of(candidate: _Candidate) -> Optional[bool]:
    """Does the live process still match the ``(pid, start time)`` recorded?

    ``True`` proved, ``False`` disproved — a pidfile naming a process that is
    not the one that wrote it, which is worth seeing in ``status`` rather than
    only at ``stop`` time — and ``None`` when ``/proc`` cannot answer.
    """
    record = candidate.record or {}
    pid = _record_pid(record)
    recorded = record.get("start_time")
    if pid is None or not isinstance(recorded, int) or isinstance(recorded, bool):
        return None
    current = _process_start_time(pid)
    return None if current is None else current == recorded


def _candidate_note(candidate: _Candidate) -> Optional[dict[str, Any]]:
    """A secondary-note entry for *candidate*, or ``None`` if it has nothing to say."""
    if not candidate.pidfile_exists:
        return None
    state, _ = _classify(candidate)
    record = candidate.record or {}
    since = record.get("started_at")
    return {
        "state_dir": str(candidate.dir),
        "state": state,
        "pid": _record_pid(record),
        "since": since if isinstance(since, float) else None,
    }


def _live(candidates: list[_Candidate]) -> Optional[_Candidate]:
    return next((c for c in candidates if c.pidfile_exists and c.locked is True), None)


# ── results ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StartResult:
    """What :func:`start` did. Never an exception; the CLI turns it into one."""

    started: bool
    already_running: bool
    pid: Optional[int] = None
    state_dir: Optional[str] = None
    target: str = DEFAULT_TARGET
    stderr_path: Optional[str] = None
    reclaimed_stale_pid: Optional[int] = None
    #: Other candidate state directories whose stale pidfile this start
    #: reclaimed, so a corpse in the machine-wide fallback cannot outlive
    #: every future start.
    reclaimed_elsewhere: tuple[str, ...] = ()
    code: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "already_running": self.already_running,
            "pid": self.pid,
            "state_dir": self.state_dir,
            "target": self.target,
            "stderr_path": self.stderr_path,
            "reclaimed_stale_pid": self.reclaimed_stale_pid,
            "reclaimed_elsewhere": list(self.reclaimed_elsewhere),
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class StopResult:
    """What :func:`stop` did, including whether it had to escalate."""

    stopped: bool
    was_running: bool
    pid: Optional[int] = None
    escalated: bool = False
    confirmed: bool = False
    waited_seconds: float = 0.0
    state_dir: Optional[str] = None
    #: Whether the process signalled was proved to be the daemon — ``True``
    #: (pid AND start time matched immediately before each signal), ``False``
    #: (they did not, so nothing was signalled) or ``None`` (no ``/proc``, so
    #: the pair could not be checked; today's pid-only behaviour, reported
    #: rather than hidden).
    identity_verified: Optional[bool] = None
    code: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stopped": self.stopped,
            "was_running": self.was_running,
            "pid": self.pid,
            "escalated": self.escalated,
            "confirmed": self.confirmed,
            "waited_seconds": round(self.waited_seconds, 3),
            "state_dir": self.state_dir,
            "identity_verified": self.identity_verified,
            "code": self.code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class StatusReport:
    """The truthful answer to "is Gwen running?".

    ``state`` is one of :data:`STATE_RUNNING`, :data:`STATE_STOPPED`,
    :data:`STATE_DEAD_UNCLEAN` or :data:`STATE_UNAVAILABLE`. ``running`` means
    a process holds the pidfile's lock — it is **not** evidence that the daemon
    heard anything (the plan's third lesson); what it heard is a counter, and
    counters live in the operational log and the ledger, both reported here.
    """

    state: str
    pid: Optional[int] = None
    state_dir: Optional[str] = None
    pidfile: Optional[str] = None
    target: Optional[str] = None
    since: Optional[float] = None
    exit_code: Optional[int] = None
    hard_exit: Optional[bool] = None
    unfinished_threads: Optional[int] = None
    #: For a ``running`` daemon: whether the recorded ``(pid, start time)``
    #: still matches the live process. ``None`` when it is not running or
    #: ``/proc`` cannot answer.
    identity_verified: Optional[bool] = None
    ledger: dict[str, Any] = field(default_factory=dict)
    daemon_state: Optional[dict[str, Any]] = None
    candidates: list[str] = field(default_factory=list)
    #: What the OTHER candidate state directories hold, as
    #: ``{state_dir, state, pid, since}`` notes. Never the headline: a dead
    #: record in the machine-wide fallback directory is a note about another
    #: directory, not this machine's state.
    other_candidates: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "pid": self.pid,
            "state_dir": self.state_dir,
            "pidfile": self.pidfile,
            "target": self.target,
            "since": self.since,
            "exit_code": self.exit_code,
            "hard_exit": self.hard_exit,
            "unfinished_threads": self.unfinished_threads,
            "identity_verified": self.identity_verified,
            "ledger": self.ledger,
            "daemon_state": self.daemon_state,
            "candidates": list(self.candidates),
            "other_candidates": list(self.other_candidates),
            "detail": self.detail,
        }


# ── status: reads, never writes ──────────────────────────────────────────────

#: How many trailing ledger entries ``status`` carries. Enough to see what led
#: to an unclean death without turning a status call into a log dump.
_LEDGER_TAIL = 5


def _ledger_view(directory: Optional[Path]) -> dict[str, Any]:
    """A read-only view of a state dir's ledger. Never writes, never raises."""
    if directory is None:
        return {"path": None, "count": 0, "last": None, "recent": []}
    ledger = DegradationLedger(directory / LEDGER_FILENAME)
    summary = ledger.status()
    summary["recent"] = [record.to_dict() for record in ledger.read_all()[-_LEDGER_TAIL:]]
    return summary


def status(*, state_dir: Optional[str | Path] = None) -> StatusReport:
    """Report the daemon's state truthfully. Reads only; starts nothing.

    Deliberately does **not** construct a :class:`DaemonState`: that creates
    directories, sweeps temp files and appends degradation records, and a
    status call that changes what it reports is not a status call. The
    ``DaemonState.status()`` counters a reader wants are carried across the
    process boundary instead — the daemon writes a reduced snapshot of them
    into its own pidfile record, so ``daemon_state`` is *the daemon's* view,
    taken when it started, while ``ledger`` is read fresh on every call.

    Which candidate directory speaks
    ---------------------------------
    Searching every candidate exists to **find a live daemon** that fell back
    during bootstrap — not to let a *dead* one in another directory answer for
    the one the operator named. So:

    1. a **live lock in any candidate** is the headline, and the report names
       the directory it was found in;
    2. otherwise the **primary** (named, or resolved) directory's own state is
       the headline — stopped, dead, or never started;
    3. any other candidate holding a pidfile is a secondary note in
       :attr:`StatusReport.other_candidates`, never the headline.

    Rule 2 is not cosmetic. The machine-wide fallback directory
    (``<tempdir>/embodiment-state-<uid>``) is shared by every invocation on the
    box, nothing but a later ``start`` ever clears it, and letting a corpse
    there win made ``embodiment status`` report ``dead (unclean)`` on a
    perfectly healthy machine until someone deleted a file in ``/tmp`` by hand.
    """
    candidates, scan_error = _scan(state_dir)
    names = [str(c.dir) for c in candidates]
    if not candidates:
        return StatusReport(
            state=STATE_UNAVAILABLE,
            candidates=names,
            ledger=_ledger_view(None),
            detail=scan_error or "no candidate state directory could be resolved",
        )

    headline = _live(candidates) or candidates[0]
    state, detail = _classify(headline)
    identity = _identity_of(headline) if state == STATE_RUNNING else None
    notes = [
        note
        for candidate in candidates
        if candidate is not headline and (note := _candidate_note(candidate)) is not None
    ]
    return _report(state, headline, names, scan_error or detail, notes, identity)


def _report(
    state: str,
    candidate: _Candidate,
    names: list[str],
    detail: str,
    other_candidates: Optional[list[dict[str, Any]]] = None,
    identity_verified: Optional[bool] = None,
) -> StatusReport:
    """Build one :class:`StatusReport` from a scanned candidate. One code path."""
    record = candidate.record or {}
    target = record.get("target")
    return StatusReport(
        state=state,
        pid=_record_pid(record),
        state_dir=str(candidate.dir),
        pidfile=str(candidate.pidfile) if candidate.pidfile_exists else None,
        target=target if isinstance(target, str) and _validate_target(target) is None else None,
        since=record.get("started_at") if isinstance(record.get("started_at"), float) else None,
        exit_code=record.get("exit_code") if isinstance(record.get("exit_code"), int) else None,
        hard_exit=record.get("hard_exit") if isinstance(record.get("hard_exit"), bool) else None,
        unfinished_threads=(
            record.get("unfinished_threads")
            if isinstance(record.get("unfinished_threads"), int)
            else None
        ),
        ledger=_ledger_view(candidate.dir if candidate.readable else None),
        daemon_state=(
            record.get("daemon_state") if isinstance(record.get("daemon_state"), dict) else None
        ),
        candidates=names,
        identity_verified=identity_verified,
        other_candidates=list(other_candidates or []),
        detail=detail or (candidate.detail or ""),
    )


# ── start ────────────────────────────────────────────────────────────────────


def _open_stderr_log(directory: Path, ledger: DegradationLedger) -> tuple[Optional[int], Path]:
    """Open ``daemon.err`` append-only at 0600, bounding it across runs.

    Opened ``O_NOFOLLOW`` and then measured and truncated **through the
    descriptor**, never through the path: a symlink planted at ``daemon.err``
    would otherwise make ``start`` truncate whatever it points at.
    """
    path = directory / DAEMON_STDERR_NAME
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, _PRIVATE_FILE_MODE
        )
    except OSError:
        return None, path
    try:
        os.fchmod(fd, _PRIVATE_FILE_MODE)
        size = os.fstat(fd).st_size
    except OSError:
        size = 0
    if size > DEFAULT_STDERR_LOG_MAX_BYTES:
        try:
            os.ftruncate(fd, 0)
        except OSError as exc:
            ledger.append(
                STDERR_LOG_TRUNCATED_CODE, f"could not truncate: {describe_exception(exc)}"
            )
        else:
            ledger.append(
                STDERR_LOG_TRUNCATED_CODE,
                f"discarded {size} bytes of the previous run's stderr before starting",
            )
    return fd, path


def _child_argv(
    python: Optional[str], target: str, directory: Path, fd: int, shutdown_deadline: float
) -> list[str]:
    """The exact argv of the daemon child. Fixed shape, no shell, no user text."""
    return [
        python or sys.executable,
        "-m",
        "embodiment.daemon.lifecycle",
        "--target",
        target,
        "--state-dir",
        str(directory),
        "--pidfile-fd",
        str(fd),
        "--shutdown-deadline",
        repr(float(shutdown_deadline)),
    ]


def start(
    target: str = DEFAULT_TARGET,
    *,
    state_dir: Optional[str | Path] = None,
    env: Optional[dict[str, str]] = None,
    python: Optional[str] = None,
    shutdown_deadline: float = DEFAULT_SHUTDOWN_DEADLINE,
    confirm_timeout: float = DEFAULT_START_CONFIRM_TIMEOUT,
) -> StartResult:
    """Start the daemon in a detached background process. Idempotent; never raises.

    Returns ``already_running=True`` without spawning anything when a daemon
    already holds the lock in *any* candidate state directory. Returns
    ``started=True`` only once the child has taken its own pidfile record to
    ``running`` — the plan's "a counter that increments, not ``armed ==
    True``". Every other outcome carries a ``code`` and a ``detail``, and is
    also appended to the ledger.
    """
    invalid = _validate_target(target)
    if invalid is not None:
        return StartResult(
            False,
            False,
            target=str(target)[:_MAX_TARGET_LEN],
            code=TARGET_INVALID_CODE,
            detail=invalid,
        )

    candidates, scan_error = _scan(state_dir)
    if scan_error is not None:
        return StartResult(False, False, target=target, code=NO_STATE_DIR_CODE, detail=scan_error)
    existing = _live(candidates)
    if existing is not None:
        return StartResult(
            False,
            True,
            pid=_record_pid(existing.record),
            state_dir=str(existing.dir),
            target=target,
            detail="a daemon is already running",
        )

    state = DaemonState(state_dir)
    if state.dir is None:
        return StartResult(
            False,
            False,
            target=target,
            code=NO_STATE_DIR_CODE,
            detail="no state directory could be created, so no daemon could be claimed",
        )
    directory = state.dir
    ledger = state.ledger

    missing = _target_is_findable(
        target, (env or {}).get("PYTHONPATH", os.environ.get("PYTHONPATH"))
    )
    if missing is not None:
        ledger.append(TARGET_UNAVAILABLE_CODE, missing)
        return StartResult(
            False,
            False,
            state_dir=str(directory),
            target=target,
            code=TARGET_UNAVAILABLE_CODE,
            detail=missing,
        )

    pidfile = PidFile(directory / PIDFILE_NAME)
    if not pidfile.acquire():
        racer = _live(_scan(state_dir)[0])
        if racer is not None:
            return StartResult(
                False,
                True,
                pid=_record_pid(racer.record),
                state_dir=str(racer.dir),
                target=target,
                detail="a daemon is already running",
            )
        detail = (
            f"could not take the exclusive lock on {directory / PIDFILE_NAME}: "
            f"{pidfile.last_error or 'unknown reason'}"
        )
        ledger.append(START_BUSY_CODE, detail)
        return StartResult(
            False,
            False,
            state_dir=str(directory),
            target=target,
            code=START_BUSY_CODE,
            detail=detail,
        )

    elsewhere = _reclaim_other_candidates(candidates, directory, ledger)

    try:
        return _spawn(
            pidfile=pidfile,
            state=state,
            target=target,
            env=env,
            python=python,
            shutdown_deadline=shutdown_deadline,
            confirm_timeout=confirm_timeout,
            reclaimed_elsewhere=elsewhere,
        )
    finally:
        # The child inherited the same open file description, so closing our
        # copy leaves the lock held by the daemon and only by the daemon.
        pidfile.close()


def _reclaim_other_candidates(
    candidates: list[_Candidate], primary: Path, ledger: DegradationLedger
) -> tuple[str, ...]:
    """Mark stale pidfiles in the OTHER candidate directories as exited.

    Without this, a daemon that died uncleanly in the machine-wide fallback
    directory leaves a record nothing ever clears: a later ``start`` succeeds in
    the primary directory and the corpse stays for good. Reclaiming it is the
    same act ``start`` already performs on the primary directory's own stale
    pidfile, and it is recorded the same way — in *that* directory's ledger, so
    the evidence of the unclean death survives the record being overwritten.

    Only a pidfile whose lock is **free** is touched; a live one was already
    handled (``start`` returns ``already_running`` before reaching here). Never
    raises: a directory that cannot be written to is simply left alone.
    """
    reclaimed: list[str] = []
    for candidate in candidates:
        if candidate.dir == primary or not candidate.pidfile_exists:
            continue
        if candidate.locked is not False or candidate.record is None:
            continue
        if candidate.record.get("state") == "exited":
            continue
        dead = _record_pid(candidate.record)
        foreign = PidFile(candidate.pidfile)
        if not foreign.acquire():
            continue
        try:
            record = dict(candidate.record)
            record.update(
                {
                    "schema": PIDFILE_SCHEMA,
                    "state": "exited",
                    "reclaimed": True,
                    "hard_exit": True,
                    "stopped_at": time.time(),
                }
            )
            if foreign.write(record) is not None:
                continue
            detail = (
                f"reclaimed a pidfile left by pid {dead} in a non-primary state "
                f"directory that no longer holds its lock"
            )
            DegradationLedger(candidate.dir / LEDGER_FILENAME).append(
                STALE_PIDFILE_RECLAIMED_CODE, detail
            )
            ledger.append(STALE_PIDFILE_RECLAIMED_CODE, f"{detail} ({candidate.dir})")
            reclaimed.append(str(candidate.dir))
        finally:
            foreign.close()
    return tuple(reclaimed)


def _spawn(
    *,
    pidfile: PidFile,
    state: DaemonState,
    target: str,
    env: Optional[dict[str, str]],
    python: Optional[str],
    shutdown_deadline: float,
    confirm_timeout: float,
    reclaimed_elsewhere: tuple[str, ...] = (),
) -> StartResult:
    """The spawn half of :func:`start`, with the lock already held."""
    directory = state.dir
    assert directory is not None  # nosec B101 - the caller checked it
    ledger = state.ledger

    previous, _ = pidfile.read()
    reclaimed = None
    if previous is not None and previous.get("state") != "exited":
        reclaimed = _record_pid(previous)
        ledger.append(
            STALE_PIDFILE_RECLAIMED_CODE,
            f"reclaimed a pidfile left by pid {reclaimed} that no longer holds its lock",
        )

    log_fd, log_path = _open_stderr_log(directory, ledger)
    if log_fd is None:
        pidfile.unlink()
        detail = f"could not open {log_path} for the daemon's stderr"
        ledger.append(NO_STATE_DIR_CODE, detail)
        return StartResult(
            False,
            False,
            state_dir=str(directory),
            target=target,
            reclaimed_elsewhere=reclaimed_elsewhere,
            code=NO_STATE_DIR_CODE,
            detail=detail,
        )

    child_env = os.environ.copy()
    child_env.update(env or {})
    child_env["EMBODIMENT_STATE_DIR"] = str(directory)
    token = os.urandom(8).hex()

    try:
        proc = subprocess.Popen(  # nosec B603 - fixed argv, shell=False, no user text
            _child_argv(python, target, directory, pidfile.fd or -1, shutdown_deadline),
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
            pass_fds=(pidfile.fd,) if pidfile.fd is not None else (),
            cwd=str(directory),
            env=child_env,
            close_fds=True,
        )
    except OSError as exc:
        pidfile.unlink()
        detail = f"could not spawn the daemon: {describe_exception(exc)}"
        ledger.append(CHILD_EXITED_EARLY_CODE, detail)
        return StartResult(
            False,
            False,
            state_dir=str(directory),
            target=target,
            reclaimed_elsewhere=reclaimed_elsewhere,
            code=CHILD_EXITED_EARLY_CODE,
            detail=detail,
        )
    finally:
        try:
            os.close(log_fd)
        except OSError:
            pass  # narrow except; our copy of a descriptor the child now owns

    pidfile.write(
        {
            "schema": PIDFILE_SCHEMA,
            "pid": proc.pid,
            "token": token,
            "target": target,
            "state": "spawned",
            "started_at": time.time(),
            # Half of the daemon's identity, recorded as early as it can be:
            # a later ``stop`` compares it against /proc before every  signal, so a
            # recycled pid can never be mistaken for this process.
            "start_time": _process_start_time(proc.pid),
        }
    )
    return _confirm(
        proc=proc,
        pidfile=pidfile,
        ledger=ledger,
        directory=directory,
        target=target,
        log_path=log_path,
        reclaimed=reclaimed,
        reclaimed_elsewhere=reclaimed_elsewhere,
        confirm_timeout=confirm_timeout,
    )


def _confirm(
    *,
    proc: "subprocess.Popen[bytes]",
    pidfile: PidFile,
    ledger: DegradationLedger,
    directory: Path,
    target: str,
    log_path: Path,
    reclaimed: Optional[int],
    reclaimed_elsewhere: tuple[str, ...],
    confirm_timeout: float,
) -> StartResult:
    """Wait for the child to say it is running, or to die trying."""
    deadline = time.monotonic() + confirm_timeout
    while True:
        record, _ = pidfile.read()
        if record is not None and record.get("state") == STATE_RUNNING:
            return StartResult(
                True,
                False,
                pid=proc.pid,
                state_dir=str(directory),
                target=target,
                stderr_path=str(log_path),
                reclaimed_stale_pid=reclaimed,
                reclaimed_elsewhere=reclaimed_elsewhere,
                detail=f"daemon running as pid {proc.pid}",
            )
        rc = proc.poll()
        if rc is not None:
            detail = (
                f"the daemon process exited with code {rc} before it began running; "
                f"its output is in {log_path}"
            )
            ledger.append(CHILD_EXITED_EARLY_CODE, f"child exited with code {rc} during start")
            return StartResult(
                False,
                False,
                state_dir=str(directory),
                target=target,
                stderr_path=str(log_path),
                reclaimed_stale_pid=reclaimed,
                reclaimed_elsewhere=reclaimed_elsewhere,
                code=CHILD_EXITED_EARLY_CODE,
                detail=detail,
            )
        if time.monotonic() >= deadline:
            detail = (
                f"the daemon process (pid {proc.pid}) did not report itself running "
                f"within {confirm_timeout}s; its output is in {log_path}"
            )
            ledger.append(START_UNCONFIRMED_CODE, f"pid {proc.pid} unconfirmed after start")
            return StartResult(
                False,
                False,
                pid=proc.pid,
                state_dir=str(directory),
                target=target,
                stderr_path=str(log_path),
                reclaimed_stale_pid=reclaimed,
                reclaimed_elsewhere=reclaimed_elsewhere,
                code=START_UNCONFIRMED_CODE,
                detail=detail,
            )
        time.sleep(_POLL_INTERVAL)


# ── stop ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _TargetCheck:
    """Whether the process about to be signalled is still the daemon."""

    #: Safe to send a signal to this pid right now.
    safe: bool
    #: ``True`` proved, ``False`` disproved, ``None`` unknowable (no ``/proc``).
    verified: Optional[bool]
    #: A fixed vocabulary — never free text, never anything about the stranger.
    reason: str


def _verify_target(pidfile: Path, pid: int, expected_start: Optional[int]) -> _TargetCheck:
    """Re-prove the daemon's identity immediately before signalling it.

    Called once per signal rather than once per :func:`stop`, because the
    whole hazard lives in the gap: the daemon can exit between the scan and
    the ``os.kill``, and the kernel can hand pid *N* to another process of the
    same user, which would then receive our ``SIGTERM`` — and, worse, our
    ``SIGKILL``. ``_signallable`` does not cover it (the stranger is ours to
    signal) and neither does ``EPERM`` (we have permission).

    Three facts are re-read, in the order that makes each cheap:

    1. the lock is still held — if it is free the daemon is already gone and
       there is nothing to signal;
    2. the pidfile still names the same pid;
    3. the live process's start time still equals the one recorded for the
       daemon, which is what a recycled pid cannot fake.

    ``expected_start`` of ``None`` means the pair was never obtainable, so the
    check degrades to today's pid-only behaviour and says so through
    ``verified=None`` rather than refusing to stop the daemon.

    This narrows the window to the microseconds between the ``/proc`` read and
    the ``kill``; it does not close it. ``os.pidfd_open`` would, and is not
    available on this interpreter.
    """
    locked, _ = _probe_locked(pidfile)
    if locked is not True:
        return _TargetCheck(False, None, "lock-released")
    record, _ = read_pid_record(pidfile)
    if _record_pid(record) != pid:
        return _TargetCheck(False, False, "pid-changed")
    if expected_start is None:
        return _TargetCheck(True, None, "start-time-unavailable")
    current = _process_start_time(pid)
    if current is None:
        return _TargetCheck(True, None, "start-time-unavailable")
    if current != expected_start:
        return _TargetCheck(False, False, "start-time-changed")
    return _TargetCheck(True, True, "verified")


def _wait_for_release(pidfile: Path, seconds: float) -> bool:
    """Wait up to *seconds* for the pidfile's lock to go free."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        locked, _ = _probe_locked(pidfile)
        if locked is not True:
            return True
        time.sleep(_POLL_INTERVAL)
    locked, _ = _probe_locked(pidfile)
    return locked is not True


def stop(
    *,
    state_dir: Optional[str | Path] = None,
    timeout: float = DEFAULT_STOP_TIMEOUT,
    kill_grace: float = DEFAULT_KILL_GRACE,
) -> StopResult:
    """Stop the running daemon within a bounded time. Never raises.

    ``SIGTERM``, then at most *timeout* waiting for the lock to be released,
    then ``SIGKILL`` and at most *kill_grace* more. Escalation is recorded
    (:data:`STOP_ESCALATED_CODE`) because a daemon that had to be killed did
    not shut down, and that is a fact about the run, not an implementation
    detail. Stopping nothing is not an error: the result says ``was_running``
    is ``False`` and carries no code.
    """
    began = time.monotonic()
    candidates, scan_error = _scan(state_dir)
    if scan_error is not None:
        return StopResult(False, False, code=NO_STATE_DIR_CODE, detail=scan_error)
    live = _live(candidates)
    if live is None:
        return _nothing_to_stop(candidates)

    ledger = DegradationLedger(live.dir / LEDGER_FILENAME)
    pid = _record_pid(live.record)
    refusal = _signallable(pid)
    if refusal is not None:
        ledger.append(REFUSED_PID_CODE, refusal)
        return StopResult(
            False,
            True,
            pid=pid,
            state_dir=str(live.dir),
            waited_seconds=time.monotonic() - began,
            code=REFUSED_PID_CODE,
            detail=refusal,
        )
    assert pid is not None  # nosec B101 - _signallable already vouched for it

    target = _StopTarget(
        pidfile=live.pidfile,
        dir=live.dir,
        pid=pid,
        expected_start=_expected_start_time(live.record, pid),
        ledger=ledger,
        began=began,
    )

    failure = _signal_target(target, signal.SIGTERM, False)
    if failure is not None:
        return failure

    if _wait_for_release(live.pidfile, timeout):
        return target.result(True, confirmed=True, detail=f"daemon {pid} stopped")

    ledger.append(
        STOP_ESCALATED_CODE,
        f"pid {pid} did not exit within {timeout}s of SIGTERM; escalating to SIGKILL",
    )
    failure = _signal_target(target, signal.SIGKILL, True)
    if failure is not None:
        return failure

    if _wait_for_release(live.pidfile, kill_grace):
        return target.result(
            True,
            escalated=True,
            confirmed=True,
            detail=f"daemon {pid} did not shut down and was killed",
        )

    detail = f"pid {pid} still holds the daemon lock after SIGKILL"
    ledger.append(STOP_UNCONFIRMED_CODE, detail)
    return target.result(
        False, escalated=True, confirmed=False, code=STOP_UNCONFIRMED_CODE, detail=detail
    )


def _nothing_to_stop(candidates: list[_Candidate]) -> StopResult:
    """The result for a ``stop`` that found no live lock anywhere."""
    # The same precedence rule ``status`` follows: a live lock in ANY
    # candidate is the thing to stop, but with nothing live the answer is
    # reported against the PRIMARY directory. A dead record in the
    # machine-wide fallback is not something to stop, and naming it here
    # would point the operator at the wrong directory.
    primary = candidates[0] if candidates else None
    return StopResult(
        False,
        False,
        state_dir=str(primary.dir) if primary is not None else None,
        detail="no running daemon found in any candidate state directory",
    )


def _expected_start_time(record: Optional[dict[str, Any]], pid: int) -> Optional[int]:
    """The process start time the daemon's identity is checked against."""
    # The daemon's identity: the pid PLUS the moment its process started. The
    # child records the pair at spawn; if it is missing (an older pidfile, or
    # no /proc at spawn time) it is captured here, right after the lock probe
    # — later than ideal, still before any signal.
    recorded_start = record.get("start_time") if record else None
    if isinstance(recorded_start, int) and not isinstance(recorded_start, bool):
        return recorded_start
    return _process_start_time(pid)


@dataclass
class _StopTarget:
    """The daemon :func:`stop` is signalling, and what each signal learned about it."""

    pidfile: Path
    dir: Path
    pid: int
    expected_start: Optional[int]
    ledger: DegradationLedger
    #: When ``stop`` began, for ``waited_seconds``.
    began: float
    #: The latest :attr:`_TargetCheck.verified`, carried onto every result.
    verified: Optional[bool] = None
    #: The unverifiable degradation is recorded once per stop, not once per signal.
    unverifiable_recorded: bool = False

    def result(
        self,
        stopped: bool,
        *,
        escalated: bool = False,
        confirmed: bool = False,
        code: Optional[str] = None,
        detail: str = "",
    ) -> StopResult:
        """A :class:`StopResult` about this target, stamped with what is known now."""
        return StopResult(
            stopped,
            True,
            pid=self.pid,
            escalated=escalated,
            confirmed=confirmed,
            state_dir=str(self.dir),
            waited_seconds=time.monotonic() - self.began,
            identity_verified=self.verified,
            code=code,
            detail=detail,
        )


def _signal_target(target: _StopTarget, sig: int, escalating: bool) -> Optional[StopResult]:
    """Signal the target only if it is still the daemon. Returns a result to
    return early, or ``None`` to carry on to :func:`_wait_for_release`."""
    check = _verify_target(target.pidfile, target.pid, target.expected_start)
    target.verified = check.verified
    if check.safe and check.verified is None and not target.unverifiable_recorded:
        # Signalling without having been able to prove identity. That is
        # the old behaviour, which is fine, but it is a degradation of
        # this check and the host is told (C3) — once per stop, not once
        # per signal.
        target.unverifiable_recorded = True
        target.ledger.append(
            IDENTITY_UNVERIFIABLE_CODE,
            f"could not prove pid {target.pid} is the daemon ({check.reason}); "
            "signalling on the pid alone",
        )
    if not check.safe:
        if check.reason != "lock-released":
            # A named code and the reason only. NOT the stranger's pid,
            # its name or anything else about it: this is a record, and
            # the process that inherited the pid is not ours to describe.
            target.ledger.append(
                STOP_TARGET_CHANGED_CODE,
                f"refused to signal pid {target.pid}: {check.reason}",
            )
        return None
    try:
        os.kill(target.pid, sig)
    except OSError as exc:
        target.ledger.append(
            SIGNAL_FAILED_CODE,
            f"signal {int(sig)} to pid {target.pid}: {describe_exception(exc)}",
        )
        return target.result(
            False,
            escalated=escalating,
            code=SIGNAL_FAILED_CODE,
            detail=f"could not signal pid {target.pid}: {describe_exception(exc)}",
        )
    return None


# ── the daemon side of the stop path ─────────────────────────────────────────


def _reduced_state_snapshot(state: DaemonState) -> dict[str, Any]:
    """``DaemonState.status()`` reduced to what may safely ride in a pidfile.

    Counts and paths only. ``last_write_error`` and ``ledger.last`` are
    deliberately dropped: they are free-form strings, and a reader that wants
    them can read the ledger, which is where they already live.
    """
    snapshot = state.status()
    log = snapshot.get("operational_log", {})
    ledger = snapshot.get("ledger", {})
    return {
        "state_dir": snapshot.get("state_dir"),
        "persistence": snapshot.get("persistence", "ok"),
        "operational_log": {
            "size_bytes": log.get("size_bytes"),
            "max_bytes": log.get("max_bytes"),
            "write_error_count": log.get("write_error_count", 0),
        },
        "ledger": {
            "count": ledger.get("count", 0),
            "write_error_count": ledger.get("write_error_count", 0),
        },
    }


class DaemonRunner:
    """Runs one :class:`Runnable` inside the daemon process, with a hard bound.

    The watchdog is the whole point. A target parked in a blocking read cannot
    be woken by a signal (PEP 475 restarts the call), and a non-daemon worker
    thread keeps the interpreter alive after ``run`` returns. So once a stop is
    requested, a daemon thread counts down *shutdown_deadline* against plain
    booleans — never a lock a signal handler could have interrupted — and, if
    the graceful path has not finished, writes what is unfinished to the ledger
    **first** and then calls ``exit_process`` (``os._exit`` by default), which
    no parked thread can delay.
    """

    def __init__(
        self,
        runnable: Runnable,
        *,
        state: DaemonState,
        pidfile: Optional[PidFile] = None,
        target: Optional[str] = None,
        shutdown_deadline: float = DEFAULT_SHUTDOWN_DEADLINE,
        exit_process: Callable[[int], Any] = os._exit,
    ) -> None:
        self._runnable = runnable
        self._state = state
        self._pidfile = pidfile
        self._target = target
        self._shutdown_deadline = float(shutdown_deadline)
        self._exit_process = exit_process
        self.stop_event = threading.Event()
        self._stop_requested = False
        self._finished = False
        self._exiting = False
        self._exit_lock = threading.Lock()

    def request_stop(self, reason: str = "signal") -> None:
        """Ask the daemon to stop and arm the watchdog. Safe from a signal handler.

        The boolean is set *before* the event, because the watchdog reads only
        the boolean: its bound must not depend on a lock that a signal arriving
        mid-``Event.set`` could have left contended.
        """
        self._stop_requested = True
        self.stop_event.set()

    def _on_signal(self, signum: int, _frame: Any) -> None:
        self.request_stop(f"signal-{signum}")

    def _watch(self) -> None:
        while not self._stop_requested:
            if self._exiting or self._finished:
                return
            time.sleep(_POLL_INTERVAL)
        deadline = time.monotonic() + self._shutdown_deadline
        while time.monotonic() < deadline:
            if self._exiting:
                return
            time.sleep(_POLL_INTERVAL)
        self._finalise(DAEMON_EXIT_HARD, hard=True)

    def _mark_running(self) -> None:
        """Take the pidfile record to ``running``. Ordering is load-bearing.

        This is what :func:`start` waits for, so it must happen *after* the
        signal handlers are installed and the watchdog is up — never before.
        Announcing readiness first leaves a window in which the daemon is
        stoppable by name but not yet stoppable in fact: a ``SIGTERM`` landing
        there takes the default action, killing the process before it can write
        its clean-exit marker, and ``status`` then reports a deliberate stop as
        ``dead (unclean)``. That race was measured, not imagined.
        """
        if self._pidfile is None:
            return
        record, _ = self._pidfile.read()
        base = dict(record or {})
        base.update(
            {
                "schema": PIDFILE_SCHEMA,
                "pid": os.getpid(),
                "state": STATE_RUNNING,
                "started_at": time.time(),
                # Authoritative: read by the process it identifies.
                "start_time": _process_start_time(os.getpid()),
                "daemon_state": _reduced_state_snapshot(self._state),
            }
        )
        if self._target:
            base["target"] = self._target
        self._pidfile.write(base)

    def run(self, *, install_signal_handlers: bool = True) -> int:
        """Run the target to completion, then exit the process. Never returns normally."""
        if install_signal_handlers:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    signal.signal(sig, self._on_signal)
                except (ValueError, OSError) as exc:
                    self._state.ledger.append(
                        SHUTDOWN_FAILED_CODE,
                        f"could not install a handler for signal {int(sig)}: "
                        f"{describe_exception(exc)}",
                    )
        threading.Thread(target=self._watch, name="embodiment-daemon-watchdog", daemon=True).start()
        self._mark_running()
        self._state.operational_log.write("started", pid=os.getpid())

        code = 0
        try:
            result = self._runnable.run(self.stop_event)
            code = result if isinstance(result, int) and not isinstance(result, bool) else 0
        except Exception as exc:  # noqa: BLE001  # a target's failure is recorded, never raised
            self._state.ledger.append(
                TARGET_FAILED_CODE, f"the daemon target raised {describe_exception(exc)}"
            )
            code = DAEMON_EXIT_TARGET_FAILED

        # Stopping either way now, so the watchdog bounds the shutdown call too.
        self.request_stop("run-returned")
        shutdown = getattr(self._runnable, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown(self._shutdown_deadline)
            except Exception as exc:  # noqa: BLE001  # recorded, never raised
                self._state.ledger.append(
                    SHUTDOWN_FAILED_CODE,
                    f"the daemon target's shutdown raised {describe_exception(exc)}",
                )
        self._finished = True
        self._finalise(code, hard=False)
        return code  # pragma: no cover - _finalise exits the process

    def _lingering(self) -> tuple[int, str]:
        """Non-daemon threads that would hold the interpreter open: ``(count, digest)``.

        The count and a digest — **never the names**. A thread name is chosen
        by the daemon *application*, so a host that names a worker after what
        it is working on would put that text straight into a degradation
        record. Attacking this module's own records is how that leak was
        found: a marker planted in a thread name came back out of the ledger
        verbatim, because sanitising the charset does nothing to a marker that
        is already alphanumeric. The digest keeps the useful property — the
        same set of stuck threads produces the same digest twice — without
        writing anything the host supplied.
        """
        current = threading.current_thread()
        names = sorted(
            thread.name
            for thread in threading.enumerate()
            if thread is not current and not thread.daemon and thread.is_alive()
        )
        if not names:
            return 0, "none"
        digest = hashlib.sha256("\x00".join(names).encode("utf-8", "surrogateescape")).hexdigest()
        return len(names), digest[:16]

    def _finalise(self, code: int, *, hard: bool) -> None:
        """Record what is unfinished, mark the pidfile, then exit. One code path."""
        with self._exit_lock:
            if self._exiting:
                return
            self._exiting = True

        count, digest = self._lingering()
        if hard:
            self._state.ledger.append(
                HARD_EXIT_CODE,
                f"hard exit {self._shutdown_deadline}s after a stop was requested; "
                f"{count} non-daemon thread(s) still running (name digest {digest})",
            )
        elif count:
            self._state.ledger.append(
                THREADS_LINGERING_CODE,
                f"{count} non-daemon thread(s) still running at exit (name digest {digest})",
            )
        self._state.operational_log.write("stopped", exit_code=code, hard_exit=hard)
        self._mark_exited(code, hard=hard, lingering=count)
        _flush_stdio()
        self._exit_process(code)

    def _mark_exited(self, code: int, *, hard: bool, lingering: int) -> None:
        """Write the clean-exit marker that separates 'stopped' from 'dead'."""
        if self._pidfile is None:
            return
        record, _ = self._pidfile.read()
        base = dict(record or {})
        base.update(
            {
                "schema": PIDFILE_SCHEMA,
                "state": "exited",
                "exit_code": code,
                "hard_exit": hard,
                "unfinished_threads": lingering,
                "stopped_at": time.time(),
                "daemon_state": _reduced_state_snapshot(self._state),
            }
        )
        base.setdefault("pid", os.getpid())
        self._pidfile.write(base)


def _flush_stdio() -> None:
    """Flush the streams ``os._exit`` would otherwise discard. Never raises."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            continue


def run_daemon(
    runnable: Runnable,
    *,
    state: DaemonState,
    pidfile: Optional[PidFile] = None,
    target: Optional[str] = None,
    shutdown_deadline: float = DEFAULT_SHUTDOWN_DEADLINE,
    exit_process: Callable[[int], Any] = os._exit,
    install_signal_handlers: bool = True,
) -> int:
    """Convenience wrapper over :class:`DaemonRunner` — the same code path."""
    return DaemonRunner(
        runnable,
        state=state,
        pidfile=pidfile,
        target=target,
        shutdown_deadline=shutdown_deadline,
        exit_process=exit_process,
    ).run(install_signal_handlers=install_signal_handlers)


# ── the child entry point (``python -m embodiment.daemon.lifecycle``) ────────


def _child_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="embodiment-daemon",
        description="Internal: the daemon child process. Use 'embodiment start'.",
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--pidfile-fd", type=int, default=-1)
    parser.add_argument("--shutdown-deadline", type=float, default=DEFAULT_SHUTDOWN_DEADLINE)
    return parser


def _child_main(argv: Optional[list[str]] = None) -> int:
    """Run inside the detached child. Never lets a traceback be the whole story."""
    args = _child_parser().parse_args(argv)
    directory = resolve_state_dir(args.state_dir)
    state = DaemonState(directory)
    pidfile: Optional[PidFile] = None
    if args.pidfile_fd >= 0:
        pidfile = PidFile.from_fd(directory / PIDFILE_NAME, args.pidfile_fd)

    factory, error = resolve_target(args.target)
    if factory is None:
        state.ledger.append(TARGET_UNAVAILABLE_CODE, error or "unresolvable target")
        _mark_child_failure(pidfile, state, DAEMON_EXIT_TARGET_UNAVAILABLE)
        return DAEMON_EXIT_TARGET_UNAVAILABLE

    try:
        runnable = factory()
    except Exception as exc:  # noqa: BLE001  # the target's own failure, recorded
        state.ledger.append(
            TARGET_FAILED_CODE, f"building the daemon target raised {describe_exception(exc)}"
        )
        _mark_child_failure(pidfile, state, DAEMON_EXIT_TARGET_FAILED)
        return DAEMON_EXIT_TARGET_FAILED

    if not callable(getattr(runnable, "run", None)):
        state.ledger.append(
            TARGET_FAILED_CODE, "the daemon target does not provide run(stop_event)"
        )
        _mark_child_failure(pidfile, state, DAEMON_EXIT_TARGET_FAILED)
        return DAEMON_EXIT_TARGET_FAILED

    # The record is taken to "running" inside the runner, once the signal
    # handlers and the watchdog are up — see DaemonRunner._mark_running.
    return run_daemon(
        runnable,
        state=state,
        pidfile=pidfile,
        target=args.target,
        shutdown_deadline=args.shutdown_deadline,
    )


def _mark_child_failure(pidfile: Optional[PidFile], state: DaemonState, code: int) -> None:
    """A child that never began running still leaves an honest record."""
    if pidfile is None:
        return
    record, _ = pidfile.read()
    base = dict(record or {})
    base.update(
        {
            "schema": PIDFILE_SCHEMA,
            "pid": os.getpid(),
            "state": "exited",
            "exit_code": code,
            "hard_exit": False,
            "unfinished_threads": 0,
            "stopped_at": time.time(),
            "daemon_state": _reduced_state_snapshot(state),
        }
    )
    pidfile.write(base)


if __name__ == "__main__":  # pragma: no cover - exercised by real child processes
    sys.exit(_child_main())
