"""Gwen's own past conversations, indexed and searched by her (deviation d8)."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from embodiment.daemon.history import ConversationHistory
from embodiment.library import conversations as conv
from embodiment.library import tools as library_tools
from embodiment.library.index import DocumentIndex


def embed(text: str) -> list[float]:
    """A deterministic bag-of-words vector: shared words, higher cosine."""
    vec = [0.0] * 64
    for word in text.lower().split():
        word = word.strip("[]().,?!:;")
        if word:
            vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 64] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _log(sessions: Path, name: str, rows: list[dict], mtime: float = 1.0) -> None:
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.utime(path, (mtime, mtime))


TOMATOES = [
    {
        "role": "user",
        "text": "how often should I water the tomatoes in the garden",
        "ts": 100.0,
        "via": "typed",
    },
    {"role": "assistant", "text": "water the tomatoes every two days in summer heat", "ts": 101.0},
]
SHED = [
    {
        "role": "user",
        "text": "where did I leave the key to the blue shed door",
        "ts": 200.0,
        "via": "spoken",
    },
    {"role": "assistant", "text": "you said the shed key is under the flower pot", "ts": 201.0},
]


@pytest.fixture
def store(tmp_path: Path) -> ConversationHistory:
    _log(tmp_path / "sessions", "tomatoes", TOMATOES)
    _log(tmp_path / "sessions", "shed", SHED)
    return ConversationHistory(tmp_path / "sessions")


def _index(tmp_path: Path) -> DocumentIndex:
    return DocumentIndex(tmp_path / "conv-index.json", embed=embed, model_tag="test")


def test_each_turn_is_labelled_with_who_said_it_and_how(store):
    found = store.conversation("shed")
    meta, passages = conv.conversation_item(found["conversation"], found["turns"])
    assert meta["kind"] == "conversation"
    assert meta["id"] == "shed"
    assert meta["title"].startswith("Conversation 1970-01-01: where did I leave")
    assert passages[0]["text"].startswith("[the operator, spoken (an AI transcription)] ")
    assert passages[1]["text"].startswith("[Gwen, her own earlier reply (not a source of fact)] ")
    assert {p["heading"] for p in passages} == {"exchange 1"}, "one exchange, one chunk"


def test_a_conversation_that_grew_has_a_new_sha(store):
    found = store.conversation("shed")
    before, _ = conv.conversation_item(found["conversation"], found["turns"])
    more = found["turns"] + [{"role": "user", "text": "and the gate?", "via": "typed"}]
    after, _ = conv.conversation_item(found["conversation"], more)
    assert before["sha256"] != after["sha256"]


def test_settled_conversations_are_indexed_a_few_per_tick(store, tmp_path):
    index = _index(tmp_path)
    first = conv.index_settled(index, store, ["tomatoes", "shed"], limit=1)
    assert (first["indexed"], first["waiting"]) == (1, 1)
    second = conv.index_settled(index, store, ["tomatoes", "shed"], limit=1)
    assert (second["indexed"], second["waiting"]) == (1, 0)
    assert {i["id"] for i in index.indexed_items()} == {"tomatoes", "shed"}


def test_a_conversation_no_longer_settled_leaves_the_index(store, tmp_path):
    index = _index(tmp_path)
    conv.index_settled(index, store, ["tomatoes", "shed"])
    result = conv.index_settled(index, store, ["tomatoes"])
    assert result["removed"] == 1
    assert {i["id"] for i in index.indexed_items()} == {"tomatoes"}


def _search(index, collector, exclude=()):
    spec = conv.build_conversation_tool(index, embed, collector, exclude=lambda: exclude)
    return spec, spec.fn


def test_search_finds_the_right_conversation_fenced_and_labelled(store, tmp_path):
    index = _index(tmp_path)
    conv.index_settled(index, store, ["tomatoes", "shed"])
    collector = library_tools.CitationCollector()
    spec, search = _search(index, collector)
    assert spec.name == "search_conversations"
    out = search(query="where is the shed key", limit=1)
    assert "id=shed" in out
    assert "written-by=conversations" in out
    assert "not a source of fact" in out
    (source,) = collector.sources()
    assert source["source"] == "conversation"
    assert source["item_id"] == "shed"
    assert collector.library_text_in_context, "a quoted past ask must arm the write guard"


def test_the_conversation_the_turn_is_in_is_never_searched(store, tmp_path):
    index = _index(tmp_path)
    conv.index_settled(index, store, ["tomatoes", "shed"])
    collector = library_tools.CitationCollector()
    _, search = _search(index, collector, exclude=("shed",))
    out = search(query="where is the shed key", limit=1)
    assert "id=shed" not in out


def test_an_empty_search_answers_with_fixed_text(tmp_path):
    _, search = _search(_index(tmp_path), library_tools.CitationCollector())
    assert search(query="anything at all") == conv.NO_MATCHES


def test_a_bad_query_is_a_declared_refusal(tmp_path):
    _, search = _search(_index(tmp_path), library_tools.CitationCollector())
    with pytest.raises(library_tools.LibraryToolError):
        search(query="")
