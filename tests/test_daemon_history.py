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
