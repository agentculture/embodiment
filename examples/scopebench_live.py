#!/usr/bin/env python3
"""scopebench_live — the pre-registered ScopeBench series, dialled (plan task ``t11``).

``t9`` committed the scaffold, the deterministic perfect subordinate, the exact
oracle, the four arms, the four disjoint record axes and the seven-condition
verdict rule, all **before** any live model call
(``docs/live-test-results/scopebench-preregistration.md``). This module executes
that protocol. It writes none of it.

Why this file is NOT under ``examples/scope/``
-----------------------------------------------
The pre-registration §14 states that no module under ``examples/scope/``
imports a transport, reaches ``embodiment.loop``, or introduces a timeout
constant, and three AST tests assert it over every file in that folder
(``tests/test_scopebench.py::TestNoLiveDial``,
``tests/test_timeout_bounds.py::TestTheScopeLaneIntroducesNoClock``). Putting
the dial in there would break a claim that was committed before any result
exists. So the hermetic scaffold stays hermetic and reproducible, and the one
module that talks to a network lives out here beside the other live harnesses
(``worker_seam.py``, ``arch_arms.py``, ``league_h2h.py``) — the same shape the
rest of this repo already uses.

What varies between arms, and what may not
------------------------------------------
Exactly one field: which lobes role fills the **strategy seat**. ``A3`` seats
``cortex``, ``A2`` seats ``worker``, and everything else — the episodes, the
seeds, the projector, the system framing, the sampling, the budget, the
transport, the parser, the register, the grader — is one code path shared by
both. :func:`arm_fingerprint` folds that shared configuration into a record so
"identical apart from the seat" is **asserted from the run records** rather
than asserted in prose (``t11`` acceptance criterion 2, applied at Stage 1
too).

Roles resolve **by name** from the gateway's ``/capabilities`` payload, through
``examples/scope/seats.py`` — the module ``t7`` built for exactly this and which
deliberately never dials. Fetching the payload is the host's job, so it is this
module's; reading it is that module's.

The protocol is the shipped one
--------------------------------
The system message is ``embodiment.scope.SCOPE_AUTHORITY`` **verbatim**, plus a
host framing block appended the way ``ScopeLoop`` appends one — never a
substitute for it. The strategist answers ``[hold]`` or ``DIRECTIVE:`` followed
by one JSON object, and it authors its own ``scope_id``, ``supersedes`` and
``version``. That is deliberately harder than what the scripted Stage-1
controls face (``subordinate._payload_for`` stamps those three for them), and it
is the honest choice: the protocol axis exists to measure whether a strategist
can phrase an admissible directive, and a harness that stamped the bookkeeping
would have made that axis vacuous. A model that cannot hold the protocol lands
below :data:`examples.scope.scopebench.PROTOCOL_FLOOR` and its cell is
``void-protocol`` — reported, and never scored as a strategic loss.

Clocks
------
**This module introduces none.** Every dial goes through
``examples/worker_seam.py``'s :class:`~examples.worker_seam.WorkerSeam`, so the
request bound, the two streaming phases and the retry ladder are the constants
``tests/test_timeout_bounds.py`` already walks. What this module *does* add is
two ``(role, budget)`` pairs in front of that clock — the cortex and the worker
at :data:`STRATEGIST_MAX_TOKENS` — and both are declared in that test's
``CLOCKS`` table, because the standing rule is that a constant's bound is the
worst of every model it fronts, not the one that was to hand.

Streaming is the transport (deviation ``d3``), which matters more here than
usual: the strategist is a thinking model asked for a judgement, and a
first-chunk bound sized for a non-thinking one would censor exactly the long
deliberations the series is trying to measure.

Usage::

    export COLLEAGUE_API_KEY=...
    uv run python examples/scopebench_live.py seats
    uv run python examples/scopebench_live.py smoke --arm A3
    uv run python examples/scopebench_live.py stage1 --arm A3 --out docs/live-test-results/...
    uv run python examples/scopebench_live.py report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodiment.scope import (  # noqa: E402
    MARKER_DIRECTIVE,
    MARKER_HOLD,
    SCOPE_AUTHORITY,
    SNAPSHOT_HEADER,
)
from examples import worker_seam as ws  # noqa: E402
from examples.scope import episodes as ep  # noqa: E402
from examples.scope import oracle as orc  # noqa: E402
from examples.scope import scopebench as sb  # noqa: E402
from examples.scope import seats as st  # noqa: E402
from examples.scope import subordinate as sub  # noqa: E402

__all__ = [
    "CAPABILITIES_PATH",
    "DEFAULT_GATEWAY",
    "DROPPED_EPISODE_TRANSPORT",
    "LIVE_ARMS",
    "RAW_DIR",
    "REPLY_DIRECTIVE",
    "REPLY_HOLD",
    "REPLY_UNREADABLE",
    "STAGE_TWO_ABSENT",
    "STRATEGIST_MAX_TOKENS",
    "STRATEGIST_TEMPERATURE",
    "ArmRun",
    "LiveStrategist",
    "ReviewCall",
    "SeatDialConfig",
    "StrategistUnavailable",
    "arm_fingerprint",
    "build_parser",
    "episode_framing",
    "fetch_capabilities",
    "first_object",
    "load_records",
    "main",
    "read_reply",
    "resolve_dial",
    "review_message",
    "run_arm",
    "run_episode",
    "summarise_live",
]


# ── where things live ─────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "docs" / "live-test-results"

#: Raw per-episode records, one JSONL per (arm, stage). Written incrementally,
#: so a run that dies partway keeps every episode it completed.
RAW_DIR = RESULTS_DIR / "scopebench-raw"

#: The gateway every other harness in this repo dials (``examples/proof.py``,
#: ``examples/league_seat.py``). Unlike ``worker_seam.py`` this default IS safe:
#: that module refuses one because the spark gateway advertises no worker role
#: and a silent fallback would reach the wrong machine; here the role is
#: resolved from ``/capabilities`` by name before anything is dialled, so a
#: gateway that does not serve the seat degrades rather than mis-dials.
DEFAULT_GATEWAY = "http://localhost:8001"

#: The ``/capabilities`` advert, committed beside the results so a reader can
#: see which rig produced them.
CAPABILITIES_PATH = RAW_DIR / "capabilities.json"

#: The two arms this module can dial. ``A0`` is represented at Stage 1 by the
#: pre-registered scripted stand-in (``STAGE_ONE_STANDIN``) and ``A1`` has no
#: Stage-1 cell at all — both declared in the pre-registration, not here.
LIVE_ARMS: tuple[str, ...] = (sb.ARM_A2, sb.ARM_A3)


# ── sampling, and the budget the clock is derived at ──────────────────────────

#: **Deviation ``d16``'s measured floor**, and the numerator every bound in
#: front of this harness divides. ``docs/live-test-results/arena-budget.md``
#: measured the shipped 2048 default truncating 6.0% of completions (5 of 83)
#: with **zero** degradations recorded — ``ModelResponse`` carries no
#: ``finish_reason`` (issue #37), so a turn cut mid-thought and a turn that
#: ended on purpose arrive at the loop as the same object. At 16000: 0 of 58.
#:
#: It matters more here than in a tool loop. A strategist that is truncated
#: mid-JSON emits an unparseable directive, which lands on the **protocol**
#: axis — so a budget set too low would be recorded as the arm being unable to
#: phrase a directive. That is the exact axis confusion the four-axis design
#: exists to prevent, arriving through the instrument instead of the model.
#:
#: Declared in ``tests/test_timeout_bounds.py``'s ``CLOCKS`` as the budget the
#: cortex and worker terms of ``worker_seam.REQUEST_TIMEOUT`` are derived at.
STRATEGIST_MAX_TOKENS = 16000

#: This repo's standing sampling temperature for measured lanes
#: (``arch-arms-sampling.json``, ``worker_seam.run_smoke``). Identical in every
#: arm, because a control that sampled differently would be a second experiment.
STRATEGIST_TEMPERATURE = 0.3

#: How many requests this harness has in flight at once. **One**, deliberately:
#: the cortex's committed rate was measured at ``concurrency: 1``, rates are
#: never interpolated (``tests/rate_config.py``'s ``at_width`` refuses), and a
#: bound divided by a width-1 rate while dialling at width 2 is the flattering
#: reading plan risk ``r1`` names. Two arms may run as two processes — the
#: worker is proxied off this box — but neither widens the cortex.
STREAM_QUEUE_WIDTH = 1


# ── how a strategist's answer is read ─────────────────────────────────────────

#: The strategist wrote a parseable JSON object. It may still be refused by the
#: register or name work nobody can do; both are protocol events downstream.
REPLY_DIRECTIVE = "directive"
#: The strategist wrote :data:`~embodiment.scope.MARKER_HOLD`. A real answer.
REPLY_HOLD = "hold"
#: Neither. Recorded as an **offered** directive with nothing in it, which the
#: register refuses as ``scope-directive-incomplete`` — a protocol event, never
#: an outcome one, and never confused with a hold.
REPLY_UNREADABLE = "unreadable"

#: An episode abandoned because a strategist call failed after the seam's whole
#: retry ladder. **Not** folded into any axis: a dead transport is an instrument
#: event and charging it to protocol acceptance could void an arm for a rig
#: fault. The episode leaves the cell and is reported by id.
DROPPED_EPISODE_TRANSPORT = "dropped-strategist-transport-failure"

#: Why Stage 2 has no cell, in ``t11``'s own words rather than ``t9``'s. Filled
#: in by :func:`summarise_live` for every unscored Stage-2 cell.
STAGE_TWO_ABSENT = (
    "Stage 2 was not dialled in this cycle: it needs a live worker-role actor "
    "playing the episode under the standing directive, which is a second harness "
    "and a second capacity budget. Stage 1 was run first because the "
    "pre-registration makes it condition 2's whole input — an improvement that "
    "does not appear against a perfect subordinate did not come from the "
    "upper-level decision"
)


class StrategistUnavailable(RuntimeError):
    """The strategist seam failed after its retries. The episode is abandoned."""


# ── seat resolution: by role name, from the gateway's own advert ──────────────


@dataclass(frozen=True)
class SeatDialConfig:
    """Everything one arm's strategist seat needs, resolved and recorded."""

    arm: str
    seat: str
    role: str
    model: str
    base_url: str
    api_key: str
    hosted_by: str = ""
    proxied: bool = False

    def to_dict(self) -> dict[str, Any]:
        # The key is never serialised, matching WorkerSeam's own convention.
        return {
            "arm": self.arm,
            "seat": self.seat,
            "role": self.role,
            "model": self.model,
            "base_url": self.base_url,
            "hosted_by": self.hosted_by,
            "proxied": self.proxied,
        }


