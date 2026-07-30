#!/usr/bin/env python3
"""league_h2h — three model arms, round-robin, up an escalating arena ladder.

Contract: ``docs/live-test-results/league-h2h-preregistration.md``, committed in
the same change as this file and **before the first measured dial**. This module
is plan task **t27**'s harness; it executes a pre-registered protocol and does
not re-open one.

The operator's question
-----------------------
*Should I switch models, or consolidate to one?* Every live experiment in this
cycle used the **mixed** pairing — Qwen 3.6 27B as cortex, Gemma 4 31B as muse.
Nothing measured whether one model alone does as well. Three arms, differing
only in which model serves which cognitive role:

=============  ===========================  ===========================
arm            cortex                       muse
=============  ===========================  ===========================
``full-gemma`` ``Gemma-4-31B``              ``Gemma-4-31B``
``mixed``      ``Qwen3.6-27B`` *(control)*  ``Gemma-4-31B``
``full-qwen``  ``Qwen3.6-27B``              ``Qwen3.6-27B``
=============  ===========================  ===========================

An arm is a **flag pair**, never a code branch: :data:`ARMS` is data, and
:func:`seat_turn` takes the two model ids as arguments. Senses stays Gemma 4 12B
in every arm — and this seat has **no senses lane at all**, so that constant is
recorded (:data:`SENSES_MODEL`) rather than dialled. Stated plainly because a
constant nobody calls cannot confound anything, and pretending otherwise would
be padding.

Round-robin, which is the hard part (deviation ``d10``)
-------------------------------------------------------
``examples/league_seat.py`` drives the rival with :func:`~examples.league_seat.
rival_orders` — a deterministic scripted policy, **not a model**. Round-robin
needs **both** teams to be model seats, so this module runs the seat logic twice
per league turn, once per team, with different model configs. league's
``match act <id> --team T`` seam is symmetric, which is what makes that possible
without touching the arena.

``league_seat.py`` is **imported, never edited** — task t24 owns it. Everything
reusable comes from there: :class:`~examples.league_seat.LeagueCli`,
:class:`~examples.league_seat.Seat` (the note/order/submit tool surface),
:data:`~examples.league_seat.TOOL_SCHEMA`,
:func:`~examples.league_seat.observation_view`,
:func:`~examples.league_seat.build_task`,
:func:`~examples.league_seat.parse_completion`, and the continuity plumbing.

The ladder, and what "+5 in size" became
-----------------------------------------
The operator asked for boards "+5 in size". **Board size is not a knob.**
``league arena list`` ships exactly three fixed scenarios and no grid parameter
exists on ``match new``. What was substituted, in rising order, is
:data:`LADDER` — the scenario ladder, an engine-enforced ``--max-actions``
handicap, and a genuinely fogged observation (league's own
``match brief --team T``, which reports only what a team has *seen*).

Cost is a result, not overhead
-------------------------------
:class:`MeteredSeam` is the only transport, and it records completion tokens,
prompt tokens, wall clock, ``finish_reason``, **truncations** and transport
retries for **every** call, cortex and muse alike.

Equal token budgets are not equal useful output — the Qwen cortex reasons at
length before it emits anything, Gemma answers directly. So the cap is set
**sufficient rather than merely equal** (:data:`MAX_TOKENS`): high enough that
neither model truncates, with cost measured by *tokens actually consumed*
rather than by the cap. A truncated turn is an instrument event, is never
scored as a reasoning failure, and disqualifies its match from the decision
(:func:`run_rung`) rather than losing it.

Software presence, not a robot body. The seat commands units on a grid in
someone else's simulation; ``reachy-mini-cli`` owns the physical robot in this
mesh (C2).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment import (  # noqa: E402
    LoopAborted,
    LoopControls,
    ModelResponse,
    MuseControls,
    PresenceEngine,
    PresenceIO,
    Scratchpad,
    ThreadedMuseRunner,
    build_continuity_fn,
    frame_muse,
    run,
)
from examples.league_seat import (  # noqa: E402
    API_KEY_ENV,
    BASE_SYSTEM,
    DEFAULT_BASE_URL,
    TOOL_SCHEMA,
    LeagueCli,
    LeagueError,
    Seat,
    append_jsonl,
    build_task,
    fold_degradations,
    lifecycle_config,
    living_units,
    make_scripted_cortex,
    muse_snapshot,
    observation_view,
    parse_completion,
    recall_prior,
    replay_digest,
    scripted_muse,
)

# ── the three arms ───────────────────────────────────────────────────────────

#: The reference rig's two candidate minds, addressed BY NAME. Nothing in this
#: module infers a role from a model id.
GEMMA_31B = "nvidia/Gemma-4-31B-IT-NVFP4"
QWEN_27B = "sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP"

#: Constant in all three arms, and **not dialled by this harness** — the league
#: seat has no intake/speakback lane. Recorded so the configuration is complete,
#: not so a reader thinks it was measured.
SENSES_MODEL = "coolthor/gemma-4-12B-it-NVFP4A16"

ARM_FULL_GEMMA = "full-gemma"
ARM_MIXED = "mixed"
ARM_FULL_QWEN = "full-qwen"


@dataclass(frozen=True)
class Arm:
    """One arm: a name and the two model ids it puts on the wire."""

    id: str
    cortex: str
    muse: str


ARMS: dict[str, Arm] = {
    ARM_FULL_GEMMA: Arm(ARM_FULL_GEMMA, GEMMA_31B, GEMMA_31B),
    ARM_MIXED: Arm(ARM_MIXED, QWEN_27B, GEMMA_31B),
    ARM_FULL_QWEN: Arm(ARM_FULL_QWEN, QWEN_27B, QWEN_27B),
}

#: The arm that ships today. Named so the write-up cannot lose which one is the
#: control.
CONTROL_ARM = ARM_MIXED

#: Round-robin: every arm meets every other arm exactly once per rung.
PAIRINGS: tuple[tuple[str, str, str], ...] = (
    ("gemma-vs-mixed", ARM_FULL_GEMMA, ARM_MIXED),
    ("mixed-vs-qwen", ARM_MIXED, ARM_FULL_QWEN),
    ("qwen-vs-gemma", ARM_FULL_QWEN, ARM_FULL_GEMMA),
)

#: Both arms play both colours at every rung. Without this, map or seat
#: asymmetry confounds the entire result — league's two home corners are not
#: equidistant from every control point in every scenario.
COLOURS = ("blue", "red")

# ── budgets: SUFFICIENT, not merely equal ────────────────────────────────────

#: The model-turn budget for ONE seat-turn. note + one order per unit + submit
#: is five turns on a three-unit roster, six on recon-1's four-unit roster, so
#: eight leaves room for exactly one self-correcting step.
MAX_STEPS = 8

#: **The fairness definition, chosen deliberately and stated out loud.**
#:
#: There are two defensible ways to make two seats fair on tokens:
#:
#: 1. *Equal budget* — the same cap for both. Fair in **cost** terms, and it
#:    silently truncates the hungrier model.
#: 2. *Sufficient budget* — a cap high enough that **neither** model truncates.
#:    Fair in **capability** terms.
#:
#: This experiment uses **(2)**, and gets (1) for free by reporting *tokens
#: actually consumed* as the cost column: Gemma simply will not spend the
#: headroom. Capability is then measured at quality and cost is measured by
#: real consumption rather than by a cap.
#:
#: The number is 16000 rather than the 3000 this task was first briefed with,
#: and the correction arrived **before the first dial** (recorded as an
#: amendment in the pre-registration). The measured evidence, from two sibling
#: tasks on this same rig:
#:
#: * at ``max_tokens=6000`` on a hard problem the Qwen cortex returns
#:   ``finish_reason=length`` with **empty content and ~12,857 characters of
#:   reasoning** — it never reaches an answer;
#: * at ``max_tokens=16000`` on the same problem it answers correctly;
#: * ``designed-problem.md`` read an ``exit=stopped`` as the model abandoning
#:   the protocol. It was truncation. That reading has since been corrected.
#:
#: This is the confound most likely to silently invalidate a head-to-head: an
#: identical cap that truncates one arm measures **truncation, not skill**.
MAX_TOKENS = 16000

#: The muse is a model turn too, and in the ``full-qwen`` arm it is the same
#: thinking model. Capping it short would truncate counsel and degrade one arm
#: for an instrument reason, so it gets the same sufficient budget.
MUSE_MAX_TOKENS = 16000

#: Recorded headroom. If :data:`MAX_TOKENS` still truncates, the answer is to
#: raise it and say so in the write-up — never to score the truncated turn.
TOKEN_CEILING_AVAILABLE = 32000

CORTEX_TEMPERATURE = 0.3
MUSE_TEMPERATURE = 0.7
MUSE_MAX_TURNS = 2

#: league's own word for a completion that ran out of budget mid-thought. A
#: turn that ends this way is an **instrument event**, never a reasoning
#: failure and never a lost match — see :func:`run_rung`.
FINISH_TRUNCATED = "length"

#: How many league turns each match plays. Three, and it is stated as short:
#: no roster in any of the three scenarios can cross to a control point from
#: its home corner in three turns, so ``outcome.total`` is expected to tie at
#: zero and the pre-registered tie-breaks are expected to carry the result.
MATCH_TURNS = 3

RECALL_TOP_K = 4

# ── transport ────────────────────────────────────────────────────────────────

#: A timeout or a connection error is CONTENTION on a shared rig, not a result.
#: Retried, bounded, and counted in the artifact — never silently.
MAX_TRANSPORT_RETRIES = 3
RETRY_SLEEP_SECONDS = 20.0
#: Generous on purpose: a 16000-token thinking turn on a busy local GPU is slow,
#: and a timeout here would be contention masquerading as a result.
REQUEST_TIMEOUT = 1800.0

# ── the ladder ───────────────────────────────────────────────────────────────

#: Rosters, per scenario, in league's own role vocabulary. ``league arena show``
#: fixes which roles a scenario fields; a roster naming a role the scenario does
#: not know still registers, but the unit it produces cannot do anything.
ROSTER_SKIRMISH = ("scout", "harvester", "defender")
ROSTER_RECON = ("explorer", "planner", "harvester", "defender")


@dataclass(frozen=True)
class Rung:
    """One rung of the escalating ladder. Every field lands in the artifact."""

    id: str
    scenario: str
    seed: int
    roster: tuple[str, ...]
    turns: int = MATCH_TURNS
    #: ``--max-actions TEAM:N`` — engine-enforced, applied to BOTH teams.
    max_actions: Optional[int] = None
    #: Observation source: ``False`` is ``match show`` (omniscient), ``True`` is
    #: ``match brief --team T`` (only what this team has seen).
    fogged: bool = False
    #: league's declared fairness metadata, set on both teams when fogged.
    map_read: Optional[str] = None
    unit_comms: Optional[str] = None
    #: The control point the operator directive names. Both teams get the same
    #: directive — a symmetric objective, not an asymmetric one.
    objective: str = ""
    why: str = ""


LADDER: tuple[Rung, ...] = (
    Rung(
        id="L1",
        scenario="skirmish-1",
        seed=4242,
        roster=ROSTER_SKIRMISH,
        objective="cp-west",
        why="12x10, turn limit 30, three units, full board view, no handicap",
    ),
    Rung(
        id="L2",
        scenario="recon-1",
        seed=4243,
        roster=ROSTER_RECON,
        objective="cp-alpha",
        why=(
            "14x12, turn limit 20, four units of which the explorer and the "
            "planner can neither gather nor capture — role reasoning is "
            "engine-enforced, not prompt convention"
        ),
    ),
    Rung(
        id="L3",
        scenario="skirmish-2",
        seed=4244,
        roster=ROSTER_SKIRMISH,
        max_actions=2,
        objective="cp-relay",
        why=(
            "14x12 under a turn limit of 16, and only two of three units may "
            "be ordered per turn — the seat has to choose"
        ),
    ),
    Rung(
        id="L4",
        scenario="skirmish-2",
        seed=4245,
        roster=ROSTER_SKIRMISH,
        max_actions=2,
        fogged=True,
        map_read="fog",
        unit_comms="off",
        objective="cp-relay",
        why=(
            "the L3 board, seen only through league's own fogged brief: at "
            "turn 0 a team knows fifteen cells, its own units, and no control "
            "point at all"
        ),
    ),
)

LADDER_BY_ID = {rung.id: rung for rung in LADDER}

# ── the decision rule, fixed before the first dial ───────────────────────────

RESULT_WIN = "WIN"
RESULT_LOSS = "LOSS"
RESULT_DRAW = "DRAW"

#: How a single match is decided, in strict priority order. Applied without
#: amendment to whatever comes back.
TIE_BREAKS = ("outcome_total", "cooperation_v1", "fewer_rejections_and_cap_violations")

#: A pairing SEPARATES only when one arm wins BOTH colour assignments. One win
#: and one loss is a split, and a split is not a separation.
MATCHES_PER_PAIRING = len(COLOURS)

#: A rung separates when at least this many of the three pairings separated.
MIN_SEPARATED_PAIRINGS = 2

VERDICT_SEPARATED = "SEPARATED"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_ABSENT = "ABSENT"
VERDICTS = (VERDICT_SEPARATED, VERDICT_INCONCLUSIVE, VERDICT_ABSENT)

#: Wall-clock caps. A rung that would run past its cap stops, and every rung
#: above it is reported ABSENT rather than implied.
#:
#: Sized for the amended 16000-token budget rather than the 3000 this task was
#: first briefed with (pre-registration amendment 2, made before the first
#: dial). A thinking cortex given five times the headroom takes correspondingly
#: longer per turn, and the instruction that came with the budget correction was
#: explicit: size the ladder to the budget and report unrun rungs ABSENT, never
#: shrink the budget to fit the clock.
RUNG_CAP_SECONDS = 10800.0
LADDER_CAP_SECONDS = 28800.0

# ── prompts, identical for both seats in a match ─────────────────────────────

H2H_FRAMING = (
    "You are playing against a LIVE opposing team commanded by another mind, "
    "not a scripted policy. Both teams stage orders for the same turn and the "
    "turn resolves simultaneously, so plan for an opponent who is also moving."
)

CAP_FRAMING = (
    "HANDICAP: this match caps you at {cap} unit action(s) per turn. Ordering "
    "more than {cap} units wastes the surplus — choose which units act."
)

FOG_FRAMING = (
    "FOG: the board you are shown is only what your team has SEEN. Control "
    "points, resource nodes and enemy units you have not yet laid eyes on are "
    "absent from it — absent is not the same as not there. Scouting is a "
    "legitimate use of a turn."
)


def system_prompt(rung: Rung) -> str:
    """The system text both seats receive. One function, no arm argument.

    Identical for the two seats in any one match, which is what makes the
    comparison a comparison. It varies BETWEEN rungs, and every variation is a
    property of the rung recorded in the artifact.
    """
    parts = [BASE_SYSTEM, H2H_FRAMING]
    if rung.max_actions is not None:
        parts.append(CAP_FRAMING.format(cap=rung.max_actions))
    if rung.fogged:
        parts.append(FOG_FRAMING)
    return "\n\n".join(parts)


def directive_for(rung: Rung) -> str:
    """The operator's standing objective. The SAME text for both teams."""
    return (
        f"Operator directive: take {rung.objective} and hold it, and deliver "
        f"resources to the mission point."
    )


