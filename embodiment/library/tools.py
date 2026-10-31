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

No daemon edits here: the caller passes the library's retrieve client, the catalog,
the ``/text`` document provider and the turn's collector (:func:`build_retrieve_tools`;
Gwen keeps no library index since embodiment#107). The tools are cheap to build, so
build them per turn with that turn's collector.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Callable, MutableMapping, Optional

from embodiment import memory
from embodiment.library.client import LibraryError
from embodiment.library.days import item_day, item_kind
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
    "BEST_PASSAGES_NOTE",
    "COULD_NOT_SEARCH",
    "build_retrieve_tools",
    "citation_record",
    "paragraph_matches",
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
#: Fixed host text after a dated search that found no item on that day: the newest day the
#: library does hold (of that kind), so "the latest newspaper" can be answered (t19 live,
#: 2026-10-31: asked for today's paper, Gwen fell back to Oct 11-17 search hits while the
#: library held 2026-10-30). The date comes from the catalog, never from the model.
NEWEST_DAY_NOTE = " The newest {what} in the library is dated {day}; search with that date."
#: Fixed host text: no citable passage at that item id and anchor.
NO_PASSAGE = "No citable library passage at that item_id and chunk_sha."
#: Fixed host text after the fence: how to cite.
CITE_HINT = "\nCite a library passage you rely on as [n], using its number above."

_HEADER_NUMBER = re.compile(r"^\[(\d+)\] id=", re.MULTILINE)

_SEARCH_DESCRIPTION = (
    "Search the user's library (documents, daily newspapers, transcripts) by meaning. "
    "Call it BEFORE answering about news or what he read or recorded. "
    "For one day, pass date. Cite as [n]."
)
#: The kinds a model may name; a reader's kind (an issue filed as an article is a
#: newspaper), not the library's raw label.
SEARCH_KINDS = ("newspaper", "transcript", "article", "document", "book")
_DATE_ARG = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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
    #: The web address of a fetched source (d: newsletter sources), else "".
    link: str = ""
    #: ``[text, url]`` links the passage carries, so the model can follow them.
    links: tuple = ()
    #: Which retrieved chunk this is (retrieve-backed citations); ``None`` otherwise.
    chunk_no: Optional[int] = None
    #: The chunk's content hash; with ``item_id`` it identifies a retrieved passage.
    chunk_sha: str = ""

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
        if citation.chunk_sha:
            key = (citation.item_id, citation.chunk_sha, "")
        else:
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


def _clean_links(value: Any) -> tuple:
    out = []
    for pair in value or ():
        try:
            text, url = pair
        except (TypeError, ValueError):
            continue
        if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
            out.append((_clean(text, FIELD_MAX_CHARS), url[:2048]))
    return tuple(out[:20])


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
        links=_clean_links(passage.get("links")),
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
        "links": getattr(hit, "links", ()),
    }
    return _citation(meta, passage)


def replace_source(citation: Citation, source: str) -> Citation:
    """*citation* from another source (a past conversation's, d8); fields unchanged."""
    return Citation(**{**asdict(citation), "source": source})


#: The links line's own budget, beyond the passage text's: whole links only, never a
#: URL cut mid-string -- a cut URL is not one the item lists, so fetch_source would
#: refuse it (review of PR #102). A link that does not fit is left out, not shortened.
LINKS_MAX_CHARS = 700


def _links_line(links: tuple) -> str:
    out: list[str] = []
    used = 0
    for text, url in links:
        entry = f"{text or 'link'} <{url}>"
        if used + len(entry) + 2 > LINKS_MAX_CHARS:
            continue
        out.append(entry)
        used += len(entry) + 2
    return "; ".join(out)


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
    if citation.link:
        provenance = (
            f"{provenance} | url: {citation.link}" if provenance else f"url: {citation.link}"
        )
    body = citation.text
    listed = _links_line(citation.links)
    if listed:
        body = f"{body}\nlinks: {listed}"
    return {
        "id": citation.item_id or "web",
        "added_by": CONVERSATION_WRITER if conversation else LIBRARY_WRITER,
        "created": citation.date,
        "text": f"{provenance}\n{body}",
    }


def _render(
    collector: CitationCollector, citations: list[Citation], max_chars: int = BODY_MAX_CHARS
) -> str:
    """The fenced tool result, each entry numbered by its collector position."""
    numbers = [collector.add(c) for c in citations]
    budget = max_chars + LINKS_MAX_CHARS + len("\nlinks: ")
    rendered = memory.render_recalled([citation_record(c) for c in citations], max_chars=budget)

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


