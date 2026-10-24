"""Whether each to-do is done: kept by Gwen, beside the to-dos culture-rules writes (d16).

Operator, 2026-10-24: "I'd like Gwen to generate todos from transcripts, so I can check
them, or ask gwen to check them (or ask about them)"; "We can generate it as
culture-rules workflow"; "Done at Gwen, and event / document sent to culture-rules to
library". So culture-rules keeps writing each recording's action items into Gwen's
memory (``embodiment memory ingest``, record type ``action_item``), and THIS module keeps
only the state the operator changes: open or done, when, and by whom.

One 0600 JSON file under the state directory, written by atomic replace, keyed by the
to-do's memory record id. Never raises: an unreadable file means every to-do reads as
open, and a write that fails is reported to the caller. A to-do whose record is gone
simply has a state nobody reads.

Who marked it is one of :data:`BY_OPERATOR` (a checkbox on the page) or :data:`BY_GWEN`
(Gwen's ``set_todo_done`` tool, which runs only when the operator's own words in that
turn ask for it).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

TODOS_NAME = "todos-done.json"
BY_OPERATOR = "operator"
BY_GWEN = "gwen"
_BY = frozenset({BY_OPERATOR, BY_GWEN})
#: A to-do id is a memory record id: this charset, at most this long.
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


def valid_todo_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


class TodoStates:
    """Open/done per to-do id, kept across restarts. Never raises."""

    def __init__(self, path: Any, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._states: Optional[dict[str, dict[str, Any]]] = None
        self.save_failures = 0

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._states is None:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (FileNotFoundError, NotADirectoryError):
                data = {}  # nothing saved yet
            except (OSError, ValueError):
                # Unreadable: kept aside, never overwritten by the next save (review of
                # PR #103), so every earlier mark can still be recovered by hand.
                self._set_aside()
                data = {}
            self._states = {
                k: v
                for k, v in (data.items() if isinstance(data, dict) else ())
                if valid_todo_id(k) and isinstance(v, dict) and isinstance(v.get("done"), bool)
            }
        return self._states

    def _set_aside(self) -> None:
        try:
            os.replace(self._path, self._path.with_name(f"{self._path.name}.unreadable"))
        except OSError:
            self.save_failures += 1  # the next save will not land over it either way

    def get(self, todo_id: str) -> dict[str, Any]:
        """``{"done", "at", "by"}`` for *todo_id*; a never-marked one is open."""
        with self._lock:
            state = self._load().get(todo_id)
        return dict(state) if state else {"done": False, "at": None, "by": None}

    def set(self, todo_id: str, done: bool, by: str) -> Optional[dict[str, Any]]:
        """Mark *todo_id*; return its new state, or None when it could not be saved."""
        if not valid_todo_id(todo_id) or by not in _BY or not isinstance(done, bool):
            return None
        with self._lock:
            states = self._load()
            state = {"done": done, "at": self._clock(), "by": by}
            previous = states.get(todo_id)
            states[todo_id] = state
            if not self._save(states):
                if previous is None:
                    states.pop(todo_id, None)
                else:
                    states[todo_id] = previous
                return None
        return dict(state)

    def _save(self, states: dict[str, dict[str, Any]]) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".todos-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(states, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            self.save_failures += 1
            return False
        return True


# ── Gwen's two to-do tools (typed turns) ────────────────────────────────────

LIST_TOOL = "list_todos"
SET_TOOL = "set_todo_done"
CODE_BAD_ARGUMENTS = "todos-bad-arguments"
CODES = frozenset({CODE_BAD_ARGUMENTS})
NO_TODOS = "There are no to-dos that match."
NOT_ASKED = (
    "Refused: the user's own words in this turn did not ask to mark a to-do. " "Ask them first."
)
UNKNOWN_TODO = "No to-do has that id."
NOT_SAVED = "The to-do could not be marked (the state could not be saved)."
#: set_todo_done runs only when the operator's own words in the turn ASK to mark a
#: to-do -- a marking verb, or a first-person "I did it" -- never because a to-do or a
#: transcript says "mark this done" (the write guard's rule, c31, applied to this
#: write). A bare stem was too loose: "what haven't I done yet?" and "check my todos"
#: authorised writes (review of PR #103). A question never counts.
_MARK_ASKS = (
    re.compile(r"\b(?:mark|tick|check\s+off|cross\s+off|uncheck|unmark|reopen)\b", re.I),
    re.compile(r"\bi(?:'ve|\s+have)?\s+(?:done|finished|completed|did)\b", re.I),
    re.compile(r"\b(?:it'?s|that'?s|is)\s+(?:done|finished|complete)\b", re.I),
    re.compile(r"(?:^|\s)(?:ת?סמני|ת?סמן|סמנו|בטלי|בטל)(?:\s|$)"),
    re.compile(r"(?:^|\s)(?:עשיתי|סיימתי|גמרתי|ביצעתי)(?:\s|$)"),
    re.compile(r"(?:^|\s)(?:בוצע|טופל|נעשה)(?:\s|$)"),
)


class TodoToolError(Exception):
    """A to-do tool refused a call; ``code`` is one of :data:`CODES`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_LIST_DESCRIPTION = (
    "List the user's to-dos (from his recordings), with ids and whether each is done. "
    "Filter by open/done, a recording's day, or words."
)
_SET_DESCRIPTION = (
    "Mark one to-do done (or open again) by its id from list_todos. Only when the user "
    "asks you to in this turn."
)


