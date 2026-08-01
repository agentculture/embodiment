"""t6 — the league lane arms, driven end to end through their real entry point.

The two acceptance criteria are the contract, and each has its own section
below:

1. **All four arms complete a scripted, no-network ``cmatch`` end-to-end.**
   ``TestAllFourArmsPlayACmatch`` plays a whole match per arm against
   ``tests/fake_cleague.py`` — no model, no key, no arena binary — and
   ``TestTheHarnessEntryPoint`` does it again through ``main``/``argv`` because
   task t12's lesson was that a harness nobody *ran* does not work.
2. **The hybrid arm's routing decision is exercised per decision point and
   logged with its stated reason.** ``TestHybridRoutingIsLogged`` is that
   section: every decision point in every arm lands a ``kind="route"`` record,
   the hybrid both routes and keeps inside one match, and a hybrid that keeps a
   unit without saying why is refused at the tool boundary rather than logged
   with an empty reason.

Everything here is hermetic. The live rig and the live arena are both frozen
this cycle and ``TestTheGates`` proves the harness refuses them.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess  # nosec B404
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import ModelResponse, ToolCall, ToolError, UnknownToolError  # noqa: E402
from examples import arch_arms as aa  # noqa: E402
from examples import arch_league as al  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import orchestrator_tools as ot  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "examples" / "arch_league.py"
FAKE_CLEAGUE = REPO_ROOT / "tests" / "fake_cleague.py"
FAKE_BIN = f"{sys.executable} {FAKE_CLEAGUE}"

#: Every league verb this harness may reach the arena with. Anything else means
#: it stopped using the public CLI.
PUBLIC_VERBS = {
    ("team", "register"),
    ("cmatch", "new"),
    ("cmatch", "show"),
    ("cmatch", "act"),
    ("cmatch", "tick"),
    ("match", "score"),
}


# ── helpers ──────────────────────────────────────────────────────────────────


def config() -> aa.ArchConfig:
    return aa.load_config()


def arena(tmp_path: Path, *, binary: str = FAKE_BIN) -> lc.LeagueCli:
    root = tmp_path / "arena"
    root.mkdir(parents=True, exist_ok=True)
    return lc.LeagueCli(root=root, binary=binary)


def play(
    arm_id: str,
    tmp_path: Path,
    *,
    cli: Optional[lc.LeagueCli] = None,
    log: Optional[aa.CallLog] = None,
    seams: Any = None,
    out: Optional[Path] = None,
    route: str = al.ROUTE_TEXT,
) -> tuple[al.LeagueMatchRecord, aa.AttemptRecord]:
    """Play one whole scripted match for one arm. No network anywhere."""
    cfg = config()
    call_log = log if log is not None else al.ThreadSafeCallLog()
    arm = aa.ARMS[arm_id]
    return al.play_match(
        cli=cli if cli is not None else arena(tmp_path),
        arm=arm,
        rung=al.LEAGUE_LADDER[0],
        match_index=0,
        seed=al.LEAGUE_LADDER[0].seeds[0],
        seams=seams if seams is not None else al.scripted_seams(arm, config=cfg, log=call_log),
        config=cfg,
        senses_hash=aa.assert_senses_identical(cfg),
        route=route,
        out=out,
    )


def briefing(
    *,
    unit_id: str = "bluee-u1",
    role: str = "defender",
    options: int = 3,
    game_time: int = 4,
    team_id: str = "bluee",
) -> dict[str, Any]:
    return {
        "game_time": game_time,
        "you": {
            "unit_id": unit_id,
            "agent_id": "b1",
            "team_id": team_id,
            "role": role,
            "pos": {"x": 1, "y": 2},
            "carrying": 0,
        },
        "menu": [
            {"kind": "move", "target": f"p{i}", "duration": i + 1, "completion_time": 5 + i}
            for i in range(options)
        ],
        "outlook": [],
        "board": {"match_id": "m", "clock": game_time},
        "messages": [],
    }


def units_of(*specs: tuple[str, str]) -> tuple[al.UnitBrief, ...]:
    """Build the seat's view of a round without going near an arena."""
    return tuple(
        al.unit_brief(briefing(unit_id=unit_id, role=role), route=al.ROUTE_TEXT, team_id="bluee")
        for unit_id, role in specs
    )


def seat(arm_id: str, *specs: tuple[str, str], worker_max_steps: int = 6) -> al.LeagueSeat:
    return al.LeagueSeat(
        units=units_of(*specs),
        arm=aa.ARMS[arm_id],
        task_id="t",
        worker_model="scripted_worker",
        worker_max_steps=worker_max_steps,
    )


# ═════════════════════════════════════════════════════════════════════════════
# criterion 1 — all four arms complete a scripted, no-network cmatch
# ═════════════════════════════════════════════════════════════════════════════


