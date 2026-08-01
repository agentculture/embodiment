"""Tests for the team-scoped fog map renderer (plan task t9).

The renderer is an **instrument**, and this cycle's spec puts instruments under
the M2 grader-kit discipline: *a wrong map fed to a mind is a defective
instrument*. So this file is written as an adversarial kit, not a smoke test —
every acceptance criterion has a named test, and the fog-leak assertion carries
a vacuity guard proving it can actually fail.

Acceptance criteria (from the plan's t9 entry), and where each is proved:

1. **The renderer consumes only the fogged briefing JSON, and no vision-radius
   constant exists anywhere in its code.** Duplicating league's vision stats
   would silently desync the moment league changed one, producing exactly the
   fog leak criterion 2 forbids.
   → :class:`TestNoVisionRadiusAnywhere` (a scan of the renderer's own source
   plus an AST walk over its numeric literals) and
   :class:`TestConsumesOnlyTheFoggedBriefing` (smuggled ground-truth keys —
   including a ``vision_mu`` field bolted onto a unit — change nothing).

2. **An adversarial fog-leak fixture must fail the suite if the hidden entity
   is ever drawn; goldens are committed.**
   → :class:`TestFogLeak` (global colour check, positional check, and the
   ``ground_truth`` vacuity guard that fails if the assertions are toothless)
   and :class:`TestGoldens`.

3. **Every measured turn's rendered map can be committed to a raw results
   directory** — the seam, not the measured turns.
   → :class:`TestCommitSeam`.

Plus the properties that make the image worth showing a model at all:
:class:`TestUnknownIsNotEmpty` (the criterion that "I cannot see there" and
"there is nothing there" must not look identical) and :class:`TestPngIsValid`
(the hand-rolled encoder really does emit a PNG).
"""

from __future__ import annotations

import ast
import copy
import json
import re
import zlib
from pathlib import Path
from typing import Any

import pytest

from examples import map_render

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens" / "maps"

#: The milliunit vision radii league's continuous roles actually carry
#: (``league/engine/continuous/roles.py``: ``vision_mu`` 6000/4000/2000) and the
#: grid lane's cell radii (``league/engine/scenario.py``: ``vision`` 6/4/2).
#: The milliunit values are what a copy-paste of league's stats would look
#: like; the small cell radii are excluded from the literal scan because 2, 4
#: and 6 are unavoidable pixel constants in any renderer — the vocabulary scan
#: and the behavioural smuggling tests cover that direction instead.
LEAGUE_VISION_MILLIUNITS = (2000, 4000, 6000)

#: Words a vision computation cannot be written without. Any of these appearing
#: in the renderer's source is the tell that the fog rule was duplicated here.
#: The scan is a blunt substring match on purpose — a word-boundary regex would
#: let ``_vision_mu`` through, and a fog leak is not worth the elegance. The
#: cost of bluntness is a false positive on innocent words that *contain* one of
#: these ("division" ends in "vision"); the renderer renames around them rather
#: than loosening the scan.
VISION_VOCABULARY = (
    "vision",
    "sight",
    "eyesight",
    "fov",
    "line_of_sight",
    "visible_cells",
    "dist_sq",
    "vision_mu",
)


def _source() -> str:
    return Path(map_render.__file__).read_text(encoding="utf-8")


def _render(briefing: dict[str, Any], **kwargs: Any) -> map_render.MapRender:
    return map_render.render_map(briefing, **kwargs)