# ── the league seam, extended (never edited) ─────────────────────────────────


class H2HLeagueCli(LeagueCli):
    """:class:`~examples.league_seat.LeagueCli` plus the verbs this rung needs.

    Two additions, both of them public league CLI surface:

    * ``match new`` grows ``--max-actions``/``--map-read``/``--unit-comms``,
      the handicap and fairness knobs the ladder escalates with.
    * ``match brief --team T --json`` is league's own **fogged** view — what a
      team has seen, rather than the omniscient ``match show``.

    Subclassed rather than patched: ``league_seat.py`` belongs to task t24 and
    is imported verbatim.
    """

    def new_match_h2h(
        self,
        *,
        match_id: str,
        scenario: str,
        teams: list[str],
        seed: int,
        drivers: dict[str, str],
        max_actions: Optional[int] = None,
        map_read: Optional[str] = None,
        unit_comms: Optional[str] = None,
    ) -> dict[str, Any]:
        args = ["match", "new", "--scenario", scenario, "--seed", str(seed), "--id", match_id]
        for team in teams:
            args += ["--team", team]
        for team, kind in sorted(drivers.items()):
            args += ["--driver", f"{team}:{kind}"]
        for team in teams:
            if max_actions is not None:
                args += ["--max-actions", f"{team}:{max_actions}"]
            if map_read:
                args += ["--map-read", f"{team}:{map_read}"]
            if unit_comms:
                args += ["--unit-comms", f"{team}:{unit_comms}"]
        return self._json([*args, "--apply"])

    def brief(self, match_id: str, team_id: str) -> dict[str, Any]:
        return self._json(["match", "brief", match_id, "--team", team_id])

    def score_v1(self, match_id: str) -> dict[str, Any]:
        """league's own score, on the **content-aware** cooperation metric.

        ``v1`` grades plan fidelity and message utility against what the team
        actually did; ``v0`` grades cadence only. The pre-registered tie-break
        names ``v1``, so a run that could not get it must say so rather than
        quietly grade on a different instrument: the version actually used is
        stamped into the payload and one notice goes to stderr (C3 — nothing
        degrades silently).
        """
        try:
            payload = self._json(["match", "score", match_id, "--cooperation-version", "v1"])
            payload["cooperation_version_used"] = "v1"
            return payload
        except LeagueError as failure:
            print(
                f"notice: this arena has no --cooperation-version; scoring on v0 ({failure})",
                file=sys.stderr,
            )
            payload = self._json(["match", "score", match_id])
            payload["cooperation_version_used"] = "v0-fallback"
            return payload


