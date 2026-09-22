"""Tests for embodiment.voice — sentence-split synthesis, playback, barge-in (task t12).

Each class below is named for the acceptance criterion (verbatim from the
plan) it proves, plus an attack class for things the criteria do not name
directly but wave-1's lessons require: a resource that blocks, speech leaking
into a record, and input this module's own sentence-splitter has to survive.

Round 2 adds :class:`TestRealTimePacingAndBargeInAfterSpeakReturns` and
:class:`TestHttpSynthesizeVoiceField` for the two defects an independent probe
(run against 3d44fac) found: features fed at ENQUEUE time rather than paced in
real time (criterion 3 was false whenever a barge-in happened — the case it
exists for), and a literal ``"voice": "default"`` reaching the gateway as a
voice NAME rather than being omitted so the gateway falls back to its own
default.
"""

from __future__ import annotations

import math
import struct
import threading
import time
from dataclasses import replace

import pytest

from embodiment.audio.endpoint import EndpointCloseReport
from embodiment.audio.features import BLOCK_SAMPLES, extract_features
from embodiment.voice import (
    BARGE_IN_BOUND_S,
    MAX_REPLY_CHARS,
    MAX_SENTENCE_CHARS,
    MAX_SENTENCES,
    PACE_SLICE_S,
    VOICE_BARGE_IN,
    VOICE_ENDPOINT_FAILED,
    VOICE_NO_BUS,
    VOICE_PUBLISH_FAILED,
    VOICE_TTS_FAILED,
    VOICE_TTS_MALFORMED,
    Voice,
    VoiceConfig,
    http_synthesize,
    split_sentences,
)

# ── test doubles ─────────────────────────────────────────────────────────────


class FakePlayer:
    """A minimal, thread-safe double satisfying the AudioEndpoint Protocol shape.

    ``play()`` returns at once and ``playing`` flips True immediately and
    stays True until ``stop_playback()`` is called — a simple, deterministic
    fake, distinct from :class:`QueueingEndpoint` below (which more closely
    mirrors a real ``HostEndpoint``'s queue-and-drain-later shape, used by the
    round 2 pacing tests).
    """

    def __init__(self, *, discarded_to_return: int = 4800) -> None:
        self.play_calls: list[bytes] = []
        self.stop_calls = 0
        self.discarded_to_return = discarded_to_return
        self._lock = threading.Lock()
        self._playing = False
        self._muted = False

    def attach(self) -> None:
        pass

    def detach(self) -> None:
        pass

    def start_capture(self, on_frame) -> None:  # noqa: ANN001 - test double
        pass

    def stop_capture(self) -> None:
        pass

    def play(self, frames: bytes) -> None:
        with self._lock:
            self.play_calls.append(frames)
            self._playing = True

    def stop_playback(self) -> int:
        with self._lock:
            self.stop_calls += 1
            discarded = self.discarded_to_return if self._playing else 0
            self._playing = False
            return discarded

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._playing

    def mute(self, muted: bool) -> None:
        self._muted = bool(muted)

    @property
    def muted(self) -> bool:
        return self._muted

    def close(self, deadline: float) -> EndpointCloseReport:
        return EndpointCloseReport(
            capture_thread_stopped=True,
            writer_thread_stopped=True,
            samples_discarded=0,
            elapsed_s=0.0,
        )

    def status(self) -> dict[str, object]:
        return {"attached": True, "capturing": False, "playing": self.playing, "muted": self.muted}


class RaisingPlayPlayer(FakePlayer):
    """Endpoint whose ``play`` breaks its own never-raise contract, on purpose."""

    def play(self, frames: bytes) -> None:
        raise RuntimeError("device yanked mid-write")


class RaisingStopPlayer(FakePlayer):
    """Endpoint whose ``stop_playback`` breaks its own never-raise contract."""

    def stop_playback(self) -> int:
        raise RuntimeError("device gone")


