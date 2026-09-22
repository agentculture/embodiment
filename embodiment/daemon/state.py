"""embodiment.daemon.state — the daemon's on-disk state: dir, bounded log, ledger.

Task t4 of the ``realtime-embodiment-app`` plan. This module is the one place
the background daemon touches disk before it touches anything else: it
resolves where the daemon's own files live, and it gives the rest of the
daemon three primitives to write through rather than each part inventing its
own file handling.

Three surfaces, and why they are three and not one
---------------------------------------------------
* :func:`resolve_state_dir` — an XDG state directory that never depends on the
  process's current working directory, so it resolves the same way whether
  the daemon is started from inside this repo, a colleague worktree, or a
  systemd unit with an unrelated cwd. Constraint from ``CLAUDE.md``'s
  "Privacy of the room": this repo commits ``.eidetic/memory/``, so anything
  the daemon writes for itself must default to a location that is provably
  outside any git working tree, never a path a ``cwd``-relative default could
  accidentally point back into a checkout.
* :class:`OperationalLog` — a size-bounded, rotating JSONL log of the
  daemon's own operational events (started, stopped, degraded, connected...).
  Bounded because a background process with no operator watching it must
  never be the reason a disk fills. It never carries transcript text — that is
  a discipline enforced by *keeping it a different file* from the one below,
  not by scanning strings for it, mirroring the shape of every other
  degrade-never-raise seam in this package (see :mod:`embodiment.events`).
* :class:`TranscriptLog` — the private, size-bounded, per-session transcript
  log the spec's retention rule calls for (``CLAUDE.md``: "raw audio is never
  written to disk; transcripts go to a private, size-bounded per-session
  log"). :meth:`DaemonState.open_transcript` is the "session API" a caller
  reaches for one: a session's spoken turns land in their own bounded file
  under ``<state dir>/sessions/``, never in :class:`OperationalLog`.
  ``embodiment/session.py`` (plan task t11, not this one) is expected to
  build the full session lifecycle — context window, remembering — on top of
  this primitive; t4's job stops at "the write path exists, is bounded, and
  is a different file from the operational log."

Crash durability, stated precisely
------------------------------------
The ledger is deliberately NOT size-bounded — a background daemon's few
recorded degradations are the one thing this module must never lose to a
rotation, and their record shape is small enough that unbounded growth is not
the risk bounded logs exist to prevent. :class:`OperationalLog` and
:class:`TranscriptLog` ARE bounded, exactly as their acceptance criterion
requires ("the log never exceeds its configured size") — see the next two
sections for how that bound is now kept cheap.

Append below the bound, rewrite only when crossing it
------------------------------------------------------
Every write used to pay for a full read-rewrite-``fsync``-rename of the whole
file, even when the file was nowhere near its bound — measured at 3.38 ms and
3.43 ms per write for a 62 kB file against a 1 MB bound and a 20 kB file at
its 20 kB bound respectively: identical, because the cost scaled with file
size and disk latency, not with how close the file was to its limit. Below
the bound, :func:`_append_line` now does a true ``O_APPEND`` write of exactly
one line — no read, no rewrite, cost independent of file size. A write is
routed to the expensive rewrite path (:func:`_bounded_rewrite_append`) ONLY
when appending the new line would push the file over *max_bytes*; when that
happens, the file is trimmed not to *max_bytes* itself but down to
:data:`_LOW_WATER_RATIO` of it (75%), so the freed 25% of headroom buys
roughly that many bytes' worth of subsequent writes — hundreds of small
records, for the sizes this module deals in — as plain appends again before
another rewrite is needed. Trimming to exactly *max_bytes* would instead
rewrite on almost every single write once a log is full, which is the same
defect restated at the boundary rather than fixed. The size-never-exceeds-
the-bound invariant holds after EVERY write either way: an append is only
taken when it is already known to stay at or under the bound, and a rewrite
always trims to *low_water_bytes* ≤ *max_bytes*, so the boundary case (a
write landing exactly on a full log) still keeps the guarantee.

A crash mid-append cannot glue the next write to its fragment
-------------------------------------------------------------------
``O_APPEND`` appends raw bytes at the file's current end-of-file, with no
concept of "lines" — so if a previous write was interrupted after writing
some but not all of its bytes, the file ends mid-line with no trailing
newline, and a naive next append would land immediately after that fragment,
merging two records into one line that can never parse (this is a defect,
not merely the well-known "torn last line": the READER already tolerated a
torn last line before this fix, but the record written right after it would
be silently lost forever, not just delayed by one skip). :func:`_append_line`
and :func:`_bounded_rewrite_append` both check whether the file's current
content already ends with a newline and prepend one when it does not, so a
torn fragment always stays on its own (permanently unparseable, correctly
skipped) line and every write after it starts clean. Every reader
(:meth:`DegradationLedger.read_all`, :meth:`_BoundedJsonlLog.read_all` — the
one implementation behind both :class:`OperationalLog` and
:class:`TranscriptLog`) already skips a line that fails to parse rather than
failing the whole read, so this is the write-side half of a guarantee the
read side already held.

fsync policy, stated per log type rather than left to accident
---------------------------------------------------------------------
:data:`LEDGER_FSYNC_PER_APPEND` is ``True``: the ledger IS the crash record,
so every append is flushed and ``fsync``\\ ed before returning — a process
killed at any point leaves every prior append durably on disk. The cost is
one ``fsync`` syscall per degradation, which is rare (a background daemon
that is behaving records at most a handful over its whole run), so paying a
few milliseconds per occurrence is immaterial in aggregate.
:data:`OPERATIONAL_LOG_FSYNC_PER_APPEND` and
:data:`TRANSCRIPT_LOG_FSYNC_PER_APPEND` are both ``False``: an ``fsync``-free
append still reaches the kernel's page cache before ``write()`` returns, so
it survives this PROCESS crashing (the exact scenario the orphaned-temp-file
sweep and the torn-line tolerance above both already assume can happen) —
the only loss window is the kernel or the machine itself going down before
the page cache is flushed, i.e. an OS crash or power loss, which loses at
most the unwritten tail of operational history or conversation transcript.
That is an acceptable trade for logs that are operational record-keeping, not
the crash ledger itself, and it is what removes the per-write ``fsync`` from
the hot spoken-turn path.

Concurrency: append atomicity is a stated assumption, not just relied on
-----------------------------------------------------------------------------
Two threads appending to the same log must never interleave bytes within a
line. A single ``os.write()`` to a local, regular file opened with
``O_APPEND`` is atomic on Linux — the kernel serializes the seek-to-end and
the write under the inode lock for the duration of one ``write()`` syscall,
for any size that one syscall actually transfers (unlike ``PIPE_BUF``, which
bounds atomicity for pipes/FIFOs specifically, not regular files) — but this
module does not rely on that alone: every :meth:`_BoundedJsonlLog._write` and
:meth:`DegradationLedger.append` is additionally serialized behind a lock, so
correctness holds even on a filesystem where single-``write()`` atomicity
does not (network filesystems such as NFS are the known exception; this
module has not been measured against one). The lock also closes a race the
kernel's write atomicity alone would not: two threads independently deciding
"we're under the bound, append" from a stale size read could together push
the file over it; holding the lock across the whole decide-then-write makes
that decision atomic too.

One lock per PATH, process-wide — not one per instance
-------------------------------------------------------------
That lock used to be per-INSTANCE, which is only as strong as "every writer
on a file is the same object." It is not: two calls to
:meth:`DaemonState.open_transcript` for the same session id used to each
mint an independent :class:`TranscriptLog` on the same file, and two
:class:`DaemonState` objects pointed at the same directory each mint their
own :class:`OperationalLog`/:class:`DegradationLedger`. Measured with two
independently-locked instances on one 3 kB-bounded file, two threads writing
400 records each: 25 records survived, but the two instances' own
``evicted_records`` summed to 807 against 800 written — a rewrite ran over a
stale read from the OTHER instance's writes and silently dropped MORE than
it should have, while both instances' counters kept counting as if nothing
had been lost. No write error, nothing in ``status()`` — a second silent
loss on top of the first (see "a bounded buffer counts what it drops" above)
that counting alone cannot catch when the thing racing is the lock itself.

:func:`_lock_for_path` fixes this at the root: a module-level registry,
keyed by the RESOLVED path and guarded by its own lock (a handful of paths
for a process's lifetime — no weak-reference cleanup is worth the
complexity here), hands out the SAME ``threading.Lock`` to every
:class:`_BoundedJsonlLog`/:class:`DegradationLedger` instance ever
constructed on that path in this process. :meth:`DaemonState.open_transcript`
also now caches its return per ``session_id`` (a second call for the same id
returns the identical object — pinned by a test), which removes the most
common way two instances on one path would arise in practice; the registry
lock is the fix for the general case, including the one caching cannot
reach (two separate :class:`DaemonState` objects on the same directory).

Explicitly out of scope: a SECOND PROCESS writing the same path. This
registry is in-process memory only and coordinates nothing across processes
— the daemon design keeps exactly one process per state directory (a
pidfile-guarded single-instance lock, plan task t5, not this one), which is
the actual control against a cross-process collision.

on_degrade never runs while a write lock is held
------------------------------------------------------
A hook passed as ``on_degrade`` might itself write to the SAME log instance
(a host logging "I got told about a degradation" back into its own
operational log, say) — if the hook fired while :func:`_lock_for_path`'s
lock were still held, that write would try to re-acquire a lock this
thread already holds and deadlock forever (``threading.Lock`` is not
re-entrant; measured: a 2 s wait that never returns). Every write path
collects what it needs to tell ``on_degrade`` — a ``(code, detail)`` pair —
while the lock is held, but only CALLS the hook after the ``with`` block
that holds the lock has exited, whether the write succeeded, failed, or hit
an oversize record. One rule, one code path, for every reason a write can
degrade.

An unreadable tail is treated as dirty, never as clean
-------------------------------------------------------------
Deciding whether the next append needs a leading newline (see "a crash
mid-append cannot glue" above) means reading the file's last byte. That
read can itself fail — a file made writable-but-unreadable (mode ``0200``)
by something else touching it, for instance — and treating a failed probe
as "confirmed clean, no prefix needed" glues the new record onto whatever
the file's actual (unread, possibly torn) tail was, with no error anywhere:
the write still reports success. :func:`_probe_tail` treats any probe
failure other than the file simply not existing as UNKNOWN, and unknown is
treated as dirty: a newline prefix is written regardless. A spurious blank
line ahead of a good record is harmless (the reader already skips blank
lines); a record silently glued onto an unread fragment and then dropped by
the reader is a lost record. The probe failure itself is also counted, on
:attr:`~_BoundedJsonlLog.probe_errors`, so this defensive choice is visible
rather than another silent correction.

A trimmed torn fragment is not an evicted record
--------------------------------------------------
A rewrite's trim can pop a line that was never a real record to begin with
— the torn fragment an interrupted earlier write left behind, still sitting
unparsed at the front of the file when a later crossing trims it away.
Counting that as one more "evicted record" overstates how much real history
a log has lost. :func:`_trim_to_low_water` checks whether each popped line
parses as JSON: a real record bumps :attr:`~_BoundedJsonlLog.evicted_records`
as before; an unparsed fragment bumps the separate
:attr:`~_BoundedJsonlLog.fragments_dropped` instead.

A record that cannot be JSON-serialized degrades, never raises
-------------------------------------------------------------------
``OperationalLog.write(event, **fields)`` accepts arbitrary keyword fields,
and nothing stopped a caller from passing something ``json.dumps`` cannot
encode (bytes, a custom object, ...) — that raised a bare ``TypeError``
straight out of an API this whole module promises never raises. The record
is now dropped, counted on ``write_errors`` naming the offending field(s)
and their TYPE (``bytes``, ``MyClass``, ...) — never ``repr()`` of the
value, which could itself carry exactly the kind of arbitrary or sensitive
payload this module elsewhere goes out of its way never to log.

A bounded buffer counts what it drops
-----------------------------------------
Trimming a full file is invisible unless something counts it: before this
addition, a rewrite that dropped 50 old records, or a single record too big
to ever fit (silently kept alone forever, exceeding the bound), left no
trace anywhere — ``write_errors`` stayed empty, ``status()`` showed nothing,
the ledger recorded nothing. Every :class:`_BoundedJsonlLog` (both
:class:`OperationalLog` and :class:`TranscriptLog` — one shared
implementation, one code path) now counts three things, in memory only,
never persisted, and surfaced through :meth:`_BoundedJsonlLog.status` and
therefore :meth:`DaemonState.status`:

* :attr:`~_BoundedJsonlLog.evicted_records` — the running total of prior
  records a trim has dropped, across every rewrite this instance has ever
  done.
* :attr:`~_BoundedJsonlLog.rewrites` — how many times the expensive rewrite
  path ran at all.
* :attr:`~_BoundedJsonlLog.oversize_records` — records whose own encoded size
  already exceeds *max_bytes*, so no trim could ever make room. These are now
  NEVER WRITTEN (previously kept alone, silently exceeding the configured
  bound — the opposite of what "the log never exceeds its configured size"
  promises) — each one is counted, and the first one on a given instance
  fires exactly ONE deduped :data:`OVERSIZE_RECORD_CODE` degradation through
  the existing ``on_degrade`` seam, carrying the byte count and the bound,
  never the record's own text (repeats after the first only bump the
  counter, mirroring the "degrade once" discipline :mod:`embodiment.events`
  already uses).

Counting costs nothing on the hot append path: the oversize check and the
size comparison that routes a write to append vs. rewrite are both already
in hand before any I/O (an encoded-length comparison and one ``stat()``, both
present before this addition), and the eviction count is a byproduct of a
rewrite that was already reading and rewriting the whole file — no extra file
read was added anywhere.

Session ids are untrusted input
-----------------------------------
:meth:`DaemonState.open_transcript` will eventually be reached from network
clients (a browser tab, a robot relay) supplying their own session id, so a
session id is treated as adversarial: :func:`_safe_session_name` accepts only
a conservative filename charset, rejects anything else, and derives a
deterministic replacement name from a hash of the rejected id — never the id
itself — so a rejection can be recorded without ever writing untrusted text
to disk. The resolved path is additionally checked to stay inside the
sessions directory before use (belt-and-suspenders: the charset already makes
escape structurally impossible, but the check is there rather than trusted).

Privacy, on disk, regardless of umask
-----------------------------------------
Every directory this module creates for its own exclusive use (the state
directory, ``sessions/``) is forced to mode ``0700``; every file it writes
(operational log, ledger, transcript, and the temp file the bounded logs'
atomic rewrite uses before ``os.replace``) is forced to mode ``0600``. Both
are enforced with an explicit ``chmod``/``fchmod`` rather than trusted to the
``mode=`` argument of ``mkdir``/``open`` alone, because that argument is
itself masked by the process umask and a permissive umask (``0022`` is a
common default) would otherwise leave transcripts of a private conversation
group- or world-readable.

The state-directory fallback is deterministic and guarded
---------------------------------------------------------------
If the preferred state directory cannot be created at all, the daemon falls
back to :func:`resolve_fallback_state_dir` — ``<tempdir>/embodiment-state-<uid>``
— rather than a randomly-named temp directory. A random name would be
unfindable by a second process (a separate ``embodiment status`` invocation
resolving the normal path would see nothing and wrongly report "stopped" —
exactly the confusion this module exists to prevent); a deterministic,
per-owner name lets :func:`candidate_state_dirs` hand a reader the same
directory a writer would have fallen back to. A predictable name in a shared
temp directory is also an attack surface, so it is never used blindly: a
pre-existing symlink at that name, or a pre-existing directory owned by
another uid, is refused (never raised, only recorded) and a last-resort
randomly-named directory is used instead.

Never raise, record instead (C3)
-----------------------------------
No public method here raises for an environment problem (a missing
directory, a permission error, a full disk). Every disk operation is guarded
by a narrow ``except OSError`` (never a bare or ``Exception``-wide catch —
see ``tests/test_no_silent_degradation.py``'s AST scan, which this module
must never need an allow-list entry from) and every failure is *recorded*,
never swallowed: on the bounded logs and the ledger it lands in
``write_errors`` and :meth:`DaemonState.status` reports each log's error
count and most recent error (type and message — filesystem detail, never
transcript text); on :class:`DaemonState` itself, a state-directory bootstrap
failure or an unsafe fallback falls back further and writes exactly one
ledger entry per transition, so a host reading ``status()`` can see it
happened.

The floor: no persistence at all
------------------------------------
Even the last-resort fallback (:func:`resolve_fallback_state_dir`, or —
refused — a fresh ``tempfile.mkdtemp()``) can fail: a missing or unwritable
system temp directory is possible, however rare. That case must still never
raise out of :class:`DaemonState`'s constructor, so it is not an error path,
it is a MODE: when no directory could be created anywhere, ``self.dir`` is
``None``, :attr:`DaemonState.operational_log` and :attr:`DaemonState.ledger`
are still real, working objects — every write to either is simply dropped
and counted on ``write_errors`` — and :meth:`DaemonState.open_transcript`
still returns a working (dropping) :class:`TranscriptLog`. Nothing a caller
holds ever needs a ``None`` check to stay in the "never raise" contract.
:meth:`DaemonState.status` reports ``state_dir: None`` and a
``persistence_detail`` naming why, rather than a state dir that silently
looks empty and healthy.

Orphaned temp files from an unclean death
---------------------------------------------
A process killed between :func:`_atomic_write_bytes` writing its temp file
and the ``os.replace`` that lands it leaves that temp file behind forever —
nothing else ever removes it. :class:`DaemonState` sweeps for exactly this
module's own temp-file naming pattern (see :func:`_sweep_stale_temp_files`)
in the state directory and ``sessions/`` on every construction and records
ONE degradation naming the count when it finds any, because a nonzero count
is itself evidence of the previous process's unclean death — information
``status()`` should carry forward, not silently clean away.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = [
    "STATE_DIR_ENV_VAR",
    "DEFAULT_OPERATIONAL_LOG_MAX_BYTES",
    "DEFAULT_TRANSCRIPT_LOG_MAX_BYTES",
    "OPERATIONAL_LOG_FILENAME",
    "LEDGER_FILENAME",
    "SESSIONS_DIRNAME",
    "STATE_DIR_FALLBACK_CODE",
    "STATE_DIR_TIGHTENED_CODE",
    "SESSION_ID_REJECTED_CODE",
    "STALE_TEMP_FILES_SWEPT_CODE",
    "OVERSIZE_RECORD_CODE",
    "PERSISTENCE_STATUS_UNAVAILABLE",
    "LEDGER_FSYNC_PER_APPEND",
    "OPERATIONAL_LOG_FSYNC_PER_APPEND",
    "TRANSCRIPT_LOG_FSYNC_PER_APPEND",
    "resolve_state_dir",
    "resolve_fallback_state_dir",
    "candidate_state_dirs",
    "DegradationRecord",
    "DegradationLedger",
    "OperationalLog",
    "TranscriptLog",
    "DaemonState",
]

#: The test/override seam. Read only when *override* is not passed explicitly
#: to :func:`resolve_state_dir` or :class:`DaemonState`.
STATE_DIR_ENV_VAR = "EMBODIMENT_STATE_DIR"

#: 1 MiB. Generous enough for a busy day of operational events, small enough
#: that an unattended daemon never turns a forgotten process into a full disk.
DEFAULT_OPERATIONAL_LOG_MAX_BYTES = 1_000_000

#: Same bound, applied per session rather than per daemon lifetime.
DEFAULT_TRANSCRIPT_LOG_MAX_BYTES = 1_000_000

OPERATIONAL_LOG_FILENAME = "embodiment.log"
LEDGER_FILENAME = "degradations.jsonl"
SESSIONS_DIRNAME = "sessions"

#: The degradation code :class:`DaemonState` records when it could not create
#: or use its preferred state directory and fell back to a temporary one.
STATE_DIR_FALLBACK_CODE = "state-dir-fallback"

#: Recorded when a directory this module owns existed but was more open than
#: 0700 and had to be (or could not be) tightened.
STATE_DIR_TIGHTENED_CODE = "state-dir-permissions-tightened"

#: Recorded when a caller-supplied session id was rejected by
#: :func:`_safe_session_name` and a derived name was used instead.
SESSION_ID_REJECTED_CODE = "session-id-rejected"

#: Recorded once, with a count, when construction finds and removes orphaned
#: atomic-rewrite temp files left by a previous unclean death.
STALE_TEMP_FILES_SWEPT_CODE = "stale-temp-files-swept"

#: Recorded via ``on_degrade``, ONCE per :class:`_BoundedJsonlLog` instance no
#: matter how many times it recurs (deduped — see :attr:`_BoundedJsonlLog.
#: oversize_records` for the running count), when a single record's own
#: encoded size already exceeds the log's *max_bytes* on its own. The record
#: is never written. Detail carries the record's byte count and the bound —
#: never the record's own text.
OVERSIZE_RECORD_CODE = "state-record-exceeds-bound"

#: The value :meth:`DaemonState.status` reports for ``persistence`` when no
#: directory — preferred or any fallback — could be created at all.
PERSISTENCE_STATUS_UNAVAILABLE = "unavailable"

#: Filesystem modes enforced regardless of the process umask.
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600

#: The degradation ledger IS the crash record — every append is durably
#: fsync'd before returning. See the module docstring's fsync policy section.
LEDGER_FSYNC_PER_APPEND = True

#: The operational log and the transcript log are not the crash record; an
#: append without fsync still survives a process crash (already in the
#: kernel's page cache) and only loses its unflushed tail to an OS crash or
#: power loss. Not fsyncing removes a syscall from every write on these two
#: logs, one of which (the transcript) sits on the spoken-turn hot path.
OPERATIONAL_LOG_FSYNC_PER_APPEND = False
TRANSCRIPT_LOG_FSYNC_PER_APPEND = False

#: When an append would cross *max_bytes*, the rewrite trims down to this
#: FRACTION of the bound rather than to the bound itself. Trimming to exactly
#: *max_bytes* would make the very next write cross it again immediately —
#: a full rewrite on nearly every write once a log is full, the same cost
#: this fix exists to remove, just relocated to the boundary. 0.75 is chosen
#: (not measured) as a middle ground: it frees 25% of the bound's byte
#: budget as headroom, which for this module's small (tens-to-low-hundreds
#: of bytes) JSONL records buys hundreds of subsequent plain appends before
#: the next rewrite, while still keeping at least 75% of the configured
#: window of history immediately after a trim.
_LOW_WATER_RATIO = 0.75

#: The exact shape of a temp file :func:`_atomic_write_bytes` creates:
#: ``.<original filename>.<random token>.tmp``. Deliberately specific — a
#: leading dot, a literal ``.tmp`` suffix, and an alphanumeric middle token
#: with no dots of its own — so :func:`_sweep_stale_temp_files` can never
#: mistake an unrelated dotfile (``.gitignore``, a editor swap file, ...) for
#: one of ours.
_TMP_FILE_RE = re.compile(r"^\..+\.[A-Za-z0-9_]+\.tmp$")

#: Session ids are accepted only in this conservative filename charset —
#: letters, digits, dot, underscore, hyphen — which structurally cannot
#: contain a path separator or a ``..`` traversal segment once a leading dot
#: is also rejected (see :func:`_safe_session_name`).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SESSION_ID_MAX_LEN = 200

# ── one lock per resolved path, process-wide ────────────────────────────────

#: Guards :data:`_PATH_LOCKS` itself — never held for longer than a dict
#: lookup/insert.
_PATH_LOCKS_GUARD = threading.Lock()

#: ``resolved path -> the lock every writer on that path shares``. See the
#: module docstring's "one lock per PATH, process-wide" section. Grows by at
#: most a handful of entries over a process's life; never pruned (per-process
#: state directory paths are few and this module does not churn them).
_PATH_LOCKS: dict[Path, threading.Lock] = {}


def _lock_for_path(path: Path) -> threading.Lock:
    """The process-wide lock every ``_BoundedJsonlLog``/``DegradationLedger``
    instance on *path* shares. See the module docstring's "one lock per PATH,
    process-wide" section for why a per-instance lock was not enough.
    """
    resolved = path.resolve()
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[resolved] = lock
        return lock


def resolve_state_dir(override: Optional[str | Path] = None) -> Path:
    """Resolve the daemon's state directory. Never consults the cwd.

    Precedence, highest first:

    1. *override* — an explicit argument (a CLI flag, or a test's ``tmp_path``).
    2. the ``EMBODIMENT_STATE_DIR`` environment variable — the test seam.
    3. ``XDG_STATE_HOME`` (``<XDG_STATE_HOME>/embodiment``), the XDG base
       directory spec's own override.
    4. ``~/.local/state/embodiment``, the XDG default.

    Every one of these bases is either an explicit absolute path or resolved
    from the home directory / an environment variable — none of them reads
    the process's current working directory — so the result is the same
    directory regardless of where the daemon happens to have been started
    from, including from inside this git repository.
    """
    if override is not None:
        base = Path(override)
    else:
        env_override = os.environ.get(STATE_DIR_ENV_VAR)
        if env_override:
            base = Path(env_override)
        else:
            xdg_state_home = os.environ.get("XDG_STATE_HOME")
            if xdg_state_home:
                base = Path(xdg_state_home) / "embodiment"
            else:
                base = Path.home() / ".local" / "state" / "embodiment"
    return base.expanduser().resolve()


def _owner_tag() -> str:
    """A stable per-OS-user token for the fallback directory's name."""
    uid_fn = getattr(os, "getuid", None)
    return str(uid_fn()) if uid_fn is not None else "shared"


def resolve_fallback_state_dir() -> Path:
    """The deterministic fallback used when the preferred dir is unusable.

    Same directory every time for the same OS user (``<tempdir>/embodiment-
    state-<uid>``), so a separate process — a later ``embodiment status`` —
    can find a daemon that fell back to it during bootstrap, which a
    randomly-named temp directory never could.
    """
    return (Path(tempfile.gettempdir()) / f"embodiment-state-{_owner_tag()}").resolve()


def candidate_state_dirs(override: Optional[str | Path] = None) -> list[Path]:
    """Every directory a reader should check, in the order a writer would use them.

    ``embodiment status`` (plan task t5, not this one) resolves the normal
    state dir first; if that shows nothing, this is where it looks next.
    """
    return [resolve_state_dir(override), resolve_fallback_state_dir()]


# ── directory privacy ────────────────────────────────────────────────────────


def _ensure_private_dir(path: Path) -> Optional[str]:
    """Ensure *path* exists and is mode 0700 (owner rwx only). Never raises.

    Returns a human-readable detail when something departed from the silent
    happy path: could not create it at all; a pre-existing directory had to
    be tightened; or tightening itself failed. Returns ``None`` on the fully
    silent happy path — created fresh (whatever the umask left it at is
    corrected without comment) or already existed at exactly 0700.
    """
    pre_existing = path.is_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"could not create {path}: {type(exc).__name__}: {exc}"
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        return f"could not stat {path} after creating it: {type(exc).__name__}: {exc}"
    if mode == _PRIVATE_DIR_MODE:
        return None
    try:
        os.chmod(path, _PRIVATE_DIR_MODE)
    except OSError as exc:
        return (
            f"could not tighten permissions on {path} (was {oct(mode)}): "
            f"{type(exc).__name__}: {exc}"
        )
    if pre_existing:
        return f"tightened pre-existing {path} from {oct(mode)} to {oct(_PRIVATE_DIR_MODE)}"
    return None


def _secure_fallback_dir(path: Path) -> tuple[bool, Optional[str]]:
    """Validate and secure a candidate fallback directory. Never raises.

    A predictable name in a shared temp directory is an attack surface: a
    symlink planted at that name, or a pre-existing directory owned by
    someone else, is refused rather than written through — checked BEFORE
    :func:`_ensure_private_dir` creates/chmods it, and again AFTER, closing
    the window between the two: another actor in a shared temp directory
    could in principle swap the path for a symlink in between (the reviewer's
    analysis is that today's ``chmod``-based creation happens to fail safely
    across that window too, but this makes it safe BY CONSTRUCTION rather
    than by accident of ``chmod`` semantics). Returns ``(usable, detail)`` —
    *detail* is ``None`` only on the fully silent path (freshly created,
    nothing to note, re-check confirms it).
    """
    if path.is_symlink():
        return False, f"refusing fallback dir {path}: it is a symlink"
    if path.exists():
        if not path.is_dir():
            return False, f"refusing fallback dir {path}: exists and is not a directory"
        try:
            owner = path.stat().st_uid
        except OSError as exc:
            return False, f"refusing fallback dir {path}: could not stat it: {exc}"
        uid_fn = getattr(os, "getuid", None)
        if uid_fn is not None and owner != uid_fn():
            return False, f"refusing fallback dir {path}: owned by uid {owner}, not us"
    detail = _ensure_private_dir(path)
    if detail is not None and detail.startswith("could not create"):
        return False, detail
    return _recheck_after_create(path, detail)


def _recheck_after_create(path: Path, detail: Optional[str]) -> tuple[bool, Optional[str]]:
    """The re-check half of :func:`_secure_fallback_dir`: trust nothing handed
    back by ``_ensure_private_dir`` — look at the path again with ``lstat``.
    """
    try:
        st = os.lstat(path)
    except OSError as exc:
        return False, f"refusing fallback dir {path}: could not lstat it after creating: {exc}"
    if stat.S_ISLNK(st.st_mode):
        return False, f"refusing fallback dir {path}: became a symlink after creation"
    if not stat.S_ISDIR(st.st_mode):
        return False, f"refusing fallback dir {path}: is not a directory after creation"
    uid_fn = getattr(os, "getuid", None)
    if uid_fn is not None and st.st_uid != uid_fn():
        return False, f"refusing fallback dir {path}: owned by uid {st.st_uid} after creation"
    mode = stat.S_IMODE(st.st_mode)
    if mode != _PRIVATE_DIR_MODE:
        return False, f"refusing fallback dir {path}: mode {oct(mode)} after creation, not 0700"
    return True, detail


def _bootstrap_fallback_dir() -> tuple[Optional[Path], str]:
    """The deterministic fallback, or — refused/unusable — a fresh random one.

    Never raises. Returns ``(dir_in_use, detail)``; *detail* is always
    non-empty, because falling back at all is itself worth one degradation
    record even when the fallback directory itself needed no repair.
    ``dir_in_use`` is ``None`` only when even a freshly, randomly named
    directory could not be created — the system temp directory itself is
    missing or unwritable — the floor described in the module docstring's
    "no persistence at all" section.
    """
    candidate = resolve_fallback_state_dir()
    usable, detail = _secure_fallback_dir(candidate)
    if usable:
        return candidate, detail or f"using deterministic fallback {candidate}"
    try:
        random_dir = Path(tempfile.mkdtemp(prefix="embodiment-state-fallback-"))
    except OSError as exc:
        combined = (
            f"{detail}; could not create a random fallback either: {type(exc).__name__}: {exc}"
        )
        return None, combined
    try:
        os.chmod(random_dir, _PRIVATE_DIR_MODE)
    except OSError:
        pass  # mkdtemp already creates at 0700; this is only a defensive re-assert
    combined = f"{detail}; using a random, unfindable fallback instead: {random_dir}"
    return random_dir, combined


# ── session ids are untrusted input ─────────────────────────────────────────


def _safe_session_name(session_id: str) -> tuple[str, Optional[str]]:
    """A filesystem-safe stem for *session_id*, plus a rejection detail if any.

    Only a conservative charset is accepted (``[A-Za-z0-9._-]``, length
    capped, never a bare ``.``/``..``, never a leading dot) so the resulting
    filename can never contain a path separator or a traversal segment.
    Anything else is replaced by a deterministic name derived from a hash of
    the rejected id — never the id itself, which may be adversarial or
    sensitive — so the caller can record ONE degradation without ever
    writing untrusted text to disk.
    """
    digest = hashlib.sha256(session_id.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]
    fallback = f"invalid-{digest}"
    if not session_id:
        return fallback, f"empty session id (hash {digest})"
    if len(session_id) > _SESSION_ID_MAX_LEN:
        return fallback, f"session id too long: {len(session_id)} chars (hash {digest})"
    if session_id in (".", ".."):
        return fallback, f"session id is a path segment (hash {digest})"
    if session_id.startswith("."):
        return fallback, f"session id has a leading dot (hash {digest})"
    if not _SESSION_ID_RE.match(session_id):
        return fallback, f"session id has disallowed characters (hash {digest})"
    return session_id, None


# ── shared disk helpers ─────────────────────────────────────────────────────


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* via a same-directory temp file + ``os.replace``.

    The temp file (and, once renamed, *path* itself) is forced to mode 0600
    regardless of umask. Raises ``OSError`` on failure — callers decide how
    to degrade, per this module's "never raise, record instead" discipline;
    this helper stays a plain, honest primitive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, _PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.remove(tmp_name)
        except OSError:
            pass  # narrow except; best-effort cleanup of our own temp file
        raise


def _trim_to_low_water(combined: bytes, low_water_bytes: int) -> tuple[bytes, int, int]:
    """Drop whole lines from the OLDEST end of *combined* until it fits at or
    under *low_water_bytes*. Only called when *combined* already exceeds
    the caller's ``max_bytes``; a single line larger than that on its own is kept
    alone regardless — nothing smaller is available and the file must stay
    valid JSONL. See :data:`_LOW_WATER_RATIO` for why the target is the low
    water mark and not *max_bytes* itself.

    Returns ``(trimmed, dropped_records, dropped_fragments)``. Each popped
    line is classified by whether it parses as JSON: a real record bumps
    *dropped_records* (what the caller adds to :attr:`_BoundedJsonlLog.
    evicted_records`); a torn fragment an earlier interrupted write left
    behind — never a real record to begin with — bumps *dropped_fragments*
    instead, so eviction counts never overstate how much real history a log
    has lost (see the module docstring's "a trimmed torn fragment is not an
    evicted record" section). Only ever pops from index 0, which is always a
    real prior line — the sentinel empty element ``combined.split(b"\\n")``
    leaves at the end (from the trailing newline) is never popped.
    """
    lines = combined.split(b"\n")
    dropped_records = 0
    dropped_fragments = 0
    while len(lines) > 2 and sum(len(item) + 1 for item in lines) > low_water_bytes:
        popped = lines.pop(0)
        try:
            json.loads(popped)
        except json.JSONDecodeError:
            dropped_fragments += 1
        else:
            dropped_records += 1
    return b"\n".join(lines), dropped_records, dropped_fragments


def _bounded_rewrite_append(
    path: Path, line: str, max_bytes: int, low_water_bytes: int
) -> tuple[int, int]:
    """Append *line* to *path* by rewriting it, trimming to *low_water_bytes*
    if the combined size would exceed *max_bytes*.

    The EXPENSIVE path: reads the whole file, so callers route a write here
    only when a plain append (:func:`_append_line`) would cross *max_bytes* —
    see the module docstring's "append below the bound, rewrite only when
    crossing it" section. If the existing content's last line is torn (an
    interrupted previous write, no trailing newline), a newline is inserted
    before concatenating the new line, for the same reason
    :func:`_append_line` does: gluing onto a fragment would make the new
    record unparseable forever, not just skip the fragment once.

    Returns ``(dropped_records, dropped_fragments)`` — both ``0`` when the
    new line fit without trimming — see :func:`_trim_to_low_water`.
    """
    existing = b""
    if path.exists():
        existing = path.read_bytes()
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    encoded = line.encode("utf-8") + b"\n"
    combined = existing + encoded
    dropped_records = 0
    dropped_fragments = 0
    if len(combined) > max_bytes:
        combined, dropped_records, dropped_fragments = _trim_to_low_water(combined, low_water_bytes)
    _atomic_write_bytes(path, combined)
    return dropped_records, dropped_fragments


def _probe_tail(path: Path) -> tuple[bool, bool]:
    """Whether the next append needs a leading newline, and whether the
    probe itself failed.

    Returns ``(needs_prefix, probe_error)``. A file that does not exist yet
    is not an error — there is no fragment to avoid — so ``(False, False)``.
    Any OTHER read failure (a file made writable-but-unreadable, mode
    ``0200``, by something else touching it) can never be read as "confirmed
    clean": that would risk gluing the next record onto an unread, possibly
    torn tail and losing it. So it is treated as dirty — ``needs_prefix``
    is ``True`` regardless — and reported back as a probe error so the
    caller can count it; see the module docstring's "an unreadable tail is
    treated as dirty" section.
    """
    try:
        with open(path, "rb") as handle:  # noqa: PTH123  # a raw byte peek, not text
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return False, False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n", False
    except FileNotFoundError:
        return False, False
    except OSError:
        return True, True


def _last_byte_is_newline_or_empty(path: Path) -> bool:
    """``True`` if *path* does not exist, is empty, or already ends in ``\\n``.

    ``False`` if the tail could not even be read — see :func:`_probe_tail`,
    which this is a thin boolean-only wrapper over (kept for existing
    callers; new code that also needs the probe-failure flag should call
    :func:`_probe_tail` directly).
    """
    needs_prefix, _probe_error = _probe_tail(path)
    return not needs_prefix


def _append_line(
    path: Path, line: str, *, fsync: bool, prefix_needed: Optional[bool] = None
) -> None:
    """A true ``O_APPEND`` write of exactly one JSONL line. Never reads or
    rewrites existing content — the CHEAP path, used for every write that
    stays under a log's bound (and always, for the unbounded ledger). Mode
    0600 regardless of umask.

    Prefixes the write with a newline when the file's current last byte is
    not already one, so a torn last line from an interrupted previous append
    is never glued to — see the module docstring's "a crash mid-append
    cannot glue" and "an unreadable tail is treated as dirty" sections.
    *prefix_needed*, when given, skips this function's own probe (the caller
    already ran :func:`_probe_tail` once, e.g. to fold into its size
    decision — see :meth:`_BoundedJsonlLog._append_or_rewrite`); when
    omitted, this function probes for itself.

    *fsync* decides whether this call durably syncs to disk before
    returning — see the module docstring's fsync policy section and
    :data:`LEDGER_FSYNC_PER_APPEND` / :data:`OPERATIONAL_LOG_FSYNC_PER_APPEND`
    / :data:`TRANSCRIPT_LOG_FSYNC_PER_APPEND`. Raises ``OSError`` on failure —
    callers decide how to degrade.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if prefix_needed is None:
        prefix_needed, _probe_error = _probe_tail(path)
    prefix = b"\n" if prefix_needed else b""
    encoded = prefix + (line + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _PRIVATE_FILE_MODE)
    try:
        os.fchmod(fd, _PRIVATE_FILE_MODE)
        os.write(fd, encoded)
        if fsync:
            os.fsync(fd)
    finally:
        os.close(fd)


def _sweep_stale_temp_files(directory: Path) -> int:
    """Delete this module's own orphaned atomic-rewrite temp files. Never raises.

    Matches ONLY :data:`_TMP_FILE_RE` — this module's exact temp-file naming
    pattern — and only regular files, never directories or symlinks, never
    anything else found in *directory*. Returns the count removed, which is
    ``0`` when *directory* does not exist or nothing matched.
    """
    if not directory.is_dir():
        return 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        if not _TMP_FILE_RE.match(entry.name):
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            entry.unlink()
        except OSError:
            continue
        else:
            removed += 1
    return removed


# ── the degradation ledger ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DegradationRecord:
    """One host-visible transition (C3), as it lands on disk."""

    ts: float
    code: str
    detail: str
    id: str

    def to_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "code": self.code, "detail": self.detail, "id": self.id}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DegradationRecord":
        return DegradationRecord(
            ts=float(data.get("ts", 0.0)),
            code=str(data.get("code", "")),
            detail=str(data.get("detail", "")),
            id=str(data.get("id", "")),
        )


class DegradationLedger:
    """An append-only, crash-durable JSONL ledger of degradation records.

    Every :meth:`append` is a true ``O_APPEND`` write (:func:`_append_line`),
    ``fsync``\\ ed before returning (:data:`LEDGER_FSYNC_PER_APPEND`) — there
    is no in-memory buffer that a crash could lose, and no read of the
    existing file (the ledger is never rewritten; it only ever grows).
    :meth:`status` (and :meth:`read_all`, which it is built on) tolerates a
    torn final line, which is what an interrupted append looks like on disk:
    everything before it stays readable, and :func:`_append_line`'s own
    torn-line guard means the NEXT append after one starts on a fresh line
    rather than gluing onto the fragment. Every append is serialized behind
    the PROCESS-WIDE lock every instance on this same path shares — see
    :func:`_lock_for_path` and the module docstring's "one lock per PATH,
    process-wide" section.

    *path* may be ``None`` — the no-persistence floor described in the module
    docstring, used when :class:`DaemonState` could not create a state
    directory anywhere. A ``None``-path ledger is still a working object:
    every :meth:`append` is simply dropped and counted on
    :attr:`write_errors`, and reads report an always-empty ledger, rather than
    a caller needing a special case for "there is no ledger".
    """

    def __init__(self, path: Optional[str | Path]) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = _lock_for_path(self._path) if self._path is not None else threading.Lock()
        #: Records this instance could not persist at all (the ledger file's
        #: own directory is unwritable, or there is no directory at all).
        #: Best-effort visibility of last resort; normally empty. Surfaced by
        #: :meth:`DaemonState.status`.
        self.write_errors: list[str] = []

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def persistent(self) -> bool:
        """``False`` in no-persistence mode — see the class docstring."""
        return self._path is not None

    def append(self, code: str, detail: str = "") -> Optional[DegradationRecord]:
        """Append one record. Returns it on success, ``None`` on failure.

        Never raises: a failure to persist is itself recorded, in memory, on
        :attr:`write_errors` — the last-resort degradation of a subsystem
        whose whole job is recording degradations.
        """
        record = DegradationRecord(ts=time.time(), code=code, detail=detail, id=uuid.uuid4().hex)
        if self._path is None:
            self.write_errors.append(f"no persistence available: {code}: {detail}")
            return None
        line = json.dumps(record.to_dict(), sort_keys=True)
        try:
            with self._lock:
                _append_line(self._path, line, fsync=LEDGER_FSYNC_PER_APPEND)
        except OSError as exc:
            self.write_errors.append(f"{type(exc).__name__}: {exc}")
            return None
        return record

    def read_all(self) -> list[DegradationRecord]:
        """Every readable record, oldest first. A torn last line is skipped."""
        if self._path is None or not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[DegradationRecord] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # A crash mid-write tears exactly the LAST line; earlier
                # entries are already fsync'd whole. Skip it rather than fail
                # the whole read.
                continue
            records.append(DegradationRecord.from_dict(data))
        return records

    def status(self) -> dict[str, Any]:
        """A summary ``status`` can read after a crash: count and the last entry."""
        records = self.read_all()
        return {
            "path": str(self._path) if self._path is not None else None,
            "count": len(records),
            "last": records[-1].to_dict() if records else None,
        }


# ── bounded rotating logs ───────────────────────────────────────────────────


class _BoundedJsonlLog:
    """Shared implementation behind :class:`OperationalLog` and :class:`TranscriptLog`.

    Not part of the public surface — both public classes exist as distinct
    types on purpose (see the module docstring's "different file" discipline)
    even though their mechanics are identical.

    Every write goes through :meth:`_append_or_rewrite`, which appends
    (:func:`_append_line`, cheap, no file read) when that stays under
    *max_bytes*, and only reaches the expensive rewrite
    (:func:`_bounded_rewrite_append`) when appending would cross it — see the
    module docstring's "append below the bound, rewrite only when crossing
    it" section. *fsync* is a required, explicit, per-subclass choice (see
    the module docstring's fsync policy section) — there is deliberately no
    default, so a new subclass cannot inherit a policy by accident. Every
    write is serialized behind the PROCESS-WIDE lock every instance on this
    same path shares (:func:`_lock_for_path`, module docstring's "one lock
    per PATH, process-wide" section), and ``on_degrade`` is always called
    AFTER that lock is released (module docstring's "on_degrade never runs
    while a write lock is held" section) — never from inside
    :meth:`_append_or_rewrite` itself.

    *path* may be ``None`` — the no-persistence floor (module docstring):
    every :meth:`_write` is dropped and counted on :attr:`write_errors`
    rather than the object being unusable.

    Counters — all in memory only, never persisted:

    * :attr:`evicted_records` / :attr:`rewrites` / :attr:`oversize_records` —
      see the module docstring's "a bounded buffer counts what it drops"
      section.
    * :attr:`fragments_dropped` — a torn fragment a trim popped, counted
      separately from a real evicted record (module docstring's "a trimmed
      torn fragment is not an evicted record" section).
    * :attr:`probe_errors` — the tail-newline probe itself failed and a
      prefix was written defensively (module docstring's "an unreadable
      tail is treated as dirty" section).

    All mutated only from :meth:`_append_or_rewrite` while the process-wide
    path lock is held, so they are exact under concurrent writers, same as
    everything else that lock protects.
    """

    def __init__(
        self,
        path: Optional[str | Path],
        *,
        max_bytes: int,
        fsync: bool,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._max_bytes = max_bytes
        self._fsync = fsync
        self._on_degrade = on_degrade
        self._lock = _lock_for_path(self._path) if self._path is not None else threading.Lock()
        self._oversize_degraded = False
        #: Failures to persist a record. Best-effort visibility of last
        #: resort; surfaced by :meth:`DaemonState.status`.
        self.write_errors: list[str] = []
        #: Total prior records dropped by every rewrite this instance has
        #: ever done. In-memory only; never persisted.
        self.evicted_records: int = 0
        #: How many times the expensive rewrite path has run.
        self.rewrites: int = 0
        #: Records whose own encoded size alone exceeds *max_bytes* — never
        #: written, always counted, see :data:`OVERSIZE_RECORD_CODE`.
        self.oversize_records: int = 0
        #: Torn fragments a trim popped, counted separately from real
        #: evicted records — see the class docstring.
        self.fragments_dropped: int = 0
        #: Times the tail-newline probe itself failed (an unreadable file) —
        #: a prefix was written defensively regardless; see the class
        #: docstring.
        self.probe_errors: int = 0

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def persistent(self) -> bool:
        """``False`` in no-persistence mode — see the class docstring."""
        return self._path is not None

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def fsync(self) -> bool:
        """This instance's fsync-per-write policy — see the module docstring."""
        return self._fsync

    def _write(self, record: dict[str, Any]) -> None:
        if self._path is None:
            self.write_errors.append("no persistence available: dropped one record")
            return
        try:
            line = json.dumps(record, sort_keys=True)
        except (TypeError, ValueError) as exc:
            self._record_serialization_failure(record, exc)
            return
        pending_degrade: Optional[tuple[str, str]] = None
        try:
            with self._lock:
                pending_degrade = self._append_or_rewrite(line)
        except OSError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self.write_errors.append(reason)
            pending_degrade = ("log-write-failed", reason)
        # ONE call site for the hook, always after the lock is released — a
        # hook that writes back to this same log would otherwise deadlock on
        # a lock this thread still held. See the module docstring's
        # "on_degrade never runs while a write lock is held" section.
        if pending_degrade is not None and self._on_degrade is not None:
            code, detail = pending_degrade
            try:
                self._on_degrade(code, detail)
            except OSError:
                pass  # narrow except; the degrade hook must never itself raise

    def _record_serialization_failure(self, record: dict[str, Any], exc: Exception) -> None:
        """A record with a field ``json.dumps`` cannot encode never raises out
        of :meth:`_write`. Names each offending field and its TYPE — never
        ``repr()`` of the value, which could carry exactly the kind of
        arbitrary or sensitive payload this module never logs elsewhere.
        """
        bad_fields = []
        for key, value in record.items():
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                bad_fields.append(f"{key}:{type(value).__name__}")
        if not bad_fields:
            bad_fields = [f"<unknown field>:{type(exc).__name__}"]
        self.write_errors.append(f"record dropped, not JSON-serializable: {', '.join(bad_fields)}")

    def _append_or_rewrite(self, line: str) -> Optional[tuple[str, str]]:
        """Called with the process-wide path lock held. Picks the cheap or
        expensive path.

        An oversize record (encoded size alone over *max_bytes*) is never
        written; a plain append is taken when it stays under the bound
        (O(1): one tail probe, one ``stat()``, no file read); only a genuine
        crossing reaches the rewrite path, which is where the eviction count
        comes from. Returns a pending ``(code, detail)`` for
        :meth:`_write` to hand ``on_degrade`` AFTER releasing the lock, or
        ``None`` when nothing needs to degrade — this method itself never
        calls the hook.
        """
        assert self._path is not None  # guarded by the caller
        encoded_body_len = len(line.encode("utf-8")) + 1  # + the line's own trailing newline
        if encoded_body_len > self._max_bytes:
            return self._record_oversize(encoded_body_len)
        try:
            current_size = self._path.stat().st_size
        except OSError:
            current_size = 0
        needs_prefix, probe_error = _probe_tail(self._path)
        if probe_error:
            self.probe_errors += 1
        prefix_len = 1 if needs_prefix else 0
        if current_size + prefix_len + encoded_body_len <= self._max_bytes:
            _append_line(self._path, line, fsync=self._fsync, prefix_needed=needs_prefix)
            return None
        low_water_bytes = int(self._max_bytes * _LOW_WATER_RATIO)
        dropped_records, dropped_fragments = _bounded_rewrite_append(
            self._path, line, self._max_bytes, low_water_bytes
        )
        self.rewrites += 1
        self.evicted_records += dropped_records
        self.fragments_dropped += dropped_fragments
        return None

    def _record_oversize(self, encoded_len: int) -> Optional[tuple[str, str]]:
        """Count an oversize record; return a pending degrade for the FIRST
        occurrence only (deduped) — the caller invokes it after releasing
        the lock (never from here — see :meth:`_append_or_rewrite`).
        """
        self.oversize_records += 1
        if self._oversize_degraded:
            return None
        self._oversize_degraded = True
        detail = f"record of {encoded_len} bytes exceeds the {self._max_bytes}-byte bound"
        return OVERSIZE_RECORD_CODE, detail

    def status(self) -> dict[str, Any]:
        """This instance's own counters plus its write-error status — what
        :meth:`DaemonState.status` surfaces for the operational log, and what
        a caller holding a :class:`TranscriptLog` directly can read the same
        way.
        """
        return {
            "evicted_records": self.evicted_records,
            "rewrites": self.rewrites,
            "oversize_records": self.oversize_records,
            "fragments_dropped": self.fragments_dropped,
            "probe_errors": self.probe_errors,
            **_write_error_status(self.write_errors),
        }

    def read_all(self) -> list[dict[str, Any]]:
        """Every readable record, oldest first. Skips a torn last line, if any."""
        if self._path is None or not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


class OperationalLog(_BoundedJsonlLog):
    """The daemon's own bounded, rotating operational log.

    Carries structured events about the daemon's OWN operation — started,
    stopped, connected, degraded — never a transcript. Enforced structurally:
    a caller that wants to record a transcript line reaches for
    :class:`TranscriptLog` instead, a different file entirely.

    fsync policy: :data:`OPERATIONAL_LOG_FSYNC_PER_APPEND` (``False``) — see
    the module docstring's fsync policy section.
    """

    def __init__(
        self,
        path: Optional[str | Path],
        *,
        max_bytes: int,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        super().__init__(
            path,
            max_bytes=max_bytes,
            fsync=OPERATIONAL_LOG_FSYNC_PER_APPEND,
            on_degrade=on_degrade,
        )

    def write(self, event: str, **fields: Any) -> None:
        """Append one operational event. Never raises; see module docstring."""
        record: dict[str, Any] = {"ts": time.time(), "event": event}
        record.update(fields)
        self._write(record)


class TranscriptLog(_BoundedJsonlLog):
    """A private, size-bounded, per-session transcript log.

    The "session API" this module's own acceptance test writes a transcript
    through — see :meth:`DaemonState.open_transcript`. Bounded exactly like
    :class:`OperationalLog`, but always a distinct file, so a transcript can
    never end up inside the operational log no matter how either is used.

    fsync policy: :data:`TRANSCRIPT_LOG_FSYNC_PER_APPEND` (``False``) — see
    the module docstring's fsync policy section. This is the spoken-turn hot
    path the original defect (a full rewrite on every write) sat on.
    """

    def __init__(
        self,
        path: Optional[str | Path],
        *,
        max_bytes: int,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        super().__init__(
            path,
            max_bytes=max_bytes,
            fsync=TRANSCRIPT_LOG_FSYNC_PER_APPEND,
            on_degrade=on_degrade,
        )

    def write(self, role: str, text: str, **fields: Any) -> None:
        """Append one turn of spoken text. Never raises; see module docstring.

        *fields* carries per-turn facts a host wants beside the words —
        timings and record ids, for t21's acceptance criteria. They are
        merged into the record, and ``ts``/``role``/``text`` always win, so a
        caller cannot overwrite what the record IS. A value that cannot be
        JSON-encoded degrades exactly as an oversized or unencodable record
        already does: counted on ``write_errors``, never raised, and never
        ``repr``-ed into the log.

        Nothing here inspects what a field means. A caller that puts speech
        in one has written speech to the transcript log, which is what this
        file is for; a caller that puts speech in an OPERATIONAL log has made
        a mistake this module still cannot catch for them.
        """
        record: dict[str, Any] = {**fields, "ts": time.time(), "role": role, "text": text}
        self._write(record)


def _write_error_status(errors: list[str]) -> dict[str, Any]:
    """The piece of ``status()`` shared by every log/ledger surface."""
    return {
        "write_error_count": len(errors),
        "last_write_error": errors[-1] if errors else None,
    }


# ── the daemon's whole state surface ────────────────────────────────────────


class DaemonState:
    """The daemon's state directory plus its operational log and ledger.

    Construction never raises. If the preferred state directory cannot be
    created at all (an ``OSError`` — permissions, a path component that is a
    plain file, a read-only filesystem), it falls back to
    :func:`resolve_fallback_state_dir` (or, if that is itself unsafe, a
    random temp directory) and records exactly one degradation once the
    fallback directory is up, so ``status()`` shows the transition rather
    than hiding it. If even that fails — the system temp directory is itself
    missing or unwritable — construction still never raises: it enters
    no-persistence mode (:attr:`persistent` is ``False``; see the module
    docstring's "no persistence at all" section), a fully working object
    whose logs and ledger simply drop every write and count it.

    Construction also sweeps the state directory for this module's own
    orphaned atomic-rewrite temp files (a previous process killed mid-write)
    and records one degradation naming the count when it finds any — see
    :func:`_sweep_stale_temp_files`.

    :meth:`open_transcript` is IDEMPOTENT per ``session_id``: a second call
    for the same id returns the identical :class:`TranscriptLog` object the
    first call did, never a second independent instance on the same file —
    see the module docstring's "one lock per PATH, process-wide" section for
    why two instances on one path was a real defect, not just wasted memory.
    """

    def __init__(
        self,
        state_dir: Optional[str | Path] = None,
        *,
        operational_log_max_bytes: int = DEFAULT_OPERATIONAL_LOG_MAX_BYTES,
        transcript_log_max_bytes: int = DEFAULT_TRANSCRIPT_LOG_MAX_BYTES,
    ) -> None:
        self._transcript_log_max_bytes = transcript_log_max_bytes
        #: session_id -> the ONE TranscriptLog ever handed out for it by
        #: this DaemonState instance. Guarded by ``_transcripts_lock``, a
        #: separate lock from the per-path write lock (:func:`_lock_for_path`)
        #: — this one only ever guards the cache dict itself.
        self._transcripts: dict[str, TranscriptLog] = {}
        self._transcripts_lock = threading.Lock()
        preferred = resolve_state_dir(state_dir)
        self.dir, used_fallback, detail = self._ensure_dir(preferred)

        #: Only non-``None`` in no-persistence mode; explains why. Surfaced
        #: by :meth:`status` as ``persistence_detail``.
        self._persistence_detail: Optional[str] = detail if self.dir is None else None

        operational_log_path = self.dir / OPERATIONAL_LOG_FILENAME if self.dir is not None else None
        ledger_path = self.dir / LEDGER_FILENAME if self.dir is not None else None
        self.operational_log = OperationalLog(
            operational_log_path, max_bytes=operational_log_max_bytes
        )
        self.ledger = DegradationLedger(ledger_path)

        if detail is not None:
            code = STATE_DIR_FALLBACK_CODE if used_fallback else STATE_DIR_TIGHTENED_CODE
            self.ledger.append(code, detail)

        if self.dir is not None:
            swept = _sweep_stale_temp_files(self.dir) + _sweep_stale_temp_files(
                self.dir / SESSIONS_DIRNAME
            )
            if swept:
                self.ledger.append(
                    STALE_TEMP_FILES_SWEPT_CODE,
                    f"removed {swept} orphaned temp file(s) left by a previous unclean death",
                )

    @property
    def persistent(self) -> bool:
        """``False`` in no-persistence mode — see the module docstring."""
        return self.dir is not None

    @staticmethod
    def _ensure_dir(preferred: Path) -> tuple[Optional[Path], bool, Optional[str]]:
        """Create+secure *preferred*, or fall back. Never raises.

        Returns ``(dir_in_use, used_fallback, detail)``; *detail* is ``None``
        only on the fully silent happy path. ``dir_in_use`` is ``None`` only
        when no directory — preferred or fallback — could be created at all
        (the module docstring's "no persistence at all" floor).
        """
        detail = _ensure_private_dir(preferred)
        if detail is None:
            return preferred, False, None
        if not detail.startswith("could not create"):
            # It exists and is usable; only its permissions needed a note.
            return preferred, False, detail
        fallback, fallback_detail = _bootstrap_fallback_dir()
        combined = f"could not use {preferred} ({detail}); {fallback_detail}"
        return fallback, True, combined

    def open_transcript(self, session_id: str, *, max_bytes: Optional[int] = None) -> TranscriptLog:
        """The session API: a bounded, private transcript log for *session_id*.

        Lives under ``<state dir>/sessions/<safe name>.jsonl`` — always a
        different file from :attr:`operational_log`. *session_id* is treated
        as untrusted input (see :func:`_safe_session_name`): anything outside
        a conservative charset is replaced by a deterministic, hash-derived
        name and recorded as one degradation naming only the hash, never the
        rejected id. The resolved path is asserted to stay inside the
        sessions directory before use.

        IDEMPOTENT per *session_id*: a second call for the same id returns
        the SAME :class:`TranscriptLog` object as the first (cached on this
        instance), never a second independent one on the same file — see the
        module docstring's "one lock per PATH, process-wide" section for why
        that used to be a real defect. *max_bytes* is honoured only on the
        FIRST call for a given *session_id*; a later call with a different
        value is silently ignored in favor of the cached instance (session
        ids are expected to be daemon-chosen, so a caller passing a second,
        different bound is not treated as an error worth degrading over).

        In no-persistence mode (:attr:`persistent` is ``False``) this returns
        a working :class:`TranscriptLog` that drops every write and counts it
        on ``write_errors``, exactly like every other write path in that mode
        — no caller needs a ``None`` check.

        A valid session id is used **verbatim** as a filename stem, with no
        case normalisation: on a case-insensitive filesystem (the default on
        macOS and Windows, not Linux), ``"abc"`` and ``"ABC"`` resolve to the
        same transcript file. Session ids are expected to be generated by the
        daemon itself (not chosen by whoever is talking to it), so this is
        noted rather than guarded against.
        """
        with self._transcripts_lock:
            cached = self._transcripts.get(session_id)
            if cached is not None:
                return cached
            transcript = self._open_transcript_uncached(session_id, max_bytes=max_bytes)
            self._transcripts[session_id] = transcript
            return transcript

    def _open_transcript_uncached(
        self, session_id: str, *, max_bytes: Optional[int]
    ) -> TranscriptLog:
        """The actual construction :meth:`open_transcript` caches. Called
        with ``_transcripts_lock`` held, exactly once per *session_id*.
        """
        bound = self._transcript_log_max_bytes if max_bytes is None else max_bytes
        if self.dir is None:
            return TranscriptLog(None, max_bytes=bound)

        sessions_dir = self.dir / SESSIONS_DIRNAME
        dir_detail = _ensure_private_dir(sessions_dir)
        if dir_detail is not None:
            self.ledger.append(STATE_DIR_TIGHTENED_CODE, f"{SESSIONS_DIRNAME} dir: {dir_detail}")

        safe_name, rejection = _safe_session_name(session_id)
        if rejection is not None:
            self.ledger.append(SESSION_ID_REJECTED_CODE, rejection)

        resolved_sessions_dir = sessions_dir.resolve()
        path = (sessions_dir / f"{safe_name}.jsonl").resolve()
        if not path.is_relative_to(resolved_sessions_dir):
            # Structurally unreachable given _safe_session_name's charset,
            # but checked rather than trusted — see the module docstring.
            digest = hashlib.sha256(safe_name.encode("utf-8")).hexdigest()[:16]
            path = (resolved_sessions_dir / f"contained-{digest}.jsonl").resolve()
            self.ledger.append(
                SESSION_ID_REJECTED_CODE, f"resolved outside sessions dir (hash {digest})"
            )

        return TranscriptLog(path, max_bytes=bound)

    def status(self) -> dict[str, Any]:
        """A snapshot ``status`` can render: dir, log size, ledger summary.

        Every log's write-failure count and most recent write error (a
        filesystem exception's type and message, never transcript text) ride
        along, so a log that has silently stopped persisting is visible here
        rather than looking healthy. In no-persistence mode ``state_dir`` is
        ``None`` and ``persistence``/``persistence_detail`` explain why,
        rather than a state dir that looks merely empty.

        ``operational_log`` also carries ``evicted_records``, ``rewrites``
        and ``oversize_records`` — see :meth:`_BoundedJsonlLog.status` and
        the module docstring's "a bounded buffer counts what it drops"
        section; a trim that silently dropped old operational history, or an
        oversized record silently kept forever, would otherwise be invisible
        here.
        """
        if self.dir is None:
            return {
                "state_dir": None,
                "persistence": PERSISTENCE_STATUS_UNAVAILABLE,
                "persistence_detail": self._persistence_detail,
                "operational_log": {
                    "path": None,
                    "size_bytes": None,
                    "max_bytes": self.operational_log.max_bytes,
                    **self.operational_log.status(),
                },
                "ledger": {
                    "path": None,
                    "count": 0,
                    "last": None,
                    **_write_error_status(self.ledger.write_errors),
                },
            }
        try:
            log_size = self.operational_log.path.stat().st_size
        except OSError:
            log_size = None
        return {
            "state_dir": str(self.dir),
            "operational_log": {
                "path": str(self.operational_log.path),
                "size_bytes": log_size,
                "max_bytes": self.operational_log.max_bytes,
                **self.operational_log.status(),
            },
            "ledger": {
                **self.ledger.status(),
                **_write_error_status(self.ledger.write_errors),
            },
        }
