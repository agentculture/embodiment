#!/usr/bin/env python3
"""A stand-in for the ``league`` CLI's CONTINUOUS lane, so the suite needs no arena.

``tests/test_league_commander.py`` must be hermetic: no live model, no network,
and **no real league binary**. This module answers the exact subset of
league-of-agents' public CLI that ``examples/league_commander.py`` calls, in the
shapes league itself emits (verified against ``league 0.16.0`` on 2026-07-31):

* ``team register <id> --name N --agent <id>:<model>:<role> --apply --json``
* ``cmatch new --scenario S --team A --team B --driver T:KIND --seed N --id ID
  --apply --json`` → ``{"match_id", "scenario", "seed", "teams",
  "driver_kinds", "due", "log", "applied"}``
* ``cmatch show ID --json`` → ``{"match_id", "game_time", "status", "winner",
  "due", "decisions": [{"unit_id", "briefing"}]}``
* ``cmatch act ID --unit U --action-json J --apply --json``
* ``cmatch tick ID --apply --json``
* ``match score ID --json`` → ``{"outcome", "winner", "status", "units"}``

It is deliberately NOT a reimplementation of the continuous engine: there is no
timeline, no role durations, and no legality oracle. What it reproduces
faithfully is the **contract** the harness is written against — the JSON keys,
the briefing shape (`game_time` / `you` / `menu` / `outlook` / `board`), the
canonical `(team_id, unit_id)` order `act` refuses to be answered out of, the
dry-run-by-default `--apply` rule, the CWD-rooted ``.league/`` store, and
league's own status vocabulary (``pending`` / ``active`` / ``finished``).

The menu is built so a *choice matters*: index 0 is worth 1 point, index 1 is
worth 2, index 2 is worth 0. A harness whose commander overrides its unit
therefore produces a different score from one that rubber-stamps, which is what
lets a hermetic test tell the two apart.

The real arena is exercised by the live series, and by
``TestLiveArena``-style opt-in checks; this file is what CI runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

#: Ticks before the match finishes. Short on purpose — a hermetic match is a
#: contract exercise, not a game.
TIME_LIMIT = 3

#: What each menu index is worth. Index 2 is worth nothing, so a bad decision
#: is representable.
MENU_POINTS = (1, 2, 0)

#: Points the house bot banks per tick. Fixed, like the real ``bot`` driver.
BOT_POINTS_PER_TICK = 1


def store_root() -> Path:
    """league roots its store at ``<cwd>/.league``; so does this."""
    return Path.cwd() / ".league"


def team_path(team_id: str) -> Path:
    return store_root() / "teams" / f"{team_id}.json"


def match_dir(match_id: str) -> Path:
    return store_root() / "matches" / match_id


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def emit(payload: Any) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def fail(message: str, *, code: int = 1) -> int:
    print(
        json.dumps(
            {"code": code, "message": message, "remediation": "see 'league explain cmatch'"}
        ),
        file=sys.stderr,
    )
    return code


# ── team register ────────────────────────────────────────────────────────────


def cmd_team_register(args: argparse.Namespace) -> int:
    agents = []
    for spec in args.agent or []:
        parts = spec.split(":")
        if len(parts) != 3:
            return fail(f"bad --agent spec {spec!r}; expected ID:MODEL:ROLE")
        agents.append({"id": parts[0], "model": parts[1], "role": parts[2]})
    record = {"id": args.team_id, "name": args.name or args.team_id, "agents": agents}
    if not args.apply:
        return emit({**record, "applied": False})
    save_json(team_path(args.team_id), record)
    return emit({**record, "path": str(team_path(args.team_id)), "applied": True})


# ── cmatch ───────────────────────────────────────────────────────────────────


def state_path(match_id: str) -> Path:
    return match_dir(match_id) / "state.json"


def log_path(match_id: str) -> Path:
    return match_dir(match_id) / "log.jsonl"


def append_event(match_id: str, event: dict[str, Any]) -> None:
    path = log_path(match_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def unit_ids(team_id: str, roster: dict[str, Any]) -> list[str]:
    return [f"{team_id}-u{index}" for index, _ in enumerate(roster.get("agents") or [], start=1)]


def cmd_cmatch_new(args: argparse.Namespace) -> int:
    if state_path(args.id).exists():
        return fail(f"match {args.id!r} already exists")
    drivers: dict[str, str] = {}
    for spec in args.driver or []:
        team_id, _, kind = spec.partition(":")
        drivers[team_id] = kind
    teams = list(args.team or [])
    if len(teams) != 2:
        return fail("a cmatch needs exactly two --team ids")

    rosters = {}
    for team_id in teams:
        roster = load_json(team_path(team_id))
        if roster is None:
            return fail(f"unknown team {team_id!r}")
        rosters[team_id] = roster

    ours = [t for t in teams if drivers.get(t) != "bot"]
    if not ours:
        return fail("at least one team must be externally driven")
    our_team = ours[0]
    their_team = [t for t in teams if t != our_team][0]

    state = {
        "match_id": args.id,
        "scenario": args.scenario,
        "seed": args.seed,
        "mode": args.mode,
        "teams": teams,
        "our_team": our_team,
        "their_team": their_team,
        "driver_kinds": drivers,
        "units": {t: unit_ids(t, rosters[t]) for t in teams},
        "roles": {t: [str(a.get("role")) for a in (rosters[t].get("agents") or [])] for t in teams},
        "models": {
            t: [str(a.get("model")) for a in (rosters[t].get("agents") or [])] for t in teams
        },
        "clock": 0,
        "status": "active",
        "winner": None,
        "points": {t: 0 for t in teams},
        "grades": {u: 0.0 for t in teams for u in unit_ids(t, rosters[t])},
    }
    state["pending"] = list(state["units"][our_team])
    if not args.apply:
        return emit({"match_id": args.id, "applied": False, "due": state["pending"]})
    save_json(state_path(args.id), state)
    append_event(
        args.id,
        {
            "clock": 0,
            "width": 10000,
            "driver_kinds": drivers,
            "scenario": args.scenario,
            "seed": args.seed,
            "teams": teams,
            "models": state["models"],
            "log_version": 1,
        },
    )
    return emit(
        {
            "match_id": args.id,
            "scenario": args.scenario,
            "mode": args.mode,
            "seed": args.seed,
            "teams": teams,
            "driver_kinds": drivers,
            "time_limit": TIME_LIMIT,
            "applied": True,
            "log": str(log_path(args.id)),
            "due": state["pending"],
        }
    )


def build_menu(state: dict[str, Any], unit_id: str) -> list[dict[str, Any]]:
    clock = int(state["clock"])
    return [
        {
            "kind": kind,
            "target": f"{target}-{clock}",
            "target_id": f"{target}-{clock}",
            "duration": 1,
            "completion_time": clock + 1,
            "menu_points": MENU_POINTS[index],
        }
        for index, (kind, target) in enumerate(
            (("take_post", "cp"), ("gather", "rn"), ("move", "corner"))
        )
    ]


def build_briefing(state: dict[str, Any], unit_id: str) -> dict[str, Any]:
    team_id = state["our_team"]
    index = state["units"][team_id].index(unit_id)
    return {
        "game_time": state["clock"],
        "you": {
            "unit_id": unit_id,
            "agent_id": f"b{index + 1}",
            "team_id": team_id,
            "role": state["roles"][team_id][index],
            "pos": {"x": 1000 * (index + 1), "y": 1000},
            "carrying": 0,
            "action": None,
        },
        "menu": build_menu(state, unit_id),
        "outlook": [],
        "board": {
            "match_id": state["match_id"],
            "clock": state["clock"],
            "time_limit": TIME_LIMIT,
            "teams": [{"id": t, "resources": state["points"][t]} for t in state["teams"]],
        },
        "messages": [],
    }


def load_state(match_id: str) -> Optional[dict[str, Any]]:
    return load_json(state_path(match_id))


def cmd_cmatch_show(args: argparse.Namespace) -> int:
    state = load_state(args.id)
    if state is None:
        return fail(f"match {args.id!r} could not be read as a continuous (cmatch) log")
    due = list(state.get("pending") or [])
    return emit(
        {
            "match_id": args.id,
            "game_time": state["clock"],
            "status": state["status"],
            "winner": state["winner"],
            "due": due,
            "decisions": [{"unit_id": u, "briefing": build_briefing(state, u)} for u in due],
        }
    )


def cmd_cmatch_act(args: argparse.Namespace) -> int:
    state = load_state(args.id)
    if state is None:
        return fail(f"unknown match {args.id!r}")
    due = list(state.get("pending") or [])
    if not due:
        return fail("no unit is due")
    if args.unit != due[0]:
        return fail(f"{due[0]} is next; answer units in the order 'show' reports them")
    try:
        action = json.loads(args.action_json) if args.action_json else None
    except ValueError:
        return fail("--action-json is not valid JSON")

    points = 0
    if isinstance(action, dict):
        points = int(action.get("menu_points") or 0)
    if not args.apply:
        return emit({"match_id": args.id, "unit": args.unit, "applied": False})

    team_id = state["our_team"]
    state["points"][team_id] += points
    state["grades"][args.unit] = float(state["grades"].get(args.unit, 0.0)) + 100.0 * points
    state["pending"] = due[1:]
    save_json(state_path(args.id), state)
    append_event(
        args.id,
        {"kind": "action_started", "clock": state["clock"], "unit_id": args.unit, "data": action},
    )
    return emit(
        {
            "match_id": args.id,
            "unit": args.unit,
            "action": action,
            "applied": True,
            "status": state["status"],
            "winner": state["winner"],
            "due": state["pending"],
        }
    )


def cmd_cmatch_tick(args: argparse.Namespace) -> int:
    state = load_state(args.id)
    if state is None:
        return fail(f"unknown match {args.id!r}")
    if not args.apply:
        return emit({"match_id": args.id, "applied": False})
    if state["status"] != "active":
        return emit({"match_id": args.id, "applied": True, "status": state["status"]})

    their_team = state["their_team"]
    state["points"][their_team] += BOT_POINTS_PER_TICK
    for unit in state["units"][their_team]:
        state["grades"][unit] = float(state["grades"].get(unit, 0.0)) + 50.0
    state["clock"] = int(state["clock"]) + 1
    if state["clock"] >= TIME_LIMIT:
        state["status"] = "finished"
        best = max(state["points"].items(), key=lambda kv: kv[1])
        tied = [t for t, p in state["points"].items() if p == best[1]]
        state["winner"] = None if len(tied) > 1 else best[0]
        state["pending"] = []
    else:
        state["pending"] = list(state["units"][state["our_team"]])
    save_json(state_path(args.id), state)
    append_event(args.id, {"kind": "tick", "clock": state["clock"], "data": {}})
    return emit(
        {
            "match_id": args.id,
            "resolved": state["units"][their_team],
            "parked": [],
            "applied": True,
            "status": state["status"],
            "winner": state["winner"],
            "due_now": state["pending"],
        }
    )


def cmd_match_score(args: argparse.Namespace) -> int:
    state = load_state(args.id)
    if state is None:
        return fail(f"unknown match {args.id!r}")
    units = {
        unit: {
            "team_id": team,
            "grade": state["grades"].get(unit, 0.0),
            "mvp": False,
            "lvp": False,
        }
        for team in state["teams"]
        for unit in state["units"][team]
    }
    return emit(
        {
            "match_id": args.id,
            "scenario_id": state["scenario"],
            "mode": state["mode"],
            "status": state["status"],
            "winner": state["winner"],
            "outcome": dict(state["points"]),
            "units": {"match_id": args.id, "units": units},
        }
    )


# ── parser ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fake-league")
    top = parser.add_subparsers(dest="noun", required=True)

    team = top.add_parser("team").add_subparsers(dest="verb", required=True)
    register = team.add_parser("register")
    register.add_argument("team_id")
    register.add_argument("--name", default="")
    register.add_argument("--agent", action="append")
    register.add_argument("--apply", action="store_true")
    register.add_argument("--json", action="store_true")
    register.set_defaults(func=cmd_team_register)

    cmatch = top.add_parser("cmatch").add_subparsers(dest="verb", required=True)
    new = cmatch.add_parser("new")
    new.add_argument("--scenario", required=True)
    new.add_argument("--mode", default="competitive")
    new.add_argument("--seed", type=int, default=0)
    new.add_argument("--id", required=True)
    new.add_argument("--team", action="append")
    new.add_argument("--driver", action="append")
    new.add_argument("--apply", action="store_true")
    new.add_argument("--json", action="store_true")
    new.set_defaults(func=cmd_cmatch_new)

    show = cmatch.add_parser("show")
    show.add_argument("id")
    show.add_argument("--unit", default="")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_cmatch_show)

    act = cmatch.add_parser("act")
    act.add_argument("id")
    act.add_argument("--unit", required=True)
    act.add_argument("--action-json", dest="action_json", default="")
    act.add_argument("--apply", action="store_true")
    act.add_argument("--json", action="store_true")
    act.set_defaults(func=cmd_cmatch_act)

    tick = cmatch.add_parser("tick")
    tick.add_argument("id")
    tick.add_argument("--timeout-park", action="store_true")
    tick.add_argument("--apply", action="store_true")
    tick.add_argument("--json", action="store_true")
    tick.set_defaults(func=cmd_cmatch_tick)

    match = top.add_parser("match").add_subparsers(dest="verb", required=True)
    score = match.add_parser("score")
    score.add_argument("id")
    score.add_argument("--json", action="store_true")
    score.set_defaults(func=cmd_match_score)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
