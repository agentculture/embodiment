#!/usr/bin/env python3
"""perception_media — the seam where a route's rendered parts become a dial.

Plan task **t18** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`).

The defect this closes
----------------------
``examples/arch_league.py`` (t6) gave :class:`~examples.arch_league.Perception`
a ``parts`` field; ``examples/arch_league_maps.py`` (t10) filled it with a
rendered PNG. **Nothing consumed it.** An image cell rendered its map, hashed
it, passed the twin check, committed the PNG — and then dialled the model with
text only, reporting an image result that was really a text result, with every
guard passing. The mechanism was right and the verification was the defect.

Where the fix lives, and why here rather than in each route
------------------------------------------------------------
This module is the **message-assembly** half, and it is deliberately route-blind:
:func:`staged_attachments` takes ``(owner, parts)`` pairs and knows nothing about
maps, twins, fog or league. ``arch_league.run_round`` calls it once, for every
route, so a route that fills ``Perception.parts`` is dialled with them **by
construction** — a future route inherits this instead of having to remember it.
The alternative (each route building its own content parts) is the same design
that produced the defect: a per-route obligation nobody was checking.

How bytes in memory meet ``embodiment.media``'s file-oriented validation
--------------------------------------------------------------------------
:func:`embodiment.media.validate_attachment` takes a **path**: it stats the
file, reads the extension, and enforces
:data:`~embodiment.media.MAX_ATTACHMENT_BYTES`. A route's parts are bytes in
memory. Rather than add a bytes door beside that one — a second place for the
cap and the media-type table to rot — this module **materialises** each part
into a short-lived staging directory and hands the path to the same
``validate_attachment``. So there remains exactly one validator, one cap and
one media-type table, and every attachment that reaches
:func:`embodiment.media.build_part` came through it.

Two disciplines make the materialisation honest:

* **The suffix is sniffed from the bytes, never asserted.** A route hands
  content; :func:`sniff_suffix` reads the container signature. Bytes matching
  no known signature are refused rather than written under a guessed extension
  — a wrong extension would be a wrong declared MIME type on the wire.
* **Nothing is written until everything can be.** :func:`staged_attachments`
  resolves and size-checks every part *before* it opens a staging directory, so
  an oversize or unusable part costs no disk at all, and a round is never
  half-materialised. The size check reads
  ``media.MAX_ATTACHMENT_BYTES`` at call time — the same constant
  ``validate_attachment`` enforces afterwards, which stays the authority.

Refusal, never a quiet downgrade (constraint C3)
--------------------------------------------------
Every failure raises :class:`PerceptionMediaError` naming the owner, the part
and the reason. There is no path here that drops a part and continues, because
that is the defect wearing a different coat: a cell dialled with text only
while its record still says ``route=map_image``. ``arch_league.run_round``
turns this exception into a recorded refusal and the cell goes ``ABSENT``.

Video rides the same seam
--------------------------
:class:`MediaBytes` carries the one thing bytes cannot carry — the caller's
intent — so an animated GIF a route means as a *replay* becomes a ``video_url``
part rather than a flattened still
(``docs/live-test-results/video-perception-probe.md``). That is the whole of
the video surface here: no frame sampler, no re-encode, no ordering protocol,
because ``embodiment/media.py`` (t17) already established none is needed.

Nothing in this module opens a socket, dials a model, or reaches an arena.
"""

from __future__ import annotations

import contextlib
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import media  # noqa: E402

__all__ = [
    "ACCEPTED_PART_SHAPES",
    "MediaBytes",
    "PerceptionMediaError",
    "sniff_suffix",
    "staged_attachments",
]


class PerceptionMediaError(RuntimeError):
    """A perception part could not be turned into a validated attachment.

    Always raised, never returned: a part that cannot ride the wire must not
    be silently dropped into a text-only dial (C3).
    """


#: Said back to a caller who handed something this seam cannot place. Written
#: once, here, so the message and the accepted set cannot drift apart.
ACCEPTED_PART_SHAPES = (
    "bytes (the container is sniffed), MediaBytes(data=..., as_video=...) when "
    "the bytes are a replay rather than a still, or a str/Path naming a file the "
    "route already wrote"
)


@dataclass(frozen=True)
class MediaBytes:
    """In-memory media, plus the one thing the bytes themselves cannot say.

    ``as_video`` is the caller declaring "these bytes are a REPLAY, not a
    still" — the same keyword-only, off-by-default intent surface
    :func:`embodiment.media.validate_attachment` takes, carried through
    unchanged rather than reinterpreted. ``suffix`` overrides the sniffer for a
    caller who genuinely knows better; empty (the default) means sniff, which is
    what keeps a wrong declared MIME type off the wire.
    """

    data: bytes
    suffix: str = ""
    as_video: bool = False


# ── sniffing: the extension is read off the bytes, never guessed ─────────────