# ── the observation, fogged or not (pure) ────────────────────────────────────


def fogged_view(brief: dict[str, Any], show: dict[str, Any], team_id: str) -> dict[str, Any]:
    """Shape league's fogged ``match brief`` into the seat's observation view.

    Two deliberate decisions, both pre-registered and both applied identically
    to the two seats:

    1. **Own-unit legality is not privileged information.** ``match brief``
       carries no ``legal_actions``, and a seat that cannot tell which squares
       its own units may step to is being tested on guessing league's movement
       rules rather than on playing. So ``legal_actions`` for this team's OWN
       units is carried over from ``match show``, and nothing else is.
    2. **Everything else comes from the brief only.** Control points, resource
       nodes and enemy units the team has not seen are simply absent — which is
       what makes this rung hard.
    """
    my_ids = {str(u.get("id")) for u in living_units(show, team_id)}
    known = [u for u in brief.get("known_units") or [] if u.get("team") == team_id]
    legal = {k: v for k, v in (show.get("legal_actions") or {}).items() if k in my_ids}
    by_id = {str(u.get("id")): u for u in living_units(show, team_id)}
    return {
        "state": {
            "match_id": brief.get("match_id"),
            "scenario_id": brief.get("scenario"),
            "turn": brief.get("turn"),
            "turn_limit": brief.get("turn_limit"),
            "status": brief.get("status"),
            "winner": brief.get("winner"),
        },
        "team": team_id,
        "fog": True,
        "cells_seen": brief.get("cells_seen"),
        "my_units": [
            {
                "id": str(u.get("unit")),
                "role": u.get("role"),
                "pos": list(u.get("pos") or []),
                "carrying": by_id.get(str(u.get("unit")), {}).get("carrying", 0),
            }
            for u in known
        ],
        "control_points": [
            {"id": c.get("id"), "pos": list(c.get("pos") or []), "owner": c.get("owner")}
            for c in brief.get("known_control_points") or []
        ],
        "resource_nodes": [
            {"id": r.get("id"), "pos": list(r.get("pos") or [])}
            for r in brief.get("known_resource_nodes") or []
        ],
        "legal_actions": legal,
        "rejections": show.get("last_turn_rejections") or [],
    }


def build_view(
    cli: H2HLeagueCli, match_id: str, show: dict[str, Any], team_id: str, *, fogged: bool
) -> dict[str, Any]:
    """What this seat is shown this turn."""
    if not fogged:
        return observation_view(show, team_id)
    return fogged_view(cli.brief(match_id, team_id), show, team_id)


def apply_cap(orders: dict[str, Any], cap: Optional[int]) -> tuple[dict[str, Any], int]:
    """Enforce ``--max-actions`` on the host side, and COUNT the overrun.

    league refuses a whole ``match act`` that declares more actions than the
    cap. Passing that refusal through would forfeit the turn, and a forfeited
    turn is a floor: both arms would score nothing and the rung would resolve
    nothing. So the surplus is **truncated to the first N staged** — the seat's
    own priority order, not the harness's — and the number dropped is recorded
    as ``cap_dropped``. The truncation is the harness's, identical for both
    seats, and the overrun itself is a reported competence signal rather than
    a hidden repair.
    """
    actions = list(orders.get("actions") or [])
    if cap is None or len(actions) <= cap:
        return orders, 0
    trimmed = dict(orders)
    trimmed["actions"] = actions[:cap]
    return trimmed, len(actions) - cap


# ── the metered transport ────────────────────────────────────────────────────


#: How much of one model turn's own words is kept verbatim in the artifact.
#: The *input* is not stored per call — it is reconstructible from the board,
#: the pad and the prompts, all of which are recorded — but what each model
#: actually SAID is the transcript, and a verdict with no transcript behind it
#: is an assertion.
TRANSCRIPT_CONTENT_CHARS = 4000
TRANSCRIPT_REASONING_CHARS = 2000


