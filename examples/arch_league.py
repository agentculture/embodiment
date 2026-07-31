#!/usr/bin/env python3
"""arch_league — the four architectures over league's continuous decision points.

Plan task **t6** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`). It adds the
**league lane** to the architecture series: the same four arms task ``t5``
carved (``E`` existing / ``W`` worker-solo / ``M`` manager / ``H`` hybrid),
driving ``league cmatch`` decision points instead of the challenge rungs.

It adds a lane, not a second harness
------------------------------------
Everything measured here is ``t5``'s and everything spawned here is
``t1``/``t4``'s:

============================  ==============================================
what                          whose
============================  ==============================================
arms, sampling table, dials   ``examples/arch_arms.py`` (t5) — ``ARMS``,
                              ``ArchConfig``, ``CallLog``, ``CallRecord``,
                              ``ScriptedSeams``/``LiveSeams``, the senses
                              hash, ``AttemptRecord``/``CellResult``, and the
                              ``analyse`` refusal, composed verbatim
the delegate + the fan-out    ``examples/orchestrator_tools.py`` (t1, t4) —
                              ``build_fanout_seam``, ``plan_fanout``,
                              ``partition``, ``MAX_FANOUT_WIDTH`` and
                              ``FANOUT_ACCOUNTING_RULE``
the arena, through its CLI    ``examples/league_commander.py`` (t28) —
                              ``LeagueCli``, ``team_ids``, ``render_menu``
============================  ==============================================

What is genuinely new is the **seat**: one tool surface, read off the arm, that
turns a round of due units into orders — and the **routing log** that makes the
hybrid arm falsifiable.

The role mapping (frame claim ``c38``, operator decision 2026-07-31)
--------------------------------------------------------------------
The cortex is the **leader / player** seat: it commands, decides, and keeps
final authority — ``order`` is the only verb that reaches the arena and it
lives on the seat alone. The worker drives **the units, all of them**, as the
explorer/actor that moves, takes locations and gathers. The worker's ~14-way
concurrency is why a turn's units are dispatched as **one fan-out** rather than
one delegation at a time: driving every idle unit simultaneously is the natural
shape of a continuous match, not an optimisation bolted onto a serial loop.

One drive per **round**, not per decision point
-----------------------------------------------
``league cmatch`` poses one question per unit, at the instant that unit goes
idle, and several units commonly go idle together. This harness drives the seat
**once per round** — every unit due at that instant, in one bounded loop — for
exactly the reason above: a per-decision drive can never fan out, because it
only ever holds one unit. Within a round the seat still answers each unit
separately, and each answer lands its own routing record, so "per decision
point" is preserved where it matters.

The routing log is the hybrid arm's whole falsifiability
--------------------------------------------------------
A hybrid that routes nothing is arm ``E`` wearing a different label; a hybrid
that routes everything is arm ``M``. So every decision point in every arm lands
one :class:`RouteRecord` naming **where the work went**, **on whose authority**,
and **why**:

* ``basis="architecture"`` — the arm's shape decided it and no mind judged
  anything. The two flat arms, always.
* ``basis="arm-mandate"`` — the manager, which may not order a unit it did not
  route. The model stated a reason; it had no alternative destination.
* ``basis="orchestrator"`` — the hybrid, the only arm where the destination is
  a genuine decision. Routing carries the model's ``reason``; **keeping** a unit
  requires ``kept_because`` and is refused without it, so a kept decision can
  never be logged with an empty reason.

``degenerate_routing`` is reported per match: a hybrid whose routing never
varied measured nothing. Whether such a cell is graded is task ``t11``'s rule to
pin, not this module's to invent.

What a scripted run may claim: nothing
--------------------------------------
The scripted minds answer by rule and are deliberately bad at the game — the
unit always proposes index 0 and the seat always orders the last index — so a
hermetic run exercises the wiring, the routing log, the fan-out accounting and
the artifact without ever looking like play. Every graded attempt carries
``scripted: true`` and :data:`SCRIPTED_NOTE`.

And a caution that must ride with any league verdict, scripted or live: league's
own outcome metric tied 0-0 in all six matches of the prior head-to-head
(``docs/live-test-results/league-h2h.md``). A win count is the cell score here
because it is the arena's own verdict, but it is a **weak** instrument and this
lane inherits that weakness rather than curing it.

No live dial happens here
-------------------------
Two gates, both the repo's existing ones: ``EMBODIMENT_LIVE_RIG`` for models
(:func:`arch_arms.require_live_rig`) and ``EMBODIMENT_LIVE_ARENA`` for the real
``league`` binary (:func:`require_live_arena`). Live runs are frozen this cycle,
so ``play --live`` refuses even with both gates open — task ``t12`` runs the
series.

Usage::

    uv run python examples/arch_league.py plan
    uv run python examples/arch_league.py play --root /tmp/al --league league
    uv run python examples/arch_league.py routes --log /tmp/al.jsonl
    uv run python examples/arch_league.py analyse --log /tmp/al.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    LoopAborted,
    ModelResponse,
    Task,
    ToolCall,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    ledger,
    run,
)
from examples import arch_arms as aa  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import orchestrator_tools as ot  # noqa: E402

__all__ = [
    "ARCHITECTURE_REASON",
    "BASES",
    "BASIS_ARCHITECTURE",
    "BASIS_ARM_MANDATE",
    "BASIS_ORCHESTRATOR",
    "BASIS_UNDECIDED",
    "Batch",
    "DEFAULT_LEAGUE_BIN",
    "DEFAULT_MAX_ROUNDS",
    "FANOUT_ACCOUNTING_RULE",
    "KIND_MATCH",
    "KIND_ROUND",
    "KIND_ROUTE",
    "LADDER_BY_ID",
    "LEAGUE_FINAL_AUTHORITY_TOOLS",
    "LEAGUE_LADDER",
    "LIVE_ARENA_ENV",
    "LIVE_GATE_ENV",
    "LeagueMatchRecord",
    "LeagueRung",
    "LeagueSeat",
    "LiveArenaClosed",
    "NOTE_TOOL",
    "ORDER_MARKER",
    "ORDER_TOOL",
    "Order",
    "Perception",
    "PerceptionRoute",
    "ROLE_MAPPING",
    "ROUND_MARKER",
    "ROUTED_CORTEX",
    "ROUTED_WORKER",
    "ROUTE_MARKER",
    "ROUTE_REGISTRY",
    "ROUTE_TEXT",
    "ROUTE_TOOL",
    "RoundRecord",
    "RoundView",
    "RouteRecord",
    "RoutingIntent",
    "SCRIPTED_KEEP_REASON",
    "SCRIPTED_NOTE",
    "SCRIPTED_PROPOSAL",
    "SCRIPTED_ROUTABLE_ROLES",
    "SCRIPTED_ROUTE_REASON",
    "ThreadSafeCallLog",
    "UNIT_TOOLS",
    "UnitBrief",
    "analyse",
    "append_jsonl",
    "build_live_seams",
    "degenerate",
    "describe_text",
    "main",
    "play_match",
    "preamble",
    "read_proposed_index",
    "read_round",
    "register_route",
    "register_teams",
    "require_live_arena",
    "resolve_arena",
    "roster_model",
    "round_instruction",
    "round_view",
    "route_for",
    "routing_table",
    "run_round",
    "run_series",
    "scripted_commander",
    "scripted_seams",
    "scripted_unit",
    "seat_schema",
    "seat_tools",
    "system_for",
    "unit_brief",
    "unit_schema",
    "unit_subtask",
]

# ── the gates ────────────────────────────────────────────────────────────────

#: The repo-wide model gate, re-exported so a reader of this file sees both.
LIVE_GATE_ENV = aa.LIVE_GATE_ENV

#: The repo-wide arena gate — ``tests/test_league_seat.py`` and
#: ``tests/test_arena_series.py`` already read this one.
LIVE_ARENA_ENV = "EMBODIMENT_LIVE_ARENA"

#: league's own console script. Reaching it needs :data:`LIVE_ARENA_ENV`.
DEFAULT_LEAGUE_BIN = "league"


class LiveArenaClosed(RuntimeError):
    """The real arena was asked for without the gate open."""


def require_live_arena(env: Optional[Mapping[str, str]] = None) -> None:
    """Refuse the real arena unless ``EMBODIMENT_LIVE_ARENA=1``."""
    live_env: Mapping[str, str] = os.environ if env is None else env
    if live_env.get(LIVE_ARENA_ENV) != "1":
        raise LiveArenaClosed(
            f"reaching the real arena needs {LIVE_ARENA_ENV}=1 in the environment; "
            "a stand-in binary (tests/fake_cleague.py) needs nothing"
        )


def resolve_arena(binary: Optional[str], *, env: Optional[Mapping[str, str]] = None) -> str:
    """Which program is the arena — and whether it may be reached at all.

    An *explicit* stand-in is an explicit operator act and needs no gate; the
    real ``league`` binary is gated whether it was named or defaulted to, so a
    forgotten ``--league`` cannot start a live match by accident.
    """
    resolved = (binary or DEFAULT_LEAGUE_BIN).strip() or DEFAULT_LEAGUE_BIN
    parts = shlex.split(resolved)
    head = Path(parts[0]).name if parts else DEFAULT_LEAGUE_BIN
    if head == DEFAULT_LEAGUE_BIN:
        require_live_arena(env)
    return resolved


# ── the rung ─────────────────────────────────────────────────────────────────

#: The arena's own house opponent, and the driver kinds either side runs under.
HOUSE_BOT = "house-bot"
OPPONENT_DRIVER = "bot"
OUR_DRIVER = "stateless"

#: How many ``show`` iterations one match may take before the harness gives up.
#: Finite by construction: a match whose status never leaves ``active`` must not
#: park the series forever.
DEFAULT_MAX_ROUNDS = 64

#: Recorded when a round arrives with no unit holding a legal action.
NO_LEGAL_ACTIONS = "no-legal-actions"

#: Recorded on a routed unit whose batch the loop declined to dispatch.
BATCH_NOT_DISPATCHED = "batch-not-dispatched"


@dataclass(frozen=True)
class LeagueRung:
    """One league rung: a scenario, its roster shape, and the seeds to play."""

    id: str
    scenario: str
    roles: tuple[str, ...]
    seeds: tuple[int, ...]
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scenario": self.scenario,
            "roles": list(self.roles),
            "seeds": list(self.seeds),
            "why": self.why,
        }


#: One rung. The escalation ladder is task ``t11``'s to pin, not this task's to
#: guess — what t6 owes is a rung all four arms can actually play.
LEAGUE_LADDER: tuple[LeagueRung, ...] = (
    LeagueRung(
        id="L1",
        scenario=lc.ESCALATION_SCENARIO,
        roles=lc.SCENARIO_ROLES[lc.ESCALATION_SCENARIO],
        seeds=lc.SEEDS,
        why=(
            "three roles per side, so a round holds three idle units at once: wide "
            "enough for the fan-out to be a fan-out, and mixed enough that the "
            "hybrid can both route and keep. A two-role scenario collapses the "
            "hybrid into the manager and its cell would measure nothing"
        ),
    ),
)

LADDER_BY_ID = {rung.id: rung for rung in LEAGUE_LADDER}


def roster_model(model: str) -> str:
    """Make a model id safe for league's ``ID:MODEL:ROLE`` roster spec.

    league splits that spec on ``:`` and requires exactly three parts, so a
    model id carrying a colon (``scripted:worker``, and any provider that ever
    uses one) would be rejected at registration. Substituting the separator is
    the whole of the change: the id is a label the arena records, never dialled.
    """
    return str(model or "").replace(":", "_") or "unknown"


def register_teams(cli: lc.LeagueCli, *, arm: aa.Arm, rung: LeagueRung, unit_model: str) -> None:
    """Declare A MODEL PER UNIT, so the arena keeps its own fairness record."""
    ours, theirs = lc.team_ids(arm.id)
    our_agents: list[str] = []
    their_agents: list[str] = []
    for index, role in enumerate(rung.roles, start=1):
        our_agents += ["--agent", f"b{index}:{roster_model(unit_model)}:{role}"]
        their_agents += ["--agent", f"r{index}:{HOUSE_BOT}:{role}"]
    cli("team", "register", ours, "--name", f"arch-{arm.id}", *our_agents, "--apply", "--json")
    cli("team", "register", theirs, "--name", HOUSE_BOT, *their_agents, "--apply", "--json")


# ── the perception seam (the hook task t10 registers against) ────────────────

#: t5's route id, reused rather than re-declared: a cell key that differed
#: between the two lanes would make one ``analyse`` unable to read both.
ROUTE_TEXT = aa.ROUTE_TEXT


@dataclass(frozen=True)
class Perception:
    """What one decision point looks like to a mind, and its twin key.

    ``snapshot_hash`` is empty on the text route and is **the** field task t10
    fills: ``examples/map_render.py`` hashes exactly the fogged state it draws,
    so an image cell and its text twin are pairable by that digest. Nothing here
    computes one — this module renders no image and imports no renderer — but
    every routing record carries the field, so t10 adds a route rather than
    rewriting the record.
    """

    route: str
    text: str
    parts: tuple[Any, ...] = ()
    snapshot_hash: str = ""


@dataclass(frozen=True)
class PerceptionRoute:
    """One way a briefing reaches a mind. Registered, never branched on."""

    id: str
    why: str
    describe: Callable[[Any, str], Perception]


ROUTE_REGISTRY: dict[str, PerceptionRoute] = {}


def register_route(route: PerceptionRoute, *, replace: bool = False) -> None:
    """Add a perception route. Clobbering a live one needs saying so out loud."""
    if route.id in ROUTE_REGISTRY and not replace:
        raise ValueError(
            f"route {route.id!r} is already registered; pass replace=True to take it over"
        )
    ROUTE_REGISTRY[route.id] = route


def route_for(route_id: str) -> PerceptionRoute:
    if route_id not in ROUTE_REGISTRY:
        raise ValueError(f"no perception route {route_id!r}; registered: {sorted(ROUTE_REGISTRY)}")
    return ROUTE_REGISTRY[route_id]


def describe_text(briefing: Any, team_id: str = "") -> Perception:
    """The text route: the unit's own block, numbered exactly as league numbers it.

    ``render_menu`` is league_commander's — the index a model answers with and
    the index this harness submits have to be the same one, and two renderers
    are two chances for them not to be.
    """
    payload = briefing if isinstance(briefing, Mapping) else {}
    you = payload.get("you") or {}
    menu = list(payload.get("menu") or [])
    text = (
        f"UNIT {you.get('unit_id')} ({you.get('role')}) of team "
        f"{you.get('team_id') or team_id}, at {you.get('pos')}, "
        f"carrying {you.get('carrying')}.\n"
        f"MENU (answer with one of these indices):\n{lc.render_menu(menu)}\n"
        f"OUTLOOK: {json.dumps(payload.get('outlook') or [], sort_keys=True)}"
    )
    return Perception(route=ROUTE_TEXT, text=text)


register_route(
    PerceptionRoute(
        id=ROUTE_TEXT,
        why=(
            "no image reaches any mind: every arm sees the same fogged briefing as "
            "text, so only the architecture varies. Task t10 registers the image "
            "route beside this one and pairs the two by snapshot hash"
        ),
        describe=describe_text,
    )
)


# ── the seat's tool surface ──────────────────────────────────────────────────

#: Hand a round's units to the worker. Only the orchestrated arms hold it.
ROUTE_TOOL = "delegate_units"

#: The only verb that reaches the arena, in every arm. This lane's final
#: authority is ``order`` rather than t1's ``finish`` because the arena poses one
#: question per unit: a round is over when its last question has an answer, and
#: a separate terminal verb would only be one more thing a model can forget.
ORDER_TOOL = "order"
NOTE_TOOL = "note"
LEAGUE_FINAL_AUTHORITY_TOOLS: tuple[str, ...] = (ORDER_TOOL,)

#: The unit's complete surface — t1's worker surface, unchanged. One verb: hand
#: it back. It shares no member with :data:`LEAGUE_FINAL_AUTHORITY_TOOLS`.
UNIT_TOOLS = ot.WORKER_TOOLS

#: t4's rule, quoted rather than restated. Every batch here obeys it because
#: every batch here is dispatched by t4's own seam.
FANOUT_ACCOUNTING_RULE = ot.FANOUT_ACCOUNTING_RULE

#: Stamped on tool results so a mind can read its own round state out of the
#: transcript rather than holding hidden state. One id per marker line,
#: deliberately: a line that also listed the units still due would make "was
#: this one done?" unanswerable by reading.
ROUTE_MARKER = "[routed]"
ORDER_MARKER = "[ordered]"
ROUND_MARKER = "ROUND (JSON):"

_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": ROUTE_TOOL,
        "description": (
            "Hand these units' decisions to the worker. Each one is driven by its "
            "own worker agent, in parallel, and reports back to you; none of them "
            "can act on the arena. Say why each unit goes out."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "units": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit_id": {"type": "string"},
                            "reason": {
                                "type": "string",
                                "description": "Why this unit's work goes to the worker.",
                            },
                        },
                        "required": ["unit_id", "reason"],
                    },
                }
            },
            "required": ["units"],
        },
    },
}

_NOTE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NOTE_TOOL,
        "description": "Record a working note for yourself.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}

_REPORT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": UNIT_TOOLS[0],
        "description": "Hand your unit's recommendation back to the commander. Your only verb.",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "string",
                    "description": "`menu_index=<n>: <one sentence of why>`.",
                }
            },
            "required": ["findings"],
        },
    },
}


def _order_schema(arm: aa.Arm) -> dict[str, Any]:
    """The order verb. The hybrid's carries one extra, load-bearing field.

    ``kept_because`` exists only where keeping is a decision, and the seat
    refuses a kept order without it — which is what stops the hybrid's routing
    log from filling up with empty reasons.
    """
    properties: dict[str, Any] = {
        "unit_id": {"type": "string"},
        "menu_index": {"type": "integer"},
        "why": {"type": "string", "description": "Why this action for this unit."},
    }
    if arm.delegates and arm.keeps_work:
        properties["kept_because"] = {
            "type": "string",
            "description": (
                "REQUIRED when you did not hand this unit to the worker: say why "
                "you kept this decision yourself."
            ),
        }
    return {
        "type": "function",
        "function": {
            "name": ORDER_TOOL,
            "description": (
                "Submit this unit's action to the arena. Yours alone — nothing else "
                "reaches the match, at any depth."
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["unit_id", "menu_index", "why"],
            },
        },
    }


def seat_schema(arm: aa.Arm) -> list[dict[str, Any]]:
    """The seat's schema for one arm. Nothing here branches on an arm *id*."""
    schema: list[dict[str, Any]] = []
    if arm.delegates:
        schema.append(dict(_ROUTE_SCHEMA))
    schema.append(_order_schema(arm))
    schema.append(dict(_NOTE_SCHEMA))
    return schema


