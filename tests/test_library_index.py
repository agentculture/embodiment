"""Document index (plan task t3). Synthetic text and a fake embedder only; never the gateway."""

import ast
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from embodiment.library import index as index_mod
from embodiment.library.index import (
    DEFAULT_KINDS,
    MAX_CHUNK_CHARS,
    DocumentIndex,
    Hit,
    IndexRefused,
)

VOCAB = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")


@dataclass(frozen=True)
class P:
    """Stands in for t2's epub.Passage: same attribute names, no import."""

    anchor: str
    heading: Optional[str]
    text: str
    start_s: Optional[float] = None
    end_s: Optional[float] = None
    speaker: Optional[str] = None


class FakeEmbed:
    """Bag-of-words over a tiny vocabulary; counts every call."""

    def __init__(self):
        self.calls = []

    @property
    def content(self):
        """The content-view embeds: one per chunk (the context view starts with the
        item's title, "Item <id>" in these tests, and is counted apart)."""
        return [c for c in self.calls if not c.startswith("Item ")]

    def __call__(self, text):
        self.calls.append(text)
        words = text.lower().split()
        return [float(words.count(w)) for w in VOCAB] + [0.01]


def _vec(*words):
    return [float(w in words) for w in VOCAB] + [0.0]


def _meta(item_id, sha="s1", kind="article", title=None, created_at="2026-10-20T08:00:00Z"):
    return {
        "id": item_id,
        "sha256": sha,
        "kind": kind,
        "title": title or f"Item {item_id}",
        "created_at": created_at,
    }


def _index(tmp_path, embed=None, **kw):
    return DocumentIndex(
        tmp_path / "library" / "index.json", embed=embed or FakeEmbed(), model_tag="m1", **kw
    )


# -- criterion 1: the surface ------------------------------------------------


def test_search_returns_hits_with_every_named_field(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("7")])
    idx.upsert_item(
        _meta("7", title="Night shift"),
        [P("ch1.xhtml#p0", "Intro", "alpha alpha bravo", 12.0, 19.5, "Ori")],
    )
    hits = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, Hit)
    assert hit.item_id == "7"
    assert hit.title == "Night shift"
    assert hit.kind == "article"
    assert hit.created_at == "2026-10-20T08:00:00Z"
    assert hit.anchor == "ch1.xhtml#p0"
    assert "alpha alpha bravo" in hit.text
    assert hit.start_s == 12.0
    assert hit.end_s == 19.5
    assert 0.0 < hit.score <= 1.0


def test_search_orders_by_score_and_respects_limit_and_threshold(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1"), _meta("2"), _meta("3")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    idx.upsert_item(_meta("2"), [P("a#0", None, "alpha bravo charlie delta")])
    idx.upsert_item(_meta("3"), [P("a#0", None, "golf hotel")])
    hits = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert [h.item_id for h in hits][:2] == ["1", "2"]
    assert hits[0].score >= hits[1].score
    assert len(idx.search(_vec("alpha"), kinds=None, limit=1, threshold=0.0)) == 1
    strict = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.9)
    assert [h.item_id for h in strict] == ["1"]


def test_remove_item_purges_it(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha uniqueword")])
    assert idx.remove_item("1") is True
    assert idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0) == []
    assert "uniqueword" not in idx.path.read_text(encoding="utf-8")
    assert idx.remove_item("1") is False


def test_passage_duck_typing_accepts_mappings_too(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [{"anchor": "a#0", "heading": None, "text": "alpha"}])
    (hit,) = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert hit.anchor == "a#0"
    assert hit.start_s is None
    assert hit.end_s is None


