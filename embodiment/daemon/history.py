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
import os
import string
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

#: The newest this many session logs are listed.
MAX_CONVERSATIONS = 60
#: At most this many turns of one conversation are returned (the newest kept).
MAX_TURNS = 400
#: A conversation's title: its first question, cut to this many characters.
TITLE_CHARS = 120
#: Turn roles the page shows; anything else in a log is left out.
ROLES = ("user", "assistant")

#: How each turn arrived, and what it is worth as evidence (operator, 2026-10-24;
#: deviation d7): "A recording is truth (that it happened, not its content). A
#: transcript is less so, as AI was doing the translation." The session log keeps
#: ``via`` on the operator's turns from d7 on; Gwen's turns are always generated.
VIA_TYPED = "typed"
VIA_SPOKEN = "spoken"
VIA_RECORDED = "recorded"
VIA_UPLOADED = "uploaded"
VIA_CLIP = "clip"
VIA_GENERATED = "generated"
#: An operator turn logged before d7, when the log did not say how it arrived.
VIA_UNKNOWN = "unknown"
#: One line per tag, carried in every export so a reader (culture-rules, the
#: library's later quality/coherence scoring) never has to guess what a tag means.
EVIDENCE = {
    VIA_TYPED: "verbatim: the operator's own words, as typed",
    VIA_SPOKEN: (
        "transcribed: the operator spoke to the room microphone; that it was said is a"
        " fact, the words are an AI transcription"
    ),
    VIA_RECORDED: (
        "transcribed: the operator recorded it on the page; that it was said is a fact,"
        " the words are an AI transcription"
    ),
    VIA_UPLOADED: (
        "transcribed: an uploaded audio file; that the file was given is a fact, who"
        " speaks in it is not known, the words are an AI transcription"
    ),
    VIA_CLIP: "transcribed: an audio clip; the words are an AI transcription",
    VIA_GENERATED: (
        "generated: Gwen's reply; a record that she said it, not evidence that it is true"
    ),
    VIA_UNKNOWN: "unknown: logged before turns recorded how they arrived",
}
_USER_VIAS = frozenset({VIA_TYPED, VIA_SPOKEN, VIA_RECORDED, VIA_UPLOADED, VIA_CLIP})
#: The words the page may send for a clip (``X-Clip-Origin``).
_CLIP_ORIGINS = frozenset({VIA_RECORDED, VIA_UPLOADED})

#: A conversation is SETTLED when nothing has been said in it for this long (d7):
#: then it is announced once, and announced again only after it is continued.
SETTLE_AFTER_S = 1800.0
#: The file that remembers which settled states were announced (0600, beside the
#: session logs' directory, never inside it so it is never listed as a conversation).
SETTLED_NAME = "conversations-settled.json"

_ID_CHARS = frozenset(string.ascii_letters + string.digits + "-_.")
_MAX_ID_CHARS = 128


def clip_via(origin: Any) -> str:
    """The ``via`` tag for a clip the page names *origin*; anything else is ``clip``."""
    return origin if isinstance(origin, str) and origin in _CLIP_ORIGINS else VIA_CLIP


def _via(row: dict[str, Any]) -> str:
    if row["role"] == "assistant":
        return VIA_GENERATED
    via = row.get("via")
    return via if via in _USER_VIAS else VIA_UNKNOWN


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
                "via": _via(row),
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


def settled(
    summaries: Iterable[dict[str, Any]],
    announced: dict[str, float],
    now: float,
    quiet_s: float = SETTLE_AFTER_S,
) -> list[dict[str, Any]]:
    """The conversations quiet for *quiet_s* whose last turn was not yet announced.

    *announced* maps an id to the ``last`` it was announced at; a continued
    conversation has a newer ``last``, so it settles — and is announced — again.
    The live conversation is no different: once quiet, it is settled too.
    """
    out = []
    for summary in summaries:
        last = summary.get("last")
        if not isinstance(last, (int, float)) or now - last < quiet_s:
            continue
        if announced.get(summary["id"], -1.0) >= last:
            continue
        out.append(summary)
    return out


class SettledLedger:
    """Which settled states were announced, kept across restarts. Never raises.

    A lost or unreadable file means every settled conversation is announced once
    more — harmless, because the export is keyed by the conversation id and the
    library item is updated, not duplicated.
    """

    def __init__(self, path: Any) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, float]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            k: float(v)
            for k, v in data.items()
            if _valid_id(k) and isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    def save(self, announced: dict[str, float]) -> bool:
        """Write atomically, 0600; keep only the newest :data:`MAX_CONVERSATIONS` x 4."""
        keep = dict(sorted(announced.items(), key=lambda kv: kv[1])[-MAX_CONVERSATIONS * 4 :])
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".settled-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(keep, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            return False
        return True
