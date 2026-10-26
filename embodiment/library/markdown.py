"""Bounded Markdown text extraction with stable passage anchors.

Stdlib only (``re``, ``html``). The Markdown twin of :mod:`embodiment.library.epub`: it
yields the same :class:`Extracted` / :class:`Passage` shape, so the index and the reader
work unchanged, and it refuses with the same :class:`EpubRefused` (a fixed ``code``), so
the sync needs no new ``except`` clause.

culture-rules writes library items as Markdown (a transcript: an H1 title, a summary,
``## Key facts``, ``## Transcript``; a to-do list: ``- [ ] text <!-- todo:... -->``).
Headings set the heading of the passages under them and are not passages themselves;
each paragraph or list item is one passage. HTML comments are dropped, tags are stripped
(a ``<div dir="rtl">`` wrapper leaves its text), entities are unescaped, and
``[text](http...)`` links are kept as ``links``. Caps cut rather than raise: an over-cap
item still yields bounded passages. Text is returned as found; fencing happens at render
time, not here.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

from embodiment.library.epub import (
    MAX_LINK_TEXT,
    MAX_LINK_URL,
    MAX_LINKS,
    EpubRefused,
    Extracted,
    Passage,
)

# No regex can be linear and clear here (Sonar S6019/S8786): a comment, to the first "-->"
# or to the end, is stripped by a scan (_strip_comments), and a heading's closing run of "#"
# in code (_heading_text).
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
# One separator, not [ \t]+: the text is normalised after; overlapping runs backtrack.
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t](.*)$")
_BULLET = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+")
_LINK = re.compile(r"(!?)\[([^\[\]]{0,%d})\]\(([^()\s]+)(?:\s+\"[^\"]*\")?\)" % MAX_LINK_TEXT)


def _strip_comments(text: str) -> str:
    """*text* without its HTML comments: each runs to the first "-->", or to the end."""
    out, at = [], 0
    while True:
        start = text.find("<!--", at)
        if start < 0:
            out.append(text[at:])
            return "".join(out)
        out.append(text[at:start])
        end = text.find("-->", start + 4)
        if end < 0:
            return "".join(out)
        at = end + 3


def _heading_text(rest: str) -> str:
    """A heading's text without its optional closing run of "#" (CommonMark: the run must
    follow a space or tab, or be all there is)."""
    text = rest.rstrip(" \t")
    bare = text.rstrip("#")
    if bare != text and (not bare or bare[-1] in " \t"):
        text = bare.rstrip(" \t")
    return text


@dataclass(frozen=True)
class Limits:
    max_line_chars: int = 2000
    max_lines: int = 20_000
    max_chars: int = 4_000_000
    max_passage_chars: int = 4000


def _inline(line: str, links: list[tuple[str, str]]) -> str:
    """Resolve links (kept, capped), drop images, strip tags, unescape entities."""

    def sub(m: re.Match) -> str:
        text, url = m.group(2), m.group(3)
        if m.group(1):  # an image: no text worth indexing
            return ""
        if url.lower().startswith(("http://", "https://")) and len(url) <= MAX_LINK_URL:
            if len(links) < MAX_LINKS:
                links.append((html.unescape(_TAG.sub("", text)).strip(), url))
        return text

    return html.unescape(_TAG.sub("", _LINK.sub(sub, line)))


def clean_text(text: str, *, limits: Optional[Limits] = None) -> str:
    """One line of library Markdown as plain text: the cleaner a checklist line needs.

    Comments dropped, links reduced to their text, images dropped, tags stripped,
    entities unescaped, whitespace collapsed, capped at ``max_line_chars``. Never raises.
    """
    limits = limits if limits is not None else Limits()
    line = str(text or "")[: limits.max_line_chars]
    return " ".join(_inline(_strip_comments(line), []).split())[: limits.max_line_chars]


def extract(data: bytes, *, limits: Optional[Limits] = None) -> Extracted:
    """Extract passages in document order, or raise :class:`EpubRefused`.

    Same one-argument seam as :func:`embodiment.library.epub.extract`.
    """
    limits = limits if limits is not None else Limits()
    try:
        source = data.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        raise EpubRefused("bad_markdown") from None
    if not source.strip():
        raise EpubRefused("bad_markdown")
    lines = [ln[: limits.max_line_chars] for ln in source.splitlines()[: limits.max_lines]]
    lines = _strip_comments("\n".join(lines)).split("\n")

    passages: list[Passage] = []
    chars = 0
    heading = ""
    para: list[str] = []
    links: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal chars, links
        text = " ".join(" ".join(para).split())
        got, links = tuple(links), []
        para.clear()
        if not text or chars >= limits.max_chars:
            return
        text = text[: min(limits.max_passage_chars, limits.max_chars - chars)]
        chars += len(text)
        passages.append(Passage(f"md#{len(passages)}", heading, text, links=got))

    for line in lines:
        m = _HEADING.match(line)
        if m:
            flush()
            heading = " ".join(_inline(_heading_text(m.group(2)), []).split())
        elif not line.strip():
            flush()
        elif _BULLET.match(line):
            flush()
            para.append(_inline(_BULLET.sub("", line, count=1), links))
        else:
            para.append(_inline(line, links))
    flush()
    return Extracted(passages=passages, chars=chars)
