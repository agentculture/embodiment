"""embodiment/daemon/reading.py: saved positions and the next/heard state machine."""

from __future__ import annotations

import hashlib
import json
import stat
import threading
from dataclasses import dataclass, field

from embodiment.daemon.reading import (
    CODE_GONE,
    CODE_NOT_FOUND,
    CODE_UNAVAILABLE,
    MAX_PART_CHARS,
    POSITIONS_NAME,
    ItemGone,
    ItemUnavailable,
    Reader,
    ReadingPositions,
    split_parts,
)


@dataclass
class Section:
    heading: str
    paragraphs: list
    links: list = field(default_factory=list)


@dataclass
class Document:
    item_id: str
    sha256: str
    title: str
    kind: str
    sections: list


def make_doc(item_id="item-a", sections=None, salt="v1"):
    sections = sections or [
        Section("One", [f"p{i} of one." for i in range(10)]),
        Section("Two", ["alpha.", "beta."]),
    ]
    sha = hashlib.sha256((salt + repr(sections)).encode()).hexdigest()
    return Document(item_id, sha, "Synthetic title", "book", sections)


class Catalog:
    def __init__(self, *docs):
        self.docs = {d.item_id: d for d in docs}
        self.gone = set()
        self.down = False

    def __call__(self, item_id):
        if self.down:
            raise ItemUnavailable()
        if item_id in self.gone:
            raise ItemGone()
        return self.docs[item_id]


def build(tmp_path, *docs, clock=lambda: 100.0):
    cat = Catalog(*docs)
    pos = ReadingPositions(tmp_path / POSITIONS_NAME, clock)
    return Reader(pos, cat), pos, cat


def test_next_starts_then_continues_and_persists(tmp_path):
    reader, pos, _ = build(tmp_path, make_doc())
    first = reader.next("item-a")
    assert (first["section"], first["paragraph"], first["part"]) == (0, 0, 0)
    assert first["text"] == "p0 of one."
    assert first["section_count"] == 2
    assert first["paragraph_count"] == 10
    assert reader.heard("item-a", first["position"])["moved"] is True
    second = reader.next("item-a")
    assert second["paragraph"] == 1
    c = reader.counters()
    assert c["reads_started"] == 1
    assert c["continues"] == 1
    assert c["units_served"] == 2
    assert c["units_heard"] == 1
    assert c["last_item"] == "item-a"
    assert c["last_position"] == second["position"]
    # restart: a new object over the same file resumes
    reader2 = Reader(ReadingPositions(tmp_path / POSITIONS_NAME), Catalog(make_doc()))
    assert reader2.next("item-a")["paragraph"] == 1


def test_file_is_0600_and_unreadable_set_aside(tmp_path):
    reader, pos, _ = build(tmp_path, make_doc())
    reader.heard("item-a", reader.next("item-a")["position"])
    path = tmp_path / POSITIONS_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("{not json", encoding="utf-8")
    fresh = ReadingPositions(path)
    assert fresh.all() == []
    assert fresh.unreadable == 1
    assert (tmp_path / (POSITIONS_NAME + ".unreadable")).exists()
    assert fresh.update(
        "item-a", lambda c: {"sha256": "a" * 64, "section": 0, "paragraph": 1, "part": 0}
    )[1]
    assert json.loads(path.read_text())["item-a"]["paragraph"] == 1


def test_heard_must_be_exact_next_and_never_backwards(tmp_path):
    reader, pos, _ = build(tmp_path, make_doc())
    toks = []
    for _ in range(5):
        t = reader.next("item-a")["position"]
        toks.append(t)
        assert reader.heard("item-a", t)["moved"]
    cursor = pos.get("item-a")
    assert cursor["paragraph"] == 5
    # stale, ahead, malformed, wrong item hash
    for bad in (toks[1], "deadbeef0000.0.9.0", "garbage", None, 7):
        out = reader.heard("item-a", bad)
        assert out["moved"] is False
    assert pos.get("item-a")["paragraph"] == 5
    assert reader.counters()["heard_refused"] == 5


def test_never_past_end_then_done(tmp_path):
    doc = make_doc(sections=[Section("Only", ["a.", "b."])])
    reader, pos, _ = build(tmp_path, doc)
    for _ in range(2):
        assert reader.heard("item-a", reader.next("item-a")["position"])["moved"]
    assert reader.next("item-a")["done"] is True
    assert "position" not in reader.next("item-a")
    before = pos.get("item-a")
    assert reader.heard("item-a", "x")["moved"] is False
    assert pos.get("item-a")["section"] == before["section"] == 1