def user_asks_to_mark(words: Any) -> bool:
    text = " ".join(str(words or "").split())
    if not text or text.rstrip().endswith("?"):
        return False
    return any(pattern.search(text) for pattern in _MARK_ASKS)


def _matches(item: dict, status: Any, day: Any, words: Any) -> bool:
    if status == "open" and item.get("done"):
        return False
    if status == "done" and not item.get("done"):
        return False
    if day and item.get("date") != day:
        return False
    if words:
        hay = f"{item.get('text', '')} {item.get('title', '')}".casefold()
        return all(w in hay for w in str(words).casefold().split())
    return True


class _TodoTools:
    def __init__(self, list_items, set_done, asked, render) -> None:
        self._list_items = list_items
        self._set_done = set_done
        self._asked = asked
        self._render = render

    def list_todos(self, status: Any = None, date: Any = None, words: Any = None) -> str:
        if status not in (None, "", "open", "done", "all"):
            raise TodoToolError(CODE_BAD_ARGUMENTS)
        items = [i for i in self._list_items() if _matches(i, status, date, words)]
        return self._render(items[:50]) if items else NO_TODOS

    def set_todo_done(self, todo_id: Any = None, done: Any = True) -> str:
        if not valid_todo_id(todo_id) or not isinstance(done, bool):
            raise TodoToolError(CODE_BAD_ARGUMENTS)
        if not self._asked():
            return NOT_ASKED
        result = self._set_done(todo_id, done)
        if result is None:
            return UNKNOWN_TODO
        if result.get("saved") is False:
            return NOT_SAVED
        return f"Marked {'done' if done else 'open'}: {todo_id}."


def build_todo_tools(
    *,
    list_items: Callable[[], list[dict]],
    set_done: Callable[[str, bool], Optional[dict]],
    asked: Callable[[], bool],
    render: Callable[[list[dict]], str],
) -> tuple[Any, Any]:
    """``(list_todos, set_todo_done)``.

    *list_items* returns the flat to-dos (id, text, title, date, done, …); *set_done*
    marks one as Gwen and returns it (None for an unknown id, or ``{"saved": False}``);
    *asked* says whether the operator's own words in this turn ask for a mark; *render*
    fences to-do text as untrusted (it is model-written, from transcripts).
    """
    from embodiment.tools import ToolSpec

    tools = _TodoTools(list_items, set_done, asked, render)
    list_spec = ToolSpec(
        name=LIST_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "all"]},
                "date": {"type": "string", "description": "A recording day, YYYY-MM-DD."},
                "words": {"type": "string", "description": "Words the to-do contains."},
            },
            "additionalProperties": False,
        },
        fn=tools.list_todos,
        description=_LIST_DESCRIPTION,
        codes=CODES,
    )
    set_spec = ToolSpec(
        name=SET_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "todo_id": {"type": "string"},
                "done": {"type": "boolean", "description": "false opens it again."},
            },
            "required": ["todo_id"],
            "additionalProperties": False,
        },
        fn=tools.set_todo_done,
        description=_SET_DESCRIPTION,
        codes=CODES,
    )
    return list_spec, set_spec