class QueueingEndpoint:
    """Shaped like a real ``HostEndpoint``: ``play()`` queues and returns at
    once; nothing drains on its own — only an explicit ``stop_playback()``
    clears the queue and reports how many samples were discarded. This is the
    shape the round 2 probe used (``scratchpad/probe_t12.py``) to show that
    round 1 fed the feature extractor at enqueue time rather than in real
    time.
    """

    def __init__(self) -> None:
        self.q: list[bytes] = []
        self.discarded_total = 0
        self.stop_calls = 0
        self._lock = threading.Lock()

    def play(self, frames: bytes) -> None:
        with self._lock:
            self.q.append(frames)

    @property
    def playing(self) -> bool:
        with self._lock:
            return bool(self.q)

    def stop_playback(self) -> int:
        with self._lock:
            n = sum(len(f) // 2 for f in self.q)
            self.q.clear()
            self.discarded_total += n
            self.stop_calls += 1
            return n

    def mute(self, muted: bool) -> None:
        pass

    @property
    def muted(self) -> bool:
        return False

    def attach(self) -> None:
        pass

    def detach(self) -> None:
        pass

    def start_capture(self, on_frame) -> None:  # noqa: ANN001 - test double
        pass

    def stop_capture(self) -> None:
        pass

    def close(self, deadline: float) -> EndpointCloseReport:
        return EndpointCloseReport(
            capture_thread_stopped=True,
            writer_thread_stopped=True,
            samples_discarded=0,
            elapsed_s=0.0,
        )

    def status(self) -> dict[str, object]:
        return {}


class FakeBus:
    """A double satisfying only the ``publish(kind, data)`` shape Voice needs."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, object]]] = []

    def publish(self, kind: str, data: dict[str, object]):  # noqa: ANN001, ANN201
        self.published.append((kind, dict(data)))
        return object()


class RaisingBus:
    def publish(self, kind, data):  # noqa: ANN001, ANN201
        raise RuntimeError("broker on fire")


def _tone_pcm(n_samples: int = 800, freq_hz: float = 440.0) -> bytes:
    """One real, non-silent pcm16 block — exactly one FeatureExtractor block by default."""
    samples = [int(12000 * math.sin(2 * math.pi * freq_hz * i / 24000)) for i in range(n_samples)]
    return struct.pack(f"<{n_samples}h", *samples)


def _silence_pcm(n_samples: int) -> bytes:
    return b"\x00\x00" * n_samples


def _wait_until(predicate, *, timeout: float = 3.0, interval: float = 0.005) -> bool:
    """Poll *predicate* until it is true or *timeout* elapses. Returns the final reading."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# ── criterion 1: barge-in stops output fast and records the drop ────────────


class TestBargeInStopsOutputFast:
    """Criterion 1 (verbatim): "with a fake player, speech_started mid-playback
    stops output in under 200 ms and the dropped remainder is recorded"."""

    def _run_once(self) -> None:
        player = FakePlayer(discarded_to_return=1234)
        bus = FakeBus()
        payload = _tone_pcm()
        text = ". ".join(f"sentence number {i}" for i in range(10)) + "."
        voice = Voice(
            endpoint=player, bus=bus, synthesize=lambda s, c: (time.sleep(0.03), payload)[1]
        )

        result_holder: dict[str, object] = {}

        def run() -> None:
            result_holder["result"] = voice.speak(text)

        thread = threading.Thread(target=run)
        thread.start()

        # Wait for at least one sentence to have actually reached the player
        # (i.e. genuinely mid-playback), bounded so a defect here fails fast
        # rather than hanging the suite.
        assert _wait_until(
            lambda: bool(player.play_calls), timeout=5.0
        ), "first sentence never reached the player"

        start = time.monotonic()
        discarded = voice.on_speech_started()
        elapsed = time.monotonic() - start

        thread.join(timeout=5.0)
        assert not thread.is_alive(), "speak() did not stop after barge-in"

        assert elapsed < BARGE_IN_BOUND_S, f"on_speech_started took {elapsed}s"
        assert discarded == 1234
        assert player.stop_calls >= 1

        result = result_holder["result"]
        assert result.interrupted is True
        assert result.sentences_dropped > 0
        assert result.sentences_queued < result.sentences_total
        assert result.samples_discarded == 1234

        codes = [d.code for d in voice.degradations]
        assert VOICE_BARGE_IN in codes
        barge_records = [d for d in voice.degradations if d.code == VOICE_BARGE_IN]
        assert len(barge_records) == 1
        # counts only, never sentence text (lesson 5)
        assert "sentence number" not in barge_records[0].reason

        voice.close(deadline=1.0)

    def test_barge_in_run_1(self) -> None:
        self._run_once()

    def test_barge_in_run_2(self) -> None:
        self._run_once()

    def test_barge_in_run_3(self) -> None:
        self._run_once()

    def test_on_speech_started_is_a_fast_noop_when_idle(self) -> None:
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=FakeBus())
        start = time.monotonic()
        discarded = voice.on_speech_started()
        elapsed = time.monotonic() - start
        assert discarded == 0
        assert elapsed < BARGE_IN_BOUND_S
        assert player.stop_calls == 1

    def test_on_speech_started_survives_endpoint_that_raises(self) -> None:
        player = RaisingStopPlayer()
        voice = Voice(endpoint=player, bus=FakeBus())
        start = time.monotonic()
        discarded = voice.on_speech_started()
        elapsed = time.monotonic() - start
        assert discarded == 0
        assert elapsed < BARGE_IN_BOUND_S
        codes = [d.code for d in voice.degradations]
        assert VOICE_ENDPOINT_FAILED in codes


