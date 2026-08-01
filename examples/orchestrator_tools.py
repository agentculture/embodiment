"""Delegation — an orchestrator's ``delegate`` and ``delegate_all`` tools.

Two shapes, one file: the **serial** delegate of plan task ``t1``, and the
**parallel fan-out** of task ``t4`` beside it.

The manager architecture, built entirely out of what ``embodiment`` already
ships. A host executor exposes one extra verb — *delegate this subtask to the
worker* — by returning ``ToolOutcome(spawn=SpawnRequest(role="worker", …))``.
The loop's existing spawn machinery does the rest: it attenuates the child's
reach, charges the child's turns to the parent's budget, and records the
attempt on :class:`~embodiment.subagent.SpawnRecord`. **Nothing under
``embodiment/`` changes** — this file is the whole feature.

Three properties are load-bearing, and each is asserted in
``tests/test_orchestrator_tools.py`` rather than merely described here:

**1. The worker's tool surface is the pre-registration.** :data:`WORKER_TOOLS`
is the *complete* list of verbs a worker may call, and
:meth:`WorkerExecutor.execute` dispatches against that tuple — so the
enumeration is the code, not a comment beside it. A verb outside it raises
:class:`~embodiment.loop.UnknownToolError`. That matters more than it looks:
returning ``ToolOutcome(result="unknown tool …")`` instead would be a
*success* carrying an error string, which the loop records as a fine step and
the model reads as prose. A narrowed surface has to bite.

**2. The cortex keeps final authority.** ``finish`` is on the orchestrator's
surface and on no other. The worker's terminal verb is ``report``, which ends
*the child's own drive* and hands text back — it can never end the task. The
worker is framed with :func:`~embodiment.framing.frame_subagent`, which says
plainly that it is not the teammate and does not answer the operator, while
the orchestrator — and only it — is framed as the cortex. Both roles get the
*same* resolved identity with role-specific authority, and with no configured
identity both prompts are byte-identical to their bases
(colleague#352's acceptance criterion).

**3. Degradation names the child that suffered it.** A worker that finishes
without reporting, or whose drive fails outright, mints one of
:data:`WORKER_DEGRADATIONS` — this host's own vocabulary, since C3 applies to
hosts too — and it rides back on
:attr:`~embodiment.subagent.SubagentResult.degradations`. Read through
:func:`embodiment.ledger.read`, every such record carries the child's
``child_task_id``, so *who degraded?* is answerable without inference.

**No repo access, no dial configuration.** Neither surface names a file, a
shell or a process verb, and the child's :class:`~embodiment.contract.Task`
carries an empty ``repo_path``: containment is the arm design's job, not an
advert's. The worker's mind arrives as an injected
:data:`~embodiment.loop.CompleteFn`; endpoints, models and dialling belong to
``examples/worker_seam.py`` (task ``t2``), and ``model`` here is a label
recorded for traces, never something this module resolves.

**The fan-out (``t4``) adds a fourth property, and it is the delicate one.**
The subagent seam was designed for *serial* spawns: ``loop._delegate`` re-checks
the allowance and the remaining budget before **every** spawn, so serial
delegation cannot outspend its parent however often it is called. ``delegate_all``
gets **one** of those checks and then runs N children behind it, concurrently
and out of order — the re-check is gone. So the accounting, the partial-failure
behaviour and the termination bound have to be *defined*, not inherited:

* :data:`FANOUT_ACCOUNTING_RULE` states the accounting in words, quotably. In
  one line: the single grant the loop hands over is **partitioned before any
  unit starts**, an unfunded unit is never dispatched, an absent unit is charged
  its whole slice, and nothing reclaims another unit's remainder.
* A unit that fails, or misses the deadline, lands a **recorded degradation and
  the other units' results**. It never aborts the parent's drive, and it never
  vanishes: refusals reach the acting model as prose, degradations reach the
  ledger with :attr:`FanoutDegradation.unit_task_id` naming the unit.
* Termination is bounded **structurally**: this module contains no ``while`` at
  all, the width is capped by :data:`MAX_FANOUT_WIDTH` *and* by the grant, the
  one :func:`concurrent.futures.wait` carries a finite timeout, and teardown
  never joins a blocked thread.

Concurrency is **standard library only** (:mod:`threading`,
:mod:`concurrent.futures`) — no new dependency, and nothing under ``embodiment/``
changes for this either.

Usage::

    uv run python examples/orchestrator_tools.py            # serial, readable
    uv run python examples/orchestrator_tools.py --json     # serial, machine-readable
    uv run python examples/orchestrator_tools.py --fanout   # parallel fan-out
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from embodiment import (
    LoopAborted,
    LoopOutcome,
    ModelResponse,
    Task,
    ToolCall,
    ToolError,
    ToolOutcome,
    UnknownToolError,
    frame_cortex,
    frame_subagent,
    ledger,
    run,
)
from embodiment.contract import ERROR, OK, SubResult
from embodiment.loop import CompleteFn
from embodiment.subagent import (
    NO_SPAWNS,
    SpawnRequest,
    SubagentCall,
    SubagentFn,
    SubagentResult,
    as_count,
)

# ── the enumerated surfaces (h28: the harness passes exactly this) ───────────

#: The role label recorded on every :class:`~embodiment.subagent.SpawnRequest`
#: this module builds. Recorded only — a label changes no authority, exactly as
#: identity framing changes none.
ROLE_WORKER = "worker"

#: The orchestrator's complete tool surface. ``finish`` lives here and nowhere
#: else, which is what "the cortex keeps final authority" means in code.
ORCHESTRATOR_TOOLS = ("delegate", "note", "finish")

#: The worker's complete tool surface — the pre-registration, quotable as-is.
#: One verb: report back. No repo verb, no shell verb, no onward ``delegate``,
#: and above all no ``finish``.
WORKER_TOOLS = ("report",)

#: The verbs that end the *task* rather than one drive. A surface that shares
#: no member with this tuple cannot be a second final authority.
FINAL_AUTHORITY_TOOLS = ("finish",)

#: An empty repo path: this rig hands the worker no repository (c36).
NO_REPO = ""

#: How many workers the orchestrator may consult in one drive. Two, so the
#: demo can show the bound *biting* on a third rather than only asserting it.
DEFAULT_ALLOWANCE = 2

#: The orchestrator's own turn budget. Child turns are charged against it, so
#: it has to cover both levels — this is the loop's guarantee, not a spare
#: allocation: nothing here can spend past it.
DEFAULT_ORCHESTRATOR_MAX_STEPS = 24

#: The turn budget offered to each worker. Clamped down by the loop to whatever
#: the parent has left; never up.
DEFAULT_WORKER_MAX_STEPS = 4

#: The default parent task id, and the stem of every child's id.
DEFAULT_TASK_ID = "orchestrator-1"

#: The engine recorded on each child's :class:`~embodiment.contract.SubResult`,
#: so a trace names what actually produced the contribution.
ENGINE = "embodiment"


# ── the parallel fan-out (task t4) ───────────────────────────────────────────

#: The fan-out verb. It lives on :data:`FANOUT_ORCHESTRATOR_TOOLS` and on no
#: other surface — a unit cannot fan out again, so width is one level deep.
FANOUT_TOOL = "delegate_all"

#: The fan-out orchestrator's complete tool surface: the serial verbs plus one.
#: ``finish`` is still here and still nowhere else.
FANOUT_ORCHESTRATOR_TOOLS = ("delegate", FANOUT_TOOL, "note", "finish")

#: The role recorded on the BATCH spawn. A label, recorded only.
ROLE_FANOUT = "fanout"

#: How many units one fan-out may dispatch at once. The reference rig's worker
#: serves roughly fourteen concurrent streams, so a wider fan-out would queue
#: rather than parallelise; the width bound is set at the rig's stream count
#: instead of at a number nobody chose. Units beyond it are refused before
#: dispatch and reported — never silently dropped.
MAX_FANOUT_WIDTH = 14

#: The smallest grant a unit can be dispatched with. **One, not zero, and this
#: is load-bearing:** ``embodiment.loop.run`` sets ``turn_budget = max(1,
#: max_steps)``, so a unit dispatched on a zero-turn slice would still spend one
#: model turn that nothing budgeted. A zero slice is therefore a refusal, not a
#: dispatch.
MIN_UNIT_GRANT = 1

#: The batch turn budget the fan-out asks the loop for. Clamped down to whatever
#: the parent has left, exactly like every other request.
DEFAULT_FANOUT_MAX_STEPS = 12

#: Seconds the fan-out waits for its units before declaring the stragglers
#: absent. Finite by construction: an unbounded wait would hand a hung worker
#: the power to park the parent's drive forever.
#:
#: **Derived, never chosen** — issue #42's rule applied one layer up, to a
#: deadline that bounds a *drive* rather than a turn (claim c16). #42's audit
#: covered six client timeouts and missed this one entirely.
#:
#: ``bound = per-turn bound x the turn budget actually granted``, where the
#: per-turn bound is ``max_tokens / rate`` with rates from
#: ``docs/live-test-results/timeout-rate-measurements.json`` and the turn
#: budget is :data:`DEFAULT_FANOUT_MAX_STEPS`, read by
#: `tests/test_timeout_bounds.py` from this module rather than retyped::
#:
#:     per-turn = 16000 / 12.921 tok/s (the **worker**'s committed floor) = 1238.3 s
#:     deadline = 1238.3 x 12                                             = 14859.6 s
#:
#: **Queue time is absorbed here rather than added**, and the arithmetic says
#: so: the worker's cited rate is a wall-clock floor that already contains
#: queue and prefill (its own caveat in the rate config), and at this budget it
#: is slower than the worker's retry-clean floor by more than the 179.3 s
#: measured allowance. The test checks that inequality per budget instead of
#: trusting the flag — at 1200 tokens the same role does *not* absorb it.
#:
#: Raised 60.0 -> 14860.0, matching pre-registration amendment 2 on branch
#: ``owa/t12`` value for value and derivation for derivation. That parity is
#: chosen deliberately over a rounder number, and it costs a margin of **0.5 s**
#: — the thinnest of the eight. A running series is dialling this constant, and
#: main disagreeing with the branch by a hundred seconds would be a second
#: instrument difference to reason about for no gain. Any re-measurement of the
#: worker's rate downward will fail the bound test, which is the correct
#: response: re-derive, do not nudge. 60.0 was roughly **1/248th** of the work
#: it bounded, and the censoring it would have produced was biased against
#: exactly the arms the series exists to test: arm ``E`` never fans out, so only
#: the orchestrated arms could be cut, and ``fanout-unit-absent`` names a
#: straggler rather than a truncation.
#:
#: Layering, asserted by the same test: a unit whose endpoint is dead exhausts
#: ``WorkerSeam``'s retry ladder well inside this deadline, so a dead transport
#: still surfaces as a transport failure rather than as a straggler.
DEFAULT_FANOUT_TIMEOUT = 14860.0

#: Thread name prefix, so a stack dump names the lane that owns the thread.
FANOUT_THREADS = "fanout-unit"

#: Stamped on every fan-out tool result so a scripted cortex can count its own
#: batches out of the transcript rather than holding hidden state.
FANOUT_MARKER = "[fanned out]"

#: A unit that did not return before the deadline.
FANOUT_EXIT_ABSENT = "fanout-unit-absent"
#: A unit whose drive fell over in a way its own loop never saw.
FANOUT_EXIT_FAILED = "fanout-unit-failed"
#: A unit the plan never dispatched.
FANOUT_EXIT_NOT_RUN = "fanout-unit-not-run"
#: Every dispatched unit came back inside the deadline.
FANOUT_EXIT_COMPLETE = "fanout-complete"
#: At least one unit was refused, absent, or failed. Partial results still ride.
FANOUT_EXIT_PARTIAL = "fanout-partial"

#: A unit the grant did not stretch to. **The bound working, not a failure** —
#: so it records no degradation, for the same reason
#: :data:`~embodiment.subagent.SPAWN_REFUSED_BUDGET` records none.
REFUSED_UNFUNDED = "unfunded"
#: A unit beyond :data:`MAX_FANOUT_WIDTH`. Also the bound working.
REFUSED_OVER_WIDTH = "over-width"
#: Every way the plan can decline to dispatch a unit. Neither is a degradation;
#: both are reported on the plan and read back to the acting model as text.
FANOUT_REFUSALS = (REFUSED_UNFUNDED, REFUSED_OVER_WIDTH)


#: **The accounting rule.** Quotable verbatim; the tests below prove each clause.
FANOUT_ACCOUNTING_RULE = """\
A fan-out spends what ONE serial spawn may spend, and never more.