def test_long_paragraph_split_at_sentences_verbatim(tmp_path):
    sentence = "This is a sentence of moderate size, with words. "
    para = (sentence * 200).rstrip() + " Tail end!  Two spaces."
    giant = "x" * 9000 + " " + "y" * 100
    unbroken = "z" * 8500
    for text in (para, giant, unbroken):
        parts = split_parts(text)
        assert "".join(parts) == text
        assert all(len(p) <= MAX_PART_CHARS for p in parts)
    assert len(split_parts(para)) >= 3
    assert all(p[-1].isspace() for p in split_parts(para)[:-1])
    doc = make_doc(sections=[Section("L", [para, "short."])])
    reader, _, _ = build(tmp_path, doc)
    seen, texts = [], []
    while True:
        unit = reader.next("item-a")
        if unit.get("done"):
            break
        if unit["paragraph"] == 0:
            texts.append(unit["text"])
            assert unit["part_count"] == len(split_parts(para))
        seen.append(unit["position"])
        assert reader.heard("item-a", unit["position"])["moved"]
    assert "".join(texts) == para
    assert len(set(seen)) == len(seen) == len(split_parts(para)) + 1


def test_changed_sha_resumes_at_section_start(tmp_path):
    reader, pos, cat = build(tmp_path, make_doc())
    for _ in range(11):  # 10 in section one, then one in section two
        reader.heard("item-a", reader.next("item-a")["position"])
    assert pos.get("item-a")["section"] == 1
    assert pos.get("item-a")["paragraph"] == 1
    stale = reader.next("item-a")["position"]
    cat.docs["item-a"] = make_doc(salt="v2")  # edited: new sha256, same shape
    resumed = reader.next("item-a")
    assert (resumed["section"], resumed["paragraph"]) == (1, 0)
    assert reader.heard("item-a", stale)["moved"] is False  # old token names the old text
    assert reader.heard("item-a", resumed["position"])["moved"] is True
    # saved section vanished: start of the item
    cat.docs["item-a"] = make_doc(sections=[Section("Solo", ["only."])], salt="v3")
    pos.update("item-a", lambda c: {"sha256": c["sha256"], "section": 5, "paragraph": 2, "part": 0})
    assert reader.next("item-a")["section"] == 0


def test_gone_item_keeps_cursor_and_lists_gone(tmp_path):
    reader, pos, cat = build(tmp_path, make_doc(), make_doc("item-b"))
    reader.heard("item-a", reader.next("item-a")["position"])
    reader.next("item-b")
    cat.gone.add("item-a")
    assert reader.next("item-a") == {"error": CODE_GONE, "item_id": "item-a"}
    assert reader.heard("item-a", "x")["error"] == CODE_GONE
    listing = {row["item_id"]: row for row in reader.listing()}
    assert listing["item-a"]["gone"] is True
    assert listing["item-b"]["gone"] is False
    assert listing["item-a"]["paragraph"] == 1
    assert set(listing["item-a"]) >= {
        "item_id",
        "sha256",
        "section",
        "paragraph",
        "part",
        "updated_at",
        "gone",
    }
    cat.down = True
    assert reader.next("item-b")["error"] == CODE_UNAVAILABLE
    assert reader.next("bad id!")["error"] == CODE_NOT_FOUND
    cat.down = False
    reader.clear("item-a")
    assert [r["item_id"] for r in reader.listing()] == ["item-b"]


def test_a_seek_repins_the_cursor_to_the_sought_unit(tmp_path):
    """d1 (approved 2026-10-26): previous/next paragraph and Choose section move the cursor."""
    reader, pos, _ = build(tmp_path, make_doc())
    for _ in range(3):
        reader.heard("item-a", reader.next("item-a")["position"])
    before = reader.next("item-a")["position"]  # paragraph 3, the cursor's next
    unit = reader.next("item-a", section=1, paragraph=1)
    assert unit["text"] == "beta."
    assert unit["heading"] == "Two"
    assert (pos.get("item-a")["section"], pos.get("item-a")["paragraph"]) == (1, 1)
    assert reader.heard("item-a", before)["moved"] is False  # stale after the seek
    assert reader.heard("item-a", unit["position"])["moved"] is True
    assert reader.next("item-a")["done"] is True  # heard the last unit
    back = reader.next("item-a", section=0, paragraph=2)  # a seek backwards is the operator's
    assert pos.get("item-a")["paragraph"] == 2
    assert back["paragraph"] == 2
    assert reader.next("item-a")["position"] == back["position"]  # continue plays the sought one
    assert reader.counters()["seeks"] == 2


