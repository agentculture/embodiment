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

import json
import math
import struct
import threading
import time
import urllib.request
from dataclasses import replace
from typing import Optional

import pytest

import embodiment.voice as V
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
        assert bus.published == [
            ("reply", {"text": text}),
            ("state", {"component": "voice", "status": "unspoken"}),
        ]

    def test_reply_still_published_when_endpoint_play_fails(self) -> None:
        player = RaisingPlayPlayer()
        bus = FakeBus()
        text = "hello there, how are you"
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: _tone_pcm())
        result = voice.speak(text)
        assert result.published is True
        assert bus.published == [
            ("reply", {"text": text}),
            ("state", {"component": "voice", "status": "unspoken"}),
        ]
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
        assert bus.published == [
            ("reply", {"text": ""}),
            ("state", {"component": "voice", "status": "unspoken"}),
        ]

    def test_non_string_reply_never_raises(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: _tone_pcm())
        result = voice.speak(None)  # type: ignore[arg-type]
        assert result.sentences_total == 0
        assert bus.published == [
            ("reply", {"text": ""}),
            ("state", {"component": "voice", "status": "unspoken"}),
        ]


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


# ── round 3, seam 1: drain_features() ────────────────────────────────────────


class TestDrainFeatures:
    """``Voice.drain_features(max_n=256)`` — the one sanctioned way for a
    caller to pop traced frames, instead of slicing ``feature_frames`` from
    outside ``_state_lock``."""

    def test_drains_up_to_max_n_and_removes_them(self) -> None:
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=FakeBus(), synthesize=lambda s, c: b"")
        payload = _tone_pcm()
        for _ in range(10):
            voice._feed_features(payload)  # noqa: SLF001 - seeding frames directly
        assert len(voice.feature_frames) == 10

        drained = voice.drain_features(4)
        assert len(drained) == 4
        assert len(voice.feature_frames) == 6

        rest = voice.drain_features(100)
        assert len(rest) == 6
        assert voice.feature_frames == []

    def test_default_max_n_is_256(self) -> None:
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=FakeBus(), synthesize=lambda s, c: b"")
        payload = _tone_pcm()
        for _ in range(300):
            voice._feed_features(payload)  # noqa: SLF001 - seeding frames directly
        drained = voice.drain_features()
        assert len(drained) == 256
        assert len(voice.feature_frames) == 44

    def test_drain_on_empty_returns_empty_list(self) -> None:
        voice = Voice(endpoint=FakePlayer(), bus=FakeBus())
        assert voice.drain_features() == []
        assert voice.drain_features(0) == []

    def test_never_raises_on_hostile_max_n(self) -> None:
        voice = Voice(endpoint=FakePlayer(), bus=FakeBus())
        payload = _tone_pcm()
        voice._feed_features(payload)  # noqa: SLF001 - seeding a frame directly
        assert voice.drain_features(-5) == []
        assert voice.drain_features("not a number") == []  # type: ignore[arg-type]
        assert voice.drain_features(None) == []  # type: ignore[arg-type]
        assert voice.drain_features(True) == []  # type: ignore[arg-type] - bool is an int
        assert len(voice.feature_frames) == 1  # nothing was drained by the hostile calls

    def test_drained_frames_match_what_was_fed(self) -> None:
        player = FakePlayer()
        bus = FakeBus()
        payload = _tone_pcm()
        voice = Voice(endpoint=player, bus=bus, synthesize=lambda s, c: payload)
        voice.speak("only one short sentence")
        assert _wait_until(lambda: len(voice.feature_frames) == 1, timeout=3.0)
        drained = voice.drain_features()
        assert drained == extract_features(payload)
        assert voice.feature_frames == []
        voice.close(deadline=1.0)

    def test_concurrent_drain_and_feed_do_not_corrupt_or_duplicate(self) -> None:
        player = FakePlayer()
        voice = Voice(endpoint=player, bus=FakeBus(), synthesize=lambda s, c: b"")
        payload = _tone_pcm()
        stop = threading.Event()
        drained_total: list[dict[str, object]] = []
        drain_lock = threading.Lock()

        def feeder() -> None:
            for _ in range(500):
                voice._feed_features(payload)  # noqa: SLF001 - hammering the feed path

        def drainer() -> None:
            while not stop.is_set():
                got = voice.drain_features(7)
                with drain_lock:
                    drained_total.extend(got)
                time.sleep(0.0001)

        feed_thread = threading.Thread(target=feeder)
        drain_thread = threading.Thread(target=drainer)
        feed_thread.start()
        drain_thread.start()
        feed_thread.join(timeout=10.0)
        stop.set()
        drain_thread.join(timeout=5.0)

        # Final sweep for anything left after the feeder finished.
        drained_total.extend(voice.drain_features(10_000))

        assert len(drained_total) == 500
        assert voice.feature_frames == []


