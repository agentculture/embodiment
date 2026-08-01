"""Pure-stdlib helpers for media attachments (images, audio, video).

Provides attachment validation, OpenAI content-part construction, and
content flattening for the multi-modal input path.

Ported from colleague ``1.52.1``'s ``colleague/media.py`` (task t3). That port
was byte-faithful; the **video content-part lane** (task t17) is this module's
first behavioural addition on top of it, and it is strictly additive — a caller
who does not ask for video gets exactly what the port shipped, byte for byte.

Why a separate transport rather than more images, recorded here so neither dead
end is re-explored (``docs/live-test-results/video-perception-probe.md``,
2026-07-31, both rig models):

============================  ========  ==============  ===================
route                         motion?   prompt tokens   verdict
============================  ========  ==============  ===================
GIF as ``image_url``          no        132             flattened to 1 frame
3 ordered PNG ``image_url``   yes       289             works; unnecessary
GIF as ``video_url``          **yes**   **86**          **the path**
============================  ========  ==============  ===================

The *same bytes* carry motion or do not depending only on which content part
they ride in. So the whole of this feature is a transport choice: there is no
frame sampler here, no re-encode, and no ordering protocol, because the probe
ruled out needing any of them.
"""

import base64
from pathlib import Path

# Per-image-tile prompt-token estimate measured on the live Gemma4 probe 2026-07-02.
IMAGE_TOKEN_ESTIMATE = 260

#: Size cap for a ``Task.attachments`` entry (CLI ``--attach``, session
#: ``/attach``, and mesh ``attach:`` references all funnel through
#: :func:`validate_attachment`). Mirrors the intent of ``colleague.tools``'s
#: ``MAX_MEDIA_BYTES`` (the ``view_media`` tool's 4 MB cap) but is defined here,
#: not imported, to avoid a tools->media->tools layering cycle. Sized generously
#: above any real attachment (screenshots, short clips) while still bounding
#: memory + the base64-inflated prompt a non-operator mesh request could force.
MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024

# Extension → media type mapping.
_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "wav": "audio/wav",
    "mp3": "audio/mp3",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
}

#: Image media types whose container can hold MORE THAN ONE FRAME, and which a
#: caller may therefore ask to have delivered as video
#: (``validate_attachment(path, as_video=True)``).
#:
#: This set exists because ``gif`` is genuinely ambiguous: league renders match
#: replays as animated GIFs, and the *same* extension is also how a caller
#: attaches an ordinary still. It is not this module's place to guess — it holds
#: no decoder and cannot count a file's frames — so the caller declares intent
#: and the default stays "still", preserving every existing caller's behaviour.
#:
#: Deliberately absent: ``image/png`` (APNG exists, but is out of scope — a
#: caller holding one should re-container it and will get a loud ``ValueError``,
#: never a silent still) and ``image/jpeg`` (single frame by construction).
_MOTION_CAPABLE_IMAGE_TYPES: frozenset[str] = frozenset({"image/gif", "image/webp"})


def _may_carry_motion(media_type: str) -> bool:
    """Can *media_type*'s container hold a frame sequence at all?"""
    return media_type.startswith("video/") or media_type in _MOTION_CAPABLE_IMAGE_TYPES


def _is_video(attachment: dict) -> bool:
    """Does *attachment* route to a ``video_url`` content part?

    Two ways in, and only two:

    * the bytes are natively a video container (``media_type`` is ``video/*``);
    * the caller declared motion intent on an ambiguous still-or-replay
      container, which :func:`validate_attachment` records as ``kind="video"``.

    An attachment carrying neither is a still (or audio) and is untouched by
    this lane — which is what keeps the default byte-identical.
    """
    if attachment.get("kind") == "video":
        return True
    return str(attachment.get("media_type") or "").startswith("video/")