def _clip(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


@dataclass
class Meter:
    """What one role's calls cost. Cost is a result, so it is first-class."""

    role: str
    model: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    retries: int = 0
    failures: int = 0
    empty_content: int = 0
    #: Turns that ran out of token budget mid-thought. An INSTRUMENT event.
    truncated: int = 0
    finish_reasons: dict[str, int] = field(default_factory=dict)
    #: One entry per completed model turn — the raw transcript.
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def record_turn(
        self,
        reply: ModelResponse,
        *,
        finish_reason: str,
        seconds: float,
        messages: int,
    ) -> None:
        content, content_clipped = _clip(reply.content or "", TRANSCRIPT_CONTENT_CHARS)
        reasoning, reasoning_clipped = _clip(reply.reasoning or "", TRANSCRIPT_REASONING_CHARS)
        self.transcript.append(
            {
                "role": self.role,
                "model": self.model,
                "messages_in": messages,
                "finish_reason": finish_reason,
                "seconds": round(seconds, 3),
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
                "content": content,
                "content_clipped": content_clipped,
                "reasoning": reasoning,
                "reasoning_clipped": reasoning_clipped,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments} for call in reply.tool_calls
                ],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model": self.model,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 3),
            "retries": self.retries,
            "failures": self.failures,
            "empty_content": self.empty_content,
            "truncated": self.truncated,
            "finish_reasons": dict(self.finish_reasons),
        }


