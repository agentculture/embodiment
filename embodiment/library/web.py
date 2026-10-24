"""Reading one web page a library item links to: bounded, public-only, GET-only.

Operator, 2026-10-24: "I want gwen to be able to summarize the newsletter for me, and
even pull from sources of the articles and summarize that" -- and, asked which pages
she may fetch: **only links in the library**. This module does the fetching; *which*
URL may be fetched (one a library item lists) is decided by the tool that calls it
(:mod:`embodiment.library.tools`), never here and never by the model alone.

Rules, each pinned by a test:

* ``http`` / ``https`` only, no credentials in the URL, GET only.
* **Public addresses only.** The host is resolved and EVERY address must be public
  (not private, loopback, link-local, multicast, reserved or unspecified), and the
  connection goes to the address that was checked -- not to a second lookup, so a name
  cannot answer differently between the check and the connect (DNS rebinding). TLS
  still verifies the certificate against the host name.
* **Redirects are never followed blindly:** at most :data:`MAX_REDIRECTS`, and each
  ``Location`` passes the same scheme and address checks before it is fetched.
* **Bounded:** one total deadline across connect, redirects and body
  (:data:`DEADLINE_S`), at most :data:`MAX_BYTES` read, at most :data:`MAX_CHARS` of
  text returned.
* HTML, XHTML and plain text only; HTML becomes text with the stdlib parser, scripts
  and styles dropped. The text is returned as found -- fencing it as untrusted is the
  caller's job, exactly as for library passages.

Every refusal raises :class:`WebRefused` with a fixed code; nothing here logs the URL's
response body or echoes server-controlled text in a reason.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 3
DEADLINE_S = 15.0
MAX_BYTES = 2_000_000
MAX_CHARS = 20_000
MAX_URL_CHARS = 2048
USER_AGENT = "Gwen/embodiment (reads a source her library links to)"

CODE_BAD_URL = "web-bad-url"
CODE_PRIVATE_ADDRESS = "web-private-address"
CODE_UNREACHABLE = "web-unreachable"
CODE_TIMEOUT = "web-timeout"
CODE_HTTP_ERROR = "web-http-error"
CODE_TOO_MANY_REDIRECTS = "web-too-many-redirects"
CODE_UNSUPPORTED_TYPE = "web-unsupported-type"
CODE_TOO_LARGE = "web-too-large"
CODES = frozenset(
    {
        CODE_BAD_URL,
        CODE_PRIVATE_ADDRESS,
        CODE_UNREACHABLE,
        CODE_TIMEOUT,
        CODE_HTTP_ERROR,
        CODE_TOO_MANY_REDIRECTS,
        CODE_UNSUPPORTED_TYPE,
        CODE_TOO_LARGE,
    }
)

_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_CHARSET = re.compile(r"charset=([\w.-]+)", re.IGNORECASE)
_SKIP = frozenset({"script", "style", "noscript", "svg", "template"})
_BLOCKS = frozenset(
    {"p", "div", "li", "br", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
)


class WebRefused(Exception):
    """A refusal with a fixed ``code`` (one of :data:`CODES`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Page:
    """What was read: the final URL, the page title, and its text (capped)."""

    url: str
    title: str
    text: str


Resolve = Callable[[str, int], list[str]]


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise WebRefused(CODE_UNREACHABLE) from None
    return sorted({info[4][0] for info in infos})


def is_public(address: str) -> bool:
    """True only for a globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str
    port: int
    path: str
    #: Every checked address, IPv4 first: a rig without IPv6 routing still reaches a
    #: dual-stack site (review of PR #102). Only these are ever connected to.
    addresses: tuple[str, ...]


def check_url(url: Any, resolve: Resolve = _resolve) -> _Target:
    """The URL's scheme, host, port, path and the public address to connect to."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS or re.search(r"\s", url):
        raise WebRefused(CODE_BAD_URL)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebRefused(CODE_BAD_URL)
    if parts.username or parts.password:
        raise WebRefused(CODE_BAD_URL)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise WebRefused(CODE_BAD_URL) from None
    addresses = resolve(parts.hostname, port)
    if not addresses or not all(is_public(a) for a in addresses):
        raise WebRefused(CODE_PRIVATE_ADDRESS)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    ordered = tuple(sorted(addresses, key=lambda a: (":" in a, a)))
    return _Target(parts.scheme, parts.hostname, port, path, ordered)


def _open_first(addresses: tuple[str, ...], port: int, timeout: Optional[float]) -> socket.socket:
    """A socket to the first checked address that answers; the last error otherwise."""
    last: Optional[OSError] = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout)
        except OSError as exc:
            last = exc
    raise last or OSError("no address")