class TestNoVisionRadiusAnywhere:
    """Criterion 1 — the renderer must not know how far anything can see."""

    def test_no_vision_vocabulary_appears_in_the_source(self) -> None:
        """A vision computation cannot be written without naming one of these.

        The prose in the module docstring *does* discuss fog, so the scan runs
        over code only: the docstring is stripped before matching, which keeps
        the module free to explain why it refuses to compute visibility.
        """
        tree = ast.parse(_source())
        body = tree.body[1:] if ast.get_docstring(tree) else tree.body
        code = "\n".join(ast.unparse(node) for node in body).lower()
        offenders = [word for word in VISION_VOCABULARY if word in code]
        assert not offenders, (
            f"{Path(map_render.__file__).name} names {offenders} in code — the fogged "
            "briefing carries no radii, so any vision vocabulary here means the fog "
            "rule was duplicated and will desync from league"
        )

    def test_no_league_vision_radius_literal_appears_in_the_source(self) -> None:
        """An AST walk, not a substring scan: a copied radius is a *number*."""
        literals = {
            node.value
            for node in ast.walk(ast.parse(_source()))
            if isinstance(node, ast.Constant) and isinstance(node.value, int)
        }
        copied = sorted(literals & set(LEAGUE_VISION_MILLIUNITS))
        assert not copied, (
            f"league's vision radii {copied} appear as literals in the renderer; "
            "a stats change in league would silently desync a duplicated fog rule"
        )

    def test_the_renderer_imports_nothing_from_league(self) -> None:
        """No league import means no back door to the radii either."""
        tree = ast.parse(_source())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported if name.split(".")[0] == "league"]

    def test_render_map_takes_no_radius_or_role_table_argument(self) -> None:
        """The seam itself refuses the input a fog computation would need."""
        import inspect

        params = set(inspect.signature(map_render.render_map).parameters)
        assert params == {"briefing", "team_id", "board_px"}, params

    def test_the_scan_would_catch_a_planted_radius(self) -> None:
        """Vacuity guard: prove the two scans above can fail.

        A test that greps its own source is worthless if the grep is wrong, so
        run both scans over a deliberately offending snippet and require them
        to fire.
        """
        planted = "def sees(unit, pos):\n    return dist_sq(unit, pos) <= 4000 * 4000\n"
        code = "\n".join(ast.unparse(n) for n in ast.parse(planted).body).lower()
        assert [w for w in VISION_VOCABULARY if w in code]
        literals = {
            node.value
            for node in ast.walk(ast.parse(planted))
            if isinstance(node, ast.Constant) and isinstance(node.value, int)
        }
        assert literals & set(LEAGUE_VISION_MILLIUNITS)


class TestConsumesOnlyTheFoggedBriefing:
    """Criterion 1, behaviourally — extra state in the payload changes nothing."""

    def test_smuggled_top_level_ground_truth_is_ignored(self) -> None:
        fixture = map_render.fog_leak_fixture()
        clean = map_render.render_png(fixture.fogged)
        smuggled = dict(fixture.fogged)
        smuggled["_ground_truth"] = fixture.ground_truth["board"]
        smuggled["hidden_units"] = [dict(e) for e in fixture.hidden]
        assert map_render.render_png(smuggled) == clean

    def test_smuggled_board_level_ground_truth_is_ignored(self) -> None:
        fixture = map_render.fog_leak_fixture()
        clean = map_render.render_png(fixture.fogged)
        smuggled = json.loads(json.dumps(fixture.fogged))
        smuggled["board"]["all_units"] = fixture.ground_truth["board"]["units"]
        smuggled["board"]["fogless_control_points"] = fixture.ground_truth["board"][
            "control_points"
        ]
        assert map_render.render_png(smuggled) == clean

    def test_a_vision_radius_added_to_the_briefing_is_still_ignored(self) -> None:
        """The forward-compatible case: league may one day ship radii.

        If it does, this renderer must keep drawing exactly what the briefing
        listed rather than start inferring a region — deriving cones is league's
        job to expose, never the harness's to reconstruct.
        """
        fixture = map_render.fog_leak_fixture()
        clean = map_render.render_png(fixture.fogged)
        widened = json.loads(json.dumps(fixture.fogged))
        for unit in widened["board"]["units"]:
            unit["vision_mu"] = 99000
        widened["board"]["fog_radius_mu"] = 99000
        assert map_render.render_png(widened) == clean

    def test_a_bare_board_mapping_renders_like_a_full_briefing(self) -> None:
        """The seam accepts the briefing or just its ``board`` projection."""
        fixture = map_render.fog_leak_fixture()
        full = _render(fixture.fogged)
        bare = _render(fixture.fogged["board"], team_id=full.team_id)
        assert bare.raster.width == full.raster.width
        assert bare.raster.height == full.raster.height


