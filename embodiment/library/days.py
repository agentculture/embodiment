"""Which day, and which kind, a library item is -- as a reader would say (2026-10-24).

Used by ``search_library``'s ``date`` and ``kind`` filters. Kept out of
:mod:`embodiment.library.index`, which by design has no clock (nothing there can
expire by age); this module only converts a stored timestamp to the operator's day.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

NEWSPAPER_KIND = "newspaper"
_NEWSPAPER_WORDS = ("newspaper", "עיתון")
_ISO_DAY = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _local_zone() -> Any:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Jerusalem")
    except Exception:  # noqa: BLE001  # no tz database: UTC, the timestamps' own zone
        return timezone.utc


#: The operator's zone, the one the prompt's clock line uses (daemon CLOCK_ZONE_NAME).
LOCAL_ZONE = _local_zone()


def item_day(meta: Mapping[str, Any]) -> str:
    """The day an item is about: an ISO date in its title, else its ``created_at`` day.

    The same rule the page uses (``web/src/api/kinds.ts`` ``itemDay``): culture-rules
    titles issues "Daily newspaper YYYY-MM-DD" and transcripts "YYYY-MM-DD topic". A
    ``created_at`` timestamp is UTC; its day is taken in :data:`LOCAL_ZONE` -- the zone
    the model's "today" comes from -- so 01:00 local on the 25th is the 25th, not the
    24th (review of PR #101).
    """
    found = _ISO_DAY.search(str(meta.get("title") or ""))
    if found:
        return found.group(1)
    created = str(meta.get("created_at") or "")
    try:
        moment = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        found = _ISO_DAY.search(created)
        return found.group(1) if found else ""
    if moment.tzinfo is None:
        return moment.date().isoformat()
    return moment.astimezone(LOCAL_ZONE).date().isoformat()


def item_kind(meta: Mapping[str, Any]) -> str:
    """The kind a reader would call it: ``newspaper`` for an issue filed as an article."""
    kind = str(meta.get("kind") or "").lower()
    title = str(meta.get("title") or "").lower()
    if kind == NEWSPAPER_KIND or any(word in title for word in _NEWSPAPER_WORDS):
        return NEWSPAPER_KIND
    return kind
