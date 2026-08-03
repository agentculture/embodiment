#!/usr/bin/env python3
"""league_seat — an embodiment-driven team plays league-of-agents.

Everything embodiment ships so far was validated by tests it wrote for itself.
This is the first *adversarial* consumer: a real game, with an opponent, where
continuity either holds up across turns or visibly does not::

    python examples/league_seat.py play --arm resident --store /tmp/seat/memory
    python examples/league_seat.py play --arm command  --store /tmp/seat/memory

**Software presence, not a robot body.** The seat commands units on a grid in
someone else's simulation. It drives no hardware and it is not a robot player;
``embodiment`` is named for the loop and the presence an app gains, and in this
mesh ``reachy-mini-cli`` owns the physical robot. C2, stated rather than left
to inference.

The public surface, and only the public surface
-----------------------------------------------
The arena is reached **through league's own CLI, in a subprocess** — never by
importing ``league``. :class:`LeagueCli` is the whole seam, and it uses exactly
the verbs a reader can type::

    league team register <id> --agent <id>:<model>:<role> --apply --json
    league match new --scenario S --team A --team B --seed N --id ID \\
        --driver A:resident|stateless --driver B:bot --apply --json
    league match show ID --json          # the board, legal actions, rejections
    league match act ID --team T --orders-json '{...}' --apply --json
    league match score ID --json
    league match replay ID --json        # hashed, so a comparison is backed

``--driver <team>:<kind>`` is league's own residency vocabulary
(``bot|stateless|resident``), recorded in the match log header it writes. This
host records the same fact three more times — the arm name, the per-turn pid,
and the muse counters — because *a result whose residency you cannot tell from
the artifact is not usable evidence*.

The two residency arms are the experiment
-----------------------------------------
Both arms give the mind the **same tool surface** (``note`` / ``order`` /
``submit``) and the same board. They differ in exactly one variable:

``--arm resident``
    ONE :func:`embodiment.run` drive spans the whole match. Each league turn is
    a ``submit`` tool call that advances the arena and hands the next board back
    as the tool result. One muse thread is built before turn 1 and closed after
    the last turn, so **the muse lives across turns**. league is told
    ``--driver <me>:resident``.

``--arm command``
    A **fresh subprocess per league turn** (``league_seat.py turn``), one drive
    each. Nothing survives in memory, so *all* per-turn continuity has to ride
    the scratchpad file and the eidetic store. league is told
    ``--driver <me>:stateless`` — its own word for this.

The command arm is the stress test. If continuity is real it plays coherently;
if continuity is a story we tell, it plays like an amnesiac. The proof is built
into the demo: the operator's **directive** ("take cp-west") is passed on turn 0
and never again. On every later turn the mind can only find it by reading the
pad or recalling it from the store — and with both empty it holds position and
says so, which is the control experiment.

What an app author is meant to copy
-----------------------------------
1. **A tool surface** (:class:`Seat`) — three domain tools. embodiment never
   builds an executor; what the mind may do is the host's to decide, and here
   that includes whether ``submit`` ends the drive or advances the match.
2. **A model seam** — :func:`make_scripted_cortex` (hermetic, the default) or
   :func:`gateway_seam` against a real OpenAI-compatible endpoint (``--live``).
3. **A continuity seam** — ``build_continuity_fn(LifecycleConfig(...))`` plus a
   :class:`embodiment.Scratchpad` persisted to disk. ``--store`` is **required**
   and has no default on purpose: an unanchored public record resolves against
   whatever git repo the host process happens to sit in, and would quietly
   commit a match's memories into it.
4. **A presence surface** and, optionally, a second advisory mind (``--muse``,
   configurable on/off so a later measurement can compare rather than assume).
5. **An optional observer** — ``--events`` wires an
   :class:`embodiment.EventEmitter`. Absent, the seat runs identically; broken,
   it records one degradation and never raises.

The turn subcommand's wire is league's own
------------------------------------------
``league_seat.py turn`` reads a board on **stdin** and writes one orders JSON
object to **stdout** — the shape ``league match act --orders-json`` accepts.
:func:`read_observation` accepts the ``match show --json`` payload, a bare
``state`` dict, *or* league's own ``command``-driver prompt
(``league/harness.py:409-423``). So the same entry point works whether this host
drives league or league drives it, and both directions are exercised:
``TestLiveArena`` runs ``league harness run --config`` with this file as the
``command`` driver.

That second direction earned its test. The first version of
:func:`read_observation` took the *first* JSON object in the prompt — but
league's template prints ``Scenario: {…}`` before the board, so the seat was
handed a scenario, read it as a finished match, and submitted nothing. Six turns
resolved with zero orders, a cooperation score of 0, and no error anywhere. A
host that only ever drives itself cannot find that.

This is a game, not a benchmark. The opponent is :func:`rival_orders`, a
deterministic house policy — not a mind — and no claim about winning is made or
implied anywhere in this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil

# The arena is a separate program reached through its CLI. That is the whole
# point: fixed argv, no shell, and no `import league` anywhere under examples/.
import subprocess  # nosec B404
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    Boundary,
    LifecycleConfig,
    LoopAborted,
    LoopControls,
    ModelResponse,
    PresenceEngine,
    PresenceIO,
    Scratchpad,
    Task,
    ToolCall,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    build_continuity_fn,
    continuity,
    frame_cortex,
    frame_muse,
    ledger,
    resume_report,
    run,
    scratchpad,
    speaker_label,
)
from embodiment.muse import MuseControls  # noqa: E402
from embodiment.muse_runner import ThreadedMuseRunner  # noqa: E402
from examples.challenge_config import write_config_preamble  # noqa: E402

# ── who this host is, and where it keeps things ──────────────────────────────

#: eidetic scope for everything a match remembers. Its own, never the repo
#: agent's — a demo must not write into the mesh's shared memory.
SCOPE = "league-seat-demo"

#: eidetic's ``type`` vocabulary is free-form; a turn is a match record.
RECORD_TYPE = "match-turn"

#: Stamped on every durable record as provenance.
ADDED_BY = "league-seat-demo"

#: ``hybrid`` (eidetic's own default) dials an embedding endpoint. This host is
#: hermetic by default, so it ranks lexically and says so.
RECALL_MODE = "keyword"

#: How many prior records are offered to the mind as context.
RECALL_TOP_K = 4

#: The model ids recorded on a hermetic run. They name no real model, because
#: no real model ran.
SCRIPTED_CORTEX = "scripted-league-mind"
SCRIPTED_MUSE = "scripted-league-muse"

# ── the live rig (opt-in, never a default) ───────────────────────────────────

#: Read from the environment on ``--live``. Never hardcoded, never printed.
API_KEY_ENV = "COLLEAGUE_API_KEY"

#: An OpenAI-compatible gateway. The reference rig is a ``lobes`` gateway.
DEFAULT_BASE_URL = "http://localhost:8001/v1"

#: The reference rig's roles, addressed BY NAME through the gateway.
CORTEX_MODEL = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"
MUSE_MODEL = "nvidia/Gemma-4-31B-IT-NVFP4"

#: Raised 2048 -> 16000 (``d16``), on this harness's own measurement: task
#: t24 played 12 matches at both budgets and found 5 of 83 completions
#: truncating at 2048 against 0 of 58 at 16000, with zero degradations
#: recorded either way (#37). One of those truncations cost blue an entire
#: opening league turn that ``turns_played`` scored as played. The published
#: ``arena-budget.md`` and ``arena-series.md`` runs were measured at 2048;
#: pass ``--max-tokens 2048`` to reproduce them.
DEFAULT_MAX_TOKENS = 16000
DEFAULT_MUSE_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MUSE_TEMPERATURE = 0.7

# ── the arena ────────────────────────────────────────────────────────────────

ARM_RESIDENT = "resident"
ARM_COMMAND = "command"
ARMS = (ARM_RESIDENT, ARM_COMMAND)

#: This host's arm → league's own declared residency (``match.py:_DRIVER_KINDS``
#: is ``("bot", "stateless", "resident")``). ``stateless`` is league's word for
#: a driver re-spawned every turn, which is exactly the command arm.
LEAGUE_RESIDENCY = {ARM_RESIDENT: "resident", ARM_COMMAND: "stateless"}

DEFAULT_LEAGUE_BIN = "league"
DEFAULT_SCENARIO = "skirmish-1"
DEFAULT_SEED = 7
DEFAULT_TEAM = "blue"
DEFAULT_RIVAL = "red"

#: The operator's standing objective. Handed to the mind on turn 0 and NEVER
#: again — every later turn must recover it from the pad or the store, which is
#: what makes the command arm a continuity proof rather than a re-enactment.
DEFAULT_DIRECTIVE = "Operator directive: take cp-west and hold it; ignore cp-east."

#: The roles a skirmish roster expects, in order.
ROSTER_ROLES = ("scout", "harvester", "defender")

#: What the house opponent's roster is labelled with. It is a scripted policy,
#: not a model, and the roster says so.
RIVAL_MODEL = "bot:house"

#: Where the observation JSON starts inside the prompt. The mind finds the
#: *last* one, so a resident drive always acts on the newest board.
OBSERVATION_MARKER = "OBSERVATION (JSON):"

#: Tool-result markers the hermetic mind counts. Bracketed on purpose: the
#: transcript also carries recalled prose, and a bare "ordered" in a remembered
#: summary would silently inflate the count.
MARK_NOTED = "[noted]"
MARK_ORDERED = "[ordered]"

BASE_SYSTEM = (
    "You command a team in a turn-based grid match. Each turn you are given the "
    "board as JSON: your living units, the legal moves for each one, and the "
    "control points. Note what you intend before you act, order every one of "
    "your living units, then submit the turn with a standing plan and a "
    "summary. Work only from the board in front of you and from what you "
    "remember of earlier turns; never invent a unit id or a control point. "
    "Your summary is the only thing a later turn remembers, so restate the "
    "standing objective in it, every time."
)

TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "note",
            "description": (
                "Write one line into your scratchpad. Record an intent BEFORE you act "
                "on it — if this turn is interrupted, that line is what tells your "
                "successor where you were."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "intend | observe | conclude"},
                    "text": {"type": "string", "description": "One sentence."},
                },
                "required": ["kind", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "order",
            "description": "Order one of your living units to act this turn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "string"},
                    "action": {"type": "string", "description": "move | gather | deliver | hold"},
                    "to": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[x, y]; required for move.",
                    },
                },
                "required": ["unit_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit",
            "description": (
                "Send this turn's orders to the arena, with a standing plan and a "
                "summary a later turn can start from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {"type": "string"},
                    "summary": {"type": "string"},
                    "message": {"type": "string", "description": "Optional team message."},
                },
                "required": ["plan", "summary"],
            },
        },
    },
]

TOOL_NAMES = tuple(entry["function"]["name"] for entry in TOOL_SCHEMA)


# ── the league seam: a subprocess, never an import ───────────────────────────


class LeagueError(RuntimeError):
    """The arena CLI refused a call. Carries what was run and what it said."""


class LeagueCli:
    """Every call this host makes to the arena, in one place.

    ``workdir`` is the process CWD for every invocation, and league roots its
    store at ``<cwd>/.league`` (``league/store.py:52``) — so pointing this at a
    scratch directory is what keeps a match out of any real checkout.
    """

    def __init__(
        self,
        *,
        binary: str = DEFAULT_LEAGUE_BIN,
        workdir: Path,
        timeout: float = 300.0,
    ) -> None:
        # An operator-supplied program name, split without a shell.
        self.argv0 = shlex.split(binary)
        if not self.argv0:
            raise LeagueError("no arena binary was configured")
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.calls: list[list[str]] = []

    # -- the one place a process is spawned ----------------------------------

    def _run(self, args: list[str], *, json_mode: bool = True) -> str:
        argv = [*self.argv0, *args] + (["--json"] if json_mode else [])
        self.calls.append(list(argv))
        # Fixed argv, shell=False, bounded by a timeout.
        proc = subprocess.run(  # nosec B603
            argv,
            cwd=str(self.workdir),
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise LeagueError(
                f"{' '.join(argv[:4])} failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:400]}"
            )
        return proc.stdout

    def _json(self, args: list[str]) -> dict[str, Any]:
        raw = self._run(args)
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise LeagueError(f"{' '.join(args[:3])} returned unreadable JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise LeagueError(f"{' '.join(args[:3])} returned {type(payload).__name__}, not object")
        return payload

    # -- the public verbs ----------------------------------------------------

    def register_team(self, team_id: str, *, name: str, agents: list[str]) -> dict[str, Any]:
        args = ["team", "register", team_id, "--name", name]
        for spec in agents:
            args += ["--agent", spec]
        return self._json([*args, "--apply"])

    def new_match(
        self,
        *,
        match_id: str,
        scenario: str,
        teams: list[str],
        seed: int,
        drivers: dict[str, str],
    ) -> dict[str, Any]:
        args = ["match", "new", "--scenario", scenario, "--seed", str(seed), "--id", match_id]
        for team in teams:
            args += ["--team", team]
        for team, kind in sorted(drivers.items()):
            args += ["--driver", f"{team}:{kind}"]
        return self._json([*args, "--apply"])

    def show(self, match_id: str) -> dict[str, Any]:
        return self._json(["match", "show", match_id])

    def act(self, match_id: str, team_id: str, orders: dict[str, Any]) -> dict[str, Any]:
        return self._json(
            [
                "match",
                "act",
                match_id,
                "--team",
                team_id,
                "--orders-json",
                json.dumps(orders, sort_keys=True),
                "--apply",
            ]
        )

    def score(self, match_id: str) -> dict[str, Any]:
        return self._json(["match", "score", match_id])

    def replay(self, match_id: str) -> str:
        """The replay payload as bytes-on-the-wire, so it can be hashed.

        league's own claim is that the same log renders byte-identically
        (``docs/replay-design.md``); hashing what the CLI actually printed is
        how a later comparison gets backed by something rather than asserted.
        """
        return self._run(["match", "replay", match_id])


def replay_digest(payload: str) -> str:
    """The deterministic hash a comparison is backed by."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── reading the board (pure; stdlib only) ────────────────────────────────────


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """The first JSON object in *text*, or ``None``.

    Mirrors ``league/harness.py:_extract_json`` — league scans a driver's
    stdout exactly this way, so a seat that answers with prose around its JSON
    still parses. Reading its input the same way is what makes this host
    symmetric with the arena that may drive it.
    """
    decoder = json.JSONDecoder()
    for start in range(len(text)):
        if text[start] != "{":
            continue
        try:
            found, _ = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(found, dict):
            return found
    return None


