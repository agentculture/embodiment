"""LibraryClient: GET-only, bearer-keyed, redirect-refusing, bounded; fakes only."""

import ast
import io
import json
import socket
import time
import urllib.error
from pathlib import Path

import pytest

from embodiment.library import client as lib
from embodiment.library.client import (
    LibraryClient,
    LibraryConfig,
    LibraryError,
    item_from_wire,
)
from embodiment.voice import VoiceRedirectRefusedError

KEY = "xtk_SECRETKEYVALUE123"
ITEM = {
    "id": 7,
    "sha256": "ab" * 32,
    "kind": "book",
    "title": "Dune",
    "author": "Herbert",
    "format": "epub",
    "size": 10,
    "created_at": "2026-10-06T12:00:00+00:00",
}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeOpener:
    def __init__(self, body=b"", status=200, raises=None):
        self.body, self.status, self.raises = body, status, raises
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.raises is not None:
            raise self.raises
        resp = _Resp(self.body)
        resp.status = self.status
        return resp


def _client(opener):
    return LibraryClient("http://lib.test:8780/", KEY, deadline=2.0, opener=opener)


def test_list_items_get_with_bearer_and_query():
    op = FakeOpener(json.dumps({"items": [ITEM]}).encode())
    items = _client(op).list_items(kind="book", q="du ne")
    req = op.requests[0]
    assert req.get_method() == "GET" and req.data is None
    assert req.get_header("Authorization") == f"Bearer {KEY}"
    assert req.full_url.startswith("http://lib.test:8780/api/library?")
    assert "kind=book" in req.full_url and "q=du+ne" in req.full_url
    assert items[0].title == "Dune" and items[0].id == 7 and items[0].format == "epub"


def test_list_items_no_filters_has_no_query():
    op = FakeOpener(b'{"items": []}')
    assert _client(op).list_items() == []
    assert op.requests[0].full_url == "http://lib.test:8780/api/library"


def test_get_item_and_fetch_file():
    op = FakeOpener(json.dumps(ITEM).encode())
    assert _client(op).get_item(7).sha256 == "ab" * 32
    assert op.requests[0].full_url.endswith("/api/library/7")
    op2 = FakeOpener(b"EPUBBYTES")
    assert _client(op2).fetch_file(7) == b"EPUBBYTES"
    assert op2.requests[0].full_url.endswith("/api/library/7/file")
    assert op2.requests[0].get_method() == "GET"


def test_item_id_is_path_quoted():
    op = FakeOpener(json.dumps(ITEM).encode())
    _client(op).get_item("a/../b")
    assert "/api/library/a%2F..%2Fb" in op.requests[0].full_url


def test_shape_mapping_is_one_function_and_tolerates_junk():
    assert item_from_wire(ITEM).author == "Herbert"
    with pytest.raises(LibraryError) as e:
        item_from_wire({"id": 1})
    assert e.value.code == "library_bad_response"
    with pytest.raises(LibraryError):
        item_from_wire("nope")


def _assert_error(call, code):
    with pytest.raises(LibraryError) as e:
        call()
    assert e.value.code == code
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert e.value.code in str(e.value)
    return e.value


def test_redirect_status_refused():
    _assert_error(lambda: _client(FakeOpener(status=302)).get_item(1), "library_redirect")


def test_redirect_raised_by_opener_refused():
    op = FakeOpener(raises=VoiceRedirectRefusedError("refused redirect: status=302"))
    _assert_error(lambda: _client(op).get_item(1), "library_redirect")
    assert len(op.requests) == 1


def test_oversize_catalog_and_file():
    big = b"x" * (lib.MAX_CATALOG_BYTES + 1)
    _assert_error(lambda: _client(FakeOpener(big)).list_items(), "library_oversize")
    _assert_error(
        lambda: _client(FakeOpener(b"x" * (lib.MAX_FILE_BYTES + 1))).fetch_file(1),
        "library_oversize",
    )


def test_catalog_cap_is_2_mib_and_file_cap_stated():
    assert lib.MAX_CATALOG_BYTES == 2 * 1024 * 1024
    assert lib.MAX_FILE_BYTES == 50 * 1024 * 1024


def test_401_unauthorized_never_echoes_key():
    err = urllib.error.HTTPError("http://x", 401, f"bad {KEY}", {}, io.BytesIO(KEY.encode()))
    _assert_error(lambda: _client(FakeOpener(raises=err)).get_item(1), "library_unauthorized")


def test_other_http_status_is_typed():
    err = urllib.error.HTTPError("http://x", 404, "nf", {}, io.BytesIO(b""))
    _assert_error(lambda: _client(FakeOpener(raises=err)).get_item(1), "library_http")