def seat_tools(arm: aa.Arm) -> tuple[str, ...]:
    return tuple(entry["function"]["name"] for entry in seat_schema(arm))


def unit_schema() -> list[dict[str, Any]]:
    return [dict(_REPORT_SCHEMA)]


# ── the prompts ──────────────────────────────────────────────────────────────

_RULES = (
    "The match runs on a timeline, not turns. Each menu entry carries its in-game "
    "duration and the absolute completion_time it would land at, so a race is "
    "plannable: if the enemy defender completes its take at t=15 and you can "
    "complete yours at t=13, you win the post. Points come from owned control "
    "points, delivered resources and completed missions."
)

FLAT_SYSTEM = (
    "You command a team in a real-time strategy match. Several of your units are "
    "idle at once; you decide every one of their actions yourself, and there is no "
    "one else to consult. The `order` tool call is what reaches the arena, and a "
    "round is finished when every idle unit has one.\n\n" + _RULES
)

MANAGER_SYSTEM = (
    "You are the COMMANDER of a team in a real-time strategy match, and you hold "
    "final authority: the `order` tool call is the only thing that reaches the "
    "arena.\n\n"
    "Each of your units is driven by its own worker agent — a different mind from "
    "yours. Hand every idle unit to the worker with `delegate_units`, in ONE call, "
    "so they are worked simultaneously; read what comes back, then issue each "
    "order. You may follow a recommendation or override it, but the ground work is "
    "the worker's: you cannot order a unit you did not hand out.\n\n" + _RULES
)

