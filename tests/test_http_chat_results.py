"""``/api/chat``'s ``request_id`` and ``GET /api/chat/{request_id}`` (operator, 2026-10-31:
a typed answer outlives its HTTP request and is recoverable after the page was left)."""

from __future__ import annotations

import json
from typing import Any

from embodiment.http import server as s
from tests.test_http_server import build, post_json

RID = "req-0123456789abcdef"


def test_a_request_id_reaches_the_chat_callable() -> None:
    seen: list[tuple[Any, ...]] = []
    built = build(controls=s.Controls(chat=lambda *a: seen.append(a) or {"ok": True, "reply": "r"}))
    try:
        status, _ = post_json(built, "/api/chat", {"message": "q", "request_id": RID})
        assert status == 200
        assert seen == [("q", None, RID)]
    finally:
        built.server.shutdown(2.0)


def test_without_a_request_id_the_call_is_unchanged() -> None:
    seen: list[tuple[Any, ...]] = []
    built = build(controls=s.Controls(chat=lambda *a: seen.append(a) or {"ok": True, "reply": "r"}))
    try:
        post_json(built, "/api/chat", {"message": "q"})
        post_json(built, "/api/chat", {"message": "q", "conversation": "c1"})
        assert seen == [("q",), ("q", "c1")]
    finally:
        built.server.shutdown(2.0)


def test_a_malformed_request_id_is_a_400_before_the_callable() -> None:
    seen: list[Any] = []
    built = build(controls=s.Controls(chat=lambda *a: seen.append(a) or {"ok": True, "reply": "r"}))
    try:
        for bad in ("../x", 7, "a b", "x" * 200):
            status, body = post_json(built, "/api/chat", {"message": "q", "request_id": bad})
            assert status == 400
            assert json.loads(body)["code"] == s.BAD_REQUEST_CODE
        assert seen == []
    finally:
        built.server.shutdown(2.0)


def test_a_stored_result_is_served_by_get() -> None:
    stored = {"state": "done", "result": {"ok": True, "reply": "late", "sources": []}}
    asked: list[Any] = []
    built = build(controls=s.Controls(chat_result=lambda rid: asked.append(rid) or stored))
    try:
        status, body = built.request("GET", f"/api/chat/{RID}", hdrs=built.headers())
        assert status == 200
        assert json.loads(body) == stored
        assert asked == [RID]
    finally:
        built.server.shutdown(2.0)


def test_get_is_guarded_like_every_api_route() -> None:
    built = build(controls=s.Controls(chat_result=lambda rid: {"state": "unknown", "result": None}))
    try:
        status, _ = built.request("GET", f"/api/chat/{RID}", hdrs=built.headers(Authorization=None))
        assert status in (401, 403)
    finally:
        built.server.shutdown(2.0)


def test_a_malformed_id_in_the_path_is_404_and_post_is_405() -> None:
    built = build(controls=s.Controls(chat_result=lambda rid: {"state": "unknown", "result": None}))
    try:
        assert built.request("GET", "/api/chat/a%20b", hdrs=built.headers())[0] == 404
        assert built.request("GET", "/api/chat/x/y", hdrs=built.headers())[0] == 404
        assert built.request("POST", f"/api/chat/{RID}", hdrs=built.headers())[0] == 405
    finally:
        built.server.shutdown(2.0)


def test_an_unbound_result_store_is_a_503() -> None:
    built = build()
    try:
        assert built.request("GET", f"/api/chat/{RID}", hdrs=built.headers())[0] == 503
    finally:
        built.server.shutdown(2.0)


def test_a_bad_shape_from_the_store_is_a_502() -> None:
    built = build(controls=s.Controls(chat_result=lambda rid: {"nope": 1}))
    try:
        assert built.request("GET", f"/api/chat/{RID}", hdrs=built.headers())[0] == 502
    finally:
        built.server.shutdown(2.0)
