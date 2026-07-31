#!/usr/bin/env python3
"""map_render — a team-scoped fog map, drawn from the fogged briefing alone.

The orchestrator-worker series puts an image in front of a mind: the same fog
snapshot the text briefing describes, rendered as a picture, so any separation
between the two arms attributes to *presentation modality* rather than to
information content. This module is that instrument.

    uv run python examples/map_render.py render --briefing turn.json --out map.png
    uv run python examples/map_render.py turn --briefing turn.json --out-dir raw/ \\
        --match cm-1 --turn 7 --seat blue-1
    uv run python examples/map_render.py goldens --out tests/goldens/maps

The one rule this file exists to obey
-------------------------------------
**It never computes visibility.** league's fogged briefing (``charness.
build_briefing`` with ``fog: true``) has already removed everything the acting
team cannot see, and — this is the load-bearing part — it carries **no vision
radii**. A renderer that reconstructed the fog rule would have to copy league's
per-role ``vision_mu`` stats, and the moment league tuned one the copy would
desync: the map would start drawing entities the mind is not entitled to see,
or hiding ones it is. That is the fog-leak defect the spec's honesty condition
forbids, and duplication is precisely how it would arrive.

So the contract here is narrow and checkable: *draw the entities the briefing
listed, and claim nothing about anywhere else*. There is no radius, no distance
comparison, no role table, and no import of league anywhere in this file —
``tests/test_map_render.py`` scans this source and fails if any appears.

The consequence for the picture, which matters
----------------------------------------------
Because visibility is not derivable, the map must not pretend it is. Every cell
starts **unobserved** — dark and hatched — and a cell is lightened only when the
briefing put something in it. That shading is therefore a strict *lower bound*
on what the team can see (a cell holding a visible entity is certainly
observed); it never over-claims, and over-claiming is the only direction that
leaks.

The alternative — drawing empty ground as clean, open board — would make "I
cannot see there" and "there is nothing there" look identical, which for a mind
planning a move is the difference between a safe flank and an ambush. The
legend and footer say so in words as well, because a vision model reads the
caption.

What the briefing gives us (verified against league 0.16-era ``charness.py``)::

    board = {"match_id", "clock", "time_limit", "width", "height", "mode",
             "teams":          [{"id", "name", "resources"}],
             "units":          [{"id", "team_id", "role", "pos", "carrying",
                                 "alive"}],
             "control_points": [{"id", "pos", "owner", "takers"}],
             "missions":       [{"id", "kind", "pos", "amount", "status"}],
             "resource_nodes": [{"id", "pos", "remaining"}]}

``width``/``height`` and every ``pos`` share one integer coordinate space; this
module treats it as opaque board units and never assumes a scale, so it renders
the continuous lane's milliunits and any other integer extent identically.

Zero image dependencies
-----------------------
``tests/test_zero_deps.py`` pins embodiment's approved dependency set, so there
is no Pillow and no matplotlib here. The raster is a palette-index byte buffer
drawn with hand-rolled primitives and encoded as an 8-bit indexed PNG with
``zlib`` and ``struct`` — the same posture league takes in
``league/replay/video.py``, whose ``_Canvas`` is the precedent this follows.

Committing what was measured
----------------------------
:func:`write_turn_map` is the seam for acceptance criterion 3: it drops the PNG
plus a JSON sidecar into a raw results directory, keyed by match/turn/seat and
carrying the :func:`snapshot_hash` of exactly the state that was drawn. Task
t10 pairs an image cell with its text twin on that hash, so the twin must be
rendered from :func:`fog_snapshot` — the identical object this module hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

# --------------------------------------------------------------------------
# palette — one index per meaning, so a test can ask "was rival ink drawn?"
# --------------------------------------------------------------------------

_PALETTE_HEX: tuple[str, ...] = (
    "#ece7db",  # 0  page matte
    "#f8f5ec",  # 1  panel surface (header, legend, footer)
    "#3f4750",  # 2  unobserved plane
    "#2b323a",  # 3  hatch marking on the unobserved plane
    "#dfe8d8",  # 4  observed plane
    "#8d949c",  # 5  grid hairline (legible over both planes)
    "#14181d",  # 6  primary ink
    "#5a626b",  # 7  secondary ink
    "#ffffff",  # 8  ink drawn on top of a filled glyph
    "#8a8378",  # 9  unowned control point
    "#c9861a",  # 10 resource node
    "#1d7f96",  # 11 mission
    "#c0392b",  # 12 alert / rival halo — chrome only, never a team hue
    "#1f7a4d",  # 13 team slot 0 (always the acting team)
    "#2f5fb0",  # 14 team slot 1
    "#7a4fa3",  # 15 team slot 2
    "#a86b1f",  # 16 team slot 3
)

IDX_MATTE = 0
IDX_PANEL = 1
IDX_UNKNOWN = 2
IDX_HATCH = 3
IDX_KNOWN = 4
IDX_GRID = 5
IDX_INK = 6
IDX_INK2 = 7
IDX_GLYPH = 8
IDX_NEUTRAL = 9
IDX_RESOURCE = 10
IDX_MISSION = 11
IDX_ALERT = 12
IDX_TEAM0 = 13

#: How many distinct team hues the palette carries before it wraps.
TEAM_SLOTS = 4


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


PALETTE: tuple[tuple[int, int, int], ...] = tuple(_hex_to_rgb(h) for h in _PALETTE_HEX)


def team_index(slot: int) -> int:
    """The palette index for a team's presentation slot (0 is always 'us')."""
    return IDX_TEAM0 + (slot % TEAM_SLOTS)


#: Everything that is *ground* rather than *content*. A probe of the board that
#: returns only these indices found nothing drawn — which is how the fog-leak
#: fixture asserts a hidden entity never reached the image.
PLANE_INDICES = frozenset({IDX_UNKNOWN, IDX_HATCH, IDX_KNOWN, IDX_GRID})

# --------------------------------------------------------------------------
# a 5x7 bitmap font — enough of ASCII for ids, numbers and captions
# --------------------------------------------------------------------------

FONT_COLS = 5
FONT_ROWS = 7

