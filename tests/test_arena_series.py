"""t19 — the arena series runner, driven hermetically through its real entry point.

t12 committed challenge harnesses that could not run at all, and nobody noticed
until t18 tried to use them. The runner is this task's deliverable, so the
acceptance bar is that it is **driven end to end here**, not that its constants
look right.

Every test is hermetic: the seat is replaced by a stub that prints a
seat-shaped report, so the matrix, the resumption and the grader are all
exercised without a model or an arena.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import arena_series  # noqa: E402
from examples.arena_series import (  # noqa: E402
    ARM_COMMAND,
    ARM_RESIDENT,
    CELLS,
    OBJECTIVE,
    PAIR_CONTROL,
    PAIR_MEMORY,
    SEEDS,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_UNREADABLE,
    VERDICTS,
    completed_keys,
    continuity_specs,
    grade_continuity,
    matrix_specs,
    seat_argv,
)


def _leg2(
    *,
    ok: bool = True,
    seen: list[Any] | None = None,
    directive: bool = False,
    error: str = "",
) -> dict[str, Any]:
    turns = [
        {"directive_given": directive, "objective_seen": s}
        for s in (seen if seen is not None else [OBJECTIVE, OBJECTIVE])
    ]
    record: dict[str, Any] = {"ok": ok, "turns": turns}
    if error:
        record["error"] = error
    return record


# ── the matrix is the one in the pre-registration ─────────────────────────────


class TestTheMatrixMatchesWhatWasPreRegistered:
    def test_four_cells_named_RO_RM_CO_CM(self) -> None:
        assert sorted(CELLS.values()) == ["CM", "CO", "RM", "RO"]
        assert CELLS[(ARM_RESIDENT, False)] == "RO"
        assert CELLS[(ARM_RESIDENT, True)] == "RM"
        assert CELLS[(ARM_COMMAND, False)] == "CO"
        assert CELLS[(ARM_COMMAND, True)] == "CM"

    def test_every_cell_is_played_at_every_seed(self) -> None:
        specs = list(matrix_specs(3))
        assert len(specs) == 12
        by_cell: dict[str, list[int]] = {}
        for spec in specs:
            by_cell.setdefault(spec.cell, []).append(spec.seed)
        assert set(by_cell) == set(CELLS.values())
        # The same three scenarios in all four cells — so a cell-to-cell
        # difference cannot be a scenario difference.
        for cell, seeds in by_cell.items():
            assert seeds == list(SEEDS), f"{cell} played {seeds}"

    def test_match_keys_are_unique(self) -> None:
        specs = list(matrix_specs(3)) + list(continuity_specs(3))
        keys = [s.key for s in specs]
        assert len(keys) == len(set(keys))


class TestTheContinuityArmHasItsControl:
    def test_each_pair_is_two_legs_and_only_leg_one_gets_the_directive(self) -> None:
        specs = list(continuity_specs(1))
        assert len(specs) == 4  # memory pair + control pair, two legs each
        for spec in specs:
            assert bool(spec.directive) is (spec.leg == 1), spec

    def test_the_memory_pair_shares_a_store_and_the_control_pair_does_not(
        self, tmp_path: Path
    ) -> None:
        specs = {(s.pair, s.leg): s for s in continuity_specs(1)}
        mem1, mem2 = specs[(PAIR_MEMORY, 1)], specs[(PAIR_MEMORY, 2)]
        ctl1, ctl2 = specs[(PAIR_CONTROL, 1)], specs[(PAIR_CONTROL, 2)]
        assert arena_series._store_for(tmp_path, mem1) == arena_series._store_for(tmp_path, mem2)
        assert arena_series._store_for(tmp_path, ctl1) != arena_series._store_for(tmp_path, ctl2)

    def test_the_control_is_what_makes_a_pass_mean_anything(self, tmp_path: Path) -> None:
        """If the control shared a store it would not be a control at all."""
        ctl2 = next(s for s in continuity_specs(1) if s.pair == PAIR_CONTROL and s.leg == 2)
        assert ctl2.share_store_with == ""
        assert "control" in arena_series._store_for(tmp_path, ctl2).as_posix()


# ── the command line the runner builds ────────────────────────────────────────


class TestTheSeatIsInvokedCorrectly:
    def _args(self, **over: Any) -> Any:
        import argparse

        base = dict(
            max_turns=3,
            live=False,
            base_url="http://x/v1",
            cortex_model="c",
            muse_model="m",
        )
        base.update(over)
        return argparse.Namespace(**base)

    def test_muse_on_passes_the_flag_and_muse_off_omits_it(self, tmp_path: Path) -> None:
        specs = {s.cell: s for s in matrix_specs(1)}
        on = seat_argv(specs["RM"], tmp_path, self._args())
        off = seat_argv(specs["RO"], tmp_path, self._args())
        assert "--muse" in on
        assert "--muse" not in off

    def test_the_arm_reaches_the_seat_verbatim(self, tmp_path: Path) -> None:
        specs = {s.cell: s for s in matrix_specs(1)}
        for cell, arm in (("RO", ARM_RESIDENT), ("CO", ARM_COMMAND)):
            argv = seat_argv(specs[cell], tmp_path, self._args())
            assert argv[argv.index("--arm") + 1] == arm

    def test_an_empty_directive_is_passed_explicitly_not_omitted(self, tmp_path: Path) -> None:
        """Omitting it would fall back to the seat's default — voiding the arm.

        Leg 2 receiving a directive is precisely the thing that makes the
        continuity result meaningless, so the empty string is passed on the wire.
        """
        leg2 = next(s for s in continuity_specs(1) if s.leg == 2)
        argv = seat_argv(leg2, tmp_path, self._args())
        assert "--directive" in argv
        assert argv[argv.index("--directive") + 1] == ""

    def test_live_flags_are_absent_unless_live(self, tmp_path: Path) -> None:
        spec = next(matrix_specs(1))
        assert "--live" not in seat_argv(spec, tmp_path, self._args())
        assert "--live" in seat_argv(spec, tmp_path, self._args(live=True))


# ── the grader ────────────────────────────────────────────────────────────────


class TestTheContinuityGrader:
    def test_an_objective_carried_with_no_directive_passes(self) -> None:
        verdict, why = grade_continuity(_leg2())
        assert verdict == VERDICT_PASS
        assert "only source was the store" in why

    def test_no_objective_at_all_fails(self) -> None:
        verdict, _ = grade_continuity(_leg2(seen=[None, None]))
        assert verdict == VERDICT_FAIL

    def test_an_inconsistent_objective_is_unreadable_not_a_pass(self) -> None:
        verdict, why = grade_continuity(_leg2(seen=[OBJECTIVE, None]))
        assert verdict == VERDICT_UNREADABLE
        assert "inconsistent" in why

    def test_a_leg_two_that_was_given_a_directive_voids_the_arm(self) -> None:
        """It could not have needed memory, so it cannot evidence memory."""
        verdict, why = grade_continuity(_leg2(directive=True))
        assert verdict == VERDICT_UNREADABLE
        assert "void" in why

    def test_a_leg_two_that_did_not_complete_is_unreadable(self) -> None:
        verdict, _ = grade_continuity(_leg2(ok=False, error="boom"))
        assert verdict == VERDICT_UNREADABLE

    def test_a_leg_two_with_no_turns_is_unreadable(self) -> None:
        verdict, _ = grade_continuity({"ok": True, "turns": []})
        assert verdict == VERDICT_UNREADABLE

    def test_every_verdict_is_declared(self) -> None:
        assert set(VERDICTS) == {VERDICT_PASS, VERDICT_FAIL, VERDICT_UNREADABLE}


# ── the runner, end to end, against a stub seat ───────────────────────────────


STUB_SEAT = '''#!/usr/bin/env python3
"""A seat-shaped stub: prints a report, touches no model and no arena."""
import json, os, sys

argv = sys.argv[1:]
def opt(name, default=""):
    return argv[argv.index(name) + 1] if name in argv else default

match_id = opt("--match-id")
directive = opt("--directive")
turns = int(opt("--max-turns", "3"))
# A leg-2 match (no directive) still "remembers" the objective when its store
# holds leg 1's record; the stub models that by looking for a marker file.
store = os.path.join(opt("--store"), "seeded")
seen = "cp-west" if (directive or os.path.exists(store)) else None
if directive:
    os.makedirs(opt("--store"), exist_ok=True)
    open(store, "w").close()

print(json.dumps({
    "arm": opt("--arm"),
    "muse": "--muse" in argv,
    "turns": [
        {"directive_given": bool(directive) and i == 0, "objective_seen": seen,
         "pid": 1000 + i, "orders": {"plan": "advance"}}
        for i in range(turns)
    ],
    "match": {
        "status": "active", "winner": None, "turn": turns, "turns_played": turns,
        "score": {}, "replay_sha256": "deadbeef" + match_id,
        "driver_kinds": {"blue": "resident" if opt("--arm") == "resident" else "stateless",
                         "red": "bot"},
    },
    "degradations": [],
}))
'''


@pytest.fixture
def stub_seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    seat = tmp_path / "stub_seat.py"
    seat.write_text(STUB_SEAT, encoding="utf-8")
    monkeypatch.setattr(arena_series, "SEAT", seat)
    return seat


class TestTheRunnerRuns:
    def test_the_whole_matrix_runs_and_records_one_line_per_match(
        self, tmp_path: Path, stub_seat: Path
    ) -> None:
        out = tmp_path / "results.jsonl"
        rc = arena_series.main(
            ["--root", str(tmp_path / "root"), "--out", str(out), "--n", "2", "--only", "matrix"]
        )
        assert rc == 0
        records = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        assert len(records) == 8  # 4 cells x n=2
        assert {r["cell"] for r in records} == set(CELLS.values())
        assert all(r["ok"] for r in records)

    def test_residency_reaches_the_record_from_the_seats_own_report(
        self, tmp_path: Path, stub_seat: Path
    ) -> None:
        out = tmp_path / "results.jsonl"
        arena_series.main(
            ["--root", str(tmp_path / "root"), "--out", str(out), "--n", "1", "--only", "matrix"]
        )
        records = {
            json.loads(ln)["cell"]: json.loads(ln)
            for ln in out.read_text(encoding="utf-8").splitlines()
            if ln
        }
        assert records["RO"]["driver_kinds"]["blue"] == "resident"
        assert records["CO"]["driver_kinds"]["blue"] == "stateless"

    def test_the_continuity_pair_passes_and_its_control_fails(
        self, tmp_path: Path, stub_seat: Path
    ) -> None:
        """The stub only 'remembers' when leg 2 shares leg 1's store.

        So this simultaneously exercises the grader and proves the control arm
        is wired to a genuinely separate store — if it were not, the control
        would pass too and the whole arm would be meaningless.
        """
        out = tmp_path / "results.jsonl"
        arena_series.main(
            [
                "--root",
                str(tmp_path / "root"),
                "--out",
                str(out),
                "--n",
                "1",
                "--only",
                "continuity",
            ]
        )
        by_key = {
            json.loads(ln)["match_key"]: json.loads(ln)
            for ln in out.read_text(encoding="utf-8").splitlines()
            if ln
        }
        memory_verdict, _ = grade_continuity(by_key["cont-memory-r0-leg2"])
        control_verdict, _ = grade_continuity(by_key["cont-control-r0-leg2"])
        assert memory_verdict == VERDICT_PASS
        assert control_verdict == VERDICT_FAIL


class TestResumption:
    """The series is hours long and runs unattended. Something will go wrong."""

    def test_a_second_run_replays_nothing(self, tmp_path: Path, stub_seat: Path) -> None:
        out = tmp_path / "results.jsonl"
        root = tmp_path / "root"
        arena_series.main(["--root", str(root), "--out", str(out), "--n", "1", "--only", "matrix"])
        first = out.read_text(encoding="utf-8")
        arena_series.main(["--root", str(root), "--out", str(out), "--n", "1", "--only", "matrix"])
        assert out.read_text(encoding="utf-8") == first, "a completed match was replayed"

    def test_an_interrupted_series_continues_where_it_stopped(
        self, tmp_path: Path, stub_seat: Path
    ) -> None:
        out = tmp_path / "results.jsonl"
        root = tmp_path / "root"
        specs = list(matrix_specs(2))
        # Simulate dying after 3 of 8 matches.
        with out.open("w", encoding="utf-8") as handle:
            for spec in specs[:3]:
                handle.write(json.dumps({"match_key": spec.key, "ok": True}) + "\n")

        arena_series.main(["--root", str(root), "--out", str(out), "--n", "2", "--only", "matrix"])
        records = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        keys = [r["match_key"] for r in records]
        assert len(keys) == len(specs)
        assert len(keys) == len(set(keys)), "resumption duplicated a match"

    def test_a_corrupt_line_does_not_lose_the_rest_of_the_ledger(self, tmp_path: Path) -> None:
        out = tmp_path / "results.jsonl"
        out.write_text('{"match_key": "a"}\nnot json\n{"match_key": "b"}\n', encoding="utf-8")
        assert completed_keys(out) == {"a", "b"}

    def test_an_absent_ledger_is_an_empty_set_not_a_crash(self, tmp_path: Path) -> None:
        assert completed_keys(tmp_path / "nope.jsonl") == set()


class TestTheStoreIsNeverSomewhereCommittable:
    def test_a_root_inside_a_git_work_tree_is_refused(
        self, tmp_path: Path, stub_seat: Path
    ) -> None:
        import subprocess  # nosec B404

        subprocess.run(  # nosec B603 B607
            ["git", "init", "-q", str(tmp_path)], check=True, timeout=60
        )
        # argv is built OUTSIDE the block so only `main` can raise inside it —
        # otherwise a SystemExit from the setup would pass this test for the
        # wrong reason.
        argv = ["--root", str(tmp_path / "root"), "--out", str(tmp_path / "o.jsonl")]
        with pytest.raises(SystemExit) as excinfo:
            arena_series.main(argv)
        assert "git work tree" in str(excinfo.value)


class TestAFailingMatchIsDataNotADiscard:
    """Rule 1 of the pre-registration, enforced rather than trusted."""

    def test_a_seat_that_dies_is_recorded_and_the_series_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seat = tmp_path / "dying_seat.py"
        seat.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
        monkeypatch.setattr(arena_series, "SEAT", seat)
        out = tmp_path / "results.jsonl"
        rc = arena_series.main(
            ["--root", str(tmp_path / "root"), "--out", str(out), "--n", "1", "--only", "matrix"]
        )
        assert rc == 0, "one dead match must not abort the series"
        records = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        assert len(records) == 4
        assert all(r["ok"] is False for r in records)
        assert all(r["returncode"] == 3 for r in records)

    def test_an_unparsable_report_is_recorded_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seat = tmp_path / "noisy_seat.py"
        seat.write_text("print('not json at all')\n", encoding="utf-8")
        monkeypatch.setattr(arena_series, "SEAT", seat)
        out = tmp_path / "results.jsonl"
        arena_series.main(
            ["--root", str(tmp_path / "root"), "--out", str(out), "--n", "1", "--only", "matrix"]
        )
        records = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        assert all("unparsable" in r["error"] for r in records)


# ── the real rig (opt-in) ─────────────────────────────────────────────────────


LIVE_ARENA = os.environ.get("EMBODIMENT_LIVE_ARENA") == "1"


@pytest.mark.skipif(not LIVE_ARENA, reason="set EMBODIMENT_LIVE_ARENA=1 to play the real arena")
class TestLiveArenaSeries:
    """One real match through the runner, offline-arena but real league."""

    def test_the_runner_drives_the_real_seat(self, tmp_path: Path) -> None:
        out = tmp_path / "results.jsonl"
        rc = arena_series.main(
            [
                "--root",
                str(tmp_path / "root"),
                "--out",
                str(out),
                "--n",
                "1",
                "--only",
                "matrix",
                "--max-turns",
                "1",
            ]
        )
        assert rc == 0
        records = [json.loads(ln) for ln in out.read_text(encoding="utf-8").splitlines() if ln]
        assert records
        assert all(r["ok"] for r in records), [r.get("error") for r in records if not r.get("ok")]
        assert all(r["replay_sha256"] for r in records)