# ── criterion 2: TTS failure -> one degradation, reply still published ──────


class TestTtsFailureStillPublishesReply:
    """Criterion 2 (verbatim): "a TTS failure yields one degradation record and
    the reply text is still published as an event"."""

    def test_tts_failure_records_exactly_one_degradation(self) -> None:
        player = FakePlayer()
        bus = FakeBus()

        def failing_synth(sentence: str, config: VoiceConfig) -> bytes:
            raise RuntimeError("upstream exploded while handling: " + sentence)

        text = "First sentence here. Second sentence here. Third sentence here."
        voice = Voice(endpoint=player, bus=bus, synthesize=failing_synth)
        result = voice.speak(text)

        assert result.tts_degraded is True
        assert result.sentences_queued == 0
        assert player.play_calls == []

        tts_records = [d for d in voice.degradations if d.code == VOICE_TTS_FAILED]
        assert len(tts_records) == 1, "TTS failure must degrade exactly once, not per sentence"
        # no speech in the record: neither the exception message nor the
        # sentence text it echoed back may appear in the reason
        assert "upstream exploded" not in tts_records[0].reason
        assert "First sentence" not in tts_records[0].reason

    def test_reply_text_still_published_on_tts_failure(self) -> None:
        player = FakePlayer()
        bus = FakeBus()

        def failing_synth(sentence: str, config: VoiceConfig) -> bytes:
            raise RuntimeError("boom")

        text = "the weather today is sunny"
        voice = Voice(endpoint=player, bus=bus, synthesize=failing_synth)
        result = voice.speak(text)

        assert result.published is True
        assert bus.published == [("reply", {"text": text})]

    def test_reply_still_published_when_endpoint_play_fails(self) -> None:
        player = RaisingPlayPlayer()
        bus = FakeBus()
        text = "hello there, how are you"
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: _tone_pcm())
        result = voice.speak(text)
        assert result.published is True
        assert bus.published == [("reply", {"text": text})]
        assert VOICE_ENDPOINT_FAILED in [d.code for d in voice.degradations]

    def test_no_bus_degrades_never_raises(self) -> None:
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=None, synthesize=lambda s, c: _tone_pcm())
        result = voice.speak("a reply with nobody listening")
        assert result.published is False
        assert VOICE_NO_BUS in [d.code for d in voice.degradations]

    def test_publish_raising_bus_degrades_never_raises(self) -> None:
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=RaisingBus(), synthesize=lambda s, c: _tone_pcm())
        result = voice.speak("a reply whose bus is on fire")
        assert result.published is False
        assert VOICE_PUBLISH_FAILED in [d.code for d in voice.degradations]

    def test_empty_reply_still_published_and_never_raises(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: _tone_pcm())
        result = voice.speak("")
        assert result.sentences_total == 0
        assert result.published is True
        assert bus.published == [("reply", {"text": ""})]

    def test_non_string_reply_never_raises(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: _tone_pcm())
        result = voice.speak(None)  # type: ignore[arg-type]
        assert result.sentences_total == 0
        assert bus.published == [("reply", {"text": ""})]


