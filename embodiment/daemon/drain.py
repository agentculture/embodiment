"""The drain: the daemon's single writer for the ingest spool, plus newspaper retention.

The CLI verbs (:mod:`embodiment.cli._commands.memory`) only *enqueue*. This module
is the one consumer of ``<state dir>/ingest/inbox/``: :meth:`Drain.drain_once`
applies batches in enqueue order through :class:`~embodiment.memory.RoomMemory`
and moves each to ``done/`` (a receipt) or ``failed/`` (a safe reason) exactly
once. It is called from the daemon's heartbeat (``DaemonApp.pump``), so it is
bounded by a *budget* and never blocks past it.

Bounded by what it bounds (CLAUDE.md lesson 1)
----------------------------------------------
Two clocks, two quantities:

* the **pass budget** bounds how much of one heartbeat the drain may take.
  :func:`budget_for` derives it from ``poll_interval_s``: half the interval, so
  the rest of ``pump`` (out-features, the bus tick) keeps the other half and the
  heartbeat never slips a beat on the drain's account.
* the **record deadline** bounds one store job. It is
  ``min(memory.DEFAULT_WRITE_DEADLINE, remaining budget)``, so a hung store can
  never hold a pass past its budget: the job is deferred (RoomMemory reaps it in
  the background) and the pass stops.

One record per lock hold, and progress across passes
----------------------------------------------------
Every record is its own ``ingest_records([record])`` / forget call — one executor
job, one ``_STORE_LOCK`` hold — so a turn's recall waits behind at most one
record write, never a batch. A batch larger than one pass's budget is carried
over by an **in-memory cursor** keyed by batch id; without it a 400-item issue
would restart at record 0 every heartbeat and never finish. The cursor is
deliberately not persisted: a daemon killed mid-batch re-applies the whole batch
on restart, and that converges because every write is an upsert by id and every
forget of an archived record is reported as ``already_archived``.

Failures: permanent vs transient
--------------------------------
A record memory refuses as *invalid* (``invalid-record``) can never succeed; it
is recorded (``app-ingest-record-refused``), the rest of the batch is still
applied, and the batch ends in ``failed/`` naming the refused ids (ids only,
never text). Anything else — a deferred job, a saturated or closed layer, a
store error — is transient: the batch stays at the head of the inbox (order is
the contract: a forget must not overtake the ingest it follows), the stall is
recorded ONCE per stalled record (a ledger that fsyncs every append must not be
fed every heartbeat), and the head is retried after an exponential backoff.

Retention (operator decision q7)
--------------------------------
A newspaper issue's items are archived together 30 days after the issue date.
The drain is the single writer, so it sees every ``issue_date`` that enters the
store; it records each one in a small 0600 ledger beside the spool
(``newspaper-retention.json``: dates only, never text) BEFORE applying the batch,
so a crash can only leave an extra date, never a missing one. Retention is a pure
date check against that ledger — no store access while nothing has expired —
run at most every :data:`RETENTION_INTERVAL_S` (and on the first pass after a
start). An issue is archived when its age in UTC days reaches
:data:`RETENTION_DAYS`; the hourly cadence sits far inside the 29-to-31-day
window the spec allows. Expired issues are archived one ``forget_source`` at a
time inside the same budget; a date leaves the ledger only once its archive came
back clean.

Degrade, never raise. Every degradation goes through the injected *record*
callback (the daemon's ``_record``) under an ``app-ingest-*`` code; a callback
that raises is counted, never propagated.
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from embodiment import continuity
from embodiment import memory as mem
from embodiment.daemon.state import atomic_write_bytes
from embodiment.ingest import contract
from embodiment.ingest.spool import Batch, Spool
from embodiment.safe_reason import safe_label

# ── codes (the daemon's _record path) ────────────────────────────────────

#: A record memory refused as invalid; the batch will end in failed/.
APP_INGEST_RECORD_REFUSED = "app-ingest-record-refused"
#: A record's job did not confirm within its deadline; the batch stays queued.
APP_INGEST_RECORD_DEFERRED = "app-ingest-record-deferred"
#: A record failed transiently (store error, saturated, closed); the batch stays queued.
APP_INGEST_RECORD_FAILED = "app-ingest-record-failed"
#: A record's text matched another record's text; it was not written (or replaced).
APP_INGEST_COLLAPSED = "app-ingest-collapsed"
#: A batch was moved to failed/ (by the drain, or by the spool as unreadable).
APP_INGEST_BATCH_FAILED = "app-ingest-batch-failed"
#: Any other spool degradation (a done/ or failed/ move that did not happen, ...).
APP_INGEST_SPOOL = "app-ingest-spool"
#: An expired newspaper issue could not be archived; retried at the next check.
APP_INGEST_RETENTION_FAILED = "app-ingest-retention-failed"
#: A newspaper issue_date is not an ISO date, so retention cannot age it.
APP_INGEST_RETENTION_UNDATED = "app-ingest-retention-undated"
#: The retention ledger could not be read or written.
APP_INGEST_LEDGER = "app-ingest-ledger"

CODES = (
    APP_INGEST_RECORD_REFUSED,
    APP_INGEST_RECORD_DEFERRED,
    APP_INGEST_RECORD_FAILED,
    APP_INGEST_COLLAPSED,
    APP_INGEST_BATCH_FAILED,
    APP_INGEST_SPOOL,
    APP_INGEST_RETENTION_FAILED,
    APP_INGEST_RETENTION_UNDATED,
    APP_INGEST_LEDGER,
)

# ── clocks ───────────────────────────────────────────────────────────────

#: Share of the heartbeat a pass may take; the other half is the rest of pump().
HEARTBEAT_SHARE = 0.5
#: Below this much remaining budget a record job is not started: its deadline would
#: be shorter than a healthy files-backend write (~10 ms measured at N=3) by margin,
#: and a spuriously deferred write costs a recorded stall.
MIN_RECORD_BUDGET_S = 0.05
#: First retry delay of a stalled head batch; doubles per consecutive stall.
RETRY_BACKOFF_S = 2.0
#: Ceiling of that backoff: at most ~1440 retries (and fsynced spool records) a day.
MAX_RETRY_BACKOFF_S = 60.0

#: Operator decision q7: an issue is archived this many days after its issue date.
RETENTION_DAYS = 30
#: How often the ledger is checked. One day would already meet "archived by 31";
#: an hour leaves 23 hours of margin for downtime and a failed attempt's retry.
RETENTION_INTERVAL_S = 3600.0
#: A failed retention archive is retried this soon rather than in an hour.
RETENTION_RETRY_S = 300.0
RETENTION_LEDGER_NAME = "newspaper-retention.json"
_LEDGER_VERSION = 1
#: Refused ids named in a failed/ reason (the spool caps the reason anyway).
_REASON_IDS = 5

_SPOOL_BATCH_FAILED = "spool-batch-failed"

RecordFn = Callable[[str, str], None]


def budget_for(poll_interval_s: float) -> float:
    """The pass budget for a heartbeat of *poll_interval_s*: half of it."""
    try:
        interval = float(poll_interval_s)
    except (TypeError, ValueError):
        interval = 0.5
    return max(0.0, interval) * HEARTBEAT_SHARE


@dataclass(frozen=True)
class DrainReport:
    """What one :meth:`Drain.drain_once` did. Ids and counts only, never text."""

    batches_done: tuple[str, ...] = ()
    batches_failed: tuple[str, ...] = ()
    records_applied: int = 0
    stalled: bool = False
    budget_exhausted: bool = False
    retention_archived: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "batches_done": list(self.batches_done),
            "batches_failed": list(self.batches_failed),
            "records_applied": self.records_applied,
            "stalled": self.stalled,
            "budget_exhausted": self.budget_exhausted,
            "retention_archived": list(self.retention_archived),
        }


class _Pass:
    """One pass's budget against the monotonic clock."""

    def __init__(self, budget: float, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self._end = monotonic() + max(0.0, budget)

    def remaining(self) -> float:
        return max(0.0, self._end - self._monotonic())

    def can_start(self) -> bool:
        return self.remaining() >= MIN_RECORD_BUDGET_S


class Drain:
    """The daemon-side single writer. Construct once; call :meth:`drain_once` per tick.

    *clock* is wall time (epoch seconds) for retention dates, backoff and status;
    *monotonic* bounds the pass budget. Both are injectable for tests.
    """

    def __init__(
        self,
        spool: Spool,
        memory: mem.RoomMemory,
        *,
        record: Optional[RecordFn] = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        record_deadline: float = mem.DEFAULT_WRITE_DEADLINE,
        retention_interval_s: float = RETENTION_INTERVAL_S,
    ) -> None:
        self._spool = spool
        self._memory = memory
        self._record_fn = record
        self._clock = clock
        self._monotonic = monotonic
        self._record_deadline = max(0.0, float(record_deadline))
        self._retention_interval = max(0.0, float(retention_interval_s))
        self._lock = threading.Lock()  # one pass at a time (pump and close may both call)

        self._cursor: dict[str, int] = {}
        self._refused: dict[str, list[str]] = {}
        self._spool_seen = 0
        self._stall_key: Optional[tuple[str, int]] = None
        self._stall_code: Optional[str] = None
        self._stall_count = 0
        self._retry_at = 0.0

        self._ledger: Optional[set[str]] = None  # loaded lazily on the first pass
        self._undated: set[str] = set()
        self._retention_due_at = 0.0  # the first pass after a start checks

        self._counts = {
            "batches_done": 0,
            "batches_failed": 0,
            "records_written": 0,
            "records_collapsed": 0,
            "records_refused": 0,
            "forgets_archived": 0,
            "stalls": 0,
            "retention_archived_issues": 0,
            "record_errors": 0,
            "busy_skips": 0,
        }
        self._last_pass_at: Optional[float] = None
        self._last_retention_at: Optional[float] = None

    # ── recording ────────────────────────────────────────────────────────

    def _record(self, code: str, reason: str) -> None:
        if self._record_fn is None:
            return
        try:
            self._record_fn(code, reason)
        except Exception:  # noqa: BLE001 - a broken sink must not break the drain
            self._counts["record_errors"] += 1

    def _forward_spool(self) -> None:
        """Every new spool degradation, under an app-ingest-* code."""
        entries = self._spool.degradations
        for code, detail in entries[self._spool_seen :]:
            if code == _SPOOL_BATCH_FAILED:
                self._record(APP_INGEST_BATCH_FAILED, str(detail))
            else:
                self._record(APP_INGEST_SPOOL, f"{code}: {detail}")
        self._spool_seen = len(entries)

    # ── the pass ─────────────────────────────────────────────────────────

    def drain_once(self, budget: float) -> DrainReport:
        """Apply what fits in *budget* seconds. Never raises, never blocks past it.

        A pass that finds another pass running returns at once (counted).
        """
        if not self._lock.acquire(blocking=False):
            self._counts["busy_skips"] += 1
            return DrainReport()
        try:
            return self._pass(_Pass(budget, self._monotonic))
        except Exception as exc:  # noqa: BLE001 - degrade, never raise (C3)
            self._record(APP_INGEST_RECORD_FAILED, f"drain pass failed: {type(exc).__name__}")
            return DrainReport(stalled=True)
        finally:
            self._lock.release()

    def _pass(self, budget: _Pass) -> DrainReport:
        now = self._clock()
        self._last_pass_at = now
        self._load_ledger()
        done: list[str] = []
        failed: list[str] = []
        applied = 0
        stalled = False
        exhausted = False
        # A wall clock stepped backwards (NTP at boot) must not stretch a backoff:
        # a retry further away than the longest backoff is due now.
        if 0.0 < self._retry_at - now <= MAX_RETRY_BACKOFF_S:
            stalled = True
        else:
            batches = self._spool.pending()
            self._forward_spool()
            live = {b.batch_id for b in batches}
            for stale in [k for k in self._cursor if k not in live]:
                self._cursor.pop(stale, None)
                self._refused.pop(stale, None)
            for batch in batches:
                outcome, count = self._apply(batch, budget)
                applied += count
                if outcome == "done":
                    done.append(batch.batch_id)
                elif outcome == "failed":
                    failed.append(batch.batch_id)
                elif outcome == "stalled":
                    stalled = True
                    break
                else:  # "budget"
                    exhausted = True
                    break
        archived: tuple[str, ...] = ()
        if not stalled and not exhausted:
            archived, exhausted = self._retention(budget)
        return DrainReport(
            batches_done=tuple(done),
            batches_failed=tuple(failed),
            records_applied=applied,
            stalled=stalled,
            budget_exhausted=exhausted,
            retention_archived=archived,
        )

    def _apply(self, batch: Batch, budget: _Pass) -> tuple[str, int]:
        """``(outcome, records applied)``; outcome is done/failed/stalled/budget."""
        records = batch.records
        start = self._cursor.get(batch.batch_id, 0)
        if start == 0 and batch.kind == "ingest":
            self._track_issue_dates(records)
        refused = self._refused.setdefault(batch.batch_id, [])
        applied = 0
        for index in range(start, len(records)):
            if not budget.can_start():
                self._cursor[batch.batch_id] = index
                return "budget", applied
            deadline = min(self._record_deadline, budget.remaining())
            verdict, label, code = self._apply_one(batch.kind, records[index], deadline)
            if verdict == "transient":
                self._cursor[batch.batch_id] = index
                self._stall(batch.batch_id, index, label, code)
                return "stalled", applied
            if verdict == "refused":
                refused.append(label)
                self._counts["records_refused"] += 1
                self._record(
                    APP_INGEST_RECORD_REFUSED,
                    f"batch {batch.batch_id}: record {label} refused ({code})",
                )
            else:
                applied += 1
            self._cursor[batch.batch_id] = index + 1
            self._clear_stall()
        return self._finish(batch, refused), applied

    def _finish(self, batch: Batch, refused: list[str]) -> str:
        if refused:
            shown = ", ".join(refused[:_REASON_IDS])
            more = f" (+{len(refused) - _REASON_IDS} more)" if len(refused) > _REASON_IDS else ""
            ok = self._spool.mark_failed(
                batch, f"{len(refused)} record(s) refused as invalid: {shown}{more}"
            )
            outcome = "failed"
        else:
            ok = self._spool.mark_done(batch)
            outcome = "done"
        self._forward_spool()
        if not ok:
            # The move did not happen (recorded by the spool): back off and retry it;
            # the cursor sits at the end, so nothing is re-applied first.
            self._stall(batch.batch_id, len(batch.records), "(receipt)", "spool-move-failed")
            return "stalled"
        self._cursor.pop(batch.batch_id, None)
        self._refused.pop(batch.batch_id, None)
        self._counts["batches_done" if outcome == "done" else "batches_failed"] += 1
        self._clear_stall()
        return outcome

    # ── one record ───────────────────────────────────────────────────────

    def _apply_one(self, kind: str, raw: Any, deadline: float) -> tuple[str, str, str]:
        """``(verdict, id label, code)``; verdict is ok, refused or transient."""
        if kind == "forget":
            return self._forget_one(raw, deadline)
        result = self._memory.ingest_records([raw], deadline=deadline, now=self._now_dt())
        label = _label(raw.get("id") if isinstance(raw, Mapping) else None)
        for degradation in result.degradations:
            if degradation.code == mem.CODE_INGEST_COLLAPSED:
                self._record(APP_INGEST_COLLAPSED, degradation.reason)
        if result.ok:
            self._counts["records_written"] += len(result.written)
            self._counts["records_collapsed"] += sum(
                1 for rid, _ in result.collapsed if rid not in result.written
            )
            return "ok", label, ""
        code = result.failed[0][1] if result.failed else mem.CODE_INGEST_NOT_ATTEMPTED
        if code == continuity.CODE_INVALID_RECORD:
            return "refused", label, code
        return "transient", label, code

    def _forget_one(self, raw: Any, deadline: float) -> tuple[str, str, str]:
        if not isinstance(raw, Mapping):
            return "refused", "(not a record)", continuity.CODE_INVALID_RECORD
        if "id" in raw:
            stored = contract.stored_id(raw.get("id"))
            if stored is None:
                return "refused", "(unusable id)", continuity.CODE_INVALID_RECORD
            label = stored
            result = self._memory.forget_ids([stored], deadline=deadline)
        else:
            source, source_id = raw.get("source"), raw.get("source_id")
            if (
                contract.source_refusal(source) is not None
                or not isinstance(source_id, str)
                or not source_id.strip()
            ):
                return "refused", "(unusable source)", continuity.CODE_INVALID_RECORD
            label = f"{safe_label(source)}/{_label(source_id)}"
            result = self._memory.forget_source(source, source_id, deadline=deadline)
        if result.ok:
            self._counts["forgets_archived"] += len(result.archived)
            return "ok", label, ""
        code = result.failed[0][1] if result.failed else _first_code(result.degradations)
        if code == continuity.CODE_INVALID_RECORD:
            return "refused", label, code
        return "transient", label, code

    # ── stalls ───────────────────────────────────────────────────────────

    def _stall(self, batch_id: str, index: int, label: str, code: str) -> None:
        key = (batch_id, index)
        self._stall_count = self._stall_count + 1 if key == self._stall_key else 1
        backoff = min(MAX_RETRY_BACKOFF_S, RETRY_BACKOFF_S * (2 ** (self._stall_count - 1)))
        self._retry_at = self._clock() + backoff
        if key != self._stall_key or code != self._stall_code:
            self._counts["stalls"] += 1
            deferred = code == mem.CODE_REMEMBER_DEFERRED
            self._record(
                APP_INGEST_RECORD_DEFERRED if deferred else APP_INGEST_RECORD_FAILED,
                f"batch {batch_id}: record {label} not applied ({code}); "
                f"the batch stays queued and is retried in {backoff:.0f}s",
            )
        self._stall_key, self._stall_code = key, code

    def _clear_stall(self) -> None:
        self._stall_key = None
        self._stall_code = None
        self._stall_count = 0
        self._retry_at = 0.0

    # ── retention ────────────────────────────────────────────────────────

    @property
    def ledger_path(self) -> Path:
        return self._spool.root / RETENTION_LEDGER_NAME

    def _load_ledger(self) -> None:
        if self._ledger is not None:
            return
        self._ledger = set()
        try:
            raw = self.ledger_path.read_text("utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            self._record(APP_INGEST_LEDGER, f"retention ledger unreadable: {type(exc).__name__}")
            return
        try:
            data = json.loads(raw)
            dates = data["issue_dates"]
            if not isinstance(dates, list):
                raise TypeError("issue_dates")
        except (ValueError, KeyError, TypeError) as exc:
            self._record(APP_INGEST_LEDGER, f"retention ledger malformed: {type(exc).__name__}")
            return
        self._ledger = {d for d in dates if isinstance(d, str) and _parse_date(d) is not None}

    def _save_ledger(self) -> None:
        body = {"version": _LEDGER_VERSION, "issue_dates": sorted(self._ledger or ())}
        try:
            self._spool.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write_bytes(self.ledger_path, json.dumps(body, sort_keys=True).encode("utf-8"))
        except OSError as exc:
            self._record(APP_INGEST_LEDGER, f"retention ledger not written: {type(exc).__name__}")

    def _track_issue_dates(self, records: list[Any]) -> None:
        """Note every newspaper issue_date BEFORE the batch is applied."""
        if self._ledger is None:
            self._ledger = set()
        added = False
        for raw in records:
            meta = raw.get("metadata") if isinstance(raw, Mapping) else None
            if not isinstance(meta, Mapping) or meta.get("source") != "newspaper":
                continue
            issue = meta.get("issue_date")
            if not isinstance(issue, str) or _parse_date(issue) is None:
                shown = _label(issue)
                if shown not in self._undated:
                    self._undated.add(shown)
                    self._record(
                        APP_INGEST_RETENTION_UNDATED,
                        f"newspaper issue_date {shown} is not YYYY-MM-DD; "
                        "its items will not be archived by retention",
                    )
                continue
            if issue not in self._ledger:
                self._ledger.add(issue)
                added = True
                if self._expired(issue):
                    self._retention_due_at = 0.0  # already past retention: check now
        if added:
            self._save_ledger()

    def _expired(self, issue: str) -> bool:
        day = _parse_date(issue)
        if day is None:
            return False
        today = _dt.datetime.fromtimestamp(self._clock(), _dt.timezone.utc).date()
        return (today - day).days >= RETENTION_DAYS

    def _retention(self, budget: _Pass) -> tuple[tuple[str, ...], bool]:
        """``(issues archived, budget exhausted)``. Pure date math unless one expired."""
        now = self._clock()
        # The same guard against a clock stepped backwards as the retry backoff.
        if 0.0 < self._retention_due_at - now <= max(self._retention_interval, RETENTION_RETRY_S):
            return (), False
        expired = sorted(d for d in (self._ledger or ()) if self._expired(d))
        archived: list[str] = []
        for issue in expired:
            if not budget.can_start():
                return tuple(archived), True  # still due: the next pass continues
            deadline = min(self._record_deadline, budget.remaining())
            result = self._memory.forget_source("newspaper", issue, deadline=deadline)
            if not result.ok:
                code = result.failed[0][1] if result.failed else _first_code(result.degradations)
                self._record(
                    APP_INGEST_RETENTION_FAILED,
                    f"newspaper issue {issue} not archived ({code}); "
                    f"retried in {RETENTION_RETRY_S:.0f}s",
                )
                self._retention_due_at = now + RETENTION_RETRY_S
                if archived:
                    self._save_ledger()
                return tuple(archived), False
            if self._ledger is not None:
                self._ledger.discard(issue)
            archived.append(issue)
            self._counts["forgets_archived"] += len(result.archived)
            self._counts["retention_archived_issues"] += 1
        if archived:
            self._save_ledger()
        self._spool.purge_failed(now)
        self._forward_spool()
        self._last_retention_at = now
        self._retention_due_at = now + self._retention_interval
        return tuple(archived), False

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Spool counts plus what the drain has done. Ids and counts only. Never raises."""
        now = self._clock()
        try:
            spool = self._spool.status(now)
            inbox: dict[str, Any] = {
                "pending": spool.pending,
                "done": spool.done,
                "failed": spool.failed,
                "oldest_pending_age_s": spool.oldest_pending_age_s,
            }
        except Exception as exc:  # noqa: BLE001 - status is a report, never a failure
            inbox = {"error": type(exc).__name__}
        return {
            "inbox": inbox,
            **dict(self._counts),
            "stalled": self._stall_key is not None,
            "stall_batch": self._stall_key[0] if self._stall_key else None,
            "stall_code": self._stall_code,
            "retry_at": self._retry_at or None,
            "last_pass_at": self._last_pass_at,
            "retention": {
                "days": RETENTION_DAYS,
                "tracked_issues": len(self._ledger or ()),
                "undated_issues": len(self._undated),
                "next_check_at": self._retention_due_at or None,
                "last_check_at": self._last_retention_at,
            },
        }

    def _now_dt(self) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(self._clock(), _dt.timezone.utc)


def _parse_date(value: Any) -> Optional[_dt.date]:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        return None


def _label(value: Any) -> str:
    """An id as a safe label; anything else as a placeholder. Never text."""
    if isinstance(value, str) and value and len(value) <= mem.HEADER_FIELD_LIMIT:
        if all(c in mem.HEADER_LABEL_CHARSET for c in value):
            return value
    return "(unusable id)"


def _first_code(degradations: Any) -> str:
    for degradation in degradations or ():
        code = getattr(degradation, "code", None)
        if isinstance(code, str):
            return code
    return continuity.CODE_SUBSYSTEM_ERROR
