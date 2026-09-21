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
:meth:`DegradationLedger.append` is additionally serialized behind a
per-instance ``threading.Lock``, so correctness holds even on a filesystem
where single-``write()`` atomicity does not (network filesystems such as NFS
are the known exception; this module has not been measured against one). The
lock also closes a race the kernel's write atomicity alone would not: two
threads independently deciding "we're under the bound, append" from a stale
size read could together push the file over it; holding the lock across the
whole decide-then-write makes that decision atomic too.

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
            f"{detail}; could not create a random fallback either: " f"{type(exc).__name__}: {exc}"
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


def _trim_to_low_water(combined: bytes, max_bytes: int, low_water_bytes: int) -> bytes:
    """Drop whole lines from the OLDEST end of *combined* until it fits at or
    under *low_water_bytes*. Only called when *combined* already exceeds
    *max_bytes*; a single line larger than *max_bytes* on its own is kept
    alone regardless — nothing smaller is available and the file must stay
    valid JSONL. See :data:`_LOW_WATER_RATIO` for why the target is the low
    water mark and not *max_bytes* itself.
    """
    lines = combined.split(b"\n")
    while len(lines) > 2 and sum(len(item) + 1 for item in lines) > low_water_bytes:
        lines.pop(0)
    return b"\n".join(lines)


def _bounded_rewrite_append(path: Path, line: str, max_bytes: int, low_water_bytes: int) -> None:
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
    """
    existing = b""
    if path.exists():
        existing = path.read_bytes()
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    encoded = line.encode("utf-8") + b"\n"
    combined = existing + encoded
    if len(combined) > max_bytes:
        combined = _trim_to_low_water(combined, max_bytes, low_water_bytes)
    _atomic_write_bytes(path, combined)


def _last_byte_is_newline_or_empty(path: Path) -> bool:
    """``True`` if *path* does not exist, is empty, or already ends in ``\\n``.

    One small read (never the whole file) used by :func:`_append_line` to
    decide whether a leading newline is needed before the new line, so a torn
    last line from an interrupted previous write is never glued to.
    """
    try:
        with open(path, "rb") as handle:  # noqa: PTH123 - a raw byte peek, not text
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return True
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) == b"\n"
    except OSError:
        return True  # no file yet — the first write has no fragment to avoid


def _append_line(path: Path, line: str, *, fsync: bool) -> None:
    """A true ``O_APPEND`` write of exactly one JSONL line. Never reads or
    rewrites existing content — the CHEAP path, used for every write that
    stays under a log's bound (and always, for the unbounded ledger). Mode
    0600 regardless of umask.

    Prefixes the write with a newline when the file's current last byte is
    not already one, so a torn last line from an interrupted previous append
    is never glued to — see :func:`_last_byte_is_newline_or_empty` and the
    module docstring's "a crash mid-append cannot glue" section.

    *fsync* decides whether this call durably syncs to disk before
    returning — see the module docstring's fsync policy section and
    :data:`LEDGER_FSYNC_PER_APPEND` / :data:`OPERATIONAL_LOG_FSYNC_PER_APPEND`
    / :data:`TRANSCRIPT_LOG_FSYNC_PER_APPEND`. Raises ``OSError`` on failure —
    callers decide how to degrade.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = b"" if _last_byte_is_newline_or_empty(path) else b"\n"
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
    rather than gluing onto the fragment. Every append is serialized behind a
    per-instance lock (see the module docstring's concurrency section).

    *path* may be ``None`` — the no-persistence floor described in the module
    docstring, used when :class:`DaemonState` could not create a state
    directory anywhere. A ``None``-path ledger is still a working object:
    every :meth:`append` is simply dropped and counted on
    :attr:`write_errors`, and reads report an always-empty ledger, rather than
    a caller needing a special case for "there is no ledger".
    """

    def __init__(self, path: Optional[str | Path]) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
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
    write is serialized behind a per-instance lock (module docstring's
    concurrency section).

    *path* may be ``None`` — the no-persistence floor (module docstring):
    every :meth:`_write` is dropped and counted on :attr:`write_errors`
    rather than the object being unusable.
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
        self._lock = threading.Lock()
        #: Failures to persist a record. Best-effort visibility of last
        #: resort; surfaced by :meth:`DaemonState.status`.
        self.write_errors: list[str] = []

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
        line = json.dumps(record, sort_keys=True)
        try:
            with self._lock:
                self._append_or_rewrite(line)
        except OSError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self.write_errors.append(reason)
            if self._on_degrade is not None:
                try:
                    self._on_degrade("log-write-failed", reason)
                except OSError:
                    pass  # narrow except; the degrade hook must never itself raise

    def _append_or_rewrite(self, line: str) -> None:
        """Called with :attr:`_lock` held. Picks the cheap or expensive path."""
        assert self._path is not None  # guarded by the caller
        try:
            current_size = self._path.stat().st_size
        except OSError:
            current_size = 0
        encoded_len = len(line.encode("utf-8")) + 1  # + the trailing newline
        if current_size + encoded_len <= self._max_bytes:
            _append_line(self._path, line, fsync=self._fsync)
            return
        low_water_bytes = int(self._max_bytes * _LOW_WATER_RATIO)
        _bounded_rewrite_append(self._path, line, self._max_bytes, low_water_bytes)

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

    def write(self, role: str, text: str) -> None:
        """Append one turn of spoken text. Never raises; see module docstring."""
        record: dict[str, Any] = {"ts": time.time(), "role": role, "text": text}
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
    """

    def __init__(
        self,
        state_dir: Optional[str | Path] = None,
        *,
        operational_log_max_bytes: int = DEFAULT_OPERATIONAL_LOG_MAX_BYTES,
        transcript_log_max_bytes: int = DEFAULT_TRANSCRIPT_LOG_MAX_BYTES,
    ) -> None:
        self._transcript_log_max_bytes = transcript_log_max_bytes
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
                    **_write_error_status(self.operational_log.write_errors),
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
                **_write_error_status(self.operational_log.write_errors),
            },
            "ledger": {
                **self.ledger.status(),
                **_write_error_status(self.ledger.write_errors),
            },
        }
