"""Three-tier example host: senses relays, the worker acts, the cortex configures.

Plan task ``t12`` of ``config-not-minds-strategist`` (spec claims ``c22``,
``c23``, honesty condition ``h17``). The operator's architecture decision
``c26``, wired:

===============  =============  ==================================================
tier             lobes role     what it does here
===============  =============  ==================================================
interaction      ``senses``     relays the world INTO the embodiment (verbatim
                                intake) and the inner state back OUT. Calls no
                                tool, touches no world, decides nothing.
operation        ``worker``     **the acting loop** — ``embodiment.loop.run``
                                driven through
                                :func:`embodiment.config_run.run_configured`.
                                Narrower scope, and **unaware of the tier above**:
                                nothing the strategist writes is delivered to it
                                as text. It simply runs under different
                                configuration.
strategy         ``cortex``     the **strategist** — background *configuration*
                                changes through
                                :class:`embodiment.config_runner.ConfigRunner`.
                                Never addresses the operator, never calls a tool.
===============  =============  ==================================================

colleague exemplifies the two-tier senses→cortex loop. This is the three-tier
variant, so the cortex moves *up* from acting to configuring and the worker
takes the acting seat. That promotion is the one the shipped worker-promotion
gate guards (``tests/test_governance.py``), and nothing here flips it: this host
is an example, not a rig default.

What the operator experiences is one teammate. What the trace shows is three
roles across two model families. Both are true at once, and the second is an
architectural fact rather than a claim about cognition (constraint ``C2``).

Running it
----------
Hermetic by default — scripted seams, no network, no gateway, no key::

    uv run python examples/three_tier.py talk --script examples/three_tier_script.txt

Against the real rig, one flag::

    export COLLEAGUE_API_KEY=…
    uv run python examples/three_tier.py seats --live
    uv run python examples/three_tier.py talk --live \\
        --script script.txt --events run.jsonl --transcript run.md --report run.json

The **matched ungoverned control** replays the identical script with the
strategist absent and *everything else held*::

    uv run python examples/three_tier.py talk --script script.txt --no-strategist

Read that flag precisely, because "ungoverned" has two honest meanings and this
host ships both:

* ``--no-strategist`` is the **matched** control. The configuration lane is
  still wired, the worker still runs under the same seeded
  :class:`~embodiment.config_lifecycle.SeatConfig`, the same ledger records the
  same baseline — only the tier that *changes* configuration is absent. The two
  arms therefore start byte-identical and differ in exactly one thing, which is
  what makes a difference between them attributable.
* ``--ungoverned`` passes no governor at all, so the drive is byte-identical to
  a bare :func:`embodiment.loop.run`. That is the *layer* control (it removes
  the lane as well as the strategist), and it is the weaker comparison for
  measuring the strategist — it is here because ``t11``'s ``A2``-class gap
  showed what it costs to not have one.

Clocks: this host introduces NONE
---------------------------------
Every dial goes through ``examples/worker_seam.py``'s
:class:`~examples.worker_seam.WorkerSeam`, whose four bounds are derived from
``docs/live-test-results/timeout-rate-measurements.json`` and pinned by
``tests/test_timeout_bounds.py``. **Streaming is the transport** (`ws.DEFAULT_STREAM`),
so both phases are bounded and both bounds are derived: a queue-aware
time-to-first-chunk bound (``STREAM_FIRST_CHUNK_TIMEOUT``, which
:func:`~examples.worker_seam.derive_first_chunk_timeout` grows with the width
this host actually dials) and, armed on the socket only once the first chunk
lands, an inter-chunk idle bound (``STREAM_IDLE_TIMEOUT``). This module defines
no module-level timeout, deadline or interval constant of its own — it
contributes only ``(role, budget)`` pairs, declared in that test's ``CLOCKS``
table so CI keeps them honest. ``tests/test_three_tier.py`` asserts the absence
rather than trusting this paragraph.

The three-tier shape inverts the rig's latency profile — the acting seat is now
the *proxied* one and the local model has retreated to background configuration
— which is exactly why the 0.11.0 clock discipline applies to the acting path
from the first line rather than as a later hardening pass.

.. _seam-traps:

The seam traps — what a stranger hits wiring this from documented seams alone
-----------------------------------------------------------------------------
Task ``t12``'s real deliverable. Issue #62 is the shape: a correct-looking host
wiring produced a tier that *appeared incapable*, because a runner's issued
chain started empty and nothing in the package said you had to seed it. Live
session 1 found it the hard way.

So this host was wired from README, module docstrings, ``pydoc`` and ``explain``
output only, and **every question that surface could not answer was answered by
running the seam rather than by opening its source**. Eight such questions came
up. Each is recorded below as data (:data:`SEAM_TRAPS`), reproduced
behaviourally in ``tests/test_three_tier.py`` so it cannot rot into prose, and
mitigated at the exact line of this file where the mitigation lives.

Two of them are true #62-class traps — a correct-looking wiring yields a
strategist that proposes nothing, with no error anywhere:

* **T1 — the review boundary is a TOOL-STEP boundary.** A drive whose actor
  answers in one turn without calling a tool projects nothing, reviews nothing
  and proposes nothing. With ``governor.armed`` reading ``True``, every counter
  reads zero. A conversational host answers many turns exactly that way.
* **T2 — the review cadence outlives the drive whose step index feeds it.**
  ``ConfigLimits.review_gap`` defaults to 2 acting steps, and the step index
  ``run_configured`` supplies restarts at 1 every drive while the runner's
  cadence memory does not. Measured on the documented seam: **six drives of two
  steps each produced one review, 11 of 12 snapshots skipped**. The fix is one
  constructor argument — ``ConfigLimits(review_gap=0)`` — that nothing in
  ``ConfigLimits``, ``ConfigRunner``, ``ConfigGovernor`` or ``run_configured``
  tells a multi-drive host to pass. This host passes it (see
  :func:`build_strategist`).

The rest are seam gaps rather than traps, and are documented in
:data:`SEAM_TRAPS` with their mitigations. None of them required reading
package source — but only because black-box probing stood in for it, and a
stranger who trusted the documented surface would have shipped a dead tier.

Threat model, stated rather than implied (constraint ``C2``)
------------------------------------------------------------
This is **software presence**, not a body, and this host is **not a sandbox**.
Its world is an in-process dictionary of potting-shed zones and a care log —
there is no shell, no filesystem tool, no network tool and no process spawn on
the worker's surface, so the containment here is that the tool surface offers
nothing dangerous, not that anything is confined. The strategist configures
prompts, knowledge and *selections among capability ids this host declared*; it
cannot mint a capability, and the host's own executor stays the only thing that
can run one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from embodiment import perception  # noqa: E402
from embodiment.capability import Capability, CapabilityCatalog  # noqa: E402
from embodiment.config_change import (  # noqa: E402
    ORIGIN_HOST,
    ORIGIN_WORKER,
    TARGET_SENSES_KNOWLEDGE,
)
from embodiment.config_ledger import ConfigLedger, ConfigPersistence  # noqa: E402
from embodiment.config_lifecycle import (  # noqa: E402
    ConfigLifecycle,
    PromptSection,
    SeatConfig,
    VerificationRequest,
    VerificationResult,
    compose_prompt,
)
from embodiment.config_report import build_config_report  # noqa: E402
from embodiment.config_revert import ConfigBaseline, RatchetGuard, revert_to_baseline  # noqa: E402
from embodiment.config_review import ConfigControls, ConfigSnapshot  # noqa: E402
from embodiment.config_run import ConfigGovernor, run_configured  # noqa: E402
from embodiment.config_runner import ConfigLimits, ConfigRunner  # noqa: E402
from embodiment.contract import ModelResponse, Task  # noqa: E402
from embodiment.identity import resolve_identity  # noqa: E402
from embodiment.loop import LoopAborted, ToolError, ToolOutcome  # noqa: E402
from embodiment.senses_text import KNOWLEDGE_ATTRIBUTION, SENSES_GROUNDING  # noqa: E402
from examples import worker_seam as ws  # noqa: E402

__all__ = [
    "CORTEX_ROLE",
    "SENSES_ROLE",
    "WORKER_ROLE",
    "ACTOR_MAX_TOKENS",
    "SENSES_MAX_TOKENS",
    "STRATEGIST_MAX_TOKENS",
    "STREAM_QUEUE_WIDTH",
    "TEMPERATURE",
    "SEAM_TRAPS",
    "SeamTrap",
    "SeatDial",
    "SeatResolution",
    "Shed",
    "WorkerTools",
    "ThreeTierSession",
    "Timeline",
    "build_catalog",
    "build_parser",
    "build_seam",
    "build_strategist",
    "build_verifier",
    "compose_senses_prompt",
    "fetch_capabilities",
    "main",
    "resolve_seats",
    "scripted_actor",
    "scripted_senses",
    "scripted_strategist",
    "advance_and_record",
    "seed_changes",
    "session_projector",
]

# ── roles, resolved by NAME and never by parsing a model id ──────────────────

#: The gateway serving all three roles. ``lobes`` on this rig.
DEFAULT_GATEWAY = "http://localhost:8001"

SENSES_ROLE = "senses"
WORKER_ROLE = "worker"
CORTEX_ROLE = "cortex"

#: seat name (the configuration lane's vocabulary) -> lobes role name.
SEAT_ROLES: Mapping[str, str] = {"worker": WORKER_ROLE, "senses": SENSES_ROLE}

# ── budgets: (role, budget) pairs, declared in tests/test_timeout_bounds.py ──

#: The acting seat's completion budget. ``d16``'s measured floor
#: (`docs/live-test-results/arena-budget.md`): at the shipped 2048 default, 6.0%
#: of completions were truncated with ZERO degradations recorded, because
#: ``ModelResponse`` carries no ``finish_reason`` (embodiment#37). That trap
#: transfers to whichever seat acts, and here the worker is the seat that acts.
ACTOR_MAX_TOKENS = 16000

#: The strategist's budget. A review is at most ``ConfigControls.max_turns``
#: tools-off turns of roughly a thousand reasoning tokens plus a short JSON
#: batch, but the cortex is a thinking model and a stingy budget returns empty
#: content mid-thought, which reads as a strategist with nothing to say.
STRATEGIST_MAX_TOKENS = 16000

#: The senses seat's budget, and the one place ``d16``'s raise-the-budget
#: argument does NOT transfer — the reasoning is `examples/scope_live_session.py`'s,
#: cited rather than re-derived. This tier speaks to a person in real time, so
#: its two failure modes are asymmetric: too small truncates visibly and is said
#: aloud, too large buys minutes of dead air while the seat generates. Silence
#: that looks like attention is precisely the outcome constraint C3 names worst.
SENSES_MAX_TOKENS = 1024

#: Requests this host may have in flight at once — senses on the REPL thread,
#: the worker on the drive thread, the strategist on its own. Handed to
#: ``WorkerSeam`` so the time-to-first-chunk bound is derived at the width
#: actually dialled rather than at the module constant's width of 1.
#: Over-counting is the direction ``derive_first_chunk_timeout`` calls safe.
STREAM_QUEUE_WIDTH = 3

#: The repo's standing sampling temperature for measured lanes.
TEMPERATURE = 0.3


# ── the seam traps, as data rather than as prose ─────────────────────────────


@dataclass(frozen=True)
class SeamTrap:
    """One thing the documented surface did not say, and what it costs.

    ``t12``'s instruction: *if you need to read package source to get it
    working, that is a #62-class trap and documenting it at the seam is part of
    this task.* These are recorded as data so ``tests/test_three_tier.py`` can
    reproduce each one against the real package rather than assert that a
    paragraph exists — a trap nobody can demonstrate is a trap nobody has.
    """

    id: str
    seam: str
    symptom: str
    fix: str
    #: ``True`` when a correct-looking wiring silently produces an incapable
    #: tier — the #62 shape itself, as opposed to a merely under-documented seam.
    incapable_tier: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seam": self.seam,
            "symptom": self.symptom,
            "fix": self.fix,
            "incapable_tier": self.incapable_tier,
        }


SEAM_TRAPS: tuple[SeamTrap, ...] = (
    SeamTrap(
        id="T1",
        seam="embodiment.config_run.run_configured / ConfigGovernor.projector",
        symptom=(
            "the review boundary is a TOOL-STEP boundary. A drive whose actor answers in "
            "one turn without calling a tool projects nothing, reviews nothing and proposes "
            "nothing — every counter reads zero while governor.armed reads True. Neither "
            "run_configured's nor ConfigGovernor's docstring names the unit of a boundary."
        ),
        fix=(
            "expect strategist activity to scale with tool steps, not with conversation "
            "turns, and read counts['boundaries_projected'] rather than outcome.applied "
            "when asking whether the tier is alive."
        ),
        incapable_tier=True,
    ),
    SeamTrap(
        id="T2",
        seam="embodiment.config_runner.ConfigLimits.review_gap",
        symptom=(
            "review_gap defaults to 2 acting steps and the step index run_configured "
            "supplies restarts at 1 every drive, while the runner's cadence memory persists "
            "across drives. Measured on the documented seam: six drives of two steps each "
            "produced ONE review, with 11 of 12 snapshots skipped by cadence. A "
            "conversational host looks like it has a dead strategist."
        ),
        fix=(
            "a multi-drive host passes ConfigLimits(review_gap=0). Nothing in ConfigLimits, "
            "ConfigRunner, ConfigGovernor or run_configured says the step index restarts."
        ),
        incapable_tier=True,
    ),
    SeamTrap(
        id="T3",
        seam="embodiment.config_run.run_configured system prompt composition",
        symptom=(
            "with no host system_prompt, the composed configuration prompt REPLACES the "
            "loop's own default prompt as soon as the strategist writes one prompt section "
            "— so the acting seat silently loses its base framing to a section that was "
            "never meant to be the whole of it."
        ),
        fix=(
            "seed the seat's SeatConfig with a base prompt section (ConfigLifecycle(seats=…)) "
            "so the baseline is part of the configuration the strategist edits, and pass no "
            "system_prompt at all — one source of the actor's prompt, not two."
        ),
    ),
    SeamTrap(
        id="T4",
        seam="embodiment.config_run.run_configured(**actor_kwargs)",
        symptom=(
            "a host-supplied system_prompt is not 'forwarded verbatim' as the docstring "
            "reads once the lane is armed — it is concatenated with the composed "
            "configuration prompt, host text first, separated by a blank line."
        ),
        fix=(
            "either supply no system_prompt (this host's choice, see T3) or know that what "
            "the actor sees is host text followed by configuration."
        ),
    ),
    SeamTrap(
        id="T5",
        seam="embodiment.knowledge.knowledge_scope(agent=…)",
        symptom=(
            "agent defaults to 'default', so a host that omits it writes the block to "
            "default.knowledge.<seat> while this repo's own /recall reads the 'embodiment' "
            "scope. The module docstring says the scope is named by culture.yaml's suffix, "
            "but nothing resolves it and no error is raised — the partitions simply miss."
        ),
        fix=(
            "resolve it explicitly: agent=resolve_identity(root) or 'default'. The link "
            "between embodiment.identity.resolve_identity and knowledge_scope's agent "
            "argument is named at neither seam."
        ),
    ),
    SeamTrap(
        id="T6",
        seam="embodiment.config_runner.ConfigRunner.drain / .close",
        symptom=(
            "a review still in flight when the last drive ends is never applied: "
            "run_configured's trailing advance has already run, and close() records the "
            "outcome as a late drop. The last thing the strategist decided in a session is "
            "the thing most likely to be lost."
        ),
        fix=(
            "settle explicitly after the final drive — wait_idle, drain, propose, advance. "
            "ThreeTierSession.settle does this; close()'s docstring names the late drop but "
            "no seam says a host should drain by hand."
        ),
    ),
    SeamTrap(
        id="T7",
        seam="embodiment.config_review.ConfigSnapshot.snapshot_id",
        symptom=(
            "a projector that returns an unchanged snapshot has it skipped "
            "(counts['snapshots_unchanged']) rather than reviewed. A projector built to be "
            "stable across a drive therefore silently produces no reviews at all."
        ),
        fix=(
            "vary the projection with what actually happened — this host's projector keys "
            "snapshot_id on the drive and step and carries the step's own observations."
        ),
    ),
    SeamTrap(
        id="T8",
        seam="embodiment.config_report.build_config_report vs ConfigLifecycle(seats=…)",
        symptom=(
            "build_config_report's docstring states its config_sha is the same digest a live "
            "ConfigLifecycle computes, 'so a host running both can assert the two agree'. That "
            "holds only if every element arrived through the ledger. A baseline seeded through "
            "ConfigLifecycle(seats=…) — which is the documented mitigation for T3 — never "
            "passes through the ledger, so the report explains one prompt section while the "
            "seat runs under two, and the digests silently disagree with nothing recorded as "
            "unexplained. The documented mitigation for one seam gap invalidates a documented "
            "property of another."
        ),
        fix=(
            "seed the baseline through the gate as ORIGIN_HOST changes instead of through the "
            "constructor (see seed_changes), so the ledger explains the whole configuration "
            "and the parity property the report claims actually holds."
        ),
    ),
)


# ── the world: a potting shed, deliberately not a coding agent ───────────────


@dataclass
class Shed:
    """The world the worker acts on. In-process, and nobody's filesystem.

    Not a coding agent and not colleague: issue #2's definition-of-done asks for
    a demonstration on a **non-colleague** host, and a domain with no shell and
    no file surface keeps the C2 threat-model statement short and true.
    """

    moisture: dict[str, int] = field(
        default_factory=lambda: {"fern-bed": 42, "orchid-bed": 61, "tomato-run": 22}
    )
    offline: tuple[str, ...] = ("cactus-shelf",)
    care_log: list[str] = field(default_factory=list)

    def read(self, zone: str) -> str:
        if zone in self.offline:
            return f"{zone}: sensor offline"
        if zone not in self.moisture:
            raise KeyError(zone)
        return f"{zone}: {self.moisture[zone]}% moisture"

    def zones(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.moisture) | set(self.offline)))


#: Every capability id this host declares. A tools or permissions change may
#: select from these and from nothing else — ``h11``'s "select, never mint",
#: held by the host being the only thing that ever executes one.
TOOL_IDS = ("read_sensor", "log_care", "tell_senses", "finish")
PERMISSION_IDS = ("water-without-asking", "speak-first")


def build_catalog() -> CapabilityCatalog:
    """The host's capability DECLARATION — never discovered from the executor.

    ``embodiment.capability``'s own docstring is emphatic about why there is no
    ``from_executor``: deriving a catalog from a wildcard executor would invent
    the authority ``h11`` forbids inventing. So this is written out by the same
    host that wired the tools, and it is the whole of what a change may select.
    """
    entries = tuple(
        Capability(capability_id=name, kind="tool", label=name.replace("_", " "))
        for name in TOOL_IDS
    ) + tuple(
        Capability(capability_id=name, kind="permission", label=name.replace("-", " "))
        for name in PERMISSION_IDS
    )
    return CapabilityCatalog(entries=entries, catalog_id="three-tier-shed")


class WorkerTools:
    """The acting seat's tool surface: ``execute(name, arguments) -> ToolOutcome``.

    ``tell_senses`` is the interesting one, and it is the authority lattice made
    operable rather than described. It does not write senses' prompt — the
    worker structurally cannot reach one — it offers an **attributed claim** for
    senses' knowledge block, ``origin=worker``, which then travels the ordinary
    propose → verify → apply path like any other change. So the worker→senses
    channel is a configuration change with a writer's name on it, not a message.
    """

    def __init__(self, shed: Shed, session: "ThreeTierSession") -> None:
        self.shed = shed
        self.session = session
        self.calls: list[str] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        allowed = self.session.allowed_tools()
        if name not in allowed:
            raise ToolError(
                f"{name} is not in this seat's configured tool surface "
                f"({', '.join(allowed) or 'none'})"
            )
        if name == "read_sensor":
            zone = str(arguments.get("zone", "")).strip()
            try:
                return ToolOutcome(result=self.shed.read(zone))
            except KeyError:
                raise ToolError(
                    f"no zone named {zone!r}; this shed has {', '.join(self.shed.zones())}"
                ) from None
        if name == "log_care":
            zone = str(arguments.get("zone", "")).strip()
            action = str(arguments.get("action", "")).strip()
            if not zone or not action:
                raise ToolError("log_care needs both a zone and an action")
            self.shed.care_log.append(f"{zone}: {action}")
            return ToolOutcome(result=f"logged: {zone} — {action}", changed_file="care.log")
        if name == "tell_senses":
            claim = str(arguments.get("claim", "")).strip()
            if not claim:
                raise ToolError("tell_senses needs a claim to record")
            record = self.session.worker_tells_senses(claim)
            return ToolOutcome(result=record)
        if name == "finish":
            summary = str(arguments.get("summary", "")).strip()
            if not summary:
                raise ToolError("finish needs a summary of what you did and found")
            return ToolOutcome(result="finished", finished=True, finish_summary=summary)
        raise ToolError(f"unknown tool {name!r}")


TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_sensor",
            "description": "Read one zone's moisture sensor.",
            "parameters": {
                "type": "object",
                "properties": {"zone": {"type": "string"}},
                "required": ["zone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_care",
            "description": "Record a care action against a zone.",
            "parameters": {
                "type": "object",
                "properties": {"zone": {"type": "string"}, "action": {"type": "string"}},
                "required": ["zone", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tell_senses",
            "description": (
                "Record an attributed claim in the interaction seat's knowledge block. "
                "It is relayed to the operator as YOUR claim, with your name on it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"claim": {"type": "string"}},
                "required": ["claim"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task with a short, complete summary.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


# ── the seeded baseline configuration (trap T3's mitigation) ─────────────────

WORKER_BASE_PROMPT = (
    "You tend a small potting shed. Read the sensors you need with read_sensor, "
    "record what you do with log_care, and finish with a short, complete summary "
    "naming every zone and reading you used. Make the smallest change that "
    "answers the question. If you learn something the person talking to us should "
    "know, record it with tell_senses — it is relayed as your claim, with your "
    "name on it."
)

SENSES_BASE_PROMPT = (
    "You are the voice of this potting-shed assistant. You relay: you never read "
    "a sensor, never water anything and never claim to have done either. Speak "
    "plainly and briefly, in the first person, to the person in the room."
)

STRATEGIST_FRAMING = (
    "The seat you configure tends a potting shed through four tools: read_sensor, "
    "log_care, tell_senses and finish. It answers a person who is standing there "
    "waiting. Configure for that: prompts that make it read before it answers and "
    "name its readings, knowledge that is worth carrying between questions, and "
    "tool or permission selections that fit what it is actually being asked to do. "
    "You are told what happened; you are not told what to decide."
)


def seed_changes() -> tuple[dict[str, Any], ...]:
    """The baseline every arm starts from, as ORDINARY host-originated changes.

    Two traps meet here, and the second is why these are change payloads rather
    than a ``ConfigLifecycle(seats=…)`` mapping.

    Trap **T3**: with nothing seeded and no ``system_prompt``, the acting seat's
    entire framing becomes whatever prompt section the strategist wrote first —
    the loop's own default prompt is replaced, silently, the moment a section
    exists. So the base text has to be part of the configuration.

    Trap **T8**: seeding it through ``ConfigLifecycle(seats=…)`` fixes T3 and
    breaks something else. ``build_config_report`` derives a seat's effective
    configuration *from the applied-change ledger alone*, and its docstring
    states that the ``config_sha`` it computes is "the SAME digest a live
    ``ConfigLifecycle`` computes for the seat it is actually driving, so a host
    running both can assert the two agree". A constructor-seeded baseline never
    passes through the ledger, so it is invisible to the report and the two
    digests silently disagree — measured here: the report explained one prompt
    section while the seat was running under two. The documented mitigation for
    one seam gap invalidates a documented property of another.

    Seeding through the gate fixes both at once, and pays for itself twice over:
    the ledger explains the *whole* configuration rather than only what changed
    since some unrecorded starting point, and revert-to-baseline becomes
    expressible from the ledger like any other change. ``ORIGIN_HOST`` exists
    for exactly this — the host wired every capability in the first place, so it
    is the ground authority and may write any target.
    """
    return (
        {
            "change_id": "seed-worker-prompt",
            "target": "worker.prompts",
            "origin": ORIGIN_HOST,
            "section": "base",
            "text": WORKER_BASE_PROMPT,
            "reason": "the host's own baseline framing for the acting seat",
        },
        {
            "change_id": "seed-worker-tools",
            "target": "worker.tools",
            "origin": ORIGIN_HOST,
            "capability_ids": list(TOOL_IDS),
            "reason": "the host's own baseline tool surface for the acting seat",
        },
        {
            "change_id": "seed-senses-prompt",
            "target": "senses.prompts",
            "origin": ORIGIN_HOST,
            "section": "base",
            "text": SENSES_BASE_PROMPT,
            "reason": "the host's own baseline framing for the interaction seat",
        },
    )


def advance_and_record(
    lifecycle: ConfigLifecycle,
    ledger: Optional[ConfigLedger],
    *,
    step_index: int = 0,
) -> tuple[str, ...]:
    """Move every pending proposal as far as its gate allows, and record applies.

    The ledger is called for its DURABILITY, never for the event it returns —
    the transition stream is already the single source of every state event, and
    emitting both would double-count applies.
    """
    report = lifecycle.advance()
    applied = tuple(getattr(report, "applied", ()) or ())
    if ledger is not None:
        for change_id in applied:
            proposal = lifecycle.proposal(change_id)
            if proposal is not None:
                ledger.record_applied(proposal.change, step_index=step_index)
    return applied


# ── the per-change-type verification suite (the 'gated and tested' clause) ───

#: How far a seat's whole prompt may grow beyond its baseline before the gate
#: refuses. A *bound*, not a target — and the quantity the ratchet re-measures
#: against a FIXED baseline, which is what makes compounding detectable rather
#: than assumed away (``c7``).
PROMPT_GROWTH_LIMIT = 1200

#: The most attributed claims a seat's knowledge block may carry. An unbounded
#: block is a prompt that grows without anyone deciding it should.
KNOWLEDGE_ENTRY_LIMIT = 8


def build_verifier(catalog: CapabilityCatalog) -> Callable[[VerificationRequest], Any]:
    """One suite per change type, run against the CANDIDATE the seat would get.

    ``drone.py``'s stage → smoke → save, moved to runtime: the request carries
    the configuration the seat *would* run under, so what is graded is what will
    be in force rather than the change in isolation. A failure is never applied
    and is recorded, exactly like a failed drone smoke.
    """

    def verify(request: VerificationRequest) -> VerificationResult:
        candidate: SeatConfig = request.candidate
        baseline: SeatConfig = request.baseline
        target = request.target or ""
        checks: list[tuple[str, bool]] = []

        # Every type, and — critically — every ratchet re-check. A RatchetGuard
        # runs this same suite with NO change and therefore no target, so a rule
        # written only under a target branch would be a rule the ratchet cannot
        # see. The growth bound is the quantity ``c7`` asks to be re-measured
        # against a fixed baseline, so it lives here where both callers reach it.
        checks.append(("prompt-non-empty", bool(compose_prompt(candidate).strip())))
        checks.append(
            (
                "base-section-kept",
                bool((candidate.section("base") or PromptSection()).text.strip()),
            )
        )
        growth = len(compose_prompt(candidate)) - len(compose_prompt(baseline))
        checks.append(("prompt-growth-bounded", growth <= PROMPT_GROWTH_LIMIT))

        if target.endswith(".prompts"):
            checks.append(("prompt-sections-named", all(s.section for s in candidate.prompt)))
        elif target.endswith(".knowledge"):
            checks.append(("knowledge-bounded", len(candidate.knowledge) <= KNOWLEDGE_ENTRY_LIMIT))
            checks.append(("knowledge-attributed", all(e.origin for e in candidate.knowledge)))
        elif target.endswith(".tools"):
            checks.append(("tools-declared", all(catalog.declares(i) for i in candidate.tools)))
            # A seat that cannot finish cannot end a drive: the loop would spend
            # its whole budget. This is the one selection the gate refuses.
            checks.append(("can-still-finish", "finish" in candidate.tools))
        elif target.endswith(".permissions"):
            checks.append(
                (
                    "permissions-declared",
                    all(catalog.declares(i, "permission") for i in candidate.permissions),
                )
            )

        failed = [name for name, ok in checks if not ok]
        return VerificationResult(
            passed=not failed,
            summary=("all checks passed" if not failed else f"failed: {', '.join(failed)}"),
            suite=f"three-tier:{target or 'seat'}",
            checks_run=len(checks),
            checks_failed=len(failed),
        )

    return verify


# ── the senses seat's prompt: composed by the HOST, never framed by the package ──


def compose_senses_prompt(
    config: SeatConfig,
    *,
    knowledge: Sequence[Any] = (),
    inner_state: str = "",
) -> str:
    """The senses system prompt, composed from host-owned pieces.

    ``embodiment.framing`` deliberately has no ``frame_senses()`` — the senses
    coordination loop is colleague's, per colleague#352 — so composing this is
    the host's act and this function is the host doing it.

    :data:`~embodiment.senses_text.SENSES_GROUNDING` goes in **verbatim and
    unconditionally**. It is not advisory: measured over 128 live calls, a
    senses prompt carrying this clause abstained under one operator push 16 of
    16 times and the identical prompt without it fabricated a reading 16 of 16
    times (`docs/live-test-results/senses-grounding.md`). Omitting it is not a
    conservative choice, it is reproducing a measured failure.

    :data:`~embodiment.senses_text.KNOWLEDGE_ATTRIBUTION` rides along whenever a
    knowledge block is present, because that block is a path from the acting
    tier to the operator's ear and the seat reading it out has to say whose
    claim it is relaying. Unlike the clause above it, **it has no measurement
    behind it** — the reasoning for it is structural and the reasoning is all it
    has.
    """
    parts = [compose_prompt(config).strip(), SENSES_GROUNDING]
    if knowledge:
        parts.append(KNOWLEDGE_ATTRIBUTION)
        lines = [f"- {entry.text} (written by: {entry.origin})" for entry in knowledge]
        parts.append("Your knowledge block:\n" + "\n".join(lines))
    if inner_state:
        parts.append("The status block you can see:\n" + inner_state)
    return "\n\n".join(part for part in parts if part.strip())


# ── role resolution: by NAME, from the gateway's own advert ──────────────────


@dataclass(frozen=True)
class SeatDial:
    """One resolved seat: which role, which model, which endpoint."""

    seat: str
    role: str
    model: str
    endpoint: str = ""
    ready: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat": self.seat,
            "role": self.role,
            "model": self.model,
            "endpoint": self.endpoint,
            "ready": self.ready,
        }


@dataclass(frozen=True)
class SeatResolution:
    """What resolved, what did not, and why — never a raise (constraint C3)."""

    dials: Mapping[str, SeatDial] = field(default_factory=dict)
    degradations: tuple[tuple[str, str], ...] = ()

    def get(self, seat: str) -> Optional[SeatDial]:
        return self.dials.get(seat)

    @property
    def actor_ready(self) -> bool:
        return "actor" in self.dials

    @property
    def senses_ready(self) -> bool:
        return "senses" in self.dials

    @property
    def strategist_ready(self) -> bool:
        return "strategist" in self.dials

    def to_dict(self) -> dict[str, Any]:
        return {
            "dials": {name: dial.to_dict() for name, dial in self.dials.items()},
            "degradations": [
                {"code": code, "reason": reason} for code, reason in self.degradations
            ],
        }


#: This host's seats, and the lobes role each one is looked up under. Resolution
#: is by dict KEY in the ``/capabilities`` payload — never by inspecting a model
#: id, which is the repo's standing rule.
HOST_SEATS: Mapping[str, str] = {
    "senses": SENSES_ROLE,
    "actor": WORKER_ROLE,
    "strategist": CORTEX_ROLE,
}


def fetch_capabilities(gateway: str, *, timeout: float) -> dict[str, Any]:
    """GET ``{gateway}/capabilities``. Needs no key on this rig.

    *timeout* is the caller's, and every caller here hands it a bound derived in
    ``worker_seam`` rather than a number chosen here — this module owns no clock.
    """
    if not gateway.startswith(("http://", "https://")):
        raise ValueError(f"gateway must be an http(s) URL, got {gateway!r}")
    url = f"{gateway.rstrip('/')}/capabilities"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def resolve_seats(capabilities: Mapping[str, Any]) -> SeatResolution:
    """Resolve every seat by role name. Never raises; every gap is recorded.

    The ``senses`` role was ``ready=false`` on this gateway at last check, and
    its advert declares nothing perceptual. So an unready seat is a **recorded
    degradation and a missing dial**, never an exception and never a reason the
    host cannot run: the operator still gets an answer, relayed by the fallback
    voice, and the notice says which tier is missing.
    """
    dials: dict[str, SeatDial] = {}
    degradations: list[tuple[str, str]] = []
    payload = capabilities if isinstance(capabilities, Mapping) else {}
    roles = payload.get("roles") if isinstance(payload.get("roles"), Mapping) else payload
    for seat, role in HOST_SEATS.items():
        entry = roles.get(role) if isinstance(roles, Mapping) else None
        if entry is None:
            degradations.append((f"seat-{seat}-absent", f"the gateway declares no {role!r} role"))
            continue
        if not isinstance(entry, Mapping):
            degradations.append((f"seat-{seat}-malformed", f"the {role!r} advert is not a mapping"))
            continue
        if not entry.get("ready"):
            degradations.append((f"seat-{seat}-not-ready", f"the {role!r} role is not ready"))
            continue
        dials[seat] = SeatDial(
            seat=seat,
            role=role,
            model=str(entry.get("model") or role),
            endpoint=str(entry.get("endpoint") or ""),
        )
    return SeatResolution(dials=dials, degradations=tuple(degradations))


def build_seam(
    dial: SeatDial,
    *,
    gateway: str,
    api_key: str,
    max_tokens: int,
    tools: Optional[list[dict[str, Any]]] = None,
    stream: bool = ws.DEFAULT_STREAM,
) -> ws.WorkerSeam:
    """One metered, STREAMING dial. Every bound comes from ``worker_seam``.

    Streaming is the default transport, and it is the reason this host has no
    total-request deadline of its own to get wrong: with chunks flowing, the
    binding quantity stops being generation length at every hop at once. The two
    phases are bounded separately — a queue-aware first-chunk bound derived at
    :data:`STREAM_QUEUE_WIDTH`, then an inter-chunk idle bound armed on the
    socket only once the first chunk has landed, so queue wait is never charged
    as idle.
    """
    endpoint = dial.endpoint or gateway
    return ws.WorkerSeam(
        base_url=f"{endpoint.rstrip('/')}/v1",
        model=dial.model,
        api_key=api_key,
        role=dial.seat,
        max_tokens=max_tokens,
        temperature=TEMPERATURE,
        tools=tools,
        stream=stream,
        stream_queue_width=STREAM_QUEUE_WIDTH,
    )


# ── hermetic seams: what runs with no network, and what CI exercises ─────────


def scripted_actor(shed: Shed) -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A deterministic acting seat: read, log, tell, finish.

    Not a stub for the sake of a test — it is what makes ``--live`` opt-in with
    no default anywhere, so CI can never trip into a real endpoint.
    """
    state: dict[str, Any] = {"turn": 0, "instruction": None}

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        instruction = ""
        for message in messages:
            if message.get("role") == "user":
                instruction = str(message.get("content") or "")
        # One seam object serves every drive, so the script restarts when the
        # instruction does. A counter that kept climbing would make every drive
        # after the first finish on its opening turn.
        if instruction != state["instruction"]:
            state["instruction"] = instruction
            state["turn"] = 0
        state["turn"] += 1
        turn = state["turn"]
        zone = next((z for z in shed.zones() if z in instruction), "fern-bed")
        if turn == 1:
            return ModelResponse(
                content="Let me read that sensor.",
                tool_calls=[_call("c1", "read_sensor", {"zone": zone})],
                completion_tokens=24,
            )
        if turn == 2:
            return ModelResponse(
                content="Recording the visit.",
                tool_calls=[_call("c2", "log_care", {"zone": zone, "action": "checked"})],
                completion_tokens=22,
            )
        if turn == 3:
            reading = shed.read(zone)
            return ModelResponse(
                content="Worth carrying forward.",
                tool_calls=[_call("c3", "tell_senses", {"claim": f"I read {reading}."})],
                completion_tokens=26,
            )
        return ModelResponse(
            content="Done.",
            tool_calls=[
                _call("c4", "finish", {"summary": f"Checked {zone}: {shed.read(zone)}. Logged."})
            ],
            completion_tokens=30,
        )

    return complete


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> Any:
    from embodiment.contract import ToolCall

    return ToolCall(id=call_id, name=name, arguments=arguments)


