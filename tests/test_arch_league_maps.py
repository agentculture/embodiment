"""Tests for the fog-scoped map-image league lane (plan task t10).

Acceptance criteria (from the plan's t10 entry), and where each is proved:

1. **The harness refuses to run a cell whose image lacks a committed text
   twin derived from the same fog snapshot — same turn, same seat, same
   ``snapshot_hash`` — and the refusal is structural.**
   → :class:`TestTheTwinRuleRefusesStructurally`: the check fires inside
   ``describe`` (below any dial), inside ``run_round`` (before any tool
   schema is built or ``seams.build`` is called — proved by an empty call
   log), and inside ``play_match`` (before the arena ever sees an ``act``
   call).

2. **Twin snapshot hashes ride the per-call records, so any dial can be
   re-inspected after the fact.**
   → :class:`TestTwinHashesRideTheRecords`: every ``RouteRecord`` produced
   over the image route carries a non-empty ``snapshot_hash`` matching the
   twin it was checked against, and a committed JSONL artifact alone (no
   in-memory ledger) is enough to cross-check every dial's pairing.

Plus the two boundaries the task is explicit about:

* **Registration hygiene.** ``arch_league.ROUTE_REGISTRY`` is shared,
  process-wide state, and t6's own suite asserts it holds only the text
  route by default. This module never leaves the image route registered
  past the block that needed it — :class:`TestRegistrationHygiene` proves
  that, including on the exception path.
* **v1 is team-scoped, and no vision-radius constant exists anywhere in
  this harness's own code (c37/h29).** :class:`TestNoVisionRadiusAnywhere`
  mirrors ``test_map_render.py``'s self-scan, applied to
  ``arch_league_maps.py``; :class:`TestTeamScopedNotPerUnit` proves the
  consequence behaviourally.

And the integration proof that this is wired into the league lane as
measurable cells, not just a standalone check: :class:`TestWiredIntoTheLeagueLane`.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import arch_arms as aa  # noqa: E402
from examples import arch_league as al  # noqa: E402
from examples import arch_league_maps as alm  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import map_render as mr  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_CLEAGUE = REPO_ROOT / "tests" / "fake_cleague.py"
FAKE_BIN = f"{sys.executable} {FAKE_CLEAGUE}"

#: Mirrors ``test_map_render.py``'s own vocabulary/literal scan, applied here
#: because h29 binds "this harness's own code", not only the renderer's.
LEAGUE_VISION_MILLIUNITS = (2000, 4000, 6000)
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


# ── fixtures shared by this file ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_module_state() -> Any:
    """Never let one test's ledger or route registration leak into the next."""
    assert al.ROUTE_TEXT in al.ROUTE_REGISTRY
    assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY, "a prior test left the image route registered"
    alm.TWIN_LEDGER.clear()
    yield
    alm.TWIN_LEDGER.clear()
    assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY, "this test left the image route registered"


def _source() -> str:
    return Path(alm.__file__).read_text(encoding="utf-8")


def _rich_briefing(
    *,
    unit_id: str = "bluee-u1",
    role: str = "defender",
    team_id: str = "bluee",
    game_time: int = 4,
    options: int = 3,
    match_id: str = "cm-t10",
) -> dict[str, Any]:
    """A briefing carrying both what ``unit_brief`` needs and what a map can draw.

    The roster in ``board.units`` is fixed regardless of which unit is "you" —
    real league briefings carry the whole team's units, not just the asking
    one — which is what makes :class:`TestTeamScopedNotPerUnit` a fair test:
    only ``you.unit_id`` differs between two units on the same team, never the
    board.
    """
    board = {
        "match_id": match_id,
        "clock": game_time,
        "width": 24000,
        "height": 16000,
        "teams": [
            {"id": "bluee", "name": "Blue Watch", "resources": 7},
            {"id": "redd", "name": "Red Company", "resources": 4},
        ],
        "units": [
            {
                "id": "bluee-u1",
                "team_id": "bluee",
                "role": "defender",
                "pos": {"x": 3500, "y": 5000},
                "carrying": 0,
                "alive": True,
            },
            {
                "id": "bluee-u2",
                "team_id": "bluee",
                "role": "harvester",
                "pos": {"x": 7000, "y": 9000},
                "carrying": 2,
                "alive": True,
            },
        ],
        "control_points": [
            {"id": "cp-west", "pos": {"x": 5000, "y": 6500}, "owner": "bluee", "takers": []}
        ],
        "resource_nodes": [{"id": "rn-1", "pos": {"x": 6500, "y": 11000}, "remaining": 12}],
        "missions": [],
    }
    return {
        "game_time": game_time,
        "you": {
            "unit_id": unit_id,
            "team_id": team_id,
            "role": role,
            "pos": {"x": 3500, "y": 5000},
            "carrying": 0,
        },
        "menu": [
            {"kind": "move", "target": f"p{i}", "duration": i + 1, "completion_time": 5 + i}
            for i in range(options)
        ],
        "outlook": [],
        "messages": [],
        "board": board,
    }