# ── criterion 3: played audio is fed to the feature extractor ───────────────


class TestPlayedAudioFedToFeatureExtractor:
    """Criterion 3 (verbatim): "played audio is fed to the feature extractor so
    the assistant trace reflects what was actually spoken". Round 2: feeding
    is paced in real time (see the module docstring), so these tests poll for
    the paced result rather than asserting it is already there the instant
    ``speak()`` returns."""

    def test_played_bytes_produce_the_same_frames_as_extract_features(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        payload = _tone_pcm()  # exactly one BLOCK_SAMPLES block
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: payload)

        result = voice.speak("only one short sentence")

        assert result.sentences_queued == 1
        assert player.play_calls == [payload]
        assert _wait_until(lambda: len(voice.feature_frames) == 1, timeout=3.0)
        assert voice.feature_frames == extract_features(payload)
        voice.close(deadline=1.0)

    def test_dropped_sentence_is_never_fed_to_features(self) -> None:
        """A sentence whose play() failed must not reach the feature extractor
        — it names 'the bytes you actually handed to play', not what TTS
        merely returned."""
        player = RaisingPlayPlayer()
        bus = FakeBus()
        payload = _tone_pcm()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: payload)

        result = voice.speak("this never actually reaches the speaker")

        assert result.sentences_queued == 0
        time.sleep(0.1)  # give a (wrongly-started) pacer a chance to misbehave
        assert voice.feature_frames == []
        voice.close(deadline=1.0)

    def test_multi_sentence_reply_feeds_frames_per_delivered_sentence(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        payload = _tone_pcm()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: payload)

        result = voice.speak("First sentence. Second sentence. Third sentence.")

        assert result.sentences_total == 3
        assert result.sentences_queued == 3
        assert len(player.play_calls) == 3
        assert _wait_until(lambda: len(voice.feature_frames) == 3, timeout=3.0)
        voice.close(deadline=1.0)


# ── round 2, defect 1: real-time pacing + barge-in AFTER speak() returns ────


