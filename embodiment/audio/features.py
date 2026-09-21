"""Audio feature extraction: raw pcm16 -> a compact oscilloscope feature stream.

Contract (plan task ``t8``, spec target ``h21``): turn raw audio — pcm16 mono,
24000 Hz, little-endian bytes — into a per-block feature stream a browser can
draw as a live oscilloscope waveform, for audio the browser never receives.
The trace must be REAL audio, never a decorative animation: the waveform slice
is a decimation of actual samples (no dithering, smoothing, or interpolation
that could invent motion), and silence yields a flat slice, never a synthetic
wiggle.

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
- ``waveform``: a fixed-length, decimated slice of the block's own samples,
  quantized to a small int range — real samples, picked by stride, never
  averaged, smoothed, or interpolated.

Never raises. Malformed input (odd byte length, empty buffer, non-bytes,
garbage) degrades to a well-formed empty or flat result rather than raising.
"""

from __future__ import annotations

import math
import struct

__all__ = [
    "SAMPLE_RATE_HZ",
    "BLOCK_SAMPLES",
    "WAVE_POINTS",
    "FLOOR_DB",
    "extract_features",
]

#: The contracted input rate: pcm16 mono, 24000 Hz, little-endian.
SAMPLE_RATE_HZ = 24000

#: ~33 ms per block at 24 kHz (800 / 24000 = 33.333... ms), chosen so a whole
#: second divides evenly into 30 blocks with no remainder to reason about.
BLOCK_SAMPLES = 800

#: How many real samples each block's decimated waveform slice carries.
#: BLOCK_SAMPLES / WAVE_POINTS = 50, an exact stride with no remainder.
WAVE_POINTS = 16

#: The stride used to decimate a block's raw samples into WAVE_POINTS real
#: samples. Picked, never averaged or interpolated.
_WAVE_STRIDE = BLOCK_SAMPLES // WAVE_POINTS

#: dBFS floor: silence (RMS == 0) can't take a real logarithm, and a very
#: quiet block would otherwise report an unbounded negative number. -96 dB is
#: the conventional 16-bit noise floor and is what "at the noise floor" means
#: for criterion 2 below.
FLOOR_DB = -96.0

_INT16_MAX = 32767

#: Quantized waveform sample range published in the ``waveform`` field —
#: small enough to keep the JSON payload compact (criterion 3).
_QUANT_MAX = 127
_QUANT_MIN = -128


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
        running_floor_db = level_db if running_floor_db is None else min(running_floor_db, level_db)
        frames.append(
            {
                "level_db": round(level_db, 2),
                "noise_floor_db": round(running_floor_db, 2),
                "zero_crossing_hz": _round_or_none(_zero_crossing_hz(block, SAMPLE_RATE_HZ), 2),
                "waveform": _decimate(block),
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


def _decimate(block: list[int]) -> list[int]:
    """A fixed-length slice of the block's own real samples, quantized.

    Picks WAVE_POINTS real samples by stride — no averaging, smoothing, or
    interpolation, so a flat (silent) block decimates to an exactly flat
    slice and nothing here can invent motion that was not in the audio.
    """
    picked = block[::_WAVE_STRIDE][:WAVE_POINTS]
    return [_quantize(sample) for sample in picked]


def _quantize(sample: int) -> int:
    """Scale a signed int16 sample into the compact published range."""
    scaled = round(sample / 32768.0 * _QUANT_MAX)
    if scaled > _QUANT_MAX:
        return _QUANT_MAX
    if scaled < _QUANT_MIN:
        return _QUANT_MIN
    return scaled


def _round_or_none(value: float | None, ndigits: int) -> float | None:
    return None if value is None else round(value, ndigits)
