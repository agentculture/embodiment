"""The real daemon serves its own session logs as the history pane (2026-10-24)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests import test_daemon_library as lib

harness = lib.harness


def test_a_typed_turn_is_listed_as_the_live_conversation(harness: Any, tmp_path: Path) -> None:
    h = lib._build(harness, tmp_path)
    assert h.app.controls().chat("golf?")["ok"] is True
    controls = h.app.controls()
    listed = controls.conversations()["conversations"]
    assert listed, "the session log holds the turn"
    live = [c for c in listed if c["current"]]
    assert [c["title"] for c in live] == ["golf?"]
    turns = controls.conversation(live[0]["id"])["turns"]
    assert [t["role"] for t in turns][:2] == ["user", "assistant"]


def test_the_history_reads_only_this_state_dirs_sessions(harness: Any, tmp_path: Path) -> None:
    h = lib._build(harness, tmp_path)
    assert h.app.controls().conversation("../install-secret") is None


# -- continuing an earlier conversation (deviation d5) ---------------------------
#
# Operator, 2026-10-24: "I need to be able to continue them ... it's like we never
# stopped the conversation."

import json  # noqa: E402

from tests.test_daemon_library import REPLY, ModelResponse, ToolScript  # noqa: E402


def _past(h: Any, conversation: str, turns: list[tuple[str, str]]) -> Path:
    sessions = Path(h.app._state.dir) / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    log = sessions / f"{conversation}.jsonl"
    log.write_text(
        "".join(
            json.dumps({"role": r, "text": t, "ts": 1792800000.0 + i}) + "\n"
            for i, (r, t) in enumerate(turns)
        )
    )
    return log


def _texts(log: Path) -> list[str]:
    return [json.loads(line)["text"] for line in log.read_text().splitlines()]


def test_a_continued_turn_sees_the_old_turns_and_writes_to_their_log(
    harness: Any, tmp_path: Path
) -> None:
    script = ToolScript(ModelResponse(content=REPLY))
    h = lib._build(harness, tmp_path, complete=script)
    log = _past(h, "pastconv1", [("user", "the shed door is blue"), ("assistant", "noted")])
    answer = h.app.controls().chat("what colour is it?", "pastconv1")
    assert answer["ok"] is True, answer
    seen = json.dumps(script.prompts[-1], ensure_ascii=False)
    assert "the shed door is blue" in seen  # the old turns are the window
    assert _texts(log)[-2:] == ["what colour is it?", REPLY]
    live = h.app._current_session_log()
    assert live is None or "what colour is it?" not in live.read_text()


def test_an_unknown_conversation_is_refused_and_writes_nothing(
    harness: Any, tmp_path: Path
) -> None:
    h = lib._build(harness, tmp_path)
    answer = h.app.controls().chat("hello", "nosuchconversation")
    assert answer["ok"] is False
    assert answer["http_status"] == 404
    assert not (Path(h.app._state.dir) / "sessions" / "nosuchconversation.jsonl").exists()


def test_an_explicit_ask_in_a_continued_conversation_is_kept_like_any_turn(
    harness: Any, tmp_path: Path
) -> None:
    h = lib._build(harness, tmp_path)
    _past(h, "pastconv2", [("user", "hi"), ("assistant", "hello")])
    memory = h.app._memory
    before = len(getattr(memory, "remembered", []))
    h.app.controls().chat("remember that the shed key is under the mat", "pastconv2")
    assert len(getattr(memory, "remembered", [])) > before


def test_the_live_sessions_own_id_is_the_live_session(harness: Any, tmp_path: Path) -> None:
    h = lib._build(harness, tmp_path)
    assert h.app.controls().chat("first")["ok"] is True
    live = h.app._current_session_log()
    assert live is not None
    assert h.app.controls().chat("second", live.stem)["ok"] is True
    assert _texts(live)[-2] == "second"
    assert not h.app._resumed  # nothing reopened for the live one


def test_item_n_means_the_list_of_this_conversation(harness: Any, tmp_path: Path) -> None:
    from embodiment.daemon import app as app_module

    first = app_module._TextTurn(serial=1, thread=0, query="q", window=[], clock="")
    first.shown_sources = ({"title": "live paper"},)
    other = app_module._TextTurn(
        serial=2, thread=0, query="q", window=[], clock="", conversation="pastconv3"
    )
    other.shown_sources = ({"title": "old paper"},)
    recent = [first, other]
    assert app_module._previous_page_sources(recent)[0]["title"] == "live paper"
    assert app_module._previous_page_sources(recent, "pastconv3")[0]["title"] == "old paper"