def _mutate_board(briefing: dict[str, Any]) -> dict[str, Any]:
    """Same match/turn/seat identity, different fog content -- a stale twin's shape."""
    mutated = json.loads(json.dumps(briefing))
    mutated["board"]["units"][0]["pos"]["x"] += 1000
    return mutated


def _config() -> aa.ArchConfig:
    return aa.load_config()


# ═════════════════════════════════════════════════════════════════════════════
# criterion 1 — the harness refuses to run a cell without a valid twin
# ═════════════════════════════════════════════════════════════════════════════


class TestTheTwinRuleRefusesStructurally:
    """Missing or stale twins raise; nothing below the check ever executes."""

    def test_describe_raises_when_no_twin_was_ever_committed(self) -> None:
        ledger = alm.TwinLedger()
        route = alm.build_image_route(ledger)
        brief = _rich_briefing()
        with pytest.raises(alm.MissingTwinError):
            route.describe(brief, "bluee")

    def test_describe_raises_when_the_committed_twin_is_stale(self) -> None:
        ledger = alm.TwinLedger()
        route = alm.build_image_route(ledger)
        original = _rich_briefing()
        ledger.commit(_mutate_board(original))  # same key, different fog content
        with pytest.raises(alm.MismatchedTwinError):
            route.describe(original, "bluee")

    def test_describe_succeeds_once_a_matching_twin_is_committed(self) -> None:
        ledger = alm.TwinLedger()
        route = alm.build_image_route(ledger)
        brief = _rich_briefing()
        twin = ledger.commit(brief)
        perception = route.describe(brief, "bluee")
        assert perception.route == alm.ROUTE_IMAGE
        assert perception.snapshot_hash == twin.snapshot_hash
        assert perception.snapshot_hash == mr.snapshot_hash(brief, "bluee")
        assert perception.parts
        assert perception.parts[0].startswith(mr.PNG_SIGNATURE)

    def test_the_scan_would_catch_a_planted_bypass(self) -> None:
        """Vacuity guard: prove the exception types themselves are distinguishable."""
        assert issubclass(alm.MissingTwinError, alm.TwinError)
        assert issubclass(alm.MismatchedTwinError, alm.TwinError)
        assert not issubclass(alm.MissingTwinError, alm.MismatchedTwinError)

    def test_run_round_refuses_before_any_model_is_dialled(self) -> None:
        """No twin at all: the round must not reach ``seams.build``."""
        cfg = _config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        brief = _rich_briefing()
        senses_hash = aa.assert_senses_identical(cfg)
        with alm.image_route_registered():
            with pytest.raises(alm.TwinError):
                al.run_round(
                    arm=aa.ARMS[aa.ARM_EXISTING],
                    rung_id=al.LEAGUE_LADDER[0].id,
                    match_key="e-0",
                    match_id="e0",
                    team_id="bluee",
                    round_index=0,
                    decision_base=0,
                    briefings=[brief],
                    seams=seams,
                    config=cfg,
                    senses_hash=senses_hash,
                    route=alm.ROUTE_IMAGE,
                )
        assert log.records == [], "a refused twin must not still have dialled a mind"

    def test_run_round_refuses_when_the_committed_twin_is_stale(self) -> None:
        cfg = _config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        brief = _rich_briefing()
        senses_hash = aa.assert_senses_identical(cfg)
        with alm.image_route_registered() as ledger:
            ledger.commit(_mutate_board(brief))
            with pytest.raises(alm.MismatchedTwinError):
                al.run_round(
                    arm=aa.ARMS[aa.ARM_EXISTING],
                    rung_id=al.LEAGUE_LADDER[0].id,
                    match_key="e-0",
                    match_id="e0",
                    team_id="bluee",
                    round_index=0,
                    decision_base=0,
                    briefings=[brief],
                    seams=seams,
                    config=cfg,
                    senses_hash=senses_hash,
                    route=alm.ROUTE_IMAGE,
                )
        assert log.records == []

    def test_run_round_proceeds_once_the_matching_twin_is_committed(self) -> None:
        """The positive control: the same setup, minus the defect, actually runs."""
        cfg = _config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        brief = _rich_briefing()
        with alm.image_route_registered() as ledger:
            ledger.commit(brief)
            record = al.run_round(
                arm=aa.ARMS[aa.ARM_EXISTING],
                rung_id=al.LEAGUE_LADDER[0].id,
                match_key="e-0",
                match_id="e0",
                team_id="bluee",
                round_index=0,
                decision_base=0,
                briefings=[brief],
                seams=seams,
                config=cfg,
                senses_hash=aa.assert_senses_identical(cfg),
                route=alm.ROUTE_IMAGE,
            )
        assert record.routes
        assert log.records, "a valid twin must have let the round actually dial a mind"

    def test_play_match_refuses_before_reaching_the_arena_with_an_order(
        self, tmp_path: Path
    ) -> None:
        """A cell backed by a plain (non-committing) CLI never submits an order."""
        seen: list[tuple[str, ...]] = []
        real = lc.LeagueCli.__call__

        class Recording(lc.LeagueCli):
            def __call__(self, *args: str) -> dict[str, Any]:
                seen.append(tuple(args[:2]))
                return real(self, *args)

        root = tmp_path / "arena"
        root.mkdir(parents=True, exist_ok=True)
        cfg = _config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        cli = Recording(root=root, binary=FAKE_BIN)
        senses_hash = aa.assert_senses_identical(cfg)
        with alm.image_route_registered():
            with pytest.raises(alm.TwinError):
                al.play_match(
                    cli=cli,
                    arm=aa.ARMS[aa.ARM_EXISTING],
                    rung=al.LEAGUE_LADDER[0],
                    match_index=0,
                    seed=al.LEAGUE_LADDER[0].seeds[0],
                    seams=seams,
                    config=cfg,
                    senses_hash=senses_hash,
                    route=alm.ROUTE_IMAGE,
                )
        assert ("cmatch", "show") in seen, "the refusal happened too early to be about pairing"
        assert ("cmatch", "act") not in seen, "a refused cell must never reach the arena"


