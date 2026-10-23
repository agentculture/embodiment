"""Tests for embodiment.daemon.text_turns (plan task t14). Synthetic data only."""

from __future__ import annotations

import ast
import builtins
import io
import json
import struct
import wave
from pathlib import Path

import pytest

import embodiment.daemon.text_turns as T
from embodiment.daemon.text_turns import TextTurns, TextTurnsConfig

PCM = b"\x01\x00" * 2400  # 0.1 s of pcm16 mono 24 kHz


def make_wav(seconds: float, rate: int = 8000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return out.getvalue()


def make_ogg_opus(seconds: float) -> bytes:
    head = b"OggS" + b"\x00" * 22 + b"OpusHead" + b"\x00" * 11
    last = b"OggS\x00\x04" + struct.pack("<q", int(seconds * 48000)) + b"\x00" * 12
    return head + b"\x00" * 40 + last


class FakeSession:
    def __init__(self):
        self.calls = []

    def add_user(self, text):
        self.calls.append(("user", text))

    def add_assistant(self, text, metadata=None):
        self.calls.append(("assistant", text))


class FakeEndpoint:
    def __init__(self):
        self.played = 0

    def play(self, *a, **k):
        self.played += 1


def make(**kw):
    recorded = []
    kw.setdefault("run_text_turn", lambda text: f"reply:{text}")
    kw.setdefault("record", lambda c, r: recorded.append((c, r)))
    return TextTurns(**kw), recorded


class TestTypedTurn:
    def test_reply_is_text_and_endpoint_never_played(self):
        endpoint = FakeEndpoint()
        tt, _ = make()
        result = tt.handle_text("hello")
        assert result.ok and result.reply == "reply:hello"
        assert endpoint.played == 0
        assert not hasattr(tt, "play")

    def test_module_never_imports_voice_playback(self):
        tree = ast.parse(Path(T.__file__).read_text())
        calls = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
        assert "play" not in calls and "stop_playback" not in calls

    def test_joins_the_session_in_order(self):
        session = FakeSession()
        tt, _ = make(session=session)
        tt.handle_text("שלום")
        assert session.calls == [("user", "שלום"), ("assistant", "reply:שלום")]

    def test_session_may_be_a_provider(self):
        session = FakeSession()
        tt, _ = make(session=lambda: session)
        tt.handle_text("hi")
        assert len(session.calls) == 2

    def test_no_session_is_recorded_not_fatal(self):
        tt, rec = make()
        assert tt.handle_text("hi").ok
        assert rec[0][0] == T.TEXT_TURN_NO_SESSION

    def test_turn_exception_degrades(self):
        def boom(text):
            raise RuntimeError("secret speech here")

        tt, rec = make(run_text_turn=boom)
        result = tt.handle_text("hi")
        assert not result.ok and result.code == T.TEXT_TURN_FAILED
        assert all("secret speech" not in r for _, r in rec)

    def test_session_failure_is_recorded_and_turn_still_runs(self):
        class Bad(FakeSession):
            def add_user(self, text):
                raise OSError("disk")

        tt, rec = make(session=Bad())
        assert tt.handle_text("hi").ok
        assert any(c == T.TEXT_TURN_SESSION_FAILED for c, _ in rec)

    def test_result_object_with_spoken_is_accepted(self):
        class R:
            spoken = "hey"

        tt, _ = make(run_text_turn=lambda t: R())
        assert tt.handle_text("x").reply == "hey"

    @pytest.mark.parametrize("bad,status", [(None, 400), (5, 400), ("  ", 400)])
    def test_refusals(self, bad, status):
        tt, _ = make()
        r = tt.handle_text(bad)
        assert not r.ok and r.http_status == status

    def test_over_long_text_refused_413(self):
        tt, _ = make()
        assert tt.handle_text("a" * (T.MAX_TEXT_CHARS + 1)).http_status == 413

    def test_sink_failure_does_not_raise(self):
        def sink(c, r):
            raise RuntimeError

        tt = TextTurns(run_text_turn=lambda t: "ok", record=sink)
        assert tt.handle_text("hi").ok


class TestSpeakMessage:
    def test_returns_wav_bytes_and_writes_nothing(self, monkeypatch):
        opened = []
        real_open = builtins.open
        monkeypatch.setattr(
            builtins, "open", lambda *a, **k: opened.append(a) or real_open(*a, **k)
        )
        tt, _ = make(tts=lambda s, cfg: PCM)
        r = tt.speak_message("One. Two.")
        assert r.ok and r.sentences == 2 and r.content_type == "audio/wav"
        with wave.open(io.BytesIO(r.audio)) as w:
            assert w.getframerate() == 24000 and w.getnframes() == 2 * 2400
        assert opened == []

    def test_no_playback_endpoint_involved(self):
        endpoint = FakeEndpoint()
        tt, _ = make(tts=lambda s, c: PCM)
        tt.speak_message("hi")
        assert endpoint.played == 0

    def test_tts_failure_degrades(self):
        def boom(s, c):
            raise OSError("down")

        tt, rec = make(tts=boom)
        r = tt.speak_message("hi")
        assert not r.ok and r.http_status == 502 and rec[0][0] == T.SPEAK_TTS_FAILED

    def test_partial_failure_returns_audio_so_far(self):
        calls = []

        def tts(s, c):
            calls.append(s)
            if len(calls) == 2:
                raise OSError("x")
            return PCM

        tt, rec = make(tts=tts)
        r = tt.speak_message("One. Two. Three.")
        assert r.ok and r.truncated and r.sentences == 1
        assert any(c == T.SPEAK_TRUNCATED for c, _ in rec)

    def test_non_bytes_tts_is_malformed(self):
        tt, _ = make(tts=lambda s, c: "nope")
        assert not tt.speak_message("hi").ok

    def test_audio_cap(self):
        big = b"\x00\x00" * (T.MAX_SPEAK_AUDIO_BYTES // 2)
        tt, _ = make(tts=lambda s, c: big)
        r = tt.speak_message("One. Two.")
        assert r.ok and r.truncated and len(r.audio) <= T.MAX_SPEAK_AUDIO_BYTES + 64

    def test_deadline_stops_synthesis(self):
        now = [0.0]

        def tts(s, c):
            now[0] += 100
            return PCM

        tt, _ = make(tts=tts, clock=lambda: now[0])
        r = tt.speak_message("One. Two. Three.")
        assert r.ok and r.truncated and r.sentences == 1

    @pytest.mark.parametrize("bad", [None, 3, "  "])
    def test_refuses_non_text(self, bad):
        tt, _ = make(tts=lambda s, c: PCM)
        assert not tt.speak_message(bad).ok


class TestClip:
    def test_transcribed_in_memory_then_typed_turn(self, monkeypatch):
        seen = []
        session = FakeSession()
        tt, _ = make(session=session, stt=lambda a, f, c: seen.append((len(a), f, c)) or " שלום ")
        r = tt.handle_clip(make_wav(1.0))
        assert r.ok and r.transcript == "שלום" and r.reply == "reply:שלום"
        assert seen[0][1:] == ("clip.wav", "audio/wav")
        assert session.calls[0] == ("user", "שלום")
        assert r.duration_s == pytest.approx(1.0)

    def test_over_byte_cap_refused_before_stt(self):
        called = []
        cfg = TextTurnsConfig(max_clip_bytes=100)
        tt, _ = make(config=cfg, stt=lambda *a: called.append(a) or "x")
        r = tt.handle_clip(make_wav(1.0))
        assert r.http_status == 413 and r.code == T.REFUSED_CLIP_TOO_BIG and not called

    def test_over_duration_cap_refused_before_stt(self):
        called = []
        cfg = TextTurnsConfig(max_clip_seconds=2.0)
        tt, _ = make(config=cfg, stt=lambda *a: called.append(a) or "x")
        r = tt.handle_clip(make_wav(3.0))
        assert r.http_status == 413 and r.code == T.REFUSED_CLIP_TOO_LONG and not called

    def test_ogg_opus_duration_read_and_capped(self):
        called = []
        cfg = TextTurnsConfig(max_clip_seconds=10.0)
        tt, _ = make(config=cfg, stt=lambda *a: called.append(a) or "x")
        assert T.probe_clip(make_ogg_opus(5.0)).duration_s == pytest.approx(5.0)
        assert tt.handle_clip(make_ogg_opus(30.0)).http_status == 413 and not called

    def test_declared_duration_can_only_refuse_more(self):
        called = []
        cfg = TextTurnsConfig(max_clip_seconds=10.0)
        tt, _ = make(config=cfg, stt=lambda *a: called.append(a) or "x")
        assert tt.handle_clip(make_wav(1.0), declared_duration_s=99).http_status == 413
        assert not called
        assert tt.handle_clip(make_wav(1.0), declared_duration_s=float("nan")).ok

    def test_webm_duration_unknown_but_accepted_under_byte_cap(self):
        tt, _ = make(stt=lambda *a: "hi")
        r = tt.handle_clip(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
        assert r.ok and r.duration_s is None

    def test_unknown_and_corrupt_refused(self):
        tt, _ = make(stt=lambda *a: "x")
        assert tt.handle_clip(b"not audio at all").http_status == 415
        assert tt.handle_clip(b"RIFF\x00\x00\x00\x00WAVEjunk").http_status == 400
        assert tt.handle_clip(b"").http_status == 400
        assert tt.handle_clip("text").http_status == 400

    def test_stt_failure_and_empty_transcript_degrade(self):
        def boom(*a):
            raise OSError("x")

        tt, rec = make(stt=boom)
        assert tt.handle_clip(make_wav(0.5)).code == T.CLIP_STT_FAILED
        tt2, rec2 = make(stt=lambda *a: "   ")
        assert tt2.handle_clip(make_wav(0.5)).code == T.CLIP_EMPTY_TRANSCRIPT
        tt3, _ = make(stt=lambda *a: 7)
        assert tt3.handle_clip(make_wav(0.5)).code == T.CLIP_STT_MALFORMED
        assert rec and rec2

    def test_no_audio_byte_reaches_state_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tt, _ = make(stt=lambda *a: "hi")
        tt.handle_clip(make_wav(0.5))
        assert list(tmp_path.rglob("*")) == []

    def test_module_has_no_file_or_tempfile_use(self):
        tree = ast.parse(Path(T.__file__).read_text())
        imported = {
            a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, ast.Import)
            for a in n.names
        } | {
            (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        }
        assert not imported & {"tempfile", "shutil", "pathlib", "os"}
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "open" not in names


class _Resp:
    def __init__(self, body):
        self._b = io.BytesIO(body)

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        return _Resp(self.body)


class TestHttpTranscribe:
    def test_multipart_shape_and_text(self):
        op = _Opener(json.dumps({"text": "שלום"}).encode())
        cfg = TextTurnsConfig(gateway_url="http://gw:1/", api_key="k", stt_model="stt")
        out = T.http_transcribe(b"AUDIO", "clip.wav", "audio/wav", cfg, opener=op)
        req, timeout = op.requests[0]
        assert out == "שלום" and timeout == cfg.stt_deadline
        assert req.full_url == "http://gw:1/v1/audio/transcriptions"
        assert req.get_header("Authorization") == "Bearer k"
        assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
        assert b'filename="clip.wav"' in req.data and b"AUDIO" in req.data
        assert b'name="model"' in req.data and b'name="language"' in req.data

    def test_bad_scheme_and_bad_body_raise(self):
        with pytest.raises(ValueError):
            T.http_transcribe(b"a", "c.wav", "audio/wav", TextTurnsConfig(gateway_url="ftp://x"))
        with pytest.raises(ValueError):
            T.http_transcribe(
                b"a", "c.wav", "audio/wav", TextTurnsConfig(), opener=_Opener(b'{"nope":1}')
            )

    def test_oversize_response_raises(self):
        body = b"x" * (T.MAX_TRANSCRIPT_BYTES + 10)
        with pytest.raises(ValueError):
            T.http_transcribe(b"a", "c.wav", "audio/wav", TextTurnsConfig(), opener=_Opener(body))

    def test_default_stt_used_when_not_injected(self, monkeypatch):
        monkeypatch.setattr(T, "http_transcribe", lambda *a, **k: "via-default")
        tt, _ = make()
        assert tt.handle_clip(make_wav(0.5)).transcript == "via-default"

    def test_key_not_in_repr(self):
        assert "secret" not in repr(TextTurnsConfig(api_key="secret"))


def _wav(seconds: float, *, rate: int, channels: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * channels * int(seconds * rate))
    return out.getvalue()


class TestTheClipClocksAreMeasured:
    """t23: the clip cap and the stt clock, from the measurement (lapse l3)."""

    def test_a_clip_is_at_most_a_minute(self):
        assert T.MAX_CLIP_SECONDS == 60.0
        assert TextTurnsConfig().max_clip_seconds == 60.0

    def test_the_stt_factor_is_the_measured_max_not_an_assumption(self):
        # docs/live-test-results/2026-10-23-recall-gemma2.md, lapse l3: seconds of
        # transcription per second of audio, median 0.314, p90 0.497, max 0.614 (n=28).
        assert T.STT_SECONDS_PER_AUDIO_SECOND == 0.614
        assert not hasattr(T, "_STT_ASSUMED_REALTIME_FACTOR")
        assert T.STT_DEADLINE_S == pytest.approx(10.0 + 60.0 * 0.614)
        assert TextTurnsConfig().stt_deadline == T.STT_DEADLINE_S

    def test_the_byte_cap_is_one_uncompressed_minute_and_no_more(self):
        # The heaviest uncompressed upload sized for: 48 kHz, 16-bit, stereo.
        minute = _wav(T.MAX_CLIP_SECONDS, rate=48000, channels=2)
        assert len(minute) <= T.MAX_CLIP_BYTES
        assert T.MAX_CLIP_BYTES < 12 * 1000 * 1000, "sized for 60 s, not the old 300 s"
        assert T.MAX_CLIP_BYTES - len(minute) <= 64 * 1024

    def test_a_full_minute_at_the_heaviest_rate_is_accepted(self):
        tt, _ = make(stt=lambda *a: "hi")
        assert tt.handle_clip(_wav(60.0, rate=48000, channels=2)).ok

    def test_a_minute_and_a_bit_is_refused_by_duration(self):
        called = []
        tt, _ = make(stt=lambda *a: called.append(a) or "x")
        r = tt.handle_clip(make_wav(61.0))
        assert r.http_status == 413 and r.code == T.REFUSED_CLIP_TOO_LONG and not called

    def test_the_dashboard_recorder_stops_at_the_same_cap(self):
        source = (
            Path(__file__).resolve().parent.parent / "web/src/components/AudioControls.tsx"
        ).read_text(encoding="utf-8")
        assert f"MAX_CLIP_SECONDS = {int(T.MAX_CLIP_SECONDS)};" in source
