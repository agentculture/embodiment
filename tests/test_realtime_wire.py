"""The lobes realtime wire codec — fixture-driven, and ears-only by construction.

Every decode test is driven from `tests/fixtures/realtime/`, whose README cites
the `lobes-cli` file and line each field came from. Nothing here opens a socket
and nothing here imports `lobes`.
"""

from __future__ import annotations

import ast
import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from embodiment.realtime import wire

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "realtime"

#: The two client events that would turn an ear into a mouth. Spelled here, in
#: the test, and nowhere in the package — `TestEarsOnly` is what enforces that.
FORBIDDEN_CLIENT_EVENTS = ("response.create", "conversation.item.create")

SOURCE_FILES = (
    Path(wire.__file__),
    Path(__file__).resolve().parents[1] / "embodiment" / "realtime" / "client.py",
)


def load(name: str) -> str:
    """One fixture, as the raw JSON text a WebSocket text frame would carry."""
    return FIXTURES.joinpath(name).read_text(encoding="utf-8")


# ── the connect URL and the session declaration ──────────────────────────────


class TestConnectUrl:
    def test_query_declares_the_whole_negotiated_config(self) -> None:
        params = parse_qs(wire.session_query())
        assert params == {
            "input_audio_format": ["pcm16"],
            "input_sample_rate": ["24000"],
            "input_channels": ["1"],
            "turn_detection": ["server_vad"],
            "aec_mode": ["aec"],
            "language": ["he"],
        }

    def test_http_origin_becomes_ws_same_host_and_port(self) -> None:
        parts = urlsplit(wire.realtime_url("http://localhost:8001"))
        assert (parts.scheme, parts.hostname, parts.port, parts.path) == (
            "ws",
            "localhost",
            8001,
            "/v1/realtime",
        )

    def test_https_origin_becomes_wss(self) -> None:
        assert wire.realtime_url("https://gw.example:443").startswith("wss://")

    def test_a_trailing_slash_does_not_double(self) -> None:
        assert "//v1/realtime" not in wire.realtime_url("http://localhost:8001/")

    def test_an_origin_path_is_discarded_not_joined(self) -> None:
        # /capabilities is the discovery route; the session lives at the ORIGIN.
        parts = urlsplit(wire.realtime_url("http://localhost:8001/capabilities"))
        assert parts.path == "/v1/realtime"

    def test_language_and_aec_are_overridable_but_default_as_the_rig_runs(self) -> None:
        params = parse_qs(wire.session_query(language="en", aec_mode="none"))
        assert params["language"] == ["en"] and params["aec_mode"] == ["none"]

    def test_the_key_never_reaches_the_url(self) -> None:
        # The bearer rides a header. Nothing in this module takes a key at all.
        assert "api_key" not in wire.realtime_url("http://localhost:8001")


class TestEncoders:
    def test_audio_append_round_trips_the_exact_bytes(self) -> None:
        pcm = bytes(range(256)) * 4
        payload = json.loads(wire.encode_audio_append(pcm))
        assert payload["type"] == "input_audio_buffer.append"
        assert base64.b64decode(payload["audio"], validate=True) == pcm

    def test_empty_audio_is_encodable_not_an_error(self) -> None:
        payload = json.loads(wire.encode_audio_append(b""))
        assert payload["audio"] == ""

    def test_session_update_carries_language_in_an_openai_session_object(self) -> None:
        payload = json.loads(wire.encode_session_update(language="he"))
        assert payload == {"type": "session.update", "session": {"language": "he"}}

    def test_session_update_is_the_only_non_audio_client_event(self) -> None:
        encoders = {n for n in wire.__all__ if n.startswith("encode_")}
        assert encoders == {"encode_audio_append", "encode_session_update"}


# ── decoding: one test per fixture, plus the unknown-server rule ─────────────


