"""The drain: the daemon's single writer for the ingest spool, plus newspaper retention.

Synthetic data only. Every test uses a tmp spool and a real RoomMemory over a tmp
store dir with the real eidetic files backend, so the assertions are about what
is on disk, not about a mock. Wall time and the pass budget are both injected.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from embodiment import continuity
from embodiment import memory as mem
from embodiment.daemon import drain as dr
from embodiment.ingest import contract
from embodiment.ingest.spool import Spool

pytestmark = pytest.mark.skipif(
    not continuity.eidetic_available(), reason="eidetic is not installed"
)

DAY = 86400.0
ISSUE = "2026-09-01"
ISSUE_EPOCH = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


class Clock:
    def __init__(self, start: float = ISSUE_EPOCH) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, code: str, reason: str) -> None:
        self.calls.append((code, reason))

    def codes(self) -> list[str]:
        return [c for c, _ in self.calls]


@pytest.fixture
def spool(tmp_path: Path) -> Spool:
    return Spool.for_state_dir(tmp_path / "state")


@pytest.fixture
def room(tmp_path: Path):
    r = mem.RoomMemory(tmp_path / "store", scope="gwen", ingest_scope=mem.INGEST_SCOPE)
    yield r
    r.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def rec() -> Recorder:
    return Recorder()


def _drain(spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder, **kw: Any) -> dr.Drain:
    return dr.Drain(spool, room, record=rec, clock=clock, **kw)


def _pocket(recording: str, n: int, *, start: int = 0) -> list[dict[str, Any]]:
    lines = [
        json.dumps(
            {
                "id": f"pocket:{recording}:fact:{i}",
                "type": "fact",
                "text": f"synthetic fact {i} of recording {recording} about otters",
                "metadata": {
                    "source": "pocket",
                    "recording_id": recording,
                    "title": "synthetic title",
                    "date": "2026-08-30",
                },
            }
        )
        for i in range(start, start + n)
    ]
    batch = contract.validate_batch(lines, created="2026-09-01")
    assert isinstance(batch, contract.Batch), batch
    return [dict(r) for r in batch.records]


def _newspaper(issue: str, n: int, word: str = "turtles") -> list[dict[str, Any]]:
    lines = [
        json.dumps(
            {
                "id": f"newspaper:{issue}:item:{i}",
                "type": "item",
                "text": f"synthetic newspaper item {i} of {issue} about {word}",
                "metadata": {
                    "source": "newspaper",
                    "issue_date": issue,
                    "section": "science",
                    "url": "https://example.invalid/item",
                },
            }
        )
        for i in range(n)
    ]
    batch = contract.validate_batch(lines, created=issue)
    assert isinstance(batch, contract.Batch), batch
    return [dict(r) for r in batch.records]


def _stored(room: mem.RoomMemory) -> dict[str, dict[str, Any]]:
    rows = mem._enumerate_scope(
        data_dir=room.data_dir, scope=mem.INGEST_SCOPE, visibility="private", backend="files"
    )
    return {r["id"]: r for r in rows}


def _scope_file(room: mem.RoomMemory) -> Path:
    return room.data_dir / f"{mem.INGEST_SCOPE}__private.jsonl"


#: Fields the store itself owns and may move on any read or write.
_STORE_OWNED = ("last_recall", "recall_count")


def _normalised(room: mem.RoomMemory) -> list[dict[str, Any]]:
    rows = []
    for line in _scope_file(room).read_text("utf-8").splitlines():
        row = json.loads(line)
        for key in _STORE_OWNED:
            row.get("metadata", {}).pop(key, None)
        rows.append(row)
    return sorted(rows, key=lambda r: r["id"])


def _recall_ids(room: mem.RoomMemory, query: str) -> list[str]:
    result = room.recall(query, deadline=5.0, mode="keyword", top_k=50)
    assert result.ok, result
    return sorted(r["id"] for r in result.records)


def _lifecycle(room: mem.RoomMemory) -> dict[str, str]:
    return {i: r.get("lifecycle") for i, r in _stored(room).items()}


# ── budget ───────────────────────────────────────────────────────────────


def test_budget_is_half_the_heartbeat() -> None:
    assert dr.budget_for(0.5) == pytest.approx(0.25)
    assert dr.budget_for("nonsense") == pytest.approx(0.25)
    assert dr.budget_for(-1) == 0.0


def test_codes_are_app_ingest() -> None:
    assert len(set(dr.CODES)) == len(dr.CODES)
    assert all(code.startswith("app-ingest-") for code in dr.CODES)


# ── criterion 2: order, done/ exactly once, one record per lock hold ────


def test_batches_apply_in_enqueue_order_and_move_to_done_exactly_once(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    assert spool.enqueue("ingest", _pocket("r1", 3)).ok
    # A forget queued after the ingest must land after it: if it overtook, r1 stays active.
    assert spool.enqueue("forget", [{"source": "pocket", "source_id": "r1"}]).ok
    assert spool.enqueue("ingest", _pocket("r2", 2)).ok
    assert spool.enqueue("forget", [{"id": "pocket:r2:fact:1"}]).ok
    d = _drain(spool, room, clock, rec)

    report = d.drain_once(5.0)

    assert len(report.batches_done) == 4 and not report.batches_failed
    life = _lifecycle(room)
    assert all(life[f"pocket.r1.fact.{i}"] == "archived" for i in range(3))
    assert life["pocket.r2.fact.0"] == "active"
    assert life["pocket.r2.fact.1"] == "archived"
    s = spool.status()
    assert (s.pending, s.done, s.failed) == (0, 4, 0)
    receipts = sorted(p.name for p in spool.done_dir.iterdir())
    assert receipts == sorted(f"{b}.json" for b in report.batches_done)
    # Receipts only: no record text survives in done/.
    assert not any("otters" in p.read_text("utf-8") for p in spool.done_dir.iterdir())

    again = d.drain_once(5.0)
    assert again.batches_done == () and again.records_applied == 0
    assert spool.status().done == 4
    assert rec.calls == []


def test_each_record_is_its_own_store_job(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder, monkeypatch
) -> None:
    sizes: list[int] = []
    real = room.ingest_records

    def spy(records, **kw):
        records = list(records)
        sizes.append(len(records))
        return real(records, **kw)

    monkeypatch.setattr(room, "ingest_records", spy)
    held: list[bool] = []
    real_list = room._list_fn

    def list_spy(**kw):
        # _STORE_LOCK is an RLock held by the worker for exactly this one record.
        held.append(continuity._STORE_LOCK._is_owned())
        return real_list(**kw)

    monkeypatch.setattr(room, "_list_fn", list_spy)
    assert spool.enqueue("ingest", _pocket("r1", 5)).ok
    _drain(spool, room, clock, rec).drain_once(5.0)
    assert sizes == [1, 1, 1, 1, 1]
    assert held == [True] * 5
    assert not continuity._STORE_LOCK._is_owned()


def test_a_recall_mid_drain_meets_its_deadline(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder, monkeypatch
) -> None:
    """The drain yields the store lock between records, so a turn's recall gets in."""
    gate = threading.Event()
    real = room.ingest_records
    seen: list[float] = []

    def slow(records, **kw):
        out = real(records, **kw)
        gate.set()
        time.sleep(0.02)  # between records: the lock is free here
        return out

    monkeypatch.setattr(room, "ingest_records", slow)
    assert spool.enqueue("ingest", _pocket("r1", 10)).ok
    d = _drain(spool, room, clock, rec)
    worker = threading.Thread(target=d.drain_once, args=(5.0,))
    worker.start()
    assert gate.wait(5.0)
    t0 = time.monotonic()
    result = room.recall("otters", deadline=mem.DEFAULT_DEADLINE, mode="keyword")
    seen.append(time.monotonic() - t0)
    worker.join(5.0)
    assert result.ok and result.degradations == ()
    assert seen[0] < mem.DEFAULT_DEADLINE


