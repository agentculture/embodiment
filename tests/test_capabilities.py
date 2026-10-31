"""CapabilitiesPoller: the chat roles lobes serves, read over the network only (t2)."""

from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from embodiment import capabilities
from embodiment.capabilities import POLL_INTERVAL_SECONDS, CapabilitiesPoller

CHAT = "/v1/chat/completions"

ADVERT = {
    "senses": {"role": "senses", "model": "m-a", "path": CHAT, "ready": True},
    "cortex": {
        "role": "cortex",
        "model": "m-b",
        "path": CHAT,
        "ready": True,
        "member": "spark2",
        "hosted_by": "http://spark2:8001",
    },
    "worker": {
        "role": "worker",
        "model": "m-c",
        "path": CHAT,
        "ready": True,
        "hosted_by": "http://thor",
    },
    "muse": {"role": "muse", "model": "m-d", "path": CHAT, "ready": False},
    "gemma2-embed": {"model": "e", "path": "/v1/embeddings", "ready": True, "lane": True},
    "stt": {"model": "w", "path": "/v1/audio/transcriptions", "ready": True},
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(advert, clock=None, degradations=None):
    state = {"body": advert}

    def fetch() -> str:
        body = state["body"]
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, str) else json.dumps(body)

    log = degradations if degradations is not None else []
    poller = CapabilitiesPoller(
        "http://gw", fetch=fetch, clock=clock or Clock(), on_degrade=lambda c, d: log.append(c)
    )
    return poller, state, log


def test_interval_is_named_60s_with_reason():
    assert POLL_INTERVAL_SECONDS == 60.0
    assert "roles" in capabilities.POLL_INTERVAL_REASON.lower()


def test_snapshot_before_any_poll_is_empty_and_stale():
    poller, _, _ = make(ADVERT)
    snap = poller.snapshot()
    assert snap.roles == ()
    assert snap.stale
    assert snap.age is None


def test_snapshot_lists_only_chat_roles_with_fields():
    clock = Clock()
    poller, _, log = make(ADVERT, clock)
    poller.poll_once()
    clock.now += 12.5
    snap = poller.snapshot()
    by = {r.role: r for r in snap.roles}
    assert set(by) == {"senses", "cortex", "worker", "muse"}
    assert by["senses"].host == "local"
    assert by["senses"].model == "m-a"
    assert by["senses"].ready
    assert by["cortex"].host == "spark2"
    assert by["worker"].host == "http://thor"
    assert by["muse"].ready is False
    assert snap.age == pytest.approx(12.5)
    assert not snap.stale
    assert log == []


def test_role_turning_not_ready_shows_next_snapshot():
    poller, state, _ = make(ADVERT)
    poller.poll_once()
    changed = json.loads(json.dumps(ADVERT))
    changed["senses"]["ready"] = False
    state["body"] = changed
    poller.poll_once()
    assert {r.role: r.ready for r in poller.snapshot().roles}["senses"] is False


@pytest.mark.parametrize(
    "bad",
    [OSError("down"), "not json", "[1]", TimeoutError("slow")],
)
def test_failed_poll_keeps_last_snapshot_marks_stale_records_degradation(bad):
    clock = Clock()
    poller, state, log = make(ADVERT, clock)
    poller.poll_once()
    state["body"] = bad
    clock.now += 61
    poller.poll_once()  # never raises
    snap = poller.snapshot()
    assert len(snap.roles) == 4
    assert snap.stale
    assert snap.age == pytest.approx(61)
    assert len(log) == 1


def test_role_identity_comes_from_key_not_model_name():
    advert = {
        "weird": {
            "role": "other",
            "model": "acme/embedding-whisper-tts",
            "path": CHAT,
            "ready": True,
        },
        "bad": "not a mapping",
        "nopath": {"model": "x", "ready": True},
        "badready": {"model": "x", "path": CHAT, "ready": "yes"},
    }
    poller, _, _ = make(advert)
    poller.poll_once()
    roles = {r.role: r for r in poller.snapshot().roles}
    assert set(roles) == {"weird", "badready"}
    assert roles["badready"].ready is False  # only a literal true counts


def test_no_code_derives_anything_from_a_model_id():
    tree = ast.parse(Path(capabilities.__file__).read_text())
    banned = {"lower", "upper", "split", "startswith", "endswith", "find", "match", "search"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in banned:
                assert "model" not in ast.dump(node.func.value).lower(), ast.unparse(node)
        if isinstance(node, ast.Compare):
            assert "model" not in ast.unparse(node).lower(), ast.unparse(node)


class _Handler(BaseHTTPRequestHandler):
    seen: list = []
    mode = "ok"

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/x")
            self.end_headers()
            return
        body = json.dumps(ADVERT).encode()
        if self.mode == "huge":
            body = b" " * (capabilities.MAX_BODY_BYTES + 10)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def gateway():
    _Handler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_port}", _Handler
    server.shutdown()
    server.server_close()


def test_real_http_fetch_with_bearer(gateway):
    url, h = gateway
    h.mode = "ok"
    poller = CapabilitiesPoller(url, api_key="k")
    poller.poll_once()
    assert len(poller.snapshot().roles) == 4
    assert h.seen == [("/capabilities", "Bearer k")]


@pytest.mark.parametrize("mode", ["redirect", "huge"])
def test_redirects_refused_and_body_bounded(gateway, mode):
    url, h = gateway
    h.mode = mode
    log = []
    poller = CapabilitiesPoller(url, on_degrade=lambda c, d: log.append(c))
    poller.poll_once()
    assert poller.snapshot().roles == ()
    assert len(log) == 1


def test_non_http_url_degrades_not_raises():
    log = []
    poller = CapabilitiesPoller("file:///etc", on_degrade=lambda c, d: log.append(c))
    poller.poll_once()
    assert len(log) == 1


def test_start_stop_background_thread():
    poller, _, _ = make(ADVERT)
    poller.start()
    poller.start()  # idempotent
    assert poller.wait_first_poll(5)
    poller.stop()
    assert len(poller.snapshot().roles) == 4


def test_a_failing_degrade_hook_is_counted_not_swallowed() -> None:
    """The host hook raising must not kill the poll, and must not vanish either."""

    def boom(code: str, detail: str) -> None:
        raise RuntimeError("hook down")

    def fail() -> str:
        raise OSError("gateway down")

    poller = CapabilitiesPoller("http://gateway.invalid", fetch=fail, on_degrade=boom)
    poller.poll_once()
    count, reason = poller.hook_failures
    assert count == 1
    assert "RuntimeError" in reason
