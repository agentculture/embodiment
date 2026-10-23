"""Vector cache + gemma2-embed client. Synthetic data only; a fake transport, never the gateway."""

import json
import os
import stat
import time

import pytest

from embodiment import vectors
from embodiment.vectors import EmbedderClient, VectorCache

MODEL = "gemma2-embed"


def _ok(vec):
    return json.dumps({"data": [{"embedding": vec}]}).encode()


class FakeTransport:
    def __init__(self, reply=None, error=None):
        self.reply = reply if reply is not None else _ok([0.1, 0.2])
        self.error = error
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), json.loads(body), timeout))
        if self.error:
            raise self.error
        return self.reply


# -- cache ---------------------------------------------------------------


def test_cache_file_is_0600_and_roundtrips(tmp_path):
    cache = VectorCache(tmp_path / "d", MODEL)
    cache.put("alpha text", [1.0, 2.0], record_id="r1")
    path = cache.path
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert VectorCache(tmp_path / "d", MODEL).get("alpha text") == [1.0, 2.0]
    assert cache.get("other text") is None


def test_cache_never_returns_entry_for_another_model(tmp_path):
    VectorCache(tmp_path, "model-a").put("same text", [9.0], record_id="r1")
    assert VectorCache(tmp_path, "model-b").get("same text") is None
    assert VectorCache(tmp_path, "model-a").get("same text") == [9.0]


def test_cache_foreign_model_entry_under_our_key_is_ignored(tmp_path):
    cache = VectorCache(tmp_path, MODEL)
    cache.put("t", [1.0], record_id="r1")
    data = json.loads(cache.path.read_text())
    for entry in data["entries"].values():
        entry["model"] = "other"
    cache.path.write_text(json.dumps(data))
    assert VectorCache(tmp_path, MODEL).get("t") is None


def test_drop_removes_entry(tmp_path):
    cache = VectorCache(tmp_path, MODEL)
    cache.put("one", [1.0], record_id="r1")
    cache.put("two", [2.0], record_id="r2")
    assert cache.drop("r1") == 1
    reread = VectorCache(tmp_path, MODEL)
    assert reread.get("one") is None
    assert reread.get("two") == [2.0]
    assert cache.drop("missing") == 0


def test_corrupt_cache_degrades_to_empty_and_is_recorded(tmp_path):
    (tmp_path / vectors.CACHE_FILENAME).write_text("{not json")
    cache = VectorCache(tmp_path, MODEL)
    assert cache.get("x") is None
    assert cache.degradations


def test_unwritable_dir_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = VectorCache(blocker / "sub", MODEL)
    cache.put("x", [1.0], record_id="r")
    assert cache.get("x") == [1.0]  # in-memory still serves
    assert cache.degradations


# -- client --------------------------------------------------------------


def test_embed_posts_role_named_model_and_one_request():
    tr = FakeTransport()
    client = EmbedderClient("http://gw:8001/", api_key="k", transport=tr)
    res = client.embed("שלום")
    assert res.vector == [0.1, 0.2] and res.online
    assert len(tr.calls) == 1
    url, headers, payload, _ = tr.calls[0]
    assert url == "http://gw:8001/v1/embeddings"
    assert payload["model"] == "gemma2-embed" and payload["input"] == "שלום"
    assert headers["Authorization"] == "Bearer k"
    assert "prompt_name" not in payload


def test_prompt_names_are_validated():
    tr = FakeTransport()
    client = EmbedderClient("http://gw", transport=tr)
    client.embed("q", prompt="Retrieval-query")
    assert tr.calls[0][2]["prompt_name"] == "Retrieval-query"
    res = client.embed("q", prompt="Query")  # the lane refuses this; never sent
    assert res.vector is None and len(tr.calls) == 1


def test_unreachable_is_offline_with_reason_not_raised():
    tr = FakeTransport(error=OSError("connection refused"))
    client = EmbedderClient("http://gw", api_key="secretkey", transport=tr)
    res = client.embed("x")
    assert res.vector is None and not res.online and res.reason
    assert "secretkey" not in res.reason
    assert client.state()["online"] is False
    assert client.transitions and client.transitions[-1][0] == "offline"


@pytest.mark.parametrize(
    "reply", [b"not json", b"{}", json.dumps({"data": [{"embedding": "x"}]}).encode()]
)
def test_malformed_reply_is_offline(reply):
    client = EmbedderClient("http://gw", transport=FakeTransport(reply=reply))
    res = client.embed("x")
    assert res.vector is None and res.reason


def test_state_comes_from_last_real_call_and_transitions_recorded():
    tr = FakeTransport()
    client = EmbedderClient("http://gw", transport=tr)
    assert client.state()["online"] is None
    client.embed("a")
    assert client.state()["online"] is True
    tr.error = OSError("down")
    client.embed("b")
    tr.error = None
    client.embed("c")
    assert [t[0] for t in client.transitions] == ["online", "offline", "online"]


def test_reprobe_is_bounded_and_only_when_stale():
    now = [100.0]
    tr = FakeTransport(error=OSError("down"))
    client = EmbedderClient(
        "http://gw", transport=tr, clock=lambda: now[0], reprobe_interval=30.0, spawn=lambda f: f()
    )
    client.embed("a")
    assert len(tr.calls) == 1
    assert client.maybe_reprobe() is False  # fresh
    assert len(tr.calls) == 1
    now[0] += 31
    tr.error = None
    assert client.maybe_reprobe() is True  # stale + offline -> probe
    assert len(tr.calls) == 2 and client.state()["online"] is True
    assert client.maybe_reprobe() is False  # fresh again, and online


def test_reprobe_not_reentrant():
    now = [0.0]
    started = []
    client = EmbedderClient(
        "http://gw", transport=FakeTransport(), clock=lambda: now[0], spawn=started.append
    )
    now[0] += 1000
    assert client.maybe_reprobe() is True
    assert client.maybe_reprobe() is False  # one in flight
    started[0]()
    assert client.maybe_reprobe() is False  # just probed


def test_default_spawn_runs_in_daemon_thread():
    tr = FakeTransport()
    now = [0.0]
    client = EmbedderClient("http://gw", transport=tr, clock=lambda: now[0])
    now[0] += 1000
    assert client.maybe_reprobe() is True
    for _ in range(100):
        if tr.calls:
            break
        time.sleep(0.01)
    assert len(tr.calls) == 1
