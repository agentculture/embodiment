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
Only :class:`DegradationLedger` promises to survive a crash mid-write. Each
:meth:`DegradationLedger.append` call opens the file, writes one JSON line,
flushes, ``fsync``\\ s, and closes — so a process killed at any point leaves
every *prior* append intact on disk, and :meth:`DegradationLedger.status`
tolerates a torn last line (an interrupted write caught mid-flight) by
skipping it rather than failing to read the rest. The ledger is deliberately
NOT size-bounded — a background daemon's few recorded degradations are the
one thing this module must never lose to a rotation, and their record shape
is small enough that unbounded growth is not the risk bounded logs exist to
prevent. :class:`OperationalLog` and :class:`TranscriptLog` take the opposite
trade: bounded by rewriting the file with oldest lines dropped, which is not
individually crash-atomic mid-rewrite, but is bounded exactly as their
acceptance criterion requires ("the log never exceeds its configured size").

Never raise, record instead (C3)
-----------------------------------
No public method here raises for an environment problem (a missing
directory, a permission error, a full disk). Every disk operation is guarded
by a narrow ``except OSError`` (never a bare or ``Exception``-wide catch —
see ``tests/test_no_silent_degradation.py``'s AST scan, which this module
must never need an allow-list entry from) and every failure is *recorded*,
never swallowed: on the bounded logs it lands in ``write_errors`` and is
offered to an optional ``on_degrade`` callback; on :class:`DaemonState`
itself, a state-directory bootstrap failure falls back to a fresh temporary
directory and writes exactly one ledger entry naming the fallback, so a host
reading ``status()`` can see it happened.
"""

from __future__ import annotations

import json
import os
import tempfile
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
    "STATE_DIR_FALLBACK_CODE",
    "resolve_state_dir",
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

#: The degradation code :class:`DaemonState` records when it could not create
#: or use its preferred state directory and fell back to a temporary one.
STATE_DIR_FALLBACK_CODE = "state-dir-fallback"


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


# ── shared disk helpers ─────────────────────────────────────────────────────


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* via a same-directory temp file + ``os.replace``.

    Raises ``OSError`` on failure — callers of this helper are the ones that
    decide how to degrade, per this module's "never raise, record instead"
    discipline; the helper itself stays a plain, honest primitive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.remove(tmp_name)
        except OSError:
            pass  # nosec B110 # noqa: BLE001  # best-effort cleanup of our own temp file
        raise


def _bounded_rewrite_append(path: Path, line: str, max_bytes: int) -> None:
    """Append *line* to *path* as JSONL, keeping the file at or under *max_bytes*.

    Reads the current content, appends the new line, and — if the combined
    size would exceed *max_bytes* — drops whole lines from the OLDEST end
    until it fits, then rewrites the file atomically. A single line larger
    than *max_bytes* is still written alone (nothing smaller is available to
    keep the file valid JSONL), so the bound is a target the log otherwise
    never exceeds, not a hard truncation of one record.
    """
    encoded = line.encode("utf-8") + b"\n"
    existing = b""
    if path.exists():
        existing = path.read_bytes()
    combined = existing + encoded
    if len(combined) > max_bytes:
        lines = combined.split(b"\n")
        while len(lines) > 2 and sum(len(item) + 1 for item in lines) > max_bytes:
            lines.pop(0)
        combined = b"\n".join(lines)
    _atomic_write_bytes(path, combined)


def _append_only(path: Path, line: str) -> None:
    """Append *line* to *path*, ``fsync``\\ ed, so a crash after this call loses
    nothing already written. Raises ``OSError`` on failure; see module docstring.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:  # noqa: PTH123 - append mode needs open()
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


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

    Every :meth:`append` is its own open/write/``fsync``/close — there is no
    in-memory buffer that a crash could lose. :meth:`status` (and
    :meth:`read_all`, which it is built on) tolerates a torn final line, which
    is what an interrupted append looks like on disk: everything before it
    stays readable.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        #: Records this instance could not persist at all (the ledger file's
        #: own directory is unwritable). Best-effort visibility of last resort;
        #: normally empty.
        self.write_errors: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    def append(self, code: str, detail: str = "") -> Optional[DegradationRecord]:
        """Append one record. Returns it on success, ``None`` on failure.

        Never raises: a failure to persist is itself recorded, in memory, on
        :attr:`write_errors` — the last-resort degradation of a subsystem
        whose whole job is recording degradations.
        """
        record = DegradationRecord(ts=time.time(), code=code, detail=detail, id=uuid.uuid4().hex)
        try:
            _append_only(self._path, json.dumps(record.to_dict(), sort_keys=True))
        except OSError as exc:
            self.write_errors.append(f"{type(exc).__name__}: {exc}")
            return None
        return record

    def read_all(self) -> list[DegradationRecord]:
        """Every readable record, oldest first. A torn last line is skipped."""
        if not self._path.exists():
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
            "path": str(self._path),
            "count": len(records),
            "last": records[-1].to_dict() if records else None,
        }


