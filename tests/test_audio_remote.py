"""``RemoteEndpoint`` — the browser ear, driven against a real socket (task ``t14``).

Every test here runs a real ``websockets`` server (``RemoteEndpoint`` itself,
bound to ``127.0.0.1:0`` so tests never collide on a fixed port) and a real
``websockets`` client dialing it — no mock transport. ``tests/conftest.py``
points ``TMPDIR``/``HOME`` at a scratch dir for the whole suite; this module
writes nothing to disk itself.

No ``pytest-asyncio`` in this repo's dev set (see ``tests/test_realtime_client.py``),
so every async body runs under an explicit ``asyncio.run``.

Round 2: authentication moved off the connect URL
-----------------------------------------------------
The coordinator corrected the brief after driving round-1's build
(``8109e7b``) with a real client: a query-string secret lands in proxy
access logs, browser history and ``Referer`` headers, so the handshake now
carries no credential at all. The WebSocket handshake completes bare, and the
FIRST JSON message on the socket must be ``{"type": "auth", "secret": ...}``
— checked before any other message, audio included, is processed. Every test
below that used to put the secret on the connect URL now sends it as that
first message instead; ``TestAuthenticationGate`` and the secret-in-URL
attack tests are new/rewritten for this round.

Fixture provenance
-------------------
``tests/fixtures/realtime/inbound_audio_append.jsonl`` is a synthetic,
NEW (this task's own) fixture — distinct from the server->client fixtures
task ``t6`` recorded under the same directory (that directory's README
documents only those; this file documents its own, since only *new* files
may be added there per this task's brief). Its first line is the
``{"type": "auth", ...}`` message every connection must send first (round 2);
the rest is shaped exactly as
``embodiment.realtime.wire.encode_audio_append``/``encode_session_update``
themselves produce (``{"type": ..., "audio": ...}`` /
``{"type": "session.update", "session": {"language": ...}}``) — a real
browser client built against ``wire.py``'s encoders emits exactly this
envelope. One line is a ``session.update``, two carry 16 bytes of
deterministic, clearly-synthetic PCM16 (``bytes(range(16))`` and
``bytes(range(16, 32))`` — never real audio), and one is an
``input_audio_buffer.append`` with a zero-byte payload, exercising
``wire.py``'s own documented rule that an empty buffer is a valid frame.

Round 5: the round-4 default bound was too large
--------------------------------------------------
The coordinator's own probe against the merged round-4 fix found the
endpoint no longer bricked, but the pre-round-5 defaults
(``handshake_open_timeout`` 10 s + ``_PENDING_CLAIM_MARGIN_S`` 1 s) meant one
aborted handshake still refused every legitimate peer for up to 11 s.
``TestRound5ReviewFindings`` pins the new defaults (3.0 s / 0.5 s, derived
from "an HTTP upgrade over a LAN/tailnet is one round trip" rather than
inherited from the library's own unstated 10 s) and proves an aborted
handshake recovers in low single digits of seconds using the plain,
unoverridden defaults — not just the short test-only overrides round 4's own
tests used. The probe also could not find a ``rejected_busy_count`` key;
what existed was named ``connections_rejected_busy`` — renamed and moved
beside ``handshake_aborted_count`` in :meth:`~embodiment.audio.remote.RemoteEndpoint.status`,
and every test that read the old key name is updated here too.

Round 6: two real-socket, real-wall-clock tests were flaky under load
---------------------------------------------------------------------
25 runs each under ``-n auto``: ``TestRound4ReviewFindings``'s
"does not brick" test and ``TestRound5ReviewFindings``'s "recovers quickly"
test both failed intermittently (7/25 and 5/25 respectively in one
measured batch), always the same way —
``assert ep.status()["handshake_aborted_count"] >= 1`` with the count at
``0``, in a run where the retried connect had ALREADY succeeded and
received ``session.created`` moments earlier. That ruled out a bricked
endpoint (a real client always eventually got through, every time, across
both batches) and pointed at the test's assertion instead: a connection
approved by ``process_request`` and then aborted can be cleaned up by
EITHER of two equally-valid paths — ``process_response``'s immediate
detection or the staleness backstop in ``process_request`` (both count
``handshake_aborted_count``) — OR, when the OS-level race resolves the
other way and the doomed handshake actually completes to ``OPEN``, by
``_handle_connection`` running for it, failing authentication instantly
(the peer is already gone), and releasing the claim through its own
ordinary ``finally`` — counted under ``unauthorized_connections``, never
``handshake_aborted_count``. Which path fires is genuine, expected TCP
timing nondeterminism (whether a peer's RST is processed by the kernel
before or after the server's attempted write), not a bug; pinning ONE of
the two paths was the test's mistake.

The fix has two parts:

1. A new dependency-injected ``clock`` argument on ``RemoteEndpoint``
   (default :func:`time.monotonic`) lets the staleness backstop's TIMING
   claim be driven deterministically, with zero real sleeping —
   ``TestRound6ReviewFindings`` calls ``_process_request`` directly against
   fake connections and a :class:`_FakeClock`, proving the exact reclaim
   boundary (refused just under the bound, reclaimed just over it) with no
   real socket and no possibility of the OS-timing race above ever
   entering into it at all.
2. The one remaining real-socket, real-wall-clock test
   (``TestRound4ReviewFindings::test_aborted_handshake_after_approval_does_not_brick_the_endpoint``)
   keeps proving the actual end-to-end claim — a real aborted TCP handshake
   really does get cleaned up and a real client really does get through —
   but now accepts EITHER valid recovery path, stated in its own docstring,
   with a generous, explicitly-margined real-time ceiling for a loaded CI
   box.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import pytest

from embodiment.audio import remote as rt
from embodiment.realtime import wire

FIXTURES = Path(__file__).parent / "fixtures" / "realtime"
MARKER_SECRET = "sk-planted-marker-7f3ab9-DO-NOT-LEAK"  # nosec B105 - test sentinel, not a secret
DEFAULT_SECRET = "s3cr3t-test-only"  # nosec B105 - test sentinel, not a real secret


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _load_fixture_lines() -> list[dict[str, Any]]:
    text = (FIXTURES / "inbound_audio_append.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def _connect(port: int, *, timeout: float = 5.0) -> Any:
    """A bare WebSocket connect. No secret anywhere on the URL — never has one."""
    from websockets.asyncio.client import connect as ws_connect

    url = f"ws://127.0.0.1:{port}/v1/realtime"
    return await asyncio.wait_for(ws_connect(url, open_timeout=timeout), timeout=timeout)


async def _connect_with_url_secret(port: int, secret: str, *, timeout: float = 5.0) -> Any:
    """A connect carrying ``?secret=...`` — only ever used to prove it is ignored."""
    from websockets.asyncio.client import connect as ws_connect

    url = f"ws://127.0.0.1:{port}/v1/realtime?secret={secret}"
    return await asyncio.wait_for(ws_connect(url, open_timeout=timeout), timeout=timeout)


async def _authed_connect(port: int, secret: str, *, timeout: float = 5.0) -> Any:
    """Connect, then send the one first-message auth event."""
    ws = await _connect(port, timeout=timeout)
    await ws.send(json.dumps({"type": "auth", "secret": secret}))
    return ws


async def _retry_authed_connect(
    port: int, secret: str, *, attempts: int = 40, interval: float = 0.1
) -> Any:
    """Retry an authenticated connect past a transient "busy" refusal.

    The staleness backstop (round 4 finding 1) only reclaims an abandoned
    pending claim lazily, INSIDE a later connection's own attempt — there is
    no background timer — so proving it works means retrying the connect
    itself, not polling a status field.
    """
    from websockets.exceptions import InvalidStatus

    last: Exception | None = None
    for _ in range(attempts):
        try:
            return await _authed_connect(port, secret)
        except InvalidStatus as exc:
            last = exc
            await asyncio.sleep(interval)
    assert last is not None
    raise last


async def _wait_until(predicate: Any, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


def _endpoint(**overrides: Any) -> rt.RemoteEndpoint:
    kwargs: dict[str, Any] = {"secret": DEFAULT_SECRET, "host": "127.0.0.1", "port": 0}
    kwargs.update(overrides)
    return rt.RemoteEndpoint(**kwargs)


class _FakeClock:
    """A manually-advanced monotonic clock (round 6).

    Injected as ``RemoteEndpoint(clock=...)`` so the staleness backstop's
    TIMING claim can be proven with zero real time elapsed — no sleeping,
    so nothing here can be flaky under a loaded scheduler the way racing a
    real wall clock against a real socket abort was (see the round 6 note
    at the top of this file)."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class _FakeConnection:
    """Just enough of a ``ServerConnection`` for ``_process_request`` /
    ``_process_response`` to run against directly — no real socket, no real
    handshake. ``respond`` mirrors the real method's signature closely
    enough to prove nothing there raises; its return value is never a real
    HTTP response object, so it is never sent anywhere."""

    def __init__(self, state: Any) -> None:
        self.state = state

    def respond(self, status: int, text: str) -> tuple[int, str]:
        return (status, text)


