"""A typed answer outlives its HTTP request (operator, 2026-10-31: "A then C"; "It also
solves recovering an answer after user left page").

The page names each chat with a ``request_id``. The daemon keeps that request's result,
pending then done, in a small bounded store served by ``chat_result(request_id)``, and
tags the turn's ``reply`` event with it. A request the chat route abandoned on its own
clock (``http-control-timeout``) still finishes, and its answer is still fetchable.
"""

from __future__ import annotations

import threading
from typing import Any

from embodiment.contract import ModelResponse
from embodiment.daemon import app as app_module
from tests import test_daemon_app as base
from tests.test_daemon_app import REPLY

harness = base.harness

TYPED = "מה שלומך?"
RID = "req-0123456789abcdef"


def test_a_named_request_is_done_with_the_same_result_the_route_returned(harness: Any) -> None:
    h = harness()
    controls = h.app.controls()
    answer = controls.chat(TYPED, None, RID)
    assert answer["ok"] and answer["reply"] == REPLY
    stored = controls.chat_result(RID)
    assert stored["state"] == "done"
    for key in ("ok", "reply", "sources", "turn_serial", "code", "fallback"):
        assert stored["result"][key] == answer[key]


def test_a_request_is_pending_while_its_turn_runs(harness: Any) -> None:
    seen: dict[str, Any] = {}
    release = threading.Event()

    def complete(messages: Any, tools: Any = None) -> Any:
        seen["during"] = h.app.controls().chat_result(RID)
        release.wait(5)
        return ModelResponse(content=REPLY, prompt_tokens=1, completion_tokens=1)

    h = harness(complete=complete)
    worker = threading.Thread(target=lambda: h.app.controls().chat(TYPED, None, RID))
    worker.start()
    release.set()
    worker.join(10)
    assert seen["during"] == {"state": "pending", "result": None}
    assert h.app.controls().chat_result(RID)["state"] == "done"


def test_an_unknown_or_malformed_request_id_is_unknown(harness: Any) -> None:
    h = harness()
    assert h.app.controls().chat_result("req-never-seen-000") == {
        "state": "unknown",
        "result": None,
    }
    for bad in ("", "../x", "a b", "x" * 200, None, 7, RID + "\n"):
        assert h.app.controls().chat_result(bad) == {"state": "unknown", "result": None}


def test_a_malformed_request_id_is_not_stored_but_the_turn_still_answers(harness: Any) -> None:
    h = harness()
    answer = h.app.controls().chat(TYPED, None, "../evil")
    assert answer["ok"]
    assert h.app.controls().chat_result("../evil")["state"] == "unknown"


def test_the_reply_event_carries_the_request_id(harness: Any) -> None:
    h = harness()
    h.app.controls().chat(TYPED, None, RID)
    replies = [e for e in h.events("reply") if e.data.get("typed")]
    assert replies and replies[-1].data["request_id"] == RID


def test_a_chat_without_a_request_id_is_unchanged(harness: Any) -> None:
    h = harness()
    assert h.app.controls().chat(TYPED)["ok"]
    replies = [e for e in h.events("reply") if e.data.get("typed")]
    assert "request_id" not in replies[-1].data


def test_the_store_is_bounded_oldest_first(harness: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(app_module, "MAX_CHAT_RESULTS", 2)
    h = harness()
    for n in range(3):
        h.app.controls().chat(TYPED, None, f"req-{n:016d}")
    states = [h.app.controls().chat_result(f"req-{n:016d}")["state"] for n in range(3)]
    assert states == ["unknown", "done", "done"]