# ── criterion 2: killed between records, re-run completes idempotently ──


class _Killed(BaseException):
    """Stands in for the process dying: not an Exception, so nothing catches it."""


def test_killed_between_records_rerun_completes_idempotently(
    tmp_path: Path, spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder, monkeypatch
) -> None:
    records = _pocket("r1", 6)
    # The reference: the same batch drained cleanly once into a separate store.
    ref_room = mem.RoomMemory(tmp_path / "ref", scope="gwen", ingest_scope=mem.INGEST_SCOPE)
    ref_spool = Spool(tmp_path / "ref-spool")
    assert ref_spool.enqueue("ingest", records).ok
    assert dr.Drain(ref_spool, ref_room, clock=clock).drain_once(5.0).batches_done
    reference = _normalised(ref_room)
    ref_room.close()

    assert spool.enqueue("ingest", records).ok
    real = room.ingest_records
    calls = {"n": 0}

    def dies_after_three(batch, **kw):
        calls["n"] += 1
        if calls["n"] == 4:
            raise _Killed()
        return real(batch, **kw)

    monkeypatch.setattr(room, "ingest_records", dies_after_three)
    with pytest.raises(_Killed):
        _drain(spool, room, clock, rec).drain_once(5.0)
    assert len(_stored(room)) == 3
    assert spool.status().pending == 1 and spool.status().done == 0

    monkeypatch.setattr(room, "ingest_records", real)
    restarted = _drain(spool, room, clock, rec)  # a new process: no cursor survives
    report = restarted.drain_once(5.0)
    assert len(report.batches_done) == 1
    assert report.records_applied == 6  # the whole batch re-applied, from record 0
    s = spool.status()
    assert (s.pending, s.done, s.failed) == (0, 1, 0)
    assert _normalised(room) == reference
    assert rec.calls == []


