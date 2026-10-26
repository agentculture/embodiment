"""RetrieveClient: the one POST to /api/rag/retrieve; a local http.server fake, synthetic data."""

import ast
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from embodiment.library import retrieve as rt
from embodiment.library.client import LibraryError
from embodiment.library.retrieve import Passage, RetrieveClient

KEY = "rag_SECRETKEYVALUE123"


def _passage(n, kind="note", item_id=1, score=0.02):
    return {
        "item_id": item_id,
        "title": f"Synthetic {n}",
        "kind": kind,
        "chunk_no": n,
        "chunk_sha": f"{n:02x}" * 32,
        "text": f"synthetic passage {n}",
        "score": score,
    }


class _Fake:
    """Programmable fake: set .status/.body/.headers/.delay before a call."""

    def __init__(self):
        self.status = 200
        self.body = json.dumps({"query": "q", "mode": "hybrid", "reranked": False, "passages": []})
        self.headers = {}
        self.delay = 0.0
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                outer.seen.append((self.command, self.path, self.headers.get("Authorization"), raw))
                if outer.delay:
                    time.sleep(outer.delay)
                data = outer.body if isinstance(outer.body, bytes) else outer.body.encode()
                try:
                    self.send_response(outer.status)
                    for k, v in outer.headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            do_POST = do_GET = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, passages):
        self.body = json.dumps(
            {"query": "q", "mode": "hybrid", "reranked": False, "passages": passages}
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = _Fake()
    yield f
    f.close()


def _client(fake, deadline=2.0):
    return RetrieveClient(fake.url, KEY, deadline=deadline)


def _code(call):
    with pytest.raises(LibraryError) as e:
        call()
    assert KEY not in str(e.value)
    assert KEY not in repr(e.value)
    return e.value.code


# --- 1: the call --------------------------------------------------------------


def test_retrieve_posts_hybrid_no_rerank_with_bearer(fake):
    fake.answer([_passage(1), _passage(2, item_id=9)])
    out = _client(fake).retrieve("hello there", 5, item_ids=[1, 9])
    method, path, auth, raw = fake.seen[0]
    assert (method, path, auth) == ("POST", "/api/rag/retrieve", f"Bearer {KEY}")
    body = json.loads(raw)
    assert body["query"] == "hello there"
    assert body["k"] == 5
    assert body["mode"] == "hybrid"
    assert body["rerank"] is False
    assert body["item_ids"] == [1, 9]
    assert out[0] == Passage(
        item_id=1,
        title="Synthetic 1",
        kind="note",
        chunk_no=1,
        chunk_sha="01" * 32,
        text="synthetic passage 1",
        score=0.02,
    )
    assert [p.item_id for p in out] == [1, 9]


def test_no_floor_on_hybrid_scores(fake):
    fake.answer([_passage(1, score=0.011), _passage(2, score=0.0001)])
    assert len(_client(fake).retrieve("q", 5)) == 2


def test_item_ids_omitted_when_not_given(fake):
    _client(fake).retrieve("q", 3)
    assert "item_ids" not in json.loads(fake.seen[0][3])


def test_env_names_match_client():
    from embodiment.library import client

    assert (rt.ENV_URL, rt.ENV_KEY) == (client.ENV_URL, client.ENV_KEY)
    assert rt.RetrieveClient.from_env({}) is None
    c = rt.RetrieveClient.from_env({rt.ENV_URL: "http://l:1", rt.ENV_KEY: KEY})
    assert c is not None
    assert KEY not in repr(c)


# --- 3: kind filtering --------------------------------------------------------


def test_todo_and_discord_message_dropped(fake):
    fake.answer(
        [
            _passage(1, kind="todo"),
            _passage(2, kind="discord-message"),
            _passage(3, kind="book"),
        ]
    )
    out = _client(fake).retrieve("q", 5)
    assert [p.kind for p in out] == ["book"]


def test_kind_is_sent_in_the_body(fake):
    fake.answer([_passage(1, kind="book")])
    assert [p.kind for p in _client(fake).retrieve("q", 5, kind="book")] == ["book"]
    assert json.loads(fake.seen[0][3])["kind"] == "book"
    _client(fake).retrieve("q", 5)
    assert "kind" not in json.loads(fake.seen[1][3])


# --- 4: failures --------------------------------------------------------------


def test_connection_failure_is_library_unavailable():
    c = RetrieveClient("http://127.0.0.1:1", KEY, deadline=1.0)
    assert _code(lambda: c.retrieve("q", 3)) == "library_unavailable"


def test_503_rag_unavailable(fake):
    fake.status, fake.body = 503, json.dumps({"error": "rag_unavailable"})
    assert _code(lambda: _client(fake).retrieve("q", 3)) == "rag_unavailable"


@pytest.mark.parametrize("status", [401, 404, 500, 503])
def test_other_non_200_is_library_unavailable(fake, status):
    fake.status, fake.body = status, "nope"
    assert _code(lambda: _client(fake).retrieve("q", 3)) == "library_unavailable"


def test_redirect_refused(fake):
    fake.status, fake.headers = 302, {"Location": "http://127.0.0.1:1/x"}
    assert _code(lambda: _client(fake).retrieve("q", 3)) == "library_unavailable"
    assert len(fake.seen) == 1


def test_oversize_body(fake):
    fake.body = b"x" * (rt.MAX_RETRIEVE_BYTES + 10)
    assert _code(lambda: _client(fake).retrieve("q", 3)) == "library_bad_response"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        '{"passages": 3}',
        '{"passages": [3]}',
        '{"passages": [{"item_id": 1}]}',
    ],
)
def test_malformed_body(fake, body):
    fake.body = body
    assert _code(lambda: _client(fake).retrieve("q", 3)) == "library_bad_response"