class _PinnedHTTPS(http.client.HTTPSConnection):
    """An HTTPS connection to a checked address, verifying the certificate for the host."""

    def __init__(self, host: str, port: int, addresses: tuple[str, ...], timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._addresses = addresses

    def connect(self) -> None:
        sock = _open_first(self._addresses, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, addresses: tuple[str, ...], timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._addresses = addresses

    def connect(self) -> None:
        self.sock = _open_first(self._addresses, self.port, self.timeout)


Connect = Callable[[_Target, float], Any]


def _connect(target: _Target, timeout: float) -> Any:
    cls = _PinnedHTTPS if target.scheme == "https" else _PinnedHTTP
    return cls(target.host, target.port, target.addresses, timeout)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._skip = 0
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCKS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCKS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data
        else:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).split("\n"))
        return "\n".join(line for line in lines if line)


def _decode(body: bytes, content_type: str) -> str:
    found = _CHARSET.search(content_type)
    try:
        return body.decode(found.group(1) if found else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _read_body(response: Any, end: float) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() > end:
            raise WebRefused(CODE_TIMEOUT)
        chunk = response.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_BYTES:
            raise WebRefused(CODE_TOO_LARGE)
        chunks.append(chunk)


def _request(target: _Target, end: float, connect: Connect) -> tuple[int, dict, bytes]:
    left = end - time.monotonic()
    if left <= 0:
        raise WebRefused(CODE_TIMEOUT)
    conn = connect(target, left)
    try:
        conn.request("GET", target.path, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        response = conn.getresponse()
        headers = {k.lower(): v for k, v in response.getheaders()}
        if 300 <= response.status < 400:
            return response.status, headers, b""
        return response.status, headers, _read_body(response, end)
    except WebRefused:
        raise
    except (socket.timeout, TimeoutError):
        raise WebRefused(CODE_TIMEOUT) from None
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise WebRefused(CODE_UNREACHABLE) from None
    finally:
        conn.close()


def fetch_page(
    url: str,
    *,
    resolve: Resolve = _resolve,
    connect: Connect = _connect,
    deadline_s: float = DEADLINE_S,
) -> Page:
    """Read *url* as text under every rule above, or raise :class:`WebRefused`."""
    end = time.monotonic() + deadline_s
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        target = check_url(current, resolve)
        status, headers, body = _request(target, end, connect)
        if 300 <= status < 400:
            location = headers.get("location")
            if not location:
                raise WebRefused(CODE_HTTP_ERROR)
            current = urljoin(current, location)
            continue
        if status != 200:
            raise WebRefused(CODE_HTTP_ERROR)
        content_type = headers.get("content-type", "").lower()
        if not content_type.startswith(_TEXT_TYPES):
            raise WebRefused(CODE_UNSUPPORTED_TYPE)
        decoded = _decode(body, content_type)
        if content_type.startswith("text/plain"):
            return Page(url=current, title="", text=decoded[:MAX_CHARS])
        parser = _Text()
        parser.feed(decoded)
        parser.close()
        title = " ".join(parser.title.split())[:300]
        return Page(url=current, title=title, text=parser.text()[:MAX_CHARS])
    raise WebRefused(CODE_TOO_MANY_REDIRECTS)


def fetcher(**overrides: Any) -> Optional[Callable[[str], Page]]:
    """``url -> Page`` with *overrides* (tests); the daemon wires the default."""
    return lambda url: fetch_page(url, **overrides)
