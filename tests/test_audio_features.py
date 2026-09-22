"""Tests for :mod:`embodiment.audio.features` — proves plan task ``t8``'s
acceptance criteria (spec target ``h21``):

1. a 440 Hz test tone yields a slice whose zero-crossing rate matches 440 Hz
   within 2%, and a level within 1 dB of expected.
2. silence yields a flat slice and a level at the noise floor, never a
   synthetic wiggle.
3. one second of audio produces under 8 kB of feature payload.

Design history, so a future reader does not reintroduce a fixed defect:

- The waveform field started as a fixed-stride decimated slice (16 samples
  per 33 ms block, stride 50) but that aliases: it effectively resamples at
  480 Hz with no low-pass ahead of it, so anything above the 240 Hz Nyquist
  limit that implies — including the module's own 440 Hz test tone — draws
  as a slow, wrong-frequency wiggle rather than the real waveform shape. It
  was replaced with a per-bucket min/max envelope, which is alias-free
  because a bucket's extremes do not depend on which samples a stride
  happens to land on. ``test_envelope_amplitude_is_frequency_independent``
  and ``test_strided_slice_would_have_missed_an_off_stride_spike`` hold that
  property to account.
- The envelope was originally shipped as two JSON integer lists
  (``env_min``/``env_max``). It is now one base64 string (``env``, decoded
  with :func:`decode_envelope`) because the two-list form left too little of
  the 8 kB/s budget for an event bus's own per-frame wrapping.
  ``test_worst_case_payload_stays_under_75_percent_of_budget`` is the canary
  that should fail *before* the hard 8 kB criterion does, the next time a
  field is added.
- ``extract_features`` was a pure, stateless function that dropped any
  trailing partial block. A real microphone feeds a daemon in small chunks
  (commonly ~20 ms, under one ~33 ms block), so every call returned nothing
  at all — 0 of 30 frames for 20 ms and 30 ms chunks, 20 of 30 for 50 ms
  chunks. :class:`FeatureExtractor` fixes this by buffering across calls.
  ``test_streaming_yields_all_30_frames_regardless_of_chunk_size`` pins the
  exact regression that was measured.
"""

from __future__ import annotations

import json
import math
import random
import struct

import pytest

from embodiment.audio import features as _features
from embodiment.audio.features import (
    BLOCK_SAMPLES,
    ENVELOPE_POINTS,
    FLOOR_DB,
    SAMPLE_RATE_HZ,
    FeatureExtractor,
    decode_envelope,
    extract_features,
)

_INT16_MAX = 32767
_BUCKET_SAMPLES = BLOCK_SAMPLES // ENVELOPE_POINTS
_PAYLOAD_HARD_LIMIT = 8 * 1024
_PAYLOAD_BUDGET_CEILING = round(_PAYLOAD_HARD_LIMIT * 0.75)  # 6144 bytes

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
    """One quantization step in the published envelope range."""
    return 256.0  # matches features._QUANT_SCALE; see that module's docstring


def _feed_in_fixed_chunks(pcm: bytes, chunk_bytes: int) -> list[dict[str, object]]:
    extractor = FeatureExtractor()
    frames: list[dict[str, object]] = []
    for i in range(0, len(pcm), chunk_bytes):
        frames.extend(extractor.feed(pcm[i : i + chunk_bytes]))
    return frames


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
            env_min, env_max = decode_envelope(frame)
            for lo, hi in zip(env_min, env_max):
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
    env_min, env_max = decode_envelope(frames[0])

    spike_bucket = spike_index // _BUCKET_SAMPLES
    for i, (lo, hi) in enumerate(zip(env_min, env_max)):
        if i == spike_bucket:
            assert hi > 0, "the bucket containing the spike must show it"
        else:
            assert lo == 0, f"bucket {i} should be silent, got ({lo}, {hi})"
            assert hi == 0, f"bucket {i} should be silent, got ({lo}, {hi})"


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
    _, env_max = decode_envelope(extract_features(block_pcm)[0])
    assert max(env_max) > 0


# ---------------------------------------------------------------------------
# The envelope's wire format: one base64 field, decoded via decode_envelope
# ---------------------------------------------------------------------------