class TestRealTimePacingAndBargeInAfterSpeakReturns:
    """Regression coverage for the probe's finding: features were fed at
    ENQUEUE time, so a barge-in shortly after ``speak()`` returned (which
    itself returns almost instantly, since it only QUEUES audio) still left
    the trace claiming the whole reply had been spoken."""

    def test_speak_returns_fast_even_for_a_long_reply(self) -> None:
        endpoint = QueueingEndpoint()
        bus = FakeBus()
        one_second = _silence_pcm(24000)
        voice = Voice(endpoint=endpoint, bus=bus, synthesize=lambda s, c: one_second)

        start = time.monotonic()
        result = voice.speak("A one. B two. C three.")
        elapsed = time.monotonic() - start

        assert result.sentences_queued == 3
        assert elapsed < 0.5, "speak() must not block for real-time playback"
        voice.close(deadline=1.0)

    def test_barge_in_shortly_after_speak_returns_corrects_the_trace(self) -> None:
        endpoint = QueueingEndpoint()
        bus = FakeBus()
        one_second = _silence_pcm(24000)  # 24000 samples == 1.0 s each
        voice = Voice(endpoint=endpoint, bus=bus, synthesize=lambda s, c: one_second)

        result = voice.speak("A one. B two. C three.")
        assert result.sentences_queued == 3
        total_samples_queued = 3 * 24000

        # Barge in essentially immediately — before the paced thread has had
        # any real chance to catch up to 3 seconds of "playback".
        discarded = voice.on_speech_started()

        assert discarded > 0, "the queueing endpoint had audio queued to discard"
        traced_frames = len(voice.feature_frames)
        # 3 real seconds of audio is ~90 33ms blocks; the trace must NOT claim
        # anywhere near that much was actually heard.
        assert traced_frames < 90, f"trace claims ~{traced_frames} blocks were spoken"
        assert voice.queued_not_traced > 0
        # Every sample queued to play() is accounted for as EITHER paced into
        # a feature frame, still sitting in the pacing buffer as a partial
        # (sub-block) tail, or counted as queued_not_traced — nothing simply
        # vanishes. Allow one block of slack for a partial tail/race.
        accounted = traced_frames * BLOCK_SAMPLES + voice.queued_not_traced
        assert accounted >= total_samples_queued - BLOCK_SAMPLES

        report = voice.close(deadline=1.0)
        assert report.pace_thread_stopped is True

    def test_status_reports_queued_not_traced_live(self) -> None:
        endpoint = QueueingEndpoint()
        bus = FakeBus()
        payload = _silence_pcm(24000)
        voice = Voice(endpoint=endpoint, bus=bus, synthesize=lambda s, c: payload)

        voice.speak("one. two. three.")
        voice.on_speech_started()

        status = voice.status()
        assert status["queued_not_traced"] == voice.queued_not_traced
        assert status["queued_not_traced"] > 0
        voice.close(deadline=1.0)

    def test_barge_in_during_slow_synth_stays_under_the_bound(self) -> None:
        """Mirrors the probe's scenario 3: a barge-in while synthesis itself
        is slow must still meet the 200 ms bound and record the drop."""
        endpoint = QueueingEndpoint()
        bus = FakeBus()

        def slow_synth(sentence: str, config: VoiceConfig) -> bytes:
            time.sleep(0.2)
            return _silence_pcm(24000)

        voice = Voice(endpoint=endpoint, bus=bus, synthesize=slow_synth)
        result_holder: dict[str, object] = {}

        def run() -> None:
            result_holder["result"] = voice.speak("One. Two. Three. Four.")

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.3)  # let the first slow synth call be in flight

        start = time.monotonic()
        voice.on_speech_started()
        elapsed = time.monotonic() - start
        thread.join(timeout=5.0)

        assert elapsed < BARGE_IN_BOUND_S
        result = result_holder["result"]
        assert result.interrupted is True
        assert result.sentences_dropped > 0
        voice.close(deadline=1.0)

    def test_close_is_idempotent_and_reports_remaining(self) -> None:
        endpoint = QueueingEndpoint()
        bus = FakeBus()
        voice = Voice(endpoint=endpoint, bus=bus, synthesize=lambda s, c: _silence_pcm(24000))
        voice.speak("one. two. three.")

        report1 = voice.close(deadline=1.0)
        assert report1.pace_thread_stopped is True

        report2 = voice.close(deadline=1.0)
        assert report2.pace_thread_stopped is True
        # Idempotent: the cumulative counter does not grow on a second close.
        assert report2.queued_not_traced == report1.queued_not_traced

    def test_close_with_nothing_ever_spoken_is_a_fast_noop(self) -> None:
        voice = Voice(endpoint=QueueingEndpoint(), bus=FakeBus())
        start = time.monotonic()
        report = voice.close(deadline=1.0)
        elapsed = time.monotonic() - start
        assert report.pace_thread_stopped is True
        assert elapsed < 1.0

    def test_pace_slice_is_twenty_milliseconds(self) -> None:
        assert PACE_SLICE_S == 0.02


# ── split_sentences: bounded, never raises ───────────────────────────────────


class TestSplitSentences:
    def test_non_string_returns_empty(self) -> None:
        assert split_sentences(None) == []
        assert split_sentences(12345) == []
        assert split_sentences([]) == []

    def test_empty_and_whitespace_only(self) -> None:
        assert split_sentences("") == []
        assert split_sentences("   \n\t  ") == []

    def test_basic_split(self) -> None:
        out = split_sentences("Hello there. How are you? Fine!")
        assert out == ["Hello there.", "How are you?", "Fine!"]

    def test_no_terminal_punctuation_returns_whole_text(self) -> None:
        out = split_sentences("just one clause with no stop")
        assert out == ["just one clause with no stop"]

    def test_huge_reply_is_bounded_on_both_axes(self) -> None:
        hostile = ("a. " * 100_000) + ("z" * 50_000)
        out = split_sentences(hostile)
        assert len(out) <= MAX_SENTENCES
        assert all(len(s) <= MAX_SENTENCE_CHARS for s in out)

    def test_reply_char_cap_applies_before_splitting(self) -> None:
        hostile = "x" * (MAX_REPLY_CHARS * 3)
        out = split_sentences(hostile)
        assert sum(len(s) for s in out) <= MAX_REPLY_CHARS

    def test_unicode_bidi_and_control_characters_do_not_crash(self) -> None:
        hostile = "hello ‮world‬. \x00\x01 second sentence here."
        out = split_sentences(hostile)
        assert isinstance(out, list)
        assert all(isinstance(s, str) for s in out)

    def test_nul_and_line_separators_survive_as_plain_data(self) -> None:
        # str.splitlines()-honoured separators (U+0085, U+2028) must not
        # silently vanish a sentence or crash the splitter.
        hostile = "first part\u0085second part third part."
        out = split_sentences(hostile)
        assert isinstance(out, list)

    def test_repeated_delimiters_do_not_explode_sentence_count(self) -> None:
        hostile = "." * 5000
        out = split_sentences(hostile)
        assert len(out) <= MAX_SENTENCES


