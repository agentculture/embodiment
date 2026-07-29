#!/usr/bin/env python3
"""A stand-in for the ``league`` CLI, so the default suite needs no arena.

``tests/test_league_seat.py`` must be hermetic: no live model, no network, and
**no real league binary**. This module answers the exact subset of
league-of-agents' public CLI that ``examples/league_seat.py`` calls, in the
shapes league itself emits (verified against ``league 0.x`` on 2026-07-25):

* ``team register <id> --name N --agent <id>:<model>:<role> --apply --json``
* ``match new --scenario S --team A --team B --seed N --id ID
  --driver T:bot|stateless|resident --apply --json``
* ``match show ID --json`` → ``{"state", "legal_actions", "staged_teams",
  "last_turn_rejections", "driver_kinds", "map_read", "unit_comms"}``
* ``match act ID --team T --orders-json '{...}' --apply --json``
* ``match score ID --json``
* ``match replay ID --json``

It is deliberately NOT a reimplementation of the arena: the grid is small, the
turn limit is short, and resolution is a straight fold of the staged moves. What
it does reproduce faithfully is the *contract* — the JSON keys, the
``--driver`` residency echo, the ``resolves_turn`` handshake, and the
CWD-rooted ``.league/`` store — because that contract is what the seat host is
written against and what a hermetic test can honestly check. Even the status
vocabulary is league's own (``league/engine/state.py:22``: ``pending`` /
``active`` / ``finished``), so a test written against this stub cannot pin a
word the real arena never says.

The real thing is exercised by ``TestLiveArena`` in the test module, which is
skipped unless ``EMBODIMENT_LIVE_ARENA=1`` points it at an installed ``league``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

GRID_W, GRID_H = 8, 8
TURN_LIMIT = 4
MOVE_RANGE = 2

#: Two control points, far enough apart that "take cp-west" and "take cp-east"
#: send units in visibly different directions.
CONTROL_POINTS = [
    {"id": "cp-west", "pos": [1, 6], "owner": None, "hold": []},
    {"id": "cp-east", "pos": [7, 1], "owner": None, "hold": []},
]
RESOURCE_NODES = [{"id": "rn-mid", "pos": [4, 4], "remaining": 12}]

#: Where each side starts. The first ``--team`` gets the top-left corner.
HOME_CORNERS = [[(0, 0), (1, 0), (0, 1)], [(7, 7), (6, 7), (7, 6)]]


def store_root() -> Path:
    """league roots its store at ``<cwd>/.league``; so does this."""
    return Path.cwd() / ".league"


def team_path(team_id: str) -> Path:
    return store_root() / "teams" / f"{team_id}.json"


def match_path(match_id: str) -> Path:
    return store_root() / "matches" / match_id / "fake-state.json"


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def emit(payload: Any) -> int:
    print(json.dumps(payload, sort_keys=True))
    return 0


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


# ── team register ────────────────────────────────────────────────────────────


def cmd_team_register(args: argparse.Namespace) -> int:
    agents = []
    for spec in args.agent or ():
        parts = spec.split(":")
        if len(parts) < 3:
            return fail(f"bad --agent {spec!r}: use <id>:<model>:<role>")
        agents.append({"id": parts[0], "model": ":".join(parts[1:-1]), "role": parts[-1]})
    record = {"id": args.team_id, "name": args.name or args.team_id, "agents": agents}
    if args.apply:
        save_json(team_path(args.team_id), record)
    return emit({**record, "path": str(team_path(args.team_id)), "applied": bool(args.apply)})


# ── match new ────────────────────────────────────────────────────────────────


def cmd_match_new(args: argparse.Namespace) -> int:
    drivers: dict[str, str] = {}
    for spec in args.driver or ():
        team, _, kind = spec.partition(":")
        if kind not in ("bot", "stateless", "resident"):
            return fail(f"unknown driver kind {kind!r}")
        if team not in (args.team or ()):
            return fail(f"--driver references team {team!r}, not one of --team")
        drivers[team] = kind

    units: list[dict[str, Any]] = []
    teams: list[dict[str, Any]] = []
    for index, team_id in enumerate(args.team or ()):
        roster = load_json(team_path(team_id))
        if roster is None:
            return fail(f"team {team_id!r} is not registered")
        teams.append({**roster, "resources": 0})
        corner = HOME_CORNERS[index % len(HOME_CORNERS)]
        for slot, agent in enumerate(roster["agents"]):
            x, y = corner[slot % len(corner)]
            units.append(
                {
                    "id": f"{team_id}-u{slot + 1}",
                    "team_id": team_id,
                    "agent_id": agent["id"],
                    "role": agent["role"],
                    "pos": [x, y],
                    "carrying": 0,
                    "alive": True,
                }
            )

    state = {
        "match_id": args.match_id,
        "scenario_id": args.scenario,
        "seed": args.seed,
        "mode": args.mode,
        "turn": 0,
        "turn_limit": TURN_LIMIT,
        "grid_width": GRID_W,
        "grid_height": GRID_H,
        "status": "active",
        "winner": None,
        "teams": teams,
        "units": units,
        "control_points": [dict(cp) for cp in CONTROL_POINTS],
        "missions": [],
        "resource_nodes": [dict(node) for node in RESOURCE_NODES],
    }
    record = {
        "state": state,
        "driver_kinds": drivers,
        "pending": {},
        "rejections": [],
        "history": [],
    }
    if args.apply:
        save_json(match_path(args.match_id), record)
    return emit(
        {
            "match_id": args.match_id,
            "scenario": args.scenario,
            "mode": args.mode,
            "seed": args.seed,
            "teams": list(args.team or ()),
            "driver_kinds": drivers,
            "turn_limit": TURN_LIMIT,
            "applied": bool(args.apply),
            "log": str(match_path(args.match_id)),
        }
    )


# ── the board ────────────────────────────────────────────────────────────────


def legal_actions(state: dict[str, Any]) -> dict[str, Any]:
    actions: dict[str, Any] = {}
    for unit in state["units"]:
        if not unit["alive"]:
            continue
        x, y = unit["pos"]
        moves = [
            [nx, ny]
            for nx in range(GRID_W)
            for ny in range(GRID_H)
            if 0 < abs(nx - x) + abs(ny - y) <= MOVE_RANGE
        ]
        actions[unit["id"]] = {
            "move": moves,
            "gather": False,
            "deliver": False,
            "hold": True,
            "can_gather": False,
            "can_capture": False,
        }
    return actions


def cmd_match_show(args: argparse.Namespace) -> int:
    record = load_json(match_path(args.match_id))
    if record is None:
        return fail(f"no match {args.match_id!r}")
    return emit(
        {
            "state": record["state"],
            "legal_actions": legal_actions(record["state"]),
            "staged_teams": sorted(record["pending"]),
            "last_turn_rejections": record["rejections"],
            "driver_kinds": record["driver_kinds"],
            "map_read": {},
            "unit_comms": {},
        }
    )


# ── match act ────────────────────────────────────────────────────────────────


def resolve(record: dict[str, Any]) -> dict[str, Any]:
    """Apply every staged move, then advance the clock."""
    state = record["state"]
    legal = legal_actions(state)
    rejections: list[dict[str, Any]] = []
    by_id = {unit["id"]: unit for unit in state["units"]}
    for team_id in sorted(record["pending"]):
        orders = record["pending"][team_id]
        for action in orders.get("actions", []):
            unit = by_id.get(str(action.get("unit_id")))
            if unit is None or unit["team_id"] != team_id:
                rejections.append(
                    {
                        "team_id": team_id,
                        "unit_id": action.get("unit_id"),
                        "reason": "not your unit",
                    }
                )
                continue
            if action.get("action") != "move":
                continue
            target = [int(v) for v in action.get("to") or []]
            if target not in (legal.get(unit["id"], {}).get("move") or []):
                rejections.append(
                    {"team_id": team_id, "unit_id": unit["id"], "reason": "illegal move"}
                )
                continue
            unit["pos"] = target

    state["turn"] += 1
    record["history"].append(
        {"turn": state["turn"], "pending": record["pending"], "positions": positions(state)}
    )
    record["pending"] = {}
    record["rejections"] = rejections
    if state["turn"] >= state["turn_limit"]:
        state["status"] = "finished"
        state["winner"] = nearest_to_west(state)
    return {
        "turn": state["turn"],
        "status": state["status"],
        "winner": state["winner"],
        "events": len(state["units"]),
        "rejected": len(rejections),
    }


def positions(state: dict[str, Any]) -> dict[str, list[int]]:
    return {unit["id"]: list(unit["pos"]) for unit in state["units"]}


def nearest_to_west(state: dict[str, Any]) -> Optional[str]:
    """Whoever ends up closest to ``cp-west``. Deterministic, and no more."""
    west = CONTROL_POINTS[0]["pos"]
    best: Optional[tuple[int, str]] = None
    for unit in state["units"]:
        distance = abs(unit["pos"][0] - west[0]) + abs(unit["pos"][1] - west[1])
        candidate = (distance, unit["team_id"])
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else None


def cmd_match_act(args: argparse.Namespace) -> int:
    record = load_json(match_path(args.match_id))
    if record is None:
        return fail(f"no match {args.match_id!r}")
    state = record["state"]
    if state["status"] != "active":
        return fail(f"match {args.match_id!r} is {state['status']}, not active")
    team_ids = {team["id"] for team in state["teams"]}
    if args.team not in team_ids:
        return fail(f"team {args.team!r} is not in this match")
    try:
        orders = json.loads(args.orders_json or "{}")
    except ValueError as exc:
        return fail(f"--orders-json is not valid JSON: {exc}")
    if not isinstance(orders, dict):
        return fail("--orders-json must be a JSON object")

    staged = set(record["pending"]) | {args.team}
    resolves = staged >= team_ids
    payload: dict[str, Any] = {
        "match_id": args.match_id,
        "team": args.team,
        "orders": orders,
        "staged_teams": sorted(staged),
        "resolves_turn": resolves,
        "applied": bool(args.apply),
    }
    if args.apply:
        record["pending"][args.team] = orders
        if resolves:
            payload["resolution"] = resolve(record)
        save_json(match_path(args.match_id), record)
    return emit(payload)


# ── score and replay ─────────────────────────────────────────────────────────


def cmd_match_score(args: argparse.Namespace) -> int:
    record = load_json(match_path(args.match_id))
    if record is None:
        return fail(f"no match {args.match_id!r}")
    state = record["state"]
    west = CONTROL_POINTS[0]["pos"]
    outcome = {}
    for team in state["teams"]:
        units = [u for u in state["units"] if u["team_id"] == team["id"]]
        closeness = sum(
            max(0, 20 - abs(u["pos"][0] - west[0]) - abs(u["pos"][1] - west[1])) for u in units
        )
        outcome[team["id"]] = {"total": closeness, "control": 0, "resources": 0}
    return emit(
        {
            "match_id": args.match_id,
            "scenario_id": state["scenario_id"],
            "turns_played": state["turn"],
            "winner": state["winner"],
            "outcome": outcome,
        }
    )


def cmd_match_replay(args: argparse.Namespace) -> int:
    record = load_json(match_path(args.match_id))
    if record is None:
        return fail(f"no match {args.match_id!r}")
    state = record["state"]
    frames = [entry["positions"] for entry in record["history"]]
    body = {
        "match_id": state["match_id"],
        "scenario_id": state["scenario_id"],
        "seed": state["seed"],
        "turn_limit": state["turn_limit"],
        "frames": frames,
        "winner": state["winner"],
    }
    body["digest"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return emit(body)


# ── the parser (the same shape as league's own) ──────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fake-league")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="noun", required=True)

    team = sub.add_parser("team").add_subparsers(dest="verb", required=True)
    register = team.add_parser("register")
    register.add_argument("team_id")
    register.add_argument("--name", default="")
    register.add_argument("--agent", action="append")
    register.add_argument("--apply", action="store_true")
    register.add_argument("--json", action="store_true")
    register.set_defaults(handler=cmd_team_register)

    match = sub.add_parser("match").add_subparsers(dest="verb", required=True)

    new = match.add_parser("new")
    new.add_argument("--scenario", required=True)
    new.add_argument("--mode", default="competitive")
    new.add_argument("--team", action="append")
    new.add_argument("--seed", type=int, default=1)
    new.add_argument("--id", dest="match_id", default="match")
    new.add_argument("--driver", action="append")
    new.add_argument("--apply", action="store_true")
    new.add_argument("--json", action="store_true")
    new.set_defaults(handler=cmd_match_new)

    for name, handler in (
        ("show", cmd_match_show),
        ("score", cmd_match_score),
        ("replay", cmd_match_replay),
    ):
        verb = match.add_parser(name)
        verb.add_argument("match_id")
        verb.add_argument("--json", action="store_true")
        verb.set_defaults(handler=handler)

    act = match.add_parser("act")
    act.add_argument("match_id")
    act.add_argument("--team", required=True)
    act.add_argument("--orders-json", dest="orders_json", default="")
    act.add_argument("--apply", action="store_true")
    act.add_argument("--json", action="store_true")
    act.set_defaults(handler=cmd_match_act)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
