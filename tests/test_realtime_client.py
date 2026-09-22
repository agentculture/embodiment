"""The lobes realtime ears — driven against a real socket, never a mock.

Every non-live test here runs a fake gateway in-process: a stdlib
``http.server`` for keyless ``GET /capabilities`` and a ``websockets``
server on ``127.0.0.1:0`` for ``/v1/realtime``. Real frames, real handshake
failures, real drops — just fast and hermetic.

No ``pytest-asyncio`` in this repo's dev set, so each async body runs under an
explicit ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import pytest
from websockets.asyncio.server import serve

from embodiment import safe_reason
from embodiment.realtime import client as rtc
from embodiment.realtime import wire

MARKER_KEY = "sk-planted-marker-7f3ab9-DO-NOT-LEAK"  # nosec B105 - test sentinel, not a secret

LIVE_GATEWAY = "http://localhost:8001"


# ── the fake gateway ─────────────────────────────────────────────────────────


def stt_advert(
    *, feasible: bool = True, realtime: bool = True, hosted_by: str | None = None
) -> dict[str, Any]:
    """An ``stt`` role entry shaped like `lobes/roles.py:999` ``role_payload``."""
    responsibilities = ["transcribe", "audio_input_to_text"]
    if realtime:
        responsibilities.append(wire.REALTIME_RESPONSIBILITY)
    entry: dict[str, Any] = {
        "role": "stt",
        "model": "ivrit-ai/whisper-large-v3-turbo",
        "runtime": "transformers",
        "endpoint": "http://127.0.0.1:0",
        "path": "/v1/audio/transcriptions",
        "responsibilities": responsibilities,
        "feasible": feasible,
        "ready": True,
        "loaded": True,
        "language": "he",
    }
    if hosted_by is not None:
        entry["hosted_by"] = hosted_by
    return entry


class _CapsHandler(BaseHTTPRequestHandler):
    payload: bytes = b"{}"
    status: int = 200
    #: A hostile HTTP reason phrase. `urllib` puts it straight into
    #: `HTTPError`'s message, which is how a server's text reaches a client's
    #: exception — the whole reason `describe_exception` exists.
    reason_phrase: str = ""
    seen_authorization: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler spelling
        type(self).seen_authorization.append(self.headers.get("Authorization"))
        if self.path.split("?")[0] != "/capabilities":
            self.send_error(404)
            return
        self.send_response(type(self).status, type(self).reason_phrase or None)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(type(self).payload)))
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *args: Any) -> None:  # silence the test log
        return


@dataclass
class Rig:
    """A fake gateway: keyless ``/capabilities`` plus a ``/v1/realtime`` socket."""

    caps_body: dict[str, Any] | str = field(default_factory=dict)
    caps_status: int = 200
    caps_reason: str = ""
    ws_reject: Callable[[Any, Any], Any] | None = None
    handler: Callable[[Any], Any] | None = None

    def __post_init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.raw_received: list[str] = []
        self.seen_authorization: list[str | None] = []
        self.seen_paths: list[str] = []

    # -- the HTTP half runs in a thread for the whole test ------------------
    def __enter__(self) -> "Rig":
        body = self.caps_body
        _CapsHandler.payload = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        _CapsHandler.status = self.caps_status
        _CapsHandler.reason_phrase = self.caps_reason
        _CapsHandler.seen_authorization = self.seen_authorization
        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _CapsHandler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()
        self.http_port = self._http.server_address[1]
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._thread.join(timeout=5)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    def config(self, **overrides: Any) -> rtc.RealtimeConfig:
        base: dict[str, Any] = {
            "gateway_url": self.origin,
            "api_key": MARKER_KEY,
            "discovery_deadline": 5.0,
            "handshake_deadline": 5.0,
            "close_deadline": 2.0,
        }
        base.update(overrides)
        return rtc.RealtimeConfig(**base)

    # -- the WebSocket half is per-async-block -----------------------------
    @contextlib.asynccontextmanager
    async def websocket(self):  # type: ignore[no-untyped-def]
        async def default_handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            async for raw in ws:
                self.raw_received.append(raw)
                with contextlib.suppress(Exception):
                    self.received.append(json.loads(raw))

        def process_request(connection: Any, request: Any) -> Any:
            self.seen_authorization.append(request.headers.get("Authorization"))
            self.seen_paths.append(request.path)
            if self.ws_reject is not None:
                return self.ws_reject(connection, request)
            return None

        async with serve(
            self.handler or default_handler,
            "127.0.0.1",
            0,
            process_request=process_request,
        ) as server:
            self.ws_port = server.sockets[0].getsockname()[1]
            yield server

    def ws_origin(self) -> str:
        return f"http://127.0.0.1:{self.ws_port}"


def FIXTURE(name: str) -> str:  # noqa: N802 - reads as a constant at the call site
    from pathlib import Path

    return (Path(__file__).resolve().parent / "fixtures" / "realtime" / name).read_text(
        encoding="utf-8"
    )


def capabilities(**kwargs: Any) -> dict[str, Any]:
    return {"cortex": {"role": "cortex"}, "stt": stt_advert(**kwargs)}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def codes(ears: rtc.RealtimeEars) -> list[str]:
    return [d.code for d in ears.degradations]


# ── discovery ────────────────────────────────────────────────────────────────


class TestDiscovery:
    def test_an_absent_stt_role_yields_exactly_one_record(self) -> None:
        with Rig(caps_body={"cortex": {"role": "cortex"}}) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.ADVERT_ABSENT]
            assert ears.connected is False and ears.degraded is True

    def test_an_stt_without_the_realtime_responsibility_is_an_absent_advert(self) -> None:
        with Rig(caps_body=capabilities(realtime=False)) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.ADVERT_ABSENT]

    def test_a_declared_off_lane_is_role_infeasible_and_names_the_peer(self) -> None:
        with Rig(
            caps_body=capabilities(feasible=False, hosted_by="http://thor.example:8000")
        ) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.ROLE_INFEASIBLE]
            assert "thor.example" in ears.degradations[0].reason

    def test_discovery_is_keyless(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config())
            run(ears.connect())
            assert rig.seen_authorization[0] is None

    def test_an_unreachable_gateway_yields_one_record(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]
        ears = rtc.RealtimeEars(
            rtc.RealtimeConfig(gateway_url=f"http://127.0.0.1:{dead_port}", api_key=MARKER_KEY)
        )
        assert run(ears.connect()) is False
        assert codes(ears) == [rtc.DISCOVERY_FAILED]

    def test_a_non_json_capabilities_body_yields_one_record(self) -> None:
        with Rig(caps_body="<html>a proxy login page</html>") as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.DISCOVERY_MALFORMED]

    def test_a_500_from_capabilities_yields_one_record(self) -> None:
        with Rig(caps_body={}, caps_status=500) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.DISCOVERY_FAILED]

    def test_a_json_array_capabilities_body_degrades(self) -> None:
        with Rig(caps_body="[1, 2, 3]") as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.DISCOVERY_MALFORMED]

    def test_a_dns_failure_yields_one_handshake_record(self) -> None:
        ears = rtc.RealtimeEars(
            rtc.RealtimeConfig(
                gateway_url="http://no-such-host.invalid:8001",
                api_key=MARKER_KEY,
                discovery_deadline=3.0,
            )
        )
        assert run(ears.connect()) is False
        assert codes(ears) == [rtc.DISCOVERY_FAILED]


# ── the handshake ────────────────────────────────────────────────────────────


def _reject(status: int, body: dict[str, Any]) -> Callable[[Any, Any], Any]:
    def process(connection: Any, request: Any) -> Any:
        return connection.respond(status, json.dumps(body))

    return process


class TestHandshakeRefusals:
    def _refused(self, reject: Callable[[Any, Any], Any]) -> rtc.RealtimeEars:
        with Rig(caps_body=capabilities(), ws_reject=reject) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    assert await ears.connect() is False
                    return ears

            return run(go())

    def test_404_role_infeasible_records_the_peer_origin(self) -> None:
        ears = self._refused(
            _reject(
                404,
                {
                    "error": {
                        "code": "role_infeasible",
                        "message": "not feasible on this machine",
                        "hosted_by": "http://thor.example:8000",
                    }
                },
            )
        )
        assert codes(ears) == [rtc.ROLE_INFEASIBLE]
        assert "http://thor.example:8000" in ears.degradations[0].reason

    def test_404_role_infeasible_without_a_peer_still_records_once(self) -> None:
        ears = self._refused(_reject(404, {"error": {"code": "role_infeasible"}}))
        assert codes(ears) == [rtc.ROLE_INFEASIBLE]

    def test_404_audio_not_configured(self) -> None:
        ears = self._refused(
            _reject(404, {"error": {"message": "realtime is not configured on this deployment"}})
        )
        assert codes(ears) == [rtc.AUDIO_NOT_CONFIGURED]

    def test_426_upgrade_required(self) -> None:
        ears = self._refused(_reject(426, {"error": {"message": "send an Upgrade handshake"}}))
        assert codes(ears) == [rtc.UPGRADE_REQUIRED]

    def test_401_unauthorized(self) -> None:
        ears = self._refused(_reject(401, {"error": {"message": "unauthorized"}}))
        assert codes(ears) == [rtc.UNAUTHORIZED]

    def test_an_unclassified_status_is_a_named_handshake_failure(self) -> None:
        ears = self._refused(_reject(503, {"error": {"message": "backend down"}}))
        assert codes(ears) == [rtc.HANDSHAKE_FAILED]

    def test_a_refusal_body_that_is_not_json_still_records_once(self) -> None:
        def process(connection: Any, request: Any) -> Any:
            return connection.respond(404, "<html>nope</html>")

        ears = self._refused(process)
        assert len(ears.degradations) == 1

    def test_a_socket_that_never_answers_hits_the_handshake_deadline(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            with Rig(caps_body=capabilities()) as rig:
                ears = rtc.RealtimeEars(
                    rig.config(realtime_url=f"http://127.0.0.1:{port}", handshake_deadline=0.35)
                )
                assert run(ears.connect()) is False
                assert codes(ears) == [rtc.HANDSHAKE_TIMEOUT]
        finally:
            listener.close()

    def test_a_dead_realtime_port_is_a_handshake_failure_not_a_timeout(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config(realtime_url=f"http://127.0.0.1:{dead}"))
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.HANDSHAKE_FAILED]


# ── a live-shaped session over a real socket ─────────────────────────────────


class TestSession:
    def test_connect_reaches_session_created_and_declares_the_config(self) -> None:
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> None:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    assert await ears.connect() is True
                    assert ears.connected is True
                    assert ears.degraded is False
                    first = await anext(aiter(ears.events()))
                    assert isinstance(first, wire.SessionCreated)
                    await ears.close()

            run(go())
            assert "aec_mode=aec" in rig.seen_paths[0]
            assert "language=he" in rig.seen_paths[0]
            assert "turn_detection=server_vad" in rig.seen_paths[0]

    def test_the_bearer_rides_the_handshake_header(self) -> None:
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> None:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    await ears.close()

            run(go())
            assert rig.seen_authorization[-1] == f"Bearer {MARKER_KEY}"

    def test_audio_arrives_as_base64_append_events(self) -> None:
        pcm = bytes(range(256)) * 8
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> None:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    ears.send_audio(pcm)
                    for _ in range(200):
                        if rig.received:
                            break
                        await asyncio.sleep(0.01)
                    await ears.close()

            run(go())
        assert rig.received, "no audio frame reached the server"
        event = rig.received[0]
        assert event["type"] == "input_audio_buffer.append"
        assert base64.b64decode(event["audio"], validate=True) == pcm

    def test_the_server_never_receives_an_arming_event(self) -> None:
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> None:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    ears.send_audio(b"\x00\x01" * 100)
                    await asyncio.sleep(0.15)
                    await ears.close()

            run(go())
        for raw in rig.raw_received:
            assert "response.create" not in raw
            assert "conversation.item.create" not in raw

    def test_a_server_error_event_is_recorded_and_still_delivered(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await ws.send(FIXTURE("error_vad_unavailable.json"))
            await asyncio.sleep(0.3)

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    seen = []
                    async for event in ears.events():
                        seen.append(event)
                        if isinstance(event, wire.ServerError):
                            break
                    await ears.close()
                    assert isinstance(seen[-1], wire.ServerError)
                    return ears

            ears = run(go())
        assert codes(ears) == [rtc.SERVER_ERROR]
        assert "vad_unavailable" in ears.degradations[0].reason

    def test_a_malformed_server_frame_records_once_and_the_session_survives(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await ws.send("{not json at all")
            await ws.send(FIXTURE("speech_started.json"))
            await asyncio.sleep(0.3)

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    seen = []
                    async for event in ears.events():
                        seen.append(event)
                        if isinstance(event, wire.SpeechStarted):
                            break
                    await ears.close()
                    assert isinstance(seen[-1], wire.SpeechStarted)
                    return ears

            ears = run(go())
        assert codes(ears) == [rtc.FRAME_MALFORMED]

    def test_a_mid_session_drop_records_once_and_leaves_the_caller_running(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await ws.close(code=1011, reason="bridge died")

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    seen = [event async for event in ears.events()]
                    # The iterator ENDED; it did not raise.
                    assert isinstance(seen[0], wire.SessionCreated)
                    assert ears.connected is False
                    # The caller is still running: it can still ask, and close.
                    assert ears.status()["connected"] is False
                    await ears.close()
                    return ears

            ears = run(go())
        assert codes(ears) == [rtc.SESSION_DROPPED]

    def test_send_audio_after_a_drop_is_recorded_not_raised(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await ws.close(code=1011, reason="bridge died")

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    async for _ in ears.events():
                        pass
                    assert ears.send_audio(b"\x00\x01" * 10) is False
                    await ears.close()
                    return ears

            ears = run(go())
        assert codes(ears).count(rtc.SESSION_DROPPED) == 1
        assert rtc.NOT_CONNECTED in codes(ears)

    def test_events_before_connect_is_an_empty_stream_not_a_crash(self) -> None:
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> rtc.RealtimeEars:
                ears = rtc.RealtimeEars(rig.config())
                seen = [e async for e in ears.events()]
                assert seen == []
                return ears

            ears = run(go())
        assert codes(ears) == [rtc.NOT_CONNECTED]


# ── backpressure ─────────────────────────────────────────────────────────────


class TestBackpressure:
    def test_a_stalled_socket_bounds_the_queue_and_counts_the_drops(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await asyncio.sleep(1.5)  # never reads: the writer will stall

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(
                        rig.config(realtime_url=rig.ws_origin(), max_queue_bytes=4800)
                    )
                    await ears.connect()
                    chunk = b"\x00" * 1600
                    for _ in range(200):
                        ears.send_audio(chunk)
                    assert ears.queued_bytes <= 4800
                    await ears.close()
                    return ears

            ears = run(go())
        assert ears.dropped_frames > 0
        assert ears.dropped_bytes >= ears.dropped_frames * 1600
        assert rtc.AUDIO_DROPPED in codes(ears)
        assert ears.status()["dropped_frames"] == ears.dropped_frames

    def test_the_drop_record_is_recorded_exactly_once_however_many_drops(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await asyncio.sleep(1.5)

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(
                        rig.config(realtime_url=rig.ws_origin(), max_queue_bytes=4800)
                    )
                    await ears.connect()
                    for _ in range(500):
                        ears.send_audio(b"\x00" * 1600)
                    await ears.close()
                    return ears

            ears = run(go())
        assert codes(ears).count(rtc.AUDIO_DROPPED) == 1
        assert ears.dropped_frames > 1

    def test_the_oldest_frame_is_the_one_dropped(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config(max_queue_bytes=40))
            ears._queue.append(b"oldest" + b"\x00" * 14)  # 20 bytes
            ears._queued_bytes = 20
            ears.send_audio(b"middle" + b"\x00" * 14)
            ears.send_audio(b"newest" + b"\x00" * 14)
            assert ears.queued_bytes <= 40
            assert not any(frame.startswith(b"oldest") for frame in ears._queue)
            assert any(frame.startswith(b"newest") for frame in ears._queue)

    def test_a_frame_larger_than_the_whole_bound_is_refused_not_looped(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config(max_queue_bytes=100))
            assert ears.send_audio(b"\x00" * 5000) is False
            assert ears.queued_bytes == 0

    def test_empty_and_non_bytes_audio_never_raise(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert ears.send_audio(b"") is True
            assert ears.send_audio("not bytes") is False  # type: ignore[arg-type]
            assert ears.send_audio(None) is False  # type: ignore[arg-type]
            assert rtc.AUDIO_NOT_BYTES in codes(ears)


# ── shutdown ─────────────────────────────────────────────────────────────────


class TestShutdown:
    def test_close_is_idempotent_and_reports_what_was_unfinished(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await asyncio.sleep(1.5)

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> tuple[Any, Any]:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    for _ in range(50):
                        ears.send_audio(b"\x00" * 1600)
                    first = await ears.close(deadline=1.0)
                    second = await ears.close(deadline=1.0)
                    return first, second

            first, second = run(go())
        assert first.queued_frames >= 0
        assert second.queued_frames == 0
        assert second.graceful is True  # already shut: nothing left to fail

    def test_close_without_a_connect_never_raises(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config())
            report = run(ears.close())
            assert report.graceful is True

    def test_close_returns_within_its_deadline_on_a_wedged_peer(self) -> None:
        """A peer that accepts TCP and never speaks: connect fails, close is fast."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            with Rig(caps_body=capabilities()) as rig:

                async def go() -> tuple[float, rtc.RealtimeEars]:
                    ears = rtc.RealtimeEars(
                        rig.config(realtime_url=f"http://127.0.0.1:{port}", handshake_deadline=0.3)
                    )
                    assert await ears.connect() is False
                    started = asyncio.get_running_loop().time()
                    report = await ears.close(deadline=0.2)
                    assert report.graceful is True
                    return asyncio.get_running_loop().time() - started, ears

                elapsed, ears = run(go())
                assert elapsed < 2.0
                assert codes(ears) == [rtc.HANDSHAKE_TIMEOUT]
        finally:
            listener.close()


