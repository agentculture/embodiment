"""Where Gwen has read to in a library item: saved positions and the next/heard machine.

The document comes from an injected *provider* (``item_id -> Document``, the shape t2
defines: ``item_id``, ``sha256``, ``title``, ``sections`` of ``heading`` and
``paragraphs``), the clock and the state file are injected too, so this module is pure
logic over one 0600 JSON file (the discipline of :mod:`embodiment.daemon.todos`).

A *unit* is one part of one paragraph of one section: a paragraph over
:data:`MAX_PART_CHARS` is served in several parts split at sentence boundaries, and the
text of every unit is the source text verbatim. The cursor names the NEXT unit to read.
``next`` serves it with an opaque position token; ``heard`` moves the cursor past a unit
only when the token names exactly the cursor's unit, so a stale or skipped-ahead report
can never move the cursor backwards or past what was served. Never raises: failures are
returned as declared codes.
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

from embodiment.daemon.text_turns import BYTES_PER_SAMPLE, MAX_SPEAK_AUDIO_BYTES, SAMPLE_RATE_HZ
from embodiment.voice import MAX_AUDIO_S_PER_CHAR

POSITIONS_NAME = "reading-positions.json"
#: A unit must fit one /api/speak answer, so it is derived from that cap (lesson 1): the
#: speak cap's seconds at the most audio a character can need. It was 4000, sized by
#: Voice.speak's text cap, and /api/speak cut such a unit at 120 s while the page reported
#: it heard (code review of #110). 120 s / 0.12 s per character = 1000.
MAX_PART_CHARS = int(
    MAX_SPEAK_AUDIO_BYTES / (SAMPLE_RATE_HZ * BYTES_PER_SAMPLE) / MAX_AUDIO_S_PER_CHAR
)

CODE_NOT_FOUND = "library-item-not-found"
CODE_GONE = "library-item-gone"
CODE_UNAVAILABLE = "library_unavailable"
CODES = frozenset({CODE_NOT_FOUND, CODE_GONE, CODE_UNAVAILABLE})

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TOKEN = re.compile(r"^([0-9a-f]{12})\.(\d{1,9})\.(\d{1,9})\.(\d{1,9})$")
_SENTENCE_END = re.compile(r"(?<=[.!?…。])\s+")


class ItemNotFound(Exception):
    """Raised by a provider: no such item was ever in the catalog."""


class ItemGone(Exception):
    """Raised by a provider: the item left the catalog."""


class ItemUnavailable(Exception):
    """Raised by a provider: the library cannot be reached right now."""


def valid_item_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def split_parts(text: str, limit: int = MAX_PART_CHARS) -> list[str]:
    """*text* as parts of at most *limit* characters; ``"".join(parts) == text``."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        pieces.append(text[start : match.end()])  # whitespace stays with the sentence
        start = match.end()
    pieces.append(text[start:])
    parts: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > limit:  # one sentence over the limit: cut at whitespace
            if current:
                parts.append(current)
                current = ""
            cut = piece.rfind(" ", 0, limit)
            cut = limit if cut <= 0 else cut + 1
            parts.append(piece[:cut])
            piece = piece[cut:]
        if len(current) + len(piece) > limit:
            parts.append(current)
            current = ""
        current += piece
    if current:
        parts.append(current)
    return parts