def scripted_senses() -> Callable[[list[dict[str, Any]]], ModelResponse]:
    """A deterministic voice. It relays and never invents a reading."""

    def complete(messages: list[dict[str, Any]]) -> ModelResponse:
        user = ""
        system = ""
        for message in messages:
            if message.get("role") == "user":
                user = str(message.get("content") or "")
            elif message.get("role") == "system":
                system = str(message.get("content") or "")
        if user.startswith("INTAKE:"):
            return ModelResponse(
                content=json.dumps(
                    {
                        "interpretation": user[len("INTAKE:") :].strip(),
                        "confidence": 0.9,
                        "task_type": "shed-question",
                        "omissions": [],
                        "ack": "On it.",
                    }
                ),
                completion_tokens=18,
            )
        block = "Your knowledge block:" in system
        tail = " (relaying what the shed tier recorded)" if block else ""
        return ModelResponse(content=f"{user.strip()}{tail}", completion_tokens=20)

    return complete


def scripted_strategist() -> Callable[..., str]:
    """A deterministic strategist: one real prompt change, then holds.

    It proposes configuration and never prose, because prose is not a thing this
    tier can produce — the acting seat is never handed anything it wrote.
    """
    state = {"reviews": 0}

    def complete(messages: list[dict[str, Any]], **kwargs: Any) -> str:
        state["reviews"] += 1
        index = state["reviews"]
        if index == 1:
            return json.dumps(
                {
                    "changes": [
                        {
                            "change_id": f"three-tier-{index}",
                            "target": "worker.prompts",
                            "origin": "strategist",
                            "section": "readings",
                            "text": (
                                "Always state the numeric reading and the zone it came "
                                "from in your summary. A summary without the number is "
                                "not an answer."
                            ),
                            "reason": (
                                "the acting seat summarised without naming the reading it "
                                "used, so the person cannot check it"
                            ),
                        }
                    ]
                }
            )
        return "[hold]"

    return complete