# ── the secret ───────────────────────────────────────────────────────────────


class TestTheKeyNeverLeaks:
    def _all_text(self, ears: rtc.RealtimeEars) -> str:
        parts = [repr(ears), repr(ears.config), json.dumps(ears.status())]
        parts += [json.dumps(d.to_dict()) for d in ears.degradations]
        parts += [repr(d) for d in ears.degradations]
        return "\n".join(parts)

    @pytest.mark.parametrize(
        "case",
        ["absent", "infeasible", "unreachable", "malformed", "refused", "connected"],
    )
    def test_no_surface_carries_the_key(self, case: str) -> None:
        if case == "unreachable":
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                dead = probe.getsockname()[1]
            ears = rtc.RealtimeEars(
                rtc.RealtimeConfig(gateway_url=f"http://127.0.0.1:{dead}", api_key=MARKER_KEY)
            )
            run(ears.connect())
            assert MARKER_KEY not in self._all_text(ears)
            return

        bodies = {
            "absent": {"cortex": {}},
            "infeasible": capabilities(feasible=False),
            "malformed": "not json",
            "refused": capabilities(),
            "connected": capabilities(),
        }
        reject = _reject(401, {"error": {"message": "nope"}}) if case == "refused" else None
        with Rig(caps_body=bodies[case], ws_reject=reject) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    await ears.close()
                    return ears

            ears = run(go())
        assert MARKER_KEY not in self._all_text(ears)

    def test_a_key_echoed_back_by_a_hostile_server_is_redacted_from_the_record(self) -> None:
        def process(connection: Any, request: Any) -> Any:
            return connection.respond(
                503, json.dumps({"error": {"message": f"your key {MARKER_KEY} is stale"}})
            )

        with Rig(caps_body=capabilities(), ws_reject=process) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    return ears

            ears = run(go())
        assert MARKER_KEY not in self._all_text(ears)
        assert rtc.REDACTED in ears.degradations[0].reason

    def test_the_config_repr_hides_the_key(self) -> None:
        assert MARKER_KEY not in repr(rtc.RealtimeConfig(api_key=MARKER_KEY))

    def test_a_transcript_never_enters_a_degradation_record(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            await ws.send(FIXTURE("transcription_completed.json"))
            await ws.close(code=1011, reason="שלום גוון")

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    async for _ in ears.events():
                        pass
                    await ears.close()
                    return ears

            ears = run(go())
        assert "שלום" not in self._all_text(ears)


# ── config resolution ────────────────────────────────────────────────────────


class TestConfigFromEnv:
    def test_the_embodiment_var_wins(self) -> None:
        cfg = rtc.RealtimeConfig.from_env(
            {"EMBODIMENT_GATEWAY_KEY": "a", "CULTURE_VLLM_API_KEY": "b"}
        )
        assert cfg.api_key == "a"

    def test_the_culture_var_is_the_fallback(self) -> None:
        cfg = rtc.RealtimeConfig.from_env({"CULTURE_VLLM_API_KEY": "b"})
        assert cfg.api_key == "b"

    def test_no_key_is_an_empty_string_not_an_error(self) -> None:
        assert rtc.RealtimeConfig.from_env({}).api_key == ""

    def test_the_gateway_url_is_overridable_by_env(self) -> None:
        cfg = rtc.RealtimeConfig.from_env({"EMBODIMENT_GATEWAY_URL": "http://other:9000"})
        assert cfg.gateway_url == "http://other:9000"

    def test_defaults_declare_the_rig(self) -> None:
        cfg = rtc.RealtimeConfig.from_env({})
        assert (cfg.language, cfg.aec_mode, cfg.input_sample_rate) == ("he", "aec", 24000)

    def test_every_wait_has_a_positive_default_deadline(self) -> None:
        cfg = rtc.RealtimeConfig.from_env({})
        assert cfg.discovery_deadline > 0
        assert cfg.handshake_deadline > 0
        assert cfg.close_deadline > 0


# ── the live dial ────────────────────────────────────────────────────────────


def _live_key() -> str:
    return os.environ.get("EMBODIMENT_GATEWAY_KEY") or os.environ.get("CULTURE_VLLM_API_KEY") or ""


def _gateway_up() -> bool:
    try:
        with urllib.request.urlopen(f"{LIVE_GATEWAY}/capabilities", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


class TestLiveDial:
    """One real session against the rig's own gateway. Sends NO audio."""

    def test_reaches_session_created_and_closes(self) -> None:
        if not _gateway_up():
            pytest.skip(f"no gateway at {LIVE_GATEWAY}/capabilities")
        key = _live_key()
        if not key:
            pytest.skip("no EMBODIMENT_GATEWAY_KEY / CULTURE_VLLM_API_KEY set")

        async def go() -> tuple[bool, Any, Any]:
            ears = rtc.RealtimeEars(
                rtc.RealtimeConfig.from_env(
                    {"EMBODIMENT_GATEWAY_KEY": key, "EMBODIMENT_GATEWAY_URL": LIVE_GATEWAY}
                )
            )
            ok = await ears.connect()
            first = None
            if ok:
                with contextlib.suppress(StopAsyncIteration, asyncio.TimeoutError):
                    first = await asyncio.wait_for(anext(aiter(ears.events())), 15)
            report = await ears.close(deadline=5.0)
            assert not ears.dropped_frames
            assert key not in json.dumps(ears.status())
            return ok, first, report

        ok, first, report = run(go())
        assert ok is True, "the live gateway refused the session"
        assert isinstance(first, wire.SessionCreated)
        assert report.graceful is True


class TestTheAdvertIsReadStrictly:
    """``in`` matches too much. Only a list of tokens is an advert."""

    @pytest.mark.parametrize(
        "responsibilities",
        [
            "realtime_vad_session",  # a string CONTAINS the token
            {"realtime_vad_session": 1},  # a mapping is keyed by it
            None,
            17,
        ],
    )
    def test_a_non_list_responsibilities_field_is_an_absent_advert(
        self, responsibilities: Any
    ) -> None:
        body = {"stt": {"feasible": True, "responsibilities": responsibilities}}
        with Rig(caps_body=body) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.ADVERT_ABSENT]

    def test_a_tuple_shaped_list_still_reads(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config(realtime_url="http://127.0.0.1:1"))
            # Discovery passes; only the dial fails, which proves the advert read.
            assert run(ears.connect()) is False
            assert codes(ears) == [rtc.HANDSHAKE_FAILED]


class TestTheDropCountIsExact:
    def test_every_discarded_frame_is_counted(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config(max_queue_bytes=3200))
            for _ in range(100):
                ears.send_audio(b"\x00" * 1600)
            # The queue holds 2 frames; the other 98 were discarded.
            assert ears.queued_bytes == 3200
            assert ears.dropped_frames == 98
            assert ears.dropped_bytes == 98 * 1600

    def test_concurrent_capture_threads_lose_no_count(self) -> None:
        with Rig(caps_body=capabilities()) as rig:
            ears = rtc.RealtimeEars(rig.config(max_queue_bytes=1600))
            per_thread = 500

            def pump() -> None:
                for _ in range(per_thread):
                    ears.send_audio(b"\x00" * 1600)

            threads = [threading.Thread(target=pump) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            sent = 4 * per_thread
            queued = ears.queued_bytes // 1600
            assert ears.dropped_frames + queued == sent


# ── the liveness clock ───────────────────────────────────────────────────────


def _server_text_frame(text: str) -> bytes:
    """One unmasked server→client text frame (RFC 6455 §5.2)."""
    payload = text.encode("utf-8")
    header = bytearray([0x81])  # FIN + opcode 0x1 (text)
    size = len(payload)
    if size < 126:
        header.append(size)
    elif size < 65536:
        header.append(126)
        header += size.to_bytes(2, "big")
    else:
        header.append(127)
        header += size.to_bytes(8, "big")
    return bytes(header) + payload


class DeafServer:
    """A hand-rolled WebSocket peer that completes the handshake and then dies.

    It answers the HTTP upgrade itself, sends one real event, and from then on
    reads nothing and answers nothing — **including pings**. That is the failure
    a `websockets` server cannot reproduce (its protocol layer always pongs) and
    the one a voice app actually meets: a pulled cable, an expired NAT entry, a
    gateway SIGKILLed on another host. No FIN is ever sent, so only a keep-alive
    clock can notice.
    """

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, first_event: str) -> None:
        self.first_event = first_event
        self._server: Any = None
        self._handlers: set[Any] = set()

    async def __aenter__(self) -> "DeafServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        # The handlers park forever on purpose, so they are CANCELLED rather
        # than awaited: `wait_closed()` alone waits for every connection
        # handler to finish and would hang the test for as long as the server
        # is deaf, which is the whole point of it.
        self._server.close()
        for task in list(self._handlers):
            task.cancel()
        for task in list(self._handlers):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=5)

    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def _handle(self, reader: Any, writer: Any) -> None:
        import base64 as _b64
        import hashlib

        self._handlers.add(asyncio.current_task())
        head = await reader.readuntil(b"\r\n\r\n")
        key = ""
        for line in head.decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = _b64.b64encode(
            hashlib.sha1((key + self.GUID).encode()).digest()
        ).decode()  # nosec B324 - RFC 6455 requires SHA-1 here; not a security hash
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        writer.write(_server_text_frame(self.first_event))
        await writer.drain()
        # And now: nothing. No reads, no pongs, no close. Forever.
        await asyncio.sleep(60)


