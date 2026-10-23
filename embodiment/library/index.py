"""Gwen's document index: a private (0600) chunk store with embeddings (plan task t3).

The library's own ``q`` is a title/author filter only, so content search is built
here, on Gwen's side (c13). The index holds, per library item, its passages (the
reader's text) and its chunks (passages joined on passage boundaries up to
:data:`MAX_CHUNK_CHARS`, each with one vector from the injected ``embed``).

File layout follows :class:`embodiment.vectors.VectorCache`: one JSON file at the
path the caller passes, mode 0600, rewritten by atomic replace - once per mutating
call, or once per :meth:`DocumentIndex.batch` block, never once per chunk. The path
is never inside an eidetic store (h8): the room's memory and the library's text
have different lifetimes and different owners.

Reconcile (c33, h20): the library's catalog, keyed by (item id, sha256), is the
truth. An item the catalog dropped loses its chunks AND its stored passages; an
item whose sha256 changed is purged at once (no citation can quote the old text)
and reported ``changed`` so the caller re-fetches and re-upserts it. Search, item
and passage reads only ever answer for ids in the last reconciled catalog - an
index that was never reconciled cites nothing.

Kinds (c38): transcripts and articles are searched by default; a book is embedded
and searched only when its item id is opted in (``book_ids``), because its recall
cost is unmeasured. Nothing here expires by age - the module has no clock - so a
newspaper issue stays in the index for as long as the library holds it.

``embed`` is ``text -> list[float]``; the daemon wires the real ``gemma2-embed``
client later. An embed failure stores nothing partial and raises
:class:`IndexRefused` with a fixed code for the caller (sync) to count.

Passages are duck-typed (t2's ``epub.Passage`` or a mapping with the same names:
``anchor, heading, text, start_s, end_s, speaker``) so this module never imports
the extractor.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from embodiment import safe_reason

INDEX_VERSION = 1
#: A chunk's text is at most this many characters (passages are joined, not cut,
#: unless one passage alone is longer; then it is split on whitespace).
MAX_CHUNK_CHARS = 1000
CHUNK_SEPARATOR = "\n\n"
BOOK_KIND = "book"
#: Searched when the caller names no kinds. xteink labels newspapers and documents
#: ``article``; transcripts arrive as ``article`` EPUBs or as ``transcript``.
DEFAULT_KINDS = ("transcript", "article")
MAX_REASON_CHARS = 200
_PASSAGE_FIELDS = ("anchor", "heading", "text", "start_s", "end_s", "speaker")
_META_FIELDS = ("title", "kind", "created_at", "author")

Embed = Callable[[str], Any]


class PassageLike(Protocol):
    """What the index reads from a passage (t2's ``epub.Passage`` satisfies it)."""

    anchor: str
    heading: Optional[str]
    text: str
    start_s: Optional[float]
    end_s: Optional[float]
    speaker: Optional[str]


class IndexRefused(Exception):
    """An item the index will not store; ``code`` is fixed, ``reason`` is scrubbed."""

    def __init__(self, code: str, reason: str = "") -> None:
        super().__init__(f"{code}: {reason}" if reason else code)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Hit:
    item_id: str
    title: str
    kind: str
    created_at: Optional[str]
    anchor: str
    text: str
    score: float
    start_s: Optional[float]
    end_s: Optional[float]
    heading: Optional[str] = None


def _reason(value: object) -> str:
    return safe_reason.scrub(str(value))[:MAX_REASON_CHARS]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _item_id(obj: Any) -> Optional[str]:
    value = _field(obj, "id")
    if value is None or isinstance(value, bool) or str(value) == "":
        return None
    return str(value)


def _identity(meta: Any) -> tuple[str, str]:
    item_id, sha = _item_id(meta), _field(meta, "sha256")
    if item_id is None or not isinstance(sha, str) or not sha:
        raise IndexRefused("bad_item", "an item needs an id and a sha256")
    return item_id, sha


def _meta(obj: Any) -> dict:
    meta = {name: _field(obj, name) for name in _META_FIELDS}
    meta["title"] = str(meta["title"] or "")
    meta["kind"] = str(meta["kind"] or "")
    return meta


def _passage(obj: Any) -> dict:
    record = {name: _field(obj, name) for name in _PASSAGE_FIELDS}
    record["anchor"] = str(record["anchor"] or "")
    record["text"] = str(record["text"] or "")
    return record


def _split_long(text: str) -> list[str]:
    """Split one over-long passage on whitespace into pieces of <= MAX_CHUNK_CHARS."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    pieces: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > MAX_CHUNK_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:MAX_CHUNK_CHARS])
            word = word[MAX_CHUNK_CHARS:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) > MAX_CHUNK_CHARS:
            pieces.append(current)
            candidate = word
        current = candidate
    if current:
        pieces.append(current)
    return pieces


def _fits(chunk: Optional[dict], heading: Any, piece: str) -> bool:
    if chunk is None or chunk["heading"] != heading:
        return False
    return len(chunk["text"]) + len(CHUNK_SEPARATOR) + len(piece) <= MAX_CHUNK_CHARS


def _chunks(passages: list[dict]) -> list[dict]:
    """Join passages in order, on passage boundaries, up to the cap; a heading starts anew."""
    chunks: list[dict] = []
    current: Optional[dict] = None
    for passage in passages:
        text = passage["text"].strip()
        if not text:
            continue
        for piece in _split_long(text):
            if _fits(current, passage["heading"], piece):
                current["text"] += CHUNK_SEPARATOR + piece  # type: ignore[index]
                if passage["end_s"] is not None:
                    current["end_s"] = passage["end_s"]  # type: ignore[index]
                continue
            current = {
                "anchor": passage["anchor"],
                "heading": passage["heading"],
                "text": piece,
                "start_s": passage["start_s"],
                "end_s": passage["end_s"],
            }
            chunks.append(current)
    return chunks


def _embed_text(chunk: dict) -> str:
    heading = chunk.get("heading")
    return f"{heading}\n{chunk['text']}" if heading else chunk["text"]


def _numeric_vector(value: Any) -> Optional[list[float]]:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
        return None
    return [float(x) for x in value]


def _cosine(query: list[float], query_norm: float, vec: Any) -> Optional[float]:
    if not isinstance(vec, list) or len(vec) != len(query):
        return None
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return None
    return sum(a * b for a, b in zip(query, vec)) / (query_norm * norm)


def _valid_item(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("sha256"), str)
        and isinstance(value.get("chunks"), list)
        and isinstance(value.get("passages"), list)
        and isinstance(value.get("meta"), dict)
    )


class DocumentIndex:
    """Library chunks + vectors in one 0600 file at *path*; never raises on IO."""

    def __init__(
        self,
        path: Any,
        *,
        embed: Embed,
        model_tag: str,
        book_ids: Iterable[Any] = (),
    ) -> None:
        self.path = Path(path)
        if ".eidetic" in self.path.parts:
            raise ValueError("the document index never lives inside an eidetic store")
        self.model_tag = model_tag
        self.book_ids = frozenset(str(x) for x in book_ids)
        self.degradations: list[str] = []
        self._embed = embed
        self._lock = threading.RLock()
        self._depth = 0
        self._dirty = False
        self._items: dict[str, dict] = {}
        self._catalog: Optional[set[str]] = None
        self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            self._degrade("document index unreadable", exc)
            return
        try:
            data = json.loads(raw)
            items, catalog = data["items"], data["catalog"]
            if not isinstance(items, dict):
                raise ValueError("items is not an object")
        except (ValueError, KeyError, TypeError) as exc:
            self._degrade("document index unreadable", exc)
            return
        if isinstance(catalog, list):
            self._catalog = {str(x) for x in catalog}
        if data.get("model_tag") != self.model_tag or data.get("version") != INDEX_VERSION:
            self.degradations.append("document index built for another model; re-indexing")
            return
        self._items = {str(k): v for k, v in items.items() if _valid_item(v)}

    def _degrade(self, what: str, exc: BaseException) -> None:
        self.degradations.append(f"{what}: {_reason(safe_reason.describe_exception(exc))}")

    def _save(self) -> None:
        payload = {
            "version": INDEX_VERSION,
            "model_tag": self.model_tag,
            "catalog": sorted(self._catalog) if self._catalog is not None else None,
            "items": self._items,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                with contextlib.suppress(OSError):
                    os.remove(tmp)
                raise
            os.replace(tmp, self.path)
        except OSError as exc:
            self._degrade("document index not persisted", exc)

    def _commit(self) -> None:
        """Persist now, or at the end of the enclosing :meth:`batch`."""
        if self._depth:
            self._dirty = True
        else:
            self._save()

    @contextlib.contextmanager
    def batch(self) -> Iterator[None]:
        """Defer every write inside the block to ONE atomic save at its end.

        The lock is not held across the block, so a recall's :meth:`search`
        never waits for a sync tick's embedding.
        """
        with self._lock:
            self._depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._depth -= 1
                if not self._depth and self._dirty:
                    self._dirty = False
                    self._save()

    # -- writes -----------------------------------------------------------

    def wants(self, item_meta: Any) -> bool:
        """False for a book whose item id is not opted in; True otherwise."""
        if str(_field(item_meta, "kind") or "") != BOOK_KIND:
            return True
        return _item_id(item_meta) in self.book_ids

    def _embed_chunk(self, chunk: dict) -> list[float]:
        try:
            value = self._embed(_embed_text(chunk))
        except Exception as exc:  # noqa: BLE001  # an embedder failure is a typed refusal
            raise IndexRefused("embed_failed", _reason(safe_reason.describe_exception(exc)))
        vector = _numeric_vector(value)
        if vector is None:
            raise IndexRefused("embed_failed", "embedding is not a numeric list")
        return vector

    def upsert_item(self, item_meta: Any, passages: Iterable[Any]) -> int:
        """Index one item; return the number of chunks embedded (0 when sha256 is unchanged).

        Raises :class:`IndexRefused` (``bad_item``, ``book_not_opted_in``,
        ``embed_failed``); on a refusal the index is unchanged.
        """
        item_id, sha = _identity(item_meta)
        if not self.wants(item_meta):
            raise IndexRefused("book_not_opted_in", "books are indexed only by opt-in")
        with self._lock:
            existing = self._items.get(item_id)
            if existing is not None and existing["sha256"] == sha:
                return 0
        stored = [_passage(p) for p in passages]
        chunks = _chunks(stored)
        for chunk in chunks:
            chunk["vec"] = self._embed_chunk(chunk)
        with self._lock:
            self._items[item_id] = {
                "sha256": sha,
                "meta": _meta(item_meta),
                "passages": stored,
                "chunks": chunks,
            }
            self._commit()
        return len(chunks)

    def remove_item(self, item_id: Any) -> bool:
        """Purge one item's chunks and passages; True if it was present."""
        with self._lock:
            if self._items.pop(str(item_id), None) is None:
                return False
            self._commit()
        return True

    def reconcile(self, catalog: Iterable[Any]) -> dict[str, list[str]]:
        """Bring the index in step with the library's catalog by (item id, sha256).

        Dropped items are purged; changed items are purged and reported for
        re-indexing; unchanged items get the catalog's metadata. The catalog's ids
        become the only ids search may return.
        """
        entries: dict[str, Any] = {}
        for entry in catalog:
            item_id = _item_id(entry)
            if item_id is not None:
                entries[item_id] = entry
        with self._lock:
            removed = [i for i in self._items if i not in entries]
            changed = [
                i
                for i, entry in entries.items()
                if i in self._items and self._items[i]["sha256"] != _field(entry, "sha256")
            ]
            added = [i for i in entries if i not in self._items]
            for item_id in removed + changed:
                del self._items[item_id]
            refreshed = self._refresh_meta(entries)
            if removed or changed or refreshed or self._catalog != set(entries):
                self._catalog = set(entries)
                self._commit()
        return {"added": added, "changed": changed, "removed": removed}

    def _refresh_meta(self, entries: dict[str, Any]) -> bool:
        touched = False
        for item_id, item in self._items.items():
            meta = _meta(entries[item_id])
            if item["meta"] != meta:
                item["meta"] = meta
                touched = True
        return touched

    # -- reads ------------------------------------------------------------

    def _citable(self, item_id: str) -> bool:
        return self._catalog is not None and item_id in self._catalog

    def _kind_allowed(self, item_id: str, kind: str, kinds: frozenset) -> bool:
        if kind not in kinds:
            return False
        return kind != BOOK_KIND or item_id in self.book_ids

    def search(
        self,
        query_vector: Any,
        *,
        kinds: Optional[Iterable[str]] = None,
        limit: int = 5,
        threshold: float = 0.0,
    ) -> list[Hit]:
        """Chunks by cosine similarity, best first, ``score >= threshold``, at most *limit*.

        *kinds* defaults to :data:`DEFAULT_KINDS` (plus ``book`` when any book is
        opted in). Whatever *kinds* says, a book is returned only if its item id
        is in ``book_ids``, and any item only if the last reconciled catalog
        names it.
        """
        query = _numeric_vector(query_vector)
        if query is None or limit <= 0:
            return []
        query_norm = math.sqrt(sum(x * x for x in query))
        if query_norm == 0.0:
            return []
        allowed = frozenset(kinds) if kinds is not None else self._default_kinds()
        hits: list[Hit] = []
        with self._lock:
            for item_id, item in self._items.items():
                if self._citable(item_id) and self._kind_allowed(
                    item_id, item["meta"]["kind"], allowed
                ):
                    hits.extend(self._score_item(item_id, item, query, query_norm, threshold))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def _default_kinds(self) -> frozenset:
        kinds = set(DEFAULT_KINDS)
        if self.book_ids:
            kinds.add(BOOK_KIND)
        return frozenset(kinds)

    @staticmethod
    def _score_item(
        item_id: str, item: dict, query: list[float], query_norm: float, threshold: float
    ) -> list[Hit]:
        meta = item["meta"]
        hits = []
        for chunk in item["chunks"]:
            score = _cosine(query, query_norm, chunk.get("vec"))
            if score is None or score < threshold:
                continue
            hits.append(
                Hit(
                    item_id=item_id,
                    title=meta["title"],
                    kind=meta["kind"],
                    created_at=meta.get("created_at"),
                    anchor=chunk["anchor"],
                    text=chunk["text"],
                    score=score,
                    start_s=chunk.get("start_s"),
                    end_s=chunk.get("end_s"),
                    heading=chunk.get("heading"),
                )
            )
        return hits

    def item(self, item_id: Any) -> Optional[dict]:
        """An item's metadata and passages (for the reader), or None if not citable."""
        key = str(item_id)
        with self._lock:
            item = self._items.get(key)
            if item is None or not self._citable(key):
                return None
            return {
                "id": key,
                "sha256": item["sha256"],
                **item["meta"],
                "passages": [dict(p) for p in item["passages"]],
            }

    def read_passage(self, item_id: Any, anchor: str) -> Optional[dict]:
        """One stored passage by anchor, or None if the item or anchor is not citable."""
        item = self.item(item_id)
        if item is None:
            return None
        for passage in item["passages"]:
            if passage["anchor"] == anchor:
                return passage
        return None

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "items": len(self._items),
                "chunks": sum(len(i["chunks"]) for i in self._items.values()),
                "catalog": len(self._catalog) if self._catalog is not None else 0,
            }