class TestAllFourArmsPlayACmatch:
    """One whole match per arm, against the fake arena, with scripted minds."""

    @pytest.mark.parametrize("arm_id", aa.ARM_ORDER)
    def test_every_arm_finishes_a_whole_cmatch(self, arm_id: str, tmp_path: Path) -> None:
        record, _ = play(arm_id, tmp_path)
        assert record.status == "finished"
        assert record.rounds > 0
        assert record.decisions == record.rounds * len(al.LEAGUE_LADDER[0].roles)

    @pytest.mark.parametrize("arm_id", aa.ARM_ORDER)
    def test_every_decision_point_reaches_the_arena_with_an_order(
        self, arm_id: str, tmp_path: Path
    ) -> None:
        record, _ = play(arm_id, tmp_path)
        assert record.no_order == 0
        assert all(entry["ordered_index"] is not None for entry in record.routes)

    @pytest.mark.parametrize("arm_id", aa.ARM_ORDER)
    def test_every_arm_lands_a_per_call_record_for_every_model_turn(
        self, arm_id: str, tmp_path: Path
    ) -> None:
        log = al.ThreadSafeCallLog()
        _, attempt = play(arm_id, tmp_path, log=log)
        assert log.records, "a played match with no recorded call is not measured"
        assert attempt.calls == len(log.records)
        assert {entry.arm for entry in log.records} == {arm_id}
        assert all(entry.rung == al.LEAGUE_LADDER[0].id for entry in log.records)

    def test_the_orchestrated_manager_routes_every_unit_to_the_worker(self, tmp_path: Path) -> None:
        manager, _ = play(aa.ARM_MANAGER, tmp_path)
        assert manager.routed == manager.decisions
        assert manager.kept == 0

    def test_the_flat_arms_never_spawn_anything(self, tmp_path: Path) -> None:
        for arm_id in aa.FLAT_ARMS:
            record, _ = play(arm_id, tmp_path / arm_id)
            assert record.routed == 0
            assert record.child_model_turns == 0

    def test_the_worker_solo_arm_drives_the_top_level_loop_itself(self, tmp_path: Path) -> None:
        log = al.ThreadSafeCallLog()
        play(aa.ARM_WORKER_SOLO, tmp_path, log=log)
        assert {entry.role for entry in log.records} == {aa.ROLE_WORKER}

    def test_the_existing_arm_is_the_cortex_alone(self, tmp_path: Path) -> None:
        log = al.ThreadSafeCallLog()
        play(aa.ARM_EXISTING, tmp_path, log=log)
        assert {entry.role for entry in log.records} == {aa.ROLE_CORTEX}

    def test_the_hermetic_lane_needs_no_api_key(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.delenv("COLLEAGUE_API_KEY", raising=False)
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        assert record.status == "finished"

    def test_the_arena_is_reached_only_through_its_public_cli(self, tmp_path: Path) -> None:
        seen: list[tuple[str, ...]] = []
        real = lc.LeagueCli.__call__

        class Recording(lc.LeagueCli):
            def __call__(self, *args: str) -> dict[str, Any]:
                seen.append(tuple(args[:2]))
                return real(self, *args)

        root = tmp_path / "arena"
        root.mkdir(parents=True, exist_ok=True)
        play(aa.ARM_MANAGER, tmp_path, cli=Recording(root=root, binary=FAKE_BIN))
        assert seen, "the harness never reached the arena at all"
        assert set(seen) <= PUBLIC_VERBS, f"non-public verbs used: {set(seen) - PUBLIC_VERBS}"

    def test_a_series_writes_one_cell_per_arm_that_analyse_can_read(self, tmp_path: Path) -> None:
        out = tmp_path / "league.jsonl"
        report = al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            out=out,
        )
        assert [cell["arm"] for cell in report["cells"]] == list(aa.ARM_ORDER)
        verdict = al.analyse(out)
        row = verdict["rungs"][0]
        assert row["state"] == aa.STATE_GRADED, row.get("refusal")

    def test_analyse_still_refuses_a_rung_missing_a_flat_control(self, tmp_path: Path) -> None:
        """t5's refusal is composed, not re-implemented — so it still bites."""
        out = tmp_path / "league.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING, aa.ARM_MANAGER, aa.ARM_HYBRID),
            out=out,
        )
        verdict = al.analyse(out)
        assert verdict["rungs_refused"] == [al.LEAGUE_LADDER[0].id]
        assert aa.ARM_WORKER_SOLO in verdict["rungs"][0]["cells_absent"]

    def test_the_artifact_carries_the_configuration_that_produced_it(self, tmp_path: Path) -> None:
        out = tmp_path / "league.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
            out=out,
        )
        records = aa.read_log(out)
        preamble = records[0]
        assert preamble["kind"] == aa.KIND_PREAMBLE
        assert preamble["task"] == "t6"
        assert preamble["senses_config_hash"] == aa.assert_senses_identical(config())

    def test_the_artifact_carries_no_secret(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("COLLEAGUE_API_KEY", "sk-league-secret")
        out = tmp_path / "league.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
            out=out,
        )
        assert "sk-league-secret" not in out.read_text(encoding="utf-8")