The loop hands the seam a single SubagentCall carrying a single grant,
`call.max_steps`, already narrowed to what the parent has left. That one grant
is PARTITIONED across the units before any of them starts — never shared,
never re-checked under contention.

1. `partition(grant, width)` splits the grant into per-unit slices whose sum is
   exactly the grant. It is a pure function of two integers, it runs on the
   calling thread before a single unit is dispatched, and it is the only
   producer of a unit's budget.
2. A unit whose slice is zero is NOT DISPATCHED. `embodiment.loop.run` takes
   `max(1, max_steps)` turns, so a zero-slice unit would still spend one turn
   nothing budgeted. An unfunded unit is refused before dispatch and the
   refusal is reported. That is the bound working, not a failure, so it mints
   no degradation — the reading `SPAWN_REFUSED_BUDGET` already gets.
3. Each dispatched unit is driven by `embodiment.loop.run(max_steps=slice)`,
   which bounds its own turns at its slice. Its actual spend is charged
   verbatim; a unit that somehow exceeds its slice is charged the overspend AND
   records a degradation, exactly as `loop._charge_child` does.
4. A unit that has not returned by the deadline is charged its WHOLE slice, not
   the zero turns it never reported. It may still be running, and a bound that
   assumes an absent worker spent nothing is not a bound.
5. No unit may borrow another's unspent remainder. Reclaim would need a shared
   counter read under contention, and a bound that depends on how threads
   interleave is not a bound. Unspent turns go back to the parent unspent.

Therefore charged = the sum over dispatched units of (what it actually spent,
or its whole slice when it is absent) <= the sum of the slices <= the grant =
call.max_steps <= what the parent had left. Concurrency changes WHEN turns are
spent, never HOW MANY.

