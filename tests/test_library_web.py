"""Reading a web page a library item links to (operator, 2026-10-24): public, bounded.

Every test is offline: name resolution and the connection are injected fakes.
"""

from __future__ import annotations

import pytest

from embodiment.library import web


class FakeResponse:
    def __init__(self, status, headers, body=b"", chunk=65536):
        self.status = status
        self._headers = headers
        self._body = body
        self._chunk = chunk

    def getheaders(self):
        return list(self._headers.items())

    def read(self, n):
        out, self._body = self._body[: min(n, self._chunk)], self._body[min(n, self._chunk) :]
        return out


class FakeConn:
    def __init__(self, routes, seen, target):
        self._routes = routes
        self._seen = seen
        self._target = target

    def request(self, method, path, headers=None):
        self._seen.append(
            (method, self._target.scheme, self._target.host, path, self._target.address)
        )

    def getresponse(self):
        key = f"{self._target.scheme}://{self._target.host}{self._target.path}"
        return self._routes[key]

    def close(self):
        pass


def _fetch(url, routes, addresses=None, **kw):
    seen = []
    table = addresses or {}

    def resolve(host, port):
        return table.get(host, ["93.184.216.34"])

    page = web.fetch_page(
        url, resolve=resolve, connect=lambda t, left: FakeConn(routes, seen, t), **kw
    )
    return page, seen


HTML = (
    b"<html><head><title> A story </title><style>x{}</style></head>\n"
    b"<body><script>evil()</script><h1>Heading</h1>"
    b"<p>First   paragraph.</p><p>Second.</p></body></html>"
)


def test_a_public_html_page_becomes_its_title_and_text():
    routes = {
        "https://example.org/a": FakeResponse(
            200, {"Content-Type": "text/html; charset=utf-8"}, HTML
        )
    }
    page, seen = _fetch("https://example.org/a", routes)
    assert page.title == "A story"
    assert page.text == "Heading\nFirst paragraph.\nSecond."
    assert "evil" not in page.text
    assert seen == [("GET", "https", "example.org", "/a", "93.184.216.34")]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.10",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "0.0.0.0",
        "::ffff:127.0.0.1",
        "100.64.0.1",
    ],
)
def test_a_private_or_local_address_is_refused_before_any_connection(address):
    with pytest.raises(web.WebRefused) as refused:
        _fetch("http://intranet.example/", {}, addresses={"intranet.example": [address]})
    assert refused.value.code == web.CODE_PRIVATE_ADDRESS


def test_one_private_address_among_public_ones_is_refused():
    with pytest.raises(web.WebRefused) as refused:
        _fetch(
            "http://mixed.example/", {}, addresses={"mixed.example": ["93.184.216.34", "10.0.0.1"]}
        )
    assert refused.value.code == web.CODE_PRIVATE_ADDRESS


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.org/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pw@example.org/",
        "https://example.org/a b",
        "",
        "https://" + "a" * 3000,
        "https://example.org:99999/",
    ],
)
def test_a_bad_url_is_refused(url):
    with pytest.raises(web.WebRefused) as refused:
        _fetch(url, {})
    assert refused.value.code == web.CODE_BAD_URL


def test_a_redirect_is_followed_only_after_its_target_is_checked():
    routes = {
        "https://example.org/a": FakeResponse(302, {"Location": "https://other.example/b"}),
        "https://other.example/b": FakeResponse(200, {"Content-Type": "text/plain"}, b"moved here"),
    }
    page, seen = _fetch("https://example.org/a", routes)
    assert page.url == "https://other.example/b"
    assert page.text == "moved here"
    assert [s[2] for s in seen] == ["example.org", "other.example"]


def test_a_redirect_to_a_private_address_is_refused():
    routes = {"https://example.org/a": FakeResponse(302, {"Location": "http://metadata.internal/"})}
    with pytest.raises(web.WebRefused) as refused:
        _fetch(
            "https://example.org/a", routes, addresses={"metadata.internal": ["169.254.169.254"]}
        )
    assert refused.value.code == web.CODE_PRIVATE_ADDRESS


def test_too_many_redirects_are_refused():
    routes = {"https://example.org/a": FakeResponse(302, {"Location": "/a"})}
    with pytest.raises(web.WebRefused) as refused:
        _fetch("https://example.org/a", routes)
    assert refused.value.code == web.CODE_TOO_MANY_REDIRECTS


def test_a_non_text_type_is_refused():
    routes = {
        "https://example.org/a": FakeResponse(200, {"Content-Type": "application/pdf"}, b"%PDF")
    }
    with pytest.raises(web.WebRefused) as refused:
        _fetch("https://example.org/a", routes)
    assert refused.value.code == web.CODE_UNSUPPORTED_TYPE


def test_an_oversized_body_is_refused():
    big = b"x" * (web.MAX_BYTES + 10)
    routes = {"https://example.org/a": FakeResponse(200, {"Content-Type": "text/plain"}, big)}
    with pytest.raises(web.WebRefused) as refused:
        _fetch("https://example.org/a", routes)
    assert refused.value.code == web.CODE_TOO_LARGE


def test_an_http_error_is_refused():
    routes = {"https://example.org/a": FakeResponse(404, {"Content-Type": "text/html"}, b"no")}
    with pytest.raises(web.WebRefused) as refused:
        _fetch("https://example.org/a", routes)
    assert refused.value.code == web.CODE_HTTP_ERROR


def test_the_text_is_capped():
    body = b"y" * (web.MAX_CHARS * 2)
    routes = {"https://example.org/a": FakeResponse(200, {"Content-Type": "text/plain"}, body)}
    page, _ = _fetch("https://example.org/a", routes)
    assert len(page.text) == web.MAX_CHARS


def test_an_expired_deadline_is_a_timeout():
    routes = {"https://example.org/a": FakeResponse(200, {"Content-Type": "text/plain"}, b"x")}
    with pytest.raises(web.WebRefused) as refused:
        _fetch("https://example.org/a", routes, deadline_s=0.0)
    assert refused.value.code == web.CODE_TIMEOUT


def test_the_connection_goes_to_the_checked_address_not_a_second_lookup():
    """DNS rebinding: the address that passed the check is the one connected to."""
    calls = []

    def resolve(host, port):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    target = web.check_url("https://example.org/a", resolve)
    assert target.address == "93.184.216.34"
    assert isinstance(web._connect(target, 1.0), web._PinnedHTTPS)
    assert web._connect(target, 1.0)._address == "93.184.216.34"
    assert calls == ["example.org"]
