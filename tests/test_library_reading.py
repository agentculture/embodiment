"""read_item and fetch_source (operator, 2026-10-24: summarize the newsletter and its
sources; fetch only links in the library)."""

from __future__ import annotations

import pytest

from embodiment import memory as mem
from embodiment.library import reading
from embodiment.library import tools as lt
from embodiment.library import web
from embodiment.library.index import DocumentIndex


def _embed(text):
    return [float(len(str(text)) % 7 + 1), 1.0]


@pytest.fixture
def index(tmp_path):
    idx = DocumentIndex(tmp_path / "i.json", embed=_embed, model_tag="m")
    meta = {"id": "5", "sha256": "s", "kind": "newspaper", "title": "Daily newspaper 2026-10-24"}
    idx.reconcile([meta])
    idx.upsert_item(
        meta,
        [
            {
                "anchor": "s1#0",
                "heading": "Jetson",
                "text": "Jetson Thor ships with more memory",
                "links": [("NVIDIA blog", "https://example.org/thor")],
            },
            {
                "anchor": "s1#1",
                "heading": "Jetson",
                "text": "A second story about edge AI",
                "links": [("Edge news", "https://example.org/edge")],
            },
        ],
    )
    return idx


def _tools(index, fetch=None):
    collector = lt.CitationCollector()
    read, fetch_spec = reading.build_reading_tools(index, collector, fetch=fetch)
    return read, fetch_spec, collector


def test_read_item_returns_every_passage_fenced_with_its_links(index):
    read, _, collector = _tools(index)
    out = read.fn(item_id="5")
    assert out.count("id=5") == 2
    assert mem.BEGIN_MARK in out
    assert mem.END_MARK in out
    assert "links: NVIDIA blog <https://example.org/thor>" in out
    assert [c.anchor for c in collector.citations] == ["s1#0", "s1#1"]
    assert collector.library_text_in_context


def test_read_item_of_an_unknown_item_is_fixed_text(index):
    read, _, _ = _tools(index)
    assert read.fn(item_id="99") == reading.NO_ITEM


def test_read_item_says_what_it_left_out(index, monkeypatch):
    monkeypatch.setattr(reading, "MAX_PASSAGES", 1)
    read, _, collector = _tools(index)
    out = read.fn(item_id="5")
    assert "1 more passages were not included" in out
    assert len(collector.citations) == 1


def test_fetch_source_reads_a_linked_page_as_a_web_citation(index):
    seen = []

    def fetch(url):
        seen.append(url)
        return web.Page(url=url, title="Thor ships", text="The full article text.")

    _, fetch_spec, collector = _tools(index, fetch)
    out = fetch_spec.fn(item_id="5", url="https://example.org/thor")
    assert seen == ["https://example.org/thor"]
    assert "The full article text." in out
    assert "url: https://example.org/thor" in out
    (source,) = collector.sources()
    assert (source["source"], source["link"], source["item_id"]) == (
        "web",
        "https://example.org/thor",
        "",
    )
    assert collector.library_text_in_context, "web text must arm the write guard"


@pytest.mark.parametrize(
    "url",
    ["https://example.org/not-listed", "http://127.0.0.1/admin", "https://example.org/thor?x=1"],
)
def test_a_url_the_item_does_not_link_is_refused_and_nothing_is_fetched(index, url):
    seen = []
    _, fetch_spec, collector = _tools(index, lambda u: seen.append(u))
    assert fetch_spec.fn(item_id="5", url=url) == reading.NOT_LINKED
    assert seen == []
    assert collector.citations == ()


def test_a_link_of_another_item_does_not_count(index):
    seen = []
    _, fetch_spec, _ = _tools(index, lambda u: seen.append(u))
    assert fetch_spec.fn(item_id="99", url="https://example.org/thor") == reading.NOT_LINKED
    assert seen == []


def test_a_refused_fetch_answers_with_its_code(index):
    def fetch(url):
        raise web.WebRefused(web.CODE_PRIVATE_ADDRESS)

    _, fetch_spec, _ = _tools(index, fetch)
    out = fetch_spec.fn(item_id="5", url="https://example.org/thor")
    assert out == reading.COULD_NOT_READ.format(code=web.CODE_PRIVATE_ADDRESS)


def test_without_a_fetcher_the_tool_says_so(index):
    _, fetch_spec, _ = _tools(index)
    assert "web-not-configured" in fetch_spec.fn(item_id="5", url="https://example.org/thor")


def test_search_hits_show_their_links_too(index):
    search, _ = lt.build_library_tools(index, _embed, lt.CitationCollector())
    out = search.fn(query="Jetson", limit=1)
    assert "links: " in out
    assert "https://example.org/" in out


def test_long_links_are_listed_whole_or_not_at_all(tmp_path):
    """Review of PR #102: a URL cut mid-string is not a link the item lists."""
    idx = DocumentIndex(tmp_path / "j.json", embed=_embed, model_tag="m")
    meta = {"id": "8", "sha256": "s", "kind": "newspaper", "title": "Daily newspaper 2026-10-24"}
    urls = [f"https://example.org/{i}/" + "x" * 200 for i in range(6)]
    idx.reconcile([meta])
    idx.upsert_item(
        meta,
        [
            {
                "anchor": "p",
                "heading": "",
                "text": "y" * 990,
                "links": [(f"link {i}", u) for i, u in enumerate(urls)],
            }
        ],
    )
    read, _, _ = _tools(idx)
    out = read.fn(item_id="8")
    listed = [u for u in urls if u in out]
    assert listed, "some links fit"
    assert "[truncated]" not in out.split("links:")[1]
    for u in urls:
        head = u[:60]
        assert (u in out) or (head not in out), "a URL is never shown cut"
