"""Bounded EPUB text extraction with stable passage anchors.

Stdlib only: ``zipfile`` for the container and ``html.parser`` for every markup
file (container.xml and the OPF included, so no XML entity machinery is
reachable). Every limit is enforced on bytes actually read, not on sizes the
archive declares, and any refusal raises :class:`EpubRefused` with a fixed code
before anything is returned. Text is returned as found; fencing happens at
render time, not here.

Timing/speaker (park v3: the transcript format is unseen): ``start_s`` /
``end_s`` / ``speaker`` are filled only when a passage carries timing markup
(``data-start`` / ``data-end`` attributes, or a leading ``[hh:mm:ss]`` /
``mm:ss`` token) **and** a speaker label (``data-speaker`` or a leading
``Name:``). Otherwise all three are None and the text is left untouched.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from typing import Optional
from urllib.parse import unquote

_SKIP = frozenset({"script", "style", "head", "title"})
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCKS = frozenset({"p", "div", "li", "blockquote", "pre", "td", "dd", "dt"}) | _HEADINGS
# A leading timestamp, bracketed or bare. Two patterns (tried in that order; they cannot
# both match, a bare one cannot start with "[") keep each regex simple.
_TOKEN_BRACKETED = re.compile(r"^\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+")
_TOKEN_BARE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+")
_LABEL = re.compile(r"^([^\W\d_][\w .'-]{0,40}?):\s+")
#: Links kept per passage, and the longest URL kept: a newspaper bullet links its
#: source (culture-rules' renderer requires it), so Gwen can follow it (2026-10-24).
MAX_LINKS = 20
MAX_LINK_URL = 2048
MAX_LINK_TEXT = 300


class EpubRefused(Exception):
    """A refusal with a fixed ``code``; no partial result accompanies it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Limits:
    max_members: int = 2000
    max_member_bytes: int = 8_000_000
    max_total_bytes: int = 64_000_000
    max_chars: int = 4_000_000


@dataclass(frozen=True)
class Passage:
    anchor: str
    heading: str
    text: str
    start_s: float | None = None
    end_s: float | None = None
    speaker: str | None = None
    #: ``(text, url)`` for each http(s) link in the passage, in order, capped.
    links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Extracted:
    passages: list[Passage]
    chars: int


def _seconds(value: str) -> float | None:
    parts = value.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not 1 <= len(nums) <= 3 or any(n < 0 for n in nums):
        return None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


class _Reader:
    """Reads members under the byte limits, counting what is actually read."""

    def __init__(self, zf: zipfile.ZipFile, limits: Limits) -> None:
        self._zf = zf
        self._limits = limits
        self._total = 0
        self._names = {i.filename for i in zf.infolist()}

    def has(self, name: str) -> bool:
        return name in self._names

    def read(self, name: str) -> str:
        cap = self._limits.max_member_bytes
        try:
            with self._zf.open(name) as fh:
                raw = fh.read(cap + 1)
        except (KeyError, zipfile.BadZipFile, RuntimeError, OSError):
            raise EpubRefused("bad_epub") from None
        if len(raw) > cap:
            raise EpubRefused("member_too_large")
        self._total += len(raw)
        if self._total > self._limits.max_total_bytes:
            raise EpubRefused("total_too_large")
        return raw.decode("utf-8", errors="replace")