class TestFogLeak:
    """Criterion 2 — the adversarial fixture, and the guard that gives it teeth."""

    def test_the_fixture_actually_hides_something(self) -> None:
        """Guard the guard: a fixture that hides nothing proves nothing."""
        fixture = map_render.fog_leak_fixture()
        assert fixture.hidden, "the fog-leak fixture must hide at least one entity"
        fogged_board = fixture.fogged["board"]
        truth_board = fixture.ground_truth["board"]
        for key in ("units", "control_points", "resource_nodes", "missions"):
            assert len(fogged_board[key]) < len(truth_board[key]) or not truth_board[key]

    def test_no_hidden_entity_is_drawn_anywhere_on_the_fogged_board(self) -> None:
        """The global check: the rival's ink appears nowhere inside the board.

        Every hidden entity in the fixture belongs to (or is owned by) the rival
        team, and the rival's palette slot plus the rival-halo slot are used for
        nothing else, so their total absence from the board rectangle is a
        whole-image proof rather than a spot check.
        """
        fixture = map_render.fog_leak_fixture()
        render = _render(fixture.fogged)
        rect = render.layout.board_rect()
        drawn = render.raster.indices_in(*rect)
        rival = map_render.team_index(1)
        assert rival not in drawn, "a rival-coloured pixel was drawn from a fogged briefing"
        assert map_render.IDX_ALERT not in drawn, "a rival halo was drawn from a fogged briefing"

    def test_each_hidden_entity_position_shows_only_the_unknown_plane(self) -> None:
        """The positional check: nothing at all sits where the hidden thing is."""
        fixture = map_render.fog_leak_fixture()
        render = _render(fixture.fogged)
        for entity in fixture.hidden:
            px, py = render.layout.to_pixel(entity["pos"]["x"], entity["pos"]["y"])
            side = map_render.GLYPH_PROBE_PX
            drawn = render.raster.indices_in(px - side, py - side, 2 * side + 1, 2 * side + 1)
            assert drawn <= map_render.PLANE_INDICES, (
                f"{entity['kind']} {entity['id']} at {entity['pos']} left ink "
                f"{sorted(drawn - map_render.PLANE_INDICES)} on a fogged map"
            )

    def test_the_leak_assertions_have_teeth(self) -> None:
        """Vacuity guard — the same two checks must FAIL on ground truth.

        Without this, a renderer that drew nothing at all would pass the fog
        tests perfectly. Rendering the fogless briefing is the control: the
        hidden entities must be drawn there, at those exact positions.
        """
        fixture = map_render.fog_leak_fixture()
        render = _render(fixture.ground_truth)
        rect = render.layout.board_rect()
        drawn = render.raster.indices_in(*rect)
        assert map_render.team_index(1) in drawn, "the fixture's rival is invisible even fogless"
        leaked = []
        for entity in fixture.hidden:
            px, py = render.layout.to_pixel(entity["pos"]["x"], entity["pos"]["y"])
            side = map_render.GLYPH_PROBE_PX
            near = render.raster.indices_in(px - side, py - side, 2 * side + 1, 2 * side + 1)
            if near - map_render.PLANE_INDICES:
                leaked.append(entity["id"])
        assert sorted(leaked) == sorted(e["id"] for e in fixture.hidden), (
            "the ground-truth control did not draw every hidden entity, so the "
            "fogged assertions above could pass vacuously"
        )

    def test_the_two_renders_are_different_images(self) -> None:
        fixture = map_render.fog_leak_fixture()
        assert map_render.render_png(fixture.fogged) != map_render.render_png(fixture.ground_truth)