def test_a_seek_past_the_end_is_done_and_moves_nothing(tmp_path):
    reader, pos, _ = build(tmp_path, make_doc())
    reader.heard("item-a", reader.next("item-a")["position"])
    assert reader.next("item-a", section=1)["paragraph"] == 0
    assert pos.get("item-a")["section"] == 1
    assert reader.next("item-a", section=0, paragraph=99)["done"] is True
    assert reader.next("item-a", section=9)["done"] is True
    assert (pos.get("item-a")["section"], pos.get("item-a")["paragraph"]) == (1, 0)


def test_interleaved_readers_never_move_backwards(tmp_path):
    shared = ReadingPositions(tmp_path / POSITIONS_NAME)
    cat = Catalog(make_doc())
    a, b = Reader(shared, cat), Reader(shared, cat)
    stale = [a.next("item-a")["position"]]
    for _ in range(3):  # A hears up to paragraph 3
        a.heard("item-a", a.next("item-a")["position"])
    a_at_3 = a.next("item-a")["position"]
    for _ in range(4):  # B gets to paragraph 7 first
        b.heard("item-a", b.next("item-a")["position"])
    assert shared.get("item-a")["paragraph"] == 7
    assert a.heard("item-a", a_at_3)["moved"] is False
    assert a.heard("item-a", stale[0])["moved"] is False
    assert shared.get("item-a")["paragraph"] == 7
    assert b.heard("item-a", b.next("item-a")["position"])["moved"] is True
    assert shared.get("item-a")["paragraph"] == 8


def test_concurrent_heard_is_atomic_and_file_not_torn(tmp_path):
    reader, pos, _ = build(tmp_path, make_doc())
    token = reader.next("item-a")["position"]
    results = []

    def go():
        results.append(reader.heard("item-a", token)["moved"])

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1
    on_disk = json.loads((tmp_path / POSITIONS_NAME).read_text())
    assert on_disk["item-a"]["paragraph"] == 1


def test_save_failure_keeps_cursor_and_reports_not_moved(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("file, not a directory")
    pos = ReadingPositions(blocker / POSITIONS_NAME)
    reader = Reader(pos, Catalog(make_doc()))
    out = reader.heard("item-a", reader.next("item-a")["position"])
    assert out["moved"] is False
    assert pos.save_failures >= 1


def test_a_unit_always_fits_one_speak_answer():
    # Code review of #110: a 4000-character unit needed up to ~200 s of audio against
    # /api/speak's 120 s cap. The part cap is derived from that cap.
    from embodiment.daemon.reading import MAX_PART_CHARS
    from embodiment.daemon.text_turns import MAX_SPEAK_AUDIO_BYTES
    from embodiment.voice import MAX_AUDIO_S_PER_CHAR

    cap_s = MAX_SPEAK_AUDIO_BYTES / (24000 * 2)
    assert MAX_PART_CHARS * MAX_AUDIO_S_PER_CHAR <= cap_s


def test_the_listing_asks_the_catalog_not_the_documents():
    # Code review of #110: the listing fetched every saved item's /text to say "gone".
    fetched: list[str] = []

    def provider(item_id: str):
        fetched.append(item_id)
        raise ItemGone()

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        positions = ReadingPositions(Path(d) / POSITIONS_NAME)
        positions.update(
            "kept", lambda _c: {"section": 0, "paragraph": 1, "part": 0, "sha256": "x"}
        )
        positions.update(
            "dropped", lambda _c: {"section": 0, "paragraph": 0, "part": 0, "sha256": "y"}
        )
        reader = Reader(
            positions, provider, listed=lambda i: {"kept": True, "dropped": False}.get(i)
        )
        gone = {p["item_id"]: p["gone"] for p in reader.listing()}
    assert gone == {"kept": False, "dropped": True}
    assert fetched == []
