"""library.document: an item's /text as sections and paragraphs; synthetic fixtures only."""

import io
import json

import pytest

from embodiment.library import markdown
from embodiment.library.client import LibraryClient, LibraryError, LibraryItem
from embodiment.library.document import Document, Link, Section, to_document

META = LibraryItem(
    id=5, sha256="cd" * 32, kind="book", title="Synthetic", author="", format="epub",
    size=1, created_at="",
)  # fmt: skip

EPUB_TEXT = {
    "item_id": 5,
    "kind": "book",
    "format": "epub",
    "title": "Synthetic",
    "text": "ignored",
    "reason": None,
    "sections": [
        {
            "heading": "One",
            "paragraphs": ["  First, with  spaces. ", "", "Second."],
            "links": [{"text": "site", "href": "https://example.test/a", "start": 3}],
        },
        {"heading": "", "paragraphs": ["Only."], "links": []},
        {"heading": "Empty", "paragraphs": [""], "links": []},
    ],
}

MD = (
    "Preface line.\n\n# Title\n\nPara one [a link](https://example.test/x) here.\n\n"
    "- item a\n- item b\n\n## Next\n\nLast &amp; end.\n"
)


def test_epub_kept_in_order_empty_paragraphs_skipped_bytes_exact():
    doc = to_document(META, EPUB_TEXT)
    assert doc == Document(
        item_id=5,
        sha256="cd" * 32,
        title="Synthetic",
        kind="book",
        sections=(
            Section(
                "One",
                ("  First, with  spaces. ", "Second."),
                (Link("site", "https://example.test/a"),),
            ),
            Section("", ("Only.",), ()),
            Section("Empty", (), ()),
        ),
    )


def test_markdown_split_matches_extract_passages():
    meta = LibraryItem(5, "cd" * 32, "transcript", "T", "", "md", 1, "")
    body = {"item_id": 5, "kind": "transcript", "format": "md", "text": MD, "sections": None}
    doc = to_document(meta, body)
    assert [s.heading for s in doc.sections] == ["", "Title", "Next"]
    flat = [p for s in doc.sections for p in s.paragraphs]
    passages = markdown.extract(MD.encode()).passages
    assert flat == [p.text for p in passages]
    assert doc.sections[0].paragraphs == ("Preface line.",)
    assert doc.sections[1].links == (Link("a link", "https://example.test/x"),)
    assert doc.sections[2].paragraphs == ("Last & end.",)


def test_markdown_empty_text_is_no_sections():
    doc = to_document(META, {"text": "  \n", "sections": None})
    assert doc.sections == ()


@pytest.mark.parametrize("bad", [None, [], "x", {"sections": "no"}, {"sections": ["x"]}])
def test_malformed_is_a_typed_error(bad):
    with pytest.raises(LibraryError) as e:
        to_document(META, bad)
    assert e.value.code == "library_bad_response"


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, body):
        self.body, self.requests = body, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return _Resp(self.body)


def test_fetch_text_is_a_get_and_parses_json():
    op = _Opener(json.dumps(EPUB_TEXT).encode())
    got = LibraryClient("http://x.test", "k", opener=op).fetch_text(5)
    assert got == EPUB_TEXT
    assert op.requests[0].get_method() == "GET"
    assert op.requests[0].full_url.endswith("/api/library/5/text")
    for body in (b"not json", b"[1]"):
        with pytest.raises(LibraryError) as e:
            LibraryClient("http://x.test", "k", opener=_Opener(body)).fetch_text(5)
        assert e.value.code == "library_bad_response"
