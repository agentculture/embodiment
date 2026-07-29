"""Tests for the ``league_seat`` arena host (task t14).

This is embodiment's first *adversarial* consumer, so these tests are the
acceptance criteria, not decoration:

* **A full match completes in both residency arms, through the public CLI
  only.** Every arena call is asserted to be one of league's documented public
  verbs, and the residency is asserted to be recorded in the match log three
  independent ways — league's own ``driver_kinds`` header echo, this host's arm
  name, and the per-turn pid.
* **No arena-specific code or dependency lands inside ``embodiment/``.** An AST
  guard over the package, in the spirit of ``tests/test_no_shell_host.py``: the
  identifier is banned, prose naming the boundary is not.
* **In the command arm, all per-turn continuity rides the pad and eidetic.** A
  fresh process per turn, and a four-way control experiment — pad+store, pad
  only, store only, neither — where only the last one is an amnesiac.
* **In the resident arm, the muse thread lives across turns.** Proved by a
  runner id minted once and stamped on every turn record, not by racing a
  counter.
* **Hermetic by default.** No live model, no network, and **no real league
  binary**: ``tests/fake_league.py`` answers the exact public subset the seat
  calls. ``urllib.request.urlopen`` is replaced with a bomb for the whole
  in-process suite; classes prefixed ``TestLive`` opt out by name.
"""

from __future__ import annotations

import ast
import json
import os
import shutil

# Both residency arms need real processes, not two calls in one interpreter.
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

from examples import league_seat

REPO_ROOT = Path(__file__).resolve().parents[1]
EMBODIMENT_ROOT = REPO_ROOT / "embodiment"
SEAT = REPO_ROOT / "examples" / "league_seat.py"
FAKE_LEAGUE = REPO_ROOT / "tests" / "fake_league.py"

#: What ``--league-bin`` is pointed at hermetically. Split with ``shlex`` by the
#: host, never handed to a shell.
FAKE_BIN = f"{sys.executable} {FAKE_LEAGUE}"

#: The control point the operator's directive names. It exists on the fake
#: board and on the real ``skirmish-1`` board alike.
OBJECTIVE = "cp-west"

