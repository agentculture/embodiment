"""The recording store (deviation d10): clips kept as they arrived, private and bounded."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from embodiment.daemon.recordings import (
    RECORDING_STORE_FULL,
    RECORDING_WRITE_FAILED,
    RecordingStore,
)

AUDIO = b"OggS" + b"\x01" * 100


def _store(tmp_path: Path, **kw):
    seen: list[tuple[str, str]] = []
    store = RecordingStore(
        tmp_path / "recordings", on_degrade=lambda c, r: seen.append((c, r)), **kw
    )
    return store, seen


def test_a_clip_is_kept_byte_for_byte_with_what_is_known_about_it(tmp_path):
    store, _ = _store(tmp_path, clock=lambda: 1234.5)
    ref = store.save(
        AUDIO, extension="ogg", content_type="audio/ogg", via="recorded", duration_s=2.5
    )
    digest = hashlib.sha256(AUDIO).hexdigest()
    assert ref == {
        "sha256": digest,
        "bytes": len(AUDIO),
        "content_type": "audio/ogg",
        "file": f"{digest}.ogg",
    }
    root = tmp_path / "recordings"
    assert (root / f"{digest}.ogg").read_bytes() == AUDIO
    meta = json.loads((root / f"{digest}.json").read_text())
    assert (meta["via"], meta["received_at"], meta["duration_s"]) == ("recorded", 1234.5, 2.5)


def test_files_are_private(tmp_path):
    store, _ = _store(tmp_path)
    ref = store.save(AUDIO, extension="ogg", content_type="audio/ogg", via="uploaded")
    root = tmp_path / "recordings"
    assert root.stat().st_mode & 0o777 == 0o700
    for name in (ref["file"], f"{ref['sha256']}.json"):
        assert (root / name).stat().st_mode & 0o777 == 0o600


def test_the_same_clip_twice_is_kept_once(tmp_path):
    store, _ = _store(tmp_path)
    first = store.save(AUDIO, extension="ogg", content_type="audio/ogg", via="uploaded")
    again = store.save(AUDIO, extension="ogg", content_type="audio/ogg", via="uploaded")
    assert first == again
    assert (store.saved, store.duplicates) == (1, 1)


def test_a_full_store_refuses_and_says_so_and_deletes_nothing(tmp_path):
    store, seen = _store(tmp_path, max_bytes=150)
    kept = store.save(AUDIO, extension="ogg", content_type="audio/ogg", via="recorded")
    refused = store.save(AUDIO + b"x", extension="ogg", content_type="audio/ogg", via="recorded")
    assert kept is not None and refused is None
    assert (tmp_path / "recordings" / kept["file"]).is_file(), "an older recording was removed"
    assert [c for c, _ in seen] == [RECORDING_STORE_FULL]
    assert store.status()["refused_full"] == 1


def test_the_cap_counts_what_is_already_on_disk(tmp_path):
    RecordingStore(tmp_path / "recordings").save(
        AUDIO, extension="ogg", content_type="audio/ogg", via="recorded"
    )
    store, seen = _store(tmp_path, max_bytes=150)
    assert store.save(b"y" * 60, extension="ogg", content_type="a", via="recorded") is None
    assert [c for c, _ in seen] == [RECORDING_STORE_FULL]


def test_a_failed_write_is_recorded_not_raised(tmp_path):
    (tmp_path / "recordings").write_text("a file where the directory should be")
    store, seen = _store(tmp_path)
    assert store.save(AUDIO, extension="ogg", content_type="audio/ogg", via="recorded") is None
    assert [c for c, _ in seen] == [RECORDING_WRITE_FAILED]
    assert store.status()["write_failures"] == 1


def test_an_odd_extension_becomes_bin_and_nothing_empty_is_kept(tmp_path):
    store, _ = _store(tmp_path)
    ref = store.save(AUDIO, extension="../x", content_type="audio/ogg", via="clip")
    assert ref["file"].endswith(".bin")
    assert store.save(b"", extension="ogg", content_type="audio/ogg", via="clip") is None


def test_a_failing_sink_is_counted(tmp_path):
    def sink(code, reason):
        raise RuntimeError("down")

    store = RecordingStore(tmp_path / "r", max_bytes=1, on_degrade=sink)
    assert store.save(AUDIO, extension="ogg", content_type="a", via="clip") is None
    assert store.status()["sink_failures"] == 1


# ── d11: the room ear's utterances, cut by stream time ──────────────────────────

import io  # noqa: E402
import wave  # noqa: E402

from embodiment.daemon.recordings import TAP_POSTROLL_MS, TAP_PREROLL_MS, UtteranceTap  # noqa: E402


def _seconds_of(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as handle:
        return handle.getnframes() / handle.getframerate()


def test_an_utterance_is_cut_by_stream_time_with_its_witnessed_time():
    tap = UtteranceTap(clock=lambda: 1000.0)
    tap.reset(16000)
    for _ in range(20):
        tap.feed(b"\x01\x00" * 1600)  # 20 x 100 ms
    wav, witnessed, duration = tap.cut(500, 1500)
    expected = (1000 + TAP_PREROLL_MS + TAP_POSTROLL_MS) / 1000
    assert abs(_seconds_of(wav) - expected) < 0.002
    assert abs(duration - expected) < 0.002
    assert witnessed == 1000.5


def test_audio_already_trimmed_is_not_cut():
    tap = UtteranceTap(max_seconds=1.0, clock=lambda: 1.0)
    tap.reset(16000)
    for _ in range(30):
        tap.feed(b"\x00\x00" * 1600)  # 3 s into a 1 s tap
    assert tap.cut(100, 600) is None
    assert tap.cut(2500, 2900) is not None


def test_a_bad_cut_keeps_nothing():
    tap = UtteranceTap()
    tap.reset(16000)
    tap.feed(b"\x00\x00" * 1600)
    assert tap.cut(None, 50) is None
    assert tap.cut(80, 40) is None
    assert UtteranceTap().cut(0, 10) is None, "no session yet"


def test_reset_starts_a_new_clock():
    tap = UtteranceTap(clock=lambda: 5.0)
    tap.reset(16000)
    tap.feed(b"\x00\x00" * 16000)
    tap.reset(24000)
    assert tap.rate == 24000 and tap.cut(0, 100) is None


def test_saved_recordings_carry_typed_timestamps(tmp_path):
    store = RecordingStore(tmp_path / "r", clock=lambda: 1792800000.0)
    ref = store.save(
        AUDIO,
        extension="wav",
        content_type="audio/wav",
        via="spoken",
        timestamps=[{"type": "witnessed", "at": "2026-10-24T10:00:00.000Z"}],
    )
    meta = json.loads((tmp_path / "r" / f"{ref['sha256']}.json").read_text())
    assert [t["type"] for t in meta["timestamps"]] == ["witnessed", "received"]