class TestTheLivenessClockIsOwned:
    def test_a_vanished_peer_is_noticed_inside_the_configured_bound(self) -> None:
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> tuple[float, rtc.RealtimeEars]:
                async with DeafServer(FIXTURE("session_created.json")) as deaf:
                    ears = rtc.RealtimeEars(
                        rig.config(
                            realtime_url=deaf.origin(),
                            ping_interval=0.2,
                            ping_timeout=0.2,
                            close_handshake_timeout=0.2,
                        )
                    )
                    assert await ears.connect() is True
                    started = asyncio.get_running_loop().time()
                    seen = [event async for event in ears.events()]
                    elapsed = asyncio.get_running_loop().time() - started
                    assert isinstance(seen[0], wire.SessionCreated)
                    await ears.close(deadline=1.0)
                    return elapsed, ears

            elapsed, ears = run(go())

        # The stream ENDED rather than raising, and said why, exactly once.
        assert codes(ears) == [rtc.SESSION_DROPPED]
        assert ears.connected is False
        # It was THIS session's configured bound that fired, not the module
        # default, and certainly not the transport's inherited 20+20+10.
        assert elapsed < rtc.RealtimeConfig().liveness_bound
        assert elapsed < 3.0
        # And it fired no EARLIER than the bound allows: a detection that beat
        # its own clock would mean something else closed the socket.
        assert elapsed >= 0.2

    def test_the_default_bound_is_a_spoken_turn_not_the_transport_default(self) -> None:
        cfg = rtc.RealtimeConfig()
        assert (cfg.ping_interval, cfg.ping_timeout, cfg.close_handshake_timeout) == (
            2.0,
            4.0,
            2.0,
        )
        assert cfg.liveness_bound == 8.0
        # The transport's own defaults sum to 20 + 20 + 10; anything near that
        # would mean this client inherited a clock instead of choosing one.
        assert cfg.liveness_bound <= 10.0

    def test_the_bound_sums_all_three_terms(self) -> None:
        cfg = rtc.RealtimeConfig(ping_interval=1.5, ping_timeout=2.5, close_handshake_timeout=0.5)
        assert cfg.liveness_bound == 4.5

    def test_the_close_handshake_term_is_not_forgotten(self) -> None:
        """The term that is easy to miss, pinned so it cannot be dropped."""
        two_terms = rtc.RealtimeConfig(ping_interval=1.0, ping_timeout=1.0)
        assert two_terms.liveness_bound > 2.0

    def test_status_publishes_the_bound_and_the_ages(self) -> None:
        with Rig(caps_body=capabilities()) as rig:

            async def go() -> dict[str, Any]:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    await anext(aiter(ears.events()))
                    snapshot = ears.status()
                    await ears.close()
                    return snapshot

            snapshot = run(go())
        assert snapshot["liveness_bound_s"] == 8.0
        assert snapshot["ping_interval_s"] == 2.0
        assert snapshot["ping_timeout_s"] == 4.0
        assert snapshot["close_handshake_timeout_s"] == 2.0
        assert 0.0 <= snapshot["last_event_age_s"] < 5.0
        assert json.dumps(snapshot)  # still JSON-safe

    def test_the_ages_are_absent_not_zero_before_a_session(self) -> None:
        ears = rtc.RealtimeEars()
        assert ears.last_event_age is None
        assert ears.latency is None
        assert ears.status()["last_event_age_s"] is None
        assert ears.status()["latency_s"] is None