# ── the timeline: one time-ordered record of every stream ────────────────────


@dataclass(frozen=True)
class Entry:
    at: float
    stream: str
    kind: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": round(self.at, 3),
            "stream": self.stream,
            "kind": self.kind,
            "text": self.text,
            "data": self.data,
        }


class Timeline:
    """Thread-safe, append-only, and flushed per line so a killed run keeps its record."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._entries: list[Entry] = []
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._handle = path.open("w", encoding="utf-8") if path is not None else None

    def add(self, stream: str, kind: str, text: str = "", **data: Any) -> Entry:
        entry = Entry(
            at=time.monotonic() - self._started, stream=stream, kind=kind, text=text, data=data
        )
        with self._lock:
            self._entries.append(entry)
            if self._handle is not None:
                self._handle.write(json.dumps(entry.to_dict(), default=str) + "\n")
                self._handle.flush()
        return entry

    def notice(self, text: str, **data: Any) -> Entry:
        """Recorded AND said once — constraint C3's whole requirement."""
        print(f"notice: {text}", file=sys.stderr, flush=True)
        return self.add("host", "notice", text, **data)

    @property
    def entries(self) -> tuple[Entry, ...]:
        with self._lock:
            return tuple(self._entries)

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


# ── the projector: the rig as the HOST understands it ────────────────────────