def _json_objects(text: str) -> "Iterator[dict[str, Any]]":
    """Every TOP-LEVEL JSON object in *text*, in order.

    Unlike :func:`_extract_json` this walks past each object it decodes rather
    than restarting one character later, so a unit dict nested inside a board
    is never yielded as if it were a board of its own.
    """
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return
        try:
            found, end = decoder.raw_decode(text[start:])
        except ValueError:
            index = start + 1
            continue
        index = start + end
        if isinstance(found, dict):
            yield found


def _looks_like_a_board(candidate: dict[str, Any]) -> bool:
    """A ``MatchState`` has both units and a clock; nothing else here does."""
    return "units" in candidate and "turn" in candidate


def read_observation(text: str) -> dict[str, Any]:
    """Normalise whatever arrived on stdin into a ``match show``-shaped dict.

    Three shapes are accepted, because three callers exist: this host's own
    parent (the full ``match show --json`` payload), a bare ``state`` dict, and
    league's ``command`` driver prompt (prose with the state embedded).

    **The prompt is not searched for the first JSON object.** league's template
    (``league/harness.py:409-423``) prints ``Scenario: {…}`` *before* ``Current
    match state (JSON): {…}``, so "first object wins" hands back the scenario —
    a dict with no units, which reads as a finished match and makes the seat
    submit nothing at all. That is exactly what happened the first time this
    host was driven by ``league harness run``: six turns resolved, zero orders,
    a cooperation score of 0, and no error anywhere. So every top-level object
    is considered and the first one that actually IS a board wins.
    """
    fallback: Optional[dict[str, Any]] = None
    for candidate in _json_objects(text):
        if isinstance(candidate.get("state"), dict):
            show = dict(candidate)
            break
        if _looks_like_a_board(candidate):
            show = {"state": candidate}
            break
        if fallback is None:
            fallback = candidate
    else:
        raise ValueError(
            "no board was offered on stdin"
            + (" (the only JSON object found was not a match state)" if fallback else "")
        )
    show.setdefault("legal_actions", {})
    show.setdefault("last_turn_rejections", [])
    show.setdefault("driver_kinds", {})
    return show