class TestGoldens:
    """Criterion 2 — committed golden images, so a regression is visible."""

    @pytest.mark.parametrize("name", map_render.GOLDEN_NAMES)
    def test_golden_image_is_committed(self, name: str) -> None:
        assert (GOLDEN_DIR / f"{name}.png").exists(), (
            f"missing golden {name}.png — regenerate with: "
            f"python examples/map_render.py goldens --out tests/goldens/maps"
        )

    @pytest.mark.parametrize("name", map_render.GOLDEN_NAMES)
    def test_render_matches_its_golden(self, name: str) -> None:
        """Compare the decoded raster, not the file bytes.

        zlib's exact output is not guaranteed stable across builds (zlib-ng
        ships in some distributions), so byte-comparing a compressed golden
        would fail for a reason that has nothing to do with the drawing. The
        raster — dimensions, palette, and every pixel index — is the thing under
        test, and it is fully deterministic.
        """
        golden = map_render.decode_png((GOLDEN_DIR / f"{name}.png").read_bytes())
        fresh = _render(map_render.demo_briefings()[name]).raster
        assert (fresh.width, fresh.height) == (golden.width, golden.height)
        assert fresh.palette == golden.palette
        assert fresh.indices == golden.indices, (
            f"golden {name}.png no longer matches the renderer; if the change is "
            "intended, regenerate the goldens and eyeball the diff"
        )

    def test_goldens_regenerate_byte_stably_within_a_run(self, tmp_path: Path) -> None:
        written = map_render.write_goldens(tmp_path)
        assert sorted(p.name for p in written) == sorted(
            f"{n}.png" for n in map_render.GOLDEN_NAMES
        )
        again = map_render.write_goldens(tmp_path / "again")
        for first, second in zip(sorted(written), sorted(again)):
            assert first.read_bytes() == second.read_bytes()

    def test_render_is_deterministic(self) -> None:
        # Two INDEPENDENTLY constructed briefings, not one object rendered
        # twice. Rendering the same object twice would also pass if the
        # renderer keyed a cache on identity, or if object identity leaked into
        # the output; equal-but-distinct inputs rule both out. (Sonar S5863
        # flagged the original as an assertion comparing an expression to
        # itself, and it was right for a reason beyond the syntax.)
        first = map_render.demo_briefings()["fog_scoped"]
        second = map_render.demo_briefings()["fog_scoped"]
        assert first is not second
        assert map_render.render_png(first) == map_render.render_png(second)