def sniff_suffix(data: bytes) -> str:
    """Which container is this? Raises rather than guessing.

    Every suffix this can return is one
    :func:`embodiment.media.validate_attachment` knows, which is asserted by
    ``tests/test_perception_media.py`` — the two tables must not drift, and a
    suffix ``media`` rejects would turn a rendering into a refusal at the far
    end of the pipe rather than here.
    """
    head = bytes(data[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"RIFF"):
        if head[8:12] == b"WEBP":
            return "webp"
        if head[8:12] == b"WAVE":
            return "wav"
    if head[4:8] == b"ftyp":
        # The brand distinguishes QuickTime from MP4. Declaring a .mov as mp4
        # would put a false MIME type on the wire for no gain.
        return "mov" if head[8:12] == b"qt  " else "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    raise PerceptionMediaError(
        f"these {len(data)} bytes match no container signature this seam knows "
        f"({head[:8]!r}...); a route must render a recognisable container, or say "
        "which one it rendered with MediaBytes(suffix=...)"
    )


# ── resolution: everything is checked before anything is written ─────────────


@dataclass(frozen=True)
class _Pending:
    """One part, resolved to what it will become. No disk touched yet."""

    owner: str
    index: int
    data: Optional[bytes]
    suffix: str
    as_video: bool
    path: Optional[str]


_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _stem(label: str, owner: str, index: int) -> str:
    """A filesystem-safe staging name. Identifying, never load-bearing."""
    safe_label = _UNSAFE.sub("-", label).strip("-") or "perception"
    safe_owner = _UNSAFE.sub("-", owner).strip("-") or "part"
    return f"{safe_label}-{safe_owner}-{index}"


def _resolve(part: Any, *, owner: str, index: int) -> _Pending:
    """Classify one part and check everything that can be checked off-disk."""
    if isinstance(part, (str, Path)):
        try:
            attachment = media.validate_attachment(str(part))
        except ValueError as broken:
            raise PerceptionMediaError(f"{owner} part {index}: {broken}") from broken
        return _Pending(
            owner=owner,
            index=index,
            data=None,
            suffix="",
            as_video=False,
            path=attachment["path"],
        )

    if isinstance(part, MediaBytes):
        data = bytes(part.data)
        suffix = part.suffix.lstrip(".").lower()
        as_video = bool(part.as_video)
    elif isinstance(part, (bytes, bytearray, memoryview)):
        data = bytes(part)
        suffix = ""
        as_video = False
    else:
        raise PerceptionMediaError(
            f"{owner} part {index}: a {type(part).__name__} is not a perception part "
            f"this seam can place on the wire. Accepted: {ACCEPTED_PART_SHAPES}"
        )

    if not data:
        raise PerceptionMediaError(f"{owner} part {index}: the route rendered zero bytes")

    # Read the cap at CALL time, from the module that owns it. This refuses
    # before a byte is written; `validate_attachment` enforces the same bound
    # afterwards and remains the authority.
    cap = media.MAX_ATTACHMENT_BYTES
    if len(data) > cap:
        raise PerceptionMediaError(
            f"{owner} part {index}: {len(data)} bytes exceeds "
            f"embodiment.media.MAX_ATTACHMENT_BYTES ({cap}); refused before it was "
            "written to disk"
        )

    if not suffix:
        try:
            suffix = sniff_suffix(data)
        except PerceptionMediaError as unknown:
            # Re-raised with the owner in front: a refusal that names only the
            # bytes sends a reader hunting for which unit rendered them.
            raise PerceptionMediaError(f"{owner} part {index}: {unknown}") from unknown

    return _Pending(
        owner=owner,
        index=index,
        data=data,
        suffix=suffix,
        as_video=as_video,
        path=None,
    )


def _materialise(pending: _Pending, *, directory: Path, label: str) -> dict[str, Any]:
    """Write one resolved part out and hand the path to ``media`` to validate."""
    if pending.path is not None:
        # Already validated during resolution; re-validating is cheap and keeps
        # `media` the only producer of an attachment dict.
        return media.validate_attachment(pending.path)

    target = directory / f"{_stem(label, pending.owner, pending.index)}.{pending.suffix}"
    target.write_bytes(pending.data or b"")
    try:
        attachment = media.validate_attachment(str(target), as_video=pending.as_video)
    except ValueError as broken:
        raise PerceptionMediaError(f"{pending.owner} part {pending.index}: {broken}") from broken
    return attachment


@contextlib.contextmanager
def staged_attachments(
    sources: Iterable[tuple[str, Sequence[Any]]], *, label: str = "perception"
) -> Iterator[list[dict[str, Any]]]:
    """Every source's parts, as validated attachments, for the life of the block.

    *sources* is ``(owner, parts)`` pairs — the owner is a name that lands in
    any refusal message (a unit id, in the league lane) and in the staging
    filename. The yielded list is exactly what
    :attr:`embodiment.contract.Task.attachments` takes, in source order, and
    the files behind it exist until the block exits and not one moment longer.

    **No parts means no staging directory at all** — the list is empty, no
    temporary directory is created, and the caller passes ``None`` to ``Task``.
    That is what keeps a media-less route's payload byte-identical to what it
    was before this seam existed.
    """
    pending = [
        _resolve(part, owner=str(owner), index=index)
        for owner, parts in sources
        for index, part in enumerate(parts or ())
    ]
    if not pending:
        yield []
        return

    with contextlib.ExitStack() as stack:
        directory: Optional[Path] = None
        if any(entry.data is not None for entry in pending):
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="embodiment-")))
        yield [_materialise(entry, directory=directory or Path(), label=label) for entry in pending]
