"""Extractor dispatch (embodiment#105 t4): EPUB zip vs Markdown, and to-do items skipped."""

from __future__ import annotations

import pytest

from embodiment.library.client import LibraryItem
from embodiment.library.epub import EpubRefused
from embodiment.library.extract import extract_item
from embodiment.library.index import DEFAULT_KINDS, TODO_KIND, DocumentIndex
from embodiment.library.tools import SEARCH_KINDS
from tests.test_library_epub import basic

MD = b"# Title\n\nA transcript paragraph that is long enough to be searched on its own.\n"
TODO_MD = b"- [ ] buy milk <!-- todo:abc -->\n"


def test_zip_goes_to_epub_and_text_to_markdown():
    epub = extract_item(basic())
    assert [p.text for p in epub.passages][0] == "First para."
    md = extract_item(MD)
    assert [p.text for p in md.passages] == [MD.decode().split("\n\n")[1].strip()]


def test_bad_zip_refuses_as_epub_and_bad_text_as_markdown():
    with pytest.raises(EpubRefused) as zipped:
        extract_item(b"PK\x03\x04not really a zip")
    assert zipped.value.code != "bad_markdown"
    with pytest.raises(EpubRefused) as text:
        extract_item(b"\xff\xfe\x00 not utf-8 \xff")
    assert text.value.code == "bad_markdown"


def _item(i, kind, fmt):
    return LibraryItem(
        id=i, sha256=f"s{i}", kind=kind, title=f"t{i}", author="", format=fmt, size=1, created_at=""
    )


def test_wants_refuses_todo_kind(tmp_path):
    index = DocumentIndex(tmp_path / "i.json", embed=lambda t: [1.0, 0.0], model_tag="m")
    assert index.wants(_item(1, TODO_KIND, "md")) is False
    assert index.wants(_item(2, "article", "md")) is True


def test_kinds_unchanged():
    assert TODO_KIND not in DEFAULT_KINDS
    assert TODO_KIND not in SEARCH_KINDS