class MeteredSeam:
    """One OpenAI-compatible round trip per model turn, fully accounted.

    Mirrors :func:`examples.league_seat.gateway_seam`'s body exactly — same
    endpoint, same four keys, ``tools`` present only when the caller passes a
    schema (the muse lane passes none, and that absence is the whole of
    "tools-off"). It exists beside that function rather than instead of it
    because three things this experiment reports are not visible through it:

    * ``finish_reason`` — the Qwen truncation trap. A budget-exhausted turn
      returns empty content and reads exactly like a model with nothing to say.
    * wall clock and token spend per role, which is half the answer here.
    * transport retries. A timeout on a shared rig is contention, not a result;
      it is retried, bounded, and **counted**. Nothing is ever retried for a
      better answer — only for a completed HTTP round trip.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        role: str,
        max_tokens: int,
        temperature: float,
        tools: Optional[list[dict[str, Any]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        if not self.endpoint.startswith(("http://", "https://")):
            raise SystemExit(f"error: --base-url must be http(s), got {base_url!r}")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.tools = tools
        self.meter = Meter(role=role, model=model)
        self._sleep = sleep

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                # From the environment, and never echoed anywhere.
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        # The scheme is pinned to http(s) in __init__ and the endpoint is the
        # operator's own --base-url; audited once, here.
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.tools:
            body["tools"] = self.tools

        started = time.monotonic()
        last: Exception
        for attempt in range(MAX_TRANSPORT_RETRIES + 1):
            try:
                payload = self._post(body)
            except (urllib.error.URLError, TimeoutError, OSError) as failure:
                last = failure
                self.meter.retries += 1
                if attempt >= MAX_TRANSPORT_RETRIES:
                    break
                self._sleep(RETRY_SLEEP_SECONDS)
                continue
            elapsed = time.monotonic() - started
            self.meter.calls += 1
            self.meter.seconds += elapsed
            reply = parse_completion(payload)
            self.meter.prompt_tokens += reply.prompt_tokens
            self.meter.completion_tokens += reply.completion_tokens
            reason = str(((payload.get("choices") or [{}])[0] or {}).get("finish_reason") or "")
            self.meter.finish_reasons[reason] = self.meter.finish_reasons.get(reason, 0) + 1
            if reason == FINISH_TRUNCATED:
                # An instrument event, recorded the moment it happens. It is
                # NEVER read as the model having nothing to say.
                self.meter.truncated += 1
                print(
                    f"notice: {self.meter.role} turn truncated at "
                    f"max_tokens={self.max_tokens} ({self.model})",
                    file=sys.stderr,
                )
            if not reply.content and not reply.tool_calls:
                self.meter.empty_content += 1
            self.meter.record_turn(
                reply, finish_reason=reason, seconds=elapsed, messages=len(messages)
            )
            return reply

        self.meter.seconds += time.monotonic() - started
        self.meter.failures += 1
        # A failed call is DATA. It degrades to an empty turn, which the loop
        # reads as a model with nothing more to say, and the failure count rides
        # into the artifact beside the result.
        raise LeagueError(f"{self.meter.role} transport failed after retries: {last}")


class ScriptedSeam:
    """The hermetic stand-in, metered the same way. Never touches a network."""

    def __init__(self, *, role: str, model: str, cortex: bool) -> None:
        self.meter = Meter(role=role, model=model)
        self._complete = make_scripted_cortex() if cortex else scripted_muse

    def __call__(self, messages: list[dict[str, Any]]) -> ModelResponse:
        self.meter.calls += 1
        reply = self._complete(messages)
        self.meter.finish_reasons["stop"] = self.meter.finish_reasons.get("stop", 0) + 1
        self.meter.record_turn(reply, finish_reason="stop", seconds=0.0, messages=len(messages))
        return reply


def build_seams(arm: Arm, *, live: bool, base_url: str, api_key: str) -> tuple[Any, Any]:
    """The two seams one seat needs. ``live`` is the ONLY path to a network."""
    if not live:
        return (
            ScriptedSeam(role="cortex", model=f"scripted:{arm.cortex}", cortex=True),
            ScriptedSeam(role="muse", model=f"scripted:{arm.muse}", cortex=False),
        )
    cortex = MeteredSeam(
        base_url=base_url,
        model=arm.cortex,
        api_key=api_key,
        role="cortex",
        max_tokens=MAX_TOKENS,
        temperature=CORTEX_TEMPERATURE,
        tools=TOOL_SCHEMA,
    )
    muse = MeteredSeam(
        base_url=base_url,
        model=arm.muse,
        api_key=api_key,
        role="muse",
        max_tokens=MUSE_MAX_TOKENS,
        temperature=MUSE_TEMPERATURE,
    )
    return cortex, muse


# ── one seat-turn: the whole of what an arm does in a league turn ────────────


@dataclass(frozen=True)
class SeatConfig:
    """Everything a seat needs that is not the board. Both seats get the same
    object except for ``arm``, ``team`` and the paths — which is the point."""

    rung: Rung
    arm: Arm
    team: str
    pad: Path
    store: Path
    scope: str
    workdir: Path
    live: bool
    base_url: str
    api_key: str


def seat_turn(
    cli: H2HLeagueCli, show: dict[str, Any], cfg: SeatConfig, *, match_id: str
) -> dict[str, Any]:
    """Drive ONE embodiment loop for ONE team for ONE league turn.

    Called twice per turn — once per team, with different model ids and nothing
    else different. That symmetry is the experiment.
    """
    view = build_view(cli, match_id, show, cfg.team, fogged=cfg.rung.fogged)
    state = show.get("state") or {}
    turn_index = int(state.get("turn") or 0)

    pad = Scratchpad.load(cfg.pad)
    prior = recall_prior(
        f"{cfg.team} standing objective control point",
        store=cfg.store,
        scope=cfg.scope,
        top_k=RECALL_TOP_K,
    )
    recalled_text = [str(record.get("text", "")) for record in prior.records]

    cortex, muse = build_seams(cfg.arm, live=cfg.live, base_url=cfg.base_url, api_key=cfg.api_key)
    runner = ThreadedMuseRunner(
        muse,
        system=frame_muse(None, identity=None),
        controls=MuseControls(max_turns=MUSE_MAX_TURNS),
    )
    runner_id = uuid.uuid4().hex[:12]

    agents = [a.get("id", "") for a in _team_agents(show, cfg.team)]
    seat = Seat(team_id=cfg.team, pad=pad, show=show, agent_id=agents[0] if agents else "")
    presence = PresenceEngine(
        io=PresenceIO(render=lambda line: print(line, file=sys.stderr), task_state=seat.state),
        muse=runner,
    )
    lifecycle = build_continuity_fn(
        lifecycle_config(cfg.store, cfg.workdir, scope=cfg.scope, coherence=False)
    )
    # The directive rides EVERY turn here. league_seat's continuity proof
    # withholds it after turn 0 on purpose; this experiment is not that
    # experiment, and withholding it would make the result partly a memory
    # measurement, which would confound the model comparison it is for.
    task = build_task(
        f"{match_id}-{cfg.team}-t{turn_index}",
        view=view,
        directive=directive_for(cfg.rung),
        pad=pad,
        recalled=recalled_text,
        engine="league-h2h",
    )

    aborted: Optional[str] = None
    started = time.monotonic()
    try:
        outcome = run(
            cortex,
            task,
            executor=seat,
            max_steps=MAX_STEPS,
            system_prompt=system_prompt(cfg.rung),
            presence=presence,
            continuity=lifecycle,
            controls=LoopControls(write_intent=False),
            model=cfg.arm.cortex,
        )
    except LoopAborted as failure:
        outcome = failure.outcome
        aborted = str(failure.__cause__ or failure)
    finally:
        runner.close()
    elapsed = time.monotonic() - started

    orders = seat.submitted[-1] if seat.submitted else {"plan": "", "actions": []}
    staged = len(orders.get("actions") or [])
    orders, dropped = apply_cap(orders, cfg.rung.max_actions)

    record = {
        "turn": turn_index,
        "team": cfg.team,
        "arm": cfg.arm.id,
        "cortex_model": cfg.arm.cortex,
        "muse_model": cfg.arm.muse,
        "orders": orders,
        "staged_actions": staged,
        "cap": cfg.rung.max_actions,
        "cap_dropped": dropped,
        "cap_violation": dropped > 0,
        "units_alive": len(living_units(show, cfg.team)),
        "fogged": cfg.rung.fogged,
        "seconds": round(elapsed, 3),
        "drive": {
            "exit_reason": outcome.exit_reason,
            "status": outcome.result.status,
            "summary": outcome.result.summary,
            "model_turns": outcome.result.stats.model_turns,
            "tools": [step.tool for step in outcome.result.steps],
            "aborted": aborted,
        },
        "cost": {
            "cortex": cortex.meter.to_dict(),
            "muse": muse.meter.to_dict(),
        },
        # The raw transcript: what each mind actually said this seat-turn, in
        # order, cortex and muse alike. Committed with the verdicts.
        "transcript": list(cortex.meter.transcript) + list(muse.meter.transcript),
        "muse": muse_snapshot(runner, runner_id),
        "degradations": fold_degradations(outcome=outcome, runner=runner, lifecycle=lifecycle),
    }
    return record


def _team_agents(show: dict[str, Any], team_id: str) -> list[dict[str, Any]]:
    for team in (show.get("state") or {}).get("teams") or []:
        if team.get("id") == team_id:
            return list(team.get("agents") or [])
    return []


# ── one match: two model seats, colours fixed by the caller ──────────────────


def match_id_for(rung: Rung, blue: str, red: str) -> str:
    return f"h2h-{rung.id}-{blue}-vs-{red}".replace("_", "-")


def play_match(
    *,
    rung: Rung,
    blue: Arm,
    red: Arm,
    home: Path,
    log_path: Path,
    live: bool,
    base_url: str,
    api_key: str,
    league_bin: str,
    league_timeout: float,
) -> dict[str, Any]:
    """Play one head-to-head match. BOTH teams are model seats."""
    match_id = match_id_for(rung, blue.id, red.id)
    workdir = home / match_id
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    store = workdir / "memory"
    store.mkdir(parents=True, exist_ok=True)

    cli = H2HLeagueCli(binary=league_bin, workdir=workdir, timeout=league_timeout)
    seats = {"blue": blue, "red": red}
    for colour, arm in seats.items():
        cli.register_team(
            colour,
            name=colour.title(),
            agents=[
                f"{colour[0]}{i + 1}:{arm.cortex}:{role}" for i, role in enumerate(rung.roster)
            ],
        )
    created = cli.new_match_h2h(
        match_id=match_id,
        scenario=rung.scenario,
        teams=list(COLOURS),
        seed=rung.seed,
        # league's own residency word for a driver rebuilt every turn, which is
        # exactly what both seats are here.
        drivers={colour: "stateless" for colour in COLOURS},
        max_actions=rung.max_actions,
        map_read=rung.map_read,
        unit_comms=rung.unit_comms,
    )

    show = cli.show(match_id)
    turns: list[dict[str, Any]] = []
    rejections = {colour: 0 for colour in COLOURS}
    started = time.monotonic()
    transport_failure: Optional[str] = None

    for _ in range(rung.turns):
        if str((show.get("state") or {}).get("status") or "") != "active":
            break
        per_turn: dict[str, dict[str, Any]] = {}
        try:
            for colour in COLOURS:
                cfg = SeatConfig(
                    rung=rung,
                    arm=seats[colour],
                    team=colour,
                    pad=workdir / f"pad-{colour}.jsonl",
                    store=store,
                    scope=f"league-h2h-{match_id}-{colour}",
                    workdir=workdir,
                    live=live,
                    base_url=base_url,
                    api_key=api_key,
                )
                per_turn[colour] = seat_turn(cli, show, cfg, match_id=match_id)
        except LeagueError as failure:
            # Transport exhausted its retries. The match stops here, the turns
            # already played stand, and the failure is reported — never papered
            # over with a re-run.
            transport_failure = str(failure)
            break
        for colour in COLOURS:
            cli.act(match_id, colour, per_turn[colour]["orders"])
        show = cli.show(match_id)
        for entry in show.get("last_turn_rejections") or []:
            team = str(entry.get("team") or entry.get("team_id") or "")
            if team in rejections:
                rejections[team] += 1
        for colour in COLOURS:
            record = {"kind": "seat-turn", "match_id": match_id, **per_turn[colour]}
            append_jsonl(log_path, record)
            turns.append(per_turn[colour])

    elapsed = time.monotonic() - started
    final = cli.show(match_id)
    score = cli.score_v1(match_id)
    replay = cli.replay(match_id)

    report = {
        "kind": "match",
        "rung": rung.id,
        "scenario": rung.scenario,
        "seed": rung.seed,
        "match_id": match_id,
        "arms": {colour: seats[colour].id for colour in COLOURS},
        "models": {
            colour: {"cortex": seats[colour].cortex, "muse": seats[colour].muse}
            for colour in COLOURS
        },
        "driver_kinds": created.get("driver_kinds") or {},
        "max_actions": created.get("max_actions") or {},
        "map_read": created.get("map_read") or {},
        "unit_comms": created.get("unit_comms") or {},
        "turns_played": len({t["turn"] for t in turns}),
        "seconds": round(elapsed, 3),
        "transport_failure": transport_failure,
        "status": (final.get("state") or {}).get("status"),
        "winner": (final.get("state") or {}).get("winner"),
        "score": score,
        "cooperation_version": score.get("cooperation_version_used"),
        "rejections": rejections,
        "cap_dropped": {
            colour: sum(t["cap_dropped"] for t in turns if t["team"] == colour)
            for colour in COLOURS
        },
        "cost": {colour: _fold_cost(turns, colour) for colour in COLOURS},
        # An INSTRUMENT event, hoisted to the top of the match record because
        # a non-zero value disqualifies this match from the decision.
        "truncated_turns": sum(
            t["cost"][role]["truncated"] for t in turns for role in ("cortex", "muse")
        ),
        "max_tokens": MAX_TOKENS,
        "muse_max_tokens": MUSE_MAX_TOKENS,
        "degradations": [d for t in turns for d in (t.get("degradations") or [])],
        "aborted": [t["team"] for t in turns if (t.get("drive") or {}).get("aborted")],
        "replay_sha256": replay_digest(replay),
        "workdir": str(workdir),
    }
    report["result"] = decide_match(report)
    append_jsonl(log_path, report)
    return report


def _fold_cost(turns: list[dict[str, Any]], colour: str) -> dict[str, Any]:
    """One team's whole spend across the match, cortex and muse separately."""
    mine = [t for t in turns if t["team"] == colour]
    folded: dict[str, Any] = {}
    for role in ("cortex", "muse"):
        folded[role] = {
            "calls": sum(t["cost"][role]["calls"] for t in mine),
            "prompt_tokens": sum(t["cost"][role]["prompt_tokens"] for t in mine),
            "completion_tokens": sum(t["cost"][role]["completion_tokens"] for t in mine),
            "seconds": round(sum(t["cost"][role]["seconds"] for t in mine), 3),
            "retries": sum(t["cost"][role]["retries"] for t in mine),
            "failures": sum(t["cost"][role]["failures"] for t in mine),
            "empty_content": sum(t["cost"][role]["empty_content"] for t in mine),
            "truncated": sum(t["cost"][role]["truncated"] for t in mine),
        }
    folded["completion_tokens"] = (
        folded["cortex"]["completion_tokens"] + folded["muse"]["completion_tokens"]
    )
    folded["truncated"] = folded["cortex"]["truncated"] + folded["muse"]["truncated"]
    folded["seconds"] = round(sum(t["seconds"] for t in mine), 3)
    return folded