def validate_attachment(path: str, *, as_video: bool = False) -> dict:
    """Validate *path* exists, is a regular file, has a known media
    extension, and is within :data:`MAX_ATTACHMENT_BYTES`.

    Returns a dict with keys ``path`` (str) and ``media_type`` (str).
    Raises ``ValueError`` for a missing file, a non-regular-file path (e.g. a
    directory), an unknown extension, or an oversize file. All three
    attachment surfaces (CLI ``--attach``, session ``/attach``, mesh
    ``attach:`` references) funnel through this one function, so the size cap
    is enforced here rather than at each call site — and video is no exception:
    it enters through the same door and meets the same cap.

    ``as_video`` is how a caller says "these bytes are a REPLAY, not a still".
    It is the whole of the intent surface, and it is keyword-only and off by
    default, so today's callers keep today's behaviour byte for byte — an
    animated GIF still validates to a plain ``{"path", "media_type"}`` and
    still builds an ``image_url`` part. Ask for video and the returned dict
    gains a third key, ``kind="video"``, which :func:`build_part` reads back;
    the key is a plain string so it survives a ``Task.attachments`` JSON round
    trip unchanged.

    ``as_video=True`` on a container that cannot hold a frame sequence (a JPEG,
    a PNG, any audio file) raises ``ValueError`` rather than quietly delivering
    a still: a caller who believes they attached a replay must not be told
    nothing when they did not.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Attachment file not found: {path}")
    if not p.is_file():
        raise ValueError(f"Attachment path is not a regular file: {path}")

    ext = p.suffix.lstrip(".").lower()
    if ext not in _MEDIA_TYPES:
        raise ValueError(f"Unknown attachment extension '{ext}' for {path}")

    media_type = _MEDIA_TYPES[ext]
    if as_video and not _may_carry_motion(media_type):
        raise ValueError(
            f"Attachment cannot be delivered as video: {path} is {media_type}, "
            "a single-frame container"
        )

    size = p.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"attachment too large: {path} is {size} bytes (max {MAX_ATTACHMENT_BYTES})"
        )

    attachment = {"path": str(p), "media_type": media_type}
    if as_video:
        attachment["kind"] = "video"
    return attachment


def build_part(attachment: dict) -> dict:
    """Build a standard OpenAI content part from a validated attachment.

    * ``attachment`` is the dict returned by :func:`validate_attachment`.
    * Video attachments become ``{"type": "video_url", "video_url": ...}``.
    * Image attachments become ``{"type": "image_url", "image_url": ...}``.
    * Audio attachments become ``{"type": "input_audio", "input_audio": ...}``.

    Video is dispatched FIRST and never falls through to ``image_url``. That
    ordering is the measured finding, not a style choice: an animated GIF sent
    as an ``image_url`` part was flattened to a single frame by both rig models,
    while the identical bytes sent as ``video_url`` were decoded as a sequence
    (and more cheaply — 86 prompt tokens against 132 for one still). The bytes
    are passed through untouched: no re-encode, no frame extraction, no
    downsampling. Whatever sampling happens is the server's.

    The declared MIME stays HONEST. A GIF delivered as video is declared
    ``data:image/gif;base64,…`` inside a ``video_url`` part — it is not
    relabelled ``video/mp4`` to "look like" video. The probe established that
    the server sniffs content and ignored a deliberately wrong declared type,
    so the label is not load-bearing, which is exactly why it costs nothing to
    tell the truth. Note what that means for provenance: the mislabelled variant
    is the one measured live; the honest label rides the same transport but has
    not itself been measured against the rig.
    """
    file_bytes = Path(attachment["path"]).read_bytes()
    encoded = base64.b64encode(file_bytes).decode("ascii")
    media_type = attachment["media_type"]

    if _is_video(attachment):
        return {
            "type": "video_url",
            "video_url": {"url": f"data:{media_type};base64,{encoded}"},
        }

    if media_type.startswith("image/"):
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
        }

    # audio
    ext = Path(attachment["path"]).suffix.lstrip(".").lower()
    return {
        "type": "input_audio",
        "input_audio": {"data": encoded, "format": ext},
    }


def flatten_parts(content) -> str:
    """Flatten *content* to a plain string.

    * A plain ``str`` passes through unchanged.
    * A list of parts is joined: text parts pass through, media parts
      become bracketed placeholders (``[image attachment]``,
      ``[audio attachment]``, ``[video attachment]``).

    Every part type :func:`build_part` can produce has a placeholder here, and
    that pairing is a constraint rather than a convenience (C3 — degradation
    must be observable to the host). A text-only surface that dropped a video
    part would leave no trace that a replay was ever attached: the request would
    read as complete and be silently poorer. A placeholder degrades the same
    situation into something the host and the reader can both see.
    """
    if isinstance(content, str):
        return content

    segments: list[str] = []
    for part in content:
        if part.get("type") == "text":
            segments.append(part["text"])
        elif part.get("type") == "image_url":
            segments.append("[image attachment]")
        elif part.get("type") == "input_audio":
            segments.append("[audio attachment]")
        elif part.get("type") == "video_url":
            segments.append("[video attachment]")
        # unknown part types are silently skipped
    return "".join(segments)
