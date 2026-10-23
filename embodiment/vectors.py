"""Vector cache and the ``gemma2-embed`` client (plan task t4).

Two small, independent pieces for semantic recall:

* :class:`VectorCache` - a private (0600) JSON file of embeddings keyed by
  ``sha256(model, text)``. An entry written for another model is never
  returned, and :meth:`VectorCache.drop` removes entries by record id.
* :class:`EmbedderClient` - ``POST /v1/embeddings`` on the lobes gateway,
  naming the embedder by ROLE (``gemma2-embed``), key via the same
  ``Authorization: Bearer`` path the daemon uses. Stdlib only.

Degrade, never raise (C3): an unreachable or malformed embedder is an
``offline`` :class:`EmbedResult` with a scrubbed reason and a recorded
transition. Online state is the outcome of the last real call; a bounded
background re-probe (one thread at a time, at most one per interval) refreshes
it when stale. A recall-path :meth:`EmbedderClient.embed` makes exactly one
request - no retry, no probe.

Probe findings (2026-10, n=1): a call through the proxy costs 158-210 ms; the
no-prompt payload is accepted; the lane accepts the ``Retrieval-query`` and
``Retrieval-document`` prompt names and refuses ``Query``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from embodiment import safe_reason

EMBED_ROLE = "gemma2-embed"
EMBED_ROUTE = "/v1/embeddings"
CACHE_FILENAME = "vectors.json"
PROMPT_NAMES = ("Retrieval-query", "Retrieval-document")
DEFAULT_DEADLINE = 5.0
DEFAULT_REPROBE_INTERVAL = 30.0
MAX_REASON_CHARS = 200
_PROBE_TEXT = "ping"

Transport = Callable[[str, dict, bytes, float], bytes]


def _reason(value: object) -> str:
    return safe_reason.scrub(str(value))[:MAX_REASON_CHARS]


def _text_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text}".encode("utf-8")).hexdigest()


class VectorCache:
    """Embeddings on disk (0600) under *directory*, keyed by text hash + model."""

    def __init__(self, directory: Any, model: str = EMBED_ROLE) -> None:
        self.model = model
        self.path = Path(directory) / CACHE_FILENAME
        self.degradations: list[str] = []
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            self.degradations.append(
                f"vector cache unreadable: {_reason(safe_reason.describe_exception(exc))}"
            )
            return {}
        try:
            entries = json.loads(raw)["entries"]
            if not isinstance(entries, dict):
                raise ValueError("entries is not an object")
            return {k: v for k, v in entries.items() if isinstance(v, dict)}
        except (ValueError, KeyError, TypeError) as exc:
            self.degradations.append(
                f"vector cache unreadable: {_reason(safe_reason.describe_exception(exc))}"
            )
            return {}

    def _save(self) -> None:
        data = json.dumps({"entries": self._entries}, ensure_ascii=False).encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            os.replace(tmp, self.path)
        except OSError as exc:
            self.degradations.append(
                f"vector cache not persisted: {_reason(safe_reason.describe_exception(exc))}"
            )

    def get(self, text: str) -> Optional[list[float]]:
        with self._lock:
            entry = self._entries.get(_text_key(self.model, text))
        if not entry or entry.get("model") != self.model:
            return None
        vec = entry.get("vec")
        if isinstance(vec, list) and all(isinstance(x, (int, float)) for x in vec):
            return [float(x) for x in vec]
        return None

    def put(self, text: str, vector: list[float], record_id: Optional[str] = None) -> None:
        with self._lock:
            self._entries[_text_key(self.model, text)] = {
                "model": self.model,
                "record_id": record_id,
                "vec": [float(x) for x in vector],
            }
            self._save()

    def drop(self, record_id: str) -> int:
        """Remove every entry (any model) written for *record_id*; return the count."""
        with self._lock:
            doomed = [k for k, v in self._entries.items() if v.get("record_id") == record_id]
            for key in doomed:
                del self._entries[key]
            if doomed:
                self._save()
        return len(doomed)


@dataclass(frozen=True)
class EmbedResult:
    vector: Optional[list[float]]
    online: bool
    reason: str = ""


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise ValueError("gateway_url must be http(s)")
    request = urllib.request.Request(  # nosec B310 - scheme checked above
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.read()


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="embedder-reprobe", daemon=True).start()


class EmbedderClient:
    """``/v1/embeddings`` by role name; never raises, records every transition."""

    def __init__(
        self,
        gateway_url: str,
        api_key: str = "",
        *,
        model: str = EMBED_ROLE,
        deadline: float = DEFAULT_DEADLINE,
        reprobe_interval: float = DEFAULT_REPROBE_INTERVAL,
        transport: Optional[Transport] = None,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _spawn_thread,
    ) -> None:
        self.model = model
        self._url = f"{gateway_url.rstrip('/')}{EMBED_ROUTE}"
        self._key = api_key
        self._deadline = deadline
        self._interval = reprobe_interval
        self._transport = transport or _urllib_transport
        self._clock = clock
        self._spawn = spawn
        self._lock = threading.Lock()
        self._online: Optional[bool] = None
        self._reason = ""
        self._last_call: Optional[float] = None
        self._probing = False
        self.transitions: list[tuple[str, str]] = []

    def _record(self, online: bool, reason: str) -> None:
        with self._lock:
            changed = self._online is not online
            self._online, self._reason = online, reason
            self._last_call = self._clock()
            if changed:
                self.transitions.append(("online" if online else "offline", reason))

    def state(self) -> dict:
        """``online`` is None until the first real call, then the last call's outcome."""
        with self._lock:
            return {"online": self._online, "reason": self._reason, "model": self.model}

    def embed(self, text: str, prompt: Optional[str] = None) -> EmbedResult:
        """One request. *prompt* is a lane prompt name or None (the accepted default)."""
        if prompt is not None and prompt not in PROMPT_NAMES:
            return EmbedResult(None, self._online is True, _reason(f"unknown prompt name {prompt}"))
        payload: dict[str, Any] = {"model": self.model, "input": text}
        if prompt is not None:
            payload["prompt_name"] = prompt
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            raw = self._transport(self._url, headers, body, self._deadline)
            vec = json.loads(raw)["data"][0]["embedding"]
            if (
                not isinstance(vec, list)
                or not vec
                or not all(isinstance(x, (int, float)) for x in vec)
            ):
                raise ValueError("embedding is not a numeric list")
            vector = [float(x) for x in vec]
        except Exception as exc:  # noqa: BLE001 - degrade, never raise; reason is scrubbed
            reason = _reason(safe_reason.describe_exception(exc))
            self._record(False, reason)
            return EmbedResult(None, False, reason)
        self._record(True, "")
        return EmbedResult(vector, True)

    def maybe_reprobe(self) -> bool:
        """Start a background probe if the last call is stale and none is in flight."""
        with self._lock:
            if self._probing:
                return False
            if self._last_call is not None and self._clock() - self._last_call < self._interval:
                return False
            self._probing = True

        def run() -> None:
            try:
                self.embed(_PROBE_TEXT)
            finally:
                with self._lock:
                    self._probing = False

        try:
            self._spawn(run)
        except Exception:  # noqa: BLE001 - a thread that cannot start is a skipped probe
            with self._lock:
                self._probing = False
            return False
        return True