def living_units(show: dict[str, Any], team_id: str) -> list[dict[str, Any]]:
    """This team's living units, in the arena's own order."""
    units = (show.get("state") or {}).get("units") or []
    return [u for u in units if u.get("team_id") == team_id and u.get("alive", True)]


def observation_view(show: dict[str, Any], team_id: str) -> dict[str, Any]:
    """What the mind is shown: this team's board, and nothing it cannot use."""
    state = show.get("state") or {}
    mine = living_units(show, team_id)
    my_ids = {u.get("id") for u in mine}
    legal = show.get("legal_actions") or {}
    return {
        "state": {
            "match_id": state.get("match_id"),
            "scenario_id": state.get("scenario_id"),
            "turn": state.get("turn"),
            "turn_limit": state.get("turn_limit"),
            "status": state.get("status"),
            "winner": state.get("winner"),
        },
        "team": team_id,
        "my_units": [
            {
                "id": u.get("id"),
                "role": u.get("role"),
                "pos": list(u.get("pos") or []),
                "carrying": u.get("carrying", 0),
            }
            for u in mine
        ],
        "control_points": [
            {"id": c.get("id"), "pos": list(c.get("pos") or []), "owner": c.get("owner")}
            for c in state.get("control_points") or []
        ],
        "resource_nodes": [
            {"id": r.get("id"), "pos": list(r.get("pos") or [])}
            for r in state.get("resource_nodes") or []
        ],
        "legal_actions": {k: v for k, v in legal.items() if k in my_ids},
        "rejections": show.get("last_turn_rejections") or [],
    }


def observation_block(view: dict[str, Any]) -> str:
    """The board as the prompt carries it — one marker, one JSON object."""
    return f"{OBSERVATION_MARKER}\n{json.dumps(view, sort_keys=True)}"


def last_observation(transcript: str) -> Optional[dict[str, Any]]:
    """The NEWEST board in a transcript. A resident drive accumulates many."""
    if OBSERVATION_MARKER not in transcript:
        return None
    return _extract_json(transcript.rsplit(OBSERVATION_MARKER, 1)[-1])


def manhattan(a: Any, b: Any) -> int:
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def control_point(view: dict[str, Any], cp_id: str) -> Optional[list[int]]:
    for point in view.get("control_points") or []:
        if point.get("id") == cp_id:
            return [int(v) for v in point.get("pos") or []]
    return None


def choose_move(
    unit: dict[str, Any], view: dict[str, Any], target: list[int]
) -> Optional[list[int]]:
    """The legal move that gets closest to *target*, or ``None`` to hold.

    Prefers the arena's own ``legal_actions`` list when it was offered. When it
    was not — league's ``command`` driver prompt does not carry one in the state
    blob — it falls back to a single orthogonal step, which every role can make.
    """
    here = [int(v) for v in unit.get("pos") or []]
    if here == target:
        return None
    legal = ((view.get("legal_actions") or {}).get(str(unit.get("id"))) or {}).get("move")
    if legal:
        best = min((list(map(int, sq)) for sq in legal), key=lambda sq: (manhattan(sq, target), sq))
        return best if manhattan(best, target) < manhattan(here, target) else None
    step = list(here)
    if here[0] != target[0]:
        step[0] += 1 if target[0] > here[0] else -1
    else:
        step[1] += 1 if target[1] > here[1] else -1
    return step


# ── the house opponent: a policy, not a mind ─────────────────────────────────


def rival_orders(show: dict[str, Any], team_id: str) -> dict[str, Any]:
    """Every unit steps toward its nearest control point, then holds.

    Deterministic and deliberately unremarkable. This file makes no claim about
    winning: the opponent exists so the seat has to keep playing against
    something that moves, not so a score means anything.
    """
    view = observation_view(show, team_id)
    points = view.get("control_points") or []
    actions: list[dict[str, Any]] = []
    for unit in view.get("my_units") or []:
        here = [int(v) for v in unit.get("pos") or []]
        if not points:
            actions.append({"unit_id": unit["id"], "action": "hold"})
            continue
        nearest = min(points, key=lambda p: (manhattan(p["pos"], here), str(p["id"])))
        step = choose_move(unit, view, [int(v) for v in nearest["pos"]])
        if step is None:
            actions.append({"unit_id": unit["id"], "action": "hold"})
        else:
            actions.append({"unit_id": unit["id"], "action": "move", "to": step})
    return {"plan": "converge on the nearest control point", "actions": actions}


# ── the seat's tool surface ──────────────────────────────────────────────────


class Seat:
    """Three domain tools. embodiment never builds one of these — the host does.

    ``advance`` is the only difference between the two arms. Absent (the command
    arm), ``submit`` ends the drive and the orders are printed for the parent to
    file. Present (the resident arm), ``submit`` files the orders itself, lets
    the opponent answer, and hands the next board back as the tool result — so
    one drive spans the whole match.
    """

    def __init__(
        self,
        *,
        team_id: str,
        pad: Scratchpad,
        show: dict[str, Any],
        agent_id: str = "",
        advance: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None,
    ) -> None:
        self.team_id = team_id
        self.pad = pad
        self.show = show
        self.agent_id = agent_id
        self.advance = advance
        self.staged: list[dict[str, Any]] = []
        self.submitted: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.changed: list[str] = []

    # -- the protocol embodiment actually uses -------------------------------

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, dict(arguments)))
        if name == "note":
            return self._note(arguments)
        if name == "order":
            return self._order(arguments)
        if name == "submit":
            return self._submit(arguments)
        raise UnknownToolError(f"this seat has no tool called {name!r}")

    def state(self) -> str:
        """A one-line snapshot for the presence pump."""
        turn = (self.show.get("state") or {}).get("turn")
        return f"turn {turn}; {len(self.staged)} order(s) staged, {len(self.submitted)} sent"

    # -- the tools -----------------------------------------------------------

    def _note(self, arguments: dict[str, Any]) -> ToolOutcome:
        kind = str(arguments.get("kind", "")).strip()
        if kind not in scratchpad.KINDS or kind == "revise":
            raise ToolError(f"note kind must be one of intend/observe/conclude, not {kind!r}")
        outcome = self.pad.execute(kind, {"text": str(arguments.get("text", ""))})
        if self.pad.path is not None:
            self.changed.append(str(self.pad.path))
        # Bracketed markers, because a hermetic mind counts them out of the
        # transcript and the transcript also carries recalled prose. A bare
        # "noted"/"ordered" would be miscounted the moment a remembered summary
        # happened to use the same word.
        return ToolOutcome(result=f"{MARK_NOTED} {outcome.result}")

    def _order(self, arguments: dict[str, Any]) -> ToolOutcome:
        unit_id = str(arguments.get("unit_id", "")).strip()
        known = {u.get("id") for u in living_units(self.show, self.team_id)}
        if unit_id not in known:
            # A ToolError costs one self-correcting step, never the turn.
            raise ToolError(f"{unit_id!r} is not one of your living units: {sorted(known)}")
        action = str(arguments.get("action", "hold")).strip() or "hold"
        order: dict[str, Any] = {"unit_id": unit_id, "action": action}
        target = arguments.get("to")
        if isinstance(target, (list, tuple)) and len(target) == 2:
            order["to"] = [int(target[0]), int(target[1])]
        elif action == "move":
            raise ToolError("a move order needs a 'to' of [x, y]")
        self.staged = [o for o in self.staged if o["unit_id"] != unit_id] + [order]
        rendered = order.get("to", "")
        return ToolOutcome(result=f"{MARK_ORDERED} {unit_id} {action} {rendered}".strip())

    def _submit(self, arguments: dict[str, Any]) -> ToolOutcome:
        summary = str(arguments.get("summary", "")).strip()
        orders: dict[str, Any] = {
            "plan": str(arguments.get("plan", "")).strip(),
            "actions": list(self.staged),
        }
        message = str(arguments.get("message", "")).strip()
        if message and self.agent_id:
            orders["messages"] = [{"from": self.agent_id, "text": message}]
        self.submitted.append(orders)
        self.staged = []

        if self.advance is None:
            # The command arm: one drive is one turn, and the parent files it.
            return ToolOutcome(result="orders submitted", finished=True, finish_summary=summary)

        nxt = self.advance(orders)
        if nxt is None:
            return ToolOutcome(result="the match is over", finished=True, finish_summary=summary)
        self.show = nxt
        return ToolOutcome(
            result="orders resolved; the next board follows.\n"
            + observation_block(observation_view(nxt, self.team_id))
        )