def fetch_capabilities(gateway: str, *, timeout: float) -> dict[str, Any]:
    """GET ``/capabilities``. Needs no key on this rig; raises on anything else.

    Deliberately not degrading: a seat resolved from a payload nobody fetched
    is a seat nobody resolved, and this runs once before any measured call.
    """
    url = f"{gateway.rstrip('/')}/capabilities"
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"--gateway must be http(s), got {gateway!r}")
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return payload


def resolve_dial(
    arm: str,
    capabilities: Mapping[str, Any],
    *,
    gateway: str,
    api_key: str,
) -> tuple[Optional[SeatDialConfig], tuple[st.SeatDegradation, ...]]:
    """The strategist dial for *arm*, or ``None`` plus why not.

    The seat-to-role mapping is ``scopebench.ARMS[arm].seats``' — the arm table
    the pre-registration fixed — and the role is looked up in *capabilities* by
    its own dict key through ``seats.resolve_seats``. Nothing here reads a model
    string to decide which seat anything belongs to (spec claim ``c2``).
    """
    resolution = st.resolve_seats(capabilities)
    role = sb.ARMS[arm].strategist_role
    if not role:
        return None, resolution.degradations
    resolved = {
        st.ROLE_CORTEX: resolution.strategist,
        st.ROLE_WORKER: resolution.actor,
        st.ROLE_SENSES: resolution.senses,
    }.get(role)
    if resolved is None:
        return None, resolution.degradations
    endpoint = resolved.endpoint or gateway
    dial = SeatDialConfig(
        arm=arm,
        seat=st.SEAT_STRATEGIST,
        role=role,
        model=resolved.model,
        base_url=f"{endpoint.rstrip('/')}/v1",
        api_key=api_key,
        hosted_by=resolved.hosted_by,
        proxied=resolved.proxied,
    )
    return dial, resolution.degradations


# ── the prompt: the shipped authority text, plus this world's vocabulary ──────


def episode_framing(episode: ep.Episode) -> str:
    """The host framing block, **appended** to :data:`SCOPE_AUTHORITY`.

    Never a substitute for it — the same discipline ``ScopeLoop._system_message``
    holds, for the same reason: no configuration may drop the authority
    boundary. What this adds is only what the strategist cannot know from a
    generic framing: the names in this world, and the two physical rules that
    make a responsibility executable at all.

    It states no strategy. It names no good answer, no heuristic, and no family
    — a framing that hinted at what to do would be the harness scoring itself.
    """
    actors = "; ".join(
        f"{actor.id} (can work: {', '.join(actor.skills)}; "
        f"rate {actor.rate} effort/tick; cost {actor.cost}/tick)"
        for actor in episode.actors
    )
    streams = "; ".join(
        f"{stream.id} (kind {stream.kind})"
        + (f", blocked until {', '.join(stream.depends_on)} completes" if stream.depends_on else "")
        for stream in episode.workstreams
    )
    return "\n".join(
        [
            "This system allocates responsibility among a fixed set of actors over a "
            "fixed number of ticks. The projection you are shown is its state now.",
            "",
            f"Actors: {actors}.",
            f"Workstreams: {streams}.",
            f"Idle is written as '{ep.IDLE}'.",
            "",
            "Your 'responsibilities' list is what this world calls an allocation. Give "
            'every actor exactly one entry: {"owner": <actor>, "responsibility": '
            f"<workstream or '{ep.IDLE}'>}}. Two physical rules bind it — an actor may "
            "only be given a workstream whose kind is one of its skills, and no actor may "
            "appear twice. An entry that breaks either is refused and that actor idles; "
            "nothing is repaired on your behalf.",
            "",
            "The commitments you are shown are priced, not enforced. An allocation that "
            "breaches one is carried out and the breach is charged to you.",
            "",
            "The allocation you set holds until the next review. There are a fixed number "
            "of reviews and you cannot see events that have not happened yet.",
        ]
    )


