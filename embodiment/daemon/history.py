"""Past conversations for the page's history pane, read from the session logs.

Every conversation already lands in a private, size-bounded per-session log
(``<state dir>/sessions/<safe name>.jsonl``, one ``{"role", "text", "ts"}`` line
per turn; :meth:`embodiment.daemon.state.DaemonState.open_transcript`). This
module only READS those files, so the history the page shows is the same on
every device and nothing new is written anywhere. Operator, 2026-10-24: "What
about chat history? It should be on the left pane."

Bounded and never raising: at most :data:`MAX_CONVERSATIONS` files, the newest
first; at most :data:`MAX_TURNS` turns of one conversation; a line that does not
parse is skipped; an unreadable file is left out. Conversation ids are the log
file stems, which the state module already restricts to a safe charset; an id
outside :data:`_ID_CHARS` is refused, so a request can never name a path.
"""

from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any, Optional

#: The newest this many session logs are listed.
MAX_CONVERSATIONS = 60
#: At most this many turns of one conversation are returned (the newest kept).
MAX_TURNS = 400
#: A conversation's title: its first question, cut to this many characters.
TITLE_CHARS = 120
#: Turn roles the page shows; anything else in a log is left out.
ROLES = ("user", "assistant")

_ID_CHARS = frozenset(string.ascii_letters + string.digits + "-_.")
_MAX_ID_CHARS = 128


def _valid_id(conversation_id: Any) -> bool:
    return (
        isinstance(conversation_id, str)
        and 0 < len(conversation_id) <= _MAX_ID_CHARS
        and not conversation_id.startswith(".")
        and set(conversation_id) <= _ID_CHARS
    )


def _turns(path: Path) -> list[dict[str, Any]]:
    """The user/assistant turns of one log, in order; [] if it cannot be read."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    turns: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("role") not in ROLES:
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        ts = row.get("ts")
        turns.append(
            {
                "role": row["role"],
                "text": text,
                "ts": float(ts) if isinstance(ts, (int, float)) else None,
            }
        )
    return turns


def _summary(conversation_id: str, turns: list[dict[str, Any]], current: bool) -> dict:
    first = next((t["text"] for t in turns if t["role"] == "user"), "")
    title = " ".join(first.split())
    if len(title) > TITLE_CHARS:
        title = title[: TITLE_CHARS - 1].rstrip() + "…"
    stamps = [t["ts"] for t in turns if t["ts"] is not None]
    return {
        "id": conversation_id,
        "title": title,
        "started": min(stamps) if stamps else None,
        "last": max(stamps) if stamps else None,
        "turns": sum(1 for t in turns if t["role"] == "user"),
        "current": current,
    }


class ConversationHistory:
    """Lists and reads the session logs under *sessions_dir*. Never raises."""

    def __init__(self, sessions_dir: Any, current: Optional[Any] = None) -> None:
        self._dir = Path(sessions_dir)
        #: A callable returning the live session's log path, or None.
        self._current = current

    def _current_id(self) -> Optional[str]:
        # The daemon's callable reads attributes only; were it ever to raise, the
        # route's own text-pool call records and answers it (no silent default here).
        path = self._current() if callable(self._current) else None
        return Path(path).stem if path else None

    def _logs(self) -> list[Path]:
        try:
            files = [p for p in self._dir.glob("*.jsonl") if p.is_file()]
        except OSError:
            return []
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        return files[:MAX_CONVERSATIONS]

    def conversations(self) -> dict[str, Any]:
        """``{"conversations": [...]}``, newest first, empty ones left out."""
        current = self._current_id()
        out = []
        for path in self._logs():
            turns = _turns(path)
            if any(t["role"] == "user" for t in turns):
                out.append(_summary(path.stem, turns, path.stem == current))
        out.sort(key=lambda c: c["last"] or 0.0, reverse=True)
        return {"conversations": out}

    def conversation(self, conversation_id: Any) -> Optional[dict[str, Any]]:
        """One conversation's summary and turns, or None if there is no such log."""
        if not _valid_id(conversation_id):
            return None
        path = self._dir / f"{conversation_id}.jsonl"
        if not path.is_file():
            return None
        turns = _turns(path)
        return {
            "conversation": _summary(conversation_id, turns, conversation_id == self._current_id()),
            "turns": turns[-MAX_TURNS:],
        }