class TestTheHarnessEntryPoint:
    """Driven through ``main``, because a harness nobody ran does not work."""

    def _play(self, tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        root = tmp_path / "arena"
        root.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.pop("COLLEAGUE_API_KEY", None)
        env.pop(al.LIVE_ARENA_ENV, None)
        env.pop(aa.LIVE_GATE_ENV, None)
        return subprocess.run(  # nosec B603
            [
                sys.executable,
                str(SOURCE),
                "play",
                "--root",
                str(root),
                "--league",
                FAKE_BIN,
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_play_runs_every_arm_and_prints_a_report(self, tmp_path: Path) -> None:
        done = self._play(tmp_path)
        assert done.returncode == 0, done.stderr
        report = json.loads(done.stdout)
        assert [cell["arm"] for cell in report["cells"]] == list(aa.ARM_ORDER)

    def test_play_writes_the_artifact_it_was_pointed_at(self, tmp_path: Path) -> None:
        out = tmp_path / "league.jsonl"
        done = self._play(tmp_path, "--arm", aa.ARM_HYBRID, "--out", str(out))
        assert done.returncode == 0, done.stderr
        kinds = {record["kind"] for record in aa.read_log(out)}
        assert {aa.KIND_PREAMBLE, aa.KIND_CALL, al.KIND_ROUTE, al.KIND_MATCH} <= kinds

    def test_plan_names_the_arms_the_rung_and_the_role_mapping(self, capsys: Any) -> None:
        assert al.main(["plan"]) == 0
        text = capsys.readouterr().out
        for arm_id in aa.ARM_ORDER:
            assert arm_id in text
        assert al.LEAGUE_LADDER[0].scenario in text
        assert "leader" in text.lower()

    def test_plan_json_is_machine_readable(self, capsys: Any) -> None:
        assert al.main(["plan", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert sorted(payload["arms"]) == sorted(aa.ARM_ORDER)
        assert payload["fanout"]["max_width"] == ot.MAX_FANOUT_WIDTH

    def test_an_unknown_rung_is_a_clean_error_not_a_traceback(self, capsys: Any) -> None:
        assert al.main(["play", "--rung", "nope", "--league", FAKE_BIN]) == 2
        captured = capsys.readouterr()
        assert captured.err.startswith("error: ")
        assert "hint: " in captured.err
        assert "Traceback" not in captured.err


# ═════════════════════════════════════════════════════════════════════════════
# criterion 2 — the hybrid's routing decision, per decision point, with reasons
# ═════════════════════════════════════════════════════════════════════════════


class TestHybridRoutingIsLogged:
    """Without this log the hybrid arm is unfalsifiable. So: read it back."""

    def test_every_decision_point_lands_exactly_one_routing_record(self, tmp_path: Path) -> None:
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        assert len(record.routes) == record.decisions
        keys = [(entry["round_index"], entry["unit_id"]) for entry in record.routes]
        assert len(set(keys)) == len(keys)

    def test_the_hybrid_both_routes_and_keeps_inside_one_match(self, tmp_path: Path) -> None:
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        assert record.routing_mix == [al.ROUTED_CORTEX, al.ROUTED_WORKER]
        assert record.degenerate_routing is False
        assert record.routed > 0
        assert record.kept > 0

    def test_a_routed_decision_names_the_worker_and_carries_the_models_reason(
        self, tmp_path: Path
    ) -> None:
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        routed = [e for e in record.routes if e["routed_to"] == al.ROUTED_WORKER]
        assert routed
        for entry in routed:
            assert entry["basis"] == al.BASIS_ORCHESTRATOR
            assert entry["reason"].strip(), "a routed decision with no stated reason"
            assert entry["unit_task_id"], "a routed decision that names no worker drive"

    def test_a_kept_decision_carries_the_reason_the_model_gave_for_keeping_it(
        self, tmp_path: Path
    ) -> None:
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        kept = [e for e in record.routes if e["routed_to"] == al.ROUTED_CORTEX]
        assert kept
        for entry in kept:
            assert entry["basis"] == al.BASIS_ORCHESTRATOR
            assert entry["reason"].strip(), "a kept decision with no stated reason"
            assert entry["unit_task_id"] == ""

    def test_the_routing_reason_is_the_models_words_not_the_harnesss(self, tmp_path: Path) -> None:
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        reasons = {entry["reason"] for entry in record.routes}
        assert al.SCRIPTED_ROUTE_REASON in reasons
        assert al.SCRIPTED_KEEP_REASON in reasons

    def test_the_hybrid_cannot_keep_a_unit_without_saying_why(self) -> None:
        surface = seat(aa.ARM_HYBRID, ("bluee-u1", "defender"))
        with pytest.raises(ToolError) as caught:
            surface.execute("order", {"unit_id": "bluee-u1", "menu_index": 0, "why": "because"})
        assert "kept_because" in str(caught.value)
        assert surface.orders == {}

    def test_a_kept_unit_with_a_reason_is_accepted_and_logged(self) -> None:
        surface = seat(aa.ARM_HYBRID, ("bluee-u1", "defender"))
        surface.execute(
            "order",
            {
                "unit_id": "bluee-u1",
                "menu_index": 0,
                "why": "hold the post",
                "kept_because": "contested: this one is mine",
            },
        )
        intent = surface.intents["bluee-u1"]
        assert intent.routed_to == al.ROUTED_CORTEX
        assert intent.basis == al.BASIS_ORCHESTRATOR
        assert intent.reason == "contested: this one is mine"

    def test_the_manager_cannot_order_a_unit_it_did_not_route(self) -> None:
        surface = seat(aa.ARM_MANAGER, ("bluee-u1", "defender"))
        with pytest.raises(ToolError) as caught:
            surface.execute("order", {"unit_id": "bluee-u1", "menu_index": 0, "why": "mine"})
        assert al.ROUTE_TOOL in str(caught.value)

    def test_the_manager_routes_every_unit_and_its_basis_says_the_arm_required_it(
        self, tmp_path: Path
    ) -> None:
        record, _ = play(aa.ARM_MANAGER, tmp_path)
        assert {entry["routed_to"] for entry in record.routes} == {al.ROUTED_WORKER}
        assert {entry["basis"] for entry in record.routes} == {al.BASIS_ARM_MANDATE}
        assert record.degenerate_routing is True

    @pytest.mark.parametrize("arm_id", aa.FLAT_ARMS)
    def test_a_flat_arm_records_the_architecture_as_the_basis_never_a_judgement(
        self, arm_id: str, tmp_path: Path
    ) -> None:
        record, _ = play(arm_id, tmp_path)
        assert record.routes
        for entry in record.routes:
            assert entry["basis"] == al.BASIS_ARCHITECTURE
            assert entry["routed_to"] == aa.ARMS[arm_id].top_level_role
            assert entry["reason"] == al.ARCHITECTURE_REASON[arm_id]

    def test_the_routing_record_carries_what_the_worker_proposed_and_what_was_ordered(
        self, tmp_path: Path
    ) -> None:
        record, _ = play(aa.ARM_MANAGER, tmp_path)
        for entry in record.routes:
            assert entry["proposed_index"] == 0, entry
            assert entry["ordered_index"] == 2, entry
            assert entry["overridden"] is True

    def test_a_kept_decision_has_no_proposal_to_override(self, tmp_path: Path) -> None:
        record, _ = play(aa.ARM_HYBRID, tmp_path)
        kept = [e for e in record.routes if e["routed_to"] == al.ROUTED_CORTEX]
        assert kept
        for entry in kept:
            assert entry["proposed_index"] is None
            assert entry["overridden"] is None

    def test_the_routing_table_reads_back_from_the_committed_artifact(self, tmp_path: Path) -> None:
        out = tmp_path / "league.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_HYBRID,),
            out=out,
        )
        table = al.routing_table(aa.read_log(out))
        assert table
        assert {row["routed_to"] for row in table} == {al.ROUTED_WORKER, al.ROUTED_CORTEX}
        assert all(row["reason"].strip() for row in table)

    def test_the_routes_verb_prints_the_table_a_reader_needs(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        out = tmp_path / "league.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_HYBRID,),
            out=out,
        )
        assert al.main(["routes", "--log", str(out)]) == 0
        text = capsys.readouterr().out
        assert al.SCRIPTED_KEEP_REASON in text
        assert al.SCRIPTED_ROUTE_REASON in text

    def test_a_hybrid_that_never_varies_its_routing_is_flagged_degenerate(self) -> None:
        """A hybrid that always routes is a manager; always keeps is arm E."""
        assert al.degenerate([al.ROUTED_WORKER, al.ROUTED_WORKER]) is True
        assert al.degenerate([al.ROUTED_CORTEX, al.ROUTED_CORTEX]) is True
        assert al.degenerate([al.ROUTED_WORKER, al.ROUTED_CORTEX]) is False
        assert al.degenerate([]) is True


# ═════════════════════════════════════════════════════════════════════════════
# the seat: one surface, four arms, and the authority line
# ═════════════════════════════════════════════════════════════════════════════


class TestTheSeatSurface:
    def test_only_the_orchestrated_arms_hold_the_routing_verb(self) -> None:
        for arm_id in aa.FLAT_ARMS:
            assert al.ROUTE_TOOL not in al.seat_tools(aa.ARMS[arm_id])
        for arm_id in (aa.ARM_MANAGER, aa.ARM_HYBRID):
            assert al.ROUTE_TOOL in al.seat_tools(aa.ARMS[arm_id])

    def test_order_is_the_only_verb_that_reaches_the_arena(self) -> None:
        assert al.LEAGUE_FINAL_AUTHORITY_TOOLS == (al.ORDER_TOOL,)
        for arm_id in aa.ARM_ORDER:
            surface = set(al.seat_tools(aa.ARMS[arm_id]))
            assert surface & set(al.LEAGUE_FINAL_AUTHORITY_TOOLS) == {al.ORDER_TOOL}

    def test_the_unit_surface_shares_no_verb_with_final_authority(self) -> None:
        assert set(al.UNIT_TOOLS) & set(al.LEAGUE_FINAL_AUTHORITY_TOOLS) == set()
        assert set(al.UNIT_TOOLS) == set(ot.WORKER_TOOLS)

    def test_an_unenumerated_verb_is_refused_not_answered(self) -> None:
        surface = seat(aa.ARM_EXISTING, ("bluee-u1", "defender"))
        with pytest.raises(UnknownToolError):
            surface.execute("shell", {})
        assert surface.refused == ["shell"]

    def test_a_flat_arm_cannot_route_even_if_it_asks(self) -> None:
        surface = seat(aa.ARM_EXISTING, ("bluee-u1", "defender"))
        with pytest.raises(UnknownToolError):
            surface.execute(al.ROUTE_TOOL, {"units": [{"unit_id": "bluee-u1", "reason": "x"}]})

    def test_ordering_a_unit_that_is_not_due_is_refused(self) -> None:
        surface = seat(aa.ARM_EXISTING, ("bluee-u1", "defender"))
        with pytest.raises(ToolError) as caught:
            surface.execute("order", {"unit_id": "ghost", "menu_index": 0, "why": "x"})
        assert "ghost" in str(caught.value)

    def test_an_out_of_range_menu_index_is_a_self_correcting_step(self) -> None:
        surface = seat(aa.ARM_EXISTING, ("bluee-u1", "defender"))
        with pytest.raises(ToolError):
            surface.execute("order", {"unit_id": "bluee-u1", "menu_index": 99, "why": "x"})
        assert surface.orders == {}

    def test_ordering_the_same_unit_twice_is_refused(self) -> None:
        surface = seat(aa.ARM_EXISTING, ("bluee-u1", "defender"))
        surface.execute("order", {"unit_id": "bluee-u1", "menu_index": 0, "why": "x"})
        with pytest.raises(ToolError):
            surface.execute("order", {"unit_id": "bluee-u1", "menu_index": 1, "why": "y"})

    def test_the_round_ends_when_the_last_due_unit_is_ordered(self) -> None:
        surface = seat(aa.ARM_EXISTING, ("bluee-u1", "defender"), ("bluee-u2", "scout"))
        first = surface.execute("order", {"unit_id": "bluee-u1", "menu_index": 0, "why": "x"})
        assert first.finished is False
        last = surface.execute("order", {"unit_id": "bluee-u2", "menu_index": 1, "why": "y"})
        assert last.finished is True

    def test_routing_an_unknown_unit_is_refused(self) -> None:
        surface = seat(aa.ARM_MANAGER, ("bluee-u1", "defender"))
        with pytest.raises(ToolError):
            surface.execute(al.ROUTE_TOOL, {"units": [{"unit_id": "ghost", "reason": "x"}]})

    def test_routing_without_a_reason_is_refused(self) -> None:
        surface = seat(aa.ARM_MANAGER, ("bluee-u1", "defender"))
        with pytest.raises(ToolError) as caught:
            surface.execute(al.ROUTE_TOOL, {"units": [{"unit_id": "bluee-u1"}]})
        assert "reason" in str(caught.value)

    def test_a_malformed_units_argument_is_refused_with_a_readable_message(self) -> None:
        surface = seat(aa.ARM_MANAGER, ("bluee-u1", "defender"))
        with pytest.raises(ToolError):
            surface.execute(al.ROUTE_TOOL, {"units": 7})

    def test_routing_the_same_unit_twice_is_refused(self) -> None:
        surface = seat(aa.ARM_MANAGER, ("bluee-u1", "defender"))
        surface.execute(al.ROUTE_TOOL, {"units": [{"unit_id": "bluee-u1", "reason": "a"}]})
        with pytest.raises(ToolError):
            surface.execute(al.ROUTE_TOOL, {"units": [{"unit_id": "bluee-u1", "reason": "b"}]})

    def test_the_batch_asks_for_one_worker_budget_per_routed_unit(self) -> None:
        surface = seat(
            aa.ARM_MANAGER,
            ("bluee-u1", "defender"),
            ("bluee-u2", "scout"),
            worker_max_steps=5,
        )
        outcome = surface.execute(
            al.ROUTE_TOOL,
            {
                "units": [
                    {"unit_id": "bluee-u1", "reason": "a"},
                    {"unit_id": "bluee-u2", "reason": "b"},
                ]
            },
        )
        assert outcome.spawn is not None
        assert outcome.spawn.max_steps == 10
        assert outcome.spawn.allowance == ot.NO_SPAWNS
        assert outcome.spawn.role == ot.ROLE_FANOUT
        assert len(outcome.spawn.context["subtasks"]) == 2


# ═════════════════════════════════════════════════════════════════════════════
# the fan-out: t4's accounting rule, honored rather than re-invented
# ═════════════════════════════════════════════════════════════════════════════


class TestTheFanoutAccounting:
    def test_a_round_drives_every_routed_unit_at_once(self, tmp_path: Path) -> None:
        """A barrier, not a high-water mark: scripted units finish too fast to overlap.

        Each unit's mind waits until every unit of the round has arrived. Driven
        serially the first one waits out the timeout and the round comes back
        broken, so this cannot pass by luck.
        """
        width = len(al.LEAGUE_LADDER[0].roles)
        barrier = threading.Barrier(width, timeout=20)

        def gated(messages: list[dict[str, Any]]) -> ModelResponse:
            barrier.wait()
            return al.scripted_unit(messages)

        cfg = config()
        log = al.ThreadSafeCallLog()
        arm = aa.ARMS[aa.ARM_MANAGER]
        seams = aa.ScriptedSeams(
            {aa.ROLE_CORTEX: al.scripted_commander(arm), aa.ROLE_WORKER: gated},
            config=cfg,
            log=log,
        )
        record, _ = play(aa.ARM_MANAGER, tmp_path, seams=seams, log=log)
        assert record.status == "finished"
        assert record.max_in_flight >= width
        assert record.degradation_codes == []

    def test_the_fanout_never_charges_more_than_the_grant_it_was_given(
        self, tmp_path: Path
    ) -> None:
        record, _ = play(aa.ARM_MANAGER, tmp_path)
        assert record.batches, "the manager arm dispatched no batch at all"
        for batch in record.batches:
            assert batch["committed"] <= batch["grant"]
        assert record.child_model_turns <= sum(batch["grant"] for batch in record.batches)

    def test_the_width_bound_is_t4s_and_refuses_the_overflow(self) -> None:
        specs = tuple((f"bluee-u{index}", "scout") for index in range(ot.MAX_FANOUT_WIDTH + 3))
        surface = seat(aa.ARM_MANAGER, *specs)
        surface.execute(
            al.ROUTE_TOOL,
            {"units": [{"unit_id": unit_id, "reason": "all of them"} for unit_id, _ in specs]},
        )
        batch = surface.batches[-1]
        plan = ot.plan_fanout(batch.subtasks, grant=batch.grant, stem="b")
        refused = [unit.refusal for unit in plan.refused]
        assert refused.count(ot.REFUSED_OVER_WIDTH) == 3
        assert len(plan.dispatched) == ot.MAX_FANOUT_WIDTH

    def test_a_unit_that_never_reports_is_visible_to_the_commander(self, tmp_path: Path) -> None:
        """t4's no-report degradation is inherited, not re-minted here."""

        def silent(_messages: list[dict[str, Any]]) -> ModelResponse:
            return ModelResponse(content="thinking about it", tool_calls=[])

        cfg = config()
        log = al.ThreadSafeCallLog()
        arm = aa.ARMS[aa.ARM_MANAGER]
        seams = aa.ScriptedSeams(
            {aa.ROLE_CORTEX: al.scripted_commander(arm), aa.ROLE_WORKER: silent},
            config=cfg,
            log=log,
        )
        record, _ = play(aa.ARM_MANAGER, tmp_path, seams=seams, log=log)
        assert ot.DEGRADED_WORKER_NO_REPORT in record.degradation_codes
        assert all(entry["proposed_index"] is None for entry in record.routes)
        assert record.no_order == 0, "a silent worker must not cost the arena its order"

    def test_the_accounting_rule_is_quoted_rather_than_restated(self) -> None:
        assert al.FANOUT_ACCOUNTING_RULE is ot.FANOUT_ACCOUNTING_RULE

    def test_the_call_log_mints_unique_indices_under_concurrency(self) -> None:
        log = al.ThreadSafeCallLog()
        ctx = aa.CallContext(
            arm=aa.ARM_MANAGER,
            rung="L1",
            problem="m",
            route=al.ROUTE_TEXT,
            senses_hash="h",
            live=False,
        )
        sampling = config().sampling_for(aa.ARM_MANAGER, aa.ROLE_WORKER)

        def hammer() -> None:
            for _ in range(40):
                log.mint(
                    ctx,
                    role=aa.ROLE_WORKER,
                    model="m",
                    sampling=sampling,
                    finish_reason="stop",
                    prompt_tokens=1,
                    completion_tokens=1,
                    reasoning_tokens=None,
                    token_detail=aa.TOKEN_DETAIL_SCRIPTED,
                    reasoning_chars=0,
                    content_chars=0,
                    seconds=0.0,
                    messages_in=1,
                    tool_calls=(),
                    retries=0,
                )

        threads = [threading.Thread(target=hammer) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(record.index for record in log.records) == list(range(240))

    def test_the_call_log_mints_exclusively(self, monkeypatch: Any) -> None:
        """The unique-index test above passes without the lock, so assert the lock.

        Under the GIL, ``index = len(records)`` and the append after it almost
        never interleave at this speed — the race is real but not reliably
        provoked, so a test that only checks the indices lets the lock be
        deleted. This one widens the window inside t5's own ``mint`` and asserts
        no two threads are ever in there together.
        """
        entered = 0
        peak = 0
        guard = threading.Lock()
        base = aa.CallLog.mint

        def slow(self: aa.CallLog, ctx: aa.CallContext, **kwargs: Any) -> aa.CallRecord:
            nonlocal entered, peak
            with guard:
                entered += 1
                peak = max(peak, entered)
            time.sleep(0.01)
            try:
                return base(self, ctx, **kwargs)
            finally:
                with guard:
                    entered -= 1

        monkeypatch.setattr(aa.CallLog, "mint", slow)
        log = al.ThreadSafeCallLog()
        ctx = aa.CallContext(
            arm=aa.ARM_MANAGER,
            rung="L1",
            problem="m",
            route=al.ROUTE_TEXT,
            senses_hash="h",
            live=False,
        )
        sampling = config().sampling_for(aa.ARM_MANAGER, aa.ROLE_WORKER)

        def once() -> None:
            log.mint(
                ctx,
                role=aa.ROLE_WORKER,
                model="m",
                sampling=sampling,
                finish_reason="stop",
                prompt_tokens=1,
                completion_tokens=1,
                reasoning_tokens=None,
                token_detail=aa.TOKEN_DETAIL_SCRIPTED,
                reasoning_chars=0,
                content_chars=0,
                seconds=0.0,
                messages_in=1,
                tool_calls=(),
                retries=0,
            )

        threads = [threading.Thread(target=once) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert peak == 1, f"{peak} threads were inside mint at once; the lock is gone"
        assert len(log.records) == 8


# ═════════════════════════════════════════════════════════════════════════════
# the perception seam t10 registers against
# ═════════════════════════════════════════════════════════════════════════════


class TestThePerceptionSeam:
    def test_exactly_one_route_ships_today_and_it_is_the_text_one(self) -> None:
        assert sorted(al.ROUTE_REGISTRY) == [al.ROUTE_TEXT]
        assert al.route_for(al.ROUTE_TEXT).why

    def test_the_text_route_carries_no_twin_key_and_says_so(self) -> None:
        seen = al.route_for(al.ROUTE_TEXT).describe(briefing(), "bluee")
        assert seen.route == al.ROUTE_TEXT
        assert seen.parts == ()
        assert seen.snapshot_hash == ""
        assert "MENU" in seen.text

    def test_registering_over_a_live_route_is_refused_without_replace(self) -> None:
        # Resolve the live route BEFORE the raises block, and keep it there.
        # `route_for` raises ValueError as well (on an unknown id), so calling
        # it inside the block lets this guard pass on the wrong axis: with a
        # cleared or clobbered ROUTE_REGISTRY, `route_for` throws, the block is
        # satisfied, and `register_route` — the call actually under test — never
        # runs. Hoisted, that same state fails loudly instead of passing green.
        live = al.route_for(al.ROUTE_TEXT)
        with pytest.raises(ValueError):
            al.register_route(live)

    def test_a_registered_route_rides_every_routing_record(self, tmp_path: Path) -> None:
        """The seam t10 needs: a route that carries a snapshot hash, end to end."""

        def describe(brief: Any, team_id: str) -> al.Perception:
            base = al.describe_text(brief, team_id)
            return al.Perception(route="twin", text=base.text, snapshot_hash="deadbeef")

        al.register_route(al.PerceptionRoute(id="twin", why="a t10 stand-in", describe=describe))
        try:
            record, _ = play(aa.ARM_HYBRID, tmp_path, route="twin")
            assert record.route == "twin"
            assert {entry["snapshot_hash"] for entry in record.routes} == {"deadbeef"}
        finally:
            al.ROUTE_REGISTRY.pop("twin", None)

    def test_an_unknown_route_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError) as caught:
            al.route_for("hologram")
        assert "hologram" in str(caught.value)

    def test_cells_are_keyed_by_route_so_a_second_route_grades_on_its_own_controls(
        self, tmp_path: Path
    ) -> None:
        out = tmp_path / "league.jsonl"
        al.run_series(
            config=config(),
            arena=FAKE_BIN,
            root=tmp_path / "arena",
            log=al.ThreadSafeCallLog(),
            arms=(aa.ARM_EXISTING,),
            out=out,
        )
        cells = [r for r in aa.read_log(out) if r["kind"] == aa.KIND_CELL]
        assert [cell["route"] for cell in cells] == [al.ROUTE_TEXT]


# ═════════════════════════════════════════════════════════════════════════════
# the gates: no live model, no live arena
# ═════════════════════════════════════════════════════════════════════════════


class TestTheGates:
    def test_the_arena_gate_is_the_repo_wide_one(self) -> None:
        assert al.LIVE_ARENA_ENV == "EMBODIMENT_LIVE_ARENA"
        assert al.LIVE_GATE_ENV == aa.LIVE_GATE_ENV == "EMBODIMENT_LIVE_RIG"

    def test_the_real_arena_binary_needs_the_arena_gate(self) -> None:
        with pytest.raises(al.LiveArenaClosed):
            al.resolve_arena(None, env={})
        with pytest.raises(al.LiveArenaClosed):
            al.resolve_arena("league", env={})
        with pytest.raises(al.LiveArenaClosed):
            al.resolve_arena("/usr/local/bin/league --json", env={})

    def test_an_explicit_stand_in_binary_needs_no_gate(self) -> None:
        assert al.resolve_arena(FAKE_BIN, env={}) == FAKE_BIN

    def test_the_gate_opens_the_real_binary(self) -> None:
        assert al.resolve_arena(None, env={al.LIVE_ARENA_ENV: "1"}) == al.DEFAULT_LEAGUE_BIN

    def test_the_cli_refuses_live_without_the_rig_gate(self, capsys: Any) -> None:
        assert al.main(["play", "--live", "--league", FAKE_BIN]) == 2
        assert aa.LIVE_GATE_ENV in capsys.readouterr().err

    def test_the_cli_still_refuses_live_with_both_gates_open(
        self, capsys: Any, monkeypatch: Any
    ) -> None:
        """t12 runs the series. t6 ships the lane and its scripted half."""
        monkeypatch.setenv(aa.LIVE_GATE_ENV, "1")
        monkeypatch.setenv(al.LIVE_ARENA_ENV, "1")
        assert al.main(["play", "--live", "--league", FAKE_BIN]) == 2
        assert "t12" in capsys.readouterr().err

    def test_an_unresolvable_role_is_absent_never_a_substitution(self) -> None:
        seams, resolutions = al.build_live_seams(
            config(), log=al.ThreadSafeCallLog(), roles=(aa.ROLE_CORTEX, aa.ROLE_WORKER), env={}
        )
        assert seams is None
        assert all(resolution.ok is False for resolution in resolutions)
        assert any(
            record.code == aa.DEGRADED_MODEL_ABSENT
            for resolution in resolutions
            for record in resolution.degradations
        )

    def test_no_model_id_is_hard_coded_in_the_lane(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        for literal in ("Qwen3.6", "sakamakismile", "unsloth/", "Gemma-4"):
            assert literal not in text, f"{literal!r} names a model this lane must read from config"

    def test_no_sampling_default_lives_in_the_lane(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        banned = ("TEMPERATURE", "MAX_TOKENS", "THINKING")
        offenders = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and any(word in target.id for word in banned)
        ]
        assert offenders == [], f"sampling belongs in the committed table: {offenders}"

    def test_the_lane_opens_no_socket_of_its_own(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        for banned in ("urllib", "http.client", "requests.", "import socket"):
            assert banned not in text, f"{banned} would be a second transport beside t2's"


# ═════════════════════════════════════════════════════════════════════════════
# the rung, the role mapping, and what a scripted run may claim
# ═════════════════════════════════════════════════════════════════════════════


class TestTheRungAndTheRoleMapping:
    def test_the_rung_gives_the_hybrid_room_to_route_and_to_keep(self) -> None:
        rung = al.LEAGUE_LADDER[0]
        assert len(rung.roles) >= 3
        routable = [role for role in rung.roles if role in al.SCRIPTED_ROUTABLE_ROLES]
        kept = [role for role in rung.roles if role not in al.SCRIPTED_ROUTABLE_ROLES]
        why = "a rung the hybrid cannot vary on measures nothing"
        assert routable, f"no routable role: {why}"
        assert kept, f"no kept role: {why}"

    def test_the_cortex_is_the_leader_seat_in_every_arm_that_has_one(self) -> None:
        for arm_id in (aa.ARM_EXISTING, aa.ARM_MANAGER, aa.ARM_HYBRID):
            assert aa.ARMS[arm_id].top_level_role == aa.ROLE_CORTEX
        assert aa.ARMS[aa.ARM_WORKER_SOLO].top_level_role == aa.ROLE_WORKER

    def test_the_worker_drives_every_unit_and_never_reaches_the_arena(self, tmp_path: Path) -> None:
        record, _ = play(aa.ARM_MANAGER, tmp_path)
        assert record.unit_drives == record.decisions
        assert set(al.UNIT_TOOLS) == {"report"}

    def test_the_model_declared_to_the_arena_is_the_one_that_drives_the_units(
        self, tmp_path: Path
    ) -> None:
        record, _ = play(aa.ARM_MANAGER, tmp_path)
        assert record.unit_model == al.roster_model("scripted:worker")

    def test_a_colon_in_a_model_id_cannot_break_the_roster_spec(self) -> None:
        assert ":" not in al.roster_model("scripted:worker")
        assert al.roster_model("some-vendor/Some-Model") == "some-vendor/Some-Model"

    def test_a_scripted_run_cannot_be_mistaken_for_a_measured_one(self, tmp_path: Path) -> None:
        record, attempt = play(aa.ARM_EXISTING, tmp_path)
        assert record.live is False
        assert attempt.graded["scripted"] is True
        assert al.SCRIPTED_NOTE in attempt.graded["note"]

    def test_the_proposal_reader_is_conservative(self) -> None:
        assert al.read_proposed_index("menu_index=2: because") == 2
        assert al.read_proposed_index("I like option 1 best") is None
        assert al.read_proposed_index("") is None
        assert al.read_proposed_index("menu_index=abc") is None

    def test_the_round_view_round_trips_through_its_own_reader(self) -> None:
        arm = aa.ARMS[aa.ARM_HYBRID]
        units = units_of(("bluee-u1", "defender"), ("bluee-u2", "scout"))
        view = al.round_view(arm=arm, match_id="m", team_id="bluee", round_index=0, units=units)
        seen = al.read_round(al.round_instruction(view, arm=arm))
        assert [entry["unit_id"] for entry in seen["units"]] == ["bluee-u1", "bluee-u2"]
        assert seen["units"][0]["options"] == 3

    def test_absent_identity_leaves_the_seat_prompt_byte_identical(self) -> None:
        for arm_id in aa.ARM_ORDER:
            base = al.system_for(aa.ARMS[arm_id])
            assert aa.top_level_prompt(base, identity=None) == base


class TestTheScriptedMinds:
    def test_a_scripted_unit_reports_and_never_reaches_for_a_second_authority(self) -> None:
        reply = al.scripted_unit([{"role": "user", "content": "Subtask: pick one"}])
        assert [call.name for call in reply.tool_calls] == ["report"]

    def test_a_worker_that_reaches_for_order_is_refused_not_answered(self) -> None:
        executor = ot.WorkerExecutor(subtask="s")
        with pytest.raises(UnknownToolError):
            executor.execute(al.ORDER_TOOL, {"unit_id": "x", "menu_index": 0})

    def test_the_scripted_commander_answers_from_the_round_it_was_handed(self) -> None:
        arm = aa.ARMS[aa.ARM_HYBRID]
        units = units_of(("bluee-u1", "defender"), ("bluee-u2", "scout"))
        text = al.round_instruction(
            al.round_view(arm=arm, match_id="m", team_id="bluee", round_index=0, units=units),
            arm=arm,
        )
        reply = al.scripted_commander(arm)([{"role": "user", "content": text}])
        assert [call.name for call in reply.tool_calls] == [al.ROUTE_TOOL]
        routed = reply.tool_calls[0].arguments["units"]
        assert [entry["unit_id"] for entry in routed] == ["bluee-u2"]

    def test_the_scripted_commander_keeps_the_unit_it_did_not_route(self) -> None:
        arm = aa.ARMS[aa.ARM_HYBRID]
        units = units_of(("bluee-u1", "defender"), ("bluee-u2", "scout"))
        text = al.round_instruction(
            al.round_view(arm=arm, match_id="m", team_id="bluee", round_index=0, units=units),
            arm=arm,
        )
        reply = al.scripted_commander(arm)(
            [
                {"role": "user", "content": text},
                {"role": "tool", "content": f"{al.ROUTE_MARKER} bluee-u2"},
            ]
        )
        call: ToolCall = reply.tool_calls[0]
        assert call.name == al.ORDER_TOOL
        assert call.arguments["unit_id"] == "bluee-u1"
        assert call.arguments["kept_because"] == al.SCRIPTED_KEEP_REASON