class TestDecodeSessionLifecycle:
    def test_session_created(self) -> None:
        event = wire.decode_server_event(load("session_created.json"))
        assert isinstance(event, wire.SessionCreated)
        assert event.session_id == "sess_aaaaaaaaaaaaaaaaaaaaaaaa"
        assert event.config["aec_mode"] == "aec"
        assert event.config["language"] == "he"
        assert event.config["input_sample_rate"] == 24000

    def test_session_updated_echoes_only_what_took_effect(self) -> None:
        event = wire.decode_server_event(load("session_updated.json"))
        assert isinstance(event, wire.SessionUpdated)
        assert event.applied == {"language": "he"}

    def test_session_closed_carries_its_reason(self) -> None:
        event = wire.decode_server_event(load("session_closed.json"))
        assert isinstance(event, wire.SessionClosed)
        assert event.reason == "client disconnected"


class TestDecodeTurnBoundaries:
    def test_speech_started_carries_audio_stream_time(self) -> None:
        event = wire.decode_server_event(load("speech_started.json"))
        assert isinstance(event, wire.SpeechStarted)
        assert event.at_ms == 1216
        assert event.item_id == "item_bbbbbbbbbbbbbbbbbbbbbbbb"

    def test_speech_stopped_silence(self) -> None:
        event = wire.decode_server_event(load("speech_stopped_silence.json"))
        assert isinstance(event, wire.SpeechStopped)
        assert event.reason == "silence"
        assert event.at_ms == 2848

    def test_speech_stopped_max_turn_is_a_boundary_not_an_error(self) -> None:
        event = wire.decode_server_event(load("speech_stopped_max_turn.json"))
        assert isinstance(event, wire.SpeechStopped)
        assert event.reason == "max_turn"
        assert not isinstance(event, wire.ServerError)

    def test_transcription_completed_carries_the_text_verbatim(self) -> None:
        event = wire.decode_server_event(load("transcription_completed.json"))
        assert isinstance(event, wire.TranscriptionCompleted)
        assert event.text == "שלום גוון"

    def test_a_transcript_repr_does_not_carry_the_speech(self) -> None:
        event = wire.decode_server_event(load("transcription_completed.json"))
        assert "שלום" not in repr(event)
        assert "chars=" in repr(event)


class TestDecodeErrors:
    ERROR_CODES = (
        "invalid_session_config",
        "vad_unavailable",
        "invalid_wire_event",
        "stt_forward_failed",
        "generate_failed",
        "tts_failed",
        "response_timeout",
    )

    @pytest.mark.parametrize("code", ERROR_CODES)
    def test_each_named_error_code_decodes(self, code: str) -> None:
        event = wire.decode_server_event(load(f"error_{code}.json"))
        assert isinstance(event, wire.ServerError)
        assert event.code == code

    def test_the_fixture_set_covers_every_code_the_module_names(self) -> None:
        assert set(self.ERROR_CODES) == set(wire.SERVER_ERROR_CODES)

    def test_an_unnamed_error_code_still_decodes_as_an_error(self) -> None:
        raw = json.dumps({"type": "error", "code": "a_code_from_the_future", "message": "?"})
        event = wire.decode_server_event(raw)
        assert isinstance(event, wire.ServerError)
        assert event.code == "a_code_from_the_future"


