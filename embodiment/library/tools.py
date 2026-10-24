"""Library tools: ``search_library`` and ``read_passage``, fenced, with citations (plan task t11).

This module is a **prompt-injection boundary**. A library item is whatever someone
put in the library - an uploaded EPUB, a transcript, a newspaper - so every passage,
title, anchor and date that reaches the model is untrusted data. A passage that says
"ignore previous instructions" or "forget X" must arrive as a *quoted claim*, never
as an instruction.

How that is held, explicitly:

* **One formatter.** A tool result is produced by
  :func:`embodiment.memory.render_recalled` and by nothing here: it prefixes
  :data:`embodiment.senses_text.KNOWLEDGE_ATTRIBUTION`, fences the entries between
  memory's BEGIN/END marks, restricts every header field to a fixed charset and
  quotes every body line, all through memory's single sanitiser. This module never
  names the attribution, the marks or the quote prefix (an AST test pins that), so
  no second, weaker formatter can appear here.
* **One sanitised value for page and prompt.** Every :class:`Citation` field is
  neutralised once (:func:`embodiment.memory.neutralise_source_text`) and capped;
  the rendered entry is built *from the citation*, so the model reads exactly the
  text the chat page shows as the source.
* **Numbering is the collector's.** The web Chat maps ``[n]`` to ``sources[n-1]``.
  :func:`render_recalled` numbers its entries from 1, so each header's ``[k]`` is
  renumbered to the citation's position in the per-turn :class:`CitationCollector`
  (a citation already collected keeps its number). The rewrite is anchored at line
  start on the header shape ``[k] id=``; a body line cannot match because every
  body line begins with the quote prefix.
* **Fixed host text outside the fence.** An empty search and an unknown passage
  answer with constants; the only text after the fence is :data:`CITE_HINT`.
* **The model's arguments are validated** and bounded; a bad argument or a failed
  query embedding raises :class:`LibraryToolError` with a code declared on the
  spec, which :class:`embodiment.tools.ToolRegistry` records without the message.

What this does **not** do: stop a model from *acting* on a quoted instruction. The
fence lowers the odds; the write tools need their own guard tied to the user's turn
(plan claim c31, not this task). :attr:`CitationCollector.library_text_in_context`
exists so the daemon can tell that a turn had library text in its prompt.

No daemon edits here: the caller passes the index (anything with t3's
``search`` and ``item``), an ``embed_query`` callable (``text -> vector``) and the
turn's collector. The tools are cheap to build, so build them per turn with that
turn's collector.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Optional

from embodiment import memory
from embodiment.tools import ToolSpec

__all__ = [
    "BODY_MAX_CHARS",
    "CITE_HINT",
    "CODE_BAD_ARGUMENTS",
    "CODE_EMBED_FAILED",
    "CONVERSATION_SOURCE",
    "CODES",
    "Citation",
    "CitationCollector",
    "LIBRARY_SOURCE",
    "LIBRARY_WRITER",
    "LibraryToolError",
    "MAX_LIMIT",
    "NO_MATCHES",
    "NO_PASSAGE",
    "READ_TOOL",
    "SEARCH_TOOL",
    "SOURCE_TEXT_MAX_CHARS",
    "build_library_tools",
    "citation_record",
    "replace_source",
]

SEARCH_TOOL = "search_library"
READ_TOOL = "read_passage"

#: ``Citation.source`` for every library citation.
LIBRARY_SOURCE = "library"
#: The ``written-by`` the fence header names for a library entry.
LIBRARY_WRITER = "library"
#: ``Citation.source`` for a hit in Gwen's own past conversations (d8), and the
#: ``written-by`` its fence header names.
CONVERSATION_SOURCE = "conversation"
CONVERSATION_WRITER = "conversations"

#: Cap on a citation's text - the same cap the daemon puts on a recalled source.
SOURCE_TEXT_MAX_CHARS = memory.DEFAULT_MAX_CHARS
#: Cap on a title / kind / anchor / date / item id carried on a citation.
FIELD_MAX_CHARS = memory.PROVENANCE_TEXT_LIMIT
#: ``max_chars`` handed to render_recalled: the passage cap plus the provenance line.
BODY_MAX_CHARS = SOURCE_TEXT_MAX_CHARS + 6 * FIELD_MAX_CHARS

#: The most hits one search may return; a model's ``limit`` is clamped to [1, this].
MAX_LIMIT = 8
DEFAULT_LIMIT = 5
#: Longest query / item id / anchor accepted from the model.
MAX_QUERY_CHARS = 500
MAX_ID_CHARS = memory.HEADER_FIELD_LIMIT
MAX_ANCHOR_CHARS = 256

#: The model's argument was missing, mistyped or too long.
CODE_BAD_ARGUMENTS = "library-bad-arguments"
#: The query could not be embedded (embedder raised or returned nothing).
CODE_EMBED_FAILED = "library-embed-failed"
CODES = frozenset({CODE_BAD_ARGUMENTS, CODE_EMBED_FAILED})

#: Fixed host text: a search with no hit above the threshold.
NO_MATCHES = "No library passage matched."
#: Fixed host text: no citable passage at that item id and anchor.
NO_PASSAGE = "No citable library passage at that item_id and anchor."
#: Fixed host text after the fence: how to cite.
CITE_HINT = "\nCite a library passage you rely on as [n], using its number above."

_HEADER_NUMBER = re.compile(r"^\[(\d+)\] id=", re.MULTILINE)

_SEARCH_DESCRIPTION = (
    "Search the user's library (documents, daily newspapers, recording transcripts) "
    "by meaning. Call it BEFORE answering about news or anything he read or recorded. "
    "Cite each passage used as [n]."
)
_READ_DESCRIPTION = (
    "Read one library passage by item_id and anchor from a search result. "
    "Returns a numbered quoted passage; cite it as [n]."
)


class LibraryToolError(Exception):
    """A library tool refused a call; ``code`` is one of :data:`CODES`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Citation:
    """One library source of a reply, every text field already neutralised and capped."""

    source: str
    item_id: str
    title: str
    kind: str
    date: str
    anchor: str
    start: Optional[float]
    end: Optional[float]
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CitationCollector:
    """A turn's library citations, in the order the model was shown them.

    Position ``i`` (0-based) is the citation the model sees as ``[i+1]``, so
    :meth:`sources` is what the daemon attaches to the reply.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._citations: list[Citation] = []
        self._index: dict[tuple[str, str, str], int] = {}
        self._library_text = False

    def add(self, citation: Citation) -> int:
        """Record *citation*; return its 1-based number (a repeat keeps its number)."""
        key = (citation.item_id, citation.anchor, citation.text)
        with self._lock:
            self._library_text = True
            if key not in self._index:
                self._citations.append(citation)
                self._index[key] = len(self._citations)
            return self._index[key]

    def mark_library_text(self) -> None:
        """Say this turn's prompt holds library-derived text that is not a citation.

        The daemon calls it when the previous answer's sources are named to this
        turn, or when the window carries a reply built from library text (review of
        PR #98, 2026-10-24): the write guard must see that text as in context.
        """
        with self._lock:
            self._library_text = True

    @property
    def citations(self) -> tuple[Citation, ...]:
        with self._lock:
            return tuple(self._citations)

    @property
    def library_text_in_context(self) -> bool:
        """True once any library text was rendered into this turn's prompt."""
        with self._lock:
            return self._library_text

    def sources(self) -> list[dict[str, Any]]:
        """The citations as plain dicts, ``[n]`` at index ``n-1``."""
        return [c.to_dict() for c in self.citations]


# -- building citations ------------------------------------------------------------


def _clean(value: Any, limit: int) -> str:
    """One citation field through memory's sanitiser, on one line, capped."""
    if value is None:
        return ""
    return " ".join(memory.neutralise_source_text(value, limit).split())


def _seconds(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _citation(meta: dict[str, Any], passage: dict[str, Any]) -> Citation:
    return Citation(
        source=LIBRARY_SOURCE,
        item_id=_clean(meta.get("item_id"), MAX_ID_CHARS),
        title=_clean(meta.get("title"), FIELD_MAX_CHARS),
        kind=_clean(meta.get("kind"), FIELD_MAX_CHARS),
        date=_clean(meta.get("created_at"), FIELD_MAX_CHARS),
        anchor=_clean(passage.get("anchor"), FIELD_MAX_CHARS),
        start=_seconds(passage.get("start_s")),
        end=_seconds(passage.get("end_s")),
        text=memory.neutralise_source_text(passage.get("text") or "", SOURCE_TEXT_MAX_CHARS),
    )


def _hit_citation(hit: Any) -> Citation:
    meta = {
        "item_id": getattr(hit, "item_id", None),
        "title": getattr(hit, "title", None),
        "kind": getattr(hit, "kind", None),
        "created_at": getattr(hit, "created_at", None),
    }
    passage = {
        "anchor": getattr(hit, "anchor", None),
        "start_s": getattr(hit, "start_s", None),
        "end_s": getattr(hit, "end_s", None),
        "text": getattr(hit, "text", None),
    }
    return _citation(meta, passage)


def replace_source(citation: Citation, source: str) -> Citation:
    """*citation* from another source (a past conversation's, d8); fields unchanged."""
    return replace(citation, source=source)


def citation_record(citation: Citation) -> dict[str, Any]:
    """*citation* as the record shape :func:`embodiment.memory.render_recalled` renders.

    The provenance line and the passage both go in ``text``, so both are quoted
    body lines; only the id, the writer and the date reach the restricted header.
    """
    conversation = citation.source == CONVERSATION_SOURCE
    parts = [
        ("conversation" if conversation else "library", citation.kind),
        ("title", citation.title),
        ("date", citation.date),
        ("anchor", citation.anchor),
    ]
    if citation.start is not None:
        parts.append(("at", f"{citation.start:g}s"))
    provenance = " | ".join(f"{k}: {v}" for k, v in parts if v)
    return {
        "id": citation.item_id,
        "added_by": CONVERSATION_WRITER if conversation else LIBRARY_WRITER,
        "created": citation.date,
        "text": f"{provenance}\n{citation.text}",
    }


def _render(collector: CitationCollector, citations: list[Citation]) -> str:
    """The fenced tool result, each entry numbered by its collector position."""
    numbers = [collector.add(c) for c in citations]
    rendered = memory.render_recalled(
        [citation_record(c) for c in citations], max_chars=BODY_MAX_CHARS
    )

    def renumber(match: re.Match[str]) -> str:
        return f"[{numbers[int(match.group(1)) - 1]}] id="

    return _HEADER_NUMBER.sub(renumber, rendered) + CITE_HINT


# -- argument checks (the model's words) ---------------------------------------------


def _text_arg(value: Any, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise LibraryToolError(CODE_BAD_ARGUMENTS)
    return value.strip()


def _limit_arg(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LibraryToolError(CODE_BAD_ARGUMENTS)
    return min(MAX_LIMIT, max(1, value))


def _item_id_arg(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _text_arg(value, MAX_ID_CHARS)


# -- the tools ----------------------------------------------------------------------


class _LibraryTools:
    """The two tool callables, closed over one index, embedder and collector."""

    def __init__(
        self,
        index: Any,
        embed_query: Callable[[str], Any],
        collector: CitationCollector,
        threshold: float,
    ) -> None:
        self._index = index
        self._embed_query = embed_query
        self._collector = collector
        self._threshold = threshold

    def search_library(self, query: Any = None, limit: Any = DEFAULT_LIMIT) -> str:
        text = _text_arg(query, MAX_QUERY_CHARS)
        bounded = _limit_arg(limit)
        try:
            vector = self._embed_query(text)
        except Exception as exc:  # noqa: BLE001  # re-raised with a declared code
            raise LibraryToolError(CODE_EMBED_FAILED) from exc
        if vector is None:
            raise LibraryToolError(CODE_EMBED_FAILED)
        hits = self._index.search(vector, kinds=None, limit=bounded, threshold=self._threshold)
        if not hits:
            return NO_MATCHES
        return _render(self._collector, [_hit_citation(hit) for hit in hits])

    def read_passage(self, item_id: Any = None, anchor: Any = None) -> str:
        key = _item_id_arg(item_id)
        wanted = _text_arg(anchor, MAX_ANCHOR_CHARS)
        item = self._index.item(key)
        if not item:
            return NO_PASSAGE
        for passage in item.get("passages") or ():
            if passage.get("anchor") == wanted:
                meta = {**item, "item_id": item.get("id", key)}
                return _render(self._collector, [_citation(meta, passage)])
        return NO_PASSAGE


def build_library_tools(
    index: Any,
    embed_query: Callable[[str], Any],
    collector: CitationCollector,
    *,
    threshold: float = 0.0,
) -> tuple[ToolSpec, ToolSpec]:
    """``(search_library, read_passage)`` over *index*, recording on *collector*.

    *index* needs t3's ``search(qvec, *, kinds, limit, threshold)`` and ``item(id)``;
    both answer only for the last reconciled catalog. *threshold* is the similarity
    floor handed to ``search``; no value is calibrated for library text yet, so the
    caller chooses it.
    """
    tools = _LibraryTools(index, embed_query, collector, threshold)
    search = ToolSpec(
        name=SEARCH_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        fn=tools.search_library,
        description=_SEARCH_DESCRIPTION,
        codes=CODES,
    )
    read = ToolSpec(
        name=READ_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "anchor": {"type": "string"},
            },
            "required": ["item_id", "anchor"],
            "additionalProperties": False,
        },
        fn=tools.read_passage,
        description=_READ_DESCRIPTION,
        codes=CODES,
    )
    return search, read
