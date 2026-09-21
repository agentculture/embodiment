"""Tests for :mod:`embodiment.audio.features` — proves plan task ``t8``'s
acceptance criteria (spec target ``h21``):

1. a 440 Hz test tone yields a slice whose zero-crossing rate matches 440 Hz
   within 2%, and a level within 1 dB of expected.
2. silence yields a flat slice and a level at the noise floor, never a
   synthetic wiggle.
3. one second of audio produces under 8 kB of feature payload.
"""

from __future__ import annotations

import json
import math
import struct

import pytest

from embodiment.audio.features import (
    BLOCK_SAMPLES,
    SAMPLE_RATE_HZ,
    WAVE_POINTS,
    extract_features,
)

_INT16_MAX = 32767


def _tone_pcm(frequency_hz: float, duration_s: float, amplitude: float = 16000.0) -> bytes:
    """Real pcm16 mono little-endian bytes of a sine tone. No dithering."""
    n_samples = round(SAMPLE_RATE_HZ * duration_s)
    samples = [
        int(round(amplitude * math.sin(2.0 * math.pi * frequency_hz * i / SAMPLE_RATE_HZ)))
        for i in range(n_samples)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def _silence_pcm(duration_s: float) -> bytes:
    n_samples = round(SAMPLE_RATE_HZ * duration_s)
    return b"\x00\x00" * n_samples


# ---------------------------------------------------------------------------
# Criterion 1 — 440 Hz tone: zero-crossing rate within 2%, level within 1 dB
# ---------------------------------------------------------------------------


def test_440hz_tone_zero_crossing_rate_within_2_percent():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)

    assert frames, "expected at least one frame from a 1s tone"
    measured = [f["zero_crossing_hz"] for f in frames if f["zero_crossing_hz"] is not None]
    assert measured, "no block produced a zero-crossing estimate"

    for hz in measured:
        error = abs(hz - 440.0) / 440.0
        assert error <= 0.02, f"zero-crossing estimate {hz} Hz off by {error:.4%}"

    average = sum(measured) / len(measured)
    assert abs(average - 440.0) / 440.0 <= 0.02


def test_440hz_tone_level_within_1db_of_expected():
    amplitude = 16000.0
    pcm = _tone_pcm(440.0, duration_s=1.0, amplitude=amplitude)
    frames = extract_features(pcm)

    # RMS of a sine of peak amplitude A is A / sqrt(2).
    expected_rms = amplitude / math.sqrt(2.0)
    expected_db = 20.0 * math.log10(expected_rms / _INT16_MAX)

    levels = [f["level_db"] for f in frames]
    assert levels
    for level in levels:
        assert abs(level - expected_db) <= 1.0, f"level {level} dB vs expected {expected_db} dB"


# ---------------------------------------------------------------------------
# Criterion 2 — silence: flat slice, level at the noise floor, no wiggle
# ---------------------------------------------------------------------------


def test_silence_yields_a_flat_waveform_slice():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        assert frame["waveform"] == [0] * len(frame["waveform"])


def test_silence_level_sits_at_the_noise_floor():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        assert frame["level_db"] == frame["noise_floor_db"]
        # Never above a conservative silence ceiling — this is the floor, not
        # a mid-range level.
        assert frame["level_db"] <= -60.0


def test_silence_produces_no_invented_zero_crossings():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        assert frame["zero_crossing_hz"] is None


def test_silence_waveform_is_identical_across_blocks_no_dithering():
    """A flat slice must stay flat block to block — never a wiggle invented
    by smoothing/dithering."""
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    waveforms = {tuple(f["waveform"]) for f in frames}
    assert waveforms == {(0,) * len(frames[0]["waveform"])}


# ---------------------------------------------------------------------------
# Criterion 3 — payload size budget
# ---------------------------------------------------------------------------


def test_one_second_of_audio_payload_under_8kb():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)

    payload = json.dumps(frames)
    assert len(payload) < 8 * 1024, f"payload is {len(payload)} bytes"


def test_one_second_of_silence_payload_under_8kb():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    payload = json.dumps(frames)
    assert len(payload) < 8 * 1024


def test_one_second_of_noise_payload_under_8kb():
    """White-noise-like content is the worst case for the decimated slice —
    every quantized sample can differ from its neighbour."""
    import random

    rng = random.Random(1234)  # noqa: DUO102 - deterministic test fixture, not security
    n_samples = SAMPLE_RATE_HZ
    samples = [rng.randint(-32768, 32767) for _ in range(n_samples)]
    pcm = struct.pack(f"<{n_samples}h", *samples)

    payload = json.dumps(extract_features(pcm))
    assert len(payload) < 8 * 1024, f"payload is {len(payload)} bytes"


# ---------------------------------------------------------------------------
# Never raises on malformed input
# ---------------------------------------------------------------------------


def test_empty_buffer_returns_empty_list():
    assert extract_features(b"") == []


def test_odd_byte_length_does_not_raise():
    frames = extract_features(b"\x01\x02\x03")
    assert frames == []


def test_buffer_shorter_than_one_block_does_not_raise():
    frames = extract_features(b"\x00\x01" * 10)
    assert frames == []


def test_garbage_bytes_do_not_raise():
    frames = extract_features(bytes(range(256)) * 10)
    assert isinstance(frames, list)


@pytest.mark.parametrize("bad", [None, 12345, "not bytes", [1, 2, 3]])
def test_non_bytes_input_does_not_raise(bad):
    frames = extract_features(bad)
    assert frames == []


# ---------------------------------------------------------------------------
# Shape / contract
# ---------------------------------------------------------------------------


def test_frames_are_json_serialisable_plain_data():
    pcm = _tone_pcm(440.0, duration_s=0.5)
    frames = extract_features(pcm)

    # Round-trips cleanly and produces the same plain structures back.
    round_tripped = json.loads(json.dumps(frames))
    assert round_tripped == frames


def test_block_size_matches_roughly_33ms():
    block_ms = 1000.0 * BLOCK_SAMPLES / SAMPLE_RATE_HZ
    assert 30.0 <= block_ms <= 40.0


def test_waveform_slice_length_is_stable_and_decimated():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)

    for frame in frames:
        assert len(frame["waveform"]) == WAVE_POINTS
        assert len(frame["waveform"]) < BLOCK_SAMPLES


def test_one_second_tone_yields_expected_block_count():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)
    expected_blocks = SAMPLE_RATE_HZ // BLOCK_SAMPLES
    assert len(frames) == expected_blocks
