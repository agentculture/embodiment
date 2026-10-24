"""Library sync: bounded ticks that keep the document index in step with the catalog.

Pure orchestration over the client (t1), extraction (t2) and index (t3). A tick
lists the catalog, reconciles, then fetches, extracts and indexes every added
and changed item until its budget runs out; the rest resumes next tick because
an unindexed item is simply ``added`` again. ``reconcile`` purges a changed item
at once, so re-fetching is this module's job.

A tick yields while the *gate* (a live turn) is held, so a recall embed is
served first. A down library is ONE recorded degradation per transition, not
per tick, and the counters freeze while it lasts.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional

from embodiment import safe_reason
from embodiment.library.client import LibraryError
from embodiment.library.epub import EpubRefused
from embodiment.library.index import IndexRefused

#: A library error with one of these codes ends the tick: nothing else would work.
_DEGRADING = frozenset({"library_unreachable", "library_unauthorized", "library_timeout"})
#: A fetch that can never succeed for this sha256 is skipped like a refused epub.
_PERMANENT = frozenset({"library_oversize"})
RECOVERED = "recovered"
POLL_S = 0.05


def _gate_held(gate: Any) -> Callable[[], bool]:
    if gate is None:
        return lambda: False
    if callable(gate):
        return lambda: bool(gate())
    return lambda: bool(gate.is_set())  # a threading.Event: set means held


def _passages(extracted: Any) -> Iterable[Any]:
    return getattr(extracted, "passages", extracted)


class LibrarySync:
    def __init__(
        self,
        client: Any,
        index: Any,
        *,
        extract: Callable[[bytes], Any],
        clock: Callable[[], float],
        budget: float,
        gate: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._index = index
        self._extract = extract
        self._clock = clock
        self._budget = budget
        self._held = _gate_held(gate)
        self._sleep = sleep
        self.degradations: list[str] = []
        self._degraded: Optional[str] = None
        self._failures: dict[str, int] = {}
        self._skipped: dict[str, str] = {}
        #: Catalog items the index does not want (books not opted in), last tick.
        self._unwanted = 0
        self._last_sync_at: Optional[float] = None
        self._frozen_gets = 0

    # -- gate -------------------------------------------------------------

    def _wait_gate(self, deadline: Optional[float] = None) -> bool:
        """Block while a live turn holds the gate; False if *deadline* passed first."""
        while self._held():
            if deadline is not None and self._clock() >= deadline:
                return False
            self._sleep(POLL_S)
        return True

    def gate_embed(self, embed: Callable[[str], Any]) -> Callable[[str], Any]:
        """Wrap the index's embedder so each chunk waits for the gate (daemon wiring)."""

        def gated(text: str) -> Any:
            self._wait_gate()
            return embed(text)

        return gated

    # -- bookkeeping ------------------------------------------------------

    def _gets(self) -> int:
        return int(getattr(self._client, "gets", 0) or 0)

    def _count(self, code: str) -> None:
        self._failures[code] = self._failures.get(code, 0) + 1

    def _fail(self, code: str) -> None:
        """Count and record a degradation only on a transition into *code*."""
        if self._degraded == code:
            return
        self._degraded = code
        self._count(code)
        self.degradations.append(code)

    def _recover(self) -> None:
        if self._degraded is not None:
            self._degraded = None
            self.degradations.append(RECOVERED)

    def status(self) -> dict:
        stats = self._index.stats()
        return {
            "configured": self._client is not None,
            "items_indexed": stats["items"],
            "chunks": stats["chunks"],
            "last_sync_at": self._last_sync_at,
            "failures_by_code": dict(self._failures),
            "gets": self._frozen_gets if self._library_down() else self._gets(),
            "skipped_books": self._unwanted,
            "skipped_refused": len(self._skipped),
        }

    def _library_down(self) -> bool:
        return self._degraded in _DEGRADING

    # -- the tick ---------------------------------------------------------

    def tick(self) -> dict:
        out = {
            "indexed": 0,
            "added": 0,
            "changed": 0,
            "removed": 0,
            "yielded": False,
            "remaining": 0,
        }
        if self._client is None:
            return out
        start = self._clock()
        deadline = start + self._budget
        if not self._wait_gate(deadline):
            out["yielded"] = True
            return out
        catalog = self._catalog()
        if catalog is None:
            return out
        diff = self._index.reconcile(catalog)
        for key in ("added", "changed", "removed"):
            out[key] = len(diff[key])
        self._forget_gone({str(i.id) for i in catalog})
        by_id = {str(i.id): i for i in catalog}
        todo = [by_id[i] for i in diff["added"] + diff["changed"] if i in by_id]
        self._last_sync_at = self._clock()
        self._work(todo, deadline, out)
        return out

    def _catalog(self) -> Optional[list]:
        before = self._gets()
        try:
            catalog = self._client.list_items()
        except LibraryError as exc:
            if not self._library_down():
                self._frozen_gets = before
            self._fail(exc.code)
            return None
        self._recover()
        return catalog

    def _forget_gone(self, ids: set[str]) -> None:
        for gone in [i for i in self._skipped if i not in ids]:
            del self._skipped[gone]

    def _work(self, todo: list, deadline: float, out: dict) -> None:
        self._unwanted = sum(1 for meta in todo if not self._index.wants(meta))
        with self._index.batch():
            for done, meta in enumerate(todo):
                if self._clock() >= deadline:
                    out["remaining"] = self._wanted_left(todo[done:])
                    break
                if not self._wait_gate(deadline):
                    out["yielded"] = True
                    out["remaining"] = self._wanted_left(todo[done:])
                    break
                if not self._index.wants(meta) or self._skipped.get(str(meta.id)) == meta.sha256:
                    continue
                result = self._one(meta)
                if result is None:
                    break
                out["indexed"] += int(result)

    def _wanted_left(self, metas: list) -> int:
        """Items a cut-short tick left for the next one (wanted, not known-skipped)."""
        return sum(
            1
            for meta in metas
            if self._index.wants(meta) and self._skipped.get(str(meta.id)) != meta.sha256
        )

    def _one(self, meta: Any) -> Optional[bool]:
        """Index one item: True indexed, False skipped, None stop the tick."""
        try:
            data = self._client.fetch_file(meta.id)
        except LibraryError as exc:
            return self._fetch_failed(meta, exc.code)
        try:
            self._index.upsert_item(meta, _passages(self._extract(data)))
        except EpubRefused as exc:
            return self._skip(meta, exc.code)
        except IndexRefused as exc:
            return self._index_refused(exc.code)
        except Exception as exc:  # noqa: BLE001 - an extractor bug must not stop the sync
            safe_reason.describe_exception(exc)
            return self._skip(meta, "extract_failed")
        self._recover()
        return True

    def _fetch_failed(self, meta: Any, code: str) -> Optional[bool]:
        if code in _PERMANENT:
            return self._skip(meta, code)
        if code in _DEGRADING and not self._library_down():
            self._frozen_gets = self._gets()
        self._fail(code)
        return None if code in _DEGRADING else False

    def _index_refused(self, code: str) -> Optional[bool]:
        if code == "embed_failed":
            self._fail(code)
            return None
        self._count(code)
        return False

    def _skip(self, meta: Any, code: str) -> bool:
        self._skipped[str(meta.id)] = meta.sha256
        self._count(code)
        return False
