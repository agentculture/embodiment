"""Reading a whole library item, and the web pages it links to (operator, 2026-10-24).

"I want gwen to be able to summarize the newsletter for me, and even pull from sources
of the articles and summarize that." Two typed-turn tools, beside ``search_library``:

* ``read_item(item_id)`` -- an item's passages in order (a whole newspaper issue), each a
  citation, so a summary can cite every article it uses. Bounded by
  :data:`MAX_PASSAGES` and :data:`MAX_CHARS`; what was left out is said, never hidden.
* ``fetch_source(item_id, url)`` -- one web page, and only one the item LINKS to: the
  operator's choice was "only links in the library". The URL is checked against the
  item's own stored links before anything is fetched; a URL the model made up, or one
  a fetched page suggested, is refused with fixed text and fetches nothing. The fetch
  itself (:mod:`embodiment.library.web`) is public-addresses-only, GET-only, bounded.

Both results go through the library tools' one formatter (``render_recalled``), so web
text reaches the prompt exactly as library text does: quoted, attributed, fenced as
untrusted, and marking the turn's collector so the write guard (c31) refuses a
remember/forget the operator's own words did not ask for.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from embodiment import memory
from embodiment.library import tools as lt
from embodiment.library import web
from embodiment.library.client import LibraryError
from embodiment.tools import ToolSpec

READ_ITEM_TOOL = "read_item"
FETCH_TOOL = "fetch_source"
#: ``Citation.source`` for a fetched page.
WEB_SOURCE = "web"
#: At most this many passages, and this many characters of them, per read_item call.
MAX_PASSAGES = 40
MAX_CHARS = 16_000
#: How much of one fetched page reaches the prompt.
WEB_TEXT_CHARS = 8_000

NO_ITEM = "No citable library item has that item_id."
NOT_LINKED = (
    "That address is not a link in that library item. Only links the item lists can be "
    "read; read the item first to see them."
)
COULD_NOT_READ = "That source could not be read ({code})."

_READ_DESCRIPTION = (
    "Read a whole library item in order, e.g. the day's newspaper, to summarize it. "
    "Each passage is numbered; cite as [n]."
)
_FETCH_DESCRIPTION = (
    "Read one web page an item links to (its 'links:' list), to summarize an article's "
    "source. Only listed links work. Cite as [n]."
)


class _Reading:
    def __init__(
        self,
        index: Any,
        collector: lt.CitationCollector,
        fetch: Optional[Callable[[str], web.Page]],
    ) -> None:
        self._index = index
        self._collector = collector
        self._fetch = fetch
        self.fetches = 0
        self.refused = 0

    def read_item(self, item_id: Any = None) -> str:
        key = lt._item_id_arg(item_id)
        item = self._index.item(key)
        if not item:
            return NO_ITEM
        meta = {**item, "item_id": item.get("id", key)}
        citations, used, skipped = [], 0, 0
        for passage in item.get("passages") or ():
            text = str(passage.get("text") or "").strip()
            if not text:
                continue
            if len(citations) >= MAX_PASSAGES or used + len(text) > MAX_CHARS:
                skipped += 1
                continue
            used += len(text)
            citations.append(lt._citation(meta, passage))
        if not citations:
            return NO_ITEM
        rendered = lt._render(self._collector, citations)
        if skipped:
            rendered += f"\n{skipped} more passages were not included (size limit)."
        return rendered

    def fetch_source(self, item_id: Any = None, url: Any = None) -> str:
        key = lt._item_id_arg(item_id)
        wanted = lt._text_arg(url, web.MAX_URL_CHARS)
        if wanted not in self._index.links_of(key):
            self.refused += 1
            return NOT_LINKED
        if self._fetch is None:
            return COULD_NOT_READ.format(code="web-not-configured")
        self.fetches += 1
        try:
            page = self._fetch(wanted)
        except web.WebRefused as refused:
            return COULD_NOT_READ.format(code=refused.code)
        citation = lt.Citation(
            source=WEB_SOURCE,
            item_id="",
            title=lt._clean(page.title or wanted, lt.FIELD_MAX_CHARS),
            kind="web",
            date="",
            anchor="",
            start=None,
            end=None,
            text=memory.neutralise_source_text(page.text, WEB_TEXT_CHARS),
            link=page.url,
        )
        return lt._render(
            self._collector, [citation], max_chars=WEB_TEXT_CHARS + 6 * lt.FIELD_MAX_CHARS
        )


def build_reading_tools(
    index: Any,
    collector: lt.CitationCollector,
    *,
    fetch: Optional[Callable[[str], web.Page]] = None,
) -> tuple[ToolSpec, ToolSpec]:
    """``(read_item, fetch_source)`` over *index*, citing on the turn's *collector*.

    *fetch* is ``url -> Page`` (:func:`embodiment.library.web.fetch_page` in the daemon);
    without one, fetch_source answers that it is not configured.
    """
    tools = _Reading(index, collector, fetch)
    read = ToolSpec(
        name=READ_ITEM_TOOL,
        parameters={
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
            "additionalProperties": False,
        },
        fn=tools.read_item,
        description=_READ_DESCRIPTION,
        codes=lt.CODES,
    )
    fetch_spec = ToolSpec(
        name=FETCH_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "The item that lists the link."},
                "url": {"type": "string", "description": "One of that item's links, exactly."},
            },
            "required": ["item_id", "url"],
            "additionalProperties": False,
        },
        fn=tools.fetch_source,
        description=_FETCH_DESCRIPTION,
        codes=lt.CODES,
    )
    return read, fetch_spec


# -- the /text-backed tools (embodiment#107 t5) --------------------------------------

#: Fixed host text: the offset is past the item's last paragraph.
END_TEXT = "That offset is past the end of the item; there is nothing more to read."
MORE_FOLLOWS = "\nMore follows: call read_item again with offset={offset} for the next part."


class _DocumentReading:
    """read_item and fetch_source over ``documents(item_id) -> Document``."""

    def __init__(
        self,
        documents: Callable[[Any], Any],
        collector: lt.CitationCollector,
        fetch: Optional[Callable[[str], web.Page]],
    ) -> None:
        self._documents = documents
        self._collector = collector
        self._fetch = fetch
        self.fetches = 0
        self.refused = 0

    def _document(self, key: str) -> Any:
        """The document, or a fixed-text string when it cannot be had."""
        try:
            return self._documents(key)
        except LibraryError as exc:
            return COULD_NOT_READ.format(code=getattr(exc, "code", "library_unavailable"))
        except Exception:  # noqa: BLE001  # nothing raises into the turn
            return COULD_NOT_READ.format(code="library_unavailable")

    def read_item(self, item_id: Any = None, offset: Any = 0) -> str:
        key = lt._item_id_arg(item_id)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise lt.LibraryToolError(lt.CODE_BAD_ARGUMENTS)
        document = self._document(key)
        if isinstance(document, str):
            return document
        flat = [
            (section, i, paragraph)
            for section in document.sections
            for i, paragraph in enumerate(section.paragraphs)
            if paragraph.strip()
        ]
        if not flat:
            return NO_ITEM
        if offset >= len(flat):
            return END_TEXT
        citations, used, position = [], 0, offset
        while position < len(flat) and len(citations) < MAX_PASSAGES:
            section, i, paragraph = flat[position]
            if citations and used + len(paragraph) > MAX_CHARS:
                break
            used += len(paragraph)
            citations.append(
                lt.Citation(
                    source=lt.LIBRARY_SOURCE,
                    item_id=lt._clean(key, lt.MAX_ID_CHARS),
                    title=lt._clean(document.title, lt.FIELD_MAX_CHARS),
                    kind=lt._clean(document.kind, lt.FIELD_MAX_CHARS),
                    date="",
                    anchor=lt._clean(
                        f"{section.heading} #{position}" if section.heading else f"#{position}",
                        lt.FIELD_MAX_CHARS,
                    ),
                    start=None,
                    end=None,
                    text=memory.neutralise_source_text(paragraph, lt.SOURCE_TEXT_MAX_CHARS),
                    links=(
                        lt._clean_links([(ln.text, ln.href) for ln in section.links])
                        if i == 0
                        else ()
                    ),
                )
            )
            position += 1
        rendered = lt._render(self._collector, citations)
        if position < len(flat):
            rendered += MORE_FOLLOWS.format(offset=position)
        return rendered

    def fetch_source(self, item_id: Any = None, url: Any = None) -> str:
        key = lt._item_id_arg(item_id)
        wanted = lt._text_arg(url, web.MAX_URL_CHARS)
        document = self._document(key)
        if isinstance(document, str):
            return document
        listed = {ln.href for section in document.sections for ln in section.links}
        if wanted not in listed:
            self.refused += 1
            return NOT_LINKED
        if self._fetch is None:
            return COULD_NOT_READ.format(code="web-not-configured")
        self.fetches += 1
        try:
            page = self._fetch(wanted)
        except web.WebRefused as refused:
            return COULD_NOT_READ.format(code=refused.code)
        citation = lt.Citation(
            source=WEB_SOURCE,
            item_id="",
            title=lt._clean(page.title or wanted, lt.FIELD_MAX_CHARS),
            kind="web",
            date="",
            anchor="",
            start=None,
            end=None,
            text=memory.neutralise_source_text(page.text, WEB_TEXT_CHARS),
            link=page.url,
        )
        return lt._render(
            self._collector, [citation], max_chars=WEB_TEXT_CHARS + 6 * lt.FIELD_MAX_CHARS
        )


def build_document_reading_tools(
    documents: Callable[[Any], Any],
    collector: lt.CitationCollector,
    *,
    fetch: Optional[Callable[[str], web.Page]] = None,
) -> tuple[ToolSpec, ToolSpec]:
    """``(read_item, fetch_source)`` over ``documents(item_id) -> Document``.

    *documents* is the t2 document provider (``/text`` sections and paragraphs); a
    LibraryError from it is a fixed fault text. *fetch* is ``url -> Page``.
    """
    tools = _DocumentReading(documents, collector, fetch)
    read = ToolSpec(
        name=READ_ITEM_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Paragraph to start from; use the offset a previous read named.",
                },
            },
            "required": ["item_id"],
            "additionalProperties": False,
        },
        fn=tools.read_item,
        description=_READ_DESCRIPTION,
        codes=lt.CODES,
    )
    fetch_spec = ToolSpec(
        name=FETCH_TOOL,
        parameters={
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "The item that lists the link."},
                "url": {"type": "string", "description": "One of that item's links, exactly."},
            },
            "required": ["item_id", "url"],
            "additionalProperties": False,
        },
        fn=tools.fetch_source,
        description=_FETCH_DESCRIPTION,
        codes=lt.CODES,
    )
    return read, fetch_spec
