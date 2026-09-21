"""Audio feature extraction: raw pcm16 -> a compact oscilloscope feature stream.

Contract (plan task ``t8``, spec target ``h21``): turn raw audio — pcm16 mono,
24000 Hz, little-endian bytes — into a per-block feature stream a browser can
draw as a live oscilloscope waveform, for audio the browser never receives.
The trace must be REAL audio, never a decorative animation, and the SHAPE must
be honest too: a fixed-stride decimated slice was tried first and rejected —
16 samples per 33 ms block at a stride of 50 samples effectively resamples at
480 Hz with no low-pass ahead of it, so anything above the 240 Hz Nyquist
limit that implies (most speech energy, and the module's own 440 Hz test tone)
aliases into a slow, wrong-frequency wiggle. The values were real samples, but
the shape they drew was not the waveform.

What ships instead is a **min/max envelope**: each 800-sample block splits
into :data:`ENVELOPE_POINTS` contiguous, non-overlapping buckets of
:data:`_ENVELOPE_BUCKET_SAMPLES` samples each, and every bucket contributes
its own minimum and maximum sample — both real values that occurred in the
audio, never an average, a smoothed value, or an interpolated one. This is how
waveform displays are conventionally drawn (a bucket's min/max span is exactly
what a scope trace shows for a bucket too narrow to plot every sample) and it
is alias-free: a bucket's extremes are frequency-independent, so a full-scale
tone reports the same amplitude whether it is 440 Hz or 3 kHz, and a single
loud transient shows up in whichever bucket it actually falls in rather than
being missed by a stride that happens to skip over it.

Pure and stdlib-only. No IO, no threads, no clock. Every function is a plain
transform from bytes (or lists of numbers) to plain data; nothing here reads
a file, opens a socket, sleeps, or touches wall-clock time. numpy is an
approved runtime import elsewhere in this package (attributed to
``embodiment.continuity`` in ``tests/test_zero_deps.py``), but this module
does not need it — block-sized (~33 ms) reductions over plain Python lists are
cheap enough, and staying off numpy here avoids any risk of shifting that
module's already-measured cost onto this one.

Output shape is plain, JSON-serialisable data (ints/floats/lists/None) because
it is published as events: ``extract_features`` returns a ``list[dict]``, one
dict per ~33 ms block, each carrying:

- ``level_db``: RMS level in dBFS (relative to full-scale int16), clamped to
  :data:`FLOOR_DB`.
- ``noise_floor_db``: a causal running minimum of ``level_db`` over the blocks
  seen so far in this call — never above the level it is a floor for.
- ``zero_crossing_hz``: an interpolated zero-crossing-rate pitch estimate for
  the block, or ``None`` when the block carries too little energy to support
  one (this is how "optional" is honoured: absent, not invented).
- ``env_min`` / ``env_max``: two parallel, fixed-length lists (one entry per
  bucket) of the bucket's real minimum and maximum sample, quantized to a
  small int range. Shipped as two parallel lists rather than a list of
  ``[min, max]`` pairs because it measured smaller on the wire — seven pairs'
  worth of extra ``[``/``]``/``,`` punctuation per block adds up across a
  second of blocks; see the module's tests for the measured comparison.

Never raises. Malformed input (odd byte length, empty buffer, non-bytes,
garbage) degrades to a well-formed empty or flat result rather than raising.
"""

from __future__ import annotations

import math
import struct

__all__ = [
    "SAMPLE_RATE_HZ",
    "BLOCK_SAMPLES",
    "ENVELOPE_POINTS",
    "FLOOR_DB",
    "extract_features",
]

#: The contracted input rate: pcm16 mono, 24000 Hz, little-endian.
SAMPLE_RATE_HZ = 24000

#: ~33 ms per block at 24 kHz (800 / 24000 = 33.333... ms), chosen so a whole
#: second divides evenly into 30 blocks with no remainder to reason about.
BLOCK_SAMPLES = 800