# ── the exception sanitiser ──────────────────────────────────────────────────


class TestNoExceptionMessageReachesARecord:
    """Every exception-derived reason goes through ``describe_exception``.

    ``tests/test_safe_reason.py``'s AST guard proves the *shape* — no
    ``str(exc)``, no ``{exc}`` — over ``client.py``, which it now scans because
    ``client.py`` imports ``safe_reason``. These prove the *effect*, with a
    marker planted where a real server would put text: a gateway's HTTP reason
    phrase, and a WebSocket close reason.
    """

    MARKER = "ZZSPEECHMARKERZZ"

    def test_a_hostile_http_reason_phrase_does_not_reach_the_record(self) -> None:
        # urllib puts the reason phrase straight into HTTPError's message, so
        # this is exactly the path where a server's text becomes a client's
        # exception. Before adoption the reason read
        # "HTTPError: HTTP Error 500: Internal <marker> Failure".
        with Rig(
            caps_body={}, caps_status=500, caps_reason=f"Internal {self.MARKER} Failure"
        ) as rig:
            ears = rtc.RealtimeEars(rig.config())
            assert run(ears.connect()) is False

        assert codes(ears) == [rtc.DISCOVERY_FAILED]
        reason = ears.degradations[0].reason
        assert self.MARKER not in reason
        # The facts survive: the class, the status, the withheld length, and a
        # fingerprint to correlate two occurrences of the same fault.
        assert "HTTPError" in reason
        assert "status=500" in reason
        assert "chars" in reason and "fp:" in reason

    def test_the_plant_is_real(self) -> None:
        """The marker really does reach the exception — so the test can fail."""
        with Rig(
            caps_body={}, caps_status=500, caps_reason=f"Internal {self.MARKER} Failure"
        ) as rig:
            caught: list[str] = []
            try:
                urllib.request.urlopen(f"{rig.origin}/capabilities", timeout=5)  # nosec B310
            except urllib.error.HTTPError as exc:
                caught.append(str(exc))
        assert caught and self.MARKER in caught[0]

    def test_a_close_reason_carrying_speech_does_not_reach_the_record(self) -> None:
        async def handler(ws: Any) -> None:
            await ws.send(FIXTURE("session_created.json"))
            # A close reason is server-chosen text. On THIS wire the server is
            # holding a transcript.
            await ws.close(code=1011, reason=f"bridge died {self.MARKER}")

        with Rig(caps_body=capabilities(), handler=handler) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    async for _ in ears.events():
                        pass
                    await ears.close()
                    return ears

            ears = run(go())

        assert codes(ears) == [rtc.SESSION_DROPPED]
        reason = ears.degradations[0].reason
        assert self.MARKER not in reason
        assert "ConnectionClosedError" in reason
        assert "fp:" in reason

    def test_the_same_fault_twice_carries_the_same_fingerprint(self) -> None:
        """What the fingerprint buys: correlation without the text."""

        def one() -> str:
            with Rig(caps_body={}, caps_status=503, caps_reason="Overloaded") as rig:
                ears = rtc.RealtimeEars(rig.config())
                run(ears.connect())
                return ears.degradations[0].reason

        first, second = one(), one()
        assert first.split("fp:")[1] == second.split("fp:")[1]

    def test_the_reason_bound_can_hold_a_whole_description(self) -> None:
        """A truncation that clips the fingerprint loses the correlation."""
        assert rtc._MAX_REASON_LEN > safe_reason.MAX_DESCRIPTION_CHARS

    def test_a_refusal_body_still_rides_the_other_sanitiser(self) -> None:
        """A gateway's JSON body is not an exception; ``_safe`` still owns it."""

        def process(connection: Any, request: Any) -> Any:
            return connection.respond(
                503, json.dumps({"error": {"message": f"your key {MARKER_KEY} is stale"}})
            )

        with Rig(caps_body=capabilities(), ws_reject=process) as rig:

            async def go() -> rtc.RealtimeEars:
                async with rig.websocket():
                    ears = rtc.RealtimeEars(rig.config(realtime_url=rig.ws_origin()))
                    await ears.connect()
                    return ears

            ears = run(go())
        reason = ears.degradations[0].reason
        assert MARKER_KEY not in reason and rtc.REDACTED in reason
        # It came from the BODY, not from the exception, so it is prose and not
        # a description — the two lanes stay distinguishable.
        assert "fp:" not in reason