def test_read_passage_and_item_return_stored_text(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", "H", "alpha"), P("a#1", "H", "bravo", 3.0, 4.0, "Gwen")])
    passage = idx.read_passage("1", "a#1")
    assert passage["text"] == "bravo"
    assert passage["speaker"] == "Gwen"
    assert idx.read_passage("1", "nope") is None
    assert idx.read_passage("404", "a#0") is None
    item = idx.item("1")
    assert item["title"] == "Item 1"
    assert [p["anchor"] for p in item["passages"]] == ["a#0", "a#1"]


# -- chunking ----------------------------------------------------------------


def test_chunks_join_passages_up_to_the_cap_on_passage_boundaries(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    passages = [P(f"d#{i}", "H", ("alpha " * 60).strip()) for i in range(10)]  # 359 chars each
    idx.upsert_item(_meta("1"), passages)
    assert len(embed.content) == 5  # two passages fit (719 + sep), a third would not
    assert all(len(t) <= MAX_CHUNK_CHARS + 50 for t in embed.calls)
    hits = idx.search(_vec("alpha"), kinds=None, limit=20, threshold=0.0)
    assert sorted(h.anchor for h in hits) == ["d#0", "d#2", "d#4", "d#6", "d#8"]


def test_a_heading_change_starts_a_new_chunk(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("d#0", "Jetson", "alpha"), P("d#1", "Isaac", "bravo")])
    assert len(embed.content) == 2


def test_an_oversized_passage_is_split_without_exceeding_the_cap(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("d#0", None, ("alpha " * 500).strip())])
    assert len(embed.content) >= 3
    hits = idx.search(_vec("alpha"), kinds=None, limit=20, threshold=0.0)
    assert {h.anchor for h in hits} == {"d#0"}
    assert all(len(h.text) <= MAX_CHUNK_CHARS for h in hits)