Width is bounded independently by MAX_FANOUT_WIDTH, and every unit is a leaf
(allowance NO_SPAWNS), so a fan-out adds exactly one level to the tree and no
branching the grant does not already pay for.
"""


# ── this host's degradation vocabulary (C3 applies to hosts too) ─────────────

#: The worker's drive ended without a report. The orchestrator asked for help
#: and got none — recorded, never passed off as a quiet no-op.
DEGRADED_WORKER_NO_REPORT = "worker-no-report"
#: The worker's drive raised (a mind that fell over, a seam that broke). The
#: parent is NOT taken down with it: the delegation failed, the drive continues.
DEGRADED_WORKER_ABORTED = "worker-drive-aborted"
#: Every code this host can mint. Deliberately disjoint from embodiment's own
#: vocabulary — a host's codes are the host's, and the ledger relays them under
#: :data:`embodiment.ledger.SOURCE_SUBAGENT` with the child's id attached.
WORKER_DEGRADATIONS = (DEGRADED_WORKER_NO_REPORT, DEGRADED_WORKER_ABORTED)

#: A dispatched unit did not return before the fan-out's deadline. It may still
#: be running, which is exactly why it is charged its whole slice.
DEGRADED_FANOUT_TIMEOUT = "fanout-unit-timeout"
#: A unit reported spending more than its slice. Charged verbatim and recorded,
#: the same reading :func:`embodiment.loop._charge_child` gives an overspend:
#: clamping would make the grant quietly mean less than it says.
DEGRADED_FANOUT_OVERSPEND = "fanout-unit-overspend"
#: The codes the fan-out lane adds. Kept separate from
#: :data:`WORKER_DEGRADATIONS` so the serial tuple stays exactly what task
#: ``t1`` pre-registered.
FANOUT_DEGRADATIONS = (DEGRADED_FANOUT_TIMEOUT, DEGRADED_FANOUT_OVERSPEND)
#: Every code this host can mint, at either shape.
HOST_DEGRADATIONS = WORKER_DEGRADATIONS + FANOUT_DEGRADATIONS


# ── the prompts ──────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM = (
    "You are working through a task with the tools you have been given. You may "
    "delegate a scoped subtask to a worker with `delegate`; the worker reports "
    "back to you and cannot act further. Weigh what it returns, then answer with "
    "`finish` — the final answer is yours to write, never the worker's."
)

WORKER_SYSTEM = (
    "You have been given one scoped subtask. Work it out and hand your findings "
    "back with `report`. You do not decide what happens next and you do not "
    "write the final answer; reporting back is the whole of your job."
)

FANOUT_ORCHESTRATOR_SYSTEM = (
    "You are working through a task with the tools you have been given. You may "
    "hand one scoped subtask to a worker with `delegate`, or hand several out at "
    "once with `delegate_all`; the workers report back to you and cannot act "
    "further. Weigh what they return — including the ones that returned nothing "
    "— then answer with `finish`. The final answer is yours to write, never a "
    "worker's."
)

DEMO_INSTRUCTION = (
    "Three gauges were read this morning: A 12%, B 47%, C 9%. Delegate the "
    "analysis, then answer."
)

#: The word the demo's scripted worker treats as "I cannot do this" — a stand-in
#: for the real thing (a model that answers in prose and calls nothing).
UNANSWERABLE = "unanswerable"

DEMO_SUBTASKS = (
    "summarise the three gauge readings in the briefing",
    f"reconcile the {UNANSWERABLE} discrepancy the briefing never mentions",
    "list the open questions worth asking next",
)

#: Five subtasks against a four-turn batch grant, so the fan-out demo shows the
#: partition *biting*: four units are funded a turn each and the fifth is
#: refused before dispatch rather than dispatched onto turns nobody has.
DEMO_FANOUT_SUBTASKS = (
    "summarise the three gauge readings in the briefing",
    f"reconcile the {UNANSWERABLE} discrepancy the briefing never mentions",
    "list the open questions worth asking next",
    "check whether the readings drifted since the last briefing",
    "draft the one-line headline for the morning note",
)

#: The batch grant the fan-out demo asks for. Deliberately smaller than the
#: number of subtasks — see :data:`DEMO_FANOUT_SUBTASKS`.
DEMO_FANOUT_GRANT = 4

#: Stamped on every delegate result so a scripted cortex can count its own
#: delegations out of the transcript rather than holding hidden state.
DELEGATION_MARKER = "[delegated]"


def orchestrator_task(instruction: str, *, task_id: str = DEFAULT_TASK_ID) -> Task:
    """The parent work item. No repo path: this rig grants no repository."""
    return Task(id=task_id, repo_path=NO_REPO, instruction=instruction)


def worker_instruction(subtask: str) -> str:
    """What the child is told to do. The subtask, and the boundary around it."""
    return (
        f"Subtask: {subtask}\n\n"
        "Work only this subtask. When you have an answer, call `report` with your "
        "findings. You have no other tools."
    )


def orchestrator_prompt(base: str = ORCHESTRATOR_SYSTEM, *, identity: Optional[str] = None) -> str:
    """Frame the **top-level acting loop** — and only it.

    Every prompt-bearing role gets the *same* resolved identity with
    role-specific authority (colleague#352): the orchestrator is framed as the
    cortex here, the worker as a typed subagent in
    :meth:`OrchestratorExecutor._delegate`. Framing the worker but not the
    cortex would leave the one mind that actually decides unnamed while the
    delegated one is told who it is not.

    With no configured identity this returns *base* unchanged, byte for byte —
    the acceptance criterion that keeps the feature honest.
    """
    return frame_cortex(base, identity=identity, muse=False) or base


# ── shapes ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkerDegradation:
    """One degradation this host minted about one worker.

    Shaped like every lane record embodiment already reads — ``code`` plus
    ``reason`` — so :func:`embodiment.ledger.from_subagent` folds it with no
    special case and stamps the child's id onto it.
    """

    code: str
    reason: str
    step_index: Optional[int] = None
    model_turns: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "step_index": self.step_index,
            "model_turns": self.model_turns,
        }


@dataclass
class WorkerRun:
    """One worker consultation, as the host recorded it.

    Everything a trace needs about a single child: which id it ran under, what
    it was asked, which role and model produced it, how far it reached, what it
    cost, what it returned, and what it refused along the way.
    """

    child_task_id: str
    subtask: str = ""
    role: str = ""
    model: str = ""
    allowance: int = NO_SPAWNS
    max_steps: int = 0
    lineage: tuple[str, ...] = ()
    exit_reason: str = ""
    model_turns: int = 0
    charged: int = 0
    """What the PARENT was billed for this child. Equal to ``model_turns`` on
    every path where the child came back; a fan-out unit that never returned is
    charged its whole slice instead, because it may still be spending it."""
    report: str = ""
    refused_tools: list[str] = field(default_factory=list)
    degradation_codes: list[str] = field(default_factory=list)
    sealed: bool = False
    """Fan-out only: the deadline passed and this unit's outcome was collected
    without it. A sealed record is FINAL — see :func:`_publish`."""
    late: bool = False
    """Fan-out only: this unit landed after it was declared absent. Recorded
    rather than hidden, and it changes nothing else about the record."""
    outcome: Optional[LoopOutcome] = None
    """The child's own :class:`~embodiment.loop.LoopOutcome`, for a caller that
    wants its steps. Deliberately absent from :meth:`to_dict` — it is a live
    object, not report data."""

    @property
    def depth(self) -> int:
        """How many ancestors this child has. One, for a serial delegation."""
        return len(self.lineage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_task_id": self.child_task_id,
            "subtask": self.subtask,
            "role": self.role,
            "model": self.model,
            "allowance": self.allowance,
            "max_steps": self.max_steps,
            "lineage": list(self.lineage),
            "depth": self.depth,
            "exit_reason": self.exit_reason,
            "model_turns": self.model_turns,
            "charged": self.charged,
            "report": self.report,
            "refused_tools": list(self.refused_tools),
            "degradation_codes": list(self.degradation_codes),
            "late": self.late,
        }


@dataclass
class DelegationLog:
    """Every worker consultation, in order, plus how many ever ran at once.

    ``max_in_flight`` exists because "serial" is a claim, and a claim that is
    only true because the code happens to be single-threaded is worth measuring
    rather than assuming — the parallel fan-out below reads the same field and
    sees a different number, which is how the tests tell the two shapes apart.

    Every mutation takes :attr:`lock`, because the fan-out's units call
    :meth:`begin` and :meth:`leave` from their own threads and ``+= 1`` is not
    atomic. On the serial path the lock is never contended and the semantics are
    byte-identical to the unlocked version task ``t1`` shipped.

    :meth:`track` and :meth:`begin` are separate so the fan-out can register
    every unit in **plan order** on the calling thread, then let the units enter
    and leave in whatever order they finish. A ``runs`` list ordered by
    completion would make the report non-deterministic for no gain.
    """

    runs: list[WorkerRun] = field(default_factory=list)
    in_flight: int = 0
    max_in_flight: int = 0
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def track(self, record: WorkerRun) -> WorkerRun:
        """Register a consultation in the order it was PLANNED."""
        with self.lock:
            self.runs.append(record)
        return record

    def begin(self) -> None:
        """One consultation started; raise the high-water mark if it is one."""
        with self.lock:
            self.in_flight += 1
            if self.in_flight > self.max_in_flight:
                self.max_in_flight = self.in_flight

    def enter(self, record: WorkerRun) -> WorkerRun:
        self.track(record)
        self.begin()
        return record

    def leave(self) -> None:
        with self.lock:
            self.in_flight -= 1

    @property
    def child_task_ids(self) -> list[str]:
        return [record.child_task_id for record in self.runs]


# ── the tool surfaces ────────────────────────────────────────────────────────


class WorkerExecutor:
    """The worker's whole surface: :data:`WORKER_TOOLS`, and nothing else.

    ``report`` ends the *child's* drive and hands its findings back. It is not
    ``finish``: the child's ``finished`` flag terminates the child's own
    :func:`embodiment.loop.run`, and the orchestrator's drive carries on to
    decide what the report is worth.
    """

    def __init__(self, *, subtask: str) -> None:
        self.subtask = subtask
        self.calls: list[str] = []
        self.refused: list[str] = []
        self.report: str = ""

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in WORKER_TOOLS:
            # Refused, not answered. See this module's docstring, point 1.
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available to a worker; its tools are {list(WORKER_TOOLS)}"
            )
        findings = str(arguments.get("findings") or "").strip()
        if not findings:
            raise ToolError("report requires `findings`: say what you worked out")
        self.report = findings
        return ToolOutcome(
            result=f"report recorded: {findings}",
            finished=True,
            finish_summary=findings,
        )


class OrchestratorExecutor:
    """The orchestrator's surface: :data:`ORCHESTRATOR_TOOLS`.

    ``delegate`` is the whole feature. It builds the child's task and the
    child's *already-narrowed* tool surface, and hands both to the loop on a
    :class:`~embodiment.subagent.SpawnRequest`. The loop neither inspects nor
    widens either — attenuation is arithmetic it does on top.

    :attr:`tools` is the surface this executor dispatches against — a class
    attribute rather than a module constant so a wider surface
    (:class:`FanoutOrchestratorExecutor`) is a subclass and not a copy. It is
    still the enumeration itself, not a comment beside one.
    """

    #: This executor's complete tool surface.
    tools: tuple[str, ...] = ORCHESTRATOR_TOOLS

    def __init__(
        self,
        *,
        task_id: str = DEFAULT_TASK_ID,
        worker_model: str = "",
        worker_max_steps: int = DEFAULT_WORKER_MAX_STEPS,
        worker_system: str = WORKER_SYSTEM,
        identity: Optional[str] = None,
    ) -> None:
        self.task_id = task_id
        self.worker_model = worker_model
        self.worker_max_steps = worker_max_steps
        self.worker_system = worker_system
        self.identity = identity
        self.calls: list[str] = []
        self.refused: list[str] = []
        self.notes: list[str] = []
        self.children: list[WorkerExecutor] = []
        self.delegations = 0
        self.fanouts = 0
        self.finished = False
        self.finish_summary = ""

    def child_task_id(self, ordinal: int) -> str:
        """``<parent>-worker-<n>`` — readable, and unique per consultation."""
        return f"{self.task_id}-worker-{ordinal}"

    def batch_task_id(self, ordinal: int) -> str:
        """``<parent>-fanout-<n>`` — the BATCH's id, and every unit id's stem."""
        return f"{self.task_id}-fanout-{ordinal}"

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in self.tools:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available to the orchestrator; its tools are "
                f"{list(self.tools)}"
            )
        if name == "delegate":
            return self._delegate(arguments)
        if name == "note":
            text = str(arguments.get("text") or "").strip()
            if not text:
                raise ToolError("note requires `text`")
            self.notes.append(text)
            return ToolOutcome(result="noted")
        summary = str(arguments.get("summary") or "").strip()
        self.finished = True
        self.finish_summary = summary
        return ToolOutcome(result="submitted", finished=True, finish_summary=summary)

    def _delegate(self, arguments: dict[str, Any]) -> ToolOutcome:
        subtask = str(arguments.get("subtask") or "").strip()
        if not subtask:
            raise ToolError("delegate requires a `subtask`: say what the worker should do")
        self.delegations += 1
        child_executor = WorkerExecutor(subtask=subtask)
        self.children.append(child_executor)
        child_task = Task(
            id=self.child_task_id(self.delegations),
            repo_path=NO_REPO,
            instruction=worker_instruction(subtask),
        )
        return ToolOutcome(
            result=f"{DELEGATION_MARKER} {subtask}",
            spawn=SpawnRequest(
                task=child_task,
                executor=child_executor,
                role=ROLE_WORKER,
                # The worker is a LEAF: it may consult no one. The loop would
                # have granted `attenuate(parent)`; asking for less is the one
                # direction a request may move a bound.
                allowance=NO_SPAWNS,
                max_steps=self.worker_max_steps,
                system_prompt=frame_subagent(self.worker_system, identity=self.identity),
                model=self.worker_model,
                context={"subtask": subtask},
            ),
        )


# ── the injected seam ────────────────────────────────────────────────────────


def _codes(degradations: list[Any]) -> list[str]:
    return [str(getattr(record, "code", record)) for record in degradations]


def build_worker_seam(complete: CompleteFn, *, log: Optional[DelegationLog] = None) -> SubagentFn:
    """Wrap an injected worker mind as a :data:`~embodiment.subagent.SubagentFn`.

    The whole protocol, and nothing beyond it: ``call.allowance`` becomes the
    child's ``spawn_allowance``, ``call.lineage`` its ``lineage``, and
    ``call.max_steps`` its budget. The seam invents no bound of its own, and it
    reports the child's turns honestly — the loop charges them to the parent.

    A child whose drive raises does **not** take the parent down: the failure
    is recorded as :data:`DEGRADED_WORKER_ABORTED` and fed back as one step, so
    the orchestrator can decide without the worker.
    """

    def seam(call: SubagentCall) -> Optional[SubagentResult]:
        context = call.context or {}
        record = WorkerRun(
            child_task_id=call.task.id,
            subtask=str(context.get("subtask") or ""),
            role=call.role or "",
            model=call.model,
            allowance=call.allowance,
            max_steps=call.max_steps,
            lineage=tuple(call.lineage),
        )
        if log is not None:
            log.enter(record)
        try:
            return _drive_worker(complete, call, record)
        finally:
            if log is not None:
                log.leave()

    return seam


def _drive_worker(complete: CompleteFn, call: SubagentCall, record: WorkerRun) -> SubagentResult:
    """One child drive, and the accounting that comes back with it."""
    try:
        child = run(
            complete,
            call.task,
            executor=call.executor,
            max_steps=call.max_steps,
            spawn_allowance=call.allowance,
            lineage=call.lineage,
            subagent=None,
            system_prompt=call.system_prompt,
            model=call.model,
        )
    except LoopAborted as aborted:
        return _worker_failed(call, record, aborted)

    record.outcome = child
    record.exit_reason = child.exit_reason
    record.model_turns = child.result.stats.model_turns
    record.charged = record.model_turns
    record.report = str(getattr(call.executor, "report", "") or "")
    record.refused_tools = list(getattr(call.executor, "refused", []))

    degradations: list[Any] = list(child.degradations)
    if not record.report:
        degradations.append(
            WorkerDegradation(
                code=DEGRADED_WORKER_NO_REPORT,
                reason=(
                    f"the worker ended {child.exit_reason} without calling `report`; "
                    "the orchestrator decides without it"
                ),
                model_turns=record.model_turns,
            )
        )
    record.degradation_codes = _codes(degradations)

    reported = bool(record.report)
    return SubagentResult(
        sub_result=SubResult(
            task_id=call.task.id,
            engine=ENGINE,
            model=call.model,
            status=OK if reported else ERROR,
            summary=record.report or "no report",
            role=call.role,
        ),
        model_turns=record.model_turns,
        degradations=degradations,
        result=(
            f"worker {call.task.id} reports: {record.report}"
            if reported
            else f"worker {call.task.id} returned no report; decide without it"
        ),
        exit_reason=child.exit_reason,
    )


def _worker_failed(call: SubagentCall, record: WorkerRun, aborted: LoopAborted) -> SubagentResult:
    """A child drive that raised. Recorded, charged, and handed back as text."""
    outcome = aborted.outcome
    record.outcome = outcome
    record.exit_reason = outcome.exit_reason
    record.model_turns = outcome.result.stats.model_turns
    record.charged = record.model_turns
    record.refused_tools = list(getattr(call.executor, "refused", []))
    degradations: list[Any] = [
        *outcome.degradations,
        WorkerDegradation(
            code=DEGRADED_WORKER_ABORTED,
            reason=f"the worker's drive failed: {outcome.result.error}",
            model_turns=record.model_turns,
        ),
    ]
    record.degradation_codes = _codes(degradations)
    return SubagentResult(
        model_turns=record.model_turns,
        degradations=degradations,
        result=f"worker {call.task.id} failed to answer; decide without it",
        exit_reason=outcome.exit_reason,
    )


# ── running one orchestration ────────────────────────────────────────────────


@dataclass
class DelegationOutcome:
    """What one orchestration produced, at both levels."""

    outcome: LoopOutcome
    executor: OrchestratorExecutor
    log: DelegationLog
    aborted: bool = False

    def records(self) -> list[Any]:
        """The one degradation stream, children attributed.

        :func:`embodiment.ledger.read` folds the parent's own records and every
        granted child's, stamping ``child_task_id`` on the latter. A parent
        record's ``child_task_id`` stays ``None`` — which is how a reader tells
        "the orchestrator degraded" from "worker 2 degraded" without guessing.
        """
        return ledger.read(loop=self.outcome)

    def attribution(self) -> dict[str, list[str]]:
        """``child_task_id`` → the codes that child was responsible for."""
        grouped: dict[str, list[str]] = {}
        for record in self.records():
            child_id = record.child_task_id
            if child_id is None:
                continue
            grouped.setdefault(child_id, []).append(record.code)
        return grouped

    def report(self) -> dict[str, Any]:
        """A JSON-ready account of the whole orchestration."""
        return {
            "orchestrator": {
                "task_id": self.executor.task_id,
                "tools": list(self.executor.tools),
                "model": self.outcome.result.stats.model,
                "exit_reason": self.outcome.exit_reason,
                "summary": self.outcome.result.summary,
                "model_turns": self.outcome.result.stats.model_turns,
                "child_model_turns": self.outcome.child_model_turns,
                "spawn_allowance_remaining": self.outcome.spawn_allowance_remaining,
                "delegations": self.executor.delegations,
                "notes": list(self.executor.notes),
                "aborted": self.aborted,
            },
            "worker": {
                "tools": list(WORKER_TOOLS),
                "withheld_tools": sorted(set(self.executor.tools) - set(WORKER_TOOLS)),
                "max_in_flight": self.log.max_in_flight,
                "runs": [record.to_dict() for record in self.log.runs],
            },
            "spawns": [record.to_dict() for record in self.outcome.spawns],
            "ledger": [record.to_dict() for record in self.records()],
            "attribution": self.attribution(),
        }


def run_delegation(
    cortex: CompleteFn,
    worker: CompleteFn,
    *,
    task: Task,
    max_steps: int = DEFAULT_ORCHESTRATOR_MAX_STEPS,
    worker_max_steps: int = DEFAULT_WORKER_MAX_STEPS,
    cortex_model: str = "",
    worker_model: str = "",
    identity: Optional[str] = None,
    system_prompt: str = ORCHESTRATOR_SYSTEM,
    spawn_allowance: int = DEFAULT_ALLOWANCE,
) -> DelegationOutcome:
    """Drive one orchestration with two injected minds.

    Both minds are arguments. This module dials nothing: ``cortex_model`` and
    ``worker_model`` are labels recorded on the artifact and on each child's
    ``SubResult`` so a trace names what produced every contribution — resolving
    an endpoint from either is ``examples/worker_seam.py``'s job (task ``t2``).
    """
    executor = OrchestratorExecutor(
        task_id=task.id,
        worker_model=worker_model,
        worker_max_steps=worker_max_steps,
        identity=identity,
    )
    log = DelegationLog()
    try:
        outcome = run(
            cortex,
            task,
            executor=executor,
            max_steps=max_steps,
            system_prompt=orchestrator_prompt(system_prompt, identity=identity),
            subagent=build_worker_seam(worker, log=log),
            spawn_allowance=spawn_allowance,
            model=cortex_model,
        )
    except LoopAborted as aborted:
        return DelegationOutcome(outcome=aborted.outcome, executor=executor, log=log, aborted=True)
    return DelegationOutcome(outcome=outcome, executor=executor, log=log)


# ── the parallel fan-out: one tool call, N concurrent units ──────────────────
#
# Read :data:`FANOUT_ACCOUNTING_RULE` first. Everything below is that rule as
# executable code, and ``tests/test_orchestrator_tools.py`` proves each clause.
#
# Why this needs a rule at all: the subagent seam was designed for SERIAL
# spawns. ``loop._delegate`` re-checks the allowance and the remaining budget
# before every spawn, so a serial delegation cannot outspend the parent no
# matter how many times it is called. A fan-out gets ONE of those checks and
# then runs N children behind it, on threads, out of order. The re-check is
# gone; the partition below replaces it.


def partition(grant: int, width: int) -> tuple[int, ...]:
    """Split ONE grant into *width* slices whose sum is exactly the grant.

    **The only producer of a unit's budget**, and the whole of the fan-out's
    arithmetic. Pure, total, and computed on the calling thread before a single
    unit is dispatched — so the bound cannot be raced, because by the time any
    thread exists every slice is already fixed.

    ``divmod`` is the one operation: ``base`` goes to everyone and the remainder
    is spread one turn each over the first few, so the slices differ by at most
    one and their sum is ``grant`` for every input. When *width* exceeds *grant*
    the tail slices come out at zero, which is exactly the signal
    :func:`plan_fanout` reads as *unfunded* — the shortfall is expressed by the
    same formula rather than by a special case beside it.
    """
    if width <= 0:
        return ()
    base, extra = divmod(as_count(grant), width)
    return tuple(base + 1 if index < extra else base for index in range(width))


@dataclass(frozen=True)
class FanoutUnit:
    """One planned unit. Frozen: a plan settled before dispatch stays settled."""

    ordinal: int
    subtask: str
    unit_task_id: str
    grant: int
    refusal: str = ""
    """One of :data:`FANOUT_REFUSALS`, or ``""`` for a unit that was dispatched.
    A refusal is the bound working, so it mints no degradation — but it IS
    reported, both on the plan and in the text the acting model reads back."""

    @property
    def dispatched(self) -> bool:
        return not self.refusal

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "subtask": self.subtask,
            "unit_task_id": self.unit_task_id,
            "grant": self.grant,
            "dispatched": self.dispatched,
            "refusal": self.refusal,
        }


@dataclass(frozen=True)
class FanoutPlan:
    """The whole fan-out, decided before any thread exists.

    Every number a reader needs to check the accounting rule is here, so
    "did this fan-out stay inside its grant?" is answerable from the artifact
    rather than by re-deriving it from the runs.
    """

    grant: int
    width: int
    units: tuple[FanoutUnit, ...] = ()

    @property
    def dispatched(self) -> tuple[FanoutUnit, ...]:
        return tuple(unit for unit in self.units if unit.dispatched)

    @property
    def refused(self) -> tuple[FanoutUnit, ...]:
        return tuple(unit for unit in self.units if not unit.dispatched)

    @property
    def committed(self) -> int:
        """Turns the plan has promised away. Never more than :attr:`grant`."""
        return sum(unit.grant for unit in self.dispatched)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant": self.grant,
            "width": self.width,
            "committed": self.committed,
            "units": [unit.to_dict() for unit in self.units],
        }


def plan_fanout(
    subtasks: tuple[str, ...],
    *,
    grant: int,
    stem: str,
    width_limit: int = MAX_FANOUT_WIDTH,
) -> FanoutPlan:
    """Turn N subtasks and one grant into a settled plan. No threads, no IO.

    Two bounds bite here and nowhere else, and both are visible in the plan:

    * *width_limit* — units past it are refused :data:`REFUSED_OVER_WIDTH`.
      Truncating silently would be a degradation nobody could see (C3).
    * the grant — a unit whose slice falls below :data:`MIN_UNIT_GRANT` is
      refused :data:`REFUSED_UNFUNDED` and never dispatched. See that constant:
      a zero-turn dispatch still costs one turn.
    """
    considered = tuple(subtasks)
    within = considered[: max(0, width_limit)]
    slices = partition(grant, len(within))
    units = [
        FanoutUnit(
            ordinal=index + 1,
            subtask=subtask,
            unit_task_id=f"{stem}-{index + 1}",
            grant=slices[index],
            refusal="" if slices[index] >= MIN_UNIT_GRANT else REFUSED_UNFUNDED,
        )
        for index, subtask in enumerate(within)
    ]
    units.extend(
        FanoutUnit(
            ordinal=len(within) + offset + 1,
            subtask=subtask,
            unit_task_id=f"{stem}-{len(within) + offset + 1}",
            grant=0,
            refusal=REFUSED_OVER_WIDTH,
        )
        for offset, subtask in enumerate(considered[len(within) :])
    )
    return FanoutPlan(grant=as_count(grant), width=len(considered), units=tuple(units))


@dataclass(frozen=True)
class FanoutDegradation:
    """One degradation this host minted about one UNIT of one fan-out.

    Shaped like every lane record embodiment already reads (``code`` +
    ``reason``) so :func:`embodiment.ledger.from_subagent` folds it with no
    special case — and it carries :attr:`unit_task_id` besides, because the
    loop's own attribution can only name the *batch*: a fan-out is one spawn, so
    :attr:`~embodiment.subagent.SpawnRecord.child_task_id` is the batch's id.
    The ledger keeps this object on ``LedgerRecord.original``, so *which unit
    degraded?* is answerable from a structured field, and the reason names it
    too for a reader who only has the text.
    """

    code: str
    reason: str
    unit_task_id: str = ""
    step_index: Optional[int] = None
    model_turns: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "reason": self.reason,
            "unit_task_id": self.unit_task_id,
            "step_index": self.step_index,
            "model_turns": self.model_turns,
        }


def _attribute(entry: Any, unit_task_id: str) -> FanoutDegradation:
    """Re-stamp one lane record with the unit it belongs to.

    The ``code`` is carried through verbatim so the ledger still resolves the
    record to its MINTING lane — a loop code stays a loop code, exactly as
    :func:`embodiment.ledger.from_subagent` documents.
    """
    reason = str(getattr(entry, "reason", "") or "")
    return FanoutDegradation(
        code=str(getattr(entry, "code", entry)),
        reason=f"unit {unit_task_id}: {reason}" if reason else f"unit {unit_task_id}",
        unit_task_id=unit_task_id,
        step_index=getattr(entry, "step_index", None),
        model_turns=getattr(entry, "model_turns", None),
    )


class FanoutOrchestratorExecutor(OrchestratorExecutor):
    """The orchestrator that can also fan out: :data:`FANOUT_ORCHESTRATOR_TOOLS`.

    ``delegate_all`` returns ONE :class:`~embodiment.subagent.SpawnRequest` for
    the whole batch, because one tool call yields one ``ToolOutcome`` and one
    ``ToolOutcome`` carries one spawn. That is not a workaround — it is what
    makes the accounting tractable: the loop performs its allowance and budget
    checks once, hands over one grant, and the seam is answerable for staying
    inside it.

    The batch carries ``executor=None`` on purpose. A batch is a *plan*, not a
    drive: no model turn is ever spent on it and it has no tool surface of its
    own. Each unit gets its own :class:`WorkerExecutor`, built by the seam, with
    the same one-verb surface a serially delegated worker gets.
    """

    tools = FANOUT_ORCHESTRATOR_TOOLS

    def __init__(
        self,
        *,
        fanout_max_steps: int = DEFAULT_FANOUT_MAX_STEPS,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.fanout_max_steps = fanout_max_steps

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name == FANOUT_TOOL:
            self.calls.append(name)
            return self._delegate_all(arguments)
        return super().execute(name, arguments)

    def _delegate_all(self, arguments: dict[str, Any]) -> ToolOutcome:
        subtasks = _read_subtasks(arguments.get("subtasks"))
        self.fanouts += 1
        batch = Task(
            id=self.batch_task_id(self.fanouts),
            repo_path=NO_REPO,
            instruction=fanout_instruction(subtasks),
        )
        return ToolOutcome(
            result=f"{FANOUT_MARKER} {len(subtasks)} subtask(s)",
            spawn=SpawnRequest(
                task=batch,
                executor=None,
                role=ROLE_FANOUT,
                # Every unit is a LEAF. The batch asks for no onward spawns, so
                # `child_call` grants zero and each unit inherits that zero —
                # a fan-out adds one level to the tree and never a branch below.
                allowance=NO_SPAWNS,
                max_steps=self.fanout_max_steps,
                system_prompt=frame_subagent(self.worker_system, identity=self.identity),
                model=self.worker_model,
                context={"subtasks": subtasks},
            ),
        )


def _read_subtasks(value: Any) -> tuple[str, ...]:
    """Coerce the model's ``subtasks`` argument; raise on anything unusable.

    A :class:`~embodiment.loop.ToolError` is a self-correcting step the model
    reads and retries. Dropping a blank entry instead would be a silent loss of
    work the orchestrator asked for.
    """
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ToolError(
            f"{FANOUT_TOOL} requires `subtasks`: a list of scoped subtasks, "
            f"not {type(value).__name__}"
        )
    subtasks = tuple(str(item).strip() for item in items)
    if not subtasks:
        raise ToolError(f"{FANOUT_TOOL} requires at least one subtask")
    if not all(subtasks):
        raise ToolError(f"{FANOUT_TOOL} was given a blank subtask; say what each worker should do")
    return subtasks


def fanout_instruction(subtasks: tuple[str, ...]) -> str:
    """What the batch records it was asked to do. Never driven; recorded."""
    lines = "\n".join(f"{index + 1}. {subtask}" for index, subtask in enumerate(subtasks))
    return f"Work these {len(subtasks)} subtasks in parallel, one worker each:\n{lines}"


def _unit_record(call: SubagentCall, unit: FanoutUnit) -> WorkerRun:
    """The host's record for one unit, built in plan order before dispatch."""
    return WorkerRun(
        child_task_id=unit.unit_task_id,
        subtask=unit.subtask,
        role=call.role or "",
        model=call.model,
        allowance=call.allowance,
        max_steps=unit.grant,
        lineage=(*call.lineage, call.task.id),
        exit_reason="" if unit.dispatched else FANOUT_EXIT_NOT_RUN,
    )


def _unit_call(call: SubagentCall, unit: FanoutUnit) -> SubagentCall:
    """The batch's :class:`SubagentCall`, narrowed to one unit.

    ``max_steps`` becomes the unit's slice — the only field that shrinks the
    budget, and it can only shrink it, because :func:`partition` never returns
    a slice larger than the grant it was given. ``allowance`` is carried
    through unchanged and is already ``NO_SPAWNS``.
    """
    return replace(
        call,
        task=Task(
            id=unit.unit_task_id,
            repo_path=NO_REPO,
            instruction=worker_instruction(unit.subtask),
        ),
        executor=WorkerExecutor(subtask=unit.subtask),
        max_steps=unit.grant,
        lineage=(*call.lineage, call.task.id),
        context={"subtask": unit.subtask},
    )


#: Held only long enough to copy one unit's outcome into the record the
#: collector holds, or to seal that record against a late arrival. Never held
#: across a model call, so it cannot serialise the fan-out it exists to protect.
_PUBLISH = threading.Lock()


def _publish(record: WorkerRun, scratch: WorkerRun) -> bool:
    """Copy a unit's outcome into the collector's record, unless it is sealed.

    **The deadline's verdict is final.** Each unit drives against a private
    *scratch* record and publishes here at the end, so a unit that lands after
    the fan-out already declared it absent, charged its slice and reported it
    cannot rewrite that record underneath the reader. It is marked
    :attr:`WorkerRun.late` — recorded, not hidden — and nothing else changes.

    Without this the fan-out has a real race, not a theoretical one: the thread
    behind an absent unit is still running, and ``_drive_worker`` writes its
    fields straight into the record the collector has already read.
    """
    with _PUBLISH:
        if record.sealed:
            record.late = True
            return False
        record.exit_reason = scratch.exit_reason
        record.model_turns = scratch.model_turns
        record.charged = scratch.charged
        record.report = scratch.report
        record.refused_tools = list(scratch.refused_tools)
        record.degradation_codes = list(scratch.degradation_codes)
        record.outcome = scratch.outcome
        return True


def _drive_unit(
    complete: CompleteFn,
    call: SubagentCall,
    unit: FanoutUnit,
    record: WorkerRun,
    log: Optional[DelegationLog],
) -> list[Any]:
    """One unit's whole drive, on its own thread. **This never raises.**

    A thread that raises would leave its :class:`~concurrent.futures.Future`
    holding an exception the collecting side has to re-raise or swallow — and
    swallowing is exactly what C3 forbids. So every fault becomes a record here,
    on the thread that saw it, and the collecting side only ever reads lists.

    A unit that fell over is charged its WHOLE slice, not the turns its broken
    drive managed to report: the failure may have happened after the spend.
    """
    scratch = _unit_record(call, unit)
    found: list[Any] = []
    if log is not None:
        log.begin()
    try:
        reply = _drive_worker(complete, _unit_call(call, unit), scratch)
        found = [_attribute(entry, unit.unit_task_id) for entry in reply.degradations]
        if scratch.model_turns > unit.grant:
            found.append(
                FanoutDegradation(
                    code=DEGRADED_FANOUT_OVERSPEND,
                    reason=(
                        f"unit {unit.unit_task_id} spent {scratch.model_turns} model turn(s) "
                        f"of a {unit.grant}-turn slice; the overspend is charged"
                    ),
                    unit_task_id=unit.unit_task_id,
                    model_turns=scratch.model_turns,
                )
            )
    except Exception as exc:  # noqa: BLE001  # a failed unit never aborts its parent
        scratch.exit_reason = FANOUT_EXIT_FAILED
        scratch.charged = unit.grant
        found = [
            FanoutDegradation(
                code=DEGRADED_WORKER_ABORTED,
                reason=f"unit {unit.unit_task_id} fell over: {type(exc).__name__}: {exc}",
                unit_task_id=unit.unit_task_id,
                model_turns=unit.grant,
            )
        ]
    finally:
        if log is not None:
            log.leave()
    scratch.degradation_codes = _codes(found)
    return found if _publish(record, scratch) else []


def _absent(unit: FanoutUnit, record: WorkerRun, timeout: float) -> FanoutDegradation:
    """A unit that missed the deadline: sealed, charged its slice, and recorded.

    Sealing and charging happen under the same lock :func:`_publish` takes, so a
    unit landing at exactly the deadline resolves one way or the other and never
    half of each. Whichever way it resolves, the charge is at most the slice.
    """
    with _PUBLISH:
        record.sealed = True
        record.exit_reason = FANOUT_EXIT_ABSENT
        record.charged = unit.grant
        record.degradation_codes = [DEGRADED_FANOUT_TIMEOUT]
    return FanoutDegradation(
        code=DEGRADED_FANOUT_TIMEOUT,
        reason=(
            f"unit {unit.unit_task_id} did not return within {timeout}s; it may still be "
            f"spending, so its whole {unit.grant}-turn slice is charged"
        ),
        unit_task_id=unit.unit_task_id,
        model_turns=unit.grant,
    )


def _fan_out(
    complete: CompleteFn,
    call: SubagentCall,
    plan: FanoutPlan,
    *,
    log: Optional[DelegationLog],
    timeout: float,
) -> tuple[list[WorkerRun], list[Any], int]:
    """Dispatch the plan concurrently and collect it. Returns ``(runs, degradations, charged)``.

    Termination, by construction rather than by hope:

    * there is no ``while`` here or anywhere in this module — every loop walks a
      sequence fixed before it starts, and ``plan.dispatched`` is bounded by
      :data:`MAX_FANOUT_WIDTH` and by the grant;
    * :func:`concurrent.futures.wait` is called exactly once, with a finite
      *timeout*, so a hung unit cannot park the parent;
    * ``shutdown(wait=False)`` runs in a ``finally``, so teardown never joins a
      thread that is still blocked;
    * ``Future.result(timeout=0)`` is the only way a result is read, and it is
      read only for futures :func:`wait` already reported done — a call that
      cannot block even if that were wrong.
    """
    runs = [_unit_record(call, unit) for unit in plan.units]
    if log is not None:
        for record in runs:
            log.track(record)
    by_id = {record.child_task_id: record for record in runs}
    dispatched = plan.dispatched
    if not dispatched:
        return runs, [], 0

    pool = ThreadPoolExecutor(max_workers=len(dispatched), thread_name_prefix=FANOUT_THREADS)
    pending: dict[Future[list[Any]], FanoutUnit] = {}
    try:
        for unit in dispatched:
            future = pool.submit(_drive_unit, complete, call, unit, by_id[unit.unit_task_id], log)
            pending[future] = unit
        _, absent = wait(list(pending), timeout=timeout)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    degradations: list[Any] = []
    charged = 0
    # `pending` preserves insertion order, so collection walks the PLAN's order
    # and not the order threads happened to finish in. A report whose shape
    # depends on the scheduler is not a report.
    for future, unit in pending.items():
        record = by_id[unit.unit_task_id]
        if future in absent:
            degradations.append(_absent(unit, record, timeout))
        else:
            degradations.extend(future.result(timeout=0))
        charged = charged + record.charged
    return runs, degradations, charged


def _fanout_note(plan: FanoutPlan, runs: list[WorkerRun]) -> str:
    """The text the acting model reads back. Partial results, and what is missing.

    A refusal or an absence has to arrive as *prose*, not only as a ledger
    entry: a cortex that silently got three answers instead of five will
    synthesise as if five agreed.
    """
    by_id = {record.child_task_id: record for record in runs}
    lines = []
    for unit in plan.units:
        record = by_id.get(unit.unit_task_id)
        if not unit.dispatched:
            lines.append(f"  - {unit.unit_task_id} NOT RUN ({unit.refusal}): {unit.subtask}")
        elif record is not None and record.report:
            lines.append(f"  - {unit.unit_task_id} reports: {record.report}")
        else:
            reason = record.exit_reason if record is not None else FANOUT_EXIT_ABSENT
            lines.append(f"  - {unit.unit_task_id} NO REPORT ({reason}): {unit.subtask}")
    reported = sum(1 for record in runs if record.report)
    header = (
        f"[fan-out] {plan.width} unit(s): {reported} reported, "
        f"{len(plan.dispatched) - reported} dispatched without a report, "
        f"{len(plan.refused)} not run"
    )
    return "\n".join([header, *lines])


def build_fanout_seam(
    complete: CompleteFn,
    *,
    log: Optional[DelegationLog] = None,
    plans: Optional[list[FanoutPlan]] = None,
    timeout: float = DEFAULT_FANOUT_TIMEOUT,
    width_limit: int = MAX_FANOUT_WIDTH,
) -> SubagentFn:
    """Wrap an injected worker mind as a seam that serves BOTH delegate shapes.

    A batch arrives carrying ``context["subtasks"]``; anything else is a serial
    ``delegate`` and is handed straight to :func:`build_worker_seam`, unchanged.
    One seam, two shapes, and the serial path is byte-identical to task ``t1``'s.
    """
    serial = build_worker_seam(complete, log=log)

    def seam(call: SubagentCall) -> Optional[SubagentResult]:
        context = call.context or {}
        subtasks = context.get("subtasks")
        if subtasks is None:
            return serial(call)
        plan = plan_fanout(
            tuple(subtasks),
            grant=call.max_steps,
            stem=call.task.id,
            width_limit=width_limit,
        )
        if plans is not None:
            plans.append(plan)
        runs, degradations, charged = _fan_out(complete, call, plan, log=log, timeout=timeout)
        reports = [record.report for record in runs if record.report]
        whole = len(reports) == plan.width
        return SubagentResult(
            sub_result=SubResult(
                task_id=call.task.id,
                engine=ENGINE,
                model=call.model,
                status=OK if reports else ERROR,
                summary="; ".join(reports) if reports else "no unit reported",
                role=call.role,
            ),
            model_turns=charged,
            degradations=degradations,
            result=_fanout_note(plan, runs),
            exit_reason=FANOUT_EXIT_COMPLETE if whole else FANOUT_EXIT_PARTIAL,
        )

    return seam


@dataclass
class FanoutOutcome(DelegationOutcome):
    """What one fan-out orchestration produced, at both levels.

    :attr:`plans` is the accounting, kept beside the runs so a reader can check
    the rule without re-deriving it: ``charged <= plan.committed <=
    plan.grant``, for every batch.
    """

    plans: list[FanoutPlan] = field(default_factory=list)

    @property
    def charged(self) -> int:
        """What the parent was billed for **every child of this drive**.

        The loop's own number, not a re-derived one. Note the scope: a drive
        that also called the serial ``delegate`` has those turns in here too, so
        ``charged <= committed`` holds per batch but not across a mixed drive.
        The per-batch check is the one the rule makes, and :attr:`plans` carries
        the numbers for it.
        """
        return self.outcome.child_model_turns

    def report(self) -> dict[str, Any]:
        data = super().report()
        data["fanout"] = {
            "rule": FANOUT_ACCOUNTING_RULE,
            "max_width": MAX_FANOUT_WIDTH,
            "batches": [plan.to_dict() for plan in self.plans],
            "grant_total": sum(plan.grant for plan in self.plans),
            "committed_total": sum(plan.committed for plan in self.plans),
            "charged_total": self.charged,
        }
        return data


def run_fanout(
    cortex: CompleteFn,
    worker: CompleteFn,
    *,
    task: Task,
    max_steps: int = DEFAULT_ORCHESTRATOR_MAX_STEPS,
    fanout_max_steps: int = DEFAULT_FANOUT_MAX_STEPS,
    worker_max_steps: int = DEFAULT_WORKER_MAX_STEPS,
    cortex_model: str = "",
    worker_model: str = "",
    identity: Optional[str] = None,
    system_prompt: str = FANOUT_ORCHESTRATOR_SYSTEM,
    spawn_allowance: int = DEFAULT_ALLOWANCE,
    timeout: float = DEFAULT_FANOUT_TIMEOUT,
    width_limit: int = MAX_FANOUT_WIDTH,
) -> FanoutOutcome:
    """Drive one fan-out orchestration with two injected minds.

    Same contract as :func:`run_delegation` — both minds are arguments, both
    ``model`` values are labels recorded for traces, and nothing here dials
    anything.
    """
    executor = FanoutOrchestratorExecutor(
        task_id=task.id,
        worker_model=worker_model,
        worker_max_steps=worker_max_steps,
        fanout_max_steps=fanout_max_steps,
        identity=identity,
    )
    log = DelegationLog()
    plans: list[FanoutPlan] = []
    seam = build_fanout_seam(worker, log=log, plans=plans, timeout=timeout, width_limit=width_limit)
    try:
        outcome = run(
            cortex,
            task,
            executor=executor,
            max_steps=max_steps,
            system_prompt=orchestrator_prompt(system_prompt, identity=identity),
            subagent=seam,
            spawn_allowance=spawn_allowance,
            model=cortex_model,
        )
    except LoopAborted as aborted:
        return FanoutOutcome(
            outcome=aborted.outcome, executor=executor, log=log, aborted=True, plans=plans
        )
    return FanoutOutcome(outcome=outcome, executor=executor, log=log, plans=plans)


# ── scripted minds (no network, no live model) ───────────────────────────────


def scripted_cortex(messages: list[dict[str, Any]]) -> ModelResponse:
    """Delegate each demo subtask in turn, then write the final answer itself.

    It counts its own delegations out of the transcript rather than holding
    hidden state, so it behaves the same however many times it is called.
    """
    delegated = sum(
        1
        for message in messages
        if message.get("role") == "tool" and DELEGATION_MARKER in str(message.get("content") or "")
    )
    if delegated < len(DEMO_SUBTASKS):
        return ModelResponse(
            content="Handing the next subtask to a worker.",
            tool_calls=[
                ToolCall(
                    id=f"call-delegate-{delegated + 1}",
                    name="delegate",
                    arguments={"subtask": DEMO_SUBTASKS[delegated]},
                )
            ],
        )
    return ModelResponse(
        content="I have what I need.",
        tool_calls=[
            ToolCall(
                id="call-finish",
                name="finish",
                arguments={
                    "summary": (
                        "Gauges A and C read low (12% and 9%); B is normal at 47%. "
                        "One subtask could not be worked and one was not delegated."
                    )
                },
            )
        ],
    )


def scripted_worker(messages: list[dict[str, Any]]) -> ModelResponse:
    """Report on a workable subtask; answer in prose on an impossible one.

    The prose branch is not decoration: it is the failure mode this host has to
    survive visibly. A model that answers without calling anything leaves the
    orchestrator with no report, and that has to reach the ledger wearing the
    right child's name.
    """
    text = " ".join(str(message.get("content") or "") for message in messages)
    if UNANSWERABLE in text:
        return ModelResponse(
            content="There is nothing in the briefing to reconcile against.",
            tool_calls=[],
        )
    return ModelResponse(
        content="Reporting back.",
        tool_calls=[
            ToolCall(
                id="call-report",
                name="report",
                arguments={"findings": "A at 12% and C at 9% are below the 20% floor; B is fine"},
            )
        ],
    )


def scripted_fanout_cortex(messages: list[dict[str, Any]]) -> ModelResponse:
    """Fan the whole batch out in one call, then write the final answer itself.

    It counts its own batches out of the transcript rather than holding hidden
    state, so it behaves the same however many times it is called.
    """
    fanned = sum(
        1
        for message in messages
        if message.get("role") == "tool" and FANOUT_MARKER in str(message.get("content") or "")
    )
    if fanned < 1:
        return ModelResponse(
            content="Handing the whole batch out at once.",
            tool_calls=[
                ToolCall(
                    id="call-fanout-1",
                    name=FANOUT_TOOL,
                    arguments={"subtasks": list(DEMO_FANOUT_SUBTASKS)},
                )
            ],
        )
    return ModelResponse(
        content="I have what came back.",
        tool_calls=[
            ToolCall(
                id="call-finish",
                name="finish",
                arguments={
                    "summary": (
                        "Gauges A and C read low (12% and 9%); B is normal at 47%. "
                        "One unit could not be worked and one was never funded."
                    )
                },
            )
        ],
    )


# ── the runnable demonstration ───────────────────────────────────────────────


def run_demo(*, spawn_allowance: int = DEFAULT_ALLOWANCE) -> dict[str, Any]:
    """Three delegations, two workers, one ledger — and the bound biting.

    Subtask 1 is worked and reported. Subtask 2 comes back with no report, and
    the degradation is attributed to *that* child. Subtask 3 is refused: the
    allowance is spent, which is the design working, so it records no
    degradation at all.
    """
    delivered = run_delegation(
        scripted_cortex,
        scripted_worker,
        task=orchestrator_task(DEMO_INSTRUCTION),
        spawn_allowance=spawn_allowance,
    )
    return delivered.report()


def run_fanout_demo(
    *,
    spawn_allowance: int = DEFAULT_ALLOWANCE,
    fanout_max_steps: int = DEMO_FANOUT_GRANT,
) -> dict[str, Any]:
    """Five units, four turns, one tool call — and the partition biting.

    Four units are funded one turn each. Three report; one answers in prose and
    the ``worker-no-report`` degradation is attributed to *that* unit. The fifth
    is never dispatched: the grant did not reach it, which is the design
    working, so it records no degradation and is reported as ``unfunded``
    instead. The parent is charged four turns — the grant, not five units' worth.
    """
    delivered = run_fanout(
        scripted_fanout_cortex,
        scripted_worker,
        task=orchestrator_task(DEMO_INSTRUCTION),
        spawn_allowance=spawn_allowance,
        fanout_max_steps=fanout_max_steps,
    )
    return delivered.report()


def _print_fanout(report: dict[str, Any]) -> int:
    boss = report["orchestrator"]
    hand = report["worker"]
    fan = report["fanout"]
    print("=" * 72)
    print("PARALLEL FAN-OUT — one tool call, N workers at once")
    print("=" * 72)
    print()
    print("Orchestrator:")
    print(f"  task_id:            {boss['task_id']}")
    print(f"  tools:              {boss['tools']}")
    print(f"  exit_reason:        {boss['exit_reason']}")
    print(f"  own model turns:    {boss['model_turns']}")
    print(f"  child model turns:  {boss['child_model_turns']} (charged to the same budget)")
    print()
    print("Accounting:")
    print(f"  grant (one spawn):  {fan['grant_total']}")
    print(f"  committed to units: {fan['committed_total']}  (never above the grant)")
    print(f"  charged to parent:  {fan['charged_total']}  (never above what was committed)")
    print(f"  max width:          {fan['max_width']}")
    print(f"  max in flight:      {hand['max_in_flight']} (parallel)")
    print()
    for plan in fan["batches"]:
        print(f"Batch of {plan['width']} on a grant of {plan['grant']}:")
        for unit in plan["units"]:
            state = "dispatched" if unit["dispatched"] else f"NOT RUN ({unit['refusal']})"
            print(f"  - {unit['unit_task_id']}: {state}, slice {unit['grant']}")
    print()
    print("Units:")
    for record in hand["runs"]:
        print(
            f"  - {record['child_task_id']}: {record['exit_reason'] or 'ok'}, "
            f"{record['model_turns']} turn(s) spent, {record['charged']} charged"
        )
        print(f"      report:  {record['report'] or '(none)'}")
    print()
    print("Who degraded:")
    if report["attribution"]:
        for child_id, codes in report["attribution"].items():
            print(f"  - {child_id}: {codes}")
    else:
        print("  - nobody")
    print()
    print(f"Final answer (the orchestrator's own): {boss['summary']}")
    print()
    print("The accounting rule this run obeys:")
    print()
    print(fan["rule"])
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit the JSON report")
    parser.add_argument(
        "--fanout",
        action="store_true",
        help="run the parallel fan-out demo instead of the serial one",
    )
    args = parser.parse_args(argv)

    report = run_fanout_demo() if args.fanout else run_demo()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    if args.fanout:
        return _print_fanout(report)

    boss = report["orchestrator"]
    hand = report["worker"]
    print("=" * 72)
    print("SERIAL DELEGATION — one orchestrator, one worker at a time")
    print("=" * 72)
    print()
    print("Orchestrator:")
    print(f"  task_id:            {boss['task_id']}")
    print(f"  tools:              {boss['tools']}")
    print(f"  delegations:        {boss['delegations']}")
    print(f"  exit_reason:        {boss['exit_reason']}")
    print(f"  own model turns:    {boss['model_turns']}")
    print(f"  child model turns:  {boss['child_model_turns']} (charged to the same budget)")
    print(f"  allowance left:     {boss['spawn_allowance_remaining']}")
    print()
    print("Worker:")
    print(f"  tools:              {hand['tools']}")
    print(f"  withheld:           {hand['withheld_tools']}")
    print(f"  max in flight:      {hand['max_in_flight']} (serial)")
    for record in hand["runs"]:
        print(
            f"  - {record['child_task_id']}: {record['exit_reason']}, "
            f"{record['model_turns']} turn(s), allowance {record['allowance']}"
        )
        print(f"      subtask: {record['subtask']}")
        print(f"      report:  {record['report'] or '(none)'}")
    print()
    print("Spawn records:")
    for spawn in report["spawns"]:
        print(f"  - {spawn['outcome']}: {spawn.get('child_task_id') or '(no child)'}")
    print()
    print("Who degraded:")
    if report["attribution"]:
        for child_id, codes in report["attribution"].items():
            print(f"  - {child_id}: {codes}")
    else:
        print("  - nobody")
    print()
    print(f"Final answer (the orchestrator's own): {boss['summary']}")
    print()
    print("`finish` is the orchestrator's tool and no one else's; a worker that")
    print("calls it is refused, not answered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
