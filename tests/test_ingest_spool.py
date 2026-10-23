"""Tests for the ingest spool (synthetic records only)."""

import json
import os
import stat
import time

import pytest

from embodiment.ingest.spool import FAILED_RETENTION_S, Spool

REC = [{"id": "r1", "text": "synthetic alpha"}, {"id": "r2", "text": "synthetic beta"}]


@pytest.fixture
def spool(tmp_path):
    return Spool.for_state_dir(tmp_path)


def _snapshot(path):
    if not path.exists():
        return None
    return sorted((p.name, p.read_bytes()) for p in path.iterdir())


def test_enqueue_modes_and_sortable_name(spool):
    res = spool.enqueue("ingest", REC)
    assert res.ok and res.batch_id
    assert stat.S_IMODE(spool.inbox.stat().st_mode) == 0o700
    files = list(spool.inbox.iterdir())
    assert [f.name for f in files] == [res.batch_id + ".json"]
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert not [n for n in os.listdir(spool.inbox) if n.endswith(".tmp")]


@pytest.mark.parametrize(
    "kind,records",
    [
        ("nope", REC),
        ("ingest", []),
        ("ingest", "x"),
        ("ingest", [1]),
        ("forget", [{"a": object()}]),
    ],
)
def test_refused_batch_leaves_inbox_identical(spool, kind, records):
    assert spool.enqueue("ingest", REC).ok
    before = _snapshot(spool.inbox)
    res = spool.enqueue(kind, records)
    assert not res.ok and res.reason
    assert _snapshot(spool.inbox) == before
    assert spool.degradations


def test_refused_on_fresh_spool_creates_nothing(spool):
    assert not spool.enqueue("bad", REC).ok
    assert not spool.root.exists()


def test_batches_list_in_enqueue_order_across_kinds(spool):
    ids = [
        spool.enqueue(k, [{"id": f"x{i}"}]).batch_id
        for i, k in enumerate(["ingest", "forget", "ingest", "forget", "ingest"])
    ]
    got = spool.pending()
    assert [b.batch_id for b in got] == ids
    assert [b.kind for b in got] == ["ingest", "forget", "ingest", "forget", "ingest"]
    assert got[0].records == [{"id": "x0"}]


def test_mark_done_keeps_only_a_receipt(spool):
    spool.enqueue("ingest", REC)
    batch = spool.pending()[0]
    assert spool.mark_done(batch)
    assert spool.pending() == []
    receipts = list(spool.done_dir.iterdir())
    assert len(receipts) == 1
    raw = receipts[0].read_text("utf-8")
    assert "synthetic" not in raw
    data = json.loads(raw)
    assert data["batch_id"] == batch.batch_id
    assert data["record_ids"] == ["r1", "r2"] and data["count"] == 2
    assert len(data["hashes"]) == 2 and all(len(h) == 64 for h in data["hashes"])
    assert stat.S_IMODE(receipts[0].stat().st_mode) == 0o600


def test_mark_failed_keeps_reason_and_purges_after_seven_days(spool):
    spool.enqueue("ingest", REC)
    batch = spool.pending()[0]
    assert spool.mark_failed(batch, "store unavailable\nsecond line")
    assert spool.pending() == []
    reason = json.loads((spool.failed_dir / f"{batch.batch_id}.reason.json").read_text("utf-8"))
    assert reason["reason"] == "store unavailable second line"
    assert spool.status().failed == 1
    assert spool.purge_failed() == 0
    old = time.time() - FAILED_RETENTION_S - 10
    for p in spool.failed_dir.iterdir():
        os.utime(p, (old, old))
    assert spool.purge_failed() == 2
    assert spool.status().failed == 0


def test_malformed_batch_moves_to_failed(spool):
    spool.enqueue("ingest", REC)
    path = next(spool.inbox.iterdir())
    path.write_text("not json")
    assert spool.pending() == []
    assert spool.status().failed == 1


def test_status_without_daemon_and_empty(spool):
    st = spool.status()
    assert (st.pending, st.done, st.failed, st.oldest_pending_age_s) == (0, 0, 0, None)
    assert not spool.root.exists()


def test_status_counts_and_oldest_age(spool):
    spool.enqueue("ingest", REC)
    spool.enqueue("forget", [{"source_id": "s1"}])
    spool.enqueue("ingest", REC)
    spool.mark_done(spool.pending()[0])
    first = spool.pending()[0]
    old = time.time() - 120
    os.utime(first.path, (old, old))
    st = spool.status()
    assert (st.pending, st.done, st.failed) == (2, 1, 0)
    assert 119 <= st.oldest_pending_age_s < 180


def test_degradation_callback_is_invoked_and_never_raises(tmp_path):
    seen = []

    def boom(code, detail):
        seen.append(code)
        raise RuntimeError("sink broke")

    sp = Spool.for_state_dir(tmp_path, on_degradation=boom)
    assert not sp.enqueue("bad", REC).ok
    assert seen == ["spool-batch-refused"]
    assert ("spool-sink-failed", "RuntimeError") in sp.degradations