class TestUnknownIsNotEmpty:
    """'I cannot see there' and 'there is nothing there' must not look alike."""

    def test_the_unobserved_plane_is_hatched(self) -> None:
        render = _render(map_render.demo_briefings()["blind_spot"])
        drawn = render.raster.indices_in(*render.layout.board_rect())
        assert map_render.IDX_UNKNOWN in drawn
        assert map_render.IDX_HATCH in drawn, "unobserved ground carries no hatch marking"

    def test_an_observed_cell_reads_differently_from_an_unobserved_one(self) -> None:
        """The two tones are distinct indices AND distinct colours."""
        render = _render(map_render.demo_briefings()["fog_scoped"])
        drawn = render.raster.indices_in(*render.layout.board_rect())
        assert {map_render.IDX_KNOWN, map_render.IDX_UNKNOWN} <= drawn
        known_rgb = render.raster.palette[map_render.IDX_KNOWN]
        unknown_rgb = render.raster.palette[map_render.IDX_UNKNOWN]
        distance = sum(abs(a - b) for a, b in zip(known_rgb, unknown_rgb))
        assert distance > 90, f"observed/unobserved tones are too close: {known_rgb} {unknown_rgb}"

    def test_an_observed_cell_is_not_hatched(self) -> None:
        """The hatch is the marking; it must stop where observation starts."""
        briefing = map_render.demo_briefings()["fog_scoped"]
        render = _render(briefing)
        unit = briefing["board"]["units"][0]
        px, py = render.layout.to_pixel(unit["pos"]["x"], unit["pos"]["y"])
        cell = render.layout.cell_rect_at(unit["pos"]["x"], unit["pos"]["y"])
        assert render.raster.count_index(map_render.IDX_HATCH, *cell) == 0
        assert render.raster.index_at(px, py) != map_render.IDX_UNKNOWN

    def test_a_board_with_nothing_visible_is_entirely_unknown(self) -> None:
        """The strongest form of the criterion: no entity, no observed ground."""
        empty = {
            "game_time": 3,
            "you": {"unit_id": "blue-1", "team_id": "blue", "role": "scout"},
            "board": {"width": 12000, "height": 8000, "teams": [{"id": "blue"}]},
        }
        render = _render(empty)
        drawn = render.raster.indices_in(*render.layout.board_rect())
        assert map_render.IDX_KNOWN not in drawn
        assert map_render.IDX_UNKNOWN in drawn

    def test_the_legend_says_blank_means_unobserved(self) -> None:
        """The caption is part of the instrument, not decoration.

        A vision model reads the legend; if the legend does not say that blank
        ground is unknown rather than empty, the image lies by omission.
        """
        caption = " ".join(map_render.FOOTER_LINES + (map_render.UNKNOWN_LEGEND_TEXT,)).upper()
        assert "NOT OBSERVED" in caption
        assert "NOT EMPTY" in caption

    def test_every_caption_character_is_renderable(self) -> None:
        """A missing glyph degrades to a blank — silently, which is worse here."""
        captions = (
            map_render.FOOTER_LINES
            + (map_render.UNKNOWN_LEGEND_TEXT,)
            + tuple(text for _, text in map_render.LEGEND_ROWS)
        )
        for caption in captions:
            missing = sorted({c for c in caption.upper() if c not in map_render.FONT})
            assert not missing, f"{caption!r} needs glyphs for {missing}"


class TestBoardDrawing:
    """The map is legible: entities are distinguishable, and 'you' is marked."""

    def test_own_and_rival_units_use_different_team_slots(self) -> None:
        render = _render(map_render.demo_briefings()["ground_truth"])
        drawn = render.raster.indices_in(*render.layout.board_rect())
        assert map_render.team_index(0) in drawn
        assert map_render.team_index(1) in drawn

    def test_the_acting_unit_is_marked(self) -> None:
        briefing = map_render.demo_briefings()["fog_scoped"]
        render = _render(briefing)
        you = briefing["you"]
        px, py = render.layout.to_pixel(you["pos"]["x"], you["pos"]["y"])
        side = map_render.GLYPH_PROBE_PX
        near = render.raster.indices_in(px - side, py - side, 2 * side + 1, 2 * side + 1)
        assert map_render.IDX_INK in near, "the acting unit carries no 'you' marker"

    def test_the_acting_team_always_takes_the_first_palette_slot(self) -> None:
        """Presentation scoping, so 'us' looks the same on every seat's map."""
        briefing = map_render.demo_briefings()["ground_truth"]
        ours = _render(briefing, team_id="blue")
        theirs = _render(briefing, team_id="red")
        assert ours.team_order[0] == "blue"
        assert theirs.team_order[0] == "red"
        assert ours.raster.indices != theirs.raster.indices

    def test_entity_kinds_do_not_share_a_colour(self) -> None:
        render = _render(map_render.demo_briefings()["ground_truth"])
        drawn = render.raster.indices_in(*render.layout.board_rect())
        for index in (map_render.IDX_RESOURCE, map_render.IDX_MISSION):
            assert index in drawn

    def test_an_entity_outside_the_board_extent_is_clamped_not_crashed(self) -> None:
        briefing = json.loads(json.dumps(map_render.demo_briefings()["fog_scoped"]))
        briefing["board"]["units"][0]["pos"] = {"x": -50000, "y": 999999}
        render = _render(briefing)
        assert render.raster.width > 0