FONT: dict[str, tuple[str, ...]] = {
    " ": (".....",) * 7,
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#....", "#....", "#..##", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..##.", "....#", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    "-": (".....", ".....", ".....", ".###.", ".....", ".....", "....."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": (".....", ".....", ".....", ".....", ".##..", ".##..", ".#..."),
    ":": (".....", "..#..", "..#..", ".....", "..#..", "..#..", "....."),
    ";": (".....", "..#..", "..#..", ".....", "..#..", "..#..", ".#..."),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
    "[": ("..###", "..#..", "..#..", "..#..", "..#..", "..#..", "..###"),
    "]": ("###..", "..#..", "..#..", "..#..", "..#..", "..#..", "###.."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    "<": ("...#.", "..#..", ".#...", "#....", ".#...", "..#..", "...#."),
    ">": (".#...", "..#..", "...#.", "....#", "...#.", "..#..", ".#..."),
    "*": (".....", "#.#.#", ".###.", "#####", ".###.", "#.#.#", "....."),
    "#": (".#.#.", ".#.#.", "#####", ".#.#.", "#####", ".#.#.", ".#.#."),
    "%": ("##..#", "##.#.", "...#.", "..#..", ".#...", ".#.##", "#..##"),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "'": ("..#..", "..#..", ".....", ".....", ".....", ".....", "....."),
}

# --------------------------------------------------------------------------
# layout constants (pixels)
# --------------------------------------------------------------------------

DEFAULT_BOARD_PX = 700
PAD = 18
HEADER_H = 56
FOOTER_H = 42
GUTTER_L = 40  # room for row coordinate labels
GUTTER_T = 15  # room for column coordinate labels
LEGEND_W = 330
LEGEND_PAD = 14
HATCH_SPACING = 7
MIN_BOARD_PX = 120

#: The half-width of the square a test probes around an entity's mapped pixel.
#: Wide enough to contain any glyph this module draws (so the ground-truth
#: control finds ink), narrow enough that two entities cannot bleed into each
#: other's probe on the committed fixture.
GLYPH_PROBE_PX = 20

#: Aim for at most this many grid cells across the longer axis. The step is
#: then a "nice" 1/2/5 x 10^k value derived from ``width``/``height`` alone —
#: never from a league constant.
MAX_CELLS_ACROSS = 12

UNKNOWN_LEGEND_TEXT = "NOT OBSERVED - UNKNOWN, NOT EMPTY"

LEGEND_ROWS: tuple[tuple[str, str], ...] = (
    ("you", "YOU - THIS DECISION POINT"),
    ("unit", "YOUR UNIT - LETTER IS ITS ROLE"),
    ("rival", "RIVAL UNIT - RED HALO"),
    ("cp", "CONTROL POINT - FILL OWNER, BAR CONTESTED"),
    ("node", "RESOURCE NODE - NUMBER IS REMAINING"),
    ("mission", "MISSION - LETTER IS ITS KIND"),
    ("known", "OBSERVED THIS TURN"),
    ("unknown", UNKNOWN_LEGEND_TEXT),
)

FOOTER_LINES: tuple[str, ...] = (
    "HATCHED GROUND IS NOT OBSERVED: UNKNOWN, NOT EMPTY. ONLY WHAT THE FOGGED "
    "BRIEFING LISTED IS DRAWN HERE.",
    "NOTHING HERE INFERS HOW FAR A UNIT CAN SEE - THE BRIEFING CARRIES NO SUCH "
    "FIGURE - SO A BLANK CELL IS NEVER EVIDENCE OF ABSENCE.",
)

RENDERER_ID = "examples/map_render.py"
RENDERER_VERSION = 1
SIDECAR_SCHEMA = "embodiment.map_render.turn/1"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# --------------------------------------------------------------------------
# raster canvas — palette indices, hand-drawn (league's _Canvas precedent)
# --------------------------------------------------------------------------


class Canvas:
    """A palette-index pixel buffer with just enough primitives."""

    __slots__ = ("width", "height", "buf")

    def __init__(self, width: int, height: int, bg: int = IDX_MATTE) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.buf = bytearray([bg]) * (self.width * self.height)

    def fill_rect(self, x0: int, y0: int, w: int, h: int, color: int) -> None:
        x1, y1 = max(0, x0), max(0, y0)
        x2, y2 = min(self.width, x0 + w), min(self.height, y0 + h)
        if x2 <= x1 or y2 <= y1:
            return
        row = bytes([color]) * (x2 - x1)
        for y in range(y1, y2):
            start = y * self.width + x1
            self.buf[start : start + (x2 - x1)] = row

    def hline(self, x0: int, y: int, length: int, color: int) -> None:
        self.fill_rect(x0, y, length, 1, color)

    def vline(self, x: int, y0: int, length: int, color: int) -> None:
        self.fill_rect(x, y0, 1, length, color)

    def outline_rect(self, x: int, y: int, w: int, h: int, color: int) -> None:
        if w <= 0 or h <= 0:
            return
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)

    def disc(self, cx: int, cy: int, r: int, color: int) -> None:
        r2 = r * r
        for y in range(max(0, cy - r), min(self.height, cy + r + 1)):
            dy2 = (y - cy) ** 2
            row = y * self.width
            for x in range(max(0, cx - r), min(self.width, cx + r + 1)):
                if (x - cx) ** 2 + dy2 <= r2:
                    self.buf[row + x] = color

    def ring(self, cx: int, cy: int, r: int, thickness: int, color: int) -> None:
        outer2 = r * r
        inner = max(0, r - max(1, thickness))
        inner2 = inner * inner
        for y in range(max(0, cy - r), min(self.height, cy + r + 1)):
            dy2 = (y - cy) ** 2
            row = y * self.width
            for x in range(max(0, cx - r), min(self.width, cx + r + 1)):
                d2 = (x - cx) ** 2 + dy2
                if inner2 <= d2 <= outer2:
                    self.buf[row + x] = color

    def diamond(self, cx: int, cy: int, r: int, color: int) -> None:
        for y in range(max(0, cy - r), min(self.height, cy + r + 1)):
            span = r - abs(y - cy)
            if span < 0:
                continue
            self.fill_rect(cx - span, y, 2 * span + 1, 1, color)

    def hatch_rect(self, x0: int, y0: int, w: int, h: int, color: int, spacing: int) -> None:
        """Diagonal dotted marking — the visual word for 'no data here'."""
        step = max(2, spacing)
        x1, y1 = max(0, x0), max(0, y0)
        x2, y2 = min(self.width, x0 + w), min(self.height, y0 + h)
        for y in range(y1, y2):
            row = y * self.width
            first = x1 + ((-(x1 + y)) % step)
            for x in range(first, x2, step):
                self.buf[row + x] = color

    def text(self, x: int, y: int, s: str, color: int, scale: int = 1) -> int:
        cursor = x
        for ch in s.upper():
            rows = FONT.get(ch, FONT[" "])
            for row_i, row in enumerate(rows):
                for col_i, mark in enumerate(row):
                    if mark == "#":
                        self.fill_rect(
                            cursor + col_i * scale, y + row_i * scale, scale, scale, color
                        )
            cursor += (FONT_COLS + 1) * scale
        return cursor

    def text_centered(self, cx: int, y: int, s: str, color: int, scale: int = 1) -> None:
        self.text(cx - text_width(s, scale) // 2, y, s, color, scale)

    def text_right(self, x_end: int, y: int, s: str, color: int, scale: int = 1) -> None:
        self.text(x_end - text_width(s, scale), y, s, color, scale)

    def to_bytes(self) -> bytes:
        return bytes(self.buf)


def text_width(s: str, scale: int = 1) -> int:
    if not s:
        return 0
    return len(s) * (FONT_COLS + 1) * scale - scale


def text_height(scale: int = 1) -> int:
    return FONT_ROWS * scale


def fit_text(s: str, max_px: int, scale: int = 1) -> str:
    """Truncate to fit; a caption that overruns its panel is unreadable."""
    advance = (FONT_COLS + 1) * scale
    limit = max(0, (max_px + scale) // advance)
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "."


# --------------------------------------------------------------------------
# PNG encode / decode — signature + IHDR + PLTE + IDAT + IEND, stdlib only
# --------------------------------------------------------------------------


def _chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(row: bytes, prior: bytes, filter_type: int) -> bytes:
    """Apply one PNG scanline filter at bpp=1 (8-bit indexed)."""
    if filter_type == 0:
        return row
    out = bytearray(len(row))
    for i, value in enumerate(row):
        left = row[i - 1] if i else 0
        up = prior[i]
        upleft = prior[i - 1] if i else 0
        if filter_type == 1:
            out[i] = (value - left) & 0xFF
        elif filter_type == 2:
            out[i] = (value - up) & 0xFF
        elif filter_type == 3:
            out[i] = (value - ((left + up) >> 1)) & 0xFF
        else:
            out[i] = (value - _paeth(left, up, upleft)) & 0xFF
    return bytes(out)


def _unfilter_row(row: bytearray, prior: bytes, filter_type: int) -> bytes:
    if filter_type == 0:
        return bytes(row)
    for i in range(len(row)):
        left = row[i - 1] if i else 0
        up = prior[i]
        upleft = prior[i - 1] if i else 0
        if filter_type == 1:
            row[i] = (row[i] + left) & 0xFF
        elif filter_type == 2:
            row[i] = (row[i] + up) & 0xFF
        elif filter_type == 3:
            row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
        elif filter_type == 4:
            row[i] = (row[i] + _paeth(left, up, upleft)) & 0xFF
        else:
            raise ValueError(f"unknown PNG filter type {filter_type}")
    return bytes(row)


def encode_png(
    width: int,
    height: int,
    palette: Sequence[tuple[int, int, int]],
    indices: bytes,
    *,
    filter_type: int = 0,
) -> bytes:
    """An 8-bit indexed PNG. ``indices`` is ``width * height`` palette bytes."""
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    if len(indices) != width * height:
        raise ValueError(f"expected {width * height} index bytes, got {len(indices)}")
    if not 0 < len(palette) <= 256:
        raise ValueError("an indexed PNG needs between 1 and 256 palette entries")
    header = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(entry) for entry in palette)
    raw = bytearray()
    prior = bytes(width)
    for y in range(height):
        row = indices[y * width : (y + 1) * width]
        raw.append(filter_type)
        raw += _filter_row(row, prior, filter_type)
        prior = row
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"PLTE", plte)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def iter_chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    """Yield ``(name, payload)`` for each chunk; raises on a truncated file."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG: bad signature")
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated PNG: incomplete chunk header")
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        name = data[offset + 4 : offset + 8]
        end = offset + 8 + length
        if end + 4 > len(data):
            raise ValueError(f"truncated PNG: chunk {name!r} is incomplete")
        yield name, data[offset + 8 : end]
        offset = end + 4


@dataclass(frozen=True)
class Raster:
    """A decoded (or freshly drawn) indexed image — the unit of comparison.

    Goldens are compared as rasters rather than as file bytes: zlib's exact
    output is not guaranteed identical across builds, and a golden that failed
    because a distro ships zlib-ng would be a false alarm about the drawing.
    """

    width: int
    height: int
    palette: tuple[tuple[int, int, int], ...]
    indices: bytes

    def index_at(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x}, {y}) is outside {self.width}x{self.height}")
        return self.indices[y * self.width + x]

    def indices_in(self, x0: int, y0: int, w: int, h: int) -> set[int]:
        found: set[int] = set()
        for y in range(max(0, y0), min(self.height, y0 + h)):
            row = y * self.width
            found.update(self.indices[row + max(0, x0) : row + min(self.width, x0 + w)])
        return found

    def count_index(self, index: int, x0: int, y0: int, w: int, h: int) -> int:
        total = 0
        for y in range(max(0, y0), min(self.height, y0 + h)):
            row = y * self.width
            total += self.indices[row + max(0, x0) : row + min(self.width, x0 + w)].count(index)
        return total


def decode_png(data: bytes) -> Raster:
    """Decode an 8-bit indexed PNG (all five scanline filters supported)."""
    header: Optional[bytes] = None
    palette_bytes = b""
    idat = bytearray()
    saw_end = False
    for name, payload in iter_chunks(data):
        if name == b"IHDR":
            header = payload
        elif name == b"PLTE":
            palette_bytes = payload
        elif name == b"IDAT":
            idat += payload
        elif name == b"IEND":
            saw_end = True
    if header is None or len(header) < 13:
        raise ValueError("PNG has no usable IHDR")
    if not saw_end:
        raise ValueError("truncated PNG: no IEND chunk")
    width, height, depth, color_type = struct.unpack(">IIBB", header[:10])
    if (depth, color_type) != (8, 3):
        raise ValueError(f"expected an 8-bit indexed PNG, got depth={depth} type={color_type}")
    palette = tuple(
        (palette_bytes[i], palette_bytes[i + 1], palette_bytes[i + 2])
        for i in range(0, len(palette_bytes) - 2, 3)
    )
    raw = zlib.decompress(bytes(idat))
    if len(raw) != height * (width + 1):
        raise ValueError("truncated PNG: image data is short")
    out = bytearray()
    prior = bytes(width)
    for y in range(height):
        start = y * (width + 1)
        row = bytearray(raw[start + 1 : start + 1 + width])
        prior = _unfilter_row(row, prior, raw[start])
        out += prior
    return Raster(width=width, height=height, palette=palette, indices=bytes(out))


# --------------------------------------------------------------------------
# reading the briefing — total functions, never raising on a bad payload
# --------------------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _pos(value: Any) -> tuple[int, int]:
    point = _mapping(value)
    return _int(point.get("x")), _int(point.get("y"))


@dataclass(frozen=True)
class Entity:
    """One drawable thing the briefing listed. Nothing else is drawable."""

    kind: str
    id: str
    x: int
    y: int
    team_id: str = ""
    label: str = ""
    note: str = ""
    contested: bool = False
    alive: bool = True


@dataclass(frozen=True)
class BoardView:
    """The briefing, normalized to exactly what this renderer draws."""

    match_id: str
    game_time: int
    width: int
    height: int
    teams: tuple[dict[str, Any], ...]
    team_order: tuple[str, ...]
    acting_team: str
    you_unit: str
    you_role: str
    entities: tuple[Entity, ...]

    def team_slot(self, team_id: str) -> int:
        try:
            return self.team_order.index(team_id)
        except ValueError:
            return len(self.team_order)


def _board_of(briefing: Any) -> dict[str, Any]:
    """Accept the whole briefing, or just its ``board`` projection."""
    payload = _mapping(briefing)
    inner = payload.get("board")
    return _mapping(inner) if isinstance(inner, Mapping) else payload


def _team_order(
    board: Mapping[str, Any], units: Sequence[Mapping[str, Any]], acting: str
) -> list[str]:
    order: list[str] = []
    if acting:
        order.append(acting)
    for team in _sequence(board.get("teams")):
        team_id = _text(team.get("id"))
        if team_id and team_id not in order:
            order.append(team_id)
    for unit in units:
        team_id = _text(unit.get("team_id"))
        if team_id and team_id not in order:
            order.append(team_id)
    return order


def read_board(briefing: Any, team_id: Optional[str] = None) -> BoardView:
    """Normalize a briefing into a :class:`BoardView`. Never raises."""
    payload = _mapping(briefing)
    board = _board_of(briefing)
    you = _mapping(payload.get("you"))
    acting = _text(team_id) or _text(you.get("team_id"))
    units = _sequence(board.get("units"))
    order = _team_order(board, units, acting)

    entities: list[Entity] = []
    for unit in units:
        x, y = _pos(unit.get("pos"))
        role = _text(unit.get("role"))
        carrying = _int(unit.get("carrying"))
        entities.append(
            Entity(
                kind="unit",
                id=_text(unit.get("id")),
                x=x,
                y=y,
                team_id=_text(unit.get("team_id")),
                label=(role[:1] or "?").upper(),
                note=str(carrying) if carrying else "",
                alive=unit.get("alive") is not False,
            )
        )
    for point in _sequence(board.get("control_points")):
        x, y = _pos(point.get("pos"))
        entities.append(
            Entity(
                kind="control_point",
                id=_text(point.get("id")),
                x=x,
                y=y,
                team_id=_text(point.get("owner")),
                contested=bool(_sequence(point.get("takers"))),
            )
        )
    for node in _sequence(board.get("resource_nodes")):
        x, y = _pos(node.get("pos"))
        entities.append(
            Entity(
                kind="resource_node",
                id=_text(node.get("id")),
                x=x,
                y=y,
                note=str(_int(node.get("remaining"))),
            )
        )
    for mission in _sequence(board.get("missions")):
        x, y = _pos(mission.get("pos"))
        kind = _text(mission.get("kind"))
        status = _text(mission.get("status"), "open")
        entities.append(
            Entity(
                kind="mission",
                id=_text(mission.get("id")),
                x=x,
                y=y,
                label=(kind[:1] or "?").upper(),
                note=str(_int(mission.get("amount"))),
                alive=status != "completed",
            )
        )

    teams = tuple(
        {
            "id": _text(team.get("id")),
            "name": _text(team.get("name")) or _text(team.get("id")),
            "resources": _int(team.get("resources")),
        }
        for team in _sequence(board.get("teams"))
    )
    return BoardView(
        match_id=_text(board.get("match_id")),
        game_time=_int(payload.get("game_time"), _int(board.get("clock"))),
        width=max(1, _int(board.get("width"), 1)),
        height=max(1, _int(board.get("height"), 1)),
        teams=teams,
        team_order=tuple(order),
        acting_team=acting,
        you_unit=_text(you.get("unit_id")),
        you_role=_text(you.get("role")),
        entities=tuple(entities),
    )


# --------------------------------------------------------------------------
# layout — board-unit coordinates to pixels
# --------------------------------------------------------------------------


def nice_step(extent: int) -> int:
    """A 1/2/5 x 10^k grid step giving at most :data:`MAX_CELLS_ACROSS` cells.

    Derived from the board extent the briefing states and nothing else — there
    is no league constant, and no relationship to what anything can see.
    """
    ladder = (1, 2, 5)
    step = 1
    rung = 0
    while extent // step > MAX_CELLS_ACROSS:
        rung += 1
        step = ladder[rung % 3] * 10 ** (rung // 3)
    return step


@dataclass(frozen=True)
class Layout:
    """Where the board sits in the image, and how a position maps into it."""

    image_w: int
    image_h: int
    board_x: int
    board_y: int
    board_w: int
    board_h: int
    extent_w: int
    extent_h: int
    step: int
    legend_x: int

    @property
    def ncols(self) -> int:
        return max(1, -(-self.extent_w // self.step))

    @property
    def nrows(self) -> int:
        return max(1, -(-self.extent_h // self.step))

    def board_rect(self) -> tuple[int, int, int, int]:
        return self.board_x, self.board_y, self.board_w, self.board_h

    def edge_x(self, value: int) -> int:
        value = min(max(value, 0), self.extent_w)
        return self.board_x + value * (self.board_w - 1) // self.extent_w

    def edge_y(self, value: int) -> int:
        value = min(max(value, 0), self.extent_h)
        return self.board_y + value * (self.board_h - 1) // self.extent_h

    def to_pixel(self, x: int, y: int) -> tuple[int, int]:
        return self.edge_x(_int(x)), self.edge_y(_int(y))

    def cell_rect(self, col: int, row: int) -> tuple[int, int, int, int]:
        x0 = self.edge_x(col * self.step)
        x1 = self.edge_x((col + 1) * self.step)
        y0 = self.edge_y(row * self.step)
        y1 = self.edge_y((row + 1) * self.step)
        if col >= self.ncols - 1:
            x1 = self.board_x + self.board_w
        if row >= self.nrows - 1:
            y1 = self.board_y + self.board_h
        return x0, y0, max(1, x1 - x0), max(1, y1 - y0)

    def cell_of(self, x: int, y: int) -> tuple[int, int]:
        col = min(self.ncols - 1, max(0, min(max(_int(x), 0), self.extent_w) // self.step))
        row = min(self.nrows - 1, max(0, min(max(_int(y), 0), self.extent_h) // self.step))
        return col, row

    def cell_rect_at(self, x: int, y: int) -> tuple[int, int, int, int]:
        return self.cell_rect(*self.cell_of(x, y))


def build_layout(view: BoardView, board_px: int = DEFAULT_BOARD_PX) -> Layout:
    board_px = max(MIN_BOARD_PX, int(board_px))
    extent_w, extent_h = max(1, view.width), max(1, view.height)
    if extent_w >= extent_h:
        board_w = board_px
        board_h = max(MIN_BOARD_PX, board_px * extent_h // extent_w)
    else:
        board_h = board_px
        board_w = max(MIN_BOARD_PX, board_px * extent_w // extent_h)
    board_x = PAD + GUTTER_L
    board_y = PAD + HEADER_H + GUTTER_T
    legend_x = board_x + board_w + PAD
    return Layout(
        image_w=legend_x + LEGEND_W + PAD,
        image_h=board_y + board_h + FOOTER_H + PAD,
        board_x=board_x,
        board_y=board_y,
        board_w=board_w,
        board_h=board_h,
        extent_w=extent_w,
        extent_h=extent_h,
        step=nice_step(max(extent_w, extent_h)),
        legend_x=legend_x,
    )


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------


def _glyph_radius(layout: Layout) -> int:
    return max(5, min(layout.board_w, layout.board_h) // 30)


def _draw_plane(canvas: Canvas, layout: Layout, view: BoardView) -> None:
    """Unobserved everywhere, then lightened only where something was listed.

    The lightening is a lower bound on visibility — a cell holding a visible
    entity is certainly observed — and a lower bound is the safe direction: it
    can understate what the team saw, never overstate it.
    """
    canvas.fill_rect(*layout.board_rect(), IDX_UNKNOWN)
    canvas.hatch_rect(*layout.board_rect(), IDX_HATCH, HATCH_SPACING)
    observed = sorted({layout.cell_of(e.x, e.y) for e in view.entities})
    for col, row in observed:
        canvas.fill_rect(*layout.cell_rect(col, row), IDX_KNOWN)
    for col in range(layout.ncols + 1):
        x = layout.edge_x(min(col * layout.step, layout.extent_w))
        canvas.vline(x, layout.board_y, layout.board_h, IDX_GRID)
    for row in range(layout.nrows + 1):
        y = layout.edge_y(min(row * layout.step, layout.extent_h))
        canvas.hline(layout.board_x, y, layout.board_w, IDX_GRID)
    canvas.outline_rect(
        layout.board_x - 1, layout.board_y - 1, layout.board_w + 2, layout.board_h + 2, IDX_INK
    )


def _draw_axes(canvas: Canvas, layout: Layout) -> None:
    for col in range(layout.ncols + 1):
        value = min(col * layout.step, layout.extent_w)
        x = layout.edge_x(value)
        canvas.text_centered(x, layout.board_y - GUTTER_T + 1, str(value), IDX_INK2)
    for row in range(layout.nrows + 1):
        value = min(row * layout.step, layout.extent_h)
        y = layout.edge_y(value)
        canvas.text_right(layout.board_x - 5, y - text_height() // 2, str(value), IDX_INK2)


def _draw_unit(canvas: Canvas, layout: Layout, entity: Entity, view: BoardView) -> None:
    cx, cy = layout.to_pixel(entity.x, entity.y)
    radius = _glyph_radius(layout)
    slot = view.team_slot(entity.team_id)
    canvas.disc(cx, cy, radius, team_index(slot))
    canvas.ring(cx, cy, radius, 2, IDX_GLYPH)
    if entity.team_id and entity.team_id != view.acting_team:
        canvas.ring(cx, cy, radius + 4, 2, IDX_ALERT)
    if not entity.alive:
        canvas.hline(cx - radius, cy, 2 * radius + 1, IDX_INK)
    canvas.text_centered(cx + 1, cy - text_height() // 2, entity.label, IDX_GLYPH)
    if entity.id and entity.id == view.you_unit:
        canvas.ring(cx, cy, radius + 4, 2, IDX_INK)
    if entity.note:
        canvas.text_centered(cx, cy + radius + 3, entity.note, IDX_INK)


def _draw_control_point(canvas: Canvas, layout: Layout, entity: Entity, view: BoardView) -> None:
    cx, cy = layout.to_pixel(entity.x, entity.y)
    radius = _glyph_radius(layout)
    side = 2 * radius
    fill = team_index(view.team_slot(entity.team_id)) if entity.team_id else IDX_NEUTRAL
    canvas.fill_rect(cx - radius, cy - radius, side, side, fill)
    canvas.outline_rect(cx - radius, cy - radius, side, side, IDX_INK)
    canvas.text_centered(cx, cy - text_height() // 2, "P", IDX_GLYPH)
    if entity.contested:
        canvas.fill_rect(cx - radius, cy + radius + 1, side, 3, IDX_ALERT)


def _draw_resource_node(canvas: Canvas, layout: Layout, entity: Entity) -> None:
    cx, cy = layout.to_pixel(entity.x, entity.y)
    radius = _glyph_radius(layout)
    canvas.diamond(cx, cy, radius, IDX_RESOURCE)
    canvas.text_centered(cx, cy + radius + 3, entity.note, IDX_INK)


def _draw_mission(canvas: Canvas, layout: Layout, entity: Entity) -> None:
    cx, cy = layout.to_pixel(entity.x, entity.y)
    radius = _glyph_radius(layout)
    canvas.ring(cx, cy, radius, 3, IDX_MISSION)
    canvas.disc(cx, cy, max(2, radius // 3), IDX_MISSION if entity.alive else IDX_INK2)
    canvas.text_centered(cx, cy + radius + 3, entity.label, IDX_MISSION)


def _draw_entities(canvas: Canvas, layout: Layout, view: BoardView) -> None:
    for entity in view.entities:
        if entity.kind == "resource_node":
            _draw_resource_node(canvas, layout, entity)
    for entity in view.entities:
        if entity.kind == "mission":
            _draw_mission(canvas, layout, entity)
    for entity in view.entities:
        if entity.kind == "control_point":
            _draw_control_point(canvas, layout, entity, view)
    for entity in view.entities:
        if entity.kind == "unit":
            _draw_unit(canvas, layout, entity, view)


def _team_name(view: BoardView, team_id: str) -> str:
    for team in view.teams:
        if team["id"] == team_id:
            return team["name"] or team_id
    return team_id or "UNNAMED"


def _draw_header(canvas: Canvas, layout: Layout, view: BoardView) -> None:
    canvas.fill_rect(PAD, PAD, layout.image_w - 2 * PAD, HEADER_H - 6, IDX_PANEL)
    title = f"FOG MAP - {_team_name(view, view.acting_team)}"
    canvas.text(PAD + 10, PAD + 8, fit_text(title, layout.image_w - 2 * PAD - 20, 2), IDX_INK, 2)
    seat = f"{view.you_unit} ({view.you_role})" if view.you_unit else "TEAM VIEW"
    subtitle = (
        f"MATCH {view.match_id or '-'} - TIME {view.game_time} - SEAT {seat} - "
        f"BOARD {view.width} X {view.height} - GRID {layout.step}"
    )
    canvas.text(PAD + 10, PAD + 32, fit_text(subtitle, layout.image_w - 2 * PAD - 20), IDX_INK2)


def _draw_footer(canvas: Canvas, layout: Layout) -> None:
    top = layout.board_y + layout.board_h + 8
    canvas.fill_rect(PAD, top, layout.image_w - 2 * PAD, FOOTER_H - 10, IDX_PANEL)
    for i, line in enumerate(FOOTER_LINES):
        canvas.text(
            PAD + 10,
            top + 5 + i * (text_height() + 4),
            fit_text(line, layout.image_w - 2 * PAD - 20),
            IDX_INK,
        )


def _legend_swatch(canvas: Canvas, x: int, y: int, kind: str, view: BoardView) -> None:
    side = 18
    cx, cy = x + side // 2, y + side // 2
    if kind in ("unit", "you", "rival"):
        slot = 0 if kind != "rival" else 1
        canvas.disc(cx, cy, side // 2, team_index(slot))
        canvas.ring(cx, cy, side // 2, 2, IDX_GLYPH)
        if kind == "rival":
            canvas.ring(cx, cy, side // 2, 1, IDX_ALERT)
        if kind == "you":
            canvas.ring(cx, cy, side // 2, 1, IDX_INK)
    elif kind == "cp":
        owner = team_index(0) if view.acting_team else IDX_NEUTRAL
        canvas.fill_rect(x, y, side, side, owner)
        canvas.outline_rect(x, y, side, side, IDX_INK)
    elif kind == "node":
        canvas.diamond(cx, cy, side // 2, IDX_RESOURCE)
    elif kind == "mission":
        canvas.ring(cx, cy, side // 2, 3, IDX_MISSION)
    elif kind == "known":
        canvas.fill_rect(x, y, side, side, IDX_KNOWN)
        canvas.outline_rect(x, y, side, side, IDX_GRID)
    else:
        canvas.fill_rect(x, y, side, side, IDX_UNKNOWN)
        canvas.hatch_rect(x, y, side, side, IDX_HATCH, HATCH_SPACING)
        canvas.outline_rect(x, y, side, side, IDX_GRID)


def _draw_legend(canvas: Canvas, layout: Layout, view: BoardView) -> None:
    x = layout.legend_x
    y = layout.board_y - GUTTER_T
    width = LEGEND_W
    height = layout.board_h + GUTTER_T
    canvas.fill_rect(x, y, width, height, IDX_PANEL)
    canvas.outline_rect(x, y, width, height, IDX_GRID)

    text_x = x + LEGEND_PAD + 18 + 8
    text_px = width - (text_x - x) - LEGEND_PAD
    cursor = y + LEGEND_PAD
    canvas.text(x + LEGEND_PAD, cursor, "LEGEND", IDX_INK)
    cursor += text_height() + 8
    for kind, caption in LEGEND_ROWS:
        _legend_swatch(canvas, x + LEGEND_PAD, cursor, kind, view)
        canvas.text(text_x, cursor + 5, fit_text(caption, text_px), IDX_INK)
        cursor += 24

    cursor += 8
    canvas.hline(x + LEGEND_PAD, cursor, width - 2 * LEGEND_PAD, IDX_GRID)
    cursor += 10
    canvas.text(x + LEGEND_PAD, cursor, "TEAMS", IDX_INK)
    cursor += text_height() + 8
    for slot, team_id in enumerate(view.team_order):
        canvas.fill_rect(x + LEGEND_PAD, cursor, 18, 18, team_index(slot))
        canvas.outline_rect(x + LEGEND_PAD, cursor, 18, 18, IDX_INK)
        name = _team_name(view, team_id)
        suffix = " (YOU)" if team_id == view.acting_team else ""
        resources = next((t["resources"] for t in view.teams if t["id"] == team_id), 0)
        canvas.text(
            text_x, cursor + 5, fit_text(f"{name}{suffix} - RES {resources}", text_px), IDX_INK
        )
        cursor += 24

    cursor += 8
    canvas.hline(x + LEGEND_PAD, cursor, width - 2 * LEGEND_PAD, IDX_GRID)
    cursor += 10
    for line in ("DRAWN FROM THE BRIEFING AS GIVEN.", "NOTHING HERE IS INFERRED."):
        canvas.text(x + LEGEND_PAD, cursor, fit_text(line, width - 2 * LEGEND_PAD), IDX_INK2)
        cursor += text_height() + 4


# --------------------------------------------------------------------------
# the public render seam
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MapRender:
    """A rendered map plus everything a test or a harness needs to check it."""

    png: bytes
    raster: Raster
    layout: Layout
    view: BoardView
    team_id: str
    team_order: tuple[str, ...]
    snapshot_hash: str


def render_map(
    briefing: Any,
    team_id: Optional[str] = None,
    board_px: int = DEFAULT_BOARD_PX,
) -> MapRender:
    """Render one team's fog map from the fogged briefing it was handed.

    ``team_id`` only chooses *whose* map this is presented as (which team takes
    the first palette slot, whose seat the header names). It never filters:
    filtering already happened in league, and this module has no means to redo
    it. Passing a different team to the same fogged briefing therefore recolours
    the picture; it cannot reveal anything the briefing withheld.
    """
    view = read_board(briefing, team_id)
    layout = build_layout(view, board_px)
    canvas = Canvas(layout.image_w, layout.image_h, IDX_MATTE)
    _draw_header(canvas, layout, view)
    _draw_plane(canvas, layout, view)
    _draw_axes(canvas, layout)
    _draw_entities(canvas, layout, view)
    _draw_legend(canvas, layout, view)
    _draw_footer(canvas, layout)
    indices = canvas.to_bytes()
    return MapRender(
        png=encode_png(layout.image_w, layout.image_h, PALETTE, indices),
        raster=Raster(layout.image_w, layout.image_h, PALETTE, indices),
        layout=layout,
        view=view,
        team_id=view.acting_team,
        team_order=view.team_order,
        snapshot_hash=_hash_snapshot(_snapshot_of(view)),
    )


def render_png(briefing: Any, **kwargs: Any) -> bytes:
    """:func:`render_map`, when only the bytes are wanted."""
    return render_map(briefing, **kwargs).png


# --------------------------------------------------------------------------
# the fog snapshot — what was drawn, hashed, so a text twin can match it
# --------------------------------------------------------------------------


def _snapshot_of(view: BoardView) -> dict[str, Any]:
    return {
        "game_time": view.game_time,
        "team_id": view.acting_team,
        "board": {
            "match_id": view.match_id,
            "width": view.width,
            "height": view.height,
            "teams": [dict(team) for team in view.teams],
            "entities": [
                {
                    "kind": e.kind,
                    "id": e.id,
                    "pos": {"x": e.x, "y": e.y},
                    "team_id": e.team_id,
                    "label": e.label,
                    "note": e.note,
                    "contested": e.contested,
                    "alive": e.alive,
                }
                for e in view.entities
            ],
        },
    }


def _hash_snapshot(snapshot: Mapping[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fog_snapshot(briefing: Any, team_id: Optional[str] = None) -> dict[str, Any]:
    """Exactly the state the map draws — the object a text twin must describe.

    Deliberately narrower than the briefing: ``menu``, ``outlook`` and
    ``messages`` are not on the map, so including them in the twin's identity
    would make two cells that rendered identical pictures look unpaired.
    """
    return _snapshot_of(read_board(briefing, team_id))


def snapshot_hash(briefing: Any, team_id: Optional[str] = None) -> str:
    """A stable digest of :func:`fog_snapshot` — the image/text twin key."""
    return _hash_snapshot(fog_snapshot(briefing, team_id))


# --------------------------------------------------------------------------
# the commit seam — a measured turn's map, in the raw results dir
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MapArtifact:
    """What landed on disk for one measured turn."""

    png_path: Path
    meta_path: Path
    snapshot_hash: str
    png_sha256: str
    width: int
    height: int


def _safe(value: Any, fallback: str) -> str:
    text = "".join(c if (c.isalnum() or c in "_-") else "-" for c in str(value))
    while "--" in text:
        text = text.replace("--", "-")
    text = text.strip("-")
    return text or fallback


def write_turn_map(
    briefing: Any,
    out_dir: Any,
    *,
    match_id: Optional[str] = None,
    turn: Optional[int] = None,
    seat: Optional[str] = None,
    team_id: Optional[str] = None,
    board_px: int = DEFAULT_BOARD_PX,
) -> MapArtifact:
    """Render one decision point's map into a raw results directory.

    This is acceptance criterion 3's seam: a harness calls it once per measured
    turn and the directory becomes re-inspectable evidence — every image beside
    the hash of the state it was drawn from, so a later reader can check the map
    against the text twin rather than trust a description of it.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    render = render_map(briefing, team_id=team_id, board_px=board_px)
    match = _safe(match_id if match_id is not None else render.view.match_id, "match")
    turn_value = render.view.game_time if turn is None else _int(turn)
    stem = f"{match}-t{max(0, turn_value):04d}"
    if seat is not None:
        stem = f"{stem}-{_safe(seat, 'seat')}"
    png_path = directory / f"{stem}.png"
    meta_path = directory / f"{stem}.json"
    png_path.write_bytes(render.png)
    digest = hashlib.sha256(render.png).hexdigest()
    meta = {
        "schema": SIDECAR_SCHEMA,
        "renderer": RENDERER_ID,
        "renderer_version": RENDERER_VERSION,
        "match_id": match_id if match_id is not None else render.view.match_id,
        "turn": turn_value,
        "seat": seat,
        "team_id": render.team_id,
        "snapshot_hash": render.snapshot_hash,
        "png": png_path.name,
        "png_sha256": digest,
        "width": render.raster.width,
        "height": render.raster.height,
        "fog_note": (
            "team-scoped view drawn from the fogged briefing alone; no visibility "
            "was recomputed and no radius is known to this renderer"
        ),
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return MapArtifact(
        png_path=png_path,
        meta_path=meta_path,
        snapshot_hash=render.snapshot_hash,
        png_sha256=digest,
        width=render.raster.width,
        height=render.raster.height,
    )


# --------------------------------------------------------------------------
# the adversarial kit — fixtures the tests and the goldens both come from
# --------------------------------------------------------------------------

_BOARD_W = 24000
_BOARD_H = 16000


@dataclass(frozen=True)
class FogLeakFixture:
    """A fogged briefing, its fogless twin, and what the fog removed.

    The point of shipping both halves is the vacuity guard: asserting the
    hidden entities are absent from the fogged image proves nothing unless the
    same assertion *fails* on the fogless one. A renderer that drew nothing at
    all would otherwise pass every fog test perfectly.
    """

    fogged: dict[str, Any]
    ground_truth: dict[str, Any]
    hidden: tuple[dict[str, Any], ...]


def _teams() -> list[dict[str, Any]]:
    return [
        {"id": "blue", "name": "Blue Watch", "resources": 7},
        {"id": "red", "name": "Red Company", "resources": 4},
    ]


def _seen_briefing() -> dict[str, Any]:
    """What blue's seat actually receives at this decision point."""
    return {
        "game_time": 42,
        "you": {
            "unit_id": "blue-1",
            "team_id": "blue",
            "role": "scout",
            "pos": {"x": 3500, "y": 5000},
            "carrying": 0,
        },
        "board": {
            "match_id": "cm-fog-1",
            "clock": 42,
            "width": _BOARD_W,
            "height": _BOARD_H,
            "teams": _teams(),
            "units": [
                {
                    "id": "blue-1",
                    "team_id": "blue",
                    "role": "scout",
                    "pos": {"x": 3500, "y": 5000},
                    "carrying": 0,
                    "alive": True,
                },
                {
                    "id": "blue-2",
                    "team_id": "blue",
                    "role": "harvester",
                    "pos": {"x": 7000, "y": 9000},
                    "carrying": 2,
                    "alive": True,
                },
            ],
            "control_points": [
                {"id": "cp-west", "pos": {"x": 5000, "y": 6500}, "owner": "blue", "takers": []}
            ],
            "resource_nodes": [{"id": "rn-1", "pos": {"x": 6500, "y": 11000}, "remaining": 12}],
            "missions": [
                {
                    "id": "m-1",
                    "kind": "deliver",
                    "pos": {"x": 3000, "y": 3000},
                    "amount": 5,
                    "status": "open",
                }
            ],
        },
    }


#: Everything the fog removed — all of it in the board's eastern third, far
#: enough from any visible entity that a probe around each can be unambiguous.
_HIDDEN: tuple[dict[str, Any], ...] = (
    {"kind": "unit", "id": "red-1", "pos": {"x": 20000, "y": 13000}},
    {"kind": "control_point", "id": "cp-east", "pos": {"x": 21000, "y": 3500}},
    {"kind": "resource_node", "id": "rn-2", "pos": {"x": 19000, "y": 8000}},
    {"kind": "mission", "id": "m-2", "pos": {"x": 22000, "y": 15000}},
)


def _ground_truth_briefing() -> dict[str, Any]:
    briefing = _seen_briefing()
    board = briefing["board"]
    board["units"].append(
        {
            "id": "red-1",
            "team_id": "red",
            "role": "defender",
            "pos": {"x": 20000, "y": 13000},
            "carrying": 0,
            "alive": True,
        }
    )
    board["control_points"].append(
        {
            "id": "cp-east",
            "pos": {"x": 21000, "y": 3500},
            "owner": "red",
            "takers": [{"unit_id": "red-1", "team_id": "red"}],
        }
    )
    board["resource_nodes"].append({"id": "rn-2", "pos": {"x": 19000, "y": 8000}, "remaining": 30})
    board["missions"].append(
        {
            "id": "m-2",
            "kind": "hold",
            "pos": {"x": 22000, "y": 15000},
            "amount": 1,
            "status": "open",
        }
    )
    return briefing


def fog_leak_fixture() -> FogLeakFixture:
    """The adversarial fixture: an entity outside visibility must not be drawn."""
    return FogLeakFixture(
        fogged=_seen_briefing(),
        ground_truth=_ground_truth_briefing(),
        hidden=_HIDDEN,
    )


def _blind_spot_briefing() -> dict[str, Any]:
    """One unit and an otherwise unknown board — unknown is not empty."""
    briefing = _seen_briefing()
    board = briefing["board"]
    board["match_id"] = "cm-fog-blind"
    board["units"] = board["units"][:1]
    board["control_points"] = []
    board["resource_nodes"] = []
    board["missions"] = []
    return briefing


#: The committed golden set, in the order a reviewer should read it.
GOLDEN_NAMES: tuple[str, ...] = ("fog_scoped", "ground_truth", "blind_spot")


def demo_briefings() -> dict[str, dict[str, Any]]:
    """Every briefing that has a committed golden image."""
    fixture = fog_leak_fixture()
    return {
        "fog_scoped": fixture.fogged,
        "ground_truth": fixture.ground_truth,
        "blind_spot": _blind_spot_briefing(),
    }


def write_goldens(out_dir: Any) -> list[Path]:
    """(Re)generate the committed golden images. Review the diff by eye."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in GOLDEN_NAMES:
        path = directory / f"{name}.png"
        path.write_bytes(render_png(demo_briefings()[name]))
        written.append(path)
    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_briefing(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 1


def _cmd_render(args: argparse.Namespace) -> int:
    try:
        briefing = _load_briefing(args.briefing)
    except (OSError, ValueError) as err:
        return _fail(f"could not read briefing {args.briefing}: {err}", "pass a fogged briefing")
    render = render_map(briefing, team_id=args.team, board_px=args.board_px)
    Path(args.out).write_bytes(render.png)
    payload = {
        "png": str(Path(args.out).resolve()),
        "width": render.raster.width,
        "height": render.raster.height,
        "team_id": render.team_id,
        "snapshot_hash": render.snapshot_hash,
    }
    print(json.dumps(payload, indent=2) if args.json else f"wrote {payload['png']}")
    return 0


def _cmd_turn(args: argparse.Namespace) -> int:
    try:
        briefing = _load_briefing(args.briefing)
    except (OSError, ValueError) as err:
        return _fail(f"could not read briefing {args.briefing}: {err}", "pass a fogged briefing")
    artifact = write_turn_map(
        briefing,
        args.out_dir,
        match_id=args.match,
        turn=args.turn,
        seat=args.seat,
        team_id=args.team,
        board_px=args.board_px,
    )
    payload = {
        "png": str(artifact.png_path.resolve()),
        "meta": str(artifact.meta_path.resolve()),
        "snapshot_hash": artifact.snapshot_hash,
        "png_sha256": artifact.png_sha256,
    }
    print(json.dumps(payload, indent=2) if args.json else f"wrote {payload['png']}")
    return 0


def _cmd_goldens(args: argparse.Namespace) -> int:
    written = write_goldens(args.out)
    if args.json:
        print(json.dumps({"goldens": [str(p.resolve()) for p in written]}, indent=2))
    else:
        for path in written:
            print(f"wrote {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="map_render",
        description="Render a team-scoped fog map from a fogged league briefing.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render one briefing to a PNG")
    render.add_argument("--json", action="store_true", help="machine-readable output")
    render.add_argument("--briefing", required=True, help="path to a fogged briefing JSON")
    render.add_argument("--out", required=True, help="path to write the PNG to")
    render.add_argument("--team", default=None, help="acting team id (default: briefing's you)")
    render.add_argument("--board-px", type=int, default=DEFAULT_BOARD_PX)
    render.set_defaults(func=_cmd_render)

    turn = sub.add_parser("turn", help="render one measured turn into a raw results dir")
    turn.add_argument("--json", action="store_true", help="machine-readable output")
    turn.add_argument("--briefing", required=True)
    turn.add_argument("--out-dir", required=True)
    turn.add_argument("--match", default=None)
    turn.add_argument("--turn", type=int, default=None)
    turn.add_argument("--seat", default=None)
    turn.add_argument("--team", default=None)
    turn.add_argument("--board-px", type=int, default=DEFAULT_BOARD_PX)
    turn.set_defaults(func=_cmd_turn)

    goldens = sub.add_parser("goldens", help="regenerate the committed golden images")
    goldens.add_argument("--json", action="store_true", help="machine-readable output")
    goldens.add_argument("--out", default="tests/goldens/maps")
    goldens.set_defaults(func=_cmd_goldens)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