def test_env_field_is_a_44_char_base64_string():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)
    for frame in frames:
        assert isinstance(frame["env"], str)
        assert len(frame["env"]) == 44


def test_decode_envelope_round_trip_including_extremes():
    """The whole quantized range, including both -128 and 127, survives an
    encode/decode round trip."""
    mins = [-128] * ENVELOPE_POINTS
    maxs = [127] * ENVELOPE_POINTS
    encoded = _features._encode_envelope(mins, maxs)

    decoded_min, decoded_max = decode_envelope({"env": encoded})
    assert decoded_min == mins
    assert decoded_max == maxs


def test_decode_envelope_round_trip_through_the_real_pipeline():
    """A block alternating full-scale negative/positive samples must
    decode_envelope back to -128 in every min slot and 127 in every max
    slot — every bucket (50 samples) contains both extremes."""
    samples = [-32768, 32767] * (BLOCK_SAMPLES // 2)
    block_pcm = struct.pack(f"<{BLOCK_SAMPLES}h", *samples)

    env_min, env_max = decode_envelope(extract_features(block_pcm)[0])
    assert env_min == [-128] * ENVELOPE_POINTS
    assert env_max == [127] * ENVELOPE_POINTS


# ---------------------------------------------------------------------------
# Criterion 2 — silence: flat slice, level at the noise floor, no wiggle
# ---------------------------------------------------------------------------


def test_silence_yields_a_flat_envelope_every_bucket():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    assert frames
    for frame in frames:
        env_min, env_max = decode_envelope(frame)
        assert env_min == [0] * len(env_min)
        assert env_max == [0] * len(env_max)


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

    mins = {tuple(decode_envelope(f)[0]) for f in frames}
    maxs = {tuple(decode_envelope(f)[1]) for f in frames}
    assert mins == {(0,) * ENVELOPE_POINTS}
    assert maxs == {(0,) * ENVELOPE_POINTS}


# ---------------------------------------------------------------------------
# The noise floor tracker: falls immediately, rises slowly and monotonically
# ---------------------------------------------------------------------------


def test_floor_falls_immediately_on_silence():
    loud = _tone_pcm(440.0, duration_s=0.2, amplitude=16000.0)
    silence = _silence_pcm(duration_s=0.2)

    extractor = FeatureExtractor()
    loud_frames = extractor.feed(loud)
    silent_frames = extractor.feed(silence)

    assert loud_frames
    assert silent_frames
    # The floor is at (or very near) the loud level by the end of the loud
    # run, then drops to exactly FLOOR_DB on the very first silent block.
    assert silent_frames[0]["noise_floor_db"] == FLOOR_DB


def test_floor_rises_slowly_and_monotonically_after_recovering_from_silence():
    amplitude = 16000.0
    loud = _tone_pcm(440.0, duration_s=0.2, amplitude=amplitude)
    silence = _silence_pcm(duration_s=0.2)
    loud_again = _tone_pcm(440.0, duration_s=1.0, amplitude=amplitude)

    extractor = FeatureExtractor()
    extractor.feed(loud)
    extractor.feed(silence)
    recovery_frames = extractor.feed(loud_again)

    assert len(recovery_frames) >= 2
    floors = [f["noise_floor_db"] for f in recovery_frames]
    level = recovery_frames[0]["level_db"]

    # Starts near the silence floor (one block's worth of rise may already
    # have applied to the very first recovery block) and climbs from there.
    one_block_rise = _features.FLOOR_RISE_DB_PER_SEC * _features._BLOCK_DURATION_S
    assert floors[0] <= FLOOR_DB + one_block_rise + 0.01
    assert floors[-1] > floors[0]

    # Monotonically non-decreasing, and never overshoots the signal's own
    # level (the floor chases the level, it never exceeds it).
    for prev, cur in zip(floors, floors[1:]):
        assert cur >= prev
    for f in floors:
        assert f <= level + 0.01


def test_floor_stays_pinned_at_floor_db_through_continuous_silence():
    pcm = _silence_pcm(duration_s=1.0)
    extractor = FeatureExtractor()
    frames = extractor.feed(pcm)

    assert frames
    for frame in frames:
        assert frame["noise_floor_db"] == FLOOR_DB


# ---------------------------------------------------------------------------
# Streaming: FeatureExtractor buffers across chunked feed() calls
# ---------------------------------------------------------------------------


def test_streaming_yields_all_30_frames_regardless_of_chunk_size():
    """Pins the exact regression reported against a daemon-like feed: 1s of
    a 440 Hz tone fed in 20ms/30ms/50ms/100ms chunks used to yield 0, 0, 20
    and 30 of 30 frames respectively. It must now yield 30 of 30 in every
    case."""
    pcm = _tone_pcm(440.0, duration_s=1.0)
    for ms in (20, 30, 50, 100):
        chunk_bytes = SAMPLE_RATE_HZ * ms // 1000 * 2
        extractor = FeatureExtractor()
        total = sum(
            len(extractor.feed(pcm[i : i + chunk_bytes])) for i in range(0, len(pcm), chunk_bytes)
        )
        assert total == 30, f"{ms}ms chunks produced {total} of 30 frames"


@pytest.mark.parametrize("chunk_bytes", [1, 3, 960, 1440, 2400, 4800])
def test_chunked_feeding_matches_one_shot_for_fixed_chunk_sizes(chunk_bytes):
    """960/1440/2400/4800 bytes are exactly 20ms/30ms/50ms/100ms of pcm16 at
    24kHz. The concatenated frames from chunked feeding must be identical to
    the one-shot result on the same buffer."""
    pcm = _tone_pcm(440.0, duration_s=1.0)
    expected = extract_features(pcm)

    got = _feed_in_fixed_chunks(pcm, chunk_bytes)
    assert got == expected


def test_chunked_feeding_matches_one_shot_for_random_sized_chunks():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    expected = extract_features(pcm)

    rng = random.Random(99)
    extractor = FeatureExtractor()
    got: list[dict[str, object]] = []
    i = 0
    while i < len(pcm):
        size = rng.randint(1, 4000)
        got.extend(extractor.feed(pcm[i : i + size]))
        i += size

    assert got == expected


def test_odd_trailing_byte_is_reassembled_not_dropped():
    """A sample split across two feed() calls must be reassembled: feeding
    one byte at a time must reach the same frame count as feeding the whole
    buffer at once."""
    pcm = _tone_pcm(440.0, duration_s=1.0)
    expected = extract_features(pcm)

    got = _feed_in_fixed_chunks(pcm, 1)
    assert got == expected


def test_flush_discards_incomplete_tail_without_emitting():
    extractor = FeatureExtractor()
    partial = _tone_pcm(440.0, duration_s=0.01)  # well under one block
    frames = extractor.feed(partial)
    assert frames == []

    extractor.flush()
    assert extractor.feed(b"") == []

    # The flushed tail is gone: feeding a full block afterwards must not
    # somehow combine with what was flushed.
    one_block = _tone_pcm(440.0, duration_s=BLOCK_SAMPLES / SAMPLE_RATE_HZ)
    frames = extractor.feed(one_block)
    assert len(frames) == 1


def test_reset_clears_buffer_and_floor():
    extractor = FeatureExtractor()
    extractor.feed(_tone_pcm(440.0, duration_s=1.0))
    extractor.reset()

    silence = _silence_pcm(duration_s=1.0)
    frames = extractor.feed(silence)
    assert frames
    # After reset, the floor starts fresh rather than remembering the prior
    # loud level — the very first block's floor is its own level (FLOOR_DB
    # for silence), not something inherited from before the reset.
    assert frames[0]["noise_floor_db"] == FLOOR_DB


def test_extract_features_is_one_code_path_via_feature_extractor():
    """extract_features must not duplicate FeatureExtractor's block logic —
    it is defined in terms of it, so the two paths cannot silently drift."""
    pcm = _tone_pcm(440.0, duration_s=1.0)
    assert extract_features(pcm) == FeatureExtractor().feed(pcm)


# ---------------------------------------------------------------------------
# Criterion 3 — payload size budget
# ---------------------------------------------------------------------------


def test_one_second_of_audio_payload_under_8kb():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)

    payload = json.dumps(frames)
    assert len(payload) < _PAYLOAD_HARD_LIMIT, f"payload is {len(payload)} bytes"