#: How many contiguous min/max buckets each block's envelope carries.
#: BLOCK_SAMPLES / ENVELOPE_POINTS = 50, an exact bucket size with no
#: remainder.
ENVELOPE_POINTS = 16

#: Samples per envelope bucket. Each bucket's min and max are both real
#: samples that occurred somewhere inside it — never averaged, smoothed, or
#: interpolated — so the envelope is alias-free regardless of the input
#: frequency (see the module docstring).
_ENVELOPE_BUCKET_SAMPLES = BLOCK_SAMPLES // ENVELOPE_POINTS

#: dBFS floor: silence (RMS == 0) can't take a real logarithm, and a very
#: quiet block would otherwise report an unbounded negative number. -96 dB is
#: the conventional 16-bit noise floor and is what "at the noise floor" means
#: for criterion 2 below.
FLOOR_DB = -96.0

_INT16_MAX = 32767
_INT16_MIN = -32768

#: Quantized envelope sample range published in ``env_min`` / ``env_max`` —
#: small enough to keep the JSON payload compact (criterion 3). The scale
#: below (divide by 256, i.e. an 8-bit-per-sample reduction) is chosen so
#: full-scale negative input (-32768) maps to exactly -128 — the bottom of
#: this range is reachable, not just approached — while full-scale positive
#: input (32767) overshoots to 128 and needs the clamp below.
_QUANT_MAX = 127
_QUANT_MIN = -128
_QUANT_SCALE = 256.0


def extract_features(pcm_bytes: object) -> list[dict[str, object]]:
    """Turn raw pcm16 mono 24 kHz little-endian bytes into a feature stream.

    Returns one plain dict per ~33 ms block. A trailing partial block (fewer
    than :data:`BLOCK_SAMPLES` samples) is dropped rather than padded or
    guessed at, so nothing published is invented. Never raises: malformed
    input (odd byte length, empty buffer, non-bytes, or a buffer shorter than
    one block) degrades to an empty list.
    """
    if not isinstance(pcm_bytes, (bytes, bytearray)):
        return []
    samples = _decode_int16_le(bytes(pcm_bytes))
    if len(samples) < BLOCK_SAMPLES:
        return []

    frames: list[dict[str, object]] = []
    running_floor_db: float | None = None
    usable = len(samples) - (len(samples) % BLOCK_SAMPLES)
    for start in range(0, usable, BLOCK_SAMPLES):
        block = samples[start : start + BLOCK_SAMPLES]
        level_db = _level_db(block)
        if running_floor_db is None:
            running_floor_db = level_db
        else:
            running_floor_db = min(running_floor_db, level_db)
        env_min, env_max = _envelope(block)
        frames.append(
            {
                # Rounded to the precision the payload budget (criterion 3)
                # can afford, not the precision the arithmetic could give: a
                # whole dB is far inside the 1 dB tolerance criterion 1 holds
                # this to (worst case adds 0.5 dB of rounding on top of the
                # measured <0.04 dB arithmetic error), and a whole Hz is far
                # inside its 2% tolerance — see
                # test_440hz_tone_level_within_1db_of_expected and
                # test_440hz_tone_zero_crossing_rate_within_2_percent.
                "level_db": round(level_db),
                "noise_floor_db": round(running_floor_db),
                "zero_crossing_hz": _round_int_or_none(_zero_crossing_hz(block, SAMPLE_RATE_HZ)),
                "env_min": env_min,
                "env_max": env_max,
            }
        )
    return frames


def _decode_int16_le(data: bytes) -> list[int]:
    """Decode little-endian pcm16 bytes into signed sample ints.

    An odd trailing byte (malformed input) is dropped, never padded or
    guessed at.
    """
    n = len(data) // 2
    if n == 0:
        return []
    return list(struct.unpack(f"<{n}h", data[: n * 2]))