@pytest.mark.parametrize(
    "exc",
    [socket.timeout("t"), TimeoutError("t"), urllib.error.URLError(socket.timeout("t"))],
)
def test_timeout(exc):
    _assert_error(lambda: _client(FakeOpener(raises=exc)).get_item(1), "library_timeout")


def test_slow_body_hits_total_deadline():
    class Slow(_Resp):
        def read(self, n=-1):
            time.sleep(0.03)
            return super().read(1)

    class SlowOpener(FakeOpener):
        def open(self, request, timeout=None):
            return Slow(b"x" * 100)

    c = LibraryClient("http://l.test", KEY, deadline=0.05, opener=SlowOpener())
    _assert_error(lambda: c.get_item(1), "library_timeout")


def test_unreachable_and_bad_json():
    err = urllib.error.URLError(ConnectionRefusedError("no"))
    _assert_error(lambda: _client(FakeOpener(raises=err)).get_item(1), "library_unreachable")
    _assert_error(lambda: _client(FakeOpener(b"not json")).get_item(1), "library_bad_response")
    _assert_error(lambda: _client(FakeOpener(b'{"items": 3}')).list_items(), "library_bad_response")


def test_non_http_base_url_rejected():
    with pytest.raises(LibraryError) as e:
        LibraryClient("file:///etc", KEY, deadline=1.0, opener=FakeOpener())
    assert e.value.code == "library_bad_config"


def test_default_opener_is_redirect_refusing():
    from embodiment import voice

    c = LibraryClient("http://lib.test", KEY, deadline=1.0)
    assert c._opener is voice._REDIRECT_REFUSING_OPENER


def test_repr_never_carries_key():
    c = _client(FakeOpener())
    cfg = LibraryConfig(base_url="http://x", key=KEY)
    for obj in (c, cfg):
        assert KEY not in repr(obj) and KEY not in str(obj)


def test_config_from_env():
    assert LibraryConfig.from_env({}) is None
    assert LibraryConfig.from_env({"EMBODIMENT_LIBRARY_URL": "  "}) is None
    cfg = LibraryConfig.from_env(
        {"EMBODIMENT_LIBRARY_URL": "http://l:1", "EMBODIMENT_LIBRARY_KEY": KEY}
    )
    assert cfg.base_url == "http://l:1" and cfg.key == KEY
    nokey = LibraryConfig.from_env({"EMBODIMENT_LIBRARY_URL": "http://l:1"})
    assert nokey is not None and nokey.key == ""


def test_key_never_written_to_disk_or_logged_by_module():
    tree = ast.parse(Path(lib.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "print"}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"write_text", "write_bytes"}
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            assert "logging" not in names


# --- AST guard: GET only -----------------------------------------------------

_BAD = {"POST", "PUT", "PATCH", "DELETE"}


def _string_violation(node) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value.strip().upper() in _BAD:
            return [f"string {node.value!r}"]
    return []


def _keyword_violations(call: ast.Call) -> list[str]:
    found = []
    for kw in call.keywords:
        is_get = isinstance(kw.value, ast.Constant) and kw.value.value == "GET"
        if kw.arg == "method" and not is_get:
            found.append("non-GET method")
        if kw.arg == "data":
            found.append("request body")
    return found


def _call_violations(node) -> list[str]:
    if not isinstance(node, ast.Call):
        return []
    name = getattr(node.func, "attr", getattr(node.func, "id", ""))
    return _keyword_violations(node) if name in {"urlopen", "Request"} else []


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        found += _string_violation(node) + _call_violations(node)
    return found


def test_module_is_get_only():
    assert _violations(Path(lib.__file__).read_text()) == []


def test_ast_guard_fails_when_a_write_method_is_planted():
    planted = 'import urllib.request\nurllib.request.Request(u, method="POST")\n'
    assert _violations(planted)
    assert _violations('x = "DELETE"')
    assert _violations("Request(u, method=m)")
    assert _violations("urlopen(u, data=b'x')")


def test_every_request_is_counted_as_a_get():
    """LibrarySync.status() and the live run read `gets` (c28): it counts each request."""

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        def read(self, n=-1):
            body, self._body = self._body, b""
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def __init__(self):
            self.methods = []

        def open(self, request, timeout=None):
            self.methods.append(request.get_method())
            return _Resp(b'{"items": []}' if "file" not in request.full_url else b"x")

    opener = _Opener()
    c = lib.LibraryClient("http://127.0.0.1:8780", "k", opener=opener)
    assert c.gets == 0
    c.list_items()
    c.fetch_file(1)
    assert c.gets == 2
    assert opener.methods == ["GET", "GET"]
