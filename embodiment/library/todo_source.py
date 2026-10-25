"""Library to-do source: the ``kind=todo`` items, re-read only when their sha256 changes.

Pure orchestration over the GET-only client: ``list_items(kind="todo")`` and
``fetch_file``, nothing else. The parser is injected (``parse(meta, data)``) so
this module knows nothing of the checklist format. No thread and no schedule
here: ``check()`` does ONE pass when the daemon calls it, ``groups()`` returns
what the last good passes hold.

A failed list or fetch never empties the page: the last good groups stay, the
source reports ``available=False`` with the failure's code, and the counters move.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from embodiment import safe_reason
from embodiment.library.client import LibraryError

KIND = "todo"
UNAVAILABLE = "library_unavailable"
PARSE_FAILED = "todo_parse_failed"


class TodoSource:
    def __init__(
        self,
        client: Any,
        *,
        parse: Callable[[Any, bytes], list],
        clock: Callable[[], float],
    ) -> None:
        self._client = client
        self._parse = parse
        self._clock = clock
        #: item id -> (meta, parsed lines); the meta carries the sha256 they came from.
        self._held: dict[str, tuple[Any, list]] = {}
        self._available = client is not None
        self._code: Optional[str] = None if client is not None else UNAVAILABLE
        self._checks = 0
        self._failures = 0
        self._failures_by_code: dict[str, int] = {}
        self._last_check_at: Optional[float] = None

    def _fail(self, code: str) -> None:
        self._failures += 1
        self._failures_by_code[code] = self._failures_by_code.get(code, 0) + 1

    def check(self) -> dict:
        """One pass: list, re-read what changed, drop what left. Never raises."""
        if self._client is None:
            return self.status()
        self._checks += 1
        self._last_check_at = self._clock()
        try:
            catalog = self._client.list_items(kind=KIND)
        except LibraryError as exc:
            self._fail(exc.code)
            self._available, self._code = False, exc.code
            return self.status()
        except Exception as exc:  # noqa: BLE001 - a client bug must not stop the daemon
            safe_reason.describe_exception(exc)
            self._fail(UNAVAILABLE)
            self._available, self._code = False, UNAVAILABLE
            return self.status()
        wanted = {str(m.id): m for m in catalog if getattr(m, "kind", KIND) == KIND}
        for gone in [i for i in self._held if i not in wanted]:
            del self._held[gone]
        code: Optional[str] = None
        for item_id, meta in wanted.items():
            held = self._held.get(item_id)
            if held is not None and held[0].sha256 == meta.sha256:
                continue
            code = self._reread(item_id, meta) or code
        self._available, self._code = code is None, code
        return self.status()

    def _reread(self, item_id: str, meta: Any) -> Optional[str]:
        """Fetch and parse one item; on failure keep its last good version."""
        try:
            data = self._client.fetch_file(meta.id)
        except LibraryError as exc:
            self._fail(exc.code)
            return exc.code
        except Exception as exc:  # noqa: BLE001
            safe_reason.describe_exception(exc)
            self._fail(UNAVAILABLE)
            return UNAVAILABLE
        try:
            lines = list(self._parse(meta, data))
        except Exception as exc:  # noqa: BLE001 - a parser bug must not stop the source
            safe_reason.describe_exception(exc)
            self._fail(PARSE_FAILED)
            return PARSE_FAILED
        self._held[item_id] = (meta, lines)
        return None

    def groups(self) -> list[dict]:
        """The held items, newest first (created_at, then title, then id); stable."""
        ordered = sorted(
            self._held.items(),
            key=lambda kv: (kv[1][0].created_at, str(kv[1][0].title), kv[0]),
            reverse=True,
        )
        return [
            {
                "id": item_id,
                "title": meta.title,
                "created_at": meta.created_at,
                "sha256": meta.sha256,
                "lines": list(lines),
            }
            for item_id, (meta, lines) in ordered
        ]

    def status(self) -> dict:
        return {
            "configured": self._client is not None,
            "available": self._available,
            "code": self._code,
            "items": len(self._held),
            "lines": sum(len(lines) for _, lines in self._held.values()),
            "checks": self._checks,
            "failures": self._failures,
            "failures_by_code": dict(self._failures_by_code),
            "last_check_at": self._last_check_at,
        }
