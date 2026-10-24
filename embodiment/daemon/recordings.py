"""The recording store: page clips and room utterances, kept as they arrived (d10, d11).

Operator, 2026-10-24: "we also save the recording as well for now" -- after "a
recording is truth (that it happened, not its content)" -- and then "room
microphone - confirmed. We keep that as well as if sent as recording." Until then
raw audio was never written to disk; this module is now the ONE place that writes
it: page clips (recorded on the page's mic, or uploaded) and each utterance the
room ear hears, cut by :class:`UtteranceTap` (the silence between utterances is
never written). Archiving to S3 is #99.

Layout, under ``<state dir>/recordings/`` (0700): ``<sha256>.<ext>`` holds the
bytes exactly as received and ``<sha256>.json`` what is known about them (bytes,
content type, how it arrived, when, its duration if the probe read one). Both are
0600 and written by atomic replace. The name is the content's sha256, so the same
file sent twice is kept once. A session-log turn links its recording by sha256.

Bounded and never raising: the store holds at most ``max_bytes`` in total. A clip
that would pass the cap is REFUSED and recorded (``recording-store-full``) -- never
an older recording deleted to make room, because a recording is the record that
something happened -- and the turn still runs. A write that fails is recorded
(``recording-write-failed``) and the turn still runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import string
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from embodiment.daemon import history

RECORDINGS_DIRNAME = "recordings"
#: The store's total cap. A judgement, not a measurement: about 2 GiB holds some
#: hundreds of hours of opus speech; the page caps one clip at 60 s.
DEFAULT_MAX_BYTES = 2 * 1024**3

RECORDING_STORE_FULL = "recording-store-full"
RECORDING_WRITE_FAILED = "recording-write-failed"

_EXT_CHARS = frozenset(string.ascii_lowercase + string.digits)


def _extension(value: Any) -> str:
    ext = str(value or "").lower().lstrip(".")
    return ext if ext and len(ext) <= 8 and set(ext) <= _EXT_CHARS else "bin"


def _write_private(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".rec-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


class RecordingStore:
    """Keeps clips under *root*. Never raises; counts what it did."""

    def __init__(
        self,
        root: Any,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        on_degrade: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._max_bytes = max(0, int(max_bytes))
        self._on_degrade = on_degrade
        self._clock = clock
        self._lock = threading.Lock()
        self._bytes: Optional[int] = None  # summed on first use, not at construction
        self.saved = 0
        self.duplicates = 0
        self.refused_full = 0
        self.write_failures = 0
        self.sink_failures = 0

    def _degrade(self, code: str, reason: str) -> None:
        if self._on_degrade is None:
            return
        try:
            self._on_degrade(code, reason)
        except Exception:  # noqa: BLE001  # the sink is a seam; the store still answers
            self.sink_failures += 1  # counted, and shown in status()

    def _total(self) -> int:
        if self._bytes is None:
            try:
                self._bytes = sum(
                    p.stat().st_size
                    for p in self._root.glob("*")
                    if p.is_file() and p.suffix != ".json"
                )
            except OSError:
                self._bytes = 0
        return self._bytes

    def save(
        self,
        data: bytes,
        *,
        extension: Any,
        content_type: Any,
        via: str,
        duration_s: Optional[float] = None,
        timestamps: Optional[list] = None,
    ) -> Optional[dict[str, Any]]:
        """Keep *data*; return the reference a turn links to, or None if not kept.

        *timestamps* are the typed times known about the audio (d12): when it was
        recorded or witnessed, and by whose clock; the store adds ``received``.
        """
        if not isinstance(data, (bytes, bytearray)) or not data:
            return None
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        ref = {
            "sha256": digest,
            "bytes": len(data),
            "content_type": str(content_type or "application/octet-stream")[:100],
            "file": f"{digest}.{_extension(extension)}",
        }
        with self._lock:
            return self._save_locked(data, ref, via, duration_s, list(timestamps or ()))

    def _save_locked(
        self,
        data: bytes,
        ref: dict[str, Any],
        via: str,
        duration_s: Optional[float],
        timestamps: list,
    ) -> Optional[dict[str, Any]]:
        audio = self._root / ref["file"]
        if audio.is_file():
            self.duplicates += 1
            return ref
        if self._total() + len(data) > self._max_bytes:
            self.refused_full += 1
            self._degrade(RECORDING_STORE_FULL, f"{len(data)} bytes over the store's cap")
            return None
        received = self._clock()
        meta = {
            **ref,
            "via": via,
            "received_at": received,
            "duration_s": duration_s if isinstance(duration_s, (int, float)) else None,
            "timestamps": [*timestamps, *history.stamps(history.stamp("received", received))],
        }
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            os.chmod(self._root, 0o700)
            _write_private(audio, data)
            _write_private(self._root / f"{ref['sha256']}.json", json.dumps(meta).encode())
        except OSError as exc:
            self.write_failures += 1
            self._degrade(RECORDING_WRITE_FAILED, type(exc).__name__)
            return None
        self._bytes = self._total() + len(data)
        self.saved += 1
        return ref

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "saved": self.saved,
                "duplicates": self.duplicates,
                "refused_full": self.refused_full,
                "write_failures": self.write_failures,
                "sink_failures": self.sink_failures,
                "bytes": self._total(),
                "max_bytes": self._max_bytes,
            }


#: How much of the room ear's audio is held to cut utterances from (d11). Longer
#: than lobes' force-commit on a stream that never falls silent, so a whole turn
#: is always still there when its speech_stopped arrives.
TAP_SECONDS = 90.0
#: Kept before an onset and after a stop: the VAD's boundaries are 32 ms-quantised
#: and its onset lags the first phoneme (the gateway's segmenter can drop the first
#: word after a silence; this keeps it in the recording either way).
TAP_PREROLL_MS = 300
TAP_POSTROLL_MS = 200


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    """*pcm* (16-bit mono little-endian) as a WAV file, in memory."""
    import io
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(pcm)
    return out.getvalue()


class UtteranceTap:
    """The room ear's sent audio, held briefly, cut by stream time (d11). Never raises.

    The realtime wire reports an utterance's onset and end as ``at_ms``, audio-stream
    time: milliseconds of audio the server has received this session. The tap is fed
    exactly the frames the ears client accepted, in order, so a byte offset here is
    that clock, and an utterance is cut out with arithmetic, not guesswork. A frame
    the client refused is not fed (the server never got it), so the two clocks stay
    one. ``reset`` starts a new session's clock.
    """

    def __init__(self, *, max_seconds: float = TAP_SECONDS, clock: Callable[[], float] = time.time):
        self._max_seconds = max_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self.reset(0)

    def reset(self, rate: int) -> None:
        with self._lock:
            self._rate = int(rate)
            self._buffer = bytearray()
            self._dropped = 0  # bytes trimmed from the front, this session
            self._t0: Optional[float] = None  # wall time of the session's first byte

    @property
    def rate(self) -> int:
        return self._rate

    def feed(self, pcm: bytes) -> None:
        if not pcm or self._rate <= 0:
            return
        with self._lock:
            if self._t0 is None:
                self._t0 = self._clock()
            self._buffer += pcm
            cap = int(self._max_seconds * self._rate) * 2
            extra = len(self._buffer) - cap
            if extra > 0:
                extra += extra % 2
                del self._buffer[:extra]
                self._dropped += extra

    def _offset(self, at_ms: int) -> int:
        offset = int(at_ms * self._rate / 1000) * 2
        return offset - self._dropped

    def cut(self, start_ms: Any, stop_ms: Any) -> Optional[tuple[bytes, float, float]]:
        """``(wav, witnessed_at, duration_s)`` for the utterance, or None if not held."""
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (start_ms, stop_ms)):
            return None
        if stop_ms <= start_ms or self._rate <= 0:
            return None
        with self._lock:
            lo = max(0, self._offset(max(0, start_ms - TAP_PREROLL_MS)))
            hi = min(len(self._buffer), self._offset(stop_ms + TAP_POSTROLL_MS))
            if self._t0 is None or hi <= lo or self._offset(start_ms) < 0:
                return None
            pcm = bytes(self._buffer[lo:hi])
            witnessed = self._t0 + start_ms / 1000.0
        return wav_bytes(pcm, self._rate), witnessed, len(pcm) / 2 / self._rate