HYBRID_SYSTEM = (
    "You are the COMMANDER of a team in a real-time strategy match, and you hold "
    "final authority: the `order` tool call is the only thing that reaches the "
    "arena.\n\n"
    "You have a worker available, and each unit you hand it is driven by its own "
    "worker agent, in parallel. Route the units whose decision you judge routine "
    "with `delegate_units`, saying why each goes out, and keep the ones you judge "
    "consequential for yourself. When you order a unit you kept, say why you kept "
    "it in `kept_because` — that is part of the decision, not paperwork.\n\n" + _RULES
)

UNIT_SYSTEM = (
    "You are a WORKER driving ONE unit in a real-time strategy match. Work out "
    "which menu index that unit should take and hand it back with `report`, as "
    "`menu_index=<n>: <one sentence of why>`.\n\n"
    "You do not act on the arena and you do not decide what happens next: your "
    "commander issues the order and may override you. Reporting back is the whole "
    "of your job.\n\n" + _RULES
)


def system_for(arm: aa.Arm) -> str:
    """The seat's framing for one arm. Read off the arm, never off its id."""
    if not arm.delegates:
        return FLAT_SYSTEM
    return HYBRID_SYSTEM if arm.keeps_work else MANAGER_SYSTEM


# ── the round, as the seat sees it ───────────────────────────────────────────


@dataclass(frozen=True)
class UnitBrief:
    """One due unit: what it is, what it may do, and how it was perceived."""

    unit_id: str
    role: str
    game_time: int
    options: int
    menu: tuple[dict[str, Any], ...]
    perception: Perception
    briefing: Mapping[str, Any]


def unit_brief(briefing: Any, *, route: str = ROUTE_TEXT, team_id: str = "") -> UnitBrief:
    payload = dict(briefing) if isinstance(briefing, Mapping) else {}
    you = payload.get("you") or {}
    menu = tuple(dict(entry) for entry in (payload.get("menu") or []))
    return UnitBrief(
        unit_id=str(you.get("unit_id") or ""),
        role=str(you.get("role") or ""),
        game_time=int(payload.get("game_time") or 0),
        options=len(menu),
        menu=menu,
        perception=route_for(route).describe(payload, str(you.get("team_id") or team_id)),
        briefing=payload,
    )


@dataclass(frozen=True)
class RoundView:
    """Everything one drive is handed. Rendered once, read back by its own reader."""

    arm: str
    match_id: str
    team_id: str
    round_index: int
    game_time: int
    units: tuple[UnitBrief, ...]
    board: Mapping[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "team_id": self.team_id,
            "round": self.round_index,
            "game_time": self.game_time,
            "units": [
                {"unit_id": unit.unit_id, "role": unit.role, "options": unit.options}
                for unit in self.units
            ],
        }


def round_view(
    *,
    arm: aa.Arm,
    match_id: str,
    team_id: str,
    round_index: int,
    units: Sequence[UnitBrief],
    board: Optional[Mapping[str, Any]] = None,
) -> RoundView:
    ordered = tuple(units)
    return RoundView(
        arm=arm.id,
        match_id=match_id,
        team_id=team_id,
        round_index=round_index,
        game_time=ordered[0].game_time if ordered else 0,
        units=ordered,
        board=dict(board or {}),
    )


def _round_tail(arm: aa.Arm) -> str:
    if not arm.delegates:
        return "Issue every idle unit's order yourself."
    if arm.keeps_work:
        return (
            f"Route the routine decisions to the worker with `{ROUTE_TOOL}` and keep "
            "the consequential ones; every order you issue for a unit you kept needs "
            "`kept_because`."
        )
    return (
        f"Hand every idle unit to the worker with `{ROUTE_TOOL}` in one call, then "
        "issue each order once they report."
    )


def round_instruction(view: RoundView, *, arm: aa.Arm) -> str:
    """The round, as prose a model reads and as one line a script can parse.

    The machine-readable line is league_seat's ``OBSERVATION (JSON):`` idiom:
    the scripted minds answer from it, and a live model gets the same content
    twice rather than a second, divergent description.
    """
    blocks = "\n\n".join(unit.perception.text for unit in view.units)
    return (
        f"{ROUND_MARKER} {json.dumps(view.summary(), sort_keys=True)}\n\n"
        f"Decision round {view.round_index} at game_time={view.game_time}. "
        f"{len(view.units)} of your units are idle and need an action.\n\n"
        f"{blocks}\n\n"
        f"BOARD:\n{json.dumps(dict(view.board), sort_keys=True)}\n\n"
        f"{_round_tail(arm)}"
    )


