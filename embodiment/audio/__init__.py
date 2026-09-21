"""Audio feature extraction for the realtime voice daemon (Gwen).

Turns raw pcm16 mono, 24000 Hz, little-endian audio into a compact,
JSON-serialisable feature stream a browser can draw as a live oscilloscope
waveform, for audio the browser never receives. Pure, no IO, no threads, no
clock — see :mod:`embodiment.audio.features`.
"""

from __future__ import annotations

from embodiment.audio.features import (
    BLOCK_SAMPLES,
    ENVELOPE_POINTS,
    FLOOR_DB,
    FLOOR_RISE_DB_PER_SEC,
    SAMPLE_RATE_HZ,
    FeatureExtractor,
    decode_envelope,
    extract_features,
)

__all__ = [
    "extract_features",
    "FeatureExtractor",
    "decode_envelope",
    "SAMPLE_RATE_HZ",
    "BLOCK_SAMPLES",
    "ENVELOPE_POINTS",
    "FLOOR_DB",
    "FLOOR_RISE_DB_PER_SEC",
]
