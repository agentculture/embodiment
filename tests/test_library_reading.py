"""read_item and fetch_source (operator, 2026-10-24: summarize the newsletter and its
sources; fetch only links in the library). Since embodiment#107 both read the item's
``/text`` document; the index-backed builder is gone."""

from __future__ import annotations

import pytest

from embodiment import memory as mem
from embodiment.library import reading
from embodiment.library import tools as lt
from embodiment.library import web
from embodiment.library.client import LibraryError
from embodiment.library.document import Document, Link, Section


def test_the_index_backed_builder_is_gone():
    assert not hasattr(reading, "build_reading_tools")
    assert not hasattr(reading, "_Reading")


def _documents(**by_id):
    def documents(item_id):
        if item_id not in by_id:
            raise LookupError(item_id)  # the daemon's provider: not in the catalog
        return by_id[item_id]

    return documents


def test_an_item_not_in_the_catalog_is_the_fixed_no_item_text():
    collector = lt.CitationCollector()
    read, fetch_spec = reading.build_document_reading_tools(_documents(), collector)
    assert read.fn(item_id="99") == reading.NO_ITEM
    assert fetch_spec.fn(item_id="99", url="https://example.org/thor") == reading.NO_ITEM
    assert collector.citations == ()


def test_a_link_of_another_item_does_not_count():
    seen = []
    thor = Document(
        "5", "s", "T", "newspaper", (Section("", ("x",), (Link("t", "https://example.org/t"),)),)
    )
    other = Document("6", "s", "O", "newspaper", (Section("", ("y",)),))
    _, fetch_spec = reading.build_document_reading_tools(
        _documents(**{"5": thor, "6": other}), lt.CitationCollector(), fetch=seen.append
    )
    assert fetch_spec.fn(item_id="6", url="https://example.org/t") == reading.NOT_LINKED
    assert seen == []


def test_long_links_are_listed_whole_or_not_at_all():
    """Review of PR #102: a URL cut mid-string is not a link the item lists."""
    urls = [f"https://example.org/{i}/" + "x" * 200 for i in range(6)]
    document = Document(
        "8",
        "s",
        "Daily newspaper 2026-10-24",
        "newspaper",
        (Section("", ("y" * 990,), tuple(Link(f"link {i}", u) for i, u in enumerate(urls))),),
    )
    read, _ = reading.build_document_reading_tools(
        _documents(**{"8": document}), lt.CitationCollector()
    )
    out = read.fn(item_id="8")
    listed = [u for u in urls if u in out]
    assert listed, "some links fit"
    assert "[truncated]" not in out.split("links:")[1]
    for u in urls:
        head = u[:60]
        assert (u in out) or (head not in out), "a URL is never shown cut"


def test_a_refused_fetch_answers_with_its_code_and_none_configured_says_so():
    def refuse(url):
        raise web.WebRefused(web.CODE_PRIVATE_ADDRESS)

    document = _document()
    _, fetch_spec, _ = _dtools(lambda _i: document, refuse)
    out = fetch_spec.fn(item_id="5", url="https://example.org/thor")
    assert out == reading.COULD_NOT_READ.format(code=web.CODE_PRIVATE_ADDRESS)
    _, unconfigured, _ = _dtools(lambda _i: document)
    assert "web-not-configured" in unconfigured.fn(item_id="5", url="https://example.org/thor")


def test_a_fetched_page_is_a_web_citation_that_arms_the_guard():
    def fetch(url):
        return web.Page(url=url, title="Thor ships", text="The full article text.")

    _, fetch_spec, collector = _dtools(fetch=fetch)
    out = fetch_spec.fn(item_id="5", url="https://example.org/thor")
    assert "The full article text." in out
    assert "url: https://example.org/thor" in out
    assert mem.BEGIN_MARK in out
    (source,) = collector.sources()
    assert (source["source"], source["link"], source["item_id"]) == (
        "web",
        "https://example.org/thor",
        "",
    )
    assert collector.library_text_in_context, "web text must arm the write guard"


# -- embodiment#107 t5: read_item / fetch_source on /text documents -----------------


