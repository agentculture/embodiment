"""Serial delegation — an orchestrator's ``delegate`` tool (plan task ``t1``).

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

Usage::

    uv run python examples/orchestrator_tools.py          # readable report
    uv run python examples/orchestrator_tools.py --json   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
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
from embodiment.subagent import NO_SPAWNS, SpawnRequest, SubagentCall, SubagentFn, SubagentResult

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
    report: str = ""
    refused_tools: list[str] = field(default_factory=list)
    degradation_codes: list[str] = field(default_factory=list)
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
            "report": self.report,
            "refused_tools": list(self.refused_tools),
            "degradation_codes": list(self.degradation_codes),
        }


@dataclass
class DelegationLog:
    """Every worker consultation, in order, plus the proof they were serial.

    ``max_in_flight`` exists because "serial" is a claim, and a claim that is
    only true because the code happens to be single-threaded is worth measuring
    rather than assuming — a parallel fan-out (task ``t4``) will read the same
    field and see a different number.
    """

    runs: list[WorkerRun] = field(default_factory=list)
    in_flight: int = 0
    max_in_flight: int = 0

    def enter(self, record: WorkerRun) -> WorkerRun:
        self.runs.append(record)
        self.in_flight += 1
        if self.in_flight > self.max_in_flight:
            self.max_in_flight = self.in_flight
        return record

    def leave(self) -> None:
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
    """

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
        self.finished = False
        self.finish_summary = ""

    def child_task_id(self, ordinal: int) -> str:
        """``<parent>-worker-<n>`` — readable, and unique per consultation."""
        return f"{self.task_id}-worker-{ordinal}"

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append(name)
        if name not in ORCHESTRATOR_TOOLS:
            self.refused.append(name)
            raise UnknownToolError(
                f"{name!r} is not available to the orchestrator; its tools are "
                f"{list(ORCHESTRATOR_TOOLS)}"
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
                "tools": list(ORCHESTRATOR_TOOLS),
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
                "withheld_tools": sorted(set(ORCHESTRATOR_TOOLS) - set(WORKER_TOOLS)),
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit the JSON report")
    args = parser.parse_args(argv)

    report = run_demo()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

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