class _Package(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rootfile: str | None = None
        self.manifest: dict[str, str] = {}
        self.spine: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "rootfile" and self.rootfile is None:
            self.rootfile = a.get("full-path")
        elif tag == "item" and a.get("id") and a.get("href"):
            self.manifest[a["id"]] = a["href"]
        elif tag == "itemref" and a.get("idref"):
            self.spine.append(a["idref"])

    handle_startendtag = handle_starttag


class _Doc(HTMLParser):
    def __init__(self, path: str) -> None:
        super().__init__(convert_charrefs=True)
        self._path = path
        self.items: list[tuple[str, str, str, dict, tuple]] = (
            []
        )  # anchor, heading, text, attrs, links
        self._heading = ""
        self._index = -1
        self._skip = 0
        self._buf: list[str] = []
        self._cur: tuple[int, dict] | None = None
        self._in_heading = False
        self._links: list[tuple[str, str]] = []
        self._open_link: tuple[str, int] | None = None  # url, buffer index at <a>

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        links, self._links, self._open_link = tuple(self._links), [], None
        cur, self._cur = self._cur, None
        if cur is None or not text:
            return
        if self._in_heading:
            self._heading = text
            self._in_heading = False
            return
        self.items.append((f"{self._path}#{cur[0]}", self._heading, text, cur[1], links))

    def handle_starttag(self, tag, attrs):
        if tag == "a" and not self._skip and self._cur is not None:
            href = (dict(attrs).get("href") or "").strip()
            if _web_url(href):
                self._open_link = (href, len(self._buf))
            return
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCKS and not self._skip:
            self._flush()
            self._index += 1
            self._cur = (self._index, dict(attrs))
            self._in_heading = tag in _HEADINGS

    def handle_endtag(self, tag):
        if tag == "a":
            opened, self._open_link = self._open_link, None
            if opened is not None and len(self._links) < MAX_LINKS:
                text = " ".join("".join(self._buf[opened[1] :]).split())[:MAX_LINK_TEXT]
                self._links.append((text, opened[0]))
            return
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCKS and not self._skip:
            self._flush()

    def handle_data(self, data):
        if not self._skip and self._cur is not None:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _web_url(href: str) -> bool:
    return (
        href.lower().startswith(("http://", "https://"))
        and len(href) <= MAX_LINK_URL
        and not any(c.isspace() for c in href)
    )


def _timing(text: str, attrs: dict) -> tuple[str, float | None, float | None, str | None]:
    """Return (text, start, end, speaker); all-None timing unless start AND speaker found."""
    start = _seconds(attrs["data-start"]) if attrs.get("data-start") else None
    end = _seconds(attrs["data-end"]) if attrs.get("data-end") else None
    speaker = (attrs.get("data-speaker") or "").strip() or None
    body = text
    m = _TOKEN_BRACKETED.match(body) or _TOKEN_BARE.match(body)
    if m:
        if start is None:
            start = _seconds(m.group(1))
        body = body[m.end() :]
        label = None if speaker else _LABEL.match(body)
        if label:
            speaker = label.group(1).strip()
            body = body[label.end() :]
    if start is None or speaker is None:
        return text, None, None, None
    return body, start, end, speaker


def _spine_paths(reader: _Reader) -> list[str]:
    container = _Package()
    container.feed(reader.read("META-INF/container.xml"))
    if not container.rootfile or not reader.has(container.rootfile):
        raise EpubRefused("bad_epub")
    opf = _Package()
    opf.feed(reader.read(container.rootfile))
    base = posixpath.dirname(container.rootfile)
    paths = []
    for idref in opf.spine:
        href = opf.manifest.get(idref)
        if href:
            paths.append(posixpath.normpath(posixpath.join(base, unquote(href.split("#")[0]))))
    if not paths:
        raise EpubRefused("bad_epub")
    return paths


def extract(data: bytes, *, limits: Optional[Limits] = None) -> Extracted:
    """Extract passages in spine order, or raise :class:`EpubRefused`.

    ``limits`` defaults to :class:`Limits` so the daemon can hand this function
    to the sync as the one-argument ``extract(data)`` seam it calls.
    """
    limits = limits if limits is not None else Limits()
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError):
        raise EpubRefused("bad_epub") from None
    with zf:
        if len(zf.infolist()) > limits.max_members:
            raise EpubRefused("too_many_members")
        reader = _Reader(zf, limits)
        if not reader.has("META-INF/container.xml"):
            raise EpubRefused("bad_epub")
        passages: list[Passage] = []
        chars = 0
        for path in _spine_paths(reader):
            if not reader.has(path):
                continue
            doc = _Doc(path)
            doc.feed(reader.read(path))
            doc.close()
            for anchor, heading, text, attrs, links in doc.items:
                body, start, end, speaker = _timing(text, attrs)
                chars += len(body)
                if chars > limits.max_chars:
                    raise EpubRefused("too_many_chars")
                passages.append(Passage(anchor, heading, body, start, end, speaker, links))
    return Extracted(passages=passages, chars=chars)
