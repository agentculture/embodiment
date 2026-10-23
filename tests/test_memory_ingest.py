"""RoomMemory's ingest write side: two scopes, supersede flip, forget by source.

Synthetic data only. Every test uses a tmp store dir and the real eidetic
backend, so the assertions are about what is on disk, not about a mock.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from embodiment import continuity
from embodiment import memory as mem
from embodiment.ingest import contract

pytestmark = pytest.mark.skipif(
    not continuity.eidetic_available(), reason="eidetic is not installed"
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def room(tmp_path: Path):
    r = mem.RoomMemory(tmp_path / "store", scope="gwen", ingest_scope=mem.INGEST_SCOPE)
    yield r
    r.close()


def _batch(*specs: tuple) -> list[dict[str, Any]]:
    """Validated pocket records from ``(rec, kind, n, text[, supersedes_wire])`` tuples."""
    lines = []
    for spec in specs:
        rec, kind, n, text = spec[:4]
        line: dict[str, Any] = {
            "id": f"pocket:{rec}:{kind}:{n}",
            "type": "fact",
            "text": text,
            "metadata": {
                "source": "pocket",
                "recording_id": rec,
                "title": "t",
                "date": "2026-10-01",
            },
        }
        if len(spec) > 4:
            line["supersedes"] = spec[4]
        lines.append(json.dumps(line))
    batch = contract.validate_batch(lines, created="2026-10-22")
    assert isinstance(batch, contract.Batch), batch
    return list(batch.records)


def _stored(room: mem.RoomMemory, scope: str = mem.INGEST_SCOPE) -> dict[str, dict[str, Any]]:
    rows = mem._enumerate_scope(
        data_dir=room.data_dir, scope=scope, visibility="private", backend=room._backend
    )
    return {r["id"]: r for r in rows}


def _recall_ids(room: mem.RoomMemory, query: str) -> list[str]:
    result = room.recall(query, deadline=5.0, mode="keyword", top_k=50)
    return [r["id"] for r in result.records]


def test_constants_match_the_contract() -> None:
    assert mem.INGEST_SCOPE == contract.INGEST_SCOPE
    assert mem.INGEST_VISIBILITY == contract.INGEST_VISIBILITY
    for source, key in mem._SOURCE_ID_KEYS.items():
        assert key in contract.REGISTRY[source].required


# -- criterion 2: recall reads exactly two scopes --------------------------------------


def test_recall_reads_the_room_scope_and_the_ingest_scope_only(room, tmp_path) -> None:
    room.remember("zebrafish note from the room", record_id="gwen-1")
    assert room.ingest_records(_batch(("r1", "fact", 1, "zebrafish fact from a recording"))).ok
    other = mem.RoomMemory(tmp_path / "store", scope="some-other-scope")
    other.remember("zebrafish note from a stranger", record_id="other-1")
    other.close()
    ids = _recall_ids(room, "zebrafish")
    assert sorted(ids) == ["gwen-1", "pocket.r1.fact.1"]


def test_a_room_without_an_ingest_scope_does_not_read_it(tmp_path) -> None:
    writer = mem.RoomMemory(tmp_path / "store", scope="gwen", ingest_scope=mem.INGEST_SCOPE)
    assert writer.ingest_records(_batch(("r1", "fact", 1, "okapi fact"))).ok
    writer.close()
    plain = mem.RoomMemory(tmp_path / "store")
    assert _recall_ids(plain, "okapi") == []
    plain.close()


def test_both_scopes_are_read_inside_one_deadline(tmp_path) -> None:
    calls: list[str] = []
    gate = threading.Event()

    def slow_recall(query, **kw):
        calls.append(kw["scope"])
        gate.wait(timeout=5)
        return continuity.RecallOutcome(ok=True, records=[], degradation=None)

    r = mem.RoomMemory(tmp_path / "s", recall_fn=slow_recall, ingest_scope=mem.INGEST_SCOPE)
    result = r.recall("x", deadline=0.05, mode="keyword")
    assert not result.ok
    assert result.degradations[0].code == mem.CODE_DEADLINE_EXCEEDED
    gate.set()
    r.close()


def test_recall_asks_exactly_the_two_scopes(tmp_path) -> None:
    seen: list[tuple[str, str]] = []

    def spy(query, **kw):
        seen.append((kw["scope"], kw["visibility"]))
        return continuity.RecallOutcome(ok=True, records=[], degradation=None)

    r = mem.RoomMemory(tmp_path / "s", scope="gwen", recall_fn=spy, ingest_scope=mem.INGEST_SCOPE)
    r.recall("x", deadline=5.0, mode="keyword")
    r.close()
    assert seen == [("gwen", "private"), (mem.INGEST_SCOPE, "private")]


def test_a_failing_ingest_read_costs_only_the_ingest_hits(tmp_path) -> None:
    def fn(query, **kw):
        if kw["scope"] == mem.INGEST_SCOPE:
            raise RuntimeError("boom")
        return continuity.RecallOutcome(
            ok=True, records=[{"id": "g", "text": "t"}], degradation=None
        )

    r = mem.RoomMemory(tmp_path / "s", recall_fn=fn, ingest_scope=mem.INGEST_SCOPE)
    result = r.recall("x", deadline=5.0, mode="keyword")
    r.close()
    assert [x["id"] for x in result.records] == ["g"]
    assert any(d.stage == "recall" for d in result.degradations)


# -- criterion 3: writes and the supersede flip -----------------------------------------


def test_ingest_writes_private_into_the_ingest_scope(room, tmp_path) -> None:
    result = room.ingest_records(_batch(("r1", "fact", 1, "narwhal fact")))
    assert result.ok and result.written == ("pocket.r1.fact.1",)
    rows = _stored(room)
    row = rows["pocket.r1.fact.1"]
    assert row["scope"] == {"name": mem.INGEST_SCOPE, "visibility": "private"}
    assert row["metadata"]["wire_id"] == "pocket:r1:fact:1"
    assert not (tmp_path / "store" / "gwen__private.jsonl").exists()


def test_scope_is_not_taken_from_the_record(room) -> None:
    rec = _batch(("r1", "fact", 1, "pangolin fact"))[0]
    rec["scope"] = "gwen"
    rec["visibility"] = "public"
    assert room.ingest_records([rec]).ok
    assert "pocket.r1.fact.1" in _stored(room)
    assert _stored(room, "gwen") == {}


def test_one_record_is_one_executor_job(tmp_path) -> None:
    holds: list[int] = []
    r = mem.RoomMemory(tmp_path / "s")
    original = r._submit

    def counting(work, *, record_id=None):
        holds.append(1)
        return original(work, record_id=record_id)

    r._submit = counting  # type: ignore[method-assign]
    result = r.ingest_records(
        _batch(
            ("r1", "fact", 1, "alpha one"),
            ("r1", "fact", 2, "alpha two"),
            ("r1", "fact", 3, "alpha three"),
        )
    )
    r.close()
    assert result.ok and len(result.written) == 3 and len(holds) == 3


def test_a_supersede_flips_the_old_record_before_returning(room) -> None:
    assert room.ingest_records(_batch(("r1", "fact", 1, "quokka lives in the old place"))).ok
    result = room.ingest_records(
        _batch(("r1", "fact", 2, "quokka lives in the new place", "pocket:r1:fact:1"))
    )
    assert result.ok and result.superseded == ("pocket.r1.fact.1",)
    assert _stored(room)["pocket.r1.fact.1"]["lifecycle"] != "active"
    assert _recall_ids(room, "quokka") == ["pocket.r1.fact.2"]


def test_superseding_an_unknown_record_is_not_a_failure(room) -> None:
    result = room.ingest_records(_batch(("r1", "fact", 2, "gecko fact", "pocket:r1:fact:1")))
    assert result.ok and result.superseded == ()
    assert _stored(room)["pocket.r1.fact.2"]["supersedes"] == "pocket.r1.fact.1"


def test_a_failed_supersede_flip_is_reported_not_swallowed(tmp_path) -> None:
    def broken_archive(*a, **kw):
        return continuity.ArchiveOutcome(
            ok=False,
            record_id="x",
            degradation=continuity.Degradation("eidetic", "archive", "subsystem-error", "no"),
        )

    r = mem.RoomMemory(tmp_path / "s", archive_fn=broken_archive)
    assert r.ingest_records(_batch(("r1", "fact", 1, "lemur old"))).ok
    result = r.ingest_records(_batch(("r1", "fact", 2, "lemur new", "pocket:r1:fact:1")))
    r.close()
    assert not result.ok and result.failed and result.degradations


# -- criterion 5: collapse ---------------------------------------------------------------


def test_a_hash_collision_is_reported_and_nothing_is_deleted(room) -> None:
    assert room.ingest_records(_batch(("r1", "fact", 1, "identical words"))).ok
    result = room.ingest_records(_batch(("r2", "fact", 1, "identical words")))
    assert result.collapsed == (("pocket.r2.fact.1", "pocket.r1.fact.1"),)
    assert result.written == ()
    assert any(d.code == mem.CODE_INGEST_COLLAPSED for d in result.degradations)
    assert set(_stored(room)) == {"pocket.r1.fact.1"}


def test_a_collision_inside_one_batch_is_reported(room) -> None:
    result = room.ingest_records(
        _batch(("r1", "fact", 1, "twin text"), ("r2", "fact", 1, "twin text"))
    )
    assert result.written == ("pocket.r1.fact.1",)
    assert result.collapsed == (("pocket.r2.fact.1", "pocket.r1.fact.1"),)


def test_a_collision_with_the_superseded_record_replaces_it_and_says_so(room) -> None:
    assert room.ingest_records(_batch(("r1", "fact", 1, "same sentence"))).ok
    result = room.ingest_records(_batch(("r1", "fact", 2, "same sentence", "pocket:r1:fact:1")))
    assert result.ok and result.written == ("pocket.r1.fact.2",)
    assert result.collapsed == (("pocket.r1.fact.2", "pocket.r1.fact.1"),)
    assert set(_stored(room)) == {"pocket.r1.fact.2"}


def test_re_ingesting_the_same_id_is_idempotent_not_a_collapse(room) -> None:
    batch = _batch(("r1", "fact", 1, "same again"))
    assert room.ingest_records(batch).ok
    again = room.ingest_records(batch)
    assert again.ok and again.collapsed == () and len(_stored(room)) == 1


# -- bad input, saturation, degrade-never-raise --------------------------------------------


def test_unusable_records_are_failed_not_raised(room) -> None:
    result = room.ingest_records([{"id": "x y", "text": "t", "type": "fact"}, "nope", None])
    assert not result.ok and len(result.failed) == 3 and result.degradations


def test_a_saturated_layer_stops_the_batch_and_names_the_rest(tmp_path) -> None:
    r = mem.RoomMemory(tmp_path / "s", max_inflight=1)
    r._inflight = 1  # every slot taken
    result = r.ingest_records(_batch(("r1", "fact", 1, "a one"), ("r1", "fact", 2, "a two")))
    r._inflight = 0
    r.close()
    assert not result.ok and result.written == ()
    assert result.not_attempted == ("pocket.r1.fact.2",)
    assert any(d.code == mem.CODE_SATURATED for d in result.degradations)


def test_a_closed_layer_refuses_visibly(tmp_path) -> None:
    r = mem.RoomMemory(tmp_path / "s")
    r.close()
    result = r.ingest_records(_batch(("r1", "fact", 1, "late")))
    assert not result.ok and result.degradations[0].code == mem.CODE_CLOSED


def test_the_store_seam_raising_degrades(tmp_path) -> None:
    def boom(*a, **kw):
        raise RuntimeError("secret heard line")

    r = mem.RoomMemory(tmp_path / "s", remember_fn=boom)
    result = r.ingest_records(_batch(("r1", "fact", 1, "kiwi")))
    r.close()
    assert not result.ok and result.failed
    assert "secret heard line" not in json.dumps(result.to_dict())


def test_ingested_files_are_private_on_disk(room, tmp_path) -> None:
    assert room.ingest_records(_batch(("r1", "fact", 1, "tapir"))).ok
    path = tmp_path / "store" / f"{mem.INGEST_SCOPE}__private.jsonl"
    assert path.stat().st_mode & 0o777 == 0o600


# -- criterion 4: forget by source and by id ------------------------------------------------


def _thirty(room) -> None:
    specs = [
        (f"rec{i // 10}", "fact", i % 10, f"distinct fact {i} about topic{i}") for i in range(30)
    ]
    assert room.ingest_records(_batch(*specs)).ok


def test_forget_source_archives_exactly_one_recording(room) -> None:
    _thirty(room)
    result = room.forget_source("pocket", "rec1")
    assert result.ok and result.matched == 10 and len(result.archived) == 10
    rows = _stored(room)
    states = {rid: row["lifecycle"] for rid, row in rows.items()}
    archived = [rid for rid, st in states.items() if st == "archived"]
    assert sorted(archived) == sorted(f"pocket.rec1.fact.{n}" for n in range(10))
    assert sum(1 for st in states.values() if st != "archived") == 20
    assert _recall_ids(room, "distinct") and all(
        "rec1" not in i for i in _recall_ids(room, "distinct")
    )


def test_forget_source_enumerates_rather_than_ranking(room) -> None:
    # Many records, query-unfriendly text: a ranked top-k recall could not see them all.
    specs = [("big", "fact", i, f"word{i} zzz") for i in range(60)]
    assert room.ingest_records(_batch(*specs[:60])).ok
    # A generous total deadline: this pins enumeration, not speed (d6 made the
    # deadline one total, so a loaded suite could otherwise end it partial).
    result = room.forget_source("pocket", "big", deadline=30.0)
    assert result.matched == 60 and len(result.archived) == 60


def test_forget_source_is_idempotent_and_reports_the_already_archived(room) -> None:
    _thirty(room)
    room.forget_source("pocket", "rec0")
    again = room.forget_source("pocket", "rec0")
    assert again.ok and again.archived == () and len(again.already_archived) == 10


def test_forget_source_never_touches_the_room_scope(room) -> None:
    room.remember("room memory about rec1", record_id="gwen-keep")
    _thirty(room)
    room.forget_source("pocket", "rec1")
    assert _stored(room, "gwen")["gwen-keep"]["lifecycle"] == "active"


class _SlowStore:
    """list_fn + archive_fn seams where every step costs *step* seconds of wall time."""

    def __init__(self, n: int, step: float) -> None:
        self.step = step
        self.rows = {
            f"pocket.slow.fact.{i}": {
                "id": f"pocket.slow.fact.{i}",
                "lifecycle": "active",
                "metadata": {"source": "pocket", "recording_id": "slow"},
            }
            for i in range(n)
        }

    def list_fn(self, **_kw: Any) -> list[dict[str, Any]]:
        time.sleep(self.step)
        return [dict(r) for r in self.rows.values()]

    def archive_fn(self, identifier: str, **_kw: Any) -> Any:
        time.sleep(self.step)
        self.rows[identifier]["lifecycle"] = continuity.LIFECYCLE_ARCHIVED
        return continuity.ArchiveOutcome(ok=True, record_id=identifier, degradation=None)


def test_forget_source_keeps_one_total_deadline_and_resumes(tmp_path) -> None:
    """d6: the deadline bounds the whole forget, not each step (1 + matched steps)."""
    store = _SlowStore(n=40, step=0.03)
    room = mem.RoomMemory(
        tmp_path / "s", list_fn=store.list_fn, archive_fn=store.archive_fn, max_inflight=64
    )
    try:
        started = time.monotonic()
        first = room.forget_source("pocket", "slow", deadline=0.3)
        elapsed = time.monotonic() - started
        assert elapsed < 0.3 + 0.15, f"forget_source ran {elapsed:.2f}s against a 0.3s deadline"
        assert not first.ok and first.matched == 40
        assert 0 < len(first.archived) < 40
        assert first.failed, "the ids it did not reach are reported, so the caller retries"
        reached = set(first.archived) | {rid for rid, _ in first.failed}
        assert reached == set(store.rows)
        codes = {code for _, code in first.failed}
        assert codes <= {mem.CODE_INGEST_NOT_ATTEMPTED, mem.CODE_REMEMBER_DEFERRED}
        time.sleep(0.1)  # let a step deferred past the deadline land
        again = room.forget_source("pocket", "slow", deadline=10.0)
        assert again.ok and again.matched == 40
        assert len(again.archived) + len(again.already_archived) == 40
        assert all(r["lifecycle"] == "archived" for r in store.rows.values())
    finally:
        room.close()


def test_forget_source_with_room_left_is_unchanged(tmp_path) -> None:
    store = _SlowStore(n=5, step=0.0)
    room = mem.RoomMemory(tmp_path / "s", list_fn=store.list_fn, archive_fn=store.archive_fn)
    try:
        result = room.forget_source("pocket", "slow", deadline=5.0)
    finally:
        room.close()
    assert result.ok and len(result.archived) == 5 and result.failed == ()


def test_forget_source_matches_the_issue_key_for_newspaper(room) -> None:
    lines = [
        json.dumps(
            {
                "id": f"newspaper:2026-10-01:item:{n}",
                "type": "item",
                "text": f"headline number {n}",
                "metadata": {
                    "source": "newspaper",
                    "issue_date": issue,
                    "section": "s",
                    "url": "https://example.invalid/x",
                },
            }
        )
        for n, issue in ((1, "2026-10-01"), (2, "2026-10-01"), (3, "2026-10-02"))
    ]
    batch = contract.validate_batch(lines)
    assert isinstance(batch, contract.Batch)
    assert room.ingest_records(batch.records).ok
    result = room.forget_source("newspaper", "2026-10-01")
    assert len(result.archived) == 2


def test_forget_source_rejects_an_unregistered_source(room) -> None:
    _thirty(room)
    for bad in (("diary", "rec1"), ("pocket", ""), (None, "rec1"), ("pocket", None)):
        result = room.forget_source(*bad)  # type: ignore[arg-type]
        assert not result.ok and result.degradations
    assert all(r["lifecycle"] != "archived" for r in _stored(room).values())


def test_forget_ids_archives_exact_ids_only(room) -> None:
    _thirty(room)
    result = room.forget_ids(["pocket.rec0.fact.3", "pocket.rec0.fact.4", "pocket.nope.fact.1"])
    assert result.archived == ("pocket.rec0.fact.3", "pocket.rec0.fact.4")
    assert result.not_found == ("pocket.nope.fact.1",)
    assert sum(1 for r in _stored(room).values() if r["lifecycle"] == "archived") == 2


def test_forget_ids_cannot_reach_another_scope(room) -> None:
    room.remember("room memory", record_id="gwen-keep")
    result = room.forget_ids(["gwen-keep"])
    assert result.not_found == ("gwen-keep",) and result.archived == ()
    assert _stored(room, "gwen")["gwen-keep"]["lifecycle"] == "active"


def test_forget_ids_flags_unusable_ids(room) -> None:
    result = room.forget_ids(["bad id!", 7])
    assert not result.ok and len(result.failed) == 2


def test_forget_never_raises_on_a_broken_list_seam(tmp_path) -> None:
    def boom(**kw):
        raise OSError("disk gone")

    r = mem.RoomMemory(tmp_path / "s", list_fn=boom)
    result = r.forget_source("pocket", "rec1")
    r.close()
    assert not result.ok and result.degradations


# -- criterion 5b: the forget tool's id path cannot reach the ingest scope --------------------


def test_the_forget_tool_path_cannot_archive_an_ingest_record(room) -> None:
    assert room.ingest_records(_batch(("r1", "fact", 1, "ocelot fact"))).ok
    result = room.forget("pocket.r1.fact.1")
    assert not result.ok
    assert _stored(room)["pocket.r1.fact.1"]["lifecycle"] == "active"


def test_a_room_pinned_to_the_ingest_scope_refuses_the_forget_tool(tmp_path) -> None:
    r = mem.RoomMemory(tmp_path / "s", scope=mem.INGEST_SCOPE)
    assert r.ingest_records(_batch(("r1", "fact", 1, "ibex fact"))).ok
    result = r.forget("pocket.r1.fact.1")
    r.close()
    assert not result.ok and result.code == continuity.CODE_INVALID_RECORD
    assert _stored(r)["pocket.r1.fact.1"]["lifecycle"] == "active"