class TestCommitSeam:
    """Criterion 3 — a measured turn's map can land in the raw results dir."""

    def test_write_turn_map_writes_a_png_and_a_sidecar(self, tmp_path: Path) -> None:
        briefing = map_render.demo_briefings()["fog_scoped"]
        artifact = map_render.write_turn_map(
            briefing, tmp_path, match_id="cm-1", turn=7, seat="blue-1"
        )
        assert artifact.png_path.exists()
        assert artifact.meta_path.exists()
        assert artifact.png_path.read_bytes().startswith(map_render.PNG_SIGNATURE)
        meta = json.loads(artifact.meta_path.read_text(encoding="utf-8"))
        assert meta["match_id"] == "cm-1"
        assert meta["turn"] == 7
        assert meta["seat"] == "blue-1"

    def test_the_artifact_filename_identifies_match_turn_and_seat(self, tmp_path: Path) -> None:
        """A raw results dir with a hundred maps in it has to stay readable."""
        artifact = map_render.write_turn_map(
            map_render.demo_briefings()["fog_scoped"],
            tmp_path,
            match_id="cm-1",
            turn=7,
            seat="blue-1",
        )
        assert artifact.png_path.name == "cm-1-t0007-blue-1.png"

    def test_unsafe_identifiers_cannot_escape_the_results_dir(self, tmp_path: Path) -> None:
        artifact = map_render.write_turn_map(
            map_render.demo_briefings()["fog_scoped"],
            tmp_path,
            match_id="../../etc",
            turn=1,
            seat="a/b",
        )
        assert artifact.png_path.parent == tmp_path
        assert ".." not in artifact.png_path.name

    def test_the_sidecar_carries_the_snapshot_hash_for_the_text_twin(self, tmp_path: Path) -> None:
        """t10 pairs an image cell with a text twin from the same fog snapshot."""
        briefing = map_render.demo_briefings()["fog_scoped"]
        artifact = map_render.write_turn_map(briefing, tmp_path, match_id="cm-1", turn=1)
        meta = json.loads(artifact.meta_path.read_text(encoding="utf-8"))
        assert meta["snapshot_hash"] == map_render.snapshot_hash(briefing)
        assert meta["png_sha256"] == artifact.png_sha256
        assert meta["renderer"].endswith("map_render.py")

    def test_the_snapshot_hash_covers_exactly_what_was_rendered(self) -> None:
        """*Exactly* is a two-sided claim, and this test used to make neither.

        It asserted ``snapshot_hash(b) == snapshot_hash(b)`` — the same
        expression twice, which measures that the digest is stable across two
        calls and says nothing about what it covers (Sonar S5863). A hash that
        ignored ``board`` entirely, or one that folded in the fog-leaking
        fields ``fog_snapshot`` deliberately drops, would both have passed.

        So: every field the map draws must change the hash, and every field it
        does not draw must not.
        """
        briefing = map_render.demo_briefings()["fog_scoped"]
        snapshot = map_render.fog_snapshot(briefing)
        assert set(snapshot) == {"game_time", "team_id", "board"}
        baseline = map_render.snapshot_hash(briefing)

        # Under-coverage: each rendered field must reach the digest.
        moved = copy.deepcopy(briefing)
        moved["game_time"] = briefing["game_time"] + 1
        assert map_render.snapshot_hash(moved) != baseline, "game_time is not covered"

        reboarded = copy.deepcopy(briefing)
        reboarded["board"] = map_render.demo_briefings()["blind_spot"]["board"]
        assert map_render.snapshot_hash(reboarded) != baseline, "board is not covered"

        # Over-coverage: a field the map does not draw must not move the
        # digest, or two cells that rendered identical pictures would look
        # unpaired to the twin rule.
        chatty = copy.deepcopy(briefing)
        chatty["messages"] = [{"from": "ally", "text": "push mid"}]
        chatty["menu"] = ["attack", "hold"]
        chatty["outlook"] = "confident"
        assert map_render.snapshot_hash(chatty) == baseline, (
            "an off-map field reached the digest; the twin rule would refuse "
            "two cells that drew the same picture"
        )

    def test_fogged_and_fogless_snapshots_hash_differently(self) -> None:
        """The twin check must be able to tell the two apart."""
        fixture = map_render.fog_leak_fixture()
        assert map_render.snapshot_hash(fixture.fogged) != map_render.snapshot_hash(
            fixture.ground_truth
        )

    def test_the_snapshot_hash_ignores_untendered_briefing_fields(self) -> None:
        """menu/outlook/messages are not on the map, so they are not in the hash."""
        briefing = json.loads(json.dumps(map_render.demo_briefings()["fog_scoped"]))
        before = map_render.snapshot_hash(briefing)
        briefing["menu"] = [{"kind": "move"}]
        briefing["messages"] = [{"from": "blue-2", "text": "hold"}]
        assert map_render.snapshot_hash(briefing) == before

    def test_two_turns_do_not_collide_in_the_results_dir(self, tmp_path: Path) -> None:
        briefing = map_render.demo_briefings()["fog_scoped"]
        first = map_render.write_turn_map(briefing, tmp_path, match_id="cm-1", turn=1, seat="b1")
        second = map_render.write_turn_map(briefing, tmp_path, match_id="cm-1", turn=2, seat="b1")
        assert first.png_path != second.png_path
        assert len(list(tmp_path.glob("*.png"))) == 2