def review_message(context: sub.PlannerContext) -> str:
    """The user turn: the projection as data, then the bookkeeping it omits.

    The projection is ``subordinate.project()``'s — the same host projector
    Stage 1 grades against, so what a live strategist reads and what the
    scripted controls were scored on cannot drift apart. It is fenced under
    :data:`~embodiment.scope.SNAPSHOT_HEADER` verbatim, because text that
    arrives unlabelled is indistinguishable from the host's own framing.

    The lines after it are **facts, never instruction** — and that separation is
    a correction, made from a pilot and recorded as pre-registration amendment 1
    rather than quietly. The first wording read "A directive must carry a
    version strictly greater than that and must supersede that scope_id", which
    restates a rule ``SCOPE_AUTHORITY`` already carries *and* names the active
    id inside the sentence that says "must carry". The worker seat read it the
    way it was written and echoed the active ``scope_id`` into its own on two of
    three reviews, so every directive it offered was refused as a duplicate id
    and the cell scored ``protocol_acceptance = 0.0``.

    That would have been published as a model property when a sentence of the
    harness's own helped produce it. So the rule stays where the package puts it
    and this block supplies only what the projection omits: which scope is
    active, at which version, under which allocation, and how much of the
    episode is left. Withholding those would measure guessing rather than
    phrasing; instructing on top of them measures the harness.
    """
    active = context.active
    allocation = (
        ()
        if active is None
        else tuple((entry.owner, entry.responsibility) for entry in active.responsibilities)
    )
    standing = ", ".join(f"{owner} -> {target}" for owner, target in allocation) or "(none)"
    reviews_left = len(context.episode.review_ticks) - context.review - 1
    return "\n".join(
        [
            SNAPSHOT_HEADER,
            json.dumps(dict(context.snapshot), indent=2, sort_keys=True),
            "",
            f"Active scope_id: {'(none)' if active is None else active.scope_id}",
            f"Active version: {-1 if active is None else active.version}",
            f"Standing allocation: {standing}.",
            f"This is review {context.review + 1} of {len(context.episode.review_ticks)}; "
            f"{reviews_left} remain after it.",
        ]
    )


# ── reading the strategist's reply ────────────────────────────────────────────


def first_object(text: str, start: int = 0) -> Optional[str]:
    """The first balanced ``{...}`` span at or after *start*, or ``None``.

    String-aware, so a brace inside a JSON string does not close the object.
    A local implementation rather than an import of ``embodiment.scope``'s
    private helper of the same shape: a harness reaching into a package's
    underscore surface is a dependency nothing declares.
    """
    depth = 0
    opened = -1
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                opened = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and opened >= 0:
                return text[opened : index + 1]
            if depth < 0:
                return None
    return None


def read_reply(text: str) -> tuple[str, Optional[dict[str, Any]], str]:
    """``(kind, payload, detail)`` for one strategist turn. Never raises.

    The shipped protocol's two answers, read in the order it states them: a
    ``DIRECTIVE:`` line followed by one JSON object, or :data:`MARKER_HOLD`.
    A reply that is neither is :data:`REPLY_UNREADABLE` — recorded, and never
    silently read as a hold. Those are different events (one is a decision, the
    other is a failure to state one) and issue #37's lesson is that they must
    never reach the reader as the same object.
    """
    body = text or ""
    marker = body.upper().find(MARKER_DIRECTIVE.upper())
    if marker >= 0:
        span = first_object(body, marker)
        parsed = _load_object(span)
        if parsed is not None:
            return REPLY_DIRECTIVE, parsed, "directive after the marker"
    if MARKER_HOLD.lower() in body.lower():
        return REPLY_HOLD, None, "the strategist wrote the hold marker"
    span = first_object(body)
    parsed = _load_object(span)
    if parsed is not None:
        return REPLY_DIRECTIVE, parsed, "a JSON object with no marker before it"
    if not body.strip():
        return REPLY_UNREADABLE, None, "the strategist returned no content at all"
    return REPLY_UNREADABLE, None, "no hold marker and no parseable JSON object"


def _load_object(span: Optional[str]) -> Optional[dict[str, Any]]:
    if not span:
        return None
    try:
        parsed = json.loads(span)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ── the live strategist, shaped exactly like a scripted planner ───────────────


@dataclass
class ReviewCall:
    """One review boundary's model call, on all four axes' terms.

    Kept apart from :class:`~examples.scope.scopebench.EpisodeRecord` because it
    is *instrument* detail: what the transport did, what the reply looked like,
    how it was read. The graded axes are folded from the rollout, never from
    here.
    """

    review: int
    kind: str
    detail: str
    seconds: float
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    stream_died: bool
    retries: int
    content_chars: int
    reasoning_chars: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "review": self.review,
            "reply_kind": self.kind,
            "reply_detail": self.detail,
            "seconds": round(self.seconds, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "finish_reason": self.finish_reason,
            "stream_died": self.stream_died,
            "retries": self.retries,
            "content_chars": self.content_chars,
            "reasoning_chars": self.reasoning_chars,
        }