def test_one_second_of_silence_payload_under_8kb():
    pcm = _silence_pcm(duration_s=1.0)
    frames = extract_features(pcm)

    payload = json.dumps(frames)
    assert len(payload) < _PAYLOAD_HARD_LIMIT


def test_one_second_of_noise_payload_under_8kb():
    """White-noise-like content is the worst case for the envelope — every
    bucket's min/max can differ from its neighbours."""
    n_samples = SAMPLE_RATE_HZ
    samples = [_RNG.randint(-32768, 32767) for _ in range(n_samples)]
    pcm = struct.pack(f"<{n_samples}h", *samples)

    payload = json.dumps(extract_features(pcm))
    assert len(payload) < _PAYLOAD_HARD_LIMIT, f"payload is {len(payload)} bytes"


def test_worst_case_payload_stays_under_75_percent_of_budget():
    """A canary well ahead of the hard 8 kB criterion: the next field added
    to a frame should fail THIS test before it breaks criterion 3 outright."""
    tone_pcm = _tone_pcm(440.0, duration_s=1.0)

    rng = random.Random(4321)
    noise_samples = [rng.randint(-32768, 32767) for _ in range(SAMPLE_RATE_HZ)]
    noise_pcm = struct.pack(f"<{SAMPLE_RATE_HZ}h", *noise_samples)

    silence_pcm = _silence_pcm(duration_s=1.0)

    for label, pcm in (("tone", tone_pcm), ("silence", silence_pcm), ("noise", noise_pcm)):
        payload_len = len(json.dumps(extract_features(pcm)))
        assert payload_len < _PAYLOAD_BUDGET_CEILING, (
            f"{label} payload is {payload_len} bytes, at or above 75% of the "
            f"{_PAYLOAD_HARD_LIMIT}-byte budget ({_PAYLOAD_BUDGET_CEILING} bytes)"
        )


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
    """Arbitrary bytes are still valid PCM: 2560 bytes is one full block and a tail."""
    frames = extract_features(bytes(range(256)) * 10)
    assert len(frames) == 1
    mins, maxes = decode_envelope(frames[0])
    assert len(mins) == len(maxes) == 16
    assert all(lo <= hi for lo, hi in zip(mins, maxes))