#: Every verb the seat is allowed to reach the arena with. A call outside this
#: set means the host stopped using the public surface.
PUBLIC_VERBS = {
    ("team", "register"),
    ("match", "new"),
    ("match", "show"),
    ("match", "act"),
    ("match", "score"),
    ("match", "replay"),
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _env(*, live: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    # A developer's ambient store override would muddy what these tests prove.
    env.pop("EIDETIC_DATA_DIR", None)
    if not live:
        # Prove the hermetic path needs no key even on a machine that has one.
        env.pop(league_seat.API_KEY_ENV, None)
    return env


def _play(home: Path, arm: str, *extra: str, expect: int = 0) -> dict[str, Any]:
    """Play a whole match in a REAL separate process; return its JSON report."""
    cmd = [
        sys.executable,
        str(SEAT),
        "play",
        "--arm",
        arm,
        "--store",
        str(home / "memory"),
        "--workdir",
        str(home / "arena"),
        "--league-bin",
        FAKE_BIN,
        "--json",
        *extra,
    ]
    # Fixed argv, no shell, interpreter taken from sys.executable.
    proc = subprocess.run(  # nosec B603
        cmd,
        capture_output=True,
        text=True,
        cwd=str(home.parent),
        env=_env(),
        timeout=600,
        check=False,
    )
    detail = (
        f"exit {proc.returncode}\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    )
    assert proc.returncode == expect, detail
    return json.loads(proc.stdout)


def _args(*argv: str) -> Any:
    """Build the seat's own argument namespace, paths resolved as ``main`` does."""
    return league_seat.resolve_paths(league_seat.build_parser().parse_args(list(argv)))


def _turn_args(home: Path, *extra: str) -> Any:
    return _args(
        "turn",
        "--team",
        "blue",
        "--store",
        str(home / "memory"),
        "--pad",
        str(home / "pad.jsonl"),
        "--workdir",
        str(home / "work"),
        *extra,
    )


def _board(home: Path, *, match_id: str = "probe") -> dict[str, Any]:
    """A live board from the fake arena, through the seat's own public seam."""
    cli = league_seat.LeagueCli(binary=FAKE_BIN, workdir=home / "arena")
    for team, letter in (("blue", "b"), ("red", "r")):
        cli.register_team(
            team,
            name=team,
            agents=[f"{letter}{i + 1}:m:{r}" for i, r in enumerate(league_seat.ROSTER_ROLES)],
        )
    cli.new_match(
        match_id=match_id,
        scenario="skirmish-1",
        teams=["blue", "red"],
        seed=7,
        drivers={"blue": "stateless", "red": "bot"},
    )
    return cli.show(match_id)


def _log_lines(report: dict[str, Any]) -> list[dict[str, Any]]:
    text = Path(report["log"]).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _actions(turn: dict[str, Any]) -> list[str]:
    return [str(order.get("action")) for order in turn["orders"].get("actions", [])]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """No in-process test may dial anything. Live classes opt out by name.

    Matched on the ``TestLive`` prefix rather than one exact class, exactly as
    ``tests/test_demo_greenhouse.py`` does: a live class named anything else
    silently keeps the bomb, and its "live" assertions then pass or fail against
    the fixture instead of a real endpoint — which is how a dead-endpoint test
    can go green without ever dialling one.
    """
    if "TestLive" in request.node.nodeid:
        return
    import urllib.request

    def _bomb(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the hermetic path must never dial a network")

    monkeypatch.setattr(urllib.request, "urlopen", _bomb)


# ── a full match, in both arms, through the public CLI only ──────────────────


class TestFullMatchBothArms:
    """The headline acceptance criterion, once per residency arm."""

    def test_the_resident_arm_plays_a_whole_match_in_one_process(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "resident", league_seat.ARM_RESIDENT)

        assert report["match"]["status"] == "finished"
        assert report["match"]["turns_played"] == 4
        # league's OWN record of who was driving, echoed from `match new --json`.
        assert report["match"]["driver_kinds"]["blue"] == "resident"
        # One drive spanned the whole match, and it finished rather than
        # running out of budget.
        assert report["drive"]["scope"] == "match"
        assert report["drive"]["exit_reason"] == "finished"
        assert report["drive"]["tools"].count("submit") == 4
        # Residency, as the artifact itself shows it: one process throughout.
        assert {turn["pid"] for turn in report["turns"]} == {report["pid"]}

    def test_the_command_arm_plays_a_whole_match_one_process_per_turn(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "command", league_seat.ARM_COMMAND)

        assert report["match"]["status"] == "finished"
        assert report["match"]["turns_played"] == 4
        assert report["match"]["driver_kinds"]["blue"] == "stateless"
        # A different interpreter every turn, and never the parent's.
        pids = [turn["pid"] for turn in report["turns"]]
        assert len(set(pids)) == len(pids)
        assert report["pid"] not in pids
        # Each turn was its own drive, and each one finished.
        assert [turn["drive"]["exit_reason"] for turn in report["turns"]] == ["finished"] * 4
        assert report["drive"] is None

    def test_both_arms_reach_the_arena_only_through_public_verbs(self, tmp_path: Path) -> None:
        for arm in league_seat.ARMS:
            report = _play(tmp_path / arm, arm, "--max-turns", "2")
            for call in report["league_calls"]:
                assert tuple(call[:2]) in PUBLIC_VERBS, f"{arm}: non-public arena call {call}"

    def test_the_seat_never_writes_a_league_store_into_the_repo(self, tmp_path: Path) -> None:
        _play(tmp_path / "hygiene", league_seat.ARM_COMMAND, "--max-turns", "1")
        assert not (REPO_ROOT / ".league").exists()


# ── the match log is the evidence ────────────────────────────────────────────


class TestMatchLog:
    """A result whose residency you cannot read off the artifact is not evidence."""

    def test_configuration_is_written_before_the_first_result_line(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "log", league_seat.ARM_RESIDENT, "--max-turns", "2")
        lines = _log_lines(report)

        assert lines[0]["kind"] == "config", "the first line must be the configuration"
        assert all(line["kind"] != "turn" for line in lines[:1])
        config = lines[0]["config"]
        # The preamble examples/challenge_config.py owns — per-role temperature
        # separately, so it can never again be the hidden variable it was.
        assert config["cortex_model"] == league_seat.SCRIPTED_CORTEX
        assert "cortex_temperature" in config and "muse_temperature" in config
        # ...written to its own file too, before the match started.
        assert Path(report["config_log"]).exists()
        assert json.loads(Path(report["config_log"]).read_text(encoding="utf-8")) == config

    def test_the_log_records_residency_seeds_models_and_every_turn(self, tmp_path: Path) -> None:
        report = _play(
            tmp_path / "log2", league_seat.ARM_COMMAND, "--seed", "23", "--max-turns", "2"
        )
        lines = _log_lines(report)
        arena = lines[0]["arena"]

        assert arena["arm"] == league_seat.ARM_COMMAND
        assert arena["residency"] == "stateless"
        assert arena["driver_kinds"] == {"blue": "stateless", "red": "bot"}
        assert arena["seed"] == 23
        assert arena["muse"] is False

        turns = [line for line in lines if line["kind"] == "turn"]
        assert [t["turn"] for t in turns] == [0, 1]
        assert all(t["mind"]["cortex"] == league_seat.SCRIPTED_CORTEX for t in turns)

        closing = lines[-1]
        assert closing["kind"] == "match"
        assert closing["arm"] == league_seat.ARM_COMMAND
        assert closing["replay_sha256"] == report["match"]["replay_sha256"]

    def test_the_replay_hash_backs_a_comparison(self, tmp_path: Path) -> None:
        """Two identical seeded runs hash identically; a different seed does not."""
        first = _play(tmp_path / "a", league_seat.ARM_RESIDENT, "--seed", "5")
        second = _play(tmp_path / "b", league_seat.ARM_RESIDENT, "--seed", "5")
        other = _play(tmp_path / "c", league_seat.ARM_RESIDENT, "--seed", "6")

        assert first["match"]["replay_sha256"] == second["match"]["replay_sha256"]
        assert first["match"]["replay_sha256"] != other["match"]["replay_sha256"]

    def test_every_degradation_reaches_the_log(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "deg", league_seat.ARM_RESIDENT, "--max-turns", "1")
        # Nothing degraded here, and the field exists anyway: a host that always
        # iterates has one fewer special case than one that sometimes gets None.
        assert isinstance(report["degradations"], list)


class TestExitCodeIsAboutTheSeatNotTheArena:
    """A bug the real arena found, pinned so it cannot come back.

    The first version exited non-zero unless the status was ``complete`` or
    ``active``. league says ``finished`` (``league/engine/state.py:22``), so a
    match played perfectly for all thirty turns reported failure.
    """

    @pytest.mark.parametrize("status", ["pending", "active", "finished", "complete"])
    def test_no_arena_status_word_is_a_failure_on_its_own(self, status: str) -> None:
        report = {"match": {"status": status}, "turns": [], "drive": None}
        assert league_seat.drive_aborted(report) is False

    def test_an_aborted_resident_drive_is_a_failure(self) -> None:
        assert league_seat.drive_aborted({"drive": {"aborted": "seam died"}, "turns": []}) is True

    def test_an_aborted_command_turn_is_a_failure(self) -> None:
        report = {"drive": None, "turns": [{"drive": {"aborted": "seam died"}}]}
        assert league_seat.drive_aborted(report) is True

    def test_a_text_mode_run_exits_zero_and_prints_no_json(self, tmp_path: Path) -> None:
        home = tmp_path / "text"
        home.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(SEAT),
            "play",
            "--arm",
            league_seat.ARM_RESIDENT,
            "--store",
            str(home / "memory"),
            "--workdir",
            str(home / "arena"),
            "--league-bin",
            FAKE_BIN,
        ]
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True, cwd=str(home), env=_env(), timeout=300
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "league seat" in proc.stdout
        assert "replay sha256" in proc.stdout
        with pytest.raises(json.JSONDecodeError):
            json.loads(proc.stdout)


# ── no arena code, and no arena dependency, inside embodiment/ ───────────────


def _league_identifiers(tree: ast.AST) -> set[str]:
    """Every place ``league`` appears as *code* rather than prose.

    AST-based on purpose, exactly as ``tests/test_no_shell_host.py`` argues: the
    boundary "the arena is a tool surface a host wires up, never a dependency"
    deserves to be documented, and a substring scan would make documenting it
    fail the build.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == "league"}
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "league":
                found.add(node.module or "")
        elif isinstance(node, ast.Name) and node.id == "league":
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr == "league":
            found.add(node.attr)
    return found


class TestNoArenaCouplingInEmbodiment:
    """The arena is a host's tool surface. embodiment must not know it exists."""

    def test_no_module_under_embodiment_references_league_in_code(self) -> None:
        for path in sorted(EMBODIMENT_ROOT.rglob("*.py")):
            refs = _league_identifiers(ast.parse(path.read_text(encoding="utf-8")))
            rel = path.relative_to(REPO_ROOT)
            assert not refs, f"{rel} references league in code: {sorted(refs)}"

    def test_prose_may_name_the_boundary(self) -> None:
        documented = ast.parse(
            '"""league-of-agents is a host\'s tool surface, not a dependency."""\n'
            "# the league seat lives under examples/, deliberately\n"
            "VALUE = 1\n"
        )
        assert not _league_identifiers(documented)

    def test_the_guard_still_catches_a_real_coupling(self) -> None:
        for snippet in (
            "import league",
            "import league.harness",
            "from league import harness",
            "from league.engine.state import MatchState",
            "state = league.harness.run_match(cfg)",
        ):
            assert _league_identifiers(ast.parse(snippet)), f"missed: {snippet}"

    def test_league_is_not_a_declared_dependency(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        in_deps = False
        for line in pyproject.splitlines():
            stripped = line.strip()
            if stripped.startswith("dependencies") or stripped.startswith("["):
                in_deps = stripped.startswith("dependencies")
            if in_deps:
                assert "league" not in stripped.lower(), f"arena dependency declared: {stripped}"

    def test_the_seat_itself_imports_no_league_module(self) -> None:
        """The host reaches the arena by subprocess, so the guard holds here too."""
        assert not _league_identifiers(ast.parse(SEAT.read_text(encoding="utf-8")))


# ── the command arm: continuity rides the pad and the store ─────────────────


class TestCommandArmContinuity:
    """A fresh process per turn, so anything that survives came off disk."""

    def test_a_later_turn_acts_on_a_directive_only_memory_holds(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "carry", league_seat.ARM_COMMAND)
        turns = report["turns"]

        # The operator's objective is handed over ONCE, on turn 0.
        assert [t["directive_given"] for t in turns] == [True, False, False, False]
        # And every later turn still plays toward it. A unit already standing on
        # the point holds, which is the objective being *kept*, not forgotten —
        # so the memory-dependent decision asserted here is the declared plan.
        assert [t["objective_seen"] for t in turns] == [OBJECTIVE] * 4
        for turn in turns[1:]:
            assert turn["orders"]["plan"] == f"take {OBJECTIVE} and hold it"
            assert set(_actions(turn)) <= {"move", "hold"}
        assert any("move" in _actions(turn) for turn in turns[1:])
        # The two seams it could have come from both filled up as it went.
        assert [len(t["recalled"]) for t in turns] == [0, 1, 2, 3]
        assert all(t["remembered"] is not None for t in turns)

    def test_the_store_is_a_real_file_the_next_process_reads(self, tmp_path: Path) -> None:
        home = tmp_path / "files"
        report = _play(home, league_seat.ARM_COMMAND, "--max-turns", "2")

        store = Path(report["arena"]["store"])
        written = [p for p in store.rglob("*") if p.is_file()]
        assert written, "no memory file was written"
        blob = "\n".join(p.read_text(encoding="utf-8") for p in written)
        assert f"take {OBJECTIVE}" in blob

        pad = Path(report["arena"]["pad"])
        assert pad.exists()
        assert f"take {OBJECTIVE}" in pad.read_text(encoding="utf-8")

    def test_the_control_experiment_pad_store_either_or_neither(self, tmp_path: Path) -> None:
        """Four runs of the SAME turn, differing only in what memory survived.

        Without this, "turn 3 knew the objective" proves nothing — the mind
        could have had it hard-coded all along. Only the amnesiac holds.
        """
        home = tmp_path / "controls"
        report = _play(home, league_seat.ARM_COMMAND, "--max-turns", "2")
        pad = Path(report["arena"]["pad"])
        store = Path(report["arena"]["store"])
        board = _board(tmp_path / "probe")

        def _run(label: str, *, with_pad: bool, with_store: bool) -> dict[str, Any]:
            case = tmp_path / label
            case.mkdir(parents=True, exist_ok=True)
            if with_pad:
                shutil.copyfile(pad, case / "pad.jsonl")
            if with_store:
                shutil.copytree(store, case / "memory")
            else:
                (case / "memory").mkdir(parents=True, exist_ok=True)
            # A REAL separate process, with no directive: exactly what a
            # command-arm turn 3 gets.
            result = league_seat.take_turn(_turn_args(case), board)
            return result["record"]

        both = _run("both", with_pad=True, with_store=True)
        pad_only = _run("pad-only", with_pad=True, with_store=False)
        store_only = _run("store-only", with_pad=False, with_store=True)
        neither = _run("neither", with_pad=False, with_store=False)

        assert both["objective_seen"] == OBJECTIVE
        assert pad_only["objective_seen"] == OBJECTIVE, "the pad alone must carry it"
        assert store_only["objective_seen"] == OBJECTIVE, "eidetic alone must carry it"
        assert neither["objective_seen"] == "", "with no memory there is nothing to recover"

        assert set(a["action"] for a in both["orders"]["actions"]) == {"move"}
        assert set(a["action"] for a in pad_only["orders"]["actions"]) == {"move"}
        assert set(a["action"] for a in store_only["orders"]["actions"]) == {"move"}
        assert set(a["action"] for a in neither["orders"]["actions"]) == {"hold"}
        assert OBJECTIVE not in json.dumps(neither["orders"])

    def test_nothing_but_files_and_argv_crosses_the_process_boundary(self, tmp_path: Path) -> None:
        """The child's whole inheritance, written down where a reader can check it."""
        args = _args(
            "play",
            "--arm",
            "command",
            "--store",
            str(tmp_path / "memory"),
            "--workdir",
            str(tmp_path / "arena"),
        )
        argv = league_seat.turn_argv(args, record_path=tmp_path / "r.json", directive="")
        assert argv[:3] == [sys.executable, str(SEAT), "turn"]
        assert "--pad" in argv and "--store" in argv
        # The directive is NOT passed unless the caller says so — that absence
        # is the whole experiment.
        assert "--directive" not in argv


# ── the resident arm: one muse thread, alive across turns ───────────────────


class TestResidentMuseLivesAcrossTurns:
    def test_one_runner_serves_every_turn_in_the_resident_arm(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "muse-res", league_seat.ARM_RESIDENT, "--muse")
        ids = [turn["muse"]["runner_id"] for turn in report["turns"]]

        assert len(ids) == 4
        assert len(set(ids)) == 1, f"the muse was rebuilt mid-match: {ids}"
        assert all(turn["muse"]["thread_started"] for turn in report["turns"])
        assert report["drive"]["muse"]["runner_id"] == ids[0]
        assert report["drive"]["muse"]["closed"] is True
        assert report["mind"]["muse"] == league_seat.SCRIPTED_MUSE

    def test_the_command_arm_gets_a_new_muse_every_turn(self, tmp_path: Path) -> None:
        """The contrast that makes the resident claim mean something."""
        report = _play(tmp_path / "muse-cmd", league_seat.ARM_COMMAND, "--muse")
        ids = [turn["muse"]["runner_id"] for turn in report["turns"]]

        assert len(set(ids)) == len(ids), f"a runner id repeated across processes: {ids}"

    def test_a_run_with_no_muse_claims_no_second_mind(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "no-muse", league_seat.ARM_RESIDENT, "--max-turns", "1")

        assert report["mind"]["muse"] is None
        assert report["drive"]["presence_mode"] == "cortex-only"
        assert all(turn["muse"] is None for turn in report["turns"])

    def test_the_muse_is_off_by_default(self) -> None:
        args = _args("play", "--store", "/tmp/x")  # nosec B108 - parser default probe
        assert args.muse is False
        assert args.live is False
        assert args.events is False
        assert args.coherence is False


# ── the optional observer ────────────────────────────────────────────────────


class _RecordingClient:
    """A stand-in transport: records envelopes, always succeeds."""

    def __init__(self) -> None:
        self.published: list[tuple[Any, str]] = []

    def publish_event(self, envelope: Any, topic: str) -> Any:
        self.published.append((envelope, topic))
        return type("Result", (), {"ok": True, "reason": ""})()

    def close(self) -> None:
        return None


class TestOptionalObserver:
    """Absent ⇒ identical; broken ⇒ one recorded degradation, never a raise."""

    def test_absent_and_present_observers_produce_the_same_orders(self, tmp_path: Path) -> None:
        from embodiment import EventEmitter

        board = _board(tmp_path / "probe")
        plain = league_seat.take_turn(
            _turn_args(tmp_path / "plain", "--directive", league_seat.DEFAULT_DIRECTIVE), board
        )
        client = _RecordingClient()
        watched = league_seat.take_turn(
            _turn_args(tmp_path / "watched", "--directive", league_seat.DEFAULT_DIRECTIVE),
            board,
            observer=EventEmitter(client=client, run_id="fixed"),
        )

        assert watched["orders"] == plain["orders"]
        assert plain["record"]["degradations"] == []
        assert watched["record"]["degradations"] == []
        # ...and the observer really did see the drive.
        assert client.published, "an active emitter published nothing"
        assert all(env.type.startswith("embodiment.") for env, _ in client.published)

    def test_a_broken_emitter_degrades_once_and_the_turn_still_completes(
        self, tmp_path: Path
    ) -> None:
        from embodiment import EventEmitter

        def _boom() -> Any:
            raise RuntimeError("no broker is listening")

        board = _board(tmp_path / "probe")
        result = league_seat.take_turn(
            _turn_args(tmp_path / "broken", "--directive", league_seat.DEFAULT_DIRECTIVE),
            board,
            observer=EventEmitter(client_factory=_boom),
        )

        assert result["record"]["drive"]["exit_reason"] == "finished"
        events = [d for d in result["record"]["degradations"] if d["source"] == "events"]
        assert len(events) == 1, result["record"]["degradations"]
        assert events[0]["code"] == "connect-failed"

    def test_no_observer_is_built_unless_asked_for(self) -> None:
        assert league_seat.build_observer(_args("play", "--store", "/tmp/x")) is None  # nosec B108


# ── the wire: the same entry point league itself could drive ────────────────


class TestTurnWire:
    """Board in on stdin, orders JSON out on stdout — league's own shape."""

    def test_read_observation_accepts_a_show_payload(self, tmp_path: Path) -> None:
        board = _board(tmp_path / "probe")
        parsed = league_seat.read_observation(json.dumps(board))
        assert parsed["state"]["match_id"] == board["state"]["match_id"]
        assert parsed["legal_actions"]

    def test_read_observation_accepts_a_bare_state(self, tmp_path: Path) -> None:
        board = _board(tmp_path / "probe")
        parsed = league_seat.read_observation(json.dumps(board["state"]))
        assert parsed["state"]["match_id"] == board["state"]["match_id"]
        # Absent pieces default rather than exploding: league's own command
        # driver sends the state and nothing else.
        assert parsed["legal_actions"] == {}
        assert parsed["last_turn_rejections"] == []

    @staticmethod
    def _league_prompt(board: dict[str, Any]) -> str:
        """league's ``command`` driver prompt, template-faithful.

        Reproduced from ``league/harness.py:409-423`` — and the ``Scenario:``
        line is the whole point of this fixture: it puts a JSON object on the
        wire BEFORE the board, which is the trap a "first object wins" reader
        falls into.
        """
        scenario = {
            "id": "skirmish-1",
            "name": "Skirmish 1",
            "grid": {"width": 12, "height": 10},
            "turn_limit": 30,
            "capture_hold_turns": 2,
            "roles": {"scout": {"move": 3, "carry": 1}},
        }
        return (
            "You are the blue team commander in a League of Agents match.\n"
            "Rules, briefly: turn-based, simultaneous orders.\n"
            f"Scenario: {json.dumps(scenario, sort_keys=True)}\n"
            "\nLegal actions right now:\n"
            "- blue-u1: move to [[0, 1], [1, 0]]; gather: no; deliver: no; hold: yes\n"
            "Current match state (JSON):\n"
            f"{json.dumps(board['state'], sort_keys=True)}\n\n"
            "You command team blue. Reply with ONLY one JSON object, no prose:\n"
            '{"plan": "<optional standing plan>",\n'
            ' "messages": [{"from": "<agent-id>", "text": "..."}],\n'
            ' "actions": [{"unit_id": "...", "action": "move|gather|deliver|hold",\n'
            '              "to": [x, y]}]}\n'
        )

    def test_read_observation_accepts_leagues_own_prose_prompt(self, tmp_path: Path) -> None:
        """The scenario blob comes FIRST in league's template. It is not the board.

        This is a regression test with a scar: the first version of
        ``read_observation`` took the first JSON object it found, so a real
        ``league harness run`` handed it the *scenario*, which has no units. The
        seat read that as a finished match and submitted nothing — six turns
        resolved, zero orders, cooperation score 0, and not one error anywhere.
        """
        board = _board(tmp_path / "probe")
        parsed = league_seat.read_observation(self._league_prompt(board))

        assert parsed["state"]["match_id"] == board["state"]["match_id"]
        assert parsed["state"]["units"], "the scenario was mistaken for the board"
        assert league_seat.living_units(parsed, "blue")

    def test_the_mind_actually_orders_from_leagues_own_prompt(self, tmp_path: Path) -> None:
        """The end the parsing exists for: real orders, not an empty submit."""
        board = _board(tmp_path / "probe")
        parsed = league_seat.read_observation(self._league_prompt(board))
        result = league_seat.take_turn(
            _turn_args(tmp_path / "driven", "--directive", league_seat.DEFAULT_DIRECTIVE), parsed
        )
        assert result["orders"]["actions"], "the seat submitted nothing at all"
        assert result["record"]["objective_seen"] == OBJECTIVE

    def test_read_observation_refuses_a_prompt_with_no_board(self) -> None:
        with pytest.raises(ValueError):
            league_seat.read_observation("there is no board here")

    def test_read_observation_refuses_a_prompt_whose_only_object_is_not_a_board(self) -> None:
        with pytest.raises(ValueError, match="not a match state"):
            league_seat.read_observation('Scenario: {"id": "skirmish-1", "turn_limit": 30}')

    def test_the_turn_subcommand_prints_only_orders_on_stdout(self, tmp_path: Path) -> None:
        board = _board(tmp_path / "probe")
        home = tmp_path / "wire"
        home.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(SEAT),
            "turn",
            "--team",
            "blue",
            "--store",
            str(home / "memory"),
            "--pad",
            str(home / "pad.jsonl"),
            "--workdir",
            str(home / "work"),
            "--record",
            str(home / "record.json"),
            "--directive",
            league_seat.DEFAULT_DIRECTIVE,
        ]
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            cmd,
            input=json.dumps(board),
            capture_output=True,
            text=True,
            cwd=str(home),
            env=_env(),
            timeout=300,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]

        # Exactly one JSON object, and it is the orders league would accept.
        orders = json.loads(proc.stdout)
        assert set(orders) <= {"plan", "actions", "messages"}
        assert orders["actions"] and all("unit_id" in a for a in orders["actions"])
        # Presence went to stderr; the two streams never blend.
        assert "presence" in proc.stderr or proc.stderr == ""
        record = json.loads((home / "record.json").read_text(encoding="utf-8"))
        assert record["arm"] == league_seat.ARM_COMMAND
        assert record["residency"] == "stateless"


# ── the scripted mind is a function of its prompt, not a fixture ─────────────


class TestScriptedMindNeedsTheMemory:
    @staticmethod
    def _messages(board: dict[str, Any], extra: str = "") -> list[dict[str, Any]]:
        view = league_seat.observation_view(board, "blue")
        text = league_seat.observation_block(view)
        if extra:
            text = f"{extra}\n\n{text}"
        return [
            {"role": "system", "content": league_seat.BASE_SYSTEM},
            {"role": "user", "content": text},
        ]

    def test_without_an_objective_it_notes_that_it_is_holding(self, tmp_path: Path) -> None:
        board = _board(tmp_path / "probe")
        response = league_seat.make_scripted_cortex()(self._messages(board))
        call = response.tool_calls[0]
        assert call.name == "note"
        assert "no standing objective" in call.arguments["text"]

    def test_with_the_objective_in_context_it_moves_toward_it(self, tmp_path: Path) -> None:
        board = _board(tmp_path / "probe")
        messages = self._messages(board, league_seat.DEFAULT_DIRECTIVE)
        messages.append({"role": "tool", "content": f"{league_seat.MARK_NOTED} n1 recorded"})
        response = league_seat.make_scripted_cortex()(messages)
        call = response.tool_calls[0]
        assert call.name == "order"
        assert call.arguments["action"] == "move"

    def test_a_remembered_summary_that_says_ordered_cannot_inflate_the_count(
        self, tmp_path: Path
    ) -> None:
        """Recalled prose sits in the same transcript the markers are counted in."""
        board = _board(tmp_path / "probe")
        recalled = (
            "Turn 2: standing objective is to take cp-west and hold it; "
            "3 unit(s) ordered toward it. noted everything."
        )
        messages = self._messages(board, league_seat.DEFAULT_DIRECTIVE)
        messages[-1]["content"] += f"\n\nContext:\n- {recalled}"
        response = league_seat.make_scripted_cortex()(messages)
        # Still the FIRST move of the turn: nothing was miscounted as done.
        assert response.tool_calls[0].name == "note"

    def test_read_objective_ignores_a_point_it_was_told_to_ignore(self) -> None:
        assert league_seat.read_objective(league_seat.DEFAULT_DIRECTIVE) == "cp-west"
        assert league_seat.read_objective("nothing to see") == ""


# ── the seat's tool surface ──────────────────────────────────────────────────


class TestToolSurface:
    def test_the_three_tools_are_domain_tools(self) -> None:
        assert set(league_seat.TOOL_NAMES) == {"note", "order", "submit"}

    def test_an_unknown_tool_is_an_unknown_tool_error(self, tmp_path: Path) -> None:
        import embodiment

        seat = league_seat.Seat(
            team_id="blue", pad=league_seat.Scratchpad(), show=_board(tmp_path / "probe")
        )
        with pytest.raises(embodiment.UnknownToolError):
            seat.execute("rm", {"path": "/"})

    def test_ordering_a_unit_that_is_not_yours_is_a_self_correcting_step(
        self, tmp_path: Path
    ) -> None:
        import embodiment

        seat = league_seat.Seat(
            team_id="blue", pad=league_seat.Scratchpad(), show=_board(tmp_path / "probe")
        )
        with pytest.raises(embodiment.ToolError):
            seat.execute("order", {"unit_id": "red-u1", "action": "hold"})

    def test_a_move_without_a_target_is_a_self_correcting_step(self, tmp_path: Path) -> None:
        import embodiment

        seat = league_seat.Seat(
            team_id="blue", pad=league_seat.Scratchpad(), show=_board(tmp_path / "probe")
        )
        with pytest.raises(embodiment.ToolError):
            seat.execute("order", {"unit_id": "blue-u1", "action": "move"})

    def test_submit_without_an_advance_ends_the_drive(self, tmp_path: Path) -> None:
        seat = league_seat.Seat(
            team_id="blue", pad=league_seat.Scratchpad(), show=_board(tmp_path / "probe")
        )
        seat.execute("order", {"unit_id": "blue-u1", "action": "hold"})
        outcome = seat.execute("submit", {"plan": "p", "summary": "s"})
        assert outcome.finished is True
        assert seat.submitted[-1]["actions"] == [{"unit_id": "blue-u1", "action": "hold"}]

    def test_the_house_opponent_is_deterministic(self, tmp_path: Path) -> None:
        """Same board in, same orders out — and orders that actually say something.

        The equality alone was too weak to mean much: a function returning a
        cached empty dict would satisfy it. The rival is the *control* half of
        every arena match, so "deterministic" has to mean "deterministically
        issues real orders", not "deterministically issues nothing".
        """
        board = _board(tmp_path / "probe")
        first = league_seat.rival_orders(board, "red")
        second = league_seat.rival_orders(board, "red")
        assert first == second, "the house opponent is not deterministic"
        assert first.get("actions"), "the house opponent issued no orders at all"


# ── framing: absent identity ⇒ byte-identical prompts ───────────────────────


class TestFraming:
    def test_absent_identity_leaves_the_prompt_byte_identical(self) -> None:
        assert league_seat.build_system_prompt(None, muse=False) == league_seat.BASE_SYSTEM
        assert league_seat.build_system_prompt("", muse=False) == league_seat.BASE_SYSTEM

    def test_a_configured_identity_only_adds(self) -> None:
        framed = league_seat.build_system_prompt("Gwen", muse=False)
        assert framed != league_seat.BASE_SYSTEM
        assert league_seat.BASE_SYSTEM in framed


# ── store hygiene and the mandatory anchor ──────────────────────────────────


class TestStoreHygiene:
    @staticmethod
    def _ambient() -> dict[str, str]:
        root = REPO_ROOT / ".eidetic"
        if not root.exists():
            return {}
        return {
            str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    def test_the_store_flag_is_required_and_has_no_default(self) -> None:
        """An unanchored public record would resolve against the host's own repo."""
        with pytest.raises(SystemExit):
            league_seat.build_parser().parse_args(["play"])
        with pytest.raises(SystemExit):
            league_seat.build_parser().parse_args(["turn", "--team", "blue"])

    def test_this_repo_s_own_store_is_byte_unchanged(self, tmp_path: Path) -> None:
        before = self._ambient()
        home = tmp_path / "hygiene"
        home.mkdir(parents=True, exist_ok=True)
        # Run from INSIDE the repo, the cwd that would make an unanchored
        # public record land in <repo-root>/.eidetic/memory.
        cmd = [
            sys.executable,
            str(SEAT),
            "play",
            "--arm",
            league_seat.ARM_COMMAND,
            "--store",
            str(home / "memory"),
            "--workdir",
            str(home / "arena"),
            "--league-bin",
            FAKE_BIN,
            "--max-turns",
            "1",
            "--json",
        ]
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), env=_env(), timeout=300
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert self._ambient() == before, "the seat leaked into this repo's own store"

    def test_the_default_workdir_is_outside_the_repo(self) -> None:
        default = league_seat.default_workdir()
        assert not str(default.resolve()).startswith(str(REPO_ROOT.resolve()))

    def test_the_lifecycle_anchor_is_the_store_the_operator_named(self, tmp_path: Path) -> None:
        config = league_seat.lifecycle_config(tmp_path / "mem", tmp_path, scope="s")
        assert Path(str(config.data_dir)) == tmp_path / "mem"
        assert config.scope == "s"
        # Coherence dials an embedder; the default path touches nothing.
        assert config.assess_action is False
        assert config.recall_mode == "keyword"


# ── the host describes itself honestly ──────────────────────────────────────


class TestSelfDescribing:
    def test_the_host_states_the_software_presence_boundary(self) -> None:
        """C2: the name must not be left to imply a robot body."""
        doc = (league_seat.__doc__ or "").lower()
        assert "software presence" in doc
        assert "not a robot body" in doc

    def test_the_host_makes_no_benchmark_claim(self) -> None:
        doc = (league_seat.__doc__ or "").lower()
        assert "not a benchmark" in doc

    def test_a_hermetic_run_names_no_model_it_did_not_run(self, tmp_path: Path) -> None:
        report = _play(tmp_path / "honest", league_seat.ARM_RESIDENT, "--max-turns", "1")
        assert league_seat.CORTEX_MODEL not in json.dumps(report)
        assert league_seat.MUSE_MODEL not in json.dumps(report)

    def test_the_key_is_read_from_the_environment_and_never_echoed(self) -> None:
        source = SEAT.read_text(encoding="utf-8")
        assert league_seat.API_KEY_ENV in source
        rendered = json.dumps(_args("play", "--store", "/tmp/x").__dict__)  # nosec B108
        assert "sk-" not in rendered

    def test_live_needs_the_flag_and_the_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No key ⇒ an environment error (exit 2), not a half-configured dial."""
        monkeypatch.delenv(league_seat.API_KEY_ENV, raising=False)
        args = _args("play", "--store", "/tmp/x", "--live")  # nosec B108
        with pytest.raises(SystemExit) as excinfo:
            league_seat.build_minds(args)
        assert excinfo.value.code == 2


# ── the dead-endpoint path, which must actually dial ────────────────────────


class TestLiveDeadEndpointReallyDials:
    """Named ``TestLive*`` so the network bomb lets it through — on purpose.

    A default test that passes because the network was stubbed proves nothing;
    that exact bug was found and fixed earlier in this plan. This one points the
    live seam at a port nothing listens on and asserts the recorded reason names
    a real socket failure, which only a real dial can produce. Loopback only, so
    it costs nothing offline.
    """

    DEAD_URL = "http://localhost:59999/v1"

    def test_a_dead_gateway_is_a_recorded_failure_naming_the_dial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(league_seat.API_KEY_ENV, "unused-against-a-dead-port")
        board = _board(tmp_path / "probe")
        args = _turn_args(
            tmp_path / "dead",
            "--live",
            "--base-url",
            self.DEAD_URL,
            "--directive",
            league_seat.DEFAULT_DIRECTIVE,
        )
        result = league_seat.take_turn(args, board)
        record = result["record"]

        assert record["drive"]["exit_reason"] != "finished" or record["degradations"]
        evidence = json.dumps(record["drive"]) + json.dumps(record["degradations"])
        assert any(
            needle in evidence
            for needle in ("URLError", "Connection refused", "ConnectionRefused", "refused")
        ), f"nothing here proves a socket was ever opened: {evidence[:500]}"
        # And the turn still produced orders rather than raising at the host.
        assert "actions" in result["orders"]

    def test_the_gateway_seam_dials_the_endpoint_it_was_given(self) -> None:
        import urllib.error

        seam = league_seat.gateway_seam(
            self.DEAD_URL, "no-such-model", "unused", max_tokens=8, timeout=5.0
        )
        with pytest.raises((urllib.error.URLError, OSError)):
            seam([{"role": "user", "content": "hello"}])


# ── the real arena (opt-in) ─────────────────────────────────────────────────


LIVE_ARENA = os.environ.get("EMBODIMENT_LIVE_ARENA") == "1"
LEAGUE_BIN = os.environ.get("EMBODIMENT_LEAGUE_BIN", "league")


def _league_installed() -> bool:
    return shutil.which(LEAGUE_BIN.split()[0]) is not None


@pytest.mark.skipif(not LIVE_ARENA, reason="set EMBODIMENT_LIVE_ARENA=1 to play the real arena")
class TestLiveArena:
    """The real ``league`` CLI, both arms. Offline — no model, no network.

    Skipped by default because the default suite must not require the arena to
    be installed. When it is, this is the honest version of every claim above:
    league's own engine, league's own match log, league's own replay.
    """

    def _play_real(self, home: Path, arm: str, *extra: str) -> dict[str, Any]:
        if not _league_installed():
            pytest.skip(f"no {LEAGUE_BIN!r} on PATH")
        cmd = [
            sys.executable,
            str(SEAT),
            "play",
            "--arm",
            arm,
            "--store",
            str(home / "memory"),
            "--workdir",
            str(home / "arena"),
            "--league-bin",
            LEAGUE_BIN,
            "--match-id",
            f"seat-{arm}",
            "--max-turns",
            "3",
            "--json",
        ]
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True, cwd=str(home.parent), env=_env(), timeout=1800
        )
        assert proc.returncode == 0, proc.stderr[-3000:]
        return json.loads(proc.stdout)

    def test_both_arms_play_the_real_arena_and_it_records_the_residency(
        self, tmp_path: Path
    ) -> None:
        resident = self._play_real(tmp_path / "resident", league_seat.ARM_RESIDENT)
        command = self._play_real(tmp_path / "command", league_seat.ARM_COMMAND)

        assert resident["match"]["driver_kinds"]["blue"] == "resident"
        assert command["match"]["driver_kinds"]["blue"] == "stateless"
        assert resident["match"]["turns_played"] == 3
        assert command["match"]["turns_played"] == 3

        # league's OWN artifact carries the same fact, in its log header.
        header = self._header(tmp_path / "resident" / "arena", "seat-resident")
        assert header["driver_kinds"]["blue"] == "resident"

    @staticmethod
    def _header(workdir: Path, match_id: str) -> dict[str, Any]:
        log = workdir / ".league" / "matches" / match_id / "log.jsonl"
        first = log.read_text(encoding="utf-8").splitlines()[0]
        return json.loads(first)

    def test_the_real_arena_carries_the_directive_across_processes(self, tmp_path: Path) -> None:
        report = self._play_real(tmp_path / "carry", league_seat.ARM_COMMAND)
        turns = report["turns"]
        assert turns[0]["directive_given"] is True
        assert all(t["directive_given"] is False for t in turns[1:])
        assert all(t["objective_seen"] == OBJECTIVE for t in turns[1:])
        assert len({t["pid"] for t in turns}) == len(turns)

    def test_league_can_drive_the_seat_as_its_own_command_driver(self, tmp_path: Path) -> None:
        """The other direction: ``league harness run`` spawns the seat per turn.

        This is the test that found the ``read_observation`` bug. Nothing this
        host drives itself could have: the parent sends the ``match show``
        payload, while league sends a prose prompt whose FIRST JSON object is
        the scenario. Six turns once resolved with zero orders and no error.

        Note the directive is fixed in ``argv`` here — league's harness has no
        way to vary a driver's command line per turn. Varying it (turn 0 only)
        is what ``play --arm command`` does, and it is the sharper experiment;
        this test is about the wire.
        """
        if not _league_installed():
            pytest.skip(f"no {LEAGUE_BIN!r} on PATH")
        home = tmp_path / "driven"
        (home / "run").mkdir(parents=True, exist_ok=True)
        (home / "memory").mkdir(parents=True, exist_ok=True)
        pad = home / "pad.jsonl"
        config = {
            "match": {
                "scenario": "skirmish-1",
                "mode": "competitive",
                "seed": 11,
                "id": "harness-seat",
            },
            "teams": [
                {
                    "id": "blue",
                    "name": "Blue",
                    "driver": {
                        "type": "command",
                        "timeout": 300,
                        "argv": [
                            sys.executable,
                            str(SEAT),
                            "turn",
                            "--team",
                            "blue",
                            "--store",
                            str(home / "memory"),
                            "--pad",
                            str(pad),
                            "--workdir",
                            str(home / "work"),
                            "--directive",
                            league_seat.DEFAULT_DIRECTIVE,
                        ],
                    },
                    "agents": [
                        {"id": f"b{i + 1}", "model": "embodiment-seat", "role": role}
                        for i, role in enumerate(league_seat.ROSTER_ROLES)
                    ],
                },
                {
                    "id": "red",
                    "name": "Red",
                    "driver": {"type": "bot"},
                    "agents": [
                        {"id": f"r{i + 1}", "model": "bot:greedy", "role": role}
                        for i, role in enumerate(league_seat.ROSTER_ROLES)
                    ],
                },
            ],
            "max_rounds": 3,
            "fog": False,
        }
        config_path = home / "harness.json"
        config_path.write_text(json.dumps(config, indent=1), encoding="utf-8")

        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            [
                *LEAGUE_BIN.split(),
                "harness",
                "run",
                "--config",
                str(config_path),
                "--apply",
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=str(home / "run"),
            env=_env(),
            timeout=1800,
        )
        assert proc.returncode == 0, proc.stderr[-3000:]
        result = json.loads(proc.stdout)
        assert result["turns_played"] == 3
        assert "idles this turn" not in proc.stderr, "the seat failed and league idled it"

        # The seat really commanded: league's OWN cooperation signals, which are
        # all zero for a team that submits nothing.
        signals = result["score"]["cooperation"]["blue"]["signals"]
        assert signals["delegation_spread"] > 0, "the seat ordered no units"
        assert signals["plan_coherence"] > 0, "the seat declared no plan"
        # ...and its working memory survived every re-spawn.
        entries = [line for line in pad.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(entries) == 3


def _fake_league_answers_the_public_subset() -> None:
    """The stub is a contract copy, so it must not silently drift."""
    source = FAKE_LEAGUE.read_text(encoding="utf-8")
    for verb in ("team register", "match new", "match show", "match act", "match score"):
        assert verb in source, f"the stub no longer documents {verb!r}"


class TestTheObjectiveExtractorSeesTheMindsOwnWords:
    """Regression pins for the t19 grader defect.

    ``read_objective`` was ``r"take (cp-[a-z0-9-]+)"``. It matched the directive
    as *issued* and missed the paraphrase the mind actually writes into the
    store, so the t19 series graded its pre-registered continuity prediction as
    FAIL on six matches whose stores plainly held the objective. See
    ``docs/live-test-results/arena-series.md``.
    """

    #: Taken verbatim from the t19 turn records. These are the exact strings the
    #: old pattern could not see.
    PARAPHRASES = [
        "Objective: take and hold cp-west.",
        "Turn 0 complete. Units advanced toward cp-west. Objective: take and hold cp-west.",
        "Objective: take and hold cp-west [3, 8]. Units are at [3, 3], [2, 3], [1, 4].",
        "Turn 1 complete. u1 at [3, 2]. Objective: take and hold cp-west.",
    ]

    @pytest.mark.parametrize("text", PARAPHRASES)
    def test_the_paraphrase_the_mind_actually_writes_is_seen(self, text: str) -> None:
        assert league_seat.read_objective(text) == OBJECTIVE

    def test_the_directive_as_issued_is_still_seen(self) -> None:
        assert league_seat.read_objective(league_seat.DEFAULT_DIRECTIVE) == OBJECTIVE

    def test_the_point_the_directive_forbids_never_becomes_the_objective(self) -> None:
        """``ignore cp-east`` must never read as an objective — the whole point."""
        assert league_seat.read_objective("ignore cp-east") == ""
        assert league_seat.read_objective(league_seat.DEFAULT_DIRECTIVE) != "cp-east"

    def test_merely_moving_toward_a_point_is_not_taking_it(self) -> None:
        """A movement note is not a standing objective; the verb is load-bearing."""
        assert league_seat.read_objective("Advance all units toward cp-west [3, 8].") == ""
        assert league_seat.read_objective("Move all units towards cp-west [3, 8].") == ""

    def test_a_distant_mention_does_not_bind_to_the_verb(self) -> None:
        """The bounded word gap is what keeps this from matching anything."""
        far = "capture the ridge, then regroup, then resupply, then consider cp-west"
        assert league_seat.read_objective(far) == ""

    def test_nothing_is_hardcoded_about_cp_west(self) -> None:
        assert league_seat.read_objective("take and hold cp-north") == "cp-north"
        assert league_seat.read_objective("") == ""


class TestTheCommandArmReportsItsDegradations:
    """C3 pin: ``_play_command`` returned nothing, so ten degradations read as zero.

    In the t19 series every CM match published ``degradations: []`` while its
    turn records held 2, 5 and 3 ``muse-insight-late`` entries. A degradation
    that never reaches the match report is a silent degradation.
    """

    def test_play_command_returns_the_degradations_it_collected(self) -> None:
        """The signature itself is the fix — a bare return cannot carry them."""
        tree = ast.parse(SEAT.read_text(encoding="utf-8"))
        func = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_play_command"
        )
        returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
        assert returns, "_play_command returns nothing — degradations cannot escape it"
        assert not any(
            r.value is None or (isinstance(r.value, ast.Constant) and r.value.value is None)
            for r in returns
        ), "_play_command still has a bare return; the command arm would report []"

    def test_the_match_report_takes_the_command_arms_return_value(self) -> None:
        """The call site must bind it — collecting and discarding is the same bug."""
        source = SEAT.read_text(encoding="utf-8")
        assert (
            "match_degradations = _play_command(" in source
        ), "the command arm's degradations are computed but dropped at the call site"


def test_the_stub_documents_the_subset_it_stands_in_for() -> None:
    _fake_league_answers_the_public_subset()


def test_the_seat_is_covered_by_the_examples_import_guard() -> None:
    """``tests/test_demo_greenhouse.py`` globs ``examples/``; make that explicit."""
    from tests.test_demo_greenhouse import _demo_sources

    assert SEAT in _demo_sources()


class TestArenaFailureIsOneLineNotATraceback:
    """An example that sprays a traceback teaches the wrong thing."""

    def test_a_missing_arena_binary_is_an_environment_error(self, tmp_path: Path) -> None:
        home = tmp_path / "noarena"
        home.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(SEAT),
            "play",
            "--store",
            str(home / "memory"),
            "--workdir",
            str(home / "arena"),
            "--league-bin",
            f"{sys.executable} {home / 'not-a-league.py'}",
            "--json",
        ]
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True, cwd=str(home), env=_env(), timeout=300
        )
        assert proc.returncode == 2
        assert proc.stderr.startswith("error: ")
        assert "hint: " in proc.stderr
        assert "Traceback" not in proc.stderr
        assert proc.stdout == ""

    def test_a_turn_handed_no_board_is_an_environment_error(self, tmp_path: Path) -> None:
        home = tmp_path / "noboard"
        home.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(SEAT),
            "turn",
            "--team",
            "blue",
            "--store",
            str(home / "memory"),
            "--pad",
            str(home / "pad.jsonl"),
            "--workdir",
            str(home / "work"),
        ]
        # Fixed argv, no shell.
        proc = subprocess.run(  # nosec B603
            cmd,
            input="no board here",
            capture_output=True,
            text=True,
            cwd=str(home),
            env=_env(),
            timeout=300,
        )
        assert proc.returncode == 2
        assert "Traceback" not in proc.stderr
        assert "no board was offered" in proc.stderr
