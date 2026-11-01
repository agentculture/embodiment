"""The persona, model and conversation-setting routes (t14), over a real socket.

Two kinds of test: the routes against fake ``Controls`` (shape, status, guard), and
the same routes against a REAL ``DaemonApp``'s ``controls()`` (the setting lands in
the conversation, a refusal changes nothing). Every route is proved refused without
the secret and allowed with a valid Access assertion on the public host, and the
configured secret never appears in a response body.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

import pytest

from embodiment.daemon import app as app_module
from embodiment.http import server as s
from embodiment.personas import BUILTIN_ID
from tests import test_daemon_app as base
from tests import test_daemon_models as models
from tests.test_http_server import (
    MARKER_GATEWAY_KEY,
    MARKER_SECRET,
    PUBLIC_HOST,
    Harness,
    _Asserted,
    build,
)

harness = base.harness

PERSONA = {"id": "ada", "name": "Ada", "language": "en", "text": "otters"}


def _fake_controls(calls: list[Any]) -> s.Controls:
    def personas() -> dict[str, Any]:
        return {"personas": [{"id": "gwen", "name": "Gwen"}], "builtin_id": "gwen"}

    def create(fields: dict[str, Any]) -> dict[str, Any]:
        calls.append(("create", fields))
        return {"code": "bad_name"} if fields.get("name") == "" else {"persona": PERSONA}

    def update(pid: str, fields: dict[str, Any]) -> dict[str, Any]:
        calls.append(("update", pid, fields))
        return {"code": "builtin_read_only"} if pid == "gwen" else {"persona": PERSONA}

    def delete(pid: str) -> dict[str, Any]:
        calls.append(("delete", pid))
        return {"code": "not_found"} if pid == "nope" else {"deleted": pid}

    def models_() -> dict[str, Any]:
        return {"roles": [], "stale": False, "age_s": 1.0, "conversation_role": "senses"}

    def settings(*args: Any) -> dict[str, Any]:
        calls.append(("settings", *args))
        persona = args[1]
        return {"code": "unknown_persona"} if persona == "ghost" else {"code": None}

    def chat(message: Any, conversation: Optional[str] = None) -> dict[str, Any]:
        calls.append(("chat", message, conversation))
        return {"ok": True, "reply": "r", "sources": [], "turn_serial": 1, "code": ""}

    def clip(audio: Any, duration: Any, *rest: Any) -> dict[str, Any]:
        calls.append(("clip", len(audio), *rest))
        return {"ok": True, "transcript": "t", "reply": "r", "sources": [], "turn_serial": 1}

    def new() -> dict[str, Any]:
        calls.append(("new",))
        return {"started": True, "ended": "x"}

    return s.Controls(
        personas=personas,
        persona_create=create,
        persona_update=update,
        persona_delete=delete,
        models=models_,
        conversation_settings=settings,
        chat=chat,
        clip=clip,
        new_conversation=new,
    )


@pytest.fixture
def fake() -> Iterator[tuple[Harness, list[Any]]]:
    calls: list[Any] = []
    built = build(controls=_fake_controls(calls))
    try:
        yield built, calls
    finally:
        built.server.shutdown(2.0)


def _send(h: Harness, method: str, path: str, payload: Any = None, **hdr: Optional[str]) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    headers = h.headers(Content_Type="application/json", **hdr)
    status, raw = h.request(method, path, body=body, hdrs=headers)
    return status, json.loads(raw) if raw else {}


ROUTES = [
    ("GET", "/api/personas", None),
    ("POST", "/api/personas", {"name": "Ada"}),
    ("PUT", "/api/personas/ada", {"name": "Ada"}),
    ("DELETE", "/api/personas/ada", None),
    ("GET", "/api/models", None),
]


class TestGuard:
    @pytest.mark.parametrize("method,path,payload", ROUTES)
    def test_refused_without_the_secret(self, fake: Any, method: str, path: str, payload: Any):
        built, calls = fake
        status, _ = _send(built, method, path, payload, Authorization=None)
        assert status == 401
        assert calls == []

    @pytest.mark.parametrize("method,path,payload", ROUTES)
    def test_refused_from_a_foreign_origin(self, fake: Any, method: str, path: str, payload: Any):
        built, calls = fake
        status, _ = _send(built, method, path, payload, Origin="http://evil.example")
        assert status in (401, 403)
        assert calls == []

    @pytest.mark.parametrize("method,path,payload", ROUTES)
    def test_allowed_with_access_on_the_public_host(self, method: str, path: str, payload: Any):
        calls: list[Any] = []
        verifier = lambda token: _Asserted(valid=True, expires_at=None)  # noqa: E731
        built = build(
            controls=_fake_controls(calls), public_hostname=PUBLIC_HOST, assertion_verifier=verifier
        )
        try:
            hdrs = built.headers(
                Host=PUBLIC_HOST,
                Authorization=None,
                Content_Type="application/json",
                Cf_Access_Jwt_Assertion="SYNTHETIC-TOKEN",
            )
            body = None if payload is None else json.dumps(payload).encode()
            status, _ = built.request(method, path, body=body, hdrs=hdrs)
            assert status == 200
            bad = {**hdrs, "Cf-Access-Jwt-Assertion": ""}
            bad.pop("Cf-Access-Jwt-Assertion")
            status, _ = built.request(method, path, body=body, hdrs=bad)
            assert status in (401, 403)
        finally:
            built.server.shutdown(2.0)

    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("POST", "/api/chat", {"message": "hi", "persona": "ada"}),
            ("POST", "/api/conversation/new", {"persona": "ada"}),
        ],
    )
    def test_the_setting_carrying_routes_stay_refused_without_the_secret(
        self, fake: Any, method: str, path: str, payload: Any
    ):
        built, calls = fake
        status, _ = _send(built, method, path, payload, Authorization=None)
        assert status == 401
        assert calls == []


class TestPersonaRoutes:
    def test_list(self, fake: Any) -> None:
        built, _ = fake
        status, body = _send(built, "GET", "/api/personas")
        assert status == 200
        assert body["builtin_id"] == "gwen"
        assert body["personas"]

    def test_create_update_delete(self, fake: Any) -> None:
        built, calls = fake
        assert _send(built, "POST", "/api/personas", {"name": "Ada"})[1] == {"persona": PERSONA}
        assert _send(built, "PUT", "/api/personas/ada", {"name": "B"})[0] == 200
        assert _send(built, "DELETE", "/api/personas/ada")[1] == {"deleted": "ada"}
        assert calls == [
            ("create", {"name": "Ada"}),
            ("update", "ada", {"name": "B"}),
            ("delete", "ada"),
        ]

    @pytest.mark.parametrize(
        "method,path,payload,status,code",
        [
            ("POST", "/api/personas", {"name": ""}, 400, "bad_name"),
            ("PUT", "/api/personas/gwen", {"name": "x"}, 409, "builtin_read_only"),
            ("DELETE", "/api/personas/nope", None, 404, "not_found"),
        ],
    )
    def test_refusals_carry_the_persona_error_code(
        self, fake: Any, method: str, path: str, payload: Any, status: int, code: str
    ) -> None:
        built, _ = fake
        got, body = _send(built, method, path, payload)
        assert got == status
        assert body["error"]["code"] == code

    def test_a_non_object_body_is_400(self, fake: Any) -> None:
        built, calls = fake
        status, _ = _send(built, "POST", "/api/personas", ["x"])
        assert status == 400
        assert calls == []

    def test_an_oversized_body_is_413(self) -> None:
        built = build(controls=_fake_controls([]), text_body_max_bytes=64)
        try:
            status, _ = _send(built, "POST", "/api/personas", {"name": "x" * 500})
            assert status == 413
        finally:
            built.server.shutdown(2.0)

    def test_wrong_methods_are_405(self, fake: Any) -> None:
        built, _ = fake
        assert _send(built, "PUT", "/api/personas", {})[0] == 405
        assert _send(built, "POST", "/api/models", {})[0] == 405
        assert _send(built, "GET", "/api/personas/ada")[0] == 405

    def test_unbound_is_503_not_a_crash(self) -> None:
        built = build(controls=s.Controls())
        try:
            assert _send(built, "GET", "/api/personas")[0] == 503
            assert _send(built, "POST", "/api/personas", {"name": "a"})[0] == 503
        finally:
            built.server.shutdown(2.0)


class TestSettingsOnTheTextRoutes:
    def test_chat_applies_the_settings_before_the_turn(self, fake: Any) -> None:
        built, calls = fake
        body = {"message": "hi", "persona": "ada", "model": "worker", "language": "en"}
        assert _send(built, "POST", "/api/chat", body)[0] == 200
        assert calls == [("settings", None, "ada", "worker", "en", True), ("chat", "hi", None)]

    def test_a_refused_setting_is_a_400_and_no_turn_runs(self, fake: Any) -> None:
        built, calls = fake
        status, body = _send(built, "POST", "/api/chat", {"message": "hi", "persona": "ghost"})
        assert status == 400
        assert body["error"]["code"] == "unknown_persona"
        assert [c[0] for c in calls] == ["settings"]

    def test_a_non_string_setting_is_a_400_never_ignored(self, fake: Any) -> None:
        built, calls = fake
        status, _ = _send(built, "POST", "/api/chat", {"message": "hi", "language": 5})
        assert status == 400
        assert calls == []

    def test_chat_without_settings_does_not_touch_them(self, fake: Any) -> None:
        built, calls = fake
        _send(built, "POST", "/api/chat", {"message": "hi", "conversation": "c1"})
        assert calls == [("chat", "hi", "c1")]

    def test_clip_headers(self, fake: Any) -> None:
        built, calls = fake
        headers = built.headers(
            Content_Type="audio/wav",
            X_Persona="ada",
            X_Model="worker",
            X_Language="he",
            X_Conversation="c1",
        )
        status, _ = built.request("POST", "/api/clip", body=b"RIFF", hdrs=headers)
        assert status == 200
        assert calls[0] == ("settings", "c1", "ada", "worker", "he", True)
        assert calls[1][0] == "clip"

    def test_clip_refusal(self, fake: Any) -> None:
        built, calls = fake
        headers = built.headers(Content_Type="audio/wav", X_Persona="ghost")
        status, raw = built.request("POST", "/api/clip", body=b"RIFF", hdrs=headers)
        assert status == 400
        assert json.loads(raw)["error"]["code"] == "unknown_persona"
        assert [c[0] for c in calls] == ["settings"]

    def test_new_conversation_validates_then_ends_then_applies(self, fake: Any) -> None:
        built, calls = fake
        status, _ = _send(built, "POST", "/api/conversation/new", {"persona": "ada"})
        assert status == 200
        assert calls == [
            ("settings", None, "ada", None, None, False),
            ("new",),
            ("settings", None, "ada", None, None, True),
        ]

    def test_new_conversation_without_a_body_is_as_before(self, fake: Any) -> None:
        built, calls = fake
        assert _send(built, "POST", "/api/conversation/new")[0] == 200
        assert calls == [("new",)]

    def test_a_refused_new_conversation_setting_ends_nothing(self, fake: Any) -> None:
        built, calls = fake
        status, body = _send(built, "POST", "/api/conversation/new", {"persona": "ghost"})
        assert status == 400
        assert body["error"]["code"] == "unknown_persona"
        assert ("new",) not in calls


# ── against a real DaemonApp ─────────────────────────────────────────────────


@pytest.fixture
def live(
    harness: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Any, Harness]]:
    gateway = models.Gateway()
    monkeypatch.setattr(app_module.urllib.request, "urlopen", gateway)
    h = models._wired(harness, tmp_path)
    built = build(controls=h.app.controls())
    try:
        yield h, built
    finally:
        built.server.shutdown(2.0)


class TestAgainstTheDaemon:
    def test_persona_crud_through_the_store(self, live: Any) -> None:
        h, built = live
        status, body = _send(built, "POST", "/api/personas", PERSONA)
        assert status == 200
        assert body["persona"]["id"] == "ada"
        assert (
            _send(built, "PUT", "/api/personas/ada", {"name": "Ada2"})[1]["persona"]["name"]
            == "Ada2"
        )
        listing = _send(built, "GET", "/api/personas")[1]
        assert [p["id"] for p in listing["personas"]] == [listing["builtin_id"], "ada"]
        assert (
            _send(built, "PUT", f"/api/personas/{listing['builtin_id']}", {"name": "x"})[0] == 409
        )
        assert _send(built, "DELETE", "/api/personas/ada")[0] == 200
        assert _send(built, "DELETE", "/api/personas/ada")[0] == 404
        assert _send(built, "POST", "/api/personas", {"name": "a", "language": "xx"})[0] == 400
        assert _send(built, "POST", "/api/personas", {"persona_id": "z", "name": "a"})[0] == 400

    def test_models_snapshot_has_the_voice_state(self, live: Any) -> None:
        h, built = live
        status, body = _send(built, "GET", "/api/models")
        assert status == 200
        by_role = {r["role"]: r for r in body["roles"]}
        assert by_role["worker"]["voice"] == "unmeasured"
        assert by_role["worker"]["voice_p90_s"] is None
        assert body["stale"] is False
        assert body["age_s"] == 1.0
        assert body["conversation_role"] == "senses"

    def test_chat_settings_land_and_a_refusal_changes_nothing(self, live: Any) -> None:
        h, built = live
        _send(built, "POST", "/api/personas", PERSONA)
        for bad in ({"persona": "ghost"}, {"model": "nope"}, {"language": "fr"}):
            status, body = _send(built, "POST", "/api/chat", {"message": "hi", **bad})
            assert status == 400, bad
            assert body["error"]["code"] in {"unknown_persona", "unknown_model", "bad_language"}
        assert h.app.conversation_persona_id() == BUILTIN_ID
        assert h.app.conversation_language() is None
        good = {"message": "hi", "persona": "ada", "model": "worker", "language": "he"}
        assert _send(built, "POST", "/api/chat", good)[0] == 200
        assert h.app.conversation_persona_id() == "ada"
        assert h.app.conversation_role() == "worker"
        assert h.app.conversation_language() == "he"

    def test_models_report_the_live_conversations_choices_for_a_reload(self, live: Any) -> None:
        h, built = live
        fresh = _send(built, "GET", "/api/models")[1]["conversation"]
        assert fresh == {"persona": BUILTIN_ID, "model": None, "language": None}
        _send(built, "POST", "/api/personas", PERSONA)
        good = {"message": "hi", "persona": "ada", "model": "worker", "language": "en"}
        assert _send(built, "POST", "/api/chat", good)[0] == 200
        chosen = _send(built, "GET", "/api/models")[1]["conversation"]
        assert chosen == {"persona": "ada", "model": "worker", "language": "en"}

    def test_a_persona_alone_reports_no_chosen_model(self, live: Any) -> None:
        _, built = live
        _send(built, "POST", "/api/personas", PERSONA)
        assert _send(built, "POST", "/api/chat", {"message": "hi", "persona": "ada"})[0] == 200
        choice = _send(built, "GET", "/api/models")[1]["conversation"]
        assert choice["persona"] == "ada"
        assert choice["model"] is None  # the persona's own default role applies

    def test_a_setting_for_an_unknown_conversation_is_a_404(self, live: Any) -> None:
        _, built = live
        status, body = _send(
            built, "POST", "/api/chat", {"message": "hi", "conversation": "nope", "persona": "gwen"}
        )
        assert status == 404

    def test_new_conversation_settings_apply_to_the_new_one(self, live: Any) -> None:
        h, built = live
        _send(built, "POST", "/api/personas", PERSONA)
        h.app.controls().chat("first")
        old = h.app.conversation_persona_id()
        status, _ = _send(
            built, "POST", "/api/conversation/new", {"persona": "ada", "language": "none"}
        )
        assert status == 200
        assert old != "ada"
        assert h.app.conversation_persona_id() == "ada"
        assert h.app.conversation_language() == "none"

    def test_status_reports_snapshot_roles_and_each_conversations_settings(self, live: Any) -> None:
        h, built = live
        _send(built, "POST", "/api/personas", PERSONA)
        _send(built, "POST", "/api/chat", {"message": "hi", "persona": "ada", "model": "worker"})
        status, body = _send(built, "GET", "/api/status")
        assert status == 200
        m = body["daemon"]["models"]
        assert m["age_s"] == 1.0
        assert [r["role"] for r in m["roles"]] == ["senses", "worker"]
        assert "voice_latency" in m
        (live_conv,) = [c for c in m["conversations"] if c["live"]]
        assert live_conv["persona"] == "ada"
        assert live_conv["role"] == "worker"

    def test_the_language_override_composes_with_a_copy_and_is_recorded(self, live: Any) -> None:
        h, built = live
        _send(built, "POST", "/api/personas", PERSONA)
        _send(built, "POST", "/api/chat", {"message": "hi", "persona": "ada", "language": "he"})
        assert h.app.personas.get("ada").language == "en"  # the stored persona is untouched
        records = models._assistant_records(h.state)
        assert records[-1]["persona"] == "ada"
        assert records[-1]["language"] == "he"

    def test_no_secret_or_key_is_in_any_response(self, live: Any) -> None:
        h, built = live
        seen = []
        for method, path, payload in ROUTES + [("GET", "/api/status", None)]:
            raw = built.request(
                method,
                path,
                body=None if payload is None else json.dumps(payload).encode(),
                hdrs=built.headers(Content_Type="application/json"),
            )[1].decode()
            seen.append(raw)
        for text in seen:
            assert MARKER_SECRET not in text
            assert MARKER_GATEWAY_KEY not in text