def _level_db(block: list[int]) -> float:
    """RMS level in dBFS, clamped at :data:`FLOOR_DB`."""
    if not block:
        return FLOOR_DB
    mean_square = sum(sample * sample for sample in block) / len(block)
    if mean_square <= 0.0:
        return FLOOR_DB
    rms = math.sqrt(mean_square)
    db = 20.0 * math.log10(rms / _INT16_MAX)
    return db if db > FLOOR_DB else FLOOR_DB


def _zero_crossing_hz(block: list[int], sample_rate: int) -> float | None:
    """An interpolated zero-crossing-rate pitch estimate for one block.

    Rather than counting crossings and dividing by the block duration (which
    quantizes to whole crossings and is sensitive to where the waveform's
    phase happens to sit at the block boundary), this locates each crossing's
    fractional sample position by linear interpolation between the two
    samples that bracket it, then derives frequency from the elapsed time
    between the first and last located crossing. That cancels the boundary
    effect and stays accurate for a single ~33 ms block.

    Returns ``None`` when the block carries too little information to
    support an estimate (fewer than two crossings) — silence, most of all.
    """
    crossing_positions: list[float] = []
    for i in range(1, len(block)):
        y0, y1 = block[i - 1], block[i]
        if y1 == 0:
            # Deferred: this sample becomes y0 on the next iteration, where
            # the y0 == 0 branch below records it once. Handling it here too
            # would double-count the same crossing.
            continue
        if y0 == 0:
            crossing_positions.append(float(i - 1))
            continue
        if (y0 < 0) != (y1 < 0):
            frac = y0 / (y0 - y1)
            crossing_positions.append((i - 1) + frac)

    if len(crossing_positions) < 2:
        return None

    span_samples = crossing_positions[-1] - crossing_positions[0]
    if span_samples <= 0.0:
        return None

    half_cycles = len(crossing_positions) - 1
    span_seconds = span_samples / sample_rate
    return half_cycles / (2.0 * span_seconds)


def _envelope(block: list[int]) -> tuple[list[int], list[int]]:
    """A per-bucket (min, max) envelope of the block's own real samples.

    Splits the block into :data:`ENVELOPE_POINTS` contiguous, non-overlapping
    buckets of :data:`_ENVELOPE_BUCKET_SAMPLES` samples and reports each
    bucket's true minimum and maximum, quantized. Alias-free by construction:
    a bucket's extremes do not depend on which samples a stride happens to
    land on, so a full-scale tone reports the same amplitude at any frequency,
    and an isolated transient shows up in whichever bucket contains it rather
    than being skipped over.
    """
    mins: list[int] = []
    maxs: list[int] = []
    for start in range(0, len(block), _ENVELOPE_BUCKET_SAMPLES):
        bucket = block[start : start + _ENVELOPE_BUCKET_SAMPLES]
        if not bucket:
            mins.append(0)
            maxs.append(0)
            continue
        mins.append(_quantize(min(bucket)))
        maxs.append(_quantize(max(bucket)))
    return mins, maxs


def _quantize(sample: int) -> int:
    """Scale a signed int16 sample into the compact published range.

    Dividing by :data:`_QUANT_SCALE` (256) maps the int16 minimum (-32768)
    to exactly -128 — the bottom of the published range is reachable by a
    real sample, not merely approached. The int16 maximum (32767) overshoots
    to 128 and needs the max clamp below; the min clamp can never fire for
    valid int16 input (the smallest possible input already lands exactly on
    -128) but stays as a defensive bound against a scale change later.
    """
    scaled = round(sample / _QUANT_SCALE)
    if scaled > _QUANT_MAX:
        return _QUANT_MAX
    if scaled < _QUANT_MIN:
        return _QUANT_MIN
    return scaled


def _round_int_or_none(value: float | None) -> int | None:
    """Round to the nearest whole Hz, or ``None`` if there was no estimate.

    An integer serialises shorter than a 2-decimal float and a whole Hz of
    resolution is far inside the 2% tolerance this feature is measured
    against.
    """
    return None if value is None else round(value)
