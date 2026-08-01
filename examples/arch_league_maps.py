#!/usr/bin/env python3
"""arch_league_maps — fog-scoped map images in the league lane, twin-enforced.

Plan task **t10** of `orchestrator-worker-architectures`
(`docs/plans/2026-07-31-orchestrator-worker-architectures.md`), covering claims
``c21``, ``c22``, ``c37`` and honesty conditions ``h15``, ``h16``, ``h29``.

It registers, not modifies
---------------------------
``examples/arch_league.py`` (t6) ships a ``PerceptionRoute`` registry precisely
so a sibling task can add a route without touching the file that owns the
seat, the round loop, or the routing log: ``register_route``/``route_for``,
and every ``Perception`` already carries a ``snapshot_hash`` field t6 never
fills. This module fills it. Nothing here edits ``arch_league.py`` or
``map_render.py``.

============================  ==============================================
what                          whose
============================  ==============================================
the seat, the round loop,     ``examples/arch_league.py`` (t6) — ``run_round``,
the routing log, the          ``play_match``, ``PerceptionRoute``,
perception seam                ``register_route``, ``Perception.snapshot_hash``
the renderer, the fog          ``examples/map_render.py`` (t9) — ``render_map``,
snapshot, the twin key         ``fog_snapshot``, ``snapshot_hash``,
                                ``write_turn_map``
the twin rule, the image       this module — ``TwinLedger``, the ``map_image``
route, the CLI wrapper that    route, ``TwinCommittingCli``, ``run_map_series``
commits ahead of the dial
============================  ==============================================

The twin rule (``c22``/``h16``), and how refusal is structural
----------------------------------------------------------------
An image cell must not carry more, less, or staler state than its text twin —
otherwise a measured separation between the two would be evidence of a leak,
not of vision. So before a mind is ever handed a map, the harness must already
hold a **committed** text rendering of the identical fog snapshot: same match,
same turn (``game_time``), same seat (``unit_id``), same ``snapshot_hash``.

The check lives inside the registered route's ``describe`` callback
(built by :func:`build_image_route`), which ``arch_league.unit_brief`` calls
**before** ``run_round`` ever reaches ``seams.build`` or drives a model turn.
A missing or mismatched twin raises (:class:`MissingTwinError` /
:class:`MismatchedTwinError`) out of that callback, which unwinds straight out
of ``run_round`` — no tool schema was built, no mind was dialled, and no call
landed in the log. That is what "structural" means here: the refusal is a
side effect of *where* the check runs, not a value a caller has to remember
to inspect.

Committing a twin is deliberately a **separate act** from rendering the image,
performed by an independent code path (``arch_league.describe_text``, t6's
own text route, called verbatim) rather than the image route re-deriving its
own agreement. :class:`TwinCommittingCli` performs that act for a real match:
it wraps ``league_commander.LeagueCli`` and, on every ``cmatch show``
response — the same response ``play_match`` is about to read the round's
briefings out of — commits a twin for every decision it carries, *before*
``play_match`` ever calls ``run_round``. :func:`run_map_series` wires that CLI
in and drives ``arch_league.play_match``/the series bookkeeping unmodified,
over a route registered only for the duration of the call (see
:func:`image_route_registered` below).

Registered only for the duration of the call — never left mutating the
shared registry
------------------------------------------------------------------------
``arch_league.ROUTE_REGISTRY`` is process-wide, shared state, and t6's own
test suite depends on that: ``test_exactly_one_route_ships_today_and_it_is_
the_text_one`` asserts it holds only the text route, and t6's own precedent
test for this exact seam (``test_a_registered_route_rides_every_routing_
record``) registers a stand-in route, uses it, and pops it in a ``finally``.
This module follows that same discipline via :func:`image_route_registered`,
a context manager: nothing here ever calls ``register_route`` and leaves it
registered past the block that needed it, whether the block exits normally or
by exception.

The v1 scoping rule (``c37``/``h29``) — team-scoped, not per-unit
---------------------------------------------------------------------
``map_render``'s own docstring is explicit: the fogged briefing carries
**no vision radii**, so a per-unit cone is not derivable here without
duplicating league's ``vision_mu`` stats into this repo — and the moment
league tuned one, the copy would desync into exactly the fog-leak defect
``h15`` forbids. So v1 renders the same **team-scoped** view for every mind,
matching the text briefing's own scoping exactly. **No vision-radius constant
exists anywhere in this file.** Per-unit cones wait on a league-side surface;
task ``t13`` drafts that proposal.

No live dial happens here
--------------------------
Fully hermetic: no live model call, no live arena binary. The gates are the
repo's existing ones (``aa.LIVE_GATE_ENV`` / ``al.LIVE_ARENA_ENV``), reused
rather than re-declared, and the scripted lane is the whole of what ships
this cycle — mirroring t6 exactly.

Usage::

    uv run python examples/arch_league_maps.py plan
    uv run python examples/arch_league_maps.py play --root /tmp/alm \\
        --league "python tests/fake_cleague.py"
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples import arch_arms as aa  # noqa: E402
from examples import arch_league as al  # noqa: E402
from examples import league_commander as lc  # noqa: E402
from examples import map_render as mr  # noqa: E402

__all__ = [
    "ROUTE_IMAGE",
    "TwinError",
    "MissingTwinError",
    "MismatchedTwinError",
    "TwinRecord",
    "TwinLedger",
    "TWIN_LEDGER",
    "IMAGE_ROUTE",
    "build_image_route",
    "image_route_registered",
    "commit_text_twin",
    "check_twin",
    "TwinCommittingCli",
    "run_map_series",
    "render_plan",
    "build_parser",
    "main",
]

# ── the route id ─────────────────────────────────────────────────────────────

#: This lane's perception route id, registered against t6's ``ROUTE_REGISTRY``
#: — only ever for the duration of a call, via :func:`image_route_registered`.
ROUTE_IMAGE = "map_image"

_IMAGE_ROUTE_WHY = (
    "a team-scoped fog map (c37/h29), rendered by map_render.render_map from "
    "exactly the same fogged briefing the text route describes; every dial is "
    "refused unless a committed text twin exists for the same match, turn and "
    "seat, keyed by map_render.snapshot_hash (c22/h16). No per-unit "
    "visibility-radius constant exists anywhere in this route."
)


# ── the twin rule ────────────────────────────────────────────────────────────


class TwinError(RuntimeError):
    """An image dial was attempted without a valid committed text twin."""


class MissingTwinError(TwinError):
    """No text twin was ever committed for this match/turn/seat."""


class MismatchedTwinError(TwinError):
    """A twin exists for this key, but it was derived from a different fog snapshot."""


#: (match_id, game_time, unit_id) — "same turn, same seat" from the acceptance
#: criterion, read straight off the briefing via ``map_render.read_board``.
TwinKey = tuple[str, int, str]


@dataclass(frozen=True)
class TwinRecord:
    """One committed text twin: what it described, and the hash it must match.

    ``briefing`` is kept (a defensive copy) so a caller can later persist the
    paired image via :func:`map_render.write_turn_map` from the exact same
    object the twin was derived from — never a re-fetch that could drift.
    """

    match_id: str
    game_time: int
    unit_id: str
    team_id: str
    snapshot_hash: str
    text: str
    briefing: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "twin",
            "route": ROUTE_IMAGE,
            "match_id": self.match_id,
            "game_time": self.game_time,
            "unit_id": self.unit_id,
            "team_id": self.team_id,
            "snapshot_hash": self.snapshot_hash,
            "text": self.text,
        }


def _twin_key(view: "mr.BoardView") -> TwinKey:
    return (view.match_id, view.game_time, view.you_unit)


class TwinLedger:
    """Committed text twins, keyed by (match, turn, seat). Thread-safe.

    ``commit`` is the only way a twin enters the ledger, and it always derives
    both the text and the hash from ``arch_league.describe_text`` and
    ``map_render.snapshot_hash`` — the same two functions any *other* caller
    would use, never a shortcut invented for this check. That is what makes
    the pairing genuine: an image dial's own hash and a twin's committed hash
    are computed by the same function, but the *record* they must agree with
    was produced by an independent call, at a different time, on a path the
    image route does not control.
    """

    def __init__(self) -> None:
        self._by_key: dict[TwinKey, TwinRecord] = {}
        #: Append-only, in commit order — the audit trail :func:`run_map_series`
        #: replays into the JSONL artifact.
        self.records: list[TwinRecord] = []
        self._lock = threading.Lock()

    def commit(self, briefing: Any, *, team_id: str = "") -> TwinRecord:
        view = mr.read_board(briefing, team_id)
        twin_text = al.describe_text(briefing, team_id).text
        snapshot = mr.snapshot_hash(briefing, team_id)
        record = TwinRecord(
            match_id=view.match_id,
            game_time=view.game_time,
            unit_id=view.you_unit,
            team_id=view.acting_team,
            snapshot_hash=snapshot,
            text=twin_text,
            briefing=dict(briefing) if isinstance(briefing, Mapping) else {},
        )
        with self._lock:
            self._by_key[_twin_key(view)] = record
            self.records.append(record)
        return record

    def get(self, briefing: Any, team_id: str = "") -> Optional[TwinRecord]:
        view = mr.read_board(briefing, team_id)
        with self._lock:
            return self._by_key.get(_twin_key(view))

    def check(self, briefing: Any, team_id: str = "") -> TwinRecord:
        """The pairing check itself. Raises; never returns a "warning" value."""
        view = mr.read_board(briefing, team_id)
        twin = self.get(briefing, team_id)
        if twin is None:
            raise MissingTwinError(
                f"no committed text twin for match={view.match_id!r} "
                f"turn={view.game_time} seat={view.you_unit!r}; commit one "
                "(TwinLedger.commit, or play through TwinCommittingCli) before "
                "the image route may be dialled for this decision point"
            )
        fresh = mr.snapshot_hash(briefing, team_id)
        if fresh != twin.snapshot_hash:
            raise MismatchedTwinError(
                f"the committed text twin for match={view.match_id!r} "
                f"turn={view.game_time} seat={view.you_unit!r} was derived from "
                f"a different fog snapshot ({twin.snapshot_hash[:12]} != "
                f"{fresh[:12]}); a stale or wrong twin must not back an image dial"
            )
        return twin

    def clear(self) -> None:
        with self._lock:
            self._by_key.clear()
            self.records.clear()


def build_image_route(ledger: TwinLedger, *, route_id: str = ROUTE_IMAGE) -> al.PerceptionRoute:
    """The image route: refuses via :meth:`TwinLedger.check`, then renders.

    The check runs first and unconditionally — nothing below it can execute on
    a missing or mismatched twin, which is what keeps the refusal structural
    rather than advisory. The returned object is a plain
    :class:`arch_league.PerceptionRoute`; it is not registered anywhere by this
    call — see :func:`image_route_registered`.
    """

    def describe(briefing: Any, team_id: str = "") -> al.Perception:
        twin = ledger.check(briefing, team_id)
        render = mr.render_map(briefing, team_id=team_id or twin.team_id)
        caption = (
            f"[FOG MAP IMAGE - team-scoped view, twin snapshot "
            f"{render.snapshot_hash[:12]}]\n{twin.text}"
        )
        return al.Perception(
            route=route_id,
            text=caption,
            parts=(render.png,),
            snapshot_hash=render.snapshot_hash,
        )

    return al.PerceptionRoute(id=route_id, why=_IMAGE_ROUTE_WHY, describe=describe)


#: A convenience default ledger for callers that don't need registry isolation
#: (e.g. exercising :func:`build_image_route`'s ``describe`` directly). It is
#: private to this module and never touches ``arch_league.ROUTE_REGISTRY`` on
#: its own — building a route object does not register it.
TWIN_LEDGER = TwinLedger()

#: A pre-built route bound to :data:`TWIN_LEDGER`, for the same convenience.
IMAGE_ROUTE = build_image_route(TWIN_LEDGER)


@contextlib.contextmanager
def image_route_registered(
    ledger: Optional[TwinLedger] = None, *, route_id: str = ROUTE_IMAGE
) -> Iterator[TwinLedger]:
    """Register the image route for the duration of a block, then remove it.

    ``arch_league.ROUTE_REGISTRY`` is shared, process-wide state. This is the
    register-then-pop discipline t6's own precedent test demonstrates for this
    exact seam — reused here so every caller (the CLI, :func:`run_map_series`,
    and this module's own tests) leaves the registry exactly as it found it,
    whether the block exits normally or by exception.
    """
    active_ledger = ledger if ledger is not None else TwinLedger()
    route = build_image_route(active_ledger, route_id=route_id)
    al.register_route(route)
    try:
        yield active_ledger
    finally:
        al.ROUTE_REGISTRY.pop(route_id, None)


def commit_text_twin(briefing: Any, *, team_id: str = "") -> TwinRecord:
    """Commit one briefing's text twin to :data:`TWIN_LEDGER`."""
    return TWIN_LEDGER.commit(briefing, team_id=team_id)


def check_twin(briefing: Any, *, team_id: str = "") -> TwinRecord:
    """Run the pairing check against :data:`TWIN_LEDGER` without rendering anything."""
    return TWIN_LEDGER.check(briefing, team_id=team_id)


# ── the CLI-level seam: commit twins as a side effect of reading the arena ────


@dataclass
class TwinCommittingCli(lc.LeagueCli):
    """``league_commander.LeagueCli``, wrapped to commit a twin per ``cmatch show``.

    ``play_match`` (t6, unmodified) fetches a round's briefings straight out of
    a ``cmatch show`` response and hands them to ``run_round`` unchanged. This
    wrapper intercepts that same response — after the arena answered, before
    ``play_match`` reads it — and commits a twin for every decision it carries.
    By the time ``run_round`` asks the ``map_image`` route to describe a unit,
    the matching twin already exists, committed from the identical briefing
    object ``run_round`` is about to see. This is what makes the pairing
    genuine rather than a hash comparing itself: the commit happens on a call
    this wrapper does not control the content of, strictly before the describe
    call it gates.
    """

    ledger: TwinLedger = field(default_factory=TwinLedger)

    def __call__(self, *args: str) -> dict[str, Any]:
        payload = super().__call__(*args)
        if tuple(args[:2]) == ("cmatch", "show"):
            for entry in payload.get("decisions") or []:
                briefing = entry.get("briefing") if isinstance(entry, Mapping) else None
                if isinstance(briefing, Mapping) and briefing:
                    self.ledger.commit(briefing)
        return payload


# ── the lane: t6's play_match/run_series, replayed over the image route ──────


def run_map_series(
    *,
    config: aa.ArchConfig,
    arena: str,
    root: Any,
    log: aa.CallLog,
    rung: Optional[al.LeagueRung] = None,
    arms: Sequence[str] = aa.ARM_ORDER,
    matches: int = 1,
    out: Optional[Path] = None,
    identity: Optional[str] = None,
    seams_factory: Optional[Callable[[aa.Arm, aa.CallLog], Any]] = None,
    max_rounds: int = al.DEFAULT_MAX_ROUNDS,
    image_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """``arch_league.run_series``, driven over ``map_image`` with twins enforced.

    Every function this calls is t5's or t6's, unmodified: only the CLI (which
    now commits twins as it reads the arena) and the route (registered only
    for this call, via :func:`image_route_registered`) differ from a plain
    ``arch_league.run_series`` call. Set ``image_dir`` to also persist every
    committed twin's paired PNG via :func:`map_render.write_turn_map` — using
    the exact briefing the twin was committed from, never a re-fetch.
    """
    chosen = rung or al.LEAGUE_LADDER[0]
    senses_hash = aa.assert_senses_identical(config)
    workdir = Path(root)
    workdir.mkdir(parents=True, exist_ok=True)
    ledger = TwinLedger()
    cli = TwinCommittingCli(root=workdir, binary=arena, ledger=ledger)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        log.subscribe(lambda record: al.append_jsonl(out, record.to_dict()))

    cells: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    live = False

    with image_route_registered(ledger):
        first = True
        for arm_id in arms:
            arm = aa.ARMS[arm_id]
            seams = (
                seams_factory(arm, log)
                if seams_factory is not None
                else al.scripted_seams(arm, config=config, log=log)
            )
            if first:
                live = bool(getattr(seams, "live", False))
                preamble = al.preamble(
                    config,
                    senses_hash=senses_hash,
                    live=live,
                    rung=chosen,
                    arms=arms,
                    route=ROUTE_IMAGE,
                    arena=arena,
                )
                preamble["task"] = "t10"
                preamble["lane"] = "league-maps"
                preamble["twin_rule"] = _IMAGE_ROUTE_WHY
                al.append_jsonl(out, preamble)
                first = False
            cell = aa.CellResult(
                arm=arm_id, rung=chosen.id, route=ROUTE_IMAGE, senses_config_hash=senses_hash
            )
            for index in range(max(1, matches)):
                seed = chosen.seeds[index % len(chosen.seeds)]
                before = len(ledger.records)
                match_record, attempt = al.play_match(
                    cli=cli,
                    arm=arm,
                    rung=chosen,
                    match_index=index,
                    seed=seed,
                    seams=seams,
                    config=config,
                    senses_hash=senses_hash,
                    route=ROUTE_IMAGE,
                    identity=identity,
                    out=out,
                    max_rounds=max_rounds,
                )
                for twin in ledger.records[before:]:
                    al.append_jsonl(out, twin.to_dict())
                    if image_dir is not None:
                        mr.write_turn_map(
                            twin.briefing,
                            image_dir,
                            match_id=twin.match_id,
                            turn=twin.game_time,
                            seat=twin.unit_id,
                            team_id=twin.team_id,
                        )
                cell.attempts.append(attempt)
                matched.append(match_record.to_dict())
            payload = cell.to_dict()
            cells.append(payload)
            al.append_jsonl(out, payload)

    return {
        "kind": "series",
        "lane": "league-maps",
        "live": live,
        "rung": chosen.id,
        "route": ROUTE_IMAGE,
        "arms": list(arms),
        "senses_config_hash": senses_hash,
        "cells": cells,
        "matches": matched,
        "twins_committed": len(ledger.records),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def render_plan() -> str:
    lines = [
        "arch_league_maps -- fog-scoped map images in the league lane, twin-enforced",
        "",
        f"route: {ROUTE_IMAGE}",
        f"  {_IMAGE_ROUTE_WHY}",
        "",
        "twin rule (c22/h16): an image dial is refused unless a text twin was",
        "committed for the same match, turn and seat, and its snapshot_hash",
        "matches map_render.snapshot_hash of the briefing being dialled.",
        "",
        "scoping (c37/h29): team-scoped only in v1 -- no per-unit visibility-radius",
        "constant exists anywhere in this module; per-unit cones wait on a",
        "league-side surface (t13's proposal).",
        "",
        f"gates: {aa.LIVE_GATE_ENV} (models), {al.LIVE_ARENA_ENV} (the real arena)",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--config", default=None, help="path to the sampling table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="the route and the twin rule")
    plan.add_argument("--json", action="store_true")

    play = subparsers.add_parser("play", help="play the rung over map_image (scripted only)")
    play.add_argument("--root", default=None, help="where the arena keeps its store")
    play.add_argument("--league", default=None, help="the arena binary or a stand-in")
    play.add_argument("--arm", action="append", default=None, help="restrict to these arms")
    play.add_argument("--matches", type=int, default=1)
    play.add_argument("--rung", default=None)
    play.add_argument("--out", default=None, help="write the JSONL artifact here")
    play.add_argument("--image-dir", default=None, help="also commit PNGs here")
    play.add_argument("--config", default=None)
    return parser


def _fail(message: str, hint: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    return 2


def _plan_payload() -> dict[str, Any]:
    return {
        "lane": "league-maps",
        "route": ROUTE_IMAGE,
        "why": _IMAGE_ROUTE_WHY,
        "twin_rule": "c22/h16: refused without a matching committed text twin",
        "scoping": (
            "c37/h29: team-scoped only; no per-unit visibility-radius constant " "in this module"
        ),
        "gates": {"models": aa.LIVE_GATE_ENV, "arena": al.LIVE_ARENA_ENV},
    }


def _play(args: argparse.Namespace, config: aa.ArchConfig) -> int:
    if args.rung and args.rung not in al.LADDER_BY_ID:
        return _fail(f"unknown rung {args.rung!r}", f"known rungs: {', '.join(al.LADDER_BY_ID)}")
    rung = al.LADDER_BY_ID[args.rung] if args.rung else al.LEAGUE_LADDER[0]

    arms = tuple(args.arm) if args.arm else aa.ARM_ORDER
    unknown = [arm for arm in arms if arm not in aa.ARMS]
    if unknown:
        return _fail(f"unknown arm(s) {unknown}", f"known arms: {', '.join(aa.ARM_ORDER)}")

    try:
        arena = al.resolve_arena(args.league)
    except al.LiveArenaClosed as shut:
        return _fail(
            str(shut),
            f"export {al.LIVE_ARENA_ENV}=1 to play the real arena, or pass --league "
            "with a stand-in binary",
        )

    root = Path(args.root) if args.root else Path(tempfile.mkdtemp(prefix="arch-league-maps-"))
    try:
        report = run_map_series(
            config=config,
            arena=arena,
            root=root,
            log=al.ThreadSafeCallLog(),
            rung=rung,
            arms=arms,
            matches=max(1, int(args.matches)),
            out=Path(args.out) if args.out else None,
            image_dir=Path(args.image_dir) if args.image_dir else None,
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

    try:
        config = aa.load_config(config_path)
    except aa.ConfigError as broken:
        return _fail(
            str(broken), f"check the sampling table at {config_path or aa.DEFAULT_CONFIG_PATH}"
        )

    return _play(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
