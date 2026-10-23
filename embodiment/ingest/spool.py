"""The ingest spool: a crash-safe, ordered queue of memory batches on disk.

Layout under ``<state dir>/ingest/``::

    inbox/    pending batches, one 0600 file each, sortable by name (0700 dir)
    done/     receipts only: batch id, record ids, counts, hashes - never text
    failed/   a safe reason plus the receipt fields, no records; purged after 7 days

Batches are opaque lists of already-validated record dicts plus a kind
(``ingest`` or ``forget``). Forget requests share the one queue, so ordering
holds across both kinds. The spool authenticates nothing beyond filesystem
permissions: the inbox is 0700 and the same account already owns the store.

Degrade, never raise: every public method returns a result and records a
degradation (``degradations`` and the optional ``on_degradation`` callback)
instead of raising. ``status()`` only reads, so it works with no daemon.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from embodiment.daemon.state import atomic_write_bytes, ensure_private_dir, resolve_state_dir

KINDS = ("ingest", "forget")
SPOOL_DIRNAME = "ingest"
INBOX_DIRNAME = "inbox"
DONE_DIRNAME = "done"
FAILED_DIRNAME = "failed"
#: Every batch file, in any of the three directories, ends in this.
BATCH_SUFFIX = ".json"

# failed/ entries older than this are purged (the spool keeps no second copy of memory text).
FAILED_RETENTION_S = 7 * 24 * 3600
_REASON_MAX_CHARS = 200

_BATCH_NAME_RE = re.compile(r"^(\d{20})-(\d{6})-(ingest|forget)-([0-9a-f]{8})\.json$", re.ASCII)
_ID_KEYS = ("id", "source_id")

_SEQ = itertools.count()
_SEQ_LOCK = threading.Lock()


@dataclass(frozen=True)
class EnqueueResult:
    ok: bool
    batch_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class Batch:
    batch_id: str
    kind: str
    records: list[dict[str, Any]]
    path: Path


@dataclass(frozen=True)
class SpoolStatus:
    pending: int
    done: int
    failed: int
    oldest_pending_age_s: Optional[float]


@dataclass
class Spool:
    root: Path
    on_degradation: Optional[Callable[[str, str], None]] = None
    degradations: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def for_state_dir(cls, override: Optional[str | Path] = None, **kwargs: Any) -> "Spool":
        return cls(resolve_state_dir(override) / SPOOL_DIRNAME, **kwargs)

    @property
    def inbox(self) -> Path:
        return self.root / INBOX_DIRNAME

    @property
    def done_dir(self) -> Path:
        return self.root / DONE_DIRNAME

    @property
    def failed_dir(self) -> Path:
        return self.root / FAILED_DIRNAME

    def _degrade(self, code: str, detail: str) -> None:
        self.degradations.append((code, detail))
        if self.on_degradation is not None:
            try:
                self.on_degradation(code, detail)
            except Exception as exc:  # noqa: BLE001 - a broken sink must not break the spool
                self.degradations.append(("spool-sink-failed", type(exc).__name__))

    def _ensure(self, *dirs: Path) -> bool:
        ok = True
        for d in (self.root, *dirs):
            detail = ensure_private_dir(d)
            if detail is not None:
                self._degrade("spool-dir", detail)
                if not d.is_dir():
                    ok = False
        return ok

    # ── enqueue ──────────────────────────────────────────────────────────

    def enqueue(self, kind: str, records: Any) -> EnqueueResult:
        """Queue one batch atomically. A refused batch touches nothing on disk."""
        refusal = _refusal(kind, records)
        if refusal is not None:
            self._degrade("spool-batch-refused", refusal)
            return EnqueueResult(False, reason=refusal)
        try:
            payload = json.dumps(
                {"kind": kind, "records": records}, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            reason = f"batch is not serialisable: {type(exc).__name__}"
            self._degrade("spool-batch-refused", reason)
            return EnqueueResult(False, reason=reason)
        if not self._ensure(self.inbox):
            return EnqueueResult(False, reason="inbox unavailable")
        with _SEQ_LOCK:
            seq = next(_SEQ) % 1_000_000
        name = f"{time.time_ns():020d}-{seq:06d}-{kind}-{os.urandom(4).hex()}{BATCH_SUFFIX}"
        try:
            atomic_write_bytes(self.inbox / name, payload)
        except OSError as exc:
            reason = f"could not write batch: {type(exc).__name__}"
            self._degrade("spool-enqueue-failed", reason)
            return EnqueueResult(False, reason=reason)
        return EnqueueResult(True, batch_id=name[: -len(BATCH_SUFFIX)])

    # ── consume ──────────────────────────────────────────────────────────

    def pending_names(self) -> list[str]:
        """Batch file names in enqueue order. Lists the inbox; opens nothing."""
        try:
            names = os.listdir(self.inbox)
        except OSError:
            return []
        return sorted(n for n in names if _BATCH_NAME_RE.match(n))

    _pending_names = pending_names

    def load(self, name: str) -> Optional[Batch]:
        """Read one named batch. Unreadable or malformed: moved to failed/, ``None``."""
        path = self.inbox / name
        batch_id = name[: -len(BATCH_SUFFIX)]
        try:
            data = json.loads(path.read_text("utf-8"))
            kind, records = data["kind"], data["records"]
            if _refusal(kind, records) is not None:
                raise ValueError("shape")
        except (OSError, ValueError, KeyError, TypeError):
            self._move_failed(path, batch_id, "batch file unreadable or malformed")
            return None
        return Batch(batch_id, kind, records, path)

    def pending(self) -> list[Batch]:
        """Batches in enqueue order. An unreadable batch is moved to failed/."""
        loaded = (self.load(name) for name in self.pending_names())
        return [batch for batch in loaded if batch is not None]

    def mark_done(self, batch: Batch) -> bool:
        """Replace the batch with a receipt (ids, counts, hashes) - never record text."""
        receipt = {
            "batch_id": batch.batch_id,
            **_receipt_fields(batch),
            "completed_at": time.time(),
        }
        if not self._ensure(self.done_dir):
            return False
        try:
            data = json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
            atomic_write_bytes(self.done_dir / f"{batch.batch_id}{BATCH_SUFFIX}", data)
            os.remove(batch.path)
        except OSError as exc:
            self._degrade("spool-mark-done-failed", type(exc).__name__)
            return False
        return True

    def mark_failed(self, batch: Batch, reason: str) -> bool:
        """Keep only the safe reason plus the receipt fields; the records are dropped."""
        return self._move_failed(batch.path, batch.batch_id, reason, batch)

    def _move_failed(
        self, path: Path, batch_id: str, reason: str, batch: Optional[Batch] = None
    ) -> bool:
        safe = "".join(c if c.isprintable() else " " for c in str(reason))[:_REASON_MAX_CHARS]
        if not self._ensure(self.failed_dir):
            return False
        body: dict[str, Any] = {"batch_id": batch_id, "reason": safe, "failed_at": time.time()}
        if batch is not None:
            body.update(_receipt_fields(batch))
        else:
            m = _BATCH_NAME_RE.match(path.name)
            body["kind"] = m.group(3) if m else None
        try:
            atomic_write_bytes(
                self.failed_dir / f"{batch_id}{BATCH_SUFFIX}",
                json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            )
            os.remove(path)
        except OSError as exc:
            self._degrade("spool-mark-failed-failed", type(exc).__name__)
            return False
        self._degrade("spool-batch-failed", f"{batch_id}: {safe}")
        return True

    def purge_failed(self, now: Optional[float] = None) -> int:
        """Delete failed/ entries older than seven days. Returns files removed."""
        now = time.time() if now is None else now
        removed = 0
        try:
            names = os.listdir(self.failed_dir)
        except OSError:
            return 0
        for name in names:
            path = self.failed_dir / name
            try:
                if now - path.stat().st_mtime > FAILED_RETENTION_S:
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                self._degrade("spool-purge-failed", type(exc).__name__)
        return removed

    # ── status ───────────────────────────────────────────────────────────

    def status(self, now: Optional[float] = None) -> SpoolStatus:
        """Counts and the oldest pending age. Read-only; needs no daemon."""
        now = time.time() if now is None else now
        names = self._pending_names()
        oldest: Optional[float] = None
        for name in names:
            try:
                age = now - (self.inbox / name).stat().st_mtime
            except OSError:
                continue
            oldest = age if oldest is None else max(oldest, age)
        return SpoolStatus(
            pending=len(names),
            done=_count(self.done_dir, lambda n: n.endswith(BATCH_SUFFIX)),
            failed=_count(self.failed_dir, lambda n: n.endswith(BATCH_SUFFIX)),
            oldest_pending_age_s=None if oldest is None else max(0.0, oldest),
        )


def _receipt_fields(batch: Batch) -> dict[str, Any]:
    return {
        "kind": batch.kind,
        "record_ids": [i for i in (_record_id(r) for r in batch.records) if i is not None],
        "count": len(batch.records),
        "hashes": [_record_hash(r) for r in batch.records],
    }


def _count(directory: Path, keep: Callable[[str], bool]) -> int:
    try:
        return sum(1 for n in os.listdir(directory) if not n.startswith(".") and keep(n))
    except OSError:
        return 0


def _refusal(kind: Any, records: Any) -> Optional[str]:
    if kind not in KINDS:
        return "unknown batch kind"
    if not isinstance(records, list) or not records:
        return "batch must be a non-empty list of records"
    if not all(isinstance(r, dict) for r in records):
        return "every record must be an object"
    return None


def _record_id(record: dict[str, Any]) -> Optional[str]:
    for key in _ID_KEYS:
        value = record.get(key)
        if isinstance(value, str):
            return value
    return None


def _record_hash(record: dict[str, Any]) -> str:
    blob = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
