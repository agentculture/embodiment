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
