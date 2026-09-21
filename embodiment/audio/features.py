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
being missed by a stride that happens to skip over it. The 32 quantized
envelope values (16 mins, then 16 maxes) are packed as one base64 string in
the ``env`` field rather than two JSON integer lists — see
:func:`decode_envelope` — because a live event bus wraps every published
frame in an envelope of its own, and the two-list form left too little of the
8 kB/s budget (criterion 3) for that wrapping.

**Streaming.** A one-shot ``extract_features(buf)`` call is convenient for
tests and for an already-buffered second of audio, but it is not how a
microphone feeds a daemon: real capture arrives in small chunks (commonly
~20 ms, well under one 800-sample/~33 ms block), and a stateless function that
drops any trailing partial block returns *nothing* for chunk sizes below one
block. :class:`FeatureExtractor` is the streaming form: it buffers raw bytes
(down to a single odd trailing byte, so a sample split across two feed calls
is reassembled rather than corrupted or dropped) and emits a frame for every
complete block accumulated across calls, however the caller chose to chunk
the input. ``extract_features`` is defined in terms of it
(``FeatureExtractor().feed(buf)``) so there is exactly one code path — the
one-shot and streaming forms cannot silently drift apart.

Pure and stdlib-only. No IO, no threads, no clock — including the noise-floor
tracker's "slow rise" below, whose time constant is derived from the number
of samples processed (an audio-domain quantity), never from
``time.monotonic()`` or any other wall-clock read. Every function/method is a
plain transform from bytes (or lists of numbers) to plain data, or a plain
mutation of a small buffer the caller owns by holding the instance; nothing
here reads a file, opens a socket, sleeps, or touches wall-clock time. numpy
is an approved runtime import elsewhere in this package (attributed to
``embodiment.continuity`` in ``tests/test_zero_deps.py``), but this module
does not need it — block-sized (~33 ms) reductions over plain Python lists are
cheap enough, and staying off numpy here avoids any risk of shifting that
module's already-measured cost onto this one.

Output shape is plain, JSON-serialisable data (ints/floats/lists/None/str)
because it is published as events: ``extract_features``/``FeatureExtractor
.feed`` return a ``list[dict]``, one dict per ~33 ms block, each carrying:

- ``level_db``: RMS level in dBFS (relative to full-scale int16), clamped to
  :data:`FLOOR_DB`.
- ``noise_floor_db``: a noise floor tracked *across* calls to the same
  :class:`FeatureExtractor` (or across the blocks of one ``extract_features``
  call) — see :class:`_NoiseFloorTracker`: it drops to a new low
  immediately and climbs back toward the current level slowly, never above
  it, so a single loud block cannot pull it back up and a single quiet block
  is not read as "the room got quiet forever".
- ``zero_crossing_hz``: an interpolated zero-crossing-rate pitch estimate for
  the block, or ``None`` when the block carries too little energy to support
  one (this is how "optional" is honoured: absent, not invented).
- ``env``: a base64 string packing the block's 16-bucket min/max envelope
  (16 signed int8 minimums, then 16 signed int8 maximums) — decode with
  :func:`decode_envelope`, never by hand.