class _FakeRequest:
    """Just enough of a ``Request`` for ``_process_request``'s query-string
    parsing to run against — no query parameters, the common case."""

    path = "/v1/realtime"


def _abort_handshake_after_request(host: str, port: int) -> None:
    """A raw socket that completes the HTTP upgrade REQUEST bytes, then aborts
    before ever reading the ``101`` response — round 4 finding 1's attack.

    ``process_request`` runs (and approves — the request is a well-formed
    upgrade) the instant the server finishes parsing these headers, well
    before this function returns; the abortive close (``SO_LINGER`` with a
    zero timeout, forcing an RST rather than a graceful FIN) is what makes
    the server's transport notice ``connection_lost`` promptly rather than
    sitting on a socket it might still believe it can write the response to.
    Blocking, stdlib-only, run off the event loop via ``asyncio.to_thread``.
    """
    import base64
    import os
    import socket
    import struct

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /v1/realtime HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock = socket.create_connection((host, port), timeout=5.0)
    try:
        sock.sendall(request.encode("ascii"))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    finally:
        sock.close()


# ── AC1: a recorded lobes-wire fixture drives the endpoint end to end ───────


class TestFixtureDrivesFrames:
    def test_fixture_frames_reach_on_frame_callback_in_order(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:
            assert ep.status()["degradation"] is None

            async def scenario() -> None:
                ws = await _connect(ep.bound_port)
                try:
                    events = _load_fixture_lines()
                    await ws.send(json.dumps(events[0]))  # the auth event
                    # Round 4 finding 4: a broken first send used to go
                    # unnoticed here — assert it's really session.created.
                    created = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    assert created["type"] == "session.created"
                    for event in events[1:]:
                        await ws.send(json.dumps(event))
                    await _wait_until(lambda: len(received) == 3)
                finally:
                    await ws.close()

            _run(scenario())

            expected = [
                bytes(range(16)),
                bytes(range(16, 32)),
                b"",
            ]
            assert received == expected
        finally:
            ep.close(2.0)

    def test_frames_are_the_same_bytes_shape_a_host_endpoint_delivers(self) -> None:
        """``on_frame`` receives ``bytes`` — the same ``FrameCallback`` contract
        ``embodiment.audio.host.HostEndpoint`` delivers through (pcm16 bytes, no
        wrapper object), so a caller wired to one endpoint needs no change to
        read the other."""
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send(wire.encode_audio_append(b"\x01\x02\x03\x04"))
                    await _wait_until(lambda: len(received) == 1)
                finally:
                    await ws.close()

            _run(scenario())
            assert received == [b"\x01\x02\x03\x04"]
            assert all(isinstance(frame, bytes) for frame in received)
        finally:
            ep.close(2.0)


# ── AC2: a connection without a valid secret is refused before any audio is accepted ──


class TestAuthenticationGate:
    def test_no_auth_message_inside_the_deadline_is_refused_1008(self) -> None:
        ep = _endpoint(auth_deadline=0.3)
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                ws = await _connect(ep.bound_port)  # handshake succeeds; no auth message sent
                with pytest.raises(ConnectionClosedError) as excinfo:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert excinfo.value.rcvd is not None
                assert excinfo.value.rcvd.code == 1008

            _run(scenario())
            status = ep.status()
            assert status["frames_received"] == 0
            assert status["unauthorized_connections"] == 1
            assert received == []
        finally:
            ep.close(2.0)

    def test_wrong_secret_is_refused_1008_and_reason_never_carries_the_guess(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                ws = await _authed_connect(ep.bound_port, MARKER_SECRET)
                with pytest.raises(ConnectionClosedError) as excinfo:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert excinfo.value.rcvd.code == 1008

            _run(scenario())
            status = ep.status()
            assert status["unauthorized_connections"] == 1
            blob = json.dumps(status)
            assert MARKER_SECRET not in blob
            assert ep.config.secret not in blob
        finally:
            ep.close(2.0)

    def test_malformed_first_message_is_refused_never_treated_as_audio(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                ws = await _connect(ep.bound_port)
                # A real audio frame arrives BEFORE any auth: still refused, never processed.
                await ws.send(wire.encode_audio_append(b"should-never-arrive"))
                with pytest.raises(ConnectionClosedError) as excinfo:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert excinfo.value.rcvd.code == 1008

            _run(scenario())
            assert received == []
            assert ep.status()["frames_received"] == 0
            assert ep.status()["unauthorized_connections"] == 1
        finally:
            ep.close(2.0)

    def test_valid_secret_is_accepted_and_receives_session_created(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> tuple[str, ...]:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    return (raw,)
                finally:
                    await ws.close()

            (raw,) = _run(scenario())
            payload = json.loads(raw)
            assert payload["type"] == "session.created"
            assert payload["config"]["input_sample_rate"] == wire.INPUT_SAMPLE_RATE
        finally:
            ep.close(2.0)

    def test_no_secret_configured_refuses_everything_fail_closed_pre_handshake(self) -> None:
        ep = _endpoint(secret="")
        ep.attach()
        try:
            assert ep.status()["degradation"]["code"] == rt.DEGRADED_NO_SECRET

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus) as excinfo:
                    await _connect(ep.bound_port)
                assert excinfo.value.response.status_code == 503

            _run(scenario())
        finally:
            ep.close(2.0)

    def test_secret_in_url_is_ignored_for_auth_and_only_counted(self) -> None:
        """A right OR wrong secret on the URL grants nothing — round 2's whole point."""
        ep = _endpoint(auth_deadline=0.3)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                # The URL carries the CORRECT secret, but no first-message auth follows.
                ws = await _connect_with_url_secret(ep.bound_port, DEFAULT_SECRET)
                with pytest.raises(ConnectionClosedError) as excinfo:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert excinfo.value.rcvd.code == 1008

            _run(scenario())
            status = ep.status()
            # Both are counted: the URL leak AND the fact it bought no auth.
            # ``status()["degradation"]`` holds only the MOST RECENT of the two
            # (the later auth timeout), which is why this asserts the counters
            # rather than that single slot.
            assert status["secret_in_url_count"] == 1
            assert status["unauthorized_connections"] == 1  # the URL secret bought nothing
        finally:
            ep.close(2.0)


# ── AC3: README states v1 ships no robot support, the seam is what ships ────


class TestReadmeStatesTheSeam:
    def test_readme_has_the_no_robot_seam_section(self) -> None:
        readme = Path(__file__).parent.parent / "README.md"
        text = readme.read_text(encoding="utf-8")
        lower = text.lower()
        assert "no robot support" in lower or "no robot" in lower
        assert "seam" in lower
        assert "audio/remote.py" in text or "remote.py" in text

    def test_readme_no_longer_tells_a_client_to_put_the_secret_on_the_url(self) -> None:
        readme = Path(__file__).parent.parent / "README.md"
        text = readme.read_text(encoding="utf-8")
        section = text.split("## Inbound realtime endpoint", 1)[1]
        section = section.split("\n## ", 1)[0]
        assert "?secret=" not in section
        assert "first message" in section.lower() or "first-message" in section.lower()


# ── Attacks: the wave-1-lesson checklist ─────────────────────────────────────


class TestAttacks:
    def test_malformed_json_frame_is_counted_never_raises_never_kills_connection(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send("not json at all {{{")
                    await ws.send(json.dumps(["not", "an", "object"]))
                    await ws.send(json.dumps({"type": "input_audio_buffer.append"}))  # no audio
                    await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": 5}))
                    await ws.send(
                        json.dumps({"type": "input_audio_buffer.append", "audio": "not-b64!!"})
                    )
                    # connection must still be alive after all that garbage:
                    await ws.send(wire.encode_audio_append(b"ok"))
                    await _wait_until(lambda: len(received) == 1)
                finally:
                    await ws.close()

            _run(scenario())
            assert received == [b"ok"]
            status = ep.status()
            assert status["frames_malformed"] >= 4
        finally:
            ep.close(2.0)

    def test_unknown_event_type_is_counted_not_fatal(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send(json.dumps({"type": "response.create"}))
                    await ws.send(json.dumps({"type": "conversation.item.create"}))
                    await asyncio.sleep(0.1)
                finally:
                    await ws.close()

            _run(scenario())
            assert ep.status()["unknown_frames"] == 2
        finally:
            ep.close(2.0)

    def test_binary_frame_and_bad_utf8_do_not_raise(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send(b"\xff\xfe\x00\x01binary-garbage")
                    await ws.send(wire.encode_audio_append(b"still-alive"))
                    await asyncio.sleep(0.1)
                finally:
                    await ws.close()

            _run(scenario())
            assert ep.status()["frames_malformed"] >= 1
        finally:
            ep.close(2.0)

    def test_10k_char_secret_and_bidi_control_chars_in_the_auth_message_do_not_crash(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:
            hostile = "x" * 10_000 + "‮\u0085 " + "'; DROP TABLE--"

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                ws = await _authed_connect(ep.bound_port, hostile)
                with pytest.raises(ConnectionClosedError) as excinfo:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert excinfo.value.rcvd.code == 1008

            _run(scenario())
            status = ep.status()
            assert hostile not in json.dumps(status)
        finally:
            ep.close(2.0)

    def test_long_secret_query_param_does_not_crash_the_gate(self) -> None:
        """A query string within the transport's own request-line cap (see the
        sibling test below for what happens past that cap) still reaches
        ``process_request`` and must not crash it."""
        ep = _endpoint(auth_deadline=0.3)
        ep.attach()
        try:
            hostile = "y" * 2000 + "‮\u0085 " + "'; DROP TABLE--"

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                ws = await _connect_with_url_secret(ep.bound_port, hostile)
                with pytest.raises(ConnectionClosedError):
                    await asyncio.wait_for(ws.recv(), timeout=2.0)

            _run(scenario())
            status = ep.status()
            assert hostile not in json.dumps(status)
            assert status["secret_in_url_count"] == 1
        finally:
            ep.close(2.0)

    def test_10k_char_secret_query_param_is_refused_by_the_transports_own_uri_cap(self) -> None:
        """Found while attacking: a 10k-char query string never reaches this
        module's code at all — ``websockets``' own HTTP layer refuses a
        request line over 8192 bytes with a 414 before ``process_request``
        runs. A defense-in-depth finding, not a defect: documented here so it
        is not mistaken for an untested path."""
        ep = _endpoint()
        ep.attach()
        try:
            hostile = "z" * 10_000

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus) as excinfo:
                    await _connect_with_url_secret(ep.bound_port, hostile)
                assert excinfo.value.response.status_code == 414

            _run(scenario())
        finally:
            ep.close(2.0)

    def test_path_separators_and_dotdot_in_path_do_not_crash_process_request(self) -> None:
        ep = _endpoint(auth_deadline=0.3)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.asyncio.client import connect as ws_connect
                from websockets.exceptions import ConnectionClosedError

                url = f"ws://127.0.0.1:{ep.bound_port}/../../etc/passwd?x=../../y"
                ws = await asyncio.wait_for(ws_connect(url, open_timeout=5.0), timeout=5.0)
                with pytest.raises(ConnectionClosedError):
                    await asyncio.wait_for(ws.recv(), timeout=2.0)

            _run(scenario())
        finally:
            ep.close(2.0)

    def test_repeated_call_ten_thousand_times_stays_bounded(self) -> None:
        ep = _endpoint()
        for _ in range(10_000):
            ep.mute(True)
            ep.mute(False)
        assert ep.muted is False
        # Never attached: play() before attach must be a harmless no-op, not a crash.
        for _ in range(1000):
            ep.play(b"\x00\x00")
        ep.close(2.0)

    def test_play_overflow_refuses_new_chunk_never_drops_queued_audio(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:
            limit = int(rt._PLAYBACK_BUFFER_SECONDS * 24000) * 2
            big = b"\x00\x01" * (limit // 2)
            ep.play(big)  # fills the buffer exactly
            before = ep.status()["playback_queued_bytes"]
            ep.play(b"\x00\x01" * 100)  # one more sample-pair chunk: must overflow
            after = ep.status()
            assert after["playback_overflow_count"] == 1
            assert after["playback_queued_bytes"] == before
        finally:
            ep.close(2.0)

    def test_stop_playback_is_idempotent_and_returns_zero_with_nothing_queued(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:
            assert ep.stop_playback() == 0
            assert ep.stop_playback() == 0
        finally:
            ep.close(2.0)

    def test_stop_playback_discards_queued_samples_and_counts_them(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:
            ep.play(b"\x00\x01" * 4800)  # 4800 samples queued, no peer to drain to
            discarded = ep.stop_playback()
            assert discarded == 4800
            assert ep.status()["playback_stop_discarded_total"] == 4800
        finally:
            ep.close(2.0)

    def test_play_reaches_a_connected_browser_as_response_audio_delta(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> list[dict[str, Any]]:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                events: list[dict[str, Any]] = []
                try:
                    created = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    events.append(created)
                    ep.play(b"\x11\x22\x33\x44")
                    delta = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    events.append(delta)
                finally:
                    await ws.close()
                return events

            events = _run(scenario())
            assert events[0]["type"] == "session.created"
            assert events[1]["type"] == "response.audio.delta"
            assert base64.b64decode(events[1]["delta"]) == b"\x11\x22\x33\x44"
        finally:
            ep.close(2.0)

    def test_mute_drops_frames_before_they_reach_on_frame(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.mute(True)
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send(wire.encode_audio_append(b"should-be-muted"))
                    await asyncio.sleep(0.1)
                finally:
                    await ws.close()

            _run(scenario())
            assert received == []
            assert ep.status()["frames_muted_dropped"] == 1
        finally:
            ep.close(2.0)

    def test_second_connection_is_refused_while_one_is_active(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                first = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await _wait_until(lambda: ep.status()["connected"] is True)
                    with pytest.raises(InvalidStatus) as excinfo:
                        await _connect(ep.bound_port)
                    assert excinfo.value.response.status_code == 503
                finally:
                    await first.close()

            _run(scenario())
            assert ep.status()["rejected_busy_count"] == 1
        finally:
            ep.close(2.0)

    def test_second_connection_is_refused_even_mid_authentication(self) -> None:
        """A second dial-in is refused even before the FIRST has authenticated —
        the pending-auth guard, not just the post-auth ``connected`` check."""
        ep = _endpoint(auth_deadline=2.0)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                first = await _connect(ep.bound_port)  # handshake only, no auth sent yet
                try:
                    await asyncio.sleep(0.1)  # let process_request's pending flag land
                    with pytest.raises(InvalidStatus) as excinfo:
                        await _connect(ep.bound_port)
                    assert excinfo.value.response.status_code == 503
                finally:
                    await first.close()

            _run(scenario())
            assert ep.status()["rejected_busy_count"] == 1
        finally:
            ep.close(2.0)

    def test_close_before_attach_is_a_harmless_noop(self) -> None:
        ep = _endpoint()
        report = ep.close(1.0)
        assert report.capture_thread_stopped is True
        assert report.samples_discarded == 0

    def test_close_is_idempotent(self) -> None:
        ep = _endpoint()
        ep.attach()
        first = ep.close(2.0)
        second = ep.close(2.0)
        assert first == second

    def test_close_honours_its_deadline_and_reports_elapsed(self) -> None:
        ep = _endpoint()
        ep.attach()
        report = ep.close(3.0)
        assert report.elapsed_s < 3.0
        assert report.capture_thread_stopped is True
        assert report.writer_thread_stopped is True

    def test_detach_then_reattach_works(self) -> None:
        ep = _endpoint()
        ep.attach()
        ep.detach()
        ep.attach()
        try:
            assert ep.status()["attached"] is True
        finally:
            ep.close(2.0)

    def test_bind_failure_on_an_already_bound_port_is_recorded_never_raised(self) -> None:
        holder = _endpoint()
        holder.attach()
        try:
            port = holder.bound_port
            second = rt.RemoteEndpoint(secret=DEFAULT_SECRET, host="127.0.0.1", port=port)
            second.attach()
            try:
                status = second.status()
                assert status["degradation"] is not None
                assert status["degradation"]["code"] == rt.DEGRADED_BIND_FAILED
            finally:
                second.close(2.0)
        finally:
            holder.close(2.0)

    def test_play_before_attach_and_after_close_are_both_harmless(self) -> None:
        ep = _endpoint()
        ep.play(b"\x00\x00")  # before attach
        ep.attach()
        ep.close(2.0)
        ep.play(b"\x00\x00")  # after close
        assert ep.status()["playback_queued_bytes"] == 0

    def test_start_capture_stop_capture_toggle_does_not_crash_mid_stream(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send(wire.encode_audio_append(b"one"))
                    await _wait_until(lambda: len(received) == 1)
                    ep.stop_capture()
                    await ws.send(wire.encode_audio_append(b"two"))
                    await asyncio.sleep(0.1)
                    ep.start_capture(received.append)
                    await ws.send(wire.encode_audio_append(b"three"))
                    await _wait_until(lambda: len(received) == 2)
                finally:
                    await ws.close()

            _run(scenario())
            assert received == [b"one", b"three"]
        finally:
            ep.close(2.0)

    def test_on_frame_callback_that_raises_never_kills_the_reader(self) -> None:
        ep = _endpoint()

        def boom(_frame: bytes) -> None:
            raise RuntimeError("boom")

        ep.start_capture(boom)
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await ws.send(wire.encode_audio_append(b"x"))
                    await ws.send(wire.encode_audio_append(b"y"))
                    await asyncio.sleep(0.2)
                finally:
                    await ws.close()

            _run(scenario())
            assert ep.status()["callback_errors"] == 2
        finally:
            ep.close(2.0)

    def test_status_is_json_serialisable_and_never_raises(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:
            json.dumps(ep.status())  # must not raise
        finally:
            report = ep.close(2.0)
            json.dumps(report.to_dict())

    def test_no_marker_string_leaks_anywhere_in_status_or_degradation(self) -> None:
        ep = rt.RemoteEndpoint(secret=MARKER_SECRET, host="127.0.0.1", port=0)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import ConnectionClosedError

                ws = await _authed_connect(ep.bound_port, "totally-wrong")
                with pytest.raises(ConnectionClosedError):
                    await asyncio.wait_for(ws.recv(), timeout=2.0)

            _run(scenario())
            blob = json.dumps(ep.status())
            assert MARKER_SECRET not in blob
        finally:
            ep.close(2.0)


# ── Round 4: review findings ─────────────────────────────────────────────────


class TestRound4ReviewFindings:
    def test_aborted_handshake_after_approval_does_not_brick_the_endpoint(self) -> None:
        """Finding 1: a client that gets past ``process_request`` and then
        aborts before the ``101`` completes must not permanently occupy the
        one-peer-at-a-time claim. This is the exact scenario finding 1
        described: the old code left ``_connection_pending`` stuck True
        forever, and every later connection — even a legitimate, correctly
        authenticated one — got refused with "endpoint already has an active
        peer" until the process restarted.

        The ONE deliberately real-socket, real-wall-clock test in this file
        (round 6): a genuine raw-socket abort, a genuine retry against a
        genuine listening endpoint. Real time elapses here on purpose — the
        exact reclaim TIMING is proven separately and deterministically by
        ``TestRound6ReviewFindings``, with no sleeping at all; this test's
        job is only to prove the real system, wired together, really does
        recover. A short ``handshake_open_timeout`` keeps that real elapsed
        time small; ``_retry_authed_connect``'s generous attempt budget is
        the margin for a loaded CI box the round 6 brief asked for.

        Round 6: do not assert WHICH internal path released the claim.
        25 real runs under ``-n auto`` showed this resolving via either of
        two equally-valid paths, depending on nondeterministic OS-level TCP
        timing (whether the client's RST is processed before or after the
        server's attempted write of the ``101``): ``handshake_aborted_count``
        (``process_response``'s immediate detection, or the staleness
        backstop) OR ``unauthorized_connections`` (the doomed handshake
        actually completed, and ``_handle_connection`` ran for it just long
        enough to fail authentication instantly against an already-vanished
        peer, releasing the claim through its own ordinary ``finally``). The
        endpoint never bricked in any of those 25 runs — a real client
        always got through — so recovery via EITHER counter is the thing
        this test exists to prove; the previous version asserted only the
        first, and about 20-30% of runs took the second."""
        ep = _endpoint(handshake_open_timeout=0.3)
        ep.attach()
        try:

            async def scenario() -> None:
                await asyncio.to_thread(_abort_handshake_after_request, "127.0.0.1", ep.bound_port)
                ws = await _retry_authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    payload = json.loads(raw)
                    assert payload["type"] == "session.created"
                finally:
                    await ws.close()

            _run(scenario())
            status = ep.status()
            recovered_via_either_path = (
                status["handshake_aborted_count"] + status["unauthorized_connections"]
            )
            assert recovered_via_either_path >= 1
        finally:
            ep.close(2.0)

    def test_second_connection_while_first_is_mid_auth_is_still_refused_busy(self) -> None:
        """The staleness backstop must never fire while a REAL handler is
        actively running a connection through authentication — round 2's
        "refused even mid-auth" guarantee stays true with the round 4 fix in
        place, for as long as ``auth_deadline`` allows, regardless of how
        short ``handshake_open_timeout`` is configured.

        Round 6: made deterministic. The claim's immunity to staleness once
        a real handler is running it (:meth:`_handle_connection` clears
        ``_connection_pending_since`` at entry) does not depend on how much
        TIME passes, only on that boolean — so instead of really sleeping
        past the bound, the injected fake clock jumps by a huge amount. A
        REAL first connection still completes a REAL handshake (this test
        still proves the guard against real ``_handle_connection`` entry
        timing, not just against the clock), but nothing here waits on a
        real wall clock for its assertion to hold."""
        clock = _FakeClock(start=5000.0)
        ep = _endpoint(auth_deadline=3.0, clock=clock)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                first = await _connect(ep.bound_port)  # handshake completes; holds the claim
                try:
                    await _wait_until(lambda: ep._connection_pending_since is None, timeout=2.0)
                    # Jump the fake clock far past ANY bound this endpoint
                    # could ever be configured with — if the fix wrongly
                    # treated an ACTIVE handler's claim as stale, this second
                    # dial-in would now succeed regardless.
                    clock.advance(10_000.0)
                    with pytest.raises(InvalidStatus) as excinfo:
                        await _connect(ep.bound_port)
                    assert excinfo.value.response.status_code == 503
                finally:
                    await first.close()

            _run(scenario())
            assert ep.status()["rejected_busy_count"] >= 1
            assert ep.status()["handshake_aborted_count"] == 0
        finally:
            ep.close(2.0)

    def test_process_response_never_clears_a_different_connections_claim(self) -> None:
        """Ownership guard, unit-level: a late ``process_response`` callback
        for a connection that is NOT the CURRENT pending owner — e.g. it was
        already superseded by the staleness backstop reclaiming the slot for
        a newer connection — must never clear that newer, still-legitimate
        claim."""
        from websockets.protocol import State

        clock = _FakeClock(start=100.0)
        ep = _endpoint(clock=clock)
        try:
            stale_owner = _FakeConnection(State.CLOSED)  # the old, aborted connection
            current_owner = _FakeConnection(State.CONNECTING)  # claimed by someone else since

            ep._connection_pending = True
            ep._pending_connection = current_owner
            ep._connection_pending_since = clock()

            ep._process_response(stale_owner, None, None)  # a late, unrelated callback

            assert ep._connection_pending is True
            assert ep._pending_connection is current_owner
            assert ep.status()["handshake_aborted_count"] == 0
        finally:
            ep.close(2.0)

    def test_a_second_fault_out_of_serve_after_a_bind_failure_is_still_counted(self) -> None:
        """Finding 2: the old ``_run`` guard recorded only the FIRST fault out
        of ``_serve`` (via ``if self._bind_error is None``); a SECOND one —
        simulated here by making the server thread raise twice, the second
        time after a bind failure was already recorded — left no trace
        anywhere. Every fault must be counted, even once a degradation is
        already on record."""
        ep = _endpoint()
        ep._bind_error = rt.EndpointDegradation(rt.DEGRADED_BIND_FAILED, "seeded")

        class _Boom:
            def __call__(self, *_a: Any, **_kw: Any) -> None:
                raise RuntimeError("second fault, after a bind failure was already recorded")

        # Drive _run directly (off any real thread) with a _serve stand-in
        # that raises — exactly the "a second fault after the first" shape
        # finding 2 described, without needing a real doomed asyncio.run.
        import threading as _threading

        ready = _threading.Event()
        original_serve = ep._serve
        ep._serve = _Boom()  # type: ignore[method-assign]
        try:
            ep._run(ready)
        finally:
            ep._serve = original_serve  # type: ignore[method-assign]

        assert ep.status()["server_thread_fault_count"] == 1
        assert ep._bind_error is not None
        assert ep._bind_error.code == rt.DEGRADED_BIND_FAILED  # the FIRST fault, kept

    def test_control_send_burst_against_a_never_reading_peer_is_bounded(self) -> None:
        """Finding 3: a burst of ``session.update`` control replies against a
        peer that stops reading must not pile up one fire-and-forget send
        task per message without limit — a NEW send past the cap is dropped
        and counted, never queued."""
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _authed_connect(ep.bound_port, DEFAULT_SECRET)
                try:
                    await asyncio.wait_for(ws.recv(), timeout=5.0)  # session.created
                    # Stop reading entirely — every control reply from here
                    # on has nowhere to drain to, exactly what floods
                    # _enqueue_control's fire-and-forget tasks.
                    for _ in range(200):
                        await ws.send(json.dumps({"type": "session.update", "session": {}}))
                    # Round 6 (a third flaky test, found by this task's own
                    # repro): wait for the server to actually have DECODED
                    # all 200 sends, not just for the FIRST dropped reply —
                    # closing the socket the instant one drop is observed
                    # can race the server's own read loop under load, tearing
                    # the connection down before it finishes draining bytes
                    # already in flight, undercounting session_updates_received.
                    await _wait_until(
                        lambda: (
                            ep.status()["session_updates_received"] == 200
                            and ep.status()["control_sends_dropped"] > 0
                        ),
                        timeout=10.0,
                    )
                finally:
                    await ws.close()

            _run(scenario())
            status = ep.status()
            assert status["control_sends_dropped"] > 0
            assert status["session_updates_received"] == 200  # every one was still DECODED
        finally:
            ep.close(2.0)


# ── Round 5: the coordinator's own probe found the default bound too large ──


class TestRound5ReviewFindings:
    def test_default_handshake_bound_is_derived_not_inherited(self) -> None:
        """Pins round 5: the pre-round-5 default (10 s open_timeout + 1 s
        margin = 11 s) let one aborted handshake refuse every legitimate peer
        for up to 11 s. An HTTP upgrade over a LAN/tailnet is one round trip,
        so the new default is sized against that, not the library's own
        unstated 10 s."""
        ep = _endpoint()
        try:
            assert ep.config.handshake_open_timeout == 3.0
            assert rt._PENDING_CLAIM_MARGIN_S == 0.5
        finally:
            ep.close(2.0)

    def test_rejected_busy_count_is_exposed_beside_handshake_aborted_count(self) -> None:
        """The coordinator's probe went looking for this key and didn't find
        it under its OLD name (``connections_rejected_busy``, round 4) —
        renamed and moved next to its sibling counter."""
        ep = _endpoint()
        try:
            status = ep.status()
            assert "rejected_busy_count" in status
            assert "handshake_aborted_count" in status
            assert status["rejected_busy_count"] == 0
            assert status["handshake_aborted_count"] == 0
        finally:
            ep.close(2.0)


# ── Round 6: two real-socket tests were flaky under load; see the module ────
# ── docstring's round 6 note for the diagnosis (test, not mechanism).    ────


class TestRound6ReviewFindings:
    def test_default_bound_reclaims_deterministically_at_its_own_boundary(self) -> None:
        """Round 6 replacement for a real-socket, real-sleep reproduction
        that was flaky under load (5/25 runs in one measured batch): NOT
        because the mechanism bricked (every failing run had already
        connected successfully by the time the assertion ran), but because a
        real aborted TCP handshake can be cleaned up by either of two valid
        paths depending on OS-level timing, and because racing a real
        ~3.5 s wall-clock bound against a loaded ``-n auto`` box is itself an
        unforced source of slowness and jitter this claim does not need.

        This proves the SAME claim — the plain, unoverridden default bound
        recovers a claim, not up to the old 11 s — deterministically: the
        endpoint's real default config (no ``handshake_open_timeout``
        override) drives ``_process_request`` directly against fake
        connections and an injected, manually-advanced clock. Real socket
        I/O and real end-to-end recovery are covered separately by
        ``TestRound4ReviewFindings``'s one deliberately real-time test."""
        from websockets.protocol import State

        clock = _FakeClock(start=9000.0)
        ep = _endpoint(clock=clock)  # the actual shipped defaults: no override
        try:
            request = _FakeRequest()
            holder = _FakeConnection(State.CONNECTING)
            assert ep._process_request(holder, request) is None  # claims the slot

            # Just under the real default bound (3.0 + 0.5 = 3.5 s): still refused.
            clock.advance(3.49)
            refused = ep._process_request(_FakeConnection(State.CONNECTING), request)
            assert refused is not None
            assert ep.status()["rejected_busy_count"] == 1
            assert ep.status()["handshake_aborted_count"] == 0

            # Just over it: reclaimed.
            clock.advance(0.02)  # total 3.51 s
            reclaimer = _FakeConnection(State.CONNECTING)
            approved = ep._process_request(reclaimer, request)
            assert approved is None
            assert ep.status()["handshake_aborted_count"] == 1
            assert ep._pending_connection is reclaimer
        finally:
            ep.close(2.0)

    def test_a_custom_bound_reclaims_at_its_own_boundary_too(self) -> None:
        """The seam is general, not just correct for the one pair of numbers
        the default happens to be: an operator-configured
        ``handshake_open_timeout`` drives the same boundary, still with zero
        real time elapsed."""
        from websockets.protocol import State

        clock = _FakeClock(start=42.0)
        ep = _endpoint(handshake_open_timeout=1.2, clock=clock)
        try:
            request = _FakeRequest()
            assert ep._process_request(_FakeConnection(State.CONNECTING), request) is None

            clock.advance(1.2 + 0.5 - 0.01)  # just under this endpoint's own bound
            assert ep._process_request(_FakeConnection(State.CONNECTING), request) is not None
            assert ep.status()["handshake_aborted_count"] == 0

            clock.advance(0.02)  # now just over it
            assert ep._process_request(_FakeConnection(State.CONNECTING), request) is None
            assert ep.status()["handshake_aborted_count"] == 1
        finally:
            ep.close(2.0)

    def test_clock_defaults_to_real_monotonic_time(self) -> None:
        """The injection seam must not change production behaviour when
        nobody uses it — the default is the real clock, not a fake one left
        in by accident."""
        ep = _endpoint()
        try:
            assert ep._clock is time.monotonic
        finally:
            ep.close(2.0)


class TestProtocolConformance:
    def test_isinstance_audio_endpoint(self) -> None:
        from embodiment.audio.endpoint import AudioEndpoint

        ep = _endpoint()
        assert isinstance(ep, AudioEndpoint)
        ep.close(2.0)

    def test_sample_rate_is_24000(self) -> None:
        """Pins t14 round 3: the browser delivers pcm16 already at the fixed
        24 kHz wire rate, never resampled — so ``sample_rate`` reports exactly
        that, not a measurement (duck-typed against ``realtime/t7``'s protocol
        member, not merged into this branch)."""
        ep = _endpoint()
        assert ep.sample_rate == 24000
        ep.close(2.0)