def session_projector(session: "ThreeTierSession") -> Callable[[Any], Optional[ConfigSnapshot]]:
    """Build the snapshot the strategist reviews.

    Trap **T7**: an unchanged snapshot is skipped rather than reviewed, so a
    projection that is stable across a drive produces no reviews at all. This one
    varies with the drive and the step, and carries the step's own observations —
    which is what the snapshot is *for*, so the mitigation and the right design
    are the same thing here.

    Nothing about what an observation MEANS is inferred by the package: which
    facts matter and which problems are worth configuring against are domain
    judgements, and this function is the host making them.
    """

    def project(context: Any) -> Optional[ConfigSnapshot]:
        drive = session.drive_index
        calls = dict(getattr(context, "calls_by_name", {}) or {})
        failures = dict(getattr(context, "failures_by_name", {}) or {})
        observations = [f"step {context.step_count}: tools used {calls or 'none'}"]
        if session.last_summary:
            observations.append(f"last summary: {session.last_summary}")
        problems: list[str] = []
        for name, count in failures.items():
            problems.append(f"{name} failed {count} time(s)")
        if session.last_summary and not any(
            str(value) in session.last_summary for value in session.shed.moisture.values()
        ):
            problems.append("the last summary named no numeric reading the person could check")
        return ConfigSnapshot(
            snapshot_id=f"d{drive}-s{context.step_count}",
            summary=f"drive {drive}: {session.last_instruction[:120]}",
            observations=tuple(observations),
            problems=tuple(problems),
            seats=(context.config,),
            capabilities=tuple(session.catalog.ids),
            resource_state={"zones": ", ".join(session.shed.zones())},
        )

    return project


