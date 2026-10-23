"""Library sync (plan task t10). Fakes only: no network, no gateway, no real library."""

import threading
import time
from dataclasses import dataclass
from typing import Optional

import pytest

from embodiment.library.client import LibraryError, LibraryItem
from embodiment.library.epub import EpubRefused
from embodiment.library.index import DocumentIndex
from embodiment.library.sync import LibrarySync


@dataclass(frozen=True)
class P:
    anchor: str
    heading: Optional[str]
    text: str
    start_s: Optional[float] = None
    end_s: Optional[float] = None
    speaker: Optional[str] = None


def item(i, sha="s1", kind="article"):
    return LibraryItem(
        id=i, sha256=sha, kind=kind, title=f"t{i}", author="", format="epub", size=1, created_at=""
    )


class FakeClient:
    def __init__(self, items):
        self.items = list(items)
        self.gets = 0
        self.fetched = []
        self.list_error = None
        self.fetch_error = {}

    def list_items(self, kind=None, q=None):
        self.gets += 1
        if self.list_error:
            raise LibraryError(self.list_error)
        return list(self.items)

    def fetch_file(self, item_id):
        self.gets += 1
        self.fetched.append(item_id)
        if item_id in self.fetch_error:
            raise LibraryError(self.fetch_error[item_id])
        return f"data-{item_id}".encode()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Embed:
    def __init__(self):
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return [1.0, 0.0]


def extract(data):
    return [P("a1", None, data.decode())]


def make(tmp_path, items, **kw):
    client = FakeClient(items)
    embed = Embed()
    index = DocumentIndex(tmp_path / "idx.json", embed=embed, model_tag="m")
    clock = kw.pop("clock", Clock())
    kw.setdefault("budget", 30.0)
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("extract", extract)
    return LibrarySync(client, index, clock=clock, **kw), client, index, embed, clock


def test_tick_indexes_added_and_status_shape(tmp_path):
    sync, client, index, embed, _ = make(tmp_path, [item(1), item(2)])
    out = sync.tick()
    assert out["indexed"] == 2
    st = sync.status()
    assert set(st) == {
        "configured",
        "items_indexed",
        "chunks",
        "last_sync_at",
        "failures_by_code",
        "gets",
        "skipped_books",
    }
    assert st["configured"] is True
    assert st["items_indexed"] == 2 and st["chunks"] == 2
    assert st["last_sync_at"] == 100.0
    assert st["gets"] == client.gets == 3
    assert st["failures_by_code"] == {} and st["skipped_books"] == 0


def test_second_tick_unchanged_fetches_nothing(tmp_path):
    sync, client, *_ = make(tmp_path, [item(1)])
    sync.tick()
    client.fetched.clear()
    sync.tick()
    assert client.fetched == []


def test_changed_item_is_refetched_and_upserted(tmp_path):
    sync, client, index, *_ = make(tmp_path, [item(1)])
    sync.tick()
    client.items = [item(1, sha="s2")]
    client.fetched.clear()
    out = sync.tick()
    assert client.fetched == [1]
    assert out["indexed"] == 1
    assert index.item(1)["sha256"] == "s2"


def test_removed_item_is_purged(tmp_path):
    sync, client, index, *_ = make(tmp_path, [item(1), item(2)])
    sync.tick()
    client.items = [item(2)]
    sync.tick()
    assert index.item(1) is None and index.item(2) is not None


def test_budget_stops_and_resumes_next_tick(tmp_path):
    clock = Clock()
    sync, client, index, *_ = make(tmp_path, [item(1), item(2), item(3)], clock=clock, budget=5.0)
    orig = client.fetch_file

    def slow(i):
        clock.t += 3.0
        return orig(i)

    client.fetch_file = slow
    first = sync.tick()
    assert first["indexed"] == 2  # 3s, 6s >= 5s budget: stops before the third
    second = sync.tick()
    assert second["indexed"] == 1
    assert sync.status()["items_indexed"] == 3


def test_unopted_book_is_not_fetched(tmp_path):
    sync, client, *_ = make(tmp_path, [item(1, kind="book")])
    sync.tick()
    assert client.fetched == []