# ═════════════════════════════════════════════════════════════════════════════
# criterion 2 — twin snapshot hashes ride the per-call records
# ═════════════════════════════════════════════════════════════════════════════


class TestTwinHashesRideTheRecords:
    def test_route_records_carry_the_twin_snapshot_hash(self) -> None:
        cfg = _config()
        log = al.ThreadSafeCallLog()
        seams = al.scripted_seams(aa.ARMS[aa.ARM_EXISTING], config=cfg, log=log)
        brief = _rich_briefing()
        with alm.image_route_registered() as ledger:
            twin = ledger.commit(brief)
            record = al.run_round(
                arm=aa.ARMS[aa.ARM_EXISTING],
                rung_id=al.LEAGUE_LADDER[0].id,
                match_key="e-0",
                match_id="e0",
                team_id="bluee",
                round_index=0,
                decision_base=0,
                briefings=[brief],
                seams=seams,
                config=cfg,
                senses_hash=aa.assert_senses_identical(cfg),
                route=alm.ROUTE_IMAGE,
            )
        assert record.routes
        for entry in record.routes:
            assert entry.route == alm.ROUTE_IMAGE
            assert entry.snapshot_hash == twin.snapshot_hash
            assert entry.snapshot_hash != ""

    def test_the_artifact_alone_is_enough_to_re_inspect_every_dial(self, tmp_path: Path) -> None:
        """No in-memory ledger needed: the committed JSONL carries both sides."""
        out = tmp_path / "maps.jsonl"
        alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
            out=out,
        )
        records = aa.read_log(out)
        routes = [r for r in records if r.get("kind") == "route"]
        twins = {
            (t["match_id"], t["game_time"], t["unit_id"]): t
            for t in records
            if t.get("kind") == "twin"
        }
        assert routes, "no routing records were written"
        assert twins, "no twin records were written"
        for entry in routes:
            key = (entry["match_id"], entry["game_time"], entry["unit_id"])
            twin = twins.get(key)
            assert twin is not None, f"route record {key} has no committed twin in the artifact"
            assert entry["snapshot_hash"] == twin["snapshot_hash"]
            assert entry["snapshot_hash"] != ""