def read_round(text: str) -> dict[str, Any]:
    """Read the round line back. Unparsable is ``{}``, never a guess."""
    for line in reversed(str(text or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith(ROUND_MARKER):
            try:
                parsed = json.loads(stripped[len(ROUND_MARKER) :].strip())
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def unit_subtask(unit: UnitBrief) -> str:
    """What one worker is asked. The unit's own briefing, and the boundary."""
    return (
        f"Drive unit {unit.unit_id} ({unit.role}) at game_time={unit.game_time}.\n\n"
        f"{unit.perception.text}\n\n"
        "Work out which menu index this unit should take and hand it back with "
        "`report`, as `menu_index=<n>: <one sentence of why>`. You cannot act on "
        "the arena; your commander issues the order."
    )


_INDEX_RE = re.compile(r"menu_index\s*=\s*(\d+)")


def read_proposed_index(text: str) -> Optional[int]:
    """Read a worker's proposed index out of its report. **Diagnostic only.**

    The commander reads the worker's prose, not this. What this feeds is the
    ``proposed_index`` / ``overridden`` columns, so an unreadable report is
    ``None`` — never a guess, and never a number the record then compares.
    """
    found = _INDEX_RE.search(str(text or ""))
    if found is None:
        return None
    return int(found.group(1))


# ── the routing log ──────────────────────────────────────────────────────────

ROUTED_WORKER = aa.ROLE_WORKER
ROUTED_CORTEX = aa.ROLE_CORTEX

#: The arm's shape decided; no mind judged anything.
BASIS_ARCHITECTURE = "architecture"
#: The arm required the routing. The reason is the model's; the destination was
#: not its choice.
BASIS_ARM_MANDATE = "arm-mandate"
#: The model chose the destination and said why. The hybrid, and only it.
BASIS_ORCHESTRATOR = "orchestrator"
#: The drive ended without ever deciding where this unit's work went.
BASIS_UNDECIDED = "undecided"
BASES = (BASIS_ARCHITECTURE, BASIS_ARM_MANDATE, BASIS_ORCHESTRATOR, BASIS_UNDECIDED)

#: What a flat arm's routing record says. Fixed text, and labelled
#: ``basis=architecture`` beside it, so nobody can read it as a judgement.
ARCHITECTURE_REASON: dict[str, str] = {
    aa.ARM_EXISTING: "arm E is flat: the cortex decides every unit itself",
    aa.ARM_WORKER_SOLO: "arm W is flat: the worker is the top-level seat and decides alone",
}

KIND_ROUTE = "route"
KIND_ROUND = "round"
KIND_MATCH = "match"


def degenerate(routed_to: Sequence[str]) -> bool:
    """Did the routing never vary? Only meaningful for the hybrid arm.

    A hybrid that routed everything is the manager; one that kept everything is
    arm E. Either way its cell measured the arm it collapsed into, not itself.
    An empty sequence is degenerate too: no decision points, no contrast.
    """
    return len({value for value in routed_to if value}) < 2


@dataclass
class RouteRecord:
    """One decision point: where the work went, on whose authority, and why."""

    arm: str
    rung: str
    match_key: str
    match_id: str
    round_index: int
    decision_index: int
    unit_id: str
    role: str
    game_time: int
    options: int
    route: str
    snapshot_hash: str = ""
    routed_to: str = ""
    basis: str = BASIS_UNDECIDED
    reason: str = ""
    batch_task_id: str = ""
    unit_task_id: str = ""
    dispatched: bool = False
    refusal: str = ""
    unit_report: str = ""
    unit_exit_reason: str = ""
    proposed_index: Optional[int] = None
    ordered_index: Optional[int] = None
    order_why: str = ""
    overridden: Optional[bool] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_ROUTE,
            "arm": self.arm,
            "rung": self.rung,
            "match_key": self.match_key,
            "match_id": self.match_id,
            "round_index": self.round_index,
            "decision_index": self.decision_index,
            "unit_id": self.unit_id,
            "role": self.role,
            "game_time": self.game_time,
            "options": self.options,
            "route": self.route,
            "snapshot_hash": self.snapshot_hash,
            "routed_to": self.routed_to,
            "basis": self.basis,
            "reason": self.reason,
            "batch_task_id": self.batch_task_id,
            "unit_task_id": self.unit_task_id,
            "dispatched": self.dispatched,
            "refusal": self.refusal,
            "unit_report": self.unit_report,
            "unit_exit_reason": self.unit_exit_reason,
            "proposed_index": self.proposed_index,
            "ordered_index": self.ordered_index,
            "order_why": self.order_why,
            "overridden": self.overridden,
        }


def routing_table(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every routing decision in a committed artifact, in the order it happened."""
    return [dict(record) for record in records if record.get("kind") == KIND_ROUTE]


# ── the seat ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoutingIntent:
    """Where one unit's work goes, decided before the order is issued."""

    unit_id: str
    routed_to: str
    basis: str
    reason: str
    batch_index: int = -1
    batch_task_id: str = ""
    unit_task_id: str = ""


@dataclass(frozen=True)
class Order:
    """One unit's submitted action. The only thing that reaches the arena."""

    unit_id: str
    menu_index: int
    why: str
    kept_because: str = ""


@dataclass
class Batch:
    """One ``delegate_units`` call, as the seat planned it before dispatch."""

    index: int
    batch_task_id: str
    unit_ids: tuple[str, ...]
    subtasks: tuple[str, ...]
    grant: int
    dispatched: bool = False


def _menu_index(arguments: Mapping[str, Any], options: int, *, unit_id: str) -> int:
    """Coerce and range-check a menu index, naming the unit it belongs to.

    Shaped exactly like ``league_commander``'s: a :class:`ToolError` is one
    self-correcting step inside the budget, where a polite error string would be
    a *success* the model reads as prose.
    """
    raw = arguments.get("menu_index")
    try:
        index = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ToolError(f"menu_index for {unit_id} must be an integer, got {raw!r}") from exc
    if not 0 <= index < options:
        raise ToolError(f"menu_index {index} for {unit_id} is out of range; pick 0..{options - 1}")
    return index


class LeagueSeat:
    """One tool surface for all four arms. The arm is data; nothing branches on its id.

    The seat holds ``order`` in every arm — that is what "the cortex keeps final
    authority" means here, and in arm ``W`` the worker *is* the seat and holds it
    for the same reason. What the arm changes is where a unit's decision may come
    from:

    * flat arms hold no routing verb at all, and every unit carries an
      ``architecture`` intent from construction;
    * the **manager** may not order a unit it did not route — the wire-level half
      of "the worker executes all ground work";
    * the **hybrid** may keep a unit, and must say why.
    """

    def __init__(
        self,
        *,
        units: Sequence[UnitBrief],
        arm: aa.Arm,
        task_id: str,
        worker_model: str = "",
        worker_max_steps: int = 0,
        identity: Optional[str] = None,
    ) -> None:
        self.units = tuple(units)
        self.by_id = {unit.unit_id: unit for unit in self.units}
        self.arm = arm
        self.task_id = task_id
        self.worker_model = worker_model
        self.worker_max_steps = worker_max_steps
        self.identity = identity
        self.surface = seat_tools(arm)
        self.calls: list[str] = []
        self.refused: list[str] = []
        self.notes: list[str] = []
        self.batches: list[Batch] = []
        self.orders: dict[str, Order] = {}
        self.intents: dict[str, RoutingIntent] = {}
        if not arm.delegates:
            reason = ARCHITECTURE_REASON.get(arm.id, "")
            for unit in self.units:
                self.intents[unit.unit_id] = RoutingIntent(
                    unit_id=unit.unit_id,
                    routed_to=arm.top_level_role,
                    basis=BASIS_ARCHITECTURE,
                    reason=reason,
                )

    # -- the surface ---------------------------------------------------------

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in self.surface:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available to the seat in arm {self.arm.id}; "
                f"its tools are {list(self.surface)}"
            )
        if name == ROUTE_TOOL:
            return self._route(arguments)
        if name == NOTE_TOOL:
            text = str(arguments.get("text") or "").strip()
            if not text:
                raise ToolError("note requires `text`")
            self.notes.append(text)
            return ToolOutcome(result="noted")
        return self._order(arguments)

    def outstanding(self) -> list[str]:
        """Units still owed an order. A unit with no legal action is not owed one."""
        return [
            unit.unit_id for unit in self.units if unit.options and unit.unit_id not in self.orders
        ]

    def summary(self) -> str:
        parts = [f"{unit_id}:{order.menu_index}" for unit_id, order in sorted(self.orders.items())]
        return f"round answered: {', '.join(parts)}" if parts else "round answered: nothing"

    # -- routing -------------------------------------------------------------

    def _read_targets(self, value: Any) -> list[tuple[str, str]]:
        if isinstance(value, Mapping):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ToolError(
                f"{ROUTE_TOOL} requires `units`: a list of "
                f"{{unit_id, reason}} objects, not {type(value).__name__}"
            )
        if not value:
            raise ToolError(f"{ROUTE_TOOL} requires at least one unit")
        targets: list[tuple[str, str]] = []
        for entry in value:
            if not isinstance(entry, Mapping):
                raise ToolError(
                    f"{ROUTE_TOOL} takes objects with `unit_id` and `reason`, "
                    f"not {type(entry).__name__}"
                )
            unit_id = str(entry.get("unit_id") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            if unit_id not in self.by_id:
                raise ToolError(
                    f"{unit_id!r} is not one of this round's due units: {list(self.by_id)}"
                )
            if not reason:
                raise ToolError(
                    f"{ROUTE_TOOL} requires a `reason` for {unit_id}: say why this "
                    "unit's work goes to the worker"
                )
            if unit_id in self.intents:
                raise ToolError(f"{unit_id} is already routed to {self.intents[unit_id].routed_to}")
            if unit_id in self.orders:
                raise ToolError(f"{unit_id} already has an order; it cannot be routed now")
            targets.append((unit_id, reason))
        seen = [unit_id for unit_id, _ in targets]
        if len(set(seen)) != len(seen):
            raise ToolError(f"{ROUTE_TOOL} was given the same unit twice: {seen}")
        return targets

    def _route(self, arguments: dict[str, Any]) -> ToolOutcome:
        targets = self._read_targets(arguments.get("units"))
        index = len(self.batches)
        batch_task_id = f"{self.task_id}-fanout-{index + 1}"
        subtasks = tuple(unit_subtask(self.by_id[unit_id]) for unit_id, _ in targets)
        # One serially-delegated worker's budget per unit. The loop narrows this
        # to whatever the parent has left and `partition` splits whatever
        # arrives — this seat invents no budget, per FANOUT_ACCOUNTING_RULE.
        grant = self.worker_max_steps * len(targets)
        self.batches.append(
            Batch(
                index=index,
                batch_task_id=batch_task_id,
                unit_ids=tuple(unit_id for unit_id, _ in targets),
                subtasks=subtasks,
                grant=grant,
            )
        )
        basis = BASIS_ORCHESTRATOR if self.arm.keeps_work else BASIS_ARM_MANDATE
        for ordinal, (unit_id, reason) in enumerate(targets, start=1):
            self.intents[unit_id] = RoutingIntent(
                unit_id=unit_id,
                routed_to=ROUTED_WORKER,
                basis=basis,
                reason=reason,
                batch_index=index,
                batch_task_id=batch_task_id,
                # `plan_fanout` mints `<stem>-<ordinal>` off the batch task id,
                # so the seat can name the drive before it exists.
                unit_task_id=f"{batch_task_id}-{ordinal}",
            )
        names = ", ".join(unit_id for unit_id, _ in targets)
        return ToolOutcome(
            result=f"{ROUTE_MARKER} {names} -> the worker",
            spawn=ot.SpawnRequest(
                task=Task(
                    id=batch_task_id,
                    repo_path=ot.NO_REPO,
                    instruction=ot.fanout_instruction(subtasks),
                ),
                executor=None,
                role=ot.ROLE_FANOUT,
                # Every unit is a LEAF: it may consult no one.
                allowance=ot.NO_SPAWNS,
                max_steps=grant,
                system_prompt=ot.frame_subagent(UNIT_SYSTEM, identity=self.identity),
                model=self.worker_model,
                context={"subtasks": subtasks},
            ),
        )

    # -- ordering ------------------------------------------------------------

    def _order(self, arguments: dict[str, Any]) -> ToolOutcome:
        unit_id = str(arguments.get("unit_id") or "").strip()
        if unit_id not in self.by_id:
            raise ToolError(f"{unit_id!r} is not one of this round's due units: {list(self.by_id)}")
        if unit_id in self.orders:
            raise ToolError(f"{unit_id} already has an order; a round answers each unit once")
        unit = self.by_id[unit_id]
        if not unit.options:
            raise ToolError(f"{unit_id} has no legal action; it is parked, not ordered")
        index = _menu_index(arguments, unit.options, unit_id=unit_id)
        kept_because = str(arguments.get("kept_because") or "").strip()
        if unit_id not in self.intents:
            if not self.arm.keeps_work:
                raise ToolError(
                    f"arm {self.arm.id} routes every unit's ground work to the worker; "
                    f"call `{ROUTE_TOOL}` for {unit_id} first"
                )
            if not kept_because:
                raise ToolError(
                    f"you did not hand {unit_id} to the worker, so `kept_because` is "
                    "required: say why you kept this decision"
                )
            self.intents[unit_id] = RoutingIntent(
                unit_id=unit_id,
                routed_to=ROUTED_CORTEX,
                basis=BASIS_ORCHESTRATOR,
                reason=kept_because,
            )
        self.orders[unit_id] = Order(
            unit_id=unit_id,
            menu_index=index,
            why=str(arguments.get("why") or "").strip(),
            kept_because=kept_because,
        )
        still_due = self.outstanding()
        head = f"{ORDER_MARKER} {unit_id} -> menu index {index}"
        if still_due:
            return ToolOutcome(result=f"{head}\nstill due: {', '.join(still_due)}")
        return ToolOutcome(result=head, finished=True, finish_summary=self.summary())


# ── the call log, minted from several threads ────────────────────────────────


class ThreadSafeCallLog(aa.CallLog):
    """t5's log, minted under a lock — a fan-out records from N threads at once.

    ``t5``'s :class:`~examples.arch_arms.CallLog` was written for a serial drive:
    ``index=len(self.records)`` and the append after it are two operations, and
    two units finishing together would take the same index. The lock is held
    only across the mint, never across a model call, so it cannot serialise the
    fan-out it exists to protect — the same discipline ``DelegationLog`` already
    applies, for the same reason.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def mint(self, ctx: aa.CallContext, **kwargs: Any) -> aa.CallRecord:
        with self._lock:
            return super().mint(ctx, **kwargs)


# ── one round ────────────────────────────────────────────────────────────────


@dataclass
class RoundRecord:
    """One drive: every unit due at one instant, and what it cost."""

    arm: str
    rung: str
    match_key: str
    match_id: str
    route: str
    round_index: int
    game_time: int = 0
    due: list[str] = field(default_factory=list)
    routes: list[RouteRecord] = field(default_factory=list)
    exit_reason: str = ""
    model_turns: int = 0
    child_model_turns: int = 0
    batches: list[dict[str, Any]] = field(default_factory=list)
    max_in_flight: int = 0
    notes: list[str] = field(default_factory=list)
    refused_tools: list[str] = field(default_factory=list)
    degradation_codes: list[str] = field(default_factory=list)
    calls: int = 0
    cost: dict[str, Any] = field(default_factory=dict)
    aborted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_ROUND,
            "arm": self.arm,
            "rung": self.rung,
            "match_key": self.match_key,
            "match_id": self.match_id,
            "route": self.route,
            "round_index": self.round_index,
            "game_time": self.game_time,
            "due": list(self.due),
            "routes": [record.to_dict() for record in self.routes],
            "exit_reason": self.exit_reason,
            "model_turns": self.model_turns,
            "child_model_turns": self.child_model_turns,
            "batches": list(self.batches),
            "max_in_flight": self.max_in_flight,
            "notes": list(self.notes),
            "refused_tools": list(self.refused_tools),
            "degradation_codes": list(self.degradation_codes),
            "calls": self.calls,
            "cost": dict(self.cost),
            "aborted": self.aborted,
        }


def _drive(mind: Any, task: Task, **kwargs: Any) -> tuple[Any, bool]:
    """Run the loop; an abort is a measured outcome of that architecture."""
    try:
        return run(mind, task, **kwargs), False
    except LoopAborted as aborted:
        return aborted.outcome, True


def run_round(
    *,
    arm: aa.Arm,
    rung_id: str,
    match_key: str,
    match_id: str,
    team_id: str,
    round_index: int,
    decision_base: int,
    briefings: Sequence[Mapping[str, Any]],
    seams: Any,
    config: aa.ArchConfig,
    senses_hash: str,
    route: str = ROUTE_TEXT,
    identity: Optional[str] = None,
    fanout_timeout: float = ot.DEFAULT_FANOUT_TIMEOUT,
    width_limit: int = ot.MAX_FANOUT_WIDTH,
) -> RoundRecord:
    """Drive one arm over every unit due at one instant, and log every decision."""
    log: aa.CallLog = seams.log
    mark = len(log.records)
    units = tuple(unit_brief(entry, route=route, team_id=team_id) for entry in briefings)
    ctx = aa.CallContext(
        arm=arm.id,
        rung=rung_id,
        problem=match_key,
        route=route,
        senses_hash=senses_hash,
        live=bool(getattr(seams, "live", False)),
    )
    budget = config.budget_for(arm.id)
    record = RoundRecord(
        arm=arm.id,
        rung=rung_id,
        match_key=match_key,
        match_id=match_id,
        route=route,
        round_index=round_index,
        game_time=units[0].game_time if units else 0,
        due=[unit.unit_id for unit in units],
    )
    seat = LeagueSeat(
        units=units,
        arm=arm,
        task_id=f"{arm.id}-{match_id}-r{round_index}",
        worker_model=seams.model_for(aa.ROLE_WORKER) if arm.delegates else "",
        worker_max_steps=budget.worker_max_steps,
        identity=identity,
    )
    delegation_log = ot.DelegationLog()
    plans: list[ot.FanoutPlan] = []

    if not any(unit.options for unit in units):
        # Nothing to decide. Driving a mind at an empty menu would spend turns on
        # a question the arena did not ask.
        record.exit_reason = NO_LEGAL_ACTIONS
        record.routes = _route_records(
            record=record,
            arm=arm,
            rung_id=rung_id,
            match_key=match_key,
            match_id=match_id,
            decision_base=decision_base,
            units=units,
            seat=seat,
            plans=plans,
            delegation_log=delegation_log,
        )
        return record

    view = round_view(
        arm=arm,
        match_id=match_id,
        team_id=team_id,
        round_index=round_index,
        units=units,
        board=(briefings[0].get("board") if briefings else {}) or {},
    )
    task = Task(id=seat.task_id, repo_path=ot.NO_REPO, instruction=round_instruction(view, arm=arm))
    extra: dict[str, Any] = {}
    if arm.delegates:
        worker = seams.build(aa.ROLE_WORKER, ctx, unit_schema())
        extra["subagent"] = ot.build_fanout_seam(
            worker,
            log=delegation_log,
            plans=plans,
            timeout=fanout_timeout,
            width_limit=width_limit,
        )
        extra["spawn_allowance"] = budget.spawn_allowance
    mind = seams.build(arm.top_level_role, ctx, seat_schema(arm))
    outcome, aborted = _drive(
        mind,
        task,
        executor=seat,
        max_steps=budget.max_steps,
        system_prompt=aa.top_level_prompt(system_for(arm), identity=identity),
        model=seams.model_for(arm.top_level_role),
        **extra,
    )

    record.aborted = aborted
    record.exit_reason = outcome.exit_reason
    record.model_turns = outcome.result.stats.model_turns
    record.child_model_turns = outcome.child_model_turns
    record.notes = list(seat.notes)
    record.refused_tools = list(seat.refused)
    record.degradation_codes = [entry.code for entry in ledger.read(loop=outcome)]
    record.max_in_flight = delegation_log.max_in_flight
    record.routes = _route_records(
        record=record,
        arm=arm,
        rung_id=rung_id,
        match_key=match_key,
        match_id=match_id,
        decision_base=decision_base,
        units=units,
        seat=seat,
        plans=plans,
        delegation_log=delegation_log,
    )
    record.batches = _batch_records(seat, plans)
    calls = log.since(mark)
    record.calls = len(calls)
    record.cost = aa.fold_cost(calls)
    return record


def _batch_records(seat: LeagueSeat, plans: Sequence[ot.FanoutPlan]) -> list[dict[str, Any]]:
    """Each batch beside the plan the seam actually settled for it.

    ``committed <= grant`` is checkable straight off this, which is the point:
    the accounting rule stays verifiable from the artifact rather than by
    re-deriving it from the runs.
    """
    out: list[dict[str, Any]] = []
    for batch in seat.batches:
        plan = plans[batch.index] if batch.index < len(plans) else None
        batch.dispatched = plan is not None
        out.append(
            {
                "batch_task_id": batch.batch_task_id,
                "unit_ids": list(batch.unit_ids),
                "grant": batch.grant,
                "requested_width": len(batch.unit_ids),
                "dispatched": batch.dispatched,
                "committed": plan.committed if plan is not None else 0,
                "plan": plan.to_dict() if plan is not None else None,
            }
        )
    return out


def _route_records(
    *,
    record: RoundRecord,
    arm: aa.Arm,
    rung_id: str,
    match_key: str,
    match_id: str,
    decision_base: int,
    units: Sequence[UnitBrief],
    seat: LeagueSeat,
    plans: Sequence[ot.FanoutPlan],
    delegation_log: ot.DelegationLog,
) -> list[RouteRecord]:
    """One record per decision point: destination, authority, reason, outcome."""
    planned: dict[str, ot.FanoutUnit] = {}
    for plan in plans:
        for planned_unit in plan.units:
            planned[planned_unit.unit_task_id] = planned_unit
    runs = {entry.child_task_id: entry for entry in delegation_log.runs}

    out: list[RouteRecord] = []
    for offset, unit in enumerate(units):
        intent = seat.intents.get(unit.unit_id)
        order = seat.orders.get(unit.unit_id)
        entry = RouteRecord(
            arm=arm.id,
            rung=rung_id,
            match_key=match_key,
            match_id=match_id,
            round_index=record.round_index,
            decision_index=decision_base + offset,
            unit_id=unit.unit_id,
            role=unit.role,
            game_time=unit.game_time,
            options=unit.options,
            route=unit.perception.route,
            snapshot_hash=unit.perception.snapshot_hash,
        )
        if intent is not None:
            entry.routed_to = intent.routed_to
            entry.basis = intent.basis
            entry.reason = intent.reason
            entry.batch_task_id = intent.batch_task_id
            entry.unit_task_id = intent.unit_task_id
        if entry.unit_task_id:
            plan_unit = planned.get(entry.unit_task_id)
            if plan_unit is None:
                entry.refusal = BATCH_NOT_DISPATCHED
            else:
                entry.dispatched = plan_unit.dispatched
                entry.refusal = plan_unit.refusal
            run_record = runs.get(entry.unit_task_id)
            if run_record is not None:
                entry.unit_report = run_record.report
                entry.unit_exit_reason = run_record.exit_reason
                proposed = read_proposed_index(run_record.report)
                if proposed is not None and 0 <= proposed < unit.options:
                    entry.proposed_index = proposed
        if order is not None:
            entry.ordered_index = order.menu_index
            entry.order_why = order.why
        if entry.proposed_index is not None and entry.ordered_index is not None:
            entry.overridden = entry.proposed_index != entry.ordered_index
        out.append(entry)
    return out


# ── one match ────────────────────────────────────────────────────────────────


@dataclass
class LeagueMatchRecord:
    """One arm's whole match: the arena's verdict, and every routing decision."""

    arm: str
    rung: str
    route: str
    match_key: str
    match_id: str
    scenario: str
    seed: int
    team_id: str
    opponent: str = ""
    seat_model: str = ""
    unit_model: str = ""
    live: bool = False
    status: str = ""
    winner: Optional[str] = None
    our_points: int = 0
    their_points: int = 0
    margin: int = 0
    our_grade: float = 0.0
    rounds: int = 0
    decisions: int = 0
    routed: int = 0
    kept: int = 0
    unit_drives: int = 0
    no_order: int = 0
    overrides: int = 0
    override_opportunities: int = 0
    routing_mix: list[str] = field(default_factory=list)
    degenerate_routing: bool = True
    model_turns: int = 0
    child_model_turns: int = 0
    batch_count: int = 0
    max_in_flight: int = 0
    batches: list[dict[str, Any]] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)
    degradation_codes: list[str] = field(default_factory=list)
    refused_tools: list[str] = field(default_factory=list)
    calls: int = 0
    cost: dict[str, Any] = field(default_factory=dict)
    aborted: bool = False
    senses_config_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": KIND_MATCH,
            "arm": self.arm,
            "rung": self.rung,
            "route": self.route,
            "match_key": self.match_key,
            "match_id": self.match_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "team_id": self.team_id,
            "opponent": self.opponent,
            "seat_model": self.seat_model,
            "unit_model": self.unit_model,
            "live": self.live,
            "status": self.status,
            "winner": self.winner,
            "our_points": self.our_points,
            "their_points": self.their_points,
            "margin": self.margin,
            "our_grade": self.our_grade,
            "rounds": self.rounds,
            "decisions": self.decisions,
            "routed": self.routed,
            "kept": self.kept,
            "unit_drives": self.unit_drives,
            "no_order": self.no_order,
            "overrides": self.overrides,
            "override_opportunities": self.override_opportunities,
            "routing_mix": list(self.routing_mix),
            "degenerate_routing": self.degenerate_routing,
            "model_turns": self.model_turns,
            "child_model_turns": self.child_model_turns,
            "batch_count": self.batch_count,
            "max_in_flight": self.max_in_flight,
            "batches": list(self.batches),
            "degradation_codes": list(self.degradation_codes),
            "refused_tools": list(self.refused_tools),
            "calls": self.calls,
            "cost": dict(self.cost),
            "aborted": self.aborted,
            "senses_config_hash": self.senses_config_hash,
        }


def append_jsonl(path: Optional[Path], record: Mapping[str, Any]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


#: What a scripted attempt records instead of a score anyone could cite.
SCRIPTED_NOTE = (
    "SCRIPTED WIRING RUN — the minds answered by rule and no judgement was made; "
    "nothing here is evidence about play"
)

#: The caveat a measured league verdict has to carry, from the prior series.
MEASURED_NOTE = (
    "league's own outcome metric tied 0-0 in every match of the prior head-to-head "
    "(docs/live-test-results/league-h2h.md); a win count is the arena's verdict, not "
    "a strong one"
)

#: Claim ``c38``, quotable as-is. Recorded on every artifact this lane writes so
#: a reader never has to go looking for why the roles sit where they sit.
ROLE_MAPPING = (
    "The cortex is the leader/player seat: it commands, decides, and keeps final "
    "authority. The worker drives the units — all of them — as the explorer/actor "
    "that moves, takes locations and gathers. The worker's ~14-way concurrency "
    "makes driving every unit simultaneously the natural shape rather than a "
    "stretch goal."
)


def play_match(
    *,
    cli: lc.LeagueCli,
    arm: aa.Arm,
    rung: LeagueRung,
    match_index: int,
    seed: int,
    seams: Any,
    config: aa.ArchConfig,
    senses_hash: str,
    route: str = ROUTE_TEXT,
    identity: Optional[str] = None,
    out: Optional[Path] = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    fanout_timeout: float = ot.DEFAULT_FANOUT_TIMEOUT,
    width_limit: int = ot.MAX_FANOUT_WIDTH,
) -> tuple[LeagueMatchRecord, aa.AttemptRecord]:
    """Play one whole match, one decision round at a time.

    The arena is reached only by this function, never by a tool: a model's
    ``order`` records a choice and the harness submits it. That is what keeps
    "nothing but the seat reaches the match" true at every depth, including
    inside a worker running on its own thread.
    """
    log: aa.CallLog = seams.log
    mark = len(log.records)
    ours, theirs = lc.team_ids(arm.id)
    unit_role = aa.ROLE_WORKER if arm.delegates else arm.top_level_role
    unit_model = roster_model(seams.model_for(unit_role))
    register_teams(cli, arm=arm, rung=rung, unit_model=unit_model)

    match_key = f"{arm.id}-{match_index}"
    match_id = f"{arm.id.lower()}{match_index}"
    try:
        cli("cmatch", "show", match_id, "--json")
    except lc.LeagueError:
        cli(
            "cmatch",
            "new",
            "--scenario",
            rung.scenario,
            "--team",
            ours,
            "--team",
            theirs,
            "--driver",
            f"{ours}:{OUR_DRIVER}",
            "--driver",
            f"{theirs}:{OPPONENT_DRIVER}",
            "--seed",
            str(seed),
            "--id",
            match_id,
            "--apply",
            "--json",
        )

    live = bool(getattr(seams, "live", False))
    record = LeagueMatchRecord(
        arm=arm.id,
        rung=rung.id,
        route=route,
        match_key=match_key,
        match_id=match_id,
        scenario=rung.scenario,
        seed=seed,
        team_id=ours,
        opponent=theirs,
        seat_model=seams.model_for(arm.top_level_role),
        unit_model=unit_model,
        live=live,
        senses_config_hash=senses_hash,
    )

    decision_index = 0
    for _ in range(max_rounds):
        show = cli("cmatch", "show", match_id, "--json")
        if show.get("status") != "active":
            break
        decisions = [
            entry
            for entry in (show.get("decisions") or [])
            if str(entry.get("unit_id") or "").startswith(ours)
        ]
        if not decisions:
            cli("cmatch", "tick", match_id, "--apply", "--json")
            continue
        briefings = [dict(entry.get("briefing") or {}) for entry in decisions]
        round_record = run_round(
            arm=arm,
            rung_id=rung.id,
            match_key=match_key,
            match_id=match_id,
            team_id=ours,
            round_index=record.rounds,
            decision_base=decision_index,
            briefings=briefings,
            seams=seams,
            config=config,
            senses_hash=senses_hash,
            route=route,
            identity=identity,
            fanout_timeout=fanout_timeout,
            width_limit=width_limit,
        )
        _fold_round(record, round_record)
        for entry in round_record.routes:
            append_jsonl(out, entry.to_dict())
        append_jsonl(out, round_record.to_dict())

        picked_by_unit = {entry.unit_id: entry.ordered_index for entry in round_record.routes}
        for index, entry in enumerate(decisions):
            unit_id = str(entry.get("unit_id") or "")
            menu = list(briefings[index].get("menu") or [])
            picked = picked_by_unit.get(unit_id)
            action = menu[picked] if picked is not None and picked < len(menu) else None
            cli(
                "cmatch",
                "act",
                match_id,
                "--unit",
                unit_id,
                "--action-json",
                json.dumps(action),
                "--apply",
                "--json",
            )
        decision_index += len(decisions)
        record.rounds += 1

    score = cli("match", "score", match_id, "--json")
    _fold_score(record, score, ours, theirs)
    calls = log.since(mark)
    record.calls = len(calls)
    record.cost = aa.fold_cost(calls)

    attempt = aa.AttemptRecord(
        arm=arm.id,
        rung=rung.id,
        problem=match_key,
        route=route,
        top_level_role=arm.top_level_role,
        exit_reason=record.status,
        model_turns=record.model_turns,
        child_model_turns=record.child_model_turns,
        delegations=record.batch_count,
        routed_to=(ROUTED_WORKER if record.routed else arm.top_level_role),
        routing=list(record.routes),
        raw_summary=(
            f"{record.our_points}-{record.their_points} "
            f"({record.winner or 'no winner'}) in {record.rounds} round(s)"
        ),
        graded={
            "winner": record.winner,
            "our_points": record.our_points,
            "their_points": record.their_points,
            "margin": record.margin,
            "our_grade": record.our_grade,
            "is_correct": record.winner == ours,
            "scripted": not live,
            "note": SCRIPTED_NOTE if not live else MEASURED_NOTE,
        },
        is_correct=record.winner == ours,
        degradation_codes=list(record.degradation_codes),
        refused_tools=list(record.refused_tools),
        calls=record.calls,
        cost=dict(record.cost),
        aborted=record.aborted,
    )
    append_jsonl(out, attempt.to_dict())
    append_jsonl(out, record.to_dict())
    return record, attempt


def _fold_round(record: LeagueMatchRecord, round_record: RoundRecord) -> None:
    record.decisions += len(round_record.routes)
    record.model_turns += round_record.model_turns
    record.child_model_turns += round_record.child_model_turns
    record.batch_count += len(round_record.batches)
    record.batches.extend(round_record.batches)
    record.max_in_flight = max(record.max_in_flight, round_record.max_in_flight)
    record.aborted = record.aborted or round_record.aborted
    record.degradation_codes.extend(round_record.degradation_codes)
    record.refused_tools.extend(round_record.refused_tools)
    for entry in round_record.routes:
        record.routes.append(entry.to_dict())
        # `routed` and `kept` count DELEGATION decisions, so a flat arm's are
        # zero by construction: its `routed_to` names the one mind it has, and
        # counting that as "routed to the worker" would make arm W look like it
        # delegated nine times when it never spawned anything at all.
        if entry.basis != BASIS_ARCHITECTURE:
            if entry.routed_to == ROUTED_WORKER:
                record.routed += 1
            elif entry.routed_to == ROUTED_CORTEX:
                record.kept += 1
        if entry.dispatched:
            record.unit_drives += 1
        if entry.ordered_index is None:
            record.no_order += 1
        if entry.overridden is not None:
            record.override_opportunities += 1
            record.overrides += 1 if entry.overridden else 0


def _fold_score(
    record: LeagueMatchRecord, score: Mapping[str, Any], ours: str, theirs: str
) -> None:
    outcome = score.get("outcome") or {}
    record.status = str(score.get("status") or "")
    record.winner = score.get("winner")
    record.our_points = int(outcome.get(ours) or 0)
    record.their_points = int(outcome.get(theirs) or 0)
    record.margin = record.our_points - record.their_points
    units = ((score.get("units") or {}).get("units")) or {}
    record.our_grade = float(
        sum(
            float(entry.get("grade") or 0.0)
            for name, entry in units.items()
            if str(name).startswith(ours)
        )
    )
    routed_to = [str(entry.get("routed_to") or "") for entry in record.routes]
    record.routing_mix = sorted({value for value in routed_to if value})
    record.degenerate_routing = degenerate(routed_to)


# ── the series ───────────────────────────────────────────────────────────────


def preamble(
    config: aa.ArchConfig,
    *,
    senses_hash: str,
    live: bool,
    rung: LeagueRung,
    arms: Sequence[str],
    route: str,
    arena: str,
) -> dict[str, Any]:
    """The configuration that produced a run, written before its first result."""
    payload = config.to_dict()
    payload.update(
        {
            "kind": aa.KIND_PREAMBLE,
            "series": "orchestrator-worker-architectures",
            "task": "t6",
            "lane": "league",
            "config_path": str(config.path) if config.path else None,
            "senses_config_hash": senses_hash,
            "live": live,
            "arena": arena,
            "arms": {arm: aa.ARMS[arm].to_dict() for arm in arms},
            "flat_arms": list(aa.FLAT_ARMS),
            "rung": rung.to_dict(),
            "route": route,
            "routes": {name: entry.why for name, entry in sorted(ROUTE_REGISTRY.items())},
            "role_mapping": ROLE_MAPPING,
            "seat_tools": {arm: list(seat_tools(aa.ARMS[arm])) for arm in arms},
            "unit_tools": list(UNIT_TOOLS),
            "final_authority_tools": list(LEAGUE_FINAL_AUTHORITY_TOOLS),
            "fanout": {"max_width": ot.MAX_FANOUT_WIDTH, "rule": FANOUT_ACCOUNTING_RULE},
            "bases": list(BASES),
            "outcome_caveat": MEASURED_NOTE,
        }
    )
    return payload


def scripted_seams(arm: aa.Arm, *, config: aa.ArchConfig, log: aa.CallLog) -> aa.ScriptedSeams:
    """One scripted mind per role this arm actually dials."""
    minds: dict[str, Callable[[list[dict[str, Any]]], ModelResponse]] = {
        arm.top_level_role: scripted_commander(arm)
    }
    if arm.delegates:
        minds[aa.ROLE_WORKER] = scripted_unit
    return aa.ScriptedSeams(minds, config=config, log=log)


def build_live_seams(
    config: aa.ArchConfig,
    *,
    log: aa.CallLog,
    roles: Sequence[str],
    env: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[aa.LiveSeams], list[aa.DialResolution]]:
    """Resolve every role this arm needs, or report ABSENT — never substitute.

    Resolution is t5's, which is t2's for the worker. An unresolvable role
    returns ``None`` plus the recorded degradations, so the caller reports the
    arm ABSENT rather than pointing it at whatever else happens to be served.
    """
    resolutions = [aa.resolve_dial(config, role, env=env) for role in roles]
    if any(not resolution.ok for resolution in resolutions):
        return None, resolutions
    dials = {
        resolution.role: resolution.dial
        for resolution in resolutions
        if resolution.dial is not None
    }
    return aa.LiveSeams(config=config, log=log, dials=dials), resolutions


def run_series(
    *,
    config: aa.ArchConfig,
    arena: str,
    root: Any,
    log: aa.CallLog,
    rung: Optional[LeagueRung] = None,
    arms: Sequence[str] = aa.ARM_ORDER,
    matches: int = 1,
    out: Optional[Path] = None,
    route: str = ROUTE_TEXT,
    identity: Optional[str] = None,
    seams_factory: Optional[Callable[[aa.Arm, aa.CallLog], Any]] = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> dict[str, Any]:
    """Every arm over one rung, recorded as it goes.

    Refuses before the first call if the arms disagree about senses — t5's rule,
    composed, because a lane whose control variable drifted is not worth playing.
    """
    chosen = rung or LEAGUE_LADDER[0]
    senses_hash = aa.assert_senses_identical(config)
    workdir = Path(root)
    workdir.mkdir(parents=True, exist_ok=True)
    cli = lc.LeagueCli(root=workdir, binary=arena)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        log.subscribe(lambda record: append_jsonl(out, record.to_dict()))

    cells: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    live = False
    first = True
    for arm_id in arms:
        arm = aa.ARMS[arm_id]
        seams = (
            seams_factory(arm, log)
            if seams_factory is not None
            else scripted_seams(arm, config=config, log=log)
        )
        if first:
            live = bool(getattr(seams, "live", False))
            append_jsonl(
                out,
                preamble(
                    config,
                    senses_hash=senses_hash,
                    live=live,
                    rung=chosen,
                    arms=arms,
                    route=route,
                    arena=arena,
                ),
            )
            first = False
        cell = aa.CellResult(
            arm=arm_id, rung=chosen.id, route=route, senses_config_hash=senses_hash
        )
        for index in range(max(1, matches)):
            seed = chosen.seeds[index % len(chosen.seeds)]
            match_record, attempt = play_match(
                cli=cli,
                arm=arm,
                rung=chosen,
                match_index=index,
                seed=seed,
                seams=seams,
                config=config,
                senses_hash=senses_hash,
                route=route,
                identity=identity,
                out=out,
                max_rounds=max_rounds,
            )
            cell.attempts.append(attempt)
            matched.append(match_record.to_dict())
        payload = cell.to_dict()
        cells.append(payload)
        append_jsonl(out, payload)

    return {
        "kind": "series",
        "lane": "league",
        "live": live,
        "rung": chosen.id,
        "route": route,
        "arms": list(arms),
        "senses_config_hash": senses_hash,
        "cells": cells,
        "matches": matched,
    }


def analyse(source: Any, *, config: Optional[aa.ArchConfig] = None) -> dict[str, Any]:
    """Re-apply t5's decision rule to a league artifact.

    The ladder is passed empty on purpose. t5's heterogeneity test asks whether a
    *rung's problem set* mixes difficulties; on this lane the contrast the hybrid
    needs is not a property of the problem set at all — it is the routing mix
    that actually happened, which each match reports as ``routing_mix`` and
    ``degenerate_routing``. Whether a degenerate hybrid cell is graded is task
    t11's rule to pin. What survives here unchanged is the part that matters
    most: **no verdict without both flat controls.**
    """
    resolved = config if config is not None else aa.load_config()
    return aa.analyse(source, config=resolved, ladder=())


# ── scripted minds (no network, no live model) ───────────────────────────────

#: The roles the scripted hybrid routes. Everything else it keeps — which is
#: what makes a hermetic run exercise BOTH halves of the routing decision.
SCRIPTED_ROUTABLE_ROLES: tuple[str, ...] = ("harvester", "scout")

SCRIPTED_ROUTE_REASON = "SCRIPTED: routine unit work goes to the worker"
SCRIPTED_KEEP_REASON = "SCRIPTED: a contested post is the commander's call"
SCRIPTED_ORDER_WHY = "SCRIPTED-NO-JUDGEMENT"

#: What a scripted worker hands back. Index 0 always, so it is deliberately not
#: the best entry on the fake arena's menu: a scripted run that scored well
#: would be mistaken for data.
SCRIPTED_PROPOSAL = "menu_index=0: SCRIPTED-NO-JUDGEMENT"


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        reasoning="scripted",
        tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=dict(arguments))],
    )


def _marked(transcript: str, marker: str, unit_ids: Sequence[str]) -> set[str]:
    """Which of these units appear on a line carrying this marker.

    Per line, not per transcript: a result that also listed the units still due
    would otherwise mark them all done. That is why the seat puts one id on the
    marker line and everything else below it.
    """
    lines = [line for line in transcript.splitlines() if marker in line]
    return {unit_id for unit_id in unit_ids for line in lines if unit_id and unit_id in line}


def scripted_commander(arm: aa.Arm) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A seat that answers by rule, reading its own round state off the transcript.

    Deliberately bad at the game: it orders the LAST menu entry where the worker
    proposes the first, so the override path is exercised rather than agreed
    into by construction — and neither choice is the fake arena's best one.
    """

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        transcript = "\n".join(str(message.get("content") or "") for message in messages)
        view = read_round(transcript)
        units = [dict(entry) for entry in (view.get("units") or [])]
        unit_ids = [str(entry.get("unit_id") or "") for entry in units]
        routed = _marked(transcript, ROUTE_MARKER, unit_ids)
        ordered = _marked(transcript, ORDER_MARKER, unit_ids)

        if arm.delegates and not routed:
            targets = [
                entry
                for entry in units
                if not arm.keeps_work or str(entry.get("role") or "") in SCRIPTED_ROUTABLE_ROLES
            ]
            if targets:
                return _call(
                    ROUTE_TOOL,
                    units=[
                        {"unit_id": str(entry.get("unit_id")), "reason": SCRIPTED_ROUTE_REASON}
                        for entry in targets
                    ],
                )

        pending = [entry for entry in units if str(entry.get("unit_id") or "") not in ordered]
        if not pending:
            return ModelResponse(content="the round is answered", reasoning="scripted")
        unit = pending[0]
        unit_id = str(unit.get("unit_id") or "")
        options = max(1, int(unit.get("options") or 1))
        arguments: dict[str, Any] = {
            "unit_id": unit_id,
            "menu_index": options - 1,
            "why": SCRIPTED_ORDER_WHY,
        }
        if arm.delegates and arm.keeps_work and unit_id not in routed:
            arguments["kept_because"] = SCRIPTED_KEEP_REASON
        return _call(ORDER_TOOL, **arguments)

    return complete


def scripted_unit(_messages: list[dict[str, Any]]) -> ModelResponse:
    """A worker that reports and reaches for nothing else."""
    return _call(UNIT_TOOLS[0], findings=SCRIPTED_PROPOSAL)


# ── CLI ──────────────────────────────────────────────────────────────────────


def render_plan() -> str:
    lines = ["arch_league — four architectures over league's continuous decision points", ""]
    lines += ["role mapping (c38):", f"  {ROLE_MAPPING}", "", "arms:"]
    for arm_id in aa.ARM_ORDER:
        arm = aa.ARMS[arm_id]
        mark = "  (control)" if arm_id in aa.FLAT_ARMS else ""
        lines.append(f"  {arm.id}  {arm.label:<12} {arm.shape:<13} seat={arm.top_level_role}{mark}")
        lines.append(f"      tools: {', '.join(seat_tools(arm))}")
    lines += [
        "",
        "controls: " + ", ".join(aa.FLAT_ARMS) + " — analyse refuses a verdict without both",
        "",
        "rung:",
    ]
    for rung in LEAGUE_LADDER:
        lines.append(f"  {rung.id}  {rung.scenario}  roles={', '.join(rung.roles)}")
        lines.append(f"      {rung.why}")
    lines += [
        "",
        f"unit surface: {', '.join(UNIT_TOOLS)} — it never reaches the arena",
        f"final authority: {', '.join(LEAGUE_FINAL_AUTHORITY_TOOLS)}",
        f"fan-out width bound: {ot.MAX_FANOUT_WIDTH} (t4)",
        "",
        "routing bases:",
        f"  {BASIS_ARCHITECTURE}: the arm's shape decided; no mind judged anything",
        f"  {BASIS_ARM_MANDATE}: the manager may not order a unit it did not route",
        f"  {BASIS_ORCHESTRATOR}: the hybrid chose, and said why",
        "",
        f"gates: {LIVE_GATE_ENV} (models), {LIVE_ARENA_ENV} (the real arena)",
    ]
    return "\n".join(lines)


def render_routes(table: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        f"{'arm':<4} {'match':<8} {'rnd':>3} {'unit':<12} {'to':<7} {'basis':<13} "
        f"{'prop':>4} {'ord':>4}  reason"
    ]
    for row in table:
        proposed = row.get("proposed_index")
        ordered = row.get("ordered_index")
        lines.append(
            f"{str(row.get('arm', '')):<4} {str(row.get('match_id', '')):<8} "
            f"{int(row.get('round_index', 0)):>3} {str(row.get('unit_id', '')):<12} "
            f"{str(row.get('routed_to', '')):<7} {str(row.get('basis', '')):<13} "
            f"{('-' if proposed is None else proposed):>4} "
            f"{('-' if ordered is None else ordered):>4}  {row.get('reason', '')}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--config", default=None, help="path to the sampling table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="the arms, the rung and the routing rules")
    plan.add_argument("--json", action="store_true")

    config_cmd = subparsers.add_parser("config", help="the configuration as it was read")
    config_cmd.add_argument("--json", action="store_true")
    config_cmd.add_argument("--config", default=None)

    play = subparsers.add_parser("play", help="play the rung (scripted unless --live)")
    play.add_argument("--root", default=None, help="where the arena keeps its store")
    play.add_argument("--league", default=None, help="the arena binary or a stand-in")
    play.add_argument("--arm", action="append", default=None, help="restrict to these arms")
    play.add_argument("--matches", type=int, default=1)
    play.add_argument("--rung", default=None)
    play.add_argument("--out", default=None, help="write the JSONL artifact here")
    play.add_argument("--live", action="store_true", help=f"dial the rig (needs {LIVE_GATE_ENV})")
    play.add_argument("--config", default=None)

    routes = subparsers.add_parser("routes", help="read the routing log back")
    routes.add_argument("--log", required=True)
    routes.add_argument("--json", action="store_true")

    analyser = subparsers.add_parser("analyse", help="re-apply t5's rule to an artifact")
    analyser.add_argument("--log", required=True)
    analyser.add_argument("--config", default=None)
    return parser


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 2


def _plan_payload() -> dict[str, Any]:
    return {
        "lane": "league",
        "role_mapping": ROLE_MAPPING,
        "arms": {arm: aa.ARMS[arm].to_dict() for arm in aa.ARM_ORDER},
        "flat_arms": list(aa.FLAT_ARMS),
        "rungs": [rung.to_dict() for rung in LEAGUE_LADDER],
        "seat_tools": {arm: list(seat_tools(aa.ARMS[arm])) for arm in aa.ARM_ORDER},
        "unit_tools": list(UNIT_TOOLS),
        "final_authority_tools": list(LEAGUE_FINAL_AUTHORITY_TOOLS),
        "bases": list(BASES),
        "routes": {name: entry.why for name, entry in sorted(ROUTE_REGISTRY.items())},
        "fanout": {"max_width": ot.MAX_FANOUT_WIDTH},
        "gates": {"models": LIVE_GATE_ENV, "arena": LIVE_ARENA_ENV},
    }


def _play(args: argparse.Namespace, config: aa.ArchConfig) -> int:
    if args.live:
        try:
            aa.require_live_rig()
        except aa.LiveRigClosed as shut:
            return _fail(
                str(shut),
                f"export {LIVE_GATE_ENV}=1 to dial the rig, or drop --live for the "
                "scripted lane (live runs are frozen for this cycle)",
            )
        try:
            require_live_arena()
        except LiveArenaClosed as shut:
            return _fail(str(shut), f"export {LIVE_ARENA_ENV}=1 to reach the real arena")
        return _fail(
            "the live lane is not dialled by this task",
            "task t12 runs the series; t6 ships the lane and its scripted half",
        )

    if args.rung and args.rung not in LADDER_BY_ID:
        return _fail(f"unknown rung {args.rung!r}", f"known rungs: {', '.join(LADDER_BY_ID)}")
    rung = LADDER_BY_ID[args.rung] if args.rung else LEAGUE_LADDER[0]

    arms = tuple(args.arm) if args.arm else aa.ARM_ORDER
    unknown = [arm for arm in arms if arm not in aa.ARMS]
    if unknown:
        return _fail(f"unknown arm(s) {unknown}", f"known arms: {', '.join(aa.ARM_ORDER)}")

    try:
        arena = resolve_arena(args.league)
    except LiveArenaClosed as shut:
        return _fail(
            str(shut),
            f"export {LIVE_ARENA_ENV}=1 to play the real arena, or pass --league "
            "with a stand-in binary",
        )

    root = Path(args.root) if args.root else Path(tempfile.mkdtemp(prefix="arch-league-"))
    try:
        report = run_series(
            config=config,
            arena=arena,
            root=root,
            log=ThreadSafeCallLog(),
            rung=rung,
            arms=arms,
            matches=max(1, int(args.matches)),
            out=Path(args.out) if args.out else None,
        )
    except (aa.ConfigError, lc.LeagueError) as broken:
        return _fail(str(broken), "check the sampling table and the arena binary, then re-run")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config) if getattr(args, "config", None) else None

    if args.command == "plan":
        print(json.dumps(_plan_payload(), indent=2) if args.json else render_plan())
        return 0

    if args.command == "routes":
        try:
            table = routing_table(aa.read_log(Path(args.log)))
        except OSError as unreadable:
            return _fail(str(unreadable), "point --log at a JSONL artifact this harness wrote")
        if args.json:
            print(json.dumps(table, indent=2, ensure_ascii=False))
        else:
            print(render_routes(table))
        return 0

    try:
        config = aa.load_config(config_path)
    except aa.ConfigError as broken:
        return _fail(
            str(broken), f"check the sampling table at {config_path or aa.DEFAULT_CONFIG_PATH}"
        )

    if args.command == "config":
        payload = config.to_dict()
        try:
            payload["senses_config_hash"] = aa.assert_senses_identical(config)
        except aa.ConfigError as drifted:
            return _fail(str(drifted), "make every arm's senses cell identical, then re-run")
        payload["lane"] = "league"
        payload["rungs"] = [rung.to_dict() for rung in LEAGUE_LADDER]
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(render_plan())
            print()
            print(f"senses_config_hash: {payload['senses_config_hash']}")
        return 0

    if args.command == "analyse":
        try:
            print(json.dumps(analyse(Path(args.log), config=config), indent=2, ensure_ascii=False))
        except OSError as unreadable:
            return _fail(str(unreadable), "point --log at a JSONL artifact this harness wrote")
        return 0

    return _play(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