def test_hang_past_deadline(fake):
    fake.delay = 1.0
    t0 = time.monotonic()
    assert _code(lambda: _client(fake, deadline=0.2).retrieve("q", 3)) == "library_timeout"
    assert time.monotonic() - t0 < 0.9


def test_declared_codes_only():
    assert set(rt.CODES) == {
        "library_unavailable",
        "rag_unavailable",
        "library_timeout",
        "library_bad_response",
    }


def test_bad_arguments_never_escape(fake):
    assert _code(lambda: _client(fake).retrieve("q", "x")) == "library_bad_response"
    assert _code(lambda: RetrieveClient("ftp://x", KEY, deadline=1).retrieve("q", 1)) == (
        "library_unavailable"
    )


# --- 5: counters --------------------------------------------------------------


def test_counters(fake):
    c = _client(fake)
    assert c.status() == {
        "calls": 0,
        "calls_ok": 0,
        "failures_by_code": {},
        "last_latency_s": None,
    }
    c.retrieve("q", 3)
    st = c.status()
    assert st["calls"] == 1
    assert st["calls_ok"] == 1
    assert st["failures_by_code"] == {}
    assert isinstance(st["last_latency_s"], float)
    assert st["last_latency_s"] >= 0
    fake.status, fake.body = 503, json.dumps({"error": "rag_unavailable"})
    with pytest.raises(LibraryError):
        c.retrieve("q", 3)
    fake.body = "junk"
    fake.status = 200
    with pytest.raises(LibraryError):
        c.retrieve("q", 3)
    st = c.status()
    assert st["calls"] == 3
    assert st["calls_ok"] == 1
    assert st["failures_by_code"] == {"rag_unavailable": 1, "library_bad_response": 1}
    st["failures_by_code"]["x"] = 1
    assert "x" not in c.status()["failures_by_code"]


# --- 2: AST pin ---------------------------------------------------------------


def _is_urllib_request_request(func) -> bool:
    """``urllib.request.Request`` spelled out in full: the only way the module may build one."""
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "Request"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "request"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "urllib"
    )


def _request_calls(tree):
    return [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_urllib_request_request(n.func)
    ]


def _alias_violations(tree) -> list[str]:
    """Anything that could build a request out of the pin's sight: a from-import or an alias
    of urllib.request, an assignment of urllib.request.Request, or a call named Request."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("urllib"):
            found.append(f"from {node.module} import")
        if isinstance(node, ast.Import):
            found += [f"import {a.name} as {a.asname}" for a in node.names if a.asname]
        if isinstance(node, ast.Call) and not _is_urllib_request_request(node.func):
            if getattr(node.func, "attr", getattr(node.func, "id", "")) in {"Request", "urlopen"}:
                found.append("a Request/urlopen call not spelled urllib.request.Request")
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
            if node.value.attr in {"Request", "urlopen", "build_opener", "OpenerDirector"}:
                found.append(f"alias of {node.value.attr}")
    return found


def _pin_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    found = _alias_violations(tree)
    calls = _request_calls(tree)
    if len(calls) != 1:
        found.append(f"{len(calls)} Request calls")
    for call in calls:
        kws = {k.arg: k.value for k in call.keywords}
        m = kws.get("method")
        if not (isinstance(m, ast.Constant) and m.value == "POST"):
            found.append("method is not the literal POST")
        if "data" not in kws:
            found.append("no body")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "urlopen":
            found.append("urlopen")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().upper() in {"GET", "PUT", "PATCH", "DELETE", "HEAD"}:
                found.append(f"string {node.value!r}")
    paths = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("/api")
    ]
    if paths != ["/api/rag/retrieve"]:
        found.append(f"paths {paths}")
    return found


def test_module_issues_exactly_one_post_to_the_literal_path():
    assert _pin_violations(Path(rt.__file__).read_text()) == []


def test_pin_fails_on_planted_changes():
    base = Path(rt.__file__).read_text()
    assert _pin_violations(base + '\nRequest(u, method="POST", data=b"")\n')
    assert _pin_violations(base.replace('method="POST"', 'method="PUT"'))
    assert _pin_violations(base.replace("/api/rag/retrieve", "/api/rag/other"))
    assert _pin_violations(base + '\nx = "GET"\n')
    # review of #107: aliases and from-imports cannot slip a second request past the pin
    assert _pin_violations(
        base + '\nfrom urllib.request import Request as R\nR(u, method="POST", data=b"")\n'
    )
    assert _pin_violations(base + "\nR = urllib.request.Request\n")
    assert _pin_violations(base + "\nimport urllib.request as ur\n")


def test_client_py_get_only_pin_unchanged():
    from tests import test_library_client as t

    assert t._violations(Path(t.lib.__file__).read_text()) == []