def test_a_batch_bigger_than_the_budget_carries_its_cursor(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder, monkeypatch
) -> None:
    fake = {"t": 0.0}
    real = room.ingest_records
    calls: list[str] = []

    def costly(records, **kw):
        records = list(records)
        calls.append(records[0]["id"])
        fake["t"] += 0.08  # each record costs 0.08 s of the pass budget
        return real(records, **kw)

    monkeypatch.setattr(room, "ingest_records", costly)
    assert spool.enqueue("ingest", _pocket("r1", 7)).ok
    d = _drain(spool, room, clock, rec, monotonic=lambda: fake["t"])

    passes = []
    for _ in range(10):
        report = d.drain_once(0.25)
        passes.append(report.records_applied)
        if report.batches_done:
            break
    # 0.25 s fits three starts (0.25, 0.17, 0.09 remaining; 0.01 is too little).
    assert passes == [3, 3, 1]
    assert len(calls) == 7 and len(set(calls)) == 7  # nothing re-applied across passes
    assert spool.status().done == 1
    assert rec.calls == []


def test_a_hung_store_never_holds_a_pass_past_its_budget(
    tmp_path: Path, spool: Spool, clock: Clock, rec: Recorder
) -> None:
    release = threading.Event()

    def hung_remember(record, **kw):
        release.wait(10.0)
        return continuity.remember(record, **kw)

    room = mem.RoomMemory(
        tmp_path / "store",
        scope="gwen",
        ingest_scope=mem.INGEST_SCOPE,
        remember_fn=hung_remember,
    )
    try:
        assert spool.enqueue("ingest", _pocket("r1", 3)).ok
        d = _drain(spool, room, clock, rec)
        t0 = time.monotonic()
        report = d.drain_once(0.2)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.2 + 0.1
        assert report.stalled and not report.batches_done
        assert rec.codes() == [dr.APP_INGEST_RECORD_DEFERRED]
        assert spool.status().pending == 1
        assert d.status()["stalled"] is True

        # Inside the backoff the head is not retried at all.
        clock.advance(1.0)
        assert d.drain_once(0.2).stalled
        assert rec.codes() == [dr.APP_INGEST_RECORD_DEFERRED]

        release.set()
        deadline = time.monotonic() + 5.0
        while room.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        clock.advance(dr.MAX_RETRY_BACKOFF_S)
        report = d.drain_once(5.0)
        assert len(report.batches_done) == 1
        assert len(_stored(room)) == 3
        assert d.status()["stalled"] is False
    finally:
        release.set()
        room.close()