def build_strategist(
    complete: Callable[..., Any],
    *,
    model: str,
    catalog: CapabilityCatalog,
    review_turns: int,
    review_gap: int,
) -> ConfigRunner:
    """The background configuring tier — one daemon thread beside the acting loop.

    Trap **T2**, mitigated here and nowhere else in this file: ``review_gap`` is
    passed explicitly. The default of 2 measures a distance in acting steps, and
    the step index ``run_configured`` supplies restarts at 1 on every drive while
    this runner's cadence memory does not — so a conversational host running many
    short drives gets **one review per session** and a strategist that looks
    dead. Measured on the documented seam: six drives of two steps each, one
    review, 11 of 12 snapshots skipped. ``0`` disables the cadence, which is the
    right setting for a host whose drives are short and whose boundaries are
    already rare.
    """
    return ConfigRunner(
        complete,
        role=CORTEX_ROLE,
        model=model,
        controls=ConfigControls(max_turns=review_turns),
        catalog=catalog,
        # APPENDED to CONFIG_AUTHORITY, never substituted for it — so no host
        # framing can drop the authority boundary. It supplies only what the
        # projection cannot: this world's vocabulary and the shape of the seat.
        system=STRATEGIST_FRAMING,
        clock=time.monotonic,
        limits=ConfigLimits(review_gap=review_gap),
    )


# ── the session ──────────────────────────────────────────────────────────────


@dataclass
class DriveRecord:
    instruction: str
    exit_reason: str = ""
    summary: str = ""
    seconds: float = 0.0
    steps: int = 0
    config_sha: str = ""
    applied: tuple[str, ...] = ()
    deferrals: int = 0
    degradations: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "exit_reason": self.exit_reason,
            "summary": self.summary,
            "seconds": round(self.seconds, 3),
            "steps": self.steps,
            "config_sha": self.config_sha,
            "applied": list(self.applied),
            "deferrals": self.deferrals,
            "degradations": list(self.degradations),
            "error": self.error,
        }