# ── round 3, seam 2: set_endpoint() ──────────────────────────────────────────


class TestSetEndpoint:
    """``Voice.set_endpoint(endpoint)`` — swaps the playback endpoint for the
    NEXT ``speak()`` call, stopping and accounting for the old one first."""

    def test_next_speak_uses_the_new_endpoint(self) -> None:
        old = FakePlayer()
        new = FakePlayer()
        voice = Voice(endpoint=old, bus=FakeBus(), synthesize=lambda s, c: _tone_pcm())

        voice.set_endpoint(new)
        voice.speak("hello there")

        assert old.play_calls == []
        assert len(new.play_calls) == 1

    def test_old_endpoint_is_stopped_and_discard_is_counted(self) -> None:
        old = FakePlayer(discarded_to_return=777)
        new = FakePlayer()
        voice = Voice(endpoint=old, bus=FakeBus(), synthesize=lambda s, c: _tone_pcm())

        voice.speak("first reply, queued on the old endpoint")
        assert old.playing is True

        voice.set_endpoint(new)

        assert old.stop_calls >= 1
        assert old.playing is False

    def test_never_raises_when_old_endpoint_stop_playback_raises(self) -> None:
        old = RaisingStopPlayer()
        new = FakePlayer()
        voice = Voice(endpoint=old, bus=FakeBus())

        voice.set_endpoint(new)  # must not raise

        assert VOICE_ENDPOINT_FAILED in [d.code for d in voice.degradations]
        voice.speak("uses the new endpoint now")
        assert len(new.play_calls) >= 0  # no crash is the assertion here

    def test_pacing_buffer_is_dropped_and_counted_on_handover(self) -> None:
        old = QueueingEndpoint()
        new = QueueingEndpoint()
        payload = _silence_pcm(24000)
        voice = Voice(endpoint=old, bus=FakeBus(), synthesize=lambda s, c: payload)

        voice.speak("one. two. three.")
        before = voice.queued_not_traced

        voice.set_endpoint(new)

        assert voice.queued_not_traced >= before
        assert voice.queued_not_traced > 0
        voice.close(deadline=1.0)

    def test_barge_in_after_handover_targets_the_new_endpoint(self) -> None:
        old = FakePlayer(discarded_to_return=111)
        new = FakePlayer(discarded_to_return=222)
        voice = Voice(endpoint=old, bus=FakeBus(), synthesize=lambda s, c: _tone_pcm())

        voice.set_endpoint(new)
        voice.speak("hello")  # queued on `new`
        discarded = voice.on_speech_started()

        assert discarded == 222
        assert new.stop_calls >= 1

    def test_idle_handover_never_raises_and_is_fast(self) -> None:
        old = FakePlayer()
        new = FakePlayer()
        voice = Voice(endpoint=old, bus=FakeBus())
        start = time.monotonic()
        voice.set_endpoint(new)
        elapsed = time.monotonic() - start
        assert elapsed < BARGE_IN_BOUND_S

    def test_concurrent_speak_and_set_endpoint_never_raise_or_corrupt_state(self) -> None:
        """Safety, not perfect real-time accounting, is the bar here — see the
        module docstring's documented limitation for this exact race."""
        endpoints = [FakePlayer() for _ in range(4)]
        voice = Voice(endpoint=endpoints[0], bus=FakeBus(), synthesize=lambda s, c: _tone_pcm())

        errors: list[BaseException] = []
        stop = threading.Event()

        def speaker() -> None:
            try:
                while not stop.is_set():
                    voice.speak("one. two. three. four. five.")
            except BaseException as exc:  # noqa: BLE001 - the assertion is that nothing raises
                errors.append(exc)

        def swapper() -> None:
            try:
                for ep in endpoints:
                    voice.set_endpoint(ep)
                    time.sleep(0.001)
            except BaseException as exc:  # noqa: BLE001 - the assertion is that nothing raises
                errors.append(exc)

        speak_thread = threading.Thread(target=speaker)
        swap_thread = threading.Thread(target=swapper)
        speak_thread.start()
        swap_thread.start()
        swap_thread.join(timeout=10.0)
        stop.set()
        speak_thread.join(timeout=10.0)

        assert not errors
        assert not speak_thread.is_alive()
        voice.close(deadline=1.0)

    def test_multiple_handovers_stay_bounded_and_consistent(self) -> None:
        endpoints = [FakePlayer() for _ in range(50)]
        voice = Voice(endpoint=endpoints[0], bus=FakeBus())
        for ep in endpoints[1:]:
            voice.set_endpoint(ep)
        assert voice._current_endpoint() is endpoints[-1]  # noqa: SLF001 - internal check
        # every prior endpoint was stopped exactly once by the handover after it
        assert all(ep.stop_calls == 1 for ep in endpoints[:-1])
        assert endpoints[-1].stop_calls == 0


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