def test_a_transient_failure_is_recorded_once_and_backs_off(
    tmp_path: Path, spool: Spool, clock: Clock, rec: Recorder
) -> None:
    broken = {"on": True}

    def flaky(record, **kw):
        if broken["on"]:
            raise OSError("synthetic disk fault")
        return continuity.remember(record, **kw)

    room = mem.RoomMemory(
        tmp_path / "store", scope="gwen", ingest_scope=mem.INGEST_SCOPE, remember_fn=flaky
    )
    try:
        assert spool.enqueue("ingest", _pocket("r1", 2)).ok
        assert spool.enqueue("ingest", _pocket("r2", 2)).ok
        d = _drain(spool, room, clock, rec)
        retries = []
        for _ in range(5):
            assert d.drain_once(1.0).stalled
            retries.append(d.status()["retry_at"] - clock())
            clock.advance(d.status()["retry_at"] - clock())
        assert rec.codes() == [dr.APP_INGEST_RECORD_FAILED]
        assert "synthetic disk fault" not in rec.calls[0][1]
        assert retries == [2.0, 4.0, 8.0, 16.0, 32.0]
        # Order holds: nothing of the second batch was written while the head stalled.
        assert _stored(room) == {}
        assert spool.status().pending == 2 and spool.status().failed == 0

        broken["on"] = False
        report = d.drain_once(5.0)
        assert len(report.batches_done) == 2
        assert d.status()["stalled"] is False
    finally:
        room.close()


