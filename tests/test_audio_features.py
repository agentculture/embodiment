"""Tests for :mod:`embodiment.audio.features` — proves plan task ``t8``'s
acceptance criteria (spec target ``h21``):

1. a 440 Hz test tone yields a slice whose zero-crossing rate matches 440 Hz
   within 2%, and a level within 1 dB of expected.
2. silence yields a flat slice and a level at the noise floor, never a
   synthetic wiggle.
3. one second of audio produces under 8 kB of feature payload.

The waveform field started as a fixed-stride decimated slice (16 samples per
33 ms block, stride 50) but that aliases: it effectively resamples at 480 Hz
with no low-pass ahead of it, so anything above the 240 Hz Nyquist limit that
implies — including the module's own 440 Hz test tone — draws as a slow,
wrong-frequency wiggle rather than the real waveform shape. It was replaced
with a per-bucket min/max envelope (``env_min`` / ``env_max``), which is
alias-free because a bucket's extremes do not depend on which samples a
stride happens to land on. ``test_envelope_amplitude_is_frequency_independent``
and ``test_strided_slice_would_have_missed_an_off_stride_spike`` are the tests
that hold that property to account.
"""

from __future__ import annotations

import json
import math
import random
import struct

import pytest

from embodiment.audio.features import (
    BLOCK_SAMPLES,
    ENVELOPE_POINTS,
    FLOOR_DB,
    SAMPLE_RATE_HZ,
    extract_features,
)

_INT16_MAX = 32767
_BUCKET_SAMPLES = BLOCK_SAMPLES // ENVELOPE_POINTS

# Seeded once at module scope: deterministic, and importing `random` here
# (rather than inside a test function) keeps the import where every other
# import in this file lives.
_RNG = random.Random(1234)


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


def _quantize_step() -> float:
    """One quantization step in the published env_min/env_max range."""
    return 256.0  # matches features._QUANT_SCALE; see that module's docstring


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
# Envelope is alias-free: amplitude reads the same regardless of frequency,
# and an off-stride transient is never skipped over.
# ---------------------------------------------------------------------------


def test_envelope_amplitude_is_frequency_independent():
    """A 3 kHz tone and a 440 Hz tone must report the same amplitude, in
    EVERY bucket, within one quantization step. A fixed-stride slice (the
    rejected design) fails this: it resamples at 480 Hz with no low-pass, so
    3 kHz content — well above its 240 Hz Nyquist limit — aliases down to a
    slow, wrong-amplitude wiggle instead of tracking the true envelope.
    """
    amplitude = 16000.0
    step = _quantize_step()

    for freq in (440.0, 3000.0):
        pcm = _tone_pcm(freq, duration_s=1.0, amplitude=amplitude)
        frames = extract_features(pcm)
        assert frames

        expected_max = round(amplitude / step)
        expected_min = -expected_max

        for frame in frames:
            for lo, hi in zip(frame["env_min"], frame["env_max"]):
                assert abs(hi - expected_max) <= 1, (
                    f"{freq} Hz: bucket max {hi} vs expected {expected_max} "
                    f"(quantization step {step})"
                )
                assert abs(lo - expected_min) <= 1, (
                    f"{freq} Hz: bucket min {lo} vs expected {expected_min} "
                    f"(quantization step {step})"
                )


def test_envelope_catches_an_off_stride_transient_in_exactly_one_bucket():
    """A single full-scale sample, off any 50-sample stride boundary, must
    show up in exactly one bucket's envelope."""
    samples = [0] * BLOCK_SAMPLES
    spike_index = 25  # deliberately not a multiple of the old stride (50)
    samples[spike_index] = 32767
    block_pcm = struct.pack(f"<{BLOCK_SAMPLES}h", *samples)

    frames = extract_features(block_pcm)
    assert len(frames) == 1
    frame = frames[0]

    spike_bucket = spike_index // _BUCKET_SAMPLES
    for i, (lo, hi) in enumerate(zip(frame["env_min"], frame["env_max"])):
        if i == spike_bucket:
            assert hi > 0, "the bucket containing the spike must show it"
        else:
            assert lo == 0 and hi == 0, f"bucket {i} should be silent, got ({lo}, {hi})"


def test_strided_slice_would_have_missed_an_off_stride_spike():
    """Demonstrates the failure mode the envelope design fixes: the OLD
    fixed-stride approach (samples picked every 50th index) would have missed
    a spike that does not land on a stride boundary."""
    samples = [0] * BLOCK_SAMPLES
    spike_index = 25
    samples[spike_index] = 32767

    old_stride_slice = samples[::_BUCKET_SAMPLES][:ENVELOPE_POINTS]
    assert spike_index not in range(
        0, BLOCK_SAMPLES, _BUCKET_SAMPLES
    ), "test setup: the spike must not land on a stride boundary"
    assert (
        max(old_stride_slice) == 0
    ), "the old strided slice should show no trace of the spike at all"

    # ...whereas the envelope this module actually ships DOES catch it.
    block_pcm = struct.pack(f"<{BLOCK_SAMPLES}h", *samples)
    frame = extract_features(block_pcm)[0]
    assert max(frame["env_max"]) > 0


# ---------------------------------------------------------------------------
# Criterion 2 — silence: flat slice, level at the noise floor, no wiggle
# ---------------------------------------------------------------------------


def test_silence_yields_a_flat_envelope_every_bucket():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        assert frame["env_min"] == [0] * len(frame["env_min"])
        assert frame["env_max"] == [0] * len(frame["env_max"])


def test_silence_level_sits_at_the_noise_floor():
    """The criterion says the level sits AT the noise floor, not merely
    below some conservative ceiling — so this asserts equality with the
    module's own FLOOR_DB constant, not a loose ``<=`` bound that would also
    pass for a level nowhere near the real floor."""
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        assert frame["level_db"] == FLOOR_DB
        assert frame["noise_floor_db"] == FLOOR_DB


def test_silence_produces_no_invented_zero_crossings():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        assert frame["zero_crossing_hz"] is None


def test_silence_envelope_is_identical_across_blocks_no_dithering():
    """A flat envelope must stay flat block to block — never a wiggle
    invented by smoothing/dithering."""
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    mins = {tuple(f["env_min"]) for f in frames}
    maxs = {tuple(f["env_max"]) for f in frames}
    assert mins == {(0,) * len(frames[0]["env_min"])}
    assert maxs == {(0,) * len(frames[0]["env_max"])}


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
    """White-noise-like content is the worst case for the envelope — every
    bucket's min/max can differ from its neighbours."""
    n_samples = SAMPLE_RATE_HZ
    samples = [_RNG.randint(-32768, 32767) for _ in range(n_samples)]
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


def test_envelope_lengths_are_stable_and_bucketed():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)

    for frame in frames:
        assert len(frame["env_min"]) == ENVELOPE_POINTS
        assert len(frame["env_max"]) == ENVELOPE_POINTS
        assert ENVELOPE_POINTS < BLOCK_SAMPLES


def test_one_second_tone_yields_expected_block_count():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)
    expected_blocks = SAMPLE_RATE_HZ // BLOCK_SAMPLES
    assert len(frames) == expected_blocks


def test_waveform_field_is_gone():
    """The superseded fixed-stride field must not linger alongside the
    envelope — nothing consumes it, and keeping both would double the
    payload for no reason."""
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)
    for frame in frames:
        assert "waveform" not in frame