@pytest.mark.parametrize("bad", [None, 12345, "not bytes", [1, 2, 3]])
def test_non_bytes_input_does_not_raise(bad):
    frames = extract_features(bad)
    assert frames == []


@pytest.mark.parametrize("bad", [None, 12345, "not bytes", [1, 2, 3]])
def test_feature_extractor_feed_non_bytes_input_does_not_raise_or_corrupt_state(bad):
    partial = _tone_pcm(440.0, duration_s=0.01)  # well under one block
    extractor = FeatureExtractor()
    extractor.feed(partial)
    assert extractor.feed(bad) == []

    # State is untouched: the buffered partial block still completes normally.
    remaining_bytes = BLOCK_SAMPLES * 2 - len(partial)
    rest = _tone_pcm(440.0, duration_s=1.0)[len(partial) : len(partial) + remaining_bytes]
    frames = extractor.feed(rest)
    assert frames  # the buffered partial block eventually completed


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
        env_min, env_max = decode_envelope(frame)
        assert len(env_min) == ENVELOPE_POINTS
        assert len(env_max) == ENVELOPE_POINTS
        assert ENVELOPE_POINTS < BLOCK_SAMPLES


def test_one_second_tone_yields_expected_block_count():
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)
    expected_blocks = SAMPLE_RATE_HZ // BLOCK_SAMPLES
    assert len(frames) == expected_blocks


def test_waveform_and_env_min_max_fields_are_gone():
    """The superseded fields must not linger alongside ``env`` — nothing
    consumes them, and keeping them would inflate the payload for no
    reason."""
    pcm = _tone_pcm(440.0, duration_s=1.0)
    frames = extract_features(pcm)
    for frame in frames:
        assert "waveform" not in frame
        assert "env_min" not in frame
        assert "env_max" not in frame
        assert "env" in frame