class TestUnknownAndMalformed:
    def test_a_known_to_lobes_but_unconsumed_event_is_unknown_not_an_error(self) -> None:
        event = wire.decode_server_event(load("unknown_response_created.json"))
        assert isinstance(event, wire.UnknownEvent)
        assert event.event_type == "response.created"

    def test_an_invented_event_type_decodes_to_unknown(self) -> None:
        event = wire.decode_server_event(json.dumps({"type": "session.telepathy"}))
        assert isinstance(event, wire.UnknownEvent)
        assert event.event_type == "session.telepathy"

    def test_a_typeless_object_is_unknown_with_an_empty_type(self) -> None:
        event = wire.decode_server_event(json.dumps({"hello": "there"}))
        assert isinstance(event, wire.UnknownEvent)
        assert event.event_type == ""

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "{",
            "null",
            "[]",
            '"a string"',
            "17",
            b"\xff\xfe not utf-8",
            "{}" * 5000,
        ],
    )
    def test_malformed_input_never_raises(self, raw: object) -> None:
        event = wire.decode_server_event(raw)
        assert isinstance(event, wire.MalformedEvent)

    def test_a_malformed_frame_does_not_echo_the_frame_back(self) -> None:
        event = wire.decode_server_event('{"secret": "hunter2-marker"}x')
        assert isinstance(event, wire.MalformedEvent)
        assert "hunter2-marker" not in event.reason

    @pytest.mark.parametrize(
        "name",
        [
            "session_created.json",
            "speech_started.json",
            "speech_stopped_silence.json",
            "transcription_completed.json",
            "error_vad_unavailable.json",
        ],
    )
    def test_wrong_typed_fields_degrade_to_unknown_rather_than_raising(self, name: str) -> None:
        payload = json.loads(load(name))
        for key in list(payload):
            if key == "type":
                continue
            mangled = dict(payload)
            mangled[key] = {"not": "what you expected"}
            event = wire.decode_server_event(json.dumps(mangled))
            assert isinstance(event, wire.ServerEvent)

    def test_every_decoded_event_is_frozen(self) -> None:
        event = wire.decode_server_event(load("speech_started.json"))
        with pytest.raises(Exception):
            event.at_ms = 5  # type: ignore[misc]


# ── the structural ears-only proof ───────────────────────────────────────────


class TestEarsOnly:
    """No encoder, and no string, that could arm a reply."""

    @pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
    @pytest.mark.parametrize("forbidden", FORBIDDEN_CLIENT_EVENTS)
    def test_no_source_string_constant_names_an_arming_event(
        self, path: Path, forbidden: str
    ) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and forbidden in node.value
        ]
        assert offenders == [], f"{path.name} names {forbidden!r}"

    @pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
    @pytest.mark.parametrize("forbidden", FORBIDDEN_CLIENT_EVENTS)
    def test_not_even_as_a_comment_or_an_f_string_piece(self, path: Path, forbidden: str) -> None:
        assert forbidden not in path.read_text(encoding="utf-8")

    def test_this_guard_can_fail(self, tmp_path: Path) -> None:
        planted = tmp_path / "planted.py"
        planted.write_text('X = "response.create"\n', encoding="utf-8")
        tree = ast.parse(planted.read_text(encoding="utf-8"))
        found = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "response.create" in n.value
        ]
        assert found == ["response.create"]

    def test_the_module_has_no_encoder_for_anything_but_audio_and_session(self) -> None:
        for name in dir(wire):
            if name.startswith("encode"):
                assert name in {"encode_audio_append", "encode_session_update"}

    def test_no_public_name_mentions_a_response(self) -> None:
        assert not [n for n in wire.__all__ if "response" in n.lower()]


class TestTheUrlNeverDowngrades:
    """A microphone must not end up on a plaintext socket by accident."""

    @pytest.mark.parametrize("origin", ["https://gw.example", "wss://gw.example:8001"])
    def test_an_encrypted_origin_stays_encrypted(self, origin: str) -> None:
        assert wire.realtime_url(origin).startswith("wss://")

    @pytest.mark.parametrize("origin", ["http://gw.example", "ws://gw.example", "gw.example:8001"])
    def test_a_plaintext_origin_stays_plaintext(self, origin: str) -> None:
        assert wire.realtime_url(origin).startswith("ws://")

    def test_a_schemeless_host_port_is_read_as_a_netloc(self) -> None:
        parts = urlsplit(wire.realtime_url("localhost:8001"))
        assert (parts.hostname, parts.port) == ("localhost", 8001)

    @pytest.mark.parametrize(
        "origin", ["", "   ", "http://", "http://[::1]:8001", "http://h/../..", "ftp://x"]
    )
    def test_a_nonsense_origin_returns_a_string_rather_than_raising(self, origin: str) -> None:
        assert wire.realtime_url(origin).endswith(wire.session_query())