def _date_arg(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _DATE_ARG.match(value.strip()):
        raise LibraryToolError(CODE_BAD_ARGUMENTS)
    return value.strip()


def _kind_arg(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value.strip().lower() not in SEARCH_KINDS:
        raise LibraryToolError(CODE_BAD_ARGUMENTS)
    return value.strip().lower()


def _where(day: Optional[str], kind: Optional[str]) -> Optional[Callable[[dict], bool]]:
    """An item filter for a named day and/or a reader's kind, or None for neither."""
    if day is None and kind is None:
        return None

    def keep(meta: dict) -> bool:
        if day is not None and item_day(meta) != day:
            return False
        return kind is None or item_kind(meta) == kind

    return keep


def _item_id_arg(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _text_arg(value, MAX_ID_CHARS)


# -- the tools ----------------------------------------------------------------------


# -- the retrieve-backed tools (embodiment#107 t5) ----------------------------------

#: Fixed host text: the search could not run. ``{code}`` is a declared LibraryError code.
COULD_NOT_SEARCH = "The library could not be searched ({code})."
#: Fixed host text before the fence: retrieve applies no similarity floor, so a
#: result is the library's best passages for the query and never a confirmed match.
BEST_PASSAGES_NOTE = (
    "These are the library's best passages for the query, not confirmed matches; "
    "check each one before relying on it.\n"
)
_NORMAL_SPACE = re.compile(r"\s+")


def _normal(text: str) -> str:
    return _NORMAL_SPACE.sub(" ", text).strip()


#: A retrieved chunk shorter than this matches only a paragraph equal to it (normalised).
MIN_MATCH_CHARS = 40
#: The leading / trailing characters of a longer chunk that may locate its paragraph.
EDGE_CHARS = 60


def paragraph_matches(paragraph: str, chunk: str) -> bool:
    """Does one ``/text`` paragraph belong to a retrieved *chunk*? Whitespace-normalised:
    the paragraph inside the chunk (8+ characters), the chunk inside the paragraph, or
    the chunk's first or last 60 characters inside it. The daemon's reader anchor uses
    the same test, so a citation opens where ``read_passage`` reads."""
    para = _normal(paragraph)
    wanted = _normal(chunk)
    if not para or not wanted:
        return False
    if len(wanted) < MIN_MATCH_CHARS:
        # A fragment (a title chunk, a 21-character tail) sits inside unrelated paragraphs
        # too, so it never matches by being found inside one; a paragraph wholly inside
        # the fragment still does (review of #107).
        return para == wanted or (len(para) >= 8 and para in wanted)
    return (
        (len(para) >= 8 and para in wanted)
        or wanted in para
        or (
            len(wanted) > EDGE_CHARS
            and (wanted[:EDGE_CHARS] in para or wanted[-EDGE_CHARS:] in para)
        )
    )


def _chunk_citation(passage: Any, date: Any = "") -> Citation:
    return Citation(
        source=LIBRARY_SOURCE,
        item_id=_clean(passage.item_id, MAX_ID_CHARS),
        title=_clean(passage.title, FIELD_MAX_CHARS),
        kind=_clean(passage.kind, FIELD_MAX_CHARS),
        date=_clean(date, FIELD_MAX_CHARS),
        anchor=_clean(f"chunk {passage.chunk_no}", FIELD_MAX_CHARS),
        start=None,
        end=None,
        text=memory.neutralise_source_text(passage.text or "", SOURCE_TEXT_MAX_CHARS),
        chunk_no=int(passage.chunk_no),
        chunk_sha=_clean(passage.chunk_sha, FIELD_MAX_CHARS),
    )


class _RetrieveTools:
    """``search_library`` and ``read_passage`` over a retrieve client and /text documents."""

    def __init__(
        self,
        retrieve: Any,
        catalog: Callable[[], Any],
        documents: Callable[[Any], Any],
        collector: CitationCollector,
        chunks: Optional[MutableMapping[tuple[str, str], str]] = None,
    ) -> None:
        self._retrieve = retrieve
        self._catalog = catalog
        self._documents = documents
        self._collector = collector
        #: (item_id, chunk_sha) -> the chunk's raw text, for matching /text paragraphs;
        #: the caller's (shared across turns) when given.
        self._chunks: MutableMapping[tuple[str, str], str] = chunks if chunks is not None else {}

    def _dates(self) -> dict[str, Any]:
        """``item_id -> created_at`` from the catalog: a citation's date. Never raises."""
        try:
            return {str(m.get("id")): m.get("created_at") for m in (self._catalog() or ())}
        except Exception:  # noqa: BLE001  # no catalog: the citation has no date
            return {}

    def _newest_day(self, kind: Optional[str]) -> Optional[str]:
        """The latest item day in the catalog (of *kind* when given), or None. Never raises."""
        try:
            days = [
                item_day(m) for m in (self._catalog() or ()) if kind is None or item_kind(m) == kind
            ]
        except Exception:  # noqa: BLE001  # no catalog: no hint, the plain no-match stands
            return None
        days = [d for d in days if isinstance(d, str) and _date_arg(d) is not None]
        return max(days) if days else None

    def _no_day_matches(self, kind: Optional[str]) -> str:
        newest = self._newest_day(kind)
        if newest is None:
            return NO_MATCHES
        return NO_MATCHES + NEWEST_DAY_NOTE.format(what=kind or "item", day=newest)

    def _day_ids(self, day: str, kind: Optional[str]) -> list:
        keep = _where(day, kind)
        return [m.get("id") for m in (self._catalog() or ()) if keep is None or keep(m)]

    def search_library(
        self, query: Any = None, limit: Any = DEFAULT_LIMIT, date: Any = None, kind: Any = None
    ) -> str:
        text = _text_arg(query, MAX_QUERY_CHARS)
        bounded = _limit_arg(limit)
        day = _date_arg(date)
        wanted_kind = _kind_arg(kind)
        item_ids = None
        try:
            if day is not None:
                item_ids = self._day_ids(day, wanted_kind)
                if not item_ids:
                    return self._no_day_matches(wanted_kind)
            passages = self._retrieve.retrieve(text, bounded, wanted_kind, item_ids)
        except LibraryError as exc:
            return COULD_NOT_SEARCH.format(code=getattr(exc, "code", "library_unavailable"))
        except Exception:  # noqa: BLE001  # a catalog fault must not raise into the turn
            return COULD_NOT_SEARCH.format(code="library_unavailable")
        if not passages:
            return NO_MATCHES
        citations = []
        dates = self._dates()
        for passage in passages:
            citation = _chunk_citation(passage, dates.get(str(passage.item_id), ""))
            self._chunks[(citation.item_id, citation.chunk_sha)] = str(passage.text or "")
            citations.append(citation)
        return BEST_PASSAGES_NOTE + _render(self._collector, citations)

    def read_passage(self, item_id: Any = None, chunk_sha: Any = None) -> str:
        key = _item_id_arg(item_id)
        sha = _text_arg(chunk_sha, MAX_ANCHOR_CHARS)
        chunk = self._chunks.get((key, sha))
        if chunk is None:
            return NO_PASSAGE
        try:
            document = self._documents(key)
        except LookupError:  # the item left the catalog: no citable passage there
            return NO_PASSAGE
        except LibraryError as exc:
            return COULD_NOT_SEARCH.format(code=getattr(exc, "code", "library_unavailable"))
        except Exception:  # noqa: BLE001
            return COULD_NOT_SEARCH.format(code="library_unavailable")
        found = [
            paragraph
            for section in document.sections
            for paragraph in section.paragraphs
            if paragraph_matches(paragraph, chunk)
        ]
        if not found:
            return NO_PASSAGE
        citation = Citation(
            source=LIBRARY_SOURCE,
            item_id=_clean(key, MAX_ID_CHARS),
            title=_clean(document.title, FIELD_MAX_CHARS),
            kind=_clean(document.kind, FIELD_MAX_CHARS),
            date=_clean(self._dates().get(key, ""), FIELD_MAX_CHARS),
            anchor=_clean(f"passage {sha[:12]}", FIELD_MAX_CHARS),
            start=None,
            end=None,
            text=memory.neutralise_source_text("\n".join(found), SOURCE_TEXT_MAX_CHARS),
            chunk_sha=_clean(sha, FIELD_MAX_CHARS),
        )
        return _render(self._collector, [citation])


def build_retrieve_tools(
    retrieve: Any,
    catalog: Callable[[], Any],
    documents: Callable[[Any], Any],
    collector: CitationCollector,
    *,
    chunks: Optional[MutableMapping[tuple[str, str], str]] = None,
) -> tuple[ToolSpec, ToolSpec]:
    """``(search_library, read_passage)`` over the library's retrieval endpoint.

    *retrieve* is a :class:`~embodiment.library.retrieve.RetrieveClient` (anything with
    ``retrieve(query, k, kind, item_ids)``); *catalog* returns the library's item metas
    (``id``, ``title``, ``kind``, ``created_at``), used to list a day's items; *documents*
    is ``item_id -> Document`` (:mod:`embodiment.library.document`). No similarity floor
    is applied: hybrid scores are small and the result is labelled best-passages-only.
    *chunks* (optional, ``(item_id, chunk_sha) -> text``) is where retrieved passages'
    raw text is kept; the daemon shares one bounded map across turns, so a passage the
    previous answer cited can still be read. A citation's date is its catalog item's
    ``created_at``.
    """
    tools = _RetrieveTools(retrieve, catalog, documents, collector, chunks)
    search = ToolSpec(
        name=SEARCH_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "date": {
                    "type": "string",
                    "description": (
                        "Only items about this day, YYYY-MM-DD; for 'today' or 'yesterday' "
                        "work it out from the current time you were given."
                    ),
                },
                "kind": {"type": "string", "enum": list(SEARCH_KINDS)},
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
            "properties": {"item_id": {"type": "string"}, "chunk_sha": {"type": "string"}},
            "required": ["item_id", "chunk_sha"],
            "additionalProperties": False,
        },
        fn=tools.read_passage,
        description=(
            "Read one library passage in full by item_id and chunk_sha from a search "
            "result. Returns a numbered quoted passage; cite it as [n]."
        ),
        codes=CODES,
    )
    return search, read