class TestPngIsValid:
    """The encoder is hand-rolled from zlib + struct; prove it emits a PNG."""

    def test_signature_and_chunk_order(self) -> None:
        data = map_render.render_png(map_render.demo_briefings()["fog_scoped"])
        assert data.startswith(map_render.PNG_SIGNATURE)
        names = [name for name, _ in map_render.iter_chunks(data)]
        assert names[0] == b"IHDR"
        assert names[-1] == b"IEND"
        assert b"PLTE" in names
        assert b"IDAT" in names
        assert names.index(b"PLTE") < names.index(b"IDAT")

    def test_every_chunk_crc_validates(self) -> None:
        data = map_render.render_png(map_render.demo_briefings()["fog_scoped"])
        offset = len(map_render.PNG_SIGNATURE)
        while offset < len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            name = data[offset + 4 : offset + 8]
            payload = data[offset + 8 : offset + 8 + length]
            crc = int.from_bytes(data[offset + 8 + length : offset + 12 + length], "big")
            assert crc == zlib.crc32(name + payload) & 0xFFFFFFFF, name
            offset += 12 + length

    def test_ihdr_declares_an_8bit_indexed_image(self) -> None:
        data = map_render.render_png(map_render.demo_briefings()["fog_scoped"])
        header = dict(map_render.iter_chunks(data))[b"IHDR"]
        width = int.from_bytes(header[0:4], "big")
        height = int.from_bytes(header[4:8], "big")
        assert (header[8], header[9]) == (8, 3), "expected 8-bit indexed colour"
        assert (header[10], header[11], header[12]) == (0, 0, 0)
        assert width > 0
        assert height > 0

    def test_decode_round_trips_an_encoded_canvas(self) -> None:
        palette = ((0, 0, 0), (255, 255, 255), (10, 200, 30))
        indices = bytes([0, 1, 2, 2, 1, 0])
        data = map_render.encode_png(3, 2, palette, indices)
        raster = map_render.decode_png(data)
        assert (raster.width, raster.height) == (3, 2)
        assert raster.palette == palette
        assert raster.indices == indices

    def test_decode_handles_every_png_filter_type(self) -> None:
        """The decoder is the golden comparator; it must not assume our writer."""
        palette = tuple((i * 7 % 256, i * 11 % 256, i * 13 % 256) for i in range(8))
        rows = [bytes((y * 3 + x) % 8 for x in range(6)) for y in range(5)]
        for filter_type in range(5):
            data = map_render.encode_png(6, 5, palette, b"".join(rows), filter_type=filter_type)
            assert map_render.decode_png(data).indices == b"".join(rows), filter_type

    def test_decode_rejects_a_truncated_file(self) -> None:
        data = map_render.render_png(map_render.demo_briefings()["blind_spot"])
        with pytest.raises(ValueError):
            map_render.decode_png(data[:40])

    def test_the_palette_fits_an_indexed_png(self) -> None:
        assert 0 < len(map_render.PALETTE) <= 256
        assert all(len(c) == 3 and all(0 <= v <= 255 for v in c) for c in map_render.PALETTE)