# ── the decision rule, applied without amendment ─────────────────────────────


def decide_match(report: dict[str, Any]) -> dict[str, Any]:
    """Which arm won this match, and on which tie-break.

    Priority order is :data:`TIE_BREAKS` and it is fixed. The primary is
    league's own ``outcome.total``; at :data:`MATCH_TURNS` turns that is
    expected to tie at zero in every scenario, which is stated in the
    pre-registration rather than discovered afterwards.
    """
    outcome = (report.get("score") or {}).get("outcome") or {}
    coop = (report.get("score") or {}).get("cooperation") or {}
    totals = {c: int((outcome.get(c) or {}).get("total") or 0) for c in COLOURS}
    coops = {c: int((coop.get(c) or {}).get("score") or 0) for c in COLOURS}
    faults = {
        c: int((report.get("rejections") or {}).get(c) or 0)
        + int((report.get("cap_dropped") or {}).get(c) or 0)
        for c in COLOURS
    }

    ladder: tuple[tuple[str, dict[str, int], bool], ...] = (
        ("outcome_total", totals, True),
        ("cooperation_v1", coops, True),
        ("fewer_rejections_and_cap_violations", faults, False),
    )
    # Blue-minus-red on every metric, recorded whether or not it decided
    # anything. This is what :func:`decide_pairing` sums across the colour swap
    # to cancel the map's own bias; see its docstring.
    margins = {
        "outcome_total": totals["blue"] - totals["red"],
        "cooperation_v1": coops["blue"] - coops["red"],
        "faults": faults["blue"] - faults["red"],
    }
    for name, values, higher_is_better in ladder:
        blue, red = values["blue"], values["red"]
        if blue == red:
            continue
        winner = "blue" if ((blue > red) == higher_is_better) else "red"
        return {
            "decided_by": name,
            "winner_colour": winner,
            "winner_arm": report["arms"][winner],
            "values": dict(values),
            "margins": margins,
        }
    return {
        "decided_by": None,
        "winner_colour": None,
        "winner_arm": None,
        "values": {"outcome_total": totals, "cooperation_v1": coops, "faults": faults},
        "margins": margins,
    }


def decide_pairing(matches: Sequence[dict[str, Any]], *, first_arm: str) -> dict[str, Any]:
    """A pairing separates only when one arm wins BOTH colour assignments.

    That strict rule is the **decision**, and it is deliberately conservative:
    it cannot be fooled by a map that favours one home corner, because a
    colour-linked advantage flips sides on the swap and so cannot win twice.

    ``net_margin`` is recorded beside it and is **not** the decision rule. It
    is the paired statistic the colour swap makes available — first-arm minus
    second-arm on each metric, summed over the two colour assignments — in
    which a constant colour bias ``b`` enters as ``+b`` once and ``-b`` once
    and cancels exactly. It is reported as an effect size because the offline
    identical-mind control (see the pre-registration) measured that bias to be
    real and, at the fogged rung, large: two byte-identical minds scored 90
    against 45 on ``cooperation_v1`` purely by which corner they started in. A
    statistic that survives that is worth reporting; it is not worth promoting
    to a verdict after the fact, so it is not one.
    """
    wins: dict[str, int] = {}
    net: dict[str, float] = {"outcome_total": 0.0, "cooperation_v1": 0.0, "faults": 0.0}
    for match in matches:
        result = match.get("result") or {}
        arm = result.get("winner_arm")
        if arm:
            wins[arm] = wins.get(arm, 0) + 1
        # Orient blue-minus-red into first-arm-minus-second-arm.
        sign = 1 if match["arms"]["blue"] == first_arm else -1
        for metric, value in (result.get("margins") or {}).items():
            net[metric] = net.get(metric, 0.0) + sign * float(value)
    complete = len(matches) == MATCHES_PER_PAIRING
    leader = next((arm for arm, count in wins.items() if count == MATCHES_PER_PAIRING), None)
    return {
        "matches": len(matches),
        "wins": wins,
        "separated": bool(leader) and complete,
        "winner_arm": leader if complete else None,
        "first_arm": first_arm,
        "net_margin": {k: round(v, 2) for k, v in net.items()},
    }