# ── the hermetic mind: a function of its prompt, not a fixture ───────────────

#: The operator's objective, as it survives into a pad line or a durable
#: record. Only a *taking* verb matches, so "ignore cp-east" never becomes one.
#:
#: The words between the verb and the point are the whole reason this is not
#: ``r"take (cp-[a-z0-9-]+)"``. That earlier pattern matched the directive as
#: issued — "take cp-west and hold it" — but **not the mind's own paraphrase**,
#: which is what actually lands in the store:
#:
#:     "Turn 0 complete. Units advanced toward cp-west.
#:      Objective: take and hold cp-west."
#:
#: "and hold" sits between ``take`` and ``cp-west``, so the extractor returned
#: ``""`` and the t19 series graded its pre-registered continuity prediction as
#: FAIL on 6 of 6 matches whose stores plainly held the objective. The grader
#: was blind to a word order, not to a missing memory. ``{0,3}`` bounds the gap
#: so a *distant* mention two sentences away still does not count.
_OBJECTIVE_RE = re.compile(
    r"\b(?:take|capture|hold|secure)\b(?:\s+\w+){0,3}\s+(cp-[a-z0-9-]+)",
    re.IGNORECASE,
)


def message_text(message: dict[str, Any]) -> str:
    """The readable text of one wire message, list-content included."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def read_objective(transcript: str) -> str:
    """The standing objective this mind can see — or ``""`` if it cannot.

    Nothing is memoised and nothing is hard-coded about ``cp-west``. Handed a
    conversation where the directive never arrived and the pad and store are
    empty, this returns ``""`` and the mind holds — which is exactly what makes
    the command arm's continuity a proof rather than a re-enactment.
    """
    found = _OBJECTIVE_RE.search(transcript)
    return found.group(1) if found else ""


def make_scripted_cortex() -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """The hermetic model seam: one turn, decided entirely by the prompt."""

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        transcript = "\n".join(message_text(message) for message in messages)
        view = last_observation(transcript)
        if view is None:
            return _call("submit", plan="", summary="no board was offered; nothing to do")

        state = view.get("state") or {}
        if str(state.get("status") or "") != "active":
            return _call("submit", plan="", summary=f"match {state.get('status')}; standing down.")

        objective = read_objective(transcript)
        units = view.get("my_units") or []
        tail = transcript.rsplit(OBSERVATION_MARKER, 1)[-1]

        if MARK_NOTED not in tail:
            # The intent is written BEFORE the act, and it restates the standing
            # objective in full — the pad is written for a successor who wakes
            # with nothing, and "toward it" would leave that successor guessing.
            text = (
                f"Turn {state.get('turn')}: the standing objective is to take "
                f"{objective} and hold it; moving every unit toward it."
                if objective
                else f"Turn {state.get('turn')}: no standing objective in memory; holding."
            )
            return _call("note", kind="intend", text=text)

        ordered = tail.count(MARK_ORDERED)
        if ordered < len(units):
            unit = units[ordered]
            target = control_point(view, objective) if objective else None
            step = choose_move(unit, view, target) if target is not None else None
            if step is None:
                return _call("order", unit_id=str(unit.get("id")), action="hold")
            return _call("order", unit_id=str(unit.get("id")), action="move", to=step)

        if objective:
            plan = f"take {objective} and hold it"
            summary = (
                f"Turn {state.get('turn')}: standing objective is to take {objective} "
                f"and hold it; {ordered} unit(s) sent toward it."
            )
        else:
            plan = "hold position"
            summary = (
                f"Turn {state.get('turn')}: no standing objective was recoverable, "
                f"so {ordered} unit(s) held."
            )
        return _call("submit", plan=plan, summary=summary, message=plan)

    return complete


_MUSE_TURNS = (
    "The objective is the part worth keeping. A position is weather; a standing "
    "order is knowledge.",
    "GUIDANCE: say WHY you are still pushing that point, not only that you are.\n[done]",
)


def scripted_muse(messages: list[dict[str, Any]]) -> ModelResponse:
    """A hermetic stand-in for the advisory lane. Proposes; never decides."""
    turn = sum(1 for message in messages if message.get("role") == "assistant")
    return ModelResponse(content=_MUSE_TURNS[min(turn, len(_MUSE_TURNS) - 1)])


def _call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        content="",
        tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=dict(arguments))],
    )


# ── the live seam: one HTTP round trip per model turn ────────────────────────


def completion_trace(role: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
    """What :class:`ModelResponse` cannot carry, kept beside the run.

    ``finish_reason`` is the field that separates *the model stopped* from *the
    model was cut off*, and ``embodiment.contract.ModelResponse`` does not carry
    it (embodiment#37). Both arrive at the loop as empty content with no tool
    calls, so a host reading only the loop's own record cannot tell a truncated
    turn from a deliberate one — and t23 measured this cortex returning
    ``length`` with **zero** content characters at a full 16000-token budget.

    This function reads it off the raw payload the seam already has in hand and
    is otherwise about to discard. It is instrumentation, not contract: nothing
    in the loop consumes it, and with no ``--trace-out`` nothing calls it.
    """
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    usage = payload.get("usage") or {}
    calls = [
        str(((raw or {}).get("function") or {}).get("name") or "")
        for raw in (message.get("tool_calls") or [])
    ]
    return {
        "ts": round(time.time(), 3),
        "pid": os.getpid(),
        "role": role,
        "model": model,
        "finish_reason": (choices[0] or {}).get("finish_reason"),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "content_chars": len(str(message.get("content") or "")),
        "reasoning_chars": len(
            str(message.get("reasoning_content") or message.get("reasoning") or "")
        ),
        "tool_calls": calls,
    }


def trace_writer(path: Path, role: str, model: str) -> Callable[[dict[str, Any]], None]:
    """Append one JSON object **per completion**, flushed as it goes.

    Per completion, not per run. A harness that writes only when the last run
    returns hands back a zero-byte file when the gateway 503s mid-series, which
    is how one sibling task lost 41 minutes of live cortex work. The command arm
    makes this sharper still: every turn is a different process appending to the
    same file, and they run strictly serially.
    """

    def write(payload: dict[str, Any]) -> None:
        append_jsonl(path, completion_trace(role, model, payload))

    return write


def parse_completion(payload: dict[str, Any]) -> ModelResponse:
    """Shape one OpenAI-compatible completion into a :class:`ModelResponse`.

    ``reasoning`` is carried separately from ``content`` because the reference
    cortex is a thinking model; a run that folded the two together would report
    a thought as if it were a reply.
    """
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    raw_calls = message.get("tool_calls") or []

    calls: list[ToolCall] = []
    for index, raw in enumerate(raw_calls):
        function = (raw or {}).get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            arguments = {}
        calls.append(
            ToolCall(
                id=str((raw or {}).get("id") or f"call-{index}"),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )

    usage = payload.get("usage") or {}
    return ModelResponse(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("reasoning_content") or message.get("reasoning") or ""),
        tool_calls=calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )


def gateway_seam(
    base_url: str,
    model: str,
    api_key: str,
    *,
    max_tokens: int,
    temperature: float = DEFAULT_TEMPERATURE,
    tools: Optional[list[dict[str, Any]]] = None,
    timeout: float = 300.0,
    trace: Optional[Callable[[dict[str, Any]], None]] = None,
) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """Build a model seam that talks to one OpenAI-compatible endpoint.

    Roles are addressed **by name**: this function is handed a model id and
    infers nothing from it. The muse lane passes no ``tools`` at all — that
    absence is the whole of "tools-off".

    ``trace`` is optional instrumentation and defaults to ``None``, so the
    shipped path is byte-for-byte what it was: the raw payload is parsed and
    dropped exactly as before unless a caller asks to keep a record of it.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    if not endpoint.startswith(("http://", "https://")):
        raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # From the environment, and never echoed anywhere.
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        # The scheme is pinned to http(s) above and the endpoint is the
        # operator's own --base-url; audited once, here.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
        if trace is not None:
            trace(payload)
        return parse_completion(payload)

    return complete


def build_minds(args: argparse.Namespace) -> tuple[Any, Optional[Any], str, Optional[str]]:
    """Resolve the model seams. ``--live`` is the ONLY path that reaches a network."""
    if not args.live:
        muse = scripted_muse if args.muse else None
        return (
            make_scripted_cortex(),
            muse,
            SCRIPTED_CORTEX,
            SCRIPTED_MUSE if args.muse else None,
        )

    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        print(f"error: --live needs {API_KEY_ENV} in the environment", file=sys.stderr)
        print(
            f"hint: export {API_KEY_ENV}=… and point --base-url at your gateway "
            f"(currently {args.base_url})",
            file=sys.stderr,
        )
        raise SystemExit(2)

    trace_out = getattr(args, "trace_out", "") or ""
    cortex = gateway_seam(
        args.base_url,
        args.cortex_model,
        key,
        max_tokens=args.max_tokens,
        temperature=args.cortex_temperature,
        tools=TOOL_SCHEMA,
        trace=trace_writer(Path(trace_out), "cortex", args.cortex_model) if trace_out else None,
    )
    muse = None
    if args.muse:
        # Tools-off by construction: no schema is passed, so none can be called.
        muse = gateway_seam(
            args.base_url,
            args.muse_model,
            key,
            max_tokens=args.muse_max_tokens,
            temperature=args.muse_temperature,
            trace=trace_writer(Path(trace_out), "muse", args.muse_model) if trace_out else None,
        )
    return cortex, muse, args.cortex_model, (args.muse_model if args.muse else None)


# ── continuity: the pad and the store ────────────────────────────────────────


def is_consequential(boundary: Boundary) -> bool:
    """Which acts are worth a continuity checkpoint. The HOST decides.

    Sending orders changes the world; writing a pad line does not. embodiment
    cannot know that, and guessing it from a tool name would be exactly the
    inference this package refuses elsewhere.
    """
    return boundary.tool == "submit"


def lifecycle_config(
    store: Path, workdir: Path, *, scope: str, coherence: bool = False
) -> LifecycleConfig:
    """How this host reaches durable memory.

    ``data_dir`` is the mandatory anchor: without it eidetic would resolve a
    public record against whatever git repo the host process happens to be
    running in, and would quietly commit a match's memories into it. That is
    why ``--store`` has no default.
    """
    return LifecycleConfig(
        data_dir=Path(store),
        scope=scope,
        record_type=RECORD_TYPE,
        added_by=ADDED_BY,
        consequential=is_consequential,
        assess_action=coherence,
        assess_completion=coherence,
        assess_memory=coherence,
        recall_mode=RECALL_MODE,
        recall_top_k=RECALL_TOP_K,
        workdir=Path(workdir),
    )


def recall_prior(query: str, *, store: Path, scope: str, top_k: int) -> Any:
    """What this seat already knows, read through the public seam."""
    return continuity.recall(
        query,
        data_dir=Path(store),
        scope=scope,
        top_k=top_k,
        mode=RECALL_MODE,
    )


def build_task(
    task_id: str,
    *,
    view: dict[str, Any],
    directive: str,
    pad: Scratchpad,
    recalled: list[str],
    engine: str,
) -> Task:
    """Compose the work item — including what the mind is TOLD it remembers.

    embodiment recalls, but it never injects: what the acting mind is told is
    host policy. This is that policy, and in the command arm it is the only
    reason turn 4 knows anything about turn 0.
    """
    parts: list[str] = []
    if directive:
        parts.append(directive)
    parts.append(observation_block(view))
    context_parts: list[str] = []
    rendered = resume_report(pad)
    if pad.entries:
        context_parts.append("What you left yourself on the scratchpad:\n" + rendered)
    if recalled:
        context_parts.append(
            "What you remember of earlier turns:\n" + "\n".join(f"- {t}" for t in recalled)
        )
    return Task(
        id=task_id,
        # A grid match has no repo; a blank path is read as "no rig to resolve"
        # rather than falling back to the ambient directory.
        repo_path="",
        instruction="\n\n".join(parts),
        context="\n\n".join(context_parts),
        engine=engine,
    )


def build_system_prompt(identity: Optional[str], *, muse: bool) -> str:
    """The cortex's system prompt, framed only when an identity is configured.

    Absent identity ⇒ **byte-identical** prompt. That is the acceptance
    criterion that keeps the whole framing feature honest, and it is one call.
    """
    return frame_cortex(BASE_SYSTEM, identity=identity or None, muse=muse) or BASE_SYSTEM


def muse_snapshot(
    runner: Optional[ThreadedMuseRunner], runner_id: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """The advisory lane's own record, JSON-safe. ``None`` when none ran.

    ``runner_id`` is minted once per runner and stamped on every turn record it
    serves. That is what makes "the muse thread lived across turns" a fact a
    reader can check in the log rather than a race against a counter: the
    resident arm's turns all carry ONE id, the command arm's carry a different
    one every turn because every turn is a different process.
    """
    if runner is None:
        return None
    snapshot = runner.snapshot()
    snapshot["degradations"] = [record.to_dict() for record in snapshot["degradations"]]
    snapshot["runner_id"] = runner_id
    return snapshot


def build_muse(
    args: argparse.Namespace, muse_complete: Any, identity: Optional[str]
) -> tuple[Any, Optional[str]]:
    """One :class:`ThreadedMuseRunner` and its id, or ``(None, None)`` when off."""
    if muse_complete is None:
        return None, None
    runner = ThreadedMuseRunner(
        muse_complete,
        system=frame_muse(None, identity=identity),
        controls=MuseControls(max_turns=args.muse_max_turns),
    )
    return runner, uuid.uuid4().hex[:12]


def fold_degradations(
    *, outcome: Any = None, runner: Any = None, lifecycle: Any = None, observer: Any = None
) -> list[dict[str, Any]]:
    """Every lane's degradations, in the one shape a host reads (C3)."""
    records = ledger.read(
        loop=outcome,
        muse_runner=runner,
        lifecycle=lifecycle,
        events=observer,
    )
    return [record.to_dict() for record in records]


# ── the match log ────────────────────────────────────────────────────────────


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """One JSON object per line, flushed as it goes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def config_path_for(log_path: Path) -> Path:
    """Where the config preamble lands, beside the log it describes."""
    return log_path.with_name(log_path.stem + ".config.json")


def write_preamble(args: argparse.Namespace, log_path: Path, arena: dict[str, Any]) -> dict:
    """Write the configuration BEFORE the first result line.

    The model/temperature/muse half is :mod:`examples.challenge_config`'s job —
    the same preamble every experiment harness in this repo writes — so a run is
    reproducible from the JSON alone and per-role temperature can never again be
    the hidden variable it was in the first live series.
    """
    cortex_model = args.cortex_model if args.live else SCRIPTED_CORTEX
    muse_model: Optional[str] = None
    if args.muse:
        muse_model = args.muse_model if args.live else SCRIPTED_MUSE
    config = write_config_preamble(
        str(config_path_for(log_path)),
        cortex_model=cortex_model,
        cortex_temperature=args.cortex_temperature,
        muse_model=muse_model,
        muse_temperature=args.muse_temperature if args.muse else None,
        max_turns=args.muse_max_turns,
        staleness_policy="default",
        n=1,
        # The token budget is a hidden variable until it is written down. The
        # t19 arena series ran its whole 24-match matrix at this file's default
        # of 2048 — ``arena_series.seat_argv`` passes no ``--max-tokens`` — and
        # its config preamble recorded temperature but not the cap, so nothing
        # in the published record says what budget the numbers were measured
        # through (embodiment#37, and t23's token-budget finding).
        extra={
            "cortex_max_tokens": args.max_tokens if args.live else None,
            "muse_max_tokens": args.muse_max_tokens if (args.live and args.muse) else None,
            "max_steps": args.max_steps,
            "trace_out": getattr(args, "trace_out", "") or None,
        },
    )
    append_jsonl(log_path, {"kind": "config", "config": config, "arena": arena})
    return config


# ── one turn (the command arm's whole process) ───────────────────────────────


def take_turn(
    args: argparse.Namespace,
    show: dict[str, Any],
    *,
    observer: Any = None,
) -> dict[str, Any]:
    """Drive ONE league turn and return ``{"orders": …, "record": …}``.

    This is the whole of the ``turn`` subcommand, and it is also what the
    resident arm would look like if it were cut at every turn boundary. It
    starts from nothing but its arguments, the board on stdin, the pad file and
    the store — which is precisely the claim the command arm exists to test.
    """
    view = observation_view(show, args.team)
    state = show.get("state") or {}
    turn_index = int(state.get("turn") or 0)

    pad = Scratchpad.load(Path(args.pad))
    prior = recall_prior(
        f"{args.team} standing objective control point",
        store=Path(args.store),
        scope=args.scope,
        top_k=args.recall_top_k,
    )
    recalled_text = [str(record.get("text", "")) for record in prior.records]
    recalled_ids = [str(record["id"]) for record in prior.records if record.get("id")]

    identity = (args.identity or "").strip() or None
    cortex, muse_complete, cortex_model, muse_model = build_minds(args)
    runner, muse_id = build_muse(args, muse_complete, identity)

    agents = [a.get("id", "") for a in _team_agents(show, args.team)]
    seat = Seat(
        team_id=args.team,
        pad=pad,
        show=show,
        agent_id=agents[0] if agents else "",
    )
    presence = PresenceEngine(
        io=PresenceIO(render=lambda line: print(line, file=sys.stderr), task_state=seat.state),
        muse=runner,
        speaker=speaker_label(identity),
    )
    lifecycle = build_continuity_fn(
        lifecycle_config(
            Path(args.store), Path(args.workdir), scope=args.scope, coherence=args.coherence
        )
    )

    task = build_task(
        f"{state.get('match_id') or 'match'}-t{turn_index}",
        view=view,
        directive=args.directive,
        pad=pad,
        recalled=recalled_text,
        engine="league-seat",
    )

    aborted: Optional[str] = None
    try:
        outcome = run(
            cortex,
            task,
            executor=seat,
            max_steps=args.max_steps,
            system_prompt=build_system_prompt(identity, muse=runner is not None),
            presence=presence,
            continuity=lifecycle,
            observer=observer,
            controls=LoopControls(write_intent=False),
            model=cortex_model,
        )
    except LoopAborted as failure:
        outcome = failure.outcome
        aborted = str(failure.__cause__ or failure)
    finally:
        if runner is not None:
            runner.close()

    orders = seat.submitted[-1] if seat.submitted else {"actions": []}
    events = [event.to_dict() for event in lifecycle.events]
    remembered = next((e["data"] for e in events if e["kind"] == "remembered"), None)
    record = {
        "turn": turn_index,
        "arm": ARM_COMMAND,
        "residency": LEAGUE_RESIDENCY[ARM_COMMAND],
        "pid": os.getpid(),
        "orders": orders,
        "pad_entries": len(pad.entries),
        "open_intent": pad.open_intent.id if pad.open_intent else None,
        "recalled": recalled_ids,
        "recalled_text": recalled_text,
        "remembered": remembered,
        "directive_given": bool(args.directive),
        "objective_seen": read_objective(task.instruction + "\n" + task.context),
        "muse": muse_snapshot(runner, muse_id),
        "drive": {
            "exit_reason": outcome.exit_reason,
            "status": outcome.result.status,
            "summary": outcome.result.summary,
            "model_turns": outcome.result.stats.model_turns,
            "tools": [step.tool for step in outcome.result.steps],
            "aborted": aborted,
        },
        "mind": {"cortex": cortex_model, "muse": muse_model},
        "degradations": fold_degradations(
            outcome=outcome, runner=runner, lifecycle=lifecycle, observer=observer
        ),
    }
    return {"orders": orders, "record": record}


def _team_agents(show: dict[str, Any], team_id: str) -> list[dict[str, Any]]:
    for team in (show.get("state") or {}).get("teams") or []:
        if team.get("id") == team_id:
            return list(team.get("agents") or [])
    return []


# ── a whole match ────────────────────────────────────────────────────────────


def play(args: argparse.Namespace, *, observer: Any = None) -> dict[str, Any]:
    """Play one match in one arm, and write the match log as it goes."""
    workdir = Path(args.workdir).expanduser()
    if args.reset:
        shutil.rmtree(workdir, ignore_errors=True)
        shutil.rmtree(Path(args.store), ignore_errors=True)
        Path(args.pad).unlink(missing_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    Path(args.store).mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log)
    log_path.unlink(missing_ok=True)
    config_path_for(log_path).unlink(missing_ok=True)

    cli = LeagueCli(binary=args.league_bin, workdir=workdir, timeout=args.league_timeout)
    identity = (args.identity or "").strip() or None
    seat_model = args.cortex_model if args.live else SCRIPTED_CORTEX

    cli.register_team(
        args.team,
        name=args.team.title(),
        agents=[f"{args.team[0]}{i + 1}:{seat_model}:{r}" for i, r in enumerate(ROSTER_ROLES)],
    )
    cli.register_team(
        args.rival,
        name=args.rival.title(),
        agents=[f"{args.rival[0]}{i + 1}:{RIVAL_MODEL}:{r}" for i, r in enumerate(ROSTER_ROLES)],
    )
    created = cli.new_match(
        match_id=args.match_id,
        scenario=args.scenario,
        teams=[args.team, args.rival],
        seed=args.seed,
        drivers={args.team: LEAGUE_RESIDENCY[args.arm], args.rival: "bot"},
    )

    show = cli.show(args.match_id)
    turn_limit = int((show.get("state") or {}).get("turn_limit") or 0)
    budget = min(args.max_turns, turn_limit) if args.max_turns else turn_limit
    arena = {
        "binary": args.league_bin,
        "workdir": str(workdir),
        "match_id": args.match_id,
        "scenario": args.scenario,
        "seed": args.seed,
        "teams": {"seat": args.team, "rival": args.rival},
        # league's own declared residency, echoed back from `match new --json`
        # and written into the match log header it keeps.
        "driver_kinds": created.get("driver_kinds") or {},
        "turn_limit": turn_limit,
        "turn_budget": budget,
        "arm": args.arm,
        "residency": LEAGUE_RESIDENCY[args.arm],
        "muse": bool(args.muse),
        "identity": identity,
        "store": str(args.store),
        "pad": str(args.pad),
        "scope": args.scope,
        "directive": args.directive,
    }
    # Configuration BEFORE the first result line.
    config = write_preamble(args, log_path, arena)

    turns: list[dict[str, Any]] = []
    match_degradations: list[dict[str, Any]] = []
    drive: Optional[dict[str, Any]] = None
    if args.arm == ARM_RESIDENT:
        drive, match_extra = _play_resident(
            args, cli, show, budget, turns, log_path, observer=observer
        )
        match_degradations = match_extra
    else:
        match_degradations = _play_command(args, cli, show, budget, turns, log_path)

    final = cli.show(args.match_id)
    state = final.get("state") or {}
    score = cli.score(args.match_id)
    replay = cli.replay(args.match_id)

    report = {
        "arm": args.arm,
        "muse": bool(args.muse),
        "pid": os.getpid(),
        "arena": arena,
        "config": config,
        "mind": {
            "cortex": seat_model,
            "muse": (args.muse_model if args.live else SCRIPTED_MUSE) if args.muse else None,
            "identity": identity,
            "speaker": speaker_label(identity),
        },
        "turns": turns,
        "drive": drive,
        "match": {
            "status": state.get("status"),
            "winner": state.get("winner"),
            "turn": state.get("turn"),
            "turns_played": len(turns),
            "score": score,
            "replay_sha256": replay_digest(replay),
            "driver_kinds": final.get("driver_kinds") or {},
        },
        "degradations": match_degradations,
        "log": str(log_path),
        "config_log": str(config_path_for(log_path)),
        "league_calls": [call[len(cli.argv0) :] for call in cli.calls],
    }
    append_jsonl(log_path, {"kind": "match", **report["match"], "arm": args.arm, "drive": drive})
    return report


def _record_turn(log_path: Path, turns: list[dict[str, Any]], record: dict[str, Any]) -> None:
    turns.append(record)
    append_jsonl(log_path, {"kind": "turn", **record})


def _play_resident(
    args: argparse.Namespace,
    cli: LeagueCli,
    show: dict[str, Any],
    budget: int,
    turns: list[dict[str, Any]],
    log_path: Path,
    *,
    observer: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """ONE drive for the whole match, with ONE muse thread beside it."""
    pad = Scratchpad.load(Path(args.pad))
    prior = recall_prior(
        f"{args.team} standing objective control point",
        store=Path(args.store),
        scope=args.scope,
        top_k=args.recall_top_k,
    )
    recalled_text = [str(record.get("text", "")) for record in prior.records]
    recalled_ids = [str(record["id"]) for record in prior.records if record.get("id")]

    identity = (args.identity or "").strip() or None
    cortex, muse_complete, cortex_model, muse_model = build_minds(args)
    runner, muse_id = build_muse(args, muse_complete, identity)

    agents = [a.get("id", "") for a in _team_agents(show, args.team)]
    state = show.get("state") or {}

    def advance(orders: dict[str, Any]) -> Optional[dict[str, Any]]:
        """One league turn: file our orders, let the house answer, read back."""
        before = seat.show
        turn_index = int((before.get("state") or {}).get("turn") or 0)
        cli.act(args.match_id, args.team, orders)
        cli.act(args.match_id, args.rival, rival_orders(before, args.rival))
        after = cli.show(args.match_id)
        _record_turn(
            log_path,
            turns,
            {
                "turn": turn_index,
                "arm": ARM_RESIDENT,
                "residency": LEAGUE_RESIDENCY[ARM_RESIDENT],
                # The same pid on every turn is the residency claim's own
                # evidence: this drive never left the process it started in.
                "pid": os.getpid(),
                "orders": orders,
                "pad_entries": len(pad.entries),
                "open_intent": pad.open_intent.id if pad.open_intent else None,
                # The resident arm recalls once and remembers once, because the
                # drive IS the whole match; both are recorded on the match, not
                # on a turn. Stated rather than faked with per-turn nulls.
                "recalled": [],
                "recalled_text": [],
                "remembered": None,
                "directive_given": turn_index == 0 and bool(args.directive),
                "objective_seen": read_objective(orders.get("plan", "")),
                "muse": muse_snapshot(runner, muse_id),
                "drive": None,
                "mind": {"cortex": cortex_model, "muse": muse_model},
                "degradations": [],
            },
        )
        if str((after.get("state") or {}).get("status") or "") != "active":
            return None
        if len(turns) >= budget:
            return None
        return after

    seat = Seat(
        team_id=args.team,
        pad=pad,
        show=show,
        agent_id=agents[0] if agents else "",
        advance=advance,
    )
    presence = PresenceEngine(
        io=PresenceIO(render=lambda line: print(line, file=sys.stderr), task_state=seat.state),
        muse=runner,
        speaker=speaker_label(identity),
    )
    lifecycle = build_continuity_fn(
        lifecycle_config(
            Path(args.store), Path(args.workdir), scope=args.scope, coherence=args.coherence
        )
    )
    task = build_task(
        f"{state.get('match_id') or 'match'}-resident",
        view=observation_view(show, args.team),
        directive=args.directive,
        pad=pad,
        recalled=recalled_text,
        engine="league-seat",
    )
    # One drive covers every turn, so the budget has to as well: a per-turn
    # allowance times the turns actually on offer, plus slack for the
    # self-correcting steps a rejected order costs. Recorded, never inferred.
    max_steps = args.max_steps * max(1, budget) + 8

    aborted: Optional[str] = None
    try:
        outcome = run(
            cortex,
            task,
            executor=seat,
            max_steps=max_steps,
            system_prompt=build_system_prompt(identity, muse=runner is not None),
            presence=presence,
            continuity=lifecycle,
            observer=observer,
            controls=LoopControls(write_intent=False),
            model=cortex_model,
        )
    except LoopAborted as failure:
        outcome = failure.outcome
        aborted = str(failure.__cause__ or failure)
    finally:
        if runner is not None:
            runner.close()

    events = [event.to_dict() for event in lifecycle.events]
    remembered = next((e["data"] for e in events if e["kind"] == "remembered"), None)
    drive = {
        "scope": "match",
        "exit_reason": outcome.exit_reason,
        "status": outcome.result.status,
        "summary": outcome.result.summary,
        "model_turns": outcome.result.stats.model_turns,
        "max_steps": max_steps,
        "tools": [step.tool for step in outcome.result.steps],
        "aborted": aborted,
        "pad_entries": len(pad.entries),
        "recalled": recalled_ids,
        "recalled_text": recalled_text,
        "remembered": remembered,
        "muse": muse_snapshot(runner, muse_id),
        "presence_mode": presence.mode,
    }
    degradations = fold_degradations(
        outcome=outcome, runner=runner, lifecycle=lifecycle, observer=observer
    )
    return drive, degradations


def _play_command(
    args: argparse.Namespace,
    cli: LeagueCli,
    show: dict[str, Any],
    budget: int,
    turns: list[dict[str, Any]],
    log_path: Path,
) -> list[dict[str, Any]]:
    """A fresh subprocess per turn. Nothing crosses the boundary but files.

    Returns the match's degradations, gathered from the per-turn records.

    **This used to return nothing**, so ``match_degradations`` stayed ``[]``
    for the whole command arm while the turn records held ten. The t19 series
    reported ``degradations: []`` for every CM match; a reader of the
    match-level field would have concluded the command arm never degraded —
    a C3 violation ("nothing degrades silently") inside the harness built to
    measure C3. Each fresh drive closes at the end of its turn, so the
    close-time race (embodiment#17) fires once *per turn* here against once
    *per match* in the resident arm: dropping these hid the arm that pays it
    most. See ``docs/live-test-results/arena-series.md``.
    """
    degradations: list[dict[str, Any]] = []
    while len(turns) < budget:
        state = show.get("state") or {}
        if str(state.get("status") or "") != "active":
            break
        turn_index = int(state.get("turn") or 0)
        record_path = Path(args.workdir) / f"turn-{turn_index}.json"
        orders, record = spawn_turn(args, show, record_path=record_path)
        _record_turn(log_path, turns, record)
        degradations.extend(record.get("degradations") or [])
        cli.act(args.match_id, args.team, orders)
        cli.act(args.match_id, args.rival, rival_orders(show, args.rival))
        show = cli.show(args.match_id)
    return degradations


def turn_argv(args: argparse.Namespace, *, record_path: Path, directive: str) -> list[str]:
    """The child's command line — the whole of what crosses the boundary."""
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "turn",
        "--team",
        args.team,
        "--pad",
        str(args.pad),
        "--store",
        str(args.store),
        "--workdir",
        str(args.workdir),
        "--scope",
        args.scope,
        "--record",
        str(record_path),
        "--max-steps",
        str(args.max_steps),
        "--recall-top-k",
        str(args.recall_top_k),
        "--muse-max-turns",
        str(args.muse_max_turns),
    ]
    if directive:
        argv += ["--directive", directive]
    if args.identity:
        argv += ["--identity", args.identity]
    if args.muse:
        argv.append("--muse")
    if args.coherence:
        argv.append("--coherence")
    if args.events:
        # Forwarded, because a parent's emitter cannot see a child's drive:
        # in this arm every turn happens somewhere else entirely.
        argv.append("--events")
        if args.events_host:
            argv += ["--events-host", str(args.events_host)]
        if args.events_port:
            argv += ["--events-port", str(args.events_port)]
    if args.live:
        argv += [
            "--live",
            "--base-url",
            args.base_url,
            "--cortex-model",
            args.cortex_model,
            "--muse-model",
            args.muse_model,
            "--max-tokens",
            str(args.max_tokens),
            "--muse-max-tokens",
            str(args.muse_max_tokens),
            "--cortex-temperature",
            str(args.cortex_temperature),
            "--muse-temperature",
            str(args.muse_temperature),
        ]
        if getattr(args, "trace_out", ""):
            # Every turn is a different process; they append to one file, in
            # turn order, because this arm is strictly serial.
            argv += ["--trace-out", args.trace_out]
    return argv


def spawn_turn(
    args: argparse.Namespace, show: dict[str, Any], *, record_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one turn in a REAL separate process; return its orders and record.

    The directive rides only on turn 0. Every later child is handed the board
    and nothing else, so if it still plays toward the objective, the pad and the
    store are the only places it can have come from.
    """
    state = show.get("state") or {}
    directive = args.directive if int(state.get("turn") or 0) == 0 else ""
    argv = turn_argv(args, record_path=record_path, directive=directive)
    # Fixed argv, no shell, interpreter taken from sys.executable.
    proc = subprocess.run(  # nosec B603
        argv,
        input=json.dumps(show),
        capture_output=True,
        text=True,
        timeout=args.turn_timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise LeagueError(
            f"seat turn failed (exit {proc.returncode}): {proc.stderr.strip()[-400:]}"
        )
    orders = _extract_json(proc.stdout)
    if orders is None:
        raise LeagueError(f"seat turn printed no orders JSON: {proc.stdout[:200]!r}")
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    return orders, record


# ── rendering ────────────────────────────────────────────────────────────────


def render_text(report: dict[str, Any]) -> None:
    """The human-readable report. Results only — diagnostics went to stderr."""
    arena = report["arena"]
    match = report["match"]
    mind = report["mind"]
    lines = [
        f"league seat — {arena['match_id']} ({arena['scenario']}, seed {arena['seed']})",
        f"arm: {report['arm']}  league driver_kinds: {match['driver_kinds']}",
        f"mind: cortex={mind['cortex']} muse={mind['muse'] or '(none)'} "
        f"identity={mind['identity'] or '(none)'}",
        f"store: {arena['store']}",
        f"pad: {arena['pad']}",
        "",
    ]
    for turn in report["turns"]:
        actions = ", ".join(
            f"{o['unit_id']}:{o['action']}{o.get('to', '')}"
            for o in turn["orders"].get("actions", [])
        )
        lines.append(f"  turn {turn['turn']} [pid {turn['pid']}] {actions or '(no orders)'}")
    lines.append("")
    lines.append(
        f"match: {match['status']} after {match['turns_played']} turn(s); "
        f"winner {match['winner'] or '(none)'}"
    )
    lines.append(f"replay sha256: {match['replay_sha256']}")
    lines.append(f"match log: {report['log']}")
    if report["degradations"]:
        lines.append(f"degradations: {len(report['degradations'])}")
    print("\n".join(lines))


# ── CLI ──────────────────────────────────────────────────────────────────────


def default_workdir() -> Path:
    """Where the arena store lands when the operator names no directory.

    Under the platform temp directory on purpose: a match must never write a
    ``.league/`` into the checkout it is being read from.
    """
    return Path(tempfile.gettempdir()) / "embodiment-league-seat"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--team", default=DEFAULT_TEAM, help="the team this seat commands")
    parser.add_argument(
        "--store",
        required=True,
        help=(
            "eidetic store for this match's memories (REQUIRED, no default: an "
            "unanchored public record would resolve against whatever git repo "
            "the host happens to sit in)"
        ),
    )
    parser.add_argument("--scope", default=SCOPE, help="eidetic scope for those memories")
    parser.add_argument(
        "--pad", default="", help="the scratchpad file (default: <workdir>/pad.jsonl)"
    )
    parser.add_argument(
        "--workdir", default=str(default_workdir()), help="arena store root and working directory"
    )
    parser.add_argument("--max-steps", type=int, default=16, help="model-turn budget per turn")
    parser.add_argument("--recall-top-k", type=int, default=RECALL_TOP_K)
    parser.add_argument(
        "--identity", default="", help="teammate identity (absent ⇒ prompts unchanged)"
    )
    parser.add_argument(
        "--muse", action="store_true", help="run a second, advisory mind beside the actor"
    )
    parser.add_argument("--muse-max-turns", type=int, default=2)
    parser.add_argument(
        "--coherence", action="store_true", help="assess coherence (dials an embedding endpoint)"
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--events", action="store_true", help="publish loop events onto the events-cli fabric"
    )
    parser.add_argument("--events-host", default=None)
    parser.add_argument("--events-port", type=int, default=None)
    live = parser.add_argument_group("live rig (opt-in; nothing here is a default)")
    live.add_argument(
        "--live",
        action="store_true",
        help=f"talk to a real OpenAI-compatible gateway; needs {API_KEY_ENV} in the environment",
    )
    live.add_argument("--base-url", default=DEFAULT_BASE_URL)
    live.add_argument("--cortex-model", default=CORTEX_MODEL)
    live.add_argument("--muse-model", default=MUSE_MODEL)
    live.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    live.add_argument("--muse-max-tokens", type=int, default=DEFAULT_MUSE_MAX_TOKENS)
    live.add_argument("--cortex-temperature", type=float, default=DEFAULT_TEMPERATURE)
    live.add_argument("--muse-temperature", type=float, default=DEFAULT_MUSE_TEMPERATURE)
    live.add_argument(
        "--trace-out",
        default="",
        help=(
            "append one JSON object per live completion here, carrying the "
            "finish_reason ModelResponse cannot (embodiment#37). Off by default; "
            "in the command arm every turn's child appends to the same file."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="league_seat",
        description=(
            "An embodiment-driven team playing league-of-agents through its public "
            "CLI. Software presence, not a robot body."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    match = sub.add_parser("play", help="play a whole match in one residency arm")
    _add_common(match)
    match.add_argument("--arm", choices=ARMS, default=ARM_RESIDENT)
    match.add_argument("--rival", default=DEFAULT_RIVAL)
    match.add_argument("--scenario", default=DEFAULT_SCENARIO)
    match.add_argument("--seed", type=int, default=DEFAULT_SEED)
    match.add_argument("--match-id", default="seat-match")
    match.add_argument("--max-turns", type=int, default=0, help="0 = play to the arena's limit")
    match.add_argument("--league-bin", default=DEFAULT_LEAGUE_BIN)
    match.add_argument("--league-timeout", type=float, default=300.0)
    match.add_argument("--turn-timeout", type=float, default=900.0)
    match.add_argument("--log", default="", help="match log (default: <workdir>/match-log.jsonl)")
    match.add_argument("--directive", default=DEFAULT_DIRECTIVE)
    match.add_argument("--reset", action="store_true", help="forget the arena, pad and store first")

    once = sub.add_parser("turn", help="one turn: board on stdin, orders JSON on stdout")
    _add_common(once)
    once.add_argument("--record", default="", help="write this turn's record here")
    once.add_argument("--directive", default="", help="the operator's standing objective, if any")
    return parser


def resolve_paths(args: argparse.Namespace) -> argparse.Namespace:
    """Fill the paths that hang off ``--workdir``, so every default is visible."""
    workdir = Path(args.workdir).expanduser()
    args.workdir = str(workdir)
    if not args.pad:
        args.pad = str(workdir / "pad.jsonl")
    # ``--log`` exists only on ``play``; ``getattr`` keeps ``turn`` from
    # growing an attribute it has no use for.
    if getattr(args, "log", None) == "":
        args.log = str(workdir / "match-log.jsonl")
    return args


def drive_aborted(report: dict[str, Any]) -> bool:
    """Did a seam break mid-run? The ONLY thing that makes a match a failure.

    Deliberately not keyed on the arena's status word. An earlier version
    returned non-zero unless the status was ``complete``/``active``, and the
    real arena says ``finished`` (``league/engine/state.py:22`` —
    ``pending``/``active``/``finished``), so a match that played perfectly for
    all thirty turns exited 1. Whether the seat's own drive survived is a fact
    this host owns; the arena's vocabulary is the arena's.
    """
    if (report.get("drive") or {}).get("aborted"):
        return True
    return any((turn.get("drive") or {}).get("aborted") for turn in report.get("turns") or [])


def build_observer(args: argparse.Namespace) -> Any:
    """The optional event observer. ``None`` unless the operator asked for it."""
    if not getattr(args, "events", False):
        return None
    from embodiment import EventEmitter

    return EventEmitter(host=args.events_host, port=args.events_port)


def main(argv: Optional[list[str]] = None) -> int:
    args = resolve_paths(build_parser().parse_args(argv))
    observer = build_observer(args)
    try:
        if args.command == "play":
            report = play(args, observer=observer)
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                render_text(report)
            return 1 if drive_aborted(report) else 0

        show = read_observation(sys.stdin.read())
        result = take_turn(args, show, observer=observer)
        if args.record:
            Path(args.record).parent.mkdir(parents=True, exist_ok=True)
            Path(args.record).write_text(
                json.dumps(result["record"], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        # ONLY the orders on stdout: league's own driver contract, and what this
        # host's parent parses back.
        print(json.dumps(result["orders"], ensure_ascii=False))
        return 0
    except (LeagueError, ValueError) as failure:
        # The arena refused, or nothing board-shaped arrived. An example should
        # say so in one line rather than spray a traceback at whoever ran it.
        print(f"error: {failure}", file=sys.stderr)
        print(
            "hint: check --league-bin points at an installed 'league', and that "
            "--workdir is writable",
            file=sys.stderr,
        )
        return 2
    finally:
        if observer is not None:
            observer.close()


if __name__ == "__main__":
    raise SystemExit(main())