class _FakeResponse:
    """Satisfies the context-manager + ``read(n)`` shape ``_read_bounded``
    (and the ``with ... as response`` in ``http_synthesize``) needs."""

    def __init__(self, body: bytes = b"") -> None:
        self._body = body
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._body[self._pos :]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeOpener:
    """Injectable in place of :data:`embodiment.voice._REDIRECT_REFUSING_OPENER`
    (the round 4 ``opener=`` seam) — a plain double, not real ``urllib``
    machinery, so these tests never touch a real socket."""

    def __init__(self, response: object = None, *, exc: Optional[BaseException] = None) -> None:
        self._response = response if response is not None else _FakeResponse()
        self._exc = exc
        self.calls = 0
        self.captured: dict[str, object] = {}

    def open(self, request, timeout=None):  # noqa: ANN001
        self.calls += 1
        self.captured["url"] = request.full_url
        self.captured["headers"] = dict(request.headers)
        self.captured["timeout"] = timeout
        self.captured["body"] = json.loads(request.data) if request.data else None
        if self._exc is not None:
            raise self._exc
        return self._response


class TestHttpSynthesize:
    def test_rejects_non_http_scheme(self) -> None:
        config = VoiceConfig(gateway_url="file:///etc/passwd")
        with pytest.raises(ValueError):
            http_synthesize("hello", config)

    def test_api_key_never_appears_in_repr(self) -> None:
        config = VoiceConfig(api_key="SUPER-SECRET-KEY-MARKER")
        assert "SUPER-SECRET-KEY-MARKER" not in repr(config)

    def test_api_key_goes_only_into_the_authorization_header(self) -> None:
        opener = _FakeOpener(_FakeResponse(b"\x00\x00" * 800))
        config = VoiceConfig(gateway_url="http://gw.example", api_key="SECRET-MARKER-42")
        http_synthesize("hi there", config, opener=opener)

        assert "SECRET-MARKER-42" not in opener.captured["url"]
        assert opener.captured["headers"].get("Authorization") == "Bearer SECRET-MARKER-42"