# ── bounded rotating logs ───────────────────────────────────────────────────


class _BoundedJsonlLog:
    """Shared implementation behind :class:`OperationalLog` and :class:`TranscriptLog`.

    Not part of the public surface — both public classes exist as distinct
    types on purpose (see the module docstring's "different file" discipline)
    even though their mechanics are identical.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int,
        on_degrade: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._on_degrade = on_degrade
        self.write_errors: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True)
        try:
            _bounded_rewrite_append(self._path, line, self._max_bytes)
        except OSError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self.write_errors.append(reason)
            if self._on_degrade is not None:
                try:
                    self._on_degrade("log-write-failed", reason)
                except OSError:
                    pass  # nosec B110 # noqa: BLE001  # the degrade hook must never itself raise

    def read_all(self) -> list[dict[str, Any]]:
        """Every readable record, oldest first. Skips a torn last line, if any."""
        if not self._path.exists():
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
    """

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
    """

    def write(self, role: str, text: str) -> None:
        """Append one turn of spoken text. Never raises; see module docstring."""
        record: dict[str, Any] = {"ts": time.time(), "role": role, "text": text}
        self._write(record)


# ── the daemon's whole state surface ────────────────────────────────────────


class DaemonState:
    """The daemon's state directory plus its operational log and ledger.

    Construction never raises. If the preferred state directory cannot be
    created or used (an ``OSError`` — permissions, a path component that is a
    plain file, a read-only filesystem), it falls back to a fresh temporary
    directory and records exactly one degradation once the fallback directory
    is up, so ``status()`` shows the transition rather than hiding it.
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
        self.dir, fallback_detail = self._ensure_dir(preferred)
        self.operational_log = OperationalLog(
            self.dir / OPERATIONAL_LOG_FILENAME, max_bytes=operational_log_max_bytes
        )
        self.ledger = DegradationLedger(self.dir / LEDGER_FILENAME)
        if fallback_detail is not None:
            self.ledger.append(STATE_DIR_FALLBACK_CODE, fallback_detail)

    @staticmethod
    def _ensure_dir(preferred: Path) -> tuple[Path, Optional[str]]:
        """Create *preferred*, or a temporary fallback. Never raises.

        Returns the directory actually in use and, when a fallback was
        needed, a detail string describing why — ``None`` on the happy path.
        """
        try:
            preferred.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fallback = Path(tempfile.mkdtemp(prefix="embodiment-state-fallback-"))
            detail = f"could not use {preferred}: {type(exc).__name__}: {exc}; using {fallback}"
            return fallback, detail
        return preferred, None

    def open_transcript(self, session_id: str, *, max_bytes: Optional[int] = None) -> TranscriptLog:
        """The session API: a bounded transcript log dedicated to *session_id*.

        Lives under ``<state dir>/sessions/<session_id>.jsonl`` — always a
        different file from :attr:`operational_log`, so nothing written
        through this method can ever reach the operational log.
        """
        sessions_dir = self.dir / "sessions"
        safe_id = session_id.strip() or "unknown"
        path = sessions_dir / f"{safe_id}.jsonl"
        bound = self._transcript_log_max_bytes if max_bytes is None else max_bytes
        return TranscriptLog(path, max_bytes=bound)

    def status(self) -> dict[str, Any]:
        """A snapshot ``status`` can render: dir, log size, ledger summary."""
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
            },
            "ledger": self.ledger.status(),
        }
