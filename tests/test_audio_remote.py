"""``RemoteEndpoint`` — the browser ear, driven against a real socket (task ``t14``).

Every test here runs a real ``websockets`` server (``RemoteEndpoint`` itself,
bound to ``127.0.0.1:0`` so tests never collide on a fixed port) and a real
``websockets`` client dialing it — no mock transport. ``tests/conftest.py``
points ``TMPDIR``/``HOME`` at a scratch dir for the whole suite; this module
writes nothing to disk itself.

No ``pytest-asyncio`` in this repo's dev set (see ``tests/test_realtime_client.py``),
so every async body runs under an explicit ``asyncio.run``.

Fixture provenance
-------------------
``tests/fixtures/realtime/inbound_audio_append.jsonl`` is a synthetic,
NEW (this task's own) fixture — distinct from the server->client fixtures
task ``t6`` recorded under the same directory (that directory's README
documents only those; this file documents its own, since only *new* files
may be added there per this task's brief). Shaped exactly as
``embodiment.realtime.wire.encode_audio_append``/``encode_session_update``
themselves produce (``{"type": ..., "audio": ...}`` /
``{"type": "session.update", "session": {"language": ...}}``) — a real
browser client built against ``wire.py``'s encoders emits exactly this
envelope. One line is a ``session.update``, two carry 16 bytes of
deterministic, clearly-synthetic PCM16 (``bytes(range(16))`` and
``bytes(range(16, 32))`` — never real audio), and one is an
``input_audio_buffer.append`` with a zero-byte payload, exercising
``wire.py``'s own documented rule that an empty buffer is a valid frame.
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


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _load_fixture_lines() -> list[dict[str, Any]]:
    text = (FIXTURES / "inbound_audio_append.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def _connect(port: int, secret: str | None, *, timeout: float = 5.0) -> Any:
    from websockets.asyncio.client import connect as ws_connect

    url = f"ws://127.0.0.1:{port}/v1/realtime"
    if secret is not None:
        url += f"?secret={secret}"
    return await asyncio.wait_for(ws_connect(url, open_timeout=timeout), timeout=timeout)


async def _wait_until(predicate: Any, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


def _endpoint(**overrides: Any) -> rt.RemoteEndpoint:
    kwargs: dict[str, Any] = {"secret": "s3cr3t-test-only", "host": "127.0.0.1", "port": 0}
    kwargs.update(overrides)
    return rt.RemoteEndpoint(**kwargs)


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
                ws = await _connect(ep.bound_port, ep.config.secret)
                try:
                    for event in _load_fixture_lines():
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
                ws = await _connect(ep.bound_port, ep.config.secret)
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
    def test_missing_secret_is_refused_and_no_frame_is_ever_delivered(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus) as excinfo:
                    await _connect(ep.bound_port, None)
                assert excinfo.value.response.status_code == 401

            _run(scenario())
            status = ep.status()
            assert status["frames_received"] == 0
            assert status["unauthorized_connections"] == 1
            assert received == []
        finally:
            ep.close(2.0)

    def test_wrong_secret_is_refused_and_reason_never_carries_the_guess(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus) as excinfo:
                    await _connect(ep.bound_port, MARKER_SECRET)
                assert excinfo.value.response.status_code == 401

            _run(scenario())
            status = ep.status()
            assert status["unauthorized_connections"] == 1
            blob = json.dumps(status)
            assert MARKER_SECRET not in blob
            assert ep.config.secret not in blob
        finally:
            ep.close(2.0)

    def test_valid_secret_is_accepted_and_receives_session_created(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> tuple[str, ...]:
                ws = await _connect(ep.bound_port, ep.config.secret)
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

    def test_no_secret_configured_refuses_everything_fail_closed(self) -> None:
        ep = _endpoint(secret="")
        ep.attach()
        try:
            assert ep.status()["degradation"]["code"] == rt.DEGRADED_NO_SECRET

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus) as excinfo:
                    await _connect(ep.bound_port, "anything")
                assert excinfo.value.response.status_code == 503

            _run(scenario())
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


# ── Attacks: the wave-1-lesson checklist ─────────────────────────────────────


class TestAttacks:
    def test_malformed_json_frame_is_counted_never_raises_never_kills_connection(self) -> None:
        ep = _endpoint()
        received: list[bytes] = []
        ep.start_capture(received.append)
        ep.attach()
        try:

            async def scenario() -> None:
                ws = await _connect(ep.bound_port, ep.config.secret)
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
                ws = await _connect(ep.bound_port, ep.config.secret)
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
                ws = await _connect(ep.bound_port, ep.config.secret)
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

    def test_10k_char_secret_query_and_bidi_control_chars_do_not_crash_the_gate(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:
            hostile = "x" * 10_000 + "‮\u0085 " + "'; DROP TABLE--"

            async def scenario() -> None:
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus):
                    await _connect(ep.bound_port, hostile)

            _run(scenario())
            status = ep.status()
            assert hostile not in json.dumps(status)
        finally:
            ep.close(2.0)

    def test_path_separators_and_dotdot_in_query_do_not_crash_process_request(self) -> None:
        ep = _endpoint()
        ep.attach()
        try:

            async def scenario() -> None:
                from websockets.asyncio.client import connect as ws_connect
                from websockets.exceptions import InvalidStatus

                url = f"ws://127.0.0.1:{ep.bound_port}/../../etc/passwd?secret=../../x"
                with pytest.raises(InvalidStatus):
                    await asyncio.wait_for(ws_connect(url, open_timeout=5.0), timeout=5.0)

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
                ws = await _connect(ep.bound_port, ep.config.secret)
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
                ws = await _connect(ep.bound_port, ep.config.secret)
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

                first = await _connect(ep.bound_port, ep.config.secret)
                try:
                    await _wait_until(lambda: ep.status()["connected"] is True)
                    with pytest.raises(InvalidStatus) as excinfo:
                        await _connect(ep.bound_port, ep.config.secret)
                    assert excinfo.value.response.status_code == 503
                finally:
                    await first.close()

            _run(scenario())
            assert ep.status()["connections_rejected_busy"] == 1
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
            second = rt.RemoteEndpoint(secret="s3cr3t-test-only", host="127.0.0.1", port=port)
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
                ws = await _connect(ep.bound_port, ep.config.secret)
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
                ws = await _connect(ep.bound_port, ep.config.secret)
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
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus):
                    await _connect(ep.bound_port, "totally-wrong")

            _run(scenario())
            blob = json.dumps(ep.status())
            assert MARKER_SECRET not in blob
        finally:
            ep.close(2.0)


class TestProtocolConformance:
    def test_isinstance_audio_endpoint(self) -> None:
        from embodiment.audio.endpoint import AudioEndpoint

        ep = _endpoint()
        assert isinstance(ep, AudioEndpoint)
        ep.close(2.0)
