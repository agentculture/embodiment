"""The library's retrieval endpoint: the ONE non-GET request this package makes.

``RetrieveClient.retrieve`` issues a single ``POST`` to the literal path
``/api/rag/retrieve`` (hybrid mode, no rerank) and nothing else. ``client.py``
stays GET-only; an AST test pins this module to exactly that one request.

Every failure is a :class:`~embodiment.library.client.LibraryError` with one of
the four declared :data:`CODES` and a fixed message; no message, repr or
attribute carries the key or response text. Redirects are refused, the body is
read under a byte cap and a total deadline. No score floor is applied here:
hybrid scores are small (0.01-0.03), so a threshold belongs to the caller.
Passages of kind ``todo`` or ``discord-message`` are dropped before they are
returned. Library text is untrusted data.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from embodiment.library.client import ENV_KEY, ENV_URL, LibraryError
from embodiment.voice import (
    _REDIRECT_REFUSING_OPENER,
    VoiceReadTimeoutError,
    _read_bounded,
)

__all__ = ["CODES", "ENV_KEY", "ENV_URL", "Passage", "RetrieveClient"]

#: k passages of chunk text (a few KiB each) plus metadata; 2 MiB is far over that.
MAX_RETRIEVE_BYTES = 2 * 1024 * 1024
#: An error body is only inspected for the marker below; read no more than this.
_MAX_ERROR_BYTES = 4096
DEFAULT_DEADLINE = 10.0
#: Kinds that never reach Gwen from retrieval.
EXCLUDED_KINDS = frozenset({"todo", "discord-message"})

CODES = {
    "library_unavailable": "library is unreachable or refused the request",
    "rag_unavailable": "library retrieval is unavailable",
    "library_timeout": "library did not answer in time",
    "library_bad_response": "library response was not the expected shape",
}


class RetrieveError(LibraryError):
    """A :class:`LibraryError` whose code is one of :data:`CODES`."""

    def __init__(self, code: str) -> None:
        self.code = code
        Exception.__init__(self, f"{code}: {CODES[code]}")


@dataclass(frozen=True)
class Passage:
    item_id: Any
    title: str
    kind: str
    chunk_no: int
    chunk_sha: str
    text: str
    score: float


def _passage_from_wire(raw: Any) -> Passage:
    if not isinstance(raw, dict):
        raise RetrieveError("library_bad_response")
    try:
        return Passage(
            item_id=raw["item_id"],
            title=str(raw["title"]),
            kind=str(raw["kind"]),
            chunk_no=int(raw["chunk_no"]),
            chunk_sha=str(raw["chunk_sha"]),
            text=str(raw["text"]),
            score=float(raw["score"]),
        )
    except (KeyError, TypeError, ValueError):
        raise RetrieveError("library_bad_response") from None


def _timeout_like(exc: BaseException) -> bool:
    return isinstance(exc, (VoiceReadTimeoutError, socket.timeout, TimeoutError))


def _classify(exc: BaseException) -> RetrieveError:
    if _timeout_like(exc):
        return RetrieveError("library_timeout")
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        if _timeout_like(exc.reason):
            return RetrieveError("library_timeout")
    return RetrieveError("library_unavailable")


def _http_error_code(exc: urllib.error.HTTPError) -> str:
    if exc.code != 503:
        return "library_unavailable"
    try:
        body = exc.read(_MAX_ERROR_BYTES)
    except Exception:  # noqa: BLE001  # an unreadable error body is just "unavailable"
        return "library_unavailable"
    return "rag_unavailable" if b"rag_unavailable" in body else "library_unavailable"


class RetrieveClient:
    def __init__(
        self,
        base_url: str,
        key: str,
        deadline: float = DEFAULT_DEADLINE,
        *,
        opener: Any = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._valid_url = base_url.startswith(("http://", "https://"))
        self._key = key
        self._deadline = deadline
        self._opener = opener if opener is not None else _REDIRECT_REFUSING_OPENER
        self._lock = threading.Lock()
        self._calls = 0
        self._calls_ok = 0
        self._failures: dict[str, int] = {}
        self._last_latency_s: Optional[float] = None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], deadline: float = DEFAULT_DEADLINE
    ) -> Optional["RetrieveClient"]:
        """``None`` (retrieval disabled) unless the URL is set, like ``LibraryConfig``."""
        url = (env.get(ENV_URL) or "").strip()
        if not url:
            return None
        return cls(url, (env.get(ENV_KEY) or "").strip(), deadline)

    def __repr__(self) -> str:
        return f"RetrieveClient(base_url={self._base!r})"

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self._calls,
                "calls_ok": self._calls_ok,
                "failures_by_code": dict(self._failures),
                "last_latency_s": self._last_latency_s,
            }

    def retrieve(
        self,
        query: str,
        k: int,
        kind: Optional[str] = None,
        item_ids: Optional[Sequence[Any]] = None,
    ) -> list[Passage]:
        started = time.monotonic()
        with self._lock:
            self._calls += 1
        try:
            passages = self._retrieve(query, k, kind, item_ids)
        except RetrieveError as exc:
            self._finish(started, exc.code)
            raise
        except Exception:  # noqa: BLE001  # nothing else escapes
            self._finish(started, "library_bad_response")
            raise RetrieveError("library_bad_response") from None
        self._finish(started, None)
        return passages

    def _finish(self, started: float, code: Optional[str]) -> None:
        with self._lock:
            self._last_latency_s = time.monotonic() - started
            if code is None:
                self._calls_ok += 1
            else:
                self._failures[code] = self._failures.get(code, 0) + 1

    def _retrieve(
        self, query: str, k: int, kind: Optional[str], item_ids: Optional[Sequence[Any]]
    ) -> list[Passage]:
        if not self._valid_url:
            raise RetrieveError("library_unavailable")
        if not isinstance(query, str) or isinstance(k, bool) or not isinstance(k, int):
            raise RetrieveError("library_bad_response")
        payload: dict[str, Any] = {"query": query, "k": k, "mode": "hybrid", "rerank": False}
        if kind:
            payload["kind"] = kind
        if item_ids:
            payload["item_ids"] = list(item_ids)
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        request = urllib.request.Request(  # nosec B310 - scheme checked above
            f"{self._base}/api/rag/retrieve",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(  # nosec B310 - scheme checked, redirects refused
                request, timeout=self._deadline
            ) as response:
                if int(getattr(response, "status", 200)) != 200:
                    raise RetrieveError("library_unavailable")
                raw = _read_bounded(
                    response, max_bytes=MAX_RETRIEVE_BYTES, deadline_s=self._deadline
                )
        except RetrieveError:
            raise
        except urllib.error.HTTPError as exc:
            raise RetrieveError(_http_error_code(exc)) from None
        except Exception as exc:  # noqa: BLE001  # folded to a typed, key-free error
            raise _classify(exc) from None
        if len(raw) > MAX_RETRIEVE_BYTES:
            raise RetrieveError("library_bad_response")
        try:
            body = json.loads(raw)
        except ValueError:
            raise RetrieveError("library_bad_response") from None
        wire = body.get("passages") if isinstance(body, dict) else None
        if not isinstance(wire, list):
            raise RetrieveError("library_bad_response")
        passages = [_passage_from_wire(p) for p in wire]
        return [p for p in passages if p.kind not in EXCLUDED_KINDS]