@dataclass
class LiveStrategist:
    """One arm's strategist as a :data:`~examples.scope.subordinate.PlannerFn`.

    Being a planner is the point: ``subordinate.execute`` then drives a live arm
    through **byte-identical** machinery to the scripted controls — the same
    register, the same authority scan, the same allocation reader, the same
    refusal codes, the same grader. Nothing about a live arm's *scoring* is new
    code, so a difference between arms cannot be a difference in how they were
    graded.
    """

    seam: ws.WorkerSeam
    episode: ep.Episode
    calls: list[ReviewCall] = field(default_factory=list)

    def __call__(self, context: sub.PlannerContext) -> Optional[Mapping[str, Any]]:
        messages = [
            {"role": "system", "content": f"{SCOPE_AUTHORITY}\n\n{episode_framing(self.episode)}"},
            {"role": "user", "content": review_message(context)},
        ]
        before = len(self.seam.meter.transcript)
        retries_before = self.seam.meter.retries
        try:
            reply = self.seam(messages)
        except ws.WorkerTransportError as failure:
            raise StrategistUnavailable(
                f"{self.episode.id} review {context.review}: {failure}"
            ) from failure
        turn = (
            self.seam.meter.transcript[before] if len(self.seam.meter.transcript) > before else {}
        )
        kind, payload, detail = read_reply(reply.content)
        self.calls.append(
            ReviewCall(
                review=context.review,
                kind=kind,
                detail=detail,
                seconds=float(turn.get("seconds") or 0.0),
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                finish_reason=str(turn.get("finish_reason") or ""),
                stream_died=bool(turn.get("stream_died")),
                retries=self.seam.meter.retries - retries_before,
                content_chars=len(reply.content or ""),
                reasoning_chars=len(reply.reasoning or ""),
            )
        )
        if kind == REPLY_HOLD:
            return None
        # An unreadable reply is an OFFER of nothing. The register refuses it as
        # `scope-directive-incomplete`, which is exactly what it is: the
        # strategist answered, and the answer governs nothing.
        return payload if payload is not None else {}

    @property
    def holds(self) -> int:
        return sum(1 for call in self.calls if call.kind == REPLY_HOLD)

    @property
    def unreadable(self) -> int:
        return sum(1 for call in self.calls if call.kind == REPLY_UNREADABLE)


# ── running one episode, and one arm ──────────────────────────────────────────


def arm_fingerprint(
    arm: str, dial: SeatDialConfig, *, stream: bool = ws.DEFAULT_STREAM
) -> dict[str, Any]:
    """Everything that was held identical, and the one field that was not.

    ``t11`` acceptance criterion 2 asks Stage 2 to pin actor config, tools,
    projection, sampling and budgets byte-identical across arms and to assert it
    **from the run records**. Stage 1 has no actor, but the same discipline
    applies to everything the strategist sees and is sampled at, so this rides
    on every arm's record and ``report`` diffs the two.

    *stream* is a **parameter and not a read of** :data:`~examples.worker_seam.DEFAULT_STREAM`,
    which is what it used to be. A fingerprint whose ``transport`` field reports
    the module default rather than what the run dialled would say ``sse`` for a
    ``--no-stream`` run — a record lying about its own instrument, in the one
    block whose entire job is to say what the instrument was. Nothing committed
    was produced that way (both arms ran on the default), so the fix changes no
    recorded value; it removes the way a future one could be wrong.
    """
    return {
        "seat_role": dial.role,
        "seat_model": dial.model,
        "seat_endpoint": dial.base_url,
        "max_tokens": STRATEGIST_MAX_TOKENS,
        "temperature": STRATEGIST_TEMPERATURE,
        "transport": ws.TRANSPORT_STREAM if stream else ws.TRANSPORT_BLOCKING,
        "stream_queue_width": STREAM_QUEUE_WIDTH,
        "request_timeout_s": ws.REQUEST_TIMEOUT,
        "stream_first_chunk_timeout_s": round(ws.STREAM_FIRST_CHUNK_TIMEOUT, 3),
        "stream_idle_timeout_s": ws.STREAM_IDLE_TIMEOUT,
        "stream_total_timeout_s": round(ws.STREAM_TOTAL_TIMEOUT, 3),
        "max_transport_retries": ws.MAX_TRANSPORT_RETRIES,
        "system_prompt_sha": _digest(SCOPE_AUTHORITY),
        "projector": "examples.scope.subordinate.project",
        "grader": "examples.scope.scopebench.grade",
        "seeds": str(ep.SEEDS_PATH.relative_to(REPO_ROOT)),
        "arm": arm,
    }


def _digest(text: str) -> str:
    """A stable short digest of a prompt, so two arms' framing can be diffed."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_episode(
    arm: str,
    episode: ep.Episode,
    dial: SeatDialConfig,
    *,
    stream: bool = ws.DEFAULT_STREAM,
) -> dict[str, Any]:
    """One episode under a live strategist. Returns the record, or a drop.

    One seam per episode, so ``Meter`` is that episode's own cost block and a
    retry in one episode cannot be charged to another.
    """
    seam = ws.WorkerSeam(
        base_url=dial.base_url,
        model=dial.model,
        api_key=dial.api_key,
        role=f"{arm}-strategist",
        max_tokens=STRATEGIST_MAX_TOKENS,
        temperature=STRATEGIST_TEMPERATURE,
        stream=stream,
        stream_queue_width=STREAM_QUEUE_WIDTH,
    )
    strategist = LiveStrategist(seam=seam, episode=episode)
    solution = orc.solve(episode)
    reasons = orc.invalidity(episode, solution)
    started = time.monotonic()
    try:
        rollout = sub.execute(episode, strategist)
    except StrategistUnavailable as failure:
        return {
            "dropped": True,
            "arm": arm,
            "stage": sb.STAGE_ONE,
            "family": episode.family,
            "episode": episode.id,
            "seed": episode.seed,
            "code": DROPPED_EPISODE_TRANSPORT,
            "detail": str(failure),
            "reviews_completed": len(strategist.calls),
            "calls": [call.to_dict() for call in strategist.calls],
            "cost": seam.meter.to_dict(),
        }
    elapsed = time.monotonic() - started
    cost = sb.Cost(
        model_calls=seam.meter.calls,
        prompt_tokens=seam.meter.prompt_tokens,
        completion_tokens=seam.meter.completion_tokens,
        seconds=elapsed,
    )
    graded = sb.grade(episode, solution, rollout, cost)
    record = sb.EpisodeRecord(
        arm=arm,
        stage=sb.STAGE_ONE,
        family=episode.family,
        episode=episode.id,
        seed=episode.seed,
        planner=f"live:{dial.role}",
        validity=sb.validity_of(reasons, graded["protocol"]),
        outcome=graded["outcome"],
        protocol=graded["protocol"],
        authority=graded["authority"],
        cost=graded["cost"],
        invalid_reasons=tuple(reasons),
    )
    payload = record.to_dict()
    payload["dropped"] = False
    payload["reply_kinds"] = {
        REPLY_DIRECTIVE: sum(1 for call in strategist.calls if call.kind == REPLY_DIRECTIVE),
        REPLY_HOLD: strategist.holds,
        REPLY_UNREADABLE: strategist.unreadable,
    }
    payload["calls"] = [call.to_dict() for call in strategist.calls]
    payload["meter"] = seam.meter.to_dict()
    payload["transcript"] = seam.meter.transcript
    payload["rollout"] = rollout.to_dict()
    return payload


@dataclass
class ArmRun:
    """One arm's whole Stage-1 pass, as it is written to disk."""

    arm: str
    dial: SeatDialConfig
    stream: bool = ws.DEFAULT_STREAM
    records: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """The header written to line one, and the summary printed at the end.

        One shape for both, deliberately. The header lands **before** the first
        episode so a run that dies still says what it was, which means its
        counts are written as zero; they are read back by nobody
        (:func:`_fingerprints` takes ``fingerprint`` and stops) and the single
        shape is what keeps the file this code emits byte-comparable with the
        committed one.
        """
        return {
            "kind": "scopebench-live-stage1",
            "arm": self.arm,
            "stage": sb.STAGE_ONE,
            "dial": self.dial.to_dict(),
            "fingerprint": arm_fingerprint(self.arm, self.dial, stream=self.stream),
            "scored": len(self.records),
            "dropped": len(self.dropped),
        }