def decide_rung(pairings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A rung separates when at least :data:`MIN_SEPARATED_PAIRINGS` did."""
    separated = [name for name, p in pairings.items() if p["separated"]]
    verdict = (
        VERDICT_SEPARATED if len(separated) >= MIN_SEPARATED_PAIRINGS else VERDICT_INCONCLUSIVE
    )
    ordering = _implied_ordering(pairings)
    return {
        "verdict": verdict,
        "separated_pairings": separated,
        "separated_count": len(separated),
        "implied_ordering": ordering["ordering"],
        "cyclic": ordering["cyclic"],
    }


def _implied_ordering(pairings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The arm ranking the separated pairings imply, and whether it cycles.

    Rock-paper-scissors is a real possible answer here and it is reported as
    one, not smoothed into a ranking that does not exist.
    """
    beats: list[tuple[str, str]] = []
    for name, result in pairings.items():
        if not result["separated"]:
            continue
        winner = result["winner_arm"]
        loser = next(
            (arm for (pid, a, b) in PAIRINGS if pid == name for arm in (a, b) if arm != winner),
            None,
        )
        if winner and loser:
            beats.append((winner, loser))
    tally: dict[str, int] = {arm: 0 for arm in ARMS}
    for winner, _loser in beats:
        tally[winner] += 1
    cyclic = len(beats) == 3 and all(count == 1 for count in tally.values())
    ordering = [arm for arm, _ in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"ordering": ordering, "cyclic": cyclic, "beats": beats}


def cheapest_arm(rung_records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Completion tokens and wall clock per arm — the answer when quality ties.

    t9 measured the muse reaching identical verdicts to the cortex for about
    a twenty-fifth of the tokens. If quality does not separate here, this is
    the publishable answer to the operator's question, and it is computed from
    the same artifact as everything else.
    """
    tokens: dict[str, int] = {arm: 0 for arm in ARMS}
    seconds: dict[str, float] = {arm: 0.0 for arm in ARMS}
    matches: dict[str, int] = {arm: 0 for arm in ARMS}
    truncated: dict[str, int] = {arm: 0 for arm in ARMS}
    for record in rung_records:
        for colour in COLOURS:
            arm = record["arms"][colour]
            tokens[arm] += int(record["cost"][colour]["completion_tokens"])
            seconds[arm] += float(record["cost"][colour]["seconds"])
            truncated[arm] += int(record["cost"][colour].get("truncated") or 0)
            matches[arm] += 1
    per_match = {
        arm: {
            "matches": matches[arm],
            "completion_tokens": tokens[arm],
            "completion_tokens_per_match": (
                round(tokens[arm] / matches[arm], 1) if matches[arm] else None
            ),
            "seconds": round(seconds[arm], 1),
            "seconds_per_match": (round(seconds[arm] / matches[arm], 1) if matches[arm] else None),
            # Prominent, per arm, because a reader must be able to see that
            # neither arm was capped short. Zero is the claim being made.
            "truncated_turns": truncated[arm],
        }
        for arm in ARMS
    }
    ranked = [
        arm
        for arm in sorted(ARMS, key=lambda a: (tokens[a] if matches[a] else float("inf"), a))
        if matches[arm]
    ]
    return {"per_arm": per_match, "cheapest": ranked[0] if ranked else None}


# ── the ladder runner ────────────────────────────────────────────────────────


def rung_matches(rung: Rung) -> list[tuple[str, Arm, Arm]]:
    """Six matches: three pairings, each played in both colour assignments."""
    plan: list[tuple[str, Arm, Arm]] = []
    for name, first, second in PAIRINGS:
        plan.append((name, ARMS[first], ARMS[second]))
        plan.append((name, ARMS[second], ARMS[first]))
    return plan


def run_rung(
    rung: Rung,
    *,
    home: Path,
    log_path: Path,
    live: bool,
    base_url: str,
    api_key: str,
    league_bin: str,
    league_timeout: float,
    deadline: Optional[float] = None,
) -> dict[str, Any]:
    """Play one rung's six matches and grade it.

    **Truncation disqualifies, it never loses.** A match in which either seat
    recorded a ``finish_reason == "length"`` turn is kept in the artifact and
    in the cost fold, and is **excluded from the decision** — because a
    truncated turn returns empty content, which the loop reads as a mind with
    nothing to say, which costs that seat its orders and therefore its
    cooperation score. Scoring that would be scoring the instrument. Its
    pairing then has fewer than two usable colour assignments and so cannot
    separate, which is the correct conservative outcome.
    """
    started = time.monotonic()
    records: list[dict[str, Any]] = []
    by_pairing: dict[str, list[dict[str, Any]]] = {name: [] for name, _, _ in PAIRINGS}
    excluded: list[str] = []
    stopped: Optional[str] = None

    for name, blue, red in rung_matches(rung):
        now = time.monotonic()
        if now - started > RUNG_CAP_SECONDS:
            stopped = "rung wall-clock cap reached"
            break
        if deadline is not None and now > deadline:
            stopped = "ladder wall-clock cap reached"
            break
        print(
            f"[{rung.id}] {name}: blue={blue.id} red={red.id}",
            file=sys.stderr,
            flush=True,
        )
        record = play_match(
            rung=rung,
            blue=blue,
            red=red,
            home=home,
            log_path=log_path,
            live=live,
            base_url=base_url,
            api_key=api_key,
            league_bin=league_bin,
            league_timeout=league_timeout,
        )
        records.append(record)
        if record["truncated_turns"]:
            excluded.append(record["match_id"])
            print(
                f"notice: {record['match_id']} excluded from the decision — "
                f"{record['truncated_turns']} truncated turn(s) at "
                f"max_tokens={MAX_TOKENS}",
                file=sys.stderr,
                flush=True,
            )
            continue
        by_pairing[name].append(record)

    first_arm_of = {name: first for name, first, _ in PAIRINGS}
    pairings = {
        name: decide_pairing(matches, first_arm=first_arm_of[name])
        for name, matches in by_pairing.items()
    }
    graded = decide_rung(pairings)
    complete = len(records) == len(rung_matches(rung))
    summary = {
        "kind": "rung",
        "rung": rung.id,
        "scenario": rung.scenario,
        "seed": rung.seed,
        "turns": rung.turns,
        "max_actions": rung.max_actions,
        "fogged": rung.fogged,
        "matches_planned": len(rung_matches(rung)),
        "matches_played": len(records),
        "matches_excluded_for_truncation": excluded,
        "truncated_turns": sum(r["truncated_turns"] for r in records),
        "max_tokens": MAX_TOKENS,
        "muse_max_tokens": MUSE_MAX_TOKENS,
        "complete": complete,
        "stopped": stopped,
        "seconds": round(time.monotonic() - started, 1),
        "pairings": pairings,
        "cost": cheapest_arm(records),
        **graded,
    }
    if not complete:
        # A rung whose matches did not all run cannot separate anything: an
        # incomplete pairing is reported ABSENT rather than graded on half a
        # colour swap.
        summary["verdict"] = VERDICT_ABSENT
    append_jsonl(log_path, summary)
    return summary


def run_ladder(args: argparse.Namespace) -> dict[str, Any]:
    """Climb until the arms separate, then stop. Separation is the result."""
    home = Path(args.home).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log)
    api_key = ""
    if args.live:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            print(f"error: --live needs {API_KEY_ENV} in the environment", file=sys.stderr)
            print(
                f"hint: export {API_KEY_ENV}=… and point --base-url at your gateway",
                file=sys.stderr,
            )
            raise SystemExit(2)

    wanted = [LADDER_BY_ID[r] for r in args.rungs.split(",")] if args.rungs else list(LADDER)
    started = time.monotonic()
    deadline = started + LADDER_CAP_SECONDS

    rungs: list[dict[str, Any]] = []
    absent: list[str] = []
    for index, rung in enumerate(wanted):
        if time.monotonic() > deadline:
            absent.extend(r.id for r in wanted[index:])
            break
        summary = run_rung(
            rung,
            home=home,
            log_path=log_path,
            live=args.live,
            base_url=args.base_url,
            api_key=api_key,
            league_bin=args.league_bin,
            league_timeout=args.league_timeout,
            deadline=deadline,
        )
        rungs.append(summary)
        if summary["verdict"] == VERDICT_SEPARATED:
            absent.extend(r.id for r in wanted[index + 1 :])
            break
        if summary["verdict"] == VERDICT_ABSENT:
            absent.extend(r.id for r in wanted[index + 1 :])
            break
    else:
        absent = []

    played = [r for r in rungs if r["verdict"] != VERDICT_ABSENT]
    separated = next((r for r in played if r["verdict"] == VERDICT_SEPARATED), None)
    all_records: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []:
        entry = json.loads(line)
        if entry.get("kind") == "match":
            all_records.append(entry)

    report = {
        "kind": "ladder",
        "verdict": VERDICT_SEPARATED if separated else VERDICT_INCONCLUSIVE,
        "separated_at": separated["rung"] if separated else None,
        "rungs_run": [r["rung"] for r in rungs],
        "rungs_absent": absent,
        "cost": cheapest_arm(all_records),
        "seconds": round(time.monotonic() - started, 1),
        "log": str(log_path),
    }
    append_jsonl(log_path, report)
    return report


# ── the config preamble, written BEFORE the first result line ────────────────


def write_preamble(args: argparse.Namespace, log_path: Path) -> dict[str, Any]:
    """The whole configuration, in one record, before any result exists."""
    config = {
        "experiment": "league-h2h",
        "task": "t27",
        "live": bool(args.live),
        "base_url": args.base_url if args.live else None,
        "arms": {arm.id: {"cortex": arm.cortex, "muse": arm.muse} for arm in ARMS.values()},
        "control_arm": CONTROL_ARM,
        "senses_model": SENSES_MODEL,
        "senses_dialled": False,
        "pairings": [name for name, _, _ in PAIRINGS],
        "colours": list(COLOURS),
        "max_steps": MAX_STEPS,
        "max_tokens": MAX_TOKENS,
        "muse_max_tokens": MUSE_MAX_TOKENS,
        "token_ceiling_available": TOKEN_CEILING_AVAILABLE,
        "budget_fairness": "sufficient (neither model truncates); cost measured by consumption",
        "cortex_temperature": CORTEX_TEMPERATURE,
        "muse_temperature": MUSE_TEMPERATURE,
        "muse_max_turns": MUSE_MAX_TURNS,
        "match_turns": MATCH_TURNS,
        "tie_breaks": list(TIE_BREAKS),
        "min_separated_pairings": MIN_SEPARATED_PAIRINGS,
        "rung_cap_seconds": RUNG_CAP_SECONDS,
        "ladder_cap_seconds": LADDER_CAP_SECONDS,
        "max_transport_retries": MAX_TRANSPORT_RETRIES,
        "ladder": [
            {
                "id": rung.id,
                "scenario": rung.scenario,
                "seed": rung.seed,
                "roster": list(rung.roster),
                "turns": rung.turns,
                "max_actions": rung.max_actions,
                "fogged": rung.fogged,
                "map_read": rung.map_read,
                "unit_comms": rung.unit_comms,
                "objective": rung.objective,
                "why": rung.why,
            }
            for rung in LADDER
        ],
    }
    path = log_path.with_name(log_path.stem + "-config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(log_path, {"kind": "config", "config": config})
    return config


# ── CLI ──────────────────────────────────────────────────────────────────────


def default_home() -> Path:
    """Outside any git work tree, always: a match's memories must never land in
    a checkout's committed ``.eidetic/``."""
    return Path(tempfile.gettempdir()) / "embodiment-league-h2h"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="league_h2h",
        description=(
            "Three model arms, round-robin head-to-head, up an escalating "
            "league-of-agents ladder. Software presence, not a robot body."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--home", default=str(default_home()), help="scratch root for the series")
        p.add_argument("--log", required=True, help="JSONL artifact (results land here)")
        p.add_argument("--league-bin", default="league")
        p.add_argument("--league-timeout", type=float, default=300.0)
        p.add_argument("--json", action="store_true")
        live = p.add_argument_group("live rig (opt-in; nothing here is a default)")
        live.add_argument("--live", action="store_true")
        live.add_argument("--base-url", default=DEFAULT_BASE_URL)

    ladder = sub.add_parser("ladder", help="climb the ladder until the arms separate")
    common(ladder)
    ladder.add_argument("--rungs", default="", help="comma-separated rung ids (default: all)")

    one = sub.add_parser("match", help="one head-to-head match (pilot/timing use)")
    common(one)
    one.add_argument("--rung", required=True, choices=sorted(LADDER_BY_ID))
    one.add_argument("--blue", required=True, choices=sorted(ARMS))
    one.add_argument("--red", required=True, choices=sorted(ARMS))

    plan = sub.add_parser("plan", help="print the ladder and the decision rule; dials nothing")
    plan.add_argument("--json", action="store_true")
    return parser


def render_plan() -> str:
    lines = [
        "league_h2h — the pre-registered ladder",
        "",
        "arms:",
    ]
    for arm in ARMS.values():
        mark = "  (control, ships today)" if arm.id == CONTROL_ARM else ""
        lines.append(f"  {arm.id:<11} cortex={arm.cortex}")
        lines.append(f"  {'':<11} muse  ={arm.muse}{mark}")
    lines += ["", "pairings: " + ", ".join(name for name, _, _ in PAIRINGS), "", "ladder:"]
    for rung in LADDER:
        lines.append(
            f"  {rung.id}  {rung.scenario:<11} seed={rung.seed} turns={rung.turns} "
            f"cap={rung.max_actions} fog={rung.fogged}"
        )
        lines.append(f"      {rung.why}")
    lines += [
        "",
        f"budgets (SUFFICIENT, identical in every seat): max_steps={MAX_STEPS} "
        f"max_tokens={MAX_TOKENS} muse_max_tokens={MUSE_MAX_TOKENS} "
        f"(ceiling available {TOKEN_CEILING_AVAILABLE}); cost is measured by "
        "tokens consumed, not by the cap",
        f"tie-breaks: {' -> '.join(TIE_BREAKS)}",
        f"a rung separates when >= {MIN_SEPARATED_PAIRINGS} of "
        f"{len(PAIRINGS)} pairings separate",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            if args.json:
                print(
                    json.dumps(
                        {
                            "arms": {
                                a.id: {"cortex": a.cortex, "muse": a.muse} for a in ARMS.values()
                            },
                            "pairings": [n for n, _, _ in PAIRINGS],
                            "ladder": [r.id for r in LADDER],
                            "tie_breaks": list(TIE_BREAKS),
                        },
                        indent=2,
                    )
                )
            else:
                print(render_plan())
            return 0

        log_path = Path(args.log)
        log_path.unlink(missing_ok=True)
        write_preamble(args, log_path)

        if args.command == "ladder":
            report = run_ladder(args)
        else:
            home = Path(args.home).expanduser()
            home.mkdir(parents=True, exist_ok=True)
            key = os.environ.get(API_KEY_ENV, "").strip() if args.live else ""
            if args.live and not key:
                print(f"error: --live needs {API_KEY_ENV} in the environment", file=sys.stderr)
                print("hint: export it and re-run", file=sys.stderr)
                return 2
            report = play_match(
                rung=LADDER_BY_ID[args.rung],
                blue=ARMS[args.blue],
                red=ARMS[args.red],
                home=home,
                log_path=log_path,
                live=args.live,
                base_url=args.base_url,
                api_key=key,
                league_bin=args.league_bin,
                league_timeout=args.league_timeout,
            )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except (LeagueError, ValueError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        print(
            "hint: check --league-bin points at an installed 'league' and that "
            "--home is writable",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