class ThreeTierSession:
    """One operator, one voice, three tiers.

    The turn shape, which is the whole architecture in five lines:

    1. **senses hears** — :func:`embodiment.perception.perceive` builds a
       ``ContextPacket`` whose ``original`` is the operator's words verbatim,
       never model output. An intake that fails still returns that packet.
    2. **the worker acts** — :func:`~embodiment.config_run.run_configured`
       drives the bounded tool loop under the configuration in force, pinned for
       the whole drive.
    3. **the cortex configures** — on its own thread, from snapshots offered at
       tool-step boundaries. It never blocks the drive and never reaches the
       actor.
    4. **the gate lands changes between runs** — never under a working seat.
    5. **senses says** — it relays the drive's result and its own knowledge
       block, attributed.
    """

    def __init__(
        self,
        *,
        shed: Shed,
        lifecycle: Optional[ConfigLifecycle],
        ledger: Optional[ConfigLedger],
        strategist: Optional[ConfigRunner],
        catalog: CapabilityCatalog,
        timeline: Timeline,
        actor_seam: Callable[..., Any],
        senses_seam: Optional[Callable[..., Any]],
        max_steps: int,
        agent: str,
        ungoverned: bool = False,
        model: str = "",
    ) -> None:
        self.shed = shed
        self.lifecycle = lifecycle
        self.ledger = ledger
        self.strategist = strategist
        self.catalog = catalog
        self.timeline = timeline
        self.actor_seam = actor_seam
        self.senses_seam = senses_seam
        self.max_steps = max_steps
        self.agent = agent
        self.ungoverned = ungoverned
        self.model = model
        self.tools = WorkerTools(shed, self)
        self.baseline = ConfigBaseline()
        self.ratchet = RatchetGuard(self.baseline)
        self.drives: list[DriveRecord] = []
        self.drive_index = 0
        self.last_instruction = ""
        self.last_summary = ""
        self.senses_degraded = False
        self._pending_worker_claims = 0
        if self.lifecycle is not None:
            self.seed()

    def seed(self) -> tuple[str, ...]:
        """Install the host's baseline through the gate, then FIX it as baseline.

        Order matters both ways. The baseline is captured *after* the seed lands,
        so "revert to baseline" means the host's own starting configuration
        rather than an empty seat; and it is captured *once*, because a baseline
        that moved itself along with the changes it is meant to catch drifting
        would not be a fixed point at all.
        """
        if self.lifecycle is None:
            return ()
        for payload in seed_changes():
            if self.lifecycle.propose(payload, catalog=self.catalog) is None:
                last = list(self.lifecycle.degradations)[-1:]
                reason = last[0].reason if last else "refused"
                self.timeline.notice(f"a baseline seed change was refused: {reason}")
        applied = advance_and_record(self.lifecycle, self.ledger, step_index=0)
        for seat in ("worker", "senses"):
            self.baseline.capture(self.lifecycle, seat)
        self.timeline.add(
            "host", "seeded", f"baseline installed through the gate: {', '.join(applied)}"
        )
        return applied

    # -- tier 1: senses relays the world IN ----------------------------------

    def hear(self, text: str) -> Any:
        """Verbatim intake. Never raises, and never loses the operator's words."""

        def read_request(raw: str) -> Any:
            return self._senses_call(
                "Read the request and answer with one JSON object carrying "
                "interpretation, confidence, task_type, omissions and ack.",
                f"INTAKE:{raw}",
            )

        # No seam wired is not a fault: perceive still returns a real packet
        # carrying the operator's words, and records nothing as degraded.
        interpret = read_request if self.senses_seam is not None else None
        packet, record = perception.perceive(
            text,
            interpret=interpret,
            clock=time.monotonic,
            on_degrade=lambda degradation: self.timeline.notice(
                f"intake degraded ({degradation.code}): {degradation.reason}; "
                "the request itself is carried through verbatim",
                code=degradation.code,
            ),
        )
        self.timeline.add(
            "senses",
            "intake",
            packet.original,
            degraded=record.degraded,
            interpretation=packet.interpretation,
        )
        return packet

    # -- tier 2: the worker acts ---------------------------------------------

    def act(self, packet: Any) -> DriveRecord:
        """One governed (or control) drive of the bounded tool loop."""
        self.drive_index += 1
        self.last_instruction = packet.original
        record = DriveRecord(instruction=packet.original)
        task = Task(
            id=f"three-tier-{self.drive_index}",
            repo_path=str(Path.cwd()),
            instruction=packet.original,
            context=packet.interpretation or "",
            engine="three-tier",
        )
        governor = self._governor()
        calls_before = len(self.tools.calls)
        started = time.monotonic()
        try:
            outcome = run_configured(
                self.actor_seam,
                task,
                executor=self.tools,
                max_steps=self.max_steps,
                governor=governor,
                # NO system_prompt, deliberately — trap T3/T4. The seat's whole
                # framing is the configuration in force, seeded at startup and
                # edited only through the gate, so there is exactly one source of
                # the actor's prompt and the ledger can explain all of it.
                progress=lambda *args: None,
                observer=self._observe,
                model=self.model,
            )
        except LoopAborted as failure:
            record.error = f"{type(failure).__name__}: {failure}"
            self.timeline.notice(f"the drive aborted: {record.error}")
            self.drives.append(record)
            return record
        record.seconds = time.monotonic() - started
        record.exit_reason = getattr(outcome, "exit_reason", "") or ""
        result = getattr(outcome, "result", None)
        record.summary = getattr(result, "summary", "") or ""
        record.steps = len(self.tools.calls) - calls_before
        record.config_sha = getattr(outcome, "config_sha", "")
        record.applied = tuple(getattr(outcome, "applied", ()) or ())
        record.deferrals = len(getattr(outcome, "deferrals", ()) or ())
        record.degradations = tuple(
            getattr(degradation, "code", "") for degradation in getattr(outcome, "degradations", ())
        )
        self.last_summary = record.summary
        for change_id in record.applied:
            self.timeline.add(
                "cortex",
                "applied",
                f"configuration change {change_id} is in force",
                change_id=change_id,
            )
        self.drives.append(record)
        return record

    def _governor(self) -> Optional[ConfigGovernor]:
        """The lane, the matched control, or nothing at all.

        ``--ungoverned`` returns ``None``, which is byte-identical to calling
        :func:`embodiment.loop.run` directly. ``--no-strategist`` returns a
        governor with the lifecycle and ledger wired and **no reviewer** — the
        matched control, where the acting seat's baseline configuration is
        identical and only the tier that changes it is gone.
        """
        if self.ungoverned or self.lifecycle is None:
            return None
        return ConfigGovernor(
            lifecycle=self.lifecycle,
            reviewer=self.strategist,
            projector=session_projector(self) if self.strategist is not None else None,
            ledger=self.ledger,
            seat="worker",
        )

    def _observe(self, event: Any) -> None:
        kind = getattr(event, "kind", "") or ""
        stream = "cortex" if kind.startswith("config") else "worker"
        self.timeline.add(stream, kind, getattr(event, "detail", "") or "")

    # -- the worker → senses knowledge channel -------------------------------

    def worker_tells_senses(self, claim: str) -> str:
        """One attributed claim, offered as an ordinary configuration change.

        This is the three-tier authority lattice at work. The worker may write
        exactly one target — the senses knowledge block — and **never** a
        prompt; the lattice refuses the ``(origin, target)`` pair whole, so the
        boundary is structural rather than conventional. The entry carries
        ``origin=worker``, which is what stops a compromised or fabricating
        acting tier from putting anonymous words in the interaction tier's mouth.
        """
        self._pending_worker_claims += 1
        entry_id = f"claim-{self.drive_index}-{self._pending_worker_claims}"
        if self.lifecycle is None:
            self.timeline.add("worker", "knowledge-ungoverned", claim, entry_id=entry_id)
            return f"noted (ungoverned control: no knowledge block is being kept): {claim}"
        proposal = self.lifecycle.propose(
            {
                "change_id": f"worker-{entry_id}",
                "target": TARGET_SENSES_KNOWLEDGE,
                "origin": ORIGIN_WORKER,
                "entry_id": entry_id,
                "text": claim,
                "reason": "the acting seat learned something the person should hear",
            }
        )
        if proposal is None:
            last = list(self.lifecycle.degradations)[-1:]
            reason = last[0].reason if last else "refused"
            self.timeline.notice(f"a worker claim was refused whole: {reason}")
            return f"that claim was refused: {reason}"
        self.timeline.add("worker", "knowledge-proposed", claim, entry_id=entry_id)
        return (
            f"recorded as an attributed claim ({entry_id}); it reaches the voice once the "
            "interaction seat is idle and the suite has passed"
        )

    # -- tier 3 lands between drives -----------------------------------------

    def settle(self, *, timeout: float = 30.0) -> int:
        """Drain the strategist and land whatever the gate now allows.

        Trap **T6**: a review still in flight when the last drive ends is never
        applied — ``run_configured``'s trailing ``advance`` has already run, and
        ``close()`` records the outcome as a late drop. The last decision of a
        session is the one most likely to be lost, so the host drains by hand.
        """
        if self.lifecycle is None:
            return 0
        if self.strategist is not None:
            self.strategist.wait_idle(timeout)
            for outcome in self.strategist.drain():
                for change in outcome.changes:
                    self.lifecycle.propose(change, catalog=self.catalog)
        applied = advance_and_record(self.lifecycle, self.ledger, step_index=self.drive_index)
        for change_id in applied:
            self.timeline.add(
                "cortex", "applied", f"{change_id} landed at settle", change_id=change_id
            )
        return len(applied)

    def check_ratchet(self) -> Optional[Any]:
        """Re-measure cumulative drift against the FIXED baseline.

        The gate compares each proposal against the state right before it, which
        is exactly the comparison a ratchet cannot be seen through: three changes
        that each grow a prompt a little all pass, while the seat's prompt has
        grown a lot against where the session started. Advice evaporates;
        configuration accumulates.
        """
        if self.lifecycle is None:
            return None
        result = self.ratchet.check(self.lifecycle, "worker")
        if getattr(result, "passed", True) is False:
            self.timeline.notice(
                "the ratchet check FAILED against the fixed baseline: cumulative drift no "
                "individual gate could have caught. /revert restores the baseline."
            )
        return result

    def revert(self) -> Optional[Any]:
        """Back to the baseline — an ordinary change, gated like any other."""
        if self.lifecycle is None:
            return None
        baseline = self.baseline.get("worker")
        if baseline is None:
            return None
        outcome = revert_to_baseline(self.lifecycle, "worker", baseline, catalog=self.catalog)
        self.timeline.add(
            "host",
            "revert",
            f"revert applied={len(outcome.applied)} matches_baseline={outcome.matches_baseline}",
            **outcome.to_dict(),
        )
        if not outcome.matches_baseline:
            self.timeline.notice(
                "the revert did not reach the baseline bit-for-bit: "
                f"{len(outcome.residual_prompt_sections)} prompt section(s) and "
                f"{len(outcome.residual_knowledge_entries)} knowledge entr(ies) remain. "
                "Revert restores configuration; it does not unsay what was already said."
            )
        return outcome

    # -- tier 1 again: senses relays the inner state OUT ---------------------

    def say(self, record: DriveRecord) -> str:
        """The one voice the operator hears. Never raises into their path."""
        knowledge = ()
        senses_config = SeatConfig(seat="senses")
        if self.lifecycle is not None:
            senses_config = self.lifecycle.effective("senses")
            knowledge = senses_config.knowledge
        inner = self._inner_state(record)
        system = compose_senses_prompt(senses_config, knowledge=knowledge, inner_state=inner)
        spoken = record.summary or record.error or "I could not finish that."
        if self.senses_seam is None:
            self.timeline.add("senses", "say-fallback", spoken, degraded=True)
            return spoken
        try:
            reply = self._senses_call(system, spoken)
        except Exception as failure:  # noqa: BLE001 - the voice never crashes the room
            self.senses_degraded = True
            self.timeline.notice(
                f"the interaction tier failed ({type(failure).__name__}); relaying the "
                "acting tier's own words instead"
            )
            self.timeline.add("senses", "say-fallback", spoken, degraded=True)
            return spoken
        text = getattr(reply, "content", None) or str(reply or "")
        if not text.strip():
            self.timeline.notice("the interaction tier returned nothing; relaying directly")
            return spoken
        self.timeline.add("senses", "say", text)
        return text

    def _inner_state(self, record: DriveRecord) -> str:
        """What the voice may see. A report of SYSTEM state, never a feeling.

        Constraint C2's discipline: an inner state relayed outward is
        degradations, budget and in-flight work — never an affective claim.
        """
        lines = [
            f"drive: {record.exit_reason or 'unfinished'} in {record.seconds:.1f}s "
            f"over {record.steps} tool step(s)",
            f"result: {record.summary or record.error or 'none'}",
        ]
        if record.applied:
            lines.append(f"configuration changes now in force: {', '.join(record.applied)}")
        if record.deferrals:
            lines.append(f"changes waiting for an idle seat: {record.deferrals}")
        if record.degradations:
            lines.append(f"degradations recorded: {', '.join(record.degradations)}")
        return "\n".join(lines)

    def _senses_call(self, system: str, user: str) -> Any:
        seam = self.senses_seam
        if seam is None:
            raise RuntimeError("no senses seat is wired")
        return seam([{"role": "system", "content": system}, {"role": "user", "content": user}])

    # -- introspection --------------------------------------------------------

    def allowed_tools(self) -> tuple[str, ...]:
        """The seat's configured tool surface, or the host's whole offer.

        A tools change SELECTS from what this host declared; nothing here can
        mint a capability, and the host's executor is still the only thing that
        runs one.
        """
        if self.lifecycle is None:
            return TOOL_IDS
        configured = self.lifecycle.effective("worker").tools
        return tuple(configured) if configured else TOOL_IDS

    def config_report(self) -> dict[str, Any]:
        """Each seat's effective configuration with provenance, from the LEDGER alone.

        Not from the lifecycle: a report derived from the live object would be a
        second bookkeeping structure, and a config state the ledger cannot
        explain is itself a recorded degradation rather than a gap to fill from
        elsewhere.
        """
        if self.ledger is None:
            return {"seats": [], "unexplained": [], "note": "no ledger wired"}
        return build_config_report(self.ledger, seats=("worker", "senses")).to_dict()

    def report(self) -> dict[str, Any]:
        lifecycle = self.lifecycle
        return {
            "arm": self.arm_name(),
            "drives": [record.to_dict() for record in self.drives],
            "effective_config": self.config_report(),
            "worker_prompt": (
                compose_prompt(lifecycle.effective("worker")) if lifecycle is not None else ""
            ),
            "senses_knowledge": (
                [entry.to_dict() for entry in lifecycle.effective("senses").knowledge]
                if lifecycle is not None
                else []
            ),
            "transitions": (
                [transition.to_dict() for transition in lifecycle.transitions]
                if lifecycle is not None
                else []
            ),
            "degradations": (
                [degradation.to_dict() for degradation in lifecycle.degradations]
                if lifecycle is not None
                else []
            ),
            "strategist": (
                None
                if self.strategist is None
                else {
                    "role": self.strategist.role,
                    "model": self.strategist.model,
                    "counts": dict(self.strategist.counts),
                    "degradation": self.strategist.degradation(),
                }
            ),
            "senses_degraded": self.senses_degraded,
            "ratchet": [
                result.to_dict()
                for result in getattr(self.ratchet, "results", ())
                if hasattr(result, "to_dict")
            ],
            "seam_traps": [trap.to_dict() for trap in SEAM_TRAPS],
        }

    def arm_name(self) -> str:
        if self.ungoverned:
            return "ungoverned (no lane at all — byte-identical to run())"
        if self.strategist is None:
            return "matched control (lane wired, NO strategist)"
        return "governed (senses → worker → cortex/strategist)"

    def close(self) -> None:
        if self.strategist is not None:
            self.strategist.close()