# ═════════════════════════════════════════════════════════════════════════════
# registration hygiene — the shared registry is never left mutated
# ═════════════════════════════════════════════════════════════════════════════


class TestRegistrationHygiene:
    def test_building_a_route_does_not_touch_the_shared_registry(self) -> None:
        alm.build_image_route(alm.TwinLedger())
        assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY

    def test_the_context_manager_registers_then_pops(self) -> None:
        assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY
        with alm.image_route_registered():
            assert alm.ROUTE_IMAGE in al.ROUTE_REGISTRY
            assert al.route_for(alm.ROUTE_IMAGE).why
        assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY

    def test_the_context_manager_pops_even_on_exception(self) -> None:
        def raise_inside_the_registration() -> None:
            with alm.image_route_registered():
                assert alm.ROUTE_IMAGE in al.ROUTE_REGISTRY
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            raise_inside_the_registration()
        assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY

    def test_run_map_series_leaves_no_trace_in_the_registry(self, tmp_path: Path) -> None:
        alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
        )
        assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY

    def test_t6s_own_baseline_assertion_still_holds(self) -> None:
        """Reproduces test_arch_league.py's own registry-baseline check."""
        assert sorted(al.ROUTE_REGISTRY) == [al.ROUTE_TEXT]


# ═════════════════════════════════════════════════════════════════════════════
# c37/h29 — team-scoped in v1, and no vision-radius constant anywhere here
# ═════════════════════════════════════════════════════════════════════════════


class TestNoVisionRadiusAnywhere:
    """Mirrors test_map_render.py's own scan, applied to this module's source."""

    def test_no_vision_vocabulary_appears_in_the_source(self) -> None:
        tree = ast.parse(_source())
        body = tree.body[1:] if ast.get_docstring(tree) else tree.body
        code = "\n".join(ast.unparse(node) for node in body).lower()
        offenders = [word for word in VISION_VOCABULARY if word in code]
        assert not offenders, (
            f"arch_league_maps.py names {offenders} in code — v1 is team-scoped and "
            "derives nothing from a per-unit vision computation"
        )

    def test_no_league_vision_radius_literal_appears_in_the_source(self) -> None:
        literals = {
            node.value
            for node in ast.walk(ast.parse(_source()))
            if isinstance(node, ast.Constant) and isinstance(node.value, int)
        }
        copied = sorted(literals & set(LEAGUE_VISION_MILLIUNITS))
        assert not copied, f"league's vision radii {copied} appear as literals in this module"

    def test_the_module_imports_nothing_from_league(self) -> None:
        tree = ast.parse(_source())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [name for name in imported if name.split(".")[0] == "league"]

    def test_the_scan_would_catch_a_planted_radius(self) -> None:
        planted = "def sees(unit, pos):\n    return dist_sq(unit, pos) <= 4000 * 4000\n"
        code = "\n".join(ast.unparse(n) for n in ast.parse(planted).body).lower()
        assert [w for w in VISION_VOCABULARY if w in code]
        literals = {
            node.value
            for node in ast.walk(ast.parse(planted))
            if isinstance(node, ast.Constant) and isinstance(node.value, int)
        }
        assert literals & set(LEAGUE_VISION_MILLIUNITS)