def test_gate_held_means_no_embed_until_released_and_recall_first(tmp_path):
    held = threading.Event()
    held.set()
    sync, client, index, embed, _ = make(
        tmp_path, [item(1)], gate=held.is_set, sleep=lambda s: time.sleep(0.005)
    )
    order = []
    real = embed.__call__

    t = threading.Thread(target=sync.tick)
    t.start()
    time.sleep(0.1)
    assert embed.calls == []  # the tick is yielding
    embed("recall query")  # a live-turn recall embed is served first
    order.append(list(embed.calls))
    held.clear()
    t.join(5)
    assert not t.is_alive()
    assert order == [["recall query"]]
    assert embed.calls[0] == "recall query" and len(embed.calls) == 2
    assert real is not None


def test_gate_held_past_budget_yields_without_work(tmp_path):
    clock = Clock()

    def tick_sleep(s):
        clock.t += 1.0

    sync, client, index, embed, _ = make(
        tmp_path, [item(1)], clock=clock, gate=lambda: True, sleep=tick_sleep, budget=3.0
    )
    out = sync.tick()
    assert embed.calls == [] and client.fetched == []
    assert out["yielded"] is True


def test_gate_accepts_event_semantics_via_gate_embed(tmp_path):
    held = threading.Event()
    sync, *_ = make(tmp_path, [item(1)], gate=held.is_set)
    seen = []
    wrapped = sync.gate_embed(lambda t: seen.append(t) or [1.0])
    wrapped("x")
    assert seen == ["x"]


@pytest.mark.parametrize("code", ["library_unreachable", "library_unauthorized"])
def test_down_library_is_one_degradation_per_transition_and_freezes(tmp_path, code):
    sync, client, index, *_ = make(tmp_path, [item(1)])
    sync.tick()
    gets_before = sync.status()["gets"]
    client.list_error = code
    for _ in range(4):
        sync.tick()
    assert sync.degradations == [code]
    st = sync.status()
    assert st["failures_by_code"] == {code: 1}
    assert st["gets"] == gets_before  # counters frozen
    assert index.item(1) is not None  # nothing purged by an unreachable catalog
    client.list_error = None
    sync.tick()
    assert sync.degradations == [code, "recovered"]
    client.list_error = code
    sync.tick()
    assert sync.degradations == [code, "recovered", code]


def test_epub_refused_skipped_counted_not_retried_until_sha_changes(tmp_path):
    calls = []

    def ex(data):
        calls.append(data)
        raise EpubRefused("bad_epub")

    sync, client, *_ = make(tmp_path, [item(1)], extract=ex)
    sync.tick()
    sync.tick()
    sync.tick()
    assert len(calls) == 1
    st = sync.status()
    assert st["failures_by_code"] == {"bad_epub": 1}
    assert st["skipped_books"] == 1
    client.items = [item(1, sha="new")]
    sync.tick()
    assert len(calls) == 2
    assert sync.status()["failures_by_code"] == {"bad_epub": 2}


def test_item_fetch_unauthorized_degrades_and_stops_tick(tmp_path):
    sync, client, *_ = make(tmp_path, [item(1), item(2)])
    client.fetch_error = {1: "library_unauthorized"}
    sync.tick()
    assert client.fetched == [1]
    assert sync.degradations == ["library_unauthorized"]


def test_embed_failure_counted_and_item_retried(tmp_path):
    sync, client, index, embed, _ = make(tmp_path, [item(1)])
    boom = {"on": True}

    def flaky(text):
        if boom["on"]:
            raise RuntimeError("down")
        return [1.0]

    index._embed = flaky
    sync.tick()
    assert sync.status()["failures_by_code"] == {"embed_failed": 1}
    boom["on"] = False
    sync.tick()
    assert sync.status()["items_indexed"] == 1


def test_unconfigured_sync_is_inert(tmp_path):
    index = DocumentIndex(tmp_path / "i.json", embed=Embed(), model_tag="m")
    sync = LibrarySync(None, index, extract=extract, clock=Clock(), budget=1.0)
    assert sync.tick()["indexed"] == 0
    assert sync.status()["configured"] is False