Never raises. Malformed input (odd byte length, empty buffer, non-bytes,
garbage) degrades to a well-formed empty or flat result rather than raising.
"""

from __future__ import annotations

import base64
import math
import struct

__all__ = [
    "SAMPLE_RATE_HZ",
    "BLOCK_SAMPLES",
    "ENVELOPE_POINTS",
    "FLOOR_DB",
    "FLOOR_RISE_DB_PER_SEC",
    "FeatureExtractor",
    "extract_features",
    "decode_envelope",
]

#: The contracted input rate: pcm16 mono, 24000 Hz, little-endian.
SAMPLE_RATE_HZ = 24000

#: ~33 ms per block at 24 kHz (800 / 24000 = 33.333... ms), chosen so a whole
#: second divides evenly into 30 blocks with no remainder to reason about.
BLOCK_SAMPLES = 800

#: Bytes per block (pcm16 = 2 bytes/sample).
_BLOCK_BYTES = BLOCK_SAMPLES * 2

#: Seconds of audio one block represents. Used only to size the noise floor's
#: per-block rise in :data:`FLOOR_RISE_DB_PER_SEC` terms — an audio-domain
#: duration derived from the sample count actually processed, not a
#: wall-clock reading, so the tracker stays clock-free.
_BLOCK_DURATION_S = BLOCK_SAMPLES / SAMPLE_RATE_HZ

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

#: How fast the tracked noise floor is allowed to climb back up once the
#: signal gets loud again, in dB per second. This is a **judgement call**,
#: not a derived or measured number: there is no reference signal or user
#: study behind it. The reasoning: a floor that could climb as fast as it
#: falls would just track the signal (making "noise floor" meaningless — see
#: the immediate-drop-then-slow-rise design), while a floor that never
#: recovers within a session would stay pinned to the single quietest moment
#: ever seen, which stops being a useful "background level" the longer a
#: session runs. 6 dB/s sits between those failure modes: recovering the
#: ~87 dB span from this module's silence floor (-96 dBFS) up to a
#: comfortably loud full-scale-ish tone (~-9 dBFS, this module's own 440 Hz
#: test fixture) takes on the order of 14-15 seconds, which is slow enough
#: that a single loud block cannot masquerade as "the floor rose", and fast
#: enough that a room that genuinely got quieter is reflected within a few
#: blocks of the quiet spell ending. Revisit with a measurement if this ever
#: matters for something more than a display.
FLOOR_RISE_DB_PER_SEC = 6.0

_INT16_MAX = 32767
_INT16_MIN = -32768

#: Quantized envelope sample range packed into the ``env`` field (criterion
#: 3). The scale below (divide by 256, i.e. an 8-bit-per-sample reduction) is
#: chosen so full-scale negative input (-32768) maps to exactly -128 — the
#: bottom of this range is reachable by a real sample, not just approached —
#: while full-scale positive input (32767) overshoots to 128 and needs the
#: clamp below.
_QUANT_MAX = 127
_QUANT_MIN = -128
_QUANT_SCALE = 256.0


class _NoiseFloorTracker:
    """Tracks a slow-rise noise floor across an arbitrary number of blocks.

    Falls immediately to a new, lower level (so a silent spell is reflected
    right away) and climbs back toward the current level at
    :data:`FLOOR_RISE_DB_PER_SEC`, never overshooting it (so a single loud
    block cannot yank the floor up with it). A pure running minimum — the
    single-call behaviour this replaces — never recovers after one quiet
    block for the rest of a long-running stream, which is why this exists.
    """

    __slots__ = ("_floor_db",)

    def __init__(self) -> None:
        self._floor_db: float | None = None

    def reset(self) -> None:
        self._floor_db = None

    def update(self, level_db: float) -> float:
        if self._floor_db is None or level_db < self._floor_db:
            self._floor_db = level_db
        else:
            max_rise = FLOOR_RISE_DB_PER_SEC * _BLOCK_DURATION_S
            self._floor_db = min(level_db, self._floor_db + max_rise)
        return self._floor_db


class FeatureExtractor:
    """Stateful, streaming feature extraction across arbitrarily chunked feed calls.

    ``feed(pcm_bytes)`` buffers raw bytes — including a lone odd trailing
    byte, so a sample split across two chunks is reassembled rather than
    dropped — and returns a frame for every complete
    :data:`BLOCK_SAMPLES`-sample block accumulated so far, across this and
    all prior ``feed`` calls. A trailing partial block stays buffered,
    waiting for more bytes; it is never padded or guessed at. ``flush()``
    discards that trailing tail without emitting anything for it — for a
    caller that knows no more audio is coming and wants the buffer cleared
    without inventing a frame from an incomplete block. ``reset()`` clears
    both the byte buffer and the tracked noise floor, for starting over.

    Never raises: a ``feed`` call with non-bytes input returns ``[]`` and
    leaves all state untouched.
    """

    __slots__ = ("_buffer", "_floor")

    def __init__(self) -> None:
        self._buffer: bytes = b""
        self._floor = _NoiseFloorTracker()

    def feed(self, pcm_bytes: object) -> list[dict[str, object]]:
        if not isinstance(pcm_bytes, (bytes, bytearray)):
            return []
        self._buffer += bytes(pcm_bytes)

        frames: list[dict[str, object]] = []
        while len(self._buffer) >= _BLOCK_BYTES:
            chunk, self._buffer = self._buffer[:_BLOCK_BYTES], self._buffer[_BLOCK_BYTES:]
            block = _decode_int16_le(chunk)
            frames.append(self._process_block(block))
        return frames

    def flush(self) -> None:
        self._buffer = b""

    def reset(self) -> None:
        self._buffer = b""
        self._floor.reset()

    def _process_block(self, block: list[int]) -> dict[str, object]:
        level_db = _level_db(block)
        floor_db = self._floor.update(level_db)
        env_min, env_max = _envelope(block)
        return {
            "level_db": round(level_db, 2),
            "noise_floor_db": round(floor_db, 2),
            "zero_crossing_hz": _round_or_none(_zero_crossing_hz(block, SAMPLE_RATE_HZ), 1),
            "env": _encode_envelope(env_min, env_max),
        }


def extract_features(pcm_bytes: object) -> list[dict[str, object]]:
    """One-shot convenience: feed a whole buffer through a fresh extractor.

    Returns one plain dict per ~33 ms block. A trailing partial block (fewer
    than :data:`BLOCK_SAMPLES` samples) is dropped rather than padded or
    guessed at, so nothing published is invented. Never raises: malformed
    input (odd byte length, empty buffer, non-bytes, or a buffer shorter than
    one block) degrades to an empty list.

    Defined in terms of :class:`FeatureExtractor` so the one-shot and
    streaming paths cannot drift apart — this is the only place either does
    the actual block-splitting and feature work.
    """
    return FeatureExtractor().feed(pcm_bytes)


def decode_envelope(frame: dict[str, object]) -> tuple[list[int], list[int]]:
    """Decode a frame's packed ``env`` field back into (mins, maxes).

    The published wire format for consumers: Python callers (including this
    module's own tests) should use this rather than hand-rolling the base64
    + struct unpack, so the packing in :func:`_encode_envelope` has exactly
    one place that has to agree with it.
    """
    raw = base64.b64decode(frame["env"])
    values = list(struct.unpack(f"{len(raw)}b", raw))
    half = len(values) // 2
    return values[:half], values[half:]


def _encode_envelope(mins: list[int], maxs: list[int]) -> str:
    """Pack quantized envelope mins then maxes as one base64 string.

    32 signed bytes (16 mins, 16 maxes) base64-encode to a fixed 44
    characters — shorter on the wire than two JSON integer lists, and a
    browser decodes it with a single ``atob`` call into an ``Int8Array``.
    """
    raw = struct.pack(f"{len(mins)}b{len(maxs)}b", *mins, *maxs)
    return base64.b64encode(raw).decode("ascii")


def _decode_int16_le(data: bytes) -> list[int]:
    """Decode little-endian pcm16 bytes into signed sample ints.

    Callers only ever pass exactly :data:`_BLOCK_BYTES` bytes (an even
    count), since :class:`FeatureExtractor` buffers everything shorter.
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


def _round_or_none(value: float | None, ndigits: int) -> float | None:
    return None if value is None else round(value, ndigits)
