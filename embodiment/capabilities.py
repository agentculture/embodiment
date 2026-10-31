"""The chat roles the lobes gateway serves, polled over the network only (t2).

``GET /capabilities`` is a JSON object keyed by role name. A role is a *chat*
role when its ``path`` is ``/v1/chat/completions``; lanes (embeddings, audio)
are not. Role identity is the response KEY. Nothing here derives a role, a
capability or a language from a model id.

Scheduling: :meth:`CapabilitiesPoller.poll_once` is the unit (blocking, never
raises) and :meth:`start` / :meth:`stop` run it on one daemon thread every
:data:`POLL_INTERVAL_SECONDS`. A failed poll keeps the last snapshot, marks it
stale, and reports one degradation through ``on_degrade(code, detail)``.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from embodiment.safe_reason import describe_exception

CAPABILITIES_PATH = "/capabilities"
CHAT_PATH = "/v1/chat/completions"

#: Seconds between polls.
POLL_INTERVAL_SECONDS = 60.0
POLL_INTERVAL_REASON = (
    "roles change rarely (a model is loaded or a peer goes away), so a minute keeps "
    "the picture fresh enough for a person choosing a model without loading the gateway"
)

#: One bounded request; a gateway that does not answer in this long has failed.
FETCH_DEADLINE_SECONDS = 5.0
#: The advert is a few KB; anything past this is not a capabilities body.
MAX_BODY_BYTES = 1_000_000

POLL_FAILED = "capabilities-poll-failed"
POLL_MALFORMED = "capabilities-poll-malformed"


@dataclass(frozen=True)
class ChatRole:
    role: str
    model: str
    ready: bool
    host: str  # member, else hosted_by, else "local"


@dataclass(frozen=True)
class Snapshot:
    roles: tuple
    age: Optional[float]  # seconds since the last good poll; None before one
    stale: bool  # True before the first poll or when the latest poll failed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


def _parse(body: str) -> tuple:
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("capabilities body was not an object")
    roles = []
    for key, entry in parsed.items():
        if not isinstance(entry, Mapping) or entry.get("path") != CHAT_PATH:
            continue
        host = entry.get("member") or entry.get("hosted_by") or "local"
        model = entry.get("model")
        roles.append(
            ChatRole(
                role=str(key),
                model=model if isinstance(model, str) else "",
                ready=entry.get("ready") is True,
                host=host if isinstance(host, str) else "local",
            )
        )
    return tuple(roles)


class CapabilitiesPoller:
    def __init__(
        self,
        gateway_url: str,
        *,
        api_key: Optional[str] = None,
        fetch: Optional[Callable[[], str]] = None,
        clock: Callable[[], float] = time.monotonic,
        on_degrade: Optional[Callable[[str, str], None]] = None,
        interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._origin = gateway_url.rstrip("/")
        self._api_key = api_key
        self._fetch = fetch or self._http_fetch
        self._clock = clock
        self._on_degrade = on_degrade
        self._interval = interval
        self._lock = threading.Lock()
        self._roles: tuple = ()
        self._good_at: Optional[float] = None
        self._failed = False
        self._stop = threading.Event()
        self._first = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _http_fetch(self) -> str:
        url = f"{self._origin}{CAPABILITIES_PATH}"
        if not url.startswith(("http://", "https://")):
            raise ValueError("gateway_url must be http(s)")
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        request = urllib.request.Request(url, headers=headers, method="GET")  # nosec B310
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=FETCH_DEADLINE_SECONDS) as response:  # nosec B310
            raw = response.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            raise ValueError("capabilities body over the bound")
        return raw.decode("utf-8", "replace")

    def _degrade(self, code: str, detail: str) -> None:
        with self._lock:
            self._failed = True
        if self._on_degrade is not None:
            try:
                self._on_degrade(code, detail)
            except Exception:  # noqa: BLE001  # nosec B110 - the host hook must not kill the poll
                pass

    def poll_once(self) -> None:
        """One blocking poll. Never raises."""
        try:
            roles = _parse(self._fetch())
        except (ValueError, RecursionError) as exc:
            self._degrade(POLL_MALFORMED, describe_exception(exc))
        except Exception as exc:  # noqa: BLE001 - urllib/socket/timeouts all degrade alike
            self._degrade(POLL_FAILED, describe_exception(exc))
        else:
            with self._lock:
                self._roles = roles
                self._good_at = self._clock()
                self._failed = False
        self._first.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            good_at = self._good_at
            age = None if good_at is None else self._clock() - good_at
            return Snapshot(self._roles, age, self._failed or good_at is None)

    def start(self) -> None:
        """Poll now and then every interval on one daemon thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="capabilities", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._interval)

    def wait_first_poll(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(FETCH_DEADLINE_SECONDS + 1)
        self._thread = None
