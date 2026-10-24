"""``embodiment history export`` (deviation d5): read-only, culture-rules pulls it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from embodiment.cli import main


def _log(state: Path, name: str, turns: list[tuple[str, str, float]], mtime: float) -> None:
    sessions = state / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps({"role": r, "text": t, "ts": ts}) + "\n" for r, t, ts in turns)
    )
    os.utime(path, (mtime, mtime))


@pytest.fixture
def state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    _log(state, "old", [("user", "the shed door?", 100.0), ("assistant", "blue", 101.0)], 1)
    _log(state, "new", [("user", "what is in the paper?", 500.0)], 2)
    return state


def test_export_prints_every_conversation_with_its_turns(state: Path, capsys) -> None:
    assert main(["history", "export", "--json", "--state-dir", str(state)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 2
    assert [c["id"] for c in out["conversations"]] == ["new", "old"]
    old = out["conversations"][1]
    assert [t["text"] for t in old["turns"]] == ["the shed door?", "blue"]
    assert "current" not in old


def test_since_keeps_only_newer_conversations(state: Path, capsys) -> None:
    assert main(["history", "export", "--json", "--since", "200", "--state-dir", str(state)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in out["conversations"]] == ["new"]


def test_a_bad_since_is_a_user_error(state: Path, capsys) -> None:
    assert main(["history", "export", "--since", "soon", "--state-dir", str(state)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "hint:" in err


def test_plain_text_lists_titles_never_turns(state: Path, capsys) -> None:
    assert main(["history", "export", "--state-dir", str(state)]) == 0
    out = capsys.readouterr().out
    assert "2 conversation(s)" in out
    assert "the shed door?" in out  # the title is the first question
    assert "blue" not in out  # an answer's text is not printed


def test_export_writes_nothing(state: Path, capsys) -> None:
    before = sorted((p.name, p.stat().st_mtime) for p in state.rglob("*"))
    main(["history", "export", "--json", "--state-dir", str(state)])
    assert sorted((p.name, p.stat().st_mtime) for p in state.rglob("*")) == before