def run_arm(
    arm: str,
    dial: SeatDialConfig,
    *,
    out: Path,
    families: Sequence[str] = ep.FIRST_CYCLE,
    per_family: Optional[int] = None,
    stream: bool = ws.DEFAULT_STREAM,
    log: Any = sys.stderr,
) -> ArmRun:
    """Every committed episode under one live strategist, written as it goes."""
    run = ArmRun(arm=arm, dial=dial, stream=stream)
    out.parent.mkdir(parents=True, exist_ok=True)
    episodes = [entry for entry in ep.first_cycle_episodes() if entry.family in families]
    if per_family is not None:
        kept: dict[str, int] = {}
        chosen = []
        for entry in episodes:
            seen = kept.get(entry.family, 0)
            if seen < per_family:
                chosen.append(entry)
                kept[entry.family] = seen + 1
        episodes = chosen
    header = json.dumps({"header": run.to_dict()})
    with out.open("w", encoding="utf-8") as handle:
        handle.write(header + "\n")
        for index, episode in enumerate(episodes, 1):
            started = time.monotonic()
            record = run_episode(arm, episode, dial, stream=stream)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            if record.get("dropped"):
                run.dropped.append(record)
                state = f"DROPPED {record['code']}"
            else:
                run.records.append(record)
                state = (
                    f"regret={record['outcome']['regret']} "
                    f"accept={record['protocol']['protocol_acceptance']} "
                    f"auth={record['authority']['authority_violations']} "
                    f"tok={record['cost']['tokens']}"
                )
            print(
                f"{arm} [{index}/{len(episodes)}] {episode.id:22s} "
                f"{time.monotonic() - started:7.1f}s  {state}",
                file=log,
                flush=True,
            )
    return run


# ── folding live records back into the pre-registered verdict ────────────────


def load_records(paths: Sequence[Path]) -> tuple[list[sb.EpisodeRecord], list[dict[str, Any]]]:
    """``(scored records, dropped episodes)`` from one or more raw JSONL files."""
    records: list[sb.EpisodeRecord] = []
    dropped: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if "header" in entry:
                continue
            if entry.get("dropped"):
                dropped.append(entry)
                continue
            records.append(
                sb.EpisodeRecord(
                    arm=entry["arm"],
                    stage=entry["stage"],
                    family=entry["family"],
                    episode=entry["episode"],
                    seed=entry["seed"],
                    planner=entry["planner"],
                    validity=entry["validity"],
                    outcome=entry["outcome"],
                    protocol=entry["protocol"],
                    authority=entry["authority"],
                    cost=entry["cost"],
                    invalid_reasons=tuple(entry.get("invalid_reasons", ())),
                )
            )
    return records, dropped


#: What :func:`arm_diagnostics` tallies from the raw records, by axis. Every
#: number here is **reported, never scored**: none of it reaches
#: :func:`~examples.scope.scopebench.verdict`, which takes only the graded
#: summary. It exists so that a cell which comes back ``void-protocol`` can be
#: read for *why* rather than left as a bare word.
_DIAGNOSTIC_AXES: Mapping[str, tuple[str, ...]] = {
    "protocol": ("directives_offered", "holds", "directives_accepted", "directives_refused"),
    "authority": ("authority_violations",),
    "cost": ("model_calls", "prompt_tokens", "completion_tokens", "tokens", "seconds"),
}