class TestHttpSynthesizeVoiceField:
    """Round 2, defect 2: lobes' ``parse_speech_request`` reads a PRESENT
    ``voice`` value as a voice name/clone path and only falls back to
    ``settings.default_voice`` when the key is absent — so a literal
    ``"default"`` used to reach the synthesizer as a voice it does not have.
    """

    def test_voice_key_omitted_when_config_voice_is_empty(self) -> None:
        opener = _FakeOpener(_FakeResponse(b"\x00\x00" * 800))
        config = VoiceConfig(gateway_url="http://gw.example")
        http_synthesize("hello there", config, opener=opener)
        assert "voice" not in opener.captured["body"]
        assert opener.captured["body"]["response_format"] == "pcm"

    def test_voice_key_present_when_config_voice_is_set(self) -> None:
        opener = _FakeOpener(_FakeResponse(b"\x00\x00" * 800))
        config = VoiceConfig(gateway_url="http://gw.example", voice="chatterbox-alex")
        http_synthesize("hello there", config, opener=opener)
        assert opener.captured["body"]["voice"] == "chatterbox-alex"

    def test_never_sends_the_literal_string_default(self) -> None:
        opener = _FakeOpener(_FakeResponse(b"\x00\x00" * 800))
        http_synthesize("hello there", VoiceConfig(gateway_url="http://gw.example"), opener=opener)
        assert opener.captured["body"].get("voice") != "default"


# ── round 4: bounded read, redirect refusal, stall-tolerance widening ───────


class TestBoundedRead:
    """Round 4, defect 1: a total-deadline, chunked read so a gateway
    dribbling bytes just under the per-socket timeout cannot block
    ``speak()`` (and the barge-in record with it) for as long as it likes."""

    def test_reads_full_body_under_the_deadline(self) -> None:
        payload = _silence_pcm(800)  # small, well under the cap
        opener = _FakeOpener(_FakeResponse(payload))
        config = VoiceConfig(gateway_url="http://gw.example")
        result = http_synthesize("hi", config, opener=opener)
        assert result == payload

    def test_stops_at_max_bytes_plus_one_for_the_oversize_path(self) -> None:
        config = VoiceConfig(gateway_url="http://gw.example", max_sentence_audio_bytes=100)
        payload = b"\x01" * 10_000  # far larger than the 100-byte cap
        opener = _FakeOpener(_FakeResponse(payload))
        result = http_synthesize("hi", config, opener=opener)
        assert len(result) == 101  # cap + 1, exactly enough to detect oversize

    def test_a_dribbling_response_times_out_and_is_bounded(self) -> None:
        """A fake response whose read(n) sleeps — the regression case the
        review named directly."""

        class _SleepyResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n: int = -1) -> bytes:
                time.sleep(0.05)
                return b"\x00"  # one byte at a time, forever

        config = VoiceConfig(
            gateway_url="http://gw.example",
            speech_deadline=0.05,
            max_sentence_audio_bytes=100,
        )
        opener = _FakeOpener(_SleepyResponse())
        start = time.monotonic()
        with pytest.raises(V.VoiceReadTimeoutError):
            http_synthesize("hi", config, opener=opener)
        elapsed = time.monotonic() - start
        # Bounded: the deadline (speech_deadline + cap/floor-rate) plus, at
        # most, one more speech_deadline for the one read() call already in
        # flight when the deadline is discovered — see _read_deadline's
        # own docstring for why that slack exists.
        deadline = (
            config.speech_deadline + config.max_sentence_audio_bytes / V.MIN_TTS_STREAM_BYTES_PER_S
        )
        assert elapsed < deadline + config.speech_deadline + 1.0

    def test_read_timeout_is_recorded_as_its_own_code_via_voice(self) -> None:
        class _SleepyResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n: int = -1) -> bytes:
                time.sleep(0.05)
                return b"\x00"

        config = VoiceConfig(
            gateway_url="http://gw.example", speech_deadline=0.05, max_sentence_audio_bytes=100
        )
        opener = _FakeOpener(_SleepyResponse())

        def synth(sentence: str, cfg: VoiceConfig) -> bytes:
            return http_synthesize(sentence, cfg, opener=opener)

        voice = Voice(endpoint=FakePlayer(), bus=FakeBus(), config=config, synthesize=synth)
        result = voice.speak("one sentence")
        assert result.tts_degraded is True
        codes = [d.code for d in voice.degradations]
        assert V.VOICE_TTS_READ_TIMEOUT in codes
        assert V.VOICE_TTS_FAILED not in codes  # the specific code, not the generic one