def test_a_closed_memory_stalls_rather_than_failing_the_batch(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    room.close()
    assert spool.enqueue("ingest", _pocket("r1", 1)).ok
    report = _drain(spool, room, clock, rec).drain_once(1.0)
    assert report.stalled
    assert rec.codes() == [dr.APP_INGEST_RECORD_FAILED]
    assert mem.CODE_CLOSED in rec.calls[0][1]
    assert spool.status().pending == 1


# ── permanent refusals go to failed/ ─────────────────────────────────────


def test_an_invalid_record_fails_its_batch_but_the_rest_is_applied(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    records = _pocket("r1", 3)
    records[1] = dict(records[1], text="   ")
    assert spool.enqueue("ingest", records).ok
    assert spool.enqueue("forget", [{"id": "not a wire id"}]).ok
    assert spool.enqueue("forget", [{"source": "elsewhere", "source_id": "x"}]).ok
    assert spool.enqueue("ingest", _pocket("r2", 1)).ok
    report = _drain(spool, room, clock, rec).drain_once(5.0)

    assert len(report.batches_failed) == 3 and len(report.batches_done) == 1
    assert set(_stored(room)) == {"pocket.r1.fact.0", "pocket.r1.fact.2", "pocket.r2.fact.0"}
    failed = {p.name: json.loads(p.read_text("utf-8")) for p in sorted(spool.failed_dir.iterdir())}
    first = failed[f"{report.batches_failed[0]}.json"]
    assert "pocket.r1.fact.1" in first["reason"] and "refused" in first["reason"]
    assert all("otters" not in json.dumps(body) for body in failed.values())
    codes = rec.codes()
    assert codes.count(dr.APP_INGEST_RECORD_REFUSED) == 3
    assert codes.count(dr.APP_INGEST_BATCH_FAILED) == 3


def test_an_unreadable_batch_is_recorded_as_failed(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    queued = spool.enqueue("ingest", _pocket("r1", 1))
    assert queued.ok
    (spool.inbox / f"{queued.batch_id}.json").write_text("{not json", "utf-8")
    _drain(spool, room, clock, rec).drain_once(1.0)
    assert rec.codes() == [dr.APP_INGEST_BATCH_FAILED]
    assert spool.status().failed == 1


def test_a_collapsed_record_is_recorded(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    first = _pocket("r1", 1)
    twin = _pocket("r2", 1)
    twin[0]["text"] = first[0]["text"]
    assert spool.enqueue("ingest", first).ok
    assert spool.enqueue("ingest", twin).ok
    d = _drain(spool, room, clock, rec)
    report = d.drain_once(5.0)
    assert len(report.batches_done) == 2
    assert rec.codes() == [dr.APP_INGEST_COLLAPSED]
    assert d.status()["records_collapsed"] == 1


def test_a_raising_record_sink_is_counted_not_propagated(
    spool: Spool, room: mem.RoomMemory, clock: Clock
) -> None:
    def broken(code: str, reason: str) -> None:
        raise RuntimeError("sink down")

    records = _pocket("r1", 1)
    records[0]["text"] = ""
    assert spool.enqueue("ingest", records).ok
    d = dr.Drain(spool, room, record=broken, clock=clock)
    report = d.drain_once(1.0)
    assert report.batches_failed
    assert d.status()["record_errors"] >= 1


# ── criterion 3: the same batch twice ────────────────────────────────────


def test_the_same_batch_twice_leaves_the_scope_file_identical(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    records = _pocket("r1", 4)
    d = _drain(spool, room, clock, rec)
    assert spool.enqueue("ingest", records).ok
    d.drain_once(5.0)
    once = _normalised(room)
    raw_once = _scope_file(room).read_bytes()
    room.recall("otters", deadline=5.0, mode="keyword")  # moves only store-owned fields

    clock.advance(DAY)
    assert spool.enqueue("ingest", records).ok
    report = d.drain_once(5.0)
    assert len(report.batches_done) == 1
    assert _normalised(room) == once
    assert len(once) == 4
    assert raw_once  # the scope file exists and is non-empty
    assert spool.status().done == 2
    assert rec.calls == []


def test_the_same_forget_twice_is_a_no_op(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    d = _drain(spool, room, clock, rec)
    assert spool.enqueue("ingest", _pocket("r1", 2)).ok
    assert spool.enqueue("forget", [{"source": "pocket", "source_id": "r1"}]).ok
    d.drain_once(5.0)
    once = _normalised(room)
    assert spool.enqueue("forget", [{"source": "pocket", "source_id": "r1"}]).ok
    assert spool.enqueue("forget", [{"id": "pocket:r1:fact:0"}]).ok
    assert len(d.drain_once(5.0).batches_done) == 2
    assert _normalised(room) == once
    assert rec.calls == []


# ── criterion 4: newspaper retention, injected clock ─────────────────────


def test_a_newspaper_item_is_recallable_at_29_days_and_archived_by_31(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    d = _drain(spool, room, clock, rec)
    assert spool.enqueue("ingest", _newspaper(ISSUE, 3)).ok
    assert spool.enqueue("ingest", _pocket("r1", 1)).ok
    d.drain_once(5.0)
    items = [f"newspaper.{ISSUE}.item.{i}" for i in range(3)]

    clock.advance(29 * DAY)
    report = d.drain_once(5.0)
    assert report.retention_archived == ()
    assert _recall_ids(room, "turtles") == items

    clock.advance(2 * DAY)
    report = d.drain_once(5.0)
    assert report.retention_archived == (ISSUE,)
    assert _recall_ids(room, "turtles") == []
    life = _lifecycle(room)
    assert all(life[i] == "archived" for i in items)
    assert life["pocket.r1.fact.0"] == "active"  # retention touches newspaper only
    assert d.status()["retention"]["tracked_issues"] == 0
    assert rec.calls == []


def test_retention_archives_on_day_30_and_not_on_day_29(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    older = "2026-08-31"
    d = _drain(spool, room, clock, rec, retention_interval_s=0.0)
    assert spool.enqueue("ingest", _newspaper(older, 1, "herons")).ok
    assert spool.enqueue("ingest", _newspaper(ISSUE, 1, "turtles")).ok
    d.drain_once(5.0)
    clock.advance(29 * DAY)  # 2026-09-30: ISSUE is 29 days old, `older` is 30
    report = d.drain_once(5.0)
    assert report.retention_archived == (older,)
    assert _recall_ids(room, "herons") == []
    assert _recall_ids(room, "turtles") == [f"newspaper.{ISSUE}.item.0"]


def test_retention_survives_a_restart_through_its_ledger(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    assert spool.enqueue("ingest", _newspaper(ISSUE, 2)).ok
    _drain(spool, room, clock, rec).drain_once(5.0)
    ledger = json.loads((spool.root / dr.RETENTION_LEDGER_NAME).read_text("utf-8"))
    assert ledger["issue_dates"] == [ISSUE]
    assert (spool.root / dr.RETENTION_LEDGER_NAME).stat().st_mode & 0o777 == 0o600

    clock.advance(31 * DAY)
    report = _drain(spool, room, clock, rec).drain_once(5.0)  # a fresh daemon
    assert report.retention_archived == (ISSUE,)
    assert _recall_ids(room, "turtles") == []
    ledger = json.loads((spool.root / dr.RETENTION_LEDGER_NAME).read_text("utf-8"))
    assert ledger["issue_dates"] == []


def test_retention_touches_the_store_only_when_an_issue_expired(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder, monkeypatch
) -> None:
    calls: list[tuple] = []
    real = room.forget_source

    def spy(source, source_id, **kw):
        calls.append((source, source_id))
        return real(source, source_id, **kw)

    monkeypatch.setattr(room, "forget_source", spy)
    d = _drain(spool, room, clock, rec)
    assert spool.enqueue("ingest", _newspaper(ISSUE, 1)).ok
    for _ in range(28):
        d.drain_once(5.0)
        clock.advance(DAY)
    assert calls == []
    status = d.status()["retention"]
    assert status["next_check_at"] == pytest.approx(
        status["last_check_at"] + dr.RETENTION_INTERVAL_S
    )


def test_a_late_issue_already_past_retention_is_archived_at_once(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    d = _drain(spool, room, clock, rec)
    d.drain_once(5.0)  # the start-up check has run; the next is an hour away
    assert spool.enqueue("ingest", _newspaper("2026-07-01", 1)).ok
    report = d.drain_once(5.0)
    assert report.retention_archived == ("2026-07-01",)
    assert _recall_ids(room, "turtles") == []


def test_a_re_ingested_archived_issue_is_archived_again(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    d = _drain(spool, room, clock, rec)
    assert spool.enqueue("ingest", _newspaper(ISSUE, 1)).ok
    d.drain_once(5.0)
    clock.advance(31 * DAY)
    assert d.drain_once(5.0).retention_archived == (ISSUE,)
    assert spool.enqueue("ingest", _newspaper(ISSUE, 1)).ok
    # The upsert re-activates the item; the ledger marks retention due, same pass.
    assert d.drain_once(5.0).retention_archived == (ISSUE,)
    assert _recall_ids(room, "turtles") == []


def test_a_failed_retention_archive_is_recorded_and_retried(
    tmp_path: Path, spool: Spool, clock: Clock, rec: Recorder
) -> None:
    broken = {"on": False}

    def flaky_archive(record_id, **kw):
        if broken["on"]:
            raise OSError("synthetic")
        return continuity.archive(record_id, **kw)

    room = mem.RoomMemory(
        tmp_path / "store", scope="gwen", ingest_scope=mem.INGEST_SCOPE, archive_fn=flaky_archive
    )
    try:
        d = _drain(spool, room, clock, rec)
        assert spool.enqueue("ingest", _newspaper(ISSUE, 1)).ok
        d.drain_once(5.0)
        clock.advance(31 * DAY)
        broken["on"] = True
        assert d.drain_once(5.0).retention_archived == ()
        assert rec.codes() == [dr.APP_INGEST_RETENTION_FAILED]
        assert d.status()["retention"]["tracked_issues"] == 1
        broken["on"] = False
        clock.advance(dr.RETENTION_RETRY_S)
        assert d.drain_once(5.0).retention_archived == (ISSUE,)
    finally:
        room.close()


def test_an_undated_issue_is_recorded_once(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    records = _newspaper(ISSUE, 2)
    for r in records:
        r["metadata"]["issue_date"] = "Sept-first"
    assert spool.enqueue("ingest", records).ok
    assert spool.enqueue("ingest", records).ok
    d = _drain(spool, room, clock, rec)
    d.drain_once(5.0)
    assert rec.codes() == [dr.APP_INGEST_RETENTION_UNDATED]
    assert d.status()["retention"]["undated_issues"] == 1


def test_a_malformed_ledger_is_recorded(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    spool.root.mkdir(parents=True, mode=0o700)
    (spool.root / dr.RETENTION_LEDGER_NAME).write_text("[", "utf-8")
    _drain(spool, room, clock, rec).drain_once(1.0)
    assert rec.codes() == [dr.APP_INGEST_LEDGER]


def test_failed_entries_are_purged_on_the_retention_cadence(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    records = _pocket("r1", 1)
    records[0]["text"] = ""
    assert spool.enqueue("ingest", records).ok
    d = _drain(spool, room, clock, rec, retention_interval_s=0.0)
    d.drain_once(1.0)
    assert spool.status().failed == 1
    for p in spool.failed_dir.iterdir():
        old = clock() - 8 * DAY
        os.utime(p, (old, old))
    d.drain_once(1.0)
    assert spool.status().failed == 0


# ── status ───────────────────────────────────────────────────────────────


def test_status_reports_the_inbox_and_the_counters(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    d = _drain(spool, room, clock, rec)
    assert spool.enqueue("ingest", _pocket("r1", 2)).ok
    before = d.status()
    assert before["inbox"]["pending"] == 1 and before["last_pass_at"] is None
    d.drain_once(5.0)
    after = d.status()
    assert after["inbox"] == {
        "pending": 0,
        "done": 1,
        "failed": 0,
        "oldest_pending_age_s": None,
    }
    assert after["batches_done"] == 1 and after["records_written"] == 2
    assert after["stalled"] is False and after["stall_code"] is None
    assert after["retention"]["days"] == dr.RETENTION_DAYS
    json.dumps(after)  # serialisable for the daemon's status()


def test_a_concurrent_pass_returns_at_once(
    spool: Spool, room: mem.RoomMemory, clock: Clock, rec: Recorder
) -> None:
    d = _drain(spool, room, clock, rec)
    d._lock.acquire()
    try:
        assert d.drain_once(1.0) == dr.DrainReport()
    finally:
        d._lock.release()
    assert d.status()["busy_skips"] == 1


def test_a_clock_stepped_backwards_does_not_stretch_a_backoff(
    tmp_path: Path, spool: Spool, clock: Clock, rec: Recorder
) -> None:
    broken = {"on": True}

    def flaky(record, **kw):
        if broken["on"]:
            raise OSError("synthetic")
        return continuity.remember(record, **kw)

    room = mem.RoomMemory(
        tmp_path / "store", scope="gwen", ingest_scope=mem.INGEST_SCOPE, remember_fn=flaky
    )
    try:
        assert spool.enqueue("ingest", _pocket("r1", 1)).ok
        d = _drain(spool, room, clock, rec)
        assert d.drain_once(1.0).stalled
        broken["on"] = False
        clock.advance(-DAY)  # the wall clock steps back a day
        assert len(d.drain_once(5.0).batches_done) == 1
    finally:
        room.close()