# ── http_synthesize: scheme guard, key placement, voice field ───────────────


class TestHttpSynthesize:
    def test_rejects_non_http_scheme(self) -> None:
        config = VoiceConfig(gateway_url="file:///etc/passwd")
        with pytest.raises(ValueError):
            http_synthesize("hello", config)

    def test_api_key_never_appears_in_repr(self) -> None:
        config = VoiceConfig(api_key="SUPER-SECRET-KEY-MARKER")
        assert "SUPER-SECRET-KEY-MARKER" not in repr(config)

    def test_api_key_goes_only_into_the_authorization_header(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"\x00\x00" * 800

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            return _FakeResponse()

        monkeypatch.setattr("embodiment.voice.urllib.request.urlopen", fake_urlopen)

        config = VoiceConfig(gateway_url="http://gw.example", api_key="SECRET-MARKER-42")
        http_synthesize("hi there", config)

        assert "SECRET-MARKER-42" not in captured["url"]
        assert captured["headers"].get("Authorization") == "Bearer SECRET-MARKER-42"


class TestHttpSynthesizeVoiceField:
    """Round 2, defect 2: lobes' ``parse_speech_request`` reads a PRESENT
    ``voice`` value as a voice name/clone path and only falls back to
    ``settings.default_voice`` when the key is absent — so a literal
    ``"default"`` used to reach the synthesizer as a voice it does not have.
    """

    @staticmethod
    def _capture(monkeypatch) -> dict[str, object]:
        captured: dict[str, object] = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"\x00\x00" * 800

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            import json as _json

            captured["body"] = _json.loads(request.data)
            return _FakeResponse()

        monkeypatch.setattr("embodiment.voice.urllib.request.urlopen", fake_urlopen)
        return captured

    def test_voice_key_omitted_when_config_voice_is_empty(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch)
        config = VoiceConfig(gateway_url="http://gw.example")
        http_synthesize("hello there", config)
        assert "voice" not in captured["body"]
        assert captured["body"]["response_format"] == "pcm"

    def test_voice_key_present_when_config_voice_is_set(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch)
        config = VoiceConfig(gateway_url="http://gw.example", voice="chatterbox-alex")
        http_synthesize("hello there", config)
        assert captured["body"]["voice"] == "chatterbox-alex"

    def test_never_sends_the_literal_string_default(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch)
        http_synthesize("hello there", VoiceConfig(gateway_url="http://gw.example"))
        assert captured["body"].get("voice") != "default"


# ── attacks that found nothing, kept as regression proof ────────────────────


class TestAttacks:
    def test_malformed_synth_return_is_recorded_not_silently_dropped(self) -> None:
        """A synth function that misbehaves (returns a str, not bytes/None)
        without raising must still be recorded — found attacking this module:
        the first draft silently treated it as 'no audio' with nothing to
        show for it (lesson 3)."""
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: "not-bytes-at-all")
        result = voice.speak("hello there. second sentence.")
        assert result.sentences_queued == 0
        assert player.play_calls == []
        codes = [d.code for d in voice.degradations]
        assert VOICE_TTS_MALFORMED in codes
        malformed = [d for d in voice.degradations if d.code == VOICE_TTS_MALFORMED]
        assert len(malformed) == 1  # stops trying after the first, like VOICE_TTS_FAILED

    def test_synth_returning_none_is_treated_as_no_audio_and_recorded(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: None)
        result = voice.speak("hello there")
        assert result.sentences_queued == 0
        assert VOICE_TTS_MALFORMED in [d.code for d in voice.degradations]

    def test_oversize_synth_return_is_truncated_and_recorded(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        huge = _silence_pcm(20 * 24000)  # 20 s, exceeds the 10 s cap
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: huge)
        voice.speak("one sentence only")
        assert len(player.play_calls) == 1
        assert len(player.play_calls[0]) < len(huge)
        from embodiment.voice import VOICE_TTS_OVERSIZE

        assert VOICE_TTS_OVERSIZE in [d.code for d in voice.degradations]
        voice.close(deadline=1.0)

    def test_api_key_never_leaks_into_a_degradation_reason(self) -> None:
        marker = "SECRET-MARKER-ZZZ-999"
        config = VoiceConfig(api_key=marker)

        def failing_synth(sentence: str, cfg: VoiceConfig) -> bytes:
            # An exception whose message legally echoes back request context,
            # simulating a hostile/careless HTTP client library.
            raise RuntimeError(f"401 unauthorized for key={cfg.api_key}")

        voice = Voice(endpoint=FakePlayer(), config=config, bus=FakeBus(), synthesize=failing_synth)
        voice.speak("please synthesize this")

        for record in voice.degradations:
            assert marker not in record.reason
            assert marker not in record.code

    def test_repeated_ten_thousand_calls_stay_bounded(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: b"")
        for _ in range(10_000):
            voice.on_speech_started()
        assert player.stop_calls == 10_000
        # idle no-ops must not accumulate degradations
        assert voice.degradations == []

    def test_feature_frames_bounded_against_a_pathological_reply(self) -> None:
        """Exercises the cap directly (bypassing real-time pacing, which
        would otherwise make this test take minutes of wall-clock time to
        push 3000+ blocks through)."""
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=FakeBus(), synthesize=lambda s, c: b"")
        payload = _tone_pcm()
        for _ in range(3000):
            voice._feed_features(payload)  # noqa: SLF001 - testing the bound directly
        assert len(voice.feature_frames) == 2048
        assert voice.feature_frames_dropped == 3000 - 2048

    def test_concurrent_barge_in_calls_do_not_corrupt_counters(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: _tone_pcm())

        errors: list[BaseException] = []

        def hammer() -> None:
            try:
                for _ in range(200):
                    voice.on_speech_started()
            except BaseException as exc:  # noqa: BLE001 - the assertion is that nothing raises
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        assert not errors
        assert player.stop_calls == 1600

    def test_concurrent_close_calls_do_not_raise(self) -> None:
        endpoint = QueueingEndpoint()
        voice = Voice(endpoint=endpoint, bus=FakeBus(), synthesize=lambda s, c: _silence_pcm(2400))
        voice.speak("one. two. three.")

        errors: list[BaseException] = []

        def closer() -> None:
            try:
                voice.close(deadline=1.0)
            except BaseException as exc:  # noqa: BLE001 - the assertion is that nothing raises
                errors.append(exc)

        threads = [threading.Thread(target=closer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert not errors

    def test_status_never_raises_when_endpoint_playing_is_broken(self) -> None:
        class BrokenPlaying(FakePlayer):
            @property
            def playing(self):  # type: ignore[override]
                raise RuntimeError("broken property")

        voice = Voice(endpoint=BrokenPlaying(), bus=FakeBus(), synthesize=lambda s, c: _tone_pcm())
        voice.speak("one sentence")
        assert _wait_until(lambda: bool(voice.degradations), timeout=3.0)
        status = voice.status()
        assert isinstance(status, dict)
        voice.close(deadline=1.0)

    def test_config_is_frozen(self) -> None:
        config = VoiceConfig()
        with pytest.raises(Exception):
            config.api_key = "nope"  # type: ignore[misc]

    def test_replace_config_does_not_mutate_shared_default(self) -> None:
        base = VoiceConfig()
        derived = replace(base, api_key="x")
        assert base.api_key == ""
        assert derived.api_key == "x"