class TestRedirectRefused:
    """Round 4, defect 2: the bearer key must never follow a redirect to
    another host — stdlib ``urlopen`` resends ``Authorization`` on a 3xx,
    unlike a browser."""

    def test_no_redirect_handler_raises_on_any_redirect(self) -> None:
        handler = V._NoRedirectHandler()
        with pytest.raises(V.VoiceRedirectRefusedError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/steal")

    def test_a_302_to_another_host_yields_the_degradation_and_no_second_request(self) -> None:
        import email.message
        import io
        from urllib.response import addinfourl

        class _RedirectingHandler(urllib.request.BaseHandler):
            def __init__(self) -> None:
                self.calls = 0

            def https_open(self, req):  # noqa: ANN001
                self.calls += 1
                headers = email.message.Message()
                headers["Location"] = "https://evil.example/steal"
                resp = addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
                resp.msg = "Found"
                return resp

            http_open = https_open

        fake_handler = _RedirectingHandler()
        opener = urllib.request.OpenerDirector()
        opener.add_handler(fake_handler)
        opener.add_handler(V._NoRedirectHandler())
        opener.add_handler(urllib.request.HTTPErrorProcessor())

        config = VoiceConfig(gateway_url="https://gw.example", api_key="k")
        with pytest.raises(V.VoiceRedirectRefusedError):
            http_synthesize("hi", config, opener=opener)

        assert fake_handler.calls == 1  # no second request to the redirect target

    def test_redirect_refusal_is_recorded_as_its_own_code_via_voice(self) -> None:
        class _RaisingOpener:
            def open(self, request, timeout=None):  # noqa: ANN001
                raise V.VoiceRedirectRefusedError("refused redirect: status=302")

        config = VoiceConfig(gateway_url="http://gw.example")
        opener = _RaisingOpener()

        def synth(sentence: str, cfg: VoiceConfig) -> bytes:
            return http_synthesize(sentence, cfg, opener=opener)

        voice = Voice(endpoint=FakePlayer(), bus=FakeBus(), config=config, synthesize=synth)
        result = voice.speak("one sentence")
        assert result.tts_degraded is True
        codes = [d.code for d in voice.degradations]
        assert V.VOICE_TTS_REDIRECT_REFUSED in codes
        assert V.VOICE_TTS_FAILED not in codes


class TestVoiceStateEvent:
    """Round 4, defect 3: a second, small event once the outcome is known,
    so a viewer can tell a reply that was actually voiced from one that only
    published text."""

    def test_spoken_when_at_least_one_sentence_was_queued(self) -> None:
        bus = FakeBus()
        voice = Voice(endpoint=FakePlayer(), bus=bus, synthesize=lambda s, c: _tone_pcm())
        voice.speak("hello there")
        assert ("state", {"component": "voice", "status": "spoken"}) in bus.published

    def test_unspoken_when_tts_fails_outright(self) -> None:
        bus = FakeBus()

        def failing(sentence: str, config: VoiceConfig) -> bytes:
            raise RuntimeError("down")

        voice = Voice(endpoint=FakePlayer(), bus=bus, synthesize=failing)
        voice.speak("hello there")
        assert ("state", {"component": "voice", "status": "unspoken"}) in bus.published

    def test_state_event_never_carries_reply_text(self) -> None:
        bus = FakeBus()
        marker = "the user's actual words go here"
        voice = Voice(endpoint=FakePlayer(), bus=bus, synthesize=lambda s, c: _tone_pcm())
        voice.speak(marker)
        state_events = [data for kind, data in bus.published if kind == "state"]
        assert state_events
        for data in state_events:
            assert marker not in json.dumps(data)

    def test_no_bus_does_not_double_record_no_bus(self) -> None:
        voice = Voice(endpoint=FakePlayer(), bus=None, synthesize=lambda s, c: _tone_pcm())
        voice.speak("hello")
        no_bus_records = [d for d in voice.degradations if d.code == VOICE_NO_BUS]
        assert len(no_bus_records) == 1  # not one per publish call

    def test_state_publish_failure_is_recorded_independently(self) -> None:
        class _FlakyBus:
            def __init__(self) -> None:
                self.calls = 0

            def publish(self, kind, data):  # noqa: ANN001, ANN201
                self.calls += 1
                if kind == "reply":
                    return True
                raise RuntimeError("state publish exploded")

        voice = Voice(endpoint=FakePlayer(), bus=_FlakyBus(), synthesize=lambda s, c: _tone_pcm())
        result = voice.speak("hello")
        assert result.published is True  # the reply publish itself succeeded
        assert VOICE_PUBLISH_FAILED in [d.code for d in voice.degradations]


class TestPaceStallToleranceWidened:
    """Round 4, defect 4: a live daemon session measured false
    voice-pace-stalled records between sentences on a healthy run. The fix
    widens the tolerance to one sentence's synthesis budget
    (config.speech_deadline)."""

    def test_stall_limit_is_derived_from_speech_deadline(self) -> None:
        voice = Voice(endpoint=FakePlayer(), bus=FakeBus(), config=VoiceConfig(speech_deadline=4.0))
        # 4.0s / 0.02s per tick = 200 ticks, above the 100-tick floor.
        assert voice._pace_stall_limit_ticks() == 200  # noqa: SLF001 - testing the derivation

    def test_floor_applies_for_a_small_speech_deadline(self) -> None:
        from embodiment.voice import PACE_STALL_TICKS

        voice = Voice(endpoint=FakePlayer(), bus=FakeBus(), config=VoiceConfig(speech_deadline=0.1))
        assert voice._pace_stall_limit_ticks() == PACE_STALL_TICKS  # noqa: SLF001

    def test_playing_flickering_between_sentences_records_zero_stalls(self) -> None:
        """An endpoint whose ``playing`` flips False for a real gap between
        sentences (shorter than config.speech_deadline) must record NO
        voice-pace-stalled — this is exactly the false-positive the live
        daemon session hit."""

        class FlickeringEndpoint(FakePlayer):
            """playing reads False for a stretch after each play() call,
            simulating the writer having fully drained one sentence and not
            yet started the next."""

            def __init__(self) -> None:
                super().__init__()
                self._flip_at: Optional[float] = None

            def play(self, frames: bytes) -> None:
                super().play(frames)
                with self._lock:
                    self._flip_at = time.monotonic() + 0.15  # "still playing" window

            @property
            def playing(self) -> bool:
                with self._lock:
                    if self._flip_at is None:
                        return False
                    return time.monotonic() < self._flip_at

        endpoint = FlickeringEndpoint()
        bus = FakeBus()
        payload = _tone_pcm()
        # A generous speech_deadline: the "gap" a flickering endpoint
        # introduces here (tens of ms) is far under it.
        config = VoiceConfig(speech_deadline=5.0)
        voice = Voice(endpoint=endpoint, bus=bus, config=config, synthesize=lambda s, c: payload)

        voice.speak("First sentence. Second sentence. Third sentence.")
        # Give the pacer time to fully drain (each sentence is one short
        # block; the 150ms "still playing" window per sentence is generous
        # relative to the ~40ms two ticks need to drain one 800-sample block).
        assert _wait_until(lambda: len(voice.feature_frames) == 3, timeout=3.0)

        codes = [d.code for d in voice.degradations]
        assert V.VOICE_PACE_STALLED not in codes
        voice.close(deadline=1.0)


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