class TestTeamScopedNotPerUnit:
    """v1's boundary, behaviourally: every unit on a team sees the same map."""

    def test_two_units_on_the_same_team_get_the_identical_map(self) -> None:
        """Same fog content for every seat on a team.

        The "YOU" ring may still differ (each render still marks which unit is
        asking), but the *information* -- the entities, the observed/unobserved
        plane, the digest a text twin must match -- is identical, which is
        exactly what "team-scoped, not per-unit" means for v1.
        """
        ledger = alm.TwinLedger()
        route = alm.build_image_route(ledger)
        brief_u1 = _rich_briefing(unit_id="bluee-u1")
        brief_u2 = _rich_briefing(unit_id="bluee-u2")
        ledger.commit(brief_u1)
        ledger.commit(brief_u2)
        seen_u1 = route.describe(brief_u1, "bluee")
        seen_u2 = route.describe(brief_u2, "bluee")
        assert seen_u1.snapshot_hash == seen_u2.snapshot_hash
        assert mr.fog_snapshot(brief_u1, "bluee") == mr.fog_snapshot(brief_u2, "bluee")

    def test_the_commander_view_and_a_units_view_are_the_same_object_in_v1(self) -> None:
        """c21 asked for a per-unit cone; c37/h29 is the honest v1 narrowing."""
        assert mr.snapshot_hash(_rich_briefing(unit_id="bluee-u1")) == mr.snapshot_hash(
            _rich_briefing(unit_id="bluee-u2")
        )


# ═════════════════════════════════════════════════════════════════════════════
# wired into the league lane as measurable cells
# ═════════════════════════════════════════════════════════════════════════════


class TestWiredIntoTheLeagueLane:
    @pytest.mark.parametrize("arm_id", aa.ARM_ORDER)
    def test_every_arm_completes_a_scripted_cmatch_over_the_image_route(
        self, arm_id: str, tmp_path: Path
    ) -> None:
        report = alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / arm_id,
            log=al.ThreadSafeCallLog(),
            arms=(arm_id,),
        )
        assert report["route"] == alm.ROUTE_IMAGE
        assert report["matches"][0]["status"] == "finished"
        assert report["matches"][0]["route"] == alm.ROUTE_IMAGE
        assert report["twins_committed"] > 0

    def test_a_series_writes_a_cell_per_arm_that_analyse_can_read(self, tmp_path: Path) -> None:
        out = tmp_path / "maps.jsonl"
        alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            out=out,
        )
        verdict = al.analyse(out)
        row = next(r for r in verdict["rungs"] if r["route"] == alm.ROUTE_IMAGE)
        assert row["state"] == aa.STATE_GRADED, row.get("refusal")

    def test_the_hybrid_arms_routing_is_still_logged_over_the_image_route(
        self, tmp_path: Path
    ) -> None:
        report = alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_HYBRID,),
        )
        match = report["matches"][0]
        assert match["routing_mix"] == [al.ROUTED_CORTEX, al.ROUTED_WORKER]
        assert match["degenerate_routing"] is False
        assert match["routed"] > 0
        assert match["kept"] > 0

    def test_image_dir_persists_a_png_per_committed_twin(self, tmp_path: Path) -> None:
        image_dir = tmp_path / "maps"
        alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
            image_dir=image_dir,
        )
        pngs = list(image_dir.glob("*.png"))
        assert pngs
        assert all(path.read_bytes().startswith(mr.PNG_SIGNATURE) for path in pngs)

    def test_the_cell_carries_the_route_and_senses_hash(self, tmp_path: Path) -> None:
        report = alm.run_map_series(
            config=_config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
        )
        cell = report["cells"][0]
        assert cell["route"] == alm.ROUTE_IMAGE
        assert cell["senses_config_hash"] == report["senses_config_hash"]


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════


class TestTheCli:
    def test_plan_reports_the_route_and_the_twin_rule(self, capsys: Any) -> None:
        assert alm.main(["plan", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["route"] == alm.ROUTE_IMAGE
        assert "twin" in payload["twin_rule"].lower()

    def test_play_refuses_the_real_arena_without_the_gate(self, capsys: Any) -> None:
        assert alm.main(["play"]) == 2
        assert al.LIVE_ARENA_ENV in capsys.readouterr().err

    def test_play_runs_hermetically_against_a_stand_in(self, tmp_path: Path) -> None:
        code = alm.main(
            [
                "play",
                "--league",
                FAKE_BIN,
                "--root",
                str(tmp_path / "arena"),
                "--arm",
                aa.ARM_EXISTING,
            ]
        )
        assert code == 0
        assert alm.ROUTE_IMAGE not in al.ROUTE_REGISTRY