class ReadingPositions:
    """One cursor per item, kept across restarts. Never raises."""

    def __init__(self, path: Any, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._cursors: Optional[dict[str, dict[str, Any]]] = None
        self.save_failures = 0
        self.unreadable = 0

    @staticmethod
    def _valid(item_id: Any, cursor: Any) -> bool:
        return (
            valid_item_id(item_id)
            and isinstance(cursor, dict)
            and isinstance(cursor.get("sha256"), str)
            and _SHA.match(cursor["sha256"]) is not None
            and all(
                isinstance(cursor.get(k), int) and not isinstance(cursor.get(k), bool)
                for k in ("section", "paragraph", "part")
            )
            and min(cursor["section"], cursor["paragraph"], cursor["part"]) >= 0
            and isinstance(cursor.get("updated_at"), (int, float))
        )

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._cursors is None:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (FileNotFoundError, NotADirectoryError):
                data = {}
            except (OSError, ValueError):
                self.unreadable += 1
                self._set_aside()
                data = {}
            self._cursors = {
                k: {**v, "item_id": k}
                for k, v in (data.items() if isinstance(data, dict) else ())
                if self._valid(k, v)
            }
        return self._cursors

    def _set_aside(self) -> None:
        try:
            os.replace(self._path, self._path.with_name(f"{self._path.name}.unreadable"))
        except OSError:
            self.save_failures += 1

    def get(self, item_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            cursor = self._load().get(item_id)
            return dict(cursor) if cursor else None

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(c) for c in self._load().values()]

    def update(
        self, item_id: str, fn: Callable[[Optional[dict[str, Any]]], Optional[dict[str, Any]]]
    ) -> tuple[Optional[dict[str, Any]], bool]:
        """Replace the cursor with ``fn(current)`` under one lock; ``(cursor, saved)``.

        *fn* returns the new ``sha256/section/paragraph/part`` or None to leave it. A
        failed save leaves the in-memory cursor as it was.
        """
        if not valid_item_id(item_id):
            return None, False
        with self._lock:
            cursors = self._load()
            current = cursors.get(item_id)
            new = fn(dict(current) if current else None)
            if new is None:
                return (dict(current) if current else None), True
            cursor = {
                "item_id": item_id,
                "sha256": new["sha256"],
                "section": new["section"],
                "paragraph": new["paragraph"],
                "part": new["part"],
                "updated_at": self._clock(),
            }
            cursors[item_id] = cursor
            if not self._save(cursors):
                if current is None:
                    cursors.pop(item_id, None)
                else:
                    cursors[item_id] = current
                return (dict(current) if current else None), False
            return dict(cursor), True

    def clear(self, item_id: str) -> bool:
        """Drop the cursor; True when there is none afterwards."""
        with self._lock:
            cursors = self._load()
            current = cursors.pop(item_id, None)
            if current is not None and not self._save(cursors):
                cursors[item_id] = current
                return False
            return True

    def _save(self, cursors: dict[str, dict[str, Any]]) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".reading-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(cursors, fh)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            self.save_failures += 1
            return False
        return True


def _token(sha: str, s: int, p: int, k: int) -> str:
    return f"{sha[:12]}.{s}.{p}.{k}"


class _Walk:
    """Unit arithmetic over one document."""

    def __init__(self, doc: Any) -> None:
        self.sections = list(doc.sections)
        self._parts: dict[tuple[int, int], list[str]] = {}

    def parts(self, s: int, p: int) -> list[str]:
        if (s, p) not in self._parts:
            self._parts[(s, p)] = split_parts(self.sections[s].paragraphs[p])
        return self._parts[(s, p)]

    def first_at_or_after(self, s: int, p: int, k: int) -> Optional[tuple[int, int, int]]:
        """The first existing unit at or after (s, p, k); None past the end."""
        while s < len(self.sections):
            paragraphs = self.sections[s].paragraphs
            while p < len(paragraphs):
                if k < len(self.parts(s, p)):
                    return s, p, k
                p, k = p + 1, 0
            s, p, k = s + 1, 0, 0
        return None

    def after(self, s: int, p: int, k: int) -> Optional[tuple[int, int, int]]:
        return self.first_at_or_after(s, p, k + 1)


class Reader:
    """``next`` and ``heard`` over a provider and a :class:`ReadingPositions`."""

    def __init__(
        self,
        positions: ReadingPositions,
        provider: Callable[[str], Any],
        listed: Optional[Callable[[str], Optional[bool]]] = None,
    ) -> None:
        self._positions = positions
        self._provider = provider
        #: Whether the catalog lists an item (None: unknown, no listing yet). The listing
        #: asks it rather than fetching each item's /text (code review of #110).
        self._listed = listed
        self.reads_started = 0
        self.units_served = 0
        self.units_heard = 0
        self.heard_refused = 0
        self.continues = 0
        self.seeks = 0
        self.last_item: Optional[str] = None
        self.last_position: Optional[str] = None

    def counters(self) -> dict[str, Any]:
        return {
            "reads_started": self.reads_started,
            "units_served": self.units_served,
            "units_heard": self.units_heard,
            "heard_refused": self.heard_refused,
            "continues": self.continues,
            "seeks": self.seeks,
            "last_item": self.last_item,
            "last_position": self.last_position,
        }

    def _doc(self, item_id: str) -> tuple[Any, Optional[str]]:
        if not valid_item_id(item_id):
            return None, CODE_NOT_FOUND
        try:
            doc = self._provider(item_id)
        except ItemNotFound:
            return None, CODE_NOT_FOUND
        except ItemGone:
            return None, CODE_GONE
        except Exception:  # noqa: BLE001  # a provider fault is a degradation, not a raise
            return None, CODE_UNAVAILABLE
        if doc is None:
            return None, CODE_NOT_FOUND
        return doc, None

    @staticmethod
    def _resolve(cursor: Optional[dict], doc: Any, walk: _Walk) -> tuple[int, int, int]:
        """Where the cursor stands in *doc*; a changed sha resumes at its section's start."""
        if cursor is None:
            return 0, 0, 0
        s, p, k = cursor["section"], cursor["paragraph"], cursor["part"]
        if cursor["sha256"] != doc.sha256:
            return (s, 0, 0) if s < len(walk.sections) else (0, 0, 0)
        return s, p, k

    def next(
        self, item_id: str, section: Optional[int] = None, paragraph: Optional[int] = None
    ) -> dict[str, Any]:
        doc, error = self._doc(item_id)
        if error:
            return {"error": error, "item_id": item_id}
        walk = _Walk(doc)
        if section is not None or paragraph is not None:
            unit, refused = self._sought(walk, section, paragraph)
            if refused:
                return {"error": CODE_NOT_FOUND, "item_id": item_id}
            if unit is not None:
                self._seek(item_id, doc, unit)
        else:
            cursor = self._positions.get(item_id)
            unit = walk.first_at_or_after(*self._resolve(cursor, doc, walk))
            if cursor is None:
                self.reads_started += 1
            else:
                self.continues += 1
            self._pin(item_id, doc, walk, unit)
        self.last_item = item_id
        base = {
            "item_id": item_id,
            "sha256": doc.sha256,
            "title": doc.title,
            "section_count": len(walk.sections),
        }
        if unit is None:
            return {**base, "done": True}
        return {**base, **self._served(doc, walk, unit)}

    @staticmethod
    def _sought(
        walk: _Walk, section: Optional[int], paragraph: Optional[int]
    ) -> tuple[Optional[tuple], bool]:
        """The unit an explicit seek asks for, and whether the ask is refused."""
        want = (section or 0, paragraph or 0, 0)
        if min(want) < 0:
            return None, True
        unit = walk.first_at_or_after(*want)
        # An explicit ask for a unit that does not exist is past the end, not a jump.
        if unit is not None and (unit[0], unit[1]) != want[:2] and paragraph is not None:
            unit = None
        return unit, False

    def _served(self, doc: Any, walk: _Walk, unit: tuple) -> dict[str, Any]:
        """The fields of one served unit; counts it."""
        s, p, k = unit
        position = _token(doc.sha256, s, p, k)
        self.units_served += 1
        self.last_position = position
        return {
            "section": s,
            "heading": walk.sections[s].heading,
            "paragraph": p,
            "paragraph_count": len(walk.sections[s].paragraphs),
            "part": k,
            "part_count": len(walk.parts(s, p)),
            "text": walk.parts(s, p)[k],
            "position": position,
        }

    def _pin(self, item_id: str, doc: Any, walk: _Walk, unit: Optional[tuple]) -> None:
        """Store the cursor where ``next`` resumed (new item, or a changed sha256)."""
        target = unit if unit is not None else (len(walk.sections), 0, 0)

        def fn(current: Optional[dict]) -> Optional[dict]:
            if current is not None and current["sha256"] == doc.sha256:
                return None  # already there; only ``heard`` moves a cursor on
            return dict(zip(("section", "paragraph", "part"), target), sha256=doc.sha256)

        self._positions.update(item_id, fn)

    def _seek(self, item_id: str, doc: Any, unit: tuple) -> None:
        """An explicit seek re-pins the cursor to the sought unit (d1, approved 2026-10-26).

        The dock's previous / next paragraph and Choose section are the operator's
        own moves, forward or back; a heard report for any other unit stays refused.
        """
        target = dict(zip(("section", "paragraph", "part"), unit), sha256=doc.sha256)
        _, saved = self._positions.update(item_id, lambda _current: target)
        if saved:
            self.seeks += 1

    def heard(self, item_id: str, position: Any) -> dict[str, Any]:
        doc, error = self._doc(item_id)
        if error:
            return {"error": error, "item_id": item_id}
        match = _TOKEN.match(position) if isinstance(position, str) else None
        walk = _Walk(doc)
        targets: list[dict] = []

        def fn(current: Optional[dict]) -> Optional[dict]:
            target = self._after_heard(current, doc, walk, match)
            if target is not None:
                targets.append(target)
            return target

        cursor, saved = self._positions.update(item_id, fn)
        moved = bool(targets) and saved
        if moved:
            self.units_heard += 1
        else:
            self.heard_refused += 1
        return {"moved": moved, "cursor": cursor}

    @classmethod
    def _after_heard(
        cls, current: Optional[dict], doc: Any, walk: _Walk, match: Any
    ) -> Optional[dict]:
        """The cursor after a heard report, or None unless it names the exact next unit."""
        if match is None or match.group(1) != doc.sha256[:12]:
            return None
        at = tuple(int(match.group(i)) for i in (2, 3, 4))
        here = cls._resolve(current, doc, walk) if current else (0, 0, 0)
        unit = walk.first_at_or_after(*here)
        if unit is None or unit != at:
            return None
        nxt = walk.after(*unit)
        if nxt is None:
            nxt = (len(walk.sections), 0, 0)  # the end: the cursor stays here
        return dict(zip(("section", "paragraph", "part"), nxt), sha256=doc.sha256)

    def clear(self, item_id: str) -> dict[str, Any]:
        return {"cleared": self._positions.clear(item_id)}

    def listing(self) -> list[dict[str, Any]]:
        """Every saved position, with ``gone`` true for an item the catalog dropped."""
        out = []
        for cursor in self._positions.all():
            if self._listed is not None:
                gone = self._listed(str(cursor["item_id"])) is False
            else:
                _, error = self._doc(cursor["item_id"])
                gone = error in (CODE_GONE, CODE_NOT_FOUND)
            out.append({**cursor, "gone": gone})
        return out