def test_chunk_timing_spans_its_passages(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1", kind="transcript")])
    idx.upsert_item(
        _meta("1", kind="transcript"),
        [P("t#0", None, "alpha", 1.0, 2.0, "A"), P("t#1", None, "bravo", 2.5, 6.0, "B")],
    )
    (hit,) = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert (hit.anchor, hit.start_s, hit.end_s) == ("t#0", 1.0, 6.0)


def test_an_empty_or_blank_item_embeds_nothing(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    assert idx.upsert_item(_meta("1"), [P("d#0", None, "   ")]) == 0
    assert embed.calls == []


# -- criterion 2: the file ----------------------------------------------------


def test_index_file_is_0600_under_the_callers_path(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    path = tmp_path / "library" / "index.json"
    assert idx.path == path
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_index_refuses_a_path_inside_an_eidetic_store(tmp_path):
    with pytest.raises(ValueError):
        DocumentIndex(
            tmp_path / ".eidetic" / "memory" / "index.json", embed=FakeEmbed(), model_tag="m"
        )


def test_one_atomic_write_per_upsert_not_per_chunk(tmp_path, monkeypatch):
    replaces = []
    real = os.replace
    monkeypatch.setattr(index_mod.os, "replace", lambda a, b: (replaces.append(b), real(a, b)))
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    replaces.clear()
    idx.upsert_item(_meta("1"), [P(f"d#{i}", f"H{i}", "alpha") for i in range(6)])
    assert len(embed.content) == 6
    assert len(replaces) == 1


def test_batch_defers_the_write_to_one_save(tmp_path, monkeypatch):
    replaces = []
    real = os.replace
    monkeypatch.setattr(index_mod.os, "replace", lambda a, b: (replaces.append(b), real(a, b)))
    idx = _index(tmp_path)
    with idx.batch():
        idx.reconcile([_meta("1"), _meta("2")])
        idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
        idx.upsert_item(_meta("2"), [P("a#0", None, "bravo")])
        assert replaces == []
    assert len(replaces) == 1


def test_unchanged_sha256_is_not_re_embedded_even_across_a_restart(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    passages = [P("a#0", None, "alpha"), P("a#1", "H2", "bravo")]
    assert idx.upsert_item(_meta("1"), passages) == 2
    assert len(embed.content) == 2
    assert idx.upsert_item(_meta("1"), passages) == 0
    assert len(embed.content) == 2
    again = _index(tmp_path, embed)
    assert again.upsert_item(_meta("1"), passages) == 0
    assert len(embed.content) == 2
    assert again.reconcile([_meta("1")]) == {"added": [], "changed": [], "removed": []}
    assert len(again.search(_vec("alpha"), kinds=None, limit=5, threshold=0.5)) == 1


def test_a_different_model_tag_on_disk_is_not_trusted(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    other = DocumentIndex(idx.path, embed=embed, model_tag="m2")
    assert other.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0) == []
    assert other.reconcile([_meta("1")])["added"] == ["1"]
    assert other.degradations


def test_a_corrupt_file_degrades_to_empty_without_raising(tmp_path):
    path = tmp_path / "library" / "index.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    idx = DocumentIndex(path, embed=FakeEmbed(), model_tag="m1")
    assert idx.degradations
    assert "unreadable" in idx.degradations[0]
    assert idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0) == []


def test_an_embed_failure_stores_nothing_partial_and_raises_a_typed_refusal(tmp_path):
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 2:
            raise OSError("lane down")
        return _vec("alpha")

    idx = _index(tmp_path, flaky)
    idx.reconcile([_meta("1")])
    with pytest.raises(IndexRefused) as err:
        idx.upsert_item(_meta("1"), [P("a#0", "A", "alpha"), P("a#1", "B", "bravo")])
    assert err.value.code == "embed_failed"
    assert idx.item("1") is None
    assert idx.reconcile([_meta("1")])["added"] == ["1"]


def test_a_non_numeric_vector_is_an_embed_failure(tmp_path):
    idx = _index(tmp_path, lambda text: None)
    idx.reconcile([_meta("1")])
    with pytest.raises(IndexRefused) as err:
        idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    assert err.value.code == "embed_failed"


def test_an_item_without_id_or_sha_is_refused(tmp_path):
    idx = _index(tmp_path)
    with pytest.raises(IndexRefused) as err:
        idx.upsert_item({"id": "1"}, [P("a#0", None, "alpha")])
    assert err.value.code == "bad_item"


# -- criterion 3: reconcile ---------------------------------------------------


def test_reconcile_reports_added_changed_removed(tmp_path):
    idx = _index(tmp_path)
    assert idx.reconcile([_meta("1"), _meta("2")]) == {
        "added": ["1", "2"],
        "changed": [],
        "removed": [],
    }
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    idx.upsert_item(_meta("2"), [P("a#0", None, "bravo")])
    result = idx.reconcile([_meta("2", sha="s2"), _meta("3")])
    assert result == {"added": ["3"], "changed": ["2"], "removed": ["1"]}


def test_a_dropped_item_loses_its_chunks_and_its_stored_text(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1"), _meta("2")])
    idx.upsert_item(_meta("1"), [P("a#0", "Secret heading", "alpha zuluword")])
    idx.upsert_item(_meta("2"), [P("a#0", None, "bravo")])
    assert "zuluword" in idx.path.read_text(encoding="utf-8")
    idx.reconcile([_meta("2")])
    on_disk = idx.path.read_text(encoding="utf-8")
    assert "zuluword" not in on_disk
    assert "Secret heading" not in on_disk
    assert "1" not in json.loads(on_disk)["items"]
    assert idx.item("1") is None
    assert idx.read_passage("1", "a#0") is None
    reloaded = _index(tmp_path)
    assert reloaded.item("1") is None


def test_a_changed_sha256_re_indexes(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha oldword")])
    assert idx.reconcile([_meta("1", sha="s2")])["changed"] == ["1"]
    before = len(embed.content)
    assert idx.upsert_item(_meta("1", sha="s2"), [P("a#0", None, "bravo newword")]) == 1
    assert len(embed.content) == before + 1
    (hit,) = idx.search(_vec("bravo"), kinds=None, limit=5, threshold=0.0)
    assert "newword" in hit.text
    # the upsert replaced the old version whole
    assert "oldword" not in idx.path.read_text(encoding="utf-8")
    assert idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.5) == []


def test_a_changed_item_stays_readable_until_its_new_version_is_indexed(tmp_path):
    """Review of PR #98 (2026-10-24): reconcile purged a changed item at once, and the
    re-index runs in later bounded ticks that can stop (embed_failed, the gate) -
    so a re-uploaded document vanished from search and the reader until then."""
    idx = _index(tmp_path)
    idx.reconcile([_meta("1", title="Old")])
    idx.upsert_item(_meta("1", title="Old"), [P("a#0", None, "alpha oldword")])
    assert idx.reconcile([_meta("1", sha="s2", title="New")])["changed"] == ["1"]
    (hit,) = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert "oldword" in hit.text
    assert hit.title == "Old", "the old passages keep the old metadata until replaced"
    assert idx.read_passage("1", "a#0")["text"] == "alpha oldword"
    assert _index(tmp_path).item("1")["sha256"] == "s1", "kept across a restart too"
    # still reported as changed until the new version lands, so the sync retries it
    assert idx.reconcile([_meta("1", sha="s2", title="New")])["changed"] == ["1"]


def test_a_changed_item_whose_re_embed_fails_keeps_the_old_version(tmp_path):
    calls = {"n": 0}
    embed = FakeEmbed()

    def flaky(text):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("embedder down")
        return embed(text)

    idx = _index(tmp_path, flaky)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha oldword")])
    idx.reconcile([_meta("1", sha="s2")])
    with pytest.raises(IndexRefused):
        idx.upsert_item(_meta("1", sha="s2"), [P("a#0", None, "bravo newword")])
    assert idx.item("1")["passages"][0]["text"] == "alpha oldword"


def test_upserting_a_changed_sha_directly_re_embeds(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1", sha="s2")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    assert idx.upsert_item(_meta("1", sha="s2"), [P("a#0", None, "bravo")]) == 1
    assert len(embed.content) == 2


def test_search_never_returns_an_id_absent_from_the_last_reconciled_catalog(tmp_path):
    idx = _index(tmp_path)
    # never reconciled: nothing is citable yet
    idx.upsert_item(_meta("9"), [P("a#0", None, "alpha")])
    assert idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0) == []
    idx.reconcile([_meta("9"), _meta("8")])
    idx.upsert_item(_meta("8"), [P("a#0", None, "alpha")])
    assert {h.item_id for h in idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)} == {
        "8",
        "9",
    }
    # an item upserted after the reconcile that the catalog does not name stays uncitable
    idx.upsert_item(_meta("77"), [P("a#0", None, "alpha")])
    ids = {h.item_id for h in idx.search(_vec("alpha"), kinds=None, limit=50, threshold=-1.0)}
    assert ids == {"8", "9"}
    idx.reconcile([_meta("8")])
    ids = {h.item_id for h in idx.search(_vec("alpha"), kinds=None, limit=50, threshold=-1.0)}
    assert ids == {"8"}


def test_the_reconciled_catalog_survives_a_restart(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    assert len(_index(tmp_path).search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)) == 1


def test_reconcile_refreshes_metadata_of_unchanged_items_without_embedding(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    before = len(embed.content)
    idx.reconcile([_meta("1", title="Renamed")])
    assert len(embed.content) == before
    (hit,) = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert hit.title == "Renamed"


def test_integer_ids_from_the_library_are_normalised_to_strings(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta(5)])
    idx.upsert_item(_meta(5), [P("a#0", None, "alpha")])
    (hit,) = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert hit.item_id == "5"
    assert idx.remove_item(5) is True


def test_catalog_entries_may_be_objects(tmp_path):
    @dataclass
    class Item:
        id: int
        sha256: str
        kind: str = "article"
        title: str = "T"
        created_at: str = "2026-10-01"

    idx = _index(tmp_path)
    assert idx.reconcile([Item(3, "x")])["added"] == ["3"]
    idx.upsert_item(Item(3, "x"), [P("a#0", None, "alpha")])
    assert idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)[0].item_id == "3"


# -- criterion 4: kinds --------------------------------------------------------


def _three_kinds(tmp_path, **kw):
    idx = _index(tmp_path, **kw)
    metas = [_meta("t", kind="transcript"), _meta("a", kind="article"), _meta("b", kind="book")]
    idx.reconcile(metas)
    for meta in metas[:2]:
        idx.upsert_item(meta, [P("x#0", None, "alpha")])
    return idx, metas[2]


def test_default_kinds_are_transcripts_and_articles():
    assert set(DEFAULT_KINDS) == {"transcript", "article"}


def test_a_book_not_opted_in_is_refused_without_embedding(tmp_path):
    embed = FakeEmbed()
    idx, book = _three_kinds(tmp_path, embed=embed)
    before = len(embed.content)
    assert idx.wants(book) is False
    with pytest.raises(IndexRefused) as err:
        idx.upsert_item(book, [P("x#0", None, "alpha")])
    assert err.value.code == "book_not_opted_in"
    assert len(embed.content) == before
    ids = {h.item_id for h in idx.search(_vec("alpha"), kinds=("book",), limit=9, threshold=0.0)}
    assert ids == set()


def test_books_are_searched_only_when_opted_in_by_item_id(tmp_path):
    idx, book = _three_kinds(tmp_path, book_ids=["b"])
    assert idx.wants(book) is True
    idx.upsert_item(book, [P("x#0", None, "alpha")])
    default = {h.item_id for h in idx.search(_vec("alpha"), kinds=None, limit=9, threshold=0.0)}
    assert default == {"t", "a", "b"}
    only_books = idx.search(_vec("alpha"), kinds=("book",), limit=9, threshold=0.0)
    assert {h.item_id for h in only_books} == {"b"}
    transcripts = idx.search(_vec("alpha"), kinds=("transcript",), limit=9, threshold=0.0)
    assert {h.item_id for h in transcripts} == {"t"}


def test_a_book_whose_opt_in_is_withdrawn_is_not_searched(tmp_path):
    idx, book = _three_kinds(tmp_path, book_ids=["b"])
    idx.upsert_item(book, [P("x#0", None, "alpha")])
    narrowed = DocumentIndex(idx.path, embed=FakeEmbed(), model_tag="m1", book_ids=[])
    ids = {h.item_id for h in narrowed.search(_vec("alpha"), kinds=None, limit=9, threshold=0.0)}
    assert ids == {"t", "a"}


def test_newspapers_are_never_expired_by_the_index(tmp_path):
    idx = _index(tmp_path)
    old = _meta("5", title="Daily newspaper 2019-01-01", created_at="2019-01-01T06:00:00Z")
    idx.reconcile([old])
    idx.upsert_item(old, [P("n#0", "Jetson news", "alpha")])
    for _ in range(3):
        idx.reconcile([old])
    reloaded = _index(tmp_path)
    (hit,) = reloaded.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert hit.item_id == "5"
    assert hit.created_at == "2019-01-01T06:00:00Z"


def test_the_index_has_no_clock_so_nothing_can_expire_by_age():
    tree = ast.parse(Path(index_mod.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    froms = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {"time", "datetime"} & (names | imports | froms)


def test_stats_counts_items_and_chunks(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1"), _meta("2")])
    idx.upsert_item(_meta("1"), [P("a#0", "A", "alpha"), P("a#1", "B", "bravo")])
    assert idx.stats() == {"items": 1, "chunks": 2, "catalog": 2}


def test_a_query_vector_of_the_wrong_size_matches_nothing(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    assert idx.search([1.0, 0.0], kinds=None, limit=5, threshold=-1.0) == []
    assert idx.search([], kinds=None, limit=5, threshold=-1.0) == []


# -- 2026-10-24 live finding: mastheads out-ranked the news -------------------
#
# Every daily paper opens with a masthead passage ("Daily newspaper" / the date,
# 10 characters). A question naming the paper matched those mastheads, so the
# model got five dates and no articles. Chunks now carry their item's title in
# the embedding, and fragments under MIN_CHUNK_CHARS are not searched.


def _paper(idx, item_id, day_word, body_word):
    meta = _meta(item_id, title=f"Daily newspaper {day_word}")
    idx.upsert_item(
        meta,
        [
            P(f"{item_id}#1", "Daily newspaper", day_word),  # the masthead: tiny
            P(f"{item_id}#3", "Jetson optimization", (f"{body_word} " * 20).strip()),
        ],
    )


def test_a_masthead_is_kept_for_reading_but_never_searched(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1"), _meta("2")])
    _paper(idx, "1", "golf", "alpha")
    _paper(idx, "2", "hotel", "bravo")
    hits = idx.search(_vec("golf"), kinds=None, limit=10, threshold=0.0)
    assert hits, "a question naming the paper finds something"
    assert all(len(h.text) >= index_mod.MIN_CHUNK_CHARS for h in hits)
    assert idx.read_passage("1", "1#1") is not None  # still readable in the reader


def test_a_question_naming_the_item_finds_that_items_content(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1"), _meta("2")])
    _paper(idx, "1", "golf", "alpha")
    _paper(idx, "2", "hotel", "bravo")
    best = idx.search(_vec("golf"), kinds=None, limit=1, threshold=0.0)[0]
    assert (best.item_id, best.anchor) == ("1", "1#3")  # the article, via the title


def test_every_chunk_is_embedded_as_content_and_in_its_items_context(tmp_path):
    embed = FakeEmbed()
    idx = _index(tmp_path, embed)
    idx.reconcile([_meta("1")])
    _paper(idx, "1", "golf", "alpha")
    content = [c for c in embed.calls if not c.startswith("Daily newspaper golf\n")]
    context = [c for c in embed.calls if c.startswith("Daily newspaper golf\n")]
    assert len(content) == len(context) == 1  # the masthead is not embedded at all
    assert context[0].endswith(content[0])


def test_a_content_question_scores_as_well_as_before_the_title_view(tmp_path):
    # Version 2 embedded the title view alone and lost content questions under the
    # threshold (measured live); the content view keeps their score whole.
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(
        _meta("1", title="golf hotel charlie delta echo"),
        [P("d#0", None, ("alpha " * 20).strip())],
    )
    [hit] = idx.search(_vec("alpha"), kinds=None, limit=5, threshold=0.0)
    assert hit.score > 0.99


def test_an_item_with_only_short_passages_is_still_searchable(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1")])
    idx.upsert_item(_meta("1"), [P("d#0", None, "alpha")])
    assert [h.anchor for h in idx.search(_vec("alpha"), kinds=None, limit=5)] == ["d#0"]


def test_an_index_built_at_an_older_version_re_indexes(tmp_path):
    path = tmp_path / "library" / "index.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "model_tag": "m1", "catalog": ["1"], "items": {}}))
    idx = _index(tmp_path)
    assert index_mod.INDEX_VERSION == 3
    assert any("re-indexing" in d for d in idx.degradations)


def test_has_item_matches_item_without_copying(tmp_path):
    """Review of PR #98 (2026-10-24): the catalog route tested existence with item()."""
    idx = _index(tmp_path)
    idx.upsert_item(_meta("1"), [P("a#0", None, "alpha")])
    assert idx.has_item("1") is False, "not citable before a reconcile, like item()"
    idx.reconcile([_meta("1"), _meta("2")])
    assert idx.has_item(1) is True
    assert idx.has_item("2") is False
    assert idx.has_item("1") == (idx.item("1") is not None)


def test_indexed_items_lists_metadata_only_for_citable_items(tmp_path):
    idx = _index(tmp_path)
    idx.reconcile([_meta("1"), _meta("2")])
    idx.upsert_item(_meta("1", title="One"), [P("a#0", None, "alpha secretword")])
    idx.upsert_item(_meta("77"), [P("a#0", None, "alpha")])  # never in the catalog
    (entry,) = _index(tmp_path).indexed_items()
    assert entry["id"] == "1"
    assert entry["title"] == "One"
    assert entry["kind"] == "article"
    assert "passages" not in entry
    assert "secretword" not in json.dumps(entry)