# ── the CLI ──────────────────────────────────────────────────────────────────


def _script_lines(path: Path) -> Iterator[str]:
    """Replay a script, printing each line so a transcript reads like a session."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        print(f"you> {line}", flush=True)
        yield line


def _stdin_lines() -> Iterator[str]:
    while True:
        print("you> ", end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            print(flush=True)
            return
        stripped = line.strip()
        if stripped:
            yield stripped


def _file_persistence(path: Path, timeline: Timeline) -> ConfigPersistence:
    """The durable ledger lane.

    Fail-closed by construction on the far side: an advisory-era or
    unknown-version payload is refused with one recorded degradation naming the
    fix, writing is disabled for the drive, and the ledger starts empty — never
    a silent reinterpretation of old state under new semantics.
    """

    def load() -> Any:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        timeline.add("host", "durable-write", f"config ledger written to {path}", path=str(path))

    return ConfigPersistence(load=load, save=save)


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--gateway", default=DEFAULT_GATEWAY, help="the lobes gateway base URL")
    shared.add_argument(
        "--live",
        action="store_true",
        help="dial the real rig. Opt-in, with no default anywhere, so CI cannot trip into it.",
    )
    shared.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        default=ws.DEFAULT_STREAM,
        help="fall back to the blocking transport (streaming is the default)",
    )

    parser = argparse.ArgumentParser(
        prog="three_tier",
        description=(
            "Three-tier example host: senses relays, the worker acts, the cortex configures."
        ),
        parents=[shared],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="verb", required=True)
    subs.add_parser("seats", parents=[shared], help="resolve the three seats and stop")
    subs.add_parser("traps", help="print the seam traps this host was wired around")

    talk = subs.add_parser("talk", parents=[shared], help="run a session")
    talk.add_argument("--script", help="replay a script file instead of reading stdin")
    talk.add_argument(
        "--no-strategist",
        action="store_true",
        help="the MATCHED control: the lane and the seeded baseline stay, the strategist goes",
    )
    talk.add_argument(
        "--ungoverned",
        action="store_true",
        help="the layer control: no governor at all, byte-identical to a bare loop.run()",
    )
    talk.add_argument("--events", help="write the timeline as JSONL")
    talk.add_argument("--transcript", help="write a markdown transcript")
    talk.add_argument("--report", help="write the JSON report, effective config included")
    talk.add_argument("--state", help="durable config-ledger file")
    talk.add_argument("--max-steps", type=int, default=8)
    talk.add_argument("--review-turns", type=int, default=3)
    talk.add_argument(
        "--review-gap",
        type=int,
        default=0,
        help="acting steps between reviews. 0 disables the cadence — see seam trap T2.",
    )
    talk.add_argument("--max-tokens-actor", type=int, default=ACTOR_MAX_TOKENS)
    talk.add_argument("--max-tokens-senses", type=int, default=SENSES_MAX_TOKENS)
    talk.add_argument("--max-tokens-strategist", type=int, default=STRATEGIST_MAX_TOKENS)
    return parser


def _api_key() -> str:
    key = (os.environ.get(ws.API_KEY_ENV) or "").strip()
    if not key:
        raise SystemExit(f"error: no {ws.API_KEY_ENV} in the environment")
    return key


def _resolve(args: argparse.Namespace, timeline: Optional[Timeline] = None) -> SeatResolution:
    if not args.live:
        return SeatResolution(
            dials={
                name: SeatDial(seat=name, role=role, model=f"scripted-{role}")
                for name, role in HOST_SEATS.items()
            }
        )
    capabilities = fetch_capabilities(args.gateway, timeout=ws.STREAM_FIRST_CHUNK_TIMEOUT)
    resolution = resolve_seats(capabilities)
    if timeline is not None:
        for code, reason in resolution.degradations:
            timeline.notice(f"{code}: {reason}")
    return resolution


def _run_seats(args: argparse.Namespace) -> int:
    resolution = _resolve(args)
    print(json.dumps(resolution.to_dict(), indent=2))
    for code, reason in resolution.degradations:
        print(f"notice: {code}: {reason}", file=sys.stderr)
    return 0 if resolution.actor_ready else 2


def _build_seams(
    args: argparse.Namespace, resolution: SeatResolution
) -> tuple[Any, Optional[Any], Optional[Any], str]:
    """(actor, senses, strategist, actor model id). A missing seat is ``None``."""
    if not args.live:
        return (None, scripted_senses(), scripted_strategist(), "scripted-worker")
    key = _api_key()
    actor_dial = resolution.get("actor")
    if actor_dial is None:
        raise SystemExit(
            "error: the worker role did not resolve, and it is the acting seat. "
            "Run `three_tier seats --live` to see what the gateway declares."
        )
    actor = build_seam(
        actor_dial,
        gateway=args.gateway,
        api_key=key,
        max_tokens=args.max_tokens_actor,
        tools=TOOL_SCHEMA,
        stream=args.stream,
    )
    senses_dial = resolution.get("senses")
    senses = (
        None
        if senses_dial is None
        else build_seam(
            senses_dial,
            gateway=args.gateway,
            api_key=key,
            max_tokens=args.max_tokens_senses,
            stream=args.stream,
        )
    )
    strategist_dial = resolution.get("strategist")
    strategist = (
        None
        if strategist_dial is None or args.no_strategist or args.ungoverned
        else build_seam(
            strategist_dial,
            gateway=args.gateway,
            api_key=key,
            max_tokens=args.max_tokens_strategist,
            stream=args.stream,
        )
    )
    return (actor, senses, strategist, actor_dial.model)


def _run_talk(args: argparse.Namespace) -> int:
    timeline = Timeline(_prepare(Path(args.events)) if args.events else None)
    resolution = _resolve(args, timeline)
    shed = Shed()
    catalog = build_catalog()
    for problem in catalog.problems:
        timeline.notice(f"the capability catalog is malformed: {problem}")

    actor_seam, senses_seam, strategist_seam, model = _build_seams(args, resolution)
    if actor_seam is None:
        actor_seam = scripted_actor(shed)

    lifecycle: Optional[ConfigLifecycle] = None
    ledger: Optional[ConfigLedger] = None
    strategist: Optional[ConfigRunner] = None
    if not args.ungoverned:
        # No ``seats=`` mapping: the baseline is installed through the gate at
        # ThreeTierSession.seed() so the ledger can explain all of it (trap T8).
        lifecycle = ConfigLifecycle(verifier=build_verifier(catalog), catalog=catalog)
        persistence = (
            _file_persistence(Path(args.state).expanduser(), timeline) if args.state else None
        )
        ledger = ConfigLedger(persistence=persistence)
        for degradation in ledger.degradations:
            timeline.notice(f"{degradation.code}: {degradation.reason}")
        if not args.no_strategist and strategist_seam is not None:
            strategist = build_strategist(
                strategist_seam,
                model=(resolution.get("strategist").model if resolution.get("strategist") else ""),
                catalog=catalog,
                review_turns=args.review_turns,
                review_gap=args.review_gap,
            )

    agent = resolve_identity(Path.cwd()) or "default"
    session = ThreeTierSession(
        shed=shed,
        lifecycle=lifecycle,
        ledger=ledger,
        strategist=strategist,
        catalog=catalog,
        timeline=timeline,
        actor_seam=actor_seam,
        senses_seam=senses_seam,
        max_steps=args.max_steps,
        agent=agent,
        ungoverned=bool(args.ungoverned),
        model=model,
    )
    _announce(session, resolution, args)

    lines: Iterable[str] = _script_lines(Path(args.script)) if args.script else _stdin_lines()
    code = 0
    try:
        for line in lines:
            if line in {"/quit", "/exit"}:
                break
            if line.startswith("/"):
                _command(session, line)
                continue
            packet = session.hear(line)
            record = session.act(packet)
            print(f"gwen> {session.say(record)}", flush=True)
            session.settle()
            session.check_ratchet()
    except KeyboardInterrupt:
        timeline.notice("interrupted")
        code = 130
    finally:
        session.settle()
        session.close()
        _write_artifacts(args, session, timeline)
        timeline.close()
    return code


def _command(session: ThreeTierSession, line: str) -> None:
    verb = line.split()[0]
    if verb == "/config":
        print(json.dumps(session.config_report(), indent=2), flush=True)
    elif verb == "/prompt":
        if session.lifecycle is None:
            print("(ungoverned: the actor runs under the loop's own prompt)", flush=True)
        else:
            print(compose_prompt(session.lifecycle.effective("worker")), flush=True)
    elif verb == "/knowledge":
        if session.lifecycle is None:
            print("(ungoverned: no knowledge block is kept)", flush=True)
        else:
            for entry in session.lifecycle.effective("senses").knowledge:
                print(f"- {entry.text} (written by: {entry.origin})", flush=True)
    elif verb == "/ratchet":
        result = session.check_ratchet()
        print(json.dumps(result.to_dict() if result is not None else {}, indent=2), flush=True)
    elif verb == "/revert":
        outcome = session.revert()
        print(json.dumps(outcome.to_dict() if outcome is not None else {}, indent=2), flush=True)
    elif verb == "/traps":
        print(json.dumps([trap.to_dict() for trap in SEAM_TRAPS], indent=2), flush=True)
    else:
        print("commands: /config /prompt /knowledge /ratchet /revert /traps /quit", flush=True)


def _announce(
    session: ThreeTierSession, resolution: SeatResolution, args: argparse.Namespace
) -> None:
    bounds = ws.StreamBounds.derived(dialled_width=STREAM_QUEUE_WIDTH)
    seated = ", ".join(f"{name}={d.role}/{d.model}" for name, d in resolution.dials.items())
    lines = [
        f"arm: {session.arm_name()}",
        f"transport: {'streaming (sse)' if args.stream else 'blocking'} — "
        f"first-chunk {bounds.first_chunk_s:.0f}s, idle {bounds.idle_s:.0f}s, "
        f"total {bounds.total_s:.0f}s (all derived; this host defines no clock)",
        f"seats: {seated or 'none'}",
    ]
    if not resolution.senses_ready and args.live:
        lines.append(
            "the interaction tier is NOT available: the voice degrades to relaying the "
            "acting tier's own words, and the session still runs"
        )
    for line in lines:
        print(f"# {line}", file=sys.stderr, flush=True)
        session.timeline.add("host", "header", line)


def _write_artifacts(
    args: argparse.Namespace, session: ThreeTierSession, timeline: Timeline
) -> None:
    if args.report:
        _prepare(Path(args.report)).write_text(
            json.dumps(session.report(), indent=2, default=str) + "\n", encoding="utf-8"
        )
    if args.transcript:
        _prepare(Path(args.transcript)).write_text(render_transcript(timeline), encoding="utf-8")


def _prepare(path: Path) -> Path:
    """Make an artifact path writable. A run that produced results and could not
    write them down is the one failure mode a record-keeping host cannot have."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def render_transcript(timeline: Timeline) -> str:
    lines = ["# three-tier session", ""]
    for entry in timeline.entries:
        text = entry.text.replace("\n", " ")
        lines.append(f"- `{entry.at:7.2f}s` **{entry.stream}/{entry.kind}** {text}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verb == "seats":
            return _run_seats(args)
        if args.verb == "traps":
            print(json.dumps([trap.to_dict() for trap in SEAM_TRAPS], indent=2))
            return 0
        return _run_talk(args)
    except KeyboardInterrupt:
        return 130
    except SystemExit:
        raise
    except (OSError, urllib.error.URLError, ValueError) as failure:
        print(f"error: {type(failure).__name__}: {failure}", file=sys.stderr)
        print(
            "hint: run `three_tier seats --live` to see what the gateway declares, "
            f"and check {ws.API_KEY_ENV} is exported",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
