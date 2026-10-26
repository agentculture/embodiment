"""A ``kind=todo`` library item read into lines (embodiment#105).

culture-rules renders one Markdown checklist per recording (``checklist.render``)::

    - [ ] text <!-- todo:pocket.<recording id>.1 -->
    - [x] text <!-- todo:pocket.<recording id>.2 done_at=<ISO> by=<by> -->

A line is a to-do only if it is a checkbox line carrying a ``todo:<id>`` comment; prose,
headings and checkbox lines without an id are ignored. Stdlib only, no IO, never raises.
Markdown cleaning is injected (``clean``) so this module does not import
:mod:`embodiment.library.markdown`; the wiring passes the real cleaner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from embodiment.library.days import _ISO_DAY

#: ``pocket.<recording id>.<n>``: the recording id is a UUID, ``n`` counts from 1.
_POCKET_ID = re.compile(
    r"^pocket\.([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"\.([1-9][0-9]*)$"
)
# Linear (Sonar S8786): the checkbox prefix, then the todo comment tried at each "<!--" in turn
# (_split_line); the first one that runs to the end of the line ends the text, exactly as a
# lazy text group would, without backtracking.
_PREFIX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]")
_COMMENT = re.compile(r"<!--\s*todo:((?:(?!-->)\S)+)((?:(?!-->)[^>])*)-->\s*$")
_DONE_AT = re.compile(r"(?:^|\s)done_at=(\S+)")
_BY = re.compile(r"(?:^|\s)by=(\S+)")


@dataclass(frozen=True)
class ChecklistLine:
    id: str
    text: str
    done: bool
    done_at: Optional[float]  # epoch seconds; None when open or the stamp is malformed
    by: str  # "" when absent


@dataclass(frozen=True)
class ChecklistGroup:
    recording_id: str  # "" when no line has a pocket id
    title: str
    date: str  # ISO day from the title, "" when it has none
    items: tuple[ChecklistLine, ...]


def recording_id_of(todo_id: object) -> Optional[str]:
    """``pocket.<recording id>.<n>`` -> the recording id; anything else -> None."""
    if not isinstance(todo_id, str):
        return None
    found = _POCKET_ID.match(todo_id)
    return found.group(1) if found else None


def _epoch(stamp: str) -> Optional[float]:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _split_line(raw: str) -> Optional[tuple[str, str, str, str]]:
    """``(mark, text, id, attrs)`` of a checkbox line with a todo comment, else ``None``."""
    head = _PREFIX.match(raw)
    if not head:
        return None
    rest = raw[head.end() :]
    at = rest.find("<!--")
    while at >= 0:
        tail = _COMMENT.match(rest, at)
        if tail:
            return head.group(1), rest[:at].strip(), tail.group(1), tail.group(2)
        at = rest.find("<!--", at + 1)
    return None


def parse_checklist(
    markdown: str, clean: Callable[[str], str] = lambda text: text
) -> list[ChecklistLine]:
    """Every checkbox line with a ``todo:<id>`` comment, in order. Never raises."""
    lines: list[ChecklistLine] = []
    for raw in str(markdown or "").splitlines():
        found = _split_line(raw)
        if not found:
            continue
        mark, text, todo_id, attrs = found
        done = mark in "xX"
        stamp = _DONE_AT.search(attrs)
        by = _BY.search(attrs)
        try:
            shown = clean(text)
        except Exception:  # noqa: BLE001  # a broken cleaner must not lose the line
            shown = text
        lines.append(
            ChecklistLine(
                id=todo_id,
                text=shown,
                done=done,
                done_at=_epoch(stamp.group(1)) if done and stamp else None,
                by=by.group(1) if done and by else "",
            )
        )
    return lines


def parse_group(
    title: str, markdown: str, clean: Callable[[str], str] = lambda text: text
) -> ChecklistGroup:
    """One item as a group: title, the ISO day in it, the lines, the recording id."""
    items = tuple(parse_checklist(markdown, clean))
    recording = next((r for r in (recording_id_of(i.id) for i in items) if r), "")
    day = _ISO_DAY.search(str(title or ""))
    return ChecklistGroup(
        recording_id=recording,
        title=str(title or ""),
        date=day.group(1) if day else "",
        items=items,
    )