class TestNeverRaises:
    """A defective briefing must produce a poor map, never an exception.

    The renderer sits in a measurement lane: a malformed turn that crashes the
    harness costs the whole run, while a map that renders the little it could
    parse is still an artifact someone can inspect.
    """

    @pytest.mark.parametrize(
        "briefing",
        [
            {},
            {"board": {}},
            {"board": {"width": 0, "height": 0}},
            {"you": None, "board": None},
            {"board": {"width": "wide", "height": None, "units": "not-a-list"}},
            {"board": {"width": 100, "height": 100, "units": [{"pos": None}, None, 7]}},
            {"board": {"width": 100, "height": 100, "control_points": [{"pos": {"x": "a"}}]}},
            {"board": {"width": 100, "height": 100, "teams": "blue"}},
            {"board": {"width": 100, "height": 100, "missions": [{"kind": None, "pos": {}}]}},
        ],
    )
    def test_a_malformed_briefing_still_renders_a_valid_png(self, briefing: Any) -> None:
        data = map_render.render_png(briefing)
        assert data.startswith(map_render.PNG_SIGNATURE)
        raster = map_render.decode_png(data)
        assert raster.width > 0
        assert raster.height > 0

    def test_unrenderable_text_degrades_to_blanks_not_a_crash(self) -> None:
        briefing = json.loads(json.dumps(map_render.demo_briefings()["fog_scoped"]))
        briefing["board"]["teams"][0]["name"] = "Ω≈ç√ team"
        assert map_render.render_png(briefing).startswith(map_render.PNG_SIGNATURE)


class TestCli:
    """The example is runnable, because an instrument nobody can run is a claim."""

    def test_render_subcommand_writes_a_png(self, tmp_path: Path) -> None:
        briefing_path = tmp_path / "b.json"
        briefing_path.write_text(json.dumps(map_render.demo_briefings()["fog_scoped"]))
        out = tmp_path / "map.png"
        code = map_render.main(["render", "--briefing", str(briefing_path), "--out", str(out)])
        assert code == 0
        assert out.read_bytes().startswith(map_render.PNG_SIGNATURE)

    def test_turn_subcommand_lands_in_a_results_dir(self, tmp_path: Path, capsys: Any) -> None:
        briefing_path = tmp_path / "b.json"
        briefing_path.write_text(json.dumps(map_render.demo_briefings()["fog_scoped"]))
        code = map_render.main(
            [
                "turn",
                "--briefing",
                str(briefing_path),
                "--out-dir",
                str(tmp_path / "raw"),
                "--match",
                "cm-9",
                "--turn",
                "2",
                "--seat",
                "blue-1",
                "--json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert Path(payload["png"]).exists()
        assert payload["snapshot_hash"]

    def test_goldens_subcommand_regenerates_the_committed_set(self, tmp_path: Path) -> None:
        assert map_render.main(["goldens", "--out", str(tmp_path)]) == 0
        for name in map_render.GOLDEN_NAMES:
            assert (tmp_path / f"{name}.png").exists()

    def test_a_missing_briefing_file_is_an_error_not_a_traceback(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        code = map_render.main(["render", "--briefing", str(tmp_path / "nope.json"), "--out", "x"])
        assert code == 1
        assert re.search(r"error:", capsys.readouterr().err)
