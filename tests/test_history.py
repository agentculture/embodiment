"""The history pane's reads (operator, 2026-10-24: chat history on the left pane).

`embodiment.daemon.history` only reads the per-session logs the daemon already
writes; these tests use synthetic logs in a tmp dir, never a real store.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from embodiment.daemon import history as h
from embodiment.daemon.history import ConversationHistory


def _log(dir_: Path, name: str, rows: list[dict], mtime: float) -> Path:
    path = dir_ / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def _turn(role: str, text: str, ts: float) -> dict:
    return {"role": role, "text": text, "ts": ts}


def test_lists_conversations_newest_first_titled_by_the_first_question(tmp_path):
    _log(
        tmp_path, "aaa", [_turn("user", "מה במחסן?", 10.0), _turn("assistant", "קומקום", 11.0)], 100
    )
    _log(tmp_path, "bbb", [_turn("user", "What's in the paper?", 50.0)], 200)
    listed = ConversationHistory(tmp_path).conversations()["conversations"]
    assert [c["id"] for c in listed] == ["bbb", "aaa"]
    assert listed[1]["title"] == "מה במחסן?"
    assert (listed[1]["started"], listed[1]["last"], listed[1]["turns"]) == (10.0, 11.0, 1)


def test_marks_the_live_session_and_skips_logs_with_no_question(tmp_path):
    live = _log(tmp_path, "live", [_turn("user", "hi", 1.0)], 100)
    _log(tmp_path, "empty", [_turn("assistant", "hello", 2.0)], 200)
    listed = ConversationHistory(tmp_path, current=lambda: live).conversations()["conversations"]
    assert [(c["id"], c["current"]) for c in listed] == [("live", True)]


def test_reads_one_conversation_and_skips_lines_it_cannot_use(tmp_path):
    path = tmp_path / "ccc.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(_turn("user", "q", 1.0)),
                "not json",
                json.dumps({"role": "system", "text": "x", "ts": 2.0}),
                json.dumps(_turn("assistant", "   ", 3.0)),
                json.dumps(_turn("assistant", "a [1]", 4.0)),
            ]
        )
    )
    got = ConversationHistory(tmp_path).conversation("ccc")
    assert got is not None
    assert [(t["role"], t["text"]) for t in got["turns"]] == [("user", "q"), ("assistant", "a [1]")]


def test_an_id_that_could_name_a_path_is_refused(tmp_path):
    (tmp_path / "secret.jsonl").write_text(json.dumps(_turn("user", "x", 1.0)))
    history = ConversationHistory(tmp_path / "sessions")
    for bad in ("../secret", "..", ".hidden", "a/b", "", "x" * 200, None, 7):
        assert history.conversation(bad) is None


def test_bounds_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "MAX_CONVERSATIONS", 2)
    monkeypatch.setattr(h, "MAX_TURNS", 3)
    for i in range(4):
        _log(tmp_path, f"s{i}", [_turn("user", f"q{j}", float(j)) for j in range(5)], 100 + i)
    history = ConversationHistory(tmp_path)
    assert len(history.conversations()["conversations"]) == 2
    turns = history.conversation("s0")["turns"]
    assert [t["text"] for t in turns] == ["q2", "q3", "q4"]  # the newest kept


def test_a_long_first_question_is_cut_for_the_title(tmp_path):
    _log(tmp_path, "long", [_turn("user", "word " * 100, 1.0)], 100)
    title = ConversationHistory(tmp_path).conversations()["conversations"][0]["title"]
    assert len(title) == h.TITLE_CHARS
    assert title.endswith("…")


def test_a_missing_directory_is_an_empty_history(tmp_path):
    history = ConversationHistory(tmp_path / "none")
    assert history.conversations() == {"conversations": []}
    assert history.conversation("abc") is None


# ── d7: how each turn arrived, and when a conversation has settled ───────────


def test_turns_carry_how_they_arrived(tmp_path):
    rows = [
        {**_turn("user", "typed one", 1.0), "via": "typed"},
        _turn("assistant", "an answer", 2.0),
        {**_turn("user", "said aloud", 3.0), "via": "spoken"},
        _turn("user", "from before d7", 4.0),
        {**_turn("user", "forged tag", 5.0), "via": "gospel"},
    ]
    _log(tmp_path, "c", rows, 1)
    turns = ConversationHistory(tmp_path).conversation("c")["turns"]
    assert [t["via"] for t in turns] == ["typed", "generated", "spoken", "unknown", "unknown"]


def test_every_tag_has_an_evidence_line():
    vias = {h.VIA_TYPED, h.VIA_SPOKEN, h.VIA_RECORDED, h.VIA_UPLOADED, h.VIA_CLIP}
    assert vias | {h.VIA_GENERATED, h.VIA_UNKNOWN} == set(h.EVIDENCE)
    assert h.EVIDENCE[h.VIA_TYPED].startswith("verbatim")
    assert h.EVIDENCE[h.VIA_GENERATED].startswith("generated")
    assert all(h.EVIDENCE[v].startswith("transcribed") for v in vias - {h.VIA_TYPED})


def test_clip_via_accepts_only_the_page_words():
    assert h.clip_via("recorded") == "recorded"
    assert h.clip_via("uploaded") == "uploaded"
    assert h.clip_via("typed") == "clip"
    assert h.clip_via(None) == "clip"


def _summary(id_: str, last):
    return {"id": id_, "last": last, "turns": 1}


def test_settled_waits_for_the_quiet_period_and_announces_once():
    quiet, fresh = _summary("quiet", 1000.0), _summary("fresh", 2500.0)
    now = 1000.0 + h.SETTLE_AFTER_S
    assert [s["id"] for s in h.settled([quiet, fresh], {}, now)] == ["quiet"]
    assert h.settled([quiet], {"quiet": 1000.0}, now) == []


def test_a_continued_conversation_settles_again():
    continued = _summary("c", 5000.0)
    assert h.settled([continued], {"c": 1000.0}, 5000.0 + h.SETTLE_AFTER_S) == [continued]


def test_settled_skips_a_conversation_with_no_times():
    assert h.settled([_summary("c", None)], {}, 1e12) == []


def test_the_settled_ledger_round_trips_privately(tmp_path):
    ledger = h.SettledLedger(tmp_path / "s.json")
    assert ledger.load() == {}
    assert ledger.save({"a": 1.0, "../x": 2.0}) is True
    assert ledger.load() == {"a": 1.0}
    assert (tmp_path / "s.json").stat().st_mode & 0o777 == 0o600


def test_an_unreadable_ledger_means_announce_again(tmp_path):
    (tmp_path / "s.json").write_text("not json")
    assert h.SettledLedger(tmp_path / "s.json").load() == {}
