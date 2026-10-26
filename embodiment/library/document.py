"""An item's text as sections and paragraphs, from ``GET /api/library/{id}/text``.

An EPUB's ``/text`` carries ``sections`` (``heading``, ``paragraphs``, ``links``); they are
kept in order and count, empty paragraphs skipped, paragraph text byte for byte. A Markdown
item arrives as ``text`` with ``sections`` null and is split by
:func:`embodiment.library.markdown.extract`: each paragraph equals one passage, a heading
starts a new section, text before the first heading sits in a section with an empty heading.
Library text is untrusted data; nothing here cleans it beyond what ``markdown.extract`` does.
A malformed answer is a ``library_bad_response`` :class:`LibraryError`, never a traceback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from embodiment.library import markdown
from embodiment.library.client import LibraryError
from embodiment.library.epub import EpubRefused


@dataclass(frozen=True)
class Link:
    text: str
    href: str


@dataclass(frozen=True)
class Section:
    heading: str
    paragraphs: tuple[str, ...]
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class Document:
    item_id: Any
    sha256: str
    title: str
    kind: str
    sections: tuple[Section, ...]


def _bad() -> LibraryError:
    return LibraryError("library_bad_response")


def _epub_sections(raw: list) -> list[Section]:
    out: list[Section] = []
    for sec in raw:
        if not isinstance(sec, dict):
            raise _bad()
        paragraphs = sec.get("paragraphs") or []
        links = sec.get("links") or []
        if not isinstance(paragraphs, list) or not isinstance(links, list):
            raise _bad()
        out.append(
            Section(
                heading=str(sec.get("heading") or ""),
                paragraphs=tuple(p for p in paragraphs if isinstance(p, str) and p),
                links=tuple(
                    Link(str(ln.get("text") or ""), str(ln.get("href") or ""))
                    for ln in links
                    if isinstance(ln, dict) and ln.get("href")
                ),
            )
        )
    return out


def _markdown_sections(text: str) -> list[Section]:
    try:
        extracted = markdown.extract(text.encode("utf-8"))
    except (EpubRefused, UnicodeError):
        return []
    out: list[Section] = []
    heading: str | None = None
    paragraphs: list[str] = []
    links: list[Link] = []

    def close() -> None:
        if heading is not None:
            out.append(Section(heading, tuple(paragraphs), tuple(links)))

    for passage in extracted.passages:
        if passage.heading != heading:
            close()
            heading, paragraphs, links = passage.heading, [], []
        paragraphs.append(passage.text)
        links.extend(Link(t, u) for t, u in passage.links)
    close()
    return out


def to_document(meta: Any, text_json: Any) -> Document:
    """``meta`` is a :class:`LibraryItem`; ``text_json`` the parsed ``/text`` answer."""
    if not isinstance(text_json, dict):
        raise _bad()
    raw = text_json.get("sections")
    if raw is not None:
        if not isinstance(raw, list):
            raise _bad()
        sections = _epub_sections(raw)
    else:
        text = text_json.get("text")
        if text is not None and not isinstance(text, str):
            raise _bad()
        sections = _markdown_sections(text or "")
    return Document(
        item_id=meta.id,
        sha256=meta.sha256,
        title=meta.title,
        kind=meta.kind,
        sections=tuple(sections),
    )
