"""GET-only HTTP client for the library (today: xteink; later: culture-library).

Read side only: ``list_items``, ``get_item``, ``fetch_file``. Every request is
a ``GET`` carrying ``Authorization: Bearer <key>``. The request path mirrors
``vectors._urllib_transport``: redirects are refused (the key never leaves the
configured origin), every body is read under a byte cap and a total deadline.
Every failure is a :class:`LibraryError` with a fixed ``code`` and a fixed
message; no message, repr or attribute ever carries the key or response text.

The shape of one library item lives in :func:`item_from_wire` and nowhere else,
so a different library (culture-library) re-points one function.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from embodiment.voice import (
    _REDIRECT_REFUSING_OPENER,
    VoiceReadTimeoutError,
    VoiceRedirectRefusedError,
    _read_bounded,
)

ENV_URL = "EMBODIMENT_LIBRARY_URL"
ENV_KEY = "EMBODIMENT_LIBRARY_KEY"

#: A catalog page is JSON metadata for at most ``limit`` (<= 1000) items of a
#: few hundred bytes each; 2 MiB is an order of magnitude over that.
MAX_CATALOG_BYTES = 2 * 1024 * 1024
#: A file is bounded by the library's own ingest limit (xteink: 50 MiB, EPUB,
#: BMP or TXT stored as-is), so nothing legitimate is censored by this cap.
MAX_FILE_BYTES = 50 * 1024 * 1024
#: Floor transfer rate used to derive the file read clock from the cap: the
#: total body deadline is ``deadline + MAX_FILE_BYTES / MIN_FILE_BYTES_PER_S``
#: (50 s + the request deadline), so the clock bounds the quantity it reads.
MIN_FILE_BYTES_PER_S = 1024 * 1024
DEFAULT_DEADLINE = 10.0

_MESSAGES = {
    "library_bad_config": "library url must be http(s)",
    "library_redirect": "library answered with a redirect; refused",
    "library_oversize": "library response over its size cap",
    "library_unauthorized": "library refused the key",
    "library_timeout": "library did not answer in time",
    "library_http": "library answered with an error status",
    "library_unreachable": "library is unreachable",
    "library_bad_response": "library response was not the expected shape",
}


class LibraryError(Exception):
    """A typed library failure: a fixed ``code`` and a fixed message, nothing else."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"{code}: {_MESSAGES[code]}")


@dataclass(frozen=True)
class LibraryItem:
    id: Any
    sha256: str
    kind: str
    title: str
    author: str
    format: str
    size: int
    created_at: str


def item_from_wire(raw: Any) -> LibraryItem:
    """The ONE shape mapping: xteink ``Item`` JSON -> :class:`LibraryItem`."""
    if not isinstance(raw, dict):
        raise LibraryError("library_bad_response")
    try:
        return LibraryItem(
            id=raw["id"],
            sha256=str(raw["sha256"]),
            kind=str(raw["kind"]),
            title=str(raw["title"]),
            author=str(raw.get("author", "")),
            format=str(raw["format"]),
            size=int(raw["size"]),
            created_at=str(raw.get("created_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        raise LibraryError("library_bad_response") from None


@dataclass(frozen=True)
class LibraryConfig:
    base_url: str
    key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Optional["LibraryConfig"]:
        """``None`` (library disabled) unless the URL is set."""
        url = (env.get(ENV_URL) or "").strip()
        if not url:
            return None
        return cls(base_url=url, key=(env.get(ENV_KEY) or "").strip())


def _classify(exc: BaseException) -> LibraryError:
    if isinstance(exc, VoiceRedirectRefusedError):
        return LibraryError("library_redirect")
    if isinstance(exc, (VoiceReadTimeoutError, socket.timeout, TimeoutError)):
        return LibraryError("library_timeout")
    if isinstance(exc, urllib.error.HTTPError):
        return LibraryError("library_unauthorized" if exc.code == 401 else "library_http")
    if isinstance(exc, urllib.error.URLError):
        inner = exc.reason
        if isinstance(inner, (socket.timeout, TimeoutError)):
            return LibraryError("library_timeout")
        if isinstance(inner, VoiceRedirectRefusedError):
            return LibraryError("library_redirect")
        return LibraryError("library_unreachable")
    return LibraryError("library_unreachable")


class LibraryClient:
    def __init__(
        self,
        base_url: str,
        key: str,
        *,
        deadline: float = DEFAULT_DEADLINE,
        opener: Any = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise LibraryError("library_bad_config")
        self._base = base_url.rstrip("/")
        self._key = key
        self._deadline = deadline
        self._opener = opener if opener is not None else _REDIRECT_REFUSING_OPENER
        #: Requests issued (every one a GET by construction - the AST test pins it);
        #: read by LibrarySync.status() and the live acceptance run (c28).
        self.gets = 0
        self._count_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"LibraryClient(base_url={self._base!r})"

    def _get(self, path: str, *, max_bytes: int, read_deadline: float) -> bytes:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        with self._count_lock:
            self.gets += 1
        request = urllib.request.Request(  # nosec B310 - scheme checked in __init__
            f"{self._base}{path}", headers=headers, method="GET"
        )
        try:
            with self._opener.open(  # nosec B310 - scheme checked, redirects refused
                request, timeout=self._deadline
            ) as response:
                if 300 <= int(getattr(response, "status", 200)) < 400:
                    raise LibraryError("library_redirect")
                raw = _read_bounded(response, max_bytes=max_bytes, deadline_s=read_deadline)
        except LibraryError:
            raise
        except Exception as exc:  # noqa: BLE001  # folded to a typed, key-free error
            raise _classify(exc) from None
        if len(raw) > max_bytes:
            raise LibraryError("library_oversize")
        return raw

    def _get_json(self, path: str) -> Any:
        raw = self._get(path, max_bytes=MAX_CATALOG_BYTES, read_deadline=self._deadline)
        try:
            return json.loads(raw)
        except ValueError:
            raise LibraryError("library_bad_response") from None

    def list_items(self, kind: Optional[str] = None, q: Optional[str] = None) -> list[LibraryItem]:
        params = {k: v for k, v in (("q", q), ("kind", kind)) if v}
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        body = self._get_json(f"/api/library{query}")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise LibraryError("library_bad_response")
        return [item_from_wire(i) for i in items]

    def get_item(self, item_id: Any) -> LibraryItem:
        return item_from_wire(self._get_json(f"/api/library/{_seg(item_id)}"))

    def fetch_file(self, item_id: Any) -> bytes:
        return self._get(
            f"/api/library/{_seg(item_id)}/file",
            max_bytes=MAX_FILE_BYTES,
            read_deadline=self._deadline + MAX_FILE_BYTES / MIN_FILE_BYTES_PER_S,
        )

    def fetch_text(self, item_id: Any) -> dict:
        """``GET /api/library/{id}/text``: ``{item_id, kind, format, title, text, sections,
        reason}`` as the library sent it; ``sections`` is null for Markdown."""
        body = self._get(
            f"/api/library/{_seg(item_id)}/text",
            max_bytes=MAX_FILE_BYTES,
            read_deadline=self._deadline + MAX_FILE_BYTES / MIN_FILE_BYTES_PER_S,
        )
        try:
            parsed = json.loads(body)
        except ValueError:
            raise LibraryError("library_bad_response") from None
        if not isinstance(parsed, dict):
            raise LibraryError("library_bad_response")
        return parsed


def _seg(item_id: Any) -> str:
    return urllib.parse.quote(str(item_id), safe="")
