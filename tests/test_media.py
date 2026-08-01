"""Tests for embodiment.media — pure-stdlib media attachment helpers.

Ported from colleague ``1.52.1``'s ``tests/test_media.py`` (task t3); only the
import target changed (``colleague.media`` → ``embodiment.media``).
"""

import base64
import json

import pytest

from embodiment.media import (
    _MEDIA_TYPES,
    IMAGE_TOKEN_ESTIMATE,
    MAX_ATTACHMENT_BYTES,
    build_part,
    flatten_parts,
    validate_attachment,
)

# ── validate_attachment ──────────────────────────────────────────────


class TestValidateAttachment:
    """validate_attachment(path) → {path, media_type}."""

    def test_png(self, tmp_path):
        f = tmp_path / "icon.png"
        f.write_bytes(b"\x89PNG")
        result = validate_attachment(str(f))
        assert result["path"] == str(f)
        assert result["media_type"] == "image/png"

    def test_jpg_maps_to_jpeg(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8")
        result = validate_attachment(str(f))
        assert result["media_type"] == "image/jpeg"

    def test_jpeg(self, tmp_path):
        f = tmp_path / "photo.jpeg"
        f.write_bytes(b"\xff\xd8")
        result = validate_attachment(str(f))
        assert result["media_type"] == "image/jpeg"

    def test_gif(self, tmp_path):
        f = tmp_path / "anim.gif"
        f.write_bytes(b"GIF89a")
        result = validate_attachment(str(f))
        assert result["media_type"] == "image/gif"

    def test_webp(self, tmp_path):
        f = tmp_path / "img.webp"
        f.write_bytes(b"RIFF")
        result = validate_attachment(str(f))
        assert result["media_type"] == "image/webp"

    def test_wav(self, tmp_path):
        f = tmp_path / "clip.wav"
        f.write_bytes(b"RIFF")
        result = validate_attachment(str(f))
        assert result["media_type"] == "audio/wav"

    def test_mp3(self, tmp_path):
        f = tmp_path / "song.mp3"
        f.write_bytes(b"\xff")
        result = validate_attachment(str(f))
        assert result["media_type"] == "audio/mp3"

    def test_ogg(self, tmp_path):
        f = tmp_path / "track.ogg"
        f.write_bytes(b"OggS")
        result = validate_attachment(str(f))
        assert result["media_type"] == "audio/ogg"

    def test_flac(self, tmp_path):
        f = tmp_path / "lossless.flac"
        f.write_bytes(b"fLaC")
        result = validate_attachment(str(f))
        assert result["media_type"] == "audio/flac"

    def test_missing_file_raises(self):
        with pytest.raises(ValueError, match="does-not-exist.png"):
            validate_attachment("does-not-exist.png")

    def test_unknown_extension_raises(self, tmp_path):
        f = tmp_path / "data.xyz"
        f.write_bytes(b"")
        path = str(f)
        with pytest.raises(ValueError, match="data.xyz"):
            validate_attachment(path)

    def test_no_extension_raises(self, tmp_path):
        f = tmp_path / "README"
        f.write_bytes(b"hello")
        path = str(f)
        with pytest.raises(ValueError, match="README"):
            validate_attachment(path)

    def test_uppercase_extension(self, tmp_path):
        f = tmp_path / "LOGO.PNG"
        f.write_bytes(b"\x89PNG")
        result = validate_attachment(str(f))
        assert result["media_type"] == "image/png"

    def test_directory_path_raises(self, tmp_path):
        d = tmp_path / "not_a_file.png"
        d.mkdir()
        path = str(d)
        with pytest.raises(ValueError, match="not_a_file.png"):
            validate_attachment(path)

    def test_oversize_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embodiment.media.MAX_ATTACHMENT_BYTES", 16)
        f = tmp_path / "big.png"
        f.write_bytes(b"\x89PNG" + b"\x00" * 32)
        path = str(f)
        with pytest.raises(ValueError, match="too large"):
            validate_attachment(path)

    def test_file_within_cap_still_validates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embodiment.media.MAX_ATTACHMENT_BYTES", 16)
        f = tmp_path / "small.png"
        f.write_bytes(b"\x89PNG")
        result = validate_attachment(str(f))
        assert result["path"] == str(f)
        assert result["media_type"] == "image/png"

    def test_default_cap_value(self):
        assert MAX_ATTACHMENT_BYTES == 16 * 1024 * 1024


# ── build_part ───────────────────────────────────────────────────────


class TestBuildPart:
    """build_part(attachment) → OpenAI content part dict."""

    def _image_attachment(self, tmp_path, ext="png"):
        f = tmp_path / f"img.{ext}"
        f.write_bytes(b"\x89PNGraw")
        return validate_attachment(str(f))

    def _audio_attachment(self, tmp_path, ext="wav"):
        f = tmp_path / f"clip.{ext}"
        f.write_bytes(b"RIFFraw")
        return validate_attachment(str(f))

    def test_image_part_shape(self, tmp_path):
        att = self._image_attachment(tmp_path, "png")
        part = build_part(att)
        assert part["type"] == "image_url"
        assert "image_url" in part
        url = part["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_image_part_base64_roundtrips(self, tmp_path):
        raw = b"\x89PNGraw"
        att = self._image_attachment(tmp_path, "png")
        part = build_part(att)
        encoded = part["image_url"]["url"].split(",", 1)[1]
        assert base64.b64decode(encoded) == raw

    def test_jpg_becomes_image_jpeg(self, tmp_path):
        att = self._image_attachment(tmp_path, "jpg")
        part = build_part(att)
        url = part["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_audio_part_shape(self, tmp_path):
        att = self._audio_attachment(tmp_path, "wav")
        part = build_part(att)
        assert part["type"] == "input_audio"
        assert "input_audio" in part
        assert part["input_audio"]["format"] == "wav"

    def test_audio_part_base64_roundtrips(self, tmp_path):
        raw = b"RIFFraw"
        att = self._audio_attachment(tmp_path, "mp3")
        part = build_part(att)
        encoded = part["input_audio"]["data"]
        assert base64.b64decode(encoded) == raw

    def test_audio_format_matches_extension(self, tmp_path):
        for ext in ("wav", "mp3", "ogg", "flac"):
            att = self._audio_attachment(tmp_path, ext)
            part = build_part(att)
            assert part["input_audio"]["format"] == ext


# ── IMAGE_TOKEN_ESTIMATE ────────────────────────────────────────────


class TestImageTokenEstimate:
    def test_value(self):
        assert IMAGE_TOKEN_ESTIMATE == 260

    def test_is_int(self):
        assert isinstance(IMAGE_TOKEN_ESTIMATE, int)


# ── flatten_parts ──────────────────────────────────────────────────


class TestFlattenParts:
    """flatten_parts(content) → plain string."""

    def test_plain_string_passthrough(self):
        assert flatten_parts("hello world") == "hello world"

    def test_text_part_passthrough(self):
        parts = [{"type": "text", "text": "hello"}]
        assert flatten_parts(parts) == "hello"

    def test_image_part_placeholder(self):
        parts = [
            {"type": "text", "text": "see "},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        result = flatten_parts(parts)
        assert "[image attachment]" in result
        assert "see " in result

    def test_audio_part_placeholder(self):
        parts = [
            {"type": "text", "text": "hear "},
            {"type": "input_audio", "input_audio": {"data": "abc", "format": "wav"}},
        ]
        result = flatten_parts(parts)
        assert "[audio attachment]" in result
        assert "hear " in result

    def test_mixed_parts(self):
        parts = [
            {"type": "text", "text": "A "},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            {"type": "text", "text": " B "},
            {"type": "input_audio", "input_audio": {"data": "y", "format": "mp3"}},
            {"type": "text", "text": " C"},
        ]
        result = flatten_parts(parts)
        assert "A " in result
        assert "[image attachment]" in result
        assert " B " in result
        assert "[audio attachment]" in result
        assert " C" in result

    def test_empty_parts_list(self):
        assert flatten_parts([]) == ""

    def test_unknown_part_type_ignored(self):
        parts = [
            {"type": "text", "text": "x "},
            {"type": "unknown_type"},
        ]
        result = flatten_parts(parts)
        assert result == "x "


# ── the video content-part lane (task t17) ──────────────────────────
#
# Everything above this line is the colleague ``1.52.1`` port, unchanged.
# Everything below pins the video lane the 2026-07-31 probe justified
# (``docs/live-test-results/video-perception-probe.md``): a rendered match
# replay reaches a vision-capable mind ONLY as a ``video_url`` part, because
# the same bytes on the ``image_url`` path were flattened to a single frame by
# both rig models.

#: Bytes standing in for a league-rendered replay. Hermetic on purpose: nothing
#: in ``media.py`` decodes a container, so a real GIF would prove nothing extra
#: and would make the "delivered unchanged" assertions harder to read.
_REPLAY_BYTES = b"GIF89a\x00replay-frames\x00"

#: Extensions whose media type is natively ``video/*``.
_NATIVE_VIDEO_EXTS = ("mp4", "webm", "mov")


def _replay_gif(tmp_path, name="match-0001.gif"):
    """A league-shaped replay GIF on disk. Returns its path as a str."""
    f = tmp_path / name
    f.write_bytes(_REPLAY_BYTES)
    return str(f)


def _video_file(tmp_path, ext):
    f = tmp_path / f"clip.{ext}"
    f.write_bytes(_REPLAY_BYTES)
    return str(f)


class TestVideoIsOptIn:
    """Criterion 3 — a GIF attached the way callers attach one TODAY is a still.

    ``gif`` became ambiguous the moment video was reachable: it is both how a
    replay is rendered and how a still is attached. Reclassifying it would
    change behaviour for every existing caller silently, so intent is declared
    and the default is preserved.
    """

    def test_gif_validates_to_the_same_two_keys_as_before(self, tmp_path):
        path = _replay_gif(tmp_path)
        assert validate_attachment(path) == {"path": path, "media_type": "image/gif"}

    def test_gif_still_builds_an_image_part(self, tmp_path):
        part = build_part(validate_attachment(_replay_gif(tmp_path)))
        assert part["type"] == "image_url"
        assert part["image_url"]["url"].startswith("data:image/gif;base64,")

    def test_as_video_false_is_identical_to_omitting_it(self, tmp_path):
        path = _replay_gif(tmp_path)
        assert validate_attachment(path, as_video=False) == validate_attachment(path)

    def test_no_kind_key_appears_unless_asked_for(self, tmp_path):
        for ext, raw in (("png", b"\x89PNG"), ("gif", b"GIF89a"), ("wav", b"RIFF")):
            f = tmp_path / f"still.{ext}"
            f.write_bytes(raw)
            assert "kind" not in validate_attachment(str(f))

    def test_asking_for_video_is_the_only_difference(self, tmp_path):
        path = _replay_gif(tmp_path)
        still = validate_attachment(path)
        replay = validate_attachment(path, as_video=True)
        assert replay == {**still, "kind": "video"}


class TestVideoNeverRidesTheImagePath:
    """Criterion 2 — the probe's core finding, encoded so it cannot regress.

    A GIF on the ``image_url`` path was answered ``ONLY ONE FRAME`` by both rig
    models. Anything routed as video must therefore never produce an
    ``image_url`` part, whichever way it was declared.
    """

    def test_replay_gif_builds_a_video_part(self, tmp_path):
        part = build_part(validate_attachment(_replay_gif(tmp_path), as_video=True))
        assert part["type"] == "video_url"
        assert "video_url" in part

    def test_replay_gif_part_carries_no_image_url_key(self, tmp_path):
        part = build_part(validate_attachment(_replay_gif(tmp_path), as_video=True))
        assert "image_url" not in part
        assert part["type"] != "image_url"

    @pytest.mark.parametrize("ext", _NATIVE_VIDEO_EXTS)
    def test_native_video_never_builds_an_image_part(self, tmp_path, ext):
        part = build_part(validate_attachment(_video_file(tmp_path, ext)))
        assert part["type"] == "video_url"
        assert "image_url" not in part

    def test_no_video_capable_input_reaches_the_image_path(self, tmp_path):
        # The sweep: every way an attachment can be declared video, checked in
        # one place, so a new route added later without a video branch fails
        # here rather than shipping as a silently-flattened single frame.
        attachments = [validate_attachment(_replay_gif(tmp_path), as_video=True)]
        attachments += [validate_attachment(_video_file(tmp_path, e)) for e in _NATIVE_VIDEO_EXTS]
        for attachment in attachments:
            part = build_part(attachment)
            assert part["type"] == "video_url", attachment
            assert "image_url" not in part, attachment

    def test_a_hand_built_video_attachment_still_routes_to_video(self, tmp_path):
        # ``loop.py`` calls ``build_part`` on raw ``Task.attachments`` entries,
        # which need not have come from ``validate_attachment`` in-process.
        part = build_part({"path": _video_file(tmp_path, "mp4"), "media_type": "video/mp4"})
        assert part["type"] == "video_url"

    def test_intent_survives_a_json_round_trip(self, tmp_path):
        # ``Task.attachments`` serializes; ``kind`` is a plain string precisely
        # so a replay stays a replay across a to_dict/from_dict boundary.
        attachment = validate_attachment(_replay_gif(tmp_path), as_video=True)
        revived = json.loads(json.dumps(attachment))
        assert build_part(revived)["type"] == "video_url"


class TestVideoPartShape:
    """The part itself: honest MIME, untouched bytes."""

    def test_declared_mime_is_the_truth_not_a_disguise(self, tmp_path):
        # The probe sent GIF bytes labelled ``video/mp4`` and the server sniffed
        # past it. We do not rely on that: a GIF says it is a GIF.
        part = build_part(validate_attachment(_replay_gif(tmp_path), as_video=True))
        assert part["video_url"]["url"].startswith("data:image/gif;base64,")
        assert "video/mp4" not in part["video_url"]["url"]

    @pytest.mark.parametrize(
        "ext,expected",
        [("mp4", "video/mp4"), ("webm", "video/webm"), ("mov", "video/quicktime")],
    )
    def test_native_video_declares_its_own_type(self, tmp_path, ext, expected):
        part = build_part(validate_attachment(_video_file(tmp_path, ext)))
        assert part["video_url"]["url"].startswith(f"data:{expected};base64,")

    def test_bytes_are_delivered_unchanged(self, tmp_path):
        # No frame sampler, no re-encode: what league rendered is what ships.
        part = build_part(validate_attachment(_replay_gif(tmp_path), as_video=True))
        encoded = part["video_url"]["url"].split(",", 1)[1]
        assert base64.b64decode(encoded) == _REPLAY_BYTES

    def test_one_part_per_attachment_no_frame_expansion(self, tmp_path):
        # The ordered-frame-sequence workaround the probe ruled out would have
        # turned one file into N parts. One file, one part.
        part = build_part(validate_attachment(_replay_gif(tmp_path), as_video=True))
        assert isinstance(part, dict)
        assert set(part) == {"type", "video_url"}


class TestVideoValidation:
    """Criterion 4 — one door, one cap, for video exactly as for images/audio."""

    @pytest.mark.parametrize(
        "ext,expected",
        [("mp4", "video/mp4"), ("webm", "video/webm"), ("mov", "video/quicktime")],
    )
    def test_native_video_extensions_resolve(self, tmp_path, ext, expected):
        assert validate_attachment(_video_file(tmp_path, ext))["media_type"] == expected

    def test_every_video_media_type_is_registered_centrally(self):
        # Single point of classification: no video type may be invented at a
        # call site, the same discipline images and audio already follow.
        assert {_MEDIA_TYPES[e] for e in _NATIVE_VIDEO_EXTS} == {
            "video/mp4",
            "video/webm",
            "video/quicktime",
        }

    def test_oversize_video_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embodiment.media.MAX_ATTACHMENT_BYTES", 16)
        f = tmp_path / "long-match.mp4"
        f.write_bytes(b"\x00" * 64)
        path = str(f)
        with pytest.raises(ValueError, match="too large"):
            validate_attachment(path)

    def test_oversize_replay_gif_as_video_raises(self, tmp_path, monkeypatch):
        # The cap is not escapable by declaring video — same door, same cap.
        monkeypatch.setattr("embodiment.media.MAX_ATTACHMENT_BYTES", 16)
        f = tmp_path / "long-match.gif"
        f.write_bytes(b"GIF89a" + b"\x00" * 64)
        path = str(f)
        with pytest.raises(ValueError, match="too large"):
            validate_attachment(path, as_video=True)

    def test_missing_video_file_raises(self):
        with pytest.raises(ValueError, match="no-such-replay.mp4"):
            validate_attachment("no-such-replay.mp4")

    def test_missing_file_raises_even_when_asked_for_as_video(self):
        with pytest.raises(ValueError, match="no-such-replay.gif"):
            validate_attachment("no-such-replay.gif", as_video=True)

    def test_directory_as_video_raises(self, tmp_path):
        d = tmp_path / "replays.gif"
        d.mkdir()
        path = str(d)
        with pytest.raises(ValueError, match="not a regular file"):
            validate_attachment(path, as_video=True)

    def test_unknown_extension_as_video_raises(self, tmp_path):
        f = tmp_path / "replay.xyz"
        f.write_bytes(_REPLAY_BYTES)
        path = str(f)
        with pytest.raises(ValueError, match="Unknown attachment extension"):
            validate_attachment(path, as_video=True)

    @pytest.mark.parametrize("ext", ["png", "jpg", "jpeg"])
    def test_as_video_on_a_single_frame_image_raises(self, tmp_path, ext):
        f = tmp_path / f"still.{ext}"
        f.write_bytes(b"\x89PNG")
        path = str(f)
        with pytest.raises(ValueError, match="cannot be delivered as video"):
            validate_attachment(path, as_video=True)

    @pytest.mark.parametrize("ext", ["wav", "mp3", "ogg", "flac"])
    def test_as_video_on_audio_raises(self, tmp_path, ext):
        f = tmp_path / f"clip.{ext}"
        f.write_bytes(b"RIFF")
        path = str(f)
        with pytest.raises(ValueError, match="cannot be delivered as video"):
            validate_attachment(path, as_video=True)

    def test_animated_webp_may_be_declared_video(self, tmp_path):
        f = tmp_path / "replay.webp"
        f.write_bytes(b"RIFF")
        assert validate_attachment(str(f), as_video=True)["kind"] == "video"

    def test_native_video_as_video_is_a_no_op(self, tmp_path):
        path = _video_file(tmp_path, "mp4")
        assert build_part(validate_attachment(path, as_video=True))["type"] == "video_url"


class TestFlattenVideoPart:
    """Criterion 1 — a text-only surface degrades visibly, never silently (C3)."""

    def _video_part(self, tmp_path):
        return build_part(validate_attachment(_replay_gif(tmp_path), as_video=True))

    def test_video_part_placeholder(self):
        parts = [
            {"type": "text", "text": "watch "},
            {"type": "video_url", "video_url": {"url": "data:image/gif;base64,abc"}},
        ]
        result = flatten_parts(parts)
        assert "[video attachment]" in result
        assert "watch " in result

    def test_a_lone_video_part_is_not_dropped(self):
        # The failing shape C3 forbids: a text surface returning "" for a
        # request that carried a replay, reading as complete and being poorer.
        parts = [{"type": "video_url", "video_url": {"url": "data:image/gif;base64,abc"}}]
        assert flatten_parts(parts) == "[video attachment]"

    def test_video_placeholder_is_distinct_from_the_image_one(self):
        parts = [{"type": "video_url", "video_url": {"url": "data:image/gif;base64,abc"}}]
        assert "[image attachment]" not in flatten_parts(parts)

    def test_placeholder_does_not_leak_the_payload(self, tmp_path):
        # A text surface must get a short marker, never a base64 blob.
        flattened = flatten_parts([self._video_part(tmp_path)])
        assert flattened == "[video attachment]"

    def test_mixed_parts_with_video(self, tmp_path):
        parts = [
            {"type": "text", "text": "match "},
            self._video_part(tmp_path),
            {"type": "text", "text": " replay"},
        ]
        result = flatten_parts(parts)
        assert result == "match [video attachment] replay"

    def test_every_built_part_type_has_a_placeholder(self, tmp_path):
        # The pairing criterion 1 asks for, checked over the builder's whole
        # output range: add a part type without a flatten branch and this fails.
        png = tmp_path / "still.png"
        png.write_bytes(b"\x89PNG")
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFF")
        attachments = [
            validate_attachment(str(png)),
            validate_attachment(str(wav)),
            validate_attachment(_replay_gif(tmp_path), as_video=True),
            validate_attachment(_video_file(tmp_path, "mp4")),
        ]
        for attachment in attachments:
            flattened = flatten_parts([build_part(attachment)])
            assert flattened.startswith("["), attachment
            assert flattened.endswith(" attachment]"), attachment