def arm_diagnostics(raw: Sequence[Path]) -> dict[str, Any]:
    """Per-arm instrument detail, folded from the raw records.

    The verdict rule reads mean regret, operational success, authority
    violations and tokens, and nothing else. That is deliberate and it is not
    enough to *write up* a result: an arm whose protocol acceptance collapsed
    needs its refusal codes named, and a hold rate is the single most legible
    signal on the non-intervention family. All of it rides beside the verdict;
    none of it can move it.
    """
    out: dict[str, Any] = {}
    for path in raw:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if "header" in entry or entry.get("dropped"):
                continue
            arm = out.setdefault(
                entry["arm"],
                {
                    "episodes": 0,
                    "validity": {},
                    "reply_kinds": {},
                    "refusals_by_code": {},
                    "unexecutable_by_code": {},
                    "violations_by_code": {},
                    "finish_reasons": {},
                    "stream_deaths": 0,
                    "truncated": 0,
                    "truncated_and_unreadable": 0,
                    "retries": 0,
                    "held_at_non_intervention": 0,
                    "trap_taken": 0,
                    "churn": 0,
                    **{name: 0 for names in _DIAGNOSTIC_AXES.values() for name in names},
                },
            )
            arm["episodes"] += 1
            _bump(arm["validity"], entry["validity"])
            for axis, names in _DIAGNOSTIC_AXES.items():
                for name in names:
                    arm[name] += entry[axis].get(name) or 0
            for field_name in ("refusals_by_code", "unexecutable_by_code"):
                _merge(arm[field_name], entry["protocol"].get(field_name) or {})
            _merge(arm["violations_by_code"], entry["authority"].get("violations_by_code") or {})
            _merge(arm["reply_kinds"], entry.get("reply_kinds") or {})
            meter = entry.get("meter") or {}
            _merge(arm["finish_reasons"], meter.get("finish_reasons") or {})
            arm["stream_deaths"] += meter.get("stream_deaths") or 0
            arm["truncated"] += meter.get("truncated") or 0
            arm["retries"] += meter.get("retries") or 0
            for name in ("held_at_non_intervention", "trap_taken"):
                arm[name] += 1 if entry["outcome"].get(name) else 0
            arm["churn"] += entry["outcome"].get("churn") or 0
            # The cross-tab that keeps an INSTRUMENT event out of a graded axis.
            # A turn cut at `max_tokens` mid-JSON arrives at the reader as prose
            # with no parseable object, which the register then refuses — so an
            # unreadable rate quoted without this number would report the budget
            # as the strategist's inability to phrase a directive. Issue #37's
            # lesson at the axis boundary rather than at the response object.
            arm["truncated_and_unreadable"] += sum(
                1
                for call in entry.get("calls") or []
                if call.get("finish_reason") == ws.FINISH_TRUNCATED
                and call.get("reply_kind") == REPLY_UNREADABLE
            )
    for arm in out.values():
        offered = arm["directives_offered"]
        arm["acceptance"] = None if not offered else round(arm["directives_accepted"] / offered, 4)
    return out


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _merge(counter: dict[str, int], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        counter[key] = counter.get(key, 0) + int(value or 0)


def _stage_one_reason(arm: str, cell: Optional[Mapping[str, Any]]) -> str:
    """Why *arm* has no Stage-1 number for this family. **Three different whys.**

    Condition 7's obligation is that every absent cell is *explained*, and an
    explanation that is not true is worse than none. Three cases reach here and
    they are not the same fact:

    * the cell was **dialled and voided** — the arm answered and its directives
      were not admissible, so :data:`~examples.scope.scopebench.PROTOCOL_FLOOR`
      voided it (§10). "The arm was not measured on strategy" is the honest
      reading, and it is emphatically *not* "nothing was run";
    * the arm is ``A1``, which has **no Stage-1 cell by design** — the
      pre-registration's own reason, still true and quoted from it;
    * the cell was **never reached** in this cycle.

    ``scopebench.STAGE_ONE_ABSENT``'s ``A2``/``A3`` entries are deliberately not
    reused for the first or third: they read "no live dial has been run: t9
    ships the scaffold and its deterministic controls only", which was true when
    it was written and stops being true the moment ``t11`` dials. Quoting it
    afterwards would explain a real gap with a stale sentence — the same drift
    this function exists to avoid one stage up.
    """
    if cell is not None and cell.get("voided"):
        return (
            f"dialled and voided: {cell['voided']} of {cell['voided']} episodes fell below "
            f"the pre-registered protocol floor of {sb.PROTOCOL_FLOOR}, so the cell is "
            f"{sb.VOID_PROTOCOL} (§10) — the arm was not measured on strategy here, and "
            "scoring it would report the wrong failure. This is NOT an undialled cell"
        )
    if arm == sb.ARM_A1:
        return sb.STAGE_ONE_ABSENT[arm]
    return (
        "not reached in this cycle: the Stage-1 series ran arm by arm in the committed "
        "family order, and this cell is past where the run stopped"
    )


def summarise_live(records: Sequence[sb.EpisodeRecord]) -> dict[str, Any]:
    """:func:`~examples.scope.scopebench.summarise`, plus ``t11``'s own absences.

    Deliberately not ``scopebench.stage_one_summary``: its absence reasons are
    ``t9``'s, written before any dial, and quoting them after ``t11`` has run
    would report a stale explanation for a real gap. Every absence claimed here
    is checked against the scored cells first, so an absence is never asserted
    for a cell that has records, and :func:`_stage_one_reason` distinguishes the
    three different ways a Stage-1 cell can be empty.
    """
    summary = sb.summarise(records)
    absent = dict(summary["absent"])
    scored = {key for key, cell in summary["cells"].items() if cell["n"]}
    for arm in sb.ARM_ORDER:
        for family in ep.FIRST_CYCLE:
            one = f"{arm}|{sb.STAGE_ONE}|{family}"
            if one not in scored:
                absent[one] = _stage_one_reason(arm, summary["cells"].get(one))
            two = f"{arm}|{sb.STAGE_TWO}|{family}"
            if two not in scored:
                absent[two] = STAGE_TWO_ABSENT
    summary["absent"] = absent
    return summary


def _fingerprints(paths: Sequence[Path]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if "header" in entry:
                out[entry["header"]["arm"]] = entry["header"]["fingerprint"]
                break
    return out


def _fingerprint_diff(fingerprints: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Which fingerprint keys differ across arms, and which are identical.

    The assertion ``t11`` owes, made from the records: the only keys allowed to
    differ are the seat's own (``arm`` names the row, not the configuration).
    """
    arms = sorted(fingerprints)
    if len(arms) < 2:
        return {"arms": arms, "differing": [], "identical": [], "checked": False}
    keys = sorted(set().union(*(set(fingerprints[arm]) for arm in arms)))
    differing = [
        key for key in keys if len({json.dumps(fingerprints[a].get(key)) for a in arms}) > 1
    ]
    return {
        "arms": arms,
        "differing": differing,
        "identical": [key for key in keys if key not in differing],
        "checked": True,
        # The keys a seat difference is ALLOWED to move. Reported rather than
        # asserted here: `tests/test_scopebench_live.py` makes the assertion, and
        # a run record that quietly asserted its own validity would be the arm
        # marking its own homework. `seat_endpoint` is on the list because a
        # proxied role may advertise a different endpoint; on this rig both
        # resolve to the same gateway, so it does not in fact move.
        "allowed_to_differ": ["arm", "seat_endpoint", "seat_model", "seat_role"],
    }


# ── rendering ─────────────────────────────────────────────────────────────────


def _regret_table(summary: Mapping[str, Any], arms: Sequence[str]) -> str:
    width = max(len(name) for name in arms)
    lines = [" " * (width + 2) + "".join(f"{family[:12]:>14s}" for family in ep.FIRST_CYCLE)]
    for arm in arms:
        row = [f"{arm:<{width}}  "]
        for family in ep.FIRST_CYCLE:
            cell = summary["cells"].get(f"{arm}|{sb.STAGE_ONE}|{family}")
            value = None if cell is None else cell["mean_regret"]
            row.append(f"{'—':>14s}" if value is None else f"{value:14.2f}")
        lines.append("".join(row))
    return "\n".join(lines)


#: Which rows the mean-regret table shows, in this order: the pre-registered
#: baseline stand-in first, the two live arms, then the scripted control panel
#: as the instrument calibration §11 already published. Controls are labelled so
#: no reader can mistake one for an arm.
_TABLE_ROWS: tuple[tuple[str, str], ...] = (
    (sb.ARM_A0, "`A0` (baseline stand-in: scripted `greedy`)"),
    (sb.ARM_A2, "`A2` (strategist = worker, live)"),
    (sb.ARM_A3, "`A3` (strategist = cortex, live)"),
    (sb.control_arm(sub.PLANNER_NONE), "control `none`"),
    (sb.control_arm(sub.PLANNER_RANDOM), "control `random`"),
    (sb.control_arm(sub.PLANNER_STATIC), "control `static`"),
    (sb.control_arm(sub.PLANNER_REVISING), "control `revising`"),
    (sb.control_arm(sub.PLANNER_ORACLE), "control `oracle` (unachievable ceiling)"),
)


def _markdown_verdict(payload: Mapping[str, Any]) -> str:
    """The seven conditions, per arm, each with its own status and detail.

    The pre-registration's publication rule (§13) requires every result to name
    which of the seven it satisfies, fails, or could not evaluate — so this is
    the one table the write-up cannot be written without, and it is generated
    from the same ``verdict()`` call the harness makes rather than retyped.
    """
    out: list[str] = []
    for arm, report in payload["verdicts"].items():
        out += [
            "",
            f"### `{arm}` — **{report['verdict']}**",
            "",
            "| # | condition | status | what the record says |",
            "|---|---|---|---|",
        ]
        for index, name in enumerate(sb.VERDICT_CONDITIONS, 1):
            entry = report["conditions"][name]
            rule = sb.CONDITION_WHY[name]
            out.append(f"| {index} | {rule} | **{entry['status']}** | {entry['detail']} |")
    return "\n".join(out).lstrip("\n")


def _markdown_tables(payload: Mapping[str, Any]) -> str:
    """The write-up's tables, generated from the record rather than transcribed.

    A number retyped into prose is a second source of truth that drifts on the
    first re-run nobody thought to mirror — the same defect
    ``tests/rate_config.py`` exists to stop one layer down. So the results doc
    pastes this output.
    """
    cells = payload["cells"]
    out: list[str] = ["## The verdict, condition by condition", "", _markdown_verdict(payload), ""]
    out += [
        "## The numbers",
        "",
        "### Mean regret by family (lower is better; `—` = no scored cell)",
        "",
    ]
    head = "| arm | " + " | ".join(f"`{name}`" for name in ep.FIRST_CYCLE) + " |"
    out += [head, "|" + "---|" * (len(ep.FIRST_CYCLE) + 1)]
    for arm, label in _TABLE_ROWS:
        row = [label]
        for family in ep.FIRST_CYCLE:
            cell = cells.get(f"{arm}|{sb.STAGE_ONE}|{family}")
            value = None if cell is None else cell["mean_regret"]
            row.append("—" if value is None else f"{value:.1f}")
        out.append("| " + " | ".join(row) + " |")

    out += ["", "### Per-family sign margin against `A0` (condition 2's own arithmetic)", ""]
    out += [
        "| arm | family | n paired | wins | losses | ties | margin | counts? |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm, rows in payload["sign_margins"].items():
        for family, row in rows.items():
            out.append(
                f"| `{arm}` | `{family}` | {row['n_paired']} | {row['wins']} | "
                f"{row['losses']} | {row['ties']} | {row['margin']:+d} | "
                f"{'yes' if row['counts'] else 'no'} |"
            )

    out += ["", "### Instrument detail — reported beside the verdict, never inside it", ""]
    diagnostics = payload["diagnostics"]
    out += [
        "| arm | episodes | valid | void-protocol | offered | held | accepted | acceptance | "
        "unreadable | truncated | of which unreadable | authority | tokens |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in sorted(diagnostics):
        entry = diagnostics[arm]
        out.append(
            f"| `{arm}` | {entry['episodes']} | {entry['validity'].get(sb.VALID, 0)} | "
            f"{entry['validity'].get(sb.VOID_PROTOCOL, 0)} | {entry['directives_offered']} | "
            f"{entry['holds']} | {entry['directives_accepted']} | {entry['acceptance']} | "
            f"{entry['reply_kinds'].get(REPLY_UNREADABLE, 0)} | {entry['truncated']} | "
            f"{entry['truncated_and_unreadable']} | "
            f"{entry['authority_violations']} | {entry['tokens']} |"
        )

    out += ["", "### Refusal and unexecutable codes, named", ""]
    out += ["| arm | code | count |", "|---|---|---|"]
    for arm in sorted(diagnostics):
        for field_name in ("refusals_by_code", "unexecutable_by_code", "violations_by_code"):
            for code, count in sorted(diagnostics[arm][field_name].items()):
                if count:
                    out.append(f"| `{arm}` | `{code}` | {count} |")
    return "\n".join(out)


def _render_report(payload: Mapping[str, Any]) -> str:
    lines = ["Stage 1 — mean regret by family (lower is better)", ""]
    lines.append(payload["regret_table"])
    lines += ["", "Verdicts:"]
    for arm, report in payload["verdicts"].items():
        lines.append(f"  {arm}: {report['verdict']}")
        for name in sb.VERDICT_CONDITIONS:
            entry = report["conditions"][name]
            lines.append(f"    {name:34s} {entry['status']:8s} {entry['detail']}")
    lines += ["", f"dropped episodes: {len(payload['dropped'])}"]
    lines.append(f"declared absent cells: {len(payload['absent'])}")
    lines.append(f"fingerprint differing keys: {payload['fingerprint_diff']['differing']}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────


_VERBS: Mapping[str, str] = {
    "seats": "resolve every arm's strategist seat from the gateway's /capabilities",
    "smoke": "one review on one episode, printing the raw reply — wiring, not data",
    "stage1": "run the pre-registered Stage-1 series for one arm",
    "report": "fold the raw records into the seven-condition verdict",
    "tables": "the write-up's markdown tables, generated from the same records",
}


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="emit JSON instead of text")
    shared.add_argument("--gateway", default=DEFAULT_GATEWAY, help="lobes gateway base URL")
    shared.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        default=ws.DEFAULT_STREAM,
        help="dial with the blocking transport instead of SSE (deviation d3's escape hatch)",
    )
    parser = argparse.ArgumentParser(
        prog="scopebench_live",
        description="ScopeBench, dialled — the pre-registered series (plan task t11).",
        parents=[shared],
    )
    subs = parser.add_subparsers(dest="verb", required=True)
    for verb, help_text in _VERBS.items():
        entry = subs.add_parser(verb, help=help_text, parents=[shared])
        if verb in ("smoke", "stage1"):
            entry.add_argument("--arm", choices=LIVE_ARMS, required=True)
            entry.add_argument("--out", default=None, help="raw JSONL path")
        if verb == "stage1":
            entry.add_argument("--per-family", type=int, default=None, help="pilot: cap per family")
            entry.add_argument(
                "--families", default=None, help="comma-separated family subset (pilot)"
            )
        if verb in ("report", "tables"):
            entry.add_argument("--raw", nargs="*", default=None, help="raw JSONL files to fold in")
            entry.add_argument("--out", default=None, help="write the output here")
    return parser


def _api_key() -> str:
    """The bearer token, from the one variable every harness on this rig reads.

    ``worker_seam.resolve_worker_config`` is not reused: it refuses without an
    explicit URL and model, and here both come from ``/capabilities`` rather
    than from a flag. The variable is the same one, imported rather than
    retyped, so one exported key still drives every harness.
    """
    key = (os.environ.get(ws.API_KEY_ENV) or "").strip()
    if not key:
        raise SystemExit(f"error: no {ws.API_KEY_ENV} in the environment")
    return key


def _seats_payload(args: argparse.Namespace) -> dict[str, Any]:
    capabilities = fetch_capabilities(args.gateway, timeout=ws.STREAM_FIRST_CHUNK_TIMEOUT)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CAPABILITIES_PATH.write_text(json.dumps(capabilities, indent=2) + "\n", encoding="utf-8")
    resolution = st.resolve_seats(capabilities)
    out: dict[str, Any] = {"resolution": resolution.to_dict(), "arms": {}}
    for arm in sb.ARM_ORDER:
        dial, _degradations = resolve_dial(arm, capabilities, gateway=args.gateway, api_key="")
        out["arms"][arm] = {
            "seats": dict(sb.ARMS[arm].seats),
            "strategist_dial": None if dial is None else dial.to_dict(),
        }
    return out


def _run_smoke(args: argparse.Namespace) -> int:
    capabilities = fetch_capabilities(args.gateway, timeout=ws.STREAM_FIRST_CHUNK_TIMEOUT)
    dial, degradations = resolve_dial(
        args.arm, capabilities, gateway=args.gateway, api_key=_api_key()
    )
    if dial is None:
        print(json.dumps([entry.to_dict() for entry in degradations], indent=2), file=sys.stderr)
        return 2
    episode = ep.first_cycle_episodes()[0]
    seam = ws.WorkerSeam(
        base_url=dial.base_url,
        model=dial.model,
        api_key=dial.api_key,
        role=f"{args.arm}-smoke",
        max_tokens=STRATEGIST_MAX_TOKENS,
        temperature=STRATEGIST_TEMPERATURE,
        stream=args.stream,
        stream_queue_width=STREAM_QUEUE_WIDTH,
    )
    strategist = LiveStrategist(seam=seam, episode=episode)
    register = sub.Register(sub.default_directive(episode))
    state = orc.initial_state(episode)
    context = sub.PlannerContext(
        episode=episode,
        state=state,
        review=0,
        active=register.active,
        snapshot=sub.project(episode, state, 0, register.active),
        next_version=register.version + 1,
    )
    payload = strategist(context)
    report = {
        "kind": "scopebench-live-smoke",
        "note": "instrument check, not data",
        "arm": args.arm,
        "dial": dial.to_dict(),
        "fingerprint": arm_fingerprint(args.arm, dial, stream=args.stream),
        "episode": episode.id,
        "reply": None if not strategist.calls else strategist.calls[0].to_dict(),
        "payload": payload,
        "transcript": seam.meter.transcript,
        "meter": seam.meter.to_dict(),
    }
    if payload is not None:
        directive, refusal = sub.directive_from_payload(payload)
        report["refusal"] = None if refusal is None else refusal.to_dict()
        if directive is not None:
            second = register.offer(directive)
            report["register_refusal"] = None if second is None else second.to_dict()
            allocation, unexecutable = sub.allocation_of(episode, state, register.active)
            report["allocation"] = [list(pair) for pair in allocation]
            report["unexecutable"] = [entry.to_dict() for entry in unexecutable]
        report["authority"] = [entry.to_dict() for entry in sub.authority_violations(payload)]
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _run_stage_one(args: argparse.Namespace) -> int:
    capabilities = fetch_capabilities(args.gateway, timeout=ws.STREAM_FIRST_CHUNK_TIMEOUT)
    dial, degradations = resolve_dial(
        args.arm, capabilities, gateway=args.gateway, api_key=_api_key()
    )
    if dial is None:
        print(json.dumps([entry.to_dict() for entry in degradations], indent=2), file=sys.stderr)
        return 2
    families = ep.FIRST_CYCLE
    if args.families:
        families = tuple(name.strip() for name in args.families.split(",") if name.strip())
    out = Path(args.out) if args.out else RAW_DIR / f"{args.arm}-stage1.jsonl"
    run = run_arm(
        args.arm,
        dial,
        out=out,
        families=families,
        per_family=args.per_family,
        stream=args.stream,
    )
    print(json.dumps(run.to_dict(), indent=2))
    return 0


def _report_payload(raw: Sequence[Path]) -> dict[str, Any]:
    # Every scripted Stage-1 record: the A0 stand-in and the whole control panel.
    # They are recomputed rather than read from disk because they are
    # deterministic — `run_stage_one` calls no model — so there is no committed
    # artifact for them to drift away from.
    scripted = sb.run_stage_one()
    live, dropped = load_records(raw)
    summary = summarise_live([*scripted, *live])
    arms = sorted({cell["arm"] for cell in summary["cells"].values()})
    verdicts = {
        arm: sb.verdict(summary, arm).to_dict() for arm in (sb.ARM_A1, sb.ARM_A2, sb.ARM_A3)
    }
    return {
        "kind": "scopebench-live-report",
        "stage": sb.STAGE_ONE,
        "raw": [str(path.relative_to(REPO_ROOT)) for path in raw],
        "cells": summary["cells"],
        "absent": summary["absent"],
        "invalid_episodes": summary["invalid_episodes"],
        "void_cells": summary["void_cells"],
        "dropped": dropped,
        "verdicts": verdicts,
        "diagnostics": arm_diagnostics(raw),
        "sign_margins": _sign_margins(summary),
        "fingerprint_diff": _fingerprint_diff(_fingerprints(raw)),
        "regret_table": _regret_table(summary, arms),
    }


def _sign_margins(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Per-family ``(wins, losses, ties)`` against the baseline, for every arm.

    Condition 1 and condition 2 both turn on a sign margin clearing
    :data:`~examples.scope.scopebench.MIN_WIN_MARGIN`, and a write-up that
    reported only the means would hide *why* a family did or did not count. This
    recomputes the same pairing the condition functions use — episode by episode
    and by id, never by position — and reports it.
    """
    out: dict[str, Any] = {}
    for arm in (sb.ARM_A1, sb.ARM_A2, sb.ARM_A3):
        rows: dict[str, Any] = {}
        for family in ep.FIRST_CYCLE:
            mine = summary["cells"].get(f"{arm}|{sb.STAGE_ONE}|{family}")
            base = summary["cells"].get(f"{sb.BASELINE_ARM}|{sb.STAGE_ONE}|{family}")
            if not mine or not base:
                continue
            by_id = {row["episode"]: row["regret"] for row in base["episodes"]}
            paired = [row for row in mine["episodes"] if row["episode"] in by_id]
            wins = sum(1 for row in paired if row["regret"] < by_id[row["episode"]])
            losses = sum(1 for row in paired if row["regret"] > by_id[row["episode"]])
            rows[family] = {
                "n_paired": len(paired),
                "wins": wins,
                "losses": losses,
                "ties": len(paired) - wins - losses,
                "margin": wins - losses,
                "mean_regret": mine["mean_regret"],
                "baseline_mean_regret": base["mean_regret"],
                "counts": mine["mean_regret"] is not None
                and base["mean_regret"] is not None
                and mine["mean_regret"] < base["mean_regret"]
                and wins - losses >= sb.MIN_WIN_MARGIN,
            }
        out[arm] = rows
    return out


def _run_report(args: argparse.Namespace, *, tables: bool = False) -> int:
    raw = (
        [Path(entry) for entry in args.raw] if args.raw else sorted(RAW_DIR.glob("*-stage1.jsonl"))
    )
    payload = _report_payload(raw)
    if tables:
        text = _markdown_tables(payload)
    else:
        text = json.dumps(payload, indent=2) if args.json else _render_report(payload)
    if args.out:
        body = json.dumps(payload, indent=2) if not tables else text
        Path(args.out).write_text(body + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verb == "seats":
        payload = _seats_payload(args)
        print(json.dumps(payload, indent=2))
        return 0
    if args.verb == "smoke":
        return _run_smoke(args)
    if args.verb == "stage1":
        return _run_stage_one(args)
    return _run_report(args, tables=args.verb == "tables")


if __name__ == "__main__":
    raise SystemExit(main())