def _document(n=3):
    return Document(
        "5",
        "s",
        "Daily newspaper",
        "newspaper",
        (
            Section(
                "Jetson",
                tuple(f"paragraph {i}" for i in range(n)),
                (Link("Thor", "https://example.org/thor"),),
            ),
            Section("Edge", ("edge story",), (Link("Edge", "https://example.org/edge"),)),
        ),
    )


def _dtools(documents=None, fetch=None):
    collector = lt.CitationCollector()
    docs = documents or (lambda _id: _document())
    read, fetch_spec = reading.build_document_reading_tools(docs, collector, fetch=fetch)
    return read, fetch_spec, collector


def test_document_read_item_returns_paragraphs_in_order():
    read, _, collector = _dtools()
    out = read.fn(item_id="5")
    for text in ("paragraph 0", "paragraph 2", "edge story"):
        assert text in out
    assert "More follows" not in out
    assert "https://example.org/thor" in out
    assert len(collector.citations) == 4


def test_document_read_item_pages_with_an_offset_and_names_the_next(monkeypatch):
    monkeypatch.setattr(reading, "MAX_PASSAGES", 2)
    read, _, _ = _dtools()
    first = read.fn(item_id="5")
    assert "paragraph 1" in first and "paragraph 2" not in first
    assert reading.MORE_FOLLOWS.format(offset=2) in first
    second = read.fn(item_id="5", offset=2)
    assert "paragraph 2" in second and "edge story" in second
    assert "More follows" not in second


def test_document_read_item_char_cap_names_the_next_offset(monkeypatch):
    monkeypatch.setattr(reading, "MAX_CHARS", 25)
    read, _, _ = _dtools()
    out = read.fn(item_id="5")
    assert reading.MORE_FOLLOWS.format(offset=2) in out


def test_offset_past_the_end_is_the_fixed_end_text():
    read, _, _ = _dtools()
    assert read.fn(item_id="5", offset=4) == reading.END_TEXT


def test_a_bad_offset_is_a_declared_fault():
    read, _, _ = _dtools()
    with pytest.raises(lt.LibraryToolError):
        read.fn(item_id="5", offset=-1)


def test_document_fault_is_fixed_text_with_its_code():
    def docs(_id):
        raise LibraryError("library_timeout")

    read, fetch_spec, _ = _dtools(docs)
    assert read.fn(item_id="5") == reading.COULD_NOT_READ.format(code="library_timeout")
    assert fetch_spec.fn(item_id="5", url="https://x.org/") == reading.COULD_NOT_READ.format(
        code="library_timeout"
    )


def test_document_fetch_source_allows_only_listed_hrefs():
    seen = []

    def fetch(url):
        seen.append(url)
        return web.Page(url=url, title="T", text="page body")

    _, fetch_spec, collector = _dtools(fetch=fetch)
    assert fetch_spec.fn(item_id="5", url="https://example.org/not-listed") == reading.NOT_LINKED
    assert seen == []
    out = fetch_spec.fn(item_id="5", url="https://example.org/edge")
    assert seen == ["https://example.org/edge"]
    assert "page body" in out
    assert collector.sources()[0]["source"] == "web"


def test_a_long_paragraph_is_read_whole_in_citation_sized_pieces():
    # Code review of #110: a paragraph past the citation cap was cut at it, and the offset
    # stepped past the whole paragraph, so following "More follows" skipped its tail.
    words = [f"w{i}" for i in range(700)]  # about 3300 characters
    long = " ".join(words)
    doc = Document("5", "s", "Book", "book", (Section("One", (long,), ()),))
    read, _, collector = _dtools(lambda _id: doc)
    out = read.fn(item_id="5")
    pieces = [c.text for c in collector.citations]
    assert all(len(p) <= lt.SOURCE_TEXT_MAX_CHARS for p in pieces)
    while reading.MORE_FOLLOWS.split("{")[0] in out:  # page on to the end
        nxt = len(collector.citations)
        out = read.fn(item_id="5", offset=nxt)
        pieces = [c.text for c in collector.citations]
    assert " ".join(pieces).split() == words


def test_a_read_item_citation_carries_the_readers_paragraph_anchor():
    read, _, collector = _dtools()
    read.fn(item_id="5")
    assert [c.anchor for c in collector.citations] == ["s0.p0", "s0.p1", "s0.p2", "s1.p0"]
